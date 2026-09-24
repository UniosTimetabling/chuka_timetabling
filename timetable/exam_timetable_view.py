
from django.shortcuts import render
from course_allocation.models import CourseAllocation
from course_allocation.allocation_scope import apply_tt_scope
from timetable.models import Timetable, ExamTimetable, AutoMergedExamGroup, MergedCourseGroup, LabTimetable, LabExamTimetable
from classreps.models import MinimalTimetable, ClassRep
from room_management.models import Venue, LabVenue
# -------------------------------------------------
# EXAM TIMETABLE VIEW (uses actual dates)
# -------------------------------------------------
def exam_timetable_view(request):
    timeslots = ["08:00-10:00", "10:00-12:00", "12:00-14:00", "14:00-16:00"]
    # Concurrent Allocation Sets: only show rows belonging to whichever
    # AllocationSet(s) are active for this session (ticked on
    # /timetable/dashboard/, or the "eligible" fallback — no set, legacy,
    # or submitted-to-TT — if nothing's been ticked yet). Mirrors
    # main_timetable_view.py exactly, so switching allocation sets on the
    # dashboard affects the exam timetable page and any PDF built from it
    # the same way it already affects the regular timetable page.
    exam_timetable = apply_tt_scope(
        ExamTimetable.objects.select_related("course_allocation").all(),
        request=request,
        prefix="course_allocation__allocation_set",
    )
    # NOTE: AutoMergedExamGroup is a backward-compat alias for
    # MergedCourseGroupTimetable — the REGULAR-timetable merged-group model
    # (see timetable/models.py). It is scoped the same way
    # main_timetable_view.py would scope it, via base_course__allocation_set,
    # even though — pre-existing, unrelated to this fix — it isn't actually
    # the exam-specific MergedCourseGroup model. Left as-is rather than
    # silently swapped, since that's a separate naming issue worth
    # confirming with you rather than guessing.
    merged_groups = apply_tt_scope(
        AutoMergedExamGroup.objects.select_related("venue").all(),
        request=request,
        prefix="base_course__allocation_set",
    )
    lab_exams = apply_tt_scope(
        LabExamTimetable.objects.select_related("lab_allocation").all(),
        request=request,
        prefix="lab_allocation__allocation_set",
    )
    dates = sorted(set(exam.date for exam in exam_timetable))
    return render(request, "timetable/exam_timetable.html", {
        "dates": dates,
        "timeslots": timeslots,
        "exam_timetable": exam_timetable,
        "merged_groups": merged_groups,
        "lab_exams": lab_exams,
    })
