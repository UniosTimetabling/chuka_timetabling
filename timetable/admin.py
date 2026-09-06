from django.contrib import admin
from django.contrib.auth.models import User
from django.utils.html import format_html
from django.urls import reverse

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget, ManyToManyWidget

from .models import (
    LabSchedulerConfig, ExamSchedulerConfig, SchedulerConfig,
    Timetable, TempTimetable, ExamTimetable, ExamTempTimetable,
    LabTimetable, LabExamTimetable, TimetableArchive,
    SharedVenueExamGroup, MergedCourseGroup,
    MergedCourseGroupTimetable, AutoMergedExamGroup,
)


# ======================================================
# BASE CLEAN RESOURCE  (shared safety logic)
# ======================================================

class BaseCleanResource(resources.ModelResource):
    """
    • Ignores unknown columns silently.
    • Strips whitespace from all string fields.
    • Normalises boolean fields from CSV text values.
    """

    def before_import_row(self, row, **kwargs):
        for k, v in row.items():
            if isinstance(v, str):
                row[k] = v.strip()

        for bool_field in ("published",):
            if bool_field in row:
                val = row.get(bool_field)
                if isinstance(val, str):
                    row[bool_field] = val.lower() in ("1", "true", "yes", "y")
                elif val in (None, ""):
                    row[bool_field] = False


# ======================================================
# CONFIG RESOURCES
# ======================================================

class LabSchedulerConfigResource(BaseCleanResource):
    class Meta:
        model = LabSchedulerConfig
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True


class ExamSchedulerConfigResource(BaseCleanResource):
    class Meta:
        model = ExamSchedulerConfig
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True


class SchedulerConfigResource(BaseCleanResource):
    class Meta:
        model = SchedulerConfig
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True


# ======================================================
# TIMETABLE RESOURCES
# ======================================================

class TimetableBaseResource(BaseCleanResource):
    course_allocation = fields.Field(
        column_name="course_allocation",
        attribute="course_allocation",
        widget=ForeignKeyWidget("course_allocation.CourseAllocation", "course_code"),
    )
    venue = fields.Field(
        column_name="venue",
        attribute="venue",
        widget=ForeignKeyWidget("room_management.Venue", "code"),
    )

    def before_import_row(self, row, **kwargs):
        super().before_import_row(row, **kwargs)
        if "day" in row and row["day"]:
            row["day"] = row["day"].title()


class TimetableResource(TimetableBaseResource):
    class Meta:
        model = Timetable
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True


class TempTimetableResource(TimetableBaseResource):
    class Meta:
        model = TempTimetable
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True


class ExamTimetableResource(TimetableBaseResource):
    class Meta:
        model = ExamTimetable
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True


class ExamTempTimetableResource(TimetableBaseResource):
    class Meta:
        model = ExamTempTimetable
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True


class LabTimetableResource(BaseCleanResource):
    lab_allocation = fields.Field(
        column_name="lab_allocation",
        attribute="lab_allocation",
        widget=ForeignKeyWidget("course_allocation.LabAllocation", "id"),
    )
    lab_venue = fields.Field(
        column_name="lab_venue",
        attribute="lab_venue",
        widget=ForeignKeyWidget("room_management.LabVenue", "code"),
    )

    class Meta:
        model = LabTimetable
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True


class LabExamTimetableResource(LabTimetableResource):
    class Meta:
        model = LabExamTimetable
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True


# ======================================================
# ARCHIVE & GROUP RESOURCES
# ======================================================

class TimetableArchiveResource(BaseCleanResource):
    archived_by = fields.Field(
        column_name="archived_by",
        attribute="archived_by",
        widget=ForeignKeyWidget(User, "username"),
    )

    class Meta:
        model = TimetableArchive
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True


class SharedVenueExamGroupResource(BaseCleanResource):
    venue = fields.Field(
        column_name="venue",
        attribute="venue",
        widget=ForeignKeyWidget("room_management.Venue", "code"),
    )
    course_allocations = fields.Field(
        column_name="course_allocations",
        attribute="course_allocations",
        widget=ManyToManyWidget("course_allocation.CourseAllocation", separator="|", field="course_code"),
    )

    class Meta:
        model = SharedVenueExamGroup
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True


