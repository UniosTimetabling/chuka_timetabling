from django.shortcuts import render
from django.contrib.auth.decorators import login_required  # kept for reference

from core.rbac import allowed_roles, HOD_AND_ABOVE, Role, user_has_role
from lecturer_portal.models import Lecturer
from course_allocation.detect_user_department import detect_user_department

# Roles that manage the whole university, not just one department — these
# keep the old "see everything" behaviour. COD / COD Admin are department-
# scoped and should only see/manage lecturers in their own department.
UNIVERSITY_WIDE_ROLES = (
    Role.DEAN, Role.DEAN_ADMIN,
    Role.DVC, Role.DVC_ADMIN,
    Role.DIRECTOR, Role.TIMETABLE_ADMIN,
    Role.SUDO,
)


# -----------------------
# Lecturer Panel
# -----------------------
@allowed_roles(*HOD_AND_ABOVE)
def lecturer_panel(request):
    """Render lecturer panel page with AJAX CRUD.

    Restricted to HOD-and-above (COD, Dean, DVC, Director, Timetable Admin,
    Sudo).  Previously guarded only by @login_required — any authenticated
    user could add/edit/delete lecturer records.

    COD / COD Admin accounts are department-scoped: they only see their own
    department's lecturers, and the department dropdown is locked to their
    own department so they can't add/reassign a lecturer elsewhere.
    Dean/DVC/Director/Timetable Admin/Sudo continue to see and manage
    lecturers across all departments.
    """
    from department_management.models import Department

    user = request.user
    is_university_wide = user.is_superuser or user_has_role(user, *UNIVERSITY_WIDE_ROLES)

    if is_university_wide:
        lecturers = Lecturer.objects.select_related("department").all()
        departments = Department.objects.all().order_by("name")
        user_department = None
    else:
        user_department = detect_user_department(user)
        if user_department:
            lecturers = Lecturer.objects.select_related("department").filter(department=user_department)
            departments = Department.objects.filter(pk=user_department.pk)
        else:
            lecturers = Lecturer.objects.none()
            departments = Department.objects.none()

    return render(request, "lecturer/lecturer_panel.html", {
        "lecturers": lecturers,
        "departments": departments,
        "user_department": user_department,
        "is_university_wide": is_university_wide,
    })