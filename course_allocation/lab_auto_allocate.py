"""
course_allocation/lab_auto_allocate.py
=======================================
Auto-allocation for the Lab & Workshop Allocations page (lab_allocations.py).

Same shape as odel_system/auto_allocate.py and campuses_timetable/auto_allocate.py:
a course tree grouped by Program -> Year, with per-group "select all" checkboxes
plus individual course checkboxes, and a semester picker that accepts Semester 1,
Semester 2, or both in one run.

Two things are genuinely new here vs. ODEL/Campus:

1. LabAllocation has no split/selection-group concept, but it DOES need at
   least one venue (LabVenue) picked, not just a lecturer -- the manual form
   requires it too. This module auto-picks a small candidate pool of venues
   whose capacity covers the enrolled class size (falling back to the
   largest venues available if nothing is big enough), leaving the final
   single-venue-per-session choice to the lab autoscheduler, exactly like
   the manual form's own multi-select behaviour already does.

2. "Already allocated" has two shades here: a course can be the *primary*
   program_course of a LabAllocation, or it can appear in another
   allocation's `additional_courses` M2M (grouped/shared lab slot). Only
   the primary case is safely re-allocatable by this tool (deleting +
   recreating a shared-slot allocation from one of its secondary courses
   would silently disturb the other courses sharing that slot), so
   secondary-only courses are always skipped and flagged in the tree/response.

Lecturer-picking itself is not reimplemented -- it's imported from
course_allocation.auto_allocate_courses, so Lab auto-allocation follows the
same rules (mapping first, department preference, max-load cap, PG
designation preference) as /cod/, /odel/, and Campuses.
"""
import json

from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import ensure_csrf_cookie

from core.rbac import allowed_roles, Role, user_has_role

from course_allocation.models import (
    LabAllocation,
    LecturerCourseMapping,
    ProgramEnrollment,
    AcademicYearTracker,
)
from course_allocation.lab_allocations import detect_user_department
from course_allocation.config_helpers import get_config
from course_allocation.auto_allocate_courses import _pick_lecturer
from course_allocation.cohort_utils import (
    get_cohort_courses_for_department,
    build_cohort_tree,
)

from program_management.models import ProgramCourse
from lecturer_portal.models import Lecturer
from department_management.models import Department
from room_management.models import LabVenue

# Mirrors the exact role set already allowed on the Lab Allocations page
# itself (course_allocation/lab_allocations.py) -- no DIRECTOR/TIMETABLE_ADMIN
# here since they can't reach this page at all.
ALLOWED_ROLES = (Role.COD, Role.COD_ADMIN, Role.SUDO)
ADMIN_ROLES = (Role.SUDO,)

# How many candidate venues to hand to the autoscheduler per allocation.
MAX_CANDIDATE_VENUES = 3


def _is_admin(user):
    if user.is_superuser:
        return True
    return user_has_role(user, *ADMIN_ROLES)


def _resolve_scope(request):
    """Returns (ok, error_response_or_None, department_or_None, is_admin)."""
    user = request.user
    if _is_admin(user):
        return True, None, None, True

    dept = detect_user_department(user)
    if not dept:
        return False, JsonResponse(
            {"success": False, "error": "Unable to detect your department."}, status=403
        ), None, False
    return True, None, dept, False


def _pick_venues(students, all_venues):
    """
    Pick a small candidate pool of venues for the autoscheduler to choose
    from -- smallest venues that fit the class first, falling back to the
    largest venues available if nothing fits.
    """
    students = students or 0
    fitting = sorted(
        [v for v in all_venues if v.capacity is not None and v.capacity >= students],
        key=lambda v: v.capacity,
    )
    if fitting:
        return fitting[:MAX_CANDIDATE_VENUES]

    # Nothing fits (or capacities are unknown) -- fall back to the largest
    # known-capacity venues, then unknown-capacity ones, so a run never
    # leaves a course with zero venues.
    known = sorted(
        [v for v in all_venues if v.capacity is not None],
        key=lambda v: v.capacity, reverse=True,
    )
    unknown = [v for v in all_venues if v.capacity is None]
    return (known + unknown)[:MAX_CANDIDATE_VENUES]


# ---------------------------------------------------------------------------
# 1. Course tree — what can be selected
# ---------------------------------------------------------------------------

