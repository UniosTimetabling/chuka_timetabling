from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import datetime


class Campus(models.Model):
    """
    Campus model for managing university locations.
    Essential fields only.
    """
    name = models.CharField(max_length=200, unique=True)
    code = models.CharField(max_length=20, unique=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"

    def save(self, *args, **kwargs):
        if self.is_default:
            Campus.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_default_campus(cls):
        default = cls.objects.filter(is_default=True).first()
        if not default:
            default = cls.objects.filter(name__icontains='Embu').first()
        return default


class CampusCourseAllocation(models.Model):
    """
    Allocation of courses to lecturers in a department/program with campus support.
    """
    DELIVERY_MODES = [
        ('PHYSICAL', 'Physical'),
        ('ONLINE', 'Online'),
        ('BLENDED', 'Blended'),
    ]

    SEMESTER_CHOICES = [
        (1, 'Semester 1'),
        (2, 'Semester 2'),
    ]

    PROGRAM_YEAR_CHOICES = [(i, f'Year {i}') for i in range(1, 7)]

    course_code = models.CharField(max_length=20)
    course_name = models.CharField(max_length=200)

    # ── Tracker fields ──────────────────────────────────────────────────────
    # The academic year this allocation belongs to (e.g. "2024/2025")
    academic_year = models.CharField(
        max_length=20,
        blank=True,
        default='',
        help_text='Academic year, e.g. 2024/2025'
    )
    # Which semester of that academic year
    allocation_semester = models.PositiveSmallIntegerField(
        choices=SEMESTER_CHOICES,
        null=True,
        blank=True,
        help_text='Semester in which this course is allocated (1 or 2)'
    )
    # Which program year this course belongs to (Year 1 … Year 6)
    program_year = models.PositiveSmallIntegerField(
        choices=PROGRAM_YEAR_CHOICES,
        null=True,
        blank=True,
        help_text='Program year this course belongs to (1-6)'
    )
    # Flag to mark courses that are outside the standard curriculum
    is_special_course = models.BooleanField(
        default=False,
        help_text='Special/extra course not part of the standard curriculum'
    )
    # Timestamps for audit trail
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)
    # ────────────────────────────────────────────────────────────────────────

    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="campus_allocations"
    )

    origin_department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.SET_NULL,
        related_name="campus_origin_allocations",
        null=True,
        blank=True,
    )

    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.CASCADE,
        related_name="campus_allocations",
        null=True,
        blank=True
    )

    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.SET_NULL,
        related_name="campus_course_allocations",
        null=True,
        blank=True,
    )

    number_of_students = models.PositiveIntegerField(default=0)

    approved_by_dvc = models.BooleanField(default=False)
    rejected_by_dvc = models.BooleanField(default=False)
    reason_for_disapproval = models.TextField(blank=True, default="No reason yet")
    submitted_to_tt = models.BooleanField(default=False)

    # Campus fields
    # Every campus allocation must belong to a campus.
    # PROTECT prevents accidental cascade deletion of allocations.
    campus = models.ForeignKey(
        Campus,
        on_delete=models.PROTECT,
        related_name="course_allocations",
        help_text="Campus this course is allocated to. Required.",
    )
    teaching_campus = models.ForeignKey(
        Campus,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teaching_allocations",
    )
    delivery_mode = models.CharField(
        max_length=20,
        choices=DELIVERY_MODES,
        default='PHYSICAL'
    )

    # ── ProgramCourse link — REQUIRED ────────────────────────────────────────
    # Every CampusCourseAllocation must be derived from a ProgramCourse entry.
    # Deleting the ProgramCourse cascades and removes this allocation too.
    program_course = models.ForeignKey(
        "program_management.ProgramCourse",
        on_delete=models.CASCADE,
        related_name="campus_course_allocations",
        null=True,   # null=True/blank=True only during the data migration window;
        blank=True,  # remove both once backfill migration is run.
        help_text=(
            "The ProgramCourse (curriculum entry) this campus allocation is "
            "derived from. Required — deleting the ProgramCourse removes this allocation."
        ),
    )

    def status_label(self):
        if self.submitted_to_tt:
            return "📅 Submitted to Timetable"
        if self.approved_by_dvc:
            return "✅ Approved by DVC"
        if self.rejected_by_dvc:
            return f"❌ Rejected by DVC - {self.reason_for_disapproval}"
        return "⏳ Pending DVC Decision"

    def __str__(self):
        campus_info = f" [{self.campus.code}]" if self.campus else ""
        if self.lecturer:
            return f"{self.course_code}{campus_info} - {self.lecturer.display_name}"
        return f"{self.course_code}{campus_info} - Unassigned"

    def clean(self):
        from django.core.exceptions import ValidationError

        # program_course is required — fail with a clear message rather than
        # a generic DB IntegrityError once the null=True window is closed.
        if not self.program_course_id:
            raise ValidationError(
                "A ProgramCourse must be selected. "
                "Every campus allocation must link to a curriculum entry."
            )

        if self.approved_by_dvc and self.rejected_by_dvc:
            raise ValidationError("A course allocation cannot be both approved and rejected.")

        if self.rejected_by_dvc and not self.reason_for_disapproval.strip():
            raise ValidationError("Please provide a reason for disapproval.")

        if self.approved_by_dvc:
            self.reason_for_disapproval = "No reason yet"

        qs = CampusCourseAllocation.objects.filter(
            program=self.program,
            course_code__iexact=self.course_code,
            campus=self.campus
        )
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        if qs.exists():
            raise ValidationError("This course code is already allocated to that program in this campus.")

    def save(self, *args, **kwargs):
        if not self.campus_id:
            default_campus = Campus.get_default_campus()
            if default_campus:
                self.campus = default_campus
        super().save(*args, **kwargs)

    class Meta:
        permissions = [
            ("approve_course_allocation", "Can approve course allocation (DVC only)"),
            ("forward_course_allocation", "Can forward course allocation to timetable (COD only)"),
        ]
        unique_together = ['program', 'course_code', 'campus']


