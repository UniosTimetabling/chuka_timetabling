# odel_system/views_allocation.py
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.db.models import Q, Count
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie
import logging
import json
from django.utils import timezone

from odel_system.models import ODELCourseAllocation
from program_management.models import ProgramCourse, Program
from lecturer_portal.models import Lecturer
from department_management.models import Department
from django.contrib.auth.models import User


logger = logging.getLogger(__name__)


def detect_user_department(user: User):
    """
    Try several heuristics to find the department associated with the logged-in user:
     - OrgRole.department: direct, authoritative link (works for COD Admin
       accounts too, unlike the checks below).
     - Department.leader == user
     - Lecturer with email matching user.email -> find department via Lecturer model? (if Lecturer had FK, not in your model)
     - OrgRole with a title containing 'COD' mapping to user (legacy fallback).
    If none found, return None and frontend will show dept select.
    """
    org = getattr(user, "org_role", None)
    if org and org.department_id:
        return org.department

    try:
        dept = Department.objects.filter(leader=user).first()
        if dept:
            return dept
    except Exception:
        pass

    # Lecturer profile linked directly to this user account.
    try:
        lect = Lecturer.objects.filter(user=user).first()
        if lect and lect.department:
            return lect.department
    except Exception:
        pass

    # Fallback: Lecturer profile matched by email (Lecturer.department is a
    # real FK on the model -- use it).
    try:
        lect = Lecturer.objects.filter(email__iexact=(user.email or "")).first()
        if lect and lect.department:
            return lect.department
    except Exception:
        pass

    # last resort: user.org_role maybe maps to OrgRole.title which could include department
    try:
        org = getattr(user, "org_role", None)
        if org and "COD" in org.title.upper():
            # if OrgRole mapping includes department info in title like "COD - Computer Science"
            parts = org.title.split("-", 1)
            if len(parts) > 1:
                dept_name = parts[1].strip()
                return Department.objects.filter(name__icontains=dept_name).first()
    except Exception:
        pass

    return None


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def sr_action(request):
    """SR (Special Request) AJAX actions for the ODEL allocation panel —
    shared handler, see special_requests.panel_actions."""
    from special_requests.models import SpecialRequest
    from special_requests.panel_actions import SR_ACTIONS, handle_sr_action

    action = request.POST.get('action')
    if action not in SR_ACTIONS:
        return JsonResponse({'status': 'error', 'message': 'Unknown action'}, status=400)

    dept = detect_user_department(request.user)
    if not dept and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'No department associated with your account.'}, status=403)

    return handle_sr_action(
        request, action,
        dept=dept,
        allocation_model=ODELCourseAllocation,
        dept_scoped_qs=lambda d: (
            ODELCourseAllocation.objects.filter(program_course__program__department=d)
            if d else ODELCourseAllocation.objects.all()
        ),
        panel=SpecialRequest.PANEL_ODEL,
    )


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@ensure_csrf_cookie
def allocation_page(request):
    """
    Single page for all course allocation management
    """
    # Detect user's department
    user_department = detect_user_department(request.user)
    
    # Get filter parameters
    status_filter = request.GET.get('status', '')
    program_filter = request.GET.get('program', '')
    search_query = request.GET.get('search', '')
    
    # Base queryset
    allocations = ODELCourseAllocation.objects.select_related(
        'program_course__program__department',
        'program_course__program',
        'lecturer'
    ).all()
    
    # Filter by user's department if detected
    if user_department:
        allocations = allocations.filter(program_course__program__department=user_department)
    
    # Apply filters - Independent statuses
    if status_filter:
        if status_filter == 'pending_dvc':
            allocations = allocations.filter(submitted_to_dvc=False, rejected=False)
        elif status_filter == 'dvc_submitted':
            allocations = allocations.filter(submitted_to_dvc=True, rejected=False)
        elif status_filter == 'pending_tt':
            allocations = allocations.filter(submitted_to_tt=False, rejected=False)
        elif status_filter == 'tt_submitted':
            allocations = allocations.filter(submitted_to_tt=True, rejected=False)
        elif status_filter == 'both_submitted':
            allocations = allocations.filter(submitted_to_tt=True, submitted_to_dvc=True, rejected=False)
        elif status_filter == 'rejected':
            allocations = allocations.filter(rejected=True)
    
    if program_filter:
        allocations = allocations.filter(program_course__program_id=program_filter)
    
    if search_query:
        allocations = allocations.filter(
            Q(program_course__course_code__icontains=search_query) |
            Q(program_course__course_name__icontains=search_query) |
            Q(lecturer__name__icontains=search_query) |
            Q(lecturer__display_name__icontains=search_query)
        )
    
    # Get programs filtered by user's department if detected
    if user_department:
        programs = Program.objects.filter(department=user_department).order_by('name')
    else:
        programs = Program.objects.all().order_by('name')
    
    # Get ALL lecturers
    lecturers = Lecturer.objects.all()
    
    # Create a list of lecturers with computed display_name for the template
    lecturer_list = []
    for lecturer in lecturers:
        # Compute display name without trying to set the property
        if hasattr(lecturer, 'display_name'):
            display_name = lecturer.display_name
        elif hasattr(lecturer, 'name') and lecturer.name:
            if hasattr(lecturer, 'designation') and lecturer.designation:
                display_name = f"{lecturer.designation} {lecturer.name}"
            else:
                display_name = lecturer.name
        elif hasattr(lecturer, 'user') and lecturer.user:
            display_name = lecturer.user.get_full_name() or lecturer.user.username
        else:
            display_name = f"Lecturer {lecturer.id}"
        
        # Get department name
        dept_name = "No Department"
        if hasattr(lecturer, 'department') and lecturer.department:
            dept_name = lecturer.department.name
        
        # Add temporary attributes for template
        lecturer.temp_display_name = display_name
        lecturer.temp_department_name = dept_name
        lecturer_list.append(lecturer)
    
    # Stats for dashboard - Independent stats
    base_stats_query = ODELCourseAllocation.objects.all()
    if user_department:
        base_stats_query = base_stats_query.filter(program_course__program__department=user_department)
    
    stats = {
        'total': base_stats_query.count(),
        'pending_dvc': base_stats_query.filter(submitted_to_dvc=False, rejected=False).count(),
        'submitted_dvc': base_stats_query.filter(submitted_to_dvc=True, rejected=False).count(),
        'pending_tt': base_stats_query.filter(submitted_to_tt=False, rejected=False).count(),
        'submitted_tt': base_stats_query.filter(submitted_to_tt=True, rejected=False).count(),
        'both_submitted': base_stats_query.filter(submitted_to_tt=True, submitted_to_dvc=True, rejected=False).count(),
        'rejected': base_stats_query.filter(rejected=True).count(),
    }
    
    from core.rbac import user_has_role, Role
    is_odel_admin = request.user.is_superuser or user_has_role(
        request.user, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN
    )

    context = {
        'allocations': allocations,
        'programs': programs,
        'lecturers': lecturer_list,
        'stats': stats,
        'current_filters': {
            'status': status_filter,
            'program': program_filter,
            'search': search_query,
        },
        'user_department': user_department,
        'is_odel_admin': is_odel_admin,
        'all_departments': Department.objects.all().order_by('name') if is_odel_admin else None,
        'can_approve_dvc': request.user.has_perm('odel_system.approve_odel_allocation'),
        'can_forward_tt': request.user.has_perm('odel_system.forward_odel_to_timetable'),
    }
    return render(request, 'odel_system/allocation_page.html', context)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
