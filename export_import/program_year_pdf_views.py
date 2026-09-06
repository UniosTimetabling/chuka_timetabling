"""
export_import/program_year_pdf_views.py
=========================================

Endpoints that hand back a timetable PDF for a (department, program,
year) scope, backed by the cache in export_import.program_year_pdf_cache
(GeneratedTimetablePDF). Because the cache is warm almost all the time
(background sweep + invalidate-on-change — see pdf_cache_signals.py and
sync_views.receive_sync), these are normally just "read a small file off
disk and hand it back", not "build a PDF" — which matters, because this
is expected to take heavy, bursty, mostly-anonymous traffic from a large
student body on a small server, hence the rate limiting below.

Endpoints:
  1. GET /program-timetable/<department_id>/<program_id>/<year>/<type>/
     Staff-facing, id-based, login required. Shown inline (unchanged
     from before).

  2. GET /timetable/program/<program_code>/<year>/<type>/
     Public. Program resolved via ProgramCode (e.g. "EB1"). Downloads
     as an attachment.

  3. GET /timetable/program-name/<program_name>/<year>/<type>/
     Public. Program resolved by name. Downloads as an attachment.

  4. GET /timetable/student/?reg=<registration_number>&type=<class|exam>
     Public, self-service. `reg` is a full registration number such as
     "EB1/66791/23" — the program code and intake year are parsed out
     of it (export_import.student_reg_lookup), the year of study is
     computed from the intake year, and the matching PDF is downloaded.
     This is the one meant to be linked from a "download my timetable"
     box on a student-facing page, so it's kept unauthenticated and
     rate-limited more tightly than the others.

`type` defaults to "class" wherever it's optional; "exam" gets the exam
timetable instead.
"""
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponse, Http404
from django.views.decorators.http import require_GET

from export_import import program_year_pdf_cache
from export_import import student_reg_lookup

VALID_TYPES = ("class", "exam")


# ─────────────────────────────────────────────────────────────
#  Rate limiting — simple, cache-backed, IP-scoped. Kept local to this
#  module rather than shared with the staff-side rate limiter in
#  course_management, since these limits are deliberately much looser
#  and tuned for public/anonymous, high-volume student traffic.
# ─────────────────────────────────────────────────────────────

def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "0.0.0.0")


def _rate_limited(request, scope, limit, window):
    """True if this IP has exceeded `limit` requests to `scope` within
    the last `window` seconds."""
    key = f"rl:pubpdf:{scope}:{_client_ip(request)}"
    count = cache.get(key, 0)
    if count >= limit:
        return True
    # First hit in the window sets the expiry; every hit after just
    # increments, so the window is a fixed rolling bucket, not extended
    # on every request.
    if count == 0:
        cache.set(key, 1, timeout=window)
    else:
        cache.incr(key)
    return False


def _too_many_requests():
    resp = HttpResponse(
        "Too many requests — please wait a moment and try again.",
        status=429, content_type="text/plain",
    )
    resp["Retry-After"] = "30"
    return resp


# ─────────────────────────────────────────────────────────────
#  Shared PDF hand-off
# ─────────────────────────────────────────────────────────────

def _serve(department_id, program_id, year, timetable_type, *, download, filename_hint=None):
    """Builds the actual HTTP response for a resolved scope. Returns
    an HttpResponse; raises Http404 for a scope that genuinely doesn't
    exist so callers don't have to duplicate that check."""
    row = program_year_pdf_cache.get_or_build(department_id, program_id, year, timetable_type)
    if row is None:
        raise Http404("No timetable found for that program and year.")

    if not row.is_ready or not row.pdf_file:
        return HttpResponse(
            "This timetable couldn't be generated right now. Please try again shortly.",
            status=503, content_type="text/plain",
        )

    try:
        data = row.pdf_file.read()
    except Exception:
        raise Http404("The cached PDF is missing on disk — it will regenerate on the next request.")

    filename = filename_hint or row.pdf_file.name.rsplit("/", 1)[-1]
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    response = HttpResponse(data, content_type="application/pdf")
    disposition = "attachment" if download else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


