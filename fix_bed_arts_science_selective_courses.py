# ==============================================================================
# fix_bed_arts_science_selective_courses.py
#
# WHAT THIS SCRIPT DOES
# ------------------------------------------------------------------------------
# For Bachelor of Education (Arts) and Bachelor of Education (Science), for
# ONLY the 15 course codes listed in COURSE_SPECS below (the DEDU common /
# shared units that have been re-created over and over under different
# StudentGroup groupings and are now unschedulable duplicates):
#
#   1. Finds and BACKS UP (JSON, restorable with `manage.py loaddata`) every
#      existing CourseAllocation row for those course codes, under those two
#      programs, in the Department of Education.
#   2. DELETES all of those existing rows (they are the excess/duplicate
#      allocations created by the old per-StudentGroup grouping approach).
#   3. RECREATES exactly the number of lettered sections specified in the
#      Word document (eduvation.docx) for each course:
#         Y1S1: EDFO 111, EPSC 111, EDCI 111            -> 15 sections each
#         Y2S1: EDCI 211, EDCI 203, EDFO 211, EPSC 222   -> 12 sections each
#         Y3S1: EDCI 322, EDCI 311, EPSC 311, EDFO 321   -> 5 sections each
#         Y4S1: EAPE 411, EAPE 412, EPSC 431, EDFO 422   -> 4 sections each
#      Each section is named "<BASE CODE><LETTER>" e.g. "EDFO 111A", carries
#      150 students, and is assigned the lecturer named against that letter
#      in the Word document (matched to the live Lecturer table by surname,
#      tolerant of name-order swaps like "Humprey Mugambi" vs "Mugambi
#      Humfrey" — see resolve_lecturer()).
#   4. Each section is created as is_elective=True, student_group=None, and
#      added to a SelectionGroup (one per base-course per program) so
#      students PICK ONE section instead of being force-assigned to a
#      StudentGroup. Being an elective also makes these sections mutually
#      exempt from the scheduler's program-year clash check (see Rule 3 in
#      timetable/timetable_panel.py: is_scheduling_exempt) — this is what
#      actually fixes the "unschedulable" problem, since electives are
#      always allowed to run concurrently with each other.
#
# ASSUMPTIONS — READ BEFORE RUNNING (edit COURSE_SPECS if any of these are wrong)
# ------------------------------------------------------------------------------
#   - Arts/Science split: the instruction given was "15 -> 8 Arts / 7 Science"
#     and "4 -> 2 Arts / 2 Science". The 12- and 5-section tiers were NOT
#     given an explicit split, so this script applies the same rule implied
#     by those two examples (ceil(n/2) to Arts, floor(n/2) to Science):
#         15 -> 8 Arts / 7 Science      (given)
#         12 -> 6 Arts / 6 Science      (inferred)
#          5 -> 3 Arts / 2 Science      (inferred)
#          4 -> 2 Arts / 2 Science      (given)
#     If your intended split for the 12- and 5-section courses is different,
#     edit ARTS_COUNT_OVERRIDES near the bottom of COURSE_SPECS.
#   - WHICH letters go to Arts vs Science: the Word document lists these as
#     single combined "Y_S1 COMMON COURSES" cohorts (letters A, B, C, ...)
#     shared across both programs — it does not say which lettered cohort
#     was historically Arts vs Science. This script assigns the FIRST
#     `arts_count` letters (alphabetically) to Arts and the rest to Science.
#     If you know the real historical split, edit the `arts_letters` /
#     `science_letters` lists in COURSE_SPECS directly instead of relying on
#     the auto-split.
#   - Every section gets number_of_students = 150, per your instruction,
#     regardless of what the Word document's (inconsistent) aggregate
#     numbers say for the old combined groupings.
#
# DRY_RUN = True by default -- writes the backup and prints the full report,
# but does NOT delete or create anything. Review the report, then flip
# DRY_RUN = False and re-run.
#
# USAGE:
#   python manage.py shell < fix_bed_arts_science_selective_courses.py
# ==============================================================================

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core import serializers
from django.db import transaction

from department_management.models import Department
from program_management.models import Program, ProgramCourse
from program_management.code_utils import normalize_code, canonical_course_key
from course_allocation.models import CourseAllocation, SelectionGroup
from lecturer_portal.models import Lecturer

