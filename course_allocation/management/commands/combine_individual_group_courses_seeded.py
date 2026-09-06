# course_allocation/management/commands/combine_individual_group_courses_seeded.py
"""
Combine split CourseAllocation rows for courses listed in the
"individual specialization" tables of the DEDU Semester 1 (2025/2026AY)
allocation sheet ("eduvation.docx") into one CombinedCourseGroup each.

THIS IS THE FOLLOW-UP TO combine_dedu_groups_seeded.py, NOT A REPLACEMENT
------------------------------------------------------------------------
That earlier command handled DEDU's own COMMON COURSES tables (e.g.
"GROUP: Bachelor of Education (Arts) AND SCIENCE, Y1S1 ... COMMON COURSES
(A,B,C,...)") — one row per DEDU course, one lecturer per letter-group,
already correctly combined. Per your confirmation, that part is done;
this command does NOT touch those rows again.

WHAT THIS COMMAND HANDLES INSTEAD
----------------------------------
Every OTHER table on the sheet — the ones headed just
"GROUP: Bachelor of Education (Arts), Y1S1 ..." (no "COMMON COURSES" in
the title) — lists each course exactly once, one lecturer, regardless
of department (ENGL/LITT/HIST under DHUM, MATH under PHSC, BCOM/BHRM
under DBAD/DMSC, COSC under DCOMP, HOSC/PESP under DEDU, etc.).

On paper that's one row. In the system it is NOT one row, because a
single B.Ed programme is actually made up of several student
subject-combination groups (e.g. ENG/LIT, HIST/RELI, KISW/FREN,
MATH/BST, BST/GEO, ...), and a subject like "BST" (e.g. BCOM 101) is
shared by more than one combination (both MATH/BST and BST/GEO
students take it). Each combination's cohort got its own
CourseAllocation row when allocations were entered, so a course that
is a SINGLE row on the sheet can exist as 2+ separate CourseAllocation
rows in the database, all pointing at the exact same curriculum entry
(the same ProgramCourse — same program, same year, same semester, same
course_code). Those need to become one CombinedCourseGroup, exactly
like DEDU's own courses did.

HOW MATCHING WORKS (important — this is NOT a bare string match)
------------------------------------------------------------------
For every seeded (program, year, semester, course_code) triple below,
this command:
  1. Resolves the real program_management.Program row (see PROGRAM
     RESOLUTION below).
  2. Finds every program_management.ProgramCourse curriculum entry for
     that program/year/semester whose course_code collapses (via
     program_management.code_utils.canonical_course_key) to the same
     bare code as the seeded one.
  3. Pulls every CourseAllocation row FK'd to those ProgramCourse rows
     (course_allocation.CourseAllocation.program_course), and groups
     them by department.
  4. Any department with 2+ allocation rows for that curriculum entry
     gets ONE CombinedCourseGroup — the same "2+ allocations, same
     base course code" rule course_management/cod_panel.py's
     create_combined_group enforces by hand.
This is stricter than the DEDU-only script: it keys off the actual
ProgramCourse curriculum row (via the FK, not a guessed base-code
string), so courses that happen to share a code across two different
programmes (MATH 122 appears in BOTH BEd Arts and BEd Science, but
they are different ProgramCourse rows) are never cross-merged.

PROGRAM RESOLUTION
-------------------
Program names weren't available to read at doc-extraction time, so
each seeded section carries a human-readable "program_guess" plus a
small keyword filter (require/exclude terms against Program.name).
On every run the command tries to resolve each guess to EXACTLY one
Program row. If it finds zero or more than one match, it SKIPS that
section and prints the candidates it found (or a note to check
spelling), rather than guessing wrong. Fix a mismatch by adding an
entry to PROGRAM_NAME_OVERRIDES below (paste the exact Program.name
from the printed list), or pass --program-map '{"Bachelor Of Education
Arts": "Exact Name In DB"}' on the command line (takes precedence over
the dict). Run with --list-programs first if you want to see every
Program in the database before doing anything else.

SECTIONS DELIBERATELY LEFT OUT
--------------------------------
"GROUP: Bachelor of Education (Arts) AND SCIENCE, Y4S1 SEPT 2023 INTAKE"
(EAPE 411, EAPE 412, EPSC 431, EDFO 422) is NOT in the seed list below.
Its title doesn't say "COMMON COURSES", but every row in it already
lists multiple lecturers by letter-group in a single cell (e.g. "Prof
Nelson Jagero(A&B), Prof Eric Mwenda(C&D)") — the same shape as the
COMMON COURSES tables you said are already handled, not the
one-lecturer-one-row shape this command is for. If that's wrong and
you do want it processed, add its 4 codes to SEED_SECTIONS by hand.

"GROUP: BACHELOR OF EDUCATION (ECDE), Y1S1/Y2S1/Y3S1/Y4S1" (ECDE 121,
122, 123, 131, 141, 231-238, 311-341, 411-444, plus COMS 101/COSC 103
in Y1) are ALSO NOT in the seed list below — confirmed excluded on
request. ECDE is a single-track programme, not a multi-subject
combination degree, so this command does not touch it at all.

"GROUP: BSC AGED Y1S1/Y2S1/Y4S1" (EDFO 111, EPSC 111, EAPE 211,
EDFO 211, EAPE 411, EPSC 331) are ALSO NOT in the seed list below —
confirmed excluded on request, same as ECDE.

So SEED_SECTIONS now covers only Bachelor of Education (Arts) and
Bachelor of Education (Science): 8 sections, 204 course rows. If
duplicate ECDE or AGED CourseAllocation rows ever need combining,
that's a separate, deliberate decision — not something this command
will do automatically.

Also excluded (never appear in an "individual" table at all, so
nothing to seed): the OPTIONAL/ELECTIVES/PURE MATHEMATICS/APPLIED
MATHEMATICS/STATISTICS MATHEMATICS/MATHEMATICS/"SERVICE COURSES TO
OTHER DEPARTMENTS" rows are section labels on the sheet, not real
course rows, and were dropped automatically during extraction.

USAGE
-----
    # 0) See every Program in the DB (handy for filling in overrides).
    python manage.py combine_individual_group_courses_seeded --list-programs

    # 1) Preview only — prints exactly what would be combined, writes nothing.
    python manage.py combine_individual_group_courses_seeded

    # 2) Check one course code first.
    python manage.py combine_individual_group_courses_seeded --course-code "BCOM 101"

    # 3) Check one section only (program + year + semester).
    python manage.py combine_individual_group_courses_seeded --program-guess "Bachelor of Education (Arts)" --year 1 --semester 1

    # 4) Apply for real, once the preview looks right.
    python manage.py combine_individual_group_courses_seeded --apply

    # 5) Override a program-name resolution that didn't come back as exactly one match
    #    (e.g. if "Bachelor of Education (Arts)" matches two similarly-named DB rows).
    python manage.py combine_individual_group_courses_seeded --apply \
        --program-map '{"Bachelor of Education (Arts)": "Bachelor of Education (Arts)"}'

INSTALLATION
------------
Drop this file at:
    course_allocation/management/commands/combine_individual_group_courses_seeded.py
"""
import json
import re
from collections import defaultdict

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from course_allocation.models import CourseAllocation, CombinedCourseGroup
from program_management.models import Program, ProgramCourse
from program_management.code_utils import normalize_code, canonical_course_key


