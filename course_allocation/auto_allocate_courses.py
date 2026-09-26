"""
course_allocation/views.py
Complete views for:
  1. Lecturer Course Mapping
  2. Program Enrollment (number of students)
  3. Auto-Allocation with Selection Groups and Course Splitting
  4. Cohort-based curriculum versioning
"""

import re
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.http import require_POST, require_GET
from django.db import transaction
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from .models import (
    CourseAllocation,
    LecturerCourseMapping,
    ArchivedCourseAllocation,
    ProgramEnrollment,
    AcademicYearTracker,
    SelectionGroup,
    BaseSelection,
    StudentGroup,
    GroupingTemplate,
    CombinedCourseGroup,
    CourseCombinationTemplate,
    AllocationSet,
)
from program_management.models import ProgramCourse, Program, ProgramCode
from lecturer_portal.models import Lecturer
from department_management.models import Department
from .config_helpers import (
    get_config, 
    get_lecturer_max_load, 
    make_group_code,
    calculate_split_groups,
    append_group_letter,
    normalize_course_code,
)
from .detect_user_department import detect_user_department


# ---------------------------------------------------------------------------
# Permission / Role helpers
# ---------------------------------------------------------------------------

ADMIN_GROUPS = [Role.TIMETABLE_ADMIN, Role.DIRECTOR, Role.DVC, Role.DVC_ADMIN]
COD_GROUPS   = [Role.COD, Role.COD_ADMIN]
ALL_ALLOWED  = ADMIN_GROUPS + COD_GROUPS


def check_user_permission(user, allowed_groups):
    """
    Canonical, alias-aware permission check.
    """
    if user.is_superuser:
        return True
    from core.rbac import user_has_role
    return user_has_role(user, *allowed_groups)


def get_homepage_url(user):
    if user.is_superuser:
        return "sudo_homepage"
    mapping = {
        "dvc": "dvc_panel", "dvc admins": "dvc_panel", "dvc_admins": "dvc_panel",
        "dean": "deans_panel", "dean admins": "deans_panel", "dean_admins": "deans_panel",
        "cod": "cod_panel", "cod admins": "cod_panel", "cod_admins": "cod_panel",
        "director timetable": "timetable_dashboard", "director_timetable": "timetable_dashboard",
        "timetable admins": "timetable_dashboard", "timetable_admins": "timetable_dashboard",
        "department users": "user_dashboard", "department_users": "user_dashboard",
        "cot": "cot_exam_timetable",
        "utility": "venues_panel",
        "academic affairs": "course_allocations_page", "academic_affairs": "course_allocations_page",
    }
    for g in user.groups.all():
        url = mapping.get(g.name.lower())
        if url:
            return url
    return "homepage"


def _resolve_user_dept(user):
    """Return (is_admin, user_dept, departments_qs)."""
    is_admin = user.is_superuser or check_user_permission(user, ADMIN_GROUPS)
    if is_admin:
        return True, None, Department.objects.all().order_by("name")
    dept = detect_user_department(user)
    return False, dept, Department.objects.filter(id=dept.id) if dept else Department.objects.none()


def is_postgraduate_course(course_code):
    m = re.search(r'\d{3,4}', course_code or '')
    return bool(m and int(m.group(0)[0]) >= 6)


def _normalize_course_code(course_code):
    """
    Collapse whitespace/case differences so 'BCOM 111', 'BCOM111', and
    'bcom 111' are all recognised as the same course for comparison /
    grouping purposes. Only used as a comparison key -- the original
    course_code (whatever spacing/case it was entered with) is still what
    gets stored/displayed.
    """
    return re.sub(r'\s+', '', course_code or '').upper()


# ---------------------------------------------------------------------------
# Shared course-code disambiguation
# ---------------------------------------------------------------------------


def _get_shared_programs(course_code, semester):
    """
    All distinct Programs (any department) offering this course_code this
    semester. Matches ignoring whitespace/case differences (e.g. 'BCOM 111'
    and 'BCOM111' are treated as the same course), since course_code__iexact
    alone only ignores case, not spacing.
    """
    target = _normalize_course_code(course_code)
    program_ids = {
        pc.program_id
        for pc in ProgramCourse.objects.filter(semester=semester).only(
            "program_id", "course_code"
        )
        if _normalize_course_code(pc.course_code) == target
    }
    if not program_ids:
        return []
    return list(
        Program.objects.filter(id__in=program_ids).distinct().order_by("name")
    )


def _build_course_code_labels(course_code, semester):
    """
    {program_id: disambiguated_course_code} for a course_code that is shared
    by 2+ programs this semester.

    Uses the same spreadsheet-style A, B, C ... Z, AA, AB, ... suffixing as
    the enrollment-threshold split groups (see make_group_code /
    calculate_split_groups in config_helpers.py), instead of a
    program-code/initials tag — so a shared course reads the same way
    whether it was split for enrollment reasons or because it's shared
    across programs, e.g. "COSC 101-A", "COSC 101-B", and after Z rolls
    over to "COSC 101-AA", "COSC 101-AB", exactly like Excel column
    headers. Programs are ordered by name so the same program always gets
    the same letter across re-runs.
    """
    programs = _get_shared_programs(course_code, semester)
    if len(programs) < 2:
        return {}

    return {
        prog.id: make_group_code(course_code, i)
        for i, prog in enumerate(programs)
    }


# ---------------------------------------------------------------------------
# Program-code / initials tag for shared courses
# ---------------------------------------------------------------------------

_INITIALS_SKIP_WORDS = {"of", "and", "the", "in", "for", "to", "a", "an"}


def _initials_from_name(name):
    """
    Build a short tag from a program's name for programs that have no
    ProgramCode set up, e.g. "Electrical and Biomedical Engineering" ->
    "EBE". Common filler words (of/and/the/...) are skipped so the
    initials stay short and readable; falls back to using every word's
    initial if skipping them would leave nothing.
    """
    words = re.findall(r"[A-Za-z]+", name or "")
    letters = [w[0].upper() for w in words if w.lower() not in _INITIALS_SKIP_WORDS]
    if not letters:
        letters = [w[0].upper() for w in words]
    return "".join(letters) or "P"


def _program_tag(program, tag_cache):
    """
    Short tag identifying `program` for shared-course disambiguation:
    the program's official ProgramCode if one has been set up, otherwise
    initials derived from its name (see _initials_from_name). Cached per
    program id in `tag_cache` since the same program comes up repeatedly
    while looping over that program's cohorts/courses.
    """
    if program.id in tag_cache:
        return tag_cache[program.id]
    pcode = (
        ProgramCode.objects.filter(program_id=program.id)
        .values_list("code", flat=True)
        .first()
    )
    tag = pcode.strip().upper() if pcode else _initials_from_name(program.name)
    tag_cache[program.id] = tag
    return tag


