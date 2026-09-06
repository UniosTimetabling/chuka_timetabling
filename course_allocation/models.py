from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class StudentGroup(models.Model):
    """
    A group within a program cohort (e.g., "BSc Computer Science — Year 1 — Group A").
    Courses in different groups of the same program/year can be scheduled concurrently.
    """
    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.CASCADE,
        related_name="student_groups",
        help_text="The program this group belongs to.",
    )
    year = models.PositiveSmallIntegerField(
        help_text="Year of study (1-6) this group belongs to.",
    )
    semester = models.PositiveSmallIntegerField(
        choices=[(1, "Semester 1"), (2, "Semester 2"), (3, "Semester 3")],
        help_text="Semester this group belongs to.",
    )
    intake = models.CharField(
        max_length=10,
        choices=[("normal", "Normal"), ("special", "Special")],
        default="normal",
        help_text="Normal or Special intake.",
    )
    name = models.CharField(
        max_length=100,
        help_text="Group name, e.g. 'Group A', 'Group B'.",
    )
    letter = models.CharField(
        max_length=4,
        help_text="Group code, e.g. 'A', 'B', 'DA', 'DB'.",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_student_groups",
        help_text="User who created this group.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Student Group"
        verbose_name_plural = "Student Groups"
        unique_together = ("program", "year", "semester", "intake", "letter")
        ordering = ["program", "year", "semester", "intake", "letter"]
        indexes = [
            models.Index(fields=["program", "year", "semester", "intake"]),
        ]

    def __str__(self):
        return f"{self.program.name} — Year {self.year} — {self.name}"

    @property
    def display_name(self):
        return f"{self.program.name} — Year {self.year} — {self.name}"

    @property
    def academic_year(self):
        """Calculate the academic year for this group."""
        from course_allocation.models import AcademicYearTracker
        ref_year = AcademicYearTracker.get_current().current_year
        return ref_year - (self.year - 1)

    def course_count(self):
        """Count of CourseAllocation records linked to this group."""
        return self.course_allocations.count()

    def save(self, *args, **kwargs):
        """Auto-generate name from letter if not provided."""
        if not self.name:
            self.name = f"Group {self.letter}"
        super().save(*args, **kwargs)

    def eligible_stems(self):
        """
        SpecializationStems this group is allowed to choose from: every stem
        under a category matching this group's program (and, if set, the
        category's year/semester) that is EITHER unrestricted (open to all
        groups) OR explicitly mapped to this group.
        """
        from django.db.models import Q
        return SpecializationStem.objects.filter(
            category__program=self.program
        ).filter(
            Q(category__year__isnull=True) | Q(category__year=self.year)
        ).filter(
            Q(category__semester__isnull=True) | Q(category__semester=self.semester)
        ).filter(
            Q(restricted_to_groups__isnull=True) | Q(restricted_to_groups=self)
        ).distinct()

    def eligible_selection_groups(self):
        """
        SelectionGroups this group is allowed to pick electives from: any
        group belonging to this program that is EITHER unrestricted (open to
        all groups) OR explicitly mapped to this group.
        """
        from django.db.models import Q
        return SelectionGroup.objects.filter(
            Q(program=self.program) | Q(program__isnull=True)
        ).filter(
            Q(restricted_to_groups__isnull=True) | Q(restricted_to_groups=self)
        ).distinct()


class GroupingTemplate(models.Model):
    """
    Knowledge base entry: "this program/year/semester/intake is always
    split into these groups (A, B, ...)". Saved independently of any
    specific StudentGroup/CourseAllocation rows so it survives an
    auto-allocate run, which wipes and rebuilds every CourseAllocation in
    the department. Auto-allocate consults this after it rebuilds
    allocations to recreate the same StudentGroup split automatically,
    instead of a COD having to redo it by hand every time.

    Normally created/updated automatically whenever a COD builds a student
    group through the usual UI (single group, bulk-year, or copy-to-years)
    — see _remember_grouping_template() in course_management/cod_panel.py.
    """
    SCOPE_ALL = "all"
    SCOPE_SELECTED = "selected"
    SCOPE_CHOICES = [
        (SCOPE_ALL, "All compulsory courses"),
        (SCOPE_SELECTED, "Selected courses only"),
    ]

    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.CASCADE,
        related_name="grouping_templates",
    )
    year = models.PositiveSmallIntegerField(
        help_text="Year of study (1-6) this template applies to.",
    )
    semester = models.PositiveSmallIntegerField(
        choices=[(1, "Semester 1"), (2, "Semester 2"), (3, "Semester 3")],
    )
    intake = models.CharField(
        max_length=10,
        choices=[("normal", "Normal"), ("special", "Special")],
        default="normal",
    )
    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES, default=SCOPE_ALL)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_grouping_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Grouping Template"
        verbose_name_plural = "Grouping Templates"
        unique_together = ("program", "year", "semester", "intake")
        ordering = ["program", "year", "semester", "intake"]

    def __str__(self):
        return f"{self.program.name} — Year {self.year} Sem {self.semester} ({self.intake}) grouping"


