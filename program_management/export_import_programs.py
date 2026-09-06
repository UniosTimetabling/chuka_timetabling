# export_import_programs.py
import csv
import io
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.conf import settings

from backup_system.signals import get_audit_context
from core.rbac import get_user_roles
from department_management.models import Department
from program_management.models import Program, ProgramCourse, ProgramCode
from program_management.programs_page import detect_user_department
from program_management.code_utils import normalize_code, canonical_course_key

logger = logging.getLogger(__name__)

PENDING_IMPORT_TTL = 15 * 60  # 15 minutes


def _pending_key(token):
    return f"prog_import_pending:{token}"


# ─────────────────────────────────────────────────────────────────────────────
# EXPORT LOGGING
# ─────────────────────────────────────────────────────────────────────────────
# Every export is appended as a row to program_management/logs/export_log.csv
# (created automatically on first export). This is a simple, human-readable
# audit trail of who exported the Programs/Courses master data, and when.

EXPORT_LOGS_DIR = Path(settings.BASE_DIR) / "program_management" / "logs"
EXPORT_LOG_FILE = EXPORT_LOGS_DIR / "export_log.csv"
EXPORT_LOG_HEADERS = [
    "date", "time", "username", "full_name", "role", "department",
    "format", "filename", "email_copy", "terms_agreed",
]


