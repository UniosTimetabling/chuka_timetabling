from django.db import models
from django.utils import timezone

# -------------------------------
# CLASS REP AUTH MODEL
# -------------------------------
class ClassRep(models.Model):
    full_name = models.CharField(max_length=150,blank=True)
    reg_no = models.CharField(max_length=50, unique=True,blank=True)
    username = models.CharField(max_length=50, unique=True ,blank=True)
    email = models.EmailField(unique=True,blank=True)
    password = models.CharField(max_length=128,default="123")  # store hashed or plaintext if using manual auth
    program = models.ForeignKey(
        "program_management.Program", on_delete=models.CASCADE, related_name="class_reps"
    )
    date_registered = models.DateTimeField(default=timezone.now)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.full_name} ({self.program.name})"

    def check_password(self, raw_password):
        """Basic password check – replace with Django's make_password if needed."""
        return self.password == raw_password


# -------------------------------
# CLASS REP NOTIFICATIONS
# -------------------------------
class ClassRepNotification(models.Model):
    recipient = models.ForeignKey(
        ClassRep, on_delete=models.CASCADE, related_name="notifications"
    )
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"To {self.recipient.username}: {self.message[:40]}..."

    class Meta:
        ordering = ["-created_at"]


# -------------------------------
# MINIMAL TIMETABLE (editable copy)
# -------------------------------
class MinimalTimetable(models.Model):
    class_rep = models.ForeignKey(ClassRep, on_delete=models.CASCADE, related_name="minimal_timetable")
    course_code = models.CharField(max_length=50)
    course_name = models.CharField(max_length=200)
    day = models.CharField(max_length=20)
    start_time = models.TimeField()
    end_time = models.TimeField()
    venue = models.CharField(max_length=100)
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.course_code} ({self.day} {self.start_time}-{self.end_time})"