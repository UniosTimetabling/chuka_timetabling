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


class GroupingTemplateStemAssignment(models.Model):
    """
    Pins one remembered lettered course-group section (a GroupingTemplateGroup,
    e.g. "Group A") to one SpecializationStem ("Combination Stem"), for the
    case where a course shared across a whole program/year (e.g. EDFO 111) is
    split into several parallel sections and DIFFERENT combination stems each
    take a DIFFERENT section of it — e.g. the "English/Literature" stem takes
    EDFO 111-A while the "History/CRE" stem takes EDFO 111-B — rather than
    every stem sharing the same shared/common row.

    Every OTHER course in the same GroupingTemplate that a stem also takes
    (e.g. EPSC 111, EDCI 111 alongside EDFO 111) reuses the SAME letter for
    that stem, so "English/Literature" ends up with EDFO 111-A, EPSC 111-A
    and EDCI 111-A together — one lettered section per stem, not per course.

    A stem MAY hold more than one letter (its own cohort needed two parallel
    classes of the shared course), but a given letter is pinned to exactly
    ONE stem per template (see unique_together) — a physical class section
    belongs to a single combination.
    """
    template = models.ForeignKey(
        GroupingTemplate, on_delete=models.CASCADE, related_name="stem_assignments",
    )
    group = models.ForeignKey(
        GroupingTemplateGroup, on_delete=models.CASCADE, related_name="stem_assignment",
    )
    stem = models.ForeignKey(
        "SpecializationStem", on_delete=models.CASCADE, related_name="grouping_template_assignments",
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_grouping_template_stem_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Grouping Template Stem Assignment"
        verbose_name_plural = "Grouping Template Stem Assignments"
        unique_together = ("template", "group")
        ordering = ["template", "stem", "group"]

    def __str__(self):
        return f"{self.template} — Group {self.group.letter} → {self.stem.name}"


class AllocationSet(models.Model):
    """
    A named, independently-editable batch of CourseAllocation rows for one
    department. Replaces the old assumption of "one semester at a time" —
    a department can now have several AllocationSets open concurrently
    (e.g. "Semester 1", "Semester 2", "Semester 1 + selected Sem 2 units",
    a Special allocation), and switches between them without wiping data.

    Every CourseAllocation belongs to exactly one AllocationSet. This field
    is additive: the migration backfills one "Legacy Current Allocation"
    AllocationSet per department and attaches every pre-existing
    CourseAllocation row to it, so nothing that already exists moves,
    changes, or disappears.
    """

    TYPE_AUTO_FULL = "auto_full"   # full auto-allocate for the composed semester(s)
    TYPE_SELECTIVE = "selective"   # COD hand-picks specific courses/programs only
    TYPE_CHOICES = [
        (TYPE_AUTO_FULL, "Auto-allocate (full semester composition)"),
        (TYPE_SELECTIVE, "Selective (hand-picked courses)"),
    ]

    STATUS_DRAFT            = "draft"
    STATUS_SUBMITTED_TO_TT  = "submitted_tt"
    STATUS_SUBMITTED_TO_DVC = "submitted_dvc"
    STATUS_CHOICES = [
        (STATUS_DRAFT,            "Draft"),
        (STATUS_SUBMITTED_TO_TT,  "Submitted to Timetabling Office"),
        (STATUS_SUBMITTED_TO_DVC, "Submitted to DVC"),
    ]

    department = models.ForeignKey(
        "department_management.Department", on_delete=models.CASCADE,
        related_name="allocation_sets",
    )
    name = models.CharField(
        max_length=150,
        help_text="e.g. 'Semester 1 2026/2027', 'Semester 1 + selected Sem 2 units', 'Special Intake Sept 2026'.",
    )
    academic_year = models.CharField(max_length=20, blank=True, default="", help_text="e.g. 2026/2027")
    allocation_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_AUTO_FULL)
    is_special = models.BooleanField(
        default=False,
        help_text="Special allocation (shifted-semester / special-intake cohort). Independent of allocation_type.",
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    submitted_to_tt_at  = models.DateTimeField(null=True, blank=True)
    submitted_to_dvc_at = models.DateTimeField(null=True, blank=True)

    is_legacy   = models.BooleanField(
        default=False,
        help_text="True only for the auto-created set that absorbed pre-migration data. Never set by users.",
    )
    is_archived = models.BooleanField(default=False, help_text="Hidden from the active picker but never deleted.")

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="allocation_sets_created",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Allocation Set"
        verbose_name_plural = "Allocation Sets"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["department", "status"]),
            models.Index(fields=["department", "is_archived"]),
        ]

    def __str__(self):
        return f"{self.department.name} — {self.name}"

    @property
    def semester_numbers(self):
        """All semester numbers composed into this set, e.g. [1] or [1, 2]."""
        return list(
            self.semester_components.order_by("semester_number")
            .values_list("semester_number", flat=True)
        )

    def label(self):
        nums = self.semester_numbers
        if not nums:
            return self.name
        if len(nums) == 1:
            return f"Semester {nums[0]}"
        return "Semester " + " + ".join(str(n) for n in nums)


