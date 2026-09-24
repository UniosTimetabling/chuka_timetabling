from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone
from datetime import datetime, timedelta
from collections import defaultdict

from timetable.models import ExamTimetable, ExamSchedulerConfig
from department_management.models import Department


@login_required
def cot_exam_timetable(request):
    """
    Render the COT exam timetable grouped by:
      Department → Date → Grid (timeslots × venues)
    """
    departments = Department.objects.order_by("name")
    exam_entries = (
        ExamTimetable.objects
        .select_related("course_allocation__department", "venue")
        .order_by("date", "start_time")
    )

    # ── Build configured slots ───────────────────────────────────────────────
    config = ExamSchedulerConfig.objects.first()
    all_slots = []
    if config:
        start = datetime.combine(timezone.now().date(), config.start_time)
        end   = datetime.combine(timezone.now().date(), config.end_time)
        delta = timedelta(hours=config.slot_size)
        while start < end:
            slot_end = start + delta
            all_slots.append(
                f"{start.strftime('%H:%M')} - {slot_end.strftime('%H:%M')}"
            )
            start = slot_end

    # ── Group entries: dept → date → slot → venue → [entries] ───────────────
    # Structure: dept_data[dept_id] = {
    #   "dept":   Department,
    #   "dates":  { date_str: { "slots": [str], "venues": [str], "grid": {slot: {venue: [entries]}} } }
    # }
    dept_data = {}

    for entry in exam_entries:
        dept     = entry.course_allocation.department
        date_str = entry.date.strftime("%Y-%m-%d")
        slot_str = f"{entry.start_time.strftime('%H:%M')} - {entry.end_time.strftime('%H:%M')}"
        venue_str = entry.venue.code if entry.venue else "—"
        course   = entry.course_allocation.course_code
        course_name = entry.course_allocation.course_name

        if dept.id not in dept_data:
            dept_data[dept.id] = {"dept": dept, "dates": {}}

        dates = dept_data[dept.id]["dates"]
        if date_str not in dates:
            dates[date_str] = {"slots": [], "venues": [], "grid": defaultdict(dict)}

        date_block = dates[date_str]
        if slot_str not in date_block["slots"]:
            date_block["slots"].append(slot_str)
        if venue_str not in date_block["venues"]:
            date_block["venues"].append(venue_str)

        cell = date_block["grid"][slot_str]
        if venue_str not in cell:
            cell[venue_str] = []
        cell[venue_str].append({"course": course, "name": course_name, "id": entry.id})

    # ── Convert defaultdict → plain dict; sort slots/venues; sort dates ──────
    for dept_id, dblock in dept_data.items():
        sorted_dates = {}
        for date_str in sorted(dblock["dates"].keys()):
            db = dblock["dates"][date_str]
            db["slots"]   = sorted(db["slots"])
            db["venues"]  = sorted(db["venues"])
            db["grid"]    = {s: dict(v) for s, v in db["grid"].items()}
            # human-friendly date label
            d = datetime.strptime(date_str, "%Y-%m-%d")
            db["label"] = d.strftime("%A, %d %B %Y")
            sorted_dates[date_str] = db
        dblock["dates"] = sorted_dates

    # Order department list by name, keep only depts that have entries + all depts for nav
    dept_list = [dept_data[d.id] for d in departments if d.id in dept_data]

    return render(request, "dashboard/cot_exam_timetable.html", {
        "departments":  departments,
        "dept_list":    dept_list,
        "slots":        all_slots,
        "exam_entries": exam_entries,
    })
