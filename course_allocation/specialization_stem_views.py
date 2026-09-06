"""
course_allocation/specialization_stem_views.py

COD panel views for "Specialization Stems" — course combinations where a
student picks ONE stem (e.g. Artificial Intelligence / Cybersecurity /
Networking) out of a SpecializationCategory and then takes EVERY course
inside that stem.

This is the mirror-opposite of SelectionGroup:

  SelectionGroup            -> student picks ONE course out of many.
                                 Courses in the SAME group may clash (only
                                 one is ever taken).

  SpecializationCategory /  -> student picks ONE stem out of many, but
  SpecializationStem           takes ALL courses inside that stem.
                                 Courses in the SAME stem must NOT clash
                                 (treated like ordinary mandatory courses).
                                 Courses in DIFFERENT stems of the same
                                 category MAY clash (never taken together).

NOTE: This module only implements the data layer (models/views/panel). The
autoscheduler algorithms are intentionally left untouched — wiring the
clash-exemption logic into the scheduler is a separate follow-up task.
"""
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
from .models import BaseSelection, SpecializationCategory, SpecializationStem, CourseAllocation, StudentGroup
from .detect_user_department import detect_user_department

logger = logging.getLogger(__name__)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def specialization_stems_page(request):
    """Main COD panel page for managing Specialization Categories & Stems."""
    dept = detect_user_department(request.user)
    if not dept:
        return redirect("cod_panel")

    program_id = request.GET.get("program_id") or None
    year = request.GET.get("year") or None

    programs = Program.objects.filter(department=dept).order_by("name")

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

    categories = (
        SpecializationCategory.objects.filter(department=dept)
        .select_related("program", "created_by")
        .prefetch_related("stems__courses")
        .annotate(stem_total=Count("stems", distinct=True))
    )

    return render(request, "course_allocation/specialization_stems.html", {
        "department": dept,
        "programs": programs,
        "program_courses": program_courses,
        "selected_program_id": int(program_id) if program_id else None,
        "selected_year": int(year) if year else None,
        "years": list(range(1, 7)),
        "selected_program": selected_program,
        "categories": categories,
    })


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def add_specialization_category(request):
    """Create a new SpecializationCategory (the choice-point, e.g. 'Year 3 Specialization')."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)

    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    category_name = (request.POST.get("category_name") or "").strip()
    if not category_name:
        return JsonResponse({"status": "error", "message": "Category name is required"}, status=400)

    program_id = request.POST.get("category_program_id")
    if not program_id:
        return JsonResponse({"status": "error", "message": "Program is required"}, status=400)

    year = request.POST.get("category_year") or None
    semester = request.POST.get("category_semester") or None

    try:
        with transaction.atomic():
            try:
                program = Program.objects.get(pk=program_id, department=dept)
            except Program.DoesNotExist:
                return JsonResponse({"status": "error", "message": "Selected program not found"}, status=400)

            if SpecializationCategory.objects.filter(program=program, name__iexact=category_name).exists():
                return JsonResponse({"status": "error", "message": "A specialization category with this name already exists for this program"}, status=400)

            category = SpecializationCategory.objects.create(
                name=category_name,
                department=dept,
                program=program,
                year=int(year) if year else None,
                semester=int(semester) if semester else None,
                created_by=request.user,
            )

        return JsonResponse({
            "status": "success",
            "message": f"Specialization category '{category_name}' created",
            "category_id": category.id,
            "category": {
                "id": category.id,
                "name": category.name,
                "program_id": program.id,
                "program_name": program.name,
                "year": category.year,
                "semester": category.semester,
            },
        })
    except Exception as e:
        logger.error(f"Error creating specialization category: {str(e)}")
        return JsonResponse({"status": "error", "message": f"Error: {str(e)}"}, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def delete_specialization_category(request, category_id):
    """Delete a SpecializationCategory (and, via cascade, all its stems)."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)

    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    try:
        category = SpecializationCategory.objects.get(id=category_id, department=dept)
        name = category.name
        category.delete()
        return JsonResponse({"status": "success", "message": f"Specialization category '{name}' deleted"})
    except SpecializationCategory.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Category not found"}, status=404)
    except Exception as e:
        logger.error(f"Error deleting specialization category {category_id}: {str(e)}", exc_info=True)
        return JsonResponse({"status": "error", "message": f"Error deleting category: {str(e)}"}, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def add_specialization_stem(request, category_id):
    """Create a new stem (e.g. 'Artificial Intelligence') inside a category, with its courses."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)

    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    try:
        category = SpecializationCategory.objects.get(id=category_id, department=dept)
    except SpecializationCategory.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Category not found"}, status=404)

    stem_name = (request.POST.get("stem_name") or "").strip()
    if not stem_name:
        return JsonResponse({"status": "error", "message": "Stem name is required"}, status=400)

    pc_ids = request.POST.getlist("program_course_ids")
    if not pc_ids:
        return JsonResponse({"status": "error", "message": "Please select at least one course for this stem"}, status=400)

    if SpecializationStem.objects.filter(category=category, name__iexact=stem_name).exists():
        return JsonResponse({"status": "error", "message": "A stem with this name already exists in this category"}, status=400)

    try:
        with transaction.atomic():
            # Courses must belong to the same program as the category.
            pcs = ProgramCourse.objects.filter(pk__in=pc_ids, program=category.program)
            if pcs.count() != len(pc_ids):
                return JsonResponse({"status": "error", "message": "All courses must belong to the same program as the category"}, status=400)

            stem = SpecializationStem.objects.create(
                category=category,
                name=stem_name,
                created_by=request.user,
            )

            course_allocations = []
            for pc in pcs:
                ca, created = CourseAllocation.get_or_create_shared(
                    program_course=pc,
                    department=dept,
                    defaults={'specialization_stem': stem},
                )
                # A SelectionGroup may be NESTED inside a stem (e.g. a
                # "Networking" stem where students still pick one of several
                # networking electives), so a course keeping its existing
                # selection_group here is intentional, not an error — the
                # scheduler's collision-exemption rules already treat
                # "same stem + same nested selection group" as non-colliding.
                course_allocations.append(ca)

                BaseSelection.objects.get_or_create(
                    program_course=pc,
                    department=dept,
                    defaults={"created_by": request.user},
                )

            stem.courses.set(course_allocations)

            # Same primary-FK logic as add_courses_to_stem: only keep the
            # singular `specialization_stem` pointer set when a course
            # belongs to exactly one stem overall; clear it when the course
            # is also a member of another stem (shared course), so it
            # keeps clashing normally with the other stem's courses.
            for ca in course_allocations:
                membership_ids = list(ca.specialization_stems.values_list("id", flat=True))
                if len(membership_ids) == 1:
                    if ca.specialization_stem_id != membership_ids[0]:
                        ca.specialization_stem_id = membership_ids[0]
                        ca.save(update_fields=["specialization_stem"])
                elif len(membership_ids) >= 2:
                    if ca.specialization_stem_id is not None:
                        ca.specialization_stem = None
                        ca.save(update_fields=["specialization_stem"])

        return JsonResponse({
            "status": "success",
            "message": f"Stem '{stem_name}' created with {len(pc_ids)} courses",
            "stem_id": stem.id,
            "course_count": len(pc_ids),
            "stem": {
                "id": stem.id,
                "name": stem.name,
                "category_id": category.id,
                "courses": [
                    {"id": ca.id, "course_code": ca.course_code, "course_name": ca.course_name}
                    for ca in course_allocations
                ],
            },
        })
    except Exception as e:
        logger.error(f"Error creating specialization stem: {str(e)}")
        return JsonResponse({"status": "error", "message": f"Error: {str(e)}"}, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def delete_specialization_stem(request, stem_id):
    """Delete a stem. Its CourseAllocations remain, just detached from the stem."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)

    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    try:
        stem = SpecializationStem.objects.get(id=stem_id, category__department=dept)
        name = stem.name
        stem.delete()
        return JsonResponse({"status": "success", "message": f"Stem '{name}' deleted"})
    except SpecializationStem.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Stem not found"}, status=404)
    except Exception as e:
        logger.error(f"Error deleting specialization stem {stem_id}: {str(e)}", exc_info=True)
        return JsonResponse({"status": "error", "message": f"Error deleting stem: {str(e)}"}, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def update_specialization_stem(request, stem_id):
    """Rename a stem."""
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
        stem = SpecializationStem.objects.get(id=stem_id, category__department=dept)
        if "name" in data and data["name"]:
            stem.name = data["name"].strip()
        stem.save()
        return JsonResponse({"status": "success", "message": "Stem updated", "stem_id": stem.id, "name": stem.name})
    except SpecializationStem.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Stem not found"}, status=404)
    except Exception as e:
        logger.error(f"Error updating stem: {str(e)}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def add_courses_to_stem(request, stem_id):
    """Add more courses to an existing stem."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)

    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    pc_ids = request.POST.getlist("program_course_ids")
    if not pc_ids:
        return JsonResponse({"status": "error", "message": "Please select courses to add"}, status=400)

    try:
        stem = SpecializationStem.objects.get(id=stem_id, category__department=dept)

        with transaction.atomic():
            pcs = ProgramCourse.objects.filter(pk__in=pc_ids, program=stem.category.program)
            if pcs.count() != len(pc_ids):
                return JsonResponse({"status": "error", "message": "All courses must belong to the same program as the stem's category"}, status=400)

            added_count = 0
            added_courses = []
            for pc in pcs:
                ca, created = CourseAllocation.get_or_create_shared(
                    program_course=pc,
                    department=dept,
                    defaults={'specialization_stem': stem},
                )
                # A SelectionGroup may be NESTED inside a stem — see the note
                # in add_specialization_stem above. Keeping ca.selection_group_id
                # intact here is intentional, not an error.

                if not stem.courses.filter(id=ca.id).exists():
                    stem.courses.add(ca)
                    added_count += 1
                    added_courses.append({"id": ca.id, "course_code": ca.course_code, "course_name": ca.course_name})

                # ── Primary stem pointer vs. shared-across-stems ──────────
                # `specialization_stem` (singular FK) is what the scheduler
                # reads to decide "different stem, same category → exempt
                # from clashing". A course that belongs to exactly ONE stem
                # keeps that FK set, so the normal one-stem exemption still
                # applies. A course that now belongs to TWO OR MORE stems
                # (e.g. MATH 101 shared between Statistics and Accounting)
                # is NOT exempt from clashing with the other courses in
                # those stems — a student in either stem still takes it —
                # so we deliberately clear the FK. With no single stem set,
                # every scheduling algorithm's clash-checker already treats
                # it like an ordinary compulsory course (see
                # `is_program_year_collision_exempt` / `_effective_window_
                # demand` in the autoscheduler modules), which is exactly
                # the "clashes with the other stem's courses unless
                # explicitly merged via a Combined Course Group" behaviour
                # requested. This only affects clash-exemption; the course
                # still shows up under every stem it was added to.
                membership_ids = list(
                    ca.specialization_stems.values_list("id", flat=True)
                )
                if len(membership_ids) == 1:
                    if ca.specialization_stem_id != membership_ids[0]:
                        ca.specialization_stem_id = membership_ids[0]
                        ca.save(update_fields=["specialization_stem"])
                elif len(membership_ids) >= 2:
                    if ca.specialization_stem_id is not None:
                        ca.specialization_stem = None
                        ca.save(update_fields=["specialization_stem"])

                BaseSelection.objects.get_or_create(
                    program_course=pc,
                    department=dept,
                    defaults={"created_by": request.user},
                )

        current_courses = [
            {"id": ca.id, "course_code": ca.course_code, "course_name": ca.course_name}
            for ca in stem.courses.all().order_by("course_code")
        ]

        return JsonResponse({
            "status": "success",
            "message": f"Added {added_count} courses to stem '{stem.name}'",
            "added_courses": added_courses,
            "courses": current_courses,
            "course_count": len(current_courses),
        })
    except SpecializationStem.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Stem not found"}, status=404)
    except Exception as e:
        logger.error(f"Error adding courses to stem: {str(e)}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def remove_course_from_stem(request, stem_id, course_id):
    """Remove a course from a stem (course allocation itself is kept)."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)

    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    try:
        stem = SpecializationStem.objects.get(id=stem_id, category__department=dept)
        course = CourseAllocation.objects.get(id=course_id, department=dept)
        stem.courses.remove(course)

        # Recompute the primary FK from what's left. 0 remaining stems →
        # clear it (plain compulsory/no-stem course). Exactly 1 remaining
        # → point the FK at it, restoring the normal single-stem clash
        # exemption. 2+ remaining → keep the FK cleared, since a course
        # shared across multiple stems must keep clashing normally against
        # the other courses in those stems (see add_courses_to_stem).
        remaining_ids = list(course.specialization_stems.values_list("id", flat=True))
        if len(remaining_ids) == 1:
            if course.specialization_stem_id != remaining_ids[0]:
                course.specialization_stem_id = remaining_ids[0]
                course.save(update_fields=["specialization_stem"])
        else:
            if course.specialization_stem_id is not None:
                course.specialization_stem = None
                course.save(update_fields=["specialization_stem"])

        return JsonResponse({"status": "success", "message": "Course removed from stem"})
    except SpecializationStem.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Stem not found"}, status=404)
    except CourseAllocation.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Course not found"}, status=404)
    except Exception as e:
        logger.error(f"Error removing course from stem: {str(e)}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def get_specialization_tree_json(request):
    """Return this department's specialization categories (choice-points),
    each with its nested stems, as JSON — optionally filtered by program_id.
    Used by the /cod/ right-click 'Add to Specialization Stem' picker so it
    can list existing categories/stems a course can be added to."""
    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"error": "Department not found"}, status=400)

    program_id = request.GET.get("program_id")

    qs = SpecializationCategory.objects.filter(department=dept).prefetch_related("stems__courses", "stems__restricted_to_groups")
    if program_id and program_id not in ("", "null"):
        try:
            qs = qs.filter(program_id=int(program_id))
        except (ValueError, TypeError):
            pass
    qs = qs.order_by("program__name", "year", "semester", "name")

    categories = []
    for cat in qs:
        categories.append({
            "id": cat.id,
            "name": cat.name,
            "program_id": cat.program_id,
            "program_name": cat.program.name if cat.program else None,
            "year": cat.year,
            "semester": cat.semester,
            "stems": [
                {
                    "id": stem.id,
                    "name": stem.name,
                    "course_count": stem.courses.count(),
                    "mapped_group_ids": list(stem.restricted_to_groups.values_list("id", flat=True)),
                }
                for stem in cat.stems.all().order_by("name")
            ],
        })
    return JsonResponse(categories, safe=False)


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def get_program_courses_json(request):
    """Return program courses as JSON for AJAX filtering (shared shape with base_selection_views)."""
    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"error": "Department not found"}, status=400)

    program_id = request.GET.get("program_id")
    year = request.GET.get("year")

    qs = ProgramCourse.objects.filter(program__department=dept)

    if program_id and program_id not in ("", "null"):
        try:
            qs = qs.filter(program_id=int(program_id))
        except (ValueError, TypeError):
            pass

    if year and year not in ("", "null"):
        try:
            qs = qs.filter(year=int(year))
        except (ValueError, TypeError):
            pass

    qs = qs.order_by("year", "semester", "course_code")

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