@ensure_csrf_cookie
def allocation_create(request):
    """AJAX handler for creating allocation"""
    try:
        # Detect user's department for validation
        user_department = detect_user_department(request.user)
        
        data = json.loads(request.body)
        
        # Verify the program course belongs to user's department if department is detected
        if user_department:
            program_course = get_object_or_404(ProgramCourse, id=data['program_course'])
            if program_course.program.department != user_department:
                return JsonResponse({
                    'success': False, 
                    'error': 'You can only create allocations for courses in your department'
                })
        
        # Check if allocation already exists
        if ODELCourseAllocation.objects.filter(program_course_id=data['program_course']).exists():
            return JsonResponse({'success': False, 'error': 'Allocation for this course already exists'})
        
        allocation = ODELCourseAllocation.objects.select_related(
            'program_course__program', 'lecturer'
        ).create(
            program_course_id=data['program_course'],
            lecturer_id=data.get('lecturer') or None,
            number_of_students=data['number_of_students']
        )

        # Re-fetch so all related fields are populated after creation
        allocation.refresh_from_db()
        allocation = ODELCourseAllocation.objects.select_related(
            'program_course__program', 'lecturer'
        ).get(pk=allocation.pk)

        # Build lecturer display name the same way the view does it
        lecturer_name = ""
        if allocation.lecturer:
            lect = allocation.lecturer
            if hasattr(lect, 'display_name') and lect.display_name:
                lecturer_name = lect.display_name
            elif hasattr(lect, 'name') and lect.name:
                designation = getattr(lect, 'designation', '')
                lecturer_name = f"{designation} {lect.name}".strip() if designation else lect.name
            else:
                lecturer_name = f"Lecturer {lect.id}"

        return JsonResponse({
            'success': True,
            'message': f'Allocation for {allocation.course_code} created successfully',
            'allocation': {
                'id':                 allocation.id,
                'course_code':        allocation.course_code,
                'course_name':        allocation.course_name,
                'program_name':       allocation.program_course.program.name,
                'lecturer_name':      lecturer_name,
                'lecturer_id':        allocation.lecturer_id,
                'number_of_students': allocation.number_of_students,
            }
        })
    except Exception as e:
        logger.exception(f"Error in allocation_create: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
@ensure_csrf_cookie
def allocation_update(request, pk):
    """AJAX handler for updating allocation"""
    try:
        # Detect user's department for validation
        user_department = detect_user_department(request.user)
        
        allocation = get_object_or_404(ODELCourseAllocation, pk=pk)
        
        # Verify allocation belongs to user's department if department is detected
        if user_department and allocation.program_course.program.department != user_department:
            return JsonResponse({
                'success': False, 
                'error': 'You can only update allocations for your department'
            })
        
        data = json.loads(request.body)
        
        allocation.lecturer_id = data.get('lecturer') or None
        allocation.number_of_students = data['number_of_students']
        allocation.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Allocation updated successfully'
        })
    except Exception as e:
        logger.exception(f"Error in allocation_update: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
@ensure_csrf_cookie
def allocation_delete(request, pk):
    """AJAX handler for deleting allocation"""
    try:
        # Detect user's department for validation
        user_department = detect_user_department(request.user)
        
        allocation = get_object_or_404(ODELCourseAllocation, pk=pk)
        
        # Verify allocation belongs to user's department if department is detected
        if user_department and allocation.program_course.program.department != user_department:
            return JsonResponse({
                'success': False, 
                'error': 'You can only delete allocations for your department'
            })
        
        course_code = allocation.course_code
        allocation.delete()
        
        return JsonResponse({
            'success': True,
            'message': f'Allocation for {course_code} deleted'
        })
    except Exception as e:
        logger.exception(f"Error in allocation_delete: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
@ensure_csrf_cookie
def allocation_approve(request, pk):
    """AJAX handler for DVC approval - Independent from TT"""
    try:
        # Detect user's department for validation
        user_department = detect_user_department(request.user)
        
        allocation = get_object_or_404(ODELCourseAllocation, pk=pk)
        
        # Verify allocation belongs to user's department if department is detected
        if user_department and allocation.program_course.program.department != user_department:
            return JsonResponse({
                'success': False, 
                'error': 'You can only approve/reject allocations for your department'
            })
        
        data = json.loads(request.body)
        
        action = data.get('action')
        reason = data.get('reason', '')
        
        # Independent DVC approval - doesn't affect TT status
        if action == 'approve_dvc':
            if allocation.rejected:
                return JsonResponse({'success': False, 'error': 'Cannot approve a rejected allocation'})
            allocation.submitted_to_dvc = True
            message = f'{allocation.course_code} approved by DVC'
        elif action == 'reject':
            allocation.rejected = True
            allocation.reason_for_rejection = reason or 'No reason provided'
            message = f'{allocation.course_code} rejected'
        else:
            return JsonResponse({'success': False, 'error': 'Invalid action'})
        
        allocation.save()
        
        return JsonResponse({
            'success': True,
            'message': message,
            'status': allocation.status_label()
        })
    except Exception as e:
        logger.exception(f"Error in allocation_approve: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
@ensure_csrf_cookie
def allocation_forward_tt(request, pk):
    """AJAX handler for forwarding to timetable - Independent from DVC"""
    try:
        # Detect user's department for validation
        user_department = detect_user_department(request.user)
        
        allocation = get_object_or_404(ODELCourseAllocation, pk=pk)
        
        # Verify allocation belongs to user's department if department is detected
        if user_department and allocation.program_course.program.department != user_department:
            return JsonResponse({
                'success': False, 
                'error': 'You can only forward allocations for your department'
            })
        
        if allocation.rejected:
            return JsonResponse({'success': False, 'error': 'Rejected allocations cannot be forwarded'})
        if allocation.submitted_to_tt:
            return JsonResponse({'success': False, 'error': 'Allocation already submitted to timetable'})
        
        # Independent TT submission - doesn't affect DVC status
        allocation.submitted_to_tt = True
        allocation.save()
        
        return JsonResponse({
            'success': True,
            'message': f'{allocation.course_code} submitted to timetable'
        })
    except Exception as e:
        logger.exception(f"Error in allocation_forward_tt: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def get_program_courses(request):
    """AJAX endpoint to get courses for a program"""
    # Detect user's department for filtering
    user_department = detect_user_department(request.user)
    
    program_id = request.GET.get('program_id')
    if program_id:
        # Verify the program belongs to user's department if department is detected
        if user_department:
            program = get_object_or_404(Program, id=program_id)
            if program.department != user_department:
                return JsonResponse({'error': 'Access denied'}, status=403)
        
        courses = ProgramCourse.objects.filter(
            program_id=program_id
        ).values('id', 'course_code', 'course_name', 'year', 'semester')
        return JsonResponse(list(courses), safe=False)
    return JsonResponse([], safe=False)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def get_lecturers(request):
    """AJAX endpoint to get all active lecturers (optionally filtered by department)"""
    try:
        # Detect user's department for filtering
        user_department = detect_user_department(request.user)
        
        # Base queryset - get ALL active lecturers
        lecturers_qs = Lecturer.objects.filter(user__is_active=True)
        
        # Optional: Apply department filter only if user_department exists
        if user_department:
            # Check if Lecturer model has department field
            if hasattr(Lecturer, 'department'):
                lecturers_qs = lecturers_qs.filter(department=user_department)
        
        # Select related to optimize queries
        lecturers_qs = lecturers_qs.select_related('user')
        
        # Try to select department if the relation exists
        try:
            lecturers_qs = lecturers_qs.select_related('department')
        except Exception as e:
            logger.debug("select_related('department') unavailable on Lecturer: %s", e)
        
        # Build lecturer list with all available information
        lecturer_list = []
        for lecturer in lecturers_qs:
            # Get display name
            display_name = None
            if hasattr(lecturer, 'display_name') and lecturer.display_name:
                display_name = lecturer.display_name
            elif hasattr(lecturer, 'name') and lecturer.name:
                if hasattr(lecturer, 'designation') and lecturer.designation:
                    display_name = f"{lecturer.designation} {lecturer.name}"
                else:
                    display_name = lecturer.name
            else:
                display_name = f"Lecturer {lecturer.id}"
            
            # Get department name
            department_name = 'No Department'
            department_id = None
            if hasattr(lecturer, 'department'):
                if lecturer.department:
                    department_name = lecturer.department.name
                    department_id = lecturer.department.id
            
            # Get designation
            designation = getattr(lecturer, 'designation', '')
            
            # Get email
            email = getattr(lecturer, 'email', '')
            if not email and hasattr(lecturer, 'user') and lecturer.user:
                email = lecturer.user.email
            
            lecturer_dict = {
                'id': lecturer.id,
                'name': getattr(lecturer, 'name', ''),
                'display_name': display_name,
                'designation': designation,
                'email': email,
                'department': department_name,
                'department_id': department_id,
            }
            
            lecturer_list.append(lecturer_dict)
        
        return JsonResponse(lecturer_list, safe=False)
        
    except Exception as e:
        logger.exception(f"Error in get_lecturers: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def get_allocation_detail(request, pk):
    """AJAX endpoint to get allocation details for editing"""
    # Detect user's department for validation
    user_department = detect_user_department(request.user)
    
    allocation = get_object_or_404(ODELCourseAllocation, pk=pk)
    
    # Verify allocation belongs to user's department if department is detected
    if user_department and allocation.program_course.program.department != user_department:
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    data = {
        'id': allocation.id,
        'program_course_id': allocation.program_course_id,
        'course_code': allocation.course_code,
        'course_name': allocation.course_name,
        'lecturer_id': allocation.lecturer_id,
        'number_of_students': allocation.number_of_students,
        'program_id': allocation.program_course.program_id,
        'program_name': allocation.program.name,
    }
    return JsonResponse(data)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
@ensure_csrf_cookie
def bulk_submit_allocations(request):
    """
    Bulk submit allocations to TT or DVC - Independent workflows
    """
    try:
        user_department = detect_user_department(request.user)
        
        # Log the request for debugging
        logger.debug(f"Bulk submit request body: {request.body}")
        
        data = json.loads(request.body)
        
        allocation_ids = data.get('allocation_ids', [])
        target = data.get('target')  # 'tt' for timetable, 'dvc' for DVC
        
        if not allocation_ids:
            return JsonResponse({'success': False, 'error': 'No allocations selected'})
        
        if not target or target not in ['tt', 'dvc']:
            return JsonResponse({'success': False, 'error': 'Invalid submission target. Must be "tt" or "dvc"'})
        
        # Get the allocations
        allocations = ODELCourseAllocation.objects.filter(id__in=allocation_ids)
        
        if not allocations.exists():
            return JsonResponse({'success': False, 'error': 'No valid allocations found'})
        
        # Verify department access
        if user_department:
            for allocation in allocations:
                if allocation.program_course.program.department != user_department:
                    return JsonResponse({
                        'success': False,
                        'error': 'You can only submit allocations for your department'
                    })
        
        # Update allocations based on target - independent updates
        updated_count = 0
        skipped_count = 0
        
        for allocation in allocations:
            if not allocation.rejected:
                if target == 'tt':
                    if not allocation.submitted_to_tt:
                        allocation.submitted_to_tt = True
                        allocation.save()
                        updated_count += 1
                    else:
                        skipped_count += 1
                elif target == 'dvc':
                    if not allocation.submitted_to_dvc:
                        allocation.submitted_to_dvc = True
                        allocation.save()
                        updated_count += 1
                    else:
                        skipped_count += 1
            else:
                skipped_count += 1
        
        if updated_count == 0:
            target_name = "Timetable" if target == 'tt' else "DVC"
            return JsonResponse({
                'success': False,
                'error': f'No eligible allocations to submit to {target_name}. {skipped_count} allocation(s) were already submitted or rejected.'
            })
        
        target_name = "Timetable" if target == 'tt' else "DVC"
        message = f'Successfully submitted {updated_count} allocation(s) to {target_name}'
        if skipped_count > 0:
            message += f' ({skipped_count} skipped)'
        
        return JsonResponse({
            'success': True,
            'message': message
        })
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in bulk_submit_allocations: {str(e)}")
        return JsonResponse({'success': False, 'error': f'Invalid JSON: {str(e)}'})
    except Exception as e:
        logger.exception(f"Error in bulk_submit_allocations: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def submission_stats(request):
    """
    Get submission statistics - Independent stats for DVC and TT
    """
    try:
        user_department = detect_user_department(request.user)
        
        # Base queryset
        allocations = ODELCourseAllocation.objects.all()
        
        if user_department:
            allocations = allocations.filter(program_course__program__department=user_department)
        
        # Get draft counts if models exist
        class_drafts = 0
        exam_drafts = 0
        try:
            from odel_system.models import ODELTempTimetable, ODELExamTempTimetable
            class_drafts = ODELTempTimetable.objects.filter(
                course_allocation__in=allocations
            ).count()
            exam_drafts = ODELExamTempTimetable.objects.filter(
                course_allocation__in=allocations
            ).count()
        except ImportError:
            pass
        
        stats = {
            'total_allocations': allocations.count(),
            'submitted_tt': allocations.filter(submitted_to_tt=True).count(),
            'pending_tt': allocations.filter(submitted_to_tt=False, rejected=False).count(),
            'submitted_dvc': allocations.filter(submitted_to_dvc=True).count(),
            'pending_dvc': allocations.filter(submitted_to_dvc=False, rejected=False).count(),
            'both_submitted': allocations.filter(submitted_to_tt=True, submitted_to_dvc=True, rejected=False).count(),
            'rejected': allocations.filter(rejected=True).count(),
            'class_drafts': class_drafts,
            'exam_drafts': exam_drafts,
        }
        
        return JsonResponse(stats)
        
    except Exception as e:
        logger.exception(f"Error in submission_stats: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def download_allocations_pdf(request):
    """
    Generate and download PDF of department allocations
    Shows: Program Course, Lecturer, Number of Students, Submission Statuses
    """
    try:
        # Detect user's department
        user_department = detect_user_department(request.user)
        
        if not user_department:
            return HttpResponse("No department detected for user", status=400)
        
        # Get allocations for the department
        allocations = ODELCourseAllocation.objects.filter(
            program_course__program__department=user_department
        ).select_related(
            'program_course__program',
            'program_course',
            'lecturer'
        ).order_by('program_course__program__name', 'program_course__course_code')
        
        # Prepare data for template
        allocation_data = []
        for allocation in allocations:
            lecturer_name = "Not Assigned"
            if allocation.lecturer:
                if hasattr(allocation.lecturer, 'display_name'):
                    lecturer_name = allocation.lecturer.display_name
                elif hasattr(allocation.lecturer, 'name'):
                    lecturer_name = allocation.lecturer.name
            
            # Determine status display
            status = "Pending"
            if allocation.rejected:
                status = "Rejected"
            elif allocation.submitted_to_tt and allocation.submitted_to_dvc:
                status = "Both Submitted"
            elif allocation.submitted_to_tt:
                status = "Submitted to TT"
            elif allocation.submitted_to_dvc:
                status = "Approved by DVC"
            
            allocation_data.append({
                'program': allocation.program_course.program.name,
                'course_code': allocation.program_course.course_code,
                'course_name': allocation.program_course.course_name,
                'lecturer': lecturer_name,
                'students': allocation.number_of_students,
                'year': allocation.program_course.year,
                'semester': allocation.program_course.semester,
                'status': status,
                'dvc_submitted': allocation.submitted_to_dvc,
                'tt_submitted': allocation.submitted_to_tt,
            })
        
        # Group by program
        programs = {}
        for item in allocation_data:
            if item['program'] not in programs:
                programs[item['program']] = []
            programs[item['program']].append(item)
        
        # Get department info
        department_name = user_department.name
        current_date = timezone.now().strftime("%Y-%m-%d %H:%M")
        
        # Calculate totals
        total_allocations = len(allocation_data)
        total_students = sum(item['students'] for item in allocation_data)
        total_tt_submitted = sum(1 for item in allocation_data if item['tt_submitted'])
        total_dvc_submitted = sum(1 for item in allocation_data if item['dvc_submitted'])
        
        # HTML template for PDF
        html_string = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Department Allocations Report</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 40px;
                    color: #333;
                }}
                .header {{
                    text-align: center;
                    margin-bottom: 30px;
                    border-bottom: 2px solid #004d26;
                    padding-bottom: 20px;
                }}
                .header h1 {{
                    color: #004d26;
                    margin: 0;
                    font-size: 24px;
                }}
                .header h2 {{
                    color: #666;
                    margin: 10px 0 0;
                    font-size: 18px;
                    font-weight: normal;
                }}
                .header .date {{
                    color: #999;
                    font-size: 14px;
                    margin-top: 10px;
                }}
                .summary {{
                    background: #f5f5f5;
                    padding: 15px;
                    border-radius: 5px;
                    margin-bottom: 30px;
                    display: flex;
                    justify-content: space-around;
                    border: 1px solid #ddd;
                }}
                .summary-item {{
                    text-align: center;
                }}
                .summary-label {{
                    font-size: 14px;
                    color: #666;
                }}
                .summary-value {{
                    font-size: 24px;
                    font-weight: bold;
                    color: #004d26;
                }}
                .program-section {{
                    margin-bottom: 30px;
                    page-break-inside: avoid;
                }}
                .program-title {{
                    background: #004d26;
                    color: white;
                    padding: 10px 15px;
                    margin: 0;
                    border-radius: 5px 5px 0 0;
                    font-size: 18px;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 10px;
                }}
                th {{
                    background: #f0f0f0;
                    padding: 12px;
                    text-align: left;
                    font-size: 14px;
                    font-weight: bold;
                    border: 1px solid #ddd;
                }}
                td {{
                    padding: 10px 12px;
                    border: 1px solid #ddd;
                    font-size: 13px;
                }}
                .course-code {{
                    font-weight: bold;
                    color: #004d26;
                }}
                .lecturer-name {{
                    color: #555;
                }}
                .students-number {{
                    text-align: center;
                    font-weight: bold;
                }}
                .status-badge {{
                    display: inline-block;
                    padding: 3px 8px;
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: bold;
                }}
                .status-submitted {{
                    background: #d4edda;
                    color: #155724;
                }}
                .status-pending {{
                    background: #fff3cd;
                    color: #856404;
                }}
                .status-rejected {{
                    background: #f8d7da;
                    color: #721c24;
                }}
                .footer {{
                    margin-top: 40px;
                    text-align: center;
                    font-size: 12px;
                    color: #999;
                    border-top: 1px solid #ddd;
                    padding-top: 20px;
                }}
                .no-data {{
                    text-align: center;
                    padding: 40px;
                    color: #999;
                    font-style: italic;
                }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>Department Course Allocations Report</h1>
                <h2>{department_name}</h2>
                <div class="date">Generated on: {current_date}</div>
            </div>
            
            <div class="summary">
                <div class="summary-item">
                    <div class="summary-label">Total Programs</div>
                    <div class="summary-value">{len(programs)}</div>
                </div>
                <div class="summary-item">
                    <div class="summary-label">Total Allocations</div>
                    <div class="summary-value">{total_allocations}</div>
                </div>
                <div class="summary-item">
                    <div class="summary-label">Total Students</div>
                    <div class="summary-value">{total_students}</div>
                </div>
                <div class="summary-item">
                    <div class="summary-label">Submitted to TT</div>
                    <div class="summary-value">{total_tt_submitted}</div>
                </div>
                <div class="summary-item">
                    <div class="summary-label">DVC Approved</div>
                    <div class="summary-value">{total_dvc_submitted}</div>
                </div>
            </div>
        """
        
        # Add allocations by program
        if programs:
            for program_name, courses in programs.items():
                html_string += f"""
                <div class="program-section">
                    <div class="program-title">{program_name}</div>
                    <table>
                        <thead>
                            <tr>
                                <th>Course Code</th>
                                <th>Course Name</th>
                                <th>Year/Sem</th>
                                <th>Lecturer</th>
                                <th>Students</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                """
                
                for course in courses:
                    status_class = "status-submitted" if course['status'] != "Pending" and course['status'] != "Rejected" else "status-pending"
                    if course['status'] == "Rejected":
                        status_class = "status-rejected"
                    
                    html_string += f"""
                            <tr>
                                <td class="course-code">{course['course_code']}</td>
                                <td>{course['course_name']}</td>
                                <td>Year {course['year']}, Sem {course['semester']}</td>
                                <td class="lecturer-name">{course['lecturer']}</td>
                                <td class="students-number">{course['students']}</td>
                                <td><span class="status-badge {status_class}">{course['status']}</span></td>
                            </tr>
                    """
                
                # Add program subtotal
                program_total = sum(c['students'] for c in courses)
                html_string += f"""
                            <tr style="background: #f9f9f9; font-weight: bold;">
                                <td colspan="4" style="text-align: right;">Program Total Students:</td>
                                <td class="students-number">{program_total}</td>
                                <td></td>
                            </tr>
                        </tbody>
                    </table>
                </div>
                """
        else:
            html_string += f"""
            <div class="no-data">
                <p>No allocations found for {department_name}</p>
            </div>
            """
        
        html_string += f"""
            <div class="footer">
                <p>Chuka University - ODEL System | Department of {department_name}</p>
                <p>This report contains {total_allocations} course allocations with {total_students} total students</p>
            </div>
        </body>
        </html>
        """
        
        # Generate PDF
        try:
            from weasyprint import HTML
            html = HTML(string=html_string, base_url=request.build_absolute_uri())
            
            # Create HTTP response with PDF
            response = HttpResponse(content_type='application/pdf')
            
            # Sanitize filename
            dept_name = department_name.replace(' ', '_').replace('/', '_')
            filename = f"ODEL_Allocations_{dept_name}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            
            # Write PDF to response
            html.write_pdf(response)
            
            return response
        except ImportError:
            # Fallback if weasyprint is not installed
            return HttpResponse("PDF generation requires weasyprint. Please install it with: pip install weasyprint", status=500)
        
    except Exception as e:
        logger.exception(f"Error generating PDF: {str(e)}")
        return HttpResponse(f"Error generating PDF: {str(e)}", status=500)