from django.apps import AppConfig


class OdelSystemConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'odel_system'

    def ready(self):
        try:
            from backup_system.signals import register_audit_signals
            from .models import ODELCourseAllocation
            register_audit_signals([ODELCourseAllocation])
        except Exception:
            pass
