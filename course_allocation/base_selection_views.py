from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.db import transaction
from django.db.models import Count
from django.contrib import messages
import json
import logging

from program_management.models import ProgramCourse, Program
from department_management.models import Department
from .models import BaseSelection, SelectionGroup, CourseAllocation, StudentGroup
from .detect_user_department import detect_user_department

logger = logging.getLogger(__name__)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def base_selections_page(request):
    dept = detect_user_department(request.user)
    if not dept:
        return redirect("cod_panel")

    # optional filters
    program_id = request.GET.get("program_id") or None
    year = request.GET.get("year") or None

    # programs in this department
    programs = Program.objects.filter(department=dept).order_by("name")

    # program courses filter by selected program and year
    qs = ProgramCourse.objects.filter(program__department=dept)
    if program_id:
        try:
            program_id = int(program_id)
            qs = qs.filter(program_id=program_id)
        except (ValueError, TypeError):
            pass
    if year:
        try:
            year_i = int(year)
            qs = qs.filter(year=year_i)
        except (ValueError, TypeError):
            pass
    program_courses = qs.order_by("program__name", "year", "course_code")

    selected_program = None
    if program_id:
        try:
            selected_program = Program.objects.get(pk=int(program_id))
        except Exception:
            selected_program = None

    # Get all selection groups with their courses
    selection_groups = SelectionGroup.objects.filter(department=dept).prefetch_related("courses").annotate(course_count=Count('courses'))

    return render(request, "course_allocation/base_selections.html", {
        "department": dept,
        "programs": programs,
        "program_courses": program_courses,
        "selected_program_id": int(program_id) if program_id else None,
        "selected_year": int(year) if year else None,
        "years": list(range(1, 7)),
        "selected_program": selected_program,
        "selection_groups": selection_groups,
    })


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def add_selection_group(request):
    """Create a new course combination group with multiple courses"""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)
    
    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    try:
        # Get program_course_ids from POST
        pc_ids = request.POST.getlist("program_course_ids")
        if not pc_ids:
            return JsonResponse({"status": "error", "message": "Please select at least one course for the combination"}, status=400)

        # Get group name
        group_name = (request.POST.get("group_name") or "").strip()
        if not group_name:
            return JsonResponse({"status": "error", "message": "Group name is required"}, status=400)
        
        # Get program ID (required)
        group_program_id = request.POST.get("group_program_id")
        if not group_program_id:
            return JsonResponse({"status": "error", "message": "Program is required"}, status=400)

        with transaction.atomic():
            # Get the program
            try:
                program = Program.objects.get(pk=group_program_id)
            except Program.DoesNotExist:
                return JsonResponse({"status": "error", "message": "Selected program not found"}, status=400)
            
            # Create the selection group (course combination)
            sg = SelectionGroup.objects.create(
                name=group_name,
                department=dept,
                program=program,
                created_by=request.user,
            )

            # Get all selected program courses - verify they belong to the selected program
            pcs = ProgramCourse.objects.filter(pk__in=pc_ids, program_id=group_program_id)
            
            if pcs.count() != len(pc_ids):
                return JsonResponse({"status": "error", "message": "Some selected courses do not belong to the selected program"}, status=400)
            
            # Create CourseAllocation entries for each course in this combination
            course_allocations = []
            for pc in pcs:
                # Create or get the SHARED CourseAllocation (mark as elective).
                # Selection Groups are shared-across-groups by construction,
                # so we must not match a Student-Group-tagged clone here.
                ca, created = CourseAllocation.get_or_create_shared(
                    program_course=pc,
                    department=dept,
                    defaults={
                        'is_elective': True,
                        'selection_group': sg,
                    },
                )
                # Update to ensure it belongs to this group
                if ca.selection_group != sg:
                    ca.selection_group = sg
                    ca.save()
                course_allocations.append(ca)
                
                # Also create base selection record for availability
                BaseSelection.objects.get_or_create(
                    program_course=pc,
                    department=dept,
                    defaults={"created_by": request.user},
                )
            
            # Link all courses to the group
            sg.courses.set(course_allocations)

        return JsonResponse({
            "status": "success",
            "message": f"Group '{group_name}' created with {len(pc_ids)} courses",
            "group_id": sg.id,
            "course_count": len(pc_ids),
        })
        
    except Exception as e:
        logger.error(f"Error creating selection group: {str(e)}")
        return JsonResponse({
            "status": "error", 
            "message": f"Error: {str(e)}"
        }, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def get_selection_groups_json(request):
    """Return this department's selection groups as JSON, optionally filtered
    by program_id. Used by the /cod/ right-click 'Add to Selection Group'
    picker to list existing groups a course can be added to."""
    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"error": "Department not found"}, status=400)

    program_id = request.GET.get("program_id")

    qs = SelectionGroup.objects.filter(department=dept).annotate(course_count=Count('courses'))
    if program_id and program_id not in ("", "null"):
        try:
            qs = qs.filter(program_id=int(program_id))
        except (ValueError, TypeError):
            pass
    qs = qs.order_by("name")

    groups = [
        {
            "id": g.id,
            "name": g.name,
            "program_id": g.program_id,
            "program_name": g.program.name if g.program else None,
            "course_count": g.course_count,
            "mapped_group_ids": list(g.restricted_to_groups.values_list("id", flat=True)),
        }
        for g in qs
    ]
    return JsonResponse(groups, safe=False)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def get_program_courses_json(request):
    """Return program courses as JSON for AJAX filtering"""
    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"error": "Department not found"}, status=400)
    
    program_id = request.GET.get("program_id")
    year = request.GET.get("year")
    
    # Base queryset for department's program courses
    qs = ProgramCourse.objects.filter(program__department=dept)
    
    # Apply filters
    if program_id and program_id != "" and program_id != "null":
        try:
            qs = qs.filter(program_id=int(program_id))
        except (ValueError, TypeError):
            pass
    
    if year and year != "" and year != "null":
        try:
            qs = qs.filter(year=int(year))
        except (ValueError, TypeError):
            pass
    
    # Order for consistent display
    qs = qs.order_by("year", "semester", "course_code")
    
    # Prepare JSON response
    courses = []
    for pc in qs:
        courses.append({
            "id": pc.id,
            "program_name": pc.program.name,
            "year": pc.year,
            "semester": pc.semester,
            "course_code": pc.course_code,
            "course_name": pc.course_name,
        })
    
    return JsonResponse(courses, safe=False)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def delete_selection_group(request, group_id):
    """Delete a course combination group"""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)
    
    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)
    
    try:
        group = SelectionGroup.objects.get(id=group_id, department=dept)
        group_name = group.name
        group.delete()
        return JsonResponse({"status": "success", "message": f"Group '{group_name}' deleted"})
    except SelectionGroup.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Group not found"}, status=404)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def update_selection_group(request, group_id):
    """Update a selection group's name only"""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)
    
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)
    
    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)
    
    try:
        group = SelectionGroup.objects.get(id=group_id, department=dept)
        if "name" in data and data["name"]:
            group.name = data["name"].strip()
        group.save()
        return JsonResponse({"status": "success", "message": "Group updated"})
    except SelectionGroup.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Group not found"}, status=404)
    except Exception as e:
        logger.error(f"Error updating group: {str(e)}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def add_courses_to_group(request, group_id):
    """Add more courses to an existing combination group"""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)
    
    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)
    
    pc_ids = request.POST.getlist("program_course_ids")
    if not pc_ids:
        return JsonResponse({"status": "error", "message": "Please select courses to add"}, status=400)
    
    try:
        group = SelectionGroup.objects.get(id=group_id, department=dept)
        
        with transaction.atomic():
            # Only allow adding courses from the same program as the group
            pcs = ProgramCourse.objects.filter(pk__in=pc_ids, program=group.program)
            
            if pcs.count() != len(pc_ids):
                return JsonResponse({"status": "error", "message": "All courses must belong to the same program as the group"}, status=400)
            
            added_count = 0
            for pc in pcs:
                # Create or get the SHARED CourseAllocation (mark as elective).
                ca, created = CourseAllocation.get_or_create_shared(
                    program_course=pc,
                    department=dept,
                    defaults={
                        'is_elective': True,
                        'selection_group': group,
                    },
                )
                if ca.selection_group != group:
                    ca.selection_group = group
                    ca.save()
                    added_count += 1
                
                # Add to group's course set if not already there
                if not group.courses.filter(id=ca.id).exists():
                    group.courses.add(ca)
                    added_count += 1
                
                # Create base selection
                BaseSelection.objects.get_or_create(
                    program_course=pc,
                    department=dept,
                    defaults={"created_by": request.user},
                )
        
        return JsonResponse({
            "status": "success",
            "message": f"Added {added_count} courses to group '{group.name}'"
        })
    except SelectionGroup.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Group not found"}, status=404)
    except Exception as e:
        logger.error(f"Error adding courses to group: {str(e)}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def remove_course_from_group(request, group_id, course_id):
    """Remove a course from a combination group"""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)
    
    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)
    
    try:
        group = SelectionGroup.objects.get(id=group_id, department=dept)
        course = CourseAllocation.objects.get(id=course_id, department=dept)
        group.courses.remove(course)
        
        # Optionally, remove the selection group reference but keep the course allocation
        if course.selection_group == group:
            course.selection_group = None
            course.save()
        
        return JsonResponse({"status": "success", "message": "Course removed from group"})
    except SelectionGroup.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Group not found"}, status=404)
    except CourseAllocation.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Course not found"}, status=404)
    except Exception as e:
        logger.error(f"Error removing course: {str(e)}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def group_detail(request, group_id):
    """View details of a course combination group"""
    dept = detect_user_department(request.user)
    if not dept:
        return redirect("cod_panel")
    
    try:
        group = SelectionGroup.objects.get(id=group_id, department=dept)
        courses = group.courses.all()
        
        # Get available courses to add (not already in this group and from same program)
        available_courses = []
        if group.program:
            available_courses = ProgramCourse.objects.filter(
                program=group.program,
                program__department=dept
            ).exclude(
                id__in=courses.values_list('program_course_id', flat=True)
            ).order_by("year", "semester", "course_code")
        
    except SelectionGroup.DoesNotExist:
        messages.error(request, "Group not found")
        return redirect("base_selections")
    
    return render(request, "course_allocation/group_detail.html", {
        "group": group,
        "department": dept,
        "courses": courses,
        "available_courses": available_courses,
    })


# ── Student Group <-> Selection Group (elective pool) mapping ───────────────
# Lets a COD restrict an elective pool to specific Student Groups (e.g. only
# Group C may choose from this Selection Group's courses). Mirrors the
# Specialization Stem mapping in specialization_stem_views.py. Reachable from
# the /cod/ Student Groups right-click menu ("Map Elective Groups").

def _selection_group_map_dict(group, student_group=None):
    d = {
        "id": group.id,
        "name": group.name,
        "program_id": group.program_id,
        "program_name": group.program.name if group.program else None,
        "course_count": group.courses.count(),
        "restricted": group.is_restricted,
        "mapped_group_ids": list(group.restricted_to_groups.values_list("id", flat=True)),
        "mapped_group_names": [g.display_name for g in group.restricted_to_groups.all()],
    }
    if student_group is not None:
        d["mapped"] = group.restricted_to_groups.filter(id=student_group.id).exists()
    return d


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def get_selection_groups_for_student_group(request):
    """
    Return every SelectionGroup (elective pool) available to this Student
    Group's program, each flagged with whether it's already mapped to this
    group. Powers the "Map Elective Groups" modal on the /cod/ Student
    Groups table.
    """
    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    group_id = request.GET.get("student_group_id")
    if not group_id:
        return JsonResponse({"status": "error", "message": "student_group_id is required"}, status=400)

    try:
        student_group = StudentGroup.objects.select_related("program").get(id=group_id, program__department=dept)
    except StudentGroup.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Student group not found"}, status=404)

    qs = (
        SelectionGroup.objects.filter(department=dept, program=student_group.program)
        .prefetch_related("restricted_to_groups")
        .annotate(course_count=Count('courses'))
        .order_by("name")
    )

    return JsonResponse({
        "status": "success",
        "student_group": {
            "id": student_group.id,
            "name": student_group.display_name,
            "program_id": student_group.program_id,
            "program_name": student_group.program.name,
            "year": student_group.year,
            "semester": student_group.semester,
        },
        "selection_groups": [_selection_group_map_dict(g, student_group) for g in qs],
    })


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def map_selection_group_groups(request, group_id):
    """
    Set (replace) the full list of Student Groups a SelectionGroup (elective
    pool) is restricted to. Passing an empty group_ids list clears the
    restriction, making the pool open/shared to every group again (the
    pre-existing default behaviour).
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)

    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    try:
        group = SelectionGroup.objects.select_related("program").get(id=group_id, department=dept)
    except SelectionGroup.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Group not found"}, status=404)

    group_ids = request.POST.getlist("group_ids")
    student_groups = StudentGroup.objects.filter(id__in=group_ids, program=group.program)
    if student_groups.count() != len(set(group_ids)):
        return JsonResponse({
            "status": "error",
            "message": "All groups must belong to this elective pool's program",
        }, status=400)

    group.restricted_to_groups.set(student_groups)

    if student_groups:
        message = f"'{group.name}' is now restricted to: " + ", ".join(g.display_name for g in student_groups)
    else:
        message = f"'{group.name}' is now open to every group again."

    return JsonResponse({
        "status": "success",
        "message": message,
        "selection_group": _selection_group_map_dict(group),
    })


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def toggle_selection_group_group(request, group_id):
    """
    Add or remove ONE Student Group from a SelectionGroup's
    restricted_to_groups, without touching any other group already mapped.
    Used by the per-row checkbox in the Student Groups "Map Elective Groups"
    modal (as opposed to map_selection_group_groups, which replaces the
    whole set and is used on the Elective/Selection Groups management page).
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)

    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    student_group_id = request.POST.get("student_group_id")
    action = (request.POST.get("action") or "add").strip().lower()
    if not student_group_id or action not in ("add", "remove"):
        return JsonResponse({"status": "error", "message": "student_group_id and a valid action are required"}, status=400)

    try:
        group = SelectionGroup.objects.select_related("program").get(id=group_id, department=dept)
    except SelectionGroup.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Group not found"}, status=404)

    try:
        student_group = StudentGroup.objects.get(id=student_group_id, program=group.program)
    except StudentGroup.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Group not found for this pool's program"}, status=404)

    if action == "add":
        group.restricted_to_groups.add(student_group)
        message = f"{student_group.display_name} can now choose from '{group.name}'"
    else:
        group.restricted_to_groups.remove(student_group)
        message = f"{student_group.display_name} can no longer choose from '{group.name}'"

    return JsonResponse({"status": "success", "message": message, "selection_group": _selection_group_map_dict(group, student_group)})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def quick_create_selection_group_for_student_group(request):
    """
    Create a Selection Group (elective pool) directly scoped to one or more
    Student Groups, in a single call. Used by the "+ Create new elective
    group for this group" flow inside the Student Groups right-click menu.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)

    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    student_group_id = request.POST.get("student_group_id")
    if not student_group_id:
        return JsonResponse({"status": "error", "message": "student_group_id is required"}, status=400)

    try:
        student_group = StudentGroup.objects.select_related("program").get(id=student_group_id, program__department=dept)
    except StudentGroup.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Student group not found"}, status=404)

    group_name = (request.POST.get("group_name") or "").strip()
    if not group_name:
        return JsonResponse({"status": "error", "message": "Group name is required"}, status=400)

    pc_ids = request.POST.getlist("program_course_ids")
    if not pc_ids:
        return JsonResponse({"status": "error", "message": "Please select at least one course for the elective group"}, status=400)

    extra_group_ids = request.POST.getlist("extra_group_ids")

    try:
        with transaction.atomic():
            if SelectionGroup.objects.filter(department=dept, program=student_group.program, name__iexact=group_name).exists():
                return JsonResponse({"status": "error", "message": "An elective group with this name already exists for this program"}, status=400)

            sg = SelectionGroup.objects.create(
                name=group_name,
                department=dept,
                program=student_group.program,
                created_by=request.user,
            )

            pcs = ProgramCourse.objects.filter(pk__in=pc_ids, program=student_group.program)
            if pcs.count() != len(pc_ids):
                return JsonResponse({"status": "error", "message": "All courses must belong to the same program as the group"}, status=400)

            course_allocations = []
            for pc in pcs:
                ca, created = CourseAllocation.get_or_create_shared(
                    program_course=pc, department=dept,
                    defaults={'is_elective': True, 'selection_group': sg},
                )
                if ca.selection_group != sg:
                    ca.selection_group = sg
                    ca.save()
                course_allocations.append(ca)
                BaseSelection.objects.get_or_create(program_course=pc, department=dept, defaults={"created_by": request.user})
            sg.courses.set(course_allocations)

            group_ids = {int(student_group_id)} | {int(g) for g in extra_group_ids if g}
            student_groups = StudentGroup.objects.filter(id__in=group_ids, program=student_group.program)
            sg.restricted_to_groups.set(student_groups)

        return JsonResponse({
            "status": "success",
            "message": f"Elective group '{group_name}' created and mapped to " + ", ".join(g.display_name for g in student_groups),
            "selection_group": _selection_group_map_dict(sg, student_group),
        })
    except Exception as e:
        logger.error(f"Error quick-creating elective group for student group: {str(e)}")
        return JsonResponse({"status": "error", "message": f"Error: {str(e)}"}, status=500)