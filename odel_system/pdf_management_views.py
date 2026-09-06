from core.rbac import allowed_roles, Role
from core.signed_download import validate_download_token
import io
import os
import logging
from datetime import datetime
from collections import defaultdict
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_GET, require_POST

logger = logging.getLogger(__name__)
from django.contrib import messages
from django.utils import timezone
from django.core.files.base import ContentFile
from django.templatetags.static import static
from django.conf import settings

# ReportLab imports
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

# Models
from .models import (
    ODELTimetable, ODELExamTimetable, ODELCourseAllocation,
    PublishedTimetablePDF
)
from export_import.models import TimetablePdfTemplate


# ===================== PDF GENERATION FUNCTIONS =====================

class ODELPDFGenerator:
    """Base class for ODEL PDF generation"""
    
    def __init__(self, request=None):
        self.request = request
        self.template_config = TimetablePdfTemplate.get_template()
        self.current_date = timezone.now()
        self.styles = getSampleStyleSheet()
        self._setup_styles()
    
    def _setup_styles(self):
        """Setup custom styles matching official university template (black/white, red title)"""
        # University name: bold, black, centred
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

        # Directorate name: bold, black, centred
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

        # Small contact/header info: black, centred
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

        # Table cell: black, centred
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

        # Venue cell: bold, black, centred
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

        # Page header: small, black, left-aligned
        self.page_header_style = ParagraphStyle(
            'PageHeader',
            parent=self.styles['Normal'],
            fontSize=7,
            alignment=0,
            textColor=colors.black,
            spaceAfter=3
        )

        # Timetable title: bold, red, centred (like the template PDF title)
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

        # Ref/date line styles
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
            textColor=colors.black,
            alignment=1,
            wordWrap='CJK',
            leading=12
        )

        # NB / Key section styles
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

        Layout:
          Row 1 (top bar): [Left: phone/address] [Centre: logo] [Right: PO Box/email/website]
          Row 2: University name bold centred
          Row 3: Motto italic centred
          Row 4: Directorate name bold centred
          Horizontal rule
          Row 5: Ref left  |  Date right
          Horizontal rule
        """
        from reportlab.platypus import HRFlowable

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

        # University name
        elements.append(Paragraph(self.template_config.university_name, self.title_style))

        # Motto
        motto_style = ParagraphStyle(
            'MottoStyle', parent=self.styles['Normal'],
            fontSize=9, alignment=1, textColor=colors.black,
            fontName='Times-Italic', spaceAfter=2
        )
        motto = f"{self.template_config.motto_latin}  {self.template_config.motto_swahili}"
        elements.append(Paragraph(motto, motto_style))

        # Directorate
        elements.append(Paragraph(self.template_config.directorate_name, self.subtitle_style))
        elements.append(Spacer(1, 6))

        # Horizontal rule
        elements.append(HRFlowable(width='100%', thickness=0.8, color=colors.black))
        elements.append(Spacer(1, 4))

        # Ref | Date row
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

        # Second horizontal rule
        elements.append(HRFlowable(width='100%', thickness=0.8, color=colors.black))
        elements.append(Spacer(1, 10))

        return elements
    
    def add_footer(self, elements, prepared_by=""):
        """Add footer matching the official template (Prepared by + Director blocks)"""
        elements.append(Spacer(1, 20))

        elements.append(Paragraph("<b>Prepared by:</b>", self.nb_label_style))
        elements.append(Spacer(1, 40))  # space for signature

        elements.append(Paragraph(
            f"<b>{self.template_config.directorate_name}</b>",
            self.nb_label_style
        ))
        elements.append(Paragraph(
            f"<b>{self.template_config.director_label}</b>",
            self.nb_label_style
        ))

        return elements


def generate_class_timetable_pdf_content(request=None):
    """Generate ODEL Class Timetable PDF content"""
    # Fetch data
    entries = ODELTimetable.objects.select_related(
        'course_allocation__program_course',
        'course_allocation__lecturer',
        'venue'
    ).all().order_by('date', 'start_time', 'venue__code')
    
    if not entries.exists():
        return None, None
    
    generator = ODELPDFGenerator(request)
    
    # Organize by date
    entries_by_date = defaultdict(list)
    for entry in entries:
        entries_by_date[entry.date].append(entry)
    
    sorted_dates = sorted(entries_by_date.keys())
    
    # Create PDF buffer
    buffer = io.BytesIO()
    
    # Determine page size based on max time slots
    max_time_slots = 0
    for date_entries in entries_by_date.values():
        time_slots = len(set(f"{e.start_time}-{e.end_time}" for e in date_entries))
        max_time_slots = max(max_time_slots, time_slots)
    
    pagesize = landscape(A4) if max_time_slots > 4 else A4
    
    doc = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        rightMargin=25,
        leftMargin=25,
        topMargin=50,
        bottomMargin=50,
        title=f"ODEL Class Timetable"
    )
    
    elements = []
    
    # Add header
    elements = generator.add_header(elements, "Class")
    
    # Timetable title (red, centred — matching template)
    title = generator.template_config.title_format.format(timetable_type="ODEL CLASS")
    elements.append(Paragraph(f"{title}", generator.timetable_title_style))
    elements.append(Spacer(1, 20))
    
    # Page tracking
    current_page = 1
    total_pages = len(sorted_dates) + 2  # +1 for key, +1 for signatures
    
    # Generate tables for each date
    for date_idx, date in enumerate(sorted_dates):
        # Page header
        page_header = generator.template_config.get_page_header(
            "CLASS", current_page, total_pages
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
        
        # Group by time slot — guard against None start_time/end_time
        raw_slots = set()
        for e in date_entries:
            if e.start_time and e.end_time:
                raw_slots.add(f"{e.start_time.strftime('%H:%M')} - {e.end_time.strftime('%H:%M')}")
        time_slots = sorted(raw_slots) if raw_slots else ['TBD']

        # Get unique venues
        venues = sorted(set(e.venue.code for e in date_entries if e.venue))

        # Create matrix
        table_data = []

        # Headers
        headers = ['Venue'] + time_slots
        header_cells = [Paragraph(f"<b>{h}</b>", generator.header_cell_style) for h in headers]
        table_data.append(header_cells)

        # Create venue rows
        for venue in venues:
            row = [Paragraph(venue, generator.venue_cell_style)]
            for time_slot in time_slots:
                if time_slot == 'TBD':
                    # Show all courses for this venue on this date
                    courses = [e.course_allocation.course_code for e in date_entries if e.venue.code == venue]
                else:
                    courses = []
                    for entry in date_entries:
                        if entry.start_time and entry.end_time:
                            entry_time = f"{entry.start_time.strftime('%H:%M')} - {entry.end_time.strftime('%H:%M')}"
                            if entry.venue.code == venue and entry_time == time_slot:
                                courses.append(entry.course_allocation.course_code)
                cell_text = "<br/>".join(sorted(set(courses))) if courses else ""
                row.append(Paragraph(cell_text, generator.cell_style))
            table_data.append(row)

        # Create table — fill full page width dynamically
        page_width = 545  # A4 usable width (595 - 25 margins each side)
        venue_col = 70
        slot_col = (page_width - venue_col) / max(len(time_slots), 1)
        col_widths = [venue_col] + [slot_col] * len(time_slots)
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
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),

            # Venue column: bold, white background
            ('BACKGROUND', (0, 1), (0, -1), colors.white),
            ('FONTNAME', (0, 1), (0, -1), 'Times-Bold'),

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
        if date_idx < len(sorted_dates) - 1:
            elements.append(PageBreak())
            current_page += 1
    
    # Key/Legend section
    elements.append(PageBreak())
    current_page += 1
    
    page_header = generator.template_config.get_page_header(
        "CLASS", current_page, total_pages
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
    elements = generator.add_footer(elements, "ODEL Coordinator")
    
    # Build PDF
    doc.build(elements)
    
    # Get PDF content
    pdf_content = buffer.getvalue()
    buffer.close()
    
    # Generate filename
    filename = f"odel_class_timetable_{generator.current_date.strftime('%Y%m%d_%H%M')}.pdf"
    
    return pdf_content, filename


def generate_exam_timetable_pdf_content(request=None):
    """Generate ODEL Exam Timetable PDF content"""
    # Fetch data
    entries = ODELExamTimetable.objects.select_related(
        'course_allocation__program_course',
        'course_allocation__lecturer',
        'venue'
    ).all().order_by('date', 'start_time', 'venue__code')
    
    if not entries.exists():
        return None, None
    
    generator = ODELPDFGenerator(request)
    
    # Organize by date
    entries_by_date = defaultdict(list)
    for entry in entries:
        entries_by_date[entry.date].append(entry)
    
    sorted_dates = sorted(entries_by_date.keys())
    
    # Create PDF buffer
    buffer = io.BytesIO()
    
    # Determine page size
    max_time_slots = 0
    for date_entries in entries_by_date.values():
        time_slots = len(set(f"{e.start_time}-{e.end_time}" for e in date_entries))
        max_time_slots = max(max_time_slots, time_slots)
    
    pagesize = landscape(A4) if max_time_slots > 4 else A4
    
    doc = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        rightMargin=25,
        leftMargin=25,
        topMargin=50,
        bottomMargin=50,
        title=f"ODEL Exam Timetable"
    )
    
    elements = []
    
    # Add header
    elements = generator.add_header(elements, "Exam")
    
    # Timetable title (red, centred — matching template)
    title = generator.template_config.title_format.format(timetable_type="ODEL EXAMINATION")
    elements.append(Paragraph(f"{title}", generator.timetable_title_style))
    elements.append(Spacer(1, 20))
    
    # Page tracking
    current_page = 1
    total_pages = len(sorted_dates) + 2
    
    # Generate tables for each date
    for date_idx, date in enumerate(sorted_dates):
        # Page header
        page_header = generator.template_config.get_page_header(
            "EXAM", current_page, total_pages
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
        
        # Group by time slot — guard against None start_time/end_time
        raw_slots = set()
        for e in date_entries:
            if e.start_time and e.end_time:
                raw_slots.add(f"{e.start_time.strftime('%H:%M')} - {e.end_time.strftime('%H:%M')}")
        time_slots = sorted(raw_slots) if raw_slots else ['TBD']

        # Get unique venues
        venues = sorted(set(e.venue.code for e in date_entries if e.venue))

        # Create matrix
        table_data = []

        # Headers
        headers = ['Venue'] + time_slots
        header_cells = [Paragraph(f"<b>{h}</b>", generator.header_cell_style) for h in headers]
        table_data.append(header_cells)

        # Create venue rows
        for venue in venues:
            row = [Paragraph(venue, generator.venue_cell_style)]
            for time_slot in time_slots:
                if time_slot == 'TBD':
                    # Show all courses for this venue on this date
                    courses = [e.course_allocation.course_code for e in date_entries if e.venue.code == venue]
                else:
                    courses = []
                    for entry in date_entries:
                        if entry.start_time and entry.end_time:
                            entry_time = f"{entry.start_time.strftime('%H:%M')} - {entry.end_time.strftime('%H:%M')}"
                            if entry.venue.code == venue and entry_time == time_slot:
                                courses.append(entry.course_allocation.course_code)
                cell_text = "<br/>".join(sorted(set(courses))) if courses else ""
                row.append(Paragraph(cell_text, generator.cell_style))
            table_data.append(row)

        # Create table — fill full page width dynamically
        page_width = 545  # A4 usable width (595 - 25 margins each side)
        venue_col = 70
        slot_col = (page_width - venue_col) / max(len(time_slots), 1)
        col_widths = [venue_col] + [slot_col] * len(time_slots)
        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        
        # Table styling — plain black/white matching official template
        table_style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.black),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 0), (-1, 0), 'Times-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),

            ('BACKGROUND', (0, 1), (0, -1), colors.white),
            ('FONTNAME', (0, 1), (0, -1), 'Times-Bold'),

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
        "EXAM", current_page, total_pages
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
    elements = generator.add_footer(elements, "Examinations Officer")
    
    # Build PDF
    doc.build(elements)
    
    # Get PDF content
    pdf_content = buffer.getvalue()
    buffer.close()
    
    # Generate filename
    filename = f"odel_exam_timetable_{generator.current_date.strftime('%Y%m%d_%H%M')}.pdf"
    
    return pdf_content, filename


# ===================== GENERATE PDF VIEWS =====================

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def generate_class_timetable_pdf(request):
    """
    Generate ODEL Class Timetable PDF for download
    """
    try:
        pdf_content, filename = generate_class_timetable_pdf_content(request)
        
        if not pdf_content:
            messages.error(request, "No class timetable data found to generate PDF.")
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))
        
        # Return as downloadable PDF
        response = HttpResponse(pdf_content, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
        
    except Exception as e:
        messages.error(request, f"Error generating PDF: {str(e)}")
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def generate_exam_timetable_pdf(request):
    """
    Generate ODEL Exam Timetable PDF for download
    """
    try:
        pdf_content, filename = generate_exam_timetable_pdf_content(request)
        
        if not pdf_content:
            messages.error(request, "No exam timetable data found to generate PDF.")
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))
        
        # Return as downloadable PDF
        response = HttpResponse(pdf_content, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
        
    except Exception as e:
        messages.error(request, f"Error generating PDF: {str(e)}")
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/admin/'))


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def preview_class_timetable_pdf(request):
    """
    Preview ODEL Class Timetable PDF in browser
    """
    try:
        pdf_content, filename = generate_class_timetable_pdf_content(request)
        
        if not pdf_content:
            return render(request, 'admin/odel_system/no_data.html', {
                'title': 'No Class Timetable Data',
                'message': 'There is no class timetable data to preview.'
            })
        
        # Return as inline PDF for preview
        response = HttpResponse(pdf_content, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response
        
    except Exception as e:
        return render(request, 'admin/odel_system/error.html', {
            'title': 'PDF Generation Error',
            'error': str(e)
        })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def preview_exam_timetable_pdf(request):
    """
    Preview ODEL Exam Timetable PDF in browser
    """
    try:
        pdf_content, filename = generate_exam_timetable_pdf_content(request)
        
        if not pdf_content:
            return render(request, 'admin/odel_system/no_data.html', {
                'title': 'No Exam Timetable Data',
                'message': 'There is no exam timetable data to preview.'
            })
        
        # Return as inline PDF for preview
        response = HttpResponse(pdf_content, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response
        
    except Exception as e:
        return render(request, 'admin/odel_system/error.html', {
            'title': 'PDF Generation Error',
            'error': str(e)
        })


# ===================== PUBLISH PDF VIEWS =====================

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def publish_class_timetable(request):
    """
    Publish the current class timetable as a new version
    """
    try:
        # Generate the PDF
        pdf_content, filename = generate_class_timetable_pdf_content(request)
        
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
        PublishedTimetablePDF.objects.filter(
            is_class_timetable=True, 
            is_latest=True
        ).update(is_latest=False)
        
        # Get next version number
        latest = PublishedTimetablePDF.objects.filter(
            is_class_timetable=True
        ).order_by('-version').first()
        next_version = (latest.version + 1) if latest else 1
        
        # Create published instance
        published = PublishedTimetablePDF(
            is_class_timetable=True,
            version=next_version,
            is_latest=True,
            published_at=timezone.now()
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
def publish_exam_timetable(request):
    """
    Publish the current exam timetable as a new version
    """
    try:
        # Generate the PDF
        pdf_content, filename = generate_exam_timetable_pdf_content(request)
        
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
        PublishedTimetablePDF.objects.filter(
            is_class_timetable=False, 
            is_latest=True
        ).update(is_latest=False)
        
        # Get next version number
        latest = PublishedTimetablePDF.objects.filter(
            is_class_timetable=False
        ).order_by('-version').first()
        next_version = (latest.version + 1) if latest else 1
        
        # Create published instance
        published = PublishedTimetablePDF(
            is_class_timetable=False,
            version=next_version,
            is_latest=True,
            published_at=timezone.now()
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
    Public endpoint — download the latest published ODEL class timetable PDF.
    No login required; requires a valid signed token: ?token=<signed>
    """
    _, pk = validate_download_token(request.GET.get("token"), "odel_class")
    if pk is None:
        return HttpResponse("Invalid or expired download link.", status=403)
    latest = get_object_or_404(PublishedTimetablePDF, pk=pk, is_class_timetable=True)
    response = HttpResponse(latest.pdf_file, content_type="application/pdf")
    filename = f"odel_class_timetable_v{latest.version}_{latest.published_at.strftime('%Y%m%d')}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_GET
