from django.apps import AppConfig


class CourseManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'course_management'

    def ready(self):
        # course_management uses program_management models for Course data
        # Register audit signals for any locally-defined models if they exist
        pass
