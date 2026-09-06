from django.contrib.auth.models import User
from department_management.models import Department
from lecturer_portal.models import Lecturer
from typing import Optional


def detect_user_department(user: User) -> Optional[Department]:
    """
    Try several heuristics to find the department associated with the logged-in user.
    Priority: OrgRole.department (authoritative, works for COD Admin too) ->
    Department leader -> Lecturer profile dept (by user FK, then by email) ->
    OrgRole title (legacy).

    Kept in sync with course_management.cod_panel.detect_user_department, which
    is the canonical implementation used by /cod/.
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
