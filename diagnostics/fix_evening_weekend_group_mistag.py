"""
Run with: python manage.py shell < diagnostics/fix_evening_weekend_group_mistag.py

Finds CourseAllocation rows that are BOTH tagged to a StudentGroup AND
flagged is_evening_weekend=True. Under the fixed code these can no longer
be created, but old ones created before the fix will still be sitting in
the DB, and are why some grouped courses appear in the bottom "Student
Groups" table but not in the top main (Regular) table.

DRY RUN by default — it only prints what it would change. Flip APPLY_FIX
to True once you've reviewed the printout and are happy with it.
"""
from course_allocation.models import CourseAllocation

APPLY_FIX = False  # <-- set True to actually untag the bad rows

bad = (CourseAllocation.objects
       .filter(student_group__isnull=False, is_evening_weekend=True)
       .select_related("student_group", "program", "program_course"))

print(f"\nFound {bad.count()} evening/weekend row(s) mistakenly tagged to a student group:\n")

for a in bad:
    print(f"  id={a.id:6d}  {a.course_code:15s}  program={a.program.name if a.program else '?'}  "
          f"group={a.student_group.letter}  (id={a.student_group_id})")

if not bad.exists():
    print("  Nothing to fix.")
elif APPLY_FIX:
    print("\nUntagging (setting student_group=NULL) so these fall back to shared...")
    count = bad.update(student_group=None)
    print(f"Done — {count} row(s) untagged.")
    print(
        "\nNext step: for each affected group, open its 'View' modal in the COD panel "
        "and use '➕ Add Course(s) to this Group' to re-attach the course — with the "
        "fix applied, it will now correctly pick the Regular row instead of the "
        "Evening/Weekend one."
    )
else:
    print(
        "\nDRY RUN — no changes made. Review the list above, then set APPLY_FIX = True "
        "and re-run to untag these rows."
    )