def _build_program_tags(course_code, semester, tag_cache):
    """
    {program_id: disambiguating_tag} for a course_code shared by 2+
    programs this semester (see _get_shared_programs), so a course
    offered by more than one program is tagged with the OFFERING
    program's code/initials rather than left ambiguous, e.g.
    "COSC 101-EB1" for a program with ProgramCode "EB1" and
    "COSC 101-CS" for one with no ProgramCode set up (falls back to
    initials of its name). Programs are ordered by name so the same
    program always gets the same tag across re-runs. If two programs
    would otherwise resolve to the same tag (e.g. neither has a
    ProgramCode and both share the same initials), later ones in name
    order get "2", "3", ... appended so tags stay unique for this course.
    """
    programs = _get_shared_programs(course_code, semester)
    if len(programs) < 2:
        return {}

    tags = {}
    seen_counts = {}
    for prog in programs:
        base_tag = _program_tag(prog, tag_cache)
        seen_counts[base_tag] = seen_counts.get(base_tag, 0) + 1
        count = seen_counts[base_tag]
        tags[prog.id] = base_tag if count == 1 else f"{base_tag}{count}"
    return tags


# ---------------------------------------------------------------------------
# Archive helper
# ---------------------------------------------------------------------------

def _archive_semester(target_dept, semester, user, allocation_set=None):
    """
    Upsert existing allocations for this department into
    ArchivedCourseAllocation before the table is wiped and rebuilt.

    Concurrent Allocation Sets: when `allocation_set` is given, only THAT
    set's rows are archived (matching the equally-scoped delete in
    _run_allocation) — other sets/semesters currently open for this
    department are left alone. With no allocation_set (old callers), this
    keeps its original whole-department behaviour: it archives allocations
    for ALL semesters currently sitting in CourseAllocation for this
    department, not just the semester being (re)allocated right now, since
    running the allocator for one semester used to archive-and-clear the
    whole department's live allocation table. Each row is archived under
    ITS OWN program_course semester (not the semester the user just
    selected), so the archive record stays correctly labeled either way.
    """
    qs = CourseAllocation.objects.filter(department=target_dept)
    if allocation_set is not None:
        # Matches the equally fine-grained delete scoping in _run_allocation:
        # within one set, rebuilding one semester layer must not archive
        # (and _run_allocation must not then delete) another semester
        # layer's rows that this same set already holds.
        qs = qs.filter(allocation_set=allocation_set, program_course__semester=semester)
    for alloc in qs.select_related("program", "lecturer", "department", "origin_department", "program_course"):
        alloc_semester = (
            alloc.program_course.semester if alloc.program_course else semester
        )
        ArchivedCourseAllocation.objects.update_or_create(
            department=alloc.department,
            semester=str(alloc_semester),
            course_code=alloc.course_code,
            program=alloc.program,
            defaults=dict(
                course_name=alloc.course_name,
                origin_department=alloc.origin_department,
                lecturer=alloc.lecturer,
                number_of_students=alloc.number_of_students,
                approved_by_dvc=alloc.approved_by_dvc,
                rejected_by_dvc=alloc.rejected_by_dvc,
                reason_for_disapproval=alloc.reason_for_disapproval,
                submitted_to_tt=alloc.submitted_to_tt,
                archived_by=user,
                archived_at=timezone.now(),
            ),
        )


def _course_rows_for(courses_iterable):
    """
    Collapse an iterable of ProgramCourse rows down to one row per unique
    course CODE, with a bit of aggregated detail (years/semesters/unit
    types it runs under, and how many program offerings share the code).
    Shared by the mapping page's initial render and the save endpoint so
    the table and the "just saved" row look identical.
    """
    by_code = {}
    for c in courses_iterable:
        key = normalize_course_code(c.course_code)
        if not key:
            continue
        row = by_code.get(key)
        if row is None:
            row = by_code[key] = {
                "code": c.course_code, "name": c.course_name,
                "years": set(), "semesters": set(), "types": set(),
                "program_count": 0,
            }
        row["program_count"] += 1
        row["years"].add(c.year)
        row["semesters"].add(c.semester)
        if c.unit_type:
            row["types"].add(c.get_unit_type_display())

    rows = [{
        "code": r["code"], "name": r["name"],
        "years": ", ".join(f"Y{y}" for y in sorted(r["years"])),
        "semesters": ", ".join(f"S{s}" for s in sorted(r["semesters"])),
        "types": ", ".join(sorted(r["types"])),
        "program_count": r["program_count"],
    } for r in by_code.values()]
    rows.sort(key=lambda r: r["code"])
    return rows


# ===========================================================================
# 1. LECTURER COURSE MAPPING
# ===========================================================================

