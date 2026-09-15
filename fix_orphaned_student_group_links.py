"""
Run with: python manage.py shell < fix_orphaned_student_group_links.py
(or paste into `python manage.py shell`)

Purpose
-------
diagnose_epsc222.py found that EPSC 222-T's CourseAllocation row has
student_group = NULL, even though a matching StudentGroup already exists
(id=277, Bachelor of Education (Arts) — Year 2, letter='T'). That single
missing link is why it showed up as a false conflict against every other
EPSC 222-<letter> course.

This is very unlikely to be the only row like this in the whole database —
manual data entry naturally produces the occasional missed link. This
script finds EVERY CourseAllocation whose course_code ends in "-<LETTER>"
(matching how these group-tagged courses are consistently named across the
dataset), has student_group = NULL, and has a StudentGroup that matches it
EXACTLY on program + year + semester + intake + letter. It does NOT guess —
if zero or more than one StudentGroup matches, it's reported as unresolved
rather than linked.

Safe by default: DRY RUN — prints every proposed fix and every unresolved
case, changes nothing. Re-run with APPLY = True once the printed list looks
right.
"""
import re
from course_allocation.models import CourseAllocation, StudentGroup
from timetable.timetable_panel import _get_year_value

APPLY = False   # <-- flip to True once you've reviewed the dry-run output

SUFFIX_RE = re.compile(r'-([A-Za-z]{1,3})$')   # matches "-T", "-AF", "-AK", etc.

print(f"\n{'APPLYING FIXES' if APPLY else 'DRY RUN — no changes will be saved'}\n")

candidates = (
    CourseAllocation.objects
    .filter(student_group__isnull=True)
    .exclude(course_code__isnull=True)
    .select_related("program", "program_course")
)

resolved, unresolved, no_suffix = [], [], 0

for alloc in candidates:
    m = SUFFIX_RE.search(alloc.course_code or "")
    if not m:
        no_suffix += 1
        continue
    letter = m.group(1)

    if not alloc.program_id:
        unresolved.append((alloc, letter, "no program set on this allocation"))
        continue

    semester = getattr(alloc.program_course, "semester", None) if alloc.program_course_id else None
    year = _get_year_value(alloc)
    if not year:
        unresolved.append((alloc, letter, "could not determine year of study"))
        continue

    qs = StudentGroup.objects.filter(
        program_id=alloc.program_id,
        year=year,
        letter__iexact=letter,
        intake=alloc.intake or "normal",
    )
    if semester is not None:
        qs = qs.filter(semester=semester)

    matches = list(qs)
    if len(matches) == 1:
        resolved.append((alloc, matches[0]))
    elif len(matches) == 0:
        unresolved.append((alloc, letter, "no matching StudentGroup found (program/year/semester/intake/letter)"))
    else:
        unresolved.append((alloc, letter, f"{len(matches)} ambiguous StudentGroup matches — needs manual review"))

print(f"=== {len(resolved)} row(s) ready to link ===\n")
for alloc, group in resolved:
    print(f"  CourseAllocation id={alloc.id:<6} {alloc.course_code!r:<20} -> StudentGroup id={group.id} ({group.display_name})")
    if APPLY:
        alloc.student_group = group
        alloc.save(update_fields=["student_group"])

if APPLY and resolved:
    print(f"\n  Saved {len(resolved)} link(s).")
elif resolved:
    print(f"\n  Nothing saved yet — set APPLY = True at the top of this script and re-run to apply.")

print(f"\n=== {len(unresolved)} row(s) could NOT be auto-resolved (need manual review) ===\n")
for alloc, letter, reason in unresolved:
    print(f"  CourseAllocation id={alloc.id:<6} {alloc.course_code!r:<20} letter={letter!r:<5} -> {reason}")

print(f"\n({no_suffix} other student_group=NULL row(s) had no '-<LETTER>' suffix — skipped, "
      f"these are likely genuinely shared/ungrouped courses, not group-tagged ones.)")