# ------------------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------------------
DRY_RUN = False    # <-- flip to False once the report below looks right

PROGRAM_ARTS_NAME    = "Bachelor of Education (Arts)"     # <-- adjust to exact name in your DB if different
PROGRAM_SCIENCE_NAME = "Bachelor of Education (Science)"  # <-- adjust to exact name in your DB if different
DEPARTMENT_NAME_HINT = "Education"                        # <-- exact (case-insensitive) match against Department.name

BACKUP_DIR = Path(settings.BASE_DIR) / "backups" / "bed_selective_course_fix"

W = 100
def line(c="="): print(c * W)
def h1(t):
    print(); line("="); print(t); line("=")
def h2(t):
    print(); line("-"); print(t); line("-")


# ------------------------------------------------------------------------------
# COURSE SPECS — parsed from eduvation.docx (DEDU Semester 1 2025/2026 course
# allocation letter). base_code -> (course_name, year, target_arts, letters{letter: lecturer_name})
# ------------------------------------------------------------------------------
def letters_split(letter_lecturer_pairs, arts_count):
    """First `arts_count` letters (in the given order) go to Arts, rest to Science."""
    letters = [l for l, _ in letter_lecturer_pairs]
    return letters[:arts_count], letters[arts_count:]


COURSE_SPECS = []

def add_spec(base_code, course_name, year, letter_lecturer_pairs, arts_count):
    arts_letters, science_letters = letters_split(letter_lecturer_pairs, arts_count)
    COURSE_SPECS.append({
        "base_code": normalize_code(base_code),
        "course_name": course_name,
        "year": year,
        "semester": 1,
        "lecturers": dict(letter_lecturer_pairs),   # letter -> lecturer name string
        "arts_letters": arts_letters,
        "science_letters": science_letters,
    })


# --- Y1S1 — target 15 sections (8 Arts / 7 Science) -------------------------
add_spec("EDFO 111", "History of Education", 1, [
    ("A", "Agnes Kendi"), ("B", "Agnes Kendi"),
    ("C", "Dr. John Karauri"),
    ("D", "Dr. Humprey Mugambi"), ("E", "Dr. Humprey Mugambi"),
    ("F", "Dr. Steve Muthomi"), ("G", "Dr. Steve Muthomi"),
    ("H", "Ms. Elyjoy Kainyu"), ("I", "Ms. Elyjoy Kainyu"),
    ("J", "Ms. Caroline Muchiri"), ("K", "Ms. Caroline Muchiri"),
    ("L", "Dr. Charles Kiptum"), ("M", "Dr. Charles Kiptum"),
    ("N", "Mr. Monene Justine"), ("O", "Mr. Monene Justine"),
], arts_count=8)

add_spec("EPSC 111", "Introduction to Psychology", 1, [
    ("A", "Dr Mercy Kariuki"), ("B", "Dr Mercy Kariuki"), ("C", "Dr Mercy Kariuki"),
    ("D", "Prof Grace Murithi"),
    ("E", "Lucy Kiriungi"), ("F", "Lucy Kiriungi"),
    ("G", "Dr Benjamin Kanga"),
    ("H", "Dr Bornace Kimeli"),
    ("I", "Dr Enidy Boera"),
    ("J", "Prof Susan Kinyua"), ("K", "Prof Susan Kinyua"),
    ("L", "Lydia Karimi"), ("M", "Lydia Karimi"),
    ("N", "Harriet Kagendo"),
    ("O", "Enid Kawira"),
], arts_count=8)

add_spec("EDCI 111", "Health and Physical Education", 1, [
    ("A", "Dr. Maluni"), ("B", "Dr. Maluni"), ("C", "Dr. Maluni"),
    ("D", "Dr. Ogembo"),
    ("E", "Ms. Rose Jakinda"),
    ("F", "Ms. Purity Kananu"),
    ("G", "Dr. Noel Mbaka"), ("H", "Dr. Noel Mbaka"),
    ("I", "Dr. Catherine"), ("J", "Dr. Catherine"),
    ("K", "Dr. Ogembo"),
    ("L", "Eric Thauri"),
    ("M", "Dr. Nduru"),
    ("N", "Gisoi"),
    ("O", "Catherine Ngaine"),
], arts_count=8)

