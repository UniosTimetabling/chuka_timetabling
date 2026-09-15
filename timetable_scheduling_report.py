#!/usr/bin/env python
"""
timetable_scheduling_report.py
================================
Reports how much of the current course allocation is actually placed on
the (regular) timetable, breaks that down per Program / Program Year, and
compares the raw course-allocation load against the theoretical
scheduling capacity of the system (venues x timeslots x days).

WHAT IT COUNTS
--------------
1. SCHEDULED  vs UNSCHEDULED
   - "Scheduled"   = a CourseAllocation that has at least one row in
     timetable.Timetable (the final/published timetable — NOT the
     scratch TempTimetable used mid-run by the scheduler).
   - "Unscheduled" = a CourseAllocation with zero Timetable rows.
   A CourseAllocation with more than one Timetable row (e.g. it meets
   twice a week) is still counted once, under "scheduled".

2. PER DEPARTMENT / PROGRAM / YEAR BREAKDOWN
   Nested three-level breakdown: Department -> Program -> Program Year.
   Each level prints how many courses are in course allocation total,
   how many of those are scheduled, and how many are still unscheduled.
   Sorting is applied independently at every level (most course
   allocations first): departments are ordered by their total
   allocations, programs are ordered within their department by their
   total allocations, and years are ordered within their program by
   their total allocations. A program/department's total is the sum of
   its children.

3. SCHEDULABLE CAPACITY vs ACTUAL LOAD
   Computes the theoretical number of course slots the system can hold
   in one week:
       capacity = venues x (weekday slots + evening slots + weekend slots)
   using timetable.SchedulerConfig (falls back to the model's own
   defaults if no config row exists), and compares that number against
   the total CourseAllocation count, so you can see at a glance whether
   there is even enough room to schedule everything currently allocated.
   This is a rough per-week capacity ceiling (one venue-slot = one
   course meeting), not a guarantee every course only needs one slot —
   treat it as an upper bound check, not an exact prediction.

HOW TO RUN
----------
    python manage.py shell < timetable_scheduling_report.py

Or as a standalone script:
    python timetable_scheduling_report.py

Output is printed to stdout AND saved to timetable_scheduling_report.txt
in the current directory.
"""

import os
import django
from datetime import datetime
from collections import defaultdict

# ── Django setup (only needed when running as standalone) ─────────────────
if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE",
        "university_timetable_system.settings",
    )
    django.setup()

from django.db.models import Count, Q

from course_allocation.models import CourseAllocation
from timetable.models import Timetable, SchedulerConfig
from room_management.models import Venue
from timetable.algorithms.regular_timetable_autosheduler_algorithm import generate_slots

# ── Config ──────────────────────────────────────────────────────────────
OUTPUT_FILE = "timetable_scheduling_report.txt"
SEP    = "=" * 80
SUBSEP = "-" * 80

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


# ── Capacity computation ────────────────────────────────────────────────

def compute_capacity():
    """Return (capacity_details dict, total_slot_capacity int)."""
    config = SchedulerConfig.objects.first()

    if config is None:
        # Mirror the model's own field defaults so the report still means
        # something even before anyone has saved a SchedulerConfig row.
        regular_slots = generate_slots(
            __import__("datetime").time(7, 0), __import__("datetime").time(19, 0), 3
        )
        evening_enabled = False
        evening_slots = []
        weekend_enabled = False
        weekend_slots = []
        weekend_days = []
        config_note = "No SchedulerConfig row found in the database — using the model's built-in defaults (07:00-19:00, 3hr slots, evening/weekend disabled)."
    else:
        regular_slots = generate_slots(config.start_time, config.end_time, config.slot_size)
        evening_enabled = config.enable_evening_classes
        evening_slots = config.get_evening_slots() if evening_enabled else []
        weekend_enabled = config.enable_weekend_classes
        weekend_slots = config.get_weekend_slots() if weekend_enabled else []
        weekend_days = config.get_weekend_days() if weekend_enabled else []
        config_note = None

    venues = Venue.objects.count()

    weekday_slot_count = len(regular_slots)
    evening_slot_count = len(evening_slots)
    weekend_slot_count = len(weekend_slots)

    weekday_capacity = venues * weekday_slot_count * len(WEEKDAYS)
    evening_capacity = venues * evening_slot_count * len(WEEKDAYS) if evening_enabled else 0
    weekend_capacity = venues * weekend_slot_count * len(weekend_days) if weekend_enabled else 0

    total_capacity = weekday_capacity + evening_capacity + weekend_capacity

    details = {
        "config_note": config_note,
        "venues": venues,
        "weekdays": len(WEEKDAYS),
        "weekday_slot_count": weekday_slot_count,
        "weekday_capacity": weekday_capacity,
        "evening_enabled": evening_enabled,
        "evening_slot_count": evening_slot_count,
        "evening_capacity": evening_capacity,
        "weekend_enabled": weekend_enabled,
        "weekend_days": len(weekend_days),
        "weekend_slot_count": weekend_slot_count,
        "weekend_capacity": weekend_capacity,
    }
    return details, total_capacity


