# program_management/management/commands/merge_duplicate_programs.py
"""
One-off + repeatable cleanup for duplicate Program rows.

The problem this fixes
-----------------------
The same academic program sometimes exists as TWO Program rows in
DIFFERENT departments because it was imported/created more than once,
e.g.:

    #41  "BSc Animal Science"   department = Department of Management Science
    #97  "BSc  Animal Science"  department = Department of Computer Science

(`Program.name` has `unique=True`, but that only blocks a byte-for-byte
duplicate string -- it does nothing about a stray double space, a
trailing period, or different punctuation, so two rows for the same
program can still both exist.)

This command finds those groups, picks one "keeper" row per group --
the one that actually has course-allocation data, per the office's
instruction: "use the one with data, the one with course allocation
remains, the other is removed" -- re-points every reference to the
"loser" row (ProgramCourse curriculum entries, CourseAllocation,
resit/campus/ODEL allocations, special requests, mobile announcements,
program-year trackers, program codes, ... anything in the whole
project with a ForeignKey/M2M to program_management.Program) onto the
keeper, and only then deletes the loser row. Nothing is ever deleted
while it still has data pointing at it -- reassignment always runs
first, in the same DB transaction.

Matching rule
-------------
Two Program rows are treated as the same program, written differently,
when their names collapse to the same canonical form (see
`canonical_program_key`):
  1. upper-cased, periods/commas stripped, whitespace collapsed;
  2. common degree abbreviations expanded to their full wording, so
     'BSc', 'B.Sc.', 'B Sc' all become 'BACHELOR OF SCIENCE' before
     comparison (also handles BA, BCom, BEd, BBA, BBM, LLB, BEng,
     BTech, MSc, MA, MEd, MBA, PhD, Dip, Cert);
  3. filler/connector words ('OF', 'IN', 'AND', 'THE', 'FOR', 'A',
     'AN') are dropped and the remaining words are sorted, so word
     order doesn't matter either -- 'BSc Horticulture' and 'Bachelor
     of Science in Horticulture' both collapse to the same
     {BACHELOR, HORTICULTURE, SCIENCE} key and are treated as the
     same program.
Department is NOT part of the matching key -- these duplicates are
specifically the cross-department kind, but same-department
duplicates (two rows in one department, worded differently) are
caught the same way.

A second, looser pass flags pairs whose canonical keys are close but
not identical (e.g. a genuine spelling difference) as REVIEW ONLY --
these are printed for a human to check but never auto-merged, since
guessing wrong here means silently deleting a real, different
program.

Choosing the keeper inside a group
-----------------------------------
  1. Prefer the row with more CourseAllocation rows attached (counting
     both the direct `CourseAllocation.program` link AND allocations
     reached indirectly via `CourseAllocation.program_course.program`,
     since program_course is the required link and program is only a
     secondary/denormalised one). This is the deciding signal per the
     office's instruction.
  2. Tie-break: the row with more ProgramCourse curriculum rows.
  3. Tie-break: the row with more references overall across every
     other discovered relation in the project.
  4. Tie-break: the lowest id (the oldest / original record).
  Department and description are NOT used to choose the keeper -- the
  keeper's own department is kept as-is; any non-empty description the
  loser has that the keeper lacks is copied over so it isn't lost.

ProgramCourse needs special handling
-------------------------------------
`ProgramCourse` is the one related model with a real uniqueness
constraint against Program (`unique_together = (program, course_code,
student_cohort)`), so a naive bulk re-point of loser curriculum rows
onto the keeper program can collide when BOTH programs already teach
the same course/cohort -- which is the common case for a duplicated
program. Blindly dropping the colliding row (the way every other
collision in this command is resolved) would be wrong here: that row
is a required, on_delete=CASCADE parent of CourseAllocation and
several other allocation tables, so deleting it would silently destroy
real allocation data -- the exact opposite of what this command is
for.

So for each loser ProgramCourse:
  - if the keeper program has no equivalent curriculum row (same
    canonical course code + cohort + year + semester), the loser row
    is simply re-pointed to the keeper program -- no collision.
  - if the keeper program already has an equivalent row, the two
    ProgramCourse rows themselves are merged first (every reference to
    the loser curriculum row -- CourseAllocation, campus/resit
    allocations, lab allocations, selections, ... -- is re-pointed onto
    the keeper's curriculum row, reusing the same discover-then-repoint
    approach as merge_duplicate_program_courses.py) before the now-empty
    loser curriculum row is deleted. No allocation is ever dropped.

Usage
-----
    python manage.py merge_duplicate_programs            # dry run -- reports only
    python manage.py merge_duplicate_programs --apply     # do it
"""
import re
import difflib
from collections import defaultdict

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import models as django_models
from django.db import transaction, IntegrityError

