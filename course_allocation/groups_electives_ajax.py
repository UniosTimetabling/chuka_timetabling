import logging
from collections import Counter

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from department_management.models import Department
from program_management.models import Program, ProgramCourse
from program_management.programs_page import detect_user_department
from lecturer_portal.models import Lecturer

from .models import (
    StudentGroup, SpecializationCategory, SpecializationStem, SelectionGroup,
    GroupingTemplate, GroupingTemplateStemAssignment,
)
from .allocation_scope import get_active_allocation_set, get_or_default_legacy_set
from .course_mapping import sync_target, map_courses_to_allocation, detach_allocations_from_stem
from . import course_group_planner
from . import course_group_draft
from .models import CourseGroupDraft

logger = logging.getLogger(__name__)


def _in_scope(detected_dept, department_id):
    """A COD/COD-Admin can only touch their own department; everyone else is unrestricted."""
    if not detected_dept:
        return True
    return str(department_id) == str(detected_dept.id)


def _resolve_department(request, detected_dept, field_label="Department"):
    """Same rule as the page view: a COD/COD-Admin is pinned to their own
    department; anyone else must pass department_id. Returns (dept, error)."""
    if detected_dept:
        return detected_dept, None
    dept_id = request.POST.get("department_id")
    if not dept_id or not str(dept_id).isdigit():
        return None, JsonResponse({"status": "error", "message": f"{field_label} is required."}, status=400)
    dept = Department.objects.filter(id=dept_id).first()
    if not dept:
        return None, JsonResponse({"status": "error", "message": "Department not found."}, status=404)
    return dept, None


def _resolve_program(detected_dept, program_id, field_label="Program"):
    """get_object_or_404 the Program, enforcing department scope. Returns
    (program, error_response_or_None)."""
    program = get_object_or_404(Program, pk=program_id)
    if not _in_scope(detected_dept, program.department_id):
        return None, JsonResponse(
            {"status": "error", "message": f"You can only manage {field_label.lower()}s in your own department."},
            status=403,
        )
    return program, None


@login_required
@require_POST
def ajax_groups_electives(request):
    """
    AJAX CRUD for StudentGroup, SpecializationStem (+ its parent
    SpecializationCategory, created/reused transparently), SelectionGroup
    ("Elective Group"), and GroupingTemplate ("Course Groups" — the bulk
    split / stem-pin plan auto-allocate replays).
    """
    action = request.POST.get("action", "").strip()
    detected_dept = detect_user_department(request.user)

    handler = {
        "add_student_group": _add_student_group,
        "edit_student_group": _edit_student_group,
        "delete_student_group": _delete_student_group,
        # Course Groups (bulk group count + scope + combination-stem pinning)
        "group_plan_context": _group_plan_context,
        "save_group_plan": _save_group_plan,
        "delete_group_plan": _delete_group_plan,
        "unpin_stem_from_letter": _unpin_stem_from_letter,
        "add_stem": _add_stem,
        "edit_stem": _edit_stem,
        "delete_stem": _delete_stem,
        "stem_student_numbers": _stem_student_numbers,
        "save_stem_student_numbers": _save_stem_student_numbers,
        "bulk_stem_student_numbers": _bulk_stem_student_numbers,
        "save_bulk_stem_student_numbers": _save_bulk_stem_student_numbers,
        "add_elective_group": _add_elective_group,
        "edit_elective_group": _edit_elective_group,
        "delete_elective_group": _delete_elective_group,
        "delete_selected_targets": _delete_selected_targets,
        # Advance mapping of curriculum (ProgramCourse) courses -> stem / elective group
        "list_mappable_courses": _list_mappable_courses,
        "map_courses": _map_courses,
        "map_to_allocation": _map_to_allocation,
        "remove_from_allocation": _remove_from_allocation,
        "unmap_course": _unmap_course,
        "apply_mappings": _apply_mappings,
        # Course Groups — quick-create-by-course-code + matrix + commit
        "cg_course_choices": _cg_course_choices,
        "cg_create_drafts": _cg_create_drafts,
        "cg_list_drafts": _cg_list_drafts,
        "cg_matrix": _cg_matrix,
        "cg_program_stems": _cg_program_stems,
        "cg_toggle_mapping": _cg_toggle_mapping,
        "cg_copy_targets": _cg_copy_targets,
        "cg_copy_draft": _cg_copy_draft,
        "cg_commit": _cg_commit,
        "cg_delete_draft": _cg_delete_draft,
        "cg_delete_letter": _cg_delete_letter,
        "cg_add_groups": _cg_add_groups,
        "cg_lecturer_choices": _cg_lecturer_choices,
        "cg_set_letter_lecturer": _cg_set_letter_lecturer,
    }.get(action)

    if not handler:
        return JsonResponse({"status": "error", "message": f"Unknown action: {action}"}, status=400)

    try:
        return handler(request, detected_dept)
    except Exception as e:
        logger.exception("Error in ajax_groups_electives (action=%s): %s", action, e)
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


# ───────────────────────── Student Group CRUD ─────────────────────────

def _clean_group_fields(request, program):
    try:
        year = int(request.POST.get("year") or 0)
        semester = int(request.POST.get("semester") or 0)
    except (TypeError, ValueError):
        return None, JsonResponse({"status": "error", "message": "Year and semester must be numbers."}, status=400)

    if not (1 <= year <= 6):
        return None, JsonResponse({"status": "error", "message": "Year must be between 1 and 6."}, status=400)
    if semester not in (1, 2, 3):
        return None, JsonResponse({"status": "error", "message": "Semester must be 1, 2, or 3."}, status=400)

    intake = (request.POST.get("intake") or "normal").strip()
    if intake not in ("normal", "special"):
        return None, JsonResponse({"status": "error", "message": "Invalid intake type."}, status=400)

    letter = (request.POST.get("letter") or "").strip().upper()
    if not letter or len(letter) > 4 or not letter.isalnum():
        return None, JsonResponse(
            {"status": "error", "message": "Group code must be 1-4 letters/numbers (e.g. 'A' or 'DA')."}, status=400
        )

    return {"year": year, "semester": semester, "intake": intake, "letter": letter}, None


def _add_student_group(request, detected_dept):
    program, err = _resolve_program(detected_dept, request.POST.get("program"))
    if err:
        return err

    fields, err = _clean_group_fields(request, program)
    if err:
        return err

    if StudentGroup.objects.filter(program=program, year=fields["year"], semester=fields["semester"],
                                    intake=fields["intake"], letter=fields["letter"]).exists():
        return JsonResponse({
            "status": "error",
            "message": f"Group '{fields['letter']}' already exists for this program/year/semester/intake.",
        }, status=400)

    group = StudentGroup.objects.create(
        program=program, created_by=request.user, **fields,
    )
    return JsonResponse({"status": "success", "id": group.id, "message": f"Student group '{group.name}' added."})


def _edit_student_group(request, detected_dept):
    group = get_object_or_404(StudentGroup, pk=request.POST.get("id"))
    if not _in_scope(detected_dept, group.program.department_id):
        return JsonResponse({"status": "error", "message": "You can only edit groups in your own department."}, status=403)

    program_id = request.POST.get("program")
    program = group.program
    if program_id:
        program, err = _resolve_program(detected_dept, program_id)
        if err:
            return err

    fields, err = _clean_group_fields(request, program)
    if err:
        return err

    if StudentGroup.objects.filter(program=program, year=fields["year"], semester=fields["semester"],
                                    intake=fields["intake"], letter=fields["letter"]) \
            .exclude(pk=group.pk).exists():
        return JsonResponse({
            "status": "error",
            "message": f"Group '{fields['letter']}' already exists for this program/year/semester/intake.",
        }, status=400)

    group.program = program
    group.year = fields["year"]
    group.semester = fields["semester"]
    group.intake = fields["intake"]
    group.letter = fields["letter"]
    group.name = f"Group {fields['letter']}"
    group.save()
    return JsonResponse({"status": "success", "message": "Student group updated."})


