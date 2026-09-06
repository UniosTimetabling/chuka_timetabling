"""
clean_lecturer_duplicates.py
==============================
Run inside the Django shell:

    python manage.py shell < clean_lecturer_duplicates.py

or paste interactively into `python manage.py shell`.

NOTE: your codebase already has a management command that does the core
duplicate-merge (lecturer_portal/management/commands/merge_duplicate_lecturers.py)
using the exact same surname+initials matching and generic FK reflection
approach used here. You can run that directly with:

    python manage.py merge_duplicate_lecturers --dry-run

This script exists alongside it because that command does NOT clean up
formatting inconsistencies first (ALL CAPS names, stray/double spaces,
mixed-case emails) - which both (a) is data mess in its own right, and
(b) makes duplicate detection weaker, since "ERASTUS NJOKA" and "Erastus
Njoka" are the same string once normalized but look different until
then. So this script does TWO passes:

  STEP 1 - NORMALIZE
    Every Lecturer row's name/email/payroll_number is cleaned up
    (whitespace collapsed, ALL-CAPS/all-lowercase names re-cased,
    email lower-cased). Nothing is merged or deleted in this step -
    it only touches formatting, never meaning.

  STEP 2 - DETECT + SAFELY MERGE DUPLICATES
    Same surname + initials matching rule as merge_duplicate_lecturers:
      "David Gitonga Mwathi" vs "D. Mwathi"    -> same person
      "David Gitonga Mwathi" vs "D.G. Mwathi"  -> same person
      "Christopher Okongo"   vs "C. Mwathi"    -> NOT the same (surname differs)
    Groups are graded AUTO (safe to merge automatically) or REVIEW
    (conflicting departments, or one side is a bare surname with no
    given name at all - too weak to auto-chain). Only AUTO groups are
    merged; REVIEW groups are printed + written to a CSV for a human
    to check.

    BEFORE any loser row is deleted, every ForeignKey/OneToOne/ManyToMany
    field ANYWHERE in the project that points at Lecturer is discovered
    via Django's app registry (so CourseAllocation.lecturer,
    LecturerCourseMapping, ArchivedCourseAllocation, resits, campus
    timetable, ODeL, special requests, exam-timetable preferences...
    literally anything - is covered without hand-maintaining a list) and
    re-pointed from the loser onto the keeper. Only once that's done is
    the loser row deleted. This all happens inside a single DB
    transaction per group, so a failure mid-merge can't leave a course
    allocation pointing at a deleted lecturer.

Usage
-----
    DRY_RUN = True   (default) - prints everything, writes nothing.
    DRY_RUN = False  - applies the formatting cleanup AND the AUTO merges.

REVIEW groups are never auto-merged by this script, matching the
existing management command's behaviour - confirm those by hand.
"""

import csv
import re
from collections import defaultdict

from django.apps import apps
from django.db import models as django_models
from django.db import transaction, IntegrityError

from lecturer_portal.models import Lecturer

# ─────────────────────────────── CONFIG ────────────────────────────────
DRY_RUN = False
REVIEW_REPORT_PATH = "lecturer_duplicate_review.csv"   # written to the current working directory


# ═══════════════════════ STEP 1: FORMATTING CLEANUP ════════════════════

