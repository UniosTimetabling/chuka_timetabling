"""
mobile_api/app_link.py
=======================
Single source of truth for "where do people go to install the app?".

Used by BOTH the staff Mobile Analytics page (QR code + link card) and the
public GET /api/mobile/app-link/ endpoint the mobile app reads, so what
staff see on the dashboard is always exactly what the app shares.

Priority (first that exists wins):
  1. custom   — SiteSettings.mobile_app_share_url, editable on /mobile/analytics/
  2. apk      — the .apk uploaded in Site Settings (direct download)
  3. default  — settings.MOBILE_APP_STORE_URL (Play Store listing)
"""
from django.conf import settings

from core.models import SiteSettings

SOURCE_CUSTOM = "custom"
SOURCE_APK = "apk"
SOURCE_DEFAULT = "default"


def resolve_app_link(request):
    """Return {"url", "source", "version", "default_url"} for the current state."""
    site_settings = SiteSettings.get_settings()
    custom = (site_settings.mobile_app_share_url or "").strip()
    has_apk = bool(site_settings.mobile_apk)

    if custom:
        url, source = custom, SOURCE_CUSTOM
    elif has_apk:
        url, source = request.build_absolute_uri(site_settings.mobile_apk.url), SOURCE_APK
    else:
        url, source = settings.MOBILE_APP_STORE_URL, SOURCE_DEFAULT

    return {
        "url": url,
        "source": source,
        "version": site_settings.mobile_apk_version if source == SOURCE_APK else "",
        "custom_url": custom,
    }
