# faculty_management/dvc_panel.py
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.http import JsonResponse
from django.contrib.auth.models import User, Group
from django.db import IntegrityError
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q, Count
import json
from collections import OrderedDict

from faculty_management.models import Faculty
from course_allocation.models import CourseAllocation, DVCActionLog
from department_management.models import Department
from program_management.models import Program, ProgramCourse
from lecturer_portal.models import Lecturer
from admins.forms import FacultyForm
from core.group_required import group_required
from django.core.cache import cache

# ── NEW: Campus & ODEL models ─────────────────────────────────────────────────
from campuses_timetable.models import CampusCourseAllocation, Campus, CampusSubmissionControl
from odel_system.models import ODELCourseAllocation


# ─────────────────────────────────────────────────────────────────────────────
# STATE MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def get_dvc_panel_state(request):
    if 'dvc_panel_state' not in request.session:
        request.session['dvc_panel_state'] = {
            'active_panel': 'allocations-panel',
            'group_by': 'lecturer',
            'last_faculty': None,
            'last_department': None,
            'last_lecturer': None,
            'search_filters': {},
            'selected_faculties': [],
            'selected_departments': [],
            'pagination': {'allocations_page': 1, 'disapproved_page': 1, 'faculties_page': 1}
        }
    return request.session['dvc_panel_state']


def update_dvc_panel_state(request, updates):
    state = get_dvc_panel_state(request)
    state.update(updates)
    request.session['dvc_panel_state'] = state
    request.session.modified = True
    return state


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def normalize_name(name):
    return name.strip().replace(" ", "_").lower()

def generate_default_password(username):
    return f"{username}@2025"

def create_dean_and_admin(faculty):
    base_name = normalize_name(faculty.name)
    dean_username       = f"dean_{base_name}"
    dean_admin_username = f"{dean_username}_admin"
    dean_user, created = User.objects.get_or_create(username=dean_username)
    if created or not dean_user.has_usable_password():
        dean_user.set_password(generate_default_password(dean_username))
        dean_user.save()
    dean_group, _ = Group.objects.get_or_create(name="Dean")
    if dean_group not in dean_user.groups.all():
        dean_user.groups.add(dean_group)
    faculty.leader = dean_user
    faculty.save(update_fields=["leader"])
    admin_user, created = User.objects.get_or_create(username=dean_admin_username)
    if created or not admin_user.has_usable_password():
        admin_user.set_password(generate_default_password(dean_admin_username))
        admin_user.save()
    admin_group, _ = Group.objects.get_or_create(name="Dean Admins")
    if admin_group not in admin_user.groups.all():
        admin_user.groups.add(admin_group)
    return dean_user, admin_user

def get_allocation_status(allocation):
    if getattr(allocation, "approved_by_dvc", False):
        return "Approved"
    if getattr(allocation, "rejected_by_dvc", False) or getattr(allocation, "rejected", False):
        return "Rejected"
    return "Pending"

def _has_rejected_field():
    return "rejected_by_dvc" in [f.name for f in CourseAllocation._meta.fields]

def _resolve_department(identifier):
    if not identifier: return None
    try: return Department.objects.get(pk=int(identifier))
    except (ValueError, TypeError, Department.DoesNotExist): pass
    try: return Department.objects.get(name__iexact=identifier)
    except Department.DoesNotExist: pass
    try: return Department.objects.get(code__iexact=identifier)
    except Exception: pass
    return None

def _resolve_faculty(identifier):
    if not identifier: return None
    try: return Faculty.objects.get(pk=int(identifier))
    except (ValueError, TypeError, Faculty.DoesNotExist): pass
    try: return Faculty.objects.get(name__iexact=identifier)
    except Faculty.DoesNotExist: pass
    return None

def _resolve_lecturer(identifier):
    if not identifier: return None
    try: return Lecturer.objects.get(pk=int(identifier))
    except (ValueError, TypeError, Lecturer.DoesNotExist): pass
    try: return Lecturer.objects.get(user__username__iexact=identifier)
    except Lecturer.DoesNotExist: pass
    return None


def _log_dvc_action(request, alloc_type, action_is_approve, department=None, lecturer=None,
                     course_code="", course_name="", count=1):
    """
    Write one (or `count` identical) DVCActionLog rows for an approve/reject event.

    These are intentionally left with reported=False — a periodic Celery task
    (notifications.tasks.flush_idle_dvc_batches) rolls them up into a single
    "DVC finished reviewing" report per department once the DVC has been idle
    for a few minutes, rather than firing a notification per click.
    """
    action = DVCActionLog.ACTION_APPROVE if action_is_approve else DVCActionLog.ACTION_REJECT
    rows = [
        DVCActionLog(
            department=department, lecturer=lecturer, alloc_type=alloc_type,
            action=action, actor=request.user if request.user.is_authenticated else None,
            course_code=course_code, course_name=course_name,
        )
        for _ in range(max(count, 1))
    ]
    DVCActionLog.objects.bulk_create(rows)


# ─────────────────────────────────────────────────────────────────────────────
# STANDARD ALLOCATION GROUPING
# ─────────────────────────────────────────────────────────────────────────────

def group_by_lecturer(allocations):
    grouped = OrderedDict()
    for alloc in allocations:
        lecturer_name = alloc.lecturer.display_name if alloc.lecturer else "Unassigned"
        lecturer_id   = alloc.lecturer.id if alloc.lecturer else 0
        dept_name     = alloc.department.name if alloc.department else "No Department"
        dept_id       = alloc.department.id if alloc.department else None
        faculty_name  = "No Faculty"
        faculty_id    = None
        if alloc.department and alloc.department.faculty:
            faculty_name = alloc.department.faculty.name
            faculty_id   = alloc.department.faculty.id
        if lecturer_name not in grouped:
            grouped[lecturer_name] = {'id': lecturer_id, 'departments': OrderedDict(),
                                       'counts': {'total':0,'approved':0,'rejected':0,'pending':0}}
        if dept_name not in grouped[lecturer_name]['departments']:
            grouped[lecturer_name]['departments'][dept_name] = {
                'id': dept_id, 'faculty': faculty_name, 'faculty_id': faculty_id, 'courses': []}
        program_name = "N/A"; year = "—"; semester = "—"
        if alloc.program_course:
            year = alloc.program_course.year; semester = alloc.program_course.semester
            if alloc.program_course.program: program_name = alloc.program_course.program.name
        elif alloc.program: program_name = alloc.program.name
        grouped[lecturer_name]['departments'][dept_name]['courses'].append({
            'id': alloc.id, 'code': alloc.course_code, 'name': alloc.course_name,
            'program': program_name, 'year': year, 'semester': semester,
            'status': get_allocation_status(alloc), 'approved': alloc.approved_by_dvc,
            'rejected': alloc.rejected_by_dvc,
            'intake': alloc.get_intake_display() if hasattr(alloc,'get_intake_display') else 'Normal',
            'is_elective': alloc.is_elective, 'students': alloc.number_of_students,
        })
        counts = grouped[lecturer_name]['counts']
        counts['total'] += 1
        if alloc.approved_by_dvc: counts['approved'] += 1
        elif alloc.rejected_by_dvc: counts['rejected'] += 1
        else: counts['pending'] += 1
    return grouped


