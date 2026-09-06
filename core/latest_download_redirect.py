"""
core/latest_download_redirect.py
─────────────────────────────────
Fixes the "Invalid or expired download link." bug on public quick-download
buttons (e.g. dashboards/templates/dashboard/unios_homepage.html).

Root cause
──────────
Every download_latest_* endpoint (export_import, campuses_timetable,
odel_system, resits_timetabling) requires a signed ?token=<hmac> minted
for one specific PDFDocument pk (see core/signed_download.py). The
homepage template linked STRAIGHT to those endpoints with no token at
all, e.g.:

    <a href="{% url 'download_latest_regular' %}">Regular PDF</a>

`validate_download_token(None, ...)` always fails, so every click showed
"Invalid or expired download link." — even seconds after a fresh publish,
and regardless of whether a timetable exists at all. The
published_timetables registry view already does this correctly
(core.signed_download.make_download_token per row), but the homepage
quick-links never went through it.

Fix
───
This view is the missing middle step for a link that doesn't yet know
which PDF pk to download. It looks up the latest published record for
the requested section, mints a fresh token, and 302-redirects to the
real signed download URL — the same thing published_timetables.py does
per-row, just resolved for "whatever is newest right now".

If nothing has been published yet for that section, it shows a plain,
friendly "not available yet" message instead of the cryptic 403 — this
is the case the user is most likely to hit while a system is still new
or a section hasn't been published for the current semester yet.

Usage
─────
    # core/urls.py
    path("go/<str:section>/", latest_download_redirect, name="latest_download_redirect")

    # template
    <a href="{% url 'latest_download_redirect' 'regular' %}">Regular PDF</a>

Valid `section` values: regular, exam, resit, odel_class, odel_exam,
campus_class, campus_exam — same vocabulary as published_timetables.py.
"""
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from core.signed_download import make_download_token

_NOT_AVAILABLE_HTML = """
<div style="font-family:sans-serif;max-width:520px;margin:80px auto;
            text-align:center;padding:32px 28px;border:1px solid #e0e0e0;
            border-radius:4px;">
  <h2 style="color:#107c41;margin:0 0 12px;">No {label} timetable published yet</h2>
  <p style="color:#555;margin:0 0 20px;line-height:1.5;">
    Nothing has been published for this section yet. Please check back later,
    or contact your department for updates.
  </p>
  <a href="{back}" style="color:#107c41;text-decoration:none;font-weight:bold;">&larr; Back</a>
</div>
"""


def _latest_regular():
    from export_import.models import PDFDocument
    doc = (PDFDocument.objects
           .filter(document_type="REGULAR", status="PUBLISHED")
           .order_by("-uploaded_at").first())
    return ("download_latest_regular", "regular", doc.pk) if doc else None


def _latest_exam():
    from export_import.models import PDFDocument
    doc = (PDFDocument.objects
           .filter(document_type="EXAM", status="PUBLISHED")
           .order_by("-uploaded_at").first())
    return ("download_latest_exam", "exam", doc.pk) if doc else None


def _latest_resit():
    from resits_timetabling.models import ResitPublishedPDF
    doc = ResitPublishedPDF.objects.order_by("-published_at").first()
    return ("resit_pdf_download", "resit", doc.pk) if doc else None


def _latest_odel_class():
    from odel_system.models import PublishedTimetablePDF
    doc = (PublishedTimetablePDF.objects
           .filter(is_class_timetable=True)
           .order_by("-published_at").first())
    return ("odel_system:odel_download_latest_class", "odel_class", doc.pk) if doc else None


def _latest_odel_exam():
    from odel_system.models import PublishedTimetablePDF
    doc = (PublishedTimetablePDF.objects
           .filter(is_class_timetable=False)
           .order_by("-published_at").first())
    return ("odel_system:odel_download_latest_exam", "odel_exam", doc.pk) if doc else None


def _latest_campus_class():
    from campuses_timetable.models import CampusPublishedTimetablePDF
    doc = (CampusPublishedTimetablePDF.objects
           .filter(timetable_type="CLASS")
           .order_by("-published_at").first())
    return ("campuses_timetable:campuses_download_latest_class", "campus_class", doc.pk) if doc else None


def _latest_campus_exam():
    from campuses_timetable.models import CampusPublishedTimetablePDF
    doc = (CampusPublishedTimetablePDF.objects
           .filter(timetable_type="EXAM")
           .order_by("-published_at").first())
    return ("campuses_timetable:campuses_download_latest_exam", "campus_exam", doc.pk) if doc else None


_SECTIONS = {
    "regular":      ("Regular",      _latest_regular),
    "exam":         ("Exam",         _latest_exam),
    "resit":        ("Resit",        _latest_resit),
    "odel_class":   ("ODEL Class",   _latest_odel_class),
    "odel_exam":    ("ODEL Exam",    _latest_odel_exam),
    "campus_class": ("Campus Class", _latest_campus_class),
    "campus_exam":  ("Campus Exam",  _latest_campus_exam),
}


def latest_download_redirect(request, section):
    """
    Public, no-login entry point for quick-download links that don't
    already know a specific PDF pk. Resolves the latest published record
    for `section`, mints a signed token, and redirects to the real
    download endpoint — or shows a friendly message if nothing is
    published yet, instead of a raw 403.
    """
    entry = _SECTIONS.get(section)
    if entry is None:
        return HttpResponse("Unknown timetable section.", status=404)

    label, lookup_fn = entry
    try:
        result = lookup_fn()
    except Exception:
        # Missing table/migration, DB hiccup, etc. — degrade to the same
        # friendly "not available" message rather than a 500.
        result = None

    if result is None:
        back = request.META.get("HTTP_REFERER") or "/"
        return HttpResponse(
            _NOT_AVAILABLE_HTML.format(label=label, back=back),
            content_type="text/html",
            status=200,
        )

    url_name, resource_type, pk = result
    token = make_download_token(resource_type, pk)
    return redirect(f"{reverse(url_name)}?token={token}")
