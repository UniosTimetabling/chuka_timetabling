"""
course_management.allocation_set_panel
---------------------------------------
The new "which allocation are you editing?" layer in front of the existing
cod_panel. Deliberately kept as its own module rather than folded into the
6,000-line cod_panel.py, so this can be reviewed/tested on its own and the
existing panel's 49 CourseAllocation call sites don't all need surgery at
once to ship this.

Flow implemented here, matching the request:
  1. GET /cod/allocations/            -> picker: existing AllocationSets for
     the COD's department (grouped by status), + "Create new".
  2. GET/POST /cod/allocations/new/   -> step 1: allocation type
     (auto_full vs selective) + special flag + academic year/name.
  3. GET/POST /cod/allocations/new/compose/<id>/ -> step 2: semester
     composition. First semester chosen is scope=ALL (primary). COD can
     "+ mix in another semester" and choose ALL-for-that-semester-too, or
     SELECTED with specific programs (and later, specific courses) — this
     can be repeated for semester 1, 2, 3, ... with no upper bound.
     On submit: auto_full sets get auto-allocated (populate + trigger the
     auto-allocator for the composed courses); selective sets skip
     auto-allocation and go straight to the course-picker.
  4. GET /cod/allocations/<id>/select-courses/ -> for allocation_type=selective
     (or for adding extra semester layers to an existing set): tick
     programs/courses per program, "Add" per program, keep going across
     programs, then "Done" -> redirects into the normal cod_panel scoped to
     this set.
  5. POST /cod/allocations/switch/    -> sets the active set in-session and
     redirects back to cod_panel (used by the switcher shown on every page,
     including for DVC).
  6. POST /cod/allocations/<id>/submit/ -> submits ONLY this set to TT (or
     DVC) — never touches any other AllocationSet's status.
"""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from department_management.models import Department
from program_management.models import Program, ProgramCourse

from .cod_panel import detect_user_department, _assert_owns_department
from course_allocation.models import (
    AllocationSet, AllocationSetSemesterComponent,
)
from course_allocation.allocation_scope import (
    set_active_allocation_set, resolve_allocation_set_for_request,
    populate_allocation_set, add_selected_courses_by_id,
)


def _dept_or_403(request):
    dept = detect_user_department(request.user)
    return dept


@login_required
def allocation_picker(request):
    dept = _dept_or_403(request)
    if not dept:
        messages.error(request, "No department detected for your account.")
        return redirect("cod_panel")

    # Where to send the COD back to once they've picked/created a set.
    # Defaults to cod_panel (the original behaviour) but lets any other
    # page — e.g. auto-allocate-courses — send the COD back to itself
    # instead of always landing on cod_panel.
    from django.urls import reverse
    next_url = request.GET.get("next") or reverse("cod_panel")

    sets = AllocationSet.objects.filter(department=dept, is_archived=False).order_by("-created_at")
    hidden_sets = AllocationSet.objects.filter(department=dept, is_archived=True).order_by("-created_at")
    return render(request, "course_management/allocation_picker.html", {
        "department": dept,
        "allocation_sets": sets,
        "hidden_sets": hidden_sets,
        "next_url": next_url,
    })


@login_required
@require_POST
def hide_allocation_set(request, allocation_set_id):
    """
    Hides an allocation from the picker and from every other place that
    reads AllocationSet querysets (they all already filter is_archived=False
    — timetable views, dashboards, reports, auto-allocate, etc.) without
    deleting anything. Reversible via unhide_allocation_set.
    """
    dept = _dept_or_403(request)
    allocation_set = get_object_or_404(AllocationSet, id=allocation_set_id, department=dept)
    allocation_set.is_archived = True
    allocation_set.save(update_fields=["is_archived"])
    messages.success(request, f"'{allocation_set.name}' hidden. It won't appear here or in the timetable until you unhide it.")
    return redirect("allocation_picker")


