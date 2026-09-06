# models.py
import re
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Program(models.Model):
    name = models.CharField(max_length=200, unique=True)
    department = models.ForeignKey("department_management.Department", on_delete=models.CASCADE, related_name="programs")
    description = models.TextField(blank=True, null=True)
    
    # Default cohort for new students
    default_cohort = models.CharField(
        max_length=20,
        default='0',
        help_text="Default cohort for new students (e.g., '2024', '2024/2025', 'LEGACY')"
    )
    
    def __str__(self):
        return f"{self.name} ({self.department.name})"
    
    def get_default_curriculum(self):
        """Get the default curriculum for this program"""
        return self.courses.filter(student_cohort=self.default_cohort)
    
    def get_curriculum_for_cohort(self, cohort):
        """Get curriculum for a specific student cohort"""
        courses = self.courses.filter(student_cohort=cohort)
        if courses.exists():
            return courses
        return self.get_default_curriculum()


class ProgramCourse(models.Model):
    SEMESTER_CHOICES = [(1, "Semester 1"), (2, "Semester 2")]
    YEAR_CHOICES = [(i, f"Year {i}") for i in range(1, 7)]
    
    UNIT_TYPE_CHOICES = [
        ('CORE', 'Core'),
        ('ELECTIVE', 'Elective'),
        ('UNIVERSITY_WIDE', 'University Wide'),
        ('REQUIRED_ELECTIVE', 'Required Elective'),
    ]
    
    program = models.ForeignKey('Program', on_delete=models.CASCADE, related_name="courses")
    course_code = models.CharField(max_length=70)
    course_name = models.CharField(max_length=200)
    year = models.PositiveSmallIntegerField(choices=YEAR_CHOICES, default=1)
    semester = models.PositiveSmallIntegerField(choices=SEMESTER_CHOICES, default=1)
    
    unit_type = models.CharField(
        max_length=20,
        choices=UNIT_TYPE_CHOICES,
        default='CORE',
        help_text="Type of course unit: Core, Elective, University Wide, or Required Elective"
    )
    
    # Student cohort based versioning
    student_cohort = models.CharField(
        max_length=20,
        default='0',
        db_index=True,
        help_text="Student cohort this curriculum applies to. '0' = legacy, '2024', '2024/2025', etc."
    )
    
    # Timestamp fields - using null=True to handle existing data safely
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)
    
    class Meta:
        unique_together = ("program", "course_code", "student_cohort")
        indexes = [
            models.Index(fields=['program', 'student_cohort']),
            models.Index(fields=['program', 'student_cohort', 'semester']),
            models.Index(fields=['course_code']),
            models.Index(fields=['program', 'semester']),
        ]
        ordering = ['program', '-student_cohort', 'year', 'semester', 'course_code']

    def __str__(self):
        cohort_label = f"Cohort {self.student_cohort}" if self.student_cohort != '0' else "Legacy"
        return f"{self.course_code} - {self.course_name} ({self.program.name}, Y{self.year}S{self.semester}, {cohort_label})"

    # Unit types that count as "elective" for the purposes of auto-flagging
    # a CourseAllocation. REQUIRED_ELECTIVE is included because it's still
    # an elective the student chooses from a required list, not a fixed CORE
    # unit — only plain CORE / UNIVERSITY_WIDE units are non-elective.
    ELECTIVE_UNIT_TYPES = {'ELECTIVE', 'REQUIRED_ELECTIVE'}

    @classmethod
    def normalize_unit_type(cls, raw_value):
        """
        Resolve a (possibly mistyped/mis-cased/whitespace-padded) unit_type
        string to the closest valid UNIT_TYPE_CHOICES key.

        Handles: exact match (any case/whitespace), then a fuzzy match via
        difflib for typos like 'Electve', 'ELCTIVE', 'requird_elective'.
        Falls back to 'CORE' only if nothing is remotely close, so a typo
        never silently loses the "this is an elective" intent.
        """
        valid_codes = [c[0] for c in cls.UNIT_TYPE_CHOICES]
        if not raw_value:
            return 'CORE'

        cleaned = str(raw_value).strip().upper().replace(' ', '_').replace('-', '_')
        if cleaned in valid_codes:
            return cleaned

        # Also compare against the human-readable labels ("Elective", "Required Elective", ...)
        label_map = {label.strip().upper().replace(' ', '_'): code for code, label in cls.UNIT_TYPE_CHOICES}
        if cleaned in label_map:
            return label_map[cleaned]

        import difflib
        candidates = valid_codes + list(label_map.keys())
        close = difflib.get_close_matches(cleaned, candidates, n=1, cutoff=0.6)
        if close:
            match = close[0]
            return label_map.get(match, match)

        return 'CORE'

    @property
    def is_elective_type(self):
        """True if this curriculum entry's unit type is Elective or Required Elective."""
        return self.normalize_unit_type(self.unit_type) in self.ELECTIVE_UNIT_TYPES

    def save(self, *args, **kwargs):
        # DB-level safety net: whatever path created/edited this row
        # (COD panel, AJAX CRUD, CSV/Celery import, admin import-export,
        # spreadsheet import, a future script, the Django shell...),
        # course_code always ends up in the one canonical display form.
        # This is on top of -- not instead of -- normalizing at each
        # call site, since normalizing only here would still let two
        # bulk_create()/bulk_update() calls (which bypass save()) create
        # divergent rows.
        if self.course_code:
            from program_management.code_utils import normalize_code
            self.course_code = normalize_code(self.course_code)

        if not self.year:
            self.year = 1
        if not self.semester:
            self.semester = 1

        # Typo-tolerant unit_type normalization. A CharField `choices`
        # constraint on its own is NOT enforced at the DB/save level in
        # Django — a caller that bypasses a <select> (raw POST, CSV/Excel
        # import, admin script, API) can persist any string. Rather than
        # let a typo like "Electve" / "elective " / "ELCTIVE" quietly
        # slip through as an unrecognised value (or get silently reset to
        # CORE by a stricter caller upstream), snap it to the closest
        # known choice so the elective flag it drives downstream — the
        # CourseAllocation "Is Elective" autofill — still works correctly.
        self.unit_type = self.normalize_unit_type(self.unit_type)

        if not self.pk or not self.year:
            match = re.search(r'\d{3,4}', self.course_code or '', re.IGNORECASE)
            if match:
                digits = match.group(0)
                if digits:
                    inferred_year = int(digits[0])
                    if 1 <= inferred_year <= 6:
                        self.year = inferred_year

        super().save(*args, **kwargs)


