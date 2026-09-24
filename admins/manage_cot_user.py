from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from admins.forms import CotUserForm


def manage_cot_user(request, user_id=None):
    """Handles both creating and editing COT users"""
    if user_id:
        user_instance = get_object_or_404(User, pk=user_id)
        initial = {
            "username": user_instance.username,
            "email": user_instance.email,
            "department": getattr(user_instance.cot_profile, "department_id", None),
            "_user_instance": user_instance,  # passed to form for uniqueness checks
        }
        form = CotUserForm(request.POST or None, initial=initial)
    else:
        user_instance = None
        form = CotUserForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            form.save(user_instance=user_instance)
            if user_instance:
                messages.success(request, "✅ COT User updated successfully!")
            else:
                messages.success(request, "✅ COT User created successfully!")
            return redirect("manage_cot_user")  # reload page
        else:
            messages.error(request, "⚠️ Please correct the errors below.")

    # List existing COT users for quick edit links
    from core.models import CotUserProfile
    cot_users = CotUserProfile.objects.select_related("user", "department")

    return render(
        request,
        "admins/create_cot_user.html",
        {"form": form, "cot_users": cot_users, "editing": bool(user_instance)},
    )