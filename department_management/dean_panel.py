from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.contrib.auth.models import User, Group
from django.db import transaction, IntegrityError

from faculty_management.models import Faculty
from department_management.models import  Department
from core.models import  OrgRole
from core.group_required import group_required
from core.rbac import Role, link_department_scope


# --- helpers ---
def normalize_name(name: str) -> str:
    """Convert to safe username format: spaces -> _, lowercase."""
    return name.strip().replace(" ", "_").lower()


def create_cod_and_admin(department_name: str, department: Department = None):
    """
    Create COD and COD Admin accounts for a department.
    Only set password when user is newly created.

    `department` (the actual Department instance, when available) is used
    to scope the accounts' OrgRole so they can be resolved back to their
    department later — see core.rbac.resolve_user_department(). Callers
    that don't have the Department instance yet (e.g. creating it in the
    same request) can pass department_name only and call
    core.rbac.link_department_scope(cod_user, dept) themselves once the
    Department has been saved.
    """
    base_name = normalize_name(department_name)

    # COD account
    cod_username = f"cod_{base_name}"
    cod_password = f"{cod_username}@2025"
    cod_user, created = User.objects.get_or_create(username=cod_username)

    if created:
        cod_user.set_password(cod_password)
        cod_user.first_name = "COD"
        cod_user.last_name = department_name
        cod_user.is_active = True
        cod_user.save()
    else:
        cod_password = None  # don't reset password if user already exists

    # `title` used to be a fixed "COD" string with OrgRole.title unique=True,
    # so a second department's COD would silently steal this OrgRole row.
    # Scoping by department name keeps it unique and readable.
    OrgRole.objects.update_or_create(
        user=cod_user,
        defaults={
            "title": f"COD - {department_name}",
            "department": department,
        },
    )

    # Canonical "cod" group (Role.COD) — matches the group seeded by
    # core.rbac.ensure_default_groups() and used by every @allowed_roles
    # check. Previously this created a separate "COD" group with different
    # casing than the canonical one, so COD accounts created here didn't
    # show up as "COD"s anywhere else in the system (e.g. /sudo/cods/), and
    # vice versa — the reported "creating a new group" symptom.
    cod_group, _ = Group.objects.get_or_create(name=Role.COD)
    if cod_group not in cod_user.groups.all():
        cod_user.groups.add(cod_group)

    # COD Admin account
    admin_username = f"{cod_username}_admin"
    admin_password = f"{admin_username}@2025"
    admin_user, created_admin = User.objects.get_or_create(username=admin_username)

    if created_admin:
        admin_user.set_password(admin_password)
        admin_user.first_name = "COD Admin"
        admin_user.last_name = department_name
        admin_user.is_active = True
        admin_user.save()
    else:
        admin_password = None  # don’t reset password if already exists

    # COD Admin never had anywhere to be assigned a department — this is
    # the root cause of COD Admin accounts being rejected by
    # department-scoped views after login ("No department associated with
    # your account."). Fixed by setting `department` directly here.
    OrgRole.objects.update_or_create(
        user=admin_user,
        defaults={
            "title": f"COD Admin - {department_name} ({admin_username})",
            "department": department,
        },
    )

    # Canonical "cod_admins" group (Role.COD_ADMIN) — see cod_group above.
    cod_admin_group, _ = Group.objects.get_or_create(name=Role.COD_ADMIN)
    if cod_admin_group not in admin_user.groups.all():
        admin_user.groups.add(cod_admin_group)

    return cod_user, admin_user, cod_password, admin_password


def handle_leader_option(option, department_name):
    """Handle leader selection when creating/updating a department."""
    if option == "none":
        return None
    try:
        user_id = int(option)
        return User.objects.get(id=user_id)
    except (User.DoesNotExist, ValueError, TypeError):
        return None


