"""
feedback/urls.py
════════════════
URL patterns for the feedback and help-chat system.

Public endpoints:
  GET  /feedback/              — Feedback form page
  POST /api/submit_feedback/   — Submit feedback (AJAX / widget)
  POST /api/bot-chat/          — Chatbot query endpoint (widget)

Staff-only endpoints (login required):
  GET  /feedback_panel/         — View submitted feedback
  GET  /feedback/export/        — Export as CSV
  GET  /feedback/export/pdf/    — Export as PDF
  POST /feedback/update-status/ — Update feedback status (AJAX)
"""

from django.urls import path
from . import feedback_panel, views, chat_view

urlpatterns = [
    # ── Public ──────────────────────────────────────────────────────────────
    path("feedback/",               feedback_panel.feedback_page,   name="feedback_page"),
    path("api/submit_feedback/",    feedback_panel.submit_feedback, name="submit_feedback"),
    path("api/bot-chat/",           chat_view.bot_chat,             name="bot_chat"),

    # ── Staff panel ─────────────────────────────────────────────────────────
    path("feedback_panel/",         feedback_panel.feedback_panel,  name="feedback_panel"),
    path("feedback/export/",        views.export_feedbacks,         name="export_feedbacks"),
    path("feedback/export/pdf/",    views.export_feedbacks_pdf,     name="export_feedbacks_pdf"),
    path("feedback/update-status/", views.update_feedback_status,   name="update_feedback_status"),
]
