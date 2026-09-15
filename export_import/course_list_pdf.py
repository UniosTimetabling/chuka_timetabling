import datetime
import os
import uuid
from io import BytesIO
from collections import defaultdict

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle,
    Paragraph, Spacer, HRFlowable
)

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, Http404
from django.conf import settings

from faculty_management.models import Faculty
from department_management.models import Department
from course_allocation.models import CourseAllocation, LabAllocation
from export_import.models import TimetablePdfTemplate
from core.group_required import group_required


# ─── helpers ──────────────────────────────────────────────────────────────────

def _truncate(text, max_len=60):
    if not text:
        return "-"
    text = str(text)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _user_pdf_dir(user):
    """Return (and create) a per-user output folder that is NOT served publicly."""
    base = os.path.join(settings.MEDIA_ROOT, "course_list_pdfs", str(user.pk))
    os.makedirs(base, exist_ok=True)
    return base


def _unique_pdf_path(user):
    """Each call creates a brand-new file; old ones for the same user are purged."""
    folder = _user_pdf_dir(user)

    # Remove stale PDFs for this user so the folder stays clean
    for old_file in os.listdir(folder):
        if old_file.endswith(".pdf"):
            try:
                os.remove(os.path.join(folder, old_file))
            except OSError:
                pass

    filename = f"course_allocations_{datetime.date.today().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}.pdf"
    return os.path.join(folder, filename), filename


# ─── PDF builder ──────────────────────────────────────────────────────────────

