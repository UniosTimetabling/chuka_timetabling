"""
resits_timetabling/resit_allocation_manager.py

Management actions for ResitCourseAllocation records:

  move_to_archive  (was "clear_allocations")
      POST  academic_year, semester  → snapshots every matching
            ResitCourseAllocation into ResitArchivedAllocation, then deletes
            the originals.  Requires the user to supply both academic_year
            AND semester in the POST body (the modal prompts for them).

  archive_allocations
      Identical behaviour — kept as a separate named action so the Archive
      card can still call it with an optional note field.

  get_archive_groups  (AJAX GET-like POST)
      Returns a summary of all ResitArchivedAllocation groups for the
      department, grouped by academic_year + semester.

  get_archive_rows
      Returns individual rows for a given archive group.

  delete_archive_group
      POST  academic_year, semester  → permanently deletes all
            ResitArchivedAllocation rows for that period.

  restore_from_archive
      POST  academic_year, semester  → re-creates ResitCourseAllocation
            rows from the archive for that period, skipping duplicates,
            then deletes the archive rows.

Permissions: COD, COD Admins, Timetabling Admins, Timetabler, superuser.
"""
from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render

from core.group_required import group_required
from department_management.models import Department

from .cod_panel import get_user_department
from .models import (
    ResitArchivedAllocation,
    ResitCourseAllocation,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Permission helpers
# ─────────────────────────────────────────────────────────────────────────────

_ALLOWED_GROUPS = {"COD", "COD Admins", "Timetabling Admins", "Timetabler"}


def _has_permission(user):
    """Return True if the user may mutate allocations / archives."""
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=_ALLOWED_GROUPS).exists()


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _allocation_qs(department, academic_year: str, semester: str):
    """Base queryset for a given department + period."""
    qs = ResitCourseAllocation.objects.filter(department=department)
    if academic_year:
        qs = qs.filter(academic_year=academic_year)
    if semester:
        qs = qs.filter(semester=semester)
    return qs


def _snapshot_allocation(
    alloc: ResitCourseAllocation,
    user,
    note: str = "",
    fallback_year: str = "",
    fallback_semester: str = "",
) -> ResitArchivedAllocation:
    """
    Copy one ResitCourseAllocation into ResitArchivedAllocation.
    Returns the (unsaved) archive row.

    Only course allocation fields are archived — timetable scheduling data
    (exam date, venue, times) is intentionally excluded.  The timetable rows
    are separate objects (ResitTimetable) and will be cascade-deleted when the
    parent ResitCourseAllocation is deleted; they should not be copied here.

    fallback_year / fallback_semester are used when the live allocation has
    blank academic_year / semester fields (e.g. older records).  The values
    typed by the user in the "Move to Archive" form are passed here so the
    archived row is always findable by period.
    """
    # Prefer the value stored on the allocation; fall back to what the user typed.
    academic_year = alloc.academic_year or fallback_year
    semester      = alloc.semester      or fallback_semester

    return ResitArchivedAllocation(
        course_code              = alloc.course_code,
        course_name              = alloc.course_name,
        academic_year            = academic_year,
        semester                 = semester,
        original_allocation      = alloc,
        department               = alloc.department,
        origin_department        = alloc.origin_department,
        program                  = alloc.program,
        program_course           = alloc.program_course,
        lecturer                 = alloc.lecturer,
        number_of_students       = alloc.number_of_students,
        submitted_to_timetabling = alloc.submitted_to_timetabling,
        scheduled                = alloc.scheduled,
        # metadata
        archived_by  = user,
        archive_note = note,
    )


