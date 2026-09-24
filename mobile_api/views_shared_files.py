"""
mobile_api/views_shared_files.py
=================================
Staff-facing "Shared Files" page: upload any file (timetable PDF, circular,
Word doc, etc.) from the Timetabling Dashboard and get back a stable, public
link — separate from Send Notification (which always posts to the app's
Events & Memos feed) and from Schedule Visibility (which just takes a URL
you already have). This is the "I need a link" step that feeds both of
those: upload here, copy the link, paste it into either page.

    GET  /mobile/shared-files/                  — list + upload page
    POST /api/mobile/admin/shared-files/upload/ — upload a new file
    POST /api/mobile/admin/shared-files/<pk>/delete/ — remove one

Files are stored under MEDIA_ROOT/mobile_shared_files/ and served from
MEDIA_URL, so the returned link works directly in a browser or the mobile
app's link-opener (same mechanism Schedule Visibility's link_url already
relies on).
"""
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST

from core.rbac import allowed_roles, Role

from .models import SharedFile

SHARED_FILES_STAFF_ROLES = (Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)


def _serialize(row: SharedFile, request) -> dict:
    return {
        "id": row.id,
        "name": row.display_name,
        "mimeType": row.mime_type,
        "size": row.file_size,
        "url": request.build_absolute_uri(row.file.url) if row.file else "",
        "uploadedAt": row.uploaded_at.isoformat(),
        "uploadedBy": row.uploaded_by.get_username() if row.uploaded_by_id else "",
    }


@allowed_roles(*SHARED_FILES_STAFF_ROLES)
def shared_files_page(request):
    """List every uploaded file with a copy-link button, plus an upload form."""
    rows = SharedFile.objects.select_related("uploaded_by").all()
    return render(request, "mobile_api/shared_files.html", {
        "rows": rows,
    })


@allowed_roles(*SHARED_FILES_STAFF_ROLES)
@require_POST
def api_upload_shared_file(request):
    upload = request.FILES.get("file")
    if not upload:
        return HttpResponseBadRequest("No file provided.")

    title = request.POST.get("title", "").strip()

    row = SharedFile(
        file=upload,
        title=title,
        uploaded_by=request.user if request.user.is_authenticated else None,
    )
    row.save()

    return JsonResponse({"ok": True, "row": _serialize(row, request)})


@allowed_roles(*SHARED_FILES_STAFF_ROLES)
@require_POST
def api_delete_shared_file(request, pk):
    row = get_object_or_404(SharedFile, pk=pk)
    row.file.delete(save=False)
    row.delete()
    return JsonResponse({"ok": True})
