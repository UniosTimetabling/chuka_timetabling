# Run with: python manage.py shell < diagnose_ftc301_split.py
# (or paste the body into `python manage.py shell`)
#
# Checks why FTC 301's exam got split across multiple venues on
# Thursday's first slot instead of going into one room, by inspecting the
# same inputs place_merged_family() uses:
#   1. Every CourseAllocation for FTC 301 (all programs/sections), their
#      combined student count.
#   2. Any active VenueSpecialization ("designated venue") rule matching
#      FTC 301 — especially whether it's marked `strict`, since a strict
#      rule stops the scheduler from EVER trying a general (non-designated)
#      venue for this course, even one big enough to hold everyone alone.
#   3. Which venue(s) the exam actually ended up in (from ExamTimetable),
#      and each one's exam capacity.
#   4. Every other venue with enough free capacity, at that same date/slot,
#      to have held the whole course alone (cross-checked against what any
#      other exam already booked there needs, so we know it's a genuine
#      candidate and not just a capacity number).

import re
import django
from course_allocation.models import CourseAllocation
from timetable.models import ExamTimetable
from room_management.models import Venue, VenueSpecialization

CODE = "FTC 301"


def _norm(code):
    """Loose normalize: letters+digits only, uppercased — matches
    'FTC301', 'FTC 301', 'FTC-301', 'FTC 301-A', 'ftc301b' etc."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(code or "")).upper()
    return s


target_norm = _norm(CODE)  # "FTC301"

# Loose match: normalized course_code STARTS WITH the target (so lettered
# sections like "FTC 301-A" / "FTC301B" are included too), done in Python
# since the DB can't normalize on the fly.
allocs = [
    a for a in CourseAllocation.objects.select_related("program").iterator()
    if _norm(a.course_code).startswith(target_norm)
]
print(f"'{CODE}' (loose match): {len(allocs)} CourseAllocation row(s)")
if not allocs:
    # Fallback: show any course code that starts with the same letters,
    # in case the real code differs more than just spacing/lettering
    # (e.g. a different subject prefix or number).
    prefix = re.sub(r"[0-9].*$", "", target_norm)  # letters before the digits, e.g. "FTC"
    similar = sorted({
        a.course_code for a in CourseAllocation.objects.only("course_code").iterator()
        if _norm(a.course_code).startswith(prefix)
    })
    print(f"  No exact/loose match. Course codes starting with {prefix!r} in the DB: {similar[:30]}")

total_students = 0
for a in allocs:
    n = a.number_of_students or 0
    total_students += n
    print(f"  id={a.id} program={getattr(a.program, 'name', None)!r} "
          f"(program_id={a.program_id}) students={n}")
print(f"Combined total students across all sections: {total_students}\n")

print("Active VenueSpecialization rules referencing this code:")
found_rule = False
for rule in VenueSpecialization.objects.filter(is_active=True).prefetch_related(
    "venues", "programs__courses", "departments__programs__courses", "courses"
):
    codes = rule.get_designated_course_codes()
    if any(CODE.replace(" ", "").upper() in c.replace(" ", "").upper() or
           c.replace(" ", "").upper() in CODE.replace(" ", "").upper() for c in codes):
        found_rule = True
        venues = list(rule.venues.values_list("code", "capacity", "exam_capacity"))
        print(f"  rule id={rule.id} name={getattr(rule, 'name', None)!r} "
              f"strict={rule.strict} exclusive={rule.exclusive} priority={rule.priority}")
        print(f"    codes={sorted(codes)}")
        print(f"    venues={venues}")
        print(f"    programs={list(rule.programs.values_list('name', flat=True))}")
        print(f"    departments={list(rule.departments.values_list('name', flat=True))}")
if not found_rule:
    print("  (none found — FTC 301 is not under any designated-venue rule)")
print()

alloc_ids = [a.id for a in allocs]
exam_rows = list(
    ExamTimetable.objects.filter(course_allocation_id__in=alloc_ids)
    .select_related("venue", "course_allocation")
)
print(f"Existing ExamTimetable rows for {CODE}: {len(exam_rows)}")
seen_slot = None
for e in exam_rows:
    cap = e.venue.exam_capacity if e.venue.exam_capacity is not None else e.venue.capacity
    print(f"  {e.date} {e.start_time} venue={e.venue.code!r} "
          f"venue_exam_cap={cap} allocated_students={e.allocated_students} "
          f"course_allocation_id={e.course_allocation_id}")
    seen_slot = (e.date, e.start_time)

if seen_slot:
    date_, slot_start = seen_slot
    print(f"\nOther exams booked at {date_} {slot_start} (any course), by venue:")
    same_slot = (
        ExamTimetable.objects.filter(date=date_, start_time=slot_start)
        .select_related("venue", "course_allocation")
        .order_by("venue__code")
    )
    venue_usage = {}
    for e in same_slot:
        cap = e.venue.exam_capacity if e.venue.exam_capacity is not None else e.venue.capacity
        venue_usage.setdefault(e.venue.code, {"cap": cap, "used": 0, "courses": []})
        venue_usage[e.venue.code]["used"] += (e.allocated_students or 0)
        venue_usage[e.venue.code]["courses"].append(e.course_allocation.course_code)

    print(f"\nAll venues with enough SPARE capacity at {date_} {slot_start} "
          f"to have held all {total_students} FTC 301 students in ONE room:")
    for v in Venue.objects.all():
        cap = v.exam_capacity if v.exam_capacity is not None else v.capacity
        if not cap:
            continue
        used = venue_usage.get(v.code, {}).get("used", 0)
        remaining = cap - used
        if remaining >= total_students:
            occupant_note = (f" (already hosting {venue_usage[v.code]['courses']}, "
                              f"{used} used)" if v.code in venue_usage else " (empty)")
            print(f"  {v.code}: cap={cap} remaining={remaining}{occupant_note}")