# --- main view ---
@allowed_roles(Role.DEAN, Role.DEAN_ADMIN, Role.SUDO)
@transaction.atomic
def dean_panel(request):
    # Get dean’s assigned faculty (if any)
    try:
        dean_faculty = Faculty.objects.get(leader=request.user)
    except Faculty.DoesNotExist:
        dean_faculty = None

    if not dean_faculty:
        return render(request, "department_management/dean_panel.html", {
            "faculties": [],
            "departments": [],
            "users": User.objects.none(),
            "error": "You are not assigned as leader of any faculty."
        })

    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        action = request.POST.get("action")

        # -------- CREATE --------
        if action == "create":
            try:
                name = request.POST.get("name", "").strip()
                desc = request.POST.get("description", "").strip()
                faculty_id = request.POST.get("faculty_id")
                leader_option = request.POST.get("leader_option")

                if not name or not faculty_id:
                    return JsonResponse({
                        "status": "error",
                        "message": "Name and Faculty are required.",
                        "popup": {"type": "error", "message": "Name and Faculty are required."}
                    }, status=400)

                # Security check: only allow dean’s faculty
                faculty = get_object_or_404(Faculty, id=faculty_id)
                if faculty != dean_faculty:
                    return JsonResponse({
                        "status": "error",
                        "message": "You can only manage your own faculty.",
                        "popup": {"type": "error", "message": "You can only manage your own faculty."}
                    }, status=403)

                cod_user = admin_user = cod_pass = admin_pass = None

                if leader_option == "new":
                    cod_user, admin_user, cod_pass, admin_pass = create_cod_and_admin(name)
                    leader = cod_user
                else:
                    leader = handle_leader_option(leader_option, name)

                dept = Department.objects.create(
                    name=name,
                    description=desc,
                    faculty=faculty,
                    leader=leader
                )

                # Now that the Department has an id, scope the COD (and, via
                # the "<username>_admin" convention, the COD Admin) OrgRole
                # to it. Without this, the COD Admin account has no way to
                # be resolved back to this department later.
                if cod_user:
                    link_department_scope(cod_user, dept)

                return JsonResponse({
                    "status": "success",
                    "message": f"Department '{dept.name}' created successfully.",
                    "popup": {"type": "success", "message": f"Department '{dept.name}' created successfully."},
                    "department": {
                        "id": dept.id,
                        "name": dept.name,
                        "description": dept.description,
                        "faculty": dept.faculty.name if dept.faculty else None,
                        "leader": dept.leader.username if dept.leader else None
                    },
                    "credentials": {
                        "cod_username": cod_user.username if cod_user else None,
                        "cod_password": cod_pass,
                        "cod_admin_username": admin_user.username if admin_user else None,
                        "cod_admin_password": admin_pass,
                    }
                })
            except IntegrityError as e:
                return JsonResponse({
                    "status": "error",
                    "message": f"Database error: {e}",
                    "popup": {"type": "error", "message": f"Database error: {e}"}
                }, status=400)
            except Exception as e:
                return JsonResponse({
                    "status": "error",
                    "message": f"Unexpected error: {e}",
                    "popup": {"type": "error", "message": f"Unexpected error: {e}"}
                }, status=500)

        # -------- EDIT --------
        elif action == "edit":
            try:
                dept_id = request.POST.get("id")
                dept = get_object_or_404(Department, id=dept_id, faculty=dean_faculty)

                name = request.POST.get("name", "").strip()
                if not name:
                    return JsonResponse({
                        "status": "error",
                        "message": "Department name cannot be empty.",
                        "popup": {"type": "error", "message": "Department name cannot be empty."}
                    }, status=400)

                dept.name = name
                dept.description = request.POST.get("description", "").strip()
                faculty_id = request.POST.get("faculty_id")

                faculty = get_object_or_404(Faculty, id=faculty_id)
                if faculty != dean_faculty:
                    return JsonResponse({
                        "status": "error",
                        "message": "You can only assign to your own faculty.",
                        "popup": {"type": "error", "message": "You can only assign to your own faculty."}
                    }, status=403)

                dept.faculty = faculty

                leader_option = request.POST.get("leader_option")
                if leader_option == "new":
                    cod_user, _, _, _ = create_cod_and_admin(dept.name, department=dept)
                    dept.leader = cod_user
                else:
                    dept.leader = handle_leader_option(leader_option, dept.name)

                dept.save()

                if leader_option == "new" and cod_user:
                    link_department_scope(cod_user, dept)

                return JsonResponse({
                    "status": "success",
                    "message": f"Department '{dept.name}' updated successfully.",
                    "popup": {"type": "success", "message": f"Department '{dept.name}' updated successfully."},
                    "department": {
                        "id": dept.id,
                        "name": dept.name,
                        "description": dept.description,
                        "faculty": dept.faculty.name if dept.faculty else None,
                        "leader": dept.leader.username if dept.leader else None
                    }
                })
            except IntegrityError as e:
                return JsonResponse({
                    "status": "error",
                    "message": f"Database error: {e}",
                    "popup": {"type": "error", "message": f"Database error: {e}"}
                }, status=400)
            except Exception as e:
                return JsonResponse({
                    "status": "error",
                    "message": f"Unexpected error: {e}",
                    "popup": {"type": "error", "message": f"Unexpected error: {e}"}
                }, status=500)

        # -------- DELETE --------
        elif action == "delete":
            try:
                dept_id = request.POST.get("id")
                dept = get_object_or_404(Department, id=dept_id, faculty=dean_faculty)
                dept_name = dept.name
                dept.delete()
                return JsonResponse({
                    "status": "success",
                    "message": f"Department '{dept_name}' deleted successfully.",
                    "popup": {"type": "success", "message": f"Department '{dept_name}' deleted successfully."},
                    "id": dept_id
                })
            except Exception as e:
                return JsonResponse({
                    "status": "error",
                    "message": f"Delete failed: {e}",
                    "popup": {"type": "error", "message": f"Delete failed: {e}"}
                }, status=500)

        return JsonResponse({
            "status": "error",
            "message": "Invalid action.",
            "popup": {"type": "error", "message": "Invalid action."}
        }, status=400)

    # -------- GET request → render page --------
    faculties = [dean_faculty]
    departments = Department.objects.filter(faculty=dean_faculty).select_related("faculty", "leader")
    users = User.objects.all()

    return render(request, "department_management/dean_panel.html", {
        "faculties": faculties,
        "departments": departments,
        "users": users
    })
