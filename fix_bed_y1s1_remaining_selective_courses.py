# ==============================================================================
# fix_bed_y1s1_remaining_selective_courses.py
#
# WHAT THIS SCRIPT DOES
# ------------------------------------------------------------------------------
# Follow-on to fix_bed_arts_science_selective_courses.py (which already fixed
# EDFO 111, EPSC 111, EDCI 111 — the pure-DEDU common units — via lettered
# SelectionGroup sections). This script finishes the rest of the BEd
# Arts/Science Y1S1 course-allocation letter (eduvation.docx):
#
#   1. COSC 103 (Introduction to Computer Applications) — a course COMMON to
#      BOTH Bachelor of Education (Arts) and (Science). Because CourseAllocation
#      requires a program-specific ProgramCourse, this needs one allocation row
#      per program — but it is ONE physical class. The two rows are linked
#      together with a CombinedCourseGroup ("shared course"), exactly like a
#      manual "combine" action in the COD panel.
#
#   2. ENGL 101 (Introduction to Language and Linguistics) — the Word document
#      lists the SAME lecturer (Dr. C. Patrick Kihara) twice against this
#      course, i.e. TWO sections, both under BEd (Arts), taught by the same
#      person. Created as ENGL 101A / ENGL 101B and merged into ONE
#      CombinedCourseGroup ("shared course"), same treatment as COSC 103.
#
#      HIST 121 and HIST 151 have the identical pattern in the document
#      ("GRP A" / "GRP B", same lecturer both times) and are given the same
#      combined-group treatment for consistency.
#
#   3. COMS 101 (Communication Skills) — a COMMON course (BEd Arts + Science),
#      cut down to 3 sections per your instruction ("be 3"), split across the
#      two programs the same way the previous script split EDFO/EPSC/EDCI
#      (more sections to Arts than Science), and added to a SelectionGroup
#      (students pick one section) — the SAME mechanism used previously.
#
#   4. ALL OTHER Y1S1 selective courses in the BEd Arts and BEd Science lists
#      (ENGL 102, LITT 101/102, RELI 100/120, KISW 101/102, FREN 110/130,
#      MUSC 101/111, GEOG 100/110, MATH 122/124, BCOM 101, BHRM 113,
#      COSC 106/111, AGRI 101, SOIL 100, HOSC 111/131, PESP 111/121/141,
#      CHEM 101/110, PHYS 121/131/161, BOTA 101/111, ZOOL 101):
#         - Ensure a CourseAllocation row EXISTS for each (create if missing)
#           and mark submitted_to_tt=True ("entry passed to timetable").
#         - Course codes that appear in BOTH the Arts list and the Science
#           list (MATH 122, MATH 124, COSC 106, COSC 111, AGRI 101, SOIL 100,
#           HOSC 111, HOSC 131) are literally the same class taught to both
#           cohorts, so their Arts-row and Science-row are merged into ONE
#           CombinedCourseGroup ("the math 122 ... passed in one course
#           group").
#         - PESP 111 has 3 differently-lecturered sections in the document,
#           so it is handled as a 3-way SelectionGroup, like COMS 101.
#         - A final SAFETY-NET SWEEP scans the live database for any of
#           these course codes that already have MORE THAN ONE
#           CourseAllocation row (leftover duplicates from the old broken
#           per-StudentGroup creation) that are not yet linked by a
#           CombinedCourseGroup, and merges them together. This is what
#           covers "for GEOG 100 expect them to be combined in one merged
#           course group" even though the Word document itself only shows
#           a single GEOG 100 row — if the database has stray duplicates,
#           this sweep finds and merges them; if it doesn't, the sweep is a
#           safe no-op for that course.
#
# GROUPING MECHANISM — WHICH ONE FOR WHICH COURSE
# ------------------------------------------------------------------------------
#   SelectionGroup     ("selection group" in your instructions) = students
#                       PICK ONE of several parallel sections taught by
#                       DIFFERENT lecturers. Used for: COMS 101, PESP 111
#                       (and previously EDFO 111 / EPSC 111 / EDCI 111).
#   CombinedCourseGroup ("merged course group" / "shared course" in your
#                       instructions) = several CourseAllocation rows that
#                       are actually the SAME physical class (same lecturer,
#                       or same course shared across two programs) and
#                       should be treated/timetabled as one. Used for:
#                       COSC 103, ENGL 101, HIST 121, HIST 151, and the
#                       Arts/Science cross-listed codes (MATH 122/124,
#                       COSC 106/111, AGRI 101, SOIL 100, HOSC 111/131).
#
# ASSUMPTIONS — READ BEFORE RUNNING (edit the specs below if any are wrong)
# ------------------------------------------------------------------------------
#   - COMS 101 "be 3": the Word document lists 5 lecturers (Dr. Jane Kathomi,
#     Dr. Elsie Kirimo, Dr. Rose Wambugu, Jacob Murigi, Gideon Munyao) but
#     only 3 sections are wanted. This script uses the FIRST THREE
#     (Kathomi, Kirimo, Wambugu) and DROPS Jacob Murigi / Gideon Munyao.
#     If a different 3 (or a different assignment of the 5 into 3 sections)
#     was intended, edit COMS101_SECTIONS below.
#   - COMS 101 Arts/Science split: 2 sections to Arts (A, B), 1 to Science
#     (C) — same "Arts gets the larger/equal share" convention as the
#     previous script.
#   - Department resolution: the Word document's short department codes
#     (DHUM, DSSC, DCOMP, PHSC, DBSC, PLSC, DBAD, DMSC, DEDU) are matched
#     against your live Department table by NAME HINT (icontains, case
#     insensitive) — see DEPARTMENT_NAME_HINTS below. If a hint matches zero
#     or more-than-one Department, that course's rows are SKIPPED (not
#     guessed) and listed in the "COULD NOT RESOLVE DEPARTMENT" report at
#     the end — fix the hint and re-run rather than trusting a guess.
#   - department vs origin_department on both CourseAllocation and
#     CombinedCourseGroup: set to the SAME value — the resolved TEACHING
#     department for that course (e.g. DCOMP for COSC 103/106/111, DHUM for
#     ENGL/COMS/LITT/HIST/RELI/KISW/FREN, DEDU for MUSC/HOSC/PESP, etc.) —
#     matching the "Dept" column printed against each course in the Word
#     document. If your workflow instead wants DEDU as the administrative
#     `department` for every row (since DEDU's COD is running this fix),
#     flip ORIGIN_DEPARTMENT_OVERRIDE_TO_DEDU = True below.
#   - number_of_students: uses the Word document's figure where given
#     (HIST 121/151 = 150 per GRP, PESP 111 = 150 per section), otherwise
#     defaults to 150 (DEFAULT_STUDENTS), same convention as the previous
#     script.
#   - All courses here are Year 1, Semester 1, Normal intake.
#   - Every row created is marked is_elective=True (even the "combined"
#     ones). This is what exempts sections of the same course from the
#     scheduler's program-year clash check (see Rule 3 in
#     timetable/timetable_panel.py: is_scheduling_exempt) — belt-and-braces
#     alongside the CombinedCourseGroup/SelectionGroup membership itself.
#
# DRY_RUN = True by default — writes the backup and prints the full report,
# but does NOT delete, create, or merge anything. Review the report, then
# flip DRY_RUN = False and re-run.
#
# USAGE:
#   python manage.py shell < fix_bed_y1s1_remaining_selective_courses.py
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
from course_allocation.models import CourseAllocation, SelectionGroup, CombinedCourseGroup
from lecturer_portal.models import Lecturer

