"""
resits_timetabling/resit_import.py
Import panel for COD — allows importing resit allocations from:
CourseAllocation  (the MAIN allocation model, not resit)
ArchivedCourseAllocation (the MAIN archived allocation model, not resit-archived)
Both sources are loaded automatically when the page opens (via auto_load AJAX call).
Archived entries are grouped by archived_at year + semester so they appear as
separate cards on the frontend.
Field mapping (from models.py):
CourseAllocation      — no academic_year / semester at top level;
semester lives on program_course (FK → ProgramCourse).
ArchivedCourseAllocation — has semester (CharField) and archived_at (DateTimeField)
but NO academic_year field and NO program_course FK.
Skips duplicates automatically (match on program + course_code + semester).
Adds to existing without overwriting anything.
"""
import csv
import io
import json
import logging
import re
import traceback

import openpyxl
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role, user_has_role
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render

from core.group_required import group_required
from department_management.models import Department
from program_management.models import Program, ProgramCourse
from .cod_panel import get_user_department
from .models import (
    ResitArchivedAllocation,
    ResitCourseAllocation,
    ResitSchedulerConfig,
    ResitVenueExclusion,
    StudentResitRegistration,
)
from .faculty_utils import FACULTY_ALIASES, extract_faculty_name, resolve_faculty

logger = logging.getLogger(__name__)

# ── Try importing the main CourseAllocation model ──────────────────────────────
try:
    from course_allocation.models import CourseAllocation
    _HAS_COURSE_ALLOC = True
except ImportError:
    _HAS_COURSE_ALLOC = False
    CourseAllocation = None
    logger.warning("resit_import: CourseAllocation model not found — source disabled.")

# ── Try importing the MAIN ArchivedCourseAllocation model ─────────────────────
# This is the main allocation archive, NOT the ResitArchivedTimetable.
# Adjust the import path to match your project structure.
try:
    from course_allocation.models import ArchivedCourseAllocation
    _HAS_ARCHIVED_ALLOC = True
except ImportError:
    _HAS_ARCHIVED_ALLOC = False
    ArchivedCourseAllocation = None
    logger.warning(
        "resit_import: ArchivedCourseAllocation model not found — archived source disabled."
    )

# ───────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────

def normalize_reg_number(reg_no: str) -> str:
    """
    Extract numeric portion from registration number.
    e.g., "EB1/66791/23" -> "66791"
          "66791" -> "66791"
          "EB1/66791/23 (John Doe)" -> "66791"
    """
    if not reg_no:
        return ""
    # Remove any trailing parentheses content
    reg_no = re.sub(r'\s*\([^)]*\)', '', str(reg_no))
    # Extract first sequence of digits (min 4 digits)
    match = re.search(r'(\d{4,})', reg_no)
    if match:
        return match.group(1)
    # Fallback: remove non-digits
    return re.sub(r'\D', '', reg_no)


def _existing_keys(department):
    """
    Return a set of (program_id, course_code_upper, semester) tuples
    for all current ResitCourseAllocations in this department.
    Used for fast duplicate detection.
    NOTE: academic_year is intentionally excluded — ArchivedCourseAllocation
    has no academic_year field, so we match on program + course_code + semester.
    """
    qs = ResitCourseAllocation.objects.filter(department=department).values(
        "program_id", "course_code", "semester"
    )
    return {
        (r["program_id"], r["course_code"].upper(), r["semester"])
        for r in qs
    }


def _is_dup(keys_set, program_id, course_code, semester):
    return (program_id, course_code.upper(), semester) in keys_set


# ───────────────────────────────────────────────────────────────
# Row builder — normalises any CourseAllocation-like ORM object
# ───────────────────────────────────────────────────────────────

def _row_from_allocation(a):
    """
    Normalise a CourseAllocation or ArchivedCourseAllocation ORM object into a dict.
    CourseAllocation:
      - semester lives on program_course (FK → ProgramCourse), NOT on the allocation.
      - no academic_year field at all.
    ArchivedCourseAllocation:
      - semester is a CharField on the model directly.
      - no program_course FK, no academic_year field.
      - archived_at (DateTimeField) is used to derive a display year for grouping.
    """
    pc = getattr(a, "program_course", None)   # None for ArchivedCourseAllocation
    program = getattr(a, "program", None)
    lecturer = getattr(a, "lecturer", None)
    origin = getattr(a, "origin_department", None)

    # semester: prefer top-level field (ArchivedCourseAllocation), fall back to
    # program_course.semester (CourseAllocation), then None.
    top_semester = getattr(a, "semester", None)
    pc_semester = getattr(pc, "semester", None) if pc else None
    semester_str = str(top_semester) if top_semester is not None else (
                   str(pc_semester) if pc_semester is not None else "")

    # academic_year: neither model has this field directly.
    # For archived rows use the year of archived_at; for current rows leave blank.
    archived_at = getattr(a, "archived_at", None)
    academic_year = str(archived_at.year) if archived_at else ""

    return {
        "id":                   a.id,
        "course_code":          getattr(a, "course_code", None) or (getattr(pc, "course_code", "") if pc else ""),
        "course_name":          getattr(a, "course_name", None) or (getattr(pc, "course_name", "") if pc else ""),
        "program_id":           getattr(a, "program_id", None),
        "program_name":         getattr(program, "name", "") if program else "",
        "year":                 getattr(pc, "year", None) if pc else None,
        "semester":             pc_semester,          # numeric year-of-study semester (may be None)
        "academic_year":        academic_year,         # derived from archived_at or ""
        "semester_str":         semester_str,          # the grouping / display semester
        "lecturer_id":          getattr(a, "lecturer_id", None),
        "lecturer_name":        getattr(lecturer, "display_name", "") if lecturer else "",
        "students":             getattr(a, "number_of_students", 0) or 0,
        "program_course_id":    getattr(a, "program_course_id", None),
        "origin_department_id": getattr(a, "origin_department_id", None),
        # Private fields — stripped before sending to client
        "_lecturer_obj":        lecturer,
        "_program_obj":         program,
        "_pc_obj":              pc,
        "_origin_dept":         origin,
        "_archived_at":         archived_at,
    }


