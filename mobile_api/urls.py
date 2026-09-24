from django.urls import path

from . import (
    views_auth,
    views_timetable,
    views_exam_timetable,
    views_events,
    views_shared,
    views_courses,
    views_feedback,
    views_admin_announcements,
    views_schedule_visibility,
    views_install,
    views_mobile_analytics,
    views_shared_files,
    views_app_link,
)

urlpatterns = [
    # ── Feedback (from the mobile app) ─────────────────────────────────────
    path("api/mobile/feedback/", views_feedback.submit_feedback, name="mobile_submit_feedback"),

    # ── Staff: send notifications/messages/documents to the mobile app ────
    path("notifications/mobile/", views_admin_announcements.manage_announcements_page,
         name="manage_mobile_announcements"),
    path("api/mobile/admin/announcements/send/", views_admin_announcements.api_send_announcement,
         name="mobile_announcement_send"),
    path("api/mobile/admin/announcements/<int:pk>/toggle/", views_admin_announcements.api_toggle_announcement,
         name="mobile_announcement_toggle"),
    path("api/mobile/admin/announcements/<int:pk>/delete/", views_admin_announcements.api_delete_announcement,
         name="mobile_announcement_delete"),

    # ── Staff: block/unblock the regular or exam timetable on the app ─────
    path("mobile/schedule-visibility/", views_schedule_visibility.schedule_visibility_page,
         name="schedule_visibility_page"),
    path("api/mobile/admin/schedule-visibility/<str:schedule_type>/save/",
         views_schedule_visibility.api_save_schedule_visibility,
         name="mobile_schedule_visibility_save"),

    # ── Staff: upload files and get a shareable link ───────────────────────
    path("mobile/shared-files/", views_shared_files.shared_files_page, name="shared_files_page"),
    path("api/mobile/admin/shared-files/upload/", views_shared_files.api_upload_shared_file,
         name="mobile_shared_file_upload"),
    path("api/mobile/admin/shared-files/<int:pk>/delete/", views_shared_files.api_delete_shared_file,
         name="mobile_shared_file_delete"),

    # ── Device install tracking + staff analytics ──────────────────────────
    path("api/mobile/app-link/", views_app_link.app_link, name="mobile_app_link"),
    path("api/mobile/admin/app-link/save/", views_app_link.api_save_app_link, name="mobile_app_link_save"),
    path("api/mobile/install/", views_install.report_install, name="mobile_report_install"),
    path("mobile/analytics/", views_mobile_analytics.mobile_analytics_page, name="mobile_analytics_page"),

    path("api/mobile/auth/student/", views_auth.student_login, name="mobile_student_login"),
    path("api/mobile/auth/lecturer/", views_auth.lecturer_login, name="mobile_lecturer_login"),

    path("api/mobile/timetable/version/", views_timetable.timetable_version, name="mobile_timetable_version"),
    path("api/mobile/timetable/shared/", views_shared.shared_timetable, name="mobile_timetable_shared"),
    path("api/mobile/timetable/", views_timetable.timetable_full, name="mobile_timetable_full"),

    path("api/mobile/exam-timetable/version/", views_exam_timetable.exam_timetable_version, name="mobile_exam_timetable_version"),
    path("api/mobile/exam-timetable/", views_exam_timetable.exam_timetable_full, name="mobile_exam_timetable_full"),

    path("api/mobile/events/", views_events.events_list, name="mobile_events_list"),

    # Search + personal timetable additions
    path("api/mobile/courses/search/", views_courses.courses_search, name="mobile_courses_search"),
    path("api/mobile/courses/add/", views_courses.courses_add, name="mobile_courses_add"),
    path("api/mobile/courses/remove/", views_courses.courses_remove, name="mobile_courses_remove"),
    path("api/mobile/courses/mine/", views_courses.courses_mine, name="mobile_courses_mine"),
]
