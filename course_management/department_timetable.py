import logging
from collections import defaultdict
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, user_has_role, Role
from django.http import JsonResponse
from django.db.models import Q
from django.core.cache import cache
from datetime import datetime, time
import json
from django.contrib.auth.models import Group
from django.contrib import messages

from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, Table

from timetable.models import (
    Timetable,
    ExamTimetable,
    LabTimetable,
    LabExamTimetable
)
from timetable.analysis_reports import (
    _get_template_config,
    _styles,
    _letterhead,
    _pdf_response,
    _standard_table_style,
    _day_grid_tables,
    _full_name,
)
from course_allocation.models import CourseAllocation, SubmissionControl, CombinedCourseGroup
from room_management.models import Venue
from department_management.models import Department
from lecturer_portal.models import Lecturer
from program_management.models import Program, ProgramCourse, ProgramCode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------
# SECURITY FIX #1 – Rate-limit helper
# ---------------------------------------------------------------
def _get_client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "0.0.0.0")


def _check_rate_limit(request, scope: str, limit: int = 60, window: int = 60) -> bool:
    uid  = request.user.pk if request.user.is_authenticated else "anon"
    ip   = _get_client_ip(request)
    key  = f"rl:{scope}:{uid}:{ip}"
    count = cache.get(key, 0)
    if count >= limit:
        return True
    cache.set(key, count + 1, timeout=window)
    return False


def _rate_limited_json():
    return JsonResponse(
        {"error": "Too many requests. Please slow down."},
        status=429,
    )


# ============================
# Enhanced Department Detection
# ============================

def detect_user_department(user):
    """
    Enhanced function to detect which department the user belongs to.
    Tries multiple methods in order of reliability.
    """
    if not user.is_authenticated:
        return None

    # Method 0: OrgRole.department — direct, authoritative link set at
    # account-creation time (see core.rbac.link_department_scope). This is
    # the ONLY method that resolves a "COD Admin" account, which is never
    # Department.leader and never has a Lecturer profile.
    org = getattr(user, "org_role", None)
    if org and org.department_id:
        return org.department

    # Method 1: Direct department leader
    dept = Department.objects.filter(leader=user).first()
    if dept:
        return dept

    # Method 2: User profile department
    if hasattr(user, 'profile') and hasattr(user.profile, 'department') and user.profile.department:
        return user.profile.department

    # Method 3: Lecturer model department
    lecturer = Lecturer.objects.filter(user=user).first()
    if lecturer and hasattr(lecturer, 'department') and lecturer.department:
        return lecturer.department

    # Method 4: Course allocation as lecturer
    allocation = CourseAllocation.objects.filter(
        lecturer__user=user
    ).first()
    if allocation and allocation.department:
        return allocation.department

    # Method 5: Check email in lecturer records
    if user.email:
        lecturer_by_email = Lecturer.objects.filter(
            Q(email__iexact=user.email) |
            Q(user__email__iexact=user.email)
        ).first()
        if lecturer_by_email and hasattr(lecturer_by_email, 'department') and lecturer_by_email.department:
            return lecturer_by_email.department

    # Method 6: Department-specific user groups
    user_groups = user.groups.all()

    for group in user_groups:
        if group.name.startswith('COD_'):
            dept_identifier = group.name.replace('COD_', '', 1)
            dept_name = dept_identifier.replace('_', ' ')
            dept = Department.objects.filter(
                Q(name__iexact=dept_name) |
                Q(name__icontains=dept_name)
            ).first()
            if dept:
                return dept

    # Method 7: Generic department groups
    for group in user_groups:
        dept = Department.objects.filter(
            Q(name__iexact=group.name) |
            Q(name__icontains=group.name)
        ).first()
        if dept:
            return dept

    # Method 8: Session-based department selection
    # SECURITY FIX #2 – Removed the dangerous session_key / SessionStore lookup.
    # Accessing another session via user.session_key is not a supported Django
    # pattern and can lead to session-fixation or privilege-escalation bugs.
    # Department stored in the *current* request's session is checked elsewhere
    # (get_department_timetable_data, etc.) using request.session directly.

    return None


