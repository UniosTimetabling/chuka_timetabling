from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from course_allocation.models import CourseAllocation
from department_management.models import Department

@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def course_allocations_list(request):
    """
    Show all CourseAllocation rows plus departments for filtering.
    """
    # Eager load related objects for efficiency
    allocations = CourseAllocation.objects.select_related(
        "department", "origin_department", "program", "lecturer"
    ).all().order_by("course_code", "course_name")

    departments = Department.objects.all().order_by("name")

    return render(
        request,
        "course_allocation/course_allocations.html",
        {
            "allocations": allocations,
            "departments": departments,
        },
    )