class ProgramYearTracker(models.Model):
    """
    Tracks course allocation progress for a specific program, program year,
    academic year and semester.  One record is created/updated each time
    allocations are saved for that combination.

    Stores a JSON snapshot of:
      - expected_courses   : list of course_codes from ProgramCourse
      - allocated_courses  : list of course_codes actually allocated
      - missing_courses    : expected – allocated (gap)
      - carryover_missing  : unresolved gaps brought forward from earlier years
    """
    SEMESTER_CHOICES = [
        (1, 'Semester 1'),
        (2, 'Semester 2'),
    ]
    PROGRAM_YEAR_CHOICES = [(i, f'Year {i}') for i in range(1, 7)]

    program = models.ForeignKey(
        'program_management.Program',
        on_delete=models.CASCADE,
        related_name='year_trackers'
    )
    program_year = models.PositiveSmallIntegerField(choices=PROGRAM_YEAR_CHOICES)
    semester = models.PositiveSmallIntegerField(choices=SEMESTER_CHOICES)
    academic_year = models.CharField(max_length=20, help_text='e.g. 2024/2025')

    # JSON fields – lists of course codes
    expected_courses = models.JSONField(default=list)
    allocated_courses = models.JSONField(default=list)
    missing_courses = models.JSONField(default=list)
    # Gaps inherited from all previous semesters not yet resolved
    carryover_missing = models.JSONField(default=list)

    # Computed summary flags
    is_complete = models.BooleanField(
        default=False,
        help_text='True when all expected courses have been allocated'
    )
    notification_sent = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('program', 'program_year', 'semester', 'academic_year')
        ordering = ['program', 'program_year', 'semester']
        verbose_name = 'Program Year Tracker'
        verbose_name_plural = 'Program Year Trackers'

    def __str__(self):
        return (
            f"{self.program.name} | Y{self.program_year}S{self.semester} "
            f"| {self.academic_year} | {'✅' if self.is_complete else '⚠️'}"
        )

    @property
    def all_missing(self):
        """Combined current + carryover gaps."""
        combined = list(set(self.missing_courses + self.carryover_missing))
        return combined

    def refresh_from_allocations(self):
        """
        Recalculate expected / allocated / missing by querying
        ProgramCourse and CampusCourseAllocation.
        Also pulls carryover from all previous semesters of this program.
        """
        from program_management.models import ProgramCourse

        # Expected courses from the curriculum
        expected_qs = ProgramCourse.objects.filter(
            program=self.program,
            year=self.program_year,
            semester=self.semester,
        ).values_list('course_code', flat=True)
        expected = [c.upper() for c in expected_qs]

        # Actually allocated standard courses for this program/year/semester
        allocated_qs = CampusCourseAllocation.objects.filter(
            program=self.program,
            program_year=self.program_year,
            allocation_semester=self.semester,
            academic_year=self.academic_year,
            is_special_course=False,
            rejected_by_dvc=False,
        ).values_list('course_code', flat=True)
        allocated = [c.upper() for c in allocated_qs]

        missing = [c for c in expected if c not in allocated]

        # Carryover: collect all unresolved gaps from previous semesters
        carryover = []
        # Determine all (year, semester) combos that come before this one
        prev_trackers = ProgramYearTracker.objects.filter(
            program=self.program,
            academic_year=self.academic_year,
        ).exclude(
            pk=self.pk
        ).filter(
            # earlier records: lower year, OR same year with lower semester
            models.Q(program_year__lt=self.program_year) |
            models.Q(program_year=self.program_year, semester__lt=self.semester)
        )
        for prev in prev_trackers:
            for code in prev.missing_courses:
                if code not in carryover and code not in allocated:
                    carryover.append(code)

        self.expected_courses = expected
        self.allocated_courses = allocated
        self.missing_courses = missing
        self.carryover_missing = carryover
        self.is_complete = (len(missing) == 0 and len(carryover) == 0)
        self.save()