@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def lecturer_course_mapping(request):
    if not check_user_permission(request.user, ALL_ALLOWED):
        raise PermissionDenied

    user = request.user
    is_admin, user_dept, departments = _resolve_user_dept(user)
    homepage_url = get_homepage_url(user)

    if not is_admin and not user_dept:
        messages.error(request, "Unable to detect your department. Contact admin.")
        return redirect(homepage_url)

    if user_dept:
        lecs = list(Lecturer.objects.filter(department=user_dept).select_related("department").order_by("name"))
        lecs += list(Lecturer.objects.exclude(department=user_dept).select_related("department").order_by("name"))
    else:
        lecs = list(Lecturer.objects.all().select_related("department").order_by("department__name", "name"))

    # ── Course picker: ONE entry per course CODE, not per program ───────────
    # ProgramCourse is a per-program row, so a course like "COSC 103" that is
    # taught in 100 different programs used to appear as 100 separate options
    # here, and mapping a lecturer meant mapping them to only whichever single
    # program row was picked. Instead we collapse every ProgramCourse across
    # EVERY department/program down to one option per normalized course code,
    # so picking "COSC 103" once represents the course itself -- save_lecturer_mapping
    # then expands that single pick back out to every program offering it.
    all_program_courses = ProgramCourse.objects.select_related(
        "program", "program__department"
    ).order_by("course_code")

    courses_by_code = {}
    for pc in all_program_courses:
        key = normalize_course_code(pc.course_code)
        if not key:
            continue
        entry = courses_by_code.get(key)
        in_user_dept = bool(user_dept and pc.program.department_id == getattr(user_dept, "id", None))
        if entry is None:
            courses_by_code[key] = {
                "id": pc.id,
                "course_code": pc.course_code,
                "course_name": pc.course_name,
                "dept_id": pc.program.department_id,
                "dept_name": pc.program.department.name if pc.program.department else "",
                "program_count": 1,
                "is_user_dept": in_user_dept,
            }
        else:
            entry["program_count"] += 1
            # Prefer a representative row from the COD's own department once
            # one turns up, purely for nicer default labelling.
            if in_user_dept and not entry["is_user_dept"]:
                entry.update({
                    "id": pc.id,
                    "course_name": pc.course_name,
                    "dept_id": pc.program.department_id,
                    "dept_name": pc.program.department.name if pc.program.department else "",
                    "is_user_dept": True,
                })

    if user_dept:
        courses = sorted(
            courses_by_code.values(),
            key=lambda e: (not e["is_user_dept"], e["course_code"]),
        )
    else:
        courses = sorted(courses_by_code.values(), key=lambda e: e["course_code"])

    qs = LecturerCourseMapping.objects.all() if is_admin else \
         LecturerCourseMapping.objects.filter(department=user_dept)
    mappings = list(qs.select_related("lecturer", "department").prefetch_related("courses").order_by(
        "department__name", "lecturer__name"
    ))

    # Each mapping's `courses` M2M can now hold every program's row for the
    # same code (e.g. 100 rows all named "COSC 103"), so collapse them back
    # down to unique codes -- with a bit of aggregated detail per code --
    # purely for display in the mappings table (each lecturer's section
    # lists one row per course code, not one row per program offering it).
    for m in mappings:
        m.course_rows = _course_rows_for(m.courses.all())
        m.unique_codes = [r["code"] for r in m.course_rows]
        m.unique_codes_count = len(m.course_rows)

    return render(request, "course_allocation/lecturer_course_mapping.html", {
        "lecturers": lecs,
        "courses": courses,
        "mappings": mappings,
        "departments": departments,
        "user_department": user_dept,
        "is_superuser": user.is_superuser,
        "is_admin": is_admin,
        "homepage_url": homepage_url,
    })


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
@require_POST
def save_lecturer_mapping(request):
    if not check_user_permission(request.user, ALL_ALLOWED):
        return HttpResponseForbidden("No permission.")

    lecturer_id  = request.POST.get("lecturer_id")
    course_ids   = request.POST.getlist("course_ids")
    department_id = request.POST.get("department_id")
    notes        = request.POST.get("notes", "")

    if not lecturer_id:
        return JsonResponse({"success": False, "error": "Lecturer is required."}, status=400)

    try:
        lecturer = Lecturer.objects.select_related("department").get(id=lecturer_id)
    except Lecturer.DoesNotExist:
        return JsonResponse({"success": False, "error": "Lecturer not found."}, status=404)

    dept = None
    if department_id:
        try:
            dept = Department.objects.get(id=department_id)
        except Department.DoesNotExist:
            pass
    if not dept:
        dept = lecturer.department

    if not check_user_permission(request.user, ADMIN_GROUPS):
        user_dept = detect_user_department(request.user)
        if user_dept and dept and dept.id != user_dept.id:
            return HttpResponseForbidden("You can only manage mappings for your own department.")

    mapping, created = LecturerCourseMapping.objects.get_or_create(
        lecturer=lecturer, department=dept, defaults={"notes": notes}
    )
    if not created:
        mapping.notes = notes
        mapping.save(update_fields=["notes", "updated_at"])

    if course_ids:
        # `course_ids` are the *representative* ProgramCourse rows the picker
        # showed (one per course code). Expand each one back out to EVERY
        # ProgramCourse row -- in any program, any department -- that shares
        # its course code, so mapping a lecturer to one "COSC 103" pick maps
        # them to all programs that run COSC 103, not just the single program
        # instance the dropdown happened to represent it with.
        selected = ProgramCourse.objects.filter(id__in=course_ids).only("id", "course_code")
        codes = {normalize_course_code(c.course_code) for c in selected if c.course_code}
        if codes:
            expanded_ids = [
                pc.id for pc in ProgramCourse.objects.all().only("id", "course_code")
                if normalize_course_code(pc.course_code) in codes
            ]
            mapping.courses.set(expanded_ids)
        else:
            mapping.courses.clear()
    else:
        mapping.courses.clear()

    # Collapse the (possibly very large) set of mapped ProgramCourse rows
    # back down to one detailed row per unique course code for the UI --
    # matches the shape the initial page render uses (see _course_rows_for)
    # so the "just saved" card looks identical to a freshly loaded one.
    mapped_courses = list(mapping.courses.all())
    course_rows = _course_rows_for(mapped_courses)

    return JsonResponse({
        "success": True,
        "mapping_id": mapping.id,
        "lecturer": lecturer.display_name,
        "lecturer_id": lecturer.id,
        "department": dept.name if dept else "",
        "department_id": dept.id if dept else None,
        "courses_count": len(course_rows),                # distinct course codes
        "total_program_offerings": len(mapped_courses),    # total program rows covered
        "courses": course_rows,
        "notes": mapping.notes,
        "created": created,
    })


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
@require_POST
def delete_lecturer_mapping(request):
    if not check_user_permission(request.user, ALL_ALLOWED):
        return HttpResponseForbidden("No permission.")

    try:
        mapping = LecturerCourseMapping.objects.get(id=request.POST.get("mapping_id"))
    except LecturerCourseMapping.DoesNotExist:
        return JsonResponse({"success": False, "error": "Mapping not found."}, status=404)

    if not check_user_permission(request.user, ADMIN_GROUPS):
        user_dept = detect_user_department(request.user)
        if not user_dept or mapping.department_id != user_dept.id:
            return HttpResponseForbidden("No permission.")

    mapping.delete()
    return JsonResponse({"success": True})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