# ───────────────────────────────────────────────────────────────
# Source fetchers
# ───────────────────────────────────────────────────────────────

def _rows_from_course_allocation(department, filters=None):
    """
    Pull rows from the MAIN CourseAllocation table for this department.
    CourseAllocation has no academic_year or top-level semester field;
    semester lives on the program_course FK. Filters on those fields are skipped.
    """
    if not _HAS_COURSE_ALLOC:
        return [], "CourseAllocation model is not available in this project."

    filters = filters or {}
    try:
        qs = CourseAllocation.objects.select_related(
            "program", "program_course", "lecturer", "department", "origin_department"
        ).filter(department=department)
    except Exception:
        try:
            qs = CourseAllocation.objects.select_related(
                "program", "program_course", "lecturer", "department"
            ).filter(department=department)
        except Exception as exc:
            logger.error("resit_import: CourseAllocation queryset failed: %s", exc)
            return [], str(exc)

    if filters.get("program_id"):
        qs = qs.filter(program_id=filters["program_id"])
    # academic_year / semester do NOT exist on CourseAllocation — skip those filters.

    try:
        rows = [_row_from_allocation(a) for a in qs.order_by("course_code")[:500]]
    except Exception as exc:
        logger.error("resit_import: error building CA rows: %s", exc)
        return [], str(exc)
    return rows, None


def _rows_from_main_archived(department, filters=None):
    """
    Pull rows from the MAIN ArchivedCourseAllocation table for this department.
    ArchivedCourseAllocation has:
      - semester (CharField, e.g. "1", "2", "2025S1")
      - archived_at (DateTimeField) — used for display grouping
    It does NOT have: academic_year, program_course.
    """
    if not _HAS_ARCHIVED_ALLOC:
        return [], "ArchivedCourseAllocation model is not available in this project."

    filters = filters or {}
    try:
        # ArchivedCourseAllocation has no program_course FK — omit it
        qs = ArchivedCourseAllocation.objects.select_related(
            "program", "lecturer", "department", "origin_department"
        ).filter(department=department)
    except Exception as exc:
        logger.error("resit_import: ArchivedCourseAllocation queryset failed: %s", exc)
        return [], str(exc)

    if filters.get("program_id"):
        qs = qs.filter(program_id=filters["program_id"])
    if filters.get("semester"):
        qs = qs.filter(semester=filters["semester"])
    # academic_year does NOT exist on ArchivedCourseAllocation — skip that filter.

    try:
        rows = [
            _row_from_allocation(a)
            for a in qs.order_by("-archived_at", "semester", "course_code")[:500]
        ]
    except Exception as exc:
        logger.error("resit_import: error building archived rows: %s", exc)
        return [], str(exc)
    return rows, None


def _rows_from_resit_archive(department, filters=None):
    """
    Pull rows from ResitArchivedAllocation (our own resit archive, NOT the
    main CourseAllocation archive).  These are previously-archived resit
    allocation snapshots that a COD may want to re-import as a fresh cycle.
    """
    filters = filters or {}
    qs = ResitArchivedAllocation.objects.select_related(
        "program", "lecturer", "department", "origin_department", "program_course"
    ).filter(department=department)
    if filters.get("academic_year"):
        qs = qs.filter(academic_year=filters["academic_year"])
    if filters.get("semester"):
        qs = qs.filter(semester=filters["semester"])
    if filters.get("program_id"):
        qs = qs.filter(program_id=filters["program_id"])

    try:
        rows = []
        for a in qs.order_by("-academic_year", "semester", "course_code")[:500]:
            program = a.program
            lecturer = a.lecturer
            rows.append({
                "id":                   a.id,
                "course_code":          a.course_code,
                "course_name":          a.course_name,
                "program_id":           a.program_id,
                "program_name":         program.name if program else "",
                "year":                 None,
                "semester":             a.semester,
                "academic_year":        a.academic_year,
                "semester_str":         a.semester,
                "lecturer_id":          a.lecturer_id,
                "lecturer_name":        lecturer.display_name if lecturer else "",
                "students":             a.number_of_students,
                "program_course_id":    a.program_course_id,
                "origin_department_id": a.origin_department_id,
                # private
                "_lecturer_obj":        lecturer,
                "_program_obj":         program,
                "_pc_obj":              a.program_course,
                "_origin_dept":         a.origin_department,
                "_archived_at":         a.archived_at,
            })
    except Exception as exc:
        logger.error("resit_import: error building resit_archive rows: %s", exc)
        return [], str(exc)
    return rows, None


def _group_resit_archive_by_period(rows):
    """Group ResitArchivedAllocation rows by (academic_year, semester)."""
    groups: dict[tuple, list] = {}
    for r in rows:
        key = (r["academic_year"], r["semester_str"])
        groups.setdefault(key, []).append(r)
    
    result = []
    for (ay, sem), group_rows in sorted(groups.items(), reverse=True):
        if ay and sem:
            label = f"{ay} — Semester {sem}"
        elif ay:
            label = f"Archived {ay}"
        elif sem:
            label = f"Semester {sem}"
        else:
            label = "Unknown Period"
        result.append({
            "academic_year": ay,
            "semester":      sem,
            "label":         label,
            "rows":          group_rows,
        })
    return result


