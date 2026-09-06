from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.db.models import Prefetch
from datetime import datetime
from timetable.models import SchedulerConfig , TempTimetable, AutoMergedExamGroup
from django.contrib import messages
from django.utils import timezone
from django.template.loader import render_to_string
from django.templatetags.static import static
import datetime, random, csv
from weasyprint import HTML

from .models import (
    ExamTempTimetable,
    ExamTimetable,
    ExamSchedulerConfig,
)
from course_allocation.models import CourseAllocation
from program_management.models import ProgramCourse

from room_management.models import Venue


# ============================================================
# Helper Functions
# ============================================================
def update_exam_config(request):
    """AJAX: Update ExamSchedulerConfig"""
    if request.method == "POST":
        config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)

        start_date = request.POST.get("start_date")
        start_time = request.POST.get("start_time")
        end_time = request.POST.get("end_time")
        slot_size = request.POST.get("slot_size")
        excluded_days = request.POST.getlist("excluded_days[]")
        max_exam_days = request.POST.get("max_exam_days")

        if start_date:
            config.start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        if start_time:
            config.start_time = datetime.datetime.strptime(start_time, "%H:%M").time()
        if end_time:
            config.end_time = datetime.datetime.strptime(end_time, "%H:%M").time()
        if slot_size:
            config.slot_size = int(slot_size)
        if max_exam_days:
            config.max_exam_days = int(max_exam_days)
        config.excluded_days = ",".join(excluded_days)

        config.save()
        return JsonResponse({"status": "success", "message": "Configuration saved!"})

    return JsonResponse({"status": "error", "message": "Invalid method"})

def generate_slots(start_time, end_time, slot_size):
    """
    Generate (start, end) slots between start_time and end_time.
    Adds a 60-minute break after each exam slot.
    
    Args:
        start_time (datetime.time): When the first slot starts.
        end_time (datetime.time): When the last slot should end.
        slot_size (float): Duration of each slot in hours (e.g. 2.0 = 2 hours).
    """
    import datetime

    slots = []
    current = datetime.datetime.combine(datetime.date.today(), start_time)
    end = datetime.datetime.combine(datetime.date.today(), end_time)
    slot_delta = datetime.timedelta(hours=slot_size)
    break_delta = datetime.timedelta(minutes=60)  # 60-minute break

    while current + slot_delta <= end:
        nxt = current + slot_delta
        slots.append((current.time(), nxt.time()))
        # Add a 60-min break before next slot
        current = nxt + break_delta

    return slots

