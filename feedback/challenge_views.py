"""
feedback/challenge_views.py
════════════════════════════
"System Challenge Log" — lets the Timetabling Office record, for every
problem raised (via a Feedback submission or logged directly), whether it
was actually caused by the system, what caused it, how it was resolved,
what was put in place so it doesn't recur, and who was involved. This is
the evidence trail used to defend the system when it is questioned.

Views:
  GET  /system-challenges/                 — dashboard: list + add/edit
  POST /system-challenges/save/            — create or update (pk in POST = update)
  POST /system-challenges/<pk>/delete/     — delete a record
  GET  /system-challenges/export/excel/    — official .xlsx export (university header/logo)
  GET  /system-challenges/export/pdf/      — official .pdf export (same layout, for filing)
  POST /system-challenges/reimport/        — re-import a previously exported .xlsx/.pdf,
                                              upserting by reference_code
"""
import io
import logging
import re
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU
from openpyxl.worksheet.page import PageMargins

from export_import.models import TimetablePdfTemplate
from .models import Feedback, SystemChallenge

logger = logging.getLogger(__name__)

CLASSIFICATION_BY_LABEL = {label.lower(): code for code, label in SystemChallenge.CAUSE_CHOICES}
STATUS_BY_LABEL = {label.lower(): code for code, label in SystemChallenge.STATUS_CHOICES}


# ─────────────────────────────────────────────────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
@login_required
def challenge_dashboard(request):
    """System Challenge Log — list, filter, and add/edit records inline."""
    challenges = SystemChallenge.objects.select_related("feedback", "created_by")

    query = request.GET.get("q", "").strip()
    classification = request.GET.get("classification", "")
    status = request.GET.get("status", "")

    if query:
        challenges = challenges.filter(
            Q(reference_code__icontains=query)
            | Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(cause__icontains=query)
            | Q(involved_parties__icontains=query)
        )
    if classification:
        challenges = challenges.filter(classification=classification)
    if status:
        challenges = challenges.filter(status=status)

    # Feedback items not yet turned into a challenge record — offered as a
    # quick-start list so staff can log a challenge straight from a complaint.
    unlogged_feedback = (
        Feedback.objects.filter(system_challenges__isnull=True)
        .order_by("-created_at")[:15]
    )

    return render(request, "feedback/challenge_dashboard.html", {
        "challenges": challenges.order_by("-occurred_at", "-created_at"),
        "unlogged_feedback": unlogged_feedback,
        "classification_choices": SystemChallenge.CAUSE_CHOICES,
        "status_choices": SystemChallenge.STATUS_CHOICES,
        "query": query,
        "selected_classification": classification,
        "selected_status": status,
        "total_count": SystemChallenge.objects.count(),
        "system_count": SystemChallenge.objects.filter(classification=SystemChallenge.CAUSE_SYSTEM).count(),
        "not_system_count": SystemChallenge.objects.filter(classification=SystemChallenge.CAUSE_NOT_SYSTEM).count(),
    })


@login_required
@require_POST
def challenge_save(request):
    """Create a new challenge, or update one in place when 'pk' is posted —
    this is what powers both the 'Add Challenge' and the on-dashboard 'Edit'
    actions."""
    pk = request.POST.get("pk")
    instance = get_object_or_404(SystemChallenge, pk=pk) if pk else SystemChallenge()

    feedback_id = request.POST.get("feedback_id") or None
    if feedback_id:
        instance.feedback_id = feedback_id

    instance.title = request.POST.get("title", "").strip()
    instance.classification = request.POST.get("classification", SystemChallenge.CAUSE_SYSTEM)
    instance.description = request.POST.get("description", "").strip()
    instance.cause = request.POST.get("cause", "").strip()
    instance.resolution = request.POST.get("resolution", "").strip()
    instance.prevention = request.POST.get("prevention", "").strip()
    instance.involved_parties = request.POST.get("involved_parties", "").strip()
    instance.status = request.POST.get("status", SystemChallenge.STATUS_OPEN)

    occurred_at = request.POST.get("occurred_at")
    instance.occurred_at = occurred_at or timezone.localdate()
    resolved_at = request.POST.get("resolved_at")
    instance.resolved_at = resolved_at or None

    if not instance.pk:
        instance.created_by = request.user

    if not instance.title or not instance.cause:
        messages.error(request, "Title and 'What caused it' are required.")
        return redirect("challenge_dashboard")

    instance.save()

    # If this was logged from a feedback item, mark that feedback attended to.
    if instance.feedback_id and instance.feedback.status == "unseen":
        instance.feedback.status = "seen"
        instance.feedback.save(update_fields=["status"])

    messages.success(
        request,
        f"Challenge {instance.reference_code} saved ({instance.get_classification_display_badge()})."
    )
    return redirect("challenge_dashboard")


