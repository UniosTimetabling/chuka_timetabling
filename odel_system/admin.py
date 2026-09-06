from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget

from django.contrib.auth.models import User

from .models import (
    ODELCourseAllocation,
    ODELTimetableConfig,
    ODELTempTimetable,
    ODELTimetable,
    ODELExamTempTimetable,
    ODELExamTimetable,
    PublishedTimetablePDF,
)

from program_management.models import ProgramCourse
from lecturer_portal.models import Lecturer
from room_management.models import Venue


# =============================================================================
# SHARED MIXIN  (identical pattern to LecturerAdmin)
# =============================================================================

class ActionButtonsMixin:
    """
    Injects Edit / Delete action buttons into every list view,
    exactly as implemented in LecturerAdmin.
    """

    def action_buttons(self, obj):
        buttons = []
        if self.has_change_permission(self.request, obj):
            edit_url = reverse(
                "admin:%s_%s_change" % (obj._meta.app_label, obj._meta.model_name),
                args=[obj.pk],
            )
            buttons.append(f'<a class="button" href="{edit_url}">Edit</a>')
        if self.has_delete_permission(self.request, obj):
            delete_url = reverse(
                "admin:%s_%s_delete" % (obj._meta.app_label, obj._meta.model_name),
                args=[obj.pk],
            )
            buttons.append(
                f'<a class="button" style="color:red" href="{delete_url}">Delete</a>'
            )
        return format_html(" ".join(buttons))

    action_buttons.short_description = "Actions"

    def get_queryset(self, request):
        self.request = request          # required for permission checks
        return super().get_queryset(request)


# =============================================================================
# ODEL COURSE ALLOCATION
# =============================================================================

class ODELCourseAllocationResource(resources.ModelResource):

    # ForeignKey fields resolved by natural key
    program_course = fields.Field(
        column_name='program_course',
        attribute='program_course',
        widget=ForeignKeyWidget(ProgramCourse, 'course_code'),
        default=None
    )
    lecturer = fields.Field(
        column_name='lecturer',
        attribute='lecturer',
        widget=ForeignKeyWidget(Lecturer, 'email'),
        default=None
    )

    # ── Read-only computed export columns ─────────────────────────────────────
    course_code_export  = fields.Field(column_name='course_code',  attribute='program_course', readonly=True)
    course_name_export  = fields.Field(column_name='course_name',  attribute='program_course', readonly=True)
    program_name        = fields.Field(column_name='program',      attribute='program_course', readonly=True)
    department_name     = fields.Field(column_name='department',   attribute='program_course', readonly=True)
    status_label_export = fields.Field(column_name='status',       attribute='status_label',   readonly=True)

    class Meta:
        model = ODELCourseAllocation
        # program_course is the natural unique key (unique_together = ('program_course',))
        import_id_fields = ('program_course',)
        fields = (
            'program_course',
            'course_code_export', 'course_name_export',
            'program_name', 'department_name',
            'lecturer', 'number_of_students',
            'submitted_to_tt', 'submitted_to_dvc',
            'approved_by_dvc', 'rejected', 'reason_for_rejection',
            'status_label_export',
            'created_at', 'updated_at',
        )
        export_order = (
            'program_course',
            'course_code_export', 'course_name_export',
            'program_name', 'department_name',
            'lecturer', 'number_of_students',
            'approved_by_dvc', 'rejected',
            'submitted_to_dvc', 'submitted_to_tt',
            'reason_for_rejection', 'status_label_export',
            'created_at',
        )
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    # ── Dehydrate helpers (export) ────────────────────────────────────────────

    def dehydrate_course_code_export(self, obj):
        return obj.program_course.course_code if obj.program_course else ''

    def dehydrate_course_name_export(self, obj):
        return obj.program_course.course_name if obj.program_course else ''

    def dehydrate_program_name(self, obj):
        try:
            return obj.program_course.program.name
        except AttributeError:
            return ''

    def dehydrate_department_name(self, obj):
        try:
            return obj.program_course.program.department.name
        except AttributeError:
            return ''

    def dehydrate_status_label_export(self, obj):
        return obj.status_label()

    # ── Import cleaning ───────────────────────────────────────────────────────

    def before_import_row(self, row, **kwargs):
        if not row.get('program_course'):
            raise ValueError("program_course (course_code) is required")

        if not row.get('number_of_students'):
            row['number_of_students'] = 0

        if not row.get('lecturer'):
            row['lecturer'] = None

        # Booleans default to False
        for f in ('submitted_to_tt', 'submitted_to_dvc', 'approved_by_dvc', 'rejected'):
            if not row.get(f):
                row[f] = False

        if not row.get('reason_for_rejection'):
            row['reason_for_rejection'] = 'No reason yet'