class AllocationGapReport(models.Model):
    """
    Immutable snapshot of a gap-report sent as a notification to the COD.
    Stored so we can resend or review past reports.
    """
    tracker = models.ForeignKey(
        ProgramYearTracker,
        on_delete=models.CASCADE,
        related_name='gap_reports'
    )
    report_text = models.TextField()
    sent_to = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='allocation_gap_reports'
    )
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sent_at']
        verbose_name = 'Allocation Gap Report'
        verbose_name_plural = 'Allocation Gap Reports'

    def __str__(self):
        return f"Gap Report – {self.tracker} – {self.sent_at:%Y-%m-%d %H:%M}"


class CampusLabAllocation(models.Model):
    """
    Lab allocation - labs are handled separately but still need allocation
    """
    program_course = models.ForeignKey(
        "program_management.ProgramCourse", on_delete=models.CASCADE, related_name="campus_lab_allocations"
    )
    lecturer = models.ForeignKey("lecturer_portal.Lecturer", on_delete=models.SET_NULL, null=True, blank=True)
    number_of_students = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    # Campus field
    campus = models.ForeignKey(
        Campus,
        on_delete=models.CASCADE,
        related_name="lab_allocations",
        null=True,
        blank=True
    )

    def __str__(self):
        campus_info = f" [{self.campus.code}]" if self.campus else ""
        return f"{self.program_course.course_code}{campus_info} - Lab"

    def save(self, *args, **kwargs):
        if not self.campus_id:
            default_campus = Campus.get_default_campus()
            if default_campus:
                self.campus = default_campus
        super().save(*args, **kwargs)


class CampusSubmissionControl(models.Model):
    department = models.OneToOneField(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="campus_submission_control",
        null=True,
        blank=True
    )
    campus = models.ForeignKey(
        Campus,
        on_delete=models.CASCADE,
        related_name="submission_controls",
        null=True,
        blank=True
    )
    allow_submission_to_dvc = models.BooleanField(default=True)
    allow_submission_to_tt = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        dept_name = self.department.name if self.department else "GLOBAL"
        campus_name = self.campus.code if self.campus else "ALL"
        return f"{dept_name} - {campus_name}: DVC: {self.allow_submission_to_dvc}, TT: {self.allow_submission_to_tt}"

    class Meta:
        verbose_name = "Campus Submission Control"
        verbose_name_plural = "Campus Submission Controls"
        unique_together = ['department', 'campus']


