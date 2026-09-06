# ==============================================================================
# inspect_lecturer_course_data.py
#
# Diagnostic script to inspect REAL data in the DB:
#   - CourseAllocation -> ProgramCourse -> Program mapping
#   - How many courses each lecturer actually has per semester
#   - Whether that matches "real world" (5-6 courses/lecturer/semester)
#     or the thin test data (2-3 courses/lecturer/semester)
#
# USAGE (run from project root, same folder as manage.py):
#
#   python manage.py shell < inspect_lecturer_course_data.py
#
#   OR paste interactively:
#   python manage.py shell
#   >>> exec(open("inspect_lecturer_course_data.py").read())
#
#   OR non-interactive, save output to a file:
#   python manage.py shell < inspect_lecturer_course_data.py > lecturer_course_report.txt
# ==============================================================================

from collections import defaultdict
from django.db.models import Count, Q

from course_allocation.models import CourseAllocation, LecturerCourseMapping
from program_management.models import Program, ProgramCourse
from lecturer_portal.models import Lecturer
from department_management.models import Department

W = 100
def line(char="="):
    print(char * W)

def h1(title):
    print()
    line("=")
    print(title)
    line("=")

def h2(title):
    print()
    line("-")
    print(title)
    line("-")


# ------------------------------------------------------------------------------
# 0. HIGH-LEVEL COUNTS
# ------------------------------------------------------------------------------
h1("0. HIGH-LEVEL COUNTS")
print(f"Departments        : {Department.objects.count()}")
print(f"Programs           : {Program.objects.count()}")
print(f"ProgramCourses     : {ProgramCourse.objects.count()}  (curriculum entries, has year+semester)")
print(f"Lecturers          : {Lecturer.objects.count()}")
print(f"CourseAllocations  : {CourseAllocation.objects.count()}  (actual assignments used by scheduler)")
print(f"  - with lecturer set     : {CourseAllocation.objects.exclude(lecturer__isnull=True).count()}")
print(f"  - without lecturer      : {CourseAllocation.objects.filter(lecturer__isnull=True).count()}")
print(f"  - submitted_to_tt=True  : {CourseAllocation.objects.filter(submitted_to_tt=True).count()}")
print(f"  - approved_by_dvc=True  : {CourseAllocation.objects.filter(approved_by_dvc=True).count()}")
print(f"LecturerCourseMappings   : {LecturerCourseMapping.objects.count()}  (qualification table, NOT actual teaching load)")


# ------------------------------------------------------------------------------
# 1. SEMESTER SOURCE OF TRUTH
# CourseAllocation has no "semester" field of its own -- it comes from
# program_course.semester. Confirm this and check for mismatches/nulls.
# ------------------------------------------------------------------------------
h1("1. SEMESTER MAPPING (CourseAllocation -> program_course.semester)")

no_program_course = CourseAllocation.objects.filter(program_course__isnull=True).count()
print(f"CourseAllocations with NO program_course link: {no_program_course}  "
      f"(these have no derivable semester at all)")

sem_counts = (
    CourseAllocation.objects
    .exclude(program_course__isnull=True)
    .values("program_course__semester")
    .annotate(n=Count("id"))
    .order_by("program_course__semester")
)
print("\nCourseAllocation count by program_course.semester:")
for row in sem_counts:
    print(f"  Semester {row['program_course__semester']}: {row['n']} allocations")

year_counts = (
    CourseAllocation.objects
    .exclude(program_course__isnull=True)
    .values("program_course__year")
    .annotate(n=Count("id"))
    .order_by("program_course__year")
)
print("\nCourseAllocation count by program_course.year:")
for row in year_counts:
    print(f"  Year {row['program_course__year']}: {row['n']} allocations")


# ------------------------------------------------------------------------------
# 2. LECTURER LOAD PER SEMESTER (the actual complaint)
# For each lecturer, how many CourseAllocation rows do they have,
# broken down by (year, semester) from program_course.
# This is the REAL teaching load, not the qualification mapping.
# ------------------------------------------------------------------------------
h1("2. LECTURER TEACHING LOAD PER (YEAR, SEMESTER) -- via CourseAllocation")

load_qs = (
    CourseAllocation.objects
    .exclude(lecturer__isnull=True)
    .exclude(program_course__isnull=True)
    .values(
        "lecturer_id",
        "lecturer__name",
        "lecturer__designation",
        "program_course__year",
        "program_course__semester",
    )
    .annotate(n=Count("id"))
    .order_by("lecturer__name", "program_course__year", "program_course__semester")
)

by_lecturer_sem = defaultdict(list)
for row in load_qs:
    key = (row["lecturer_id"], row["lecturer__name"], row["lecturer__designation"])
    by_lecturer_sem[key].append(
        (row["program_course__year"], row["program_course__semester"], row["n"])
    )

print(f"{'Lecturer':35} {'Year':>5} {'Sem':>5} {'#Courses':>10}")
line("-")
for (lec_id, name, desig), rows in by_lecturer_sem.items():
    label = f"{desig or ''} {name}".strip()
    for (yr, sem, n) in rows:
        print(f"{label:35} {yr!s:>5} {sem!s:>5} {n:>10}")

