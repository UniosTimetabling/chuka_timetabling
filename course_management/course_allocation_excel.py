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
import logging

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone

from core.rbac import allowed_roles, Role
from department_management.models import Department
from lecturer_portal.models import Lecturer
from program_management.models import Program, ProgramCourse
from course_allocation.models import (
    CourseAllocation,
    StudentGroup,
    SelectionGroup,
    SpecializationStem,
    SpecializationCategory,
)
from course_management.cod_panel import detect_user_department  # reuse existing helper

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
    "Course Code",
    "Course Name",
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
]

ALL_COLUMNS = VISIBLE_COLUMNS + HIDDEN_COLUMNS
N_COLS = len(ALL_COLUMNS)

SECTION_FILL = "1F4E78"      # dark blue — program/year band
HEADER_FILL = "D9E1F2"       # light blue — column header row
DATA_ALT_FILL = "F2F2F2"     # light grey — alternate data rows


# ===========================================================================
# LANDING PAGE (the form the COD actually sees)
# ===========================================================================

@login_required
@allowed_roles(*ALLOCATION_TEMPLATE_ROLES)
def course_allocation_template_page(request):
    """
    GET /course-management/cod/course-allocation-template/

    The page linked from the COD panel's side nav. Shows the "export"
    form (pick semester, tick include-missing) and the "re-import" form,
    in the same visual language as Safe Undo / the rest of the COD panel.
    """
    dept = detect_user_department(request.user)
    is_dept_free_admin = request.user.is_superuser and dept is None
    departments = Department.objects.order_by("name") if is_dept_free_admin else None
    return render(request, "course_management/course_allocation_template.html", {
        "department": dept,
        "departments": departments,
    })


# ===========================================================================
# EXPORT
# ===========================================================================

