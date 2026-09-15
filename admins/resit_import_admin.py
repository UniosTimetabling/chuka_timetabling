"""
admins/resit_import_admin.py

Admin-level resit student import view.

Unlike the COD panel (resits_timetabling/resit_import.py), this view:
  - Is accessible to superusers / Timetabling Admins only.
  - Is NOT bound to any department — the admin can import students for
    courses across ALL departments in one upload.
  - Resolves course → program → department automatically by searching
    ProgramCourse university-wide; no department context is needed or used.

CSV / Excel format — TWO required columns, ONE optional column:
    reg_no        Student registration number (any format)
    course_code   Course code (e.g. CSC301, MATH201)
    faculty       OPTIONAL. Faculty this course belongs to (e.g. "Faculty of
                  Science"). Also accepted under the headers faculty_name,
                  faculty_code, school, school_name. When supplied it is
                  matched against faculty_management.Faculty and stored on
                  the ResitCourseAllocation, and is also used to disambiguate
                  course codes that exist under more than one department. If
                  omitted (or not recognised), the faculty is auto-derived
                  from the resolved department instead — no row is ever
                  rejected for missing/unmatched faculty.

Optional / ignored columns:
    student_name  — NOT required; left blank (no name lookup)
    program_name  — NOT required; resolved from ProgramCourse automatically
    department    — NOT required; resolved from program → department

Resits do not track which lecturer teaches which unit — this import never
sets or looks up a lecturer on the ResitCourseAllocation.

How it works
------------
1. Parse the uploaded file (CSV or .xlsx / .xls).
2. Group rows by unique course_code, noting the faculty column if present.
3. For each course_code, search ProgramCourse across ALL programs/departments,
   preferring a department under the imported faculty when one is given.
4. find_or_create ResitCourseAllocation using the resolved program + department
   + faculty, and the academic_year/semester from POST params or the active
   ResitSchedulerConfig.
5. Normalise all registration numbers (numeric portion only).
6. Bulk-create StudentResitRegistration rows, skipping exact duplicates.
7. Return JSON summary + error list.
"""

import csv
import io
import logging
import re
import traceback

import openpyxl
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render

from core.group_required import group_required
from department_management.models import Department
from program_management.models import ProgramCourse