class GroupingTemplateGroup(models.Model):
    """One remembered group code (e.g. 'A') within a GroupingTemplate."""
    template = models.ForeignKey(
        GroupingTemplate, on_delete=models.CASCADE, related_name="groups",
    )
    letter = models.CharField(max_length=4)
    name = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        verbose_name = "Grouping Template Group"
        verbose_name_plural = "Grouping Template Groups"
        unique_together = ("template", "letter")
        ordering = ["letter"]

    def __str__(self):
        return f"{self.template} — {self.letter}"


class GroupingTemplateCourse(models.Model):
    """
    A base course code included in a GroupingTemplate whose scope is
    "selected" (rather than every compulsory course). Stored as the plain
    course code string, not a ProgramCourse FK, since ProgramCourse ids can
    be unstable across curriculum edits while the code itself is stable.
    """
    template = models.ForeignKey(
        GroupingTemplate, on_delete=models.CASCADE, related_name="course_codes",
    )
    base_course_code = models.CharField(max_length=100)

    class Meta:
        verbose_name = "Grouping Template Course"
        verbose_name_plural = "Grouping Template Courses"
        unique_together = ("template", "base_course_code")

    def __str__(self):
        return f"{self.template} — {self.base_course_code}"


class CourseAllocation(models.Model):

    # ── Intake choices ───────────────────────────────────────────────────────
    INTAKE_NORMAL  = "normal"
    INTAKE_SPECIAL = "special"
    INTAKE_CHOICES = [
        (INTAKE_NORMAL,  "Normal"),
        (INTAKE_SPECIAL, "Special"),
    ]

    course_code = models.CharField(max_length=100)
    course_name = models.CharField(max_length=200)

    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    origin_department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.SET_NULL,
        related_name="origin_allocations",
        null=True, blank=True,
        help_text="Original department offering this course.",
    )
    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.CASCADE,
        related_name="allocations",
        null=True, blank=True,
    )
    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.SET_NULL,
        related_name="course_allocations",
        null=True, blank=True,
        help_text="Assigned lecturer",
    )
    number_of_students = models.PositiveIntegerField(default=0)
    approved_by_dvc    = models.BooleanField(default=False)
    rejected_by_dvc    = models.BooleanField(default=False)
    reason_for_disapproval = models.TextField(
        blank=True, default="No reason yet",
        help_text="Provide a reason if the DVC rejects this course.",
    )
    submitted_to_tt = models.BooleanField(default=False)

    # ── ProgramCourse link — REQUIRED (non-nullable) ─────────────────────────
    program_course = models.ForeignKey(
        "program_management.ProgramCourse",
        on_delete=models.CASCADE,
        related_name="course_allocations",
        help_text=(
            "The ProgramCourse (curriculum entry) this allocation is derived "
            "from. Required — every allocation must belong to a curriculum course. "
            "Deleting the ProgramCourse will cascade-delete this allocation."
        ),
    )

    intake = models.CharField(
        max_length=10,
        choices=INTAKE_CHOICES,
        default=INTAKE_NORMAL,
        db_index=True,
        help_text=(
            "Normal = standard cohort. Special = shifted-semester cohort "
            "within the same programme year."
        ),
    )

    is_elective = models.BooleanField(
        default=False,
        help_text=(
            "Mark as a Selection / Elective course. "
            "Can be added to a SelectionGroup so students choose one from the group."
        ),
    )
    is_evening_weekend = models.BooleanField(
        default=False,
        help_text=(
            "Mark as an Evening / Weekend class. "
            "Displayed separately below the regular allocations."
        ),
    )

    # ── Primary SelectionGroup ────────────────────────────────────────────────
    selection_group = models.ForeignKey(
        "SelectionGroup",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="primary_allocations",
        help_text=(
            "Optional. The primary SelectionGroup this elective allocation "
            "belongs to. Students in the program must choose exactly one "
            "course from this group."
        ),
    )

    # ── Primary SpecializationStem ──────────────────────────────────────────
    specialization_stem = models.ForeignKey(
        "SpecializationStem",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="primary_allocations",
        help_text=(
            "Optional. The SpecializationStem (e.g. 'Artificial Intelligence', "
            "'Cybersecurity') this course belongs to. Students choose exactly "
            "one stem out of the SpecializationCategory and take EVERY course "
            "inside that stem. Unlike a SelectionGroup, courses inside the "
            "SAME stem must still be checked for clashes against each other; "
            "only courses that belong to DIFFERENT stems of the same category "
            "are exempt from clashing with one another."
        ),
    )

    # ── Special Intake Group ──────────────────────────────────────────────────
    special_intake_group = models.ForeignKey(
        "SpecialIntakeGroup",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="course_allocations",
        help_text="If this is a special intake allocation, link to the group it belongs to."
    )

    # ── Student Group ──────────────────────────────────────────────────────────
    student_group = models.ForeignKey(
        "StudentGroup",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="course_allocations",
        help_text=(
            "The student group this allocation belongs to. If null, this course "
            "is shared across all groups in the program/year (typically electives "
            "or specialization-stem courses)."
        ),
    )

    def status_label(self):
        if self.submitted_to_tt:
            return "Submitted to Timetable"
        if self.approved_by_dvc:
            return "Approved by DVC"
        if self.rejected_by_dvc:
            return f"Rejected by DVC - {self.reason_for_disapproval}"
        return "Pending DVC Decision"

    @property
    def is_special_intake(self):
        return self.intake == self.INTAKE_SPECIAL

    def __str__(self):
        elective_tag   = " [Elective]"        if self.is_elective        else ""
        evening_tag    = " [Evening/Weekend]"  if self.is_evening_weekend else ""
        intake_tag     = " [Special Intake]"   if self.is_special_intake  else ""
        sg_tag         = f" [Group: {self.selection_group.name}]" if self.selection_group else ""
        stem_tag       = f" [Stem: {self.specialization_stem.name}]" if self.specialization_stem else ""
        student_group_tag = f" [Student Group: {self.student_group.name}]" if self.student_group else ""
        base = self.course_code + elective_tag + evening_tag + intake_tag + sg_tag + stem_tag + student_group_tag
        suffix = self.lecturer.display_name if self.lecturer else "Unassigned"
        return f"{base} - {suffix}"

    @classmethod
    def get_or_create_shared(cls, program_course, department, defaults=None):
        """
        Fetch (or create) the SHARED CourseAllocation row for a given
        ProgramCourse + department — i.e. the row with student_group=NULL
        that applies across every cohort/group rather than one specific
        Student Group.

        Since the Student Group (cohort-splitting) feature clones one
        CourseAllocation row PER GROUP for the same ProgramCourse (see
        course_management/cod_panel.py `_ensure_group_allocation`), a plain
        ``CourseAllocation.objects.get_or_create(program_course=pc,
        department=dept)`` is no longer safe once a course has been split
        across groups — it can match more than one row (one per group) and
        raise MultipleObjectsReturned. Callers that need the course-wide
        row (electives, Selection Groups, Specialization Stems — all of
        which are shared-by-construction per `clean()` above) must go
        through this method instead.

        Returns (allocation, created) exactly like get_or_create.
        """
        existing = cls.objects.filter(
            program_course=program_course,
            department=department,
            student_group__isnull=True,
        ).order_by("id")

        ca = existing.first()
        if ca:
            if existing.count() > 1:
                import logging
                logging.getLogger(__name__).warning(
                    "Multiple shared CourseAllocation rows (student_group=NULL) "
                    "found for program_course=%s department=%s — using the "
                    "oldest (id=%s). The extras should be reviewed/merged.",
                    program_course.id, department.id, ca.id,
                )
            return ca, False

        create_kwargs = {
            "program_course": program_course,
            "department": department,
            "student_group": None,
            "program": program_course.program,
            "course_code": program_course.course_code,
            "course_name": program_course.course_name,
        }
        if defaults:
            create_kwargs.update(defaults)
        ca = cls.objects.create(**create_kwargs)
        return ca, True

    def clean(self):
        from django.core.exceptions import ValidationError

        if not self.program_course_id:
            raise ValidationError(
                "A ProgramCourse must be selected. "
                "Every course allocation must link to a curriculum entry."
            )

        if self.approved_by_dvc and self.rejected_by_dvc:
            raise ValidationError("A course allocation cannot be both approved and rejected.")
        if self.rejected_by_dvc and not self.reason_for_disapproval.strip():
            raise ValidationError("Please provide a reason for disapproval.")
        if self.approved_by_dvc:
            self.reason_for_disapproval = "No reason yet"

        qs = CourseAllocation.objects.filter(
            program=self.program,
            course_code__iexact=self.course_code,
            intake=self.intake,
            student_group=self.student_group,
        )
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        if qs.exists():
            if self.student_group:
                raise ValidationError(
                    f"This course code is already allocated to that program, intake, "
                    f"and student group ({self.student_group.name})."
                )
            raise ValidationError(
                "This course code is already allocated to that program for the same intake."
            )

        if self.selection_group and not self.is_elective:
            raise ValidationError(
                "A course must be marked as Elective before it can be assigned to a Selection Group."
            )

        if self.selection_group and self.specialization_stem:
            raise ValidationError(
                "A course cannot belong to both a Selection Group (pick-one, "
                "mutually exclusive courses) and a Specialization Stem "
                "(pick-one-stem, take-all-courses-in-stem) at the same time — "
                "these two groupings have opposite scheduling semantics."
            )

        # ── Student Group validation ──────────────────────────────────────────
        # A course cannot belong to a student group if it's an elective or
        # part of a specialization stem — those are shared by construction.
        if self.student_group and (self.is_elective or self.specialization_stem_id):
            raise ValidationError(
                "Elective courses and specialization-stem courses are shared across "
                "all student groups and cannot be assigned to a specific group."
            )

        # If student_group is set, the intake must match the group's intake
        if self.student_group and self.intake != self.student_group.intake:
            raise ValidationError(
                f"The intake type ({self.intake}) must match the student group's "
                f"intake type ({self.student_group.intake})."
            )

        # If student_group is set, the program must match the group's program
        if self.student_group and self.program_id != self.student_group.program_id:
            raise ValidationError(
                f"The program ({self.program.name}) must match the student group's "
                f"program ({self.student_group.program.name})."
            )

    class Meta:
        permissions = [
            ("approve_course_allocation", "Can approve course allocation (DVC only)"),
            ("forward_course_allocation", "Can forward course allocation to timetable (COD only)"),
        ]
        indexes = [
            models.Index(fields=['course_code']),
            models.Index(fields=['program', 'course_code']),
            models.Index(fields=['intake']),
            models.Index(fields=['special_intake_group']),
            models.Index(fields=['student_group']),
        ]


