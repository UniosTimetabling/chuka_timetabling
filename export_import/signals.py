"""
export_import/signals.py
═════════════════════════
Django post_save signal on PDFDocument.

When a new PDFDocument is created with status='PUBLISHED', this signal
fires sync_chatbot_from_pdf_document() which:
  - Marks previous ChatbotPublication as stale
  - Deletes their entries
  - Re-reads the live DB (ExamTimetable / Timetable) and bulk-inserts
    fresh ChatbotTimetableEntry rows

No user action needed — publishing the PDF also publishes the chatbot model.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import PDFDocument

logger = logging.getLogger(__name__)


@receiver(post_save, sender=PDFDocument)
def on_pdf_document_saved(sender, instance, created, **kwargs):
    """
    Only trigger when a *new* published document is created.
    Avoid re-syncing on metadata-only saves (e.g. is_latest=False update).
    """
    if not created:
        return
    if instance.status != "PUBLISHED":
        return

    try:
        from feedback.chatbot_publisher import sync_chatbot_from_pdf_document
        sync_chatbot_from_pdf_document(instance)
    except Exception as exc:
        # Never let a sync failure crash the publish view
        logger.exception("chatbot sync failed for PDFDocument %s: %s", instance.pk, exc)
