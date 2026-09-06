"""
campuses_timetable/auto_allocate.py
=====================================
Auto-allocation for the Campus course-allocation panel.

Same shape as odel_system/auto_allocate.py: a course tree grouped by
Program -> Year, with per-group "select all" checkboxes plus individual
course checkboxes, and a semester picker that accepts Semester 1, Semester
2, or both in one run.

The one thing genuinely new here (vs. ODEL) is that CampusCourseAllocation
is scoped to a *campus* as well as a course -- the same ProgramCourse can
legitimately get a separate allocation per campus it's taught at
(unique_together = ['program', 'course_code', 'campus']). So the tree /
run endpoints both take a required campus_id, and "already allocated"
means "already allocated for THIS campus" -- the same course can still be
freely auto-allocated again for a different campus.

Lecturer-picking itself is not reimplemented -- it's imported from
course_allocation.auto_allocate_courses, so Campus auto-allocation follows
exactly the same rules (mapping first, department preference, max-load cap,
PG designation preference) as /cod/ and /odel/.
"""
import json

from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import ensure_csrf_cookie

from core.rbac import allowed_roles, Role, user_has_role

from campuses_timetable.models import CampusCourseAllocation, Campus
from campuses_timetable.tracker_service import _current_academic_year
from campuses_timetable.course_allocation_views import detect_user_department

from program_management.models import ProgramCourse
from lecturer_portal.models import Lecturer
from department_management.models import Department

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


# ---------------------------------------------------------------------------
# 1. Course tree — what can be selected, for a given campus
# ---------------------------------------------------------------------------

@allowed_roles(*ALLOWED_ROLES)
@require_GET
def get_allocation_tree(request):
    """
    Query params:
      department_id   required for admins.
      campus_id       required — allocation status is per-campus.
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

    campus_id = request.GET.get("campus_id")
    if not campus_id:
        return HttpResponseBadRequest("campus_id is required.")
    try:
        campus = Campus.objects.get(id=campus_id)
    except Campus.DoesNotExist:
        return JsonResponse({"success": False, "error": "Campus not found."}, status=404)

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
        for a in CampusCourseAllocation.objects.filter(
            program_course_id__in=pc_ids, campus_id=campus_id,
        ).select_related("lecturer")
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
        "campus": campus.name,
        "campus_id": campus.id,
        "semesters": semesters,
        "programs": programs_out,
    })


# ---------------------------------------------------------------------------
# 2. Run allocation on a chosen set of ProgramCourse ids, for one campus
# ---------------------------------------------------------------------------

@allowed_roles(*ALLOWED_ROLES)
@require_POST
@ensure_csrf_cookie
def run_allocation(request):
    """
    POST body (JSON):
      program_course_ids : list[int]   -- required, non-empty. Every checked
                            course, whether picked via a "select all" group
                            toggle or one by one -- the backend treats both
                            the same way.
      department_id       : required for admins.
      campus_id            : required — which campus this run allocates for.
      teaching_campus_id  : optional, defaults to campus_id.
      delivery_mode        : "PHYSICAL" | "ONLINE" | "BLENDED", default PHYSICAL.
      academic_year         : optional string e.g. "2025/2026", default = current.
      overwrite            : bool, default False. If False, courses that
                            already have a CampusCourseAllocation for THIS
                            campus are skipped. If True, they're re-picked.
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

    campus_id = data.get("campus_id")
    if not campus_id:
        return JsonResponse({"success": False, "error": "campus_id is required."}, status=400)
    try:
        campus = Campus.objects.get(id=campus_id)
    except Campus.DoesNotExist:
        return JsonResponse({"success": False, "error": "Campus not found."}, status=404)

    teaching_campus_id = data.get("teaching_campus_id") or campus_id
    delivery_mode = data.get("delivery_mode") or "PHYSICAL"
    if delivery_mode not in dict(CampusCourseAllocation.DELIVERY_MODES):
        delivery_mode = "PHYSICAL"
    academic_year = (data.get("academic_year") or "").strip() or _current_academic_year()
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
        for row in CampusCourseAllocation.objects.filter(
            program_course_id__in=[pc.id for pc in pcs], campus_id=campus_id,
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
            "message": f"Nothing to do — all selected courses already have a {campus.code} allocation.",
        })

    result_rows = []
    created_count = 0

    with transaction.atomic():
        if overwrite:
            CampusCourseAllocation.objects.filter(
                program_course_id__in=[pc.id for pc in pcs], campus_id=campus_id,
            ).delete()

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
            # Load is capped across ALL campuses for that semester -- a
            # lecturer shouldn't be double-booked across two campuses just
            # because each campus counts its own allocations separately.
            alloc_count = {
                row["lecturer_id"]: row["cnt"]
                for row in CampusCourseAllocation.objects
                    .filter(lecturer__isnull=False, program_course__semester=semester)
                    .values("lecturer_id")
                    .annotate(cnt=Count("id"))
            }
            year_cov = {}
            for row in (
                CampusCourseAllocation.objects
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
                alloc = CampusCourseAllocation.objects.create(
                    course_code=pc.course_code,
                    course_name=pc.course_name,
                    department=dept,
                    origin_department=pc.program.department,
                    program=pc.program,
                    lecturer=lecturer,
                    number_of_students=students,
                    campus_id=campus_id,
                    teaching_campus_id=teaching_campus_id,
                    delivery_mode=delivery_mode,
                    program_course=pc,
                    academic_year=academic_year,
                    allocation_semester=pc.semester,
                    program_year=pc.year,
                    is_special_course=False,
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
                    "campus": campus.code,
                    "lecturer": lecturer.display_name if lecturer else "Unassigned",
                    "number_of_students": students,
                })

    return JsonResponse({
        "success": True,
        "created": created_count,
        "skipped": skip_count,
        "rows": result_rows,
        "message": f"{created_count} course(s) auto-allocated for {campus.code}"
                   + (f", {skip_count} already-allocated course(s) skipped." if skip_count else "."),
    })
