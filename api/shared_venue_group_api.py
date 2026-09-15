from django.views.decorators.http import require_http_methods
from django.forms.models import model_to_dict
import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from datetime import datetime, time, timedelta
from timetable.models import (
    ExamSchedulerConfig,
    ExamTimetable,
    MergedCourseGroup,
    SharedVenueExamGroup,
)
from course_allocation.models import CourseAllocation
from room_management.models import Venue
from core.group_required import group_required
@login_required
@group_required("Director Timetable", "Timetable Admins")
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def shared_venue_group_api(request):
    """
    Handles CRUD (Create, Read, Update, Delete) for SharedVenueExamGroup
    All through one endpoint using HTTP method dispatch.
    """

    if request.method == "GET":
        # Fetch published only
        groups = (
            SharedVenueExamGroup.objects.filter(published=True)
            .select_related("venue")
            .prefetch_related("course_allocations")
            .order_by("date", "start_time")
        )

        data = [
            {
                "id": g.id,
                "venue": g.venue.code,
                "date": g.date.strftime("%Y-%m-%d"),
                "day": g.day,
                "start_time": g.start_time.strftime("%H:%M"),
                "end_time": g.end_time.strftime("%H:%M"),
                "total_students": g.total_students,
                "courses": [
                    {
                        "id": c.id,
                        "course_code": c.course_code,
                        "lecturer": str(c.lecturer),
                    }
                    for c in g.course_allocations.all()
                ],
            }
            for g in groups
        ]
        return JsonResponse({"status": "success", "groups": data})

    elif request.method == "POST":
        # Create new group
        try:
            data = json.loads(request.body)
            venue_code = data.get("venue")
            date = data.get("date")
            start_time = data.get("start_time")
            end_time = data.get("end_time")
            course_ids = data.get("course_ids", [])

            venue = Venue.objects.get(code=venue_code)
            day = datetime.strptime(date, "%Y-%m-%d").strftime("%A")

            group = SharedVenueExamGroup.objects.create(
                venue=venue,
                date=date,
                day=day,
                start_time=start_time,
                end_time=end_time,
                published=True,  # Auto-publish on creation
            )
            group.course_allocations.set(course_ids)
            group.update_total_students()

            return JsonResponse({"status": "success", "message": "Shared group created successfully"})

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)})

    elif request.method == "PUT":
        # Update
        try:
            data = json.loads(request.body)
            group = SharedVenueExamGroup.objects.get(id=data["id"])
            if "venue" in data:
                group.venue = Venue.objects.get(code=data["venue"])
            if "date" in data:
                group.date = data["date"]
                group.day = datetime.strptime(group.date, "%Y-%m-%d").strftime("%A")
            if "start_time" in data:
                group.start_time = data["start_time"]
            if "end_time" in data:
                group.end_time = data["end_time"]
            if "course_ids" in data:
                group.course_allocations.set(data["course_ids"])
            group.save()
            group.update_total_students()

            return JsonResponse({"status": "success", "message": "Shared group updated successfully"})

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)})

    elif request.method == "DELETE":
        # Delete
        try:
            data = json.loads(request.body)
            SharedVenueExamGroup.objects.get(id=data["id"]).delete()
            return JsonResponse({"status": "success", "message": "Shared group deleted successfully"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)})
