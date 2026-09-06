import datetime
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.utils.dateparse import parse_time

from timetable.models import (
    LabTimetable, LabSchedulerConfig
)
from course_allocation.models import LabAllocation
from room_management.models import LabVenue

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def _get_scheduler_config():
    cfg, _ = LabSchedulerConfig.objects.get_or_create(
        pk=1,
        defaults={"start_time": "07:00", "end_time": "19:00", "slot_size": 2},
    )
    return cfg


def _cfg_to_dict(cfg):
    return {
        "id": cfg.id,
        "start_time": cfg.start_time if isinstance(cfg.start_time, str) else cfg.start_time.strftime("%H:%M"),
        "end_time": cfg.end_time if isinstance(cfg.end_time, str) else cfg.end_time.strftime("%H:%M"),
        "slot_size": cfg.slot_size,
    }


def _parse_time_string(time_str):
    """Parse time string to datetime.time object"""
    if isinstance(time_str, datetime.time):
        return time_str
    if isinstance(time_str, str):
        parsed = parse_time(time_str)
        if parsed:
            return parsed
    # Default fallback
    return datetime.time(7, 0)  # 07:00


def _generate_time_slots(cfg):
    slots = []
    
    # Parse time strings to datetime.time objects
    start_time = _parse_time_string(cfg.start_time)
    end_time = _parse_time_string(cfg.end_time)
    
    # Use datetime.combine with date.today() and the time objects
    start = datetime.datetime.combine(datetime.date.today(), start_time)
    end = datetime.datetime.combine(datetime.date.today(), end_time)
    
    step = datetime.timedelta(hours=cfg.slot_size)
    cur = start
    
    while cur + step <= end:
        st, et = cur.time(), (cur + step).time()
        slots.append((st.strftime("%H:%M"), f"{st.strftime('%H:%M')} - {et.strftime('%H:%M')}"))
        cur += step
    
    return slots


@login_required
def lab_timetable_panel(request):
    cfg = _get_scheduler_config()
    time_slots = _generate_time_slots(cfg)

    timetables = LabTimetable.objects.select_related(
        "lab_allocation__program_course", "lab_allocation__lecturer", "lab_venue"
    ).prefetch_related("lab_allocation__venues")

    all_allocations = LabAllocation.objects.select_related(
        "program_course", "lecturer"
    ).prefetch_related("venues")

    venues = LabVenue.objects.all().order_by("code")

    context = {
        "heading": "Lab Timetable",
        "cfg": cfg,
        "time_slots": time_slots,
        "weekdays": WEEKDAYS,
        "timetables": timetables,
        "all_allocations": all_allocations,
        "venues": venues,
    }
    return render(request, "dashboard/lab_timetable.html", context)