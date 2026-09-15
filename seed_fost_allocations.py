# -*- coding: utf-8 -*-
"""
seed_fost_allocations.py
=========================

Creates / updates CourseAllocation rows for:

    Program : Bachelor of Science in Food Science & Technology
    Dept    : Food Technology   (the department running this allocation, i.e. DFTEC)

Source data (embedded below as SEED_* constants, taken from the two CSVs
you already imported / provided):

    fostv2programcourse.csv     -> SEED_PROGRAM_COURSES
    fostv2lecturermapping.csv   -> SEED_LECTURER_MAP

Behaviour
---------
For every row in SEED_PROGRAM_COURSES:

  1. Find the matching ProgramCourse (already imported by you) under the
     FOST program.
  2. Find the matching row in SEED_LECTURER_MAP by course_code.
       - If that row has a payroll_number -> it's an individual lecturer.
         We look them up by payroll_number (falls back to email, then
         exact name) and set `lecturer`.
       - If that row has NO payroll_number -> the "lecturer_name" column
         actually holds a DEPARTMENT name (a service unit, e.g.
         "Physical Sciences Department"). In that case we do NOT set a
         lecturer; instead we resolve that Department and set it as
         `origin_department`.
  3. get_or_create the CourseAllocation for (program_course, department,
     student_group=None) using the model's own
     `CourseAllocation.get_or_create_shared()` helper (this is exactly
     what it exists for), then UPDATE it in place with the resolved
     lecturer / origin_department / is_elective / course_name and save.
     So re-running this script is idempotent: existing rows are updated,
     not duplicated.

Run it with:

    python manage.py shell < seed_fost_allocations.py

or paste the whole file into `python manage.py shell`.
"""

import difflib
import re
from django.db import transaction

from program_management.models import Program, ProgramCourse
from program_management.code_utils import canonical_course_key
from department_management.models import Department
from lecturer_portal.models import Lecturer
from course_allocation.models import CourseAllocation

# ─────────────────────────────────────────────────────────────────────────
# CONFIG — tweak these if you reuse this script for another program/dept
# ─────────────────────────────────────────────────────────────────────────
PROGRAM_NAME = "Bachelor of Science in Food Science & Technology"

# The department actually running / hosting this allocation exercise
# (DFTEC minutes -> Food Technology).
ALLOCATING_DEPARTMENT_NAME = "Food Technology"

# Which student_cohort of ProgramCourse to allocate against. '0' = legacy
# / default cohort, which is what the fostv2programcourse.csv import used.
# If you imported under a specific cohort (e.g. "2025/2026"), change this.
STUDENT_COHORT = "0"

# If True: for rows where an INDIVIDUAL lecturer is assigned but that
# lecturer's home department differs from ALLOCATING_DEPARTMENT_NAME,
# also tag origin_department with the lecturer's own department. Left
# False by default to match the literal instruction: origin_department is
# only set when the mapping row names a DEPARTMENT instead of a person.
TAG_ORIGIN_FOR_BORROWED_LECTURERS = False

DRY_RUN = False  # set True to preview without writing anything

