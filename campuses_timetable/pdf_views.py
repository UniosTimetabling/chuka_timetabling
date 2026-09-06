from core.rbac import allowed_roles, Role
from core.signed_download import validate_download_token
import io
import os
import logging
from datetime import datetime
from collections import defaultdict
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.views.decorators.http import require_GET, require_POST

logger = logging.getLogger(__name__)
from django.contrib import messages
from django.utils import timezone
from django.core.files.base import ContentFile
from django.conf import settings
from django.urls import reverse
from django.db.models import Q
from django.template.loader import render_to_string
from django.db.models import Q, Sum
# ReportLab imports
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

# Models
from .models import (
    CampusTimetable, 
    CampusExamTimetable, 
    CampusCourseAllocation,
    CampusPublishedTimetablePDF,
    CampusTimetableTemplate,
    Campus
)
from django.contrib.auth.models import User


# ===================== PDF GENERATION FUNCTIONS =====================

class CampusPDFGenerator:
    """Base class for Campus PDF generation"""
    
    def __init__(self, request=None, campus=None):
        self.request = request
        self.campus = campus
        self.template_config = CampusTimetableTemplate.get_template()
        self.current_date = timezone.now()
        self.styles = getSampleStyleSheet()
        self._setup_styles()
    
    def _setup_styles(self):
        """Setup custom styles matching official university template (black/white, red title)"""
        # University name: bold, black, centered
        self.title_style = ParagraphStyle(
            'CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=16,
            spaceAfter=2,
            spaceBefore=2,
            alignment=1,
            fontName='Times-Bold',
            textColor=colors.black
        )

        # Directorate name: bold, black, centered
        self.subtitle_style = ParagraphStyle(
            'SubtitleStyle',
            parent=self.styles['Heading2'],
            fontSize=12,
            spaceAfter=4,
            spaceBefore=2,
            alignment=1,
            fontName='Times-Bold',
            textColor=colors.black
        )

        # Small contact/header info: black, centered
        self.header_style = ParagraphStyle(
            'HeaderStyle',
            parent=self.styles['Normal'],
            fontSize=9,
            spaceAfter=2,
            alignment=1,
            textColor=colors.black
        )

        # Date/day section header: bold, black, left-aligned
        self.date_header_style = ParagraphStyle(
            'DateHeader',
            parent=self.styles['Heading2'],
            fontSize=11,
            spaceAfter=6,
            spaceBefore=4,
            alignment=0,
            fontName='Times-Bold',
            textColor=colors.black,
        )

        # Table cell: black, centered
        self.cell_style = ParagraphStyle(
            'TableCell',
            parent=self.styles['Normal'],
            fontSize=10,
            fontName='Times-Roman',
            textColor=colors.black,
            alignment=1,
            wordWrap='CJK',
            leading=11
        )

        # Venue cell: bold, black, centered
        self.venue_cell_style = ParagraphStyle(
            'VenueCell',
            parent=self.styles['Normal'],
            fontSize=10,
            fontName='Times-Bold',
            textColor=colors.black,
            alignment=1,
            wordWrap='CJK',
            leading=11
        )

        # Campus/secondary cell: normal, black, left-aligned
        self.campus_cell_style = ParagraphStyle(
            'CampusCell',
            parent=self.styles['Normal'],
            fontSize=9,
            fontName='Times-Roman',
            textColor=colors.black,
            alignment=1,
            wordWrap='CJK',
            leading=10
        )

        # Page header: small, black, left-aligned
        self.page_header_style = ParagraphStyle(
            'PageHeader',
            parent=self.styles['Normal'],
            fontSize=7,
            alignment=0,
            textColor=colors.black,
            spaceAfter=3
        )

        # Timetable title: bold, red, centered (like the template PDF title)
        self.timetable_title_style = ParagraphStyle(
            'TimetableTitle',
            parent=self.styles['Heading1'],
            fontSize=14,
            spaceAfter=6,
            spaceBefore=6,
            alignment=1,
            fontName='Times-Bold',
            textColor=colors.black
        )

        # Ref/date line: normal, black
        self.ref_style = ParagraphStyle(
            'RefStyle',
            parent=self.styles['Normal'],
            fontSize=10,
            fontName='Times-Bold',
            textColor=colors.black,
            alignment=0,
        )
        self.date_right_style = ParagraphStyle(
            'DateRightStyle',
            parent=self.styles['Normal'],
            fontSize=10,
            fontName='Times-Bold',
            textColor=colors.black,
            alignment=2,
        )

        # Header cell style: bold black, centred — used for Paragraph objects in table header rows
        self.header_cell_style = ParagraphStyle(
            'HeaderCell',
            parent=self.styles['Normal'],
            fontSize=10,
            fontName='Times-Bold',
            textColor=colors.white,
            alignment=1,
            wordWrap='CJK',
            leading=12
        )

        # NB/footer label style
        self.nb_label_style = ParagraphStyle(
            'NBLabel',
            parent=self.styles['Normal'],
            fontSize=10,
            fontName='Times-Bold',
            textColor=colors.black,
            alignment=0,
        )
        self.nb_text_style = ParagraphStyle(
            'NBText',
            parent=self.styles['Normal'],
            fontSize=9,
            fontName='Times-Roman',
            textColor=colors.black,
            alignment=0,
            leftIndent=8
        )
    
    def get_logo(self):
        """Get logo for PDF"""
        if self.template_config.university_logo and self.template_config.university_logo.path:
            try:
                if os.path.exists(self.template_config.university_logo.path):
                    img = Image(self.template_config.university_logo.path, width=1.0*inch, height=1.0*inch)
                    img.hAlign = 'CENTER'
                    return img
            except (OSError, ValueError) as e:
                logger.warning("Could not load university logo for PDF: %s", e)
        return None
    
    def add_header(self, elements, timetable_type):
        """Add university header matching the official template layout.

        Layout (mirroring the Chuka University template PDF):
          Row 1 (top bar): [Left: phone/address] [Centre: logo] [Right: PO Box/email/website]
          Row 2: University name bold centred
          Row 3: Motto italic centred
          Row 4: Directorate name bold centred
          Horizontal rule
          Row 5: Ref left  |  Date right
          Horizontal rule
        """
        from reportlab.platypus import HRFlowable

        # ── Top contact-bar with logo ──────────────────────────────────────────
        left_contact = ParagraphStyle(
            'LeftContact', parent=self.styles['Normal'],
            fontSize=9, alignment=0, textColor=colors.black
        )
        right_contact = ParagraphStyle(
            'RightContact', parent=self.styles['Normal'],
            fontSize=9, alignment=2, textColor=colors.black
        )

        left_text = (
            f"Telephones: {self.template_config.telephone}<br/>"
            f"Direct Line:"
        )
        right_text = (
            f"{self.template_config.address}<br/>"
            f"Email: {self.template_config.email} "
            f"Website: {self.template_config.website}"
        )

        logo = self.get_logo()
        logo_cell = logo if logo else Paragraph("", self.header_style)

        top_bar = Table(
            [[Paragraph(left_text, left_contact), logo_cell, Paragraph(right_text, right_contact)]],
            colWidths=['35%', '30%', '35%'],
            hAlign='CENTRE'
        )
        top_bar.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(top_bar)

        # ── University name ────────────────────────────────────────────────────
        elements.append(Paragraph(self.template_config.university_name, self.title_style))

        # ── Motto ──────────────────────────────────────────────────────────────
        motto_style = ParagraphStyle(
            'MottoStyle', parent=self.styles['Normal'],
            fontSize=9, alignment=1, textColor=colors.black,
            fontName='Times-Italic', spaceAfter=2
        )
        motto = f"{self.template_config.motto_latin}  {self.template_config.motto_swahili}"
        elements.append(Paragraph(motto, motto_style))

        # ── Directorate ────────────────────────────────────────────────────────
        elements.append(Paragraph(self.template_config.directorate_name, self.subtitle_style))
        elements.append(Spacer(1, 6))

        # ── Thin horizontal rule ───────────────────────────────────────────────
        elements.append(HRFlowable(width='100%', thickness=0.8, color=colors.black))
        elements.append(Spacer(1, 4))

        # ── Ref  |  Date row ───────────────────────────────────────────────────
        ref_number = self.template_config.get_reference_number(
            self.current_date.strftime("%d-%b-%Y").upper()
        )
        date_str = self.current_date.strftime("%-d<super>th</super> %B, %Y")
        ref_date_table = Table(
            [[
                Paragraph(f"<b>Ref:</b> {ref_number}", self.ref_style),
                Paragraph(f"<b>Date:</b> {date_str}", self.date_right_style)
            ]],
            colWidths=['50%', '50%'],
            hAlign='CENTRE'
        )
        ref_date_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        elements.append(ref_date_table)

        # ── Second horizontal rule ─────────────────────────────────────────────
        elements.append(HRFlowable(width='100%', thickness=0.8, color=colors.black))
        elements.append(Spacer(1, 10))

        # ── Campus info (if applicable) ────────────────────────────────────────
        if self.campus:
            campus_info = f"Campus: {self.campus.name} ({self.campus.code})"
            elements.append(Paragraph(campus_info, self.subtitle_style))
            elements.append(Spacer(1, 6))

        return elements
    
    def add_footer(self, elements, prepared_by=""):
        """Add footer matching the official template (Prepared by + Director blocks)"""
        from reportlab.platypus import HRFlowable

        elements.append(Spacer(1, 20))

        # NB / Key section label (only label, content added before calling this)
        elements.append(Paragraph("<b>Prepared by:</b>", self.nb_label_style))
        elements.append(Spacer(1, 40))  # space for signature

        # Director block
        elements.append(Paragraph(
            f"<b>{self.template_config.directorate_name}</b>",
            self.nb_label_style
        ))
        elements.append(Paragraph(
            f"<b>{self.template_config.director_label}</b>",
            self.nb_label_style
        ))

        return elements