@admin.register(ODELCourseAllocation)
class ODELCourseAllocationAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = ODELCourseAllocationResource

    list_display = (
        'course_code_display',
        'course_name_display',
        'program_display',
        'department_display',
        'lecturer_display',
        'students_display',
        'approval_status',
        'action_buttons',
    )
    list_filter = (
        'approved_by_dvc',
        'rejected',
        'submitted_to_dvc',
        'submitted_to_tt',
        'program_course__program__department',
    )
    search_fields = (
        'program_course__course_code',
        'program_course__course_name',
        'program_course__program__name',
        'lecturer__name',
        'lecturer__email',
    )
    ordering = ('program_course__course_code',)
    list_per_page = 30

    fieldsets = (
        ('Course', {
            'fields': ('program_course', 'lecturer', 'number_of_students')
        }),
        ('Approval Workflow', {
            'fields': (
                'submitted_to_tt',
                'submitted_to_dvc',
                'approved_by_dvc',
                'rejected',
                'reason_for_rejection',
            )
        }),
    )

    # ── Display helpers ───────────────────────────────────────────────────────

    def course_code_display(self, obj):
        return format_html('<strong>{}</strong>', obj.course_code)
    course_code_display.short_description = 'Course Code'

    def course_name_display(self, obj):
        return obj.course_name
    course_name_display.short_description = 'Course Name'

    def program_display(self, obj):
        return obj.program.name if obj.program else '—'
    program_display.short_description = 'Program'

    def department_display(self, obj):
        return obj.department.name if obj.department else '—'
    department_display.short_description = 'Department'

    def lecturer_display(self, obj):
        if obj.lecturer:
            return format_html(
                '<span style="color:#2e7d32;">{}</span>',
                obj.lecturer.display_name
            )
        return format_html('<span style="color:#FF9800;">Unassigned</span>')
    lecturer_display.short_description = 'Lecturer'

    def students_display(self, obj):
        return format_html(
            '<span style="font-weight:bold;">{}</span>', obj.number_of_students
        )
    students_display.short_description = 'Students'

    def approval_status(self, obj):
        return format_html(
            '<span style="font-size:12px;">{}</span>', obj.status_label()
        )
    approval_status.short_description = 'Status'

    def get_queryset(self, request):
        self.request = request
        return (
            super(ActionButtonsMixin, self)
            .get_queryset(request)
            .select_related(
                'program_course__program__department',
                'lecturer',
            )
        )


# =============================================================================
# ODEL TIMETABLE CONFIG
# =============================================================================

class ODELTimetableConfigResource(resources.ModelResource):

    class Meta:
        model = ODELTimetableConfig
        import_id_fields = ('id',)
        fields = (
            'id',
            'start_date', 'end_date',
            'day_start_time', 'day_end_time',
            'class_slot_size',
            'exam_slot_size', 'exam_break_duration',
            'created_at', 'updated_at',
        )
        export_order = (
            'id',
            'start_date', 'end_date',
            'day_start_time', 'day_end_time',
            'class_slot_size',
            'exam_slot_size', 'exam_break_duration',
        )
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def before_import_row(self, row, **kwargs):
        if not row.get('start_date'):
            raise ValueError("start_date is required")
        if not row.get('end_date'):
            raise ValueError("end_date is required")
        if not row.get('class_slot_size'):
            row['class_slot_size'] = 4
        if not row.get('exam_slot_size'):
            row['exam_slot_size'] = 3
        if not row.get('exam_break_duration'):
            row['exam_break_duration'] = 60


