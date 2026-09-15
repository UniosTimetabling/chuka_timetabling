import re
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget

from .models import Building, Venue, LabVenue, VenueBlock, VenueSpecialization
from faculty_management.models import Faculty


# ==============================
# RESOURCE CLASSES
# ==============================

class BuildingResource(resources.ModelResource):
    faculty = fields.Field(
        column_name="faculty",
        attribute="faculty",
        widget=ForeignKeyWidget(Faculty, "name"),
    )

    class Meta:
        model = Building
        import_id_fields = ("code",)
        fields = ("name", "code", "faculty", "description", "is_workshop")
        export_order = ("name", "code", "faculty", "description", "is_workshop")
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if row.get("name"):
            row["name"] = row["name"].strip().title()
        if row.get("code"):
            row["code"] = row["code"].strip().upper()
        if row.get("faculty"):
            row["faculty"] = row["faculty"].strip().title()
        if "is_workshop" in row:
            row["is_workshop"] = str(row["is_workshop"]).strip().lower() in ("true", "1", "yes")


class VenueResource(resources.ModelResource):
    building = fields.Field(
        column_name="building",
        attribute="building",
        widget=ForeignKeyWidget(Building, "code"),
    )

    class Meta:
        model = Venue
        import_id_fields = ("code",)
        fields = ("code", "building", "capacity", "exam_capacity", "description", "is_workshop")
        export_order = ("code", "building", "capacity", "exam_capacity", "description", "is_workshop")
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if row.get("code"):
            row["code"] = row["code"].strip().upper()
        if row.get("building"):
            row["building"] = row["building"].strip().upper()
        if row.get("capacity"):
            try:
                row["capacity"] = int(row["capacity"])
            except (TypeError, ValueError):
                row["capacity"] = None
        if row.get("exam_capacity"):
            try:
                row["exam_capacity"] = int(row["exam_capacity"])
            except (TypeError, ValueError):
                row["exam_capacity"] = None
        if "is_workshop" in row:
            row["is_workshop"] = str(row["is_workshop"]).strip().lower() in ("true", "1", "yes")


class LabVenueResource(resources.ModelResource):

    class Meta:
        model = LabVenue
        import_id_fields = ("code",)
        fields = ("code", "capacity", "description", "equipment")
        export_order = ("code", "capacity", "description", "equipment")
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if row.get("code"):
            row["code"] = row["code"].strip().upper()
        if row.get("capacity"):
            try:
                row["capacity"] = int(row["capacity"])
            except (TypeError, ValueError):
                row["capacity"] = None


# ==============================
# ADMIN CLASSES
# ==============================

