"""
mobile_api/views_auth.py
==========================
POST /api/mobile/auth/student/  { regNo }
POST /api/mobile/auth/lecturer/ { name }

Both are public and rate-limited, matching the existing self-service
pattern in export_import.program_year_pdf_views.student_timetable_by_registration
— a student/lecturer proves who they are just by knowing their own
registration number / name, same as the existing "download my timetable"
box. No password, no Django User account required.
"""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .scope import ScopeError, resolve_student_scope, find_lecturer_matches, user_id_for_student, user_id_for_lecturer
from .timetable_builder import compute_version, exam_compute_version


def _json_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}


# These two are public, stateless JSON endpoints hit directly by the mobile
# app (no Django session/cookie, so there's no CSRF token for it to send) —
# same trust model as the existing "download my timetable by reg number"
# self-service box. csrf_exempt is safe here because nothing below relies on
# session auth to decide what the caller can see; identity comes entirely
# from the regNo/name in the body, same as that existing feature.
@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="20/m", method="POST", block=True)
def student_login(request):
    data = _json_body(request)
    reg_no = (data.get("regNo") or "").strip()
    program_id = data.get("programId")
    department_id = data.get("departmentId")
    if not reg_no:
        return JsonResponse({"error": "Please provide your registration number."}, status=400)

    try:
        scope = resolve_student_scope(reg_no, program_id=program_id, department_id=department_id)
    except ScopeError as e:
        return JsonResponse({"error": e.message, **e.extra}, status=e.status)

    return JsonResponse({
        "user": {
            "id": user_id_for_student(scope),
            # There's no individual student record in this system — see
            # scope.py — so "name" is the cohort label, and regNo is what
            # the app displays alongside it for a personal touch.
            "name": f"{scope['program_name']} — Year {scope['year']}",
            "regNo": scope["reg_no"],
            "role": "student",
        },
        "timetableVersion": compute_version(scope),
        "examTimetableVersion": exam_compute_version(scope),
    })


@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="20/m", method="POST", block=True)
def lecturer_login(request):
    data = _json_body(request)
    name = (data.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "Please provide your name."}, status=400)

    matches = find_lecturer_matches(name)

    if not matches:
        return JsonResponse({"error": f"No lecturer found matching '{name}'."}, status=404)

    if len(matches) > 1:
        return JsonResponse({
            "error": "Multiple lecturers match that name — please be more specific.",
            "matches": [
                {
                    "id": user_id_for_lecturer(m.id),
                    "name": m.name,
                    "department": m.department.name if m.department else None,
                }
                for m in matches
            ],
        }, status=300)

    lecturer = matches[0]
    scope = {"role": "lecturer", "lecturer_id": lecturer.id}

    return JsonResponse({
        "user": {
            "id": user_id_for_lecturer(lecturer.id),
            "name": lecturer.name,
            "role": "lecturer",
        },
        "timetableVersion": compute_version(scope),
        "examTimetableVersion": exam_compute_version(scope),
    })
