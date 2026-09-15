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
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

logger = logging.getLogger(__name__)
from reportlab.lib import colors
from reportlab.lib.units import inch, cm
from timetable.models import (
    ExamTimetable,
    MergedCourseGroup,
    SharedVenueExamGroup,
    LabExamTimetable,
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

def export_main_exam_pdf(request):
    """
    Generate unified Exam + Lab Exam timetable PDF using database-driven header/footer.
    Organizes timetable by date, with each date having its own properly sized table.
    Removes duplicate course codes from the same venue and time slot.
    Features light colors and bluish fonts for a clean, professional look.
    """
    try:
        # Get template configuration with ALL fields
        template_config = TimetablePdfTemplate.get_template()
        
        # Get current date and generate reference
        current_date = timezone.now()
        reference_number = template_config.get_reference_number(current_date.strftime("%d-%b-%Y").upper())
        
        # ---------- MAIN EXAMS ----------
        exams = (
            ExamTimetable.objects.select_related("course_allocation__lecturer", "venue")
            .all()
            .order_by("date", "start_time", "venue__code")
        )

        # ---------- LAB EXAMS ----------
        lab_exams = (
            LabExamTimetable.objects.select_related(
                "lab_allocation__program_course",
                "lab_allocation__lecturer",
                "lab_venue",
            )
            .all()
            .order_by("date", "start_time", "lab_venue__code")
        )

        # ---------- SHARED VENUE EXAM GROUPS (published) ----------
        # SharedVenueExamGroup.exam_timetable_entry → ExamTimetable (published).
        # Courses in these groups share a room at the same date/slot.
        shared_venue_groups = (
            SharedVenueExamGroup.objects
            .filter(published=True)
            .select_related("venue", "exam_timetable_entry")
            .prefetch_related("course_allocations__lecturer")
            .order_by("date", "start_time")
        )

        if not exams.exists() and not lab_exams.exists() and not shared_venue_groups.exists():
            return HttpResponse("No exam timetable data found.", content_type="text/plain")

        # ---------- ORGANIZE DATA BY DATE ----------
        # Combine all exam entries
        all_exam_entries = []
        entry_dedup_set = set()  # To track duplicates
        
        # Main exams
        for e in exams:
            if not e.date or not e.start_time or not e.end_time:
                continue
                
            venue_name = getattr(e.venue, 'code', None) or getattr(e.venue, 'name', None) or "Unassigned"
            
            # Create unique key for this entry
            entry_key = (e.date, e.start_time, e.end_time, venue_name, e.course_allocation.course_code)
            
            # Skip if already added
            if entry_key in entry_dedup_set:
                continue
            entry_dedup_set.add(entry_key)
            
            entry = {
                "date": e.date,
                "day": e.day,
                "start_time": e.start_time,
                "end_time": e.end_time,
                "time_slot": f"{e.start_time.strftime('%H:%M')} - {e.end_time.strftime('%H:%M')}",
                "venue": venue_name,
                "course_code": e.course_allocation.course_code,
                "course_name": e.course_allocation.course_name,
                "lecturer": getattr(e.course_allocation.lecturer, "display_name", "Unassigned"),
                "type": "Main Exam",
                "original_obj": e
            }
            all_exam_entries.append(entry)
            
            # Handle published merged groups.
            # MergedCourseGroup.exam_timetable_entry → ExamTimetable (new FK).
            merged_group = (
                MergedCourseGroup.objects.filter(
                    base_course=e.course_allocation,
                    published=True,
                )
                .prefetch_related("merged_courses__lecturer")
                .first()
            )
            if merged_group:
                for mc in merged_group.merged_courses.all():
                    # Create unique key for merged entry
                    merged_entry_key = (e.date, e.start_time, e.end_time, venue_name, mc.course_code)
                    
                    # Skip if already added
                    if merged_entry_key in entry_dedup_set:
                        continue
                    entry_dedup_set.add(merged_entry_key)
                    
                    merged_entry = {
                        "date": e.date,
                        "day": e.day,
                        "start_time": e.start_time,
                        "end_time": e.end_time,
                        "time_slot": f"{e.start_time.strftime('%H:%M')} - {e.end_time.strftime('%H:%M')}",
                        "venue": venue_name,
                        "course_code": mc.course_code,
                        "course_name": mc.course_name,
                        "lecturer": getattr(mc.lecturer, "display_name", "Unassigned"),
                        "type": "Merged Exam",
                        "original_obj": e
                    }
                    all_exam_entries.append(merged_entry)

        # Lab exams
        for le in lab_exams:
            if not le.date or not le.start_time or not le.end_time:
                continue
                
            venue_name = getattr(le.lab_venue, 'code', None) or getattr(le.lab_venue, 'name', None) or "Unassigned"
            course = le.lab_allocation.program_course
            
            # Create unique key for lab entry
            lab_entry_key = (le.date, le.start_time, le.end_time, venue_name, course.course_code)
            
            # Skip if already added
            if lab_entry_key in entry_dedup_set:
                continue
            entry_dedup_set.add(lab_entry_key)
            
            entry = {
                "date": le.date,
                "day": le.day,
                "start_time": le.start_time,
                "end_time": le.end_time,
                "time_slot": f"{le.start_time.strftime('%H:%M')} - {le.end_time.strftime('%H:%M')}",
                "venue": venue_name,
                "course_code": course.course_code,
                "course_name": course.course_name,
                "lecturer": getattr(le.lab_allocation.lecturer, "display_name", "Unassigned"),
                "type": "Lab Exam",
                "original_obj": le
            }
            all_exam_entries.append(entry)

        # Shared venue exam groups (published)
        # SharedVenueExamGroup.exam_timetable_entry → ExamTimetable (new FK).
        # Each group has multiple course_allocations sharing one venue/slot.
        for svg in shared_venue_groups:
            if not svg.date or not svg.start_time or not svg.end_time:
                continue
            venue_name = (
                getattr(svg.venue, "code", None) or
                getattr(svg.venue, "name", None) or "Unassigned"
            )
            for ca in svg.course_allocations.all():
                shared_entry_key = (svg.date, svg.start_time, svg.end_time, venue_name, ca.course_code)
                if shared_entry_key in entry_dedup_set:
                    continue
                entry_dedup_set.add(shared_entry_key)
                all_exam_entries.append({
                    "date": svg.date,
                    "day": svg.day,
                    "start_time": svg.start_time,
                    "end_time": svg.end_time,
                    "time_slot": f"{svg.start_time.strftime('%H:%M')} - {svg.end_time.strftime('%H:%M')}",
                    "venue": venue_name,
                    "course_code": ca.course_code,
                    "course_name": ca.course_name,
                    "lecturer": getattr(ca.lecturer, "display_name", "Unassigned"),
                    "type": "Exam (Shared Venue)",
                    "original_obj": svg,
                })

        # Group entries by date — MAIN exams only (no lab exams in this map)
        main_exam_entries = [e for e in all_exam_entries if e["type"] != "Lab Exam"]
        lab_exam_entries  = [e for e in all_exam_entries if e["type"] == "Lab Exam"]

        def _build_date_tables_from_entries(entries):
            by_date = defaultdict(list)
            for entry in entries:
                by_date[entry["date"]].append(entry)
            result = OrderedDict()
            for date in sorted(by_date.keys()):
                date_entries = by_date[date]
                time_slots   = sorted(set(e["time_slot"] for e in date_entries))
                venues       = sorted(set(e["venue"] for e in date_entries))
                matrix_data  = []
                for venue in venues:
                    row = [venue]
                    for time_slot in time_slots:
                        courses_in_slot = [
                            e for e in date_entries
                            if e["venue"] == venue and e["time_slot"] == time_slot
                        ]
                        if courses_in_slot:
                            course_codes_set = set(e["course_code"] for e in courses_in_slot)
                            row.append("\n".join(sorted(course_codes_set)))
                        else:
                            row.append("")
                    matrix_data.append(row)
                day = date_entries[0]["day"] if date_entries else ""
                result[date] = {
                    "day":          day,
                    "date_str":     date.strftime("%Y-%m-%d"),
                    "date_display": date.strftime("%A, %d %B %Y"),
                    "time_slots":   time_slots,
                    "venues":       venues,
                    "matrix_data":  matrix_data,
                    "entry_count":  len(date_entries),
                    "num_rows":     len(venues),
                    "num_cols":     len(time_slots) + 1,
                }
            return result

        date_tables     = _build_date_tables_from_entries(main_exam_entries)
        lab_date_tables = _build_date_tables_from_entries(lab_exam_entries)

        if not date_tables and not lab_date_tables:
            return HttpResponse("No exam timetable data found.", content_type="text/plain")

        # --- Semester and Title ---
        semester = define_semester()
        current_year = current_date.year
        timetable_type = "Unified Examination"
        dynamic_title = template_config.title_format.format(timetable_type=timetable_type)
        prepared_by_name = template_config.director_full_name
        
        # Create PDF buffer with optimized settings
        buffer = io.BytesIO()
        
        # Determine orientation based on widest table across both sets
        all_tables_combined = list(date_tables.values()) + list(lab_date_tables.values())
        max_time_slots = max(
            [len(data["time_slots"]) for data in all_tables_combined],
            default=0
        )
        
        if max_time_slots > 4:  # If more than 4 time slots, use landscape
            pagesize = landscape(A4)
            # Adjust margins for landscape
            rightMargin = 20
            leftMargin = 20
            topMargin = 40
            bottomMargin = 40
        else:
            pagesize = A4
            rightMargin = 25
            leftMargin = 25
            topMargin = 50
            bottomMargin = 50
        
        # Use SimpleDocTemplate with calculated margins
        doc = SimpleDocTemplate(
            buffer,
            pagesize=pagesize,
            rightMargin=rightMargin,
            leftMargin=leftMargin,
            topMargin=topMargin,
            bottomMargin=bottomMargin,
            title=f"Exam Timetable - {semester} {current_year}"
        )
        
        elements = []
        styles = getSampleStyleSheet()

        # ── Styles matching the official Chuka University PDF template ──────────
        univ_name_style = ParagraphStyle(
            'UnivName', parent=styles['Normal'],
            fontSize=16, fontName='Times-Bold', alignment=1,
            spaceAfter=2, textColor=colors.black,
        )
        directorate_style = ParagraphStyle(
            'Directorate', parent=styles['Normal'],
            fontSize=11, fontName='Times-Bold', alignment=1,
            spaceAfter=2, textColor=colors.black,
        )
        contact_style = ParagraphStyle(
            'Contact', parent=styles['Normal'],
            fontSize=9, fontName='Times-Roman', alignment=0, textColor=colors.black,
        )
        ref_style = ParagraphStyle(
            'RefLine', parent=styles['Normal'],
            fontSize=9, fontName='Times-Bold', alignment=0, textColor=colors.black,
        )
        title_style = ParagraphStyle(
            'TimetableTitle', parent=styles['Normal'],
            fontSize=13, fontName='Times-Bold', alignment=1,
            spaceAfter=10, textColor=colors.black,
        )
        subtitle_style = ParagraphStyle(
            'DateSection', parent=styles['Normal'],
            fontSize=11, fontName='Times-Bold', alignment=1,
            spaceAfter=4, textColor=colors.black,
        )
        date_header_style = ParagraphStyle(
            'DateHeader', parent=styles['Normal'],
            fontSize=10, fontName='Times-Bold', alignment=0,
            spaceAfter=4, textColor=colors.black,
        )

        # ── University header block ──────────────────────────────────────────
        logo_img = None
        try:
            if template_config.university_logo and hasattr(template_config.university_logo, 'path'):
                logo_img = Image(template_config.university_logo.path, width=0.9*inch, height=0.9*inch)
        except Exception as e:
            print(f"Error loading logo: {e}")

        left_contact = f"Telephones: {template_config.telephone}\nDirect Line:"
        right_contact = (f"P. O. Box {template_config.address}\n"
                         f"Email: {template_config.email}   Website: {template_config.website}")

        left_para = Paragraph(left_contact.replace('\n', '<br/>'), contact_style)
        right_para = Paragraph(right_contact.replace('\n', '<br/>'),
                               ParagraphStyle('ContactRight', parent=contact_style, alignment=2))
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

        elements.append(Paragraph(template_config.university_name, univ_name_style))
        motto = f"Knowledge is Wealth (<i>{template_config.motto_latin}</i>) {template_config.motto_swahili}"
        elements.append(Paragraph(motto, ParagraphStyle(
            'Motto', parent=styles['Normal'], fontSize=9, alignment=1,
            fontName='Times-Roman', spaceAfter=2)))
        elements.append(Paragraph(template_config.directorate_name, directorate_style))
        elements.append(Spacer(1, 6))

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

        report_title = f"{dynamic_title} ({semester} {current_year})"
        elements.append(Paragraph(report_title, title_style))
        elements.append(Spacer(1, 6))

        # Track page number for headers
        current_page = 1
        total_pages = len(date_tables) + len(lab_date_tables) + 1

        # ── Helper: render one date_data dict into a table element ───────────
        def _render_date_table(date_data):
            """Build and return a ReportLab Table for a single date block."""
            t_data = []
            headers = ['Venue'] + date_data['time_slots']

            header_style_cell = ParagraphStyle(
                'TableHeaderCell', parent=styles['Normal'],
                fontSize=9, fontName='Times-Bold',
                textColor=colors.black, alignment=1, wordWrap='CJK', leading=12,
            )
            cell_style = ParagraphStyle(
                'TableCell', parent=styles['Normal'],
                fontSize=9, fontName='Times-Roman',
                textColor=colors.black, alignment=1, wordWrap='CJK', leading=11,
            )
            venue_cell_style = ParagraphStyle(
                'VenueCell', parent=styles['Normal'],
                fontSize=9, fontName='Times-Bold',
                textColor=colors.black, alignment=1, wordWrap='CJK', leading=11,
            )

            t_data.append([Paragraph(h, header_style_cell) for h in headers])
            for row_data in date_data['matrix_data']:
                venue_row = [Paragraph(row_data[0], venue_cell_style)]
                for cell_content in row_data[1:]:
                    venue_row.append(Paragraph(cell_content, cell_style))
                t_data.append(venue_row)

            num_columns = len(headers)
            page_width  = pagesize[0] - leftMargin - rightMargin
            vcw = max(min(page_width * 0.22, 100), 65)
            tsw = max(min((page_width - vcw) / max(num_columns - 1, 1), 130), 75)
            col_widths = [vcw] + [tsw] * (num_columns - 1)

            tbl = Table(t_data, colWidths=col_widths, repeatRows=1)

            row_heights = [28]
            for row in t_data[1:]:
                max_lines = 1
                for ci, cell in enumerate(row):
                    if hasattr(cell, 'text') and cell.text:
                        cw = col_widths[ci]
                        cpl = cw / 5.5
                        if cpl > 0:
                            nl = cell.text.count('\n') + 1
                            max_lines = max(max_lines, int(max(nl, len(cell.text) / cpl)) + 1)
                row_heights.append(max(22, min(13 * max_lines, 65)))

            tbl_style = TableStyle([
                ('BACKGROUND',   (0, 0), (-1,  0), colors.white),
                ('TEXTCOLOR',    (0, 0), (-1,  0), colors.black),
                ('ALIGN',        (0, 0), (-1,  0), 'CENTER'),
                ('VALIGN',       (0, 0), (-1,  0), 'MIDDLE'),
                ('FONTNAME',     (0, 0), (-1,  0), 'Times-Bold'),
                ('FONTSIZE',     (0, 0), (-1,  0), 9),
                ('BOTTOMPADDING',(0, 0), (-1,  0), 8),
                ('TOPPADDING',   (0, 0), (-1,  0), 8),
                ('BACKGROUND',   (0, 1), ( 0, -1), colors.white),
                ('FONTNAME',     (0, 1), ( 0, -1), 'Times-Bold'),
                ('TEXTCOLOR',    (0, 1), ( 0, -1), colors.black),
                ('GRID',         (0, 0), (-1, -1), 0.5, colors.black),
                ('BOX',          (0, 0), (-1, -1), 0.8, colors.black),
                ('LINEBELOW',    (0, 0), (-1,  0), 1.0, colors.black),
                ('BACKGROUND',   (1, 1), (-1, -1), colors.white),
                ('LEFTPADDING',  (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING',   (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
                ('ALIGN',        (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
                ('WORDWRAP',     (0, 0), (-1, -1), True),
            ])
            for ri, ht in enumerate(row_heights):
                tbl_style.add('ROWHEIGHTS', (0, ri), (-1, ri), ht)
            tbl.setStyle(tbl_style)
            return tbl

        # ── Section banner helper ─────────────────────────────────────────────
        def _section_banner(label, bg=colors.HexColor("#1a1a1a"), fg=colors.white):
            page_width = pagesize[0] - leftMargin - rightMargin
            banner = Table([[Paragraph(
                f"<b>{label}</b>",
                ParagraphStyle("SBan", parent=styles['Normal'],
                               fontSize=12, fontName='Times-Bold',
                               alignment=1, textColor=fg),
            )]], colWidths=[page_width])
            banner.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, -1), bg),
                ("LEFTPADDING",   (0, 0), (-1, -1), 8),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
                ("TOPPADDING",    (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            return banner

        # ── Render main exam date tables ──────────────────────────────────────
        main_list = list(date_tables.items())
        for date_idx, (date, date_data) in enumerate(main_list):
            elements.append(Paragraph(date_data['date_display'], date_header_style))
            elements.append(Spacer(1, 8))
            elements.append(_render_date_table(date_data))
            elements.append(Spacer(1, 25))
            if date_idx < len(main_list) - 1:
                elements.append(PageBreak())
                current_page += 1

        # ── Render lab exam date tables — clearly labelled separate section ───
        if lab_date_tables:
            elements.append(PageBreak())
            current_page += 1
            elements.append(_section_banner("LAB EXAMINATIONS"))
            elements.append(Spacer(1, 10))

            lab_list = list(lab_date_tables.items())
            for date_idx, (date, date_data) in enumerate(lab_list):
                elements.append(Paragraph(date_data['date_display'], date_header_style))
                elements.append(Spacer(1, 8))
                elements.append(_render_date_table(date_data))
                elements.append(Spacer(1, 25))
                if date_idx < len(lab_list) - 1:
                    elements.append(PageBreak())
                    current_page += 1
                    elements.append(_section_banner("LAB EXAMINATIONS (cont.)"))
                    elements.append(Spacer(1, 10))
        
        # ── Signature block matching the PDF template ──────────────────────────
        # Reference code now comes from the director's real initials + active
        # PdfSignatory rows (admin), instead of the old hardcoded "DIR/EXT".
        footer_data = template_config.get_footer_data(prepared_by=prepared_by_name)
        elements.append(Spacer(1, 30))

        sig_style = ParagraphStyle(
            'Sig', parent=styles['Normal'],
            fontSize=10, fontName='Times-Roman', textColor=colors.black, alignment=0,
        )
        sig_bold = ParagraphStyle(
            'SigBold', parent=sig_style, fontName='Times-Bold',
        )
        nb_style = ParagraphStyle(
            'NB', parent=styles['Normal'], fontSize=8,
            fontName='Times-Roman', textColor=colors.black, alignment=0,
        )

        # NB section from key_section if available
        if template_config.key_section and template_config.key_section.strip():
            elements.append(Paragraph("<b>NB:</b>", nb_style))
            for line in template_config.key_section.strip().splitlines():
                if line.strip():
                    elements.append(Paragraph(line.strip(), nb_style))
            elements.append(Spacer(1, 15))

        # Prepared by label
        elements.append(Paragraph(f"<b>{footer_data['prepared_by_label']}</b>", sig_bold))
        elements.append(Spacer(1, 40))  # space for signature

        # Director's full name, designation, then the GAO/fm/sk reference code
        elements.append(Paragraph(footer_data['prepared_by'], sig_bold))
        elements.append(Paragraph(f"<b>{footer_data['director_label']}</b>", sig_bold))
        if footer_data.get('signature_code'):
            elements.append(Paragraph(footer_data['signature_code'], sig_style))
        
        # ── Page furniture: watermark (drawn first, so content paints over it) + page number ──
        def add_page_furniture(canvas_obj, doc_obj):
            draw_reportlab_watermark(canvas_obj, doc_obj, template_config, doc_ref=reference_number)
            canvas_obj.saveState()
            canvas_obj.setFont('Times-Roman', 8)
            page_num_text = f"Page {canvas_obj.getPageNumber()} of {doc_obj.page}"
            canvas_obj.drawCentredString(pagesize[0] / 2.0, 15, page_num_text)
            canvas_obj.restoreState()

        # Build PDF
        doc.build(elements, onFirstPage=add_page_furniture, onLaterPages=add_page_furniture)
        
        # Get PDF value from buffer
        pdf = buffer.getvalue()
        buffer.close()
        
        # Create HTTP response with PDF - Use attachment for download
        response = HttpResponse(content_type='application/pdf')
        filename = f"exam_timetable_{current_year}_{semester.split()[0]}_{current_date.strftime('%Y%m%d_%H%M')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response.write(pdf)
        
        return response
        
    except Exception as e:
        # Log error and return error response
        import traceback
        error_msg = f"Error generating exam PDF: {str(e)}\n\n{traceback.format_exc()}"
        print(error_msg)
        
        return HttpResponse(
            f"Error generating exam PDF: {str(e)}",
            status=500,
            content_type='text/plain'
        )