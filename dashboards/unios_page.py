from django.shortcuts import redirect, render
from core.models import SiteSettings, SitePhoto
from core.rbac import resolve_dashboard_url_name


def unios_page(request):
    """
    Public landing page.

    Logged-in visitors don't need the marketing/public homepage — send them
    straight to the dashboard that matches their role, using the same
    role -> dashboard mapping the login flow uses (core.rbac), so a bookmark
    or link to "/" always lands an authenticated user on their own workspace
    instead of the public page.
    """
    if request.user.is_authenticated:
        return redirect(resolve_dashboard_url_name(request.user))

    context = {
        "site_settings": SiteSettings.get_settings(),
        "carousel_photos": SitePhoto.objects.filter(is_active=True).order_by("order", "uploaded_at"),
    }
    return render(request, "dashboard/unios_homepage.html", context)