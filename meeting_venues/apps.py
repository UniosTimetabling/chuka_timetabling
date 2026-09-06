from django.apps import AppConfig


class MeetingVenuesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "meeting_venues"
    verbose_name = "Meeting Venues"

    def ready(self):
        try:
            from backup_system.signals import register_audit_signals
            from .models import MeetingVenue, MeetingBooking, UnbookRequest
            register_audit_signals([MeetingVenue, MeetingBooking, UnbookRequest])
        except Exception:
            pass
