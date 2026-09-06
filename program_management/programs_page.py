# -----------------------
# Programs + Courses + Program Codes Page / AJAX
# -----------------------
from django.shortcuts import render
from department_management.models import Department
from program_management.models import Program, ProgramCode
from django.contrib.auth.decorators import login_required
from typing import Optional
from django.contrib.auth.models import User
from lecturer_portal.models import Lecturer


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

    # Lecturer profile linked directly to this user account.
    try:
        lect = Lecturer.objects.filter(user=user).first()
        if lect and lect.department:
            return lect.department
    except Exception:
        pass

    # Fallback: Lecturer profile matched by email (Lecturer.department is a
    # real FK on the model -- use it).
    try:
        lect = Lecturer.objects.filter(email__iexact=(user.email or "")).first()
        if lect and lect.department:
            return lect.department
    except Exception:
        pass

    # last resort: user.org_role maybe maps to OrgRole.title which could include department
    try:
        org = getattr(user, "org_role", None)
        if org and "COD" in org.title.upper():
            # if OrgRole mapping includes department info in title like "COD - Computer Science"
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
    Page to manage Programs, ProgramCourses, and ProgramCodes.
    - COD / COD Admins: restricted to their department (via detect_user_department).
    - Others (DVC, TT, etc.): see all departments/programs.
    """

    detected_dept = detect_user_department(request.user)

    if detected_dept:
        # COD / COD Admin → only their department
        departments = Department.objects.select_related("faculty").filter(id=detected_dept.id)
        programs = Program.objects.filter(department=detected_dept).prefetch_related("courses")
        program_codes = ProgramCode.objects.filter(
            program__department=detected_dept
        ).select_related("program")
    else:
        # Non-department users (DVC, TT, etc.) → see all
        departments = Department.objects.select_related("faculty").all()
        programs = Program.objects.prefetch_related("courses").all()
        program_codes = ProgramCode.objects.select_related("program").all()

    years_range = range(1, 7)  # Years 1–6

    return render(request, "program/programs.html", {
        "departments": departments,
        "programs": programs,
        "program_codes": program_codes,
        "years_range": years_range,
    })
