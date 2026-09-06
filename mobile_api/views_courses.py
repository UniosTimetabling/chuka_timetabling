"""
mobile_api/views_courses.py
==============================
GET  /api/mobile/courses/search/?q=&userId=&role=[&regNo=]  — search, one or many
                                                                comma-separated codes
POST /api/mobile/courses/add/     {userId, role, regNo?, allocationIds: [...]}
POST /api/mobile/courses/remove/  {userId, role, regNo?, allocationId}
GET  /api/mobile/courses/mine/?userId=&role=[&regNo=]        — list personal additions

`q` accepts one or several course codes separated by commas ("coms101,
PHYS 342-a") and is matched with course_search.py's normalized comparison,
so "COMS 101", "coms101", "COMS-101" and "COMS_101" all find the same
thing, and a bare base code ("COMS 101") returns every section/combined
group under it (e.g. all 10 "COMS 101" sections) for the person to pick
theirs from.

`userId`/`role` resolve identity exactly like every other mobile_api
endpoint (scope.parse_user_id). `regNo` is the one extra field a student
must also send — see personal_entries.py's module docstring for why a
student's userId alone isn't enough to key their personal additions.
Lecturers never need it.
"""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from .scope import ScopeError, parse_user_id
from .course_search import search_courses
from .personal_entries import (
    PersonalEntryError,
    identity_kwargs,
    identity_kwargs_or_none,
    list_entries,
    add_entries,
    remove_entry,
)


def _json_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}


@require_GET
@ratelimit(key="ip", rate="30/m", method="GET", block=True)
def courses_search(request):
    q = request.GET.get("q", "")
    if not q.strip():
        return JsonResponse(
            {"error": "Provide at least one course code in 'q' (comma-separate several)."},
            status=400,
        )

    results, not_found = search_courses(q)

    # Best-effort "already added" flag: search itself doesn't require an
    # identity, so a missing/invalid userId+role/regNo just means no
    # flags get set — never an error for the search itself.
    added_ids = set()
    user_id = request.GET.get("userId", "")
    role = request.GET.get("role", "")
    if user_id and role:
        try:
            scope = parse_user_id(user_id, role)
        except ScopeError:
            scope = None
        if scope:
            identity = identity_kwargs_or_none(scope, request.GET.get("regNo"))
            if identity:
                added_ids = set(list_entries(identity).values_list("course_allocation_id", flat=True))

    for r in results:
        r["isAdded"] = r["id"] in added_ids

    return JsonResponse({"results": results, "notFound": not_found})


@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="20/m", method="POST", block=True)
def courses_add(request):
    data = _json_body(request)
    try:
        scope = parse_user_id(data.get("userId", ""), data.get("role", ""))
        identity = identity_kwargs(scope, reg_no=data.get("regNo"))
    except (ScopeError, PersonalEntryError) as e:
        return JsonResponse({"error": e.message}, status=e.status)

    allocation_ids = data.get("allocationIds") or []
    if not isinstance(allocation_ids, list) or not allocation_ids:
        return JsonResponse(
            {"error": "Provide 'allocationIds': a non-empty list of course ids from a search result."},
            status=400,
        )

    added, already_had, invalid = add_entries(identity, allocation_ids)
    return JsonResponse({"added": added, "alreadyAdded": already_had, "invalid": invalid})


@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="30/m", method="POST", block=True)
def courses_remove(request):
    data = _json_body(request)
    try:
        scope = parse_user_id(data.get("userId", ""), data.get("role", ""))
        identity = identity_kwargs(scope, reg_no=data.get("regNo"))
    except (ScopeError, PersonalEntryError) as e:
        return JsonResponse({"error": e.message}, status=e.status)

    allocation_id = data.get("allocationId")
    if not allocation_id:
        return JsonResponse({"error": "Provide 'allocationId'."}, status=400)

    removed = remove_entry(identity, allocation_id)
    return JsonResponse({"removed": removed})


@require_GET
@ratelimit(key="ip", rate="60/m", method="GET", block=True)
def courses_mine(request):
    try:
        scope = parse_user_id(request.GET.get("userId", ""), request.GET.get("role", ""))
        identity = identity_kwargs(scope, reg_no=request.GET.get("regNo"))
    except (ScopeError, PersonalEntryError) as e:
        return JsonResponse({"error": e.message}, status=e.status)

    entries = list(
        list_entries(identity).prefetch_related("course_allocation__combined_groups__allocations")
    )

    # A search result can be added here before it's ever been placed on a
    # venue/time slot (course_search.py doesn't filter for that — see its
    # docstring) — timetable_builder.serialize_timetable only merges an
    # entry into the main timetable once it IS scheduled, so an unscheduled
    # add would otherwise silently show here and nowhere else with no
    # explanation. Compute the same scheduled/not-scheduled check here so
    # the app can say so.
    from timetable.models import Timetable

    candidate_ids_by_entry = {}
    all_candidate_ids = set()
    for e in entries:
        alloc = e.course_allocation
        combined_list = list(alloc.combined_groups.all())
        candidates = (
            set(combined_list[0].allocations.values_list("id", flat=True))
            if combined_list
            else {alloc.id}
        )
        candidate_ids_by_entry[e.id] = candidates
        all_candidate_ids |= candidates

    scheduled_alloc_ids = set(
        Timetable.objects.filter(
            course_allocation_id__in=all_candidate_ids,
            venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
        ).values_list("course_allocation_id", flat=True)
    )

    return JsonResponse({
        "courses": [
            {
                "allocationId": e.course_allocation_id,
                "courseCode": e.course_allocation.course_code,
                "courseName": e.course_allocation.course_name,
                "lecturer": getattr(e.course_allocation.lecturer, "name", None) or "Unassigned",
                "addedAt": e.added_at.isoformat(),
                "scheduled": bool(candidate_ids_by_entry[e.id] & scheduled_alloc_ids),
            }
            for e in entries
        ]
    })
