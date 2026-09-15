from django.apps import AppConfig


class SpecialRequestsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "special_requests"
    verbose_name = "Special Requests (SR)"

    def ready(self):
        # Connect post_delete signals so that whenever a course allocation
        # (from ANY of the cod panels: normal, ODEL, or campuses) is deleted
        # or bulk-archived, any Special Requests attached to it are archived
        # automatically rather than silently lost.
        from . import signals  # noqa: F401
