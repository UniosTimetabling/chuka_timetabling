from django.shortcuts import render
from django.contrib.auth.models import Group
from django.contrib.auth.decorators import login_required
# ----------------- ADMIN HOMEPAGE -----------------
@login_required
def admin_homepage(request):
    groups = Group.objects.all()  # Fetch all groups
    return render(request, "dashboard/admin_homepage.html", {
        "groups": groups
    })