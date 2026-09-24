from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import datetime


class ODELCourseAllocation(models.Model):
    """
    ODEL Course Allocation referencing main program management
    """
    # Reference to existing ProgramCourse from program_management
    program_course = models.ForeignKey(
        "program_management.ProgramCourse",
        on_delete=models.CASCADE,
        related_name="odel_allocations"
    )
    
    lecturer = models.ForeignKey(
        "lecturer_portal.Lecturer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="odel_allocations",
        help_text="Assigned lecturer"
    )
    
    number_of_students = models.PositiveIntegerField(default=0)
    
    # Updated approval workflow - removed approved_by_hod
    submitted_to_tt = models.BooleanField(default=False)
    submitted_to_dvc = models.BooleanField(default=False)
    approved_by_dvc = models.BooleanField(default=False)
    rejected = models.BooleanField(default=False)
    reason_for_rejection = models.TextField(
        blank=True,
        default="No reason yet",
        help_text="Provide a reason if rejected"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        if self.lecturer:
            return f"{self.program_course.course_code} - {self.lecturer.display_name}"
        return f"{self.program_course.course_code} - Unassigned"
    
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
        return self.program.department if self.program else None
    
    def status_label(self):
        if self.approved_by_dvc:
            return "✅ Approved by DVC"
        if self.rejected:
            return f"❌ Rejected - {self.reason_for_rejection}"
        if self.submitted_to_dvc:
            return "⏳ Pending DVC Decision"
        if self.submitted_to_tt:
            return "📅 Available for Timetabling"
        return "📝 Pending Submission"
    
    def clean(self):
        from django.core.exceptions import ValidationError
        
        if self.approved_by_dvc and self.rejected:
            raise ValidationError("An allocation cannot be both approved and rejected.")
        
        if self.rejected and not self.reason_for_rejection.strip():
            raise ValidationError("Please provide a reason for rejection.")
        
        if self.approved_by_dvc:
            self.reason_for_rejection = "No reason yet"
    
    class Meta:
        unique_together = ('program_course',)  # One allocation per course per semester
        permissions = [
            ("approve_odel_allocation", "Can approve ODEL course allocation"),
            ("forward_odel_to_timetable", "Can forward ODEL to timetable"),
            ("forward_odel_to_dvc", "Can forward ODEL to DVC"),
        ]
        verbose_name = "ODEL Course Allocation"
        verbose_name_plural = "ODEL Course Allocations"


class ODELTimetableConfig(models.Model):
    """
    Configuration for ODEL timetable scheduling with date ranges
    """
    # Date range for scheduling
    start_date = models.DateField(default=datetime.date.today)
    end_date = models.DateField(
        default=datetime.date.today() + datetime.timedelta(days=30),
        help_text="Last date for scheduling"
    )
    
    # Daily time range
    day_start_time = models.TimeField(
        default=datetime.time(7, 0),
        help_text="Daily start time (e.g., 7:00 AM)"
    )
    day_end_time = models.TimeField(
        default=datetime.time(19, 0),
        help_text="Daily end time (e.g., 7:00 PM)"
    )
    
    # Class scheduling
    class_slot_size = models.PositiveIntegerField(
        default=4,
        help_text="Number of class slots per day"
    )
    
    # Exam scheduling
    exam_slot_size = models.PositiveIntegerField(
        default=3,
        help_text="Number of exam slots per day"
    )
    exam_break_duration = models.PositiveIntegerField(
        default=60,
        help_text="Break duration between exam slots in minutes",
        choices=[
            (30, "30 minutes"),
            (60, "1 hour"),
            (90, "1.5 hours"),
            (120, "2 hours"),
        ]
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"ODEL Config: {self.start_date} to {self.end_date}"
    
    class Meta:
        verbose_name = "ODEL Timetable Config"
        verbose_name_plural = "ODEL Timetable Config"


class ODELTempTimetable(models.Model):
    """
    Temporary ODEL class timetable (draft)
    """
    course_allocation = models.ForeignKey(
        "odel_system.ODELCourseAllocation",
        on_delete=models.CASCADE,
        related_name="temp_timetable_entries"
    )
    venue = models.ForeignKey("room_management.Venue", on_delete=models.CASCADE)
    
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True)
    
    def __str__(self):
        return f"[TEMP] {self.course_allocation.course_code} - {self.date} {self.start_time}"
    
    class Meta:
        unique_together = ('venue', 'date', 'start_time', 'end_time')
        ordering = ['date', 'start_time']
        verbose_name = "ODEL Temporary Timetable"
        verbose_name_plural = "ODEL Temporary Timetables"


class ODELTimetable(models.Model):
    """
    Approved ODEL class timetable
    """
    course_allocation = models.ForeignKey(
        "odel_system.ODELCourseAllocation",
        on_delete=models.CASCADE,
        related_name="timetable_entries"
    )
    venue = models.ForeignKey("room_management.Venue", on_delete=models.CASCADE)
    
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    
    approved_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, related_name="odel_approved")
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.course_allocation.course_code} - {self.date}"
    
    class Meta:
        unique_together = ('venue', 'date', 'start_time', 'end_time')
        ordering = ['date', 'start_time']
        permissions = [
            ("approve_odel_timetable", "Can approve ODEL timetable"),
        ]
        verbose_name = "ODEL Timetable"
        verbose_name_plural = "ODEL Timetables"