def group_by_department(allocations):
    grouped = OrderedDict()
    for alloc in allocations:
        dept_name    = alloc.department.name if alloc.department else "No Department"
        dept_id      = alloc.department.id if alloc.department else None
        faculty_name = "No Faculty"; faculty_id = None
        if alloc.department and alloc.department.faculty:
            faculty_name = alloc.department.faculty.name; faculty_id = alloc.department.faculty.id
        lecturer_name = alloc.lecturer.display_name if alloc.lecturer else "Unassigned"
        lecturer_id   = alloc.lecturer.id if alloc.lecturer else 0
        if dept_name not in grouped:
            grouped[dept_name] = {'id': dept_id, 'faculty': faculty_name, 'faculty_id': faculty_id,
                                   'lecturers': OrderedDict(), 'counts': {'total':0,'approved':0,'rejected':0,'pending':0}}
        if lecturer_name not in grouped[dept_name]['lecturers']:
            grouped[dept_name]['lecturers'][lecturer_name] = {'id': lecturer_id, 'courses': []}
        program_name = "N/A"; year = "—"; semester = "—"
        if alloc.program_course:
            year = alloc.program_course.year; semester = alloc.program_course.semester
            if alloc.program_course.program: program_name = alloc.program_course.program.name
        elif alloc.program: program_name = alloc.program.name
        grouped[dept_name]['lecturers'][lecturer_name]['courses'].append({
            'id': alloc.id, 'code': alloc.course_code, 'name': alloc.course_name,
            'program': program_name, 'year': year, 'semester': semester,
            'status': get_allocation_status(alloc), 'approved': alloc.approved_by_dvc,
            'rejected': alloc.rejected_by_dvc,
            'intake': alloc.get_intake_display() if hasattr(alloc,'get_intake_display') else 'Normal',
            'is_elective': alloc.is_elective, 'students': alloc.number_of_students,
            'lecturer': lecturer_name, 'lecturer_id': lecturer_id,
        })
        counts = grouped[dept_name]['counts']
        counts['total'] += 1
        if alloc.approved_by_dvc: counts['approved'] += 1
        elif alloc.rejected_by_dvc: counts['rejected'] += 1
        else: counts['pending'] += 1
    return grouped


# ─────────────────────────────────────────────────────────────────────────────
# CAMPUS ALLOCATION GROUPING
# ─────────────────────────────────────────────────────────────────────────────

def group_campus_by_lecturer(allocations):
    grouped = OrderedDict()
    for alloc in allocations:
        lecturer_name = alloc.lecturer.display_name if alloc.lecturer else "Unassigned"
        lecturer_id   = alloc.lecturer.id if alloc.lecturer else 0
        dept_name     = alloc.department.name if alloc.department else "No Department"
        dept_id       = alloc.department.id if alloc.department else None
        campus_name   = alloc.campus.name if alloc.campus else "No Campus"
        campus_code   = alloc.campus.code if alloc.campus else "—"
        faculty_name  = "No Faculty"; faculty_id = None
        if alloc.department and alloc.department.faculty:
            faculty_name = alloc.department.faculty.name; faculty_id = alloc.department.faculty.id
        if lecturer_name not in grouped:
            grouped[lecturer_name] = {'id': lecturer_id, 'departments': OrderedDict(),
                                       'counts': {'total':0,'approved':0,'rejected':0,'pending':0}}
        key = f"{dept_name} [{campus_code}]"
        if key not in grouped[lecturer_name]['departments']:
            grouped[lecturer_name]['departments'][key] = {
                'id': dept_id, 'faculty': faculty_name, 'faculty_id': faculty_id,
                'campus': campus_name, 'campus_code': campus_code, 'courses': []}
        program_name = alloc.program.name if alloc.program else "N/A"
        grouped[lecturer_name]['departments'][key]['courses'].append({
            'id': alloc.id, 'code': alloc.course_code, 'name': alloc.course_name,
            'program': program_name, 'year': alloc.program_year or "—",
            'semester': alloc.allocation_semester or "—",
            'status': get_allocation_status(alloc), 'approved': alloc.approved_by_dvc,
            'rejected': alloc.rejected_by_dvc,
            'delivery_mode': alloc.get_delivery_mode_display() if hasattr(alloc,'get_delivery_mode_display') else alloc.delivery_mode,
            'students': alloc.number_of_students, 'campus': campus_name, 'campus_code': campus_code,
            'alloc_type': 'campus',
        })
        counts = grouped[lecturer_name]['counts']
        counts['total'] += 1
        if alloc.approved_by_dvc: counts['approved'] += 1
        elif alloc.rejected_by_dvc: counts['rejected'] += 1
        else: counts['pending'] += 1
    return grouped