def download_latest_exam_timetable(request):
    """
    Public endpoint — download the latest published ODEL exam timetable PDF.
    No login required; requires a valid signed token: ?token=<signed>
    """
    _, pk = validate_download_token(request.GET.get("token"), "odel_exam")
    if pk is None:
        return HttpResponse("Invalid or expired download link.", status=403)
    latest = get_object_or_404(PublishedTimetablePDF, pk=pk, is_class_timetable=False)
    response = HttpResponse(latest.pdf_file, content_type="application/pdf")
    filename = f"odel_exam_timetable_v{latest.version}_{latest.published_at.strftime('%Y%m%d')}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_GET
def download_published_timetable(request, pk):
    """
    Public endpoint — download a specific published ODEL timetable by PK.
    No login required; requires a valid signed token: ?token=<signed>
    Token type must match the record's type (odel_class or odel_exam).
    """
    published = get_object_or_404(PublishedTimetablePDF, pk=pk)
    expected = "odel_class" if published.is_class_timetable else "odel_exam"
    _, tok_pk = validate_download_token(request.GET.get("token"), expected)
    if tok_pk != pk:
        return HttpResponse("Invalid or expired download link.", status=403)
    response = HttpResponse(published.pdf_file, content_type="application/pdf")
    type_str = "class" if published.is_class_timetable else "exam"
    filename = f"odel_{type_str}_timetable_v{published.version}_{published.published_at.strftime('%Y%m%d')}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ===================== VIEW PUBLISHED TIMETABLES =====================

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def view_published_timetables(request):
    """
    View all published timetables with version history
    """
    class_timetables = PublishedTimetablePDF.objects.filter(
        is_class_timetable=True
    ).order_by('-version')
    
    exam_timetables = PublishedTimetablePDF.objects.filter(
        is_class_timetable=False
    ).order_by('-version')
    
    context = {
        'title': 'Published Timetables',
        'class_timetables': class_timetables,
        'exam_timetables': exam_timetables,
        'latest_class': class_timetables.filter(is_latest=True).first(),
        'latest_exam': exam_timetables.filter(is_latest=True).first(),
    }
    
    return render(request, 'admin/odel_system/published_timetables.html', context)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def get_published_info(request, pk):
    """
    Get info about a published timetable (AJAX endpoint)
    """
    published = get_object_or_404(PublishedTimetablePDF, pk=pk)
    
    return JsonResponse({
        'id': published.id,
        'type': 'Class' if published.is_class_timetable else 'Exam',
        'version': published.version,
        'is_latest': published.is_latest,
        'published_at': published.published_at.isoformat(),
        'download_url': published.pdf_file.url,
        'filename': published.pdf_file.name.split('/')[-1],
    })