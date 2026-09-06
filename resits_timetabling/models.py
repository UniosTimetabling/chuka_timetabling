"""
resits_timetabling/models.py
Models for resit exam timetabling with support for multiple courses per cell.
"""
import datetime

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


# Callable defaults for TimeField — avoids Django W161 warning that fires when
# a fixed datetime/time value is passed directly as `default=`.  Django only
# suppresses the warning when `default` is a callable (i.e. a function or
# lambda), not a bare value.
def _default_start_time():
    return datetime.time(8, 0)

def _default_end_time():
    return datetime.time(17, 0)


class ResitSchedulerConfig(models.Model):
    """
    Configuration for the resit scheduling engine.
    Controls the resit exam period dates, slot sizes, venue spacing, etc.
    """
    start_date = models.DateField(
        help_text="Start date of the resit exam period",
        null=True, blank=True,
    )
    end_date = models.DateField(
        help_text="End date of the resit exam period",
        null=True, blank=True,
    )
    start_time = models.TimeField(
        default=_default_start_time,
        help_text="Earliest resit exam start time",
    )
    end_time = models.TimeField(
        default=_default_end_time,
        help_text="Latest resit exam end time",
    )
    slot_size = models.PositiveIntegerField(
        default=2,
        help_text="Duration of one resit exam session (in hours)",
    )
    excluded_days = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Comma-separated dates (YYYY-MM-DD) to skip. "
            "Weekends are auto-added on save; add public holidays here."
        ),
    )
    max_exam_days = models.PositiveIntegerField(
        default=10,
        help_text="Number of valid resit exam days required (weekends excluded).",
    )
    spacing_ratio = models.FloatField(
        default=0.7,
        help_text="Allowed fill ratio for venue capacity (e.g. 0.7 = 70%).",
    )
    academic_year = models.CharField(
        max_length=20,
        default="",
        blank=True,
        help_text="e.g. 2025/2026",
    )
    semester = models.CharField(
        max_length=10,
        default="",
        blank=True,
        help_text="e.g. S1, S2",
    )

    def excluded_date_list(self) -> list[str]:
        if not self.excluded_days:
            return []
        return [d.strip() for d in self.excluded_days.split(",") if d.strip()]

    def _lookahead_days(self) -> int:
        return int(self.max_exam_days * (7 / 5)) + 60

    def get_date_range(self) -> list[tuple[str, str]]:
        """Return a list of (YYYY-MM-DD, weekday-name) tuples for valid exam days."""
        if not self.start_date:
            return []
        
        # Ensure start_date is a date object
        start_date = self.start_date
        if isinstance(start_date, str):
            try:
                start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                return []
        
        excluded: set[str] = set(self.excluded_date_list())
        valid_dates: list[tuple[str, str]] = []
        i = 0
        safety_cap = self._lookahead_days()
        
        while len(valid_dates) < self.max_exam_days and i <= safety_cap:
            try:
                current: datetime.date = start_date + datetime.timedelta(days=i)
                date_str: str = current.strftime("%Y-%m-%d")
                # Skip weekends (Saturday=5, Sunday=6) AND explicitly excluded dates.
                if current.weekday() < 5 and date_str not in excluded:
                    valid_dates.append((date_str, current.strftime("%A")))
                i += 1
            except Exception:
                i += 1
                continue
        
        return valid_dates

    def _generate_slots(self):
        """
        Delete all existing ResitTimeSlot rows for this config, then
        regenerate them from the current start_date, end_date, start_time,
        end_time, slot_size, spacing_ratio, and excluded_days.

        Called automatically at the end of save() whenever start_date is set.

        slot layout per day (example: slot_size=2h, break=1h, 08:00–17:00):
            08:00–10:00  →  break  →  11:00–13:00  →  break  →  14:00–16:00
        """
        # Avoid circular import — ResitTimeSlot is defined just below.
        ResitTimeSlot.objects.filter(config=self).delete()

        date_range = self.get_date_range()
        if not date_range:
            return

        st        = self.start_time if self.start_time else datetime.time(8, 0)
        et        = self.end_time   if self.end_time   else datetime.time(17, 0)
        slot_size = self.slot_size or 2

        # Work entirely in minutes so that a start time of 08:30 is preserved
        # exactly — previous code used only .hour and silently dropped .minute.
        start_mins = st.hour * 60 + st.minute   # e.g. 08:30 → 510
        end_mins   = et.hour * 60 + et.minute   # e.g. 17:00 → 1020
        slot_mins  = slot_size * 60

        # spacing_ratio is repurposed: values >= 1 = break hours between slots;
        # legacy values < 1 (old fill-ratio) = no break.
        raw_break  = self.spacing_ratio if self.spacing_ratio else 0
        break_mins = int(raw_break) * 60 if raw_break >= 1 else 0

        bulk = []
        for date_str, day_name in date_range:
            exam_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            current = start_mins
            while current + slot_mins <= end_mins:
                s_h, s_m = divmod(current, 60)
                e_h, e_m = divmod(current + slot_mins, 60)
                bulk.append(ResitTimeSlot(
                    config     = self,
                    date       = exam_date,
                    day        = day_name,
                    start_time = datetime.time(s_h, s_m),
                    end_time   = datetime.time(e_h, e_m),
                ))
                current += slot_mins + break_mins

        ResitTimeSlot.objects.bulk_create(bulk)

    def save(self, *args, **kwargs):
        # Ensure start_date is a date object, not a string
        if self.start_date and isinstance(self.start_date, str):
            try:
                self.start_date = datetime.datetime.strptime(self.start_date, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass

        # Ensure start_time / end_time are time objects, not strings (e.g. "08:00")
        if self.start_time and isinstance(self.start_time, str):
            try:
                self.start_time = datetime.datetime.strptime(self.start_time[:5], "%H:%M").time()
            except (ValueError, TypeError):
                self.start_time = datetime.time(8, 0)
        if self.end_time and isinstance(self.end_time, str):
            try:
                self.end_time = datetime.datetime.strptime(self.end_time[:5], "%H:%M").time()
            except (ValueError, TypeError):
                self.end_time = datetime.time(17, 0)

        if not self.start_date:
            super().save(*args, **kwargs)
            return

        existing_excluded: set[str] = set(self.excluded_date_list())

        # Auto-add weekends to excluded_days
        for i in range(self._lookahead_days()):
            try:
                candidate: datetime.date = self.start_date + datetime.timedelta(days=i)
                if candidate.weekday() >= 5:
                    existing_excluded.add(candidate.strftime("%Y-%m-%d"))
            except Exception:
                pass

        self.excluded_days = ", ".join(sorted(existing_excluded))

        # Always recalculate end_date from the current config so it never goes stale.
        date_range = self.get_date_range()
        if date_range:
            last_date = date_range[-1][0]
            try:
                self.end_date = datetime.datetime.strptime(last_date, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass
        else:
            self.end_date = None

        super().save(*args, **kwargs)

        # Regenerate the stored time slots now that the config row is committed.
        self._generate_slots()

    def __str__(self) -> str:
        return f"Resit Config — {self.academic_year} {self.semester} starting {self.start_date}"

    class Meta:
        verbose_name = "Resit Scheduler Config"
        verbose_name_plural = "Resit Scheduler Configs"


class ResitTimeSlot(models.Model):
    """
    Pre-generated, DB-stored time slots for a ResitSchedulerConfig.

    Regenerated automatically whenever ResitSchedulerConfig.save() is called.
    Consumers (auto-scheduler, manual timetabler) read from this table instead
    of recomputing slots inline — guaranteeing that the break_between_slots
    setting is always applied consistently and only in one place.
    """
    config = models.ForeignKey(
        "ResitSchedulerConfig",
        on_delete=models.CASCADE,
        related_name="time_slots",
    )
    date       = models.DateField()
    day        = models.CharField(max_length=20)
    start_time = models.TimeField()
    end_time   = models.TimeField()

    class Meta:
        ordering = ["date", "start_time"]
        verbose_name = "Resit Time Slot"
        verbose_name_plural = "Resit Time Slots"
        unique_together = [["config", "date", "start_time"]]

    def __str__(self) -> str:
        return f"{self.date} {self.start_time}–{self.end_time}"


class StudentResitRegistration(models.Model):
    """
    Tracks which students are registered for which resit courses.
    Normalizes registration numbers for consistent matching.
    """
    student_reg_no = models.CharField(max_length=50, db_index=True)
    student_reg_no_normalized = models.CharField(
        max_length=50, 
        db_index=True,
        blank=True,
        help_text="Normalized registration number (numeric portion only)"
    )
    student_name = models.CharField(max_length=200, blank=True, default="")
    
    resit_allocation = models.ForeignKey(
        "ResitCourseAllocation",
        on_delete=models.CASCADE,
        related_name="student_registrations",
    )
    
    registered_at = models.DateTimeField(auto_now_add=True)
    registered_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="student_resit_registrations",
    )
    
    class Meta:
        unique_together = [["resit_allocation", "student_reg_no_normalized"]]
        ordering = ["student_reg_no_normalized"]
        verbose_name = "Student Resit Registration"
        verbose_name_plural = "Student Resit Registrations"
    
    def save(self, *args, **kwargs):
        if self.student_reg_no:
            self.student_reg_no_normalized = self.normalize_reg_number(self.student_reg_no)
        super().save(*args, **kwargs)
    
    @staticmethod
    def normalize_reg_number(reg_no: str) -> str:
        """
        Extract numeric portion from registration number.
        e.g., "EB1/66791/23" -> "66791"
              "66791" -> "66791"
              "EB1/66791/23 (John Doe)" -> "66791"
        """
        import re
        if not reg_no:
            return ""
        # Extract first sequence of digits (min 4 digits)
        match = re.search(r'(\d{4,})', str(reg_no))
        if match:
            return match.group(1)
        # Fallback: remove non-digits
        return re.sub(r'\D', '', reg_no)
    
    def __str__(self):
        return f"{self.student_reg_no_normalized} - {self.resit_allocation.course_code}"


class ResitCourseAllocation(models.Model):
    """
    Simplified course allocation specifically for resit/supplementary examinations.
    """
    course_code = models.CharField(max_length=100)
    course_name = models.CharField(max_length=200)

    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="resit_allocations",
        help_text="Department offering this resit course.",
    )
    origin_department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.SET_NULL,
        related_name="origin_resit_allocations",
        null=True, blank=True,
        help_text="Original department that offered the course.",
    )
    faculty = models.ForeignKey(
        "faculty_management.Faculty",
        on_delete=models.SET_NULL,
        related_name="resit_allocations",
        null=True, blank=True,
        help_text=(
            "Faculty this resit course belongs to. Auto-derived from the "
            "department when not supplied explicitly (e.g. via an imported "
            "'faculty' column). Used by the auto-scheduler to disperse "
            "courses per faculty across days, timeslots and venues."
        ),
    )
    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.CASCADE,
        related_name="resit_allocations",
        null=True, blank=True,
        help_text="Program that this resit course belongs to.",
    )
    # ProgramCourse is required — every resit allocation must trace back
    # to a curriculum entry so year/semester are always known.
    # PROTECT stops accidental ProgramCourse deletion while resits exist.
    program_course = models.ForeignKey(
        "program_management.ProgramCourse",
        on_delete=models.PROTECT,
        related_name="resit_allocations",
        help_text="The curriculum course this resit allocation is for. Required.",
    )
    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.SET_NULL,
        related_name="resit_course_allocations",
        null=True, blank=True,
    )

    number_of_students = models.PositiveIntegerField(
        default=0,
        help_text="Number of students registered for this resit exam.",
    )

    submitted_to_timetabling = models.BooleanField(default=False)
    scheduled = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_resit_allocations",
    )

    academic_year = models.CharField(max_length=20, blank=True, default="")
    semester = models.CharField(max_length=10, blank=True, default="")

    class Meta:
        verbose_name = "Resit Course Allocation"
        verbose_name_plural = "Resit Course Allocations"
        ordering = ["department", "course_code"]
        unique_together = [["program", "course_code", "academic_year", "semester"]]

    def __str__(self) -> str:
        lecturer_name = self.lecturer.display_name if self.lecturer else "Unassigned"
        return f"Resit: {self.course_code} ({self.department.name}) - {lecturer_name}"

    def save(self, *args, **kwargs):
        if self.program_course and not self.course_name:
            self.course_name = self.program_course.course_name
        if self.program_course and not self.course_code:
            self.course_code = self.program_course.course_code
        # Auto-derive faculty from department when not explicitly set
        # (e.g. by an imported 'faculty' column).
        if not self.faculty_id and self.department_id and getattr(self.department, "faculty_id", None):
            self.faculty_id = self.department.faculty_id
        # Auto-update student count from registrations
        if self.pk:
            self.number_of_students = self.student_registrations.count()
        super().save(*args, **kwargs)
    
    def get_student_registrations_list(self) -> list:
        """Return list of normalized registration numbers for display."""
        return list(self.student_registrations.values_list(
            'student_reg_no_normalized', flat=True
        ))