def generate_class_timetable_pdf_content(request=None, campus=None):
    """Generate Campus Class Timetable PDF content"""
    # Fetch data
    filters = {}
    if campus:
        filters['campus'] = campus
    
    entries = CampusTimetable.objects.select_related(
        'course_allocation__program',
        'course_allocation__lecturer',
        'campus'
    ).filter(**filters).order_by('day', 'start_time')
    
    if not entries.exists():
        return None, None
    
    generator = CampusPDFGenerator(request, campus)
    
    # Organize by day
    entries_by_day = defaultdict(list)
    for entry in entries:
        entries_by_day[entry.day].append(entry)
    
    # Sort days in week order
    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    sorted_days = [day for day in day_order if day in entries_by_day]
    
    # Create PDF buffer
    buffer = io.BytesIO()
    
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=25,
        leftMargin=25,
        topMargin=50,
        bottomMargin=50,
        title=f"Campus Class Timetable"
    )
    
    elements = []
    
    # Add header
    elements = generator.add_header(elements, "CLASS")
    
    # Timetable title (red, centred — matching template)
    title = generator.template_config.title_format.format(timetable_type="CAMPUS CLASS")
    elements.append(Paragraph(f"{title}", generator.timetable_title_style))
    elements.append(Spacer(1, 20))
    
    # Page tracking
    current_page = 1
    total_pages = len(sorted_days) + 2
    
    # Generate tables for each day
    for day_idx, day in enumerate(sorted_days):
        # Page header
        page_header = generator.template_config.get_page_header(
            current_page, total_pages
        )
        if page_header:
            elements.append(Paragraph(page_header, generator.page_header_style))
            elements.append(Spacer(1, 5))
        
        # Day header
        elements.append(Paragraph(day, generator.date_header_style))
        elements.append(Spacer(1, 8))
        
        # Get entries for this day
        day_entries = entries_by_day[day]
        
        # Table data
        table_data = [[
            Paragraph('<b>Time</b>', generator.header_cell_style),
            Paragraph('<b>Course Code</b>', generator.header_cell_style),
            Paragraph('<b>Course Name</b>', generator.header_cell_style),
            Paragraph('<b>Program</b>', generator.header_cell_style),
            Paragraph('<b>Lecturer</b>', generator.header_cell_style),
            Paragraph('<b>Campus</b>', generator.header_cell_style),
        ]]
        
        # Sort by time
        sorted_entries = sorted(day_entries, key=lambda e: e.start_time)
        
        for entry in sorted_entries:
            time_slot = f"{entry.start_time.strftime('%H:%M')} - {entry.end_time.strftime('%H:%M')}"
            row = [
                Paragraph(time_slot, generator.cell_style),
                Paragraph(entry.course_allocation.course_code, generator.cell_style),
                Paragraph(entry.course_allocation.course_name[:30], generator.cell_style),
                Paragraph(entry.course_allocation.program.name[:20] if entry.course_allocation.program else '-', generator.cell_style),
                Paragraph(entry.course_allocation.lecturer.display_name if entry.course_allocation.lecturer else 'Unassigned', generator.cell_style),
                Paragraph(entry.campus.code if entry.campus else '-', generator.campus_cell_style),
            ]
            table_data.append(row)
        
        # Create table
        col_widths = [70, 70, 120, 100, 100, 60]
        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        
        # Table styling — plain black/white matching official template
        table_style = TableStyle([
            # Header row: white text on black background
            ('BACKGROUND', (0, 0), (-1, 0), colors.black),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 0), (-1, 0), 'Times-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),

            # Grid: black borders
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BOX', (0, 0), (-1, -1), 1, colors.black),

            # Padding
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (1, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (1, 0), (-1, -1), 6),

            # Data rows: white background
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ])
        
        table.setStyle(table_style)
        elements.append(table)
        elements.append(Spacer(1, 20))
        
        # Page break if not last
        if day_idx < len(sorted_days) - 1:
            elements.append(PageBreak())
            current_page += 1
    
    # Key/Legend section
    elements.append(PageBreak())
    current_page += 1
    
    page_header = generator.template_config.get_page_header(
        current_page, total_pages
    )
    if page_header:
        elements.append(Paragraph(page_header, generator.page_header_style))
        elements.append(Spacer(1, 5))
    
    elements.append(Paragraph("<b>NB:</b>", generator.nb_label_style))
    elements.append(Paragraph("<b>KEY:</b>", generator.nb_label_style))
    elements.append(Spacer(1, 4))
    
    # Format key section
    key_text = generator.template_config.key_section.replace('KEY:', '').strip()
    for line in key_text.splitlines():
        line = line.strip()
        if line:
            elements.append(Paragraph(line, generator.nb_text_style))
    elements.append(Spacer(1, 20))
    
    # Add footer
    prepared_by = f"Campus Timetable Officer"
    if campus:
        prepared_by += f" - {campus.name}"
    elements = generator.add_footer(elements, prepared_by)
    
    # Build PDF
    doc.build(elements)
    
    # Get PDF content
    pdf_content = buffer.getvalue()
    buffer.close()
    
    # Generate filename
    campus_code = campus.code if campus else 'ALL'
    filename = f"campus_class_timetable_{campus_code}_{generator.current_date.strftime('%Y%m%d_%H%M')}.pdf"
    
    return pdf_content, filename


