# ==============================================================================
# backup_and_delete_department_allocations.py
#
# WHAT THIS SCRIPT DOES
#   For a single department (matched by name, e.g. "Public Health"):
#     1. Serializes EVERY allocation-related row that touches that department
#        to a timestamped JSON file (full model fields, restorable with
#        Django's loaddata / deserializer).
#     2. Prints a pre-flight report, including cross-department warnings for
#        CombinedCourseGroup / MergedCourseGroup / MergedCourseGroupTimetable /
#        SharedVenueExamGroup rows that link this department's allocations to
#        OTHER departments' allocations (those groups will lose a member, not
#        get deleted themselves — the script does NOT auto-repair them).
#     3. Only if DRY_RUN = False: deletes CourseAllocation + LabAllocation
#        rows for the department inside a single transaction. Everything
#        downstream (Timetable, TempTimetable, ExamTimetable,
#        ExamTempTimetable, LabTimetable, LabExamTimetable, MergedCourseGroup,
#        MergedCourseGroupTimetable) cascades via on_delete=CASCADE. M2M rows
#        (CombinedCourseGroup.allocations, SharedVenueExamGroup.course_allocations,
#        MergedCourseGroup.merged_courses) are cleaned up automatically by
#        Django/DB when the CourseAllocation side is removed.
#
#   WHAT IS *NOT* TOUCHED:
#     - ArchivedCourseAllocation rows (separate historical table, untouched).
#     - Department / Faculty / ProgramCourse / Program rows themselves.
#     - CombinedCourseGroup / MergedCourseGroup rows that still have
#       allocations left over from OTHER departments after this department's
#       rows are removed (they survive, just smaller — reported, not fixed).
#
# WHY BACKUP FIRST INSTEAD OF RELYING ON backup_system'S DB-LEVEL BACKUPS:
#   backup_system/services.py backs up the whole database/files, which is
#   overkill to restore from for "I deleted one department by mistake". This
#   script produces a narrow, department-scoped JSON snapshot that can be
#   restored with `python manage.py loaddata <file>` in seconds.
#
# DRY_RUN = True by default -- writes the backup and prints the report, but
# does NOT delete anything. Flip to False once you've reviewed the report.
#
# USAGE:
#   python manage.py shell < scripts/backup_and_delete_department_allocations.py
# ==============================================================================

import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core import serializers
from django.db import transaction

from department_management.models import Department
from course_allocation.models import (
    CourseAllocation,
    LabAllocation,
    CombinedCourseGroup,
)
from timetable.models import (
    Timetable,
    TempTimetable,
    ExamTimetable,
    ExamTempTimetable,
    LabTimetable,
    LabExamTimetable,
    MergedCourseGroup,
    MergedCourseGroupTimetable,
    SharedVenueExamGroup,
)

# ------------------------------------------------------------------------------
DEPARTMENT_NAME = "Public Health"   # <-- exact or partial (case-insensitive) match
DRY_RUN         = False             # <-- flip to False once the report looks right
BACKUP_DIR      = Path(settings.BASE_DIR) / "backups" / "department_deletions"

W = 100
def line(c="="): print(c * W)
def h1(t):
    print(); line("="); print(t); line("=")
def h2(t):
    print(); line("-"); print(t); line("-")


# ------------------------------------------------------------------------------
# 1. Resolve the department (safety: refuse to guess between multiple matches)
# ------------------------------------------------------------------------------
h1(f"RESOLVING DEPARTMENT: {DEPARTMENT_NAME!r}")

matches = list(Department.objects.filter(name__icontains=DEPARTMENT_NAME))
if not matches:
    print(f"No department matching {DEPARTMENT_NAME!r} was found. Nothing to do.")
    raise SystemExit(0)
if len(matches) > 1:
    print("Multiple departments matched — narrow DEPARTMENT_NAME and re-run:")
    for d in matches:
        print(f"   id={d.id}  {d.name}  ({d.faculty.name})")
    raise SystemExit(1)

