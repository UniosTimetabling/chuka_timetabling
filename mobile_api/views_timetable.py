"""
mobile_api/views_timetable.py
================================
GET /api/mobile/timetable/version/?userId=&role=[&regNo=]  — cheap staleness check
GET /api/mobile/timetable/?userId=&role=[&regNo=]          — full timetable JSON

`regNo` is optional and only meaningful for role=student — it's what lets
a student's own PersonalCourseEntry additions (courses they searched for
and added on top of their program/year curriculum — see course_search.py
and personal_entries.py) merge into the timetable below, tagged
"personal": true. Lecturers don't need it: their identity is already
individual (userId is lec:<id>), so their additions merge automatically.
Omitting regNo for a student just means their added-on courses are left
out — the base curriculum timetable still returns normally.
"""
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django_ratelimit.decorators import ratelimit

from .scope import ScopeError, parse_user_id, owner_name_for_scope
from .personal_entries import identity_kwargs_or_none
from .timetable_builder import compute_version, serialize_timetable
from django.utils import timezone


@require_GET
@ratelimit(key="ip", rate="90/m", method="GET", block=True)
def timetable_version(request):
    try:
        scope = parse_user_id(request.GET.get("userId", ""), request.GET.get("role", ""))
    except ScopeError as e:
        return JsonResponse({"error": e.message}, status=e.status)

    personal_identity = identity_kwargs_or_none(scope, request.GET.get("regNo"))
    return JsonResponse({
        "version": compute_version(scope, personal_identity),
        "lastUpdated": timezone.now().isoformat(),
    })


@require_GET
@ratelimit(key="ip", rate="30/m", method="GET", block=True)
def timetable_full(request):
    try:
        scope = parse_user_id(request.GET.get("userId", ""), request.GET.get("role", ""))
    except ScopeError as e:
        return JsonResponse({"error": e.message}, status=e.status)

    personal_identity = identity_kwargs_or_none(scope, request.GET.get("regNo"))
    owner_name = owner_name_for_scope(scope)
    return JsonResponse(serialize_timetable(scope, owner_name, personal_identity))
