"""
map_lecturers_to_course_allocations.py
========================================
Run inside the Django shell:

    python manage.py shell < map_lecturers_to_course_allocations.py

or paste interactively into `python manage.py shell`.

This version is SELF-CONTAINED: the two CSVs (the course/lecturer mapping
file and the Lecturer roster export) are embedded below as seed data, so
there's nothing to upload into the container - just paste/run this file.
If you want to swap in a different pair of CSVs later, replace the two
MAPPING_CSV_TEXT / LECTURER_CSV_TEXT blocks near the bottom.

WHAT THIS DOES
--------------
1. Audits the embedded Lecturer roster CSV against the live Lecturer
   table (payroll number + name), so you can see up front whether the
   roster export and the DB agree before any mapping happens.
2. Reads the embedded course->lecturer CSV and, for every row:
     - Finds the matching ProgramCourse (program + course_code + cohort).
     - Finds the CourseAllocation row(s) built from that ProgramCourse.
     - If the `lecturer` cell is a real person's name, fuzzy-matches it
       against Lecturer.objects and sets CourseAllocation.lecturer.
     - If the `lecturer` cell is actually a department marker (e.g.
       "Comp Sci Dept", "Humanities Dept") it resolves that to a real
       Department and sets CourseAllocation.origin_department instead
       (lecturer is left alone - a specific person hasn't been named).
     - If the cell is blank or "ALL" (project units supervised by
       everyone), the row is skipped and logged.
3. NEVER creates a new Lecturer or Department record. Anything it can't
   match with confidence is written to a review CSV instead of being
   guessed at.

WHY THE FUZZY MATCHING IS CONSERVATIVE
---------------------------------------
Spot-checking the Lecturer roster turned up TEMP0263 "Prof. Dennis
Murithi" and TEMP0264 "Prof. Mark Okango" - almost certainly junk
records created by an earlier import that couldn't find "Dennis
Muriithi" / "Mark Okongo" and created a new Lecturer from the raw CSV
string (designation baked into the name field) instead of matching the
real CHU/0075 / CHU/0092 records. This script's matcher is
order/token-based specifically so it prefers the real record over
near-duplicate junk, and it downranks any payroll number starting with
"TEMP". It still won't silently invent new people - ambiguous names are
logged, not guessed.
"""

import csv
import difflib
import io
import re
from collections import defaultdict

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
PROGRAM_NAME = "Bachelor of Science"          # Program.name is unique, so this pins one row

DRY_RUN = False          # <-- flip to False once the printed report looks right
AUTO_APPLY_MIN_OVERLAP = 0.9     # accept if this fraction of name-tokens overlap either way
AUTO_APPLY_MIN_RATIO = 0.85      # ...or accept on a strong difflib ratio even with lower overlap
REVIEW_REPORT_PATH = "lecturer_mapping_review.csv"   # written to the current working directory

DEPT_ALIASES = {
    "comp sci": "computer science",
    "comp. sci": "computer science",
    "computer sci": "computer science",
    "biological sci": "biological sciences",
    "biological science": "biological sciences",
    "physical science": "physical sciences",
    "physical sci": "physical sciences",
    "social science": "social sciences",
    "social sciences": "social sciences",
    "humanities": "humanities",
}

# ────────────────────────────── HELPERS ────────────────────────────────

def normalize_person_name(raw):
    """Strip designation prefix + punctuation, lowercase, collapse spaces."""
    if not raw:
        return ""
    s = raw.strip()
    s = re.sub(r"^(prof\.?|dr\.?|mr\.?|ms\.?|mrs\.?)\s+", "", s, flags=re.I)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.lower()


def is_all_or_blank(raw):
    return (not raw) or raw.strip().upper() == "ALL"


def is_department_marker(raw):
    return "dept" in raw.lower()


def resolve_department(raw, department_cache):
    """Resolve a 'Comp Sci Dept'-style string to a real Department row."""
    base = re.sub(r"\bdepartment\b|\bdept\.?\b", "", raw, flags=re.I).strip()
    base = base.rstrip(".").strip()
    key = base.lower()

    if key in department_cache:
        return department_cache[key]

    canonical = DEPT_ALIASES.get(key)
    dept = None
    if canonical:
        dept = Department.objects.filter(name__iexact=canonical).first()
    if not dept:
        all_names = {d.name.lower(): d for d in Department.objects.all()}
        close = difflib.get_close_matches(key, list(all_names.keys()), n=1, cutoff=0.5)
        if close:
            dept = all_names[close[0]]

    department_cache[key] = dept
    return dept


def build_lecturer_index():
    index = []
    for lec in Lecturer.objects.select_related("department").all():
        norm = normalize_person_name(lec.name)
        index.append({
            "obj": lec,
            "norm": norm,
            "tokens": set(norm.split()),
            "dept_name": lec.department.name if lec.department else None,
            "is_temp": lec.payroll_number.strip().upper().startswith("TEMP"),
        })
    return index


def resolve_lecturer(raw, lecturer_index, expected_dept_name):
    """
    Returns (matched_lecturer_or_None, confident_bool, suggestions)
    suggestions is a list of (payroll_number, name, score) for the report,
    top-3, regardless of whether the top one was confident enough to apply.
    """
    q_norm = normalize_person_name(raw)
    q_tokens = set(q_norm.split())
    if not q_tokens:
        return None, False, []

    scored = []
    for entry in lecturer_index:
        if not entry["tokens"]:
            continue
        overlap = len(q_tokens & entry["tokens"]) / min(len(q_tokens), len(entry["tokens"]))
        ratio = difflib.SequenceMatcher(None, q_norm, entry["norm"]).ratio()
        score = overlap * 0.7 + ratio * 0.3
        if expected_dept_name and entry["dept_name"] == expected_dept_name:
            score += 0.1
        if entry["is_temp"]:
            score -= 0.3
        scored.append((score, overlap, ratio, entry))

    if not scored:
        return None, False, []

    scored.sort(key=lambda t: -t[0])
    top3 = scored[:3]
    best_score, best_overlap, best_ratio, best_entry = top3[0]
    confident = best_overlap >= AUTO_APPLY_MIN_OVERLAP or (
        best_overlap >= 0.5 and best_ratio >= AUTO_APPLY_MIN_RATIO
    )
    suggestions = [(e["obj"].payroll_number, e["obj"].name, round(s, 3)) for s, _, _, e in top3]
    return (best_entry["obj"] if confident else None), confident, suggestions


# ───────────────────────── STEP 1: LECTURER AUDIT ──────────────────────

def audit_lecturer_csv():
    print("=" * 70)
    print("STEP 1 — Auditing Lecturer roster (seed data) against the DB")
    print("=" * 70)

    total = found_exact = found_by_name_only = missing = 0
    missing_rows = []

    reader = csv.DictReader(io.StringIO(LECTURER_CSV_TEXT))
    for row in reader:
        total += 1
        payroll = (row.get("payroll_number") or "").strip()
        name = (row.get("name") or "").strip()

        lec = Lecturer.objects.filter(payroll_number=payroll).first()
        if lec:
            found_exact += 1
            if lec.name.strip().lower() != name.lower():
                print(f"  ~ {payroll}: DB name {lec.name!r} != CSV name {name!r}")
            continue

        lec_by_name = Lecturer.objects.filter(name__iexact=name).first()
        if lec_by_name:
            found_by_name_only += 1
            print(f"  ~ {payroll}: no DB row with that payroll number, "
                  f"but name matches {lec_by_name.payroll_number} ({lec_by_name.name!r})")
            continue

        missing += 1
        missing_rows.append((payroll, name))

    print(f"\n  Rows in CSV: {total}")
    print(f"  Matched by payroll number: {found_exact}")
    print(f"  Matched by name only (payroll mismatch): {found_by_name_only}")
    print(f"  Not found in DB at all: {missing}")
    if missing_rows:
        print("  Missing:")
        for payroll, name in missing_rows[:25]:
            print(f"    - {payroll}: {name}")
        if len(missing_rows) > 25:
            print(f"    ... and {len(missing_rows) - 25} more")
    print()


# ─────────────────────── STEP 2: COURSE MAPPING ────────────────────────

def run_mapping():
    print("=" * 70)
    print("STEP 2 — Mapping lecturers / origin departments onto CourseAllocation")
    print(f"DRY_RUN = {DRY_RUN}")
    print("=" * 70)

    try:
        program = Program.objects.get(name__iexact=PROGRAM_NAME)
    except Program.DoesNotExist:
        print(f"  ! Program {PROGRAM_NAME!r} not found. Aborting.")
        return
    except Program.MultipleObjectsReturned:
        print(f"  ! Multiple programs named {PROGRAM_NAME!r} - Program.name should be unique. Aborting.")
        return

    expected_dept_name = program.department.name if program.department else None
    print(f"  Program: {program.name} (home department: {expected_dept_name})")

    lecturer_index = build_lecturer_index()
    department_cache = {}
    stats = defaultdict(int)
    review_rows = []

    def process():
        reader = csv.DictReader(io.StringIO(MAPPING_CSV_TEXT))
        for row in reader:
            stats["rows_read"] += 1
            course_code = normalize_code(row.get("course_code", ""))
            cohort = (row.get("student_cohort") or "0").strip() or "0"
            raw_lecturer = (row.get("lecturer") or "").strip()

            pc = ProgramCourse.objects.filter(
                program=program, course_code=course_code, student_cohort=cohort
            ).first()
            if not pc:
                pc = ProgramCourse.objects.filter(
                    program=program, course_code=course_code
                ).order_by("-student_cohort").first()
            if not pc:
                stats["unmatched_program_course"] += 1
                review_rows.append({
                    "course_code": course_code, "raw_lecturer": raw_lecturer,
                    "issue": "No matching ProgramCourse", "suggestions": "",
                })
                continue

            cas = list(CourseAllocation.objects.filter(program_course=pc))
            if not cas:
                stats["no_course_allocation"] += 1
                review_rows.append({
                    "course_code": course_code, "raw_lecturer": raw_lecturer,
                    "issue": "ProgramCourse matched but no CourseAllocation rows exist yet",
                    "suggestions": "",
                })
                continue

            if is_all_or_blank(raw_lecturer):
                stats["skipped_all_or_blank"] += 1
                continue

            if is_department_marker(raw_lecturer):
                dept = resolve_department(raw_lecturer, department_cache)
                if not dept:
                    stats["unresolved_department"] += 1
                    review_rows.append({
                        "course_code": course_code, "raw_lecturer": raw_lecturer,
                        "issue": "Could not resolve department marker", "suggestions": "",
                    })
                    continue
                for ca in cas:
                    if ca.origin_department_id == dept.id:
                        stats["origin_department_already_correct"] += 1
                        continue
                    stats["origin_department_set"] += 1
                    print(f"  [DEPT] {course_code}: origin_department -> {dept.name}"
                          f"{' (would set)' if DRY_RUN else ''}")
                    if not DRY_RUN:
                        ca.origin_department = dept
                        ca.save(update_fields=["origin_department"])
                continue

            # otherwise: treat as a person's name
            lecturer_obj, confident, suggestions = resolve_lecturer(
                raw_lecturer, lecturer_index, expected_dept_name
            )
            if confident and lecturer_obj:
                for ca in cas:
                    if ca.lecturer_id == lecturer_obj.id:
                        stats["lecturer_already_correct"] += 1
                        continue
                    stats["lecturer_set"] += 1
                    print(f"  [LEC ] {course_code}: {raw_lecturer!r} -> "
                          f"{lecturer_obj.name} ({lecturer_obj.payroll_number})"
                          f"{' (would set)' if DRY_RUN else ''}")
                    if not DRY_RUN:
                        ca.lecturer = lecturer_obj
                        ca.save(update_fields=["lecturer"])
            else:
                stats["needs_review"] += 1
                review_rows.append({
                    "course_code": course_code, "raw_lecturer": raw_lecturer,
                    "issue": "No confident lecturer match",
                    "suggestions": "; ".join(f"{n} ({p}) score={s}" for p, n, s in suggestions),
                })

    if DRY_RUN:
        process()
    else:
        with transaction.atomic():
            process()

    print("\n" + "-" * 70)
    print("SUMMARY")
    print("-" * 70)
    for key in [
        "rows_read", "lecturer_set", "lecturer_already_correct",
        "origin_department_set", "origin_department_already_correct",
        "skipped_all_or_blank", "needs_review",
        "unresolved_department", "unmatched_program_course", "no_course_allocation",
    ]:
        print(f"  {key:35} {stats[key]}")

    if review_rows:
        with open(REVIEW_REPORT_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["course_code", "raw_lecturer", "issue", "suggestions"])
            w.writeheader()
            w.writerows(review_rows)
        print(f"\n  {len(review_rows)} rows need manual review -> {REVIEW_REPORT_PATH}")

    if DRY_RUN:
        print("\n  DRY_RUN is True — nothing was written to the DB. "
              "Review the output above and the review CSV, then set DRY_RUN = False and re-run.")


# ═══════════════════════════ EMBEDDED SEED DATA ═══════════════════════════
# The two CSVs below are the exact data used to build/verify this script's
# matching logic. Replace MAPPING_CSV_TEXT / LECTURER_CSV_TEXT if you need
# to run this against a different department's data later.

