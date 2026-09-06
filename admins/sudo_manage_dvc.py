# TT_APP/views.py
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.models import User, Group
from django.db import transaction
from django.utils.text import slugify

from core.models import OrgRole
from faculty_management.models import Faculty
from core.rbac import Role


# --- superuser only decorator ---
def sudo_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            # Redirect page loads to login; return JSON for AJAX/POST requests
            is_ajax = (
                request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or request.headers.get('Accept', '').startswith('application/json')
                or request.method == 'POST'
            )
            if is_ajax:
                return JsonResponse({"status": "error", "message": "Not authenticated"}, status=403)
            from django.conf import settings
            login_url = getattr(settings, 'LOGIN_URL', '/accounts/login/')
            return redirect(f"{login_url}?next={request.path}")
        if not request.user.is_superuser:
            is_ajax = (
                request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or request.headers.get('Accept', '').startswith('application/json')
                or request.method == 'POST'
            )
            if is_ajax:
                return JsonResponse({"status": "error", "message": "Unauthorized"}, status=403)
            return redirect('/')
        return view_func(request, *args, **kwargs)
    return wrapper


# --- helpers ---
def normalize_username(value: str) -> str:
    safe = slugify(value).replace("-", "_")
    if not safe or not safe[0].isalpha():
        safe = "u_" + safe
    return f"dvc_{safe}"


def create_or_update_orgrole(user, title):
    try:
        OrgRole.objects.get_or_create(user=user, title=title)
    except Exception:
        pass


# --- page ---
@sudo_required
def manage_dvc_page(request):
    faculties = Faculty.objects.all()[:200]
    return render(request, "admins/sudo_manage_dvc.html", {"faculties": faculties})


# --- ajax crud ---
@sudo_required
@transaction.atomic
def ajax_manage_dvc(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid request"}, status=400)

    action = (request.POST.get("action") or "").strip().lower()

    # --------- LIST ----------
    if action == "list":
        qs = User.objects.filter(
            groups__name__in=[Role.DVC, Role.DVC_ADMIN]
        ).distinct().order_by("username")
        data = []
        for u in qs:
            groups = [g.name for g in u.groups.all()]
            data.append({
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "full_name": f"{u.first_name} {u.last_name}".strip(),
                "is_active": u.is_active,
                "groups": groups,
            })
        return JsonResponse({"status": "success", "results": data})

    # --------- CREATE DVC ----------
    if action == "create_dvc":
        full_name = (request.POST.get("full_name") or "").strip()
        email = (request.POST.get("email") or "").strip()
        custom_username = (request.POST.get("username") or "").strip()
        password = (request.POST.get("password") or "").strip()

        if not full_name:
            return JsonResponse({"status": "error", "message": "Full name required"}, status=400)
        if not password:
            return JsonResponse({"status": "error", "message": "Password required"}, status=400)

        existing_dvc = User.objects.filter(groups__name=Role.DVC).first()
        if existing_dvc:
            return JsonResponse({
                "status": "error",
                "message": f"DVC already exists: {existing_dvc.username}. Please edit or delete first."
            }, status=400)

        username = normalize_username(full_name) if not custom_username else custom_username.lower()
        if User.objects.filter(username=username).exists():
            return JsonResponse({"status": "error", "message": "Username already taken"}, status=400)

        if email and User.objects.filter(email__iexact=email).exists():
            return JsonResponse({"status": "error", "message": "A user with this email address already exists."}, status=400)

        user = User.objects.create(username=username, email=email, is_active=True)
        parts = full_name.split()
        user.first_name = parts[0] if parts else ""
        user.last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
        user.set_password(password)
        user.save()

        dvc_group, _ = Group.objects.get_or_create(name=Role.DVC)
        user.groups.add(dvc_group)
        create_or_update_orgrole(user, "DVC")

        return JsonResponse({
            "status": "success",
            "message": "DVC created",
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "full_name": f"{user.first_name} {user.last_name}".strip(),
            }
        })

    # --------- CREATE ADMIN ----------
    if action == "create_admin":
        dvc = User.objects.filter(groups__name=Role.DVC).first()
        if not dvc:
            return JsonResponse({"status": "error", "message": "No DVC exists"}, status=400)

        email = (request.POST.get("email") or "").strip()
        password = (request.POST.get("password") or "").strip()
        if not password:
            return JsonResponse({"status": "error", "message": "Password required"}, status=400)

        # Check email uniqueness for admin
        if email and User.objects.filter(email__iexact=email).exists():
            return JsonResponse({"status": "error", "message": "A user with this email address already exists."}, status=400)

        # Default username pattern: dvc_admin, dvc_admin1, dvc_admin2, ...
        base_username = "dvc_admin"
        username = base_username
        i = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}{i}"
            i += 1

        admin = User.objects.create(username=username, email=email, is_active=True)
        admin.first_name, admin.last_name = dvc.first_name, dvc.last_name
        admin.set_password(password)
        admin.save()

        admin_group, _ = Group.objects.get_or_create(name=Role.DVC_ADMIN)
        admin.groups.add(admin_group)
        create_or_update_orgrole(admin, "DVC Admin")

        return JsonResponse({
            "status": "success",
            "message": "Admin created",
            "admin": {
                "id": admin.id,
                "username": admin.username,
                "email": admin.email,
            }
        })

    # --------- EDIT ----------
    if action == "edit":
        uid = request.POST.get("user_id")
        user = get_object_or_404(User, pk=uid)
        full_name = (request.POST.get("full_name") or "").strip()
        email = (request.POST.get("email") or "").strip()
        is_active = request.POST.get("is_active")
        if full_name:
            parts = full_name.split()
            user.first_name = parts[0] if parts else ""
            user.last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
        if email:
            user.email = email
        if is_active is not None:
            user.is_active = is_active in ("1", "true", "on")
        user.save()
        return JsonResponse({"status": "success", "message": "Updated"})

    # --------- RESET PASSWORD ----------
    if action == "reset_password":
        uid = request.POST.get("user_id")
        new_password = (request.POST.get("new_password") or "").strip()
        if not new_password:
            return JsonResponse({"status": "error", "message": "Password required"}, status=400)
        user = get_object_or_404(User, pk=uid)
        user.set_password(new_password)
        user.save()
        return JsonResponse({"status": "success", "message": "Password updated"})

    # --------- DELETE ----------
    if action == "delete":
        uid = request.POST.get("user_id")
        user = get_object_or_404(User, pk=uid)
        user.delete()
        return JsonResponse({"status": "success", "message": "Deleted"})

    return JsonResponse({"status": "error", "message": "Unknown action"}, status=400)
