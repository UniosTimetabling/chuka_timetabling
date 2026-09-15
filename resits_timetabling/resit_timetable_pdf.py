"""
resits_timetabling/resit_timetable_pdf.py

Three public views — now styled to match the official Chuka University
letterhead and timetable-grid design used by publish_timetables_pdfs.py.

  1. publish_resit_timetable_pdf  POST /resits/pdf/publish/
     Timetablers / Sudo only.
     Generates a polished A4/landscape PDF using TimetablePdfTemplate
     (logo, motto, directorate header, ref/date row, NB/KEY block,
     signature) and date-grouped tables identical to the exam/regular
     publisher.  Persists a ResitPublishedPDF record and returns the PDF
     inline so the browser opens it immediately.

  2. download_latest_resit_pdf    GET /resits/pdf/download/
     Any authenticated user.  Serves the most-recently published PDF as
     an attachment (browser save-as dialog).

  3. list_published_pdfs          GET /resits/pdf/list/
     Timetablers / Sudo only.  Returns JSON array of the 30 most-recent
     ResitPublishedPDF records.

Dependencies
------------
  pip install reportlab
"""

from __future__ import annotations

import io
import re
from collections import defaultdict, OrderedDict
from datetime import datetime

from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from core.signed_download import validate_download_token
from django.core.files.base import ContentFile
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.group_required import group_required

# ── ReportLab ────────────────────────────────────────────────────────────────
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── App models ────────────────────────────────────────────────────────────────
from .models import ResitTimetable, ResitSchedulerConfig, ResitPublishedPDF

# ── Shared PDF template model (same one used by the regular/exam publishers) ─
from export_import.models import TimetablePdfTemplate


# ══════════════════════════════════════════════════════════════════════════════
#  Small utilities  (mirrors publish_timetables_pdfs.py)
# ══════════════════════════════════════════════════════════════════════════════

def _ordinal(n: int) -> str:
    s = ["th", "st", "nd", "rd"] + ["th"] * 16
    return str(n) + (s[n % 100] if n % 100 <= 20 else s[n % 10])


def _time_slot_str(start, end) -> str:
    return (
        f"{start.strftime('%I:%M %p').lstrip('0')}"
        f"→{end.strftime('%I:%M%p').lstrip('0')}"
    )


def _time_slot_sort_key(ts_str: str):
    start_part = ts_str.split("→")[0].strip()
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            return datetime.strptime(start_part, fmt).time()
        except ValueError:
            pass
    return start_part


_CODE_SPACE_RE = re.compile(r"^([A-Za-z]+)\s*(\d.*)$")


def _normalize_course_code(code: str) -> str:
    """
    Ensure a single space between the letter prefix and the numeric part
    of a course code, e.g. 'COSC101' and 'COSC 101' both become 'COSC 101'.
    Codes with no letter/digit split (or already correctly spaced) pass
    through the regex unchanged aside from whitespace collapsing.
    """
    if not code:
        return code
    code = code.strip()
    m = _CODE_SPACE_RE.match(code)
    if not m:
        return code
    prefix, rest = m.groups()
    return f"{prefix} {rest}"


# ══════════════════════════════════════════════════════════════════════════════
#  Letterhead — identical to publish_timetables_pdfs._build_letterhead_header
# ══════════════════════════════════════════════════════════════════════════════