# ── Scheduled / unscheduled + per program-year breakdown ───────────────

def build_report():
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    qs = (
        CourseAllocation.objects
        .select_related("program", "program__department", "program_course")
        .annotate(tt_count=Count("timetable_entries", distinct=True))
        .only(
            "id", "course_code",
            "program__id", "program__name",
            "program__department__id", "program__department__name",
            "program_course__year",
        )
    )

    total_allocations = qs.count()
    scheduled_qs = qs.filter(tt_count__gt=0)
    unscheduled_qs = qs.filter(tt_count=0)
    total_scheduled = scheduled_qs.count()
    total_unscheduled = unscheduled_qs.count()

    # Distinct scheduled Timetable rows (sessions), as opposed to distinct
    # courses — a course meeting twice a week produces 2 Timetable rows.
    total_timetable_rows = Timetable.objects.count()

    # ── Per Department -> Program -> Year breakdown ─────────────────────
    # Three-level nested structure. Counts are rolled up from year level
    # to program level to department level, so every level's "total"
    # reflects the sum of its children.
    def new_counts():
        return {"total": 0, "scheduled": 0, "unscheduled": 0}

    departments = defaultdict(lambda: {
        "counts": new_counts(),
        "programs": defaultdict(lambda: {
            "counts": new_counts(),
            "years": defaultdict(new_counts),
        }),
    })

    for alloc in qs:
        dept_name = (
            alloc.program.department.name
            if alloc.program and alloc.program_id and alloc.program.department_id
            else "— No Department —"
        )
        prog_name = alloc.program.name if alloc.program else "— No Program —"
        year = alloc.program_course.year if alloc.program_course_id and alloc.program_course else None
        year_key = year if year else 99
        year_label = f"Year {year}" if year else "Year ?"

        dept = departments[dept_name]
        prog = dept["programs"][prog_name]
        yr = prog["years"][(year_key, year_label)]

        is_scheduled = alloc.tt_count > 0
        for counts in (dept["counts"], prog["counts"], yr):
            counts["total"] += 1
            if is_scheduled:
                counts["scheduled"] += 1
            else:
                counts["unscheduled"] += 1

    # Sort departments by total allocations desc, then name
    sorted_departments = sorted(
        departments.items(),
        key=lambda item: (-item[1]["counts"]["total"], item[0]),
    )
    for dept_name, dept in sorted_departments:
        # Sort programs within a department the same way
        dept["sorted_programs"] = sorted(
            dept["programs"].items(),
            key=lambda item: (-item[1]["counts"]["total"], item[0]),
        )
        for prog_name, prog in dept["sorted_programs"]:
            # Sort years within a program: most allocations first
            prog["sorted_years"] = sorted(
                prog["years"].items(),
                key=lambda item: (-item[1]["total"], item[0][0]),
            )

    capacity_details, total_capacity = compute_capacity()

    # ── Build printable lines ────────────────────────────────────────────
    lines = []
    lines.append(SEP)
    lines.append("  TIMETABLE SCHEDULING COVERAGE + CAPACITY REPORT")
    lines.append(f"  Generated : {generated_at}")
    lines.append(SEP)
    lines.append("")

    lines.append("  1) SCHEDULED vs UNSCHEDULED (course allocation level)")
    lines.append(SUBSEP)
    lines.append(f"  Total course allocations         : {total_allocations}")
    lines.append(f"  Scheduled (>=1 Timetable row)     : {total_scheduled}")
    lines.append(f"  Unscheduled (0 Timetable rows)    : {total_unscheduled}")
    if total_allocations:
        pct = 100.0 * total_scheduled / total_allocations
        lines.append(f"  Scheduled coverage                : {pct:.1f}%")
    lines.append(f"  Total Timetable rows (sessions)   : {total_timetable_rows}"
                  f"  (>1 per course if it meets more than once a week)")
    lines.append("")

    lines.append("  2) PER DEPARTMENT / PROGRAM / YEAR BREAKDOWN  (most allocations first at each level)")
    lines.append(SUBSEP)
    lines.append(f"  {'':<4}{'Name':<58}{'TOT':>6}{'SCHED':>8}{'UNSCHED':>9}")
    lines.append("  " + "-" * 92)

    for d_rank, (dept_name, dept) in enumerate(sorted_departments, start=1):
        dc = dept["counts"]
        dept_display = (dept_name[:55] + "...") if len(dept_name) > 58 else dept_name
        lines.append(
            f"  {d_rank:<4}{dept_display:<58}"
            f"{dc['total']:>6}{dc['scheduled']:>8}{dc['unscheduled']:>9}"
        )
        lines.append("      " + "." * 84)

        for p_rank, (prog_name, prog) in enumerate(dept["sorted_programs"], start=1):
            pc = prog["counts"]
            prog_label = f"{d_rank}.{p_rank} {prog_name}"
            prog_display = (prog_label[:56] + "...") if len(prog_label) > 58 else prog_label
            lines.append(
                f"      {prog_display:<56}"
                f"{pc['total']:>6}{pc['scheduled']:>8}{pc['unscheduled']:>9}"
            )

            for (_year_sort, year_label), yc in prog["sorted_years"]:
                lines.append(
                    f"          {year_label:<52}"
                    f"{yc['total']:>6}{yc['scheduled']:>8}{yc['unscheduled']:>9}"
                )
        lines.append("")

    lines.append("  3) SCHEDULABLE CAPACITY (venues x timeslots x days) vs ACTUAL LOAD")
    lines.append(SUBSEP)
    if capacity_details["config_note"]:
        lines.append(f"  NOTE: {capacity_details['config_note']}")
    lines.append(f"  Venues in system                  : {capacity_details['venues']}")
    lines.append(f"  Weekday slots/day x days           : {capacity_details['weekday_slot_count']} x {capacity_details['weekdays']}"
                  f"  →  weekday capacity: {capacity_details['weekday_capacity']}")
    if capacity_details["evening_enabled"]:
        lines.append(f"  Evening slots/day x days            : {capacity_details['evening_slot_count']} x {capacity_details['weekdays']}"
                      f"  →  evening capacity: {capacity_details['evening_capacity']}")
    else:
        lines.append("  Evening classes                    : disabled")
    if capacity_details["weekend_enabled"]:
        lines.append(f"  Weekend slots/day x days            : {capacity_details['weekend_slot_count']} x {capacity_details['weekend_days']}"
                      f"  →  weekend capacity: {capacity_details['weekend_capacity']}")
    else:
        lines.append("  Weekend classes                    : disabled")
    lines.append("")
    lines.append(f"  TOTAL schedulable capacity (1 venue-slot = 1 course meeting/week) : {total_capacity}")
    lines.append(f"  TOTAL course allocations needing a slot                           : {total_allocations}")
    if total_capacity:
        headroom = total_capacity - total_allocations
        pct_used = 100.0 * total_allocations / total_capacity
        if headroom >= 0:
            lines.append(f"  → Capacity covers demand, with {headroom} venue-slot(s) of headroom "
                          f"({pct_used:.1f}% of capacity used).")
        else:
            lines.append(f"  → DEMAND EXCEEDS CAPACITY by {-headroom} venue-slot(s) "
                          f"({pct_used:.1f}% of capacity — over 100% means it cannot all fit "
                          f"in a single week even in theory, e.g. more venues, days, or slots "
                          f"are needed, or some courses need to share sessions).")
    else:
        lines.append("  → Capacity is 0 (no venues and/or no timeslots configured) — cannot schedule anything.")
    lines.append("")

    lines.append(SEP)
    lines.append("  END OF REPORT")
    lines.append(SEP)

    return lines, {
        "total_allocations": total_allocations,
        "total_scheduled": total_scheduled,
        "total_unscheduled": total_unscheduled,
        "total_capacity": total_capacity,
    }


# ── Main ──────────────────────────────────────────────────────────────

def main():
    lines, summary = build_report()
    output_text = "\n".join(lines)

    print()
    print(output_text)
    print()

    with open(OUTPUT_FILE, "w") as f:
        f.write(output_text + "\n")

    print(f"[Report also saved to: {os.path.abspath(OUTPUT_FILE)}]")
    print(
        f"[Summary] allocations={summary['total_allocations']}  "
        f"scheduled={summary['total_scheduled']}  "
        f"unscheduled={summary['total_unscheduled']}  "
        f"capacity={summary['total_capacity']}"
    )


main()