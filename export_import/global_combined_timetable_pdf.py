"""
global_combined_timetable_pdf.py
=================================
Generates a GLOBAL COMBINED PDF that includes:
  - Main campus: Regular timetable + Lab timetable
  - All satellite campuses: their class + exam + lab timetables

Two separate views/URLs are exposed:
  - /export/global/regular-timetable/   → regular (teaching) combined PDF
  - /export/global/exam-timetable/      → exam combined PDF

Place this file in:   export_import/global_combined_timetable_pdf.py

Register the two URLs in export_import/urls.py:
    from . import global_combined_timetable_pdf as global_pdf
    urlpatterns += [
        path('export/global/regular-timetable/', global_pdf.export_global_regular_pdf,
             name='export_global_regular_pdf'),
        path('export/global/exam-timetable/',    global_pdf.export_global_exam_pdf,
             name='export_global_exam_pdf'),
    ]

Design mirrors the official Chuka University letterhead — black and white
only, one typeface (Times New Roman) throughout:
  - Logo centred at the top, contact details left/right beneath it
  - University name bold centred, motto below, directorate bold centred
  - Ref/date row with horizontal rule
  - Black centred report title
  - Day/date section headers in bold black (centred)
  - Black grid tables, white cells, bold column headers
  - Lab section in dark banner
  - Campus sections separated by page-breaks with bold campus banners
  - Signature block at the end of each major section

Color scheme (matching official PDF — black and white only):
  - Header text:          black
  - Report title:         black
  - Table header bg:      white, text black bold
  - Table cell bg:        white, text black
  - Table borders:        black 0.5 pt grid, 0.8 pt box
  - Day/section banners:  dark (#1a1a1a) bg, white text
  - Campus banner:        navy (#003366) bg, white text
"""

# ─────────────────────────────────────────────────────────────────────────────
# Standard library
# ─────────────────────────────────────────────────────────────────────────────
import io
import os
import base64
from datetime import datetime, timedelta
from collections import defaultdict, OrderedDict

# ─────────────────────────────────────────────────────────────────────────────
# Django
# ─────────────────────────────────────────────────────────────────────────────
from django.http import HttpResponse
from django.utils import timezone
from django.conf import settings
from django.templatetags.static import static

# ─────────────────────────────────────────────────────────────────────────────
# ReportLab
# ─────────────────────────────────────────────────────────────────────────────
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import inch, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    Table, TableStyle, PageBreak, Image, HRFlowable,
)

# ─────────────────────────────────────────────────────────────────────────────
# App models
# ─────────────────────────────────────────────────────────────────────────────
from timetable.models import (
    Timetable,
    MergedCourseGroupTimetable,
    AutoMergedExamGroup,          # backward-compat alias
    SchedulerConfig,
    LabTimetable,
    ExamTimetable,
    MergedCourseGroup,
    SharedVenueExamGroup,
    LabExamTimetable,
)
from room_management.models import Venue
from campuses_timetable.models import (
    Campus,
    CampusTimetable,
    CampusExamTimetable,
    CampusLabTimetable,
    CampusLabExamTimetable,
    CampusTimetableTemplate,
)
from export_import.models import TimetablePdfTemplate


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
DAYS_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

# Colors matching the official template
COLOR_TITLE       = colors.black
COLOR_DARK_BANNER = colors.HexColor("#1a1a1a")
COLOR_CAMPUS_BAN  = colors.HexColor("#003366")   # navy for campus sections
COLOR_LAB_BANNER  = colors.HexColor("#333333")
COLOR_WHITE       = colors.white
COLOR_BLACK       = colors.black
COLOR_LIGHT_GRAY  = colors.HexColor("#F5F5F5")   # very subtle alternating row tint


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ordinal(n):
    """Return ordinal string, e.g. 31 → '31st'."""
    s = ["th", "st", "nd", "rd"] + ["th"] * 16
    return str(n) + (s[n % 100] if n % 100 <= 20 else s[n % 10])


def _slot_label(st, en):
    """Format a time-slot label from two time objects."""
    return f"{st.strftime('%I:%M %p').lstrip('0')}→{en.strftime('%I:%M%p').lstrip('0')}"


def _define_semester():
    """
    Determine current semester (SEPTEMBER-DECEMBER or JANUARY-APRIL)
    from majority of allocated courses.
    """
    try:
        from course_allocation.models import CourseAllocation
        from program_management.models import ProgramCourse
        from course_allocation.config_helpers import strip_course_code_tag

        s1 = s2 = 0
        threshold = 10
        for alloc in CourseAllocation.objects.select_related("program").all():
            pc = ProgramCourse.objects.filter(
                program=alloc.program, course_code__iexact=strip_course_code_tag(alloc.course_code)
            ).first()
            if not pc:
                continue
            if pc.semester == 1:
                s1 += 1
            elif pc.semester == 2:
                s2 += 1
            if abs(s1 - s2) > threshold:
                break

        if s1 > s2:
            return "SEPTEMBER - DECEMBER"
        elif s2 > s1:
            return "JANUARY - APRIL"
    except Exception:
        pass
    return "JANUARY - APRIL"


def _get_logo_base64(template_config):
    """
    Return a ReportLab Image object from the logo stored on the template,
    or None if not available.
    """
    try:
        if template_config.university_logo and hasattr(template_config.university_logo, "path"):
            logo_path = template_config.university_logo.path
            if os.path.exists(logo_path):
                return Image(logo_path, width=0.85 * inch, height=0.85 * inch)
    except Exception:
        pass

    # Fallback: try STATICFILES_DIRS for chuka.png
    try:
        for sd in getattr(settings, "STATICFILES_DIRS", []):
            candidate = os.path.join(sd, "images", "chuka.png")
            if os.path.exists(candidate):
                return Image(candidate, width=0.85 * inch, height=0.85 * inch)
    except Exception:
        pass
    return None


def _get_scheduler_time_slots():
    """
    Build time-slot labels from SchedulerConfig (default 07:00-19:00, 3-hour slots).
    Returns list of strings like '7:00 AM→10:00AM'.
    """
    config = SchedulerConfig.objects.first()
    start_time = config.start_time if config else datetime.strptime("07:00", "%H:%M").time()
    end_time   = config.end_time   if config else datetime.strptime("19:00", "%H:%M").time()
    slot_size  = config.slot_size  if config else 3

    slots = []
    cur = datetime.combine(datetime.today(), start_time)
    end = datetime.combine(datetime.today(), end_time)
    while cur < end:
        nxt = min(cur + timedelta(hours=slot_size), end)
        slots.append(_slot_label(cur.time(), nxt.time()))
        cur = nxt
    return slots


