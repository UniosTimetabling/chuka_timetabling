"""
signals.py – campuses_timetable
================================
Post-save signal on CampusCourseAllocation that triggers the
Program Year Tracker to refresh and notify the COD of any gaps.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
import logging

logger = logging.getLogger(__name__)


def _trigger_tracker(sender, instance, **kwargs):
    """Deferred import to avoid circular imports at module load time."""
    try:
        from .tracker_service import run_tracker_for_allocation
        run_tracker_for_allocation(instance)
    except Exception as exc:
        logger.error(
            f"Tracker signal failed for allocation {instance.pk}: {exc}",
            exc_info=True
        )


def connect_signals():
    """
    Called from AppConfig.ready().  Connects signals so they are
    only registered once, after all apps have loaded.
    """
    from .models import CampusCourseAllocation
    post_save.connect(_trigger_tracker, sender=CampusCourseAllocation,
                      dispatch_uid='campus_allocation_tracker')
