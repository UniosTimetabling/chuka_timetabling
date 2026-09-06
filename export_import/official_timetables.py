from datetime import datetime, timedelta
from collections import defaultdict, OrderedDict
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.templatetags.static import static
from django.conf import settings
import os
import logging
from weasyprint import HTML, CSS
import tempfile
import base64
import io
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

logger = logging.getLogger(__name__)
from reportlab.lib import colors
from reportlab.lib.units import inch
from timetable.models import (
    Timetable,
    MergedCourseGroupTimetable,  # renamed from AutoMergedExamGroup; alias still works
    AutoMergedExamGroup,          # backward-compat alias
    SchedulerConfig,
    LabTimetable,
)
from room_management.models import Venue
from .models import TimetablePdfTemplate
from .pdf_watermark import draw_reportlab_watermark

def define_semester():
    """
    Determines the current semester (SEPT–DEC or JAN–APR)
    based on the majority of courses allocated in ProgramCourse.
    """
    from course_allocation.models import CourseAllocation
    from program_management.models import ProgramCourse
    from course_allocation.config_helpers import strip_course_code_tag

    sem1counter = 0
    sem2counter = 0
    threshold = 10

    for alloc in CourseAllocation.objects.select_related("program").all():
        course = ProgramCourse.objects.filter(
            program=alloc.program,
            course_code__iexact=strip_course_code_tag(alloc.course_code)
        ).first()

        if not course:
            continue

        if course.semester == 1:
            sem1counter += 1
        elif course.semester == 2:
            sem2counter += 1

        if abs(sem1counter - sem2counter) > threshold:
            break

    if sem1counter > sem2counter:
        semester = "SEPTEMBER - DECEMBER"
    elif sem2counter > sem1counter:
        semester = "JANUARY - APRIL"
    else:
        semester = "UNDETERMINED"

    return semester

def get_logo_url(request, template_config):
    """
    Get the logo URL properly for both local and PythonAnywhere
    """
    if template_config.university_logo and template_config.university_logo.name:
        try:
            # For PythonAnywhere, we need to handle file paths differently
            if 'pythonanywhere' in request.get_host():
                # On PythonAnywhere, serve from media URL
                logo_url = request.build_absolute_uri(template_config.university_logo.url)
            else:
                # Local development
                logo_url = request.build_absolute_uri(template_config.university_logo.url)
        except Exception as e:
            logger.warning("Could not resolve logo URL, falling back to base64: %s", e)
            # Fallback to base64 encoded image
            logo_path = os.path.join(settings.MEDIA_ROOT, template_config.university_logo.name)
            if os.path.exists(logo_path):
                with open(logo_path, "rb") as img_file:
                    logo_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                logo_url = f"data:image/png;base64,{logo_base64}"
            else:
                # Final fallback to static
                static_url = static('images/chuka.png')
                logo_url = request.build_absolute_uri(static_url)
    else:
        # Use static logo
        static_url = static('images/chuka.png')
        logo_url = request.build_absolute_uri(static_url)
    
    return logo_url

