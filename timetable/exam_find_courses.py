"""
timetable/exam_find_courses.py
================================
"Find Courses" feature for the Exam Timetable Panel — mirrors
`timetable/find_courses.py` but reports scheduling status from
`ExamTimetable` (date-based) instead of `Timetable` (weekday-based).
"""

from django.http import JsonResponse
from core.rbac import allowed_roles, Role

from course_allocation.models import CourseAllocation, CombinedCourseGroup, SelectionGroup
from lecturer_portal.models import Lecturer
from department_management.models import Department
from program_management.models import Program
from timetable.models import ExamTimetable

from timetable.exam_timetable_panel import get_course_year_from_program_course

MAX_RESULTS = 60


# ═══════════════════════════════════════════════════════════════════
# API — filter picker options
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_find_filter_options_api(request):
    lecturers = list(
        Lecturer.objects.filter(course_allocations__isnull=False)
        .distinct().order_by('name').values('id', 'name')
    )
    departments = list(
        Department.objects.filter(allocations__isnull=False)
        .distinct().order_by('name').values('id', 'name')
    )
    programs = list(
        Program.objects.filter(allocations__isnull=False)
        .distinct().order_by('name').values('id', 'name', 'department_id')
    )
    years = [{'value': y, 'label': f'Year {y}'} for y in range(1, 7)]
    combined_groups = list(
        CombinedCourseGroup.objects.order_by('group_code').values(
            'id', 'group_code', 'base_course_code'
        )
    )
    selection_groups = list(
        SelectionGroup.objects.order_by('name').values('id', 'name')
    )

    return JsonResponse({
        'status': 'success',
        'lecturers': lecturers,
        'departments': departments,
        'programs': programs,
        'years': years,
        'combined_groups': [
            {'id': g['id'], 'label': f"{g['group_code']} ({g['base_course_code']})"}
            for g in combined_groups
        ],
        'selection_groups': [{'id': g['id'], 'label': g['name']} for g in selection_groups],
    })


# ═══════════════════════════════════════════════════════════════════
# API — search exam courses by any combination of filters
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_find_courses_api(request):
    q = (request.GET.get('q') or '').strip()
    lecturer_id = request.GET.get('lecturer_id') or None
    department_id = request.GET.get('department_id') or None
    program_id = request.GET.get('program_id') or None
    year = request.GET.get('year') or None
    group_type = (request.GET.get('group_type') or '').strip()
    group_id = request.GET.get('group_id') or None

    qs = CourseAllocation.objects.select_related(
        'lecturer', 'department', 'program', 'program_course', 'selection_group'
    ).prefetch_related('combined_groups')

    if q:
        from django.db.models import Q
        qs = qs.filter(Q(course_code__icontains=q) | Q(course_name__icontains=q))
    if lecturer_id:
        qs = qs.filter(lecturer_id=lecturer_id)
    if department_id:
        qs = qs.filter(department_id=department_id)
    if program_id:
        qs = qs.filter(program_id=program_id)
    if year:
        qs = qs.filter(program_course__year=year)
    if group_type == 'combined' and group_id:
        qs = qs.filter(combined_groups__id=group_id)
    elif group_type == 'selection' and group_id:
        qs = qs.filter(selection_groups__id=group_id)

    qs = qs.distinct().order_by('course_code')[:MAX_RESULTS]

    alloc_ids = [a.id for a in qs]
    scheduled_map = {
        et.course_allocation_id: et
        for et in ExamTimetable.objects.filter(course_allocation_id__in=alloc_ids).select_related('venue')
    }

    results = []
    for alloc in qs:
        et = scheduled_map.get(alloc.id)
        combined_list = list(alloc.combined_groups.all())
        combined = combined_list[0] if combined_list else None
        results.append({
            'id': alloc.id,
            'course_code': alloc.course_code,
            'course_name': alloc.course_name,
            'lecturer': getattr(alloc.lecturer, 'name', 'Unassigned'),
            'lecturer_id': alloc.lecturer_id,
            'department': getattr(alloc.department, 'name', 'N/A'),
            'department_id': alloc.department_id,
            'program': getattr(alloc.program, 'name', 'N/A'),
            'program_id': alloc.program_id,
            'year': get_course_year_from_program_course(alloc),
            'students': alloc.number_of_students or 0,
            'is_elective': alloc.is_elective,
            'combined_group': (
                {'id': combined.id, 'label': combined.group_code} if combined else None
            ),
            'selection_group': (
                {'id': alloc.selection_group_id, 'label': alloc.selection_group.name}
                if alloc.selection_group_id else None
            ),
            'scheduled': (
                {
                    'et_id': et.id,
                    'date': str(et.date),
                    'day': et.day,
                    'start': et.start_time.strftime('%H:%M'),
                    'end': et.end_time.strftime('%H:%M'),
                    'venue': et.venue.code if et.venue else 'Unknown',
                } if et else None
            ),
        })

    return JsonResponse({'status': 'success', 'count': len(results), 'courses': results})