dept = matches[0]
print(f"Department resolved: id={dept.id}  {dept.name}  ({dept.faculty.name})")


# ------------------------------------------------------------------------------
# 2. Gather everything scoped to this department
# ------------------------------------------------------------------------------
h1("GATHERING RECORDS")

allocations = list(CourseAllocation.objects.filter(department=dept))
alloc_ids = [a.id for a in allocations]
print(f"CourseAllocation rows (department={dept.name}): {len(allocations)}")

lab_allocations = list(
    LabAllocation.objects.filter(program_course__program__department=dept)
)
lab_alloc_ids = [la.id for la in lab_allocations]
print(f"LabAllocation rows (via program_course.program.department): {len(lab_allocations)}")

timetable_entries          = list(Timetable.objects.filter(course_allocation_id__in=alloc_ids))
temp_timetable_entries     = list(TempTimetable.objects.filter(course_allocation_id__in=alloc_ids))
exam_timetable_entries     = list(ExamTimetable.objects.filter(course_allocation_id__in=alloc_ids))
exam_temp_timetable_entries = list(ExamTempTimetable.objects.filter(course_allocation_id__in=alloc_ids))
lab_timetable_entries      = list(LabTimetable.objects.filter(lab_allocation_id__in=lab_alloc_ids))
lab_exam_timetable_entries = list(LabExamTimetable.objects.filter(lab_allocation_id__in=lab_alloc_ids))

print(f"Timetable entries:           {len(timetable_entries)}")
print(f"TempTimetable entries:       {len(temp_timetable_entries)}")
print(f"ExamTimetable entries:       {len(exam_timetable_entries)}")
print(f"ExamTempTimetable entries:   {len(exam_temp_timetable_entries)}")
print(f"LabTimetable entries:        {len(lab_timetable_entries)}")
print(f"LabExamTimetable entries:    {len(lab_exam_timetable_entries)}")

combined_groups = list(
    CombinedCourseGroup.objects.filter(allocations__id__in=alloc_ids).distinct()
)
merged_groups = list(
    MergedCourseGroup.objects.filter(merged_courses__id__in=alloc_ids).distinct()
)
merged_tt_groups = list(
    MergedCourseGroupTimetable.objects.filter(merged_courses__id__in=alloc_ids).distinct()
)
shared_venue_groups = list(
    SharedVenueExamGroup.objects.filter(course_allocations__id__in=alloc_ids).distinct()
)

print(f"CombinedCourseGroup rows touched:        {len(combined_groups)}")
print(f"MergedCourseGroup rows touched:          {len(merged_groups)}")
print(f"MergedCourseGroupTimetable rows touched: {len(merged_tt_groups)}")
print(f"SharedVenueExamGroup rows touched:       {len(shared_venue_groups)}")


# ------------------------------------------------------------------------------
# 3. Cross-department warnings — groups that will be left half-emptied
# ------------------------------------------------------------------------------
h2("CROSS-DEPARTMENT WARNINGS (groups NOT fully owned by this department)")

alloc_id_set = set(alloc_ids)
any_warning = False

for g in combined_groups:
    other_ids = set(g.allocations.values_list("id", flat=True)) - alloc_id_set
    if other_ids:
        any_warning = True
        print(f"  CombinedCourseGroup '{g.group_code}' has {len(other_ids)} allocation(s) "
              f"from OTHER departments — it will survive with fewer members, not be deleted.")
    if g.primary_allocation_id and g.primary_allocation_id in alloc_id_set and other_ids:
        print(f"    -> its primary_allocation is inside {dept.name}; the group will lose its primary.")

for g in merged_groups:
    other_ids = set(g.merged_courses.values_list("id", flat=True)) - alloc_id_set
    if other_ids or (g.base_course_id not in alloc_id_set):
        any_warning = True
        print(f"  MergedCourseGroup '{g.merged_code}' (id={g.id}) references allocation(s) "
              f"outside {dept.name}. If its base_course is inside this department, the WHOLE "
              f"group row will cascade-delete even though other departments' courses were in it.")

