"""
Celery Tasks — All backup, sync, and replication work happens here,
queued AFTER the request completes so the user is never made to wait.

PythonAnywhere / bare-metal without Redis:
  Celery's @shared_task decorator is used if Celery is installed.
  If the broker is unavailable when .delay() is called, the task
  automatically falls back to a daemon threading.Thread via the
  _safe_delay() wrapper below — so backups still run in the
  background without blocking the HTTP response.

With Redis/Celery:
  Workers pick up tasks normally. Add to docker-compose.yml or systemd:
    celery -A university_timetable_system worker -l info
    celery -A university_timetable_system beat   -l info
"""
import logging
import threading
from datetime import timedelta
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─── Celery-optional import ──────────────────────────────────────────
try:
    from celery import shared_task as _shared_task
    _CELERY_AVAILABLE = True
except ImportError:
    _CELERY_AVAILABLE = False

    # Minimal stub so the decorator syntax still works without Celery installed
    def _shared_task(*args, **kwargs):
        def decorator(fn):
            fn.delay = lambda *a, **kw: _thread_fallback(fn, *a, **kw)
            fn.apply_async = lambda args=(), kwargs={}, **_: _thread_fallback(fn, *args, **kwargs)
            return fn
        return decorator(args[0]) if args and callable(args[0]) else decorator


def _thread_fallback(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) in a daemon thread — used when Celery broker is unreachable."""
    t = threading.Thread(target=_run_in_thread, args=(fn, args, kwargs), daemon=True)
    t.start()
    return t


def _run_in_thread(fn, args, kwargs):
    """Wrapper that sets up Django's DB connection handling for the thread."""
    try:
        import django
        django.setup()
    except RuntimeError:
        pass  # Already set up
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        logger.error(f"Background task {fn.__name__} failed: {exc}", exc_info=True)


def _safe_delay(task_fn, *args, **kwargs):
    """
    Call task_fn.delay(*args) — if the broker is unavailable (e.g. PythonAnywhere
    without Redis), fall back transparently to a daemon thread.
    """
    try:
        return task_fn.delay(*args, **kwargs)
    except Exception as exc:
        logger.warning(
            f"Celery broker unavailable ({exc}); running {task_fn.__name__} in thread."
        )
        return _thread_fallback(task_fn, *args, **kwargs)


from .models import (
    BackupConfiguration, BackupJob, SyncQueue, DisasterRecoveryConfig
)


# ─── Backup tasks ────────────────────────────────────────────────────

@_shared_task(bind=True, max_retries=3, default_retry_delay=300)
def run_scheduled_backup(self, config_id: int):
    """
    Entry point for scheduled and manual backups.
    Triggered by 'Run Backup Now' button or Celery Beat schedule.
    """
    from .services import BackupService

    try:
        config = BackupConfiguration.objects.get(pk=config_id, enabled=True)
    except BackupConfiguration.DoesNotExist:
        logger.warning(f"Backup config {config_id} not found or disabled")
        return

    service = BackupService(config)
    job = service.execute_backup()

    if job.status == 'success':
        _safe_delay(upload_backup_to_destination, job.id)
        _safe_delay(cleanup_old_backups, config_id)
        if config.notify_on_success:
            _safe_delay(send_backup_notification, job.id, success=True)
    else:
        if config.notify_on_failure:
            _safe_delay(send_backup_notification, job.id, success=False)
        if _CELERY_AVAILABLE:
            raise self.retry(exc=Exception(job.error_message))


@_shared_task(bind=True, max_retries=5, default_retry_delay=120)
def upload_backup_to_destination(self, job_id: int):
    """Push a completed local backup to its configured remote destination."""
    from .destination_service import DestinationService, DestinationUploadError

    job = BackupJob.objects.get(pk=job_id)
    if not hasattr(job.config, 'destination'):
        logger.info(f"No destination configured for backup job {job_id}, skipping upload.")
        return

    destination = job.config.destination
    service = DestinationService(destination)
    try:
        service.upload(job)
    except DestinationUploadError as e:
        logger.error(f"Upload failed for backup {job_id}: {e}")
        if _CELERY_AVAILABLE:
            raise self.retry(exc=e)


@_shared_task
def trigger_pre_change_backup(config_id: int, reason: str = 'pre-change'):
    """
    Triggered before deployments, data imports, or .env changes.
    Call from deploy.sh or watch_critical_config management command.
    """
    logger.info(f"Pre-change backup triggered: {reason}")
    _safe_delay(run_scheduled_backup, config_id)


