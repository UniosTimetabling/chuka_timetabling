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