# ─────────────────────────────────────────────────────────────────────────────
# Style factories (all depend only on getSampleStyleSheet)
# ─────────────────────────────────────────────────────────────────────────────

def _build_styles(base_styles):
    """Return a dict of named ParagraphStyles matching the official template."""
    s = {}

    s["univ_name"] = ParagraphStyle(
        "UnivName", parent=base_styles["Normal"],
        fontSize=16, fontName="Times-Bold",
        alignment=1, spaceAfter=2, textColor=COLOR_BLACK,
    )
    s["motto"] = ParagraphStyle(
        "Motto", parent=base_styles["Normal"],
        fontSize=9, fontName="Times-Roman",
        alignment=1, spaceAfter=2, textColor=COLOR_BLACK,
    )
    s["directorate"] = ParagraphStyle(
        "Directorate", parent=base_styles["Normal"],
        fontSize=11, fontName="Times-Bold",
        alignment=1, spaceAfter=2, textColor=COLOR_BLACK,
    )
    s["contact_left"] = ParagraphStyle(
        "ContactLeft", parent=base_styles["Normal"],
        fontSize=9, fontName="Times-Roman",
        alignment=0, textColor=COLOR_BLACK,
    )
    s["contact_right"] = ParagraphStyle(
        "ContactRight", parent=base_styles["Normal"],
        fontSize=9, fontName="Times-Roman",
        alignment=2, textColor=COLOR_BLACK,
    )
    s["ref"] = ParagraphStyle(
        "Ref", parent=base_styles["Normal"],
        fontSize=9, fontName="Times-Bold",
        alignment=0, textColor=COLOR_BLACK,
    )
    s["ref_right"] = ParagraphStyle(
        "RefRight", parent=base_styles["Normal"],
        fontSize=9, fontName="Times-Bold",
        alignment=2, textColor=COLOR_BLACK,
    )
    s["report_title"] = ParagraphStyle(
        "ReportTitle", parent=base_styles["Normal"],
        fontSize=13, fontName="Times-Bold",
        alignment=1, spaceAfter=10, textColor=COLOR_TITLE,
    )
    s["day_section"] = ParagraphStyle(
        "DaySection", parent=base_styles["Normal"],
        fontSize=11, fontName="Times-Bold",
        alignment=1, spaceAfter=4, textColor=COLOR_BLACK,
    )
    s["table_header"] = ParagraphStyle(
        "TableHeader", parent=base_styles["Normal"],
        fontSize=9, fontName="Times-Bold",
        alignment=1, textColor=COLOR_BLACK, wordWrap="CJK",
    )
    s["table_cell"] = ParagraphStyle(
        "TableCell", parent=base_styles["Normal"],
        fontSize=8, fontName="Times-Roman",
        alignment=1, textColor=COLOR_BLACK, wordWrap="CJK", leading=10,
    )
    s["banner_text"] = ParagraphStyle(
        "BannerText", parent=base_styles["Normal"],
        fontSize=12, fontName="Times-Bold",
        alignment=1, textColor=COLOR_WHITE,
    )
    s["campus_banner_text"] = ParagraphStyle(
        "CampusBannerText", parent=base_styles["Normal"],
        fontSize=13, fontName="Times-Bold",
        alignment=1, textColor=COLOR_WHITE,
    )
    s["sig"] = ParagraphStyle(
        "Sig", parent=base_styles["Normal"],
        fontSize=10, fontName="Times-Roman",
        alignment=0, textColor=COLOR_BLACK,
    )
    s["sig_bold"] = ParagraphStyle(
        "SigBold", parent=base_styles["Normal"],
        fontSize=10, fontName="Times-Bold",
        alignment=0, textColor=COLOR_BLACK,
    )
    s["nb"] = ParagraphStyle(
        "NB", parent=base_styles["Normal"],
        fontSize=8, fontName="Times-Roman",
        alignment=0, textColor=COLOR_BLACK,
    )
    return s


# ─────────────────────────────────────────────────────────────────────────────
# Reusable element builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_letterhead(elements, s, template_config, ref_number, date_str, page_width):
    """
    Append the full Chuka University letterhead block to `elements`.
    Matches layout of the official PDF template exactly.
    """
    logo_img = _get_logo_base64(template_config)

    # Three-column header: contact-left | logo | contact-right
    left_txt = (
        f"Telephones: {getattr(template_config, 'telephone', '')}<br/>"
        f"Direct Line:"
    )
    right_txt = (
        f"P. O. Box {getattr(template_config, 'address', '')}<br/>"
        f"Email: {getattr(template_config, 'email', '')} &nbsp; "
        f"Website: {getattr(template_config, 'website', '')}"
    )
    logo_cell = logo_img if logo_img else Paragraph("", s["contact_left"])
    hdr_data = [[
        Paragraph(left_txt,  s["contact_left"]),
        logo_cell,
        Paragraph(right_txt, s["contact_right"]),
    ]]
    hdr_table = Table(hdr_data, colWidths=[160, 80, page_width - 240])
    hdr_table.setStyle(TableStyle([
        ("VALIGN",  (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",   (0, 0), (0,  0),  "LEFT"),
        ("ALIGN",   (1, 0), (1,  0),  "CENTER"),
        ("ALIGN",   (2, 0), (2,  0),  "RIGHT"),
    ]))
    elements.append(hdr_table)
    elements.append(Spacer(1, 4))

    # University name
    elements.append(Paragraph(
        getattr(template_config, "university_name", "CHUKA UNIVERSITY"), s["univ_name"]
    ))

    # Motto
    motto_latin  = getattr(template_config, "motto_latin",   "Sapientia divitia est")
    motto_swahili = getattr(template_config, "motto_swahili", "Akili ni Mali")
    elements.append(Paragraph(
        f"Knowledge is Wealth (<i>{motto_latin}</i>) {motto_swahili}", s["motto"]
    ))

    # Directorate
    elements.append(Paragraph(
        getattr(template_config, "directorate_name", "DIRECTORATE OF EXAMINATIONS AND TIMETABLING"),
        s["directorate"]
    ))
    elements.append(Spacer(1, 6))

    # Ref / Date row with bottom rule
    ref_data = [[
        Paragraph(f"<b>Ref: {ref_number}</b>",    s["ref"]),
        Paragraph(f"<b>Date: {date_str}</b>",     s["ref_right"]),
    ]]
    ref_table = Table(ref_data, colWidths=[page_width / 2, page_width / 2])
    ref_table.setStyle(TableStyle([
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW",  (0, 0), (-1, -1), 0.5, COLOR_BLACK),
    ]))
    elements.append(ref_table)
    elements.append(Spacer(1, 8))


def _build_banner(label, page_width, s, bg=None, fg=None):
    """
    Return a dark-banner Table element with the given label.
    Used for section separators (day banners, lab banners, campus banners).
    """
    bg = bg or COLOR_DARK_BANNER
    fg = fg or COLOR_WHITE
    style_name = "campus_banner_text" if bg == COLOR_CAMPUS_BAN else "banner_text"
    para = Paragraph(f"<b>{label}</b>", s[style_name])
    tbl = Table([[para]], colWidths=[page_width])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), bg),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return tbl