def _delete_student_group(request, detected_dept):
    group = get_object_or_404(StudentGroup, pk=request.POST.get("id"))
    if not _in_scope(detected_dept, group.program.department_id):
        return JsonResponse({"status": "error", "message": "You can only delete groups in your own department."}, status=403)
    name = group.name
    group.delete()
    return JsonResponse({"status": "success", "message": f"Student group '{name}' deleted."})


# ───────────────────── Course Groups (bulk count + scope + stem pins) ─────────────────────
# "How many groups does this program/year/semester/intake need, do they
# apply to every compulsory course or just some, and — if this program/year
# already has Combination Stems — which letter belongs to which stem."
# See course_allocation/course_group_planner.py for the actual logic; this
# file only validates the request and adapts it to/from JSON.

def _group_plan_fields(request):
    try:
        year = int(request.POST.get("year") or 0)
        semester = int(request.POST.get("semester") or 0)
    except (TypeError, ValueError):
        return None, JsonResponse({"status": "error", "message": "Year and semester must be numbers."}, status=400)
    if not (1 <= year <= 6):
        return None, JsonResponse({"status": "error", "message": "Year must be between 1 and 6."}, status=400)
    if semester not in (1, 2, 3):
        return None, JsonResponse({"status": "error", "message": "Semester must be 1, 2, or 3."}, status=400)
    intake = (request.POST.get("intake") or "normal").strip()
    if intake not in ("normal", "special"):
        return None, JsonResponse({"status": "error", "message": "Invalid intake type."}, status=400)
    return {"year": year, "semester": semester, "intake": intake}, None


def _group_plan_context(request, detected_dept):
    program, err = _resolve_program(detected_dept, request.POST.get("program"))
    if err:
        return err
    fields, err = _group_plan_fields(request)
    if err:
        return err

    ctx = course_group_planner.get_plan_context(program, fields["year"], fields["semester"], fields["intake"])
    return JsonResponse({"status": "success", **ctx})


def _save_group_plan(request, detected_dept):
    program, err = _resolve_program(detected_dept, request.POST.get("program"))
    if err:
        return err
    fields, err = _group_plan_fields(request)
    if err:
        return err

    try:
        num_groups = int(request.POST.get("num_groups") or 1)
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "Number of groups must be a number."}, status=400)
    if num_groups < 1 or num_groups > 52:
        return JsonResponse({"status": "error", "message": "Number of groups must be between 1 and 52."}, status=400)

    scope = (request.POST.get("scope") or "all").strip()
    if scope not in (GroupingTemplate.SCOPE_ALL, GroupingTemplate.SCOPE_SELECTED):
        return JsonResponse({"status": "error", "message": "Invalid scope."}, status=400)

    selected_ids = [pk for pk in (_intval(v) for v in request.POST.getlist("course_ids[]")) if pk]
    if scope == GroupingTemplate.SCOPE_SELECTED and not selected_ids:
        return JsonResponse({
            "status": "error",
            "message": "Select at least one course, or choose 'All compulsory courses'.",
        }, status=400)

    stem_letter_map = {}
    for stem_id in request.POST.getlist("stem_ids[]"):
        sid = _intval(stem_id)
        if not sid:
            continue
        letters = [
            l.strip().upper()
            for l in request.POST.getlist(f"stem_letters_{stem_id}[]")
            if l.strip()
        ]
        if letters:
            stem_letter_map[sid] = letters

    dept = detected_dept or program.department
    alloc_set = get_active_allocation_set(request, dept) or get_or_default_legacy_set(dept)

    result = course_group_planner.save_group_plan(
        program=program,
        year=fields["year"],
        semester=fields["semester"],
        intake=fields["intake"],
        department=dept,
        user=request.user,
        num_groups=num_groups,
        scope=scope,
        selected_program_course_ids=selected_ids,
        stem_letter_map=stem_letter_map,
        allocation_set=alloc_set,
    )
    return JsonResponse({"status": "success", **result})


# ───────────────────── Course Groups (plan CRUD from the tab) ─────────────────────
# The "Course Groups" tab on /groups-electives/ manages the GroupingTemplate
# records that course_group_planner.save_group_plan() writes. Creating a plan
# always goes through the existing dialog -> save_group_plan; these two
# actions cover the tab's own Edit-side operations: removing a whole plan,
# and dropping a single stem<->letter pin (so a course group can be
# "un-combined" from a stem without losing the letter or any other pin).

def _delete_group_plan(request, detected_dept):
    plan_id = _intval(request.POST.get("id"))
    if not plan_id:
        return JsonResponse({"status": "error", "message": "Course group id is required."}, status=400)

    try:
        tmpl = GroupingTemplate.objects.select_related("program").get(pk=plan_id)
    except GroupingTemplate.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Course group plan not found."}, status=404)

    if not _in_scope(detected_dept, tmpl.program.department_id):
        return JsonResponse(
            {"status": "error", "message": "You can only delete course groups in your own department."},
            status=403,
        )

    label = f"{tmpl.program.name} — Year {tmpl.year} Sem {tmpl.semester} ({tmpl.intake})"
    with transaction.atomic():
        tmpl.delete()

    return JsonResponse({
        "status": "success",
        "message": (
            f"Course group plan '{label}' removed. Course allocations already created from it are "
            f"NOT deleted — use the allocation list if you also want to remove those."
        ),
    })