from resits_timetabling.models import (
    ResitCourseAllocation,
    ResitSchedulerConfig,
    StudentResitRegistration,
)
from resits_timetabling.faculty_utils import (
    FACULTY_ALIASES,
    extract_faculty_name,
    resolve_faculty,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_reg(reg_no: str) -> str:
    """
    Extract the core numeric portion from any registration-number format.
        "EB1/66791/23"          → "66791"
        "SCH/12345/22"          → "12345"
        "66791"                 → "66791"
        "EB1/66791/23 (J. Doe)" → "66791"
    Falls back to stripping all non-digits when no 4+-digit run is found.
    """
    if not reg_no:
        return ""
    reg_no = re.sub(r"\s*\([^)]*\)", "", str(reg_no)).strip()
    m = re.search(r"(\d{4,})", reg_no)
    if m:
        return m.group(1)
    return re.sub(r"\D", "", reg_no)


def _normalize_code(code: str) -> str:
    return str(code).strip().upper() if code else ""


def _resolve_course(course_code: str, faculty=None) -> dict | None:
    """
    Find the best matching ProgramCourse for course_code across the entire
    university (all departments, all programs).

    Args:
        course_code: the code to resolve.
        faculty: an optional resolved Faculty instance (see
            faculty_utils.resolve_faculty). When given, a ProgramCourse whose
            department belongs to that faculty is preferred — this is what
            disambiguates a course_code that exists under more than one
            department/faculty in the imported file.

    Returns:
        { course_code, course_name, program, department, program_course }
    or None if not found.

    Tie-breaking when the same code appears in multiple programs and no
    faculty was supplied (or none of the matches belong to it):
        Fall back to alphabetical order by department name.
    """
    code_upper = _normalize_code(course_code)
    if not code_upper:
        return None

    qs = (
        ProgramCourse.objects
        .filter(course_code__iexact=code_upper)
        .select_related("program", "program__department", "program__department__faculty")
        .order_by("program__department__name", "program__name")
    )

    if not qs.exists():
        # Try without spaces (e.g. "CSC 301" → "CSC301")
        code_nospace = code_upper.replace(" ", "")
        qs = (
            ProgramCourse.objects
            .filter(course_code__iregex=r"^" + re.escape(code_nospace) + r"$")
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


# ─────────────────────────────────────────────────────────────────────────────
# File parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_file(uploaded_file) -> tuple[list[dict], str | None]:
    """
    Parse a CSV or Excel upload into a list of normalised row dicts.
    Returns (rows, error_message_or_None).
    """
    filename = uploaded_file.name.lower()
    rows = []

    try:
        if filename.endswith(".csv"):
            raw = uploaded_file.read()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            for row in csv.DictReader(io.StringIO(text)):
                rows.append({k.strip().lower().replace(" ", "_"): v for k, v in row.items()})

        elif filename.endswith((".xlsx", ".xls")):
            wb = openpyxl.load_workbook(uploaded_file, read_only=True, data_only=True)
            ws = wb.active
            header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
            if header_row is None:
                return [], "Excel file appears to be empty."
            headers = [
                str(h).strip().lower().replace(" ", "_") if h else f"col_{i}"
                for i, h in enumerate(header_row)
            ]
            for row_vals in ws.iter_rows(min_row=2, values_only=True):
                if not any(row_vals):
                    continue
                rows.append({
                    headers[i]: (str(v).strip() if v is not None else "")
                    for i, v in enumerate(row_vals) if i < len(headers)
                })
        else:
            return [], "Unsupported file type. Please upload a .csv or .xlsx file."

    except Exception as exc:
        logger.error("admin_resit_import: file parse error: %s", exc)
        return [], f"Could not parse file: {exc}"

    return rows, None


_REG_ALIASES    = {"reg_no", "reg_number", "registration", "student_id", "admission", "admission_no", "reg"}
_COURSE_ALIASES = {"course_code", "course", "code", "unit_code", "subject_code"}


def _extract_fields(row: dict) -> tuple[str, str]:
    """Pull reg_no and course_code from a normalised row dict."""
    reg_no = course_code = ""
    for k, v in row.items():
        k2 = k.strip().lower().replace(" ", "_")
        if k2 in _REG_ALIASES and not reg_no:
            reg_no = str(v).strip() if v else ""
        elif k2 in _COURSE_ALIASES and not course_code:
            course_code = str(v).strip().upper() if v else ""
    return reg_no, course_code


def _validate_headers(rows: list[dict]) -> str | None:
    """Return an error string if required columns are missing, else None."""
    if not rows:
        return "File is empty."
    sample = set(rows[0].keys())
    if not (sample & _REG_ALIASES):
        return (
            "Missing reg_no column. "
            f"Accepted names: {', '.join(sorted(_REG_ALIASES))}. "
            f"Found: {', '.join(sorted(sample))}"
        )
    if not (sample & _COURSE_ALIASES):
        return (
            "Missing course_code column. "
            f"Accepted names: {', '.join(sorted(_COURSE_ALIASES))}. "
            f"Found: {', '.join(sorted(sample))}"
        )
    return None


# Note: resits deliberately do NOT autofill or resolve a lecturer on import —
# which lecturer teaches a unit isn't tracked for resits. Lecturer stays
# unset here; it can still be assigned manually elsewhere if ever needed.


# ─────────────────────────────────────────────────────────────────────────────
# Preview (dry-run — no DB writes)
# ─────────────────────────────────────────────────────────────────────────────

def _do_preview(uploaded_file) -> dict:
    rows, err = _parse_file(uploaded_file)
    if err:
        return {"status": "error", "message": err}
    col_err = _validate_headers(rows)
    if col_err:
        return {"status": "error", "message": col_err}

    course_summary: dict[str, dict] = {}
    course_faculty_name: dict[str, str] = {}
    for row in rows:
        reg_no, course_code = _extract_fields(row)
        if not reg_no or not course_code:
            continue
        if course_code not in course_faculty_name:
            course_faculty_name[course_code] = extract_faculty_name(row)
        if course_code not in course_summary:
            faculty_name = course_faculty_name[course_code]
            faculty = resolve_faculty(faculty_name) if faculty_name else None
            resolved = _resolve_course(course_code, faculty=faculty)
            resolved_faculty = faculty or (
                resolved["department"].faculty if resolved and resolved["department"] else None
            )
            course_summary[course_code] = {
                "course_code":   course_code,
                "course_name":   resolved["course_name"] if resolved else "— NOT FOUND —",
                "program":       resolved["program"].name if resolved else "",
                "department":    resolved["department"].name if resolved else "",
                "faculty":       resolved_faculty.name if resolved_faculty else (faculty_name or ""),
                "found":         resolved is not None,
                "student_count": 0,
            }
        course_summary[course_code]["student_count"] += 1

    return {
        "status":       "ok",
        "total_rows":   len(rows),
        "course_count": len(course_summary),
        "courses":      list(course_summary.values()),
        "headers":      list(rows[0].keys()) if rows else [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Import (writes to DB)
# ─────────────────────────────────────────────────────────────────────────────

def _do_import(request, uploaded_file) -> dict:
    rows, err = _parse_file(uploaded_file)
    if err:
        return {"status": "error", "message": err}
    col_err = _validate_headers(rows)
    if col_err:
        return {"status": "error", "message": col_err}

    # Academic year + semester — from POST or active config
    academic_year = request.POST.get("academic_year", "").strip()
    semester      = request.POST.get("semester", "").strip()
    config = ResitSchedulerConfig.objects.order_by("-id").first()
    if not academic_year and config:
        academic_year = config.academic_year or ""
    if not semester and config:
        semester = config.semester or ""

    # Group by course_code, remembering the first faculty value seen per course
    course_groups: dict[str, list[str]] = {}
    course_faculty_name: dict[str, str] = {}
    parse_warnings: list[str] = []

    for i, row in enumerate(rows, start=2):
        reg_no, course_code = _extract_fields(row)
        reg_no      = reg_no.strip()
        course_code = _normalize_code(course_code)
        if not reg_no:
            parse_warnings.append(f"Row {i}: missing reg_no — skipped.")
            continue
        if not course_code:
            parse_warnings.append(f"Row {i}: missing course_code — skipped.")
            continue
        course_groups.setdefault(course_code, []).append(reg_no)
        if course_code not in course_faculty_name:
            fname = extract_faculty_name(row)
            if fname:
                course_faculty_name[course_code] = fname

    if not course_groups:
        return {"status": "error", "message": "No valid rows found after parsing.", "warnings": parse_warnings}

    # Process
    allocations_created  = 0
    allocations_existing = 0
    students_registered  = 0
    duplicates_skipped   = 0
    not_found_codes: list[str] = []
    errors = [*parse_warnings]

    with transaction.atomic():
        for course_code, reg_nos in course_groups.items():
            faculty_name = course_faculty_name.get(course_code, "")
            faculty = resolve_faculty(faculty_name) if faculty_name else None

            resolved = _resolve_course(course_code, faculty=faculty)
            if not resolved:
                not_found_codes.append(course_code)
                errors.append(f"Course '{course_code}' not found in any program — skipped.")
                continue

            department     = resolved["department"]
            program        = resolved["program"]
            program_course = resolved["program_course"]
            course_name    = resolved["course_name"]
            # Faculty: prefer the explicitly imported/resolved one, otherwise
            # derive from the resolved department. Resits don't track a
            # lecturer, so it's intentionally never set here.
            resolved_faculty = faculty or getattr(department, "faculty", None)

            try:
                allocation, created = ResitCourseAllocation.objects.get_or_create(
                    program=program,
                    course_code=course_code,
                    academic_year=academic_year,
                    semester=semester,
                    defaults={
                        "department":     department,
                        "faculty":        resolved_faculty,
                        "course_name":    course_name,
                        "program_course": program_course,
                        "created_by":     request.user,
                    },
                )
                if created:
                    allocations_created += 1
                else:
                    allocations_existing += 1

                for raw_reg in reg_nos:
                    normalized = _normalize_reg(raw_reg)
                    if not normalized:
                        errors.append(f"Could not normalize reg '{raw_reg}' for {course_code} — skipped.")
                        continue
                    if StudentResitRegistration.objects.filter(
                        resit_allocation=allocation,
                        student_reg_no_normalized=normalized,
                    ).exists():
                        duplicates_skipped += 1
                        continue
                    StudentResitRegistration.objects.create(
                        resit_allocation=allocation,
                        student_reg_no=raw_reg,
                        student_reg_no_normalized=normalized,
                        student_name="",
                        registered_by=request.user,
                    )
                    students_registered += 1

                allocation.number_of_students = allocation.student_registrations.count()
                allocation.save(update_fields=["number_of_students"])

            except Exception as exc:
                logger.error("admin_resit_import: error for %s: %s", course_code, exc)
                errors.append(f"Error processing '{course_code}': {exc}")

    return {
        "status": "success",
        "summary": {
            "allocations_created":  allocations_created,
            "allocations_existing": allocations_existing,
            "students_registered":  students_registered,
            "duplicates_skipped":   duplicates_skipped,
            "courses_not_found":    len(not_found_codes),
            "not_found_codes":      not_found_codes[:20],
        },
        "errors":        errors[:30],
        "academic_year": academic_year,
        "semester":      semester,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Stats helper for the GET page
# ─────────────────────────────────────────────────────────────────────────────

def _get_stats() -> dict:
    return {
        "total_allocations": ResitCourseAllocation.objects.count(),
        "total_students":    StudentResitRegistration.objects.count(),
        "departments":       Department.objects.count(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# View
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@group_required("Timetabling Admins", "Timetabler", "COD Admins")
def admin_resit_import_view(request):
    """
    GET  → render the admin import page (no department context required).
    POST (action=preview) → parse file, return preview JSON (no DB writes).
    POST (action=import)  → parse file, write to DB, return summary JSON.

    The admin is NOT tied to any department.  Course → program → department
    is resolved automatically for every row in the uploaded file.
    """
    # Superusers bypass the group check
    if not (
        request.user.is_superuser
        or request.user.groups.filter(
            name__in=["Timetabling Admins", "Timetabler", "COD Admins"]
        ).exists()
    ):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Access denied.")

    if request.method == "POST":
        action = request.POST.get("action", "import")

        if action == "preview":
            if not request.FILES.get("import_file"):
                return JsonResponse({"status": "error", "message": "No file uploaded."}, status=400)
            return JsonResponse(_do_preview(request.FILES["import_file"]))

        if action == "import":
            if not request.FILES.get("import_file"):
                return JsonResponse({"status": "error", "message": "No file uploaded."}, status=400)
            try:
                result = _do_import(request, request.FILES["import_file"])
            except Exception as exc:
                logger.error("admin_resit_import_view unhandled: %s", traceback.format_exc())
                result = {"status": "error", "message": str(exc)}
            return JsonResponse(result)

        return JsonResponse({"status": "error", "message": "Unknown action."}, status=400)

    # ── GET ──────────────────────────────────────────────────────────────────
    config = ResitSchedulerConfig.objects.order_by("-id").first()
    ctx = {
        "config":         config,
        "stats":          _get_stats(),
        "departments":    Department.objects.select_related("faculty").order_by("name"),
        "academic_years": ["2022/2023", "2023/2024", "2024/2025", "2025/2026", "2026/2027"],
        "current_year":   config.academic_year if config else "",
        "current_sem":    config.semester if config else "",
    }
    return render(request, "admins/resit_import_admin.html", ctx)