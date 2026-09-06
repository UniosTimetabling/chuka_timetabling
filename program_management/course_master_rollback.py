# course_master_rollback.py
import logging
from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from core.rbac import allowed_roles, Role
from department_management.models import Department
from program_management.models import Program, ProgramCourse
from backup_system.page_undo import (
    PageUndoConfig, log_to_dict, apply_undo_batch, _still_applicable, MAX_LOOKBACK,
)
from backup_system.models import AuditLog

logger = logging.getLogger(__name__)


def _department_id_for_program(program_id):
    if program_id is None:
        return None
    dept_id = (
        Program.objects.filter(pk=program_id).values_list("department_id", flat=True).first()
    )
    if dept_id is not None:
        return dept_id
    log = (
        AuditLog.objects
        .filter(table_name=Program._meta.db_table, record_id=program_id, action="delete")
        .order_by("-timestamp")
        .first()
    )
    if log:
        return (log.before_data or {}).get("department")
    return None


def _get_scope_id(table_name, data):
    if table_name == Program._meta.db_table:
        return data.get("department")
    if table_name == ProgramCourse._meta.db_table:
        return _department_id_for_program(data.get("program"))
    return None


def _label_for_log(log, data):
    if log.table_name == Program._meta.db_table:
        return "Program", data.get("name") or f"#{log.record_id}"
    code = data.get("course_code") or ""
    name = data.get("course_name") or ""
    cohort = data.get("student_cohort") or "0"
    label = f"{code} — {name} (Cohort {cohort})".strip(" —") or f"#{log.record_id}"
    return "Course", label


config = PageUndoConfig(
    models=[Program, ProgramCourse],
    get_scope_id=_get_scope_id,
    label_fn=_label_for_log,
)


def _all_undoable_logs():
    qs = (
        AuditLog.objects
        .filter(action__in=["create", "delete", "update"], table_name__in=config.model_by_table.keys())
        .select_related("user")
        .order_by("-timestamp")[:MAX_LOOKBACK]
    )
    return _still_applicable(config, list(qs))


def _undo_logs(logs):
    """Undo a mixed batch of create/update/delete AuditLog rows."""
    creates = [l for l in logs if l.action == "create"]
    others = [l for l in logs if l.action != "create"]

    restored, failed = [], []

    course_creates = [l for l in creates if l.table_name == ProgramCourse._meta.db_table]
    program_creates = [l for l in creates if l.table_name == Program._meta.db_table]

    for log in course_creates + program_creates:
        model = config.model_by_table[log.table_name]
        try:
            deleted, _ = model.objects.filter(pk=log.record_id).delete()
            if deleted:
                restored.append(log.id)
            else:
                restored.append(log.id)
        except Exception as exc:
            logger.warning("Course Master Rollback: failed to remove created row (log %s): %s", log.id, exc)
            failed.append({"id": log.id, "reason": str(exc)})

    program_others = [l for l in others if l.table_name == Program._meta.db_table]
    course_others = [l for l in others if l.table_name == ProgramCourse._meta.db_table]

    r1, f1 = apply_undo_batch(config, program_others)
    r2, f2 = apply_undo_batch(config, course_others)

    restored += r1 + r2
    failed += f1 + f2
    return restored, failed


def _rows_with_scope(logs):
    rows = []
    for log in logs:
        data = log.before_data or {}
        d = log_to_dict(config, log)
        if log.action == "create":
            d["action_label"] = "Created"
        d["request_id"] = log.request_id or ""
        d["department_id"] = _get_scope_id(log.table_name, data if log.action != "create" else (log.after_data or {}))
        rows.append(d)
    return rows


def _group_by_department_and_batch(rows):
    dept_ids = {r["department_id"] for r in rows if r["department_id"] is not None}
    dept_names = dict(Department.objects.filter(pk__in=dept_ids).values_list("id", "name"))

    by_dept = defaultdict(lambda: defaultdict(list))
    for r in rows:
        batch_key = r["request_id"] or f"solo-{r['id']}"
        by_dept[r["department_id"]][batch_key].append(r)

    departments = []
    for dept_id, batches in by_dept.items():
        batch_list = []
        for batch_key, batch_rows in batches.items():
            batch_rows.sort(key=lambda r: r["at"], reverse=True)
            batch_list.append({
                "batch_key": batch_key,
                "request_id": batch_rows[0]["request_id"],
                "is_grouped": len(batch_rows) > 1 and bool(batch_rows[0]["request_id"]),
                "count": len(batch_rows),
                "by": batch_rows[0]["by"],
                "at": batch_rows[0]["at"],
                "rows": batch_rows,
            })
        batch_list.sort(key=lambda b: b["at"], reverse=True)
        departments.append({
            "department_id": dept_id,
            "department_name": dept_names.get(dept_id, "Unassigned / Unknown"),
            "batches": batch_list,
            "total_changes": len(rows) and sum(b["count"] for b in batch_list),
        })
    departments.sort(key=lambda d: d["department_name"])
    return departments


@login_required
@allowed_roles(Role.SUDO)
def course_master_rollback_page(request):
    rows = _rows_with_scope(_all_undoable_logs())
    departments = _group_by_department_and_batch(rows)
    return render(request, "program/course_master_rollback.html", {
        "departments": departments,
        "total_changes": len(rows),
    })


@login_required
@allowed_roles(Role.SUDO)
def course_master_rollback_action(request):
    if request.method != "POST" or request.headers.get("x-requested-with") != "XMLHttpRequest":
        return JsonResponse({"status": "error", "message": "Invalid request"}, status=400)

    action = request.POST.get("action")
    if action not in {"undo_single", "undo_batch", "list_logs"}:
        return JsonResponse({"status": "error", "message": "Unknown action"}, status=400)

    all_rows = _rows_with_scope(_all_undoable_logs())
    all_logs_by_id = {log.id: log for log in _all_undoable_logs()}

    if action == "list_logs":
        return JsonResponse({"status": "success", "logs": all_rows})

    if action == "undo_single":
        log_id = request.POST.get("log_id")
        log = all_logs_by_id.get(int(log_id)) if log_id else None
        if not log:
            return JsonResponse(
                {"status": "error", "message": "Entry not found, or it has already been undone."}, status=404,
            )
        restored, failed = _undo_logs([log])
        if failed:
            return JsonResponse({"status": "error", "message": failed[0]["reason"]}, status=400)
        return JsonResponse({"status": "success", "restored_count": 1})

    # undo_batch
    request_id = (request.POST.get("request_id") or "").strip()
    department_id = request.POST.get("department_id")
    department_id = int(department_id) if department_id not in (None, "", "all") else None

    if not request_id:
        return JsonResponse({"status": "error", "message": "Missing batch id."}, status=400)

    def _scope_of(log):
        data = log.before_data if log.action != "create" else (log.after_data or {})
        return _get_scope_id(log.table_name, data or {})

    matching = [
        log for log in all_logs_by_id.values()
        if log.request_id == request_id
        and (department_id is None or _scope_of(log) == department_id)
    ]
    if not matching:
        return JsonResponse(
            {"status": "error", "message": "No undoable entries found for that import."}, status=404,
        )

    restored, failed = _undo_logs(matching)

    return JsonResponse({
        "status": "success" if not failed else "partial",
        "restored_count": len(restored),
        "failed": failed,
    })