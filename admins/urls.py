from django.urls import path
from . import views as sudo, manage_cot_user
from . import sudo_manage_dvc, manage_timetable_view, manage_cod
from . import sudo_manage_timetabler, manage_deans
from . import resit_import_admin   # ← NEW
from . import smart_importer       # ← AI Smart Importer v1 (provider-agnostic)
from . import smart_importer_v2    # ← Smart Importer 2.0 (full ETL pipeline)
from . import views_ai_settings    # ← Central AI provider registry UI
from dashboards import published_timetables as dash_published_timetables

urlpatterns = [
    path("sudo-dashboard/", sudo.sudo_dashboard_view, name="sudo_homepage"),
    path("sudo/manage_dvc/", sudo_manage_dvc.manage_dvc_page, name="sudo_manage_dvc"),
    path("sudo/manage_dv/", sudo_manage_dvc.manage_dvc_page, name="manage_dvc_page"),
    path("dvc/ajax/", sudo_manage_dvc.ajax_manage_dvc, name="ajax_manage_dvc"),
    path("sudo/users/", sudo.sudo_manage_accounts, name="sudo_manage_accounts"),
    path("sudo/", sudo.sudo_dashboard, name="sudo_dashboard"),

    # AJAX load-more / modal
    path("sudo/ajax/load_more/", sudo.ajax_load_more, name="ajax_load_more"),
    path("sudo/ajax/modal_view/", sudo.ajax_modal_view, name="ajax_modal_view"),

    # Faculties
    path("sudo/ajax/faculty/", sudo.ajax_faculty, name="ajax_faculty"),

    # Departments
    path("sudo/ajax/department/", sudo.ajax_department, name="ajax_department"),

    # Allocations
    path("sudo/ajax/allocation/", sudo.ajax_allocation, name="ajax_allocation"),

    # Timetables
    path("sudo/ajax/timetable/", sudo.ajax_timetable, name="ajax_timetable"),
    path("sudo/manage-accounts/", sudo.sudo_manage_accounts, name="sudo_manage_accounts"),
    path("sudo/ajax/users/", sudo.ajax_users, name="ajax_users"),
    path("sudo/ajax/leaders/", sudo.ajax_leaders, name="ajax_leaders"),
    path("sudo/ajax/groups/", sudo.ajax_groups, name="ajax_groups"),
    path("timetable/manage/", sudo_manage_timetabler.manage_timetable_view, name="manage_timetable"),
    path("timetable/ajax/", sudo_manage_timetabler.ajax_manage_timetable, name="ajax_manage_timetable"),
    path("dean/reset_credentials/", manage_deans.reset_dean_credentials_view, name="reset_dean_credentials"),
    path("cot/users/", manage_cot_user.manage_cot_user, name="manage_cot_user"),
    path("cot/users/<int:user_id>/", manage_cot_user.manage_cot_user, name="manage_cot_user"),
    path("timetable/manage/", manage_timetable_view.manage_timetable_view, name="manage_timetable"),
    path("timetable/ajax/", manage_timetable_view.ajax_manage_timetable, name="ajax_manage_timetable"),
    path("cods/", manage_cod.cod_management, name="cod_management"),
    path("cods/create/", manage_cod.create_cod, name="create_cod"),
    path("cods/edit/<int:user_id>/", manage_cod.edit_cod, name="edit_cod"),
    path("cods/reset_password/<int:user_id>/", manage_cod.reset_cod_password, name="reset_cod_password"),
    path("cods/delete/<int:user_id>/", manage_cod.delete_cod, name="delete_cod"),
    path("sudo/bulk-reset/", sudo.admin_bulk_reset_password, name="admin_bulk_reset_password"),

    # ── Resit Management (Admin-level) ──────────────────────────────────────
    path(
        "resits/admin/import/",
        resit_import_admin.admin_resit_import_view,
        name="admin_resit_import",
    ),

    # ── Published Timetables Registry ──────────────────────────────────────
    path(
        "published/timetables/",
        dash_published_timetables.published_timetables_view,
        name="admin_published_timetables",
    ),
    # ── AI Smart Importer ───────────────────────────────────────────────────
    path(
        "sudo-dashboard/smart-import/",
        smart_importer.smart_import_view,
        name="sudo_smart_import",
    ),
    path(
        "sudo-dashboard/smart-import/extract/",
        smart_importer.smart_import_extract,
        name="sudo_smart_import_extract",
    ),
    path(
        "sudo-dashboard/smart-import/refine/",
        smart_importer.smart_import_refine,
        name="sudo_smart_import_refine",
    ),
    path(
        "sudo-dashboard/smart-import/commit/",
        smart_importer.smart_import_commit,
        name="sudo_smart_import_commit",
    ),
    path(
        "sudo-dashboard/smart-import/csv/",
        smart_importer.smart_import_csv,
        name="sudo_smart_import_csv",
    ),

    # ── Smart Importer 2.0 (full 12-stage ETL pipeline) ─────────────────────
    path(
        "sudo-dashboard/smart-import-v2/",
        smart_importer_v2.smart_import_v2_view,
        name="sudo_smart_import_v2",
    ),
    path(
        "sudo-dashboard/smart-import-v2/classify/",
        smart_importer_v2.smart_import_v2_classify,
        name="sudo_smart_import_v2_classify",
    ),
    path(
        "sudo-dashboard/smart-import-v2/extract/",
        smart_importer_v2.smart_import_v2_extract,
        name="sudo_smart_import_v2_extract",
    ),
    path(
        "sudo-dashboard/smart-import-v2/refine/",
        smart_importer_v2.smart_import_v2_refine,
        name="sudo_smart_import_v2_refine",
    ),
    path(
        "sudo-dashboard/smart-import-v2/commit/",
        smart_importer_v2.smart_import_v2_commit,
        name="sudo_smart_import_v2_commit",
    ),
    path(
        "sudo-dashboard/smart-import-v2/csv/",
        smart_importer_v2.smart_import_v2_csv,
        name="sudo_smart_import_v2_csv",
    ),
    path(
        "sudo-dashboard/smart-import-v2/corrections/",
        smart_importer_v2.smart_import_v2_corrections,
        name="sudo_smart_import_v2_corrections",
    ),

    # ── Central AI Provider Registry (used by Smart Importer + future modules) ──
    path(
        "sudo-dashboard/ai-settings/",
        views_ai_settings.ai_settings_view,
        name="sudo_ai_settings",
    ),
    path(
        "sudo-dashboard/ai-settings/save/",
        views_ai_settings.ai_settings_save,
        name="sudo_ai_settings_save",
    ),
    path(
        "sudo-dashboard/ai-settings/<int:pk>/activate/",
        views_ai_settings.ai_settings_activate,
        name="sudo_ai_settings_activate",
    ),
    path(
        "sudo-dashboard/ai-settings/deactivate-all/",
        views_ai_settings.ai_settings_deactivate_all,
        name="sudo_ai_settings_deactivate_all",
    ),
    path(
        "sudo-dashboard/ai-settings/<int:pk>/delete/",
        views_ai_settings.ai_settings_delete,
        name="sudo_ai_settings_delete",
    ),
    path(
        "sudo-dashboard/ai-settings/<int:pk>/test/",
        views_ai_settings.ai_settings_test,
        name="sudo_ai_settings_test",
    ),
]