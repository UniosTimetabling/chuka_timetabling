from django.urls import path

from export_import import export_course_allocations_csv, export_exam_csv
from . import official_exam_timetable, views, course_list_pdf, export_course_allocations_csv, export_exam_pdf, view_and_export_all_course_allocations, smart_import_export
from . import official_timetables, official_exam_timetable
from . import special_requests_pdf
from . import sync_views
from . import program_year_pdf_views

urlpatterns = [
    path("smart-data-import-export/", smart_import_export.import_export_view, name="smart-import_export"),
    path("export/special-requests/pdf/", special_requests_pdf.export_special_requests_pdf, name="export_special_requests_pdf"),

    path("export/timetable/", views.export_exam_csv, name="export_exam_timetable"),
    path("autoscheduler/export/main/csv/", views.export_main_csv, name="export_main_csv"),
    path("autoscheduler/export/main/pdf/", views.export_main_pdf, name="export_main_pdf"),
    path('exam/timetable/auto/pdf/', views.export_exam_pdf, name='exam_sheduler_pdf'),
    path('exam/timetable/auto/csv/', views.export_exam_csv, name='exam_sheduler_csv'),
    path("download/regular/timetable/", official_timetables.export_main_pdf, name="regular_timetabling_pdf_official"),
    path("download/exam/timetable/", official_exam_timetable.export_main_exam_pdf, name="export_main_exam_pdf_official"),
   
    path('course-allocations/csv/', export_course_allocations_csv.export_course_allocations_csv, name='export_course_allocations_csv'),
    path("download/timetable/", export_exam_pdf.export_exam_pdf, name="download_exam_timetable"),
    path("export/timetable/", export_exam_csv.export_exam_csv, name="export_exam_timetable"),
    path('view-export-allocations/', view_and_export_all_course_allocations.view_course_allocations, name='view_export_course_allocations'),

    # ── Zero-Student Report ───────────────────────────────────────────────────
    # PDF download (role-scoped: COD sees own dept, timetabler sees all)
    path('export/zero-student-report/', __import__('export_import.zero_student_report', fromlist=['zero_student_report_pdf']).zero_student_report_pdf, name='zero_student_report_pdf'),
    # JSON endpoint consumed by the timetable panel JS
    path('api/zero-student-courses/', __import__('export_import.zero_student_report', fromlist=['zero_student_report_json']).zero_student_report_json, name='zero_student_report_json'),
]

from export_import import publish_timetables_pdfs
urlpatterns += [
    # Publish and store PDFs
    path('publish-exam-pdf/', publish_timetables_pdfs.publish_exam_timetable_pdf,
         name='publish_exam_pdf'),
    path('publish-regular-pdf/', publish_timetables_pdfs.publish_regular_timetable_pdf,
         name='publish_regular_pdf'),

    # Download and list PDFs
    path('download-pdf/<int:document_id>/', publish_timetables_pdfs.download_pdf_document,
         name='download_pdf'),
    path('list-pdfs/', publish_timetables_pdfs.list_pdf_documents,
         name='list_pdfs'),
]

from django.urls import path
from . import download_latest_timetable_pdf as views
from . import global_combined_timetable_pdf as global_pdf

urlpatterns += [
    # Simple endpoints for downloading latest timetables
    path('download/latest/regular/', views.download_latest_regular, name='download_latest_regular'),
    path('download/latest/exam/', views.download_latest_exam, name='download_latest_exam'),
    path(
        'export/global/regular-timetable/',
        global_pdf.export_global_regular_pdf,
        name='export_global_regular_pdf',
    ),

    # ── Global combined Exam Timetable (main campus + all campuses + lab exams) ──
    path(
        'export/global/exam-timetable/',
        global_pdf.export_global_exam_pdf,
        name='export_global_exam_pdf',
    ),
]



urlpatterns += [
    # Single synchronous endpoint: generates, saves to disk, streams inline as PDF blob
    path('course-list/pdf/', course_list_pdf.course_list_pdf_view, name='course_list_pdf'),
    path('export/course-list-pdf/', course_list_pdf.course_list_pdf_view, name='course_list_pdf_export'),
]

# ── Host / Remote data sync ─────────────────────────────────────────────────
urlpatterns += [
    path('sync/settings/', sync_views.sync_settings, name='sync_settings'),
    path('sync/run/<int:run_id>/', sync_views.sync_run_detail, name='sync_run_detail'),
    path('sync/trigger/', sync_views.trigger_sync, name='trigger_sync'),
    path('sync/status/', sync_views.sync_status, name='sync_status'),
    # Connection status — cached read (global badge) + on-demand active check.
    path('sync/connection-status/', sync_views.connection_status_api, name='sync_connection_status_api'),
    path('sync/test-connection/', sync_views.test_connection_api, name='sync_test_connection_api'),
    # One real push of a single harmless fake record — proves the whole
    # pipe (network + token + encryption + remote save) works end to end,
    # without waiting through a full multi-model run_sync().
    path('sync/test-push/', sync_views.test_push_api, name='sync_test_push_api'),
    # Certificate pinning (TOFU) — HOST only.
    path('sync/fetch-remote-cert/', sync_views.fetch_remote_cert, name='sync_fetch_remote_cert'),
    path('sync/pin-remote-cert/', sync_views.pin_remote_cert, name='sync_pin_remote_cert'),
    # Remote-side inbound endpoints.
    path('sync/ping/', sync_views.sync_ping, name='sync_ping'),
    path('sync/receive/', sync_views.receive_sync, name='receive_sync'),
    path('sync/verify-pks/', sync_views.sync_verify_pks, name='sync_verify_pks'),
]

# ── Cached per (department, program, year) timetable PDFs ──────────────────
urlpatterns += [
    # Staff-facing, id-based, login required, viewed inline.
    path(
        'program-timetable/<int:department_id>/<int:program_id>/<int:year>/<str:timetable_type>/',
        program_year_pdf_views.program_year_pdf,
        name='program_year_pdf',
    ),

    # Public, self-service downloads — rate limited, backed by the same
    # cache. See export_import/program_year_pdf_views.py for details.
    path(
        'timetable/program/<str:program_code>/<int:year>/<str:timetable_type>/',
        program_year_pdf_views.program_timetable_by_code,
        name='timetable_by_program_code',
    ),
    path(
        'timetable/program-name/<str:program_name>/<int:year>/<str:timetable_type>/',
        program_year_pdf_views.program_timetable_by_name,
        name='timetable_by_program_name',
    ),
    path(
        'timetable/student/',
        program_year_pdf_views.student_timetable_by_registration,
        name='timetable_by_registration',
    ),
    path(
        'timetable/program-id/<int:department_id>/<int:program_id>/<int:year>/<str:timetable_type>/',
        program_year_pdf_views.program_timetable_by_ids,
        name='timetable_by_program_id',
    ),
    path(
        'timetable/resolve/',
        program_year_pdf_views.resolve_student_scope,
        name='timetable_resolve_student_scope',
    ),
]