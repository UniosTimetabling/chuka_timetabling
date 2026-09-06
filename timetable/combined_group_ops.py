"""
Right-click context-menu actions on the timetable panel for managing
Combined Course Groups directly from the grid:

  - Rename Course…            (course NOT in a combined group)
  - Add to Combined Group…    (course NOT in a combined group)
  - Rename Combined Group…    (course IS in a combined group)
  - Move to Another Group…    (course IS in a combined group)
  - Remove from Combined Group…  (course IS in a combined group)

These mirror the equivalent COD Panel actions (course_management.cod_panel
.CombinedCourseGroupService) but are permission-scoped by ROLE
(SUDO/DIRECTOR/TIMETABLE_ADMIN), not by department ownership — the
timetable panel isn't a department-scoped view, so
core.rbac.allowed_roles is used instead of cod_panel's
_assert_owns_department.

The actual add/remove reconciliation logic (folding a course onto the
group's shared slot, finding it a free slot when it leaves, etc.) is
delegated to cod_panel's already-tested
_add_allocation_to_group_core / _remove_allocation_from_group_core via a
LOCAL import inside each view — not a module-level import — so the
timetable panel's hot request path (page load, grid refresh) never pays
the cost of cod_panel's heavier dependencies (e.g. weasyprint).
"""
import re
from typing import Optional, Tuple

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from core.rbac import allowed_roles, Role
from course_allocation.models import CourseAllocation, CombinedCourseGroup
from program_management.code_utils import canonical_course_key


# -----------------------------------------------------------------------
# Local, lightweight duplicate of course_management.cod_panel.strip_group_
# suffix — same behaviour, kept separate so this module has no import-time
# dependency on cod_panel (see module docstring).
# -----------------------------------------------------------------------
def _strip_group_suffix(code: str) -> Tuple[str, Optional[str]]:
    if not code:
        return code, None
    code = code.strip()
    m = re.match(r"^(.*?\d+)\s*\(\s*([A-Za-z]+)\s*\)\s*$", code, re.I)
    if m:
        base = m.group(1).strip()
        letter = m.group(2).upper()
        if re.search(r"\d", base):
            return base, letter
    m = re.match(r"^(.*?\d+)\s*[-/_]?\s*([A-Za-z]+)\s*$", code, re.I)
    if m:
        base = m.group(1).strip()
        letter = m.group(2).upper()
        if re.search(r"\d", base):
            return base, letter
    return code, None


def _base_key(course_code: str) -> str:
    """Canonical matching key for a course's base code, ignoring any
    trailing section/group-letter suffix, so 'COSC 471-A', 'COSC471(B)'
    and 'COSC 471' all resolve to the same key."""
    base, _ = _strip_group_suffix(course_code or "")
    return canonical_course_key(base)


def _current_group(allocation):
    return allocation.combined_groups.first()


def _candidate_groups(allocation, exclude_group_id=None):
    """Existing CombinedCourseGroup rows that share this allocation's base
    course code — candidates for 'Add to Combined Group…' / 'Move to
    Another Group…'."""
    key = _base_key(allocation.course_code)
    qs = CombinedCourseGroup.objects.select_related("department")
    if exclude_group_id:
        qs = qs.exclude(pk=exclude_group_id)
    return [g for g in qs if canonical_course_key(g.base_course_code) == key]


# -----------------------------------------------------------------------
# Read-only lookup: powers the context menu's show/hide logic and the
# Add/Move picker lists.
# -----------------------------------------------------------------------
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def combined_group_options_api(request):
    allocation_id = request.POST.get("allocation_id") or request.GET.get("allocation_id")
    if not allocation_id:
        return JsonResponse({"status": "error", "message": "allocation_id is required."}, status=400)

    allocation = get_object_or_404(CourseAllocation, pk=allocation_id)
    group = _current_group(allocation)

    candidates = _candidate_groups(allocation, exclude_group_id=group.id if group else None)

    return JsonResponse({
        "status": "success",
        "allocation": {"id": allocation.id, "course_code": allocation.course_code},
        "in_group": bool(group),
        "group": {
            "id": group.id,
            "group_code": group.group_code,
            "member_count": group.allocations.count(),
        } if group else None,
        "candidate_groups": [
            {"id": g.id, "group_code": g.group_code, "member_count": g.allocations.count()}
            for g in candidates
        ],
    })


