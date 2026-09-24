"""
feedback/chatbot_models.py
══════════════════════════
ChatbotTimetable model — a flat, denormalised snapshot of the PUBLISHED
timetable that the chatbot reads from.

Populated automatically via a Django signal every time a PDF is published
through publish_timetables_pdfs.publish_exam_timetable_pdf  or
                                 .publish_regular_timetable_pdf

The chatbot NEVER touches TempTimetable / Timetable / ExamTimetable directly.
It only reads ChatbotTimetableEntry rows whose publication is the latest for
each timetable_type+academic_year+semester triple.
"""
from django.db import models
from django.utils import timezone
from datetime import timedelta


class ChatConversationLog(models.Model):
    """
    Alias / replacement for ChatbotConversationLog.
    Stores every chatbot conversation turn for improving the bot.
    Auto-deleted after 2 weeks. Only the formal contact-form submissions
    (Feedback model) are kept permanently.
    """
    session_id   = models.CharField(max_length=128, db_index=True)
    user_message = models.TextField()
    bot_reply    = models.TextField()
    topic        = models.CharField(max_length=80, blank=True)
    query_type   = models.CharField(max_length=40, blank=True)
    # Did the bot fail to answer (show_form=True)?
    bot_failed   = models.BooleanField(default=False)
    created_at   = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Chat Conversation Log"
        verbose_name_plural = "Chat Conversation Logs"
        # Use the same DB table as the existing ChatbotConversationLog migration
        # so we don't need a new migration — we just add a few columns
        db_table = "feedback_chatconversationlog"
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["bot_failed", "created_at"]),
        ]

    def __str__(self):
        return f"[{self.session_id[:8]}] {self.created_at:%Y-%m-%d %H:%M} — {self.user_message[:60]}"

    @classmethod
    def purge_old(cls):
        """Delete all logs older than 2 weeks. Called from bot_chat view (5% of requests)."""
        cutoff = timezone.now() - timedelta(weeks=2)
        deleted, _ = cls.objects.filter(created_at__lt=cutoff).delete()
        if deleted:
            import logging
            logging.getLogger(__name__).info(f"Purged {deleted} old chat logs (older than 2 weeks)")
        return deleted


class ChatbotPublication(models.Model):
    """
    One record per published PDF.  When a new PDF is published for the same
    (timetable_type, academic_year, semester), the previous publication is
    marked is_latest=False and all its entries are deleted; fresh entries are
    written for the new publication.
    """
    TIMETABLE_TYPES = [
        ("REGULAR", "Regular Timetable"),
        ("EXAM",    "Exam Timetable"),
    ]
    SEMESTER_CHOICES = [
        ("1", "Semester 1"),
        ("2", "Semester 2"),
    ]

    timetable_type = models.CharField(max_length=10, choices=TIMETABLE_TYPES)
    academic_year  = models.CharField(max_length=15, help_text="e.g. 2024/2025")
    semester       = models.CharField(max_length=2,  choices=SEMESTER_CHOICES)
    published_at   = models.DateTimeField(auto_now_add=True)
    published_by   = models.CharField(max_length=150, blank=True)
    is_latest      = models.BooleanField(default=True, db_index=True)

    # FK to PDFDocument so staff can trace back to the exact PDF
    pdf_document_id = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="ID of the export_import.PDFDocument that triggered this publication",
    )

    class Meta:
        ordering = ["-published_at"]
        indexes = [
            models.Index(fields=["timetable_type", "academic_year", "semester", "is_latest"]),
        ]

    def __str__(self):
        return (
            f"[{self.timetable_type}] {self.academic_year} S{self.semester} "
            f"{'(latest)' if self.is_latest else '(superseded)'} — {self.published_at:%Y-%m-%d %H:%M}"
        )


class ChatbotTimetableEntry(models.Model):
    """
    One row per course-slot in the published timetable.
    Flat denormalised fields so the chatbot can answer queries with simple
    .filter() calls — no joins, no FK resolution needed at query time.
    """
    publication = models.ForeignKey(
        ChatbotPublication,
        on_delete=models.CASCADE,
        related_name="entries",
    )

    # ── Scheduling fields ────────────────────────────────────────────────────
    # Regular timetable uses day/start_time/end_time
    # Exam timetable uses date/start_time/end_time  (day derived from date)
    day          = models.CharField(max_length=20, blank=True)        # e.g. "Monday"
    date         = models.DateField(null=True, blank=True)            # exam date
    start_time   = models.TimeField(null=True, blank=True)
    end_time     = models.TimeField(null=True, blank=True)
    venue_code   = models.CharField(max_length=50, blank=True)        # e.g. "BSL 101"
    venue_name   = models.CharField(max_length=200, blank=True)

    # ── Course / allocation fields ───────────────────────────────────────────
    course_code  = models.CharField(max_length=100, db_index=True)
    course_name  = models.CharField(max_length=200, blank=True)

    # ── Programme / department ───────────────────────────────────────────────
    program_name = models.CharField(max_length=200, blank=True, db_index=True)
    department_name = models.CharField(max_length=200, blank=True)

    # ── Year of study (extracted from course code pattern, e.g. COSC1xx → 1) ─
    year_of_study = models.CharField(max_length=5, blank=True)

    # ── Lecturer ─────────────────────────────────────────────────────────────
    lecturer_name = models.CharField(max_length=200, blank=True)

    # ── Merges (slashed codes) ───────────────────────────────────────────────
    # Comma-separated list of all course codes in this cell (for merged groups)
    merged_codes = models.TextField(
        blank=True,
        help_text="Comma-separated list of all codes sharing this slot (including this entry's own code)",
    )

    class Meta:
        indexes = [
            models.Index(fields=["publication", "course_code"]),
            models.Index(fields=["publication", "program_name"]),
            models.Index(fields=["publication", "day", "start_time"]),
            models.Index(fields=["publication", "venue_code"]),
        ]

    def __str__(self):
        slot = self.day or (str(self.date) if self.date else "?")
        return f"{self.course_code} — {slot} {self.start_time} @ {self.venue_code}"


# ─────────────────────────────────────────────────────────────────────────────
#  Convenience queryset helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_latest_entries(timetable_type: str = None):
    """
    Return a queryset of ChatbotTimetableEntry rows for the most recent
    publication(s).  If timetable_type is None, returns both REGULAR and EXAM.
    """
    qs = ChatbotPublication.objects.filter(is_latest=True)
    if timetable_type:
        qs = qs.filter(timetable_type=timetable_type)
    pub_ids = list(qs.values_list("id", flat=True))
    return ChatbotTimetableEntry.objects.filter(publication_id__in=pub_ids)

