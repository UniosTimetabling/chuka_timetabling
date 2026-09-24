"""
desktop_sync/views_auth.py
=============================
POST /api/desktop/auth/login/    { username, password, device_label? } -> { user: {..., token} }
POST /api/desktop/auth/logout/   (Authorization: Token <key>)          -> { ok: true }
GET  /api/desktop/auth/me/       (Authorization: Token <key>)          -> { user }

Who may sign in: exactly the roles that may edit the timetable in the web app
(timetable_panel / exam_timetable_panel are gated by
`@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)`); superusers
pass too. `is_staff` is NOT used — this project never relies on it for access
control, so gating on it would lock out real Timetable Office accounts and let
in any unrelated staff account.

The role check runs on EVERY authenticated request, not just at login, so
removing someone from the group cuts off an already-issued token immediately.
"""
import functools
import json
import logging

from django.contrib.auth import authenticate
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from core.rbac import Role, get_user_roles, user_has_role
from core.signals import set_current_user
from .models import DesktopAuthToken

security_logger = logging.getLogger("security")

# Same trio the web timetable panels use. Superusers always pass user_has_role().
DESKTOP_ROLES = (Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)


def client_ip(request) -> str:
    """
    Real client IP behind the project's nginx. nginx sets X-Real-IP to
    $remote_addr (overwriting anything the client sent); X-Forwarded-For's
    FIRST entry is client-controlled, so it is deliberately not trusted here.
    """
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR", "") or "unknown"


def _ip_key(group, request):
    return client_ip(request)


def _username_key(group, request):
    return "u:" + (_json_body(request).get("username") or "").strip().lower()[:150]


def _json_body(request) -> dict:
    cached = getattr(request, "_desktop_json", None)
    if cached is None:
        try:
            cached = json.loads(request.body or "{}")
            if not isinstance(cached, dict):
                cached = {}
        except (ValueError, UnicodeDecodeError):
            cached = {}
        request._desktop_json = cached
    return cached


def _serialize_user(user, token: DesktopAuthToken | None = None):
    data = {
        "username": user.username,
        "display_name": user.get_full_name() or user.username,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "roles": sorted(get_user_roles(user)),
    }
    if token:
        data["token"] = token.key
        data["device_label"] = token.device_label
    return data


@csrf_exempt  # token-authenticated JSON API for a native client; no cookies involved
@require_POST
@ratelimit(key=_ip_key, rate="30/m", method="POST", block=False)
@ratelimit(key=_username_key, rate="10/m", method="POST", block=False)
def login(request):
    if getattr(request, "limited", False):
        return JsonResponse({"error": "Too many sign-in attempts. Wait a minute and try again."}, status=429)

    body = _json_body(request)
    username = str(body.get("username") or "").strip().lower()
    password = str(body.get("password") or "")
    device_label = str(body.get("device_label") or "")[:150]

    if not username or not password:
        return JsonResponse({"error": "Username and password are required."}, status=400)

    user = authenticate(request, username=username, password=password)
    if user is None:
        security_logger.warning("Desktop login failure | attempted_user=%s | ip=%s", username, client_ip(request))
        return JsonResponse({"error": "Invalid username or password."}, status=401)
    if not user_has_role(user, *DESKTOP_ROLES):
        security_logger.warning("Desktop login denied (role) | user=%s | ip=%s", user.username, client_ip(request))
        return JsonResponse(
            {"error": "This account is not authorized for the desktop app (Timetable Office roles only)."},
            status=403,
        )

    token = DesktopAuthToken.objects.create(user=user, device_label=device_label)
    security_logger.info(
        "Desktop login success | user=%s | device=%s | ip=%s", user.username, device_label, client_ip(request)
    )
    return JsonResponse({"user": _serialize_user(user, token)})


def desktop_auth_required(view):
    """Resolve `Authorization: Token <key>` into request.user, enforce role, or return JSON 401/403."""

    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Token "):
            return JsonResponse({"error": "Missing or malformed Authorization header."}, status=401)
        key = header[len("Token "):].strip()
        try:
            token = DesktopAuthToken.objects.select_related("user").get(key=key)
        except DesktopAuthToken.DoesNotExist:
            return JsonResponse({"error": "Invalid or revoked token."}, status=401)
        user = token.user
        if not user.is_active:
            return JsonResponse({"error": "This account has been deactivated."}, status=403)
        if not user_has_role(user, *DESKTOP_ROLES):
            return JsonResponse({"error": "This account no longer has Timetable Office access."}, status=403)

        token.touch()
        request.user = user
        request.desktop_token = token

        # The project's audit trail (backup_system) and ActivityLog read the
        # acting user from thread-locals that middleware fills from the
        # *session* user — which is anonymous for a token request. Re-point
        # both at the token's user so desktop edits are attributed properly.
        set_current_user(user)
        try:
            from backup_system.signals import get_audit_context, set_audit_context

            ctx = get_audit_context()
            ctx["user"] = user
            set_audit_context(**ctx)
        except Exception:  # audit attribution must never break the request
            logging.getLogger("app").exception("desktop_sync: could not set audit context")
        return view(request, *args, **kwargs)

    return wrapped


@csrf_exempt  # without this CsrfViewMiddleware 403s the POST and the token is never revoked
@require_POST
@desktop_auth_required
def logout(request):
    request.desktop_token.delete()
    security_logger.info("Desktop logout | user=%s", request.user.username)
    return JsonResponse({"ok": True})


@require_GET
@desktop_auth_required
def me(request):
    return JsonResponse({"user": _serialize_user(request.user)})
