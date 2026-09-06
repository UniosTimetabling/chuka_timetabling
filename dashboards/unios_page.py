from django.shortcuts import render
from core.models import SiteSettings, SitePhoto


def unios_page(request):
    context = {
        "site_settings": SiteSettings.get_settings(),
        "carousel_photos": SitePhoto.objects.filter(is_active=True).order_by("order", "uploaded_at"),
    }
    return render(request, "dashboard/unios_homepage.html", context)