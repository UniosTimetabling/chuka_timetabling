from django.shortcuts import render
from core.models import SiteSettings, SitePhoto


def staffportal(request):
    context = {
        "site_settings": SiteSettings.get_settings(),
        "carousel_photos": SitePhoto.objects.filter(is_active=True).order_by("order", "uploaded_at"),
    }
    return render(request, "dashboard/staff_portal.html", context)