# ------------------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------------------
DRY_RUN = False     # <-- flip to False once the report below looks right

PROGRAM_ARTS_NAME    = "Bachelor of Education (Arts)"     # <-- adjust to exact name in your DB if different
PROGRAM_SCIENCE_NAME = "Bachelor of Education (Science)"  # <-- adjust to exact name in your DB if different

ORIGIN_DEPARTMENT_OVERRIDE_TO_DEDU = False   # <-- see "department vs origin_department" note above
DEDU_NAME_HINT = "Education"                 # only used if the override above is True

DEFAULT_STUDENTS = 150

BACKUP_DIR = Path(settings.BASE_DIR) / "backups" / "bed_y1s1_remaining_selective_fix"

W = 100
def line(c="="): print(c * W)
def h1(t):
    print(); line("="); print(t); line("=")
def h2(t):
    print(); line("-"); print(t); line("-")


# ------------------------------------------------------------------------------
# Department name hints — short code (as printed in the Word doc) -> list of
# candidate substrings to look for in Department.name (tried in order, first
# unambiguous icontains match wins). EDIT THESE if your Department names are
# spelled differently.
# ------------------------------------------------------------------------------
DEPARTMENT_NAME_HINTS = {
    "DEDU":  ["Education"],
    "DHUM":  ["Humanities"],
    "DSSC":  ["Social Sciences", "Social Science"],
    "DCOMP": ["Computer Science", "Computing"],
    "PHSC":  ["Physical Sciences", "Physical Science"],
    "DBSC":  ["Biological Sciences", "Biological Science"],
    "PLSC":  ["Plant Sciences", "Plant Science"],
    "DBAD":  ["Business Administration"],
    "DMSC":  ["Management Science", "Management Studies", "Marketing"],
}

_department_cache = {}
_dept_resolution_failures = []   # list of (short_code, reason)


