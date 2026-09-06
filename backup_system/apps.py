from django.apps import AppConfig


class BackupSystemConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'backup_system'
    verbose_name = 'Backup, Recovery & Audit System'

    def ready(self):
        # Import signal registration helper. Actual model registration
        # for audit tracking happens per-app (see INTEGRATION_GUIDE.md) -
        # each app calls register_audit_signals([Model1, Model2, ...])
        # from its own apps.py ready(), keeping this app decoupled from
        # the rest of the project's models.
        from . import signals  # noqa: F401