@admin.register(Building)
class BuildingAdmin(ImportExportModelAdmin):
    resource_class = BuildingResource

    list_display = (
        "name",
        "code",
        "faculty_display",
        "description_preview",
        "venues_count",
        "is_workshop_display",
        "edit_button",
        "delete_button",
    )
    list_filter = ("faculty", "is_workshop")
    search_fields = ("name", "code", "faculty__name", "description")
    list_per_page = 25

    fieldsets = (
        ("Building Information", {
            "fields": ("name", "code", "faculty", "description"),
        }),
        ("Classification", {
            "fields": ("is_workshop",),
            "description": "Mark this building as a workshop facility.",
        }),
    )

    def faculty_display(self, obj):
        return obj.faculty.name if obj.faculty else "-"
    faculty_display.short_description = "Faculty"

    def description_preview(self, obj):
        if obj.description:
            return obj.description[:60] + "..." if len(obj.description) > 60 else obj.description
        return "-"
    description_preview.short_description = "Description"

    def venues_count(self, obj):
        return obj.venues.count()
    venues_count.short_description = "Venues"

    def is_workshop_display(self, obj):
        if obj.is_workshop:
            return format_html(
                '<span style="background:#e65100;color:white;padding:3px 10px;'
                'border-radius:12px;font-weight:bold;">Workshop</span>'
            )
        return format_html('<span style="color:#999;">—</span>')
    is_workshop_display.short_description = "Workshop?"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("faculty")

    def edit_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Edit</a>',
            reverse('admin:%s_%s_change' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    edit_button.short_description = 'Edit'
    edit_button.allow_tags = True

    def delete_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Delete</a>',
            reverse('admin:%s_%s_delete' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    delete_button.short_description = 'Delete'
    delete_button.allow_tags = True


@admin.register(Venue)
class VenueAdmin(ImportExportModelAdmin):
    resource_class = VenueResource

    list_display = (
        "code",
        "building_display",
        "capacity_display",
        "exam_capacity_display",
        "description_preview",
        "is_workshop_display",
        "edit_button",
        "delete_button",
    )
    list_filter = ("building", "building__faculty", "is_workshop")
    search_fields = ("code", "building__name", "building__code", "description")
    list_per_page = 25

    fieldsets = (
        ("Venue Information", {
            "fields": ("code", "building", "description"),
        }),
        ("Capacity Information", {
            "fields": ("capacity", "exam_capacity"),
            "classes": ("wide",),
            "description": "Regular capacity is for normal lectures. Exam capacity is specifically for exam seating arrangements.",
        }),
        ("Classification", {
            "fields": ("is_workshop",),
            "description": "Mark this venue as a workshop room.",
        }),
    )

    def building_display(self, obj):
        if obj.building:
            return f"{obj.building.name} ({obj.building.code})"
        return "-"
    building_display.short_description = "Building"

    def capacity_display(self, obj):
        if obj.capacity:
            return format_html(
                '<span style="background:#4CAF50;color:white;padding:3px 8px;'
                'border-radius:12px;font-weight:bold;">{} seats</span>',
                obj.capacity,
            )
        return format_html('<span style="color:#999;">Unknown</span>')
    capacity_display.short_description = "Regular Capacity"

    def exam_capacity_display(self, obj):
        if obj.exam_capacity:
            return format_html(
                '<span style="background:#FF9800;color:white;padding:3px 8px;'
                'border-radius:12px;font-weight:bold;">{} seats</span>',
                obj.exam_capacity,
            )
        return format_html('<span style="color:#999;">Not specified</span>')
    exam_capacity_display.short_description = "Exam Capacity"

    def description_preview(self, obj):
        if obj.description:
            return obj.description[:60] + "..." if len(obj.description) > 60 else obj.description
        return "-"
    description_preview.short_description = "Description"

    def is_workshop_display(self, obj):
        if obj.is_workshop:
            return format_html(
                '<span style="background:#e65100;color:white;padding:3px 10px;'
                'border-radius:12px;font-weight:bold;">Workshop</span>'
            )
        return format_html('<span style="color:#999;">—</span>')
    is_workshop_display.short_description = "Workshop?"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "building",
            "building__faculty",
        )

    def edit_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Edit</a>',
            reverse('admin:%s_%s_change' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    edit_button.short_description = 'Edit'
    edit_button.allow_tags = True

    def delete_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Delete</a>',
            reverse('admin:%s_%s_delete' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    delete_button.short_description = 'Delete'
    delete_button.allow_tags = True


@admin.register(LabVenue)
class LabVenueAdmin(ImportExportModelAdmin):
    resource_class = LabVenueResource

    list_display = (
        "code",
        "capacity_display",
        "description_preview",
        "equipment_preview",
        "edit_button",
        "delete_button",
    )
    search_fields = ("code", "description", "equipment")
    list_per_page = 25

    fieldsets = (
        ("Lab Information", {
            "fields": ("code", "capacity", "description"),
        }),
        ("Equipment Details", {
            "fields": ("equipment",),
        }),
    )

    def capacity_display(self, obj):
        if obj.capacity:
            return format_html(
                '<span style="background:#2196F3;color:white;padding:3px 8px;'
                'border-radius:12px;font-weight:bold;">{} seats</span>',
                obj.capacity,
            )
        return format_html('<span style="color:#999;">Unknown</span>')
    capacity_display.short_description = "Capacity"

    def description_preview(self, obj):
        if obj.description:
            return obj.description[:50] + "..." if len(obj.description) > 50 else obj.description
        return "-"
    description_preview.short_description = "Description"

    def equipment_preview(self, obj):
        if obj.equipment:
            return obj.equipment[:50] + "..." if len(obj.equipment) > 50 else obj.equipment
        return "-"
    equipment_preview.short_description = "Equipment"

    def edit_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Edit</a>',
            reverse('admin:%s_%s_change' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    edit_button.short_description = 'Edit'
    edit_button.allow_tags = True

    def delete_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Delete</a>',
            reverse('admin:%s_%s_delete' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    delete_button.short_description = 'Delete'
    delete_button.allow_tags = True

@admin.register(VenueBlock)
class VenueBlockAdmin(admin.ModelAdmin):
    """
    Hard-blocks a venue from the autoscheduler entirely. Managed here in
    Django admin — day-to-day CRUD for venues/specializations happens in the
    in-app Venues panel, but blocks are infrequent enough that admin is fine.
    """
    list_display = ("venue", "reason", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("venue__code", "reason")
    autocomplete_fields = ("venue",)
    list_editable = ("is_active",)


@admin.register(VenueSpecialization)
class VenueSpecializationAdmin(admin.ModelAdmin):
    """
    The day-to-day create/edit workflow for specialization rules lives in
    the in-app Venues panel (dashboards/venues_panel.py). This admin
    registration exists so 'strict' and 'exclusive' — which aren't yet
    exposed as checkboxes in that panel's quick-create form — can still be
    toggled without needing a database console.
    """
    list_display = ("name", "scope", "strict", "exclusive", "priority", "is_active")
    list_filter = ("scope", "strict", "exclusive", "is_active")
    search_fields = ("name", "notes")
    filter_horizontal = ("venues", "departments", "programs", "courses")
    list_editable = ("strict", "exclusive", "priority", "is_active")