class CampusArchivedCourseAllocation(models.Model):
    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="campus_archived_allocations"
    )
    semester = models.CharField(max_length=32)
    archived_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    archived_at = models.DateTimeField(default=timezone.now)

    course_code = models.CharField(max_length=20)
    course_name = models.CharField(max_length=200)

    origin_department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.SET_NULL,
        related_name="campus_archived_origin_allocations",
        null=True,
        blank=True,
    )
    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    number_of_students = models.PositiveIntegerField(default=0)

    approved_by_dvc = models.BooleanField(default=False)
    rejected_by_dvc = models.BooleanField(default=False)
    reason_for_disapproval = models.TextField(blank=True, default="No reason yet")
    submitted_to_tt = models.BooleanField(default=False)

    # Campus field
    campus = models.ForeignKey(
        Campus,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="archived_allocations"
    )

    class Meta:
        verbose_name = "Campus Archived Course Allocation"
        verbose_name_plural = "Campus Archived Course Allocations"
        ordering = ["-archived_at"]

    def __str__(self):
        campus_info = f" [{self.campus.code}]" if self.campus else ""
        return f"[{self.semester}]{campus_info} {self.course_code} - {self.department.name}"


class CampusSchedulerConfig(models.Model):
    campus = models.OneToOneField(
        Campus,
        on_delete=models.CASCADE,
        related_name="scheduler_config",
        null=True,
        blank=True
    )
    start_time = models.TimeField(default=datetime.time(7, 0))
    end_time = models.TimeField(default=datetime.time(19, 0))
    slot_size = models.PositiveIntegerField(default=3)

    def __str__(self):
        campus_name = self.campus.code if self.campus else "DEFAULT"
        return f"{campus_name} Config ({self.start_time}-{self.end_time}, {self.slot_size} hrs)"
    
    class Meta:
        verbose_name = "Campus Scheduler Configuration"
        verbose_name_plural = "Campus Scheduler Configurations"


class CampusExamSchedulerConfig(models.Model):
    campus = models.OneToOneField(
        Campus,
        on_delete=models.CASCADE,
        related_name="exam_scheduler_config",
        null=True,
        blank=True
    )
    start_date = models.DateField(default=timezone.now)
    start_time = models.TimeField(default=datetime.time(8, 0))
    end_time = models.TimeField(default=datetime.time(17, 0))
    slot_size = models.PositiveIntegerField(default=2)
    excluded_days = models.TextField(blank=True, default="")
    max_exam_days = models.PositiveIntegerField(default=14)
    spacing_ratio = models.FloatField(default=0.7)

    def get_date_range(self):
        return [
            (
                (self.start_date + datetime.timedelta(days=i)).strftime("%Y-%m-%d"),
                (self.start_date + datetime.timedelta(days=i)).strftime("%A")
            )
            for i in range(self.max_exam_days)
        ]
    
    def get_excluded_date_range(self):
        excluded = set(self.excluded_date_list())
        date_range = []
        for i in range(self.max_exam_days):
            current_date = self.start_date + datetime.timedelta(days=i)
            date_str = current_date.strftime("%Y-%m-%d")
            if date_str not in excluded:
                date_range.append((date_str, current_date.strftime("%A")))
        return date_range

    def excluded_date_list(self):
        if not self.excluded_days:
            return []
        return [d.strip() for d in self.excluded_days.split(",") if d.strip()]

    def __str__(self):
        campus_name = self.campus.code if self.campus else "DEFAULT"
        return f"{campus_name} Exam Config starting {self.start_date}"


class CampusTimetable(models.Model):
    """
    Timetable entry - no venue information as campuses manage their own venues
    """
    course_allocation = models.ForeignKey(
        CampusCourseAllocation, on_delete=models.CASCADE, related_name="timetable_entries"
    )
    day = models.CharField(max_length=20)
    start_time = models.TimeField()
    end_time = models.TimeField()

    # Campus field
    campus = models.ForeignKey(
        Campus,
        on_delete=models.CASCADE,
        related_name="timetables",
    )

    def __str__(self):
        campus_info = f" [{self.campus.code}]" if self.campus else ""
        return f"{self.course_allocation.course_code}{campus_info} - {self.day} {self.start_time}-{self.end_time}"

    def save(self, *args, **kwargs):
        if not self.campus_id and self.course_allocation.campus_id:
            self.campus = self.course_allocation.campus
        super().save(*args, **kwargs)

    class Meta:
        permissions = [
            ("approve_timetable", "Can approve timetable"),
        ]
        unique_together = ('course_allocation', 'day', 'start_time', 'end_time')


