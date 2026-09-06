from django.contrib import admin

from .models import SpecialRequest, SpecialRequestAllocation


class SpecialRequestAllocationInline(admin.TabularInline):
    model = SpecialRequestAllocation
    extra = 0
    readonly_fields = ("content_type", "object_id", "course_code", "course_name", "created_at")


@admin.register(SpecialRequest)
class SpecialRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id", "department", "scope", "course_code", "lecturer",
        "status", "archived", "semester", "created_by", "created_at",
    )
    list_filter = ("department", "scope", "status", "archived", "panel", "semester")
    search_fields = ("course_code", "course_name", "description", "lecturer__name")
    autocomplete_fields = ("department", "program", "lecturer", "created_by")
    readonly_fields = ("created_at", "updated_at", "archived_at")
    inlines = [SpecialRequestAllocationInline]