class SpecialIntakeGroup(models.Model):
    """
    Tracks special intake cohorts grouped by program, year, and semester.
    Similar to how normal program year tracking works for enrollment.
    """
    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.CASCADE,
        related_name="special_intake_groups",
    )
    year = models.PositiveSmallIntegerField(
        help_text="Year of study (1-6) this special intake cohort is in."
    )
    semester = models.PositiveSmallIntegerField(
        choices=[(1, "Semester 1"), (2, "Semester 2"), (3, "Semester 3")],
        help_text=(
            "The cohort's own semester label (e.g. self-sponsored students may sit in "
            "Semester 3 while others in the same programme/year are in Semester 2). "
            "This does NOT restrict which curriculum semester's courses can be pulled "
            "into the group — that is chosen explicitly when pulling courses."
        )
    )
    entry_year = models.PositiveIntegerField(
        help_text="Calendar year this special intake cohort was admitted."
    )
    number_of_students = models.PositiveIntegerField(
        default=0,
        help_text="Total number of students in this special intake cohort."
    )
    academic_year = models.CharField(
        max_length=20,
        blank=True,
        help_text="e.g. '2024/2025' - auto-generated from entry_year."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Special Intake Group"
        verbose_name_plural = "Special Intake Groups"
        unique_together = ("program", "year", "semester", "entry_year")
        ordering = ["program", "-entry_year", "year", "semester"]

    def __str__(self):
        return f"{self.program.name} - Year {self.year} Sem {self.semester} (Special Intake {self.entry_year})"

    def save(self, *args, **kwargs):
        if not self.academic_year:
            self.academic_year = f"{self.entry_year}/{self.entry_year + 1}"
        super().save(*args, **kwargs)

    @property
    def total_courses(self):
        """Count of CourseAllocation records linked to this group."""
        return self.course_allocations.count()

    @property
    def total_electives(self):
        """Count of elective CourseAllocation records linked to this group."""
        return self.course_allocations.filter(is_elective=True).count()


class SelectionGroup(models.Model):
    """
    A named group of elective CourseAllocations from which students must
    choose exactly ONE course.
    """
    name = models.CharField(
        max_length=200,
        help_text="Descriptive name, e.g. 'Year 3 Semester 1 Electives – Group A'.",
    )
    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="selection_groups",
    )
    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="selection_groups",
        help_text="Optional: restrict this group to a specific program.",
    )
    courses = models.ManyToManyField(
        "CourseAllocation",
        blank=True,
        related_name="selection_groups",
        limit_choices_to={"is_elective": True},
        help_text="Only elective CourseAllocations may be added here.",
    )
    restricted_to_groups = models.ManyToManyField(
        "StudentGroup",
        blank=True,
        related_name="available_selection_groups",
        help_text=(
            "Optional: restrict this elective choice to specific Student "
            "Groups (e.g. only Group C may choose from this pool). Leave "
            "empty to keep it shared/open to every group in the "
            "program/year/semester, same as before this field existed."
        ),
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_selection_groups",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name         = "Selection Group"
        verbose_name_plural  = "Selection Groups"
        ordering             = ["department", "name"]

    def __str__(self):
        return f"{self.name} ({self.department.name})"

    def course_count(self):
        return self.courses.count()

    @property
    def is_restricted(self):
        return self.restricted_to_groups.exists()


class SpecializationCategory(models.Model):
    """
    A choice-point (specialization/track slot) for a Program at which
    students choose exactly ONE SpecializationStem out of several offered.

    Example: "BSc Computer Science - Year 3 Semester 1 Specialization" with
    stems "Artificial Intelligence", "Cybersecurity", "Networking" — each
    stem carrying its own set of (e.g. 4) courses.

    Scheduling semantics (for the autoscheduler; NOT enforced by this app —
    the models/views here only capture the data, wiring into the scheduling
    algorithm is a separate follow-up):
      * Courses belonging to DIFFERENT stems under the SAME category MAY be
        scheduled at the same time slot — a student follows only one stem,
        so they never take courses from two different stems at once.
      * Courses belonging to the SAME stem must NEVER be scheduled at the
        same time slot — a student who picks that stem takes ALL of its
        courses together, same as ordinary mandatory courses.
    """
    name = models.CharField(
        max_length=200,
        help_text="Descriptive name, e.g. 'Year 3 Semester 1 Specialization'.",
    )
    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="specialization_categories",
    )
    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.CASCADE,
        related_name="specialization_categories",
        help_text="The program these specialization stems belong to.",
    )
    year = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Optional: the program year this specialization applies to (e.g. Year 3).",
    )
    semester = models.PositiveSmallIntegerField(
        choices=[(1, "Semester 1"), (2, "Semester 2")],
        null=True, blank=True,
        help_text="Optional: the semester this specialization applies to.",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_specialization_categories",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name         = "Specialization Category"
        verbose_name_plural  = "Specialization Categories"
        ordering             = ["department", "program", "year", "semester", "name"]
        unique_together      = ("program", "name")

    def __str__(self):
        yr = f" Y{self.year}" if self.year else ""
        sem = f"S{self.semester}" if self.semester else ""
        suffix = f" ({yr.strip()} {sem})".strip() if (yr or sem) else ""
        return f"{self.name} — {self.program.name}{suffix}"

    def stem_count(self):
        return self.stems.count()


class SpecializationStem(models.Model):
    """
    One option (e.g. 'Artificial Intelligence') within a
    SpecializationCategory. A student who picks this stem takes EVERY
    course listed in it, so courses within a single stem must be treated
    like ordinary mandatory courses for clash-checking purposes. Courses
    belonging to a different stem of the same category may safely be
    scheduled at the same time.
    """
    category = models.ForeignKey(
        SpecializationCategory,
        on_delete=models.CASCADE,
        related_name="stems",
    )
    name = models.CharField(
        max_length=200,
        help_text="e.g. 'Artificial Intelligence', 'Cybersecurity', 'Networking'.",
    )
    courses = models.ManyToManyField(
        "CourseAllocation",
        blank=True,
        related_name="specialization_stems",
        help_text="The set of courses a student takes if they choose this stem.",
    )
    restricted_to_groups = models.ManyToManyField(
        "StudentGroup",
        blank=True,
        related_name="available_stems",
        help_text=(
            "Optional: restrict this stem to specific Student Groups (e.g. "
            "only Group A and Group B may choose this stem, while Group C "
            "may not). Leave empty to keep it shared/open to every group in "
            "the program/year/semester, same as before this field existed."
        ),
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_specialization_stems",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name         = "Specialization Stem"
        verbose_name_plural  = "Specialization Stems"
        ordering             = ["category", "name"]
        unique_together      = ("category", "name")

    def __str__(self):
        return f"{self.name} ({self.category.name})"

    def course_count(self):
        return self.courses.count()

    @property
    def is_restricted(self):
        return self.restricted_to_groups.exists()

    @property
    def department(self):
        return self.category.department

    @property
    def program(self):
        return self.category.program


class BaseSelection(models.Model):
    """
    Repository of 'base' elective/selection courses that should be
    considered by autoschedulers and presented as recommendations when
    building SelectionGroups. Each entry maps a ProgramCourse to a
    department (the owning department that maintains the selection list).
    """
    program_course = models.ForeignKey(
        "program_management.ProgramCourse",
        on_delete=models.CASCADE,
        related_name="base_selections",
        help_text="ProgramCourse that represents this base selection.",
    )
    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="base_selections",
        help_text="Department that owns this base selection list.",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_base_selections",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Base Selection Course"
        verbose_name_plural = "Base Selection Courses"
        unique_together = ("program_course", "department")

    def __str__(self):
        return f"{self.program_course.course_code} - {self.program_course.course_name} ({self.department.name})"


class AllocationConfig(models.Model):
    """
    Configurable knobs for the autoallocator.

    Resolution order: per-department row -> GLOBAL row (department=None) -> fallback.
    """
    department = models.OneToOneField(
        "department_management.Department",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="allocation_config",
        help_text="Leave blank for the GLOBAL default config (exactly one such row).",
    )

    # ── Lecturer load cap ─────────────────────────────────────────────────────
    max_load_per_semester = models.PositiveSmallIntegerField(
        default=6,
        help_text="Default max CourseAllocations a lecturer may carry PER SEMESTER.",
    )
    max_load_enabled = models.BooleanField(
        default=True,
        help_text="If False, the load cap is disabled entirely.",
    )

    # ── Large-class splitting ─────────────────────────────────────────────────
    split_threshold = models.PositiveIntegerField(
        default=200,
        help_text="Maximum number of students allowed per group (hard limit). "
                   "If students exceed threshold + extend_by, the course is split "
                   "into the minimum number of groups where no group exceeds threshold.",
    )
    split_extend_by = models.PositiveIntegerField(
        default=0,
        help_text="How much the threshold can be exceeded before triggering a split. "
                   "e.g., threshold=200, extend_by=20 → groups up to 220 students are allowed. "
                   "A 220-student course → 1 group (220 ≤ 220). "
                   "A 221-student course → 2 groups (ceil(221/200) = 2 → 110 + 111).",
    )
    splitting_enabled = models.BooleanField(
        default=True,
        help_text="If False, courses are never auto-split.",
    )

    # ── Postgraduate designation rule ─────────────────────────────────────────
    pg_designations = models.CharField(
        max_length=200,
        default="Dr,Prof",
        help_text="Comma-separated Lecturer.designation codes considered qualified "
                   "to teach postgraduate-level courses.",
    )

    # ── Academic calendar ──────────────────────────────────────────────────────
    allowed_semesters = models.CharField(
        max_length=100,
        default="1,2",
        help_text="Comma-separated valid semester numbers.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Allocation Config"
        verbose_name_plural = "Allocation Configs"

    def __str__(self):
        return f"AllocationConfig({self.department.name if self.department else 'GLOBAL'})"

    def pg_designation_list(self):
        return [d.strip() for d in self.pg_designations.split(",") if d.strip()]

    def allowed_semester_list(self):
        return [int(s.strip()) for s in self.allowed_semesters.split(",") if s.strip()]


class LecturerCourseMapping(models.Model):
    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.CASCADE,
        related_name="course_mappings",
        help_text="Lecturer who can teach these courses.",
    )
    courses = models.ManyToManyField(
        "program_management.ProgramCourse",
        related_name="lecturer_mappings",
        blank=True,
        help_text="Courses this lecturer is qualified to teach.",
    )
    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="lecturer_course_mappings",
        help_text="Department context for this mapping.",
    )
    notes      = models.TextField(blank=True, default="", help_text="Optional notes.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Lecturer Course Mapping"
        verbose_name_plural = "Lecturer Course Mappings"
        unique_together     = ("lecturer", "department")

    def __str__(self):
        dept = self.department.name if self.department else "No Dept"
        return f"{self.lecturer.display_name} ({dept})"


class AcademicYearTracker(models.Model):
    """
    Single global "clock" for the whole institution.
    """
    current_year = models.PositiveIntegerField(
        help_text="Calendar year that currently counts as 'Year 1' of study.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Academic Year Tracker"
        verbose_name_plural = "Academic Year Tracker"

    def __str__(self):
        return f"Current reference year: {self.current_year}"

    @classmethod
    def get_current(cls):
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create(current_year=timezone.now().year)
        return obj

    @classmethod
    def set_current_year(cls, year):
        obj = cls.get_current()
        obj.current_year = year
        obj.save(update_fields=["current_year", "updated_at"])
        return obj


class ProgramEnrollment(models.Model):
    """
    ONE row per program per intake cohort.
    """
    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    entry_year = models.PositiveIntegerField(
        help_text="Calendar year this cohort was admitted.",
    )
    number_of_students = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name        = "Program Enrollment"
        verbose_name_plural = "Program Enrollments"
        unique_together     = ("program", "entry_year")
        ordering            = ("program__name", "-entry_year")

    def __str__(self):
        return (
            f"{self.program.name} (entry {self.entry_year}) "
            f"- {self.number_of_students} students"
        )

    def current_study_year(self, reference_year=None):
        if reference_year is None:
            reference_year = AcademicYearTracker.get_current().current_year
        return reference_year - self.entry_year + 1


class LabAllocation(models.Model):
    """
    A lab / workshop allocation linking one or more program courses to one or
    more lab venues.
    """
    program_course = models.ForeignKey(
        "program_management.ProgramCourse",
        on_delete=models.CASCADE,
        related_name="lab_allocations",
        help_text="Primary / first course for this allocation.",
    )
    additional_courses = models.ManyToManyField(
        "program_management.ProgramCourse",
        blank=True,
        related_name="additional_lab_allocations",
        help_text="Extra course codes that share this lab allocation.",
    )
    venues = models.ManyToManyField(
        "room_management.LabVenue",
        related_name="lab_allocations",
        help_text="Candidate venues; the autoscheduler picks one per session.",
    )
    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    number_of_students = models.PositiveIntegerField(default=0)
    is_workshop_course = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def course_code(self):
        return self.program_course.course_code

    @property
    def course_name(self):
        return self.program_course.course_name

    @property
    def program(self):
        return self.program_course.program

    @property
    def department(self):
        return self.program_course.program.department if self.program_course.program else None

    def all_course_codes(self):
        codes = [self.program_course.course_code]
        codes += list(self.additional_courses.values_list("course_code", flat=True))
        return ", ".join(codes)

    def __str__(self):
        workshop_tag = " [Workshop]" if self.is_workshop_course else ""
        venue_codes = ", ".join(self.venues.values_list("code", flat=True)) or "No venue"
        return f"{self.all_course_codes()}{workshop_tag} → {venue_codes}"


class SubmissionControl(models.Model):
    department = models.OneToOneField(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="submission_control",
        null=True, blank=True,
    )
    allow_submission_to_dvc = models.BooleanField(default=True)
    allow_submission_to_tt  = models.BooleanField(default=True)
    updated_at              = models.DateTimeField(auto_now=True)

    def __str__(self):
        dept_name = self.department.name if self.department else "GLOBAL (legacy)"
        return f"{dept_name} -> DVC: {self.allow_submission_to_dvc}, TT: {self.allow_submission_to_tt}"

    class Meta:
        verbose_name        = "Submission Control"
        verbose_name_plural = "Submission Controls"


class ArchivedCourseAllocation(models.Model):
    """Archived copy of CourseAllocation saved before auto-allocation recreates entries."""
    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="archived_allocations",
    )
    semester    = models.CharField(max_length=32, help_text="Semester identifier")
    archived_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="archived_by_user",
    )
    archived_at = models.DateTimeField(default=timezone.now)

    course_code  = models.CharField(max_length=100)
    course_name  = models.CharField(max_length=200)
    origin_department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.SET_NULL,
        related_name="archived_origin_allocations",
        null=True, blank=True,
    )
    program  = models.ForeignKey("program_management.Program", on_delete=models.SET_NULL, null=True, blank=True)
    program_course = models.ForeignKey(
        "program_management.ProgramCourse",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="archived_course_allocations",
    )
    lecturer = models.ForeignKey("lecturer_portal.Lecturer", on_delete=models.SET_NULL, null=True, blank=True)
    number_of_students = models.PositiveIntegerField(default=0)
    approved_by_dvc    = models.BooleanField(default=False)
    rejected_by_dvc    = models.BooleanField(default=False)
    reason_for_disapproval = models.TextField(blank=True, default="No reason yet")
    submitted_to_tt        = models.BooleanField(default=False)
    intake = models.CharField(
        max_length=10,
        choices=CourseAllocation.INTAKE_CHOICES,
        default=CourseAllocation.INTAKE_NORMAL,
    )
    combination_name   = models.CharField(max_length=200, blank=True, default="")
    selection_group_name = models.CharField(max_length=200, blank=True, default="")
    student_group_name = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        verbose_name        = "Archived Course Allocation"
        verbose_name_plural = "Archived Course Allocations"
        ordering            = ["-archived_at"]

    def __str__(self):
        intake_tag = " [Special]" if self.intake == CourseAllocation.INTAKE_SPECIAL else ""
        return f"[{self.semester}] {self.course_code}{intake_tag} - {self.department.name}"