class PDFReportGenerator:
    """
    Polished PDF Generator matching Chuka University official letterhead
    with clean Excel-green framing across the A4 landscape canvas.
    """

    def __init__(self, request, grouped_allocations, lab_allocations,
                 campus_grouped_list, odel_allocations, today, user_dept):
        self.request = request
        self.grouped_allocations = grouped_allocations
        self.lab_allocations = lab_allocations
        self.campus_grouped_list = campus_grouped_list
        self.odel_allocations = odel_allocations
        self.today = today
        self.user_dept = user_dept
        self.doc = None

        self.template_config = TimetablePdfTemplate.objects.first()
        if not self.template_config:
            class _Mock:
                university_name     = "CHUKA UNIVERSITY"
                motto_latin         = "Sapientia divitia est"
                motto_swahili       = "Akili ni Mali"
                directorate_name    = "DIRECTORATE OF EXAMINATIONS AND TIMETABLING"
                telephone           = "020-2310512/18"
                address             = "P. O. Box 109-60400, Chuka"
                email               = "extt@chuka.ac.ke"
                website             = "https://www.chuka.ac.ke"
                university_logo     = None
                director_label      = "DIRECTOR, EXAMINATIONS AND TIMETABLING"
            self.template_config = _Mock()

        self.colors = {
            'header':    colors.HexColor('#1E7145'),
            'grid':      colors.HexColor('#D0E1D4'),
            'alt_row':   colors.HexColor('#F4F9F5'),
            'text_dark': colors.HexColor('#111111'),
        }

        self.styles = getSampleStyleSheet()
        self._setup_styles()

    # ── styles ────────────────────────────────────────────────────────────────

    def _setup_styles(self):
        add = self.styles.add
        add(ParagraphStyle('ReportTitle',      parent=self.styles['Heading2'],
                           fontSize=15, textColor=self.colors['header'],
                           alignment=TA_CENTER, spaceBefore=10, spaceAfter=15,
                           fontName="Helvetica-Bold"))
        add(ParagraphStyle('SectionHeaderText', parent=self.styles['Normal'],
                           fontSize=12, textColor=self.colors['text_dark'],
                           alignment=TA_LEFT, fontName="Helvetica-Bold",
                           spaceBefore=14, spaceAfter=6))
        add(ParagraphStyle('TableCell',        parent=self.styles['Normal'],
                           fontSize=10, leading=14,
                           textColor=self.colors['text_dark'], fontName="Helvetica",
                           wordWrap='CJK', spaceAfter=2))
        add(ParagraphStyle('TableHeader',      parent=self.styles['Normal'],
                           fontSize=10, textColor=colors.white, leading=14,
                           alignment=TA_CENTER, fontName="Helvetica-Bold",
                           wordWrap='CJK'))

    # ── letterhead ────────────────────────────────────────────────────────────

    def _build_letterhead_header(self, elements):
        no_pad = TableStyle([
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
            ("TOPPADDING",    (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ])

        uni_sty = ParagraphStyle("UniName", parent=self.styles["Normal"],
                                 fontSize=15, fontName="Helvetica-Bold",
                                 alignment=1, spaceAfter=2)
        mot_sty = ParagraphStyle("Motto",   parent=self.styles["Normal"],
                                 fontSize=8.5, fontName="Helvetica",
                                 alignment=1, spaceAfter=2)
        dir_sty = ParagraphStyle("Dir",     parent=self.styles["Normal"],
                                 fontSize=9.5, fontName="Helvetica-Bold",
                                 alignment=1, spaceAfter=2)
        con_l   = ParagraphStyle("ConL",    parent=self.styles["Normal"],
                                 fontSize=8, fontName="Helvetica", alignment=0, spaceAfter=1)
        con_r   = ParagraphStyle("ConR",    parent=con_l, alignment=2)
        ref_l   = ParagraphStyle("RefL",    parent=self.styles["Normal"],
                                 fontSize=8.5, fontName="Helvetica-Bold", alignment=0,
                                 spaceBefore=4, spaceAfter=4)
        ref_r   = ParagraphStyle("RefR",    parent=ref_l, alignment=2)

        try:
            if (self.template_config.university_logo
                    and hasattr(self.template_config.university_logo, "path")):
                from reportlab.platypus import Image as RLImage
                logo = RLImage(self.template_config.university_logo.path,
                               width=1.0 * inch, height=1.0 * inch)
                logo.hAlign = "CENTER"
                elements.append(logo)
        except Exception as exc:
            pass  # logo is optional

        elements.append(Paragraph(self.template_config.university_name.upper(), uni_sty))
        elements.append(Paragraph(
            f"Knowledge is Wealth <i>({self.template_config.motto_latin})</i> "
            f"{self.template_config.motto_swahili}", mot_sty))
        elements.append(Paragraph(self.template_config.directorate_name, dir_sty))
        elements.append(Spacer(1, 3))

        col_w = [385, 385]
        for left_text, right_text in [
            (f"Telephones: {self.template_config.telephone}", self.template_config.address),
            (f"Direct Line &amp; Email: <font color='blue'>{self.template_config.email}</font>",
             f"Website: {self.template_config.website}"),
        ]:
            row = Table([[Paragraph(left_text, con_l), Paragraph(right_text, con_r)]],
                        colWidths=col_w, hAlign="LEFT")
            row.setStyle(no_pad)
            elements.append(row)

        elements.append(Spacer(1, 2))
        elements.append(HRFlowable(width="100%", thickness=0.8, color=colors.black))
        elements.append(Spacer(1, 2))

        ref_num = f"CU/EXTT/ALLOC/{datetime.date.today().strftime('%d.%m.%Y')}"
        ref_row = Table(
            [[Paragraph(f"<b>Ref: {ref_num}</b>", ref_l),
              Paragraph(f"<b>Date: {self.today}</b>", ref_r)]],
            colWidths=col_w, hAlign="LEFT")
        ref_row.setStyle(no_pad)
        elements.append(ref_row)
        elements.append(Spacer(1, 5))

    # ── footer ────────────────────────────────────────────────────────────────

    def _draw_footer(self, canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor('#555555'))
        canvas.setFont("Helvetica", 7.5)
        page_num = canvas.getPageNumber()
        canvas.drawCentredString(
            doc.pagesize[0] / 2.0,
            doc.bottomMargin - 15,
            f"Official Course Allocation Report — Generated on {self.today} | Page {page_num}"
        )
        canvas.restoreState()

    # ── table helper ──────────────────────────────────────────────────────────

    def _create_excel_table(self, headers, data):
        if not data:
            return None

        table_data = [
            [Paragraph(f"<b>{h}</b>", self.styles['TableHeader']) for h in headers]
        ]
        for row in data:
            # No truncation — let Paragraph + column width handle wrapping naturally
            table_data.append(
                [Paragraph(str(cell) if cell else '-', self.styles['TableCell']) for cell in row]
            )

        # A4 landscape usable width = 841 - 35 - 35 = 771pt
        available_width = 771
        col_count = len(headers)

        if col_count == 6:
            # Code | Course Name | Origin Dept | Program | Lecturer | Students
            col_widths = [70, 200, 115, 185, 150, 51]
        elif col_count == 5:
            # Code | Course Name | Program | Lecturer | Students
            col_widths = [75, 220, 210, 186, 80]
        else:
            col_widths = [available_width / col_count] * col_count

        tbl = Table(table_data, colWidths=col_widths, repeatRows=1,
                    hAlign='LEFT', splitByRow=True)
        style = [
            ('BACKGROUND',    (0, 0), (-1, 0),  self.colors['header']),
            ('ALIGN',         (0, 0), (-1, 0),  'CENTER'),
            ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING',    (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING',   (0, 0), (-1, -1), 7),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 7),
            ('GRID',          (0, 0), (-1, -1), 0.5, self.colors['grid']),
            ('BOX',           (0, 0), (-1, -1), 1,   self.colors['header']),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),
             [colors.white, self.colors['alt_row']]),
        ]
        tbl.setStyle(TableStyle(style))
        return tbl

    # ── main build ────────────────────────────────────────────────────────────

    def generate_pdf(self, file_path):
        """Write the PDF directly to *file_path* and return the raw bytes."""
        buffer = BytesIO()
        self.doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            rightMargin=35, leftMargin=35,
            topMargin=35,   bottomMargin=45,
        )

        story = []
        self._build_letterhead_header(story)
        story.append(Paragraph(
            "MASTER COURSE ALLOCATIONS SCHEDULE REGISTER LIST",
            self.styles['ReportTitle']))

        # 1. Main faculty / department allocations
        for fac in self.grouped_allocations:
            fac_lbl = f"FACULTY OF {fac['faculty'].upper()}"
            for dept in fac['departments']:
                dept_lbl = f"{fac_lbl} — DEPARTMENT OF {dept['department'].upper()}"
                headers = ['Course Code', 'Course Name', 'Origin Dept.',
                           'Program Target Allocation', 'Lecturer Assigned', 'Students']
                rows = []
                for day_entry in dept.get('days', []):
                    for alloc in day_entry.get('allocations', []):
                        prog = alloc.get('program')
                        prog_name = (prog.name if hasattr(prog, 'name')
                                     else (prog.get('name', '-') if isinstance(prog, dict)
                                           else str(prog or '-')))
                        rows.append([
                            alloc.get('course_code', '-'),
                            alloc.get('course_name', '-'),
                            alloc.get('origin_department', '-'),
                            prog_name,
                            alloc.get('lecturer', '-'),
                            alloc.get('number_of_students', 0),
                        ])

                if rows:
                    story.append(Paragraph(dept_lbl, self.styles['SectionHeaderText']))
                    tbl = self._create_excel_table(headers, rows)
                    if tbl:
                        story.append(tbl)
                        story.append(Spacer(1, 10))

                # Lab sub-section
                lab_headers = ['Course Code', 'Course Name', 'Program Target',
                               'Lab Venue', 'Lecturer Assigned', 'Students']
                lab_rows = []
                for lab in self.lab_allocations:
                    try:
                        if lab.program_course.program.department.name == dept['department']:
                            lab_rows.append([
                                lab.program_course.course_code  if lab.program_course else '-',
                                lab.program_course.course_name  if lab.program_course else '-',
                                lab.program_course.program.name if (lab.program_course and lab.program_course.program) else '-',
                                lab.venue.code if hasattr(lab.venue, 'code') else str(lab.venue or '-'),
                                lab.lecturer.display_name if lab.lecturer else 'Unassigned',
                                lab.number_of_students or 0,
                            ])
                    except Exception:
                        pass

                if lab_rows:
                    story.append(Paragraph(
                        f"{dept_lbl} — (LABORATORY TECHNICAL ALLOCATIONS)",
                        self.styles['SectionHeaderText']))
                    ltbl = self._create_excel_table(lab_headers, lab_rows)
                    if ltbl:
                        story.append(ltbl)
                        story.append(Spacer(1, 10))

        # 2. Campus allocations
        for campus_entry in self.campus_grouped_list:
            c_lbl = f"CAMPUS: {campus_entry['campus'].upper()} SYSTEM ALLOCATIONS"
            for dept in campus_entry.get('departments', []):
                c_dept_lbl = f"{c_lbl} — {dept['department'].upper()}"
                c_headers = ['Course Code', 'Course Name', 'Origin Dept.',
                             'Program Target', 'Lecturer Assigned', 'Students']
                c_rows = []
                for alloc in dept.get('allocations', []):
                    prog = alloc.get('program')
                    prog_name = (prog.name if hasattr(prog, 'name')
                                 else (prog.get('name', '-') if isinstance(prog, dict)
                                       else str(prog or '-')))
                    c_rows.append([
                        alloc.get('course_code', '-'),
                        alloc.get('course_name', '-'),
                        alloc.get('origin_department', '-'),
                        prog_name,
                        alloc.get('lecturer', '-'),
                        alloc.get('number_of_students', 0),
                    ])

                if c_rows:
                    story.append(Paragraph(c_dept_lbl, self.styles['SectionHeaderText']))
                    ctbl = self._create_excel_table(c_headers, c_rows)
                    if ctbl:
                        story.append(ctbl)
                        story.append(Spacer(1, 10))

        # 3. ODEL allocations
        if self.odel_allocations:
            story.append(Paragraph(
                "ODEL (DISTANCE & ELEVATED LEARNING) ALLOCATIONS",
                self.styles['SectionHeaderText']))
            odel_headers = ['Course Code', 'Course Name', 'Program Target',
                            'Lecturer Assigned', 'Students Group']
            odel_rows = []
            for alloc in self.odel_allocations:
                try:
                    odel_rows.append([
                        alloc.program_course.course_code  if alloc.program_course else '-',
                        alloc.program_course.course_name  if alloc.program_course else '-',
                        alloc.program_course.program.name if (alloc.program_course and alloc.program_course.program) else '-',
                        alloc.lecturer.display_name if alloc.lecturer else 'Unassigned',
                        alloc.number_of_students or 0,
                    ])
                except Exception:
                    pass

            if odel_rows:
                otbl = self._create_excel_table(odel_headers, odel_rows)
                if otbl:
                    story.append(otbl)
                    story.append(Spacer(1, 10))

        # Signature block
        story.append(Spacer(1, 15))
        story.append(Paragraph("<b>Prepared and Issued By:</b>", self.styles['SectionHeaderText']))
        story.append(Spacer(1, 30))
        dir_label = (self.template_config.director_label
                     if hasattr(self.template_config, 'director_label')
                     else 'DIRECTOR, EXAMINATIONS AND TIMETABLING')
        story.append(Paragraph(f"<b>{dir_label.upper()}</b>", self.styles['SectionHeaderText']))

        self.doc.build(story,
                       onFirstPage=self._draw_footer,
                       onLaterPages=self._draw_footer)

        pdf_bytes = buffer.getvalue()
        buffer.close()

        # Persist to disk
        with open(file_path, 'wb') as f:
            f.write(pdf_bytes)

        return pdf_bytes