class ResitTempTimetable(models.Model):
    """
    Draft (unpublished) resit exam timetable entry.
    Multiple courses can share the same venue/date/time.
    """
    resit_course_allocation = models.ForeignKey(
        "ResitCourseAllocation",
        on_delete=models.CASCADE,
        related_name="temp_timetable_entries",
    )
    venue = models.ForeignKey(
        "room_management.Venue",
        on_delete=models.CASCADE,
        related_name="resit_temp_entries",
        null=True, blank=True,
    )
    date = models.DateField(null=True, blank=True)
    day = models.CharField(max_length=20, blank=True, default="")
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    registered_students = models.PositiveIntegerField(default=0, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_resit_temp_entries",
    )

    def save(self, *args, **kwargs):
        if self.date and not self.day:
            self.day = self.date.strftime("%A")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"[DRAFT RESIT] {self.resit_course_allocation.course_code} — {self.date} {self.start_time}-{self.end_time}"

    class Meta:
        ordering = ["date", "start_time"]
        verbose_name = "Resit Temp Timetable Entry"
        verbose_name_plural = "Resit Temp Timetable Entries"


class ResitTimetable(models.Model):
    """
    Published / approved resit exam timetable entry.
    Multiple courses can share the same venue/date/time.
    """
    resit_course_allocation = models.ForeignKey(
        "ResitCourseAllocation",
        on_delete=models.CASCADE,
        related_name="published_timetable_entries",
    )
    venue = models.ForeignKey(
        "room_management.Venue",
        on_delete=models.CASCADE,
        related_name="resit_published_entries",
        null=True, blank=True,
    )
    date = models.DateField(null=True, blank=True)
    day = models.CharField(max_length=20, blank=True, default="")
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    registered_students = models.PositiveIntegerField(default=0, blank=True)

    published_by = models.ForeignKey(
        User,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="resit_timetable_publishes",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True, default="")

    def save(self, *args, **kwargs):
        if self.date and not self.day:
            self.day = self.date.strftime("%A")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"RESIT: {self.resit_course_allocation.course_code} — {self.date} {self.start_time}-{self.end_time}"

    class Meta:
        ordering = ["date", "start_time"]
        verbose_name = "Resit Timetable Entry"
        verbose_name_plural = "Resit Timetable Entries"


