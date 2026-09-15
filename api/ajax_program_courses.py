import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from department_management.models import Department
from program_management.models import Program, ProgramCourse, ProgramCode
from program_management.programs_page import detect_user_department
from program_management.code_utils import normalize_code, canonical_course_key

logger = logging.getLogger(__name__)

VALID_UNIT_TYPES = ['CORE', 'ELECTIVE', 'UNIVERSITY_WIDE', 'REQUIRED_ELECTIVE']


def _in_scope(detected_dept, department_id):
    """A COD/COD-Admin can only touch their own department; everyone else is unrestricted."""
    if not detected_dept:
        return True
    return str(department_id) == str(detected_dept.id)


@login_required
@require_POST
def ajax_program_courses(request):
    """
    AJAX CRUD for Program, ProgramCourse, and ProgramCode.
    Department-scoped: COD / COD Admin users are restricted to their own
    department (via detect_user_department); everyone else (DVC, TT, etc.)
    can manage all departments.
    """
    action = request.POST.get("action", "").strip()
    detected_dept = detect_user_department(request.user)

    try:
        handler = {
            "add_program": _add_program,
            "edit_program": _edit_program,
            "delete_program": _delete_program,
            "add_course": _add_course,
            "edit_course": _edit_course,
            "delete_course": _delete_course,
            "add_program_code": _add_program_code,
            "edit_program_code": _edit_program_code,
            "delete_program_code": _delete_program_code,
        }.get(action)

        if not handler:
            return JsonResponse({"status": "error", "message": f"Unknown action: {action}"}, status=400)

        return handler(request, detected_dept)
    except Exception as e:
        logger.exception("Error in ajax_program_courses (action=%s): %s", action, e)
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


# ── Program CRUD ──

def _add_program(request, detected_dept):
    name = (request.POST.get("name") or "").strip()
    department_id = request.POST.get("department")
    default_cohort = (request.POST.get("default_cohort") or "0").strip() or "0"
    description = request.POST.get("description", "")

    if not name:
        return JsonResponse({"status": "error", "message": "Program name is required."}, status=400)

    if detected_dept:
        if department_id and not _in_scope(detected_dept, department_id):
            return JsonResponse({"status": "error", "message": "You can only add programs to your own department."}, status=403)
        department = detected_dept
    else:
        if not department_id:
            return JsonResponse({"status": "error", "message": "Department is required."}, status=400)
        department = get_object_or_404(Department, pk=department_id)

    if Program.objects.filter(name__iexact=name, department=department).exists():
        return JsonResponse({"status": "error", "message": f"Program '{name}' already exists in this department."}, status=400)

    program = Program.objects.create(
        name=name, description=description, department=department, default_cohort=default_cohort,
    )
    return JsonResponse({"status": "success", "id": program.id, "message": f"Program '{name}' added."})


def _edit_program(request, detected_dept):
    program = get_object_or_404(Program, pk=request.POST.get("id"))
    if not _in_scope(detected_dept, program.department_id):
        return JsonResponse({"status": "error", "message": "You can only edit programs in your own department."}, status=403)

    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"status": "error", "message": "Program name is required."}, status=400)

    department_id = request.POST.get("department")
    if department_id:
        if not _in_scope(detected_dept, department_id):
            return JsonResponse({"status": "error", "message": "You can only move programs within your own department."}, status=403)
        department = get_object_or_404(Department, pk=department_id)
        program.department = department

    if Program.objects.filter(name__iexact=name, department=program.department).exclude(pk=program.pk).exists():
        return JsonResponse({"status": "error", "message": f"Program '{name}' already exists in this department."}, status=400)

    program.name = name
    program.description = request.POST.get("description", program.description)
    program.default_cohort = (request.POST.get("default_cohort") or program.default_cohort or "0").strip() or "0"
    program.save()
    return JsonResponse({"status": "success", "message": "Program updated."})


def _delete_program(request, detected_dept):
    program = get_object_or_404(Program, pk=request.POST.get("id"))
    if not _in_scope(detected_dept, program.department_id):
        return JsonResponse({"status": "error", "message": "You can only delete programs in your own department."}, status=403)
    program.delete()
    return JsonResponse({"status": "success", "message": "Program deleted."})


# ── Course CRUD ──

def _add_course(request, detected_dept):
    program = get_object_or_404(Program, pk=request.POST.get("program"))
    if not _in_scope(detected_dept, program.department_id):
        return JsonResponse({"status": "error", "message": "You can only add courses to programs in your own department."}, status=403)

    # Previously this endpoint only .strip()'d course_code -- no upper-casing,
    # no space normalization -- so 'bcom112', 'BCOM112', and 'BCOM 112' all
    # got saved as distinct rows for the same program/cohort. Route through
    # the shared normalizer so this path matches the other 3 curriculum-
    # writing paths (COD panel, CSV import, admin import-export).
    course_code = normalize_code((request.POST.get("course_code") or "").strip())
    course_name = (request.POST.get("course_name") or "").strip()
    if not course_code or not course_name:
        return JsonResponse({"status": "error", "message": "Course code and name are required."}, status=400)

    year = request.POST.get("year") or 1
    semester = request.POST.get("semester") or 1
    unit_type = ProgramCourse.normalize_unit_type(request.POST.get("unit_type") or "CORE")
    student_cohort = (request.POST.get("student_cohort") or "0").strip() or "0"

    # Canonical (whitespace/case/separator-insensitive) duplicate check --
    # a literal filter(course_code=...) would miss 'BCOM112' vs 'BCOM 112'.
    target_key = canonical_course_key(course_code)
    duplicate = next(
        (pc for pc in ProgramCourse.objects.filter(program=program, student_cohort=student_cohort)
         if canonical_course_key(pc.course_code) == target_key),
        None,
    )
    if duplicate:
        return JsonResponse({
            "status": "error",
            "message": f"Course '{course_code}' already exists for cohort {student_cohort} (saved as '{duplicate.course_code}')."
        }, status=400)

    course = ProgramCourse.objects.create(
        program=program, course_code=course_code, course_name=course_name,
        year=year, semester=semester, unit_type=unit_type, student_cohort=student_cohort,
    )
    return JsonResponse({"status": "success", "id": course.id, "message": f"Course '{course_code}' added."})


