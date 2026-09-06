"""
backup_system/page_undo.py

Shared "Safe Undo" engine used by every allocation page (COD's main
allocation page, Lab Allocations, ODeL, Campus, Resits, ...).

Each page supplies a small PageUndoConfig describing which models it
cares about and how to resolve the "scope" (almost always a department)
for a given AuditLog row. This module supplies the shared list / preview
/ undo logic so it doesn't need to be duplicated in every app.

Unlike the original COD-only version, this handles BOTH:
  * "delete" audit rows  -> recreates the deleted record  ("Restore")
  * "update" audit rows  -> writes the pre-edit values back onto the
                            still-existing record          ("Undo edit")

Both draw on the SAME audit trail (backup_system.models.AuditLog) that
is populated automatically for any model passed to
backup_system.signals.register_audit_signals([...]) in an app's
apps.py -> ready().
"""
import logging
from dataclasses import dataclass, field as dc_field
from datetime import datetime, time as dtime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.db import transaction, IntegrityError
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from core.rbac import allowed_roles
from backup_system.models import AuditLog

logger = logging.getLogger(__name__)

# Displayed / grouped in the university's local time, regardless of the
# UTC timestamps stored on the audit rows.
DISPLAY_TZ = ZoneInfo("Africa/Nairobi")

# How far back to look when scanning for undoable rows. Kept generous but
# bounded so a busy table can't make this page slow.
MAX_LOOKBACK = 1000


def _default_label(log, data):
    return "Record", f"#{log.record_id}"


@dataclass
class PageUndoConfig:
    models: list
    kind_labels: dict = dc_field(default_factory=dict)
    # callable(table_name: str, data: dict) -> Optional[int]
    # Resolves the scope id (almost always a department id) that a given
    # before_data snapshot belongs to.
    get_scope_id: Callable[[str, dict], Optional[int]] = None
    # callable(log, data) -> (kind: str, label: str)
    label_fn: Optional[Callable] = None

    def __post_init__(self):
        self.model_by_table = {m._meta.db_table: m for m in self.models}
        if self.label_fn is None:
            self.label_fn = _default_label


# ---------------------------------------------------------------
# Listing
# ---------------------------------------------------------------
def _still_applicable(config: PageUndoConfig, logs):
    """
    Drop rows that no longer make sense to show:
      * a 'delete' log whose record already exists again (already restored)
      * an 'update' log whose record has since been deleted (use the
        delete-undo flow for that instead)
    For 'update' logs, keep only the single most recent one per record so
    "Undo edit" always reverts to the immediately-prior state.
    """
    deletes_by_table = {}
    updates = []
    for log in logs:
        if log.action == "delete":
            deletes_by_table.setdefault(log.table_name, []).append(log)
        else:
            updates.append(log)

    result = []
    for table_name, table_logs in deletes_by_table.items():
        model = config.model_by_table.get(table_name)
        if model is None:
            continue
        ids = [l.record_id for l in table_logs]
        existing = set(model.objects.filter(pk__in=ids).values_list("pk", flat=True))
        result.extend(l for l in table_logs if l.record_id not in existing)

    latest_update = {}
    for log in updates:
        model = config.model_by_table.get(log.table_name)
        if model is None or not model.objects.filter(pk=log.record_id).exists():
            continue
        key = (log.table_name, log.record_id)
        if key not in latest_update or log.timestamp > latest_update[key].timestamp:
            latest_update[key] = log
    result.extend(latest_update.values())

    result.sort(key=lambda l: l.timestamp, reverse=True)
    return result


def get_undoable_logs(config: PageUndoConfig, scope_id):
    """All delete/update AuditLog rows for this page's models, scoped."""
    if scope_id is None:
        return []
    qs = (
        AuditLog.objects
        .filter(action__in=["delete", "update"], table_name__in=config.model_by_table.keys())
        .select_related("user")
        .order_by("-timestamp")[:MAX_LOOKBACK]
    )
    matched = [
        log for log in qs
        if config.get_scope_id(log.table_name, log.before_data or {}) == scope_id
    ]
    return _still_applicable(config, matched)


def log_to_dict(config: PageUndoConfig, log):
    data = log.before_data or {}
    local_ts = timezone.localtime(log.timestamp, DISPLAY_TZ)
    kind, label = config.label_fn(log, data)
    return {
        "id": log.id,
        "action": log.action,
        "action_label": "Deleted" if log.action == "delete" else "Edited",
        "kind": kind,
        "label": label,
        "changed_fields": log.changed_fields if log.action == "update" else [],
        "by": (log.user.get_full_name() or log.user.username) if log.user else "Unknown",
        "at": local_ts.strftime("%Y-%m-%d %H:%M:%S"),
        "date": local_ts.strftime("%Y-%m-%d"),
        "time": local_ts.strftime("%H:%M"),
    }


# ---------------------------------------------------------------
# Undo / restore
# ---------------------------------------------------------------
def _apply_snapshot(model, pk, before, create_if_missing):
    """
    Write a before_data snapshot onto a record, either recreating it
    (delete-undo) or overwriting fields on the existing row (edit-undo).
    Snapshot keys are field NAMES; foreign keys were captured as plain
    ids, so concrete fields are remapped to their attnames (e.g.
    'program_id') before writing. M2M fields are applied afterwards.
    """
    field_map = {f.name: f.attname for f in model._meta.concrete_fields}
    m2m_names = {f.name for f in model._meta.many_to_many}

    values, m2m_values = {}, {}
    for key, value in before.items():
        if key in m2m_names:
            m2m_values[key] = value or []
            continue
        attname = field_map.get(key)
        if attname is None:
            continue
        values[attname] = value

    if create_if_missing:
        obj, created = model.objects.update_or_create(pk=pk, defaults=values)
    else:
        obj = model.objects.get(pk=pk)
        for attname, value in values.items():
            setattr(obj, attname, value)
        obj.save()
        created = False

    for name, ids in m2m_values.items():
        getattr(obj, name).set(ids)

    return obj, created