def _annotate_duplicates(rows, existing_keys):
    for r in rows:
        r["is_duplicate"] = _is_dup(
            existing_keys,
            r["program_id"],
            r["course_code"],
            r["semester_str"],
        )
    return rows


def _serialise_rows(rows):
    """Strip private _* keys before sending to the client."""
    safe_keys = {
        "id", "course_code", "course_name", "program_id", "program_name",
        "year", "semester", "academic_year", "semester_str",
        "lecturer_id", "lecturer_name", "students",
        "program_course_id", "origin_department_id", "is_duplicate",
    }
    return [{k: v for k, v in r.items() if k in safe_keys} for r in rows]


def _group_archived_by_period(rows):
    """
    Group archived rows by (academic_year derived from archived_at, semester_str).
    Returns a list sorted newest-first:
    [ { academic_year, semester, label, rows: [...] }, ... ]
    """
    groups: dict[tuple, list] = {}
    for r in rows:
        # academic_year is derived from archived_at.year in _row_from_allocation
        key = (r["academic_year"], r["semester_str"])
        groups.setdefault(key, []).append(r)
    
    result = []
    for (ay, sem), group_rows in sorted(groups.items(), reverse=True):
        if ay and sem:
            label = f"{ay} — Semester {sem}"
        elif ay:
            label = f"Archived {ay}"
        elif sem:
            label = f"Semester {sem}"
        else:
            label = "Unknown Period"
        result.append({
            "academic_year": ay,
            "semester":      sem,
            "label":         label,
            "rows":          group_rows,
        })
    return result


# ───────────────────────────────────────────────────────────────
# Auto-load handler — called once on page open
# ───────────────────────────────────────────────────────────────

def _handle_auto_load(request, department):
    """
    Returns both sources in one JSON payload so the frontend can render
    everything immediately without a multi-step wizard.
    Response shape:
    {
       "status": "success",
       "course_allocation": {
           "available": bool, "error": str|null,
           "rows": [...], "total": N
      },
       "archived": {
           "available": bool, "error": str|null,
           "groups": [ { academic_year, semester, label, rows: [...] } ],
           "total": N
      }
    }
    """
    existing_keys = _existing_keys(department)

    # Current allocations
    ca_rows, ca_err = _rows_from_course_allocation(department)
    if ca_err:
        ca_rows = []
    _annotate_duplicates(ca_rows, existing_keys)

    # Archived allocations
    ar_rows, ar_err = _rows_from_main_archived(department)
    if ar_err:
        ar_rows = []
    _annotate_duplicates(ar_rows, existing_keys)

    ar_groups = _group_archived_by_period(ar_rows)
    for g in ar_groups:
        g["rows"] = _serialise_rows(g["rows"])

    # Resit Archive allocations (our own archived resit allocs)
    ra_rows, ra_err = _rows_from_resit_archive(department)
    if ra_err:
        ra_rows = []
    _annotate_duplicates(ra_rows, existing_keys)
    ra_groups = _group_resit_archive_by_period(ra_rows)
    for g in ra_groups:
        g["rows"] = _serialise_rows(g["rows"])

    return JsonResponse({
        "status": "success",
        "course_allocation": {
            "available": _HAS_COURSE_ALLOC,
            "error":     ca_err,
            "rows":      _serialise_rows(ca_rows),
            "total":     len(ca_rows),
        },
        "archived": {
            "available": _HAS_ARCHIVED_ALLOC,
            "error":     ar_err,
            "groups":    ar_groups,
            "total":     len(ar_rows),
        },
        "resit_archive": {
            "available": True,
            "error":     ra_err,
            "groups":    ra_groups,
            "total":     len(ra_rows),
        },
    })


# ───────────────────────────────────────────────────────────────
# Import handler
# ───────────────────────────────────────────────────────────────

def _handle_import(request, department):
    """
    POST fields expected:
    source  — 'course_allocation' | 'archived'
    ids     — JSON array of integer PKs to import
    """
    source = request.POST.get("source")
    try:
        raw_ids = json.loads(request.POST.get("ids", "[]"))
        ids = [int(i) for i in raw_ids]
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"status": "error", "message": "Invalid IDs."}, status=400)
    
    if not ids:
        return JsonResponse({"status": "error", "message": "No IDs provided."}, status=400)
    if len(ids) > 200:
        return JsonResponse({"status": "error", "message": "Too many IDs (max 200)."}, status=400)

    if source == "course_allocation":
        all_rows, err = _rows_from_course_allocation(department)
    elif source == "archived":
        all_rows, err = _rows_from_main_archived(department)
    elif source == "resit_archive":
        all_rows, err = _rows_from_resit_archive(department)
    else:
        return JsonResponse({"status": "error", "message": "Invalid source."}, status=400)

    if err:
        return JsonResponse({"status": "error", "message": err}, status=400)

    row_map = {r["id"]: r for r in all_rows}
    existing_keys = _existing_keys(department)
    imported = 0
    skipped = 0
    errors = 0
    error_details = []

    for rid in ids:
        row = row_map.get(rid)
        if not row:
            skipped += 1
            continue

        if _is_dup(existing_keys, row["program_id"], row["course_code"], row["semester_str"]):
            skipped += 1
            continue

        try:
            with transaction.atomic():
                ResitCourseAllocation.objects.create(
                    course_code        = row["course_code"],
                    course_name        = row["course_name"],
                    department         = department,
                    origin_department  = row["_origin_dept"],
                    program            = row["_program_obj"],
                    program_course     = row["_pc_obj"],
                    lecturer           = row["_lecturer_obj"],
                    number_of_students = row["students"],
                    academic_year      = row.get("academic_year", ""),
                    semester           = row["semester_str"],
                    created_by         = request.user,
                )
            existing_keys.add((
                row["program_id"],
                row["course_code"].upper(),
                row["semester_str"],
            ))
            imported += 1
        except Exception as exc:
            logger.error("resit_import: error creating allocation for %s: %s", row["course_code"], exc)
            errors += 1
            error_details.append(f"{row['course_code']}: {exc}")

    return JsonResponse({
        "status":         "success",
        "imported":       imported,
        "skipped":        skipped,
        "errors":         errors,
        "error_details":  error_details,
    })