class AllocationSetSemesterComponent(models.Model):
    """
    One semester "layer" mixed into an AllocationSet.

    scope=ALL      -> every ProgramCourse for that semester, across every
                       program in the department, is eligible.
    scope=SELECTED -> only the programs (and optionally specific courses,
                       via AllocationSetComponentCourse) listed below are
                       eligible for this semester layer.

    The semester picked on the COD's "which semester is this?" step is
    normally added as scope=ALL; any further semesters mixed in afterwards
    (e.g. "also pull in a few Semester 2 units") are normally scope=SELECTED.
    """

    SCOPE_ALL      = "all"
    SCOPE_SELECTED = "selected"
    SCOPE_CHOICES = [
        (SCOPE_ALL,      "All programs / all courses"),
        (SCOPE_SELECTED, "Selected programs/courses only"),
    ]

    allocation_set = models.ForeignKey(
        AllocationSet, on_delete=models.CASCADE, related_name="semester_components",
    )
    semester_number = models.PositiveSmallIntegerField(help_text="1, 2, 3, ... — no upper limit assumed.")
    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES, default=SCOPE_ALL)

    programs = models.ManyToManyField(
        "program_management.Program", blank=True, related_name="allocation_set_components",
        help_text="Used only when scope=SELECTED.",
    )

    added_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("allocation_set", "semester_number")
        ordering = ["allocation_set", "semester_number"]

    def __str__(self):
        return f"{self.allocation_set} — Sem {self.semester_number} ({self.scope})"


