# course_allocation/management/commands/normalize_course_codes.py
"""
Normalise CourseAllocation.course_code values that were corrupted by the
group-letter "compounding" bug, e.g.:

    MATH 221(ASTA)-G-G-G-G-G-G        ->  MATH 221-G
    MATH 221(EPHY)-G-G-G-G-G-G        ->  MATH 221-G
    MATH 221(ICHE)-H-H-H-H-H-H        ->  MATH 221-H
    PHYS 232(EPHY)-A-A-A-AA-A         ->  PHYS 232-A
    LITT 212-COMM-B-B-B-B-B-B         ->  LITT 212-B

Rule applied to each code:

    <base course code> [ (TAG) | -tag ] <-LETTER repeated 1..n times>
                                 |
                                 v
                       <base course code>-<LETTER>

i.e. the program-disambiguation tag ("(ASTA)" or "-COMM") is dropped, the
stacked letters are collapsed to ONE copy, and the base code is kept.

What it will NOT touch
----------------------
  * Codes with no group letter at all (e.g. "MATH 221", "MATH 221(EDU)").
  * Codes that are already clean ("ZOOL 143-A").
  * By default, codes with a tag + a SINGLE letter (e.g. "MATH 221(ASTA)-G").
    Those are what the allocator legitimately produces; only rows where the
    letter is stacked (the bug signature) are fixed. Pass --include-single
    if you want the tag removed from those too.
  * Codes whose stacked letters DISAGREE (e.g. "-A-B-A"). We can't know which
    is right, so they are listed for manual review and left as they are.
  * Rows where the fix would collide with another row's
    (program, course_code, intake, student_group) -- the model's own
    uniqueness rule. They are listed and skipped unless --allow-duplicates.

This is a TEXT-ONLY fix on the course_code column: nothing is merged or
deleted, and timetable entries (which point at the allocation by foreign
key) are unaffected.

DRY RUN by default. With --apply, a CSV of every old -> new change is
written first so the run can be reversed (see --backup-csv).

Usage:
    python manage.py normalize_course_codes                          # dry run, all departments
    python manage.py normalize_course_codes --apply
    python manage.py normalize_course_codes --department "Mathematics" --apply
    python manage.py normalize_course_codes --include-single --apply
    python manage.py normalize_course_codes --include-archived --apply
"""
import csv
import re
from collections import defaultdict
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from course_allocation.models import CourseAllocation, ArchivedCourseAllocation
from department_management.models import Department

# Same shapes as clean_shared_course_tags.py, kept here so the parsing is
# self-contained and unit-testable.

# Trailing run of one-or-more "-A" / "-AA" segments (1-2 letters each).
_LETTER_RUN_RE = re.compile(r'(?:[-/_]\s*[A-Za-z]{1,2}\s*)+$')
_LETTER_SEG_RE = re.compile(r'[-/_]\s*([A-Za-z]{1,2})')

# "(ASTA)" at the end of what's left after the letters are removed.
_PAREN_TAG_RE = re.compile(r'\s*\(\s*[A-Za-z0-9]+\s*\)\s*$')

# "-COMM" style tag: 3+ letters (1-2 letters would have been read as a group letter).
_DASH_TAG_RE = re.compile(r'\s*[-/_]\s*[A-Za-z]{3,}\s*$')


def normalize_code(code, include_single=False):
    """
    Return (new_code, status) where status is one of:

        "fixed"   -- new_code differs from code and should be written
        "same"    -- nothing to do (no letter, already clean, or single-letter
                     row and include_single is False)
        "mixed"   -- stacked letters disagree; needs a human (new_code == code)
    """
    raw = (code or "").strip()

    m = _LETTER_RUN_RE.search(raw)
    if not m:
        return code, "same"

    segments = _LETTER_SEG_RE.findall(raw[m.start():])
    base = raw[:m.start()].rstrip()

    # Tag removal: "(ASTA)" first, then "-COMM".
    base = _PAREN_TAG_RE.sub("", base).rstrip()
    base = _DASH_TAG_RE.sub("", base).rstrip()

    # The base must still look like a course code (contain the number).
    if not re.search(r"\d", base):
        return code, "same"

    # Stacked = more than one segment. Letters agree when every character
    # across all segments is the same letter (A-A-A-AA-A -> all 'A').
    stacked = len(segments) > 1
    chars = {c.lower() for seg in segments for c in seg}
    if len(chars) > 1:
        return code, "mixed"

    if not stacked and not include_single:
        return code, "same"

    letter = segments[-1]  # last segment, original case preserved
    new_code = f"{base}-{letter}"
    if new_code == raw:
        return code, "same"
    return new_code, "fixed"