def _do_archive_qs(qs, user, note="", fallback_year="", fallback_semester=""):
    """
    Snapshot every ResitCourseAllocation in *qs* into ResitArchivedAllocation,
    then delete the originals by their explicit PKs.

    Only course allocation fields are archived — timetable data is not copied.
    The cascade delete of the ResitCourseAllocation rows will automatically
    remove any linked ResitTimetable / ResitTempTimetable children.

    fallback_year / fallback_semester are passed through to _snapshot_allocation
    so that allocations whose own academic_year / semester fields are blank are
    still stored under the correct period in the archive.

    Steps (all inside one transaction):
      1. Materialise the queryset.
      2. bulk_create the archive snapshot rows.
      3. Delete originals by explicit PK list.

    Returns (archived_count, deleted_count).
    """
    allocs = list(
        qs.select_related(
            "department", "origin_department", "program", "program_course", "lecturer"
        )
    )
    if not allocs:
        return 0, 0

    # Capture PKs now so the delete is explicit — no lazy queryset re-evaluation
    alloc_ids = [a.pk for a in allocs]

    # Build archive rows while originals and their timetable rows still exist
    archive_rows = [
        _snapshot_allocation(a, user, note, fallback_year, fallback_semester)
        for a in allocs
    ]

    with transaction.atomic():
        ResitArchivedAllocation.objects.bulk_create(archive_rows, batch_size=200)
        # Delete by PK list — safe, unambiguous
        deleted, _ = ResitCourseAllocation.objects.filter(pk__in=alloc_ids).delete()

    return len(archive_rows), deleted


# ─────────────────────────────────────────────────────────────────────────────
# AJAX action handlers
# ─────────────────────────────────────────────────────────────────────────────

def _handle_move_to_archive(request, department):
    """
    POST: action=move_to_archive, academic_year=..., semester=...

    Moves ALL current ResitCourseAllocation rows for this department into the
    archive, stamping each archived row with the academic_year and semester
    the user typed.  We do NOT filter the live table by year/semester because
    those fields are often blank on existing records — the user-supplied values
    are the archive label, not a filter condition.
    """
    if not _has_permission(request.user):
        return JsonResponse({"status": "error", "message": "Permission denied."}, status=403)

    academic_year = request.POST.get("academic_year", "").strip()
    semester      = request.POST.get("semester", "").strip()
    note          = request.POST.get("note", "").strip()

    if not academic_year or not semester:
        return JsonResponse(
            {"status": "error",
             "message": "Both Academic Year and Semester are required."},
            status=400,
        )

    # Fetch ALL allocations for this department — year/semester are used as the
    # archive label (fallback), not as filter criteria.
    qs = ResitCourseAllocation.objects.filter(department=department)
    count = qs.count()

    if count == 0:
        return JsonResponse({
            "status":   "success",
            "message":  "No active allocations found for your department — nothing archived.",
            "archived": 0,
        })

    archived, deleted = _do_archive_qs(qs, request.user, note, academic_year, semester)

    logger.info(
        "resit_allocation_manager: %s moved %d allocations to archive "
        "(dept=%s, year=%s, sem=%s)",
        request.user, archived, department, academic_year, semester,
    )
    return JsonResponse({
        "status":   "success",
        "archived": archived,
        "deleted":  deleted,
        "message":  (
            f"{archived} allocation(s) moved to archive under {academic_year} Semester {semester}."
        ),
    })


def _handle_archive(request, department):
    """
    POST: action=archive_allocations, academic_year=..., semester=..., note=...
    Same as move_to_archive but accepts an optional note.
    """
    return _handle_move_to_archive(request, department)


def _handle_get_archive_groups(request, department):
    """
    Returns a summary of ResitArchivedAllocation groups for this department.
    """
    qs = (
        ResitArchivedAllocation.objects
        .filter(department=department)
        .values("academic_year", "semester")
        .distinct()
        .order_by("-academic_year", "-semester")
    )

    groups = []
    for g in qs:
        ay  = g["academic_year"]
        sem = g["semester"]
        count = ResitArchivedAllocation.objects.filter(
            department=department,
            academic_year=ay,
            semester=sem,
        ).count()
        label = (
            f"{ay} — Semester {sem}" if ay and sem
            else (ay or sem or "Unknown")
        )
        groups.append({
            "academic_year": ay,
            "semester":      sem,
            "label":         label,
            "count":         count,
        })

    return JsonResponse({"status": "success", "groups": groups})