class CampusTempTimetable(models.Model):
    """
    Temporary timetable entry - no venue information
    """
    course_allocation = models.ForeignKey(
        CampusCourseAllocation, on_delete=models.CASCADE, related_name="temp_timetable_entries"
    )
    day = models.CharField(max_length=20)
    start_time = models.TimeField()
    end_time = models.TimeField()

    # Campus field
    campus = models.ForeignKey(
        Campus,
        on_delete=models.CASCADE,
        related_name="temp_timetables",
    )

    def __str__(self):
        campus_info = f" [{self.campus.code}]" if self.campus else ""
        return f"[TEMP]{campus_info} {self.course_allocation.course_code} - {self.day} {self.start_time}-{self.end_time}"

    def save(self, *args, **kwargs):
        if not self.campus_id and self.course_allocation.campus_id:
            self.campus = self.course_allocation.campus
        super().save(*args, **kwargs)

    class Meta:
        unique_together = ('course_allocation', 'day', 'start_time', 'end_time')


class CampusExamTimetable(models.Model):
    """
    Exam timetable entry - no venue information
    """
    course_allocation = models.ForeignKey(
        CampusCourseAllocation,
        on_delete=models.CASCADE,
        related_name="exam_timetable_entries"
    )
    day = models.CharField(max_length=20)
    date = models.DateField(default=timezone.now)
    start_time = models.TimeField()
    end_time = models.TimeField()

    # Campus field
    campus = models.ForeignKey(
        Campus,
        on_delete=models.CASCADE,
        related_name="exam_timetables",
    )

    def __str__(self):
        campus_info = f" [{self.campus.code}]" if self.campus else ""
        return f"{self.course_allocation.course_code}{campus_info} - {self.day} {self.date} {self.start_time}-{self.end_time}"

    def save(self, *args, **kwargs):
        if not self.campus_id and self.course_allocation.campus_id:
            self.campus = self.course_allocation.campus
        super().save(*args, **kwargs)

    class Meta:
        permissions = [
            ("approve_timetable", "Can approve timetable"),
        ]
        ordering = ["date", "start_time"]
        unique_together = ('course_allocation', 'date', 'start_time', 'end_time')


class CampusExamTempTimetable(models.Model):
    """
    Temporary exam timetable entry - no venue information
    """
    course_allocation = models.ForeignKey(
        CampusCourseAllocation,
        on_delete=models.CASCADE,
        related_name="exam_temp_timetable_entries"
    )
    day = models.CharField(max_length=20)
    date = models.DateField(default=timezone.now)
    start_time = models.TimeField()
    end_time = models.TimeField()

    # Campus field
    campus = models.ForeignKey(
        Campus,
        on_delete=models.CASCADE,
        related_name="exam_temp_timetables",
    )

    def __str__(self):
        campus_info = f" [{self.campus.code}]" if self.campus else ""
        return f"[TEMP]{campus_info} {self.course_allocation.course_code} - {self.day} {self.date} {self.start_time}-{self.end_time}"

    def save(self, *args, **kwargs):
        if not self.campus_id and self.course_allocation.campus_id:
            self.campus = self.course_allocation.campus
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["date", "start_time"]
        unique_together = ('course_allocation', 'date', 'start_time', 'end_time')


class CampusLabTimetable(models.Model):
    """
    Lab timetable entry - no lab venue information as campuses manage their own labs
    """
    lab_allocation = models.ForeignKey(
        CampusLabAllocation, on_delete=models.CASCADE, related_name="lab_timetables"
    )
    day = models.CharField(max_length=16)
    start_time = models.TimeField()
    end_time = models.TimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Campus field
    campus = models.ForeignKey(
        Campus,
        on_delete=models.CASCADE,
        related_name="lab_timetables",
    )

    class Meta:
        unique_together = ('lab_allocation', 'day', 'start_time', 'end_time')

    def __str__(self):
        campus_info = f" [{self.campus.code}]" if self.campus else ""
        return f"{self.lab_allocation.program_course.course_code}{campus_info} - Lab {self.day} {self.start_time}-{self.end_time}"

    def save(self, *args, **kwargs):
        if not self.campus_id and self.lab_allocation.campus_id:
            self.campus = self.lab_allocation.campus
        super().save(*args, **kwargs)


