"""
resits_timetabling/cod_panel.py
COD (Chair of Department) panel for resit timetabling.

Allows CODs to:
  - View/manage their department's resit course allocations
  - Submit resit courses for timetabling
  - See published resit timetable for their department
  - Manage student registrations per course (add/remove/view)
"""
import json
import logging
import re
from collections import defaultdict
from typing import Optional

from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role, user_has_role
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q, Case, When, Value, IntegerField
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from core.group_required import group_required
from department_management.models import Department
from faculty_management.models import Faculty
from lecturer_portal.models import Lecturer
from program_management.models import Program, ProgramCourse
from program_management.code_utils import canonical_course_key

from .models import (
    ResitSchedulerConfig,
    ResitCourseAllocation,
    ResitTimetable,
    ResitTempTimetable,
    ResitSubmissionControl,
    StudentResitRegistration,
)

from special_requests.models import SpecialRequest
from special_requests.panel_actions import SR_ACTIONS, handle_sr_action

logger = logging.getLogger(__name__)



def get_user_department(user) -> Optional[Department]:
    """
    Return the department linked to a COD user account.
    Uses multiple methods to find the department.

    Priority: OrgRole.department (authoritative — this is what's set for
    COD Admin helper accounts, which are not the literal Department.leader
    and often have no Lecturer profile) -> Department leader -> Lecturer
    profile dept -> Lecturer email match.
    """
    if not user or not user.is_authenticated:
        return None

    # Method 0: OrgRole.department — authoritative, and the ONLY method
    # that works for "*_admin" (COD Admin) accounts, since those accounts
    # are deliberately not the Department.leader and usually have no
    # Lecturer row of their own.
    try:
        org = getattr(user, "org_role", None)
        if org and org.department_id:
            logger.info(f"Found department via org_role: {org.department.name}")
            return org.department
    except Exception as e:
        logger.warning(f"Error checking org_role department: {e}")

    # Method 1: Check if user is Department leader
    try:
        dept = Department.objects.filter(leader=user).first()
        if dept:
            logger.info(f"Found department via leader: {dept.name}")
            return dept
    except Exception as e:
        logger.warning(f"Error checking department leader: {e}")
    
    # Method 2: Check Lecturer profile
    try:
        lect = Lecturer.objects.filter(user=user).first()
        if lect and lect.department:
            logger.info(f"Found department via lecturer profile: {lect.department.name}")
            return lect.department
    except Exception as e:
        logger.warning(f"Error checking lecturer profile: {e}")
    
    # Method 3: Check by email match
    try:
        if user.email:
            lect = Lecturer.objects.filter(email__iexact=user.email).first()
            if lect and lect.department:
                logger.info(f"Found department via email match: {lect.department.name}")
                return lect.department
    except Exception as e:
        logger.warning(f"Error checking email match: {e}")
    
    logger.warning(f"No department found for user: {user.username}")
    return None


def _assert_owns_department(request, dept: Optional[Department]) -> Optional[JsonResponse]:
    """Returns an error JsonResponse if the user does not own dept."""
    if dept is None:
        return JsonResponse(
            {"status": "error", "message": "No department associated with your account. Please contact administrator."},
            status=403,
        )
    return None


def get_resit_control(department=None):
    """Return the ResitSubmissionControl instance for the given department."""
    if not department:
        return None
    
    try:
        control, created = ResitSubmissionControl.objects.get_or_create(department=department)
        return control
    except Exception as e:
        logger.warning(f"Error getting ResitSubmissionControl: {e}")
        return None


