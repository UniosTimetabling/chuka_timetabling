"""
resits_timetabling/clear_timetable.py
=======================================
Clears the entire published ResitTimetable and redirects to the
manual timetabling panel.

Sequence
--------
1. DELETE all ResitTimetable rows.
2. Redirect to resit_manual_panel (/resits/manual/).
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from core.group_required import group_required
from .models import ResitTimetable


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def clear_resit_timetable(request):
    """
    POST — delete every ResitTimetable row then redirect to the manual panel.

    On success  → HTTP 302 → /resits/manual/
    On error    → JSON { success: false, error: "..." }  HTTP 500
    """
    try:
        deleted_count, _ = ResitTimetable.objects.all().delete()
        return redirect("resit_manual_panel")

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse(
            {"success": False, "error": str(exc)},
            status=500,
        )