def group_campus_by_department(allocations):
    grouped = OrderedDict()
    for alloc in allocations:
        dept_name    = alloc.department.name if alloc.department else "No Department"
        dept_id      = alloc.department.id if alloc.department else None
        campus_name  = alloc.campus.name if alloc.campus else "No Campus"
        campus_code  = alloc.campus.code if alloc.campus else "—"
        faculty_name = "No Faculty"; faculty_id = None
        if alloc.department and alloc.department.faculty:
            faculty_name = alloc.department.faculty.name; faculty_id = alloc.department.faculty.id
        lecturer_name = alloc.lecturer.display_name if alloc.lecturer else "Unassigned"
        lecturer_id   = alloc.lecturer.id if alloc.lecturer else 0
        key = f"{dept_name} [{campus_code}]"
        if key not in grouped:
            grouped[key] = {'id': dept_id, 'faculty': faculty_name, 'faculty_id': faculty_id,
                             'campus': campus_name, 'campus_code': campus_code,
                             'lecturers': OrderedDict(), 'counts': {'total':0,'approved':0,'rejected':0,'pending':0}}
        if lecturer_name not in grouped[key]['lecturers']:
            grouped[key]['lecturers'][lecturer_name] = {'id': lecturer_id, 'courses': []}
        program_name = alloc.program.name if alloc.program else "N/A"
        grouped[key]['lecturers'][lecturer_name]['courses'].append({
            'id': alloc.id, 'code': alloc.course_code, 'name': alloc.course_name,
            'program': program_name, 'year': alloc.program_year or "—",
            'semester': alloc.allocation_semester or "—",
            'status': get_allocation_status(alloc), 'approved': alloc.approved_by_dvc,
            'rejected': alloc.rejected_by_dvc,
            'delivery_mode': alloc.get_delivery_mode_display() if hasattr(alloc,'get_delivery_mode_display') else alloc.delivery_mode,
            'students': alloc.number_of_students, 'campus': campus_name, 'campus_code': campus_code,
            'alloc_type': 'campus',
        })
        counts = grouped[key]['counts']
        counts['total'] += 1
        if alloc.approved_by_dvc: counts['approved'] += 1
        elif alloc.rejected_by_dvc: counts['rejected'] += 1
        else: counts['pending'] += 1
    return grouped


# ─────────────────────────────────────────────────────────────────────────────
# ODEL ALLOCATION GROUPING
# ─────────────────────────────────────────────────────────────────────────────

def group_odel_by_lecturer(allocations):
    grouped = OrderedDict()
    for alloc in allocations:
        lecturer_name = alloc.lecturer.display_name if alloc.lecturer else "Unassigned"
        lecturer_id   = alloc.lecturer.id if alloc.lecturer else 0
        dept = alloc.department
        dept_name = dept.name if dept else "No Department"; dept_id = dept.id if dept else None
        faculty_name = "No Faculty"; faculty_id = None
        if dept and dept.faculty: faculty_name = dept.faculty.name; faculty_id = dept.faculty.id
        if lecturer_name not in grouped:
            grouped[lecturer_name] = {'id': lecturer_id, 'departments': OrderedDict(),
                                       'counts': {'total':0,'approved':0,'rejected':0,'pending':0}}
        if dept_name not in grouped[lecturer_name]['departments']:
            grouped[lecturer_name]['departments'][dept_name] = {
                'id': dept_id, 'faculty': faculty_name, 'faculty_id': faculty_id, 'courses': []}
        pc = alloc.program_course
        program_name = pc.program.name if pc and pc.program else "N/A"
        year = pc.year if pc else "—"; semester = pc.semester if pc else "—"
        grouped[lecturer_name]['departments'][dept_name]['courses'].append({
            'id': alloc.id, 'code': alloc.course_code, 'name': alloc.course_name,
            'program': program_name, 'year': year, 'semester': semester,
            'status': get_allocation_status(alloc), 'approved': alloc.approved_by_dvc,
            'rejected': alloc.rejected, 'students': alloc.number_of_students, 'alloc_type': 'odel',
        })
        counts = grouped[lecturer_name]['counts']
        counts['total'] += 1
        if alloc.approved_by_dvc: counts['approved'] += 1
        elif alloc.rejected: counts['rejected'] += 1
        else: counts['pending'] += 1
    return grouped


def group_odel_by_department(allocations):
    grouped = OrderedDict()
    for alloc in allocations:
        dept = alloc.department
        dept_name = dept.name if dept else "No Department"; dept_id = dept.id if dept else None
        faculty_name = "No Faculty"; faculty_id = None
        if dept and dept.faculty: faculty_name = dept.faculty.name; faculty_id = dept.faculty.id
        lecturer_name = alloc.lecturer.display_name if alloc.lecturer else "Unassigned"
        lecturer_id   = alloc.lecturer.id if alloc.lecturer else 0
        if dept_name not in grouped:
            grouped[dept_name] = {'id': dept_id, 'faculty': faculty_name, 'faculty_id': faculty_id,
                                   'lecturers': OrderedDict(), 'counts': {'total':0,'approved':0,'rejected':0,'pending':0}}
        if lecturer_name not in grouped[dept_name]['lecturers']:
            grouped[dept_name]['lecturers'][lecturer_name] = {'id': lecturer_id, 'courses': []}
        pc = alloc.program_course
        program_name = pc.program.name if pc and pc.program else "N/A"
        year = pc.year if pc else "—"; semester = pc.semester if pc else "—"
        grouped[dept_name]['lecturers'][lecturer_name]['courses'].append({
            'id': alloc.id, 'code': alloc.course_code, 'name': alloc.course_name,
            'program': program_name, 'year': year, 'semester': semester,
            'status': get_allocation_status(alloc), 'approved': alloc.approved_by_dvc,
            'rejected': alloc.rejected, 'students': alloc.number_of_students, 'alloc_type': 'odel',
        })
        counts = grouped[dept_name]['counts']
        counts['total'] += 1
        if alloc.approved_by_dvc: counts['approved'] += 1
        elif alloc.rejected: counts['rejected'] += 1
        else: counts['pending'] += 1
    return grouped


# ─────────────────────────────────────────────────────────────────────────────
# DVC PANEL  (main view)
# ─────────────────────────────────────────────────────────────────────────────

