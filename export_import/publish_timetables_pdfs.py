import re
import io
import os
import base64
from collections import defaultdict, OrderedDict
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.templatetags.static import static
from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.views.decorators.http import require_GET
from core.rbac import allowed_roles, Role
from django.core.files.base import ContentFile
from django.shortcuts import get_object_or_404
from django.db.models import Q
from course_allocation.allocation_scope import resolve_tt_scope, apply_tt_scope
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    Table, TableStyle, PageBreak, Image, HRFlowable,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from timetable.models import (
    ExamTimetable,
    MergedCourseGroup,            # exam manual merges
    MergedCourseGroupTimetable,   # regular timetable auto-merges (renamed from AutoMergedExamGroup)
    AutoMergedExamGroup,          # backward-compat alias for MergedCourseGroupTimetable
    SharedVenueExamGroup,         # exam shared venue groups
    LabExamTimetable,
    Timetable,
    SchedulerConfig,
)
from room_management.models import Venue, LabVenue
from export_import.models import TimetablePdfTemplate, PDFDocument
from course_allocation.models import CombinedCourseGroup

# ─────────────────────────────────────────────────────────────
# Small utilities
# ─────────────────────────────────────────────────────────────
def _ordinal(n):
    s = ["th", "st", "nd", "rd"] + ["th"] * 16
    return str(n) + (s[n % 100] if n % 100 <= 20 else s[n % 10])

def _lecturer_name(lecturer_obj):
    if lecturer_obj is None:
        return "—"
    return getattr(lecturer_obj, "display_name", None) or str(lecturer_obj)

def _resolve_programme(course_code):
    """Look up the Programme name for a given course_code."""
    try:
        from program_management.models import ProgramCourse as PC
        from course_allocation.config_helpers import strip_course_code_tag
        pc = PC.objects.filter(
            course_code__iexact=strip_course_code_tag(course_code)
        ).select_related("program").first()
        if pc:
            return pc.program.name
    except Exception:
        pass
    return "—"

def _time_slot_str(start, end):
    return (
        f"{start.strftime('%I:%M %p').lstrip('0')}"
        f"→{end.strftime('%I:%M%p').lstrip('0')}"
    )

def _time_slot_sort_key(ts_str):
    """Return a sortable key from a time-slot string like '7:00 AM→10:00AM'.
    Parses only the start portion so slots are ordered chronologically."""
    from datetime import datetime
    start_part = ts_str.split("→")[0].strip()
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            return datetime.strptime(start_part, fmt).time()
        except ValueError:
            pass
    return start_part  # fallback: original string

# ─────────────────────────────────────────────────────────────
# Release / Debug cell rendering  (exam timetable)
# ─────────────────────────────────────────────────────────────
# RELEASE mode (default): when several splits / combined sections of the
# SAME course sit in one cell (MATH 241 (161), MATH 241-A (20), MATH 241-B (10) …)
# show the course once with the total of the students placed in that cell:
# "MATH 241 (200)". DEBUG mode prints every entry exactly as stored.
_RELEASE_BASE_RE = re.compile(r"^\s*([A-Za-z]+)\s*(\d+)")


def _release_base_code(code):
    """'MATH 241-GEO-D' / 'MATH 241-b' / 'MATH241(EDU)' -> 'MATH 241'.
    Codes that don't look like <LETTERS><DIGITS> are returned unchanged."""
    m = _RELEASE_BASE_RE.match(code or "")
    if not m:
        return (code or "").strip()
    return f"{m.group(1).upper()} {m.group(2)}"


def _format_cell_entries(entries, release_mode=True):
    """entries: iterable of (code, count|None). Returns a sorted list of display strings."""
    def _fmt(code, count):
        return f"{code} ({count})" if count is not None else code

    if not release_mode:
        return sorted(_fmt(c, n) for c, n in entries)

    grouped = OrderedDict()
    for code, count in sorted(entries, key=lambda e: (e[0], e[1] if e[1] is not None else -1)):
        grouped.setdefault(_release_base_code(code), []).append((code, count))

    out = []
    for base, items in grouped.items():
        if len(items) == 1:                      # nothing to collapse
            out.append(_fmt(*items[0]))
            continue
        counts = [n for _, n in items if n is not None]
        out.append(_fmt(base, sum(counts) if counts else None))
    return sorted(out)


def _apportion(total, weights):
    """Split `total` seats across rooms in proportion to `weights` (room
    capacities; equal shares if any weight is unknown) using largest-remainder
    rounding, so the shares always add up to exactly `total`."""
    n = len(weights)
    if n == 0:
        return []
    ws = [w if (w and w > 0) else 0 for w in weights]
    if not all(ws):
        ws = [1] * n
    wsum = float(sum(ws))
    raw = [total * w / wsum for w in ws]
    shares = [int(r) for r in raw]
    leftover = total - sum(shares)
    for i in sorted(range(n), key=lambda i: raw[i] - shares[i], reverse=True)[:leftover]:
        shares[i] += 1
    return shares


def _get_all_venues():
    """Fetch all venues and return a sorted list of unique venue display names."""
    all_venue_objs = Venue.objects.all()
    return list(set([
        (getattr(v, "code", None) or getattr(v, "name", None) or "Unassigned")
        for v in all_venue_objs
    ]))

def _get_lab_venues():
    """Fetch lab venues and return a sorted list of unique venue display names.
    Lab venues live in their own model (room_management.LabVenue), separate
    from the regular room_management.Venue model used by normal timetables —
    LabTimetable/LabExamTimetable.lab_venue is a FK straight to LabVenue."""
    return sorted(set(
        (getattr(v, "code", None) or "Unassigned")
        for v in LabVenue.objects.all()
    ))

# ─────────────────────────────────────────────────────────────
# Semester / academic year helpers
# ─────────────────────────────────────────────────────────────
def define_semester():
    from course_allocation.models import CourseAllocation
    from program_management.models import ProgramCourse
    from course_allocation.config_helpers import strip_course_code_tag
    s1 = s2 = 0
    for alloc in CourseAllocation.objects.select_related("program").all():
        course = ProgramCourse.objects.filter(
            program=alloc.program,
            course_code__iexact=strip_course_code_tag(alloc.course_code),
        ).first()
        if not course:
            continue
        if course.semester == 1:
            s1 += 1
        elif course.semester == 2:
            s2 += 1
        if abs(s1 - s2) > 10:
            break
    if s1 > s2:
        return "SEPTEMBER - DECEMBER"
    if s2 > s1:
        return "JANUARY - APRIL"
    return "UNDETERMINED"

