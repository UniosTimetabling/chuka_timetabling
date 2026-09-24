# Run with: python manage.py shell < diagnose_econ306_math301.py
# (or paste the body into `python manage.py shell`)
#
# Finds the actual CourseAllocation row(s) behind the ECON 306 vs MATH 301
# false collision reported for Bachelor of Arts Year 3 (08:30 2026-12-16),
# and prints every field exam_panel_is_exempt() / exam_is_collision_exempt()
# consult — including the REAL M2M stem membership (specialization_stems),
# not just the deprecated singular pointer — plus which ExamTimetable row(s)
# actually exist for each, so we can see:
#   1. whether there are duplicate CourseAllocation rows for either code
#      (a known recurring data issue in this DB — see memory notes on
#      duplicate CourseAllocation rows from repeated bulk imports), and
#      whether the ExamTimetable entry points at a row with EMPTY stem
#      membership while a different duplicate row has the correct one;
#   2. whether the two courses' stems sit under the SAME SpecializationCategory
#      (exempt by current design) or different categories (NOT auto-exempt
#      by current design — see SpecializationCategory's docstring, which
#      defines the category as the actual choice-point/exclusivity boundary,
#      not the stem alone).

from course_allocation.models import CourseAllocation
from timetable.models import ExamTimetable

CODES = ["ECON 306", "MATH 301"]
PROGRAM_FILTER = "Bachelor of Arts"


def describe(alloc):
    print(f"  id={alloc.id}  code={alloc.course_code!r}  name={alloc.course_name!r}")
    prog = alloc.program
    print(f"    program={getattr(prog, 'name', None)!r} (id={getattr(prog, 'id', None)})")
    pc = getattr(alloc, "program_course", None)
    print(f"    program_course_id={getattr(alloc, 'program_course_id', None)} "
          f"year={getattr(pc, 'year', None)} semester={getattr(pc, 'semester', None)}")
    print(f"    allocation_set_id={getattr(alloc, 'allocation_set_id', None)} "
          f"is_elective={getattr(alloc, 'is_elective', None)}")
    print(f"    student_group_id={getattr(alloc, 'student_group_id', None)} "
          f"selection_group_id={getattr(alloc, 'selection_group_id', None)} "
          f"special_intake_group_id={getattr(alloc, 'special_intake_group_id', None)}")

    # DEPRECATED singular pointer — shown only for comparison against the
    # real M2M membership below; a mismatch (pointer=None, M2M non-empty)
    # is exactly the cross-listing pattern that caused the CHEM314/PHYS392
    # bug earlier.
    single = getattr(alloc, "specialization_stem", None)
    print(f"    [deprecated] specialization_stem pointer: "
          f"{single.name if single else None} "
          f"(id={getattr(single, 'id', None)}, category={getattr(single, 'category_id', None)})")

    stems = list(alloc.specialization_stems.select_related("category").all())
    if stems:
        print(f"    [REAL] specialization_stems M2M membership ({len(stems)}):")
        for st in stems:
            print(f"      - stem={st.name!r} (id={st.id}) "
                  f"category={st.category.name!r} (category_id={st.category_id}, "
                  f"program={st.category.program_id}, year={st.category.year}, "
                  f"semester={st.category.semester})")
            other_courses = list(
                st.courses.exclude(id=alloc.id).values_list("course_code", flat=True)
            )
            print(f"        other courses in this stem: {other_courses}")
    else:
        print("    [REAL] specialization_stems M2M membership: EMPTY "
              "(this allocation is not linked to ANY stem — it will fall "
              "through the stem-exemption rule entirely)")

    exam_rows = list(
        ExamTimetable.objects.filter(course_allocation_id=alloc.id)
        .values("id", "date", "start_time", "venue_id")
    )
    print(f"    ExamTimetable rows pointing at THIS allocation id: {exam_rows}")
    print()


print("=" * 70)
for code in CODES:
    qs = CourseAllocation.objects.filter(
        program__name__icontains=PROGRAM_FILTER,
        course_code__icontains=code,
    ).select_related("program", "program_course", "specialization_stem")
    rows = list(qs)
    print(f"'{code}' under {PROGRAM_FILTER}: {len(rows)} CourseAllocation row(s) found")
    if len(rows) > 1:
        print("  *** MULTIPLE rows for this code — possible duplicate-allocation "
              "issue (different rows can have different stem membership) ***")
    for r in rows:
        describe(r)
print("=" * 70)

# Live-check the actual exemption functions against every found pair, so we
# see the real verdict (and, for the autoscheduler version, WHY) without
# guessing from the rules alone.
try:
    from timetable.exam_timetable_panel import exam_panel_is_exempt, get_program_year_key
    from timetable.algorithms.exam_timetable_autosheduler_algorith import (
        exam_is_collision_exempt, _explain_collision_reason,
    )

    all_rows = list(
        CourseAllocation.objects.filter(
            program__name__icontains=PROGRAM_FILTER,
            course_code__icontains="ECON 306",
        )
    ) + list(
        CourseAllocation.objects.filter(
            program__name__icontains=PROGRAM_FILTER,
            course_code__icontains="MATH 301",
        )
    )
    econ_rows = [r for r in all_rows if "ECON" in r.course_code.upper()]
    math_rows = [r for r in all_rows if "MATH" in r.course_code.upper()]
    for e in econ_rows:
        for m in math_rows:
            print(f"\nPair: {e.course_code} (id={e.id})  vs  {m.course_code} (id={m.id})")
            print(f"  program_year_key: {get_program_year_key(e)!r} vs {get_program_year_key(m)!r}")
            print(f"  exam_panel_is_exempt -> {exam_panel_is_exempt(e, m)}")
            print(f"  exam_is_collision_exempt (autoscheduler) -> {exam_is_collision_exempt(e, m)}")
            print(f"  _explain_collision_reason -> {_explain_collision_reason(e, m)}")
except Exception as exc:
    print(f"(live exemption check skipped: {exc})")
