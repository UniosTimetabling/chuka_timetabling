"""
course_allocation/cohort_utils.py
==================================
Shared utilities for cohort-based curriculum versioning, used by every
auto-allocator in the system: /cod/ (auto_allocate_courses.py), Campuses
(campuses_timetable/auto_allocate.py), ODEL (odel_system/auto_allocate.py),
Resits (resits_timetabling/auto_allocate.py) and Lab/Workshop
(course_allocation/lab_auto_allocate.py).

A "cohort" is one intake -- every student admitted into a program in the
same calendar year (ProgramEnrollment.entry_year). Two cohorts of the same
program can legitimately be following two *different* curricula at once
(e.g. the department revised the course list starting with the 2026
intake, while the 2024 and 2025 intakes keep sitting the units they
started with). ProgramCourse rows carry that curriculum version in
``student_cohort`` (unique_together = program + course_code +
student_cohort), so this module is what maps "which cohort" to "which
curriculum version" and pulls the right ProgramCourse rows for each one.

Resolution rule for a cohort's curriculum version:
  1. Exact match -- student_cohort == str(entry_year).
  2. Otherwise, the closest older version that exists.
  3. Otherwise, the legacy version ('0').

Every auto-allocator MUST build its course list through this module rather
than querying ProgramCourse directly by (program, year, semester) --
querying directly ignores student_cohort entirely and will mix courses
from every curriculum version a program has ever had into one list,
double-counting course codes that were re-tagged between versions.
"""

from program_management.models import ProgramCourse, Program
from .models import ProgramEnrollment, AcademicYearTracker


# ---------------------------------------------------------------------------
# Curriculum-version resolution
# ---------------------------------------------------------------------------

def get_cohort_curriculum_map(program, reference_year=None):
    """
    Returns {entry_year: student_cohort_value} for every entry-year that
    has at least one ProgramEnrollment row for this program.
    """
    if reference_year is None:
        reference_year = AcademicYearTracker.get_current().current_year

    existing_cohorts = set(
        ProgramCourse.objects.filter(program=program)
        .values_list("student_cohort", flat=True)
        .distinct()
    )

    entry_years = sorted(
        ProgramEnrollment.objects.filter(program=program)
        .values_list("entry_year", flat=True)
        .distinct(),
        reverse=True,
    )

    cohort_map = {}
    for entry_year in entry_years:
        if str(entry_year) in existing_cohorts:
            cohort_map[entry_year] = str(entry_year)
            continue
        found = None
        for year in range(entry_year - 1, 2000, -1):
            if str(year) in existing_cohorts:
                found = str(year)
                break
        cohort_map[entry_year] = found or "0"
    return cohort_map


def get_courses_for_cohort(program, cohort_value, year_of_study, semester):
    """ProgramCourse queryset for one cohort's curriculum version/year/semester."""
    return ProgramCourse.objects.filter(
        program=program,
        student_cohort=cohort_value,
        year=year_of_study,
        semester=semester,
    )


# ---------------------------------------------------------------------------
# Program / department level rollups -- what every auto-allocator wants
# ---------------------------------------------------------------------------

def get_cohort_courses_for_program(program, semesters, reference_year=None):
    """
    All (cohort, course) pairs for one program across the given semesters.

    ``semesters`` accepts a single int or an iterable of ints.

    Returns a list of dicts:
      {program_course, program, entry_year, cohort_value, year_of_study, students}
    """
    if reference_year is None:
        reference_year = AcademicYearTracker.get_current().current_year
    if isinstance(semesters, int):
        semesters = [semesters]

    enrollments = list(ProgramEnrollment.objects.filter(program=program))
    if not enrollments:
        return []

    cohort_map = get_cohort_curriculum_map(program, reference_year)
    result = []

    for enrollment in enrollments:
        entry_year = enrollment.entry_year
        students = enrollment.number_of_students
        year_of_study = reference_year - entry_year + 1
        if year_of_study < 1 or year_of_study > 6:
            continue

        cohort_value = cohort_map.get(entry_year, "0")

        for semester in semesters:
            for pc in get_courses_for_cohort(program, cohort_value, year_of_study, semester):
                result.append({
                    "program_course": pc,
                    "program": program,
                    "entry_year": entry_year,
                    "cohort_value": cohort_value,
                    "year_of_study": year_of_study,
                    "students": students,
                })
    return result


def get_cohort_courses_for_department(department, semesters, reference_year=None, program_id=None):
    """Same as get_cohort_courses_for_program but rolled up across every
    program in the department (or one program, if program_id is given)."""
    if reference_year is None:
        reference_year = AcademicYearTracker.get_current().current_year

    programs_qs = Program.objects.filter(department=department)
    if program_id:
        programs_qs = programs_qs.filter(id=program_id)

    all_courses = []
    for program in programs_qs:
        all_courses.extend(
            get_cohort_courses_for_program(program, semesters, reference_year)
        )
    return all_courses


# ---------------------------------------------------------------------------
# Tree building -- the Program -> Cohort -> Courses JSON shape every
# auto-allocate "tree" endpoint (Campus / ODEL / Resits / Lab) serves to
# its checkbox-tree frontend.
# ---------------------------------------------------------------------------

def build_cohort_tree(cohort_items, course_extra_fn=None):
    """
    cohort_items: list of dicts as returned by get_cohort_courses_for_department
                  / get_cohort_courses_for_program.
    course_extra_fn: optional callable(item) -> dict, merged into each
                  course's output entry. Use this to attach
                  already_allocated/lecturer/locked/etc without this
                  module needing to know about any allocation model.

    Returns the ``programs`` list ready to serialize as JSON:
      [{
        "program_id": ..., "program_name": ...,
        "cohorts": [{
          "entry_year": ..., "cohort_value": ..., "year_of_study": ...,
          "students": ..., "courses": [{...}, ...]
        }, ...]
      }, ...]
    """
    tree = {}
    for item in cohort_items:
        pc = item["program_course"]
        program = item["program"]

        prog_bucket = tree.setdefault(program.id, {
            "program_id": program.id,
            "program_name": program.name,
            "cohorts": {},
        })
        cohort_bucket = prog_bucket["cohorts"].setdefault(item["entry_year"], {
            "entry_year": item["entry_year"],
            "cohort_value": item["cohort_value"],
            "year_of_study": item["year_of_study"],
            "students": item["students"],
            "courses": [],
        })

        course_entry = {
            "program_course_id": pc.id,
            "course_code": pc.course_code,
            "course_name": pc.course_name,
            "semester": pc.semester,
            "year": pc.year,
        }
        if course_extra_fn:
            course_entry.update(course_extra_fn(item) or {})
        cohort_bucket["courses"].append(course_entry)

    programs_out = []
    for prog_bucket in sorted(tree.values(), key=lambda p: p["program_name"]):
        cohorts_out = [
            prog_bucket["cohorts"][entry_year]
            for entry_year in sorted(prog_bucket["cohorts"].keys(), reverse=True)
        ]
        programs_out.append({
            "program_id": prog_bucket["program_id"],
            "program_name": prog_bucket["program_name"],
            "cohorts": cohorts_out,
        })
    return programs_out