# Distribution summary: how many (lecturer, year, semester) combos have
# 1, 2, 3, 4, 5, 6+ courses -- this tells you the shape of your test data.
h2("2b. DISTRIBUTION: how many lecturer-semester slots have N courses")
bucket = defaultdict(int)
for (lec_id, name, desig), rows in by_lecturer_sem.items():
    for (yr, sem, n) in rows:
        bucket[n] += 1

for n in sorted(bucket):
    print(f"  {n} course(s) in that semester: {bucket[n]} lecturer-semester slot(s)")

if bucket:
    max_n = max(bucket)
    if max_n <= 3:
        print("\n  >>> CONFIRMED: no lecturer-semester slot exceeds 3 courses.")
        print("  >>> This matches your observation -- test data is too thin vs real world (5-6).")


# ------------------------------------------------------------------------------
# 3. LECTURER LOAD IGNORING SEMESTER (total distinct courses per lecturer,
#    across all semesters/years) -- in case allocations aren't being
#    split correctly by semester at all.
# ------------------------------------------------------------------------------
h1("3. TOTAL COURSE ALLOCATIONS PER LECTURER (ALL SEMESTERS COMBINED)")

total_per_lecturer = (
    CourseAllocation.objects
    .exclude(lecturer__isnull=True)
    .values("lecturer_id", "lecturer__name", "lecturer__designation")
    .annotate(n=Count("id"))
    .order_by("-n")
)
for row in total_per_lecturer:
    label = f"{row['lecturer__designation'] or ''} {row['lecturer__name']}".strip()
    print(f"  {label:35} {row['n']:>3} total allocation(s) across all semesters/years")


# ------------------------------------------------------------------------------
# 4. LECTURER_COURSE_MAPPING (qualification table) vs ACTUAL ALLOCATIONS
# This is likely the source of confusion: LecturerCourseMapping might have
# many ProgramCourses per lecturer, but CourseAllocation (actual teaching
# assignment) only uses 2-3 of them. Compare the two directly.
# ------------------------------------------------------------------------------
h1("4. QUALIFICATION MAPPING vs ACTUAL ALLOCATION (per lecturer)")

print(f"{'Lecturer':35} {'#Qualified (mapping)':>22} {'#Actually Allocated':>22}")
line("-")
for lcm in LecturerCourseMapping.objects.select_related("lecturer").prefetch_related("courses"):
    lec = lcm.lecturer
    qualified_count = lcm.courses.count()
    actual_count = CourseAllocation.objects.filter(lecturer=lec).count()
    label = f"{lec.designation or ''} {lec.name}".strip() if lec else "Unknown"
    print(f"{label:35} {qualified_count:>22} {actual_count:>22}")


# ------------------------------------------------------------------------------
# 5. PROGRAM -> PROGRAMCOURSE -> ALLOCATION COVERAGE
# For each program+year+semester, how many ProgramCourse entries exist
# vs how many actually have a CourseAllocation created for them.
# Helps you see if the curriculum (ProgramCourse) is richer than what
# ever gets allocated/tested.
# ------------------------------------------------------------------------------
h1("5. PROGRAM CURRICULUM COVERAGE (ProgramCourse vs CourseAllocation)")

print(f"{'Program':30} {'Yr':>3} {'Sem':>4} {'#ProgramCourses':>16} {'#Allocations':>13}")
line("-")
for pc_group in (
    ProgramCourse.objects
    .values("program_id", "program__name", "year", "semester")
    .annotate(n_courses=Count("id", distinct=True))
    .order_by("program__name", "year", "semester")
):
    program_id = pc_group["program_id"]
    yr = pc_group["year"]
    sem = pc_group["semester"]
    n_alloc = CourseAllocation.objects.filter(
        program_id=program_id,
        program_course__year=yr,
        program_course__semester=sem,
    ).count()
    print(f"{pc_group['program__name'][:30]:30} {yr:>3} {sem:>4} "
          f"{pc_group['n_courses']:>16} {n_alloc:>13}")


# ------------------------------------------------------------------------------
# 6. "OUT RUN" / submitted_to_tt CHECK
# Confirms which allocations have actually been pushed to the timetable
# vs sitting un-submitted -- since the scheduler/autoscheduler only ever
# "sees" submitted_to_tt=True rows, thin data there would explain low
# lecturer course counts in the generated timetable even if CourseAllocation
# itself has more rows.
# ------------------------------------------------------------------------------
h1("6. submitted_to_tt STATUS BREAKDOWN PER LECTURER")

print(f"{'Lecturer':35} {'Total':>8} {'Submitted':>10} {'Not submitted':>15}")
line("-")
for row in (
    CourseAllocation.objects
    .exclude(lecturer__isnull=True)
    .values("lecturer_id", "lecturer__name", "lecturer__designation")
    .annotate(
        total=Count("id"),
        submitted=Count("id", filter=Q(submitted_to_tt=True)),
    )
    .order_by("-total")
):
    label = f"{row['lecturer__designation'] or ''} {row['lecturer__name']}".strip()
    not_sub = row["total"] - row["submitted"]
    print(f"{label:35} {row['total']:>8} {row['submitted']:>10} {not_sub:>15}")


h1("DONE")
print("Review section 2b first -- it directly answers whether lecturer-semester")
print("course counts are capped too low (2-3) vs realistic (5-6+).")
print("Review section 4 to see if LecturerCourseMapping (qualifications) is being")
print("mistaken for actual teaching load, or if the seed/test-data generator")
print("only ever creates 2-3 CourseAllocation rows per lecturer per semester.")
