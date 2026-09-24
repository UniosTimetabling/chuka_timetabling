"""
Disaster Recovery Service - Replica health checks, sync, and failover
Implements both async and sync replication modes
"""
import logging
import subprocess
from datetime import datetime
from django.db import connections
from django.conf import settings
from django.utils import timezone
from .models import DisasterRecoveryConfig, SyncQueue

logger = logging.getLogger(__name__)


class ReplicaConnectionError(Exception):
    pass


class DisasterRecoveryService:
    """Manages replica health checks, replication, and failover"""

    def __init__(self, config: DisasterRecoveryConfig):
        self.config = config

    def check_replica_health(self) -> bool:
        """Ping the replica database to verify it's reachable and responsive"""
        if not self.config.replica_enabled:
            return False

        try:
            import pymysql  # works for MySQL; for Postgres use psycopg2 similarly
            conn = pymysql.connect(
                host=self.config.replica_host,
                port=self.config.replica_port,
                connect_timeout=5,
            )
            conn.close()
            self.config.replica_status = 'online'
            self.config.last_health_check = timezone.now()
            self.config.save()
            return True
        except Exception as e:
            logger.warning(f"Replica health check failed: {str(e)}")
            self.config.replica_status = 'offline'
            self.config.last_health_check = timezone.now()
            self.config.save()

            if self.config.auto_failover:
                self._trigger_failover_alert()

            return False

    def sync_to_replica(self, table_name: str = None, record_id: int = None):
        """
        Queue a sync operation to replicate changes to the replica database.
        Called from signal handlers AFTER the primary write completes,
        so it never blocks or delays the user's request.
        """
        if not self.config.replica_enabled:
            return

        SyncQueue.objects.create(
            task_type='sync_replica',
            status='pending',
            payload={
                'table_name': table_name,
                'record_id': record_id,
                'replication_mode': self.config.replication_mode,
                'queued_at': timezone.now().isoformat(),
            }
        )

    def execute_sync(self, sync_task: SyncQueue):
        """
        Execute a queued sync task. This is called by the Celery worker,
        never inline with a user request.
        """
        sync_task.status = 'processing'
        sync_task.started_at = timezone.now()
        sync_task.save()

        try:
            if self.config.replication_mode == 'async':
                self._async_replicate(sync_task)
            else:
                self._sync_replicate(sync_task)

            sync_task.status = 'completed'
            sync_task.completed_at = timezone.now()
            self.config.last_sync_time = timezone.now()
            self.config.save()

        except Exception as e:
            logger.error(f"Sync task {sync_task.id} failed: {str(e)}", exc_info=True)
            sync_task.status = 'failed'
            sync_task.last_error = str(e)
            sync_task.retry_count += 1

            if sync_task.retry_count < sync_task.max_retries:
                sync_task.status = 'pending'  # will be retried by worker

        finally:
            sync_task.save()

    def _async_replicate(self, sync_task: SyncQueue):
        """
        Async replication: changes are streamed to replica without
        blocking. Relies on native DB replication (binlog/WAL) where
        possible, falling back to row-level resync for specific records.
        """
        payload = sync_task.payload
        table_name = payload.get('table_name')
        record_id = payload.get('record_id')

        if table_name and record_id:
            self._resync_record(table_name, record_id)
        else:
            logger.info("Async replication relying on native DB replication stream")

    def _sync_replicate(self, sync_task: SyncQueue):
        """
        Synchronous replication: write must be confirmed on replica
        before considered complete. Slower, but stronger guarantee.
        """
        payload = sync_task.payload
        table_name = payload.get('table_name')
        record_id = payload.get('record_id')

        if not (table_name and record_id):
            raise ReplicaConnectionError(
                "Sync replication requires table_name and record_id"
            )

        self._resync_record(table_name, record_id, wait_for_confirmation=True)

    def _resync_record(self, table_name: str, record_id: int, wait_for_confirmation=False):
        """Copy a single record's current state from primary to replica"""
        from django.apps import apps

        model = None
        for m in apps.get_models():
            if m._meta.db_table == table_name:
                model = m
                break

        if model is None:
            logger.warning(f"Could not resolve model for table: {table_name}")
            return

        try:
            instance = model.objects.using('default').get(pk=record_id)
            instance.save(using='replica')
            if wait_for_confirmation:
                # Verify the write landed
                model.objects.using('replica').get(pk=record_id)
        except model.DoesNotExist:
            # Record deleted on primary - delete on replica too
            model.objects.using('replica').filter(pk=record_id).delete()

    def trigger_failover(self, initiated_by=None) -> bool:
        """
        Promote replica to primary. This is a manual, deliberate action
        that requires explicit admin confirmation - never automatic
        unless auto_failover is explicitly enabled by the admin.
        """
        if not self.config.replica_enabled:
            raise ReplicaConnectionError("No replica configured for failover")

        if self.config.replica_status != 'online':
            raise ReplicaConnectionError(
                "Cannot fail over to an offline replica"
            )

        logger.critical(
            f"FAILOVER TRIGGERED: Primary {self.config.primary_host} -> "
            f"Replica {self.config.replica_host}, initiated_by={initiated_by}"
        )

        old_primary = self.config.primary_host, self.config.primary_port, self.config.primary_db_name
        self.config.primary_host = self.config.replica_host
        self.config.primary_port = self.config.replica_port
        self.config.primary_db_name = self.config.replica_db_name

        self.config.replica_host = old_primary[0]
        self.config.replica_port = old_primary[1]
        self.config.replica_db_name = old_primary[2]
        self.config.replica_status = 'offline'  # old primary now needs health check
        self.config.save()

        return True

    def _trigger_failover_alert(self):
        """Send alert to admins when auto_failover conditions are met"""
        logger.critical(
            f"REPLICA DOWN - auto_failover is enabled. "
            f"Primary: {self.config.primary_host}, "
            f"Replica: {self.config.replica_host}. "
            f"Manual review recommended before promoting replica."
        )
        # In production, wire this to email/SMS/Slack notification
