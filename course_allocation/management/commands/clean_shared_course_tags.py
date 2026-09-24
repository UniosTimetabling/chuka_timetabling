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
from course_allocation.config_helpers import normalize_course_code
from department_management.models import Department
from program_management.models import ProgramCode

# Trailing "-A", "-B", "-AA", ... group-split suffix: ONE OR MORE stacked
# segments, each capped at 1-2 letters (Excel-column style, matching
# make_group_code()'s own convention -- a real course is never split into
# 27+ lecture sections). Matching the WHOLE stacked run in one shot (not
# just the last segment) is what lets this fully repair rows already
# corrupted by the make_group_code() compounding bug in one pass, instead
# of only peeling off one "-C" per run and leaving "-C-C-C-C-C" behind.
# The {1,2} cap also means a genuine (longer) dash-style tag like "-BIO"
# is correctly left alone rather than being misread as a group letter.
_GROUP_LETTER_RUN_RE = re.compile(r'(?:[-/_]\s*[A-Za-z]{1,2}\s*)+$')

# Trailing "(TAG)" disambiguation suffix, captured (not just stripped).
_PAREN_TAG_RE = re.compile(r'\s*\(([A-Za-z0-9]+)\)\s*$')

# Trailing "-tag" dash-style disambiguation suffix, e.g. "CHEM 323-bio".
# Only 3+ letters count as a tag here -- anything 1-2 letters is a group
# letter and _GROUP_LETTER_RUN_RE above has already stripped it before
# this ever runs, so there's no ambiguity between the two. Written this
# way, the tag is conventionally lowercase (unlike the "(TAG)" form,
# which is uppercase) -- see the rebuild step below, which reproduces
# whichever form/case the tag was actually found in.
_DASH_TAG_RE = re.compile(r'[-/_]\s*([A-Za-z]{3,})\s*$')

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


def _index_to_letter(n):
    """0->A, 1->B, ..., 25->Z, 26->AA, ... -- same Excel-column style the
    rest of this module already assumes (see _GROUP_LETTER_RUN_RE above).
    Only used to mint a BRAND NEW letter for a row that doesn't have one
    yet; a row's own existing letter is always preserved as-is instead of
    being recomputed through this."""
    n += 1
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def dedupe_letter_run(raw):
    """Collapse a trailing run of stacked group-letter segments down to a
    single instance of the real letter, and leave literally everything
    else about the code -- base, tag, tag's original form/case -- exactly
    as found. No tag logic, no sibling grouping, no dropping a letter just
    because a row looks like a singleton: if a letter is there, one copy
    of it stays; if there's no letter at all, the code is untouched."""
    remaining = (raw or "").strip()
    m = _GROUP_LETTER_RUN_RE.search(remaining)
    if not m:
        return remaining
    prefix = remaining[:m.start()].rstrip()
    run = remaining[m.start():]
    last_seg = re.search(r'[A-Za-z]{1,2}$', run)
    if not last_seg:
        return remaining
    letter = last_seg.group(0).upper()
    return f"{prefix}-{letter}"