def export_main_pdf(request):
    """
    Generate optimized PDF timetable - NO lecturer details, only course codes
    """
    try:
        # Get template configuration with ALL fields
        template_config = TimetablePdfTemplate.get_template()
        
        # Get current date and generate reference
        current_date = timezone.now()
        reference_number = template_config.get_reference_number(current_date.strftime("%d-%b-%Y").upper())
        
        # --- Scheduler Configuration ---
        config = SchedulerConfig.objects.first()
        start_time = config.start_time if config else datetime.strptime("07:00", "%H:%M").time()
        end_time = config.end_time if config else datetime.strptime("19:00", "%H:%M").time()
        slot_size = config.slot_size if config else 3

        # --- Time Slots Generation ---
        time_slots = []
        current = datetime.combine(datetime.today(), start_time)
        end_dt = datetime.combine(datetime.today(), end_time)
        while current < end_dt:
            next_slot = current + timedelta(hours=slot_size)
            if next_slot > end_dt:
                next_slot = end_dt
            time_slots.append(f"{current.strftime('%H:%M')} - {next_slot.strftime('%H:%M')}")
            current = next_slot

        # --- Optimized Database Queries - NO lecturer fields ---
        # Get venue capacities for display
        venues_with_capacity = {}
        for venue in Venue.objects.only('code', 'capacity').all():
            venues_with_capacity[venue.code] = venue.capacity
        
        # Regular timetable - ONLY course_code, no lecturer fields
        timetable_qs = Timetable.objects.select_related(
            "course_allocation",
            "venue"
        ).only(
            "day", "start_time", "end_time", "venue__code",
            "course_allocation__course_code"
        ).all().order_by("venue__code", "start_time", "end_time", "day")

        # Merged timetable — MergedCourseGroupTimetable (was AutoMergedExamGroup).
        # Now has timetable_entry FK → Timetable (published) and
        # temp_timetable_entry FK → TempTimetable (draft).
        # We query published=True so timetable_entry is the live record.
        merged_qs = MergedCourseGroupTimetable.objects.select_related(
            "venue"
        ).prefetch_related(
            "merged_courses"
        ).filter(
            published=True
        ).only(
            "merged_code", "total_students", "date", "start_time", "end_time",
            "venue__code"
        ).order_by("venue__code", "start_time", "end_time", "date")

        # Lab timetable - NO lecturer details
        lab_qs = LabTimetable.objects.select_related(
            "lab_allocation__program_course", "lab_venue"
        ).only(
            "day", "start_time", "end_time",
            "lab_allocation__program_course__course_code",
            "lab_venue__code"
        ).all().order_by("lab_venue__code", "day", "start_time")

        if not timetable_qs.exists() and not merged_qs.exists() and not lab_qs.exists():
            return HttpResponse("No timetable data found.", content_type="text/plain")

        # REMOVED Saturday from days order - Monday to Friday only
        DAYS_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

        def slot_label(st, en):
            return f"{st.strftime('%H:%M')} - {en.strftime('%H:%M')}"

        def venue_key_from_obj(obj):
            """Extract venue code with capacity"""
            if hasattr(obj, 'venue') and obj.venue:
                venue_code = getattr(obj.venue, 'code', str(obj.venue))
                capacity = venues_with_capacity.get(venue_code, "")
                return f"{venue_code} ({capacity})" if capacity else venue_code
            elif hasattr(obj, 'lab_venue') and obj.lab_venue:
                venue_code = getattr(obj.lab_venue, 'code', str(obj.lab_venue))
                capacity = venues_with_capacity.get(venue_code, "")
                return f"{venue_code} ({capacity})" if capacity else venue_code
            return "Unknown"

        # --- Data Organization - ONLY course codes ---
        timetable_by_slot_day_venue = defaultdict(lambda: defaultdict(dict))
        for e in timetable_qs:
            # Skip Saturday entries
            if e.day.lower() == 'saturday':
                continue
                
            slot = slot_label(e.start_time, e.end_time)
            venue_key = venue_key_from_obj(e)
            day = e.day.lower()
            
            if venue_key not in timetable_by_slot_day_venue[slot][day]:
                timetable_by_slot_day_venue[slot][day][venue_key] = []
            
            code = getattr(e.course_allocation, "course_code", "")
            
            timetable_by_slot_day_venue[slot][day][venue_key].append({
                "course_code": code
            })

        merged_by_slot_date_venue = defaultdict(lambda: defaultdict(dict))
        for m in merged_qs:
            if m.start_time and m.end_time:
                slot = slot_label(m.start_time, m.end_time)
                venue_key = venue_key_from_obj(m)
                date_str = str(m.date).lower() if m.date else ""
                
                # Skip Saturday entries
                if 'saturday' in date_str:
                    continue
                    
                if venue_key not in merged_by_slot_date_venue[slot][date_str]:
                    merged_by_slot_date_venue[slot][date_str][venue_key] = []
                
                merged_code = m.merged_code or ""
                member_codes = [getattr(mc, "course_code", "") for mc in m.merged_courses.all()]
                display_code = merged_code or ", ".join(member_codes)
                
                merged_by_slot_date_venue[slot][date_str][venue_key].append({
                    "course_code": display_code
                })

        # Get all unique venues
        venues_all = set()
        for e in timetable_qs:
            # Skip Saturday venues
            if e.day.lower() != 'saturday':
                venues_all.add(venue_key_from_obj(e))
        for m in merged_qs:
            # Skip Saturday entries
            if m.date and 'saturday' not in str(m.date).lower():
                venues_all.add(venue_key_from_obj(m))
        for l in lab_qs:
            # Skip Saturday lab sessions
            if l.day.lower() != 'saturday':
                venues_all.add(venue_key_from_obj(l))

        # --- Day Grids with Optimized Layout ---
        day_grids = OrderedDict()
        for day in DAYS_ORDER:
            venues_set = set()
            
            # Add venues from regular timetable
            for slot in time_slots:
                day_venues = timetable_by_slot_day_venue.get(slot, {}).get(day.lower(), {})
                venues_set.update(day_venues.keys())
            
            # Add venues from merged timetable
            for slot in time_slots:
                date_venues = merged_by_slot_date_venue.get(slot, {}).get(day.lower(), {})
                venues_set.update(date_venues.keys())
            
            if not venues_set:
                venues_set = venues_all or {"Unassigned"}

            rows = []
            for vk in sorted(venues_set):
                cells = []
                for slot in time_slots:
                    cell_entries = []
                    
                    # Regular timetable
                    day_data = timetable_by_slot_day_venue.get(slot, {}).get(day.lower(), {})
                    if vk in day_data:
                        cell_entries.extend(day_data[vk])
                    
                    # Merged timetable
                    date_data = merged_by_slot_date_venue.get(slot, {}).get(day.lower(), {})
                    if vk in date_data:
                        cell_entries.extend(date_data[vk])
                    
                    cells.append({"entries": cell_entries or []})
                rows.append({"venue_key": vk, "cells": cells})
            day_grids[day] = rows

        # --- Lab Timetable — build as day-keyed grid (same structure as main) ---
        lab_by_slot_day_venue = defaultdict(lambda: defaultdict(dict))
        for l in lab_qs:
            if l.day.lower() == 'saturday':
                continue
            lslot     = slot_label(l.start_time, l.end_time)
            lvkey     = venue_key_from_obj(l)
            lday      = l.day.lower()
            lcode     = getattr(l.lab_allocation.program_course, "course_code", "")
            if lvkey not in lab_by_slot_day_venue[lslot][lday]:
                lab_by_slot_day_venue[lslot][lday][lvkey] = []
            lab_by_slot_day_venue[lslot][lday][lvkey].append({"course_code": lcode})

        # Collect all lab time slots and build per-day lab grids
        lab_time_slots = sorted(
            set(lslot for lslot in lab_by_slot_day_venue.keys()),
            key=lambda s: s  # already HH:MM format, string sort is correct
        )

        lab_day_grids = OrderedDict()
        for day in DAYS_ORDER:
            lab_venues_set = set()
            for lslot in lab_time_slots:
                lab_venues_set.update(
                    lab_by_slot_day_venue.get(lslot, {}).get(day.lower(), {}).keys()
                )
            if not lab_venues_set:
                continue  # no lab sessions this day — skip entirely
            lab_rows = []
            for lvk in sorted(lab_venues_set):
                cells = []
                for lslot in lab_time_slots:
                    slot_data = lab_by_slot_day_venue.get(lslot, {}).get(day.lower(), {})
                    entries = slot_data.get(lvk, [])
                    cells.append({"entries": entries})
                lab_rows.append({"venue_key": lvk, "cells": cells})
            lab_day_grids[day] = lab_rows
        
        # --- Semester and Title ---
        semester = define_semester()
        current_year = current_date.year
        timetable_type = "Teaching"
        dynamic_title = template_config.title_format.format(timetable_type=timetable_type)
        prepared_by_name = template_config.director_full_name
        
        # Create PDF buffer with optimized settings
        buffer = io.BytesIO()
        
        # Use A4 page size with appropriate margins
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=36,
            leftMargin=36,
            topMargin=72,
            bottomMargin=72
        )
        
        elements = []
        styles = getSampleStyleSheet()

        # ── Styles matching the official Chuka University PDF template ──────────
        # Bold centered university name
        univ_name_style = ParagraphStyle(
            'UnivName',
            parent=styles['Normal'],
            fontSize=16,
            fontName='Times-Bold',
            alignment=1,
            spaceAfter=2,
            textColor=colors.black,
        )
        # Directorate block – bold, centered, slightly smaller
        directorate_style = ParagraphStyle(
            'Directorate',
            parent=styles['Normal'],
            fontSize=11,
            fontName='Times-Bold',
            alignment=1,
            spaceAfter=2,
            textColor=colors.black,
        )
        # Small contact / motto line
        contact_style = ParagraphStyle(
            'Contact',
            parent=styles['Normal'],
            fontSize=9,
            fontName='Times-Roman',
            alignment=0,  # left-aligned inside table cells
            textColor=colors.black,
        )
        # Reference / date line
        ref_style = ParagraphStyle(
            'RefLine',
            parent=styles['Normal'],
            fontSize=9,
            fontName='Times-Bold',
            alignment=0,
            textColor=colors.black,
        )
        # Red centered timetable title — #CC0000 is the official Chuka
        # University brand red used across the other PDF generators
        # (campuses_timetable/pdf_views.py, odel_system/pdf_management_views.py).
        # Plain reportlab colors.red (#FF0000) is NOT the official color.
        title_style = ParagraphStyle(
            'TimetableTitle',
            parent=styles['Normal'],
            fontSize=13,
            fontName='Times-Bold',
            alignment=1,
            spaceAfter=10,
            textColor=colors.black,
        )
        # Day header (e.g. "MONDAY 13/10/2025")
        subtitle_style = ParagraphStyle(
            'DaySection',
            parent=styles['Normal'],
            fontSize=11,
            fontName='Times-Bold',
            alignment=1,
            spaceAfter=4,
            textColor=colors.black,
        )

        # ── University header block (logo centred, contact rows) ─────────────
        logo_img = None
        try:
            if template_config.university_logo and hasattr(template_config.university_logo, 'path'):
                logo_img = Image(template_config.university_logo.path, width=0.9*inch, height=0.9*inch)
        except Exception as e:
            print(f"Error loading logo: {e}")

        # Build a header table: logo in middle column, text around it
        left_contact = (
            f"Telephones: {template_config.telephone}\n"
            f"Direct Line:"
        )
        right_contact = (
            f"P. O. Box {template_config.address}\n"
            f"Email: {template_config.email}   Website: {template_config.website}"
        )

        left_para = Paragraph(left_contact.replace('\n', '<br/>'), contact_style)
        right_para = Paragraph(right_contact.replace('\n', '<br/>'), ParagraphStyle(
            'ContactRight', parent=contact_style, alignment=2))  # right-align

        logo_cell = logo_img if logo_img else Paragraph("", contact_style)

        header_table_data = [[left_para, logo_cell, right_para]]
        header_table = Table(header_table_data, colWidths=[160, 80, 280])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'CENTER'),
            ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 4))

        # University name + motto + directorate (centred block)
        elements.append(Paragraph(template_config.university_name, univ_name_style))
        motto = f"Knowledge is Wealth (<i>{template_config.motto_latin}</i>) {template_config.motto_swahili}"
        elements.append(Paragraph(motto, ParagraphStyle(
            'Motto', parent=styles['Normal'], fontSize=9, alignment=1, fontName='Times-Roman', spaceAfter=2)))
        elements.append(Paragraph(template_config.directorate_name, directorate_style))
        elements.append(Spacer(1, 6))

        # Reference and date line (bold left + bold right in a 2-col table)
        ref_date_data = [[
            Paragraph(f"<b>Ref: {reference_number}</b>", ref_style),
            Paragraph(f"<b>Date: {current_date.strftime('%-d<super>th</super> %B, %Y')}</b>",
                      ParagraphStyle('RefRight', parent=ref_style, alignment=2))
        ]]
        ref_table = Table(ref_date_data, colWidths=[260, 260])
        ref_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.black),
        ]))
        elements.append(ref_table)
        elements.append(Spacer(1, 8))

        # Report Title (red, centred)
        report_title = f"{dynamic_title} ({semester} {current_year})"
        elements.append(Paragraph(report_title, title_style))
        elements.append(Spacer(1, 6))
        
        # ── Per-day tables ──────────────────────────────────────────────────────
        for day, rows in day_grids.items():
            # Day section header – uppercase, centred, black bold (like the PDF)
            elements.append(Paragraph(day.upper(), subtitle_style))
            elements.append(Spacer(1, 4))
            
            # Create table for this day
            table_data = []
            
            # Table headers
            headers = ['Venue'] + time_slots
            header_style = ParagraphStyle(
                'TableHeader',
                parent=styles['Normal'],
                fontSize=9,
                fontName='Times-Bold',
                textColor=colors.black,
                alignment=1,
                wordWrap='CJK'
            )
            
            header_cells = [Paragraph(header, header_style) for header in headers]
            table_data.append(header_cells)
            
            # Add rows for each venue
            cell_style = ParagraphStyle(
                'TableCell',
                parent=styles['Normal'],
                fontSize=8,
                fontName='Times-Roman',
                textColor=colors.black,
                alignment=1,
                wordWrap='CJK',
                leading=10,
            )
            
            for row in rows:
                venue_row = [Paragraph(row['venue_key'], cell_style)]
                for cell in row['cells']:
                    if cell['entries']:
                        # Combine all course codes for this cell
                        course_text = "\n".join([entry['course_code'] for entry in cell['entries']])
                        venue_row.append(Paragraph(course_text, cell_style))
                    else:
                        venue_row.append(Paragraph("", cell_style))
                table_data.append(venue_row)
            
            # Calculate column widths
            num_columns = len(headers)
            col_widths = [80] + [100] * (num_columns - 1)  # Fixed venue column, flexible time slots
            
            # Create table
            table = Table(table_data, colWidths=col_widths, repeatRows=1)
            
            # Apply table styles – matching the official PDF template
            table_style = TableStyle([
                # Header row: white background, black bold text, bordered
                ('BACKGROUND', (0, 0), (-1, 0), colors.white),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Times-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
                ('TOPPADDING', (0, 0), (-1, 0), 6),

                # Black grid borders
                ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                ('BOX', (0, 0), (-1, -1), 0.8, colors.black),

                # White background for all data cells
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),

                # Cell padding
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),

                # Alignment
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),

                ('WORDWRAP', (0, 0), (-1, -1), True),
            ])
            
            table.setStyle(table_style)
            elements.append(table)
            elements.append(Spacer(1, 30))
            
            # Add page break if needed (except for last day)
            if list(day_grids.keys()).index(day) < len(day_grids) - 1:
                elements.append(PageBreak())

        # ── Lab Timetable section — separate, clearly labelled ────────────────
        if lab_day_grids:
            elements.append(PageBreak())

            # Section banner
            lab_banner_style = ParagraphStyle(
                'LabBanner', parent=styles['Normal'],
                fontSize=12, fontName='Times-Bold', alignment=1,
                textColor=colors.white,
            )
            lab_banner_data = [[Paragraph("<b>LAB TIMETABLE</b>", lab_banner_style)]]
            pw = A4[0] - 36 - 36  # page width minus margins
            lab_banner_tbl = Table(lab_banner_data, colWidths=[pw])
            lab_banner_tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#1a1a1a")),
                ("LEFTPADDING",   (0, 0), (-1, -1), 8),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
                ("TOPPADDING",    (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            elements.append(lab_banner_tbl)
            elements.append(Spacer(1, 10))

            lab_header_style = ParagraphStyle(
                'LabTableHeader', parent=styles['Normal'],
                fontSize=9, fontName='Times-Bold',
                textColor=colors.black, alignment=1, wordWrap='CJK'
            )
            lab_cell_style = ParagraphStyle(
                'LabTableCell', parent=styles['Normal'],
                fontSize=8, fontName='Times-Roman',
                textColor=colors.black, alignment=1, wordWrap='CJK', leading=10,
            )
            lab_table_style = TableStyle([
                ('BACKGROUND',    (0, 0), (-1,  0), colors.white),
                ('TEXTCOLOR',     (0, 0), (-1,  0), colors.black),
                ('ALIGN',         (0, 0), (-1,  0), 'CENTER'),
                ('FONTNAME',      (0, 0), (-1,  0), 'Times-Bold'),
                ('FONTSIZE',      (0, 0), (-1,  0), 9),
                ('BOTTOMPADDING', (0, 0), (-1,  0), 6),
                ('TOPPADDING',    (0, 0), (-1,  0), 6),
                ('GRID',          (0, 0), (-1, -1), 0.5, colors.black),
                ('BOX',           (0, 0), (-1, -1), 0.8, colors.black),
                ('BACKGROUND',    (0, 1), (-1, -1), colors.white),
                ('LEFTPADDING',   (0, 0), (-1, -1), 3),
                ('RIGHTPADDING',  (0, 0), (-1, -1), 3),
                ('TOPPADDING',    (0, 1), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
                ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
                ('WORDWRAP',      (0, 0), (-1, -1), True),
            ])

            lab_days_list = list(lab_day_grids.items())
            for lab_idx, (lab_day, lab_rows) in enumerate(lab_days_list):
                elements.append(Paragraph(lab_day.upper(), subtitle_style))
                elements.append(Spacer(1, 4))

                lab_tbl_data = []
                lab_headers  = ['Venue'] + lab_time_slots
                lab_tbl_data.append([Paragraph(h, lab_header_style) for h in lab_headers])

                for row in lab_rows:
                    vrow = [Paragraph(row['venue_key'], lab_cell_style)]
                    for cell in row['cells']:
                        if cell['entries']:
                            text = "\n".join(e['course_code'] for e in cell['entries'])
                            vrow.append(Paragraph(text, lab_cell_style))
                        else:
                            vrow.append(Paragraph("", lab_cell_style))
                    lab_tbl_data.append(vrow)

                num_lab_cols = len(lab_headers)
                lab_col_widths = [80] + [100] * (num_lab_cols - 1)
                lab_tbl = Table(lab_tbl_data, colWidths=lab_col_widths, repeatRows=1)
                lab_tbl.setStyle(lab_table_style)
                elements.append(lab_tbl)
                elements.append(Spacer(1, 30))

                if lab_idx < len(lab_days_list) - 1:
                    elements.append(PageBreak())
                    # Continuation banner
                    cont_banner_data = [[Paragraph("<b>LAB TIMETABLE (cont.)</b>", lab_banner_style)]]
                    cont_banner_tbl  = Table(cont_banner_data, colWidths=[pw])
                    cont_banner_tbl.setStyle(TableStyle([
                        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#1a1a1a")),
                        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
                        ("TOPPADDING",    (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ]))
                    elements.append(cont_banner_tbl)
                    elements.append(Spacer(1, 10))

        # ── Evening Classes pages ─────────────────────────────────────────────
        if config and config.enable_evening_classes:
            ev_start     = config.evening_start_time
            ev_end       = config.evening_end_time
            ev_slot_hrs  = config.slot_size
            ev_slot_count = config.evening_slot_count

            # Generate evening time slots
            ev_slots = []
            cur = datetime.combine(datetime.today(), ev_start)
            end_ev = datetime.combine(datetime.today(), ev_end)
            while cur < end_ev and len(ev_slots) < ev_slot_count:
                nxt = cur + timedelta(hours=ev_slot_hrs)
                if nxt > end_ev:
                    nxt = end_ev
                ev_slots.append(f"{cur.strftime('%H:%M')} - {nxt.strftime('%H:%M')}")
                cur = nxt

            # Build evening data grid (weekdays only)
            EVENING_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            ev_by_slot_day_venue = defaultdict(lambda: defaultdict(dict))
            for e in timetable_qs:
                if not e.start_time:
                    continue
                t = e.start_time.hour * 100 + e.start_time.minute
                if t < 1900:
                    continue  # daytime — skip
                sl = slot_label(e.start_time, e.end_time)
                vk = venue_key_from_obj(e)
                dy = e.day.lower()
                if vk not in ev_by_slot_day_venue[sl][dy]:
                    ev_by_slot_day_venue[sl][dy][vk] = []
                ev_by_slot_day_venue[sl][dy][vk].append({"course_code": getattr(e.course_allocation, "course_code", "")})

            ev_day_grids = OrderedDict()
            for day in EVENING_DAYS:
                venues_set = set()
                for sl in ev_slots:
                    venues_set.update(ev_by_slot_day_venue.get(sl, {}).get(day.lower(), {}).keys())
                if not venues_set:
                    continue
                rows = []
                for vk in sorted(venues_set):
                    cells = []
                    for sl in ev_slots:
                        entries = ev_by_slot_day_venue.get(sl, {}).get(day.lower(), {}).get(vk, [])
                        cells.append({"entries": entries})
                    rows.append({"venue_key": vk, "cells": cells})
                ev_day_grids[day] = rows

            if ev_day_grids:
                elements.append(PageBreak())
                # Evening banner
                ev_banner_style = ParagraphStyle(
                    'EvBanner', parent=styles['Normal'],
                    fontSize=12, fontName='Times-Bold', alignment=1, textColor=colors.white,
                )
                pw = A4[0] - 36 - 36
                ev_banner_data = [[Paragraph("<b>EVENING CLASSES TIMETABLE</b>", ev_banner_style)]]
                ev_banner_tbl  = Table(ev_banner_data, colWidths=[pw])
                ev_banner_tbl.setStyle(TableStyle([
                    ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#1565c0")),
                    ("LEFTPADDING",   (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
                    ("TOPPADDING",    (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]))
                elements.append(ev_banner_tbl)
                elements.append(Spacer(1, 6))
                elements.append(Paragraph(
                    f"Time: {ev_start.strftime('%H:%M')} – {ev_end.strftime('%H:%M')}",
                    ParagraphStyle('EvSub', parent=styles['Normal'], fontSize=9, fontName='Times-Roman',
                                   alignment=1, textColor=colors.HexColor("#1565c0"))
                ))
                elements.append(Spacer(1, 10))

                grid_cell_style = ParagraphStyle(
                    'EvCell', parent=styles['Normal'],
                    fontSize=8, fontName='Times-Roman', textColor=colors.black, alignment=1, leading=10,
                )
                grid_hdr_style = ParagraphStyle(
                    'EvHdr', parent=styles['Normal'],
                    fontSize=9, fontName='Times-Bold', textColor=colors.black, alignment=1,
                )
                ev_tbl_style = TableStyle([
                    ('BACKGROUND',    (0, 0), (-1,  0), colors.HexColor("#d0e8ff")),
                    ('FONTNAME',      (0, 0), (-1,  0), 'Times-Bold'),
                    ('GRID',          (0, 0), (-1, -1), 0.5, colors.black),
                    ('BOX',           (0, 0), (-1, -1), 0.8, colors.black),
                    ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING',   (0, 0), (-1, -1), 3),
                    ('RIGHTPADDING',  (0, 0), (-1, -1), 3),
                    ('TOPPADDING',    (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ])

                ev_days_list = list(ev_day_grids.items())
                for idx, (day, rows) in enumerate(ev_days_list):
                    elements.append(Paragraph(day.upper() + " (Evening)", subtitle_style))
                    elements.append(Spacer(1, 4))
                    tbl_data = [[Paragraph(h, grid_hdr_style) for h in ['Venue'] + ev_slots]]
                    for row in rows:
                        vrow = [Paragraph(row['venue_key'], grid_cell_style)]
                        for cell in row['cells']:
                            text = "\n".join(e['course_code'] for e in cell['entries']) if cell['entries'] else ""
                            vrow.append(Paragraph(text, grid_cell_style))
                        tbl_data.append(vrow)
                    ncols = len(ev_slots) + 1
                    cw = [80] + [100] * len(ev_slots)
                    tbl = Table(tbl_data, colWidths=cw, repeatRows=1)
                    tbl.setStyle(ev_tbl_style)
                    elements.append(tbl)
                    elements.append(Spacer(1, 24))
                    if idx < len(ev_days_list) - 1:
                        elements.append(PageBreak())

        # ── Weekend Classes pages ─────────────────────────────────────────────
        if config and config.enable_weekend_classes:
            wk_start     = config.weekend_start_time
            wk_end       = config.weekend_end_time
            wk_slot_size = config.weekend_slot_size

            wk_slots = []
            cur = datetime.combine(datetime.today(), wk_start)
            end_wk = datetime.combine(datetime.today(), wk_end)
            while cur < end_wk:
                nxt = cur + timedelta(hours=wk_slot_size)
                if nxt > end_wk:
                    nxt = end_wk
                wk_slots.append(f"{cur.strftime('%H:%M')} - {nxt.strftime('%H:%M')}")
                cur = nxt

            WEEKEND_DAYS = ["Saturday", "Sunday"]
            wk_by_slot_day_venue = defaultdict(lambda: defaultdict(dict))
            for e in timetable_qs:
                if (e.day or '').capitalize() not in WEEKEND_DAYS:
                    continue
                sl = slot_label(e.start_time, e.end_time)
                vk = venue_key_from_obj(e)
                dy = e.day.lower()
                if vk not in wk_by_slot_day_venue[sl][dy]:
                    wk_by_slot_day_venue[sl][dy][vk] = []
                wk_by_slot_day_venue[sl][dy][vk].append({"course_code": getattr(e.course_allocation, "course_code", "")})

            wk_day_grids = OrderedDict()
            for day in WEEKEND_DAYS:
                venues_set = set()
                for sl in wk_slots:
                    venues_set.update(wk_by_slot_day_venue.get(sl, {}).get(day.lower(), {}).keys())
                if not venues_set:
                    continue
                rows = []
                for vk in sorted(venues_set):
                    cells = []
                    for sl in wk_slots:
                        entries = wk_by_slot_day_venue.get(sl, {}).get(day.lower(), {}).get(vk, [])
                        cells.append({"entries": entries})
                    rows.append({"venue_key": vk, "cells": cells})
                wk_day_grids[day] = rows

            if wk_day_grids:
                elements.append(PageBreak())
                wk_banner_style = ParagraphStyle(
                    'WkBanner', parent=styles['Normal'],
                    fontSize=12, fontName='Times-Bold', alignment=1, textColor=colors.white,
                )
                pw = A4[0] - 36 - 36
                wk_banner_data = [[Paragraph("<b>WEEKEND CLASSES TIMETABLE</b>", wk_banner_style)]]
                wk_banner_tbl  = Table(wk_banner_data, colWidths=[pw])
                wk_banner_tbl.setStyle(TableStyle([
                    ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#6a1b9a")),
                    ("LEFTPADDING",   (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
                    ("TOPPADDING",    (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]))
                elements.append(wk_banner_tbl)
                elements.append(Spacer(1, 6))
                elements.append(Paragraph(
                    f"Time: {wk_start.strftime('%H:%M')} – {wk_end.strftime('%H:%M')} | Slot: {wk_slot_size}h",
                    ParagraphStyle('WkSub', parent=styles['Normal'], fontSize=9, fontName='Times-Roman',
                                   alignment=1, textColor=colors.HexColor("#6a1b9a"))
                ))
                elements.append(Spacer(1, 10))

                wk_cell_style = ParagraphStyle(
                    'WkCell', parent=styles['Normal'],
                    fontSize=8, fontName='Times-Roman', textColor=colors.black, alignment=1, leading=10,
                )
                wk_hdr_style = ParagraphStyle(
                    'WkHdr', parent=styles['Normal'],
                    fontSize=9, fontName='Times-Bold', textColor=colors.black, alignment=1,
                )
                wk_tbl_style = TableStyle([
                    ('BACKGROUND',    (0, 0), (-1,  0), colors.HexColor("#ede1f5")),
                    ('FONTNAME',      (0, 0), (-1,  0), 'Times-Bold'),
                    ('GRID',          (0, 0), (-1, -1), 0.5, colors.black),
                    ('BOX',           (0, 0), (-1, -1), 0.8, colors.black),
                    ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING',   (0, 0), (-1, -1), 3),
                    ('RIGHTPADDING',  (0, 0), (-1, -1), 3),
                    ('TOPPADDING',    (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ])

                wk_days_list = list(wk_day_grids.items())
                for idx, (day, rows) in enumerate(wk_days_list):
                    elements.append(Paragraph(day.upper(), subtitle_style))
                    elements.append(Spacer(1, 4))
                    tbl_data = [[Paragraph(h, wk_hdr_style) for h in ['Venue'] + wk_slots]]
                    for row in rows:
                        vrow = [Paragraph(row['venue_key'], wk_cell_style)]
                        for cell in row['cells']:
                            text = "\n".join(e['course_code'] for e in cell['entries']) if cell['entries'] else ""
                            vrow.append(Paragraph(text, wk_cell_style))
                        tbl_data.append(vrow)
                    cw = [80] + [100] * len(wk_slots)
                    tbl = Table(tbl_data, colWidths=cw, repeatRows=1)
                    tbl.setStyle(wk_tbl_style)
                    elements.append(tbl)
                    elements.append(Spacer(1, 24))
                    if idx < len(wk_days_list) - 1:
                        elements.append(PageBreak())

        # ── Signature block matching the PDF template ──────────────────────────
        # No more hardcoded "DIR/EXT" — the reference code is now built from
        # the director's real initials plus whichever PdfSignatory rows are
        # marked active in admin, e.g. "GAO/fm/sk".
        footer_data = template_config.get_footer_data(prepared_by=prepared_by_name)
        elements.append(Spacer(1, 30))

        sig_style = ParagraphStyle(
            'Sig',
            parent=styles['Normal'],
            fontSize=10,
            fontName='Times-Roman',
            textColor=colors.black,
            alignment=0,
        )
        sig_bold = ParagraphStyle(
            'SigBold',
            parent=sig_style,
            fontName='Times-Bold',
        )

        # "Prepared by:" label
        elements.append(Paragraph(f"<b>{footer_data['prepared_by_label']}</b>", sig_bold))
        elements.append(Spacer(1, 40))  # space for signature image

        # Director's full name, designation, then the GAO/fm/sk reference code
        elements.append(Paragraph(footer_data['prepared_by'], sig_bold))
        elements.append(Paragraph(f"<b>{footer_data['director_label']}</b>", sig_bold))
        if footer_data.get('signature_code'):
            elements.append(Paragraph(footer_data['signature_code'], sig_style))

        # Page number note
        elements.append(Spacer(1, 20))
        nb_style = ParagraphStyle('NB', parent=styles['Normal'], fontSize=8,
                                  fontName='Times-Roman', textColor=colors.black, alignment=0)
        if template_config.key_section and template_config.key_section.strip():
            elements.append(Paragraph("<b>NB:</b>", nb_style))
            for line in template_config.key_section.strip().splitlines():
                if line.strip():
                    elements.append(Paragraph(line.strip(), nb_style))
        
        # ── Page furniture: watermark (drawn first, so content paints over it) + page number ──
        def add_page_furniture(canvas_obj, doc_obj):
            draw_reportlab_watermark(canvas_obj, doc_obj, template_config, doc_ref=reference_number)
            canvas_obj.saveState()
            canvas_obj.setFont('Times-Roman', 8)
            page_num_text = f"Page {canvas_obj.getPageNumber()} of {doc_obj.page}"
            canvas_obj.drawCentredString(A4[0] / 2.0, 20, page_num_text)
            canvas_obj.restoreState()

        # Build PDF
        doc.build(elements, onFirstPage=add_page_furniture, onLaterPages=add_page_furniture)
        
        # Get PDF value from buffer
        pdf = buffer.getvalue()
        buffer.close()
        
        # Create HTTP response with PDF
        response = HttpResponse(content_type='application/pdf')
        filename = f"timetable_{current_year}_{semester.split()[0]}_{current_date.strftime('%Y%m%d_%H%M')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response.write(pdf)
        
        return response
        
    except Exception as e:
        # Log error and return error response
        import traceback
        print(f"Error generating PDF: {e}")
        print(traceback.format_exc())
        
        return HttpResponse(
            f"Error generating PDF: {str(e)}",
            status=500,
            content_type='text/plain'
        )