from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
import json
from datetime import datetime
from django.shortcuts import get_object_or_404  
from timetable.models import ExamTimetable
from course_allocation.models import CourseAllocation
@login_required
@require_POST
def cot_exam_api(request):
    """AJAX CRUD API with collision detection and slot appending"""
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    action = data.get("action")

    # --- CREATE ---
    if action == "create":
        latest_allocation = CourseAllocation.objects.filter(submitted_to_tt=True).first()
        if not latest_allocation:
            return JsonResponse({"error": "No course allocation found"}, status=400)

        venue = data.get("venue")
        date_str = data.get("date")
        slot_str = data.get("slot")

        if not (venue and date_str and slot_str):
            return JsonResponse({"error": "Missing required fields"}, status=400)

        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        day_name = date_obj.strftime("%A")
        start_str, end_str = [s.strip() for s in slot_str.split("-")]
        start_time = datetime.strptime(start_str, "%H:%M").time()
        end_time = datetime.strptime(end_str, "%H:%M").time()

        # --- Collision Detection ---
        overlap_exists = ExamTimetable.objects.filter(
            venue=venue,
            date=date_obj,
            start_time__lt=end_time,
            end_time__gt=start_time
        ).exists()

        if overlap_exists and not data.get("force"):
            return JsonResponse({
                "warning": True,
                "message": f"Collision detected: {venue} already booked for this slot. Proceed?"
            })

        # --- Create or Append Entry ---
        existing_entry = ExamTimetable.objects.filter(
            course_allocation=latest_allocation,
            venue=venue,
            date=date_obj,
            start_time=start_time,
            end_time=end_time
        ).first()

        if existing_entry:
            # Append course instead of overwrite
            existing_entry.venue += f", {venue}"
            existing_entry.save()
            return JsonResponse({"success": True, "message": "Slot updated (appended)"})
        else:
            entry = ExamTimetable.objects.create(
                course_allocation=latest_allocation,
                venue=venue,
                day=day_name,
                date=date_obj,
                start_time=start_time,
                end_time=end_time
            )
            return JsonResponse({"success": True, "id": entry.id})

    # --- UPDATE ---
    elif action == "update":
        entry = get_object_or_404(ExamTimetable, id=data.get("id"))
        date_obj = datetime.strptime(data.get("date"), "%Y-%m-%d").date()
        slot_str = data.get("slot")
        start_str, end_str = [s.strip() for s in slot_str.split("-")]
        start_time = datetime.strptime(start_str, "%H:%M").time()
        end_time = datetime.strptime(end_str, "%H:%M").time()

        # Collision check for update
        conflict = ExamTimetable.objects.filter(
            venue=data.get("venue"),
            date=date_obj,
            start_time__lt=end_time,
            end_time__gt=start_time
        ).exclude(id=entry.id).exists()

        if conflict and not data.get("force"):
            return JsonResponse({
                "warning": True,
                "message": f"Conflict detected at {data.get('venue')} on {date_obj}. Proceed?"
            })

        entry.venue = data.get("venue")
        entry.date = date_obj
        entry.day = date_obj.strftime("%A")
        entry.start_time = start_time
        entry.end_time = end_time
        entry.save()
        return JsonResponse({"success": True})

    # --- DELETE ---
    elif action == "delete":
        entry = get_object_or_404(ExamTimetable, id=data.get("id"))
        entry.delete()
        return JsonResponse({"success": True})

    # --- LIST ---
    elif action == "list":
        dept_id = data.get("department")
        qs = ExamTimetable.objects.select_related("course_allocation__department")
        if dept_id:
            qs = qs.filter(course_allocation__department_id=dept_id)

        results = [
            {
                "id": e.id,
                "course": e.course_allocation.course_code,
                "department": e.course_allocation.department.name,
                "venue": e.venue,
                "day": e.day,
                "date": str(e.date),
                "time": f"{e.start_time.strftime('%H:%M')} - {e.end_time.strftime('%H:%M')}"
            }
            for e in qs
        ]
        return JsonResponse({"success": True, "entries": results})

    return JsonResponse({"error": "Unknown action"}, status=400)