class Command(BaseCommand):
    help = (
        "Collapse corrupted course codes like 'MATH 221(ASTA)-G-G-G-G-G-G' "
        "to 'MATH 221-G' (drops the program tag, keeps one group letter). "
        "Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument("--department", type=str, default=None,
                            help="Restrict to one department name (default: all).")
        parser.add_argument("--include-archived", action="store_true",
                            help="Also fix ArchivedCourseAllocation rows.")
        parser.add_argument("--include-single", action="store_true",
                            help="Also drop the tag from rows with a single (non-stacked) "
                                 "letter, e.g. 'MATH 221(ASTA)-G' -> 'MATH 221-G'.")
        parser.add_argument("--allow-duplicates", action="store_true",
                            help="Rename even if the result collides with another row's "
                                 "(program, course_code, intake, student_group).")
        parser.add_argument("--backup-csv", type=str, default=None,
                            help="Path for the old->new CSV written on --apply "
                                 "(default: course_code_fix_<timestamp>.csv).")
        parser.add_argument("--apply", action="store_true",
                            help="Actually write changes. Without this, only reports.")

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        include_single = options["include_single"]
        allow_dupes = options["allow_duplicates"]

        dept = None
        if options["department"]:
            try:
                dept = Department.objects.get(name__iexact=options["department"])
            except Department.DoesNotExist:
                raise CommandError(f"No department named '{options['department']}'.")
            except Department.MultipleObjectsReturned:
                raise CommandError(f"Multiple departments match '{options['department']}'.")

        targets = [("CourseAllocation", CourseAllocation)]
        if options["include_archived"]:
            targets.append(("ArchivedCourseAllocation", ArchivedCourseAllocation))

        all_changes = []  # (model_label, id, dept, old, new) for the CSV
        grand_fixed = grand_skipped = grand_mixed = 0

        for label, model in targets:
            # Occupancy is built from ALL rows (uniqueness is checked across
            # departments), even when --department narrows what we change.
            everything = list(model.objects.all().only(
                "id", "course_code", "program_id", "intake", "student_group_id"
            )) if self._has_field(model, "student_group") else list(
                model.objects.all().only("id", "course_code", "program_id", "intake")
            )

            def key(r):
                return (
                    getattr(r, "program_id", None),
                    getattr(r, "intake", None),
                    getattr(r, "student_group_id", None),
                )

            occupied = defaultdict(lambda: defaultdict(int))
            for r in everything:
                occupied[key(r)][(r.course_code or "").strip().lower()] += 1

            qs = model.objects.all().select_related("department")
            if dept is not None:
                qs = qs.filter(department=dept)
            rows = list(qs.order_by("department__name", "id"))

            fixed, mixed, skipped = [], [], []
            claimed = defaultdict(set)  # codes already claimed by a proposed rename
            for row in rows:
                new_code, status = normalize_code(row.course_code, include_single)
                if status == "same":
                    continue
                if status == "mixed":
                    mixed.append(row)
                    continue
                k = key(row)
                new_lower = new_code.strip().lower()
                old_lower = (row.course_code or "").strip().lower()
                # Collides if another *existing* row already has the new code,
                # or an earlier proposed rename in this run already took it.
                existing = occupied[k].get(new_lower, 0) - (1 if new_lower == old_lower else 0)
                if (existing > 0 or new_lower in claimed[k]) and not allow_dupes:
                    skipped.append((row, new_code))
                    continue
                claimed[k].add(new_lower)
                fixed.append((row, new_code))

            where = dept.name if dept else "all departments"
            self.stdout.write(
                f"\n{label}: {len(rows)} row(s) checked in {where}. "
                f"{len(fixed)} to fix, {len(skipped)} skipped (duplicate risk), "
                f"{len(mixed)} need manual review. "
                f"{'APPLYING' if apply_changes else 'DRY RUN -- pass --apply to write changes'}."
            )

            last = None
            for row, new_code in fixed:
                d = row.department.name if row.department_id else "(no department)"
                if d != last:
                    self.stdout.write(f"  -- {d} --")
                    last = d
                self.stdout.write(f"    #{row.id}  '{row.course_code}'  ->  '{new_code}'")

            for row, new_code in skipped:
                self.stdout.write(self.style.WARNING(
                    f"    #{row.id}  '{row.course_code}' -> '{new_code}'  SKIPPED: would duplicate "
                    f"another row in the same program/intake/student group "
                    f"(--allow-duplicates to force)."
                ))
            for row in mixed:
                self.stdout.write(self.style.WARNING(
                    f"    #{row.id}  '{row.course_code}'  REVIEW: stacked letters differ, "
                    f"can't tell which is correct -- fix by hand."
                ))

            grand_fixed += len(fixed)
            grand_skipped += len(skipped)
            grand_mixed += len(mixed)
            for row, new_code in fixed:
                all_changes.append((label, row.id,
                                    row.department.name if row.department_id else "",
                                    row.course_code, new_code))

            if apply_changes and fixed:
                with transaction.atomic():
                    for row, new_code in fixed:
                        row.course_code = new_code
                        row.save(update_fields=["course_code"])

        # CSV backup of exactly what was changed (written only on --apply).
        if apply_changes and all_changes:
            path = options["backup_csv"] or (
                f"course_code_fix_{datetime.now():%Y%m%d_%H%M%S}.csv"
            )
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["model", "id", "department", "old_course_code", "new_course_code"])
                w.writerows(all_changes)
            self.stdout.write(f"\nBackup of changes written to: {path}")

        self.stdout.write("")
        if not apply_changes and grand_fixed:
            self.stdout.write(self.style.WARNING(
                f"Dry run complete -- {grand_fixed} row(s) would be renamed, "
                f"{grand_skipped} skipped, {grand_mixed} need review. "
                f"Re-run with --apply to write them."
            ))
        elif apply_changes:
            self.stdout.write(self.style.SUCCESS(
                f"Done. Renamed {grand_fixed} row(s); {grand_skipped} skipped; "
                f"{grand_mixed} left for manual review."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))

    @staticmethod
    def _has_field(model, name):
        return any(f.name == name for f in model._meta.get_fields())
