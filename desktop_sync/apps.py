from django.apps import AppConfig


class DesktopSyncConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "desktop_sync"
    verbose_name = "Desktop App Sync"

    # NOTE: there is deliberately no `ready()` signal wiring here any more.
    # An earlier draft bumped a per-row version from post_save/post_delete
    # signals, but the project's real write paths (the web panel's
    # `Timetable.objects.bulk_create`, the autoscheduler publish step,
    # queryset `.update()` moves) bypass model signals entirely — so a
    # signal-driven version silently missed web edits, which is exactly the
    # case optimistic concurrency exists to catch. The concurrency token is
    # now derived from the row's own content (see views_sync.content_version),
    # which is correct no matter which code path wrote it.