def resolve_department(short_code):
    if short_code in _department_cache:
        return _department_cache[short_code]

    hints = DEPARTMENT_NAME_HINTS.get(short_code, [])
    found = None
    for hint in hints:
        matches = list(Department.objects.filter(name__icontains=hint))
        if len(matches) == 1:
            found = matches[0]
            break
        elif len(matches) > 1:
            _dept_resolution_failures.append(
                (short_code, f"hint {hint!r} matched {len(matches)} departments: "
                             f"{[d.name for d in matches]} — narrow DEPARTMENT_NAME_HINTS['{short_code}']")
            )
            _department_cache[short_code] = None
            return None

    if found is None:
        _dept_resolution_failures.append(
            (short_code, f"no Department matched any hint in {hints!r} — "
                         f"add/fix DEPARTMENT_NAME_HINTS['{short_code}']")
        )
    _department_cache[short_code] = found
    return found


# ------------------------------------------------------------------------------
# Lecturer resolution (identical approach to the previous fix script)
# ------------------------------------------------------------------------------
_lecturer_cache = {}
_all_lecturers_cache = None
unmatched_lecturers = set()


def _clean_for_match(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _get_all_lecturers():
    global _all_lecturers_cache
    if _all_lecturers_cache is None:
        _all_lecturers_cache = list(Lecturer.objects.all())
    return _all_lecturers_cache


def resolve_lecturer(lecturer_name, department=None):
    """Resolve a Word-doc lecturer string to a Lecturer instance by SURNAME
    substring match, tolerant of title prefixes and first/last name order
    swaps. Returns None (and records a warning) if unresolved — callers
    leave the lecturer FK unset rather than guess."""
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

    candidates = [l for l in all_lecturers if surname_clean and surname_clean in _clean_for_match(l.name)]

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
        unmatched_lecturers.add(lecturer_name)
        print(f"      \u26a0 Could not match lecturer '{lecturer_name}' to any Lecturer record — leaving unset.")

    _lecturer_cache[cache_key] = lecturer_obj
    return lecturer_obj


def matches_base_course(course_code, base_code):
    """True if `course_code` is `base_code` itself or a lettered/suffixed
    variant of it (e.g. 'ENGL 101A' matches 'ENGL 101')."""
    a = canonical_course_key(course_code)
    b = canonical_course_key(base_code)
    if not a.startswith(b):
        return False
    rest = a[len(b):]
    if rest == "":
        return True
    return not rest[0].isdigit()


# ------------------------------------------------------------------------------
# 1. Resolve programs (+ optional DEDU override department)
# ------------------------------------------------------------------------------
h1("RESOLVING PROGRAMS")


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

dedu_dept = None
if ORIGIN_DEPARTMENT_OVERRIDE_TO_DEDU:
    dedu_matches = list(Department.objects.filter(name__icontains=DEDU_NAME_HINT))
    if len(dedu_matches) != 1:
        print(f"ORIGIN_DEPARTMENT_OVERRIDE_TO_DEDU=True but found {len(dedu_matches)} departments "
              f"matching {DEDU_NAME_HINT!r}. Fix DEDU_NAME_HINT and re-run.")
        sys.exit(1)
    dedu_dept = dedu_matches[0]
    print(f"DEDU override department: id={dedu_dept.id}  {dedu_dept.name}")


def effective_department(short_code):
    """Resolved teaching department for this short code, or the DEDU
    override if ORIGIN_DEPARTMENT_OVERRIDE_TO_DEDU is on."""
    if ORIGIN_DEPARTMENT_OVERRIDE_TO_DEDU:
        return dedu_dept
    return resolve_department(short_code)


# ------------------------------------------------------------------------------
# 2. Course specs — parsed from eduvation.docx, GROUP: BACHELOR OF EDUCATION
#    ARTS AND SCIENCE Y1S1 COMMON COURSES / ARTS Y1S1 / SCIENCE Y1S1
# ------------------------------------------------------------------------------

# --- 2a. Cross-program "shared course" (one program_course/allocation PER
#         program, then the two are merged into one CombinedCourseGroup) ----
CROSS_PROGRAM_COMBINED_SPECS = [
    {
        "base_code": "COSC 103",
        "course_name": "Introduction to Computer Applications",
        "dept_hint": "DCOMP",
        "lecturer_arts": "",       # blank in the Word document
        "lecturer_science": "",    # blank in the Word document
        "students": DEFAULT_STUDENTS,
    },
]

# --- 2b. Same-program "shared course" (N lettered sections, ALL taught by
#         the SAME lecturer, merged into one CombinedCourseGroup) -----------
SAME_PROGRAM_COMBINED_SPECS = [
    {
        "base_code": "ENGL 101",
        "course_name": "Introduction to Language and Linguistics",
        "dept_hint": "DHUM",
        "program": "arts",
        "sections": [("A", "Dr. C.Patrick Kihara", DEFAULT_STUDENTS),
                     ("B", "Dr. C.Patrick Kihara", DEFAULT_STUDENTS)],
    },
    {
        "base_code": "HIST 121",
        "course_name": "Selected Topics in Kenyan History",
        "dept_hint": "DHUM",
        "program": "arts",
        "sections": [("A", "Dr. Paul Muiru", 150),
                     ("B", "Dr. Paul Muiru", 150)],
    },
    {
        "base_code": "HIST 151",
        "course_name": "Selected Topics in Global History I",
        "dept_hint": "DHUM",
        "program": "arts",
        "sections": [("A", "Livan Njeru", 150),
                     ("B", "Livan Njeru", 150)],
    },
]

# --- 2c. Cross-program SelectionGroup (N lettered sections split across
#         Arts/Science, DIFFERENT lecturers, students pick one) -------------
COMS101_SECTIONS_ARTS    = [("A", "Dr. Jane Kathomi"), ("B", "Dr. Elsie Kirimo")]
COMS101_SECTIONS_SCIENCE = [("C", "Dr. Rose Wambugu")]
# NOTE: Jacob Murigi and Gideon Munyao are in the Word doc's lecturer list
# for COMS 101 but are NOT used, per the "be 3" instruction. Re-add them
# above (and adjust the split) if that's wrong.

CROSS_PROGRAM_SELECTION_SPECS = [
    {
        "base_code": "COMS 101",
        "course_name": "Communication Skills",
        "dept_hint": "DHUM",
        "arts_sections": COMS101_SECTIONS_ARTS,
        "science_sections": COMS101_SECTIONS_SCIENCE,
        "students": DEFAULT_STUDENTS,
    },
]

# --- 2d. Same-program SelectionGroup (N lettered sections, DIFFERENT
#         lecturers, students pick one) -------------------------------------
SAME_PROGRAM_SELECTION_SPECS = [
    {
        "base_code": "PESP 111",
        "course_name": "Hockey, Netball and Soccer",
        "dept_hint": "DEDU",
        "program": "arts",
        "sections": [("A", "Ms. Purity Kananu", 150),
                     ("B", "Ms. Rose Jakinda", 150),
                     ("C", "Mr. Antony Mbita", 150)],
    },
]

# --- 2e. Simple single-row electives — just ensure they exist + submitted --
# (base_code, course_name, dept_hint, program_key, lecturer_name_or_blank)
SIMPLE_SPECS = [
    ("ENGL 102", "Introduction to Phonetics and Phonology",     "DHUM",  "arts",    "Prof. Christine Atieno"),
    ("LITT 101", "Introduction to Literature and Literary Criticism", "DHUM", "arts", "Ms. Purity Wanja"),
    ("LITT 102", "Introduction to Oral Literature",             "DHUM",  "arts",    "Prof. Waita"),
    ("RELI 100", "Introduction Study of Religion",              "DHUM",  "arts",    "Prof. Bururia"),
    ("RELI 120", "African Religions",                           "DHUM",  "arts",    "Prof. Nkonge"),
    ("KISW 101", "Introduction to Linguistics",                 "DHUM",  "arts",    "Prof. John Kobia"),
    ("KISW 102", "History and Modern Development of Kiswahili", "DHUM",  "arts",    "Lemmy Muriuki"),
    ("FREN 110", "French Structure I",                          "DHUM",  "arts",    "Crispus Mwakundia"),
    ("FREN 130", "Written Expression, Interaction & Comprehension", "DHUM", "arts", "Crispus Mwakundia"),
    ("MUSC 101", "Fundamentals of Music and Practical Performance", "DEDU", "arts", ""),
    ("MUSC 111", "Music of the Medieval and Renaissance Times", "DEDU",  "arts",    ""),
    ("GEOG 100", "History of Geographic Thought",               "DSSC",  "arts",    ""),
    ("GEOG 110", "Introduction to Physical Geography",          "DSSC",  "arts",    ""),
    ("BCOM 101", "Introduction to Business",                    "DMSC",  "arts",    ""),
    ("BHRM 113", "Foundations of Accounting",                   "DBAD",  "arts",    "Dr. H. Kimathi"),
    ("PESP 121", "Foundations of Physical Education",           "DEDU",  "arts",    "Ms. Rose Jakinda"),
    ("PESP 141", "Adapted Physical Activity",                   "DEDU",  "arts",    "Mr. Antony Mbita"),

    ("CHEM 101", "Chemical Laboratory Safety and Security",     "PHSC",  "science", ""),
    ("CHEM 110", "Inorganic Chemistry I",                       "PHSC",  "science", ""),
    ("PHYS 121", "Physics Practical I",                         "PHSC",  "science", ""),
    ("PHYS 131", "Mechanics",                                   "PHSC",  "science", ""),
    ("PHYS 161", "Heat and Thermodynamics",                     "PHSC",  "science", ""),
    ("BOTA 101", "General Botany",                              "DBSC",  "science", "Dr. Christopher Mutuku"),
    ("BOTA 111", "General Genetics",                            "DBSC",  "science", "Prof. Moses Muraya"),
    ("ZOOL 101", "Lower Invertebrates",                         "DBSC",  "science", "Dr. Ciriaka Gitonga"),
]

# --- 2f. Cross-listed codes (appear in BOTH Arts and Science lists) — same
#         class, one row per program, merged into one CombinedCourseGroup --
# (base_code, course_name, dept_hint, lecturer_arts, lecturer_science)
CROSS_PROGRAM_MERGE_SPECS = [
    ("MATH 122", "Basic Mathematics",                    "PHSC",  "", ""),
    ("MATH 124", "Geometry and Linear Algebra",          "PHSC",  "", ""),
    ("COSC 106", "Information Technology and Society",   "DCOMP", "", ""),
    ("COSC 111", "Computer Systems and Organization",    "DCOMP", "", ""),
    ("AGRI 101", "Introduction to Agriculture and Food Security", "PLSC", "", "Ms Felister Mbaka/Kimathi"),
    ("SOIL 100", "Introduction to Soil Science",         "PLSC",  "", "Ms. Felsiter Mbaka"),
    ("HOSC 111", "General Science for Home Science",     "DEDU",  "Prof Susan Kinyua", "Prof Susan Kinyua"),
    ("HOSC 131", "Introduction to Foods, Nutrition and Dietetics", "DEDU", "Alice Ngunu", "Alice Ngunu"),
]

PROGRAM_OF = {"arts": program_arts, "science": program_science}


# ------------------------------------------------------------------------------
# 3. Back up EVERY existing CourseAllocation row for EVERY base_code touched
#    by this script, across both programs, before changing anything.
# ------------------------------------------------------------------------------
h1("GATHERING + BACKING UP EXISTING ALLOCATIONS")

all_base_codes = set()
for s in CROSS_PROGRAM_COMBINED_SPECS: all_base_codes.add(normalize_code(s["base_code"]))
for s in SAME_PROGRAM_COMBINED_SPECS: all_base_codes.add(normalize_code(s["base_code"]))
for s in CROSS_PROGRAM_SELECTION_SPECS: all_base_codes.add(normalize_code(s["base_code"]))
for s in SAME_PROGRAM_SELECTION_SPECS: all_base_codes.add(normalize_code(s["base_code"]))
for (bc, *_r) in SIMPLE_SPECS: all_base_codes.add(normalize_code(bc))
for (bc, *_r) in CROSS_PROGRAM_MERGE_SPECS: all_base_codes.add(normalize_code(bc))

candidate_rows = list(
    CourseAllocation.objects
    .filter(program_id__in=[program_arts.id, program_science.id])
    .select_related("program", "department", "lecturer", "selection_group")
    .prefetch_related("combined_groups")
)

existing_by_base = {}
all_existing_ids = []
for base in sorted(all_base_codes):
    rows = [r for r in candidate_rows if matches_base_course(r.course_code, base)]
    existing_by_base[base] = rows
    all_existing_ids.extend(r.id for r in rows)
    codes = sorted({r.course_code for r in rows})
    print(f"{base:<12} existing rows: {len(rows):<4} distinct course_code variants: {codes}")

print(f"\nTotal existing CourseAllocation rows in scope: {len(all_existing_ids)}")

BACKUP_DIR.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_path = BACKUP_DIR / f"bed_y1s1_remaining_fix_{timestamp}.json"

rows_to_backup = list(CourseAllocation.objects.filter(id__in=all_existing_ids))
serialized = serializers.serialize("json", rows_to_backup, indent=2)
with open(backup_path, "w") as f:
    f.write(serialized)

meta_path = backup_path.with_suffix(".meta.json")
with open(meta_path, "w") as f:
    json.dump({
        "created_at": timestamp,
        "programs": [program_arts.name, program_science.name],
        "course_allocations_backed_up": len(rows_to_backup),
        "course_codes": sorted(all_base_codes),
    }, f, indent=2)

print(f"\nBackup written:   {backup_path}")
print(f"Metadata written: {meta_path}")
print(f"To restore:  python manage.py loaddata {backup_path}")


# ------------------------------------------------------------------------------
# 4. Helpers to create curriculum + allocation rows idempotently
# ------------------------------------------------------------------------------
def ensure_program_course(program, base_code, course_name, unit_type="ELECTIVE"):
    pc, created = ProgramCourse.objects.get_or_create(
        program=program,
        course_code=base_code,
        student_cohort="0",
        defaults={
            "course_name": course_name,
            "year": 1,
            "semester": 1,
            "unit_type": unit_type,
        },
    )
    if created:
        print(f"    Created ProgramCourse {base_code} for {program.name}")
    return pc


def ensure_allocation(course_code, course_name, department, origin_department, program,
                       lecturer_obj, students, program_course, is_elective=True,
                       selection_group=None, submitted_to_tt=True):
    existing = CourseAllocation.objects.filter(
        program=program, program_course=program_course, course_code=course_code,
    ).first()

    if existing:
        existing.course_name = course_name
        existing.department = department
        existing.origin_department = origin_department
        existing.lecturer = lecturer_obj
        existing.number_of_students = students
        existing.is_elective = is_elective
        existing.selection_group = selection_group
        existing.submitted_to_tt = submitted_to_tt
        existing.full_clean(exclude=["reason_for_disapproval"])
        existing.save()
        return existing, False

    ca = CourseAllocation(
        course_code=course_code,
        course_name=course_name,
        department=department,
        origin_department=origin_department,
        program=program,
        lecturer=lecturer_obj,
        number_of_students=students,
        program_course=program_course,
        intake=CourseAllocation.INTAKE_NORMAL,
        is_elective=is_elective,
        student_group=None,
        selection_group=selection_group,
        submitted_to_tt=submitted_to_tt,
    )
    ca.full_clean(exclude=["reason_for_disapproval"])
    ca.save()
    return ca, True


def ensure_combined_group(base_code, department, origin_department, allocations, lecturer_obj):
    """Create (or extend) the ONE CombinedCourseGroup for this base_code,
    linking every allocation in `allocations` together."""
    group_code = f"{base_code.replace(' ', '_')}_COMBINED"
    group, created = CombinedCourseGroup.objects.get_or_create(
        group_code=group_code,
        defaults={
            "base_course_code": base_code,
            "lecturer": lecturer_obj,
            "department": department,
            "origin_department": origin_department,
        },
    )
    if not created:
        # extend an existing group rather than clobber it
        if lecturer_obj and not group.lecturer:
            group.lecturer = lecturer_obj
        group.save()
    group.allocations.add(*allocations)
    if not group.primary_allocation_id and allocations:
        group.primary_allocation = allocations[0]
        group.save()
    return group, created


# ------------------------------------------------------------------------------
# 5. Build the full plan (dry-run-safe: no writes happen until section 6)
# ------------------------------------------------------------------------------
h1("PLAN")

plan_actions = []   # list of callables that perform the actual writes


def add_action(description, fn):
    plan_actions.append((description, fn))


# --- 5a. Cross-program combined ("shared course") ---------------------------
for spec in CROSS_PROGRAM_COMBINED_SPECS:
    base = normalize_code(spec["base_code"])
    dept = effective_department(spec["dept_hint"])
    h2(f"{base} — {spec['course_name']}  [cross-program shared course]")
    if dept is None:
        print(f"  SKIPPED — could not resolve department for hint {spec['dept_hint']!r}")
        continue

    lect_arts = resolve_lecturer(spec["lecturer_arts"], department=dept)
    lect_science = resolve_lecturer(spec["lecturer_science"], department=dept)
    print(f"  Arts:    lecturer={spec['lecturer_arts']!r} -> {lect_arts}")
    print(f"  Science: lecturer={spec['lecturer_science']!r} -> {lect_science}")

    def make(spec=spec, dept=dept, base=base, lect_arts=lect_arts, lect_science=lect_science):
        pc_arts = ensure_program_course(program_arts, base, spec["course_name"])
        pc_sci = ensure_program_course(program_science, base, spec["course_name"])
        ca_arts, _ = ensure_allocation(base, spec["course_name"], dept, dept, program_arts,
                                        lect_arts, spec["students"], pc_arts)
        ca_sci, _ = ensure_allocation(base, spec["course_name"], dept, dept, program_science,
                                       lect_science, spec["students"], pc_sci)
        group_lecturer = lect_arts or lect_science
        ensure_combined_group(base, dept, dept, [ca_arts, ca_sci], group_lecturer)

    add_action(f"{base}: create Arts+Science rows, merge into CombinedCourseGroup", make)

# --- 5b. Same-program combined ("shared course") -----------------------------
for spec in SAME_PROGRAM_COMBINED_SPECS:
    base = normalize_code(spec["base_code"])
    dept = effective_department(spec["dept_hint"])
    prog = PROGRAM_OF[spec["program"]]
    h2(f"{base} — {spec['course_name']}  [same-program shared course, {prog.name}]")
    if dept is None:
        print(f"  SKIPPED — could not resolve department for hint {spec['dept_hint']!r}")
        continue

    resolved_sections = []
    for letter, lecturer_name, students in spec["sections"]:
        lect = resolve_lecturer(lecturer_name, department=dept)
        print(f"  {base}{letter}: lecturer={lecturer_name!r} -> {lect}  ({students} students)")
        resolved_sections.append((letter, lect, students))

    def make(spec=spec, dept=dept, base=base, prog=prog, resolved_sections=resolved_sections):
        pc = ensure_program_course(prog, base, spec["course_name"])
        allocations = []
        group_lecturer = None
        for letter, lect, students in resolved_sections:
            ca, _ = ensure_allocation(f"{base}{letter}", spec["course_name"], dept, dept, prog,
                                       lect, students, pc)
            allocations.append(ca)
            group_lecturer = group_lecturer or lect
        ensure_combined_group(base, dept, dept, allocations, group_lecturer)

    add_action(f"{base}: create {len(spec['sections'])} sections under {prog.name}, merge into CombinedCourseGroup", make)

# --- 5c. Cross-program SelectionGroup ----------------------------------------
for spec in CROSS_PROGRAM_SELECTION_SPECS:
    base = normalize_code(spec["base_code"])
    dept = effective_department(spec["dept_hint"])
    h2(f"{base} — {spec['course_name']}  [cross-program SelectionGroup]")
    if dept is None:
        print(f"  SKIPPED — could not resolve department for hint {spec['dept_hint']!r}")
        continue

    resolved = {"arts": [], "science": []}
    for key, sections in (("arts", spec["arts_sections"]), ("science", spec["science_sections"])):
        for letter, lecturer_name in sections:
            lect = resolve_lecturer(lecturer_name, department=dept)
            print(f"  {base}{letter} ({key}): lecturer={lecturer_name!r} -> {lect}")
            resolved[key].append((letter, lect))

    def make(spec=spec, dept=dept, base=base, resolved=resolved):
        for key, prog in (("arts", program_arts), ("science", program_science)):
            sections = resolved[key]
            if not sections:
                continue
            pc = ensure_program_course(prog, base, spec["course_name"])
            sg_name = f"{base} — {prog.name} Sections"
            sg, sg_created = SelectionGroup.objects.get_or_create(
                department=dept, name=sg_name, defaults={"program": prog},
            )
            if sg_created:
                print(f"    Created SelectionGroup '{sg_name}'")
            for letter, lect in sections:
                ca, _ = ensure_allocation(f"{base}{letter}", spec["course_name"], dept, dept, prog,
                                           lect, spec["students"], pc, selection_group=sg)
                sg.courses.add(ca)

    add_action(f"{base}: create {len(spec['arts_sections'])} Arts + {len(spec['science_sections'])} Science sections, add to SelectionGroup", make)

# --- 5d. Same-program SelectionGroup -----------------------------------------
for spec in SAME_PROGRAM_SELECTION_SPECS:
    base = normalize_code(spec["base_code"])
    dept = effective_department(spec["dept_hint"])
    prog = PROGRAM_OF[spec["program"]]
    h2(f"{base} — {spec['course_name']}  [same-program SelectionGroup, {prog.name}]")
    if dept is None:
        print(f"  SKIPPED — could not resolve department for hint {spec['dept_hint']!r}")
        continue

    resolved_sections = []
    for letter, lecturer_name, students in spec["sections"]:
        lect = resolve_lecturer(lecturer_name, department=dept)
        print(f"  {base}{letter}: lecturer={lecturer_name!r} -> {lect}  ({students} students)")
        resolved_sections.append((letter, lect, students))

    def make(spec=spec, dept=dept, base=base, prog=prog, resolved_sections=resolved_sections):
        pc = ensure_program_course(prog, base, spec["course_name"])
        sg_name = f"{base} — {prog.name} Sections"
        sg, sg_created = SelectionGroup.objects.get_or_create(
            department=dept, name=sg_name, defaults={"program": prog},
        )
        if sg_created:
            print(f"    Created SelectionGroup '{sg_name}'")
        for letter, lect, students in resolved_sections:
            ca, _ = ensure_allocation(f"{base}{letter}", spec["course_name"], dept, dept, prog,
                                       lect, students, pc, selection_group=sg)
            sg.courses.add(ca)

    add_action(f"{base}: create {len(spec['sections'])} sections under {prog.name}, add to SelectionGroup", make)

# --- 5e. Simple single-row electives -----------------------------------------
h2("Simple single-row electives (ensure exists + submitted_to_tt=True)")
for base_code, course_name, dept_hint, program_key, lecturer_name in SIMPLE_SPECS:
    base = normalize_code(base_code)
    dept = effective_department(dept_hint)
    prog = PROGRAM_OF[program_key]
    if dept is None:
        print(f"  {base:<10} SKIPPED — could not resolve department for hint {dept_hint!r}")
        continue
    lect = resolve_lecturer(lecturer_name, department=dept)
    print(f"  {base:<10} ({prog.name[:24]:<24}) lecturer={lecturer_name!r} -> {lect}")

    def make(base=base, course_name=course_name, dept=dept, prog=prog, lect=lect):
        pc = ensure_program_course(prog, base, course_name)
        ensure_allocation(base, course_name, dept, dept, prog, lect, DEFAULT_STUDENTS, pc)

    add_action(f"{base}: ensure single elective row under {prog.name}", make)

# --- 5f. Cross-listed (Arts+Science) codes — merge into CombinedCourseGroup -
h2("Cross-listed Arts+Science codes (merge into CombinedCourseGroup)")
for base_code, course_name, dept_hint, lecturer_arts, lecturer_science in CROSS_PROGRAM_MERGE_SPECS:
    base = normalize_code(base_code)
    dept = effective_department(dept_hint)
    if dept is None:
        print(f"  {base:<10} SKIPPED — could not resolve department for hint {dept_hint!r}")
        continue
    lect_arts = resolve_lecturer(lecturer_arts, department=dept)
    lect_science = resolve_lecturer(lecturer_science, department=dept)
    print(f"  {base:<10} Arts lecturer={lecturer_arts!r} -> {lect_arts}   "
          f"Science lecturer={lecturer_science!r} -> {lect_science}")

    def make(base=base, course_name=course_name, dept=dept, lect_arts=lect_arts, lect_science=lect_science):
        pc_arts = ensure_program_course(program_arts, base, course_name)
        pc_sci = ensure_program_course(program_science, base, course_name)
        ca_arts, _ = ensure_allocation(base, course_name, dept, dept, program_arts,
                                        lect_arts, DEFAULT_STUDENTS, pc_arts)
        ca_sci, _ = ensure_allocation(base, course_name, dept, dept, program_science,
                                       lect_science, DEFAULT_STUDENTS, pc_sci)
        group_lecturer = lect_arts or lect_science
        ensure_combined_group(base, dept, dept, [ca_arts, ca_sci], group_lecturer)

    add_action(f"{base}: ensure Arts+Science rows, merge into CombinedCourseGroup", make)

if _dept_resolution_failures:
    h2("COULD NOT RESOLVE DEPARTMENT — these courses will be SKIPPED")
    for short_code, reason in _dept_resolution_failures:
        print(f"   - {short_code}: {reason}")
    print("Fix DEPARTMENT_NAME_HINTS above and re-run to include them.")

if unmatched_lecturers:
    h2("LECTURERS THAT COULD NOT BE MATCHED — these rows will be created with lecturer=None")
    for name in sorted(unmatched_lecturers):
        print(f"   - {name}")


# ------------------------------------------------------------------------------
# 6. Apply
# ------------------------------------------------------------------------------
h1("APPLY" if not DRY_RUN else "APPLY (SKIPPED — DRY_RUN = True)")

if DRY_RUN:
    print(f"DRY_RUN is True — {len(plan_actions)} planned action(s) were NOT executed.")
    print("Review the PLAN above (and the backup), then flip DRY_RUN = False and re-run.")
    line("=")
    sys.exit(0)

applied = 0
with transaction.atomic():
    for description, fn in plan_actions:
        fn()
        applied += 1
        print(f"  \u2713 {description}")

print(f"\nApplied {applied}/{len(plan_actions)} planned action(s).")


# ------------------------------------------------------------------------------
# 7. Safety-net sweep — merge any REMAINING duplicate rows for these course
#    codes that weren't already linked by a CombinedCourseGroup above. This is
#    what catches stray legacy duplicates (e.g. GEOG 100) even though the
#    Word document itself shows only a single row for them.
# ------------------------------------------------------------------------------
h1("SAFETY-NET SWEEP — MERGE ANY REMAINING DUPLICATE ROWS")

swept = 0
already_combined_ids = set(
    CombinedCourseGroup.objects.values_list("allocations__id", flat=True)
)

for base in sorted(all_base_codes):
    rows = [
        r for r in CourseAllocation.objects.filter(program_id__in=[program_arts.id, program_science.id])
        if matches_base_course(r.course_code, base)
    ]
    leftover = [r for r in rows if r.id not in already_combined_ids]
    if len(leftover) < 2:
        continue

    dept = leftover[0].department
    lecturers = {r.lecturer_id for r in leftover if r.lecturer_id}
    group_lecturer = leftover[0].lecturer if len(lecturers) <= 1 else None
    group, created = ensure_combined_group(base, dept, dept, leftover, group_lecturer)
    swept += 1
    print(f"  Merged {len(leftover)} leftover row(s) for {base} into CombinedCourseGroup "
          f"'{group.group_code}' ({'new' if created else 'existing, extended'})")

if swept == 0:
    print("No leftover duplicate rows found — nothing to sweep.")

h1("DONE")
line("=")
