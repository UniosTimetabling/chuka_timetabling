from django.shortcuts import render, get_object_or_404
from django.contrib.auth.models import User, Group
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from faculty_management.models import Faculty
from core.models import OrgRole
from core.rbac import Role, ROLE_ALIASES


@login_required
def reset_dean_credentials_view(request):
    """
    Dean/Admin credential management:
    - View all dean and dean admin users
    - Inline edit usernames
    - Reset passwords
    - Create new dean/admin accounts linked to Faculty + OrgRole
    """
    if request.method == "GET":
        deans = User.objects.filter(groups__name__in=[Role.DEAN, Role.DEAN_ADMIN]).distinct()
        faculties = Faculty.objects.all()
        return render(
            request,
            "admins/reset_dean_credentials.html",
            {"deans": deans, "faculties": faculties},
        )

    # --- AJAX Handling ---
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        action = request.POST.get("action")

        # ---------- CREATE NEW DEAN ----------
        if action == "create_dean":
            username = request.POST.get("username")
            email = request.POST.get("email")
            password = request.POST.get("password")
            group_name = request.POST.get("group")
            faculty_id = request.POST.get("faculty_id")

            if not all([username, email, password, group_name, faculty_id]):
                return JsonResponse({"success": False, "error": "All fields are required."})

            if User.objects.filter(Q(username=username) | Q(email=email)).exists():
                return JsonResponse({"success": False, "error": "Username or email already exists."})

            # Resolve the posted label ("Dean" / "Dean Admins") to the
            # canonical, actually-seeded group name (e.g. "dean" / "dean_admins").
            canonical_group_name = ROLE_ALIASES.get(
                group_name.strip().lower(), group_name.strip().lower()
            )
            is_dean = canonical_group_name == Role.DEAN

            faculty = get_object_or_404(Faculty, id=faculty_id)
            user = User.objects.create_user(username=username, email=email, password=password)
            group, _ = Group.objects.get_or_create(name=canonical_group_name)
            user.groups.add(group)
            user.save()

            # --- Link user to OrgRole ---
            # `role_title` used to be a fixed "Dean" / "Dean Admin" string,
            # and OrgRole.title was unique=True. That meant a SECOND
            # faculty's Dean (or a second Dean Admin) would silently steal
            # the OrgRole row away from the first one, and — because
            # nothing recorded which faculty a Dean Admin belonged to —
            # Dean Admins had no way to be resolved back to a faculty at
            # all. Scoping the title by faculty (and username, for Dean
            # Admins, since one faculty can have several admins) keeps
            # titles unique; the explicit `faculty=` FK is what actually
            # lets department/faculty-scoped views resolve this account
            # (see core.rbac.resolve_user_faculty).
            role_title = (
                f"Dean - {faculty.name}"
                if is_dean
                else f"Dean Admin - {faculty.name} ({username})"
            )
            OrgRole.objects.update_or_create(
                user=user, defaults={"title": role_title, "faculty": faculty}
            )

            # --- Link Dean to Faculty ---
            if is_dean:
                faculty.leader = user
                faculty.save()

            return JsonResponse(
                {
                    "success": True,
                    "message": f"{group_name} account created and linked to {faculty.name}.",
                }
            )

        # ---------- RESET PASSWORD ----------
        elif action == "reset_password":
            user_id = request.POST.get("user_id")
            new_password = request.POST.get("new_password")
            user = get_object_or_404(User, id=user_id)
            if not new_password:
                return JsonResponse({"success": False, "error": "Password required."})
            user.set_password(new_password)
            user.save()
            return JsonResponse({"success": True, "message": f"Password reset for {user.username}."})

        # ---------- RESET USERNAME ----------
        elif action == "reset_username":
            user_id = request.POST.get("user_id")
            new_username = request.POST.get("new_username")
            user = get_object_or_404(User, id=user_id)
            if not new_username:
                return JsonResponse({"success": False, "error": "Username required."})
            if User.objects.exclude(id=user.id).filter(username=new_username).exists():
                return JsonResponse({"success": False, "error": "Username already exists."})
            user.username = new_username
            user.save()
            return JsonResponse({"success": True, "message": f"Username changed to {new_username}."})

        return JsonResponse({"success": False, "error": "Unknown action."})

    return JsonResponse({"success": False, "error": "Invalid request."})
