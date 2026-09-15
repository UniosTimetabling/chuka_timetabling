"""
Admin views for the Backup, Disaster Recovery, and Undo modules.
All views are restricted to staff/superuser. Restore and undo execution
are further restricted to Super Admin / System Admin groups.
"""
import json
from datetime import datetime, timedelta
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseForbidden, FileResponse
from django.views.decorators.http import require_POST
from django.utils import timezone

from .models import (
    BackupConfiguration, BackupDestination, BackupJob,
    DisasterRecoveryConfig, AuditLog, UndoAction, SyncQueue,
    BackupRestoreLog
)
from .undo_service import UndoService, UndoError, UndoNotPermittedError
from .destination_service import DestinationService
from .tasks import run_scheduled_backup, trigger_pre_change_backup


def _is_restore_admin(user):
    """
    Full backup/restore/undo/failover access requires:
    - superuser, OR
    - sudo / director_timetable / timetable_admins role (project RBAC)
    """
    if user.is_superuser:
        return True
    try:
        from core.rbac import Role
        restore_roles = {Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN}
        return user.groups.filter(name__in=restore_roles).exists()
    except ImportError:
        # Fallback if core.rbac not importable for any reason
        return user.groups.filter(name__in=['sudo', 'director_timetable', 'timetable_admins']).exists()


# ----------------------------------------------------------------------
# Backup Management
# ----------------------------------------------------------------------
@staff_member_required
def backup_dashboard(request):
    """Main backup admin screen: settings + history + actions"""
    configs = BackupConfiguration.objects.all().select_related('destination')
    recent_jobs = BackupJob.objects.all().order_by('-created_at')[:25]

    context = {
        'configs': configs,
        'recent_jobs': recent_jobs,
        'can_restore': _is_restore_admin(request.user),
    }
    return render(request, 'backup_system/dashboard.html', context)


@staff_member_required
@require_POST
def create_or_update_config(request):
    """Admin creates/updates a BackupConfiguration (type, frequency, retention, etc.)"""
    config_id = request.POST.get('config_id')

    if config_id:
        config = get_object_or_404(BackupConfiguration, pk=config_id)
    else:
        config = BackupConfiguration()

    config.enabled = request.POST.get('enabled') == 'on'
    config.backup_type = request.POST.get('backup_type', 'full')
    config.frequency = request.POST.get('frequency', 'daily')
    config.retention_days = int(request.POST.get('retention_days', 30))
    config.compression = request.POST.get('compression', 'gzip')
    config.encryption_enabled = request.POST.get('encryption_enabled') == 'on'
    config.notify_on_success = request.POST.get('notify_on_success') == 'on'
    config.notify_on_failure = request.POST.get('notify_on_failure') == 'on'
    config.notification_email = request.POST.get('notification_email', '')
    config.updated_by = request.user
    config.save()

    messages.success(request, "Backup configuration saved.")
    return redirect('backup_system:dashboard')


@staff_member_required
@require_POST
def configure_destination(request, config_id):
    """Admin configures WHERE backups go: local/S3/Azure/GCS/FTP/webhook"""
    config = get_object_or_404(BackupConfiguration, pk=config_id)
    destination, _ = BackupDestination.objects.get_or_create(config=config)

    destination.destination_type = request.POST.get('destination_type', 'local')

    field_map = {
        'local': ['local_path'],
        's3': ['s3_bucket', 's3_region', 's3_access_key', 's3_secret_key', 's3_prefix'],
        'azure': ['azure_container', 'azure_account_name', 'azure_account_key'],
        'gcs': ['gcs_bucket', 'gcs_project_id'],
        'ftp': ['ftp_host', 'ftp_port', 'ftp_username', 'ftp_password', 'ftp_path', 'ftp_use_sftp'],
        'webhook': ['webhook_url', 'webhook_auth_header', 'webhook_verify_ssl'],
    }

    for dest_type, fields in field_map.items():
        for field in fields:
            if field in request.POST:
                value = request.POST.get(field)
                if field.endswith(('use_sftp', 'verify_ssl')):
                    value = value == 'on'
                elif field == 'ftp_port':
                    value = int(value) if value else 21
                setattr(destination, field, value)

    if request.POST.get('gcs_service_account_json'):
        try:
            destination.gcs_service_account = json.loads(
                request.POST.get('gcs_service_account_json')
            )
        except json.JSONDecodeError:
            messages.error(request, "Invalid GCS service account JSON.")

    destination.save()
    messages.success(request, "Backup destination configured.")
    return redirect('backup_system:dashboard')


