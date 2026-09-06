"""
export_import/download_latest_timetable_pdf.py

Public download endpoints for the latest Regular and Exam timetable PDFs.

Security
────────
No login is required, but every request must carry a short-lived HMAC token
issued by the published_timetables view (core.signed_download).  This means:
  • The URL is not guessable — a correct token requires Django's SECRET_KEY.
  • Tokens expire after DOWNLOAD_TOKEN_MAX_AGE_SECONDS (default 1 h).
  • A token for "regular" cannot be replayed against "exam" and vice-versa.

If no token (or an invalid/expired one) is supplied, a 403 is returned.
"""

import os

from django.http import FileResponse, HttpResponse, HttpResponseNotFound
from django.views.decorators.http import require_GET

from core.signed_download import validate_download_token
from .models import PDFDocument


def _token_error(msg="Invalid or expired download link."):
    return HttpResponse(
        f"<h2 style='font-family:sans-serif;margin:40px auto;text-align:center;color:#b71c1c;'>"
        f"{msg}</h2>",
        content_type="text/html",
        status=403,
    )


@require_GET
def download_latest_regular(request):
    """
    Public endpoint — download the latest published Regular timetable PDF.
    Requires a valid signed token: ?token=<signed>
    """
    _, pk = validate_download_token(request.GET.get("token"), "regular")
    if pk is None:
        return _token_error()

    try:
        doc = PDFDocument.objects.get(
            pk=pk,
            document_type="REGULAR",
            status="PUBLISHED",
        )
    except PDFDocument.DoesNotExist:
        return HttpResponseNotFound("Timetable record not found.")

    if not doc.pdf_file:
        return HttpResponseNotFound("PDF file not attached to this record.")

    file_path = doc.pdf_file.path
    if not os.path.exists(file_path):
        return HttpResponseNotFound("PDF file missing from storage.")

    filename = f"regular_timetable_{doc.academic_year}_sem{doc.semester}.pdf"
    response = FileResponse(open(file_path, "rb"), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_GET
def download_latest_exam(request):
    """
    Public endpoint — download the latest published Exam timetable PDF.
    Requires a valid signed token: ?token=<signed>
    """
    _, pk = validate_download_token(request.GET.get("token"), "exam")
    if pk is None:
        return _token_error()

    try:
        doc = PDFDocument.objects.get(
            pk=pk,
            document_type="EXAM",
            status="PUBLISHED",
        )
    except PDFDocument.DoesNotExist:
        return HttpResponseNotFound("Timetable record not found.")

    if not doc.pdf_file:
        return HttpResponseNotFound("PDF file not attached to this record.")

    file_path = doc.pdf_file.path
    if not os.path.exists(file_path):
        return HttpResponseNotFound("PDF file missing from storage.")

    filename = f"exam_timetable_{doc.academic_year}_sem{doc.semester}.pdf"
    response = FileResponse(open(file_path, "rb"), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