def detect_user_departments(user):
    """
    Returns all departments a user has access to (for users with multiple departments)
    """
    departments = []

    dept = Department.objects.filter(leader=user).first()
    if dept and dept not in departments:
        departments.append(dept)

    lecturer = Lecturer.objects.filter(user=user).first()
    if lecturer and hasattr(lecturer, 'department') and lecturer.department and lecturer.department not in departments:
        departments.append(lecturer.department)

    allocation_depts = Department.objects.filter(
        allocations__lecturer__user=user
    ).distinct()
    for dept in allocation_depts:
        if dept not in departments:
            departments.append(dept)

    user_groups = user.groups.all()
    for group in user_groups:
        if group.name.startswith('COD_'):
            dept_identifier = group.name.replace('COD_', '', 1)
            dept_name = dept_identifier.replace('_', ' ')

            possible_depts = Department.objects.filter(
                Q(name__iexact=dept_name) |
                Q(name__icontains=dept_name)
            )
            for dept in possible_depts:
                if dept not in departments:
                    departments.append(dept)

    return departments


# ---------------------------------------------------------------
# SECURITY FIX #3 – Department ownership verifier
# ---------------------------------------------------------------
def _user_owns_department(user, department) -> bool:
    """Return True when `department` appears in the user's allowed departments."""
    return department in detect_user_departments(user)


# ---------------------------------------------------------------
# Department scope + combined-group display helpers
# ---------------------------------------------------------------
def _department_allocation_filter(department):
    """
    Which CourseAllocation rows belong to a department's own timetable.

    A course belongs on a department's timetable if that department is
    EITHER:
      - the administratively allocating department
        (CourseAllocation.department), OR
      - the home department of the assigned lecturer
        (CourseAllocation.lecturer.department) — so a lecturer's own
        department sees everything they teach, even a course allocated
        under a different department's program, OR
      - the originating/servicing department
        (CourseAllocation.origin_department) — so a department that
        services a course taught under another department's program still
        sees it on its own timetable, not only on the allocating
        department's.

    These three overlap for the common case (all three point at the same
    department) but each can independently be the only match, so they're
    combined with OR rather than any one replacing the others.
    """
    return (
        Q(department=department) |
        Q(lecturer__department=department) |
        Q(origin_department=department)
    )


def _combined_group_display_map(alloc_ids):
    """
    {allocation_id: group_display_name} for every allocation that belongs to
    a CombinedCourseGroup, so the departmental timetable shows the group's
    own name (e.g. "COSC 103" — the name given when the courses were
    combined, via CombinedCourseGroup.display_name()) instead of each
    member's own individual course code (e.g. "COSC 101 A", "COSC 101 B").
    Allocations with no combined group simply won't appear in the returned
    map — callers should fall back to the allocation's own course_code.
    """
    display_map = {}
    alloc_ids = [aid for aid in alloc_ids if aid]
    if not alloc_ids:
        return display_map
    for group in CombinedCourseGroup.objects.filter(
        allocations__id__in=alloc_ids
    ).prefetch_related('allocations').only('id', 'group_code', 'base_course_code'):
        name = group.display_name()
        for member_id in group.allocations.values_list('id', flat=True):
            display_map[member_id] = name
    return display_map


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def department_timetable_view(request):
    """
    Department timetable page – shows only the logged-in user's department data.
    """
    user = request.user
    department = detect_user_department(user)

    if not department:
        user_depts = detect_user_departments(user)
        if len(user_depts) == 0:
            messages.warning(request, "No department assigned to your account. Please contact administrator.")
            return redirect('cod_panel')
        elif len(user_depts) == 1:
            department = user_depts[0]
        else:
            return redirect('select_department')

    try:
        control = SubmissionControl.objects.get(department=department)
        if not control.allow_submission_to_tt:
            messages.warning(request, f"Timetable viewing is not enabled for {department.name}")
            return redirect('cod_panel')
    except SubmissionControl.DoesNotExist:
        control = SubmissionControl.objects.create(department=department)
        if not control.allow_submission_to_tt:
            messages.warning(request, f"Timetable viewing is not enabled for {department.name}")
            return redirect('cod_panel')

    context = {
        'department': department,
        'user': user,
        'timetable_type': 'regular',
        'date': datetime.now().date(),
    }

    return render(request, 'course_management/department_timetable.html', context)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def get_department_timetable_data(request):
    """
    API endpoint to get department timetable data.
    """
    # SECURITY FIX #1 – Rate-limit: 60 requests/minute per user
    if _check_rate_limit(request, "dept_timetable_data"):
        return _rate_limited_json()

    user = request.user

    department_id = request.session.get('selected_department_id')
    department = None

    if department_id:
        try:
            department = Department.objects.get(id=department_id)
            # SECURITY FIX #3 – Confirm user still owns this session department
            if not _user_owns_department(user, department):
                del request.session['selected_department_id']
                department = None
        except Department.DoesNotExist:
            if 'selected_department_id' in request.session:
                del request.session['selected_department_id']

    if not department:
        department = detect_user_department(user)

    if not department:
        user_depts = detect_user_departments(user)
        if len(user_depts) == 0:
            return JsonResponse({
                'error': 'No department found for user',
                'redirect': True,
                'redirect_url': '/cod/'
            }, status=400)
        elif len(user_depts) > 1:
            return JsonResponse({
                'error': 'Multiple departments found. Please select one.',
                'redirect': True,
                'redirect_url': '/select-department/'
            }, status=400)
        else:
            department = user_depts[0]

    # SECURITY FIX #4 – Whitelist allowed timetable_type values
    timetable_type = request.GET.get('type', 'regular')
    if timetable_type not in ('regular', 'exam'):
        timetable_type = 'regular'

    date_str     = request.GET.get('date', None)
    search_query = request.GET.get('search', '')
    program_id   = request.GET.get('program_id')

    # SECURITY FIX #4 – Limit search string length
    if len(search_query) > 100:
        return JsonResponse({'error': 'Search query too long'}, status=400)

    try:
        if timetable_type == 'regular':
            data = get_regular_department_timetable(department, search_query, program_id)
        else:
            data = get_exam_department_timetable(department, date_str, search_query, program_id)

        return JsonResponse(data)
    except Exception as e:
        logger.error("Error in get_department_timetable_data: %s", e, exc_info=True)
        # SECURITY FIX #7 – Never expose raw exception text to non-staff users
        return JsonResponse({
            'error': 'An internal server error occurred',
            'details': str(e) if request.user.is_staff else 'Please contact administrator'
        }, status=500)


