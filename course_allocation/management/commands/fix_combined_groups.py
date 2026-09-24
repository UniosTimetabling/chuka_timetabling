# course_allocation/management/commands/fix_combined_groups.py
"""
One script that cleans up two known sources of drift between a
CombinedCourseGroup and the individual CourseAllocation rows folded into
it: the LECTURER, and the group-letter suffix on course_code.

Background
----------
1) LECTURER drift
   CombinedCourseGroup.lecturer is the field everything downstream reads
   (timetabling, PDFs, the COD panel) -- CourseAllocation.lecturer on a
   member row is really just "who this course was assigned to before/
   outside the combine". Two ways that can drift out of sync:

     a) Group never got a lecturer (lecturer=NULL) even though a member
        row already has one -- e.g. the combine happened before a
        lecturer was picked. Fix: copy the member's lecturer up onto the
        group.
     b) Group DOES have a lecturer, but a member's own `lecturer` field
        is stale -- e.g. the course had a different lecturer allocated
        earlier, then got combined into a group that (later, or at
        combine time) was given a different lecturer, and the member row
        was never updated to match. Fix: the group's lecturer is
        authoritative once set -- overwrite every disagreeing member's
        `lecturer` to match it.

   If a group has NO lecturer AND its members disagree with each other
   (more than one distinct lecturer, case (a) with ambiguity), that group
   is reported as ambiguous and left alone unless --prefer-primary is
   passed, in which case primary_allocation's lecturer breaks the tie.

2) LETTER drift
   Under the normal combine flow, base_course_code is stored as the
   plain, letter-stripped base -- the section letter for a "sectioned"
   group (one of several combined groups splitting the same base course
   into lecture sections, e.g. two distinct groups "ECON 232 C" and
   "ECON 232 D" for two sections of the same course) actually lives on
   the group's own `group_code` -- that's the field the UI shows
   ("Combined: <group_code>"), and it's where real data keeps landing
   the letter, whether typed by hand at combine time or produced by an
   older/seeded import path. The problem is member CourseAllocation rows
   inside one of these groups don't reliably carry the matching letter: a
   member might read "CHEM 102-B" while sitting in the "CHEM 102-A"
   group -- which is doubly confusing when a genuinely separate "CHEM
   102-B" group also exists elsewhere -- or have no letter at all, or use
   a different separator ("101-a", "101_a", "101(a)", "101  a", "101A").
   Fix: when a group's own group_code (falling back to base_course_code)
   carries a letter, that letter is authoritative -- every member's
   course_code is rewritten to `<member's base>-<LETTER>`, uppercase,
   overriding whatever it had. Groups with no letter on either field are
   left untouched -- there's nothing authoritative to sync from.

Both fixes are independent and can be run separately (--skip-lecturers /
--skip-letters). Neither merges or deletes rows, and neither touches
number_of_students or schedule data. Letter rewrites are checked against
the same uniqueness rule CourseAllocation.clean() enforces (program,
course_code case-insensitive, intake, student_group) before being
applied -- a collision is skipped and reported unless --allow-duplicates
is passed.

DRY RUN by default -- pass --apply to actually write changes.

Usage:
    python manage.py fix_combined_groups                              # dry run, everything, all departments
    python manage.py fix_combined_groups --apply
    python manage.py fix_combined_groups --department "Economics" --apply
    python manage.py fix_combined_groups --apply --prefer-primary      # tie-break ambiguous NULL-lecturer groups
    python manage.py fix_combined_groups --apply --allow-duplicates    # also rewrite letters even if it collides
    python manage.py fix_combined_groups --apply --skip-letters        # lecturers only
    python manage.py fix_combined_groups --apply --skip-lecturers      # letters only
"""
import re
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from course_allocation.models import CombinedCourseGroup, CourseAllocation
from department_management.models import Department


# Mirrors course_management.cod_panel.strip_group_suffix, so a group's own
# letter is read the same way it would have been read at combine-time --
# with one deliberate tightening: the trailing letter run is capped at 2
# characters (matching config_helpers.make_group_code's own convention:
# single letters A-Z, then AA/AB/... only past 26 splits -- a real course
# is never split into 27+ lecture sections). Without that cap, this regex
# would also match the tail of auto-generated group_code values like
# "CHEM_102_COMBINED" or "CHEM_102_SPLIT" (whose trailing word is *all
# letters* after the digit run) and misread "COMBINED"/"SPLIT" as if they
# were a real group letter. Recognised shapes:
#   <base><digits> ( LETTER(S) )        COSC 101(A), COSC101(AC)
#   <base><digits> - LETTER(S)          COSC 101-A, COSC 101-AA
#   <base><digits> / LETTER(S)          COSC 101/A
#   <base><digits> _ LETTER(S)          COSC 101_A
#   <base><digits>   LETTER(S)          COSC 101 A, COSC 101  A (bare space)
#   <base><digits>LETTER(S)             COSC101A (no separator at all)
def strip_group_suffix(code):
    code = (code or "").strip()

    m = re.match(r"^(.*?\d+)\s*\(\s*([A-Za-z]{1,2})\s*\)\s*$", code, re.I)
    if m:
        base, letter = m.group(1).strip(), m.group(2).upper()
        if re.search(r"\d", base):

            return base, letter

    m = re.match(r"^(.*?\d+)\s*[-/_]?\s*([A-Za-z]{1,2})\s*$", code, re.I)
    if m:
        base, letter = m.group(1).strip(), m.group(2).upper()
        if re.search(r"\d", base):
            return base, letter

    return code, None


