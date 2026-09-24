"""
map_program_courses_from_existing_allocations.py
==================================================
Run inside the Django shell:

    python manage.py shell < map_program_courses_from_existing_allocations.py

What this fixes
----------------
The Student Groups / Stems / Electives page lets a COD map a curriculum
course (ProgramCourse) onto a SpecializationStem / SelectionGroup IN ADVANCE
(SpecializationStem.program_courses / SelectionGroup.program_courses). From
then on, course_allocation.course_mapping.sync_target()/attach_allocation()
keeps that mapping and the actual CourseAllocation rows (stem.courses /
group.courses) in sync automatically.

But plenty of stems and elective groups were built the OLD way, before that
ProgramCourse-level mapping existed: someone dragged an already-created
CourseAllocation straight onto the stem/group on the COD panel. Those stems
and groups have real courses attached (`.courses`), but nothing recorded in
`.program_courses` — so:
  * the Stems/Electives page shows them as "0 mapped from course master",
  * and the NEXT allocation set won't auto-attach that same course, because
    there is no ProgramCourse-level mapping for sync_target() to work from.

This script closes that gap in the direction the page can't: for every stem
and elective group, it looks at the CourseAllocations ALREADY attached, reads
each one's ProgramCourse, and adds that ProgramCourse into `.program_courses`
if it isn't there yet. Nothing is removed, no CourseAllocation is touched or
created — this only ever ADDS ProgramCourse rows to the two mapping M2Ms.

Safe to re-run: `.add()` on a ManyToMany is idempotent, so running this
twice (or after doing more manual drag-and-drop) just reports "already
mapped" the second time round.

Every ProgramCourse added this way is also recorded in the sister field
`program_courses_from_allocation`, so the Stems/Electives page can flag
those rows as "Mapped from allocation" (blue highlight) instead of showing
them exactly like a course someone picked by hand in the Map courses dialog.

Mismatched program guard
-------------------------
A stem lives under a SpecializationCategory that belongs to exactly one
Program; an elective group MAY be restricted to one Program (`group.program`)
or left open. If an attached CourseAllocation's ProgramCourse belongs to a
DIFFERENT program than the stem's category / the group's restriction, that
almost always means the allocation was manually dropped onto the wrong
stem/group (or the two are deliberately cross-program, which the mapping
model doesn't support). Those are flagged and SKIPPED rather than guessed at
— they need a human to look at the printed list below and either fix the
stem/group assignment or re-point the course.

DRY_RUN = True by default -- prints the full report, writes nothing.
Set DRY_RUN = False and re-run to actually apply the additions.
"""
from collections import defaultdict

from django.db import transaction

from course_allocation.models import SpecializationStem, SelectionGroup

DRY_RUN = True   # <-- flip to False once the report below looks right

# Optional scope: limit the scan to one department (its id), or leave None
# to scan every department in the system.
DEPARTMENT_ID = None


