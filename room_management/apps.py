from django.apps import AppConfig


class RoomManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'room_management'

    def ready(self):
        try:
            from backup_system.signals import register_audit_signals
            from .models import Venue, Building
            register_audit_signals([Venue, Building])
        except Exception:
            pass
