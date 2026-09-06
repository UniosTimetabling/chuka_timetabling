"""
mobile_api/views_events.py
=============================
GET /api/mobile/events/?userId=&role=

Returns published Announcements visible to this user:
  - "all" audience announcements always show.
  - "students" audience: only for role=student, further narrowed by the
    announcement's program/year if it set them (blank = every program/year).
  - "lecturers" audience: only for role=lecturer.
"""
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django_ratelimit.decorators import ratelimit

from .scope import ScopeError, parse_user_id
from .models import Announcement


@require_GET
@ratelimit(key="ip", rate="60/m", method="GET", block=True)
def events_list(request):
    try:
        scope = parse_user_id(request.GET.get("userId", ""), request.GET.get("role", ""))
    except ScopeError as e:
        return JsonResponse({"error": e.message}, status=e.status)

    qs = Announcement.objects.filter(is_published=True).prefetch_related("attachments")

    if scope["role"] == "student":
        qs = qs.filter(Q(audience=Announcement.AUDIENCE_ALL) | Q(audience=Announcement.AUDIENCE_STUDENTS))
        qs = qs.filter(Q(program__isnull=True) | Q(program_id=scope["program_id"]))
        qs = qs.filter(Q(year__isnull=True) | Q(year=scope["year"]))
    elif scope["role"] == "lecturer":
        qs = qs.filter(Q(audience=Announcement.AUDIENCE_ALL) | Q(audience=Announcement.AUDIENCE_LECTURERS))
    else:
        qs = Announcement.objects.none()

    data = []
    for a in qs.order_by("-date")[:50]:
        data.append({
            "id": str(a.id),
            "title": a.title,
            "description": a.description,
            "type": a.announcement_type,
            "date": a.date.isoformat(),
            "attachments": [
                {
                    "name": att.name,
                    "url": request.build_absolute_uri(att.file.url),
                    "mimeType": att.mime_type,
                }
                for att in a.attachments.all() if att.file
            ],
        })

    return JsonResponse(data, safe=False)
