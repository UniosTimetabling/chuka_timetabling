# Run with: python manage.py shell < diagnose_dibm_collision.py
# (or paste the body into `python manage.py shell`)
#
# WHY THIS EXISTS
# ----------------------------------------------------------------------
# The conflicts panel says:
#   "DIBM 0216/0211 is colliding with DIBM 0141 at Monday 11:30 AM —
#    same programme and year, with no stem or student group separating
#    them"
# That sentence is not a generic error message — it is the literal
# fallback branch of _explain_collision_reason() / exam_is_collision_exempt()
# in timetable/algorithms/exam_timetable_autosheduler_algorith.py. Every
# other rule (same stem, disjoint student_group, same selection_group,
# combined-group pairing, distinct special intakes) was checked and NONE
# of them fired, so the two rows fall through to "same program+year,
# nothing on either row proves the cohorts are different -> treat as one
# shared cohort". That is either:
#   (a) correct — they genuinely are the same students, so this is a real
#       clash the scheduler is right to keep apart, or
#   (b) a data gap — these SHOULD be different sub-cohorts (e.g. lettered
#       groups, an elective pool, a specialization stem) but nobody ever
#       tagged student_group / selection_group / specialization_stem on
#       these specific rows, so the system has no way to tell them apart
#       from a genuine shared unit.
# This script pulls every CourseAllocation row involved, prints every
# field the exemption logic actually reads, and re-runs the SAME
# exemption function the scheduler and panel use — so you can see exactly
# which rule is missing data instead of guessing.

import re
from collections import defaultdict

from course_allocation.models import CourseAllocation
from timetable.models import ExamTempTimetable
try:
    from timetable.models import ExamTimetable
except Exception:
    ExamTimetable = None

from timetable.algorithms.exam_timetable_autosheduler_algorith import (
    normalize_course_code,
    get_program_year,
    _exam_get_intake,
    _exam_get_specialization_stem_ids,
    _exam_get_specialization_category_ids,
    _exam_get_student_group_ids,
    _exam_get_selection_group_id,
    _combined_group_are_paired,
    _build_combined_group_cache,
    exam_is_collision_exempt,
    _explain_collision_reason,
    courses_share_students,
)

# ----------------------------------------------------------------------
# 1. CONFIGURE THE TARGETS
# ----------------------------------------------------------------------
PROGRAM_NAME_HINT = "Diploma in Business Management"
TARGET_YEAR = 1
# "DIBM 0216/0211" as shown by the panel is almost certainly two rows
# (0216 and 0211) whose merged label the panel is displaying together
# (like a shared_unit family display), so we chase all three codes.
TARGET_CODES = ["DIBM 0216", "DIBM 0211", "DIBM 0141"]


def _norm(code):
    return re.sub(r"[^A-Za-z0-9]", "", str(code or "")).upper()


def _year_matches(course, target_year):
    """get_program_year() returns 'year_1', 'year_2', ... (or 'unknown') —
    NOT a bare int/str digit or 'Year 1'. The first version of this script
    compared against the wrong shapes and silently excluded every real
    row. Compare against the actual format here."""
    return get_program_year(course) == f"year_{target_year}"


target_norms = {_norm(c) for c in TARGET_CODES}

print("=" * 100)
print(f"Looking for CourseAllocation rows in '{PROGRAM_NAME_HINT}' Year {TARGET_YEAR}")
print(f"matching course codes: {TARGET_CODES}")
print("=" * 100)

candidates = list(
    CourseAllocation.objects.filter(program__name__icontains=PROGRAM_NAME_HINT)
    .select_related(
        "program", "program_course", "lecturer", "selection_group",
        "specialization_stem", "specialization_stem__category",
        "student_group", "allocation_set", "department",
    )
    .prefetch_related("specialization_stems", "specialization_stems__category",
                       "additional_student_groups")
)

rows = [
    c for c in candidates
    if _year_matches(c, TARGET_YEAR)
    and _norm(c.course_code) in target_norms
]

if not rows:
    print("\nNo matching rows found with an exact code match. Falling back to a "
          "loose 'starts with'/'contains' match — this also catches a single "
          "row whose course_code is literally stored WITH the slash, e.g. "
          "'DIBM 0216/0211' as one field value, which normalizes to "
          "'DIBM0216211' and won't exact-match either target code alone...\n")
    rows = [
        c for c in candidates
        if _year_matches(c, TARGET_YEAR)
        and any(tn in _norm(c.course_code) for tn in target_norms)
    ]

if not rows:
    print("STILL nothing found within this program/year filter. Searching the "
          "ENTIRE CourseAllocation table (any program, any year) for anything "
          "containing '0216', '0211' or '0141', so we can see exactly where "
          "these rows actually live and why the program/year filter missed "
          "them:\n")
    loose = [
        c for c in CourseAllocation.objects.select_related("program", "program_course")
        if any(part in _norm(c.course_code) for part in ("0216", "0211", "0141"))
    ]
    for c in loose:
        print(f"  id={c.id}  code={c.course_code!r}  "
              f"program={getattr(c.program,'name',None)!r}  "
              f"year={get_program_year(c)}  "
              f"program_course.year={getattr(c.program_course,'year',None)!r}")
    raise SystemExit(0)