@login_required
@require_POST
def challenge_delete(request, pk):
    challenge = get_object_or_404(SystemChallenge, pk=pk)
    ref = challenge.reference_code
    challenge.delete()
    messages.success(request, f"Challenge {ref} deleted.")
    return redirect("challenge_dashboard")


# ─────────────────────────────────────────────────────────────────────────────
#  ROW EXTRACTION (shared by both exporters)
# ─────────────────────────────────────────────────────────────────────────────
def _filtered_queryset(request):
    challenges = SystemChallenge.objects.select_related("feedback", "created_by")
    query = request.GET.get("q", "").strip()
    classification = request.GET.get("classification", "")
    status = request.GET.get("status", "")
    if query:
        challenges = challenges.filter(
            Q(reference_code__icontains=query) | Q(title__icontains=query) | Q(description__icontains=query)
        )
    if classification:
        challenges = challenges.filter(classification=classification)
    if status:
        challenges = challenges.filter(status=status)
    return challenges.order_by("-occurred_at", "-created_at")


# ─────────────────────────────────────────────────────────────────────────────
#  EXCEL EXPORT — official university-headed, black & white, single font
# ─────────────────────────────────────────────────────────────────────────────
OFFICIAL_FONT = "Times New Roman"


# Card layout used by the Excel export: instead of one narrow column per
# field (which wasted width on short things like Ref. Code and the four
# date/logged columns), each record is a labelled block — Title sits as a
# full-width header ROW, and every other field is a label/value pair below
# it. Status + both dates + who/when logged share a single "Status & Dates"
# cell, partitioned by line, instead of four separate columns.
CARD_FIELDS = [
    ("description", "Description"),
    ("cause", "What Caused It"),
    ("resolution", "How It Was Resolved"),
    ("prevention", "Future Prevention"),
    ("involved_parties", "Involved Users / Parties"),
]
META_LABEL = "Status & Dates"


def _meta_lines(c):
    return [
        f"Status: {c.get_status_display()}",
        f"Date Occurred: {c.occurred_at.strftime('%Y-%m-%d') if c.occurred_at else '—'}",
        f"Date Resolved: {c.resolved_at.strftime('%Y-%m-%d') if c.resolved_at else '—'}",
        f"Logged By: {(c.created_by.get_full_name() or c.created_by.username) if c.created_by else 'System'}",
        f"Logged At: {timezone.localtime(c.created_at).strftime('%Y-%m-%d %H:%M') if c.created_at else '—'}",
    ]