# ---------------------------------------------------------------------------
# If a program_guess below doesn't resolve to exactly one Program in your
# database, add the exact Program.name here (copy it from
# --list-programs output) and re-run. Overrides here are used unless
# --program-map supplies the same key on the command line.
# ---------------------------------------------------------------------------
PROGRAM_NAME_OVERRIDES = {
    # "Bachelor of Education (Arts)": "Bachelor of Education (Arts)",
    # "Bachelor of Education (Science)": "Bachelor of Education (Science)",
}

# Keyword filters used to auto-resolve each program_guess against
# Program.name when no override is given: (must_include, must_exclude),
# case-insensitive substring checks, ALL must_include terms required,
# NONE of must_exclude may appear.
PROGRAM_RESOLUTION_KEYWORDS = {
    "Bachelor of Education (Arts)": (["education", "arts"], ["science", "ecde", "master", "doctor", "philosophy", "aged", "agricultural"]),
    "Bachelor of Education (Science)": (["education", "science"], ["arts", "ecde", "master", "doctor", "philosophy", "aged", "agricultural"]),
}


# ---------------------------------------------------------------------------
# SEED DATA — every "individual specialization" table on the DEDU Semester 1
# (2025/2026AY) allocation sheet (eduvation.docx), i.e. every "GROUP: ..."
# table WITHOUT "COMMON COURSES" in its title, EXCLUDING the ECDE tables
# and the BSC AGED tables (both deliberately left out — see "SECTIONS
# DELIBERATELY LEFT OUT" above) and the ambiguous Y4S1 "ARTS AND SCIENCE"
# table. 8 sections (Bachelor of Education (Arts) / Science only), 204
# course rows, extracted 2026-08-17. Course title is included only as a
# human-readable comment; matching uses course_code only.
# ---------------------------------------------------------------------------
SEED_SECTIONS = [
    {
        "program_guess": 'Bachelor of Education (Arts)',
        "year": 1,
        "semester": 1,
        "header": 'GROUP: Bachelor of Education (Arts), Y1S1  SEPT 2026 INTAKE',
        "codes": [
            'ENGL 101',  # Introduction to Language and Linguistics
            'ENGL 102',  # Introduction to Phonetics and Phonology
            'LITT 101',  # Introduction to Literature and Literary Criticism
            'LITT 102',  # Introduction to Oral Literature
            'HIST 121',  # Selected Topics in Kenyan History
            'HIST 151',  # Selected Topics in Global History I
            'RELI 100',  # Introduction Study of Religion
            'RELI 120',  # African Religions
            'KISW 101',  # Introduction to Linguistics
            'KISW 102',  # History and Modern Development of Kiswahili
            'FREN 110',  # French Structure I
            'FREN 130',  # Written Expression, Interaction & Comprehension
            'MUSC 101',  # Fundamentals of Music and Practical Performance
            'MUSC 111',  # Music of the Medieval and Renaissance Times
            'GEOG 100',  # History of Geographic Thought
            'GEOG 110',  # Introduction to Physical Geography
            'MATH 122',  # Basic Mathematics
            'MATH 124',  # Geometry and Linear Algebra
            'BCOM 101',  # Introduction to Business
            'BHRM 113',  # Foundations of Accounting
            'COSC 106',  # Information Technology and Society
            'COSC 111',  # Computer Systems and Organization
            'AGRI 101',  # Introduction to Agriculture and Food Security
            'SOIL 100',  # Introduction to Soil Science
            'HOSC 111',  # General Science for Home Science
            'HOSC 131',  # Introduction to Foods, Nutrition and Dietetics
            'PESP 111',  # Hockey, Netball and Soccer
            'PESP 121',  # Foundations of Physical Education
            'PESP 141',  # Adapted Physical Activity
        ],
    },
    {
        "program_guess": 'Bachelor of Education (Science)',
        "year": 1,
        "semester": 1,
        "header": 'GROUP: Bachelor of Education (Science), Y1S1   SEPT 2026 INTAKE',
        "codes": [
            'CHEM 101',  # Chemical Laboratory Safety and Security
            'CHEM 110',  # Inorganic Chemistry I
            'MATH 122',  # Basic Mathematics
            'MATH 124',  # Geometry and Linear Algebra
            'PHYS 121',  # Physics Practical I
            'PHYS 131',  # Mechanics
            'PHYS 161',  # Heat and Thermodynamics
            'BOTA 101',  # General Botany
            'BOTA 111',  # General Genetics
            'ZOOL 101',  # Lower Invertebrates
            'COSC 106',  # Information Technology and Society
            'COSC 111',  # Computer Systems and Organization
            'AGRI 101',  # Introduction to Agriculture and Food Security
            'SOIL 100',  # Introduction to Soil Science
            'HOSC 111',  # General Science for Home Science
            'HOSC 131',  # Introduction to Foods, Nutrition and Dietetics
        ],
    },
    {
        "program_guess": 'Bachelor of Education (Arts)',
        "year": 2,
        "semester": 1,
        "header": 'GROUP: Bachelor of Education (Arts), Y2S1            SEPT 2025 INTAKE',
        "codes": [
            'ENGL 211',  # Morphology and Syntax in English
            'ENGL 231',  # Sociolinguistics
            'LITT 211',  # Children's Literature
            'LITT 212',  # Theory of Literature
            'HIST 211',  # Historical Method
            'HIST 253',  # The History of Middle East
            'KISW 201',  # Phonetics and Phonology
            'KISW 202',  # Theory of Literature and Literacy Criticism
            'MUSC 201',  # Compositional Studies 1
            'MUSC 202',  # Musicianship II
            'RELI 221',  # Comparative Religions
            'RELI 230',  # Introduction to Biblical Studies
            'HOSC 251',  # Household Resource Management
            'HOSC 211',  # Meal Management and Service
            'HOSC 221',  # Household Equipment
            'GEOG 200',  # Geography of East Africa
            'GEOG 223',  # Agricultural Geography
            'MATH 221',  # Calculus II
            'MATH 241',  # Probability and Statistics 1
            'BCOM 214',  # Intermediate Financial Accounting
            'BCOM 264',  # Quantitative Methods In Business
            'COSC 225',  # C Programming
            'COSC 221',  # Fundamentals of Computer Networks
            'FREN 243',  # Introduction to Translation and Interpreting
            'FREN 240',  # Sociocultural study of the Francophone World
            'PESP 211',  # Athletics, Softball and Swimming I
            'PESP 221',  # Sports Pedagogy and Management
            'PESP 222',  # Legal Issues in Sports
            'AGRI 271',  # Pastures and Fodder Crops
            'ANSC 359',  # Principles of Animal Production
        ],
    },
    {
        "program_guess": 'Bachelor of Education (Science)',
        "year": 2,
        "semester": 1,
        "header": 'GROUP: Bachelor of Education (Science), Y2S1     SEPT 2025 INTAKE',
        "codes": [
            'CHEM 211',  # Physical Inorganic Chemistry
            'CHEM 231',  # Organic Chemistry II
            'CHEM 241',  # Chemical Separation Techniques
            'MATH 221',  # Calculus II
            'MATH 241',  # Probability and Statistics 1
            'MATH 222',  # Vector Analysis
            'PHYS 223',  # Physics Practical III
            'PHYS 232',  # Waves and Oscillations
            'PHYS 241',  # Electricity and Magnetism I
            'BOTA 241',  # Taxonomy of Higher Plants
            'ZOOL 210',  # Ecology
            'ZOOL 232',  # Cell Biology
            'COSC 225',  # Computer Programming
            'COSC 261',  # Fundamentals of Computer Networks
            'AGRI 271',  # Pastures and Fodder Crops
            'ANSC 359',  # Principles of Animal Production
            'HOSC 251',  # Household Resource Management
            'HOSC 211',  # Meal Management and Service
            'HOSC 221',  # Household Equipment
        ],
    },
    {
        "program_guess": 'Bachelor of Education (Arts)',
        "year": 3,
        "semester": 1,
        "header": 'GROUP: Bachelor of Education (Arts), Y3S1     SEPT 2024 INTAKE',
        "codes": [
            'ENGL 351',  # Historical and Comparative Linguistics
            'ENGL 313',  # Aspects of Grammatical Analysis II
            'LITT 313',  # Stylistics
            'LITT 323',  # African Drama
            'HIST 361',  # History of Government and Constitutions
            'HIST 347',  # Economic History of Africa since 1900
            'KISW 302',  # Modern Kiswahili Prose and Novel
            'KISW 303',  # Morphology and Morphophonemics
            'RELI 340',  # Church History I
            'RELI 362',  # Religions & Contemporary Ethical Issues
            'FREN 350',  # Issues in African, Caribbean & Malagasy literature
            'FREN 344',  # Varieties of Contemporary French
            'FREN 345',  # Translation & Interpreting
            'GEOG 300',  # Geography of Africa
            'GEOG 314',  # Biogeography
            'BCOM 314',  # Management Accounting I
            'BCOM 351',  # Organizational Theory
            'AGRI 351',  # Annual Crops
            'ANSC 333',  # Animal Nutrition and Livestock Feeding
            'SOIL 320',  # Soil Fertility and Nutrition
            'AGRI 322',  # Plant Breeding
            'PESP 331',  # Nutrition and Sports Performance
            'PESP 311',  # Aerobics, Dance and Gymnastics
            'HOSC 341',  # Pattern Drafting & Clothing Construction
            'HOSC 321',  # Housing the Family
            'COSC 371',  # E-Commerce
            'COSC 331',  # Essentials of Object Oriented Systems
            'MATH 322',  # Ordinary Differential Equations I (CORE UNIT)
            'MATH 302',  # Real Analysis I (CORE UNIT)
            'MATH 301',  # Linear Algebra II
            'MATH 344',  # Theory of Estimation
            'MATH 342',  # Quality Control Methods
            'MATH 343',  # Applied Statistics
            'MATH 324',  # Dynamics
            'MATH 325',  # Fluid Mechanics I
        ],
    },
    {
        "program_guess": 'Bachelor of Education (Science)',
        "year": 3,
        "semester": 1,
        "header": 'GROUP: Bachelor of Education (Science), Y3S1 SEPT 2024',
        "codes": [
            'CHEM 323',  # Chemical Kinetics
            'CHEM 322',  # Physical Chemistry III
            'CHEM 332',  # Organic Chemistry III
            'CHEM 351',  # Forensic Chemistry
            'PHYS 336',  # Quantum Mechanics I
            'PHYS 342',  # Electricity & Magnetism II
            'PHYS 325',  # Physics Practical V
            'BOTA 322',  # Plant Growth and Development
            'ZOOL 301',  # Animal Systematic and Evolution
            'ZOOL 330',  # Animal Physiology
            'AGRI 351',  # Annual Crops
            'ANSC 333',  # Animal Nutrition and Livestock Feeding
            'SOIL 320',  # Soil Fertility and Nutrition
            'AGRI 322',  # Plant Breeding
            'COSC 371',  # E-Commerce
            'COSC 331',  # Essentials of Object Oriented Systems
            'MATH 322',  # Ordinary Differential Equations I (CORE UNIT)
            'MATH 302',  # Real Analysis I (CORE UNIT)
            'MATH 301',  # Linear Algebra II
            'MATH 344',  # Theory of Estimation
            'MATH 342',  # Quality Control Methods
            'MATH 343',  # Applied Statistics
            'MATH 324',  # Dynamics
            'MATH 325',  # Fluid Mechanics I
        ],
    },
    {
        "program_guess": 'Bachelor of Education (Arts)',
        "year": 4,
        "semester": 1,
        "header": 'GROUP: Bachelor of Education (Arts), Y4S1    SEPT 2022 INTAKE',
        "codes": [
            'ENGL 412',  # Advanced Description of Modern English
            'ENGL 411',  # Semantics and Pragmatics
            'LITT 424',  # Poetry from West & Southern Africa
            'LITT 431',  # European Literature I (From Classical to Middle Ages)
            'HIST 412',  # Philosophy of History
            'HIST 447',  # Themes in African Environmental History
            'KISW 402',  # Modern Kiswahili Poetry
            'KISW 404',  # Kiswahili Literature in Translation
            'RELI 450',  # New Christian Movements in Africa
            'RELI 475',  # Christian Theology in Africa
            'FREN 448',  # Semantics & Lexicology
            'FREN 441',  # Stylistics in French
            'GEOG 426',  # Regional Development
            'GEOG 433',  # Remote Sensing
            'BCOM 403',  # Strategic Management
            'BCOM 337',  # Financial Management I
            'ANSC 460',  # Non-Ruminant Production
            'AGBM 430',  # Agri-business Management I
            'BOTA 453',  # Plant Pathology
            'COSC 432',  # Human-Machine Interface
            'COSC 461',  # Computer Systems Security
            'COSC 440',  # Computational Intelligence
            'PESP 441',  # Outdoor Education and First Aid
            'PESP 442',  # Prevention and Management of Sports Injuries
            'MATH 421',  # Partial Differential Equations I (CORE UNIT)
            'MATH 401',  # Topology I (CORE UNIT)
            'MATH 411',  # Differential Geometry
            'MATH 452',  # Test of Hypothesis
            'MATH 443',  # Design and Analysis of Experiments I
            'MATH 424',  # Numerical Analysis II
            'MATH 428',  # Mathematical Modelling
        ],
    },
    {
        "program_guess": 'Bachelor of Education (Science)',
        "year": 4,
        "semester": 1,
        "header": 'GROUP: Bachelor of Education (Science), Y4S1     SEPT 2023 INTAKE',
        "codes": [
            'CHEM 417',  # Radiation And Nuclear Chemistry
            'CHEM 436',  # Advanced Stereochemistry and Reaction Mechanism
            'PHYS 483',  # Solid State Physics
            'PHYS 484',  # Atomic and Nuclear Physics
            'PHYS 411',  # Physics Practical VII
            'BOTA 453',  # Plant Pathology
            'BOTA 473',  # Plant Biochemistry
            'ZOOL 430',  # Comparative Animal Physiology
            'ANSC 460',  # Non-Ruminant Production
            'AGBM 430',  # Agri-business Management I
            'COSC 432',  # Human-Machine Interface
            'COSC 461',  # Computer Systems Security
            'COSC 440',  # Computational Intelligence
            'MATH 421',  # Partial Differential Equations I (CORE UNIT)
            'MATH 401',  # Topology I (CORE UNIT)
            'MATH 411',  # Differential Geometry
            'MATH 452',  # Test of Hypothesis
            'MATH 443',  # Design and Analysis of Experiments I
            'MATH 423',  # Numerical Analysis II
            'MATH 428',  # Mathematical Modelling
        ],
    },
]


