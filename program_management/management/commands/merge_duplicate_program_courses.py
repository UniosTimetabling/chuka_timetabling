# program_management/management/commands/merge_duplicate_program_courses.py
"""
One-off cleanup for existing ProgramCourse duplicates.

The normalize_code() fix (program_management/code_utils.py) stops NEW
duplicate rows like ('BCOM112', 'BCOM 112', 'bcom112') from being
created going forward, but it does nothing for rows that were already
written to the DB before the fix. This command finds those, merges
every FK/M2M reference from the "loser" rows onto one "keeper" row per
duplicate group, and deletes the losers.

A "duplicate group" is every ProgramCourse row for the same
(program, student_cohort) whose course_code collapses to the same
canonical_course_key() -- i.e. the same course, spelled/spaced/cased
differently. Rows are NOT merged across different (year, semester)
slots -- only rows within the exact same slot are treated as
duplicates (this mirrors cod_panel.resolve_program_course's
auto-resolve rule).

The keeper is chosen the same way cod_panel._pick_canonical_duplicate
does: prefer a row already in the canonical normalize_code() form,
tie-broken by lowest id (the oldest row), so this command and the live
auto-resolve logic agree on which row survives.

Usage:
    python manage.py merge_duplicate_program_courses            # dry run
    python manage.py merge_duplicate_program_courses --apply     # do it
"""
from collections import defaultdict

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import models as django_models
from django.db import transaction, IntegrityError

from program_management.models import ProgramCourse
from program_management.code_utils import normalize_code, canonical_course_key


def _pick_canonical_duplicate(rows):
    well_formatted = [pc for pc in rows if pc.course_code == normalize_code(pc.course_code)]
    pool = well_formatted or rows
    return min(pool, key=lambda pc: pc.id)


def _discover_referencing_fields():
    """
    Reflect over every installed model to find every FK and M2M field
    that points at ProgramCourse, so this command doesn't need to be
    hand-updated every time a new feature adds a new relation to
    ProgramCourse (LecturerCourseMapping.courses, CourseAllocation.
    program_course, LabAllocation.program_course/additional_courses,
    BaseSelection.program_course, ArchivedCourseAllocation.program_course,
    ...).
    """
    fk_fields = []
    m2m_fields = []
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if isinstance(field, django_models.ManyToManyField) and field.related_model is ProgramCourse:
                m2m_fields.append((model, field.name))
            elif isinstance(field, django_models.ForeignKey) and field.related_model is ProgramCourse:
                fk_fields.append((model, field.name))
    return fk_fields, m2m_fields


class Command(BaseCommand):
    help = (
        "Find ProgramCourse rows that are whitespace/case/separator "
        "duplicates of each other (e.g. 'BCOM112' and 'BCOM 112' for the "
        "same program/cohort/year/semester) created before the "
        "normalize_code() fix, merge every reference onto one keeper row, "
        "and delete the redundant rows. Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually write changes. Without this flag, only reports what would happen.",
        )
        parser.add_argument(
            "--program", type=int, default=None,
            help="Restrict to a single Program id (mainly for testing on one program first).",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        program_id = options["program"]

        qs = ProgramCourse.objects.all().order_by("id")
        if program_id:
            qs = qs.filter(program_id=program_id)

        groups = defaultdict(list)
        for pc in qs:
            key = (pc.program_id, canonical_course_key(pc.course_code), pc.student_cohort or "0", pc.year, pc.semester)
            groups[key].append(pc)

        dup_groups = {k: v for k, v in groups.items() if len(v) > 1}

        if not dup_groups:
            self.stdout.write(self.style.SUCCESS("No duplicate ProgramCourse rows found."))
            return

        fk_fields, m2m_fields = _discover_referencing_fields()

        self.stdout.write(
            f"Found {len(dup_groups)} duplicate group(s) "
            f"({sum(len(v) for v in dup_groups.values())} rows total). "
            f"{'APPLYING' if apply_changes else 'DRY RUN -- pass --apply to write changes'}."
        )
        self.stdout.write(
            f"Reassigning references via {len(fk_fields)} FK field(s) and "
            f"{len(m2m_fields)} M2M field(s) discovered on: "
            + ", ".join(sorted({f"{m.__name__}.{n}" for m, n in fk_fields + m2m_fields})) or "(none)"
        )

        rows_deleted = 0
        for key, rows in dup_groups.items():
            program_id_, canon_key, cohort, year, semester = key
            keeper = _pick_canonical_duplicate(rows)
            losers = [r for r in rows if r.pk != keeper.pk]

            self.stdout.write(
                f"  Program {program_id_} / cohort {cohort} / Y{year}S{semester}: "
                f"keeping #{keeper.pk} '{keeper.course_code}', "
                f"merging {[(r.pk, r.course_code) for r in losers]}"
            )

            if not apply_changes:
                continue

            with transaction.atomic():
                # Make sure the keeper itself is stored in canonical form
                # (ProgramCourse.save() also does this now, but be explicit).
                canonical_form = normalize_code(keeper.course_code)
                if keeper.course_code != canonical_form:
                    keeper.course_code = canonical_form
                    keeper.save()

                for loser in losers:
                    # M2M first: through-table rows just get the FK value
                    # swapped, duplicates collapse naturally (a set can't
                    # contain the same (owner, keeper) pair twice).
                    for model, field_name in m2m_fields:
                        manager_field = f"{field_name}"
                        # Find every instance of `model` that has `loser` in
                        # its M2M field, and repoint it at `keeper`.
                        related_qs = model.objects.filter(**{field_name: loser})
                        for instance in related_qs:
                            getattr(instance, manager_field).remove(loser)
                            getattr(instance, manager_field).add(keeper)

                    # FK fields: bulk .update() where safe; fall back to a
                    # row-by-row save (and drop the row if it collides with
                    # an existing unique_together on the keeper, e.g.
                    # BaseSelection.unique_together = (program_course, department))
                    # so one bad row can't abort the whole merge.
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
                                    # Keeper already has an equivalent row
                                    # (unique_together collision) -- the
                                    # loser's copy is now redundant.
                                    instance.delete()

                    loser.delete()
                    rows_deleted += 1

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(f"Done. Deleted {rows_deleted} duplicate row(s)."))
        else:
            self.stdout.write(self.style.WARNING("Dry run complete -- no changes written. Re-run with --apply to merge."))
