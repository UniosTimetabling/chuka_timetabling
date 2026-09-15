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
