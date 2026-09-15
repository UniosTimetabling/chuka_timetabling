"""
Run with: python manage.py shell < diagnose_student_groups.py
(or paste into `python manage.py shell`)

Part 1: prints every StudentGroup for the given program/year/semester/intake
and which CourseAllocation rows are tagged to it.

Part 2: prints EVERY distinct course in that program/year/semester/intake
(shared or grouped) and explains, for each one, why it either joined the
groups or stayed shared — the exact same rule the app uses internally:
    excluded from grouping if is_elective=True  OR  specialization_stem is set.
"""
from course_allocation.models import StudentGroup, CourseAllocation

PROGRAM_NAME = "Bachelor of Commerce"   # <-- change if needed
YEAR = 1
SEMESTER = 1
INTAKE = "normal"

groups = (StudentGroup.objects
          .filter(program__name=PROGRAM_NAME, year=YEAR, semester=SEMESTER, intake=INTAKE)
          .order_by("letter"))

print(f"\n=== PART 1: {groups.count()} StudentGroup row(s) for {PROGRAM_NAME} Y{YEAR} S{SEMESTER} ({INTAKE}) ===\n")

for g in groups:
    allocs = CourseAllocation.objects.filter(student_group=g)
    print(f"  Group '{g.letter}' (id={g.id}, name={g.name!r}) — {allocs.count()} course(s):")
    for a in allocs:
        print(f"      {a.course_code:15s} stem={a.specialization_stem_id} elective={a.is_elective} sem={a.program_course.semester if a.program_course else '?'}")
    if not allocs.exists():
        print("      (none — this is why it won't show a subheading in the main table)")
    print()

print(f"=== PART 2: every distinct course for this program/year/semester/intake ===\n")

all_allocs = (CourseAllocation.objects
              .filter(program__name=PROGRAM_NAME, program_course__year=YEAR,
                       program_course__semester=SEMESTER, intake=INTAKE)
              .select_related("specialization_stem", "student_group")
              .order_by("program_course_id", "student_group_id"))

seen_courses = {}
for a in all_allocs:
    seen_courses.setdefault(a.program_course_id, []).append(a)

for pc_id, rows in seen_courses.items():
    first = rows[0]
    reasons = []
    if first.is_elective:
        reasons.append("is_elective=True")
    if first.specialization_stem_id:
        reasons.append(f"specialization_stem={first.specialization_stem.name!r}")
    verdict = "STAYS SHARED (" + ", ".join(reasons) + ")" if reasons else "groupable"
    print(f"  {first.course_code:15s} program_course_id={pc_id} -> {verdict}")
    if len(rows) > 1:
        for r in rows:
            grp = r.student_group.letter if r.student_group_id else "SHARED"
            print(f"      row id={r.id} course_code={r.course_code!r} group={grp}")
    print()

