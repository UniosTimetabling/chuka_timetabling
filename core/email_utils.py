"""
core/email_utils.py
===================
Shared utility for sending branded HTML emails.
Pulls university details from SiteSettings (with safe fallbacks).
"""

import logging
from datetime import date

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _get_site_context(request=None):
    """
    Build the common context injected into every email template.
    Falls back gracefully if SiteSettings row doesn't exist yet.
    """
    ctx = {
        "university_name": getattr(settings, "FALLBACK_UNI_NAME", "University Timetabling System"),
        "tagline": "Directorate of Timetabling & Examinations",
        "logo_url": None,
        "contact_email": getattr(settings, "SUPPORT_EMAIL", ""),
        "contact_phone": "",
        "contact_location": "",
        "website_url": "",
        "support_email": getattr(settings, "SUPPORT_EMAIL", ""),
        "year": date.today().year,
    }
    try:
        from core.models import SiteSettings
        s = SiteSettings.get_settings()
        ctx["university_name"] = s.university_name or ctx["university_name"]
        ctx["tagline"] = s.tagline or ctx["tagline"]
        ctx["contact_email"] = s.contact_email or ctx["contact_email"]
        ctx["contact_phone"] = s.contact_phone or ""
        ctx["contact_location"] = s.contact_location or ""
        ctx["website_url"] = s.website_url or ""
        ctx["support_email"] = s.contact_email or ctx["support_email"]

        # Build absolute logo URL if a request is available and logo exists
        if s.logo and request:
            ctx["logo_url"] = request.build_absolute_uri(s.logo.url)
        elif s.logo:
            base = getattr(settings, "SITE_URL", "").rstrip("/")
            ctx["logo_url"] = f"{base}{s.logo.url}" if base else None
    except Exception as exc:
        logger.warning(f"email_utils: could not load SiteSettings — {exc}")

    return ctx


def send_html_email(
    subject,
    template_name,
    extra_context,
    recipient_list,
    request=None,
    from_email=None,
):
    """
    Render an HTML email template and send it.
    Falls back to a plain-text summary if the template fails.

    Args:
        subject (str): Email subject line.
        template_name (str): Template path, e.g. "emails/password_reset_request.html"
        extra_context (dict): Variables merged into the template context.
        recipient_list (list[str]): List of recipient email addresses.
        request: Django request object (used to build absolute URLs).
        from_email (str|None): Override sender address; defaults to DEFAULT_FROM_EMAIL.

    Returns:
        (bool, str): (success, message)
    """
    if not recipient_list:
        return False, "No recipients provided."

    ctx = _get_site_context(request)
    ctx.update(extra_context)

    from_email = from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@example.com")
    subject = f"[{ctx['university_name']}] {subject}"

    try:
        html_body = render_to_string(template_name, ctx)
        # Build a minimal plain-text fallback
        plain_body = (
            f"{ctx.get('greeting', '')}\n\n"
            + extra_context.get("plain_text_fallback", "")
            + f"\n\n— {ctx['university_name']} | {ctx['contact_email']}"
        )

        msg = EmailMultiAlternatives(subject, plain_body, from_email, recipient_list)
        msg.attach_alternative(html_body, "text/html")
        msg.send()
        return True, f"Email sent to {', '.join(recipient_list)}"
    except Exception as exc:
        logger.error(f"send_html_email failed (template={template_name}): {exc}")
        return False, str(exc)
