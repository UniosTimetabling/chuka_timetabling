from django.contrib import admin

from .models import (
    ResitSchedulerConfig, 
    ResitCourseAllocation, 
    ResitTempTimetable, 
    ResitTimetable,
    ResitArchivedTimetable,
    ResitSubmissionControl
)
from django.utils import timezone

@admin.register(ResitSchedulerConfig)
class ResitSchedulerConfigAdmin(admin.ModelAdmin):
    list_display = ("__str__", "start_date", "start_time", "end_time", "slot_size", "max_exam_days")
    search_fields = ("academic_year", "semester")
    list_filter = ("academic_year", "semester")


@admin.register(ResitCourseAllocation)
class ResitCourseAllocationAdmin(admin.ModelAdmin):
    list_display = ("course_code", "course_name", "faculty", "department", "program", "lecturer", "number_of_students", "submitted_to_timetabling", "scheduled")
    list_filter = ("faculty", "department", "program", "submitted_to_timetabling", "scheduled", "academic_year", "semester")
    search_fields = ("course_code", "course_name", "department__name", "program__name")
    list_editable = ("submitted_to_timetabling", "scheduled")
    readonly_fields = ("created_at", "updated_at", "created_by")
    
    def save_model(self, request, obj, form, change):
        if not change:  # New object
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ResitTempTimetable)
class ResitTempTimetableAdmin(admin.ModelAdmin):
    list_display = ("resit_course_allocation", "venue", "date", "start_time", "end_time", "registered_students")
    list_filter = ("date", "venue")
    search_fields = ("resit_course_allocation__course_code",)
    ordering = ("date", "start_time")


@admin.register(ResitTimetable)
class ResitTimetableAdmin(admin.ModelAdmin):
    list_display = ("resit_course_allocation", "venue", "date", "start_time", "end_time", "registered_students", "published_by", "published_at")
    list_filter = ("date", "venue")
    search_fields = ("resit_course_allocation__course_code",)
    ordering = ("date", "start_time")
    readonly_fields = ("published_by", "published_at", "version")
    
    def save_model(self, request, obj, form, change):
        if not obj.published_by:
            obj.published_by = request.user
            obj.published_at = timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(ResitArchivedTimetable)
class ResitArchivedTimetableAdmin(admin.ModelAdmin):
    list_display = ("course_code", "course_name", "venue_code", "date", "archived_at")
    list_filter = ("date", "archived_at")
    search_fields = ("course_code", "course_name")
    readonly_fields = ("archived_at",)
    ordering = ("-archived_at",)


@admin.register(ResitSubmissionControl)
class ResitSubmissionControlAdmin(admin.ModelAdmin):
    list_display = ("department", "allow_submission_to_timetabling", "allow_auto_scheduling", "updated_at")
    list_filter = ("allow_submission_to_timetabling", "allow_auto_scheduling")
    search_fields = ("department__name",)