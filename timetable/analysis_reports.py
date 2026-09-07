"""
timetable/analysis_reports.py
==============================
Timetable Analysis / Reports dashboard.

Adds one page (`/timetable/analysis/`) plus a handful of JSON + PDF
endpoints that sit ALONGSIDE the existing timetable panel — nothing here
writes to Timetable/CourseAllocation, it only reads, so it can never drift
out of sync with the authoritative scheduling logic.

Provided on the page:
  1. Summary cards — scheduled / unscheduled / evening-weekend / zero-student
     counts, reusing the exact same helpers the main panel uses
     (timetable_panel._get_unscheduled_allocations, etc.) so the numbers
     never disagree with what the panel itself shows.
  2. "Export unscheduled" — PDF of every unscheduled course, optionally
     scoped to a faculty / department / program.
  3. "Export scheduled" — PDF timetable for a chosen scope: whole
     university, a faculty, a department, a program, or a specific
     program + year.
  4. "Course lookup" — type one or more course codes (comma or newline
     separated). This:
       - answers "when/where is this course scheduled" on-screen (JSON,
         `query_course_schedule_api`), AND
       - can export the same result set as a PDF
         (`export_course_schedule_pdf`).
     Both are CombinedCourseGroup-aware: if "COSC 101" is merged across
     Pure CS / Applied CS / BBIT, searching "COSC 101" returns every
     member's own program-specific code plus the single shared
     venue/day/time/lecturer they were all merged onto — not just the
     primary's row.
  5. Program Analysis — detailed breakdown per program/year showing:
       - Student Groups and their courses
       - Combined Course Groups and their member courses
       - Specialization Stems and their courses
       - Selection Groups (electives) and their courses
       - Ungrouped / standalone courses
  6. Zero-Student Courses — all courses with 0 students, filterable by
     department/program
  7. Lecturer Overload — lecturers with more than 6 units, showing count and
     which courses they're teaching. Combined Course Groups count as ONE unit.
  8. Email Department — compose a message and send a department's
     unscheduled timetable / latest published timetable / current course
     allocation (PDFs, department-scoped) to that department's COD and
     COD Admin(s) on file. See _resolve_department_email_recipients(),
     department_email_recipients_api(), and send_department_timetable_email().

CombinedCourseGroup awareness
------------------------------
A base code like "COSC 101" may correspond to several CourseAllocation
rows once merged (different course_code strings, e.g. "COSC 101(BBIT)"),
sharing one Timetable row via the group's primary_allocation. Every
listing/query/export function below expands a user-entered code through
`_expand_code_to_allocations()`, which:
  - matches by canonical course code (letters+digits only, so
    "COSC101", "COSC 101", "cosc-101" all match), AND
  - pulls in every CombinedCourseGroup sibling of any match, even if the
    sibling's own stored course_code differs (e.g. "COSC 101(BBIT)").
"""
import logging
import re
from datetime import datetime, timedelta, time as dt_time
from io import BytesIO
from collections import defaultdict
from typing import Dict, List, Optional, Any, Tuple

from django.http import HttpResponse, JsonResponse, QueryDict
from django.shortcuts import render
from django.db.models import Q, Count, Sum, F
from django.views.decorators.http import require_POST
from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.conf import settings

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, HRFlowable,
)

from core.rbac import allowed_roles, Role
from course_allocation.models import (
    CourseAllocation, CombinedCourseGroup, StudentGroup, SpecializationStem,
    SelectionGroup,
)
from timetable.models import Timetable, ExamTimetable, SchedulerConfig
from room_management.models import Venue, VenueBlock, VenueSpecialization
from program_management.models import Program, ProgramCourse
from department_management.models import Department
from faculty_management.models import Faculty
from lecturer_portal.models import Lecturer

from export_import.models import TimetablePdfTemplate
from export_import.pdf_watermark import draw_reportlab_watermark
from program_management.code_utils import canonical_course_key
from course_management.cod_panel import strip_group_suffix, normalize_code

from timetable.timetable_panel import (
    _get_unscheduled_allocations,
    _get_unscheduled_evening_weekend_allocations,
    _get_zero_student_allocations,
    _get_combined_group_meta_map,
    _get_year_value,
)
# Exam-scope equivalent of the "which CourseAllocations are already booked"
# exclusion set above — deliberately NOT the same query. Exam scheduling has
# its own notion of "covered" (direct ExamTimetable row, OR a member of a
# published/draft MergedCourseGroup, OR a SharedVenueExamGroup, OR a
# CombinedCourseGroup member whose primary is exam-scheduled) which has
# nothing to do with the regular Timetable table. See _scheduled_excluded_ids()
# in exam_timetable_panel.py for the authoritative definition.
from timetable.exam_timetable_panel import _scheduled_excluded_ids as _exam_scheduled_excluded_ids
from allocation_reports.models import AllocationPdfRun

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# SCOPE — every analysis endpoint operates on either the regular Timetable
# or the ExamTimetable. The scope is chosen once (the front-end asks
# "Timetable or Exam?" before it ever lands on /timetable/analysis/), sent
# on the querystring as ?scope=timetable|exam, and pinned in the session so
# every subsequent AJAX call on the page — even ones that forget to pass it
# — stays on the same scope as the page the user is looking at.
# ═══════════════════════════════════════════════════════════════════════════

SCOPE_TIMETABLE = 'timetable'
SCOPE_EXAM = 'exam'
SCOPE_SESSION_KEY = 'analysis_scope'


def _resolve_scope(request):
    """Return 'timetable' or 'exam' for this request.

    Precedence: an explicit ?scope=/POST scope= param (also re-pins the
    session so the choice sticks for the rest of the visit) then the
    session's last choice, then 'timetable' as the safe default for any
    old bookmarked/linked URL that never specified one.
    """
    raw = (request.GET.get('scope') or request.POST.get('scope') or '').strip().lower()
    if raw in (SCOPE_TIMETABLE, SCOPE_EXAM):
        request.session[SCOPE_SESSION_KEY] = raw
        return raw
    return request.session.get(SCOPE_SESSION_KEY, SCOPE_TIMETABLE)


def _scoped_model(scope):
    return ExamTimetable if scope == SCOPE_EXAM else Timetable


def _scope_title(scope):
    return "Exam Timetable" if scope == SCOPE_EXAM else "Timetable"


def _get_unscheduled_allocations_for_scope(scope):
    """Scope-aware equivalent of timetable_panel._get_unscheduled_allocations().

    Regular scope defers entirely to the existing, heavily-used
    timetable_panel implementation (untouched, so nothing else that
    depends on it changes behavior). Exam scope is built from the real
    exam-scheduling exclusion set in exam_timetable_panel.py rather than
    by re-pointing the regular query at ExamTimetable, because "covered"
    means something different for exams (merged/shared exam groups) than
    it does for the regular timetable (AutoMergedExamGroup).
    """
    if scope == SCOPE_EXAM:
        exclude_ids = _exam_scheduled_excluded_ids()
        return (
            CourseAllocation.objects
            .select_related('department', 'department__faculty', 'lecturer', 'program')
            .exclude(id__in=exclude_ids)
            .filter(
                Q(department__submission_control__allow_submission_to_tt=True)
                | Q(department__submission_control__isnull=True)
            )
            .order_by('department__faculty__name', 'department__name', 'course_code')
        )
    return _get_unscheduled_allocations()


def _get_unscheduled_evening_weekend_allocations_for_scope(scope):
    """Exam-scope sibling of _get_unscheduled_evening_weekend_allocations()."""
    if scope == SCOPE_EXAM:
        exclude_ids = _exam_scheduled_excluded_ids()
        return (
            CourseAllocation.objects
            .select_related('department', 'department__faculty', 'lecturer', 'program')
            .filter(is_evening_weekend=True)
            .exclude(id__in=exclude_ids)
            .filter(
                Q(department__submission_control__allow_submission_to_tt=True)
                | Q(department__submission_control__isnull=True)
            )
            .order_by('department__faculty__name', 'department__name', 'course_code')
        )
    return _get_unscheduled_evening_weekend_allocations()


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 0 — shared helpers
# ═══════════════════════════════════════════════════════════════════════════

def _get_template_config():
    try:
        return TimetablePdfTemplate.get_template()
    except Exception:
        class _Mock:
            university_name = "Chuka University"
            motto_latin = "Sapientia divitia est"
            motto_swahili = "Akili ni Mali"
            directorate_name = "DIRECTORATE OF EXAMINATIONS AND TIMETABLING"
            telephone = "020-2310512/18"
            direct_line = ""
            address = "P. O. Box 109-60400, Chuka"
            email = "extt@chuka.ac.ke"
            website = "https://www.chuka.ac.ke"
            university_logo = None
            watermark_enabled = False

            def get_reference_number(self, date_str):
                return f"CU/EXTT/{date_str}"
        return _Mock()


def _clean_ref(ref):
    """Strip a redundant leading 'Ref:' label so the letterhead never
    ends up printing 'Ref: Ref: ...' regardless of whether the configured
    template's get_reference_number() already includes the label."""
    ref = (ref or "").strip()
    if ref.lower().startswith("ref:"):
        ref = ref[4:].strip()
    return ref


REPORT_COLORS = {
    'header':       colors.HexColor('#000000'),  # black table/section header band — official B/W look, no branding color
    'header_light': colors.HexColor('#4D4D4D'),  # dark grey header band for secondary/reference tables
    'grid':         colors.HexColor('#999999'),  # mid-grey rule/grid lines
    'alt_row':      colors.HexColor('#F2F2F2'),  # light-grey zebra striping
    'text':         colors.HexColor('#000000'),  # body text — pure black
    'warn':         colors.HexColor('#000000'),  # emphasis (unscheduled/overload/etc.) — black + bold, not colored
    'orange':       colors.HexColor('#000000'),  # kept for backward-compat; no longer used as an accent colour
}


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        'RTitle', parent=styles['Heading2'], fontSize=15,
        textColor=REPORT_COLORS['header'], alignment=TA_CENTER,
        fontName="Helvetica-Bold", spaceBefore=6, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        'RSubtitle', parent=styles['Normal'], fontSize=10.5,
        textColor=REPORT_COLORS['text'], alignment=TA_CENTER,
        fontName="Helvetica", spaceAfter=10,
    ))
    styles.add(ParagraphStyle(
        'RSection', parent=styles['Normal'], fontSize=12,
        textColor=REPORT_COLORS['header'], fontName="Helvetica-Bold",
        spaceBefore=14, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        'RCell', parent=styles['Normal'], fontSize=9, leading=12,
        textColor=REPORT_COLORS['text'], fontName="Helvetica", wordWrap='CJK',
    ))
    styles.add(ParagraphStyle(
        'RCellBold', parent=styles['Normal'], fontSize=9, leading=12,
        textColor=REPORT_COLORS['text'], fontName="Helvetica-Bold", wordWrap='CJK',
    ))
    styles.add(ParagraphStyle(
        'RHead', parent=styles['Normal'], fontSize=9.5, leading=12,
        textColor=colors.white, fontName="Helvetica-Bold",
        alignment=TA_CENTER, wordWrap='CJK',
    ))
    # ── Letterhead-specific styles — matches the official university
    #    letterhead: serif, black-and-white, centered university block,
    #    left/right contact columns, bold Ref/Date line. ──────────────────
    styles.add(ParagraphStyle(
        'RUniName', parent=styles['Normal'], fontSize=20, leading=23,
        textColor=colors.black, alignment=TA_CENTER,
        fontName="Times-Bold", spaceBefore=4, spaceAfter=0,
    ))
    styles.add(ParagraphStyle(
        'RUniMotto', parent=styles['Normal'], fontSize=10, leading=12,
        textColor=colors.black, alignment=TA_CENTER,
        fontName="Times-Italic", spaceBefore=0, spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        'RDirectorate', parent=styles['Normal'], fontSize=11, leading=14,
        textColor=colors.black, alignment=TA_CENTER,
        fontName="Times-Bold", spaceBefore=0, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        'RContactLeft', parent=styles['Normal'], fontSize=8.5, leading=12,
        textColor=colors.black, alignment=TA_LEFT, fontName="Helvetica",
    ))
    styles.add(ParagraphStyle(
        'RContactRight', parent=styles['Normal'], fontSize=8.5, leading=12,
        textColor=colors.black, alignment=TA_RIGHT, fontName="Helvetica",
    ))
    styles.add(ParagraphStyle(
        'RRefDate', parent=styles['Normal'], fontSize=9.5, leading=12,
        textColor=colors.black, fontName="Helvetica",
    ))
    styles.add(ParagraphStyle(
        'RReportTitle', parent=styles['Normal'], fontSize=14, leading=18,
        textColor=colors.black, alignment=TA_CENTER,
        fontName="Times-Bold", spaceBefore=10, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        'RReportSubtitle', parent=styles['Normal'], fontSize=9.5, leading=12,
        textColor=colors.HexColor('#444444'), alignment=TA_CENTER,
        fontName="Helvetica", spaceAfter=6,
    ))
    # ── Dashboard summary styles ────────────────────────────────────────
    styles.add(ParagraphStyle(
        'RStatLabel', parent=styles['Normal'], fontSize=8, leading=10,
        textColor=colors.white, fontName="Helvetica-Bold",
        alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        'RStatValue', parent=styles['Normal'], fontSize=15, leading=18,
        textColor=REPORT_COLORS['header'], fontName="Helvetica-Bold",
        alignment=TA_CENTER,
    ))
    return styles


def _letterhead(elements, styles, template_config, title, subtitle="", ref="", date_str=""):
    """Shared letterhead block, matching the official university letterhead
    design: centered logo, university name + motto, directorate name, a
    left/right contact block (telephones/direct line vs. P.O. Box/email/
    website), a rule, a bold Ref/Date line, then the report title.
    """
    if getattr(template_config, "university_logo", None):
        try:
            logo_path = template_config.university_logo.path
            logo = Image(logo_path, width=0.9 * inch, height=0.9 * inch)
            logo.hAlign = 'CENTER'
            elements.append(logo)
        except Exception:
            pass

    elements.append(Paragraph(
        getattr(template_config, "university_name", "Chuka University"), styles['RUniName']
    ))
    motto = f"{getattr(template_config, 'motto_latin', '')} {getattr(template_config, 'motto_swahili', '')}".strip()
    if motto:
        elements.append(Paragraph(motto, styles['RUniMotto']))

    directorate_name = getattr(template_config, "directorate_name", "")
    if directorate_name:
        elements.append(Paragraph(directorate_name.upper(), styles['RDirectorate']))

    telephone = getattr(template_config, "telephone", "")
    direct_line = getattr(template_config, "direct_line", "")
    left_lines = []
    if telephone:
        left_lines.append(f"Telephones: {telephone}")
    left_lines.append(f"Direct Line: {direct_line}" if direct_line else "Direct Line:")

    address = getattr(template_config, "address", "")
    email = getattr(template_config, "email", "")
    website = getattr(template_config, "website", "")
    right_lines = []
    if address:
        right_lines.append(address)
    email_website = " ".join(filter(None, [f"Email: {email}" if email else "", website]))
    if email_website:
        right_lines.append(email_website)

    if left_lines or right_lines:
        contact_row = Table(
            [[
                Paragraph("<br/>".join(left_lines), styles['RContactLeft']),
                Paragraph("<br/>".join(right_lines), styles['RContactRight']),
            ]],
            colWidths=["50%", "50%"],
        )
        contact_row.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        elements.append(contact_row)

    elements.append(HRFlowable(width="100%", thickness=0.8, color=colors.black, spaceAfter=6))

    ref = _clean_ref(ref)
    if ref or date_str:
        ref_date_row = Table(
            [[
                Paragraph(f"<b>Ref:</b> {ref}" if ref else "", styles['RRefDate']),
                Paragraph(f"<b>Date:</b> {date_str}" if date_str else "", styles['RRefDate']),
            ]],
            colWidths=["70%", "30%"],
        )
        ref_date_row.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ]))
        elements.append(ref_date_row)

    elements.append(Paragraph(title, styles['RReportTitle']))
    if subtitle:
        elements.append(Paragraph(subtitle, styles['RReportSubtitle']))
    elements.append(Spacer(1, 8))


def _dashboard_summary_table(dashboard_counts, styles):
    """A compact, professionally formatted summary strip of the on-screen
    dashboard totals for this report's scope, used in place of a plain
    run-on sentence of counts."""
    labels = ['Scheduled', 'Unscheduled', 'Evening/Weekend\n(Unscheduled)', 'Zero-Student']
    keys = ['scheduled_count', 'unscheduled_count', 'ew_unscheduled_count', 'zero_student_count']

    header_row = [Paragraph(label.replace('\n', '<br/>'), styles['RStatLabel']) for label in labels]
    value_row = [Paragraph(str(dashboard_counts.get(k, 0)), styles['RStatValue']) for k in keys]

    table = Table([header_row, value_row], colWidths=[1.7 * inch] * 4)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), REPORT_COLORS['header']),
        ('BACKGROUND', (0, 1), (-1, 1), REPORT_COLORS['alt_row']),
        ('GRID', (0, 0), (-1, -1), 0.5, REPORT_COLORS['grid']),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return table


def _full_name(request):
    """Best-effort display name of the person generating the report — their
    actual name, not their login/username. Falls back to the username only
    if no first/last name is on file, and to 'Unknown' if unauthenticated."""
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return "Unknown"
    full_name = user.get_full_name().strip() if hasattr(user, 'get_full_name') else ""
    return full_name or getattr(user, 'username', 'Unknown')


def _footer_factory(template_config, doc_ref, compiled_by="Unknown"):
    """Returns an onPage callback that paints the watermark + a small footer."""
    def _on_page(canvas_obj, doc_obj):
        draw_reportlab_watermark(canvas_obj, doc_obj, template_config, doc_ref)
        canvas_obj.saveState()
        canvas_obj.setFont('Helvetica', 7.5)
        canvas_obj.setFillColor(colors.grey)
        page_w, page_h = doc_obj.pagesize
        canvas_obj.drawString(0.5 * inch, 0.35 * inch, f"Compiled by: {compiled_by}")
        canvas_obj.drawRightString(page_w - 0.5 * inch, 0.35 * inch, f"Page {doc_obj.page}")
        canvas_obj.restoreState()
    return _on_page


def _standard_table_style(header_bg=None):
    header_bg = header_bg or REPORT_COLORS['header']
    return TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), header_bg),
        ('GRID', (0, 0), (-1, -1), 0.5, REPORT_COLORS['grid']),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, REPORT_COLORS['alt_row']]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ])


