import json
import mimetypes
import os

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.models import SiteSettings, SitePhoto


# ---------------------------------------------------------------------------
# Context processor (add to TEMPLATES > OPTIONS > context_processors)
# ---------------------------------------------------------------------------
def site_settings_context(request):
    """
    Injects `site_settings` and `carousel_photos` into every template
    so base.html can render the logo, name, and carousel without extra view code.
    """
    return {
        "site_settings": SiteSettings.get_settings(),
        "carousel_photos": SitePhoto.objects.filter(is_active=True).order_by("order", "uploaded_at"),
    }




# ---------------------------------------------------------------------------
# AJAX — secure logo endpoint
# ---------------------------------------------------------------------------
def site_logo(request):
    """
    Serve the logo via a view so the real upload path stays private.
    Returns the logo bytes with the correct Content-Type.
    Falls back to a 1px transparent PNG if no logo is set.
    """
    settings = SiteSettings.get_settings()

    if settings.logo and os.path.isfile(settings.logo.path):
        mime, _ = mimetypes.guess_type(settings.logo.path)
        mime = mime or "image/png"
        from django.http import FileResponse
        return FileResponse(open(settings.logo.path, "rb"), content_type=mime)

    # --- Fallback: serve an inline SVG logo (avoids external network call) ---
    from django.http import HttpResponse
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48">'
        '<rect width="48" height="48" rx="6" fill="#005a48"/>'
        '<text x="50%" y="54%" dominant-baseline="middle" text-anchor="middle" '
        'font-family="Arial,sans-serif" font-size="16" font-weight="bold" fill="#ffffff">CU</text>'
        '</svg>'
    )
    return HttpResponse(svg, content_type="image/svg+xml")


# ---------------------------------------------------------------------------
# AJAX — secure photo endpoint (serves individual photos)
# ---------------------------------------------------------------------------
def site_photo(request, photo_id):
    """
    Serve a single gallery photo securely.
    Anonymous users can only view active photos.
    """
    photo = get_object_or_404(SitePhoto, pk=photo_id, is_active=True)

    if not os.path.isfile(photo.image.path):
        from django.http import HttpResponseNotFound
        return HttpResponseNotFound("Photo file not found.")

    mime, _ = mimetypes.guess_type(photo.image.path)
    mime = mime or "image/jpeg"
    from django.http import FileResponse
    return FileResponse(open(photo.image.path, "rb"), content_type=mime)


# ---------------------------------------------------------------------------
# AJAX — site settings JSON (used by admin JS, no sensitive data exposed)
# ---------------------------------------------------------------------------
def site_settings_json(request):
    """
    Returns non-sensitive site settings as JSON for front-end JS use.
    Does NOT expose file paths — only public-facing display data.
    """
    settings = SiteSettings.get_settings()
    photos = SitePhoto.objects.filter(is_active=True).order_by("order", "uploaded_at")

    data = {
        "university_name": settings.university_name,
        "tagline": settings.tagline,
        "contact_email": settings.contact_email,
        "contact_phone": settings.contact_phone,
        "contact_location": settings.contact_location,
        "website_url": settings.website_url,
        "logo_url": request.build_absolute_uri("/site/logo/"),
        "social": {
            "twitter":  settings.twitter_url,
            "facebook": settings.facebook_url,
            "linkedin": settings.linkedin_url,
            "youtube":  settings.youtube_url,
        },
        "photos": [
            {
                "id":      p.pk,
                "url":     request.build_absolute_uri(f"/site/photo/{p.pk}/"),
                "caption": p.caption,
                "alt":     p.alt_text or p.caption or "Gallery image",
                "order":   p.order,
            }
            for p in photos
        ],
    }
    return JsonResponse(data)