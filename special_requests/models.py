from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


class SpecialRequest(models.Model):
    """
    A "SR" (Special Request) raised by a COD against a course allocation.

    The SAME model is shared by every cod panel in the system (the normal
    /cod/ panel backed by course_allocation.CourseAllocation, the ODEL cod
    panel backed by odel_system.ODELCourseAllocation, and the campuses cod
    panel backed by campuses_timetable.CampusCourseAllocation) — a generic
    (content_type, object_id) link is used instead of a hard FK so any of
    those allocation models can be attached without this app depending on
    them.

    Examples of what an SR captures:
      - "This lecturer is a delegate, should attend the Thursday meeting."
      - "This lecturer is disabled/elderly, cannot go to upstairs venues."
      - "This course is a workshop, should be scheduled at a certain time."
      - "This course should be scheduled at a certain time."
      - "This lecturer is a part-timer, avoid early morning slots."
    """

    # ── Scope: who/what the SR applies to ──────────────────────────────────
    SCOPE_UNIT            = "unit"              # this course/unit only
    SCOPE_PROGRAM         = "program"            # the whole program
    SCOPE_PROGRAM_COURSES = "program_courses"    # some courses of the program
    SCOPE_LECTURER        = "lecturer"           # a specific lecturer

    SCOPE_CHOICES = [
        (SCOPE_UNIT,            "This unit only"),
        (SCOPE_PROGRAM,         "This program"),
        (SCOPE_PROGRAM_COURSES, "Some courses of this program"),
        (SCOPE_LECTURER,        "This lecturer"),
    ]

    STATUS_OPEN         = "open"
    STATUS_ACKNOWLEDGED = "acknowledged"
    STATUS_RESOLVED      = "resolved"
    STATUS_CHOICES = [
        (STATUS_OPEN,         "Open"),
        (STATUS_ACKNOWLEDGED, "Acknowledged"),
        (STATUS_RESOLVED,     "Resolved"),
    ]

    # ── Generic link to the course allocation this SR was raised from ─────
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, null=True, blank=True,
        help_text="Model of the source allocation (CourseAllocation, ODELCourseAllocation, CampusCourseAllocation, ...).",
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    allocation = GenericForeignKey("content_type", "object_id")

    # Which cod panel this SR originated from, kept as a plain label too so
    # it survives even after the source allocation row is deleted/archived.
    PANEL_NORMAL   = "normal"
    PANEL_CAMPUSES = "campuses"
    PANEL_RESIT    = "resit"
    PANEL_ODEL     = "odel"
    PANEL_LAB      = "lab"
    PANEL_CHOICES = [
        (PANEL_NORMAL,   "COD (Regular)"),
        (PANEL_CAMPUSES, "Campus Allocation"),
        (PANEL_RESIT,    "Resit Allocation"),
        (PANEL_ODEL,     "ODEL Allocation"),
        (PANEL_LAB,      "Lab Allocation"),
    ]
    panel = models.CharField(
        max_length=20,
        choices=PANEL_CHOICES,
        default=PANEL_NORMAL,
        help_text="Which allocation panel this SR was raised from — regular /cod/, campus, resit, ODEL, or lab.",
    )

    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES, default=SCOPE_UNIT)

    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="special_requests",
    )
    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="special_requests",
    )
    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="special_requests",
    )

    # Denormalised snapshot fields so the SR still reads sensibly even after
    # the source allocation is archived/deleted.
    course_code = models.CharField(max_length=100, blank=True, default="")
    course_name = models.CharField(max_length=200, blank=True, default="")

    description = models.TextField(help_text="What should be done for this SR.")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)

    semester = models.CharField(max_length=32, blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="special_requests_created",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Archiving ───────────────────────────────────────────────────────────
    # An SR is archived (never hard-deleted) whenever its source allocation
    # is deleted or archived, so the request history is never silently lost.
    archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_reason = models.CharField(max_length=200, blank=True, default="")

    # Set when a new semester's allocation automatically carries this SR
    # forward (see special_requests.services.carry_forward_special_requests).
    carried_forward_from = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="carried_forward_to",
    )

    # ── Converted into a scheduler constraint by the director ─────────────
    # Set once the director, from the /venues/ panel, turns this SR into an
    # actual autoscheduler constraint row (LecturerBlockedSlot,
    # LecturerTimePreference, or LecturerVenuePreference). Kept as a plain
    # label + id (not an FK) so this app doesn't need to depend on core's
    # constraint models, mirroring the generic `allocation` link above.
    CONSTRAINT_LECTURER_BLOCK      = "lecturer_block"
    CONSTRAINT_LECTURER_TIME_PREF  = "lecturer_time_pref"
    CONSTRAINT_LECTURER_VENUE_PREF = "lecturer_venue_pref"
    CONSTRAINT_VENUE_BLOCK         = "venue_block"
    CONSTRAINT_VENUE_SPECIALIZATION = "venue_specialization"
    CONSTRAINT_VENUE_EXCLUSIVE     = "venue_exclusive"
    CONSTRAINT_TYPE_CHOICES = [
        (CONSTRAINT_LECTURER_BLOCK,       "Lecturer Blocked Days/Times"),
        (CONSTRAINT_LECTURER_TIME_PREF,   "Lecturer Day/Time Preference"),
        (CONSTRAINT_LECTURER_VENUE_PREF,  "Lecturer Venue Preference"),
        (CONSTRAINT_VENUE_BLOCK,          "Blocked Venue"),
        (CONSTRAINT_VENUE_SPECIALIZATION, "Venue Specialization (priority pass)"),
        (CONSTRAINT_VENUE_EXCLUSIVE,      "Exclusive Venue Restriction"),
    ]
    constraint_applied_type = models.CharField(
        max_length=30, choices=CONSTRAINT_TYPE_CHOICES, blank=True, default="",
    )
    constraint_applied_id = models.PositiveIntegerField(null=True, blank=True)
    constraint_applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["department", "archived"]),
            models.Index(fields=["lecturer"]),
        ]
        verbose_name = "Special Request (SR)"
        verbose_name_plural = "Special Requests (SR)"

    def __str__(self):
        target = self.lecturer.display_name if (self.scope == self.SCOPE_LECTURER and self.lecturer) else self.course_code
        return f"SR[{self.get_scope_display()}] {target} — {self.department.name}"

    def archive(self, reason=""):
        self.archived = True
        self.archived_at = timezone.now()
        if reason:
            self.archived_reason = reason
        self.save(update_fields=["archived", "archived_at", "archived_reason", "updated_at"])

    @property
    def affected_courses(self):
        """Course codes this SR currently covers, e.g. for 'this lecturer' or
        'this program' scoped SRs that touch many course allocations."""
        return list(
            self.allocation_links.order_by("course_code").values_list("course_code", flat=True)
        )


class SpecialRequestAllocation(models.Model):
    """
    Links ONE SpecialRequest to ONE course allocation.

    A "this unit only" SR has exactly one of these rows. A "this lecturer" /
    "this program" / "some courses of this program" SR has one row per
    affected course allocation, so the director sees every course the SR
    covers, and the COD can select all courses or pick them one by one.

    When an allocation is deleted, its link row is removed; once a
    SpecialRequest has no links left (its last course is gone), the SR
    itself is archived automatically (see special_requests.services).
    """
    special_request = models.ForeignKey(
        SpecialRequest, on_delete=models.CASCADE, related_name="allocation_links",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    allocation = GenericForeignKey("content_type", "object_id")

    # Snapshot so the course still reads sensibly even if the allocation
    # itself later changes fields (e.g. lecturer reassigned).
    course_code = models.CharField(max_length=100, blank=True, default="")
    course_name = models.CharField(max_length=200, blank=True, default="")

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [("special_request", "content_type", "object_id")]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.special_request_id} → {self.course_code}"