def _pdf_response(doc_elements, filename, template_config, doc_ref, landscape_mode=False, compiled_by="Unknown"):
    buffer = BytesIO()
    pagesize = landscape(A4) if landscape_mode else A4
    doc = SimpleDocTemplate(
        buffer, pagesize=pagesize,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.55 * inch, rightMargin=0.55 * inch,
    )
    on_page = _footer_factory(template_config, doc_ref, compiled_by=compiled_by)
    doc.build(doc_elements, onFirstPage=on_page, onLaterPages=on_page)
    buffer.seek(0)
    response = HttpResponse(buffer.read(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — scope filtering (faculty / department / program / program+year)
# ═══════════════════════════════════════════════════════════════════════════

def _apply_scope_filters(qs, request, alloc_field_prefix=""):
    """
    Apply faculty_id / department_id / program_id / year GET params to a
    CourseAllocation (or Timetable, via alloc_field_prefix='course_allocation__')
    queryset. All params are optional and combine with AND.

    department_id / faculty_id match EITHER the allocation's own
    `department` (the administratively "allocating" department) OR its
    program's home department (`program__department`). These two are
    usually the same department, but not always — e.g. a course can be
    allocated with `department` set to whichever department is actually
    teaching it (origin_department territory) while its `program` still
    belongs to a different department's curriculum (e.g. COSC 103 taught
    by Computer Science but taken under Social Sciences' BLIS program).
    Matching only `department` there would silently drop that course from
    Social Sciences' own scoped report even though the program is theirs.
    Matching both means a department-scoped report always shows every
    course belonging to its programs, however `department` was set.
    """
    p = alloc_field_prefix
    faculty_id = request.GET.get('faculty_id')
    department_id = request.GET.get('department_id')
    program_id = request.GET.get('program_id')
    year = request.GET.get('year')

    if faculty_id:
        qs = qs.filter(
            Q(**{f'{p}department__faculty_id': faculty_id}) |
            Q(**{f'{p}program__department__faculty_id': faculty_id})
        )
    if department_id:
        qs = qs.filter(
            Q(**{f'{p}department_id': department_id}) |
            Q(**{f'{p}program__department_id': department_id})
        )
    if program_id:
        qs = qs.filter(**{f'{p}program_id': program_id})
    if year:
        qs = qs.filter(**{f'{p}program_course__year': year})
    return qs


def _scope_label(request):
    """Human-readable description of the applied scope, for the PDF subtitle."""
    parts = []
    faculty_id = request.GET.get('faculty_id')
    department_id = request.GET.get('department_id')
    program_id = request.GET.get('program_id')
    year = request.GET.get('year')

    if faculty_id:
        f = Faculty.objects.filter(id=faculty_id).first()
        if f:
            parts.append(f"Faculty: {f.name}")
    if department_id:
        d = Department.objects.filter(id=department_id).first()
        if d:
            parts.append(f"Department: {d.name}")
    if program_id:
        prog = Program.objects.filter(id=program_id).first()
        if prog:
            parts.append(f"Program: {prog.name}")
    if year:
        parts.append(f"Year: {year}")

    return " | ".join(parts) if parts else "All Faculties / Departments / Programs"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1B — Serviced Courses (origin_department ≠ allocating department)
# ═══════════════════════════════════════════════════════════════════════════
# A course is allocated under the department that owns the PROGRAM it's
# taken in (CourseAllocation.department) — but is sometimes actually
# taught by a different department (CourseAllocation.origin_department).
# e.g. "ZOOL 143" is allocated under Computer Science (it's a unit on the
# BSc Computer Science curriculum) but is taught/serviced by Biological
# Sciences. Biological Sciences never shows up as the "department" on that
# allocation, so without this section they'd have no way to see, on their
# own scoped report, that they're servicing it.

def _apply_scope_filters_origin(qs, request):
    """
    Same faculty_id/department_id/program_id/year GET params as
    `_apply_scope_filters`, but matched against origin_department instead
    of the allocating department — so scoping the report to a department
    surfaces courses THAT department services for someone else's program,
    not just courses under its own programs.
    """
    faculty_id = request.GET.get('faculty_id')
    department_id = request.GET.get('department_id')
    program_id = request.GET.get('program_id')
    year = request.GET.get('year')

    if faculty_id:
        qs = qs.filter(origin_department__faculty_id=faculty_id)
    if department_id:
        qs = qs.filter(origin_department_id=department_id)
    if program_id:
        qs = qs.filter(program_id=program_id)
    if year:
        qs = qs.filter(program_course__year=year)
    return qs


def _serviced_courses_qs(request):
    """
    CourseAllocation rows with an origin_department set that differs from
    the allocating department, scoped the same way as the rest of the
    report (against origin_department — see `_apply_scope_filters_origin`).
    """
    qs = CourseAllocation.objects.exclude(
        origin_department__isnull=True
    ).exclude(
        origin_department=F('department')
    ).select_related(
        'department', 'origin_department', 'origin_department__faculty',
        'program', 'lecturer', 'program_course',
    )
    return _apply_scope_filters_origin(qs, request)


def _serviced_courses_section(elements, styles, request, tt_cache, only_scheduled=False):
    """
    Appends a "SERVICED COURSES" section to `elements`: every in-scope
    course whose origin_department differs from its allocating department,
    grouped by the servicing (origin) department, showing which department
    it's actually allocated under, which program/section it's for, and
    (when scheduled) its day/time/venue.

    only_scheduled=True (used by the Scheduled Timetable export) drops any
    serviced course that isn't actually scheduled yet, since that report
    only ever lists scheduled entries.
    """
    serviced = list(_serviced_courses_qs(request))
    if only_scheduled:
        serviced = [a for a in serviced if tt_cache.get(a.id)]

    elements.append(Spacer(1, 14))
    elements.append(HRFlowable(width="100%", thickness=0.8, color=REPORT_COLORS['grid'], spaceAfter=10))
    elements.append(Paragraph("SERVICED COURSES", styles['RSection']))
    elements.append(Spacer(1, 6))

    if not serviced:
        elements.append(Paragraph("None.", styles['RCell']))
        return

    by_origin = defaultdict(list)
    for a in serviced:
        by_origin[a.origin_department.name if a.origin_department else 'Unknown'].append(a)

    if only_scheduled:
        header = ['Course Code', 'Course Name', 'Allocating Dept', 'Program', 'Lecturer', 'Students', 'Day', 'Time', 'Venue']
        widths = [0.9 * inch, 1.7 * inch, 1.4 * inch, 1.6 * inch, 1.2 * inch, 0.6 * inch, 0.75 * inch, 0.8 * inch, 0.75 * inch]
    else:
        header = ['Course Code', 'Course Name', 'Allocating Dept', 'Program', 'Lecturer', 'Students', 'Status', 'Day', 'Time', 'Venue']
        widths = [0.9 * inch, 1.6 * inch, 1.3 * inch, 1.5 * inch, 1.1 * inch, 0.55 * inch, 0.75 * inch, 0.7 * inch, 0.75 * inch, 0.7 * inch]

    for origin_name in sorted(by_origin.keys()):
        allocs = by_origin[origin_name]
        elements.append(Paragraph(
            f"Serviced by: {origin_name}",
            ParagraphStyle('SvcDeptSub', parent=styles['RCellBold'], textColor=REPORT_COLORS['header']),
        ))
        rows = [[Paragraph(h, styles['RHead']) for h in header]]
        for a in sorted(allocs, key=lambda x: x.course_code):
            sched = _schedule_info_for_allocation(a, tt_cache)
            row = [
                Paragraph(a.course_code, styles['RCellBold']),
                Paragraph(a.course_name, styles['RCell']),
                Paragraph(a.department.name if a.department else '-', styles['RCell']),
                Paragraph(a.program.name if a.program else '-', styles['RCell']),
                Paragraph(a.lecturer.display_name if a.lecturer else 'Unassigned', styles['RCell']),
                Paragraph(str(a.number_of_students or 0), styles['RCell']),
            ]
            if not only_scheduled:
                status_style = styles['RCell'] if sched['scheduled'] else ParagraphStyle(
                    'SvcWarnCell', parent=styles['RCell'], textColor=REPORT_COLORS['warn'], fontName='Helvetica-Bold',
                )
                row.append(Paragraph('Scheduled' if sched['scheduled'] else 'NOT SCHEDULED', status_style))
            time_str = f"{sched['start_time']}-{sched['end_time']}" if sched['scheduled'] else '—'
            row += [
                Paragraph(sched['day'] if sched['scheduled'] else '—', styles['RCell']),
                Paragraph(time_str, styles['RCell']),
                Paragraph(sched['venue'] if sched['scheduled'] else '—', styles['RCell']),
            ]
            rows.append(row)
        table = Table(rows, colWidths=widths, repeatRows=1)
        table.setStyle(_standard_table_style())
        elements.append(table)
        elements.append(Spacer(1, 8))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — course-code expansion (CombinedCourseGroup aware)
# ═══════════════════════════════════════════════════════════════════════════

def _expand_code_to_allocations(raw_code):
    """
    Given a user-entered course code (any formatting), return the full set
    of CourseAllocation rows it should mean:
      - every allocation whose canonical base code matches, PLUS
      - every CombinedCourseGroup sibling of any of those matches, even if
        the sibling's own course_code string is completely different
        (e.g. searching "COSC 101" also returns "COSC 101(BBIT)" if the two
        were merged together).
    Returns a queryset-like list of CourseAllocation objects, deduplicated.
    """
    base, _suffix = strip_group_suffix(normalize_code(raw_code))
    key = canonical_course_key(base)
    if not key:
        return []

    # Narrow the candidate set at the DB level instead of loading every
    # CourseAllocation row (with joins + prefetches) on every lookup — that
    # used to mean a full-table scan/instantiation PER requested code, which
    # is what made the course-lookup + its PDF export slow as the table grew.
    # The letters+digits substrings extracted from the canonical key are a
    # superset filter: anything that can canonical-match `key` is guaranteed
    # to contain them, so this never drops a legitimate match — it just
    # avoids materializing rows that can't possibly match.
    m = re.match(r'^([A-Z]+)(\d+)', key)
    narrowed = CourseAllocation.objects.all()
    if m:
        letters, digits = m.group(1), m.group(2)
        narrowed = narrowed.filter(course_code__icontains=letters).filter(course_code__icontains=digits)
    else:
        narrowed = narrowed.filter(course_code__icontains=key)

    candidates = list(
        narrowed
        .select_related(
            'department', 'department__faculty', 'program', 'lecturer', 'program_course',
            'student_group', 'specialization_stem', 'specialization_stem__category',
        )
        .prefetch_related('combined_groups__allocations')
    )
    matched = {}
    for a in candidates:
        a_base, _ = strip_group_suffix(a.course_code or "")
        if canonical_course_key(a_base) == key:
            matched[a.id] = a

    # Pull in CombinedCourseGroup siblings of anything matched so far.
    changed = True
    while changed:
        changed = False
        current_ids = list(matched.keys())
        for aid in current_ids:
            for group in matched[aid].combined_groups.all():
                for sibling in group.allocations.all():
                    if sibling.id not in matched:
                        matched[sibling.id] = sibling
                        changed = True

    return list(matched.values())


def _expand_term_to_allocations(raw_term):
    """
    Universal version of `_expand_code_to_allocations` — the term doesn't
    have to be a course code any more. It tries an exact course-code match
    first (fast, narrowed DB query, CombinedCourseGroup-aware), and if that
    finds nothing — because the term is a lecturer name, program, department,
    faculty, venue, specialization, student group, etc. — it falls back to
    the same broad free-text search used by Universal Search, then still
    pulls in CombinedCourseGroup siblings for whatever that finds.
    """
    matched = {a.id: a for a in _expand_code_to_allocations(raw_term)}

    if not matched:
        for a in _universal_search_qs(raw_term):
            matched[a.id] = a

        changed = True
        while changed:
            changed = False
            current_ids = list(matched.keys())
            for aid in current_ids:
                for group in matched[aid].combined_groups.all():
                    for sibling in group.allocations.all():
                        if sibling.id not in matched:
                            matched[sibling.id] = sibling
                            changed = True

    return list(matched.values())


def _specialization_label(alloc):
    """
    'Which specialization' for a course that belongs to a SpecializationStem
    — e.g. 'Artificial Intelligence (Computer Science Specializations)'.
    None if the course isn't stem-based (an ordinary/core course).
    """
    stem = getattr(alloc, 'specialization_stem', None)
    if not stem:
        return None
    category_name = getattr(stem.category, 'name', None) if getattr(stem, 'category_id', None) else None
    return f"{stem.name} ({category_name})" if category_name else stem.name


def _student_group_label(alloc):
    """
    'Which group' for a course tied to a specific StudentGroup (e.g.
    'Group A'). None if the course is shared/ungrouped.
    """
    grp = getattr(alloc, 'student_group', None)
    if not grp:
        return None
    return grp.name or grp.letter or None


def _schedule_info_for_allocation(alloc, tt_cache=None, lookup_id=None, model=Timetable):
    """Return {'scheduled': bool, 'day', 'start_time', 'end_time', 'venue'} for one allocation.

    lookup_id lets a non-primary CombinedCourseGroup member resolve its
    schedule via the group's primary_allocation id instead of its own —
    non-primary members never get their own Timetable row (the autoscheduler
    books one venue/timeslot for the whole merged group, on the primary
    allocation only), so looking up the member's own id would always report
    "Unscheduled" even when the group it belongs to has a slot.

    `model` (Timetable or ExamTimetable) is only consulted when no tt_cache
    is supplied — every real caller in this file passes a pre-built cache
    already keyed against the correct scoped model, so this fallback mainly
    matters for direct/ad-hoc calls.
    """
    tt = None
    key = lookup_id if lookup_id is not None else alloc.id
    if tt_cache is not None:
        tt = tt_cache.get(key)
    else:
        tt = model.objects.filter(
            course_allocation_id=key, venue__isnull=False,
            start_time__isnull=False, end_time__isnull=False,
        ).select_related('venue').first()
    if tt:
        return {
            'scheduled': True,
            'day': tt.day or '',
            'start_time': tt.start_time.strftime('%H:%M') if tt.start_time else '',
            'end_time': tt.end_time.strftime('%H:%M') if tt.end_time else '',
            'venue': tt.venue.code if tt.venue else '',
        }
    return {'scheduled': False, 'day': '', 'start_time': '', 'end_time': '', 'venue': ''}


def _parse_codes_param(request):
    """
    Accept search terms — course codes OR any free-text term (lecturer,
    program, department, faculty, venue, specialization, student group,
    day) — from either:
      - repeated GET params: ?code=COSC 101&code=Mwangi
      - one comma/newline separated param: ?codes=COSC 101, Mwangi, BBIT
    Returns a deduplicated, order-preserved list of raw term strings.
    """
    codes = request.GET.getlist('code')
    combined_param = request.GET.get('codes', '')
    if combined_param:
        for part in combined_param.replace('\n', ',').split(','):
            part = part.strip()
            if part:
                codes.append(part)
    seen = set()
    result = []
    for c in codes:
        c = c.strip()
        if c and c.upper() not in seen:
            seen.add(c.upper())
            result.append(c)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — dashboard page + summary / filter-options APIs
# ═══════════════════════════════════════════════════════════════════════════

def _dashboard_scope_counts(request):
    """
    Single source of truth for the scheduled / unscheduled / evening-weekend
    / zero-student counts for a scope.

    Both the on-screen summary cards (analysis_summary_api) AND the PDF
    exports call this SAME function now — previously the PDF re-derived its
    own "unscheduled" total from a different definition (individual
    non-merged sections counted one way, merged groups counted a completely
    separate way, never summed together), which is why the number printed
    on the PDF didn't match the card on screen. Routing both through this
    one function means they can't drift apart again.
    """
    scope = _resolve_scope(request)
    Model = _scoped_model(scope)

    scheduled_qs = Model.objects.filter(
        venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
    )
    scheduled_qs = _apply_scope_filters(scheduled_qs, request, alloc_field_prefix='course_allocation__')
    scheduled_count = scheduled_qs.values('course_allocation_id').distinct().count()

    unscheduled_qs = _apply_scope_filters(_get_unscheduled_allocations_for_scope(scope), request)
    ew_qs = _apply_scope_filters(_get_unscheduled_evening_weekend_allocations_for_scope(scope), request)
    zero_qs = _apply_scope_filters(_get_zero_student_allocations(), request)

    return {
        'scheduled_count': scheduled_count,
        'unscheduled_count': unscheduled_qs.count(),
        'ew_unscheduled_count': ew_qs.count(),
        'zero_student_count': zero_qs.count(),
    }


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def analysis_dashboard(request):
    """Render the Timetable Analysis & Reports page.

    Requires ?scope=timetable or ?scope=exam (the dashboard's own launch
    link asks the user which one before it ever links here — see
    timetabling_dashboard.html). Anyone who lands here without a scope
    (an old bookmark, a typed URL) falls back to whatever they last picked
    this session, or 'timetable' the very first time.
    """
    scope = _resolve_scope(request)
    faculties = list(Faculty.objects.order_by('name').values('id', 'name'))
    departments = list(Department.objects.order_by('name').values('id', 'name', 'faculty_id'))
    programs = list(Program.objects.order_by('name').values('id', 'name', 'department_id'))
    years = [{'value': y, 'label': f'Year {y}'} for y in range(1, 7)]

    context = {
        'faculties': faculties,
        'departments': departments,
        'programs': programs,
        'years': years,
        'scope': scope,
        'scope_label': _scope_title(scope),
        'is_exam_scope': scope == SCOPE_EXAM,
    }
    return render(request, 'timetable/analysis_dashboard.html', context)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def analysis_summary_api(request):
    """
    Summary counts for the dashboard cards, scoped by the same
    faculty_id/department_id/program_id/year filters as the exports, so the
    numbers on screen always match what the "Export" buttons will produce.
    """
    counts = _dashboard_scope_counts(request)
    scope = _resolve_scope(request)

    return JsonResponse({
        'status': 'success',
        'scope_label': _scope_label(request),
        'exam_or_timetable': scope,
        'exam_or_timetable_label': _scope_title(scope),
        **counts,
    })


_DAY_SORT_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def scheduled_timetable_api(request):
    """
    JSON: the actual scheduled Timetable rows for whatever scope is
    currently applied (faculty_id/department_id/program_id/year GET
    params — same as every other report on this dashboard), so the on-
    screen "Timetable" table always matches what "Export Scheduled
    Timetable — PDF" would print, without having to open the PDF.

    Returns every matching row, uncapped, sorted Monday→Sunday then by
    start time then venue — same ordering the day-grid PDF uses.
    """
    scope = _resolve_scope(request)
    Model = _scoped_model(scope)

    qs = Model.objects.filter(
        venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
    ).select_related(
        'venue', 'course_allocation', 'course_allocation__lecturer',
        'course_allocation__program', 'course_allocation__department',
        'course_allocation__program_course',
    )
    qs = _apply_scope_filters(qs, request, alloc_field_prefix='course_allocation__')

    rows = []
    for tt in qs:
        alloc = tt.course_allocation
        rows.append({
            'day': tt.day or '',
            'date': tt.date.isoformat() if scope == SCOPE_EXAM and tt.date else '',
            'start_time': tt.start_time.strftime('%H:%M') if tt.start_time else '',
            'end_time': tt.end_time.strftime('%H:%M') if tt.end_time else '',
            'course_code': alloc.course_code,
            'course_name': alloc.course_name,
            'venue': tt.venue.code if tt.venue else '',
            'lecturer': getattr(alloc.lecturer, 'name', 'Unassigned'),
            'department': getattr(alloc.department, 'name', 'N/A'),
            'program': getattr(alloc.program, 'name', 'N/A'),
            'year': _get_year_value(alloc),
            'students': alloc.number_of_students or 0,
        })

    def _sort_key(r):
        try:
            day_idx = _DAY_SORT_ORDER.index(r['day'])
        except ValueError:
            day_idx = len(_DAY_SORT_ORDER)
        return (r['date'], day_idx, r['start_time'], r['venue']) if scope == SCOPE_EXAM else (day_idx, r['start_time'], r['venue'])

    rows.sort(key=_sort_key)

    return JsonResponse({
        'status': 'success',
        'scope_label': _scope_label(request),
        'exam_or_timetable': scope,
        'count': len(rows),
        'results': rows,
    })


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — export: unscheduled courses
# ═══════════════════════════════════════════════════════════════════════════

DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def _build_time_slots():
    """
    Column axis for the day grids: derived from SchedulerConfig
    (start_time/end_time/slot_size), same source the autoscheduler itself
    uses — so the grid's columns line up with the slots the algorithm
    actually schedules into.
    """
    try:
        config = SchedulerConfig.objects.first()
    except Exception:
        config = None
    start_time = config.start_time if config else dt_time(7, 0)
    end_time = config.end_time if config else dt_time(19, 0)
    slot_size = config.slot_size if config else 3

    slots = []
    current = datetime.combine(datetime.today(), start_time)
    end_dt = datetime.combine(datetime.today(), end_time)
    while current < end_dt:
        nxt = current + timedelta(hours=slot_size)
        if nxt > end_dt:
            nxt = end_dt
        slots.append((current.time(), nxt.time()))
        current = nxt
    return slots


def _slot_label(st, en):
    return f"{st.strftime('%H:%M')}-{en.strftime('%H:%M')}"


def _day_grid_tables(tt_iterable, styles, table_style_fn=None, rich=False, show_program=False, col_total_width=9.6 * inch):
    """
    Build one Table PER DAY: venues down the left column, timeslots across
    the top — the standard timetable-grid layout used elsewhere in this app
    (export_import/official_timetables.py).

    `tt_iterable`: Timetable objects, already select_related('venue',
    'course_allocation', ...).
    `rich`: if True, each cell shows course code + lecturer (bold code,
    small lecturer line) — used for the full scheduled-timetable export.
    If False, cells show just the course code(s) — used for the compact
    "already scheduled" reference tables.
    `show_program`: if True (only meaningful when rich=True), each cell
    also gets a third line with the program name — used for department
    views that span multiple programs.

    Any entry whose (start_time, end_time) doesn't match the configured
    SchedulerConfig grid still gets its own extra column for that day
    (appended, sorted by start time) — so nothing is silently dropped just
    because it doesn't align with the standard slot size.

    Exam entries (ExamTimetable) carry a real calendar `date` field, unlike
    regular Timetable entries which only have a recurring weekday name —
    an exam period spans multiple weeks, so the same weekday (e.g. every
    "Monday") recurs on several different dates. Grouping by weekday name
    alone would silently merge all of those different dates' exams into
    one table (and even into the same grid cell, if they share a venue +
    timeslot). So: if the entries carry a `date` attribute, group by
    (date, day) — one table per actual date — instead of by day alone.
    Regular Timetable entries have no `date` field and keep the original
    day-only grouping.

    Returns: list of (label, Table) — label is the day name for regular
    entries, or "<Day> — <dd Mon yyyy>" per date for exam entries.
    """
    table_style_fn = table_style_fn or _standard_table_style
    base_slots = _build_time_slots()

    entries_all = list(tt_iterable)
    has_date = bool(entries_all) and hasattr(entries_all[0], 'date')

    by_group = defaultdict(list)
    for tt in entries_all:
        if has_date:
            by_group[(tt.date, tt.day or 'Unspecified')].append(tt)
        else:
            by_group[tt.day or 'Unspecified'].append(tt)

    # Combined-group awareness: several member allocations of the same
    # CombinedCourseGroup are taught together (same venue/day/time), so they
    # land in the same grid cell. Rather than listing every member's own
    # course code there (e.g. "EDFO 111-A, EDFO 111-B, EDFO 111-C"), show the
    # group once via its base_course_code — one built-in query, not one per
    # cell.
    combined_group_of_alloc = {}
    all_alloc_ids = {
        tt.course_allocation_id
        for entries in by_group.values()
        for tt in entries
        if tt.course_allocation_id
    }
    if all_alloc_ids:
        for group in CombinedCourseGroup.objects.filter(
            allocations__id__in=all_alloc_ids
        ).prefetch_related('allocations').only('id', 'base_course_code'):
            for member_id in group.allocations.values_list('id', flat=True):
                combined_group_of_alloc[member_id] = group.base_course_code

    tables = []
    if has_date:
        # One table per actual calendar date, earliest first.
        ordered_keys = sorted(by_group.keys(), key=lambda k: k[0])
    else:
        ordered_keys = [d for d in DAY_ORDER if d in by_group] + [d for d in by_group if d not in DAY_ORDER]

    for group_key in ordered_keys:
        entries = [e for e in by_group[group_key] if e.venue and e.start_time and e.end_time]
        if not entries:
            continue

        if has_date:
            date_val, day_name = group_key
            label = f"{day_name} — {date_val.strftime('%d %b %Y')}"
        else:
            label = group_key

        slot_set = list(base_slots)
        known = set(slot_set)
        for tt in entries:
            slot_key = (tt.start_time, tt.end_time)
            if slot_key not in known:
                slot_set.append(slot_key)
                known.add(slot_key)
        slot_set.sort(key=lambda s: s[0])

        venues_sorted = sorted({tt.venue.code for tt in entries})

        cell_map = defaultdict(list)
        for tt in entries:
            cell_map[(tt.venue.code, tt.start_time, tt.end_time)].append(tt)

        header = ['Venue'] + [_slot_label(s, e) for s, e in slot_set]
        rows = [[Paragraph(h, styles['RHead']) for h in header]]

        for v in venues_sorted:
            row = [Paragraph(v, styles['RCellBold'])]
            for s, e in slot_set:
                occupants = cell_map.get((v, s, e), [])
                if not occupants:
                    row.append(Paragraph('', styles['RCell']))
                elif rich:
                    lines = []
                    seen_codes = set()
                    for tt in occupants:
                        alloc = tt.course_allocation
                        if not alloc:
                            code, lect, prog = '-', '', ''
                        else:
                            code = combined_group_of_alloc.get(alloc.id) or alloc.course_code
                            lect = getattr(alloc.lecturer, 'name', '') or ''
                            prog = getattr(alloc.program, 'name', '') or ''
                        if code in seen_codes:
                            continue  # combined-group sibling already shown for this cell
                        seen_codes.add(code)
                        cell_text = f"<b>{code}</b><br/><font size=7>{lect}</font>"
                        if show_program and prog:
                            cell_text += f"<br/><font size=6>{prog}</font>"
                        lines.append(cell_text)
                    row.append(Paragraph('<br/>'.join(lines), ParagraphStyle(
                        'RichCell', parent=styles['RCell'], fontSize=8,
                        leading=(12 if show_program else 10),
                    )))
                else:
                    codes = []
                    for tt in occupants:
                        alloc = tt.course_allocation
                        if not alloc:
                            code = '-'
                        else:
                            # Combined-group member — show the group's base
                            # course code once instead of this member's own
                            # (section-specific) code.
                            code = combined_group_of_alloc.get(alloc.id) or alloc.course_code
                        if code not in codes:
                            codes.append(code)
                    row.append(Paragraph(', '.join(codes), styles['RCell']))
            rows.append(row)

        n_cols = len(slot_set)
        venue_col_width = 1.0 * inch
        slot_col_width = max((col_total_width - venue_col_width) / max(n_cols, 1), 0.55 * inch)
        col_widths = [venue_col_width] + [slot_col_width] * n_cols

        table = Table(rows, colWidths=col_widths, repeatRows=1)
        table.setStyle(table_style_fn())
        tables.append((label, table))

    return tables


SEMESTER_LABELS = {1: "Semester 1", 2: "Semester 2"}
INTAKE_LABELS = {'normal': 'Normal Intake', 'special': 'Special Intake'}


def _build_match_matrix_elements(matches, styles, opts=None):
    """
    Same "matrix method" as the on-screen search/lookup results
    (buildMatrixTables() in analysis_dashboard.html): one Table PER DAY,
    venues down the left column, timeslots across the top, and each
    scheduled course dropped into the cell matching its own venue +
    timeslot. Rows with no venue/day/time yet (unscheduled) are listed in
    a separate table underneath, since they have no cell to sit in.

    `matches`: list of dicts as produced by universal_search_api /
    _build_course_lookup_results — each needs at least course_code and
    scheduled/day/start_time/end_time/venue; course_name, year, semester,
    lecturer, students, specialization, student_group, is_special_intake
    and merged_group are shown when present.

    `opts`: {'show_program': bool, 'show_dept': bool}

    Returns a list of flowables ready to append to a PDF's elements list.
    """
    opts = opts or {}
    matches = matches or []
    elements = []

    def _is_scheduled(m):
        return bool(m.get('scheduled') and m.get('day') and m.get('venue') and m.get('start_time') and m.get('end_time'))

    scheduled = [m for m in matches if _is_scheduled(m)]
    unscheduled = [m for m in matches if not _is_scheduled(m)]

    if not scheduled and not unscheduled:
        elements.append(Paragraph("No matching course allocation found.", styles['RCell']))
        return elements

    day_heading_style = ParagraphStyle(
        'MxDayHeading', parent=styles['RCellBold'], textColor=REPORT_COLORS['header'], fontSize=10.5, spaceBefore=10, spaceAfter=4,
    )
    warn_heading_style = ParagraphStyle(
        'MxWarnHeading', parent=day_heading_style, textColor=REPORT_COLORS['warn'],
    )
    cell_style = ParagraphStyle('MxCell', parent=styles['RCell'], fontSize=7.5, leading=9.5)
    code_line = '<b>{code}</b>'

    by_day = defaultdict(list)
    for m in scheduled:
        by_day[m['day']].append(m)
    ordered_days = [d for d in DAY_ORDER if d in by_day] + sorted(d for d in by_day if d not in DAY_ORDER)

    for day in ordered_days:
        entries = by_day[day]
        venues = sorted({e['venue'] for e in entries})
        slot_keys = sorted({(e['start_time'], e['end_time']) for e in entries})

        cell_map = defaultdict(list)
        for e in entries:
            cell_map[(e['venue'], e['start_time'], e['end_time'])].append(e)

        elements.append(Paragraph(
            f"{day} — {len(entries)} class(es), {len(venues)} venue(s)", day_heading_style,
        ))

        header = ['Venue'] + [f"{s}\u2013{e}" for s, e in slot_keys]
        rows = [[Paragraph(h, styles['RHead']) for h in header]]

        for v in venues:
            row = [Paragraph(v, styles['RCellBold'])]
            for s, e in slot_keys:
                occupants = cell_map.get((v, s, e), [])
                if not occupants:
                    row.append(Paragraph('', cell_style))
                else:
                    parts = []
                    for m in occupants:
                        lines = [code_line.format(code=m.get('course_code') or '-')]
                        if m.get('course_name'):
                            lines.append(m['course_name'])
                        yr_sem = []
                        if m.get('year'):
                            yr_sem.append(f"Year {m['year']}")
                        if m.get('semester'):
                            yr_sem.append(f"Sem {m['semester']}")
                        if yr_sem:
                            lines.append(' / '.join(yr_sem))
                        if m.get('lecturer'):
                            lines.append(m['lecturer'])
                        if opts.get('show_program') and m.get('program'):
                            lines.append(m['program'])
                        if opts.get('show_dept') and m.get('department'):
                            lines.append(m['department'])
                        lines.append(f"Students: {m.get('students', 0)}")
                        if m.get('specialization'):
                            lines.append(f"Specialization: {m['specialization']}")
                        if m.get('student_group'):
                            lines.append(f"Group: {m['student_group']}")
                        if m.get('is_special_intake'):
                            lines.append('<b>SPECIAL INTAKE</b>')
                        mg = m.get('merged_group')
                        if mg:
                            dept = mg.get('combined_by_department')
                            combined_line = f"Combined: {mg.get('group_code', '')} \u00d7{mg.get('member_count', '')}"
                            if dept:
                                combined_line += f" (by {dept})"
                            lines.append(combined_line)
                        parts.append('<br/>'.join(lines))
                    row.append(Paragraph('<br/><br/>'.join(parts), cell_style))
            rows.append(row)

        n_cols = len(slot_keys)
        venue_col_width = 1.1 * inch
        col_total_width = 9.6 * inch
        slot_col_width = max((col_total_width - venue_col_width) / max(n_cols, 1), 1.1 * inch)
        col_widths = [venue_col_width] + [slot_col_width] * n_cols

        table = Table(rows, colWidths=col_widths, repeatRows=1)
        table.setStyle(_standard_table_style())
        elements.append(table)
        elements.append(Spacer(1, 6))

    if unscheduled:
        elements.append(Paragraph(
            f"Not Scheduled — {len(unscheduled)} course(s)", warn_heading_style,
        ))
        header = ['Course Code', 'Course Name', 'Program', 'Year', 'Specialization', 'Student Group', 'Combined Group', 'Special', 'Lecturer', 'Students']
        rows = [[Paragraph(h, styles['RHead']) for h in header]]
        for m in unscheduled:
            mg = m.get('merged_group')
            if mg:
                combined_cell = mg.get('group_code', '')
                if mg.get('combined_by_department'):
                    combined_cell += f" (by {mg['combined_by_department']})"
            else:
                combined_cell = '\u2014'
            rows.append([
                Paragraph(m.get('course_code') or '-', styles['RCellBold']),
                Paragraph(m.get('course_name') or '-', cell_style),
                Paragraph(m.get('program') or '-', cell_style),
                Paragraph(str(m.get('year') or '-'), cell_style),
                Paragraph(m.get('specialization') or '\u2014', cell_style),
                Paragraph(m.get('student_group') or '\u2014', cell_style),
                Paragraph(combined_cell, cell_style),
                Paragraph('Special' if m.get('is_special_intake') else '\u2014', cell_style),
                Paragraph(m.get('lecturer') or 'Unassigned', cell_style),
                Paragraph(str(m.get('students', 0)), cell_style),
            ])
        table = Table(
            rows,
            colWidths=[0.85 * inch, 1.3 * inch, 1.15 * inch, 0.45 * inch, 1.1 * inch, 0.85 * inch, 1.15 * inch, 0.55 * inch, 1.0 * inch, 0.5 * inch],
            repeatRows=1,
        )
        table.setStyle(_standard_table_style())
        elements.append(table)

    return elements


def _small_table_style():
    """Lighter-weight style for the 'already scheduled' reference tables."""
    return TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), REPORT_COLORS['header_light']),
        ('GRID', (0, 0), (-1, -1), 0.4, REPORT_COLORS['grid']),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, REPORT_COLORS['alt_row']]),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ])