print(f"\nFound {len(rows)} matching row(s) (out of {len(TARGET_CODES)} target codes).\n")

# Report per-target-code hit/miss BEFORE moving on — the earlier version of
# this script silently swallowed this: if ANY code matched, it skipped the
# "nothing found" fallback entirely and never told you the OTHER codes were
# missing. That's exactly what happened with DIBM 0141 (found) vs DIBM 0216
# / DIBM 0211 (not found) in the previous run.
found_norms = {_norm(c.course_code) for c in rows}
missing_codes = [c for c in TARGET_CODES if not any(
    _norm(c) == fn or _norm(c) in fn or fn in _norm(c) for fn in found_norms
)]
if missing_codes:
    print(f"*** {len(missing_codes)} target code(s) were NOT found in "
          f"'{PROGRAM_NAME_HINT}' Year {TARGET_YEAR}: {missing_codes}")
    print("    Searching the ENTIRE CourseAllocation table (any program, any "
          "year) for these, to see where they actually live:\n")
    missing_norms = {_norm(c) for c in missing_codes}
    everywhere = [
        c for c in CourseAllocation.objects.select_related(
            "program", "program_course", "lecturer", "selection_group",
            "specialization_stem", "specialization_stem__category",
            "student_group", "allocation_set", "department",
        ).prefetch_related("specialization_stems", "specialization_stems__category",
                            "additional_student_groups")
        if any(mn in _norm(c.course_code) or _norm(c.course_code) in mn for mn in missing_norms)
    ]
    if everywhere:
        for c in everywhere:
            print(f"      id={c.id}  code={c.course_code!r}  "
                  f"program={getattr(c.program,'name',None)!r}  "
                  f"year={get_program_year(c)}  "
                  f"program_course.year={getattr(c.program_course,'year',None)!r}")
        # Fold these back into `rows` so the field dump / pairwise verdicts /
        # placement check below actually cover them too, instead of only
        # ever analysing whichever code happened to exact-match on the first
        # try. (This is exactly what went wrong last run: DIBM 0216/0211
        # WAS found here, but never made it into the analysis below.)
        existing_ids = {c.id for c in rows}
        added = [c for c in everywhere if c.id not in existing_ids]
        if added:
            print(f"\n      -> adding {len(added)} newly-found row(s) into the "
                  f"analysis below: {[c.course_code for c in added]}")
            rows.extend(added)
    else:
        print(f"      Found NOWHERE in the entire CourseAllocation table. "
              f"Either the panel's label 'DIBM 0216/0211' doesn't map to a "
              f"literal course_code at all (e.g. it may be assembled from "
              f"two curriculum entries, or the row was deleted/renamed after "
              f"the conflict was recorded), or the code is stored with "
              f"different digits/spacing than shown. Worth pasting the exact "
              f"label back into the COD panel's course search to confirm it "
              f"still resolves to a real row.")
    print()


# ----------------------------------------------------------------------
# 2. FULL FIELD DUMP — everything the exemption logic reads
# ----------------------------------------------------------------------
_build_combined_group_cache()  # needed for _combined_group_are_paired to work

def describe(c):
    stems = sorted(_exam_get_specialization_stem_ids(c))
    cats = sorted(_exam_get_specialization_category_ids(c))
    stg = sorted(_exam_get_student_group_ids(c))
    sel = _exam_get_selection_group_id(c)
    intake = _exam_get_intake(c)
    extra_groups = list(c.additional_student_groups.values_list("id", flat=True))
    print(f"--- CourseAllocation id={c.id} " + "-" * 60)
    print(f"  course_code            : {c.course_code!r}")
    print(f"  course_name            : {c.course_name!r}")
    print(f"  program / year         : {c.program.name!r} / {get_program_year(c)}")
    print(f"  allocation_set         : {getattr(c.allocation_set, 'name', None)} (id={c.allocation_set_id})")
    print(f"  department             : {getattr(c.department, 'name', None)}")
    print(f"  intake                 : {intake!r}  (special_intake_group_id={c.special_intake_group_id})")
    print(f"  is_elective            : {c.is_elective}")
    print(f"  section_number         : {c.section_number!r}  (is_section={c.is_section})")
    print(f"  number_of_students     : {c.number_of_students}")
    print(f"  student_group (primary): id={c.student_group_id} name={getattr(c.student_group,'name',None)}")
    print(f"  additional_student_grps: {extra_groups}")
    print(f"  -> effective student_group_ids used by exemption logic: {stg}")
    print(f"  selection_group        : id={sel} name={getattr(c.selection_group,'name',None)}")
    print(f"  specialization_stem(FK): id={c.specialization_stem_id} "
          f"name={getattr(c.specialization_stem,'name',None)}  "
          f"(NOTE: this singular pointer is cleared to NULL if the row "
          f"belongs to >1 stem — see specialization_stems M2M below)")
    print(f"  specialization_stems(M2M membership actually used): {stems}")
    print(f"  -> their categories: {cats}")
    print()
    return c