def _log_export_event(user, detected_dept, fmt, filename, email_copy, terms_agreed):
    """
    Append one row to program_management/logs/export_log.csv recording who
    exported the Programs/Courses master data, and when. Never raises —
    a logging failure should never block or fail an export.
    """
    try:
        EXPORT_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        is_new_file = not EXPORT_LOG_FILE.exists()

        roles = get_user_roles(user)
        role_str = ", ".join(sorted(roles)) if roles else "none"
        now = datetime.now()

        with open(EXPORT_LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_new_file:
                writer.writerow(EXPORT_LOG_HEADERS)
            writer.writerow([
                now.strftime("%Y-%m-%d"),
                now.strftime("%H:%M:%S"),
                user.username,
                user.get_full_name() or user.username,
                role_str,
                detected_dept.name if detected_dept else "All Departments",
                fmt,
                filename,
                "yes" if email_copy else "no",
                "yes" if terms_agreed else "no",
            ])
    except Exception as e:
        logger.warning("Failed to write export log entry: %s", e)


# Optional password-protection libraries. Everything degrades gracefully
# (plain export/import keeps working) if these aren't installed.
try:
    import msoffcrypto
    HAS_MSOFFCRYPTO = True
except ImportError:
    HAS_MSOFFCRYPTO = False

try:
    import pyzipper
    HAS_PYZIPPER = True
except ImportError:
    HAS_PYZIPPER = False

try:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

MAX_UPLOAD_BYTES = 5 * 1024 * 1024

# Headers
PROGRAM_HEADERS = ["program_id", "program_name", "department", "description", "default_cohort"]
COURSE_HEADERS = ["course_id", "program_name", "course_code", "course_name", "year", "semester", "unit_type", "student_cohort"]
CODE_HEADERS = ["code_id", "program_name", "code"]
CSV_HEADERS = [
    "row_type", "id", "program_name", "department", "description", "default_cohort",
    "course_code", "course_name", "year", "semester", "unit_type", "student_cohort",
    "code",
]

INSTRUCTIONS = [
    "How to use this file",
    "",
    "1. This workbook has three data sheets: 'Programs', 'Courses', and 'Program Codes'.",
    "2. To EDIT a program, course, or code, change any of its fields but leave the id column alone.",
    "3. To ADD a new program, course, or code, add a new row and leave the id column blank.",
    "4. To DELETE a program, course, or code, simply delete its entire row, then re-upload this file.",
    "5. On 'Courses', the 'program_name' column must exactly match a name on the 'Programs' sheet.",
    "6. On 'Program Codes', the 'program_name' column must exactly match a name on the 'Programs' sheet.",
    "7. Re-upload the edited file on the Programs page using the Import button.",
    "8. Rows/sheets you leave out entirely are left untouched — e.g. a file with only a",
    "   'Courses' sheet will add/edit/delete courses without touching any programs.",
    "",
    "PASSWORD-PROTECTED FILES:",
    "   - If you exported with a password, you'll need that same password to re-open",
    "     the file locally (Excel will ask for it) AND to re-upload it here.",
    "   - The password is never stored — only the person who exported it, and anyone",
    "     it was emailed to, will know it.",
    "",
    "FIELDS:",
    "   - unit_type: CORE, ELECTIVE, UNIVERSITY_WIDE, REQUIRED_ELECTIVE (default: CORE)",
    "   - student_cohort: '0' for legacy, or year like '2024', '2024/2025'",
    "   - default_cohort (on Programs): default cohort for new students",
    "   - Program Codes: each program can have multiple unique codes",
    "",
    "TERMS OF USE (agreed to by the exporter before this file was generated):",
    "   - This Excel file is to be used for this system only. It must not be shared",
    "     with any third-party system, or moved / copied to a different device.",
    "   - This file's sole purpose is to update the course master. It should not be shared.",
    "   - Sharing this file is done at the sharer's own responsibility for any resulting harm.",
    "   - Once you are done using this file, delete it from your device.",
]


def _scope_programs(user):
    """Return (detected_dept_or_None, programs_queryset_in_scope)."""
    detected_dept = detect_user_department(user)
    if detected_dept:
        qs = Program.objects.filter(department=detected_dept)
    else:
        qs = Program.objects.all()
    return detected_dept, qs.select_related("department").prefetch_related("courses")


# ─────────────────────────────────────────────────────────────────────────────
# EXPORT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def export_programs(request):
    """
    Excel-only export, always password-protected, and only after the
    exporter has agreed to the usage terms (see the export modal in
    programs.html). Every export is appended to
    program_management/logs/export_log.csv (date, time, user, role).

    NOTE: CSV export has been disabled (commented out below) — Excel (.xlsx)
    is now the only export format. CSV *import* still works fine; only the
    CSV *export* branch is disabled. Plain, unprotected export has also been
    removed: every export (GET or POST) now requires a password.
    """
    fmt = (request.POST.get("fmt") or request.GET.get("fmt") or "xlsx").strip().lower()
    password = (request.POST.get("password") or "").strip()
    email_copy = (request.POST.get("email_copy") or "").strip().lower() == "true"
    terms_agreed = (request.POST.get("agree_terms") or request.GET.get("agree_terms") or "").strip().lower() == "true"

    if request.method not in ("GET", "POST"):
        return HttpResponseBadRequest("Unsupported method.")

    detected_dept, programs = _scope_programs(request.user)
    programs = list(programs.order_by("department__name", "name"))

    # ── CSV export disabled — Excel is now the only export format. ─────────
    # if fmt == "csv":
    #     if password:
    #         if not HAS_PYZIPPER:
    #             return HttpResponse("pyzipper is not installed on the server. Cannot create a password-protected CSV.", status=500)
    #         return _export_encrypted_csv(programs, password, request.user, detected_dept, email_copy)
    #     return _export_csv(programs)
    if fmt == "csv":
        return HttpResponseBadRequest(
            "CSV export has been disabled. Please use Excel (.xlsx) export instead. "
            "(CSV import is still supported.)"
        )

    if fmt in ("xlsx", "excel"):
        if not HAS_OPENPYXL:
            return HttpResponse("openpyxl is not installed on the server.", status=500)
        if not password:
            return HttpResponseBadRequest(
                "A password is required to export. Unprotected export is no longer available — "
                "please use the Export with Password option."
            )
        if not terms_agreed:
            return HttpResponseBadRequest(
                "You must agree to the usage terms before exporting."
            )
        if not HAS_MSOFFCRYPTO:
            return HttpResponse("msoffcrypto-tool is not installed on the server. Cannot create a password-protected Excel file.", status=500)
        response = _export_encrypted_xlsx(programs, password, request.user, detected_dept, email_copy)
        filename = "programs_and_courses_protected.xlsx"
        disposition = response.get("Content-Disposition", "")
        if "filename=" in disposition:
            filename = disposition.split("filename=")[-1].strip('"')
        _log_export_event(request.user, detected_dept, "xlsx", filename, email_copy, terms_agreed)
        return response

    return HttpResponseBadRequest("Unsupported format. Only Excel (.xlsx) export is supported.")


# ── CSV export disabled — these helpers are only used by the disabled CSV
# export branch above. CSV *import* does not use these (see _parse_csv
# further down, which is unaffected). Left in place, commented out, in case
# CSV export needs to be restored later.
#
# def _csv_rows(programs):
#     rows = [CSV_HEADERS]
#     for p in programs:
#         rows.append([
#             "PROGRAM", p.id, p.name, p.department.name, p.description or "", p.default_cohort or "0",
#             "", "", "", "", "", "", "",
#         ])
#         for c in p.courses.all().order_by("year", "semester", "course_code"):
#             rows.append([
#                 "COURSE", c.id, p.name, "", "", "",
#                 c.course_code, c.course_name, c.year, c.semester, c.unit_type or "CORE", c.student_cohort or "0", "",
#             ])
#         for code in p.program_codes.all().order_by("code"):
#             rows.append([
#                 "CODE", code.id, p.name, "", "", "",
#                 "", "", "", "", "", "", code.code,
#             ])
#     return rows
#
#
# def _export_csv(programs):
#     response = HttpResponse(content_type="text/csv; charset=utf-8")
#     response["Content-Disposition"] = 'attachment; filename="programs_and_courses.csv"'
#     writer = csv.writer(response)
#     for row in _csv_rows(programs):
#         writer.writerow(row)
#     return response
#
#
# def _export_encrypted_csv(programs, password, user, detected_dept, email_copy):
#     """Export CSV wrapped in a password-protected (AES) ZIP."""
#     csv_buffer = io.StringIO()
#     writer = csv.writer(csv_buffer)
#     for row in _csv_rows(programs):
#         writer.writerow(row)
#     csv_data = csv_buffer.getvalue().encode("utf-8-sig")
#
#     zip_buffer = io.BytesIO()
#     with pyzipper.AESZipFile(zip_buffer, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:
#         zf.setpassword(password.encode("utf-8"))
#         zf.writestr("programs_and_courses.csv", csv_data)
#     zip_buffer.seek(0)
#
#     if email_copy:
#         _email_export_password(user, detected_dept, password, "CSV (password-protected ZIP)")
#
#     response = HttpResponse(zip_buffer.read(), content_type="application/zip")
#     response["Content-Disposition"] = 'attachment; filename="programs_and_courses_protected.zip"'
#     return response


def _build_xlsx_bytes(programs):
    """Build the Excel workbook bytes (no password)."""
    ID_FILL = PatternFill("solid", fgColor="F3F3F3")  # grey = leave as-is / blank to create

    wb = openpyxl.Workbook()

    # --- Instructions sheet ---
    ws0 = wb.active
    ws0.title = "Instructions"
    for i, line in enumerate(INSTRUCTIONS, start=1):
        cell = ws0.cell(row=i, column=1, value=line)
        if i == 1:
            cell.font = Font(bold=True, size=13)
    ws0.column_dimensions["A"].width = 95

    # --- Programs sheet ---
    ws1 = wb.create_sheet("Programs")
    _style_header(ws1, PROGRAM_HEADERS, "2E7D32")
    for p in programs:
        ws1.append([p.id, p.name, p.department.name, p.description or "", p.default_cohort or "0"])
    for row_idx in range(2, ws1.max_row + 1):
        ws1.cell(row=row_idx, column=1).fill = ID_FILL
    _autosize(ws1, [12, 40, 28, 50, 18])

    # --- Courses sheet ---
    ws2 = wb.create_sheet("Courses")
    _style_header(ws2, COURSE_HEADERS, "1B5E90")
    for p in programs:
        for c in p.courses.all().order_by("year", "semester", "course_code"):
            ws2.append([
                c.id, p.name, c.course_code, c.course_name,
                c.year, c.semester, c.unit_type or "CORE", c.student_cohort or "0",
            ])
    for row_idx in range(2, ws2.max_row + 1):
        ws2.cell(row=row_idx, column=1).fill = ID_FILL
    _autosize(ws2, [12, 40, 18, 40, 8, 10, 20, 18])

    # --- Program Codes sheet ---
    ws3 = wb.create_sheet("Program Codes")
    _style_header(ws3, CODE_HEADERS, "6A1B9A")
    for p in programs:
        for code in p.program_codes.all().order_by("code"):
            ws3.append([code.id, p.name, code.code])
    for row_idx in range(2, ws3.max_row + 1):
        ws3.cell(row=row_idx, column=1).fill = ID_FILL
    _autosize(ws3, [12, 40, 25])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# Unprotected Excel export is no longer offered — every export now requires
# a password (see export_programs above). Left here, commented out, in case
# an unprotected export path is ever needed again.
#
# def _export_xlsx_response(programs):
#     xlsx_bytes = _build_xlsx_bytes(programs)
#     response = HttpResponse(
#         xlsx_bytes,
#         content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
#     )
#     response["Content-Disposition"] = 'attachment; filename="programs_and_courses.xlsx"'
#     return response


def _export_encrypted_xlsx(programs, password, user, detected_dept, email_copy):
    """Export Excel with Excel's own native 'Encrypt with Password' protection."""
    xlsx_bytes = _build_xlsx_bytes(programs)

    input_buffer = io.BytesIO(xlsx_bytes)
    output_buffer = io.BytesIO()
    crypto = msoffcrypto.OfficeFile(input_buffer)
    crypto.encrypt(password, output_buffer)
    encrypted_data = output_buffer.getvalue()

    if email_copy:
        _email_export_password(user, detected_dept, password, "Excel (password-protected)")

    response = HttpResponse(
        encrypted_data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="programs_and_courses_protected.xlsx"'
    return response


def _style_header(ws, headers, fill_color):
    ws.append(headers)
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True, color="FFFFFF", size=11)
        cell.fill = PatternFill("solid", fgColor=fill_color)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 20
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"


def _autosize(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _email_export_password(user, detected_dept, password, file_type):
    """
    Email the export password to the person who exported the file, and to
    their department's COD / admin (Department.leader), if any. Failures are
    logged but never break the export itself.
    """
    recipients = []
    if user.email:
        recipients.append(user.email)

    if detected_dept and detected_dept.leader and detected_dept.leader.email:
        if detected_dept.leader.email not in recipients:
            recipients.append(detected_dept.leader.email)

    if not recipients:
        logger.warning("Program export password requested but no recipient email was found for user %s", user)
        return

    subject = f"Password for your Programs {file_type} export"
    message = (
        f"Hello,\n\n"
        f"A {file_type} export of Programs, Courses, and Program Codes was just generated "
        f"by {user.get_full_name() or user.username}.\n\n"
        f"Password: {password}\n\n"
        f"Keep this password safe — anyone with the file AND this password can open and "
        f"edit it. Do not forward this email to anyone who shouldn't have access.\n\n"
        f"If you re-upload the edited file to re-import your changes, you'll be asked "
        f"for this same password.\n\n"
        f"— Chuka University Timetabling System"
    )

    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            recipients,
            fail_silently=True,
        )
    except Exception as e:
        logger.warning("Failed to send export password email: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# IMPORT
# ─────────────────────────────────────────────────────────────────────────────

def _clean_headers(raw):
    return [str(h).strip().lower() if h is not None else "" for h in raw]


def _rows_from_sheet(ws):
    headers = _clean_headers(next(ws.iter_rows(min_row=1, max_row=1, values_only=True)))
    rows = []
    for values in ws.iter_rows(min_row=2, values_only=True):
        if any(v is not None and str(v).strip() != "" for v in values):
            rows.append({h: v for h, v in zip(headers, values) if h})
    return rows


def _decrypt_xlsx_bytes(raw_bytes, password):
    """Decrypt a password-protected .xlsx file, returning a BytesIO of the plain workbook."""
    if not HAS_MSOFFCRYPTO:
        raise ValueError("msoffcrypto-tool is not installed on the server. Cannot read encrypted Excel files.")
    infile = io.BytesIO(raw_bytes)
    outfile = io.BytesIO()
    try:
        crypto = msoffcrypto.OfficeFile(infile)
        crypto.load_key(password=password)
        crypto.decrypt(outfile)
    except Exception:
        raise ValueError("Incorrect password, or the file is not a valid password-protected Excel file.")
    outfile.seek(0)
    return outfile


def _decrypt_zip_bytes(raw_bytes, password):
    """Decrypt a password-protected ZIP and return the bytes of its first file."""
    if not HAS_PYZIPPER:
        raise ValueError("pyzipper is not installed on the server. Cannot read encrypted ZIP files.")
    try:
        with pyzipper.AESZipFile(io.BytesIO(raw_bytes)) as zf:
            zf.setpassword(password.encode("utf-8"))
            names = zf.namelist()
            if not names:
                raise ValueError("No files found inside the ZIP archive.")
            return zf.read(names[0])
    except ValueError:
        raise
    except Exception:
        raise ValueError("Incorrect password, or the file is not a valid password-protected ZIP.")


def _is_zip_signature(raw_bytes):
    return raw_bytes[:4] == b"PK\x03\x04"


def _parse_xlsx(fileobj, password=None):
    """Returns (program_rows, course_rows, code_rows, programs_present, courses_present, codes_present)."""
    raw_bytes = fileobj.read()

    if password:
        source = _decrypt_xlsx_bytes(raw_bytes, password)
    else:
        if _is_zip_signature(raw_bytes):
            # A plain .xlsx is technically also a zip container, but msoffcrypto-encrypted
            # files are OLE/CFB, not a normal zip — a valid xlsx will still open fine here,
            # so just hand raw bytes straight to openpyxl.
            source = io.BytesIO(raw_bytes)
        else:
            source = io.BytesIO(raw_bytes)

    try:
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
    except Exception as e:
        # Most likely this file IS encrypted and no password was supplied.
        raise ValueError("need_password") if not password else ValueError(f"Could not read Excel file: {e}")

    sheet_by_lower = {name.lower(): name for name in wb.sheetnames}

    program_rows, course_rows, code_rows = [], [], []
    programs_present = "programs" in sheet_by_lower
    courses_present = "courses" in sheet_by_lower
    codes_present = "program codes" in sheet_by_lower or "program_codes" in sheet_by_lower

    if programs_present:
        program_rows = _rows_from_sheet(wb[sheet_by_lower["programs"]])
    if courses_present:
        course_rows = _rows_from_sheet(wb[sheet_by_lower["courses"]])
    if codes_present:
        sheet_name = sheet_by_lower.get("program codes") or sheet_by_lower.get("program_codes")
        code_rows = _rows_from_sheet(wb[sheet_name])

    if not programs_present and not courses_present and not codes_present:
        raise ValueError("Workbook must contain a 'Programs', 'Courses', and/or 'Program Codes' sheet.")

    return program_rows, course_rows, code_rows, programs_present, courses_present, codes_present


def _parse_csv_text(decoded):
    dict_reader = csv.DictReader(io.StringIO(decoded))
    fieldnames = dict_reader.fieldnames
    if not fieldnames:
        raise ValueError("The CSV file is empty.")

    reader = list(dict_reader)
    normalized = [{(k or "").strip().lower(): v for k, v in row.items()} for row in reader]
    headers = {(h or "").strip().lower() for h in fieldnames}

    program_rows, course_rows, code_rows = [], [], []
    programs_present = courses_present = codes_present = False

    if "row_type" in headers:
        programs_present = courses_present = codes_present = True
        for row in normalized:
            rt = (row.get("row_type") or "").strip().upper()
            if rt == "PROGRAM":
                program_rows.append({
                    "program_id": row.get("id"),
                    "program_name": row.get("program_name"),
                    "department": row.get("department"),
                    "description": row.get("description"),
                    "default_cohort": row.get("default_cohort") or "0",
                })
            elif rt == "COURSE":
                course_rows.append({
                    "course_id": row.get("id"),
                    "program_name": row.get("program_name"),
                    "course_code": row.get("course_code"),
                    "course_name": row.get("course_name"),
                    "year": row.get("year"),
                    "semester": row.get("semester"),
                    "unit_type": row.get("unit_type") or "CORE",
                    "student_cohort": row.get("student_cohort") or "0",
                })
            elif rt == "CODE":
                code_rows.append({
                    "code_id": row.get("id"),
                    "program_name": row.get("program_name"),
                    "code": row.get("code"),
                })
    else:
        # Try to detect by columns
        if "course_code" in headers:
            courses_present = True
            for row in normalized:
                course_rows.append({
                    "course_id": row.get("course_id") or row.get("id"),
                    "program_name": row.get("program_name") or row.get("program"),
                    "course_code": row.get("course_code"),
                    "course_name": row.get("course_name"),
                    "year": row.get("year"),
                    "semester": row.get("semester"),
                    "unit_type": row.get("unit_type") or "CORE",
                    "student_cohort": row.get("student_cohort") or "0",
                })
        elif "code" in headers:
            codes_present = True
            for row in normalized:
                code_rows.append({
                    "code_id": row.get("code_id") or row.get("id"),
                    "program_name": row.get("program_name") or row.get("program"),
                    "code": row.get("code"),
                })
        elif "department" in headers or "program_name" in headers:
            programs_present = True
            for row in normalized:
                program_rows.append({
                    "program_id": row.get("program_id") or row.get("id"),
                    "program_name": row.get("program_name") or row.get("name"),
                    "department": row.get("department"),
                    "description": row.get("description"),
                    "default_cohort": row.get("default_cohort") or "0",
                })
        else:
            raise ValueError("Could not recognize the CSV columns. Please re-download the template.")

    return program_rows, course_rows, code_rows, programs_present, courses_present, codes_present


def _parse_csv(fileobj, password=None):
    """Parse a CSV, or a password-protected ZIP containing one."""
    raw_bytes = fileobj.read()

    if _is_zip_signature(raw_bytes):
        if not password:
            raise ValueError("need_password")
        csv_bytes = _decrypt_zip_bytes(raw_bytes, password)
        decoded = csv_bytes.decode("utf-8-sig")
    else:
        decoded = raw_bytes.decode("utf-8-sig")

    return _parse_csv_text(decoded)


def _to_int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class _ProgRef:
    """Lightweight stand-in for a Program that doesn't exist in the DB yet"""
    def __init__(self, id, name):
        self.id = id
        self.name = name


def _diff_fields(old, new):
    changes = {}
    for key, new_val in new.items():
        old_val = old.get(key)
        if str(old_val if old_val is not None else "") != str(new_val if new_val is not None else ""):
            changes[key] = [old_val, new_val]
    return changes


def _run_import_sync(program_rows, course_rows, code_rows, programs_present, courses_present, codes_present,
                      detected_dept, scope_dept_ids, dry_run):
    """
    Single engine used for BOTH the preview and the real apply.
    """
    errors = []
    diff = {
        "programs": {"create": [], "update": [], "delete": []},
        "courses": {"create": [], "update": [], "delete": []},
        "codes": {"create": [], "update": [], "delete": []},
    }
    counts = {
        "programs_created": 0, "programs_updated": 0, "programs_deleted": 0,
        "courses_created": 0, "courses_updated": 0, "courses_deleted": 0,
        "codes_created": 0, "codes_updated": 0, "codes_deleted": 0,
    }

    # ── Programs sync ──
    if scope_dept_ids:
        existing_programs = {p.id: p for p in Program.objects.filter(department__in=scope_dept_ids)}
    else:
        existing_programs = {p.id: p for p in Program.objects.all()}

    seen_program_ids = set()
    name_to_program = {p.name.lower(): p for p in existing_programs.values()}

    if programs_present:
        for i, row in enumerate(program_rows, start=1):
            try:
                with transaction.atomic():
                    pid = _to_int(row.get("program_id"))
                    name = str(row.get("program_name") or "").strip()
                    dept_val = str(row.get("department") or "").strip()
                    description = str(row.get("description") or "").strip()
                    default_cohort = str(row.get("default_cohort") or "0").strip()

                    if not name:
                        raise ValueError("program_name is required")

                    if dept_val:
                        dept_obj = Department.objects.filter(name__iexact=dept_val).first()
                        if not dept_obj:
                            raise ValueError(f"department '{dept_val}' not found")
                    elif detected_dept:
                        dept_obj = detected_dept
                    else:
                        raise ValueError("department is required")

                    if scope_dept_ids and dept_obj.id not in scope_dept_ids:
                        raise ValueError(f"you can only manage programs in {detected_dept.name}")

                    if pid:
                        prog = existing_programs.get(pid)
                        if not prog:
                            raise ValueError(f"program id {pid} not found in your scope")
                        old_vals = {"name": prog.name, "department": prog.department.name,
                                    "description": prog.description or "", "default_cohort": prog.default_cohort or "0"}
                        new_vals = {"name": name, "department": dept_obj.name, "description": description,
                                    "default_cohort": default_cohort}
                        changes = _diff_fields(old_vals, new_vals)
                        if changes:
                            diff["programs"]["update"].append({
                                "id": prog.id, "name": name, "department": dept_obj.name, "changes": changes,
                            })
                        if not dry_run:
                            prog.name, prog.description, prog.department = name, description, dept_obj
                            prog.default_cohort = default_cohort
                            prog.save()
                        if changes:
                            counts["programs_updated"] += 1
                    else:
                        match = Program.objects.filter(name__iexact=name).first()
                        if match:
                            if scope_dept_ids and match.department_id not in scope_dept_ids:
                                raise ValueError(f"program '{name}' already exists in another department")
                            old_vals = {"name": match.name, "department": match.department.name,
                                        "description": match.description or "", "default_cohort": match.default_cohort or "0"}
                            new_vals = {"name": name, "department": dept_obj.name, "description": description,
                                        "default_cohort": default_cohort}
                            changes = _diff_fields(old_vals, new_vals)
                            if changes:
                                diff["programs"]["update"].append({
                                    "id": match.id, "name": name, "department": dept_obj.name, "changes": changes,
                                })
                                counts["programs_updated"] += 1
                            if not dry_run:
                                match.description, match.department = description, dept_obj
                                match.default_cohort = default_cohort
                                match.save()
                            prog = match
                        else:
                            diff["programs"]["create"].append({
                                "name": name, "department": dept_obj.name, "description": description,
                                "default_cohort": default_cohort,
                            })
                            counts["programs_created"] += 1
                            if dry_run:
                                prog = _ProgRef(id=None, name=name)
                            else:
                                prog = Program.objects.create(
                                    name=name, department=dept_obj, description=description,
                                    default_cohort=default_cohort
                                )

                    seen_program_ids.add(prog.id)
                    name_to_program[name.lower()] = prog
            except Exception as e:
                errors.append(f"Programs row {i}: {e}")

        for pid, prog in existing_programs.items():
            if pid not in seen_program_ids:
                diff["programs"]["delete"].append({
                    "id": prog.id, "name": prog.name, "department": prog.department.name,
                    "course_count": prog.courses.count(),
                })
                counts["programs_deleted"] += 1
                if not dry_run:
                    try:
                        with transaction.atomic():
                            prog.delete()
                    except ProtectedError as e:
                        counts["programs_deleted"] -= 1
                        blockers = sorted({o._meta.verbose_name for o in e.protected_objects[:5]})
                        errors.append(
                            f"Program '{prog.name}' was not deleted: still referenced by "
                            f"existing {', '.join(blockers)} record(s). Remove or reassign "
                            f"those first, or keep this program in the sheet."
                        )
                    except Exception as e:
                        counts["programs_deleted"] -= 1
                        errors.append(f"Program '{prog.name}' was not deleted: {e}")

    # ── Courses sync ──
    if courses_present:
        if scope_dept_ids:
            current_scope_qs = Program.objects.filter(department__in=scope_dept_ids)
        else:
            current_scope_qs = Program.objects.all()

        for p in current_scope_qs:
            name_to_program.setdefault(p.name.lower(), p)

        existing_courses = {c.id: c for c in ProgramCourse.objects.filter(program__in=current_scope_qs)}
        # Canonical (whitespace/case/separator-insensitive) key, so
        # re-importing against legacy dirty rows ('BCOM112') updates the
        # existing row instead of creating a fresh 'BCOM 112' duplicate.
        # course_code itself is still stored via normalize_code() below --
        # this key is for MATCHING only.
        key_to_course = {
            (c.program_id, canonical_course_key(c.course_code), c.student_cohort or "0"): c
            for c in existing_courses.values()
        }
        seen_course_ids = set()

        for i, row in enumerate(course_rows, start=1):
            try:
                with transaction.atomic():
                    cid = _to_int(row.get("course_id"))
                    prog_name = str(row.get("program_name") or "").strip()
                    course_code = normalize_code(str(row.get("course_code") or "").strip())
                    course_name = str(row.get("course_name") or "").strip()
                    year = _to_int(row.get("year"), default=1)
                    semester = _to_int(row.get("semester"), default=1)
                    unit_type = str(row.get("unit_type") or "CORE").strip().upper()
                    student_cohort = str(row.get("student_cohort") or "0").strip()

                    if not course_code or not course_name:
                        raise ValueError("course_code and course_name are required")
                    if not prog_name:
                        raise ValueError("program_name is required")

                    valid_types = ['CORE', 'ELECTIVE', 'UNIVERSITY_WIDE', 'REQUIRED_ELECTIVE']
                    if unit_type not in valid_types:
                        unit_type = 'CORE'

                    prog = name_to_program.get(prog_name.lower())
                    if not prog:
                        raise ValueError(f"program '{prog_name}' not found in your scope")

                    if cid:
                        course = existing_courses.get(cid)
                        if not course:
                            raise ValueError(f"course id {cid} not found in your scope")
                        old_vals = {
                            "program": course.program.name, "course_code": course.course_code,
                            "course_name": course.course_name, "year": course.year,
                            "semester": course.semester, "unit_type": course.unit_type or "CORE",
                            "student_cohort": course.student_cohort or "0",
                        }
                        new_vals = {
                            "program": prog.name, "course_code": course_code,
                            "course_name": course_name, "year": year, "semester": semester,
                            "unit_type": unit_type, "student_cohort": student_cohort,
                        }
                        changes = _diff_fields(old_vals, new_vals)
                        if changes:
                            diff["courses"]["update"].append({
                                "id": course.id, "program": prog.name, "course_code": course_code,
                                "course_name": course_name, "changes": changes,
                            })
                            counts["courses_updated"] += 1
                        if not dry_run:
                            course.program_id = prog.id
                            course.course_code, course.course_name = course_code, course_name
                            course.year, course.semester = year, semester
                            course.unit_type = unit_type
                            course.student_cohort = student_cohort
                            course.save()
                        seen_course_ids.add(course.id)
                    else:
                        key = (prog.id, canonical_course_key(course_code), student_cohort)
                        match = key_to_course.get(key) if prog.id else None
                        if match:
                            old_vals = {
                                "course_name": match.course_name, "year": match.year,
                                "semester": match.semester, "unit_type": match.unit_type or "CORE",
                            }
                            new_vals = {
                                "course_name": course_name, "year": year,
                                "semester": semester, "unit_type": unit_type,
                            }
                            changes = _diff_fields(old_vals, new_vals)
                            if changes:
                                diff["courses"]["update"].append({
                                    "id": match.id, "program": prog.name, "course_code": course_code,
                                    "course_name": course_name, "changes": changes,
                                })
                                counts["courses_updated"] += 1
                            if not dry_run:
                                match.course_name = course_name
                                match.year, match.semester = year, semester
                                match.unit_type = unit_type
                                match.save()
                            seen_course_ids.add(match.id)
                        else:
                            diff["courses"]["create"].append({
                                "program": prog.name, "course_code": course_code,
                                "course_name": course_name, "year": year, "semester": semester,
                                "unit_type": unit_type, "student_cohort": student_cohort,
                            })
                            counts["courses_created"] += 1
                            if not dry_run:
                                course = ProgramCourse.objects.create(
                                    program_id=prog.id, course_code=course_code, course_name=course_name,
                                    year=year, semester=semester, unit_type=unit_type,
                                    student_cohort=student_cohort,
                                )
                                seen_course_ids.add(course.id)
            except Exception as e:
                errors.append(f"Courses row {i}: {e}")

        for cid, course in existing_courses.items():
            if cid not in seen_course_ids:
                diff["courses"]["delete"].append({
                    "id": course.id, "program": course.program.name, "course_code": course.course_code,
                    "course_name": course.course_name,
                })
                counts["courses_deleted"] += 1
                if not dry_run:
                    try:
                        with transaction.atomic():
                            course.delete()
                    except ProtectedError as e:
                        # e.g. a curriculum row that still has resit allocations
                        # pointing at it (ProgramCourse -> ResitAllocation is a
                        # PROTECT FK on purpose, so year/semester always stay
                        # known for existing resits). Don't let one row like
                        # this crash the whole import -- report it and leave
                        # the curriculum entry in place.
                        counts["courses_deleted"] -= 1
                        blockers = sorted({o._meta.verbose_name for o in e.protected_objects[:5]})
                        errors.append(
                            f"Course '{course.course_code}' ({course.program.name}) was not "
                            f"deleted: still referenced by existing {', '.join(blockers)} "
                            f"record(s). Remove or reassign those first, or keep this course "
                            f"in the sheet."
                        )
                    except Exception as e:
                        counts["courses_deleted"] -= 1
                        errors.append(
                            f"Course '{course.course_code}' ({course.program.name}) was not deleted: {e}"
                        )

    # ── Program Codes sync ──
    if codes_present:
        if scope_dept_ids:
            current_scope_qs = Program.objects.filter(department__in=scope_dept_ids)
        else:
            current_scope_qs = Program.objects.all()

        for p in current_scope_qs:
            name_to_program.setdefault(p.name.lower(), p)

        existing_codes = {c.id: c for c in ProgramCode.objects.filter(program__in=current_scope_qs)}
        key_to_code = {(c.program_id, c.code): c for c in existing_codes.values()}
        seen_code_ids = set()

        for i, row in enumerate(code_rows, start=1):
            try:
                with transaction.atomic():
                    cid = _to_int(row.get("code_id"))
                    prog_name = str(row.get("program_name") or "").strip()
                    code = str(row.get("code") or "").strip().upper()

                    if not code:
                        raise ValueError("code is required")
                    if not prog_name:
                        raise ValueError("program_name is required")

                    prog = name_to_program.get(prog_name.lower())
                    if not prog:
                        raise ValueError(f"program '{prog_name}' not found in your scope")

                    # Global uniqueness check (ProgramCode.code is unique across the whole table)
                    conflict = ProgramCode.objects.filter(code=code).exclude(id=cid).first()
                    if conflict and conflict.program_id != prog.id:
                        raise ValueError(f"code '{code}' is already used by program '{conflict.program.name}'")

                    if cid:
                        code_obj = existing_codes.get(cid)
                        if not code_obj:
                            raise ValueError(f"code id {cid} not found in your scope")
                        old_vals = {"program": code_obj.program.name, "code": code_obj.code}
                        new_vals = {"program": prog.name, "code": code}
                        changes = _diff_fields(old_vals, new_vals)
                        if changes:
                            diff["codes"]["update"].append({
                                "id": code_obj.id, "program": prog.name, "code": code, "changes": changes,
                            })
                            counts["codes_updated"] += 1
                        if not dry_run:
                            code_obj.program_id = prog.id
                            code_obj.code = code
                            code_obj.save()
                        seen_code_ids.add(code_obj.id)
                    else:
                        key = (prog.id, code)
                        match = key_to_code.get(key) if prog.id else None
                        if match:
                            seen_code_ids.add(match.id)
                        else:
                            diff["codes"]["create"].append({"program": prog.name, "code": code})
                            counts["codes_created"] += 1
                            if not dry_run:
                                code_obj = ProgramCode.objects.create(program_id=prog.id, code=code)
                                seen_code_ids.add(code_obj.id)
            except Exception as e:
                errors.append(f"Codes row {i}: {e}")

        for cid, code_obj in existing_codes.items():
            if cid not in seen_code_ids:
                diff["codes"]["delete"].append({
                    "id": code_obj.id, "program": code_obj.program.name, "code": code_obj.code,
                })
                counts["codes_deleted"] += 1
                if not dry_run:
                    try:
                        with transaction.atomic():
                            code_obj.delete()
                    except ProtectedError as e:
                        counts["codes_deleted"] -= 1
                        blockers = sorted({o._meta.verbose_name for o in e.protected_objects[:5]})
                        errors.append(
                            f"Code '{code_obj.code}' ({code_obj.program.name}) was not deleted: "
                            f"still referenced by existing {', '.join(blockers)} record(s)."
                        )
                    except Exception as e:
                        counts["codes_deleted"] -= 1
                        errors.append(f"Code '{code_obj.code}' ({code_obj.program.name}) was not deleted: {e}")

    return counts, diff, errors


def _summary_message(programs_present, courses_present, codes_present, counts, errors, applied):
    bits = []
    if programs_present:
        bits.append(
            f"Programs: +{counts['programs_created']} / ~{counts['programs_updated']} / -{counts['programs_deleted']}"
        )
    if courses_present:
        bits.append(
            f"Courses: +{counts['courses_created']} / ~{counts['courses_updated']} / -{counts['courses_deleted']}"
        )
    if codes_present:
        bits.append(
            f"Codes: +{counts['codes_created']} / ~{counts['codes_updated']} / -{counts['codes_deleted']}"
        )
    verb = "Import complete." if applied else "Ready to review."
    message = f"{verb} " + "  ".join(bits) if bits else "Nothing to import — the file didn't contain any changes."
    if errors:
        message += f"  ({len(errors)} row(s) will be skipped — see details.)"
    return message


def _has_pending_changes(counts):
    return any(counts.values())


@login_required
@require_POST
def import_programs(request):
    """
    STEP 1 — Preview. Parses the uploaded file and computes exactly what
    would change WITHOUT writing anything to the database. Supports plain
    CSV/XLSX, a password-protected XLSX, or a password-protected ZIP
    containing a CSV.
    """
    uploaded = request.FILES.get("file")
    if not uploaded:
        return JsonResponse({"status": "error", "message": "No file uploaded."}, status=400)

    if uploaded.size > MAX_UPLOAD_BYTES:
        return JsonResponse({"status": "error", "message": "File exceeds 5 MB limit."}, status=400)

    ext = uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""
    password = (request.POST.get("password") or "").strip() or None

    try:
        if ext == "csv" or ext == "zip":
            program_rows, course_rows, code_rows, programs_present, courses_present, codes_present = _parse_csv(uploaded, password)
        elif ext in ("xlsx", "xls"):
            if not HAS_OPENPYXL:
                return JsonResponse(
                    {"status": "error", "message": "openpyxl not installed on server. Please upload a CSV file."},
                    status=400,
                )
            program_rows, course_rows, code_rows, programs_present, courses_present, codes_present = _parse_xlsx(uploaded, password)
        else:
            return JsonResponse(
                {"status": "error", "message": f"Unsupported file type: .{ext}. Use CSV, XLSX, or a password-protected ZIP."},
                status=400,
            )
    except ValueError as e:
        if str(e) == "need_password" or "password" in str(e).lower() or "encrypt" in str(e).lower():
            return JsonResponse({
                "status": "need_password",
                "message": "This file is password-protected. Please enter the password to continue.",
            }, status=200)
        return JsonResponse({"status": "error", "message": f"Could not read file: {e}"}, status=400)
    except Exception as e:
        return JsonResponse({"status": "error", "message": f"Could not read file: {e}"}, status=400)

    detected_dept, _ = _scope_programs(request.user)
    scope_dept_ids = {detected_dept.id} if detected_dept else None

    counts, diff, errors = _run_import_sync(
        program_rows, course_rows, code_rows, programs_present, courses_present, codes_present,
        detected_dept, scope_dept_ids, dry_run=True,
    )

    if not _has_pending_changes(counts) and not errors:
        return JsonResponse({
            "status": "success",
            "message": "Nothing to import — the file matches what's already in the system.",
            "summary": counts, "diff": diff, "errors": [], "token": None,
        })

    token = uuid.uuid4().hex
    cache.set(_pending_key(token), {
        "user_id": request.user.id,
        "dept_id": detected_dept.id if detected_dept else None,
        "program_rows": program_rows,
        "course_rows": course_rows,
        "code_rows": code_rows,
        "programs_present": programs_present,
        "courses_present": courses_present,
        "codes_present": codes_present,
        "filename": uploaded.name,
    }, timeout=PENDING_IMPORT_TTL)

    return JsonResponse({
        "status": "preview",
        "message": _summary_message(programs_present, courses_present, codes_present, counts, errors, applied=False),
        "summary": counts,
        "diff": diff,
        "errors": errors[:50],
        "token": token,
        "expires_in": PENDING_IMPORT_TTL,
    })


@login_required
@require_POST
def import_programs_apply(request):
    """
    STEP 2 — Apply. Takes a token returned by import_programs (preview)
    and actually performs the sync.
    """
    token = (request.POST.get("token") or "").strip()
    if not token:
        return JsonResponse({"status": "error", "message": "Missing import token."}, status=400)

    pending = cache.get(_pending_key(token))
    if not pending:
        return JsonResponse(
            {"status": "error", "message": "This import preview has expired. Please re-upload the file."},
            status=400,
        )

    if pending["user_id"] != request.user.id:
        return JsonResponse({"status": "error", "message": "This import belongs to a different user."}, status=403)

    detected_dept, _ = _scope_programs(request.user)
    scope_dept_ids = {detected_dept.id} if detected_dept else None
    cached_dept_id = pending["dept_id"]
    if cached_dept_id != (detected_dept.id if detected_dept else None):
        return JsonResponse(
            {"status": "error", "message": "Your department scope changed since this preview was generated. Please re-upload the file."},
            status=400,
        )

    counts, diff, errors = _run_import_sync(
        pending.get("program_rows", []), pending.get("course_rows", []), pending.get("code_rows", []),
        pending.get("programs_present", False), pending.get("courses_present", False), pending.get("codes_present", False),
        detected_dept, scope_dept_ids, dry_run=False,
    )
    cache.delete(_pending_key(token))

    batch_id = get_audit_context().get("request_id") or ""

    return JsonResponse({
        "status": "success" if not errors else "partial",
        "message": _summary_message(
            pending.get("programs_present", False), pending.get("courses_present", False),
            pending.get("codes_present", False), counts, errors, applied=True,
        ),
        "summary": counts,
        "diff": diff,
        "errors": errors[:50],
        "batch_id": batch_id,
        "rollback_hint": (
            "A backup of everything this import changed was kept automatically. "
            "A sudo administrator can undo this whole import — or just part of it — "
            "from the Course Master Rollback page." if _has_pending_changes(counts) else ""
        ),
    })
