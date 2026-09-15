
from django.shortcuts import render
from course_allocation.models import CourseAllocation
from timetable.models import Timetable, ExamTimetable,AutoMergedExamGroup,MergedCourseGroup,LabTimetable,LabExamTimetable
from classreps.models import MinimalTimetable, ClassRep
from room_management.models import Venue, LabVenue
# -------------------------------------------------
# EXAM TIMETABLE VIEW (uses actual dates)
# -------------------------------------------------
def exam_timetable_view(request):
    timeslots = ["08:00-10:00", "10:00-12:00", "12:00-14:00", "14:00-16:00"]
    exam_timetable = ExamTimetable.objects.select_related("course_allocation").all()
    merged_groups = AutoMergedExamGroup.objects.select_related("venue").all()
    lab_exams = LabExamTimetable.objects.select_related("lab_allocation").all()
    dates = sorted(set(exam.date for exam in exam_timetable))
    return render(request, "timetable/exam_timetable.html", {
        "dates": dates,
        "timeslots": timeslots,
        "exam_timetable": exam_timetable,
        "merged_groups": merged_groups,
        "lab_exams": lab_exams,
    })

