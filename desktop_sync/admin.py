from django.contrib import admin

from .models import DesktopAuthToken


@admin.register(DesktopAuthToken)
class DesktopAuthTokenAdmin(admin.ModelAdmin):
    """Revoke a lost laptop's access: select its row(s) and delete."""

    list_display = ("user", "device_label", "created_at", "last_used_at")
    list_filter = ("user",)
    search_fields = ("user__username", "device_label")
    readonly_fields = ("user", "device_label", "created_at", "last_used_at")
    ordering = ("-last_used_at",)

    def has_add_permission(self, request):
        return False  # tokens are only issued by the login endpoint