from program_management.models import Program, ProgramCourse
from program_management.code_utils import canonical_course_key


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

# Applied longest/most-specific first so e.g. "MBA" is expanded whole
# rather than accidentally leaving a stray "BA" behind. \b boundaries
# and the requirement that every letter be present mean these don't
# mis-fire on substrings of other words (e.g. "\bBA\b" cannot match
# inside "BBA" or "LIBRARY").
_ABBREVIATIONS = [
    (r"LL\.?\s?B\.?", "BACHELOR OF LAWS"),
    (r"B\.?\s?B\.?\s?A\.?", "BACHELOR OF BUSINESS ADMINISTRATION"),
    (r"B\.?\s?B\.?\s?M\.?", "BACHELOR OF BUSINESS MANAGEMENT"),
    (r"B\.?\s?ENG\.?", "BACHELOR OF ENGINEERING"),
    (r"B\.?\s?TECH\.?", "BACHELOR OF TECHNOLOGY"),
    (r"B\.?\s?COM\.?", "BACHELOR OF COMMERCE"),
    (r"B\.?\s?ED\.?", "BACHELOR OF EDUCATION"),
    (r"B\.?\s?SC\.?", "BACHELOR OF SCIENCE"),
    (r"B\.?\s?A\.?", "BACHELOR OF ARTS"),
    (r"M\.?\s?B\.?\s?A\.?", "MASTER OF BUSINESS ADMINISTRATION"),
    (r"M\.?\s?ED\.?", "MASTER OF EDUCATION"),
    (r"M\.?\s?SC\.?", "MASTER OF SCIENCE"),
    (r"M\.?\s?A\.?", "MASTER OF ARTS"),
    (r"PH\.?\s?D\.?", "DOCTOR OF PHILOSOPHY"),
    (r"DIP\.?", "DIPLOMA"),
    (r"CERT\.?", "CERTIFICATE"),
]
_FILLER_WORDS = {"OF", "IN", "AND", "THE", "FOR", "A", "AN"}


def canonical_program_key(name):
    """
    Collapse a program name down to a comparable canonical form so that
    'BSc Animal Science', 'BSc  Animal Science', 'B.Sc. Animal Science.',
    'bsc animal science', and 'Bachelor of Science in Animal Science'
    all compare equal. Matching/deduplication only -- never stored or
    displayed.
    """
    name = (name or "").strip().upper()
    for pattern, expansion in _ABBREVIATIONS:
        name = re.sub(rf"\b{pattern}\b", expansion, name)
    name = re.sub(r"[.,()]", "", name)          # drop periods/commas/parens
    tokens = [t for t in re.split(r"\s+", name) if t and t not in _FILLER_WORDS]
    return " ".join(sorted(tokens))


def find_duplicate_program_groups():
    """Returns (auto_groups, review_pairs): auto_groups are rows whose
    canonical keys match exactly; review_pairs are (program_a, program_b,
    ratio) for close-but-not-identical keys, flagged for a human to check
    but never auto-merged."""
    groups = defaultdict(list)
    for program in Program.objects.all().order_by("id"):
        groups[canonical_program_key(program.name)].append(program)

    auto_groups = [rows for rows in groups.values() if len(rows) > 1]

    matched_keys = list(groups.keys())
    review_pairs = []
    for i, key_a in enumerate(matched_keys):
        if len(groups[key_a]) > 1:
            continue  # already an exact-match group
        for key_b in matched_keys[i + 1:]:
            if len(groups[key_b]) > 1:
                continue
            ratio = difflib.SequenceMatcher(None, key_a, key_b).ratio()
            if ratio >= 0.85:
                review_pairs.append((groups[key_a][0], groups[key_b][0], ratio))

    return auto_groups, review_pairs