# ───────────────────────────────────────────────────────────────
# Student Import Handlers
# ───────────────────────────────────────────────────────────────

def _resolve_course_globally(course_code: str, faculty=None):
    """
    Resolve a course code to (program, department, program_course, course_name)
    by searching across ALL programs in the database.
    Courses may span many departments; when *faculty* (a resolved Faculty
    instance — see faculty_utils.resolve_faculty) is given, a department
    under that faculty is preferred, which disambiguates a course_code that
    exists under more than one department. Otherwise falls back to the
    first match (alphabetical by department).
    Returns a dict or None.
    """
    import re as _re
    code_upper = str(course_code).strip().upper() if course_code else ""
    if not code_upper:
        return None

    qs = (
        ProgramCourse.objects
        .filter(course_code__iexact=code_upper)
        .select_related("program", "program__department", "program__department__faculty")
        .order_by("program__department__name", "program__name")
    )
    if not qs.exists():
        # Try stripping spaces (e.g. "CSC 301" → "CSC301")
        code_no_sp = code_upper.replace(" ", "")
        qs = (
            ProgramCourse.objects
            .filter(course_code__iregex=r"^" + _re.escape(code_no_sp) + r"$")
            .select_related("program", "program__department", "program__department__faculty")
            .order_by("program__department__name", "program__name")
        )
    if not qs.exists():
        return None

    pc = None
    if faculty is not None:
        pc = next(
            (row for row in qs if row.program.department.faculty_id == faculty.id),
            None,
        )
    if pc is None:
        pc = qs.first()

    return {
        "course_code":    pc.course_code,
        "course_name":    pc.course_name,
        "program":        pc.program,
        "department":     pc.program.department,
        "program_course": pc,
    }


def _handle_preview_students_csv(request, department):
    """
    POST: action=preview_students_csv
    Parses the uploaded file and returns a per-course summary — no DB writes.

    Response shape:
    {
      "status": "ok",
      "total_rows": N,
      "course_count": N,
      "courses": [
        {
          "course_code": "CSC301",
          "course_name": "Data Structures",
          "program":     "BSc Computer Science",
          "department":  "Computing",
          "found":       true,
          "student_count": 12,
          "already_allocated": false   -- True if a ResitCourseAllocation already exists
        }, ...
      ]
    }
    """
    if not request.FILES.get('students_file'):
        return JsonResponse({"status": "error", "message": "No file uploaded."}, status=400)

    uploaded_file = request.FILES['students_file']
    filename = uploaded_file.name.lower()
    rows = []

    # ── Parse ──────────────────────────────────────────────────────────────────
    try:
        if filename.endswith('.csv'):
            raw = uploaded_file.read()
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('latin-1')
            rows = [
                {k.strip().lower().replace(' ', '_'): v for k, v in r.items()}
                for r in csv.DictReader(io.StringIO(text))
            ]
        elif filename.endswith(('.xlsx', '.xls')):
            wb = openpyxl.load_workbook(uploaded_file, read_only=True, data_only=True)
            ws = wb.active
            header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
            if not header_row:
                return JsonResponse({"status": "error", "message": "Excel file is empty."}, status=400)
            headers = [
                str(h).strip().lower().replace(' ', '_') if h else f'col_{i}'
                for i, h in enumerate(header_row)
            ]
            for row_vals in ws.iter_rows(min_row=2, values_only=True):
                if not any(row_vals):
                    continue
                rows.append({
                    headers[i]: (str(v).strip() if v is not None else '')
                    for i, v in enumerate(row_vals) if i < len(headers)
                })
        else:
            return JsonResponse(
                {"status": "error", "message": "Unsupported file type. Upload .csv or .xlsx/.xls."},
                status=400,
            )
    except Exception as exc:
        return JsonResponse({"status": "error", "message": f"Could not parse file: {exc}"}, status=400)

    if not rows:
        return JsonResponse({"status": "error", "message": "No valid rows found in file."}, status=400)

    # ── Column detection ───────────────────────────────────────────────────────
    sample = rows[0]
    reg_aliases    = {'reg_no', 'reg_number', 'registration', 'student_id', 'admission', 'reg'}
    course_aliases = {'course_code', 'course', 'code', 'unit_code', 'subject_code'}
    reg_key    = next((k for k in sample if k in reg_aliases), None)
    course_key = next((k for k in sample if k in course_aliases), None)

    if not reg_key or not course_key:
        return JsonResponse({
            "status": "error",
            "message": (
                "File must contain reg_no (or registration/admission/student_id) "
                "and course_code (or course/code/unit_code). "
                f"Found: {', '.join(sorted(sample.keys()))}"
            ),
        }, status=400)

    # ── Academic year + semester (for existing-allocation check) ───────────────
    academic_year = request.POST.get('academic_year', '').strip()
    semester      = request.POST.get('semester', '').strip()
    config = ResitSchedulerConfig.objects.order_by('-id').first()
    if not academic_year and config:
        academic_year = config.academic_year or ''
    if not semester and config:
        semester = config.semester or ''

    # ── Summarise by course_code ───────────────────────────────────────────────
    course_summary: dict[str, dict] = {}
    course_faculty_name: dict[str, str] = {}
    for row in rows:
        reg_no      = str(row.get(reg_key, '') or '').strip()
        course_code = str(row.get(course_key, '') or '').strip().upper()
        if not reg_no or not course_code:
            continue
        if course_code not in course_faculty_name:
            course_faculty_name[course_code] = extract_faculty_name(row)
        if course_code not in course_summary:
            faculty_name = course_faculty_name[course_code]
            faculty = resolve_faculty(faculty_name) if faculty_name else None
            resolved = _resolve_course_globally(course_code, faculty=faculty)
            resolved_faculty = faculty or (
                resolved["department"].faculty if resolved and resolved["department"] else None
            )
            # Check if a ResitCourseAllocation already exists for this course+period
            already = False
            if resolved and academic_year and semester:
                already = ResitCourseAllocation.objects.filter(
                    department=department,
                    program=resolved['program'],
                    course_code=course_code,
                    academic_year=academic_year,
                    semester=semester,
                ).exists()
            course_summary[course_code] = {
                "course_code":       course_code,
                "course_name":       resolved["course_name"] if resolved else "— NOT FOUND —",
                "program":           resolved["program"].name if resolved else "",
                "department":        resolved["department"].name if resolved else "",
                "faculty":           resolved_faculty.name if resolved_faculty else (faculty_name or ""),
                "found":             resolved is not None,
                "student_count":     0,
                "already_allocated": already,
            }
        course_summary[course_code]["student_count"] += 1

    return JsonResponse({
        "status":         "ok",
        "total_rows":     len(rows),
        "course_count":   len(course_summary),
        "courses":        list(course_summary.values()),
        "academic_year":  academic_year,
        "semester":       semester,
    })


