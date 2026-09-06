
from django.db.models import Q
from django.http import (
    HttpResponse,

)
import csv
from course_allocation.models import CourseAllocation
# -----------------------
# CSV Export
# -----------------------
def export_course_allocations_csv(request):
    """
    Export all course allocations to CSV.
    Includes department, origin department, faculty, lecturer, and student count.
    """
    allocations = CourseAllocation.objects.select_related(
        'department', 'department__faculty', 'origin_department', 'lecturer'
    ).all().order_by(
        'department__faculty__name', 'department__name', 'course_code'
    )

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="course_allocations.csv"'
    writer = csv.writer(response)

    writer.writerow([
        'Course Code',
        'Course Name',
        'Department',
        'Origin Department',
        'Faculty',
        'Lecturer',
        'Number of Students',
        'DVC Approved',
        'DVC Rejected',
        'Status',
    ])

    for alloc in allocations:
        lecturer_name = alloc.lecturer.display_name if alloc.lecturer else "Unassigned"
        origin_name = alloc.origin_department.name if alloc.origin_department else "—"
        status = alloc.status_label()
        writer.writerow([
            alloc.course_code,
            alloc.course_name,
            alloc.department.name,
            origin_name,
            alloc.department.faculty.name,
            lecturer_name,
            alloc.number_of_students,
            "Yes" if alloc.approved_by_dvc else "No",
            "Yes" if alloc.rejected_by_dvc else "No",
            status,
        ])

    return response
