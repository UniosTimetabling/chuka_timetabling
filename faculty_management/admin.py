from django.contrib import admin
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils.html import format_html

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget

from .models import Faculty


# =====================================================
# CUSTOM WIDGET
# =====================================================

class LeaderCreateWidget(ForeignKeyWidget):
    """
    Custom widget:
    - Accepts username
    - Creates User if not existing
    """

    def clean(self, value, row=None, *args, **kwargs):
        if not value:
            return None

        username = value.strip().lower()

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": f"{username}@example.com",
                "first_name": username.title(),
            },
        )
        return user


# =====================================================
# IMPORT / EXPORT RESOURCE
# =====================================================

class FacultyResource(resources.ModelResource):

    leader = fields.Field(
        column_name="leader",
        attribute="leader",
        widget=LeaderCreateWidget(User, "username"),
    )

    class Meta:
        model = Faculty
        import_id_fields = ["name"]
        fields = ("name", "description", "leader")
        export_order = ("name", "description", "leader")
        skip_unchanged = True
        report_skipped = True

    def before_import(self, dataset, **kwargs):
        super().before_import(dataset, **kwargs)

    def before_import_row(self, row, **kwargs):
        if row.get("name"):
            row["name"] = row["name"].strip().title()

        if row.get("description"):
            row["description"] = row["description"].strip()

        if "leader" in row and not row["leader"]:
            row["leader"] = None


# =====================================================
# ADMIN CONFIGURATION
# =====================================================

@admin.register(Faculty)
class FacultyAdmin(ImportExportModelAdmin):
    resource_class = FacultyResource

    list_display = (
        "name",
        "leader_display",
        "description_preview",
        "departments_count",
        "action_buttons",   # 👈 NEW
    )

    list_filter = ("leader",)
    search_fields = (
        "name",
        "description",
        "leader__username",
        "leader__email",
    )

    list_per_page = 25

    fieldsets = (
        ("Faculty Information", {
            "fields": ("name", "description"),
        }),
        ("Leadership", {
            "fields": ("leader",),
        }),
    )

    # -------------------------
    # Display helpers
    # -------------------------

    def leader_display(self, obj):
        if obj.leader:
            full_name = obj.leader.get_full_name()
            return full_name or obj.leader.username
        return "Not assigned"

    leader_display.short_description = "Leader"

    def description_preview(self, obj):
        if obj.description:
            return (
                obj.description[:60] + "..."
                if len(obj.description) > 60
                else obj.description
            )
        return "-"

    description_preview.short_description = "Description"

    def departments_count(self, obj):
        return obj.departments.count() if hasattr(obj, "departments") else 0

    departments_count.short_description = "Departments"

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

    # -------------------------
    # Query optimization
    # -------------------------

    def get_queryset(self, request):
        self.request = request  # needed for permission checks
        return super().get_queryset(request).select_related("leader")