@allowed_roles(Role.DVC, Role.DVC_ADMIN, Role.SUDO)
def dvc_panel(request):
    """
    DVC Panel — shows Standard, Campus, and ODEL course allocations.
    """
    state = get_dvc_panel_state(request)

    # ── AJAX POST handler ─────────────────────────────────────────────────────
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        action = (request.POST.get("action") or "").strip()

        if action == "update_active_panel":
            panel = request.POST.get("panel")
            update_dvc_panel_state(request, {'active_panel': panel})
            return JsonResponse({"status": "success", "active_panel": panel})

        if action == "toggle_group_by":
            new_group_by = request.POST.get("group_by", "lecturer")
            update_dvc_panel_state(request, {'group_by': new_group_by})
            return JsonResponse({"status": "success", "group_by": new_group_by})

        # Faculty CRUD
        if action in ("create_faculty", "add_faculty"):
            name = request.POST.get("name", "").strip()
            desc = request.POST.get("description", "").strip()
            if not name:
                return JsonResponse({"status": "error", "message": "Name is required"}, status=400)
            form = FacultyForm({"name": name})
            if not form.is_valid():
                return JsonResponse({"status": "error", "message": form.errors.get_json_data()}, status=400)
            try:
                faculty = Faculty.objects.create(name=name, description=desc or None)
            except IntegrityError:
                return JsonResponse({"status": "error", "message": "Faculty already exists"}, status=400)
            create_dean_and_admin(faculty)
            update_dvc_panel_state(request, {'last_faculty': faculty.id, 'active_panel': 'faculty-panel'})
            dvc_group, _ = Group.objects.get_or_create(name="DVC Admins")
            if request.user.is_authenticated:
                request.user.groups.add(dvc_group)
            return JsonResponse({"status": "success", "message": "Faculty created successfully",
                                  "faculty": {"id": faculty.id, "name": faculty.name,
                                              "description": faculty.description,
                                              "leader": faculty.leader.username if faculty.leader else None},
                                  "redirect_panel": "faculty-panel"})

        if action == "edit_faculty":
            faculty = get_object_or_404(Faculty, pk=request.POST.get("id"))
            name = request.POST.get("name", "").strip(); desc = request.POST.get("description", "").strip()
            form = FacultyForm({"name": name}, instance=faculty)
            if not form.is_valid():
                return JsonResponse({"status": "error", "message": form.errors.get_json_data()}, status=400)
            faculty.name = name or faculty.name
            if desc: faculty.description = desc
            faculty.save(update_fields=["name", "description"])
            create_dean_and_admin(faculty)
            return JsonResponse({"status": "success", "message": "Faculty updated successfully",
                                  "faculty": {"id": faculty.id, "name": faculty.name,
                                              "description": faculty.description,
                                              "leader": faculty.leader.username if faculty.leader else None}})

        if action == "delete_faculty":
            get_object_or_404(Faculty, pk=request.POST.get("id")).delete()
            return JsonResponse({"status": "success", "message": "Faculty deleted"})

        # ── Standard allocation actions ───────────────────────────────────────
        if action in ("approve_allocation", "reject_allocation"):
            allocation = get_object_or_404(CourseAllocation, pk=request.POST.get("id"))
            is_approve = (action == "approve_allocation")
            allocation.approved_by_dvc = is_approve
            if _has_rejected_field():
                allocation.rejected_by_dvc = (action == "reject_allocation")
            allocation.save()
            _log_dvc_action(request, DVCActionLog.ALLOC_STANDARD, is_approve,
                             department=allocation.department, lecturer=allocation.lecturer,
                             course_code=allocation.course_code, course_name=allocation.course_name)
            update_dvc_panel_state(request, {'active_panel': 'allocations-panel'})
            return JsonResponse({"status": "success", "message": f"Course {action.replace('_',' ').title()}",
                                  "id": allocation.id, "status_display": get_allocation_status(allocation)})

        if action in ("approve_all_allocations", "reject_all_allocations"):
            is_approve = (action == "approve_all_allocations")
            qs = CourseAllocation.objects.all()
            affected = list(qs.values_list("department_id", flat=True))
            updated = qs.update(approved_by_dvc=is_approve, rejected_by_dvc=not is_approve) if _has_rejected_field() \
                      else qs.update(approved_by_dvc=is_approve)
            for dept in Department.objects.filter(id__in=set(affected)):
                cnt = affected.count(dept.id)
                if cnt:
                    _log_dvc_action(request, DVCActionLog.ALLOC_STANDARD, is_approve, department=dept, count=cnt)
            update_dvc_panel_state(request, {'active_panel': 'allocations-panel'})
            return JsonResponse({"status": "success", "message": f"{'Approved' if is_approve else 'Rejected'} {updated} allocations", "updated": updated})

        if action in ("approve_department_allocations", "reject_department_allocations"):
            dept = _resolve_department(request.POST.get("department_id") or request.POST.get("department"))
            if not dept: return JsonResponse({"status": "error", "message": "Department not found"}, status=404)
            is_approve = (action == "approve_department_allocations")
            qs = CourseAllocation.objects.filter(department=dept)
            updated = qs.update(approved_by_dvc=is_approve, rejected_by_dvc=not is_approve) if _has_rejected_field() \
                      else qs.update(approved_by_dvc=is_approve)
            if updated:
                _log_dvc_action(request, DVCActionLog.ALLOC_STANDARD, is_approve, department=dept, count=updated)
            update_dvc_panel_state(request, {'last_department': dept.id, 'active_panel': 'allocations-panel'})
            return JsonResponse({"status": "success",
                                  "message": f"{'Approved' if is_approve else 'Rejected'} {updated} allocations for {dept.name}",
                                  "department_id": dept.id, "updated": updated})

        if action in ("approve_faculty_allocations", "reject_faculty_allocations"):
            faculty = _resolve_faculty(request.POST.get("faculty_id") or request.POST.get("faculty"))
            if not faculty: return JsonResponse({"status": "error", "message": "Faculty not found"}, status=404)
            dept_ids   = Department.objects.filter(faculty=faculty).values_list('id', flat=True)
            is_approve = (action == "approve_faculty_allocations")
            qs = CourseAllocation.objects.filter(department_id__in=dept_ids)
            affected = list(qs.values_list("department_id", flat=True))
            updated = qs.update(approved_by_dvc=is_approve, rejected_by_dvc=not is_approve) if _has_rejected_field() \
                      else qs.update(approved_by_dvc=is_approve)
            for dept in Department.objects.filter(id__in=set(affected)):
                cnt = affected.count(dept.id)
                if cnt:
                    _log_dvc_action(request, DVCActionLog.ALLOC_STANDARD, is_approve, department=dept, count=cnt)
            update_dvc_panel_state(request, {'last_faculty': faculty.id, 'active_panel': 'allocations-panel'})
            return JsonResponse({"status": "success",
                                  "message": f"{'Approved' if is_approve else 'Rejected'} {updated} allocations for {faculty.name}",
                                  "faculty_id": faculty.id, "updated": updated})

        if action in ("approve_lecturer_allocations", "reject_lecturer_allocations"):
            lecturer = _resolve_lecturer(request.POST.get("lecturer_id") or request.POST.get("lecturer"))
            if not lecturer: return JsonResponse({"status": "error", "message": "Lecturer not found"}, status=404)
            is_approve = (action == "approve_lecturer_allocations")
            qs = CourseAllocation.objects.filter(lecturer=lecturer)
            updated = qs.update(approved_by_dvc=is_approve, rejected_by_dvc=not is_approve) if _has_rejected_field() \
                      else qs.update(approved_by_dvc=is_approve)
            if updated:
                _log_dvc_action(request, DVCActionLog.ALLOC_STANDARD, is_approve,
                                 department=lecturer.department, lecturer=lecturer, count=updated)
            update_dvc_panel_state(request, {'last_lecturer': lecturer.id, 'active_panel': 'allocations-panel'})
            return JsonResponse({"status": "success",
                                  "message": f"{'Approved' if is_approve else 'Rejected'} {updated} allocations for {lecturer.display_name}",
                                  "lecturer_id": lecturer.id, "updated": updated})

        # ── Campus allocation actions ─────────────────────────────────────────
        if action in ("approve_campus_allocation", "reject_campus_allocation"):
            alloc = get_object_or_404(CampusCourseAllocation, pk=request.POST.get("id"))
            is_approve = (action == "approve_campus_allocation")
            alloc.approved_by_dvc = is_approve
            alloc.rejected_by_dvc = (action == "reject_campus_allocation")
            alloc.save()
            _log_dvc_action(request, DVCActionLog.ALLOC_CAMPUS, is_approve,
                             department=alloc.department, lecturer=alloc.lecturer,
                             course_code=alloc.course_code, course_name=getattr(alloc, "course_name", ""))
            update_dvc_panel_state(request, {'active_panel': 'campus-panel'})
            return JsonResponse({"status": "success", "message": f"Campus course {action.replace('_',' ').title()}",
                                  "id": alloc.id, "status_display": get_allocation_status(alloc)})

        if action in ("approve_all_campus_allocations", "reject_all_campus_allocations"):
            is_approve = (action == "approve_all_campus_allocations")
            qs = CampusCourseAllocation.objects.all()
            affected = list(qs.values_list("department_id", flat=True))
            updated = qs.update(approved_by_dvc=is_approve, rejected_by_dvc=not is_approve)
            for dept in Department.objects.filter(id__in=set(affected)):
                cnt = affected.count(dept.id)
                if cnt:
                    _log_dvc_action(request, DVCActionLog.ALLOC_CAMPUS, is_approve, department=dept, count=cnt)
            update_dvc_panel_state(request, {'active_panel': 'campus-panel'})
            return JsonResponse({"status": "success",
                                  "message": f"{'Approved' if is_approve else 'Rejected'} {updated} campus allocations", "updated": updated})

        if action in ("approve_campus_dept_allocations", "reject_campus_dept_allocations"):
            dept = _resolve_department(request.POST.get("department_id") or request.POST.get("department"))
            if not dept: return JsonResponse({"status": "error", "message": "Department not found"}, status=404)
            is_approve = (action == "approve_campus_dept_allocations")
            updated = CampusCourseAllocation.objects.filter(department=dept).update(
                approved_by_dvc=is_approve, rejected_by_dvc=not is_approve)
            if updated:
                _log_dvc_action(request, DVCActionLog.ALLOC_CAMPUS, is_approve, department=dept, count=updated)
            return JsonResponse({"status": "success",
                                  "message": f"{'Approved' if is_approve else 'Rejected'} {updated} campus allocations for {dept.name}",
                                  "updated": updated})

        # ── ODEL allocation actions ───────────────────────────────────────────
        if action in ("approve_odel_allocation", "reject_odel_allocation"):
            alloc = get_object_or_404(ODELCourseAllocation, pk=request.POST.get("id"))
            is_approve = (action == "approve_odel_allocation")
            alloc.approved_by_dvc = is_approve
            alloc.rejected        = (action == "reject_odel_allocation")
            alloc.save()
            odel_dept = None
            if alloc.program_course and alloc.program_course.program:
                odel_dept = alloc.program_course.program.department
            _log_dvc_action(request, DVCActionLog.ALLOC_ODEL, is_approve,
                             department=odel_dept, lecturer=alloc.lecturer,
                             course_code=alloc.program_course.course_code if alloc.program_course else "")
            update_dvc_panel_state(request, {'active_panel': 'odel-panel'})
            return JsonResponse({"status": "success", "message": f"ODEL course {action.replace('_',' ').title()}",
                                  "id": alloc.id, "status_display": get_allocation_status(alloc)})

        if action in ("approve_all_odel_allocations", "reject_all_odel_allocations"):
            is_approve = (action == "approve_all_odel_allocations")
            qs = ODELCourseAllocation.objects.filter(submitted_to_dvc=True).select_related("program_course__program__department")
            dept_ids = [a.program_course.program.department_id
                        for a in qs if a.program_course and a.program_course.program]
            updated = qs.update(approved_by_dvc=is_approve, rejected=not is_approve)
            for dept in Department.objects.filter(id__in=set(dept_ids)):
                cnt = dept_ids.count(dept.id)
                if cnt:
                    _log_dvc_action(request, DVCActionLog.ALLOC_ODEL, is_approve, department=dept, count=cnt)
            update_dvc_panel_state(request, {'active_panel': 'odel-panel'})
            return JsonResponse({"status": "success",
                                  "message": f"{'Approved' if is_approve else 'Rejected'} {updated} ODEL allocations", "updated": updated})

        if action in ("approve_odel_dept_allocations", "reject_odel_dept_allocations"):
            dept = _resolve_department(request.POST.get("department_id") or request.POST.get("department"))
            if not dept: return JsonResponse({"status": "error", "message": "Department not found"}, status=404)
            is_approve = (action == "approve_odel_dept_allocations")
            prog_ids = Program.objects.filter(department=dept).values_list('id', flat=True)
            pc_ids   = ProgramCourse.objects.filter(program_id__in=prog_ids).values_list('id', flat=True)
            updated  = ODELCourseAllocation.objects.filter(program_course_id__in=pc_ids, submitted_to_dvc=True).update(
                approved_by_dvc=is_approve, rejected=not is_approve)
            if updated:
                _log_dvc_action(request, DVCActionLog.ALLOC_ODEL, is_approve, department=dept, count=updated)
            return JsonResponse({"status": "success",
                                  "message": f"{'Approved' if is_approve else 'Rejected'} {updated} ODEL allocations for {dept.name}",
                                  "updated": updated})

        return JsonResponse({"status": "error", "message": "Invalid action"}, status=400)

    # ── AJAX STATUS POLL ─────────────────────────────────────────────────────
    # Called by the front-end every 30 s to detect status changes without a
    # full page reload.  Returns a compact list of {id, status} for every
    # allocation that is currently visible (i.e. submitted to DVC).
    if request.GET.get('ajax_status') == '1' and request.headers.get('x-requested-with') == 'XMLHttpRequest':
        from course_allocation.models import SubmissionControl
        allowed_depts = SubmissionControl.objects.filter(
            allow_submission_to_dvc=True, department__isnull=False
        ).values_list("department_id", flat=True)
        allowed_campus_depts = CampusSubmissionControl.objects.filter(
            allow_submission_to_dvc=True, department__isnull=False
        ).values_list("department_id", flat=True)

        rows = []
        for a in CourseAllocation.objects.filter(department_id__in=allowed_depts).only('id','approved_by_dvc','rejected_by_dvc'):
            rows.append({'id': a.id, 'status': 'approved' if a.approved_by_dvc else ('rejected' if a.rejected_by_dvc else 'pending')})
        for a in CampusCourseAllocation.objects.filter(department_id__in=allowed_campus_depts).only('id','approved_by_dvc','rejected_by_dvc'):
            rows.append({'id': a.id, 'status': 'approved' if a.approved_by_dvc else ('rejected' if a.rejected_by_dvc else 'pending')})
        for a in ODELCourseAllocation.objects.filter(submitted_to_dvc=True).only('id','approved_by_dvc','rejected'):
            rows.append({'id': a.id, 'status': 'approved' if a.approved_by_dvc else ('rejected' if a.rejected else 'pending')})
        return JsonResponse({'rows': rows})

    # ── GET handler ───────────────────────────────────────────────────────────
    if 'clear_session' in request.GET:
        update_dvc_panel_state(request, {'last_faculty': None, 'last_department': None,
                                          'last_lecturer': None, 'search_filters': {}})
        remaining = {k: v for k, v in request.GET.items() if k != 'clear_session'}
        if remaining:
            from urllib.parse import urlencode
            return redirect(f"{request.path}?{urlencode(remaining)}")
        return redirect(request.path)

    params_present = bool(request.GET)
    group_by = request.GET.get('group_by', state.get('group_by', 'lecturer'))

    if params_present:
        raw_faculty    = request.GET.get('faculty',    '').strip()
        raw_department = request.GET.get('department', '').strip()
        raw_lecturer   = request.GET.get('lecturer',   '').strip()
        raw_search     = request.GET.get('search',     '').strip()
        update_dvc_panel_state(request, {
            'last_faculty': raw_faculty or None, 'last_department': raw_department or None,
            'last_lecturer': raw_lecturer or None, 'group_by': group_by,
            'search_filters': {'search': raw_search} if raw_search else {},
        })
    else:
        raw_faculty    = state.get('last_faculty')    or ''
        raw_department = state.get('last_department') or ''
        raw_lecturer   = state.get('last_lecturer')   or ''
        raw_search     = (state.get('search_filters') or {}).get('search', '')

    faculty_filter_id    = int(raw_faculty)    if raw_faculty    else None
    department_filter_id = int(raw_department) if raw_department else None
    lecturer_filter_id   = int(raw_lecturer)   if raw_lecturer   else None

    faculty_search = request.GET.get('faculty_search', '').strip()
    faculties_qs   = Faculty.objects.select_related("leader").all()
    if faculty_search:
        faculties_qs = faculties_qs.filter(name__icontains=faculty_search)

    # ── Standard allocations ──────────────────────────────────────────────────
    from course_allocation.models import SubmissionControl
    allowed_depts = SubmissionControl.objects.filter(
        allow_submission_to_dvc=True, department__isnull=False
    ).values_list("department_id", flat=True)

    allocations = (
        CourseAllocation.objects.filter(department_id__in=allowed_depts)
        .select_related("department","department__faculty","lecturer","program","program_course","program_course__program")
        .order_by("lecturer__name","department__name","course_code")
    )
    if faculty_filter_id:
        dept_ids = Department.objects.filter(faculty_id=faculty_filter_id).values_list('id', flat=True)
        allocations = allocations.filter(department_id__in=dept_ids)
    if department_filter_id: allocations = allocations.filter(department_id=department_filter_id)
    if lecturer_filter_id:   allocations = allocations.filter(lecturer_id=lecturer_filter_id)
    if raw_search:
        allocations = allocations.filter(
            Q(course_code__icontains=raw_search)|Q(course_name__icontains=raw_search)|
            Q(lecturer__name__icontains=raw_search)|Q(lecturer__user__first_name__icontains=raw_search)|
            Q(lecturer__user__last_name__icontains=raw_search)|Q(program__name__icontains=raw_search))

    # ── Campus allocations ────────────────────────────────────────────────────
    allowed_campus_depts = CampusSubmissionControl.objects.filter(
        allow_submission_to_dvc=True, department__isnull=False
    ).values_list("department_id", flat=True)

    campus_allocations = (
        CampusCourseAllocation.objects.filter(department_id__in=allowed_campus_depts)
        .select_related("department","department__faculty","lecturer","program","campus")
        .order_by("campus__name","lecturer__name","department__name","course_code")
    )
    if faculty_filter_id:
        dept_ids = Department.objects.filter(faculty_id=faculty_filter_id).values_list('id', flat=True)
        campus_allocations = campus_allocations.filter(department_id__in=dept_ids)
    if department_filter_id: campus_allocations = campus_allocations.filter(department_id=department_filter_id)
    if lecturer_filter_id:   campus_allocations = campus_allocations.filter(lecturer_id=lecturer_filter_id)
    if raw_search:
        campus_allocations = campus_allocations.filter(
            Q(course_code__icontains=raw_search)|Q(course_name__icontains=raw_search)|
            Q(lecturer__name__icontains=raw_search)|Q(campus__name__icontains=raw_search)|
            Q(program__name__icontains=raw_search))

    # ── ODEL allocations ──────────────────────────────────────────────────────
    odel_allocations = (
        ODELCourseAllocation.objects.filter(submitted_to_dvc=True)
        .select_related("lecturer","program_course","program_course__program",
                         "program_course__program__department","program_course__program__department__faculty")
        .order_by("program_course__program__department__name","lecturer__name","program_course__course_code")
    )
    if faculty_filter_id:
        dept_ids = Department.objects.filter(faculty_id=faculty_filter_id).values_list('id', flat=True)
        prog_ids = Program.objects.filter(department_id__in=dept_ids).values_list('id', flat=True)
        pc_ids   = ProgramCourse.objects.filter(program_id__in=prog_ids).values_list('id', flat=True)
        odel_allocations = odel_allocations.filter(program_course_id__in=pc_ids)
    if department_filter_id:
        prog_ids = Program.objects.filter(department_id=department_filter_id).values_list('id', flat=True)
        pc_ids   = ProgramCourse.objects.filter(program_id__in=prog_ids).values_list('id', flat=True)
        odel_allocations = odel_allocations.filter(program_course_id__in=pc_ids)
    if lecturer_filter_id:   odel_allocations = odel_allocations.filter(lecturer_id=lecturer_filter_id)
    if raw_search:
        odel_allocations = odel_allocations.filter(
            Q(program_course__course_code__icontains=raw_search)|Q(program_course__course_name__icontains=raw_search)|
            Q(lecturer__name__icontains=raw_search)|Q(program_course__program__name__icontains=raw_search))

    # ── Group ─────────────────────────────────────────────────────────────────
    if group_by == 'lecturer':
        grouped_allocations        = group_by_lecturer(allocations)
        grouped_campus_allocations = group_campus_by_lecturer(campus_allocations)
        grouped_odel_allocations   = group_odel_by_lecturer(odel_allocations)
    else:
        grouped_allocations        = group_by_department(allocations)
        grouped_campus_allocations = group_campus_by_department(campus_allocations)
        grouped_odel_allocations   = group_odel_by_department(odel_allocations)

    # ── Disapproved ───────────────────────────────────────────────────────────
    disapproved = (
        CourseAllocation.objects.filter(department_id__in=allowed_depts, rejected_by_dvc=True)
        .select_related("department","department__faculty","lecturer","program","program_course")
        .order_by("lecturer__name","department__name","course_code")
    )
    campus_disapproved = (
        CampusCourseAllocation.objects.filter(department_id__in=allowed_campus_depts, rejected_by_dvc=True)
        .select_related("department","department__faculty","lecturer","program","campus")
        .order_by("campus__name","lecturer__name","department__name","course_code")
    )
    odel_disapproved = (
        ODELCourseAllocation.objects.filter(submitted_to_dvc=True, rejected=True)
        .select_related("lecturer","program_course","program_course__program","program_course__program__department")
        .order_by("program_course__program__department__name","lecturer__name")
    )

    campuses    = Campus.objects.filter(is_active=True).order_by('name')
    departments = Department.objects.select_related('faculty').order_by('faculty__name','name')
    lecturers   = Lecturer.objects.select_related('department','department__faculty').all().order_by('name')
    if department_filter_id:
        lecturers = lecturers.filter(department_id=department_filter_id)
    elif faculty_filter_id:
        dept_ids  = Department.objects.filter(faculty_id=faculty_filter_id).values_list('id', flat=True)
        lecturers = lecturers.filter(department_id__in=dept_ids)

    return render(request, "faculty/dvc_panel.html", {
        # Standard
        "faculties":                   faculties_qs,
        "grouped_allocations":         grouped_allocations,
        "disapproved_allocations":     disapproved,
        # Campus
        "grouped_campus_allocations":  grouped_campus_allocations,
        "campus_disapproved":          campus_disapproved,
        "campuses":                    campuses,
        # ODEL
        "grouped_odel_allocations":    grouped_odel_allocations,
        "odel_disapproved":            odel_disapproved,
        # Shared
        "departments":   departments,
        "lecturers":     lecturers,
        "state":         state,
        "active_panel":  state.get('active_panel', 'allocations-panel'),
        "group_by":      group_by,
        "faculty_filter":     faculty_filter_id,
        "department_filter":  department_filter_id,
        "lecturer_filter":    lecturer_filter_id,
        "search_query":       raw_search,
    })


