"""
Backup, Disaster Recovery, and Audit System Models
Core data structures for backup management, replication, and undo functionality
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import json
import hashlib


class BackupConfiguration(models.Model):
    """Admin configuration for backup behavior"""
    
    BACKUP_TYPE_CHOICES = [
        ('database', 'Database Backup'),
        ('files', 'File Backup'),
        ('full', 'Full System Backup'),
        ('config', 'Configuration Backup'),
    ]
    
    FREQUENCY_CHOICES = [
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('manual', 'Manual Only'),
    ]
    
    COMPRESSION_CHOICES = [
        ('none', 'None'),
        ('gzip', 'GZIP'),
        ('zip', 'ZIP'),
        ('bzip2', 'BZIP2'),
    ]
    
    # Enable/disable backups
    enabled = models.BooleanField(default=True)
    
    # Backup type
    backup_type = models.CharField(
        max_length=20,
        choices=BACKUP_TYPE_CHOICES,
        default='full'
    )
    
    # Frequency
    frequency = models.CharField(
        max_length=20,
        choices=FREQUENCY_CHOICES,
        default='daily'
    )
    
    # Retention policy (in days)
    retention_days = models.IntegerField(default=30)
    
    # Compression
    compression = models.CharField(
        max_length=20,
        choices=COMPRESSION_CHOICES,
        default='gzip'
    )
    
    # Encryption
    encryption_enabled = models.BooleanField(default=True)
    encryption_algorithm = models.CharField(max_length=50, default='AES-256')
    
    # Notification settings
    notify_on_success = models.BooleanField(default=True)
    notify_on_failure = models.BooleanField(default=True)
    notification_email = models.EmailField(blank=True)
    
    # Last backup
    last_backup_time = models.DateTimeField(null=True, blank=True)
    last_backup_status = models.CharField(
        max_length=20,
        choices=[('success', 'Success'), ('failed', 'Failed'), ('pending', 'Pending')],
        default='pending'
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    class Meta:
        db_table = 'backup_configuration'
        verbose_name = 'Backup Configuration'
        verbose_name_plural = 'Backup Configurations'
    
    def __str__(self):
        return f"Backup Config - {self.backup_type} ({self.frequency})"


class BackupDestination(models.Model):
    """Storage destinations for backups (local, S3, FTP, etc.)"""
    
    DESTINATION_TYPE_CHOICES = [
        ('local', 'Local Storage'),
        ('s3', 'AWS S3'),
        ('azure', 'Azure Blob Storage'),
        ('gcs', 'Google Cloud Storage'),
        ('ftp', 'FTP/SFTP'),
        ('webhook', 'Webhook/HTTP'),
    ]
    
    config = models.OneToOneField(
        BackupConfiguration,
        on_delete=models.CASCADE,
        related_name='destination'
    )
    
    destination_type = models.CharField(
        max_length=20,
        choices=DESTINATION_TYPE_CHOICES,
        default='local'
    )
    
    # Local storage
    local_path = models.CharField(
        max_length=500,
        blank=True,
        help_text='/backups/automated'
    )
    
    # S3 Configuration
    s3_bucket = models.CharField(max_length=255, blank=True)
    s3_region = models.CharField(max_length=50, blank=True, default='us-east-1')
    s3_access_key = models.CharField(max_length=255, blank=True)
    s3_secret_key = models.CharField(max_length=255, blank=True)
    s3_prefix = models.CharField(max_length=255, blank=True, default='backups/')
    
    # Azure Configuration
    azure_container = models.CharField(max_length=255, blank=True)
    azure_account_name = models.CharField(max_length=255, blank=True)
    azure_account_key = models.CharField(max_length=255, blank=True)
    
    # GCS Configuration
    gcs_bucket = models.CharField(max_length=255, blank=True)
    gcs_project_id = models.CharField(max_length=255, blank=True)
    gcs_service_account = models.JSONField(default=dict, blank=True)
    
    # FTP Configuration
    ftp_host = models.CharField(max_length=255, blank=True)
    ftp_port = models.IntegerField(default=21, blank=True)
    ftp_username = models.CharField(max_length=255, blank=True)
    ftp_password = models.CharField(max_length=255, blank=True)
    ftp_path = models.CharField(max_length=500, blank=True)
    ftp_use_sftp = models.BooleanField(default=True)
    
    # Webhook Configuration
    webhook_url = models.URLField(blank=True)
    webhook_auth_header = models.CharField(max_length=255, blank=True)
    webhook_verify_ssl = models.BooleanField(default=True)
    
    # Test configuration
    last_test_time = models.DateTimeField(null=True, blank=True)
    last_test_status = models.BooleanField(null=True, blank=True)
    last_test_message = models.TextField(blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'backup_destination'
        verbose_name = 'Backup Destination'
    
    def __str__(self):
        return f"{self.destination_type.upper()} - {self.get_location()}"
    
    def get_location(self):
        """Get human-readable location"""
        if self.destination_type == 'local':
            return self.local_path
        elif self.destination_type == 's3':
            return f"s3://{self.s3_bucket}/{self.s3_prefix}"
        elif self.destination_type == 'azure':
            return f"{self.azure_account_name}/{self.azure_container}"
        elif self.destination_type == 'gcs':
            return f"gs://{self.gcs_bucket}"
        elif self.destination_type == 'ftp':
            return f"{self.ftp_host}:{self.ftp_path}"
        elif self.destination_type == 'webhook':
            return self.webhook_url
        return 'Unknown'


class BackupJob(models.Model):
    """Individual backup job execution record"""
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('partial', 'Partial Success'),
    ]
    
    config = models.ForeignKey(
        BackupConfiguration,
        on_delete=models.CASCADE,
        related_name='jobs'
    )
    
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    
    backup_type = models.CharField(max_length=50)
    
    # Size information (in bytes)
    total_size = models.BigIntegerField(default=0)
    compressed_size = models.BigIntegerField(default=0)
    
    # File hash for integrity verification
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    
    # Timing
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(default=0)
    
    # Files/records backed up
    database_records = models.IntegerField(default=0)
    files_count = models.IntegerField(default=0)
    
    # Error tracking
    error_message = models.TextField(blank=True)
    error_details = models.JSONField(default=dict, blank=True)
    
    # Metadata
    backup_filename = models.CharField(max_length=500, blank=True)
    backup_path = models.CharField(max_length=1000, blank=True)
    initiated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='backup_jobs_initiated'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'backup_job'
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['config', 'created_at']),
        ]
        verbose_name = 'Backup Job'
        verbose_name_plural = 'Backup Jobs'
    
    def __str__(self):
        return f"Backup {self.id} - {self.status} ({self.created_at.date()})"
    
    def duration_minutes(self):
        if self.completed_at and self.started_at:
            delta = self.completed_at - self.started_at
            return delta.total_seconds() / 60
        return 0


class DisasterRecoveryConfig(models.Model):
    """Disaster Recovery and Replication Configuration"""
    
    REPLICATION_MODE_CHOICES = [
        ('async', 'Asynchronous'),
        ('sync', 'Synchronous'),
    ]
    
    # Primary database
    primary_host = models.CharField(max_length=255)
    primary_port = models.IntegerField()
    primary_db_name = models.CharField(max_length=255)
    
    # Secondary/Replica database
    replica_enabled = models.BooleanField(default=False)
    replica_host = models.CharField(max_length=255, blank=True)
    replica_port = models.IntegerField(blank=True)
    replica_db_name = models.CharField(max_length=255, blank=True)
    
    # Replication settings
    replication_mode = models.CharField(
        max_length=20,
        choices=REPLICATION_MODE_CHOICES,
        default='async'
    )
    
    # Health check
    health_check_interval = models.IntegerField(
        default=30,
        help_text='Seconds between health checks'
    )
    health_check_enabled = models.BooleanField(default=True)
    
    # Failover settings
    auto_failover = models.BooleanField(default=False)
    failover_timeout = models.IntegerField(default=60, help_text='Seconds')
    
    # Status
    replica_status = models.CharField(
        max_length=50,
        choices=[('online', 'Online'), ('offline', 'Offline'), ('syncing', 'Syncing')],
        default='offline'
    )
    last_sync_time = models.DateTimeField(null=True, blank=True)
    last_health_check = models.DateTimeField(null=True, blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    class Meta:
        db_table = 'disaster_recovery_config'
        verbose_name = 'Disaster Recovery Config'
    
    def __str__(self):
        return f"DR Config - Replica {'Enabled' if self.replica_enabled else 'Disabled'}"


class AuditLog(models.Model):
    """Immutable audit trail for all changes"""
    
    ACTION_CHOICES = [
        ('create', 'Create'),
        ('update', 'Update'),
        ('delete', 'Delete'),
        ('restore', 'Restore'),
        ('bulk_action', 'Bulk Action'),
    ]
    
    # Action information
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    table_name = models.CharField(max_length=255)
    record_id = models.IntegerField()
    
    # Change data
    before_data = models.JSONField(default=dict, blank=True)
    after_data = models.JSONField(default=dict, blank=True)
    changed_fields = models.JSONField(default=list, blank=True)
    
    # User information
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    
    # Request information
    request_id = models.CharField(max_length=255, blank=True)
    session_id = models.CharField(max_length=255, blank=True)
    
    # Timestamp and integrity
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    
    # Hash of record for integrity verification
    record_hash = models.CharField(max_length=64, editable=False)
    
    # Metadata
    description = models.TextField(blank=True)
    
    class Meta:
        db_table = 'audit_log'
        indexes = [
            models.Index(fields=['table_name', 'record_id', 'timestamp']),
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['action', 'timestamp']),
        ]
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'
    
    def __str__(self):
        return f"{self.action.upper()} {self.table_name}:{self.record_id} by {self.user}"
    
    def save(self, *args, **kwargs):
        """Generate integrity hash"""
        if not self.record_hash:
            hash_input = f"{self.action}{self.table_name}{self.record_id}{self.timestamp}{str(self.after_data)}"
            self.record_hash = hashlib.sha256(hash_input.encode()).hexdigest()
        super().save(*args, **kwargs)


class UndoAction(models.Model):
    """Undo/Rollback action tracking"""
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    # Audit logs being undone
    audit_logs = models.ManyToManyField(AuditLog, related_name='undo_actions')
    
    # Undo information
    undo_type = models.CharField(
        max_length=50,
        choices=[
            ('single', 'Single Action'),
            ('multiple', 'Multiple Actions'),
            ('time_range', 'Time Range'),
        ]
    )
    
    # Time-based undo
    undo_start_time = models.DateTimeField(null=True, blank=True)
    undo_end_time = models.DateTimeField(null=True, blank=True)
    
    # Status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    
    # User information
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='undo_actions_approved'
    )
    
    # Execution
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    
    # Records affected
    records_affected = models.IntegerField(default=0)
    
    # Metadata
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'undo_action'
        indexes = [
            models.Index(fields=['requested_by', 'created_at']),
            models.Index(fields=['status', 'created_at']),
        ]
        verbose_name = 'Undo Action'
        verbose_name_plural = 'Undo Actions'
    
    def __str__(self):
        return f"Undo {self.id} - {self.undo_type} ({self.status})"


class BackupRestoreLog(models.Model):
    """Log of all restore operations"""
    
    STATUS_CHOICES = [
        ('requested', 'Requested'),
        ('approved', 'Approved'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    backup_job = models.ForeignKey(
        BackupJob,
        on_delete=models.CASCADE,
        related_name='restore_operations'
    )
    
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='requested'
    )
    
    # Request information
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='restore_operations_approved'
    )
    
    # Restore target
    restore_to_database = models.CharField(
        max_length=255,
        default='primary',
        help_text='Database to restore to'
    )
    
    # Execution
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(default=0)
    
    # Records affected
    records_restored = models.IntegerField(default=0)
    
    # Error tracking
    error_message = models.TextField(blank=True)
    error_details = models.JSONField(default=dict, blank=True)
    
    # Metadata
    description = models.TextField(blank=True)
    request_ip = models.GenericIPAddressField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'backup_restore_log'
        indexes = [
            models.Index(fields=['requested_by', 'created_at']),
            models.Index(fields=['backup_job', 'created_at']),
        ]
        verbose_name = 'Backup Restore Log'
        verbose_name_plural = 'Backup Restore Logs'
    
    def __str__(self):
        return f"Restore {self.id} - {self.status}"


class SyncQueue(models.Model):
    """Background synchronization queue"""
    
    TASK_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    # Task information
    task_type = models.CharField(
        max_length=50,
        choices=[
            ('backup', 'Backup'),
            ('sync_replica', 'Sync Replica'),
            ('health_check', 'Health Check'),
            ('restore', 'Restore'),
        ]
    )
    
    status = models.CharField(
        max_length=20,
        choices=TASK_STATUS_CHOICES,
        default='pending'
    )
    
    # Payload
    payload = models.JSONField(default=dict)
    
    # Execution
    retry_count = models.IntegerField(default=0)
    max_retries = models.IntegerField(default=3)
    
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Error tracking
    error_message = models.TextField(blank=True)
    last_error = models.TextField(blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'sync_queue'
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['task_type', 'status']),
        ]
        verbose_name = 'Sync Queue'
        verbose_name_plural = 'Sync Queues'
    
    def __str__(self):
        return f"{self.task_type} - {self.status}"
