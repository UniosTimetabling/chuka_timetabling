from django.contrib import admin
from django.utils.html import format_html

from import_export import resources
from import_export.admin import ImportExportModelAdmin

from .models import MeetingVenue, MeetingBooking, UnbookRequest


# ==============================
# RESOURCE CLASSES
# ==============================

class MeetingVenueResource(resources.ModelResource):

    class Meta:
        model = MeetingVenue
        import_id_fields = ("code",)
        fields = (
            "code", "name", "location", "capacity", "venue_type",
            "is_workshop", "is_lab", "description", "is_active",
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if row.get("code"):
            row["code"] = row["code"].strip().upper()
        if row.get("name"):
            row["name"] = row["name"].strip()
        if row.get("venue_type"):
            row["venue_type"] = row["venue_type"].strip().lower()
        if row.get("capacity"):
            try:
                row["capacity"] = int(row["capacity"])
            except (TypeError, ValueError):
                row["capacity"] = None
        for flag in ("is_workshop", "is_lab", "is_active"):
            if flag in row:
                row[flag] = str(row[flag]).strip().lower() in ("true", "1", "yes")


class MeetingBookingResource(resources.ModelResource):

    class Meta:
        model = MeetingBooking
        import_id_fields = ("id",)
        fields = (
            "id", "venue__code", "booked_by", "booked_for", "contact",
            "date", "day", "start_time", "end_time", "status", "is_current", "notes",
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True


class UnbookRequestResource(resources.ModelResource):

    class Meta:
        model = UnbookRequest
        import_id_fields = ("id",)
        fields = (
            "id", "booking__id", "requested_by", "contact", "reason",
            "status", "requested_at", "resolved_at", "resolution_note",
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True


# ==============================
# ADMIN CLASSES
# ==============================

@admin.register(MeetingVenue)
class MeetingVenueAdmin(ImportExportModelAdmin):
    resource_class = MeetingVenueResource

    list_display = (
        "code", "name", "location", "capacity_display",
        "venue_type", "is_workshop", "is_lab", "status_display", "is_active",
    )
    list_filter = ("venue_type", "is_workshop", "is_lab", "is_active")
    search_fields = ("code", "name", "location", "description")
    list_per_page = 25

    def capacity_display(self, obj):
        return obj.capacity if obj.capacity is not None else "—"
    capacity_display.short_description = "Capacity"

    def status_display(self, obj):
        if obj.is_booked:
            return format_html(
                '<span style="background:#d32f2f;color:white;padding:3px 10px;'
                'border-radius:12px;font-weight:bold;">Booked</span>'
            )
        return format_html(
            '<span style="background:#2e7d32;color:white;padding:3px 10px;'
            'border-radius:12px;font-weight:bold;">Available</span>'
        )
    status_display.short_description = "Status"


@admin.register(MeetingBooking)
class MeetingBookingAdmin(ImportExportModelAdmin):
    resource_class = MeetingBookingResource

    list_display = (
        "venue", "booked_by", "booked_for", "date", "day",
        "start_time", "end_time", "status", "is_current",
    )
    list_filter = ("status", "is_current", "day", "venue")
    search_fields = ("booked_by", "booked_for", "venue__code")
    autocomplete_fields = ("venue",)
    list_per_page = 25


@admin.register(UnbookRequest)
class UnbookRequestAdmin(ImportExportModelAdmin):
    resource_class = UnbookRequestResource

    list_display = ("booking", "requested_by", "status", "requested_at", "resolved_by", "resolved_at")
    list_filter = ("status",)
    search_fields = ("requested_by", "booking__venue__code", "booking__booked_for")
    list_per_page = 25
