"""
odel_system/safe_undo.py

Safe Undo for the ODeL Course Allocations page. Same pattern as
course_management/safe_undo.py and course_allocation/lab_safe_undo.py,
built on the shared engine in backup_system.page_undo.

ODELCourseAllocation has no direct 'department' column — it resolves via
program_course -> program -> department, same as LabAllocation.
"""
from functools import lru_cache

from core.rbac import Role
from odel_system.models import ODELCourseAllocation
from odel_system.views_allocation import detect_user_department
from backup_system.page_undo import PageUndoConfig, build_views

TRACKED_MODELS = [ODELCourseAllocation]


@lru_cache(maxsize=2048)
def _department_id_for_program_course(program_course_id):
    if program_course_id is None:
        return None
    from program_management.models import ProgramCourse
    return (
        ProgramCourse.objects
        .filter(pk=program_course_id)
        .values_list("program__department_id", flat=True)
        .first()
    )


def _get_scope_id(table_name, data):
    return _department_id_for_program_course(data.get("program_course"))


def _label_for_log(log, data):
    course_id = data.get("program_course")
    course_label = f"Course #{course_id}" if course_id else "?"
    if course_id:
        from program_management.models import ProgramCourse
        pc = ProgramCourse.objects.filter(pk=course_id).only("course_code", "course_name").first()
        if pc:
            course_label = f"{pc.course_code} — {pc.course_name}"
    tags = []
    if data.get("approved_by_dvc"):
        tags.append("Approved")
    if data.get("rejected"):
        tags.append("Rejected")
    tag_str = f" ({', '.join(tags)})" if tags else ""
    return "ODeL Allocation", f"{course_label}{tag_str}"


config = PageUndoConfig(
    models=TRACKED_MODELS,
    get_scope_id=_get_scope_id,
    label_fn=_label_for_log,
)

odel_safe_undo_page, odel_safe_undo_action = build_views(
    config,
    get_user_scope=detect_user_department,
    template_name="odel_system/safe_undo.html",
    roles=(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN),
    action_url_name="odel_system:odel_safe_undo_action",
)