def h1(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _stem_program_course_pairs(stems):
    """
    For each stem: (to_add, mismatched, already) where
      to_add      = [ProgramCourse, ...] newly discovered from stem.courses,
                     not yet in stem.program_courses, program matches,
      mismatched  = [(CourseAllocation, ProgramCourse), ...] program differs
                     from the stem's own program — skipped,
      already     = count of attached courses whose ProgramCourse was
                     already mapped (nothing to do).
    """
    out = {}
    for stem in stems:
        stem_program_id = stem.category.program_id
        existing_ids = set(stem.program_courses.values_list("id", flat=True))
        to_add, seen, mismatched, already = [], set(), [], 0
        for ca in stem.courses.select_related("program_course__program").all():
            pc = ca.program_course
            if pc is None:
                continue
            if pc.id in existing_ids:
                already += 1
                continue
            if pc.id in seen:
                continue
            if pc.program_id != stem_program_id:
                mismatched.append((ca, pc))
                continue
            seen.add(pc.id)
            to_add.append(pc)
        out[stem.id] = (to_add, mismatched, already)
    return out


def _group_program_course_pairs(groups):
    """Same idea as above, for SelectionGroup. A group with no `program`
    restriction accepts a ProgramCourse from any program."""
    out = {}
    for group in groups:
        restrict_id = group.program_id  # None = open to any program
        existing_ids = set(group.program_courses.values_list("id", flat=True))
        to_add, seen, mismatched, already = [], set(), [], 0
        for ca in group.courses.select_related("program_course__program").all():
            pc = ca.program_course
            if pc is None:
                continue
            if pc.id in existing_ids:
                already += 1
                continue
            if pc.id in seen:
                continue
            if restrict_id is not None and pc.program_id != restrict_id:
                mismatched.append((ca, pc))
                continue
            seen.add(pc.id)
            to_add.append(pc)
        out[group.id] = (to_add, mismatched, already)
    return out


def run():
    stems = (
        SpecializationStem.objects
        .select_related("category", "category__program")
        .prefetch_related("courses__program_course__program", "program_courses")
        .order_by("category__program__name", "category__name", "name")
    )
    groups = (
        SelectionGroup.objects
        .select_related("program")
        .prefetch_related("courses__program_course__program", "program_courses")
        .order_by("department__name", "name")
    )
    if DEPARTMENT_ID:
        stems = stems.filter(category__department_id=DEPARTMENT_ID)
        groups = groups.filter(department_id=DEPARTMENT_ID)

    stems = list(stems)
    groups = list(groups)

    h1(f"Scanning {len(stems)} combination stem(s) and {len(groups)} elective group(s)")
    print(f"DRY_RUN = {DRY_RUN}" + ("  (no department filter)" if not DEPARTMENT_ID else f"  (department_id={DEPARTMENT_ID})"))

    stem_plan = _stem_program_course_pairs(stems)
    group_plan = _group_program_course_pairs(groups)

    # ── Report + apply: stems ────────────────────────────────────────────
    h1("COMBINATION STEMS")
    total_stem_added = total_stem_already = total_stem_mismatch = 0
    touched_stems = 0
    with transaction.atomic():
        for stem in stems:
            to_add, mismatched, already = stem_plan[stem.id]
            if not to_add and not mismatched:
                continue  # nothing to report for a fully-mapped or empty stem
            touched_stems += 1
            print(f"\n[{stem.category.program.name}] {stem.category.name} -> {stem.name}  (stem id={stem.id})")
            for pc in to_add:
                print(f"    + map {pc.course_code} - {pc.course_name}  (Y{pc.year} S{pc.semester})"
                      f"{'  (would map)' if DRY_RUN else '  (mapped)'}")
            for ca, pc in mismatched:
                print(f"    ! SKIPPED {pc.course_code} - {pc.course_name}: belongs to program "
                      f"'{pc.program.name if pc.program else 'None'}', not '{stem.category.program.name}' "
                      f"(CourseAllocation id={ca.id}) — check this stem assignment manually.")
            if already:
                print(f"    ({already} attached course(s) were already mapped)")

            total_stem_added += len(to_add)
            total_stem_already += already
            total_stem_mismatch += len(mismatched)

            if to_add and not DRY_RUN:
                stem.program_courses.add(*to_add)
                stem.program_courses_from_allocation.add(*to_add)

        if DRY_RUN:
            transaction.set_rollback(True)

    # ── Report + apply: elective groups ──────────────────────────────────
    h1("ELECTIVE GROUPS")
    total_group_added = total_group_already = total_group_mismatch = 0
    touched_groups = 0
    with transaction.atomic():
        for group in groups:
            to_add, mismatched, already = group_plan[group.id]
            if not to_add and not mismatched:
                continue
            touched_groups += 1
            scope = group.program.name if group.program_id else "any program"
            print(f"\n[{group.department.name}] {group.name}  (group id={group.id}, scope={scope})")
            for pc in to_add:
                print(f"    + map {pc.course_code} - {pc.course_name}  (Y{pc.year} S{pc.semester})"
                      f"{'  (would map)' if DRY_RUN else '  (mapped)'}")
            for ca, pc in mismatched:
                print(f"    ! SKIPPED {pc.course_code} - {pc.course_name}: belongs to program "
                      f"'{pc.program.name if pc.program else 'None'}', group is restricted to "
                      f"'{group.program.name}' (CourseAllocation id={ca.id}) — check this group's "
                      f"restriction or the course manually.")
            if already:
                print(f"    ({already} attached course(s) were already mapped)")

            total_group_added += len(to_add)
            total_group_already += already
            total_group_mismatch += len(mismatched)

            if to_add and not DRY_RUN:
                group.program_courses.add(*to_add)
                group.program_courses_from_allocation.add(*to_add)

        if DRY_RUN:
            transaction.set_rollback(True)

    # ── Summary ───────────────────────────────────────────────────────────
    h1("SUMMARY")
    print(f"Stems   touched: {touched_stems:>4}  |  newly {'to map' if DRY_RUN else 'mapped'}: {total_stem_added:>4}  "
          f"|  already mapped: {total_stem_already:>4}  |  mismatched program (skipped): {total_stem_mismatch:>4}")
    print(f"Groups  touched: {touched_groups:>4}  |  newly {'to map' if DRY_RUN else 'mapped'}: {total_group_added:>4}  "
          f"|  already mapped: {total_group_already:>4}  |  mismatched program (skipped): {total_group_mismatch:>4}")

    if DRY_RUN:
        print("\nDRY_RUN is True — nothing was written. Review the '+' lines above, then set")
        print("DRY_RUN = False and re-run this script to actually apply the mappings.")
    else:
        print("\nApplied. Re-run with DRY_RUN = True at any time to confirm nothing is left unmapped.")

    if total_stem_mismatch or total_group_mismatch:
        print(f"\n{total_stem_mismatch + total_group_mismatch} mismatched-program course(s) were skipped — "
              "see the '!' lines above. These need a manual look, not a re-run of this script.")


run()
