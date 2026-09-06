"""
sync_now management command
============================

Runs a HOST -> REMOTE sync immediately, synchronously, in the current
process — no browser, no click on "Sync Now" needed. This is what makes
it possible to schedule the sync feature (see export_import/sync_engine.py
and core/models.SyncNode) from plain cron or a PythonAnywhere "Scheduled
task", on installations that don't run Celery/Celery Beat at all.

If Celery IS running, export_import.tasks.auto_run_sync (wired into
Celery Beat) already does this automatically based on
SyncNode.auto_sync_interval_minutes, so this command isn't required —
but it works either way and is useful for a manual/ad hoc run from the
server too, e.g.:

    python manage.py sync_now

Exits non-zero if the run finished as "failed" or "partial", so a cron
wrapper can alert on it.
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Runs a host->remote sync immediately (see /sync/settings/)."

    def handle(self, *args, **options):
        from core.models import SyncNode
        from export_import import sync_engine

        node = SyncNode.get_settings()

        if not node.is_enabled:
            raise CommandError("Sync is disabled for this installation (Sync Settings > Enabled).")
        if not node.is_host:
            raise CommandError("This installation is in REMOTE mode — only a HOST can push a sync.")
        if not node.remote_url or not node.get_token():
            raise CommandError("Remote URL and/or shared token are not configured in Sync Settings.")

        self.stdout.write("Starting sync...")
        run = sync_engine.run_sync(triggered_by=None)

        for log in run.model_logs.order_by("order"):
            self.stdout.write(
                f"  [{log.order:03d}] {log.status.upper():7} {log.app_label}.{log.model_name} "
                f"({log.records_changed}/{log.records_total} changed)"
                + (f" — {log.error_message}" if log.error_message else "")
            )

        self.stdout.write(f"Finished: status={run.status}, records_sent={run.records_sent}")

        if run.status in ("failed", "partial"):
            raise CommandError(f"Sync run #{run.pk} finished as '{run.status}'.")
