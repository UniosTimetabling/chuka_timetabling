"""
mobile_api/views_schedule_visibility.py
========================================
Staff-facing "Schedule Visibility" page: block or unblock what the mobile
app shows for the regular ("main") and exam timetables, with a message and
an optional tappable link (e.g. a PDF uploaded elsewhere) shown in the app
in place of the blocked grid.

This wraps mobile_api.models.ScheduleVisibility — previously editable only
from /admin/ — in a page consistent with the rest of the Timetabling
Dashboard, and adds one thing Django admin couldn't: an optional one-tap
fan-out of the same block message as a mobile Announcement (Events & Memos
feed) via the existing manage_mobile_announcements pipeline, so blocked
users get a heads-up beyond just opening the Timetable tab and finding it
blocked.

    GET  /mobile/schedule-visibility/                        — the page
    POST /api/mobile/admin/schedule-visibility/<type>/save/   — update one row

See mobile_api.timetable_builder (_visibility_payload) for how a blocked
row reaches the app, and screens/TimetableScreen.js +
components/BlockedScheduleNotice.js for how the app renders it.
"""
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.rbac import allowed_roles, Role

from .models import Announcement, ScheduleVisibility

SCHEDULE_VISIBILITY_STAFF_ROLES = (Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)


def _serialize(row: ScheduleVisibility) -> dict:
    return {
        "scheduleType": row.schedule_type,
        "label": row.get_schedule_type_display(),
        "isBlocked": row.is_blocked,
        "message": row.message,
        "linkLabel": row.link_label,
        "linkUrl": row.link_url,
        "updatedAt": row.updated_at.isoformat(),
    }


@allowed_roles(*SCHEDULE_VISIBILITY_STAFF_ROLES)
def schedule_visibility_page(request):
    """The block/unblock page — one card per schedule (main, exam)."""
    rows = [
        ScheduleVisibility.get_for(schedule_type)
        for schedule_type, _label in ScheduleVisibility.SCHEDULE_CHOICES
    ]
    return render(request, "mobile_api/schedule_visibility.html", {
        "rows": rows,
    })


@allowed_roles(*SCHEDULE_VISIBILITY_STAFF_ROLES)
@require_POST
def api_save_schedule_visibility(request, schedule_type):
    if schedule_type not in dict(ScheduleVisibility.SCHEDULE_CHOICES):
        return HttpResponseBadRequest("Unknown schedule type.")

    row = ScheduleVisibility.get_for(schedule_type)

    is_blocked = request.POST.get("is_blocked", "false").strip().lower() in ("true", "1", "on")
    message = request.POST.get("message", "").strip()
    link_label = request.POST.get("link_label", "").strip()
    link_url = request.POST.get("link_url", "").strip()

    if is_blocked and not message:
        return JsonResponse(
            {"ok": False, "error": "Add a message to show in place of the schedule before blocking it."},
            status=400,
        )

    row.is_blocked = is_blocked
    row.message = message
    row.link_label = link_label
    row.link_url = link_url
    row.save()

    notify_sent = False
    want_notify = request.POST.get("notify_app", "false").strip().lower() in ("true", "1", "on")
    if is_blocked and want_notify:
        description = row.message
        if row.link_url:
            label = row.link_label or "Reference link"
            description = f"{description}\n\n{label}: {row.link_url}"
        Announcement.objects.create(
            title=f"{row.get_schedule_type_display()} update",
            description=description,
            announcement_type=Announcement.TYPE_MEMO,
            audience=Announcement.AUDIENCE_ALL,
            date=timezone.now(),
            is_published=True,
        )
        notify_sent = True

    return JsonResponse({"ok": True, "row": _serialize(row), "notifySent": notify_sent})
