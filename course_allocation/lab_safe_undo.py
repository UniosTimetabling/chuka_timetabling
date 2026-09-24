"""
course_allocation/lab_safe_undo.py

Safe Undo for the Lab & Workshop Allocations page. Same pattern as
course_management/safe_undo.py, built on the shared engine in
backup_system.page_undo.

LabAllocation has no direct 'department' column — it links to a
department indirectly via program_course -> program -> department, so
scope resolution requires a small DB lookup per row.
"""
from functools import lru_cache

from core.rbac import Role
from course_allocation.models import LabAllocation
from course_allocation.detect_user_department import detect_user_department
from backup_system.page_undo import PageUndoConfig, build_views

TRACKED_MODELS = [LabAllocation]


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
    tag = " (Workshop)" if data.get("is_workshop_course") else ""
    return "Lab Allocation", f"{course_label}{tag}"


config = PageUndoConfig(
    models=TRACKED_MODELS,
    get_scope_id=_get_scope_id,
    label_fn=_label_for_log,
)

lab_safe_undo_page, lab_safe_undo_action = build_views(
    config,
    get_user_scope=detect_user_department,
    template_name="course_allocation/lab_safe_undo.html",
    roles=(Role.COD, Role.COD_ADMIN, Role.SUDO),
    action_url_name="lab_safe_undo_action",
)