@require_POST
def remove_mapping_course(request):
    """
    Inline per-course-row removal for a lecturer's mapping: drops just one
    course CODE (every ProgramCourse row nationwide that shares it) from
    the mapping, without touching the lecturer's other courses. Lets the
    right-hand table's expanded course rows carry their own quick 'remove'
    action instead of forcing a full re-save through the form.
    """
    if not check_user_permission(request.user, ALL_ALLOWED):
        return HttpResponseForbidden("No permission.")

    mapping_id = request.POST.get("mapping_id")
    course_code = request.POST.get("course_code", "")

    try:
        mapping = LecturerCourseMapping.objects.select_related("department").get(id=mapping_id)
    except LecturerCourseMapping.DoesNotExist:
        return JsonResponse({"success": False, "error": "Mapping not found."}, status=404)

    if not check_user_permission(request.user, ADMIN_GROUPS):
        user_dept = detect_user_department(request.user)
        if not user_dept or mapping.department_id != user_dept.id:
            return HttpResponseForbidden("No permission.")

    target = normalize_course_code(course_code)
    if not target:
        return JsonResponse({"success": False, "error": "Course code is required."}, status=400)

    to_remove = [
        c.id for c in mapping.courses.all()
        if normalize_course_code(c.course_code) == target
    ]
    if to_remove:
        mapping.courses.remove(*to_remove)

    remaining = mapping.courses.count()
    return JsonResponse({
        "success": True,
        "mapping_id": mapping.id,
        "removed_code": course_code,
        "remaining_program_offerings": remaining,
    })


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
@require_GET
def get_lecturer_mapping(request):
    lecturer_id   = request.GET.get("lecturer_id")
    department_id = request.GET.get("department_id")

    try:
        lecturer = Lecturer.objects.get(id=lecturer_id)
    except (Lecturer.DoesNotExist, TypeError, ValueError):
        return JsonResponse({"success": False, "error": "Lecturer not found."}, status=404)

    dept = None
    if department_id:
        try:
            dept = Department.objects.get(id=department_id)
        except Department.DoesNotExist:
            pass
    if not dept:
        dept = lecturer.department

    try:
        mapping = LecturerCourseMapping.objects.prefetch_related("courses").get(
            lecturer=lecturer, department=dept
        )
        # The picker's <option> values are representative ProgramCourse ids
        # (one per code) that won't generally match the (possibly hundreds
        # of) actual ProgramCourse ids stored on the mapping, so also return
        # normalized course codes -- the form matches picker options against
        # these to decide what should show as selected.
        codes = sorted({
            normalize_course_code(c.course_code)
            for c in mapping.courses.all() if c.course_code
        })
        return JsonResponse({
            "success": True,
            "mapping_id": mapping.id,
            "course_ids": list(mapping.courses.values_list("id", flat=True)),
            "course_codes": codes,
            "notes": mapping.notes,
        })
    except LecturerCourseMapping.DoesNotExist:
        return JsonResponse({"success": True, "mapping_id": None, "course_ids": [], "course_codes": [], "notes": ""})


# ===========================================================================
# 2. GET ALLOCATION CONFIGURATION
# ===========================================================================

@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
@require_GET
def get_allocation_config(request):
    """
    Get the current allocation configuration for a department.
    """
    if not check_user_permission(request.user, ALL_ALLOWED):
        return HttpResponseForbidden("No permission.")
    
    dept_id = request.GET.get("department_id")
    
    if dept_id:
        try:
            dept = Department.objects.get(id=dept_id)
        except Department.DoesNotExist:
            return JsonResponse({"error": "Department not found."}, status=404)
        
        if not check_user_permission(request.user, ADMIN_GROUPS):
            user_dept = detect_user_department(request.user)
            if not user_dept or user_dept.id != dept.id:
                return HttpResponseForbidden("You can only view your own department's config.")
    else:
        dept = None
    
    cfg = get_config(dept)
    
    return JsonResponse({
        "success": True,
        "department": dept.name if dept else "Global",
        "department_id": dept.id if dept else None,
        "config": {
            "max_load_per_semester": cfg["max_load_per_semester"],
            "max_load_enabled": cfg["max_load_enabled"],
            "split_threshold": cfg["split_threshold"],
            "split_extend_by": cfg["split_extend_by"],
            "splitting_enabled": cfg["splitting_enabled"],
            "pg_designations": cfg["pg_designations"],
            "allowed_semesters": cfg["allowed_semesters"],
        }
    })


# ===========================================================================
# 3. AUTO-ALLOCATION WITH COHORT-BASED CURRICULUM VERSIONING
# ===========================================================================

@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def auto_allocate_all_page(request):
    if not check_user_permission(request.user, ALL_ALLOWED):
        raise PermissionDenied

    user = request.user
    is_admin, user_dept, departments = _resolve_user_dept(user)
    homepage_url = get_homepage_url(user)

    if not is_admin and not user_dept:
        messages.error(request, "Unable to detect your department.")
        return redirect(homepage_url)

    # Concurrent Allocation Sets: this page used to silently run against
    # whatever set happened to be active in session (or the department's
    # legacy set) without ever asking, so a COD could auto-allocate courses
    # straight into the wrong allocation set with no indication it had
    # happened. A single-department COD is sent to the same "which
    # allocation are you working on? / create new" picker cod_panel uses,
    # the first time each session, exactly like cod_panel does. Bulk
    # "allocate every department at once" (admin-only) has no single set to
    # pick here, so it's left to fall back per-department as before.
    from course_allocation.allocation_scope import get_active_allocation_set, get_or_default_legacy_set
    active_allocation_set = None
    allocation_sets_for_switcher = []
    if user_dept:
        active_allocation_set = get_active_allocation_set(request, user_dept)
        if (request.method == "GET"
                and not request.GET.get("skip_allocation_picker")
                and active_allocation_set is None):
            from django.urls import reverse
            return redirect(reverse("allocation_picker") + f"?next={request.path}")
        if active_allocation_set is None:
            active_allocation_set = get_or_default_legacy_set(user_dept)
        allocation_sets_for_switcher = AllocationSet.objects.filter(
            department=user_dept, is_archived=False
        ).order_by("-created_at")

    return render(request, "course_allocation/auto_allocate_all.html", {
        "departments": departments,
        "is_superuser": user.is_superuser,
        "is_admin": is_admin,
        "is_cod": check_user_permission(user, COD_GROUPS),
        "user_department": user_dept,
        "homepage_url": homepage_url,
        "active_allocation_set": active_allocation_set,
        "allocation_sets_for_switcher": allocation_sets_for_switcher,
    })


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
@require_POST
def auto_allocate_all_ajax(request):
    if not check_user_permission(request.user, ALL_ALLOWED):
        return HttpResponseForbidden("No permission.")

    user = request.user
    semester = request.POST.get("semester", 1)
    dept_id = request.POST.get("department")
    allocate_all = request.POST.get("allocate_all") == "true"
    only_enrolled = request.POST.get("only_enrolled", "true") != "false"
    
    # Parse config overrides from request
    config_overrides = {}
    config_fields = [
        "max_load_per_semester", "max_load_enabled", 
        "split_threshold", "split_extend_by", "splitting_enabled"
    ]
    for field in config_fields:
        value = request.POST.get(f"config_{field}")
        if value is not None:
            if field in ["max_load_enabled", "splitting_enabled"]:
                config_overrides[field] = value.lower() in ("true", "1", "on")
            else:
                try:
                    config_overrides[field] = int(value)
                except ValueError:
                    pass

    try:
        semester = int(semester)
        allowed = get_config(None)["allowed_semesters"]
        if semester not in allowed:
            raise ValueError
    except ValueError:
        return HttpResponseBadRequest(
            f"Semester must be one of {get_config(None)['allowed_semesters']}."
        )

    if allocate_all and check_user_permission(user, ADMIN_GROUPS):
        targets = list(Department.objects.all())
    else:
        if not dept_id:
            return HttpResponseBadRequest("Department is required.")
        try:
            targets = [Department.objects.get(id=dept_id)]
        except Department.DoesNotExist:
            return HttpResponseBadRequest("Department not found.")

        if not check_user_permission(user, ADMIN_GROUPS):
            user_dept = detect_user_department(user)
            if not user_dept or user_dept.id != targets[0].id:
                return HttpResponseForbidden("You can only allocate for your own department.")

    all_rows = []
    total_del = 0
    total_new = 0
    total_skipped = 0

    from course_allocation.allocation_scope import get_active_allocation_set, get_or_default_legacy_set

    for dept in targets:
        # Concurrent Allocation Sets: resolve which set this run belongs to
        # so the archive/delete/rebuild inside _run_allocation is scoped to
        # just this set, not the whole department (see _run_allocation's
        # docstring for why that matters). Falls back to the department's
        # legacy set so a department that hasn't touched the new picker
        # flow yet still gets a real set rather than allocation_set=None,
        # which would silently re-enable the old whole-department wipe.
        dept_allocation_set = get_active_allocation_set(request, dept) or get_or_default_legacy_set(dept)
        result = _run_allocation(
            dept, semester, user, 
            only_enrolled=only_enrolled,
            config_overrides=config_overrides,
            allocation_set=dept_allocation_set,
        )
        total_del += result["deleted"]
        total_new += result["created"]
        total_skipped += result.get("skipped_no_enrollment", 0)
        all_rows.extend(result["rows"])

    return JsonResponse({
        "success": True,
        "semester": semester,
        "deleted_count": total_del,
        "created_count": total_new,
        "skipped_no_enrollment": total_skipped,
        "only_enrolled": only_enrolled,
        "allocations": all_rows,
        "config_used": config_overrides,
    })


