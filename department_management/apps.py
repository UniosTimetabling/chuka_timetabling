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

        # Give every newly-created Department a default DepartmentCode
        # straight away (see department_codes.py) so exports never fall
        # back to the long full name just because nobody visited the
        # Department Codes admin yet. Never touches a department that
        # already has a code (including one just added by the migration
        # backfill or edited by hand).
        try:
            from django.db.models.signals import post_save
            from .models import Department, DepartmentCode
            from .department_codes import unique_default_code

            def _ensure_department_code(sender, instance, created, **kwargs):
                if not created or instance.department_codes.exists():
                    return
                code = unique_default_code(instance.name, exclude_department_id=instance.id)
                DepartmentCode.objects.create(department=instance, code=code)

            post_save.connect(_ensure_department_code, sender=Department, dispatch_uid="department_management_auto_code")
        except Exception:
            pass
