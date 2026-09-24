# program_management/management/commands/strip_program_course_group_suffix.py
"""
One-off cleanup for ProgramCourse.course_code values that carry a
group-letter (or other split-group) suffix that should never have been
typed into course_code in the first place -- e.g.:

    COSC 223 A   ->  COSC 223
    ECON 121-K   ->  ECON 121
    BCOM403_D    ->  BCOM 403
    HIST151(B)   ->  HIST 151

A ProgramCourse row represents one curriculum entry for a program --
group letters belong to CourseAllocation (see
course_allocation/config_helpers.py make_group_code() and
lowercase_group_letters.py), never to ProgramCourse.course_code. When a
lettered code gets typed/imported into ProgramCourse anyway, every
lookup that expects the clean 'LETTERS 123' form (COD panel curriculum
matching, CourseAllocation auto-resolve, exports, reports...) either
misses the row entirely or creates a second, "duplicate-looking" entry
for what's really the same course.

Relationship to cleanup_unlinked_program_course_duplicates
------------------------------------------------------------
That command *also* strips this same suffix (via base_course_key), but
it only acts on groups of 2+ rows that collapse to the same base code
-- e.g. 'ASC 110 A' + 'ASC 110 B' both present. It deliberately leaves
a *lone* 'COSC 223 A' alone, because with nothing to merge it against,
renaming it isn't a "duplicate cleanup", it's a straight rename -- which
is exactly the more common case reported in practice (one curriculum
row with a suffix that was never supposed to be there, no sibling row
sharing the same base code).

This command handles BOTH:
  - A lone dirty row (no other row shares its base code): renamed
    in place to the clean 'LETTERS 123' form.
  - Multiple rows that share a base code once suffixes are stripped
    (whether or not any of them was already clean): consolidated onto
    one canonical row exactly the way cleanup_unlinked_program_course_
    duplicates does -- preferring an already-clean/most-referenced row
    as the keeper, repointing every FK/M2M reference (CourseAllocation,
    LabAllocation, SelectionGroup/BaseSelection, LecturerCourseMapping,
    etc. -- discovered by reflection, so new relations are picked up
    automatically), and deleting the losers.

So running this command is a strict superset of running
cleanup_unlinked_program_course_duplicates -- you do not need to run
both.

ArchivedCourseAllocation is ignored (as in the sibling commands): known
to hold stale/test data, and its FK is on_delete=SET_NULL, so deleting
a duplicate never loses that archived row, it just clears the pointer.

DRY RUN by default -- pass --apply to actually write changes.

Usage:
    python manage.py strip_program_course_group_suffix              # dry run, all programs
    python manage.py strip_program_course_group_suffix --apply
    python manage.py strip_program_course_group_suffix --program 7 --apply
"""
import re
from collections import defaultdict

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import models as django_models
from django.db import transaction, IntegrityError

from program_management.models import ProgramCourse
from program_management.code_utils import base_course_key, normalize_code

CLEAN_CODE_RE = re.compile(r"^[A-Z]+ \d{2,5}$")
BASE_KEY_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _is_clean(code):
    """True if code is already exactly 'LETTERS 123' with nothing trailing."""
    return bool(CLEAN_CODE_RE.match(normalize_code(code or "")))


def _display_form(base_key):
    """'ASC110' -> 'ASC 110' for a newly-created/renamed canonical row."""
    m = BASE_KEY_RE.match(base_key)
    return f"{m.group(1)} {m.group(2)}" if m else base_key


def _pick_canonical_duplicate(rows):
    """Prefer a row already in normalize_code() form, tie-broken by lowest id."""
    well_formatted = [pc for pc in rows if pc.course_code == normalize_code(pc.course_code)]
    pool = well_formatted or rows
    return min(pool, key=lambda pc: pc.id)


def _discover_referencing_fields():
    """
    Reflect over every installed model to find every FK/M2M field that
    points at ProgramCourse, mirroring cleanup_unlinked_program_course_
    duplicates.py, so a relation added later is picked up automatically.
    ArchivedCourseAllocation is deliberately excluded (see module docstring).
    """
    from course_allocation.models import ArchivedCourseAllocation

    fk_fields = []
    m2m_fields = []
    for model in apps.get_models():
        if model is ArchivedCourseAllocation:
            continue
        for field in model._meta.get_fields():
            if isinstance(field, django_models.ManyToManyField) and field.related_model is ProgramCourse:
                m2m_fields.append((model, field.name))
            elif isinstance(field, django_models.ForeignKey) and field.related_model is ProgramCourse:
                fk_fields.append((model, field.name))
    return fk_fields, m2m_fields