def normalize_reg_number(reg_no: str) -> str:
    """
    Extract numeric portion from registration number.
    e.g., "EB1/66791/23" -> "66791"
          "66791" -> "66791"
    """
    if not reg_no:
        return ""
    match = re.search(r'(\d{4,})', str(reg_no))
    if match:
        return match.group(1)
    return re.sub(r'\D', '', reg_no)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def cod_panel(request):
    """
    Main COD panel for resit timetabling.
    Handles both GET (view) and POST (AJAX actions).
    """
    department = get_user_department(request.user)
    
    # Log department detection result for debugging
    if department:
        logger.info(f"COD panel accessed by {request.user.username} for department: {department.name}")
    else:
        logger.warning(f"COD panel accessed by {request.user.username} but no department found")
    
    # Handle AJAX POST requests
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        action = request.POST.get("action")
        
        ALLOWED_ACTIONS = {
            "list_allocations", "create_allocation", "update_allocation", 
            "delete_allocation", "allocation_detail", "toggle_submit_to_timetabling",
            "get_programs_for_department", "get_program_courses", "get_course_details",
            "search_lecturers", "get_allocation_summary",
            "list_allocations_for_enrollment", "update_student_count",
            # New student management actions
            "add_student_to_allocation", "remove_student_from_allocation", 
            "get_allocation_students", "search_students",
            # SR (Special Request) actions
            "create_special_request", "get_special_request_for_allocation",
            "get_courses_for_sr_scope", "update_special_request",
            "cancel_special_request",
        }
        
        if action not in ALLOWED_ACTIONS:
            return JsonResponse({"status": "error", "message": "Unknown action"}, status=400)
        
        # Check department ownership for all actions except get_course_details and get_program_courses
        if action not in ["get_course_details", "get_program_courses", "get_programs_for_department", 
                          "search_students"]:
            err = _assert_owns_department(request, department)
            if err:
                return err

        if action in SR_ACTIONS:
            from django.db.models import Q as _Q
            return handle_sr_action(
                request, action,
                dept=department,
                allocation_model=ResitCourseAllocation,
                dept_scoped_qs=lambda d: ResitCourseAllocation.objects.filter(
                    _Q(department=d) | _Q(origin_department=d)
                ),
                panel=SpecialRequest.PANEL_RESIT,
            )

        if action == "list_allocations":
            return list_allocations(request, department)
        elif action == "create_allocation":
            return create_allocation(request, department)
        elif action == "update_allocation":
            return update_allocation(request, department)
        elif action == "delete_allocation":
            return delete_allocation(request, department)
        elif action == "allocation_detail":
            return allocation_detail(request, department)
        elif action == "toggle_submit_to_timetabling":
            return toggle_submit_to_timetabling(request, department)
        elif action == "get_programs_for_department":
            return get_programs_for_department(request)
        elif action == "get_program_courses":
            return get_program_courses(request)
        elif action == "get_course_details":
            return get_course_details(request)
        elif action == "search_lecturers":
            return search_lecturers(request, department)
        elif action == "get_allocation_summary":
            return get_allocation_summary(request, department)
        elif action == "list_allocations_for_enrollment":
            return list_allocations_for_enrollment(request, department)
        elif action == "update_student_count":
            return update_student_count(request, department)
        # New student management actions
        elif action == "add_student_to_allocation":
            return add_student_to_allocation(request, department)
        elif action == "remove_student_from_allocation":
            return remove_student_from_allocation(request, department)
        elif action == "get_allocation_students":
            return get_allocation_students(request, department)
        elif action == "search_students":
            return search_students(request)
    
    # GET request - render the panel
    return render_cod_panel(request, department)


