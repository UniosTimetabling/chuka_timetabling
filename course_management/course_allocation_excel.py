"""
course_management/course_allocation_excel.py

Official "Course Allocation Template" — Excel export/import for the COD panel.

WHY THIS FILE EXISTS
---------------------
CODs currently allocate courses one-by-one on screen, which is slow and error
prone (wrong lecturer picked because two lecturers share a similar name,
courses forgotten, etc). This gives the COD a single Excel workbook that:

  1. EXPORT — lists every course allocation that already exists for the
     chosen semester (optionally topped up with any course from the
     department's own curriculum that has NOT been allocated yet), grouped
     visually by Program + Year, with one merged section header per group.

  2. IMPORT — the COD edits that same file (fixes lecturer emails, student
     numbers, elective groups, combination stems, etc.) and re-uploads it.
     The system recognises every row (via hidden identity columns baked
     into the sheet — invisible to the COD, but read on import) and
     creates/updates CourseAllocation rows accordingly, instead of asking
     the COD to re-enter everything by hand.

KEY DESIGN DECISIONS
---------------------
* Lecturers are matched by EMAIL, never by name — two lecturers can share a
  name, but email is unique on the Lecturer model. This is called out
  explicitly to the COD in the "How To Use" sheet and in the column header.

* Every data row carries a set of HIDDEN columns (CourseAllocation ID,
  ProgramCourse ID, Department IDs, StudentGroup ID, SelectionGroup ID,
  SpecializationStem ID). These are the source of truth on import — the
  human-readable columns (course code, department name, lecturer email...)
  are what the COD edits, but identity always resolves through the hidden
  ID first, then falls back to name-matching for rows that were added by
  hand (new rows the COD typed in) or for the "missing course" rows we
  generated (which have no CourseAllocation yet, so no allocation ID).
  This is what lets a re-upload be understood safely instead of guessing
  from text alone.

* A "common unit" (e.g. an Education unit taken by students following
  several different Combination Stems, or by more than one Programme) is
  NOT collapsed into a single row. Every (Programme, Year, Student Group,
  Combination Stem) combination that takes the course gets its OWN row,
  even though the Course Code repeats. This is what avoids the collision
  the COD described: two different stems/groups taking the "same" course
  code stay on separate rows, each with its own registration number, and
  the hidden identity columns keep them distinct on import.
"""

import datetime
import io
import logging
import re
import zipfile

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.rbac import allowed_roles, Role, user_has_role
from core.department_recipients import (
    resolve_department_email_recipients,
    filter_recipients_by_audience,
    parse_custom_emails,
)
from department_management.models import Department
from lecturer_portal.models import Lecturer
from program_management.models import Program, ProgramCourse, ProgramCode
from program_management.code_utils import normalize_code, canonical_course_key
from course_allocation.models import (
    CourseAllocation,
    StudentGroup,
    SelectionGroup,
    SpecializationStem,
    SpecializationCategory,
)
# reuse existing helpers — generate_unique_payroll is the same one the COD
# panel's own "new lecturer" inline flow uses, so an auto-created lecturer
# gets a payroll number in the same convention either path would produce.
from course_management.cod_panel import detect_user_department, generate_unique_payroll

logger = logging.getLogger(__name__)

# Roles allowed to use this feature — mirrors the rest of the COD panel.
ALLOCATION_TEMPLATE_ROLES = (
    Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN,
)

# ---------------------------------------------------------------------------
# Column layout. Order matters — the importer reads by header name, not
# position, so re-ordering visible columns by hand in Excel is safe, but
# DO NOT rename the header text or the importer will not recognise it.
# ---------------------------------------------------------------------------
VISIBLE_COLUMNS = [
    "No.",
    "Course Code",
    "Course Name",
    "Semester",
    "Origin Department",
    "Allocating Department",
    "Lecturer Email",
    "Number of Students",
    "Student Group",
    "Unit Type",
    "Elective Group",
    "Combination Stem",
    "Intake",
]

# Hidden identity columns — invisible in Excel, read on import.
HIDDEN_COLUMNS = [
    "_row_kind",              # "DATA" marks a real course row; anything else is skipped
    "_allocation_id",
    "_program_course_id",
    "_program_id",
    "_year",
    "_semester",
    "_department_id",
    "_origin_department_id",
    "_lecturer_id",
    "_student_group_id",
    "_selection_group_id",
    "_specialization_stem_id",
    # --- added with the "grouped by Combination Stem / Student Group" layout.
    # OPTIONAL on import (see OPTIONAL_HIDDEN_COLUMNS) so a file exported
    # before this layout existed still re-imports exactly as it used to.
    "_context_kind",          # which sub-block a row sits in: STEM / GROUP / SHARED / ELECTIVE / ""
    "_orig_sig",              # fingerprint of the editable cells at export time (see _edit_signature)
    "_is_copy",               # "COPY" when this row is a repeated echo of a row shown elsewhere
]
OPTIONAL_HIDDEN_COLUMNS = {"_context_kind", "_orig_sig", "_is_copy"}

ALL_COLUMNS = VISIBLE_COLUMNS + HIDDEN_COLUMNS
N_COLS = len(ALL_COLUMNS)
_COL_IDX = {name: i for i, name in enumerate(ALL_COLUMNS)}   # 0-based, for building rows

# ---------------------------------------------------------------------------
# "Special Request" sheet — separate, much smaller column set. Fill in
# exactly ONE of Lecturer Email / Program Code / Course Code per row to say
# who/what the request applies to (see _build_special_request_sheet).
# ---------------------------------------------------------------------------
SR_VISIBLE_COLUMNS = [
    "No.",
    "Lecturer Email",
    "Program Code",
    "Course Code",
    "Request",
    "Status",
]
SR_HIDDEN_COLUMNS = [
    "_row_kind",              # "DATA" marks a real row; anything else is skipped
    "_special_request_id",
]
SR_ALL_COLUMNS = SR_VISIBLE_COLUMNS + SR_HIDDEN_COLUMNS
SR_COLUMN_WIDTHS = {
    "No.": 6,
    "Lecturer Email": 30,
    "Program Code": 16,
    "Course Code": 20,
    "Request": 60,
    "Status": 14,
}

SECTION_FILL = "1F4E78"      # dark blue — program/year band
HEADER_FILL = "D9E1F2"       # light blue — column header row
DATA_ALT_FILL = "F2F2F2"     # light grey — alternate data rows
STEM_FILL = "2F75B5"         # mid blue  — "Combination Stem" sub-band
GROUP_FILL = "548235"        # green     — "Student Group" sub-band
SHARED_FILL = "DDEBF7"       # pale blue — "Shared units" sub-band (dark text)
ELECTIVE_FILL = "FFF2CC"     # pale gold — "Electives" sub-band (dark text)

# ---------------------------------------------------------------------------
# Column sizing. Widths are computed per export from the actual content
# (see _compute_visible_column_widths), clamped to these (min, max) bounds
# so one unusually long or short value can't make a column unusably wide or
# clip everyone else's. Columns not listed fall back to (10, 30).
# ---------------------------------------------------------------------------
COLUMN_WIDTH_BOUNDS = {
    "No.": (6, 8),
    "Course Code": (12, 18),
    "Course Name": (28, 48),
    "Semester": (10, 12),
    "Origin Department": (16, 32),
    "Allocating Department": (16, 32),
    "Lecturer Email": (22, 38),
    "Number of Students": (10, 16),
    "Student Group": (14, 26),
    "Unit Type": (10, 14),
    "Elective Group": (16, 30),
    "Combination Stem": (16, 30),
    "Intake": (10, 12),
}

# Short, code-like or numeric columns read better centered; the rest
# (names, emails, free text) read better left-aligned.
CENTERED_COLUMNS = {"No.", "Course Code", "Semester", "Number of Students", "Unit Type", "Intake"}


# Course codes that only differ by a trailing section-letter suffix are the
# SAME unit — just one particular section/group of it (e.g. "ECON 232-D" and
# "ECON 232", or "COMS 101_A" — the same convention used elsewhere in this
# codebase, see mobile_api/course_search.py's module docstring). Matches a
# trailing hyphen/underscore/space + a single letter at the very end of the
# code, case-insensitively.
_SECTION_SUFFIX_RE = re.compile(r"[\s_-]\s*[A-Za-z]$")


def _edit_signature(get):
    """Short fingerprint of every cell on a row a COD can meaningfully edit.
    `get(column_name)` returns the raw cell value. Written into the hidden
    `_orig_sig` column at export; on import it is recomputed from the
    current cells, and a row whose fingerprint differs from the stored one
    is one the COD actually EDITED. That is how the importer decides which
    copy to believe when the same allocation appears under several
    Combination Stems / Student Groups (see _build_section_blocks)."""
    import hashlib
    parts = []
    for name in ("Lecturer Email", "Number of Students", "Student Group", "Unit Type",
                 "Elective Group", "Combination Stem", "Intake",
                 "Origin Department", "Allocating Department"):
        v = get(name)
        if name == "Number of Students":
            try:
                v = int(v or 0)
            except (TypeError, ValueError):
                v = str(v)
        parts.append(str("" if v is None else v).strip().lower())
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _finish_row(values):
    """Append the three optional hidden context columns to a freshly built
    row (which is VISIBLE + the legacy hidden columns), computing _orig_sig
    from the visible values."""
    values = list(values)
    sig = _edit_signature(lambda n: values[VISIBLE_COLUMNS.index(n)])
    return values + ["", sig, ""]


def _display_group_key(course_code):
    """Key used ONLY to decide which rows are "the same unit" for the
    purposes of sitting together and sharing a "No." on this export (see
    _group_rows_by_course_code / _assign_row_numbers below). Strips a
    trailing section-letter suffix so 'ECON 232-D' groups with 'ECON 232'
    instead of being numbered as if it were a different course. This never
    touches the literal Course Code text written to the sheet, and is not
    used anywhere in the import path — ProgramCourse/CourseAllocation
    matching there still keys off the exact Course Code, unchanged."""
    code = (course_code or "").strip().upper()
    return _SECTION_SUFFIX_RE.sub("", code).strip()


def _group_rows_by_course_code(rows):
    """Reorder a section's rows alphabetically by Course Code (rather than
    the order the underlying query — or the "include missing courses"
    top-up, appended afterwards — happened to produce), with every row
    belonging to the same unit sitting together as one contiguous block.
    "Same unit" includes rows whose codes only differ by a trailing
    section-letter suffix (see _display_group_key) — e.g. 'ECON 232' and
    'ECON 232-D' are the same unit, not two different ones. The "No."
    column already numbers repeats as 1, 1a, 1b..., but that only reads
    well if those rows are actually next to each other on the sheet, which
    this guarantees."""
    distinct_keys = sorted({_display_group_key(row_values[1]) for row_values in rows})
    position = {key: idx for idx, key in enumerate(distinct_keys)}
    # Stable sort: rows sharing a unit keep their original relative order
    # (e.g. Student Group A before Group B), only the GROUPS get reordered
    # alphabetically.
    return sorted(rows, key=lambda row_values: position[_display_group_key(row_values[1])])


