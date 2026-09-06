from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.db import models, transaction
from program_management.models import (
    Program,
    ProgramCourse,
)
from django.views.decorators.http import require_POST
from django.utils.dateparse import parse_time
from django.db.models import Q
from course_allocation.models import CourseAllocation
from timetable.models import Timetable, ExamTimetable,AutoMergedExamGroup,MergedCourseGroup,LabTimetable,LabExamTimetable
from classreps.models import MinimalTimetable, ClassRep
from room_management.models import Venue, LabVenue
import json
# -------------------------------------------------
# UPDATE NORMAL TIMETABLE ENTRY
# -------------------------------------------------
@require_POST
def update_timetable(request):
    data = request.POST
    venue_code = data.get("venue")
    day = data.get("day")
    slot = data.get("slot")
    course_code = data.get("course")

    if not (venue_code and day and slot and course_code):
        return JsonResponse({"error": "Missing fields"}, status=400)

    start_time_str, end_time_str = slot.split("-")
    start_time = parse_time(start_time_str)
    end_time = parse_time(end_time_str)

    try:
        course = CourseAllocation.objects.get(course_code=course_code)
    except CourseAllocation.DoesNotExist:
        return JsonResponse({"error": f"Course '{course_code}' not found"}, status=404)

    # Update if exists, otherwise create
    tt, created = Timetable.objects.update_or_create(
        venue=venue_code,
        day=day,
        start_time=start_time,
        defaults={
            "end_time": end_time,
            "course_allocation": course,
        },
    )

    return JsonResponse({"status": "created" if created else "updated"})


# -------------------------------------------------
# DELETE NORMAL TIMETABLE ENTRY
# -------------------------------------------------
@require_POST
def delete_timetable(request):
    data = request.POST
    venue_code = data.get("venue")
    day = data.get("day")
    slot = data.get("slot")

    if not (venue_code and day and slot):
        return JsonResponse({"error": "Missing fields"}, status=400)

    start_time_str, _ = slot.split("-")
    start_time = parse_time(start_time_str)

    deleted, _ = Timetable.objects.filter(
        venue=venue_code,
        day=day,
        start_time=start_time
    ).delete()

    return JsonResponse({"status": "deleted" if deleted else "not_found"})


# -------------------------------------------------
# UPDATE LAB TIMETABLE ENTRY
# -------------------------------------------------
@require_POST
def update_lab_timetable(request):
    data = json.loads(request.body.decode("utf-8"))
    lab_venue_code = data.get("lab_venue")
    day = data.get("day")
    start_time = parse_time(data.get("start_time"))
    end_time = parse_time(data.get("end_time"))
    course_code = data.get("course")

    try:
        lab_venue = LabVenue.objects.get(code=lab_venue_code)
        allocation = CourseAllocation.objects.get(course_code=course_code)
    except (LabVenue.DoesNotExist, CourseAllocation.DoesNotExist):
        return JsonResponse({"error": "Invalid lab venue or course"}, status=404)

    lab_tt, created = LabTimetable.objects.update_or_create(
        lab_venue=lab_venue,
        day=day,
        start_time=start_time,
        defaults={
            "end_time": end_time,
            "lab_allocation": allocation,
        },
    )

    return JsonResponse({"status": "created" if created else "updated"})



# -------------------------------------------------
# CRUD for exam timetable
# -------------------------------------------------
@require_POST
def create_exam_timetable(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
        venue = data.get("venue")
        date = data.get("date")
        slot = data.get("slot")
        course_code = data.get("course")
        start_time, end_time = slot.split("-")

        course = CourseAllocation.objects.filter(course_code=course_code).first()
        if not course:
            return JsonResponse({"status": "error", "message": "Course not found"}, status=404)

        conflict = ExamTimetable.objects.filter(
            venue=venue, date=date,
            start_time__lt=end_time, end_time__gt=start_time
        ).exists()
        if conflict:
            return JsonResponse({"status": "error", "message": "Time conflict"}, status=409)

        timetable = ExamTimetable.objects.create(
            course_allocation=course,
            venue=venue,
            date=date,
            start_time=start_time,
            end_time=end_time
        )
        return JsonResponse({"status": "success", "id": timetable.id})
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)


@require_POST
def update_exam_timetable(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
        timetable_id = data.get("id")
        timetable = ExamTimetable.objects.get(id=timetable_id)
        timetable.course_allocation = CourseAllocation.objects.get(course_code=data.get("course"))
        timetable.save()
        return JsonResponse({"status": "success"})
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)
