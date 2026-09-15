"""
Diagnostic: trace exactly what happens to a specific course code through
every step of export_unscheduled_pdf's filtering/grouping, to pin down
precisely why it is or isn't showing up on a department-scoped PDF.

Run with:
    python manage.py shell < debug_course_visibility.py

Edit COURSE_CODE and DEPARTMENT_ID below first.
"""
from course_allocation.models import CourseAllocation, CombinedCourseGroup

# ── EDIT THESE TWO ──────────────────────────────────────────────────────
COURSE_CODE = "COSC 103"
DEPARTMENT_ID = None   # the department_id you're scoping the PDF export to
                        # (the Plant Science department's id) — leave None
                        # to skip the scope-filter check and just inspect
                        # the raw row(s).
# ─────────────────────────────────────────────────────────────────────────

rows = list(CourseAllocation.objects.filter(course_code__iexact=COURSE_CODE)
            .select_related('department', 'origin_department', 'program', 'program__department'))

if not rows:
    print(f"No CourseAllocation rows found with course_code == {COURSE_CODE!r} "
          f"(check exact spelling/spacing — this is an exact, case-insensitive match).")
else:
    print(f"Found {len(rows)} row(s) for {COURSE_CODE!r}:\n")

all_member_ids = set()
for group in CombinedCourseGroup.objects.prefetch_related('allocations'):
    all_member_ids.update(group.allocations.values_list('id', flat=True))

for a in rows:
    print("=" * 70)
    print(f"CourseAllocation id={a.id}  course_code={a.course_code!r}")
    print(f"  department (allocating)      = {a.department_id} "
          f"({a.department.name if a.department else 'NULL'})")
    print(f"  origin_department            = {a.origin_department_id} "
          f"({a.origin_department.name if a.origin_department else 'NULL'})")
    print(f"  program                      = {a.program_id} "
          f"({a.program.name if a.program else 'NULL'})")
    print(f"  program.department           = "
          f"{(a.program.department_id if a.program else None)} "
          f"({(a.program.department.name if a.program and a.program.department else 'NULL')})")

    dept_used_for_grouping = a.department or (a.program.department if a.program else None)
    print(f"  -> department used for the Main Allocation table heading: "
          f"{dept_used_for_grouping.name if dept_used_for_grouping else 'Unspecified Department'}")

    is_merged_member = a.id in all_member_ids
    print(f"  Is a member of a CombinedCourseGroup (excluded from the main "
          f"breakdown, shown only in 'Merged Course Groups' instead)? "
          f"{'YES — THIS IS WHY IT IS MISSING FROM THE PROGRAM ROW' if is_merged_member else 'No'}")

    if DEPARTMENT_ID is not None:
        passes_scope_filter = (
            a.department_id == DEPARTMENT_ID or
            (a.program_id and a.program.department_id == DEPARTMENT_ID)
        )
        print(f"  Would pass _apply_scope_filters for department_id={DEPARTMENT_ID}? "
              f"{'YES' if passes_scope_filter else 'NO — THIS IS WHY IT IS MISSING'}")

    print()

if all_member_ids and not DEPARTMENT_ID:
    print("(Set DEPARTMENT_ID at the top of this script and re-run to also check "
          "whether it passes the scope filter for that department.)")
