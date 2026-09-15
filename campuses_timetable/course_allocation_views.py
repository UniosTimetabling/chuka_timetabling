from core.rbac import allowed_roles, Role
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.db.models import Q, Count, Sum
from django.template.loader import get_template
from weasyprint import HTML
from django.utils import timezone
import json
import logging
from django.conf import settings
from .models import CampusCourseAllocation, Campus
from program_management.models import Program, ProgramCourse, ProgramCode
from lecturer_portal.models import Lecturer
from department_management.models import Department
from django.contrib.auth.models import User
from special_requests.models import SpecialRequest
from special_requests.panel_actions import SR_ACTIONS, handle_sr_action

# Set up logging
logger = logging.getLogger(__name__)


def detect_user_department(user: User):
    """
    Detect department for COD user based on:
    0. OrgRole.department (authoritative — the only method that works for
       COD Admin accounts, which are never a department leader or lecturer)
    1. Department leader field
    2. Lecturer profile with department
    3. User groups/permissions
    """
    logger.info(f"Detecting department for user: {user.username} (ID: {user.id})")

    org = getattr(user, "org_role", None)
    if org and org.department_id:
        logger.info(f"Found department via OrgRole: {org.department.name}")
        return org.department

    # Method 1: Check if user is a department leader
    try:
        dept = Department.objects.filter(leader=user).first()
        if dept:
            logger.info(f"Found department via leader: {dept.name}")
            return dept
    except Exception as e:
        logger.error(f"Error checking department leader: {e}")

    # Method 2: Check lecturer profile
    try:
        lecturer = Lecturer.objects.filter(user=user).first()
        if lecturer:
            logger.info(f"Found lecturer profile: {lecturer}")
            # If Lecturer has department field, return it
            if hasattr(lecturer, 'department') and lecturer.department:
                logger.info(f"Lecturer has department: {lecturer.department.name}")
                return lecturer.department
    except Exception as e:
        logger.error(f"Error checking lecturer profile: {e}")

    # Method 2b: Lecturer profile matched by email (covers accounts whose
    # Lecturer record isn't linked via the user FK).
    try:
        lecturer = Lecturer.objects.filter(email__iexact=(user.email or "")).first()
        if lecturer and lecturer.department:
            logger.info(f"Found department via lecturer email match: {lecturer.department.name}")
            return lecturer.department
    except Exception as e:
        logger.error(f"Error checking lecturer profile by email: {e}")

    # Method 3: Check user groups for COD role and try to find department
    try:
        if user.groups.filter(name=Role.COD).exists():
            logger.info("User is in COD group")
            # Try to find department from lecturer profile
            lecturer = Lecturer.objects.filter(user=user).first()
            if lecturer and hasattr(lecturer, 'department') and lecturer.department:
                logger.info(f"Found department via COD group + lecturer: {lecturer.department.name}")
                return lecturer.department
    except Exception as e:
        logger.error(f"Error checking COD group: {e}")

    logger.warning(f"No department detected for user: {user.username}")
    return None