class AllocationSetComponentCourse(models.Model):
    """
    Optional fine-grained pin: within a SELECTED-scope semester component,
    lock to specific ProgramCourse entries rather than "every course for
    the chosen program". Used when a COD wants only a handful of another
    semester's units mixed into an otherwise single-semester set.
    """
    component = models.ForeignKey(
        AllocationSetSemesterComponent, on_delete=models.CASCADE, related_name="pinned_courses",
    )
    program_course = models.ForeignKey("program_management.ProgramCourse", on_delete=models.CASCADE)

    class Meta:
        unique_together = ("component", "program_course")


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

    allocation_set = models.ForeignKey(
        "course_allocation.AllocationSet",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="course_allocations",
        help_text=(
            "Which AllocationSet (concurrent allocation batch, e.g. 'Semester 1', "
            "'Semester 2', a mixed set, or a Special allocation) this row belongs "
            "to. Null only transiently on rows created before this field existed; "
            "the backfill command (allocation_set backfill_legacy_allocation_sets) "
            "attaches every such row to a per-department 'Legacy Current "
            "Allocation' set without moving or altering the row itself. "
            "on_delete=PROTECT so an AllocationSet can never be deleted while it "
            "still holds allocations — it must be emptied or archived first."
        ),
    )

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

    # ── Additional mapped Student Groups (many-to-many) ─────────────────────
    additional_student_groups = models.ManyToManyField(
        "StudentGroup",
        blank=True,
        related_name="additional_course_allocations",
        help_text=(
            "Extra StudentGroups this allocation (e.g. one lettered common-"
            "course group, EDFO 111-C) is ALSO offered to, on top of the "
            "single `student_group` above. Added for the bulk multi-group "
            "course creation flow, where one lettered group can be mapped "
            "to more than one student group at once. `student_group` stays "
            "the 'primary' group (kept for backward compatibility with "
            "every existing single-group query/filter); this field is "
            "purely additive — a group with only `student_group` set and "
            "an empty `additional_student_groups` behaves exactly as "
            "before. Use the `all_mapped_student_groups` property to read "
            "the full combined set."
        ),
    )

    # ── Numbered section of a split course ───────────────────────────────────
    section_number = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text=(
            "Set when this course has been split into numbered SECTIONS "
            "(e.g. COSC 103 has too many students for one class): 1 for the "
            "original row, 2, 3, ... for the extra classes. Every section "
            "keeps the same base course code and the same student group / "
            "stem / elective-group mapping. Sections partition the cohort's "
            "students, so they may be scheduled at the SAME time without "
            "being a collision. NULL = the course has not been split."
        ),
    )

    @property
    def is_section(self):
        return self.section_number is not None

    def section_family(self):
        """Every CourseAllocation that is a section of the same course for the
        same cohort as this row (this row included). Empty queryset if this
        row is not a section."""
        if self.section_number is None:
            return CourseAllocation.objects.none()
        return CourseAllocation.objects.filter(
            program_course_id=self.program_course_id,
            allocation_set_id=self.allocation_set_id,
            intake=self.intake,
            is_evening_weekend=self.is_evening_weekend,
            student_group_id=self.student_group_id,
            special_intake_group_id=self.special_intake_group_id,
            section_number__isnull=False,
        ).order_by("section_number", "id")

    def refresh_primary_stem_pointer(self):
        """Keep the legacy singular `specialization_stem` pointer consistent
        with the real M2M membership: set when the course is in exactly one
        stem, cleared when it is in several (see specialization_stem_views)."""
        ids = list(self.specialization_stems.values_list("id", flat=True))
        want = ids[0] if len(ids) == 1 else None
        if len(ids) == 0:
            return
        if self.specialization_stem_id != want:
            self.specialization_stem_id = want
            self.save(update_fields=["specialization_stem"])

    @property
    def all_mapped_student_groups(self):
        """
        Every StudentGroup this allocation is mapped to — the primary
        `student_group` (if any) plus every group in
        `additional_student_groups`, de-duplicated. Prefer this over
        reading `student_group` alone when checking "is this group offered
        to student group X", now that a group can map to more than one.
        """
        groups = list(self.additional_student_groups.all())
        if self.student_group_id and self.student_group_id not in {g.id for g in groups}:
            groups.append(self.student_group)
        return groups

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
    def get_or_create_shared(cls, program_course, department, defaults=None, allocation_set=None):
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

        Concurrent Allocation Sets: pass `allocation_set` (the caller's
        active/legacy set) so this also scopes correctly by set — without
        it, the SAME curriculum course existing in two different concurrent
        sets (e.g. a Semester 1 set and a Semester 2 set both containing
        "COSC 101") would incorrectly match/share ONE row across both,
        letting a Semester 2 Selection Group silently reuse (and mutate)
        Semester 1's allocation. `allocation_set=None` preserves the old,
        set-unaware behaviour for any caller not yet updated.

        Returns (allocation, created) exactly like get_or_create.
        """
        existing = cls.objects.filter(
            program_course=program_course,
            department=department,
            student_group__isnull=True,
        ).order_by("id")
        if allocation_set is not None:
            existing = existing.filter(allocation_set=allocation_set)

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
            "allocation_set": allocation_set,
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

        # A Selection Group (pick ONE) may be NESTED inside a Specialization
        # Stem (a stem of 12 units where 10 are core and the student picks one
        # of the last two). Same stem + same selection group => the
        # alternatives may share a slot; alternative vs. core unit in the same
        # stem still clash-checks. See course_allocation/exemption_helpers.py.

        # ── Student Group validation ──────────────────────────────────────────
        # Electives are shared by construction and can never belong to one
        # specific student group. A specialization-stem course, however, MAY
        # now carry a student_group: this is how a course shared across a
        # whole program/year (e.g. EDFO 111) gets split into parallel lettered
        # sections that different Combination Stems each pin to (e.g. stem
        # "English/Literature" -> EDFO 111-A, stem "History/CRE" -> EDFO
        # 111-B) — see GroupingTemplateStemAssignment and
        # course_allocation/course_group_planner.py. A stem course with NO
        # student_group is still perfectly valid (shared/unsplit, as before).
        if self.student_group and self.is_elective:
            raise ValidationError(
                "Elective courses are shared across all student groups and cannot "
                "be assigned to a specific group."
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
    allocation_set = models.ForeignKey(
        "course_allocation.AllocationSet",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="selection_groups",
        help_text=(
            "Which concurrent allocation (e.g. Semester 1 vs Semester 2) this "
            "group belongs to. A COD editing Semester 1 should not see or edit "
            "Semester 2's selection groups, and the same name may be reused "
            "across different sets. Null only for rows created before this "
            "field existed; the backfill command attaches them to the "
            "department's legacy set."
        ),
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
    specialization_stems = models.ManyToManyField(
        "SpecializationStem",
        blank=True,
        related_name="elective_groups",
        help_text=(
            "Optional: the stem(s) this pick-ONE pool is nested inside "
            "(e.g. a stem of 12 units where 10 are core and the student "
            "chooses one of the last two). The pool's courses are also "
            "members of the stem, so they still clash with the stem's core "
            "units, but not with each other. Leave empty for an ordinary "
            "program-level elective pool."
        ),
    )
    program_courses = models.ManyToManyField(
        "program_management.ProgramCourse",
        blank=True,
        related_name="mapped_selection_groups",
        help_text=(
            "Curriculum (course-master) courses mapped to this elective group "
            "IN ADVANCE, on the Student Groups / Stems / Electives page. When a "
            "CourseAllocation is later created for one of these ProgramCourses "
            "(same allocation set), it is attached to this group automatically "
            "(see course_allocation/course_mapping.py)."
        ),
    )
    program_courses_from_allocation = models.ManyToManyField(
        "program_management.ProgramCourse",
        blank=True,
        related_name="+",
        help_text=(
            "Subset of program_courses that were mapped automatically by the "
            "map_program_courses_from_existing_allocations.py backfill script "
            "(reverse-derived from an existing CourseAllocation already "
            "attached here), rather than picked by hand in the Map courses "
            "dialog. Used only to flag those rows in the UI."
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

    @property
    def is_nested(self):
        return self.specialization_stems.exists()

    def sync_courses_into_mapped_stems(self):
        """Make every course of this pool a member of every stem the pool is
        nested in (so alternatives clash with the stem's core units)."""
        courses = list(self.courses.all())
        if not courses:
            return
        for stem in self.specialization_stems.all():
            stem.courses.add(*courses)
        for ca in courses:
            ca.refresh_primary_stem_pointer()


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
    allocation_set = models.ForeignKey(
        "course_allocation.AllocationSet",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="specialization_categories",
        help_text=(
            "Which concurrent allocation this category belongs to — same "
            "reasoning as SelectionGroup.allocation_set above. Lets the same "
            "category/stem name be reused across different sets."
        ),
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
        unique_together      = ("program", "name", "allocation_set")

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
    program_courses = models.ManyToManyField(
        "program_management.ProgramCourse",
        blank=True,
        related_name="mapped_stems",
        help_text=(
            "Curriculum (course-master) courses mapped to this stem IN ADVANCE, "
            "on the Student Groups / Stems / Electives page. When a "
            "CourseAllocation is later created for one of these ProgramCourses "
            "(same allocation set), it is attached to this stem automatically "
            "(see course_allocation/course_mapping.py)."
        ),
    )
    program_courses_from_allocation = models.ManyToManyField(
        "program_management.ProgramCourse",
        blank=True,
        related_name="+",
        help_text=(
            "Subset of program_courses that were mapped automatically by the "
            "map_program_courses_from_existing_allocations.py backfill script "
            "(reverse-derived from an existing CourseAllocation already "
            "attached here), rather than picked by hand in the Map courses "
            "dialog. Used only to flag those rows in the UI."
        ),
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

    def elective_course_ids(self):
        """Ids of this stem's courses that are pick-one alternatives of a pool
        nested in the stem (the stem's remaining courses are its core units)."""
        ids = set()
        for pool in self.elective_groups.all():
            ids.update(pool.courses.values_list("id", flat=True))
        return ids

    def core_courses(self):
        return self.courses.exclude(id__in=self.elective_course_ids())

    @property
    def is_restricted(self):
        return self.restricted_to_groups.exists()

    @property
    def department(self):
        return self.category.department

    @property
    def program(self):
        return self.category.program


class StemStudentCount(models.Model):
    """
    Number of students taking one Combination Stem in a specific program
    Year/Semester — set from the "Student numbers" action on the
    Combination Stem Allocations panel (/groups-electives/).

    Kept separate from SpecializationStem rather than a single field on it
    because one stem's core courses can span more than one Year/Semester
    term (its category's own year/semester can be "Any"), so the same
    combination can carry a different headcount per term it is actually
    taught in.

    Saving a row here pushes number_of_students onto every CourseAllocation
    already attached to this stem's CORE courses (not its nested elective
    pool alternatives — those are picked individually, so one shared total
    would misrepresent them) for that Year/Semester — see
    course_group_planner.apply_stem_student_count. The row also survives
    independently of those allocations, so a count can be entered before any
    allocation exists yet and is not lost if the courses are re-mapped.
    """
    stem = models.ForeignKey(
        SpecializationStem,
        on_delete=models.CASCADE,
        related_name="student_counts",
    )
    year = models.PositiveSmallIntegerField(
        help_text="The program year this headcount applies to.",
    )
    semester = models.PositiveSmallIntegerField(
        choices=[(1, "Semester 1"), (2, "Semester 2")],
        help_text="The semester this headcount applies to.",
    )
    number_of_students = models.PositiveIntegerField(default=0)
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name         = "Combination Stem Student Count"
        verbose_name_plural  = "Combination Stem Student Counts"
        unique_together      = ("stem", "year", "semester")
        ordering             = ("stem__category__program__name", "-year", "semester")

    def __str__(self):
        return f"{self.stem.name} — Year {self.year} Semester {self.semester}: {self.number_of_students} students"


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
    allocation_set = models.ForeignKey(
        "course_allocation.AllocationSet",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="lab_allocations",
        help_text=(
            "Which concurrent AllocationSet (e.g. Semester 1 vs Semester 2, "
            "or a Special allocation) this lab/workshop allocation belongs "
            "to. Mirrors CourseAllocation.allocation_set: a COD working in "
            "one allocation set should not see or bulk-affect another "
            "set's lab allocations, even for the same department/course. "
            "Null only for rows created before this field existed; the "
            "backfill command (allocation_set backfill_legacy_allocation_sets) "
            "attaches those to the department's legacy set."
        ),
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
    allocation_set = models.ForeignKey(
        "course_allocation.AllocationSet",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="archived_allocations",
        help_text=(
            "Which concurrent AllocationSet this archive came from. Without "
            "this, archives from different concurrent sets that happen to "
            "share the same semester label got merged together on restore/"
            "delete — the same class of cross-set bleed already fixed for "
            "live CourseAllocation rows. Null only for archives created "
            "before this field existed; those are treated as legacy/"
            "unscoped and shown regardless of which set is active."
        ),
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
    group_code = models.CharField(max_length=50)
    base_course_code = models.CharField(max_length=100)
    allocation_set = models.ForeignKey(
        "course_allocation.AllocationSet",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="combined_groups",
        help_text=(
            "Which concurrent allocation this combined group belongs to. "
            "group_code used to be globally unique across the whole system, "
            "which blocked recombining the same course code in a different "
            "semester's allocation set. Now unique per allocation_set instead "
            "(see Meta.unique_together) — the same code can be reused once "
            "it's for a different set. Null only for rows created before this "
            "field existed; the backfill command attaches them to the "
            "department's legacy set."
        ),
    )
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
        unique_together = ("group_code", "allocation_set")

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

# ---------------------------------------------------------------------------
# Course Groups tab — "quick create by course code" flow.
#
# The classic Course Groups dialog (see course_group_planner.save_group_plan)
# always starts from one Program + Year + Semester. CourseGroupDraft is the
# opposite entry point: pick a course code (typed, or picked from the
# department's course list) and a number of groups, with NO program attached
# yet. Letters are stored immediately so the draft is usable right away; a
# program is mapped in afterwards (its own curriculum entry for this course
# code supplies the year/semester automatically), and nothing touches real
# StudentGroup / CourseAllocation / SpecializationStem data until the COD
# clicks Commit — at which point course_group_draft.commit_draft() fans the
# outstanding CourseGroupDraftMapping rows out to save_group_plan(), one call
# per mapped program. A mapping row is kept after a successful commit, not
# deleted — course_group_draft.draft_matrix() tells pending from committed
# apart by checking the live data on each read, so a mapping whose real
# allocations later get cleared elsewhere reappears as pending on its own,
# ready to be committed again without re-ticking every checkbox.
# ---------------------------------------------------------------------------
class CourseGroupDraft(models.Model):
    department = models.ForeignKey(
        "department_management.Department", on_delete=models.CASCADE,
        related_name="course_group_drafts",
    )
    course_code = models.CharField(max_length=100)
    course_name = models.CharField(max_length=200, blank=True, default="")
    num_groups = models.PositiveSmallIntegerField(default=1)
    # Ratchet — the highest group-letter index EVER issued for this draft.
    # Only ever moves up. Lets a dropped letter (see course_group_draft.
    # delete_letter) stay dropped forever: growing the course again (via
    # resizing this same quick-create form or the "+ Add groups" control)
    # always continues past this, never back-fills a letter that was
    # deleted. num_groups, by contrast, tracks the CURRENT active count and
    # goes back down when a letter is deleted.
    highest_letter_index = models.PositiveSmallIntegerField(default=0)
    # Informational only — the mode (most common) year/semester across every
    # ProgramCourse in the department that carries this course code, so the
    # course can show a sensible "Year X Semester Y" label before any
    # program has actually been mapped in. Never fed back into save_group_plan;
    # each mapped program's OWN ProgramCourse match decides its own year/semester.
    suggested_year = models.PositiveSmallIntegerField(null=True, blank=True)
    suggested_semester = models.PositiveSmallIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_course_group_drafts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Course Group Draft"
        verbose_name_plural = "Course Group Drafts"
        unique_together = ("department", "course_code")
        ordering = ["course_code"]

    def __str__(self):
        return f"{self.course_code} ({self.num_groups} groups)"


class CourseGroupDraftLetter(models.Model):
    draft = models.ForeignKey(CourseGroupDraft, on_delete=models.CASCADE, related_name="letters")
    letter = models.CharField(max_length=4)
    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="course_group_draft_letters",
        help_text=(
            "Lecturer assigned to THIS lettered group (e.g. 'EDFO 111-A') for the "
            "whole course code, regardless of which program/stem it ends up mapped "
            "to — one letter is one physical class. Settable before or after Commit; "
            "set_letter_lecturer() keeps any already-committed CourseAllocation rows "
            "for this letter in sync."
        ),
    )

    class Meta:
        verbose_name = "Course Group Draft Letter"
        verbose_name_plural = "Course Group Draft Letters"
        unique_together = ("draft", "letter")
        ordering = ["letter"]

    def __str__(self):
        return f"{self.draft.course_code}-{self.letter}"


class CourseGroupDraftMapping(models.Model):
    """
    One pending checkbox in the Course Groups matrix: "letter <letter> of
    this draft goes to stem <stem> of program <program>" (stem left blank =
    a plain/shared letter for that program, no combination-stem split).
    Not applied to real data until Commit — see commit_draft() in
    course_group_draft.py. year/semester are snapshotted from the matching
    ProgramCourse the moment the row is created.
    """
    draft = models.ForeignKey(CourseGroupDraft, on_delete=models.CASCADE, related_name="mappings")
    letter = models.ForeignKey(CourseGroupDraftLetter, on_delete=models.CASCADE, related_name="mappings")
    program = models.ForeignKey(
        "program_management.Program", on_delete=models.CASCADE,
        related_name="course_group_draft_mappings",
    )
    stem = models.ForeignKey(
        SpecializationStem, on_delete=models.CASCADE, null=True, blank=True,
        related_name="course_group_draft_mappings",
    )
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    semester = models.PositiveSmallIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Course Group Draft Mapping"
        verbose_name_plural = "Course Group Draft Mappings"
        unique_together = ("letter", "program", "stem")
