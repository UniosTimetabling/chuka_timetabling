from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.utils import timezone
from django.utils.timesince import timesince

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget

from .models import ClassRep, ClassRepNotification, MinimalTimetable
from program_management.models import Program


# =====================================================
# ADMIN ROW ACTION MIXIN (Edit / Delete buttons)
# =====================================================

class RowActionMixin:
    """
    Adds Edit / Delete buttons on the right
    """

    def row_actions(self, obj):
        opts = self.model._meta

        edit_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_change",
            args=[obj.pk],
        )
        delete_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_delete",
            args=[obj.pk],
        )

        return format_html(
            """
            <div class="row-actions">
                <a class="button edit" href="{}">Edit</a>
                <a class="button delete" href="{}">Delete</a>
            </div>
            """,
            edit_url,
            delete_url,
        )

    row_actions.short_description = "Actions"


# =====================================================
# BASE RESOURCE (handles extra/missing fields safely)
# =====================================================

class SafeBaseResource(resources.ModelResource):
    """
    - Ignores extra columns
    - Allows missing fields
    - Normalizes common values
    """

    def before_import_row(self, row, **kwargs):
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.strip()


# ==============================
# RESOURCE CLASSES
# ==============================

class ClassRepResource(SafeBaseResource):
    program = fields.Field(
        column_name="program",
        attribute="program",
        widget=ForeignKeyWidget(Program, "name"),
    )

    class Meta:
        model = ClassRep
        import_id_fields = ("reg_no",)
        # Removed fields = "__all__" — django-import-export treats it as a
        # literal whitelist containing the string "__all__", which blocks all
        # explicitly declared Field() instances (like `program` above).
        # Using only exclude lets all model fields + custom fields through.
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        super().before_import_row(row, **kwargs)

        if row.get("full_name"):
            row["full_name"] = row["full_name"].title()

        if row.get("reg_no"):
            row["reg_no"] = row["reg_no"].upper()

        if row.get("username"):
            row["username"] = row["username"].lower()

        if row.get("email"):
            row["email"] = row["email"].lower()

        if "active" not in row or row.get("active") in ("", None):
            row["active"] = True


class ClassRepNotificationResource(SafeBaseResource):
    recipient = fields.Field(
        column_name="recipient",
        attribute="recipient",
        widget=ForeignKeyWidget(ClassRep, "reg_no"),
    )

    class Meta:
        model = ClassRepNotification
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        super().before_import_row(row, **kwargs)

        if row.get("recipient"):
            row["recipient"] = row["recipient"].upper()

        if "is_read" not in row or row.get("is_read") in ("", None):
            row["is_read"] = False

        if "created_at" not in row:
            row["created_at"] = timezone.now()


class MinimalTimetableResource(SafeBaseResource):
    class_rep = fields.Field(
        column_name="class_rep",
        attribute="class_rep",
        widget=ForeignKeyWidget(ClassRep, "reg_no"),
    )

    class Meta:
        model = MinimalTimetable
        import_id_fields = ("class_rep", "course_code", "day", "start_time")
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        super().before_import_row(row, **kwargs)

        if row.get("class_rep"):
            row["class_rep"] = row["class_rep"].upper()

        if row.get("course_code"):
            row["course_code"] = row["course_code"].upper()

        if row.get("course_name"):
            row["course_name"] = row["course_name"].title()

        if row.get("day"):
            day_map = {
                "Mon": "Monday",
                "Tue": "Tuesday",
                "Wed": "Wednesday",
                "Thu": "Thursday",
                "Fri": "Friday",
                "Sat": "Saturday",
                "Sun": "Sunday",
            }
            clean_day = row["day"].title()
            row["day"] = day_map.get(clean_day, clean_day)


# ==============================
# ADMIN CLASSES
# ==============================

@admin.register(ClassRep)
class ClassRepAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = ClassRepResource

    list_display = (
        "full_name",
        "reg_no",
        "username",
        "email",
        "program",
        "date_registered",
        "status_badge",
        "row_actions",
    )

    list_display_links = ("full_name",)
    list_filter = ("active", "program", "date_registered")
    search_fields = ("full_name", "reg_no", "username", "email", "program__name")
    ordering = ("-date_registered",)
    readonly_fields = ("date_registered",)
    list_per_page = 25

    fieldsets = (
        ("Personal Details", {
            "fields": ("full_name", "reg_no", "username", "email"),
        }),
        ("Academic Info", {
            "fields": ("program",),
        }),
        ("Account Status", {
            "fields": ("password", "active", "date_registered"),
        }),
    )

    def status_badge(self, obj):
        color = "#4CAF50" if obj.active else "#f44336"
        label = "Active" if obj.active else "Inactive"
        return format_html(
            '<span style="background:{};color:white;padding:3px 10px;border-radius:12px;">{}</span>',
            color,
            label,
        )

    status_badge.short_description = "Status"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("program")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


@admin.register(ClassRepNotification)
class ClassRepNotificationAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = ClassRepNotificationResource

    list_display = (
        "recipient",
        "short_message",
        "read_badge",
        "created_at",
        "time_ago",
        "row_actions",
    )

    list_display_links = ("recipient",)
    list_filter = ("is_read", "created_at")
    search_fields = ("recipient__full_name", "recipient__reg_no", "message")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)
    list_per_page = 25

    def short_message(self, obj):
        return obj.message[:50] + "..." if len(obj.message) > 50 else obj.message

    short_message.short_description = "Message"

    def read_badge(self, obj):
        color = "#4CAF50" if obj.is_read else "#2196F3"
        label = "Read" if obj.is_read else "Unread"
        return format_html(
            '<span style="background:{};color:white;padding:3px 10px;border-radius:12px;">{}</span>',
            color,
            label,
        )

    read_badge.short_description = "Status"

    def time_ago(self, obj):
        return f"{timesince(obj.created_at)} ago" if obj.created_at else "-"

    time_ago.short_description = "Time Ago"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("recipient")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


@admin.register(MinimalTimetable)
class MinimalTimetableAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = MinimalTimetableResource

    list_display = (
        "class_rep",
        "course_code",
        "course_name",
        "day",
        "time_slot",
        "venue",
        "last_updated",
        "row_actions",
    )

    list_display_links = ("class_rep",)
    list_filter = ("day", "class_rep__program", "last_updated")
    search_fields = ("course_code", "course_name", "venue", "class_rep__full_name")
    ordering = ("day", "start_time")
    readonly_fields = ("last_updated",)
    list_per_page = 25

    fieldsets = (
        ("Class Rep", {"fields": ("class_rep",)}),
        ("Course", {"fields": ("course_code", "course_name")}),
        ("Schedule", {"fields": ("day", "start_time", "end_time", "venue")}),
        ("System", {"fields": ("last_updated",)}),
    )

    def time_slot(self, obj):
        return f"{obj.start_time.strftime('%H:%M')} – {obj.end_time.strftime('%H:%M')}"

    time_slot.short_description = "Time"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "class_rep",
            "class_rep__program",
        )

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)