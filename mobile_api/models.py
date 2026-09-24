"""
mobile_api/models.py
=====================
Backs the mobile app's "Events & Memos" feed. This is intentionally a new,
small model rather than reusing notifications.Notification, because that
model targets Django Users/Groups/Roles (staff-only, session-authenticated)
and has no attachment support — it isn't reachable by an unauthenticated
student/lecturer identified only by registration number or name, and can't
carry a memo PDF or event flyer image.

Staff post Announcements from /admin/ (registered in admin.py with an
inline for attachments). The mobile API (views_events.py) filters them by
audience and, for students, by program/year using the same scope every
other mobile endpoint resolves from the registration number.
"""
import mimetypes

from django.db import models


class ScheduleVisibility(models.Model):
    """
    Per-schedule on/off switch for what the mobile app dashboard shows.

    There are exactly two rows, one per SCHEDULE_CHOICES value ("main" —
    the recurring weekly timetable — and "exam" — the published, date-based
    exam timetable), enforced by the unique constraint on schedule_type.
    Managed from Django admin rather than a bespoke dashboard screen, so
    no new UI surface or permission model is needed — staff who already
    have admin access can flip these.

    When is_blocked is True for a schedule, the mobile app hides that
    schedule's grid entirely and shows `message` in its place, plus an
    optional tappable link (`link_label` / `link_url`) — e.g. pointing at
    a PDF of a provisional timetable — that opens/downloads on tap. See
    mobile_api.timetable_builder (`_visibility_payload`, folded into both
    the version hash and the full serialized payload) and, on the app
    side, screens/TimetableScreen.js + components/BlockedScheduleNotice.js.
    """
    MAIN = "main"
    EXAM = "exam"
    SCHEDULE_CHOICES = [
        (MAIN, "Regular timetable"),
        (EXAM, "Exam timetable"),
    ]

    schedule_type = models.CharField(max_length=10, choices=SCHEDULE_CHOICES, unique=True)
    is_blocked = models.BooleanField(
        default=False,
        help_text="When ON, the mobile app hides this schedule's grid and shows the "
                  "message/link below instead of the timetable data.",
    )
    message = models.TextField(
        blank=True,
        default="",
        help_text="Shown to students/lecturers in place of the schedule while blocked, "
                  "e.g. 'The exam timetable is being finalised and will be published shortly.'",
    )
    link_label = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Text on the tappable link/button shown under the message, e.g. "
                  "'Download provisional timetable (PDF)'. Leave blank to show no link.",
    )
    link_url = models.URLField(
        blank=True,
        default="",
        help_text="Where the link goes, e.g. a PDF uploaded elsewhere on the site. "
                  "Opened (and downloaded, for a PDF) on the device when tapped.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Mobile schedule visibility"
        verbose_name_plural = "Mobile schedule visibility"
        ordering = ["schedule_type"]

    def __str__(self):
        state = "BLOCKED" if self.is_blocked else "visible"
        return f"{self.get_schedule_type_display()} — {state}"

    @classmethod
    def get_for(cls, schedule_type):
        """Return the row for 'main' or 'exam', creating a default (visible) one if missing."""
        obj, _ = cls.objects.get_or_create(schedule_type=schedule_type)
        return obj


class DeviceInstall(models.Model):
    """
    One row per distinct mobile-app installation, identified by a
    device-generated ID rather than a user account — the same person can
    have several devices, and this system has no per-student account
    anyway (see mobile_api.scope). Reported once per device by the app
    itself, right after it launches for the first time post-install, via
    mobile_api.views_install.report_install; every later launch just
    touches last_seen_at instead of creating a new row (get_or_create on
    device_id), so the count here reflects distinct installed devices, not
    launches or logins. Powers the "Mobile Analytics" page on the
    Timetabling Dashboard. See mobile_app/src/utils/deviceId.js and
    services/installTracker.js for the app side.
    """
    ANDROID = "android"
    IOS = "ios"
    WEB = "web"
    OTHER = "other"
    PLATFORM_CHOICES = [
        (ANDROID, "Android"),
        (IOS, "iOS"),
        (WEB, "Web"),
        (OTHER, "Other"),
    ]

    device_id = models.CharField(
        max_length=200,
        unique=True,
        help_text="Device-generated identifier the app persists locally so it never reports the same install twice.",
    )
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES, default=OTHER)
    app_version = models.CharField(max_length=30, blank=True, default="")
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Mobile app install"
        verbose_name_plural = "Mobile app installs"
        ordering = ["-first_seen_at"]
        indexes = [
            models.Index(fields=["platform"], name="mobapi_devinst_platform_idx"),
            models.Index(fields=["first_seen_at"], name="mobapi_devinst_firstseen_idx"),
        ]

    def __str__(self):
        return f"{self.device_id[:16]}… ({self.get_platform_display()})"


