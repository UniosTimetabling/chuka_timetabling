from django.apps import AppConfig


class DepartmentManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'department_management'

    def ready(self):
        try:
            from backup_system.signals import register_audit_signals
            from .models import Department
            register_audit_signals([Department])
        except Exception:
            pass
