from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.utils.dateparse import parse_time
from django.db.models import Q
import json

from timetable.models import (
    Timetable, LabTimetable,
    SchedulerConfig, LabSchedulerConfig
)
from course_allocation.models import CourseAllocation
from course_allocation.allocation_scope import apply_tt_scope
from room_management.models import Venue, LabVenue
# -------------------------------------------------
# MAIN TIMETABLE VIEW
# -------------------------------------------------
def main_timetable_view(request):
    # Fetch scheduler configs for time slot generation
    config = SchedulerConfig.objects.first()
    lab_config = LabSchedulerConfig.objects.first()

    def generate_slots(config):
        """Generate readable time slot strings."""
        slots = []
        if not config:
            return ["08:00-10:00", "10:00-12:00", "12:00-14:00", "14:00-16:00"]
        start = config.start_time.hour
        end = config.end_time.hour
        size = config.slot_size
        for t in range(start, end, size):
            s = f"{t:02d}:00"
            e = f"{t+size:02d}:00"
            slots.append(f"{s}-{e}")
        return slots

    timeslots = generate_slots(config)
    lab_timeslots = generate_slots(lab_config)

    # Load database data
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    venues = Venue.objects.all()
    lab_venues = LabVenue.objects.all()
    # Concurrent Allocation Sets: only show rows belonging to whichever
    # AllocationSet(s) are active for this session (ticked on
    # /timetable/dashboard/, or the "eligible" fallback — no set, legacy,
    # or submitted-to-TT — if nothing's been ticked yet). This is what
    # keeps two departments' (or two semesters') concurrent timetables
    # from being shown mixed together on this page / in any PDF built
    # from it.
    courses = apply_tt_scope(
        CourseAllocation.objects.select_related("course").all(),
        request=request,
        prefix="allocation_set",
    )
    timetable = apply_tt_scope(
        Timetable.objects.select_related("course_allocation").all(),
        request=request,
        prefix="course_allocation__allocation_set",
    )
    lab_timetable = apply_tt_scope(
        LabTimetable.objects.select_related("lab_allocation", "lab_venue").all(),
        request=request,
        prefix="lab_allocation__allocation_set",
    )

    return render(request, "timetable/main_timetable.html", {
        "days": days,
        "timeslots": timeslots,
        "lab_timeslots": lab_timeslots,
        "venues": venues,
        "lab_venues": lab_venues,
        "courses": courses,
        "timetable": timetable,
        "lab_timetable": lab_timetable,
    })