class Announcement(models.Model):
    AUDIENCE_ALL = "all"
    AUDIENCE_STUDENTS = "students"
    AUDIENCE_LECTURERS = "lecturers"
    AUDIENCE_CHOICES = [
        (AUDIENCE_ALL, "Everyone"),
        (AUDIENCE_STUDENTS, "Students only"),
        (AUDIENCE_LECTURERS, "Lecturers only"),
    ]

    TYPE_EVENT = "event"
    TYPE_MEMO = "memo"
    TYPE_CHOICES = [
        (TYPE_EVENT, "Event"),
        (TYPE_MEMO, "Memo"),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    announcement_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_MEMO)
    audience = models.CharField(max_length=10, choices=AUDIENCE_CHOICES, default=AUDIENCE_ALL)

    # Optional narrowing for student audiences. Leave both blank to reach
    # every student regardless of program/year (e.g. a university-wide
    # closure notice); set program (and optionally year) to target one
    # cohort (e.g. "BSc CS Year 2 CAT postponed").
    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mobile_announcements",
        help_text="Leave blank to reach students in every program.",
    )
    year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Leave blank to reach every year of study in the selected program.",
    )

    date = models.DateTimeField(help_text="Date shown to the reader (event date, or memo issue date).")
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["is_published", "audience"]),
            models.Index(fields=["program", "year"]),
        ]

    def __str__(self):
        return f"[{self.get_announcement_type_display()}] {self.title}"


def announcement_attachment_path(instance, filename):
    return f"mobile_announcements/{instance.announcement_id}/{filename}"


class AnnouncementAttachment(models.Model):
    """A memo PDF, event flyer image, Word doc, etc. attached to an
    Announcement. mime_type is auto-detected on save so the mobile app
    knows which icon/opener to use without guessing from the filename."""

    announcement = models.ForeignKey(
        Announcement, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to=announcement_attachment_path)
    name = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)

    def save(self, *args, **kwargs):
        if self.file and not self.name:
            self.name = self.file.name.rsplit("/", 1)[-1]
        if self.file and not self.mime_type:
            guessed, _ = mimetypes.guess_type(self.file.name)
            self.mime_type = guessed or "application/octet-stream"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name or str(self.file)


def shared_file_upload_path(instance, filename):
    return f"mobile_shared_files/{filename}"


class SharedFile(models.Model):
    """
    A file (timetable PDF, circular, any document) uploaded once from the
    Timetabling Dashboard and given a stable, copyable public link — separate
    from Announcement/AnnouncementAttachment, which are always tied to a
    posted memo/event in the app's feed. This is for the simpler case: "I
    just want a link to hand out or drop into Schedule Visibility's link
    field, without pushing a notification."

    Served from MEDIA_URL (see `file.url`); the dashboard's "Shared Files"
    page (views_shared_files.py) lists every upload with a one-click "Copy
    link" button and an upload form, wired into Section 4 of
    timetabling_dashboard.html.
    """
    file = models.FileField(upload_to=shared_file_upload_path, max_length=500)
    title = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Optional display name. Defaults to the filename if left blank.",
    )
    mime_type = models.CharField(max_length=100, blank=True, default="")
    file_size = models.PositiveIntegerField(default=0, help_text="Bytes, captured at upload time.")
    uploaded_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mobile_shared_files",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Shared file"
        verbose_name_plural = "Shared files"
        ordering = ["-uploaded_at"]

    def save(self, *args, **kwargs):
        if self.file and not self.mime_type:
            guessed, _ = mimetypes.guess_type(self.file.name)
            self.mime_type = guessed or "application/octet-stream"
        if self.file:
            try:
                self.file_size = self.file.size
            except (OSError, ValueError):
                pass
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        return self.title or self.filename

    @property
    def filename(self):
        return self.file.name.rsplit("/", 1)[-1] if self.file else ""

    def __str__(self):
        return self.display_name


