# course_allocation/management/commands/lowercase_group_letters.py
"""
One-off fix for CourseAllocation.course_code (and, optionally,
ArchivedCourseAllocation.course_code) values that carry a split/combined
-GROUP letter suffix.

Background
----------
Everywhere this suffix gets generated today -- make_group_code() in
course_allocation/config_helpers.py, append_group()/_lettered_code_for_group()
in course_management/cod_panel.py, and any manual entry -- it's produced (or
typed) in UPPERCASE, e.g.:

    HIST 151-B
    BCOM 403-D
    EDFO 111 AB      (space separator, no dash)
    COSC 471(EDU)-AA (with a program-disambiguation tag in between)

Now that individual-group rows get folded into a CombinedCourseGroup, the
convention is that the trailing letter(s) should read lowercase instead --
e.g. "HIST 151-b", "BCOM 403-d", "EDFO 111 ab" -- while the base course code
(and any "(TAG)") stays exactly as it was.

Suffix detection mirrors course_management.cod_panel.strip_group_suffix --
same three recognised shapes:

    <base><digits> - LETTER(S)      COSC 471-A, COSC 471-AA
    <base><digits> / LETTER(S)      COSC 471/A
    <base><digits> _ LETTER(S)      COSC 471_A
    <base><digits>   LETTER(S)      EDFO 111 AB   (bare space, no separator char)
    <base><digits>LETTER(S)         COSC471A      (no separator at all)
    <base><digits> ( LETTER(S) )    COSC 471(C), COSC471(AC)

Unlike strip_group_suffix, this script does NOT re-derive or move anything --
it only lowercases the letters it finds, in place, and leaves every other
character (spacing, dashes, parens, the "(TAG)" block if present) untouched.
That keeps it safe to run even on rows clean_shared_course_tags.py hasn't
touched yet.

DRY RUN by default -- pass --apply to actually write changes.

Usage:
    python manage.py lowercase_group_letters                          # dry run, all rows
    python manage.py lowercase_group_letters --apply
    python manage.py lowercase_group_letters --department "Education" --apply
    python manage.py lowercase_group_letters --include-archived --apply
"""
import re

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import transaction

from course_allocation.models import CourseAllocation, ArchivedCourseAllocation
from department_management.models import Department

# Parenthesis form: ...471(C), ...471(AC) -- captures prefix up to and
# including "(", the letters, and the ")" + any trailing whitespace as-is.
_PAREN_RE = re.compile(
    r'^(?P<prefix>.*?\d+\s*\(\s*)(?P<letter>[A-Za-z]+)(?P<suffix>\s*\)\s*)$'
)

# Every other form: digit run, then an optional "(TAG)" disambiguation
# block (e.g. "COSC 471(EDU)-A"), then an optional separator (-, /, _, bare
# space, or nothing), then the letters. Separator/tag/whitespace are
# preserved exactly as captured in `prefix`.
_SEP_RE = re.compile(
    r'^(?P<prefix>.*?\d+\s*(?:\([A-Za-z0-9]+\)\s*)?[-/_]?\s*)(?P<letter>[A-Za-z]+)(?P<suffix>\s*)$'
)


def lowercase_suffix(code):
    """
    Return (new_code, old_letter, new_letter) if `code` ends in a group-letter
    suffix that isn't already lowercase, else (code, None, None).
    """
    raw = code or ""

    m = _PAREN_RE.match(raw)
    if not m:
        m = _SEP_RE.match(raw)

    if not m:
        return code, None, None

    prefix, letter, suffix = m.group("prefix"), m.group("letter"), m.group("suffix")

    # Guard against matching a bare course code with no real group suffix,
    # e.g. "COSC" alone -- prefix must actually contain the course number.
    if not re.search(r"\d", prefix):
        return code, None, None

    if letter == letter.lower():
        return code, None, None  # already lowercase, nothing to do

    new_code = f"{prefix}{letter.lower()}{suffix}"
    return new_code, letter, letter.lower()


class Command(BaseCommand):
    help = (
        "Lowercase the trailing group-letter suffix on CourseAllocation."
        "course_code (e.g. 'HIST 151-B' -> 'HIST 151-b', 'EDFO 111 AB' -> "
        "'EDFO 111 ab'). Base code and any '(TAG)' are left untouched. "
        "Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--department", type=str, default=None,
            help="Restrict to one department name (default: all departments).",
        )
        parser.add_argument(
            "--include-archived", action="store_true",
            help="Also fix ArchivedCourseAllocation.course_code rows.",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually write changes. Without this flag, only reports what would happen.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        dept_name = options["department"]
        include_archived = options["include_archived"]

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
        total_failed = 0

        for label, model in targets:
            qs = model.objects.all()
            if dept is not None:
                qs = qs.filter(department=dept)
            rows = list(qs.order_by("id"))

            changes = []
            for row in rows:
                new_code, old_letter, new_letter = lowercase_suffix(row.course_code)
                if old_letter is not None:
                    changes.append((row, row.course_code, new_code))

            if not changes:
                self.stdout.write(self.style.SUCCESS(
                    f"{label}: no course_code values need a lowercase fix "
                    f"({len(rows)} row(s) checked)."
                ))
                continue

            self.stdout.write(
                f"{label}: {len(changes)} of {len(rows)} row(s) need fixing. "
                f"{'APPLYING' if apply_changes else 'DRY RUN -- pass --apply to write changes'}."
            )
            for row, old_code, new_code in changes:
                self.stdout.write(f"  #{row.id}  '{old_code}'  ->  '{new_code}'")

            if not apply_changes:
                total_changes += len(changes)
                continue

            fixed, failed = 0, 0
            with transaction.atomic():
                for row, old_code, new_code in changes:
                    row.course_code = new_code
                    try:
                        if hasattr(row, "full_clean"):
                            row.full_clean(validate_unique=False)
                        row.save(update_fields=["course_code"])
                        fixed += 1
                    except ValidationError as e:
                        failed += 1
                        self.stdout.write(self.style.ERROR(
                            f"  #{row.id}  '{old_code}' -> '{new_code}' FAILED: {e}"
                        ))
            total_changes += fixed
            total_failed += failed

        if not apply_changes and total_changes:
            self.stdout.write(self.style.WARNING(
                "\nDry run complete -- no changes written. Re-run with --apply to fix them."
            ))
        elif apply_changes:
            if total_failed:
                self.stdout.write(self.style.WARNING(
                    f"\nDone with errors. Fixed {total_changes}, {total_failed} failed (see above)."
                ))
            else:
                self.stdout.write(self.style.SUCCESS(f"\nDone. Fixed {total_changes} row(s)."))
