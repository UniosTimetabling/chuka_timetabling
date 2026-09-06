"""
odel_system/auto_allocate.py
=============================
Auto-allocation for the ODEL course-allocation page.

Unlike /cod/'s auto-allocator (course_allocation/auto_allocate_courses.py),
which is all-or-nothing (whole department + one semester at a time), this
gives the COD a course tree grouped by Program -> Year -> Semester where they
can:
  - tick "select all" for an entire program-year (both semesters if wanted),
  - tick individual courses one by one,
  - run Semester 1 only, Semester 2 only, or both at once in one click.

This exists because ODELCourseAllocation rows are opt-in (a COD chooses
which of their department's courses are actually delivered via ODEL) rather
than a 1:1 mirror of every ProgramCourse, so a blind "allocate everything in
this semester" run (like /cod/'s) isn't the right default here -- some
courses in the semester were deliberately never meant to get an ODEL row.

The lecturer-picking logic itself is NOT reimplemented here -- it's imported
straight from course_allocation.auto_allocate_courses, which is pure and
takes pre-loaded data structures (no hidden queries), so both auto-
allocators pick lecturers under exactly the same rules (mapping first,
department-preference, max-load cap, PG designation preference, etc).
"""

from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import ensure_csrf_cookie

from core.rbac import allowed_roles, Role

from odel_system.models import ODELCourseAllocation
from program_management.models import ProgramCourse, Program
from lecturer_portal.models import Lecturer
from department_management.models import Department

from odel_system.views_allocation import detect_user_department

from course_allocation.models import (
    LecturerCourseMapping,
    ProgramEnrollment,
    AcademicYearTracker,
)
from course_allocation.config_helpers import get_config
from course_allocation.auto_allocate_courses import _pick_lecturer
from course_allocation.cohort_utils import (
    get_cohort_courses_for_department,
    build_cohort_tree,
)

ALLOWED_ROLES = (Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
ADMIN_ROLES = (Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)


def _is_admin(user):
    if user.is_superuser:
        return True
    from core.rbac import user_has_role
    return user_has_role(user, *ADMIN_ROLES)


def _resolve_scope(request):
    """
    Returns (ok, error_response_or_None, department_or_None, is_admin).
    department is None for admins browsing all departments (they must then
    pass an explicit department_id in the request).
    """
    user = request.user
    if _is_admin(user):
        return True, None, None, True

    dept = detect_user_department(user)
    if not dept:
        return False, JsonResponse(
            {"success": False, "error": "Unable to detect your department."}, status=403
        ), None, False
    return True, None, dept, False


# ---------------------------------------------------------------------------
# 1. Course tree — what can be selected
# ---------------------------------------------------------------------------

@allowed_roles(*ALLOWED_ROLES)
@require_GET
def get_allocation_tree(request):
    """
    Returns the department's ProgramCourses grouped by Program -> Year,
    tagged with their ODEL allocation status, so the frontend can render a
    checkbox tree with "select all for this program-year" controls.

    Query params:
      department_id   required for admins, ignored (forced) for COD users.
      semesters       comma list, e.g. "1", "2" or "1,2". Defaults to "1,2".
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
        for a in ODELCourseAllocation.objects.filter(program_course_id__in=pc_ids)
        .select_related("lecturer")
    }

    def _course_extra(item):
        pc = item["program_course"]
        alloc = allocated.get(pc.id)
        return {
            "already_allocated": bool(alloc),
            "lecturer": alloc.lecturer.display_name if (alloc and alloc.lecturer) else None,
            "number_of_students": alloc.number_of_students if alloc else None,
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
      program_course_ids : list[int]   -- required, non-empty. This is the
                            *only* selection mechanism: the frontend sends
                            every checked course, whether that came from
                            ticking "select all" on a program-year group or
                            from picking courses one by one -- the backend
                            doesn't need to know which.
      department_id       : required for admins.
      overwrite            : bool, default False. If False, courses that
                            already have an ODELCourseAllocation are left
                            untouched (skipped). If True, they're re-picked
                            (existing row deleted then recreated, so the
                            Safe Undo log captures the change).
    """
    ok, err, dept, is_admin = _resolve_scope(request)
    if not ok:
        return err

    import json
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON body.")

    course_ids = data.get("program_course_ids") or []
    if not isinstance(course_ids, list) or not course_ids:
        return JsonResponse({"success": False, "error": "No courses selected."}, status=400)

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

    skipped_ids = {row["program_course_id"] for row in
                   ODELCourseAllocation.objects.filter(program_course_id__in=[pc.id for pc in pcs])
                   .values("program_course_id")}

    if not overwrite:
        pcs = [pc for pc in pcs if pc.id not in skipped_ids]
        skip_count = len(skipped_ids)
    else:
        skip_count = 0

    if not pcs:
        return JsonResponse({
            "success": True,
            "created": 0,
            "skipped": skip_count,
            "rows": [],
            "message": "Nothing to do — all selected courses already have an ODEL allocation.",
        })

    result_rows = []
    created_count = 0

    with transaction.atomic():
        if overwrite:
            ODELCourseAllocation.objects.filter(
                program_course_id__in=[pc.id for pc in pcs]
            ).delete()

        # Process per-semester so lecturer max-load capping stays coherent
        # with the per-semester meaning used everywhere else in the system
        # (a lecturer capped in Semester 1 isn't blocked from a full load in
        # Semester 2), exactly like /cod/'s _run_allocation.
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

        enroll_by_entry_year = {
            (e.program_id, e.entry_year): e.number_of_students
            for e in ProgramEnrollment.objects.filter(program__department=dept)
        }

        def _students_for(pc):
            entry_year = reference_year - (pc.year - 1)
            return enroll_by_entry_year.get((pc.program_id, entry_year), 0)

        for semester, sem_pcs in by_semester.items():
            alloc_count = {
                row["lecturer_id"]: row["cnt"]
                for row in ODELCourseAllocation.objects
                    .filter(lecturer__isnull=False, program_course__semester=semester)
                    .values("lecturer_id")
                    .annotate(cnt=Count("id"))
            }
            year_cov = {}
            for row in (
                ODELCourseAllocation.objects
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
                alloc = ODELCourseAllocation.objects.create(
                    program_course=pc,
                    lecturer=lecturer,
                    number_of_students=students,
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
                    "number_of_students": students,
                })

    return JsonResponse({
        "success": True,
        "created": created_count,
        "skipped": skip_count,
        "rows": result_rows,
        "message": f"{created_count} course(s) auto-allocated"
                   + (f", {skip_count} already-allocated course(s) skipped." if skip_count else "."),
    })