def _handle_get_archive_rows(request, department):
    """
    Returns individual rows for a given academic_year + semester archive group.
    """
    academic_year = request.POST.get("academic_year", "").strip()
    semester      = request.POST.get("semester", "").strip()

    qs = ResitArchivedAllocation.objects.filter(
        department=department,
        academic_year=academic_year,
        semester=semester,
    ).select_related("program", "lecturer").order_by("course_code")

    rows = []
    for a in qs[:500]:
        program  = a.program
        lecturer = a.lecturer
        rows.append({
            "id":            a.id,
            "course_code":   a.course_code,
            "course_name":   a.course_name,
            "academic_year": a.academic_year,
            "semester":      a.semester,
            "program_id":    a.program_id,
            "program_name":  program.name if program else "",
            "lecturer_name": lecturer.display_name if lecturer else "",
            "students":      a.number_of_students,
            "exam_date":     str(a.exam_date) if a.exam_date else "",
            "venue_code":    a.venue_code,
            "scheduled":     a.scheduled,
        })

    return JsonResponse({"status": "success", "rows": rows, "total": len(rows)})


def _handle_delete_archive_group(request, department):
    """
    POST: action=delete_archive_group, academic_year=..., semester=...
    Permanently deletes all ResitArchivedAllocation rows for that period.
    """
    if not _has_permission(request.user):
        return JsonResponse({"status": "error", "message": "Permission denied."}, status=403)

    academic_year = request.POST.get("academic_year", "").strip()
    semester      = request.POST.get("semester", "").strip()

    if not academic_year or not semester:
        return JsonResponse(
            {"status": "error", "message": "Both Academic Year and Semester are required."},
            status=400,
        )

    qs = ResitArchivedAllocation.objects.filter(
        department=department,
        academic_year=academic_year,
        semester=semester,
    )
    count = qs.count()
    if count == 0:
        return JsonResponse({
            "status":  "success",
            "message": "No archived records found for that period.",
            "deleted": 0,
        })

    with transaction.atomic():
        deleted, _ = qs.delete()

    logger.info(
        "resit_allocation_manager: %s deleted archive group "
        "(dept=%s, year=%s, sem=%s, rows=%d)",
        request.user, department, academic_year, semester, deleted,
    )
    return JsonResponse({
        "status":  "success",
        "deleted": deleted,
        "message": f"{deleted} archived record(s) permanently deleted.",
    })


