"""
Audit Trail Signals - Automatically captures before/after state for every
create/update/delete across registered models, without touching each view.

Wire into apps.py: `from . import signals` inside ready().
"""
import threading
import json
import logging
from django.db.models.signals import pre_save, post_save, pre_delete
from django.forms.models import model_to_dict
from django.core.serializers.json import DjangoJSONEncoder

logger = logging.getLogger(__name__)

# Thread-local storage to capture request context (user, IP) set by middleware
_audit_context = threading.local()


def set_audit_context(user=None, ip_address=None, user_agent='', request_id='', session_id=''):
    _audit_context.user = user
    _audit_context.ip_address = ip_address
    _audit_context.user_agent = user_agent
    _audit_context.request_id = request_id
    _audit_context.session_id = session_id


def get_audit_context():
    return {
        'user': getattr(_audit_context, 'user', None),
        'ip_address': getattr(_audit_context, 'ip_address', None),
        'user_agent': getattr(_audit_context, 'user_agent', ''),
        'request_id': getattr(_audit_context, 'request_id', ''),
        'session_id': getattr(_audit_context, 'session_id', ''),
    }


def clear_audit_context():
    for attr in ('user', 'ip_address', 'user_agent', 'request_id', 'session_id'):
        if hasattr(_audit_context, attr):
            delattr(_audit_context, attr)


# Models that should NOT be audited (audit/backup system's own tables,
# session/log tables, etc.) to avoid infinite loops and noise.
EXCLUDED_MODELS = {
    'AuditLog', 'UndoAction', 'BackupJob', 'BackupConfiguration',
    'BackupDestination', 'SyncQueue', 'BackupRestoreLog',
    'Session', 'LogEntry', 'ContentType', 'Permission',
}

# Sensitive fields that should never be stored in plaintext in audit snapshots
SENSITIVE_FIELDS = {'password', 'secret_key', 'access_key', 'api_key', 'token'}


def _serialize_instance(instance) -> dict:
    """Convert model instance to a JSON-safe dict, redacting sensitive fields"""
    try:
        data = model_to_dict(instance)
    except Exception:
        data = {}
        for field in instance._meta.fields:
            try:
                data[field.name] = getattr(instance, field.name)
            except Exception:
                pass

    redacted = {}
    for key, value in data.items():
        if any(sensitive in key.lower() for sensitive in SENSITIVE_FIELDS):
            redacted[key] = '***REDACTED***'
        else:
            try:
                json.dumps(value, cls=DjangoJSONEncoder)
                redacted[key] = value
            except (TypeError, ValueError):
                redacted[key] = str(value)

    return json.loads(json.dumps(redacted, cls=DjangoJSONEncoder))


# Cache of "before" states keyed by (model_label, pk), populated in pre_save
_before_state_cache = threading.local()

# Thread-local switch to silence audit writes during bulk operations (e.g.
# CSV import of thousands of rows). Without this, each row triggers a
# pre_save SELECT + a post_save AuditLog INSERT, which also cascades into
# core.signals' global ActivityLog logger (see suppress_activity_log there)
# — multiplying per-row DB work several times over inside django-import-
# export's single atomic transaction, and blowing up memory on large files.
_suppress = threading.local()


class suppress_audit_signals:
    """Context manager: temporarily disable audit-trail writes on this thread.

    Usage:
        with suppress_audit_signals():
            resource.import_data(...)
    """
    def __enter__(self):
        self._prev = getattr(_suppress, "active", False)
        _suppress.active = True
        return self

    def __exit__(self, *exc_info):
        _suppress.active = self._prev


def capture_before_state(sender, instance, **kwargs):
    """pre_save: snapshot the record's current DB state before it changes"""
    if getattr(_suppress, "active", False):
        return

    if sender.__name__ in EXCLUDED_MODELS:
        return

    if not instance.pk:
        return  # new record, nothing to capture

    try:
        old_instance = sender.objects.get(pk=instance.pk)
        before_data = _serialize_instance(old_instance)
    except sender.DoesNotExist:
        before_data = {}

    if not hasattr(_before_state_cache, 'states'):
        _before_state_cache.states = {}

    _before_state_cache.states[(sender.__name__, instance.pk)] = before_data


def capture_after_state(sender, instance, created, **kwargs):
    """post_save: write the audit log entry with before/after diff"""
    if getattr(_suppress, "active", False):
        return

    if sender.__name__ in EXCLUDED_MODELS:
        return

    from .models import AuditLog  # local import to avoid circular import

    after_data = _serialize_instance(instance)
    context = get_audit_context()

    if created:
        before_data = {}
        action = 'create'
        changed_fields = list(after_data.keys())
    else:
        cache_key = (sender.__name__, instance.pk)
        before_data = getattr(_before_state_cache, 'states', {}).pop(cache_key, {})
        action = 'update'
        changed_fields = [
            k for k in after_data
            if before_data.get(k) != after_data.get(k)
        ]
        if not changed_fields:
            return  # no actual change, skip noise

    try:
        AuditLog.objects.create(
            action=action,
            table_name=sender._meta.db_table,
            record_id=instance.pk,
            before_data=before_data,
            after_data=after_data,
            changed_fields=changed_fields,
            user=context['user'],
            ip_address=context['ip_address'],
            user_agent=context['user_agent'],
            request_id=context['request_id'],
            session_id=context['session_id'],
            description=f"{action} on {sender.__name__} #{instance.pk}",
        )
    except Exception as e:
        # Audit logging must never break the actual request
        logger.error(f"Failed to write audit log: {str(e)}", exc_info=True)


def capture_delete(sender, instance, **kwargs):
    """pre_delete: capture full record state before it's removed"""
    if getattr(_suppress, "active", False):
        return

    if sender.__name__ in EXCLUDED_MODELS:
        return

    from .models import AuditLog

    before_data = _serialize_instance(instance)
    context = get_audit_context()

    try:
        AuditLog.objects.create(
            action='delete',
            table_name=sender._meta.db_table,
            record_id=instance.pk,
            before_data=before_data,
            after_data={},
            changed_fields=list(before_data.keys()),
            user=context['user'],
            ip_address=context['ip_address'],
            user_agent=context['user_agent'],
            request_id=context['request_id'],
            session_id=context['session_id'],
            description=f"delete on {sender.__name__} #{instance.pk}",
        )
    except Exception as e:
        logger.error(f"Failed to write audit log for delete: {str(e)}", exc_info=True)


def register_audit_signals(model_list):
    """
    Connect audit signals to a specific list of models.
    Call this from each app's apps.py ready() with the models you want tracked,
    e.g. register_audit_signals([Course, Department, Allocation, ...])
    Keeping this explicit (rather than connecting to ALL models globally)
    avoids auditing noisy/irrelevant tables and keeps performance predictable.
    """
    for model in model_list:
        pre_save.connect(capture_before_state, sender=model, weak=False)
        post_save.connect(capture_after_state, sender=model, weak=False)
        pre_delete.connect(capture_delete, sender=model, weak=False)
        logger.info(f"Audit signals registered for {model.__name__}")