def _handle_import_students_csv(request, department):
    """
    POST: action=import_students_csv
    Accepts CSV/Excel file with ONLY two required columns:
        reg_no        — student registration number (any format)
        course_code   — course code (e.g. CSC301)

    student_name and program_name are NO LONGER required.
    They are resolved automatically:
        - program_name → looked up via ProgramCourse across all departments
        - student_name → left blank (not stored)

    Courses spanning multiple departments are handled: the system searches all
    programs university-wide to find a matching ProgramCourse and derives the
    correct program + department automatically.

    CSV Format (minimal):
        reg_no,course_code
        EB1/66791/23,CSC301
        66792,MATH201
    """
    if not request.FILES.get('students_file'):
        return JsonResponse({"status": "error", "message": "No file uploaded"}, status=400)

    uploaded_file = request.FILES['students_file']
    filename = uploaded_file.name.lower()

    rows = []
    errors = []

    # ── Parse file ────────────────────────────────────────────────────────────
    try:
        if filename.endswith('.csv'):
            raw = uploaded_file.read()
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('latin-1')
            reader = csv.DictReader(io.StringIO(text))
            rows = [{k.strip().lower().replace(' ', '_'): v for k, v in r.items()}
                    for r in reader]

        elif filename.endswith(('.xlsx', '.xls')):
            workbook = openpyxl.load_workbook(uploaded_file, read_only=True, data_only=True)
            sheet = workbook.active
            header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
            if header_row is None:
                return JsonResponse({"status": "error", "message": "Excel file is empty."}, status=400)

            headers = [str(h).strip().lower().replace(' ', '_') if h else f'col_{i}'
                       for i, h in enumerate(header_row)]

            for row_vals in sheet.iter_rows(min_row=2, values_only=True):
                if not any(row_vals):
                    continue
                row = {headers[i]: (str(v).strip() if v is not None else '')
                       for i, v in enumerate(row_vals) if i < len(headers)}
                rows.append(row)
        else:
            return JsonResponse({
                "status": "error",
                "message": "Unsupported file type. Please upload CSV or Excel (.xlsx, .xls)"
            }, status=400)

    except Exception as e:
        logger.error(f"Error parsing resit CSV: {e}")
        return JsonResponse({"status": "error", "message": f"Error parsing file: {e}"}, status=400)

    if not rows:
        return JsonResponse({"status": "error", "message": "No valid rows found in file."}, status=400)

    # ── Detect columns ────────────────────────────────────────────────────────
    sample = rows[0]
    reg_aliases    = {'reg_no', 'reg_number', 'registration', 'student_id', 'admission', 'reg'}
    course_aliases = {'course_code', 'course', 'code', 'unit_code', 'subject_code'}

    reg_key    = next((k for k in sample if k in reg_aliases), None)
    course_key = next((k for k in sample if k in course_aliases), None)

    if not reg_key or not course_key:
        return JsonResponse({
            "status": "error",
            "message": (
                "File must contain: reg_no (or registration/admission/student_id) "
                "and course_code (or course/code/unit_code). "
                f"Found columns: {', '.join(sorted(sample.keys()))}"
            )
        }, status=400)

    # ── Academic year + semester ──────────────────────────────────────────────
    academic_year = request.POST.get('academic_year', '').strip()
    semester      = request.POST.get('semester', '').strip()
    config = ResitSchedulerConfig.objects.order_by('-id').first()
    if not academic_year and config:
        academic_year = config.academic_year or ''
    if not semester and config:
        semester = config.semester or ''

    # ── Group rows by course_code ─────────────────────────────────────────────
    course_groups = {}        # course_code_upper → [reg_no, ...]
    course_faculty_name = {}  # course_code_upper → faculty name (first seen)
    for i, row in enumerate(rows, start=2):
        reg_no      = str(row.get(reg_key, '') or '').strip()
        course_code = str(row.get(course_key, '') or '').strip().upper()

        if not reg_no:
            errors.append(f"Row {i}: missing reg_no — skipped.")
            continue
        if not course_code:
            errors.append(f"Row {i}: missing course_code — skipped.")
            continue

        course_groups.setdefault(course_code, []).append(reg_no)
        if course_code not in course_faculty_name:
            fname = extract_faculty_name(row)
            if fname:
                course_faculty_name[course_code] = fname

    if not course_groups:
        return JsonResponse({"status": "error", "message": "No valid rows after parsing."}, status=400)

    # ── Create allocations + register students ────────────────────────────────
    allocations_created  = 0
    allocations_updated  = 0
    students_registered  = 0
    duplicates_skipped   = 0
    not_found_courses    = []

    with transaction.atomic():
        for course_code, reg_nos in course_groups.items():
            faculty_name = course_faculty_name.get(course_code, "")
            faculty = resolve_faculty(faculty_name) if faculty_name else None

            # Auto-resolve program, department, course_name from ProgramCourse
            resolved = _resolve_course_globally(course_code, faculty=faculty)
            if not resolved:
                not_found_courses.append(course_code)
                errors.append(f"Course '{course_code}' not found in any program — skipped.")
                continue

            prog_department = resolved["department"]
            program         = resolved["program"]
            program_course  = resolved["program_course"]
            course_name     = resolved["course_name"]
            # Prefer the explicitly imported/resolved faculty, otherwise
            # derive from the resolved department. No lecturer is set here —
            # resits don't track which lecturer teaches which unit.
            resolved_faculty = faculty or getattr(prog_department, "faculty", None)

            try:
                allocation, created = ResitCourseAllocation.objects.get_or_create(
                    program=program,
                    course_code=course_code,
                    academic_year=academic_year,
                    semester=semester,
                    defaults={
                        'department':     prog_department,
                        'faculty':        resolved_faculty,
                        'course_name':    course_name,
                        'program_course': program_course,
                        'created_by':     request.user,
                    }
                )
                if created:
                    allocations_created += 1
                else:
                    allocations_updated += 1

                # Register students
                registered = 0
                for raw_reg in reg_nos:
                    normalized = normalize_reg_number(raw_reg)
                    if not normalized:
                        errors.append(f"Cannot normalize reg '{raw_reg}' for {course_code} — skipped.")
                        continue

                    existing = StudentResitRegistration.objects.filter(
                        resit_allocation=allocation,
                        student_reg_no_normalized=normalized
                    ).exists()
                    if existing:
                        duplicates_skipped += 1
                        continue

                    StudentResitRegistration.objects.create(
                        student_reg_no=raw_reg,
                        student_reg_no_normalized=normalized,
                        student_name='',   # not required; auto-generated if needed later
                        resit_allocation=allocation,
                        registered_by=request.user,
                    )
                    registered += 1

                students_registered += registered
                allocation.number_of_students = allocation.student_registrations.count()
                allocation.save(update_fields=['number_of_students'])

            except Exception as e:
                errors.append(f"Error processing '{course_code}': {e}")

    return JsonResponse({
        "status": "success",
        "summary": {
            "allocations_created":  allocations_created,
            "allocations_updated":  allocations_updated,
            "students_registered":  students_registered,
            "duplicates_skipped":   duplicates_skipped,
            "errors_count":         len(errors),
            "not_found_courses":    not_found_courses[:15],
        },
        "errors": errors[:25],
    })


