from django.http import JsonResponse
from timetable.models import ExamTempTimetable, MergedCourseGroup, SharedVenueExamGroup
from room_management.models import Venue
def api_exam_venues(request):
    venues = list(
        Venue.objects.all().order_by("code")
        .values("id", "code", "capacity")
    )
    return JsonResponse({"venues": venues})
def api_exam_temp_data(request):
    temp = ExamTempTimetable.objects.select_related(
        "course_allocation", "venue"
    ).values(
        "id", "date", "start_time", "venue__code", "venue_id",
        "course_allocation__course_code",
        "course_allocation__number_of_students",
        "course_allocation__lecturer"
    )

    return JsonResponse({"temp_data": list(temp)})
def api_exam_merged_groups(request):
    groups = []

    for m in MergedCourseGroup.objects.select_related("venue").prefetch_related("merged_courses"):
        groups.append({
            "id": m.id,
            "date": m.date,
            "start_time": m.start_time,
            "venue": m.venue.code if m.venue else None,
            "base_code": m.base_course.course_code,
            "merged": [c.course_code for c in m.merged_courses.all()],
        })

    return JsonResponse({"merged_groups": groups})
def api_exam_shared_venues(request):
    shared = list(
        SharedVenueExamGroup.objects.all().values(
            "id", "venue_id", "course_allocations", "date", "start_time"
        )
    )
    return JsonResponse({"shared_groups": shared})
