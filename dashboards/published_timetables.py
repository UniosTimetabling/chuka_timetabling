# dashboards/published_timetables.py
# ──────────────────────────────────────────────────────────────────────────────
#  Published Timetables Registry — PUBLIC (no login required)
#  Shows stored PDF records from every sub-system. Download links are
#  secured via short-lived HMAC tokens (core.signed_download) — no login
#  is needed, but the link is not guessable without Django's SECRET_KEY.
#
#  Sections: Regular · Exam · Resit · ODEL Class · ODEL Exam
#            · Campus Class · Campus Exam
# ──────────────────────────────────────────────────────────────────────────────

from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from core.signed_download import make_download_token


def _fmt_size(size_bytes):
    if not size_bytes:
        return "—"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _fmt_dt(dt):
    try:
        return dt.strftime("%d %b %Y, %H:%M")
    except Exception:
        return str(dt) if dt else "—"


def _signed_url(url_name, resource_type, pk, **kwargs):
    """Build a signed download URL for the given endpoint."""
    token = make_download_token(resource_type, pk)
    base = reverse(url_name, kwargs=kwargs) if kwargs else reverse(url_name)
    return f"{base}?token={token}"


# ── Regular class timetable PDFs ───────────────────────────────────────────────
def _get_regular():
    from export_import.models import PDFDocument
    qs = PDFDocument.objects.filter(
        document_type="REGULAR"
    ).select_related("previous_version").order_by("-uploaded_at")

    rows = []
    for doc in qs:
        rows.append({
            "id":            doc.pk,
            "title":         doc.title,
            "academic_year": doc.academic_year,
            "semester":      doc.get_semester_display(),
            "version":       doc.version,
            "status":        doc.get_status_display(),
            "status_key":    doc.status,
            "is_latest":     doc.is_latest,
            "uploaded_by":   doc.uploaded_by or "—",
            "uploaded_at":   _fmt_dt(doc.uploaded_at),
            "file_size":     _fmt_size(doc.file_size),
            "download_url":  _signed_url("download_latest_regular", "regular", doc.pk),
            "description":   doc.description or "",
        })
    return rows


# ── Main exam timetable PDFs ──────────────────────────────────────────────────
def _get_exam():
    from export_import.models import PDFDocument
    qs = PDFDocument.objects.filter(
        document_type="EXAM"
    ).select_related("previous_version").order_by("-uploaded_at")

    rows = []
    for doc in qs:
        rows.append({
            "id":            doc.pk,
            "title":         doc.title,
            "academic_year": doc.academic_year,
            "semester":      doc.get_semester_display(),
            "version":       doc.version,
            "status":        doc.get_status_display(),
            "status_key":    doc.status,
            "is_latest":     doc.is_latest,
            "uploaded_by":   doc.uploaded_by or "—",
            "uploaded_at":   _fmt_dt(doc.uploaded_at),
            "file_size":     _fmt_size(doc.file_size),
            "download_url":  _signed_url("download_latest_exam", "exam", doc.pk),
            "description":   doc.description or "",
        })
    return rows


# ── Resit timetable PDFs ──────────────────────────────────────────────────────
def _get_resit():
    from resits_timetabling.models import ResitPublishedPDF
    qs = ResitPublishedPDF.objects.select_related("published_by").order_by("-published_at")

    rows = []
    for doc in qs:
        rows.append({
            "id":            doc.pk,
            "title":         f"Resit Timetable — {doc.academic_year} {doc.semester}",
            "academic_year": doc.academic_year or "—",
            "semester":      doc.semester or "—",
            "version":       doc.version,
            "total_entries": doc.total_entries,
            "published_by":  doc.published_by.get_full_name() if doc.published_by else "System",
            "published_at":  _fmt_dt(doc.published_at),
            "file_size":     f"{doc.file_size_kb} KB" if doc.file_size_kb else "—",
            "download_url":  _signed_url("resit_pdf_download", "resit", doc.pk),
            "notes":         doc.notes or "",
        })
    return rows


# ── ODEL class timetable PDFs ─────────────────────────────────────────────────
def _get_odel_class():
    from odel_system.models import PublishedTimetablePDF
    qs = PublishedTimetablePDF.objects.filter(
        is_class_timetable=True
    ).order_by("-published_at")

    rows = []
    for doc in qs:
        rows.append({
            "id":           doc.pk,
            "title":        f"ODEL Class Timetable v{doc.version}",
            "version":      doc.version,
            "is_latest":    doc.is_latest,
            "published_at": _fmt_dt(doc.published_at),
            "download_url": _signed_url("odel_download_latest_class", "odel_class", doc.pk),
        })
    return rows


