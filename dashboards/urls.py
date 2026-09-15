from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from . import (
    analysis_dashboard, studentportal, timetable_view, staffportal,
    unios_page, mainportal, cot_exam_timetable, autoscheduler_home, views,
    user_dashboard, admin_homepage, timetable_dashboard_view,
    exam_autoscheduler_home, lab_timetable_panel, venues_panel,
    lab_exam_timetable_panel, published_timetables, utility_dashboard, mytimetable
)

urlpatterns = [
    path("published/timetables/",  published_timetables.published_timetables_view, name="published_timetables"),
    path("analysis/",              analysis_dashboard.analysis_dashboard,           name="analysis_dashboard"),
    path("portal/",                studentportal.studentportal,                     name="student_portal"),
    path("my-timetable/",          mytimetable.mytimetable,                         name="my_timetable"),
    path("student/portal/",        timetable_view.timetable_view,                  name="view_timetable"),
    path("timetable/<int:program_id>/<str:timetable_type>/", timetable_view.timetable_view, name="timetable_data"),
    path("staff/portal/",          staffportal.staffportal,                         name="staff_portal"),
    path("",                       unios_page.unios_page,                           name="homepage"),
    path("main/portal/",           mainportal.mainportal,                           name="main_portal_"),
    path("dashboard/",             user_dashboard.user_dashboard,                   name="user_dashboard"),
    path("portal/admin/",          admin_homepage.admin_homepage,                   name="admin-index"),
    path("timetable/dashboard/",   timetable_dashboard_view.timetable_dashboard_view, name="timetable_dashboard"),
    path("exam-autoscheduler/",    exam_autoscheduler_home.exam_autoscheduler_home, name="exam_autoscheduler_home"),
    path("lab/timetable/",         lab_timetable_panel.lab_timetable_panel,         name="lab_timetable_panel"),
    path("cot/exam-timetable/",    cot_exam_timetable.cot_exam_timetable,           name="cot_exam_timetable"),
    path("venues/",                venues_panel.venues_panel,                       name="venues_panel"),
    path("utility/dashboard/",     utility_dashboard.utility_dashboard,             name="utility_dashboard"),
    path("autoscheduler/home/",    autoscheduler_home.autoscheduler_home,           name="autoscheduler_home"),

    # Lab exam timetable
    path("lab_exam_timetable/",       lab_exam_timetable_panel.lab_exam_timetable_panel, name="lab_exam_timetable_panel"),
    path("lab_exam_timetable/api/",   lab_exam_timetable_panel.lab_exam_timetable_api,   name="lab_exam_timetable_api"),
    
    # Secure media endpoints
    path("site/logo/",                 views.site_logo,          name="site_logo"),
    path("site/photo/<int:photo_id>/", views.site_photo,         name="site_photo"),

    # Public JSON API
    path("api/site-settings/",        views.site_settings_json,  name="site_settings_json"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)