# ── Student Group <-> Specialization Stem mapping ───────────────────────────
# Lets a COD restrict a stem to specific Student Groups within a program/year
# (e.g. only Group A and Group B may pick "Artificial Intelligence", while
# Group C may not). Reachable from the /cod/ Student Groups right-click menu
# ("Map Specialization Stems") in both directions: pick an existing stem to
# map onto a group, or create a brand-new stem that's scoped to one or more
# groups from the start.

def _stem_to_dict(stem, group=None):
    d = {
        "id": stem.id,
        "name": stem.name,
        "category_id": stem.category_id,
        "category_name": stem.category.name,
        "course_count": stem.courses.count(),
        "restricted": stem.is_restricted,
        "mapped_group_ids": list(stem.restricted_to_groups.values_list("id", flat=True)),
        "mapped_group_names": [g.display_name for g in stem.restricted_to_groups.all()],
    }
    if group is not None:
        d["mapped"] = stem.restricted_to_groups.filter(id=group.id).exists()
    return d


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def get_stems_for_student_group(request):
    """
    Return every SpecializationCategory/Stem available to this Student
    Group's program (optionally narrowed by the category's own year/semester),
    each stem flagged with whether it's already mapped to this group.
    Powers the "Map Specialization Stems" modal on the /cod/ Student Groups
    table.
    """
    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    group_id = request.GET.get("student_group_id")
    if not group_id:
        return JsonResponse({"status": "error", "message": "student_group_id is required"}, status=400)

    try:
        group = StudentGroup.objects.select_related("program").get(id=group_id, program__department=dept)
    except StudentGroup.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Student group not found"}, status=404)

    from django.db.models import Q
    categories = (
        SpecializationCategory.objects.filter(department=dept, program=group.program)
        .filter(Q(year__isnull=True) | Q(year=group.year))
        .filter(Q(semester__isnull=True) | Q(semester=group.semester))
        .prefetch_related("stems__restricted_to_groups")
        .order_by("name")
    )

    return JsonResponse({
        "status": "success",
        "student_group": {
            "id": group.id,
            "name": group.display_name,
            "program_id": group.program_id,
            "program_name": group.program.name,
            "year": group.year,
            "semester": group.semester,
        },
        "categories": [
            {
                "id": cat.id,
                "name": cat.name,
                "year": cat.year,
                "semester": cat.semester,
                "stems": [_stem_to_dict(stem, group) for stem in cat.stems.all().order_by("name")],
            }
            for cat in categories
        ],
    })


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def map_specialization_stem_groups(request, stem_id):
    """
    Set (replace) the full list of Student Groups a stem is restricted to.
    Passing an empty group_ids list clears the restriction, making the stem
    open/shared to every group again (the pre-existing default behaviour).
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)

    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    try:
        stem = SpecializationStem.objects.select_related("category__program").get(
            id=stem_id, category__department=dept
        )
    except SpecializationStem.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Stem not found"}, status=404)

    group_ids = request.POST.getlist("group_ids")
    groups = StudentGroup.objects.filter(id__in=group_ids, program=stem.category.program)
    if groups.count() != len(set(group_ids)):
        return JsonResponse({
            "status": "error",
            "message": "All groups must belong to this stem's program",
        }, status=400)

    stem.restricted_to_groups.set(groups)

    if groups:
        message = f"'{stem.name}' is now restricted to: " + ", ".join(g.display_name for g in groups)
    else:
        message = f"'{stem.name}' is now open to every group again."

    return JsonResponse({
        "status": "success",
        "message": message,
        "stem": _stem_to_dict(stem),
    })


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def toggle_specialization_stem_group(request, stem_id):
    """
    Add or remove ONE Student Group from a stem's restricted_to_groups,
    without touching any other group already mapped to that stem. Used by
    the per-row checkbox in the Student Groups "Map Specialization Stems"
    modal (as opposed to map_specialization_stem_groups, which replaces the
    whole set and is used on the Specialization Stems management page).
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
        stem = SpecializationStem.objects.select_related("category__program").get(id=stem_id, category__department=dept)
    except SpecializationStem.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Stem not found"}, status=404)

    try:
        student_group = StudentGroup.objects.get(id=student_group_id, program=stem.category.program)
    except StudentGroup.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Group not found for this stem's program"}, status=404)

    if action == "add":
        stem.restricted_to_groups.add(student_group)
        message = f"{student_group.display_name} can now choose '{stem.name}'"
    else:
        stem.restricted_to_groups.remove(student_group)
        message = f"{student_group.display_name} can no longer choose '{stem.name}'"

    return JsonResponse({"status": "success", "message": message, "stem": _stem_to_dict(stem, student_group)})


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def quick_create_stem_for_student_group(request):
    """
    Create a stem (optionally a brand-new category too) directly scoped to
    one or more Student Groups, in a single call. Used by the "+ Create new
    stem for this group" flow inside the Student Groups right-click menu.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)

    dept = detect_user_department(request.user)
    if not dept:
        return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

    group_id = request.POST.get("student_group_id")
    if not group_id:
        return JsonResponse({"status": "error", "message": "student_group_id is required"}, status=400)

    try:
        group = StudentGroup.objects.select_related("program").get(id=group_id, program__department=dept)
    except StudentGroup.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Student group not found"}, status=404)

    stem_name = (request.POST.get("stem_name") or "").strip()
    if not stem_name:
        return JsonResponse({"status": "error", "message": "Stem name is required"}, status=400)

    pc_ids = request.POST.getlist("program_course_ids")
    if not pc_ids:
        return JsonResponse({"status": "error", "message": "Please select at least one course for this stem"}, status=400)

    category_id = request.POST.get("category_id") or None
    category_name = (request.POST.get("category_name") or "").strip()

    extra_group_ids = request.POST.getlist("extra_group_ids")

    try:
        with transaction.atomic():
            if category_id:
                try:
                    category = SpecializationCategory.objects.get(id=category_id, department=dept, program=group.program)
                except SpecializationCategory.DoesNotExist:
                    return JsonResponse({"status": "error", "message": "Category not found"}, status=404)
            else:
                if not category_name:
                    category_name = f"{group.program.name} Y{group.year} S{group.semester} Specialization"
                category, _ = SpecializationCategory.objects.get_or_create(
                    program=group.program,
                    name=category_name,
                    defaults={
                        "department": dept,
                        "year": group.year,
                        "semester": group.semester,
                        "created_by": request.user,
                    },
                )

            if SpecializationStem.objects.filter(category=category, name__iexact=stem_name).exists():
                return JsonResponse({"status": "error", "message": "A stem with this name already exists in this category"}, status=400)

            pcs = ProgramCourse.objects.filter(pk__in=pc_ids, program=category.program)
            if pcs.count() != len(pc_ids):
                return JsonResponse({"status": "error", "message": "All courses must belong to the same program as the category"}, status=400)

            stem = SpecializationStem.objects.create(category=category, name=stem_name, created_by=request.user)

            course_allocations = []
            for pc in pcs:
                ca, created = CourseAllocation.get_or_create_shared(
                    program_course=pc, department=dept, defaults={'specialization_stem': stem},
                )
                # A SelectionGroup may be NESTED inside a stem — see the note
                # in add_specialization_stem above. Keeping ca.selection_group_id
                # intact here is intentional, not an error.
                course_allocations.append(ca)
                BaseSelection.objects.get_or_create(program_course=pc, department=dept, defaults={"created_by": request.user})
            stem.courses.set(course_allocations)

            # Same primary-FK logic as add_courses_to_stem: keep the
            # singular pointer set only for courses that belong to exactly
            # one stem; clear it for courses shared across multiple stems
            # so they keep clashing normally with the other stem's courses.
            for ca in course_allocations:
                membership_ids = list(ca.specialization_stems.values_list("id", flat=True))
                if len(membership_ids) == 1:
                    if ca.specialization_stem_id != membership_ids[0]:
                        ca.specialization_stem_id = membership_ids[0]
                        ca.save(update_fields=["specialization_stem"])
                elif len(membership_ids) >= 2:
                    if ca.specialization_stem_id is not None:
                        ca.specialization_stem = None
                        ca.save(update_fields=["specialization_stem"])

            group_ids = {int(group_id)} | {int(g) for g in extra_group_ids if g}
            groups = StudentGroup.objects.filter(id__in=group_ids, program=group.program)
            stem.restricted_to_groups.set(groups)

        return JsonResponse({
            "status": "success",
            "message": f"Stem '{stem_name}' created and mapped to " + ", ".join(g.display_name for g in groups),
            "stem": _stem_to_dict(stem, group),
        })
    except Exception as e:
        logger.error(f"Error quick-creating stem for student group: {str(e)}")
        return JsonResponse({"status": "error", "message": f"Error: {str(e)}"}, status=500)
