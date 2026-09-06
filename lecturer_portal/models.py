# lecturer_portal/models.py
# ===================================================================
#  LECTURER PORTAL MODELS
#  Contains the Lecturer model and any lecturer-specific portal models.
#  Lecturer constraints are NOT here — they belong in timetable/models.py
#  where they are used by the autoscheduler.
# ===================================================================

from django.db import models
from django.contrib.auth.models import User


class Lecturer(models.Model):
    DESIGNATIONS = [
        ("Prof", "Prof."),
        ("Dr", "Dr."),
        ("Mr", "Mr."),
        ("Ms", "Ms."),
        ("Mrs", "Mrs."),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="lecturer_profile",
    )
    payroll_number = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    email = models.EmailField(unique=True)
    designation = models.CharField(max_length=10, choices=DESIGNATIONS)

    # ✅ Optional association to Department
    department = models.ForeignKey(
        'department_management.Department',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lecturers",
        default=None,
        help_text="Department the lecturer belongs to (optional).",
    )

    max_load_override = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text=(
            "Optional per-lecturer override of max courses PER SEMESTER "
            "(seniority handling, e.g. a senior lecturer/Prof = 8, a "
            "junior/part-time lecturer = 4). Leave blank to fall back to "
            "the department/global AllocationConfig default."
        ),
    )

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        """
        Return the best human-readable name for this lecturer.
        Priority:
        1. Linked User full name
        2. Linked User username
        3. Lecturer designation + name
        """
        if self.user:
            return self.user.get_full_name() or self.user.username
        return f"{self.designation} {self.name}"

    class Meta:
        indexes = [
            # name is searched/matched frequently (chatbot lecturer lookups,
            # admin search, dvc/cod panels).
            models.Index(fields=['name']),
        ]