# ─────────────────────────────────────────────────────────────────────────
# SEED DATA — from fostv2programcourse.csv
# ─────────────────────────────────────────────────────────────────────────
SEED_PROGRAM_COURSES = [
    # course_code, course_name, year, semester, unit_type, student_cohort
    ("CHEM 102", "Physical and Inorganic Chemistry", 1, 1, "Core", "0"),
    ("COMS 101", "Communication Skills", 1, 1, "Core", "0"),
    ("ECON 100", "Micro Economics", 1, 1, "Core", "0"),
    ("ENSC 100", "Environmental Education", 1, 1, "Core", "0"),
    ("FOST 101", "Introduction to the Food Industry", 1, 1, "Core", "0"),
    ("MATH 100", "Introductory Mathematics", 1, 1, "Core", "0"),
    ("ZOOL 104", "General Zoology", 1, 1, "Core", "0"),
    ("PHIL 100", "Introduction to Philosophy", 1, 1, "Core", "0"),
    ("ZOOL 232", "Cell Biology", 2, 1, "Core", "0"),
    ("ANHE 215", "Animal Physiology", 2, 1, "Core", "0"),
    ("FOST 211", "Food Microbiology I", 2, 1, "Core", "0"),
    ("FOST 221", "Principles of Human Nutrition", 2, 1, "Core", "0"),
    ("FOST 233", "Food Processing Instrumentation", 2, 1, "Core", "0"),
    ("FOST 234", "Food Plant Utilities and Services", 2, 1, "Core", "0"),
    ("FOST 235", "Unit operations in food processing", 2, 1, "Core", "0"),
    ("FOST 232", "Food Processing Engineering II", 2, 1, "Core", "0"),
    ("CHEM 304", "Analytical Chemistry I", 3, 1, "Core", "0"),
    ("FOST 351", "Dairy Technology", 3, 1, "Core", "0"),
    ("ANSC 333", "Animal Nutrition and Livestock Feeding", 3, 1, "Core", "0"),
    ("FOST 323", "Food Chemistry II", 3, 1, "Core", "0"),
    ("FOST 341", "Cereals and Root Crop Technology", 3, 1, "Core", "0"),
    ("FOST 342", "Fruits & Vegetable Technology", 3, 1, "Core", "0"),
    ("FOST 343", "Non-Alcoholic Beverages Technology", 3, 1, "Core", "0"),
    ("ANHE 427", "Introduction to Animal Health and Diseases", 4, 1, "Core", "0"),
    ("DATM 456", "Dairy Technology", 4, 1, "Core", "0"),
    ("FOST 413", "Food Toxicology", 4, 1, "Core", "0"),
    ("FOST 425", "Applications of Enzyme Technology in Foods", 4, 1, "Core", "0"),
    ("FOST 461", "Food Packaging Storage and Distribution of Foods", 4, 1, "Core", "0"),
    ("FOST 462", "Food Plant Operations Management", 4, 1, "Core", "0"),
    ("FOST 463", "Food Quality Assurance", 4, 1, "Core", "0"),
    ("FOST 482", "Research Projects Proposals Writing and Seminars", 4, 1, "Core", "0"),
]

# ─────────────────────────────────────────────────────────────────────────
# SEED DATA — from fostv2lecturermapping.csv
# course_code, lecturer_name, payroll_number, lecturer_email
# Rows with blank payroll_number/email are DEPARTMENTS, not people.
# ─────────────────────────────────────────────────────────────────────────
SEED_LECTURER_MAP = [
    ("CHEM 102", "Physical Sciences Department", "", ""),
    ("COMS 101", "Social Sciences Department", "", ""),
    ("ECON 100", "Economics Department", "", ""),
    ("ENSC 100", "Environmental Science Department", "", ""),
    ("FOST 101", "Stephen Wachira Kariuki", "CHU/0148", "stephen.wachira@chuka.ac.ke"),
    ("MATH 100", "Mathematics Department", "", ""),
    ("ZOOL 104", "Biological Sciences Department", "", ""),
    ("PHIL 100", "Philosophy Department", "", ""),
    ("ZOOL 232", "Biological Sciences Department", "", ""),
    ("ANHE 215", "Animal Science Department", "", ""),
    ("FOST 211", "Joyce Wangui Njoki", "CHU/0180", "joyce.njoki@chuka.ac.ke"),
    ("FOST 221", "Johnson Kyalo Mwove", "CHU/0172", "johnson.mwove@chuka.ac.ke"),
    ("FOST 233", "Francis Gichuho Irungu", "CHU/0170", "francis.irungu@chuka.ac.ke"),
    ("FOST 234", "Francis Gichuho Irungu", "CHU/0170", "francis.irungu@chuka.ac.ke"),
    ("FOST 235", "Njagi Jackin Nanua", "CHU/0253", "njagi.nanua@chuka.ac.ke"),
    ("FOST 232", "Stephen Wachira Kariuki", "CHU/0148", "stephen.wachira@chuka.ac.ke"),
    ("CHEM 304", "Physical Sciences Department", "", ""),
    ("FOST 351", "Deborah Kangai", "CHU/0229", "deborah.kangai@chuka.ac.ke"),
    ("ANSC 333", "Animal Science Department", "", ""),
    ("FOST 323", "Stephen Wachira Kariuki", "CHU/0148", "stephen.wachira@chuka.ac.ke"),
    ("FOST 341", "Johnson Kyalo Mwove", "CHU/0172", "johnson.mwove@chuka.ac.ke"),
    ("FOST 342", "Joyce Wangui Njoki", "CHU/0180", "joyce.njoki@chuka.ac.ke"),
    ("FOST 343", "Francis Gichuho Irungu", "CHU/0170", "francis.irungu@chuka.ac.ke"),
    ("ANHE 427", "Animal Science Department", "", ""),
    ("DATM 456", "Joy Debora Orwa", "CHU/0171", "joy.orwa@chuka.ac.ke"),
    ("FOST 413", "Johnson Kyalo Mwove", "CHU/0172", "johnson.mwove@chuka.ac.ke"),
    ("FOST 425", "Joyce Wangui Njoki", "CHU/0180", "joyce.njoki@chuka.ac.ke"),
    ("FOST 461", "Stephen Wachira Kariuki", "CHU/0148", "stephen.wachira@chuka.ac.ke"),
    ("FOST 462", "Francis Gichuho Irungu", "CHU/0170", "francis.irungu@chuka.ac.ke"),
    ("FOST 463", "Joyce Wangui Njoki", "CHU/0180", "joyce.njoki@chuka.ac.ke"),
    ("FOST 482", "Njagi Jackin Nanua", "CHU/0253", "njagi.nanua@chuka.ac.ke"),
]

