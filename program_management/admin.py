# admin.py
import re
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.contrib import messages
from django.http import HttpResponseRedirect

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget

from .models import Program, ProgramCourse, ProgramCode, ImportJob
from .tasks import queue_program_course_import
from .code_utils import normalize_code
from department_management.models import Department
from backup_system.signals import suppress_audit_signals
from core.signals import suppress_activity_log

# Max size for a Program Course CSV routed through the async Celery
# importer. Anything under this could in principle still run inline, but
# CSV always goes async now (see ProgramCourseAdmin.import_action) — this
# just guards against an absurd/wrong file being uploaded by mistake.
MAX_ASYNC_IMPORT_BYTES = 50 * 1024 * 1024


class SuppressAuditDuringImportMixin:
    """
    CSV import (django-import-export) processes every row inside one atomic
    transaction. Left as-is, each row fans out into backup_system's
    AuditLog write *and* core's global ActivityLog write (which itself
    re-triggers on the AuditLog write) — 4 writes + 2 SHOW TABLES queries
    per row, all held open in a single transaction. On large files this
    balloons memory until the worker gets OOM-killed.

    Suppressing both audit signals for the duration of the import keeps
    the import itself fast and bounded; normal single-record admin edits
    are unaffected and still fully audited.
    """
    def import_action(self, request, *args, **kwargs):
        with suppress_audit_signals(), suppress_activity_log():
            return super().import_action(request, *args, **kwargs)


# =====================================================
# RESOURCES (ROBUST AGAINST MESSY / PARTIAL CSV FILES)
# =====================================================

class ProgramResource(resources.ModelResource):
    department = fields.Field(
        column_name='department',
        attribute='department',
        widget=ForeignKeyWidget(Department, 'name')
    )

    class Meta:
        model = Program
        import_id_fields = ['name']
        fields = ('name', 'department', 'description', 'default_cohort')
        export_order = fields
        skip_unchanged = True
        report_skipped = True
        # Commit each row as it's saved instead of wrapping the whole file in
        # one giant transaction. On large CSVs a single open transaction held
        # every row's DB work (+ audit-log writes) in memory/undo-log until
        # the very end, which is what was OOM-killing the worker. Trade-off:
        # if the import fails partway through, rows already processed stay
        # committed rather than rolling back — acceptable here since
        # skip_unchanged/report_skipped make re-running the same file safe.
        use_transactions = False

    def before_import_row(self, row, **kwargs):
        if row.get('name'):
            row['name'] = row['name'].strip()

        if row.get('department'):
            row['department'] = row['department'].strip()

        if row.get('description'):
            row['description'] = row['description'].strip()
        
        if row.get('default_cohort'):
            row['default_cohort'] = row['default_cohort'].strip()
        else:
            row['default_cohort'] = '0'


