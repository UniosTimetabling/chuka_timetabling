from django.contrib import admin
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils.html import format_html

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget

from .models import Department, DepartmentCode
from faculty_management.models import Faculty


# =========================
# Import / Export Resource
# =========================

class DepartmentResource(resources.ModelResource):
    faculty = fields.Field(
        column_name="faculty",
        attribute="faculty",
        widget=ForeignKeyWidget(Faculty, "name"),
    )

    leader = fields.Field(
        column_name="leader",
        attribute="leader",
        widget=ForeignKeyWidget(User, "username"),
    )

    class Meta:
        model = Department
        import_id_fields = ("name",)
        fields = ("name", "faculty", "description", "leader")
        export_order = ("name", "faculty", "description", "leader")
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if row.get("name"):
            row["name"] = row["name"].strip().title()

        if row.get("faculty"):
            row["faculty"] = row["faculty"].strip().title()

        if not row.get("leader"):
            row["leader"] = None
        else:
            row["leader"] = row["leader"].strip()

        if row.get("description"):
            row["description"] = row["description"].strip()


# =========================
# Department Code (initials)
# =========================
# Short code shown instead of the full department name on the Course
# Allocation Excel/PDF exports (see department_management/department_codes.py
# for how a default is generated). Edited here — either inline on the
# Department itself or via the standalone "Department Codes" admin below.

class DepartmentCodeInline(admin.TabularInline):
    model = DepartmentCode
    extra = 0
    max_num = 1
    verbose_name = "Department Code"
    verbose_name_plural = "Department Code (initials used on exports)"


class DepartmentCodeResource(resources.ModelResource):
    department = fields.Field(
        column_name="department",
        attribute="department",
        widget=ForeignKeyWidget(Department, "name"),
    )

    class Meta:
        model = DepartmentCode
        import_id_fields = ("code",)
        fields = ("code", "department")
        export_order = ("code", "department")
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if row.get("code"):
            row["code"] = row["code"].strip().upper()
        if row.get("department"):
            row["department"] = row["department"].strip()


@admin.register(DepartmentCode)
class DepartmentCodeAdmin(ImportExportModelAdmin):
    resource_class = DepartmentCodeResource
    list_display = ("code", "department")
    search_fields = ("code", "department__name")
    list_select_related = ("department",)


# =========================
# Admin Configuration
# =========================

@admin.register(Department)
class DepartmentAdmin(ImportExportModelAdmin):
    resource_class = DepartmentResource
    inlines = [DepartmentCodeInline]

    list_display = (
        "name",
        "faculty",
        "code_display",
        "leader_display",
        "description_preview",
        "action_buttons",   # 👈 NEW COLUMN
    )

    list_filter = ("faculty",)
    search_fields = (
        "name",
        "faculty__name",
        "description",
        "leader__username",
    )

    list_per_page = 25

    fieldsets = (
        ("Department Information", {
            "fields": ("name", "faculty", "description"),
        }),
        ("Leadership", {
            "fields": ("leader",),
        }),
    )

    # -------------------------
    # Custom display methods
    # -------------------------

    def code_display(self, obj):
        entry = obj.department_codes.first()
        return entry.code if entry else "—"

    code_display.short_description = "Code"

    def leader_display(self, obj):
        if obj.leader:
            full_name = obj.leader.get_full_name()
            return f"{obj.leader.username}" + (
                f" ({full_name})" if full_name else ""
            )
        return "Not assigned"

    leader_display.short_description = "Leader"

    def description_preview(self, obj):
        if obj.description:
            return obj.description[:60] + (
                "..." if len(obj.description) > 60 else ""
            )
        return "-"

    description_preview.short_description = "Description"

    # -------------------------
    # Edit / Delete buttons
    # -------------------------

    def action_buttons(self, obj):
        buttons = []

        if self.has_change_permission(self.request, obj):
            edit_url = reverse(
                "admin:%s_%s_change"
                % (obj._meta.app_label, obj._meta.model_name),
                args=[obj.pk],
            )
            buttons.append(
                f'<a class="button" href="{edit_url}">Edit</a>'
            )

        if self.has_delete_permission(self.request, obj):
            delete_url = reverse(
                "admin:%s_%s_delete"
                % (obj._meta.app_label, obj._meta.model_name),
                args=[obj.pk],
            )
            buttons.append(
                f'<a class="button" style="color:red" href="{delete_url}">Delete</a>'
            )

        return format_html(" ".join(buttons))

    action_buttons.short_description = "Actions"
    action_buttons.allow_tags = True

    # -------------------------
    # Optimize queries
    # -------------------------

    def get_queryset(self, request):
        self.request = request  # 👈 needed for permission checks
        return super().get_queryset(request).select_related(
            "faculty", "leader"
        ).prefetch_related("department_codes")