def _handle_get_student_list(request, department):
    """
    GET/POST: action=get_student_list
    Returns list of students registered for a specific allocation.
    """
    allocation_id = request.POST.get('allocation_id')
    if not allocation_id:
        return JsonResponse({"status": "error", "message": "allocation_id required"}, status=400)
    
    try:
        allocation = ResitCourseAllocation.objects.get(
            pk=allocation_id,
            department=department
        )
    except ResitCourseAllocation.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Allocation not found"}, status=404)
    
    students = allocation.student_registrations.values(
        'id', 'student_reg_no', 'student_reg_no_normalized', 'student_name'
    ).order_by('student_reg_no_normalized')
    
    return JsonResponse({
        "status": "success",
        "allocation_id": allocation.id,
        "course_code": allocation.course_code,
        "total_students": students.count(),
        "students": list(students),
    })


def _handle_remove_student(request, department):
    """
    POST: action=remove_student, registration_id=...
    Removes a student from a resit allocation.
    """
    if not _can_mutate(request.user):
        return JsonResponse({"status": "error", "message": "Permission denied."}, status=403)
    
    registration_id = request.POST.get('registration_id')
    if not registration_id:
        return JsonResponse({"status": "error", "message": "registration_id required"}, status=400)
    
    try:
        registration = StudentResitRegistration.objects.get(
            pk=registration_id,
            resit_allocation__department=department
        )
        allocation = registration.resit_allocation
        registration.delete()
        
        # Update student count
        allocation.number_of_students = allocation.student_registrations.count()
        allocation.save()
        
        return JsonResponse({
            "status": "success",
            "message": "Student removed successfully",
            "new_count": allocation.number_of_students,
        })
    except StudentResitRegistration.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Registration not found"}, status=404)


