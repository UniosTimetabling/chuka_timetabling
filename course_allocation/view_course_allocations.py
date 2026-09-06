# view_course_allocations.py
import json
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db.models import Q
from collections import OrderedDict
from course_allocation.models import CourseAllocation
from department_management.models import Department
from program_management.models import Program
from lecturer_portal.models import Lecturer
from faculty_management.models import Faculty
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def course_allocations_page(request):
    """Main hierarchical allocations page: Faculty > Dept > Program > Intake"""
    departments = Department.objects.select_related('faculty').order_by('faculty__name', 'name')
    programs    = Program.objects.select_related('department').order_by('name')
    lecturers   = Lecturer.objects.order_by('name')
    faculties   = Faculty.objects.prefetch_related('departments').order_by('name')

    # Build hierarchy using only real model fields
    allocations = (
        CourseAllocation.objects
        .select_related('department', 'department__faculty', 'program', 'lecturer')
        .order_by(
            'department__faculty__name',
            'department__name',
            'program__name',
            'intake',
            'course_code',
        )
    )

    hierarchy = OrderedDict()
    for alloc in allocations:
        fac_name  = alloc.department.faculty.name if alloc.department and alloc.department.faculty else 'No Faculty'
        fac_id    = alloc.department.faculty.id   if alloc.department and alloc.department.faculty else 0
        dept_name = alloc.department.name if alloc.department else 'No Department'
        dept_id   = alloc.department.id   if alloc.department else 0
        prog_name = alloc.program.name    if alloc.program    else 'No Program'
        prog_id   = alloc.program.id      if alloc.program    else 0
        intake    = str(alloc.intake) if getattr(alloc, 'intake', None) else 'N/A'
        year_key  = f"Intake {intake}" if intake != 'N/A' else 'General'

        if fac_name not in hierarchy:
            hierarchy[fac_name] = {'id': fac_id, 'departments': OrderedDict(), 'total': 0}
        if dept_name not in hierarchy[fac_name]['departments']:
            hierarchy[fac_name]['departments'][dept_name] = {
                'id': dept_id,
                'programs': OrderedDict(),
                'lecturers': {},
                'total': 0,
            }
        dept_node = hierarchy[fac_name]['departments'][dept_name]

        # Track lecturers per department
        if alloc.lecturer:
            lname = alloc.lecturer.display_name
            if lname not in dept_node['lecturers']:
                dept_node['lecturers'][lname] = {'courses': [], 'id': alloc.lecturer.id}
            dept_node['lecturers'][lname]['courses'].append(alloc.course_code)

        if prog_name not in dept_node['programs']:
            dept_node['programs'][prog_name] = {
                'id': prog_id,
                'years': OrderedDict(),
                'total': 0,
            }
        prog_node = dept_node['programs'][prog_name]

        if year_key not in prog_node['years']:
            prog_node['years'][year_key] = []

        prog_node['years'][year_key].append({
            'id':          alloc.id,
            'course_code': alloc.course_code,
            'course_name': alloc.course_name,
            'lecturer':    alloc.lecturer.display_name if alloc.lecturer else '',
            'lecturer_id': alloc.lecturer.id if alloc.lecturer else None,
            'students':    alloc.number_of_students,
            'approved':    getattr(alloc, 'approved_by_dvc', False),
            'rejected':    getattr(alloc, 'rejected_by_dvc', False),
        })

        prog_node['total'] += 1
        dept_node['total'] += 1
        hierarchy[fac_name]['total'] += 1

    # Serialize hierarchy to JSON for use in <script> blocks.
    # {{ hierarchy|safe }} outputs Python repr (OrderedDict([...])) which is NOT valid JS.
    def _to_plain(obj):
        if isinstance(obj, (dict, OrderedDict)):
            return {k: _to_plain(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_to_plain(i) for i in obj]
        return obj

    hierarchy_json = json.dumps(_to_plain(hierarchy), ensure_ascii=False)

    return render(request, 'course_allocation/academic_course_allocations.html', {
        'departments':    departments,
        'programs':       programs,
        'lecturers':      lecturers,
        'faculties':      faculties,
        'hierarchy':      hierarchy,       # for Django template {% for %} loops
        'hierarchy_json': hierarchy_json,  # for inline <script> — valid JSON
    })


def course_allocations_data(request):
    """Return allocations as JSON (for AJAX filtering)"""
    q = CourseAllocation.objects.all().select_related('department', 'program', 'lecturer')
    dept   = request.GET.get('department')
    prog   = request.GET.get('program')
    search = request.GET.get('search')
    if dept:   q = q.filter(department_id=dept)
    if prog:   q = q.filter(program_id=prog)
    if search:
        q = q.filter(
            Q(course_code__icontains=search)
            | Q(course_name__icontains=search)
            | Q(lecturer__name__icontains=search)
        )
    data = [
        {
            'id':               x.id,
            'course_code':      x.course_code,
            'course_name':      x.course_name,
            'department':       x.department.name if x.department else '',
            'program':          x.program.name    if x.program    else '',
            'lecturer':         x.lecturer.display_name if x.lecturer else '',
            'number_of_students': x.number_of_students,
        }
        for x in q
    ]
    return JsonResponse({'data': data})


@require_POST
def save_course_allocation(request):
    pk = request.POST.get('id')
    allocation = get_object_or_404(CourseAllocation, pk=pk) if pk else CourseAllocation()
    allocation.course_code        = request.POST.get('course_code')
    allocation.course_name        = request.POST.get('course_name')
    allocation.department_id      = request.POST.get('department') or None
    allocation.program_id         = request.POST.get('program')    or None
    allocation.lecturer_id        = request.POST.get('lecturer')   or None
    allocation.number_of_students = request.POST.get('number_of_students') or 0
    allocation.save()
    return JsonResponse({'success': True})


@require_POST
def delete_course_allocation(request, pk):
    allocation = get_object_or_404(CourseAllocation, pk=pk)
    allocation.delete()
    return JsonResponse({'success': True})