def _repoint(loser, keeper, fk_fields, m2m_fields):
    """Move every reference from `loser` onto `keeper` (see module docstring)."""
    for model, field_name in m2m_fields:
        for instance in model.objects.filter(**{field_name: loser}):
            getattr(instance, field_name).remove(loser)
            getattr(instance, field_name).add(keeper)

    for model, field_name in fk_fields:
        related_qs = model.objects.filter(**{field_name: loser})
        try:
            with transaction.atomic():
                related_qs.update(**{field_name: keeper})
        except IntegrityError:
            for instance in related_qs:
                setattr(instance, field_name, keeper)
                try:
                    with transaction.atomic():
                        instance.save()
                except IntegrityError:
                    # Keeper already has an equivalent row -- the loser's
                    # copy is now redundant.
                    instance.delete()


def _choose_keeper(rows, linked, linked_pks, base_key):
    """Returns (keeper, is_new). See cleanup_unlinked_program_course_duplicates."""
    clean = [pc for pc in rows if _is_clean(pc.course_code)]
    if clean:
        clean_linked = [pc for pc in clean if pc.pk in linked_pks]
        pool = clean_linked or clean
        return min(pool, key=lambda pc: pc.id), False

    if len(linked) >= 2:
        primary = max(linked, key=lambda pair: pair[1])[0]
        new_pc = ProgramCourse(
            program=primary.program,
            course_code=_display_form(base_key),
            course_name=primary.course_name,
            year=primary.year,
            semester=primary.semester,
            unit_type=primary.unit_type,
            student_cohort=primary.student_cohort,
        )
        return new_pc, True

    keeper = linked[0][0] if linked else _pick_canonical_duplicate(rows)
    keeper.course_code = _display_form(base_key)
    return keeper, False