@login_required
@require_POST
def unhide_allocation_set(request, allocation_set_id):
    """Brings a hidden allocation back into the picker and everywhere else."""
    dept = _dept_or_403(request)
    allocation_set = get_object_or_404(AllocationSet, id=allocation_set_id, department=dept)
    allocation_set.is_archived = False
    allocation_set.save(update_fields=["is_archived"])
    messages.success(request, f"'{allocation_set.name}' is visible again.")
    return redirect("allocation_picker")


@login_required
def edit_allocation_details(request, allocation_set_id):
    """
    Lets the COD fix up an allocation's own details (name, academic year,
    type, special flag) without going through the create wizard again.
    Reachable from an "Edit" button on the /cod/allocations/ picker.
    Never touches semester composition or course rows — that's still done
    via the compose/select-courses pages.
    """
    dept = _dept_or_403(request)
    if not dept:
        messages.error(request, "No department detected for your account.")
        return redirect("cod_panel")

    allocation_set = get_object_or_404(AllocationSet, id=allocation_set_id, department=dept)

    if request.method == "POST":
        name = request.POST.get("name", "").strip() or allocation_set.name
        academic_year = request.POST.get("academic_year", "").strip()
        allocation_type = request.POST.get("allocation_type", allocation_set.allocation_type)
        is_special = request.POST.get("is_special") == "on"

        allocation_set.name = name
        allocation_set.academic_year = academic_year
        allocation_set.allocation_type = allocation_type
        allocation_set.is_special = is_special
        allocation_set.save(update_fields=["name", "academic_year", "allocation_type", "is_special"])

        messages.success(request, f"'{allocation_set.name}' updated.")
        return redirect("allocation_picker")

    return render(request, "course_management/allocation_edit.html", {
        "department": dept,
        "allocation_set": allocation_set,
    })


@login_required
def create_allocation_step1(request):
    """Step 1: type + special flag + a name/academic year."""
    dept = _dept_or_403(request)
    if not dept:
        messages.error(request, "No department detected for your account.")
        return redirect("cod_panel")

    next_url = request.GET.get("next") or request.POST.get("next") or ""

    if request.method == "POST":
        allocation_type = request.POST.get("allocation_type", AllocationSet.TYPE_AUTO_FULL)
        is_special = request.POST.get("is_special") == "on"
        name = request.POST.get("name", "").strip() or "New Allocation"
        academic_year = request.POST.get("academic_year", "").strip()

        allocation_set = AllocationSet.objects.create(
            department=dept,
            name=name,
            academic_year=academic_year,
            allocation_type=allocation_type,
            is_special=is_special,
            status=AllocationSet.STATUS_DRAFT,
            created_by=request.user,
        )
        redirect_url = reverse("create_allocation_step2", kwargs={"allocation_set_id": allocation_set.id})
        if next_url:
            redirect_url += f"?next={next_url}"
        return redirect(redirect_url)

    return render(request, "course_management/allocation_create_step1.html", {
        "department": dept,
        "next_url": next_url,
    })


