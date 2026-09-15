# timetable/models.py
# ===================================================================
#  TIMETABLE AND LECTURER CONSTRAINT MODELS
#  Contains all timetable-related models (Timetable, TempTimetable,
#  ExamTimetable, etc.) AND all lecturer constraint models
#  (LecturerBlockedSlot, LecturerTimePreference, LecturerTimePreferenceSlot,
#  LecturerVenuePreference).
#
#  These belong here because they are used by the autoscheduler and
#  directly affect timetable generation.
# ===================================================================

from django.db import models
from django.contrib.auth.models import User
import datetime
from django.utils import timezone


# ===========================================================================
#  SCHEDULER CONFIGURATION MODELS
# ===========================================================================

class LabSchedulerConfig(models.Model):
    start_time = models.TimeField(default=datetime.time(7, 0))   # 7:00 AM
    end_time = models.TimeField(default=datetime.time(19, 0))    # 7:00 PM
    slot_size = models.PositiveIntegerField(default=2, help_text="Slot size in hours")

    def __str__(self):
        return f"Scheduler Config ({self.start_time}-{self.end_time}, {self.slot_size} hrs)"

    class Meta:
        verbose_name = "Scheduler Configuration"
        verbose_name_plural = "Scheduler Configuration"


class ExamSchedulerConfig(models.Model):
    """
    Configuration for the exam scheduling engine.
    Controls dates, slots, and rules.
    """

    start_date = models.DateField(
        help_text="Start date of the exam period",
        default=timezone.now,
    )
    start_time = models.TimeField(
        default=datetime.time(8, 0),
        help_text="Earliest exam start time",
    )
    end_time = models.TimeField(
        default=datetime.time(17, 0),
        help_text="Latest exam end time",
    )
    slot_size = models.PositiveIntegerField(
        default=2,
        help_text="Duration of one exam session (in hours)",
    )
    excluded_days = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Comma-separated dates (YYYY-MM-DD) to skip. "
            "Weekends are populated automatically on save; "
            "you may add public holidays or any other dates here."
        ),
    )
    max_exam_days = models.PositiveIntegerField(
        default=14,
        help_text=(
            "Number of valid exam days required. "
            "Weekends and excluded dates are NOT counted – the scheduler "
            "will keep walking forward until this many working days are found."
        ),
    )
    spacing_ratio = models.FloatField(
        default=0.7,
        help_text=(
            "Allowed fill ratio for venue capacity "
            "(e.g. 0.7 = use up to 70 % of capacity)"
        ),
    )

    def excluded_date_list(self) -> list[str]:
        if not self.excluded_days:
            return []
        return [d.strip() for d in self.excluded_days.split(",") if d.strip()]

    def _lookahead_days(self) -> int:
        return int(self.max_exam_days * (7 / 5)) + 60

    def save(self, *args, **kwargs):
        lookahead = self._lookahead_days()

        # Build the set of every date in the new window (start_date + lookahead days).
        window_dates: set[str] = set()
        for i in range(lookahead):
            d = self.start_date + datetime.timedelta(days=i)
            window_dates.add(d.strftime("%Y-%m-%d"))

        # Keep only manually-excluded dates that still fall inside the new window.
        # Anything outside the window is stale and must be dropped.
        retained_manual: set[str] = {
            d for d in self.excluded_date_list()
            if d in window_dates
        }

        # Re-seed every weekend in the window (always excluded, regardless of
        # what was stored before).
        weekends: set[str] = set()
        for i in range(lookahead):
            candidate = self.start_date + datetime.timedelta(days=i)
            if candidate.weekday() >= 5:          # Saturday=5, Sunday=6
                weekends.add(candidate.strftime("%Y-%m-%d"))

        self.excluded_days = ", ".join(sorted(retained_manual | weekends))
        super().save(*args, **kwargs)

    def get_date_range(self) -> list[tuple[str, str]]:
        return self.get_excluded_date_range()

    def get_excluded_date_range(self) -> list[tuple[str, str]]:
        excluded: set[str] = set(self.excluded_date_list())
        valid_dates: list[tuple[str, str]] = []
        i = 0
        safety_cap = self._lookahead_days()
        while len(valid_dates) < self.max_exam_days:
            current: datetime.date = self.start_date + datetime.timedelta(days=i)
            date_str: str = current.strftime("%Y-%m-%d")
            if date_str not in excluded:
                valid_dates.append((date_str, current.strftime("%A")))
            i += 1
            if i > safety_cap:
                break
        return valid_dates

    def add_public_holiday(self, date: datetime.date) -> None:
        existing: set[str] = set(self.excluded_date_list())
        existing.add(date.strftime("%Y-%m-%d"))
        self.excluded_days = ", ".join(sorted(existing))
        self.save()

    def remove_excluded_date(self, date: datetime.date) -> None:
        existing: set[str] = set(self.excluded_date_list())
        existing.discard(date.strftime("%Y-%m-%d"))
        self.excluded_days = ", ".join(sorted(existing))
        self.save()

    def __str__(self) -> str:
        return f"Exam Config starting {self.start_date}"

    class Meta:
        verbose_name = "Exam Scheduler Config"
        verbose_name_plural = "Exam Scheduler Configs"


