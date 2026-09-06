"""
resits_timetabling/faculty_utils.py
====================================
Small shared helper for resolving an imported "faculty" column (CSV/Excel)
to a real faculty_management.Faculty row.

Used by both resit import entry points:
    /resits/admin/import/   (admins/resit_import_admin.py)
    /resits/import/         (resits_timetabling/resit_import.py)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Column header aliases accepted for the faculty column in an uploaded
# CSV/Excel file. Headers are lower-cased + spaces→underscores before
# matching (see _parse_file()/row normalisation in the import views).
FACULTY_ALIASES = {"faculty", "faculty_name", "faculty_code", "school", "school_name"}


def extract_faculty_name(row: dict) -> str:
    """Pull the faculty value out of a normalised row dict, if present."""
    for k, v in row.items():
        k2 = str(k).strip().lower().replace(" ", "_")
        if k2 in FACULTY_ALIASES and v:
            return str(v).strip()
    return ""


def resolve_faculty(faculty_name: str):
    """
    Resolve a free-text faculty name from an import file to a real Faculty
    row. Tries an exact (case-insensitive) match first, then falls back to
    a contains-match (so e.g. "Science" matches "Faculty of Science").
    Returns None if nothing matches or no name was supplied.
    """
    if not faculty_name:
        return None

    from faculty_management.models import Faculty

    name = faculty_name.strip()
    if not name:
        return None

    faculty = Faculty.objects.filter(name__iexact=name).first()
    if faculty:
        return faculty

    faculty = Faculty.objects.filter(name__icontains=name).first()
    if faculty:
        return faculty

    logger.warning("resit import: faculty '%s' not found — leaving unset (will auto-derive from department).", faculty_name)
    return None