def apply_undo(config: PageUndoConfig, log):
    model = config.model_by_table[log.table_name]
    before = log.before_data or {}
    create_if_missing = log.action == "delete"
    return _apply_snapshot(model, log.record_id, before, create_if_missing)


def apply_undo_batch(config: PageUndoConfig, logs):
    """Undo a list of AuditLog rows, tolerating individual failures."""
    restored, failed = [], []
    for log in logs:
        try:
            with transaction.atomic():
                apply_undo(config, log)
            restored.append(log.id)
        except IntegrityError as exc:
            logger.warning("Safe Undo: integrity error on log %s: %s", log.id, exc)
            failed.append({"id": log.id, "reason": "A record with the same unique code already exists."})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Safe Undo: failed to undo log %s: %s", log.id, exc)
            failed.append({"id": log.id, "reason": str(exc)})
    return restored, failed


# ---------------------------------------------------------------
# Reusable view builder — each app calls this once to get a fully
# wired (page_view, action_view) pair for its own urls.py.
# ---------------------------------------------------------------
def build_views(config: PageUndoConfig, get_user_scope, template_name, roles, action_url_name):
    """
    get_user_scope(user) -> object with an `.id`, or None
    roles: tuple of core.rbac.Role values allowed to view/use this page
    action_url_name: the url name (namespaced if needed, e.g.
        'odel_system:odel_safe_undo_action') registered for the returned
        safe_undo_action view, so the page template can post to it.
    """
    from django.urls import reverse

    def _still_applicable_now(scope):
        return get_undoable_logs(config, scope.id if scope else None)

    @login_required
    @allowed_roles(*roles)
    def safe_undo_page(request):
        scope = get_user_scope(request.user)
        logs = _still_applicable_now(scope)
        rows = [log_to_dict(config, l) for l in logs]
        available_dates = sorted({r["date"] for r in rows}, reverse=True)
        return render(request, template_name, {
            "scope": scope,
            "logs": rows,
            "available_dates": available_dates,
            "action_url": reverse(action_url_name),
        })

    @login_required
    @allowed_roles(*roles)
    def safe_undo_action(request):
        if request.method != "POST" or request.headers.get("x-requested-with") != "XMLHttpRequest":
            return JsonResponse({"status": "error", "message": "Invalid request"}, status=400)

        scope = get_user_scope(request.user)
        if scope is None:
            return JsonResponse(
                {"status": "error", "message": "No department associated with your account."},
                status=403,
            )

        action = request.POST.get("action")
        ALLOWED_ACTIONS = {"undo_single", "undo_by_date", "undo_by_time", "list_logs"}
        if action not in ALLOWED_ACTIONS:
            return JsonResponse({"status": "error", "message": "Unknown action"}, status=400)

        all_logs = {log.id: log for log in _still_applicable_now(scope)}

        if action == "list_logs":
            return JsonResponse({
                "status": "success",
                "logs": [log_to_dict(config, l) for l in all_logs.values()],
            })

        if action == "undo_single":
            log_id = request.POST.get("log_id")
            log = all_logs.get(int(log_id)) if log_id else None
            if not log:
                return JsonResponse(
                    {"status": "error", "message": "Entry not found, or it has already been undone."},
                    status=404,
                )
            restored, failed = apply_undo_batch(config, [log])
            if failed:
                return JsonResponse({"status": "error", "message": failed[0]["reason"]}, status=400)
            return JsonResponse({"status": "success", "restored_count": 1})

        # ---- date / time window undo ----
        date_str = request.POST.get("date", "").strip()
        if not date_str:
            return JsonResponse({"status": "error", "message": "A date is required."}, status=400)

        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return JsonResponse({"status": "error", "message": "Invalid date."}, status=400)

        if action == "undo_by_date":
            start_t, end_t = dtime.min, dtime.max
        else:  # undo_by_time
            start_str = request.POST.get("start_time", "00:00")
            end_str = request.POST.get("end_time", "23:59")
            try:
                start_t = datetime.strptime(start_str, "%H:%M").time()
                end_t = datetime.strptime(end_str, "%H:%M").time()
            except ValueError:
                return JsonResponse({"status": "error", "message": "Invalid time."}, status=400)

        start_dt = datetime.combine(day, start_t).replace(tzinfo=DISPLAY_TZ)
        end_dt = datetime.combine(day, end_t).replace(tzinfo=DISPLAY_TZ)

        matching = [
            log for log in all_logs.values()
            if start_dt <= timezone.localtime(log.timestamp, DISPLAY_TZ) <= end_dt
        ]

        if not matching:
            return JsonResponse(
                {"status": "error", "message": "No undoable entries found in that window."},
                status=404,
            )

        restored, failed = apply_undo_batch(config, matching)
        return JsonResponse({
            "status": "success",
            "restored_count": len(restored),
            "failed": failed,
        })

    return safe_undo_page, safe_undo_action