def get_regular_department_timetable(department, search_query='', program_id=None):
    """
    Get regular timetable for a department with optional program filter.
    """
    allocation_filter = _department_allocation_filter(department)

    if program_id and str(program_id).strip():
        try:
            allocation_filter &= Q(program_id=int(program_id))
        except (ValueError, TypeError):
            pass

    allocations = CourseAllocation.objects.filter(allocation_filter).select_related(
        'lecturer', 'lecturer__department', 'program', 'department', 'origin_department'
    )

    if search_query:
        allocations = allocations.filter(
            Q(course_code__icontains=search_query) |
            Q(course_name__icontains=search_query) |
            Q(lecturer__name__icontains=search_query) |
            Q(program__name__icontains=search_query)
        )

    timetable_entries = Timetable.objects.filter(
        course_allocation__in=allocations
    ).select_related(
        'course_allocation',
        'course_allocation__lecturer',
        'course_allocation__program',
        'course_allocation__department',
        'course_allocation__origin_department',
        'venue'
    ).order_by('day', 'start_time')

    timetable_entries = list(timetable_entries)
    combined_display_map = _combined_group_display_map(
        {tt.course_allocation_id for tt in timetable_entries}
    )

    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    organized_data = {day: [] for day in days}

    for entry in timetable_entries:
        day = entry.day
        if day in organized_data:
            organized_data[day].append({
                'id': entry.id,
                'course_code': combined_display_map.get(
                    entry.course_allocation_id, entry.course_allocation.course_code
                ),
                'course_name': entry.course_allocation.course_name,
                'lecturer': entry.course_allocation.lecturer.name if entry.course_allocation.lecturer else 'Not assigned',
                'lecturer_id': entry.course_allocation.lecturer.id if entry.course_allocation.lecturer else None,
                'program': entry.course_allocation.program.name if entry.course_allocation.program else 'N/A',
                'program_id': entry.course_allocation.program.id if entry.course_allocation.program else None,
                'venue': entry.venue.code if entry.venue else 'N/A',
                'venue_id': entry.venue.id if entry.venue else None,
                'start_time': entry.start_time.strftime('%H:%M'),
                'end_time': entry.end_time.strftime('%H:%M'),
                'time_slot': f"{entry.start_time.strftime('%I:%M %p')} - {entry.end_time.strftime('%I:%M %p')}",
                'students': entry.course_allocation.number_of_students or 0,
                'allocation_id': entry.course_allocation.id,
            })

    all_slots = set()
    for day_data in organized_data.values():
        for entry in day_data:
            all_slots.add(entry['time_slot'])

    time_slots = sorted(list(all_slots), key=lambda x: datetime.strptime(x.split(' - ')[0], '%I:%M %p'))

    programs = Program.objects.filter(
        department=department
    ).distinct().values('id', 'name')

    return {
        'department': {
            'id': department.id,
            'name': department.name,
        },
        'type': 'regular',
        'days': organized_data,
        'time_slots': time_slots,
        'programs': list(programs),
        'total_courses': len(set(entry['course_code'] for day in organized_data.values() for entry in day)),
        'total_slots': len(timetable_entries),
        'has_data': len(timetable_entries) > 0,
    }