# Known aliases where the mapping CSV's department name doesn't literally
# match a Department.name in the DB. Add to this dict as you hit more.
DEPARTMENT_ALIASES = {
    "ENVIRONMENTAL SCIENCE": "Environmental Science & Resources Development",
}

ELECTIVE_UNIT_TYPES = {"ELECTIVE", "REQUIRED_ELECTIVE"}


def normalize_dept_label(raw):
    """'Physical Sciences Department' -> 'PHYSICAL SCIENCES'"""
    label = raw.strip().upper()
    label = re.sub(r"\bDEPARTMENT\b", "", label).strip()
    label = re.sub(r"\s+", " ", label)
    return label


def resolve_department(raw_label, dept_cache, unresolved_set):
    """
    Resolve a free-text department label (from the CSV) to a real
    Department row. Tries: exact normalized match -> alias table ->
    fuzzy/contains match. Returns None (and records the miss) if nothing
    reasonable is found -- it is NEVER invented.
    """
    key = normalize_dept_label(raw_label)
    if key in dept_cache:
        return dept_cache[key]

    # exact case-insensitive match against Department.name
    dept = Department.objects.filter(name__iexact=key).first()

    # alias table
    if not dept and key in DEPARTMENT_ALIASES:
        dept = Department.objects.filter(name__iexact=DEPARTMENT_ALIASES[key]).first()

    # contains-match fallback (e.g. key is a substring of the real name)
    if not dept:
        dept = Department.objects.filter(name__icontains=key).first()

    # fuzzy match fallback
    if not dept:
        all_names = list(Department.objects.values_list("name", flat=True))
        close = difflib.get_close_matches(key.title(), all_names, n=1, cutoff=0.75)
        if close:
            dept = Department.objects.filter(name=close[0]).first()

    dept_cache[key] = dept
    if not dept:
        unresolved_set.add(raw_label)
    return dept


def resolve_lecturer(payroll_number, email, name, unresolved_set):
    lec = None
    if payroll_number:
        lec = Lecturer.objects.filter(payroll_number__iexact=payroll_number.strip()).first()
    if not lec and email:
        lec = Lecturer.objects.filter(email__iexact=email.strip()).first()
    if not lec and name:
        lec = Lecturer.objects.filter(name__iexact=name.strip()).first()
    if not lec:
        unresolved_set.add(f"{name} ({payroll_number or 'no payroll'})")
    return lec


