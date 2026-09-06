from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role, user_has_role
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.db.models import Q

from program_management.models import Program, ProgramCourse
from lecturer_portal.models import Lecturer
from course_allocation.models import LabAllocation
from room_management.models import LabVenue
from department_management.models import Department
from special_requests.models import SpecialRequest
from special_requests.panel_actions import SR_ACTIONS, handle_sr_action

# ── Re-use the same department-detection helper that cod_panel uses ──────────
# Import directly if it lives in a shared module; otherwise copy the logic here.
try:
    from course_management.cod_panel import detect_user_department
except ImportError:
    # Fallback: inline copy so this view works even if the import path differs.
    from typing import Optional
    from django.contrib.auth.models import User

    def detect_user_department(user: User) -> Optional[Department]:
        """
        Try several heuristics to find the department associated with the
        logged-in user.  Mirrors cod_panel.detect_user_department exactly.
        Priority: OrgRole.department (authoritative) → Department leader →
        Lecturer profile dept → OrgRole title (legacy).
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

        try:
            lect = Lecturer.objects.filter(user=user).first()
            if lect and lect.department:
                return lect.department
        except Exception:
            pass

        try:
            lect = Lecturer.objects.filter(
                email__iexact=(user.email or "")
            ).first()
            if lect and lect.department:
                return lect.department
        except Exception:
            pass

        try:
            org = getattr(user, "org_role", None)
            if org and "COD" in org.title.upper():
                parts = org.title.split("-", 1)
                if len(parts) > 1:
                    dept_name = parts[1].strip()
                    return Department.objects.filter(
                        name__icontains=dept_name
                    ).first()
        except Exception:
            pass

        return None


def _dept_error(message="No department associated with your account."):
    """Shorthand for a 403 JSON response when department cannot be resolved."""
    return JsonResponse({"status": "error", "message": message}, status=403)


# ── Lab-allocation queryset scoped to a department ───────────────────────────
def _lab_qs_for_dept(dept):
    """
    Return LabAllocations whose primary program_course belongs to a program
    in the given department.  No cross-department leakage.
    """
    return (
        LabAllocation.objects
        .select_related(
            "program_course",
            "program_course__program",
            "program_course__program__department",
            "lecturer",
        )
        .prefetch_related("venues", "additional_courses")
        .filter(program_course__program__department=dept)
        .order_by(
            "program_course__program__name",
            "program_course__year",
            "program_course__semester",
            "program_course__course_code",
        )
    )


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def lab_allocations(request):
    """Main lab allocations page + AJAX actions — scoped to the user's department."""

    dept = detect_user_department(request.user)

    # ── POST / AJAX ──────────────────────────────────────────────────────────
    if (
        request.method == "POST"
        and request.headers.get("x-requested-with") == "XMLHttpRequest"
    ):
        # Every write action requires a resolved department.
        if dept is None:
            return _dept_error()

        action = request.POST.get("action")

        # ── SR (Special Request) actions — shared handler ──────────────────
        if action in SR_ACTIONS:
            return handle_sr_action(
                request, action,
                dept=dept,
                allocation_model=LabAllocation,
                dept_scoped_qs=_lab_qs_for_dept,
                panel=SpecialRequest.PANEL_LAB,
            )

        # ── Create / Update ──────────────────────────────────────────────────
        if action == "create_lab":
            lab_id            = request.POST.get("id")
            program_course_id = request.POST.get("program_course_id")
            additional_ids    = request.POST.getlist("additional_course_ids[]")
            venue_ids         = request.POST.getlist("venue_ids[]")
            lecturer_id       = request.POST.get("lecturer_id") or None
            num_students      = request.POST.get("number_of_students") or 0
            is_workshop       = request.POST.get("is_workshop_course") == "true"

            if not program_course_id or not venue_ids:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": "Primary course and at least one venue are required.",
                    }
                )

            # Security: ensure the chosen program_course belongs to the user's dept.
            program_course = get_object_or_404(
                ProgramCourse,
                pk=program_course_id,
                program__department=dept,
            )

            lecturer = (
                Lecturer.objects.filter(pk=lecturer_id).first()
                if lecturer_id
                else None
            )
            venues = LabVenue.objects.filter(pk__in=venue_ids)

            # Additional courses must also belong to the user's department.
            additional_courses = ProgramCourse.objects.filter(
                pk__in=additional_ids,
                program__department=dept,
            )

            if lab_id:
                # Edit: verify the allocation belongs to this dept before touching it.
                allocation = get_object_or_404(
                    LabAllocation,
                    pk=lab_id,
                    program_course__program__department=dept,
                )
                allocation.program_course     = program_course
                allocation.lecturer           = lecturer
                allocation.number_of_students = int(num_students)
                allocation.is_workshop_course = is_workshop
                allocation.save()
            else:
                allocation = LabAllocation.objects.create(
                    program_course     = program_course,
                    lecturer           = lecturer,
                    number_of_students = int(num_students),
                    is_workshop_course = is_workshop,
                )

            allocation.venues.set(venues)
            allocation.additional_courses.set(additional_courses)

            return JsonResponse(
                {
                    "status":        "success",
                    "id":            allocation.id,
                    "course_codes":  allocation.all_course_codes(),
                    "program_name":  program_course.program.name,
                    "venue_codes":   ", ".join(venues.values_list("code", flat=True)),
                    "lecturer_name": lecturer.display_name if lecturer else "",
                    "num_students":  allocation.number_of_students,
                    "is_workshop":   allocation.is_workshop_course,
                }
            )

        # ── Detail (for edit form) ────────────────────────────────────────────
        elif action == "lab_detail":
            lab_id = request.POST.get("id")
            # Scope to this department so one COD cannot peek at another's data.
            allocation = get_object_or_404(
                LabAllocation.objects.prefetch_related("venues", "additional_courses"),
                pk=lab_id,
                program_course__program__department=dept,
            )
            return JsonResponse(
                {
                    "status": "success",
                    "lab": {
                        "id":                    allocation.id,
                        "program_id":            allocation.program_course.program.id,
                        "program_course_id":     allocation.program_course.id,
                        "additional_course_ids": list(
                            allocation.additional_courses.values_list("id", flat=True)
                        ),
                        "venue_ids": list(
                            allocation.venues.values_list("id", flat=True)
                        ),
                        "lecturer_id":         allocation.lecturer.id
                            if allocation.lecturer
                            else None,
                        "number_of_students":  allocation.number_of_students,
                        "is_workshop_course":  allocation.is_workshop_course,
                    },
                }
            )

        # ── Delete ────────────────────────────────────────────────────────────
        elif action == "delete_lab":
            lab_id = request.POST.get("id")
            # Scope delete to the user's own department.
            allocation = get_object_or_404(
                LabAllocation,
                pk=lab_id,
                program_course__program__department=dept,
            )
            allocation.delete()
            return JsonResponse({"status": "success"})

        return JsonResponse({"status": "error", "message": "Invalid action"})

    # ── GET ───────────────────────────────────────────────────────────────────
    if dept is not None:
        lab_allocations_qs = _lab_qs_for_dept(dept)

        # Courses: only programs that belong to this department.
        courses  = ProgramCourse.objects.select_related("program").filter(
            program__department=dept
        )
        programs = Program.objects.filter(department=dept)

        # Lecturers: show all but put own-dept lecturers first (mirrors cod_panel).
        from django.db.models import Case, When, Value, IntegerField
        lecturers = Lecturer.objects.all().annotate(
            is_dept=Case(
                When(department=dept, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        ).order_by("-is_dept", "name")

    else:
        lab_allocations_qs = LabAllocation.objects.none()
        courses            = ProgramCourse.objects.none()
        programs           = Program.objects.none()
        lecturers          = Lecturer.objects.none()

    context = {
        "programs":        programs,
        "courses":         courses,
        "venues":          LabVenue.objects.all(),   # venues are not dept-scoped
        "lecturers":       lecturers,
        "lab_allocations": lab_allocations_qs,
        "detected_dept":   dept,                     # lets the template show dept name / warnings
        "is_lab_admin":    request.user.is_superuser or user_has_role(request.user, Role.SUDO),
        "all_departments": Department.objects.all().order_by("name"),
    }
    return render(request, "course_allocation/lab_allocations.html", context)