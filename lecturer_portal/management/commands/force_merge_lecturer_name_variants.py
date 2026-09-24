# lecturer_portal/management/commands/force_merge_lecturer_name_variants.py
"""
One-off, repeatable merge for the SECOND batch of confirmed duplicate
Lecturer rows: name-order inversions ("Sigar Otula" vs "Kenneth Otula
Sigar"), dropped middle/last names ("Gilbert Odilla" vs "Gilbert Odilla
Abura"), a person split across TWO extra rows instead of one, and
outright misspellings ("Isboke" vs "Isaboke", "Gitaari" vs "Gitari").

The first batch (round 1) is handled by force_merge_confirmed_lecturer_pairs.py
in the repo root, which pins exact pks from one DB snapshot and is run
via `python manage.py shell < ...`. This is a proper management command
instead, so it can be run the same way as the other cleanup commands in
this project (and works cleanly with `docker exec ... --apply`, no
stdin piping needed):

    docker exec -it <container> python manage.py force_merge_lecturer_name_variants
    docker exec -it <container> python manage.py force_merge_lecturer_name_variants --apply

merge_duplicate_lecturers's automatic surname+initials matcher does not
catch any of these on its own -- name-order inversions and dropped
tokens don't share a common "last token", and misspellings don't match
by string equality at all. So, same as round 1, this command does NOT
re-derive anything: it force-merges EXACTLY the groups listed in
CONFIRMED_GROUPS below, which were confirmed by hand from the payroll
list.

Keyed by payroll_number, not pk
--------------------------------
payroll_number is unique and is what the groups below were audited
against, so this runs correctly regardless of which environment /
snapshot it's pointed at (staging vs production, or after a restore),
and needs no re-editing if pks differ between them.

Groups, not just pairs
-----------------------
Two of the confirmed cases are 3-way (one real person accidentally
split into three rows: "Marcel Odhiambo Ohanga" / "Marcel" / "Ohanga",
and "Maryanne Gitari" / "Maryann Gitari" / "Maryanne Gitaari"). Each
entry in CONFIRMED_GROUPS is (keeper_payroll, keeper_name, [(loser_
payroll, loser_name), ...], reason) so a group can have any number of
losers, all merged onto the same keeper inside one transaction.

Deliberately EXCLUDED from CONFIRMED_GROUPS -- printed under --review
instead, never merged by this command:
    CHU/0092 Mark Onyango Okongo (Physical Sciences)
        <-> CHU/0554 Okongo (Computer Science)
        -- different departments AND a bare surname on one side; the
           notes themselves say only "possibly same person".
    CHU/0077 Christopher Nkonge Kiboro (Prof, Social Sciences)
        <-> TEMP0019 Nkonge (Mr, Humanities)
        -- different department, different designation, bare surname;
           notes say "possibly same; check".
Also skipped entirely -- not duplicate rows at all, just a typo inside
a single existing record's email field, nothing to merge:
    CHU/0093 Bernard Ong'Era Osero        (email says "oseroh")
    CHU/0130 William Murithi Ndeke        (email says "ndege")
If you want those two email fields corrected, that's a one-line
`Lecturer.objects.filter(payroll_number=...).update(email=...)`, not a
merge -- not included here since it touches a different field, not
duplicate-row cleanup.

Overlap with round 1
---------------------
A few payrolls here (CHU/0254/CHU/0395, CHU/0272/CHU/0707,
CHU/0211/CHU/0386) were also in the round-1 confirmed-pairs batch. If
that script already ran, those payrolls will simply be reported as
"not found" below and skipped -- harmless, kept here so this command is
self-contained and safe to run even if round 1 hasn't been applied yet.

Reassignment logic
-------------------
Reuses the exact same generic FK/O2O/M2M reflection + reassign-then-
delete helpers as merge_duplicate_lecturers.py
(_discover_referencing_fields / _reassign_and_delete):
  - every reference anywhere in the project (course allocations, exam
    timetable, resits, ODeL, blocked slots, preferences, ...) is
    re-pointed from loser -> keeper before the loser row is deleted
  - the keeper picks up department/max_load_override/user from a loser
    if the keeper is missing them
  - unique_together collisions on reassignment (keeper already has an
    equivalent row) drop the loser's redundant copy instead of crashing
  - each group is merged inside its own DB transaction

Safety checks
-------------
Before touching anything, every payroll_number is looked up and its
current name is compared against what's recorded below. If a payroll
number doesn't exist (already merged, or a typo) or its name has
drifted from what was reviewed, that ONE row is skipped and flagged --
never guessed at. A group still merges its other, verified rows even
if one member is skipped.

Usage
-----
    python manage.py force_merge_lecturer_name_variants            # dry run, prints the plan
    python manage.py force_merge_lecturer_name_variants --apply    # perform the merges for real
    python manage.py force_merge_lecturer_name_variants --review   # also print the 2 excluded/uncertain pairs
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from lecturer_portal.models import Lecturer
from lecturer_portal.management.commands.merge_duplicate_lecturers import (
    _discover_referencing_fields,
    _reassign_and_delete,
)

# (keeper_payroll, keeper_name, [(loser_payroll, loser_name), ...], reason)
CONFIRMED_GROUPS = [
    ("CHU/0119", "Kenneth Otula Sigar",
     [("CHU/0522", "Sigar Otula")],
     "name-order inversion"),

    ("CHU/0031", "Gilbert Odilla Abura",
     [("CHU/0591", "Gilbert Odilla")],
     "second entry drops the surname 'Abura'"),

    ("CHU/0088", "Raphael Mwiti Gikunda",
     [("CHU/0592", "Raphael Mwiti")],
     "second entry drops the surname 'Gikunda'"),

    ("CHU/0272", "Marcel Odhiambo Ohanga",
     [("CHU/0706", "Marcel"), ("CHU/0707", "Ohanga")],
     "one real person split into three rows: full name, bare first name, "
     "bare surname"),

    ("CHU/0264", "Josephat Machoka Bundi",
     [("CHU/0708", "Josephat Machoka")],
     "second entry drops the surname 'Bundi'"),

    ("CHU/0254", "Joseph Muema Kavulya",
     [("CHU/0395", "Kavulya")],
     "bare surname (also in round-1 batch; kept here for a "
     "self-contained re-run)"),

    ("CHU/0157", "Brian Rotich Kanyongi",
     [("CHU/0329", "Brian Rotich")],
     "second entry drops the surname 'Kanyongi'"),

    ("CHU/0153", "Virginia Kavuu Muia",
     [("CHU/0323", "Virginia Kavuu")],
     "second entry drops the surname 'Muia'"),

    ("CHU/0151", "Justin Mugendi Njeru",
     [("CHU/0318", "Justin Mugendi")],
     "second entry drops the surname 'Njeru'"),

    ("CHU/0079", "Agatha Mutio Nthenge",
     [("CHU/0362", "Agatha Mutio")],
     "second entry drops the surname 'Nthenge'"),

    ("CHU/0211", "Joab Mwange Ifedha",
     [("CHU/0386", "Mwange Ifedha")],
     "bare/partial name (also in round-1 batch; kept here for a "
     "self-contained re-run)"),

    ("CHU/0057", "Kibetu Dickson Kinoti",
     [("CHU/0417", "Kinoti Kibetu")],
     "name-order inversion"),

    ("CHU/0142", "Kenneth Kigundu Macharia",
     [("CHU/0369", "Kenneth Kigundu")],
     "second entry drops the surname 'Macharia'"),

    ("CHU/0061", "Caroline Mutunga Ndunge",
     [("CHU/0373", "Carolyne Mutunga")],
     "spelling variant ('Caroline'/'Carolyne') + dropped surname 'Ndunge'"),

    ("CHU/0156", "Monica Buyatsi Oundo",
     [("CHU/0415", "Monicah Oundo")],
     "spelling variant ('Monica'/'Monicah') + dropped middle name 'Buyatsi'"),

    ("CHU/0158", "Charity Nyaboke Onsinyo",
     [("CHU/0392", "Charity Nyaboke")],
     "second entry drops the surname 'Onsinyo'"),

    ("CHU/0226", "Boniface Munene Rufo",
     [("CHU/0380", "Rufo Munene")],
     "name-order inversion + dropped first name 'Boniface'"),

    ("CHU/0114", "Crispin Ong'Era Isaboke",
     [("CHU/0383", "Crispine Isboke")],
     "misspelling ('Crispin'/'Crispine', 'Isaboke'/'Isboke') + dropped "
     "middle name"),

    ("CHU/0129", "Humphrey Kirimi Ireri",
     [("TEMP0189", "Humphrey Kirimi")],
     "second entry drops the surname 'Ireri'"),

    ("CHU/0253", "Njagi Jackin Nanua",
     [("CHU/0512", "Jackin N. Nanua")],
     "name-order inversion + initial for 'Njagi'"),

    ("CHU/0310", "Maryanne Gitari",
     [("CHU/0344", "Maryann Gitari"), ("CHU/0352", "Maryanne Gitaari")],
     "one real person split into three rows via spelling variants of "
     "both first and last name"),

    ("CHU/0423", "Mercy Bwire",
     [("TEMP0255", "Mercy Bwera")],
     "misspelling ('Bwire'/'Bwera')"),

    ("CHU/0496", "Hannington Sitati",
     [("CHU/0212", "Hanningtone Sitati")],
     "misspelling ('Hannington'/'Hanningtone')"),

    ("CHU/0115", "Stephen Kairu Wambugu",
     [("CHU/0505", "S.K. Wambugu")],
     "initials vs full name"),
]

# Flagged in the notes as only "possibly" the same person (different
# department, and/or a bare surname on one side). Printed only when
# --review is passed. NEVER merged by this command -- confirm by hand
# first, the same way the groups above were confirmed before being
# added here.
REVIEW_ONLY = [
    ("CHU/0092", "Mark Onyango Okongo", "CHU/0554", "Okongo",
     "different departments (Physical Sciences vs Computer Science); "
     "notes say 'possibly same person'"),
    ("CHU/0077", "Christopher Nkonge Kiboro", "TEMP0019", "Nkonge",
     "different department and designation (Prof/Social Sciences vs "
     "Mr/Humanities); notes say 'possibly same; check'"),
]


def _verify(payroll, expected_name):
    try:
        lec = Lecturer.objects.get(payroll_number=payroll)
    except Lecturer.DoesNotExist:
        return None, f"payroll {payroll} not found (already merged elsewhere?)"
    except Lecturer.MultipleObjectsReturned:
        return None, f"payroll {payroll} matches more than one row -- fix that first"
    if lec.name != expected_name:
        return None, (f"payroll {payroll} name drifted: expected '{expected_name}', "
                       f"now '{lec.name}'")
    return lec, None


class Command(BaseCommand):
    help = (
        "Force-merge the round-2 batch of manually-confirmed duplicate "
        "Lecturer rows (name-order inversions, dropped names, "
        "misspellings, split-into-3 cases). Dry run by default; pass "
        "--apply to actually merge and delete."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                             help="Perform the merges for real. Without this flag, "
                                  "only prints the plan and writes nothing.")
        parser.add_argument("--review", action="store_true",
                             help="Also print the excluded/uncertain pairs that this "
                                  "command deliberately never merges.")

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        self.stdout.write(
            f"{'LIVE' if apply_changes else 'DRY RUN'} -- force-merging "
            f"{len(CONFIRMED_GROUPS)} manually-confirmed group(s)\n"
        )

        fk_fields, m2m_fields = _discover_referencing_fields()
        self.stdout.write(
            f"Discovered {len(fk_fields)} FK/O2O field(s) and {len(m2m_fields)} "
            f"M2M field(s) across the project that reference Lecturer.\n"
        )

        plan = []          # (keeper, [loser, ...], reason)
        skipped_rows = []  # individual rows that failed verification

        for keeper_payroll, keeper_name, losers, reason in CONFIRMED_GROUPS:
            keeper, k_err = _verify(keeper_payroll, keeper_name)
            if k_err:
                skipped_rows.append((keeper_payroll, k_err))
                self.stdout.write(self.style.WARNING(
                    f"  ! SKIPPING whole group (keeper {keeper_payroll}): {k_err}"))
                continue

            verified_losers = []
            for loser_payroll, loser_name in losers:
                loser, l_err = _verify(loser_payroll, loser_name)
                if l_err:
                    skipped_rows.append((loser_payroll, l_err))
                    self.stdout.write(self.style.WARNING(
                        f"  ! SKIPPING loser {loser_payroll} in {keeper_payroll}'s "
                        f"group: {l_err}"))
                    continue
                verified_losers.append(loser)

            if not verified_losers:
                self.stdout.write(
                    f"  ! Nothing left to merge for keeper {keeper_payroll} "
                    f"'{keeper.name}' -- all losers skipped.\n"
                )
                continue

            plan.append((keeper, verified_losers, reason))
            self.stdout.write(
                f"  keep   #{keeper.pk} {keeper.payroll_number} '{keeper.name}' "
                f"({keeper.designation}, dept={keeper.department})"
            )
            for loser in verified_losers:
                self.stdout.write(
                    f"  merge  #{loser.pk} {loser.payroll_number} '{loser.name}' "
                    f"({loser.designation}, dept={loser.department})"
                )
            self.stdout.write(f"         reason on file: {reason}\n")

        if options["review"]:
            self.stdout.write("\n-- REVIEW ONLY (never auto-merged by this command) --")
            for k_payroll, k_name, l_payroll, l_name, reason in REVIEW_ONLY:
                self.stdout.write(f"  {k_payroll} '{k_name}'  <->  {l_payroll} '{l_name}'")
                self.stdout.write(f"    reason to hold off: {reason}")

        if skipped_rows:
            self.stdout.write(self.style.WARNING(
                f"\n{len(skipped_rows)} row(s) skipped -- verify these by hand:"))
            for payroll, err in skipped_rows:
                self.stdout.write(f"    {payroll}: {err}")

        if not plan:
            self.stdout.write(self.style.WARNING("\nNothing to merge."))
            return

        total_losers = sum(len(losers) for _, losers, _ in plan)
        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                f"\nDRY RUN -- {len(plan)} group(s) / {total_losers} loser row(s) "
                f"would be merged. Re-run with --apply to write changes."
            ))
            return

        merged = 0
        with transaction.atomic():
            for keeper, losers, reason in plan:
                for loser in losers:
                    self.stdout.write(
                        f"  merging #{loser.pk} '{loser.name}' -> #{keeper.pk} '{keeper.name}'")
                    _reassign_and_delete(keeper, loser, fk_fields, m2m_fields, self.stdout)
                    merged += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Merged/deleted {merged} lecturer row(s) across {len(plan)} group(s)."))
        if skipped_rows:
            self.stdout.write(self.style.WARNING(
                f"{len(skipped_rows)} row(s) were skipped -- see above, handle by hand."))
