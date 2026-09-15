"""
force_merge_confirmed_lecturer_pairs.py
========================================
Run inside the Django shell:

    python manage.py shell < force_merge_confirmed_lecturer_pairs.py

or paste interactively into `python manage.py shell`.

What this does
---------------
merge_duplicate_lecturers (and clean_lecturer_duplicates.py) deliberately
never auto-merge REVIEW groups -- a bare surname or a department mismatch
is weak evidence on its own. lecturer_duplicate_review.csv listed 12 such
pairs; you manually confirmed 10 of them by hand as the same real person.
This script force-merges EXACTLY those 10 confirmed (keeper_pk, loser_pk)
pairs below -- nothing else, no re-detection, no re-grading.

Deliberately EXCLUDED (left untouched, still in the DB as-is):
    11226 Anita Mwende Mutegi  <-> 11706 M. Mutegi        (different depts)
    11226 Anita Mwende Mutegi  <-> 11793 Miriam Mutegi     (different depts,
                                                             different first
                                                             name entirely --
                                                             very likely two
                                                             different people)

Reassignment logic
-------------------
Reuses the exact same generic FK/O2O/M2M reflection + reassign-then-delete
helpers as lecturer_portal/management/commands/merge_duplicate_lecturers.py
(_discover_referencing_fields / _reassign_and_delete) rather than
duplicating that logic, so:
  - every reference anywhere in the project (course allocations, exam
    timetable, resits, ODeL, blocked slots, preferences, ...) is
    re-pointed from loser -> keeper before the loser row is deleted
  - the keeper picks up department/max_load_override/user from the loser
    if the keeper is missing them
  - unique_together collisions on reassignment (keeper already has an
    equivalent row) drop the loser's redundant copy instead of crashing
  - each pair is merged inside its own DB transaction

Safety checks
-------------
Before touching anything, each pair is verified against the pk/payroll/
name recorded in lecturer_duplicate_review.csv. If a row's pk no longer
exists, or its payroll_number/name has drifted from what was reviewed,
that pair is SKIPPED and flagged -- never guessed at.

Usage
-----
    DRY_RUN = True   (default) - prints the plan, writes nothing.
    DRY_RUN = False  - performs the 10 merges for real.
"""

from lecturer_portal.models import Lecturer
from lecturer_portal.management.commands.merge_duplicate_lecturers import (
    _discover_referencing_fields,
    _reassign_and_delete,
)
from django.db import transaction

DRY_RUN = False

# (keeper_pk, keeper_payroll, keeper_name, loser_pk, loser_payroll, loser_name, reason)
CONFIRMED_PAIRS = [
    (11169, "CHU/0004", "Andrew Thiuru Muguna", 11684, "CHU/0675", "Muguna",
     "one side is a bare surname with no given name"),
    (11466, "CHU/0316", "David Mugambi", 11476, "CHU/0361", "Mugambi",
     "one side is a bare surname with no given name"),
    (11227, "CHU/0065", "Catherine Kathure Kaimenyi", 11692, "CHU/0683", "Kaimenyi",
     "one side is a bare surname with no given name"),
    (11264, "CHU/0102", "Annah Njoki Ngeretha", 11691, "CHU/0682", "Ngeretha",
     "one side is a bare surname with no given name"),
    (11930, "TEMP9004", "Antony Kimathi", 11927, "TEMP9001", "Kimathi",
     "one side is a bare surname with no given name"),
    (11370, "CHU/0211", "Joab Mwange Ifedha", 11490, "CHU/0386", "Mwange Ifedha",
     "different departments set"),
    (11412, "CHU/0254", "Joseph Muema Kavulya", 11494, "CHU/0395", "Kavulya",
     "one side is a bare surname with no given name"),
    (11430, "CHU/0272", "Marcel Odhiambo Ohanga", 11716, "CHU/0707", "Ohanga",
     "one side is a bare surname with no given name"),
    (11707, "CHU/0698", "Alaka", 11714, "CHU/0705", "E. Alaka",
     "one side is a bare surname with no given name"),
    (11893, "TEMP0107", "Kathuri Kathure", 11925, "TEMP0144", "Kathure",
     "one side is a bare surname with no given name"),
]


def _verify(pk, expected_payroll, expected_name):
    try:
        lec = Lecturer.objects.get(pk=pk)
    except Lecturer.DoesNotExist:
        return None, f"pk {pk} no longer exists"
    if lec.payroll_number != expected_payroll:
        return None, (f"pk {pk} payroll drifted: expected '{expected_payroll}', "
                       f"now '{lec.payroll_number}'")
    if lec.name != expected_name:
        return None, (f"pk {pk} name drifted: expected '{expected_name}', "
                       f"now '{lec.name}'")
    return lec, None


def run():
    print(f"{'DRY RUN' if DRY_RUN else 'LIVE'} -- force-merging {len(CONFIRMED_PAIRS)} "
          f"manually-confirmed pair(s)\n")

    fk_fields, m2m_fields = _discover_referencing_fields()
    print(f"Discovered {len(fk_fields)} FK/O2O field(s) and {len(m2m_fields)} "
          f"M2M field(s) across the project that reference Lecturer.\n")

    plan = []
    skipped = []
    for (k_pk, k_payroll, k_name, l_pk, l_payroll, l_name, reason) in CONFIRMED_PAIRS:
        keeper, k_err = _verify(k_pk, k_payroll, k_name)
        loser, l_err = _verify(l_pk, l_payroll, l_name)
        if k_err or l_err:
            skipped.append((k_pk, l_pk, k_err or l_err))
            print(f"  ! SKIPPING pair (#{k_pk} <-> #{l_pk}): {k_err or l_err}")
            continue
        plan.append((keeper, loser, reason))
        print(f"  keep   #{keeper.pk} {keeper.payroll_number} '{keeper.name}' "
              f"({keeper.designation}, dept={keeper.department})")
        print(f"  merge  #{loser.pk} {loser.payroll_number} '{loser.name}' "
              f"({loser.designation}, dept={loser.department})")
        print(f"         reason on file: {reason}\n")

    if skipped:
        print(f"\n{len(skipped)} pair(s) skipped -- verify these by hand, nothing "
              f"touched for them:")
        for k_pk, l_pk, err in skipped:
            print(f"    #{k_pk} <-> #{l_pk}: {err}")

    if not plan:
        print("\nNothing to merge.")
        return

    if DRY_RUN:
        print(f"\nDRY RUN -- {len(plan)} pair(s) would be merged, "
              f"{len(skipped)} skipped. Set DRY_RUN = False to apply.")
        return

    merged = 0
    with transaction.atomic():
        for keeper, loser, reason in plan:
            print(f"  merging #{loser.pk} '{loser.name}' -> #{keeper.pk} '{keeper.name}'")
            _reassign_and_delete(keeper, loser, fk_fields, m2m_fields, _StdoutShim())
            merged += 1

    print(f"\nDone. Merged/deleted {merged} lecturer row(s).")
    if skipped:
        print(f"{len(skipped)} pair(s) were skipped -- see above, handle by hand.")


class _StdoutShim:
    """_reassign_and_delete expects something with .write(), like a
    management command's self.stdout -- plain print() stands in fine here."""
    def write(self, msg):
        print(msg)


run()