def _strip_group_suffix_base(code: str) -> str:
    """
    Base course code with any trailing student-group letter suffix
    removed (PESP 111-A / PESP111(B) / PESP 111 C -> PESP 111). Kept
    local (not imported from course_management.cod_panel) so this
    command has no dependency on a views module.
    """
    code = (code or "").strip()
    m = re.match(r"^(.*?\d+)\s*\(\s*([A-Za-z]+)\s*\)\s*$", code, re.I)
    if m and re.search(r"\d", m.group(1)):
        return m.group(1).strip()
    m = re.match(r"^(.*?\d+)\s*[-/_]?\s*([A-Za-z]+)\s*$", code, re.I)
    if m and re.search(r"\d", m.group(1)):
        return m.group(1).strip()
    return code


def _resolve_program(program_guess: str, overrides: dict) -> Program:
    """Return exactly one Program, or raise (with diagnostics)."""
    if program_guess in overrides:
        name = overrides[program_guess]
        return Program.objects.get(name=name)

    include, exclude = PROGRAM_RESOLUTION_KEYWORDS.get(program_guess, ([], []))
    qs = Program.objects.all()
    for kw in include:
        qs = qs.filter(name__icontains=kw)
    for kw in exclude:
        qs = qs.exclude(name__icontains=kw)

    count = qs.count()
    if count == 1:
        return qs.first()
    if count == 0:
        raise Program.DoesNotExist(
            f"No Program matched keywords include={include} exclude={exclude}."
        )
    raise Program.MultipleObjectsReturned(
        f"{count} Programs matched keywords include={include} exclude={exclude}: "
        + ", ".join(qs.values_list('name', flat=True))
    )


