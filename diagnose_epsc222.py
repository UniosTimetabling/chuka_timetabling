"""
Run with: python manage.py shell < diagnose_epsc222.py
(or paste into `python manage.py shell`)

Purpose
-------
The timetable panel's conflict engine (timetable_panel.is_scheduling_exempt)
DOES already know about the StudentGroup model — Rule 2 exempts two
allocations from a program-year "conflict" when they have different,
EXPLICITLY-SET `student_group` foreign keys:

    sgrp_a = _student_group_id_of(alloc_a)
    sgrp_b = _student_group_id_of(alloc_b)
    if sgrp_a is not None and sgrp_b is not None and sgrp_a != sgrp_b:
        return True   # exempt — different cohorts, not a real clash

The exemption is keyed off the `student_group` FK on CourseAllocation, NOT
off any letter suffix in course_code (e.g. "-T" / "-W"). If EPSC 222-T and
EPSC 222-W are showing as a false conflict, the most likely cause is that
one or both of those CourseAllocation rows have student_group = NULL, or
they both point at the SAME StudentGroup row (so the "different group" test
never fires) — a data-linkage gap, not a missing feature.

This script prints exactly what the conflict engine sees for EPSC 222 so you
can tell which case you're in.
"""
from course_allocation.models import CourseAllocation, StudentGroup
from timetable.models import Timetable
from timetable.timetable_panel import (
    _student_group_id_of, _is_elective_alloc, _selection_group_id_of,
    _specialization_stem_id_of, _specialization_category_id_of,
    _intake_of, _get_year_value, is_scheduling_exempt,
)

COURSE_CODE_PREFIX = "EPSC 222"   # <-- change if needed

print(f"\n=== CourseAllocation rows matching '{COURSE_CODE_PREFIX}*' ===\n")

allocs = list(
    CourseAllocation.objects
    .filter(course_code__istartswith=COURSE_CODE_PREFIX)
    .select_related("program", "student_group", "specialization_stem",
                     "specialization_stem__category", "selection_group")
)

if not allocs:
    print(f"  No CourseAllocation rows found starting with {COURSE_CODE_PREFIX!r}.")
else:
    for a in allocs:
        grp = a.student_group.display_name if a.student_group_id else "— NOT SET —"
        tt = Timetable.objects.filter(course_allocation=a).select_related("venue").first()
        slot = f"{tt.day} {tt.start_time}-{tt.end_time} @ {tt.venue.code}" if tt else "not scheduled"
        print(f"  id={a.id:<6} code={a.course_code!r:<20} program={a.program}  year={_get_year_value(a)}")
        print(f"           student_group={grp}")
        print(f"           elective={a.is_elective}  selection_group={_selection_group_id_of(a)}  "
              f"stem={_specialization_stem_id_of(a)}  intake={_intake_of(a)}")
        print(f"           scheduled: {slot}")
        print()

print("=== Pairwise is_scheduling_exempt() verdicts ===\n")
for i in range(len(allocs)):
    for j in range(i + 1, len(allocs)):
        a, b = allocs[i], allocs[j]
        exempt = is_scheduling_exempt(a, b)
        verdict = "EXEMPT (no conflict)" if exempt else "NOT EXEMPT — will show as a conflict if same slot"
        print(f"  {a.course_code!r} vs {b.course_code!r}: {verdict}")
        if not exempt:
            sg_a, sg_b = a.student_group_id, b.student_group_id
            if sg_a is None or sg_b is None:
                print(f"      -> student_group missing on at least one side "
                      f"(a={sg_a}, b={sg_b}). Set both to their correct, DIFFERENT "
                      f"StudentGroup to fix this pair.")
            elif sg_a == sg_b:
                print(f"      -> both point at the SAME StudentGroup (id={sg_a}). "
                      f"If these are really different cohorts, one of them is "
                      f"tagged wrong.")
        print()

print("=== StudentGroup rows available for this program (sanity check) ===\n")
if allocs and allocs[0].program_id:
    groups = StudentGroup.objects.filter(program_id=allocs[0].program_id).order_by("year", "letter")
    for g in groups:
        print(f"  id={g.id:<5} {g.display_name} (letter={g.letter!r})")
    if not groups:
        print("  No StudentGroup rows exist for this program at all — "
              "they need to be created first (Student Groups admin/panel) "
              "before CourseAllocation rows can be tagged to one.")