# ─── data helpers ─────────────────────────────────────────────────────────────

def _build_grouped_allocations(alloc_qs):
    """
    Convert a CourseAllocation queryset into the nested structure expected by
    PDFReportGenerator:  faculty → department → days → allocations
    """
    from collections import defaultdict

    fac_map = defaultdict(lambda: defaultdict(list))
    for alloc in alloc_qs:
        try:
            dept = alloc.program_course.program.department
            fac  = dept.faculty
            fac_name  = fac.name  if fac  else "Unknown Faculty"
            dept_name = dept.name if dept else "Unknown Department"
            prog      = alloc.program_course.program
            fac_map[fac_name][dept_name].append({
                'course_code':        alloc.program_course.course_code  if alloc.program_course else '-',
                'course_name':        alloc.program_course.course_name  if alloc.program_course else '-',
                'origin_department':  dept_name,
                'program':            prog,
                'lecturer':           alloc.lecturer.display_name if alloc.lecturer else 'Unassigned',
                'number_of_students': alloc.number_of_students or 0,
            })
        except Exception:
            pass

    grouped = []
    for fac_name, depts in fac_map.items():
        dept_list = []
        for dept_name, allocs in depts.items():
            dept_list.append({
                'department':  dept_name,
                'days': [{'allocations': allocs}],   # flat — no day split needed here
            })
        grouped.append({'faculty': fac_name, 'departments': dept_list})

    return grouped


