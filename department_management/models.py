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