def _standard_table_style():
    """Return the standard timetable TableStyle (black grid, white bg, bold header)."""
    return TableStyle([
        # Header row
        ("BACKGROUND",    (0, 0), (-1,  0), COLOR_WHITE),
        ("TEXTCOLOR",     (0, 0), (-1,  0), COLOR_BLACK),
        ("ALIGN",         (0, 0), (-1,  0), "CENTER"),
        ("FONTNAME",      (0, 0), (-1,  0), "Times-Bold"),
        ("FONTSIZE",      (0, 0), (-1,  0), 9),
        ("BOTTOMPADDING", (0, 0), (-1,  0), 6),
        ("TOPPADDING",    (0, 0), (-1,  0), 6),
        # Grid
        ("GRID",          (0, 0), (-1, -1), 0.5, COLOR_BLACK),
        ("BOX",           (0, 0), (-1, -1), 0.8, COLOR_BLACK),
        # Data rows
        ("BACKGROUND",    (0, 1), (-1, -1), COLOR_WHITE),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("TOPPADDING",    (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("WORDWRAP",      (0, 0), (-1, -1), True),
    ])


def _build_day_grid_table(day_label, rows, time_slots, s, page_width, row_header="ROOM"):
    """
    Build and return a list of elements for one day's timetable grid.
    `rows` is a list of dicts: {"venue_key": str, "cells": [{"entries": [{"course_code": str}]}]}
    `row_header` is the label for the first column (e.g. "ROOM" or "PROGRAMME").
    """
    elems = []

    # Day section header
    elems.append(Paragraph(day_label.upper(), s["day_section"]))
    elems.append(Spacer(1, 4))

    # Build table data
    headers = [row_header] + time_slots
    tbl_data = [[Paragraph(h, s["table_header"]) for h in headers]]

    for row in rows:
        r = [Paragraph(row["venue_key"], s["table_cell"])]
        for cell in row["cells"]:
            if cell["entries"]:
                text = "\n".join(e["course_code"] for e in cell["entries"] if e.get("course_code"))
                r.append(Paragraph(text, s["table_cell"]))
            else:
                r.append(Paragraph("", s["table_cell"]))
        tbl_data.append(r)

    # Column widths: fixed venue col, equal time-slot cols
    n_cols = len(headers)
    venue_w = 75
    slot_w  = (page_width - venue_w) / max(n_cols - 1, 1)
    col_widths = [venue_w] + [slot_w] * (n_cols - 1)

    tbl = Table(tbl_data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(_standard_table_style())
    elems.append(tbl)
    elems.append(Spacer(1, 24))
    return elems


def _append_overflow_pages(elements, s, config):
    """Append Evening and Weekend class pages if enabled in config."""
    from reportlab.platypus import PageBreak, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph
    from reportlab.lib.styles import ParagraphStyle
    from collections import defaultdict, OrderedDict
    from timetable.models import Timetable

    pw = A4[0] - 36 - 36

    cell_s = s.get("cell", s.get("body", None))
    hdr_s  = s.get("col_hdr", s.get("header", None))
    sub_s  = s.get("day_title", s.get("subtitle", None))

    def _tbl_style(hdr_bg):
        return TableStyle([
            ('BACKGROUND',    (0, 0), (-1, 0), hdr_bg),
            ('FONTNAME',      (0, 0), (-1, 0), 'Times-Bold'),
            ('GRID',          (0, 0), (-1, -1), 0.5, colors.black),
            ('BOX',           (0, 0), (-1, -1), 0.8, colors.black),
            ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',   (0, 0), (-1, -1), 3),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 3),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ])

    def _banner(text, bg_hex):
        from reportlab.platypus import Paragraph
        ban_s = ParagraphStyle('OvBan', fontSize=12, fontName='Times-Bold',
                               alignment=1, textColor=colors.white)
        tbl = Table([[Paragraph(f"<b>{text}</b>", ban_s)]], colWidths=[pw])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor(bg_hex)),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("TOPPADDING",    (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        return tbl

    def _gen_slots(start_t, end_t, slot_hrs):
        from datetime import datetime, timedelta
        slots, cur = [], datetime.combine(datetime.today(), start_t)
        end_dt = datetime.combine(datetime.today(), end_t)
        while cur < end_dt:
            nxt = min(cur + timedelta(hours=slot_hrs), end_dt)
            slots.append(f"{cur.strftime('%H:%M')} - {nxt.strftime('%H:%M')}")
            cur = nxt
        return slots

    def _build_grid(qs_days, slot_labels, filter_fn):
        by_slot_day_venue = defaultdict(lambda: defaultdict(dict))
        for e in Timetable.objects.select_related("course_allocation", "venue").all():
            if not filter_fn(e):
                continue
            if not e.start_time or not e.end_time or not e.venue:
                continue
            sl = f"{e.start_time.strftime('%H:%M')} - {e.end_time.strftime('%H:%M')}"
            vk = e.venue.code if e.venue else "Unknown"
            dy = (e.day or "").lower()
            by_slot_day_venue[sl][dy].setdefault(vk, []).append(
                getattr(e.course_allocation, "course_code", "") if e.course_allocation else ""
            )
        day_grids = OrderedDict()
        for day in qs_days:
            venues = set()
            for sl in slot_labels:
                venues.update(by_slot_day_venue.get(sl, {}).get(day.lower(), {}).keys())
            if not venues:
                continue
            rows = []
            for vk in sorted(venues):
                cells = []
                for sl in slot_labels:
                    codes = by_slot_day_venue.get(sl, {}).get(day.lower(), {}).get(vk, [])
                    cells.append("\n".join(codes))
                rows.append((vk, cells))
            day_grids[day] = rows
        return day_grids

    def _render_grid(elements, day_grids, slot_labels, hdr_bg, sub_s, hdr_s, cell_s):
        days_list = list(day_grids.items())
        for idx, (day, rows) in enumerate(days_list):
            if sub_s:
                elements.append(Paragraph(day.upper(), sub_s))
            elements.append(Spacer(1, 4))
            tbl_data = [[Paragraph(h, hdr_s) if hdr_s else h for h in ['Venue'] + slot_labels]]
            for vk, cells in rows:
                vrow = [Paragraph(vk, cell_s) if cell_s else vk]
                vrow += [Paragraph(c, cell_s) if cell_s else c for c in cells]
                tbl_data.append(vrow)
            cw = [80] + [100] * len(slot_labels)
            tbl = Table(tbl_data, colWidths=cw, repeatRows=1)
            tbl.setStyle(_tbl_style(hdr_bg))
            elements.append(tbl)
            elements.append(Spacer(1, 24))
            if idx < len(days_list) - 1:
                elements.append(PageBreak())

    # Evening
    if getattr(config, 'enable_evening_classes', False):
        ev_slots = _gen_slots(config.evening_start_time, config.evening_end_time, config.slot_size)
        ev_slots = ev_slots[:config.evening_slot_count]

        def _is_evening(e):
            t = e.start_time.hour * 100 + e.start_time.minute if e.start_time else 0
            return t >= 1900 and (e.day or '') not in ('Saturday', 'Sunday')

        ev_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        ev_grid = _build_grid(ev_days, ev_slots, _is_evening)
        if ev_grid:
            elements.append(PageBreak())
            elements.append(_banner("EVENING CLASSES TIMETABLE", "#1565c0"))
            elements.append(Spacer(1, 10))
            _render_grid(elements, ev_grid, ev_slots, colors.HexColor("#d0e8ff"), sub_s, hdr_s, cell_s)

    # Weekend
    if getattr(config, 'enable_weekend_classes', False):
        wk_slots = _gen_slots(config.weekend_start_time, config.weekend_end_time, config.weekend_slot_size)

        def _is_weekend(e):
            return (e.day or '').capitalize() in ('Saturday', 'Sunday')

        wk_days = ["Saturday", "Sunday"]
        wk_grid = _build_grid(wk_days, wk_slots, _is_weekend)
        if wk_grid:
            elements.append(PageBreak())
            elements.append(_banner("WEEKEND CLASSES TIMETABLE", "#6a1b9a"))
            elements.append(Spacer(1, 10))
            _render_grid(elements, wk_grid, wk_slots, colors.HexColor("#ede1f5"), sub_s, hdr_s, cell_s)


def _build_signature(elements, s, template_config):
    """Append signature / prepared-by block."""
    elements.append(Spacer(1, 30))
    try:
        footer_data = template_config.get_footer_data(
            prepared_by=getattr(template_config, "directorate_name", "Director (Examinations and Timetabling)"),
            director_initials="DIR",
            sub_director_initials="EXT",
        )
        elements.append(Paragraph(f"<b>{footer_data.get('prepared_by_label', 'Prepared by:')}</b>", s["sig_bold"]))
        elements.append(Spacer(1, 40))
        elements.append(Paragraph(footer_data.get("prepared_by", ""), s["sig_bold"]))
        elements.append(Paragraph(f"<b>{footer_data.get('director_label', '')}</b>", s["sig_bold"]))
    except Exception:
        elements.append(Paragraph("<b>Prepared by:</b>", s["sig_bold"]))
        elements.append(Spacer(1, 40))
        elements.append(Paragraph("<b>Director (Examinations and Timetabling)</b>", s["sig_bold"]))


def _build_nb_section(elements, s, template_config):
    """Append the NB/KEY section if configured on the template."""
    try:
        key_section = getattr(template_config, "key_section", "") or ""
        if key_section.strip():
            elements.append(Spacer(1, 10))
            elements.append(Paragraph("<b>NB:</b>", s["nb"]))
            for line in key_section.strip().splitlines():
                if line.strip():
                    elements.append(Paragraph(line.strip(), s["nb"]))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Data collection helpers: main campus regular timetable
# ─────────────────────────────────────────────────────────────────────────────

def _collect_main_regular(time_slots):
    """
    Collect and organise main-campus regular timetable data.
    Returns (day_grids, lab_day_grids, lab_time_slots).
    """
    venues_cap = {v.code: v.capacity for v in Venue.objects.only("code", "capacity")}

    def venue_key(obj):
        code = (
            getattr(getattr(obj, "venue", None), "code", None)
            or getattr(getattr(obj, "lab_venue", None), "code", None)
            or "Unknown"
        )
        cap = venues_cap.get(code, "")
        return f"{code} ({cap})" if cap else code

    def slot_lbl(st, en):
        return f"{st.strftime('%I:%M %p').lstrip('0')}→{en.strftime('%I:%M%p').lstrip('0')}"

    # Regular timetable entries
    tt_qs = (
        Timetable.objects
        .select_related("course_allocation", "venue")
        .only("day", "start_time", "end_time", "venue__code",
              "course_allocation__course_code")
        .exclude(day__iexact="Saturday")
        .order_by("venue__code", "start_time", "day")
    )

    # Merged timetable entries (published)
    merged_qs = (
        MergedCourseGroupTimetable.objects
        .select_related("venue")
        .prefetch_related("merged_courses")
        .filter(published=True)
        .only("merged_code", "date", "start_time", "end_time", "venue__code")
        .order_by("venue__code", "start_time")
    )

    # Lab timetable
    lab_qs = (
        LabTimetable.objects
        .select_related("lab_allocation__program_course", "lab_venue")
        .only("day", "start_time", "end_time",
              "lab_allocation__program_course__course_code", "lab_venue__code")
        .exclude(day__iexact="Saturday")
        .order_by("lab_venue__code", "day", "start_time")
    )

    # ── Organise into slot → day → venue → [entries] ────────────────────────
    by_slot_day_venue = defaultdict(lambda: defaultdict(dict))

    for e in tt_qs:
        sl = slot_lbl(e.start_time, e.end_time)
        vk = venue_key(e)
        dy = e.day.lower()
        if vk not in by_slot_day_venue[sl][dy]:
            by_slot_day_venue[sl][dy][vk] = []
        code = getattr(e.course_allocation, "course_code", "") or ""
        by_slot_day_venue[sl][dy][vk].append({"course_code": code})

    for m in merged_qs:
        if not (m.start_time and m.end_time):
            continue
        date_str = str(m.date).lower() if m.date else ""
        if "saturday" in date_str:
            continue
        sl = slot_lbl(m.start_time, m.end_time)
        vk = venue_key(m)
        member_codes = [getattr(mc, "course_code", "") for mc in m.merged_courses.all()]
        display = m.merged_code or ", ".join(member_codes)
        if vk not in by_slot_day_venue[sl][date_str]:
            by_slot_day_venue[sl][date_str][vk] = []
        by_slot_day_venue[sl][date_str][vk].append({"course_code": display})

    # ── Day grids ────────────────────────────────────────────────────────────
    day_grids = OrderedDict()
    for day in DAYS_ORDER:
        venues_set = set()
        for sl in time_slots:
            venues_set.update(by_slot_day_venue.get(sl, {}).get(day.lower(), {}).keys())
        if not venues_set:
            continue
        rows = []
        for vk in sorted(venues_set):
            cells = []
            for sl in time_slots:
                entries = by_slot_day_venue.get(sl, {}).get(day.lower(), {}).get(vk, [])
                cells.append({"entries": entries})
            rows.append({"venue_key": vk, "cells": cells})
        day_grids[day] = rows

    # ── Lab timetable ────────────────────────────────────────────────────────
    lab_slot_day_venue = defaultdict(lambda: defaultdict(dict))
    for l in lab_qs:
        sl = slot_lbl(l.start_time, l.end_time)
        vk = venue_key(l)
        dy = l.day.lower()
        code = getattr(l.lab_allocation.program_course, "course_code", "") or ""
        if vk not in lab_slot_day_venue[sl][dy]:
            lab_slot_day_venue[sl][dy][vk] = []
        lab_slot_day_venue[sl][dy][vk].append({"course_code": code})

    lab_time_slots = sorted(lab_slot_day_venue.keys())

    lab_day_grids = OrderedDict()
    for day in DAYS_ORDER:
        lab_venues = set()
        for sl in lab_time_slots:
            lab_venues.update(lab_slot_day_venue.get(sl, {}).get(day.lower(), {}).keys())
        if not lab_venues:
            continue
        rows = []
        for vk in sorted(lab_venues):
            cells = []
            for sl in lab_time_slots:
                entries = lab_slot_day_venue.get(sl, {}).get(day.lower(), {}).get(vk, [])
                cells.append({"entries": entries})
            rows.append({"venue_key": vk, "cells": cells})
        lab_day_grids[day] = rows

    return day_grids, lab_day_grids, lab_time_slots


# ─────────────────────────────────────────────────────────────────────────────
# Data collection helpers: main campus exam timetable
# ─────────────────────────────────────────────────────────────────────────────

def _collect_main_exam():
    """
    Collect main-campus exam timetable entries.
    Returns (date_grids, lab_date_grids) where each is an OrderedDict
    keyed by date-string, value = {time_slot: {venue: [course_codes]}}.
    """
    def slot_lbl(st, en):
        return f"{st.strftime('%I:%M %p').lstrip('0')}→{en.strftime('%I:%M%p').lstrip('0')}"

    # Main exams
    exams_qs = (
        ExamTimetable.objects
        .select_related("course_allocation", "venue")
        .order_by("date", "start_time", "venue__code")
    )

    # Lab exams
    lab_exams_qs = (
        LabExamTimetable.objects
        .select_related("lab_allocation__program_course", "lab_venue")
        .order_by("date", "start_time", "lab_venue__code")
    )

    # Shared venue groups
    shared_qs = (
        SharedVenueExamGroup.objects
        .filter(published=True)
        .select_related("venue")
        .prefetch_related("course_allocations")
        .order_by("date", "start_time")
    )

    # date → slot → venue → [codes]
    date_slot_venue = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    seen = set()

    def _add(date, sl, venue, code):
        key = (date, sl, venue, code)
        if key not in seen:
            seen.add(key)
            date_slot_venue[date][sl][venue].append(code)

    for e in exams_qs:
        if not (e.date and e.start_time and e.end_time):
            continue
        vname = getattr(e.venue, "code", None) or "Unassigned"
        sl    = slot_lbl(e.start_time, e.end_time)
        code  = getattr(e.course_allocation, "course_code", "") or ""
        _add(str(e.date), sl, vname, code)

    for g in shared_qs:
        if not (g.date and g.start_time and g.end_time):
            continue
        vname = getattr(g.venue, "code", None) or "Unassigned"
        sl    = slot_lbl(g.start_time, g.end_time)
        for ca in g.course_allocations.all():
            _add(str(g.date), sl, vname, getattr(ca, "course_code", "") or "")

    # Build grids per date
    date_grids = OrderedDict()
    for date_str in sorted(date_slot_venue.keys()):
        slots = sorted(date_slot_venue[date_str].keys())
        venues = sorted({v for sl in slots for v in date_slot_venue[date_str][sl]})
        rows = []
        for vk in venues:
            cells = []
            for sl in slots:
                codes = date_slot_venue[date_str][sl].get(vk, [])
                cells.append({"entries": [{"course_code": c} for c in codes]})
            rows.append({"venue_key": vk, "cells": cells, "time_slots": slots})
        date_grids[date_str] = {"rows": rows, "time_slots": slots}

    # Lab exam grids
    lab_date_slot_venue = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    lab_seen = set()

    def _add_lab(date, sl, venue, code):
        key = (date, sl, venue, code)
        if key not in lab_seen:
            lab_seen.add(key)
            lab_date_slot_venue[date][sl][venue].append(code)

    for l in lab_exams_qs:
        if not (l.date and l.start_time and l.end_time):
            continue
        vname = getattr(l.lab_venue, "code", None) or "Unassigned"
        sl    = slot_lbl(l.start_time, l.end_time)
        code  = getattr(l.lab_allocation.program_course, "course_code", "") or ""
        _add_lab(str(l.date), sl, vname, code)

    lab_date_grids = OrderedDict()
    for date_str in sorted(lab_date_slot_venue.keys()):
        slots   = sorted(lab_date_slot_venue[date_str].keys())
        venues  = sorted({v for sl in slots for v in lab_date_slot_venue[date_str][sl]})
        rows    = []
        for vk in venues:
            cells = []
            for sl in slots:
                codes = lab_date_slot_venue[date_str][sl].get(vk, [])
                cells.append({"entries": [{"course_code": c} for c in codes]})
            rows.append({"venue_key": vk, "cells": cells})
        lab_date_grids[date_str] = {"rows": rows, "time_slots": slots}

    return date_grids, lab_date_grids


# ─────────────────────────────────────────────────────────────────────────────
# Data collection helpers: campus (satellite) timetables
# ─────────────────────────────────────────────────────────────────────────────

def _collect_campus_regular(campus, time_slots):
    """
    Collect CampusTimetable entries for a given campus.
    Campus models have NO venue field — entries are grouped by programme/department
    as the row label, with course codes filling the time-slot cells.
    Returns (day_grids, lab_day_grids, lab_time_slots).
    """
    def slot_lbl(st, en):
        return f"{st.strftime('%I:%M %p').lstrip('0')}→{en.strftime('%I:%M%p').lstrip('0')}"

    # No venue — select only the fields that actually exist on CampusTimetable
    tt_qs = (
        CampusTimetable.objects
        .filter(campus=campus)
        .select_related("course_allocation", "course_allocation__program")
        .exclude(day__iexact="Saturday")
        .order_by("course_allocation__program__name", "start_time", "day")
    )

    # Group by slot → day → program (row label) → [course codes]
    # We use program name as the "row" equivalent of a venue since there's no venue field
    by_slot_day_dept = defaultdict(lambda: defaultdict(dict))
    for e in tt_qs:
        sl   = slot_lbl(e.start_time, e.end_time)
        prog = (
            getattr(getattr(e.course_allocation, "program", None), "name", None)
            or getattr(e.course_allocation, "course_code", "")
            or "General"
        )
        dy   = e.day.lower()
        code = getattr(e.course_allocation, "course_code", "") or ""
        if prog not in by_slot_day_dept[sl][dy]:
            by_slot_day_dept[sl][dy][prog] = []
        by_slot_day_dept[sl][dy][prog].append({"course_code": code})

    day_grids = OrderedDict()
    for day in DAYS_ORDER:
        depts_set = set()
        for sl in by_slot_day_dept:
            depts_set.update(by_slot_day_dept[sl].get(day.lower(), {}).keys())
        if not depts_set:
            continue
        day_slots = sorted(by_slot_day_dept.keys())
        rows = []
        for dept in sorted(depts_set):
            cells = []
            for sl in day_slots:
                entries = by_slot_day_dept.get(sl, {}).get(day.lower(), {}).get(dept, [])
                cells.append({"entries": entries})
            rows.append({"venue_key": dept, "cells": cells})
        day_grids[day] = {"rows": rows, "time_slots": day_slots}

    # Lab timetable — also no lab_venue field
    try:
        lab_qs = (
            CampusLabTimetable.objects
            .filter(campus=campus)
            .select_related("lab_allocation__program_course")
            .exclude(day__iexact="Saturday")
            .order_by("lab_allocation__program_course__course_code", "day", "start_time")
        )
        lab_slot_day_venue = defaultdict(lambda: defaultdict(dict))
        for l in lab_qs:
            sl   = slot_lbl(l.start_time, l.end_time)
            # Use course_code as the row identifier since there's no lab_venue
            vk   = getattr(l.lab_allocation.program_course, "course_code", "Lab") or "Lab"
            dy   = l.day.lower()
            code = vk
            if vk not in lab_slot_day_venue[sl][dy]:
                lab_slot_day_venue[sl][dy][vk] = []
            lab_slot_day_venue[sl][dy][vk].append({"course_code": code})

        lab_time_slots = sorted(lab_slot_day_venue.keys())
        lab_day_grids  = OrderedDict()
        for day in DAYS_ORDER:
            lab_venues = set()
            for sl in lab_time_slots:
                lab_venues.update(lab_slot_day_venue.get(sl, {}).get(day.lower(), {}).keys())
            if not lab_venues:
                continue
            rows = []
            for vk in sorted(lab_venues):
                cells = []
                for sl in lab_time_slots:
                    entries = lab_slot_day_venue.get(sl, {}).get(day.lower(), {}).get(vk, [])
                    cells.append({"entries": entries})
                rows.append({"venue_key": vk, "cells": cells})
            lab_day_grids[day] = rows
    except Exception:
        lab_day_grids  = OrderedDict()
        lab_time_slots = []

    return day_grids, lab_day_grids, lab_time_slots


def _collect_campus_exam(campus):
    """
    Collect CampusExamTimetable entries for a given campus.
    Campus exam models have NO venue field — entries are grouped by department/course.
    Returns (date_grids, lab_date_grids).
    """
    def slot_lbl(st, en):
        return f"{st.strftime('%I:%M %p').lstrip('0')}→{en.strftime('%I:%M%p').lstrip('0')}"

    exam_qs = (
        CampusExamTimetable.objects
        .filter(campus=campus)
        .select_related("course_allocation", "course_allocation__program")
        .order_by("date", "start_time")
    )

    # date → slot → program_label → [codes]
    date_slot_dept = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    seen = set()
    for e in exam_qs:
        if not (e.date and e.start_time and e.end_time):
            continue
        dept = (
            getattr(getattr(e.course_allocation, "program", None), "name", None)
            or getattr(e.course_allocation, "course_code", "")
            or "General"
        )
        sl   = slot_lbl(e.start_time, e.end_time)
        code = getattr(e.course_allocation, "course_code", "") or ""
        key  = (str(e.date), sl, dept, code)
        if key not in seen:
            seen.add(key)
            date_slot_dept[str(e.date)][sl][dept].append(code)

    date_grids = OrderedDict()
    for date_str in sorted(date_slot_dept.keys()):
        slots  = sorted(date_slot_dept[date_str].keys())
        depts  = sorted({d for sl in slots for d in date_slot_dept[date_str][sl]})
        rows   = []
        for dept in depts:
            cells = [
                {"entries": [{"course_code": c} for c in date_slot_dept[date_str][sl].get(dept, [])]}
                for sl in slots
            ]
            rows.append({"venue_key": dept, "cells": cells})
        date_grids[date_str] = {"rows": rows, "time_slots": slots}

    # Lab exam (also no venue)
    lab_date_grids = OrderedDict()
    try:
        lab_qs = (
            CampusLabExamTimetable.objects
            .filter(campus=campus)
            .select_related("lab_allocation__program_course")
            .order_by("date", "start_time")
        )
        lab_dsv = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        lab_seen = set()
        for l in lab_qs:
            if not (l.date and l.start_time and l.end_time):
                continue
            code  = getattr(l.lab_allocation.program_course, "course_code", "") or "Lab"
            sl    = slot_lbl(l.start_time, l.end_time)
            key   = (str(l.date), sl, code, code)
            if key not in lab_seen:
                lab_seen.add(key)
                lab_dsv[str(l.date)][sl][code].append(code)
        for date_str in sorted(lab_dsv.keys()):
            slots  = sorted(lab_dsv[date_str].keys())
            labels = sorted({v for sl in slots for v in lab_dsv[date_str][sl]})
            rows   = []
            for lbl in labels:
                cells = [
                    {"entries": [{"course_code": c} for c in lab_dsv[date_str][sl].get(lbl, [])]}
                    for sl in slots
                ]
                rows.append({"venue_key": lbl, "cells": cells})
            lab_date_grids[date_str] = {"rows": rows, "time_slots": slots}
    except Exception:
        pass

    return date_grids, lab_date_grids


# ─────────────────────────────────────────────────────────────────────────────
# Timetable-grid element builders (day-based for regular, date-based for exam)
# ─────────────────────────────────────────────────────────────────────────────

def _append_regular_grids(elements, s, day_grids, time_slots, lab_day_grids, lab_time_slots,
                           page_width, section_label="REGULAR TIMETABLE"):
    """
    Append all day-grid tables for a regular timetable section.
    Includes the lab timetable at the end.
    """
    days_list = list(day_grids.items())
    for idx, (day, rows) in enumerate(days_list):
        elems = _build_day_grid_table(day, rows, time_slots, s, page_width)
        elements.extend(elems)
        if idx < len(days_list) - 1:
            elements.append(PageBreak())

    # Lab timetable
    if lab_day_grids:
        elements.append(PageBreak())
        elements.append(_build_banner("LAB TIMETABLE", page_width, s, bg=COLOR_LAB_BANNER))
        elements.append(Spacer(1, 10))

        lab_days_list = list(lab_day_grids.items())
        for idx, (day, rows) in enumerate(lab_days_list):
            elems = _build_day_grid_table(day, rows, lab_time_slots, s, page_width)
            elements.extend(elems)
            if idx < len(lab_days_list) - 1:
                elements.append(PageBreak())
                elements.append(_build_banner("LAB TIMETABLE (cont.)", page_width, s, bg=COLOR_LAB_BANNER))
                elements.append(Spacer(1, 10))


def _append_exam_grids(elements, s, date_grids, lab_date_grids, page_width, row_header="ROOM"):
    """Append date-based exam grids to elements."""
    dates_list = list(date_grids.items())
    for idx, (date_str, grid_data) in enumerate(dates_list):
        rows       = grid_data["rows"]
        time_slots = grid_data["time_slots"]
        try:
            dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
            day_name = dt_obj.strftime("%A")
            date_label = f"{day_name.upper()}  {_ordinal(dt_obj.day)} {dt_obj.strftime('%B %Y').upper()}"
        except Exception:
            date_label = date_str.upper()

        elems = _build_day_grid_table(date_label, rows, time_slots, s, page_width, row_header=row_header)
        elements.extend(elems)
        if idx < len(dates_list) - 1:
            elements.append(PageBreak())

    if lab_date_grids:
        elements.append(PageBreak())
        elements.append(_build_banner("LAB EXAM TIMETABLE", page_width, s, bg=COLOR_LAB_BANNER))
        elements.append(Spacer(1, 10))

        lab_dates = list(lab_date_grids.items())
        for idx, (date_str, grid_data) in enumerate(lab_dates):
            rows       = grid_data["rows"]
            time_slots = grid_data["time_slots"]
            try:
                dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
                date_label = f"LAB EXAM — {dt_obj.strftime('%A').upper()} {_ordinal(dt_obj.day)} {dt_obj.strftime('%B %Y').upper()}"
            except Exception:
                date_label = f"LAB EXAM — {date_str.upper()}"
            elems = _build_day_grid_table(date_label, rows, time_slots, s, page_width, row_header=row_header)
            elements.extend(elems)
            if idx < len(lab_dates) - 1:
                elements.append(PageBreak())
                elements.append(_build_banner("LAB EXAM TIMETABLE (cont.)", page_width, s, bg=COLOR_LAB_BANNER))
                elements.append(Spacer(1, 10))


# ─────────────────────────────────────────────────────────────────────────────
# Campus day-grid helper (campus grids have per-day time_slots, not global ones)
# ─────────────────────────────────────────────────────────────────────────────

def _append_campus_regular_grids(elements, s, day_grids, lab_day_grids, lab_time_slots, page_width):
    """Append campus regular timetable grids (per-day time_slots stored in grid_data)."""
    days_list = list(day_grids.items())
    for idx, (day, grid_data) in enumerate(days_list):
        rows       = grid_data["rows"]
        time_slots = grid_data["time_slots"]
        elems = _build_day_grid_table(day, rows, time_slots, s, page_width, row_header="PROGRAMME")
        elements.extend(elems)
        if idx < len(days_list) - 1:
            elements.append(PageBreak())

    if lab_day_grids:
        elements.append(PageBreak())
        elements.append(_build_banner("LAB TIMETABLE", page_width, s, bg=COLOR_LAB_BANNER))
        elements.append(Spacer(1, 10))

        lab_days_list = list(lab_day_grids.items())
        for idx, (day, rows) in enumerate(lab_days_list):
            elems = _build_day_grid_table(day, rows, lab_time_slots, s, page_width, row_header="COURSE")
            elements.extend(elems)
            if idx < len(lab_days_list) - 1:
                elements.append(PageBreak())
                elements.append(_build_banner("LAB TIMETABLE (cont.)", page_width, s, bg=COLOR_LAB_BANNER))
                elements.append(Spacer(1, 10))


# ─────────────────────────────────────────────────────────────────────────────
# PDF builder utilities
# ─────────────────────────────────────────────────────────────────────────────

def _make_doc(buffer, pagesize=A4):
    return SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        rightMargin=36, leftMargin=36,
        topMargin=72,   bottomMargin=72,
    )


def _page_width(pagesize=A4):
    return pagesize[0] - 36 - 36   # total width minus left/right margins


def _page_number_cb(canvas_obj, doc_obj):
    """onPage callback that draws 'Page X' at bottom centre."""
    canvas_obj.saveState()
    canvas_obj.setFont("Times-Roman", 8)
    canvas_obj.drawCentredString(
        A4[0] / 2.0, 20,
        f"Page {canvas_obj.getPageNumber()}"
    )
    canvas_obj.restoreState()


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC VIEW: Global Regular Timetable PDF
# ─────────────────────────────────────────────────────────────────────────────

def export_global_regular_pdf(request):
    """
    Generate and return a combined PDF containing:
      1. Main campus regular (class) timetable + lab timetable
      2. Each active satellite campus regular timetable + lab timetable

    URL: /export/global/regular-timetable/
    """
    try:
        template_config = TimetablePdfTemplate.get_template()
        current_date    = timezone.now()
        ref_number      = template_config.get_reference_number(
            current_date.strftime("%d-%b-%Y").upper()
        )
        date_str = (
            f"{_ordinal(current_date.day)} {current_date.strftime('%B, %Y')}"
        )
        semester     = _define_semester()
        current_year = current_date.year
        report_title = f"Regular Timetable ({semester} {current_year})"

        buffer   = io.BytesIO()
        doc      = _make_doc(buffer)
        pw       = _page_width()
        styles   = getSampleStyleSheet()
        s        = _build_styles(styles)
        elements = []

        # ── Letterhead ───────────────────────────────────────────────────────
        _build_letterhead(elements, s, template_config, ref_number, date_str, pw)
        elements.append(Paragraph(report_title, s["report_title"]))
        elements.append(Spacer(1, 6))

        # ── SECTION 1: Main campus ───────────────────────────────────────────
        main_banner = _build_banner("MAIN CAMPUS — REGULAR TIMETABLE", pw, s, bg=COLOR_CAMPUS_BAN)
        elements.append(main_banner)
        elements.append(Spacer(1, 10))

        time_slots = _get_scheduler_time_slots()
        day_grids, lab_day_grids, lab_time_slots = _collect_main_regular(time_slots)

        if day_grids:
            _append_regular_grids(
                elements, s, day_grids, time_slots,
                lab_day_grids, lab_time_slots, pw
            )
        else:
            elements.append(Paragraph("No main campus timetable data found.", s["nb"]))

        # ── SECTION 2: Satellite campuses ────────────────────────────────────
        campuses = Campus.objects.filter(is_active=True, is_default=False).order_by("name")
        for campus in campuses:
            elements.append(PageBreak())
            camp_banner = _build_banner(
                f"{campus.name.upper()} CAMPUS — REGULAR TIMETABLE", pw, s, bg=COLOR_CAMPUS_BAN
            )
            elements.append(camp_banner)
            elements.append(Spacer(1, 10))

            c_day_grids, c_lab_grids, c_lab_slots = _collect_campus_regular(campus, time_slots)

            if c_day_grids:
                _append_campus_regular_grids(
                    elements, s, c_day_grids, c_lab_grids, c_lab_slots, pw
                )
            else:
                elements.append(Paragraph(
                    f"No timetable data found for {campus.name} campus.", s["nb"]
                ))

        # ── Evening / Weekend pages ──────────────────────────────────────────
        config = SchedulerConfig.objects.first()
        if config:
            _append_overflow_pages(elements, s, config)

        # ── Signature + NB ───────────────────────────────────────────────────
        _build_signature(elements, s, template_config)
        _build_nb_section(elements, s, template_config)

        # Build PDF
        doc.build(elements, onFirstPage=_page_number_cb, onLaterPages=_page_number_cb)

        pdf = buffer.getvalue()
        buffer.close()

        filename = (
            f"global_regular_timetable_{current_year}_"
            f"{semester.split()[0]}_{current_date.strftime('%Y%m%d_%H%M%S')}.pdf"
        )
        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write(pdf)
        return response

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return HttpResponse(
            f"Error generating global regular PDF: {exc}",
            status=500,
            content_type="text/plain",
        )


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC VIEW: Global Exam Timetable PDF
# ─────────────────────────────────────────────────────────────────────────────

def export_global_exam_pdf(request):
    """
    Generate and return a combined PDF containing:
      1. Main campus exam timetable + lab exam timetable
      2. Each active satellite campus exam timetable + lab exam timetable

    URL: /export/global/exam-timetable/
    """
    try:
        template_config = TimetablePdfTemplate.get_template()
        current_date    = timezone.now()
        ref_number      = template_config.get_reference_number(
            current_date.strftime("%d-%b-%Y").upper()
        )
        date_str     = f"{_ordinal(current_date.day)} {current_date.strftime('%B, %Y')}"
        semester     = _define_semester()
        current_year = current_date.year
        report_title = f"Examination Timetable ({semester} {current_year})"

        buffer   = io.BytesIO()
        doc      = _make_doc(buffer, pagesize=A4)
        pw       = _page_width()
        styles   = getSampleStyleSheet()
        s        = _build_styles(styles)
        elements = []

        # ── Letterhead ───────────────────────────────────────────────────────
        _build_letterhead(elements, s, template_config, ref_number, date_str, pw)
        elements.append(Paragraph(report_title, s["report_title"]))
        elements.append(Spacer(1, 6))

        # ── SECTION 1: Main campus exams ─────────────────────────────────────
        main_banner = _build_banner("MAIN CAMPUS — EXAMINATION TIMETABLE", pw, s, bg=COLOR_CAMPUS_BAN)
        elements.append(main_banner)
        elements.append(Spacer(1, 10))

        date_grids, lab_date_grids = _collect_main_exam()

        if date_grids or lab_date_grids:
            _append_exam_grids(elements, s, date_grids, lab_date_grids, pw)
        else:
            elements.append(Paragraph("No main campus exam timetable data found.", s["nb"]))

        # ── SECTION 2: Satellite campus exams ────────────────────────────────
        campuses = Campus.objects.filter(is_active=True, is_default=False).order_by("name")
        for campus in campuses:
            elements.append(PageBreak())
            camp_banner = _build_banner(
                f"{campus.name.upper()} CAMPUS — EXAMINATION TIMETABLE", pw, s, bg=COLOR_CAMPUS_BAN
            )
            elements.append(camp_banner)
            elements.append(Spacer(1, 10))

            c_date_grids, c_lab_date_grids = _collect_campus_exam(campus)

            if c_date_grids or c_lab_date_grids:
                _append_exam_grids(elements, s, c_date_grids, c_lab_date_grids, pw,
                                   row_header="PROGRAMME")
            else:
                elements.append(Paragraph(
                    f"No exam timetable data found for {campus.name} campus.", s["nb"]
                ))

        # ── Evening / Weekend pages ──────────────────────────────────────────
        config = SchedulerConfig.objects.first()
        if config:
            _append_overflow_pages(elements, s, config)

        # ── Signature + NB ───────────────────────────────────────────────────
        _build_signature(elements, s, template_config)
        _build_nb_section(elements, s, template_config)

        # Build PDF
        doc.build(elements, onFirstPage=_page_number_cb, onLaterPages=_page_number_cb)

        pdf = buffer.getvalue()
        buffer.close()

        filename = (
            f"global_exam_timetable_{current_year}_"
            f"{semester.split()[0]}_{current_date.strftime('%Y%m%d_%H%M%S')}.pdf"
        )
        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write(pdf)
        return response

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return HttpResponse(
            f"Error generating global exam PDF: {exc}",
            status=500,
            content_type="text/plain",
        )