class CampusLabExamTimetable(models.Model):
    """
    Lab exam timetable entry - no lab venue information
    """
    lab_allocation = models.ForeignKey(
        CampusLabAllocation, on_delete=models.CASCADE, related_name="lab_exam_timetables"
    )
    date = models.DateField(default=timezone.now)
    day = models.CharField(max_length=16)
    start_time = models.TimeField()
    end_time = models.TimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Campus field
    campus = models.ForeignKey(
        Campus,
        on_delete=models.CASCADE,
        related_name="lab_exam_timetables",
    )

    class Meta:
        unique_together = ('lab_allocation', 'date', 'start_time', 'end_time')
        ordering = ["date", "start_time"]

    def __str__(self):
        campus_info = f" [{self.campus.code}]" if self.campus else ""
        return f"{self.lab_allocation.program_course.course_code}{campus_info} - Lab Exam {self.day} {self.date} {self.start_time}-{self.end_time}"

    def save(self, *args, **kwargs):
        if not self.campus_id and self.lab_allocation.campus_id:
            self.campus = self.lab_allocation.campus
        super().save(*args, **kwargs)


class CampusTimetableArchive(models.Model):
    TIMETABLE_TYPES = [
        ("MAIN", "Main Timetable"),
        ("EXAM", "Exam Timetable"),
        ("LAB", "Lab Timetable"),
        ("LAB_EXAM", "Lab Exam Timetable"),
    ]

    SEMESTER_CHOICES = [
        ("1", "1st Semester"),
        ("2", "2nd Semester"),
    ]

    timetable_type = models.CharField(max_length=20, choices=TIMETABLE_TYPES)
    semester = models.CharField(max_length=2, choices=SEMESTER_CHOICES)
    academic_year = models.CharField(max_length=15)
    archived_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    archived_at = models.DateTimeField(default=timezone.now)
    data = models.JSONField()

    # Campus field
    campus = models.ForeignKey(
        Campus,
        on_delete=models.CASCADE,
        related_name="timetable_archives",
    )

    class Meta:
        unique_together = ("semester", "academic_year", "timetable_type", "campus")
        ordering = ["-archived_at"]

    def __str__(self):
        campus_info = f" [{self.campus.code}]" if self.campus else ""
        return f"{self.get_timetable_type_display()}{campus_info} - {self.academic_year} S{self.semester}"

    @classmethod
    def clean_old_archives(cls):
        for campus in Campus.objects.filter(is_active=True):
            archives = cls.objects.filter(campus=campus).order_by("-archived_at")
            if archives.count() > 4:
                for old in archives[4:]:
                    old.delete()

class CampusPublishedTimetablePDF(models.Model):
    """
    Stores published timetable PDFs with version control
    """
    TIMETABLE_TYPES = [
        ('CLASS', 'Class Timetable'),
        ('EXAM', 'Exam Timetable'),
    ]
    
    timetable_type = models.CharField(max_length=10, choices=TIMETABLE_TYPES)
    version = models.PositiveIntegerField(default=1)
    is_latest = models.BooleanField(default=True)
    published_at = models.DateTimeField(default=timezone.now)
    published_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='campus_published_timetables'
    )
    
    # PDF file
    pdf_file = models.FileField(upload_to='campus_timetables/published/')
    
    # Campus association (optional - if None, it's for all campuses)
    campus = models.ForeignKey(
        Campus,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='published_timetables'
    )
    
    # Additional metadata
    description = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(default=0, help_text="File size in bytes")
    download_count = models.PositiveIntegerField(default=0)
    
    class Meta:
        ordering = ['-published_at']
        unique_together = ['timetable_type', 'version', 'campus']
        verbose_name = "Published Campus Timetable"
        verbose_name_plural = "Published Campus Timetables"
    
    def __str__(self):
        campus_info = f" [{self.campus.code}]" if self.campus else ""
        return f"{self.get_timetable_type_display()}{campus_info} v{self.version}"
    
    def save(self, *args, **kwargs):
        # Update file size
        if self.pdf_file and hasattr(self.pdf_file, 'size'):
            self.file_size = self.pdf_file.size
        
        # Set previous versions as not latest
        if self.is_latest:
            CampusPublishedTimetablePDF.objects.filter(
                timetable_type=self.timetable_type,
                campus=self.campus,
                is_latest=True
            ).exclude(pk=self.pk).update(is_latest=False)
        
        super().save(*args, **kwargs)
    
    def increment_download_count(self):
        self.download_count += 1
        self.save(update_fields=['download_count'])
    
    @property
    def formatted_file_size(self):
        """Return human-readable file size"""
        if self.file_size < 1024:
            return f"{self.file_size} B"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        else:
            return f"{self.file_size / (1024 * 1024):.1f} MB"