@admin.register(ODELTimetableConfig)
class ODELTimetableConfigAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = ODELTimetableConfigResource

    list_display = (
        'date_range_display',
        'time_range_display',
        'class_slot_size',
        'exam_slot_size',
        'break_display',
        'updated_at',
        'action_buttons',
    )
    search_fields = ('start_date', 'end_date')
    ordering = ('-created_at',)
    list_per_page = 25

    fieldsets = (
        ('Schedule Period', {
            'fields': ('start_date', 'end_date')
        }),
        ('Daily Hours', {
            'fields': ('day_start_time', 'day_end_time')
        }),
        ('Slot Configuration', {
            'fields': ('class_slot_size', 'exam_slot_size', 'exam_break_duration')
        }),
    )

    def date_range_display(self, obj):
        return format_html(
            '<strong>{}</strong> &rarr; <strong>{}</strong>',
            obj.start_date, obj.end_date
        )
    date_range_display.short_description = 'Date Range'

    def time_range_display(self, obj):
        return f'{obj.day_start_time.strftime("%H:%M")} – {obj.day_end_time.strftime("%H:%M")}'
    time_range_display.short_description = 'Daily Hours'

    def break_display(self, obj):
        return f'{obj.exam_break_duration} min'
    break_display.short_description = 'Exam Break'


# =============================================================================
# ODEL TEMP TIMETABLE  (draft class schedule)
# =============================================================================

class ODELTempTimetableResource(resources.ModelResource):

    course_allocation = fields.Field(
        column_name='course_allocation',
        attribute='course_allocation',
        widget=ForeignKeyWidget(ODELCourseAllocation, 'id'),
        default=None
    )
    venue = fields.Field(
        column_name='venue',
        attribute='venue',
        widget=ForeignKeyWidget(Venue, 'id'),
        default=None
    )
    created_by = fields.Field(
        column_name='created_by',
        attribute='created_by',
        widget=ForeignKeyWidget(User, 'username'),
        default=None
    )

    # read-only export helpers
    course_code = fields.Field(
        column_name='course_code', attribute='course_allocation', readonly=True
    )
    venue_name = fields.Field(
        column_name='venue_name', attribute='venue', readonly=True
    )

    class Meta:
        model = ODELTempTimetable
        # unique_together = ('venue', 'date', 'start_time', 'end_time')
        import_id_fields = ('venue', 'date', 'start_time', 'end_time')
        fields = (
            'course_allocation', 'course_code',
            'venue', 'venue_name',
            'date', 'start_time', 'end_time',
            'created_by', 'created_at',
        )
        export_order = (
            'course_code', 'course_allocation',
            'venue_name', 'venue',
            'date', 'start_time', 'end_time',
            'created_by', 'created_at',
        )
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def dehydrate_course_code(self, obj):
        return obj.course_allocation.course_code if obj.course_allocation else ''

    def dehydrate_venue_name(self, obj):
        return str(obj.venue) if obj.venue else ''

    def before_import_row(self, row, **kwargs):
        if not row.get('course_allocation'):
            raise ValueError("course_allocation (ID) is required")
        if not row.get('venue'):
            raise ValueError("venue (ID) is required")
        if not row.get('date'):
            raise ValueError("date is required")
        if not row.get('created_by'):
            row['created_by'] = None


@admin.register(ODELTempTimetable)
class ODELTempTimetableAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = ODELTempTimetableResource

    list_display = (
        'course_code_display',
        'venue',
        'date',
        'start_time',
        'end_time',
        'created_by',
        'created_at',
        'action_buttons',
    )
    list_filter = ('date', 'venue')
    search_fields = (
        'course_allocation__program_course__course_code',
        'venue__name',
    )
    ordering = ('date', 'start_time')
    list_per_page = 30

    fieldsets = (
        ('Draft Class Entry', {
            'fields': ('course_allocation', 'venue', 'date', 'start_time', 'end_time', 'created_by')
        }),
    )

    def course_code_display(self, obj):
        return format_html(
            '<strong style="color:#FF9800;">[TEMP]</strong> {}',
            obj.course_allocation.course_code
        )
    course_code_display.short_description = 'Course'

    def get_queryset(self, request):
        self.request = request
        return (
            super(ActionButtonsMixin, self)
            .get_queryset(request)
            .select_related('course_allocation__program_course', 'venue', 'created_by')
        )