class Command(BaseCommand):
    help = (
        "Combine per-student-group CourseAllocation rows for courses listed in the "
        "'individual specialization' tables of the DEDU allocation sheet (i.e. every "
        "GROUP: table that is NOT a COMMON COURSES table) into one CombinedCourseGroup "
        "per curriculum entry, across all departments. Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--program-guess", type=str, default=None,
            help="Only process sections whose seeded program_guess matches this (e.g. 'Bachelor of Education (Arts)').",
        )
        parser.add_argument("--year", type=int, default=None, help="Only process this year (1-4).")
        parser.add_argument("--semester", type=int, default=None, help="Only process this semester (1 or 2).")
        parser.add_argument(
            "--course-code", type=str, default=None,
            help="Only process this one seeded course code (e.g. 'BCOM 101') — useful before a full --apply.",
        )
        parser.add_argument(
            "--program-map", type=str, default=None,
            help='JSON object mapping a seeded program_guess to the exact Program.name in the DB, '
                 'e.g. \'{"Bachelor of Education (Arts)": "Bachelor of Education (Arts)"}\'. Takes precedence over '
                 'PROGRAM_NAME_OVERRIDES in this file.',
        )
        parser.add_argument(
            "--created-by", type=str, default=None,
            help="Username to record as CombinedCourseGroup.created_by (optional).",
        )
        parser.add_argument(
            "--list-programs", action="store_true",
            help="Print every Program in the database (id, name) and exit — nothing else is done.",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Write changes. Without this flag, only reports what would happen.",
        )

    def handle(self, *args, **options):
        if options["list_programs"]:
            for p in Program.objects.order_by("name"):
                self.stdout.write(f"  [{p.id}] {p.name}")
            return

        apply_changes = options["apply"]

        overrides = dict(PROGRAM_NAME_OVERRIDES)
        if options["program_map"]:
            try:
                overrides.update(json.loads(options["program_map"]))
            except json.JSONDecodeError as exc:
                raise CommandError(f"--program-map is not valid JSON: {exc}")

        created_by_user = None
        if options["created_by"]:
            try:
                created_by_user = User.objects.get(username=options["created_by"])
            except User.DoesNotExist:
                raise CommandError(f"No user {options['created_by']!r}.")

        sections = SEED_SECTIONS
        if options["program_guess"]:
            sections = [s for s in sections if s["program_guess"] == options["program_guess"]]
            if not sections:
                raise CommandError(
                    f"{options['program_guess']!r} doesn't match any seeded program_guess. "
                    f"Valid values: {sorted({s['program_guess'] for s in SEED_SECTIONS})}"
                )
        if options["year"] is not None:
            sections = [s for s in sections if s["year"] == options["year"]]
        if options["semester"] is not None:
            sections = [s for s in sections if s["semester"] == options["semester"]]

        wanted_key = canonical_course_key(options["course_code"]) if options["course_code"] else None

        self.stdout.write(f"{len(sections)} seeded section(s) to process.")
        self.stdout.write(
            self.style.WARNING("DRY RUN — pass --apply to write changes.") if not apply_changes
            else self.style.SUCCESS("APPLY MODE — changes will be written.")
        )
        self.stdout.write("")

        program_cache = {}
        n_created = n_extended = n_skipped_single = n_skipped_nomatch = n_conflicts = n_program_errors = 0

        for section in sections:
            guess = section["program_guess"]
            self.stdout.write(f"=== {section['header']} ===")

            if guess not in program_cache:
                try:
                    program_cache[guess] = _resolve_program(guess, overrides)
                except (Program.DoesNotExist, Program.MultipleObjectsReturned) as exc:
                    program_cache[guess] = None
                    self.stdout.write(self.style.ERROR(
                        f"  Could not resolve program {guess!r}: {exc} "
                        f"— add it to PROGRAM_NAME_OVERRIDES or pass --program-map. Skipping this section."
                    ))
            program = program_cache[guess]
            if program is None:
                n_program_errors += 1
                self.stdout.write("")
                continue

            for raw_code in section["codes"]:
                key = canonical_course_key(raw_code)
                if wanted_key and key != wanted_key:
                    continue
                display_code = normalize_code(raw_code)

                pc_rows = ProgramCourse.objects.filter(
                    program=program, year=section["year"], semester=section["semester"]
                ).values_list("id", "course_code")
                # canonical_course_key comparison in Python, not SQL — course_code
                # spacing/punctuation in the DB isn't guaranteed to match the sheet.
                matching_pc_ids = [pc_id for pc_id, code in pc_rows if canonical_course_key(code) == key]

                if not matching_pc_ids:
                    n_skipped_nomatch += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  {display_code}: no ProgramCourse curriculum entry found for "
                            f"{program.name} Y{section['year']}S{section['semester']} — skipped"
                        )
                    )
                    continue

                allocs = list(
                    CourseAllocation.objects.filter(program_course_id__in=matching_pc_ids)
                    .select_related("lecturer", "department")
                )
                by_dept = defaultdict(list)
                for a in allocs:
                    by_dept[a.department_id].append(a)

                if not allocs:
                    n_skipped_nomatch += 1
                    self.stdout.write(
                        self.style.WARNING(f"  {display_code}: curriculum entry exists but has no CourseAllocation rows — skipped")
                    )
                    continue

                for dept_id, dept_allocs in by_dept.items():
                    department = dept_allocs[0].department

                    free_allocs, conflicts = [], []
                    for a in dept_allocs:
                        other = a.combined_groups.exclude(base_course_code=display_code).first()
                        (conflicts if other else free_allocs).append((a, other) if other else a)

                    for a, other in conflicts:
                        n_conflicts += 1
                        self.stdout.write(self.style.WARNING(
                            f"    ! {a.course_code} (id={a.id}, {department.name}) already in combined group "
                            f"'{other.group_code}' — left alone"
                        ))

                    existing_group = (
                        CombinedCourseGroup.objects.filter(department=department)
                        .filter(allocations__in=[a.id for a in free_allocs])
                        .distinct()
                        .first()
                    ) if free_allocs else None

                    already_in_group_ids = (
                        set(existing_group.allocations.values_list("id", flat=True)) if existing_group else set()
                    )
                    to_add = [a for a in free_allocs if a.id not in already_in_group_ids]
                    total_after = len(already_in_group_ids) + len(to_add)

                    if total_after < 2:
                        if len(dept_allocs) > 1 or existing_group:
                            n_skipped_single += 1
                        self.stdout.write(f"  {display_code} [{department.name}]: only {total_after} allocation row(s) — nothing to combine")
                        continue

                    if not to_add:
                        self.stdout.write(f"  {display_code} [{department.name}]: already fully combined ({total_after} rows) — nothing to do")
                        continue

                    action = "extend existing" if existing_group else "create new"
                    row_desc = ", ".join(
                        f"id={a.id}({a.lecturer.display_name if a.lecturer_id else 'unassigned'})" for a in to_add
                    )
                    self.stdout.write(f"  {display_code} [{department.name}]: {action} combined group — adding [{row_desc}]")

                    if not apply_changes:
                        continue

                    with transaction.atomic():
                        group = existing_group
                        if group is None:
                            lecturer_ids = {a.lecturer_id for a in to_add if a.lecturer_id}
                            lecturer_obj = to_add[0].lecturer if len(lecturer_ids) == 1 else None

                            group_code = f"{display_code.replace(' ', '_')}_COMBINED"
                            base_group_code, suffix = group_code, 1
                            while CombinedCourseGroup.objects.filter(group_code=group_code).exists():
                                suffix += 1
                                group_code = f"{base_group_code}_{suffix}"

                            group = CombinedCourseGroup.objects.create(
                                group_code=group_code,
                                base_course_code=display_code,
                                lecturer=lecturer_obj,
                                department=department,
                                origin_department=department,
                                created_by=created_by_user,
                            )
                            n_created += 1
                        else:
                            n_extended += 1

                        group.allocations.add(*to_add)
                        if not group.primary_allocation_id:
                            group.primary_allocation = group.allocations.order_by("id").first()
                        group.save()

            self.stdout.write("")

        self.stdout.write(self.style.SUCCESS(
            f"Groups created: {n_created} | extended: {n_extended} | "
            f"already single (skipped): {n_skipped_single} | no DB match (skipped): {n_skipped_nomatch} | "
            f"rows left in another group (conflict): {n_conflicts} | "
            f"sections skipped (program not resolved): {n_program_errors}"
        ))
        if not apply_changes:
            self.stdout.write(self.style.WARNING("Nothing was written — re-run with --apply to save these changes."))
