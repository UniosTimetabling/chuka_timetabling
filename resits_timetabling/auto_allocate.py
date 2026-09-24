"""
resits_timetabling/auto_allocate.py
=====================================
Auto-allocation for the Resits COD panel (cod_panel.py).

Same shape as odel_system/auto_allocate.py, campuses_timetable/auto_allocate.py
and course_allocation/lab_auto_allocate.py: a course tree grouped by
Program -> Year, with per-group "select all" checkboxes plus individual
course checkboxes, and a semester picker that accepts Semester 1, Semester
2, or both in one run.

Two things are genuinely different here vs. ODEL/Campus:

1. ResitCourseAllocation.number_of_students is NOT an enrollment figure --
   every save() recomputes it from student_registrations.count() (see
   ResitCourseAllocation.save()). Resit numbers only exist once students
   actually register for a resit, which happens after this tool runs, so
   auto-allocate always creates rows with 0 students; there is nothing to
   guess here (unlike COD/ODEL/Campus, which size classes off
   ProgramEnrollment for the whole cohort).

2. academic_year / semester are free-text strings on the model (e.g.
   "2025/2026" / "S1"), not FK/int fields, because a resit sitting can
   legitimately be labelled for a period that doesn't equal the course's
   own curriculum year/semester. This tool follows the same convention the
   manual "create_allocation" form already uses: semester is derived from
   the ProgramCourse ("S1"/"S2"), and academic_year defaults to the current
   institutional academic year (from AcademicYearTracker) unless the COD
   types a different one into the run dialog.

Lecturer-picking itself is not reimplemented -- it's imported from
course_allocation.auto_allocate_courses, so Resit auto-allocation follows
the same rules (mapping first, department preference, max-load cap, PG
designation preference) as /cod/, /odel/, Campuses, and Lab.
"""
import json

from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import ensure_csrf_cookie

from core.rbac import allowed_roles, Role, user_has_role

from resits_timetabling.models import ResitCourseAllocation
from resits_timetabling.cod_panel import get_user_department as detect_user_department

from course_allocation.models import (
    LecturerCourseMapping,
    AcademicYearTracker,
)
from course_allocation.config_helpers import get_config
from course_allocation.auto_allocate_courses import _pick_lecturer
from course_allocation.cohort_utils import (
    get_cohort_courses_for_department,
    build_cohort_tree,
)

from program_management.models import ProgramCourse
from lecturer_portal.models import Lecturer
from department_management.models import Department

# Mirrors the exact role set already allowed on the Resits COD panel itself
# (resits_timetabling/cod_panel.py).
ALLOWED_ROLES = (Role.COD, Role.COD_ADMIN, Role.SUDO)
ADMIN_ROLES = (Role.SUDO,)


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


def _default_academic_year():
    year = AcademicYearTracker.get_current().current_year
    return f"{year}/{year + 1}"


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
    # curriculum version (student_cohort) that actually applies to it.
    cohort_items = get_cohort_courses_for_department(
        dept, semesters, reference_year=reference_year, program_id=program_id,
    )
    pc_ids = [item["program_course"].id for item in cohort_items]

    # NOTE: .values("lecturer__display_name") does NOT work -- display_name
    # is a Python @property on Lecturer, not a DB column, so that raises a
    # FieldError on every call. select_related + read the property instead.
    allocated = {
        a.program_course_id: a
        for a in ResitCourseAllocation.objects.filter(
            program_course_id__in=pc_ids, department=dept,
        ).select_related("lecturer")
    }

    def _course_extra(item):
        pc = item["program_course"]
        alloc = allocated.get(pc.id)
        return {
            "already_allocated": bool(alloc),
            "lecturer": alloc.lecturer.display_name if (alloc and alloc.lecturer) else None,
            "resit_period": f"{alloc.academic_year} {alloc.semester}" if alloc else None,
        }

    programs_out = build_cohort_tree(cohort_items, course_extra_fn=_course_extra)

    return JsonResponse({
        "success": True,
        "department": dept.name,
        "department_id": dept.id,
        "semesters": semesters,
        "default_academic_year": _default_academic_year(),
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
      academic_year        : optional string e.g. "2025/2026", default = current.
      overwrite            : bool, default False. If False, courses that
                            already have a ResitCourseAllocation for this
                            department are skipped. If True, that allocation
                            is deleted and re-created (registered students,
                            if any, are lost with it -- same as deleting it
                            manually would do).
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

    academic_year = (data.get("academic_year") or "").strip() or _default_academic_year()
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

    already = {
        row["program_course_id"]
        for row in ResitCourseAllocation.objects.filter(
            program_course_id__in=[pc.id for pc in pcs], department=dept,
        ).values("program_course_id")
    }

    if not overwrite:
        pcs = [pc for pc in pcs if pc.id not in already]
        skip_count = len(already)
    else:
        skip_count = 0

    if not pcs:
        return JsonResponse({
            "success": True,
            "created": 0,
            "skipped": skip_count,
            "rows": [],
            "message": "Nothing to do — all selected courses already have a resit allocation.",
        })

    result_rows = []
    created_count = 0

    with transaction.atomic():
        if overwrite:
            ResitCourseAllocation.objects.filter(
                program_course_id__in=[pc.id for pc in pcs], department=dept,
            ).delete()

        by_semester = {}
        for pc in pcs:
            by_semester.setdefault(pc.semester, []).append(pc)

        cfg = get_config(dept)

        mapping_idx = {}
        for m in LecturerCourseMapping.objects.select_related("lecturer").prefetch_related("courses"):
            for c in m.courses.all():
                mapping_idx.setdefault(c.id, []).append(m.lecturer)

        dept_lecs = list(Lecturer.objects.filter(department=dept).select_related("department"))
        other_lecs = list(Lecturer.objects.exclude(department=dept).select_related("department"))
        all_lecs = dept_lecs + other_lecs

        for semester, sem_pcs in by_semester.items():
            # Resit-supervision load counted separately from lecture load
            # (CourseAllocation) -- against ResitCourseAllocation only,
            # scoped per-semester like everywhere else in the system.
            alloc_count = {
                row["lecturer_id"]: row["cnt"]
                for row in ResitCourseAllocation.objects
                    .filter(lecturer__isnull=False, program_course__semester=semester)
                    .values("lecturer_id")
                    .annotate(cnt=Count("id"))
            }
            year_cov = {}
            for row in (
                ResitCourseAllocation.objects
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
                alloc = ResitCourseAllocation.objects.create(
                    course_code=pc.course_code,
                    course_name=pc.course_name,
                    department=dept,
                    origin_department=pc.program.department,
                    program=pc.program,
                    program_course=pc,
                    lecturer=lecturer,
                    number_of_students=0,   # recomputed from registrations on save()
                    academic_year=academic_year,
                    semester=f"S{semester}",
                    created_by=request.user,
                )
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
                    "academic_year": academic_year,
                })

    return JsonResponse({
        "success": True,
        "created": created_count,
        "skipped": skip_count,
        "rows": result_rows,
        "message": f"{created_count} course(s) auto-allocated for {academic_year}"
                   + (f", {skip_count} already-allocated course(s) skipped." if skip_count else "."),
    })