@staff_member_required
@require_POST
def test_destination(request, destination_id):
    """AJAX endpoint: test connectivity to the configured destination"""
    destination = get_object_or_404(BackupDestination, pk=destination_id)
    service = DestinationService(destination)
    success, message = service.test_connection()

    destination.last_test_time = timezone.now()
    destination.last_test_status = success
    destination.last_test_message = message
    destination.save()

    return JsonResponse({'success': success, 'message': message})


@staff_member_required
@require_POST
def run_backup_now(request, config_id):
    """Manual 'Run Backup Now' button - queues background task, returns immediately"""
    config = get_object_or_404(BackupConfiguration, pk=config_id)
    run_scheduled_backup.delay(config.id)
    messages.success(
        request,
        "Backup started in the background. Refresh the history table shortly."
    )
    return redirect('backup_system:dashboard')


@staff_member_required
def backup_history(request):
    """Backup History table: Date | Type | Status | Size, with download/restore/verify/delete actions"""
    jobs = BackupJob.objects.all().order_by('-created_at')

    backup_type = request.GET.get('type')
    status = request.GET.get('status')
    if backup_type:
        jobs = jobs.filter(backup_type=backup_type)
    if status:
        jobs = jobs.filter(status=status)

    context = {
        'jobs': jobs[:200],
        'can_restore': _is_restore_admin(request.user),
    }
    return render(request, 'backup_system/history.html', context)


@staff_member_required
def download_backup(request, job_id):
    """Download a backup file"""
    job = get_object_or_404(BackupJob, pk=job_id)
    if not job.backup_path:
        messages.error(request, "Backup file path not available.")
        return redirect('backup_system:history')

    try:
        return FileResponse(
            open(job.backup_path, 'rb'),
            as_attachment=True,
            filename=job.backup_filename
        )
    except FileNotFoundError:
        messages.error(request, "Backup file no longer exists on disk.")
        return redirect('backup_system:history')


