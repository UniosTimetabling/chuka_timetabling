"""
export_import — background sync task.

This is the piece that was missing: `sync_engine.run_sync()` only ever
ran when a human clicked "Sync Now" on /sync/settings/ (sync_views.trigger_sync).
There was no periodic job and nothing re-triggered a push after a network
drop/reconnect, so a HOST installation that nobody visited could sit
indefinitely with "No sync runs yet" even though it was fully configured
and enabled.

`auto_run_sync` closes that gap: it's scheduled frequently (see
university_timetable_system/celery.py beat_schedule) and, each time it
fires, decides for itself whether it's actually time to sync based on
`SyncNode.auto_sync_interval_minutes` — the same "checked often, acts
according to a per-row setting" pattern backup_system.tasks already uses
for dispatch_scheduled_backups. Leaving auto_sync_interval_minutes at 0
(the default) keeps today's manual-only behaviour unchanged.

Mirrors backup_system/tasks.py's Celery-optional setup so this still
works (via a daemon thread) on a deployment that never runs a Celery
worker/beat process at all.
"""
import logging
import threading

from django.utils import timezone

logger = logging.getLogger(__name__)

try:
    from celery import shared_task as _shared_task
    _CELERY_AVAILABLE = True
except ImportError:
    _CELERY_AVAILABLE = False

    def _shared_task(*args, **kwargs):
        def decorator(fn):
            fn.delay = lambda *a, **kw: _thread_fallback(fn, *a, **kw)
            fn.apply_async = lambda args=(), kwargs={}, **_: _thread_fallback(fn, *args, **kwargs)
            return fn
        return decorator(args[0]) if args and callable(args[0]) else decorator


def _thread_fallback(fn, *args, **kwargs):
    t = threading.Thread(target=_run_in_thread, args=(fn, args, kwargs), daemon=True)
    t.start()
    return t


def _run_in_thread(fn, args, kwargs):
    try:
        import django
        django.setup()
    except RuntimeError:
        pass
    try:
        fn(*args, **kwargs)
    except Exception:
        logger.error("Background task %s failed", fn.__name__, exc_info=True)


@_shared_task
def auto_run_sync():
    """
    Runs on a schedule (see beat_schedule) and does two independent
    things, both without needing anyone to have the browser open:

    1. RECONNECT-TRIGGERED SYNC (the actual original gap): every time
       this fires, it actively pings the remote via sync_engine
       .test_connection() — the exact same check the "Test Connection"
       button does. If the remote was NOT reachable last time we
       checked (or we've never checked) and IS reachable now, that's a
       "just came back online" transition, and we sync immediately,
       right then — not on the next interval tick, and regardless of
       whether auto_sync_interval_minutes is even set above 0. This is
       what makes "host goes away and comes back" actually push data
       on its own instead of sitting there until someone clicks Sync
       Now.

    2. INTERVAL-BASED SYNC: separately, if auto_sync_interval_minutes
       > 0, also resyncs on that schedule even while the connection
       has stayed continuously up (catches local changes made while
       already connected, on a steady cadence) — same as before.

    Leaving auto_sync_interval_minutes at 0 only turns off (2); (1)
    stays active for any enabled, fully-configured HOST, since it's
    the direct fix for "nothing sends automatically after a reconnect."
    """
    from core.models import SyncNode, SyncRun
    from export_import import sync_engine

    node = SyncNode.get_settings()

    if not node.is_enabled or not node.is_host:
        return "skipped: not an enabled host"
    if not node.remote_url or not node.get_token():
        return "skipped: remote URL/token not configured"

    # Clear out anything only *labeled* "running" because a previous
    # attempt died without finishing (dev server reload, worker restart,
    # etc.) — otherwise it blocks every future auto-sync forever.
    sync_engine.reap_stale_runs()

    if SyncRun.objects.filter(status="running").exists():
        return "skipped: a sync run is already in progress"

    # ── (1) Reconnect detection — check reachability ourselves, every
    # time, independent of the interval setting. ──────────────────────
    was_connected = bool(node.last_connection_ok)  # False/None both count as "wasn't connected"
    check = sync_engine.test_connection()  # also updates node.last_connection_* fields
    node.refresh_from_db()
    just_reconnected = bool(check.get("ok")) and not was_connected

    if just_reconnected:
        logger.info("auto_run_sync: remote just came back online — syncing immediately")
        run = sync_engine.run_sync(triggered_by=None)
        return f"reconnect-triggered sync run #{run.pk} finished with status={run.status}"

    if not check.get("ok"):
        return f"skipped: remote still unreachable ({check.get('error', 'unknown error')})"

    # ── (2) Plain interval-based resync, only if configured. ─────────
    if node.auto_sync_interval_minutes <= 0:
        return "skipped: still connected, no reconnect edge, and auto sync interval is 0"

    last_attempt = node.last_sync_finished_at or node.last_sync_started_at
    if last_attempt is not None:
        due_at = last_attempt + timezone.timedelta(minutes=node.auto_sync_interval_minutes)
        if timezone.now() < due_at:
            return "skipped: connected, but not due for the next interval sync yet"

    logger.info("auto_run_sync: starting scheduled interval sync run")
    run = sync_engine.run_sync(triggered_by=None)
    return f"interval sync run #{run.pk} finished with status={run.status}"