def get_exam_department_timetable(department, date_str=None, search_query='', program_id=None):
    """
    Get exam timetable for a department with optional program filter.
    """
    allocation_filter = _department_allocation_filter(department)

    if program_id and str(program_id).strip():
        try:
            allocation_filter &= Q(program_id=int(program_id))
        except (ValueError, TypeError):
            pass

    allocations = CourseAllocation.objects.filter(allocation_filter).select_related(
        'lecturer', 'lecturer__department', 'program', 'department', 'origin_department'
    )

    if search_query:
        allocations = allocations.filter(
            Q(course_code__icontains=search_query) |
            Q(course_name__icontains=search_query) |
            Q(lecturer__name__icontains=search_query) |
            Q(program__name__icontains=search_query)
        )

    exam_query = ExamTimetable.objects.filter(
        course_allocation__in=allocations
    ).select_related(
        'course_allocation',
        'course_allocation__lecturer',
        'course_allocation__program',
        'course_allocation__department',
        'course_allocation__origin_department',
        'venue'
    ).order_by('date', 'start_time')

    if date_str:
        try:
            filter_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            exam_query = exam_query.filter(date=filter_date)
        except (ValueError, TypeError):
            # SECURITY FIX #4 – Silently ignore invalid dates rather than crashing
            pass

    exam_entries = list(exam_query.all())
    combined_display_map = _combined_group_display_map(
        {e.course_allocation_id for e in exam_entries}
    )

    organized_data = {}
    for entry in exam_entries:
        date_key = entry.date.strftime('%Y-%m-%d')
        day_name = entry.date.strftime('%A')

        if date_key not in organized_data:
            organized_data[date_key] = {
                'date': date_key,
                'day': day_name,
                'display_date': entry.date.strftime('%d %b %Y'),
                'entries': []
            }

        organized_data[date_key]['entries'].append({
            'id': entry.id,
            'course_code': combined_display_map.get(
                entry.course_allocation_id, entry.course_allocation.course_code
            ),
            'course_name': entry.course_allocation.course_name,
            'lecturer': entry.course_allocation.lecturer.name if entry.course_allocation.lecturer else 'Not assigned',
            'lecturer_id': entry.course_allocation.lecturer.id if entry.course_allocation.lecturer else None,
            'program': entry.course_allocation.program.name if entry.course_allocation.program else 'N/A',
            'program_id': entry.course_allocation.program.id if entry.course_allocation.program else None,
            'venue': entry.venue.code if entry.venue else 'N/A',
            'venue_id': entry.venue.id if entry.venue else None,
            'start_time': entry.start_time.strftime('%H:%M'),
            'end_time': entry.end_time.strftime('%H:%M'),
            'time_slot': f"{entry.start_time.strftime('%I:%M %p')} - {entry.end_time.strftime('%I:%M %p')}",
            'students': entry.course_allocation.number_of_students or 0,
            'allocation_id': entry.course_allocation.id,
        })

    sorted_dates = sorted(organized_data.keys())
    organized_data = {date: organized_data[date] for date in sorted_dates}

    all_slots = set()
    for date_data in organized_data.values():
        for entry in date_data['entries']:
            all_slots.add(entry['time_slot'])

    time_slots = sorted(list(all_slots), key=lambda x: datetime.strptime(x.split(' - ')[0], '%I:%M %p'))

    programs = Program.objects.filter(
        department=department
    ).distinct().values('id', 'name')

    return {
        'department': {
            'id': department.id,
            'name': department.name,
        },
        'type': 'exam',
        'dates': organized_data,
        'time_slots': time_slots,
        'programs': list(programs),
        'total_exams': len(exam_entries),
        'date_range': sorted_dates[0] + ' to ' + sorted_dates[-1] if sorted_dates else 'No exams scheduled',
        'has_data': len(exam_entries) > 0,
    }


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def get_department_courses(request):
    """
    API to get all courses for the department (for search dropdown).
    """
    # SECURITY FIX #1 – Rate-limit
    if _check_rate_limit(request, "dept_courses", limit=60, window=60):
        return _rate_limited_json()

    try:
        user = request.user

        department_id = request.session.get('selected_department_id')
        department = None

        if department_id:
            try:
                department = Department.objects.get(id=department_id)
                # SECURITY FIX #3 – Verify ownership
                if not _user_owns_department(user, department):
                    del request.session['selected_department_id']
                    department = None
            except Department.DoesNotExist:
                if 'selected_department_id' in request.session:
                    del request.session['selected_department_id']

        if not department:
            department = detect_user_department(user)

        if not department:
            user_depts = detect_user_departments(user)
            if len(user_depts) > 0:
                courses = CourseAllocation.objects.filter(
                    department__in=user_depts
                ).values('course_code', 'course_name').distinct()
                return JsonResponse({
                    'courses': list(courses),
                    'department_name': 'Multiple Departments',
                    'has_multiple': True
                })

            return JsonResponse({
                'courses': [],
                'redirect': False,
                'message': 'No department found for user'
            })

        courses = CourseAllocation.objects.filter(
            _department_allocation_filter(department)
        ).values('course_code', 'course_name').distinct()

        return JsonResponse({
            'courses': list(courses),
            'department_name': department.name,
            'has_multiple': False
        })

    except Exception as e:
        logger.error("Error in get_department_courses: %s", e, exc_info=True)
        return JsonResponse({
            'courses': [],
            'error': 'Failed to load courses',
            # SECURITY FIX #7 – Only expose details to staff
            'details': str(e) if request.user.is_staff else None
        }, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def get_department_programs(request):
    """
    API to get all programs for the department.
    """
    # SECURITY FIX #1 – Rate-limit
    if _check_rate_limit(request, "dept_programs", limit=60, window=60):
        return _rate_limited_json()

    try:
        user = request.user

        department_id = request.session.get('selected_department_id')
        department = None

        if department_id:
            try:
                department = Department.objects.get(id=department_id)
                # SECURITY FIX #3 – Verify ownership
                if not _user_owns_department(user, department):
                    del request.session['selected_department_id']
                    department = None
            except Department.DoesNotExist:
                if 'selected_department_id' in request.session:
                    del request.session['selected_department_id']

        if not department:
            department = detect_user_department(user)

        if not department:
            user_depts = detect_user_departments(user)
            if len(user_depts) > 0:
                programs = Program.objects.filter(
                    department__in=user_depts
                ).distinct().values('id', 'name')
                return JsonResponse({
                    'programs': list(programs),
                    'department_name': 'Multiple Departments',
                    'has_multiple': True
                })

            return JsonResponse({
                'programs': [],
                'redirect': False,
                'message': 'No department found for user'
            })

        programs = Program.objects.filter(
            department=department
        ).distinct().values('id', 'name')

        program_codes = {}
        for program in programs:
            codes = ProgramCode.objects.filter(program_id=program['id']).values_list('code', flat=True)
            program_codes[program['id']] = list(codes)

        return JsonResponse({
            'programs': list(programs),
            'program_codes': program_codes,
            'department_name': department.name,
            'has_multiple': False
        })

    except Exception as e:
        logger.error("Error in get_department_programs: %s", e, exc_info=True)
        return JsonResponse({
            'programs': [],
            'error': 'Failed to load programs',
            'details': str(e) if request.user.is_staff else None
        }, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def select_department_view(request):
    """
    Allow user to select a department (for staff/admin with multiple departments).
    """
    user = request.user
    user_departments = detect_user_departments(user)

    if len(user_departments) == 0:
        messages.warning(request, "No departments assigned to your account. Please contact administrator.")
        return redirect('cod_panel')

    if request.method == 'POST':
        department_id = request.POST.get('department_id')
        if department_id:
            try:
                dept_id_int = int(department_id)
                # SECURITY FIX #3 – Re-validate ownership server-side
                if dept_id_int in [dept.id for dept in user_departments]:
                    # SECURITY FIX #5 – Store integer, not the raw string, in session
                    request.session['selected_department_id'] = dept_id_int
                    messages.success(request, "Department selected successfully")
                    return redirect('department_timetable')
                else:
                    messages.error(request, "You don't have access to this department")
            except (ValueError, TypeError):
                messages.error(request, "Invalid department ID")

    return render(request, 'timetable/select_department.html', {
        'departments': user_departments,
        'has_multiple': len(user_departments) > 1
    })


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def clear_selected_department(request):
    """
    Clear the selected department from session.
    """
    if 'selected_department_id' in request.session:
        del request.session['selected_department_id']
        messages.success(request, "Department selection cleared")

    return redirect('select_department')


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def get_selected_department_data(request):
    """
    API to get data for selected department (from session).
    """
    # SECURITY FIX #1 – Rate-limit
    if _check_rate_limit(request, "selected_dept_data", limit=60, window=60):
        return _rate_limited_json()

    try:
        department_id = request.session.get('selected_department_id')

        if not department_id:
            department = detect_user_department(request.user)
            if department:
                department_id = department.id
            else:
                return JsonResponse({
                    'error': 'No department selected or detected',
                    'redirect': True,
                    'redirect_url': '/select-department/'
                })

        try:
            department = Department.objects.get(id=department_id)
        except Department.DoesNotExist:
            if 'selected_department_id' in request.session:
                del request.session['selected_department_id']

            return JsonResponse({
                'error': 'Selected department not found',
                'redirect': True,
                'redirect_url': '/select-department/'
            })

        # SECURITY FIX #3 – Always re-verify ownership on every request
        if not _user_owns_department(request.user, department):
            if 'selected_department_id' in request.session:
                del request.session['selected_department_id']

            return JsonResponse({
                'error': 'Access denied to selected department',
                'redirect': True,
                'redirect_url': '/select-department/'
            })

        # SECURITY FIX #4 – Whitelist timetable type
        timetable_type = request.GET.get('type', 'regular')
        if timetable_type not in ('regular', 'exam'):
            timetable_type = 'regular'

        search_query = request.GET.get('search', '')
        if len(search_query) > 100:
            return JsonResponse({'error': 'Search query too long'}, status=400)

        if timetable_type == 'regular':
            data = get_regular_department_timetable(department, search_query)
        else:
            date_str = request.GET.get('date', None)
            data = get_exam_department_timetable(department, date_str, search_query)

        return JsonResponse(data)

    except Exception as e:
        logger.error("Error in get_selected_department_data: %s", e, exc_info=True)
        return JsonResponse({
            'error': 'An internal server error occurred',
            'details': str(e) if request.user.is_staff else None
        }, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def user_department_info(request):
    """
    API to get current user's department information.
    """
    # SECURITY FIX #1 – Rate-limit
    if _check_rate_limit(request, "user_dept_info", limit=60, window=60):
        return _rate_limited_json()

    try:
        user = request.user

        department_id = request.session.get('selected_department_id')
        department = None
        is_selected = False

        if department_id:
            try:
                department = Department.objects.get(id=department_id)
                # SECURITY FIX #3 – Re-verify ownership
                if not _user_owns_department(user, department):
                    del request.session['selected_department_id']
                    department = None
                else:
                    is_selected = True
            except Department.DoesNotExist:
                if 'selected_department_id' in request.session:
                    del request.session['selected_department_id']

        if not department:
            department = detect_user_department(user)

        user_departments = detect_user_departments(user)

        if department:
            return JsonResponse({
                'current_department': {
                    'id': department.id,
                    'name': department.name,
                    'is_selected': is_selected
                },
                'available_departments': [
                    {
                        'id': dept.id,
                        'name': dept.name,
                        'is_current': dept.id == department.id
                    }
                    for dept in user_departments
                ],
                'has_multiple': len(user_departments) > 1,
                'can_select': len(user_departments) > 1,
                'status': 'success'
            })
        else:
            return JsonResponse({
                'current_department': None,
                'available_departments': [
                    {
                        'id': dept.id,
                        'name': dept.name,
                        'is_current': False
                    }
                    for dept in user_departments
                ],
                'has_multiple': len(user_departments) > 1,
                'can_select': len(user_departments) > 1,
                'redirect': len(user_departments) == 0,
                'redirect_url': '/cod/' if len(user_departments) == 0 else None,
                'status': 'success'
            })

    except Exception as e:
        logger.error("Error in user_department_info: %s", e, exc_info=True)
        return JsonResponse({
            'error': 'Failed to load department information',
            'details': str(e) if request.user.is_staff else None,
            'status': 'error'
        }, status=500)


# ═══════════════════════════════════════════════════════════════════════════
# PDF export — replaces the old window.print() "Print" button with a
# properly formatted, letterhead-branded PDF built with reportlab. Reuses
# the same letterhead/table/footer helpers as the main analysis reports
# (timetable.analysis_reports) so this document matches the rest of the
# system's official PDFs instead of a raw browser print-out.
# ═══════════════════════════════════════════════════════════════════════════

def _resolve_department_for_pdf(request):
    """Same department-resolution order used by get_department_timetable_data:
    session-selected department (re-verified) → auto-detected → the user's
    single department if they only have one.

    Admin-level roles (SUDO/DIRECTOR/TIMETABLE_ADMIN) may additionally target
    an arbitrary department via ?department_id=, which lets the Timetable
    Analysis & Reports dashboard export/attach the departmental timetable for
    whichever department is selected in its scope dropdown, rather than only
    the logged-in user's own department. COD/COD Admin are deliberately
    excluded from this override — they must only ever see their own
    department, regardless of what a query string says.
    """
    user = request.user

    admin_department_id = request.GET.get('department_id')
    if admin_department_id and user_has_role(user, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN):
        try:
            return Department.objects.get(id=admin_department_id)
        except (Department.DoesNotExist, ValueError, TypeError):
            pass

    department_id = request.session.get('selected_department_id')
    department = None

    if department_id:
        try:
            department = Department.objects.get(id=department_id)
            if not _user_owns_department(user, department):
                department = None
        except Department.DoesNotExist:
            department = None

    if not department:
        department = detect_user_department(user)

    if not department:
        user_depts = detect_user_departments(user)
        if len(user_depts) == 1:
            department = user_depts[0]

    return department


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def export_department_timetable_pdf(request):
    """
    Generate a letterhead-formatted PDF of the logged-in user's department
    timetable (regular or exam), honouring the same type/program_id/search/
    date filters as the on-screen view.
    """
    if _check_rate_limit(request, "dept_timetable_pdf", limit=20, window=60):
        return _rate_limited_json()

    department = _resolve_department_for_pdf(request)
    if not department:
        messages.warning(request, "No department assigned to your account. Please contact administrator.")
        return redirect('cod_panel')

    timetable_type = request.GET.get('type', 'regular')
    if timetable_type not in ('regular', 'exam'):
        timetable_type = 'regular'

    search_query = (request.GET.get('search', '') or '')[:100]
    program_id = request.GET.get('program_id')
    date_str = request.GET.get('date')

    template_config = _get_template_config()
    styles = _styles()
    today_str = datetime.now().strftime("%d-%b-%Y").upper()
    ref = template_config.get_reference_number(today_str)

    program_name = None
    if program_id and str(program_id).strip():
        try:
            program_name = Program.objects.filter(
                id=int(program_id), department=department
            ).values_list('name', flat=True).first()
        except (ValueError, TypeError):
            program_name = None

    subtitle = department.name if not program_name else f"{department.name} — {program_name}"
    safe_dept_name = department.name.replace(' ', '_').replace('/', '-')

    allocation_filter = _department_allocation_filter(department)
    if program_id and str(program_id).strip():
        try:
            allocation_filter &= Q(program_id=int(program_id))
        except (ValueError, TypeError):
            pass

    allocations = CourseAllocation.objects.filter(allocation_filter)
    if search_query:
        allocations = allocations.filter(
            Q(course_code__icontains=search_query) |
            Q(course_name__icontains=search_query) |
            Q(lecturer__name__icontains=search_query) |
            Q(program__name__icontains=search_query)
        )

    elements = []

    if timetable_type == 'regular':
        qs = Timetable.objects.filter(
            course_allocation__in=allocations,
            venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
        ).select_related(
            'course_allocation', 'course_allocation__lecturer',
            'course_allocation__program', 'venue',
        )

        _letterhead(
            elements, styles, template_config,
            "DEPARTMENTAL TIMETABLE",
            subtitle,
            ref=ref,
            date_str=today_str,
        )

        # rich=True + show_program=True: each cell shows course code,
        # lecturer, and program name — a department spans multiple
        # programs, so the program line disambiguates cells at a glance
        # instead of only showing the (comma-separated) course code(s).
        day_tables = _day_grid_tables(qs, styles, table_style_fn=_standard_table_style, rich=True, show_program=True)
        total_rows = qs.count()

        if not day_tables:
            elements.append(Paragraph("No scheduled entries found for this department.", styles['RCell']))
        else:
            for day_label, day_table in day_tables:
                elements.append(Paragraph(day_label.upper(), styles['RSection']))
                elements.append(day_table)
                elements.append(Spacer(1, 10))

        elements.append(Spacer(1, 6))
        elements.append(Paragraph(f"Total scheduled entries: {total_rows}", styles['RCellBold']))

        # Unscheduled courses — allocations belonging to this department
        # (and matching the program/search filters) that have no Timetable
        # entry yet (or an incomplete one missing venue/start/end time).
        scheduled_alloc_ids = set(qs.values_list('course_allocation_id', flat=True))
        unscheduled_allocations = allocations.exclude(
            id__in=scheduled_alloc_ids
        ).select_related('lecturer', 'program').order_by('course_code')

        if unscheduled_allocations.exists():
            elements.append(Spacer(1, 14))
            elements.append(Paragraph("UNSCHEDULED COURSES", styles['RSection']))

            unsched_rows = [[
                Paragraph('Course Code', styles['RHead']),
                Paragraph('Course Name', styles['RHead']),
                Paragraph('Program', styles['RHead']),
                Paragraph('Lecturer', styles['RHead']),
                Paragraph('Students', styles['RHead']),
            ]]
            for alloc in unscheduled_allocations:
                unsched_rows.append([
                    Paragraph(alloc.course_code or '-', styles['RCellBold']),
                    Paragraph(alloc.course_name or '-', styles['RCell']),
                    Paragraph(getattr(alloc.program, 'name', 'N/A'), styles['RCell']),
                    Paragraph(getattr(alloc.lecturer, 'name', 'Unassigned'), styles['RCell']),
                    Paragraph(str(alloc.number_of_students or 0), styles['RCell']),
                ])

            unsched_col_widths = [1.1 * inch, 2.8 * inch, 2.0 * inch, 2.0 * inch, 0.9 * inch]
            unsched_table = Table(unsched_rows, colWidths=unsched_col_widths, repeatRows=1)
            unsched_table.setStyle(_standard_table_style())
            elements.append(unsched_table)
            elements.append(Spacer(1, 6))
            elements.append(Paragraph(
                f"Total unscheduled courses: {unscheduled_allocations.count()}",
                styles['RCellBold'],
            ))

        filename = f"{safe_dept_name}_timetable.pdf"

    else:
        exam_qs = ExamTimetable.objects.filter(
            course_allocation__in=allocations,
        ).select_related(
            'course_allocation', 'course_allocation__lecturer',
            'course_allocation__program', 'venue',
        ).order_by('date', 'start_time')

        if date_str:
            try:
                filter_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                exam_qs = exam_qs.filter(date=filter_date)
            except (ValueError, TypeError):
                pass

        exam_qs = list(exam_qs)
        combined_display_map = _combined_group_display_map(
            {e.course_allocation_id for e in exam_qs}
        )

        entries_by_date = defaultdict(list)
        for entry in exam_qs:
            entries_by_date[entry.date].append(entry)
        sorted_dates = sorted(entries_by_date.keys())

        _letterhead(
            elements, styles, template_config,
            "DEPARTMENTAL EXAMINATION TIMETABLE",
            subtitle,
            ref=ref,
            date_str=today_str,
        )

        if not sorted_dates:
            elements.append(Paragraph("No exam entries found for this department.", styles['RCell']))
        else:
            for exam_date in sorted_dates:
                elements.append(Paragraph(exam_date.strftime("%A, %d %B %Y").upper(), styles['RSection']))

                rows = [[
                    Paragraph('Time', styles['RHead']),
                    Paragraph('Course Code', styles['RHead']),
                    Paragraph('Course Name', styles['RHead']),
                    Paragraph('Program', styles['RHead']),
                    Paragraph('Lecturer', styles['RHead']),
                    Paragraph('Venue', styles['RHead']),
                    Paragraph('Students', styles['RHead']),
                ]]

                for entry in sorted(entries_by_date[exam_date], key=lambda e: e.start_time):
                    alloc = entry.course_allocation
                    display_code = (
                        combined_display_map.get(entry.course_allocation_id, alloc.course_code)
                        if alloc else '-'
                    )
                    rows.append([
                        Paragraph(f"{entry.start_time.strftime('%H:%M')}-{entry.end_time.strftime('%H:%M')}", styles['RCell']),
                        Paragraph(display_code, styles['RCellBold']),
                        Paragraph(alloc.course_name if alloc else '-', styles['RCell']),
                        Paragraph(getattr(alloc.program, 'name', 'N/A') if alloc else 'N/A', styles['RCell']),
                        Paragraph(getattr(alloc.lecturer, 'name', 'Unassigned') if alloc else 'Unassigned', styles['RCell']),
                        Paragraph(entry.venue.code if entry.venue else 'N/A', styles['RCell']),
                        Paragraph(str(alloc.number_of_students or 0) if alloc else '0', styles['RCell']),
                    ])

                col_widths = [0.9 * inch, 0.9 * inch, 2.3 * inch, 1.6 * inch, 1.6 * inch, 0.8 * inch, 0.7 * inch]
                table = Table(rows, colWidths=col_widths, repeatRows=1)
                table.setStyle(_standard_table_style())
                elements.append(table)
                elements.append(Spacer(1, 12))

        elements.append(Spacer(1, 6))
        elements.append(Paragraph(f"Total exam entries: {len(exam_qs)}", styles['RCellBold']))

        filename = f"{safe_dept_name}_exam_timetable.pdf"

    return _pdf_response(
        elements, filename, template_config, ref,
        landscape_mode=True, compiled_by=_full_name(request),
    )