# ─────────────────────────────────────────────────────────────
#  1. Staff-facing, id-based (unchanged behaviour)
# ─────────────────────────────────────────────────────────────

@login_required
@require_GET
def program_year_pdf(request, department_id, program_id, year, timetable_type):
    if timetable_type not in VALID_TYPES:
        raise Http404("Unknown timetable type.")
    return _serve(department_id, program_id, year, timetable_type, download=False)


# ─────────────────────────────────────────────────────────────
#  2. Public, by program code + year
# ─────────────────────────────────────────────────────────────

@require_GET
def program_timetable_by_code(request, program_code, year, timetable_type):
    if _rate_limited(request, "by_code", limit=30, window=60):
        return _too_many_requests()
    if timetable_type not in VALID_TYPES:
        raise Http404("Unknown timetable type.")

    program = student_reg_lookup.resolve_program_by_code(program_code)
    if not program:
        raise Http404(f"Unknown program code '{program_code}'.")

    filename = f"{program_code.upper()}_Year{year}_{timetable_type}_timetable.pdf"
    return _serve(program.department_id, program.id, year, timetable_type,
                  download=True, filename_hint=filename)


# ─────────────────────────────────────────────────────────────
#  3. Public, by program name + year
# ─────────────────────────────────────────────────────────────

@require_GET
def program_timetable_by_name(request, program_name, year, timetable_type):
    if _rate_limited(request, "by_name", limit=30, window=60):
        return _too_many_requests()
    if timetable_type not in VALID_TYPES:
        raise Http404("Unknown timetable type.")

    program = student_reg_lookup.resolve_program_by_name(program_name)
    if not program:
        raise Http404(f"Unknown or ambiguous program name '{program_name}'.")

    safe_name = program.name.replace(" ", "_").replace("/", "-")
    filename = f"{safe_name}_Year{year}_{timetable_type}_timetable.pdf"
    return _serve(program.department_id, program.id, year, timetable_type,
                  download=True, filename_hint=filename)


# ─────────────────────────────────────────────────────────────
#  4. Public, self-service: full registration number
# ─────────────────────────────────────────────────────────────

@require_GET
def student_timetable_by_registration(request):
    # Tighter limit than the two above — this is the one meant to sit
    # behind a "download my timetable" box any student can hit.
    if _rate_limited(request, "student_lookup", limit=12, window=60):
        return _too_many_requests()

    reg = (request.GET.get("reg") or request.GET.get("admission_number") or "").strip()
    timetable_type = (request.GET.get("type") or "class").strip().lower()
    if timetable_type not in VALID_TYPES:
        timetable_type = "class"

    if not reg:
        return HttpResponse(
            "Provide your registration number, e.g. ?reg=EB1/66791/23",
            status=400, content_type="text/plain",
        )

    parsed = student_reg_lookup.parse_registration_number(reg)
    if not parsed:
        return HttpResponse(
            "That doesn't look like a registration number. Expected format: "
            "PROGRAMCODE/ADMISSIONNO/INTAKEYEAR, e.g. EB1/66791/23.",
            status=400, content_type="text/plain",
        )
    program_code, intake_year = parsed

    year = student_reg_lookup.year_of_study_from_intake(intake_year)
    if year < 1 or year > 8:
        return HttpResponse(
            f"'{reg}' doesn't map to a current year of study — please check the "
            "intake year at the end of your registration number.",
            status=404, content_type="text/plain",
        )

    program = student_reg_lookup.resolve_program_by_code(program_code)
    if not program:
        return HttpResponse(
            f"Unknown program code '{program_code}' in '{reg}'.",
            status=404, content_type="text/plain",
        )

    filename = f"{program_code}_Year{year}_{timetable_type}_timetable.pdf"
    return _serve(program.department_id, program.id, year, timetable_type,
                  download=True, filename_hint=filename)
