# course_allocation/management/commands/strip_group_letters.py
"""
One-off cleanup for CourseAllocation.course_code (and, optionally,
ArchivedCourseAllocation.course_code) values that carry a leftover
split-group letter suffix that shouldn't be there any more, e.g.:

    ZOOL 143-A   ->   ZOOL 143
    ZOOL 143-B   ->   ZOOL 143
    HIST 151-C   ->   HIST 151
    COSC 471(EDU)-A -> COSC 471(EDU)     (the "(TAG)" part is left alone)

This is a TEXT-ONLY fix. It does NOT:
  - merge/consolidate number_of_students between rows
  - move Timetable / ExamTimetable / TempTimetable / ExamTempTimetable
    entries between rows
  - delete any row
It only rewrites the course_code field, exactly like lowercase_group_letters.py
does for letter casing. Every row keeps its own id, lecturer, students,
schedule, etc. -- only the text label changes.

Because this does NOT delete or merge rows, stripping the letter off two or
more sibling rows for the SAME (program, course_code, intake) will leave
them sharing an identical, no-longer-unique course_code. That's flagged
loudly in the report (and left in place if --allow-duplicates isn't passed)
because CourseAllocation.clean()'s own duplicate-code check would normally
block exactly that combination -- this command bypasses full_clean()'s
uniqueness check on purpose (it does not touch validate_unique-independent
fields), so a human should look at flagged sets before deciding whether the
letter should really come off, or whether those rows need an actual merge
(see scripts/merge_lettered_group_allocations.py for that separate,
row-consolidating operation).

Suffix detection reuses parse_code() from clean_shared_course_tags.py --
the same function that command uses to tell a group LETTER (always a
trailing "-A"/"-B"/... dash suffix) apart from a "(TAG)" program-
disambiguation block (always in parens, e.g. "COSC 471(EDU)"). This
matters: an earlier version of this command used its own regex and
mis-read "(EDU)" as a group letter -- reusing parse_code avoids that class
of bug and keeps both cleanup commands agreeing on what a "letter" is.

Recognised shapes (letter, optionally preceded by a "(TAG)" block):

    <base><digits> - LETTER(S)              ZOOL 143-A, COSC 471-AA
    <base><digits> ( TAG ) - LETTER(S)      COSC 471(EDU)-A
    <base><digits> ( LETTER(S) )            COSC 471(C)   -- bare parens with
                                             no dash are treated as TAG, not
                                             a letter, by parse_code (so
                                             something like "COSC 471(EDU)"
                                             alone is correctly left alone).

DRY RUN by default -- pass --apply to actually write changes.

Usage:
    python manage.py strip_group_letters                              # dry run, ALL departments
    python manage.py strip_group_letters --apply                      # apply, ALL departments
    python manage.py strip_group_letters --department "Zoology" --apply   # one department only
    python manage.py strip_group_letters --include-archived --apply
    python manage.py strip_group_letters --allow-duplicates --apply    # also strip letters
                                                                        # even when it creates
                                                                        # a duplicate code
"""
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from course_allocation.models import CourseAllocation, ArchivedCourseAllocation
from course_allocation.management.commands.clean_shared_course_tags import parse_code
from department_management.models import Department


def strip_letter(code):
    """
    Return (new_code, old_letter) if `code` ends in a recognised
    group-letter suffix, else (code, None). Uses parse_code() so a
    "(TAG)" program-disambiguation block is correctly preserved and never
    mistaken for a group letter.
    """
    base, tag, letter = parse_code(code)
    if letter is None:
        return code, None
    new_code = f"{base}({tag})" if tag else base
    return new_code, letter


