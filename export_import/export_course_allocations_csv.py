
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
    Export course allocations to CSV.
    Includes department, origin department, faculty, lecturer, and student count.

    Concurrent Allocation Sets: optional ?allocation_set_id= scopes the
    export to one set (e.g. just Semester 2, or a Special allocation)
    instead of dumping every set for every department at once. Omitting it
    keeps the previous "export everything" behaviour.
    """
    allocations = CourseAllocation.objects.select_related(
        'department', 'department__faculty', 'origin_department', 'lecturer', 'allocation_set'
    ).all().order_by(
        'department__faculty__name', 'department__name', 'course_code'
    )

    allocation_set_id = request.GET.get('allocation_set_id')
    if allocation_set_id:
        allocations = allocations.filter(allocation_set_id=allocation_set_id)

    filename = f"course_allocations_set_{allocation_set_id}.csv" if allocation_set_id else "course_allocations.csv"
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)

    writer.writerow([
        'Course Code',
        'Course Name',
        'Department',
        'Origin Department',
        'Faculty',
        'Lecturer',
        'Number of Students',
        'Allocation Set',
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
            alloc.allocation_set.name if alloc.allocation_set else "—",
            "Yes" if alloc.approved_by_dvc else "No",
            "Yes" if alloc.rejected_by_dvc else "No",
            status,
        ])

    return response