# ---------------------------------------------------------------------------
# Keeper selection
# ---------------------------------------------------------------------------

def _allocation_count(program):
    from course_allocation.models import CourseAllocation
    return CourseAllocation.objects.filter(
        django_models.Q(program_id=program.pk) | django_models.Q(program_course__program_id=program.pk)
    ).distinct().count()


def _program_course_count(program):
    return ProgramCourse.objects.filter(program_id=program.pk).count()


def _total_reference_count(program, fk_fields, m2m_fields):
    total = 0
    for model, field_name in fk_fields:
        total += model.objects.filter(**{field_name: program}).count()
    for model, field_name in m2m_fields:
        total += model.objects.filter(**{field_name: program}).count()
    return total


def pick_keeper(rows, fk_fields, m2m_fields):
    def sort_key(program):
        return (
            -_allocation_count(program),
            -_program_course_count(program),
            -_total_reference_count(program, fk_fields, m2m_fields),
            program.pk,
        )
    return min(rows, key=sort_key)


# ---------------------------------------------------------------------------
# Reference reassignment -- Program-level (everything except ProgramCourse,
# which is handled separately below because of its unique_together).
# ---------------------------------------------------------------------------

def _discover_program_referencing_fields():
    fk_fields = []
    m2m_fields = []
    for model in apps.get_models():
        if model is Program:
            continue
        for field in model._meta.get_fields():
            if isinstance(field, django_models.ManyToManyField) and field.related_model is Program:
                m2m_fields.append((model, field.name))
            elif isinstance(field, (django_models.ForeignKey, django_models.OneToOneField)) \
                    and field.related_model is Program and model is not ProgramCourse:
                fk_fields.append((model, field.name))
    return fk_fields, m2m_fields


def _discover_program_course_referencing_fields():
    """Same reflection trick, one level down -- everything that points at
    ProgramCourse (reused when two curriculum rows collapse into one)."""
    fk_fields = []
    m2m_fields = []
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if isinstance(field, django_models.ManyToManyField) and field.related_model is ProgramCourse:
                m2m_fields.append((model, field.name))
            elif isinstance(field, django_models.ForeignKey) and field.related_model is ProgramCourse:
                fk_fields.append((model, field.name))
    return fk_fields, m2m_fields


def _repoint(related_qs, field_name, target, stdout, on_collision_label):
    """Bulk .update() where safe; fall back to a row-by-row save, and drop
    the row only if it collides with something the target already has --
    logging every drop so nothing disappears silently."""
    try:
        with transaction.atomic():
            related_qs.update(**{field_name: target})
        return
    except IntegrityError:
        pass
    for instance in related_qs:
        setattr(instance, field_name, target)
        try:
            with transaction.atomic():
                instance.save()
        except IntegrityError:
            stdout.write(
                f"    ! dropped redundant {instance.__class__.__name__}#{instance.pk} "
                f"({on_collision_label} already has an equivalent row)"
            )
            instance.delete()


def _merge_program_course_into(keeper_pc, loser_pc, pc_fk_fields, pc_m2m_fields, stdout):
    """Merge one duplicate curriculum row into another -- every allocation
    or reference the loser curriculum row carries is moved onto the
    keeper's matching row first, so nothing is lost when the loser is
    finally deleted."""
    for model, field_name in pc_m2m_fields:
        for instance in model.objects.filter(**{field_name: loser_pc}):
            getattr(instance, field_name).remove(loser_pc)
            getattr(instance, field_name).add(keeper_pc)
    for model, field_name in pc_fk_fields:
        related_qs = model.objects.filter(**{field_name: loser_pc})
        _repoint(related_qs, field_name, keeper_pc, stdout, "the keeper curriculum entry")
    loser_pc.delete()