class Command(BaseCommand):
    help = (
        "Strip stray group-letter/tag suffixes off ProgramCourse.course_code "
        "(e.g. 'COSC 223 A' -> 'COSC 223', 'ECON 121-K' -> 'ECON 121'), "
        "renaming lone rows in place and consolidating any rows that turn "
        "out to share the same base code once suffixes are stripped. "
        "Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually write changes. Without this flag, only reports what would happen.",
        )
        parser.add_argument(
            "--program", type=int, default=None,
            help="Restrict to a single Program id (recommended: test on one program first).",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        program_id = options["program"]

        qs = ProgramCourse.objects.all().order_by("id")
        if program_id:
            qs = qs.filter(program_id=program_id)

        fk_fields, m2m_fields = _discover_referencing_fields()

        def reference_count(pc):
            total = 0
            for model, field_name in fk_fields:
                total += model.objects.filter(**{field_name: pc}).count()
            for model, field_name in m2m_fields:
                total += model.objects.filter(**{field_name: pc}).count()
            return total

        groups = defaultdict(list)
        for pc in qs:
            key = (pc.program_id, pc.student_cohort or "0", base_course_key(pc.course_code))
            groups[key].append(pc)

        # Unlike cleanup_unlinked_program_course_duplicates, we ALSO act on
        # a group of exactly 1 row when that row isn't already clean --
        # that's the lone 'COSC 223 A' case with no sibling row to merge
        # against, which is the common real-world shape of this problem.
        #
        # IMPORTANT: a lone row only belongs in this report if stripping
        # its group suffix would actually CHANGE the stored value. Using
        # _is_clean() alone is not enough -- a code with no digits at all
        # ('COMM'), a typo with no contiguous digit run ('ZOOL1O3', letter
        # O instead of zero), or a code with no leading letters ('344',
        # '003144101A') will never match the strict 'LETTERS 123' pattern
        # either, but there's no group suffix on them to strip, so
        # base_course_key()/_display_form() reconstructs the exact same
        # value -- that's a false positive, not a fix, and would just be
        # noise in the report. We only keep a lone row if the recomputed
        # code differs from the original (byte-for-byte, so this also
        # correctly catches invisible-character junk -- e.g. a trailing
        # zero-width space from a CSV/Excel import -- that looks identical
        # when printed but breaks lookups just like a real group suffix).
        def _lone_row_candidate(pc):
            candidate = _display_form(base_course_key(pc.course_code))
            return candidate if candidate != (pc.course_code or "") else None

        target_groups = {}
        lone_candidates = {}
        for k, v in groups.items():
            if len(v) > 1:
                target_groups[k] = v
            else:
                candidate = _lone_row_candidate(v[0])
                if candidate is not None:
                    target_groups[k] = v
                    lone_candidates[v[0].pk] = candidate

        if not target_groups:
            self.stdout.write(self.style.SUCCESS(
                "No ProgramCourse.course_code values need a group-suffix fix."
            ))
            return

        rename_only = {k: v for k, v in target_groups.items() if len(v) == 1}
        merge_groups = {k: v for k, v in target_groups.items() if len(v) > 1}

        self.stdout.write(
            f"Found {len(target_groups)} row(s)/group(s) to fix: "
            f"{len(rename_only)} lone row(s) to rename, {len(merge_groups)} "
            f"duplicate group(s) to consolidate "
            f"({sum(len(v) for v in target_groups.values())} rows total). "
            f"{'APPLYING' if apply_changes else 'DRY RUN -- pass --apply to write changes'}."
        )

        rows_deleted = 0
        rows_merged = 0
        rows_renamed = 0
        new_rows_created = 0

        for key, rows in sorted(target_groups.items()):
            program_id_, cohort, base_key = key
            program_name = rows[0].program.name if rows else "?"

            # Capture the true "before" value NOW, before _choose_keeper()
            # potentially mutates a row's course_code attribute in place
            # (when the keeper it returns IS one of the existing rows,
            # e.g. the lone-row rename case, keeper and rows[0] are the
            # same Python object -- printing rows[0].course_code AFTER
            # that call would show the already-changed value on both
            # sides of the arrow).
            original_codes = {pc.pk: pc.course_code for pc in rows}

            annotated = [(pc, reference_count(pc)) for pc in rows]
            linked = [(pc, n) for pc, n in annotated if n > 0]
            linked_pks = {pc.pk for pc, _ in linked}

            keeper, keeper_is_new = _choose_keeper(rows, linked, linked_pks, base_key)
            losers = list(rows) if keeper_is_new else [pc for pc in rows if pc.pk != keeper.pk]
            loser_info = [(pc, next((n for p, n in annotated if p.pk == pc.pk), 0)) for pc in losers]

            header = f"  Program {program_id_} ({program_name}) / cohort {cohort} / base code {base_key}:"
            self.stdout.write(header)

            if len(rows) == 1:
                original_code = original_codes[keeper.pk]
                note = ""
                if original_code.strip() == keeper.course_code.strip() and original_code != keeper.course_code:
                    note = "  (removed invisible/non-standard character(s) -- text looks identical)"
                self.stdout.write(
                    f"      RENAME #{keeper.pk} {original_code!r} -> {keeper.course_code!r}{note}"
                )
            elif keeper_is_new:
                to_merge = [pc for pc, n in loser_info if n > 0]
                self.stdout.write(
                    f"      CREATE '{keeper.course_code}' (Y{keeper.year}S{keeper.semester}) -- "
                    f"new canonical row absorbing {len(to_merge)} referenced row(s)"
                )
            else:
                keeper_original = original_codes.get(keeper.pk)
                rename_note = (
                    f" (renamed from {keeper_original!r})"
                    if keeper_original is not None and keeper_original != keeper.course_code
                    else ""
                )
                self.stdout.write(
                    f"      KEEP   #{keeper.pk} '{keeper.course_code}'{rename_note} (Y{keeper.year}S{keeper.semester})"
                )
                for pc, n in loser_info:
                    tag = "MERGE " if n > 0 else "DELETE"
                    self.stdout.write(f"      {tag} #{pc.pk} {original_codes.get(pc.pk, pc.course_code)!r} -- {n} reference(s)")

            if not apply_changes:
                continue

            with transaction.atomic():
                target_code = keeper.course_code if keeper_is_new else normalize_code(keeper.course_code)
                for pc, n in loser_info:
                    if pc.pk and normalize_code(pc.course_code) == target_code:
                        ProgramCourse.objects.filter(pk=pc.pk).update(
                            course_code=f"__MERGING_{pc.pk}__"
                        )

                keeper.save()
                if keeper_is_new:
                    new_rows_created += 1
                elif len(rows) == 1:
                    rows_renamed += 1

                for pc, n in loser_info:
                    if n > 0:
                        _repoint(pc, keeper, fk_fields, m2m_fields)
                        rows_merged += 1
                    pc.delete()
                    rows_deleted += 1

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(
                f"Done. {rows_renamed} row(s) renamed in place, {new_rows_created} new "
                f"canonical row(s) created, {rows_merged} row(s) had references moved, "
                f"{rows_deleted} duplicate row(s) deleted."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                "Dry run complete -- no changes written. Re-run with --apply to write these changes."
            ))