def _unpin_stem_from_letter(request, detected_dept):
    """
    Drop ONE stem<->letter pin from a course-group plan — e.g. because the
    Combination Stem should no longer take Group A of the shared course.

    The letter itself, the plan's other pins, and every course allocation
    already created are left alone. This only changes what auto-allocate
    will re-create the next time it rebuilds this department's allocations.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)

    plan_id = _intval(request.POST.get("id"))
    stem_id = _intval(request.POST.get("stem_id"))
    letter = (request.POST.get("letter") or "").strip().upper()
    if not (plan_id and stem_id and letter):
        return JsonResponse(
            {"status": "error", "message": "id, stem_id and letter are all required."},
            status=400,
        )

    try:
        tmpl = GroupingTemplate.objects.select_related("program").get(pk=plan_id)
    except GroupingTemplate.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Course group plan not found."}, status=404)

    if not _in_scope(detected_dept, tmpl.program.department_id):
        return JsonResponse(
            {"status": "error", "message": "You can only edit course groups in your own department."},
            status=403,
        )

    deleted, _ = (
        GroupingTemplateStemAssignment.objects
        .filter(template=tmpl, stem_id=stem_id, group__letter=letter)
        .delete()
    )
    if not deleted:
        return JsonResponse(
            {"status": "error", "message": f"'{letter}' is not pinned to that stem on this plan."},
            status=404,
        )

    return JsonResponse({
        "status": "success",
        "message": (
            f"Letter {letter} un-pinned from that stem. Auto-allocate will no longer re-create "
            f"this stem-tied section; existing allocations are unchanged."
        ),
    })


# ───────────────────── Combination Stem CRUD ─────────────────────
# A "Combination Stem" row here = one SpecializationStem, nested inside a
# SpecializationCategory that's created/reused transparently from the
# Program + (optional) Year/Semester the form submits — the person managing
# this page never names or thinks about the category as a separate object.

def _auto_category_name(year, semester):
    """Name for a category the page creates itself, from the term it covers."""
    if year and semester:
        return f"Year {year} Semester {semester} Combinations"
    if year:
        return f"Year {year} Combinations"
    if semester:
        return f"Semester {semester} Combinations"
    return "Combinations"


def _category_for_stem(dept, program, allocation_set, year, semester):
    """
    The category a stem for this program/term belongs to. All stems of one
    program/year/semester are alternatives of the same choice-point, so:
      * if exactly one category already covers that program + term (e.g. one
        made on the /cod/ panel), reuse it — never split a choice-point in two;
      * otherwise get-or-create the auto-named one.
    """
    existing = list(
        SpecializationCategory.objects
        .filter(program=program, allocation_set=allocation_set, year=year, semester=semester)
        .order_by("id")[:2]
    )
    if len(existing) == 1:
        category = existing[0]
        if category.department_id != dept.id:
            category.department = dept
            category.save(update_fields=["department"])
        return category
    category, _ = SpecializationCategory.objects.get_or_create(
        program=program, name=_auto_category_name(year, semester), allocation_set=allocation_set,
        defaults={"department": dept, "year": year, "semester": semester},
    )
    if category.year != year or category.semester != semester or category.department_id != dept.id:
        category.year = year
        category.semester = semester
        category.department = dept
        category.save(update_fields=["year", "semester", "department"])
    return category


def _delete_category_if_empty(category):
    if category and not category.stems.exists():
        category.delete()


def _add_stem(request, detected_dept):
    program, err = _resolve_program(detected_dept, request.POST.get("program"), "combination stem")
    if err:
        return err

    dept = program.department
    stem_name = (request.POST.get("stem_name") or "").strip()
    if not stem_name:
        return JsonResponse({"status": "error", "message": "Stem name is required."}, status=400)

    year = request.POST.get("year") or None
    semester = request.POST.get("semester") or None
    try:
        year = int(year) if year else None
        semester = int(semester) if semester else None
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "Year/semester must be numbers."}, status=400)

    allocation_set = get_active_allocation_set(request, dept) or get_or_default_legacy_set(dept)

    with transaction.atomic():
        category = _category_for_stem(dept, program, allocation_set, year, semester)
        if SpecializationStem.objects.filter(category=category, name__iexact=stem_name).exists():
            return JsonResponse({"status": "error", "message": "A stem with this name already exists for this program and term."}, status=400)
        stem = SpecializationStem.objects.create(category=category, name=stem_name, created_by=request.user)

    return JsonResponse({"status": "success", "id": stem.id, "message": f"Combination stem '{stem_name}' added."})


def _edit_stem(request, detected_dept):
    stem = get_object_or_404(SpecializationStem, pk=request.POST.get("id"))
    old_category = stem.category
    if not _in_scope(detected_dept, old_category.department_id):
        return JsonResponse({"status": "error", "message": "You can only edit stems in your own department."}, status=403)

    program_id = request.POST.get("program") or old_category.program_id
    program, err = _resolve_program(detected_dept, program_id, "combination stem")
    if err:
        return err
    dept = program.department

    stem_name = (request.POST.get("stem_name") or "").strip()
    if not stem_name:
        return JsonResponse({"status": "error", "message": "Stem name is required."}, status=400)

    year = request.POST.get("year") or None
    semester = request.POST.get("semester") or None
    try:
        year = int(year) if year else None
        semester = int(semester) if semester else None
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "Year/semester must be numbers."}, status=400)

    with transaction.atomic():
        allocation_set = old_category.allocation_set
        # Same program + same term -> the stem stays where it is (keeps whatever
        # name its category already has). Changing either moves it to the
        # category for the new program/term.
        same_category = (
            program.id == old_category.program_id
            and year == old_category.year
            and semester == old_category.semester
        )
        if same_category:
            category = old_category
        else:
            category = _category_for_stem(dept, program, allocation_set, year, semester)

        if SpecializationStem.objects.filter(category=category, name__iexact=stem_name).exclude(pk=stem.pk).exists():
            return JsonResponse({"status": "error", "message": "A stem with this name already exists for that program and term."}, status=400)

        stem.category = category
        stem.name = stem_name
        stem.save()

        if not same_category:
            _delete_category_if_empty(old_category)

    return JsonResponse({"status": "success", "message": "Combination stem updated."})


def _delete_stem(request, detected_dept):
    stem = get_object_or_404(SpecializationStem, pk=request.POST.get("id"))
    category = stem.category
    if not _in_scope(detected_dept, category.department_id):
        return JsonResponse({"status": "error", "message": "You can only delete stems in your own department."}, status=403)
    name = stem.name
    with transaction.atomic():
        stem.delete()
        _delete_category_if_empty(category)
    return JsonResponse({"status": "success", "message": f"Combination stem '{name}' deleted."})


def _stem_student_numbers(request, detected_dept):
    """
    "Student numbers" button on the Combination Stem Allocations panel:
    returns one row per Year/Semester term this stem's core courses occupy,
    for the dialog to pre-fill.
    """
    stem, err = _resolve_one_target("stem", request.POST.get("id"), detected_dept)
    if err:
        return err
    terms = course_group_planner.stem_student_number_terms(stem)
    if not terms:
        return JsonResponse({
            "status": "error",
            "message": "This stem has no core courses with a Year/Semester yet — map or allocate its courses first.",
        }, status=400)
    return JsonResponse({"status": "success", "stem_name": stem.name, "terms": terms})


def _save_stem_student_numbers(request, detected_dept):
    """
    Save side of the "Student numbers" dialog. POST id=<stem id> plus one or
    more terms=<year>:<semester>:<number_of_students>.
    """
    stem, err = _resolve_one_target("stem", request.POST.get("id"), detected_dept)
    if err:
        return err

    entries = []
    for raw in request.POST.getlist("terms"):
        year_s, _sep1, rest = raw.partition(":")
        semester_s, _sep2, number_s = rest.partition(":")
        if not (year_s.isdigit() and semester_s.isdigit() and number_s.isdigit()):
            return JsonResponse({"status": "error", "message": f"Bad term value: {raw!r}"}, status=400)
        entries.append((int(year_s), int(semester_s), int(number_s)))

    if not entries:
        return JsonResponse({"status": "error", "message": "Nothing to save."}, status=400)

    total_courses = 0
    with transaction.atomic():
        for year, semester, number_of_students in entries:
            _count, updated = course_group_planner.apply_stem_student_count(
                stem=stem, year=year, semester=semester,
                number_of_students=number_of_students, user=request.user,
            )
            total_courses += updated

    return JsonResponse({
        "status": "success",
        "message": (
            f"Student numbers saved for '{stem.name}' — applied to {total_courses} "
            f"course allocation(s) across {len(entries)} term(s)."
        ),
    })


def _bulk_stem_student_numbers(request, detected_dept):
    """
    "Update number of students" button on the Combination Stem tab: one row
    per (stem, Year/Semester term) across every program in the department, for
    the bulk dialog to fill — Program section -> Program Year subsection ->
    one row per stem with its current headcount.

    A stem contributes a row per term its CORE courses already occupy (same
    as the single-stem "Student numbers" dialog). A stem with no mapped core
    courses yet still gets one row keyed to its own category's Year/Semester
    (so a headcount can be entered ahead of mapping), provided that category
    has a specific Year and Semester set; stems with no year/semester to key
    on at all (category "Any year"/"Any semester" and no courses mapped yet)
    are left out — there's nothing to save them against.
    """
    dept, err = _resolve_department(request, detected_dept, "Department")
    if err:
        return err

    stems = (
        SpecializationStem.objects
        .filter(category__department_id=dept.id)
        .select_related("category", "category__program")
        .prefetch_related("courses__program_course", "elective_groups__courses", "student_counts")
        .order_by("category__program__name", "category__year", "category__semester", "category__name", "name")
    )

    programs_by_id = {}
    skipped = 0
    for stem in stems:
        program = stem.category.program
        terms = course_group_planner.stem_student_number_terms(stem)
        if not terms:
            year, semester = stem.category.year, stem.category.semester
            if not (year and semester):
                skipped += 1
                continue
            saved = stem.student_counts.filter(year=year, semester=semester).first()
            terms = [{
                "year": year, "semester": semester,
                "label": f"Year {year} Semester {semester}",
                "course_count": 0,
                "number_of_students": saved.number_of_students if saved else 0,
            }]

        prog_entry = programs_by_id.setdefault(program.id, {"id": program.id, "name": program.name, "years": {}})
        for t in terms:
            year_key = (t["year"], t["semester"])
            year_entry = prog_entry["years"].setdefault(year_key, {
                "year": t["year"], "semester": t["semester"], "label": t["label"], "stems": [],
            })
            year_entry["stems"].append({
                "id": stem.id,
                "name": stem.name,
                "number_of_students": t["number_of_students"],
                "course_count": t["course_count"],
            })

    programs_out = []
    for prog in sorted(programs_by_id.values(), key=lambda p: p["name"]):
        years_out = [prog["years"][k] for k in sorted(prog["years"].keys(), key=lambda k: (k[0] or 0, k[1] or 0))]
        programs_out.append({"id": prog["id"], "name": prog["name"], "years": years_out})

    return JsonResponse({"status": "success", "programs": programs_out, "skipped": skipped})


def _save_bulk_stem_student_numbers(request, detected_dept):
    """
    Save side of the "Update number of students" bulk dialog. POST one or
    more entries=<stem_id>:<year>:<semester>:<number_of_students>. Every stem
    referenced must belong to the caller's department scope.
    """
    raw_entries = request.POST.getlist("entries")
    if not raw_entries:
        return JsonResponse({"status": "error", "message": "Nothing to save."}, status=400)

    parsed = []
    stem_ids = set()
    for raw in raw_entries:
        stem_id_s, _sep0, rest0 = raw.partition(":")
        year_s, _sep1, rest = rest0.partition(":")
        semester_s, _sep2, number_s = rest.partition(":")
        if not (stem_id_s.isdigit() and year_s.isdigit() and semester_s.isdigit() and number_s.isdigit()):
            return JsonResponse({"status": "error", "message": f"Bad entry value: {raw!r}"}, status=400)
        parsed.append((int(stem_id_s), int(year_s), int(semester_s), int(number_s)))
        stem_ids.add(int(stem_id_s))

    stems_by_id = {
        s.id: s for s in SpecializationStem.objects.filter(id__in=stem_ids).select_related("category")
    }
    for stem_id in stem_ids:
        stem = stems_by_id.get(stem_id)
        if not stem:
            return JsonResponse({"status": "error", "message": "One of these stems no longer exists."}, status=404)
        if not _in_scope(detected_dept, stem.category.department_id):
            return JsonResponse({"status": "error", "message": "You can only manage stems in your own department."}, status=403)

    total_courses = 0
    with transaction.atomic():
        for stem_id, year, semester, number_of_students in parsed:
            _count, updated = course_group_planner.apply_stem_student_count(
                stem=stems_by_id[stem_id], year=year, semester=semester,
                number_of_students=number_of_students, user=request.user,
            )
            total_courses += updated

    return JsonResponse({
        "status": "success",
        "message": (
            f"Student numbers saved for {len(stem_ids)} stem(s) — applied to "
            f"{total_courses} course allocation(s) across {len(parsed)} entry(ies)."
        ),
    })


# ───────────────────────── Elective Group CRUD ─────────────────────────
# An "Elective Group" row here = one SelectionGroup. Actual elective
# courses are still added/removed on /cod/base-selections/.

def _add_elective_group(request, detected_dept):
    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"status": "error", "message": "Elective group name is required."}, status=400)

    department_id = request.POST.get("department")
    if detected_dept:
        department = detected_dept
    else:
        if not department_id:
            return JsonResponse({"status": "error", "message": "Department is required."}, status=400)
        department = get_object_or_404(Department, pk=department_id)

    program = None
    program_id = request.POST.get("program")
    if program_id:
        program, err = _resolve_program(detected_dept, program_id, "elective group")
        if err:
            return err

    allocation_set = get_active_allocation_set(request, department) or get_or_default_legacy_set(department)

    if SelectionGroup.objects.filter(name__iexact=name, department=department, allocation_set=allocation_set).exists():
        return JsonResponse({"status": "error", "message": f"Elective group '{name}' already exists in this department."}, status=400)

    group = SelectionGroup.objects.create(
        name=name, department=department, program=program,
        created_by=request.user, allocation_set=allocation_set,
    )
    return JsonResponse({"status": "success", "id": group.id, "message": f"Elective group '{name}' added."})


def _edit_elective_group(request, detected_dept):
    group = get_object_or_404(SelectionGroup, pk=request.POST.get("id"))
    if not _in_scope(detected_dept, group.department_id):
        return JsonResponse({"status": "error", "message": "You can only edit elective groups in your own department."}, status=403)

    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"status": "error", "message": "Elective group name is required."}, status=400)

    program = group.program
    program_id = request.POST.get("program")
    if program_id:
        program, err = _resolve_program(detected_dept, program_id, "elective group")
        if err:
            return err
    elif program_id == "":
        program = None

    if SelectionGroup.objects.filter(name__iexact=name, department=group.department, allocation_set=group.allocation_set) \
            .exclude(pk=group.pk).exists():
        return JsonResponse({"status": "error", "message": f"Elective group '{name}' already exists in this department."}, status=400)

    group.name = name
    group.program = program
    group.save()
    return JsonResponse({"status": "success", "message": "Elective group updated."})


def _delete_elective_group(request, detected_dept):
    group = get_object_or_404(SelectionGroup, pk=request.POST.get("id"))
    if not _in_scope(detected_dept, group.department_id):
        return JsonResponse({"status": "error", "message": "You can only delete elective groups in your own department."}, status=403)
    name = group.name
    group.delete()
    return JsonResponse({"status": "success", "message": f"Elective group '{name}' deleted."})


def _delete_selected_targets(request, detected_dept):
    """
    "Delete selected" toolbar button on the Combination Stem / Elective Group lists.
    Reuses the same targets=kind:id encoding as the multi-target course mapping
    picker (see _mapping_targets), so whatever the user ticked with the
    select-all checkbox gets deleted in one call. Deleting a stem also cleans
    up its parent category if it's left empty, same as the single-row delete.
    """
    targets, err = _mapping_targets(request, detected_dept, require=True)
    if err:
        return err

    deleted_names = []
    with transaction.atomic():
        for obj in targets:
            deleted_names.append(obj.name)
            if isinstance(obj, SpecializationStem):
                category = obj.category
                obj.delete()
                _delete_category_if_empty(category)
            else:
                obj.delete()

    count = len(deleted_names)
    noun = "item" if count == 1 else "items"
    return JsonResponse({
        "status": "success",
        "message": f"{count} {noun} deleted.",
        "deleted": deleted_names,
    })


# ───────────── Map curriculum courses (ProgramCourse) to a stem / elective group ─────────────

def _resolve_one_target(kind, tid, detected_dept):
    """Resolve a single (kind, id) pair -> (stem_or_group, error_response_or_None)."""
    if kind == "stem":
        obj = get_object_or_404(SpecializationStem.objects.select_related("category"), pk=tid)
        dept_id = obj.category.department_id
    elif kind == "elective":
        obj = get_object_or_404(SelectionGroup, pk=tid)
        dept_id = obj.department_id
    else:
        return None, JsonResponse({"status": "error", "message": "kind must be 'stem' or 'elective'."}, status=400)
    if not _in_scope(detected_dept, dept_id):
        return None, JsonResponse(
            {"status": "error", "message": "You can only map courses for your own department."}, status=403)
    return obj, None


def _mapping_target(request, detected_dept):
    """Resolve ONE (kind, stem_or_group, error_response). kind is 'stem' or 'elective'.
    Used by the single-target actions: unmap and apply-mappings."""
    kind = (request.POST.get("kind") or "").strip()
    obj, err = _resolve_one_target(kind, request.POST.get("id"), detected_dept)
    if err:
        return None, None, err
    return kind, obj, None


def _mapping_targets(request, detected_dept, require=True):
    """
    Resolve ZERO OR MORE mapping targets, so a single "Map courses" action can push
    the same batch of courses onto several stems and/or elective groups at once.

    Accepts either:
      * targets=stem:12&targets=elective:5&targets=stem:9   (multi-target toolbar / picker flow)
      * kind=stem&id=12                                     (classic single-row "Map courses" button)
    With require=False, no targets at all is not an error (used by the course
    listing endpoint before the user has picked a target in the "pick" flow) and
    an empty list is returned instead. require=True (the default, used whenever
    courses are actually about to be mapped) still insists on at least one.
    Returns (list_of_stem_or_group_objects, error_response_or_None).
    """
    raw = request.POST.getlist("targets")
    pairs = []
    if raw:
        for item in raw:
            kind, sep, tid = item.partition(":")
            if sep:
                pairs.append((kind.strip(), tid.strip()))
    else:
        kind, tid = (request.POST.get("kind") or "").strip(), (request.POST.get("id") or "").strip()
        if kind and tid:
            pairs.append((kind, tid))

    if not pairs:
        if require:
            return None, JsonResponse({"status": "error", "message": "Select at least one stem or elective group."}, status=400)
        return [], None

    targets, seen = [], set()
    for kind, tid in pairs:
        key = (kind, tid)
        if key in seen:
            continue
        seen.add(key)
        obj, err = _resolve_one_target(kind, tid, detected_dept)
        if err:
            return None, err
        targets.append(obj)
    return targets, None


def _intval(value):
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def _list_mappable_courses(request, detected_dept):
    """
    Curriculum courses that can still be mapped to the target(s).
    Filters: program (required) -> optional year -> optional semester.
    Program only = every course of the program.

    Accepts ONE program (`program=<id>`, as before) or SEVERAL
    (`program[]=<id>&program[]=<id>`) — e.g. "BEd Arts" + "BEd Science" both
    teaching "EDFO 111". With several programs, every course CODE that
    appears in AT LEAST ONE of them is listed (not just ones common to all —
    a combination stem is often built on one program while sharing a course
    code with a stem on another program whose curriculum never got a
    ProgramCourse row for it, and that course must not silently disappear
    from the picker). Each row carries one ProgramCourse id per program that
    already has that code; a program missing it gets a "new:<program_id>:
    <rep_pc_id>" placeholder instead — ticking the row and mapping it creates
    that program's ProgramCourse on the fly (course_name/year/semester/
    unit_type/cohort copied from the representative row) before mapping, see
    `_resolve_or_create_program_courses` — this is what lets a course group
    be combined across two programs' stems (see PROGRAM_COURSE_MAPPING.md,
    "Combining a mapping across programs").

    With a single target, courses already mapped there are left out (as before).
    With several targets, a course is only left out once it is already mapped to
    EVERY selected target AND every selected program already has its own
    ProgramCourse for it — otherwise it's still useful to show, since mapping it
    again will fill in whichever targets (or whichever program's curriculum) it's
    missing from.

    With mode=alloc (the "Map to course allocation" dialog) nothing is left out; each
    course is annotated with the selected targets whose course allocation already holds it.
    """
    targets, err = _mapping_targets(request, detected_dept, require=False)
    if err:
        return err

    program_ids = [pid for pid in request.POST.getlist("program[]") if str(pid).isdigit()]
    if not program_ids:
        single, err = _resolve_program(detected_dept, request.POST.get("program"))
        if err:
            return err
        programs = [single]
    else:
        programs = []
        for pid in dict.fromkeys(program_ids):  # de-dupe, keep order
            p, err = _resolve_program(detected_dept, pid)
            if err:
                return err
            programs.append(p)
    multi = len(programs) > 1

    # mode=alloc ("Map to course allocation"): being mapped at program-course level says
    # nothing about whether an allocation exists, and a course already allocated can be
    # added again on request — so nothing is left out; each row is annotated with the
    # selected targets that already have it in their course allocation instead.
    alloc_mode = (request.POST.get("mode") or "").strip() == "alloc"
    allocated_per_target = []  # [(target name, Counter(program_course_id -> copies))]
    if alloc_mode:
        for t in targets:
            allocated_per_target.append((t.name, Counter(t.courses.values_list("program_course_id", flat=True))))

    year, sem = _intval(request.POST.get("year")), _intval(request.POST.get("semester"))

    def _base_qs(program):
        qs = ProgramCourse.objects.filter(program=program)
        if year:
            qs = qs.filter(year=year)
        if sem:
            qs = qs.filter(semester=sem)
        return qs.prefetch_related("mapped_stems", "mapped_selection_groups").order_by("year", "semester", "course_code")

    LIMIT = 3000

    if not multi:
        program = programs[0]
        qs = _base_qs(program)
        if not alloc_mode:
            if len(targets) == 1:
                qs = qs.exclude(pk__in=targets[0].program_courses.values("pk"))
            else:
                common_ids = None
                for t in targets:
                    ids = set(t.program_courses.values_list("pk", flat=True))
                    common_ids = ids if common_ids is None else (common_ids & ids)
                if common_ids:
                    qs = qs.exclude(pk__in=common_ids)

        rows = []
        for pc in qs[:LIMIT + 1]:
            others = [st.name for st in pc.mapped_stems.all()] + [g.name for g in pc.mapped_selection_groups.all()]
            row = {
                "id": pc.id, "program_course_ids": [pc.id], "code": pc.course_code, "name": pc.course_name,
                "year": pc.year, "semester": pc.semester,
                "unit_type": pc.get_unit_type_display(), "cohort": pc.student_cohort,
                "also_in": ", ".join(others[:3]) + (" …" if len(others) > 3 else ""),
            }
            if alloc_mode:
                have = [(name, cnt[pc.id]) for name, cnt in allocated_per_target if cnt.get(pc.id)]
                row["in_alloc"] = ", ".join(n + (f" ×{c}" if c > 1 else "") for n, c in have[:3]) + (" …" if len(have) > 3 else "")
                row["in_alloc_all"] = bool(targets) and len(have) == len(targets)
            rows.append(row)
        truncated = len(rows) > LIMIT
        already_mapped = targets[0].program_courses.filter(program=program).count() if len(targets) == 1 else None
        return JsonResponse({
            "status": "success", "program": program.name, "courses": rows[:LIMIT], "truncated": truncated,
            "already_mapped": already_mapped,
            "target_names": [t.name for t in targets],
        })

    # ── Several programs: list every course CODE found in ANY of them ──
    # (previously this intersected the programs' codes, which silently dropped a
    # course whenever one program's curriculum never got a ProgramCourse row for
    # it — exactly the case for a combination stem built across programs where
    # only one side's curriculum was kept up to date).
    per_program_by_code = []
    for program in programs:
        by_code = {}
        for pc in _base_qs(program)[:LIMIT + 1]:
            by_code.setdefault(pc.course_code.strip().upper(), pc)
        per_program_by_code.append(by_code)

    all_codes = set()
    for by_code in per_program_by_code:
        all_codes |= set(by_code.keys())

    # Pre-fetch each target's already-mapped ProgramCourse ids once, so the
    # "already mapped everywhere" exclusion below (non-alloc mode) doesn't hit
    # the DB per row.
    mapped_sets = [set(t.program_courses.values_list("pk", flat=True)) for t in targets] if not alloc_mode else []

    rows = []
    for code in sorted(all_codes):
        pcs_for_code = [by_code.get(code) for by_code in per_program_by_code]
        rep = next(pc for pc in pcs_for_code if pc is not None)
        existing_ids = [pc.id for pc in pcs_for_code if pc is not None]
        missing_programs = [p.name for p, pc in zip(programs, pcs_for_code) if pc is None]
        ids = [
            (pc.id if pc is not None else f"new:{program.id}:{rep.id}")
            for program, pc in zip(programs, pcs_for_code)
        ]

        if not alloc_mode and not missing_programs and targets:
            if all(all(pid in ms for pid in existing_ids) for ms in mapped_sets):
                continue  # already mapped to every target, every program already has it

        others = []
        for pc in pcs_for_code:
            if pc is None:
                continue
            others += [st.name for st in pc.mapped_stems.all()] + [g.name for g in pc.mapped_selection_groups.all()]
        row = {
            "id": rep.id, "program_course_ids": ids, "code": rep.course_code,
            "name": rep.course_name, "year": rep.year, "semester": rep.semester,
            "unit_type": rep.get_unit_type_display(), "cohort": rep.student_cohort,
            "also_in": ", ".join(dict.fromkeys(others))[:80],
            "missing_in": ", ".join(missing_programs)[:80],
        }
        if alloc_mode:
            have = []
            for name, cnt in allocated_per_target:
                if any(cnt.get(pid) for pid in existing_ids):
                    have.append(name)
            row["in_alloc"] = ", ".join(have[:3]) + (" …" if len(have) > 3 else "")
            row["in_alloc_all"] = bool(targets) and len(have) == len(targets)
        rows.append(row)

    truncated = len(rows) > LIMIT
    return JsonResponse({
        "status": "success", "program": " + ".join(p.name for p in programs),
        "courses": rows[:LIMIT], "truncated": truncated,
        "already_mapped": None,
        "target_names": [t.name for t in targets],
    })


def _resolve_or_create_program_courses(raw_ids, detected_dept, user):
    """
    Turn the id list posted by the mapping dialog into real ProgramCourse rows.

    A plain entry is a digit primary key, as before. An entry can also be a
    "new:<program_id>:<rep_pc_id>" placeholder — built by `_list_mappable_courses`
    when a course code exists in some of the selected programs but not this one
    (its ProgramCourse row was never created there). Resolving it creates that
    program's ProgramCourse on the spot, copying course_code/course_name/year/
    semester/unit_type/student_cohort off the representative row `rep_pc_id`, so
    the course can be mapped to that program's stems/groups immediately instead
    of being silently left out of the picker.

    Returns (pcs, created_count).
    """
    real_ids, new_specs = [], []
    for raw in raw_ids:
        raw = str(raw).strip()
        if raw.isdigit():
            real_ids.append(raw)
            continue
        parts = raw.split(":")
        if len(parts) == 3 and parts[0] == "new" and parts[1].isdigit() and parts[2].isdigit():
            new_specs.append((int(parts[1]), int(parts[2])))

    pcs = list(ProgramCourse.objects.filter(pk__in=real_ids).select_related("program")) if real_ids else []
    created = 0
    if new_specs:
        rep_ids = {rep_id for _pid, rep_id in new_specs}
        reps = {pc.id: pc for pc in ProgramCourse.objects.filter(pk__in=rep_ids)}
        program_ids = {pid for pid, _rep in new_specs}
        programs_by_id = {p.id: p for p in Program.objects.filter(pk__in=program_ids)}
        seen = {(pc.program_id, pc.course_code.strip().upper(), pc.student_cohort) for pc in pcs}
        for program_id, rep_id in dict.fromkeys(new_specs):  # de-dupe, keep first-seen order
            rep = reps.get(rep_id)
            program = programs_by_id.get(program_id)
            if rep is None or program is None or not _in_scope(detected_dept, program.department_id):
                continue
            key = (program_id, rep.course_code.strip().upper(), rep.student_cohort)
            if key in seen:
                continue
            existing = ProgramCourse.objects.filter(
                program_id=program_id, course_code__iexact=rep.course_code,
                student_cohort=rep.student_cohort,
            ).select_related("program").first()
            if existing is not None:
                pcs.append(existing)
                seen.add(key)
                continue
            try:
                with transaction.atomic():
                    pc = ProgramCourse.objects.create(
                        program=program, course_code=rep.course_code, course_name=rep.course_name,
                        year=rep.year, semester=rep.semester, unit_type=rep.unit_type,
                        student_cohort=rep.student_cohort,
                    )
            except IntegrityError:
                # Created concurrently between the exists-check and here — use that row.
                pc = ProgramCourse.objects.filter(
                    program_id=program_id, course_code__iexact=rep.course_code,
                    student_cohort=rep.student_cohort,
                ).select_related("program").first()
                if pc is None:
                    continue
            else:
                created += 1
            pcs.append(pc)
            seen.add(key)
    return pcs, created


def _map_courses(request, detected_dept):
    """
    Map the selected courses onto every selected target in one shot — one or many
    stems, one or many elective groups, or a mix of both — so the same batch of
    courses can be wired into several combination stems at the same time. An entry
    can be a "new:<program_id>:<rep_pc_id>" placeholder (see
    `_resolve_or_create_program_courses`), which creates that program's missing
    ProgramCourse before mapping.
    """
    targets, err = _mapping_targets(request, detected_dept)
    if err:
        return err
    raw_ids = [i for i in request.POST.getlist("program_course_ids") if str(i).strip()]
    if not raw_ids:
        return JsonResponse({"status": "error", "message": "Select at least one course to map."}, status=400)
    pcs, created = _resolve_or_create_program_courses(raw_ids, detected_dept, request.user)
    if not pcs:
        return JsonResponse({"status": "error", "message": "Those courses no longer exist."}, status=404)

    apply_now = request.POST.get("apply_now") == "1"
    total_added = total_attached = total_skipped = 0
    # A batch spanning several programs (the dialog's "+ Add another program") carries
    # each program's OWN copy of a shared unit. Every stem / group must only receive its
    # own program's copies — never the other programs' — or their allocations would later
    # be attached to the wrong program's stems. A one-program batch is left as it was.
    multi_program_batch = len({pc.program_id for pc in pcs}) > 1
    idle_targets = 0
    with transaction.atomic():
        for target in targets:
            target_program_id = (target.category.program_id if isinstance(target, SpecializationStem)
                                 else target.program_id)
            mine = ([pc for pc in pcs if pc.program_id == target_program_id]
                    if multi_program_batch and target_program_id else pcs)
            if not mine:
                idle_targets += 1
                continue
            before = set(target.program_courses.values_list("id", flat=True))
            target.program_courses.add(*mine)
            total_added += len({pc.id for pc in mine} - before)
            if apply_now:
                applied = sync_target(target)
                total_attached += applied["attached"]
                total_skipped += applied.get("skipped_student_group", 0)

    if len(targets) == 1:
        target_desc = f"'{targets[0].name}'"
    else:
        names = [t.name for t in targets]
        target_desc = f"{len(targets)} targets ({', '.join(names[:4])}{'…' if len(names) > 4 else ''})"

    msg = f"Mapped {len(pcs)} course(s) to {target_desc}."
    if created:
        msg += f" {created} course(s) were newly added to the curriculum (program course) to match."
    if idle_targets:
        msg += f" {idle_targets} target(s) had no courses from their own program and were left unchanged."
    if apply_now:
        msg += (f" Attached {total_attached} existing allocation(s)"
                + (f"; {total_skipped} skipped (bound to a student group)" if total_skipped else "") + ".")
    return JsonResponse({"status": "success", "message": msg, "added": total_added})


def _map_to_allocation(request, detected_dept):
    """
    "Map to course allocation": put the selected curriculum courses into the COURSE
    ALLOCATION of every selected stem / elective group — adding the allocations that are
    missing — and make sure each course is also mapped at program-course level on that
    same stem / group. A course the target already has is skipped and reported; with
    allow_duplicates=1 another copy (an extra section) is added instead, e.g. COSC 103
    twice because its students are split. See course_mapping.map_courses_to_allocation.
    An entry can be a "new:<program_id>:<rep_pc_id>" placeholder (see
    `_resolve_or_create_program_courses`), which creates that program's missing
    ProgramCourse before mapping — this is what lets a combination stem pick up a
    course whose curriculum entry only ever existed on another program.
    """
    targets, err = _mapping_targets(request, detected_dept)
    if err:
        return err
    raw_ids = [i for i in request.POST.getlist("program_course_ids") if str(i).strip()]
    if not raw_ids:
        return JsonResponse({"status": "error", "message": "Select at least one course to map."}, status=400)
    pcs, created = _resolve_or_create_program_courses(raw_ids, detected_dept, request.user)
    pcs.sort(key=lambda pc: (pc.year, pc.semester, pc.course_code))
    if not pcs:
        return JsonResponse({"status": "error", "message": "Those courses no longer exist."}, status=404)
    if any(not _in_scope(detected_dept, pc.program.department_id) for pc in pcs):
        return JsonResponse({"status": "error", "message": "You can only map courses of your own department."}, status=403)

    allow_dup = request.POST.get("allow_duplicates") == "1"
    res = map_courses_to_allocation(targets, pcs, allow_duplicates=allow_dup, user=request.user)
    c = res["counts"]

    # Every count is one course on one stem / elective group.
    parts = []
    if c["added"]:
        parts.append(f"{c['added']} added to the course allocation")
    if c["attached"]:
        parts.append(f"{c['attached']} existing allocation(s) attached")
    if c["duplicate"]:
        parts.append(f"{c['duplicate']} extra cop{'y' if c['duplicate'] == 1 else 'ies'} added")
    if c["skipped"]:
        parts.append(f"{c['skipped']} skipped (already in the allocation)")
    if c["error"]:
        parts.append(f"{c['error']} could not be mapped")
    if c["pc_mapped"]:
        parts.append(f"{c['pc_mapped']} newly mapped at program-course level")
    if created:
        parts.append(f"{created} program-course(s) newly created to match another program's curriculum")
    msg = ("; ".join(parts) + " (counted per course per stem / group).") if parts else "Nothing to do."
    combined = res.get("combined_groups") or []
    if combined:
        msg += f" {len(combined)} course group(s) combined across programs: " + ", ".join(
            f"{cg['group_code']} ({' + '.join(cg['programs'])})" for cg in combined[:5]
        ) + ("…" if len(combined) > 5 else "") + "."
    problems = res.get("combine_problems") or []
    if problems:
        msg += (f" {len(problems)} course group(s) could NOT be combined across programs "
                f"(the courses themselves were mapped) — see the details below.")
    changed = c["added"] + c["attached"] + c["duplicate"] + c["pc_mapped"]
    return JsonResponse({
        "status": "success", "message": msg, "counts": c, "changed": changed,
        "combined_groups": combined, "combine_problems": problems,
        "lines": res["lines"][:500], "lines_truncated": len(res["lines"]) > 500,
    })


def _remove_from_allocation(request, detected_dept):
    """
    "View combination allocations" -> remove courses from stems' COURSE ALLOCATION, one at
    a time or in bulk. Only the link between the stem and the allocation is removed: the
    allocation itself is kept (it may be shared with other stems / used by the timetable),
    and the program-course mapping stays, so it can be added back with "Add to allocation"
    or "Apply".

    POST either  id=<stem id> + allocation_ids=<id> (repeatable)         — one stem
           or    items=<stem id>:<allocation id> (repeatable)            — any mix of stems
    All stems are checked against the user's department before anything is changed.
    """
    by_stem = {}
    raw_items = request.POST.getlist("items")
    if raw_items:
        for item in raw_items:
            sid, _sep, aid = item.partition(":")
            if sid.strip().isdigit() and aid.strip().isdigit():
                by_stem.setdefault(int(sid), []).append(int(aid))
    else:
        sid = (request.POST.get("id") or "").strip()
        ids = [int(i) for i in request.POST.getlist("allocation_ids") if str(i).isdigit()]
        if sid.isdigit() and ids:
            by_stem[int(sid)] = ids
    if not by_stem:
        return JsonResponse({"status": "error", "message": "Select at least one course to remove."}, status=400)

    stems = []
    for sid, ids in by_stem.items():
        stem, err = _resolve_one_target("stem", sid, detected_dept)
        if err:
            return err
        stems.append((stem, ids))

    removed, pooled = [], []
    with transaction.atomic():
        for stem, ids in stems:
            res = detach_allocations_from_stem(stem, ids)
            removed += res["removed"]
            pooled += res["pooled"]

    if not removed and pooled:
        return JsonResponse({
            "status": "error",
            "message": ("Those courses come from an elective pool nested in the stem — remove them "
                        "from the elective group instead."),
        }, status=400)
    if not removed:
        return JsonResponse({"status": "error", "message": "None of those courses are in the stem's allocation."}, status=404)

    codes = ", ".join(dict.fromkeys(ca.course_code for ca in removed))
    if len(codes) > 80:
        codes = codes[:77] + "…"
    where = f"the allocation of '{stems[0][0].name}'" if len(stems) == 1 else f"the allocation of {len(stems)} stems"
    msg = f"Removed {len(removed)} course(s) ({codes}) from {where}. The allocations themselves are kept."
    if pooled:
        msg += f" {len(pooled)} pooled course(s) were left — they belong to an elective pool."
    return JsonResponse({"status": "success", "message": msg, "removed": len(removed)})


def _unmap_course(request, detected_dept):
    kind, target, err = _mapping_target(request, detected_dept)
    if err:
        return err
    pc = get_object_or_404(ProgramCourse, pk=request.POST.get("program_course_id"))
    target.program_courses.remove(pc)
    target.program_courses_from_allocation.remove(pc)
    return JsonResponse({"status": "success",
                         "message": f"{pc.course_code} un-mapped from '{target.name}' "
                                    f"(existing allocations were left as they are)."})


def _apply_mappings(request, detected_dept):
    kind, target, err = _mapping_target(request, detected_dept)
    if err:
        return err
    res = sync_target(target)
    if not res["mapped"]:
        return JsonResponse({"status": "error", "message": "No curriculum courses are mapped here yet."}, status=400)
    msg = (f"Attached {res['attached']} allocation(s); {res['already']} were already there"
           + (f"; {res['skipped_student_group']} skipped (bound to a student group)" if res["skipped_student_group"] else "")
           + (f"; {res['without_allocation']} mapped course(s) have no allocation yet" if res["without_allocation"] else "")
           + ".")
    return JsonResponse({"status": "success", "message": msg, "result": res})

# ───────────────────── Course Groups: quick-create-by-course-code + matrix ─────────────────────
# See course_group_draft.py for the actual logic; this file only validates
# the request, resolves department/program/stem scope, and adapts the
# result to/from JSON — same split as the classic Course Groups handlers above.

def _cg_course_choices(request, detected_dept):
    dept, err = _resolve_department(request, detected_dept)
    if err:
        return err
    return JsonResponse({"status": "success", "courses": course_group_draft.department_course_choices(dept)})


def _cg_create_drafts(request, detected_dept):
    dept, err = _resolve_department(request, detected_dept)
    if err:
        return err

    try:
        num_groups = int(request.POST.get("num_groups") or 1)
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "Number of groups must be a number."}, status=400)
    if num_groups < 1 or num_groups > 52:
        return JsonResponse({"status": "error", "message": "Number of groups must be between 1 and 52."}, status=400)

    entries = []
    # Picked from the scrollable multi-select: course_codes[] (+ matching course_names[]).
    codes = request.POST.getlist("course_codes[]")
    names = request.POST.getlist("course_names[]")
    for i, code in enumerate(codes):
        entries.append({"code": code, "name": names[i] if i < len(names) else ""})
    # Typed by hand: comma or newline separated codes, no names.
    typed = (request.POST.get("typed_codes") or "").strip()
    if typed:
        for chunk in typed.replace("\n", ",").split(","):
            chunk = chunk.strip()
            if chunk:
                entries.append({"code": chunk, "name": ""})

    if not entries:
        return JsonResponse({"status": "error", "message": "Pick or type at least one course code."}, status=400)

    drafts = course_group_draft.create_or_update_drafts(
        department=dept, user=request.user, course_entries=entries, num_groups=num_groups,
    )
    return JsonResponse({
        "status": "success",
        "message": f"{len(drafts)} course group(s) ready — add a program below to start mapping.",
        "draft_ids": [d.id for d in drafts],
    })


def _resolve_draft(detected_dept, draft_id):
    draft = get_object_or_404(CourseGroupDraft, pk=draft_id)
    if not _in_scope(detected_dept, draft.department_id):
        return None, JsonResponse(
            {"status": "error", "message": "You can only manage course groups in your own department."}, status=403,
        )
    return draft, None


def _cg_list_drafts(request, detected_dept):
    dept, err = _resolve_department(request, detected_dept)
    if err:
        return err
    drafts = CourseGroupDraft.objects.filter(department=dept).order_by("course_code")
    return JsonResponse({"status": "success", "drafts": [
        {
            "id": d.id, "course_code": d.course_code, "course_name": d.course_name,
            "num_groups": d.num_groups,
            "suggested_year": d.suggested_year, "suggested_semester": d.suggested_semester,
        }
        for d in drafts
    ]})


def _cg_matrix(request, detected_dept):
    draft, err = _resolve_draft(detected_dept, request.POST.get("draft_id"))
    if err:
        return err
    matrix = course_group_draft.draft_matrix(draft)
    return JsonResponse({"status": "success", "draft": {
        "id": draft.id, "course_code": draft.course_code, "course_name": draft.course_name,
        "num_groups": draft.num_groups,
    }, **matrix})


def _cg_program_stems(request, detected_dept):
    """Stems to show as new matrix rows once a program is added to a
    draft's mapping matrix, plus that program's own year/semester match
    for this draft's course code (for the row header)."""
    draft, err = _resolve_draft(detected_dept, request.POST.get("draft_id"))
    if err:
        return err
    program, err = _resolve_program(detected_dept, request.POST.get("program_id"))
    if err:
        return err
    pc = course_group_draft._matching_program_course(draft, program)
    if not pc:
        return JsonResponse({
            "status": "error",
            "message": f"{program.name} has no '{draft.course_code}' in its curriculum.",
        }, status=400)
    stems = course_group_draft.stems_for_program(program)
    return JsonResponse({"status": "success", "year": pc.year, "semester": pc.semester, "stems": [
        {"id": s.id, "name": s.name, "category_name": s.category.name if s.category_id else ""} for s in stems
    ]})


def _cg_toggle_mapping(request, detected_dept):
    draft, err = _resolve_draft(detected_dept, request.POST.get("draft_id"))
    if err:
        return err
    program, err = _resolve_program(detected_dept, request.POST.get("program_id"))
    if err:
        return err
    stem_id = _intval(request.POST.get("stem_id"))
    stem = None
    if stem_id:
        stem = get_object_or_404(SpecializationStem, pk=stem_id)
        if not stem.category or stem.category.program_id != program.id:
            return JsonResponse({"status": "error", "message": "That stem does not belong to this program."}, status=400)
    letter = (request.POST.get("letter") or "").strip().upper()
    if not letter:
        return JsonResponse({"status": "error", "message": "Letter is required."}, status=400)
    on = (request.POST.get("on") or "1") in ("1", "true", "True")

    ok, message = course_group_draft.toggle_mapping(
        draft=draft, program=program, stem=stem, letter_str=letter, on=on, user=request.user,
    )
    if not ok:
        return JsonResponse({"status": "error", "message": message}, status=400)
    return JsonResponse({"status": "success", "matrix": course_group_draft.draft_matrix(draft)})


def _cg_copy_targets(request, detected_dept):
    """Other course-group drafts in the department, for the "Copy to" picker."""
    draft, err = _resolve_draft(detected_dept, request.POST.get("draft_id"))
    if err:
        return err
    targets = course_group_draft.copyable_target_drafts(draft)
    return JsonResponse({"status": "success", "targets": [
        {"id": t.id, "course_code": t.course_code, "course_name": t.course_name} for t in targets
    ]})


def _cg_copy_draft(request, detected_dept):
    """"Copy to" button on a course group's card: fan EVERY letter's
    stem/program mappings for this whole course group out, letter-for-
    letter, onto one or more other drafts (see
    course_group_draft.copy_draft_mappings)."""
    draft, err = _resolve_draft(detected_dept, request.POST.get("draft_id"))
    if err:
        return err

    target_ids = [tid for tid in request.POST.getlist("target_draft_ids[]") if tid]
    if not target_ids:
        return JsonResponse({"status": "error", "message": "Pick at least one course group to copy to."}, status=400)
    targets = list(CourseGroupDraft.objects.filter(id__in=target_ids, department=draft.department))
    if not targets:
        return JsonResponse({"status": "error", "message": "No valid course groups selected."}, status=400)

    result = course_group_draft.copy_draft_mappings(
        source_draft=draft, target_drafts=targets, user=request.user,
    )
    copied_names = [t.course_code for t in targets if result["copied"].get(t.id)]
    total = sum(result["copied"].values())
    if total:
        msg = f"Copied {draft.course_code} to {', '.join(copied_names)}."
    elif result["skipped"]:
        msg = "Nothing new to copy."
    else:
        msg = f"{draft.course_code} has no stems mapped yet — nothing to copy."
    if result["skipped"]:
        msg += " " + "; ".join(result["skipped"][:3])
        if len(result["skipped"]) > 3:
            msg += f" (+{len(result['skipped']) - 3} more)"

    return JsonResponse({
        "status": "success",
        "message": msg,
        "matrices": {str(t.id): course_group_draft.draft_matrix(t) for t in targets},
    })


def _cg_commit(request, detected_dept):
    draft, err = _resolve_draft(detected_dept, request.POST.get("draft_id"))
    if err:
        return err
    dept = detected_dept or draft.department
    result = course_group_draft.commit_draft(draft=draft, user=request.user, request=request, department=dept)
    matrix = course_group_draft.draft_matrix(draft)
    if result.get("applied"):
        msg = f"Committed {result['applied']} mapping(s)."
    else:
        msg = result.get("message", "Nothing pending to commit.")
    if result.get("errors"):
        msg += " Some programs were skipped: " + "; ".join(result["errors"])
    return JsonResponse({"status": "success", "message": msg, "result": result, "matrix": matrix})


def _cg_delete_draft(request, detected_dept):
    draft, err = _resolve_draft(detected_dept, request.POST.get("draft_id"))
    if err:
        return err
    # A committed mapping row is kept around on purpose (see course_group_draft
    # module docstring) so it can reappear as pending if the real data is ever
    # cleared elsewhere — so the guard here checks for OUTSTANDING checkboxes
    # (draft_matrix's pending_rows), not just draft.mappings.exists(), which
    # would now stay true forever on any draft that was ever committed.
    if course_group_draft.draft_matrix(draft)["pending_rows"]:
        return JsonResponse({
            "status": "error",
            "message": "This course group still has pending mappings — remove them (uncheck everything) or Commit first.",
        }, status=400)
    code = draft.course_code
    draft.delete()
    return JsonResponse({"status": "success", "message": f"Course group draft '{code}' deleted."})


def _cg_delete_letter(request, detected_dept):
    """Drop one lettered group (e.g. 'EDFO 111-C') from a draft."""
    draft, err = _resolve_draft(detected_dept, request.POST.get("draft_id"))
    if err:
        return err
    letter = (request.POST.get("letter") or "").strip().upper()
    if not letter:
        return JsonResponse({"status": "error", "message": "Letter is required."}, status=400)

    ok, message, had_committed = course_group_draft.delete_letter(draft=draft, letter_str=letter)
    if not ok:
        return JsonResponse({"status": "error", "message": message}, status=400)

    msg = f"Group '{draft.course_code}-{letter}' dropped."
    if had_committed:
        msg += (
            " Note: it already had committed allocations in place — those were left "
            "untouched, only removed from this draft's view."
        )
    return JsonResponse({"status": "success", "message": msg, "matrix": course_group_draft.draft_matrix(draft)})


def _cg_add_groups(request, detected_dept):
    """Append N fresh lettered groups after the draft's current highest letter."""
    draft, err = _resolve_draft(detected_dept, request.POST.get("draft_id"))
    if err:
        return err
    try:
        add_count = int(request.POST.get("add_count") or 0)
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "Number of groups to add must be a number."}, status=400)
    if add_count < 1 or add_count > 52:
        return JsonResponse(
            {"status": "error", "message": "Number of groups to add must be between 1 and 52."}, status=400
        )

    new_letters = course_group_draft.add_groups(draft=draft, add_count=add_count)
    names = ", ".join(f"{draft.course_code}-{l}" for l in new_letters)
    return JsonResponse({
        "status": "success",
        "message": f"Added {len(new_letters)} group(s): {names}.",
        "matrix": course_group_draft.draft_matrix(draft),
    })