def is_cod_or_superuser(user):
    """Check if user is a COD or superuser"""
    if not user.is_authenticated:
        logger.warning("User is not authenticated")
        return False
    
    if user.is_superuser:
        logger.info(f"User {user.username} is superuser - access granted")
        return True
    
    # Check if user is in COD or COD Admin group
    is_cod = user.groups.filter(name__in=[Role.COD, Role.COD_ADMIN]).exists()
    if is_cod:
        logger.info(f"User {user.username} is in COD/COD Admin group - access granted")
        return True
    
    # Check if user has any department as leader
    has_dept = Department.objects.filter(leader=user).exists()
    if has_dept:
        logger.info(f"User {user.username} is department leader - access granted")
        return True

    # Check if user has an OrgRole with a department scope (covers COD Admin
    # helper accounts, which are never a literal Department.leader)
    org = getattr(user, "org_role", None)
    if org and org.department_id:
        logger.info(f"User {user.username} has OrgRole department scope - access granted")
        return True
    
    logger.warning(f"User {user.username} does not have COD access")
    return False


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def course_allocation_panel(request):
    """Main course allocation panel view for COD"""
    # Check if user has access
    if not is_cod_or_superuser(request.user):
        logger.warning(f"User {request.user.username} attempted to access panel without COD rights")
        messages.error(request, "You do not have permission to access this page.")
        return redirect('login')
    
    # Detect user's department
    user_department = detect_user_department(request.user)
    
    logger.info(f"User {request.user.username} accessing panel with department: {user_department}")
    
    # Get programs - filter by department if available
    if user_department and not request.user.is_superuser:
        programs = Program.objects.filter(department=user_department)
        logger.info(f"Found {programs.count()} programs for department {user_department.name}")
    else:
        programs = Program.objects.all()
        logger.info(f"Found {programs.count()} total programs")
    
    # Get lecturers - filter by department if available
    if user_department and not request.user.is_superuser:
        lecturers = Lecturer.objects.filter(department=user_department)
        logger.info(f"Found {lecturers.count()} lecturers for department {user_department.name}")
    else:
        lecturers = Lecturer.objects.all()
        logger.info(f"Found {lecturers.count()} total lecturers")
    
    # Get all departments for origin department selection
    departments = Department.objects.all()
    
    # Get all active campuses
    campuses = Campus.objects.filter(is_active=True)
    
    # Get existing allocations for this department.
    # Include allocations this department only *originated* (origin_department)
    # even though the record is owned/hosted by another department — e.g. a
    # Business Administration-origin course taught to Computer Science students
    # and owned by Computer Science. Without this, the record silently
    # disappears from the originating COD's panel the moment they set
    # themselves as the Origin Department, and they can never open it to
    # assign a lecturer.
    if user_department and not request.user.is_superuser:
        allocations = CampusCourseAllocation.objects.filter(
            Q(department=user_department) | Q(origin_department=user_department),
            rejected_by_dvc=False
        ).select_related('program', 'lecturer', 'origin_department', 'campus').order_by('course_code')
        
        rejected_count = CampusCourseAllocation.objects.filter(
            department=user_department,
            rejected_by_dvc=True
        ).count()
        
        logger.info(f"Found {allocations.count()} active allocations and {rejected_count} rejected for department")
    else:
        # Superuser sees all
        allocations = CampusCourseAllocation.objects.filter(
            rejected_by_dvc=False
        ).select_related('program', 'lecturer', 'origin_department', 'campus', 'department').order_by('course_code')
        
        rejected_count = CampusCourseAllocation.objects.filter(
            rejected_by_dvc=True
        ).count()
        
        logger.info(f"Superuser: Found {allocations.count()} total active allocations and {rejected_count} rejected")
    
    context = {
        'programs': programs,
        'lecturers': lecturers,
        'departments': departments,
        'campuses': campuses,
        'detected_dept': user_department,
        'allocations': allocations,
        'allocations_rejected_count': rejected_count,
        'allow_submission_to_dvc': True,
        'allow_submission_to_tt': True,
        'is_superuser': request.user.is_superuser,
        'is_campus_admin': request.user.is_superuser or request.user.groups.filter(
            name__in=[Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN]
        ).exists(),
        'user_groups': [g.name for g in request.user.groups.all()],  # For debugging
    }
    
    # Add debug info if in development
    if settings.DEBUG:
        context['debug_info'] = {
            'user_id': request.user.id,
            'username': request.user.username,
            'is_authenticated': request.user.is_authenticated,
            'is_superuser': request.user.is_superuser,
            'groups': [g.name for g in request.user.groups.all()],
            'has_department': user_department is not None,
            'department_name': user_department.name if user_department else None,
        }
    
    return render(request, 'campus/panel.html', context)