class ProgramCourseResource(resources.ModelResource):
    program = fields.Field(
        column_name='program',
        attribute='program',
        widget=ForeignKeyWidget(Program, 'name')
    )

    class Meta:
        model = ProgramCourse

        # student_cohort is the key identifier with course_code
        import_id_fields = ['program', 'course_code', 'student_cohort']

        fields = (
            'program',
            'course_code',
            'course_name',
            'year',
            'semester',
            'unit_type',
            'student_cohort',
        )

        export_order = fields
        skip_unchanged = True
        report_skipped = True
        # See ProgramResource.Meta above for why: avoids one huge
        # transaction (and the memory it accumulates) across the whole file.
        use_transactions = False

    def before_import_row(self, row, **kwargs):
        """
        Makes CSV imports tolerant to messy/partial data
        """

        # ---------- Clean text ----------
        if row.get('program'):
            row['program'] = row['program'].strip()

        if row.get('course_code'):
            # Shared normalizer (not just .strip().upper()) so this path
            # matches the other 3 curriculum-writing paths and 'BCOM112' /
            # 'BCOM 112' / 'bcom112' all land on the exact same DB row.
            row['course_code'] = normalize_code(row['course_code'])

        if row.get('course_name'):
            row['course_name'] = row['course_name'].strip()

        if row.get('unit_type'):
            row['unit_type'] = row['unit_type'].strip().upper()
            valid_types = ['CORE', 'ELECTIVE', 'UNIVERSITY_WIDE', 'REQUIRED_ELECTIVE']
            if row['unit_type'] not in valid_types:
                row['unit_type'] = 'CORE'
        else:
            row['unit_type'] = 'CORE'

        # ---------- Handle student_cohort ----------
        # Accepts: "2024", "2024/2025", "2024-2025", "LEGACY", "V1", "V2"
        if row.get('student_cohort'):
            row['student_cohort'] = row['student_cohort'].strip()
        else:
            row['student_cohort'] = '0'

        # ---------- Defaults ----------
        row.setdefault('semester', 1)

        # ---------- Infer year ----------
        if not row.get('year') and row.get('course_code'):
            match = re.search(r'\d{3,4}', row['course_code'])
            if match:
                inferred_year = int(match.group(0)[0])
                row['year'] = inferred_year if 1 <= inferred_year <= 6 else 1
            else:
                row['year'] = 1

        # ---------- Final safety ----------
        row['year'] = int(row.get('year', 1))
        row['semester'] = int(row.get('semester', 1))


class ProgramCodeResource(resources.ModelResource):
    program = fields.Field(
        column_name='program',
        attribute='program',
        widget=ForeignKeyWidget(Program, 'name')
    )

    class Meta:
        model = ProgramCode
        import_id_fields = ['code']
        fields = ('code', 'program')
        export_order = fields
        skip_unchanged = True
        report_skipped = True
        # See ProgramResource.Meta above for why.
        use_transactions = False

    def before_import_row(self, row, **kwargs):
        if row.get('code'):
            row['code'] = row['code'].strip().upper()

        if row.get('program'):
            row['program'] = row['program'].strip()


# =====================================================
# ADMIN CONFIGURATIONS
# =====================================================

@admin.register(Program)
class ProgramAdmin(SuppressAuditDuringImportMixin, ImportExportModelAdmin):
    resource_class = ProgramResource

    list_display = (
        'name',
        'department',
        'default_cohort_display',
        'program_codes_display',
        'description_preview',
        'edit_button',
        'delete_button',
    )

    list_filter = ('department',)
    search_fields = ('name', 'department__name')
    list_per_page = 25

    fieldsets = (
        ('Program Details', {
            'fields': ('name', 'department', 'description')
        }),
        ('Default Cohort', {
            'fields': ('default_cohort',),
            'description': 'Set the default cohort for new students (e.g., "2024", "2024/2025")'
        }),
    )

    def default_cohort_display(self, obj):
        if obj.default_cohort == '0':
            return format_html('<span style="color: #6c757d;">Legacy</span>')
        return format_html('<span style="color: green; font-weight: bold;">Cohort {}</span>', obj.default_cohort)
    default_cohort_display.short_description = 'Default Cohort'

    def program_codes_display(self, obj):
        codes = obj.program_codes.all()
        if not codes:
            return format_html('<span class="quiet">None</span>')
        return format_html(
            ', '.join(f'<code>{c.code}</code>' for c in codes)
        )
    program_codes_display.short_description = 'Program Codes'

    def description_preview(self, obj):
        if not obj.description:
            return '-'
        return obj.description[:75] + '...' if len(obj.description) > 75 else obj.description
    description_preview.short_description = 'Description'

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