# ─────────────────────────────────────────────────────────────────────────────
# DISAPPROVAL ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@allowed_roles(Role.DVC, Role.DVC_ADMIN, Role.SUDO)
def ajax_disapproved_allocations(request):
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        action     = request.POST.get("action")
        alloc_id   = request.POST.get("id")
        alloc_type = request.POST.get("alloc_type", "standard")

        if action == "update_disapproval_reason":
            if alloc_type == "campus":
                a = get_object_or_404(CampusCourseAllocation, pk=alloc_id)
                a.reason_for_disapproval = request.POST.get("reason","").strip() or None
                a.save(update_fields=["reason_for_disapproval"])
            elif alloc_type == "odel":
                a = get_object_or_404(ODELCourseAllocation, pk=alloc_id)
                a.reason_for_rejection = request.POST.get("reason","").strip() or "No reason yet"
                a.save(update_fields=["reason_for_rejection"])
            else:
                a = get_object_or_404(CourseAllocation, pk=alloc_id)
                a.reason_for_disapproval = request.POST.get("reason","").strip() or None
                a.save(update_fields=["reason_for_disapproval"])
            return JsonResponse({"status": "success", "message": "Reason updated"})

        if action == "approve_allocation":
            if alloc_type == "campus":
                a = get_object_or_404(CampusCourseAllocation, pk=alloc_id)
                a.approved_by_dvc=True; a.rejected_by_dvc=False; a.reason_for_disapproval=None
                a.save()
                _log_dvc_action(request, DVCActionLog.ALLOC_CAMPUS, True,
                                 department=a.department, lecturer=a.lecturer, course_code=a.course_code)
            elif alloc_type == "odel":
                a = get_object_or_404(ODELCourseAllocation, pk=alloc_id)
                a.approved_by_dvc=True; a.rejected=False; a.reason_for_rejection="No reason yet"
                a.save()
                odel_dept = a.program_course.program.department if a.program_course and a.program_course.program else None
                _log_dvc_action(request, DVCActionLog.ALLOC_ODEL, True,
                                 department=odel_dept, lecturer=a.lecturer,
                                 course_code=a.program_course.course_code if a.program_course else "")
            else:
                a = get_object_or_404(CourseAllocation, pk=alloc_id)
                a.approved_by_dvc=True; a.rejected_by_dvc=False; a.reason_for_disapproval=None
                a.save()
                _log_dvc_action(request, DVCActionLog.ALLOC_STANDARD, True,
                                 department=a.department, lecturer=a.lecturer, course_code=a.course_code)
            return JsonResponse({"status": "success", "message": f"{a.course_code} approved"})

        if action == "approve_all_disapproved":
            if alloc_type == "campus":
                qs = CampusCourseAllocation.objects.filter(rejected_by_dvc=True)
                dept_ids = list(qs.values_list("department_id", flat=True))
                updated = qs.update(approved_by_dvc=True, rejected_by_dvc=False, reason_for_disapproval=None)
                for dept in Department.objects.filter(id__in=set(dept_ids)):
                    cnt = dept_ids.count(dept.id)
                    if cnt: _log_dvc_action(request, DVCActionLog.ALLOC_CAMPUS, True, department=dept, count=cnt)
            elif alloc_type == "odel":
                qs = ODELCourseAllocation.objects.filter(rejected=True).select_related("program_course__program__department")
                dept_ids = [a.program_course.program.department_id
                            for a in qs if a.program_course and a.program_course.program]
                updated = qs.update(approved_by_dvc=True, rejected=False, reason_for_rejection="No reason yet")
                for dept in Department.objects.filter(id__in=set(dept_ids)):
                    cnt = dept_ids.count(dept.id)
                    if cnt: _log_dvc_action(request, DVCActionLog.ALLOC_ODEL, True, department=dept, count=cnt)
            else:
                qs = CourseAllocation.objects.filter(rejected_by_dvc=True)
                dept_ids = list(qs.values_list("department_id", flat=True))
                updated = qs.update(approved_by_dvc=True, rejected_by_dvc=False, reason_for_disapproval=None)
                for dept in Department.objects.filter(id__in=set(dept_ids)):
                    cnt = dept_ids.count(dept.id)
                    if cnt: _log_dvc_action(request, DVCActionLog.ALLOC_STANDARD, True, department=dept, count=cnt)
            return JsonResponse({"status": "success", "message": f"Approved {updated} disapproved courses", "updated": updated})

        return JsonResponse({"status": "error", "message": "Invalid action"}, status=400)
    return JsonResponse({"status": "error", "message": "Invalid request"}, status=400)


