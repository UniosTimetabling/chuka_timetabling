from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required  # kept for reference
from course_allocation.models import CourseAllocation
from lecturer_portal.models import  Lecturer
from course_allocation.detect_user_department import detect_user_department
from core.rbac import allowed_roles, Role

@allowed_roles(Role.COD, Role.COD_ADMIN, Role.TIMETABLER, Role.SUDO)
def lecture_allocator_panel(request):
    """
    Lecture Allocator Panel — allows CODs to edit course allocations for their
    department.  Previously guarded only by @login_required; any authenticated
    user could reach and edit allocations.
    """
    user = request.user
    dept = detect_user_department(user)

    if not dept:
        messages.error(request, "Your department could not be detected. Contact Admin.")
        # Redirect to a safe page instead of self to avoid 302 loop
        return redirect("cod_panel")  # <-- change this to your actual dashboard URL name

    allocations = (
        CourseAllocation.objects.filter(department=dept)
        .select_related("origin_department", "lecturer", "program")
        .order_by("course_code")
    )

    lecturers = Lecturer.objects.all().order_by("name")

    # Handle update (AJAX or POST)
    if request.method == "POST":
        alloc_id = request.POST.get("id")
        alloc = get_object_or_404(CourseAllocation, id=alloc_id, department=dept)

        alloc.course_code = request.POST.get("course_code", alloc.course_code)
        alloc.course_name = request.POST.get("course_name", alloc.course_name)

        lecturer_id = request.POST.get("lecturer_id")
        if lecturer_id:
            if lecturer_id == "__new__":
                messages.info(request, "Redirecting to Lecturer Panel to add new lecturer.")
                return redirect("lecturer_panel")
            alloc.lecturer_id = lecturer_id or None

        alloc.save()
        messages.success(request, f"✅ {alloc.course_code} updated successfully.")

    context = {
        "allocations": allocations,
        "lecturers": lecturers,
        "detected_dept": dept,
    }
    return render(request, "lecturer/lecture_allocator_panel.html", context)