@_shared_task
def cleanup_old_backups(config_id: int):
    """Enforce retention_days by removing expired backup files and records."""
    import os

    config = BackupConfiguration.objects.get(pk=config_id)
    cutoff = timezone.now() - timedelta(days=config.retention_days)

    expired_jobs = BackupJob.objects.filter(
        config=config, created_at__lt=cutoff, status='success'
    )

    for job in expired_jobs:
        try:
            if job.backup_path and os.path.exists(job.backup_path):
                os.remove(job.backup_path)
            key_path = f"{job.backup_path}.key"
            if os.path.exists(key_path):
                os.remove(key_path)
            logger.info(f"Removed expired backup: {job.backup_filename}")
        except OSError as e:
            logger.error(f"Failed to remove expired backup file: {e}")

    count = expired_jobs.count()
    expired_jobs.delete()
    logger.info(f"Cleaned up {count} expired backup(s) for config {config_id}")


@_shared_task
def send_backup_notification(job_id: int, success: bool):
    """Email notification on backup success/failure."""
    from django.core.mail import send_mail

    job = BackupJob.objects.select_related('config').get(pk=job_id)
    config = job.config

    if not config.notification_email:
        return

    subject = f"Backup {'Succeeded' if success else 'FAILED'} — {job.backup_type}"
    body = (
        f"Backup job #{job.id}\n"
        f"Type:      {job.backup_type}\n"
        f"Status:    {job.status}\n"
        f"Started:   {job.started_at}\n"
        f"Completed: {job.completed_at}\n"
        f"Size:      {job.compressed_size} bytes\n"
    )
    if not success:
        body += f"Error: {job.error_message}\n"

    try:
        send_mail(subject, body, from_email=None,
                  recipient_list=[config.notification_email], fail_silently=True)
    except Exception as e:
        logger.error(f"Failed to send backup notification: {e}")


# ─── Disaster recovery / sync tasks ──────────────────────────────────

@_shared_task
def process_sync_queue():
    """
    Drains SyncQueue. Run periodically via Celery Beat or a cron job:
      * * * * * cd /path/to/project && python manage.py shell -c
        "from backup_system.tasks import process_sync_queue; process_sync_queue()"
    """
    from .disaster_recovery_service import DisasterRecoveryService

    pending = SyncQueue.objects.filter(status='pending').order_by('created_at')[:50]
    dr_config = DisasterRecoveryConfig.objects.first()

    for task in pending:
        if task.task_type == 'sync_replica' and dr_config:
            service = DisasterRecoveryService(dr_config)
            service.execute_sync(task)
        elif task.task_type == 'backup':
            backup_job_id = task.payload.get('backup_job_id')
            if backup_job_id:
                task.status = 'processing'
                task.save()
                _safe_delay(upload_backup_to_destination, backup_job_id)
                task.status = 'completed'
                task.completed_at = timezone.now()
                task.save()
        elif task.task_type == 'health_check' and dr_config:
            service = DisasterRecoveryService(dr_config)
            service.check_replica_health()
            task.status = 'completed'
            task.completed_at = timezone.now()
            task.save()


@_shared_task
def replica_health_check():
    """Periodic health check of the DR replica."""
    from .disaster_recovery_service import DisasterRecoveryService

    dr_config = DisasterRecoveryConfig.objects.filter(health_check_enabled=True).first()
    if dr_config:
        service = DisasterRecoveryService(dr_config)
        service.check_replica_health()


@_shared_task
def dispatch_scheduled_backups():
    """
    Hourly task: check every enabled config and dispatch if due.
    Lets the admin change frequency from the UI without restarting Beat.
    """
    now = timezone.now()
    for config in BackupConfiguration.objects.filter(enabled=True).exclude(frequency='manual'):
        if config.last_backup_time is None:
            _safe_delay(run_scheduled_backup, config.id)
            continue
        elapsed = now - config.last_backup_time
        due = (
            (config.frequency == 'daily'   and elapsed >= timedelta(days=1))   or
            (config.frequency == 'weekly'  and elapsed >= timedelta(weeks=1))  or
            (config.frequency == 'monthly' and elapsed >= timedelta(days=30))
        )
        if due:
            _safe_delay(run_scheduled_backup, config.id)
            logger.info(f"Dispatched {config.frequency} backup for config {config.id}")


@_shared_task
def cleanup_all_expired_backups():
    """Daily: run retention cleanup across every backup configuration."""
    for config in BackupConfiguration.objects.all():
        _safe_delay(cleanup_old_backups, config.id)


@_shared_task
def retry_failed_sync_tasks():
    """Periodic: retry sync tasks that failed but haven't hit max_retries."""
    from django.db.models import F
    failed = SyncQueue.objects.filter(status='failed', retry_count__lt=F('max_retries'))
    for task in failed:
        task.status = 'pending'
        task.save()