class CombinedCourseGroup(models.Model):
    """
    Groups multiple CourseAllocation records that are taught together.
    """
    group_code = models.CharField(max_length=50, unique=True)
    base_course_code = models.CharField(max_length=100)
    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="combined_groups",
    )
    allocations = models.ManyToManyField(
        "CourseAllocation",
        related_name="combined_groups",
    )
    primary_allocation = models.ForeignKey(
        "CourseAllocation",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="is_primary_of_combined_group",
    )
    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="combined_groups",
        help_text="Allocating department — currently responsible for assigning a lecturer to this combined group.",
    )
    origin_department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="origin_combined_groups",
        help_text="Servicing/originating department — the COD who first combined this group. Kept fixed even after the group is submitted to another department for lecturer allocation.",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_combined_groups",
    )
    split_from_group_code = models.CharField(
        max_length=50, blank=True, default="",
        help_text="If this group was produced by splitting a submitted combined group, the group_code of that parent (the parent row itself is deleted once split, so this is a historical snapshot, not a live FK).",
    )
    split_from_group_id = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Historical id of the parent group that was split to produce this one (not a live FK — the parent row no longer exists).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Combined Course Group"
        verbose_name_plural = "Combined Course Groups"
        ordering = ["base_course_code", "-created_at"]

    def __str__(self):
        return f"{self.base_course_code} Combined ({self.allocations.count()} allocations, {self.lecturer})"

    def total_students(self):
        return sum(a.number_of_students for a in self.allocations.all())

    def display_name(self):
        """
        Single canonical name this group should ALWAYS be shown as, everywhere
        (autoscheduler logs, timetable panel, unscheduled list, Find Courses).
        Individual member course codes (e.g. 'MATH 221', 'MATH 221 B',
        'MATH 221 C') must never surface on their own once they're grouped —
        only this name should. Which member course codes/program-years make
        up the group is carried separately as "identifiers" (see
        timetable_panel._combined_group_identifiers), not folded into the name.

        FIX: this used to always fabricate "{base_course_code} Combined" and
        silently discard group_code — which is the REAL name given at
        creation time (cod_panel.create_combined_group's `group_name` form
        field, e.g. "PHYS 342-A"). That meant the name typed by the COD was
        never actually shown anywhere; the panel/scheduler/logs all showed a
        generic derived label instead. group_code is a required, unique
        field on every CombinedCourseGroup (set explicitly on manual
        creation, or auto-generated on split/template paths), so it is
        always the right thing to show — use it directly.
        """
        return self.group_code.strip() if self.group_code else f"{self.base_course_code.strip()} Combined"


