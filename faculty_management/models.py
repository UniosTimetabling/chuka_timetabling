from django.db import models
from django.contrib.auth.models import User


class Faculty(models.Model):
    """
    A faculty, managed by DVC and their admins.
    Example: Faculty of Science, Faculty of Arts.
    """
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True, null=True)
    leader = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="faculty_leader"
    )

    def __str__(self):
        return self.name

    class Meta:
        permissions = [
            ("assign_faculty_leader", "Can assign faculty leader"),
            ("remove_faculty_leader", "Can remove faculty leader"),
        ]
