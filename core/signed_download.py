"""
core/signed_download.py
───────────────────────
Lightweight signed-URL helper for public timetable downloads.

Why this exists
───────────────
Published timetable PDFs should be downloadable by anyone who has the
link (students, parents, public notice boards) without requiring a login.
However, bare media-file URLs expose the storage path and bypass any
future per-file access logic.

This module wraps Django's built-in TimestampSigner to produce short-lived
HMAC tokens that are embedded in download links by the published_timetables
view and verified by each download endpoint.

Token anatomy
─────────────
    ?token=<signed-payload>
    payload = "<resource_type>:<pk>"   e.g. "regular:7", "resit:3", "odel_class:12"

Token expiry
────────────
DOWNLOAD_TOKEN_MAX_AGE_SECONDS (default 3 600 s = 1 hour).
Extend it in settings.py if needed:
    DOWNLOAD_TOKEN_MAX_AGE_SECONDS = 7200

Security properties
───────────────────
• HMAC-SHA256 signed with Django's SECRET_KEY — unforgeable without the key.
• Timestamp embedded — tokens expire after MAX_AGE.
• Resource type + pk bound — a token for "resit:3" cannot be replayed
  against "regular:3".
• No session, no cookie, no login required on the download endpoint.
• The download endpoint itself is rate-limitable via standard middleware
  if you later add django-ratelimit or similar.

Usage
─────
    # In a view that renders download links:
    from core.signed_download import make_download_token
    token = make_download_token("regular", doc.pk)
    url = f"{reverse('download_latest_regular')}?token={token}"

    # In the download endpoint:
    from core.signed_download import validate_download_token
    resource_type, pk = validate_download_token(request.GET.get("token"), "regular")
    if pk is None:
        return HttpResponse("Invalid or expired download link.", status=403)
"""

from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.conf import settings

# Default 1-hour expiry; override in settings.py if desired
_MAX_AGE = getattr(settings, "DOWNLOAD_TOKEN_MAX_AGE_SECONDS", 3600)

_signer = TimestampSigner(salt="timetable-public-download")


def make_download_token(resource_type: str, pk: int) -> str:
    """
    Return a signed token string for the given resource.

    Args:
        resource_type: Logical name of the resource, e.g. "regular", "exam",
                       "resit", "odel_class", "odel_exam",
                       "campus_class", "campus_exam".
        pk:            Primary key of the PDF record.

    Returns:
        URL-safe signed string to embed as ``?token=<value>``.
    """
    payload = f"{resource_type}:{pk}"
    return _signer.sign(payload)


def validate_download_token(token: str | None, expected_type: str):
    """
    Validate a signed download token.

    Args:
        token:         The raw ``?token=`` value from the request.
        expected_type: The resource type this endpoint handles.

    Returns:
        (resource_type, pk:int) on success.
        (None, None)            on any failure (bad sig, expired, type mismatch).
    """
    if not token:
        return None, None
    try:
        payload = _signer.unsign(token, max_age=_MAX_AGE)
        parts = payload.split(":", 1)
        if len(parts) != 2:
            return None, None
        rtype, pk_str = parts
        if rtype != expected_type:
            return None, None
        return rtype, int(pk_str)
    except (BadSignature, SignatureExpired, ValueError):
        return None, None