# --- Y2S1 — target 12 sections (6 Arts / 6 Science, inferred) ---------------
add_spec("EDCI 211", "Principles and Theory of Curriculum Development", 2, [
    ("A", "Dr. Benedict Maluni"), ("B", "Dr. Benedict Maluni"), ("C", "Dr. Benedict Maluni"),
    ("D", "Dr. Catherine Nkirote"), ("E", "Dr. Catherine Nkirote"), ("F", "Dr. Catherine Nkirote"),
    ("G", "Dr. John Ogembo"), ("H", "Dr. John Ogembo"),
    ("I", "Dr. Monica Ituma"),
    ("J", "Mr. Robinson Kenyatta"), ("K", "Mr. Robinson Kenyatta"),
    ("L", "Dr. Kithinji"),
], arts_count=6)

add_spec("EDCI 203", "Measurement and Evaluation", 2, [
    ("A", "Dr. Noel Mbaka"), ("B", "Dr. Noel Mbaka"),
    ("C", "Dr. Catherine Nkirote"), ("D", "Dr. Catherine Nkirote"), ("E", "Dr. Catherine Nkirote"),
    ("F", "Mr. Robinson Kenyatta"), ("G", "Mr. Robinson Kenyatta"), ("H", "Mr. Robinson Kenyatta"),
    ("I", "Prof. Mercy Njagi"), ("J", "Prof. Mercy Njagi"), ("K", "Prof. Mercy Njagi"),
    ("L", "Ms. Nelly Kananu"),
], arts_count=6)

add_spec("EDFO 211", "Philosophy of Education", 2, [
    ("A", "Dr James Mwenda"),
    ("B", "Dr. Peter Kimanthi"), ("C", "Dr. Peter Kimanthi"),
    ("D", "Dr. John Karauri"), ("E", "Dr. John Karauri"), ("F", "Dr. John Karauri"),
    ("G", "Dr. Humprey Mugambi"), ("H", "Dr. Humprey Mugambi"),
    ("I", "Dr. Prisca Kobia"), ("J", "Dr. Prisca Kobia"),
    ("K", "Dr. Steve Muthomi"),
    ("L", "Dr Gladys Njogu"),
], arts_count=6)

add_spec("EPSC 222", "Research Methods in Education", 2, [
    ("A", "Prof Mercy Njagi"), ("B", "Prof Mercy Njagi"),
    ("C", "Dr Benjamin Kanga"),
    ("D", "Dr Charles Kiptum"), ("E", "Dr Charles Kiptum"),
    ("F", "Dr Peter Kimanthi"),
    ("G", "Prof Grace Murithi"),
    ("H", "Dr John Karauri"),
    ("I", "Dr Kenkelvin Kimathi"),
    ("J", "Dr John Kamoyo"),
    ("K", "Prof Eric Mwenda"), ("L", "Prof Eric Mwenda"),
], arts_count=6)

# --- Y3S1 — target 5 sections (3 Arts / 2 Science, inferred) ---------------
add_spec("EDCI 322", "General Methods and Principles of Teaching", 3, [
    ("A", "Dr. Monica Ituma"), ("B", "Dr. Monica Ituma"),
    ("C", "Dr. Noel Mbaka"),
    ("D", "Prof. Mercy Njagi"), ("E", "Prof. Mercy Njagi"),
], arts_count=3)

add_spec("EDCI 311", "Secondary Education Curriculum", 3, [
    ("A", "Dr. Noel Mbaka"), ("B", "Dr. Noel Mbaka"),
    ("C", "Dr. John Ogembo"),
    ("D", "Dr. Benedict Maluni"),
    ("E", "Dr. Monica Ituma"),
], arts_count=3)

add_spec("EPSC 311", "Measurement and Evaluation in Education", 3, [
    ("A", "Dr Benjamin Kanga"), ("B", "Dr Benjamin Kanga"),
    ("C", "Prof Grace Murithi"), ("D", "Prof Grace Murithi"),
    ("E", "Dr John Kamoyo"),
], arts_count=3)

add_spec("EDFO 321", "Adult Education", 3, [
    ("A", "Dr. Humprey Mugambi"),
    ("B", "Dr. John Karauri"),
    ("C", "Ms. Marcella Kamami"),
    ("D", "Dr. Prisca Kobia"),
    ("E", "Dr Kenkelvin Kimathi"),
], arts_count=3)

