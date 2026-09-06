# lecturer_portal/management/commands/merge_duplicate_lecturers.py
"""
One-off + repeatable cleanup for duplicate Lecturer rows.

The problem this fixes
-----------------------
The same real person sometimes exists as TWO Lecturer rows because they
were imported/created more than once with the name spelled differently,
e.g.:

    CHU/0014  David Gitonga Mwathi   Prof  Computer Science
    CHU/0512  D. Mwathi              Dr    Computer Science
    CHU/0733  D.G. Mwathi            Dr    (no department)

All three are the same lecturer. The designation may disagree (one was
never corrected after a promotion), and the department may be missing on
one of the rows even though it's clearly the same person as a row that
DOES have a department.

This command finds those groups, picks one "keeper" row per group,
re-points every reference to the "loser" rows (course allocations, exam
timetable entries, blocked slots, time/venue preferences, ODeL
allocations, special requests, resit allocations, ... -- anything in the
whole project with a ForeignKey/OneToOne/M2M to lecturer_portal.Lecturer)
onto the keeper, and only then deletes the loser rows. Nothing is ever
deleted while it still has allocations pointing at it -- the reassignment
step always runs first, in the same DB transaction.

Matching rule
-------------
Two lecturer names are considered "the same person, written differently"
when:
  1. The last token (surname) is identical, case-insensitive, AND
  2. Every remaining name token on the SHORTER name is either an exact
     match, or a bare initial (e.g. "D." / "D") that matches the first
     letter of the corresponding token on the longer name, in order.

     "David Gitonga Mwathi" vs "D. Mwathi"   -> match (D matches David,
                                                 Gitonga skipped, Mwathi
                                                 == Mwathi)
     "David Gitonga Mwathi" vs "D.G. Mwathi" -> match
     "Christopher Okongo" vs "C. Mwathi"     -> NOT a match (different
                                                 surname)

Confidence tiers (see MergeGroup.confidence):
  AUTO   - every row in the group has an actual given name (not just a
           bare surname) that matches via the initials rule above, AND
           the rows share the same department, or at least one of them
           has no department at all. This is exactly the "duplicate in
           the same department" and "stray duplicate with no department"
           cases described by the office.
  REVIEW - anything weaker than that:
             * two rows have two DIFFERENT departments set, or
             * one of the matching rows is a BARE surname with no given
               name on file at all (e.g. just "Mugambi"). A bare surname
               is treated as much weaker evidence -- on its own it would
               happily "match" every unrelated person who shares that
               surname, so it is never chained or auto-merged, only
               reported, even when the department happens to line up.
           Confirm these by hand, then pass them through --pairs-csv to
           force the merge.

Choosing the keeper inside a group:
  1. Prefer the row with a linked Django User account (real login).
  2. Prefer the row that HAS a department set.
  3. Prefer the row with the most complete (least abbreviated) name.
  4. Prefer the row with the lowest id (the oldest / original record).
  Designation is NOT used to choose the keeper (an abbreviated
  duplicate's designation is often stale) -- the keeper's own
  designation is kept, and any disagreement is printed as a warning for
  a human to double check after the merge.

Usage
-----
    python manage.py merge_duplicate_lecturers            # merges AUTO groups for real, right away
    python manage.py merge_duplicate_lecturers --dry-run   # preview only, writes nothing

REVIEW groups (different departments, or one side is a bare surname with
no given name) are always just printed, never merged automatically --
there's no flag that force-merges them. Fix those by hand in the admin
if needed.
"""
import re
from collections import defaultdict

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import models as django_models
from django.db import transaction, IntegrityError

from lecturer_portal.models import Lecturer


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

def _tokens(name):
    """
    'David  Gitonga Mwathi' -> ['David', 'Gitonga', 'Mwathi']
    'S.M. Kagwanja'         -> ['S.', 'M.', 'Kagwanja']   (splits compound
                                                            initials like
                                                            "S.M." apart so
                                                            each initial is
                                                            matched on its own)
    """
    name = (name or "").strip()
    name = re.sub(r"\.(?=[A-Za-z])", ". ", name)  # "D.G." -> "D. G."
    return [t for t in re.split(r"\s+", name) if t.strip(".")]


def _is_initial(token):
    return len(token.rstrip(".")) == 1


def _surname_key(name):
    toks = _tokens(name)
    return toks[-1].lower() if toks else ""


