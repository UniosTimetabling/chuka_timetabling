# program_management/management/commands/cleanup_unlinked_program_course_duplicates.py
"""
One-off cleanup for ProgramCourse rows that are the SAME course
duplicated under slightly different course_code text within the same
program -- e.g. 'ASC 110 A' and 'ASC 110 B' each linked to their own
CourseAllocation (a group letter got typed into course_code instead of
its own field), or 'COSC 103' / 'COSC 103 Y1S1' where a year/semester
tag got baked in the same way.

Every duplicate group (same program + cohort, same course once group
letters / 'Y1S1'-style tags are ignored -- see base_course_key()) is
consolidated down to ONE canonical ProgramCourse row:

  - If one of the existing rows is already in clean 'LETTERS 123' form
    (no trailing group/year/semester junk), that row is reused as the
    keeper -- preferring one that's already referenced, so nothing has
    to move if it doesn't need to.
  - Otherwise -- e.g. 'ASC 110 A' / 'ASC 110 B', where NEITHER row is
    clean but BOTH are individually linked to their own
    CourseAllocation -- a brand-new ProgramCourse is created with the
    clean course_code (course_name/year/semester/unit_type/cohort
    copied from whichever of the duplicates has the most references),
    and every reference on every other row in the group (both
    CourseAllocations in the 'ASC 110 A/B' example) is repointed onto
    it.
  - A duplicate row that's referenced nowhere is simply deleted once
    its group's keeper is settled. A group where NOTHING is referenced
    anywhere still collapses to one survivor (see
    _pick_canonical_duplicate) instead of being left as 10 identical
    rows.

References are discovered by reflecting over every installed model for
any FK/M2M pointing at ProgramCourse (CourseAllocation, LabAllocation,
SelectionGroup/BaseSelection, campus allocation, M2M like
LecturerCourseMapping.courses, ...), the same way
merge_duplicate_program_courses.py does, so a new relation added later
is picked up automatically -- EXCEPT ArchivedCourseAllocation, which is
deliberately ignored (known to hold stale/test rows) both when deciding
whether a row is "in use" and when repointing; its FK is
on_delete=SET_NULL so deleting an old duplicate just clears that
historical pointer instead of losing data.

Any metadata disagreement between merged rows (different course_name,
year/semester, or unit_type) is printed as a warning before you apply,
since picking one is a judgement call this command makes for you --
review it.

Usage:
    python manage.py cleanup_unlinked_program_course_duplicates             # dry run
    python manage.py cleanup_unlinked_program_course_duplicates --apply      # do it
    python manage.py cleanup_unlinked_program_course_duplicates --program 7  # scope to one program first
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
    """
    Same rule merge_duplicate_program_courses.py uses: prefer a row
    already stored in normalize_code() canonical form, tie-broken by
    lowest id (oldest row), so the two commands never disagree about
    which duplicate should survive when nothing is referenced.
    """
    well_formatted = [pc for pc in rows if pc.course_code == normalize_code(pc.course_code)]
    pool = well_formatted or rows
    return min(pool, key=lambda pc: pc.id)


def _discover_referencing_fields():
    """
    Reflect over every installed model to find every FK and M2M field
    that points at ProgramCourse, so "is this row in use anywhere" is
    never a hand-maintained, out-of-date list. Mirrors the same helper
    in merge_duplicate_program_courses.py.

    ArchivedCourseAllocation is deliberately excluded: it's known to
    carry stale/test data, so a duplicate ProgramCourse that's only
    referenced there (and nowhere in the live CourseAllocation table or
    anything else current) should still be treated as unreferenced and
    eligible for cleanup. Its FK is on_delete=SET_NULL, so deleting a
    duplicate never loses that archive row -- the pointer just clears.
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
    """
    Move every reference from `loser` onto `keeper`. M2M rows just get
    the value swapped (duplicates collapse naturally). FK rows use a
    bulk .update() where safe, falling back to a row-by-row save (and
    dropping the row if it collides with an existing unique_together on
    the keeper, e.g. BaseSelection.unique_together = (program_course,
    department)) so one bad row can't abort the whole merge.
    """
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
    """
    Returns (keeper, is_new). `linked` is a list of (ProgramCourse, ref_count)
    for rows referenced somewhere; `linked_pks` is the set of their pks.
    """
    clean = [pc for pc in rows if _is_clean(pc.course_code)]
    if clean:
        clean_linked = [pc for pc in clean if pc.pk in linked_pks]
        pool = clean_linked or clean
        return min(pool, key=lambda pc: pc.id), False

    if len(linked) >= 2:
        # Neither/none of the duplicates is already clean, but more than
        # one is independently referenced (the 'ASC 110 A' / 'ASC 110 B'
        # case) -- a brand-new canonical row is needed so both sets of
        # references can move onto the same place. Base its metadata on
        # whichever duplicate has the most references.
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

    # 0 or 1 referenced row and nothing already clean -- no need to create
    # a new row, just rename the one that should survive.
    keeper = linked[0][0] if linked else _pick_canonical_duplicate(rows)
    keeper.course_code = _display_form(base_key)
    return keeper, False