@login_required
@allowed_roles(*ALLOCATION_TEMPLATE_ROLES)
def export_course_allocation_template(request):
    """
    GET /course-management/cod/course-allocation-template/export/?semester=1&include_missing=1

    Exports the official Course Allocation Template for the COD's own
    department (or, for SUDO/DIRECTOR/TIMETABLE_ADMIN, an optional
    ?department_id=... override).
    """
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    semester = request.GET.get("semester")
    if semester not in ("1", "2"):
        return redirect(reverse("course_allocation_template_page") + "?error=semester")
    semester = int(semester)
    include_missing = request.GET.get("include_missing") in ("1", "true", "on", "yes")

    dept = detect_user_department(request.user)
    if request.user.is_superuser or (dept is None):
        override_id = request.GET.get("department_id")
        if override_id:
            dept = Department.objects.filter(pk=override_id).first()
    if dept is None:
        return render(request, "course_management/course_allocation_template_error.html", {
            "error": "No department could be determined for your account.",
        })

    # ---- 1. Existing allocations for this department + semester -----------
    # "Belongs to" this COD's export if their department either allocates it
    # or originated it (covers common/university-wide units the department
    # teaches out to other programs, e.g. Education units).
    allocations = (
        CourseAllocation.objects
        .filter(Q(department=dept) | Q(origin_department=dept))
        .filter(program_course__semester=semester)
        .select_related(
            "program_course", "program_course__program", "department",
            "origin_department", "lecturer", "student_group",
            "selection_group", "specialization_stem",
        )
        .order_by("program_course__program__name", "program_course__year", "course_code")
    )

    # Group rows by (program_id, year)
    sections = {}  # (program_id, year) -> {"program": Program, "year": y, "rows": [...]}

    def _section_for(program, year):
        key = (program.id, year)
        if key not in sections:
            sections[key] = {"program": program, "year": year, "rows": []}
        return sections[key]

    seen_program_course_ids = set()

    for alloc in allocations:
        pc = alloc.program_course
        seen_program_course_ids.add(pc.id)
        sec = _section_for(pc.program, pc.year)
        sec["rows"].append(_row_from_allocation(alloc))

    # ---- 2. Optionally top up with curriculum courses that are NOT yet
    #         allocated at all (own department's programs only — that is
    #         the curriculum this COD actually owns). ----------------------
    if include_missing:
        own_program_courses = (
            ProgramCourse.objects
            .filter(program__department=dept, semester=semester)
            .select_related("program")
        )
        for pc in own_program_courses:
            if pc.id in seen_program_course_ids:
                continue
            sec = _section_for(pc.program, pc.year)
            sec["rows"].append(_row_for_missing_course(pc, dept))

    # ---- 3. Build the workbook ---------------------------------------------
    wb = Workbook()

    _build_instructions_sheet(wb, dept, semester)
    ws = wb.active if wb.active.title == "Sheet" else wb.create_sheet("Course Allocation", 0)
    ws.title = "Course Allocation"
    wb.move_sheet("Course Allocation", offset=-len(wb.sheetnames))  # put it first

    # Try to stamp the university logo + name at the very top, if configured.
    _stamp_logo(ws, wb)

    header_font = Font(bold=True, color="FFFFFF")
    section_font = Font(bold=True, size=12, color="FFFFFF")
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    current_row = 4  # leave room for the logo/title band

    if not sections:
        ws.cell(row=current_row, column=1,
                 value=f"No courses found for Semester {semester}. "
                       f"Try ticking 'include courses not yet allocated'.")
    else:
        ordered_keys = sorted(sections.keys(), key=lambda k: (sections[k]["program"].name, sections[k]["year"]))
        for key in ordered_keys:
            sec = sections[key]
            program, year = sec["program"], sec["year"]

            # -- merged section header band --------------------------------
            ws.merge_cells(start_row=current_row, start_column=1,
                             end_row=current_row, end_column=N_COLS)
            band = ws.cell(row=current_row, column=1,
                            value=f"{program.name}  —  Year {year}  (Semester {semester})")
            band.font = section_font
            band.fill = PatternFill("solid", fgColor=SECTION_FILL)
            band.alignment = Alignment(horizontal="left", vertical="center")
            ws.row_dimensions[current_row].height = 22
            current_row += 1

            # -- column header row -------------------------------------------
            for col_idx, name in enumerate(ALL_COLUMNS, start=1):
                c = ws.cell(row=current_row, column=col_idx, value=name)
                c.font = header_font if col_idx <= len(VISIBLE_COLUMNS) else Font(bold=True)
                c.fill = PatternFill("solid", fgColor=HEADER_FILL if col_idx <= len(VISIBLE_COLUMNS) else "E7E6E6")
                c.border = border
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            current_row += 1

            # -- data rows -----------------------------------------------------
            for i, row_values in enumerate(sec["rows"]):
                fill = PatternFill("solid", fgColor=DATA_ALT_FILL) if i % 2 else None
                for col_idx, value in enumerate(row_values, start=1):
                    c = ws.cell(row=current_row, column=col_idx, value=value)
                    c.border = border
                    if fill:
                        c.fill = fill
                current_row += 1

            current_row += 1  # blank spacer row between program-year sections

    # -- column widths / hide identity columns -------------------------------
    visible_widths = [16, 34, 20, 20, 28, 10, 16, 12, 22, 22, 10]
    for idx, width in enumerate(visible_widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    for idx in range(len(VISIBLE_COLUMNS) + 1, N_COLS + 1):
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = 4
        ws.column_dimensions[letter].hidden = True

    ws.freeze_panes = "A5"

    filename = f"course_allocation_template_{dept.name.replace(' ', '_')}_sem{semester}_{timezone.now():%Y%m%d}.xlsx"
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


def _row_from_allocation(alloc):
    pc = alloc.program_course
    return [
        alloc.course_code,
        alloc.course_name,
        alloc.origin_department.name if alloc.origin_department else "",
        alloc.department.name if alloc.department else "",
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
    ]


def _row_for_missing_course(pc, dept):
    return [
        pc.course_code,
        pc.course_name,
        dept.name,       # default guess: origin = the owning department
        dept.name,       # default guess: allocating = the owning department
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
    ]


def _stamp_logo(ws, wb):
    """Best-effort: put the configured university logo + name at the top of
    the sheet. Silently skipped if no logo is configured — this is a nice-to
    -have, never a reason to fail the export."""
    try:
        from openpyxl.drawing.image import Image as XLImage
        from export_import.models import TimetablePdfTemplate
        tpl = TimetablePdfTemplate.objects.first()
        if tpl and tpl.university_logo and hasattr(tpl.university_logo, "path"):
            img = XLImage(tpl.university_logo.path)
            img.height = 60
            img.width = 60
            ws.add_image(img, "A1")
        if tpl:
            ws.cell(row=1, column=3, value=tpl.university_name).font = None
            ws.cell(row=2, column=3, value="Official Course Allocation Template")
    except Exception:
        logger.exception("Could not stamp logo on course allocation template — continuing without it.")


def _build_instructions_sheet(wb, dept, semester):
    ws = wb.active
    ws.title = "How To Use"
    from openpyxl.styles import Font, Alignment

    lines = [
        ("Course Allocation Template — How To Use", True, 14),
        ("", False, 11),
        (f"Department: {dept.name}   |   Semester: {semester}   |   Generated: {timezone.now():%d %b %Y %H:%M}", False, 11),
        ("", False, 11),
        ("1. This workbook lists every course allocation already on the system for this "
         "semester, grouped by Programme and Year (each group has its own blue header band).", False, 11),
        ("2. If you ticked 'include courses not yet allocated', courses from your curriculum "
         "that have no allocation yet are included too, with Number of Students = 0 and "
         "Lecturer Email blank, so nothing is left out.", False, 11),
        ("", False, 11),
        ("IMPORTANT — Lecturer Email, not Lecturer Name", True, 12),
        ("Always identify the lecturer by their EMAIL address, never by typing their name. "
         "Two lecturers can share the same or a similar name, and the system matches "
         "purely on email — using a name here can create a duplicate lecturer record.", False, 11),
        ("", False, 11),
        ("Column reference:", True, 12),
        (" - Course Code / Course Name: as defined in the programme curriculum.", False, 11),
        (" - Origin Department: the department that owns/created the course.", False, 11),
        (" - Allocating Department: the department actually teaching/allocating it this semester.", False, 11),
        (" - Lecturer Email: the assigned lecturer's email. Leave blank if unassigned.", False, 11),
        (" - Number of Students: expected enrolment for this row.", False, 11),
        (" - Student Group: e.g. 'Group A'. Leave blank if the course is shared by the "
         "whole programme year (not split into groups).", False, 11),
        (" - Unit Type: 'Core' or 'Elective'.", False, 11),
        (" - Elective Group: only for Elective units — the pool of courses students choose "
         "ONE from (e.g. 'Year 3 Sem 1 Electives - Group A').", False, 11),
        (" - Combination Stem: only if the course belongs to a specialization/combination "
         "stem (e.g. 'Artificial Intelligence' stem). Leave blank otherwise.", False, 11),
        (" - Intake: 'Normal' or 'Special'.", False, 11),
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
    for i in range(1, len(lines) + 1):
        ws.row_dimensions[i].height = 18


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

    dept = detect_user_department(request.user)
    upload = request.FILES["file"]

    try:
        wb = load_workbook(upload, data_only=True)
    except Exception as exc:
        return render(request, "course_management/course_allocation_template_error.html", {
            "error": f"Could not read that file as an Excel workbook: {exc}",
        })

    if "Course Allocation" not in wb.sheetnames:
        return render(request, "course_management/course_allocation_template_error.html", {
            "error": "This doesn't look like a Course Allocation Template "
                     "(missing the 'Course Allocation' sheet).",
        })
    ws = wb["Course Allocation"]

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

    missing_cols = [c for c in ALL_COLUMNS if c not in header_map]
    if missing_cols:
        return render(request, "course_management/course_allocation_template_error.html", {
            "error": "This file is missing expected columns (it may have been altered): "
                     + ", ".join(missing_cols),
        })

    def val(row_cells, name):
        idx = header_map[name]
        return row_cells[idx].value if idx < len(row_cells) else None

    created, updated, skipped = 0, 0, 0
    warnings = []

    with transaction.atomic():
        for row in ws.iter_rows(min_row=header_row_idx + 1, max_row=ws.max_row):
            if not row:
                continue
            row_cells = list(row)
            if val(row_cells, "_row_kind") != "DATA":
                continue  # section header / blank spacer row

            excel_row_no = row_cells[0].row
            course_code = (val(row_cells, "Course Code") or "").strip()
            if not course_code:
                continue

            # ---- resolve ProgramCourse (identity anchor for the row) -----
            pc_id = val(row_cells, "_program_course_id")
            pc = ProgramCourse.objects.filter(pk=pc_id).first() if pc_id else None
            if pc is None:
                program_id = val(row_cells, "_program_id")
                year = val(row_cells, "_year")
                semester = val(row_cells, "_semester")
                pc = ProgramCourse.objects.filter(
                    program_id=program_id, year=year, semester=semester, course_code=course_code
                ).first()
            if pc is None:
                warnings.append(f"Row {excel_row_no}: could not match '{course_code}' to a "
                                 f"curriculum course — row skipped.")
                skipped += 1
                continue

            # ---- resolve allocating + origin department ------------------
            dept_name = (val(row_cells, "Allocating Department") or "").strip()
            allocating_dept = Department.objects.filter(name__iexact=dept_name).first() if dept_name else None
            if allocating_dept is None:
                allocating_dept = dept  # fall back to the COD's own department
                if dept_name:
                    warnings.append(f"Row {excel_row_no}: Allocating Department "
                                     f"'{dept_name}' not found — used {dept.name} instead.")

            origin_name = (val(row_cells, "Origin Department") or "").strip()
            origin_dept = Department.objects.filter(name__iexact=origin_name).first() if origin_name else None
            if origin_name and origin_dept is None:
                warnings.append(f"Row {excel_row_no}: Origin Department '{origin_name}' not found — left blank.")

            # ---- resolve lecturer by EMAIL ONLY ----------------------------
            lecturer = None
            email = (val(row_cells, "Lecturer Email") or "").strip()
            if email:
                lecturer = Lecturer.objects.filter(email__iexact=email).first()
                if lecturer is None:
                    warnings.append(f"Row {excel_row_no}: no lecturer found with email "
                                     f"'{email}' — allocation saved without a lecturer. "
                                     f"Add the lecturer to the system first, then re-import.")

            # ---- resolve student group -------------------------------------
            student_group = None
            sg_name = (val(row_cells, "Student Group") or "").strip()
            sg_id = val(row_cells, "_student_group_id")
            if sg_id:
                student_group = StudentGroup.objects.filter(pk=sg_id).first()
            if student_group is None and sg_name:
                student_group = StudentGroup.objects.filter(
                    program_id=pc.program_id, year=pc.year, semester=pc.semester, name__iexact=sg_name
                ).first()
                if student_group is None:
                    warnings.append(f"Row {excel_row_no}: Student Group '{sg_name}' not found "
                                     f"for {pc.program.name} Year {pc.year} — treated as shared "
                                     f"(no group).")

            # ---- unit type / elective ---------------------------------------
            unit_type_text = (val(row_cells, "Unit Type") or "").strip().lower()
            is_elective = unit_type_text.startswith("elect")

            # ---- elective group (SelectionGroup) -----------------------------
            selection_group = None
            eg_name = (val(row_cells, "Elective Group") or "").strip()
            eg_id = val(row_cells, "_selection_group_id")
            if eg_id:
                selection_group = SelectionGroup.objects.filter(pk=eg_id).first()
            if selection_group is None and eg_name and is_elective:
                selection_group, _sg_created = SelectionGroup.objects.get_or_create(
                    name=eg_name, department=allocating_dept, program=pc.program,
                )

            # ---- combination / specialization stem --------------------------
            stem = None
            stem_name = (val(row_cells, "Combination Stem") or "").strip()
            stem_id = val(row_cells, "_specialization_stem_id")
            if stem_id:
                stem = SpecializationStem.objects.filter(pk=stem_id).first()
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
                            defaults=defaults,
                        )
                    else:
                        alloc, was_created = CourseAllocation.get_or_create_shared(
                            pc, allocating_dept, defaults=defaults,
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

    return render(request, "course_management/course_allocation_template_result.html", {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "warnings": warnings,
        "department": dept,
    })