class ODELExamTempTimetable(models.Model):
    """
    Temporary ODEL exam timetable (draft)
    """
    course_allocation = models.ForeignKey(
        "odel_system.ODELCourseAllocation",
        on_delete=models.CASCADE,
        related_name="exam_temp_entries"
    )
    venue = models.ForeignKey("room_management.Venue", on_delete=models.CASCADE)
    
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"[TEMP EXAM] {self.course_allocation.course_code}"
    
    class Meta:
        unique_together = ('venue', 'date', 'start_time', 'end_time')
        ordering = ['date', 'start_time']
        verbose_name = "ODEL Temporary Exam Timetable"
        verbose_name_plural = "ODEL Temporary Exam Timetables"


class ODELExamTimetable(models.Model):
    """
    Approved ODEL exam timetable
    """
    course_allocation = models.ForeignKey(
        "odel_system.ODELCourseAllocation",
        on_delete=models.CASCADE,
        related_name="exam_entries"
    )
    venue = models.ForeignKey("room_management.Venue", on_delete=models.CASCADE)
    
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    
    approved_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, related_name="odel_exam_approved")
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.course_allocation.course_code} - {self.date}"
    
    class Meta:
        unique_together = ('venue', 'date', 'start_time', 'end_time')
        ordering = ['date', 'start_time']
        permissions = [
            ("approve_odel_exam", "Can approve ODEL exam timetable"),
        ]
        verbose_name = "ODEL Exam Timetable"
        verbose_name_plural = "ODEL Exam Timetables"

import os
from django.db import models
from django.utils import timezone


def timetable_pdf_upload_path(instance, filename):
    """Generate upload path for published timetable PDFs"""
    # Create folder structure: timetables/published/class|exam/YYYY/MM/
    timetable_type = "class" if instance.is_class_timetable else "exam"
    year = timezone.now().strftime('%Y')
    month = timezone.now().strftime('%m')
    # Use timestamp from when file is being saved
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{timetable_type}_timetable_v{instance.version}_{timestamp}.pdf"
    return os.path.join('timetables', 'published', timetable_type, year, month, filename)


class PublishedTimetablePDF(models.Model):
    """
    Model to store published timetable PDFs
    """
    # Type identification
    is_class_timetable = models.BooleanField(
        default=False,
        help_text="True for class timetable, False for exam timetable"
    )
    
    # File storage
    pdf_file = models.FileField(
        upload_to=timetable_pdf_upload_path,
        help_text="The published PDF file"
    )
    
    # Version info
    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number (increments with each publication)"
    )
    is_latest = models.BooleanField(
        default=True,
        help_text="Whether this is the latest published version"
    )
    
    # Publishing timestamp
    published_at = models.DateTimeField(default=timezone.now)
    
    class Meta:
        ordering = ['-published_at', '-version']
        verbose_name = "Published Timetable PDF"
        verbose_name_plural = "Published Timetable PDFs"
        permissions = [
            ("publish_timetable", "Can publish timetables"),
            ("view_published_timetable", "Can view published timetables"),
        ]
    
    def __str__(self):
        type_str = "Class" if self.is_class_timetable else "Exam"
        return f"{type_str} Timetable v{self.version} - {self.published_at.strftime('%Y-%m-%d %H:%M')}"
    
    def save(self, *args, **kwargs):
        # Ensure version is set
        if not self.version:
            latest = PublishedTimetablePDF.objects.filter(
                is_class_timetable=self.is_class_timetable
            ).order_by('-version').first()
            self.version = (latest.version + 1) if latest else 1
        
        # Set previous versions as not latest
        if self.is_latest:
            PublishedTimetablePDF.objects.filter(
                is_class_timetable=self.is_class_timetable,
                is_latest=True
            ).exclude(pk=self.pk).update(is_latest=False)
        
        super().save(*args, **kwargs)
    
    @classmethod
    def get_latest_class(cls):
        """Get the latest published class timetable"""
        return cls.objects.filter(is_class_timetable=True, is_latest=True).first()
    
    @classmethod
    def get_latest_exam(cls):
        """Get the latest published exam timetable"""
        return cls.objects.filter(is_class_timetable=False, is_latest=True).first()
    
    @classmethod
    def publish(cls, pdf_content, is_class_timetable, filename=None):
        """
        Publish a new timetable version
        
        Args:
            pdf_content: bytes or ContentFile of the PDF
            is_class_timetable: True for class, False for exam
            filename: Optional custom filename
        """
        from django.core.files.base import ContentFile
        
        # Create instance
        published = cls(
            is_class_timetable=is_class_timetable,
            is_latest=True,
            published_at=timezone.now()
        )
        
        # Generate filename if not provided
        if not filename:
            timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
            type_str = "class" if is_class_timetable else "exam"
            filename = f"{type_str}_timetable_{timestamp}.pdf"
        
        # Save the file (version will be set in save method)
        published.pdf_file.save(filename, ContentFile(pdf_content), save=True)
        
        return published