# ── ODEL exam timetable PDFs ──────────────────────────────────────────────────
def _get_odel_exam():
    from odel_system.models import PublishedTimetablePDF
    qs = PublishedTimetablePDF.objects.filter(
        is_class_timetable=False
    ).order_by("-published_at")

    rows = []
    for doc in qs:
        rows.append({
            "id":           doc.pk,
            "title":        f"ODEL Exam Timetable v{doc.version}",
            "version":      doc.version,
            "is_latest":    doc.is_latest,
            "published_at": _fmt_dt(doc.published_at),
            "download_url": _signed_url("odel_download_latest_exam", "odel_exam", doc.pk),
        })
    return rows


# ── Campus class timetable PDFs ────────────────────────────────────────────────
def _get_campus_class():
    from campuses_timetable.models import CampusPublishedTimetablePDF
    qs = CampusPublishedTimetablePDF.objects.filter(
        timetable_type="CLASS"
    ).select_related("campus", "published_by").order_by("-published_at")

    rows = []
    for doc in qs:
        campus_label = doc.campus.name if doc.campus else "All Campuses"
        rows.append({
            "id":            doc.pk,
            "title":         f"Campus Class Timetable — {campus_label}",
            "campus":        campus_label,
            "version":       doc.version,
            "is_latest":     doc.is_latest,
            "published_by":  doc.published_by.get_full_name() if doc.published_by else "System",
            "published_at":  _fmt_dt(doc.published_at),
            "file_size":     _fmt_size(doc.file_size),
            "download_count": doc.download_count,
            "description":   doc.description or "",
            "download_url":  _signed_url("campuses_download_latest_class", "campus_class", doc.pk),
        })
    return rows


# ── Campus exam timetable PDFs ─────────────────────────────────────────────────
def _get_campus_exam():
    from campuses_timetable.models import CampusPublishedTimetablePDF
    qs = CampusPublishedTimetablePDF.objects.filter(
        timetable_type="EXAM"
    ).select_related("campus", "published_by").order_by("-published_at")

    rows = []
    for doc in qs:
        campus_label = doc.campus.name if doc.campus else "All Campuses"
        rows.append({
            "id":            doc.pk,
            "title":         f"Campus Exam Timetable — {campus_label}",
            "campus":        campus_label,
            "version":       doc.version,
            "is_latest":     doc.is_latest,
            "published_by":  doc.published_by.get_full_name() if doc.published_by else "System",
            "published_at":  _fmt_dt(doc.published_at),
            "file_size":     _fmt_size(doc.file_size),
            "download_count": doc.download_count,
            "description":   doc.description or "",
            "download_url":  _signed_url("campuses_download_latest_exam", "campus_exam", doc.pk),
        })
    return rows


# ── Main view — NO login required ─────────────────────────────────────────────
def published_timetables_view(request):
    section = request.GET.get("section", "regular")

    sections = [
        ("regular",      "Regular",       "fa-chalkboard-teacher"),
        ("exam",         "Exam",          "fa-pen-alt"),
        ("resit",        "Resit",         "fa-redo-alt"),
        ("odel_class",   "ODEL Class",    "fa-satellite-dish"),
        ("odel_exam",    "ODEL Exam",     "fa-satellite-dish"),
        ("campus_class", "Campus Class",  "fa-map-marker-alt"),
        ("campus_exam",  "Campus Exam",   "fa-map-marker-alt"),
    ]

    dispatch = {
        "regular":      _get_regular,
        "exam":         _get_exam,
        "resit":        _get_resit,
        "odel_class":   _get_odel_class,
        "odel_exam":    _get_odel_exam,
        "campus_class": _get_campus_class,
        "campus_exam":  _get_campus_exam,
    }

    rows = []
    error = None
    try:
        rows = dispatch.get(section, _get_regular)()
    except Exception as e:
        error = str(e)

    context = {
        "section":        section,
        "sections":       sections,
        "rows":           rows,
        "row_count":      len(rows),
        "error":          error,
        "back_url":       request.META.get("HTTP_REFERER", "/"),
        "page_title":     "Published Timetables",
        "now":            timezone.now(),
        "is_public_view": True,
    }
    return render(request, "dashboard/published_timetables.html", context)