# ─────────────────────────────────────────────────────────────────────────────
# AJAX SEARCH ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@allowed_roles(Role.DVC, Role.DVC_ADMIN, Role.SUDO)
def ajax_search_faculties(request):
    query = request.GET.get('q', '').strip()
    qs = Faculty.objects.select_related('leader').all()
    if query: qs = qs.filter(name__icontains=query)
    results = [{'id': f.id, 'text': f.name, 'description': f.description or '',
                'dean': f.leader.get_full_name() if f.leader else 'Not assigned'} for f in qs[:20]]
    return JsonResponse({'results': results})


@allowed_roles(Role.DVC, Role.DVC_ADMIN, Role.SUDO)
def ajax_search_departments(request):
    query      = request.GET.get('q', '').strip()
    faculty_id = request.GET.get('faculty_id', '').strip()
    qs = Department.objects.select_related('faculty').all()
    if faculty_id:
        try: qs = qs.filter(faculty_id=int(faculty_id))
        except (ValueError, TypeError): return JsonResponse({'results': []})
    if query: qs = qs.filter(name__icontains=query)
    if not faculty_id: qs = qs[:50]
    results = [{'id': d.id, 'text': d.name, 'faculty': d.faculty.name if d.faculty else 'No faculty'}
               for d in qs.order_by('name')]
    return JsonResponse({'results': results})


