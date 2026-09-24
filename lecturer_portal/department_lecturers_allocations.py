from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required  # kept for reference
from course_allocation.models import CourseAllocation, AllocationSet
from course_allocation.allocation_scope import resolve_allocation_set_for_request, scope_qs
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

    # Concurrent Allocation Sets: this panel used to show/edit every
    # CourseAllocation for the department across every set, so a COD
    # working in "Semester 2" (or a Special allocation) could see and
    # silently edit "Semester 1" rows here too. An explicit
    # allocation_set_id (from the switcher below) wins; otherwise fall
    # back to the session's active set / the department's legacy set —
    # same resolution order used everywhere else in the codebase.
    allocation_set_id = request.POST.get("allocation_set_id") or request.GET.get("allocation_set_id")
    if allocation_set_id:
        active_allocation_set = AllocationSet.objects.filter(id=allocation_set_id, department=dept).first()
    else:
        active_allocation_set = resolve_allocation_set_for_request(request, dept)

    allocations = scope_qs(
        CourseAllocation.objects.filter(department=dept),
        active_allocation_set,
    ).select_related("origin_department", "lecturer", "program").order_by("course_code")

    lecturers = Lecturer.objects.all().order_by("name")

    # Handle update (AJAX or POST)
    if request.method == "POST":
        alloc_id = request.POST.get("id")
        # Scope the edit to this department AND this allocation set so a
        # save can never silently land on a different set's row.
        alloc = get_object_or_404(
            CourseAllocation, id=alloc_id, department=dept, allocation_set=active_allocation_set,
        )

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

    allocation_sets_for_switcher = AllocationSet.objects.filter(
        department=dept, is_archived=False
    ).order_by("-created_at")

    context = {
        "allocations": allocations,
        "lecturers": lecturers,
        "detected_dept": dept,
        "active_allocation_set": active_allocation_set,
        "allocation_sets_for_switcher": allocation_sets_for_switcher,
    }
    return render(request, "lecturer/lecture_allocator_panel.html", context)