class SchedulerConfig(models.Model):
    # ── Regular (daytime) session ────────────────────────────────────────────
    start_time = models.TimeField(default="07:00")
    end_time = models.TimeField(default="19:00")
    slot_size = models.PositiveIntegerField(default=3, help_text="Slot size in hours")

    # ── Evening-class config ─────────────────────────────────────────────────
    enable_evening_classes = models.BooleanField(
        default=False,
        help_text=(
            "When enabled, courses marked as Evening/Weekend (is_evening_weekend=True) "
            "are scheduled into evening slots on weekdays. "
            "Regular courses are never placed here."
        ),
    )
    evening_start_time = models.TimeField(
        default="19:00",
        help_text="Start of the evening session (default 19:00 / 7 PM).",
    )
    evening_end_time = models.TimeField(
        default="21:00",
        help_text="End of the evening session (default 21:00 / 9 PM).",
    )
    evening_slot_count = models.PositiveIntegerField(
        default=1,
        help_text=(
            "Maximum number of evening slots per weekday. "
            "Default is 1 (a single 7–9 PM block)."
        ),
    )

    # ── Weekend-class config ─────────────────────────────────────────────────
    enable_weekend_classes = models.BooleanField(
        default=False,
        help_text=(
            "When enabled, courses marked as Evening/Weekend (is_evening_weekend=True) "
            "that could not fit in evening slots are placed on Saturday. "
            "Regular courses are never placed here."
        ),
    )
    weekend_start_time = models.TimeField(
        default="09:00",
        help_text="Start time for weekend sessions (default 09:00 / 9 AM).",
    )
    weekend_end_time = models.TimeField(
        default="17:00",
        help_text="End time for weekend sessions (default 17:00 / 5 PM).",
    )
    weekend_slot_size = models.PositiveIntegerField(
        default=3,
        help_text="Slot duration in hours for weekend sessions (default 3 hrs).",
    )

    def get_evening_slots(self):
        """Return the generated evening (start, end) time-slot tuples for weekdays."""
        from timetable.algorithms.regular_timetable_autosheduler_algorithm import generate_slots
        all_slots = generate_slots(self.evening_start_time, self.evening_end_time, self.slot_size)
        return all_slots[: self.evening_slot_count]

    def get_weekend_slots(self):
        """Return the generated weekend (start, end) time-slot tuples."""
        from timetable.algorithms.regular_timetable_autosheduler_algorithm import generate_slots
        return generate_slots(self.weekend_start_time, self.weekend_end_time, self.weekend_slot_size)

    def get_weekend_days(self):
        return ["Saturday"]

    def __str__(self):
        return f"Scheduler Config ({self.start_time}-{self.end_time}, {self.slot_size} hrs)"

    class Meta:
        verbose_name = "Scheduler Configuration"
        verbose_name_plural = "Scheduler Configuration"


