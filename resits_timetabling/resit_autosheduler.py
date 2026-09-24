"""
resits_timetabling/resit_autosheduler.py
=========================================
HTTP layer and progress tracker for the Resit auto-scheduler.

All scheduling logic lives in:
    timetable/algorithms/resit_autosheduler_algorithm.py

This file handles:
  • In-memory progress store + _update_progress()
  • run_resit_autosheduler  — starts background thread, calls engine
  • resit_autosheduler_progress — progress poll endpoint
  • resit_temp_timetable_data   — draft-data endpoint for the panel
  • autosheduler_panel          — renders the HTML panel

Each course is scheduled exactly once — there is no sessions_per_course
concept in the resit scheduler.
"""
from __future__ import annotations

import json
import threading
import traceback
import uuid
import logging

from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET

from core.group_required import group_required
from .models import ResitCourseAllocation, ResitSchedulerConfig, ResitTempTimetable

# ── engine import ─────────────────────────────────────────────────────────────
from timetable.algorithms.resit_autosheduler_algorithm import run_resit_scheduler

scheduler_logger = logging.getLogger("scheduler")

# ---------------------------------------------------------------------------
# Progress store
# ---------------------------------------------------------------------------

_resit_progress: dict = {}
_resit_lock = threading.Lock()


def _update_progress(job_id: str, data: dict) -> None:
    """Merge *data* into the progress entry for *job_id* (thread-safe)."""
    with _resit_lock:
        _resit_progress.setdefault(job_id, {}).update(data)


# ---------------------------------------------------------------------------
# Helper: consistent time formatter (used in resit_temp_timetable_data)
# ---------------------------------------------------------------------------

def _fmt(t) -> str:
    if hasattr(t, "strftime"):
        return t.strftime("%H:%M")
    if isinstance(t, str):
        return t[:5]
    return str(t)[:5]


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def _run_resit_scheduler_logged(job_id, allocation_ids, config, progress_callback):
    """
    Wraps run_resit_scheduler so that any exception raised inside the
    background thread is logged to the centralized 'scheduler' logger
    (logs/errors.log) instead of disappearing silently, and the job's
    progress entry is updated to status='error' so the frontend doesn't
    poll forever.
    """
    try:
        run_resit_scheduler(
            job_id=job_id,
            allocation_ids=allocation_ids,
            config=config,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        tb = traceback.format_exc()
        scheduler_logger.error(
            "Resit scheduler fatal error | job_id=%s | allocations=%d: %s\n%s",
            job_id, len(allocation_ids), exc, tb,
        )
        progress_callback(job_id, {
            "status": "error",
            "error": str(exc),
        })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def run_resit_autosheduler(request):
    """
    AJAX POST — start the resit auto-scheduler in a background thread.

    Always clears ALL existing ResitTempTimetable rows before scheduling
    so each run produces a completely fresh draft.

    Request body (JSON):
        {
            "allocation_ids": [int, ...]   // optional — defaults to all
        }

    Response:
        { "success": true, "job_id": "<uuid>", "total": <int> }
    """
    try:
        data = json.loads(request.body)
        allocation_ids: list[int] = [int(i) for i in data.get("allocation_ids", [])]
    except (ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)

    config = ResitSchedulerConfig.objects.order_by("-id").first()
    if not config:
        return JsonResponse(
            {"success": False, "error": "No ResitSchedulerConfig found. Create one first."},
            status=400,
        )

    if not allocation_ids:
        allocation_ids = list(ResitCourseAllocation.objects.values_list("id", flat=True))

    if not allocation_ids:
        return JsonResponse(
            {"success": False, "error": "No resit course allocations found to schedule."},
            status=400,
        )

    # Always clear before a fresh run
    deleted_count, _ = ResitTempTimetable.objects.all().delete()

    job_id = str(uuid.uuid4())
    total_sessions = len(allocation_ids)   # one session per course

    _update_progress(job_id, {
        "status": "starting",
        "scheduled": 0,
        "failed": [],
        "total": total_sessions,
    })

    thread = threading.Thread(
        target=_run_resit_scheduler_logged,
        kwargs={
            "job_id": job_id,
            "allocation_ids": allocation_ids,
            "config": config,
            "progress_callback": _update_progress,
        },
        daemon=True,
    )
    thread.start()

    return JsonResponse({
        "success": True,
        "job_id": job_id,
        "total": total_sessions,
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def resit_autosheduler_progress(request, job_id: str):
    """
    GET — poll the progress of a running auto-scheduler job.

    Response: { status, scheduled, failed, total, conflict_warnings }
    """
    with _resit_lock:
        progress = dict(_resit_progress.get(job_id, {"status": "unknown"}))
    return JsonResponse(progress)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def resit_temp_timetable_data(request):
    """
    GET — return all ResitTempTimetable (draft) entries as JSON for the
    auto-scheduler panel.  Only draft rows; published entries excluded.

    Response shape:
        {
            "entries": [
                {
                    "id": int,
                    "course_code": str,
                    "date": "YYYY-MM-DD",
                    "day_name": str,
                    "start_time": "HH:MM",
                    "end_time": "HH:MM",
                    "venue": str,
                    "registered_students": int,
                    "lecturer_name": str | null
                },
                ...
            ]
        }
    """
    try:
        rows = (
            ResitTempTimetable.objects
            .select_related(
                "resit_course_allocation",
                "resit_course_allocation__lecturer",
                "venue",
            )
            .order_by("date", "start_time", "venue__code")
        )

        entries = []
        for row in rows:
            alloc = row.resit_course_allocation
            lecturer = alloc.lecturer if alloc else None
            date_obj = row.date

            day_name = ""
            try:
                import datetime as _dt
                if date_obj:
                    if isinstance(date_obj, str):
                        date_obj = _dt.date.fromisoformat(date_obj)
                    day_name = date_obj.strftime("%A")
            except Exception:
                pass

            lecturer_name = None
            if lecturer:
                lecturer_name = (
                    getattr(lecturer, "name", None)
                    or getattr(lecturer, "full_name", None)
                    or getattr(lecturer, "display_name", None)
                    or str(lecturer)
                )

            entries.append({
                "id": row.id,
                "course_code": alloc.course_code if alloc else "",
                "date": str(row.date),
                "day_name": day_name,
                "start_time": _fmt(row.start_time),
                "end_time": _fmt(row.end_time),
                "venue": row.venue.code if row.venue else "",
                "registered_students": row.registered_students or 0,
                "lecturer_name": lecturer_name,
            })

        return JsonResponse({"entries": entries})

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse({"entries": [], "error": str(exc)}, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def autosheduler_panel(request):
    """Render the auto-scheduler UI page."""
    from django.shortcuts import render

    config = ResitSchedulerConfig.objects.order_by("-id").first()

    total_count = ResitCourseAllocation.objects.count()
    temp_scheduled_ids = set(
        ResitTempTimetable.objects.values_list("resit_course_allocation_id", flat=True)
    )
    unscheduled_count = ResitCourseAllocation.objects.exclude(id__in=temp_scheduled_ids).count()

    return render(
        request,
        "resits_timetabling/autosheduler_timetabling.html",
        {
            "config": config,
            "unscheduled_count": unscheduled_count,
            "total_count": total_count,
            "temp_count": len(temp_scheduled_ids),
        },
    )