"""
mobile_api/views_admin_announcements.py
=========================================
Staff-facing "Send Notification" panel + its backing JSON API.

Lets Director Timetable / Timetable Admins / SUDO compose an Announcement
(a message, optionally with PDF/image/document attachments) and push it to
the mobile app's Events & Memos feed (GET /api/mobile/events/) for
everyone, students only, or lecturers only — without needing /admin/.

    GET  /notifications/mobile/                      — the compose page
    POST /api/mobile/admin/announcements/send/        — create + attach
    POST /api/mobile/admin/announcements/<id>/toggle/  — publish/unpublish
    POST /api/mobile/admin/announcements/<id>/delete/  — remove

The page's own JS calls the three API endpoints below via fetch(), so the
API is fully usable on its own (e.g. from another internal tool) and the
page is just its first caller — one implementation, no duplicated logic.
"""
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from core.rbac import allowed_roles, Role
from program_management.models import Program

from .models import Announcement, AnnouncementAttachment

MAX_ATTACHMENTS = 8
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25MB/file — a scanned memo or event poster can be sizeable

ANNOUNCEMENT_STAFF_ROLES = (Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)


def _serialize(a: Announcement) -> dict:
    return {
        "id": a.id,
        "title": a.title,
        "description": a.description,
        "type": a.announcement_type,
        "typeDisplay": a.get_announcement_type_display(),
        "audience": a.audience,
        "audienceDisplay": a.get_audience_display(),
        "program": a.program.name if a.program_id else None,
        "year": a.year,
        "date": a.date.isoformat(),
        "isPublished": a.is_published,
        "createdAt": a.created_at.isoformat(),
        "attachments": [
            {"name": att.name, "url": att.file.url, "mimeType": att.mime_type}
            for att in a.attachments.all() if att.file
        ],
    }


@allowed_roles(*ANNOUNCEMENT_STAFF_ROLES)
def manage_announcements_page(request):
    """The compose page: a send form + a list of everything sent so far."""
    announcements = (
        Announcement.objects.all()
        .select_related("program")
        .prefetch_related("attachments")
        .order_by("-created_at")[:50]
    )
    programs = Program.objects.order_by("name").only("id", "name")
    return render(request, "mobile_api/send_notification.html", {
        "announcements": announcements,
        "programs": programs,
        "audience_choices": Announcement.AUDIENCE_CHOICES,
        "type_choices": Announcement.TYPE_CHOICES,
    })


@allowed_roles(*ANNOUNCEMENT_STAFF_ROLES)
@require_POST
def api_send_announcement(request):
    title = request.POST.get("title", "").strip()
    description = request.POST.get("description", "").strip()
    announcement_type = request.POST.get("announcement_type", Announcement.TYPE_MEMO).strip()
    audience = request.POST.get("audience", Announcement.AUDIENCE_ALL).strip()
    program_id = request.POST.get("program_id", "").strip()
    year = request.POST.get("year", "").strip()
    date_raw = request.POST.get("date", "").strip()
    is_published = request.POST.get("is_published", "true").strip().lower() not in ("false", "0", "")

    if not title:
        return JsonResponse({"ok": False, "error": "Title is required."}, status=400)

    if announcement_type not in dict(Announcement.TYPE_CHOICES):
        return JsonResponse({"ok": False, "error": "Invalid type."}, status=400)

    if audience not in dict(Announcement.AUDIENCE_CHOICES):
        return JsonResponse({"ok": False, "error": "Invalid audience."}, status=400)

    date_value = parse_datetime(date_raw) if date_raw else None
    if not date_value:
        date_value = timezone.now()

    program = None
    if program_id:
        program = Program.objects.filter(id=program_id).first()
        if not program:
            return JsonResponse({"ok": False, "error": "Unknown program."}, status=400)

    year_value = None
    if year:
        try:
            year_value = int(year)
        except ValueError:
            return JsonResponse({"ok": False, "error": "Year must be a number."}, status=400)

    files = request.FILES.getlist("attachments")
    if len(files) > MAX_ATTACHMENTS:
        return JsonResponse(
            {"ok": False, "error": f"You can attach up to {MAX_ATTACHMENTS} files."}, status=400
        )
    for f in files:
        if f.size > MAX_ATTACHMENT_BYTES:
            return JsonResponse(
                {"ok": False, "error": f"'{f.name}' is too large (max 25MB)."}, status=400
            )

    announcement = Announcement.objects.create(
        title=title,
        description=description,
        announcement_type=announcement_type,
        audience=audience,
        program=program,
        year=year_value,
        date=date_value,
        is_published=is_published,
    )
    for f in files:
        AnnouncementAttachment.objects.create(announcement=announcement, file=f)

    return JsonResponse({"ok": True, "announcement": _serialize(announcement)})


@allowed_roles(*ANNOUNCEMENT_STAFF_ROLES)
@require_POST
def api_toggle_announcement(request, pk):
    announcement = get_object_or_404(Announcement, pk=pk)
    announcement.is_published = not announcement.is_published
    announcement.save(update_fields=["is_published", "updated_at"])
    return JsonResponse({"ok": True, "isPublished": announcement.is_published})


@allowed_roles(*ANNOUNCEMENT_STAFF_ROLES)
@require_POST
def api_delete_announcement(request, pk):
    announcement = get_object_or_404(Announcement, pk=pk)
    announcement.delete()
    return JsonResponse({"ok": True})