# ===========================================================================
#  TIMETABLE MODELS
# ===========================================================================

class Timetable(models.Model):
    """
    Final timetable allocations.
    Managed by Director of Timetable and Timetable admins.
    Department users can only view this.
    """
    course_allocation = models.ForeignKey(
        'course_allocation.CourseAllocation', on_delete=models.CASCADE, related_name="timetable_entries"
    )
    venue = models.ForeignKey('room_management.Venue', on_delete=models.CASCADE)
    day = models.CharField(max_length=20)  # e.g. Monday, Tuesday
    start_time = models.TimeField()
    end_time = models.TimeField()

    def __str__(self):
        return f"{self.course_allocation.course_code} - {self.day} {self.start_time}-{self.end_time}"

    class Meta:
        permissions = [
            ("approve_timetable", "Can approve timetable"),
        ]
        indexes = [
            models.Index(fields=['day']),
            # Matches the slot-collision lookup pattern used by the
            # notification/conflict-detection generators (day + start/end time).
            models.Index(fields=['day', 'start_time', 'end_time']),
        ]


class TempTimetable(models.Model):
    course_allocation = models.ForeignKey(
        'course_allocation.CourseAllocation', on_delete=models.CASCADE, related_name="temp_timetable_entries"
    )
    venue = models.ForeignKey('room_management.Venue', on_delete=models.CASCADE)
    day = models.CharField(max_length=20)  # Monday, Tuesday...
    start_time = models.TimeField()
    end_time = models.TimeField()

    def __str__(self):
        return f"[TEMP] {self.course_allocation.course_code} - {self.day} {self.start_time}-{self.end_time}"

    class Meta:
        # course_allocation is deliberately part of this key. Without it,
        # the DB treats ANY two rows sharing (venue, day, start, end) as a
        # duplicate — which blocks the legitimate case of a merged/combined
        # course group, where two or more DIFFERENT course_allocations are
        # meant to sit in the exact same venue/slot together (they're taught
        # as one class). Including course_allocation still catches the real
        # error this constraint exists for — the same course being inserted
        # into the same slot twice — without falsely rejecting combined
        # groups.
        unique_together = ("course_allocation", "venue", "day", "start_time", "end_time")


class ExamTimetable(models.Model):
    """
    Final approved exam timetable.
    Managed by Director/Timetable Admins; view-only for departments.
    """
    course_allocation = models.ForeignKey(
        "course_allocation.CourseAllocation",
        on_delete=models.CASCADE,
        related_name="exam_timetable_entries"
    )
    venue = models.ForeignKey('room_management.Venue', on_delete=models.CASCADE)
    day = models.CharField(max_length=20)  # e.g. Monday, Tuesday
    date = models.DateField(default=timezone.now)
    start_time = models.TimeField()
    end_time = models.TimeField()

    def __str__(self):
        return f"{self.course_allocation.course_code} - {self.day} {self.date} {self.start_time}-{self.end_time}"

    class Meta:
        # course_allocation is deliberately part of this key — see the
        # identical fix/comment on TempTimetable above. Without it, the DB
        # only blocks two DIFFERENT courses sharing a room/slot; it does
        # nothing to stop the SAME course being written twice (e.g. once on
        # date 10 and again on date 27), and it also silently drops rows for
        # legitimate merged/combined exam groups sharing one venue.
        unique_together = ("course_allocation", "venue", "date", "start_time", "end_time")
        permissions = [
            ("approve_timetable", "Can approve timetable"),
        ]
        ordering = ["date", "start_time"]