class ResitAutoMergedGroup(models.Model):
    """
    Auto-merged group for resit exams - multiple resit courses scheduled together
    in the same venue and timeslot.
    """
    base_course = models.ForeignKey(
        "ResitCourseAllocation",
        on_delete=models.CASCADE,
        related_name="base_merged_groups",
        help_text="Primary resit course allocation for this merged group.",
    )
    merged_courses = models.ManyToManyField(
        "ResitCourseAllocation",
        related_name="merged_in_groups",
        help_text="All resit course allocations included in this merged group.",
    )
    merged_code = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Display code for the merged group (e.g., 'RESIT-MERGED-01')",
    )
    total_students = models.PositiveIntegerField(
        default=0,
        help_text="Total number of students across all merged resit courses.",
    )
    
    # Scheduling information
    venue = models.ForeignKey(
        "room_management.Venue",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="resit_merged_groups",
    )
    date = models.DateField(null=True, blank=True)
    day = models.CharField(max_length=20, blank=True, default="")
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    registered_students = models.PositiveIntegerField(default=0, blank=True)
    
    # Status
    published = models.BooleanField(
        default=False,
        help_text="Whether this merged group is published to the main timetable.",
    )
    
    # Links to timetable entries
    temp_timetable_entry = models.ForeignKey(
        "ResitTempTimetable",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="merged_as_temp",
    )
    timetable_entry = models.ForeignKey(
        "ResitTimetable",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="merged_as_published",
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_resit_merged_groups",
    )
    
    class Meta:
        verbose_name = "Resit Auto Merged Group"
        verbose_name_plural = "Resit Auto Merged Groups"
        ordering = ["-created_at"]
    
    def __str__(self):
        return f"Resit Merged: {self.merged_code} ({self.total_students} students)"
    
    def get_active_timetable_entry(self):
        """Return the active timetable entry (published if published, else temp)."""
        if self.published:
            return self.timetable_entry
        return self.temp_timetable_entry