class Command(BaseCommand):
    help = (
        "Strip the trailing group-letter suffix from CourseAllocation."
        "course_code (e.g. 'ZOOL 143-A' -> 'ZOOL 143') across EVERY "
        "department by default. TEXT ONLY -- does not merge rows, "
        "students, or schedules. Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--department", type=str, default=None,
            help="Restrict to one department name (default: all departments).",
        )
        parser.add_argument(
            "--include-archived", action="store_true",
            help="Also strip ArchivedCourseAllocation.course_code rows.",
        )
        parser.add_argument(
            "--allow-duplicates", action="store_true",
            help=(
                "Without this flag, a row is left untouched if stripping its "
                "letter would produce a course_code that collides with "
                "another row's (program, course_code, intake). Pass this to "
                "strip it anyway."
            ),
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually write changes. Without this flag, only reports what would happen.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        dept_name = options["department"]
        include_archived = options["include_archived"]
        allow_duplicates = options["allow_duplicates"]

        dept = None
        if dept_name:
            try:
                dept = Department.objects.get(name__iexact=dept_name)
            except Department.DoesNotExist:
                raise CommandError(f"No department named '{dept_name}'.")
            except Department.MultipleObjectsReturned:
                raise CommandError(f"Multiple departments match '{dept_name}'; be more specific.")

        targets = [("CourseAllocation", CourseAllocation)]
        if include_archived:
            targets.append(("ArchivedCourseAllocation", ArchivedCourseAllocation))

        total_changes = 0
        total_skipped_dupes = 0

        for label, model in targets:
            qs = model.objects.all().select_related("department")
            if dept is not None:
                qs = qs.filter(department=dept)
            rows = list(qs.order_by("department__name", "id"))

            # Existing (program, course_code_upper, intake) occupancy, so we
            # can tell whether stripping a letter would collide with a row
            # that ISN'T being changed (or already occupies the bare code).
            # Deliberately built from ALL rows in scope (every department
            # when --department isn't passed), since (program, course_code,
            # intake) uniqueness is checked across departments, not within one.
            occupied = defaultdict(set)
            for row in rows:
                intake = getattr(row, "intake", None)
                occupied[(row.program_id, intake)].add((row.course_code or "").strip().upper())

            proposed = []  # (row, old_code, new_code)
            for row in rows:
                new_code, old_letter = strip_letter(row.course_code)
                if old_letter is None:
                    continue
                proposed.append((row, row.course_code, new_code))

            if not proposed:
                self.stdout.write(self.style.SUCCESS(
                    f"{label}: no course_code values have a group-letter suffix "
                    f"({len(rows)} row(s) checked across "
                    f"{dept.name if dept else 'all departments'})."
                ))
                continue

            to_apply = []
            skipped = []
            for row, old_code, new_code in proposed:
                intake = getattr(row, "intake", None)
                key = (row.program_id, intake)
                new_upper = new_code.strip().upper()
                others = occupied[key] - {(old_code or "").strip().upper()}
                collides = new_upper in others
                if collides and not allow_duplicates:
                    skipped.append((row, old_code, new_code))
                else:
                    to_apply.append((row, old_code, new_code))

            self.stdout.write(
                f"\n{label}: {len(proposed)} row(s) have a group-letter suffix across "
                f"{dept.name if dept else 'all departments'}. "
                f"{'APPLYING' if apply_changes else 'DRY RUN -- pass --apply to write changes'}."
            )

            last_dept_name = None
            for row, old_code, new_code in to_apply:
                dept_name_for_row = row.department.name if row.department_id else "(no department)"
                if dept_name_for_row != last_dept_name:
                    self.stdout.write(f"  -- {dept_name_for_row} --")
                    last_dept_name = dept_name_for_row
                self.stdout.write(f"    #{row.id}  '{old_code}'  ->  '{new_code}'")

            for row, old_code, new_code in skipped:
                dept_name_for_row = row.department.name if row.department_id else "(no department)"
                self.stdout.write(self.style.WARNING(
                    f"    [{dept_name_for_row}] #{row.id}  '{old_code}'  ->  '{new_code}'  "
                    f"SKIPPED -- would duplicate another row's (program, course_code, intake). "
                    f"Pass --allow-duplicates to strip it anyway, or use "
                    f"scripts/merge_lettered_group_allocations.py to consolidate the sibling "
                    f"rows properly instead."
                ))

            total_skipped_dupes += len(skipped)

            if not apply_changes:
                total_changes += len(to_apply)
                continue

            fixed = 0
            with transaction.atomic():
                for row, old_code, new_code in to_apply:
                    row.course_code = new_code
                    row.save(update_fields=["course_code"])
                    fixed += 1
            total_changes += fixed

        self.stdout.write("")
        if not apply_changes and total_changes:
            self.stdout.write(self.style.WARNING(
                f"Dry run complete -- {total_changes} row(s) would be renamed "
                f"({total_skipped_dupes} would be skipped as duplicates). "
                f"Re-run with --apply to write them."
            ))
        elif apply_changes:
            self.stdout.write(self.style.SUCCESS(
                f"Done. Renamed {total_changes} row(s)."
                + (f" Skipped {total_skipped_dupes} duplicate-risk row(s)." if total_skipped_dupes else "")
            ))
        elif not total_changes:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