def generate_exam_timetable_pdf_content(request=None, campus=None):
    """Generate Campus Exam Timetable PDF content"""
    # Fetch data
    filters = {}
    if campus:
        filters['campus'] = campus
    
    entries = CampusExamTimetable.objects.select_related(
        'course_allocation__program',
        'course_allocation__lecturer',
        'campus'
    ).filter(**filters).order_by('date', 'start_time')
    
    if not entries.exists():
        return None, None
    
    generator = CampusPDFGenerator(request, campus)
    
    # Organize by date
    entries_by_date = defaultdict(list)
    for entry in entries:
        entries_by_date[entry.date].append(entry)
    
    sorted_dates = sorted(entries_by_date.keys())
    
    # Create PDF buffer
    buffer = io.BytesIO()
    
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=25,
        leftMargin=25,
        topMargin=50,
        bottomMargin=50,
        title=f"Campus Exam Timetable"
    )
    
    elements = []
    
    # Add header
    elements = generator.add_header(elements, "EXAM")
    
    # Timetable title (red, centred — matching template)
    title = generator.template_config.title_format.format(timetable_type="CAMPUS EXAMINATION")
    elements.append(Paragraph(f"{title}", generator.timetable_title_style))
    elements.append(Spacer(1, 20))
    
    # Page tracking
    current_page = 1
    total_pages = len(sorted_dates) + 2
    
    # Generate tables for each date
    for date_idx, date in enumerate(sorted_dates):
        # Page header
        page_header = generator.template_config.get_page_header(
            current_page, total_pages
        )
        if page_header:
            elements.append(Paragraph(page_header, generator.page_header_style))
            elements.append(Spacer(1, 5))
        
        # Date header
        date_str = date.strftime("%A, %d %B %Y")
        elements.append(Paragraph(date_str, generator.date_header_style))
        elements.append(Spacer(1, 8))
        
        # Get entries for this date
        date_entries = entries_by_date[date]
        
        # Table data
        table_data = [[
            Paragraph('<b>Time</b>', generator.header_cell_style),
            Paragraph('<b>Course Code</b>', generator.header_cell_style),
            Paragraph('<b>Course Name</b>', generator.header_cell_style),
            Paragraph('<b>Program</b>', generator.header_cell_style),
            Paragraph('<b>Lecturer</b>', generator.header_cell_style),
            Paragraph('<b>Campus</b>', generator.header_cell_style),
        ]]
        
        # Sort by time
        sorted_entries = sorted(date_entries, key=lambda e: e.start_time)
        
        for entry in sorted_entries:
            time_slot = f"{entry.start_time.strftime('%H:%M')} - {entry.end_time.strftime('%H:%M')}"
            row = [
                Paragraph(time_slot, generator.cell_style),
                Paragraph(entry.course_allocation.course_code, generator.cell_style),
                Paragraph(entry.course_allocation.course_name[:30], generator.cell_style),
                Paragraph(entry.course_allocation.program.name[:20] if entry.course_allocation.program else '-', generator.cell_style),
                Paragraph(entry.course_allocation.lecturer.display_name if entry.course_allocation.lecturer else 'Unassigned', generator.cell_style),
                Paragraph(entry.campus.code if entry.campus else '-', generator.campus_cell_style),
            ]
            table_data.append(row)
        
        # Create table
        col_widths = [70, 70, 120, 100, 100, 60]
        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        
        # Table styling — plain black/white matching official template
        table_style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.black),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 0), (-1, 0), 'Times-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),

            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BOX', (0, 0), (-1, -1), 1, colors.black),

            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (1, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (1, 0), (-1, -1), 6),

            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ])
        
        table.setStyle(table_style)
        elements.append(table)
        elements.append(Spacer(1, 20))
        
        # Page break if not last
        if date_idx < len(sorted_dates) - 1:
            elements.append(PageBreak())
            current_page += 1
    
    # Key/Legend section
    elements.append(PageBreak())
    current_page += 1
    
    page_header = generator.template_config.get_page_header(
        current_page, total_pages
    )
    if page_header:
        elements.append(Paragraph(page_header, generator.page_header_style))
        elements.append(Spacer(1, 5))
    
    elements.append(Paragraph("<b>NB:</b>", generator.nb_label_style))
    elements.append(Paragraph("<b>KEY:</b>", generator.nb_label_style))
    elements.append(Spacer(1, 4))

    key_text = generator.template_config.key_section.replace('KEY:', '').strip()
    for line in key_text.splitlines():
        line = line.strip()
        if line:
            elements.append(Paragraph(line, generator.nb_text_style))
    elements.append(Spacer(1, 20))
    
    # Add footer
    prepared_by = f"Examinations Officer"
    if campus:
        prepared_by += f" - {campus.name}"
    elements = generator.add_footer(elements, prepared_by)
    
    # Build PDF
    doc.build(elements)
    
    # Get PDF content
    pdf_content = buffer.getvalue()
    buffer.close()
    
    # Generate filename
    campus_code = campus.code if campus else 'ALL'
    filename = f"campus_exam_timetable_{campus_code}_{generator.current_date.strftime('%Y%m%d_%H%M')}.pdf"
    
    return pdf_content, filename


