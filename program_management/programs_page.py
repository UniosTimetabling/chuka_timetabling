# -----------------------
# Programs + Courses + Program Codes Page / AJAX
# -----------------------
#
# PERFORMANCE FIX: the old version of this view (for non-department users,
# i.e. DVC/TT/admin) loaded *every* department's Programs with *every*
# Program's `courses` prefetched, plus every ProgramCode, in one request —
# hundreds to thousands of rows rendered into one template. That's what made
# the page unresponsive as data grew.
#
# Fix: the page view now only ever sends the (small) department list plus an
# empty shell. Programs/ProgramCourses/ProgramCodes for a given department
# are fetched on demand via `get_programs_for_department_page`, paginated
# with Django's Paginator, and the frontend loads one page/department at a
# time instead of everything at once. A COD/COD-admin still only ever sees
# their own department, unchanged from before.
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from typing import Optional
from django.contrib.auth.models import User

from department_management.models import Department
from program_management.models import Program, ProgramCode
from lecturer_portal.models import Lecturer

DEFAULT_PAGE_SIZE = 25


def detect_user_department(user: User) -> Optional[Department]:
    """
    Try several heuristics to find the department associated with the logged-in user:
     - OrgRole.department: direct, authoritative link (works for COD Admin
       accounts too, unlike the leader/Lecturer checks below).
     - Department.leader == user
     - Lecturer with email matching user.email -> find department via Lecturer model? (if Lecturer had FK, not in your model)
     - OrgRole with a title containing 'COD' mapping to user (legacy fallback).
    If none found, return None and frontend will show dept select.
    """
    org = getattr(user, "org_role", None)
    if org and org.department_id:
        return org.department

    try:
        dept = Department.objects.filter(leader=user).first()
        if dept:
            return dept
    except Exception:
        pass

    try:
        lect = Lecturer.objects.filter(user=user).first()
        if lect and lect.department:
            return lect.department
    except Exception:
        pass

    try:
        lect = Lecturer.objects.filter(email__iexact=(user.email or "")).first()
        if lect and lect.department:
            return lect.department
    except Exception:
        pass

    try:
        org = getattr(user, "org_role", None)
        if org and "COD" in org.title.upper():
            parts = org.title.split("-", 1)
            if len(parts) > 1:
                dept_name = parts[1].strip()
                return Department.objects.filter(name__icontains=dept_name).first()
    except Exception:
        pass

    return None


@login_required
def programs_page(request):
    """
    Programs / ProgramCourses / ProgramCodes management page.

    Root-cause fix, kept template-compatible (no rewrite of the existing
    1295-line template's render loops needed):
      - A COD/COD-admin still only ever sees their own department (as
        before) — that path was never the slow one.
      - A non-scoped user (DVC/TT/admin) previously got EVERY department's
        Programs with EVERY Program's `courses` prefetched, plus every
        ProgramCode, in one request. Now they must pick one department via
        `?department_id=` (the dropdown submits this), and only that one
        department's data is fetched at all.
      - Within a department, Programs (and ProgramCodes) are paginated with
        Django's Paginator via `?page=`. The Page object returned still
        supports `{% for program in programs %}` exactly like a plain
        queryset did, so the template's loops need no changes.
    For a fully AJAX/infinite-scroll frontend later, `get_programs_for_department_page`
    below returns the same data as JSON.
    """
    detected_dept = detect_user_department(request.user)

    requested_dept_id = request.GET.get("department_id")
    active_dept_id = detected_dept.id if detected_dept else (
        int(requested_dept_id) if requested_dept_id and requested_dept_id.isdigit() else None
    )

    try:
        page_number = max(int(request.GET.get("page", 1)), 1)
    except (TypeError, ValueError):
        page_number = 1

    if detected_dept:
        departments = Department.objects.select_related("faculty").filter(id=detected_dept.id)
    else:
        departments = Department.objects.select_related("faculty").all().only(
            "id", "name", "faculty"
        )

    if active_dept_id:
        programs_qs = Program.objects.filter(department_id=active_dept_id).prefetch_related("courses").order_by("name")
        codes_qs = ProgramCode.objects.filter(program__department_id=active_dept_id).select_related("program").order_by("program__name")
    else:
        # No department chosen yet (non-scoped user, first visit): show the
        # picker only. This is the change that kills the "load everything"
        # behaviour outright.
        programs_qs = Program.objects.none()
        codes_qs = ProgramCode.objects.none()

    programs_paginator = Paginator(programs_qs, DEFAULT_PAGE_SIZE)
    programs_page_obj = programs_paginator.get_page(page_number)

    codes_paginator = Paginator(codes_qs, DEFAULT_PAGE_SIZE)
    codes_page_obj = codes_paginator.get_page(page_number)

    years_range = range(1, 7)  # Years 1-6

    return render(request, "program/programs.html", {
        "departments": departments,
        "active_department_id": active_dept_id,
        "years_range": years_range,
        "page_size": DEFAULT_PAGE_SIZE,
        "programs": programs_page_obj,
        "programs_paginator": programs_paginator,
        "program_codes": codes_page_obj,
        "codes_paginator": codes_paginator,
    })