class PersonalCourseEntry(models.Model):
    """
    A course a student or lecturer searched for (mobile_api/course_search.py)
    and chose to add to their own mobile timetable, on top of whatever their
    normal scope already shows automatically — a student's program/year
    curriculum, or a lecturer's own CourseAllocation.lecturer assignments
    (see timetable_builder.queryset_for_scope).

    Exists for the cases automatic scope doesn't cover: a student picking up
    a cross-cutting/elective unit outside their program's curriculum, or a
    lecturer who co-teaches/guest-lectures a section that isn't recorded
    against them as the CourseAllocation.lecturer — e.g. one of several
    "COMS 101" sections/combined groups, where they search the base code,
    see every section, and pick theirs.

    Deliberately NOT a DB-level unique constraint: MySQL (the production
    backend) doesn't support conditional/partial unique indexes, and a
    plain composite unique_together across role+reg_no+lecturer+
    course_allocation is unreliable here too, because whichever identity
    column is irrelevant for a given role (lecturer for a student row,
    reg_no for a lecturer row) is always NULL/blank, and MySQL doesn't
    treat repeated NULLs as a collision in a unique index. Dedup is instead
    enforced the one place rows get created — personal_entries.add_entries()
    always goes through get_or_create with the exact role-appropriate
    identity kwargs — so this is safe as long as that stays the only write
    path (it's the only one exposed via mobile_api/views_courses.py).
    """
    ROLE_STUDENT = "student"
    ROLE_LECTURER = "lecturer"
    ROLE_CHOICES = [
        (ROLE_STUDENT, "Student"),
        (ROLE_LECTURER, "Lecturer"),
    ]

    role = models.CharField(max_length=10, choices=ROLE_CHOICES)

    # Set when role=student: the student's own registration number
    # (upper-cased). The only truly individual identifier available for a
    # student in this system — see mobile_api/scope.py's module docstring;
    # the shared cohort "userId" (stu:<dept>:<program>:<year>) isn't unique
    # per student, so it can't be the key for a personal add-on list.
    reg_no = models.CharField(max_length=40, blank=True, default="")

    # Set when role=lecturer — lecturer identity IS already individual
    # (userId is lec:<id>), so no extra field is needed for that role.
    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="personal_timetable_entries",
    )

    course_allocation = models.ForeignKey(
        "course_allocation.CourseAllocation",
        on_delete=models.CASCADE,
        related_name="personal_timetable_entries",
        help_text=(
            "The allocation the search matched. If it belongs to a "
            "CombinedCourseGroup this is always the group's primary "
            "allocation — course_search.py resolves to that, mirroring "
            "timetable/find_courses.py's collapse-by-group rule — so the "
            "group's one shared timetable slot is what shows up, and "
            "adding any of its sections is the same action as adding the "
            "group itself."
        ),
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Personal Timetable Entry"
        verbose_name_plural = "Personal Timetable Entries"
        indexes = [
            models.Index(fields=["role", "reg_no"]),
            models.Index(fields=["role", "lecturer"]),
        ]
        ordering = ["-added_at"]

    def __str__(self):
        who = self.reg_no or (self.lecturer.name if self.lecturer_id else "?")
        return f"{who} + {self.course_allocation.course_code}"