class CourseCombinationTemplate(models.Model):
    """
    Knowledge base entry: "these course codes should always be combined
    into one taught session", independent of any specific CourseAllocation
    rows (auto-allocate wipes and rebuilds every CourseAllocation in the
    department on each run, which would otherwise silently un-combine
    everything). Auto-allocate consults this after it rebuilds allocations
    to recreate the same CombinedCourseGroup automatically.

    Normally created/updated automatically whenever a COD combines
    allocations by hand (single combine or smart combine) — see
    _remember_course_combination_template() in
    course_management/cod_panel.py.
    """
    base_course_code = models.CharField(
        max_length=100,
        help_text="Normalized base course code shared by every allocation in this combination, e.g. 'COSC 101'.",
    )
    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="course_combination_templates",
    )
    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="course_combination_templates",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_course_combination_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Course Combination Template"
        verbose_name_plural = "Course Combination Templates"
        unique_together = ("department", "base_course_code")
        ordering = ["department", "base_course_code"]

    def __str__(self):
        return f"{self.department.name} — {self.base_course_code} combination"


class CourseCombinationTemplateProgram(models.Model):
    """
    A program whose section of the base course should be pulled into this
    combination when it's replayed. Recorded so replaying the template
    doesn't accidentally sweep in a program that wasn't part of the
    original combine (e.g. a 3rd program that happens to share the same
    course code but was deliberately left out).
    """
    template = models.ForeignKey(
        CourseCombinationTemplate, on_delete=models.CASCADE, related_name="programs",
    )
    program = models.ForeignKey(
        "program_management.Program",
        on_delete=models.CASCADE,
        related_name="course_combination_template_links",
    )

    class Meta:
        verbose_name = "Course Combination Template Program"
        verbose_name_plural = "Course Combination Template Programs"
        unique_together = ("template", "program")

    def __str__(self):
        return f"{self.template} — {self.program.name}"


