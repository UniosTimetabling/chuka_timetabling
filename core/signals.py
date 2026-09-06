from django.db.models.signals import post_migrate, post_save, post_delete
from django.contrib.auth.models import Group, Permission, AnonymousUser
from django.dispatch import receiver
from django.forms.models import model_to_dict
from django.apps import apps
from django.db import connection
from django.db.utils import OperationalError, ProgrammingError
from django.db.models.fields.files import FieldFile   # ← key import for the fix

from datetime import datetime, date, time
from decimal import Decimal
import threading
import json


# ============================================================
# ROLE & PERMISSION SEEDING
# ============================================================

DEFAULT_GROUPS = [
    "dvc",
    "dvc_admins",
    "dean",
    "dean_admins",
    "cod",
    "cod_admins",
    "director_timetable",
    "timetable_admins",
    "department_users",
]

ROLE_PERMISSION_MAP = {
    "dvc": [
        "approve_course_allocation",
        "assign_faculty_leader",
        "remove_faculty_leader",
    ],
    "dvc_admins": [
        "add_faculty",
        "change_faculty",
    ],
    "dean": [
        "assign_department_leader",
        "remove_department_leader",
    ],
    "dean_admins": [
        "change_department",
    ],
    "cod": [
        "forward_course_allocation",
        "add_courseallocation",
        "change_courseallocation",
        "delete_courseallocation",
    ],
    "cod_admins": [
        "add_courseallocation",
        "change_courseallocation",
    ],
    "director_timetable": [
        "approve_timetable",
    ],
    "timetable_admins": [
        "approve_timetable",
    ],
    "department_users": [
        "view_timetable",
    ],
}


def seed_roles_and_permissions():
    for group_name in DEFAULT_GROUPS:
        Group.objects.get_or_create(name=group_name)

    for role, codenames in ROLE_PERMISSION_MAP.items():
        group = Group.objects.get(name=role)
        perms = Permission.objects.filter(codename__in=codenames)
        group.permissions.set(perms)


@receiver(post_migrate)
def sync_groups_and_permissions(sender, **kwargs):
    if sender.name == "core":
        seed_roles_and_permissions()

        # Also seed canonical RBAC groups (deferred import avoids circular
        # import at app-load time; post_migrate fires safely after all apps load)
        from core.rbac import ensure_default_groups
        ensure_default_groups()

        print("✅ Groups & permissions synced")


# ============================================================
# THREAD SAFE CURRENT USER
# ============================================================

_user_local = threading.local()


def set_current_user(user):
    _user_local.user = user


def get_current_user():
    return getattr(_user_local, "user", None)


# ============================================================
# JSON SAFE SERIALIZER
# ============================================================

def make_json_safe(data):
    """
    Recursively convert a model_to_dict() result into JSON-serialisable
    primitives.

    FIX: The previous version used `hasattr(v, "url")` to detect file fields.
    In Python 3, `hasattr` only suppresses AttributeError — it lets all other
    exceptions propagate.  Django's FieldFile.url raises ValueError (not
    AttributeError) when no file is attached to the field, so the old check
    crashed on the first save of SiteSettings before any logo was uploaded.

    Correct approach: check `isinstance(v, FieldFile)` first (which never
    raises), then read `.name` (always safe) rather than `.url`.
    """
    def safe(v):
        # ── datetime / date / time ────────────────────────────────────────
        if isinstance(v, (datetime, date, time)):
            return v.isoformat()

        # ── Decimal ───────────────────────────────────────────────────────
        if isinstance(v, Decimal):
            return float(v)

        # ── FieldFile / ImageFieldFile ────────────────────────────────────
        # MUST come before any hasattr("url") check.
        # .name is the stored filename string (or "" / None when empty).
        # Accessing .url on an empty FieldFile raises ValueError, so we
        # only call it when we know a file is actually attached.
        if isinstance(v, FieldFile):
            if v.name:                # file is attached — safe to get URL
                try:
                    return v.url
                except Exception:
                    return v.name     # storage misconfigured: fall back to path
            return None               # no file uploaded yet → store null

        # ── nested dict ───────────────────────────────────────────────────
        if isinstance(v, dict):
            return {k: safe(vv) for k, vv in v.items()}

        # ── list / tuple ──────────────────────────────────────────────────
        if isinstance(v, (list, tuple)):
            return [safe(vv) for vv in v]

        # ── set / frozenset ───────────────────────────────────────────────
        if isinstance(v, (set, frozenset)):
            return [safe(vv) for vv in v]

        # ── anything that's already JSON-serialisable ─────────────────────
        try:
            json.dumps(v)
            return v
        except Exception:
            return str(v)

    return {k: safe(v) for k, v in data.items()}


# ============================================================
# AUDIT LOGGER (MIGRATION SAFE)
# ============================================================

# Once the table is confirmed to exist we never need to ask again for the
# life of the process — SHOW TABLES was previously run on *every single*
# model save (including every row of a CSV import), which is a needless
# DB round-trip multiplied by row count. Only re-check while it's still
# missing (e.g. before migrations have run).
_activitylog_table_exists_cache = False


def activitylog_table_exists():
    global _activitylog_table_exists_cache
    if _activitylog_table_exists_cache:
        return True
    try:
        exists = "core_activitylog" in connection.introspection.table_names()
    except Exception:
        return False
    if exists:
        _activitylog_table_exists_cache = True
    return exists


# Thread-local switch mirroring backup_system.signals.suppress_audit_signals
# — used to silence this logger during bulk operations (CSV import etc.).
_suppress = threading.local()


class suppress_activity_log:
    """Context manager: temporarily disable ActivityLog writes on this thread."""
    def __enter__(self):
        self._prev = getattr(_suppress, "active", False)
        _suppress.active = True
        return self

    def __exit__(self, *exc_info):
        _suppress.active = self._prev


def log_activity(action, instance):
    if getattr(_suppress, "active", False):
        return

    # Skip logging ActivityLog itself to prevent infinite recursion
    if instance.__class__.__name__ == "ActivityLog":
        return

    # backup_system already runs its own full before/after audit trail for
    # every model it registers (see backup_system/signals.py). Logging its
    # AuditLog writes *again* here just doubles write/query load for no
    # extra information — each of those rows already documents the exact
    # same event this signal is reacting to.
    if instance._meta.app_label == "backup_system":
        return

    # Skip during migrations (table may not exist yet)
    if not activitylog_table_exists():
        return

    try:
        ActivityLog = apps.get_model("core", "ActivityLog")
    except LookupError:
        return

    user = get_current_user()
    if isinstance(user, AnonymousUser):
        user = None

    try:
        ActivityLog.objects.create(
            user=user,
            action=action,
            app_label=instance._meta.app_label,
            model_name=instance.__class__.__name__,
            object_id=str(instance.pk),
            data=make_json_safe(model_to_dict(instance)),
        )
    except (OperationalError, ProgrammingError):
        pass          # absolute safety net during schema changes
    except Exception:
        pass          # never let logging crash the actual request


# ============================================================
# GLOBAL MODEL SIGNALS
# ============================================================

@receiver(post_save)
def log_model_save(sender, instance, created, **kwargs):
    if sender._meta.app_label in ["auth", "admin", "contenttypes", "sessions"]:
        return

    action = "CREATE" if created else "UPDATE"
    log_activity(action, instance)


@receiver(post_delete)
def log_model_delete(sender, instance, **kwargs):
    if sender._meta.app_label in ["auth", "admin", "contenttypes", "sessions"]:
        return

    log_activity("DELETE", instance)