# =============================================================================
# ODEL TIMETABLE  (approved class schedule)
# =============================================================================

class ODELTimetableResource(resources.ModelResource):

    course_allocation = fields.Field(
        column_name='course_allocation',
        attribute='course_allocation',
        widget=ForeignKeyWidget(ODELCourseAllocation, 'id'),
        default=None
    )
    venue = fields.Field(
        column_name='venue',
        attribute='venue',
        widget=ForeignKeyWidget(Venue, 'id'),
        default=None
    )
    approved_by = fields.Field(
        column_name='approved_by',
        attribute='approved_by',
        widget=ForeignKeyWidget(User, 'username'),
        default=None
    )

    # read-only export helpers
    course_code = fields.Field(
        column_name='course_code', attribute='course_allocation', readonly=True
    )
    venue_name = fields.Field(
        column_name='venue_name', attribute='venue', readonly=True
    )

    class Meta:
        model = ODELTimetable
        import_id_fields = ('venue', 'date', 'start_time', 'end_time')
        fields = (
            'course_allocation', 'course_code',
            'venue', 'venue_name',
            'date', 'start_time', 'end_time',
            'approved_by', 'approved_at', 'created_at',
        )
        export_order = (
            'course_code', 'course_allocation',
            'venue_name', 'venue',
            'date', 'start_time', 'end_time',
            'approved_by', 'approved_at',
        )
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def dehydrate_course_code(self, obj):
        return obj.course_allocation.course_code if obj.course_allocation else ''

    def dehydrate_venue_name(self, obj):
        return str(obj.venue) if obj.venue else ''

    def before_import_row(self, row, **kwargs):
        if not row.get('course_allocation'):
            raise ValueError("course_allocation (ID) is required")
        if not row.get('venue'):
            raise ValueError("venue (ID) is required")
        if not row.get('date'):
            raise ValueError("date is required")
        if not row.get('approved_by'):
            row['approved_by'] = None
        if not row.get('approved_at'):
            row['approved_at'] = None


@admin.register(ODELTimetable)
class ODELTimetableAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = ODELTimetableResource

    list_display = (
        'course_code_display',
        'venue',
        'date',
        'start_time',
        'end_time',
        'approved_by',
        'approved_at',
        'action_buttons',
    )
    list_filter = ('date', 'venue', 'approved_by')
    search_fields = (
        'course_allocation__program_course__course_code',
        'venue__name',
        'approved_by__username',
    )
    ordering = ('date', 'start_time')
    list_per_page = 30

    fieldsets = (
        ('Class Entry', {
            'fields': ('course_allocation', 'venue', 'date', 'start_time', 'end_time')
        }),
        ('Approval', {
            'fields': ('approved_by', 'approved_at')
        }),
    )

    def course_code_display(self, obj):
        return format_html('<strong>{}</strong>', obj.course_allocation.course_code)
    course_code_display.short_description = 'Course'

    def get_queryset(self, request):
        self.request = request
        return (
            super(ActionButtonsMixin, self)
            .get_queryset(request)
            .select_related('course_allocation__program_course', 'venue', 'approved_by')
        )


# =============================================================================
# ODEL EXAM TEMP TIMETABLE  (draft exam schedule)
# =============================================================================

class ODELExamTempTimetableResource(resources.ModelResource):

    course_allocation = fields.Field(
        column_name='course_allocation',
        attribute='course_allocation',
        widget=ForeignKeyWidget(ODELCourseAllocation, 'id'),
        default=None
    )
    venue = fields.Field(
        column_name='venue',
        attribute='venue',
        widget=ForeignKeyWidget(Venue, 'id'),
        default=None
    )

    course_code = fields.Field(
        column_name='course_code', attribute='course_allocation', readonly=True
    )
    venue_name = fields.Field(
        column_name='venue_name', attribute='venue', readonly=True
    )

    class Meta:
        model = ODELExamTempTimetable
        import_id_fields = ('venue', 'date', 'start_time', 'end_time')
        fields = (
            'course_allocation', 'course_code',
            'venue', 'venue_name',
            'date', 'start_time', 'end_time',
            'created_at',
        )
        export_order = (
            'course_code', 'course_allocation',
            'venue_name', 'venue',
            'date', 'start_time', 'end_time',
        )
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def dehydrate_course_code(self, obj):
        return obj.course_allocation.course_code if obj.course_allocation else ''

    def dehydrate_venue_name(self, obj):
        return str(obj.venue) if obj.venue else ''

    def before_import_row(self, row, **kwargs):
        if not row.get('course_allocation'):
            raise ValueError("course_allocation (ID) is required")
        if not row.get('venue'):
            raise ValueError("venue (ID) is required")
        if not row.get('date'):
            raise ValueError("date is required")


