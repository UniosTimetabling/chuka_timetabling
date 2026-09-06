"""
export_import/student_reg_lookup.py
=====================================

Turns what a student actually has in hand — a registration number like
"EB1/66791/23", a program code, or a program name — into the
(department, program, year) scope that export_import.program_year_pdf_cache
knows how to serve.

Registration number format assumed:  <PROGRAM_CODE>/<ADMISSION_NO>/<INTAKE_YY>
e.g. EB1/66791/23  →  program code "EB1", intake year 2023.

Year of study is derived from the intake year against
course_allocation.models.AcademicYearTracker.current_year — the SAME
global "clock" every other year-of-study computation in this codebase
uses (course_allocation/program_enrollment.py,
course_allocation/cohort_utils.py and everything the auto-allocators
build on). That tracker is a manually-advanced value, not
`timezone.now().year`: COD/Admin only bump it forward when the new
academic year's Semester 1 actually starts, so an intake stays "Year 1"
through its Semester 2 even after the calendar has ticked over to the
next January — e.g. the 2026 intake is still Year 1 in Semester 2 even
once the calendar reads 2027, because the tracker isn't advanced until
Semester 1 of the *next* academic year begins. Whichever intake the
tracker currently counts as "Year 1" is First Years, the one before it
is Second Years, and so on.

Previously this used `timezone.now().year` directly, which drifts out
of sync with the tracker for exactly the Semester-2-after-New-Year
window described above — that mismatch is what made the mobile API
compute the wrong `year` and come back with "no courses" for scopes
that the COD dashboard (which reads the tracker) resolved just fine.
"""
import re

# Loose on purpose: program codes are short alnum tokens (EB1, BCOM,
# BSC-CS, ...), the middle segment is whatever the registrar uses
# (numeric admission number, sometimes with letters), and the last
# segment is a 2- or 4-digit intake year.
_REG_NUMBER_RE = re.compile(
    r"^\s*(?P<code>[A-Za-z][A-Za-z0-9\-]{0,14})\s*/\s*.+/\s*(?P<year>\d{2}|\d{4})\s*$"
)


def parse_registration_number(raw):
    """Returns (program_code: str, intake_year: int) or None if `raw`
    doesn't look like "<CODE>/.../<YY or YYYY>"."""
    if not raw:
        return None
    m = _REG_NUMBER_RE.match(raw.strip())
    if not m:
        return None

    program_code = m.group("code").upper()
    year_str = m.group("year")
    intake_year = int(year_str) if len(year_str) == 4 else 2000 + int(year_str)
    return program_code, intake_year


def year_of_study_from_intake(intake_year, reference_year=None):
    """Which year of study a cohort that started in `intake_year` is in
    right now, on the AcademicYearTracker convention described at the
    top of this file.

    `reference_year` is an explicit override for callers that already
    have one (tests, scripts pinning a particular year); leave it None
    in normal request-handling code so this always tracks the same
    global "clock" the rest of the system uses.
    """
    if reference_year is None:
        from course_allocation.models import AcademicYearTracker
        reference_year = AcademicYearTracker.get_current().current_year
    return reference_year - intake_year + 1


def resolve_program_by_code(code):
    """Program whose ProgramCode.code matches, case-insensitively."""
    from program_management.models import ProgramCode

    if not code:
        return None
    pc = (
        ProgramCode.objects
        .filter(code__iexact=code.strip())
        .select_related("program", "program__department")
        .first()
    )
    return pc.program if pc else None


def resolve_program_by_name(name):
    """Program by exact (case-insensitive) name match, falling back to a
    substring match ONLY if it's unambiguous — an ambiguous partial
    match returns None rather than guessing which program was meant."""
    from program_management.models import Program

    name = (name or "").strip()
    if not name:
        return None

    program = Program.objects.filter(name__iexact=name).select_related("department").first()
    if program:
        return program

    matches = list(Program.objects.filter(name__icontains=name).select_related("department")[:2])
    return matches[0] if len(matches) == 1 else None