@allowed_roles(*ALLOWED_ROLES)
@require_GET
def get_allocation_tree(request):
    """
    Query params:
      department_id   required for admins.
      semesters       comma list, e.g. "1" or "1,2". Defaults to "1,2".
      program_id      optional, narrow to one program.
    """
    ok, err, dept, is_admin = _resolve_scope(request)
    if not ok:
        return err

    if is_admin:
        dept_id = request.GET.get("department_id")
        if not dept_id:
            return HttpResponseBadRequest("department_id is required.")
        try:
            dept = Department.objects.get(id=dept_id)
        except Department.DoesNotExist:
            return JsonResponse({"success": False, "error": "Department not found."}, status=404)

    sem_param = request.GET.get("semesters", "1,2")
    try:
        semesters = sorted({int(s) for s in sem_param.split(",") if s.strip()})
    except ValueError:
        return HttpResponseBadRequest("Invalid semesters value.")
    semesters = [s for s in semesters if s in (1, 2)] or [1, 2]

    program_id = request.GET.get("program_id")
    reference_year = AcademicYearTracker.get_current().current_year

    # Cohort-aware course list: each intake (entry_year) only shows the
    # curriculum version (student_cohort) that actually applies to it, so
    # two intakes of the same program mid-curriculum-change don't get
    # each other's course codes mixed into one flat "Year N" bucket.
    cohort_items = get_cohort_courses_for_department(
        dept, semesters, reference_year=reference_year, program_id=program_id,
    )
    pc_ids = [item["program_course"].id for item in cohort_items]

    # NOTE: .values("lecturer__display_name") does NOT work -- display_name
    # is a Python @property on Lecturer, not a DB column, so that raises a
    # FieldError on every call (this was the cause of the 500 error on this
    # endpoint). select_related + read the property in Python instead.
    primary = {
        a.program_course_id: a
        for a in LabAllocation.objects.filter(program_course_id__in=pc_ids)
        .select_related("lecturer")
    }
    secondary_ids = set(
        LabAllocation.objects.filter(additional_courses__id__in=pc_ids)
        .values_list("additional_courses__id", flat=True)
    )

    def _course_extra(item):
        pc = item["program_course"]
        alloc = primary.get(pc.id)
        is_secondary = pc.id in secondary_ids and not alloc
        return {
            "already_allocated": bool(alloc),
            "lecturer": alloc.lecturer.display_name if (alloc and alloc.lecturer) else None,
            "number_of_students": alloc.number_of_students if alloc else None,
            # Shared-slot secondary course -- not safely re-allocatable here.
            "locked": is_secondary,
        }

    programs_out = build_cohort_tree(cohort_items, course_extra_fn=_course_extra)

    return JsonResponse({
        "success": True,
        "department": dept.name,
        "department_id": dept.id,
        "semesters": semesters,
        "programs": programs_out,
    })


# ---------------------------------------------------------------------------
# 2. Run allocation on a chosen set of ProgramCourse ids
# ---------------------------------------------------------------------------

