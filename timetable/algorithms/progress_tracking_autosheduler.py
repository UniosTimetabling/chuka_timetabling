"""
Progress Tracking for Exam Auto-Scheduler
==========================================
Uses Django's cache backend as the shared progress store so that progress
updates written by the scheduler thread (in one gunicorn/uwsgi worker process)
are immediately visible to progress-poll requests served by any other worker.

All previous in-memory state was lost across worker processes — this is why
the frontend stayed stuck at 97% while the backend had already reached 100%.

The cache key is EXAM_SCHEDULER_PROGRESS. Any cache backend works:
  - LocMemCache  (dev only — single process, same problem as before)
  - FileBasedCache (works across processes on the same machine)
  - MemcachedCache / RedisCache (works across machines)
  - DatabaseCache (works everywhere, slightly slower)

For a single-server deployment the fastest zero-dependency option is
FileBasedCache. Add to settings.py if not already present:

    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": "/tmp/django_cache",
        }
    }

If you already have Redis configured, no settings change is needed.
"""

import threading
import datetime
import queue
import json
import logging

from django.core.cache import cache
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views import View

scheduler_logger = logging.getLogger("scheduler")

# ── Constants ────────────────────────────────────────────────────────────────

CACHE_KEY      = "exam_scheduler_progress"
CACHE_TIMEOUT  = 60 * 60 * 4   # 4 hours — outlasts any realistic run
MAX_LOG_ENTRIES = 300

# Real mutual-exclusion lock for "is a run in progress", separate from the
# CACHE_KEY progress snapshot. cache.add() is atomic (only succeeds if the
# key does not already exist), so it closes the race that a plain
# read-status-then-write-status check cannot: two near-simultaneous POSTs
# to /autoscheduler/exam/run/ (double-click, a duplicate frontend submit,
# or two requests landing on different worker processes) can both read
# status="idle" before either has written status="running", both pass that
# check, and both launch a scheduler_thread — two independent SchedulerState
# instances then place courses into the same venue/slot with no awareness
# of each other, which is how a room ends up holding more students than it
# has seats for. cache.add() lets only one of those requests "win".
RUN_LOCK_KEY     = "exam_scheduler_run_lock"
RUN_LOCK_TIMEOUT = 60 * 60 * 4  # matches CACHE_TIMEOUT; released explicitly on finish/error

# ── Thread-local lock (guards the SSE queue only — cache is its own lock) ───
_lock      = threading.Lock()
_log_queue: queue.Queue = queue.Queue(maxsize=2000)

# Cancellation signal — checked by the scheduler between phases
cancel_event = threading.Event()

scheduler_thread: threading.Thread | None = None


# ── Default progress snapshot ────────────────────────────────────────────────

def _default_progress() -> dict:
    return {
        "status":          "idle",
        "progress":        0,
        "current_action":  "",
        "scheduled_count": 0,
        "remaining_count": 0,
        "batch_info":      "",
        "total_courses":   0,
        "start_time":      None,
        "elapsed_seconds": 0,
        "current_batch":   0,
        "total_batches":   0,
        "phase_stats":     {},
        "audit":           {},
        "log_entries":     [],
    }


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _get_progress() -> dict:
    """Read the progress snapshot from the shared cache."""
    data = cache.get(CACHE_KEY)
    if data is None:
        data = _default_progress()
    return data


def _set_progress(data: dict):
    """Write the full progress snapshot to the shared cache."""
    cache.set(CACHE_KEY, data, CACHE_TIMEOUT)


def _update_progress_fields(**fields):
    """
    Read-modify-write the progress snapshot atomically enough for our needs.
    Not truly atomic across processes, but collisions are harmless here
    because the scheduler thread is the only writer.
    """
    data = _get_progress()
    data.update(fields)
    _set_progress(data)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _elapsed() -> float:
    data = _get_progress()
    raw  = data.get("start_time")
    if not raw:
        return 0.0
    try:
        start = datetime.datetime.fromisoformat(raw)
        return (datetime.datetime.now() - start).total_seconds()
    except Exception:
        return 0.0


def _append_log(level: str, message: str):
    """
    Append a structured log entry to the cache-stored progress snapshot and
    push it to the SSE queue for any connected streaming clients.
    """
    entry = {"ts": _now_iso(), "level": level, "msg": message}

    data = _get_progress()
    logs = data.get("log_entries", [])
    logs.append(entry)
    if len(logs) > MAX_LOG_ENTRIES:
        logs = logs[-MAX_LOG_ENTRIES:]
    data["log_entries"] = logs
    _set_progress(data)

    try:
        _log_queue.put_nowait(entry)
    except queue.Full:
        pass


# ── Public API — called by the scheduler algorithm ───────────────────────────

