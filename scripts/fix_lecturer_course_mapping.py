# ==============================================================================
# fix_lecturer_course_mapping.py
#
# WHY THIS SCRIPT INSTEAD OF EDITING CourseAllocation DIRECTLY:
#   course_allocation/auto_allocate_courses.py::_run_allocation() DELETES all
#   CourseAllocation rows for a dept+semester and REBUILDS them from
#   ProgramCourse, picking a lecturer via _pick_lecturer(), which prefers
#   candidates found in `mapping_idx` -- built entirely from
#   LecturerCourseMapping.courses (M2M to ProgramCourse). So the qualification
#   table, NOT CourseAllocation.lecturer, is the thing that actually drives
#   who gets picked next time the autoallocator runs.
#
#   ALSO NOTE (see _pick_lecturer, MAX = 6): that cap is a GLOBAL/lifetime
#   count of a lecturer's CourseAllocation rows across ALL departments and
#   BOTH semesters -- not per semester. With MAX = 6 hardcoded, no lecturer
#   can ever reach a 10-14/year target; you must raise that constant in
#   course_allocation/auto_allocate_courses.py separately from this script.
#   This script only fixes the *qualification* data (LecturerCourseMapping).
#
# WHAT THIS SCRIPT DOES:
#   For each department (origin = ProgramCourse.program.department), for each
#   semester, groups ProgramCourses by (program, year), round-robin merges
#   them so consecutive items alternate program/year, then chunks them into
#   5-7 per lecturer. The SAME lecturer's semester-1 and semester-2 chunks are
#   unioned into one LecturerCourseMapping.courses M2M set (10-14 total),
#   since LecturerCourseMapping is not itself semester-scoped.
#   Existing lecturers are reused first (minimise headcount); new ones are
#   only created if the department doesn't have enough to keep chunks <= 7.
#
# DRY_RUN = True by default -- prints the plan only. Flip to False to write.
#
# USAGE:
#   python manage.py shell < fix_lecturer_course_mapping.py
# ==============================================================================

import itertools
from collections import defaultdict

from django.db import transaction

from course_allocation.models import LecturerCourseMapping
from program_management.models import ProgramCourse
from lecturer_portal.models import Lecturer
from department_management.models import Department

# ------------------------------------------------------------------------------
DRY_RUN            = True   # <-- flip to False once the plan looks right
MIN_PER_SEMESTER   = 5
MAX_PER_SEMESTER   = 7
NEW_LECTURER_DESIGNATION = "Dr"

W = 100
def line(c="="): print(c * W)
def h1(t):
    print(); line("="); print(t); line("=")
def h2(t):
    print(); line("-"); print(t); line("-")


def compute_chunk_sizes(n, lo=MIN_PER_SEMESTER, hi=MAX_PER_SEMESTER):
    if n <= 0:
        return []
    if n <= hi:
        return [n]
    k = max(1, round(n / ((lo + hi) / 2)))
    while lo * k > n and k > 1:
        k -= 1
    while hi * k < n:
        k += 1
    base, rem = divmod(n, k)
    return [base + 1 if i < rem else base for i in range(k)]


def round_robin_merge(grouped):
    queues = [list(v) for v in grouped.values()]
    result = []
    for group in itertools.zip_longest(*queues, fillvalue=None):
        result.extend(x for x in group if x is not None)
    return result


def get_lecturer_pool(department, n_needed, creation_log):
    existing = list(Lecturer.objects.filter(department=department).order_by("id"))
    pool = existing[:n_needed]
    n_missing = n_needed - len(pool)
    if n_missing > 0:
        tag = "".join(w[0] for w in department.name.split()).upper()[:6] or "DPT"
        start = Lecturer.objects.filter(payroll_number__startswith=f"AUTO-{tag}-").count() + 1
        for i in range(n_missing):
            idx = start + i
            payroll = f"AUTO-{tag}-{idx:03d}"
            email = f"auto.{tag.lower()}.{idx:03d}@university.local"
            name = f"Auto Lecturer {tag}-{idx:03d}"
            creation_log.append((department.name, payroll, name))
            if DRY_RUN:
                pool.append(Lecturer(payroll_number=payroll, name=name, email=email,
                                      designation=NEW_LECTURER_DESIGNATION, department=department))
            else:
                new_lec, _ = Lecturer.objects.get_or_create(
                    payroll_number=payroll,
                    defaults=dict(name=name, email=email,
                                  designation=NEW_LECTURER_DESIGNATION, department=department),
                )
                pool.append(new_lec)
    return pool, existing[n_needed:]


