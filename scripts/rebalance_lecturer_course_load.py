# ==============================================================================
# rebalance_lecturer_course_load.py
#
# GOAL (per your spec):
#   - Minimise the number of lecturers actually carrying load per department,
#     and add lecturers only where the existing pool is too small to cover
#     the workload without breaching the target.
#   - Each lecturer should end up with:
#         SEMESTER load : 5-7 courses
#         YEAR load     : 10-14 courses  (sum of both semesters)
#   - Courses are grouped by ORIGIN DEPARTMENT (origin_department, falling
#     back to department if origin_department is null) -- i.e. a lecturer
#     is drawn from the department that actually owns/originates the course,
#     not the department it was cross-allocated to.
#   - Within a department+semester, allocations are spread across DIFFERENT
#     PROGRAMS and DIFFERENT YEARS before being chunked to lecturers, so no
#     lecturer gets stuck teaching only one program/year repeatedly.
#
# SAFE BY DEFAULT: DRY_RUN = True below. It will PRINT the full rebalancing
# plan (per department/semester) without writing anything to the DB.
# Set DRY_RUN = False to actually apply the changes inside one transaction.
#
# USAGE:
#   python manage.py shell < rebalance_lecturer_course_load.py
# ==============================================================================

import itertools
from collections import defaultdict

from django.db import transaction
from django.db.models import Count

from course_allocation.models import CourseAllocation, LecturerCourseMapping
from program_management.models import ProgramCourse
from lecturer_portal.models import Lecturer
from department_management.models import Department

# ------------------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------------------
DRY_RUN               = True   # <-- flip to False once you've reviewed the plan
MIN_PER_SEMESTER       = 5
MAX_PER_SEMESTER       = 7
EXCLUDE_REJECTED       = True   # skip allocations rejected_by_dvc=True
SYNC_QUALIFICATIONS    = True   # also update LecturerCourseMapping to match new assignments
NEW_LECTURER_DESIGNATION = "Dr"

W = 100
def line(c="="): print(c * W)
def h1(t):
    print(); line("="); print(t); line("=")
def h2(t):
    print(); line("-"); print(t); line("-")


# ------------------------------------------------------------------------------
# Helper: split n items into chunks between lo and hi (inclusive) each.
# Returns a list of chunk sizes summing to n.
# ------------------------------------------------------------------------------
def compute_chunk_sizes(n, lo=MIN_PER_SEMESTER, hi=MAX_PER_SEMESTER):
    if n <= 0:
        return []
    if n <= hi:
        # Not enough courses to hit the minimum with more than one lecturer;
        # a single lecturer takes all of it (may be below `lo`, unavoidable).
        return [n]

    k = max(1, round(n / ((lo + hi) / 2)))
    while lo * k > n and k > 1:
        k -= 1
    while hi * k < n:
        k += 1

    base, rem = divmod(n, k)
    sizes = [base + 1 if i < rem else base for i in range(k)]
    return sizes


# ------------------------------------------------------------------------------
# Helper: interleave a dict of {group_key: [items]} round-robin so consecutive
# items in the merged list alternate across groups (programs/years) instead
# of clumping all of one program together before chunking to lecturers.
# ------------------------------------------------------------------------------
def round_robin_merge(grouped):
    queues = [list(v) for v in grouped.values()]
    result = []
    for group in itertools.zip_longest(*queues, fillvalue=None):
        for item in group:
            if item is not None:
                result.append(item)
    return result