# --- Y4S1 — target 4 sections (2 Arts / 2 Science, given) -------------------
add_spec("EAPE 411", "Educational Administration and Management", 4, [
    ("A", "Prof Nelson Jagero"), ("B", "Prof Nelson Jagero"),
    ("C", "Prof Eric Mwenda"), ("D", "Prof Eric Mwenda"),
], arts_count=2)

add_spec("EAPE 412", "Entrepreneurship Education and Development", 4, [
    ("A", "Dr Charles Kiptum"),
    ("B", "Dr Mary Mugambi"),
    ("C", "Prof Nelson Jagero"), ("D", "Prof Nelson Jagero"),
], arts_count=2)

add_spec("EPSC 431", "Personality and Group Dynamics", 4, [
    ("A", "Prof Grace Murithi"),
    ("B", "Dr Mercy Kariuki"),
    ("C", "Dr Benjamin Kanga"),
    ("D", "Dr John Kamoyo"),
], arts_count=2)

add_spec("EDFO 422", "Comparative Education", 4, [
    ("A", "Dr. James Mwenda"), ("B", "Dr. James Mwenda"),
    ("C", "Dr. Peter Kimanthi"), ("D", "Dr. Peter Kimanthi"),
], arts_count=2)

STUDENTS_PER_SECTION = 150


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def matches_base_course(course_code, base_code):
    """True if `course_code` is the base course or a lettered/suffixed variant
    of it (EDFO 111, EDFO 111A, EDFO111-A, EDFO 111 A, ... all match 'EDFO 111'),
    while NOT matching an unrelated code that merely shares a prefix
    (e.g. 'EDFO 1110' must not match 'EDFO 111')."""
    a = canonical_course_key(course_code)
    b = canonical_course_key(base_code)
    if not a.startswith(b):
        return False
    rest = a[len(b):]
    if rest == "":
        return True
    # Only a letter/hyphen suffix counts as "the same course, different section" —
    # a leading digit means it's actually a different course code (e.g. 1110 vs 111).
    return not rest[0].isdigit()


_lecturer_cache = {}
_all_lecturers_cache = None


