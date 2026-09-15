from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.http import JsonResponse



from timetable.models import Timetable, AutoMergedExamGroup


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def delete_timetable_entry(request):
    """
    Deletes a specific timetable entry via AJAX.
    Also removes any AutoMergedExamGroup linked to the same course allocation.
    """
    entry_id = request.POST.get("id")
    if not entry_id:
        return JsonResponse({"status": "error", "message": "Missing entry ID."}, status=400)

    try:
        entry = Timetable.objects.get(pk=entry_id)
        course_alloc = entry.course_allocation

        # Capture the freed allocation's details BEFORE deleting, so the
        # frontend can put it back into the "unscheduled" list/dropdown
        # immediately (e.g. when removed from a combined/merged group cell
        # or from the conflict details modal).
        allocation_info = None
        if course_alloc:
            allocation_info = {
                "id": course_alloc.id,
                "course_code": course_alloc.course_code,
                "course_name": course_alloc.course_name,
                "lecturer": getattr(course_alloc.lecturer, "name", "Unassigned"),
            }

        # Delete the timetable entry first
        entry.delete()

        # Find and delete AutoMergedExamGroups associated with this course allocation
        deleted_count = 0

        if course_alloc:
            # 1️⃣ If this allocation was the base course of an auto-merged group
            base_groups = AutoMergedExamGroup.objects.filter(base_course=course_alloc)
            deleted_count += base_groups.count()
            base_groups.delete()

            # 2️⃣ If this allocation appeared as a merged member in any group
            merged_groups = AutoMergedExamGroup.objects.filter(merged_courses=course_alloc)
            deleted_count += merged_groups.count()
            merged_groups.delete()

        return JsonResponse({
            "status": "success",
            "message": f"Entry deleted. {deleted_count} linked auto-merged group(s) also removed.",
            "allocation_info": allocation_info,
        })

    except Timetable.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Entry not found."}, status=404)
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)

    # Fallback (shouldn't reach here)
    return redirect("timetable_panel")