def get_academic_year_and_semester(allocation_set=None):
    if allocation_set is not None:
        d = timezone.now().date()
        academic_year = allocation_set.academic_year or (
            f"{d.year}/{d.year + 1}" if d.month >= 9 else f"{d.year - 1}/{d.year}"
        )
        nums = allocation_set.semester_numbers
        semester = "+".join(str(n) for n in nums) if nums else "?"
        return academic_year, semester
    
    d = timezone.now().date()
    academic_year = (
        f"{d.year}/{d.year + 1}" if d.month >= 9 else f"{d.year - 1}/{d.year}"
    )
    sem_str = define_semester()
    semester = "1" if ("SEPTEMBER" in sem_str or "DECEMBER" in sem_str) else "2"
    return academic_year, semester

# ─────────────────────────────────────────────────────────────
# Letterhead
# ─────────────────────────────────────────────────────────────
def _build_letterhead_header(elements, styles, template_config, ref_number, ref_date_str):
    no_pad = TableStyle([
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ])
    uni_style  = ParagraphStyle("UniName", parent=styles["Normal"],
                     fontSize=16, fontName="Times-Bold", alignment=1, spaceAfter=2)
    motto_sty  = ParagraphStyle("Motto",   parent=styles["Normal"],
                     fontSize=9,  fontName="Times-Roman",      alignment=1, spaceAfter=2)
    dir_style  = ParagraphStyle("Dir",     parent=styles["Normal"],
                     fontSize=10, fontName="Times-Bold", alignment=1, spaceAfter=2)
    con_l      = ParagraphStyle("ConL",    parent=styles["Normal"],
                     fontSize=8,  fontName="Times-Roman",      alignment=0, spaceAfter=1)
    con_r      = ParagraphStyle("ConR",    parent=con_l, alignment=2)
    ref_l      = ParagraphStyle("RefL",    parent=styles["Normal"],
                     fontSize=9,  fontName="Times-Bold", alignment=0,
                     spaceBefore=4, spaceAfter=4)
    ref_r      = ParagraphStyle("RefR",    parent=ref_l, alignment=2)
    
    try:
        if template_config.university_logo and hasattr(template_config.university_logo, "path"):
            logo = Image(template_config.university_logo.path, width=1.1*inch, height=1.1*inch)
            logo.hAlign = "CENTER"
            elements.append(logo)
    except Exception as exc:
        print(f"Logo error: {exc}")
        
    elements.append(Paragraph(template_config.university_name.upper(), uni_style))
    elements.append(Paragraph(
        f"Knowledge is Wealth <i>({template_config.motto_latin})</i> {template_config.motto_swahili}",
        motto_sty,
    ))
    elements.append(Paragraph(template_config.directorate_name, dir_style))
    elements.append(Spacer(1, 4))
    
    for left_text, right_text in [
        (f"Telephones: {template_config.telephone}", template_config.address),
        (f"Direct Line:&nbsp;&nbsp;&nbsp;&nbsp;Email: <font color='blue'>{template_config.email}</font>",
         f"Website: {template_config.website}"),
    ]:
        row = Table([[Paragraph(left_text, con_l), Paragraph(right_text, con_r)]],
                    colWidths=None, hAlign="LEFT")
        row.setStyle(no_pad)
        elements.append(row)
        
    elements.append(Spacer(1, 3))
    elements.append(HRFlowable(width="100%", thickness=0.8, color=colors.black))
    elements.append(Spacer(1, 2))
    
    ref_row = Table([[
        Paragraph(f"<b>Ref: {ref_number}</b>", ref_l),
        Paragraph(f"<b>Date: {ref_date_str}</b>", ref_r),
    ]], colWidths=None, hAlign="LEFT")
    ref_row.setStyle(no_pad)
    elements.append(ref_row)
    elements.append(Spacer(1, 6))