def _handle_restore_from_archive(request, department):
    """
    POST: action=restore_from_archive, academic_year=..., semester=...
    Re-creates ResitCourseAllocation rows from the archive for that period,
    skipping any that already exist (same program + course_code + academic_year
    + semester).  Then deletes the restored archive rows.
    """
    if not _has_permission(request.user):
        return JsonResponse({"status": "error", "message": "Permission denied."}, status=403)

    academic_year = request.POST.get("academic_year", "").strip()
    semester      = request.POST.get("semester", "").strip()

    if not academic_year or not semester:
        return JsonResponse(
            {"status": "error", "message": "Both Academic Year and Semester are required."},
            status=400,
        )

    archive_qs = ResitArchivedAllocation.objects.filter(
        department=department,
        academic_year=academic_year,
        semester=semester,
    ).select_related("department", "origin_department", "program", "program_course", "lecturer")

    archived_rows = list(archive_qs)
    if not archived_rows:
        return JsonResponse({
            "status":   "success",
            "message":  "No archived records found for that period.",
            "restored": 0,
            "skipped":  0,
        })

    # Build duplicate-detection set
    existing_keys = set(
        ResitCourseAllocation.objects.filter(department=department)
        .values_list("program_id", "course_code", "academic_year", "semester")
    )

    to_create   = []
    restored_ids = []
    skipped     = 0

    for a in archived_rows:
        key = (a.program_id, a.course_code, a.academic_year, a.semester)
        if key in existing_keys:
            skipped += 1
            continue
        to_create.append(ResitCourseAllocation(
            course_code              = a.course_code,
            course_name              = a.course_name,
            department               = a.department,
            origin_department        = a.origin_department,
            program                  = a.program,
            program_course           = a.program_course,
            lecturer                 = a.lecturer,
            number_of_students       = a.number_of_students,
            submitted_to_timetabling = False,   # reset — needs re-submission
            scheduled                = False,
            academic_year            = a.academic_year,
            semester                 = a.semester,
            created_by               = request.user,
        ))
        existing_keys.add(key)
        restored_ids.append(a.id)

    restored = 0
    with transaction.atomic():
        if to_create:
            ResitCourseAllocation.objects.bulk_create(to_create, batch_size=200)
            restored = len(to_create)
        # Remove the archive rows that were successfully restored
        if restored_ids:
            ResitArchivedAllocation.objects.filter(id__in=restored_ids).delete()

    logger.info(
        "resit_allocation_manager: %s restored %d allocations from archive "
        "(dept=%s, year=%s, sem=%s, skipped=%d)",
        request.user, restored, department, academic_year, semester, skipped,
    )
    return JsonResponse({
        "status":   "success",
        "restored": restored,
        "skipped":  skipped,
        "message":  (
            f"{restored} allocation(s) restored to active list"
            + (f", {skipped} skipped (already exist)." if skipped else ".")
        ),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Main view
# ─────────────────────────────────────────────────────────────────────────────


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def resit_allocation_manager(request):
    """
    GET  → render the allocation manager page
    POST (AJAX) →
      action=move_to_archive        — move allocations to archive (prompts for year+sem)
      action=archive_allocations    — alias for move_to_archive (with optional note)
      action=get_archive_groups     — list archive groups for this dept
      action=get_archive_rows       — rows for a specific archive group
      action=delete_archive_group   — permanently delete an archive group
      action=restore_from_archive   — restore an archive group back to active
      action=count_allocations      — preview count
      action=get_stats              — dashboard counts
    """
    try:
        department = get_user_department(request.user)
        if not department:
            ctx = {
                "department":     None,
                "error": "No department associated with your account.",
            }
            return render(request, "resits_timetabling/resit_allocation_manager.html", ctx)

        if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
            action = request.POST.get("action")

            if action == "move_to_archive":
                return _handle_move_to_archive(request, department)
            if action == "archive_allocations":
                return _handle_archive(request, department)
            if action == "get_archive_groups":
                return _handle_get_archive_groups(request, department)
            if action == "get_archive_rows":
                return _handle_get_archive_rows(request, department)
            if action == "delete_archive_group":
                return _handle_delete_archive_group(request, department)
            if action == "restore_from_archive":
                return _handle_restore_from_archive(request, department)
            if action == "count_allocations":
                # Count ALL active allocations for this department.
                # The year/semester the user typed are archive labels, not filters.
                count = ResitCourseAllocation.objects.filter(department=department).count()
                return JsonResponse({"status": "success", "count": count})
            if action == "get_stats":
                return JsonResponse({
                    "status":        "success",
                    "current_count": ResitCourseAllocation.objects.filter(department=department).count(),
                    "archive_count": ResitArchivedAllocation.objects.filter(department=department).count(),
                })
            return JsonResponse({"status": "error", "message": "Unknown action."}, status=400)

        # GET — summary counts for context
        current_count = ResitCourseAllocation.objects.filter(department=department).count()
        archive_count = ResitArchivedAllocation.objects.filter(department=department).count()

        ctx = {
            "department":     department,
            "current_count":  current_count,
            "archive_count":  archive_count,
        }
        return render(request, "resits_timetabling/resit_allocation_manager.html", ctx)

    except Exception as exc:
        import traceback
        logger.error("resit_allocation_manager unhandled: %s", traceback.format_exc())
        if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"status": "error", "message": str(exc)}, status=500)
        ctx = {
            "department":     None,
            "error": f"Unexpected error: {exc}",
        }
        return render(request, "resits_timetabling/resit_allocation_manager.html", ctx)