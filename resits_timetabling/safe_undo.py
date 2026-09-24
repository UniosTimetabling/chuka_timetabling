"""
resits_timetabling/safe_undo.py

Safe Undo for the Resits COD panel. Same pattern as
course_management/safe_undo.py, built on the shared engine in
backup_system.page_undo.

ResitCourseAllocation carries a direct 'department' FK, captured as a
plain id in the audit snapshot, so no indirect lookup is needed.
"""
from core.rbac import Role
from resits_timetabling.models import ResitCourseAllocation
from resits_timetabling.cod_panel import get_user_department
from backup_system.page_undo import PageUndoConfig, build_views

TRACKED_MODELS = [ResitCourseAllocation]


def _get_scope_id(table_name, data):
    return data.get("department")


def _label_for_log(log, data):
    return "Resit Allocation", f"{data.get('course_code', '?')} — {data.get('course_name', '')}"


config = PageUndoConfig(
    models=TRACKED_MODELS,
    get_scope_id=_get_scope_id,
    label_fn=_label_for_log,
)

resit_safe_undo_page, resit_safe_undo_action = build_views(
    config,
    get_user_scope=get_user_department,
    template_name="resits_timetabling/safe_undo.html",
    roles=(Role.COD, Role.COD_ADMIN, Role.SUDO),
    action_url_name="resit_safe_undo_action",
)
