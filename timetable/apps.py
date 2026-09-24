import logging

from django.apps import AppConfig
from django.conf import settings


class TimetableConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'timetable'

    def ready(self):
        try:
            from backup_system.signals import register_audit_signals
            from .models import Timetable, ExamTimetable, SchedulerConfig
            register_audit_signals([Timetable, ExamTimetable, SchedulerConfig])
        except Exception:
            pass

        self._clear_stale_scheduler_lock_if_debug()

    def _clear_stale_scheduler_lock_if_debug(self):
        """
        DEBUG-only startup safety net: clear a stale exam_scheduler_run_lock
        left behind by a process that died mid-run — e.g. runserver's
        autoreloader restarting the process while a scheduler thread was
        still going. That thread never reaches its own
        `finally: cache.delete(RUN_LOCK_KEY)` in
        progress_tracking_autosheduler.py, so the lock survives the restart
        and every future run 409s ("Scheduler is already running") until
        it's cleared by hand or its 4-hour timeout expires.

        Deliberately gated to DEBUG only. In a multi-worker production
        deployment, ready() runs once PER WORKER PROCESS. If one worker
        restarts while another worker genuinely still has a live run in
        progress, blindly clearing the lock here would let a second run
        start alongside it — the exact double-scheduling hazard
        cache.add(RUN_LOCK_KEY, ...) exists to prevent (two SchedulerState
        instances independently placing courses into the same venue/slot).
        In a single-process dev setup there's only ever one owner of the
        lock, so if it's still set at startup it can only be stale.
        """
        if not settings.DEBUG:
            return
        try:
            from django.core.cache import cache
            from timetable.algorithms.progress_tracking_autosheduler import RUN_LOCK_KEY
            if cache.get(RUN_LOCK_KEY) is not None:
                cache.delete(RUN_LOCK_KEY)
                logging.getLogger("scheduler").warning(
                    "Cleared a stale exam_scheduler_run_lock on startup (DEBUG mode) — "
                    "a previous run's process likely died before releasing it."
                )
        except Exception:
            # Never let this block app startup — cache backend unreachable,
            # module not importable yet, etc. are all non-fatal here.
            pass