def generate_course_allocations_pdf_content(request=None, campus=None):
    """Generate Course Allocations PDF"""
    # Fetch data
    filters = {'approved_by_dvc': True, 'rejected_by_dvc': False}
    if campus:
        filters['campus'] = campus
    
    allocations = CampusCourseAllocation.objects.select_related(
        'program',
        'lecturer',
        'campus',
        'department',
        'origin_department'
    ).filter(**filters).order_by('program__name', 'course_code')
    
    if not allocations.exists():
        return None, None
    
    generator = CampusPDFGenerator(request, campus)
    
    # Organize by program
    by_program = defaultdict(list)
    for alloc in allocations:
        program_name = alloc.program.name if alloc.program else 'No Program'
        by_program[program_name].append(alloc)
    
    buffer = io.BytesIO()
    
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=25,
        leftMargin=25,
        topMargin=50,
        bottomMargin=50,
        title=f"Campus Course Allocations"
    )
    
    elements = []
    
    # Add header
    elements = generator.add_header(elements, "COURSE ALLOCATIONS")
    
    # Title
    elements.append(Paragraph("COURSE ALLOCATIONS REPORT", generator.timetable_title_style))
    elements.append(Spacer(1, 15))
    
    elements.append(Spacer(1, 15))

    total_students = allocations.aggregate(Sum('number_of_students'))['number_of_students__sum'] or 0
    
    # Summary
    summary_data = [
        ['Total Allocations', 'Total Students'],
        [str(allocations.count()), str(total_students)]
    ]
    summary_table = Table(summary_data, colWidths=[150, 150])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.black),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Times-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 20))
    
    # Allocations by program
    for program_name, program_allocs in by_program.items():
        elements.append(Paragraph(f"<b>{program_name}</b>", generator.date_header_style))
        elements.append(Spacer(1, 8))
        
        # Table data
        table_data = [[
            Paragraph('<b>Course Code</b>', generator.header_cell_style),
            Paragraph('<b>Course Name</b>', generator.header_cell_style),
            Paragraph('<b>Lecturer</b>', generator.header_cell_style),
            Paragraph('<b>Students</b>', generator.header_cell_style),
            Paragraph('<b>Campus</b>', generator.header_cell_style),
        ]]
        
        for alloc in program_allocs:
            row = [
                Paragraph(alloc.course_code, generator.cell_style),
                Paragraph(alloc.course_name[:30], generator.cell_style),
                Paragraph(alloc.lecturer.display_name if alloc.lecturer else 'Unassigned', generator.cell_style),
                Paragraph(str(alloc.number_of_students), generator.cell_style),
                Paragraph(alloc.campus.code if alloc.campus else '-', generator.campus_cell_style),
            ]
            table_data.append(row)
        
        # Program total
        prog_total = sum(a.number_of_students for a in program_allocs)
        table_data.append([
            Paragraph("<b>Program Total</b>", generator.cell_style),
            '', '', '',
            Paragraph(f"<b>{prog_total}</b>", generator.cell_style)
        ])
        
        col_widths = [80, 150, 120, 60, 60]
        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        
        table_style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.black),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Times-Bold'),
            ('GRID', (0, 0), (-1, -2), 0.5, colors.black),
            ('BOX', (0, -1), (-1, -1), 1, colors.black),
            ('BACKGROUND', (0, 1), (-1, -2), colors.white),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f5f5f5')),
            ('FONTNAME', (0, -1), (-1, -1), 'Times-Bold'),
        ])
        
        table.setStyle(table_style)
        elements.append(table)
        elements.append(Spacer(1, 20))
    
    # Add footer
    elements = generator.add_footer(elements, "Academic Registrar")
    
    doc.build(elements)
    
    pdf_content = buffer.getvalue()
    buffer.close()
    
    campus_code = campus.code if campus else 'ALL'
    filename = f"campus_allocations_{campus_code}_{generator.current_date.strftime('%Y%m%d_%H%M')}.pdf"
    
    return pdf_content, filename


