"""
resits_timetabling/publish.py
==============================
Publishes the auto-scheduler draft timetable.

Sequence
--------
1. Delete ALL existing ResitTimetable rows  (clean slate).
2. Copy every ResitTempTimetable row → ResitTimetable (bulk insert).
3. Delete ALL ResitTempTimetable rows  (clear the draft).
4. Redirect to the manual timetabling panel (resit_manual_panel).

This is intentionally a single, atomic-ish POST endpoint with no partial
updates — either the whole draft is promoted or nothing changes.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.group_required import group_required
from .models import ResitTempTimetable, ResitTimetable


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def publish_resit_draft(request):
    """
    POST — promote the entire ResitTempTimetable draft to ResitTimetable.

    Steps:
      1. Clear ResitTimetable.
      2. Bulk-copy every ResitTempTimetable row into ResitTimetable.
      3. Clear ResitTempTimetable.
      4. Redirect to resit_manual_panel.

    On error returns JSON { success: false, error: "..." } with HTTP 500
    so the frontend can surface the message if called via fetch().
    """
    try:
        # ── 1. Wipe the published table ───────────────────────────────────
        deleted_published, _ = ResitTimetable.objects.all().delete()

        # ── 2. Read all temp rows with their FK relations ─────────────────
        temp_rows = (
            ResitTempTimetable.objects
            .select_related(
                "resit_course_allocation",
                "venue",
            )
            .all()
        )

        now         = timezone.now()
        published_by = request.user

        # ── 3. Build ResitTimetable objects in memory ─────────────────────
        to_create = []
        for row in temp_rows:
            to_create.append(
                ResitTimetable(
                    resit_course_allocation = row.resit_course_allocation,
                    venue                  = row.venue,
                    date                   = row.date,
                    day                    = row.day,
                    start_time             = row.start_time,
                    end_time               = row.end_time,
                    registered_students    = row.registered_students,
                    published_by           = published_by,
                    published_at           = now,
                    version                = 1,
                    notes                  = "",
                )
            )

        # ── 4. Bulk-insert (single DB round-trip) ─────────────────────────
        created = ResitTimetable.objects.bulk_create(to_create)

        # ── 5. Clear the draft table ──────────────────────────────────────
        deleted_temp, _ = ResitTempTimetable.objects.all().delete()

        # ── 6. Redirect to the manual timetabling panel ───────────────────
        return redirect("resit_manual_panel")

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse(
            {"success": False, "error": str(exc)},
            status=500,
        )