class DVCActionLog(models.Model):
    ACTION_SUBMIT  = "submit"
    ACTION_APPROVE = "approve"
    ACTION_REJECT  = "reject"
    ACTION_CHOICES = [
        (ACTION_SUBMIT,  "COD Submitted"),
        (ACTION_APPROVE, "DVC Approved"),
        (ACTION_REJECT,  "DVC Rejected"),
    ]

    ALLOC_STANDARD = "standard"
    ALLOC_CAMPUS   = "campus"
    ALLOC_ODEL     = "odel"
    ALLOC_TYPE_CHOICES = [
        (ALLOC_STANDARD, "Standard"),
        (ALLOC_CAMPUS,   "Campus"),
        (ALLOC_ODEL,     "ODEL"),
    ]

    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="dvc_action_logs",
        null=True, blank=True,
    )
    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="dvc_action_logs",
    )
    alloc_type  = models.CharField(max_length=10, choices=ALLOC_TYPE_CHOICES, default=ALLOC_STANDARD)
    action      = models.CharField(max_length=10, choices=ACTION_CHOICES)
    actor       = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    course_code = models.CharField(max_length=100, blank=True, default="")
    course_name = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    reported = models.BooleanField(default=False)

    class Meta:
        verbose_name = "DVC Action Log"
        verbose_name_plural = "DVC Action Logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["department", "reported"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        dept = self.department.name if self.department else "N/A"
        return f"[{dept}] {self.get_action_display()} — {self.course_code} @ {self.created_at:%Y-%m-%d %H:%M}"