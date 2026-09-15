# program_management/code_utils.py
"""
Single source of truth for ProgramCourse.course_code normalization.

Every code path that CREATES or MATCHES a ProgramCourse.course_code must
go through these two functions instead of rolling its own
.strip()/.upper(). Before this module existed, four different places
each did their own (subtly different) cleanup:

    cod_panel.py (manual "add curriculum course")   strip().upper() + space-insert
    api/ajax_program_courses.py (_add_course/_edit) only .strip()
    program_management/admin.py (import-export)     .strip().upper()  -- no space fix
    program_management/import_helpers.py (CSV/Celery) .strip().upper() -- no space fix
    program_management/export_import_programs.py    just .strip()

...which let "BCOM112", "BCOM 112", and "bcom112" all get stored as
distinct rows for the same (program, student_cohort), even though
ProgramCourse.Meta.unique_together = ("program", "course_code",
"student_cohort") is a literal string comparison and has no idea
they're "the same" course.

Two functions:
  - normalize_code(code): the DISPLAY/STORAGE form. Upper-cases, strips,
    and inserts a single space between a leading letter-run and the
    digit-run that follows it ("bcom112" -> "BCOM 112"). This is what
    gets written to the database.
  - canonical_course_key(code): the MATCHING form. Strips everything
    down to bare uppercase alphanumerics so "BCOM 112", "BCOM112",
    "BCOM-112", "BCOM_112" all compare equal. Never stored or
    displayed -- lookups/dedup only.
"""
import re

__all__ = ["normalize_code", "canonical_course_key", "base_course_key"]


def normalize_code(code: str) -> str:
    """
    Canonical DISPLAY form for a course code: stripped, upper-cased,
    with a single space inserted between the leading letters and the
    digits that follow (e.g. 'bcom112' / 'BCOM  112' / 'bcom 112' all
    become 'BCOM 112').
    """
    if not code:
        return ""
    code = code.strip().upper()
    # Collapse any run of internal whitespace first so 'BCOM   112'
    # doesn't survive as multiple spaces after the regex below.
    code = re.sub(r"\s+", " ", code)
    code = re.sub(r"^([A-Z]+)\s*(\d+)", r"\1 \2", code)
    return code


def canonical_course_key(code: str) -> str:
    """
    Collapse a course code down to bare uppercase alphanumerics so that
    'BCOM 112', 'BCOM112', 'BCOM-112', 'BCOM_112' all compare equal.
    Used only for matching/deduplication, never for storage/display.
    """
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def base_course_key(code: str) -> str:
    """
    Looser than canonical_course_key(): keeps only the leading letter
    prefix + digit run and discards everything after it, so
    'COSC 103', 'COSC103', 'COSC-103', 'COSC 103 Y1S1' and
    'COSC 103 (GROUP B)' all collapse to 'COSC103'.

    This exists ONLY for the one-off
    cleanup_unlinked_program_course_duplicates management command, which
    cleans up rows where a group letter, "Y1S1"-style year/semester tag,
    or other note got typed straight into course_code by a bad manual
    entry or import instead of living in its own field. It is
    intentionally more aggressive than canonical_course_key() -- two
    codes sharing a prefix+digit run are assumed to be the same course
    -- so it must never be used for everyday matching/display, only for
    a human-reviewed cleanup pass.

    Falls back to canonical_course_key() when no letters+digits pattern
    is found, so an odd code is never silently dropped from grouping.
    """
    if not code:
        return ""
    cleaned = re.sub(r"\s+", " ", code.strip().upper())
    match = re.match(r"^([A-Z]+)\s*[-_ ]?\s*(\d{2,5})", cleaned)
    if match:
        return f"{match.group(1)}{match.group(2)}"
    return canonical_course_key(cleaned)