def _scheduled_grid_for_program_year(program_id, year, styles):
    """
    'Already scheduled' reference grids for a program + year — one small
    table per day, venues down the left, timeslots across the top.
    """
    qs = Timetable.objects.filter(
        venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
        course_allocation__program_id=program_id,
        course_allocation__program_course__year=year,
    ).select_related('course_allocation', 'course_allocation__lecturer', 'venue')
    return _day_grid_tables(qs, styles, table_style_fn=_small_table_style, rich=False, col_total_width=9.6 * inch)


def _status_row(a, sched_info, styles, merged_note=None):
    """One row combining a course's identity with its scheduling status.

    merged_note, if given (e.g. "Merged — Group GRP-014"), is appended to
    the course name so it's clear this row shares a single combined class
    session with other departments' allocations of the same course — its
    Scheduled/Unscheduled status and Day/Time/Venue reflect that shared
    session, not a slot booked for this row individually.
    """
    if sched_info['scheduled']:
        status = Paragraph("Scheduled", ParagraphStyle(
            'OkCell', parent=styles['RCell'], textColor=REPORT_COLORS['header'], fontName='Helvetica-Bold',
        ))
        day = sched_info['day']
        time_str = f"{sched_info['start_time']}-{sched_info['end_time']}"
        venue = sched_info['venue']
    else:
        status = Paragraph("Unscheduled", ParagraphStyle(
            'NoCell', parent=styles['RCell'], textColor=REPORT_COLORS['warn'], fontName='Helvetica-Bold',
        ))
        day, time_str, venue = '—', '—', '—'
    course_name = a.course_name or '-'
    if merged_note:
        course_name = f"{course_name} <i>({merged_note})</i>"
    return [
        Paragraph(a.course_code, styles['RCellBold']),
        Paragraph(course_name, styles['RCell']),
        Paragraph(getattr(a.lecturer, 'name', 'Unassigned'), styles['RCell']),
        Paragraph(str(a.number_of_students or 0), styles['RCell']),
        status,
        Paragraph(day, styles['RCell']),
        Paragraph(time_str, styles['RCell']),
        Paragraph(venue, styles['RCell']),
    ]


def _maybe_split(allocs, key_fn, label_fn):
    """
    Partition `allocs` by key_fn. If every item shares the same key (no real
    variation — e.g. a program-year with no student groups at all), return
    ONE bucket with an empty label so the caller doesn't print a pointless
    "Group: All Students" heading on every single program. Only produces
    multiple labeled buckets when the underlying data genuinely varies
    (e.g. this program-year really does have Group A and Group B).
    """
    buckets = defaultdict(list)
    for a in allocs:
        buckets[key_fn(a)].append(a)
    if len(buckets) <= 1:
        return [("", allocs)]
    items = [(label_fn(k), v) for k, v in buckets.items()]
    items.sort(key=lambda t: t[0])
    return items


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def export_unscheduled_pdf(request):
    """
    Program scheduling status report, organized as:

      1. Every department → every program → every year in scope (not just
         the ones with gaps — a fully-scheduled program-year still gets
         its own table, so the report is a complete picture, not just a
         to-do list). Each program-year table lists BOTH scheduled and
         unscheduled courses with a Status column (and day/time/venue for
         the scheduled ones).

         Within a program-year, the report automatically breaks out:
           - Semester 1 / Semester 2
           - Special Intake (only if that year actually has one, split
             from Normal Intake)
           - Student Group (only if that year is actually split into
             Group A / Group B / etc. — most program-years aren't, and
             those print as a single ungrouped table)
           - Specialization Stem (only if that semester actually has
             stem-based courses — e.g. Year 3 Sem 1 "AI" vs "Cybersecurity")

      2. MERGED COURSE GROUPS — split into Scheduled / Unscheduled, printed
         after the department/program/year breakdown since a merged group
         spans multiple programs and doesn't belong under any single
         department/program section.

      3. SERVICED COURSES — courses allocated under one department's
         program but actually taught by another department, printed last.
    """
    scope = _resolve_scope(request)
    Model = _scoped_model(scope)

    template_config = _get_template_config()
    styles = _styles()
    ref = template_config.get_reference_number(datetime.now().strftime("%d-%b-%Y").upper())

    scope_qs = _apply_scope_filters(CourseAllocation.objects.all(), request)
    scope_ids = set(scope_qs.values_list('id', flat=True))

    combined_meta = _get_combined_group_meta_map()
    # Non-primary members never get their own Timetable row — the whole
    # group is booked as one shared session on the primary allocation (see
    # _schedule_info_for_allocation). member_to_primary lets each member's
    # row in its OWN department's table resolve that shared schedule
    # instead of always showing "Unscheduled". member_group_code labels
    # those rows so it's clear they're sharing a session with other
    # departments, not booked individually.
    member_to_primary = {}
    member_group_code = {}
    for group in CombinedCourseGroup.objects.prefetch_related('allocations').only(
        'id', 'primary_allocation_id', 'group_code'
    ):
        member_ids = set(group.allocations.values_list('id', flat=True))
        primary_id = group.primary_allocation_id
        if primary_id:
            for mid in member_ids - {primary_id}:
                member_to_primary[mid] = primary_id
                member_group_code[mid] = group.group_code

    tt_cache = {
        tt.course_allocation_id: tt
        for tt in Model.objects.filter(
            venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
        ).select_related('venue')
    }
    stem_names = {
        s.id: f"{s.name} ({s.category.name})"
        for s in SpecializationStem.objects.select_related('category')
    }
    group_names = {g.id: g.name for g in StudentGroup.objects.all()}

    # Same counts the on-screen dashboard cards show for this scope — see
    # _dashboard_scope_counts() docstring for why this must be the one and
    # only place both the page and every PDF get these numbers from.
    dashboard_counts = _dashboard_scope_counts(request)

    elements = []
    _letterhead(
        elements, styles, template_config,
        "PROGRAM SCHEDULING STATUS REPORT",
        _scope_label(request),
        ref=ref,
        date_str=datetime.now().strftime("%d-%b-%Y").upper(),
    )
    elements.append(_dashboard_summary_table(dashboard_counts, styles))
    elements.append(Spacer(1, 10))

    # ── Merged Course Groups computation (rendered further down, just
    #    above the Serviced Courses section — see below) ─────────────────
    groups = (
        CombinedCourseGroup.objects.filter(primary_allocation__isnull=False)
        .prefetch_related('allocations').select_related('primary_allocation')
    )
    scheduled_groups, unscheduled_groups = [], []
    for g in groups:
        member_ids = set(g.allocations.values_list('id', flat=True))
        if not (member_ids & scope_ids):
            continue  # this merged group has no member inside the requested scope
        tt = tt_cache.get(g.primary_allocation_id)
        (scheduled_groups if tt else unscheduled_groups).append((g, tt))

    def _group_table(rows_src, scheduled):
        header = ['Group Code', 'Primary Section', 'Member Sections', 'Programs', 'Total Students']
        if scheduled:
            header += ['Day', 'Time', 'Venue']
        rows = [[Paragraph(h, styles['RHead']) for h in header]]
        for g, tt in rows_src:
            meta = combined_meta.get(g.primary_allocation_id, {
                'group_code': g.group_code, 'member_course_codes': [], 'member_programs': [], 'total_students': 0,
            })
            row = [
                Paragraph(meta['group_code'] or '-', styles['RCellBold']),
                Paragraph(g.primary_allocation.course_code if g.primary_allocation else '-', styles['RCell']),
                Paragraph(', '.join(meta['member_course_codes']), styles['RCell']),
                Paragraph(', '.join(meta['member_programs']), styles['RCell']),
                Paragraph(str(meta['total_students']), styles['RCell']),
            ]
            if scheduled:
                time_str = f"{tt.start_time.strftime('%H:%M') if tt.start_time else ''}-{tt.end_time.strftime('%H:%M') if tt.end_time else ''}"
                row += [
                    Paragraph(tt.day or '-', styles['RCell']),
                    Paragraph(time_str, styles['RCell']),
                    Paragraph(getattr(tt.venue, 'code', '-'), styles['RCell']),
                ]
            rows.append(row)
        widths = [1.0 * inch, 1.0 * inch, 1.9 * inch, 1.9 * inch, 0.8 * inch]
        if scheduled:
            widths += [0.7 * inch, 0.8 * inch, 0.7 * inch]
        table = Table(rows, colWidths=widths, repeatRows=1)
        table.setStyle(_standard_table_style())
        return table

    # ── Every department > program > year — scheduled + unscheduled ─────
    # NOTE: this used to .exclude(id__in={all merged-group member ids}),
    # which pulled every CombinedCourseGroup member out of its own
    # department's table entirely — a department could only see that a
    # course exists at all via the separate MERGED COURSE GROUPS section
    # below, which has no per-department breakdown. Now every row shows
    # here, under its own department/program/year, same as any other
    # course; only its scheduled/unscheduled status and day/time/venue are
    # resolved through the shared group schedule (via member_to_primary)
    # since non-primary members never get their own Timetable row.
    remaining_qs = scope_qs.select_related(
        'department', 'department__faculty', 'program', 'program__department',
        'program__department__faculty', 'lecturer',
        'program_course', 'student_group', 'specialization_stem', 'specialization_stem__category',
    )
    remaining = list(remaining_qs)

    if not remaining:
        elements.append(Paragraph("No courses for this scope.", styles['RCell']))

    by_dept = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    # by_dept[dept_key][program_key][year] -> [allocations]
    dept_objs, program_objs = {}, {}

    for a in remaining:
        # Group under the allocation's own `department` (the explicit
        # "allocating department") whenever it's set — that's the
        # administrative record of who this course is actually allocated
        # to, and must be respected even when it deliberately differs from
        # the program's home department (e.g. COSC 103 allocated under
        # Plant Science even though its program, BSc Agriculture, has its
        # own department — Plant Science IS the allocating department here
        # and must see it on its own program row).
        #
        # Only fall back to the program's home department when `department`
        # itself is left unset — that's the case that was silently landing
        # rows in an "Unspecified Department" bucket instead of under the
        # program's real owner (e.g. COSC 103 under BLIS/Social Sciences
        # with no department set at all). See _apply_scope_filters for the
        # matching fix on the filtering side, which stays regardless of
        # which department ends up chosen here.
        program = a.program
        dept = a.department or (program.department if program else None)
        pc = a.program_course
        year = getattr(pc, 'year', None) or _get_year_value(a) or 'Unspecified'
        dept_key = dept.id if dept else 'none'
        program_key = program.id if program else 'none'
        dept_objs[dept_key] = dept
        program_objs[(dept_key, program_key)] = program
        by_dept[dept_key][program_key][year].append(a)

    dept_order = sorted(
        by_dept.keys(),
        key=lambda k: (
            (dept_objs[k].faculty.name if dept_objs[k] and dept_objs[k].faculty else 'zzz'),
            (dept_objs[k].name if dept_objs[k] else 'Unspecified Department'),
        )
    )

    total_scheduled, total_unscheduled = 0, 0

    for dept_key in dept_order:
        dept = dept_objs[dept_key]
        dept_name = dept.name if dept else 'Unspecified Department'
        faculty_name = dept.faculty.name if dept and dept.faculty else ''
        elements.append(Paragraph(
            f"DEPARTMENT: {dept_name}" + (f"  ({faculty_name})" if faculty_name else ''),
            styles['RSection'],
        ))

        program_map = by_dept[dept_key]
        program_order = sorted(
            program_map.keys(),
            key=lambda k: (program_objs[(dept_key, k)].name if program_objs[(dept_key, k)] else 'zzz')
        )

        for program_key in program_order:
            program = program_objs[(dept_key, program_key)]
            program_name = program.name if program else 'Unspecified Program'
            elements.append(Paragraph(f"Program: {program_name}", styles['RCellBold']))
            elements.append(Spacer(1, 4))

            year_map = program_map[program_key]
            for year in sorted(year_map.keys(), key=lambda y: (isinstance(y, str), y)):
                elements.append(Paragraph(f"Year {year}", styles['RCell']))
                year_allocs = year_map[year]

                # Split by semester first (always — semesters are always meaningful)
                by_semester = defaultdict(list)
                for a in year_allocs:
                    sem = getattr(a.program_course, 'semester', None) or 0
                    by_semester[sem].append(a)

                for semester in sorted(by_semester.keys()):
                    sem_label = SEMESTER_LABELS.get(semester, f"Semester {semester}" if semester else "Semester Unspecified")
                    sem_allocs = by_semester[semester]

                    # Only split by intake if this year actually has a special intake
                    intake_buckets = _maybe_split(
                        sem_allocs,
                        key_fn=lambda a: a.intake or 'normal',
                        label_fn=lambda k: INTAKE_LABELS.get(k, k.title()),
                    )
                    for intake_label, intake_allocs in intake_buckets:

                        # Only split by student group if this year is actually
                        # split into groups (Group A / Group B / ...)
                        group_buckets = _maybe_split(
                            intake_allocs,
                            key_fn=lambda a: a.student_group_id,
                            label_fn=lambda k: group_names.get(k, 'Group') if k else 'All Students (Ungrouped)',
                        )
                        for group_label, group_allocs in group_buckets:

                            # Only split by specialization stem if this
                            # semester actually has stem-based courses
                            stem_buckets = _maybe_split(
                                group_allocs,
                                key_fn=lambda a: a.specialization_stem_id,
                                label_fn=lambda k: stem_names.get(k, 'Core (No Specialization)') if k else 'Core (No Specialization)',
                            )
                            for stem_label, stem_allocs in stem_buckets:
                                title_parts = [sem_label]
                                if intake_label:
                                    title_parts.append(intake_label)
                                if group_label:
                                    title_parts.append(group_label)
                                if stem_label:
                                    title_parts.append(stem_label)
                                elements.append(Paragraph(" — ".join(title_parts), ParagraphStyle(
                                    'SemTitle', parent=styles['RCellBold'], textColor=REPORT_COLORS['header'],
                                )))

                                header = ['Course Code', 'Course Name', 'Lecturer', 'Students', 'Status', 'Day', 'Time', 'Venue']
                                rows = [[Paragraph(h, styles['RHead']) for h in header]]
                                for a in sorted(stem_allocs, key=lambda x: x.course_code):
                                    lookup_id = member_to_primary.get(a.id)
                                    sched_info = _schedule_info_for_allocation(a, tt_cache, lookup_id=lookup_id)
                                    if sched_info['scheduled']:
                                        total_scheduled += 1
                                    else:
                                        total_unscheduled += 1
                                    merged_note = (
                                        f"Merged — Group {member_group_code[a.id]}"
                                        if a.id in member_group_code else None
                                    )
                                    rows.append(_status_row(a, sched_info, styles, merged_note=merged_note))
                                table = Table(
                                    rows,
                                    colWidths=[0.9 * inch, 1.7 * inch, 1.2 * inch, 0.6 * inch, 0.8 * inch, 0.8 * inch, 0.8 * inch, 0.8 * inch],
                                    repeatRows=1,
                                )
                                table.setStyle(_standard_table_style())
                                elements.append(table)
                                elements.append(Spacer(1, 6))

                elements.append(Spacer(1, 12))

        elements.append(Spacer(1, 6))
        elements.append(HRFlowable(width="100%", thickness=0.6, color=REPORT_COLORS['grid'], spaceAfter=8))

    elements.append(Spacer(1, 10))

    # ── Merged Course Groups — rendered here (above Serviced Courses,
    #    below the main department/program/year breakdown) since a merged
    #    group spans multiple programs and doesn't belong under any single
    #    department/program section above. ────────────────────────────────
    elements.append(Spacer(1, 14))
    elements.append(HRFlowable(width="100%", thickness=0.8, color=REPORT_COLORS['grid'], spaceAfter=10))
    elements.append(Paragraph("MERGED COURSE GROUPS", styles['RSection']))

    elements.append(Paragraph("Scheduled", ParagraphStyle('MGSub', parent=styles['RCellBold'], textColor=REPORT_COLORS['header'])))
    if scheduled_groups:
        elements.append(_group_table(scheduled_groups, scheduled=True))
    else:
        elements.append(Paragraph("None.", styles['RCell']))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("Unscheduled", ParagraphStyle('MGSub', parent=styles['RCellBold'], textColor=REPORT_COLORS['warn'])))
    if unscheduled_groups:
        elements.append(_group_table(unscheduled_groups, scheduled=False))
    else:
        elements.append(Paragraph("None.", styles['RCell']))

    _serviced_courses_section(elements, styles, request, tt_cache, only_scheduled=False)

    return _pdf_response(elements, "program_scheduling_status_report.pdf", template_config, ref, landscape_mode=True, compiled_by=_full_name(request))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4b — export: UNSCHEDULED courses ONLY (lean list, no scheduled rows)