MAPPING_CSV_TEXT = """\
program,course_code,course_name,lecturer,year,semester,unit_type,student_cohort
Bachelor of Science,FNM 103,Introduction to Accounts and Finance,Mr. Edwin Misati,1,1,Core,0
Bachelor of Science,MATH 122,Basic Mathematics,Dr. Edith Warue,1,1,Core,0
Bachelor of Science,MATH 124,Geometry and Linear Algebra,Ms. Olivia Waka,1,1,Core,0
Bachelor of Science,MATH 125,Discrete Mathematics,Ms. Teresa Mbothia,1,1,Core,0
Bachelor of Science,COSC 103,Introduction to Computer Applications,Comp Sci Dept,1,1,Core,0
Bachelor of Science,COMS 101,Communication skills,Humanities Dept,1,1,Core,0
Bachelor of Science,ECON 111,Principles of Microeconomics,Social Sciences Dept,1,1,Core,0
Bachelor of Science,GPHY 121,Geophysics Practical I,Mr. Samson Njogu,1,1,Core,0
Bachelor of Science,GPHY 111,Introduction to Geology,Dr. Anthony Odek,1,1,Core,0
Bachelor of Science,GPHY 131,Mining and Metallurgy,Dr. Jane Mbae,1,1,Core,0
Bachelor of Science,PHYS 131,Mechanics I,Dr. Elosy Gatakaa,1,1,Core,0
Bachelor of Science,PHYS 161,Heat and Thermodynamics,Dr. Zipporah Muthui,1,1,Core,0
Bachelor of Science,MATH 122,Basic Mathematics,Dr. Edith Warue,1,1,Core,0
Bachelor of Science,COSC 103,Introduction to Computer Applications,Comp Sci Dept,1,1,Core,0
Bachelor of Science,ZOOL 143,HIV/AIDS and Society,Biological Sci Dept,1,1,Core,0
Bachelor of Science,CHEM 102,General Inorganic and Physical Chemistry,Dr. Edward Njagi,1,1,Core,0
Bachelor of Science,PHYS 121,Physics Practical I,Mr. Samson Njogu,1,1,Core,0
Bachelor of Science,CHEM 101,Chemical Laboratory Safety and Security,Dr. Jane Mbae,1,1,Core,0
Bachelor of Science,CHEM 120,Physical Chemistry I,Prof Ochieng Ombaka,1,1,Core,0
Bachelor of Science,CHEM 110,Inorganic Chemistry I,Ms. Faith Yator,1,1,Core,0
Bachelor of Science,MATH 122,Basic Mathematics,Dr. Edith Warue,1,1,Core,0
Bachelor of Science,MATH 124,Geometry and Linear Algebra,Mr. Victor Lumumba,1,1,Core,0
Bachelor of Science,PHYS 131,Mechanics I,Dr. Elosy Gatakaa,1,1,Core,0
Bachelor of Science,PHYS 161,Heat and Thermodynamics,Dr. Zipporah Muthui,1,1,Core,0
Bachelor of Science,ZOOL 143,HIV/AIDS and Society,Biological Sci Dept,1,1,Core,0
Bachelor of Science,COSC 103,Introduction to Computer Applications,Comp Sci Dept,1,1,Core,0
Bachelor of Science,PHY 121,Physics Practical I,Mr. Samson Njogu,1,1,Core,0
Bachelor of Science,BOTA 101,General Botany,Biological Sci Dept,1,1,Core,0
Bachelor of Science,BOTA 111,Genetics,Biological Sci Dept,1,1,Core,0
Bachelor of Science,ZOOL 101,Lower Invertebrates,Biological Sci Dept,1,1,Core,0
Bachelor of Science,MATH 101,Mathematics for Science,Dr. Nelson Mugambi,1,1,Core,0
Bachelor of Science,COSC 103,Introduction to Computer Applications,Comp Sci Dept,1,1,Core,0
Bachelor of Science,MATH 122,Basic Mathematics,Dr. Edith Warue,1,1,Core,0
Bachelor of Science,MATH 124,Geometry and Linear Algebra,Mr. Victor Lumumba,1,1,Core,0
Bachelor of Science,BOTA 101,General Botany,Biological Sci Dept,1,1,Core,0
Bachelor of Science,BOTA 111,General Genetics,Biological Sci Dept,1,1,Core,0
Bachelor of Science,ZOOL 101,Lower Invertebrates,Biological Sci Dept,1,1,Core,0
Bachelor of Science,COSC 103,Introduction to Computer Applications,Comp Sci Dept,1,1,Core,0
Bachelor of Science,COMS 101,Communication Skills,Humanities Dept,1,1,Core,0
Bachelor of Science,ZOOL 143,HIV/AIDS and Society,Biological Sci Dept,1,1,Core,0
Bachelor of Science,MATH 142,Exploratory Data Analysis,Ms. Olivia Waka,1,1,Core,0
Bachelor of Science,CHEM 211,Physical Inorganic Chemistry,Prof. Joel Gichumbi,2,1,Core,0
Bachelor of Science,CHEM 231,Organic Chemistry II,Mr. Richard Kariuki,2,1,Core,0
Bachelor of Science,MATH 221,Calculus II,Ms. Monicah Maithima,2,1,Core,0
Bachelor of Science,MATH 241,Probability and Statistics I,Dr. Elizabeth Njoroge,2,1,Core,0
Bachelor of Science,MATH 222,Vector Analysis,Mr. Edwin Mwenda,2,1,Core,0
Bachelor of Science,PHYS 223,Physics Practical III,Dr. Zipporah Muthui,2,1,Core,0
Bachelor of Science,PHYS 232,Waves and Oscillations,Mr. Beniface Mwanzia,2,1,Core,0
Bachelor of Science,PHYS 241,Electricity and Magnetism I,Dr. Elosy Gatakaa,2,1,Core,0
Bachelor of Science,CHAL 202,Statistical Methods for Environmental Analysis II,Prof. Moses Muraya,2,1,Core,0
Bachelor of Science,CHEM 211,Physical Inorganic Chemistry,Prof. Joel Gichumbi,2,1,Core,0
Bachelor of Science,CHEM 231,Organic Chemistry II,Mr. Richard Kariuki,2,1,Core,0
Bachelor of Science,BOTA 232,Psychology,Biological Sci Dept,2,1,Core,0
Bachelor of Science,BOTA 241,Taxonomy of Higher Plants,Biological Sci Dept,2,1,Core,0
Bachelor of Science,ZOOL 210,Ecology,Biological Sci Dept,2,1,Core,0
Bachelor of Science,ZOOL 232,Cell Biology,Biological Sci Dept,2,1,Core,0
Bachelor of Science,CHAL 202,Statistical Methods for Environmental Analysis II,Biological Sci Dept,2,1,Core,0
Bachelor of Science,MATH 221,Calculus II,Ms. Monicah Maithima,2,1,Core,0
Bachelor of Science,MATH 241,Probability and Statistics I,Dr. Elizabeth Njoroge,2,1,Core,0
Bachelor of Science,MATH 222,Vector Analysis,Mr. Edwin Mwenda,2,1,Core,0
Bachelor of Science,BOTA 232,Psychology,Biological Sci Dept,2,1,Core,0
Bachelor of Science,BOTA 241,Taxonomy of Higher Plants,Biological Sci Dept,2,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 342,Economic Botany,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 352,Principles of Crop Protection,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 372,Ecophysiology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 301,Animal Systematics and Evolution,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 302,History and Philosophy of Biology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 311,Freshwater Biology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 362,Botany Field Course,Biological Sci Dept,3,1,Core,0
Bachelor of Science,MATH 302,Real Analysis I,Ms. Teresa Mbothia,3,1,Core,0
Bachelor of Science,MATH 301,Linear Algebra II,Mr. Edwin Mwenda,3,1,Core,0
Bachelor of Science,MATH 322,Ordinary Differential Equations I,Dr. Alice Lunani,3,1,Core,0
Bachelor of Science,BOTA 302,Biostatistics,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 322,Plant Growth and Development,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 321,Fundamentals of Entomology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 330,Animal Physiology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,MATH 321,Calculus III,Dr. Alice Lunani,3,1,Core,0
Bachelor of Science,MATH 301,Linear Algebra II,Mr. Edwin Mwenda,3,1,Core,0
Bachelor of Science,MATH 324,Dynamics,Prof. Mark Okongo,3,1,Core,0
Bachelor of Science,MATH 344,Theory of Estimation,Dr. Elizabeth Njoroge,3,1,Core,0
Bachelor of Science,MATH 342,Quality Control Methods,Prof. Dennis Muriithi,3,1,Core,0
Bachelor of Science,MATH 343,Applied statistics,Prof. Dennis Muriithi,3,1,Core,0
Bachelor of Science,MATH 325,Fluid Mechanics I,Dr. Jacob Kirimi,3,1,Core,0
Bachelor of Science,CHEM 322,Physical Chemistry III,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,CHEM 323,Chemical Kinetics,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,BOTA 302,Biostatistics,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 322,Plant Growth and Development,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 321,Fundamentals of Entomology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 330,Animal Physiology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,CHEM 344,Environmental Chemistry I,Prof. Stanley Kagwanja,3,1,Core,0
Bachelor of Science,CHEM 342,Atomic Spectroscopy,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,CHEM 332,Organic Chemistry III,Prof. Eric Njagi,3,1,Core,0
Bachelor of Science,CHEM 314,Bioinorganic Chemistry,Prof. Stanley Kagwanja,3,1,Core,0
Bachelor of Science,CHEM 343,Industrial and Applied Chemistry I,Prof. Eric Njagi,3,1,Core,0
Bachelor of Science,CHEM 333,Chemistry of Lipids,Physical Science Dept,3,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 342,Economic Botany,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 352,Principles of Crop Protection,Biological Sci Dept,3,1,Core,0
Bachelor of Science,MATH 302,Real Analysis I,Ms. Teresa Mbothia,3,1,Core,0
Bachelor of Science,MATH 301,Linear Algebra II,Mr. Edwin Mwenda,3,1,Core,0
Bachelor of Science,MATH 322,Ordinary Differential Equations I,Dr. Alice Lunani,3,1,Core,0
Bachelor of Science,CHEM 322,Physical Chemistry III,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,CHEM 323,Chemical Kinetics,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,CHEM 344,Environmental Chemistry I,Prof. Stanley Kagwanja,3,1,Core,0
Bachelor of Science,CHEM 342,Atomic Spectroscopy,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,CHEM 332,Organic Chemistry III,Prof. Eric Njagi,3,1,Core,0
Bachelor of Science,CHEM 314,Bioinorganic Chemistry,Prof. Stanley Kagwanja,3,1,Core,0
Bachelor of Science,CHEM 343,Industrial and Applied Chemistry I,Prof. Eric Njagi,3,1,Core,0
Bachelor of Science,CHEM 333,Chemistry of Lipids,Physical Science Dept,3,1,Core,0
Bachelor of Science,MATH 321,Calculus III,Dr. Alice Lunani,3,1,Core,0
Bachelor of Science,MATH 301,Linear Algebra II,Mr. Edwin Mwenda,3,1,Core,0
Bachelor of Science,MATH 324,Dynamics,Prof. Mark Okongo,3,1,Core,0
Bachelor of Science,MATH 344,Theory of Estimation,Dr. Elizabeth Njoroge,3,1,Core,0
Bachelor of Science,MATH 342,Quality Control Methods,Prof. Dennis Muriithi,3,1,Core,0
Bachelor of Science,MATH 302,Real Analysis I,Ms. Teresa Mbothia,3,1,Core,0
Bachelor of Science,MATH 301,Linear Algebra II,Mr. Edwin Mwenda,3,1,Core,0
Bachelor of Science,MATH 322,Ordinary Differential Equations I,Dr. Alice Lunani,3,1,Core,0
Bachelor of Science,PHYS 336,Quantum Mechanics I,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,PHYS 342,Electricity & Magnetism II,Dr. Elosy Gatakaa,3,1,Core,0
Bachelor of Science,PHYS 325,Physics Practicals V,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,PHYS 317,Mathematical Physics I,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,MATH 321,Calculus III,Dr. Alice Lunani,3,1,Core,0
Bachelor of Science,MATH 301,Linear Algebra II,Mr. Edwin Mwenda,3,1,Core,0
Bachelor of Science,MATH 324,Dynamics,Prof. Mark Okongo,3,1,Core,0
Bachelor of Science,MATH 344,Theory of Estimation,Dr. Elizabeth Njoroge,3,1,Core,0
Bachelor of Science,MATH 342,Quality Control Methods,Prof. Dennis Muriithi,3,1,Core,0
Bachelor of Science,MATH 343,Applied statistics,Prof. Dennis Muriithi,3,1,Core,0
Bachelor of Science,MATH 325,Fluid Mechanics I,Dr. Jacob Kirimi,3,1,Core,0
Bachelor of Science,PHYS 391,Astrophysics,Dr. Martin Mutie,3,1,Core,0
Bachelor of Science,PHYS 392,Biophysics,Dr. Martin Mutie,3,1,Core,0
Bachelor of Science,PHYS 362,Thermal and Statistical Physics,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,PHYS 333/233,Mechanics II,Mr. Samson Njogu,3,1,Core,0
Bachelor of Science,PHYS 317,Mathematical Physics I,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,CHEM 322,Physical Chemistry III,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,CHEM 323,Chemical Kinetics,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,PHYS 336,Quantum Mechanics I,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,PHYS 342,Electricity & Magnetism II,Dr. Elosy Gatakaa,3,1,Core,0
Bachelor of Science,PHYS 325,Physics Practicals V,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,PHYS 317,Mathematical Physics I,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,CHEM 344,Environmental Chemistry I,Prof. Stanley Kagwanja,3,1,Core,0
Bachelor of Science,CHEM 342,Atomic Spectroscopy,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,CHEM 332,Organic Chemistry III,Prof. Eric Njagi,3,1,Core,0
Bachelor of Science,CHEM 314,Bioinorganic Chemistry,Prof. Stanley Kagwanja,3,1,Core,0
Bachelor of Science,CHEM 343,Industrial and Applied Chemistry I,Prof. Eric Njagi,3,1,Core,0
Bachelor of Science,CHEM 333,Chemistry of Lipids,Physical Science Dept,3,1,Core,0
Bachelor of Science,PHYS 391,Astrophysics,Dr. Martin Mutie,3,1,Core,0
Bachelor of Science,PHYS 317,Mathematical Physics I (Core for Physics Major),Dr. William Ndeke,3,1,Core,0
Bachelor of Science,PHYS 392,Biophysics,Dr. Martin Mutie,3,1,Core,0
Bachelor of Science,PHYS 362,Thermal and Statistical Physics,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,PHYS 333/233,Mechanics II (Core for Physics Major),Mr. Samson Njogu,3,1,Core,0
Bachelor of Science,PHYS 317,Mathematical Physics I,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,PHYS 325,Physics Practicals V,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,PHYS 336,Quantum Mechanics I,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,PHYS 342,Electricity & Magnetism II,Dr. Elosy Gatakaa,3,1,Core,0
Bachelor of Science,PHYS 391,Astrophysics,Dr. Martin Mutie,3,1,Core,0
Bachelor of Science,PHYS 392,Biophysics,Dr. Martin Mutie,3,1,Core,0
Bachelor of Science,PHYS 362,Thermal and Statistical Physics,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,PHYS 333/233,Mechanics II,Mr. Samson Njogu,3,1,Core,0
Bachelor of Science,CHEM 344,Environmental Chemistry I,Prof. Stanley Kagwanja,3,1,Core,0
Bachelor of Science,CHEM 342,Atomic Spectroscopy,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,CHEM 332,Organic Chemistry III,Prof. Eric Njagi,3,1,Core,0
Bachelor of Science,CHEM 314,Bioinorganic Chemistry,Prof. Stanley Kagwanja,3,1,Core,0
Bachelor of Science,CHEM 343,Industrial and Applied Chemistry I,Prof. Eric Njagi,3,1,Core,0
Bachelor of Science,CHEM 333,Chemistry of Lipids,Physical Science Dept,3,1,Core,0
Bachelor of Science,MATH 321,Calculus III,Dr. Alice Lunani,3,1,Core,0
Bachelor of Science,MATH 301,Linear Algebra II,Mr. Edwin Mwenda,3,1,Core,0
Bachelor of Science,MATH 324,Dynamics,Prof. Mark Okongo,3,1,Core,0
Bachelor of Science,MATH 344,Theory of Estimation,Dr. Elizabeth Njoroge,3,1,Core,0
Bachelor of Science,MATH 342,Quality Control Methods,Prof. Dennis Muriithi,3,1,Core,0
Bachelor of Science,PHYS 317,Mathematical Physics I,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 342,Economic Botany,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 352,Principles of Crop Protection,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 372,Ecophysiology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 301,Animal Systematics and Evolution,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 302,History and Philosophy of Biology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 311,Freshwater Biology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 362,Botany Field Course,Biological Sci Dept,3,1,Core,0
Bachelor of Science,MATH 302,Real Analysis I,Ms. Teresa Mbothia,3,1,Core,0
Bachelor of Science,MATH 301,Linear Algebra II,Mr. Edwin Mwenda,3,1,Core,0
Bachelor of Science,MATH 322,Ordinary Differential Equations I,Dr. Alice Lunani,3,1,Core,0
Bachelor of Science,BOTA 302,Biostatistics,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 322,Plant Growth and Development,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 321,Fundamentals of Entomology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 330,Animal Physiology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,MATH 321,Calculus III,Dr. Alice Lunani,3,1,Core,0
Bachelor of Science,MATH 301,Linear Algebra II,Mr. Edwin Mwenda,3,1,Core,0
Bachelor of Science,MATH 324,Dynamics,Prof. Mark Okongo,3,1,Core,0
Bachelor of Science,MATH 344,Theory of Estimation,Dr. Elizabeth Njoroge,3,1,Core,0
Bachelor of Science,MATH 342,Quality Control Methods,Prof. Dennis Muriithi,3,1,Core,0
Bachelor of Science,MATH 343,Applied statistics,Prof. Dennis Muriithi,3,1,Core,0
Bachelor of Science,MATH 325,Fluid Mechanics I,Dr. Jacob Kirimi,3,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 342,Economic Botany,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 352,Principles of Crop Protection,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 372,Ecophysiology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 301,Animal Systematics and Evolution,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 302,History and Philosophy of Biology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 311,Freshwater Biology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 362,Botany Field Course,Biological Sci Dept,3,1,Core,0
Bachelor of Science,CHEM 322,Physical Chemistry III,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,CHEM 323,Chemical Kinetics,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,BOTA 302,Biostatistics,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 322,Plant Growth and Development,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 321,Fundamentals of Entomology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 330,Animal Physiology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,CHEM 344,Environmental Chemistry I,Prof. Stanley Kagwanja,3,1,Core,0
Bachelor of Science,CHEM 342,Atomic Spectroscopy,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,CHEM 332,Organic Chemistry III,Prof. Eric Njagi,3,1,Core,0
Bachelor of Science,CHEM 314,Bioinorganic Chemistry,Prof. Stanley Kagwanja,3,1,Core,0
Bachelor of Science,CHEM 343,Industrial and Applied Chemistry I,Prof. Eric Njagi,3,1,Core,0
Bachelor of Science,CHEM 333,Chemistry of Lipids,Physical Science Dept,3,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 342,Economic Botany,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 352,Principles of Crop Protection,Biological Sci Dept,3,1,Core,0
Bachelor of Science,PHYS 317,Mathematical Physics I,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 342,Economic Botany,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 352,Principles of Crop Protection,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 372,Ecophysiology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 301,Animal Systematics and Evolution,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 302,History and Philosophy of Biology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 311,Freshwater Biology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 362,Botany Field Course,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 342,Economic Botany,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 301,Animal Systematics and Evolution,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 302,History and Philosophy of Biology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 311,Freshwater Biology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 362,Botany Field Course,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,MATH 302,Real Analysis I,Ms. Teresa Mbothia,3,1,Core,0
Bachelor of Science,MATH 301,Linear Algebra II,Mr. Edwin Mwenda,3,1,Core,0
Bachelor of Science,MATH 322,Ordinary Differential Equations I,Dr. Alice Lunani,3,1,Core,0
Bachelor of Science,BOTA 302,Biostatistics,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 322,Plant Growth and Development,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 321,Fundamentals of Entomology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 330,Animal Physiology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,MATH 321,Calculus III,Dr. Alice Lunani,3,1,Core,0
Bachelor of Science,MATH 301,Linear Algebra II,Mr. Edwin Mwenda,3,1,Core,0
Bachelor of Science,MATH 324,Dynamics,Prof. Mark Okongo,3,1,Core,0
Bachelor of Science,MATH 344,Theory of Estimation,Dr. Elizabeth Njoroge,3,1,Core,0
Bachelor of Science,MATH 342,Quality Control Methods,Prof. Dennis Muriithi,3,1,Core,0
Bachelor of Science,MATH 343,Applied statistics,Prof. Dennis Muriithi,3,1,Core,0
Bachelor of Science,MATH 325,Fluid Mechanics I,Dr. Jacob Kirimi,3,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 342,Economic Botany,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 352,Principles of Crop Protection,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 372,Ecophysiology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 301,Animal Systematics and Evolution,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 302,History and Philosophy of Biology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 311,Freshwater Biology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 362,Botany Field Course,Biological Sci Dept,3,1,Core,0
Bachelor of Science,CHEM 322,Physical Chemistry III,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,CHEM 323,Chemical Kinetics,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,BOTA 302,Biostatistics,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 322,Plant Growth and Development,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 321,Fundamentals of Entomology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,ZOOL 330,Animal Physiology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,CHEM 344,Environmental Chemistry I,Prof. Stanley Kagwanja,3,1,Core,0
Bachelor of Science,CHEM 342,Atomic Spectroscopy,Prof. Ombaka Ochieng,3,1,Core,0
Bachelor of Science,CHEM 332,Organic Chemistry III,Prof. Eric Njagi,3,1,Core,0
Bachelor of Science,CHEM 314,Bioinorganic Chemistry,Prof. Stanley Kagwanja,3,1,Core,0
Bachelor of Science,CHEM 343,Industrial and Applied Chemistry I,Prof. Eric Njagi,3,1,Core,0
Bachelor of Science,CHEM 333,Chemistry of Lipids,Physical Science Dept,3,1,Core,0
Bachelor of Science,BOTA 303,Bryology and Pteridology,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 342,Economic Botany,Biological Sci Dept,3,1,Core,0
Bachelor of Science,BOTA 352,Principles of Crop Protection,Biological Sci Dept,3,1,Core,0
Bachelor of Science,PHYS 317,Mathematical Physics I,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,PHYS 325,Physics Practicals V,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,MATH 325,Fluid Mechanics I,Dr. Jacob Kirimi,3,1,Core,0
Bachelor of Science,PHYS 392,Biophysics,Dr. Martin Mutie,3,1,Core,0
Bachelor of Science,PHYS 336,Quantum Mechanics I,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,PHYS 342,Electricity & Magnetism II,Dr. Elosy Gatakaa,3,1,Core,0
Bachelor of Science,EPHY 315,Principles of Measurement Systems II,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,EPHY 316,Principles of Design,Dr. William Ndeke,3,1,Core,0
Bachelor of Science,EPHY 321,Applied Industrial & Applied Chemistry I,Dr. Elosy Gatakaa,3,1,Core,0
Bachelor of Science,CHEM 342,Environmental Chemistry I,Dr. Elosy Gatakaa,3,1,Core,0
Bachelor of Science,EPHY 343,Statics and Strength of Materials,Dr. Martin Mutie,3,1,Core,0
Bachelor of Science,EPHY 352,Electrical Networks II,Dr. Martin Mutie,3,1,Core,0
Bachelor of Science,COSC 322,Unix and C Programming,Comp. Sci Dept,3,1,Core,0
Bachelor of Science,EPHY 343,Statics and Strength of Materials,Dr. Martin Mutie,3,1,Core,0
Bachelor of Science,EPHY 321,Applied Industrial & Applied Chemistry I,Dr. Elosy Gatakaa,3,1,Core,0
Bachelor of Science,EPHY 322,Applied Industrial & Applied Chemistry I,Dr. Elosy Gatakaa,3,1,Core,0
Bachelor of Science,CHEM 342,Environmental Chemistry I,Dr. Elosy Gatakaa,3,1,Core,0
Bachelor of Science,EPHY 343,Statics and Strength of Materials,Dr. Martin Mutie,3,1,Core,0
Bachelor of Science,EPHY 352,Electrical Networks II,Dr. Martin Mutie,3,1,Core,0
Bachelor of Science,COSC 322,Unix and C Programming,Comp. Sci Dept,3,1,Core,0
Bachelor of Science,EPHY 343,Statics and Strength of Materials,Dr. Martin Mutie,3,1,Core,0
Bachelor of Science,MATH 302,Real Analysis I,Ms. Teresa Mbothia,3,1,Core,0
Bachelor of Science,MATH 344,Theory of Estimation,Dr. Elizabeth Njoroge,3,1,Core,0
Bachelor of Science,MATH 342,Quality Control Methods,Prof. Dennis Muriithi,3,1,Core,0
Bachelor of Science,MATH 452,Tests of Hypothesis,Prof. Dennis Muriithi,4,1,Core,0
Bachelor of Science,MATH 443,Design & Analysis of Experiments I,Dr. Elizabeth Njoroge,4,1,Core,0
Bachelor of Science,MATH 453,Applied Regression Analysis I,Ms. Olivia Waka,4,1,Core,0
Bachelor of Science,MATH 412,Non-Parametric Methods,Mr. Emmanuel Koech,4,1,Core,0
Bachelor of Science,MATH 452,Measure & Probability,Mr. Emmanuel Koech,4,1,Core,0
Bachelor of Science,MATH 458,Categorical Data Analysis,Mr. Emmanuel Koech,4,1,Core,0
Bachelor of Science,MATH 456,Decision theory and Bayesian Inference II,Mr. Emmanuel Koech,4,1,Core,0
Bachelor of Science,ZOOL 451,Research Project in Zoology I,Biological Science Dept,4,1,Core,0
Bachelor of Science,ZOOL 443,Immunology & Immunopathology,Biological Science Dept,4,1,Core,0
Bachelor of Science,ZOOL 410,Ichthyology,Biological Science Dept,4,1,Core,0
Bachelor of Science,BOTA 482,Research Project in Botany I,Biological Science Dept,4,1,Core,0
Bachelor of Science,BOTA 474,Plant Physiology II,Biological Science Dept,4,1,Core,0
Bachelor of Science,BOTA 435,Plant Virology,Biological Science Dept,4,1,Core,0
Bachelor of Science,BOTA 424,Pesticides,Biological Science Dept,4,1,Core,0
Bachelor of Science,BOTA 412,Plant Biotechnology,Biological Science Dept,4,1,Core,0
Bachelor of Science,BOTA 412,Plant Biotechnology,Biological Science Dept,4,1,Core,0
Bachelor of Science,CHEM 426,Statistical Thermodynamics,Biological Science Dept,4,1,Core,0
Bachelor of Science,CHEM 448,Environmental Chemistry II,Biological Science Dept,4,1,Core,0
Bachelor of Science,CHEM 447,Industrial and Applied Chemistry II,Biological Science Dept,4,1,Core,0
Bachelor of Science,CHEM 462,Research Project I (Core),Biological Science Dept,4,1,Core,0
Bachelor of Science,CHEM 446,Chemistry of Natural Products,Biological Science Dept,4,1,Core,0
Bachelor of Science,CHEM 417,Radiation And Nuclear Chemistry,Biological Science Dept,4,1,Core,0
Bachelor of Science,CHEM 417,Radiation And Nuclear Chemistry,Biological Science Dept,4,1,Core,0
Bachelor of Science,CHEM 436,Advanced Stereochemistry and Reaction Mechanisms,Biological Science Dept,4,1,Core,0
Bachelor of Science,CHEM 419,Chemistry of Transition Metal Elements,Biological Science Dept,4,1,Core,0
Bachelor of Science,BOTA 453,Plant Pathology,Biological Science Dept,4,1,Core,0
Bachelor of Science,BOTA 473,Plant Biochemistry,Biological Science Dept,4,1,Core,0
Bachelor of Science,ZOOL 430,Comparative Animal Physiology,Biological Science Dept,4,1,Core,0
Bachelor of Science,ZOOL 440,Protozoology,Biological Science Dept,4,1,Core,0
Bachelor of Science,MATH 491,Project In Mathematics I (Core),ALL,4,1,Core,0
Bachelor of Science,MATH 411,Differential Geometry,Dr. Sammy Musundi,4,1,Core,0
Bachelor of Science,MATH 405,Algebra II,Ms. Teresa Mbothia,4,1,Core,0
Bachelor of Science,MATH 408,Number Theory,Prof. Mark Okongo,4,1,Core,0
Bachelor of Science,MATH 407,Fourier Analysis,Dr. Alice Lunani,4,1,Core,0
Bachelor of Science,MATH 423,Numerical Analysis II,Dr. Alice Lunani,4,1,Core,0
Bachelor of Science,MATH 425,Fluid Mechanics II,Prof. Mark Okongo,4,1,Core,0
Bachelor of Science,MATH 428,Mathematical Modelling,Physical Science Dept,4,1,Core,0
Bachelor of Science,MATH 452,Test of Hypothesis,Prof. Dennis Muriithi,4,1,Core,0
Bachelor of Science,MATH 443,Design and Analysis of Experiments I,Dr. Elizabeth Njoroge,4,1,Core,0
Bachelor of Science,MATH 451,Non-Parametric Methods,Prof. Dennis Muriithi,4,1,Core,0
Bachelor of Science,MATH 449,Probability Theory,Prof. Dennis Muriithi,4,1,Core,0
Bachelor of Science,MATH 441,Sampling Methods II,Mr. Teddy Mungai,4,1,Core,0
Bachelor of Science,MATH 441,Sampling Methods II,Mr. Teddy Mungai,4,1,Core,0
Bachelor of Science,MATH 449,Probability Theory,Prof. Dennis Muriithi,4,1,Core,0
Bachelor of Science,MATH 451,Non-Parametric Methods,Prof. Dennis Muriithi,4,1,Core,0
Bachelor of Science,MATH 443,Design and Analysis of Experiments I,Dr. Elizabeth Njoroge,4,1,Core,0
Bachelor of Science,MATH 452,Test of Hypothesis,Prof. Dennis Muriithi,4,1,Core,0
Bachelor of Science,MATH 428,Mathematical Modelling,Prof. Mark Okongo,4,1,Core,0
Bachelor of Science,MATH 425,Fluid Mechanics II,Prof. Mark Okongo,4,1,Core,0
Bachelor of Science,MATH 423,Numerical Analysis II,Dr. Alice Lunani,4,1,Core,0
Bachelor of Science,MATH 407,Fourier Analysis,Prof. Mark Okongo,4,1,Core,0
Bachelor of Science,MATH 408,Number Theory,Prof. Mark Okongo,4,1,Core,0
Bachelor of Science,MATH 405,Algebra II,Ms. Teresa Mbothia,4,1,Core,0
Bachelor of Science,MATH 411,Differential Geometry,Dr. Sammy Musundi,4,1,Core,0
Bachelor of Science,MATH 491,Project in Mathematics I (Core),ALL,4,1,Core,0
Bachelor of Science,PHYS 427,Quantum Mechanics II,Dr. William Ndeke,4,1,Core,0
Bachelor of Science,PHYS 437,Physics Practical VI,Dr. Elosy Gatakaa,4,1,Core,0
Bachelor of Science,PHYS 484,Atomic and Nuclear Physics,Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,PHYS 483,Solid State Physics,Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,PHYS 485,Environmental and Renewable Energy Physics,Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,PHYS 475,Communications Electronics II,Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,PHYS 474,Microprocessor I,Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,PHYS 494,Applied Geophysics,Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,PHYS 416,Physics project I (Core),Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,PHYS 427,Quantum Mechanics II,Dr. William Ndeke,4,1,Core,0
Bachelor of Science,PHYS 437,Physics Practical VI,Dr. Elosy Gatakaa,4,1,Core,0
Bachelor of Science,PHYS 484,Atomic and Nuclear Physics,Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,PHYS 483,Solid State Physics,Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,PHYS 485,Environmental and Renewable Energy Physics,Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,PHYS 475,Communications Electronics II,Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,PHYS 474,Microprocessor I,Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,PHYS 494,Applied Geophysics,Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,PHYS 416,Physics project I (Core),Dr. Martin Mutie,4,1,Core,0
Bachelor of Science,ACMT 301,Life Contingencies I,Ms. Elizabeth Magero,3,1,Core,0
Bachelor of Science,MATH 346,Statistical Inference I,Social Sciences Dept,3,1,Core,0
Bachelor of Science,ECON 313,Advanced Microeconomics Theory,Dr. Jacob Kirimi,3,1,Core,0
Bachelor of Science,MATH 322,Ordinary Differential Equations I,Dr. Alice Lunani,3,1,Core,0
Bachelor of Science,MATH 321,Calculus III,Dr. Alice Lunani,3,1,Core,0
"""