class ResitArchivedTimetable(models.Model):
    """Archived resit timetable entries for historical records."""
    resit_course_allocation = models.ForeignKey(
        "ResitCourseAllocation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="archived_entries",
    )
    course_code = models.CharField(max_length=100, blank=True, default="")
    course_name = models.CharField(max_length=200, blank=True, default="")
    venue_code = models.CharField(max_length=50, blank=True, default="")
    venue_name = models.CharField(max_length=200, blank=True, default="")
    date = models.DateField(null=True, blank=True)
    day = models.CharField(max_length=20, blank=True, default="")
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    registered_students = models.PositiveIntegerField(default=0, blank=True)

    archived_at = models.DateTimeField(auto_now_add=True)
    archived_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resit_archived_entries",
    )
    archive_reason = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ["-archived_at"]
        verbose_name = "Archived Resit Timetable Entry"
        verbose_name_plural = "Archived Resit Timetable Entries"

    def __str__(self) -> str:
        return f"[ARCHIVED] {self.course_code} - {self.date}"


class ResitArchivedAllocation(models.Model):
    """
    Archive snapshot of ResitCourseAllocation records.

    When "Archive Resit Allocations" is triggered for a given academic_year +
    semester, every ResitCourseAllocation for that period is copied here and
    the originals are deleted (clear).  This gives a permanent historical
    record while keeping the live table clean.
    """
    # ── Identity (denormalised so the archive is self-contained) ──────────
    course_code  = models.CharField(max_length=100, blank=True, default="")
    course_name  = models.CharField(max_length=200, blank=True, default="")
    academic_year = models.CharField(max_length=20, blank=True, default="",
                                     help_text="e.g. 2025/2026")
    semester     = models.CharField(max_length=10, blank=True, default="",
                                    help_text="e.g. S1, S2")

    # ── Original FK references (nullable — source rows may later be deleted) ─
    original_allocation = models.ForeignKey(
        "ResitCourseAllocation",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="resit_allocation_archives",
    )
    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="archived_resit_allocations",
    )
    origin_department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="origin_archived_resit_allocations",
    )
    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="archived_resit_allocations",
    )
    program_course = models.ForeignKey(
        "program_management.ProgramCourse",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="archived_resit_allocations",
    )
    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="archived_resit_allocations",
    )

    # ── Stats ─────────────────────────────────────────────────────────────
    number_of_students   = models.PositiveIntegerField(default=0)
    submitted_to_timetabling = models.BooleanField(default=False)
    scheduled            = models.BooleanField(default=False)

    # ── Timetable snapshot (denormalised from ResitTimetable if published) ─
    exam_date   = models.DateField(null=True, blank=True)
    exam_day    = models.CharField(max_length=20, blank=True, default="")
    start_time  = models.TimeField(null=True, blank=True)
    end_time    = models.TimeField(null=True, blank=True)
    venue_code  = models.CharField(max_length=50, blank=True, default="")
    venue_name  = models.CharField(max_length=200, blank=True, default="")

    # ── Archive metadata ──────────────────────────────────────────────────
    archived_at = models.DateTimeField(auto_now_add=True)
    archived_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="resit_allocation_archives",
    )
    archive_note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-archived_at", "course_code"]
        verbose_name = "Archived Resit Allocation"
        verbose_name_plural = "Archived Resit Allocations"
        indexes = [
            models.Index(fields=["academic_year", "semester"]),
            models.Index(fields=["archived_at"]),
        ]

    def __str__(self) -> str:
        return (
            f"[ARCHIVED ALLOC] {self.course_code} "
            f"— {self.academic_year} {self.semester}"
        )