@admin.register(ODELExamTempTimetable)
class ODELExamTempTimetableAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = ODELExamTempTimetableResource

    list_display = (
        'course_code_display',
        'venue',
        'date',
        'start_time',
        'end_time',
        'created_at',
        'action_buttons',
    )
    list_filter = ('date', 'venue')
    search_fields = (
        'course_allocation__program_course__course_code',
        'venue__name',
    )
    ordering = ('date', 'start_time')
    list_per_page = 30

    fieldsets = (
        ('Draft Exam Entry', {
            'fields': ('course_allocation', 'venue', 'date', 'start_time', 'end_time')
        }),
    )

    def course_code_display(self, obj):
        return format_html(
            '<strong style="color:#FF9800;">[TEMP EXAM]</strong> {}',
            obj.course_allocation.course_code
        )
    course_code_display.short_description = 'Course'

    def get_queryset(self, request):
        self.request = request
        return (
            super(ActionButtonsMixin, self)
            .get_queryset(request)
            .select_related('course_allocation__program_course', 'venue')
        )


# =============================================================================
# ODEL EXAM TIMETABLE  (approved exam schedule)
# =============================================================================

class ODELExamTimetableResource(resources.ModelResource):

    course_allocation = fields.Field(
        column_name='course_allocation',
        attribute='course_allocation',
        widget=ForeignKeyWidget(ODELCourseAllocation, 'id'),
        default=None
    )
    venue = fields.Field(
        column_name='venue',
        attribute='venue',
        widget=ForeignKeyWidget(Venue, 'id'),
        default=None
    )
    approved_by = fields.Field(
        column_name='approved_by',
        attribute='approved_by',
        widget=ForeignKeyWidget(User, 'username'),
        default=None
    )

    course_code = fields.Field(
        column_name='course_code', attribute='course_allocation', readonly=True
    )
    venue_name = fields.Field(
        column_name='venue_name', attribute='venue', readonly=True
    )

    class Meta:
        model = ODELExamTimetable
        import_id_fields = ('venue', 'date', 'start_time', 'end_time')
        fields = (
            'course_allocation', 'course_code',
            'venue', 'venue_name',
            'date', 'start_time', 'end_time',
            'approved_by', 'approved_at', 'created_at',
        )
        export_order = (
            'course_code', 'course_allocation',
            'venue_name', 'venue',
            'date', 'start_time', 'end_time',
            'approved_by', 'approved_at',
        )
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def dehydrate_course_code(self, obj):
        return obj.course_allocation.course_code if obj.course_allocation else ''

    def dehydrate_venue_name(self, obj):
        return str(obj.venue) if obj.venue else ''

    def before_import_row(self, row, **kwargs):
        if not row.get('course_allocation'):
            raise ValueError("course_allocation (ID) is required")
        if not row.get('venue'):
            raise ValueError("venue (ID) is required")
        if not row.get('date'):
            raise ValueError("date is required")
        if not row.get('approved_by'):
            row['approved_by'] = None
        if not row.get('approved_at'):
            row['approved_at'] = None


@admin.register(ODELExamTimetable)
class ODELExamTimetableAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = ODELExamTimetableResource

    list_display = (
        'course_code_display',
        'venue',
        'date',
        'start_time',
        'end_time',
        'approved_by',
        'approved_at',
        'action_buttons',
    )
    list_filter = ('date', 'venue', 'approved_by')
    search_fields = (
        'course_allocation__program_course__course_code',
        'venue__name',
        'approved_by__username',
    )
    ordering = ('date', 'start_time')
    list_per_page = 30

    fieldsets = (
        ('Exam Entry', {
            'fields': ('course_allocation', 'venue', 'date', 'start_time', 'end_time')
        }),
        ('Approval', {
            'fields': ('approved_by', 'approved_at')
        }),
    )

    def course_code_display(self, obj):
        return format_html(
            '<span style="background:#6a1b9a;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:11px;">EXAM</span> {}',
            obj.course_allocation.course_code
        )
    course_code_display.short_description = 'Course'

    def get_queryset(self, request):
        self.request = request
        return (
            super(ActionButtonsMixin, self)
            .get_queryset(request)
            .select_related('course_allocation__program_course', 'venue', 'approved_by')
        )