def _edit_course(request, detected_dept):
    course = get_object_or_404(ProgramCourse, pk=request.POST.get("id"))
    if not _in_scope(detected_dept, course.program.department_id):
        return JsonResponse({"status": "error", "message": "You can only edit courses in your own department."}, status=403)

    course_code = normalize_code((request.POST.get("course_code") or "").strip())
    course_name = (request.POST.get("course_name") or "").strip()
    if not course_code or not course_name:
        return JsonResponse({"status": "error", "message": "Course code and name are required."}, status=400)

    unit_type = ProgramCourse.normalize_unit_type(request.POST.get("unit_type") or course.unit_type or "CORE")
    student_cohort = (request.POST.get("student_cohort") or course.student_cohort or "0").strip() or "0"

    # Canonical duplicate check against every OTHER course in this program
    # + cohort, so editing one row's spacing/case can't collide with -- or
    # silently create a near-duplicate of -- another existing row.
    target_key = canonical_course_key(course_code)
    duplicate = next(
        (pc for pc in ProgramCourse.objects.filter(program=course.program, student_cohort=student_cohort).exclude(pk=course.pk)
         if canonical_course_key(pc.course_code) == target_key),
        None,
    )
    if duplicate:
        return JsonResponse({
            "status": "error",
            "message": f"Course '{course_code}' already exists for cohort {student_cohort} (saved as '{duplicate.course_code}')."
        }, status=400)

    course.course_code = course_code
    course.course_name = course_name
    course.year = request.POST.get("year") or course.year or 1
    course.semester = request.POST.get("semester") or course.semester or 1
    course.unit_type = unit_type
    course.student_cohort = student_cohort
    course.save()
    return JsonResponse({"status": "success", "message": "Course updated."})


def _delete_course(request, detected_dept):
    course = get_object_or_404(ProgramCourse, pk=request.POST.get("id"))
    if not _in_scope(detected_dept, course.program.department_id):
        return JsonResponse({"status": "error", "message": "You can only delete courses in your own department."}, status=403)
    course.delete()
    return JsonResponse({"status": "success", "message": "Course deleted."})


# ── Program Code CRUD ──

def _add_program_code(request, detected_dept):
    program = get_object_or_404(Program, pk=request.POST.get("program"))
    if not _in_scope(detected_dept, program.department_id):
        return JsonResponse({"status": "error", "message": "You can only add codes to programs in your own department."}, status=403)

    code = (request.POST.get("code") or "").strip().upper()
    if not code:
        return JsonResponse({"status": "error", "message": "Code is required."}, status=400)

    if ProgramCode.objects.filter(code=code).exists():
        return JsonResponse({"status": "error", "message": f"Code '{code}' already exists."}, status=400)

    program_code = ProgramCode.objects.create(program=program, code=code)
    return JsonResponse({"status": "success", "id": program_code.id, "message": f"Code '{code}' added."})


def _edit_program_code(request, detected_dept):
    program_code = get_object_or_404(ProgramCode, pk=request.POST.get("id"))
    if not _in_scope(detected_dept, program_code.program.department_id):
        return JsonResponse({"status": "error", "message": "You can only edit codes in your own department."}, status=403)

    code = (request.POST.get("code") or "").strip().upper()
    if not code:
        return JsonResponse({"status": "error", "message": "Code is required."}, status=400)

    program_id = request.POST.get("program")
    if program_id:
        program = get_object_or_404(Program, pk=program_id)
        if not _in_scope(detected_dept, program.department_id):
            return JsonResponse({"status": "error", "message": "You can only assign codes to programs in your own department."}, status=403)
        program_code.program = program

    if ProgramCode.objects.filter(code=code).exclude(pk=program_code.pk).exists():
        return JsonResponse({"status": "error", "message": f"Code '{code}' already exists."}, status=400)

    program_code.code = code
    program_code.save()
    return JsonResponse({"status": "success", "message": "Code updated."})


def _delete_program_code(request, detected_dept):
    program_code = get_object_or_404(ProgramCode, pk=request.POST.get("id"))
    if not _in_scope(detected_dept, program_code.program.department_id):
        return JsonResponse({"status": "error", "message": "You can only delete codes in your own department."}, status=403)
    program_code.delete()
    return JsonResponse({"status": "success", "message": "Code deleted."})
