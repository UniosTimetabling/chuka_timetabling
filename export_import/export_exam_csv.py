from django.http import HttpResponse
from timetable.models import ExamTimetable, MergedCourseGroup
import csv
def export_exam_csv(request):
    """
    Export ExamTimetable to CSV including only published merged courses (no duplicates).
    """
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="exam_timetable.csv"'
    writer = csv.writer(response)
    writer.writerow(
        ["Course Code", "Course Name", "Lecturer", "Venue", "Day", "Date", "Start", "End"]
    )

    entries = (
        ExamTimetable.objects.select_related("course_allocation__lecturer")
        .order_by("date", "start_time")
    )
    handled_groups = set()
    scheduled_course_ids = set(entries.values_list("course_allocation_id", flat=True))

    for e in entries:
        ca = e.course_allocation

        # venue display
        v = getattr(e, "venue", None)
        venue_display = "Unassigned"
        if v:
            if hasattr(v, "code"):
                venue_display = v.code
            elif hasattr(v, "name"):
                venue_display = v.name
            else:
                venue_display = str(v)

        # base course
        writer.writerow(
            [
                ca.course_code,
                ca.course_name,
                ca.lecturer.display_name if ca.lecturer else "Unassigned",
                venue_display,
                e.day,
                e.date.strftime("%Y-%m-%d"),
                e.start_time.strftime("%H:%M"),
                e.end_time.strftime("%H:%M"),
            ]
        )

        # merged ones only if published and not already scheduled
        merged_group = (
            MergedCourseGroup.objects.filter(base_course=ca, published=True)
            .first()
        )
        if merged_group and merged_group.id not in handled_groups:
            handled_groups.add(merged_group.id)
            for mc in merged_group.merged_courses.all():
                if mc.id not in scheduled_course_ids:  # avoid duplicate
                    writer.writerow(
                        [
                            mc.course_code,
                            mc.course_name,
                            mc.lecturer.display_name if mc.lecturer else "Unassigned",
                            venue_display,
                            e.day,
                            e.date.strftime("%Y-%m-%d"),
                            e.start_time.strftime("%H:%M"),
                            e.end_time.strftime("%H:%M"),
                        ]
                    )

    return response
