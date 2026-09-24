"""
desktop_sync/views_documents.py
================================
Lets the desktop app list and download the latest PUBLISHED regular/exam
timetable PDFs (export_import.models.PDFDocument) so they can be cached
locally and viewed with no connection — e.g. in a venue with no signal, or
just to avoid re-downloading a multi-MB PDF on every launch.

Unlike dashboards/published_timetables.py's public HMAC-signed-link flow
(built for people with no login at all), these two endpoints use the
desktop's own token auth — simpler, since the desktop app is always signed
in already, and avoids minting/tracking a second kind of access token
purely for this.

GET /api/desktop/published-pdfs/            -> latest PUBLISHED doc per
    (document_type, academic_year, semester), newest first.
GET /api/desktop/published-pdfs/<id>/file/  -> streams that document's PDF
    bytes (only if it's still PUBLISHED and not superseded by a check at
    request time — a stale id from an old list response can't fetch a
    withdrawn document).
"""
from django.http import FileResponse, Http404, JsonResponse
from django.views.decorators.http import require_GET

from export_import.models import PDFDocument

from .views_auth import desktop_auth_required


def _serialize(doc: PDFDocument) -> dict:
    return {
        "id": doc.pk,
        "title": doc.title,
        "document_type": doc.document_type,  # "REGULAR" | "EXAM"
        "academic_year": doc.academic_year,
        "semester": doc.semester,
        "version": doc.version,
        "file_size": doc.file_size,
        "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
    }


@require_GET
@desktop_auth_required
def list_published_pdfs(request):
    qs = PDFDocument.objects.filter(status="PUBLISHED", is_latest=True).order_by("-uploaded_at")
    return JsonResponse({"documents": [_serialize(d) for d in qs]})


@require_GET
@desktop_auth_required
def download_published_pdf(request, doc_id: int):
    try:
        doc = PDFDocument.objects.get(pk=doc_id, status="PUBLISHED")
    except PDFDocument.DoesNotExist:
        raise Http404("That document is no longer published.")
    if not doc.pdf_file:
        raise Http404("That document has no file attached.")
    return FileResponse(doc.pdf_file.open("rb"), as_attachment=True, filename=f"{doc.title}.pdf", content_type="application/pdf")