def render_cod_panel(request, department):
    """Render the main COD panel template."""
    if not department:
        return render(request, "resits_timetabling/cod_panel.html", {
            "department": None,
            "allocations": [],
            "own_dept_allocations": [],
            "cross_dept_allocations": [],
            "published_entries": [],
            "draft_entries": [],
            "config": None,
            "control": None,
            "departments": Department.objects.all(),
            "lecturers": Lecturer.objects.all(),
            "programs": Program.objects.all(),
            "academic_years": ["2024/2025", "2025/2026", "2026/2027"],
            "semester_choices": [("S1", "Semester 1"), ("S2", "Semester 2")],
            "error": "No department associated with your account. Please contact administrator."
        })
    
    # Get resit allocations for this department. Include allocations this
    # department only *originated* (origin_department) even though the record
    # is owned/hosted by another department — e.g. a Business Administration-
    # origin course taught to Computer Science students and owned by Computer
    # Science. Without this, the record silently disappears from the
    # originating COD's panel the moment they set themselves as the Origin
    # Department, and they can never open it to assign a lecturer.
    allocations = ResitCourseAllocation.objects.filter(
        Q(department=department) | Q(origin_department=department)
    ).select_related(
        "program", "lecturer", "origin_department", "program_course"
    ).prefetch_related(
        "student_registrations"
    ).order_by(
        "program__name", "program_course__year", "program_course__semester", "course_code"
    )
    
    # Split into own-dept and cross-dept
    own_dept_allocations = allocations.filter(
        Q(program__department=department) | Q(program__isnull=True)
    )
    cross_dept_allocations = allocations.exclude(
        Q(program__department=department) | Q(program__isnull=True)
    )
    
    # Published resit timetable entries for this department
    published_entries = ResitTimetable.objects.filter(
        resit_course_allocation__department=department
    ).select_related("resit_course_allocation", "venue").order_by("date", "start_time")
    
    # Draft entries (visible to COD for awareness)
    draft_entries = ResitTempTimetable.objects.filter(
        resit_course_allocation__department=department
    ).select_related("resit_course_allocation", "venue").order_by("date", "start_time")
    
    # Get scheduler config
    config = ResitSchedulerConfig.objects.order_by("-id").first()
    
    # Get submission control
    control = get_resit_control(department)
    
    # Get all departments for dropdowns
    departments = Department.objects.select_related("faculty").all()
    
    # Get lecturers ordered by department match (prioritize user's department)
    lecturers = Lecturer.objects.all().annotate(
        is_dept=Case(
            When(department=department, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    ).order_by("-is_dept", "name")
    
    # Get programs for this department
    programs = Program.objects.filter(department=department).order_by("name")
    
    # Get all program courses for course selection
    program_courses = ProgramCourse.objects.select_related("program").order_by("program__name", "year", "semester", "course_code")
    
    context = {
        "department": department,
        "department_name": department.name,
        "allocations": allocations,
        "own_dept_allocations": own_dept_allocations,
        "cross_dept_allocations": cross_dept_allocations,
        "published_entries": published_entries,
        "draft_entries": draft_entries,
        "config": config,
        "control": control,
        "departments": departments,
        "lecturers": lecturers,
        "programs": programs,
        "program_courses": program_courses,
        "academic_years": ["2024/2025", "2025/2026", "2026/2027"],
        "semester_choices": [("S1", "Semester 1"), ("S2", "Semester 2")],
        "is_resit_admin": request.user.is_superuser or user_has_role(request.user, Role.SUDO),
    }
    
    return render(request, "resits_timetabling/cod_panel.html", context)


# ---------------------------------------------------------------
# AJAX Handlers
# ---------------------------------------------------------------

def list_allocations(request, department):
    """Return JSON list of resit allocations for the department."""
    allocations = ResitCourseAllocation.objects.filter(
        Q(department=department) | Q(origin_department=department)
    ).select_related("program", "lecturer", "origin_department", "program_course")
    
    data = [{
        "id": a.id,
        "course_code": a.course_code,
        "course_name": a.course_name,
        "program_id": a.program.id if a.program else None,
        "program_name": a.program.name if a.program else "",
        "lecturer_id": a.lecturer.id if a.lecturer else None,
        "lecturer_name": a.lecturer.display_name if a.lecturer else "",
        "number_of_students": a.number_of_students,
        "origin_department_id": a.origin_department.id if a.origin_department else None,
        "origin_department_name": a.origin_department.name if a.origin_department else "",
        "submitted_to_timetabling": a.submitted_to_timetabling,
        "scheduled": a.scheduled,
        "program_course_id": a.program_course_id,
        "program_course_year": a.program_course.year if a.program_course else None,
        "program_course_semester": a.program_course.semester if a.program_course else None,
        "academic_year": a.academic_year,
        "semester": a.semester,
    } for a in allocations]
    
    return JsonResponse({"status": "success", "allocations": data})


def create_allocation(request, department):
    """Create a new resit course allocation."""
    course_code = request.POST.get("course_code", "").strip()
    course_name = request.POST.get("course_name", "").strip()
    program_id = request.POST.get("program_id") or None
    lecturer_id = request.POST.get("lecturer_id") or None
    number_of_students = request.POST.get("number_of_students", "0")
    origin_department_id = request.POST.get("origin_department_id") or None
    program_course_id = request.POST.get("program_course_id") or None
    academic_year = request.POST.get("academic_year", "")
    semester = request.POST.get("semester", "")
    
    # Input validation
    if not course_code or not course_name:
        return JsonResponse({"status": "error", "message": "Course code and name are required."}, status=400)
    
    if len(course_code) > 100:
        return JsonResponse({"status": "error", "message": "Course code too long."}, status=400)
    if len(course_name) > 200:
        return JsonResponse({"status": "error", "message": "Course name too long."}, status=400)
    
    try:
        number_of_students = int(number_of_students)
        if number_of_students < 0:
            number_of_students = 0
        if number_of_students > 10000:
            return JsonResponse({"status": "error", "message": "Student count out of range."}, status=400)
    except (ValueError, TypeError):
        number_of_students = 0
    
    # Get related objects
    program = get_object_or_404(Program, pk=program_id) if program_id and program_id != "" else None
    lecturer = get_object_or_404(Lecturer, pk=lecturer_id) if lecturer_id and lecturer_id != "" and lecturer_id != "__new__" else None
    origin_department = get_object_or_404(Department, pk=origin_department_id) if origin_department_id and origin_department_id != "" else None
    program_course = get_object_or_404(ProgramCourse, pk=program_course_id) if program_course_id and program_course_id != "" else None
    
    # If program_course is selected, auto-fill academic_year and semester from it
    if program_course:
        if not academic_year:
            academic_year = f"{program_course.year}/{(program_course.year + 1) % 100}" if program_course.year else ""
        if not semester:
            semester = f"S{program_course.semester}" if program_course.semester else ""
    
    # Check for duplicate
    duplicate_check = ResitCourseAllocation.objects.filter(
        department=department,
        course_code__iexact=course_code
    )
    if program:
        duplicate_check = duplicate_check.filter(program=program)
    if duplicate_check.exists():
        return JsonResponse({
            "status": "error", 
            "message": f"A resit allocation for {course_code} already exists."
        }, status=400)
    
    with transaction.atomic():
        allocation = ResitCourseAllocation.objects.create(
            course_code=course_code,
            course_name=course_name,
            department=department,
            origin_department=origin_department,
            program=program,
            lecturer=lecturer,
            number_of_students=number_of_students,
            program_course=program_course,
            academic_year=academic_year,
            semester=semester,
            created_by=request.user,
        )
    
    return JsonResponse({
        "status": "success",
        "allocation": {
            "id": allocation.id,
            "course_code": allocation.course_code,
            "course_name": allocation.course_name,
            "program_id": allocation.program_id,
            "program_name": allocation.program.name if allocation.program else "",
            "lecturer_id": allocation.lecturer_id,
            "lecturer_name": allocation.lecturer.display_name if allocation.lecturer else "",
            "number_of_students": allocation.number_of_students,
            "origin_department_name": allocation.origin_department.name if allocation.origin_department else "",
            "submitted_to_timetabling": allocation.submitted_to_timetabling,
            "scheduled": allocation.scheduled,
            "academic_year": allocation.academic_year,
            "semester": allocation.semester,
        }
    })


def update_allocation(request, department):
    """Update an existing resit course allocation."""
    allocation_id = request.POST.get("id")
    if not allocation_id:
        return JsonResponse({"status": "error", "message": "Allocation ID required"}, status=400)
    
    allocation = get_object_or_404(
        ResitCourseAllocation.objects.filter(
            Q(department=department) | Q(origin_department=department)
        ),
        pk=allocation_id,
    )

    # An "origin department" collaborator (e.g. Business Admin on a course that
    # Computer Science owns and originated from them) is allowed in here purely
    # to assign/change the lecturer or student count -- they do NOT own this
    # allocation. Only the true owning department may change authoring fields
    # (course name, program, program_course, origin_department, academic
    # year/semester); a pure origin-department collaborator's submission of
    # those fields is ignored so they can't silently reassign ownership or
    # authoring details away from the true owner.
    is_true_owner = (allocation.department_id == department.id)

    course_name = request.POST.get("course_name", "").strip()
    lecturer_id = request.POST.get("lecturer_id") or None
    number_of_students = request.POST.get("number_of_students", "0")
    origin_department_id = request.POST.get("origin_department_id") or None
    program_id = request.POST.get("program_id") or None
    program_course_id = request.POST.get("program_course_id") or None
    academic_year = request.POST.get("academic_year", "")
    semester = request.POST.get("semester", "")
    
    if lecturer_id and lecturer_id != "" and lecturer_id != "__new__":
        allocation.lecturer = get_object_or_404(Lecturer, pk=lecturer_id)
    
    try:
        number_of_students = int(number_of_students)
        if 0 <= number_of_students <= 10000:
            allocation.number_of_students = number_of_students
    except (ValueError, TypeError):
        pass

    if is_true_owner:
        if course_name:
            allocation.course_name = course_name

        if program_id:
            allocation.program = get_object_or_404(Program, pk=program_id) if program_id != "" else None

        if program_course_id:
            allocation.program_course = get_object_or_404(ProgramCourse, pk=program_course_id) if program_course_id != "" else None
            if allocation.program_course:
                if not academic_year:
                    academic_year = f"{allocation.program_course.year}/{(allocation.program_course.year + 1) % 100}" if allocation.program_course.year else ""
                if not semester:
                    semester = f"S{allocation.program_course.semester}" if allocation.program_course.semester else ""

        if origin_department_id:
            allocation.origin_department = get_object_or_404(Department, pk=origin_department_id) if origin_department_id != "" else None

        if academic_year:
            allocation.academic_year = academic_year

        if semester:
            allocation.semester = semester
    
    allocation.save()
    
    return JsonResponse({
        "status": "success",
        "allocation": {
            "id": allocation.id,
            "course_code": allocation.course_code,
            "course_name": allocation.course_name,
            "lecturer_name": allocation.lecturer.display_name if allocation.lecturer else "",
            "number_of_students": allocation.number_of_students,
            "origin_department_name": allocation.origin_department.name if allocation.origin_department else "",
            "academic_year": allocation.academic_year,
            "semester": allocation.semester,
        }
    })


def delete_allocation(request, department):
    """Delete a resit course allocation — allow the true owning department,
    or the origin-department collaborator who originated this cross-dept
    course."""
    allocation_id = request.POST.get("id")
    if not allocation_id:
        return JsonResponse({"status": "error", "message": "Allocation ID required"}, status=400)
    
    allocation = get_object_or_404(
        ResitCourseAllocation.objects.filter(
            Q(department=department) | Q(origin_department=department)
        ),
        pk=allocation_id,
    )
    allocation.delete()
    return JsonResponse({"status": "success", "id": allocation_id})


def allocation_detail(request, department):
    """Get details of a specific allocation for editing.

    View access: the true owning department, OR an origin-department
    collaborator opening it purely to assign a lecturer. Save-time
    restrictions (see update_allocation) still apply regardless.
    """
    allocation_id = request.POST.get("id")
    if not allocation_id:
        return JsonResponse({"status": "error", "message": "Allocation ID required"}, status=400)
    
    allocation = get_object_or_404(
        ResitCourseAllocation.objects.select_related(
            "department", "origin_department", "program", "lecturer", "program_course"
        ).prefetch_related("student_registrations").filter(
            Q(department=department) | Q(origin_department=department)
        ),
        pk=allocation_id,
    )
    
    is_cross_dept = bool(
        allocation.program and 
        allocation.program.department_id and 
        allocation.program.department_id != department.id
    )
    is_true_owner = (allocation.department_id == department.id)
    
    # Get student registrations
    students = list(allocation.student_registrations.values(
        'id', 'student_reg_no', 'student_reg_no_normalized', 'student_name'
    ))
    
    return JsonResponse({
        "status": "success",
        "allocation": {
            "id": allocation.id,
            "course_code": allocation.course_code,
            "course_name": allocation.course_name,
            "department_id": allocation.department.id,
            "department_name": allocation.department.name,
            "origin_department_id": allocation.origin_department.id if allocation.origin_department else None,
            "origin_department_name": allocation.origin_department.name if allocation.origin_department else "",
            "program_id": allocation.program.id if allocation.program else None,
            "program_name": allocation.program.name if allocation.program else "",
            "program_department_id": allocation.program.department_id if allocation.program else None,
            "is_cross_dept": is_cross_dept,
            "is_true_owner": is_true_owner,
            "lecturer_id": allocation.lecturer.id if allocation.lecturer else None,
            "lecturer_name": allocation.lecturer.display_name if allocation.lecturer else "",
            "number_of_students": allocation.number_of_students,
            "submitted_to_timetabling": allocation.submitted_to_timetabling,
            "scheduled": allocation.scheduled,
            "program_course_id": allocation.program_course_id,
            "academic_year": allocation.academic_year,
            "semester": allocation.semester,
            "students": students,
        }
    })


def toggle_submit_to_timetabling(request, department):
    """Toggle the submitted_to_timetabling flag for an allocation."""
    allocation_id = request.POST.get("id")
    if not allocation_id:
        return JsonResponse({"status": "error", "message": "Allocation ID required"}, status=400)
    
    allocation = get_object_or_404(ResitCourseAllocation, pk=allocation_id, department=department)
    allocation.submitted_to_timetabling = not allocation.submitted_to_timetabling
    allocation.save()
    
    return JsonResponse({
        "status": "success", 
        "id": allocation_id,
        "submitted_to_timetabling": allocation.submitted_to_timetabling
    })


def get_programs_for_department(request):
    """Get programs for a given department (for cross-dept allocation editing)."""
    dept_id = request.POST.get("department_id")
    if not dept_id:
        return JsonResponse({"status": "error", "message": "department_id required"}, status=400)
    
    try:
        dept_id = int(dept_id)
    except (ValueError, TypeError):
        return JsonResponse({"status": "error", "message": "Invalid department_id"}, status=400)
    
    dept_obj = get_object_or_404(Department, pk=dept_id)
    user_dept = get_user_department(request.user)
    
    programs = Program.objects.filter(department=dept_obj).order_by("name")
    is_own = user_dept and dept_obj.pk == user_dept.pk
    
    data = [{"id": p.id, "name": p.name, "is_own_dept": is_own} for p in programs]
    
    return JsonResponse({
        "status": "success",
        "programs": data,
        "department_id": dept_obj.id,
        "department_name": dept_obj.name,
        "is_own_dept": is_own,
    })


def get_program_courses(request):
    """Return program courses for a given program."""
    program_id = request.POST.get("program_id")
    if not program_id:
        return JsonResponse({"status": "error", "message": "program_id required"}, status=400)
    
    try:
        program_id = int(program_id)
    except (ValueError, TypeError):
        return JsonResponse({"status": "error", "message": "Invalid program_id"}, status=400)
    
    courses = ProgramCourse.objects.filter(program_id=program_id).select_related("program")
    
    data = [{
        "id": c.id,
        "course_code": c.course_code,
        "course_name": c.course_name,
        "year": c.year,
        "semester": c.semester,
        "academic_year": f"{c.year}/{(c.year + 1) % 100}" if c.year else "",
        "semester_display": f"S{c.semester}" if c.semester else "",
    } for c in courses.order_by("year", "semester", "course_code")]
    
    return JsonResponse({"status": "success", "program_courses": data})


def get_course_details(request):
    """Get course details when a course code is selected from dropdown."""
    course_code = request.POST.get("course_code", "").strip()
    program_id = request.POST.get("program_id")
    
    if not course_code:
        return JsonResponse({"status": "error", "message": "course_code required"}, status=400)
    
    # Search for the course in ProgramCourse. Plain __iexact only catches
    # case differences, not the whitespace-variant duplicates that could
    # exist in older data (e.g. dropdown built from a 'BCOM112' row while
    # the user's typed/edited value is 'BCOM 112') -- match canonically
    # instead, same rule the COD panel curriculum matching uses.
    target_key = canonical_course_key(course_code)
    query = ProgramCourse.objects.filter(program_id=program_id) if program_id else ProgramCourse.objects.all()
    course = next(
        (c for c in query.select_related("program") if canonical_course_key(c.course_code) == target_key),
        None,
    )
    
    if course:
        return JsonResponse({
            "status": "success",
            "found": True,
            "course": {
                "id": course.id,
                "course_code": course.course_code,
                "course_name": course.course_name,
                "year": course.year,
                "semester": course.semester,
                "academic_year": f"{course.year}/{(course.year + 1) % 100}" if course.year else "",
                "semester_display": f"S{course.semester}" if course.semester else "",
                "program_id": course.program_id,
                "program_name": course.program.name if course.program else "",
            }
        })
    else:
        return JsonResponse({
            "status": "success",
            "found": False,
            "message": "Course not found. You can manually enter details."
        })


def search_lecturers(request, department):
    """Search lecturers by name."""
    q = request.POST.get("q", "").strip()
    
    if len(q) > 100:
        return JsonResponse({"status": "error", "message": "Search term too long"}, status=400)
    
    qs = Lecturer.objects.all()
    if q:
        qs = qs.filter(name__icontains=q)
    
    qs = qs.annotate(
        is_dept=Case(
            When(department=department, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    ).order_by("-is_dept", "name")[:30]
    
    data = [{"id": l.id, "label": l.display_name, "email": l.email} for l in qs]
    return JsonResponse({"status": "success", "lecturers": data})


def get_allocation_summary(request, department):
    """Get summary of allocations for the department."""
    semester_filter = request.POST.get("semester")
    
    qs = ResitCourseAllocation.objects.filter(
        Q(department=department) | Q(origin_department=department)
    ).select_related(
        "program", "lecturer", "program_course"
    )
    
    if semester_filter and semester_filter in ("1", "2", "S1", "S2"):
        clean_sem = semester_filter.replace("S", "")
        qs = qs.filter(program_course__semester=clean_sem)
    
    # Lecturer workload summary
    lecturer_counts = defaultdict(list)
    for a in qs:
        lect_name = a.lecturer.display_name if a.lecturer else "Unassigned"
        lecturer_counts[lect_name].append({
            "course_code": a.course_code,
            "course_name": a.course_name,
            "program": a.program.name if a.program else "-",
        })
    
    # Program summary
    program_summary = {}
    for a in qs:
        if a.program:
            prog_name = a.program.name
            if prog_name not in program_summary:
                program_summary[prog_name] = {
                    "total_courses": 0,
                    "submitted": 0,
                    "scheduled": 0,
                    "total_students": 0,
                }
            program_summary[prog_name]["total_courses"] += 1
            program_summary[prog_name]["total_students"] += a.number_of_students
            if a.submitted_to_timetabling:
                program_summary[prog_name]["submitted"] += 1
            if a.scheduled:
                program_summary[prog_name]["scheduled"] += 1
    
    return JsonResponse({
        "status": "success",
        "lecturer_summary": [
            {"lecturer": lect, "count": len(courses), "courses": courses}
            for lect, courses in sorted(lecturer_counts.items(), key=lambda x: -len(x[1]))
        ],
        "program_summary": [
            {
                "program": prog_name,
                "total_courses": data["total_courses"],
                "submitted": data["submitted"],
                "scheduled": data["scheduled"],
                "total_students": data["total_students"],
                "submission_pct": round(data["submitted"] / data["total_courses"] * 100) if data["total_courses"] > 0 else 0,
            }
            for prog_name, data in sorted(program_summary.items())
        ],
        "total_allocations": qs.count(),
        "total_submitted": qs.filter(submitted_to_timetabling=True).count(),
        "total_scheduled": qs.filter(scheduled=True).count(),
        "total_students": sum(a.number_of_students for a in qs),
    })


# ---------------------------------------------------------------
# Quick Enrollment Edit helpers
# ---------------------------------------------------------------

def list_allocations_for_enrollment(request, department):
    """
    Return all resit allocations for the department, grouped by program and
    year, for use in the Quick Enrollment Edit modal.
    """
    allocations = (
        ResitCourseAllocation.objects.filter(department=department)
        .select_related("program", "program_course")
        .order_by("program__name", "program_course__year", "course_code")
    )

    programs: dict = {}
    for a in allocations:
        prog_key = a.program_id
        prog_name = a.program.name if a.program else "Unlinked / No Program"
        year_key = a.program_course.year if a.program_course else None

        if prog_key not in programs:
            programs[prog_key] = {
                "program_id": prog_key,
                "program_name": prog_name,
                "years": {},
            }

        yrs = programs[prog_key]["years"]
        if year_key not in yrs:
            yrs[year_key] = {"year": year_key, "courses": []}

        yrs[year_key]["courses"].append({
            "id": a.id,
            "course_code": a.course_code,
            "course_name": a.course_name,
            "number_of_students": a.number_of_students,
        })

    result = []
    for prog in sorted(programs.values(), key=lambda p: p["program_name"] or ""):
        sorted_years = sorted(
            prog["years"].values(),
            key=lambda y: (y["year"] is None, y["year"] or 0),
        )
        result.append({
            "program_id": prog["program_id"],
            "program_name": prog["program_name"],
            "years": sorted_years,
        })

    return JsonResponse({"status": "success", "programs": result})


def update_student_count(request, department):
    """
    Patch the number_of_students for a single ResitCourseAllocation.
    POST params: id, number_of_students
    """
    allocation_id = request.POST.get("id")
    raw_count = request.POST.get("number_of_students", "")

    if not allocation_id:
        return JsonResponse({"status": "error", "message": "Allocation ID required"}, status=400)

    try:
        count = int(raw_count)
        if count < 0 or count > 10000:
            return JsonResponse(
                {"status": "error", "message": "Student count must be between 0 and 10 000."},
                status=400,
            )
    except (ValueError, TypeError):
        return JsonResponse({"status": "error", "message": "Invalid student count."}, status=400)

    allocation = get_object_or_404(ResitCourseAllocation, pk=allocation_id, department=department)
    allocation.number_of_students = count
    allocation.save(update_fields=["number_of_students", "updated_at"])

    return JsonResponse({
        "status": "success",
        "id": allocation.id,
        "number_of_students": allocation.number_of_students,
    })


# ---------------------------------------------------------------
# Student Management Handlers
# ---------------------------------------------------------------

def add_student_to_allocation(request, department):
    """
    Add a student to a resit allocation.
    POST params: allocation_id, reg_no, student_name (optional)
    """
    allocation_id = request.POST.get("allocation_id")
    reg_no = request.POST.get("reg_no", "").strip()
    student_name = request.POST.get("student_name", "").strip()
    
    if not allocation_id or not reg_no:
        return JsonResponse({"status": "error", "message": "Allocation ID and registration number are required"}, status=400)
    
    try:
        allocation = ResitCourseAllocation.objects.get(pk=allocation_id, department=department)
    except ResitCourseAllocation.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Allocation not found"}, status=404)
    
    normalized = normalize_reg_number(reg_no)
    
    # Check for duplicate
    if StudentResitRegistration.objects.filter(
        resit_allocation=allocation,
        student_reg_no_normalized=normalized
    ).exists():
        return JsonResponse({
            "status": "error",
            "message": f"Student {normalized} is already registered for this course"
        }, status=400)
    
    registration = StudentResitRegistration.objects.create(
        student_reg_no=reg_no,
        student_name=student_name,
        resit_allocation=allocation,
        registered_by=request.user,
    )
    
    # Update the allocation's student count
    allocation.number_of_students = allocation.student_registrations.count()
    allocation.save(update_fields=["number_of_students", "updated_at"])
    
    return JsonResponse({
        "status": "success",
        "message": "Student added successfully",
        "registration": {
            "id": registration.id,
            "student_reg_no": registration.student_reg_no,
            "student_reg_no_normalized": registration.student_reg_no_normalized,
            "student_name": registration.student_name,
        },
        "new_count": allocation.number_of_students,
    })


def remove_student_from_allocation(request, department):
    """
    Remove a student from a resit allocation.
    POST params: registration_id
    """
    registration_id = request.POST.get("registration_id")
    
    if not registration_id:
        return JsonResponse({"status": "error", "message": "Registration ID required"}, status=400)
    
    try:
        registration = StudentResitRegistration.objects.get(
            pk=registration_id,
            resit_allocation__department=department
        )
        allocation = registration.resit_allocation
        registration.delete()
        
        # Update the allocation's student count
        allocation.number_of_students = allocation.student_registrations.count()
        allocation.save(update_fields=["number_of_students", "updated_at"])
        
        return JsonResponse({
            "status": "success",
            "message": "Student removed successfully",
            "new_count": allocation.number_of_students,
        })
    except StudentResitRegistration.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Registration not found"}, status=404)


def get_allocation_students(request, department):
    """
    Get list of students registered for a specific allocation.
    POST params: allocation_id
    """
    allocation_id = request.POST.get("allocation_id")
    
    if not allocation_id:
        return JsonResponse({"status": "error", "message": "Allocation ID required"}, status=400)
    
    try:
        allocation = ResitCourseAllocation.objects.get(pk=allocation_id, department=department)
    except ResitCourseAllocation.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Allocation not found"}, status=404)
    
    students = list(allocation.student_registrations.values(
        'id', 'student_reg_no', 'student_reg_no_normalized', 'student_name'
    ).order_by('student_reg_no_normalized'))
    
    return JsonResponse({
        "status": "success",
        "allocation_id": allocation.id,
        "course_code": allocation.course_code,
        "students": students,
        "total": len(students),
    })


def search_students(request):
    """
    Search for students by registration number or name.
    This searches across all StudentResitRegistration records in the system.
    GET/POST params: q (search query)
    """
    q = request.GET.get("q", "") or request.POST.get("q", "")
    q = q.strip()
    
    if not q or len(q) < 2:
        return JsonResponse({"status": "success", "students": []})
    
    # Normalize the search term
    normalized_q = normalize_reg_number(q)
    
    # Search in StudentResitRegistration
    students = StudentResitRegistration.objects.filter(
        Q(student_reg_no__icontains=q) |
        Q(student_reg_no_normalized__icontains=normalized_q) |
        Q(student_name__icontains=q)
    ).values('student_reg_no', 'student_reg_no_normalized', 'student_name').distinct()[:20]
    
    return JsonResponse({
        "status": "success",
        "students": list(students),
    })


# ---------------------------------------------------------------
# Additional endpoints
# ---------------------------------------------------------------

@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
@require_GET
def get_department_resit_courses(request):
    """AJAX: Return course codes for the COD's department that have resit entries."""
    department = get_user_department(request.user)
    if not department:
        return JsonResponse({"error": "No department found for this user."}, status=400)
    
    allocations = ResitCourseAllocation.objects.filter(department=department)
    course_codes = list(allocations.values_list("course_code", flat=True).distinct())
    
    resit_scheduled = list(
        ResitTimetable.objects.filter(
            resit_course_allocation__department=department
        ).values_list("resit_course_allocation__course_code", flat=True).distinct()
    )
    
    return JsonResponse({
        "department": str(department),
        "department_id": department.id,
        "total_courses": len(course_codes),
        "scheduled_for_resit": resit_scheduled,
        "unscheduled": [c for c in course_codes if c not in resit_scheduled],
    })