def names_match(name_a, name_b):
    """
    True if name_a and name_b look like the same person written with
    different amounts of abbreviation. Order-independent about which
    one is longer.
    """
    a, b = _tokens(name_a), _tokens(name_b)
    if not a or not b:
        return False
    if a[-1].lower() != b[-1].lower():
        return False  # surname must match exactly

    # work with the shorter list of "given name" tokens against the longer
    if len(a) < len(b):
        short, long_ = a[:-1], b[:-1]
    else:
        short, long_ = b[:-1], a[:-1]

    li = 0  # pointer into long_
    for s_tok in short:
        matched = False
        while li < len(long_):
            l_tok = long_[li]
            li += 1
            if _is_initial(s_tok):
                if l_tok[:1].lower() == s_tok.rstrip(".")[:1].lower():
                    matched = True
                    break
            else:
                if l_tok.lower() == s_tok.lower():
                    matched = True
                    break
        if not matched:
            return False
    return True


def _completeness_score(name):
    """Higher = more complete / less abbreviated name."""
    toks = _tokens(name)
    return sum(1 for t in toks if not _is_initial(t))


def _given_token_count(name):
    """Number of name tokens BEFORE the surname. 0 means the record only
    has a bare surname on file ('Mugambi') with no given name at all --
    that is very weak evidence and must never be allowed to chain two
    unrelated full names together just because they share a surname."""
    return max(len(_tokens(name)) - 1, 0)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

class MergeGroup:
    def __init__(self, rows, has_weak_edge=False):
        self.rows = rows  # Lecturer instances
        self.keeper = self._pick_keeper(rows)
        self.losers = [r for r in rows if r.pk != self.keeper.pk]
        # a "weak" edge is a match that only exists because one side is a
        # bare surname with no given name at all -- never auto-merge those,
        # a human needs to eyeball it.
        self.has_weak_edge = has_weak_edge
        self.confidence = self._confidence()

    def _pick_keeper(self, rows):
        def sort_key(lec):
            return (
                0 if lec.user_id else 1,             # linked user wins
                0 if lec.department_id else 1,        # has department wins
                -_completeness_score(lec.name),       # fuller name wins
                lec.pk,                               # oldest row wins
            )
        return sorted(rows, key=sort_key)[0]

    def _confidence(self):
        if self.has_weak_edge:
            return "REVIEW"
        depts = {r.department_id for r in self.rows if r.department_id}
        if len(depts) <= 1:
            return "AUTO"
        return "REVIEW"

    def designations(self):
        return sorted({r.get_designation_display() for r in self.rows})


def find_duplicate_groups():
    qs = Lecturer.objects.all().order_by("id")

    by_surname = defaultdict(list)
    for lec in qs:
        by_surname[_surname_key(lec.name)].append(lec)

    groups = []
    for surname, lecturers in by_surname.items():
        if not surname or len(lecturers) < 2:
            continue

        named = [l for l in lecturers if _given_token_count(l.name) > 0]
        bare = [l for l in lecturers if _given_token_count(l.name) == 0]

        # Step 1: cluster only the "named" rows (real given-name tokens on
        # both sides of every comparison) via chained matching. This is
        # safe to chain because every edge is real evidence, not a
        # surname-only coincidence.
        clusters = []
        for lec in named:
            placed = False
            for cluster in clusters:
                if any(names_match(lec.name, other.name) for other in cluster):
                    cluster.append(lec)
                    placed = True
                    break
            if not placed:
                clusters.append([lec])

        # Step 2: try to attach each bare-surname row ("Mugambi", no given
        # name at all) to exactly one existing cluster -- ONLY if its
        # department matches a member of that cluster and no other
        # cluster/bare row for this surname is an equally plausible
        # candidate. This is deliberately conservative: a bare surname is
        # not allowed to bridge two different full-name clusters together.
        unattached_bare = []
        for lec in bare:
            candidate_clusters = [
                c for c in clusters
                if lec.department_id and any(m.department_id == lec.department_id for m in c)
            ]
            if len(candidate_clusters) == 1:
                candidate_clusters[0].append(lec)
            else:
                unattached_bare.append(lec)

        for cluster in clusters:
            if len(cluster) > 1:
                has_weak = any(_given_token_count(m.name) == 0 for m in cluster)
                groups.append(MergeGroup(cluster, has_weak_edge=has_weak))

        # Step 3: bare rows that share a surname AND department with each
        # other (but attached to no named cluster) are still worth
        # flagging -- as REVIEW only, never AUTO.
        bare_by_dept = defaultdict(list)
        for lec in unattached_bare:
            bare_by_dept[lec.department_id].append(lec)
        for dept_id, rows in bare_by_dept.items():
            if dept_id and len(rows) > 1:
                groups.append(MergeGroup(rows, has_weak_edge=True))

    return groups


