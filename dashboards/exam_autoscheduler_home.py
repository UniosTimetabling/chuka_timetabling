from django.shortcuts import render
import datetime
from timetable.models import (
    ExamSchedulerConfig,
)

# ============================================================
# Helper Functions
# ============================================================

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


# ============================================================
# CONFIGURATION VIEWS
# ============================================================
def exam_autoscheduler_home(request):
    """
    Render the Exam AutoScheduler configuration and timetable view.

    This view intentionally loads only lightweight config data on the initial
    page render. Heavy data (temp timetable entries, merged groups, shared
    venue groups, venue lists) is fetched asynchronously by the frontend via
    dedicated API endpoints after the page loads. This keeps the initial
    response fast even when thousands of exam entries exist.

    Context variables passed to the template:
        config          - ExamSchedulerConfig instance (created with defaults
                          if none exists yet).
        slots           - List of (start, end) datetime.time tuples for display
                          in the config strip.
        tableslots      - JSON-serialisable list of ["HH:MM","HH:MM"] pairs,
                          consumed by the frontend timetable grid renderer.
        date_range      - Full list of (date_str, weekday_name) pairs from config.
        excluded_list   - List of date strings currently marked as excluded.
        days_with_names - List of (date_str, weekday_name) for dates inside the
                          exam window, used by the frontend day-name map.
        navbar_links    - Dict of {label: url_name} for action bar buttons.
    """
    config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)

    # Generate slots once; reuse for both display and the JSON grid.
    slots = generate_slots(config.start_time, config.end_time, config.slot_size)

    # FIX: Ensure we generate a *full chronological window* of dates here so that
    # excluded days are not hidden from the modal view array.
    date_range = []
    if config.start_date and config.max_exam_days:
        base_date = config.start_date
        # Generate the absolute max window so excluded days remain trackable and unselectable
        for i in range(config.max_exam_days + len(config.excluded_date_list()) + 5):
            current_date = base_date + datetime.timedelta(days=i)
            date_str = current_date.strftime("%Y-%m-%d")
            day_name = current_date.strftime("%A")
            date_range.append((date_str, day_name))
    else:
        date_range = config.get_date_range()

    excluded_list = config.excluded_date_list()

    existing_dates = [d[0] for d in date_range]
    date_day_map = {
        d: datetime.datetime.strptime(d, "%Y-%m-%d").strftime("%A")
        for d in existing_dates
    }
    days_with_names = [(d, date_day_map[d]) for d in existing_dates]

    # Convert time objects to plain strings for JSON serialisation in the template
    tableslots = [
        [s.strftime("%H:%M"), e.strftime("%H:%M")]
        for s, e in slots
    ]

    return render(request, "dashboard/exam_autosheduler.html", {
        "config": config,
        "slots": slots,
        "tableslots": tableslots,
        "date_range": date_range,
        "excluded_list": excluded_list,
        "days_with_names": days_with_names,
        "navbar_links": {
            "Go to Schedule main timetable": "autoscheduler_home",
            "Publish to Exam": "publish_to_exam_main",
        },
    })