for c in rows:
    describe(c)

# ----------------------------------------------------------------------
# 3. PAIRWISE VERDICT — the exact same call the scheduler/panel make
# ----------------------------------------------------------------------
print("=" * 100)
print("PAIRWISE COLLISION VERDICTS (exam_is_collision_exempt / courses_share_students)")
print("=" * 100)
for i in range(len(rows)):
    for j in range(i + 1, len(rows)):
        a, b = rows[i], rows[j]
        exempt = exam_is_collision_exempt(a, b)
        shares = courses_share_students(a, b)
        reason = _explain_collision_reason(a, b)
        print(f"\n[{a.course_code} (id={a.id})]  vs  [{b.course_code} (id={b.id})]")
        print(f"  exam_is_collision_exempt -> {exempt}")
        print(f"  courses_share_students   -> {shares}")
        print(f"  reason: {reason}")

# ----------------------------------------------------------------------
# 4. WHAT ACTUALLY GOT SCHEDULED on the reported dates
# ----------------------------------------------------------------------
print("\n" + "=" * 100)
print("ACTUAL PLACEMENTS on the reported dates")
print("=" * 100)
row_ids = [c.id for c in rows]
for label, qs in [
    ("ExamTempTimetable (draft)", ExamTempTimetable.objects.filter(course_allocation_id__in=row_ids)),
] + ([("ExamTimetable (published)", ExamTimetable.objects.filter(course_allocation_id__in=row_ids))]
     if ExamTimetable is not None else []):
    print(f"\n--- {label} ---")
    entries = list(qs.select_related("venue", "course_allocation").order_by("date", "start_time"))
    if not entries:
        print("  (no rows)")
    for e in entries:
        print(f"  {e.date} {e.start_time}  {e.course_allocation.course_code:15s}  "
              f"venue={getattr(e.venue,'code',None)}  seats={e.allocated_students}")

# ----------------------------------------------------------------------
# 5. SANITY CHECK — is this really the only untagged pair in this
#    program/year, or does the whole cohort look like this (which would
#    point at a systemic data-entry gap rather than 2-3 stray rows)?
# ----------------------------------------------------------------------
print("\n" + "=" * 100)
print(f"SIBLING CHECK — every OTHER course in {PROGRAM_NAME_HINT} Year {TARGET_YEAR}, "
      f"and whether it carries ANY separating tag")
print("=" * 100)
siblings = [c for c in candidates if _year_matches(c, TARGET_YEAR)
            and c.id not in row_ids]
untagged, tagged = [], []
for c in siblings:
    has_tag = bool(_exam_get_specialization_stem_ids(c) or _exam_get_student_group_ids(c)
                    or _exam_get_selection_group_id(c) is not None)
    (tagged if has_tag else untagged).append(c)
print(f"  {len(tagged)} sibling row(s) carry a stem/student_group/selection_group tag")
print(f"  {len(untagged)} sibling row(s) carry NONE of those (same situation as the colliding pair)")
if untagged:
    print("  -> untagged siblings:")
    for c in untagged[:20]:
        print(f"       {c.course_code}  (id={c.id})")

print("\n" + "=" * 100)
print("HOW TO READ THIS")
print("=" * 100)
print("""
If DIBM 0216, DIBM 0211 and DIBM 0141 all have student_group_id=None,
no additional_student_groups, no specialization_stem membership, and no
selection_group — and the SIBLING CHECK above shows most/all of the rest
of that program/year is the same — this is most likely a genuine data
gap, not an algorithm bug: per CourseAllocation.student_group's own
help_text, a NULL student_group means "shared across all groups in the
program/year", so the exemption logic has no choice but to treat every
untagged course in that year as one shared cohort and flag them as
colliding.

Two ways to actually fix it (pick whichever matches campus reality):
  1. If these are genuinely three different electives/streams (e.g. DIBM
     0216/0211 is one elective pool and DIBM 0141 is a separate core
     unit taken by a different sub-group), tag the DISTINGUISHING rows
     with the correct student_group / selection_group / specialization_stem
     via the COD panel. Once tagged, exam_is_collision_exempt will exempt
     them the same way it already does for every other tagged pair in
     this cohort.
  2. If they really are taken by the exact same students (a real clash),
     then the panel's flag is CORRECT — the timetable genuinely cannot
     place all three at the same 11:30 slot, and the fix is scheduling
     them at different times, not touching the exemption logic.

If instead the PAIRWISE VERDICTS above show one side has stems/groups
and the other doesn't (an inconsistent, half-tagged pair), that's the
narrower data flaw: whoever tagged one of the three rows never tagged
the others to match.
""")