from django.apps import AppConfig


class CampusesTimetableConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'campuses_timetable'

    def ready(self):
        from .signals import connect_signals
        connect_signals()

        try:
            from backup_system.signals import register_audit_signals
            from .models import CampusCourseAllocation, CampusLabAllocation
            register_audit_signals([CampusCourseAllocation, CampusLabAllocation])
        except Exception:
            pass