def _handle_add_student(request, department):
    """
    POST: action=add_student, allocation_id=..., reg_no=..., student_name=...
    Manually add a student to a resit allocation.
    """
    if not _can_mutate(request.user):
        return JsonResponse({"status": "error", "message": "Permission denied."}, status=403)
    
    allocation_id = request.POST.get('allocation_id')
    reg_no = request.POST.get('reg_no', '').strip()
    student_name = request.POST.get('student_name', '').strip()
    
    if not allocation_id or not reg_no:
        return JsonResponse({"status": "error", "message": "allocation_id and reg_no required"}, status=400)
    
    try:
        allocation = ResitCourseAllocation.objects.get(pk=allocation_id, department=department)
    except ResitCourseAllocation.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Allocation not found"}, status=404)
    
    normalized = normalize_reg_number(reg_no)
    
    # Check for duplicate
    if StudentResitRegistration.objects.filter(
        resit_allocation=allocation,
        student_reg_no_normalized=normalized
    ).exists():
        return JsonResponse({
            "status": "error",
            "message": f"Student {normalized} is already registered for this course"
        }, status=400)
    
    registration = StudentResitRegistration.objects.create(
        student_reg_no=reg_no,
        student_name=student_name,
        resit_allocation=allocation,
        registered_by=request.user,
    )
    
    # Update student count
    allocation.number_of_students = allocation.student_registrations.count()
    allocation.save()
    
    return JsonResponse({
        "status": "success",
        "message": "Student added successfully",
        "registration": {
            "id": registration.id,
            "student_reg_no": registration.student_reg_no,
            "student_reg_no_normalized": registration.student_reg_no_normalized,
            "student_name": registration.student_name,
        },
        "new_count": allocation.number_of_students,
    })


# ───────────────────────────────────────────────────────────────
# Main view
# ───────────────────────────────────────────────────────────────

_MUTATE_GROUPS = {"COD", "COD Admins", "Timetabling Admins", "Timetabler"}

def _can_mutate(user):
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=_MUTATE_GROUPS).exists()


def _handle_delete_resit_archive_group(request, department):
    """
    POST: action=delete_resit_archive_group, academic_year=..., semester=...
    Permanently removes all ResitArchivedAllocation rows for that period.
    """
    if not _can_mutate(request.user):
        return JsonResponse({"status": "error", "message": "Permission denied."}, status=403)
    
    academic_year = request.POST.get("academic_year", "").strip()
    semester = request.POST.get("semester", "").strip()
    
    if not academic_year or not semester:
        return JsonResponse(
            {"status": "error", "message": "Both Academic Year and Semester are required."},
            status=400,
        )

    qs = ResitArchivedAllocation.objects.filter(
        department=department,
        academic_year=academic_year,
        semester=semester,
    )
    count = qs.count()
    if count == 0:
        return JsonResponse({"status": "success", "message": "No records found.", "deleted": 0})

    with transaction.atomic():
        deleted, _ = qs.delete()

    logger.info(
        "resit_import: %s deleted resit archive group (dept=%s, year=%s, sem=%s, rows=%d)",
        request.user, department, academic_year, semester, deleted,
    )
    return JsonResponse({
        "status":   "success",
        "deleted":  deleted,
        "message":  f"{deleted} archived record(s) permanently deleted.",
    })


def _handle_restore_resit_archive(request, department):
    """
    POST: action=restore_resit_archive, academic_year=..., semester=...
    Re-creates ResitCourseAllocation rows from ResitArchivedAllocation,
    skipping duplicates, then removes the restored archive rows.
    """
    if not _can_mutate(request.user):
        return JsonResponse({"status": "error", "message": "Permission denied."}, status=403)
    
    academic_year = request.POST.get("academic_year", "").strip()
    semester = request.POST.get("semester", "").strip()
    
    if not academic_year or not semester:
        return JsonResponse(
            {"status": "error", "message": "Both Academic Year and Semester are required."},
            status=400,
        )

    archive_qs = ResitArchivedAllocation.objects.filter(
        department=department,
        academic_year=academic_year,
        semester=semester,
    ).select_related("department", "origin_department", "program", "program_course", "lecturer")

    archived_rows = list(archive_qs)
    if not archived_rows:
        return JsonResponse({
            "status": "success",
            "message": "No archived records found for that period.",
            "restored": 0, "skipped": 0,
        })

    existing_keys = set(
        ResitCourseAllocation.objects.filter(department=department)
        .values_list("program_id", "course_code", "academic_year", "semester")
    )

    to_create = []
    restored_ids = []
    skipped = 0

    for a in archived_rows:
        key = (a.program_id, a.course_code, a.academic_year, a.semester)
        if key in existing_keys:
            skipped += 1
            continue
        to_create.append(ResitCourseAllocation(
            course_code              = a.course_code,
            course_name              = a.course_name,
            department               = a.department,
            origin_department        = a.origin_department,
            program                  = a.program,
            program_course           = a.program_course,
            lecturer                 = a.lecturer,
            number_of_students       = a.number_of_students,
            submitted_to_timetabling = False,
            scheduled                = False,
            academic_year            = a.academic_year,
            semester                 = a.semester,
            created_by               = request.user,
        ))
        existing_keys.add(key)
        restored_ids.append(a.id)

    restored = 0
    with transaction.atomic():
        if to_create:
            ResitCourseAllocation.objects.bulk_create(to_create, batch_size=200)
            restored = len(to_create)
        if restored_ids:
            ResitArchivedAllocation.objects.filter(id__in=restored_ids).delete()

    logger.info(
        "resit_import: %s restored %d allocations from resit archive "
        "(dept=%s, year=%s, sem=%s, skipped=%d)",
        request.user, restored, department, academic_year, semester, skipped,
    )
    return JsonResponse({
        "status":    "success",
        "restored":  restored,
        "skipped":   skipped,
        "message":   (
            f"{restored} allocation(s) restored to active list "
            + (f", {skipped} skipped (already exist)." if skipped else ".")
        ),
    })