class MergedCourseGroupResource(BaseCleanResource):
    base_course = fields.Field(
        column_name="base_course",
        attribute="base_course",
        widget=ForeignKeyWidget("course_allocation.CourseAllocation", "course_code"),
    )
    merged_courses = fields.Field(
        column_name="merged_courses",
        attribute="merged_courses",
        widget=ManyToManyWidget("course_allocation.CourseAllocation", separator="|", field="course_code"),
    )

    class Meta:
        model = MergedCourseGroup
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True


class MergedCourseGroupTimetableResource(BaseCleanResource):
    base_course = fields.Field(
        column_name="base_course",
        attribute="base_course",
        widget=ForeignKeyWidget("course_allocation.CourseAllocation", "course_code"),
    )
    merged_courses = fields.Field(
        column_name="merged_courses",
        attribute="merged_courses",
        widget=ManyToManyWidget("course_allocation.CourseAllocation", separator="|", field="course_code"),
    )

    class Meta:
        model = MergedCourseGroupTimetable
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True


# ======================================================
# INLINE ADMINS
# ======================================================

class SharedVenueCoursesInline(admin.TabularInline):
    """Shows all course allocations sharing a venue in one table row per course."""
    model = SharedVenueExamGroup.course_allocations.through
    extra = 1
    verbose_name = "Course Allocation"
    verbose_name_plural = "Course Allocations in this Shared Venue"
    # Show the FK field that links to CourseAllocation
    fields = ("courseallocation",)
    autocomplete_fields = ("courseallocation",) if hasattr(admin, "autocomplete_fields") else ()

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("courseallocation")


class MergedCoursesInline(admin.TabularInline):
    """Displays every merged CourseAllocation row with key details."""
    model = MergedCourseGroup.merged_courses.through
    extra = 1
    verbose_name = "Merged Course"
    verbose_name_plural = "All Merged Courses in this Group"
    fields = ("courseallocation",)
    autocomplete_fields = ("courseallocation",) if hasattr(admin, "autocomplete_fields") else ()

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("courseallocation")


class MergedCourseGroupTimetableCoursesInline(admin.TabularInline):
    """Displays every merged CourseAllocation row for regular timetable groups."""
    model = MergedCourseGroupTimetable.merged_courses.through
    extra = 1
    verbose_name = "Merged Course"
    verbose_name_plural = "All Merged Courses in this Group"
    fields = ("courseallocation",)
    autocomplete_fields = ("courseallocation",) if hasattr(admin, "autocomplete_fields") else ()

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("courseallocation")


# ======================================================
# SHARED MIXIN — edit / delete action buttons
# ======================================================

class ActionButtonsMixin:
    """Adds reusable Edit and Delete action buttons to list_display."""

    def edit_button(self, obj):
        url = reverse(
            "admin:%s_%s_change" % (obj._meta.app_label, obj._meta.model_name),
            args=[obj.pk],
        )
        return format_html('<a class="button" href="{}">Edit</a>', url)

    edit_button.short_description = "Edit"

    def delete_button(self, obj):
        url = reverse(
            "admin:%s_%s_delete" % (obj._meta.app_label, obj._meta.model_name),
            args=[obj.pk],
        )
        return format_html('<a class="button" style="color:red;" href="{}">Delete</a>', url)

    delete_button.short_description = "Delete"


# ======================================================
# CONFIG ADMINS
# ======================================================

@admin.register(LabSchedulerConfig)
class LabSchedulerConfigAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = LabSchedulerConfigResource
    list_display = ("start_time", "end_time", "slot_size", "edit_button", "delete_button")
    fieldsets = (
        ("Time Window", {
            "fields": ("start_time", "end_time", "slot_size"),
        }),
    )
    search_fields = ()
    list_per_page = 25


@admin.register(ExamSchedulerConfig)
class ExamSchedulerConfigAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = ExamSchedulerConfigResource
    list_display = (
        "start_date", "start_time", "end_time", "slot_size",
        "max_exam_days", "spacing_ratio",
        "edit_button", "delete_button",
    )
    search_fields = ("start_date",)
    list_filter = ("start_date",)
    readonly_fields = ("excluded_days",)
    fieldsets = (
        ("Schedule Window", {
            "fields": ("start_date", "start_time", "end_time", "slot_size"),
        }),
        ("Capacity & Duration", {
            "fields": ("max_exam_days", "spacing_ratio"),
        }),
        ("Excluded Dates (auto-managed)", {
            "classes": ("collapse",),
            "fields": ("excluded_days",),
            "description": (
                "Weekends are added automatically. "
                "You may manually add public holidays as YYYY-MM-DD separated by commas."
            ),
        }),
    )
    list_per_page = 25