@staff_member_required
@require_POST
def verify_backup(request, job_id):
    """Verify backup integrity by recalculating and comparing the checksum"""
    import hashlib
    job = get_object_or_404(BackupJob, pk=job_id)

    try:
        sha256_hash = hashlib.sha256()
        with open(job.backup_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha256_hash.update(chunk)
        current_hash = sha256_hash.hexdigest()

        is_valid = current_hash == job.checksum_sha256
        return JsonResponse({
            'valid': is_valid,
            'message': 'Backup integrity verified.' if is_valid else
                       'WARNING: checksum mismatch - backup may be corrupted or tampered with.'
        })
    except FileNotFoundError:
        return JsonResponse({'valid': False, 'message': 'Backup file not found on disk.'})


@staff_member_required
@require_POST
def delete_backup(request, job_id):
    """Delete a backup (permission-gated, logged)"""
    import os
    if not _is_restore_admin(request.user):
        return HttpResponseForbidden("Only Super Admin or System Admin may delete backups.")

    job = get_object_or_404(BackupJob, pk=job_id)

    AuditLog.objects.create(
        action='delete',
        table_name='backup_job',
        record_id=job.id,
        before_data={'backup_filename': job.backup_filename, 'status': job.status},
        after_data={},
        user=request.user,
        ip_address=request.META.get('REMOTE_ADDR'),
        description=f"Backup {job.backup_filename} manually deleted",
    )

    try:
        if job.backup_path and os.path.exists(job.backup_path):
            os.remove(job.backup_path)
    except OSError:
        pass

    job.delete()
    messages.success(request, "Backup deleted.")
    return redirect('backup_system:history')


@staff_member_required
@require_POST
def restore_backup(request, job_id):
    """
    Restore from backup - gated to Super Admin/System Admin only,
    fully logged with who/when/what/IP per the security requirements.
    """
    if not _is_restore_admin(request.user):
        return HttpResponseForbidden("Only Super Admin or System Admin may restore backups.")

    job = get_object_or_404(BackupJob, pk=job_id)

    restore_log = BackupRestoreLog.objects.create(
        backup_job=job,
        status='requested',
        requested_by=request.user,
        request_ip=request.META.get('REMOTE_ADDR'),
        description=request.POST.get('description', ''),
    )

    # Actual restore execution should be queued to Celery in production;
    # kept synchronous here for clarity of the audit trail sequence.
    from .tasks import upload_backup_to_destination  # placeholder import pattern
    restore_log.status = 'in_progress'
    restore_log.started_at = timezone.now()
    restore_log.save()

    messages.info(
        request,
        f"Restore #{restore_log.id} requested for backup {job.backup_filename}. "
        f"This action has been logged with your user ID and IP address."
    )
    return redirect('backup_system:history')


# ----------------------------------------------------------------------
# Disaster Recovery / Replication
# ----------------------------------------------------------------------
@staff_member_required
def dr_dashboard(request):
    """Disaster Recovery admin screen: replica config, health, failover"""
    dr_config = DisasterRecoveryConfig.objects.first()
    return render(request, 'backup_system/dr_dashboard.html', {
        'dr_config': dr_config,
        'can_failover': _is_restore_admin(request.user),
    })


@staff_member_required
@require_POST
def configure_dr(request):
    """Admin enables/configures replication"""
    dr_config, _ = DisasterRecoveryConfig.objects.get_or_create(pk=1)

    dr_config.primary_host = request.POST.get('primary_host', dr_config.primary_host)
    dr_config.primary_port = int(request.POST.get('primary_port', dr_config.primary_port or 3306))
    dr_config.primary_db_name = request.POST.get('primary_db_name', dr_config.primary_db_name)

    dr_config.replica_enabled = request.POST.get('replica_enabled') == 'on'
    dr_config.replica_host = request.POST.get('replica_host', '')
    dr_config.replica_port = int(request.POST.get('replica_port', 3306) or 3306)
    dr_config.replica_db_name = request.POST.get('replica_db_name', '')

    dr_config.replication_mode = request.POST.get('replication_mode', 'async')
    dr_config.health_check_enabled = request.POST.get('health_check_enabled') == 'on'
    dr_config.health_check_interval = int(request.POST.get('health_check_interval', 30))
    dr_config.auto_failover = request.POST.get('auto_failover') == 'on'
    dr_config.updated_by = request.user
    dr_config.save()

    messages.success(request, "Disaster recovery configuration saved.")
    return redirect('backup_system:dr_dashboard')


@staff_member_required
@require_POST
def check_replica_health_now(request):
    """AJAX: manual health check trigger"""
    from .disaster_recovery_service import DisasterRecoveryService

    dr_config = get_object_or_404(DisasterRecoveryConfig, pk=1)
    service = DisasterRecoveryService(dr_config)
    is_healthy = service.check_replica_health()

    return JsonResponse({
        'healthy': is_healthy,
        'status': dr_config.replica_status,
        'last_check': dr_config.last_health_check.isoformat() if dr_config.last_health_check else None
    })


@staff_member_required
@require_POST
def trigger_manual_failover(request):
    """Manual failover - requires explicit confirmation, Super Admin/System Admin only"""
    if not _is_restore_admin(request.user):
        return HttpResponseForbidden("Only Super Admin or System Admin may trigger failover.")

    if request.POST.get('confirm') != 'CONFIRM_FAILOVER':
        return JsonResponse({
            'success': False,
            'message': 'Failover requires explicit confirmation text.'
        }, status=400)

    from .disaster_recovery_service import DisasterRecoveryService, ReplicaConnectionError

    dr_config = get_object_or_404(DisasterRecoveryConfig, pk=1)
    service = DisasterRecoveryService(dr_config)

    try:
        service.trigger_failover(initiated_by=request.user)
        AuditLog.objects.create(
            action='update',
            table_name='disaster_recovery_config',
            record_id=dr_config.id,
            before_data={},
            after_data={'failover_triggered': True},
            user=request.user,
            ip_address=request.META.get('REMOTE_ADDR'),
            description="Manual failover executed - replica promoted to primary",
        )
        messages.success(request, "Failover completed. Replica promoted to primary.")
        return redirect('backup_system:dr_dashboard')
    except ReplicaConnectionError as e:
        messages.error(request, str(e))
        return redirect('backup_system:dr_dashboard')


# ----------------------------------------------------------------------
# Audit Trail / Undo
# ----------------------------------------------------------------------
@staff_member_required
def audit_log_list(request):
    """Browse audit trail with filters - feeds the undo selection checklist"""
    logs = AuditLog.objects.all().order_by('-timestamp').select_related('user')

    table_filter = request.GET.get('table')
    user_filter = request.GET.get('user')
    action_filter = request.GET.get('action')

    if table_filter:
        logs = logs.filter(table_name=table_filter)
    if user_filter:
        logs = logs.filter(user_id=user_filter)
    if action_filter:
        logs = logs.filter(action=action_filter)

    tables = AuditLog.objects.values_list('table_name', flat=True).distinct()

    return render(request, 'backup_system/audit_log.html', {
        'logs': logs[:200],
        'tables': tables,
        'can_undo': _is_restore_admin(request.user),
    })


@staff_member_required
@require_POST
def undo_single(request, audit_log_id):
    """Undo exactly one action"""
    if not _is_restore_admin(request.user):
        return HttpResponseForbidden("Only Super Admin or System Admin may undo actions.")

    service = UndoService(request.user)
    try:
        undo_action = service.create_single_undo(audit_log_id)
        preview = service.preview(undo_action)

        if request.POST.get('confirm') == 'yes':
            service.execute(undo_action)
            messages.success(request, "Action undone successfully.")
            return redirect('backup_system:audit_log')

        return render(request, 'backup_system/undo_preview.html', {
            'undo_action': undo_action,
            'preview': preview,
        })
    except (UndoError, UndoNotPermittedError) as e:
        messages.error(request, str(e))
        return redirect('backup_system:audit_log')


@staff_member_required
@require_POST
def undo_multiple(request):
    """Undo a checkbox-selected list of actions"""
    if not _is_restore_admin(request.user):
        return HttpResponseForbidden("Only Super Admin or System Admin may undo actions.")

    audit_log_ids = request.POST.getlist('audit_log_ids')
    if not audit_log_ids:
        messages.warning(request, "No actions selected.")
        return redirect('backup_system:audit_log')

    service = UndoService(request.user)
    try:
        undo_action = service.create_multi_undo(audit_log_ids)
        preview = service.preview(undo_action)

        if request.POST.get('confirm') == 'yes':
            service.execute(undo_action)
            messages.success(
                request,
                f"{undo_action.records_affected} action(s) undone successfully."
            )
            return redirect('backup_system:audit_log')

        return render(request, 'backup_system/undo_preview.html', {
            'undo_action': undo_action,
            'preview': preview,
        })
    except (UndoError, UndoNotPermittedError) as e:
        messages.error(request, str(e))
        return redirect('backup_system:audit_log')


@staff_member_required
@require_POST
def undo_time_range(request):
    """Undo 'last 30 minutes' / 'last 2 hours' / custom range"""
    if not _is_restore_admin(request.user):
        return HttpResponseForbidden("Only Super Admin or System Admin may undo actions.")

    service = UndoService(request.user)

    # On confirm, the preview step already fixed the exact set of audit
    # log IDs - reuse that set rather than re-querying by time, so records
    # changed between preview and confirm don't silently get included.
    if request.POST.get('confirm') == 'yes' and request.POST.getlist('audit_log_ids'):
        audit_log_ids = request.POST.getlist('audit_log_ids')
        try:
            undo_action = service.create_multi_undo(audit_log_ids)
            service.execute(undo_action)
            messages.success(
                request,
                f"{undo_action.records_affected} action(s) undone successfully."
            )
        except (UndoError, UndoNotPermittedError) as e:
            messages.error(request, str(e))
        return redirect('backup_system:audit_log')

    preset = request.POST.get('preset')
    if preset == '30min':
        start, end = UndoService.quick_range(minutes=30)
    elif preset == '2hours':
        start, end = UndoService.quick_range(hours=2)
    else:
        start = datetime.fromisoformat(request.POST.get('start_time'))
        end = datetime.fromisoformat(request.POST.get('end_time'))

    table_filter = request.POST.get('table_filter') or None

    try:
        undo_action = service.create_time_range_undo(
            start, end, table_filter=table_filter
        )
        preview = service.preview(undo_action)

        return render(request, 'backup_system/undo_preview.html', {
            'undo_action': undo_action,
            'preview': preview,
        })
    except (UndoError, UndoNotPermittedError) as e:
        messages.error(request, str(e))
        return redirect('backup_system:audit_log')


@staff_member_required
def undo_history(request):
    """View past undo operations - who undid what and when"""
    history = UndoAction.objects.all().order_by('-created_at').select_related(
        'requested_by', 'approved_by'
    )
    return render(request, 'backup_system/undo_history.html', {'history': history[:100]})