class ExamTempTimetable(models.Model):
    """
    Temporary exam timetable before approval.
    Used for generating and editing draft exam schedules.
    """
    course_allocation = models.ForeignKey(
        "course_allocation.CourseAllocation",
        on_delete=models.CASCADE,
        related_name="exam_temp_timetable_entries"
    )
    venue = models.ForeignKey("room_management.Venue", on_delete=models.CASCADE)
    day = models.CharField(max_length=20)  # Monday, Tuesday...
    date = models.DateField(default=timezone.now)
    start_time = models.TimeField()
    end_time = models.TimeField()

    def __str__(self):
        return f"[TEMP] {self.course_allocation.course_code} - {self.day} {self.date} {self.start_time}-{self.end_time}"

    class Meta:
        # FIX: course_allocation added to the unique key (previously just
        # venue/date/start_time/end_time). That old key had two problems:
        #   1. It could NOT stop the same course_allocation being inserted
        #      twice at two different dates/venues — the root cause of the
        #      "same course scheduled on date 10 AND date 27" bug, since
        #      nothing in the DB schema referenced course_allocation at all.
        #   2. Worse, when a merged/combined exam family placed 2+ DIFFERENT
        #      course_allocations in the SAME venue/date/slot (by design —
        #      see place_merged_family), the old key treated those rows as
        #      duplicates of EACH OTHER. bulk_create(..., ignore_conflicts=True)
        #      then silently kept only the first one and dropped the rest,
        #      even though the scheduler had already marked every member as
        #      "scheduled" — leaving real exams missing from the DB.
        # Including course_allocation fixes both: two different courses can
        # still share a venue/slot (combined groups), but the same course
        # can never get two rows for the same slot again. (Genuine multi-
        # venue splits of one big course still work fine — those rows differ
        # by venue.) This mirrors the identical, already-applied fix on
        # TempTimetable (see its Meta docstring / migration 0002).
        unique_together = ("course_allocation", "venue", "date", "start_time", "end_time")
        ordering = ["date", "start_time"]


class LabTimetable(models.Model):
    """A scheduled lab session for a lab allocation."""
    lab_allocation = models.ForeignKey(
        "course_allocation.LabAllocation", on_delete=models.CASCADE, related_name="lab_timetables"
    )
    lab_venue = models.ForeignKey(
        "room_management.LabVenue", on_delete=models.CASCADE, related_name="lab_timetable_entries"
    )
    day = models.CharField(max_length=16)  # e.g. Monday
    start_time = models.TimeField()
    end_time = models.TimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("lab_venue", "day", "start_time", "end_time")

    def __str__(self):
        return f"{self.lab_allocation.program_course.course_code} - {self.lab_venue.code} {self.day} {self.start_time}-{self.end_time}"


class LabExamTimetable(models.Model):
    """Scheduled Lab Exam session."""
    lab_allocation = models.ForeignKey(
        "course_allocation.LabAllocation", on_delete=models.CASCADE, related_name="lab_exam_timetables"
    )
    lab_venue = models.ForeignKey(
        "room_management.LabVenue", on_delete=models.CASCADE, related_name="lab_exam_timetable_entries"
    )
    date = models.DateField(default=timezone.now)
    day = models.CharField(max_length=16)
    start_time = models.TimeField()
    end_time = models.TimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("lab_venue", "date", "start_time", "end_time")
        ordering = ["date", "start_time"]

    def __str__(self):
        return f"{self.lab_allocation.program_course.course_code} - {self.lab_venue.code} {self.day} {self.date} {self.start_time}-{self.end_time}"


