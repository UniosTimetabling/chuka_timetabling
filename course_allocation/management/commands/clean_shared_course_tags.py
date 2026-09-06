# course_allocation/management/commands/clean_shared_course_tags.py
"""
One-off cleanup for CourseAllocation.course_code values left over from
earlier imports/allocation runs, where the "(TAG)" disambiguation suffix
and/or the "-A"/"-B" split-group suffix no longer match reality.

Background
----------
course_code can carry up to two suffixes, always in this order:

    <base code><(TAG)><-LETTER>          e.g.  FREN 211(EDU)-A

  * (TAG)   -- added by _build_course_code_labels() in
               course_allocation/auto_allocate_courses.py when the SAME
               base course code is offered by 2+ Programs (any
               department). TAG is that program's ProgramCode.code, or a
               best-effort initials guess when no ProgramCode exists.
               If a course is only ever offered by ONE program, it should
               have NO tag at all.

  * -LETTER -- added by make_group_code() when a course had to be split
               into multiple lecture groups (large class size). If a
               course only produced a single group, it should have NO
               letter suffix.

Real data (this is exactly the situation reported for the Education
department after a previous manual clean) can drift out of sync with
those two rules:

  * a course tagged "(EDU)-A" that is actually the ONLY group for that
    course any more (stray "-A" -- should just be "...( EDU)")
  * a course "FREN 211-A" that is the only "FREN 211" in the ENTIRE
    system, i.e. no other program uses that code (should be plain
    "FREN 211", no tag, no letter)
  * a course that IS shared with another program elsewhere but is
    currently stored untagged (needs "(TAG)" added)
  * a course split into several real groups within Education that
    should keep "(TAG)-A", "(TAG)-B", ... as-is

This command re-derives the correct course_code for every allocation in
a department (default: Education) using exactly those two rules and
rewrites any row that doesn't already match. Dry-run by default.

Usage:
    python manage.py clean_shared_course_tags                       # dry run, Education
    python manage.py clean_shared_course_tags --department "Education" --apply
    python manage.py clean_shared_course_tags --department "Law" --program 4 --apply
"""
import re
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.core.exceptions import ValidationError

from course_allocation.models import CourseAllocation
from course_allocation.config_helpers import normalize_course_code, make_group_code
from department_management.models import Department
from program_management.models import ProgramCode

# Trailing "-A", "-B", "-AA", ... group-split suffix (letters only -- a
# digit here means it's part of the code itself, e.g. "MATH-101", not a
# group letter, so it's deliberately left alone).
_GROUP_LETTER_RE = re.compile(r'\s*-\s*([A-Za-z]+)\s*$')

# Trailing "(TAG)" disambiguation suffix, captured (not just stripped).
_TAG_RE = re.compile(r'\s*\(([A-Za-z0-9]+)\)\s*$')

_INITIALS_STOPWORDS = {
    "bachelor", "master", "degree", "of", "in", "the", "and", "science",
    "arts", "doctor", "diploma", "certificate", "technology", "bsc", "ba",
}


def _generate_program_initials(program_name):
    """Same best-effort tag fallback used by auto_allocate_courses.py so a
    freshly-added tag matches what the live allocator would produce."""
    words = re.findall(r"[A-Za-z]+", program_name or "")
    significant = [w for w in words if w.lower() not in _INITIALS_STOPWORDS]
    if not significant:
        significant = words
    if not significant:
        return "PRG"
    if len(significant) == 1:
        return significant[0][:3].upper()
    *qualifiers, core = significant
    prefix = "".join(w[0] for w in qualifiers).upper()
    return f"{prefix}{core[:3].upper()}"


def parse_code(raw):
    """Split a stored course_code into (base_code, tag_or_None, letter_or_None)."""
    remaining = (raw or "").strip()

    letter = None
    m = _GROUP_LETTER_RE.search(remaining)
    if m:
        letter = m.group(1).upper()
        remaining = remaining[:m.start()].rstrip()

    tag = None
    m = _TAG_RE.search(remaining)
    if m:
        tag = m.group(1).upper()
        remaining = remaining[:m.start()].rstrip()

    return remaining, tag, letter


def resolve_tag_for_program(program, code_cache={}):
    """ProgramCode.code if the program has one, else generated initials."""
    if program.id not in code_cache:
        pc = ProgramCode.objects.filter(program=program).first()
        code_cache[program.id] = (pc.code if pc else _generate_program_initials(program.name)).upper()
    return code_cache[program.id]