class ImportJob(models.Model):
    """
    Tracks a background (Celery) bulk import of a data file into the
    system. Introduced so large Program Course CSV imports run outside
    the HTTP request/response cycle — see program_management/tasks.py
    and program_management/import_helpers.py for the worker side, and
    program_management/admin.py (ProgramCourseAdmin.import_action) for
    how a job gets created from the admin upload form.

    Historically ProgramCourse CSV imports ran synchronously inside the
    admin request via django-import-export's import_data(), which meant
    a large file could run past the Gunicorn/Nginx timeout and leave a
    partial import committed (use_transactions=False commits row by
    row). ImportJob lets the admin hand the file to a Celery worker and
    poll/watch progress instead of holding the request open.
    """

    STATUS_PENDING = "PENDING"
    STATUS_RUNNING = "RUNNING"
    STATUS_COMPLETED = "COMPLETED"
    STATUS_COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    STATUS_FAILED = "FAILED"
    STATUS_CANCELLED = "CANCELLED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_COMPLETED_WITH_ERRORS, "Completed with errors"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    # Only one resource type exists today (Program Course), but this is
    # kept as a choice field rather than assumed, so ImportJob can be
    # reused for Program / ProgramCode imports later without a new model.
    RESOURCE_PROGRAM_COURSE = "program_course"
    RESOURCE_CHOICES = [
        (RESOURCE_PROGRAM_COURSE, "Program Course"),
    ]

    resource_type = models.CharField(
        max_length=30, choices=RESOURCE_CHOICES, default=RESOURCE_PROGRAM_COURSE, db_index=True,
    )

    uploaded_file = models.FileField(
        upload_to="program_course_imports/%Y/%m/",
        help_text="The CSV file as originally uploaded. Kept so a failed/cancelled job can be resumed or re-run.",
    )
    original_filename = models.CharField(max_length=255, blank=True, default="")

    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)

    # Configurable per-job, defaults to import_helpers.DEFAULT_CHUNK_SIZE.
    chunk_size = models.PositiveIntegerField(default=500)

    total_rows = models.PositiveIntegerField(default=0)
    processed_rows = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    progress_percent = models.PositiveSmallIntegerField(default=0)

    celery_task_id = models.CharField(max_length=155, blank=True, null=True)

    # Newline-separated human-readable error/skip messages, capped in the
    # task to avoid unbounded growth on a file with many bad rows.
    error_log = models.TextField(blank=True, default="")

    initiated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="program_course_import_jobs",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "resource_type"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        label = self.original_filename or (self.uploaded_file.name if self.uploaded_file else f"job #{self.pk}")
        return f"Import #{self.pk} [{self.get_status_display()}] — {label}"

    @property
    def duration_seconds(self):
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        if self.started_at:
            return (timezone.now() - self.started_at).total_seconds()
        return None

    @property
    def is_resumable(self):
        """A job that stopped early (failed, or cancelled/killed mid-run) and
        hasn't processed every row yet can be safely resumed — the import
        logic re-checks each row's (program, course_code, student_cohort)
        key against the DB before writing, so replaying already-processed
        rows never creates duplicates."""
        return self.status in (self.STATUS_FAILED, self.STATUS_CANCELLED) and self.processed_rows < self.total_rows

    def append_error(self, message, max_lines=2000):
        """Append a line to error_log, capping total stored lines so a file
        with thousands of bad rows doesn't blow up the DB text field."""
        lines = self.error_log.splitlines() if self.error_log else []
        if len(lines) >= max_lines:
            return
        lines.append(message)
        self.error_log = "\n".join(lines[:max_lines])


class ProgramCode(models.Model):
    program = models.ForeignKey(
        'Program',
        on_delete=models.CASCADE,
        related_name='program_codes'
    )
    code = models.CharField(max_length=20, unique=True)

    def __str__(self):
        return f"{self.code} - {self.program.name}"