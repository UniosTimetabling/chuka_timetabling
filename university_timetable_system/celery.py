"""
Celery application for university_timetable_system.

This file goes at: university_timetable_system/celery.py
(next to settings.py, urls.py, wsgi.py)
"""
import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'university_timetable_system.settings')

app = Celery('university_timetable_system')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# ----------------------------------------------------------------------
# Periodic schedule (Celery Beat)
# Frequencies (daily/weekly/monthly) are also stored per-config in the
# database (BackupConfiguration.frequency) and read by
# `dispatch_scheduled_backups` below, so the admin can change frequency
# without redeploying code.
# ----------------------------------------------------------------------
app.conf.beat_schedule = {
    'dispatch-scheduled-backups': {
        'task': 'backup_system.tasks.dispatch_scheduled_backups',
        'schedule': crontab(minute=0),  # checked hourly; dispatches based on each config's frequency
    },
    'replica-health-check': {
        'task': 'backup_system.tasks.replica_health_check',
        'schedule': 30.0,  # seconds; overridden dynamically by health_check_interval in-task
    },
    'process-sync-queue': {
        'task': 'backup_system.tasks.process_sync_queue',
        'schedule': 10.0,  # seconds - drains background backup/sync jobs frequently
    },
    'retry-failed-sync-tasks': {
        'task': 'backup_system.tasks.retry_failed_sync_tasks',
        'schedule': crontab(minute='*/5'),  # every 5 minutes
    },
    'cleanup-expired-backups': {
        'task': 'backup_system.tasks.cleanup_all_expired_backups',
        'schedule': crontab(hour=3, minute=0),  # daily at 3am
    },
    'flush-idle-dvc-batches': {
        'task': 'notifications.tasks.flush_idle_dvc_batches',
        # Checked every 30s; a batch is only sent once its department has gone
        # quiet for IDLE_MINUTES (see notifications/tasks.py), so this being
        # frequent just controls how promptly we notice the DVC went idle.
        'schedule': 30.0,
    },
    'auto-host-remote-sync': {
        'task': 'export_import.tasks.auto_run_sync',
        # Checked every 20s — deliberately as frequent as replica-health-check
        # above. If this only checked every couple of minutes, a remote that
        # wakes up for a short window and goes back to sleep could be missed
        # entirely; checking this often means "just came back online" gets
        # caught (and synced) within seconds, not minutes.
        'schedule': 20.0,
    },
}

app.conf.timezone = 'Africa/Nairobi'


@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