def _cg_lecturer_choices(request, detected_dept):
    dept, err = _resolve_department(request, detected_dept)
    if err:
        return err
    return JsonResponse({"status": "success", "lecturers": course_group_draft.lecturer_choices(dept)})


def _cg_set_letter_lecturer(request, detected_dept):
    """Assign (or clear, when lecturer_id is blank) the lecturer for one
    lettered group of a draft — e.g. 'EDFO 111-A'. Applies immediately to
    any already-committed CourseAllocation rows for that letter, and is
    remembered for any future Commit too."""
    draft, err = _resolve_draft(detected_dept, request.POST.get("draft_id"))
    if err:
        return err
    letter = (request.POST.get("letter") or "").strip().upper()
    if not letter:
        return JsonResponse({"status": "error", "message": "Letter is required."}, status=400)

    lecturer_id = (request.POST.get("lecturer_id") or "").strip()
    lecturer = None
    if lecturer_id:
        lecturer = get_object_or_404(Lecturer, pk=lecturer_id)

    course_group_draft.set_letter_lecturer(draft=draft, letter_str=letter, lecturer=lecturer, user=request.user)
    msg = f"{draft.course_code}-{letter} assigned to {lecturer.display_name}." if lecturer else f"Lecturer cleared for {draft.course_code}-{letter}."
    return JsonResponse({"status": "success", "message": msg, "matrix": course_group_draft.draft_matrix(draft)})
