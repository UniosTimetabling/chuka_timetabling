"""
resits_timetabling/urls.py
URL configuration for the resit timetabling module.
All URLs are routed to the appropriate handlers in their respective module files.
"""
from django.urls import path

# Import from individual module files
from . import cod_panel
from . import manual_timetabler
from . import resit_autosheduler
from . import resit_allocation_manager  # clear / archive panel
from . import views                     # toggle_resit_submission + config views
from . import resit_import
from . import publish
from . import clear_timetable
from . import resit_timetable_pdf       # PDF generation & download
from . import safe_undo
from . import auto_allocate

urlpatterns = [
    # ── COD panel (from cod_panel.py) ─────────────────────────────────────
    path("resits/cod/",         cod_panel.cod_panel,                      name="resit_cod_panel"),
    path("resits/cod/courses/", cod_panel.get_department_resit_courses,   name="resit_cod_courses_api"),
    path("resits/cod/safe-undo/",        safe_undo.resit_safe_undo_page,   name="resit_safe_undo_page"),
    path("resits/cod/safe-undo/action/", safe_undo.resit_safe_undo_action, name="resit_safe_undo_action"),
    path("resits/cod/auto-allocate/tree/", auto_allocate.get_allocation_tree, name="api_resit_auto_allocate_tree"),
    path("resits/cod/auto-allocate/run/",  auto_allocate.run_allocation,      name="api_resit_auto_allocate_run"),

    # ── Import panel (from resit_import.py) ───────────────────────────────
    path("resits/import/",      resit_import.resit_import_panel,          name="resit_import_panel"),

    # ── Allocation manager — clear & archive (from resit_allocation_manager.py) ─
    path("resits/allocations/manage/", resit_allocation_manager.resit_allocation_manager, name="resit_allocation_manager"),

    # ── Toggle submission control (simple view in views.py) ───────────────
    path("resits/toggle-submission/", views.toggle_resit_submission,      name="toggle_resit_submission"),

    # ── Manual timetabling (from manual_timetabler.py) ─────────────────────
    path("resits/manual/",              manual_timetabler.manual_timetabling_panel,    name="resit_manual_panel"),
    path("resits/manual/data/",         manual_timetabler.get_resit_panel_data,        name="resit_panel_data_api"),
    path("resits/manual/assign/",       manual_timetabler.assign_resit_slot,           name="resit_assign_slot"),
    path("resits/manual/check-conflict/", manual_timetabler.check_resit_slot_conflict,  name="resit_check_conflict"),
    path("resits/manual/delete/",       manual_timetabler.delete_resit_draft,          name="resit_delete_draft"),
    path("resits/manual/bulk-delete/",  manual_timetabler.bulk_delete_resit_entries,   name="resit_bulk_delete"),
    path("resits/manual/clear/",        manual_timetabler.clear_all_resit_drafts,      name="resit_clear_drafts"),
    path("resits/manual/publish/",      manual_timetabler.publish_resit_timetable,     name="resit_publish"),
    path("resits/manual/delete-published/", manual_timetabler.delete_published_entry,  name="resit_delete_published"),
    path("resits/manual/clear-timetable/", clear_timetable.clear_resit_timetable,      name="resit_clear_timetable"),

    # ── Auto-scheduler (from resit_autosheduler.py) ────────────────────────
    path("resits/auto/",                        resit_autosheduler.autosheduler_panel,             name="resit_auto_panel"),
    path("resits/auto/run/",                    resit_autosheduler.run_resit_autosheduler,          name="resit_run_autosheduler"),
    path("resits/auto/progress/<str:job_id>/",  resit_autosheduler.resit_autosheduler_progress,    name="resit_autosheduler_progress"),
    path("resits/auto/temp-data/",              resit_autosheduler.resit_temp_timetable_data,      name="resit_auto_temp_data"),
    path("resits/auto/publish/",                publish.publish_resit_draft,                        name="resit_auto_publish"),

    # ── Configuration endpoints (from views.py) ────────────────────────────
    path("resits/manual/config/",               views.get_resit_config,                 name="resit_manual_get_config"),
    path("resits/manual/config/save/",          views.save_resit_config,                name="resit_manual_save_config"),
    path("resits/manual/excluded-days/",        views.get_excluded_days_calendar,       name="resit_manual_excluded_days"),
    path("resits/manual/excluded-days/toggle/", views.toggle_excluded_day,              name="resit_manual_toggle_excluded"),

    # ── PDF: Publish timetable as PDF ──────────────────────────────────────
    # POST /resits/pdf/publish/
    # Timetablers / Sudo only. Generates a fresh landscape A4 PDF from all
    # current ResitTimetable entries, persists it as a ResitPublishedPDF
    # record (stored in MEDIA_ROOT/resit_timetable_pdfs/), and returns it
    # inline so the browser immediately opens it in a new tab.
    path(
        "resits/pdf/publish/",
        resit_timetable_pdf.publish_resit_timetable_pdf,
        name="resit_pdf_publish",
    ),

    # ── PDF: Download latest published PDF ────────────────────────────────
    # GET /resits/pdf/download/
    # Any authenticated user. Serves the most-recently published PDF as an
    # attachment (triggers the browser's save-as dialog).
    path(
        "resits/pdf/download/",
        resit_timetable_pdf.download_latest_resit_pdf,
        name="resit_pdf_download",
    ),

    # ── PDF: List published PDF history (JSON) ────────────────────────────
    # GET /resits/pdf/list/
    # Timetablers / Sudo only. Returns JSON array of the 30 most recent
    # ResitPublishedPDF records (newest first). Useful for a history panel.
    path(
        "resits/pdf/list/",
        resit_timetable_pdf.list_published_pdfs,
        name="resit_pdf_list",
    ),
]