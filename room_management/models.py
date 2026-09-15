from django.db import models


class Building(models.Model):
    """
    A building that belongs to a faculty, e.g. Science Block, Arts Complex.
    """
    name = models.CharField(max_length=150, unique=True)
    code = models.CharField(max_length=50, unique=True)  # e.g. BSL, SRP, FTC
    faculty = models.ForeignKey(
        'faculty_management.Faculty',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="buildings",
        help_text="Faculty this building belongs to (if any).",
    )
    description = models.TextField(blank=True, null=True)
    is_workshop = models.BooleanField(
        default=False,
        help_text="Designates whether this building is a workshop facility.",
    )

    def __str__(self):
        fac = f" - {self.faculty.name}" if self.faculty else ""
        workshop = " [Workshop]" if self.is_workshop else ""
        return f"{self.name} ({self.code}){fac}{workshop}"


class Venue(models.Model):
    """
    A specific room or hall within a building, e.g. BSL 303 or SRP B03.
    """
    code = models.CharField(max_length=50, unique=True)  # e.g. BSL 303
    building = models.ForeignKey(
        Building,
        on_delete=models.SET_NULL,  # don't delete the venue if the building is deleted
        null=True,
        blank=True,
        default=None,
        related_name="venues",
        help_text="Building this venue belongs to (if any).",
    )
    capacity = models.PositiveIntegerField(null=True, blank=True)
    exam_capacity = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Capacity specifically for exam seating arrangements",
    )
    description = models.TextField(blank=True, null=True)
    is_workshop = models.BooleanField(
        default=False,
        help_text="Designates whether this venue is a workshop room.",
    )

    # ── Specialization flag ────────────────────────────────────────────────
    is_specialized = models.BooleanField(
        default=False,
        help_text=(
            "When True, this venue is reserved for courses/programs assigned "
            "via VenueSpecialization rules. The scheduler will fill these rooms "
            "with their designated courses first; other courses may only use the "
            "venue once all its designated courses have been scheduled."
        ),
    )

    def __str__(self):
        cap = self.capacity if self.capacity is not None else "Unknown capacity"
        exam_cap = f", Exam: {self.exam_capacity}" if self.exam_capacity is not None else ""
        bld = f" - {self.building.name}" if self.building else ""
        workshop = " [Workshop]" if self.is_workshop else ""
        specialized = " [Specialized]" if self.is_specialized else ""
        return f"{self.code} ({cap}{exam_cap}){bld}{workshop}{specialized}"

    @property
    def specialization_rules(self):
        """Return all active specialization rules attached to this venue."""
        return self.specializations.all()

    def get_designated_course_codes(self):
        """
        Return a flat set of all course codes (normalised, upper-case) that are
        designated to this venue via its specialization rules.
        Includes courses from: department rules, program rules, and explicit
        individual-course rules.
        """
        codes = set()
        for rule in self.specializations.prefetch_related(
            'programs__courses', 'departments__programs__courses', 'courses'
        ):
            # Explicit individual courses
            for c in rule.courses.all():
                codes.add(c.course_code.strip().upper())

            # Program-level designation
            for prog in rule.programs.all():
                for pc in prog.courses.all():
                    codes.add(pc.course_code.strip().upper())

            # Department-level designation
            for dept in rule.departments.all():
                for prog in dept.programs.all():
                    for pc in prog.courses.all():
                        codes.add(pc.course_code.strip().upper())

        return codes