@transaction.atomic
def run():
    stats = {"created": 0, "updated": 0}
    missing_program_courses = []
    unresolved_lecturers = set()
    unresolved_departments = set()
    dept_cache = {}

    try:
        program = Program.objects.get(name=PROGRAM_NAME)
    except Program.DoesNotExist:
        print(f"[ABORT] Program not found: {PROGRAM_NAME!r}")
        return

    try:
        allocating_department = Department.objects.get(name=ALLOCATING_DEPARTMENT_NAME)
    except Department.DoesNotExist:
        print(f"[ABORT] Department not found: {ALLOCATING_DEPARTMENT_NAME!r}")
        return

    lecturer_map_by_code = {
        canonical_course_key(code): (lname, payroll, email)
        for code, lname, payroll, email in SEED_LECTURER_MAP
    }

    for code, cname, year, sem, unit_type, cohort in SEED_PROGRAM_COURSES:
        cohort = cohort or STUDENT_COHORT
        ckey = canonical_course_key(code)

        program_course = (
            ProgramCourse.objects
            .filter(program=program, student_cohort=cohort)
            .filter(course_code__iexact=code)
            .first()
        )
        if not program_course:
            # fall back to canonical-key match in case of formatting drift
            for pc in ProgramCourse.objects.filter(program=program, student_cohort=cohort):
                if canonical_course_key(pc.course_code) == ckey:
                    program_course = pc
                    break

        if not program_course:
            missing_program_courses.append(code)
            continue

        lecturer_obj = None
        origin_department_obj = None

        mapping = lecturer_map_by_code.get(ckey)
        if mapping:
            lname, payroll, email = mapping
            if payroll.strip():
                # Individual lecturer.
                lecturer_obj = resolve_lecturer(payroll, email, lname, unresolved_lecturers)
                if (
                    TAG_ORIGIN_FOR_BORROWED_LECTURERS
                    and lecturer_obj
                    and lecturer_obj.department_id
                    and lecturer_obj.department_id != allocating_department.id
                ):
                    origin_department_obj = lecturer_obj.department
            else:
                # The "lecturer_name" column actually names a DEPARTMENT
                # -> no individual lecturer yet; tag origin_department.
                origin_department_obj = resolve_department(
                    lname, dept_cache, unresolved_departments
                )
        else:
            print(f"[WARN] No lecturer-mapping row found for {code}")

        is_elective = ProgramCourse.normalize_unit_type(unit_type) in ELECTIVE_UNIT_TYPES

        if DRY_RUN:
            lec_label = lecturer_obj.display_name if lecturer_obj else "UNASSIGNED"
            origin_label = origin_department_obj.name if origin_department_obj else "-"
            print(f"[DRY RUN] {code:10s} lecturer={lec_label:30s} origin_dept={origin_label}")
            continue

        allocation, created = CourseAllocation.get_or_create_shared(
            program_course=program_course,
            department=allocating_department,
            defaults={
                "lecturer": lecturer_obj,
                "origin_department": origin_department_obj,
                "is_elective": is_elective,
            },
        )

        # get_or_create_shared only sets these on CREATE; make sure an
        # existing row also gets refreshed (update-or-create semantics).
        allocation.course_code = program_course.course_code
        allocation.course_name = program_course.course_name
        allocation.program = program
        allocation.lecturer = lecturer_obj
        allocation.origin_department = origin_department_obj
        allocation.is_elective = is_elective
        allocation.full_clean()
        allocation.save()

        if created:
            stats["created"] += 1
        else:
            stats["updated"] += 1

    print("\n================ SUMMARY ================")
    print(f"Program              : {program.name}")
    print(f"Allocating department: {allocating_department.name}")
    print(f"Cohort               : {STUDENT_COHORT}")
    print(f"Created              : {stats['created']}")
    print(f"Updated              : {stats['updated']}")

    if missing_program_courses:
        print(f"\n[!] ProgramCourse not found for {len(missing_program_courses)} code(s) "
              f"(check STUDENT_COHORT or that they were really imported):")
        for c in missing_program_courses:
            print(f"    - {c}")

    if unresolved_lecturers:
        print(f"\n[!] Could not resolve {len(unresolved_lecturers)} lecturer(s) "
              f"(left unassigned on those allocations):")
        for l in sorted(unresolved_lecturers):
            print(f"    - {l}")

    if unresolved_departments:
        print(f"\n[!] Could not resolve {len(unresolved_departments)} department name(s) "
              f"as origin_department (left blank on those allocations) - "
              f"either add an alias in DEPARTMENT_ALIASES or create the Department:")
        for d in sorted(unresolved_departments):
            print(f"    - {d!r}")

    print("===========================================\n")


run()
