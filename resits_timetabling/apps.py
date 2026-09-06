from django.apps import AppConfig


class ResitsTimetablingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'resits_timetabling'

    def ready(self):
        try:
            from backup_system.signals import register_audit_signals
            from .models import ResitCourseAllocation
            register_audit_signals([ResitCourseAllocation])
        except Exception:
            pass