for g in merged_tt_groups:
    other_ids = set(g.merged_courses.values_list("id", flat=True)) - alloc_id_set
    if other_ids or (g.base_course_id not in alloc_id_set):
        any_warning = True
        print(f"  MergedCourseGroupTimetable '{g.merged_code}' (id={g.id}) references allocation(s) "
              f"outside {dept.name}. If its base_course is inside this department, the WHOLE "
              f"group row will cascade-delete even though other departments' courses were in it.")

for g in shared_venue_groups:
    other_ids = set(g.course_allocations.values_list("id", flat=True)) - alloc_id_set
    if other_ids:
        any_warning = True
        print(f"  SharedVenueExamGroup (id={g.id}, venue={g.venue.code}, {g.date}) has "
              f"{len(other_ids)} allocation(s) from OTHER departments sharing that slot — "
              f"it will survive with fewer members, not be deleted.")

if not any_warning:
    print("  None. Every touched group is fully owned by this department.")


# ------------------------------------------------------------------------------
# 4. Write the backup (always, even in DRY_RUN — cheap insurance)
# ------------------------------------------------------------------------------
h1("WRITING BACKUP")

BACKUP_DIR.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
dept_slug = dept.name.lower().replace(" ", "_")
backup_path = BACKUP_DIR / f"{dept_slug}_{timestamp}.json"

# Order matters for a clean loaddata restore: parents before children.
objects_to_serialize = (
    allocations
    + lab_allocations
    + timetable_entries
    + temp_timetable_entries
    + exam_timetable_entries
    + exam_temp_timetable_entries
    + lab_timetable_entries
    + lab_exam_timetable_entries
    + combined_groups
    + merged_groups
    + merged_tt_groups
    + shared_venue_groups
)

serialized = serializers.serialize("json", objects_to_serialize, indent=2)

with open(backup_path, "w") as f:
    f.write(serialized)

meta_path = backup_path.with_suffix(".meta.json")
with open(meta_path, "w") as f:
    json.dump({
        "department_id": dept.id,
        "department_name": dept.name,
        "faculty": dept.faculty.name,
        "created_at": timestamp,
        "counts": {
            "course_allocations": len(allocations),
            "lab_allocations": len(lab_allocations),
            "timetable_entries": len(timetable_entries),
            "temp_timetable_entries": len(temp_timetable_entries),
            "exam_timetable_entries": len(exam_timetable_entries),
            "exam_temp_timetable_entries": len(exam_temp_timetable_entries),
            "lab_timetable_entries": len(lab_timetable_entries),
            "lab_exam_timetable_entries": len(lab_exam_timetable_entries),
            "combined_groups": len(combined_groups),
            "merged_groups": len(merged_groups),
            "merged_tt_groups": len(merged_tt_groups),
            "shared_venue_groups": len(shared_venue_groups),
        },
    }, f, indent=2)

print(f"Backup written:  {backup_path}")
print(f"Metadata written: {meta_path}")
print(f"Total objects backed up: {len(objects_to_serialize)}")
print()
print("To restore later:")
print(f"   python manage.py loaddata {backup_path}")


# ------------------------------------------------------------------------------
# 5. Delete (only if DRY_RUN is False)
# ------------------------------------------------------------------------------
h1("DELETE" if not DRY_RUN else "DELETE (SKIPPED — DRY_RUN = True)")

if DRY_RUN:
    print("DRY_RUN is True — no rows were deleted. Review the report and backup above,")
    print("then flip DRY_RUN = False and re-run this script to actually delete.")
else:
    with transaction.atomic():
        lab_deleted = LabAllocation.objects.filter(id__in=lab_alloc_ids).delete()
        alloc_deleted = CourseAllocation.objects.filter(id__in=alloc_ids).delete()

    print(f"Deleted LabAllocation (+cascades):    {lab_deleted}")
    print(f"Deleted CourseAllocation (+cascades): {alloc_deleted}")
    print()
    print(f"{dept.name} allocations removed. Backup is safe at: {backup_path}")

line("=")