class Command(BaseCommand):
    help = (
        "Consolidate ProgramCourse duplicates (same program/cohort, same "
        "course once group letters / 'Y1S1'-style tags baked into "
        "course_code are ignored) down to one canonical row. If two or "
        "more duplicates are each individually referenced (e.g. 'ASC 110 A' "
        "and 'ASC 110 B' each linked to their own CourseAllocation), a "
        "clean row is reused or created and every reference is moved onto "
        "it before the old rows are deleted. Rows with nothing referencing "
        "them are deleted outright. ArchivedCourseAllocation is ignored -- "
        "known to hold stale/test data. Dry-run by default."
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

        dup_groups = {k: v for k, v in groups.items() if len(v) > 1}

        if not dup_groups:
            self.stdout.write(self.style.SUCCESS("No ProgramCourse duplicate groups found."))
            return

        self.stdout.write(
            f"Found {len(dup_groups)} duplicate group(s) "
            f"({sum(len(v) for v in dup_groups.values())} rows total). "
            f"{'APPLYING' if apply_changes else 'DRY RUN -- pass --apply to write changes'}."
        )
        self.stdout.write(
            f"Checking usage via {len(fk_fields)} FK field(s) and {len(m2m_fields)} M2M "
            f"field(s) discovered on: "
            + (", ".join(sorted({f"{m.__name__}.{n}" for m, n in fk_fields + m2m_fields})) or "(none)")
        )

        rows_deleted = 0
        rows_merged = 0
        new_rows_created = 0
        fully_unreferenced_groups = 0

        for key, rows in sorted(dup_groups.items()):
            program_id_, cohort, base_key = key
            program_name = rows[0].program.name if rows else "?"

            annotated = [(pc, reference_count(pc)) for pc in rows]
            linked = [(pc, n) for pc, n in annotated if n > 0]
            linked_pks = {pc.pk for pc, _ in linked}
            if not linked:
                fully_unreferenced_groups += 1

            keeper, keeper_is_new = _choose_keeper(rows, linked, linked_pks, base_key)
            losers = list(rows) if keeper_is_new else [pc for pc in rows if pc.pk != keeper.pk]
            loser_info = [(pc, next((n for p, n in annotated if p.pk == pc.pk), 0)) for pc in losers]

            header = (
                f"  Program {program_id_} ({program_name}) / cohort {cohort} / "
                f"base code {base_key}:"
            )
            self.stdout.write(header)

            if keeper_is_new:
                to_merge = [pc for pc, n in loser_info if n > 0]
                self.stdout.write(
                    f"      CREATE '{keeper.course_code}' (Y{keeper.year}S{keeper.semester}) -- "
                    f"new canonical row absorbing {len(to_merge)} referenced row(s)"
                )
                names = {pc.course_name for pc, n in loser_info if n > 0}
                yr_sem = {(pc.year, pc.semester) for pc, n in loser_info if n > 0}
                units = {pc.unit_type for pc, n in loser_info if n > 0}
                if len(names) > 1 or len(yr_sem) > 1 or len(units) > 1:
                    self.stdout.write(self.style.WARNING(
                        "      ! merged rows disagree on name/year-semester/unit_type -- "
                        "double-check before applying:"
                    ))
                    for pc, n in loser_info:
                        if n > 0:
                            self.stdout.write(
                                f"          #{pc.pk} '{pc.course_code}' name={pc.course_name!r} "
                                f"Y{pc.year}S{pc.semester} unit_type={pc.unit_type}"
                            )
            elif not linked:
                self.stdout.write(self.style.WARNING(
                    f"      no row referenced anywhere -- keeping 1 of {len(rows)}"
                ))
                self.stdout.write(
                    f"      KEEP   #{keeper.pk} '{keeper.course_code}' (Y{keeper.year}S{keeper.semester}) -- 0 references"
                )
            else:
                self.stdout.write(
                    f"      KEEP   #{keeper.pk} '{keeper.course_code}' (Y{keeper.year}S{keeper.semester})"
                )

            for pc, n in loser_info:
                if n > 0:
                    self.stdout.write(f"      MERGE  #{pc.pk} '{pc.course_code}' -- {n} reference(s) to move onto keeper")
                else:
                    self.stdout.write(f"      DELETE #{pc.pk} '{pc.course_code}' -- 0 references")

            if not apply_changes:
                continue

            with transaction.atomic():
                # ProgramCourse.save() unconditionally re-normalizes
                # course_code (see program_management/models.py), even when
                # the value we set here looks already normalized. That means
                # saving/creating the keeper can collide with a loser row
                # that ALREADY holds that exact normalized code and hasn't
                # been deleted yet -- e.g. keeper 'HIST211' normalizes to
                # 'HIST 211' on save(), colliding with a not-yet-deleted
                # loser row that's already stored as 'HIST 211'. Since that
                # loser is being deleted in this same transaction anyway,
                # bump it out of the way first with a harmless placeholder
                # code (via .update(), which bypasses save()'s own
                # normalization) so the keeper's save/create never hits the
                # unique_together constraint.
                target_code = keeper.course_code if keeper_is_new else normalize_code(keeper.course_code)
                for pc, n in loser_info:
                    if pc.pk and normalize_code(pc.course_code) == target_code:
                        ProgramCourse.objects.filter(pk=pc.pk).update(
                            course_code=f"__MERGING_{pc.pk}__"
                        )

                keeper.save()
                if keeper_is_new:
                    new_rows_created += 1
                for pc, n in loser_info:
                    if n > 0:
                        _repoint(pc, keeper, fk_fields, m2m_fields)
                        rows_merged += 1
                    pc.delete()
                    rows_deleted += 1

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(
                f"Done. {new_rows_created} new canonical row(s) created, {rows_merged} "
                f"row(s) had references moved, {rows_deleted} duplicate row(s) deleted "
                f"({fully_unreferenced_groups} group(s) had no referenced row at all)."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                "Dry run complete -- no changes written. Re-run with --apply to write these changes."
            ))
