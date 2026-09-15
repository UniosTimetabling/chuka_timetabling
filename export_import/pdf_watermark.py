"""
Shared watermark helpers for the export_import PDF generators.

Two rendering paths exist in this app:
  - ReportLab (SimpleDocTemplate/canvas) — official_timetables.py,
    official_exam_timetable.py, course_list_pdf.py, global_combined_timetable_pdf.py, etc.
  - WeasyPrint (HTML/CSS) — any exporter that builds via render_to_string() + HTML().

Both draw the SAME concept: a faint, tiled, diagonal "<directorate name> ·
<code>" mark that sits behind the page content and is never intrusive
enough to obscure the timetable grid, but is present across the whole
page (so cropping a section of the PDF still carries it). The <code> comes
from TimetablePdfTemplate.generate_watermark_code(), which is a keyed
HMAC of the document's reference number using a secret that is never
exposed to admins — so the mark cannot be reproduced on a document that
didn't actually pass through this system.
"""
from urllib.parse import quote

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4


def draw_reportlab_watermark(canvas_obj, doc_obj, template_config, doc_ref=""):
    """
    Paint the tiled watermark onto the current page's canvas. Call this
    FIRST inside the onFirstPage/onLaterPages callback, before anything
    else touches the canvas — platypus paints the page's flowables (the
    actual table/content) on top of this afterwards, so the watermark
    naturally sits behind the content.

    Works for both portrait and landscape docs: page size is read off
    `doc_obj` (SimpleDocTemplate exposes .pagesize) rather than assumed,
    since some exporters in this app use landscape(A4).
    """
    if not template_config or not getattr(template_config, "watermark_enabled", False):
        return

    try:
        code = template_config.generate_watermark_code(doc_ref)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "watermark code generation failed; skipping watermark for this page"
        )
        return

    label = f"{template_config.directorate_name}  \u00b7  {code}"

    page_w, page_h = getattr(doc_obj, "pagesize", A4)
    canvas_obj.saveState()
    try:
        canvas_obj.setFont('Helvetica-Bold', 7.5)
        canvas_obj.setFillColor(colors.Color(0.5, 0.5, 0.5, alpha=0.09))

        # Rotate the whole coordinate system so the tiled text reads
        # diagonally, matching a classic "not for redistribution" stamp.
        canvas_obj.translate(page_w / 2.0, page_h / 2.0)
        canvas_obj.rotate(35)
        canvas_obj.translate(-page_w / 2.0, -page_h / 2.0)

        step_x, step_y = 280, 130
        y = -page_h * 0.5
        while y < page_h * 1.5:
            x = -page_w * 0.5
            while x < page_w * 1.5:
                canvas_obj.drawString(x, y, label)
                x += step_x
            y += step_y
    finally:
        canvas_obj.restoreState()


def get_watermark_css(template_config, doc_ref=""):
    """
    Return a <style> body for HTML/WeasyPrint exports that paints the same
    tiled watermark as a fixed, full-page background layer sitting behind
    the content (z-index -1). Returns '' when watermarking is disabled so
    callers can always safely inject the result into their template.
    """
    if not template_config or not template_config.watermark_enabled:
        return ""

    code = template_config.generate_watermark_code(doc_ref)
    label = f"{template_config.directorate_name} \u00b7 {code}"
    svg_text = quote(label)

    svg_data_uri = (
        "data:image/svg+xml;utf8,"
        "<svg xmlns='http://www.w3.org/2000/svg' width='280' height='95'>"
        "<text x='0' y='55' font-size='11' font-family='Helvetica, Arial, sans-serif' "
        "font-weight='bold' fill='rgba(115,115,115,0.11)' "
        f"transform='rotate(-35 140 47)'>{svg_text}</text>"
        "</svg>"
    )

    return f"""
    .cu-watermark-layer {{
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        z-index: -1;
        pointer-events: none;
        background-image: url("{svg_data_uri}");
        background-repeat: repeat;
    }}
    """


WATERMARK_DIV_HTML = '<div class="cu-watermark-layer"></div>'