def _build_letterhead_header(elements, styles, template_config, ref_number, ref_date_str):
    no_pad = TableStyle([
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ])

    uni_style = ParagraphStyle("UniName", parent=styles["Normal"],
                    fontSize=16, fontName="Times-Bold", alignment=1, spaceAfter=2)
    motto_sty = ParagraphStyle("Motto",   parent=styles["Normal"],
                    fontSize=9,  fontName="Times-Roman",      alignment=1, spaceAfter=2)
    dir_style = ParagraphStyle("Dir",     parent=styles["Normal"],
                    fontSize=10, fontName="Times-Bold", alignment=1, spaceAfter=2)
    con_l     = ParagraphStyle("ConL",    parent=styles["Normal"],
                    fontSize=8,  fontName="Times-Roman",      alignment=0, spaceAfter=1)
    con_r     = ParagraphStyle("ConR",    parent=con_l, alignment=2)
    ref_l     = ParagraphStyle("RefL",    parent=styles["Normal"],
                    fontSize=9,  fontName="Times-Bold", alignment=0,
                    spaceBefore=4, spaceAfter=4)
    ref_r     = ParagraphStyle("RefR",    parent=ref_l, alignment=2)

    # Logo
    try:
        if template_config.university_logo and hasattr(template_config.university_logo, "path"):
            logo = Image(template_config.university_logo.path, width=1.1 * inch, height=1.1 * inch)
            logo.hAlign = "CENTER"
            elements.append(logo)
    except Exception as exc:
        print(f"Resit PDF — logo error: {exc}")

    # University name, motto, directorate
    elements.append(Paragraph(template_config.university_name.upper(), uni_style))
    elements.append(Paragraph(
        f"Knowledge is Wealth <i>({template_config.motto_latin})</i> {template_config.motto_swahili}",
        motto_sty,
    ))
    elements.append(Paragraph(template_config.directorate_name, dir_style))
    elements.append(Spacer(1, 4))

    # Two-column contact rows
    for left_text, right_text in [
        (f"Telephones: {template_config.telephone}", template_config.address),
        (
            f"Direct Line:&nbsp;&nbsp;&nbsp;&nbsp;Email: "
            f"<font color='blue'>{template_config.email}</font>",
            f"Website: {template_config.website}",
        ),
    ]:
        row = Table(
            [[Paragraph(left_text, con_l), Paragraph(right_text, con_r)]],
            colWidths=None, hAlign="LEFT",
        )
        row.setStyle(no_pad)
        elements.append(row)

    elements.append(Spacer(1, 3))
    elements.append(HRFlowable(width="100%", thickness=0.8, color=colors.black))
    elements.append(Spacer(1, 2))

    # Ref / date row
    ref_row = Table([[
        Paragraph(f"<b>Ref: {ref_number}</b>", ref_l),
        Paragraph(f"<b>Date: {ref_date_str}</b>", ref_r),
    ]], colWidths=None, hAlign="LEFT")
    ref_row.setStyle(no_pad)
    elements.append(ref_row)
    elements.append(Spacer(1, 6))


# ══════════════════════════════════════════════════════════════════════════════
#  Date-section table — mirrors _build_day_table in publish_timetables_pdfs.py
# ══════════════════════════════════════════════════════════════════════════════