# ===================== GENERATE PDF VIEWS =====================

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required('campuses_timetable.view_campustimetable', raise_exception=True)
def generate_class_timetable_pdf(request):
    """Generate Campus Class Timetable PDF for download"""
    try:
        campus_id = request.GET.get('campus')
        campus = get_object_or_404(Campus, id=campus_id) if campus_id else None
        
        pdf_content, filename = generate_class_timetable_pdf_content(request, campus)
        
        if not pdf_content:
            messages.error(request, "No class timetable data found to generate PDF.")
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))
        
        response = HttpResponse(pdf_content, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
        
    except Exception as e:
        messages.error(request, f"Error generating PDF: {str(e)}")
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required('campuses_timetable.view_campusexamtimetable', raise_exception=True)
def generate_exam_timetable_pdf(request):
    """Generate Campus Exam Timetable PDF for download"""
    try:
        campus_id = request.GET.get('campus')
        campus = get_object_or_404(Campus, id=campus_id) if campus_id else None
        
        pdf_content, filename = generate_exam_timetable_pdf_content(request, campus)
        
        if not pdf_content:
            messages.error(request, "No exam timetable data found to generate PDF.")
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))
        
        response = HttpResponse(pdf_content, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
        
    except Exception as e:
        messages.error(request, f"Error generating PDF: {str(e)}")
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required('campuses_timetable.view_campuscourseallocation', raise_exception=True)
def generate_allocations_pdf(request):
    """Generate Course Allocations PDF for download"""
    try:
        campus_id = request.GET.get('campus')
        campus = get_object_or_404(Campus, id=campus_id) if campus_id else None
        
        pdf_content, filename = generate_course_allocations_pdf_content(request, campus)
        
        if not pdf_content:
            messages.error(request, "No course allocations data found to generate PDF.")
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))
        
        response = HttpResponse(pdf_content, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
        
    except Exception as e:
        messages.error(request, f"Error generating PDF: {str(e)}")
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required('campuses_timetable.view_campustimetable', raise_exception=True)
def preview_class_timetable_pdf(request):
    """Preview Campus Class Timetable PDF in browser"""
    try:
        campus_id = request.GET.get('campus')
        campus = get_object_or_404(Campus, id=campus_id) if campus_id else None
        
        pdf_content, filename = generate_class_timetable_pdf_content(request, campus)
        
        if not pdf_content:
            return render(request, 'campuses_timetable/no_data.html', {
                'title': 'No Class Timetable Data',
                'message': 'There is no class timetable data to preview.'
            })
        
        response = HttpResponse(pdf_content, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response
        
    except Exception as e:
        return render(request, 'campuses_timetable/error.html', {
            'title': 'PDF Generation Error',
            'error': str(e)
        })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required('campuses_timetable.view_campusexamtimetable', raise_exception=True)
def preview_exam_timetable_pdf(request):
    """Preview Campus Exam Timetable PDF in browser"""
    try:
        campus_id = request.GET.get('campus')
        campus = get_object_or_404(Campus, id=campus_id) if campus_id else None
        
        pdf_content, filename = generate_exam_timetable_pdf_content(request, campus)
        
        if not pdf_content:
            return render(request, 'campuses_timetable/no_data.html', {
                'title': 'No Exam Timetable Data',
                'message': 'There is no exam timetable data to preview.'
            })
        
        response = HttpResponse(pdf_content, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response
        
    except Exception as e:
        return render(request, 'campuses_timetable/error.html', {
            'title': 'PDF Generation Error',
            'error': str(e)
        })


# ===================== PUBLISH PDF VIEWS =====================

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
@permission_required('campuses_timetable.publish_timetable', raise_exception=True)
def publish_class_timetable(request):
    """Publish the current class timetable as a new version"""
    try:
        campus_id = request.POST.get('campus')
        campus = get_object_or_404(Campus, id=campus_id) if campus_id else None
        
        # Generate the PDF
        pdf_content, filename = generate_class_timetable_pdf_content(request, campus)
        
        if not pdf_content:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'error': 'No class timetable data to publish'
                }, status=404)
            else:
                messages.error(request, 'No class timetable data to publish')
                return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))
        
        # Set previous versions as not latest
        CampusPublishedTimetablePDF.objects.filter(
            timetable_type='CLASS',
            campus=campus,
            is_latest=True
        ).update(is_latest=False)
        
        # Get next version number
        latest = CampusPublishedTimetablePDF.objects.filter(
            timetable_type='CLASS',
            campus=campus
        ).order_by('-version').first()
        next_version = (latest.version + 1) if latest else 1
        
        # Create published instance
        published = CampusPublishedTimetablePDF(
            timetable_type='CLASS',
            version=next_version,
            is_latest=True,
            published_at=timezone.now(),
            published_by=request.user,
            campus=campus
        )
        
        # Save the file
        published.pdf_file.save(filename, ContentFile(pdf_content), save=True)
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'version': published.version,
                'published_at': published.published_at.isoformat(),
                'download_url': published.pdf_file.url,
                'message': f'Class timetable version {published.version} published successfully!'
            })
        else:
            messages.success(
                request, 
                f'Class timetable version {published.version} published successfully!'
            )
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))
        
    except Exception as e:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': False,
                'error': str(e)
            }, status=500)
        else:
            messages.error(request, f'Error publishing class timetable: {str(e)}')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