# Cohort/curriculum-versioning helpers
from .cohort_utils import (
    get_cohort_curriculum_map as _get_cohort_curriculum_map,
    get_courses_for_cohort as _get_courses_for_cohort,
)


def _run_allocation(target_dept, semester, user, only_enrolled=True, config_overrides=None, allocation_set=None):
    """
    Allocate courses for one department+semester with cohort-based curriculum versioning.

    Concurrent Allocation Sets — CRITICAL FIX: this function used to
    archive-then-DELETE every CourseAllocation row for the WHOLE
    department (every semester's live data) before rebuilding just the
    requested semester. That is exactly the kind of data loss this
    upgrade exists to prevent: with several AllocationSets now open at
    once for a department, running the allocator for one of them must
    never touch another set's rows. When `allocation_set` is given, the
    archive/delete/rebuild below is scoped to ONLY that set's rows —
    every other AllocationSet (including other semesters currently open
    for this department) is left completely untouched. If no
    allocation_set is passed (an old caller that hasn't been updated),
    the previous whole-department behaviour is preserved exactly, so
    nothing already depending on it breaks.
    """
    with transaction.atomic():
        # ── 1. Archive existing allocations ──────────────────────────────────
        # Scoped to this AllocationSet only when one is given (see docstring).
        _archive_semester(target_dept, semester, user, allocation_set=allocation_set)

        # ── 2. Delete existing allocations ──────────────────────────────────
        # Scoped to this AllocationSet only when one is given — a Semester 1
        # allocator run for one set never removes another set's (or another
        # semester's) live rows.
        old_qs = CourseAllocation.objects.filter(department=target_dept)
        if allocation_set is not None:
            # Fine-grained scoping now available: within one AllocationSet
            # that composes multiple ALL-scope semesters (e.g. Sem 1 ALL +
            # Sem 2 ALL both auto-allocated into the same set), rebuilding
            # Sem 2 must not wipe the Sem 1 rows this same set already has
            # — so also match on the target semester, not just the set.
            old_qs = old_qs.filter(allocation_set=allocation_set, program_course__semester=semester)
        deleted = old_qs.count()
        old_qs.delete()

        # ── 3. Get current reference year ────────────────────────────────────
        reference_year = AcademicYearTracker.get_current().current_year

        # ── 4. Get all programs in this department ──────────────────────────
        programs = list(Program.objects.filter(department=target_dept))

        if not programs:
            return {"deleted": deleted, "created": 0, "rows": [], "skipped_no_enrollment": 0}

        # ── 5. Build enrollment lookup ──────────────────────────────────────
        enroll_by_program = {}
        for e in ProgramEnrollment.objects.filter(program__department=target_dept):
            enroll_by_program.setdefault(e.program_id, []).append(e)

        # ── 6. Bulk-load BaseSelections ─────────────────────────────────────
        base_selection_ids = set(
            BaseSelection.objects.filter(department=target_dept)
            .values_list("program_course_id", flat=True)
        )

        # ── 7. Bulk-load lecturer allocation counts ─────────────────────────
        from django.db.models import Count
        alloc_count = {
            row["lecturer_id"]: row["cnt"]
            for row in CourseAllocation.objects
            .filter(lecturer__isnull=False, program_course__semester=semester)
            .values("lecturer_id")
            .annotate(cnt=Count("id"))
        }

        # ── 8. Bulk-load year coverage ──────────────────────────────────────
        year_cov = {}
        for row in (
            CourseAllocation.objects
            .filter(lecturer__isnull=False, program_course__isnull=False)
            .values("lecturer_id", "program_id", "program_course__year")
        ):
            year_cov.setdefault(row["lecturer_id"], set()).add(
                (row["program_id"], row["program_course__year"])
            )

        # ── 9. Bulk-load lecturer mappings from ALL departments ─────────────
        # This loads mappings from ALL departments, not just the target department,
        # allowing lecturers from any department to be assigned to courses they're mapped to.
        mapping_idx = {}
        for m in LecturerCourseMapping.objects.select_related("lecturer").prefetch_related("courses"):
            for c in m.courses.all():
                mapping_idx.setdefault(c.id, []).append(m.lecturer)

        # ── 10. Bulk-load all lecturers ─────────────────────────────────────
        dept_lecs = list(Lecturer.objects.filter(department=target_dept).select_related("department"))
        other_lecs = list(Lecturer.objects.exclude(department=target_dept).select_related("department"))
        all_lecs = dept_lecs + other_lecs

        # ── 11. Config with overrides ──────────────────────────────────────
        cfg = get_config(target_dept)
        
        if config_overrides:
            for key, value in config_overrides.items():
                if key in cfg:
                    cfg[key] = value
        
        SPLIT_THRESHOLD = cfg["split_threshold"] if cfg["splitting_enabled"] else float("inf")
        SPLIT_EXTEND_BY = cfg.get("split_extend_by", 0)

        # ── 12. Course code resolution ──────────────────────────────────────
        # A course_code offered by 2+ programs this semester (e.g. "COSC
        # 101" taught to both Computer Science and Electrical & Biomedical
        # Engineering) is tagged with the OFFERING program's ProgramCode,
        # or, when that program has no ProgramCode set up, initials built
        # from its name -- e.g. "COSC 101-EB1" / "COSC 101-CS" -- instead of
        # being left identical across programs. `_program_tag_cache` /
        # `_shared_tag_cache` memoise this per program / per course_code
        # since the same program-course pairs recur across cohorts below.
        _program_tag_cache = {}
        _shared_tag_cache = {}

        def _resolve_course_code(pc, program):
            key = _normalize_course_code(pc.course_code)
            if key not in _shared_tag_cache:
                _shared_tag_cache[key] = _build_program_tags(
                    pc.course_code, semester, _program_tag_cache
                )
            tag = _shared_tag_cache[key].get(program.id)
            return f"{pc.course_code}-{tag}" if tag else pc.course_code

        # ── 13. Process each program and its cohorts ──────────────────────
        created_allocs = []
        skipped_no_enrollment = 0

        for program in programs:
            enrollments = enroll_by_program.get(program.id, [])
            if not enrollments:
                continue

            cohort_map = _get_cohort_curriculum_map(program, reference_year)

            # ── 13a. Gather every (ProgramCourse, students) pair across ALL
            # cohorts/enrollments for this program FIRST, then aggregate by
            # course_code below. Different cohorts can each resolve to their
            # own ProgramCourse row (different pc.id) for what is really the
            # same course (same course_code, same year, same semester) -- if
            # those were split/created independently, one cohort's count
            # could push it over SPLIT_THRESHOLD (-> "-A"/"-B") while the
            # other cohort's smaller count stays unsplit, producing a
            # duplicate plain "COURSE(TAG)" allocation alongside the split
            # ones. Aggregating first means the split decision is made once,
            # on the true combined enrollment for that course.
            raw_items = []  # list of {'program_course': pc, 'students': n}
            seen_pc_ids = set()

            for enrollment in enrollments:
                entry_year = enrollment.entry_year
                students = enrollment.number_of_students

                if students <= 0 and only_enrolled:
                    skipped_no_enrollment += 1
                    continue

                current_year_of_study = reference_year - entry_year + 1

                if current_year_of_study < 1 or current_year_of_study > 6:
                    continue

                cohort_value = cohort_map.get(entry_year, '0')
                courses = _get_courses_for_cohort(
                    program, cohort_value, current_year_of_study, semester
                )

                for pc in courses:
                    if pc.id in seen_pc_ids:
                        continue
                    seen_pc_ids.add(pc.id)
                    raw_items.append({
                        'program_course': pc,
                        'students': students,
                    })

            # ── 13b. Aggregate by (course_code, year) so multiple
            # ProgramCourse rows for the same course (different cohorts)
            # are merged into a single allocation/split decision.
            aggregated = {}
            order = []
            for item in raw_items:
                pc = item['program_course']
                key = (_normalize_course_code(pc.course_code), pc.year)
                if key not in aggregated:
                    aggregated[key] = {'program_course': pc, 'students': 0}
                    order.append(key)
                aggregated[key]['students'] += item['students']

            course_list = [aggregated[key] for key in order]

            for item in course_list:
                pc = item['program_course']
                student_count = item['students']

                if student_count <= 0 and only_enrolled:
                    skipped_no_enrollment += 1
                    continue

                is_elective = (pc.id in base_selection_ids) or pc.is_elective_type

                # ── Intelligent splitting using calculate_split_groups ──
                group_sizes = calculate_split_groups(
                    student_count,
                    SPLIT_THRESHOLD,
                    SPLIT_EXTEND_BY
                )

                if group_sizes:
                    # Split path - create multiple groups. display_code
                    # already carries the program tag when this course is
                    # shared (e.g. "COSC 101-EB1"), so the split letter is
                    # appended AFTER it via append_group_letter (not
                    # make_group_code, which would risk mistaking a short
                    # tag for a stray split-letter suffix and stripping
                    # it) -- giving "COSC 101-EB1-A", "COSC 101-EB1-B", etc.
                    display_code = _resolve_course_code(pc, program)
                    for i, group_students in enumerate(group_sizes):
                        group_code = append_group_letter(display_code, i)
                        lecturer = _pick_lecturer(
                            pc, mapping_idx, alloc_count, year_cov,
                            target_dept, dept_lecs, other_lecs, all_lecs, cfg,
                        )
                        alloc = CourseAllocation.objects.create(
                            course_code=group_code,
                            course_name=pc.course_name,
                            department=target_dept,
                            origin_department=pc.program.department,
                            program=program,
                            lecturer=lecturer,
                            number_of_students=group_students,
                            approved_by_dvc=False,
                            rejected_by_dvc=False,
                            reason_for_disapproval="No reason yet",
                            submitted_to_tt=False,
                            is_elective=is_elective,
                            program_course=pc,
                            allocation_set=allocation_set,
                        )
                        created_allocs.append(alloc)
                        if lecturer:
                            alloc_count[lecturer.id] = alloc_count.get(lecturer.id, 0) + 1
                            year_cov.setdefault(lecturer.id, set()).add((pc.program_id, pc.year))
                else:
                    # Regular path - single group
                    lecturer = _pick_lecturer(
                        pc, mapping_idx, alloc_count, year_cov,
                        target_dept, dept_lecs, other_lecs, all_lecs, cfg,
                    )
                    alloc = CourseAllocation.objects.create(
                        course_code=_resolve_course_code(pc, program),
                        course_name=pc.course_name,
                        department=target_dept,
                        origin_department=pc.program.department,
                        program=program,
                        lecturer=lecturer,
                        number_of_students=student_count,
                        approved_by_dvc=False,
                        rejected_by_dvc=False,
                        reason_for_disapproval="No reason yet",
                        submitted_to_tt=False,
                        is_elective=is_elective,
                        program_course=pc,
                        allocation_set=allocation_set,
                    )
                    created_allocs.append(alloc)
                    if lecturer:
                        alloc_count[lecturer.id] = alloc_count.get(lecturer.id, 0) + 1
                        year_cov.setdefault(lecturer.id, set()).add((pc.program_id, pc.year))

        # ── 14. Auto-create selection groups ──────────────────────────────────
        _create_selection_groups_for_courses(target_dept, semester, created_allocs, user)

        # ── 15. Replay the grouping knowledge base ─────────────────────────────
        # Recreate any student-group splits a COD has previously built for a
        # program/year/semester/intake in this department, since the
        # CourseAllocation rows they were tagged to just got wiped and
        # rebuilt above.
        groups_applied = _apply_grouping_templates(target_dept, semester, user)

        # ── 16. Replay the course-combination knowledge base ───────────────────
        # Recreate any CombinedCourseGroup a COD has previously built for a
        # base course code in this department, for the same reason.
        combinations_applied = _apply_course_combination_templates(target_dept, semester, user)

    # ── Serialise rows ──────────────────────────────────────────────────────────
    rows = [
        {
            "id": a.id,
            "program": a.program.name if a.program else "",
            "course_code": a.course_code,
            "course_name": a.course_name,
            "year": a.program_course.year if a.program_course else "",
            "semester": semester,
            "department": a.department.name,
            "lecturer": a.lecturer.display_name if a.lecturer else "Unassigned",
            "number_of_students": a.number_of_students,
            "status": a.status_label(),
            "is_elective": a.is_elective,
            "selection_group": a.selection_group.name if a.selection_group else None,
        }
        for a in created_allocs
    ]

    return {
        "deleted": deleted,
        "created": len(created_allocs),
        "rows": rows,
        "skipped_no_enrollment": skipped_no_enrollment,
        "splitting_info": {
            "threshold": SPLIT_THRESHOLD,
            "extend_by": SPLIT_EXTEND_BY,
        },
        "groups_applied": groups_applied,
        "combinations_applied": combinations_applied,
    }