# ─────────────────────────────────────────────────────────────
# Timetable grid
# ─────────────────────────────────────────────────────────────
def _build_day_table(elements, styles, date_label, time_slots, venues,
                     matrix_data, pagesize, left_margin, right_margin):
    page_width = pagesize[0] - left_margin - right_margin
    
    banner = Table([[Paragraph(
        f"<b>{date_label}</b>",
        ParagraphStyle("Banner", parent=styles["Normal"], fontSize=11,
                       fontName="Times-Bold", alignment=1, textColor=colors.white),
    )]], colWidths=[page_width])
    banner.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), colors.black),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(banner)
    
    hdr_sty  = ParagraphStyle("TH",  parent=styles["Normal"],
                   fontSize=9, fontName="Times-Bold", alignment=1, wordWrap="CJK", leading=11)
    cell_sty = ParagraphStyle("TC",  parent=styles["Normal"],
                   fontSize=8, fontName="Times-Roman",      alignment=1, wordWrap="CJK", leading=10)
    room_sty = ParagraphStyle("TRoom", parent=cell_sty,  fontName="Times-Bold")
    
    headers = [Paragraph("ROOM", hdr_sty)] + [Paragraph(ts, hdr_sty) for ts in time_slots]
    rows = [headers]
    for row_data in matrix_data:
        row = [Paragraph(row_data[0], room_sty)]
        for cell in row_data[1:]:
            row.append(Paragraph(cell.replace("\n", ", "), cell_sty))
        rows.append(row)
        
    room_w = min(max(page_width * 0.14, 55), 80)
    slot_w = max((page_width - room_w) / max(len(time_slots), 1), 70)
    col_widths = [room_w] + [slot_w] * len(time_slots)
    
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1,  0), colors.white),
        ("FONTNAME",      (0, 0), (-1,  0), "Times-Bold"),
        ("FONTSIZE",      (0, 0), (-1,  0), 9),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1,  0), 6),
        ("BOTTOMPADDING", (0, 0), (-1,  0), 6),
        ("BACKGROUND",    (0, 1), (-1, -1), colors.white),
        ("FONTNAME",      (0, 1), ( 0, -1), "Times-Bold"),
        ("FONTNAME",      (1, 1), (-1, -1), "Times-Roman"),
        ("FONTSIZE",      (0, 1), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("TOPPADDING",    (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("GRID",          (0, 0), (-1, -1), 0.5, colors.black),
        ("BOX",           (0, 0), (-1, -1), 0.8, colors.black),
        ("LINEBELOW",     (0, 0), (-1,  0), 1.0, colors.black),
        ("WORDWRAP",      (0, 0), (-1, -1), True),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 10))

# ─────────────────────────────────────────────────────────────
# Footer tables
# ─────────────────────────────────────────────────────────────
def _footer_style():
    return TableStyle([
        ("BACKGROUND",     (0, 0), (-1,  0), colors.HexColor("#1a1a1a")),
        ("TEXTCOLOR",      (0, 0), (-1,  0), colors.white),
        ("FONTNAME",       (0, 0), (-1,  0), "Times-Bold"),
        ("FONTSIZE",       (0, 0), (-1,  0), 8),
        ("ALIGN",          (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",     (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 4),
        ("LEFTPADDING",    (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 4),
        ("BACKGROUND",     (0, 1), (-1, -1), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
        ("TEXTCOLOR",      (0, 1), (-1, -1), colors.black),
        ("FONTNAME",       (0, 1), (-1, -1), "Times-Roman"),
        ("FONTSIZE",       (0, 1), (-1, -1), 8),
        ("GRID",           (0, 0), (-1, -1), 0.5, colors.black),
        ("BOX",            (0, 0), (-1, -1), 0.8, colors.black),
    ])

def _build_footer_tables(elements, styles, merged_rows, shared_rows, section_label):
    if not merged_rows and not shared_rows:
        return
        
    elements.append(Spacer(1, 14))
    elements.append(HRFlowable(width="100%", thickness=1.0, color=colors.black))
    elements.append(Spacer(1, 6))
    
    title_sty = ParagraphStyle("FTitle", parent=styles["Normal"],
        fontSize=10, fontName="Times-Bold", alignment=0,
        textColor=colors.black, spaceAfter=4)
    sub_sty = ParagraphStyle("FSub", parent=styles["Normal"],
        fontSize=9, fontName="Times-Bold", alignment=0,
        textColor=colors.HexColor("#333333"), spaceBefore=8, spaceAfter=4)
    hdr_sty = ParagraphStyle("FHdr", parent=styles["Normal"],
        fontSize=8, fontName="Times-Bold", alignment=1, textColor=colors.white)
    cel_sty = ParagraphStyle("FCel", parent=styles["Normal"],
        fontSize=8, fontName="Times-Roman", alignment=1,
        textColor=colors.black, leading=10)
        
    elements.append(Paragraph(section_label, title_sty))
    
    if merged_rows:
        elements.append(Paragraph("Merged / Auto-Merged Courses", sub_sty))
        data = [[
            Paragraph("BASE COURSE",   hdr_sty),
            Paragraph("MERGED COURSE", hdr_sty),
            Paragraph("LECTURER",      hdr_sty),
            Paragraph("PROGRAMME",     hdr_sty),
        ]]
        for r in merged_rows:
            data.append([
                Paragraph(r.get("base_code",   "—"), cel_sty),
                Paragraph(r.get("merged_code", "—"), cel_sty),
                Paragraph(r.get("lecturer",    "—"), cel_sty),
                Paragraph(r.get("programme",   "—"), cel_sty),
            ])
        tbl = Table(data, colWidths=[110, 110, 160, 170], repeatRows=1)
        tbl.setStyle(_footer_style())
        elements.append(tbl)
        
    if shared_rows:
        elements.append(Paragraph("Shared Venue Courses", sub_sty))
        data = [[
            Paragraph("COURSES (SLASHED)", hdr_sty),
            Paragraph("VENUE",             hdr_sty),
            Paragraph("DATE",              hdr_sty),
            Paragraph("TIME SLOT",         hdr_sty),
        ]]
        for r in shared_rows:
            codes_str = " / ".join(sorted(r.get("codes", [])))
            data.append([
                Paragraph(codes_str,                cel_sty),
                Paragraph(r.get("venue",     "—"),  cel_sty),
                Paragraph(r.get("date",      "—"),  cel_sty),
                Paragraph(r.get("time_slot", "—"),  cel_sty),
            ])
        tbl = Table(data, colWidths=[200, 80, 100, 130], repeatRows=1)
        tbl.setStyle(_footer_style())
        elements.append(tbl)
        
    elements.append(Spacer(1, 8))

# ─────────────────────────────────────────────────────────────
# NB / KEY / Signature shared helpers
# ─────────────────────────────────────────────────────────────
def _append_nb_and_key(elements, styles, template_config):
    nb_sty  = ParagraphStyle("NB",    parent=styles["Normal"],
        fontSize=8, fontName="Times-Roman", alignment=0,
        spaceBefore=2, spaceAfter=2, leftIndent=12)
    lbl_sty = ParagraphStyle("NBLbl", parent=nb_sty,
        leftIndent=0, fontName="Times-Bold")
        
    elements.append(Spacer(1, 8))
    elements.append(Paragraph("<b>NB:</b>", lbl_sty))
    roman = ["i","ii","iii","iv","v","vi","vii","viii","ix","x"]
    for idx, line in enumerate(
        [l.strip() for l in template_config.notes.strip().split("\n") if l.strip()], 1
    ):
        line = re.sub(r"^(i{1,3}v?|vi{0,3}|ix|x{0,3})\.\s*", "", line, flags=re.IGNORECASE)
        prefix = roman[idx - 1] if idx <= len(roman) else str(idx)
        elements.append(Paragraph(f"{prefix}.&nbsp;&nbsp;{line}", nb_sty))
        
    key_text = template_config.key_section.strip()
    if key_text:
        elements.append(Spacer(1, 8))
        key_body = re.sub(r"^KEY\s*:\s*", "", key_text, flags=re.IGNORECASE).strip()
        elements.append(Paragraph("<b>KEY:</b>", lbl_sty))
        for line in [l.strip() for l in key_body.split("\n") if l.strip()]:
            elements.append(Paragraph(line, nb_sty))

def _append_overflow_pages_pub(elements, styles, config):
    from reportlab.platypus import PageBreak, Spacer, Table, TableStyle, Paragraph
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from collections import defaultdict, OrderedDict
    from datetime import datetime, timedelta
    
    pw = A4[0] - 36 - 36
    
    def _gen_slots(start_t, end_t, hrs):
        slots, cur = [], datetime.combine(datetime.today(), start_t)
        end_dt = datetime.combine(datetime.today(), end_t)
        while cur < end_dt:
            nxt = min(cur + timedelta(hours=hrs), end_dt)
            slots.append(f"{cur.strftime('%H:%M')} - {nxt.strftime('%H:%M')}")
            cur = nxt
        return slots
        
    def _banner_tbl(text, bg_hex):
        ban_s = ParagraphStyle('OvPubBan', fontSize=12, fontName='Times-Bold',
                               alignment=1, textColor=colors.white)
        tbl = Table([[Paragraph(f"<b>{text}</b>", ban_s)]], colWidths=[pw])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0),(-1,-1), colors.HexColor(bg_hex)),
            ("LEFTPADDING",(0,0),(-1,-1), 8), ("RIGHTPADDING",(0,0),(-1,-1), 8),
            ("TOPPADDING", (0,0),(-1,-1), 7), ("BOTTOMPADDING",(0,0),(-1,-1), 7),
        ]))
        return tbl
        
    cell_s = ParagraphStyle('OvCell', fontSize=8, fontName='Times-Roman',
                            alignment=1, leading=10, textColor=colors.black)
    hdr_s  = ParagraphStyle('OvHdr',  fontSize=9, fontName='Times-Bold',
                            alignment=1, textColor=colors.black)
    day_s  = ParagraphStyle('OvDay',  fontSize=10, fontName='Times-Bold',
                            alignment=0, textColor=colors.black, spaceBefore=8)
                            
    def _grid_style(hdr_bg):
        return TableStyle([
            ('BACKGROUND',    (0,0),(-1, 0), hdr_bg),
            ('FONTNAME',      (0,0),(-1, 0), 'Times-Bold'),
            ('GRID',          (0,0),(-1,-1), 0.5, colors.black),
            ('BOX',           (0,0),(-1,-1), 0.8, colors.black),
            ('ALIGN',         (0,0),(-1,-1), 'CENTER'),
            ('VALIGN',        (0,0),(-1,-1), 'TOP'),
            ('LEFTPADDING',   (0,0),(-1,-1), 3), ('RIGHTPADDING',(0,0),(-1,-1), 3),
            ('TOPPADDING',    (0,0),(-1,-1), 4), ('BOTTOMPADDING',(0,0),(-1,-1), 4),
        ])

    def _build_and_render(days, slot_labels, filter_fn, hdr_bg):
        by_slot_day_venue = defaultdict(lambda: defaultdict(dict))
        for e in Timetable.objects.select_related("course_allocation","venue").all():
            if not filter_fn(e):
                continue
            if not e.start_time or not e.end_time or not e.venue:
                continue
            sl = f"{e.start_time.strftime('%H:%M')} - {e.end_time.strftime('%H:%M')}"
            vk = e.venue.code
            dy = (e.day or '').lower()
            by_slot_day_venue[sl][dy].setdefault(vk, []).append(
                getattr(e.course_allocation, 'course_code', '') if e.course_allocation else '')
                
        day_grids = OrderedDict()
        for day in days:
            venues = set()
            for sl in slot_labels:
                venues.update(by_slot_day_venue.get(sl,{}).get(day.lower(),{}).keys())
            if not venues:
                continue
                
            rows = []
            for vk in sorted(venues):
                cells = []
                for sl in slot_labels:
                    codes = by_slot_day_venue.get(sl,{}).get(day.lower(),{}).get(vk,[])
                    cells.append('\n'.join(codes))
                rows.append((vk, cells))
            day_grids[day] = rows
            
        days_list = list(day_grids.items())
        for idx, (day, rows) in enumerate(days_list):
            elements.append(Paragraph(day.upper(), day_s))
            elements.append(Spacer(1, 4))
            tbl_data = [[Paragraph(h, hdr_s) for h in ['Venue'] + slot_labels]]
            for vk, cells in rows:
                vrow = [Paragraph(vk, cell_s)]
                vrow += [Paragraph(c, cell_s) for c in cells]
                tbl_data.append(vrow)
            cw = [80] + [100] * len(slot_labels)
            tbl = Table(tbl_data, colWidths=cw, repeatRows=1)
            tbl.setStyle(_grid_style(hdr_bg))
            elements.append(tbl)
            elements.append(Spacer(1, 24))
            if idx < len(days_list) - 1:
                elements.append(PageBreak())
                
    # Evening
    if getattr(config, 'enable_evening_classes', False):
        ev_slots = _gen_slots(config.evening_start_time, config.evening_end_time, config.slot_size)
        ev_slots = ev_slots[:config.evening_slot_count]
        def _is_ev(e):
            t = e.start_time.hour * 100 + e.start_time.minute if e.start_time else 0
            return t >= 1900 and (e.day or '') not in ('Saturday','Sunday')
        ev_days = ['Monday','Tuesday','Wednesday','Thursday','Friday']
        from timetable.models import Timetable as _TT
        has_ev = _TT.objects.filter(start_time__hour__gte=19).exclude(day__in=['Saturday','Sunday']).exists()
        if has_ev:
            elements.append(PageBreak())
            elements.append(_banner_tbl("EVENING CLASSES TIMETABLE", "#1565c0"))
            elements.append(Spacer(1, 10))
            _build_and_render(ev_days, ev_slots, _is_ev, colors.HexColor("#d0e8ff"))
            
    # Weekend
    if getattr(config, 'enable_weekend_classes', False):
        wk_slots = _gen_slots(config.weekend_start_time, config.weekend_end_time, config.weekend_slot_size)
        def _is_wk(e):
            return (e.day or '').capitalize() in ('Saturday', 'Sunday')
        wk_days = ['Saturday', 'Sunday']
        has_wk = _TT.objects.filter(day__in=['Saturday','Sunday']).exists()
        if has_wk:
            elements.append(PageBreak())
            elements.append(_banner_tbl("WEEKEND CLASSES TIMETABLE", "#6a1b9a"))
            elements.append(Spacer(1, 10))
            _build_and_render(wk_days, wk_slots, _is_wk, colors.HexColor("#ede1f5"))