@login_required
def create_allocation_step2(request, allocation_set_id):
    """
    Step 2: semester composition. Repeatable "add a semester layer" form.
    Submitting "Finish" here triggers auto-allocation for auto_full sets, or
    sends selective sets to the course picker.
    """
    dept = _dept_or_403(request)
    allocation_set = get_object_or_404(AllocationSet, id=allocation_set_id, department=dept)
    programs = Program.objects.filter(department=dept).order_by("name")
    next_url = request.GET.get("next") or request.POST.get("next") or ""

    def _step2_redirect():
        url = reverse("create_allocation_step2", kwargs={"allocation_set_id": allocation_set.id})
        return redirect(f"{url}?next={next_url}" if next_url else url)

    if request.method == "POST":
        action = request.POST.get("form_action")

        if action == "add_semester":
            semester_numbers = [int(n) for n in request.POST.getlist("semester_numbers")]
            if not semester_numbers:
                messages.error(request, "Tick at least one semester before adding.")
                return _step2_redirect()

            scope = request.POST.get("scope", AllocationSetSemesterComponent.SCOPE_ALL)
            program_ids = request.POST.getlist("program_ids") if scope == AllocationSetSemesterComponent.SCOPE_SELECTED else []

            added_numbers = []
            for semester_number in semester_numbers:
                component, created = allocation_set.semester_components.get_or_create(
                    semester_number=semester_number, defaults={"scope": scope},
                )
                if not created:
                    # Already existed — update its scope/programs instead of skipping silently.
                    component.scope = scope
                    component.save(update_fields=["scope"])
                if scope == AllocationSetSemesterComponent.SCOPE_SELECTED and program_ids:
                    component.programs.set(program_ids)
                added_numbers.append(str(semester_number))

            label = ", ".join(added_numbers)
            messages.success(request, f"Semester{'s' if len(added_numbers) > 1 else ''} {label} added to this allocation.")
            return _step2_redirect()

        if action == "remove_semester":
            component_id = request.POST.get("component_id")
            allocation_set.semester_components.filter(id=component_id).delete()
            return _step2_redirect()

        if action == "finish":
            if not allocation_set.semester_components.exists():
                messages.error(request, "Add at least one semester before finishing.")
                return _step2_redirect()

            set_active_allocation_set(request, allocation_set)

            if allocation_set.allocation_type == AllocationSet.TYPE_AUTO_FULL:
                # Concurrent Allocation Sets: run the REAL auto-allocator
                # (lecturer assignment, split-group sizing, grouping/
                # combination templates — everything the panel's normal
                # auto-allocate button does) for every ALL-scope semester
                # layer, scoped to THIS set so it never touches another
                # set's rows. SELECTED-scope layers (courses mixed in from
                # another semester) skip the full algorithm and are just
                # populated directly — they're explicit hand-picks, not a
                # "give me the whole semester" request.
                from course_allocation.auto_allocate_courses import _run_allocation
                total_created = 0
                for component in allocation_set.semester_components.filter(
                    scope=AllocationSetSemesterComponent.SCOPE_ALL
                ):
                    result = _run_allocation(dept, component.semester_number, request.user, allocation_set=allocation_set)
                    total_created += result.get("created", 0)
                for component in allocation_set.semester_components.exclude(
                    scope=AllocationSetSemesterComponent.SCOPE_ALL
                ):
                    total_created += populate_allocation_set(allocation_set)

                messages.success(
                    request,
                    f"'{allocation_set.name}' auto-allocated — {total_created} course(s) created "
                    f"(lecturers assigned where the algorithm could match one).",
                )
                return redirect(next_url or "cod_panel")
            else:
                # Selective: don't auto-populate everything — send the COD to
                # hand-pick which courses go into this set.
                url = reverse("select_allocation_courses", kwargs={"allocation_set_id": allocation_set.id})
                return redirect(f"{url}?next={next_url}" if next_url else url)

    existing_components = allocation_set.semester_components.all()
    return render(request, "course_management/allocation_create_step2.html", {
        "department": dept,
        "allocation_set": allocation_set,
        "programs": programs,
        "existing_components": existing_components,
        "existing_semester_numbers": set(existing_components.values_list("semester_number", flat=True)),
        "semester_number_choices": range(1, 7),
        "next_url": next_url,
    })


