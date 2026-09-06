from django.apps import AppConfig


class ExportImportConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'export_import'

    def ready(self):
        # Register post_save signal on PDFDocument so that every new
        # published PDF automatically syncs the chatbot timetable model.
        import export_import.signals  # noqa: F401

        # Keep the per (department, program, year) cached timetable PDFs
        # (program_year_pdf_cache) in step with local edits.
        from export_import.pdf_cache_signals import connect_all
        connect_all()

        self._warm_program_year_pdf_cache()

    def _warm_program_year_pdf_cache(self):
        """On process start, if this installation is a REMOTE with sync
        enabled, kick off a background sweep so the cache is already
        warm before the first student ever asks for a PDF — otherwise
        an empty cache only ever gets filled reactively (after a sync
        push or a local edit), and a freshly deployed/restarted remote
        would serve the very first request for each scope slowly.

        Skipped for management commands that don't actually serve
        requests (migrate, makemigrations, shell, tests, etc.) — those
        either don't need it or run before the DB is even migrated, and
        wrapped in a broad try/except so a not-yet-migrated DB (e.g.
        during the very first `migrate`) can never crash startup.
        """
        import sys

        non_serving_commands = {
            "makemigrations", "migrate", "collectstatic", "shell", "shell_plus",
            "test", "createsuperuser", "dumpdata", "loaddata", "dbshell",
            "check", "showmigrations", "sqlmigrate", "sync_now",
        }
        if len(sys.argv) > 1 and sys.argv[1] in non_serving_commands:
            return

        try:
            from core.models import SyncNode
            node = SyncNode.get_settings()
            if node.is_enabled and node.is_remote:
                from export_import.program_year_pdf_cache import regenerate_all_in_background
                regenerate_all_in_background()
        except Exception:  # noqa: BLE001 — e.g. DB not migrated yet; never block startup
            pass