@login_required
def challenge_export_excel(request):
    template = TimetablePdfTemplate.get_template()
    header_data = template.get_header_data(timetable_type="System Challenge Log")
    challenges = _filtered_queryset(request)

    wb = Workbook()
    ws = wb.active
    ws.title = "System Challenge Log"

    # Just two data columns, starting at A — no wasted spacer column, so the
    # sheet uses the full printable width instead of leaving a blank gutter.
    LABEL_COL, VALUE_COL = 1, 2
    label_letter, value_letter = get_column_letter(LABEL_COL), get_column_letter(VALUE_COL)
    ws.column_dimensions[label_letter].width = 22
    ws.column_dimensions[value_letter].width = 100

    black = "FF000000"
    white = "FFFFFFFF"
    label_fill = "FFD9D9D9"  # darker, more legible grey than before
    label_text = "FF1A1A1A"  # near-black, reads cleanly against the grey fill
    thin = Side(style="thin", color=black)
    box_border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def full_width(row, text, size=11, bold=False, italic=False, align="center", fill=None, font_color=black):
        ws.merge_cells(start_row=row, start_column=LABEL_COL, end_row=row, end_column=VALUE_COL)
        cell = ws.cell(row=row, column=LABEL_COL, value=text)
        cell.font = Font(name=OFFICIAL_FONT, size=size, bold=bold, italic=italic, color=font_color)
        cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=True)
        if fill:
            cell.fill = PatternFill("solid", fgColor=fill)
            ws.cell(row=row, column=VALUE_COL).fill = PatternFill("solid", fgColor=fill)
        return cell

    # ── Logo — its own row at the very top, centered, above every other
    # header line (not squeezed into a side column any more) ─────────────
    row = 1
    logo_row = row
    ws.row_dimensions[logo_row].height = 58
    try:
        if template.university_logo and hasattr(template.university_logo, "path"):
            logo_px = 62
            img = XLImage(template.university_logo.path)
            img.width, img.height = logo_px, logo_px

            # Centre the image across the merged Label+Value width using a
            # precise pixel offset, rather than pinning it to one column.
            label_px = ws.column_dimensions[label_letter].width * 7 + 5
            value_px = ws.column_dimensions[value_letter].width * 7 + 5
            total_px = label_px + value_px
            offset_into_value_col = max(0, (total_px - logo_px) / 2 - label_px)

            marker = AnchorMarker(col=VALUE_COL - 1, colOff=pixels_to_EMU(offset_into_value_col),
                                   row=logo_row - 1, rowOff=0)
            img.anchor = OneCellAnchor(_from=marker, ext=XDRPositiveSize2D(pixels_to_EMU(logo_px), pixels_to_EMU(logo_px)))
            ws.add_image(img)
    except (OSError, ValueError) as e:
        logger.warning("Could not embed university logo in Excel export: %s", e)
    row += 1

    ws.row_dimensions[row].height = 24
    full_width(row, header_data["university_name"], size=16, bold=True)
    row += 1
    ws.row_dimensions[row].height = 18
    full_width(row, header_data["directorate_name"], size=12, bold=True)
    row += 1
    full_width(row, f"{header_data['address']}  |  Tel: {header_data['telephone']}  |  "
                     f"Email: {header_data['email']}  |  {header_data['website']}", size=10)
    row += 1
    full_width(row, f"{header_data['motto_latin']}  |  {header_data['motto_swahili']}", size=10, italic=True)
    row += 1
    full_width(row, "SYSTEM CHALLENGE LOG REPORT", size=13, bold=True)
    row += 1
    filter_bits = [f"Generated: {timezone.now().strftime('%Y-%m-%d %H:%M')}", f"Total records: {challenges.count()}"]
    if request.GET.get("classification"):
        filter_bits.append(f"Filter — Type: {dict(SystemChallenge.CAUSE_CHOICES).get(request.GET['classification'])}")
    if request.GET.get("status"):
        filter_bits.append(f"Filter — Status: {dict(SystemChallenge.STATUS_CHOICES).get(request.GET['status'])}")
    if request.GET.get("q"):
        filter_bits.append(f"Search: '{request.GET['q']}'")
    full_width(row, "   |   ".join(filter_bits), size=10, bold=True, align="left")
    row += 1

    # ── One card per record ─────────────────────────────────────────────────
    def label_value_row(row, label, value):
        lbl = ws.cell(row=row, column=LABEL_COL, value=label)
        lbl.font = Font(name=OFFICIAL_FONT, size=11, bold=True, color=label_text)
        lbl.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        lbl.fill = PatternFill("solid", fgColor=label_fill)
        lbl.border = box_border

        val = ws.cell(row=row, column=VALUE_COL, value=value if value else "—")
        val.font = Font(name=OFFICIAL_FONT, size=11, color=black)
        val.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        val.border = box_border

        # Conservative chars-per-line so wrapped text never gets clipped —
        # Excel's own wrap doesn't line up exactly with the column's stated
        # character width, so we undershoot deliberately and round up.
        chars_per_line = 68
        n_lines = max(1, sum(-(-max(len(line), 1) // chars_per_line) for line in str(value or "—").split("\n")))
        ws.row_dimensions[row].height = min(220, max(20, n_lines * 16))

    for c in challenges:
        # Title as a full-width header ROW (not a column) — Ref. Code and
        # classification ride along on the same line so neither wastes a
        # column of its own.
        header_text = f"{c.reference_code}    |    {c.title}    |    {c.get_classification_display_badge()}"
        header_cell = full_width(row, header_text, size=12, bold=True, align="left",
                                  fill=black, font_color=white)
        header_cell.border = box_border
        ws.cell(row=row, column=VALUE_COL).border = box_border
        ws.row_dimensions[row].height = 24
        row += 1

        for key, label in CARD_FIELDS:
            label_value_row(row, label, getattr(c, key))
            row += 1

        label_value_row(row, META_LABEL, "\n".join(_meta_lines(c)))
        row += 1

        row += 1  # spacer between cards

    # Footer signature block, matching the timetable PDF's official style.
    footer = f"{template.prepared_by_label} Timetabling Office System — {template.director_label}: {template.director_full_name}"
    full_width(row, footer, size=10, align="left")
    last_row = row

    ws.sheet_view.showGridLines = False
    # Intentionally no freeze_panes — the whole sheet scrolls freely with
    # nothing pinned to the top.

    # ── Page setup: A4, single page wide, tight print margins ──────────────
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins = PageMargins(left=0.25, right=0.25, top=0.3, bottom=0.3, header=0, footer=0)
    ws.print_area = f"A1:{value_letter}{last_row}"

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"system_challenge_log_{timezone.now():%Y%m%d_%H%M}.xlsx"
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ─────────────────────────────────────────────────────────────────────────────
#  PDF EXPORT — same official layout, for filing / printing / defending the
#  system on paper. Built as a proper ReportLab Table so it can be read back
#  reliably by the re-importer below.
# ─────────────────────────────────────────────────────────────────────────────
@login_required
def challenge_export_pdf(request):
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Image, KeepTogether, Paragraph, Spacer, Table, TableStyle
    from core.doc_export import SimpleDocTemplate

    template = TimetablePdfTemplate.get_template()
    header_data = template.get_header_data(timetable_type="System Challenge Log")
    challenges = _filtered_queryset(request)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=0.35 * inch, rightMargin=0.35 * inch,
        topMargin=0.3 * inch, bottomMargin=0.3 * inch,
    )
    styles = getSampleStyleSheet()
    elements = []

    title_style = ParagraphStyle("T", parent=styles["Heading1"], fontName="Times-Bold", fontSize=16, alignment=1, spaceAfter=2)
    sub_style = ParagraphStyle("S", parent=styles["Heading2"], fontName="Times-Bold", fontSize=12, alignment=1, spaceAfter=2)
    small_style = ParagraphStyle("Sm", parent=styles["Normal"], fontName="Times-Roman", fontSize=10, alignment=1, leading=13)
    meta_line_style = ParagraphStyle("Mt", parent=styles["Normal"], fontName="Times-Bold", fontSize=10, alignment=0)
    card_header_style = ParagraphStyle("CH", parent=styles["Normal"], fontName="Times-Bold", fontSize=11.5,
                                        textColor=colors.white, alignment=0, wordWrap="CJK")
    label_style = ParagraphStyle("L", parent=styles["Normal"], fontName="Times-Bold", fontSize=10.5,
                                  textColor=colors.HexColor("#1A1A1A"), leading=13, wordWrap="CJK")
    value_style = ParagraphStyle("V", parent=styles["Normal"], fontName="Times-Roman", fontSize=10.5,
                                  leading=13, wordWrap="CJK")

    # ── Logo — its own centered element sitting above every other header
    # line, the same as the Excel export ─────────────────────────────────
    try:
        if template.university_logo and hasattr(template.university_logo, "path"):
            logo = Image(template.university_logo.path, width=0.75 * inch, height=0.75 * inch)
            logo.hAlign = "CENTER"
            elements.append(logo)
            elements.append(Spacer(1, 4))
    except (OSError, ValueError) as e:
        logger.warning("Could not embed university logo in PDF export: %s", e)

    elements.append(Paragraph(escape(header_data["university_name"]), title_style))
    elements.append(Paragraph(escape(header_data["directorate_name"]), sub_style))
    elements.append(Paragraph(
        escape(f"{header_data['address']}  |  Tel: {header_data['telephone']}  |  "
               f"Email: {header_data['email']}  |  {header_data['website']}"),
        small_style,
    ))
    elements.append(Paragraph(
        escape(f"{header_data['motto_latin']}  |  {header_data['motto_swahili']}"), small_style,
    ))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph("SYSTEM CHALLENGE LOG REPORT", sub_style))

    filter_bits = [f"Generated: {timezone.now().strftime('%Y-%m-%d %H:%M')}", f"Total records: {challenges.count()}"]
    if request.GET.get("classification"):
        filter_bits.append(f"Filter — Type: {dict(SystemChallenge.CAUSE_CHOICES).get(request.GET['classification'])}")
    if request.GET.get("status"):
        filter_bits.append(f"Filter — Status: {dict(SystemChallenge.STATUS_CHOICES).get(request.GET['status'])}")
    if request.GET.get("q"):
        filter_bits.append(f"Search: '{request.GET['q']}'")
    elements.append(Paragraph(escape("   |   ".join(filter_bits)), meta_line_style))
    elements.append(Spacer(1, 8))

    # ── One card per record — Title rides as a full-width header bar (not
    # a column), Ref. Code and classification ride along on that same
    # line, and Status/both dates/who-and-when-logged share ONE compact
    # row instead of eating four separate narrow columns. ──────────────────
    page_width = A4[0] - 0.7 * inch
    label_col_w = 1.5 * inch
    value_col_w = page_width - label_col_w

    for c in challenges:
        header_text = f"{c.reference_code}    |    {c.title}    |    {c.get_classification_display_badge()}"
        rows = [[Paragraph(escape(header_text), card_header_style), ""]]

        for key, label in CARD_FIELDS:
            value = getattr(c, key) or "—"
            rows.append([Paragraph(label, label_style), Paragraph(escape(value).replace("\n", "<br/>"), value_style)])

        meta_html = "<br/>".join(escape(line) for line in _meta_lines(c))
        rows.append([Paragraph(META_LABEL, label_style), Paragraph(meta_html, value_style)])

        table = Table(rows, colWidths=[label_col_w, value_col_w])
        table.setStyle(TableStyle([
            ("SPAN", (0, 0), (1, 0)),
            ("BACKGROUND", (0, 0), (1, 0), colors.black),
            ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#D9D9D9")),
            ("LINEAFTER", (0, 0), (0, 0), 0, colors.white),  # header bar reads as one merged cell
            ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(KeepTogether(table))
        elements.append(Spacer(1, 10))

    footer = f"{template.prepared_by_label} Timetabling Office System — {template.director_label}: {template.director_full_name}"
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(escape(footer), small_style))

    doc.build(elements)
    buffer.seek(0)
    filename = f"system_challenge_log_{timezone.now():%Y%m%d_%H%M}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ─────────────────────────────────────────────────────────────────────────────
#  RE-IMPORT — read back a previously exported .xlsx or .pdf file and upsert
#  records by reference_code, so anything added or corrected by hand on the
#  exported copy is folded back into the system.
# ─────────────────────────────────────────────────────────────────────────────
def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


CARD_LABEL_TO_FIELD = {label: key for key, label in CARD_FIELDS}
_CARD_HEADER_RE = re.compile(r"^\s*(?P<ref>SC-\d{4}-\d+)\s*\|\s*(?P<title>.*?)\s*\|\s*(?P<classification>[^|]+?)\s*$")


def _parse_meta_block(text):
    """Split a 'Status & Dates' card cell back into its parts, e.g.
    'Status: Open\\nDate Occurred: 2026-01-01\\n...' -> field dict."""
    out = {}
    for line in str(text or "").split("\n"):
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        label, value = label.strip().lower(), value.strip()
        if label == "status":
            out["status_display"] = value
        elif label == "date occurred":
            out["occurred_at"] = value
        elif label == "date resolved":
            out["resolved_at"] = value
    return out


def _upsert_card_record(record, request):
    """record: dict with reference_code/title/classification_display plus
    the CARD_FIELDS keys and the parsed meta block — assembled from one
    record-card in the new Excel layout."""
    ref = (record.get("reference_code") or "").strip()
    title = (record.get("title") or "").strip()
    if not ref and not title:
        return None

    lookup = {"reference_code": ref} if ref else {"title": title}
    instance, created = SystemChallenge.objects.get_or_create(
        **lookup, defaults={"created_by": request.user, "cause": "Imported record"}
    )

    instance.title = title or instance.title
    classification_label = (record.get("classification_display") or "").strip().lower()
    if classification_label in CLASSIFICATION_BY_LABEL:
        instance.classification = CLASSIFICATION_BY_LABEL[classification_label]

    for key, _ in CARD_FIELDS:
        value = record.get(key)
        if value and value != "—":
            setattr(instance, key, value)
        elif not getattr(instance, key, None) and key == "cause":
            instance.cause = instance.cause or "Imported record"

    status_label = (record.get("status_display") or "").strip().lower()
    if status_label in STATUS_BY_LABEL:
        instance.status = STATUS_BY_LABEL[status_label]

    occurred = _parse_date(record.get("occurred_at"))
    if occurred:
        instance.occurred_at = occurred
    resolved = _parse_date(record.get("resolved_at"))
    if resolved:
        instance.resolved_at = resolved

    if not instance.pk or created:
        instance.created_by = instance.created_by or request.user

    instance.save()
    return instance, created


@login_required
@require_POST
def challenge_reimport(request):
    upload = request.FILES.get("import_file")
    if not upload:
        messages.error(request, "Please choose a file to re-import.")
        return redirect("challenge_dashboard")

    name = upload.name.lower()
    created_count = 0
    updated_count = 0
    skipped = 0

    try:
        if name.endswith(".xlsx"):
            wb = load_workbook(upload, data_only=True)
            ws = wb.active

            # Card layout: find the label column by locating a "Description"
            # label cell, then walk down. A record starts at a header row
            # matching "SC-<year>-<seq> | Title | Classification", followed
            # by label/value rows, and ends at the next header row (or a
            # blank pair of cells).
            label_col = None
            for r in ws.iter_rows(min_row=1, max_row=min(60, ws.max_row)):
                for cell in r:
                    if cell.value and str(cell.value).strip() == "Description":
                        label_col = cell.column
                        break
                if label_col:
                    break

            if not label_col:
                messages.error(request, "Could not find a record card in this file — "
                                         "please re-import a file exported from this dashboard.")
                return redirect("challenge_dashboard")

            value_col = label_col + 1
            current = None

            def flush(rec):
                nonlocal created_count, updated_count, skipped
                if rec is None:
                    return
                result = _upsert_card_record(rec, request)
                if result is None:
                    skipped += 1
                else:
                    _, created = result
                    created_count += 1 if created else 0
                    updated_count += 0 if created else 1

            for r in range(1, ws.max_row + 1):
                label_val = ws.cell(row=r, column=label_col).value
                header_match = _CARD_HEADER_RE.match(str(label_val)) if label_val else None
                if header_match:
                    flush(current)
                    current = {
                        "reference_code": header_match.group("ref"),
                        "title": header_match.group("title"),
                        "classification_display": header_match.group("classification"),
                    }
                    continue
                if current is None or not label_val:
                    continue
                label_text = str(label_val).strip()
                value_val = ws.cell(row=r, column=value_col).value
                if label_text == META_LABEL:
                    current.update(_parse_meta_block(value_val))
                elif label_text in CARD_LABEL_TO_FIELD:
                    current[CARD_LABEL_TO_FIELD[label_text]] = (str(value_val).strip()
                                                                 if value_val not in (None, "—") else "")
            flush(current)

        elif name.endswith(".pdf"):
            import pdfplumber

            def parse_pdf_card_table(table):
                if not table or not table[0]:
                    return None
                header_text = str(table[0][0] or "").strip()
                m = _CARD_HEADER_RE.match(header_text)
                if not m:
                    return None
                rec = {
                    "reference_code": m.group("ref"),
                    "title": m.group("title"),
                    "classification_display": m.group("classification"),
                }
                for raw_row in table[1:]:
                    if not raw_row or len(raw_row) < 2:
                        continue
                    # Normalize whitespace: a wrapped label (e.g. "Involved
                    # Users /\nParties") comes back from pdfplumber with a
                    # line break where the export just had a space wrap.
                    label_text = " ".join(str(raw_row[0] or "").split())
                    value_text = str(raw_row[1] or "").strip()
                    if label_text == META_LABEL:
                        # The Status & Dates block uses real line breaks on
                        # purpose (one per field) — keep them as-is.
                        rec.update(_parse_meta_block(value_text))
                    elif label_text in CARD_LABEL_TO_FIELD:
                        # Every other value is normal prose that only wraps
                        # across lines because of column width — collapse
                        # those wrap-artifact line breaks back to spaces.
                        value_text = " ".join(value_text.split())
                        rec[CARD_LABEL_TO_FIELD[label_text]] = value_text if value_text not in ("", "—") else ""
                return rec

            with pdfplumber.open(upload) as pdf:
                for page in pdf.pages:
                    for table in page.extract_tables():
                        record = parse_pdf_card_table(table)
                        if record is None:
                            continue
                        result = _upsert_card_record(record, request)
                        if result is None:
                            skipped += 1
                        else:
                            _, created = result
                            created_count += 1 if created else 0
                            updated_count += 0 if created else 1
        else:
            messages.error(request, "Unsupported file type — please upload the .xlsx or .pdf exported from this dashboard.")
            return redirect("challenge_dashboard")

    except Exception:
        logger.exception("System challenge re-import failed")
        messages.error(request, "Could not read that file. Please re-import a file exported from this dashboard, unedited in structure.")
        return redirect("challenge_dashboard")

    messages.success(
        request,
        f"Re-import complete: {created_count} new record(s) added, {updated_count} existing record(s) updated"
        + (f", {skipped} blank row(s) skipped." if skipped else ".")
    )
    return redirect("challenge_dashboard")
