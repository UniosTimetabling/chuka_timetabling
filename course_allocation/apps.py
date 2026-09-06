from django.apps import AppConfig


class CourseAllocationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'course_allocation'

    def ready(self):
        try:
            from .combined_group_signals import register_combined_group_signals
            register_combined_group_signals()
        except Exception:
            pass
        try:
            from backup_system.signals import register_audit_signals
            from .models import (
                CourseAllocation, LecturerCourseMapping, LabAllocation,
                SelectionGroup, CombinedCourseGroup,
                SpecializationCategory, SpecializationStem,
            )
            register_audit_signals([
                CourseAllocation, LecturerCourseMapping, LabAllocation,
                # Added so "Safe Undo" on the COD panel can also reverse
                # deletions of Selection Groups and Combined Course Groups,
                # not just individual CourseAllocation rows.
                SelectionGroup, CombinedCourseGroup,
                # Specialization Categories/Stems (pick-one-stem,
                # take-all-courses-in-stem combination sets).
                SpecializationCategory, SpecializationStem,
            ])
        except Exception:
            pass