@admin.register(SchedulerConfig)
class SchedulerConfigAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = SchedulerConfigResource
    list_display = ("start_time", "end_time", "slot_size", "edit_button", "delete_button")
    fieldsets = (
        ("Time Window", {
            "fields": ("start_time", "end_time", "slot_size"),
        }),
    )
    search_fields = ()
    list_per_page = 25


# ======================================================
# TIMETABLE BASE ADMIN
# ======================================================

class TimetableBaseAdmin(ImportExportModelAdmin):
    """Shared base for all course-timetable admin classes."""

    list_display  = ("course_code", "course_name", "day", "time_slot", "venue_code")
    list_filter   = ("day", "venue")
    search_fields = (
        "course_allocation__course_code",
        "course_allocation__course_name",
        "venue__code",
        "day",
    )
    list_per_page = 50
    ordering      = ("day", "start_time")
    autocomplete_fields = ("course_allocation", "venue")

    fieldsets = (
        ("Course & Venue", {
            "fields": ("course_allocation", "venue"),
        }),
        ("Schedule", {
            "fields": ("day", "start_time", "end_time"),
        }),
    )

    def course_code(self, obj):
        return obj.course_allocation.course_code
    course_code.short_description = "Course Code"
    course_code.admin_order_field = "course_allocation__course_code"

    def course_name(self, obj):
        return obj.course_allocation.course_name
    course_name.short_description = "Course Name"
    course_name.admin_order_field = "course_allocation__course_name"

    def time_slot(self, obj):
        return f"{obj.start_time}–{obj.end_time}"
    time_slot.short_description = "Time Slot"
    time_slot.admin_order_field = "start_time"

    def venue_code(self, obj):
        return obj.venue.code
    venue_code.short_description = "Venue"
    venue_code.admin_order_field = "venue__code"


class ExamTimetableBaseAdmin(TimetableBaseAdmin):
    """Extended base for exam timetables that have a date field."""

    list_display  = ("course_code", "course_name", "date", "day", "time_slot", "venue_code")
    list_filter   = ("day", "venue", "date")
    search_fields = TimetableBaseAdmin.search_fields + ("date",)
    ordering      = ("date", "start_time")
    date_hierarchy = "date"

    fieldsets = (
        ("Course & Venue", {
            "fields": ("course_allocation", "venue"),
        }),
        ("Schedule", {
            "fields": ("date", "day", "start_time", "end_time"),
        }),
    )


# ======================================================
# TIMETABLE ADMINS
# ======================================================

@admin.register(Timetable)
class TimetableAdmin(TimetableBaseAdmin):
    resource_class = TimetableResource


@admin.register(TempTimetable)
class TempTimetableAdmin(TimetableBaseAdmin):
    resource_class = TempTimetableResource


@admin.register(ExamTimetable)
class ExamTimetableAdmin(ExamTimetableBaseAdmin):
    resource_class = ExamTimetableResource


@admin.register(ExamTempTimetable)
class ExamTempTimetableAdmin(ExamTimetableBaseAdmin):
    resource_class = ExamTempTimetableResource


# ======================================================
# LAB TIMETABLE ADMINS
# ======================================================

class LabTimetableBaseAdmin(ImportExportModelAdmin):
    """Shared base for lab timetable admin classes."""

    list_display  = ("lab_course_code", "lab_course_name", "lab_venue_code", "day", "time_slot")
    list_filter   = ("day", "lab_venue")
    search_fields = (
        "lab_allocation__program_course__course_code",
        "lab_allocation__program_course__course_name",
        "lab_venue__code",
        "day",
    )
    list_per_page = 50
    ordering      = ("day", "start_time")

    fieldsets = (
        ("Lab Allocation & Venue", {
            "fields": ("lab_allocation", "lab_venue"),
        }),
        ("Schedule", {
            "fields": ("day", "start_time", "end_time"),
        }),
    )

    def lab_course_code(self, obj):
        return obj.lab_allocation.program_course.course_code
    lab_course_code.short_description = "Course Code"
    lab_course_code.admin_order_field = "lab_allocation__program_course__course_code"

    def lab_course_name(self, obj):
        return obj.lab_allocation.program_course.course_name
    lab_course_name.short_description = "Course Name"
    lab_course_name.admin_order_field = "lab_allocation__program_course__course_name"

    def lab_venue_code(self, obj):
        return obj.lab_venue.code
    lab_venue_code.short_description = "Lab Venue"
    lab_venue_code.admin_order_field = "lab_venue__code"

    def time_slot(self, obj):
        return f"{obj.start_time}–{obj.end_time}"
    time_slot.short_description = "Time Slot"
    time_slot.admin_order_field = "start_time"