# ---------------------------------------------------------------------------
# Reference reassignment (same reflection trick as
# merge_duplicate_program_courses.py, so new FKs added anywhere in the
# project in the future are picked up automatically -- nothing to
# hand-maintain here).
# ---------------------------------------------------------------------------

def _discover_referencing_fields():
    fk_fields = []
    m2m_fields = []
    for model in apps.get_models():
        if model is Lecturer:
            continue
        for field in model._meta.get_fields():
            if isinstance(field, django_models.ManyToManyField) and field.related_model is Lecturer:
                m2m_fields.append((model, field.name))
            elif isinstance(field, (django_models.ForeignKey, django_models.OneToOneField)) \
                    and field.related_model is Lecturer:
                fk_fields.append((model, field.name))
    return fk_fields, m2m_fields


def _reassign_and_delete(keeper, loser, fk_fields, m2m_fields, stdout):
    # Fill in anything the keeper is missing but the loser has, so no
    # data is silently lost.
    changed = False
    if not keeper.department_id and loser.department_id:
        keeper.department_id = loser.department_id
        changed = True
    if not keeper.max_load_override and loser.max_load_override:
        keeper.max_load_override = loser.max_load_override
        changed = True
    if not keeper.user_id and loser.user_id:
        # OneToOne: must clear it off the loser first or the assignment
        # below collides with the not-null unique constraint.
        transferred_user_id = loser.user_id
        loser.user_id = None
        loser.save(update_fields=["user"])
        keeper.user_id = transferred_user_id
        changed = True
    if changed:
        keeper.save()

    for model, field_name in m2m_fields:
        related_qs = model.objects.filter(**{field_name: loser})
        for instance in related_qs:
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
                    # keeper already has an equivalent row (unique_together
                    # collision, e.g. already allocated the same course) --
                    # the loser's copy of it is now redundant.
                    stdout.write(
                        f"    ! dropped redundant {model.__name__}#{instance.pk} "
                        f"(keeper already has an equivalent row)"
                    )
                    instance.delete()

    loser.delete()


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = (
        "Find Lecturer rows that are the same person written differently "
        "(full name vs initials, with/without a department, mismatched "
        "designation), merge every allocation/reference onto one keeper "
        "row, and delete the redundant rows. Runs for real by default."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                             help="Preview only -- report what would happen and write nothing.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        groups = find_duplicate_groups()
        auto_groups = [g for g in groups if g.confidence == "AUTO"]
        review_groups = [g for g in groups if g.confidence == "REVIEW"]

        self.stdout.write(
            f"Found {len(groups)} candidate duplicate group(s): "
            f"{len(auto_groups)} AUTO (merged automatically), "
            f"{len(review_groups)} REVIEW (needs a human decision, never auto-merged)."
        )
        for g in groups:
            self._print_group(g)

        if not auto_groups:
            self.stdout.write(self.style.WARNING("Nothing to merge."))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN -- {len(auto_groups)} AUTO group(s) would be merged. "
                f"Re-run without --dry-run to write changes."
            ))
            return

        fk_fields, m2m_fields = _discover_referencing_fields()
        self.stdout.write(
            f"Reassigning references via {len(fk_fields)} FK/O2O field(s) and "
            f"{len(m2m_fields)} M2M field(s) discovered across the whole project."
        )

        merged = 0
        with transaction.atomic():
            for g in auto_groups:
                for loser in g.losers:
                    self.stdout.write(f"  merging #{loser.pk} '{loser.name}' -> #{g.keeper.pk} '{g.keeper.name}'")
                    _reassign_and_delete(g.keeper, loser, fk_fields, m2m_fields, self.stdout)
                    merged += 1

        self.stdout.write(self.style.SUCCESS(f"Done. Merged/deleted {merged} duplicate lecturer row(s)."))

    # -- helpers -----------------------------------------------------------

    def _print_group(self, g):
        tag = self.style.SUCCESS("[AUTO]  ") if g.confidence == "AUTO" else self.style.WARNING("[REVIEW]")
        self.stdout.write(f"{tag} keep #{g.keeper.pk} {g.keeper.payroll_number} "
                           f"'{g.keeper.name}' ({g.keeper.designation}, dept={g.keeper.department})")
        for loser in g.losers:
            self.stdout.write(f"         merge #{loser.pk} {loser.payroll_number} "
                               f"'{loser.name}' ({loser.designation}, dept={loser.department})")
        designations = g.designations()
        if len(designations) > 1:
            self.stdout.write(self.style.WARNING(
                f"         ! conflicting designations in this group: {', '.join(designations)} "
                f"-- keeper keeps '{g.keeper.designation}', verify by hand."
            ))