# -----------------------------------------------------------------------
# Rename Course…  (only while NOT in a combined group)
# -----------------------------------------------------------------------
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def rename_course_api(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required."}, status=405)

    allocation_id = request.POST.get("allocation_id")
    new_code = (request.POST.get("new_code") or "").strip()

    if not allocation_id:
        return JsonResponse({"status": "error", "message": "allocation_id is required."}, status=400)
    if not new_code:
        return JsonResponse({"status": "error", "message": "Course code cannot be empty."}, status=400)
    if len(new_code) > 100:
        return JsonResponse({"status": "error", "message": "Course code must be 100 characters or fewer."}, status=400)

    allocation = get_object_or_404(CourseAllocation, pk=allocation_id)

    if allocation.combined_groups.exists():
        return JsonResponse({
            "status": "error",
            "message": "This course is part of a Combined Course Group — rename the group instead.",
        }, status=400)

    old_code = allocation.course_code
    allocation.course_code = new_code
    allocation.save(update_fields=["course_code"])

    try:
        from audit_management.models import AuditLog
        AuditLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            action="RENAME_COURSE",
            model_name="CourseAllocation",
            object_id=allocation.id,
            details={"old_course_code": old_code, "new_course_code": new_code},
        )
    except ImportError:
        pass

    return JsonResponse({
        "status": "success",
        "message": f'Renamed "{old_code}" to "{new_code}".',
        "allocation_id": allocation.id,
        "course_code": allocation.course_code,
    })


# -----------------------------------------------------------------------
# Rename Combined Group…
# -----------------------------------------------------------------------
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def rename_combined_group_api(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required."}, status=405)

    group_id = request.POST.get("group_id")
    new_name = (request.POST.get("new_name") or "").strip()

    if not group_id:
        return JsonResponse({"status": "error", "message": "group_id is required."}, status=400)
    if not new_name:
        return JsonResponse({"status": "error", "message": "Group name cannot be empty."}, status=400)
    if len(new_name) > 50:
        return JsonResponse({"status": "error", "message": "Group name must be 50 characters or fewer."}, status=400)

    group = get_object_or_404(CombinedCourseGroup, pk=group_id)

    if CombinedCourseGroup.objects.filter(group_code=new_name).exclude(pk=group.id).exists():
        return JsonResponse(
            {"status": "error", "message": f'A combined group named "{new_name}" already exists.'},
            status=400,
        )

    old_name = group.group_code
    group.group_code = new_name
    group.save(update_fields=["group_code", "updated_at"])

    try:
        from audit_management.models import AuditLog
        AuditLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            action="RENAME_COMBINED_GROUP",
            model_name="CombinedCourseGroup",
            object_id=group.id,
            details={"old_group_code": old_name, "new_group_code": new_name},
        )
    except ImportError:
        pass

    return JsonResponse({
        "status": "success",
        "message": f'Renamed "{old_name}" to "{new_name}".',
        "group_id": group.id,
        "group_code": group.group_code,
    })