class ResitVenueExclusion(models.Model):
    """
    Excludes venues from being used by the RESIT auto-scheduler specifically.

    This is independent of room_management.VenueBlock, which hard-blocks a
    venue for the regular/main autoscheduler only. A venue can be perfectly
    fine for regular teaching but still unwanted for resit exams (e.g. a
    building reserved for another faculty during the resit period), so this
    model gives resit scheduling its own on/off switch per venue or per
    whole building.

    Exactly one of `building` or `venue` must be set on a given row:
      - building set → every venue currently inside that building is
        excluded from the resit auto-scheduler (dynamic — venues added to
        the building later are automatically excluded too).
      - venue set    → only that specific venue is excluded.
    """
    building = models.ForeignKey(
        "room_management.Building",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="resit_exclusions",
        help_text="Exclude every venue in this building from the resit auto-scheduler.",
    )
    venue = models.ForeignKey(
        "room_management.Venue",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="resit_exclusions",
        help_text="Exclude this specific venue from the resit auto-scheduler.",
    )
    reason = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Why this venue/building is excluded from resits, e.g. 'Reserved for Senate sittings'.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck to temporarily lift the exclusion without deleting it.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="resit_venue_exclusions_created",
    )

    class Meta:
        verbose_name = "Resit Venue Exclusion"
        verbose_name_plural = "Resit Venue Exclusions"
        ordering = ["building__name", "venue__code"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(building__isnull=False, venue__isnull=True) |
                    models.Q(building__isnull=True, venue__isnull=False)
                ),
                name="resit_venue_exclusion_building_xor_venue",
            ),
            models.UniqueConstraint(
                fields=["building"],
                condition=models.Q(venue__isnull=True),
                name="unique_resit_building_exclusion",
            ),
            models.UniqueConstraint(
                fields=["venue"],
                condition=models.Q(building__isnull=True),
                name="unique_resit_venue_exclusion",
            ),
        ]

    def __str__(self) -> str:
        status = "ACTIVE" if self.is_active else "inactive"
        if self.building_id:
            return f"RESIT EXCLUDED BUILDING: {self.building.name} [{status}]"
        return f"RESIT EXCLUDED VENUE: {self.venue.code} [{status}]"

    @staticmethod
    def get_excluded_venue_ids() -> set:
        """
        Return the full set of Venue IDs currently excluded from the resit
        auto-scheduler, resolving whole-building exclusions to their member
        venues. Only active rows count.
        """
        from room_management.models import Venue

        qs = ResitVenueExclusion.objects.filter(is_active=True)

        excluded_ids = set(
            qs.filter(venue__isnull=False).values_list("venue_id", flat=True)
        )

        building_ids = list(
            qs.filter(building__isnull=False).values_list("building_id", flat=True)
        )
        if building_ids:
            excluded_ids.update(
                Venue.objects.filter(building_id__in=building_ids).values_list("id", flat=True)
            )

        return excluded_ids


