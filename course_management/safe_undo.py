"""
Safe Undo — lets a COD reverse deletions AND edits made in the COD panel.

This does NOT duplicate the sudo-level backup_system undo tool. It is a
narrow, department-scoped window onto the SAME audit trail
(backup_system.models.AuditLog), built on the shared engine in
backup_system.page_undo so the same feature can be reused, unmodified,
on the other allocation pages (Lab, ODeL, Campus, Resits).

Currently tracked (and therefore undoable here):
  * CourseAllocation    - allocations, rejected allocations, and the
                          "secondary" allocations removed by Smart Combine
  * SelectionGroup      - elective/course-combination groups
  * CombinedCourseGroup - combined lecture groups
  * SpecializationCategory / SpecializationStem
                        - specialization tracks (pick-one-stem,
                          take-all-courses-in-stem)

A COD may only see and undo changes that happened inside their own
department. Deletions that have already been restored, or edits that have
since been superseded by a newer edit, simply drop out of the list.

Three ways to reverse:
  * a single entry (one specific deleted/edited record)
  * "per date"  - every change that happened on one calendar day
  * "per time"  - every change inside a specific time window
                  (a date plus a start/end time)
"""
from core.rbac import Role
from course_allocation.models import (
    CourseAllocation,
    SelectionGroup,
    CombinedCourseGroup,
    SpecializationCategory,
    SpecializationStem,
)
from course_allocation.detect_user_department import detect_user_department
from backup_system.page_undo import PageUndoConfig, build_views

TRACKED_MODELS = [
    CourseAllocation,
    SelectionGroup,
    CombinedCourseGroup,
    SpecializationCategory,
    SpecializationStem,
]

KIND_LABELS = {
    CourseAllocation._meta.db_table: "allocation",
    SelectionGroup._meta.db_table: "selection_group",
    CombinedCourseGroup._meta.db_table: "combined_group",
    SpecializationCategory._meta.db_table: "specialization_category",
    SpecializationStem._meta.db_table: "specialization_stem",
}


def _get_scope_id(table_name, data):
    # Most models here carry a direct 'department' FK, captured as a plain
    # id in the audit snapshot. SpecializationStem has no 'department'
    # column of its own (it hangs off SpecializationCategory), so resolve
    # its department via the snapshotted category id instead.
    if table_name == SpecializationStem._meta.db_table:
        category_id = data.get("category")
        if category_id is None:
            return None
        return (
            SpecializationCategory.objects
            .filter(pk=category_id)
            .values_list("department_id", flat=True)
            .first()
        )
    return data.get("department")


def _label_for_log(log, data):
    kind = KIND_LABELS.get(log.table_name, "record")
    if kind == "allocation":
        tags = []
        if data.get("is_elective"):
            tags.append("Elective")
        if data.get("is_evening_weekend"):
            tags.append("Evening/Weekend")
        tag_str = f" ({', '.join(tags)})" if tags else ""
        return "Allocation", f"{data.get('course_code', '?')} - {data.get('course_name', '')}{tag_str}"
    if kind == "selection_group":
        return "Selection Group", data.get("name", f"Group #{log.record_id}")
    if kind == "combined_group":
        return "Combined Group", f"{data.get('group_code', '?')} ({data.get('base_course_code', '')})"
    if kind == "specialization_category":
        return "Specialization Category", data.get("name", f"Category #{log.record_id}")
    if kind == "specialization_stem":
        return "Specialization Stem", data.get("name", f"Stem #{log.record_id}")
    return "Record", f"#{log.record_id}"


config = PageUndoConfig(
    models=TRACKED_MODELS,
    kind_labels=KIND_LABELS,
    get_scope_id=_get_scope_id,
    label_fn=_label_for_log,
)

safe_undo_page, safe_undo_action = build_views(
    config,
    get_user_scope=detect_user_department,
    template_name="course_management/safe_undo.html",
    roles=(Role.COD, Role.COD_ADMIN, Role.SUDO),
    action_url_name="safe_undo_action",
)
