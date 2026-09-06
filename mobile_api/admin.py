from django.contrib import admin
from .models import Announcement, AnnouncementAttachment, PersonalCourseEntry


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
