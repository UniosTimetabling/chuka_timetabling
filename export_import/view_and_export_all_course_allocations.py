from django.core.paginator import Paginator
from django.shortcuts import render
from django.db.models import Q, Prefetch
from course_allocation.models import CourseAllocation, LabAllocation
from department_management.models import Department
from program_management.models import Program
from lecturer_portal.models import Lecturer
from django.contrib.auth.decorators import login_required


@login_required
def view_course_allocations(request):
    # Get filter parameters
    allocation_type = request.GET.get('type', 'normal')  # 'normal' or 'lab'
    department_id = request.GET.get('department', '')
    program_id = request.GET.get('program', '')
    lecturer_id = request.GET.get('lecturer', '')
    search_query = request.GET.get('search', '')
    sort_by = request.GET.get('sort', 'department')
    
    # Fetch all departments and programs for dropdowns
    departments = Department.objects.all().order_by('name')
    programs = Program.objects.all().order_by('name')
    lecturers = Lecturer.objects.all().order_by('name')
    
    if allocation_type == 'lab':
        # Handle lab allocations
        allocations = LabAllocation.objects.select_related(
            'program_course',
            'program_course__program',
            'program_course__program__department',
            'venue',
            'lecturer'
        ).order_by('program_course__program__department__name', 
                   'program_course__program__name')
        
        # Apply filters
        if department_id:
            allocations = allocations.filter(
                program_course__program__department_id=department_id
            )
        if program_id:
            allocations = allocations.filter(
                program_course__program_id=program_id
            )
        if lecturer_id:
            allocations = allocations.filter(lecturer_id=lecturer_id)
        if search_query:
            allocations = allocations.filter(
                Q(program_course__course_code__icontains=search_query) |
                Q(program_course__course_name__icontains=search_query) |
                Q(venue__code__icontains=search_query) |
                Q(lecturer__name__icontains=search_query)
            )
        
        # Group by department and program
        grouped_data = {}
        for allocation in allocations:
            dept = allocation.program_course.program.department
            prog = allocation.program_course.program
            
            dept_name = dept.name
            prog_name = prog.name
            
            if dept_name not in grouped_data:
                grouped_data[dept_name] = {}
            if prog_name not in grouped_data[dept_name]:
                grouped_data[dept_name][prog_name] = []
            
            grouped_data[dept_name][prog_name].append(allocation)
            
    else:
        # Handle normal course allocations
        allocations = CourseAllocation.objects.select_related(
            'department', 'origin_department', 'program', 'lecturer'
        ).order_by('department__name', 'program__name')
        
        # Apply filters
        if department_id:
            allocations = allocations.filter(department_id=department_id)
        if program_id:
            allocations = allocations.filter(program_id=program_id)
        if lecturer_id:
            allocations = allocations.filter(lecturer_id=lecturer_id)
        if search_query:
            allocations = allocations.filter(
                Q(course_code__icontains=search_query) |
                Q(course_name__icontains=search_query) |
                Q(lecturer__name__icontains=search_query) |
                Q(program__name__icontains=search_query)
            )
        
        # Apply sorting
        if sort_by == 'lecturer':
            allocations = allocations.order_by('lecturer__name', 'department__name')
        elif sort_by == 'program':
            allocations = allocations.order_by('program__name', 'course_code')
        elif sort_by == 'course':
            allocations = allocations.order_by('course_code', 'program__name')
        
        # Group by department and program
        grouped_data = {}
        for allocation in allocations:
            dept_name = allocation.department.name if allocation.department else "No Department"
            prog_name = allocation.program.name if allocation.program else "No Program"
            
            if dept_name not in grouped_data:
                grouped_data[dept_name] = {}
            if prog_name not in grouped_data[dept_name]:
                grouped_data[dept_name][prog_name] = []
            
            grouped_data[dept_name][prog_name].append(allocation)
    
    # Pagination
    if allocation_type == 'lab':
        # For lab allocations, flatten for pagination
        flat_list = []
        for dept in grouped_data.values():
            for prog_allocations in dept.values():
                flat_list.extend(prog_allocations)
        
        paginator = Paginator(flat_list, 20)  # 20 items per page
    else:
        # For normal allocations
        flat_list = []
        for dept in grouped_data.values():
            for prog_allocations in dept.values():
                flat_list.extend(prog_allocations)
        
        paginator = Paginator(flat_list, 20)  # 20 items per page
    
    page_number = request.GET.get('page', 1)
    
    try:
        page_obj = paginator.get_page(page_number)
    except Exception as e:
        # If invalid page number, default to page 1
        page_obj = paginator.get_page(1)
    
    # Calculate safe previous and next page numbers
    previous_page = page_obj.number - 1 if page_obj.has_previous() else 1
    next_page = page_obj.number + 1 if page_obj.has_next() else page_obj.paginator.num_pages
    
    context = {
        'grouped_data': grouped_data,
        'page_obj': page_obj,
        'departments': departments,
        'programs': programs,
        'lecturers': lecturers,
        'allocation_type': allocation_type,
        'selected_department': department_id,
        'selected_program': program_id,
        'selected_lecturer': lecturer_id,
        'search_query': search_query,
        'sort_by': sort_by,
        'total_allocations': paginator.count,
        'previous_page': previous_page,
        'next_page': next_page,
    }
    
    return render(request, 'export/view_allocations.html', context)