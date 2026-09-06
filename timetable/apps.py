from django.apps import AppConfig


class TimetableConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'timetable'

    def ready(self):
        try:
            from backup_system.signals import register_audit_signals
            from .models import Timetable, ExamTimetable, SchedulerConfig
            register_audit_signals([Timetable, ExamTimetable, SchedulerConfig])
        except Exception:
            pass