@permission_required('campuses_timetable.publish_timetable', raise_exception=True)
def publish_exam_timetable(request):
    """Publish the current exam timetable as a new version"""
    try:
        campus_id = request.POST.get('campus')
        campus = get_object_or_404(Campus, id=campus_id) if campus_id else None
        
        # Generate the PDF
        pdf_content, filename = generate_exam_timetable_pdf_content(request, campus)
        
        if not pdf_content:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'error': 'No exam timetable data to publish'
                }, status=404)
            else:
                messages.error(request, 'No exam timetable data to publish')
                return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))
        
        # Set previous versions as not latest
        CampusPublishedTimetablePDF.objects.filter(
            timetable_type='EXAM',
            campus=campus,
            is_latest=True
        ).update(is_latest=False)
        
        # Get next version number
        latest = CampusPublishedTimetablePDF.objects.filter(
            timetable_type='EXAM',
            campus=campus
        ).order_by('-version').first()
        next_version = (latest.version + 1) if latest else 1
        
        # Create published instance
        published = CampusPublishedTimetablePDF(
            timetable_type='EXAM',
            version=next_version,
            is_latest=True,
            published_at=timezone.now(),
            published_by=request.user,
            campus=campus
        )
        
        # Save the file
        published.pdf_file.save(filename, ContentFile(pdf_content), save=True)
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'version': published.version,
                'published_at': published.published_at.isoformat(),
                'download_url': published.pdf_file.url,
                'message': f'Exam timetable version {published.version} published successfully!'
            })
        else:
            messages.success(
                request, 
                f'Exam timetable version {published.version} published successfully!'
            )
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))
        
    except Exception as e:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': False,
                'error': str(e)
            }, status=500)
        else:
            messages.error(request, f'Error publishing exam timetable: {str(e)}')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))


