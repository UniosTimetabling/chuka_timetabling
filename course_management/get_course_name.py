from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.core.cache import cache
from program_management.models import ProgramCourse


def _check_rate_limit(request, scope: str, limit: int = 30, window: int = 60) -> bool:
    """Simple cache-based rate limiter. Returns True when the caller is over-limit."""
    uid  = request.user.pk if request.user.is_authenticated else "anon"
    xff  = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip   = xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", "0.0.0.0")
    key  = f"rl:{scope}:{uid}:{ip}"
    count = cache.get(key, 0)
    if count >= limit:
        return True
    cache.set(key, count + 1, timeout=window)
    return False


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def get_course_name(request):
    # SECURITY FIX #1 – Rate-limit: 30 requests / minute per user+IP
    if _check_rate_limit(request, "get_course_name"):
        return JsonResponse(
            {"found": False, "error": "Too many requests. Please slow down."},
            status=429,
        )

    code       = request.GET.get("code", "").strip()
    program_id = request.GET.get("program_id")

    # SECURITY FIX #4 – Validate input length to prevent DoS via huge strings
    if len(code) > 30:
        return JsonResponse({"found": False}, status=400)

    # Normalize spaces + uppercase
    normalized_code = code.replace(" ", "").upper()

    if not normalized_code:
        return JsonResponse({"found": False})

    # SECURITY FIX – Validate program_id is a positive integer before using it
    safe_program_id = None
    if program_id:
        try:
            safe_program_id = int(program_id)
            if safe_program_id <= 0:
                safe_program_id = None
        except (ValueError, TypeError):
            safe_program_id = None

    qs = ProgramCourse.objects.all()
    if safe_program_id:
        qs = qs.filter(program_id=safe_program_id)

    # NOTE: The Python-side normalisation loop is intentional here because
    # different databases handle whitespace-stripping in LIKE queries
    # inconsistently.  For large datasets, consider adding a
    # denormalised `course_code_normalized` DB column and filtering on that
    # instead to avoid a full-table scan.
    course = None
    for c in qs:
        if c.course_code.replace(" ", "").upper() == normalized_code:
            course = c
            break

    if course:
        return JsonResponse({"found": True, "name": course.course_name})
    return JsonResponse({"found": False})