@login_required
def get_programs_for_department_page(request):
    """
    AJAX endpoint: paginated Programs (+ their ProgramCourses) and
    ProgramCodes for ONE department at a time.

    GET params:
      department_id  (required unless the user is department-scoped)
      page           (default 1)
      page_size      (default DEFAULT_PAGE_SIZE, max 100)
      search         (optional, filters Program name)
    """
    detected_dept = detect_user_department(request.user)

    dept_id = request.GET.get("department_id")
    if detected_dept:
        # A COD/COD-admin can only ever page their own department, regardless
        # of what department_id was passed in.
        dept_id = detected_dept.id

    if not dept_id:
        return JsonResponse({"status": "error", "message": "department_id is required."}, status=400)

    try:
        page_number = max(int(request.GET.get("page", 1)), 1)
    except (TypeError, ValueError):
        page_number = 1
    try:
        page_size = min(max(int(request.GET.get("page_size", DEFAULT_PAGE_SIZE)), 1), 100)
    except (TypeError, ValueError):
        page_size = DEFAULT_PAGE_SIZE

    search = (request.GET.get("search") or "").strip()

    programs_qs = Program.objects.filter(department_id=dept_id).prefetch_related("courses")
    if search:
        programs_qs = programs_qs.filter(name__icontains=search)
    programs_qs = programs_qs.order_by("name")

    paginator = Paginator(programs_qs, page_size)
    page_obj = paginator.get_page(page_number)

    programs_payload = []
    for program in page_obj.object_list:
        programs_payload.append({
            "id": program.id,
            "name": program.name,
            "courses": [
                {
                    "id": pc.id,
                    "course_code": getattr(pc, "course_code", ""),
                    "course_name": getattr(pc, "course_name", ""),
                    "year": getattr(pc, "year", None),
                    "semester": getattr(pc, "semester", None),
                }
                for pc in program.courses.all()
            ],
        })

    # ProgramCodes paged independently under the same page number/size, since
    # it's a different table (kept separate from the Program payload above so
    # each list can be paged without over-fetching the other).
    codes_qs = ProgramCode.objects.filter(program__department_id=dept_id).select_related("program")
    codes_paginator = Paginator(codes_qs.order_by("program__name"), page_size)
    codes_page_obj = codes_paginator.get_page(page_number)
    codes_payload = [
        {"id": c.id, "program_id": c.program_id, "program_name": c.program.name, "code": c.code}
        for c in codes_page_obj.object_list
    ]

    return JsonResponse({
        "status": "success",
        "department_id": int(dept_id),
        "programs": programs_payload,
        "programs_page": page_obj.number,
        "programs_total_pages": paginator.num_pages,
        "programs_total_count": paginator.count,
        "program_codes": codes_payload,
        "codes_page": codes_page_obj.number,
        "codes_total_pages": codes_paginator.num_pages,
    })
