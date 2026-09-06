"""
campuses_timetable/safe_undo.py

Safe Undo for the Campus Course Allocation panel. Same pattern as
course_management/safe_undo.py, built on the shared engine in
backup_system.page_undo.

CampusCourseAllocation carries a direct 'department' FK, captured as a
plain id in the audit snapshot, so no indirect lookup is needed.
"""
from core.rbac import Role
from campuses_timetable.models import CampusCourseAllocation, CampusLabAllocation
from campuses_timetable.course_allocation_views import detect_user_department
from backup_system.page_undo import PageUndoConfig, build_views

TRACKED_MODELS = [CampusCourseAllocation, CampusLabAllocation]

KIND_LABELS = {
    CampusCourseAllocation._meta.db_table: "allocation",
    CampusLabAllocation._meta.db_table: "lab_allocation",
}


def _get_scope_id(table_name, data):
    if table_name == CampusCourseAllocation._meta.db_table:
        return data.get("department")
    # CampusLabAllocation has no direct department field; resolve via
    # program_course -> program -> department.
    program_course_id = data.get("program_course")
    if not program_course_id:
        return None
    from program_management.models import ProgramCourse
    return (
        ProgramCourse.objects
        .filter(pk=program_course_id)
        .values_list("program__department_id", flat=True)
        .first()
    )


def _label_for_log(log, data):
    kind = KIND_LABELS.get(log.table_name, "record")
    if kind == "allocation":
        return "Campus Allocation", f"{data.get('course_code', '?')} — {data.get('course_name', '')}"
    if kind == "lab_allocation":
        course_id = data.get("program_course")
        course_label = f"Course #{course_id}" if course_id else "?"
        if course_id:
            from program_management.models import ProgramCourse
            pc = ProgramCourse.objects.filter(pk=course_id).only("course_code", "course_name").first()
            if pc:
                course_label = f"{pc.course_code} — {pc.course_name}"
        return "Campus Lab Allocation", course_label
    return "Record", f"#{log.record_id}"


config = PageUndoConfig(
    models=TRACKED_MODELS,
    kind_labels=KIND_LABELS,
    get_scope_id=_get_scope_id,
    label_fn=_label_for_log,
)

campus_safe_undo_page, campus_safe_undo_action = build_views(
    config,
    get_user_scope=detect_user_department,
    template_name="campus/safe_undo.html",
    roles=(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN),
    action_url_name="campuses_timetable:campus_safe_undo_action",
)