def _append_signature(elements, styles, template_config):
    sig_sty = ParagraphStyle("Sig", parent=styles["Normal"],
        fontSize=9, fontName="Times-Bold", alignment=0, spaceAfter=2)
    elements.append(Spacer(1, 18))
    elements.append(Paragraph("<b>Prepared by:</b>", sig_sty))
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(template_config.director_label, sig_sty))

def _make_doc(buffer, pagesize, left, right, top, bottom, title):
    return SimpleDocTemplate(
        buffer, pagesize=pagesize,
        leftMargin=left, rightMargin=right,
        topMargin=top, bottomMargin=bottom,
        title=title,
    )

# ─────────────────────────────────────────────────────────────
# EXAM TIMETABLE PDF
# ─────────────────────────────────────────────────────────────
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def publish_exam_timetable_pdf(request):
    try:
        # ?mode=release (default) collapses splits of one course in a cell into
        # a single "CODE (total)"; ?mode=debug prints every split as stored.
        release_mode = (request.GET.get("mode") or request.POST.get("mode") or "release").strip().lower() != "debug"
        template_config = TimetablePdfTemplate.get_template()
        now = timezone.now()
        user = request.user
        
        allocation_set = None
        allocation_set_id = request.GET.get("allocation_set_id") or request.POST.get("allocation_set_id")
        if allocation_set_id:
            from course_allocation.models import AllocationSet as _AllocationSet
            allocation_set = _AllocationSet.objects.filter(id=allocation_set_id).first()
            
        if allocation_set is not None:
            tt_scope = {"type": "sets", "ids": [allocation_set.id]}
        else:
            tt_scope = resolve_tt_scope(request)
            
        academic_year, semester = get_academic_year_and_semester(allocation_set)
        ref_num = template_config.get_reference_number(now.strftime("%d.%m.%Y"))
        ref_date = f"{_ordinal(now.day)} {now.strftime('%B, %Y')}"
        
        exams = apply_tt_scope(
            ExamTimetable.objects.select_related("course_allocation__lecturer", "venue"),
            scope=tt_scope, prefix="course_allocation__allocation_set",
        ).order_by("date", "start_time", "venue__code")
        
        lab_exams = apply_tt_scope(
            LabExamTimetable.objects.select_related(
                "lab_allocation__program_course",
                "lab_allocation__lecturer",
                "lab_venue",
            ),
            scope=tt_scope, prefix="lab_allocation__allocation_set",
        ).order_by("date", "start_time", "lab_venue__code")
        
        if not exams.exists() and not lab_exams.exists():
            return HttpResponse("No exam timetable data found.", content_type="text/plain")
            
        cell_map = defaultdict(set)
        dedup = set()
        venue_capacity = {}

        # ── Seats per ROOM, not per course ───────────────────────────────
        # A course split over several rooms in one slot has one ExamTimetable
        # row per room. Rows written by the auto-scheduler carry that room's
        # own share in allocated_students, but legacy rows (NULL) and rows
        # added by hand / by the family-resolve tool carry the WHOLE course
        # enrollment on every room -- so each room appeared to hold the full
        # class (looks double-booked, and totals were inflated). For such
        # split slots we re-apportion the enrollment across the rooms by
        # capacity so the shares add up to the real total.
        _slot_rows = defaultdict(list)
        for _e in exams:
            if _e.date and _e.start_time and _e.end_time:
                _slot_rows[(_e.course_allocation_id, _e.date, _e.start_time, _e.end_time)].append(_e)

        _count_lookup = {}    # (alloc_id, date, start, end, venue_name) -> seats in that room
        _slot_placed  = {}    # (alloc_id, date, start, end)            -> seats placed over all rooms
        for _key, _rows in _slot_rows.items():
            _total = _rows[0].course_allocation.number_of_students or 0
            _recorded = [r.allocated_students for r in _rows]
            if len(_rows) > 1 and _total and (
                None in _recorded or sum(_recorded) > _total
            ):
                _rows = sorted(_rows, key=lambda r: getattr(r.venue, "code", "") or "")
                _caps = [
                    (getattr(r.venue, "exam_capacity", None) or getattr(r.venue, "capacity", None))
                    for r in _rows
                ]
                _shares = _apportion(_total, _caps)
            else:
                _shares = [
                    r.allocated_students if r.allocated_students is not None else (_total or None)
                    for r in _rows
                ]
            for r, sh in zip(_rows, _shares):
                _vn = getattr(r.venue, "code", None) or getattr(r.venue, "name", None) or "Unassigned"
                _count_lookup[_key + (_vn,)] = sh
            _slot_placed[_key] = sum(sh or 0 for sh in _shares)

        def _seat_count(ca, date, start, end, vname):
            """Seats of allocation `ca` sitting in room `vname` for this slot."""
            k = (ca.pk, date, start, end)
            if k + (vname,) in _count_lookup:
                return _count_lookup[k + (vname,)]
            total = ca.number_of_students
            if k in _slot_placed:
                # already seated in other room(s) this slot -> only what's left
                left = max((total or 0) - _slot_placed[k], 0)
                return left or None
            return total
        
        def _add_cell(date, time_slot, venue_name, code, start, end, count=None):
            key = (date, start, end, venue_name, code)
            if key not in dedup:
                dedup.add(key)
                cell_map[(date, time_slot, venue_name)].add((code, count))
                
        all_merged_groups = (
            MergedCourseGroup.objects
            .filter(published=True)
            .select_related("base_course", "exam_timetable_entry", "venue")
            .prefetch_related("merged_courses")
        )
        
        from collections import defaultdict as _dd
        _groups_by_entry  = _dd(list)
        _groups_by_alloc  = _dd(list)
        for mg in all_merged_groups:
            if mg.exam_timetable_entry_id:
                _groups_by_entry[mg.exam_timetable_entry_id].append(mg)
            if mg.base_course_id:
                _groups_by_alloc[mg.base_course_id].append(mg)
            for mc in mg.merged_courses.all():
                if mc.pk != mg.base_course_id:
                    _groups_by_alloc[mc.pk].append(mg)
                    
        def _resolve_cell_coords(mg, anchor_exam):
            v = mg.venue
            vn = (getattr(v, "code", None) or getattr(v, "name", None)) if v else None
            d  = mg.date       or anchor_exam.date
            st = mg.start_time or anchor_exam.start_time
            en = mg.end_time   or anchor_exam.end_time
            vn = vn or (getattr(anchor_exam.venue, "code", None)
                        or getattr(anchor_exam.venue, "name", None)
                        or "Unassigned")
            return d, _time_slot_str(st, en), vn, st, en
            
        for e in exams:
            if not e.date or not e.start_time or not e.end_time:
                continue
            vname = getattr(e.venue, "code", None) or getattr(e.venue, "name", None) or "Unassigned"
            ts    = _time_slot_str(e.start_time, e.end_time)
            base_code = e.course_allocation.course_code
            if e.venue is not None:
                venue_capacity[vname] = e.venue.exam_capacity
                
            count = _seat_count(e.course_allocation, e.date, e.start_time, e.end_time, vname)
            _add_cell(e.date, ts, vname, base_code, e.start_time, e.end_time, count=count)
            
            seen_mg_pks = set()
            candidate_groups = (
                _groups_by_entry.get(e.pk, []) +
                _groups_by_alloc.get(e.course_allocation_id, [])
            )
            for mg in candidate_groups:
                if mg.pk in seen_mg_pks:
                    continue
                seen_mg_pks.add(mg.pk)
                d, slot_ts, slot_vname, slot_st, slot_en = _resolve_cell_coords(mg, e)
                if mg.venue is not None:
                    venue_capacity[slot_vname] = mg.venue.exam_capacity
                    
                for mc in mg.merged_courses.all():
                    if mc.pk == e.course_allocation.pk:
                        continue
                    mc_count = _seat_count(mc, d, slot_st, slot_en, slot_vname)
                    _add_cell(d, slot_ts, slot_vname, mc.course_code, slot_st, slot_en, count=mc_count)
                    
        for svg in apply_tt_scope(
                SharedVenueExamGroup.objects.filter(published=True),
                scope=tt_scope, prefix="course_allocations__allocation_set",
        ).distinct().select_related("venue", "exam_timetable_entry").prefetch_related("course_allocations__lecturer"):
            vname = getattr(svg.venue, "code", None) or getattr(svg.venue, "name", None) or "Unassigned"
            ts    = _time_slot_str(svg.start_time, svg.end_time)
            if svg.venue is not None:
                venue_capacity[vname] = svg.venue.exam_capacity
            for ca in svg.course_allocations.all():
                ca_count = _seat_count(ca, svg.date, svg.start_time, svg.end_time, vname)
                _add_cell(svg.date, ts, vname, ca.course_code, svg.start_time, svg.end_time, count=ca_count)
                
        lab_cell_map = defaultdict(set)
        lab_dedup    = set()
        lab_venue_capacity = {}
        
        def _add_lab_cell(date, time_slot, venue_name, code, start, end, count=None):
            key = (date, start, end, venue_name, code)
            if key not in lab_dedup:
                lab_dedup.add(key)
                lab_cell_map[(date, time_slot, venue_name)].add((code, count))
                
        # Lab rows have no per-room share column: when one lab allocation
        # occupies several lab venues in a slot, split its headcount across
        # them (by capacity) instead of printing the full class in each.
        _lab_slot_rows = defaultdict(list)
        for le in lab_exams:
            if le.date and le.start_time and le.end_time:
                _lab_slot_rows[(le.lab_allocation_id, le.date, le.start_time, le.end_time)].append(le)
        _lab_share = {}
        for _rows in _lab_slot_rows.values():
            _total = _rows[0].lab_allocation.number_of_students
            if len(_rows) > 1 and _total:
                _rows = sorted(_rows, key=lambda r: getattr(r.lab_venue, "code", "") or "")
                _shares = _apportion(_total, [getattr(r.lab_venue, "capacity", None) for r in _rows])
            else:
                _shares = [_total] * len(_rows)
            for r, sh in zip(_rows, _shares):
                _lab_share[r.pk] = sh

        for le in lab_exams:
            if not le.date or not le.start_time or not le.end_time:
                continue
            vname = getattr(le.lab_venue, "code", None) or getattr(le.lab_venue, "name", None) or "Unassigned"
            ts    = _time_slot_str(le.start_time, le.end_time)
            code  = le.lab_allocation.program_course.course_code
            if le.lab_venue is not None:
                lab_venue_capacity[vname] = le.lab_venue.capacity
            _add_lab_cell(
                le.date, ts, vname, code, le.start_time, le.end_time,
                count=_lab_share.get(le.pk, le.lab_allocation.number_of_students),
            )
            
        # Fetch all venues and lab venues separately
        all_venue_names = _get_all_venues()
        all_lab_venue_names = _get_lab_venues()

        date_to_day = {e.date: e.day for e in exams if e.date}
        for le in lab_exams:
            if le.date and le.date not in date_to_day:
                date_to_day[le.date] = le.day
                
        def _build_date_tables(cm, day_lookup, capacity_map=None, is_lab=False):
            by_date = defaultdict(lambda: {"time_slots": set(), "venues": set(), "cells": {}})
            for (date, ts, venue), codes in cm.items():
                by_date[date]["time_slots"].add(ts)
                by_date[date]["venues"].add(venue)
                by_date[date]["cells"][(ts, venue)] = codes
                
            # Use appropriate venue list based on whether this is for lab exams
            venue_list = all_lab_venue_names if is_lab else all_venue_names
            for date in by_date:
                for v in venue_list:
                    by_date[date]["venues"].add(v)

            result = OrderedDict()
            for date in sorted(by_date.keys()):
                info       = by_date[date]
                time_slots = sorted(info["time_slots"], key=_time_slot_sort_key)
                venues     = sorted(info["venues"])
                matrix     = []
                for venue in venues:
                    cap = capacity_map.get(venue) if capacity_map else None
                    room_label = f"{venue} ({cap})" if cap is not None else venue
                    row = [room_label]
                    for ts in time_slots:
                        codes = _format_cell_entries(info["cells"].get((ts, venue), set()), release_mode)
                        row.append("\n".join(codes))
                    matrix.append(row)
                day_name = day_lookup.get(date, date.strftime("%A"))
                result[date] = {
                    "date_label": f"{day_name.upper()} {date.strftime('%d/%m/%Y')}",
                    "time_slots": time_slots,
                    "venues":     venues,
                    "matrix":     matrix,
                }
            return result
            
        # Build separate date tables - main exams use all venues, lab exams use only lab venues
        date_tables     = _build_date_tables(cell_map,     date_to_day, venue_capacity, is_lab=False)
        lab_date_tables = _build_date_tables(lab_cell_map, date_to_day, lab_venue_capacity, is_lab=True)
        
        if not date_tables and not lab_date_tables:
            return HttpResponse("No exam data to display.", content_type="text/plain")
            
        semester_str = define_semester()
        report_title = f"{now.strftime('%B, %Y')} Examinations Timetable"
        buffer       = io.BytesIO()
        all_tables   = list(date_tables.values()) + list(lab_date_tables.values())
        max_slots    = max((len(d["time_slots"]) for d in all_tables), default=0) if all_tables else 0
        
        if max_slots > 4:
            pagesize, lm, rm, tm, bm = landscape(A4), 25, 25, 35, 35
        else:
            pagesize, lm, rm, tm, bm = A4, 30, 30, 40, 40
            
        doc      = _make_doc(buffer, pagesize, lm, rm, tm, bm, report_title)
        elements = []
        styles   = getSampleStyleSheet()
        _build_letterhead_header(elements, styles, template_config, ref_num, ref_date)
        
        title_sty = ParagraphStyle("RTitle", parent=styles["Normal"],
            fontSize=13, fontName="Times-Bold", alignment=1,
            textColor=colors.black, spaceBefore=4, spaceAfter=10)
        elements.append(Paragraph(report_title, title_sty))
        
        cont_sty = ParagraphStyle("Cont", parent=styles["Normal"],
            fontSize=10, fontName="Times-Bold",
            alignment=1, textColor=colors.black, spaceAfter=8)
            
        def _section_banner(label, bg=colors.HexColor("#1a1a1a"), fg=colors.white):
            page_width = pagesize[0] - lm - rm
            banner = Table([[Paragraph(
                f"<b>{label}</b>",
                ParagraphStyle("SBan", parent=styles["Normal"],
                               fontSize=12, fontName="Times-Bold",
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
            
        if date_tables:
            for idx, (date, dd) in enumerate(date_tables.items()):
                _build_day_table(
                    elements, styles,
                    dd["date_label"], dd["time_slots"], dd["venues"], dd["matrix"],
                    pagesize, lm, rm,
                )
                if idx < len(date_tables) - 1:
                    elements.append(PageBreak())
                    elements.append(Paragraph("<b>Examination Timetable (cont.)</b>", cont_sty))
                    
        if lab_date_tables:
            elements.append(PageBreak())
            elements.append(_section_banner("LAB EXAMINATIONS"))
            elements.append(Spacer(1, 10))
            lab_date_list = list(lab_date_tables.items())
            for idx, (date, dd) in enumerate(lab_date_list):
                _build_day_table(
                    elements, styles,
                    dd["date_label"], dd["time_slots"], dd["venues"], dd["matrix"],
                    pagesize, lm, rm,
                )
                if idx < len(lab_date_list) - 1:
                    elements.append(PageBreak())
                    elements.append(_section_banner("LAB EXAMINATIONS (cont.)"))
                    elements.append(Spacer(1, 10))
                    
        _append_nb_and_key(elements, styles, template_config)
        _append_signature(elements, styles, template_config)
        
        doc.build(elements)
        pdf      = buffer.getvalue()
        buffer.close()
        
        filename = (
            f"exam_timetable_{academic_year.replace('/', '_')}"
            f"_S{semester}_{now.strftime('%Y%m%d_%H%M%S')}.pdf"
        )
        sem_disp = "First" if semester == "1" else "Second"
        title    = f"{academic_year} {sem_disp} Semester Examination Timetable"
        
        pdf_doc = PDFDocument(
            title=title, document_type="EXAM",
            academic_year=academic_year, semester=semester,
            uploaded_by=user.get_full_name() or user.username,
            description=(
                f"Generated {now.strftime('%Y-%m-%d %H:%M:%S')} "
                f"by {user.username}. Ref: {ref_num}"
            ),
            status="PUBLISHED",
        )
        pdf_doc.pdf_file.save(filename, ContentFile(pdf), save=False)
        pdf_doc.save()
        
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({
                "success": True, "message": "Exam timetable published successfully",
                "mode": "release" if release_mode else "debug",
                "document_id": pdf_doc.id, "document_url": pdf_doc.get_download_url(),
                "filename": filename, "title": title,
            })
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp
        
    except Exception as exc:
        import traceback
        print(f"Exam PDF error: {exc}\n{traceback.format_exc()}")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": str(exc)}, status=500)
        return HttpResponse(f"Error: {exc}", status=500, content_type="text/plain")

# ─────────────────────────────────────────────────────────────
# REGULAR TIMETABLE PDF
# ─────────────────────────────────────────────────────────────
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def publish_regular_timetable_pdf(request):
    try:
        template_config = TimetablePdfTemplate.get_template()
        now = timezone.now()
        user = request.user
        
        allocation_set = None
        allocation_set_id = request.GET.get("allocation_set_id") or request.POST.get("allocation_set_id")
        if allocation_set_id:
            from course_allocation.models import AllocationSet as _AllocationSet
            allocation_set = _AllocationSet.objects.filter(id=allocation_set_id).first()
            
        if allocation_set is not None:
            tt_scope = {"type": "sets", "ids": [allocation_set.id]}
        else:
            tt_scope = resolve_tt_scope(request)
            
        academic_year, semester = get_academic_year_and_semester(allocation_set)
        ref_num  = template_config.get_reference_number(now.strftime("%d.%m.%Y"))
        ref_date = f"{_ordinal(now.day)} {now.strftime('%B, %Y')}"
        
        timetables = apply_tt_scope(
            Timetable.objects.select_related(
                "course_allocation__lecturer",
                "course_allocation__program",
                "venue",
            ),
            scope=tt_scope, prefix="course_allocation__allocation_set",
        ).order_by("day", "start_time", "venue__code")
        
        from timetable.models import LabTimetable as LabTimetableModel
        lab_timetables = apply_tt_scope(
            LabTimetableModel.objects.select_related("lab_allocation__program_course", "lab_venue"),
            scope=tt_scope, prefix="lab_allocation__allocation_set",
        ).order_by("day", "start_time", "lab_venue__code")
        
        if not timetables.exists() and not lab_timetables.exists():
            return HttpResponse("No regular timetable data found.", content_type="text/plain")
            
        combined_groups_by_allocation = {}
        all_combined_groups = CombinedCourseGroup.objects.prefetch_related("allocations")
        for group in all_combined_groups:
            group_display_name = group.display_name()
            for alloc in group.allocations.all():
                if alloc.id not in combined_groups_by_allocation:
                    combined_groups_by_allocation[alloc.id] = group_display_name
                    
        cell_map     = defaultdict(set)
        dedup        = set()
        merged_rows  = []
        merged_dedup = set()
        
        def _add(day, ts, venue, display_name):
            key = (day, ts, venue, display_name)
            if key not in dedup:
                dedup.add(key)
                cell_map[(day, ts, venue)].add(display_name)
                
        for t in timetables:
            if not t.day or not t.start_time or not t.end_time:
                continue
            vname = getattr(t.venue, "code", None) or getattr(t.venue, "name", None) or "Unassigned"
            ts = _time_slot_str(t.start_time, t.end_time)
            base_code = t.course_allocation.course_code
            course_allocation_id = t.course_allocation_id
            
            combined_name = combined_groups_by_allocation.get(course_allocation_id)
            if combined_name:
                _add(t.day, ts, vname, combined_name)
            else:
                _add(t.day, ts, vname, base_code)
                
            for ag in MergedCourseGroupTimetable.objects.filter(
                    base_course=t.course_allocation, published=True
            ).select_related("timetable_entry").prefetch_related("merged_courses__lecturer"):
                for mc in ag.merged_courses.all():
                    if mc.pk == t.course_allocation.pk:
                        continue
                    if not combined_name:
                        _add(t.day, ts, vname, mc.course_code)
                    mkey = (base_code, mc.course_code)
                    if mkey not in merged_dedup:
                        merged_dedup.add(mkey)
                        merged_rows.append({
                            "base_code":   base_code,
                            "merged_code": mc.course_code,
                            "lecturer":    _lecturer_name(mc.lecturer),
                            "programme":   _resolve_programme(mc.course_code),
                        })
                        
        # Fetch all venues and lab venues separately
        all_venue_names = _get_all_venues()
        all_lab_venue_names = _get_lab_venues()

        day_order = ["Monday", "Tuesday", "Wednesday", "Thursday",
                     "Friday", "Saturday", "Sunday"]
        days_in_data = set(day for (day, ts, venue) in cell_map.keys())
        sorted_days  = [d for d in day_order if d in days_in_data]
        day_tables = OrderedDict()
        for day in sorted_days:
            day_cells  = {(ts, venue): codes
                          for (d, ts, venue), codes in cell_map.items() if d == day}
            time_slots = sorted(set(ts for (ts, venue) in day_cells), key=_time_slot_sort_key)
            # Use all venues for regular timetable
            venues     = sorted(set(list(set(venue for (ts, venue) in day_cells)) + all_venue_names))
            matrix = []
            for venue in venues:
                row = [venue]
                for ts in time_slots:
                    codes = sorted(day_cells.get((ts, venue), set()))
                    row.append("\n".join(codes))
                matrix.append(row)
            day_tables[day] = {
                "date_label": day.upper(),
                "time_slots": time_slots,
                "venues":     venues,
                "matrix":     matrix,
            }
            
        lab_cell_map  = defaultdict(set)
        lab_dedup_reg = set()
        
        def _add_lab(day, ts, venue, code):
            key = (day, ts, venue, code)
            if key not in lab_dedup_reg:
                lab_dedup_reg.add(key)
                lab_cell_map[(day, ts, venue)].add(code)
                
        for lt in lab_timetables:
            if not lt.day or not lt.start_time or not lt.end_time:
                continue
            if lt.day.lower() == "saturday":
                continue
            vname = (getattr(lt.lab_venue, "code", None)
                     or getattr(lt.lab_venue, "name", None) or "Unassigned")
            ts    = _time_slot_str(lt.start_time, lt.end_time)
            code  = lt.lab_allocation.program_course.course_code
            _add_lab(lt.day, ts, vname, code)
            
        lab_days_in_data = set(day for (day, ts, venue) in lab_cell_map.keys())
        lab_sorted_days  = [d for d in day_order if d in lab_days_in_data]
        lab_day_tables = OrderedDict()
        for day in lab_sorted_days:
            day_cells  = {(ts, venue): codes
                          for (d, ts, venue), codes in lab_cell_map.items() if d == day}
            time_slots = sorted(set(ts for (ts, venue) in day_cells), key=_time_slot_sort_key)
            # Use only lab venues for lab timetable
            venues     = sorted(set(list(set(venue for (ts, venue) in day_cells)) + all_lab_venue_names))
            matrix = []
            for venue in venues:
                row = [venue]
                for ts in time_slots:
                    codes = sorted(day_cells.get((ts, venue), set()))
                    row.append("\n".join(codes))
                matrix.append(row)
            lab_day_tables[day] = {
                "date_label": day.upper(),
                "time_slots": time_slots,
                "venues":     venues,
                "matrix":     matrix,
            }
            
        semester_str = define_semester()
        report_title = f"Regular Timetable ({semester_str} {now.year})"
        buffer       = io.BytesIO()
        all_tt = list(day_tables.values()) + list(lab_day_tables.values())
        max_slots = max((len(d["time_slots"]) for d in all_tt), default=0) if all_tt else 0
        
        if max_slots > 5:
            pagesize, lm, rm, tm, bm = landscape(A4), 20, 20, 35, 35
        else:
            pagesize, lm, rm, tm, bm = A4, 30, 30, 40, 40
            
        doc      = _make_doc(buffer, pagesize, lm, rm, tm, bm, report_title)
        elements = []
        styles   = getSampleStyleSheet()
        _build_letterhead_header(elements, styles, template_config, ref_num, ref_date)
        
        title_sty = ParagraphStyle("RTitle", parent=styles["Normal"],
            fontSize=13, fontName="Times-Bold", alignment=1,
            textColor=colors.black, spaceBefore=4, spaceAfter=10)
        elements.append(Paragraph(report_title, title_sty))
        
        cont_sty = ParagraphStyle("Cont", parent=styles["Normal"],
            fontSize=10, fontName="Times-Bold",
            alignment=1, textColor=colors.black, spaceAfter=8)
            
        def _reg_section_banner(label, bg=colors.HexColor("#1a1a1a"), fg=colors.white):
            pw = pagesize[0] - lm - rm
            b = Table([[Paragraph(
                f"<b>{label}</b>",
                ParagraphStyle("RegSBan", parent=styles["Normal"], fontSize=12,
                               fontName="Times-Bold", alignment=1, textColor=fg),
            )]], colWidths=[pw])
            b.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, -1), bg),
                ("LEFTPADDING",   (0, 0), (-1, -1), 8),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
                ("TOPPADDING",    (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            return b
            
        all_days_ordered = [d for d in day_order
                             if d in day_tables or d in lab_day_tables]
        for idx, day in enumerate(all_days_ordered):
            dd = day_tables.get(day)
            if dd:
                _build_day_table(
                    elements, styles,
                    dd["date_label"], dd["time_slots"], dd["venues"], dd["matrix"],
                    pagesize, lm, rm,
                )
            lab_dd = lab_day_tables.get(day)
            if lab_dd:
                elements.append(Spacer(1, 10))
                elements.append(_reg_section_banner(f"{day.upper()} — LAB TIMETABLE"))
                elements.append(Spacer(1, 10))
                _build_day_table(
                    elements, styles,
                    lab_dd["date_label"], lab_dd["time_slots"], lab_dd["venues"], lab_dd["matrix"],
                    pagesize, lm, rm,
                )
            if idx < len(all_days_ordered) - 1:
                elements.append(PageBreak())
                elements.append(Paragraph("<b>Regular Timetable (cont.)</b>", cont_sty))
                
        _append_nb_and_key(elements, styles, template_config)
        
        cfg = SchedulerConfig.objects.first()
        if cfg:
            _append_overflow_pages_pub(elements, styles, cfg)
            
        _append_signature(elements, styles, template_config)
        
        doc.build(elements)
        pdf      = buffer.getvalue()
        buffer.close()
        
        filename = (
            f"regular_timetable_{academic_year.replace('/', '_')}"
            f"_S{semester}_{now.strftime('%Y%m%d_%H%M%S')}.pdf"
        )
        sem_disp = "First" if semester == "1" else "Second"
        title    = f"{academic_year} {sem_disp} Semester Regular Timetable"
        
        pdf_doc = PDFDocument(
            title=title, document_type="REGULAR",
            academic_year=academic_year, semester=semester,
            uploaded_by=user.get_full_name() or user.username,
            description=(
                f"Generated {now.strftime('%Y-%m-%d %H:%M:%S')} "
                f"by {user.username}. Ref: {ref_num}"
            ),
            status="PUBLISHED",
        )
        pdf_doc.pdf_file.save(filename, ContentFile(pdf), save=False)
        pdf_doc.save()
        
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({
                "success": True, "message": "Regular timetable published successfully",
                "document_id": pdf_doc.id, "document_url": pdf_doc.get_download_url(),
                "filename": filename, "title": title,
            })
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp
        
    except Exception as exc:
        import traceback
        print(f"Regular PDF error: {exc}\n{traceback.format_exc()}")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": str(exc)}, status=500)
        return HttpResponse(f"Error: {exc}", status=500, content_type="text/plain")

# ─────────────────────────────────────────────────────────────
# Download / List helpers
# ─────────────────────────────────────────────────────────────
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def download_pdf_document(request, document_id):
    document = get_object_or_404(PDFDocument, id=document_id)
    if not document.pdf_file:
        return HttpResponse("PDF file not found.", status=404, content_type="text/plain")
    try:
        resp = HttpResponse(document.pdf_file.read(), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{document.filename}"'
        return resp
    except Exception as exc:
        return HttpResponse(f"Error: {exc}", status=500, content_type="text/plain")

@login_required
@require_GET
def list_pdf_documents(request):
    qs = PDFDocument.objects.all()
    if dt := request.GET.get("type"):
        qs = qs.filter(document_type=dt)
    if ay := request.GET.get("academic_year"):
        qs = qs.filter(academic_year=ay)
    if sm := request.GET.get("semester"):
        qs = qs.filter(semester=sm)
    qs = qs.order_by("-uploaded_at")
    return JsonResponse({
        "count": qs.count(),
        "documents": [{
            "id":            doc.id,
            "title":         doc.title,
            "document_type": doc.get_document_type_display(),
            "academic_year": doc.academic_year,
            "semester":      doc.get_semester_display(),
            "version":       doc.version,
            "status":        doc.get_status_display(),
            "uploaded_by":   doc.uploaded_by,
            "uploaded_at":   doc.uploaded_at.strftime("%Y-%m-%d %H:%M:%S"),
            "file_size":     doc.file_size,
            "filename":      doc.filename,
            "download_url":  doc.get_download_url(),
            "is_latest":     doc.is_latest,
        } for doc in qs],
    })