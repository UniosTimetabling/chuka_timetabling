"""
mobile_api/views_exam_timetable.py
=====================================
GET /api/mobile/exam-timetable/version/?userId=&role=  — cheap staleness check
GET /api/mobile/exam-timetable/?userId=&role=          — full exam timetable JSON

Same design as views_timetable.py, pointed at timetable.models.ExamTimetable
via the exam_* helpers in timetable_builder.py — a separate version/full
pair so the mobile app can poll and refresh the exam schedule independently
of the regular one (they change on different schedules and the app shows
them as separate tabs).
"""
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django_ratelimit.decorators import ratelimit

from .scope import ScopeError, parse_user_id, owner_name_for_scope
from .timetable_builder import exam_compute_version, exam_serialize_timetable
from django.utils import timezone


@require_GET
@ratelimit(key="ip", rate="90/m", method="GET", block=True)
def exam_timetable_version(request):
    try:
        scope = parse_user_id(request.GET.get("userId", ""), request.GET.get("role", ""))
    except ScopeError as e:
        return JsonResponse({"error": e.message}, status=e.status)

    return JsonResponse({
        "version": exam_compute_version(scope),
        "lastUpdated": timezone.now().isoformat(),
    })


@require_GET
@ratelimit(key="ip", rate="30/m", method="GET", block=True)
def exam_timetable_full(request):
    try:
        scope = parse_user_id(request.GET.get("userId", ""), request.GET.get("role", ""))
    except ScopeError as e:
        return JsonResponse({"error": e.message}, status=e.status)

    owner_name = owner_name_for_scope(scope)
    return JsonResponse(exam_serialize_timetable(scope, owner_name))