# ===================== DOWNLOAD PUBLISHED PDF VIEWS =====================

@require_GET
def download_latest_class_timetable(request):
    """
    Public endpoint — download the latest published campus class timetable PDF.
    No login required; requires a valid signed token: ?token=<signed>
    """
    _, pk = validate_download_token(request.GET.get("token"), "campus_class")
    if pk is None:
        return HttpResponse("Invalid or expired download link.", status=403)
    latest = get_object_or_404(CampusPublishedTimetablePDF, pk=pk, timetable_type="CLASS")
    latest.increment_download_count()
    response = HttpResponse(latest.pdf_file, content_type="application/pdf")
    campus_code = latest.campus.code if latest.campus else "ALL"
    filename = f"campus_class_timetable_{campus_code}_v{latest.version}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_GET
def download_latest_exam_timetable(request):
    """
    Public endpoint — download the latest published campus exam timetable PDF.
    No login required; requires a valid signed token: ?token=<signed>
    """
    _, pk = validate_download_token(request.GET.get("token"), "campus_exam")
    if pk is None:
        return HttpResponse("Invalid or expired download link.", status=403)
    latest = get_object_or_404(CampusPublishedTimetablePDF, pk=pk, timetable_type="EXAM")
    latest.increment_download_count()
    response = HttpResponse(latest.pdf_file, content_type="application/pdf")
    campus_code = latest.campus.code if latest.campus else "ALL"
    filename = f"campus_exam_timetable_{campus_code}_v{latest.version}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_GET