def append_group(code, letter):
    return f"{code.strip()}-{letter.upper()}"


def lettered_code_for_group(code, group_letter):
    """Rewrite `code` so it carries `group_letter` instead of whatever
    letter (if any) it currently has. Always strips first, so a member
    tagged with the wrong letter -- or no letter -- ends up consistent."""
    base, _ = strip_group_suffix(code)
    return append_group(base, group_letter)


class Command(BaseCommand):
    help = (
        "Fix CombinedCourseGroup drift in one pass: (1) sync lecturer "
        "between a group and its member allocations in both directions "
        "-- fill a NULL group lecturer from members, and overwrite stale "
        "member lecturers once a group has one -- and (2) force every "
        "member's course_code letter to match its group's own "
        "group_code (or base_course_code) letter. Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--department", type=str, default=None,
            help="Restrict to one department name (default: all departments).",
        )
        parser.add_argument(
            "--prefer-primary", action="store_true",
            help=(
                "When a group has NO lecturer and its members disagree "
                "(more than one distinct lecturer), use the "
                "primary_allocation's lecturer to break the tie instead "
                "of leaving the group flagged as ambiguous."
            ),
        )
        parser.add_argument(
            "--allow-duplicates", action="store_true",
            help=(
                "Without this flag, a letter rewrite is skipped if it "
                "would produce a course_code colliding with another "
                "row's (program, course_code, intake, student_group). "
                "Pass this to rewrite it anyway."
            ),
        )
        parser.add_argument(
            "--skip-lecturers", action="store_true",
            help="Don't touch lecturers -- only sync letters.",
        )
        parser.add_argument(
            "--skip-letters", action="store_true",
            help="Don't touch course-code letters -- only sync lecturers.",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually write changes. Without this flag, only reports what would happen.",
        )

    # ------------------------------------------------------------------ #
    # Lecturer pass
    # ------------------------------------------------------------------ #
    def _plan_lecturer_changes(self, groups, prefer_primary):
        """
        Returns (group_updates, member_updates, ambiguous) where:
          group_updates  = [(group, lecturer, reason)]
          member_updates = [(group, member, old_lecturer, new_lecturer)]
          ambiguous      = [(group, members)]  -- group has no lecturer,
                            members disagree, no tie-break available
        """
        group_updates = []
        member_updates = []
        ambiguous = []

        for group in groups:
            members = list(group.allocations.all())

            if group.lecturer_id:
                # Case (b): group already has a lecturer -- it's
                # authoritative. Any member that disagrees gets corrected.
                for m in members:
                    if m.lecturer_id != group.lecturer_id:
                        member_updates.append((group, m, m.lecturer, group.lecturer))
                continue

            # Case (a): group has no lecturer -- try to derive one from members.
            lecturer_ids = {m.lecturer_id for m in members if m.lecturer_id}
            if not lecturer_ids:
                continue  # nothing on any member either -- nothing to do

            if len(lecturer_ids) == 1:
                lecturer = next(m.lecturer for m in members if m.lecturer_id)
                group_updates.append((group, lecturer, "only lecturer found among member allocations"))
                # Once the group is set, bring any (impossible here, since
                # they all share one lecturer) stragglers into line too --
                # no-op in this branch since they already agree.
                continue

            if prefer_primary and group.primary_allocation_id and group.primary_allocation.lecturer_id:
                lecturer = group.primary_allocation.lecturer
                group_updates.append((
                    group, lecturer,
                    "members disagree -- used primary_allocation's lecturer (--prefer-primary)",
                ))
                for m in members:
                    if m.lecturer_id != lecturer.id:
                        member_updates.append((group, m, m.lecturer, lecturer))
            else:
                ambiguous.append((group, members))

        return group_updates, member_updates, ambiguous

    # ------------------------------------------------------------------ #
    # Letter pass
    # ------------------------------------------------------------------ #
    def _plan_letter_changes(self, groups, allow_duplicates):
        """Returns (to_apply, skipped) of (group, member, old_code, new_code).

        The authoritative letter for a "sectioned" group (one of several
        combined groups splitting the same base course into lecture
        sections, e.g. "CHEM 102-A" vs "CHEM 102-B") is read from the
        group's own `group_code` first -- that's what the COD actually
        typed/sees (it's what every "Combined: <label>" badge in the UI
        displays), and it's where real data keeps landing the letter.
        `base_course_code` is checked only as a fallback, for the rarer
        case where it carries the letter instead.
        """
        sectioned = []
        for group in groups:
            _, group_letter = strip_group_suffix(group.group_code)
            if group_letter is None:
                _, group_letter = strip_group_suffix(group.base_course_code)
            if group_letter is not None:
                sectioned.append((group, group_letter))

        if not sectioned:
            return [], [], 0

        all_rows = list(
            CourseAllocation.objects.all().only(
                "id", "course_code", "program_id", "intake", "student_group_id"
            )
        )
        occupied = defaultdict(set)
        for row in all_rows:
            key = (row.program_id, row.intake, row.student_group_id)
            occupied[key].add((row.course_code or "").strip().upper())

        proposed = []
        for group, group_letter in sectioned:
            for member in group.allocations.all():
                new_code = lettered_code_for_group(member.course_code, group_letter)
                if new_code.strip().upper() == (member.course_code or "").strip().upper():
                    continue
                proposed.append((group, member, member.course_code, new_code))

        to_apply, skipped = [], []
        for group, member, old_code, new_code in proposed:
            key = (member.program_id, member.intake, member.student_group_id)
            new_upper = new_code.strip().upper()
            others = occupied[key] - {(old_code or "").strip().upper()}
            if new_upper in others and not allow_duplicates:
                skipped.append((group, member, old_code, new_code))
            else:
                to_apply.append((group, member, old_code, new_code))

        return to_apply, skipped, len(sectioned)

    # ------------------------------------------------------------------ #
    def handle(self, *args, **options):
        apply_changes = options["apply"]
        dept_name = options["department"]
        prefer_primary = options["prefer_primary"]
        allow_duplicates = options["allow_duplicates"]
        do_lecturers = not options["skip_lecturers"]
        do_letters = not options["skip_letters"]

        dept = None
        if dept_name:
            try:
                dept = Department.objects.get(name__iexact=dept_name)
            except Department.DoesNotExist:
                raise CommandError(f"No department named '{dept_name}'.")
            except Department.MultipleObjectsReturned:
                raise CommandError(f"Multiple departments match '{dept_name}'; be more specific.")

        qs = (
            CombinedCourseGroup.objects
            .select_related("department", "lecturer", "primary_allocation__lecturer")
            .prefetch_related("allocations__lecturer", "allocations__program")
        )
        if dept is not None:
            qs = qs.filter(department=dept)
        groups = list(qs.order_by("department__name", "id"))

        if not groups:
            self.stdout.write(self.style.SUCCESS(
                f"No combined groups found ({'department ' + dept.name if dept else 'all departments'})."
            ))
            return

        # ---------------- Lecturers ----------------
        group_lecturer_updates, member_lecturer_updates, ambiguous = [], [], []
        if do_lecturers:
            group_lecturer_updates, member_lecturer_updates, ambiguous = \
                self._plan_lecturer_changes(groups, prefer_primary)

            self.stdout.write(self.style.MIGRATE_HEADING("Lecturer sync"))
            if not group_lecturer_updates and not member_lecturer_updates and not ambiguous:
                self.stdout.write(self.style.SUCCESS("  Nothing to do -- lecturers already consistent.\n"))
            else:
                if group_lecturer_updates:
                    self.stdout.write(f"  {len(group_lecturer_updates)} group(s) need a lecturer filled in:")
                    for group, lecturer, reason in group_lecturer_updates:
                        dept_label = group.department.name if group.department_id else "(no department)"
                        self.stdout.write(
                            f"    [{dept_label}] '{group.group_code}' ({group.base_course_code})  "
                            f"lecturer -> {lecturer.display_name}   [{reason}]"
                        )
                if member_lecturer_updates:
                    self.stdout.write(f"  {len(member_lecturer_updates)} member row(s) have a stale lecturer:")
                    last_group_id = None
                    for group, member, old_lecturer, new_lecturer in member_lecturer_updates:
                        if group.id != last_group_id:
                            dept_label = group.department.name if group.department_id else "(no department)"
                            self.stdout.write(f"    -- [{dept_label}] group '{group.group_code}' ({group.base_course_code}) --")
                            last_group_id = group.id
                        old_label = old_lecturer.display_name if old_lecturer else "unassigned"
                        self.stdout.write(
                            f"      #{member.id}  {member.course_code}  "
                            f"{old_label} -> {new_lecturer.display_name}"
                        )
                if ambiguous:
                    self.stdout.write(self.style.WARNING(
                        f"  {len(ambiguous)} group(s) AMBIGUOUS -- no lecturer on the group, "
                        f"and members disagree with each other. Left alone:"
                    ))
                    for group, members in ambiguous:
                        dept_label = group.department.name if group.department_id else "(no department)"
                        self.stdout.write(self.style.WARNING(
                            f"    [{dept_label}] '{group.group_code}' ({group.base_course_code}):"
                        ))
                        for m in members:
                            who = m.lecturer.display_name if m.lecturer_id else "unassigned"
                            self.stdout.write(f"        #{m.id}  {m.course_code}  -> {who}")
                        self.stdout.write(
                            "        Re-run with --prefer-primary to auto-resolve using the "
                            "primary allocation's lecturer, or set the group's lecturer manually."
                        )
                self.stdout.write("")

        # ---------------- Letters ----------------
        letter_to_apply, letter_skipped, n_sectioned = [], [], 0
        if do_letters:
            letter_to_apply, letter_skipped, n_sectioned = self._plan_letter_changes(groups, allow_duplicates)

            self.stdout.write(self.style.MIGRATE_HEADING("Letter sync"))
            if n_sectioned == 0:
                self.stdout.write(self.style.SUCCESS(
                    "  No combined group has a letter on its group_code or "
                    "base_course_code -- nothing to sync.\n"
                ))
            elif not letter_to_apply and not letter_skipped:
                self.stdout.write(self.style.SUCCESS(
                    f"  {n_sectioned} sectioned group(s) found, but every member already matches. Nothing to do.\n"
                ))
            else:
                self.stdout.write(
                    f"  {n_sectioned} sectioned group(s) checked. "
                    f"{len(letter_to_apply) + len(letter_skipped)} member row(s) need their letter resynced."
                )
                last_group_id = None
                for group, member, old_code, new_code in letter_to_apply:
                    if group.id != last_group_id:
                        dept_label = group.department.name if group.department_id else "(no department)"
                        self.stdout.write(f"    -- [{dept_label}] group '{group.group_code}' ({group.base_course_code}) --")
                        last_group_id = group.id
                    self.stdout.write(f"      #{member.id}  '{old_code}'  ->  '{new_code}'")
                if letter_skipped:
                    self.stdout.write(self.style.WARNING(f"  {len(letter_skipped)} row(s) SKIPPED -- would create a duplicate:"))
                    for group, member, old_code, new_code in letter_skipped:
                        dept_label = group.department.name if group.department_id else "(no department)"
                        self.stdout.write(self.style.WARNING(
                            f"    [{dept_label}] #{member.id}  '{old_code}' -> '{new_code}'  "
                            f"collides with another row. Pass --allow-duplicates to rewrite anyway."
                        ))
                self.stdout.write("")

        # ---------------- Apply ----------------
        total_planned = (
            len(group_lecturer_updates) + len(member_lecturer_updates) + len(letter_to_apply)
        )

        if not apply_changes:
            if total_planned:
                self.stdout.write(self.style.WARNING(
                    f"Dry run complete -- {total_planned} change(s) would be written "
                    f"({len(ambiguous)} group(s) still ambiguous, {len(letter_skipped)} letter "
                    f"row(s) would be skipped as duplicates). Re-run with --apply to write them."
                ))
            else:
                self.stdout.write(self.style.SUCCESS("Dry run complete -- nothing to change."))
            return

        fixed_groups = fixed_members_lecturer = fixed_members_letter = 0
        with transaction.atomic():
            for group, lecturer, _reason in group_lecturer_updates:
                group.lecturer = lecturer
                group.save(update_fields=["lecturer"])
                fixed_groups += 1

            for _group, member, _old_lecturer, new_lecturer in member_lecturer_updates:
                member.lecturer = new_lecturer
                member.save(update_fields=["lecturer"])
                fixed_members_lecturer += 1

            for _group, member, _old_code, new_code in letter_to_apply:
                member.course_code = new_code
                member.save(update_fields=["course_code"])
                fixed_members_letter += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. Filled {fixed_groups} group lecturer(s), corrected "
            f"{fixed_members_lecturer} stale member lecturer(s), resynced "
            f"{fixed_members_letter} member course-code letter(s)."
            + (f" {len(ambiguous)} group(s) still need manual lecturer resolution." if ambiguous else "")
            + (f" {len(letter_skipped)} letter row(s) still need manual duplicate resolution." if letter_skipped else "")
        ))