# ─── views ────────────────────────────────────────────────────────────────────

@login_required
@group_required("Chairperson of Department", "COD Admins", "Timetabler", "Sudo")
def course_list_pdf_view(request):
    """
    Synchronously generates the PDF, saves it to a per-user folder on disk,
    and streams it back so the browser opens it inline (blob view).
    No caching, no threading, no progress overlay.
    """
    faculty_id    = request.GET.get('faculty_id')
    department_id = request.GET.get('department_id')

    # ── query ──
    alloc_qs = (CourseAllocation.objects
                .all()
                .select_related(
                    'program_course__program__department__faculty',
                    'lecturer'))

    if faculty_id:
        alloc_qs = alloc_qs.filter(
            program_course__program__department__faculty_id=faculty_id)
    if department_id:
        alloc_qs = alloc_qs.filter(
            program_course__program__department_id=department_id)

    # COD / COD Admin see only their own department. Superusers, Timetablers,
    # etc. see everything (or whatever faculty_id/department_id filters
    # they passed above).
    user = request.user
    from course_allocation.detect_user_department import detect_user_department
    detected_dept = None
    if user.groups.filter(name__in=["Chairperson of Department", "COD Admins"]).exists() and not user.is_superuser:
        detected_dept = detect_user_department(user)
        if detected_dept:
            alloc_qs = alloc_qs.filter(
                program_course__program__department=detected_dept)

    lab_qs = LabAllocation.objects.select_related(
        'program_course__program__department',
        'lecturer')

    grouped     = _build_grouped_allocations(alloc_qs)
    today_str   = datetime.date.today().strftime("%d-%B-%Y")

    if detected_dept:
        user_dept = detected_dept.name
    else:
        try:
            dept_obj = (user.lecturer.department
                        if hasattr(user, 'lecturer') and user.lecturer.department
                        else None)
            user_dept = dept_obj.name if dept_obj else "Academic Administration Registry"
        except Exception:
            user_dept = "Academic Administration Registry"

    # ── generate & save ──
    file_path, filename = _unique_pdf_path(user)

    generator = PDFReportGenerator(
        request           = request,
        grouped_allocations = grouped,
        lab_allocations   = list(lab_qs),
        campus_grouped_list = [],
        odel_allocations  = [],
        today             = today_str,
        user_dept         = user_dept,
    )
    pdf_bytes = generator.generate_pdf(file_path)

    # ── stream inline so the browser renders it as a blob ──
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    response['Content-Length'] = len(pdf_bytes)
    # No-store so it is never cached by the browser
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    return response