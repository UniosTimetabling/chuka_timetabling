"""
Shared handling for the 5 SR (Special Request) AJAX actions, so every
allocation panel — /cod/ (regular), campus, resit, ODEL, lab — can raise,
view, edit, and cancel SRs against its own allocation model without each
app re-implementing the same ~150 lines of view logic.

Usage from any panel's POST dispatcher, BEFORE that panel's own
ALLOWED_ACTIONS check (or as part of it):

    from special_requests.panel_actions import SR_ACTIONS, handle_sr_action

    if action in SR_ACTIONS:
        return handle_sr_action(
            request, action,
            dept=dept,                       # already resolved + ownership-checked by the caller
            allocation_model=CampusCourseAllocation,
            dept_scoped_qs=lambda d: CampusCourseAllocation.objects.filter(
                Q(department=d) | Q(origin_department=d)
            ),
            panel=SpecialRequest.PANEL_CAMPUSES,
        )

`dept_scoped_qs` must return every allocation row of `allocation_model`
that the given department is allowed to attach/view an SR against — this
mirrors each panel's own "do I own this row" rule (usually
`department=dept`, sometimes also `origin_department=dept` for
cross-department courses).
"""
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404

from .models import SpecialRequest
from .services import (
    create_special_request, get_active_special_requests_for_allocation,
    update_special_request,
)

SR_ACTIONS = {
    "get_special_request_for_allocation",
    "get_courses_for_sr_scope",
    "create_special_request",
    "update_special_request",
    "cancel_special_request",
}


def _serialize_sr(sr):
    return {
        "id": sr.id,
        "scope": sr.scope,
        "scope_display": sr.get_scope_display(),
        "description": sr.description,
        "status": sr.status,
        "status_display": sr.get_status_display(),
        "lecturer_name": sr.lecturer.display_name if sr.lecturer else "",
        "program_name": sr.program.name if sr.program else "",
        "semester": sr.semester,
        "created_at": sr.created_at.strftime("%d %b %Y %H:%M"),
        "courses": [{"code": c} for c in sr.affected_courses] or [{"code": sr.course_code}],
    }


def handle_sr_action(request, action, *, dept, allocation_model, dept_scoped_qs, panel):
    """
    Handles one of the 5 SR_ACTIONS for `allocation_model`, scoped to `dept`.
    Returns a JsonResponse. Callers should only invoke this once `action` is
    confirmed to be in SR_ACTIONS.
    """
    base_qs = dept_scoped_qs(dept)

    def _owned_or_404(pk):
        allocation = get_object_or_404(allocation_model, pk=pk)
        if not base_qs.filter(pk=allocation.pk).exists():
            raise Http404
        return allocation

    if action == "get_special_request_for_allocation":
        allocation_id = request.POST.get("allocation_id")
        allocation = _owned_or_404(allocation_id)
        srs = get_active_special_requests_for_allocation(allocation)
        return JsonResponse({
            "status": "success",
            "has_sr": bool(srs),
            "sr": _serialize_sr(srs[0]) if srs else None,
            "srs": [_serialize_sr(sr) for sr in srs],
        })

    if action == "get_courses_for_sr_scope":
        allocation_id = request.POST.get("allocation_id")
        scope = request.POST.get("scope")
        allocation = _owned_or_404(allocation_id)

        if scope == SpecialRequest.SCOPE_LECTURER:
            if not getattr(allocation, "lecturer_id", None):
                return JsonResponse({"status": "error", "message": "This allocation has no lecturer assigned."}, status=400)
            courses = base_qs.filter(lecturer_id=allocation.lecturer_id).order_by("course_code")
            label = allocation.lecturer.display_name
        elif scope in (SpecialRequest.SCOPE_PROGRAM, SpecialRequest.SCOPE_PROGRAM_COURSES):
            program_id = getattr(allocation, "program_id", None)
            if not program_id:
                return JsonResponse({"status": "error", "message": "This allocation has no program assigned."}, status=400)
            courses = base_qs.filter(program_id=program_id).order_by("course_code")
            label = allocation.program.name
        else:
            return JsonResponse({"status": "error", "message": "Invalid SR scope."}, status=400)

        return JsonResponse({
            "status": "success",
            "label": label,
            "courses": [
                {
                    "id": c.id,
                    "course_code": c.course_code,
                    "course_name": c.course_name,
                    "lecturer_name": c.lecturer.display_name if getattr(c, "lecturer", None) else "Unassigned",
                }
                for c in courses
            ],
        })

    if action == "create_special_request":
        scope = request.POST.get("scope", SpecialRequest.SCOPE_UNIT)
        description = (request.POST.get("description") or "").strip()
        target_ids = request.POST.getlist("target_ids[]") or [request.POST.get("allocation_id")]
        target_ids = [t for t in target_ids if t]

        if scope not in dict(SpecialRequest.SCOPE_CHOICES):
            return JsonResponse({"status": "error", "message": "Invalid SR scope."}, status=400)
        if not description:
            return JsonResponse({"status": "error", "message": "Please describe what needs to be done."}, status=400)
        if len(description) > 2000:
            return JsonResponse({"status": "error", "message": "Description is too long (max 2000 characters)."}, status=400)
        if not target_ids:
            return JsonResponse({"status": "error", "message": "Select at least one course for this SR."}, status=400)

        allocations = list(base_qs.filter(pk__in=target_ids))
        if not allocations:
            return JsonResponse({"status": "error", "message": "None of the selected courses could be found."}, status=404)

        sr = create_special_request(
            target_allocations=allocations, scope=scope, description=description,
            user=request.user, panel=panel,
        )
        return JsonResponse({"status": "success", "sr": _serialize_sr(sr)})

    if action == "update_special_request":
        sr_id = request.POST.get("id")
        description = (request.POST.get("description") or "").strip()
        if not description:
            return JsonResponse({"status": "error", "message": "Please describe what needs to be done."}, status=400)
        sr = get_object_or_404(SpecialRequest, pk=sr_id, department=dept, panel=panel, archived=False)
        sr = update_special_request(sr, description=description)
        return JsonResponse({"status": "success", "sr": _serialize_sr(sr)})

    if action == "cancel_special_request":
        sr_id = request.POST.get("id")
        sr = get_object_or_404(SpecialRequest, pk=sr_id, department=dept, panel=panel)
        sr.archive(reason="Cancelled by COD")
        return JsonResponse({"status": "success", "id": sr_id})

    return JsonResponse({"status": "error", "message": "Unknown SR action."}, status=400)