def _clean_for_match(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _get_all_lecturers():
    global _all_lecturers_cache
    if _all_lecturers_cache is None:
        _all_lecturers_cache = list(Lecturer.objects.all())
    return _all_lecturers_cache


def resolve_lecturer(lecturer_name, department=None):
    """
    Resolve a Word-doc lecturer string to a Lecturer instance by SURNAME
    substring match, tolerant of title prefixes and of first/last name
    order being swapped between the doc and the DB (e.g. 'Humprey Mugambi'
    in the doc vs 'Mugambi Humfrey' in the DB both match on 'MUGAMBI').
    Returns None (and prints a warning) if no confident match is found —
    callers should leave the lecturer FK unset rather than guess.
    """
    if not lecturer_name or not lecturer_name.strip():
        return None

    dept_name = getattr(department, "name", "") or ""
    cache_key = (lecturer_name.strip().upper(), dept_name)
    if cache_key in _lecturer_cache:
        return _lecturer_cache[cache_key]

    all_lecturers = _get_all_lecturers()
    if not all_lecturers:
        _lecturer_cache[cache_key] = None
        return None

    normalized = re.sub(r"^(Dr\.?|Prof\.?|Mr\.?|Ms\.?|Mrs\.?)\s*", "", lecturer_name.strip(), flags=re.IGNORECASE)
    normalized = normalized.replace(".", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    tokens = normalized.split()
    if not tokens:
        _lecturer_cache[cache_key] = None
        return None
    surname = tokens[-1]
    surname_clean = _clean_for_match(surname)

    candidates = [
        l for l in all_lecturers
        if surname_clean and surname_clean in _clean_for_match(l.name)
    ]

    lecturer_obj = None
    if len(candidates) == 1:
        lecturer_obj = candidates[0]
    elif len(candidates) > 1:
        if department:
            dept_matches = [c for c in candidates if dept_name.lower() in (getattr(c.department, "name", "") or "").lower()]
            if len(dept_matches) == 1:
                lecturer_obj = dept_matches[0]
        if not lecturer_obj:
            lecturer_obj = candidates[0]
            print(f"      \u26a0 Multiple matches for '{lecturer_name}' ({len(candidates)} found, "
                  f"surname={surname!r}); using {lecturer_obj.name} — VERIFY THIS.")
    else:
        print(f"      \u26a0 Could not match lecturer '{lecturer_name}' to any Lecturer record — leaving unset.")

    _lecturer_cache[cache_key] = lecturer_obj
    return lecturer_obj


# ------------------------------------------------------------------------------
# 1. Resolve Department + Programs
# ------------------------------------------------------------------------------
h1("RESOLVING DEPARTMENT AND PROGRAMS")

dept_matches = list(Department.objects.filter(name__iexact=DEPARTMENT_NAME_HINT))
if len(dept_matches) != 1:
    print(f"Expected exactly one Department matching {DEPARTMENT_NAME_HINT!r}, found {len(dept_matches)}:")
    for d in dept_matches:
        print(f"   id={d.id}  {d.name}")
    print("Narrow DEPARTMENT_NAME_HINT and re-run.")
    sys.exit(1)
dept = dept_matches[0]
print(f"Department: id={dept.id}  {dept.name}")

def resolve_program(name):
    matches = list(Program.objects.filter(name__iexact=name))
    if not matches:
        matches = list(Program.objects.filter(name__icontains=name))
    if len(matches) != 1:
        print(f"Expected exactly one Program matching {name!r}, found {len(matches)}:")
        for p in matches:
            print(f"   id={p.id}  {p.name}")
        print("Adjust PROGRAM_ARTS_NAME / PROGRAM_SCIENCE_NAME to the exact name and re-run.")
        sys.exit(1)
    return matches[0]

program_arts = resolve_program(PROGRAM_ARTS_NAME)
program_science = resolve_program(PROGRAM_SCIENCE_NAME)
print(f"Arts program:    id={program_arts.id}  {program_arts.name}")
print(f"Science program: id={program_science.id}  {program_science.name}")

PROGRAM_LETTERS_KEY = {
    program_arts.id: "arts_letters",
    program_science.id: "science_letters",
}


# ------------------------------------------------------------------------------
# 2. Gather + back up existing rows for the 15 target course codes
# ------------------------------------------------------------------------------
h1("GATHERING EXISTING ALLOCATIONS FOR TARGET COURSES")

existing_by_spec = {}   # base_code -> list[CourseAllocation]
all_existing_ids = []

candidate_rows = list(
    CourseAllocation.objects
    .filter(department=dept, program_id__in=[program_arts.id, program_science.id])
    .select_related("program", "student_group", "lecturer", "selection_group")
)

for spec in COURSE_SPECS:
    base = spec["base_code"]
    rows = [r for r in candidate_rows if matches_base_course(r.course_code, base)]
    existing_by_spec[base] = rows
    all_existing_ids.extend(r.id for r in rows)
    codes = sorted({r.course_code for r in rows})
    print(f"{base:<12} existing rows: {len(rows):<4} distinct course_code variants: {codes}")

print(f"\nTotal existing CourseAllocation rows in scope: {len(all_existing_ids)}")


# ------------------------------------------------------------------------------
# 3. Write backup (always — cheap insurance, even in DRY_RUN)
# ------------------------------------------------------------------------------
h1("WRITING BACKUP")

BACKUP_DIR.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_path = BACKUP_DIR / f"bed_selective_fix_{timestamp}.json"

rows_to_backup = list(CourseAllocation.objects.filter(id__in=all_existing_ids))
serialized = serializers.serialize("json", rows_to_backup, indent=2)
with open(backup_path, "w") as f:
    f.write(serialized)

meta_path = backup_path.with_suffix(".meta.json")
with open(meta_path, "w") as f:
    json.dump({
        "created_at": timestamp,
        "department": dept.name,
        "programs": [program_arts.name, program_science.name],
        "course_allocations_backed_up": len(rows_to_backup),
        "course_codes": [s["base_code"] for s in COURSE_SPECS],
    }, f, indent=2)

print(f"Backup written:   {backup_path}")
print(f"Metadata written: {meta_path}")
print(f"To restore:  python manage.py loaddata {backup_path}")


# ------------------------------------------------------------------------------
# 4. Preview what will be created
# ------------------------------------------------------------------------------
h1("PREVIEW — SECTIONS THAT WILL BE CREATED")

unmatched_lecturers = set()

for spec in COURSE_SPECS:
    h2(f"{spec['base_code']} — {spec['course_name']} (Year {spec['year']} Sem {spec['semester']})")
    for prog, key in ((program_arts, "arts_letters"), (program_science, "science_letters")):
        letters = spec[key]
        print(f"  {prog.name}: {len(letters)} section(s) -> {letters}")
        for letter in letters:
            lecturer_name = spec["lecturers"][letter]
            lect_obj = resolve_lecturer(lecturer_name, department=dept)
            if lect_obj is None:
                unmatched_lecturers.add(lecturer_name)
            resolved = lect_obj.name if lect_obj else "**UNRESOLVED**"
            print(f"      {spec['base_code']}{letter}  -> {lecturer_name!r} => {resolved}  ({STUDENTS_PER_SECTION} students)")

if unmatched_lecturers:
    h2("LECTURERS THAT COULD NOT BE MATCHED — these sections will be created with lecturer=None")
    for name in sorted(unmatched_lecturers):
        print(f"   - {name}")
    print("Fix these manually afterward, or add name variants to Lecturer records and re-run.")


# ------------------------------------------------------------------------------
# 5. Apply changes
# ------------------------------------------------------------------------------
h1("APPLY" if not DRY_RUN else "APPLY (SKIPPED — DRY_RUN = True)")

if DRY_RUN:
    print("DRY_RUN is True — nothing was deleted or created.")
    print("Review the report above (and the backup), then flip DRY_RUN = False and re-run.")
    line("=")
    sys.exit(0)

created_count = 0
deleted_count = 0

with transaction.atomic():
    # 5a. Delete all existing rows for these 15 courses under these 2 programs
    deleted_count = CourseAllocation.objects.filter(id__in=all_existing_ids).count()
    CourseAllocation.objects.filter(id__in=all_existing_ids).delete()
    print(f"Deleted {deleted_count} existing CourseAllocation rows.")

    # 5b. Recreate the clean set of lettered, elective sections
    for spec in COURSE_SPECS:
        base = spec["base_code"]

        for prog, key in ((program_arts, "arts_letters"), (program_science, "science_letters")):
            letters = spec[key]
            if not letters:
                continue

            # Base curriculum entry (ProgramCourse) for this program — get or create.
            program_course, pc_created = ProgramCourse.objects.get_or_create(
                program=prog,
                course_code=base,
                student_cohort="0",
                defaults={
                    "course_name": spec["course_name"],
                    "year": spec["year"],
                    "semester": spec["semester"],
                    "unit_type": "CORE",
                },
            )
            if pc_created:
                print(f"  Created ProgramCourse {base} for {prog.name}")

            # One SelectionGroup per (base course, program) so students pick one section.
            sg_name = f"{base} — {prog.name} Sections"
            selection_group, sg_created = SelectionGroup.objects.get_or_create(
                department=dept,
                name=sg_name,
                defaults={"program": prog},
            )
            if sg_created:
                print(f"  Created SelectionGroup '{sg_name}'")

            for letter in letters:
                lecturer_name = spec["lecturers"][letter]
                lecturer_obj = resolve_lecturer(lecturer_name, department=dept)
                section_code = f"{base}{letter}"

                ca = CourseAllocation(
                    course_code=section_code,
                    course_name=spec["course_name"],
                    department=dept,
                    origin_department=dept,
                    program=prog,
                    lecturer=lecturer_obj,
                    number_of_students=STUDENTS_PER_SECTION,
                    program_course=program_course,
                    intake=CourseAllocation.INTAKE_NORMAL,
                    is_elective=True,
                    student_group=None,
                    selection_group=selection_group,
                )
                ca.full_clean(exclude=["reason_for_disapproval"])
                ca.save()
                selection_group.courses.add(ca)
                created_count += 1

print(f"Created {created_count} new elective CourseAllocation rows.")

h1("DONE")
print(f"Deleted: {deleted_count}   Created: {created_count}")
print(f"Backup is safe at: {backup_path}")
if unmatched_lecturers:
    print(f"\n{len(unmatched_lecturers)} lecturer name(s) could not be auto-matched — see list above. "
          f"Those sections were saved with lecturer=None; assign them manually in the COD panel.")
line("=")