LECTURER_CSV_TEXT = """\
payroll_number,name,email,designation,department,user,user_details
CHU/0001,Erastus Nyaga Njoka,erastus.njoka@chuka.ac.ke,Prof,Animal Science,,No user linked
CHU/0002,Stanley M. Kagwanja,stanley.kagwanja@chuka.ac.ke,Prof,Physical Sciences,,No user linked
CHU/0003,Zachary Njogu Waita,zachary.waita@chuka.ac.ke,Prof,Humanities,,No user linked
CHU/0004,Andrew Thiuru Muguna,andrew.muguna@chuka.ac.ke,Prof,Business Administration,,No user linked
CHU/0005,Grace N Ngigi,grace.ngigi@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0006,Samson Guantai Raiji,samson.raiji@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0007,Jafford Njeru Rithaa,jafford.rithaa@chuka.ac.ke,Prof,Environmental Science & Resources Development,,No user linked
CHU/0009,Tabitha Maugi Mbungu,tabitha.mbungu@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0010,Eric Mwenda Elias,eric.elias@chuka.ac.ke,Prof,Education,,No user linked
CHU/0011,David Nyaga Bururia,david.bururia@chuka.ac.ke,Prof,Humanities,,No user linked
CHU/0012,Lenity Kananu Maugu,lenity.maugu@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0013,Agnes Wamai Wamuyu,agnes.wamuyu@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0014,David Gitonga Mwathi,david.mwathi@chuka.ac.ke,Prof,Computer Science,,No user linked
CHU/0015,Moses Kathuri Njeru,moses.kathuri@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0016,Anthony Mbugua Ngereki,anthony.ngereki@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0017,Isaac Micheni Nkari,isaac.nkari@chuka.ac.ke,Prof,Business Administration,,No user linked
CHU/0018,Onesmus Munene Nderi,onesmus.nderi@chuka.ac.ke,Dr,Animal Science,,No user linked
CHU/0019,David Mwenda Nyaga,david.mwenda@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0020,Lewis Kinyua Kathuni,lewis.kathuni@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0021,Joel Mwangi Gichumbi,joel.gichumbi@chuka.ac.ke,Prof,Physical Sciences,,No user linked
CHU/0022,Edward Silas Njagi,edward.njagi@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0023,Enock Seme Matundura,enock.matundura@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0024,Ochieng Ombaka,ochieng.ombaka@chuka.ac.ke,Prof,Physical Sciences,,No user linked
CHU/0025,Lucy Kirigo Mureu,lucy.mureu@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0026,Duncan Nyang'Ara,duncan.nyangara@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0027,Benjamin Kanga Mugambi,benjamin.mugambi@chuka.ac.ke,Mr,Education,,No user linked
CHU/0028,Elizabeth Wambui Njoroge,elizabeth.njoroge@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0029,Charles Kinyua Gitonga,charles.gitonga@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0030,Jackson Gikunda Njogu,jackson.njogu@chuka.ac.ke,Mr,Humanities,,No user linked
CHU/0031,Gilbert Odilla Abura,gilbert.abura@chuka.ac.ke,Dr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0032,Joyline Muchiri Mugero,joyline.mugero@chuka.ac.ke,Dr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0033,Samson Njogu Muriuki,samson.njogu@chuka.ac.ke,Mr,Physical Sciences,,No user linked
CHU/0035,Henry Nabea Nkoru,henry.nkoru@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0036,Mugenda Nebat Galo,mugenda.galo@chuka.ac.ke,Dr,Management Science,,No user linked
CHU/0037,Kenneth Mutuiri Nthuni,kenneth.nthuni@chuka.ac.ke,Mr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0038,Paul Kuria Kamweru,paul.kamweru@chuka.ac.ke,Prof,Physical Sciences,,No user linked
CHU/0039,Susan Muthoni Kinyua,susan.kinyua@chuka.ac.ke,Dr,Education,,No user linked
CHU/0040,Sammy Musundi Wabomba,sammy.wabomba@chuka.ac.ke,Prof,Physical Sciences,,No user linked
CHU/0041,Dickson Nkonge Kagema,dickson.kagema@chuka.ac.ke,Prof,Humanities,,No user linked
CHU/0042,Thomas Mochoge Motindi,thomas.motindi@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0043,Henry Kimathi Mukaria,henry.mukaria@chuka.ac.ke,Mr,Management Science,,No user linked
CHU/0044,Eston Kamau Warui,eston.warui@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0045,Shelmith Wanja Munyiri,shelmith.munyiri@chuka.ac.ke,Prof,Plant Science,,No user linked
CHU/0046,Rael Mwirigi Nkatha,rael.nkatha@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0047,Harun Ngugi Njenga,harun.njenga@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0048,John Kobia Mwithalii,john.mwithalii@chuka.ac.ke,Prof,Humanities,,No user linked
CHU/0049,Anne Jerotich Garry Michura,anne.michura@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0050,Charles Mbogo Kariuki,charles.kariuki@chuka.ac.ke,Dr,Animal Science,,No user linked
CHU/0051,Noel Otiende Uside,noel.uside@chuka.ac.ke,Mr,Education,,No user linked
CHU/0052,Alice Murwayi Lunani,alice.lunani@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0053,Colomba Kaburi Muriungi,colomba.muriungi@chuka.ac.ke,Prof,Humanities,,No user linked
CHU/0054,Fredrick Mugambi Muthengi,fredrick.muthengi@chuka.ac.ke,Dr,Computer Science,,No user linked
CHU/0055,Adiel Micheni Magana,adiel.magana@chuka.ac.ke,Prof,Biological Sciences,,No user linked
CHU/0056,Lucy Gitonga Kawira,lucy.kawira@chuka.ac.ke,Ms,Public Health,,No user linked
CHU/0057,Kibetu Dickson Kinoti,kibetu.kinoti@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0058,Lucy Nyambura Kiriungi,lucy.kiriungi@chuka.ac.ke,Ms,Education,,No user linked
CHU/0059,Job Mulati Chebai,job.chebai@chuka.ac.ke,Mr,Humanities,,No user linked
CHU/0060,Kennedy O Moenga,kennedy.moenga@chuka.ac.ke,Mr,Management Science,,No user linked
CHU/0061,Caroline Mutunga Ndunge,caroline.ndunge@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0063,Christine Atieno Peter,christine.peter@chuka.ac.ke,Prof,Humanities,,No user linked
CHU/0064,Anita Mwende Mutegi,anita.mutegi@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0065,Catherine Kathure Kaimenyi,catherine.kaimenyi@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0066,Anne Andayi Sande,anne.sande@chuka.ac.ke,Prof,Social Sciences,,No user linked
CHU/0067,Jane Kananu Kiruki,jane.kiruki@chuka.ac.ke,Dr,Computer Science,,No user linked
CHU/0068,Daniel Ngochi Kinyanjui,daniel.kinyanjui@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0069,Beatrice M Mburugu,beatrice.mburugu@chuka.ac.ke,Prof,Social Sciences,,No user linked
CHU/0070,Carolyne Anaye Omukoko,carolyne.omukoko@chuka.ac.ke,Dr,Plant Science,,No user linked
CHU/0071,John Mutisya Mutua,john.mutua@chuka.ac.ke,Mr,Management Science,,No user linked
CHU/0072,Peris Wangari Nderitu,peris.nderitu@chuka.ac.ke,Dr,Plant Science,,No user linked
CHU/0073,Christopher Sikuku Mutuku,christopher.mutuku@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0074,Eric Chomba Njagi,eric.njagi@chuka.ac.ke,Prof,Physical Sciences,,No user linked
CHU/0075,Dennis Kariuki Muriithi,dennis.muriithi@chuka.ac.ke,Prof,Physical Sciences,,No user linked
CHU/0076,Bernard Cheruiyot Soi,bernard.soi@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0077,Christopher Nkonge Kiboro,christopher.kiboro@chuka.ac.ke,Prof,Social Sciences,,No user linked
CHU/0078,Joseph Maina Kariuki,joseph.maina@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0079,Agatha Mutio Nthenge,agatha.nthenge@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0080,Grace M Akenga,grace.akenga@chuka.ac.ke,Ms,Management Science,,No user linked
CHU/0081,Zipporah Wanjiku Muthui,zipporah.muthui@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0082,Jacob Ngai Kirimi,jacob.kirimi@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0083,Stephen M'Kiunga Kainga,stephen.kainga@chuka.ac.ke,Mr,Public Health,,No user linked
CHU/0084,Benedict Mutina Maluni,benedict.maluni@chuka.ac.ke,Mr,Education,,No user linked
CHU/0085,James Maina Mwangi,james.maina@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0086,James Mwenda Murungi,james.murungi@chuka.ac.ke,Mr,Education,,No user linked
CHU/0087,Martha Wanjiru Muraya,martha.muraya@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0088,Raphael Mwiti Gikunda,raphael.gikunda@chuka.ac.ke,Dr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0089,Peter Kimanthi Mbaka,peter.mbaka@chuka.ac.ke,Dr,Education,,No user linked
CHU/0090,Geofrey Kingori Gathungu,geofrey.gathungu@chuka.ac.ke,Dr,Plant Science,,No user linked
CHU/0091,Nancy Wangui Mbaka,nancy.mbaka@chuka.ac.ke,Prof,Humanities,,No user linked
CHU/0092,Mark Onyango Okongo,mark.okongo@chuka.ac.ke,Prof,Physical Sciences,,No user linked
CHU/0093,Bernard Ong'Era Osero,bernard.oseroh@chuka.ac.ke,Dr,Computer Science,,No user linked
CHU/0094,Koech Peter Kiplang'At,koech.peter@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0095,John Mwithali Kamoyo,john.kamoyo@chuka.ac.ke,Dr,Education,,No user linked
CHU/0096,Lilian Makena Mugambi,lilian.makena@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0097,Gilbert Mugambi Miriti,gilbert.miriti@chuka.ac.ke,Prof,Business Administration,,No user linked
CHU/0098,Grace Opetu Abucheli,grace.abucheli@chuka.ac.ke,Prof,Plant Science,,No user linked
CHU/0099,William Ndungu Mwangi,william.mwangi@chuka.ac.ke,Mr,Management Science,,No user linked
CHU/0100,Olivia Njiri Adhiambo,olivia.adhiambo@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0101,Lucas Chege Waweru,lucas.waweru@chuka.ac.ke,Mr,Management Science,,No user linked
CHU/0102,Annah Njoki Ngeretha,annah.ngeretha@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0103,Martin Warutere,martin.warutere@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0104,Emily Gakii Murerwa,emily.murerwa@chuka.ac.ke,Ms,Computer Science,,No user linked
CHU/0105,Domisiano Koome Impwi,domisiano.impwi@chuka.ac.ke,Mr,Public Health,,No user linked
CHU/0106,Joshua Kabugi Mwangi,joshua.kabugi@chuka.ac.ke,Mr,Public Health,,No user linked
CHU/0107,Willy Rankesh Mutisya,willy.mutisya@chuka.ac.ke,Mr,Public Health,,No user linked
CHU/0108,Mercy Wanja Njagi,mercy.njagi@chuka.ac.ke,Dr,Education,,No user linked
CHU/0109,Caroline Mucece Kithinji,caroline.kithinji@chuka.ac.ke,Prof,Humanities,,No user linked
CHU/0110,Eunice Wamuyu Githae,eunice.githae@chuka.ac.ke,Prof,Biological Sciences,,No user linked
CHU/0111,Moses Mahugu Muraya,moses.muraya@chuka.ac.ke,Prof,Plant Science,,No user linked
CHU/0112,Joseph Masinde Wabwire,joseph.wabwire@chuka.ac.ke,Dr,Management Science,,No user linked
CHU/0113,Nelson Oluoch Jagero,nelson.jagero@chuka.ac.ke,Prof,Education,,No user linked
CHU/0114,Crispin Ong'Era Isaboke,crispin.isaboke@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0115,Stephen Kairu Wambugu,stephen.wambugu@chuka.ac.ke,Prof,Social Sciences,,No user linked
CHU/0116,Edward Odera Okana,edward.okana@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0117,Allan Mwangi Njoki,allan.njoki@chuka.ac.ke,Mr,Humanities,,No user linked
CHU/0118,Silas Kiruki,silas.kiruki@chuka.ac.ke,Prof,Physical Sciences,,No user linked
CHU/0119,Kenneth Otula Sigar,kenneth.sigar@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0120,Jane Mumbi Thuita,jane.thuita@chuka.ac.ke,Ms,Management Science,,No user linked
CHU/0121,Pauline Nyokabi Kamau,pauline.kamau@chuka.ac.ke,Ms,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0122,Henry Nyabuto Otiso,henry.otiso@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0123,Margaret Akinyi Olang',margaret.olang@chuka.ac.ke,Ms,Management Science,,No user linked
CHU/0124,Geoffrey Kipruto Kosgei,geoffrey.kosgei@chuka.ac.ke,Mr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0125,Aggrey Otieno Bunde,aggrey.bunde@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0126,Miriam Thogori Nyambura,miriam.nyambura@chuka.ac.ke,Dr,Management Science,,No user linked
CHU/0127,Paul Njoroge Muiru,paul.muiru@chuka.ac.ke,Mr,Humanities,,No user linked
CHU/0128,Edwin Mwenda,edwin.mwenda@chuka.ac.ke,Mr,Physical Sciences,,No user linked
CHU/0129,Humphrey Kirimi Ireri,humphrey.ireri@chuka.ac.ke,Prof,Humanities,,No user linked
CHU/0130,William Murithi Ndeke,william.ndege@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0131,Martin Mule Mutie,martin.mutie@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0132,Antony Odek,antony.odek@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0133,Bramuel Muyela,bramuel.muyela@chuka.ac.ke,Mr,Physical Sciences,,No user linked
CHU/0134,George Manono Areri,george.manono@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0135,Jane Njeri Mukuha,jane.mukuha@chuka.ac.ke,Ms,Humanities,,No user linked
CHU/0136,James Muita Kinyua,james.kinyua@chuka.ac.ke,Dr,Animal Science,,No user linked
CHU/0137,Patrick Mutwiri Karitu,patrick.karitu@chuka.ac.ke,Mr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0138,Bornace Jepkorir Kimeli,bornace.kimeli@chuka.ac.ke,Mr,Education,,No user linked
CHU/0139,Miriam Wamaitha Thuo,miriam.thuo@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0140,Mary Nyambura Gichure,mary.gichure@chuka.ac.ke,Dr,Animal Science,,No user linked
CHU/0142,Kenneth Kigundu Macharia,kenneth.macharia@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0143,Edna Chebet Too,edna.too@chuka.ac.ke,Dr,Computer Science,,No user linked
CHU/0144,Haggai Onyango Ndukhu,haggai.ndukhu@chuka.ac.ke,Dr,Plant Science,,No user linked
CHU/0145,David Chebutia Kemboi,david.kemboi@chuka.ac.ke,Dr,Animal Science,,No user linked
CHU/0146,Catherine Wairimu Thiong'O,catherine.thiongo@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0147,Keith Kiswili Julius,keith.kiswili@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0148,Stephen Wachira Kariuki,stephen.wachira@chuka.ac.ke,Mr,Food Technology,,No user linked
CHU/0149,Kevin Otieno Gogo,kevin.gogo@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0150,Roseline Kafedha Kahindi,roseline.kahindi@chuka.ac.ke,Dr,Animal Science,,No user linked
CHU/0151,Justin Mugendi Njeru,justin.njeru@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0152,Annette Mukami Njue,annette.njue@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0153,Virginia Kavuu Muia,virginia.muia@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0154,David M Mbuba,david.mbuba@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0155,Grace Gatune Murithi,grace.murithi@chuka.ac.ke,Prof,Education,,No user linked
CHU/0156,Monica Buyatsi Oundo,monica.oundo@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0157,Brian Rotich Kanyongi,brian.kanyongi@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0158,Charity Nyaboke Onsinyo,charity.onsinyo@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0159,Fredrick Gogo Adol,fredrick.adol@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0160,Peter Fundi Njagi,peter.fundi@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0161,John Mwaura Ireri,john.ireri@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0162,Joyce Mghoi Macharia,joyce.mghoi@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0163,Dorothy Muthoni Mbaya,dorothy.mbaya@chuka.ac.ke,Ms,Public Health,,No user linked
CHU/0164,Winfred Kendi Mutwiri,winfred.mutwiri@chuka.ac.ke,Ms,Biological Sciences,,No user linked
CHU/0165,Christine Weveti Kinyua,christine.kinyua@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0166,Chrispus Mwakazi Mwakundia,chrispus.mwakundia@chuka.ac.ke,Mr,Humanities,,No user linked
CHU/0167,Elosy Gatakaa Njeru,elosy.njeru@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0168,Margaret Nyaruiru Mugure,margaret.mugure@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0170,Francis Gichuho Irungu,francis.irungu@chuka.ac.ke,Dr,Food Technology,,No user linked
CHU/0171,Joy Debora Orwa,joy.orwa@chuka.ac.ke,Dr,Food Technology,,No user linked
CHU/0172,Johnson Kyalo Mwove,johnson.mwove@chuka.ac.ke,Dr,Food Technology,,No user linked
CHU/0173,Dennis Mosoti,dennis.mosoti@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0174,Nancy Karinthoni Mustafa,nancy.mustafa@chuka.ac.ke,Ms,Business Administration,,No user linked
CHU/0176,Solomon Mosomi Ogachi,solomon.ogachi@chuka.ac.ke,Mr,Public Health,,No user linked
CHU/0177,Willy Kahanya Kiboi,willy.kiboi@chuka.ac.ke,Dr,Public Health,,No user linked
CHU/0178,Eugine Sundays Mukhwana,eugine.mukhwana@chuka.ac.ke,Dr,Public Health,,No user linked
CHU/0179,Mary Wambui Wacuka,mary.wacuka@chuka.ac.ke,Ms,Education,,No user linked
CHU/0180,Joyce Wangui Njoki,joyce.njoki@chuka.ac.ke,Ms,Food Technology,,No user linked
CHU/0181,Peter Kingori Gakai,peter.gakai@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0182,Edwin Muchomba Kiria,edwin.kiria@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0183,Onesmus Gitonga Ntiba,onesmus.ntiba@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0184,Peter Gituma Kimathi,peter.gituma@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0185,Monica Gakii Ituma,monica.ituma@chuka.ac.ke,Dr,Education,,No user linked
CHU/0186,Nyangau Dynesius,nyangau.dynesius@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0187,Hannah Wanjiku Kangara,hannah.kangara@chuka.ac.ke,Dr,Education,,No user linked
CHU/0188,Richard Guto,richard.guto@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0189,Hellen Kagwiria Orina,hellen.orina@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0190,Joshua Ngacha Weru,joshua.weru@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0191,Dave Mwangi Ireri,dave.ireri@chuka.ac.ke,Mr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0192,Pauline Kananu Micheni,pauline.micheni@chuka.ac.ke,Dr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0193,Nyariki Kiprop Samoita Ibrahim,nyariki.samoita@chuka.ac.ke,Mr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0194,Teresa Muthoni Thuita,teresa.thuita@chuka.ac.ke,Ms,Business Administration,,No user linked
CHU/0195,Caroline Khasoha Shikuku,caroline.shikuku@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0196,Tabitha Kavuli Itotia,tabitha.itotia@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0197,Simon Mburu Wambui,simon.wambui@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0198,Chris Enidy Boera,chris.boera@chuka.ac.ke,Mr,Education,,No user linked
CHU/0199,Daniel Muthee Gaichu,daniel.gaichu@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0200,Mary Kanyua Njeru,mary.njeru@chuka.ac.ke,Ms,Humanities,,No user linked
CHU/0201,Elizabeth Wangai Njiru,elizabeth.njiru@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0202,Evans Mutuma,evans.mutuma@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0203,John Otieno Ogembo,john.ogembo@chuka.ac.ke,Dr,Education,,No user linked
CHU/0204,Julian Jepkemoi Chepkonga,julian.chepkonga@chuka.ac.ke,Ms,Management Science,,No user linked
CHU/0205,Joseph Musyoki Mbuvi,joseph.mbuvi@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0206,Antony Mukasa Mate,antony.mate@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0207,Purity Wanja,purity.wanja@chuka.ac.ke,Ms,Humanities,,No user linked
CHU/0208,Faith Muthoni Nyaga,faith.nyaga@chuka.ac.ke,Ms,Humanities,,No user linked
CHU/0209,Grace Njeri Kamau,grace.kamau@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0210,Linet Kamene Mutua,linet.mutua@chuka.ac.ke,Ms,Education,,No user linked
CHU/0211,Joab Mwange Ifedha,joab.ifedha@chuka.ac.ke,Mr,Humanities,,No user linked
CHU/0212,Hanningtone Sitati,hanningtone.sitati@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0213,Claudius P. Kihara,claudius.kihara@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0214,Dominic Kiragu Mureithi,dominic.mureithi@chuka.ac.ke,Dr,Animal Science,,No user linked
CHU/0215,Purity Kananu Mwongera,purity.mwongera@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0216,Shadrack Kirimi Nyagah,shadrack.nyagah@chuka.ac.ke,Mr,Humanities,,No user linked
CHU/0217,Erick K. Koech,erick.koech@chuka.ac.ke,Mr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0218,Beth Wanjira Gichobi,beth.gichobi@chuka.ac.ke,Ms,Public Health,,No user linked
CHU/0219,Alex Mwirigi Kinyua,alex.kinyua@chuka.ac.ke,Mr,Humanities,,No user linked
CHU/0220,Joshua Mulele Machayo,joshua.machayo@chuka.ac.ke,Mr,Physical Sciences,,No user linked
CHU/0221,Richard Kariuki Muya,richard.muya@chuka.ac.ke,Mr,Physical Sciences,,No user linked
CHU/0222,Charles Kibet Kiptum,charles.kiptum@chuka.ac.ke,Mr,Education,,No user linked
CHU/0223,Abel Bennet Holla,abel.holla@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0224,Teddy Mutugi Wanjuki,teddy.wanjuki@chuka.ac.ke,Mr,Physical Sciences,,No user linked
CHU/0225,Scholastica Twili Nzomo,scholastica.nzomo@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0226,Boniface Munene Rufo,boniface.rufo@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0227,Sumukwo Chesang,sumukwo.chesang@chuka.ac.ke,Mr,Animal Science,,No user linked
CHU/0228,Robinson Kenyatta Jacob,robinson.jacob@chuka.ac.ke,Mr,Education,,No user linked
CHU/0229,Deborah Kangai,deborah.kangai@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0230,John Kihiu Magothe,john.kihiu@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0232,Julius Karanja Maina,julius.karanja@chuka.ac.ke,Mr,Animal Science,,No user linked
CHU/0233,Kenkelvin Kimathi Mbaka,kenkelvin.mbaka@chuka.ac.ke,Mr,Education,,No user linked
CHU/0234,Catherine Nkirote Gichunge,catherine.gichunge@chuka.ac.ke,Ms,Public Health,,No user linked
CHU/0235,Kyalo Wa Ngula,kyalo.ngula@chuka.ac.ke,Prof,Humanities,,No user linked
CHU/0236,James Kirimi Kiramana,james.kiramana@chuka.ac.ke,Dr,Plant Science,,No user linked
CHU/0237,Lucy Mutare Mathai,lucy.mathai@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0238,Allan Mugambi,allan.mugambi@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0239,Anjeline Ndele Wambua,anjeline.wambua@chuka.ac.ke,Ms,Physical Sciences,,No user linked
CHU/0240,Dorcas Muendi Musyimi,dorcas.musyimi@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0241,David Mbabu Nchunge,david.nchunge@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0242,Eric Kimani Kuria,eric.kuria@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0243,Jonathan Kitheka Kathenge,jonathan.kathenge@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0244,Augustus Onchari Nyakundi,augustus.nyakundi@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0245,Jane Wanjiku Gitau,jane.gitau@chuka.ac.ke,Ms,Plant Science,,No user linked
CHU/0246,Irene Atieno Omolo,irene.omolo@chuka.ac.ke,Ms,Public Health,,No user linked
CHU/0247,Reuben Nahashon Angachi,reuben.angachi@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0248,Elizabeth Mutete Mutunga,elizabeth.mutunga@chuka.ac.ke,Ms,Public Health,,No user linked
CHU/0249,Cornellius Musembi Muendo,cornellius.muendo@chuka.ac.ke,Dr,Public Health,,No user linked
CHU/0250,Kennedy Kemboi Chumar,kennedy.chumar@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0251,Edwin Nyang'Wara Omosa,edwin.omosa@chuka.ac.ke,Dr,Engineering,,No user linked
CHU/0252,Elizabeth Anyango Magero,elizabeth.magero@chuka.ac.ke,Ms,Physical Sciences,,No user linked
CHU/0253,Njagi Jackin Nanua,njagi.nanua@chuka.ac.ke,Prof,Food Technology,,No user linked
CHU/0254,Joseph Muema Kavulya,joseph.kavulya@chuka.ac.ke,Prof,Social Sciences,,No user linked
CHU/0255,Edwin Ondari Misati,edwin.misati@chuka.ac.ke,Mr,Physical Sciences,,No user linked
CHU/0256,James Peter Wainaina,james.wainaina@chuka.ac.ke,Mr,Humanities,,No user linked
CHU/0257,Lucy Karimi Njagi,lucy.njagi@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0258,Livan Njeru,livan.njeru@chuka.ac.ke,Mr,Humanities,,No user linked
CHU/0259,Saif Kinyori,saif.kinyori@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0260,Abdirizak Roba Duba,abdirizak.duba@chuka.ac.ke,Mr,Law,,No user linked
CHU/0261,Mercy Nkinga Kariuki,mercy.kariuki@chuka.ac.ke,Ms,Education,,No user linked
CHU/0262,Mary Mukwairu Mugambi,mary.mukwairu@chuka.ac.ke,Ms,Education,,No user linked
CHU/0263,Samuel Maina Mwai,samuel.mwai@chuka.ac.ke,Mr,Law,,No user linked
CHU/0264,Josephat Machoka Bundi,josephat.bundi@chuka.ac.ke,Dr,Engineering,,No user linked
CHU/0265,Allan Oduor Awour,allan.awour@chuka.ac.ke,Mr,Law,,No user linked
CHU/0266,Duncan Mutongu Maina,duncan.maina@chuka.ac.ke,Mr,Public Health,,No user linked
CHU/0267,Elizabeth Namuki Sisenda,elizabeth.sisenda@chuka.ac.ke,Mr,Law,,No user linked
CHU/0268,Peter Mutegi Kamunyu,peter.kamunyu@chuka.ac.ke,Mr,Engineering,,No user linked
CHU/0269,Wycliffe Nyachoti Otiso,wycliffe.otiso@chuka.ac.ke,Dr,Law,,No user linked
CHU/0270,Juster Nthinga Mungiria,juster.mungiria@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0271,Erick V. Onyango Fwaya,erick.fwaya@chuka.ac.ke,Prof,Environmental Science & Resources Development,,No user linked
CHU/0272,Marcel Odhiambo Ohanga,marcel.ohanga@chuka.ac.ke,Prof,Engineering,,No user linked
CHU/0273,Grace Karimi Njiru,grace.njiru@chuka.ac.ke,Ms,Law,,No user linked
CHU/0274,Gibson Gisore Nyamato,gibson.nyamato@chuka.ac.ke,Mr,Law,,No user linked
CHU/0275,Antony Mwenda Kinyua,antony.kinyua@chuka.ac.ke,Mr,Law,,No user linked
CHU/0276,John Karauri Mbaka,john.mbaka@chuka.ac.ke,Mr,Education,,No user linked
CHU/0277,Rose Anyango Jakinda,rose.jakinda@chuka.ac.ke,Ms,Education,,No user linked
CHU/0278,Kipkirui Rotich,kipkirui.rotich@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0279,Janet Wanja Nyaga,janet.nyaga@chuka.ac.ke,Ms,Business Administration,,No user linked
CHU/0280,Geoffrey Mwikamba Bita,geoffrey.bita@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0281,Peris Wanjiku Mwangi,peris.wanjiku@chuka.ac.ke,Ms,Public Health,,No user linked
CHU/0282,Geoffrey Karanja Muiruri,geoffrey.muiruri@chuka.ac.ke,Mr,Public Health,,No user linked
CHU/0283,Antony Murithi Gitonga,antony.gitonga@chuka.ac.ke,Mr,Public Health,,No user linked
CHU/0284,Osiemo Deborah Kwamboka,osiemo.kwamboka@chuka.ac.ke,Ms,Engineering,,No user linked
CHU/0285,Francis Kimathi M'Thuranira,francis.thuranira@chuka.ac.ke,Mr,Public Health,,No user linked
CHU/0286,Dorothy Kagendo Kithinji,dorothy.kithinji@chuka.ac.ke,Ms,Public Health,,No user linked
CHU/0287,Pauline Wanza Mwaka,pauline.mwaka@chuka.ac.ke,Ms,Computer Science,,No user linked
CHU/0288,Catherine Nkirote Kairo,catherine.kairo@chuka.ac.ke,Ms,Education,,No user linked
CHU/0289,Purity Kagwiria Mureithi,purity.mureithi@chuka.ac.ke,Ms,Education,,No user linked
CHU/0290,Alice Njeri Ngunu,alice.ngunu@chuka.ac.ke,Ms,Education,,No user linked
CHU/0291,Humphrey Mugambi Kararwa,humphrey.kararwa@chuka.ac.ke,Mr,Education,,No user linked
CHU/0294,Joshua W. Ngacha,joshua.ngacha@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0295,Eric Fwaya Onyango,eric.fwaya@chuka.ac.ke,Prof,Environmental Science & Resources Development,,No user linked
CHU/0303,Francis Mwangi,francis.mwangi@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0304,Justine Auko,justine.auko@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0305,Anthony Murithi,anthony.murithi@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0306,John Mulyungi,john.mulyungi@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0307,Rutfa Adeti,rutfa.adeti@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0308,Jane Mbugua,jane.mbugua@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0309,Annastacia Muthoki,annastacia.muthoki@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0310,Maryanne Gitari,maryanne.gitari@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0311,Mark Mbuvi,mark.mbuvi@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0312,Silvia Kanyua,silvia.kanyua@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0313,Michael Onyango,michael.onyango@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0314,Anthony Gitari,anthony.gitari@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0315,Francis Oduor,francis.oduor@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0316,David Mugambi,david.mugambi@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0318,Justin Mugendi,justin.mugendi@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0323,Virginia Kavuu,virginia.kavuu@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0327,Samson Chabari,samson.chabari@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0329,Brian Rotich,brian.rotich@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0333,Faith Gaceri,faith.gaceri@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0334,Jedidah Kariuki,jedidah.kariuki@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0344,Maryann Gitari,maryann.gitari@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0352,Maryanne Gitaari,maryanne.gitaari@chuka.ac.ke,Ms,Environmental Science & Resources Development,,No user linked
CHU/0361,Mugambi,mugambi@chuka.ac.ke,Mr,Environmental Science & Resources Development,,No user linked
CHU/0362,Agatha Mutio,agatha.mutio@chuka.ac.ke,Dr,Environmental Science & Resources Development,,No user linked
CHU/0366,Brendah Chepkorir,brendah.chepkorir@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0369,Kenneth Kigundu,kenneth.kigundu@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0370,Amos Mutambu,amos.mutambu@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0372,Leornard Musyoki,leornard.musyoki@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0373,Carolyne Mutunga,carolyne.mutunga@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0376,Gideon Bett,gideon.bett@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0378,Francis Mukundi,francis.mukundi@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0380,Rufo Munene,rufo.munene@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0381,Estonic Mutugi,estonic.mutugi@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0382,Rose Wambugu,rose.wambugu@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0383,Crispine Isboke,crispine.isboke@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0384,Kathenge,kathenge@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0386,Mwange Ifedha,mwange.ifedha@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0387,Liza Riungu,liza.riungu@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0392,Charity Nyaboke,charity.nyaboke@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0394,Loyford Kinegeni,loyford.kinegeni@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0395,Kavulya,kavulya@chuka.ac.ke,Prof,Social Sciences,,No user linked
CHU/0398,Purity Kananu,purity.kananu@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0399,Dennis Muriungi,dennis.muriungi@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0402,Ruth Manyara,ruth.manyara@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0403,Sophia W. Ndungu,sophia.ndungu@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0404,Mary Syombua Kisomo,mary.kisomo@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0406,Florence Musenya,florence.musenya@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0407,Lyn Goodness,lyn.goodness@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0408,Ridge Omabene,ridge.omabene@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0409,Wiviance Obuya,wiviance.obuya@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0410,Eliud Nguku,eliud.nguku@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0411,Isaiah Njeru Kibaara,isaiah.kibaara@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0412,Denis Mosoti,denis.mosoti@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0415,Monicah Oundo,monicah.oundo@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0416,Andrew Kimani,andrew.kimani@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0417,Kinoti Kibetu,kinoti.kibetu@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0421,Caroline Mwende,caroline.mwende@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0422,Hilder Mukami,hilder.mukami@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0423,Mercy Bwire,mercy.bwire@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0424,Immaculate Gichuru,immaculate.gichuru@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0425,Eric Kamau Chege,eric.chege@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0426,Geoffrey Okoth,geoffrey.okoth@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0427,Toney Omondi,toney.omondi@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0428,Grace Kirika,grace.kirika@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0429,Daisy Gatugi,daisy.gatugi@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0430,Tony Rono,tony.rono@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0431,Violet Oyiro,violet.oyiro@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0432,Silas Kariuki,silas.kariuki@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0433,Ibrahim Mulinge,ibrahim.mulinge@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0434,Elphas Ohuru,elphas.ohuru@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0435,Hillary Koros,hillary.koros@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0436,Linet Kajuju Minyori,linet.minyori@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0437,Elizabeth Mukuru,elizabeth.mukuru@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0438,Rose Ogallo,rose.ogallo@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0439,Tony Abonyo,tony.abonyo@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0440,Joseph Kathurima,joseph.kathurima@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0441,Bernadine M.,bernadine.m@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0442,Paul Alwanga,paul.alwanga@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0443,Sharlene Mulamula,sharlene.mulamula@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0444,Lavender Alividza,lavender.alividza@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0447,Veronica Mutinda,veronica.mutinda@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0449,Carolyne,carolyne@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0452,Charles Chabari,charles.chabari@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0453,Joseph Bill,joseph.bill@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0454,Kenneth Gituma,kenneth.gituma@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0455,Evelyne Mogire,evelyne.mogire@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0456,Zipporah Micere,zipporah.micere@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0457,Steve Kago,steve.kago@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0459,Michael Otieno,michael.otieno@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0460,David Kimathi,david.kimathi@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0461,Job Njeru,job.njeru@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0462,Joram Kiarie,joram.kiarie@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0463,Miriam Ngina,miriam.ngina@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0464,Lacton Mugambi,lacton.mugambi@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0465,Salome Nyaga,salome.nyaga@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0467,Grace Miako,grace.miako@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0468,Abraham Maruta,abraham.maruta@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0469,Benjamin Musau,benjamin.musau@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0470,Felister Wawira,felister.wawira@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0471,Nicasio Njiru,ncasio.njiru@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0472,George Okongo,george.okongo@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0473,Jessee Kithaka,jessee.kithaka@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0474,Peter Nderitu,peter.nderitu@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0475,Kalvin Malonza,kalvin.malonza@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0476,Anita Ngugi,anita.ngugi@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0477,Clementina Maina,clementina.maina@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0478,Zablon Mwangai,zablon.mwangai@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0479,Desmond Otieno,desmond.otieno@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0480,Lavender Echessa,lavender.echessa@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0481,Raymond Mutwiri,raymond.mutwiri@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0482,Julius Kaburu,julius.kaburu@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0483,Charles Gikunda,charles.gikunda@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0484,Faith Gichovi,faith.gichovi@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0485,Lilly Muchangi,lilly.muchangi@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0486,Gideon Munyao,gideon.munyao@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0487,Agnes Maigallo,agnes.maigallo@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0496,Hannington Sitati,hannington.sitati@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0497,Winfred Mbinya,winfred.mbinya@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0498,Sarah Muturi,sarah.muturi@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0500,Bridgit Kawira,bridgit.kawira@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0501,Sammy Kaburi,sammy.kaburi@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0502,Stephen Mureithi,stephen.mureithi@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0503,Obiero,obiero@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0505,S.K. Wambugu,sk.wambugu@chuka.ac.ke,Prof,Social Sciences,,No user linked
CHU/0506,Daniel Wachira,daniel.wachira@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0507,Mary Mukami,mary.mukami@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0508,Justine Okemwa,justine.okemwa@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0509,Lenity Mugendi,lenity.mugendi@chuka.ac.ke,Dr,Social Sciences,,No user linked
CHU/0510,John Wambua,john.wambua@chuka.ac.ke,Mr,Social Sciences,,No user linked
CHU/0511,Cecilia Chege,cecilia.chege@chuka.ac.ke,Ms,Social Sciences,,No user linked
CHU/0512,Jackin N. Nanua,jackin.nanua@chuka.ac.ke,Prof,Food Technology,,No user linked
CHU/0518,Fredrick Gatobu,fredrick.gatobu@chuka.ac.ke,Mr,Food Technology,,No user linked
CHU/0522,Sigar Otula,sigar.otula@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0524,Kelvin Gogo Otieno,kelvin.gogo@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0525,Malach Obisa Amonga,malach.amonga@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0530,Mohabe Chacha,mohabe.chacha@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0531,Justin Ireri,justin.ireri@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0533,Dennis Rugendo,dennis.rugendo@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0534,Kiruka Chacha,kiruka.chacha@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0535,Nancy Njoki,nancy.njoki@chuka.ac.ke,Ms,Computer Science,,No user linked
CHU/0536,Robert Mwenda,robert.mwenda@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0537,George Mutwiri,george.mutwiri@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0538,Chessy Maingi,chessy.maingi@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0539,Stephen Njoroge,stephen.njoroge@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0540,Caroline Nyaga,caroline.nyaga@chuka.ac.ke,Ms,Computer Science,,No user linked
CHU/0541,Fidelis Kinanu,fidelis.kinanu@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0542,Jane Kathambi,jane.kathambi@chuka.ac.ke,Ms,Computer Science,,No user linked
CHU/0543,Martin Muthomi,martin.muthomi@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0544,Dan Belator,dan.belator@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0545,Teresia Mbuthia,teresia.mbuthia@chuka.ac.ke,Ms,Computer Science,,No user linked
CHU/0546,Boniface Mwanzia,boniface.mwanzia@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0548,Dan Mwangi Njuguna,dan.njuguna@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0549,Mercy Wanjiru Njeru,mercy.njeru@chuka.ac.ke,Ms,Computer Science,,No user linked
CHU/0550,Calvince Otieno,calvince.otieno@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0551,Grace Kaunda,grace.kaunda@chuka.ac.ke,Ms,Computer Science,,No user linked
CHU/0552,Valentine John,valentine.john@chuka.ac.ke,Mr,Computer Science,,No user linked
CHU/0554,Okongo,okongo@chuka.ac.ke,Prof,Computer Science,,No user linked
CHU/0556,Patrick Mutwiri,patrick.mutwiri@chuka.ac.ke,Mr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0563,Samuel Otieno,samuel.otieno@chuka.ac.ke,Mr,Plant Science,,No user linked
CHU/0564,Vincent Koech,vincent.koech@chuka.ac.ke,Mr,Animal Science,,No user linked
CHU/0566,Daniel Wanja,daniel.wanja@chuka.ac.ke,Dr,Animal Science,,No user linked
CHU/0568,Dickson Machira,dickson.machira@chuka.ac.ke,Dr,Animal Science,,No user linked
CHU/0571,Ciriaka Muthoni,ciriaka.muthoni@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0572,Gitonga Kalawa,gitonga.kalawa@chuka.ac.ke,Mr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0573,David Kihoro,david.kihoro@chuka.ac.ke,Dr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0574,Raphael Kithome,raphael.kithome@chuka.ac.ke,Mr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0575,Kibet Maxin,kibet.maxin@chuka.ac.ke,Mr,Animal Science,,No user linked
CHU/0576,Reuben Kipkorir,reuben.kipkorir@chuka.ac.ke,Mr,Animal Science,,No user linked
CHU/0577,Dominic Maiyo,dominic.maiyo@chuka.ac.ke,Mr,Animal Science,,No user linked
CHU/0579,Glory Ntinyari,glory.ntinyari@chuka.ac.ke,Ms,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0580,Felisiter Mbaka,felisiter.mbaka@chuka.ac.ke,Ms,Plant Science,,No user linked
CHU/0581,Chesang Sumukwo,chesang.sumukwo@chuka.ac.ke,Mr,Animal Science,,No user linked
CHU/0588,Ibrahim Nyariki,ibrahim.nyariki@chuka.ac.ke,Mr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0590,Joyline Mugambi,joyline.mugambi@chuka.ac.ke,Dr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0591,Gilbert Odilla,gilbert.odilla@chuka.ac.ke,Dr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0592,Raphael Mwiti,raphael.mwiti@chuka.ac.ke,Dr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0593,Hellen Maseke,hellen.maseke@chuka.ac.ke,Ms,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0594,Margaret Mwangi,margaret.mwangi@chuka.ac.ke,Ms,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0595,Amos Kiptoo,amos.kiptoo@chuka.ac.ke,Mr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0596,Eric Koech,eric.koech@chuka.ac.ke,Mr,"Agricultural Economics, Agribusiness Management & Agricultural Education",,No user linked
CHU/0597,Simon Mburu,simon.mburu@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0598,Fredrick Ogolla,fredrick.ogolla@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0602,Judith Gitari,judith.gitari@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0603,Ciriaka Gitonga,ciriaka.gitonga@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0605,Peter Koech,peter.koech@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0606,Harun Ngugi,harun.ngugi@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0608,Ann Michura,ann.michura@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0609,Olivia Njiri,olivia.njiri@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0610,Joseph Mugendi,joseph.mugendi@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0612,Mercy Kinyua,mercy.kinyua@chuka.ac.ke,Ms,Biological Sciences,,No user linked
CHU/0613,Maryanne Odilla,maryanne.odilla@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0614,Winfred Kendi,winfred.kendi@chuka.ac.ke,Ms,Biological Sciences,,No user linked
CHU/0616,Hezron Kibet,hezron.kibet@chuka.ac.ke,Mr,Biological Sciences,,No user linked
CHU/0617,Mercy Wasonga,mercy.wasonga@chuka.ac.ke,Ms,Biological Sciences,,No user linked
CHU/0618,Purity Maginyo,purity.maginyo@chuka.ac.ke,Ms,Biological Sciences,,No user linked
CHU/0619,Vincent Obara,vincent.obara@chuka.ac.ke,Mr,Biological Sciences,,No user linked
CHU/0620,Misheck Mutuma M'Muyuri,misheck.mmuyuri@chuka.ac.ke,Dr,Biological Sciences,,No user linked
CHU/0621,Truphena Koech,truphena.koech@chuka.ac.ke,Ms,Biological Sciences,,No user linked
CHU/0622,Muhoro Anthony,muhoro.anthony@chuka.ac.ke,Mr,Biological Sciences,,No user linked
CHU/0623,Catherine Gatwiri,catherine.gatwiri@chuka.ac.ke,Ms,Biological Sciences,,No user linked
CHU/0625,Mwangi Peter,mwangi.peter@chuka.ac.ke,Mr,Biological Sciences,,No user linked
CHU/0626,Fridah Cheptum,fridah.cheptum@chuka.ac.ke,Ms,Biological Sciences,,No user linked
CHU/0627,Serah Kimingi,serah.kimingi@chuka.ac.ke,Ms,Physical Sciences,,No user linked
CHU/0628,Ann Kendi,ann.kendi@chuka.ac.ke,Ms,Physical Sciences,,No user linked
CHU/0631,Nancy Mutwiri,nancy.mutwiri@chuka.ac.ke,Ms,Physical Sciences,,No user linked
CHU/0632,Olivia Waka,olivia.waka@chuka.ac.ke,Ms,Physical Sciences,,No user linked
CHU/0633,Eric Munene,eric.munene@chuka.ac.ke,Mr,Physical Sciences,,No user linked
CHU/0635,Nelson Mugambi,nelson.mugambi@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0636,Edith Warue,edith.warue@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0637,Sammy Musundi,sammy.musundi@chuka.ac.ke,Prof,Physical Sciences,,No user linked
CHU/0638,Esther Kawira,esther.kawira@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0640,Victor Lumumba,victor.lumumba@chuka.ac.ke,Mr,Physical Sciences,,No user linked
CHU/0641,Jimrise Ochwach,jimrise.ochwach@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0644,Monicah Maithima,monicah.maithima@chuka.ac.ke,Ms,Physical Sciences,,No user linked
CHU/0647,Teddy Mutugi,teddy.mutugi@chuka.ac.ke,Mr,Physical Sciences,,No user linked
CHU/0649,Emmanuel Koech,emmanuel.koech@chuka.ac.ke,Mr,Physical Sciences,,No user linked
CHU/0651,Jane Mbae,jane.mbae@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0653,Faith Yator,faith.yator@chuka.ac.ke,Ms,Physical Sciences,,No user linked
CHU/0657,Richard Kariuki,richard.kariuki@chuka.ac.ke,Mr,Physical Sciences,,No user linked
CHU/0658,Peace Kaviti,peace.kaviti@chuka.ac.ke,Ms,Physical Sciences,,No user linked
CHU/0660,Bramwel Muyela,bramwel.muyela@chuka.ac.ke,Mr,Physical Sciences,,No user linked
CHU/0661,Daniel Muthee,daniel.muthee@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0664,Angeline Wambua,angeline.wambua@chuka.ac.ke,Ms,Physical Sciences,,No user linked
CHU/0666,Elosy Gatakaa,elosy.gatakaa@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0668,Anthony Odek,anthony.odek@chuka.ac.ke,Dr,Physical Sciences,,No user linked
CHU/0672,P. Gituma,p.gituma@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0675,Muguna,muguna@chuka.ac.ke,Prof,Business Administration,,No user linked
CHU/0677,R. Mwirigi,r.mwirigi@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0678,Bita M,bita.m@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0680,T. Mochoge,t.mochoge@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0682,Ngeretha,ngeretha@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0683,Kaimenyi,kaimenyi@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0686,M. Warutere,m.warutere@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0687,Wahu,wahu@chuka.ac.ke,Ms,Business Administration,,No user linked
CHU/0688,Namulia,namulia@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0689,C. Muthoni,c.muthoni@chuka.ac.ke,Ms,Business Administration,,No user linked
CHU/0690,J. Kagwi,j.kagwi@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0691,R. Mate,r.mate@chuka.ac.ke,Dr,Business Administration,,No user linked
CHU/0692,N. Momanyi,n.momanyi@chuka.ac.ke,Ms,Business Administration,,No user linked
CHU/0693,L. Muli,l.muli@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0694,W. Cherop,w.cherop@chuka.ac.ke,Ms,Business Administration,,No user linked
CHU/0696,A. Micheni,a.micheni@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0697,M. Mutegi,m.mutegi@chuka.ac.ke,Ms,Business Administration,,No user linked
CHU/0698,Alaka,alaka@chuka.ac.ke,Ms,Business Administration,,No user linked
CHU/0699,D. Nthiiri,d.nthiiri@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0700,E. Masita,e.masita@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0701,C. Wairimu,c.wairimu@chuka.ac.ke,Ms,Business Administration,,No user linked
CHU/0702,S. Kangethe,s.kangethe@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0703,R. Kariuki,r.kariuki@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0704,M. Murithi,m.murithi@chuka.ac.ke,Mr,Business Administration,,No user linked
CHU/0705,E. Alaka,e.alaka@chuka.ac.ke,Ms,Business Administration,,No user linked
CHU/0706,Marcel,marcel@chuka.ac.ke,Prof,Engineering,,No user linked
CHU/0707,Ohanga,ohanga@chuka.ac.ke,Mr,Engineering,,No user linked
CHU/0708,Josephat Machoka,josephat.machoka@chuka.ac.ke,Dr,Engineering,,No user linked
CHU/0710,Christopher Maina,christopher.maina@chuka.ac.ke,Prof,Engineering,,No user linked
CHU/0711,Roy Orenge,roy.orenge@chuka.ac.ke,Dr,Engineering,,No user linked
CHU/0713,Pius Njuguna,pius.njuguna@chuka.ac.ke,Mr,Engineering,,No user linked
CHU/0714,James Paul Chibole,james.chibole@chuka.ac.ke,Mr,Engineering,,No user linked
CHU/0715,Gachanja Muigai,gachanja.muigai@chuka.ac.ke,Mr,Engineering,,No user linked
CHU/0716,Francis Njoroge,francis.njoroge@chuka.ac.ke,Mr,Engineering,,No user linked
CHU/0718,Kiptoo,kiptoo@chuka.ac.ke,Mr,Engineering,,No user linked
CHU/0719,Gakera,gakera@chuka.ac.ke,Mr,Engineering,,No user linked
CHU/0732,Lemmy Mwaki Muriuki,lemmy.muriuki@chuka.ac.ke,Mr,Humanities,,No user linked
CHU/0734,Maore Josephat,maore.josephat@chuka.ac.ke,Mr,Humanities,,No user linked
CHU/0735,Bernard Kathata,bernard.kathata@chuka.ac.ke,Mr,Humanities,,No user linked
CHU/0736,Frankline Njeru Mbae,frankline.mbae@chuka.ac.ke,Dr,Humanities,,No user linked
CHU/0737,Henry Nyamu,henry.nyamu@chuka.ac.ke,Dr,Humanities,,No user linked
J.K MWANGI,JKMWANGI,jk@chuka.ac.ke,Mr,Management Science,,No user linked
MS/001/12,D.kiilu,kiiilu@chuka.ac.ke,Mr,Management Science,,No user linked
PENDING-1,Nelly Gatuti,nelly.gatuti@chuka.ac.ke,Ms,Humanities,,No user linked
PENDING-2,Paul M. Jinaro,paul.jinaro@chuka.ac.ke,Mr,Humanities,,No user linked
PUHE/001,Dr. Isaac Kamau,puhe001@chuka.ac.ke,Dr,Public Health,,No user linked
PUHE/002,Dr. Koome Impwii,puhe002@chuka.ac.ke,Dr,Public Health,,No user linked
PUHE/003,Stephen Kainga,puhe003@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/004,Br. Beth Gichobi,puhe004@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/005,Dr Muthee Gaichu,puhe005@chuka.ac.ke,Dr,Public Health,,No user linked
PUHE/006,Irene Atieno,puhe006@chuka.ac.ke,Ms,Public Health,,No user linked
PUHE/007,Casam Njagi,puhe007@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/008,Dorothy Micheni,puhe008@chuka.ac.ke,Ms,Public Health,,No user linked
PUHE/009,Elizabeth Mutunga,puhe009@chuka.ac.ke,Ms,Public Health,,No user linked
PUHE/010,Joshua Mwangi,puhe010@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/011,Hellen Mugambi,puhe011@chuka.ac.ke,Ms,Public Health,,No user linked
PUHE/012,Amos Mbaabu,puhe012@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/013,Lawrence Ireri,puhe013@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/014,Timothy Kinoti,puhe014@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/015,Murithi Nchege,puhe015@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/016,Willy Rankesh,puhe016@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/017,Dr. Dorothy Kagendo,puhe017@chuka.ac.ke,Dr,Public Health,,No user linked
PUHE/018,Purity Micheni,puhe018@chuka.ac.ke,Ms,Public Health,,No user linked
PUHE/019,Dr. Doreen Mbae,puhe019@chuka.ac.ke,Dr,Public Health,,No user linked
PUHE/020,Dr. Purity Silas,puhe020@chuka.ac.ke,Dr,Public Health,,No user linked
PUHE/021,Prof. Muraya,puhe021@chuka.ac.ke,Prof,Public Health,,No user linked
PUHE/022,Dr. Mary Wanjira,puhe022@chuka.ac.ke,Dr,Public Health,,No user linked
PUHE/023,Faith Kinya,puhe023@chuka.ac.ke,Ms,Public Health,,No user linked
PUHE/024,Ferdnard Rufus,puhe024@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/025,Dr. Nebert Muchiri,puhe025@chuka.ac.ke,Dr,Public Health,,No user linked
PUHE/026,Prof. Lucy Gitonga,puhe026@chuka.ac.ke,Prof,Public Health,,No user linked
PUHE/027,Nicholus Mbae,puhe027@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/028,Dr. E. Sundays,puhe028@chuka.ac.ke,Dr,Public Health,,No user linked
PUHE/029,Solomon Ogachi,puhe029@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/030,Dr. Willy Kiboi,puhe030@chuka.ac.ke,Dr,Public Health,,No user linked
PUHE/031,Dr. C. Muendo,puhe031@chuka.ac.ke,Dr,Public Health,,No user linked
PUHE/032,Patrick Mucheru,puhe032@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/033,Leah Mututho,puhe033@chuka.ac.ke,Ms,Public Health,,No user linked
PUHE/034,Peris Mwangi,puhe034@chuka.ac.ke,Ms,Public Health,,No user linked
PUHE/035,Monica Njoroge,puhe035@chuka.ac.ke,Ms,Public Health,,No user linked
PUHE/036,Dr. Cornellius Muendo,puhe036@chuka.ac.ke,Dr,Public Health,,No user linked
PUHE/037,Dr. Catherine Gichunge,puhe037@chuka.ac.ke,Dr,Public Health,,No user linked
PUHE/038,Antony Gitonga,puhe038@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/039,Gilbert Muchiri,puhe039@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/040,Duncan Maina,puhe040@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/041,Francis Thuranira,puhe041@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/042,Geoffrey Karanja,puhe042@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/043,Doreen Karimi,puhe043@chuka.ac.ke,Ms,Public Health,,No user linked
PUHE/044,Evans Nyamboi,puhe044@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/045,Kelvin Mwenda,puhe045@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/046,Nancy Karimi,puhe046@chuka.ac.ke,Ms,Public Health,,No user linked
PUHE/047,Hillary Bett,puhe047@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/048,Eric Kimathi,puhe048@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/049,Simon Ewoi,puhe049@chuka.ac.ke,Mr,Public Health,,No user linked
PUHE/050,Kelvin M. Mwega,puhe050@chuka.ac.ke,Mr,Public Health,,No user linked
temp/0001/kananu,PurityKananu,Kananu@chuka.ac.ke,Ms,Education,,No user linked
temp/ng/001,Githinji,githinji@chuka.ac.ke,Dr,Education,,No user linked
TEMP0001,Maryanne Makena,maryanne.makena@chuka.ac.ke,Ms,,,No user linked
TEMP0002,Agnes Kendi,agnes.kendi@chuka.ac.ke,Ms,Education,,No user linked
TEMP0003,Steve Muthomi,steve.muthomi@chuka.ac.ke,Mr,Education,,No user linked
TEMP0004,Elyjoy Kainyu,elyjoy.kainyu@chuka.ac.ke,Ms,Education,,No user linked
TEMP0005,Caroline Muchiri,caroline.muchiri@chuka.ac.ke,Ms,Education,,No user linked
TEMP0006,Monene Justine,monene.justine@chuka.ac.ke,Mr,Education,,No user linked
TEMP0007,Lydia Karimi,lydia.karimi@chuka.ac.ke,Ms,Education,,No user linked
TEMP0008,Harriet Kagendo,harriet.kagendo@chuka.ac.ke,Mr,Education,,No user linked
TEMP0009,Enid Kawira,enid.kawira@chuka.ac.ke,Mr,Education,,No user linked
TEMP0010,Noel Mbaka,noel.mbaka@chuka.ac.ke,Mr,Education,,No user linked
TEMP0011,Catherine,catherine@chuka.ac.ke,Dr,Education,,No user linked
TEMP0012,Eric Thauri,eric.thauri@chuka.ac.ke,Mr,Education,,No user linked
TEMP0013,Nduru,nduru@chuka.ac.ke,Mr,Education,,No user linked
TEMP0014,Gisoi,gisoi@chuka.ac.ke,Mr,Education,,No user linked
TEMP0015,Catherine Ngaine,catherine.ngaine@chuka.ac.ke,Ms,Education,,No user linked
TEMP0016,Jane Kathomi,jane.kathomi@chuka.ac.ke,Ms,Humanities,,No user linked
TEMP0017,Elsie Kirimo,elsie.kirimo@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0018,Jacob Murigi,jacob.murigi@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0019,Nkonge,nkonge@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0020,Crispus Mwakundia,crispus.mwakundia@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0021,Antony Mbita,antony.mbita@chuka.ac.ke,Mr,Education,,No user linked
TEMP0023,Catherine Nkirote,catherine.nkirote@chuka.ac.ke,Ms,Education,,No user linked
TEMP0024,Nelly Kananu,nelly.kananu@chuka.ac.ke,Ms,Education,,No user linked
TEMP0025,Prisca Kobia,prisca.kobia@chuka.ac.ke,Mr,Education,,No user linked
TEMP0026,Gladys Njogu,gladys.njogu@chuka.ac.ke,Ms,Education,,No user linked
TEMP0027,Emis Miriti,emis.miriti@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0028,Lily Mbugua,lily.mbugua@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0029,Nelly Kamankura,nelly.kamankura@chuka.ac.ke,Ms,Humanities,,No user linked
TEMP0030,Grp B Dr. Mulati,grp.mulati@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0031,Martin Njogu,martin.njogu@chuka.ac.ke,Mr,Plant Science,,No user linked
TEMP0032,Marcella Kamami,marcella.kamami@chuka.ac.ke,Ms,Education,,No user linked
TEMP0033,Emmanuel Njuki,emmanuel.njuki@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0035,Muriungi Kanaa,muriungi.kanaa@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0036,Murati,murati@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0037,Sawa Wambua,sawa.wambua@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0038,Eunice Magu,eunice.magu@chuka.ac.ke,Ms,Humanities,,No user linked
TEMP0039,Patrick Njoroge,patrick.njoroge@chuka.ac.ke,Mr,Plant Science,,No user linked
TEMP0040,Diana Kaburo,diana.kaburo@chuka.ac.ke,Ms,Humanities,,No user linked
TEMP0041,Kirimi Kainga,kirimi.kainga@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0042,Fridah Njerere,fridah.njerere@chuka.ac.ke,Ms,Humanities,,No user linked
TEMP0043,Gikunda,gikunda@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0045,Elsie Kaburu,elsie.kaburu@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0046,Timothy Kangori,timothy.kangori@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0047,Lucy Njeru,lucy.njeru@chuka.ac.ke,Ms,Humanities,,No user linked
TEMP0049,John Karauri,john.karauri@chuka.ac.ke,Dr,,,No user linked
TEMP0050,Humprey Mugambi,humprey.mugambi@chuka.ac.ke,Dr,,,No user linked
TEMP0059,Benjamin Kanga,benjamin.kanga@chuka.ac.ke,Dr,,,No user linked
TEMP0067,Maluni,maluni@chuka.ac.ke,Mr,,,No user linked
TEMP0068,Ogembo,ogembo@chuka.ac.ke,Dr,,,No user linked
TEMP0078,C.Patrick Kihara,cpatrick.kihara@chuka.ac.ke,Dr,Humanities,,No user linked
TEMP0079,Christine Atieno,christine.atieno@chuka.ac.ke,Ms,,,No user linked
TEMP0080,Waita,waita@chuka.ac.ke,Mr,,,No user linked
TEMP0081,Bururia,bururia@chuka.ac.ke,Mr,,,No user linked
TEMP0082,John Kobia,john.kobia@chuka.ac.ke,Mr,,,No user linked
TEMP0092,Felsiter Mbaka,felsiter.mbaka@chuka.ac.ke,Mr,,,No user linked
TEMP0097,Robinson Kenyatta,robinson.kenyatta@chuka.ac.ke,Mr,,,No user linked
TEMP0098,Kithinji,kithinji@chuka.ac.ke,Dr,,,No user linked
TEMP0104,James Mwenda,james.mwenda@chuka.ac.ke,Mr,,,No user linked
TEMP0105,Peter Kimanthi,peter.kimanthi@chuka.ac.ke,Dr,,,No user linked
TEMP0107,Kathuri Kathure,kathuri.kathure@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0108,Martin Githinji,martin.githinji@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0109,Wanyonyi,a.wanyonyi@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0110,Wanyama,s.wanyama@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0111,Muriungi,p.muriungi@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0112,Gwaya,d.gwaya@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0113,Muhanji,g.muhanji@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0114,Mambo,j.mambo@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0115,Grace Murith,grace.murith@chuka.ac.ke,Prof,,,No user linked
TEMP0116,Nyagah,d.nyagah@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0117,Kenkelvin Kimathi,kenkelvin.kimathi@chuka.ac.ke,Dr,,,No user linked
TEMP0118,Mawia,r.mawia@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0119,Eric Mwenda,eric.mwenda@chuka.ac.ke,Prof,,,No user linked
TEMP0120,Mutinda,j.mutinda@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0121,Kagiri,r.kagiri@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0122,Cherono,d.cherono@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0123,Jepkorir,s.jepkorir@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0124,Liz Riungu,liz.riungu@chuka.ac.ke,Dr,,,No user linked
TEMP0125,Nzuve,r.nzuve@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0126,Mwita,e.mwita@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0127,Kabubu,c.kabubu@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0128,Muchuo,c.muchuo@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0129,Kosgei,l.kosgei@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0130,Kobia,em.kobia@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0131,Chebet,i.chebet@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0132,Mbithe,a.mbithe@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0133,Venessa,dr.venessa@chuka.ac.ke,Dr,Management Science,,No user linked
TEMP0134,Masinde,prof.masinde@chuka.ac.ke,Prof,Management Science,,No user linked
TEMP0140,Makena,n.makena@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0141,Wanjiru,m.wanjiru@chuka.ac.ke,Ms,Management Science,,No user linked
TEMP0142,Kirimi,dr.kirimi@chuka.ac.ke,Dr,Management Science,,No user linked
TEMP0143,Mwove,dr.mwove@chuka.ac.ke,Dr,Management Science,,No user linked
TEMP0144,Kathure,m.kathure@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0145,Miriam Mutegi,miriam.mutegi@chuka.ac.ke,Ms,Humanities,,No user linked
TEMP0146,Mukasa,mukasa@chuka.ac.ke,Mr,,,No user linked
TEMP0147,Mary Kanyua,mary.kanyua@chuka.ac.ke,Ms,,,No user linked
TEMP0148,Dr.Hellen Orina,drhellen.orina@chuka.ac.ke,Mr,,,No user linked
TEMP0155,Susan Muthoni,susan.muthoni@chuka.ac.ke,Ms,,,No user linked
TEMP0170,Dr.Shadrack Kirimi,drshadrack.kirimi@chuka.ac.ke,Mr,,,No user linked
TEMP0189,Humphrey Kirimi,humphrey.kirimi@chuka.ac.ke,Mr,,,No user linked
TEMP0191,Lemmy Mwaki,lemmy.mwaki@chuka.ac.ke,Mr,,,No user linked
TEMP0195,Job Mulati,job.mulati@chuka.ac.ke,Mr,,,No user linked
TEMP0200,Cod. Humanities,cod.humanities@chuka.ac.ke,Mr,,,No user linked
TEMP0201,Academic Leader,academic.leader@chuka.ac.ke,Mr,,,No user linked
TEMP0202,"Member, Ethics Cemmittee",member.cemmittee@chuka.ac.ke,Mr,,,No user linked
TEMP0203,Section Head Religious Studies,section.studies@chuka.ac.ke,Mr,,,No user linked
TEMP0204,"Director, Quality Assurance",director.assurance@chuka.ac.ke,Mr,,,No user linked
TEMP0205,Industrial & Field Attachment Coordinator,industrial.coordinator@chuka.ac.ke,Mr,,,No user linked
TEMP0206,Literature Section Coordinator /Academic Leader,literature.coordinator@chuka.ac.ke,Mr,,,No user linked
TEMP0209,Departmental Assistant Exams Officer,departmental.officer@chuka.ac.ke,Mr,,,No user linked
TEMP0210,Coordinator French And Linguistics Section /Academic Leader,coordinator.section@chuka.ac.ke,Mr,,,No user linked
TEMP0211,Coordinator History Section And Chair Of Departmental Welfare,coordinator.welfare@chuka.ac.ke,Mr,,,No user linked
TEMP0212,Graduate Assistant,graduate.assistant@chuka.ac.ke,Mr,,,No user linked
TEMP0213,"Dean, Faculty Of Humanities And Social Sciences",dean.sciences@chuka.ac.ke,Mr,,,No user linked
TEMP0214,"Coordinator, Communication And Media Studies & In Charge Radio Station",coordinator.station@chuka.ac.ke,Mr,,,No user linked
TEMP0215,"Assistant Coordinator, French And Linguistics Section",assistant.section@chuka.ac.ke,Mr,,,No user linked
TEMP0216,Angelo Mwirigi,angelo.mwirigi@chuka.ac.ke,Mr,,,No user linked
TEMP0217,Benson Machogu,benson.machogu@chuka.ac.ke,Mr,,,No user linked
TEMP0218,Bonface Nyamweya,bonface.nyamweya@chuka.ac.ke,Mr,,,No user linked
TEMP0219,David Wachira,david.wachira@chuka.ac.ke,Mr,,,No user linked
TEMP0220,Dennis Ongeri,dennis.ongeri@chuka.ac.ke,Mr,,,No user linked
TEMP0221,Dounglas Muthinji,dounglas.muthinji@chuka.ac.ke,Mr,,,No user linked
TEMP0222,Dr Theophilus,dr.theophilus@chuka.ac.ke,Mr,,,No user linked
TEMP0223,Dr. Caroline Sikuku,dr.sikuku@chuka.ac.ke,Mr,,,No user linked
TEMP0224,Dr. Dorcas Shikuku,dr.shikuku@chuka.ac.ke,Mr,,,No user linked
TEMP0225,Dr. Mary Mbogo,dr.mbogo@chuka.ac.ke,Mr,,,No user linked
TEMP0226,Dr. Paul Ginaro,dr.ginaro@chuka.ac.ke,Mr,,,No user linked
TEMP0227,Dr. Peter Wambugu,dr.wambugu@chuka.ac.ke,Mr,,,No user linked
TEMP0228,Dr. Preston Njerus,dr.njerus@chuka.ac.ke,Mr,Humanities,,No user linked
TEMP0229,Eugene Nyaga,eugene.nyaga@chuka.ac.ke,Mr,,,No user linked
TEMP0230,Elsie Mukami,elsie.mukami@chuka.ac.ke,Mr,,,No user linked
TEMP0231,Elosy Kawira Kaburu,elosy.kaburu@chuka.ac.ke,Mr,,,No user linked
TEMP0232,Enock Kilwa,enock.kilwa@chuka.ac.ke,Mr,,,No user linked
TEMP0233,Evangeline Makena,evangeline.makena@chuka.ac.ke,Mr,,,No user linked
TEMP0234,Evangeline Mitambo,evangeline.mitambo@chuka.ac.ke,Mr,,,No user linked
TEMP0235,Felix Muthomi,felix.muthomi@chuka.ac.ke,Mr,,,No user linked
TEMP0236,Florence Ngei,florence.ngei@chuka.ac.ke,Ms,,,No user linked
TEMP0237,Fridah Karambu,fridah.karambu@chuka.ac.ke,Ms,,,No user linked
TEMP0238,George Nyongesa,george.nyongesa@chuka.ac.ke,Mr,,,No user linked
TEMP0239,Gilbert Muthomi,gilbert.muthomi@chuka.ac.ke,Mr,,,No user linked
TEMP0240,Hannington Sikuku,hannington.sikuku@chuka.ac.ke,Mr,,,No user linked
TEMP0241,Isaac Kathio,isaac.kathio@chuka.ac.ke,Mr,,,No user linked
TEMP0242,Isiah Kibaara,isiah.kibaara@chuka.ac.ke,Mr,,,No user linked
TEMP0243,James Gitonga,james.gitonga@chuka.ac.ke,Mr,,,No user linked
TEMP0244,Jesse Mutugi,jesse.mutugi@chuka.ac.ke,Mr,,,No user linked
TEMP0245,Joseck Ogero,joseck.ogero@chuka.ac.ke,Mr,,,No user linked
TEMP0246,Josphat Kaume,josphat.kaume@chuka.ac.ke,Mr,,,No user linked
TEMP0247,Josphat Koome,josphat.koome@chuka.ac.ke,Mr,,,No user linked
TEMP0248,Linet Kawira,linet.kawira@chuka.ac.ke,Ms,,,No user linked
TEMP0249,Lisper Kathure,lisper.kathure@chuka.ac.ke,Mr,,,No user linked
TEMP0250,Lydia Gacheri,lydia.gacheri@chuka.ac.ke,Ms,,,No user linked
TEMP0251,Lydia Gacieri,lydia.gacieri@chuka.ac.ke,Ms,,,No user linked
TEMP0253,Mary Kainda,mary.kainda@chuka.ac.ke,Ms,,,No user linked
TEMP0254,Mary Wangai,mary.wangai@chuka.ac.ke,Ms,,,No user linked
TEMP0255,Mercy Bwera,mercy.bwera@chuka.ac.ke,Ms,,,No user linked
TEMP0256,Michael Wekesa,michael.wekesa@chuka.ac.ke,Mr,,,No user linked
TEMP0258,Moses Mwithali,moses.mwithali@chuka.ac.ke,Mr,,,No user linked
TEMP0259,Nancy Kainda,nancy.kainda@chuka.ac.ke,Ms,,,No user linked
TEMP0260,Nelly Gatugi,nelly.gatugi@chuka.ac.ke,Ms,,,No user linked
TEMP0261,Paul Thuranira,paul.thuranira@chuka.ac.ke,Mr,,,No user linked
TEMP0262,Preston Njeru,preston.njeru@chuka.ac.ke,Mr,,,No user linked
TEMP0263,Prof. Dennis Murithi,prof.murithi@chuka.ac.ke,Mr,,,No user linked
TEMP0264,Prof. Mark Okango,prof.okango@chuka.ac.ke,Mr,,,No user linked
TEMP0265,Vicmoses Punyuah,vicmoses.punyuah@chuka.ac.ke,Mr,,,No user linked
TEMP0266,Purity Damiano,purity.damiano@chuka.ac.ke,Ms,,,No user linked
TEMP0267,Purity Kajuju,purity.kajuju@chuka.ac.ke,Ms,,,No user linked
TEMP0268,Richard Olwande,richard.olwande@chuka.ac.ke,Mr,,,No user linked
TEMP0269,Robert Kaberia,robert.kaberia@chuka.ac.ke,Mr,,,No user linked
TEMP0270,Serah Njoki,serah.njoki@chuka.ac.ke,Ms,,,No user linked
TEMP0271,Samuel Irungu,samuel.irungu@chuka.ac.ke,Mr,,,No user linked
TEMP0272,Samuel Murigu,samuel.murigu@chuka.ac.ke,Mr,,,No user linked
TEMP0273,Samwel Marigu,samwel.marigu@chuka.ac.ke,Mr,,,No user linked
TEMP0274,Samwel Njeru,samwel.njeru@chuka.ac.ke,Mr,,,No user linked
TEMP0275,Sarah Kawira,sarah.kawira@chuka.ac.ke,Ms,,,No user linked
TEMP0276,Sawa Wabomba,sawa.wabomba@chuka.ac.ke,Mr,,,No user linked
TEMP0277,Sheila Wangari,sheila.wangari@chuka.ac.ke,Ms,,,No user linked
TEMP0278,Sheilla Wangari,sheilla.wangari@chuka.ac.ke,Ms,,,No user linked
TEMP0279,Simon Mabea,simon.mabea@chuka.ac.ke,Mr,,,No user linked
TEMP0280,Stella Njiru,stella.njiru@chuka.ac.ke,Ms,,,No user linked
TEMP0281,Tabitha Kamau,tabitha.kamau@chuka.ac.ke,Mr,,,No user linked
TEMP0282,Theophilus Nzengu,theophilus.nzengu@chuka.ac.ke,Mr,,,No user linked
TEMP0283,Thomas Gichovi,thomas.gichovi@chuka.ac.ke,Mr,,,No user linked
TEMP0284,Winfred Mukami,winfred.mukami@chuka.ac.ke,Ms,,,No user linked
TEMP0285,Winjoy Nyawira,winjoy.nyawira@chuka.ac.ke,Ms,Plant Science,,No user linked
TEMP0300,C Mathenge,c.mathenge@chuka.ac.ke,Mr,Business Administration,,No user linked
TEMP0301,Mbeche,w.mbeche@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0302,Ndwiga,ww.ndwiga@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0303,Rimba,e.rimba@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP0304,Nyariki,i.nyariki@chuka.ac.ke,Mr,Management Science,,No user linked
TEMP9001,Kimathi,kimathi@chuka.ac.ke,Mr,Plant Science,,No user linked
TEMP9002,Sioma,sioma@chuka.ac.ke,Mr,Plant Science,,No user linked
TEMP9003,Lucy,lucy@chuka.ac.ke,Ms,Plant Science,,No user linked
TEMP9004,Antony Kimathi,antony.kimathi@chuka.ac.ke,Mr,Plant Science,,No user linked
TEMP9005,Lucy Gatwiri,lucy.gatwiri@chuka.ac.ke,Ms,Plant Science,,No user linked
"""


# ────────────────────────────── ENTRYPOINT ─────────────────────────────

audit_lecturer_csv()
run_mapping()