def _handle_toggle_resit_venue_exclusion(request, department):
    """
    POST: action=toggle_resit_venue_exclusion, id=<ResitVenueExclusion pk>, is_active=true|false

    Quick on/off switch for an existing resit venue/building exclusion,
    surfaced on this import page so admins can see (and adjust) which
    venues won't be used by the resit auto-scheduler without leaving the
    page. Creating brand-new exclusions still happens on /venues/ — this
    is a toggle only, restricted to timetabling admins (not COD/COD_ADMIN,
    since a department head shouldn't be able to open up or shut off
    university-wide venue availability for resits).
    """
    if not user_has_role(request.user, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN):
        return JsonResponse({"status": "error", "message": "Permission denied."}, status=403)

    excl_id = request.POST.get("id")
    is_active = request.POST.get("is_active") == "true"

    try:
        excl = ResitVenueExclusion.objects.get(pk=excl_id)
    except (ResitVenueExclusion.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"status": "error", "message": "Exclusion not found."}, status=404)

    excl.is_active = is_active
    excl.save(update_fields=["is_active"])

    return JsonResponse({"status": "success", "id": excl.id, "is_active": excl.is_active})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def resit_import_panel(request):
    """
    GET  → render the import page (data loads automatically via AJAX on DOMContentLoaded)
    POST (AJAX, action=auto_load)                 → return all sources in one payload
    POST (AJAX, action=do_import)                 → execute import, return result JSON
    POST (AJAX, action=delete_resit_archive_group)→ permanently delete an archive group
    POST (AJAX, action=restore_resit_archive)     → restore an archive group to active
    POST (AJAX, action=import_students_csv)       → import students from CSV/Excel
    POST (AJAX, action=get_student_list)          → get list of students for allocation
    POST (AJAX, action=remove_student)            → remove student from allocation
    POST (AJAX, action=add_student)               → manually add student to allocation
    """
    try:
        department = get_user_department(request.user)
        if not department:
            ctx = {
                "department":    None,
                "programs":      Program.objects.none(),
                "departments":   Department.objects.all(),
                "academic_years": ["2023/2024", "2024/2025", "2025/2026", "2026/2027"],
                "error": "No department associated with your account. Contact an administrator.",
            }
            return render(request, "resits_timetabling/resit_import.html", ctx)
        
        # ── AJAX ──────────────────────────────────────────────────────────────────
        if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
            action = request.POST.get("action")

            if action == "auto_load":
                return _handle_auto_load(request, department)

            if action == "do_import":
                return _handle_import(request, department)

            if action == "delete_resit_archive_group":
                return _handle_delete_resit_archive_group(request, department)

            if action == "restore_resit_archive":
                return _handle_restore_resit_archive(request, department)
            
            # Student CSV preview (dry-run — no DB writes)
            if action == "preview_students_csv":
                return _handle_preview_students_csv(request, department)

            # NEW: Student CSV import
            if action == "import_students_csv":
                return _handle_import_students_csv(request, department)
            
            # NEW: Get student list for allocation
            if action == "get_student_list":
                return _handle_get_student_list(request, department)
            
            # NEW: Remove student from allocation
            if action == "remove_student":
                return _handle_remove_student(request, department)
            
            # NEW: Add single student
            if action == "add_student":
                return _handle_add_student(request, department)

            # NEW: Quick on/off toggle for a resit venue/building exclusion,
            # restricted to timetabling admins — COD users can view but not
            # change which venues/buildings are excluded from resits.
            if action == "toggle_resit_venue_exclusion":
                return _handle_toggle_resit_venue_exclusion(request, department)

            return JsonResponse({"status": "error", "message": "Unknown action."}, status=400)

        # ── GET ───────────────────────────────────────────────────────────────────
        programs    = Program.objects.filter(department=department).order_by("name")
        departments = Department.objects.select_related("faculty").order_by("name")
        config      = ResitSchedulerConfig.objects.order_by("-id").first()

        can_manage_venue_exclusions = user_has_role(
            request.user, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN
        )
        resit_venue_exclusions = (
            ResitVenueExclusion.objects
            .select_related("building", "venue", "venue__building")
            .all().order_by("building__name", "venue__code")
        )

        ctx = {
            "department":    department,
            "programs":      programs,
            "departments":   departments,
            "config":        config,
            "academic_years": ["2023/2024", "2024/2025", "2025/2026", "2026/2027"],
            "resit_venue_exclusions": resit_venue_exclusions,
            "can_manage_venue_exclusions": can_manage_venue_exclusions,
        }
        return render(request, "resits_timetabling/resit_import.html", ctx)

    except Exception as exc:
        logger.error("resit_import_panel unhandled exception: %s", traceback.format_exc())
        # Always return JSON for AJAX callers; fall back to JSON for page requests too
        # so the browser console shows the real error rather than an HTML 500 page.
        if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"status": "error", "message": str(exc)}, status=500)
        # For GET requests re-render with an error message rather than crashing
        ctx = {
            "department":    None,
            "programs":      Program.objects.none(),
            "departments":   Department.objects.all(),
            "academic_years": ["2023/2024", "2024/2025", "2025/2026", "2026/2027"],
            "error": f"An unexpected error occurred: {exc}",
        }
        return render(request, "resits_timetabling/resit_import.html", ctx)