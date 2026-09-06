"""
post_delete signals: whenever a row from ANY of the three course-allocation
models is deleted (single delete via a cod panel, or a bulk queryset
.delete() such as the semester archive/clear action), archive any SR
attached to it instead of leaving it orphaned.

Django's Collector sends pre_delete/post_delete for every instance even for
bulk QuerySet.delete() calls, so this covers both the single "Delete" button
and the "Archive & Delete" whole-semester action.
"""
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .services import archive_special_requests_for_allocation


def _archive_on_delete(sender, instance, **kwargs):
    try:
        archive_special_requests_for_allocation(instance, reason=f"{sender.__name__} deleted")
    except Exception:
        # Never let SR bookkeeping break an allocation delete.
        import logging
        logging.getLogger(__name__).exception("Failed to archive SRs for deleted %s", sender)


def _connect():
    from course_allocation.models import CourseAllocation
    post_delete.connect(_archive_on_delete, sender=CourseAllocation, dispatch_uid="sr_archive_course_allocation")

    try:
        from odel_system.models import ODELCourseAllocation
        post_delete.connect(_archive_on_delete, sender=ODELCourseAllocation, dispatch_uid="sr_archive_odel_allocation")
    except Exception:
        pass

    try:
        from campuses_timetable.models import CampusCourseAllocation
        post_delete.connect(_archive_on_delete, sender=CampusCourseAllocation, dispatch_uid="sr_archive_campus_allocation")
    except Exception:
        pass


_connect()
