"""
university_timetable_system/urls.py
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

import core
from core import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', views.health_check, name='health_check'),
    path("", include("core.urls")),
    path("", include("admins.urls")),
    path("", include("api.urls")),
    path("", include("classreps.urls")),
    path("", include("course_allocation.urls")),
    path("", include("allocation_reports.urls")),
    path("", include("course_management.urls")),
    path("", include("dashboards.urls")),
    path("", include("department_management.urls")),
    path("", include("export_import.urls")),
    path("", include("faculty_management.urls")),
    path("", include("feedback.urls")),
    #path("", include("help_system.urls")),
    path("", include("lecturer_portal.urls")),
    path("", include("mess.urls")),
    path("", include("mobile_api.urls")),
    path("", include("notifications.urls")),
    path("", include("program_management.urls")),
    path("", include("room_management.urls")),
    path("", include("meeting_venues.urls")),
    path("", include("timetable.urls")),
    path("odel/", include("odel_system.urls")),
    path("", include("campuses_timetable.urls")),
    path("documentation/", include("documentation.urls")),
    # ── Resit Timetabling ─────────────────────────────────
    path("", include("resits_timetabling.urls")),

    # Backup, Disaster Recovery & Audit/Undo
    path("backup/", include("backup_system.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# GLOBAL ERROR HANDLERS
handler404 = views.universal_error
handler500 = views.universal_error
handler403 = views.universal_error

# In development, also serve static files via Django (WhiteNoise handles production)
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])