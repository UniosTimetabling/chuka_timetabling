"""
Update the number of students on a CourseAllocation directly from the
timetable panel's right-click menu.

Two scopes:
  - 'course'  — just the right-clicked allocation.
  - 'program' — every CourseAllocation sharing the same program AND the
                same year of study (the same cohort), matching the exact
                scoping the existing "Move for these Students…" bulk-move
                action already uses (see simulate_move.bulk_move_candidates_api,
                scope_type='program_year') so "whole program" here means the
                same cohort of students, not literally every course the
                program ever offers across all years.

Since this writes straight to CourseAllocation.number_of_students, it is
immediately visible in Course Allocation (that IS the source of the number
shown there) with no separate sync step needed. CombinedCourseGroup.total_students()
is a live-computed method (sums its members' number_of_students on every
call), so combined-group totals pick up the change automatically too — the
only stored total that can go stale is MergedCourseGroupTimetable
(AutoMergedExamGroup) for the exam side, which this recomputes explicitly
for any group the updated allocation(s) belong to.
"""
from django.views.decorators.http import require_POST
from django.db import transaction
from django.http import JsonResponse

from core.rbac import allowed_roles, Role
from course_allocation.models import CourseAllocation
from timetable.models import AutoMergedExamGroup
from timetable.timetable_panel import _get_year_value


@require_POST
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def update_student_count_api(request):
    alloc_id = (request.POST.get('allocation_id') or '').strip()
    scope = (request.POST.get('scope') or 'course').strip().lower()
    raw_count = (request.POST.get('number_of_students') or '').strip()

    if not alloc_id:
        return JsonResponse({'status': 'error', 'message': 'allocation_id is required.'}, status=400)
    if scope not in ('course', 'program'):
        return JsonResponse({'status': 'error', 'message': "scope must be 'course' or 'program'."}, status=400)

    try:
        new_count = int(raw_count)
        if new_count < 0:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse({'status': 'error', 'message': 'Enter a whole, non-negative number of students.'}, status=400)

    try:
        alloc = CourseAllocation.objects.select_related('program').get(id=alloc_id)
    except CourseAllocation.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Course allocation not found.'}, status=404)

    updated = []

    with transaction.atomic():
        if scope == 'course':
            alloc.number_of_students = new_count
            alloc.save(update_fields=['number_of_students'])
            updated.append(alloc)
        else:  # scope == 'program'
            if not alloc.program_id:
                return JsonResponse({
                    'status': 'error',
                    'message': f'{alloc.course_code} has no program assigned, so it cannot be updated by program.',
                }, status=400)
            year = _get_year_value(alloc)
            candidates = CourseAllocation.objects.filter(program_id=alloc.program_id).select_related('program')
            for a in candidates:
                if _get_year_value(a) != year:
                    continue
                a.number_of_students = new_count
                a.save(update_fields=['number_of_students'])
                updated.append(a)
            if not updated:
                return JsonResponse({
                    'status': 'error',
                    'message': 'No course allocations matched this program/year — nothing was updated.',
                }, status=400)

        # Keep AutoMergedExamGroup.total_students (a stored, not computed,
        # field) in sync for any group any updated allocation belongs to.
        # CombinedCourseGroup needs no equivalent step — its total_students()
        # is computed live from current allocation values on every call.
        updated_ids = [a.id for a in updated]
        exam_groups = AutoMergedExamGroup.objects.filter(
            merged_courses__id__in=updated_ids
        ).distinct()
        for g in exam_groups:
            g.total_students = sum(c.number_of_students or 0 for c in g.merged_courses.all())
            g.save(update_fields=['total_students'])

    return JsonResponse({
        'status': 'success',
        'scope': scope,
        'number_of_students': new_count,
        'updated_count': len(updated),
        'updated_allocation_ids': updated_ids,
        'message': (
            f'Updated {alloc.course_code} to {new_count} students.'
            if scope == 'course'
            else f'Updated {len(updated)} course allocation(s) for this program/year to {new_count} students each.'
        ),
    })