# ═══════════════════════════════════════════════════════════════════════════

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def export_unscheduled_only_pdf(request):
    """
    A lean report listing ONLY unscheduled course allocations — unlike
    export_unscheduled_pdf above (which prints every program-year table,
    scheduled AND unscheduled rows together), this is just the gap list:
    one row per course that still has no day/time/venue.

    Respects the same Scope filters (faculty_id/department_id/program_id/
    year) as the other exports — leave Scope on "All" to get every
    unscheduled course in the whole university. No row cap: whatever the
    scope matches, all of it prints.
    """
    exam_or_tt_scope = _resolve_scope(request)
    template_config = _get_template_config()
    styles = _styles()
    ref = template_config.get_reference_number(datetime.now().strftime("%d-%b-%Y").upper())
    date_str = datetime.now().strftime("%d-%b-%Y").upper()

    qs = _apply_scope_filters(_get_unscheduled_allocations_for_scope(exam_or_tt_scope), request)
    qs = qs.select_related(
        'department', 'department__faculty', 'program', 'lecturer', 'program_course',
    ).order_by('department__name', 'program__name', 'course_code')
    allocations = list(qs)

    elements = []
    _letterhead(
        elements, styles, template_config,
        "UNSCHEDULED COURSES REPORT",
        f"{_scope_label(request)} — {len(allocations)} unscheduled course(s)",
        ref=ref, date_str=date_str,
    )

    if not allocations:
        elements.append(Paragraph("No unscheduled courses in this scope.", styles['RCell']))
        return _pdf_response(elements, "unscheduled_only_report.pdf", template_config, ref, compiled_by=_full_name(request))

    grouped = defaultdict(list)
    for a in allocations:
        dept_name = getattr(a.department, 'name', None) or getattr(getattr(a.program, 'department', None), 'name', None) or 'Unknown Department'
        grouped[dept_name].append(a)

    header = ['Course Code', 'Program', 'Year', 'Lecturer', 'Students']
    for dept_name in sorted(grouped.keys()):
        rows_for_dept = grouped[dept_name]
        elements.append(Paragraph(f"{dept_name} ({len(rows_for_dept)})", styles['RSection']))
        rows = [[Paragraph(h, styles['RHead']) for h in header]]
        for a in rows_for_dept:
            rows.append([
                Paragraph(a.course_code or '-', styles['RCellBold']),
                Paragraph(getattr(a.program, 'name', 'N/A'), styles['RCell']),
                Paragraph(str(_get_year_value(a) or '-'), styles['RCell']),
                Paragraph(getattr(a.lecturer, 'name', 'Unassigned'), styles['RCell']),
                Paragraph(str(a.number_of_students or 0), styles['RCell']),
            ])
        table = Table(rows, colWidths=[1.5 * inch, 2.6 * inch, 0.6 * inch, 1.8 * inch, 0.8 * inch], repeatRows=1)
        table.setStyle(_standard_table_style())
        elements.append(table)
        elements.append(Spacer(1, 10))

    return _pdf_response(elements, "unscheduled_only_report.pdf", template_config, ref, compiled_by=_full_name(request))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — export: scheduled timetable for a scope
# ═══════════════════════════════════════════════════════════════════════════

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def export_scheduled_pdf(request):
    """
    Export the SCHEDULED timetable for a scope: whole university (no
    filters), a faculty, a department, a program, or a program + year.

    Layout: one table PER DAY — venues down the left, timeslots across the
    top (each cell showing course code + lecturer), matching the standard
    grid layout used by the official timetable exporter.
    """
    Model = _scoped_model(_resolve_scope(request))

    template_config = _get_template_config()
    styles = _styles()
    ref = template_config.get_reference_number(datetime.now().strftime("%d-%b-%Y").upper())

    qs = Model.objects.filter(
        venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
    ).select_related(
        'course_allocation', 'course_allocation__lecturer',
        'course_allocation__program', 'course_allocation__department', 'venue',
    )
    qs = _apply_scope_filters(qs, request, alloc_field_prefix='course_allocation__')

    # Also needed for the "Serviced Courses" section below, which is scoped
    # by origin_department (a separate CourseAllocation queryset), so it
    # can't just reuse `qs` (which is this report's Timetable queryset).
    tt_cache = {
        tt.course_allocation_id: tt
        for tt in Model.objects.filter(
            venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
        ).select_related('venue')
    }

    elements = []
    _letterhead(
        elements, styles, template_config,
        "SCHEDULED TIMETABLE REPORT",
        _scope_label(request),
        ref=ref,
        date_str=datetime.now().strftime("%d-%b-%Y").upper(),
    )
    dashboard_counts = _dashboard_scope_counts(request)
    elements.append(_dashboard_summary_table(dashboard_counts, styles))
    elements.append(Spacer(1, 10))

    day_tables = _day_grid_tables(qs, styles, table_style_fn=_standard_table_style, rich=True)
    total_rows = qs.count()

    if not day_tables:
        elements.append(Paragraph("No scheduled entries found for this scope.", styles['RCell']))
    else:
        for day_label, day_table in day_tables:
            elements.append(Paragraph(day_label.upper(), styles['RSection']))
            elements.append(day_table)
            elements.append(Spacer(1, 10))

    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"Total scheduled entries: {total_rows}", styles['RCellBold']))

    # Unscheduled courses — allocations within this same scope (faculty/
    # department/program/year, via _apply_scope_filters) that have no
    # complete Timetable entry (missing or incomplete venue/start/end time).
    scope_allocations = _apply_scope_filters(CourseAllocation.objects.all(), request)
    scheduled_alloc_ids = set(qs.values_list('course_allocation_id', flat=True))
    unscheduled_allocations = scope_allocations.exclude(
        id__in=scheduled_alloc_ids
    ).select_related('lecturer', 'program').order_by('course_code')

    if unscheduled_allocations.exists():
        elements.append(Spacer(1, 14))
        elements.append(Paragraph("UNSCHEDULED COURSES", styles['RSection']))

        unsched_rows = [[
            Paragraph('Course Code', styles['RHead']),
            Paragraph('Course Name', styles['RHead']),
            Paragraph('Program', styles['RHead']),
            Paragraph('Lecturer', styles['RHead']),
            Paragraph('Students', styles['RHead']),
        ]]
        for alloc in unscheduled_allocations:
            unsched_rows.append([
                Paragraph(alloc.course_code or '-', styles['RCellBold']),
                Paragraph(alloc.course_name or '-', styles['RCell']),
                Paragraph(getattr(alloc.program, 'name', 'N/A'), styles['RCell']),
                Paragraph(getattr(alloc.lecturer, 'name', 'Unassigned'), styles['RCell']),
                Paragraph(str(alloc.number_of_students or 0), styles['RCell']),
            ])

        unsched_col_widths = [1.1 * inch, 2.8 * inch, 2.0 * inch, 2.0 * inch, 0.9 * inch]
        unsched_table = Table(unsched_rows, colWidths=unsched_col_widths, repeatRows=1)
        unsched_table.setStyle(_standard_table_style())
        elements.append(unsched_table)
        elements.append(Spacer(1, 6))
        elements.append(Paragraph(
            f"Total unscheduled courses: {unscheduled_allocations.count()}",
            styles['RCellBold'],
        ))

    _serviced_courses_section(elements, styles, request, tt_cache, only_scheduled=True)

    return _pdf_response(elements, "scheduled_timetable_report.pdf", template_config, ref, landscape_mode=True, compiled_by=_full_name(request))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — course lookup: on-screen JSON query + PDF export
#              (accepts multiple course codes at once)
# ═══════════════════════════════════════════════════════════════════════════

def _combined_group_department_map():
    """
    {primary_allocation_id: {'combined_by_department': ..., 'allocating_department': ...}}
    for every CombinedCourseGroup with a primary_allocation set.

    Kept local to analysis_reports.py (not folded into
    timetable_panel._get_combined_group_meta_map) so the search results here
    can show which department combined a group without touching that shared
    helper's output for its other callers.

    'combined_by_department' is the COD who first combined the group
    (origin_department, fixed even after it's submitted elsewhere for
    lecturer allocation); 'allocating_department' is whoever is currently
    responsible for assigning it a lecturer (department) — the two differ
    once a combined group has been submitted to another department.
    """
    dept_map = {}
    for group in (
        CombinedCourseGroup.objects
        .filter(primary_allocation__isnull=False)
        .select_related('department', 'origin_department')
        .only('primary_allocation_id', 'department__name', 'origin_department__name')
    ):
        origin_dept = group.origin_department or group.department
        dept_map[group.primary_allocation_id] = {
            'combined_by_department': origin_dept.name if origin_dept else '',
            'allocating_department': group.department.name if group.department else '',
        }
    return dept_map


def _build_course_lookup_results(codes, model=Timetable):
    """
    For each requested search term — a course code OR any free-text term
    (lecturer, program, department, faculty, venue, specialization, student
    group, day) — expand to the full CombinedCourseGroup-aware allocation
    set and resolve each allocation's schedule.

    `model` picks which table ("Timetable" or "ExamTimetable") the schedule
    is resolved against — pass the scoped model so an exam-scope search
    reports exam slots instead of regular-timetable ones.
    Returns: {term: {'matches': [...], 'not_found': bool}}
    """
    tt_cache = {
        tt.course_allocation_id: tt
        for tt in model.objects.filter(
            venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
        ).select_related('venue')
    }
    combined_meta = _get_combined_group_meta_map()
    dept_map = _combined_group_department_map()

    results = {}
    for raw_code in codes:
        allocs = _expand_term_to_allocations(raw_code)
        matches = []
        for a in allocs:
            sched = _schedule_info_for_allocation(a, tt_cache, model=model)
            if model is ExamTimetable:
                tt_row = tt_cache.get(a.id)
                sched = {**sched, 'date': tt_row.date.isoformat() if tt_row and tt_row.date else ''}
            meta = combined_meta.get(a.id)
            if meta:
                meta = {**meta, **dept_map.get(a.id, {})}
            matches.append({
                'allocation_id': a.id,
                'course_code': a.course_code,
                'course_name': a.course_name,
                'department': getattr(a.department, 'name', 'N/A'),
                'faculty': getattr(getattr(a.department, 'faculty', None), 'name', 'N/A'),
                'program': getattr(a.program, 'name', 'N/A'),
                'year': _get_year_value(a),
                'lecturer': getattr(a.lecturer, 'name', 'Unassigned'),
                'students': a.number_of_students or 0,
                'is_primary_of_merge': bool(meta),
                'merged_group': meta,
                'is_special_intake': a.is_special_intake,
                'specialization': _specialization_label(a),
                'student_group': _student_group_label(a),
                **sched,
            })
        # Sort so the primary/scheduled entries surface first, then by program name
        matches.sort(key=lambda m: (not m['scheduled'], m['program'] or ''))
        results[raw_code] = {
            'matches': matches,
            'not_found': len(matches) == 0,
        }
    return results


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def query_course_schedule_api(request):
    """
    JSON: 'when/where is this course scheduled' — universal, supports
    multiple terms in one call, and each term can be a course code OR any
    free-text term (lecturer, program, department, faculty, venue,
    specialization, student group, day).
    GET ?code=COSC 101&code=Mwangi  or  ?codes=COSC 101, Mwangi, BBIT
    """
    codes = _parse_codes_param(request)
    if not codes:
        return JsonResponse({'status': 'error', 'message': 'Provide at least one search term via ?code= or ?codes='}, status=400)

    scope = _resolve_scope(request)
    results = _build_course_lookup_results(codes, model=_scoped_model(scope))
    return JsonResponse({'status': 'success', 'exam_or_timetable': scope, 'results': results})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def export_course_schedule_pdf(request):
    """
    PDF export for one or more search terms — each term can be a course
    code OR any free-text term (lecturer, program, department, faculty,
    venue, specialization, student group, day). One section per requested
    term, listing every allocation it expands to (including
    CombinedCourseGroup siblings) and its schedule.
    """
    codes = _parse_codes_param(request)
    template_config = _get_template_config()
    styles = _styles()
    ref = template_config.get_reference_number(datetime.now().strftime("%d-%b-%Y").upper())

    date_str = datetime.now().strftime("%d-%b-%Y").upper()

    elements = []
    if not codes:
        _letterhead(elements, styles, template_config, "COURSE SCHEDULE REPORT",
                     ref=ref, date_str=date_str)
        elements.append(Paragraph("No search terms were supplied.", styles['RCell']))
        return _pdf_response(elements, "course_schedule_report.pdf", template_config, ref, compiled_by=_full_name(request))

    _letterhead(
        elements, styles, template_config,
        "COURSE SCHEDULE REPORT",
        f"Searched: {', '.join(codes)}",
        ref=ref,
        date_str=date_str,
    )

    results = _build_course_lookup_results(codes, model=_scoped_model(_resolve_scope(request)))

    for raw_code, data in results.items():
        elements.append(Paragraph(raw_code.upper(), styles['RSection']))

        if data['not_found']:
            elements.append(Paragraph("No matching course allocation found.", styles['RCell']))
            elements.append(Spacer(1, 8))
            continue

        # If every match belongs to the same CombinedCourseGroup, call that out.
        merged_groups = {m['merged_group']['group_id'] for m in data['matches'] if m['merged_group']}
        if len(merged_groups) == 1 and len(data['matches']) > 1:
            g = next(m['merged_group'] for m in data['matches'] if m['merged_group'])
            dept_note = f" — combined by {g['combined_by_department']}" if g.get('combined_by_department') else ""
            elements.append(Paragraph(
                f"Merged group: {g['group_code']} — {g['member_count']} sections taught together "
                f"as ONE class ({g['total_students']} students total){dept_note}.",
                styles['RCell'],
            ))
            elements.append(Spacer(1, 4))

        elements.extend(_build_match_matrix_elements(data['matches'], styles, {'show_program': True}))
        elements.append(Spacer(1, 10))

    filename = "course_schedule_report.pdf" if len(codes) > 1 else f"{canonical_course_key(codes[0])}_schedule.pdf"
    return _pdf_response(elements, filename, template_config, ref, landscape_mode=True, compiled_by=_full_name(request))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6a — export: ALL courses, venue-allocation summary
# ═══════════════════════════════════════════════════════════════════════════
# One row per course code (e.g. "COSC 203") covering the WHOLE course list —
# not a search box, every course that has at least one CourseAllocation row.
# For each code we report:
#   - total sections (every CourseAllocation row that canonically matches,
#     across every program/year it's taught in)
#   - how many of those are scheduled vs still unscheduled (unscheduled ones
#     still count towards the total — they just can't have a venue yet)
#   - of the scheduled ones, how many sit in a venue that's ALSO used by at
#     least one other course code somewhere on the timetable ("shared"),
#     vs. how many have a venue used by nobody but this course ("standalone")

def _global_course_scheduling_maps(model=Timetable):
    """
    University-wide (unscoped) lookup maps used to judge venue-sharing
    correctly for the "All Courses" summary. This has to stay unscoped even
    when the report itself is filtered to one faculty/department/program —
    a course in scope can share a room with a course that belongs to a
    completely different department, and that fact would be invisible if
    we only looked at the scoped queryset.

    `model` is Timetable or ExamTimetable depending on the page's scope —
    "shared venue" only means something within one table; a lecture room
    booked for a regular class Monday 9am and an exam Monday 9am aren't
    the same booking.

    Returns:
      tt_cache: {course_allocation_id (primary or standalone): row from `model`}
      member_to_primary: {non-primary CombinedCourseGroup member id: primary id}
      venue_to_keys: {venue_id: set of canonical course keys booked there}
    """
    tt_cache = {
        tt.course_allocation_id: tt
        for tt in model.objects.filter(
            venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
        ).select_related('venue')
    }

    member_to_primary = {}
    groups = CombinedCourseGroup.objects.filter(
        primary_allocation__isnull=False
    ).prefetch_related('allocations')
    for group in groups:
        primary_id = group.primary_allocation_id
        for member in group.allocations.all():
            if member.id != primary_id:
                member_to_primary[member.id] = primary_id

    venue_to_keys = defaultdict(set)
    for alloc_id, code in CourseAllocation.objects.values_list('id', 'course_code'):
        lookup_id = member_to_primary.get(alloc_id, alloc_id)
        tt = tt_cache.get(lookup_id)
        if not tt:
            continue
        base, _ = strip_group_suffix(code or "")
        key = canonical_course_key(base)
        if key:
            venue_to_keys[tt.venue_id].add(key)

    return tt_cache, member_to_primary, venue_to_keys