# ------------------------------------------------------------------------------
# Helper: get or create the N lecturers to use for a department, reusing
# existing ones first (ordered by id, i.e. oldest/most-established first)
# and only creating new placeholder lecturers if the department genuinely
# doesn't have enough. Lecturers beyond N are simply left unused (this pass)
# -- that is the "minimise lecturers used" behaviour; nothing is deleted.
# ------------------------------------------------------------------------------
def get_lecturer_pool(department, n_needed, creation_log):
    existing = list(
        Lecturer.objects.filter(department=department).order_by("id")
    )
    pool = existing[:n_needed]

    n_missing = n_needed - len(pool)
    if n_missing > 0:
        dept_tag = "".join(w[0] for w in department.name.split()).upper()[:6] or "DPT"
        start_idx = Lecturer.objects.filter(
            payroll_number__startswith=f"AUTO-{dept_tag}-"
        ).count() + 1
        for i in range(n_missing):
            idx = start_idx + i
            payroll = f"AUTO-{dept_tag}-{idx:03d}"
            email = f"auto.{dept_tag.lower()}.{idx:03d}@university.local"
            name = f"Auto Lecturer {dept_tag}-{idx:03d}"
            if DRY_RUN:
                creation_log.append((department.name, payroll, name))
                # Fabricate a lightweight placeholder object (not saved) so the
                # dry-run report can still show it as part of the pool.
                placeholder = Lecturer(
                    payroll_number=payroll, name=name, email=email,
                    designation=NEW_LECTURER_DESIGNATION, department=department,
                )
                pool.append(placeholder)
            else:
                new_lec, created = Lecturer.objects.get_or_create(
                    payroll_number=payroll,
                    defaults=dict(
                        name=name, email=email,
                        designation=NEW_LECTURER_DESIGNATION,
                        department=department,
                    ),
                )
                creation_log.append((department.name, payroll, name))
                pool.append(new_lec)

    unused_existing = existing[n_needed:]
    return pool, unused_existing


# ------------------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------------------
h1(f"REBALANCE PLAN  (DRY_RUN={DRY_RUN})  target/semester={MIN_PER_SEMESTER}-{MAX_PER_SEMESTER}, "
   f"target/year={MIN_PER_SEMESTER*2}-{MAX_PER_SEMESTER*2}")

base_qs = CourseAllocation.objects.exclude(program_course__isnull=True)
if EXCLUDE_REJECTED:
    base_qs = base_qs.exclude(rejected_by_dvc=True)

# Group by origin department (fallback to department)
depts = Department.objects.all().order_by("name")

creation_log = []
unused_report = []
plan_rows = []          # (dept, sem, lecturer_label, n_courses, programs_touched, years_touched)
assignment_map = {}      # allocation_id -> lecturer (obj or placeholder)

for dept in depts:
    dept_allocs = list(
        base_qs.filter(origin_department=dept) |
        base_qs.filter(origin_department__isnull=True, department=dept)
    )
    if not dept_allocs:
        continue

    for sem in (1, 2):
        sem_allocs = [a for a in dept_allocs if a.program_course.semester == sem]
        if not sem_allocs:
            continue

        # Group by (program_id, year) so round-robin spreads across BOTH
        # different programs and different years within the department.
        grouped = defaultdict(list)
        for a in sem_allocs:
            key = (a.program_id, a.program_course.year)
            grouped[key].append(a)
        # Stable ordering for reproducibility
        grouped = dict(sorted(grouped.items(), key=lambda kv: kv[0]))

        merged = round_robin_merge(grouped)
        n_total = len(merged)
        sizes = compute_chunk_sizes(n_total)
        n_lecturers_needed = len(sizes)

        pool, unused = get_lecturer_pool(dept, n_lecturers_needed, creation_log)
        if unused:
            unused_report.append((dept.name, sem, [l.payroll_number for l in unused]))

        # Slice merged allocations into chunks per lecturer
        idx = 0
        for lecturer, size in zip(pool, sizes):
            chunk = merged[idx: idx + size]
            idx += size
            programs_touched = sorted({c.program_id for c in chunk})
            years_touched = sorted({c.program_course.year for c in chunk})
            label = f"{lecturer.designation or ''} {lecturer.name}".strip()
            plan_rows.append((dept.name, sem, label, len(chunk), programs_touched, years_touched))
            for c in chunk:
                assignment_map[c.id] = lecturer

