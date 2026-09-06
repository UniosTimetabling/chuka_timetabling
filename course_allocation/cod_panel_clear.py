from collections import defaultdict
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db import transaction
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.db.models import Count

from .models import (
    CourseAllocation,
    ArchivedCourseAllocation,
    SpecialIntakeGroup,
)
from .detect_user_department import detect_user_department
from special_requests.services import tag_semester_for_allocations, carry_forward_special_requests

@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def cod_panel_clear(request):
    """
    Page for COD to archive/delete all allocations for their department (by semester),
    view archived semesters, restore or permanently delete archived semester data.
    """
    user = request.user
    dept = detect_user_department(user)
    if not dept:
        messages.error(request, "Unable to detect your department. Please contact admin.")
        return redirect("cod_panel")

    # active allocations for this COD's department
    allocations = CourseAllocation.objects.filter(department=dept).select_related(
        "lecturer", "program", "origin_department", "special_intake_group"
    ).order_by("course_code")

    # archived allocations for this department (all semesters)
    archived_qs = ArchivedCourseAllocation.objects.filter(department=dept).order_by("-archived_at")

    # group archived allocations by semester for display
    archived_by_semester = defaultdict(list)
    for a in archived_qs:
        archived_by_semester[a.semester].append(a)

    if request.method == "POST":
        action = request.POST.get("action")

        # ---------- Archive & Delete ----------
        if action == "archive_delete":
            semester = (request.POST.get("semester") or "").strip()
            if not semester:
                messages.error(request, "Semester is required to archive allocations. Example: 1 or 2025S1")
                return redirect("cod_panel_clear")

            if not allocations.exists():
                messages.warning(request, "No course allocations found to archive for your department.")
                return redirect("cod_panel_clear")

            with transaction.atomic():
                archived_objs = []
                for a in allocations:
                    archived_objs.append(ArchivedCourseAllocation(
                        department=dept,
                        semester=semester,
                        archived_by=user,
                        course_code=a.course_code,
                        course_name=a.course_name,
                        origin_department=a.origin_department,
                        program=a.program,
                        program_course=a.program_course,
                        lecturer=a.lecturer,
                        number_of_students=a.number_of_students or 0,
                        approved_by_dvc=a.approved_by_dvc,
                        rejected_by_dvc=a.rejected_by_dvc,
                        reason_for_disapproval=a.reason_for_disapproval,
                        submitted_to_tt=a.submitted_to_tt,
                        intake=a.intake,
                        combination_name=a.combination_name if hasattr(a, 'combination_name') else '',
                        selection_group_name=a.selection_group.name if a.selection_group else '',
                    ))
                
                ArchivedCourseAllocation.objects.bulk_create(archived_objs)
                # Stamp the semester onto any open SRs before the allocations
                # (and their SRs, via the post_delete signal) get archived.
                tag_semester_for_allocations(list(allocations), semester)
                count, _ = allocations.delete()
                
                # Clean up empty special intake groups (those with no remaining allocations)
                empty_groups = SpecialIntakeGroup.objects.filter(
                    program__department=dept,
                    course_allocations__isnull=True
                )
                empty_count = empty_groups.count()
                if empty_count:
                    empty_groups.delete()
                    messages.success(
                        request, 
                        f"Archived and deleted {count} allocations for {dept.name} (semester: {semester}). "
                        f"Also cleaned up {empty_count} empty special intake group(s)."
                    )
                else:
                    messages.success(
                        request, 
                        f"Archived and deleted {count} allocations for {dept.name} (semester: {semester})."
                    )
            return redirect("cod_panel_clear")

        # ---------- Restore (publish) archived semester ----------
        if action == "restore_semester":
            semester = (request.POST.get("semester") or "").strip()
            if not semester:
                messages.error(request, "Please specify a semester to restore.")
                return redirect("cod_panel_clear")

            archived = ArchivedCourseAllocation.objects.filter(department=dept, semester=semester)
            if not archived.exists():
                messages.warning(request, f"No archived allocations found for semester '{semester}'.")
                return redirect("cod_panel_clear")

            created = 0
            skipped = 0
            skipped_no_curriculum = 0
            with transaction.atomic():
                for a in archived:
                    # Rows archived before the program_course field existed have
                    # no curriculum link — they can't be restored automatically
                    # since CourseAllocation.program_course is required.
                    if not a.program_course_id:
                        skipped_no_curriculum += 1
                        continue

                    # avoid duplicate creation: same department + course_code + program
                    exists = CourseAllocation.objects.filter(
                        department=dept,
                        course_code__iexact=a.course_code,
                        program=a.program
                    ).exists()
                    if exists:
                        skipped += 1
                        continue
                    
                    new_alloc = CourseAllocation.objects.create(
                        course_code=a.course_code,
                        course_name=a.course_name,
                        department=dept,
                        origin_department=a.origin_department,
                        program=a.program,
                        program_course=a.program_course,
                        lecturer=a.lecturer,
                        number_of_students=a.number_of_students or 0,
                        approved_by_dvc=a.approved_by_dvc,
                        rejected_by_dvc=a.rejected_by_dvc,
                        reason_for_disapproval=a.reason_for_disapproval,
                        submitted_to_tt=a.submitted_to_tt,
                        intake=a.intake or CourseAllocation.INTAKE_NORMAL,
                    )
                    created += 1
                    try:
                        carry_forward_special_requests(new_alloc, panel="normal")
                    except Exception:
                        pass

            msg = f"Restore complete for semester '{semester}': created {created}, skipped {skipped} (already exist)."
            if skipped_no_curriculum:
                msg += (
                    f" {skipped_no_curriculum} record(s) could not be restored because they were "
                    "archived before curriculum linking was added — please re-add those manually."
                )
                messages.warning(request, msg)
            else:
                messages.success(request, msg)
            return redirect("cod_panel_clear")

        # ---------- Permanently delete archived semester ----------
        if action == "delete_archived_semester":
            semester = (request.POST.get("semester") or "").strip()
            if not semester:
                messages.error(request, "Please specify a semester to delete archived data for.")
                return redirect("cod_panel_clear")

            qs = ArchivedCourseAllocation.objects.filter(department=dept, semester=semester)
            cnt = qs.count()
            qs.delete()
            messages.success(request, f"Permanently deleted {cnt} archived allocation(s) for semester '{semester}'.")
            return redirect("cod_panel_clear")

    # GET -> render page
    return render(request, "course_allocation/cod_panel_clear.html", {
        "dept": dept,
        "allocations": allocations,
        "archived_by_semester": dict(archived_by_semester),
    })