def _apply_grouping_templates(target_dept, semester, user):
    """
    Replay every GroupingTemplate that applies to this department/semester:
    recreate the remembered StudentGroup(s) and re-tag the freshly rebuilt
    CourseAllocation rows into them, exactly as if a COD had rebuilt the
    split by hand through the Student Group UI. Returns how many
    group/course assignments were (re)applied, for the response payload.

    Imports the group-assignment helpers from course_management.cod_panel
    locally (rather than at module load time) to keep this a plain,
    late-bound cross-app call.
    """
    from course_management.cod_panel import _groupable_courses_qs, _assign_course_to_group, strip_group_suffix

    templates = (
        GroupingTemplate.objects
        .filter(program__department=target_dept, semester=semester)
        .select_related("program")
        .prefetch_related("groups", "course_codes")
    )

    applied = 0
    for tmpl in templates:
        by_course = _groupable_courses_qs(tmpl.program, tmpl.year, semester, tmpl.intake)
        if not by_course:
            continue

        if tmpl.scope == GroupingTemplate.SCOPE_SELECTED:
            selected_codes = {c.base_course_code for c in tmpl.course_codes.all()}
            if not selected_codes:
                continue
            target_pc_ids = [
                pc_id for pc_id, alloc in by_course.items()
                if strip_group_suffix(alloc.course_code)[0] in selected_codes
            ]
        else:
            target_pc_ids = list(by_course.keys())

        if not target_pc_ids:
            continue

        for grp_def in tmpl.groups.all():
            group, _ = StudentGroup.objects.get_or_create(
                program=tmpl.program, year=tmpl.year, semester=semester,
                intake=tmpl.intake, letter=grp_def.letter,
                defaults={"name": grp_def.name or f"Group {grp_def.letter}", "created_by": user},
            )
            for pc_id in target_pc_ids:
                _assign_course_to_group(tmpl.program, pc_id, tmpl.intake, group)
                applied += 1

    return applied