class TimetableArchive(models.Model):
    """
    Stores deleted timetables (both main and exam) for up to 4 semesters.
    Avoids duplicates and allows unarchiving (republishing).
    """

    TIMETABLE_TYPES = [
        ("MAIN", "Main Timetable"),
        ("EXAM", "Exam Timetable"),
    ]
    SEMESTER_CHOICES = [
        ("1", "1st Semester"),
        ("2", "2nd Semester"),
    ]

    timetable_type = models.CharField(max_length=10, choices=TIMETABLE_TYPES)
    semester = models.CharField(max_length=2, choices=SEMESTER_CHOICES)
    academic_year = models.CharField(max_length=15, help_text="e.g. 2024/2025")
    archived_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    archived_at = models.DateTimeField(default=timezone.now)
    data = models.JSONField()

    class Meta:
        unique_together = ("semester", "academic_year", "timetable_type")
        ordering = ["-archived_at"]

    def __str__(self):
        return f"{self.get_timetable_type_display()} - {self.academic_year} S{self.semester}"

    def clean_old_archives():
        """Keeps only 4 most recent semester archives."""
        archives = TimetableArchive.objects.order_by("-archived_at")
        if archives.count() > 4:
            for old in archives[4:]:
                old.delete()


# ===========================================================================
#  SHARED VENUE EXAM GROUP  — links to ExamTimetable / ExamTempTimetable
# ===========================================================================

