# ---------- DELETE SINGLE ----------
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.views.decorators.http import require_POST
from django.db import transaction, models
from timetable.models import ExamTimetable, SharedVenueExamGroup, MergedCourseGroup
from room_management.models import Venue


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
@transaction.atomic
def delete_exam_entry(request):
    """
    Deletes an ExamTimetable entry and any related SharedVenueExamGroup
    or MergedCourseGroup entries that reference the same course(s), venue, date, and time.
    """
    entry_id = request.POST.get("id")
    if not entry_id:
        print("⚠️ Missing entry ID.")
        return JsonResponse({"status": "error", "message": "Missing entry ID."}, status=400)

    try:
        entry = ExamTimetable.objects.select_related("course_allocation").get(id=entry_id)
        print(f"🗑️ Attempting to delete ExamTimetable ID={entry.id}")

        course_allocation = getattr(entry, "course_allocation", None)
        date = getattr(entry, "date", None)
        start_time = getattr(entry, "start_time", None)
        end_time = getattr(entry, "end_time", None)
        venue_value = getattr(entry, "venue", None)

        # Normalize venue
        venue_obj = None
        if venue_value:
            if hasattr(venue_value, "id"):
                venue_obj = venue_value
            else:
                venue_obj = Venue.objects.filter(code=str(venue_value).strip()).first()

        deleted_shared = 0
        deleted_merged = 0

        # 1️⃣ Delete related merged course groups (both published/unpublished)
        if course_allocation:
            merged_qs = MergedCourseGroup.objects.filter(
                models.Q(base_course=course_allocation) |
                models.Q(merged_courses=course_allocation)
            )

            if merged_qs.exists():
                print(f"🧾 Found {merged_qs.count()} merged groups to delete for course {course_allocation}.")
                deleted_merged, _ = merged_qs.delete()
            else:
                print(f"⚠️ No merged groups found for course {course_allocation}.")

        # 2️⃣ Delete related shared venue groups (both published/unpublished)
        if venue_obj and date and start_time and end_time:
            shared_qs = SharedVenueExamGroup.objects.filter(
                venue=venue_obj,
                date=date
            ).filter(
                start_time__lte=start_time,
                end_time__gte=end_time,
            )

            if shared_qs.exists():
                print(f"🏫 Found {shared_qs.count()} shared groups to delete for venue={venue_obj}, date={date}.")
                deleted_shared, _ = shared_qs.delete()
            else:
                print(f"⚠️ No shared venue groups found for venue={venue_obj}, date={date}.")

        # 3️⃣ Delete the main exam timetable entry
        entry.delete()
        print(f"✅ Successfully deleted ExamTimetable ID={entry_id}")

        return JsonResponse({
            "status": "success",
            "message": f"Exam entry deleted. ({deleted_shared} shared, {deleted_merged} merged deleted)",
            "deleted_shared": deleted_shared,
            "deleted_merged": deleted_merged,
        })

    except ExamTimetable.DoesNotExist:
        print("❌ Entry not found in ExamTimetable table.")
        return JsonResponse({"status": "error", "message": "Entry not found"}, status=404)

    except Exception as e:
        print(f"🔥 Server error deleting exam entry: {e}")
        return JsonResponse({
            "status": "error",
            "message": f"Server error: {str(e)}",
        }, status=500)

