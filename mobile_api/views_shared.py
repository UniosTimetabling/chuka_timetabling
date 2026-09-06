"""
mobile_api/views_shared.py
=============================
GET /api/mobile/timetable/shared/?token=&viewerRole=

`token` is the JSON payload the mobile app encoded into its QR code (see
QRShareScreen.js in the mobile app): {"v":1,"role","id","name","regNo"}.
Re-resolves it against live data (never trusts anything from the QR
beyond "which scope to look up") and enforces same-role sharing:
students may only view other students' timetables, lecturers only other
lecturers' — matching the policy the mobile app also checks client-side.
"""
import json

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django_ratelimit.decorators import ratelimit

from .scope import ScopeError, parse_user_id, owner_name_for_scope
from .personal_entries import identity_kwargs_or_none
from .timetable_builder import serialize_timetable


@require_GET
@ratelimit(key="ip", rate="30/m", method="GET", block=True)
def shared_timetable(request):
    raw_token = request.GET.get("token", "")
    viewer_role = request.GET.get("viewerRole", "")

    try:
        payload = json.loads(raw_token)
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid or corrupted QR code."}, status=400)

    token_role = payload.get("role")
    if not token_role or token_role != viewer_role:
        return JsonResponse(
            {"error": "You can only view timetables from your own role (student/lecturer)."},
            status=403,
        )

    try:
        scope = parse_user_id(payload.get("id", ""), token_role)
    except ScopeError as e:
        return JsonResponse({"error": e.message}, status=e.status)

    # Same regNo-based merge as the owner's own fetch (views_timetable.py) —
    # the QR payload already carries it for a student token (see the
    # module docstring), so a shared timetable shows the same added-on
    # courses the owner sees, not just their base curriculum.
    personal_identity = identity_kwargs_or_none(scope, payload.get("regNo"))
    owner_name = owner_name_for_scope(scope)
    return JsonResponse(serialize_timetable(scope, owner_name, personal_identity))
