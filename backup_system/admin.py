from django.contrib import admin
from .models import (
    BackupConfiguration, BackupDestination, BackupJob,
    DisasterRecoveryConfig, AuditLog, UndoAction,
    BackupRestoreLog, SyncQueue
)


@admin.register(BackupConfiguration)
class BackupConfigurationAdmin(admin.ModelAdmin):
    list_display = ('backup_type', 'frequency', 'enabled', 'retention_days',
                     'last_backup_status', 'last_backup_time')
    list_filter = ('backup_type', 'frequency', 'enabled', 'last_backup_status')
    readonly_fields = ('last_backup_time', 'last_backup_status', 'created_at', 'updated_at')


@admin.register(BackupDestination)
class BackupDestinationAdmin(admin.ModelAdmin):
    list_display = ('config', 'destination_type', 'last_test_status', 'last_test_time')
    list_filter = ('destination_type', 'last_test_status')


@admin.register(BackupJob)
class BackupJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'backup_type', 'status', 'total_size', 'compressed_size',
                     'created_at', 'duration_seconds')
    list_filter = ('status', 'backup_type', 'created_at')
    readonly_fields = ('checksum_sha256', 'created_at', 'updated_at')
    search_fields = ('backup_filename',)

    def has_delete_permission(self, request, obj=None):
        # Deletion must go through the audited delete_backup view,
        # not the raw admin, to keep the audit trail intact.
        return request.user.is_superuser


@admin.register(DisasterRecoveryConfig)
class DisasterRecoveryConfigAdmin(admin.ModelAdmin):
    list_display = ('primary_host', 'replica_enabled', 'replica_host',
                     'replication_mode', 'replica_status', 'last_sync_time')
    readonly_fields = ('replica_status', 'last_sync_time', 'last_health_check')


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'action', 'table_name', 'record_id', 'user', 'ip_address')
    list_filter = ('action', 'table_name', 'timestamp')
    search_fields = ('table_name', 'user__username')
    readonly_fields = [f.name for f in AuditLog._meta.fields]  # fully immutable in admin

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # audit logs are immutable - delete only via retention policy task


@admin.register(UndoAction)
class UndoActionAdmin(admin.ModelAdmin):
    list_display = ('id', 'undo_type', 'status', 'requested_by',
                     'records_affected', 'created_at')
    list_filter = ('undo_type', 'status', 'created_at')
    readonly_fields = ('created_at', 'updated_at', 'started_at', 'completed_at')


@admin.register(BackupRestoreLog)
class BackupRestoreLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'backup_job', 'status', 'requested_by',
                     'request_ip', 'created_at')
    list_filter = ('status', 'created_at')
    readonly_fields = [f.name for f in BackupRestoreLog._meta.fields]

    def has_delete_permission(self, request, obj=None):
        return False  # restore logs are immutable for security audit purposes


@admin.register(SyncQueue)
class SyncQueueAdmin(admin.ModelAdmin):
    list_display = ('id', 'task_type', 'status', 'retry_count', 'created_at')
    list_filter = ('task_type', 'status')
