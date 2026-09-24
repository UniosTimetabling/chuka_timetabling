"""
mobile_api/views_install.py
=============================
POST /api/mobile/install/   { deviceId, platform, appVersion }

The mobile app calls this once per device — right after it launches for
the first time after a fresh install, before any login — so the
Timetabling Dashboard's Mobile Analytics page can show how many distinct
devices actually have the app installed, not just how many people have
logged in (this system has no per-student account to count logins against
anyway — see mobile_api.scope).

Public and csrf_exempt for the same reason as views_auth.student_login /
lecturer_login: there's no session yet for the app to send a CSRF token
for. Idempotent by design — get_or_create on device_id — so a later
launch from the same device just refreshes last_seen_at/app_version
instead of creating a second row; a genuine reinstall (which clears the
app's local storage, so a fresh device_id gets generated) is the only way
to be counted again, which matches what "installed" should mean.
"""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .models import DeviceInstall


def _json_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}


@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="20/m", method="POST", block=True)
def report_install(request):
    data = _json_body(request)
    device_id = (data.get("deviceId") or "").strip()[:200]
    platform = (data.get("platform") or "").strip().lower()
    app_version = (data.get("appVersion") or "").strip()[:30]

    if not device_id:
        return JsonResponse({"error": "deviceId is required."}, status=400)

    if platform not in dict(DeviceInstall.PLATFORM_CHOICES):
        platform = DeviceInstall.OTHER

    obj, created = DeviceInstall.objects.get_or_create(
        device_id=device_id,
        defaults={"platform": platform, "app_version": app_version},
    )
    if not created:
        # Same device checking in again (a later launch) — refresh what we
        # know about it, but this never counts as a second install.
        obj.platform = platform or obj.platform
        obj.app_version = app_version or obj.app_version
        obj.save(update_fields=["platform", "app_version", "last_seen_at"])

    return JsonResponse({"ok": True, "isNewInstall": created})
