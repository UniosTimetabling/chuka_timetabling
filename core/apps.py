from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core System"

    def ready(self):
        # Register signals (post_migrate)
        import core.signals  # noqa