def parse_code(raw):
    """Split a stored course_code into (base_code, tag_or_None, letter_or_None).

    Strips the ENTIRE trailing run of group-letter segments in one pass,
    not just the last one -- so an already-corrupted "CHEM 323-C-C-C-C-C-C"
    parses straight to base "CHEM 323" / letter "C" instead of needing six
    separate runs of this command to peel off one "-C" at a time.
    """
    remaining = (raw or "").strip()

    letter = None
    m = _GROUP_LETTER_RUN_RE.search(remaining)
    if m:
        run = remaining[m.start():]
        remaining = remaining[:m.start()].rstrip()
        # Stacked segments are always identical in practice (that's the
        # compounding bug); the one nearest the end is the real letter.
        last_seg = re.search(r'[A-Za-z]{1,2}$', run)
        if last_seg:
            letter = last_seg.group(0).upper()

    tag = None
    m = _PAREN_TAG_RE.search(remaining)
    if m:
        tag = m.group(1).upper()
        remaining = remaining[:m.start()].rstrip()
    else:
        m = _DASH_TAG_RE.search(remaining)
        if m:
            # Preserve the dash form's own lowercase convention here --
            # rebuilt later as "base-tag", not "base(TAG)".
            tag = m.group(1).lower()
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
        parser.add_argument(
            "--letters-only", action="store_true",
            help=(
                "Only collapse duplicate stacked group-letter suffixes "
                "(e.g. '-B-B-B-B-B-B' -> '-B'). Does not add, remove, or "
                "change any '(TAG)' / '-tag' suffix, and never drops a "
                "letter a row already has -- just dedupes it to one copy."
            ),
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

        if options["letters_only"]:
            changes = []
            for row in target_rows:
                new_code = dedupe_letter_run(row.course_code)
                if new_code != row.course_code:
                    changes.append((row, row.course_code, new_code))

            if not changes:
                self.stdout.write(self.style.SUCCESS(
                    f"'{dept.name}': no stacked/duplicate letter suffixes found "
                    f"({len(target_rows)} row(s) checked). Nothing to do."
                ))
                return

            self.stdout.write(
                f"'{dept.name}': {len(changes)} of {len(target_rows)} row(s) have "
                f"duplicate letter suffixes. "
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
            return

        # ── 3. Group target rows into split-siblings ───────────────────────
        # Siblings = same program + same intake + same underlying semester +
        # same base code + same TAG -- these are the rows that, together,
        # represent every group of ONE course (or ONE specialization track
        # of that course) for ONE program. Only rows within a sibling set
        # may share a -LETTER suffix.
        #
        # The tag MUST be part of this key: "PHYS 232-comp" and
        # "PHYS 232-geo" are two distinct specialization tracks that just
        # happen to share a base course and program -- they are NOT split
        # lecture sections of one class. Leaving tag out of the key used to
        # lump them into one sibling set and renumber them together (e.g.
        # both originally "-A" ending up re-lettered "-C" / "-D"), silently
        # moving a track into a completely different, wrong group.
        sibling_key = {}
        siblings = defaultdict(list)
        for r in target_rows:
            base, tag, letter = parse_code(r.course_code)
            semester = r.program_course.semester if r.program_course else None
            key = (r.program_id, r.intake, semester, normalize_course_code(base), (tag or "").lower())
            sibling_key[r.id] = key
            siblings[key].append(r)

        # ── 4. Decide + apply the correct code per row ──────────────────────
        changes = []  # (row, old_code, new_code)
        tag_cache = {}

        for key, rows in siblings.items():
            program_id_, intake, semester, norm_base, _key_tag = key
            program = rows[0].program
            needs_letters = len(rows) > 1

            # Ordering here only matters for handing out BRAND NEW letters
            # below (rows that don't have one yet) -- it no longer decides
            # what letter an already-lettered row ends up with. Rows that
            # already carry a real letter sort first, in their existing
            # letter order; unlettered rows come after, by id.
            def sort_key(row):
                _b, _t, letter = parse_code(row.course_code)
                return (0, letter, row.id) if letter else (1, "", row.id)

            ordered_rows = sorted(rows, key=sort_key)

            # Preserve every row's own existing letter as-is. Recomputing a
            # letter from a row's position in this list is exactly what
            # caused the earlier bugs: a row that's already correctly
            # "-B" would get silently reassigned to whatever letter its
            # array index happened to land on, or dropped altogether. Only
            # a row that doesn't have a letter yet gets a freshly minted
            # one, and that new letter skips anything already taken in
            # this sibling set so it can never collide with a preserved one.
            used_letters = {
                parse_code(r.course_code)[2] for r in ordered_rows
            } - {None}
            letter_for = {}
            next_idx = 0
            for row in ordered_rows:
                _b, _t, existing_letter = parse_code(row.course_code)
                if existing_letter:
                    letter_for[row.id] = existing_letter
                    continue
                candidate = _index_to_letter(next_idx)
                while candidate in used_letters:
                    next_idx += 1
                    candidate = _index_to_letter(next_idx)
                letter_for[row.id] = candidate
                used_letters.add(candidate)
                next_idx += 1

            for row in ordered_rows:
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

                # Reproduce whichever tag form this row already used --
                # dash-lowercase ("CHEM 323-bio") stays dash-lowercase,
                # parens-uppercase ("CHEM 323(ICHE)") stays parens-uppercase.
                # A brand-new tag (no old_tag to preserve) keeps the
                # original parens-uppercase default.
                if tag:
                    base_with_tag = f"{base}-{tag}" if tag.islower() else f"{base}({tag})"
                else:
                    base_with_tag = base
                new_code = f"{base_with_tag}-{letter_for[row.id]}" if needs_letters else base_with_tag

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