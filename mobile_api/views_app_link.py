"""
mobile_api/views_app_link.py
=============================
The "Share the App" link, editable by staff and read live by the app.

    GET  /api/mobile/app-link/           — public; the app calls this
    POST /api/mobile/admin/app-link/save/ — staff; set or clear the custom link

Public GET is deliberate: the link is meant to be handed to anyone, and the
app calls it before/without a staff session. It exposes nothing but the URL.
"""
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from core.models import SiteSettings
from core.rbac import allowed_roles, Role

from .app_link import resolve_app_link

APP_LINK_STAFF_ROLES = (Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)

_url_validator = URLValidator(schemes=["http", "https"])


def _payload(request):
    data = resolve_app_link(request)
    return {
        "ok": True,
        "url": data["url"],
        "source": data["source"],
        "version": data["version"],
        "customUrl": data["custom_url"],
    }


@require_GET
@ratelimit(key="ip", rate="60/m", method="GET", block=True)
def app_link(request):
    response = JsonResponse(_payload(request))
    response["Cache-Control"] = "no-store"
    return response


@allowed_roles(*APP_LINK_STAFF_ROLES)
@require_POST
def api_save_app_link(request):
    url = request.POST.get("url", "").strip()

    if url:
        # Be forgiving: staff often paste "play.google.com/..." without a scheme.
        if "://" not in url:
            url = "https://" + url
        try:
            _url_validator(url)
        except ValidationError:
            return JsonResponse(
                {"ok": False, "error": "That doesn't look like a valid web link. Include the full address, e.g. https://…"},
                status=400,
            )
        if len(url) > 500:
            return JsonResponse({"ok": False, "error": "Link is too long (max 500 characters)."}, status=400)

    site_settings = SiteSettings.get_settings()
    site_settings.mobile_app_share_url = url  # blank = clear override
    site_settings.save(update_fields=["mobile_app_share_url", "updated_at"])

    return JsonResponse(_payload(request))
