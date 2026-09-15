"""
Undo Service - Reverses one action, several selected actions, or every
action within a time window, using the AuditLog before/after snapshots.

This NEVER touches backup files. It works purely off the audit trail,
which is why it can undo a single field change without restoring an
entire database.
"""
import logging
from datetime import datetime, timedelta
from django.apps import apps
from django.db import transaction
from django.utils import timezone
from .models import AuditLog, UndoAction
from core.rbac import Role

logger = logging.getLogger(__name__)


class UndoError(Exception):
    pass


class UndoNotPermittedError(UndoError):
    """Raised when the requesting user lacks restore permission"""
    pass


class UndoService:
    """Builds and executes undo operations against the audit trail"""

    # Only these roles may execute an undo - mirrors restore permission rules
    # in backup_system/views.py._is_restore_admin(). Keep these two in sync.
    ALLOWED_ROLES = {Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN}

    def __init__(self, requesting_user):
        self.user = requesting_user

    def _check_permission(self):
        is_allowed = (
            self.user.is_superuser or
            self.user.groups.filter(name__in=self.ALLOWED_ROLES).exists()
        )
        if not is_allowed:
            raise UndoNotPermittedError(
                "Only Super Admin or System Admin may undo actions."
            )

    # ------------------------------------------------------------------
    # Building undo requests
    # ------------------------------------------------------------------
    def create_single_undo(self, audit_log_id: int, description: str = '') -> UndoAction:
        """Undo exactly one audit log entry"""
        self._check_permission()

        audit_log = AuditLog.objects.get(pk=audit_log_id)

        undo = UndoAction.objects.create(
            undo_type='single',
            requested_by=self.user,
            description=description or f"Undo: {audit_log.description}",
        )
        undo.audit_logs.add(audit_log)
        return undo

    def create_multi_undo(self, audit_log_ids: list, description: str = '') -> UndoAction:
        """Undo a hand-picked selection of audit log entries (the checkbox list)"""
        self._check_permission()

        audit_logs = AuditLog.objects.filter(pk__in=audit_log_ids)
        if not audit_logs.exists():
            raise UndoError("No matching audit logs found for the given IDs")

        undo = UndoAction.objects.create(
            undo_type='multiple',
            requested_by=self.user,
            description=description or f"Undo {audit_logs.count()} selected actions",
        )
        undo.audit_logs.set(audit_logs)
        return undo

    def create_time_range_undo(
        self, start_time: datetime, end_time: datetime,
        user_filter=None, table_filter: str = None, description: str = ''
    ) -> UndoAction:
        """
        Undo everything within a time window, e.g. 'last 30 minutes' or
        'last 2 hours'. Optionally scoped to a specific user or table.
        """
        self._check_permission()

        qs = AuditLog.objects.filter(timestamp__gte=start_time, timestamp__lte=end_time)
        if user_filter:
            qs = qs.filter(user=user_filter)
        if table_filter:
            qs = qs.filter(table_name=table_filter)

        if not qs.exists():
            raise UndoError("No actions found in the specified time range")

        undo = UndoAction.objects.create(
            undo_type='time_range',
            undo_start_time=start_time,
            undo_end_time=end_time,
            requested_by=self.user,
            description=description or (
                f"Undo all actions between {start_time} and {end_time}"
            ),
        )
        undo.audit_logs.set(qs)
        return undo

    @staticmethod
    def quick_range(minutes: int = None, hours: int = None):
        """Helper: 'last 30 minutes' / 'last 2 hours' -> (start, end) tuple"""
        end = timezone.now()
        delta = timedelta(minutes=minutes or 0, hours=hours or 0)
        start = end - delta
        return start, end

    # ------------------------------------------------------------------
    # Preview before committing
    # ------------------------------------------------------------------
    def preview(self, undo_action: UndoAction) -> list:
        """
        Return a human-readable list of what WILL happen if this undo
        is executed, without making any changes. Always show this to
        the admin before they confirm.
        """
        results = []
        # Process newest-first so dependent reversals apply in the
        # correct order (e.g. an update followed by another update).
        logs = undo_action.audit_logs.order_by('-timestamp')

        for log in logs:
            if log.action == 'create':
                summary = (
                    f"Will DELETE {log.table_name} record #{log.record_id} "
                    f"(created {log.timestamp})"
                )
            elif log.action == 'update':
                changed = ', '.join(log.changed_fields) or 'fields'
                summary = (
                    f"Will REVERT {log.table_name} record #{log.record_id}: "
                    f"{changed} back to prior values"
                )
            elif log.action == 'delete':
                summary = (
                    f"Will RESTORE {log.table_name} record #{log.record_id} "
                    f"(deleted {log.timestamp})"
                )
            else:
                summary = f"Will reverse {log.action} on {log.table_name} #{log.record_id}"

            results.append({
                'audit_log_id': log.id,
                'action': log.action,
                'table_name': log.table_name,
                'record_id': log.record_id,
                'timestamp': log.timestamp,
                'summary': summary,
            })

        return results

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def execute(self, undo_action: UndoAction) -> UndoAction:
        """
        Execute the undo. Wrapped in a single DB transaction so a
        partial failure rolls everything back rather than leaving
        data half-reverted.
        """
        self._check_permission()

        undo_action.status = 'in_progress'
        undo_action.started_at = timezone.now()
        undo_action.approved_by = self.user
        undo_action.save()

        affected = 0

        try:
            with transaction.atomic():
                logs = undo_action.audit_logs.order_by('-timestamp')
                for log in logs:
                    self._reverse_single_log(log)
                    affected += 1

            undo_action.status = 'completed'
            undo_action.records_affected = affected

        except Exception as e:
            logger.error(f"Undo action {undo_action.id} failed: {str(e)}", exc_info=True)
            undo_action.status = 'failed'
            undo_action.error_message = str(e)
            raise UndoError(f"Undo failed and was rolled back: {str(e)}")

        finally:
            undo_action.completed_at = timezone.now()
            undo_action.save()

        # Record that the undo itself happened - this becomes a new
        # auditable event ('restore'), preserving full traceability.
        AuditLog.objects.create(
            action='restore',
            table_name='undo_action',
            record_id=undo_action.id,
            before_data={},
            after_data={'records_affected': affected},
            user=self.user,
            description=f"Executed undo action #{undo_action.id}: {undo_action.description}",
        )

        return undo_action

    def _reverse_single_log(self, log: AuditLog):
        """Apply the inverse of one audit log entry"""
        model = self._resolve_model(log.table_name)
        if model is None:
            raise UndoError(f"Cannot resolve model for table '{log.table_name}'")

        if log.action == 'create':
            # Reverse a create -> delete the record
            model.objects.filter(pk=log.record_id).delete()

        elif log.action == 'update':
            # Reverse an update -> write back the 'before' values
            obj, _ = model.objects.get_or_create(pk=log.record_id)
            for field, value in log.before_data.items():
                if hasattr(obj, field):
                    setattr(obj, field, value)
            obj.save()

        elif log.action == 'delete':
            # Reverse a delete -> recreate the record from 'before' snapshot
            model.objects.update_or_create(
                pk=log.record_id,
                defaults={
                    k: v for k, v in log.before_data.items()
                    if k != 'id' and hasattr(model, k)
                }
            )

        else:
            raise UndoError(f"Unsupported action type for reversal: {log.action}")

    @staticmethod
    def _resolve_model(table_name: str):
        for m in apps.get_models():
            if m._meta.db_table == table_name:
                return m
        return None

    # ------------------------------------------------------------------
    # Querying history for the UI
    # ------------------------------------------------------------------
    @staticmethod
    def get_recent_actions(user=None, table_name=None, limit=100):
        """Feed for the 'select actions to undo' checklist UI"""
        qs = AuditLog.objects.all().order_by('-timestamp')
        if user:
            qs = qs.filter(user=user)
        if table_name:
            qs = qs.filter(table_name=table_name)
        return qs[:limit]

    @staticmethod
    def get_undo_history(limit=50):
        """Feed for 'previous undo operations' admin view"""
        return UndoAction.objects.all().order_by('-created_at')[:limit]