@admin.register(ProgramCourse)
class ProgramCourseAdmin(SuppressAuditDuringImportMixin, ImportExportModelAdmin):
    resource_class = ProgramCourseResource
    
    actions = ['copy_curriculum_for_cohort']

    # ── Async CSV import ────────────────────────────────────────────────
    # WHY: this used to run django-import-export's import_data() directly
    # inside this request. On a large file that took longer than Nginx's
    # proxy_read_timeout / Gunicorn's --timeout, the worker got killed
    # mid-import; because ProgramCourseResource.Meta.use_transactions is
    # False (each row commits as it's saved — see that class for why),
    # whatever rows had already been processed stayed committed, silently
    # producing a partial import with no record of where it stopped.
    #
    # Now: a CSV upload is saved to storage, wrapped in an ImportJob row,
    # and handed to a Celery task (program_management.tasks) that does the
    # real work in chunks, completely outside this request. The request
    # returns immediately with "Import started successfully." — see
    # REQUIRED IMPLEMENTATION point 2. Non-CSV formats (xlsx, etc., if the
    # site format list is ever expanded) still go through the original
    # synchronous django-import-export flow via the mixin/super() call
    # below, since large-file safety is specifically a CSV/ProgramCourse
    # concern here.
    def import_action(self, request, *args, **kwargs):
        if request.method == "POST" and request.FILES.get("import_file"):
            uploaded = request.FILES["import_file"]
            ext = uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""
            if ext == "csv":
                return self._start_async_csv_import(request, uploaded)
        # Non-CSV formats, and the initial GET that renders the upload
        # form: unchanged synchronous django-import-export behaviour
        # (still wrapped in suppress_audit_signals()/suppress_activity_log()
        # by SuppressAuditDuringImportMixin, resolved via super()).
        return super().import_action(request, *args, **kwargs)

    def _start_async_csv_import(self, request, uploaded_file):
        if uploaded_file.size > MAX_ASYNC_IMPORT_BYTES:
            self.message_user(
                request,
                f"File exceeds the {MAX_ASYNC_IMPORT_BYTES // (1024 * 1024)} MB limit for Program Course import.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(request.path)

        job = ImportJob.objects.create(
            resource_type=ImportJob.RESOURCE_PROGRAM_COURSE,
            uploaded_file=uploaded_file,
            original_filename=uploaded_file.name,
            initiated_by=request.user if request.user.is_authenticated else None,
        )
        queue_program_course_import(job.id)

        self.message_user(
            request,
            format_html(
                'Import started successfully. Track progress on the '
                '<a href="{}">Import Jobs</a> page (Job #{}).',
                reverse('admin:program_management_importjob_changelist'), job.id,
            ),
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse('admin:program_management_programcourse_changelist'))

    list_display = (
        'course_code',
        'course_name',
        'program',
        'year',
        'semester',
        'unit_type_display',
        'student_cohort_display',
        'program_context',
        'edit_button',
        'delete_button',
    )

    list_filter = (
        'program',
        'year',
        'semester',
        'unit_type',
        'student_cohort',
        'program__department',
    )

    search_fields = (
        'course_code',
        'course_name',
        'program__name',
        'student_cohort',
    )

    list_per_page = 25

    fieldsets = (
        ('Course Info', {
            'fields': ('course_code', 'course_name', 'unit_type')
        }),
        ('Program Placement', {
            'fields': ('program', 'year', 'semester')
        }),
        ('Student Cohort', {
            'fields': ('student_cohort',),
            'description': '''
                <div style="background: #e7f3ff; padding: 10px; border-radius: 4px; margin-bottom: 10px;">
                    <strong>📘 Student Cohort Guide:</strong><br>
                    • <strong>"0" = Legacy</strong> - Original curriculum<br>
                    • <strong>"2024"</strong> - Students who started in 2024<br>
                    • <strong>"2024/2025"</strong> - Students who started in 2024/2025 academic year<br>
                    • <strong>"V1", "V2"</strong> - Version numbers<br>
                    <br>
                    <strong>Note:</strong> The program's default_cohort determines which curriculum new students get.
                </div>
            ''',
        }),
    )

    # Display methods
    def unit_type_display(self, obj):
        return dict(ProgramCourse.UNIT_TYPE_CHOICES).get(obj.unit_type, obj.unit_type)
    unit_type_display.short_description = 'Unit Type'
    unit_type_display.admin_order_field = 'unit_type'

    def student_cohort_display(self, obj):
        if obj.student_cohort == '0':
            return format_html('<span style="color: #6c757d;">Legacy</span>')
        return format_html('<strong style="color: #0066cc;">{}</strong>', obj.student_cohort)
    student_cohort_display.short_description = 'Student Cohort'
    student_cohort_display.admin_order_field = 'student_cohort'

    def program_context(self, obj):
        return f"{obj.program.name} – Y{obj.year} S{obj.semester}"
    program_context.short_description = 'Program Context'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'program',
            'program__department'
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

    # ==============================================
    # ADMIN ACTION: Copy Curriculum for Cohort
    # ==============================================

    def copy_curriculum_for_cohort(self, request, queryset):
        """
        Admin action to copy a curriculum for a new student cohort.
        """
        if 'apply' in request.POST:
            new_cohort = request.POST.get('new_cohort')
            if not new_cohort:
                self.message_user(request, "Please provide a new cohort name.", level=messages.ERROR)
                return HttpResponseRedirect(request.get_full_path())
            
            try:
                new_cohort = new_cohort.strip()
                program = queryset.first().program
                source_cohort = queryset.first().student_cohort
                
                # Check if this cohort already exists for this program
                existing = ProgramCourse.objects.filter(
                    program=program,
                    student_cohort=new_cohort
                )
                
                if existing.exists():
                    self.message_user(
                        request,
                        f"Cohort {new_cohort} already exists for {program.name}. "
                        f"Delete it first or choose a different cohort.",
                        level=messages.ERROR
                    )
                    return HttpResponseRedirect(request.get_full_path())
                
                # Bulk create the new courses
                new_courses = []
                for course in queryset:
                    new_courses.append(
                        ProgramCourse(
                            program=course.program,
                            course_code=course.course_code,
                            course_name=course.course_name,
                            year=course.year,
                            semester=course.semester,
                            unit_type=course.unit_type,
                            student_cohort=new_cohort,
                        )
                    )
                
                ProgramCourse.objects.bulk_create(new_courses)
                
                self.message_user(
                    request,
                    f"Successfully copied {len(new_courses)} courses from Cohort {source_cohort} "
                    f"to Cohort {new_cohort} for {program.name}.",
                    level=messages.SUCCESS
                )
                    
            except Exception as e:
                self.message_user(
                    request,
                    f"Error: {str(e)}",
                    level=messages.ERROR
                )
            
            return HttpResponseRedirect(request.get_full_path())
        
        # Show intermediate page
        context = {
            'action': 'copy_curriculum_for_cohort',
            'queryset': queryset,
            'program': queryset.first().program if queryset.exists() else None,
            'source_cohort': queryset.first().student_cohort if queryset.exists() else None,
        }
        return self.render_copy_form(request, context)

    def render_copy_form(self, request, context):
        from django.template.response import TemplateResponse
        return TemplateResponse(request, "admin/program_management/copy_curriculum_form.html", context)

    copy_curriculum_for_cohort.short_description = "Copy curriculum for new student cohort"


@admin.register(ImportJob)
class ImportJobAdmin(admin.ModelAdmin):
    """
    Read-only progress/monitoring view for background Program Course CSV
    imports (see ProgramCourseAdmin._start_async_csv_import and
    program_management.tasks.process_program_course_import). Jobs
    themselves aren't editable here — 'Resume' and 'Cancel' actions cover
    the two things an admin legitimately needs to do to a job in flight.
    """

    STATUS_COLORS = {
        ImportJob.STATUS_PENDING: '#6c757d',
        ImportJob.STATUS_RUNNING: '#0066cc',
        ImportJob.STATUS_COMPLETED: '#198754',
        ImportJob.STATUS_COMPLETED_WITH_ERRORS: '#fd7e14',
        ImportJob.STATUS_FAILED: '#dc3545',
        ImportJob.STATUS_CANCELLED: '#6c757d',
    }

    list_display = (
        'id',
        'original_filename',
        'resource_type',
        'status_badge',
        'progress_display',
        'created_count',
        'updated_count',
        'skipped_count',
        'failed_count',
        'duration_display',
        'started_at',
        'completed_at',
        'initiated_by',
    )
    list_filter = ('status', 'resource_type', 'started_at')
    search_fields = ('original_filename', 'id')
    ordering = ('-created_at',)
    actions = ['resume_selected_imports', 'cancel_selected_imports']
    list_per_page = 25

    readonly_fields = (
        'resource_type', 'uploaded_file', 'original_filename', 'status',
        'chunk_size', 'total_rows', 'processed_rows', 'created_count',
        'updated_count', 'skipped_count', 'failed_count', 'progress_percent',
        'celery_task_id', 'error_log_display', 'initiated_by',
        'created_at', 'started_at', 'completed_at',
    )
    fields = readonly_fields

    def has_add_permission(self, request):
        # Jobs are only ever created from the Program Course import form.
        return False

    def has_change_permission(self, request, obj=None):
        # View-only detail page; mutations happen only via the actions below.
        return False

    def status_badge(self, obj):
        color = self.STATUS_COLORS.get(obj.status, '#6c757d')
        return format_html('<strong style="color: {};">{}</strong>', color, obj.get_status_display())
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def progress_display(self, obj):
        return f"{obj.progress_percent}% ({obj.processed_rows}/{obj.total_rows})"
    progress_display.short_description = 'Progress'

    def duration_display(self, obj):
        seconds = obj.duration_seconds
        if seconds is None:
            return '-'
        return f"{seconds:.1f}s"
    duration_display.short_description = 'Duration'

    def error_log_display(self, obj):
        if not obj.error_log:
            return format_html('<em>(no errors)</em>')
        return format_html(
            '<pre style="max-height:400px; overflow:auto; white-space:pre-wrap;">{}</pre>', obj.error_log,
        )
    error_log_display.short_description = 'Error Log'

    def resume_selected_imports(self, request, queryset):
        resumed = 0
        for job in queryset:
            if job.is_resumable:
                job.status = ImportJob.STATUS_PENDING
                job.save(update_fields=['status'])
                queue_program_course_import(job.id)
                resumed += 1
        skipped = queryset.count() - resumed
        msg = f"Resumed {resumed} import job(s)."
        if skipped:
            msg += f" Skipped {skipped} job(s) not in a resumable (failed/cancelled + incomplete) state."
        self.message_user(request, msg, level=messages.SUCCESS if resumed else messages.WARNING)
    resume_selected_imports.short_description = "Resume selected (failed/cancelled) imports"

    def cancel_selected_imports(self, request, queryset):
        jobs = list(queryset.filter(status__in=[ImportJob.STATUS_PENDING, ImportJob.STATUS_RUNNING]))
        for job in jobs:
            if job.celery_task_id:
                try:
                    from celery.result import AsyncResult
                    AsyncResult(job.celery_task_id).revoke(terminate=False)
                except Exception:
                    pass  # best-effort; the in-task cancellation check still catches it within one chunk
        ImportJob.objects.filter(id__in=[j.id for j in jobs]).update(status=ImportJob.STATUS_CANCELLED)
        self.message_user(request, f"Marked {len(jobs)} import job(s) as cancelled.", level=messages.SUCCESS)
    cancel_selected_imports.short_description = "Cancel selected pending/running imports"


@admin.register(ProgramCode)
class ProgramCodeAdmin(SuppressAuditDuringImportMixin, ImportExportModelAdmin):
    resource_class = ProgramCodeResource

    list_display = (
        'code',
        'program',
        'department_name',
        'edit_button',
        'delete_button',
    )

    list_filter = (
        'program',
        'program__department',
    )

    search_fields = (
        'code',
        'program__name',
    )

    list_per_page = 25

    fieldsets = (
        ('Code Assignment', {
            'fields': ('code', 'program')
        }),
    )

    def department_name(self, obj):
        return obj.program.department.name if obj.program else '-'
    department_name.short_description = 'Department'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'program',
            'program__department'
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