@allowed_roles(Role.DVC, Role.DVC_ADMIN, Role.SUDO)
def ajax_search_lecturers(request):
    try:
        query         = request.GET.get('q', '').strip()
        department_id = request.GET.get('department_id', '').strip()
        faculty_id    = request.GET.get('faculty_id', '').strip()
        qs = Lecturer.objects.select_related('user','department','department__faculty').all()
        if department_id:
            try: qs = qs.filter(department_id=int(department_id))
            except (ValueError, TypeError): return JsonResponse({'error': 'Invalid dept ID', 'results': []}, status=400)
        if faculty_id and not department_id:
            try:
                dept_ids = Department.objects.filter(faculty_id=int(faculty_id)).values_list('id', flat=True)
                qs = qs.filter(department_id__in=list(dept_ids))
            except (ValueError, TypeError): return JsonResponse({'error': 'Invalid faculty ID', 'results': []}, status=400)
        if query:
            qs = qs.filter(Q(name__icontains=query)|Q(user__first_name__icontains=query)|
                           Q(user__last_name__icontains=query)|Q(user__username__icontains=query))
        results = []
        for l in qs.order_by('name')[:50]:
            try:
                if l.user and l.user.get_full_name(): dn = l.user.get_full_name()
                elif l.user and l.user.username: dn = l.user.username
                else:
                    desig = ""
                    if l.designation:
                        try: desig = l.get_designation_display() + " "
                        except Exception: desig = l.designation + " "
                    dn = f"{desig}{l.name}".strip() if l.name else f"Lecturer {l.id}"
                results.append({'id': l.id, 'text': dn,
                                  'department': l.department.name if l.department else "No department",
                                  'faculty': l.department.faculty.name if l.department and l.department.faculty else "No faculty"})
            except Exception: continue
        return JsonResponse({'results': results})
    except Exception as e:
        import traceback; traceback.print_exc()
        return JsonResponse({'error': str(e), 'results': [], 'message': 'Server error'}, status=500)


@allowed_roles(Role.DVC, Role.DVC_ADMIN, Role.SUDO)
def ajax_search_campuses(request):
    """AJAX search for campuses (used by campus filter dropdown)."""
    query = request.GET.get('q', '').strip()
    qs = Campus.objects.filter(is_active=True)
    if query: qs = qs.filter(Q(name__icontains=query)|Q(code__icontains=query))
    results = [{'id': c.id, 'text': c.name, 'code': c.code} for c in qs.order_by('name')[:20]]
    return JsonResponse({'results': results})