def _build_all_courses_venue_summary(request):
    """
    Group every CourseAllocation in scope by canonical course code and
    return one summary dict per code, sorted alphabetically by code.
    """
    Model = _scoped_model(_resolve_scope(request))
    tt_cache, member_to_primary, venue_to_keys = _global_course_scheduling_maps(model=Model)

    qs = _apply_scope_filters(CourseAllocation.objects.all(), request).select_related(
        'department', 'department__faculty', 'program', 'program__department',
    )

    grouped = defaultdict(list)
    display_code = {}
    for a in qs:
        base, _suffix = strip_group_suffix(a.course_code or "")
        key = canonical_course_key(base)
        if not key:
            continue
        grouped[key].append(a)
        # Prefer the shortest/plainest-looking raw code seen for display —
        # keeps "COSC 203" rather than an odd per-section variant.
        norm = normalize_code(base)
        if key not in display_code or len(norm) < len(display_code[key]):
            display_code[key] = norm

    rows = []
    for key, allocs in grouped.items():
        total = len(allocs)
        scheduled = 0
        shared = 0
        standalone = 0
        shared_with = set()
        venues_used = set()

        for a in allocs:
            lookup_id = member_to_primary.get(a.id, a.id)
            tt = tt_cache.get(lookup_id)
            if not tt:
                continue  # unscheduled — already reflected in total - scheduled
            scheduled += 1
            venues_used.add(tt.venue.code if tt.venue else '—')
            other_keys = venue_to_keys.get(tt.venue_id, set()) - {key}
            if other_keys:
                shared += 1
                shared_with |= other_keys
            else:
                standalone += 1

        rows.append({
            'code': display_code[key],
            'total_sections': total,
            'scheduled': scheduled,
            'unscheduled': total - scheduled,
            'shared_venue': shared,
            'standalone_venue': standalone,
            'venues_used': sorted(venues_used),
            'shared_with_codes': sorted(shared_with),
        })

    rows.sort(key=lambda r: r['code'])
    return rows


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def export_all_courses_venue_summary_pdf(request):
    """
    PDF export: every course code in scope, one row each, stating how many
    sections it has in course allocation, how many are scheduled vs still
    unscheduled, and — of the scheduled ones — how many sit in a venue
    shared with another course code vs. how many have that venue to
    themselves.
    """
    template_config = _get_template_config()
    styles = _styles()
    ref = template_config.get_reference_number(datetime.now().strftime("%d-%b-%Y").upper())
    date_str = datetime.now().strftime("%d-%b-%Y").upper()

    rows = _build_all_courses_venue_summary(request)

    elements = []
    _letterhead(
        elements, styles, template_config,
        "ALL COURSES — VENUE ALLOCATION SUMMARY",
        f"{_scope_label(request)} — {len(rows)} course code(s)",
        ref=ref, date_str=date_str,
    )
    elements.append(Paragraph(
        "\"Shared Venue\" = sections whose room is also booked for at least one other course code "
        "anywhere on the timetable. \"Standalone Venue\" = sections whose room is booked for this "
        "course code only. Unscheduled sections still count toward the total but have no venue yet.",
        styles['RCell'],
    ))
    elements.append(Spacer(1, 8))

    if not rows:
        elements.append(Paragraph("No courses found in this scope.", styles['RCell']))
        return _pdf_response(elements, "all_courses_venue_summary.pdf", template_config, ref,
                              compiled_by=_full_name(request))

    header = ['Course Code', 'Sections (Total)', 'Scheduled', 'Unscheduled', 'Shared Venue', 'Standalone Venue']
    table_rows = [[Paragraph(h, styles['RHead']) for h in header]]
    for r in rows:
        table_rows.append([
            Paragraph(r['code'], styles['RCellBold']),
            Paragraph(str(r['total_sections']), styles['RCell']),
            Paragraph(str(r['scheduled']), styles['RCell']),
            Paragraph(str(r['unscheduled']), styles['RCell']),
            Paragraph(str(r['shared_venue']), styles['RCell']),
            Paragraph(str(r['standalone_venue']), styles['RCell']),
        ])
    table = Table(
        table_rows,
        colWidths=[1.6 * inch, 1.3 * inch, 1.0 * inch, 1.1 * inch, 1.1 * inch, 1.3 * inch],
        repeatRows=1,
    )
    table.setStyle(_standard_table_style())
    elements.append(table)
    elements.append(Spacer(1, 10))

    # Detail note for any course whose scheduled sections share a room with
    # another course code — lists which other codes and which venues.
    shared_rows = [r for r in rows if r['shared_venue'] > 0]
    if shared_rows:
        elements.append(Paragraph("Shared-Venue Detail", styles['RSection']))
        for r in shared_rows:
            elements.append(Paragraph(
                f"{r['code']}: {r['shared_venue']} of {r['scheduled']} scheduled section(s) share a room "
                f"with {', '.join(r['shared_with_codes']) if r['shared_with_codes'] else 'another course'} "
                f"(venue(s): {', '.join(r['venues_used']) if r['venues_used'] else '—'}).",
                styles['RCell'],
            ))
        elements.append(Spacer(1, 8))

    return _pdf_response(elements, "all_courses_venue_summary.pdf", template_config, ref,
                          compiled_by=_full_name(request))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6a-2 — export: Blocked & Designated Venues report
# ═══════════════════════════════════════════════════════════════════════════
# Not scoped by the faculty/department/program/year Scope panel — venue
# blocks and specializations are properties of the ROOM, not of a course's
# curriculum scope, so this always prints the full, university-wide list.

def _render_blocked_venues_section(elements, styles, blocked_rows):
    """
    Shared renderer for the "Blocked Venues" table — used by both the
    Blocked & Designated Venues report and the All Venues report, so the
    two never drift out of sync on formatting.
    """
    elements.append(Paragraph("Blocked Venues", styles['RSection']))
    elements.append(Paragraph(
        "Rooms hidden from the autoscheduler entirely — never offered to any course, "
        "regardless of specialization rules.",
        styles['RCell'],
    ))
    elements.append(Spacer(1, 6))
    if not blocked_rows:
        elements.append(Paragraph("No blocked venues.", styles['RCell']))
    else:
        header = ['Venue', 'Building', 'Capacity', 'Reason', 'Status', 'Since']
        rows = [[Paragraph(h, styles['RHead']) for h in header]]
        for b in blocked_rows:
            status_style = styles['RCell'] if b['active'] else ParagraphStyle(
                'InactiveCell', parent=styles['RCell'], textColor=colors.grey,
            )
            rows.append([
                Paragraph(b['venue_code'], styles['RCellBold']),
                Paragraph(b['building'], styles['RCell']),
                Paragraph(str(b['capacity']), styles['RCell']),
                Paragraph(b['reason'], styles['RCell']),
                Paragraph('Active' if b['active'] else 'Inactive', status_style),
                Paragraph(b['since'], styles['RCell']),
            ])
        table = Table(rows, colWidths=[1.1 * inch, 1.5 * inch, 0.9 * inch, 2.2 * inch, 0.8 * inch, 1.0 * inch], repeatRows=1)
        table.setStyle(_standard_table_style())
        elements.append(table)
    elements.append(Spacer(1, 14))


def _build_blocked_designated_venues_report():
    """
    Returns (blocked_rows, designated_rows):
      blocked_rows: one dict per VenueBlock, active or not
      designated_rows: one dict per VenueSpecialization rule, with its
        resolved venues and the full set of course codes it designates
    """
    blocked_rows = []
    for b in VenueBlock.objects.select_related('venue', 'venue__building').order_by('venue__code'):
        blocked_rows.append({
            'venue_code': b.venue.code if b.venue else '—',
            'building': getattr(b.venue.building, 'name', '') if b.venue and b.venue.building else '—',
            'capacity': b.venue.capacity if b.venue and b.venue.capacity is not None else '—',
            'reason': b.reason or '—',
            'active': b.is_active,
            'since': b.created_at.strftime('%d-%b-%Y') if b.created_at else '—',
        })

    designated_rows = []
    specs = (
        VenueSpecialization.objects
        .prefetch_related('venues', 'programs', 'departments', 'courses')
        .order_by('priority', 'name')
    )
    for s in specs:
        codes = sorted(s.get_designated_course_codes())
        designated_rows.append({
            'name': s.name,
            'venues': sorted(v.code for v in s.venues.all()),
            'scope': s.get_scope_display() if hasattr(s, 'get_scope_display') else s.scope,
            'strict': s.strict,
            'exclusive': s.exclusive,
            'priority': s.priority,
            'active': s.is_active,
            'course_count': len(codes),
            'courses': codes,
            # ── Rule targeting detail — WHY these courses are designated ──
            'departments': sorted(d.name for d in s.departments.all()),
            'programs': sorted(p.name for p in s.programs.all()),
            'individual_courses': sorted(
                f"{c.course_code}" for c in s.courses.all()
            ),
            'notes': (s.notes or '').strip(),
        })

    return blocked_rows, designated_rows


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def export_blocked_designated_venues_pdf(request):
    """
    PDF export of every VenueBlock (rooms hidden from the autoscheduler
    entirely) and every VenueSpecialization rule (rooms reserved/preferred
    for a designated set of courses, programs, or departments), including
    the full designated course-code list per rule.
    """
    template_config = _get_template_config()
    styles = _styles()
    ref = template_config.get_reference_number(datetime.now().strftime("%d-%b-%Y").upper())
    date_str = datetime.now().strftime("%d-%b-%Y").upper()

    blocked_rows, designated_rows = _build_blocked_designated_venues_report()

    elements = []
    _letterhead(
        elements, styles, template_config,
        "BLOCKED & DESIGNATED VENUES REPORT",
        f"{len(blocked_rows)} blocked venue(s) — {len(designated_rows)} designation rule(s)",
        ref=ref, date_str=date_str,
    )

    # ── Blocked venues ──────────────────────────────────────────────────
    _render_blocked_venues_section(elements, styles, blocked_rows)

    # ── Designated venues (VenueSpecialization rules) ──────────────────
    elements.append(Paragraph("Designated Venues (Specialization Rules)", styles['RSection']))
    elements.append(Paragraph(
        "Rooms reserved or preferred for a designated set of courses/programs/departments. "
        "STRICT means the designated courses must be placed in one of these venues or are marked "
        "unschedulable. EXCLUSIVE means no other course may ever use the venue, even on free slots.",
        styles['RCell'],
    ))
    elements.append(Spacer(1, 6))
    if not designated_rows:
        elements.append(Paragraph("No venue specialization rules.", styles['RCell']))
    else:
        for s in designated_rows:
            flags = []
            if s['strict']:
                flags.append('STRICT')
            if s['exclusive']:
                flags.append('EXCLUSIVE')
            if not s['active']:
                flags.append('INACTIVE')
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            elements.append(Paragraph(
                f"{s['name']}{flag_str} — venues: {', '.join(s['venues']) if s['venues'] else '—'} "
                f"— scope: {s['scope']} — priority: {s['priority']} — {s['course_count']} designated course(s)",
                styles['RCellBold'],
            ))

            # ── Rule detail — what actually drives this designation ─────
            rule_bits = []
            if s['departments']:
                rule_bits.append(f"Department(s): {', '.join(s['departments'])}")
            if s['programs']:
                rule_bits.append(f"Program(s): {', '.join(s['programs'])}")
            if s['individual_courses']:
                rule_bits.append(f"Individual course(s): {', '.join(s['individual_courses'])}")
            if rule_bits:
                elements.append(Paragraph(
                    "Rule targets — " + " | ".join(rule_bits),
                    styles['RCell'],
                ))
            if s['notes']:
                elements.append(Paragraph(f"Notes: {s['notes']}", styles['RCell']))

            if s['courses']:
                elements.append(Paragraph(', '.join(s['courses']), styles['RCell']))
            else:
                elements.append(Paragraph("No course codes resolved for this rule.", styles['RCell']))
            elements.append(Spacer(1, 8))

    return _pdf_response(elements, "blocked_designated_venues_report.pdf", template_config, ref,
                          compiled_by=_full_name(request))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6a-2b — export: All Venues report (every venue, then blocked below)
# ═══════════════════════════════════════════════════════════════════════════
# Same "not scope-filtered" reasoning as the Blocked & Designated report —
# venues are properties of the room, not of a course's curriculum scope, so
# this always prints the full, university-wide list.

def _build_all_venues_report():
    """
    Returns (all_rows, blocked_rows):
      all_rows: one dict per Venue that exists in the system at all
      blocked_rows: identical shape to _build_blocked_designated_venues_report's
        blocked_rows, reused so the "Blocked Venues" section below stays in
        lockstep with the dedicated Blocked & Designated report.
    """
    blocked_venue_ids = set(
        VenueBlock.objects.filter(is_active=True).values_list('venue_id', flat=True)
    )

    all_rows = []
    for v in Venue.objects.select_related('building').order_by('code'):
        all_rows.append({
            'venue_code': v.code,
            'building': getattr(v.building, 'name', '') if v.building else '—',
            'capacity': v.capacity if v.capacity is not None else '—',
            'exam_capacity': v.exam_capacity if v.exam_capacity is not None else '—',
            'workshop': v.is_workshop,
            'specialized': v.is_specialized,
            'blocked': v.id in blocked_venue_ids,
        })

    blocked_rows, _designated_rows = _build_blocked_designated_venues_report()
    return all_rows, blocked_rows


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def export_all_venues_pdf(request):
    """
    PDF export: every Venue in the system (code, building, capacity, exam
    capacity, workshop/specialized flags, and whether it's currently
    blocked), followed underneath by the dedicated Blocked Venues table.
    """
    template_config = _get_template_config()
    styles = _styles()
    ref = template_config.get_reference_number(datetime.now().strftime("%d-%b-%Y").upper())
    date_str = datetime.now().strftime("%d-%b-%Y").upper()

    all_rows, blocked_rows = _build_all_venues_report()

    elements = []
    _letterhead(
        elements, styles, template_config,
        "ALL VENUES REPORT",
        f"{len(all_rows)} venue(s) — {len(blocked_rows)} blocked",
        ref=ref, date_str=date_str,
    )

    # ── All venues ────────────────────────────────────────────────────
    elements.append(Paragraph("All Venues", styles['RSection']))
    elements.append(Paragraph(
        "Every room registered in the system, regardless of whether it's currently "
        "blocked, specialized, or free for general use.",
        styles['RCell'],
    ))
    elements.append(Spacer(1, 6))
    if not all_rows:
        elements.append(Paragraph("No venues found.", styles['RCell']))
    else:
        header = ['Venue', 'Building', 'Capacity', 'Exam Cap.', 'Workshop', 'Specialized', 'Blocked']
        rows = [[Paragraph(h, styles['RHead']) for h in header]]
        for v in all_rows:
            blocked_style = ParagraphStyle(
                'BlockedCell', parent=styles['RCellBold'],
            ) if v['blocked'] else styles['RCell']
            rows.append([
                Paragraph(v['venue_code'], styles['RCellBold']),
                Paragraph(v['building'], styles['RCell']),
                Paragraph(str(v['capacity']), styles['RCell']),
                Paragraph(str(v['exam_capacity']), styles['RCell']),
                Paragraph('Yes' if v['workshop'] else '—', styles['RCell']),
                Paragraph('Yes' if v['specialized'] else '—', styles['RCell']),
                Paragraph('Yes' if v['blocked'] else '—', blocked_style),
            ])
        table = Table(
            rows,
            colWidths=[1.0 * inch, 1.4 * inch, 0.8 * inch, 0.8 * inch, 0.8 * inch, 0.9 * inch, 0.7 * inch],
            repeatRows=1,
        )
        table.setStyle(_standard_table_style())
        elements.append(table)
    elements.append(Spacer(1, 14))

    # ── Blocked venues, below the full list ─────────────────────────────
    _render_blocked_venues_section(elements, styles, blocked_rows)

    return _pdf_response(elements, "all_venues_report.pdf", template_config, ref,
                          compiled_by=_full_name(request))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6a-2c — export: Used Rooms report
# ═══════════════════════════════════════════════════════════════════════════
# Unlike the two reports above, this one respects the Scope filters (it's a
# reporting view of the live timetable, not a property of the room itself).

def _build_used_rooms_report(request):
    """
    Group every scheduled Timetable entry in scope by venue and return one
    row per venue actually in use, with the distinct course codes and
    section counts booked into it.
    """
    Model = _scoped_model(_resolve_scope(request))
    qs = _apply_scope_filters(
        Model.objects.filter(
            venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
        ),
        request, alloc_field_prefix='course_allocation__',
    ).select_related('venue', 'venue__building', 'course_allocation')

    venue_map = {}
    for tt in qs:
        v = tt.venue
        if not v:
            continue
        entry = venue_map.setdefault(v.id, {
            'venue_code': v.code,
            'building': getattr(v.building, 'name', '') if v.building else '—',
            'capacity': v.capacity if v.capacity is not None else '—',
            'sections': 0,
            'codes': set(),
        })
        entry['sections'] += 1
        code = tt.course_allocation.course_code if tt.course_allocation_id else None
        if code:
            base, _suffix = strip_group_suffix(code)
            entry['codes'].add(normalize_code(base))

    rows = []
    for entry in venue_map.values():
        codes = sorted(entry['codes'])
        rows.append({
            'venue_code': entry['venue_code'],
            'building': entry['building'],
            'capacity': entry['capacity'],
            'sections': entry['sections'],
            'course_count': len(codes),
            'codes': codes,
        })
    rows.sort(key=lambda r: r['venue_code'])
    return rows


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def export_used_rooms_pdf(request):
    """
    PDF export: every venue that's actually occupied by at least one
    scheduled section (in scope), with how many sections and which course
    codes are booked into it. This is the complement of the Blocked &
    Designated report — "what's in play right now" rather than "what's
    off-limits or reserved".
    """
    template_config = _get_template_config()
    styles = _styles()
    ref = template_config.get_reference_number(datetime.now().strftime("%d-%b-%Y").upper())
    date_str = datetime.now().strftime("%d-%b-%Y").upper()

    rows = _build_used_rooms_report(request)

    elements = []
    _letterhead(
        elements, styles, template_config,
        "USED ROOMS REPORT",
        f"{_scope_label(request)} — {len(rows)} venue(s) in use",
        ref=ref, date_str=date_str,
    )
    elements.append(Paragraph(
        "Every venue with at least one scheduled section in the current scope, "
        "how many sections it's booked for, and which course codes occupy it.",
        styles['RCell'],
    ))
    elements.append(Spacer(1, 8))

    if not rows:
        elements.append(Paragraph("No venues are in use for this scope.", styles['RCell']))
        return _pdf_response(elements, "used_rooms_report.pdf", template_config, ref,
                              compiled_by=_full_name(request))

    header = ['Venue', 'Building', 'Capacity', 'Sections Booked', 'Course Codes']
    table_rows = [[Paragraph(h, styles['RHead']) for h in header]]
    for r in rows:
        table_rows.append([
            Paragraph(r['venue_code'], styles['RCellBold']),
            Paragraph(r['building'], styles['RCell']),
            Paragraph(str(r['capacity']), styles['RCell']),
            Paragraph(str(r['sections']), styles['RCell']),
            Paragraph(', '.join(r['codes']) if r['codes'] else '—', styles['RCell']),
        ])
    table = Table(
        table_rows,
        colWidths=[1.0 * inch, 1.4 * inch, 0.8 * inch, 1.1 * inch, 2.4 * inch],
        repeatRows=1,
    )
    table.setStyle(_standard_table_style())
    elements.append(table)

    return _pdf_response(elements, "used_rooms_report.pdf", template_config, ref,
                          compiled_by=_full_name(request))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6a-3 — export: Venue Capacity & Availability Report
# ═══════════════════════════════════════════════════════════════════════════
# Answers three questions in one report:
#   1. Which venues are actually available to the scheduler right now
#      (every Venue minus anything under an active VenueBlock)?
#   2. How many venues does the busiest timeslot actually need — i.e. what
#      is the minimum number of venues required so that no two classes are
#      ever forced into the same room at the same time (a scheduling
#      collision)?
#   3. Of the classes in that busiest slot (and every slot), does each one
#      fit the capacity of the venue it's been assigned — flagging any
#      class whose student count overflows its venue (a capacity
#      collision, distinct from a room double-booking)?
# Respects the Scope filters (faculty/department/program/year) exactly like
# "Export Scheduled Timetable" — leave everything on "All" for the
# university-wide picture.

def _build_venue_availability_rows():
    """Every Venue NOT hidden by an active VenueBlock, sorted by capacity
    descending (unknown capacity sorts last)."""
    blocked_venue_ids = set(
        VenueBlock.objects.filter(is_active=True).values_list('venue_id', flat=True)
    )
    rows = []
    for v in Venue.objects.select_related('building').all():
        if v.id in blocked_venue_ids:
            continue
        rows.append({
            'code': v.code,
            'building': getattr(v.building, 'name', '') or '—',
            'capacity': v.capacity if v.capacity is not None else 0,
            'capacity_display': v.capacity if v.capacity is not None else 'Unknown',
        })
    rows.sort(key=lambda r: r['capacity'], reverse=True)
    return rows