@admin.register(LabTimetable)
class LabTimetableAdmin(LabTimetableBaseAdmin):
    resource_class = LabTimetableResource
    readonly_fields = ("created_at", "updated_at")
    fieldsets = LabTimetableBaseAdmin.fieldsets + (
        ("Timestamps", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )


@admin.register(LabExamTimetable)
class LabExamTimetableAdmin(LabTimetableBaseAdmin):
    resource_class  = LabExamTimetableResource
    list_display    = ("lab_course_code", "lab_course_name", "lab_venue_code", "date", "day", "time_slot")
    list_filter     = ("day", "lab_venue", "date")
    search_fields   = LabTimetableBaseAdmin.search_fields + ("date",)
    ordering        = ("date", "start_time")
    date_hierarchy  = "date"
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Lab Allocation & Venue", {
            "fields": ("lab_allocation", "lab_venue"),
        }),
        ("Schedule", {
            "fields": ("date", "day", "start_time", "end_time"),
        }),
        ("Timestamps", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )


# ======================================================
# ARCHIVE ADMIN
# ======================================================

@admin.register(TimetableArchive)
class TimetableArchiveAdmin(ImportExportModelAdmin):
    resource_class  = TimetableArchiveResource
    list_display    = ("timetable_type", "semester", "academic_year", "archived_by", "archived_at")
    list_filter     = ("timetable_type", "semester", "academic_year")
    search_fields   = ("academic_year", "archived_by__username", "timetable_type")
    readonly_fields = ("archived_at",)
    ordering        = ("-archived_at",)
    date_hierarchy  = "archived_at"
    list_per_page   = 25
    fieldsets = (
        ("Archive Details", {
            "fields": ("timetable_type", "semester", "academic_year", "archived_by"),
        }),
        ("Archived Data", {
            "classes": ("collapse",),
            "fields": ("data",),
            "description": "Raw JSON snapshot of the timetable at the time of archiving.",
        }),
        ("Timestamps", {
            "classes": ("collapse",),
            "fields": ("archived_at",),
        }),
    )


# ======================================================
# SHARED VENUE EXAM GROUP ADMIN
# ======================================================