# ------------------------------------------------------------------------------
h1(f"LECTURER-COURSE-MAPPING FIX PLAN (DRY_RUN={DRY_RUN}) "
   f"target/semester={MIN_PER_SEMESTER}-{MAX_PER_SEMESTER}, "
   f"target/year={MIN_PER_SEMESTER*2}-{MAX_PER_SEMESTER*2}")

creation_log = []
unused_report = []
plan_rows = []                       # (dept, sem, lecturer_label, n_courses)
lecturer_course_ids = defaultdict(set)   # lecturer_id -> {ProgramCourse ids} (both semesters unioned)
lecturer_obj = {}                    # lecturer_id -> Lecturer (incl. not-yet-saved placeholders)

for dept in Department.objects.all().order_by("name"):
    dept_pcs_all = list(ProgramCourse.objects.filter(program__department=dept))
    if not dept_pcs_all:
        continue

    for sem in (1, 2):
        sem_pcs = [pc for pc in dept_pcs_all if pc.semester == sem]
        if not sem_pcs:
            continue

        grouped = defaultdict(list)
        for pc in sem_pcs:
            grouped[(pc.program_id, pc.year)].append(pc)
        grouped = dict(sorted(grouped.items(), key=lambda kv: kv[0]))

        merged = round_robin_merge(grouped)
        sizes = compute_chunk_sizes(len(merged))
        pool, unused = get_lecturer_pool(dept, len(sizes), creation_log)
        if unused:
            unused_report.append((dept.name, sem, [l.payroll_number for l in unused]))

        idx = 0
        for lecturer, size in zip(pool, sizes):
            chunk = merged[idx: idx + size]
            idx += size
            lecturer_obj[lecturer.payroll_number] = lecturer
            lecturer_course_ids[lecturer.payroll_number].update(pc.id for pc in chunk)
            label = f"{lecturer.designation or ''} {lecturer.name}".strip()
            plan_rows.append((dept.name, sem, label, lecturer.payroll_number, len(chunk)))

h2("PER-LECTURER PLAN (department / semester / lecturer / #courses)")
print(f"{'Department':25} {'Sem':>4} {'Lecturer':30} {'#Courses':>9}")
line("-")
for dept_name, sem, label, payroll, n in plan_rows:
    print(f"{dept_name[:25]:25} {sem:>4} {label[:30]:30} {n:>9}")

h2("YEARLY MAPPING SIZE PER LECTURER (sem1+sem2 union -- what gets written to LecturerCourseMapping)")
print(f"{'Lecturer':30} {'#ProgramCourses mapped':>24}")
line("-")
for payroll, ids in sorted(lecturer_course_ids.items()):
    n = len(ids)
    flag = "" if (MIN_PER_SEMESTER*2) <= n <= (MAX_PER_SEMESTER*2) else "  <-- outside 10-14 target"
    lec = lecturer_obj[payroll]
    label = f"{lec.designation or ''} {lec.name}".strip()
    print(f"{label:30} {n:>24}{flag}")

if creation_log:
    h2("NEW LECTURERS TO BE CREATED")
    for dept_name, payroll, name in creation_log:
        print(f"  {dept_name:25} {payroll:20} {name}")

if unused_report:
    h2("EXISTING LECTURERS LEFT UNUSED THIS PASS (not deleted)")
    for dept_name, sem, payrolls in unused_report:
        print(f"  {dept_name} sem{sem}: {', '.join(payrolls)}")

# ------------------------------------------------------------------------------
if not DRY_RUN:
    h1("APPLYING CHANGES TO LecturerCourseMapping")
    with transaction.atomic():
        updated = 0
        for payroll, pc_ids in lecturer_course_ids.items():
            lecturer = Lecturer.objects.get(payroll_number=payroll)
            mapping, _ = LecturerCourseMapping.objects.get_or_create(
                lecturer=lecturer, department=lecturer.department,
            )
            mapping.courses.set(pc_ids)   # replace with the computed set
            updated += 1
        print(f"Updated/created {updated} LecturerCourseMapping rows.")
    h1("DONE")
    print("REMINDER: also raise MAX in course_allocation/auto_allocate_courses.py::_pick_lecturer")
    print("(currently MAX = 6, a GLOBAL lifetime cap) or the autoallocator will still refuse to")
    print("give any lecturer more than 6 total allocations across both semesters combined.")
else:
    h1("DRY RUN COMPLETE -- NOTHING WRITTEN")
    print("Set DRY_RUN = False to apply. Then RUN THE ACTUAL AUTOALLOCATOR (per dept, per semester)")
    print("so it regenerates CourseAllocation from this new LecturerCourseMapping data.")