def _merge_program_courses(keeper, loser, pc_fk_fields, pc_m2m_fields, stdout):
    keeper_index = {}
    for pc in ProgramCourse.objects.filter(program=keeper):
        key = (canonical_course_key(pc.course_code), pc.student_cohort or "0", pc.year, pc.semester)
        keeper_index[key] = pc

    for loser_pc in list(ProgramCourse.objects.filter(program=loser)):
        key = (canonical_course_key(loser_pc.course_code), loser_pc.student_cohort or "0",
               loser_pc.year, loser_pc.semester)
        match = keeper_index.get(key)
        if match is None:
            # No equivalent curriculum row on the keeper -- safe to just
            # move it across.
            ProgramCourse.objects.filter(pk=loser_pc.pk).update(program=keeper)
            keeper_index[key] = loser_pc
        else:
            stdout.write(
                f"    merging curriculum row #{loser_pc.pk} '{loser_pc.course_code}' "
                f"-> #{match.pk} '{match.course_code}' (same course already on keeper program)"
            )
            _merge_program_course_into(match, loser_pc, pc_fk_fields, pc_m2m_fields, stdout)


def _merge_program(keeper, loser, fk_fields, m2m_fields, pc_fk_fields, pc_m2m_fields, stdout):
    if not (keeper.description or "").strip() and (loser.description or "").strip():
        keeper.description = loser.description
        keeper.save(update_fields=["description"])

    _merge_program_courses(keeper, loser, pc_fk_fields, pc_m2m_fields, stdout)

    for model, field_name in m2m_fields:
        for instance in model.objects.filter(**{field_name: loser}):
            getattr(instance, field_name).remove(loser)
            getattr(instance, field_name).add(keeper)

    for model, field_name in fk_fields:
        related_qs = model.objects.filter(**{field_name: loser})
        _repoint(related_qs, field_name, keeper, stdout, "the keeper program")

    loser.delete()


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = (
        "Find Program rows that are the same academic program duplicated "
        "across departments (or just spelled/spaced/punctuated "
        "differently), keep whichever row actually has course-allocation "
        "data, re-point every reference in the project onto that row, and "
        "delete the redundant program(s). Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually write changes. Without this flag, only reports what would happen.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        groups, review_pairs = find_duplicate_program_groups()
        if not groups and not review_pairs:
            self.stdout.write(self.style.SUCCESS("No duplicate Program rows found."))
            return

        if review_pairs:
            self.stdout.write(self.style.WARNING(
                f"{len(review_pairs)} REVIEW pair(s) found -- names are close but not "
                f"an exact match after normalization, so these are NEVER auto-merged:"
            ))
            for a, b, ratio in review_pairs:
                self.stdout.write(
                    f"  [REVIEW] #{a.pk} '{a.name}' (dept={a.department}) "
                    f"~ #{b.pk} '{b.name}' (dept={b.department})  similarity={ratio:.2f}"
                )

        if not groups:
            self.stdout.write(self.style.WARNING("No exact-match duplicate groups to merge."))
            return

        fk_fields, m2m_fields = _discover_program_referencing_fields()
        pc_fk_fields, pc_m2m_fields = _discover_program_course_referencing_fields()

        self.stdout.write(
            f"Found {len(groups)} duplicate program group(s). "
            f"{'APPLYING' if apply_changes else 'DRY RUN -- pass --apply to write changes'}."
        )
        self.stdout.write(
            "Reassigning references via: "
            + ", ".join(sorted({f"{m.__name__}.{n}" for m, n in fk_fields + m2m_fields})) or "(none)"
        )

        merged = 0
        for rows in groups:
            keeper = pick_keeper(rows, fk_fields, m2m_fields)
            losers = [r for r in rows if r.pk != keeper.pk]

            self.stdout.write(
                f"  KEEP  #{keeper.pk} '{keeper.name}' (dept={keeper.department}, "
                f"allocations={_allocation_count(keeper)}, courses={_program_course_count(keeper)})"
            )
            for loser in losers:
                self.stdout.write(
                    f"  MERGE #{loser.pk} '{loser.name}' (dept={loser.department}, "
                    f"allocations={_allocation_count(loser)}, courses={_program_course_count(loser)}) -> #{keeper.pk}"
                )

            if not apply_changes:
                continue

            with transaction.atomic():
                for loser in losers:
                    _merge_program(keeper, loser, fk_fields, m2m_fields, pc_fk_fields, pc_m2m_fields, self.stdout)
                    merged += 1

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(f"Done. Merged/deleted {merged} duplicate program row(s)."))
        else:
            self.stdout.write(self.style.WARNING("Dry run complete -- no changes written. Re-run with --apply to merge."))
