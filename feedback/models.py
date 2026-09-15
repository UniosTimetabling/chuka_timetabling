import mimetypes

from django.db import models

class Feedback(models.Model):
    full_name = models.CharField(max_length=200)
    email = models.EmailField()
    admission_number = models.CharField(max_length=50, blank=True, null=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    seen = models.BooleanField(default=False)
    
    # Add status field for better tracking
    STATUS_CHOICES = [
        ('unseen', 'Unseen'),
        ('seen', 'Seen'),
        ('attended', 'Attended'),
        ('solved', 'Solved'),
    ]
    
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='unseen'
    )

    # ── Mobile app submission metadata ─────────────────────────────────────
    # Populated only when this feedback came in through
    # POST /api/mobile/feedback/ (mobile_api/views_feedback.py). Left blank
    # for feedback submitted through the public web form, which has no
    # concept of a logged-in student/lecturer identity.
    SOURCE_WEB = "web"
    SOURCE_MOBILE = "mobile"
    SOURCE_CHOICES = [
        (SOURCE_WEB, "Web"),
        (SOURCE_MOBILE, "Mobile App"),
    ]
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_WEB)

    ROLE_STUDENT = "student"
    ROLE_LECTURER = "lecturer"
    ROLE_CHOICES = [
        (ROLE_STUDENT, "Student"),
        (ROLE_LECTURER, "Lecturer"),
    ]
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, blank=True, default="")

    # The opaque mobile userId ("stu:<dept>:<program>:<year>" / "lec:<id>")
    # sent alongside the submission — see mobile_api/scope.py. Stored as-is
    # (not resolved to an FK) so this app stays independent of mobile_api.
    mobile_user_id = models.CharField(max_length=60, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Feedback"
        verbose_name_plural = "Feedbacks"

    def __str__(self):
        return f"{self.full_name} — {self.email} ({self.created_at:%Y-%m-%d %H:%M})"
    
    def get_status_display(self):
        """Get display text for status"""
        status_dict = dict(self.STATUS_CHOICES)
        return status_dict.get(self.status, 'Unseen')
    
    def is_seen(self):
        """Check if feedback is seen based on status"""
        return self.status in ['seen', 'attended', 'solved']

def feedback_attachment_path(instance, filename):
    return f"feedback_attachments/{instance.feedback_id}/{filename}"


class FeedbackAttachment(models.Model):
    """A photo or document (screenshot of an error, a scanned form, etc.)
    attached to a Feedback submission. Mirrors
    mobile_api.models.AnnouncementAttachment — same auto-detected
    mime_type-on-save pattern — so the panel/mobile app can pick an icon
    without guessing from the filename."""

    feedback = models.ForeignKey(Feedback, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=feedback_attachment_path)
    name = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.file and not self.name:
            self.name = self.file.name.rsplit("/", 1)[-1]
        if self.file and not self.mime_type:
            guessed, _ = mimetypes.guess_type(self.file.name)
            self.mime_type = guessed or "application/octet-stream"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name or str(self.file)


# ─────────────────────────────────────────────────────────────────────────────
#  Re-export chatbot models so they are registered under the 'feedback' app
#  and picked up by migrations.
# ─────────────────────────────────────────────────────────────────────────────
from .chatbot_models import ChatbotPublication, ChatbotTimetableEntry  # noqa: F401, E402
