from django.contrib import admin
from .models import (
    Announcement, AnnouncementAttachment, DeviceInstall, PersonalCourseEntry,
    ScheduleVisibility, SharedFile,
)


@admin.register(ScheduleVisibility)
class ScheduleVisibilityAdmin(admin.ModelAdmin):
    """
    Fixed-row admin: exactly one "Regular timetable" row and one "Exam
    timetable" row ever exist (see ScheduleVisibility.get_for). "Add" is
    hidden once both exist and "Delete" is disabled, so staff can only
    ever edit a row, never end up with duplicates or a missing one.
    """
    list_display = ("get_schedule_type_display", "is_blocked", "link_label", "updated_at")
    list_editable = ("is_blocked",)
    fields = ("schedule_type", "is_blocked", "message", "link_label", "link_url", "updated_at")
    readonly_fields = ("updated_at",)

    def get_schedule_type_display(self, obj):
        return obj.get_schedule_type_display()
    get_schedule_type_display.short_description = "Schedule"

    def has_add_permission(self, request):
        # Only ever the two SCHEDULE_CHOICES rows — block "Add" once both exist.
        return ScheduleVisibility.objects.count() < len(ScheduleVisibility.SCHEDULE_CHOICES)

    def has_delete_permission(self, request, obj=None):
        return False


class AnnouncementAttachmentInline(admin.TabularInline):
    model = AnnouncementAttachment
    extra = 1
    fields = ("file", "name", "mime_type")
    readonly_fields = ("mime_type",)


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "announcement_type", "audience", "program", "year", "date", "is_published")
    list_filter = ("announcement_type", "audience", "is_published", "program")
    search_fields = ("title", "description")
    inlines = [AnnouncementAttachmentInline]
    date_hierarchy = "date"


@admin.register(PersonalCourseEntry)
class PersonalCourseEntryAdmin(admin.ModelAdmin):
    list_display = ("__str__", "role", "reg_no", "lecturer", "course_allocation", "added_at")
    list_filter = ("role",)
    search_fields = ("reg_no", "lecturer__name", "course_allocation__course_code")
    autocomplete_fields = ("lecturer", "course_allocation")
    readonly_fields = ("added_at",)


@admin.register(DeviceInstall)
class DeviceInstallAdmin(admin.ModelAdmin):
    """Read-only — rows are created/updated only by the app's own install check-in."""
    list_display = ("device_id", "platform", "app_version", "first_seen_at", "last_seen_at")
    list_filter = ("platform",)
    search_fields = ("device_id",)
    readonly_fields = ("device_id", "platform", "app_version", "first_seen_at", "last_seen_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SharedFile)
class SharedFileAdmin(admin.ModelAdmin):
    """
    Files are uploaded from the Timetabling Dashboard (Shared Files page), so
    "Add" is hidden here; staff can still rename (title) or delete a row.
    """
    list_display = ("display_name", "mime_type", "file_size", "uploaded_by", "uploaded_at")
    search_fields = ("title", "file")
    readonly_fields = ("mime_type", "file_size", "uploaded_by", "uploaded_at")

    def has_add_permission(self, request):
        return False