def update_progress(
    progress: int,
    current_action: str,
    scheduled_count: int = 0,
    remaining_count: int = 0,
    batch_info: str = "",
    current_batch: int = 0,
    total_batches: int = 0,
    status: str = None,
):
    """
    Write a progress update to the shared cache.

    Called from the scheduler thread. Because the cache is shared across all
    worker processes, the next poll — regardless of which worker handles it —
    will see the updated values.

    When progress >= 100 and no explicit status is supplied, the status is
    automatically promoted to 'completed' or 'partial' based on remaining_count,
    so the frontend can detect completion from a single poll response.
    """
    elapsed = _elapsed()

    action_lower = current_action.lower()
    if any(k in action_lower for k in ("error", "fail", "fatal", "violation")):
        level = "error"
    elif any(k in action_lower for k in ("warn", "could not", "unplaced")):
        level = "warn"
    elif any(k in action_lower for k in ("success", "✓", "complete", "done")):
        level = "success"
    else:
        level = "info"

    # Auto-promote status at 100% so the frontend always unblocks
    if status:
        resolved_status = status
    elif progress >= 100:
        resolved_status = "completed" if remaining_count == 0 else "partial"
    else:
        resolved_status = None

    fields = {
        "progress":        progress,
        "current_action":  current_action,
        "scheduled_count": scheduled_count,
        "remaining_count": remaining_count,
        "batch_info":      batch_info,
        "current_batch":   current_batch,
        "total_batches":   total_batches,
        "elapsed_seconds": round(elapsed, 1),
    }
    if resolved_status:
        fields["status"] = resolved_status

    _update_progress_fields(**fields)
    _append_log(level, f"[{progress:3d}%] {current_action}")


def log_debug(message: str):
    _append_log("debug", message)


def log_warn(message: str):
    _append_log("warn", message)


def log_error(message: str):
    _append_log("error", message)


def is_cancelled() -> bool:
    """Checked by the scheduler between phases."""
    return cancel_event.is_set()


# ── Thread runner ─────────────────────────────────────────────────────────────

def run_scheduler_in_thread(disabled_constraints=None):
    try:
        from .exam_timetable_autosheduler_algorith import run_optimized_autoscheduler_thread

        result       = run_optimized_autoscheduler_thread(disabled_constraints)
        final_status = result.get("status", "completed")
        final_msg    = result.get("message", "Scheduling completed")
        sched        = result.get("scheduled_count", 0)
        rem          = result.get("remaining_count", 0)

        level = "success" if final_status == "completed" else (
            "warn" if final_status == "partial" else "error"
        )
        if level == "error":
            scheduler_logger.error(
                "Regular/Exam scheduler completed with error status: %s | scheduled=%s remaining=%s",
                final_msg, sched, rem,
            )

        # Write the definitive final state to the cache
        update_progress(
            progress=100,
            current_action=final_msg,
            scheduled_count=sched,
            remaining_count=rem,
            status=final_status,
        )

        data = _get_progress()
        data.update({
            "status":          final_status,
            "progress":        100,
            "current_action":  final_msg,
            "scheduled_count": sched,
            "remaining_count": rem,
            "elapsed_seconds": round(_elapsed(), 1),
            "phase_stats":     result.get("phase_stats", {}),
            "audit":           result.get("audit", {}),
            "family_stats":    result.get("family_stats", {}),
        })
        _set_progress(data)

        _append_log(level, f"FINAL: {final_msg}")

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        scheduler_logger.error(
            "Regular/Exam scheduler fatal error: %s\n%s", exc, tb,
        )
        _update_progress_fields(
            status="error",
            current_action=f"Fatal error: {exc}",
            elapsed_seconds=round(_elapsed(), 1),
        )
        _append_log("error", f"FATAL: {exc}")
        _append_log("debug", tb)

    finally:
        # Always release the run lock when this thread ends, success or not,
        # so a genuinely finished/crashed run never permanently blocks the
        # next real attempt via StartSchedulingView.
        cache.delete(RUN_LOCK_KEY)


# ── Django views ──────────────────────────────────────────────────────────────

class SchedulerProgressView(View):
    """
    GET /exam-scheduler/progress/
    Returns the current progress snapshot from the shared cache.
    Safe to call from any worker process.
    """

    def get(self, request):
        data = _get_progress()

        # Self-correcting guard: if the cache somehow has progress=100 but
        # status was not promoted (e.g. a very old cache entry), fix it here.
        if data.get("progress", 0) >= 100:
            if data.get("status") not in ("completed", "partial", "error", "cancelled"):
                data["status"] = "completed" if data.get("remaining_count", 0) == 0 \
                                 else "partial"
                _set_progress(data)

        return JsonResponse(data)


