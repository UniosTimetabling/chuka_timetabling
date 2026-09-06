# ==============================================================================
# course_allocation/config_helpers.py
#
# Central place the autoallocator reads all previously-hardcoded constants
# from. Resolution order per field: per-department AllocationConfig row ->
# GLOBAL row (department=None) -> hardcoded _FALLBACK below.
# ==============================================================================

from .models import AllocationConfig
import re

_FALLBACK = {
    "max_load_per_semester": 6,
    "max_load_enabled": True,
    "split_threshold": 200,      # Maximum students per group (hard limit)
    "split_extend_by": 0,        # How much threshold can be exceeded before splitting
    "splitting_enabled": True,
    "pg_designations": ["Dr", "Prof"],
    "allowed_semesters": [1, 2],
}


# ------------------------------------------------------------------------
# Shared-course disambiguation tag helper
# ------------------------------------------------------------------------
_COURSE_CODE_TAG_RE = re.compile(r'\([A-Z0-9]+\)\s*$', re.IGNORECASE)


def strip_course_code_tag(course_code):
    """Return course_code with a trailing '(TAG)' disambiguation suffix removed, if present."""
    if not course_code:
        return course_code
    return _COURSE_CODE_TAG_RE.sub('', course_code).strip()


# ------------------------------------------------------------------------
# Course code normalization
# ------------------------------------------------------------------------
# ProgramCourse.course_code is stored WITH a space between the letter
# prefix and the digits (e.g. "COSC 101", "COSC 0101", "COSC 00101" --
# degree / diploma / certificate levels of the "same" number are deliberately
# different courses, distinguished only by leading-zero depth, so we must
# NEVER strip or alter the digits themselves).
#
# Hand-prepared / externally-sourced CSVs (lecturer mappings, special-intake
# sheets, etc.) very often drop that space ("COSC101") and/or vary case.
# Previously nothing normalized for the *missing space* case: an exact match
# and a case-insensitive match both fail outright, and `icontains` is a
# substring test so "COSC101" is never a substring of "COSC 101" either --
# every space-dropped code silently fell through as "unmatched", even though
# the course clearly exists.
#
# normalize_course_code() strips ALL whitespace and upper-cases, so
# "COSC 101", "cosc101", " COSC  101 " all collapse to the same key
# ("COSC101"). This is safe because it never touches digits -- "COSC 0101"
# and "COSC 00101" still normalize to distinct keys ("COSC0101" vs
# "COSC00101"), so the certificate/diploma/degree distinction is preserved.
_WHITESPACE_RE = re.compile(r'\s+')


def normalize_course_code(course_code):
    """Return course_code with all whitespace removed and upper-cased.

    Used as a matching key only (never for display/storage) so that
    "COSC 101", "cosc101", and "COSC101" are all treated as the same code,
    without collapsing genuinely different codes that differ only in
    leading-zero digits (e.g. "COSC 101" vs "COSC 0101").
    """
    if not course_code:
        return ''
    return _WHITESPACE_RE.sub('', course_code).strip().upper()


def get_config(department):
    """
    Return a plain dict of effective config values for `department`.
    """
    dept_cfg = AllocationConfig.objects.filter(department=department).first() if department else None
    global_cfg = AllocationConfig.objects.filter(department__isnull=True).first()

    def resolve(field, list_field=False):
        for cfg in (dept_cfg, global_cfg):
            if cfg is not None:
                if field == "pg_designations" and list_field:
                    return cfg.pg_designation_list()
                if field == "allowed_semesters" and list_field:
                    return cfg.allowed_semester_list()
                return getattr(cfg, field)
        return _FALLBACK[field]

    return {
        "max_load_per_semester": resolve("max_load_per_semester"),
        "max_load_enabled":      resolve("max_load_enabled"),
        "split_threshold":       resolve("split_threshold"),
        "split_extend_by":       resolve("split_extend_by"),
        "splitting_enabled":     resolve("splitting_enabled"),
        "pg_designations":       resolve("pg_designations", list_field=True),
        "allowed_semesters":     resolve("allowed_semesters", list_field=True),
    }


def get_lecturer_max_load(lecturer, config):
    """
    Per-lecturer override beats department/global config.
    Returns None to mean "unlimited" when max_load_enabled is False.
    """
    if not config["max_load_enabled"]:
        return None
    override = getattr(lecturer, "max_load_override", None)
    return override if override else config["max_load_per_semester"]


def make_group_code(course_code, index):
    """
    Unlimited split groups (spreadsheet-column style).
    index is 0-based.
      0-25  -> A .. Z
      26+   -> AA, AB, AC, ...
    """
    n = index + 1
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return f"{course_code}-{letters}"


def calculate_split_groups(student_count, threshold, extend_by=0):
    """
    Intelligently calculate how to split students into groups.
    
    Args:
        student_count: Total number of students
        threshold: Maximum students per group (hard limit)
        extend_by: How much threshold can be exceeded before triggering a split
    
    Returns:
        list of group sizes, or None if no split needed
        
    Examples:
        threshold=200, extend_by=20:
        - 200 students → No split (200 <= 200)
        - 210 students → No split (210 <= 220) 
        - 220 students → No split (220 <= 220)
        - 221 students → Split: 110 + 111 (ceil(221/200) = 2 groups)
        - 300 students → Split: 150 + 150 (2 groups)
        - 410 students → Split: 137 + 137 + 136 (ceil(410/200) = 3 groups)
        - 600 students → Split: 200 + 200 + 200 (3 groups)
        - 601 students → Split: 151 + 150 + 150 + 150 (4 groups)
    """
    # If below or at threshold + extend, no split needed
    if student_count <= threshold + extend_by:
        return None
    
    # Calculate minimum number of groups needed
    # Each group must have at most 'threshold' students
    num_groups = (student_count + threshold - 1) // threshold  # Ceiling division
    
    # Calculate balanced group sizes
    base_size = student_count // num_groups
    remainder = student_count % num_groups
    
    # Create groups: first 'remainder' groups get base_size + 1, rest get base_size
    groups = []
    for i in range(num_groups):
        size = base_size + (1 if i < remainder else 0)
        groups.append(size)
    
    return groups