def _apply_course_combination_templates(target_dept, semester, user):
    """
    Replay every CourseCombinationTemplate for this department: for each
    remembered base course code, gather the freshly rebuilt
    CourseAllocation rows (this semester, in the remembered programs) that
    share it and re-combine them into a CombinedCourseGroup, exactly as if
    a COD had re-run the combine by hand. Returns how many combinations
    were (re)applied.
    """
    templates = (
        CourseCombinationTemplate.objects
        .filter(department=target_dept)
        .prefetch_related("programs")
    )

    applied = 0
    for tmpl in templates:
        program_ids = list(tmpl.programs.values_list("program_id", flat=True))
        if not program_ids:
            continue

        norm_base = _normalize_course_code(tmpl.base_course_code)
        # The cross-program labels currently in effect for this base code
        # this semester (e.g. "EDFO 111-A" for one program, "EDFO 111-B"
        # for another) -- see _build_course_code_labels. A course that's
        # combined across programs may be stored under one of these labels
        # rather than the plain base code.
        cross_program_labels = {
            _normalize_course_code(v)
            for v in _build_course_code_labels(tmpl.base_course_code, semester).values()
        }
        acceptable_codes = {norm_base} | cross_program_labels

        candidates = CourseAllocation.objects.filter(
            department=target_dept, program_id__in=program_ids,
            program_course__semester=semester,
            student_group__isnull=True,  # exclude cohort-split sections (Group A/B, etc.)
            # those are deliberately kept separate, not meant to be
            # lecture-combined by this mechanism.
        )
        matches = [
            a for a in candidates
            if _normalize_course_code(a.course_code) in acceptable_codes
        ]
        if len(matches) < 2:
            continue

        group_code = f"{tmpl.base_course_code.replace(' ', '_')}_COMBINED"
        group, _ = CombinedCourseGroup.objects.get_or_create(
            department=target_dept, group_code=group_code,
            defaults={
                "base_course_code": tmpl.base_course_code,
                "lecturer": tmpl.lecturer,
                "created_by": user,
            },
        )
        group.lecturer = tmpl.lecturer
        group.save(update_fields=["lecturer"])
        group.allocations.set(matches)
        primary = next((a for a in matches if a.department_id == target_dept.id), matches[0])
        group.primary_allocation = primary
        group.save(update_fields=["primary_allocation"])
        applied += 1

    return applied