class SchedulerLogsView(View):
    """
    GET /exam-scheduler/logs/?since=<ISO-timestamp>
    Returns log entries newer than the given timestamp.
    """

    def get(self, request):
        since_str = request.GET.get("since")
        data      = _get_progress()
        all_logs  = data.get("log_entries", [])

        if since_str:
            try:
                since_dt = datetime.datetime.fromisoformat(since_str)
                filtered = [
                    e for e in all_logs
                    if datetime.datetime.fromisoformat(e["ts"]) > since_dt
                ]
            except Exception:
                filtered = all_logs[-100:]
        else:
            filtered = all_logs[-100:]

        return JsonResponse({"log_entries": filtered})


class SchedulerLogsSSEView(View):
    """
    GET /exam-scheduler/logs/stream/
    Server-Sent Events stream for real-time log delivery.
    Connect from JS with EventSource and listen for 'log', 'progress',
    and 'done' events.
    """

    def get(self, request):
        def event_stream():
            # Drain any backlog from the queue first
            while True:
                try:
                    entry = _log_queue.get_nowait()
                    yield f"event: log\ndata: {json.dumps(entry)}\n\n"
                except queue.Empty:
                    break

            while True:
                try:
                    entry = _log_queue.get(timeout=30)
                    yield f"event: log\ndata: {json.dumps(entry)}\n\n"

                    data = _get_progress()
                    snap = {
                        "progress":        data.get("progress", 0),
                        "status":          data.get("status", ""),
                        "current_action":  data.get("current_action", ""),
                        "scheduled_count": data.get("scheduled_count", 0),
                        "remaining_count": data.get("remaining_count", 0),
                        "elapsed_seconds": data.get("elapsed_seconds", 0),
                    }
                    yield f"event: progress\ndata: {json.dumps(snap)}\n\n"

                    if snap["status"] in ("completed", "partial", "error", "cancelled"):
                        yield "event: done\ndata: {}\n\n"
                        return

                except queue.Empty:
                    data           = _get_progress()
                    current_status = data.get("status", "")
                    if current_status in ("completed", "partial", "error", "cancelled"):
                        snap = {
                            "progress":        data.get("progress", 0),
                            "status":          current_status,
                            "current_action":  data.get("current_action", ""),
                            "scheduled_count": data.get("scheduled_count", 0),
                            "remaining_count": data.get("remaining_count", 0),
                            "elapsed_seconds": data.get("elapsed_seconds", 0),
                        }
                        yield f"event: progress\ndata: {json.dumps(snap)}\n\n"
                        yield "event: done\ndata: {}\n\n"
                        return
                    yield ": heartbeat\n\n"

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"]     = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


@method_decorator(csrf_exempt, name="dispatch")
class StartSchedulingView(View):
    """POST /autoscheduler/exam/run/ — launches the scheduler in a background thread."""

    def post(self, request):
        global scheduler_thread

        # Atomic acquire: cache.add() only succeeds if RUN_LOCK_KEY is not
        # already set, so exactly one concurrent request can win this race —
        # unlike the old "read status, then later write status" check, there
        # is no window between check and act for a second request to slip
        # through. If this returns False, a run is already in progress
        # (started by this request or another one) and we bail out here,
        # before any second SchedulerState/thread gets created.
        acquired = cache.add(RUN_LOCK_KEY, True, RUN_LOCK_TIMEOUT)
        if not acquired:
            return JsonResponse(
                {"status": "already_running", "message": "Scheduler is already running"},
                status=409,
            )

        cancel_event.clear()

        disabled_constraints = set()
        try:
            body = json.loads(request.body or b"{}")
            disabled_constraints = set(body.get('disabled_constraints') or [])
        except Exception:
            disabled_constraints = set()

        # Drain the SSE queue
        while not _log_queue.empty():
            try:
                _log_queue.get_nowait()
            except queue.Empty:
                break

        # Reset the shared cache state
        fresh = _default_progress()
        fresh.update({
            "status":         "running",
            "current_action": "Initializing scheduler...",
            "start_time":     _now_iso(),
        })
        _set_progress(fresh)

        _append_log("info", "Scheduler started")
        if disabled_constraints:
            _append_log("info", f"Constraints disabled for this run: {sorted(disabled_constraints)}")

        try:
            scheduler_thread = threading.Thread(
                target=run_scheduler_in_thread, args=(disabled_constraints,),
                daemon=True, name="ExamScheduler"
            )
            scheduler_thread.start()
        except Exception:
            # Thread never started — release the lock immediately so a retry
            # isn't permanently blocked by a run that never happened.
            cache.delete(RUN_LOCK_KEY)
            raise

        return JsonResponse({"status": "started", "message": "Scheduling process started"})


@method_decorator(csrf_exempt, name="dispatch")
class CancelSchedulingView(View):
    """POST /exam-scheduler/cancel/ — signals the scheduler to stop after the current phase."""

    def post(self, request):
        cancel_event.set()
        _update_progress_fields(
            status="cancelled",
            current_action="Cancellation requested — stopping after current phase...",
        )
        _append_log("warn", "Cancellation requested by user")
        return JsonResponse({"status": "cancelled", "message": "Cancellation signal sent"})