class SharedVenueExamGroup(models.Model):
    """
    Allows multiple CourseAllocation entries to share one exam venue
    at the same date/time slot while preventing time collisions.
    Used when different programs have exams together in the same room.

    Timetable linkage (NEW — both fields nullable, DB-safe)
    --------------------------------------------------------
    published=True  → exam_timetable_entry      → ExamTimetable
    published=False → exam_temp_timetable_entry → ExamTempTimetable
    """

    venue = models.ForeignKey(
        "room_management.Venue",
        on_delete=models.CASCADE,
        related_name="shared_exam_groups",
        help_text="Venue being shared for this exam session",
    )
    date = models.DateField(default=timezone.now)
    day = models.CharField(max_length=20, help_text="Day of the exam (e.g., Monday)")
    start_time = models.TimeField()
    end_time = models.TimeField()

    course_allocations = models.ManyToManyField(
        "course_allocation.CourseAllocation",
        related_name="shared_exam_venues",
        help_text="Courses sharing this venue at the same time",
    )
    total_students = models.PositiveIntegerField(
        default=0,
        help_text="Total number of students across all shared courses",
    )
    published = models.BooleanField(
        default=False,
        help_text="Has this shared group been published/approved?",
    )

    # --- NEW: exam timetable linkage (nullable = no existing rows broken) ---
    exam_timetable_entry = models.ForeignKey(
        "ExamTimetable",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shared_venue_groups",
        help_text="Published ExamTimetable entry for this shared group (used when published=True)",
    )
    exam_temp_timetable_entry = models.ForeignKey(
        "ExamTempTimetable",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shared_venue_groups_temp",
        help_text="Draft ExamTempTimetable entry for this shared group (used when published=False)",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("venue", "date", "start_time", "end_time")
        ordering = ["date", "start_time"]
        verbose_name = "Shared Venue Exam Group"
        verbose_name_plural = "Shared Venue Exam Groups"

    def __str__(self):
        return f"{self.venue.code} shared on {self.date} {self.start_time}-{self.end_time}"

    def clean(self):
        from django.core.exceptions import ValidationError
        overlapping = SharedVenueExamGroup.objects.filter(
            venue=self.venue,
            date=self.date,
        ).exclude(pk=self.pk).filter(
            start_time__lt=self.end_time,
            end_time__gt=self.start_time,
        )
        if overlapping.exists():
            raise ValidationError(
                f"Venue {self.venue.code} already booked during this time."
            )

    def update_total_students(self):
        total = sum(ca.number_of_students for ca in self.course_allocations.all())
        self.total_students = total
        self.save()

    def get_active_timetable_entry(self):
        """Return the correct timetable entry based on published state."""
        return self.exam_timetable_entry if self.published else self.exam_temp_timetable_entry


# ===========================================================================
#  MERGED COURSE GROUP  — EXAM merges, links to ExamTimetable / ExamTempTimetable
# ===========================================================================

class MergedCourseGroup(models.Model):
    """
    Records that several CourseAllocation rows were merged for a single
    exam sitting (manual exam merge workflow).

    Timetable linkage (NEW — both fields nullable, DB-safe)
    --------------------------------------------------------
    published=True  → exam_timetable_entry      → ExamTimetable (base course row)
    published=False → exam_temp_timetable_entry → ExamTempTimetable (base course row)
    """
    base_course = models.ForeignKey(
        "course_allocation.CourseAllocation",
        on_delete=models.CASCADE,
        related_name="merged_as_base",
        help_text="Representative course allocation (first code)",
    )
    merged_courses = models.ManyToManyField(
        "course_allocation.CourseAllocation",
        related_name="merged_groups",
        help_text="All course allocations included in this merged exam",
    )
    merged_code = models.CharField(max_length=50, blank=True, help_text="Normalized base code")
    total_students = models.PositiveIntegerField(default=0)
    date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    venue = models.ForeignKey(
        "room_management.Venue", null=True, blank=True, on_delete=models.SET_NULL
    )
    published = models.BooleanField(
        default=False,
        help_text="Has this merged group been published/approved?",
    )

    # --- NEW: exam timetable linkage (nullable = no existing rows broken) ---
    exam_timetable_entry = models.ForeignKey(
        "ExamTimetable",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="merged_course_groups",
        help_text="Published ExamTimetable entry for the base course (used when published=True)",
    )
    exam_temp_timetable_entry = models.ForeignKey(
        "ExamTempTimetable",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="merged_course_groups_temp",
        help_text="Draft ExamTempTimetable entry for the base course (used when published=False)",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"MergedGroup: {self.merged_code} ({self.total_students} students) on {self.date} {self.start_time}"

    def get_active_timetable_entry(self):
        """Return the correct timetable entry based on published state."""
        return self.exam_timetable_entry if self.published else self.exam_temp_timetable_entry


# ===========================================================================
#  MERGED COURSE GROUP (REGULAR TIMETABLE)
#  Renamed from AutoMergedExamGroup → MergedCourseGroupTimetable
#  Links base_course to Timetable (published) / TempTimetable (draft)
#  Uses `day` (not `date`) — matches the regular Timetable model structure
#  DB table name pinned to original so NO rename migration is generated
# ===========================================================================

class MergedCourseGroupTimetable(models.Model):
    """
    Represents multiple CourseAllocation rows merged automatically
    during regular (non-exam) timetable scheduling
    (e.g. COSC312, COSC312(A), COSC312(C) sharing one lecture slot).

    Previously named AutoMergedExamGroup.
    The physical DB table is pinned via Meta.db_table so the rename
    produces only ADD COLUMN migrations — no destructive changes.

    Timetable linkage (NEW — both fields nullable, DB-safe)
    --------------------------------------------------------
    published=True  → timetable_entry      → Timetable (base course row)
    published=False → temp_timetable_entry → TempTimetable (base course row)

    Note: uses `day` (e.g. "Monday"), not a date field, matching the
    regular Timetable / TempTimetable models.
    """
    base_course = models.ForeignKey(
        "course_allocation.CourseAllocation",
        on_delete=models.CASCADE,
        related_name="auto_merged_as_base",        # kept to avoid breaking existing queries
        help_text="Representative base course for this merged timetable group",
    )
    merged_courses = models.ManyToManyField(
        "course_allocation.CourseAllocation",
        related_name="auto_merged_groups",          # kept to avoid breaking existing queries
        help_text="All course allocations included in this merged timetable slot",
    )
    merged_code = models.CharField(
        max_length=50,
        blank=True,
        help_text="Normalized base course code (e.g., COSC312)",
    )
    total_students = models.PositiveIntegerField(default=0)
    # Kept as CharField — preserves the existing DB column type exactly
    date = models.CharField(max_length=100, blank=True, default="check in timetable")
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    venue = models.ForeignKey(
        "room_management.Venue", null=True, blank=True, on_delete=models.SET_NULL
    )
    published = models.BooleanField(
        default=False,
        help_text="Has this merged group been published/approved?",
    )

    # --- NEW: regular timetable linkage (nullable = no existing rows broken) ---
    timetable_entry = models.ForeignKey(
        "Timetable",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="merged_timetable_groups",
        help_text="Published Timetable entry for the base course (used when published=True)",
    )
    temp_timetable_entry = models.ForeignKey(
        "TempTimetable",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="merged_timetable_groups_temp",
        help_text="Draft TempTimetable entry for the base course (used when published=False)",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Pins the physical table to the original name — no rename migration needed
        db_table = "timetable_automergedexamgroup"

    def __str__(self):
        return f"{self.merged_code} ({self.total_students} students)"

    def get_active_timetable_entry(self):
        """Return the correct timetable entry based on published state."""
        return self.timetable_entry if self.published else self.temp_timetable_entry


# ---------------------------------------------------------------------------
# Backward-compat alias so any existing code using AutoMergedExamGroup
# continues to work without any changes.
# ---------------------------------------------------------------------------
AutoMergedExamGroup = MergedCourseGroupTimetable


# ===========================================================================
#  LECTURER CONSTRAINT MODELS
#  These belong in timetable/models.py because they are used by the
#  autoscheduler and directly affect timetable generation.
# ===========================================================================

class LecturerBlockedSlot(models.Model):
    """
    HARD constraint: a lecturer must never be placed in a given day (and
    optionally a specific time range within that day) by the autoscheduler.

    Two shapes:
      - Whole day blocked: start_time and end_time both left blank
        (e.g. "no classes for Dr. Otieno on Thursday at all").
      - Specific day+time blocked: start_time/end_time set
        (e.g. "if scheduled on Wednesday, never after 2pm").

    Enforced inside ConflictTracker.has_lecturer_conflict() in the
    autoscheduler, which every scheduling phase already calls before placing
    a course — so this is respected everywhere automatically, with no need
    to touch each phase individually.
    """
    DAY_CHOICES = [
        ("Monday", "Monday"), ("Tuesday", "Tuesday"), ("Wednesday", "Wednesday"),
        ("Thursday", "Thursday"), ("Friday", "Friday"),
        ("Saturday", "Saturday"), ("Sunday", "Sunday"),
    ]

    lecturer = models.ForeignKey(
        'lecturer_portal.Lecturer',
        on_delete=models.CASCADE,
        related_name="blocked_slots",
    )
    day = models.CharField(max_length=20, choices=DAY_CHOICES)
    start_time = models.TimeField(
        null=True, blank=True,
        help_text="Leave blank together with End Time to block the entire day.",
    )
    end_time = models.TimeField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["lecturer__name", "day", "start_time"]
        verbose_name = "Lecturer Blocked Slot"
        verbose_name_plural = "Lecturer Blocked Slots"

    def __str__(self):
        if self.start_time and self.end_time:
            span = f"{self.start_time.strftime('%H:%M')}–{self.end_time.strftime('%H:%M')}"
        else:
            span = "ALL DAY"
        status = "" if self.is_active else " [inactive]"
        return f"{self.lecturer.name}: blocked {self.day} {span}{status}"


class LecturerTimePreference(models.Model):
    """
    Container for a lecturer's time preferences.
    Each preference entry can have multiple slots (day + timeslot).
    This replaces the old single-day, single-time-range model.

    SOFT constraint: the autoscheduler tries to honour this (via a
    best-effort post-scheduling relocation pass) but will not leave a
    course unscheduled just to satisfy it.
    """
    DAY_CHOICES = LecturerBlockedSlot.DAY_CHOICES

    lecturer = models.ForeignKey(
        'lecturer_portal.Lecturer',
        on_delete=models.CASCADE,
        related_name='time_preferences'
    )
    notes = models.CharField(max_length=255, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        days = self.slots.values_list('day', flat=True).distinct()
        day_list = ', '.join(days[:3])
        if len(days) > 3:
            day_list += f' +{len(days) - 3} more'
        return f"{self.lecturer.name} — {day_list}"

    def get_slots_for_day(self, day):
        """Return all slots for a given day."""
        return self.slots.filter(day=day)

    def has_whole_day(self, day):
        """Check if a given day has a 'whole day' slot."""
        return self.slots.filter(day=day, is_whole_day=True).exists()

    def get_day_slot_times(self, day):
        """Return list of (start_time, end_time) tuples for a given day."""
        return list(self.slots.filter(day=day, is_whole_day=False).values_list('start_time', 'end_time'))

    def get_all_days(self):
        """Return all days that have at least one slot."""
        return list(self.slots.values_list('day', flat=True).distinct())

    def get_formatted_summary(self):
        """Return a human-readable summary of all preferences."""
        summary = []
        for day in self.get_all_days():
            slots = self.slots.filter(day=day)
            if slots.filter(is_whole_day=True).exists():
                summary.append(f"{day} (Whole Day)")
            else:
                times = [f"{s.start_time.strftime('%H:%M')}–{s.end_time.strftime('%H:%M')}"
                        for s in slots.filter(is_whole_day=False).order_by('start_time')]
                if times:
                    summary.append(f"{day}: {', '.join(times)}")
        return '; '.join(summary)


class LecturerTimePreferenceSlot(models.Model):
    """
    Individual day + timeslot for a lecturer's time preference.
    """
    DAY_CHOICES = LecturerBlockedSlot.DAY_CHOICES

    preference = models.ForeignKey(
        LecturerTimePreference,
        on_delete=models.CASCADE,
        related_name='slots'
    )
    day = models.CharField(max_length=20, choices=DAY_CHOICES)
    start_time = models.TimeField(null=True, blank=True, help_text="Null = whole day")
    end_time = models.TimeField(null=True, blank=True, help_text="Null = whole day")
    is_whole_day = models.BooleanField(default=False)

    class Meta:
        ordering = ['day', 'start_time']
        unique_together = ('preference', 'day', 'start_time', 'end_time')

    def __str__(self):
        if self.is_whole_day:
            return f"{self.day} (Whole Day)"
        return f"{self.day} {self.start_time.strftime('%H:%M')}–{self.end_time.strftime('%H:%M')}"

    @property
    def time_display(self):
        if self.is_whole_day:
            return "Whole Day"
        return f"{self.start_time.strftime('%H:%M')}–{self.end_time.strftime('%H:%M')}"


class LecturerVenuePreference(models.Model):
    """
    SOFT constraint: a lecturer prefers one or more particular venues. The
    autoscheduler tries to relocate their classes into a preferred venue
    when one is free at the same day/time, but never at the cost of leaving
    the course unscheduled.
    """
    lecturer = models.ForeignKey(
        'lecturer_portal.Lecturer',
        on_delete=models.CASCADE,
        related_name="venue_preferences",
    )
    venues = models.ManyToManyField(
        'room_management.Venue',
        related_name="lecturer_preferences",
        help_text="One or more venues this lecturer prefers to be scheduled in.",
    )
    notes = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["lecturer__name"]
        verbose_name = "Lecturer Venue Preference"
        verbose_name_plural = "Lecturer Venue Preferences"

    def __str__(self):
        venue_list = ", ".join(v.code for v in self.venues.all()[:3])
        if self.venues.count() > 3:
            venue_list += f" +{self.venues.count() - 3} more"
        status = "" if self.is_active else " [inactive]"
        return f"{self.lecturer.name}: prefers [{venue_list}]{status}"