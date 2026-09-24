from django.http import JsonResponse
from timetable.models import ExamTempTimetable, MergedCourseGroup, SharedVenueExamGroup
from room_management.models import Venue
from course_allocation.allocation_scope import apply_tt_scope

def api_exam_venues(request):
    venues = list(
        Venue.objects.all().order_by("code")
        .values("id", "code", "capacity", "exam_capacity")
    )
    return JsonResponse({"venues": venues})
def api_exam_temp_data(request):
    temp = apply_tt_scope(
        ExamTempTimetable.objects, request=request,
        prefix="course_allocation__allocation_set",
    ).select_related(
        "course_allocation", "venue"
    ).values(
        "id", "date", "start_time", "venue__code", "venue_id",
        "course_allocation__course_code",
        "course_allocation__number_of_students",
        "course_allocation__lecturer",
        # This row's own share of the course when it's split across several
        # rooms (see ExamTempTimetable.allocated_students docstring). Without
        # this, the autoscheduler panel had no way to show a split room's
        # real headcount and fell back to the course's FULL enrollment for
        # every room it was split into.
        "allocated_students",
    )

    return JsonResponse({"temp_data": list(temp)})
def api_exam_merged_groups(request):
    groups = []

    scoped_merged = apply_tt_scope(
        MergedCourseGroup.objects, request=request,
        prefix="base_course__allocation_set",
    ).select_related("venue").prefetch_related("merged_courses")

    for m in scoped_merged:
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
        apply_tt_scope(
            SharedVenueExamGroup.objects, request=request,
            prefix="course_allocations__allocation_set",
        ).distinct().values(
            "id", "venue_id", "course_allocations", "date", "start_time"
        )
    )
    return JsonResponse({"shared_groups": shared})