# -----------------------------------------------------------------------
# Add to Combined Group…  (course must NOT already be in a group)
# -----------------------------------------------------------------------
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def add_to_combined_group_api(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required."}, status=405)

    allocation_id = request.POST.get("allocation_id")
    group_id = request.POST.get("group_id")
    if not (allocation_id and group_id):
        return JsonResponse({"status": "error", "message": "allocation_id and group_id are required."}, status=400)

    allocation = get_object_or_404(CourseAllocation, pk=allocation_id)
    group = get_object_or_404(CombinedCourseGroup, pk=group_id)

    if allocation.combined_groups.exists():
        return JsonResponse({"status": "error", "message": "This course already belongs to a combined group."}, status=400)

    if canonical_course_key(group.base_course_code) != _base_key(allocation.course_code):
        return JsonResponse({
            "status": "error",
            "message": (
                f'Can\'t add — "{group.display_name()}" is a group for '
                f'{group.base_course_code}, not {allocation.course_code}.'
            ),
        }, status=400)

    with transaction.atomic():
        from course_management.cod_panel import _add_allocation_to_group_core
        result = _add_allocation_to_group_core(group, allocation, group.department)

    return JsonResponse({
        "status": "success",
        "message": f'Added {allocation.course_code} to "{group.display_name()}".',
        "group_id": group.id,
        "group_code": group.display_name(),
        "remaining_count": result["remaining_count"],
        "total_students": result["total_students"],
        "placement": result.get("placement"),
    })


# -----------------------------------------------------------------------
# Remove from Combined Group…
# -----------------------------------------------------------------------
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def remove_from_combined_group_api(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required."}, status=405)

    allocation_id = request.POST.get("allocation_id")
    if not allocation_id:
        return JsonResponse({"status": "error", "message": "allocation_id is required."}, status=400)

    allocation = get_object_or_404(CourseAllocation, pk=allocation_id)
    group = _current_group(allocation)
    if not group:
        return JsonResponse({"status": "error", "message": "This course is not in a combined group."}, status=400)

    with transaction.atomic():
        from course_management.cod_panel import _remove_allocation_from_group_core
        result = _remove_allocation_from_group_core(group, allocation, group.department)

    msg = f"Removed {allocation.course_code} from the combined group."
    if result.get("deleted"):
        msg += " The group has been dissolved (fewer than 2 members remained)."

    return JsonResponse({
        "status": "success",
        "message": msg,
        "deleted": bool(result.get("deleted")),
        "remaining_count": result.get("remaining_count"),
        "total_students": result.get("total_students"),
        "placement": result.get("placement"),
    })


# -----------------------------------------------------------------------
# Move to Another Group…  (course must already be in a group; target must
# share the same base course code)
# -----------------------------------------------------------------------
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def move_to_combined_group_api(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required."}, status=405)

    allocation_id = request.POST.get("allocation_id")
    to_group_id = request.POST.get("to_group_id")
    if not (allocation_id and to_group_id):
        return JsonResponse({"status": "error", "message": "allocation_id and to_group_id are required."}, status=400)

    allocation = get_object_or_404(CourseAllocation, pk=allocation_id)
    from_group = _current_group(allocation)
    if not from_group:
        return JsonResponse({"status": "error", "message": "This course is not currently in a combined group."}, status=400)

    to_group = get_object_or_404(CombinedCourseGroup, pk=to_group_id)
    if to_group.id == from_group.id:
        return JsonResponse({"status": "error", "message": "That course is already in this group."}, status=400)

    if canonical_course_key(to_group.base_course_code) != canonical_course_key(from_group.base_course_code):
        return JsonResponse({
            "status": "error",
            "message": (
                f'Can\'t move — "{to_group.display_name()}" is a group for '
                f'{to_group.base_course_code}, not {from_group.base_course_code}.'
            ),
        }, status=400)

    with transaction.atomic():
        from course_management.cod_panel import (
            _remove_allocation_from_group_core,
            _add_allocation_to_group_core,
        )
        removal = _remove_allocation_from_group_core(from_group, allocation, from_group.department)
        add_result = _add_allocation_to_group_core(to_group, allocation, to_group.department)

    return JsonResponse({
        "status": "success",
        "message": f'Moved {allocation.course_code} to "{to_group.display_name()}".',
        "from_group_deleted": bool(removal.get("deleted")),
        "to_group_id": to_group.id,
        "to_group_code": to_group.display_name(),
        "remaining_count": add_result["remaining_count"],
        "total_students": add_result["total_students"],
        "placement": add_result.get("placement"),
    })