h2("PER-LECTURER PLAN (department / semester / lecturer / #courses / programs / years)")
print(f"{'Department':25} {'Sem':>4} {'Lecturer':30} {'#Crs':>5} {'#Progs':>7} {'#Yrs':>5}")
line("-")
for dept_name, sem, label, n, progs, yrs in plan_rows:
    print(f"{dept_name[:25]:25} {sem:>4} {label[:30]:30} {n:>5} {len(progs):>7} {len(yrs):>5}")

h2("VALIDATION: any semester slot outside 5-7 target?")
bad = [r for r in plan_rows if not (MIN_PER_SEMESTER <= r[3] <= MAX_PER_SEMESTER)]
if bad:
    for dept_name, sem, label, n, progs, yrs in bad:
        print(f"  ! {dept_name} sem{sem} {label}: {n} courses (outside {MIN_PER_SEMESTER}-{MAX_PER_SEMESTER}, "
              f"likely because total courses in that dept/semester couldn't divide evenly)")
else:
    print("  All lecturer-semester slots fall within target range.")

h2("YEARLY LOAD PER LECTURER (sem1 + sem2 combined, after this plan)")
yearly = defaultdict(int)
for dept_name, sem, label, n, progs, yrs in plan_rows:
    yearly[(dept_name, label)] += n
print(f"{'Department':25} {'Lecturer':30} {'#Courses/Year':>15}")
line("-")
for (dept_name, label), n in sorted(yearly.items()):
    flag = "" if (MIN_PER_SEMESTER*2) <= n <= (MAX_PER_SEMESTER*2) else "  <-- outside 10-14 target"
    print(f"{dept_name[:25]:25} {label[:30]:30} {n:>15}{flag}")

if creation_log:
    h2("NEW LECTURERS TO BE CREATED (department was short-staffed)")
    for dept_name, payroll, name in creation_log:
        print(f"  {dept_name:25} {payroll:20} {name}")
else:
    h2("NEW LECTURERS TO BE CREATED")
    print("  None needed -- every department already has enough lecturers.")

if unused_report:
    h2("EXISTING LECTURERS LEFT UNUSED THIS PASS (minimised out, not deleted)")
    for dept_name, sem, payrolls in unused_report:
        print(f"  {dept_name} sem{sem}: {', '.join(payrolls)}")


# ------------------------------------------------------------------------------
# APPLY (only if DRY_RUN is False)
# ------------------------------------------------------------------------------
if not DRY_RUN:
    h1("APPLYING CHANGES")
    with transaction.atomic():
        updated = 0
        for alloc_id, lecturer in assignment_map.items():
            CourseAllocation.objects.filter(id=alloc_id).update(lecturer=lecturer)
            updated += 1
        print(f"Updated lecturer field on {updated} CourseAllocation rows.")

        if SYNC_QUALIFICATIONS:
            synced = 0
            # Group new assignments by (lecturer, department) to match
            # LecturerCourseMapping's unique_together=(lecturer, department).
            by_lec_dept = defaultdict(set)
            allocs_by_id = {a.id: a for a in CourseAllocation.objects.filter(id__in=assignment_map.keys())
                            .select_related("program_course", "department")}
            for alloc_id, lecturer in assignment_map.items():
                a = allocs_by_id[alloc_id]
                by_lec_dept[(lecturer.id, a.department_id)].add(a.program_course_id)

            for (lecturer_id, dept_id), pc_ids in by_lec_dept.items():
                mapping, _ = LecturerCourseMapping.objects.get_or_create(
                    lecturer_id=lecturer_id, department_id=dept_id,
                )
                mapping.courses.add(*pc_ids)
                synced += 1
            print(f"Synced LecturerCourseMapping for {synced} lecturer/department pairs.")
    h1("DONE -- CHANGES APPLIED")
else:
    h1("DRY RUN COMPLETE -- NOTHING WRITTEN")
    print("Review the plan above. Set DRY_RUN = False at the top of this script and re-run to apply.")