def _build_venue_demand_by_slot(request):
    """
    Groups every scheduled Timetable entry (in scope) by (day, start_time,
    end_time). Each Timetable row already represents ONE physical room
    booking — a Combined Course Group shares a single Timetable row on its
    primary_allocation — so len(entries in a slot) IS the number of venues
    that slot genuinely needs simultaneously.

    Returns a list of slot dicts sorted by (day in DAY_ORDER, start_time):
      {day, start_time, end_time, venues_needed, classes: [
          {course_code, program, students_needed, venue_code, venue_capacity,
           over_capacity (bool)}
      ]}
    """
    Model = _scoped_model(_resolve_scope(request))
    qs = Model.objects.select_related(
        'venue', 'course_allocation', 'course_allocation__program',
    ).filter(venue__isnull=False, start_time__isnull=False, end_time__isnull=False)
    qs = _apply_scope_filters(qs, request, alloc_field_prefix='course_allocation__')

    combined_meta = _get_combined_group_meta_map()

    by_slot = defaultdict(list)
    for tt in qs:
        alloc = tt.course_allocation
        meta = combined_meta.get(alloc.id)
        students_needed = meta['total_students'] if meta else (alloc.number_of_students or 0)
        course_label = meta['group_code'] if meta else alloc.course_code
        venue_cap = tt.venue.capacity if tt.venue.capacity is not None else None
        by_slot[(tt.day, tt.start_time, tt.end_time)].append({
            'course_code': course_label,
            'program': alloc.program.name if alloc.program else '',
            'students_needed': students_needed,
            'venue_code': tt.venue.code,
            'venue_capacity': venue_cap,
            'over_capacity': venue_cap is not None and students_needed > venue_cap,
        })

    slots = []
    for (day, start, end), classes in by_slot.items():
        classes.sort(key=lambda c: c['students_needed'], reverse=True)
        slots.append({
            'day': day, 'start_time': start, 'end_time': end,
            'venues_needed': len(classes), 'classes': classes,
        })

    def _day_key(d):
        return DAY_ORDER.index(d) if d in DAY_ORDER else len(DAY_ORDER)

    slots.sort(key=lambda s: (_day_key(s['day']), s['start_time']))
    return slots


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def export_venue_capacity_report_pdf(request):
    """
    PDF: available venues + the minimum number of venues the busiest
    timeslot needs to avoid a double-booking, plus a capacity-fit check
    for every slot's classes against their assigned venues.
    """
    template_config = _get_template_config()
    styles = _styles()
    ref = template_config.get_reference_number(datetime.now().strftime("%d-%b-%Y").upper())
    date_str = datetime.now().strftime("%d-%b-%Y").upper()

    venue_rows = _build_venue_availability_rows()
    available_count = len(venue_rows)
    slots = _build_venue_demand_by_slot(request)

    peak_needed = max((s['venues_needed'] for s in slots), default=0)
    peak_slots = [s for s in slots if s['venues_needed'] == peak_needed] if peak_needed else []
    shortfall = max(0, peak_needed - available_count)

    largest_class = 0
    overflow_count = 0
    for s in slots:
        for c in s['classes']:
            largest_class = max(largest_class, c['students_needed'])
            if c['over_capacity']:
                overflow_count += 1

    elements = []
    _letterhead(
        elements, styles, template_config,
        "VENUE CAPACITY & AVAILABILITY REPORT",
        _scope_label(request),
        ref=ref, date_str=date_str,
    )

    # ── Peak demand summary ─────────────────────────────────────────────
    elements.append(Paragraph("Peak Demand Summary", styles['RSection']))
    stat_labels = ['Available\nVenues', 'Min. Venues Needed\n(Busiest Slot)', 'Largest Class\n(Students)', 'Venue Shortfall']
    stat_values = [str(available_count), str(peak_needed), str(largest_class), str(shortfall)]
    header_row = [Paragraph(l.replace('\n', '<br/>'), styles['RStatLabel']) for l in stat_labels]
    value_row = []
    for v, is_shortfall in zip(stat_values, [False, False, False, True]):
        style = styles['RStatValue']
        if is_shortfall and shortfall > 0:
            style = ParagraphStyle('RStatValueWarn', parent=styles['RStatValue'], textColor=REPORT_COLORS['warn'])
        value_row.append(Paragraph(v, style))
    stat_table = Table([header_row, value_row], colWidths=[1.7 * inch] * 4)
    stat_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), REPORT_COLORS['header']),
        ('BACKGROUND', (0, 1), (-1, 1), REPORT_COLORS['alt_row']),
        ('GRID', (0, 0), (-1, -1), 0.5, REPORT_COLORS['grid']),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    elements.append(stat_table)
    elements.append(Spacer(1, 4))
    if shortfall > 0:
        busiest = ", ".join(
            f"{s['day']} {s['start_time'].strftime('%H:%M')}-{s['end_time'].strftime('%H:%M')}" for s in peak_slots
        )
        elements.append(Paragraph(
            f"WARNING: {available_count} venue(s) are available but the busiest slot ({busiest}) needs "
            f"{peak_needed} simultaneously — {shortfall} short. At least {peak_needed} adequately-sized "
            f"venues are required in total to guarantee no double-booking.",
            ParagraphStyle('WarnNote', parent=styles['RCell'], textColor=REPORT_COLORS['warn'], fontName='Helvetica-Bold'),
        ))
    else:
        elements.append(Paragraph(
            f"The venue pool ({available_count}) comfortably covers the busiest slot's requirement "
            f"of {peak_needed} simultaneous venue(s) — no double-booking is structurally forced.",
            styles['RCell'],
        ))
    if overflow_count:
        elements.append(Paragraph(
            f"WARNING: {overflow_count} scheduled class(es) exceed the capacity of their assigned venue — see "
            f"'OVERFLOW' flags below.",
            ParagraphStyle('WarnNote2', parent=styles['RCell'], textColor=REPORT_COLORS['warn'], fontName='Helvetica-Bold'),
        ))
    elements.append(Spacer(1, 14))

    # ── Available venues ─────────────────────────────────────────────────
    elements.append(Paragraph(f"Available Venues ({available_count})", styles['RSection']))
    elements.append(Paragraph(
        "Every venue currently offered to the scheduler (excludes any venue under an active Block).",
        styles['RCell'],
    ))
    elements.append(Spacer(1, 6))
    if not venue_rows:
        elements.append(Paragraph("No available venues.", styles['RCell']))
    else:
        header = ['Venue', 'Building', 'Capacity']
        rows = [[Paragraph(h, styles['RHead']) for h in header]]
        for v in venue_rows:
            rows.append([
                Paragraph(v['code'], styles['RCellBold']),
                Paragraph(v['building'], styles['RCell']),
                Paragraph(str(v['capacity_display']), styles['RCell']),
            ])
        table = Table(rows, colWidths=[1.8 * inch, 2.6 * inch, 1.5 * inch], repeatRows=1)
        table.setStyle(_standard_table_style())
        elements.append(table)
    elements.append(Spacer(1, 14))

    # ── Venues needed per slot (concurrency), flagged where it exceeds pool ─
    elements.append(Paragraph("Venues Needed by Timeslot", styles['RSection']))
    elements.append(Paragraph(
        "Every distinct day/time slot in scope with the number of venues it books simultaneously. "
        "Any row exceeding the available-venue count is structurally impossible to schedule without "
        "a collision.",
        styles['RCell'],
    ))
    elements.append(Spacer(1, 6))
    if not slots:
        elements.append(Paragraph("No scheduled entries in this scope.", styles['RCell']))
    else:
        header = ['Day', 'Time', 'Venues Needed', 'Status']
        rows = [[Paragraph(h, styles['RHead']) for h in header]]
        for s in slots:
            ok = s['venues_needed'] <= available_count
            status_style = styles['RCell'] if ok else ParagraphStyle(
                'OverCell', parent=styles['RCell'], textColor=REPORT_COLORS['warn'], fontName='Helvetica-Bold',
            )
            rows.append([
                Paragraph(s['day'], styles['RCell']),
                Paragraph(f"{s['start_time'].strftime('%H:%M')}-{s['end_time'].strftime('%H:%M')}", styles['RCell']),
                Paragraph(str(s['venues_needed']), styles['RCellBold']),
                Paragraph('OK' if ok else f'SHORT by {s["venues_needed"] - available_count}', status_style),
            ])
        table = Table(rows, colWidths=[1.4 * inch, 1.6 * inch, 1.4 * inch, 1.5 * inch], repeatRows=1)
        table.setStyle(_standard_table_style())
        elements.append(table)
    elements.append(Spacer(1, 14))

    # ── Busiest slot(s) — capacity fit detail ───────────────────────────
    elements.append(Paragraph("Busiest Slot — Capacity Fit", styles['RSection']))
    if not peak_slots:
        elements.append(Paragraph("No scheduled entries in this scope.", styles['RCell']))
    else:
        for s in peak_slots:
            elements.append(Paragraph(
                f"{s['day']} {s['start_time'].strftime('%H:%M')}-{s['end_time'].strftime('%H:%M')} "
                f"— {s['venues_needed']} venue(s) needed",
                styles['RCellBold'],
            ))
            header = ['Course', 'Program', 'Students Needed', 'Assigned Venue', 'Venue Capacity', 'Fit']
            rows = [[Paragraph(h, styles['RHead']) for h in header]]
            for c in s['classes']:
                fit_ok = not c['over_capacity']
                fit_style = styles['RCell'] if fit_ok else ParagraphStyle(
                    'OverfitCell', parent=styles['RCell'], textColor=REPORT_COLORS['warn'], fontName='Helvetica-Bold',
                )
                rows.append([
                    Paragraph(c['course_code'] or '-', styles['RCellBold']),
                    Paragraph(c['program'], styles['RCell']),
                    Paragraph(str(c['students_needed']), styles['RCell']),
                    Paragraph(c['venue_code'], styles['RCell']),
                    Paragraph(str(c['venue_capacity']) if c['venue_capacity'] is not None else 'Unknown', styles['RCell']),
                    Paragraph('Fits' if fit_ok else 'OVERFLOW', fit_style),
                ])
            table = Table(
                rows,
                colWidths=[1.1 * inch, 1.5 * inch, 1.1 * inch, 1.0 * inch, 1.0 * inch, 0.9 * inch],
                repeatRows=1,
            )
            table.setStyle(_standard_table_style())
            elements.append(table)
            elements.append(Spacer(1, 10))

    return _pdf_response(elements, "venue_capacity_availability_report.pdf", template_config, ref,
                          compiled_by=_full_name(request))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6b — universal search (lecturer / program / department / course code)
# ═══════════════════════════════════════════════════════════════════════════

def _universal_search_qs(query, scope=SCOPE_TIMETABLE):
    """
    One free-text box that matches ANYTHING relevant — course code, course
    name, lecturer name, program name, department name, faculty name,
    specialization stem/category, student group, or the venue/day it's
    scheduled in — all OR'd together, case-insensitive substring match.

    This is the single search definition shared by both search boxes on
    the analysis dashboard (Course Lookup and Universal Search) so that
    "search anything" means the same thing in both places.

    `scope` picks which reverse relation the venue/day terms search
    through: CourseAllocation.timetable_entries for the regular timetable,
    CourseAllocation.exam_timetable_entries for exams — searching "LR 4"
    with scope=exam should only match courses actually sitting an exam in
    LR 4, not ones merely taught there.
    """
    q = (query or '').strip()
    if not q:
        return CourseAllocation.objects.none()
    entries_field = 'exam_timetable_entries' if scope == SCOPE_EXAM else 'timetable_entries'
    return (
        CourseAllocation.objects.filter(
            Q(course_code__icontains=q) |
            Q(course_name__icontains=q) |
            Q(lecturer__name__icontains=q) |
            Q(program__name__icontains=q) |
            Q(department__name__icontains=q) |
            Q(department__faculty__name__icontains=q) |
            Q(specialization_stem__name__icontains=q) |
            Q(specialization_stem__category__name__icontains=q) |
            Q(student_group__name__icontains=q) |
            Q(student_group__letter__icontains=q) |
            Q(**{f'{entries_field}__venue__code__icontains': q}) |
            Q(**{f'{entries_field}__venue__building__name__icontains': q}) |
            Q(**{f'{entries_field}__venue__building__code__icontains': q}) |
            Q(**{f'{entries_field}__day__icontains': q})
        )
        .select_related(
            'department', 'department__faculty', 'program', 'lecturer', 'program_course',
            'student_group', 'specialization_stem', 'specialization_stem__category',
        )
        .distinct()
    )


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def universal_search_api(request):
    """
    JSON: GET ?q=<anything> — searches lecturer name, program name,
    department name, course code, and course name in one go. Returns
    EVERY match, uncapped — if a term matches 111 rows, all 111 come back,
    so the on-screen list and the PDF export are never out of sync.
    """
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'status': 'error', 'message': 'Provide a search term via ?q='}, status=400)

    scope = _resolve_scope(request)
    Model = _scoped_model(scope)
    qs = _universal_search_qs(query, scope=scope).order_by('department__name', 'program__name', 'course_code')
    tt_cache = {
        tt.course_allocation_id: tt
        for tt in Model.objects.filter(
            venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
        ).select_related('venue')
    }
    combined_meta = _get_combined_group_meta_map()
    dept_map = _combined_group_department_map()

    results = []
    for a in qs:
        sched = _schedule_info_for_allocation(a, tt_cache, model=Model)
        meta = combined_meta.get(a.id)
        if meta:
            meta = {**meta, **dept_map.get(a.id, {})}
        results.append({
            'allocation_id': a.id,
            'course_code': a.course_code,
            'course_name': a.course_name,
            'department': getattr(a.department, 'name', 'N/A'),
            'program': getattr(a.program, 'name', 'N/A'),
            'year': _get_year_value(a),
            'semester': getattr(a.program_course, 'semester', None),
            'lecturer': getattr(a.lecturer, 'name', 'Unassigned'),
            'students': a.number_of_students or 0,
            'is_special_intake': a.is_special_intake,
            'specialization': _specialization_label(a),
            'student_group': _student_group_label(a),
            'merged_group': meta,
            **sched,
        })

    return JsonResponse({'status': 'success', 'query': query, 'exam_or_timetable': scope, 'count': len(results), 'results': results})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def export_universal_search_pdf(request):
    """
    PDF export of the universal search — prints every matching row, no
    cap. Whatever universal_search_api counted, this prints all of it
    (e.g. 111 matches means 111 rows on the PDF, not a truncated sample).
    """
    query = request.GET.get('q', '').strip()
    template_config = _get_template_config()
    styles = _styles()
    ref = template_config.get_reference_number(datetime.now().strftime("%d-%b-%Y").upper())
    date_str = datetime.now().strftime("%d-%b-%Y").upper()

    elements = []
    if not query:
        _letterhead(elements, styles, template_config, "SEARCH RESULTS REPORT", ref=ref, date_str=date_str)
        elements.append(Paragraph("No search term was supplied.", styles['RCell']))
        return _pdf_response(elements, "search_results_report.pdf", template_config, ref, compiled_by=_full_name(request))

    scope = _resolve_scope(request)
    Model = _scoped_model(scope)
    qs = _universal_search_qs(query, scope=scope).order_by('department__name', 'program__name', 'course_code')
    tt_cache = {
        tt.course_allocation_id: tt
        for tt in Model.objects.filter(
            venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
        ).select_related('venue')
    }
    combined_meta = _get_combined_group_meta_map()
    dept_map = _combined_group_department_map()
    allocations = list(qs)

    _letterhead(
        elements, styles, template_config,
        "SEARCH RESULTS REPORT",
        f'Search: "{query}" — {len(allocations)} match(es)',
        ref=ref, date_str=date_str,
    )

    if not allocations:
        elements.append(Paragraph("No matches found.", styles['RCell']))
        return _pdf_response(elements, "search_results_report.pdf", template_config, ref, compiled_by=_full_name(request))

    matches = []
    for a in allocations:
        sched = _schedule_info_for_allocation(a, tt_cache)
        meta = combined_meta.get(a.id)
        if meta:
            meta = {**meta, **dept_map.get(a.id, {})}
        matches.append({
            'course_code': a.course_code,
            'course_name': a.course_name,
            'department': getattr(a.department, 'name', 'N/A'),
            'program': getattr(a.program, 'name', 'N/A'),
            'year': _get_year_value(a),
            'semester': getattr(a.program_course, 'semester', None),
            'lecturer': getattr(a.lecturer, 'name', 'Unassigned'),
            'students': a.number_of_students or 0,
            'is_special_intake': a.is_special_intake,
            'specialization': _specialization_label(a),
            'student_group': _student_group_label(a),
            'merged_group': meta,
            **sched,
        })

    elements.extend(_build_match_matrix_elements(matches, styles, {'show_program': True, 'show_dept': True}))

    filename = f"search_{re.sub(r'[^A-Za-z0-9]+', '_', query)[:40].strip('_') or 'results'}.pdf"
    return _pdf_response(elements, filename, template_config, ref, landscape_mode=True, compiled_by=_full_name(request))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7 — Program Analysis: per-program/year breakdown
# ═══════════════════════════════════════════════════════════════════════════

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def program_analysis_api(request):
    """
    Returns a detailed breakdown for each program/year:
      - Total courses (CourseAllocation rows)
      - Student Groups (if any) and their courses
      - Combined Course Groups and their member courses
      - Courses not in any group/combination
      - Specialization Stems and their courses
      - Selection Groups and their courses
    """
    try:
        dept_id = request.GET.get('department_id')
        program_id = request.GET.get('program_id')
        
        # Base queryset
        qs = CourseAllocation.objects.select_related(
            'program', 'program__department', 'program_course',
            'student_group', 'lecturer',
        ).prefetch_related(
            'combined_groups', 'specialization_stems', 'selection_groups',
        )
        
        if program_id:
            try:
                qs = qs.filter(program_id=int(program_id))
            except (ValueError, TypeError):
                return JsonResponse({'status': 'error', 'message': 'Invalid program_id'}, status=400)
        elif dept_id:
            try:
                qs = qs.filter(program__department_id=int(dept_id))
            except (ValueError, TypeError):
                return JsonResponse({'status': 'error', 'message': 'Invalid department_id'}, status=400)
        
        # Group by program
        by_program = defaultdict(lambda: defaultdict(list))
        for a in qs:
            if not a.program:
                continue
            year = getattr(a.program_course, 'year', None) or _get_year_value(a) or 0
            by_program[a.program_id][year].append(a)
        
        # Get all combined groups with their members
        combined_groups = CombinedCourseGroup.objects.prefetch_related(
            'allocations', 'allocations__program', 'allocations__program_course',
            'allocations__student_group', 'allocations__lecturer',
        )
        group_members = defaultdict(list)
        group_meta = {}
        for g in combined_groups:
            for a in g.allocations.all():
                group_members[a.id].append(g)
            group_meta[g.id] = {
                'id': g.id,
                'group_code': g.group_code,
                'base_course_code': g.base_course_code,
                'lecturer': g.lecturer.display_name if g.lecturer else 'Unassigned',
                'total_students': g.total_students(),
                'member_count': g.allocations.count(),
            }
        
        # Get student groups
        student_groups = StudentGroup.objects.select_related('program').prefetch_related('course_allocations')
        sg_members = defaultdict(list)
        sg_by_id = {}
        for sg in student_groups:
            sg_by_id[sg.id] = sg
            for a in sg.course_allocations.all():
                sg_members[a.id].append(sg)
        
        # Get specialization stems
        stems = SpecializationStem.objects.select_related('category').prefetch_related('courses')
        stem_members = defaultdict(list)
        stem_meta = {}
        for s in stems:
            for a in s.courses.all():
                stem_members[a.id].append(s)
            stem_meta[s.id] = {
                'id': s.id,
                'name': s.name,
                'category': s.category.name if s.category else '',
            }
        
        # Get selection groups
        sel_groups = SelectionGroup.objects.prefetch_related('courses')
        sel_members = defaultdict(list)
        sel_meta = {}
        for sg in sel_groups:
            for a in sg.courses.all():
                sel_members[a.id].append(sg)
            sel_meta[sg.id] = {
                'id': sg.id,
                'name': sg.name,
            }
        
        program_by_id = {
            p.id: p for p in Program.objects.select_related('department').filter(id__in=by_program.keys())
        }

        result = []
        for prog_id, year_data in by_program.items():
            program = program_by_id.get(prog_id)
            if program is None:
                continue
                
            prog_entry = {
                'program_id': program.id,
                'program_name': program.name,
                'department': program.department.name if program.department else '',
                'years': []
            }
            
            for year, allocations in sorted(year_data.items(), key=lambda x: x[0] if isinstance(x[0], int) else 0):
                # Group allocations by student group
                by_sg = defaultdict(list)
                for a in allocations:
                    if a.student_group:
                        by_sg[a.student_group_id].append(a)
                
                # Group allocations by combined group
                by_combined = defaultdict(list)
                for a in allocations:
                    if a.id in group_members:
                        for g in group_members[a.id]:
                            by_combined[g.id].append(a)
                # Remove duplicates from combined groups
                for gid in by_combined:
                    by_combined[gid] = list({a.id: a for a in by_combined[gid]}.values())
                
                # Group by specialization stem
                by_stem = defaultdict(list)
                for a in allocations:
                    stems_for_a = stem_members.get(a.id, [])
                    if stems_for_a:
                        for s in stems_for_a:
                            by_stem[s.id].append(a)
                for sid in by_stem:
                    by_stem[sid] = list({a.id: a for a in by_stem[sid]}.values())
                
                # Group by selection group
                by_sel = defaultdict(list)
                for a in allocations:
                    if a.is_elective and a.id in sel_members:
                        for sg in sel_members[a.id]:
                            by_sel[sg.id].append(a)
                for sid in by_sel:
                    by_sel[sid] = list({a.id: a for a in by_sel[sid]}.values())
                
                # Find ungrouped courses (not in student group, not in combined group)
                grouped_ids = set()
                for sg_list in by_sg.values():
                    for a in sg_list:
                        grouped_ids.add(a.id)
                for cg_list in by_combined.values():
                    for a in cg_list:
                        grouped_ids.add(a.id)
                for stem_list in by_stem.values():
                    for a in stem_list:
                        grouped_ids.add(a.id)
                for sel_list in by_sel.values():
                    for a in sel_list:
                        grouped_ids.add(a.id)
                
                ungrouped = [a for a in allocations if a.id not in grouped_ids]
                
                # Build year entry
                year_entry = {
                    'year': year,
                    'total_courses': len(allocations),
                    'student_groups': [],
                    'combined_groups': [],
                    'specialization_stems': [],
                    'selection_groups': [],
                    'ungrouped_courses': [],
                }
                
                # Combined group code(s) each allocation belongs to, for
                # annotating that course wherever it's listed (student
                # group, stem, selection group, ungrouped) — not just
                # inside its own "Combined Course Groups" section.
                def _combined_code_for(a):
                    gs = group_members.get(a.id)
                    if not gs:
                        return ''
                    return ', '.join(sorted({g.group_code for g in gs}))

                # Student groups
                for sg_id, sg_allocs in by_sg.items():
                    sg = sg_by_id.get(sg_id)
                    year_entry['student_groups'].append({
                        'id': sg_id,
                        'name': sg.name if sg else f'Group {sg_id}',
                        'letter': sg.letter if sg else '',
                        'course_count': len(sg_allocs),
                        'courses': [
                            {
                                'id': a.id,
                                'course_code': a.course_code,
                                'course_name': a.course_name,
                                'lecturer': a.lecturer.display_name if a.lecturer else 'Unassigned',
                                'students': a.number_of_students,
                                'year': year,
                                'semester': getattr(a.program_course, 'semester', None),
                                'is_special_intake': a.is_special_intake,
                                'combined_group_code': _combined_code_for(a),
                            }
                            for a in sg_allocs
                        ]
                    })
                
                # Combined groups
                for gid, g_allocs in by_combined.items():
                    if gid not in group_meta:
                        continue
                    meta = group_meta[gid]
                    year_entry['combined_groups'].append({
                        'id': gid,
                        'group_code': meta['group_code'],
                        'base_course_code': meta['base_course_code'],
                        'lecturer': meta['lecturer'],
                        'total_students': meta['total_students'],
                        'courses': [
                            {
                                'id': a.id,
                                'course_code': a.course_code,
                                'course_name': a.course_name,
                                'program': a.program.name if a.program else '',
                                'student_group': a.student_group.name if a.student_group else '',
                                'lecturer': a.lecturer.display_name if a.lecturer else 'Unassigned',
                                'year': year,
                                'semester': getattr(a.program_course, 'semester', None),
                                'is_special_intake': a.is_special_intake,
                            }
                            for a in g_allocs
                        ]
                    })
                
                # Specialization stems
                for sid, s_allocs in by_stem.items():
                    if sid not in stem_meta:
                        continue
                    meta = stem_meta[sid]
                    year_entry['specialization_stems'].append({
                        'id': sid,
                        'name': meta['name'],
                        'category': meta['category'],
                        'courses': [
                            {
                                'id': a.id,
                                'course_code': a.course_code,
                                'course_name': a.course_name,
                                'lecturer': a.lecturer.display_name if a.lecturer else 'Unassigned',
                                'year': year,
                                'semester': getattr(a.program_course, 'semester', None),
                                'is_special_intake': a.is_special_intake,
                                'combined_group_code': _combined_code_for(a),
                            }
                            for a in s_allocs
                        ]
                    })
                
                # Selection groups
                for sid, s_allocs in by_sel.items():
                    if sid not in sel_meta:
                        continue
                    meta = sel_meta[sid]
                    year_entry['selection_groups'].append({
                        'id': sid,
                        'name': meta['name'],
                        'courses': [
                            {
                                'id': a.id,
                                'course_code': a.course_code,
                                'course_name': a.course_name,
                                'lecturer': a.lecturer.display_name if a.lecturer else 'Unassigned',
                                'year': year,
                                'semester': getattr(a.program_course, 'semester', None),
                                'is_special_intake': a.is_special_intake,
                                'combined_group_code': _combined_code_for(a),
                            }
                            for a in s_allocs
                        ]
                    })
                
                # Ungrouped courses
                for a in ungrouped:
                    year_entry['ungrouped_courses'].append({
                        'id': a.id,
                        'course_code': a.course_code,
                        'course_name': a.course_name,
                        'lecturer': a.lecturer.display_name if a.lecturer else 'Unassigned',
                        'students': a.number_of_students,
                        'is_elective': a.is_elective,
                        'is_evening_weekend': a.is_evening_weekend,
                        'year': year,
                        'semester': getattr(a.program_course, 'semester', None),
                        'is_special_intake': a.is_special_intake,
                        'combined_group_code': _combined_code_for(a),
                    })
                
                prog_entry['years'].append(year_entry)
            
            # Sort years descending
            prog_entry['years'].sort(key=lambda x: x['year'] if isinstance(x['year'], int) else 0, reverse=True)
            result.append(prog_entry)
        
        # Sort programs by name
        result.sort(key=lambda x: x['program_name'])
        
        return JsonResponse({'status': 'success', 'programs': result})
        
    except Exception as e:
        logger.exception("Error in program_analysis_api")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 8 — Zero-Student Courses API
