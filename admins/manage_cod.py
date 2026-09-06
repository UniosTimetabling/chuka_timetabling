from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.models import User, Group
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
from faculty_management.models import Faculty
from department_management.models import Department
from core.models import OrgRole
from core.rbac import Role, link_department_scope

# ---------------- Superuser-only decorator ----------------
# Mirrors admins/views.py:sudo_required — kept local to avoid a circular
# import between admins.views and admins.manage_cod.
def sudo_required(view_func):
    """Restrict access to superusers."""
    return login_required(user_passes_test(lambda u: u.is_superuser)(view_func))

# Page to manage CODs
@sudo_required
def cod_management(request):
    faculties = Faculty.objects.all()
    departments = Department.objects.all()

    # Use the canonical "cod" group (Role.COD) — this is the SAME group
    # that core.rbac.ensure_default_groups() seeds on post_migrate, and the
    # same one every @allowed_roles(Role.COD, ...) check resolves to.
    # Previously this created a *second*, differently-named "COD" group,
    # so COD users created elsewhere (e.g. via the department form on
    # /sudo/departments/) never showed up in this list, and vice versa.
    cod_group, created = Group.objects.get_or_create(name=Role.COD)

    cods = User.objects.filter(groups=cod_group)
    return render(request, "admins/cod_management.html", {
        "faculties": faculties,
        "departments": departments,
        "cods": cods
    })


# Create COD + Auto COD admin
@sudo_required
@require_POST
def create_cod(request):
    username = request.POST.get("username")
    email = request.POST.get("email")
    department_id = request.POST.get("department")
    password = request.POST.get("password")

    if not username or not email or not password or not department_id:
        return JsonResponse({"status": "error", "message": "All fields are required."})

    if User.objects.filter(username=username).exists():
        return JsonResponse({"status": "error", "message": "Username already exists."})

    if User.objects.filter(email__iexact=email).exists():
        return JsonResponse({"status": "error", "message": "A user with this email address already exists."})

    try:
        department = Department.objects.get(id=department_id)
    except Department.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Selected department does not exist."})

    user = User.objects.create_user(username=username, email=email, password=password)

    # Canonical "cod" group — see cod_management() above for why.
    cod_group, created = Group.objects.get_or_create(name=Role.COD)
    user.groups.add(cod_group)

    department.leader = user
    department.save()

    # `title` used to be a fixed "COD" string, and OrgRole.title was
    # unique=True — so creating a SECOND department's COD would silently
    # steal the OrgRole away from the first one. Scoping the title by
    # department name keeps titles human-readable and unique, and the
    # explicit `department=` FK is what actually lets the system resolve
    # "which department is this COD/COD Admin responsible for" (see
    # core.rbac.resolve_user_department).
    OrgRole.objects.update_or_create(
        user=user,
        defaults={"title": f"COD - {department.name}", "department": department},
    )

    # Auto COD Admin — created without ever being asked which department it
    # administers. That's the root cause of "COD Admin can't log in / gets
    # rejected": nothing anywhere linked this account back to a department,
    # so any department-scoped view treated it as having no department at
    # all. Fixed below via link_department_scope().
    admin_username = f"{username}_admin"
    if not User.objects.filter(username=admin_username).exists():
        admin_user = User.objects.create_user(
            username=admin_username,
            email=f"{username}_admin@example.com",
            password="cod_admin@2025"
        )
        # Canonical "cod_admins" group (Role.COD_ADMIN) — previously this
        # created a separate "COD Admins" group with a different name/casing
        # than the seeded canonical group, resulting in duplicate Group rows
        # for what should be the same role.
        admin_group, _ = Group.objects.get_or_create(name=Role.COD_ADMIN)
        admin_user.groups.add(admin_group)
        OrgRole.objects.update_or_create(
            user=admin_user,
            defaults={
                "title": f"COD Admin - {department.name} ({username})",
                "department": department,
            },
        )

    # Belt-and-braces: also propagate the department scope by the
    # "<username>_admin" naming convention, in case the admin account
    # already existed (e.g. re-running create_cod after a department
    # reassignment).
    link_department_scope(user, department)

    return JsonResponse({"status": "success", "message": "COD and admin created successfully."})


# Edit COD
@sudo_required
@require_POST
def edit_cod(request, user_id):
    user = get_object_or_404(User, id=user_id)
    username = request.POST.get("username")
    email = request.POST.get("email")
    department_id = request.POST.get("department")

    if User.objects.filter(username=username).exclude(id=user.id).exists():
        return JsonResponse({"status": "error", "message": "Username already exists."})

    user.username = username
    user.email = email
    user.save()

    # Update department
    department = Department.objects.get(id=department_id)
    department.leader = user
    department.save()

    # Keep the OrgRole (and the COD Admin's scope) in sync with the new
    # department — previously this endpoint never touched OrgRole at all,
    # so editing a COD's department here left their (and their admin's)
    # department scope stale.
    OrgRole.objects.update_or_create(
        user=user,
        defaults={"title": f"COD - {department.name}", "department": department},
    )
    link_department_scope(user, department)

    return JsonResponse({"status": "success", "message": "COD updated successfully."})


# Reset COD password
@sudo_required
@require_POST
def reset_cod_password(request, user_id):
    user = get_object_or_404(User, id=user_id)
    new_password = request.POST.get("password", "defaultpassword123")
    user.set_password(new_password)
    user.save()
    return JsonResponse({"status": "success", "message": "Password reset successfully."})


# Delete COD
@sudo_required
@require_POST
def delete_cod(request, user_id):
    user = get_object_or_404(User, id=user_id)
    user.delete()
    return JsonResponse({"status": "success", "message": "COD deleted successfully."})
