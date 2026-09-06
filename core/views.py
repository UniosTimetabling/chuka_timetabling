from django.shortcuts import render
# ---------- LOGIN ----------
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect
from django.views.decorators.csrf import requires_csrf_token
from django.views.decorators.csrf import requires_csrf_token
from django.shortcuts import redirect
from django.http import JsonResponse
from django_ratelimit.decorators import ratelimit
import logging

from django.http import HttpResponse
from django.db import connection


def health_check(request):
    """
    Liveness/readiness endpoint for docker-compose healthchecks and load
    balancers. Deliberately has no auth and does minimal work: a trivial
    DB query so a broken DB connection is correctly reported as unhealthy,
    not just "the process is running".
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return HttpResponse("unhealthy", status=503)
    return HttpResponse("ok", status=200)


security_logger = logging.getLogger("security")
errors_logger = logging.getLogger("django.request")


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")

@requires_csrf_token
def csrf_failure(request, reason=""):
    security_logger.warning(
        "CSRF failure on %s %s | ip=%s | reason=%s",
        request.method, request.get_full_path(), _client_ip(request), reason,
    )
    messages.error(request, "Your session expired. Please log in again.")
    return redirect("login")

# ── API throttling ──────────────────────────────────────────────────────────
# Renders when any @ratelimit-protected view trips its limit (see settings.RATELIMIT_VIEW).
def ratelimited_error(request, exception=None):
    security_logger.warning(
        "Rate limit exceeded on %s %s | ip=%s",
        request.method, request.get_full_path(), _client_ip(request),
    )
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", ""):
        return JsonResponse(
            {"ok": False, "success": False, "error": "Too many requests. Please slow down and try again shortly."},
            status=429,
        )
    return render(
        request,
        "core/error.html",
        {
            "code": "429",
            "title": "Too Many Requests",
            "msg": "You've made too many requests in a short time. Please wait a moment and try again.",
        },
        status=429,
    )

@requires_csrf_token
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username", "").lower()
        password = request.POST.get("password", "")

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            security_logger.info(
                "Login success | user=%s | ip=%s",
                user.username, _client_ip(request),
            )
            messages.success(request, f"Welcome back, {user.username}!")

            # ✅ Superuser redirection
            if user.is_superuser:
                return redirect("sudo_homepage")

            # ✅ Role-based redirect mapping
            # Keyed by CANONICAL role values from core.rbac.Role — the same
            # source of truth used by ensure_default_groups() and every
            # @allowed_roles(...) check across the app. Do not hardcode a
            # second copy of role names here again; add new roles to
            # core/rbac.py instead.
            from core.rbac import get_user_roles, Role

            group_redirects = {
                Role.DVC: "dvc_panel",
                Role.DVC_ADMIN: "dvc_panel",
                Role.DEAN: "deans_panel",
                Role.DEAN_ADMIN: "deans_panel",
                Role.COD: "cod_panel",
                Role.COD_ADMIN: "cod_panel",
                Role.DIRECTOR: "timetable_dashboard",
                Role.TIMETABLE_ADMIN: "timetable_dashboard",
                Role.TIMETABLER: "timetable_dashboard",
                Role.DEPARTMENT_USERS: "user_dashboard",
                Role.COT: "cot_exam_timetable",
                Role.UTILITY: "meeting_venues_panel",
                Role.ACADEMIC_AFFAIRS: "course_allocations_page",
            }

            # ✅ Determine redirect target based on canonical role
            # (resolves legacy/aliased group names automatically)
            user_roles = get_user_roles(user)
            for role, url_name in group_redirects.items():
                if role in user_roles:
                    return redirect(url_name)

            # ✅ Fallback redirect
            return redirect("user_dashboard")

        else:
            security_logger.warning(
                "Login failure | attempted_user=%s | ip=%s",
                username, _client_ip(request),
            )
            messages.error(request, "Invalid username or password.")

    return render(request, "core/login.html") 

def logout_view(request):
    if request.user.is_authenticated:
        security_logger.info(
            "Logout | user=%s | ip=%s",
            request.user.username, _client_ip(request),
        )
    logout(request)
    messages.success(request, "✅ You have successfully logged out!")
    return redirect("login")


def universal_error(request, exception=None):
    """
    Single view for ALL errors. Also registered as handler404/handler403/handler500
    in university_timetable_system/urls.py, so every framework-level error path
    funnels through here — log it for the errors.log monitoring feed.
    """
    from django.core.exceptions import PermissionDenied
    from django.http import Http404

    # Determine the real status code from the exception type rather than
    # hardcoding 500 for every handler — otherwise 403s (permission denied)
    # and 404s get reported to the client as "500 Internal Server Error".
    if isinstance(exception, PermissionDenied):
        default_code, default_title, default_msg, status = (
            "403", "Permission Denied",
            "You don't have permission to access this page.", 403,
        )
    elif isinstance(exception, Http404):
        default_code, default_title, default_msg, status = (
            "404", "Not Found",
            "The page you're looking for doesn't exist.", 404,
        )
    else:
        default_code, default_title, default_msg, status = (
            "Oops", "Unexpected Error",
            "Our support team has been notified.", 500,
        )

    code = request.GET.get("code", default_code)
    title = request.GET.get("title", default_title)
    msg = request.GET.get("msg", default_msg)

    errors_logger.error(
        "universal_error triggered on %s %s | code=%s | exception=%s",
        request.method, request.get_full_path(), code,
        exception.__class__.__name__ if exception else "n/a",
        exc_info=exception is not None,
    )

    context = {
        "code": code,
        "title": title,
        "msg": msg,
    }

    return render(request, "core/error.html", context, status=status)

# ─────────────────────────────────────────────
# PASSWORD RESET VIEWS
# ─────────────────────────────────────────────
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.conf import settings as django_settings
from django.urls import reverse
import logging

from core.email_utils import send_html_email

logger = logging.getLogger(__name__)


def password_reset_request(request):
    """Step 1 – user enters email; we send a reset link."""
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()

        # Always show "email sent" to avoid user enumeration
        try:
            user = User.objects.get(email__iexact=email)
            uid   = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = request.build_absolute_uri(
                reverse("password_reset_confirm_view", args=[uid, token])
            )

            user_name = user.get_full_name() or user.username
            ok, msg = send_html_email(
                subject="Password Reset Request",
                template_name="emails/password_reset_request.html",
                extra_context={
                    "user_name": user_name,
                    "reset_url": reset_url,
                    "plain_text_fallback": (
                        f"You requested a password reset.\n\n"
                        f"Reset link: {reset_url}\n\n"
                        f"This link is valid for 24 hours.\n"
                        f"If you did not request this, please ignore this email."
                    ),
                },
                recipient_list=[user.email],
                request=request,
            )
            if not ok:
                logger.error(f"Password reset email failed for {email}: {msg}")
                support = getattr(django_settings, "SUPPORT_EMAIL", "support@example.com")
                messages.error(
                    request,
                    f"Failed to send email. Please contact admin at {support}."
                )
                return render(request, "core/password_reset_request.html")
        except User.DoesNotExist:
            pass  # Don't reveal whether email exists

        request.session["reset_email_sent_to"] = email
        return redirect("password_reset_done_view")

    return render(request, "core/password_reset_request.html")


def password_reset_done_view(request):
    """Step 2 – confirmation page after email sent."""
    email = request.session.pop("reset_email_sent_to", "your registered email")
    return render(request, "core/password_reset_done.html", {"email": email})


def password_reset_confirm_view(request, uidb64, token):
    """Step 3 – user clicks link from email; enters new password."""
    try:
        uid  = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    valid = user is not None and default_token_generator.check_token(user, token)

    if request.method == "POST" and valid:
        pw1 = request.POST.get("new_password1", "")
        pw2 = request.POST.get("new_password2", "")
        if not pw1 or len(pw1) < 8:
            messages.error(request, "Password must be at least 8 characters.")
        elif pw1 != pw2:
            messages.error(request, "Passwords do not match.")
        else:
            user.set_password(pw1)
            user.save()
            return redirect("password_reset_success_view")

    ctx = {
        "valid_token": valid,
        "token": f"{uidb64}/{token}",
        "username": user.username if user else "",
    }
    return render(request, "core/password_reset_confirm.html", ctx)


def password_reset_success_view(request):
    """Step 4 – success page after password changed."""
    return render(request, "core/password_reset_success.html")


# =====================================================
# AUTOSCHEDULER CONSTRAINT CONFIRMATION ENDPOINT
#
# Shared by the regular timetable autoscheduler today; the exam
# autoscheduler (or resit/ODEL) can call the same endpoint with
# ?scheduler_type=exam once it's wired up to core.scheduling_constraints.
# =====================================================

from core.rbac import allowed_roles, Role


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def scheduler_constraint_summary(request):
    """
    GET  -> JSON list of constraint categories + rule counts + enabled
            state, for the "are you sure?" pre-run confirmation screen.
    """
    from core.scheduling_constraints import get_constraint_summary
    scheduler_type = request.GET.get("scheduler_type", "regular")
    return JsonResponse({
        "status": "success",
        "constraints": get_constraint_summary(scheduler_type),
    })