@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def allocation_action(request):
    """Handle AJAX requests for allocations"""
    action = request.POST.get('action')
    response = {'status': 'error', 'message': 'Invalid action'}
    
    # Detect user's department
    user_department = detect_user_department(request.user)

    # ---- SR (Special Request) actions — shared handler, same 5 actions ----
    # available on every allocation panel (see special_requests.panel_actions).
    if action in SR_ACTIONS:
        if not user_department and not request.user.is_superuser:
            return JsonResponse({'status': 'error', 'message': 'No department associated with your account.'}, status=403)
        return handle_sr_action(
            request, action,
            dept=user_department,
            allocation_model=CampusCourseAllocation,
            dept_scoped_qs=lambda d: CampusCourseAllocation.objects.filter(
                Q(department=d) | Q(origin_department=d)
            ) if d else CampusCourseAllocation.objects.all(),
            panel=SpecialRequest.PANEL_CAMPUSES,
        )

    if action == 'create_allocation':
        # Create or update allocation
        allocation_id = request.POST.get('id')
        course_code = request.POST.get('course_code')
        course_name = request.POST.get('course_name')
        program_id = request.POST.get('program_id')
        origin_dept_id = request.POST.get('origin_department_id')
        lecturer_id = request.POST.get('lecturer_id')
        num_students = request.POST.get('number_of_students')
        campus_id = request.POST.get('campus_id')
        teaching_campus_id = request.POST.get('teaching_campus_id')
        delivery_mode = request.POST.get('delivery_mode', 'PHYSICAL')

        # ── Tracker fields ────────────────────────────────────────────────
        academic_year = request.POST.get('academic_year', '').strip()
        allocation_semester = request.POST.get('allocation_semester')
        program_year = request.POST.get('program_year')
        is_special_course = request.POST.get('is_special_course', 'false').lower() == 'true'
        # ─────────────────────────────────────────────────────────────────
        
        try:
            # Verify program belongs to user's department
            if program_id and user_department and not request.user.is_superuser:
                program = get_object_or_404(Program, id=program_id)
                if program.department != user_department:
                    return JsonResponse({'status': 'error', 'message': 'You can only allocate courses for your department'})
            
            if allocation_id:
                # Update existing
                allocation = get_object_or_404(CampusCourseAllocation, id=allocation_id)

                # An "origin department" collaborator (e.g. Business Admin on a
                # course that Computer Science owns and originated from them) is
                # allowed in here purely to assign/change the lecturer or student
                # count -- they do NOT own this allocation. Previously any save
                # attempt by anyone other than the literal owning department was
                # rejected outright, so a collaborator could never open the record
                # to assign a lecturer at all. Now: the true owning department (or
                # a superuser/no detected department) may change every field; a
                # pure origin-department collaborator may only touch the lecturer
                # and student count, and every other field they submit is ignored.
                is_true_owner = (
                    not user_department
                    or allocation.department_id == user_department.id
                    or request.user.is_superuser
                )
                is_origin_collaborator = (
                    user_department and allocation.origin_department_id == user_department.id
                )
                if not is_true_owner and not is_origin_collaborator:
                    return JsonResponse({'status': 'error', 'message': 'You can only edit allocations for your department'})
            else:
                # Create new
                is_true_owner = True
                allocation = CampusCourseAllocation()
                allocation.department = user_department
                allocation.approved_by_dvc = False
                allocation.rejected_by_dvc = False
                allocation.submitted_to_tt = False

            if is_true_owner:
                allocation.course_code = course_code
                allocation.course_name = course_name

                if program_id:
                    allocation.program_id = program_id

                if origin_dept_id:
                    allocation.origin_department_id = origin_dept_id

                # Set campus fields
                if campus_id:
                    allocation.campus_id = campus_id
                else:
                    default_campus = Campus.get_default_campus()
                    if default_campus:
                        allocation.campus = default_campus

                if teaching_campus_id:
                    allocation.teaching_campus_id = teaching_campus_id

                allocation.delivery_mode = delivery_mode

                # ── Tracker fields ────────────────────────────────────────────
                from .tracker_service import _current_academic_year
                import re as _re

                if academic_year:
                    allocation.academic_year = academic_year
                elif not allocation.academic_year:
                    allocation.academic_year = _current_academic_year()

                if allocation_semester:
                    try:
                        allocation.allocation_semester = int(allocation_semester)
                    except (ValueError, TypeError):
                        pass

                if program_year:
                    try:
                        allocation.program_year = int(program_year)
                    except (ValueError, TypeError):
                        pass
                elif not allocation.program_year:
                    # Infer from course code (e.g. COSC341 → year 3)
                    _match = _re.search(r'\d{3,4}', course_code or '')
                    if _match:
                        _digit = int(_match.group(0)[0])
                        if 1 <= _digit <= 6:
                            allocation.program_year = _digit

                allocation.is_special_course = is_special_course
                # ─────────────────────────────────────────────────────────────

            # Lecturer + student count: the whole point of collaborator access,
            # so these are always allowed regardless of ownership.
            if lecturer_id:
                allocation.lecturer_id = lecturer_id
            else:
                allocation.lecturer = None

            allocation.number_of_students = num_students or 0

            allocation.save()

            response = {
                'status': 'success',
                'message': 'Allocation saved successfully',
                'id': allocation.id
            }
        except Exception as e:
            response = {'status': 'error', 'message': str(e)}
    
    elif action == 'allocation_detail':
        # Get allocation details for editing
        allocation_id = request.POST.get('id')
        try:
            allocation = CampusCourseAllocation.objects.get(id=allocation_id)
            # View access: the true owning department, OR an origin-department
            # collaborator opening it purely to assign a lecturer. Save-time
            # restrictions (see create_allocation) still apply regardless.
            is_true_owner = (
                not user_department
                or allocation.department_id == user_department.id
                or request.user.is_superuser
            )
            is_origin_collaborator = (
                user_department and allocation.origin_department_id == user_department.id
            )
            if not is_true_owner and not is_origin_collaborator:
                return JsonResponse({'status': 'error', 'message': 'Access denied'})
            
            response = {
                'status': 'success',
                'allocation': {
                    'id': allocation.id,
                    'course_code': allocation.course_code,
                    'course_name': allocation.course_name,
                    'program_id': allocation.program_id if allocation.program else '',
                    'origin_department_id': allocation.origin_department_id,
                    'lecturer_id': allocation.lecturer_id if allocation.lecturer else '',
                    'number_of_students': allocation.number_of_students,
                    'campus_id': allocation.campus_id if allocation.campus else '',
                    'teaching_campus_id': allocation.teaching_campus_id if allocation.teaching_campus else '',
                    'delivery_mode': allocation.delivery_mode,
                    # ── Tracker fields ────────────────────────────────
                    'academic_year': allocation.academic_year or '',
                    'program_year': allocation.program_year or '',
                    'allocation_semester': allocation.allocation_semester or '',
                    'is_special_course': allocation.is_special_course,
                    'is_true_owner': is_true_owner,
                }
            }
        except CampusCourseAllocation.DoesNotExist:
            response = {'status': 'error', 'message': 'Allocation not found'}
    
    elif action == 'delete_allocation':
        # Delete allocation — allow the true owning department, or the
        # origin-department collaborator who originated this cross-dept course.
        allocation_id = request.POST.get('id')
        try:
            allocation = CampusCourseAllocation.objects.get(id=allocation_id)
            is_owner_or_origin = (
                not user_department
                or allocation.department_id == user_department.id
                or allocation.origin_department_id == user_department.id
                or request.user.is_superuser
            )
            if not is_owner_or_origin:
                return JsonResponse({'status': 'error', 'message': 'Access denied'})
            
            allocation.delete()
            response = {'status': 'success', 'message': 'Allocation deleted'}
        except CampusCourseAllocation.DoesNotExist:
            response = {'status': 'error', 'message': 'Allocation not found'}
    
    elif action == 'list_rejected':
        # Get rejected allocations
        if user_department and not request.user.is_superuser:
            rejected = CampusCourseAllocation.objects.filter(
                department=user_department,
                rejected_by_dvc=True
            ).select_related('program', 'lecturer', 'campus').order_by('-updated_at')
        else:
            rejected = CampusCourseAllocation.objects.filter(
                rejected_by_dvc=True
            ).select_related('program', 'lecturer', 'campus').order_by('-updated_at')
        
        rejected_list = []
        for r in rejected:
            rejected_list.append({
                'id': r.id,
                'course_code': r.course_code,
                'course_name': r.course_name,
                'program': r.program.name if r.program else None,
                'lecturer': r.lecturer.display_name if r.lecturer else None,
                'number_of_students': r.number_of_students,
                'reason': r.reason_for_disapproval,
                'campus': r.campus.code if r.campus else None,
            })
        
        response = {
            'status': 'success',
            'rejected': rejected_list
        }
    
    elif action == 'restore_rejected':
        # Restore rejected allocation
        allocation_id = request.POST.get('id')
        try:
            allocation = CampusCourseAllocation.objects.get(id=allocation_id)
            # Verify allocation belongs to user's department
            if user_department and allocation.department != user_department and not request.user.is_superuser:
                return JsonResponse({'status': 'error', 'message': 'Access denied'})
            
            allocation.rejected_by_dvc = False
            allocation.reason_for_disapproval = "No reason yet"
            allocation.save()
            response = {'status': 'success', 'message': 'Allocation restored'}
        except CampusCourseAllocation.DoesNotExist:
            response = {'status': 'error', 'message': 'Allocation not found'}
    
    elif action == 'delete_rejected':
        # Delete rejected allocation
        allocation_id = request.POST.get('id')
        try:
            allocation = CampusCourseAllocation.objects.get(id=allocation_id)
            # Verify allocation belongs to user's department
            if user_department and allocation.department != user_department and not request.user.is_superuser:
                return JsonResponse({'status': 'error', 'message': 'Access denied'})
            
            allocation.delete()
            response = {'status': 'success', 'message': 'Rejected allocation deleted'}
        except CampusCourseAllocation.DoesNotExist:
            response = {'status': 'error', 'message': 'Allocation not found'}

    elif action == 'list_allocations':
        # Lightweight list used by the frontend after an offline-queue flush
        # Returns enough data for the table to re-render without a page reload.
        if user_department and not request.user.is_superuser:
            qs = CampusCourseAllocation.objects.filter(
                Q(department=user_department) | Q(origin_department=user_department),
                rejected_by_dvc=False
            ).select_related('program', 'lecturer', 'origin_department', 'campus')
        else:
            qs = CampusCourseAllocation.objects.filter(
                rejected_by_dvc=False
            ).select_related('program', 'lecturer', 'origin_department', 'campus', 'department')

        allocations_list = []
        for a in qs.order_by('course_code'):
            allocations_list.append({
                'id': a.id,
                'course_code': a.course_code,
                'course_name': a.course_name,
                'program_id': a.program_id or '',
                'program_label': a.program.name if a.program else '-',
                'origin_department_id': a.origin_department_id or '',
                'origin_dept_label': a.origin_department.name if a.origin_department else '-',
                'lecturer_id': a.lecturer_id or '',
                'lecturer_label': a.lecturer.display_name if a.lecturer else '',
                'campus_id': a.campus_id or '',
                'campus_label': a.campus.code if a.campus else '-',
                'number_of_students': a.number_of_students,
                'delivery_mode': a.delivery_mode,
            })
        response = {'status': 'success', 'allocations': allocations_list}

    return JsonResponse(response)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def api_course_codes(request):
    """API endpoint to get course codes from ProgramCourse"""
    # Detect user's department
    user_department = detect_user_department(request.user)
    
    program_id = request.GET.get('program_id')
    search = request.GET.get('search', '')
    
    courses = ProgramCourse.objects.all()
    
    # Filter by user's department if not superuser
    if user_department and not request.user.is_superuser:
        courses = courses.filter(program__department=user_department)
    
    if program_id:
        courses = courses.filter(program_id=program_id)
    
    if search:
        courses = courses.filter(
            Q(course_code__icontains=search) | 
            Q(course_name__icontains=search)
        )
    
    # Limit results
    courses = courses[:50]
    
    course_list = []
    for course in courses:
        course_list.append({
            'code': course.course_code,
            'name': course.course_name,
            'program': course.program.name,
        })
    
    return JsonResponse({'courses': course_list})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def get_course_name(request):
    """Get course name from ProgramCourse"""
    # Detect user's department
    user_department = detect_user_department(request.user)
    
    code = request.GET.get('code', '').strip()
    program_id = request.GET.get('program_id')
    
    if not code:
        return JsonResponse({'found': False})
    
    query = ProgramCourse.objects.filter(course_code__iexact=code)
    
    # Filter by user's department if not superuser
    if user_department and not request.user.is_superuser:
        query = query.filter(program__department=user_department)
    
    if program_id:
        query = query.filter(program_id=program_id)
    
    course = query.first()
    
    if course:
        return JsonResponse({
            'found': True,
            'name': course.course_name,
            'program': course.program.name,
        })
    
    return JsonResponse({'found': False})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def toggle_submission_to_dvc(request):
    """Toggle DVC submission setting"""
    return JsonResponse({'status': 'success', 'allow_submission_to_dvc': True})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def toggle_submission_to_tt(request):
    """Toggle Timetable submission setting"""
    return JsonResponse({'status': 'success', 'allow_submission_to_tt': True})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def download_allocations_pdf(request):
    """
    Generate and download PDF of department course allocations
    """
    try:
        # Detect user's department
        user_department = detect_user_department(request.user)
        
        if not user_department and not request.user.is_superuser:
            return HttpResponse("No department detected for user", status=400)
        
        # Get allocations based on user's department
        if user_department and not request.user.is_superuser:
            allocations = CampusCourseAllocation.objects.filter(
                department=user_department,
                rejected_by_dvc=False
            ).select_related(
                'program',
                'lecturer',
                'origin_department',
                'campus'
            ).order_by('program__name', 'course_code')
            department_name = user_department.name
        else:
            # Superuser sees all or filter by request
            dept_id = request.GET.get('department')
            if dept_id:
                allocations = CampusCourseAllocation.objects.filter(
                    department_id=dept_id,
                    rejected_by_dvc=False
                ).select_related(
                    'program', 'lecturer', 'origin_department', 'campus', 'department'
                ).order_by('program__name', 'course_code')
                dept = Department.objects.get(id=dept_id)
                department_name = dept.name
            else:
                allocations = CampusCourseAllocation.objects.filter(
                    rejected_by_dvc=False
                ).select_related(
                    'program', 'lecturer', 'origin_department', 'campus', 'department'
                ).order_by('department__name', 'program__name', 'course_code')
                department_name = "All Departments"
        
        # Prepare data for template
        allocation_data = []
        for allocation in allocations:
            allocation_data.append({
                'department': allocation.department.name if allocation.department else 'N/A',
                'program': allocation.program.name if allocation.program else 'N/A',
                'course_code': allocation.course_code,
                'course_name': allocation.course_name,
                'origin_dept': allocation.origin_department.name if allocation.origin_department else 'N/A',
                'lecturer': allocation.lecturer.display_name if allocation.lecturer else 'Not Assigned',
                'students': allocation.number_of_students,
                'campus': allocation.campus.code if allocation.campus else 'N/A',
                'delivery_mode': allocation.get_delivery_mode_display(),
            })
        
        # Group by department and program
        departments = {}
        for item in allocation_data:
            dept_name = item['department']
            if dept_name not in departments:
                departments[dept_name] = {}
            
            prog_name = item['program']
            if prog_name not in departments[dept_name]:
                departments[dept_name][prog_name] = []
            
            departments[dept_name][prog_name].append(item)
        
        current_date = timezone.now().strftime("%Y-%m-%d %H:%M")
        
        # Calculate totals
        total_allocations = len(allocation_data)
        total_students = sum(item['students'] for item in allocation_data)
        
        # HTML template for PDF
        html_string = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Course Allocations Report</title>
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
                .dept-section {{
                    margin-bottom: 40px;
                    page-break-inside: avoid;
                }}
                .dept-title {{
                    background: #004d26;
                    color: white;
                    padding: 10px 15px;
                    margin: 0 0 15px 0;
                    border-radius: 5px;
                    font-size: 18px;
                }}
                .program-title {{
                    background: #e8f5e9;
                    color: #004d26;
                    padding: 8px 12px;
                    margin: 15px 0 10px 0;
                    border-left: 4px solid #004d26;
                    font-size: 16px;
                    font-weight: bold;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-bottom: 15px;
                }}
                th {{
                    background: #f0f0f0;
                    padding: 10px;
                    text-align: left;
                    font-size: 13px;
                    font-weight: bold;
                    border: 1px solid #ddd;
                }}
                td {{
                    padding: 8px 10px;
                    border: 1px solid #ddd;
                    font-size: 12px;
                }}
                .course-code {{
                    font-weight: bold;
                    color: #004d26;
                }}
                .students-number {{
                    text-align: center;
                    font-weight: bold;
                }}
                .campus-badge {{
                    display: inline-block;
                    padding: 2px 6px;
                    background-color: #e8f5e9;
                    color: #2e7d32;
                    border-radius: 10px;
                    font-size: 11px;
                    border: 1px solid #a5d6a7;
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
                <h1>Course Allocations Report</h1>
                <h2>{department_name}</h2>
                <div class="date">Generated on: {current_date}</div>
            </div>
            
            <div class="summary">
                <div class="summary-item">
                    <div class="summary-label">Total Departments</div>
                    <div class="summary-value">{len(departments)}</div>
                </div>
                <div class="summary-item">
                    <div class="summary-label">Total Allocations</div>
                    <div class="summary-value">{total_allocations}</div>
                </div>
                <div class="summary-item">
                    <div class="summary-label">Total Students</div>
                    <div class="summary-value">{total_students}</div>
                </div>
            </div>
        """
        
        # Add allocations by department and program
        if departments:
            for dept_name, programs in departments.items():
                dept_total_students = sum(
                    course['students'] 
                    for prog_courses in programs.values() 
                    for course in prog_courses
                )
                
                html_string += f"""
                <div class="dept-section">
                    <div class="dept-title">{dept_name} (Total Students: {dept_total_students})</div>
                """
                
                for program_name, courses in programs.items():
                    program_total = sum(c['students'] for c in courses)
                    
                    html_string += f"""
                    <div class="program-title">{program_name} - Total: {program_total} students</div>
                    <table>
                        <thead>
                            <tr>
                                <th>Course Code</th>
                                <th>Course Name</th>
                                <th>Origin Dept</th>
                                <th>Lecturer</th>
                                <th>Campus</th>
                                <th>Mode</th>
                                <th>Students</th>
                            </tr>
                        </thead>
                        <tbody>
                    """
                    
                    for course in courses:
                        html_string += f"""
                            <tr>
                                <td class="course-code">{course['course_code']}</td>
                                <td>{course['course_name']}</td>
                                <td>{course['origin_dept']}</td>
                                <td>{course['lecturer']}</td>
                                <td><span class="campus-badge">{course['campus']}</span></td>
                                <td>{course['delivery_mode']}</td>
                                <td class="students-number">{course['students']}</td>
                            </tr>
                        """
                    
                    html_string += """
                        </tbody>
                    </table>
                    """
                
                html_string += "</div>"
        else:
            html_string += f"""
            <div class="no-data">
                <p>No allocations found</p>
            </div>
            """
        
        html_string += f"""
            <div class="footer">
                <p>Chuka University - Campus Timetable System</p>
                <p>This report contains {total_allocations} course allocations with {total_students} total students</p>
            </div>
        </body>
        </html>
        """
        
        # Generate PDF
        html = HTML(string=html_string, base_url=request.build_absolute_uri())
        
        # Create HTTP response with PDF
        response = HttpResponse(content_type='application/pdf')
        
        # Sanitize filename
        safe_name = department_name.replace(' ', '_').replace('/', '_')
        filename = f"Course_Allocations_{safe_name}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        # Write PDF to response
        html.write_pdf(response)
        
        return response
        
    except Exception as e:
        return HttpResponse(f"Error generating PDF: {str(e)}", status=500)



# ──────────────────────────────────────────────────────────────────────────────
# Program Year Tracker Views
# ──────────────────────────────────────────────────────────────────────────────

@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def tracker_dashboard(request):
    """
    Display the program year tracker dashboard for the COD.
    Shows per-program allocation progress, missing courses, and carryover gaps.
    """
    if not is_cod_or_superuser(request.user):
        messages.error(request, "You do not have permission to access this page.")
        return redirect('login')

    from .tracker_service import generate_full_program_report, _current_academic_year
    from program_management.models import Program

    user_department = detect_user_department(request.user)

    if user_department and not request.user.is_superuser:
        programs = Program.objects.filter(department=user_department)
    else:
        programs = Program.objects.all()

    academic_year = request.GET.get('academic_year', _current_academic_year())

    # Build one report per program
    program_reports = []
    for program in programs:
        report = generate_full_program_report(program, academic_year)
        program_reports.append(report)

    context = {
        'program_reports': program_reports,
        'academic_year': academic_year,
        'detected_dept': user_department,
        'is_superuser': request.user.is_superuser,
    }
    return render(request, 'campus/tracker_dashboard.html', context)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def tracker_api(request):
    """
    JSON API for tracker data.
    GET params: program_id, academic_year
    """
    if not is_cod_or_superuser(request.user):
        return JsonResponse({'status': 'error', 'message': 'Access denied'}, status=403)

    from .tracker_service import generate_full_program_report, _current_academic_year
    from program_management.models import Program

    program_id = request.GET.get('program_id')
    academic_year = request.GET.get('academic_year', _current_academic_year())

    if not program_id:
        return JsonResponse({'status': 'error', 'message': 'program_id required'}, status=400)

    try:
        program = Program.objects.get(pk=program_id)
        report = generate_full_program_report(program, academic_year)

        # Serialize for JSON
        years_json = []
        for yr_data in report['years']:
            semesters_json = []
            for sem_data in yr_data['semesters']:
                semesters_json.append({
                    'semester': sem_data['semester'],
                    'expected': sem_data['expected'],
                    'allocated': sem_data['allocated'],
                    'missing': sem_data['missing'],
                    'carryover': sem_data['carryover'],
                    'is_complete': sem_data['is_complete'],
                    'report_text': sem_data['report_text'],
                    'has_tracker': sem_data['tracker'] is not None,
                })
            years_json.append({
                'program_year': yr_data['program_year'],
                'semesters': semesters_json,
                'year_missing': yr_data['year_missing'],
                'year_complete': yr_data['year_complete'],
            })

        return JsonResponse({
            'status': 'success',
            'program': program.name,
            'academic_year': report['academic_year'],
            'started_from_year': report['started_from_year'],
            'years': years_json,
            'overall_missing': report['overall_missing'],
            'overall_complete': report['overall_complete'],
        })
    except Program.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Program not found'}, status=404)
    except Exception as e:
        logger.error(f"Tracker API error: {e}", exc_info=True)
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def resend_gap_notification(request, tracker_id):
    """Force-resend a gap notification for a specific tracker record."""
    if not is_cod_or_superuser(request.user):
        return JsonResponse({'status': 'error', 'message': 'Access denied'}, status=403)

    from .models import ProgramYearTracker
    from .tracker_service import send_gap_notification

    try:
        tracker = ProgramYearTracker.objects.get(pk=tracker_id)
        tracker.refresh_from_allocations()
        gap_report = send_gap_notification(tracker, force=True)
        return JsonResponse({
            'status': 'success',
            'message': 'Notification re-sent successfully',
            'report_id': gap_report.pk if gap_report else None,
        })
    except ProgramYearTracker.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Tracker not found'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)