@allowed_roles(*ALLOWED_ROLES)
@require_POST
@ensure_csrf_cookie
def run_allocation(request):
    """
    POST body (JSON):
      program_course_ids : list[int]   -- required, non-empty.
      department_id       : required for admins.
      is_workshop_course  : bool, default False. Applies to every allocation
                            created in this run.
      overwrite            : bool, default False. If False, courses that
                            already have a LabAllocation as their primary
                            course are skipped. If True, that allocation is
                            deleted and re-created. Courses that only appear
                            as a *secondary* course of someone else's shared
                            lab slot are always skipped (see module docstring).
    """
    ok, err, dept, is_admin = _resolve_scope(request)
    if not ok:
        return err

    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON body.")

    course_ids = data.get("program_course_ids") or []
    if not isinstance(course_ids, list) or not course_ids:
        return JsonResponse({"success": False, "error": "No courses selected."}, status=400)

    is_workshop_course = bool(data.get("is_workshop_course", False))
    overwrite = bool(data.get("overwrite", False))

    if is_admin:
        dept_id = data.get("department_id")
        if not dept_id:
            return HttpResponseBadRequest("department_id is required.")
        try:
            dept = Department.objects.get(id=dept_id)
        except Department.DoesNotExist:
            return JsonResponse({"success": False, "error": "Department not found."}, status=404)

    pcs = list(
        ProgramCourse.objects.filter(id__in=course_ids, program__department=dept)
        .select_related("program", "program__department")
    )
    if not pcs:
        return JsonResponse(
            {"success": False, "error": "None of the selected courses belong to this department."},
            status=400,
        )

    pc_ids = [pc.id for pc in pcs]

    primary_ids = set(
        LabAllocation.objects.filter(program_course_id__in=pc_ids)
        .values_list("program_course_id", flat=True)
    )
    secondary_ids = set(
        LabAllocation.objects.filter(additional_courses__id__in=pc_ids)
        .values_list("additional_courses__id", flat=True)
    ) - primary_ids

    # Secondary-only courses are never touched by this tool.
    locked_count = 0
    if secondary_ids:
        locked_count = len(secondary_ids)
        pcs = [pc for pc in pcs if pc.id not in secondary_ids]

    if not overwrite:
        skip_count = len([pc for pc in pcs if pc.id in primary_ids])
        pcs = [pc for pc in pcs if pc.id not in primary_ids]
    else:
        skip_count = 0

    if not pcs:
        return JsonResponse({
            "success": True,
            "created": 0,
            "skipped": skip_count,
            "locked": locked_count,
            "rows": [],
            "message": "Nothing to do — all selected courses already have a lab allocation "
                       "or are locked inside a shared lab slot.",
        })

    result_rows = []
    created_count = 0

    with transaction.atomic():
        if overwrite:
            LabAllocation.objects.filter(program_course_id__in=[pc.id for pc in pcs]).delete()

        by_semester = {}
        for pc in pcs:
            by_semester.setdefault(pc.semester, []).append(pc)

        cfg = get_config(dept)
        reference_year = AcademicYearTracker.get_current().current_year

        mapping_idx = {}
        for m in LecturerCourseMapping.objects.select_related("lecturer").prefetch_related("courses"):
            for c in m.courses.all():
                mapping_idx.setdefault(c.id, []).append(m.lecturer)

        dept_lecs = list(Lecturer.objects.filter(department=dept).select_related("department"))
        other_lecs = list(Lecturer.objects.exclude(department=dept).select_related("department"))
        all_lecs = dept_lecs + other_lecs

        all_venues = list(LabVenue.objects.all())

        enroll_by_entry_year = {
            (e.program_id, e.entry_year): e.number_of_students
            for e in ProgramEnrollment.objects.filter(program__department=dept)
        }

        def _students_for(pc):
            entry_year = reference_year - (pc.year - 1)
            return enroll_by_entry_year.get((pc.program_id, entry_year), 0)

        for semester, sem_pcs in by_semester.items():
            # Lab-supervision load is capped separately from lecture load
            # (CourseAllocation) -- it's counted against LabAllocation only,
            # scoped per-semester like everywhere else in the system.
            alloc_count = {
                row["lecturer_id"]: row["cnt"]
                for row in LabAllocation.objects
                    .filter(lecturer__isnull=False, program_course__semester=semester)
                    .values("lecturer_id")
                    .annotate(cnt=Count("id"))
            }
            year_cov = {}
            for row in (
                LabAllocation.objects
                .filter(lecturer__isnull=False, program_course__isnull=False)
                .values("lecturer_id", "program_course__program_id", "program_course__year")
            ):
                year_cov.setdefault(row["lecturer_id"], set()).add(
                    (row["program_course__program_id"], row["program_course__year"])
                )

            for pc in sem_pcs:
                lecturer = _pick_lecturer(
                    pc, mapping_idx, alloc_count, year_cov,
                    dept, dept_lecs, other_lecs, all_lecs, cfg,
                )
                students = _students_for(pc)
                venues = _pick_venues(students, all_venues)

                alloc = LabAllocation.objects.create(
                    program_course=pc,
                    lecturer=lecturer,
                    number_of_students=students,
                    is_workshop_course=is_workshop_course,
                )
                if venues:
                    alloc.venues.set(venues)

                created_count += 1
                if lecturer:
                    alloc_count[lecturer.id] = alloc_count.get(lecturer.id, 0) + 1
                    year_cov.setdefault(lecturer.id, set()).add((pc.program_id, pc.year))

                result_rows.append({
                    "id": alloc.id,
                    "program_course_id": pc.id,
                    "program": pc.program.name,
                    "course_code": pc.course_code,
                    "course_name": pc.course_name,
                    "year": pc.year,
                    "semester": pc.semester,
                    "lecturer": lecturer.display_name if lecturer else "Unassigned",
                    "number_of_students": students,
                    "venues": ", ".join(v.code for v in venues) if venues else "—",
                })

    message = f"{created_count} course(s) auto-allocated"
    extras = []
    if skip_count:
        extras.append(f"{skip_count} already-allocated course(s) skipped")
    if locked_count:
        extras.append(f"{locked_count} course(s) locked inside a shared lab slot skipped")
    if extras:
        message += ", " + ", ".join(extras) + "."
    else:
        message += "."

    return JsonResponse({
        "success": True,
        "created": created_count,
        "skipped": skip_count,
        "locked": locked_count,
        "rows": result_rows,
        "message": message,
    })