def download_published_timetable(request, pk):
    """
    Public endpoint — download a specific published campus timetable by PK.
    No login required; requires a valid signed token: ?token=<signed>
    Token type must match the record's timetable_type.
    """
    published = get_object_or_404(CampusPublishedTimetablePDF, pk=pk)
    expected = "campus_class" if published.timetable_type == "CLASS" else "campus_exam"
    _, tok_pk = validate_download_token(request.GET.get("token"), expected)
    if tok_pk != pk:
        return HttpResponse("Invalid or expired download link.", status=403)
    published.increment_download_count()
    response = HttpResponse(published.pdf_file, content_type="application/pdf")
    campus_code = published.campus.code if published.campus else "ALL"
    filename = f"campus_{published.timetable_type.lower()}_timetable_{campus_code}_v{published.version}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ===================== VIEW PUBLISHED TIMETABLES =====================

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required('campuses_timetable.view_campuspublishedtimetablepdf', raise_exception=True)
def view_published_timetables(request):
    """View all published timetables with version history"""
    campus_id = request.GET.get('campus')
    campus = get_object_or_404(Campus, id=campus_id) if campus_id else None
    
    filters = {}
    if campus:
        filters['campus'] = campus
    
    class_timetables = CampusPublishedTimetablePDF.objects.filter(
        timetable_type='CLASS',
        **filters
    ).order_by('-version')
    
    exam_timetables = CampusPublishedTimetablePDF.objects.filter(
        timetable_type='EXAM',
        **filters
    ).order_by('-version')
    
    context = {
        'title': 'Published Timetables',
        'class_timetables': class_timetables,
        'exam_timetables': exam_timetables,
        'latest_class': class_timetables.filter(is_latest=True).first(),
        'latest_exam': exam_timetables.filter(is_latest=True).first(),
        'campus': campus,
        'campuses': Campus.objects.filter(is_active=True),
    }
    
    return render(request, 'campuses_timetable/published_timetables.html', context)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def get_published_info(request, pk):
    """Get info about a published timetable (AJAX endpoint)"""
    published = get_object_or_404(CampusPublishedTimetablePDF, pk=pk)
    
    return JsonResponse({
        'id': published.id,
        'type': published.get_timetable_type_display(),
        'version': published.version,
        'is_latest': published.is_latest,
        'published_at': published.published_at.isoformat(),
        'published_by': published.published_by.username if published.published_by else None,
        'download_url': published.pdf_file.url,
        'filename': published.pdf_file.name.split('/')[-1],
        'file_size': published.formatted_file_size,
        'download_count': published.download_count,
        'campus': published.campus.code if published.campus else 'All Campuses',
    })