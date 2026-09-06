"""
create_missing_lecturers_and_map.py
=====================================
Run inside the Django shell:

    python manage.py shell < create_missing_lecturers_and_map.py

This follows up on lecturer_mapping_review.csv. Per your confirmation:

  - Martin Mule Mutie   -> already exists correctly in the DB (CHU/0131),
                            stored as plain "Martin Mule Mutie" with no
                            designation baked into the name field. No
                            action needed on the lecturer itself.
  - Samson Njogu Muriuki -> already exists correctly (CHU/0033). No
                            action needed on the lecturer itself.
  - Mark Onyango Okongo  -> already exists correctly (CHU/0092). No
                            action needed on the lecturer itself.
  - Teresia Mbothia      -> does NOT exist yet (none of the suggested
                            near-matches were actually her) -> CREATE,
                            department = Physical Sciences, then map to
                            her courses.
  - Teddy Mungai         -> does NOT exist yet (distinct from "Teddy
                            Mutugi" / "Teddy Mutugi Wanjuki" already in
                            the DB) -> CREATE, department = Physical
                            Sciences, then map to his course.

IMPORTANT - what this script does NOT fix
------------------------------------------
The three "already correct" lecturers above were flagged in the review
CSV under "No matching ProgramCourse", not a lecturer-identity problem
-- i.e. the course codes below don't exist as ProgramCourse rows for
Program "Bachelor of Science" at all, so there's nothing to point their
CourseAllocation.lecturer at yet:

    PHYS 333/233  (-> Samson Njogu Muriuki)
    EPHY 322      (-> Dr. Elosy Gatakaa, already resolved elsewhere)
    MATH 408      (-> Mark Onyango Okongo)
    PHYS 485      (-> Martin Mule Mutie)

That's a curriculum/course-code problem (a typo, a combined code like
"333/233" that needs splitting, or a ProgramCourse that's simply
missing for that cohort), not something this script can safely guess.
Confirm the correct course codes for those four rows and they can be
mapped in a follow-up pass.

Safe to re-run: if a lecturer with the exact target name already
exists (e.g. you run this twice), it's reused rather than duplicated.
"""

import re

from django.db import transaction

from program_management.models import Program, ProgramCourse
from course_allocation.models import CourseAllocation
from lecturer_portal.models import Lecturer
from department_management.models import Department

try:
    from program_management.code_utils import normalize_code
except ImportError:
    def normalize_code(code):
        if not code:
            return ""
        code = re.sub(r"\s+", " ", code.strip().upper())
        return re.sub(r"^([A-Z]+)\s*(\d+)", r"\1 \2", code)

# ─────────────────────────────── CONFIG ────────────────────────────────
DRY_RUN = False
PROGRAM_NAME = "Bachelor of Science"
DEPARTMENT_NAME = "Physical Sciences"

NEW_LECTURERS = [
    {
        "name": "Teresia Mbothia",
        "designation": "Ms",
        "courses": ["MATH 125", "MATH 302", "MATH 405"],
    },
    {
        "name": "Teddy Mungai",
        "designation": "Mr",
        "courses": ["MATH 441"],
    },
]


# ────────────────────────────── HELPERS ────────────────────────────────

def _slug(name):
    return re.sub(r"[^a-z0-9.]+", ".", name.lower()).strip(".")


def generate_unique_payroll_number():
    """CHU/xxxx and TEMPxxxx are both already in use as real conventions,
    so new manually-created records get their own clearly-labelled
    prefix instead of colliding with (or looking like) either."""
    n = 1
    while True:
        candidate = f"NEW/{n:04d}"
        if not Lecturer.objects.filter(payroll_number=candidate).exists():
            return candidate
        n += 1


def generate_unique_email(name):
    base = _slug(name)
    candidate = f"{base}@chuka.ac.ke"
    n = 2
    while Lecturer.objects.filter(email=candidate).exists():
        candidate = f"{base}{n}@chuka.ac.ke"
        n += 1
    return candidate


def get_or_create_lecturer(entry, department):
    existing = Lecturer.objects.filter(name__iexact=entry["name"]).first()
    if existing:
        print(f"  = {entry['name']!r} already exists as #{existing.pk} "
              f"({existing.payroll_number}) - reusing, not creating a duplicate.")
        return existing

    payroll = generate_unique_payroll_number()
    email = generate_unique_email(entry["name"])
    print(f"  + creating Lecturer {entry['name']!r} "
          f"({entry['designation']}, {department.name}, payroll={payroll}, email={email})"
          f"{' (would create)' if DRY_RUN else ''}")

    if DRY_RUN:
        # Return an unsaved instance so the mapping step below can still
        # print what it WOULD do, without touching the DB.
        return Lecturer(
            name=entry["name"], designation=entry["designation"],
            department=department, payroll_number=payroll, email=email,
        )

    lecturer = Lecturer.objects.create(
        name=entry["name"], designation=entry["designation"],
        department=department, payroll_number=payroll, email=email,
    )
    return lecturer


def map_courses(lecturer, course_codes, program):
    for raw_code in course_codes:
        code = normalize_code(raw_code)
        pc = ProgramCourse.objects.filter(program=program, course_code=code).order_by("-student_cohort").first()
        if not pc:
            print(f"    ! {code}: no matching ProgramCourse - skipped")
            continue

        cas = list(CourseAllocation.objects.filter(program_course=pc))
        if not cas:
            print(f"    ! {code}: ProgramCourse found but no CourseAllocation rows exist yet - skipped")
            continue

        for ca in cas:
            if ca.lecturer_id == getattr(lecturer, "id", None) and lecturer.pk:
                print(f"    = {code}: already set to {lecturer.name}")
                continue
            print(f"    -> {code}: lecturer = {lecturer.name}"
                  f"{' (would set)' if DRY_RUN else ''}")
            if not DRY_RUN:
                ca.lecturer = lecturer
                ca.save(update_fields=["lecturer"])


# ───────────────────────────────── MAIN ─────────────────────────────────

def run():
    print("=" * 70)
    print("Creating confirmed-missing lecturers and mapping their courses")
    print(f"DRY_RUN = {DRY_RUN}")
    print("=" * 70)

    program = Program.objects.get(name__iexact=PROGRAM_NAME)
    department = Department.objects.get(name__iexact=DEPARTMENT_NAME)

    def process():
        for entry in NEW_LECTURERS:
            print(f"\n{entry['name']}:")
            lecturer = get_or_create_lecturer(entry, department)
            map_courses(lecturer, entry["courses"], program)

    if DRY_RUN:
        process()
    else:
        with transaction.atomic():
            process()

    print("\n" + "-" * 70)
    print("Already-correct lecturers - no changes made, course-code issue remains:")
    print("-" * 70)
    for name, payroll, codes in [
        ("Martin Mule Mutie", "CHU/0131", ["PHYS 485"]),
        ("Samson Njogu Muriuki", "CHU/0033", ["PHYS 333/233"]),
        ("Mark Onyango Okongo", "CHU/0092", ["MATH 408"]),
    ]:
        print(f"  {name} ({payroll}): {', '.join(codes)} - no matching ProgramCourse yet, "
              f"confirm the correct course code before mapping.")

    if DRY_RUN:
        print("\nDRY_RUN is True — nothing was written. Set DRY_RUN = False to apply.")


run()
