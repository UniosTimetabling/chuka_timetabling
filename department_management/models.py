from django.db import models
from django.contrib.auth.models import User

class Department(models.Model):
    """
    Departments belong to faculties.
    Example: Computer Science Department inside Faculty of Science.
    """
    name = models.CharField(max_length=150, unique=True)
    faculty = models.ForeignKey('faculty_management.Faculty', on_delete=models.CASCADE, related_name="departments")
    description = models.TextField(blank=True, null=True)
    leader = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="department_leader"
    )

    def __str__(self):
        return f"{self.name} ({self.faculty.name})"

    class Meta:
        permissions = [
            ("assign_department_leader", "Can assign department leader"),
            ("remove_department_leader", "Can remove department leader"),
        ]


class DepartmentCode(models.Model):
    """
    Short, stable initials for a Department — e.g. "Department of
    Humanities" -> DHUM. Full department names (especially merged/combined
    ones like "Agricultural Economics, Agribusiness Management &
    Agricultural Education") are too long for the Course Allocation
    Excel/PDF columns, so exports show this code instead and the importer
    resolves a department by its code first (falling back to matching the
    full name for older files that still have it).

    Mirrors program_management.models.ProgramCode's FK-to-parent shape, so
    a department could in principle have more than one code on record, but
    in practice every department has exactly one — see
    department_management/department_codes.py for how a default is
    generated and auto-assigned the first time a department is used
    without one.
    """
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="department_codes"
    )
    code = models.CharField(max_length=12, unique=True)

    def __str__(self):
        return f"{self.code} - {self.department.name}"

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().upper()
        super().save(*args, **kwargs)