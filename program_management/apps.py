from django.apps import AppConfig


class ProgramManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'program_management'

    def ready(self):
        # Wire Program / ProgramCourse into the shared audit trail so every
        # create/update/delete (including ones made via the Import feature)
        # is captured and can later be found/rolled back from the
        # sudo-only Course Master Rollback page.
        try:
            from backup_system.signals import register_audit_signals
            from program_management.models import Program, ProgramCourse
            register_audit_signals([Program, ProgramCourse])
        except Exception:
            # Never let audit wiring block app startup (e.g. during
            # early migrations when backup_system isn't ready yet).
            import logging
            logging.getLogger(__name__).exception(
                "program_management: failed to register audit signals"
            )
