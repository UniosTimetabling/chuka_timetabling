"""
Management command: watch_critical_config

Run as a lightweight periodic check (cron or Celery Beat) that hashes
the contents of .env and key settings files. If the hash changes since
the last run, it means someone edited critical configuration - and per
the "Before critical configuration changes" trigger requirement, this
fires an immediate background backup BEFORE the change can cause
irreversible damage.

Usage:
    python manage.py watch_critical_config

Suggested cron: every 5 minutes
    */5 * * * * cd /path/to/project && python manage.py watch_critical_config
"""
import hashlib
import json
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings
from backup_system.models import BackupConfiguration
from backup_system.tasks import trigger_pre_change_backup


WATCHED_FILES = ['.env', '.env.example']
STATE_FILE = '.backup_system_config_watch_state.json'


class Command(BaseCommand):
    help = 'Watches critical config files for changes and triggers a backup before damage occurs'

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        state_path = base_dir / STATE_FILE

        previous_state = {}
        if state_path.exists():
            previous_state = json.loads(state_path.read_text())

        current_state = {}
        changed_files = []

        for filename in WATCHED_FILES:
            file_path = base_dir / filename
            if not file_path.exists():
                continue

            file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
            current_state[filename] = file_hash

            if filename in previous_state and previous_state[filename] != file_hash:
                changed_files.append(filename)

        state_path.write_text(json.dumps(current_state, indent=2))

        if changed_files:
            self.stdout.write(
                self.style.WARNING(
                    f"Critical config change detected in: {', '.join(changed_files)}"
                )
            )

            config = BackupConfiguration.objects.filter(
                enabled=True, backup_type='config'
            ).first()

            if not config:
                config = BackupConfiguration.objects.filter(enabled=True).first()

            if config:
                trigger_pre_change_backup.delay(
                    config.id,
                    reason=f"Critical config change detected: {', '.join(changed_files)}"
                )
                self.stdout.write(self.style.SUCCESS("Pre-change backup queued."))
            else:
                self.stdout.write(
                    self.style.ERROR(
                        "No enabled BackupConfiguration found - cannot trigger pre-change backup."
                    )
                )
        else:
            self.stdout.write("No critical config changes detected.")