def _create_selection_groups_for_courses(target_dept, semester, created_allocs, user):
    """Create selection groups for elective courses that are not already in groups."""
    # Group electives by (program, year, allocation_set) — allocation_set is
    # included in the key because two concurrent sets can each have their own
    # elective pool for the same program/year, and they must never be merged
    # into (or matched against) each other's SelectionGroup.
    elective_by_program_year_set = {}
    for alloc in created_allocs:
        if alloc.is_elective and not alloc.selection_group and alloc.program_course:
            key = (alloc.program_id, alloc.program_course.year, alloc.allocation_set_id)
            elective_by_program_year_set.setdefault(key, []).append(alloc)

    if not elective_by_program_year_set:
        return

    program_ids = {pid for pid, _yr, _set in elective_by_program_year_set}
    programs = {p.id: p for p in Program.objects.filter(id__in=program_ids)}

    group_names = [
        f"{programs[pid].name} Y{yr} S{semester} Electives"
        for pid, yr, _set in elective_by_program_year_set
        if pid in programs
    ]
    # Keyed by (name, allocation_set_id) so a name match in a different
    # concurrent set is never mistaken for "already exists" here.
    existing_groups = {
        (g.name, g.allocation_set_id): g
        for g in SelectionGroup.objects.filter(
            department=target_dept, name__in=group_names
        )
    }

    for (program_id, year, allocation_set_id), electives in elective_by_program_year_set.items():
        if len(electives) < 2 or program_id not in programs:
            continue
        program = programs[program_id]
        group_name = f"{program.name} Y{year} S{semester} Electives"

        existing_key = (group_name, allocation_set_id)
        if existing_key in existing_groups:
            sg = existing_groups[existing_key]
            sg.courses.add(*electives)
        else:
            sg = SelectionGroup.objects.create(
                name=group_name,
                department=target_dept,
                program=program,
                created_by=user,
                allocation_set_id=allocation_set_id,
            )
            sg.courses.set(electives)

        for alloc in electives:
            alloc.selection_group = sg
        CourseAllocation.objects.filter(
            id__in=[a.id for a in electives]
        ).update(selection_group=sg)


def _pick_lecturer(pc, mapping_idx, alloc_count, year_cov,
                   target_dept, dept_lecs, other_lecs, all_lecs, cfg):
    """
    Pick the best available lecturer from the explicit mappings for a ProgramCourse.
    Checks mappings from ALL departments (not just the target department).
    If no mapping exists for the course, leave it unassigned (return None).
    
    Mapped lecturers (LecturerCourseMapping) are an explicit qualification
    made by the CoD/admin. Only mapped lecturers are considered for assignment.
    If a course is mapped to multiple lecturers, the least-loaded one is chosen,
    preferring lecturers from the target department.
    If no mapping exists, the course remains unassigned.
    """
    mapped = mapping_idx.get(pc.id, [])
    
    # No mapping exists for this course - leave unassigned
    if not mapped:
        return None
    
    # Filter mapped lecturers who are under their max load
    def under_cap(lec):
        cap = get_lecturer_max_load(lec, cfg)
        return cap is None or alloc_count.get(lec.id, 0) < cap
    
    def score(lec):
        count = alloc_count.get(lec.id, 0)
        covered = (pc.program_id, pc.year) in year_cov.get(lec.id, set())
        return count * 10 + (5 if covered else 0)
    
    # Get mapped lecturers who are under capacity
    available_mapped = [l for l in mapped if under_cap(l)]
    
    # If all mapped lecturers are over capacity, still assign to the least loaded one
    # (better to overload a mapped lecturer than leave unassigned)
    if not available_mapped:
        return min(mapped, key=score)
    
    # Prefer lecturers from the target department, then by load
    return sorted(
        available_mapped,
        key=lambda l: (0 if l.department_id == target_dept.id else 1, score(l))
    )[0]


# ---------------------------------------------------------------------------
# Shared AJAX: department allocations table
# ---------------------------------------------------------------------------

@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
@require_GET
def get_department_allocations(request):
    if not check_user_permission(request.user, ALL_ALLOWED):
        return HttpResponseForbidden()

    dept_id = request.GET.get("department_id")
    semester = request.GET.get("semester")

    if not dept_id:
        return JsonResponse({"error": "department_id required."}, status=400)

    if not check_user_permission(request.user, ADMIN_GROUPS):
        user_dept = detect_user_department(request.user)
        if not user_dept or str(user_dept.id) != str(dept_id):
            return HttpResponseForbidden("You can only view your department's allocations.")

    try:
        dept = Department.objects.get(id=dept_id)
    except Department.DoesNotExist:
        return JsonResponse({"error": "Department not found."}, status=404)

    # Concurrent Allocation Sets: this table/stats endpoint used to ignore
    # allocation_set entirely and show every CourseAllocation row ever
    # created for the department — so a brand-new, empty AllocationSet
    # would still display the old set's full course list and stats. Scope
    # to whichever set the page has active: an explicit allocation_set_id
    # (passed by the page's switcher) takes priority; otherwise fall back
    # to the session's active set for this department, then its legacy set.
    from course_allocation.allocation_scope import (
        resolve_allocation_set_for_request, scope_qs,
    )
    allocation_set_id = request.GET.get("allocation_set_id")
    if allocation_set_id:
        allocation_set = AllocationSet.objects.filter(id=allocation_set_id, department=dept).first()
    else:
        allocation_set = resolve_allocation_set_for_request(request, dept)

    qs = CourseAllocation.objects.filter(department=dept).select_related(
        "lecturer", "program", "department", "selection_group"
    )
    qs = scope_qs(qs, allocation_set)

    if semester:
        try:
            pc_ids = ProgramCourse.objects.filter(
                program__department=dept, semester=int(semester)
            ).values_list("id", flat=True)
            qs = qs.filter(program_course_id__in=pc_ids)
        except ValueError:
            pass

    qs = qs.order_by("program__name", "course_code").select_related("program_course")

    data = []
    for a in qs:
        pc = a.program_course
        data.append({
            "id": a.id,
            "program": a.program.name if a.program else "",
            "program_id": a.program_id,
            "course_code": a.course_code,
            "course_name": a.course_name,
            "year": pc.year if pc else "",
            "semester": pc.semester if pc else "",
            "department": a.department.name,
            "lecturer": a.lecturer.display_name if a.lecturer else "Unassigned",
            "lecturer_id": a.lecturer_id,
            "number_of_students": a.number_of_students,
            "status": a.status_label(),
            "is_elective": a.is_elective,
            "selection_group": a.selection_group.name if a.selection_group else None,
        })

    return JsonResponse({
        "department": dept.name,
        "allocations": data,
        "total": len(data),
        "allocation_set": allocation_set.name if allocation_set else None,
        "allocation_set_id": allocation_set.id if allocation_set else None,
    })