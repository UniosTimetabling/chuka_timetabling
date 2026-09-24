"""
mobile_api/views_feedback.py
==============================
POST /api/mobile/feedback/

Lets a student or lecturer using the mobile app submit feedback — the same
Feedback record the staff-side feedback_panel already shows, just reached
from the app instead of the public web form (feedback/feedback_panel.py's
submit_feedback). Also accepts file attachments (a screenshot of a clash,
a scanned form, a photo of a notice board, etc.), which the web form
doesn't support.

multipart/form-data fields:
    full_name          (required)
    email              (required)
    message            (required, >= 10 chars)
    admission_number   (optional)
    userId, role       (optional — same opaque identifiers every other
                         mobile_api endpoint uses, see scope.py. When both
                         are present and valid they're stored on the
                         Feedback row so staff can see it came from a
                         verified student/lecturer session; an invalid or
                         missing pair never blocks submission — the human
                         fields above are what actually matters here.)
    attachments        (optional — repeat the field for multiple files,
                         e.g. attachments=file1&attachments=file2)
"""
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from feedback.models import Feedback, FeedbackAttachment
from .scope import ScopeError, parse_user_id

MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # 15MB/file — generous enough for a phone photo or a scanned PDF


@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def submit_feedback(request):
    full_name = request.POST.get("full_name", "").strip()
    email = request.POST.get("email", "").strip()
    admission_number = request.POST.get("admission_number", "").strip()
    message = request.POST.get("message", "").strip()
    user_id = request.POST.get("userId", "").strip()
    role_param = request.POST.get("role", "").strip()

    if not full_name or not email or not message:
        return JsonResponse({"ok": False, "error": "Please fill in all required fields."}, status=400)

    if len(message) < 10:
        return JsonResponse({"ok": False, "error": "Message is too short. Please provide more details."}, status=400)

    files = request.FILES.getlist("attachments")
    if len(files) > MAX_ATTACHMENTS:
        return JsonResponse(
            {"ok": False, "error": f"You can attach up to {MAX_ATTACHMENTS} files."}, status=400
        )
    for f in files:
        if f.size > MAX_ATTACHMENT_BYTES:
            return JsonResponse(
                {"ok": False, "error": f"'{f.name}' is too large (max 15MB)."}, status=400
            )

    # Best-effort identity tagging — never fail the submission over this.
    role = ""
    if user_id and role_param:
        try:
            scope = parse_user_id(user_id, role_param)
            role = scope["role"]
        except ScopeError:
            pass

    feedback = Feedback.objects.create(
        full_name=full_name,
        email=email,
        admission_number=admission_number or None,
        message=message,
        source=Feedback.SOURCE_MOBILE,
        role=role,
        mobile_user_id=user_id if role else "",
    )

    for f in files:
        FeedbackAttachment.objects.create(feedback=feedback, file=f)

    return JsonResponse({
        "ok": True,
        "message": "Thank you! Your feedback has been submitted successfully.",
        "id": feedback.id,
    })