@admin.register(SharedVenueExamGroup)
class SharedVenueExamGroupAdmin(ImportExportModelAdmin):
    resource_class = SharedVenueExamGroupResource
    inlines        = [SharedVenueCoursesInline]

    list_display   = (
        "venue_code", "date", "day", "time_slot",
        "total_students", "num_courses", "published",
    )
    list_filter    = ("published", "day", "venue", "date")
    search_fields  = (
        "venue__code",
        "day",
        "course_allocations__course_code",
        "course_allocations__course_name",
    )
    ordering       = ("date", "start_time")
    date_hierarchy = "date"
    list_per_page  = 50

    readonly_fields = ("created_at", "updated_at", "total_students", "course_list_display")

    fieldsets = (
        ("Venue & Session", {
            "fields": ("venue", "date", "day", "start_time", "end_time"),
        }),
        ("Shared Courses (summary)", {
            "fields": ("course_list_display", "total_students"),
            "description": "Edit individual course allocations using the inline table below.",
        }),
        ("Status", {
            "fields": ("published",),
        }),
        ("Timetable Linkage", {
            "classes": ("collapse",),
            "fields": ("exam_timetable_entry", "exam_temp_timetable_entry"),
            "description": (
                "exam_timetable_entry is used when published=True; "
                "exam_temp_timetable_entry when published=False."
            ),
        }),
        ("Timestamps", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    def venue_code(self, obj):
        return obj.venue.code
    venue_code.short_description = "Venue"
    venue_code.admin_order_field = "venue__code"

    def time_slot(self, obj):
        return f"{obj.start_time}–{obj.end_time}"
    time_slot.short_description = "Time Slot"
    time_slot.admin_order_field = "start_time"

    def num_courses(self, obj):
        return obj.course_allocations.count()
    num_courses.short_description = "# Courses"

    def course_list_display(self, obj):
        """Renders a readable bullet list of all shared courses."""
        courses = obj.course_allocations.all().order_by("course_code")
        if not courses.exists():
            return "—"
        rows = "".join(
            f"<li><strong>{ca.course_code}</strong> — {ca.course_name} "
            f"({ca.number_of_students} students)</li>"
            for ca in courses
        )
        return format_html("<ul style='margin:0; padding-left:18px;'>{}</ul>", format_html(rows))
    course_list_display.short_description = "Courses Sharing This Venue"


# ======================================================
# SHARED HELPER — builds the merged-courses HTML table
# (used by both MergedCourseGroupAdmin and MergedCourseGroupTimetableAdmin)
# ======================================================

def _merged_courses_table(courses, total_students):
    """
    Returns a safe HTML table listing every course in a merged group.
    courses  – queryset of CourseAllocation ordered by course_code
    total_students – integer, pre-computed total stored on the group object
    """
    if not courses.exists():
        return format_html("<em>No merged courses yet. Add them using the inline table below.</em>")

    th = "padding:6px 10px; border:1px solid #ccc; text-align:left;"
    thr = "padding:6px 10px; border:1px solid #ccc; text-align:right;"

    header = (
        "<table style='border-collapse:collapse; width:100%; margin-top:4px;'>"
        "<thead><tr style='background:#e8f0fe;'>"
        f"<th style='{th}'>#</th>"
        f"<th style='{th}'>Course Code</th>"
        f"<th style='{th}'>Course Name</th>"
        f"<th style='{thr}'>Students</th>"
        "</tr></thead><tbody>"
    )
    rows = ""
    for i, ca in enumerate(courses, start=1):
        bg = "#ffffff" if i % 2 == 0 else "#f9fbff"
        rows += (
            f"<tr style='background:{bg};'>"
            f"<td style='padding:5px 10px; border:1px solid #ddd; color:#888;'>{i}</td>"
            f"<td style='padding:5px 10px; border:1px solid #ddd;'>"
            f"<strong style='color:#1a1a2e;'>{ca.course_code}</strong></td>"
            f"<td style='padding:5px 10px; border:1px solid #ddd;'>{ca.course_name}</td>"
            f"<td style='padding:5px 10px; border:1px solid #ddd; text-align:right;'>{ca.number_of_students}</td>"
            f"</tr>"
        )
    footer = (
        f"<tr style='background:#f0f0f0; font-weight:bold;'>"
        f"<td colspan='3' style='padding:6px 10px; border:1px solid #ccc;'>Total ({courses.count()} courses)</td>"
        f"<td style='padding:6px 10px; border:1px solid #ccc; text-align:right;'>{total_students}</td>"
        f"</tr>"
    )
    return format_html("{}", format_html(header + rows + footer + "</tbody></table>"))


def _merged_codes_badge_list(courses):
    """
    Returns a compact comma-separated list of coloured course-code badges
    for the list_display column — so you can see every merged course at a glance
    without opening the record.
    """
    if not courses.exists():
        return format_html("<span style='color:#aaa;'>—</span>")
    badges = " ".join(
        f"<span style='display:inline-block; background:#e8f0fe; color:#1a56db; "
        f"border:1px solid #c3d2f7; border-radius:4px; padding:1px 7px; "
        f"font-size:12px; margin:1px;'>{ca.course_code}</span>"
        for ca in courses
    )
    return format_html("{}", format_html(badges))


# ======================================================
# MERGED COURSE GROUP ADMIN  (Exam merges)
# ======================================================

@admin.register(MergedCourseGroup)
class MergedCourseGroupAdmin(ImportExportModelAdmin):
    """
    Exam merged-course groups.

    • `date`  → real DateField (exam calendar date, e.g. 2025-06-04)
    • `start_time` / `end_time` → the exam slot on that date
    • `venue` → where the combined sitting takes place
    • Timetable linkage:
        published=True  → exam_timetable_entry  (ExamTimetable)
        published=False → exam_temp_timetable_entry (ExamTempTimetable)
    """
    resource_class = MergedCourseGroupResource
    inlines        = [MergedCoursesInline]

    # ---- list view -------------------------------------------------------
    # merged_courses_badges shows every course code as a coloured tag inline
    list_display   = (
        "merged_code",
        "merged_courses_badges",   # ← actual codes visible in list
        "date",
        "time_slot",
        "venue_code",
        "total_students",
        "published",
    )
    list_filter    = ("published", "date", "venue")
    search_fields  = (
        "merged_code",
        "base_course__course_code",
        "base_course__course_name",
        "venue__code",
        "merged_courses__course_code",
        "merged_courses__course_name",
    )
    ordering       = ("date", "start_time")
    date_hierarchy = "date"
    list_per_page  = 50

    # ---- detail view -----------------------------------------------------
    readonly_fields = ("created_at", "merged_courses_display")

    fieldsets = (
        ("Merge Identity", {
            "fields": ("merged_code", "base_course", "total_students"),
            "description": (
                "<strong>merged_code</strong> is the normalised base code (e.g. COSC312). "
                "<strong>base_course</strong> is the representative CourseAllocation row."
            ),
        }),
        ("Merged Courses (full list)", {
            "fields": ("merged_courses_display",),
            "description": (
                "Every CourseAllocation in this merged group. "
                "To add or remove a course use the <em>Merged Courses</em> inline table at the bottom of this page."
            ),
        }),
        ("Exam Session", {
            "fields": ("date", "start_time", "end_time", "venue"),
            "description": (
                "<strong>date</strong> is the actual calendar exam date. "
                "The day-of-week is derived from it automatically — you do not need to store it separately."
            ),
        }),
        ("Status", {
            "fields": ("published",),
            "description": (
                "Tick <em>published</em> once the exam schedule is approved. "
                "This also switches the active timetable linkage from the draft to the final entry."
            ),
        }),
        ("Timetable Linkage", {
            "classes": ("collapse",),
            "fields": ("exam_timetable_entry", "exam_temp_timetable_entry"),
            "description": (
                "<strong>exam_timetable_entry</strong> → populated when published=True (points to ExamTimetable row).<br>"
                "<strong>exam_temp_timetable_entry</strong> → populated when published=False (points to ExamTempTimetable row).<br>"
                "These are set automatically by the scheduler — you rarely need to edit them here."
            ),
        }),
        ("Timestamps", {
            "classes": ("collapse",),
            "fields": ("created_at",),
        }),
    )

    # ---- list-display helpers --------------------------------------------

    def merged_courses_badges(self, obj):
        """Shows every merged course code as a coloured badge in the list view."""
        return _merged_codes_badge_list(obj.merged_courses.all().order_by("course_code"))
    merged_courses_badges.short_description = "Merged Courses"

    def base_course_code(self, obj):
        return obj.base_course.course_code if obj.base_course else "—"
    base_course_code.short_description = "Base Course"
    base_course_code.admin_order_field = "base_course__course_code"

    def venue_code(self, obj):
        return obj.venue.code if obj.venue else "—"
    venue_code.short_description = "Venue"
    venue_code.admin_order_field = "venue__code"

    def time_slot(self, obj):
        if obj.start_time and obj.end_time:
            return f"{obj.start_time:%H:%M} – {obj.end_time:%H:%M}"
        return "—"
    time_slot.short_description = "Time Slot"
    time_slot.admin_order_field = "start_time"

    # ---- detail helpers --------------------------------------------------

    def merged_courses_display(self, obj):
        """Full HTML table of every CourseAllocation in this merged group."""
        return _merged_courses_table(
            obj.merged_courses.all().order_by("course_code"),
            obj.total_students,
        )
    merged_courses_display.short_description = "All Courses in This Merged Group"


# ======================================================
# MERGED COURSE GROUP TIMETABLE ADMIN  (Regular / non-exam merges)
# ======================================================

@admin.register(MergedCourseGroupTimetable)
class MergedCourseGroupTimetableAdmin(ImportExportModelAdmin):
    """
    Regular (non-exam) timetable merged-course groups.

    IMPORTANT — `date` field explanation
    ─────────────────────────────────────
    Despite its name, the `date` field in this model is a **CharField** that
    stores the weekday label, e.g. "Monday", "Tuesday".  It is NOT a calendar
    date.  The regular timetable is week-repeating and uses day names — not
    specific dates — matching the Timetable / TempTimetable models it links to.

    Timetable linkage:
        published=True  → timetable_entry      (Timetable row)
        published=False → temp_timetable_entry (TempTimetable row)
    Both FKs point to the *base course* row in the respective timetable table.
    """
    resource_class = MergedCourseGroupTimetableResource
    inlines        = [MergedCourseGroupTimetableCoursesInline]

    # ---- list view -------------------------------------------------------
    list_display   = (
        "merged_code",
        "merged_courses_badges",   # ← actual codes visible in list
        "day_label",               # ← friendly column name (it's a CharField day, not date)
        "time_slot",
        "venue_code",
        "total_students",
        "published",
    )
    list_filter    = ("published", "date", "venue")   # `date` here = day-name CharField
    search_fields  = (
        "merged_code",
        "base_course__course_code",
        "base_course__course_name",
        "venue__code",
        "date",                          # day-name search, e.g. "Monday"
        "merged_courses__course_code",
        "merged_courses__course_name",
    )
    ordering       = ("date", "merged_code")   # alphabetical day then code
    list_per_page  = 50

    # ---- detail view -----------------------------------------------------
    readonly_fields = ("created_at", "merged_courses_display")

    fieldsets = (
        ("Merge Identity", {
            "fields": ("merged_code", "base_course", "total_students"),
            "description": (
                "<strong>merged_code</strong> is the normalised base code (e.g. COSC312). "
                "<strong>base_course</strong> is the representative CourseAllocation row."
            ),
        }),
        ("Merged Courses (full list)", {
            "fields": ("merged_courses_display",),
            "description": (
                "Every CourseAllocation in this merged group. "
                "To add or remove a course use the <em>Merged Courses</em> inline table at the bottom of this page."
            ),
        }),
        ("Timetable Session", {
            "fields": ("date", "start_time", "end_time", "venue"),
            "description": (
                "<strong>⚠ 'date' stores the weekday name (e.g. Monday), NOT a calendar date.</strong><br>"
                "The regular timetable repeats weekly — there are no specific exam dates here. "
                "start_time and end_time define the lecture/lab slot on that day."
            ),
        }),
        ("Status", {
            "fields": ("published",),
            "description": (
                "Tick <em>published</em> once the timetable is approved. "
                "This switches the active linkage from temp_timetable_entry to timetable_entry."
            ),
        }),
        ("Timetable Linkage", {
            "classes": ("collapse",),
            "fields": ("timetable_entry", "temp_timetable_entry"),
            "description": (
                "<strong>timetable_entry</strong> → populated when published=True (points to Timetable row for base course).<br>"
                "<strong>temp_timetable_entry</strong> → populated when published=False (points to TempTimetable row for base course).<br>"
                "These are set automatically by the scheduler — you rarely need to edit them here."
            ),
        }),
        ("Timestamps", {
            "classes": ("collapse",),
            "fields": ("created_at",),
        }),
    )

    # ---- list-display helpers --------------------------------------------

    def merged_courses_badges(self, obj):
        """Shows every merged course code as a coloured badge in the list view."""
        return _merged_codes_badge_list(obj.merged_courses.all().order_by("course_code"))
    merged_courses_badges.short_description = "Merged Courses"

    def day_label(self, obj):
        """
        Renders the CharField `date` (which stores a day name) with a clear label
        so it is never confused with a real calendar date.
        """
        val = obj.date or "—"
        return format_html(
            "<span style='background:#fff3cd; color:#856404; padding:1px 8px; "
            "border-radius:4px; font-size:12px; border:1px solid #ffc107;'>{}</span>",
            val,
        )
    day_label.short_description = "Day"
    day_label.admin_order_field = "date"

    def venue_code(self, obj):
        return obj.venue.code if obj.venue else "—"
    venue_code.short_description = "Venue"
    venue_code.admin_order_field = "venue__code"

    def time_slot(self, obj):
        if obj.start_time and obj.end_time:
            return f"{obj.start_time:%H:%M} – {obj.end_time:%H:%M}"
        return "—"
    time_slot.short_description = "Time Slot"
    time_slot.admin_order_field = "start_time"

    # ---- detail helpers --------------------------------------------------

    def merged_courses_display(self, obj):
        """Full HTML table of every CourseAllocation in this merged group."""
        return _merged_courses_table(
            obj.merged_courses.all().order_by("course_code"),
            obj.total_students,
        )
    merged_courses_display.short_description = "All Courses in This Merged Group"