def _build_date_table(elements, styles, date_label, time_slots, matrix_data,
                      pagesize, left_margin, right_margin):
    """
    Render one exam-date block.

    matrix_data: list of rows, each row = [venue_name, cell_text, cell_text …]
    cell_text: '\n'-joined course codes → rendered as CODE1, CODE2
    """
    page_width = pagesize[0] - left_margin - right_margin

    # Black banner with date label
    banner = Table([[Paragraph(
        f"<b>{date_label}</b>",
        ParagraphStyle("RBanner", parent=styles["Normal"], fontSize=11,
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

    hdr_sty  = ParagraphStyle("RTH",   parent=styles["Normal"],
                   fontSize=9, fontName="Times-Bold", alignment=1,
                   wordWrap="CJK", leading=11)
    cell_sty = ParagraphStyle("RTC",   parent=styles["Normal"],
                   fontSize=8, fontName="Times-Roman",      alignment=0,
                   wordWrap="CJK", leading=10)
    room_sty = ParagraphStyle("RTRoom", parent=cell_sty, fontName="Times-Bold",
                   alignment=1)

    headers = [Paragraph("ROOM", hdr_sty)] + [Paragraph(ts, hdr_sty) for ts in time_slots]
    rows = [headers]
    for row_data in matrix_data:
        row = [Paragraph(row_data[0], room_sty)]
        for cell in row_data[1:]:
            row.append(Paragraph(cell.replace("\n", ", "), cell_sty))
        rows.append(row)

    room_w  = min(max(page_width * 0.14, 55), 80)
    slot_w  = max((page_width - room_w) / max(len(time_slots), 1), 70)
    col_w   = [room_w] + [slot_w] * len(time_slots)

    tbl = Table(rows, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1,  0), colors.white),
        ("FONTNAME",      (0, 0), (-1,  0), "Times-Bold"),
        ("FONTSIZE",      (0, 0), (-1,  0), 9),
        ("ALIGN",         (0, 0), (-1,  0), "CENTER"),
        ("ALIGN",         (0, 1), (0,  -1), "CENTER"),
        ("ALIGN",         (1, 1), (-1, -1), "LEFT"),
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


# ══════════════════════════════════════════════════════════════════════════════
#  NB / KEY / Signature — identical helpers to publish_timetables_pdfs.py
# ══════════════════════════════════════════════════════════════════════════════

def _append_nb_and_key(elements, styles, template_config):
    nb_sty  = ParagraphStyle("NB",    parent=styles["Normal"],
        fontSize=8, fontName="Times-Roman", alignment=0,
        spaceBefore=2, spaceAfter=2, leftIndent=12)
    lbl_sty = ParagraphStyle("NBLbl", parent=nb_sty,
        leftIndent=0, fontName="Times-Bold")

    elements.append(Spacer(1, 8))
    elements.append(Paragraph("<b>NB:</b>", lbl_sty))
    roman = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]
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


def _append_signature(elements, styles, template_config):
    sig_sty = ParagraphStyle("Sig", parent=styles["Normal"],
        fontSize=9, fontName="Times-Bold", alignment=0, spaceAfter=2)
    elements.append(Spacer(1, 18))
    elements.append(Paragraph("<b>Prepared by:</b>", sig_sty))
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(template_config.director_label, sig_sty))


# ══════════════════════════════════════════════════════════════════════════════
#  Core PDF builder
# ══════════════════════════════════════════════════════════════════════════════

def _build_pdf_bytes(entries: list, config, template_config) -> bytes:
    """
    Build the complete resit timetable PDF and return raw bytes.

    Layout
    ------
    • Official Chuka University letterhead (logo, name, motto, directorate,
      contact rows, horizontal rule, ref/date row)
    • Red centred report title
    • One date-section per exam date, sorted chronologically.
      Each section: black banner + venue × time-slot grid.
    • NB / KEY block
    • Signature block
    • Page numbers in footer via onPage callback
    """
    # ── Academic year / semester from ResitSchedulerConfig ───────────────────
    academic_year = config.academic_year if config else "—"
    semester_label = config.semester    if config else "—"

    now = timezone.now()
    ref_num  = template_config.get_reference_number(now.strftime("%d.%m.%Y"))
    ref_date = f"{_ordinal(now.day)} {now.strftime('%B, %Y')}"

    # ── Build cell map: (date_str, time_slot, venue) → set of course codes ───
    cell_map: dict[tuple, set] = defaultdict(set)
    dedup: set = set()

    for e in entries:
        if not (e.date and e.start_time and e.end_time):
            continue
        ts = _time_slot_str(e.start_time, e.end_time)

        alloc = e.resit_course_allocation
        code  = _normalize_course_code(alloc.course_code) if alloc else "—"

        if e.venue:
            venue_name = e.venue.code or e.venue.name or "—"
        else:
            venue_name = "Unassigned"

        key = (str(e.date), e.start_time, e.end_time, venue_name, code)
        if key not in dedup:
            dedup.add(key)
            cell_map[(str(e.date), ts, venue_name)].add(code)

    # ── Organise into date-keyed tables ──────────────────────────────────────
    dates_sorted = sorted({d for (d, ts, v) in cell_map})

    # Collect ALL time slots across every day so that every day's table
    # always shows the full set of columns — even when a slot has no class
    # on a particular day.
    all_time_slots = sorted(
        {ts for (d, ts, v) in cell_map},
        key=_time_slot_sort_key,
    )

    date_tables: OrderedDict = OrderedDict()
    for date_str in dates_sorted:
        try:
            dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
            label  = dt_obj.strftime("%A %d/%m/%Y").upper()   # e.g. MONDAY 12/05/2025
        except ValueError:
            label = date_str.upper()

        day_cells    = {(ts, v): codes for (d, ts, v), codes in cell_map.items()
                        if d == date_str}
        # Use the global slot list so empty slots still render as columns
        time_slots    = all_time_slots
        venues_sorted = sorted({v for (ts, v) in day_cells})

        matrix = []
        for venue in venues_sorted:
            row = [venue]
            for ts in time_slots:
                codes = sorted(day_cells.get((ts, venue), set()))
                row.append("\n".join(codes))
            matrix.append(row)

        date_tables[date_str] = {
            "date_label": label,
            "time_slots": time_slots,
            "venues":     venues_sorted,
            "matrix":     matrix,
        }

    # ── Choose portrait vs landscape based on slot count ─────────────────────
    # Use the global slot count — all days now share the same column set
    max_slots = len(all_time_slots)
    if max_slots > 5:
        pagesize, lm, rm, tm, bm = landscape(A4), 20, 20, 35, 35
    else:
        pagesize, lm, rm, tm, bm = A4, 30, 30, 40, 40

    # ── Document ──────────────────────────────────────────────────────────────
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=pagesize,
        leftMargin=lm, rightMargin=rm,
        topMargin=tm, bottomMargin=bm,
        title="Resit Examination Timetable",
        author=template_config.directorate_name,
    )

    # ── Page-number footer ────────────────────────────────────────────────────
    def _on_page(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Times-Roman", 8)
        canvas.drawCentredString(
            pagesize[0] / 2,
            15,
            f"Page {canvas.getPageNumber()}",
        )
        canvas.restoreState()

    elements = []
    styles   = getSampleStyleSheet()

    # ── Letterhead ────────────────────────────────────────────────────────────
    _build_letterhead_header(elements, styles, template_config, ref_num, ref_date)

    # ── Report title (red, centred — same style as regular/exam publishers) ───
    report_title = f"Resit Examination Timetable ({academic_year} — {semester_label})"
    title_sty = ParagraphStyle(
        "RTitle", parent=styles["Normal"],
        fontSize=13, fontName="Times-Bold", alignment=1,
        textColor=colors.black, spaceBefore=4, spaceAfter=10,
    )
    elements.append(Paragraph(report_title, title_sty))

    cont_sty = ParagraphStyle(
        "Cont", parent=styles["Normal"],
        fontSize=10, fontName="Times-Bold",
        alignment=1, textColor=colors.black, spaceAfter=8,
    )

    # ── Empty-state fallback ──────────────────────────────────────────────────
    if not date_tables:
        elements.append(Spacer(1, 40))
        elements.append(Paragraph(
            "No published resit timetable entries found.",
            ParagraphStyle("Empty", parent=styles["Normal"], fontSize=14,
                           fontName="Times-Bold", alignment=1,
                           textColor=colors.HexColor("#b71c1c")),
        ))
    else:
        # ── Date-section tables ───────────────────────────────────────────────
        date_list = list(date_tables.items())
        for idx, (date_str, dd) in enumerate(date_list):
            _build_date_table(
                elements, styles,
                dd["date_label"], dd["time_slots"], dd["matrix"],
                pagesize, lm, rm,
            )
            if idx < len(date_list) - 1:
                elements.append(Paragraph(
                    "<b>Resit Examination Timetable (cont.)</b>", cont_sty,
                ))
                elements.append(PageBreak())

    # ── NB / KEY / Signature ──────────────────────────────────────────────────
    _append_nb_and_key(elements, styles, template_config)
    _append_signature(elements, styles, template_config)

    doc.build(elements, onFirstPage=_on_page, onLaterPages=_on_page)
    return buffer.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
#  VIEW 1 — POST /resits/pdf/publish/
# ══════════════════════════════════════════════════════════════════════════════

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def publish_resit_timetable_pdf(request):
    """
    Generate a PDF from current ResitTimetable entries, persist it as a
    ResitPublishedPDF record, and return it inline (browser opens in new tab).

    Response headers include:
      Content-Disposition: inline; filename="..."
      Content-PDF-Record-Id: <pk of the new ResitPublishedPDF>
      X-PDF-Version: <version number>
    """
    try:
        entries = list(
            ResitTimetable.objects.select_related(
                "resit_course_allocation",
                "resit_course_allocation__department",
                "venue",
            ).order_by("date", "start_time")
        )

        config          = ResitSchedulerConfig.objects.order_by("-id").first()
        template_config = TimetablePdfTemplate.get_template()

        pdf_bytes = _build_pdf_bytes(entries, config, template_config)

        # ── Version bookkeeping ───────────────────────────────────────────────
        academic_year = config.academic_year if config else ""
        semester      = config.semester      if config else ""

        last = (
            ResitPublishedPDF.objects
            .filter(academic_year=academic_year, semester=semester)
            .order_by("-version")
            .first()
        )
        version = (last.version + 1) if last else 1

        ts_str    = timezone.now().strftime("%Y%m%d_%H%M%S")
        safe_year = academic_year.replace("/", "-")
        filename  = f"resit_timetable_{safe_year}_{semester}_v{version}_{ts_str}.pdf"

        pdf_record = ResitPublishedPDF(
            academic_year = academic_year,
            semester      = semester,
            version       = version,
            total_entries = len(entries),
            published_by  = request.user,
        )
        pdf_record.pdf_file.save(filename, ContentFile(pdf_bytes), save=True)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"]   = f'inline; filename="{filename}"'
        response["Content-PDF-Record-Id"] = str(pdf_record.pk)
        response["X-PDF-Version"]         = str(version)
        return response

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


# ══════════════════════════════════════════════════════════════════════════════
#  VIEW 2 — GET /resits/pdf/download/
# ══════════════════════════════════════════════════════════════════════════════

@require_GET
def download_latest_resit_pdf(request):
    """
    Public endpoint — download the latest published Resit timetable PDF.
    No login required, but a valid signed token must be supplied: ?token=<signed>
    Token is issued by the published_timetables view (core.signed_download).
    """
    _, pk = validate_download_token(request.GET.get("token"), "resit")
    if pk is None:
        return HttpResponse(
            "<h2 style='font-family:sans-serif;color:#b71c1c;"
            "margin:40px auto;text-align:center;'>Invalid or expired download link.</h2>",
            content_type="text/html",
            status=403,
        )

    try:
        latest = ResitPublishedPDF.objects.get(pk=pk)
    except ResitPublishedPDF.DoesNotExist:
        return HttpResponse(
            "<h2 style='font-family:sans-serif;color:#b71c1c;"
            "margin:40px auto;text-align:center;'>No published resit timetable PDF found.</h2>"
            "<p style='text-align:center;font-family:sans-serif;'>"
            "Please ask the timetabler to publish one first.</p>",
            content_type="text/html",
            status=404,
        )

    if not latest.pdf_file:
        return HttpResponse(
            "<h2 style='font-family:sans-serif;color:#b71c1c;"
            "margin:40px auto;text-align:center;'>PDF file not found.</h2>",
            content_type="text/html",
            status=404,
        )

    try:
        pdf_bytes   = latest.pdf_file.read()
        safe_year   = latest.academic_year.replace("/", "-")
        dl_filename = (
            f"Resit_Timetable_{safe_year}_{latest.semester}_v{latest.version}.pdf"
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{dl_filename}"'
        return response
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


# ══════════════════════════════════════════════════════════════════════════════
#  VIEW 3 — GET /resits/pdf/list/
# ══════════════════════════════════════════════════════════════════════════════

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def list_published_pdfs(request):
    """
    Return JSON array of the 30 most-recent ResitPublishedPDF records.
    Useful for a future "PDF history" panel in the UI.
    """
    try:
        records = (
            ResitPublishedPDF.objects
            .select_related("published_by")
            .order_by("-published_at")[:30]
        )
        data = []
        for r in records:
            publisher = "System"
            if r.published_by:
                publisher = r.published_by.get_full_name() or r.published_by.username
            data.append({
                "id":            r.pk,
                "academic_year": r.academic_year,
                "semester":      r.semester,
                "version":       r.version,
                "total_entries": r.total_entries,
                "file_size_kb":  r.file_size_kb,
                "published_at":  r.published_at.strftime("%d %b %Y, %H:%M") if r.published_at else None,
                "published_by":  publisher,
                "filename":      r.filename,
                "download_url":  "/resits/pdf/download/",
            })
        return JsonResponse({"success": True, "pdfs": data})
    except Exception as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=500)