# ═══════════════════════════════════════════════════════════════════════════

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def zero_student_courses_api(request):
    """
    Returns all courses with 0 students, filterable by department and program.
    """
    try:
        dept_id = request.GET.get('department_id')
        program_id = request.GET.get('program_id')
        
        qs = CourseAllocation.objects.filter(number_of_students=0).select_related(
            'program', 'program__department', 'lecturer', 'program_course',
            'department', 'student_group',
        )
        
        if program_id:
            try:
                qs = qs.filter(program_id=int(program_id))
            except (ValueError, TypeError):
                return JsonResponse({'status': 'error', 'message': 'Invalid program_id'}, status=400)
        elif dept_id:
            try:
                qs = qs.filter(program__department_id=int(dept_id))
            except (ValueError, TypeError):
                return JsonResponse({'status': 'error', 'message': 'Invalid department_id'}, status=400)
        
        # Group by department then program
        by_dept = defaultdict(lambda: defaultdict(list))
        for a in qs:
            dept_name = a.program.department.name if a.program and a.program.department else 'Unknown'
            prog_name = a.program.name if a.program else 'Unknown'
            by_dept[dept_name][prog_name].append(a)
        
        result = []
        for dept_name, programs in sorted(by_dept.items()):
            dept_entry = {
                'department': dept_name,
                'total_courses': sum(len(courses) for courses in programs.values()),
                'programs': []
            }
            for prog_name, courses in sorted(programs.items()):
                dept_entry['programs'].append({
                    'program': prog_name,
                    'course_count': len(courses),
                    'courses': [
                        {
                            'id': a.id,
                            'course_code': a.course_code,
                            'course_name': a.course_name,
                            'lecturer': a.lecturer.display_name if a.lecturer else 'Unassigned',
                            'year': getattr(a.program_course, 'year', 'N/A'),
                            'semester': getattr(a.program_course, 'semester', 'N/A'),
                            'student_group': a.student_group.name if a.student_group else '',
                            'is_elective': a.is_elective,
                            'is_evening_weekend': a.is_evening_weekend,
                            'intake': a.intake,
                        }
                        for a in courses
                    ]
                })
            result.append(dept_entry)
        
        return JsonResponse({'status': 'success', 'data': result, 'total': qs.count()})
        
    except Exception as e:
        logger.exception("Error in zero_student_courses_api")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9 — Lecturer Overload API (with Combined Course Group support)
# ═══════════════════════════════════════════════════════════════════════════

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def lecturer_overload_api(request):
    """
    Returns lecturers with more than the threshold units, showing count and 
    which courses they're teaching. Filterable by department and program.
    
    IMPORTANT: Combined Course Groups count as ONE unit (not individual 
    allocations), since they're taught together as a single class.
    """
    try:
        dept_id = request.GET.get('department_id')
        program_id = request.GET.get('program_id')
        threshold = int(request.GET.get('threshold', 6))
        
        # Get all allocations with lecturers
        qs = CourseAllocation.objects.select_related(
            'lecturer', 'program', 'program__department', 'program_course',
            'department', 'student_group',
        ).exclude(lecturer__isnull=True)
        
        if program_id:
            try:
                qs = qs.filter(program_id=int(program_id))
            except (ValueError, TypeError):
                return JsonResponse({'status': 'error', 'message': 'Invalid program_id'}, status=400)
        elif dept_id:
            try:
                qs = qs.filter(program__department_id=int(dept_id))
            except (ValueError, TypeError):
                return JsonResponse({'status': 'error', 'message': 'Invalid department_id'}, status=400)
        
        # ── Build combined group membership map ──
        # For each allocation, track which combined group it belongs to
        combined_group_map = {}
        group_primary_map = {}
        group_code_map = {}
        group_members_map = defaultdict(list)
        
        for cg in CombinedCourseGroup.objects.prefetch_related('allocations'):
            alloc_ids = list(cg.allocations.values_list('id', flat=True))
            primary_id = cg.primary_allocation_id
            if primary_id:
                group_primary_map[primary_id] = cg.id
                group_code_map[cg.id] = cg.group_code
                for aid in alloc_ids:
                    combined_group_map[aid] = {
                        'group_id': cg.id,
                        'primary_id': primary_id,
                        'group_code': cg.group_code,
                        'is_primary': aid == primary_id,
                    }
                    group_members_map[primary_id].append(aid)
        
        # ── Group allocations by lecturer ──
        by_lecturer = defaultdict(list)
        for a in qs:
            if a.lecturer:
                by_lecturer[a.lecturer.id].append(a)
        
        result = []
        for lect_id, allocations in by_lecturer.items():
            # ── Count UNIQUE units (combined groups count as 1) ──
            combined_group_ids = set()
            standalone_allocations = []
            
            for a in allocations:
                if a.id in combined_group_map:
                    cg_info = combined_group_map[a.id]
                    # Only count the PRIMARY allocation of each combined group
                    if cg_info['is_primary']:
                        combined_group_ids.add(cg_info['group_id'])
                elif a.id in group_primary_map:
                    # This allocation is the primary of a group (group_primary_map
                    # was built up-front from ALL CombinedCourseGroup rows, so this
                    # is a plain dict lookup — no extra query per allocation, which
                    # is what made this endpoint slow with many lecturers/courses).
                    combined_group_ids.add(group_primary_map[a.id])
                else:
                    standalone_allocations.append(a)
            
            # Total units = number of combined groups + number of standalone allocations
            total_units = len(combined_group_ids) + len(standalone_allocations)
            
            if total_units > threshold:
                lecturer = allocations[0].lecturer
                dept_name = lecturer.department.name if lecturer.department else 'N/A'
                
                # ── Build courses list with combined group info ──
                by_program = defaultdict(list)
                
                # First, add all standalone allocations
                for a in standalone_allocations:
                    prog_name = a.program.name if a.program else 'Unknown'
                    by_program[prog_name].append({
                        'id': a.id,
                        'course_code': a.course_code,
                        'course_name': a.course_name,
                        'year': getattr(a.program_course, 'year', 'N/A'),
                        'semester': getattr(a.program_course, 'semester', 'N/A'),
                        'student_group': a.student_group.name if a.student_group else '',
                        'students': a.number_of_students,
                        'is_elective': a.is_elective,
                        'is_combined': False,
                        'group_code': None,
                        'group_members': None,
                    })
                
                # Then add combined groups (each as ONE unit)
                for group_id in combined_group_ids:
                    # Find the primary allocation for this group
                    primary_alloc = None
                    group_codes = []
                    group_students = 0
                    
                    for a in allocations:
                        if a.id in combined_group_map and combined_group_map[a.id]['group_id'] == group_id:
                            group_codes.append(a.course_code)
                            group_students += a.number_of_students
                            if combined_group_map[a.id]['is_primary']:
                                primary_alloc = a
                    
                    # If primary not found in map, try direct lookup
                    if not primary_alloc:
                        cg = CombinedCourseGroup.objects.filter(id=group_id).first()
                        if cg and cg.primary_allocation:
                            primary_alloc = cg.primary_allocation
                            # Also get all members
                            for member in cg.allocations.all():
                                if member.course_code not in group_codes:
                                    group_codes.append(member.course_code)
                                group_students += member.number_of_students
                    
                    if primary_alloc:
                        prog_name = primary_alloc.program.name if primary_alloc.program else 'Unknown'
                        by_program[prog_name].append({
                            'id': primary_alloc.id,
                            'course_code': primary_alloc.course_code,
                            'course_name': primary_alloc.course_name,
                            'year': getattr(primary_alloc.program_course, 'year', 'N/A'),
                            'semester': getattr(primary_alloc.program_course, 'semester', 'N/A'),
                            'student_group': primary_alloc.student_group.name if primary_alloc.student_group else '',
                            'students': group_students,
                            'is_elective': primary_alloc.is_elective,
                            'is_combined': True,
                            'group_code': group_code_map.get(group_id, f'CG-{group_id}'),
                            'group_members': group_codes,
                        })
                
                result.append({
                    'lecturer_id': lect_id,
                    'lecturer_name': lecturer.display_name,
                    'designation': lecturer.designation,
                    'department': dept_name,
                    'course_count': total_units,
                    'standalone_count': len(standalone_allocations),
                    'combined_group_count': len(combined_group_ids),
                    'programs': [
                        {
                            'program': prog_name,
                            'courses': course_list
                        }
                        for prog_name, course_list in sorted(by_program.items())
                    ]
                })
        
        # Sort by course count descending
        result.sort(key=lambda x: x['course_count'], reverse=True)
        
        return JsonResponse({
            'status': 'success',
            'data': result,
            'total_lecturers': len(result),
            'threshold': threshold,
            'note': 'Combined course groups count as ONE unit (not individual allocations).',
        })
        
    except Exception as e:
        logger.exception("Error in lecturer_overload_api")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9B — Consecutive Classes & Weekly Workload
# ═══════════════════════════════════════════════════════════════════════════
# "Workload" here means the ACTUAL scheduled weekly timetable (from
# Timetable rows — day/start/end), not just a count of course units like
# Section 9 above. Two lenses:
#   - Lecturer: every Timetable row whose course_allocation has that
#     lecturer. Non-primary CombinedCourseGroup members never get their
#     own Timetable row (see note in _schedule_info_for_allocation), so
#     querying Timetable directly already gives one row per REAL physical
#     class the lecturer stands in front of — no dedup needed.
#   - Program / Program+Year: every Timetable row whose course_allocation
#     belongs to that program (and, when drilling in, that curriculum
#     year). A course that is a non-primary combined-group member has no
#     Timetable row of its own, so its schedule is resolved through the
#     group's primary_allocation — same pattern used in
#     _schedule_info_for_allocation()/lecturer_overload_api() above.
#
# "Consecutive" = two or more sessions on the same day where one starts
# exactly when the previous ends (back-to-back, no gap). Endpoints:
#   - lecturer_workload_api         — ranked list, highest workload first
#   - lecturer_weekly_schedule_api  — one lecturer's full week
#   - program_workload_api          — ranked list of programs (by year)
#   - program_year_weekly_schedule_api — one program+year's full week
# ═══════════════════════════════════════════════════════════════════════════

def _duration_hours(start, end):
    """Hours between two time-of-day objects (same-day, end > start)."""
    if not start or not end:
        return 0.0
    base = datetime.today()
    delta = datetime.combine(base, end) - datetime.combine(base, start)
    return max(delta.total_seconds() / 3600.0, 0.0)


def _analyze_weekly_blocks(blocks):
    """
    blocks: list of dicts, each with at least 'day' (str), 'start' (time),
    'end' (time) — plus whatever extra keys the caller wants carried
    through into 'by_day' for display.

    Returns:
      {
        'by_day': {day: [blocks sorted by start_time, each annotated with
                          'consecutive_with_prev': bool]},
        'runs': [[block, block, ...], ...]   # each run has len >= 2
        'max_run_length': int,
        'max_run_hours': float,
        'total_hours': float,
        'total_sessions': int,
      }
    """
    by_day_raw = defaultdict(list)
    for b in blocks:
        by_day_raw[b['day'] or 'Unspecified'].append(b)

    by_day = {}
    runs = []
    max_run_length = 0
    max_run_hours = 0.0

    ordered_days = [d for d in DAY_ORDER if d in by_day_raw] + \
                   sorted(d for d in by_day_raw if d not in DAY_ORDER)

    for day in ordered_days:
        items = sorted(by_day_raw[day], key=lambda x: (x['start'], x['end']))
        for i, item in enumerate(items):
            item['consecutive_with_prev'] = (
                i > 0 and items[i - 1]['end'] == item['start']
            )
        by_day[day] = items

        current_run = [items[0]] if items else []
        for prev, curr in zip(items, items[1:]):
            if prev['end'] == curr['start']:
                current_run.append(curr)
            else:
                if len(current_run) >= 2:
                    runs.append(current_run)
                current_run = [curr]
        if len(current_run) >= 2:
            runs.append(current_run)

    for run in runs:
        length = len(run)
        hours = _duration_hours(run[0]['start'], run[-1]['end'])
        if (length, hours) > (max_run_length, max_run_hours):
            max_run_length = length
            max_run_hours = hours

    total_hours = sum(_duration_hours(b['start'], b['end']) for b in blocks)

    return {
        'by_day': by_day,
        'runs': runs,
        'max_run_length': max_run_length,
        'max_run_hours': round(max_run_hours, 2),
        'total_hours': round(total_hours, 2),
        'total_sessions': len(blocks),
    }


def _serialize_day_blocks(by_day, extra_keys):
    """Turn _analyze_weekly_blocks()'s by_day into JSON-safe day->sessions."""
    out = []
    for day, items in by_day.items():
        sessions = []
        for b in items:
            entry = {
                'start_time': b['start'].strftime('%H:%M') if b['start'] else '',
                'end_time': b['end'].strftime('%H:%M') if b['end'] else '',
                'duration_hours': round(_duration_hours(b['start'], b['end']), 2),
                'consecutive_with_prev': bool(b.get('consecutive_with_prev')),
            }
            for k in extra_keys:
                entry[k] = b.get(k, '')
            sessions.append(entry)
        out.append({'day': day, 'sessions': sessions})
    return out


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def lecturer_workload_api(request):
    """
    Lecturers ranked by consecutive (back-to-back) scheduled classes,
    highest first — with their overall weekly workload (scheduled hours,
    session count). Filterable by department/program; `min_consecutive`
    (default 2) is the minimum back-to-back run length to be included.
    """
    try:
        dept_id = request.GET.get('department_id')
        program_id = request.GET.get('program_id')
        try:
            min_consecutive = max(int(request.GET.get('min_consecutive', 2)), 2)
        except (ValueError, TypeError):
            return JsonResponse({'status': 'error', 'message': 'Invalid min_consecutive'}, status=400)

        Model = _scoped_model(_resolve_scope(request))
        qs = Model.objects.select_related(
            'venue', 'course_allocation', 'course_allocation__lecturer',
            'course_allocation__lecturer__department', 'course_allocation__program',
            'course_allocation__program_course',
        ).exclude(course_allocation__lecturer__isnull=True).filter(
            start_time__isnull=False, end_time__isnull=False,
        )

        if program_id:
            try:
                qs = qs.filter(course_allocation__program_id=int(program_id))
            except (ValueError, TypeError):
                return JsonResponse({'status': 'error', 'message': 'Invalid program_id'}, status=400)
        elif dept_id:
            try:
                qs = qs.filter(course_allocation__program__department_id=int(dept_id))
            except (ValueError, TypeError):
                return JsonResponse({'status': 'error', 'message': 'Invalid department_id'}, status=400)

        by_lecturer_blocks = defaultdict(list)
        lecturer_meta = {}
        for tt in qs:
            alloc = tt.course_allocation
            lecturer = alloc.lecturer
            lecturer_meta[lecturer.id] = lecturer
            by_lecturer_blocks[lecturer.id].append({
                'day': tt.day,
                'start': tt.start_time,
                'end': tt.end_time,
                'course_code': alloc.course_code,
                'course_name': alloc.course_name,
                'program': alloc.program.name if alloc.program else '',
                'venue': tt.venue.code if tt.venue else '',
            })

        result = []
        for lect_id, blocks in by_lecturer_blocks.items():
            analysis = _analyze_weekly_blocks(blocks)
            if analysis['max_run_length'] < min_consecutive:
                continue
            lecturer = lecturer_meta[lect_id]
            distinct_courses = len({(b['course_code'], b['program']) for b in blocks})
            result.append({
                'lecturer_id': lect_id,
                'lecturer_name': lecturer.display_name,
                'designation': lecturer.designation,
                'department': lecturer.department.name if lecturer.department else 'N/A',
                'total_weekly_hours': analysis['total_hours'],
                'total_sessions': analysis['total_sessions'],
                'distinct_courses': distinct_courses,
                'max_consecutive_length': analysis['max_run_length'],
                'max_consecutive_hours': analysis['max_run_hours'],
                'consecutive_run_count': len(analysis['runs']),
            })

        result.sort(key=lambda x: (
            x['max_consecutive_length'], x['max_consecutive_hours'], x['total_weekly_hours'],
        ), reverse=True)

        return JsonResponse({
            'status': 'success',
            'data': result,
            'total_lecturers': len(result),
            'min_consecutive': min_consecutive,
        })

    except Exception as e:
        logger.exception("Error in lecturer_workload_api")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def lecturer_weekly_schedule_api(request):
    """Full Monday–Sunday schedule for one lecturer, with consecutive flags."""
    try:
        lecturer_id = request.GET.get('lecturer_id')
        if not lecturer_id:
            return JsonResponse({'status': 'error', 'message': 'lecturer_id is required'}, status=400)
        try:
            lecturer = Lecturer.objects.select_related('department').get(id=int(lecturer_id))
        except (Lecturer.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'status': 'error', 'message': 'Lecturer not found'}, status=404)

        Model = _scoped_model(_resolve_scope(request))
        qs = Model.objects.select_related(
            'venue', 'course_allocation', 'course_allocation__program',
            'course_allocation__program_course', 'course_allocation__student_group',
        ).filter(
            course_allocation__lecturer_id=lecturer.id,
            start_time__isnull=False, end_time__isnull=False,
        )

        blocks = []
        for tt in qs:
            alloc = tt.course_allocation
            blocks.append({
                'day': tt.day,
                'start': tt.start_time,
                'end': tt.end_time,
                'course_code': alloc.course_code,
                'course_name': alloc.course_name,
                'program': alloc.program.name if alloc.program else '',
                'year': getattr(alloc.program_course, 'year', '') or '',
                'student_group': alloc.student_group.name if alloc.student_group else '',
                'venue': tt.venue.code if tt.venue else '',
            })

        analysis = _analyze_weekly_blocks(blocks)
        days = _serialize_day_blocks(analysis['by_day'], [
            'course_code', 'course_name', 'program', 'year', 'student_group', 'venue',
        ])
        distinct_courses = len({(b['course_code'], b['program']) for b in blocks})

        return JsonResponse({
            'status': 'success',
            'lecturer_id': lecturer.id,
            'lecturer_name': lecturer.display_name,
            'designation': lecturer.designation,
            'department': lecturer.department.name if lecturer.department else 'N/A',
            'total_weekly_hours': analysis['total_hours'],
            'total_sessions': analysis['total_sessions'],
            'distinct_courses': distinct_courses,
            'max_consecutive_length': analysis['max_run_length'],
            'max_consecutive_hours': analysis['max_run_hours'],
            'days': days,
        })

    except Exception as e:
        logger.exception("Error in lecturer_weekly_schedule_api")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