class VenueSpecialization(models.Model):
    """
    Declares that one or more venues are *preferred / reserved* for a specific
    set of courses, programs, or entire departments.

    The scheduler uses this table in its priority pass (before any general
    assignment) to place designated courses into their preferred venue(s).
    A venue may have many specialization rules; a single rule may designate
    many venues at once (bulk-flag workflow).

    Scope hierarchy (additive – all matching rules are collected):
        department  → all courses of all programs in that department
        programs    → all courses within the selected programs
        courses     → individual ProgramCourse entries (finest grain)

    The ``priority`` field lets admins order rules when a course matches
    more than one specialization (lower number = higher priority).
    """

    SCOPE_CHOICES = [
        ('department', 'Department (all programs & courses)'),
        ('program',    'Program (all courses in program)'),
        ('course',     'Individual Courses'),
        ('mixed',      'Mixed (programs + individual courses)'),
    ]

    name = models.CharField(
        max_length=200,
        help_text="A human-readable label for this specialization rule, e.g. 'CS Lab rooms — Year 3'.",
    )
    venues = models.ManyToManyField(
        Venue,
        related_name='specializations',
        help_text="One or more venues reserved for the designated courses.",
    )
    scope = models.CharField(
        max_length=20,
        choices=SCOPE_CHOICES,
        default='course',
        help_text="Broadest targeting level for this rule.",
    )

    # ── Targeting relations ────────────────────────────────────────────────
    departments = models.ManyToManyField(
        'department_management.Department',
        blank=True,
        related_name='venue_specializations',
        help_text="Courses from ALL programs in these departments will be designated.",
    )
    programs = models.ManyToManyField(
        'program_management.Program',
        blank=True,
        related_name='venue_specializations',
        help_text="All courses within these programs will be designated.",
    )
    courses = models.ManyToManyField(
        'program_management.ProgramCourse',
        blank=True,
        related_name='venue_specializations',
        help_text="Specific individual courses designated to the selected venues.",
    )

    # ── Behaviour flags ────────────────────────────────────────────────────
    strict = models.BooleanField(
        default=False,
        help_text=(
            "If True, designated courses MUST be placed in one of the listed venues "
            "(they will be marked unschedulable if no designated venue is free). "
            "If False (soft reservation), designated courses are placed in these "
            "venues first but may fall back to any venue when all designated venues "
            "are occupied."
        ),
    )
    exclusive = models.BooleanField(
        default=False,
        help_text=(
            "If True, the listed venues are RESERVED ONLY for the designated "
            "courses/programs/departments — no other course may ever be placed "
            "there, even if the designated courses don't fill every timeslot. "
            "If False (default), any free timeslots left in the venue after the "
            "designated courses are scheduled may be given to other courses "
            "(this is the original 'priority pass' behaviour)."
        ),
    )
    priority = models.PositiveSmallIntegerField(
        default=10,
        help_text="Lower number = higher priority when multiple rules match a course.",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['priority', 'name']
        verbose_name = 'Venue Specialization'
        verbose_name_plural = 'Venue Specializations'

    def __str__(self):
        venue_list = ", ".join(v.code for v in self.venues.all()[:3])
        if self.venues.count() > 3:
            venue_list += f" +{self.venues.count() - 3} more"
        strict_tag = " [STRICT]" if self.strict else ""
        return f"{self.name} → [{venue_list}]{strict_tag} (priority {self.priority})"

    def get_designated_course_codes(self):
        """
        Collect the full set of normalised upper-case course codes covered by
        this single specialization rule.
        """
        codes = set()

        for c in self.courses.all():
            codes.add(c.course_code.strip().upper())

        for prog in self.programs.all():
            for pc in prog.courses.all():
                codes.add(pc.course_code.strip().upper())

        for dept in self.departments.all():
            for prog in dept.programs.all():
                for pc in prog.courses.all():
                    codes.add(pc.course_code.strip().upper())

        return codes


class VenueBlock(models.Model):
    """
    Hard-blocks a venue from autoscheduler use entirely — the room simply
    never appears in the candidate pool for any course, regardless of
    specialization rules. This is different from VenueSpecialization:
    a specialization *reserves* a room for certain courses (others can still
    use it once free); a VenueBlock removes the room from consideration
    completely (e.g. it's under renovation, reserved for another department's
    exclusive manual use, or otherwise off-limits to the autoscheduler).

    Toggled off (is_active=False) or disabled for a single run via the
    autoscheduler's pre-run confirmation screen without deleting the rule.
    """
    venue = models.OneToOneField(
        Venue,
        on_delete=models.CASCADE,
        related_name="block_rule",
        help_text="Venue to hide from the autoscheduler entirely.",
    )
    reason = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Why this venue is blocked, e.g. 'Under renovation', 'Reserved for Senate sittings'.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck to temporarily lift the block without deleting it.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Venue Block"
        verbose_name_plural = "Venue Blocks"
        ordering = ["venue__code"]

    def __str__(self):
        status = "ACTIVE" if self.is_active else "inactive"
        reason = f" — {self.reason}" if self.reason else ""
        return f"BLOCKED: {self.venue.code}{reason} [{status}]"


class LabVenue(models.Model):
    """
    Represents a laboratory venue (separate from normal lecture venues).
    """
    code = models.CharField(max_length=50, unique=True)  # e.g. CS LAB 1
    capacity = models.PositiveIntegerField(null=True, blank=True)
    description = models.TextField(blank=True, null=True)
    equipment = models.TextField(blank=True, null=True, help_text="List of lab equipment or resources")

    def __str__(self):
        cap = self.capacity if self.capacity is not None else "Unknown capacity"
        return f"{self.code} ({cap} seats)"