def _assign_row_numbers(rows):
    """Fill in the "No." column (index 0) for one program/year section's
    rows, in place. Each distinct unit (see _display_group_key — a trailing
    section-letter suffix like 'ECON 232-D' does not count as a different
    unit from 'ECON 232') gets the next sequential number, in the
    alphabetical order _group_rows_by_course_code already sorted them into.
    When a unit repeats within the SAME section (a common unit taken by
    more than one Student Group / Combination Stem, or split into
    sections), every occurrence shares that number but gets a letter
    suffix instead of its own number — 1a, 1b, 1c — so the count of
    distinct units on the sheet stays obvious at a glance."""
    counts = {}
    for row_values in rows:
        key = _display_group_key(row_values[1])
        counts[key] = counts.get(key, 0) + 1

    next_number = 1
    assigned_number = {}
    next_letter_index = {}
    for row_values in rows:
        key = _display_group_key(row_values[1])
        if key not in assigned_number:
            assigned_number[key] = next_number
            next_number += 1
        if counts[key] > 1:
            letter_idx = next_letter_index.get(key, 0)
            next_letter_index[key] = letter_idx + 1
            row_values[0] = f"{assigned_number[key]}{chr(ord('a') + letter_idx)}"
        else:
            row_values[0] = str(assigned_number[key])


def _build_section_blocks(sec):
    """Lay one Programme/Year/Semester section out as a list of blocks, IF
    the section involves any Combination Stem or Student Group; otherwise
    return None and the caller keeps the plain flat layout.

    For every Combination Stem / Student Group in the section the sheet
    gets its own sub-header (the name exactly as set on the system), then:

        <Stem / Group name>            <- sub-header band
          the stem's / group's OWN units
        Shared units                   <- sub-band
          every unit with no stem and no group (taken by everyone) —
          REPEATED under every stem/group, because each of them takes them
        Electives                      <- sub-band
          the section's elective units (Elective Group column says which pool)

    Rows that are only echoes of a row shown elsewhere carry
    `_is_copy = "COPY"` so the importer never double-counts them.

    Returns a list of block dicts:
        {"kind": STEM|GROUP|SHARED|ELECTIVE, "label": str,
         "stem_id": int|None, "group_id": int|None, "rows": [row_values...]}
    """
    entries = sec.get("entries") or []
    program_id = sec["program"].id
    if not entries:
        return None

    info = []   # one dict per entry: precomputed membership
    groups, stems = {}, {}
    for row_values, alloc, pc in entries:
        stem_ids, group_ids = set(), set()
        elective = bool(pc and pc.is_elective_type)
        if alloc is not None:
            elective = bool(alloc.is_elective)
            if alloc.student_group_id:
                group_ids.add(alloc.student_group_id)
                groups[alloc.student_group_id] = alloc.student_group
            for g in alloc.additional_student_groups.all():
                if g.program_id == program_id:
                    group_ids.add(g.id)
                    groups[g.id] = g
            if alloc.specialization_stem_id:
                stem_ids.add(alloc.specialization_stem_id)
                stems[alloc.specialization_stem_id] = alloc.specialization_stem
            for st in alloc.specialization_stems.all():
                if st.category.program_id == program_id:
                    stem_ids.add(st.id)
                    stems[st.id] = st
        info.append({"row": row_values, "alloc": alloc, "stem_ids": stem_ids,
                     "group_ids": group_ids, "elective": elective})

    if not groups and not stems:
        return None

    def _clone(item, kind, is_copy):
        r = list(item["row"])
        r[_COL_IDX["_context_kind"]] = kind
        r[_COL_IDX["_is_copy"]] = "COPY" if is_copy else ""
        return r

    plain = [it for it in info if not it["stem_ids"] and not it["group_ids"]]
    shared_items = [it for it in plain if not it["elective"]]
    elective_items = [it for it in plain if it["elective"]]

    def _elective_ok_for_group(it, g):
        alloc = it["alloc"]
        sg = alloc.selection_group if alloc is not None else None
        if sg is None:
            return True
        restricted = list(sg.restricted_to_groups.all())
        return (not restricted) or any(x.id == g.id for x in restricted)

    blocks, placed = [], set()

    def _add_block(kind, label, tag, stem_id, group_id, items, own_pred=None):
        if not items and kind in ("SHARED", "ELECTIVE"):
            return
        rows = []
        for it in items:
            is_copy = not (own_pred(it) if own_pred else False)
            rows.append(_clone(it, kind, is_copy))
            placed.add(id(it))
        rows = _group_rows_by_course_code(rows)
        _assign_row_numbers(rows)
        blocks.append({"kind": kind, "label": label, "tag": tag,
                       "stem_id": stem_id, "group_id": group_id, "rows": rows})

    ctx_list = (
        [("GROUP", g) for g in sorted(groups.values(), key=lambda g: (g.intake, g.letter, g.name))]
        + [("STEM", st) for st in sorted(stems.values(), key=lambda st: (st.category.name, st.name))]
    )
    for kind, obj in ctx_list:
        if kind == "GROUP":
            own = [it for it in info if obj.id in it["group_ids"]]
            own_pred = lambda it, o=obj: it["alloc"] is not None and it["alloc"].student_group_id == o.id
            electives = [it for it in elective_items if _elective_ok_for_group(it, obj)]
            _add_block("GROUP", obj.name, "GROUP", None, obj.id, own, own_pred)
        else:
            own = [it for it in info if obj.id in it["stem_ids"]]
            own_pred = lambda it, o=obj: it["alloc"] is not None and it["alloc"].specialization_stem_id == o.id
            electives = elective_items
            _add_block("STEM", obj.name, "STEM", obj.id, None, own, own_pred)
        _add_block("SHARED", "Shared units — taken by everyone in " + obj.name, "",
                   None, None, shared_items)
        _add_block("ELECTIVE", "Electives — " + obj.name, "", None, None, electives)

    # Safety net: nothing may silently vanish from the export.
    leftovers = [it for it in info if id(it) not in placed]
    if leftovers:
        _add_block("SHARED", "Other units", "", None, None, leftovers)
    return blocks


def _compute_visible_column_widths(sections):
    """Return one Excel column width per VISIBLE_COLUMNS entry, sized to fit
    the longest value actually being written in that column (header text
    counts too), clamped to COLUMN_WIDTH_BOUNDS. This replaces a fixed
    guessed width per column, which cut off longer course names, department
    names, and emails while leaving short columns like Semester too wide."""
    max_len = [len(name) for name in VISIBLE_COLUMNS]
    for sec in sections.values():
        for row_values in sec["rows"]:
            for idx in range(len(VISIBLE_COLUMNS)):
                text = "" if row_values[idx] is None else str(row_values[idx])
                if len(text) > max_len[idx]:
                    max_len[idx] = len(text)

    widths = []
    for idx, name in enumerate(VISIBLE_COLUMNS):
        lo, hi = COLUMN_WIDTH_BOUNDS.get(name, (10, 30))
        widths.append(max(lo, min(hi, max_len[idx] + 2)))
    return widths


def _lines_needed(value, column_width):
    """Estimate how many wrapped lines a cell's text will take at the given
    column width (Excel's width unit is roughly one character), so the row
    can be made tall enough that wrap_text doesn't visually truncate it."""
    text = "" if value is None else str(value)
    chars_per_line = max(int(column_width) - 2, 1)
    if not text:
        return 1
    import math
    return max(1, math.ceil(len(text) / chars_per_line))


# ===========================================================================
# LANDING PAGE (the form the COD actually sees)
# ===========================================================================

@login_required
@allowed_roles(*ALLOCATION_TEMPLATE_ROLES)
def course_allocation_template_page(request):
    """
    GET /course-management/cod/course-allocation-template/

    The page linked from the COD panel's side nav. Shows the "export"
    form (pick the allocation set, tick include-missing) and the
    "re-import" form, in the same visual language as Safe Undo / the rest
    of the COD panel.
    """
    dept = detect_user_department(request.user)
    # Anyone who can override the export department (true superuser, SUDO,
    # Director, or Timetable Admin — see _can_override_department) also gets
    # the department picker and the "All Departments" option, not just a
    # true Django superuser with no department of their own.
    can_override = _can_override_department(request.user)
    departments = Department.objects.order_by("name") if (can_override or dept is None) else None

    # Concurrent Allocation Sets: export/import is per-set, not per-semester
    # (a single set can mix several semesters), so show which set is
    # currently active and let the COD switch, same as the rest of the panel.
    active_allocation_set = None
    allocation_sets_for_switcher = []
    if dept is not None:
        from course_allocation.allocation_scope import get_active_allocation_set, get_or_default_legacy_set
        from course_allocation.models import AllocationSet
        active_allocation_set = get_active_allocation_set(request, dept) or get_or_default_legacy_set(dept)
        allocation_sets_for_switcher = AllocationSet.objects.filter(
            department=dept, is_archived=False
        ).order_by("-created_at")

    return render(request, "course_management/course_allocation_template.html", {
        "department": dept,
        "can_share_email": can_override,
        "departments": departments,
        "active_allocation_set": active_allocation_set,
        "allocation_sets_for_switcher": allocation_sets_for_switcher,
    })


# ===========================================================================
# EXPORT
# ===========================================================================

def _can_override_department(user):
    """True Django superusers, plus SUDO/DIRECTOR/TIMETABLE_ADMIN, may pick
    a department other than their own for export/import/sharing — this was
    previously true-superuser-only, which shut Director and Timetable Admin
    out of exporting/sharing another department's template even though
    they're already trusted with every other cross-department action on
    this page (see ALLOCATION_TEMPLATE_ROLES)."""
    return user.is_superuser or user_has_role(user, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)


def _resolve_export_department(request):
    """
    Shared by the download view and the email-share view: figures out which
    department to build the template for from detect_user_department() plus
    an optional ?department_id=/department_id= override. Returns
    (department_or_None, error_message_or_None).
    """
    dept = detect_user_department(request.user)
    if _can_override_department(request.user) or dept is None:
        override_id = request.GET.get("department_id") or request.POST.get("department_id")
        if override_id and override_id != "all":
            dept = Department.objects.filter(pk=override_id).first()
    if dept is None:
        return None, "No department could be determined for your account."
    return dept, None


def _resolve_export_allocation_set(request, dept):
    """
    Shared allocation-set resolution: an explicit allocation_set_id (from
    the page's switcher, or a share-form's per-department field) takes
    priority over the department's current session-active set. Returns
    (allocation_set_or_None, redirect_response_or_None) — the caller should
    return the redirect as-is when the set is None (only meaningful for the
    interactive download; the email path treats a None set as "skip this
    department").
    """
    from course_allocation.allocation_scope import get_active_allocation_set, get_or_default_legacy_set
    from course_allocation.models import AllocationSet

    allocation_set_id = request.GET.get("allocation_set_id") or request.POST.get("allocation_set_id")
    if allocation_set_id:
        active_allocation_set = AllocationSet.objects.filter(id=allocation_set_id, department=dept).first()
    else:
        active_allocation_set = get_active_allocation_set(request, dept) or get_or_default_legacy_set(dept)
    return active_allocation_set