def _resolve_schedule_blocks_for_allocations(allocations, model=Timetable):
    """
    Build weekly 'blocks' (day/start/end + display fields) for a list of
    CourseAllocation objects, resolving non-primary CombinedCourseGroup
    members through their group's primary_allocation Timetable row (see
    section header note above) so combined courses still show up on their
    OWN program/year's weekly view even though they have no Timetable row
    of their own.

    `model` is Timetable or ExamTimetable, matching whichever scope the
    caller resolved the page/request to.
    """
    alloc_ids = [a.id for a in allocations]
    if not alloc_ids:
        return []

    # Map: non-primary allocation id -> primary allocation id, for any
    # CombinedCourseGroup touching these allocations.
    lookup_id_for = {}
    for cg in CombinedCourseGroup.objects.filter(
        allocations__id__in=alloc_ids
    ).prefetch_related('allocations').select_related('primary_allocation').distinct():
        if not cg.primary_allocation_id:
            continue
        for member_id in cg.allocations.values_list('id', flat=True):
            if member_id != cg.primary_allocation_id:
                lookup_id_for[member_id] = cg.primary_allocation_id

    lookup_ids = {lookup_id_for.get(a.id, a.id) for a in allocations}
    tt_by_alloc = defaultdict(list)
    for tt in model.objects.select_related('venue').filter(
        course_allocation_id__in=lookup_ids,
        start_time__isnull=False, end_time__isnull=False,
    ):
        tt_by_alloc[tt.course_allocation_id].append(tt)

    blocks = []
    for a in allocations:
        key = lookup_id_for.get(a.id, a.id)
        for tt in tt_by_alloc.get(key, []):
            blocks.append({
                'day': tt.day,
                'start': tt.start_time,
                'end': tt.end_time,
                'course_code': a.course_code,
                'course_name': a.course_name,
                'lecturer': a.lecturer.display_name if a.lecturer else 'Unassigned',
                'student_group': a.student_group.name if a.student_group else '',
                'venue': tt.venue.code if tt.venue else '',
            })
    return blocks


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def program_workload_api(request):
    """
    Programs ranked by consecutive (back-to-back) scheduled classes,
    highest first — broken down year by year so a program with one heavy
    year doesn't get diluted by lighter ones. `min_consecutive` (default 2)
    filters to programs with at least one year at/above that run length.
    """
    try:
        exam_or_tt_scope = _resolve_scope(request)
        Model = _scoped_model(exam_or_tt_scope)
        dept_id = request.GET.get('department_id')
        try:
            min_consecutive = max(int(request.GET.get('min_consecutive', 2)), 2)
        except (ValueError, TypeError):
            return JsonResponse({'status': 'error', 'message': 'Invalid min_consecutive'}, status=400)

        qs = CourseAllocation.objects.select_related(
            'program', 'program__department', 'program_course', 'lecturer', 'student_group',
        )
        if dept_id:
            try:
                qs = qs.filter(program__department_id=int(dept_id))
            except (ValueError, TypeError):
                return JsonResponse({'status': 'error', 'message': 'Invalid department_id'}, status=400)
        qs = qs.exclude(program__isnull=True)

        by_program_year = defaultdict(list)
        program_meta = {}
        for a in qs:
            year = getattr(a.program_course, 'year', None) or _get_year_value(a) or 0
            by_program_year[(a.program_id, year)].append(a)
            program_meta[a.program_id] = a.program

        # Resolve schedule blocks per (program, year) — batched per group
        # of allocations sharing that key.
        result_by_program = defaultdict(list)
        for (prog_id, year), allocations in by_program_year.items():
            blocks = _resolve_schedule_blocks_for_allocations(allocations, model=Model)
            analysis = _analyze_weekly_blocks(blocks)
            result_by_program[prog_id].append({
                'year': year,
                'total_hours': analysis['total_hours'],
                'total_sessions': analysis['total_sessions'],
                'max_consecutive_length': analysis['max_run_length'],
                'max_consecutive_hours': analysis['max_run_hours'],
                'consecutive_run_count': len(analysis['runs']),
            })

        result = []
        for prog_id, years in result_by_program.items():
            program = program_meta.get(prog_id)
            if not program:
                continue
            years.sort(key=lambda y: y['year'] if isinstance(y['year'], int) else 0, reverse=True)
            peak_year = max(years, key=lambda y: (y['max_consecutive_length'], y['max_consecutive_hours']))
            if peak_year['max_consecutive_length'] < min_consecutive:
                continue
            result.append({
                'program_id': prog_id,
                'program_name': program.name,
                'department': program.department.name if program.department else '',
                'total_weekly_hours_all_years': round(sum(y['total_hours'] for y in years), 2),
                'peak_year': peak_year['year'],
                'peak_max_consecutive_length': peak_year['max_consecutive_length'],
                'peak_max_consecutive_hours': peak_year['max_consecutive_hours'],
                'years': years,
            })

        result.sort(key=lambda x: (
            x['peak_max_consecutive_length'], x['peak_max_consecutive_hours'], x['total_weekly_hours_all_years'],
        ), reverse=True)

        return JsonResponse({
            'status': 'success',
            'data': result,
            'total_programs': len(result),
            'min_consecutive': min_consecutive,
        })

    except Exception as e:
        logger.exception("Error in program_workload_api")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def program_year_weekly_schedule_api(request):
    """Full Monday–Sunday schedule for one program + curriculum year."""
    try:
        program_id = request.GET.get('program_id')
        year = request.GET.get('year')
        if not program_id or year is None or year == '':
            return JsonResponse({'status': 'error', 'message': 'program_id and year are required'}, status=400)
        try:
            program = Program.objects.select_related('department').get(id=int(program_id))
        except (Program.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'status': 'error', 'message': 'Program not found'}, status=404)

        try:
            year_val = int(year)
        except (ValueError, TypeError):
            year_val = year  # allow non-numeric year labels, matched as string below

        allocations = list(CourseAllocation.objects.select_related(
            'program_course', 'lecturer', 'student_group',
        ).filter(program_id=program.id))
        allocations = [
            a for a in allocations
            if (getattr(a.program_course, 'year', None) or _get_year_value(a) or 0) == year_val
        ]

        blocks = _resolve_schedule_blocks_for_allocations(allocations, model=_scoped_model(_resolve_scope(request)))
        analysis = _analyze_weekly_blocks(blocks)
        days = _serialize_day_blocks(analysis['by_day'], [
            'course_code', 'course_name', 'lecturer', 'student_group', 'venue',
        ])

        return JsonResponse({
            'status': 'success',
            'program_id': program.id,
            'program_name': program.name,
            'department': program.department.name if program.department else '',
            'year': year_val,
            'total_weekly_hours': analysis['total_hours'],
            'total_sessions': analysis['total_sessions'],
            'max_consecutive_length': analysis['max_run_length'],
            'max_consecutive_hours': analysis['max_run_hours'],
            'days': days,
        })

    except Exception as e:
        logger.exception("Error in program_year_weekly_schedule_api")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9 — Email Department (unscheduled / published timetable + allocation)
# ═══════════════════════════════════════════════════════════════════════════
# Sends a department its unscheduled timetable, latest published (scheduled)
# timetable, and/or current course-allocation PDF — attached to a message the
# operator writes on the page — to that department's COD and COD Admin(s).
#
# Recipients are resolved the same way the rest of the system resolves "who
# administers this department": via OrgRole.department + the canonical
# Role.COD / Role.COD_ADMIN groups (see admins/manage_cod.py, which is what
# actually creates these links when a COD account is created), falling back
# to Department.leader for the COD's own email if no OrgRole/group match
# exists yet (e.g. an older account created before OrgRole existed).
#
# The two timetable PDFs are NOT rebuilt from scratch here — this re-invokes
# export_unscheduled_pdf() / export_scheduled_pdf() above with GET forced to
# a single department_id, so the attached PDF is byte-for-byte the same
# report a user would get from the "Export" buttons on the Summary tab.
# Nothing here writes to Timetable/CourseAllocation, same as every other
# view in this module — this section only reads and emails.

def _resolve_department_email_recipients(department):
    """
    Returns {'cod': [...], 'cod_admins': [...]} — each entry
    {'name': str, 'email': str} — for the given Department, deduplicated
    and skipping any account with no email on file.
    """
    cod_qs = User.objects.filter(
        groups__name=Role.COD, org_role__department_id=department.id
    ).distinct()
    cod_admin_qs = User.objects.filter(
        groups__name=Role.COD_ADMIN, org_role__department_id=department.id
    ).distinct()

    cod_list = [
        {'name': u.get_full_name() or u.username, 'email': u.email}
        for u in cod_qs if u.email
    ]
    # Fallback: Department.leader is set at COD-creation time even before
    # OrgRole existed (see admins/manage_cod.py) — use it if the group
    # lookup above found nobody with an email.
    if not cod_list and department.leader_id and department.leader.email:
        u = department.leader
        cod_list = [{'name': u.get_full_name() or u.username, 'email': u.email}]

    cod_admin_list = [
        {'name': u.get_full_name() or u.username, 'email': u.email}
        for u in cod_admin_qs if u.email
    ]

    return {'cod': cod_list, 'cod_admins': cod_admin_list}


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def department_email_recipients_api(request):
    """Preview endpoint — resolves and returns the COD/COD Admin recipients
    for a department, so the Email tab can show who a message will go to
    before the user hits Send."""
    department_id = request.GET.get('department_id')
    if not department_id:
        return JsonResponse({'status': 'error', 'message': 'department_id is required.'}, status=400)

    department = Department.objects.filter(id=department_id).select_related('leader').first()
    if not department:
        return JsonResponse({'status': 'error', 'message': 'Department not found.'}, status=404)

    recipients = _resolve_department_email_recipients(department)
    return JsonResponse({
        'status': 'success',
        'department': department.name,
        'cod': recipients['cod'],
        'cod_admins': recipients['cod_admins'],
    })


def _pdf_bytes_scoped(view_func, request, department_id):
    """
    Re-run one of this module's own scoped PDF-export views with GET forced
    to a single department_id and return (bytes, filename) instead of the
    HttpResponse it normally returns — lets the email view attach the exact
    same report the on-screen "Export" buttons produce, without duplicating
    any report-building logic. Restores request.GET afterwards.
    """
    original_get = request.GET
    qd = QueryDict(mutable=True)
    qd['department_id'] = str(department_id)
    # Carry the page's exam/timetable scope through to the re-run view —
    # _resolve_scope() would otherwise fall back to the session value, which
    # is usually still correct (set by the original page load) but there's
    # no reason to rely on that when the current request already has it.
    incoming_scope = original_get.get('scope') or request.POST.get('scope')
    if incoming_scope:
        qd['scope'] = incoming_scope
    request.GET = qd
    try:
        response = view_func(request)
    finally:
        request.GET = original_get

    filename = 'report.pdf'
    match = re.search(r'filename="([^"]+)"', response.get('Content-Disposition', ''))
    if match:
        filename = match.group(1)
    return response.content, filename


def _build_department_email_attachments(request, department, include_unscheduled, include_scheduled,
                                          include_allocation, include_department_timetable):
    """
    Build the (filename, bytes, mimetype) attachment list for ONE department,
    honouring the same four checkboxes as the Email tab. Shared by the
    single-department send and the "send to all departments" bulk send so
    both produce identical per-department PDFs.
    Returns (attachments, notes) — notes are non-fatal "couldn't attach X"
    strings to surface to the operator even when other attachments succeed.
    """
    attachments = []  # list of (filename, bytes, mimetype)
    notes = []

    if include_unscheduled:
        try:
            content, _fn = _pdf_bytes_scoped(export_unscheduled_pdf, request, department.id)
            attachments.append((f"{department.name} - Unscheduled Timetable.pdf", content, 'application/pdf'))
        except Exception as exc:
            logger.exception("send_department_timetable_email: failed building unscheduled PDF")
            notes.append(f"Could not attach the unscheduled timetable ({exc}).")

    if include_scheduled:
        # Attach the actual latest PUBLISHED Regular Timetable PDF — the
        # same record/file the public Published Timetables registry shows
        # at /published/timetables/?section=regular — rather than
        # regenerating a live PDF from current Timetable rows. This is a
        # single university-wide document (not department-scoped), so every
        # department that gets this attachment receives the identical file.
        from export_import.models import PDFDocument
        latest_doc = (
            PDFDocument.objects
            .filter(document_type='REGULAR', status='PUBLISHED', is_latest=True)
            .order_by('-uploaded_at')
            .first()
        )
        if latest_doc and latest_doc.pdf_file:
            try:
                latest_doc.pdf_file.open('rb')
                file_bytes = latest_doc.pdf_file.read()
            finally:
                latest_doc.pdf_file.close()
            attachments.append((
                f"Latest Published Timetable - {latest_doc.academic_year} Sem {latest_doc.semester} (v{latest_doc.version}).pdf",
                file_bytes, 'application/pdf',
            ))
        else:
            notes.append(
                "No published Regular Timetable is on file yet — publish one via the "
                "Published Timetables page (Regular section) before sending."
            )

    if include_department_timetable:
        # The course_management "Department Timetable" module's own
        # letterhead PDF (DEPARTMENTAL TIMETABLE) — distinct from
        # export_scheduled_pdf above: it's grouped/labelled per-department
        # with rich cells (course + lecturer + program) and appends an
        # UNSCHEDULED COURSES table for this department underneath, so it
        # doubles as the department's single at-a-glance document.
        from course_management.department_timetable import export_department_timetable_pdf
        try:
            content, _fn = _pdf_bytes_scoped(export_department_timetable_pdf, request, department.id)
            attachments.append((f"{department.name} - Departmental Timetable.pdf", content, 'application/pdf'))
        except Exception as exc:
            logger.exception("send_department_timetable_email: failed building departmental timetable PDF")
            notes.append(f"Could not attach the departmental timetable ({exc}).")

    if include_allocation:
        run = (
            AllocationPdfRun.objects
            .filter(
                department=department,
                scope=AllocationPdfRun.SCOPE_MAIN,
                is_current=True,
                status=AllocationPdfRun.STATUS_READY,
            )
            .exclude(file='')
            .first()
        )
        if run and run.file:
            try:
                run.file.open('rb')
                file_bytes = run.file.read()
            finally:
                run.file.close()
            attachments.append((f"{department.name} - Course Allocation.pdf", file_bytes, 'application/pdf'))
        else:
            notes.append(
                f"No current course-allocation PDF is on file yet for {department.name} — "
                f"generate one first from the COD Allocations panel."
            )

    return attachments, notes


def _send_one_department_email(request, department, subject, body, sender_name,
                                 include_unscheduled, include_scheduled,
                                 include_allocation, include_department_timetable):
    """
    Resolve recipients, build attachments, and send for ONE department.
    Returns a dict describing the outcome — never raises, so a bulk "send to
    all" loop can call this per-department without one failure aborting the
    rest of the batch.
    """
    recipients = _resolve_department_email_recipients(department)
    to_emails = list(dict.fromkeys(
        [r['email'] for r in recipients['cod']] + [r['email'] for r in recipients['cod_admins']]
    ))
    if not to_emails:
        return {
            'department': department.name,
            'status': 'skipped',
            'reason': 'No COD or COD Admin email on file.',
        }

    attachments, notes = _build_department_email_attachments(
        request, department, include_unscheduled, include_scheduled,
        include_allocation, include_department_timetable,
    )
    if not attachments:
        return {
            'department': department.name,
            'status': 'skipped',
            'reason': 'None of the selected attachments could be produced. ' + ' '.join(notes),
        }

    full_body = f"{body}\n\n— Sent via the Timetable Analysis & Reports dashboard by {sender_name}."
    email = EmailMessage(
        subject=subject,
        body=full_body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=to_emails,
    )
    for filename, content, mimetype in attachments:
        email.attach(filename, content, mimetype)

    try:
        email.send(fail_silently=False)
    except Exception as exc:
        logger.exception("send_department_timetable_email: send() failed for %s", department.name)
        return {
            'department': department.name,
            'status': 'error',
            'reason': f'Email failed to send: {exc}',
        }

    return {
        'department': department.name,
        'status': 'sent',
        'recipients': to_emails,
        'attachments': [a[0] for a in attachments],
        'notes': notes,
    }


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def send_department_timetable_email(request):
    """
    Compose and send an email to a department's COD + COD Admin(s),
    attaching any combination of:
      - the department's unscheduled-timetable PDF
      - the department's latest published (scheduled) timetable PDF
      - the department's own "Departmental Timetable" letterhead PDF
      - the department's current course-allocation PDF
        (AllocationPdfRun, scope='main', is_current=True, status='ready' —
        the same file the COD's own allocation panel links to)

    department_id='all' fans this out to EVERY department in the system —
    each one still gets its own individually-scoped PDFs (nobody receives
    another department's timetable), sent as its own separate email so a
    bounced/skipped department never blocks the rest. The response
    summarizes how many sent vs. were skipped and why.
    """
    department_id = request.POST.get('department_id')
    subject = (request.POST.get('subject') or '').strip()
    body = (request.POST.get('body') or '').strip()
    include_unscheduled = request.POST.get('include_unscheduled') == '1'
    include_scheduled = request.POST.get('include_scheduled') == '1'
    include_allocation = request.POST.get('include_allocation') == '1'
    include_department_timetable = request.POST.get('include_department_timetable') == '1'

    if not department_id:
        return JsonResponse({'status': 'error', 'message': 'Select a department.'}, status=400)

    if not subject or not body:
        return JsonResponse({'status': 'error', 'message': 'Subject and message body are both required.'}, status=400)

    if not (include_unscheduled or include_scheduled or include_allocation or include_department_timetable):
        return JsonResponse({'status': 'error', 'message': 'Select at least one attachment to send.'}, status=400)

    # "Latest published timetable" and "Departmental Timetable" attachments
    # are built from a published-PDF registry and a departmental-timetable
    # module that only exist for the regular timetable — there's no exam
    # equivalent of either yet, so refuse rather than silently attaching
    # the wrong (regular) document to an exam-scope email.
    exam_or_tt_scope = _resolve_scope(request)
    if exam_or_tt_scope == SCOPE_EXAM and (include_scheduled or include_department_timetable):
        return JsonResponse({
            'status': 'error',
            'message': (
                "The published-timetable and Departmental Timetable attachments are only "
                "available for the regular timetable scope — they have no exam equivalent yet. "
                "Deselect them, or use the Unscheduled/Course-Allocation attachments for exams."
            ),
        }, status=400)

    sender_name = _full_name(request)

    # ─────────────────────────────────────────────────────────────
    # Bulk path: send to every department, one email each.
    # ─────────────────────────────────────────────────────────────
    if department_id == 'all':
        departments = list(Department.objects.order_by('name'))
        if not departments:
            return JsonResponse({'status': 'error', 'message': 'No departments exist.'}, status=400)

        results = [
            _send_one_department_email(
                request, department, subject, body, sender_name,
                include_unscheduled, include_scheduled,
                include_allocation, include_department_timetable,
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
    # Single-department path (unchanged behaviour/response shape).
    # ─────────────────────────────────────────────────────────────
    department = Department.objects.filter(id=department_id).select_related('leader').first()
    if not department:
        return JsonResponse({'status': 'error', 'message': 'Department not found.'}, status=404)

    result = _send_one_department_email(
        request, department, subject, body, sender_name,
        include_unscheduled, include_scheduled,
        include_allocation, include_department_timetable,
    )

    if result['status'] == 'skipped':
        return JsonResponse({'status': 'error', 'message': result['reason']}, status=400)
    if result['status'] == 'error':
        return JsonResponse({'status': 'error', 'message': result['reason']}, status=500)

    return JsonResponse({
        'status': 'success',
        'message': f"Email sent to {', '.join(result['recipients'])}.",
        'recipients': result['recipients'],
        'attachments': result['attachments'],
        'notes': result['notes'],
    })