class ResitSubmissionControl(models.Model):
    """Controls whether departments can submit resit allocations for timetabling."""
    department = models.OneToOneField(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="resit_submission_control",
        null=True, blank=True,
    )
    allow_submission_to_timetabling = models.BooleanField(default=True)
    allow_auto_scheduling = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="resit_submission_updates",
    )

    def __str__(self):
        dept_name = self.department.name if self.department else "GLOBAL"
        return f"Resit Control - {dept_name}"

    class Meta:
        verbose_name = "Resit Submission Control"
        verbose_name_plural = "Resit Submission Controls"



import os


class ResitPublishedPDF(models.Model):
    """
    Stores a published PDF snapshot of the resit timetable.

    Each time a timetabler clicks "Publish Timetable PDF", a new record is
    created with the generated PDF attached.  The latest record (highest pk)
    is considered the *current* published PDF and is served by the
    download-latest endpoint.
    """

    # ── Identifiers ──────────────────────────────────────────────────────
    academic_year = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="e.g. 2025/2026 — copied from ResitSchedulerConfig at publish time.",
    )
    semester = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text="e.g. S1, S2 — copied from ResitSchedulerConfig at publish time.",
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Auto-incremented version number within the same year/semester.",
    )

    # ── The PDF file itself ───────────────────────────────────────────────
    pdf_file = models.FileField(
        upload_to="resit_timetable_pdfs/",
        help_text="Generated PDF binary stored in MEDIA_ROOT.",
    )
    file_size_kb = models.PositiveIntegerField(
        default=0,
        help_text="File size in kilobytes — filled automatically on save.",
    )

    # ── Stats snapshot at publish time ────────────────────────────────────
    total_entries = models.PositiveIntegerField(
        default=0,
        help_text="Number of ResitTimetable rows included in this PDF.",
    )

    # ── Audit ─────────────────────────────────────────────────────────────
    published_at = models.DateTimeField(auto_now_add=True)
    published_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resit_pdf_publishes",
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Optional notes about this PDF version.",
    )

    # ── Helpers ───────────────────────────────────────────────────────────
    def save(self, *args, **kwargs):
        # Auto-compute file size after the file has been saved.
        super().save(*args, **kwargs)
        if self.pdf_file:
            try:
                size_bytes = self.pdf_file.size
                self.file_size_kb = max(1, size_bytes // 1024)
                # Update only the file_size_kb field to avoid recursion.
                ResitPublishedPDF.objects.filter(pk=self.pk).update(
                    file_size_kb=self.file_size_kb
                )
            except Exception:
                pass

    @property
    def filename(self):
        return os.path.basename(self.pdf_file.name) if self.pdf_file else ""

    def __str__(self) -> str:
        return (
            f"Resit PDF v{self.version} — "
            f"{self.academic_year} {self.semester} "
            f"({self.published_at.strftime('%Y-%m-%d %H:%M') if self.published_at else 'draft'})"
        )

    class Meta:
        ordering = ["-published_at"]
        verbose_name = "Resit Published PDF"
        verbose_name_plural = "Resit Published PDFs"
        indexes = [
            models.Index(fields=["academic_year", "semester"]),
            models.Index(fields=["published_at"]),
        ]