def _write_hidden(ws, row_idx, values):
    """Fill the hidden identity cells of a band row (section / stem / group
    sub-header). The importer reads these to know which Programme, Year,
    Semester, Combination Stem or Student Group any hand-typed row beneath
    the band belongs to."""
    for name, value in values.items():
        ws.cell(row=row_idx, column=_COL_IDX[name] + 1, value=value)


def _build_course_allocation_workbook(dept, active_allocation_set, include_missing):
    """
    Builds and returns (workbook, filename) for ONE department + ONE
    AllocationSet — the actual Excel-building logic, factored out of
    export_course_allocation_template() so both the interactive download
    and the "share by email" path (single department or the whole
    university, one workbook each) produce byte-for-byte the same file.

    Concurrent Allocation Sets: this used to export "by semester" — pick
    Semester 1 or 2 and get everything the department has allocated for
    that semester, mixing every AllocationSet together. Now that a set can
    span more than one semester (e.g. "Semester 1 + a few Semester 2
    units"), exporting by semester either missed part of a set or bled in
    rows from a completely different set. It now exports exactly one
    AllocationSet — whichever semesters it actually contains — grouped by
    Programme + Year + Semester.
    """
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from course_allocation.allocation_scope import eligible_program_courses_for_component

    # ---- 1. Existing allocations for this department, this set ------------
    # "Belongs to" this COD's export if their department either allocates it
    # or originated it (covers common/university-wide units the department
    # teaches out to other programs, e.g. Education units).
    allocations = (
        CourseAllocation.objects
        .filter(Q(department=dept) | Q(origin_department=dept))
        .filter(allocation_set=active_allocation_set)
        .select_related(
            "program_course", "program_course__program", "department",
            "origin_department", "lecturer", "student_group",
            "selection_group", "specialization_stem",
            "specialization_stem__category",
        )
        .prefetch_related(
            "additional_student_groups", "specialization_stems__category",
            "selection_group__restricted_to_groups",
        )
        .order_by("program_course__program__name", "program_course__year",
                   "program_course__semester", "course_code")
    )

    # Group rows by (program_id, year, semester) — a set can mix semesters,
    # so semester now needs to be part of the section key, not assumed fixed.
    sections = {}  # (program_id, year, semester) -> {"program", "year", "semester", "rows": [...]}
    # Same grouping, but only for the subset that feeds the separate
    # "Course Allocation" sheet: courses that ORIGINATE from this
    # department's own curriculum (origin_department == dept) but are
    # actually being taught/allocated by a DIFFERENT department. See
    # _build_course_allocation_sheet.
    cross_alloc_sections = {}

    def _section_for(program, year, semester):
        key = (program.id, year, semester)
        if key not in sections:
            sections[key] = {"program": program, "year": year, "semester": semester,
                             "rows": [], "entries": []}
        return sections[key]

    def _cross_section_for(program, year, semester):
        key = (program.id, year, semester)
        if key not in cross_alloc_sections:
            cross_alloc_sections[key] = {"program": program, "year": year, "semester": semester, "rows": []}
        return cross_alloc_sections[key]

    seen_program_course_ids = set()

    for alloc in allocations:
        pc = alloc.program_course
        seen_program_course_ids.add(pc.id)
        sec = _section_for(pc.program, pc.year, pc.semester)
        _row = _row_from_allocation(alloc)
        sec["rows"].append(_row)
        sec["entries"].append((_row, alloc, pc))

        if (alloc.origin_department_id == dept.id
                and alloc.department_id
                and alloc.department_id != dept.id):
            csec = _cross_section_for(pc.program, pc.year, pc.semester)
            csec["rows"].append(_row_from_allocation(alloc))

    # ---- 2. Optionally top up with curriculum courses that are NOT yet
    #         allocated at all, within this set's own semester composition
    #         (own department's programs only — that is the curriculum this
    #         COD actually owns). A set with no semester components yet
    #         (e.g. courses were hand-picked one at a time) has no known
    #         "eligible universe" to top up against, so nothing extra shows.
    if include_missing:
        for component in active_allocation_set.semester_components.all():
            for pc in eligible_program_courses_for_component(component):
                if pc.id in seen_program_course_ids:
                    continue
                seen_program_course_ids.add(pc.id)
                sec = _section_for(pc.program, pc.year, pc.semester)
                _row = _row_for_missing_course(pc, dept)
                _row[_COL_IDX["_is_copy"]] = ""
                sec["rows"].append(_row)
                sec["entries"].append((_row, None, pc))

    # ---- 3. Build the workbook ---------------------------------------------
    for sec in sections.values():
        sec["rows"] = _group_rows_by_course_code(sec["rows"])
        _assign_row_numbers(sec["rows"])
    for sec in cross_alloc_sections.values():
        sec["rows"] = _group_rows_by_course_code(sec["rows"])
        _assign_row_numbers(sec["rows"])

    wb = Workbook()

    _build_instructions_sheet(wb, dept, active_allocation_set)
    # "How To Use" is built on the workbook's original default sheet, so it
    # is already first — just add the data sheet after it, instead of
    # moving it in front (which previously bumped "How To Use" to second).
    ws = wb.create_sheet("Course Schedule")

    # Try to stamp the university logo + name at the very top, if configured.
    _stamp_logo(ws, wb)

    # NOTE on header_font color: the header row's fill (HEADER_FILL, a very
    # light blue) previously paired with WHITE bold text — nearly invisible
    # against such a light background. Headers now use a dark navy that
    # reads clearly against HEADER_FILL, while the darker SECTION_FILL band
    # keeps its white text (good contrast there).
    header_font = Font(bold=True, color=SECTION_FILL)  # dark navy on light-blue header fill
    section_font = Font(bold=True, size=12, color="FFFFFF")
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # -- compute per-column widths from the ACTUAL content we're about to
    #    write, instead of hard-coded guesses. This is what stops text like
    #    long course names, department names, or emails being clipped, and
    #    keeps columns that only ever hold short values (Semester, Intake)
    #    from being needlessly wide. Bounds keep the sheet usable even with
    #    one extreme outlier value.
    visible_widths = _compute_visible_column_widths(sections)

    current_row = 4  # leave room for the logo/title band

    if not sections:
        ws.cell(row=current_row, column=1,
                 value=f"No courses found for '{active_allocation_set.name}'. "
                       f"Try ticking 'include courses not yet allocated'.")
    else:
        ordered_keys = sorted(
            sections.keys(),
            key=lambda k: (sections[k]["program"].name, sections[k]["year"], sections[k]["semester"]),
        )
        for key in ordered_keys:
            sec = sections[key]
            program, year, semester = sec["program"], sec["year"], sec["semester"]

            # -- section header band: Program Code cell + Program Name band --
            # The code sits in its OWN cell (column A), ahead of the name,
            # rather than appended inside the name text — that way the code
            # is there to cross-check against whenever a program name
            # doesn't normalize cleanly, without anyone having to manually
            # edit the name text to see it.
            program_codes = list(
                program.program_codes.order_by("code").values_list("code", flat=True)
            )
            code_text = ", ".join(program_codes) if program_codes else ""

            code_cell = ws.cell(row=current_row, column=1, value=code_text)
            code_cell.font = section_font
            code_cell.fill = PatternFill("solid", fgColor=SECTION_FILL)
            code_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

            # Merge only across the VISIBLE columns — the hidden identity
            # columns to the right stay free so this band can carry the
            # Programme/Year/Semester the importer uses to anchor any row a
            # COD types by hand underneath it.
            ws.merge_cells(start_row=current_row, start_column=2,
                             end_row=current_row, end_column=len(VISIBLE_COLUMNS))
            band = ws.cell(row=current_row, column=2,
                            value=f"{program.name}  —  Year {year}  (Semester {semester})")
            band.font = section_font
            band.fill = PatternFill("solid", fgColor=SECTION_FILL)
            band.alignment = Alignment(horizontal="left", vertical="center")
            _write_hidden(ws, current_row, {
                "_row_kind": "SECTION", "_program_id": program.id,
                "_year": year, "_semester": semester,
            })
            ws.row_dimensions[current_row].height = 22
            current_row += 1

            # -- column header row -------------------------------------------
            for col_idx, name in enumerate(ALL_COLUMNS, start=1):
                c = ws.cell(row=current_row, column=col_idx, value=name)
                c.font = header_font if col_idx <= len(VISIBLE_COLUMNS) else Font(bold=True)
                c.fill = PatternFill("solid", fgColor=HEADER_FILL if col_idx <= len(VISIBLE_COLUMNS) else "E7E6E6")
                c.border = border
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws.row_dimensions[current_row].height = 30  # room for headers that wrap to 2 lines
            current_row += 1

            def _write_data_rows(rows, start_row):
                """Write one contiguous run of data rows; returns next free row."""
                r_idx = start_row
                for i, row_values in enumerate(rows):
                    fill = PatternFill("solid", fgColor=DATA_ALT_FILL) if i % 2 else None
                    max_lines = 1
                    for col_idx, value in enumerate(row_values, start=1):
                        c = ws.cell(row=r_idx, column=col_idx, value=value)
                        c.border = border
                        if fill:
                            c.fill = fill
                        if col_idx <= len(VISIBLE_COLUMNS):
                            col_name = VISIBLE_COLUMNS[col_idx - 1]
                            horiz = "center" if col_name in CENTERED_COLUMNS else "left"
                            c.alignment = Alignment(horizontal=horiz, vertical="top", wrap_text=True)
                            max_lines = max(max_lines, _lines_needed(value, visible_widths[col_idx - 1]))
                    # Row height follows whichever cell needs the most wrapped
                    # lines in this row, so wrapped text is never sliced off —
                    # capped so one long outlier can't blow out the whole sheet.
                    ws.row_dimensions[r_idx].height = 15 * min(max_lines, 5)
                    r_idx += 1
                return r_idx

            blocks = _build_section_blocks(sec)
            if blocks is None:
                # No Combination Stem / Student Group in this section: the
                # plain flat layout, exactly as before.
                current_row = _write_data_rows(sec["rows"], current_row)
            else:
                for blk in blocks:
                    kind = blk["kind"]
                    if kind == "STEM":
                        fill_hex, font_color, tag = STEM_FILL, "FFFFFF", "STEM"
                    elif kind == "GROUP":
                        fill_hex, font_color, tag = GROUP_FILL, "FFFFFF", "GROUP"
                    elif kind == "ELECTIVE":
                        fill_hex, font_color, tag = ELECTIVE_FILL, "7F6000", ""
                    else:
                        fill_hex, font_color, tag = SHARED_FILL, SECTION_FILL, ""
                    is_main = kind in ("STEM", "GROUP")
                    fnt = Font(bold=True, size=11 if is_main else 10,
                               italic=not is_main, color=font_color)
                    tag_cell = ws.cell(row=current_row, column=1, value=tag)
                    tag_cell.font = fnt
                    tag_cell.fill = PatternFill("solid", fgColor=fill_hex)
                    tag_cell.alignment = Alignment(horizontal="center", vertical="center")
                    ws.merge_cells(start_row=current_row, start_column=2,
                                   end_row=current_row, end_column=len(VISIBLE_COLUMNS))
                    lbl = ws.cell(row=current_row, column=2, value=blk["label"])
                    lbl.font = fnt
                    lbl.fill = PatternFill("solid", fgColor=fill_hex)
                    lbl.alignment = Alignment(horizontal="left", vertical="center")
                    _write_hidden(ws, current_row, {
                        "_row_kind": "CONTEXT", "_context_kind": kind,
                        "_program_id": program.id, "_year": year, "_semester": semester,
                        "_student_group_id": blk["group_id"] or "",
                        "_specialization_stem_id": blk["stem_id"] or "",
                    })
                    ws.row_dimensions[current_row].height = 20 if is_main else 18
                    current_row += 1
                    current_row = _write_data_rows(blk["rows"], current_row)

            current_row += 1  # blank spacer row between program-year sections

    # -- column widths / hide identity columns -------------------------------
    for idx, width in enumerate(visible_widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    for idx in range(len(VISIBLE_COLUMNS) + 1, N_COLS + 1):
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = 4
        ws.column_dimensions[letter].hidden = True

    # No frozen panes: the logo/title band and the per-program header bands
    # previously stayed "stuck" at the top while everything else scrolled
    # underneath them. The whole sheet now scrolls together as one piece.
    ws.freeze_panes = None

    _build_course_allocation_sheet(wb, cross_alloc_sections)
    _build_special_request_sheet(wb, dept)
    _build_department_codes_sheet(wb)
    _build_program_codes_sheet(wb)

    safe_set_name = "".join(c if c.isalnum() else "_" for c in active_allocation_set.name)
    filename = f"course_allocation_template_{dept.name.replace(' ', '_')}_{safe_set_name}_{timezone.now():%Y%m%d}.xlsx"
    return wb, filename


@login_required
@allowed_roles(*ALLOCATION_TEMPLATE_ROLES)
def export_course_allocation_template(request):
    """
    GET /course-management/cod/course-allocation-template/export/?allocation_set_id=...&include_missing=1

    Exports the official Course Allocation Template for the COD's own
    department (or, for a true superuser / SUDO / DIRECTOR / TIMETABLE_ADMIN,
    an optional ?department_id=... override — see _can_override_department).
    """
    include_missing = request.GET.get("include_missing") in ("1", "true", "on", "yes")

    # "All Departments" — only offered to whoever can already override the
    # department (see _can_override_department); everyone else's
    # department_id=all is silently ignored by _resolve_export_department
    # falling back to their own department.
    if request.GET.get("department_id") == "all" and _can_override_department(request.user):
        return _export_all_departments_zip(request, include_missing)

    dept, error = _resolve_export_department(request)
    if dept is None:
        return render(request, "course_management/course_allocation_template_error.html", {
            "error": error,
        })

    active_allocation_set = _resolve_export_allocation_set(request, dept)
    if active_allocation_set is None:
        return redirect(reverse("allocation_picker") + f"?next={request.path}")

    wb, filename = _build_course_allocation_workbook(dept, active_allocation_set, include_missing)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


def _export_all_departments_zip(request, include_missing):
    """
    Bundles one Course Allocation Template workbook per department (whole
    university) into a single .zip download. A department with no
    allocation set yet (nothing to export) is silently skipped rather than
    failing the whole download — same "don't let one department block the
    rest" philosophy as the bulk email send below.
    """
    departments = Department.objects.order_by("name")
    buffer = io.BytesIO()
    included = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for dept in departments:
            active_allocation_set = _resolve_export_allocation_set(request, dept)
            if active_allocation_set is None:
                continue
            wb, filename = _build_course_allocation_workbook(dept, active_allocation_set, include_missing)
            wb_buffer = io.BytesIO()
            wb.save(wb_buffer)
            zf.writestr(filename, wb_buffer.getvalue())
            included += 1

    if included == 0:
        return render(request, "course_management/course_allocation_template_error.html", {
            "error": "No department has an active allocation set to export yet.",
        })

    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = (
        f'attachment; filename="course_allocation_templates_all_departments_{timezone.now():%Y%m%d}.zip"'
    )
    return response


# ===========================================================================
# SHARE BY EMAIL
# ===========================================================================
# Lets a Director / SUDO / Timetable Admin send the Course Allocation
# Template Excel — for one department, or every department at once — to
# that department's COD/COD Admin(s), or to a typed override address
# instead. Mirrors timetable's "Email Department" flow (see
# timetable/analysis_reports.py Section 9) closely enough that both share
# the recipient-resolution helpers in core/department_recipients.py; the
# recipient-preview endpoint that timetable's Email tab already uses
# (department_email_recipients_api) is reused here too rather than
# duplicated, since it takes nothing but a department_id.

def _send_one_department_allocation_email(request, department, subject, body, sender_name,
                                             include_missing, audience, custom_emails):
    """Builds and emails ONE department's Course Allocation Template.
    Returns a dict with 'status' in {'sent', 'skipped', 'error'} — shape
    matches timetable's _send_one_department_email so a bulk "all
    departments" send can be summarised the same way."""
    active_allocation_set = _resolve_export_allocation_set(request, department)
    if active_allocation_set is None:
        return {
            'department': department.name,
            'status': 'skipped',
            'reason': 'No allocation set found for this department yet.',
        }

    if custom_emails:
        to_emails = list(dict.fromkeys(custom_emails))
    else:
        recipients = resolve_department_email_recipients(department)
        to_emails = filter_recipients_by_audience(recipients, audience)
    if not to_emails:
        return {
            'department': department.name,
            'status': 'skipped',
            'reason': 'No COD or COD Admin email on file for the selected audience.',
        }

    try:
        wb, filename = _build_course_allocation_workbook(department, active_allocation_set, include_missing)
        wb_buffer = io.BytesIO()
        wb.save(wb_buffer)
        wb_bytes = wb_buffer.getvalue()
    except Exception as exc:
        logger.exception("share_course_allocation_template_email: failed building workbook for %s", department.name)
        return {
            'department': department.name,
            'status': 'error',
            'reason': f'Could not build the template: {exc}',
        }

    full_body = f"{body}\n\n— {sender_name}" if sender_name else body
    email = EmailMessage(
        subject=subject,
        body=full_body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=to_emails,
    )
    email.attach(
        filename, wb_bytes,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    try:
        email.send(fail_silently=False)
    except Exception as exc:
        logger.exception("share_course_allocation_template_email: send() failed for %s", department.name)
        return {
            'department': department.name,
            'status': 'error',
            'reason': f'Email failed to send: {exc}',
        }

    return {
        'department': department.name,
        'status': 'sent',
        'recipients': to_emails,
        'attachment': filename,
    }


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def share_course_allocation_template_email(request):
    """
    POST /course-management/cod/course-allocation-template/share-email/

    Emails the Course Allocation Template Excel for one department, or for
    department_id='all' (every department, one email each, one workbook
    each — nobody receives another department's allocations), to that
    department's COD and/or COD Admin(s).

    `audience` ('both' default / 'cod' / 'cod_admin') narrows the normal
    auto-resolved recipients. A typed `custom_email` overrides that
    resolution entirely — for every department in a bulk send too — letting
    the Director redirect a department's (or every department's) template
    to themselves or anyone else instead of that department's registered
    COD/COD Admin.
    """
    department_id = request.POST.get('department_id')
    subject = (request.POST.get('subject') or '').strip()
    body = (request.POST.get('body') or '').strip()
    include_missing = request.POST.get('include_missing') == '1'

    audience = (request.POST.get('audience') or 'both').strip().lower()
    if audience not in ('both', 'cod', 'cod_admin'):
        audience = 'both'
    custom_emails = parse_custom_emails((request.POST.get('custom_email') or '').strip())

    if not department_id:
        return JsonResponse({'status': 'error', 'message': 'Select a department (or "All Departments").'}, status=400)
    if not subject or not body:
        return JsonResponse({'status': 'error', 'message': 'Subject and message body are both required.'}, status=400)

    sender_name = request.user.get_full_name() or request.user.username

    # ─────────────────────────────────────────────────────────────
    # Bulk path: send to every department, one email each.
    # ─────────────────────────────────────────────────────────────
    if department_id == 'all':
        departments = list(Department.objects.order_by('name'))
        if not departments:
            return JsonResponse({'status': 'error', 'message': 'No departments exist.'}, status=400)

        results = [
            _send_one_department_allocation_email(
                request, department, subject, body, sender_name,
                include_missing, audience, custom_emails,
            )
            for department in departments
        ]

        sent = [r for r in results if r['status'] == 'sent']
        skipped = [r for r in results if r['status'] == 'skipped']
        errored = [r for r in results if r['status'] == 'error']

        if not sent:
            return JsonResponse({
                'status': 'error',
                'mode': 'bulk',
                'message': 'No department could be emailed — see per-department reasons below.',
                'total_departments': len(departments),
                'sent': sent,
                'skipped': skipped,
                'errors': errored,
            }, status=400)

        return JsonResponse({
            'status': 'success' if not (skipped or errored) else 'partial',
            'mode': 'bulk',
            'message': (
                f"Sent to {len(sent)} of {len(departments)} department(s)."
                + (f" {len(skipped)} skipped, {len(errored)} failed." if (skipped or errored) else '')
            ),
            'total_departments': len(departments),
            'sent': sent,
            'skipped': skipped,
            'errors': errored,
        })

    # ─────────────────────────────────────────────────────────────
    # Single-department path.
    # ─────────────────────────────────────────────────────────────
    department = Department.objects.filter(id=department_id).select_related('leader').first()
    if not department:
        return JsonResponse({'status': 'error', 'message': 'Department not found.'}, status=404)

    result = _send_one_department_allocation_email(
        request, department, subject, body, sender_name,
        include_missing, audience, custom_emails,
    )

    if result['status'] == 'skipped':
        return JsonResponse({'status': 'error', 'message': result['reason']}, status=400)
    if result['status'] == 'error':
        return JsonResponse({'status': 'error', 'message': result['reason']}, status=500)

    return JsonResponse({
        'status': 'success',
        'message': f"Email sent to {', '.join(result['recipients'])}.",
        'recipients': result['recipients'],
        'attachment': result['attachment'],
    })


def _row_from_allocation(alloc):
    from department_management.department_codes import department_initials
    pc = alloc.program_course
    return _finish_row([
        None,  # "No." — filled in per-section once rows are grouped (see _assign_row_numbers)
        alloc.course_code,
        alloc.course_name,
        pc.semester,
        department_initials(alloc.origin_department),
        department_initials(alloc.department),
        alloc.lecturer.email if alloc.lecturer else "",
        alloc.number_of_students,
        alloc.student_group.name if alloc.student_group else "",
        "Elective" if alloc.is_elective else "Core",
        alloc.selection_group.name if alloc.selection_group else "",
        alloc.specialization_stem.name if alloc.specialization_stem else "",
        "Special" if alloc.intake == CourseAllocation.INTAKE_SPECIAL else "Normal",
        # hidden
        "DATA",
        alloc.id,
        pc.id,
        pc.program_id,
        pc.year,
        pc.semester,
        alloc.department_id or "",
        alloc.origin_department_id or "",
        alloc.lecturer_id or "",
        alloc.student_group_id or "",
        alloc.selection_group_id or "",
        alloc.specialization_stem_id or "",
    ])


def _row_for_missing_course(pc, dept):
    from department_management.department_codes import department_initials
    dept_code = department_initials(dept)
    return _finish_row([
        None,  # "No." — filled in per-section once rows are grouped (see _assign_row_numbers)
        pc.course_code,
        pc.course_name,
        pc.semester,
        dept_code,       # default guess: origin = the owning department
        dept_code,       # default guess: allocating = the owning department
        "",               # lecturer email — COD fills this in
        0,
        "",               # student group — shared/none by default
        "Elective" if pc.is_elective_type else "Core",
        "",
        "",
        "Normal",
        # hidden
        "DATA",
        "",               # no CourseAllocation yet
        pc.id,
        pc.program_id,
        pc.year,
        pc.semester,
        "",
        "",
        "",
        "",
        "",
        "",
    ])


def _resolve_department(text):
    """Resolve a Department from the text in an Allocating/Origin
    Department cell — the department's CODE first (what current exports
    write, e.g. "DHUM"; see department_management/department_codes.py and
    the "Department Codes" sheet), then the full department name, for
    older exported files or a cell someone edited by hand."""
    if not text:
        return None
    from department_management.models import DepartmentCode
    code_entry = DepartmentCode.objects.select_related("department").filter(code__iexact=text).first()
    if code_entry:
        return code_entry.department
    return Department.objects.filter(name__iexact=text).first()


def _stamp_logo(ws, wb):
    """Best-effort: put the configured university logo + name at the top of
    the sheet. Silently skipped if no logo is configured — this is a nice-to
    -have, never a reason to fail the export."""
    try:
        from openpyxl.drawing.image import Image as XLImage
        from openpyxl.styles import Font, Alignment
        from export_import.models import TimetablePdfTemplate
        tpl = TimetablePdfTemplate.objects.first()
        if tpl and tpl.university_logo and hasattr(tpl.university_logo, "path"):
            img = XLImage(tpl.university_logo.path)
            img.height = 60
            img.width = 60
            ws.add_image(img, "A1")
        if tpl:
            # Merge + wrap the title text across several columns instead of
            # dropping it into a single cell (column C). Column widths are
            # now computed per-export from the data, so column C alone can
            # end up too narrow for "Chuka University" or "Official Course
            # Allocation Template" — merging gives the text real room and
            # wrap_text keeps it fully visible instead of being cut off.
            title_end_col = min(9, N_COLS)

            ws.merge_cells(start_row=1, start_column=3, end_row=1, end_column=title_end_col)
            name_cell = ws.cell(row=1, column=3, value=tpl.university_name)
            name_cell.font = Font(bold=True, size=14)
            name_cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            ws.row_dimensions[1].height = 22

            ws.merge_cells(start_row=2, start_column=3, end_row=2, end_column=title_end_col)
            subtitle_cell = ws.cell(row=2, column=3, value="Official Course Allocation Template")
            subtitle_cell.font = Font(italic=True, size=11)
            subtitle_cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            ws.row_dimensions[2].height = 18
    except Exception:
        logger.exception("Could not stamp logo on course allocation template — continuing without it.")


def _build_instructions_sheet(wb, dept, allocation_set):
    ws = wb.active
    ws.title = "How To Use"
    from openpyxl.styles import Font, Alignment

    semesters_covered = sorted({
        c.semester_number for c in allocation_set.semester_components.all()
    })
    semesters_text = (
        ", ".join(str(s) for s in semesters_covered) if semesters_covered else "mixed / hand-picked"
    )

    lines = [
        ("Course Allocation Template — How To Use", True, 14),
        ("", False, 11),
        (f"Department: {dept.name}   |   Allocation: {allocation_set.name}   |   "
         f"Semester(s): {semesters_text}   |   Generated: {timezone.now():%d %b %Y %H:%M}", False, 11),
        ("", False, 11),
        ("1. This workbook lists every course allocation already on the system for this "
         "allocation, grouped by Programme, Year and Semester (each group has its own blue "
         "header band) — a single allocation can span more than one semester.", False, 11),
        ("2. If you ticked 'include courses not yet allocated', courses from your curriculum "
         "that belong to this allocation but have no allocation row yet are included too, "
         "with Number of Students = 0 and Lecturer Email blank, so nothing is left out.", False, 11),
        ("", False, 11),
        ("IMPORTANT — Lecturer Email, not Lecturer Name", True, 12),
        ("Always identify the lecturer by their EMAIL address, never by typing their name. "
         "Two lecturers can share the same or a similar name, and the system matches "
         "purely on email — using a name here can create a duplicate lecturer record.", False, 11),
        ("", False, 11),
        ("Column reference:", True, 12),
        (" - No.: a running count of units within each Programme/Year section. A unit taken "
         "by more than one Student Group / Combination Stem, or split into lettered sections "
         "(e.g. 'ECON 232' and 'ECON 232-D' are the same unit), keeps ONE number across all "
         "its rows, distinguished by a letter (1a, 1b, 1c...) — purely a reading aid, not used "
         "on import. These rows are also kept together, one after another, and the whole "
         "section is sorted alphabetically by Course Code.", False, 11),
        (" - Course Code / Course Name: as defined in the programme curriculum.", False, 11),
        (" - Origin Department: the department that owns/created the course — shown as its "
         "short CODE (e.g. 'DHUM'), not its full name, so this column doesn't overflow. See "
         "the 'Department Codes' sheet for what each code stands for.", False, 11),
        (" - Allocating Department: the department actually teaching/allocating it this "
         "semester — also shown as its code (see 'Department Codes' sheet).", False, 11),
        (" - Lecturer Email: the assigned lecturer's email. Leave blank if unassigned.", False, 11),
        (" - Number of Students: expected enrolment for this row.", False, 11),
        (" - Student Group: e.g. 'Group A'. Leave blank if the course is shared by the "
         "whole programme year (not split into groups).", False, 11),
        (" - Unit Type: 'Core' or 'Elective'.", False, 11),
        (" - Elective Group: only for Elective units — the pool of courses students choose "
         "ONE from (e.g. 'Year 3 Sem 1 Electives - Group A').", False, 11),
        (" - Combination Stem: only if the course belongs to a specialization/combination "
         "stem (e.g. 'Artificial Intelligence' stem). Leave blank otherwise.", False, 11),
        ("", False, 11),
        ("COMBINATION STEMS & STUDENT GROUPS (how a Programme/Year is laid out)", True, 12),
        ("When a Programme/Year has Combination Stems or Student Groups, the section is split "
         "under the blue band into one block per stem / group. Each block starts with a header "
         "(STEM or GROUP, then the name exactly as set on the system) and lists, in order: "
         "(1) that stem's / group's own units, (2) a 'Shared units' sub-band with every unit "
         "taken by everyone (repeated under EVERY stem/group, because each of them takes them), "
         "and (3) an 'Electives' sub-band with the elective units.", False, 11),
        (" - A repeated unit is still ONE allocation. Edit it in any one place; if you change "
         "more than one copy differently, the first edited copy is used and you are warned.", False, 11),
        (" - Adding a unit: type it on a new row directly UNDER the stem/group header to put it in "
         "that stem/group, under 'Shared units' to make it shared, or under 'Electives' to make "
         "it an elective. The system reads which header it sits under, so you do not need to "
         "fill in the Student Group / Combination Stem columns yourself.", False, 11),
        (" - Intake: 'Normal' or 'Special'.", False, 11),
        ("", False, 11),
        ("'COURSE ALLOCATION' SHEET", True, 12),
        ("The 3rd sheet, 'Course Allocation', lists only the courses that ORIGINATE from your "
         "department's own curriculum but are actually taught/allocated by a DIFFERENT "
         "department — i.e. units you own that have been farmed out elsewhere. It's a "
         "read-only reference view, grouped the same way as 'Course Schedule'; every row on "
         "it also appears there. Editing it has no effect — only 'Course Schedule' is read "
         "on import.", False, 11),
        ("", False, 11),
        ("'SPECIAL REQUEST' SHEET", True, 12),
        ("The 4th sheet. Raise an SR (Special Request — e.g. 'this lecturer cannot take "
         "upstairs venues', 'this course is a workshop, schedule it at a certain time') "
         "straight from this workbook instead of the on-screen SR button. Your currently "
         "open SRs are listed first (editing the Request or Status text and re-uploading "
         "UPDATES that same SR); add a new request on any blank row below them. Fill in "
         "exactly ONE of the three identifier columns per new row: Lecturer Email for a "
         "request about everything that lecturer teaches, Program Code for a whole "
         "programme (add a Course Code too to narrow it to specific units), or Course Code "
         "alone for just that unit — comma-separate more than one code to cover several at "
         "once.", False, 11),
        ("", False, 11),
        ("DEPARTMENT CODES", True, 12),
        ("The Origin/Allocating Department columns show each department's short code "
         "instead of its full name (see the 'Department Codes' sheet in this workbook for "
         "the full list). On re-upload, either the code or the full department name is "
         "accepted.", False, 11),
        ("", False, 11),
        ("'PROGRAM CODES' SHEET", True, 12),
        ("Lists every Programme alongside its short Program Code — the same code shown in "
         "each blue section-header band on 'Course Schedule' and 'Course Allocation'.", False, 11),
        ("", False, 11),
        ("COMMON / SHARED UNITS (e.g. Education units taken by several combinations)", True, 12),
        ("If the same Course Code is taken by students from more than one Combination Stem, "
         "Programme, or Student Group, do NOT merge them into one row. Keep one row PER "
         "Combination Stem / Student Group, each with its own Number of Students. This is "
         "what stops the system from colliding two different groups' registrations under a "
         "single course code.", False, 11),
        ("", False, 11),
        ("RE-UPLOADING THIS FILE", True, 12),
        ("You can edit any of the visible columns above and re-upload this same file from the "
         "'Import Course Allocation' button on the COD panel. The system recognises each row "
         "automatically (this is handled by hidden columns in the sheet — please do not "
         "unhide, reorder, or delete them). Rows for courses that had no allocation yet will "
         "be created; rows for existing allocations will be updated. Do not delete the blue "
         "section header rows or the column header rows.", False, 11),
        ("", False, 11),
        ("Adding a brand-new row: you may type a new row from scratch for a course that "
         "belongs to your curriculum, as long as the Course Code matches exactly what is in "
         "the programme curriculum (Course Management > Programme Courses).", False, 11),
    ]
    for i, (text, bold, size) in enumerate(lines, start=1):
        c = ws.cell(row=i, column=1, value=text)
        c.font = Font(bold=bold, size=size)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 130
    # Row height now follows how many lines each line of instructions
    # actually wraps to at that column width, instead of a flat 18pt —
    # the longer paragraphs above were being visually cut off at one line.
    for i, (text, bold, size) in enumerate(lines, start=1):
        lines_wrapped = _lines_needed(text, 130)
        ws.row_dimensions[i].height = max(18, lines_wrapped * (size + 7))


def _build_course_allocation_sheet(wb, cross_alloc_sections):
    """"Course Allocation" sheet (3rd sheet in the workbook) — the courses
    that ORIGINATE from this department's own curriculum but are actually
    being taught/allocated by a DIFFERENT department (e.g. a unit this
    department owns that another department's staff teach to their own
    students). Every one of these rows also appears on "Course Schedule" —
    this sheet exists purely so the ones farmed out elsewhere are visible
    at a glance instead of having to hunt through the full schedule for a
    mismatched Origin/Allocating pair.

    Reference only: same grouped-by-Programme/Year/Semester layout as
    "Course Schedule", but no hidden identity columns — the importer never
    reads this sheet, it only reads "Course Schedule"."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    ws = wb.create_sheet("Course Allocation")
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_font = Font(bold=True, color=SECTION_FILL)
    section_font = Font(bold=True, size=12, color="FFFFFF")
    n_visible = len(VISIBLE_COLUMNS)

    if not cross_alloc_sections:
        ws.cell(row=1, column=1,
                 value="No cross-department course allocations found for this allocation.")
        ws.column_dimensions["A"].width = 80
        return

    visible_widths = _compute_visible_column_widths(cross_alloc_sections)
    current_row = 1

    ordered_keys = sorted(
        cross_alloc_sections.keys(),
        key=lambda k: (cross_alloc_sections[k]["program"].name,
                        cross_alloc_sections[k]["year"], cross_alloc_sections[k]["semester"]),
    )
    for key in ordered_keys:
        sec = cross_alloc_sections[key]
        program, year, semester = sec["program"], sec["year"], sec["semester"]

        program_codes = list(
            program.program_codes.order_by("code").values_list("code", flat=True)
        )
        code_text = ", ".join(program_codes) if program_codes else ""

        code_cell = ws.cell(row=current_row, column=1, value=code_text)
        code_cell.font = section_font
        code_cell.fill = PatternFill("solid", fgColor=SECTION_FILL)
        code_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        ws.merge_cells(start_row=current_row, start_column=2,
                         end_row=current_row, end_column=n_visible)
        band = ws.cell(row=current_row, column=2,
                        value=f"{program.name}  —  Year {year}  (Semester {semester})")
        band.font = section_font
        band.fill = PatternFill("solid", fgColor=SECTION_FILL)
        band.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[current_row].height = 22
        current_row += 1

        for col_idx, name in enumerate(VISIBLE_COLUMNS, start=1):
            c = ws.cell(row=current_row, column=col_idx, value=name)
            c.font = header_font
            c.fill = PatternFill("solid", fgColor=HEADER_FILL)
            c.border = border
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[current_row].height = 30
        current_row += 1

        for i, row_values in enumerate(sec["rows"]):
            fill = PatternFill("solid", fgColor=DATA_ALT_FILL) if i % 2 else None
            max_lines = 1
            for col_idx, value in enumerate(row_values[:n_visible], start=1):
                c = ws.cell(row=current_row, column=col_idx, value=value)
                c.border = border
                if fill:
                    c.fill = fill
                col_name = VISIBLE_COLUMNS[col_idx - 1]
                horiz = "center" if col_name in CENTERED_COLUMNS else "left"
                c.alignment = Alignment(horizontal=horiz, vertical="top", wrap_text=True)
                max_lines = max(max_lines, _lines_needed(value, visible_widths[col_idx - 1]))
            ws.row_dimensions[current_row].height = 15 * min(max_lines, 5)
            current_row += 1

        current_row += 1  # blank spacer row between program-year sections

    for idx, width in enumerate(visible_widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = None


def _build_program_codes_sheet(wb):
    """Reference sheet, mirrors "Department Codes": every Program alongside
    its short Program Code(s) — the same codes shown in the section-header
    band on "Course Schedule" / "Course Allocation" — so the COD never has
    to leave the workbook to see what a program code stands for."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    ws = wb.create_sheet("Program Codes")
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    header_font = Font(bold=True, color="FFFFFF")
    for col_idx, title in enumerate(["Program Code", "Program Name"], start=1):
        c = ws.cell(row=1, column=col_idx, value=title)
        c.font = header_font
        c.fill = PatternFill("solid", fgColor=SECTION_FILL)
        c.border = border
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 20

    entries = (
        ProgramCode.objects
        .select_related("program")
        .order_by("code")
    )
    row_idx = 2
    for entry in entries:
        ws.cell(row=row_idx, column=1, value=entry.code).border = border
        ws.cell(row=row_idx, column=2, value=entry.program.name).border = border
        row_idx += 1

    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 60


def _write_sr_row(ws, row_idx, values, border, alt):
    from openpyxl.styles import PatternFill, Alignment
    fill = PatternFill("solid", fgColor=DATA_ALT_FILL) if alt else None
    n_visible = len(SR_VISIBLE_COLUMNS)
    for col_idx, value in enumerate(values, start=1):
        c = ws.cell(row=row_idx, column=col_idx, value=value)
        c.border = border
        if fill:
            c.fill = fill
        if col_idx <= n_visible:
            c.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    ws.row_dimensions[row_idx].height = 32


def _build_special_request_sheet(wb, dept):
    """"Special Request" sheet — lets the COD raise an SR (see the
    special_requests app / the on-screen SR button elsewhere in the COD
    panel) straight from this workbook, instead of one allocation at a
    time. Every currently open SR for this department is pre-listed
    first (a hidden ID makes re-uploading it an UPDATE, not a duplicate),
    followed by a few blank rows for brand-new requests.

    Fill in exactly ONE of the three identifier columns per row to say
    who/what a NEW request applies to:
      - Lecturer Email  -> applies to everything that lecturer teaches
      - Program Code    -> applies to a whole programme (add a Course
                            Code too to narrow it to specific units of
                            that programme)
      - Course Code     -> applies to just that unit — comma-separate
                            more than one code to cover several at once

    Reference + input: read on import from this sheet's own section,
    entirely independent of "Course Schedule"."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from special_requests.models import SpecialRequest

    ws = wb.create_sheet("Special Request")
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_font = Font(bold=True, color=SECTION_FILL)
    n_visible = len(SR_VISIBLE_COLUMNS)

    for col_idx, name in enumerate(SR_ALL_COLUMNS, start=1):
        c = ws.cell(row=1, column=col_idx, value=name)
        c.font = header_font if col_idx <= n_visible else Font(bold=True)
        c.fill = PatternFill("solid", fgColor=HEADER_FILL if col_idx <= n_visible else "E7E6E6")
        c.border = border
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 20

    existing = (
        SpecialRequest.objects
        .filter(department=dept, archived=False, panel=SpecialRequest.PANEL_NORMAL)
        .select_related("lecturer", "program")
        .order_by("-created_at")
    )

    row_idx = 2
    for i, sr in enumerate(existing, start=1):
        lecturer_email = (
            sr.lecturer.email if sr.scope == SpecialRequest.SCOPE_LECTURER and sr.lecturer else ""
        )
        program_code = ""
        if sr.scope in (SpecialRequest.SCOPE_PROGRAM, SpecialRequest.SCOPE_PROGRAM_COURSES) and sr.program:
            codes = list(sr.program.program_codes.order_by("code").values_list("code", flat=True))
            program_code = ", ".join(codes)
        if sr.scope == SpecialRequest.SCOPE_UNIT:
            course_code = sr.course_code
        elif sr.scope == SpecialRequest.SCOPE_PROGRAM_COURSES:
            course_code = ", ".join(sr.affected_courses)
        else:
            course_code = ""

        _write_sr_row(
            ws, row_idx,
            [i, lecturer_email, program_code, course_code, sr.description, sr.get_status_display(),
             "DATA", sr.id],
            border, alt=(i % 2 == 0),
        )
        row_idx += 1

    for _ in range(8):  # blank rows for brand-new requests
        _write_sr_row(ws, row_idx, ["", "", "", "", "", "", "DATA", ""], border, alt=False)
        row_idx += 1

    for idx, name in enumerate(SR_VISIBLE_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = SR_COLUMN_WIDTHS.get(name, 20)
    for idx in range(n_visible + 1, len(SR_ALL_COLUMNS) + 1):
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = 4
        ws.column_dimensions[letter].hidden = True


def _build_department_codes_sheet(wb):
    """Reference sheet: Origin Department / Allocating Department now show a
    short code (e.g. "DHUM") instead of the full department name, to stop
    long names overflowing/getting clipped in those columns — see
    department_management/department_codes.py. This sheet spells every
    code back out to its full department name so the COD (or anyone else
    opening the file) never has to guess what a code means."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from department_management.models import DepartmentCode

    ws = wb.create_sheet("Department Codes")
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    header_font = Font(bold=True, color="FFFFFF")
    for col_idx, title in enumerate(["Code", "Department Name"], start=1):
        c = ws.cell(row=1, column=col_idx, value=title)
        c.font = header_font
        c.fill = PatternFill("solid", fgColor=SECTION_FILL)
        c.border = border
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 20

    entries = (
        DepartmentCode.objects
        .select_related("department")
        .order_by("code")
    )
    row_idx = 2
    for entry in entries:
        ws.cell(row=row_idx, column=1, value=entry.code).border = border
        ws.cell(row=row_idx, column=2, value=entry.department.name).border = border
        row_idx += 1

    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 60


# ===========================================================================
# IMPORT
# ===========================================================================

@login_required
@allowed_roles(*ALLOCATION_TEMPLATE_ROLES)
def import_course_allocation_template(request):
    """
    POST /course-management/cod/course-allocation-template/import/
    multipart form field: "file"

    Re-imports a (possibly edited) export produced by
    export_course_allocation_template above. Renders a result page listing
    what was created / updated / skipped / flagged per row.
    """
    if request.method != "POST" or not request.FILES.get("file"):
        return render(request, "course_management/course_allocation_template_error.html", {
            "error": "Please choose the Excel file to import.",
        })

    from openpyxl import load_workbook

    from course_allocation.allocation_scope import get_active_allocation_set, get_or_default_legacy_set

    dept = detect_user_department(request.user)
    upload = request.FILES["file"]
    # Concurrent Allocation Sets: this whole import pipeline pre-dates the
    # allocation-set feature and previously ignored it entirely — every new
    # or matched row landed unscoped (or matched across sets by accident).
    # Resolve the COD's active set once, up front, and thread it through
    # every create/match below so an import always lands in (and only
    # matches within) the set the COD is currently working on.
    active_allocation_set = (
        get_active_allocation_set(request, dept) or get_or_default_legacy_set(dept)
        if dept else None
    )

    try:
        wb = load_workbook(upload, data_only=True)
    except Exception as exc:
        return render(request, "course_management/course_allocation_template_error.html", {
            "error": f"Could not read that file as an Excel workbook: {exc}",
        })

    # Accept both the current sheet name and the legacy one, so a file
    # exported before this rename (was "Course Allocation") can still be
    # re-imported without the COD needing to re-export first.
    sheet_name = "Course Schedule" if "Course Schedule" in wb.sheetnames else "Course Allocation"
    if sheet_name not in wb.sheetnames:
        return render(request, "course_management/course_allocation_template_error.html", {
            "error": "This doesn't look like a Course Allocation Template "
                     "(missing the 'Course Schedule' sheet).",
        })
    ws = wb[sheet_name]

    # Map header name -> column index, read from whichever row holds the
    # column headers (first row containing "Course Code" in column A's row).
    header_row_idx = None
    header_map = {}
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 20)):
        values = [c.value for c in row]
        if "Course Code" in values:
            header_row_idx = row[0].row
            header_map = {v: idx for idx, v in enumerate(values) if v}
            break
    if header_row_idx is None:
        return render(request, "course_management/course_allocation_template_error.html", {
            "error": "Could not find the column header row in this file.",
        })

    missing_cols = [c for c in ALL_COLUMNS
                    if c not in header_map and c not in OPTIONAL_HIDDEN_COLUMNS]
    if missing_cols:
        return render(request, "course_management/course_allocation_template_error.html", {
            "error": "This file is missing expected columns (it may have been altered): "
                     + ", ".join(missing_cols),
        })

    def val(row_cells, name):
        idx = header_map.get(name)   # optional columns may be absent in older exports
        if idx is None:
            return None
        return row_cells[idx].value if idx < len(row_cells) else None

    created, updated, skipped = 0, 0, 0
    courses_created = 0     # curriculum courses (ProgramCourse) auto-created on import
    lecturers_created = 0   # lecturer records auto-created on import
    warnings = []

    # ---- PASS 1: walk the sheet top-to-bottom, tracking which Programme /
    # Year / Semester section and which Combination Stem / Student Group /
    # Shared / Electives sub-block each data row sits in. A row the COD
    # typed by hand has no hidden identity cells of its own — this is what
    # tells the importer where it belongs (and that a row typed under a
    # Student Group or Combination Stem header belongs to THAT group/stem).
    # Rows are only collected here; nothing is written to the database yet.
    collected = []          # list of (row_cells, ctx dict)
    ctx = {}
    # Start from row 1, not the first header row: the FIRST section band sits
    # just above it and carries that section's Programme/Year/Semester.
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
        if not row:
            continue
        row_cells = list(row)
        kind = val(row_cells, "_row_kind")
        if kind == "SECTION":
            ctx = {
                "program_id": val(row_cells, "_program_id"),
                "year": val(row_cells, "_year"),
                "semester": val(row_cells, "_semester"),
                "block": None, "stem_id": None, "group_id": None,
            }
            continue
        if kind == "CONTEXT":
            block = (val(row_cells, "_context_kind") or "").strip().upper() or None
            stem_id = val(row_cells, "_specialization_stem_id") or None
            group_id = val(row_cells, "_student_group_id") or None
            label = (row_cells[1].value if len(row_cells) > 1 else None) or ""
            label = str(label).strip()
            # Name fallback for a sub-header with no hidden id (one the COD
            # added by hand): match by the name as set on the system.
            if block == "STEM" and not stem_id and label and ctx.get("program_id"):
                st = SpecializationStem.objects.filter(
                    category__program_id=ctx["program_id"], name__iexact=label).first()
                stem_id = st.id if st else None
            if block == "GROUP" and not group_id and label and ctx.get("program_id"):
                sg_obj = StudentGroup.objects.filter(
                    program_id=ctx["program_id"], year=ctx.get("year"),
                    semester=ctx.get("semester"), name__iexact=label).first()
                group_id = sg_obj.id if sg_obj else None
            ctx = dict(ctx)
            ctx["block"] = block
            ctx["stem_id"] = stem_id if block == "STEM" else None
            ctx["group_id"] = group_id if block == "GROUP" else None
            continue
        if kind in (None, ""):
            # A row typed by hand has none of the hidden cells filled in. It
            # is a real data row as long as it has a Course Code AND sits
            # under a Programme section band (which gives it its Programme /
            # Year / Semester, and — via the last stem / group / shared /
            # electives sub-band above it — its Combination Stem or Student
            # Group). Fully blank spacer rows have no Course Code and are
            # ignored.
            typed_code = val(row_cells, "Course Code")
            if typed_code and str(typed_code).strip() and ctx.get("program_id"):
                collected.append((row_cells, dict(ctx)))
            continue
        if kind != "DATA":
            continue  # column-header row
        collected.append((row_cells, dict(ctx)))

    # ---- PASS 1b: the same allocation can legitimately appear several
    # times (a Shared unit is repeated under every Combination Stem /
    # Student Group; an elective under each). Import it ONCE. If the COD
    # edited one of the copies, that copy wins (its fingerprint no longer
    # matches the one stamped at export); if untouched, the first wins.
    def _row_sig_now(cells):
        return _edit_signature(lambda n: val(cells, n))

    def _dedupe_key(cells):
        alloc_id = val(cells, "_allocation_id")
        if alloc_id:
            return ("a", alloc_id)
        pc_id = val(cells, "_program_course_id")
        if pc_id and (val(cells, "_is_copy") or "") == "COPY":
            return ("p", pc_id)
        return None

    by_key = {}
    for i, (cells, _c) in enumerate(collected):
        k = _dedupe_key(cells)
        if k is not None:
            by_key.setdefault(k, []).append(i)
    skip_indexes = set()
    for k, idxs in by_key.items():
        if len(idxs) < 2:
            continue
        edited = []
        for i in idxs:
            cells = collected[i][0]
            orig = val(cells, "_orig_sig")
            if orig and _row_sig_now(cells) != orig:
                edited.append(i)
        winner = edited[0] if edited else idxs[0]
        if len({_row_sig_now(collected[i][0]) for i in edited}) > 1:
            code = (val(collected[winner][0], "Course Code") or "").strip()
            rows_txt = ", ".join(str(collected[i][0][0].row) for i in edited)
            warnings.append(
                f"'{code}' appears under several Combination Stems / Student Groups and "
                f"was edited differently in rows {rows_txt} — used row "
                f"{collected[winner][0][0].row} (the first edited copy). Edit only one "
                f"copy of a repeated unit, or make the copies match."
            )
        skip_indexes.update(i for i in idxs if i != winner)

    with transaction.atomic():
        for _i, (row_cells, row_ctx) in enumerate(collected):
            if _i in skip_indexes:
                continue  # an untouched / superseded echo of a row handled elsewhere

            excel_row_no = row_cells[0].row
            course_code = (val(row_cells, "Course Code") or "").strip()
            if not course_code:
                continue

            # ---- resolve ProgramCourse (identity anchor for the row) -----
            pc_id = val(row_cells, "_program_course_id")
            pc = ProgramCourse.objects.filter(pk=pc_id).first() if pc_id else None
            # A hidden ID can point at the WRONG course when a row was made
            # by copy-pasting an existing row and then typing a different
            # Course Code over it — the hidden ID comes along for the ride
            # unchanged. Don't trust it unless it still matches what's
            # actually written in the Course Code cell.
            if pc is not None and canonical_course_key(pc.course_code) != canonical_course_key(course_code):
                pc = None

            program_id = val(row_cells, "_program_id") or row_ctx.get("program_id")
            year = val(row_cells, "_year") or row_ctx.get("year")
            semester = val(row_cells, "_semester") or row_ctx.get("semester")
            block_kind = row_ctx.get("block")

            if pc is None and program_id and year and semester:
                candidates = ProgramCourse.objects.filter(
                    program_id=program_id, year=year, semester=semester,
                )
                key = canonical_course_key(course_code)
                pc = next((c for c in candidates if canonical_course_key(c.course_code) == key), None)

            if pc is None:
                # Not in the curriculum yet — create it so the row can still
                # be incorporated instead of being skipped, as long as we
                # know which Program/Year/Semester it belongs under (that
                # comes from the row's own section — a brand new row typed
                # outside any Programme section has nothing to anchor to).
                if not (program_id and year and semester):
                    warnings.append(f"Row {excel_row_no}: could not match '{course_code}' to a "
                                     f"curriculum course, and this row isn't inside a Programme "
                                     f"section (no Program/Year/Semester to create it under) — "
                                     f"row skipped.")
                    skipped += 1
                    continue

                program = Program.objects.filter(pk=program_id).first()
                if program is None:
                    warnings.append(f"Row {excel_row_no}: could not match '{course_code}' to a "
                                     f"curriculum course, and its Programme no longer exists — "
                                     f"row skipped.")
                    skipped += 1
                    continue

                course_name = (val(row_cells, "Course Name") or "").strip() or course_code
                unit_type_raw = (val(row_cells, "Unit Type") or "").strip().lower()
                if not unit_type_raw and block_kind == "ELECTIVE":
                    unit_type_raw = "elective"
                new_unit_type = "ELECTIVE" if unit_type_raw.startswith("elect") else "CORE"

                try:
                    new_pc = ProgramCourse(
                        program=program, course_code=normalize_code(course_code),
                        course_name=course_name, year=year, semester=semester,
                        unit_type=new_unit_type, student_cohort="0",
                    )
                    new_pc.full_clean()
                    new_pc.save()
                    pc = new_pc
                    courses_created += 1
                    warnings.append(f"Row {excel_row_no}: '{course_code}' wasn't in the "
                                     f"curriculum yet — added it to {program.name} Year {year} "
                                     f"Semester {semester}. Please review its details under "
                                     f"Program Courses.")
                except ValidationError as exc:
                    warnings.append(f"Row {excel_row_no}: could not add '{course_code}' to the "
                                     f"curriculum — {exc} — row skipped.")
                    skipped += 1
                    continue

            # ---- resolve allocating + origin department ------------------
            # Exports now write the department's CODE here (e.g. "DHUM"),
            # not the full name, so a code is resolved first — the legacy
            # full-name lookup is kept as a fallback so a file exported
            # before this change (or a code typed as a name by hand) still
            # imports correctly.
            dept_text = (val(row_cells, "Allocating Department") or "").strip()
            allocating_dept = _resolve_department(dept_text)
            if allocating_dept is None:
                allocating_dept = dept  # fall back to the COD's own department
                if dept_text:
                    warnings.append(f"Row {excel_row_no}: Allocating Department "
                                     f"'{dept_text}' not found — used {dept.name} instead.")

            origin_text = (val(row_cells, "Origin Department") or "").strip()
            origin_dept = _resolve_department(origin_text)
            if origin_text and origin_dept is None:
                warnings.append(f"Row {excel_row_no}: Origin Department '{origin_text}' not found — left blank.")

            # ---- resolve lecturer by EMAIL ONLY ----------------------------
            # Lecturers are matched by email, never by name (see module
            # docstring). When no match exists, create one rather than
            # dropping the lecturer from the allocation — the sheet gives
            # us nothing but the email, so the email doubles as a
            # placeholder name too, and the record is mapped to the
            # department actually allocating/teaching the course (the COD
            # uploading this file is that department). It's flagged in the
            # warnings so the COD knows to go fill in the real name/title.
            lecturer = None
            email = (val(row_cells, "Lecturer Email") or "").strip()
            if email:
                lecturer = Lecturer.objects.filter(email__iexact=email).first()
                if lecturer is None:
                    try:
                        new_lecturer = Lecturer(
                            name=email, email=email, designation="Mr",
                            department=allocating_dept,
                            payroll_number=generate_unique_payroll(),
                        )
                        new_lecturer.full_clean()
                        new_lecturer.save()
                        lecturer = new_lecturer
                        lecturers_created += 1
                        warnings.append(f"Row {excel_row_no}: no lecturer found with email "
                                         f"'{email}' — created a new lecturer record mapped to "
                                         f"{allocating_dept.name} (name/title are placeholders — "
                                         f"please update them under Lecturers).")
                    except ValidationError as exc:
                        warnings.append(f"Row {excel_row_no}: could not create a lecturer for "
                                         f"'{email}' — {exc} — allocation saved without a "
                                         f"lecturer.")

            # ---- resolve student group -------------------------------------
            student_group = None
            sg_name = (val(row_cells, "Student Group") or "").strip()
            sg_id = val(row_cells, "_student_group_id")
            if sg_id:
                student_group = StudentGroup.objects.filter(pk=sg_id).first()
            # A row typed under a "GROUP" sub-header inherits that group
            # (only when it names no group of its own and isn't an existing
            # allocation that already carries its own hidden ids).
            if (student_group is None and not sg_name and block_kind == "GROUP"
                    and row_ctx.get("group_id") and not val(row_cells, "_allocation_id")):
                student_group = StudentGroup.objects.filter(pk=row_ctx["group_id"]).first()
            if student_group is None and sg_name:
                _sg_qs = StudentGroup.objects.filter(
                    program_id=pc.program_id, year=pc.year, semester=pc.semester, name__iexact=sg_name
                )
                # Same name can exist for both intakes — prefer the row's own intake.
                _intake_hint = (val(row_cells, "Intake") or "").strip().lower()
                student_group = (
                    (_sg_qs.filter(intake=_intake_hint).first() if _intake_hint in ("normal", "special") else None)
                    or _sg_qs.first()
                )
                if student_group is None:
                    warnings.append(f"Row {excel_row_no}: Student Group '{sg_name}' not found "
                                     f"for {pc.program.name} Year {pc.year} — treated as shared "
                                     f"(no group).")

            # ---- unit type / elective ---------------------------------------
            unit_type_text = (val(row_cells, "Unit Type") or "").strip().lower()
            if not unit_type_text and block_kind == "ELECTIVE" and student_group is None:
                unit_type_text = "elective"   # typed under an "Electives" sub-band
            is_elective = unit_type_text.startswith("elect")

            # ---- elective group (SelectionGroup) -----------------------------
            selection_group = None
            eg_name = (val(row_cells, "Elective Group") or "").strip()
            eg_id = val(row_cells, "_selection_group_id")
            if eg_id:
                selection_group = SelectionGroup.objects.filter(pk=eg_id).first()
            if selection_group is None and eg_name and is_elective:
                # Scope by allocation_set so a name match in a different
                # concurrent set is never reused, and a new group created
                # here always lands tagged with the set being imported into.
                selection_group, _sg_created = SelectionGroup.objects.get_or_create(
                    name=eg_name, department=allocating_dept, program=pc.program,
                    allocation_set=active_allocation_set,
                )

            # ---- combination / specialization stem --------------------------
            stem = None
            stem_name = (val(row_cells, "Combination Stem") or "").strip()
            stem_id = val(row_cells, "_specialization_stem_id")
            if stem_id:
                stem = SpecializationStem.objects.filter(pk=stem_id).first()
            # A row typed under a "STEM" sub-header inherits that stem.
            if (stem is None and not stem_name and block_kind == "STEM"
                    and row_ctx.get("stem_id") and not val(row_cells, "_allocation_id")):
                stem = SpecializationStem.objects.filter(pk=row_ctx["stem_id"]).first()
            if stem is None and stem_name:
                stem = SpecializationStem.objects.filter(
                    category__program=pc.program, name__iexact=stem_name
                ).first()
                if stem is None:
                    warnings.append(f"Row {excel_row_no}: Combination Stem '{stem_name}' not "
                                     f"found for {pc.program.name} — please create it first "
                                     f"under Specialization Stems, then re-import.")

            # ---- number of students / intake ---------------------------------
            try:
                n_students = int(val(row_cells, "Number of Students") or 0)
            except (TypeError, ValueError):
                n_students = 0
                warnings.append(f"Row {excel_row_no}: invalid Number of Students — set to 0.")

            intake_text = (val(row_cells, "Intake") or "").strip().lower()
            if not intake_text and student_group is not None:
                intake_text = (student_group.intake or "").lower()   # follow the group's own intake
            intake = CourseAllocation.INTAKE_SPECIAL if intake_text == "special" else CourseAllocation.INTAKE_NORMAL

            defaults = dict(
                course_code=pc.course_code,
                course_name=pc.course_name,
                origin_department=origin_dept,
                program=pc.program,
                lecturer=lecturer,
                number_of_students=n_students,
                is_elective=is_elective,
                selection_group=selection_group if is_elective else None,
                specialization_stem=stem,
                intake=intake,
                allocation_set=active_allocation_set,
            )

            # ---- upsert -------------------------------------------------------
            alloc_id = val(row_cells, "_allocation_id")
            alloc = CourseAllocation.objects.filter(pk=alloc_id).first() if alloc_id else None

            try:
                if alloc:
                    for field, value in defaults.items():
                        setattr(alloc, field, value)
                    alloc.department = allocating_dept
                    alloc.student_group = student_group
                    alloc.full_clean()
                    alloc.save()
                    updated += 1
                else:
                    if student_group is not None:
                        alloc, was_created = CourseAllocation.objects.get_or_create(
                            program_course=pc, department=allocating_dept, student_group=student_group,
                            allocation_set=active_allocation_set,
                            defaults=defaults,
                        )
                    else:
                        alloc, was_created = CourseAllocation.get_or_create_shared(
                            pc, allocating_dept, defaults=defaults,
                            allocation_set=active_allocation_set,
                        )
                    if not was_created:
                        for field, value in defaults.items():
                            setattr(alloc, field, value)
                        alloc.full_clean()
                        alloc.save()
                        updated += 1
                    else:
                        created += 1
            except ValidationError as exc:
                warnings.append(f"Row {excel_row_no} ({course_code}): {exc}")
                skipped += 1

    # ---------------------------------------------------------------------
    # "Special Request" sheet (optional) — entirely separate from the
    # "Course Schedule" import above: raises/updates special_requests.
    # SpecialRequest rows instead of course allocations. Missing sheet is
    # not an error — older exports, or a file where the COD deleted this
    # sheet, simply have nothing to process here.
    # ---------------------------------------------------------------------
    sr_created = sr_updated = sr_skipped = 0
    sr_warnings = []
    sr_processed = "Special Request" in wb.sheetnames

    if sr_processed:
        from special_requests.models import SpecialRequest
        from special_requests.services import create_special_request, update_special_request

        sr_ws = wb["Special Request"]
        sr_header_map = {}
        for row in sr_ws.iter_rows(min_row=1, max_row=1):
            sr_header_map = {c.value: idx for idx, c in enumerate(row) if c.value}

        sr_missing_cols = [c for c in SR_ALL_COLUMNS if c not in sr_header_map]
        if sr_missing_cols:
            sr_warnings.append(
                "Special Request sheet is missing expected columns (it may have been "
                "altered) — skipped entirely: " + ", ".join(sr_missing_cols)
            )
        else:
            def sr_val(row_cells, name):
                idx = sr_header_map[name]
                return row_cells[idx].value if idx < len(row_cells) else None

            # Every allocation this department is allowed to attach an SR
            # to — mirrors the export's own department scoping (teaches it
            # or originated it).
            sr_base_qs = (
                CourseAllocation.objects
                .filter(Q(department=dept) | Q(origin_department=dept))
                .filter(allocation_set=active_allocation_set)
            )

            with transaction.atomic():
                for row in sr_ws.iter_rows(min_row=2, max_row=sr_ws.max_row):
                    if not row:
                        continue
                    row_cells = list(row)
                    if sr_val(row_cells, "_row_kind") != "DATA":
                        continue

                    excel_row_no = row_cells[0].row
                    description = (sr_val(row_cells, "Request") or "").strip()
                    sr_id = sr_val(row_cells, "_special_request_id")
                    existing_sr = (
                        SpecialRequest.objects.filter(pk=sr_id, department=dept).first()
                        if sr_id else None
                    )

                    if not description:
                        continue  # blank template row, or an existing SR left untouched

                    if existing_sr:
                        status_text = (sr_val(row_cells, "Status") or "").strip().lower()
                        status_value = next(
                            (val for val, label in SpecialRequest.STATUS_CHOICES
                             if label.lower() == status_text),
                            None,
                        )
                        update_special_request(existing_sr, description=description, status=status_value)
                        sr_updated += 1
                        continue

                    lecturer_email = (sr_val(row_cells, "Lecturer Email") or "").strip()
                    program_code_text = (sr_val(row_cells, "Program Code") or "").strip()
                    course_code_text = (sr_val(row_cells, "Course Code") or "").strip()
                    course_codes = [c.strip() for c in course_code_text.split(",") if c.strip()]

                    if not lecturer_email and not program_code_text and not course_codes:
                        sr_warnings.append(
                            f"Special Request row {excel_row_no}: fill in Lecturer Email, "
                            f"Program Code, or Course Code to say who/what this applies to "
                            f"— row skipped."
                        )
                        sr_skipped += 1
                        continue

                    targets = sr_base_qs
                    scope = None

                    if lecturer_email:
                        lecturer = Lecturer.objects.filter(email__iexact=lecturer_email).first()
                        if lecturer is None:
                            sr_warnings.append(
                                f"Special Request row {excel_row_no}: no lecturer found with "
                                f"email '{lecturer_email}' — row skipped."
                            )
                            sr_skipped += 1
                            continue
                        targets = targets.filter(lecturer_id=lecturer.id)
                        scope = SpecialRequest.SCOPE_LECTURER
                    elif program_code_text:
                        first_code = program_code_text.split(",")[0].strip()
                        code_entry = (
                            ProgramCode.objects.select_related("program")
                            .filter(code__iexact=first_code).first()
                        )
                        if code_entry is None:
                            sr_warnings.append(
                                f"Special Request row {excel_row_no}: no program found with "
                                f"code '{first_code}' — row skipped."
                            )
                            sr_skipped += 1
                            continue
                        targets = targets.filter(program_id=code_entry.program_id)
                        scope = SpecialRequest.SCOPE_PROGRAM

                    if course_codes:
                        targets = targets.filter(course_code__in=course_codes)
                        scope = (
                            SpecialRequest.SCOPE_PROGRAM_COURSES if scope == SpecialRequest.SCOPE_PROGRAM
                            else SpecialRequest.SCOPE_UNIT if scope is None
                            else scope
                        )

                    target_list = list(targets)
                    if not target_list:
                        sr_warnings.append(
                            f"Special Request row {excel_row_no}: could not find any course "
                            f"allocation matching those details — row skipped."
                        )
                        sr_skipped += 1
                        continue

                    create_special_request(
                        target_allocations=target_list, scope=scope, description=description,
                        user=request.user, panel=SpecialRequest.PANEL_NORMAL,
                    )
                    sr_created += 1

    return render(request, "course_management/course_allocation_template_result.html", {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "courses_created": courses_created,
        "lecturers_created": lecturers_created,
        "warnings": warnings,
        "department": dept,
        "sr_processed": sr_processed,
        "sr_created": sr_created,
        "sr_updated": sr_updated,
        "sr_skipped": sr_skipped,
        "sr_warnings": sr_warnings,
    })
