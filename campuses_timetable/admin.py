from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget, BooleanWidget, IntegerWidget

from django.contrib.auth.models import User

from .models import (
    Campus,
    CampusCourseAllocation,
    CampusLabAllocation,
    CampusSubmissionControl,
    CampusArchivedCourseAllocation,
    CampusSchedulerConfig,
    CampusExamSchedulerConfig,
    CampusTimetable,
    CampusTempTimetable,
    CampusExamTimetable,
    CampusExamTempTimetable,
    CampusLabTimetable,
    CampusLabExamTimetable,
    CampusTimetableArchive,
    CampusPublishedTimetablePDF,
    CampusTimetableTemplate,
)

from department_management.models import Department
from program_management.models import Program, ProgramCourse
from lecturer_portal.models import Lecturer


# ══════════════════════════════════════════════════════════════════════════════
# SHARED HELPER MIXIN
# ══════════════════════════════════════════════════════════════════════════════

class ActionButtonsMixin:
    """
    Reusable mixin that injects Edit / Delete action buttons in list views,
    identical to the pattern used in LecturerAdmin.
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
        self.request = request  # required for permission checks
        return super().get_queryset(request)


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS
# ══════════════════════════════════════════════════════════════════════════════

class CampusResource(resources.ModelResource):

    class Meta:
        model = Campus
        import_id_fields = ('code',)   # campus code is the unique identifier
        fields = ('name', 'code', 'is_active', 'is_default')
        export_order = ('code', 'name', 'is_active', 'is_default')
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def before_import_row(self, row, **kwargs):
        if not row.get('code'):
            raise ValueError("Campus code is required")
        row['code'] = row['code'].strip().upper()
        if row.get('name'):
            row['name'] = row['name'].strip().title()


@admin.register(Campus)
class CampusAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusResource

    list_display = ('code', 'name', 'is_active', 'is_default', 'action_buttons')
    list_filter = ('is_active', 'is_default')
    search_fields = ('name', 'code')
    ordering = ('name',)
    list_per_page = 25

    fieldsets = (
        ('Campus Details', {
            'fields': ('name', 'code')
        }),
        ('Status', {
            'fields': ('is_active', 'is_default')
        }),
    )


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS COURSE ALLOCATION
# ══════════════════════════════════════════════════════════════════════════════

class CampusCourseAllocationResource(resources.ModelResource):

    department = fields.Field(
        column_name='department',
        attribute='department',
        widget=ForeignKeyWidget(Department, 'name'),
        default=None
    )
    origin_department = fields.Field(
        column_name='origin_department',
        attribute='origin_department',
        widget=ForeignKeyWidget(Department, 'name'),
        default=None
    )
    program = fields.Field(
        column_name='program',
        attribute='program',
        widget=ForeignKeyWidget(Program, 'name'),
        default=None
    )
    lecturer = fields.Field(
        column_name='lecturer',
        attribute='lecturer',
        widget=ForeignKeyWidget(Lecturer, 'email'),
        default=None
    )
    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )
    teaching_campus = fields.Field(
        column_name='teaching_campus',
        attribute='teaching_campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )

    # Read-only computed column for human-readable status
    status_label = fields.Field(
        column_name='status_label',
        attribute='status_label',
        readonly=True
    )

    class Meta:
        model = CampusCourseAllocation
        import_id_fields = ('course_code', 'program', 'campus')
        fields = (
            'course_code', 'course_name',
            'department', 'origin_department',
            'program', 'lecturer',
            'number_of_students',
            'campus', 'teaching_campus', 'delivery_mode',
            'approved_by_dvc', 'rejected_by_dvc', 'reason_for_disapproval',
            'submitted_to_tt',
            'status_label',
        )
        export_order = (
            'campus', 'course_code', 'course_name',
            'department', 'origin_department',
            'program', 'lecturer',
            'number_of_students', 'delivery_mode',
            'approved_by_dvc', 'rejected_by_dvc',
            'reason_for_disapproval', 'submitted_to_tt',
            'status_label',
        )
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def dehydrate_status_label(self, obj):
        return obj.status_label()

    def before_import_row(self, row, **kwargs):
        if not row.get('course_code'):
            raise ValueError("course_code is required")
        row['course_code'] = row['course_code'].strip().upper()

        if row.get('course_name'):
            row['course_name'] = row['course_name'].strip()

        if row.get('delivery_mode'):
            row['delivery_mode'] = row['delivery_mode'].strip().upper()

        # Default boolean fields
        for bool_field in ('approved_by_dvc', 'rejected_by_dvc', 'submitted_to_tt'):
            if not row.get(bool_field):
                row[bool_field] = False

        if not row.get('number_of_students'):
            row['number_of_students'] = 0

        # Nullable FKs
        for fk_field in ('origin_department', 'lecturer', 'teaching_campus'):
            if not row.get(fk_field):
                row[fk_field] = None


@admin.register(CampusCourseAllocation)
class CampusCourseAllocationAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusCourseAllocationResource

    list_display = (
        'course_code_display',
        'course_name',
        'campus_badge',
        'program_display',
        'lecturer_display',
        'students_display',
        'delivery_mode',
        'approval_status',
        'action_buttons',
    )
    list_filter = (
        'campus',
        'delivery_mode',
        'approved_by_dvc',
        'rejected_by_dvc',
        'submitted_to_tt',
        'department',
    )
    search_fields = (
        'course_code',
        'course_name',
        'program__name',
        'lecturer__name',
        'lecturer__email',
        'campus__name',
        'campus__code',
    )
    ordering = ('campus__name', 'course_code')
    list_per_page = 30

    fieldsets = (
        ('Course Details', {
            'fields': ('course_code', 'course_name', 'delivery_mode', 'number_of_students')
        }),
        ('Department & Program', {
            'fields': ('department', 'origin_department', 'program')
        }),
        ('Lecturer & Campus', {
            'fields': ('lecturer', 'campus', 'teaching_campus')
        }),
        ('Approval Status', {
            'fields': ('approved_by_dvc', 'rejected_by_dvc', 'reason_for_disapproval', 'submitted_to_tt'),
            'classes': ('collapse',),
        }),
    )

    # ── Display helpers ──────────────────────────────────────────────────────

    def course_code_display(self, obj):
        return format_html('<strong>{}</strong>', obj.course_code)
    course_code_display.short_description = 'Course Code'

    def campus_badge(self, obj):
        if obj.campus:
            return format_html(
                '<span style="background:#1976d2;color:#fff;padding:2px 8px;'
                'border-radius:4px;font-size:11px;">{}</span>',
                obj.campus.code
            )
        return format_html('<span style="color:#aaa;">—</span>')
    campus_badge.short_description = 'Campus'

    def program_display(self, obj):
        return obj.program.name if obj.program else '—'
    program_display.short_description = 'Program'

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
        label = obj.status_label()
        return format_html('<span style="font-size:12px;">{}</span>', label)
    approval_status.short_description = 'Status'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related(
            'campus', 'program', 'lecturer', 'department', 'origin_department', 'teaching_campus'
        )


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS LAB ALLOCATION
# ══════════════════════════════════════════════════════════════════════════════

class CampusLabAllocationResource(resources.ModelResource):

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
    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )

    class Meta:
        model = CampusLabAllocation
        import_id_fields = ('program_course', 'campus')
        fields = ('program_course', 'lecturer', 'number_of_students', 'campus', 'created_at')
        export_order = ('campus', 'program_course', 'lecturer', 'number_of_students', 'created_at')
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def before_import_row(self, row, **kwargs):
        if not row.get('program_course'):
            raise ValueError("program_course is required")
        if not row.get('number_of_students'):
            row['number_of_students'] = 0
        if not row.get('lecturer'):
            row['lecturer'] = None
        if not row.get('campus'):
            row['campus'] = None


@admin.register(CampusLabAllocation)
class CampusLabAllocationAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusLabAllocationResource

    list_display = (
        'program_course_display',
        'campus_badge',
        'lecturer_display',
        'number_of_students',
        'created_at',
        'action_buttons',
    )
    list_filter = ('campus',)
    search_fields = (
        'program_course__course_code',
        'program_course__course_name',
        'lecturer__name',
        'campus__code',
    )
    ordering = ('campus__name', 'program_course__course_code')
    list_per_page = 30

    fieldsets = (
        ('Lab Allocation', {
            'fields': ('program_course', 'lecturer', 'number_of_students', 'campus')
        }),
    )

    def program_course_display(self, obj):
        return format_html('<strong>{}</strong>', obj.program_course.course_code)
    program_course_display.short_description = 'Course'

    def campus_badge(self, obj):
        if obj.campus:
            return format_html(
                '<span style="background:#1976d2;color:#fff;padding:2px 8px;'
                'border-radius:4px;font-size:11px;">{}</span>',
                obj.campus.code
            )
        return '—'
    campus_badge.short_description = 'Campus'

    def lecturer_display(self, obj):
        if obj.lecturer:
            return format_html('<span style="color:#2e7d32;">{}</span>', obj.lecturer.display_name)
        return format_html('<span style="color:#FF9800;">Unassigned</span>')
    lecturer_display.short_description = 'Lecturer'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related(
            'campus', 'program_course', 'lecturer'
        )


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS SUBMISSION CONTROL
# ══════════════════════════════════════════════════════════════════════════════

class CampusSubmissionControlResource(resources.ModelResource):

    department = fields.Field(
        column_name='department',
        attribute='department',
        widget=ForeignKeyWidget(Department, 'name'),
        default=None
    )
    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )

    class Meta:
        model = CampusSubmissionControl
        import_id_fields = ('department', 'campus')
        fields = ('department', 'campus', 'allow_submission_to_dvc', 'allow_submission_to_tt', 'updated_at')
        export_order = ('campus', 'department', 'allow_submission_to_dvc', 'allow_submission_to_tt', 'updated_at')
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def before_import_row(self, row, **kwargs):
        if not row.get('department'):
            row['department'] = None
        if not row.get('campus'):
            row['campus'] = None
        for bool_field in ('allow_submission_to_dvc', 'allow_submission_to_tt'):
            if row.get(bool_field) is None:
                row[bool_field] = True


@admin.register(CampusSubmissionControl)
class CampusSubmissionControlAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusSubmissionControlResource

    list_display = (
        'department_display',
        'campus_badge',
        'dvc_status',
        'tt_status',
        'updated_at',
        'action_buttons',
    )
    list_filter = ('campus', 'allow_submission_to_dvc', 'allow_submission_to_tt')
    search_fields = ('department__name', 'campus__code', 'campus__name')
    ordering = ('campus__name', 'department__name')
    list_per_page = 25

    fieldsets = (
        ('Scope', {
            'fields': ('department', 'campus')
        }),
        ('Submission Gates', {
            'fields': ('allow_submission_to_dvc', 'allow_submission_to_tt')
        }),
    )

    def department_display(self, obj):
        return obj.department.name if obj.department else 'GLOBAL'
    department_display.short_description = 'Department'

    def campus_badge(self, obj):
        if obj.campus:
            return format_html(
                '<span style="background:#1976d2;color:#fff;padding:2px 8px;'
                'border-radius:4px;font-size:11px;">{}</span>',
                obj.campus.code
            )
        return format_html('<span style="color:#aaa;">ALL</span>')
    campus_badge.short_description = 'Campus'

    def dvc_status(self, obj):
        if obj.allow_submission_to_dvc:
            return format_html('<span style="color:#4CAF50;font-weight:bold;">✓ Open</span>')
        return format_html('<span style="color:#F44336;font-weight:bold;">✗ Closed</span>')
    dvc_status.short_description = 'DVC Submission'

    def tt_status(self, obj):
        if obj.allow_submission_to_tt:
            return format_html('<span style="color:#4CAF50;font-weight:bold;">✓ Open</span>')
        return format_html('<span style="color:#F44336;font-weight:bold;">✗ Closed</span>')
    tt_status.short_description = 'Timetable Submission'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related('campus', 'department')


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS ARCHIVED COURSE ALLOCATION
# ══════════════════════════════════════════════════════════════════════════════

class CampusArchivedCourseAllocationResource(resources.ModelResource):

    department = fields.Field(
        column_name='department',
        attribute='department',
        widget=ForeignKeyWidget(Department, 'name'),
        default=None
    )
    origin_department = fields.Field(
        column_name='origin_department',
        attribute='origin_department',
        widget=ForeignKeyWidget(Department, 'name'),
        default=None
    )
    program = fields.Field(
        column_name='program',
        attribute='program',
        widget=ForeignKeyWidget(Program, 'name'),
        default=None
    )
    lecturer = fields.Field(
        column_name='lecturer',
        attribute='lecturer',
        widget=ForeignKeyWidget(Lecturer, 'email'),
        default=None
    )
    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )
    archived_by = fields.Field(
        column_name='archived_by',
        attribute='archived_by',
        widget=ForeignKeyWidget(User, 'username'),
        default=None
    )

    class Meta:
        model = CampusArchivedCourseAllocation
        import_id_fields = ('course_code', 'program', 'semester', 'campus')
        fields = (
            'department', 'semester', 'archived_by', 'archived_at',
            'course_code', 'course_name',
            'origin_department', 'program', 'lecturer',
            'number_of_students',
            'approved_by_dvc', 'rejected_by_dvc',
            'reason_for_disapproval', 'submitted_to_tt',
            'campus',
        )
        export_order = (
            'campus', 'semester', 'course_code', 'course_name',
            'department', 'program', 'lecturer',
            'number_of_students', 'archived_by', 'archived_at',
            'approved_by_dvc', 'rejected_by_dvc',
            'reason_for_disapproval', 'submitted_to_tt',
        )
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def before_import_row(self, row, **kwargs):
        if not row.get('course_code'):
            raise ValueError("course_code is required")
        row['course_code'] = row['course_code'].strip().upper()
        if row.get('course_name'):
            row['course_name'] = row['course_name'].strip()
        if not row.get('number_of_students'):
            row['number_of_students'] = 0
        for bool_field in ('approved_by_dvc', 'rejected_by_dvc', 'submitted_to_tt'):
            if not row.get(bool_field):
                row[bool_field] = False
        for fk_field in ('origin_department', 'lecturer', 'archived_by'):
            if not row.get(fk_field):
                row[fk_field] = None


@admin.register(CampusArchivedCourseAllocation)
class CampusArchivedCourseAllocationAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusArchivedCourseAllocationResource

    list_display = (
        'course_code',
        'course_name',
        'campus_badge',
        'semester',
        'department_display',
        'lecturer_display',
        'archived_at',
        'archived_by',
        'action_buttons',
    )
    list_filter = ('campus', 'semester', 'department', 'approved_by_dvc', 'submitted_to_tt')
    search_fields = (
        'course_code',
        'course_name',
        'semester',
        'campus__code',
        'department__name',
        'lecturer__name',
    )
    ordering = ('-archived_at',)
    list_per_page = 30

    fieldsets = (
        ('Archive Info', {
            'fields': ('semester', 'archived_by', 'archived_at', 'campus')
        }),
        ('Course', {
            'fields': ('course_code', 'course_name', 'department', 'origin_department',
                       'program', 'lecturer', 'number_of_students')
        }),
        ('Status', {
            'fields': ('approved_by_dvc', 'rejected_by_dvc', 'reason_for_disapproval', 'submitted_to_tt'),
            'classes': ('collapse',),
        }),
    )

    def department_display(self, obj):
        return obj.department.name if obj.department else '—'
    department_display.short_description = 'Department'

    def campus_badge(self, obj):
        if obj.campus:
            return format_html(
                '<span style="background:#6a1b9a;color:#fff;padding:2px 8px;'
                'border-radius:4px;font-size:11px;">{}</span>',
                obj.campus.code
            )
        return '—'
    campus_badge.short_description = 'Campus'

    def lecturer_display(self, obj):
        if obj.lecturer:
            return obj.lecturer.display_name
        return format_html('<span style="color:#aaa;">—</span>')
    lecturer_display.short_description = 'Lecturer'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related(
            'campus', 'department', 'origin_department', 'program', 'lecturer', 'archived_by'
        )


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS SCHEDULER CONFIG
# ══════════════════════════════════════════════════════════════════════════════

class CampusSchedulerConfigResource(resources.ModelResource):

    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )

    class Meta:
        model = CampusSchedulerConfig
        import_id_fields = ('campus',)
        fields = ('campus', 'start_time', 'end_time', 'slot_size')
        export_order = ('campus', 'start_time', 'end_time', 'slot_size')
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def before_import_row(self, row, **kwargs):
        if not row.get('campus'):
            row['campus'] = None
        if not row.get('slot_size'):
            row['slot_size'] = 3


@admin.register(CampusSchedulerConfig)
class CampusSchedulerConfigAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusSchedulerConfigResource

    list_display = (
        'campus_display',
        'start_time',
        'end_time',
        'slot_size_display',
        'action_buttons',
    )
    list_filter = ('campus',)
    search_fields = ('campus__code', 'campus__name')
    ordering = ('campus__name',)
    list_per_page = 25

    fieldsets = (
        ('Campus', {
            'fields': ('campus',)
        }),
        ('Schedule Settings', {
            'fields': ('start_time', 'end_time', 'slot_size')
        }),
    )

    def campus_display(self, obj):
        return obj.campus.code if obj.campus else 'DEFAULT'
    campus_display.short_description = 'Campus'

    def slot_size_display(self, obj):
        return f'{obj.slot_size} hrs'
    slot_size_display.short_description = 'Slot Size'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related('campus')


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS EXAM SCHEDULER CONFIG
# ══════════════════════════════════════════════════════════════════════════════

class CampusExamSchedulerConfigResource(resources.ModelResource):

    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )

    class Meta:
        model = CampusExamSchedulerConfig
        import_id_fields = ('campus',)
        fields = (
            'campus', 'start_date', 'start_time', 'end_time',
            'slot_size', 'excluded_days', 'max_exam_days', 'spacing_ratio',
        )
        export_order = (
            'campus', 'start_date', 'start_time', 'end_time',
            'slot_size', 'max_exam_days', 'spacing_ratio', 'excluded_days',
        )
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def before_import_row(self, row, **kwargs):
        if not row.get('campus'):
            row['campus'] = None
        if not row.get('slot_size'):
            row['slot_size'] = 2
        if not row.get('max_exam_days'):
            row['max_exam_days'] = 14
        if not row.get('spacing_ratio'):
            row['spacing_ratio'] = 0.7
        if not row.get('excluded_days'):
            row['excluded_days'] = ''


@admin.register(CampusExamSchedulerConfig)
class CampusExamSchedulerConfigAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusExamSchedulerConfigResource

    list_display = (
        'campus_display',
        'start_date',
        'start_time',
        'end_time',
        'slot_size_display',
        'max_exam_days',
        'spacing_ratio',
        'action_buttons',
    )
    list_filter = ('campus',)
    search_fields = ('campus__code', 'campus__name')
    ordering = ('campus__name',)
    list_per_page = 25

    fieldsets = (
        ('Campus', {
            'fields': ('campus',)
        }),
        ('Exam Period', {
            'fields': ('start_date', 'start_time', 'end_time', 'max_exam_days')
        }),
        ('Scheduling Parameters', {
            'fields': ('slot_size', 'spacing_ratio', 'excluded_days')
        }),
    )

    def campus_display(self, obj):
        return obj.campus.code if obj.campus else 'DEFAULT'
    campus_display.short_description = 'Campus'

    def slot_size_display(self, obj):
        return f'{obj.slot_size} hrs'
    slot_size_display.short_description = 'Slot Size'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related('campus')


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS TIMETABLE
# ══════════════════════════════════════════════════════════════════════════════

class CampusTimetableResource(resources.ModelResource):

    course_allocation = fields.Field(
        column_name='course_allocation',
        attribute='course_allocation',
        widget=ForeignKeyWidget(CampusCourseAllocation, 'id'),
        default=None
    )
    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )

    # Read-only: export human-readable course code
    course_code = fields.Field(column_name='course_code', attribute='course_allocation', readonly=True)

    class Meta:
        model = CampusTimetable
        import_id_fields = ('course_allocation', 'day', 'start_time', 'end_time')
        fields = ('course_allocation', 'course_code', 'day', 'start_time', 'end_time', 'campus')
        export_order = ('campus', 'course_code', 'course_allocation', 'day', 'start_time', 'end_time')
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def dehydrate_course_code(self, obj):
        return obj.course_allocation.course_code if obj.course_allocation else '—'

    def before_import_row(self, row, **kwargs):
        if not row.get('course_allocation'):
            raise ValueError("course_allocation ID is required")
        if not row.get('day'):
            raise ValueError("day is required")
        row['day'] = row['day'].strip().capitalize()
        if not row.get('campus'):
            row['campus'] = None


@admin.register(CampusTimetable)
class CampusTimetableAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusTimetableResource

    list_display = (
        'course_code_display',
        'campus_badge',
        'day',
        'start_time',
        'end_time',
        'action_buttons',
    )
    list_filter = ('campus', 'day')
    search_fields = ('course_allocation__course_code', 'campus__code', 'day')
    ordering = ('campus__name', 'day', 'start_time')
    list_per_page = 30

    fieldsets = (
        ('Timetable Entry', {
            'fields': ('course_allocation', 'day', 'start_time', 'end_time', 'campus')
        }),
    )

    def course_code_display(self, obj):
        return format_html('<strong>{}</strong>', obj.course_allocation.course_code)
    course_code_display.short_description = 'Course Code'

    def campus_badge(self, obj):
        if obj.campus:
            return format_html(
                '<span style="background:#1976d2;color:#fff;padding:2px 8px;'
                'border-radius:4px;font-size:11px;">{}</span>',
                obj.campus.code
            )
        return '—'
    campus_badge.short_description = 'Campus'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related(
            'campus', 'course_allocation'
        )


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS TEMP TIMETABLE
# ══════════════════════════════════════════════════════════════════════════════

class CampusTempTimetableResource(resources.ModelResource):

    course_allocation = fields.Field(
        column_name='course_allocation',
        attribute='course_allocation',
        widget=ForeignKeyWidget(CampusCourseAllocation, 'id'),
        default=None
    )
    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )
    course_code = fields.Field(column_name='course_code', attribute='course_allocation', readonly=True)

    class Meta:
        model = CampusTempTimetable
        import_id_fields = ('course_allocation', 'day', 'start_time', 'end_time')
        fields = ('course_allocation', 'course_code', 'day', 'start_time', 'end_time', 'campus')
        export_order = ('campus', 'course_code', 'day', 'start_time', 'end_time')
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def dehydrate_course_code(self, obj):
        return obj.course_allocation.course_code if obj.course_allocation else '—'

    def before_import_row(self, row, **kwargs):
        if not row.get('course_allocation'):
            raise ValueError("course_allocation ID is required")
        row['day'] = row.get('day', '').strip().capitalize()
        if not row.get('campus'):
            row['campus'] = None


@admin.register(CampusTempTimetable)
class CampusTempTimetableAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusTempTimetableResource

    list_display = (
        'course_code_display',
        'campus_badge',
        'day',
        'start_time',
        'end_time',
        'action_buttons',
    )
    list_filter = ('campus', 'day')
    search_fields = ('course_allocation__course_code', 'campus__code', 'day')
    ordering = ('campus__name', 'day', 'start_time')
    list_per_page = 30

    fieldsets = (
        ('Temp Timetable Entry', {
            'fields': ('course_allocation', 'day', 'start_time', 'end_time', 'campus')
        }),
    )

    def course_code_display(self, obj):
        return format_html(
            '<strong style="color:#FF9800;">[TEMP]</strong> {}',
            obj.course_allocation.course_code
        )
    course_code_display.short_description = 'Course Code'

    def campus_badge(self, obj):
        if obj.campus:
            return format_html(
                '<span style="background:#1976d2;color:#fff;padding:2px 8px;'
                'border-radius:4px;font-size:11px;">{}</span>',
                obj.campus.code
            )
        return '—'
    campus_badge.short_description = 'Campus'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related(
            'campus', 'course_allocation'
        )


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS EXAM TIMETABLE
# ══════════════════════════════════════════════════════════════════════════════

class CampusExamTimetableResource(resources.ModelResource):

    course_allocation = fields.Field(
        column_name='course_allocation',
        attribute='course_allocation',
        widget=ForeignKeyWidget(CampusCourseAllocation, 'id'),
        default=None
    )
    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )
    course_code = fields.Field(column_name='course_code', attribute='course_allocation', readonly=True)

    class Meta:
        model = CampusExamTimetable
        import_id_fields = ('course_allocation', 'date', 'start_time', 'end_time')
        fields = ('course_allocation', 'course_code', 'day', 'date', 'start_time', 'end_time', 'campus')
        export_order = ('campus', 'course_code', 'date', 'day', 'start_time', 'end_time')
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def dehydrate_course_code(self, obj):
        return obj.course_allocation.course_code if obj.course_allocation else '—'

    def before_import_row(self, row, **kwargs):
        if not row.get('course_allocation'):
            raise ValueError("course_allocation ID is required")
        if not row.get('date'):
            raise ValueError("date is required for exam timetable")
        if row.get('day'):
            row['day'] = row['day'].strip().capitalize()
        if not row.get('campus'):
            row['campus'] = None


@admin.register(CampusExamTimetable)
class CampusExamTimetableAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusExamTimetableResource

    list_display = (
        'course_code_display',
        'campus_badge',
        'date',
        'day',
        'start_time',
        'end_time',
        'action_buttons',
    )
    list_filter = ('campus', 'day', 'date')
    search_fields = ('course_allocation__course_code', 'campus__code', 'day')
    ordering = ('date', 'start_time')
    list_per_page = 30

    fieldsets = (
        ('Exam Timetable Entry', {
            'fields': ('course_allocation', 'date', 'day', 'start_time', 'end_time', 'campus')
        }),
    )

    def course_code_display(self, obj):
        return format_html('<strong>{}</strong>', obj.course_allocation.course_code)
    course_code_display.short_description = 'Course Code'

    def campus_badge(self, obj):
        if obj.campus:
            return format_html(
                '<span style="background:#1976d2;color:#fff;padding:2px 8px;'
                'border-radius:4px;font-size:11px;">{}</span>',
                obj.campus.code
            )
        return '—'
    campus_badge.short_description = 'Campus'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related(
            'campus', 'course_allocation'
        )


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS EXAM TEMP TIMETABLE
# ══════════════════════════════════════════════════════════════════════════════

class CampusExamTempTimetableResource(resources.ModelResource):

    course_allocation = fields.Field(
        column_name='course_allocation',
        attribute='course_allocation',
        widget=ForeignKeyWidget(CampusCourseAllocation, 'id'),
        default=None
    )
    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )
    course_code = fields.Field(column_name='course_code', attribute='course_allocation', readonly=True)

    class Meta:
        model = CampusExamTempTimetable
        import_id_fields = ('course_allocation', 'date', 'start_time', 'end_time')
        fields = ('course_allocation', 'course_code', 'day', 'date', 'start_time', 'end_time', 'campus')
        export_order = ('campus', 'course_code', 'date', 'day', 'start_time', 'end_time')
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def dehydrate_course_code(self, obj):
        return obj.course_allocation.course_code if obj.course_allocation else '—'

    def before_import_row(self, row, **kwargs):
        if not row.get('course_allocation'):
            raise ValueError("course_allocation ID is required")
        if not row.get('date'):
            raise ValueError("date is required")
        if row.get('day'):
            row['day'] = row['day'].strip().capitalize()
        if not row.get('campus'):
            row['campus'] = None


@admin.register(CampusExamTempTimetable)
class CampusExamTempTimetableAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusExamTempTimetableResource

    list_display = (
        'course_code_display',
        'campus_badge',
        'date',
        'day',
        'start_time',
        'end_time',
        'action_buttons',
    )
    list_filter = ('campus', 'day')
    search_fields = ('course_allocation__course_code', 'campus__code')
    ordering = ('date', 'start_time')
    list_per_page = 30

    fieldsets = (
        ('Temp Exam Timetable Entry', {
            'fields': ('course_allocation', 'date', 'day', 'start_time', 'end_time', 'campus')
        }),
    )

    def course_code_display(self, obj):
        return format_html(
            '<strong style="color:#FF9800;">[TEMP]</strong> {}',
            obj.course_allocation.course_code
        )
    course_code_display.short_description = 'Course Code'

    def campus_badge(self, obj):
        if obj.campus:
            return format_html(
                '<span style="background:#1976d2;color:#fff;padding:2px 8px;'
                'border-radius:4px;font-size:11px;">{}</span>',
                obj.campus.code
            )
        return '—'
    campus_badge.short_description = 'Campus'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related(
            'campus', 'course_allocation'
        )


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS LAB TIMETABLE
# ══════════════════════════════════════════════════════════════════════════════

class CampusLabTimetableResource(resources.ModelResource):

    lab_allocation = fields.Field(
        column_name='lab_allocation',
        attribute='lab_allocation',
        widget=ForeignKeyWidget(CampusLabAllocation, 'id'),
        default=None
    )
    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )
    course_code = fields.Field(column_name='course_code', attribute='lab_allocation', readonly=True)

    class Meta:
        model = CampusLabTimetable
        import_id_fields = ('lab_allocation', 'day', 'start_time', 'end_time')
        fields = ('lab_allocation', 'course_code', 'day', 'start_time', 'end_time', 'campus')
        export_order = ('campus', 'course_code', 'day', 'start_time', 'end_time')
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def dehydrate_course_code(self, obj):
        return obj.lab_allocation.program_course.course_code if obj.lab_allocation else '—'

    def before_import_row(self, row, **kwargs):
        if not row.get('lab_allocation'):
            raise ValueError("lab_allocation ID is required")
        if row.get('day'):
            row['day'] = row['day'].strip().capitalize()
        if not row.get('campus'):
            row['campus'] = None


@admin.register(CampusLabTimetable)
class CampusLabTimetableAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusLabTimetableResource

    list_display = (
        'course_code_display',
        'campus_badge',
        'day',
        'start_time',
        'end_time',
        'updated_at',
        'action_buttons',
    )
    list_filter = ('campus', 'day')
    search_fields = ('lab_allocation__program_course__course_code', 'campus__code')
    ordering = ('campus__name', 'day', 'start_time')
    list_per_page = 30

    fieldsets = (
        ('Lab Timetable Entry', {
            'fields': ('lab_allocation', 'day', 'start_time', 'end_time', 'campus')
        }),
    )

    def course_code_display(self, obj):
        return format_html(
            '<strong>{}</strong> <span style="color:#aaa;">[LAB]</span>',
            obj.lab_allocation.program_course.course_code
        )
    course_code_display.short_description = 'Course'

    def campus_badge(self, obj):
        if obj.campus:
            return format_html(
                '<span style="background:#1976d2;color:#fff;padding:2px 8px;'
                'border-radius:4px;font-size:11px;">{}</span>',
                obj.campus.code
            )
        return '—'
    campus_badge.short_description = 'Campus'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related(
            'campus', 'lab_allocation__program_course'
        )


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS LAB EXAM TIMETABLE
# ══════════════════════════════════════════════════════════════════════════════

class CampusLabExamTimetableResource(resources.ModelResource):

    lab_allocation = fields.Field(
        column_name='lab_allocation',
        attribute='lab_allocation',
        widget=ForeignKeyWidget(CampusLabAllocation, 'id'),
        default=None
    )
    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )
    course_code = fields.Field(column_name='course_code', attribute='lab_allocation', readonly=True)

    class Meta:
        model = CampusLabExamTimetable
        import_id_fields = ('lab_allocation', 'date', 'start_time', 'end_time')
        fields = ('lab_allocation', 'course_code', 'date', 'day', 'start_time', 'end_time', 'campus')
        export_order = ('campus', 'course_code', 'date', 'day', 'start_time', 'end_time')
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def dehydrate_course_code(self, obj):
        return obj.lab_allocation.program_course.course_code if obj.lab_allocation else '—'

    def before_import_row(self, row, **kwargs):
        if not row.get('lab_allocation'):
            raise ValueError("lab_allocation ID is required")
        if not row.get('date'):
            raise ValueError("date is required")
        if row.get('day'):
            row['day'] = row['day'].strip().capitalize()
        if not row.get('campus'):
            row['campus'] = None


@admin.register(CampusLabExamTimetable)
class CampusLabExamTimetableAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusLabExamTimetableResource

    list_display = (
        'course_code_display',
        'campus_badge',
        'date',
        'day',
        'start_time',
        'end_time',
        'action_buttons',
    )
    list_filter = ('campus', 'day')
    search_fields = ('lab_allocation__program_course__course_code', 'campus__code')
    ordering = ('date', 'start_time')
    list_per_page = 30

    fieldsets = (
        ('Lab Exam Timetable Entry', {
            'fields': ('lab_allocation', 'date', 'day', 'start_time', 'end_time', 'campus')
        }),
    )

    def course_code_display(self, obj):
        return format_html(
            '<strong>{}</strong> <span style="color:#aaa;">[LAB EXAM]</span>',
            obj.lab_allocation.program_course.course_code
        )
    course_code_display.short_description = 'Course'

    def campus_badge(self, obj):
        if obj.campus:
            return format_html(
                '<span style="background:#1976d2;color:#fff;padding:2px 8px;'
                'border-radius:4px;font-size:11px;">{}</span>',
                obj.campus.code
            )
        return '—'
    campus_badge.short_description = 'Campus'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related(
            'campus', 'lab_allocation__program_course'
        )


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS TIMETABLE ARCHIVE
# ══════════════════════════════════════════════════════════════════════════════

class CampusTimetableArchiveResource(resources.ModelResource):

    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )
    archived_by = fields.Field(
        column_name='archived_by',
        attribute='archived_by',
        widget=ForeignKeyWidget(User, 'username'),
        default=None
    )

    class Meta:
        model = CampusTimetableArchive
        import_id_fields = ('semester', 'academic_year', 'timetable_type', 'campus')
        fields = (
            'timetable_type', 'semester', 'academic_year',
            'campus', 'archived_by', 'archived_at', 'data',
        )
        export_order = (
            'campus', 'timetable_type', 'semester', 'academic_year',
            'archived_by', 'archived_at',
        )
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def before_import_row(self, row, **kwargs):
        if not row.get('semester'):
            raise ValueError("semester is required")
        if not row.get('academic_year'):
            raise ValueError("academic_year is required")
        if not row.get('timetable_type'):
            raise ValueError("timetable_type is required")
        if not row.get('archived_by'):
            row['archived_by'] = None
        if not row.get('campus'):
            row['campus'] = None


@admin.register(CampusTimetableArchive)
class CampusTimetableArchiveAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusTimetableArchiveResource

    list_display = (
        'timetable_type_display',
        'campus_badge',
        'semester',
        'academic_year',
        'archived_by',
        'archived_at',
        'action_buttons',
    )
    list_filter = ('campus', 'timetable_type', 'semester')
    search_fields = ('academic_year', 'campus__code', 'archived_by__username')
    ordering = ('-archived_at',)
    list_per_page = 25

    fieldsets = (
        ('Archive Reference', {
            'fields': ('timetable_type', 'semester', 'academic_year', 'campus')
        }),
        ('Meta', {
            'fields': ('archived_by', 'archived_at')
        }),
        ('Data', {
            'fields': ('data',),
            'classes': ('collapse',),
        }),
    )

    def timetable_type_display(self, obj):
        color_map = {
            'MAIN': '#1976d2',
            'EXAM': '#6a1b9a',
            'LAB': '#2e7d32',
            'LAB_EXAM': '#e65100',
        }
        color = color_map.get(obj.timetable_type, '#555')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:11px;">{}</span>',
            color, obj.get_timetable_type_display()
        )
    timetable_type_display.short_description = 'Type'

    def campus_badge(self, obj):
        if obj.campus:
            return format_html(
                '<span style="background:#1976d2;color:#fff;padding:2px 8px;'
                'border-radius:4px;font-size:11px;">{}</span>',
                obj.campus.code
            )
        return '—'
    campus_badge.short_description = 'Campus'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related(
            'campus', 'archived_by'
        )


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS PUBLISHED TIMETABLE PDF
# ══════════════════════════════════════════════════════════════════════════════

class CampusPublishedTimetablePDFResource(resources.ModelResource):

    campus = fields.Field(
        column_name='campus',
        attribute='campus',
        widget=ForeignKeyWidget(Campus, 'code'),
        default=None
    )
    published_by = fields.Field(
        column_name='published_by',
        attribute='published_by',
        widget=ForeignKeyWidget(User, 'username'),
        default=None
    )

    # Read-only computed helpers
    formatted_size = fields.Field(column_name='formatted_file_size', attribute='formatted_file_size', readonly=True)

    class Meta:
        model = CampusPublishedTimetablePDF
        import_id_fields = ('timetable_type', 'version', 'campus')
        fields = (
            'timetable_type', 'version', 'is_latest',
            'campus', 'published_by', 'published_at',
            'description', 'file_size', 'download_count',
            'formatted_file_size',
        )
        export_order = (
            'campus', 'timetable_type', 'version', 'is_latest',
            'published_by', 'published_at', 'description',
            'file_size', 'formatted_file_size', 'download_count',
        )
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def dehydrate_formatted_size(self, obj):
        return obj.formatted_file_size

    def before_import_row(self, row, **kwargs):
        if not row.get('timetable_type'):
            raise ValueError("timetable_type is required")
        if not row.get('version'):
            row['version'] = 1
        if not row.get('campus'):
            row['campus'] = None
        if not row.get('published_by'):
            row['published_by'] = None
        if row.get('is_latest') is None:
            row['is_latest'] = True
        if not row.get('download_count'):
            row['download_count'] = 0


@admin.register(CampusPublishedTimetablePDF)
class CampusPublishedTimetablePDFAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusPublishedTimetablePDFResource

    list_display = (
        'timetable_type_display',
        'campus_badge',
        'version',
        'is_latest_badge',
        'published_by',
        'published_at',
        'file_size_display',
        'download_count',
        'action_buttons',
    )
    list_filter = ('campus', 'timetable_type', 'is_latest')
    search_fields = ('description', 'campus__code', 'published_by__username')
    ordering = ('-published_at',)
    list_per_page = 25

    fieldsets = (
        ('PDF Details', {
            'fields': ('timetable_type', 'version', 'is_latest', 'pdf_file', 'description')
        }),
        ('Campus & Publisher', {
            'fields': ('campus', 'published_by', 'published_at')
        }),
        ('Stats', {
            'fields': ('file_size', 'download_count'),
            'classes': ('collapse',),
        }),
    )

    def timetable_type_display(self, obj):
        color = '#1976d2' if obj.timetable_type == 'CLASS' else '#6a1b9a'
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:11px;">{}</span>',
            color, obj.get_timetable_type_display()
        )
    timetable_type_display.short_description = 'Type'

    def campus_badge(self, obj):
        if obj.campus:
            return format_html(
                '<span style="background:#1976d2;color:#fff;padding:2px 8px;'
                'border-radius:4px;font-size:11px;">{}</span>',
                obj.campus.code
            )
        return format_html('<span style="color:#aaa;">ALL</span>')
    campus_badge.short_description = 'Campus'

    def is_latest_badge(self, obj):
        if obj.is_latest:
            return format_html('<span style="color:#4CAF50;font-weight:bold;">✓ Latest</span>')
        return format_html('<span style="color:#aaa;">Archived</span>')
    is_latest_badge.short_description = 'Latest?'

    def file_size_display(self, obj):
        return obj.formatted_file_size
    file_size_display.short_description = 'File Size'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request).select_related(
            'campus', 'published_by'
        )


# ══════════════════════════════════════════════════════════════════════════════
# CAMPUS TIMETABLE TEMPLATE
# ══════════════════════════════════════════════════════════════════════════════

class CampusTimetableTemplateResource(resources.ModelResource):

    class Meta:
        model = CampusTimetableTemplate
        import_id_fields = ('template_type',)
        fields = (
            'template_type',
            'university_name', 'directorate_name',
            'address', 'telephone', 'email', 'website',
            'motto_latin', 'motto_swahili',
            'title_format', 'reference_format',
            'key_section',
            'prepared_by_label', 'director_label',
            'primary_color', 'secondary_color', 'accent_color',
            'page_header_format',
        )
        export_order = (
            'template_type',
            'university_name', 'directorate_name',
            'address', 'telephone', 'email', 'website',
            'motto_latin', 'motto_swahili',
            'primary_color', 'secondary_color', 'accent_color',
            'title_format', 'reference_format', 'page_header_format',
        )
        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    def before_import_row(self, row, **kwargs):
        if not row.get('template_type'):
            raise ValueError("template_type is required")
        row['template_type'] = row['template_type'].strip().upper()
        for color_field in ('primary_color', 'secondary_color', 'accent_color'):
            if row.get(color_field):
                val = row[color_field].strip()
                if not val.startswith('#'):
                    val = f'#{val}'
                row[color_field] = val
        if row.get('email'):
            row['email'] = row['email'].strip().lower()


@admin.register(CampusTimetableTemplate)
class CampusTimetableTemplateAdmin(ActionButtonsMixin, ImportExportModelAdmin):
    resource_class = CampusTimetableTemplateResource

    list_display = (
        'template_type_display',
        'university_name',
        'email',
        'color_swatches',
        'updated_at',
        'action_buttons',
    )
    list_filter = ('template_type',)
    search_fields = ('template_type', 'university_name')
    ordering = ('template_type',)
    list_per_page = 25

    fieldsets = (
        ('Template Type', {
            'fields': ('template_type',)
        }),
        ('University Details', {
            'fields': (
                'university_name', 'directorate_name',
                'address', 'telephone', 'email', 'website',
                'motto_latin', 'motto_swahili',
                'university_logo',
            )
        }),
        ('Title & Formats', {
            'fields': ('title_format', 'reference_format', 'page_header_format')
        }),
        ('Branding', {
            'fields': ('primary_color', 'secondary_color', 'accent_color')
        }),
        ('Signatures & Key', {
            'fields': ('key_section', 'prepared_by_label', 'director_label')
        }),
    )

    def template_type_display(self, obj):
        color_map = {
            'CLASS': '#1976d2',
            'EXAM': '#6a1b9a',
            'COURSE_ALLOCATION': '#2e7d32',
        }
        color = color_map.get(obj.template_type, '#555')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:11px;">{}</span>',
            color, obj.get_template_type_display()
        )
    template_type_display.short_description = 'Type'

    def color_swatches(self, obj):
        return format_html(
            '<span style="display:inline-block;width:14px;height:14px;'
            'background:{};border-radius:50%;margin-right:3px;"></span>'
            '<span style="display:inline-block;width:14px;height:14px;'
            'background:{};border-radius:50%;margin-right:3px;"></span>'
            '<span style="display:inline-block;width:14px;height:14px;'
            'background:{};border-radius:50%;"></span>',
            obj.primary_color, obj.secondary_color, obj.accent_color
        )
    color_swatches.short_description = 'Colors'

    def get_queryset(self, request):
        self.request = request
        return super(ActionButtonsMixin, self).get_queryset(request)

# ══════════════════════════════════════════════════════════════════════════════
# PROGRAM YEAR TRACKER  &  ALLOCATION GAP REPORT
# ══════════════════════════════════════════════════════════════════════════════

from .models import ProgramYearTracker, AllocationGapReport
from .tracker_service import send_gap_notification


class AllocationGapReportInline(admin.TabularInline):
    model = AllocationGapReport
    extra = 0
    readonly_fields = ('report_text', 'sent_to', 'sent_at')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ProgramYearTracker)
class ProgramYearTrackerAdmin(admin.ModelAdmin):
    list_display = (
        'program_display',
        'program_year_display',
        'semester_display',
        'academic_year',
        'completeness_badge',
        'missing_count',
        'carryover_count',
        'notification_sent',
        'updated_at',
        'resend_action',
    )
    list_filter = (
        'program__department',
        'program_year',
        'semester',
        'academic_year',
        'is_complete',
        'notification_sent',
    )
    search_fields = ('program__name', 'academic_year')
    readonly_fields = (
        'expected_courses', 'allocated_courses', 'missing_courses',
        'carryover_missing', 'is_complete', 'notification_sent',
        'created_at', 'updated_at',
    )
    ordering = ('program__name', 'program_year', 'semester')
    list_per_page = 30
    inlines = [AllocationGapReportInline]

    fieldsets = (
        ('Scope', {
            'fields': ('program', 'program_year', 'semester', 'academic_year')
        }),
        ('Tracking Data', {
            'fields': (
                'expected_courses', 'allocated_courses',
                'missing_courses', 'carryover_missing',
            ),
            'classes': ('collapse',),
        }),
        ('Status', {
            'fields': ('is_complete', 'notification_sent', 'created_at', 'updated_at')
        }),
    )

    def program_display(self, obj):
        return format_html('<strong>{}</strong>', obj.program.name)
    program_display.short_description = 'Program'

    def program_year_display(self, obj):
        return format_html(
            '<span style="background:#1565c0;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:11px;">Year {}</span>',
            obj.program_year
        )
    program_year_display.short_description = 'Year'

    def semester_display(self, obj):
        return format_html(
            '<span style="background:#4527a0;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:11px;">Sem {}</span>',
            obj.semester
        )
    semester_display.short_description = 'Semester'

    def completeness_badge(self, obj):
        if obj.is_complete:
            return format_html(
                '<span style="color:#2e7d32;font-weight:bold;">✅ Complete</span>'
            )
        return format_html(
            '<span style="color:#c62828;font-weight:bold;">⚠️ Gaps Found</span>'
        )
    completeness_badge.short_description = 'Status'

    def missing_count(self, obj):
        count = len(obj.missing_courses)
        if count:
            return format_html('<span style="color:#c62828;font-weight:bold;">{}</span>', count)
        return format_html('<span style="color:#2e7d32;">0</span>')
    missing_count.short_description = 'Missing'

    def carryover_count(self, obj):
        count = len(obj.carryover_missing)
        if count:
            return format_html('<span style="color:#e65100;font-weight:bold;">{}</span>', count)
        return format_html('<span style="color:#2e7d32;">0</span>')
    carryover_count.short_description = 'Carryover'

    def resend_action(self, obj):
        url = f'/campus/tracker/{obj.pk}/resend/'
        return format_html(
            '<a class="button" href="{}" style="font-size:11px;">🔔 Resend</a>',
            url
        )
    resend_action.short_description = 'Notify COD'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('program__department')

    actions = ['refresh_selected_trackers', 'resend_notifications']

    def refresh_selected_trackers(self, request, queryset):
        refreshed = 0
        for tracker in queryset:
            try:
                tracker.refresh_from_allocations()
                refreshed += 1
            except Exception:
                pass
        self.message_user(request, f'{refreshed} tracker(s) refreshed successfully.')
    refresh_selected_trackers.short_description = '🔄 Refresh selected trackers'

    def resend_notifications(self, request, queryset):
        sent = 0
        for tracker in queryset:
            try:
                send_gap_notification(tracker, force=True)
                sent += 1
            except Exception:
                pass
        self.message_user(request, f'Notifications re-sent for {sent} tracker(s).')
    resend_notifications.short_description = '🔔 Resend gap notifications to COD'


@admin.register(AllocationGapReport)
class AllocationGapReportAdmin(admin.ModelAdmin):
    list_display = (
        'tracker_display',
        'sent_to',
        'sent_at',
        'report_preview',
    )
    list_filter = ('tracker__program__department', 'tracker__program_year', 'tracker__semester')
    search_fields = ('tracker__program__name', 'sent_to__username', 'report_text')
    readonly_fields = ('tracker', 'report_text', 'sent_to', 'sent_at')
    ordering = ('-sent_at',)
    list_per_page = 30

    def has_add_permission(self, request):
        return False

    def tracker_display(self, obj):
        return format_html(
            '<strong>{}</strong> Y{}S{} <em>({})</em>',
            obj.tracker.program.name,
            obj.tracker.program_year,
            obj.tracker.semester,
            obj.tracker.academic_year,
        )
    tracker_display.short_description = 'Tracker'

    def report_preview(self, obj):
        preview = obj.report_text[:120] + '…' if len(obj.report_text) > 120 else obj.report_text
        return format_html('<pre style="font-size:11px;margin:0;">{}</pre>', preview)
    report_preview.short_description = 'Report Preview'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'tracker__program__department', 'sent_to'
        )