class CampusTimetableTemplate(models.Model):
    """
    Configuration template for PDF generation
    """
    TEMPLATE_TYPES = [
        ('CLASS', 'Class Timetable'),
        ('EXAM', 'Exam Timetable'),
        ('COURSE_ALLOCATION', 'Course Allocation'),
    ]
    
    template_type = models.CharField(max_length=20, choices=TEMPLATE_TYPES, unique=True)
    
    # University details
    university_name = models.CharField(max_length=200, default="CHUKA UNIVERSITY")
    directorate_name = models.CharField(max_length=200, default="DIRECTORATE OF CAMPUS TIMETABLE")
    address = models.CharField(max_length=200, default="P.O. Box 109-60400, Chuka")
    telephone = models.CharField(max_length=50, default="020-231 0512/0721-712 609")
    email = models.EmailField(default="info@chuka.ac.ke")
    website = models.URLField(default="www.chuka.ac.ke")
    
    # Mottos
    motto_latin = models.CharField(max_length=200, default="Scientia ad Excelsitudinem")
    motto_swahili = models.CharField(max_length=200, default="Elimu ya Malidadi")
    
    # Logo
    university_logo = models.ImageField(
        upload_to='timetable_templates/logos/',
        blank=True,
        null=True
    )
    
    # Title formats
    title_format = models.CharField(
        max_length=200,
        default="{timetable_type} TIMETABLE",
        help_text="Use {timetable_type} placeholder"
    )
    
    # Reference format
    reference_format = models.CharField(
        max_length=100,
        default="CU/DIR/TT/{date}/REF",
        help_text="Reference number format"
    )
    
    # Key/Legend section
    key_section = models.TextField(
        default="KEY:",
        help_text="Key/Legend text to appear at the end"
    )
    
    # Signatures
    prepared_by_label = models.CharField(max_length=100, default="Prepared by:")
    director_label = models.CharField(max_length=100, default="DIRECTOR, CAMPUS TIMETABLE")
    
    # Colors
    primary_color = models.CharField(max_length=20, default="#2e7d32", help_text="Primary color (hex)")
    secondary_color = models.CharField(max_length=20, default="#43a047", help_text="Secondary color (hex)")
    accent_color = models.CharField(max_length=20, default="#4caf50", help_text="Accent color (hex)")
    
    # Page settings
    page_header_format = models.CharField(
        max_length=100,
        default="Page {current_page} of {total_pages}",
        help_text="Use {current_page} and {total_pages} placeholders"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Timetable Template"
        verbose_name_plural = "Timetable Templates"
    
    def __str__(self):
        return f"{self.get_template_type_display()} Template"
    
    @classmethod
    def get_template(cls, template_type='CLASS'):
        """Get or create template for type"""
        template, created = cls.objects.get_or_create(template_type=template_type)
        return template
    
    def get_reference_number(self, date_str):
        """Generate reference number"""
        return self.reference_format.replace('{date}', date_str)
    
    def get_page_header(self, current_page, total_pages):
        """Generate page header"""
        return self.page_header_format.replace('{current_page}', str(current_page)).replace('{total_pages}', str(total_pages))