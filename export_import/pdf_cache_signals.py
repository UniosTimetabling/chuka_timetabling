"""
export_import/pdf_cache_signals.py
====================================

Keeps the per (department, program, year) cached timetable PDFs
(export_import.program_year_pdf_cache / GeneratedTimetablePDF) in step
with the data they were built from.

Two triggers invalidate the cache:
  1. A push received from a HOST via export_import.sync_views.receive_sync
     — see the call to `invalidate_all()` + `regenerate_all_in_background()`
     there. A sync batch can touch many scopes at once and there's no
     cheap way to know exactly which from a raw fixture payload, so that
     path just clears everything and lets the background sweep rebuild
     only what's actually missing.
  2. A LOCAL edit on this installation (someone editing the timetable
     directly on the remote, or on the host itself) — handled here. This
     is scoped precisely to the one (department, program, year) the
     changed row belongs to, so a single edit doesn't force a full
     cache wipe.

Only fires anything when SyncNode is actually in "remote" mode with
sync enabled — a plain standalone installation (no sync feature in use)
never touches this table, so nothing changes for it.
"""
import logging

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _cache_enabled():
    """Only bother maintaining the cache on an installation that's
    actually using the host/remote sync feature. Cheap to check for
    every save, and keeps this a no-op everywhere else."""
    try:
        from core.models import SyncNode
        node = SyncNode.get_settings()
        return node.is_enabled and node.is_remote
    except Exception:  # noqa: BLE001 — never let this break a normal save
        return False


def _invalidate_for_allocation(course_allocation):
    """Resolve the (department, program, year) scope a CourseAllocation
    belongs to and invalidate just that scope's cached PDFs."""
    if not course_allocation or not course_allocation.program_id:
        return
    program_course = getattr(course_allocation, "program_course", None)
    if not program_course:
        return

    from export_import.program_year_pdf_cache import invalidate_scope, regenerate_all_in_background

    department_id = course_allocation.department_id or course_allocation.program.department_id
    invalidate_scope(department_id, course_allocation.program_id, program_course.year)
    # Rebuild in the background rather than on this request, so whoever
    # just saved the edit doesn't wait on a PDF rebuild for someone
    # else's future request.
    regenerate_all_in_background()


def _connect(model_path, related_name):
    """Wires post_save/post_delete for a Timetable-like model whose
    scope is reached via `instance.<related_name>` (a CourseAllocation)."""
    app_label, model_name = model_path.split(".")

    def _handler(sender, instance, **kwargs):
        if not _cache_enabled():
            return
        try:
            alloc = getattr(instance, related_name, None)
            _invalidate_for_allocation(alloc)
        except Exception:  # noqa: BLE001
            logger.exception("PDF cache invalidation failed for %s", sender)

    from django.apps import apps
    model = apps.get_model(app_label, model_name)
    post_save.connect(_handler, sender=model, weak=False)
    post_delete.connect(_handler, sender=model, weak=False)


def connect_all():
    _connect("timetable.Timetable", "course_allocation")
    _connect("timetable.ExamTimetable", "course_allocation")

    # A CourseAllocation edit (e.g. reassigned to a different program/
    # year, or its program_course changed) also needs to invalidate
    # whichever scope it now/previously belonged to.
    from django.apps import apps
    CourseAllocation = apps.get_model("course_allocation", "CourseAllocation")

    def _on_allocation_change(sender, instance, **kwargs):
        if not _cache_enabled():
            return
        try:
            _invalidate_for_allocation(instance)
        except Exception:  # noqa: BLE001
            logger.exception("PDF cache invalidation failed for CourseAllocation")

    post_save.connect(_on_allocation_change, sender=CourseAllocation, weak=False)
    post_delete.connect(_on_allocation_change, sender=CourseAllocation, weak=False)