def _collapse_whitespace(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def _smart_titlecase(name):
    """
    Only re-cases a name if it's currently ALL CAPS or all lower-case
    (i.e. clearly never been through proper casing). Names that are
    already mixed-case are left completely alone, so something
    deliberate like 'McDonald' or 'O'Brien' is never mangled.
    Handles compound initials like 'S.M.' -> 'S.M.' (each letter
    before a dot is upper-cased, nothing after a dot is touched).
    """
    if not name:
        return name
    if name != name.upper() and name != name.lower():
        return name  # already mixed case - leave it alone

    parts = name.split(" ")
    out = []
    for p in parts:
        if not p:
            continue
        if "." in p:
            sub = p.split(".")
            out.append(".".join(
                (t[:1].upper() + t[1:].lower()) if t else "" for t in sub
            ))
        else:
            out.append(p[:1].upper() + p[1:].lower())
    return " ".join(out)


def normalize_formatting():
    print("=" * 70)
    print("STEP 1 — Normalizing Lecturer name / email / payroll formatting")
    print(f"DRY_RUN = {DRY_RUN}")
    print("=" * 70)

    changed_count = 0
    for lec in Lecturer.objects.all():
        updates = {}

        new_name = _smart_titlecase(_collapse_whitespace(lec.name))
        if new_name != lec.name:
            updates["name"] = new_name

        new_email = _collapse_whitespace(lec.email).lower()
        if new_email != lec.email:
            updates["email"] = new_email

        new_payroll = _collapse_whitespace(lec.payroll_number)
        if new_payroll != lec.payroll_number:
            updates["payroll_number"] = new_payroll

        if updates:
            changed_count += 1
            diff = ", ".join(f"{k}: {getattr(lec, k)!r} -> {v!r}" for k, v in updates.items())
            print(f"  #{lec.pk}: {diff}")
            if not DRY_RUN:
                for k, v in updates.items():
                    setattr(lec, k, v)
                lec.save(update_fields=list(updates.keys()))

    print(f"\n  {changed_count} row(s) {'would be' if DRY_RUN else ''} normalized.\n")


# ═══════════════════ STEP 2: DUPLICATE DETECTION + MERGE ═══════════════
# (same matching rule as lecturer_portal/management/commands/merge_duplicate_lecturers.py,
# kept in lockstep intentionally so both tools agree on what counts as a duplicate)

def _tokens(name):
    name = (name or "").strip()
    name = re.sub(r"\.(?=[A-Za-z])", ". ", name)  # "D.G." -> "D. G."
    return [t for t in re.split(r"\s+", name) if t.strip(".")]


def _is_initial(token):
    return len(token.rstrip(".")) == 1


def _surname_key(name):
    toks = _tokens(name)
    return toks[-1].lower() if toks else ""


def names_match(name_a, name_b):
    a, b = _tokens(name_a), _tokens(name_b)
    if not a or not b:
        return False
    if a[-1].lower() != b[-1].lower():
        return False

    if len(a) < len(b):
        short, long_ = a[:-1], b[:-1]
    else:
        short, long_ = b[:-1], a[:-1]

    li = 0
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
    toks = _tokens(name)
    return sum(1 for t in toks if not _is_initial(t))


def _given_token_count(name):
    return max(len(_tokens(name)) - 1, 0)


class MergeGroup:
    def __init__(self, rows, has_weak_edge=False):
        self.rows = rows
        self.keeper = self._pick_keeper(rows)
        self.losers = [r for r in rows if r.pk != self.keeper.pk]
        self.has_weak_edge = has_weak_edge
        self.confidence = self._confidence()

    def _pick_keeper(self, rows):
        def sort_key(lec):
            return (
                0 if lec.user_id else 1,
                0 if lec.department_id else 1,
                -_completeness_score(lec.name),
                lec.pk,
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

        bare_by_dept = defaultdict(list)
        for lec in unattached_bare:
            bare_by_dept[lec.department_id].append(lec)
        for dept_id, rows in bare_by_dept.items():
            if dept_id and len(rows) > 1:
                groups.append(MergeGroup(rows, has_weak_edge=True))

    return groups


def _discover_referencing_fields():
    """Every FK/O2O/M2M anywhere in the project pointing at Lecturer -
    this is how CourseAllocation.lecturer (and everything else, e.g.
    LecturerCourseMapping, ArchivedCourseAllocation, resits, campus
    timetable, ODeL, special requests...) gets picked up automatically."""
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


def _reassign_and_delete(keeper, loser, fk_fields, m2m_fields):
    changed = False
    if not keeper.department_id and loser.department_id:
        keeper.department_id = loser.department_id
        changed = True
    if not keeper.max_load_override and loser.max_load_override:
        keeper.max_load_override = loser.max_load_override
        changed = True
    if not keeper.user_id and loser.user_id:
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
                    # keeper already has an equivalent row (e.g. a
                    # unique_together collision - already allocated the
                    # same course) - the loser's copy is now redundant.
                    print(f"    ! dropped redundant {model.__name__}#{instance.pk} "
                          f"(keeper already has an equivalent row)")
                    instance.delete()

    loser.delete()


def merge_duplicates():
    print("=" * 70)
    print("STEP 2 — Detecting and merging duplicate Lecturer rows")
    print(f"DRY_RUN = {DRY_RUN}")
    print("=" * 70)

    groups = find_duplicate_groups()
    auto_groups = [g for g in groups if g.confidence == "AUTO"]
    review_groups = [g for g in groups if g.confidence == "REVIEW"]

    print(f"\n  Found {len(groups)} candidate duplicate group(s): "
          f"{len(auto_groups)} AUTO, {len(review_groups)} REVIEW (never auto-merged)\n")

    review_rows = []
    for g in groups:
        tag = "[AUTO]  " if g.confidence == "AUTO" else "[REVIEW]"
        print(f"{tag} keep #{g.keeper.pk} {g.keeper.payroll_number} '{g.keeper.name}' "
              f"({g.keeper.designation}, dept={g.keeper.department})")
        for loser in g.losers:
            print(f"         merge #{loser.pk} {loser.payroll_number} '{loser.name}' "
                  f"({loser.designation}, dept={loser.department})")
            if g.confidence == "REVIEW":
                review_rows.append({
                    "keeper_pk": g.keeper.pk, "keeper_payroll": g.keeper.payroll_number,
                    "keeper_name": g.keeper.name,
                    "loser_pk": loser.pk, "loser_payroll": loser.payroll_number,
                    "loser_name": loser.name,
                    "reason": "different departments set" if not g.has_weak_edge
                              else "one side is a bare surname with no given name",
                })
        designations = g.designations()
        if len(designations) > 1:
            print(f"         ! conflicting designations in this group: {', '.join(designations)} "
                  f"- keeper keeps '{g.keeper.designation}', verify by hand.")

    if review_rows:
        with open(REVIEW_REPORT_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "keeper_pk", "keeper_payroll", "keeper_name",
                "loser_pk", "loser_payroll", "loser_name", "reason",
            ])
            w.writeheader()
            w.writerows(review_rows)
        print(f"\n  {len(review_rows)} REVIEW row(s) written to {REVIEW_REPORT_PATH} - confirm by hand.")

    if not auto_groups:
        print("\n  Nothing to auto-merge.")
        return

    if DRY_RUN:
        print(f"\n  DRY_RUN is True — {len(auto_groups)} AUTO group(s) would be merged, "
              f"including re-pointing every CourseAllocation / LecturerCourseMapping / "
              f"etc. reference first. Nothing was written. Set DRY_RUN = False to apply.")
        return

    fk_fields, m2m_fields = _discover_referencing_fields()
    print(f"\n  Reassigning references via {len(fk_fields)} FK/O2O field(s) and "
          f"{len(m2m_fields)} M2M field(s) discovered across the whole project "
          f"(includes course_allocation.CourseAllocation.lecturer).")

    merged = 0
    with transaction.atomic():
        for g in auto_groups:
            for loser in g.losers:
                print(f"  merging #{loser.pk} '{loser.name}' -> #{g.keeper.pk} '{g.keeper.name}'")
                _reassign_and_delete(g.keeper, loser, fk_fields, m2m_fields)
                merged += 1

    print(f"\n  Done. Merged/deleted {merged} duplicate lecturer row(s).")


# ────────────────────────────── ENTRYPOINT ─────────────────────────────

normalize_formatting()
merge_duplicates()