@login_required
def select_allocation_courses(request, allocation_set_id):
    """
    Per-program course tick-list. Used for:
      - allocation_type=selective sets (the "select certain courses" type)
      - adding hand-picked courses from another semester into an
        otherwise full-semester set
    Selection can be done per-program, submitted, and repeated for another
    program without losing what was already added (each POST only adds to
    the set — it never clears previous selections).
    """
    dept = _dept_or_403(request)
    allocation_set = get_object_or_404(AllocationSet, id=allocation_set_id, department=dept)
    programs = Program.objects.filter(department=dept).order_by("name")
    next_url = request.GET.get("next") or request.POST.get("next") or ""

    selected_program_id = request.GET.get("program_id") or request.POST.get("program_id")
    if not selected_program_id and programs:
        # Default to the first program so the right-hand table is never
        # blank on first load — the COD can still switch on the left.
        selected_program_id = str(programs[0].id)

    courses_for_program = []
    if selected_program_id:
        courses_for_program = list(
            ProgramCourse.objects.filter(program_id=selected_program_id).order_by("semester", "year", "course_code")
        )

    if request.method == "POST" and request.POST.get("form_action") == "add_courses":
        program_course_ids = request.POST.getlist("program_course_ids")
        try:
            num_groups = int(request.POST.get("num_groups", "1"))
        except (TypeError, ValueError):
            num_groups = 1
        num_groups = max(1, min(num_groups, 20))
        added = add_selected_courses_by_id(allocation_set, program_course_ids, num_groups=num_groups)
        if num_groups > 1:
            messages.success(request, f"Added {added} lettered group(s) ({num_groups} per course) from this program.")
        else:
            messages.success(request, f"Added {added} course(s) from this program.")
        url = f"/cod/allocations/{allocation_set.id}/select-courses/?program_id={selected_program_id}"
        if next_url:
            url += f"&next={next_url}"
        return redirect(url)

    if request.method == "POST" and request.POST.get("form_action") == "done":
        set_active_allocation_set(request, allocation_set)
        return redirect(next_url or "cod_panel")

    return render(request, "course_management/allocation_select_courses.html", {
        "department": dept,
        "allocation_set": allocation_set,
        "programs": programs,
        "selected_program_id": selected_program_id,
        "courses_for_program": courses_for_program,
        "next_url": next_url,
        "already_added_ids": set(
            allocation_set.course_allocations.values_list("program_course_id", flat=True)
        ),
    })


@login_required
@require_POST
def switch_allocation_set(request):
    """Used by the switcher dropdown on cod_panel / DVC panel."""
    dept = _dept_or_403(request)
    allocation_set_id = request.POST.get("allocation_set_id")
    allocation_set = get_object_or_404(AllocationSet, id=allocation_set_id)

    if allocation_set.is_archived:
        messages.error(request, f"'{allocation_set.name}' is hidden. Unhide it first if you need to switch to it.")
        return redirect("allocation_picker")

    # A COD can only switch within their own department. DVC/TT users (no
    # detected department) may switch across any department's sets — the
    # DVC panel view itself enforces which departments a given DVC can see.
    if dept and allocation_set.department_id != dept.id:
        return JsonResponse({"status": "error", "message": "Not your department's allocation."}, status=403)

    set_active_allocation_set(request, allocation_set)
    next_url = request.POST.get("next") or "cod_panel"
    return redirect(next_url)


@login_required
@require_POST
@transaction.atomic
def submit_allocation_set(request, allocation_set_id):
    """
    Submits ONLY this AllocationSet — never any other set for this
    department, and never any other department's data. Mirrors the
    "the COD selects submit based on the page he is on" requirement.
    """
    dept = _dept_or_403(request)
    allocation_set = get_object_or_404(AllocationSet, id=allocation_set_id, department=dept)

    if allocation_set.is_archived:
        messages.error(request, f"'{allocation_set.name}' is hidden — unhide it first before submitting.")
        return redirect("cod_panel")

    target = request.POST.get("target", "tt")  # "tt" or "dvc"
    if target == "dvc":
        allocation_set.status = AllocationSet.STATUS_SUBMITTED_TO_DVC
        allocation_set.submitted_to_dvc_at = timezone.now()
    else:
        allocation_set.status = AllocationSet.STATUS_SUBMITTED_TO_TT
        allocation_set.submitted_to_tt_at = timezone.now()
    allocation_set.save(update_fields=["status", "submitted_to_dvc_at", "submitted_to_tt_at"])

    messages.success(request, f"'{allocation_set.name}' submitted.")
    return redirect("cod_panel")
