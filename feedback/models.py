import mimetypes

from django.conf import settings
from django.db import models
from django.utils import timezone

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


def _next_challenge_reference():
    """Generate the next SC-<year>-<seq> reference code, e.g. SC-2026-0001."""
    year = timezone.now().year
    prefix = f"SC-{year}-"
    last = (
        SystemChallenge.objects.filter(reference_code__startswith=prefix)
        .order_by("-reference_code")
        .first()
    )
    if last:
        try:
            seq = int(last.reference_code.rsplit("-", 1)[-1]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


class SystemChallenge(models.Model):
    """
    A logged challenge/incident record used to defend the system: for every
    problem raised (whether it originated from a user Feedback submission or
    was logged directly by staff), this records whether it was actually a
    fault of the system or not, what caused it, how it was resolved, what
    was put in place to stop it recurring, and who was involved.

    Shown on the Timetable Dashboard as a "System Challenge Log" so the
    Timetabling Office has a durable, exportable audit trail.
    """

    CAUSE_SYSTEM = "system"
    CAUSE_NOT_SYSTEM = "not_system"
    CAUSE_CHOICES = [
        (CAUSE_SYSTEM, "System Challenge"),
        (CAUSE_NOT_SYSTEM, "Not a System Challenge"),
    ]

    STATUS_OPEN = "open"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_RESOLVED = "resolved"
    STATUS_MONITORING = "monitoring"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_MONITORING, "Monitoring"),
    ]

    reference_code = models.CharField(
        max_length=30, unique=True, blank=True,
        help_text="Auto-generated tracking code, e.g. SC-2026-0001. Also used to "
                   "match rows on re-import so re-importing an export updates the "
                   "same record instead of duplicating it.",
    )

    # Optional link back to the originating public/mobile Feedback submission.
    feedback = models.ForeignKey(
        Feedback, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="system_challenges",
        help_text="The feedback item this challenge was logged from, if any.",
    )

    title = models.CharField(max_length=255)
    classification = models.CharField(
        max_length=20, choices=CAUSE_CHOICES, default=CAUSE_SYSTEM,
        verbose_name="Is this a system challenge?",
    )
    description = models.TextField(
        help_text="What happened / what the challenge was.",
    )
    cause = models.TextField(
        verbose_name="What caused it",
    )
    resolution = models.TextField(
        verbose_name="How it was resolved",
        blank=True,
    )
    prevention = models.TextField(
        verbose_name="How future recurrence is prevented",
        blank=True,
    )
    involved_parties = models.TextField(
        verbose_name="Involved users / parties",
        help_text="Names, roles or departments of everyone involved "
                   "(reporter, affected users, staff who investigated/fixed it, etc.).",
        blank=True,
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)

    occurred_at = models.DateField(default=timezone.localdate)
    resolved_at = models.DateField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="system_challenges_logged",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-occurred_at", "-created_at"]
        verbose_name = "System Challenge"
        verbose_name_plural = "System Challenge Log"

    def save(self, *args, **kwargs):
        if not self.reference_code:
            self.reference_code = _next_challenge_reference()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reference_code} — {self.title}"

    def get_classification_display_badge(self):
        return "System Challenge" if self.classification == self.CAUSE_SYSTEM else "Not a System Challenge"

    def as_json(self):
        """Serialized for the Edit modal's JS prefill (see challenge_dashboard.html)."""
        import json
        return json.dumps({
            "id": self.id,
            "reference_code": self.reference_code,
            "title": self.title,
            "classification": self.classification,
            "status": self.status,
            "description": self.description,
            "cause": self.cause,
            "resolution": self.resolution,
            "prevention": self.prevention,
            "involved_parties": self.involved_parties,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else "",
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else "",
        })


# ─────────────────────────────────────────────────────────────────────────────
#  Re-export chatbot models so they are registered under the 'feedback' app
#  and picked up by migrations.
# ─────────────────────────────────────────────────────────────────────────────
from .chatbot_models import ChatbotPublication, ChatbotTimetableEntry  # noqa: F401, E402