# =============================================================================
# PUBLISHED TIMETABLE PDF
# =============================================================================

class PublishedTimetablePDFResource(resources.ModelResource):

    # read-only computed export columns
    timetable_type_label = fields.Field(
        column_name='timetable_type',
        attribute='is_class_timetable',
        readonly=True
    )
    published_at_formatted = fields.Field(
        column_name='published_at_formatted',
        attribute='published_at',
        readonly=True
    )

    class Meta:
        model = PublishedTimetablePDF
        # (is_class_timetable, version) is the natural unique pair
        import_id_fields = ('is_class_timetable', 'version')
        fields = (
            'id',
            'is_class_timetable',
            'timetable_type_label',
            'version',
            'is_latest',
            'published_at',
            'published_at_formatted',
        )
        export_order = (
            'id',
            'timetable_type_label',
            'version',
            'is_latest',
            'published_at',
            'published_at_formatted',
        )
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    # ── Dehydrate helpers ─────────────────────────────────────────────────────

    def dehydrate_timetable_type_label(self, obj):
        return 'Class Timetable' if obj.is_class_timetable else 'Exam Timetable'

    def dehydrate_published_at_formatted(self, obj):
        return obj.published_at.strftime('%Y-%m-%d %H:%M') if obj.published_at else ''

    # ── Import cleaning ───────────────────────────────────────────────────────

    def before_import_row(self, row, **kwargs):
        # Normalise is_class_timetable to a real boolean
        val = row.get('is_class_timetable', False)
        if isinstance(val, str):
            row['is_class_timetable'] = val.strip().lower() in ('true', '1', 'yes')

        if not row.get('version'):
            row['version'] = 1

        if row.get('is_latest') is None:
            row['is_latest'] = True


@admin.register(PublishedTimetablePDF)
class PublishedTimetablePDFAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = PublishedTimetablePDFResource

    list_display = (
        'timetable_type_badge',
        'version_display',
        'is_latest_badge',
        'published_at',
        'pdf_link',
        'action_buttons',
    )
    list_filter = ('is_class_timetable', 'is_latest')
    search_fields = ('version',)
    ordering = ('-published_at', '-version')
    list_per_page = 25

    fieldsets = (
        ('Timetable PDF', {
            'fields': (
                'is_class_timetable',
                'pdf_file',
                'version',
                'is_latest',
                'published_at',
            )
        }),
    )

    # ── Display helpers ───────────────────────────────────────────────────────

    def timetable_type_badge(self, obj):
        color = '#1976d2' if obj.is_class_timetable else '#6a1b9a'
        label = 'Class Timetable' if obj.is_class_timetable else 'Exam Timetable'
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 10px;'
            'border-radius:4px;font-size:11px;">{}</span>',
            color, label
        )
    timetable_type_badge.short_description = 'Type'

    def version_display(self, obj):
        return format_html(
            '<span style="font-weight:bold;font-size:13px;">v{}</span>', obj.version
        )
    version_display.short_description = 'Version'

    def is_latest_badge(self, obj):
        if obj.is_latest:
            return format_html(
                '<span style="color:#4CAF50;font-weight:bold;">&#10003; Latest</span>'
            )
        return format_html('<span style="color:#aaa;">Archived</span>')
    is_latest_badge.short_description = 'Latest?'

    def pdf_link(self, obj):
        if obj.pdf_file:
            return format_html(
                '<a href="{}" target="_blank" class="button" '
                'style="font-size:11px;">&#128196; View PDF</a>',
                obj.pdf_file.url
            )
        return format_html('<span style="color:#aaa;">No file</span>')
    pdf_link.short_description = 'PDF'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request)