class Command(BaseCommand):
    help = (
        "Re-derive the correct '(TAG)' / '-LETTER' suffixes on "
        "CourseAllocation.course_code for a department, based on whether "
        "the base course code is shared with other programs system-wide "
        "and how many real split-groups currently exist for it. "
        "Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--department", type=str, default="Education",
            help="Department name to clean (default: Education).",
        )
        parser.add_argument(
            "--program", type=int, default=None,
            help="Restrict to a single Program id within that department.",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually write changes. Without this flag, only reports what would happen.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        dept_name = options["department"]
        program_id = options["program"]

        try:
            dept = Department.objects.get(name__iexact=dept_name)
        except Department.DoesNotExist:
            raise CommandError(f"No department named '{dept_name}'.")
        except Department.MultipleObjectsReturned:
            raise CommandError(f"Multiple departments match '{dept_name}'; be more specific.")

        # ── 1. Build a system-wide index of base_code -> {program_ids} ─────
        # so we know, for every course, whether ANY program other than its
        # own currently has an allocation for that same base code. This has
        # to look at ALL departments, not just the one being cleaned.
        all_rows = list(
            CourseAllocation.objects.all().select_related("program", "program_course")
        )
        programs_by_base = defaultdict(set)
        for r in all_rows:
            base, _tag, _letter = parse_code(r.course_code)
            programs_by_base[normalize_course_code(base)].add(r.program_id)

        # ── 2. Rows to actually clean ───────────────────────────────────────
        qs = CourseAllocation.objects.filter(department=dept).select_related(
            "program", "program_course"
        )
        if program_id:
            qs = qs.filter(program_id=program_id)
        target_rows = list(qs.order_by("program_id", "course_code", "id"))

        if not target_rows:
            self.stdout.write(self.style.WARNING(
                f"No CourseAllocation rows found for department '{dept.name}'"
                + (f" / program {program_id}" if program_id else "") + "."
            ))
            return

        # ── 3. Group target rows into split-siblings ───────────────────────
        # Siblings = same program + same intake + same underlying semester +
        # same base code -- these are the rows that, together, represent
        # every group of ONE course for ONE program. Only rows within a
        # sibling set may share a -LETTER suffix.
        sibling_key = {}
        siblings = defaultdict(list)
        for r in target_rows:
            base, tag, letter = parse_code(r.course_code)
            semester = r.program_course.semester if r.program_course else None
            key = (r.program_id, r.intake, semester, normalize_course_code(base))
            sibling_key[r.id] = key
            siblings[key].append(r)

        # ── 4. Decide + apply the correct code per row ──────────────────────
        changes = []  # (row, old_code, new_code)
        tag_cache = {}

        for key, rows in siblings.items():
            program_id_, intake, semester, norm_base = key
            program = rows[0].program
            needs_letters = len(rows) > 1

            # Stable ordering for re-lettering: by existing letter (rows
            # already lettered A, B, ... keep that relative order), then by
            # id for anything untagged/unlettered so ties are deterministic.
            def sort_key(row):
                _b, _t, letter = parse_code(row.course_code)
                return (letter or "~", row.id)

            ordered_rows = sorted(rows, key=sort_key)

            for idx, row in enumerate(ordered_rows):
                base, old_tag, old_letter = parse_code(row.course_code)

                other_programs = programs_by_base.get(norm_base, set()) - {program_id_}
                needs_tag = bool(other_programs)

                if needs_tag:
                    if old_tag:
                        tag = old_tag  # already tagged -- keep the existing tag text as-is
                    else:
                        tag = resolve_tag_for_program(program, tag_cache)
                else:
                    tag = None

                base_with_tag = f"{base}({tag})" if tag else base
                new_code = make_group_code(base_with_tag, idx) if needs_letters else base_with_tag

                if new_code != row.course_code:
                    changes.append((row, row.course_code, new_code))

        # ── 5. Report / apply ───────────────────────────────────────────────
        if not changes:
            self.stdout.write(self.style.SUCCESS(
                f"'{dept.name}': every course_code already matches the shared/"
                f"grouping rules ({len(target_rows)} row(s) checked). Nothing to do."
            ))
            return

        self.stdout.write(
            f"'{dept.name}': {len(changes)} of {len(target_rows)} row(s) need fixing. "
            f"{'APPLYING' if apply_changes else 'DRY RUN -- pass --apply to write changes'}."
        )
        for row, old_code, new_code in changes:
            self.stdout.write(f"  #{row.id}  program={row.program.name!r:<40}  '{old_code}'  ->  '{new_code}'")

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                "Dry run complete -- no changes written. Re-run with --apply to fix them."
            ))
            return

        fixed, failed = 0, 0
        with transaction.atomic():
            for row, old_code, new_code in changes:
                row.course_code = new_code
                try:
                    row.full_clean(validate_unique=False)
                    row.save(update_fields=["course_code"])
                    fixed += 1
                except ValidationError as e:
                    failed += 1
                    self.stdout.write(self.style.ERROR(
                        f"  #{row.id}  '{old_code}' -> '{new_code}' FAILED: {e}"
                    ))

        if failed:
            self.stdout.write(self.style.WARNING(f"Done with errors. Fixed {fixed}, {failed} failed (see above)."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Done. Fixed {fixed} row(s)."))
