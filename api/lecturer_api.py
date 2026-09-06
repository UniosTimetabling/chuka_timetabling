from django.contrib.auth.models import User
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from lecturer_portal.models import Lecturer
from department_management.models import Department
from core.rbac import allowed_roles, HOD_AND_ABOVE, Role, user_has_role
from course_allocation.detect_user_department import detect_user_department

# Roles that manage lecturers across every department. COD / COD Admin are
# department-scoped and may only create/edit/delete lecturers in their own
# department.
UNIVERSITY_WIDE_ROLES = (
    Role.DEAN, Role.DEAN_ADMIN,
    Role.DVC, Role.DVC_ADMIN,
    Role.DIRECTOR, Role.TIMETABLE_ADMIN,
    Role.SUDO,
)


def _scope_for(request):
    """
    Return (is_university_wide, user_department) for the requesting user.
    """
    user = request.user
    if user.is_superuser or user_has_role(user, *UNIVERSITY_WIDE_ROLES):
        return True, None
    return False, detect_user_department(user)


@require_http_methods(["POST"])
@allowed_roles(*HOD_AND_ABOVE)
def lecturer_api(request):
    """Handle AJAX CRUD for Lecturer.

    Previously this endpoint only required @login_required and did no
    department checks at all, so any authenticated user — including a COD
    from a different department — could create, edit, or delete any
    lecturer record university-wide. Now:
      * Only HOD-and-above roles may call this at all.
      * COD / COD Admin (department-scoped) can only create a lecturer in
        their own department, and can only edit/delete lecturers that
        already belong to their own department.
      * Dean/DVC/Director/Timetable Admin/Sudo are unrestricted, as before.
    """
    action = request.POST.get("action")
    is_university_wide, user_department = _scope_for(request)

    if not is_university_wide and not user_department:
        return JsonResponse(
            {"status": "error", "message": "No department associated with your account. Please contact administrator."},
            status=403,
        )

    # CREATE
    if action == "create":
        payroll_number = request.POST.get("payroll_number")
        name = request.POST.get("name")
        email = request.POST.get("email")
        designation = request.POST.get("designation")

        if is_university_wide:
            department_id = request.POST.get("department") or None
            department = Department.objects.filter(pk=department_id).first()
        else:
            # Department-scoped COD/COD Admin: always their own department,
            # regardless of what was posted from the form.
            department = user_department

        user, created = User.objects.get_or_create(
            username=email,
            defaults={
                "email": email,
                "first_name": name.split()[0] if name else "",
                "last_name": " ".join(name.split()[1:]) if len(name.split()) > 1 else "",
            }
        )
        if created:
            user.set_password(payroll_number)
            user.save()

        lec = Lecturer.objects.create(
            payroll_number=payroll_number,
            name=name,
            email=email,
            designation=designation,
            user=user,
            department=department,
        )

        return JsonResponse({
            "status": "success",
            "lecturer": {
                "id": lec.id,
                "payroll_number": lec.payroll_number,
                "name": lec.name,
                "email": lec.email,
                "designation": lec.designation,
                "department": lec.department.name if lec.department else None,
            }
        })

    # UPDATE
    if action == "update":
        lec_id = request.POST.get("id")
        lec = get_object_or_404(Lecturer, pk=lec_id)

        if not is_university_wide and lec.department_id != user_department.id:
            return JsonResponse(
                {"status": "error", "message": "You can only edit lecturers in your own department."},
                status=403,
            )

        if is_university_wide:
            department_id = request.POST.get("department") or None
            department = Department.objects.filter(pk=department_id).first()
        else:
            # Department-scoped users cannot move a lecturer to another
            # department via this form.
            department = user_department

        lec.payroll_number = request.POST.get("payroll_number")
        lec.name = request.POST.get("name")
        lec.email = request.POST.get("email")
        lec.designation = request.POST.get("designation")
        lec.department = department
        lec.save()

        if lec.user:
            lec.user.username = lec.email
            lec.user.email = lec.email
            lec.user.first_name = lec.name.split()[0] if lec.name else ""
            lec.user.last_name = " ".join(lec.name.split()[1:]) if len(lec.name.split()) > 1 else ""
            lec.user.save()

        return JsonResponse({"status": "success"})

    # DELETE
    if action == "delete":
        lec_id = request.POST.get("id")
        lec = get_object_or_404(Lecturer, pk=lec_id)

        if not is_university_wide and lec.department_id != user_department.id:
            return JsonResponse(
                {"status": "error", "message": "You can only delete lecturers in your own department."},
                status=403,
            )

        if lec.user:
            lec.user.delete()
        lec.delete()
        return JsonResponse({"status": "success"})

    return JsonResponse({"status": "error", "message": "Unknown action"})
