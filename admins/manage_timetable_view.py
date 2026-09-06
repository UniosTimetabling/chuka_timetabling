from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User, Group
from django.db import transaction
from django.utils.text import slugify
from core.models import OrgRole


def sudo_required_json(view_func):
    """Ensure only superusers can access JSON endpoints."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_superuser:
            return JsonResponse({"status": "error", "message": "Unauthorized"}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


def normalize_username(name: str, prefix: str):
    """Generate safe username."""
    safe = slugify(name).replace("-", "_")
    if not safe or not safe[0].isalpha():
        safe = "u_" + safe
    return f"{prefix}_{safe}"


def get_director_user():
    """Return the single Director (if exists)."""
    try:
        grp = Group.objects.get(name="Director Timetable")
    except Group.DoesNotExist:
        return None
    qs = User.objects.filter(groups=grp)
    return qs.first() if qs.exists() else None


def ensure_group(name):
    g, _ = Group.objects.get_or_create(name=name)
    return g


@login_required
def manage_timetable_view(request):
    """Main page view."""
    if not request.user.is_superuser:
        return HttpResponseForbidden("Forbidden")
    return render(request, "admins/timetable_manage.html", {
        "director": get_director_user(),
        "admins": User.objects.filter(groups=ensure_group("Timetable Admins")).order_by("username"),
    })


@login_required
@transaction.atomic
@sudo_required_json
def ajax_manage_timetable(request):
    """Single AJAX endpoint for Director + Admin CRUD."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid request"}, status=400)

    action = (request.POST.get("action") or "").strip().lower()

    # ---------------- DIRECTOR ----------------
    if action == "list_director":
        d = get_director_user()
        if not d:
            return JsonResponse({"status": "success", "director": None})
        return JsonResponse({"status": "success", "director": {
            "id": d.id, "username": d.username, "email": d.email,
            "full_name": f"{d.first_name} {d.last_name}".strip(),
            "is_active": d.is_active
        }})

    if action == "create_director":
        full_name = (request.POST.get("full_name") or "").strip()
        email = (request.POST.get("email") or "").strip()
        custom_username = (request.POST.get("username") or "").strip()
        password = (request.POST.get("password") or "").strip()

        if not full_name:
            return JsonResponse({"status": "error", "message": "Full name is required"}, status=400)
        if not password:
            return JsonResponse({"status": "error", "message": "Password is required"}, status=400)
        if get_director_user():
            return JsonResponse({"status": "error", "message": "A Director already exists. Delete first."}, status=400)

        username = custom_username.lower() if custom_username else normalize_username(full_name, "director")
        if User.objects.filter(username=username).exists():
            return JsonResponse({"status": "error", "message": f"Username '{username}' is already taken."}, status=400)

        user = User.objects.create(username=username, email=email, is_active=True)
        parts = full_name.split()
        user.first_name = parts[0] if parts else ""
        user.last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
        user.set_password(password)
        user.save()
        user.groups.add(ensure_group("Director Timetable"))
        OrgRole.objects.get_or_create(user=user, title="Director Timetable")

        return JsonResponse({"status": "success", "message": "Director created", "director": {
            "id": user.id, "username": user.username, "email": user.email,
            "full_name": f"{user.first_name} {user.last_name}".strip()
        }})

    if action == "update_director":
        uid = request.POST.get("user_id")
        user = get_object_or_404(User, pk=uid)
        full_name = (request.POST.get("full_name") or "").strip()
        email = (request.POST.get("email") or "").strip()

        if full_name:
            parts = full_name.split()
            user.first_name = parts[0]
            user.last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
        user.email = email
        user.save()
        return JsonResponse({"status": "success", "message": "Director updated"})

    if action == "reset_director_password":
        uid = request.POST.get("user_id")
        password = request.POST.get("password")
        if not password:
            return JsonResponse({"status": "error", "message": "Password is required"}, status=400)
        user = get_object_or_404(User, pk=uid)
        user.set_password(password)
        user.save()
        return JsonResponse({"status": "success", "message": "Director password reset"})

    if action == "delete_director":
        uid = request.POST.get("user_id")
        user = get_object_or_404(User, pk=uid)
        user.groups.remove(ensure_group("Director Timetable"))
        user.delete()
        return JsonResponse({"status": "success", "message": "Director deleted"})

    # ---------------- ADMINS ----------------
    if action == "list_admins":
        admins = User.objects.filter(groups=ensure_group("Timetable Admins")).order_by("username")
        return JsonResponse({"status": "success", "admins": [{
            "id": a.id, "username": a.username, "email": a.email,
            "full_name": f"{a.first_name} {a.last_name}".strip(),
            "is_active": a.is_active
        } for a in admins]})

    if action == "create_admin":
        full_name = (request.POST.get("full_name") or "").strip()
        email = (request.POST.get("email") or "").strip()
        custom_username = (request.POST.get("username") or "").strip()
        password = (request.POST.get("password") or "").strip()

        if not full_name:
            return JsonResponse({"status": "error", "message": "Full name is required"}, status=400)
        if not password:
            return JsonResponse({"status": "error", "message": "Password is required"}, status=400)

        username = custom_username.lower() if custom_username else normalize_username(full_name, "timetable_admin")
        if User.objects.filter(username=username).exists():
            return JsonResponse({"status": "error", "message": f"Username '{username}' is already taken."}, status=400)

        a = User.objects.create(username=username, email=email, is_active=True)
        parts = full_name.split()
        a.first_name = parts[0] if parts else ""
        a.last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
        a.set_password(password)
        a.save()
        a.groups.add(ensure_group("Timetable Admins"))
        OrgRole.objects.get_or_create(user=a, title="Timetable Admin")

        return JsonResponse({"status": "success", "message": "Admin created", "admin": {
            "id": a.id, "username": a.username, "email": a.email,
            "full_name": f"{a.first_name} {a.last_name}".strip()
        }})

    if action == "update_admin":
        uid = request.POST.get("user_id")
        user = get_object_or_404(User, pk=uid)
        full_name = (request.POST.get("full_name") or "").strip()
        email = (request.POST.get("email") or "").strip()
        if full_name:
            parts = full_name.split()
            user.first_name = parts[0]
            user.last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
        user.email = email
        user.save()
        return JsonResponse({"status": "success", "message": "Admin updated"})

    if action == "reset_admin_password":
        uid = request.POST.get("user_id")
        password = request.POST.get("password")
        if not password:
            return JsonResponse({"status": "error", "message": "Password is required"}, status=400)
        user = get_object_or_404(User, pk=uid)
        user.set_password(password)
        user.save()
        return JsonResponse({"status": "success", "message": "Admin password reset"})

    if action == "delete_admin":
        uid = request.POST.get("user_id")
        user = get_object_or_404(User, pk=uid)
        user.groups.remove(ensure_group("Timetable Admins"))
        user.delete()
        return JsonResponse({"status": "success", "message": "Admin deleted"})

    return JsonResponse({"status": "error", "message": "Unknown action"}, status=400)
