from django.urls import path
from . import views_sync, views_auth, views_reference, views_documents

urlpatterns = [
    path("api/desktop/auth/login/", views_auth.login, name="desktop_auth_login"),
    path("api/desktop/auth/logout/", views_auth.logout, name="desktop_auth_logout"),
    path("api/desktop/auth/me/", views_auth.me, name="desktop_auth_me"),

    path("api/desktop/timetable/<str:kind>/", views_sync.pull_timetable, name="desktop_sync_pull"),
    path("api/desktop/timetable/<str:kind>/push/", views_sync.push_timetable, name="desktop_sync_push"),

    path("api/desktop/reference/lab-exam/", views_reference.pull_lab_exam_reference, name="desktop_reference_lab_exam"),

    path("api/desktop/published-pdfs/", views_documents.list_published_pdfs, name="desktop_published_pdfs_list"),
    path("api/desktop/published-pdfs/<int:doc_id>/file/", views_documents.download_published_pdf, name="desktop_published_pdf_file"),
]
