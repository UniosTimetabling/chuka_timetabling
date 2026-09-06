from django.urls import path

from timetable import exam_timetable_view
from .algorithms.regular_timetable_autosheduler_algorithm import *
from . import views,timetable_panel,delete_all_timetables,update_scheduler_config,simulate_move,venue_evacuation
from . import publish_timetables,delete_exam_entry,delete_all_Exam_timetables,timetable_entry_crud
from .algorithms import progress_tracking_autosheduler,lab_allocation_autosheduler,regular_timetable_autosheduler_algorithm,run_lab_exam_autoscheduler
from . import delete_timetable_entry,exam_timetable_panel,update_exam_scheduler_config,remove_from_merged,add_to_merged,delete_merged_group
from . import main_timetable_view,timetable_views_crud,history_views,update_exam_config
from . import exam_simulate_move
from . import find_courses, exam_find_courses
from . import analysis_reports

urlpatterns=[

   
    path("update-exam-config/", update_exam_config.update_exam_config, name="update_exam_config"),

    # ── Regular autoscheduler ────────────────────────────────────────────────
    path("autoscheduler/run/", regular_timetable_autosheduler_algorithm.run_autoscheduler, name="run_autoscheduler"),
    path('scheduler/progress/', regular_timetable_autosheduler_algorithm.SchedulerProgressView.as_view(), name='scheduler_progress'),
    path('scheduler/cancel/', regular_timetable_autosheduler_algorithm.CancelSchedulingView.as_view(), name='cancel_scheduling'),
    path('scheduler/start/', regular_timetable_autosheduler_algorithm.StartSchedulingView.as_view(), name='start_scheduling'),
    path('autoscheduler/progress/', regular_timetable_autosheduler_algorithm.run_autoscheduler, name='autoscheduler_progress_page'),

    # ── Publish ──────────────────────────────────────────────────────────────
    path("autoscheduler/publish/exam/", publish_timetables.exam_publish_to_main, name="publish_to_exam_main"),
    path("autoscheduler/publish/", publish_timetables.publish_to_main, name="publish_to_main"),

    # ── Exam autoscheduler ───────────────────────────────────────────────────
    path('exam-scheduler/progress/', progress_tracking_autosheduler.SchedulerProgressView.as_view(), name='scheduler_progress_exam'),
    path('exam-scheduler/cancel/', progress_tracking_autosheduler.CancelSchedulingView.as_view(), name='cancel_scheduling_exam'),
    path('autoscheduler/exam/run/', progress_tracking_autosheduler.StartSchedulingView.as_view(), name='run_exam_autoscheduler'),

    # ── Timetable entry management ───────────────────────────────────────────
    path("timetable/delete-entry/", delete_timetable_entry.delete_timetable_entry, name="delete_timetable_entry"),
    path('timetable/delete-all/', delete_all_timetables.delete_all_timetables, name='delete_all_timetables'),
    path("autoscheduler/config/", update_scheduler_config.update_scheduler_config, name="update_scheduler_config"),

    # ── Exam timetable ───────────────────────────────────────────────────────
    path('exam/timetable/', exam_timetable_panel.exam_timetable_panel, name='exam_timetable_panel'),
    path('exam/timetable/delete-all/', delete_all_Exam_timetables.delete_all_Exam_timetables, name='delete_all_exam_timetables'),
    path("update_scheduler_config/exam/", update_exam_scheduler_config.update_scheduler_config, name="update_scheduler_exam_config"),
    path("delete_exam_entry/", delete_exam_entry.delete_exam_entry, name="delete_exam_entry"),

    # ── Merged / shared group management ────────────────────────────────────
    path("merged/remove/", remove_from_merged.remove_from_merged, name="remove_from_merged"),
    path("merged/add/", add_to_merged.add_to_merged, name="add_to_merged"),
    path("merged/delete/", delete_merged_group.delete_merged_group, name="delete_merged_group"),

    # ── Main timetable view / CRUD ───────────────────────────────────────────
    path('timetable/view/', main_timetable_view.main_timetable_view, name='main_timetable'),
    path('timetable/update/', timetable_views_crud.update_timetable, name='update_timetable'),
    path('timetable/delete/', timetable_views_crud.delete_timetable, name='delete_timetable'),
    path('timetable/lab/update/', timetable_views_crud.update_lab_timetable, name='update_lab_timetable'),
    path('create-exam-timetable/', timetable_views_crud.create_exam_timetable, name='create_exam_timetable'),
    path('update-exam-timetable/', timetable_views_crud.update_exam_timetable, name='update_exam_timetable'),
    path('exam/view/', exam_timetable_view.exam_timetable_view, name='exam_timetable'),

    # ── Archive ──────────────────────────────────────────────────────────────
    path("archive/", history_views.archive_timetable_view, name="archive_timetable"),
    path("archive/unarchive/<int:archive_id>/", history_views.unarchive_timetable_view, name="unarchive_timetable"),

    # ── Lab scheduler ────────────────────────────────────────────────────────
    path("auto_schedule/lab/run/", lab_allocation_autosheduler.run_autoscheduler, name="run_lab_autoscheduler"),
    path("lab_exam_timetable/auto/", run_lab_exam_autoscheduler.run_lab_exam_autoscheduler, name="run_lab_exam_autoscheduler"),

    # ── Timetable entry CRUD (model-based) ──────────────────────────────────
    path("timetable/edit/<str:model_name>/<int:entry_id>/", timetable_entry_crud.edit_timetable_entry, name="edit_timetable_entry"),
    path("timetable/delete/<str:model_name>/<int:entry_id>/", timetable_entry_crud.delete_timetable_entry, name="delete_timetable_entry"),

    # ── Exam timetable data / conflict APIs ──────────────────────────────────
    path('exam/timetable/load-data/', exam_timetable_panel.load_exam_timetable_data, name='load_exam_timetable_data'),
    path('exam/timetable/conflicts-api/', exam_timetable_panel.exam_conflicts_api, name='exam_conflicts_api'),
]

from . import  timetable_panel
from . import combined_group_ops
from . import update_student_count

urlpatterns += [
    path('timetable/optimize-venues/', timetable_panel.optimize_venues_per_timeslot, name='optimize_venues_per_timeslot'),
   path('timetable/', timetable_panel.timetable_panel, name='timetable_panel'),
    path('timetable/load-data/', timetable_panel.load_timetable_data, name='load_timetable_data'),
    path('timetable/start-loading/', timetable_panel.start_timetable_loading, name='start_timetable_loading'),
    path('timetable/loading-progress/',timetable_panel. timetable_loading_progress, name='timetable_loading_progress'),
    path('timetable/stream-progress/', timetable_panel.stream_timetable_progress, name='stream_timetable_progress'),
    path('timetable/conflicts-api/', timetable_panel.timetable_conflicts_api, name='timetable_conflicts_api'),
    path('timetable/merged-api/', timetable_panel.merged_api, name='merged_api'),
    path('timetable/bulk-delete/', timetable_panel.bulk_delete_timetable_entries, name='bulk_delete_timetable_entries'),
    path('timetable/resolve-unscheduled/', timetable_panel.resolve_unscheduled_courses, name='resolve_unscheduled_courses'),
    path('timetable/resolve-collisions/propose/', timetable_panel.propose_collision_resolutions, name='propose_collision_resolutions'),
    path('timetable/resolve-collisions/commit/', timetable_panel.commit_collision_resolutions, name='commit_collision_resolutions'),

    # ── Simulate Move / Bulk Move ────────────────────────────────────────────
    path('timetable/move/slot-catalog/', simulate_move.slot_catalog_api, name='slot_catalog_api'),
    path('timetable/move/simulate/', simulate_move.simulate_move_api, name='simulate_move_api'),
    path('timetable/move/execute/', simulate_move.execute_move_api, name='execute_move_api'),
    path('timetable/move/bulk-scope-options/', simulate_move.bulk_move_scope_options_api, name='bulk_move_scope_options_api'),
    path('timetable/move/bulk-candidates/', simulate_move.bulk_move_candidates_api, name='bulk_move_candidates_api'),
    path('timetable/move/bulk-execute/', simulate_move.bulk_move_execute_api, name='bulk_move_execute_api'),
    path('timetable/copy/simulate/', simulate_move.simulate_copy_api, name='simulate_copy_api'),
    path('timetable/copy/execute/', simulate_move.execute_copy_api, name='execute_copy_api'),
    path('timetable/swap/simulate/', simulate_move.simulate_swap_api, name='simulate_swap_api'),
    path('timetable/swap/execute/', simulate_move.execute_swap_api, name='execute_swap_api'),
    path('timetable/combine/simulate/', simulate_move.simulate_combine_api, name='simulate_combine_api'),
    path('timetable/combine/execute/', simulate_move.execute_combine_api, name='execute_combine_api'),

    # ── Combined Course Group context-menu actions (rename course/group,
    #    add/remove/move group membership) ──────────────────────────────
    path('timetable/combined-group/options/', combined_group_ops.combined_group_options_api, name='tt_combined_group_options_api'),
    path('timetable/course/rename/', combined_group_ops.rename_course_api, name='tt_rename_course_api'),
    path('timetable/combined-group/rename/', combined_group_ops.rename_combined_group_api, name='tt_rename_combined_group_api'),
    path('timetable/combined-group/add/', combined_group_ops.add_to_combined_group_api, name='tt_add_to_combined_group_api'),
    path('timetable/combined-group/remove/', combined_group_ops.remove_from_combined_group_api, name='tt_remove_from_combined_group_api'),
    path('timetable/combined-group/move/', combined_group_ops.move_to_combined_group_api, name='tt_move_to_combined_group_api'),

    # ── Update student count from the right-click menu (this course only,
    #    or the whole program+year cohort) — writes straight to
    #    CourseAllocation.number_of_students, so it shows up in Course
    #    Allocation immediately with no separate sync step. ────────────────
    path('timetable/course/update-student-count/', update_student_count.update_student_count_api, name='tt_update_student_count_api'),

    # ── Evacuate Venue (right-click a venue row → move all its courses) ──────
    path('timetable/venue/evacuate/preview/', venue_evacuation.evacuate_venue_preview_api, name='evacuate_venue_preview_api'),
    path('timetable/venue/evacuate/execute/', venue_evacuation.evacuate_venue_execute_api, name='evacuate_venue_execute_api'),

    # ── Simulate Move / Bulk Move — exam timetable ───────────────────────────
    path('exam/timetable/move/slot-catalog/', exam_simulate_move.exam_slot_catalog_api, name='exam_slot_catalog_api'),
    path('exam/timetable/move/simulate/', exam_simulate_move.exam_simulate_move_api, name='exam_simulate_move_api'),
    path('exam/timetable/move/execute/', exam_simulate_move.exam_execute_move_api, name='exam_execute_move_api'),
    path('exam/timetable/move/bulk-scope-options/', exam_simulate_move.exam_bulk_move_scope_options_api, name='exam_bulk_move_scope_options_api'),
    path('exam/timetable/move/bulk-candidates/', exam_simulate_move.exam_bulk_move_candidates_api, name='exam_bulk_move_candidates_api'),
    path('exam/timetable/move/bulk-execute/', exam_simulate_move.exam_bulk_move_execute_api, name='exam_bulk_move_execute_api'),

    # ── Find Courses ──────────────────────────────────────────────────────────
    path('timetable/find/filter-options/', find_courses.find_filter_options_api, name='find_filter_options_api'),
    path('timetable/find/courses/', find_courses.find_courses_api, name='find_courses_api'),
    path('exam/timetable/find/filter-options/', exam_find_courses.exam_find_filter_options_api, name='exam_find_filter_options_api'),
    path('exam/timetable/find/courses/', exam_find_courses.exam_find_courses_api, name='exam_find_courses_api'),

    # ═══════════════════════════════════════════════════════════════════════
    # ── Analysis / Reports dashboard ─────────────────────────────────────────
    # ═══════════════════════════════════════════════════════════════════════
    
    # Main dashboard page
    path('timetable/analysis/', analysis_reports.analysis_dashboard, name='timetable_analysis_dashboard'),
    
    # Summary API (cards on dashboard)
    path('timetable/analysis/summary-api/', analysis_reports.analysis_summary_api, name='timetable_analysis_summary_api'),
    
    # PDF exports
    path('timetable/analysis/export-unscheduled/', analysis_reports.export_unscheduled_pdf, name='export_unscheduled_pdf'),
    path('timetable/analysis/export-unscheduled-only/', analysis_reports.export_unscheduled_only_pdf, name='export_unscheduled_only_pdf'),
    path('timetable/analysis/export-scheduled/', analysis_reports.export_scheduled_pdf, name='export_scheduled_pdf'),

    # Scheduled Timetable API (on-screen table backing "Export Scheduled Timetable — PDF")
    path('timetable/analysis/scheduled-timetable/', analysis_reports.scheduled_timetable_api, name='scheduled_timetable_api'),
    path('timetable/analysis/export-all-courses-venue-summary/', analysis_reports.export_all_courses_venue_summary_pdf, name='export_all_courses_venue_summary_pdf'),
    path('timetable/analysis/export-blocked-designated-venues/', analysis_reports.export_blocked_designated_venues_pdf, name='export_blocked_designated_venues_pdf'),
    path('timetable/analysis/export-all-venues/', analysis_reports.export_all_venues_pdf, name='export_all_venues_pdf'),
    path('timetable/analysis/export-used-rooms/', analysis_reports.export_used_rooms_pdf, name='export_used_rooms_pdf'),
    path('timetable/analysis/export-venue-capacity-report/', analysis_reports.export_venue_capacity_report_pdf, name='export_venue_capacity_report_pdf'),
    
    # Course lookup
    path('timetable/analysis/query-course-schedule/', analysis_reports.query_course_schedule_api, name='query_course_schedule_api'),
    path('timetable/analysis/export-course-schedule/', analysis_reports.export_course_schedule_pdf, name='export_course_schedule_pdf'),

    # Universal search (lecturer / program / department / course code)
    path('timetable/analysis/universal-search/', analysis_reports.universal_search_api, name='universal_search_api'),
    path('timetable/analysis/export-universal-search/', analysis_reports.export_universal_search_pdf, name='export_universal_search_pdf'),
    
    # Program Analysis API
    path('timetable/analysis/program-analysis-api/', analysis_reports.program_analysis_api, name='program_analysis_api'),
    
    # Zero-Student Courses API
    path('timetable/analysis/zero-student-courses/', analysis_reports.zero_student_courses_api, name='zero_student_courses_api'),
    
    # Lecturer Overload API
    path('timetable/analysis/lecturer-overload/', analysis_reports.lecturer_overload_api, name='lecturer_overload_api'),

    # Consecutive Classes / Weekly Workload API
    path('timetable/analysis/lecturer-workload/', analysis_reports.lecturer_workload_api, name='lecturer_workload_api'),
    path('timetable/analysis/lecturer-weekly-schedule/', analysis_reports.lecturer_weekly_schedule_api, name='lecturer_weekly_schedule_api'),
    path('timetable/analysis/program-workload/', analysis_reports.program_workload_api, name='program_workload_api'),
    path('timetable/analysis/program-year-weekly-schedule/', analysis_reports.program_year_weekly_schedule_api, name='program_year_weekly_schedule_api'),

    # Email Department (recipients preview + send)
    path('timetable/analysis/department-email-recipients/', analysis_reports.department_email_recipients_api, name='department_email_recipients_api'),
    path('timetable/analysis/send-department-email/', analysis_reports.send_department_timetable_email, name='send_department_timetable_email'),
]


from django.urls import path
from .algorithms import stable_sheduling_algorithm as views

urlpatterns += [
    # Basic Stable Scheduler URLs (Mode 1)
    path('scheduler/basic-stable/start/', views.StartBasicStableSchedulingView.as_view(), name='start_basic_stable'),
    path('scheduler/basic-stable/progress/', views.BasicStableProgressView.as_view(), name='basic_stable_progress'),
    path('scheduler/basic-stable/cancel/', views.CancelBasicStableSchedulingView.as_view(), name='cancel_basic_stable'),
    path('scheduler/basic-stable/run/', views.run_basic_stable_scheduler, name='run_basic_stable'),
    
    # Advanced Stable Scheduler URLs (Mode 2)
    path('scheduler/advanced-stable/start/', views.StartAdvancedStableSchedulingView.as_view(), name='start_advanced_stable'),
    path('scheduler/advanced-stable/progress/', views.AdvancedStableProgressView.as_view(), name='advanced_stable_progress'),
    path('scheduler/advanced-stable/cancel/', views.CancelAdvancedStableSchedulingView.as_view(), name='cancel_advanced_stable'),
    path('scheduler/advanced-stable/run/', views.run_advanced_stable_scheduler, name='run_advanced_stable'),
]

from .algorithms import dual_campus_scheduler as dcs

# Add these URL patterns to your existing urlpatterns list:
urlpatterns += [
    # Main scheduler page
    path('dual-scheduler/', dcs.dual_scheduler_page, name='dual_scheduler_page'),

    # Start / Cancel / Progress
    path('dual-scheduler/start/', dcs.StartDualSchedulerView.as_view(), name='start_dual_scheduling'),
    path('dual-scheduler/cancel/', dcs.CancelDualSchedulerView.as_view(), name='cancel_dual_scheduling'),
    path('dual-scheduler/progress/', dcs.DualSchedulerProgressView.as_view(), name='dual_scheduler_progress'),

    # Publish
    path('dual-scheduler/publish/main/', dcs.PublishMainTimetableView.as_view(), name='publish_main_timetable'),
    path('dual-scheduler/publish/campus/', dcs.PublishCampusTimetableView.as_view(), name='publish_campus_timetable'),

    # Data APIs
    path('dual-scheduler/data/', dcs.DualTimetableDataView.as_view(), name='dual_timetable_data'),
    path('dual-scheduler/cross-campus/', dcs.CrossCampusLecturersView.as_view(), name='cross_campus_lecturers'),
]

from .algorithms.dual_campus_exam_autosheduler_algorithm import (
    dual_exam_scheduler_page,
    StartDualExamSchedulerView,
    DualExamSchedulerProgressView,
    CancelDualExamSchedulerView,
    PublishMainExamTimetableView,
    PublishCampusExamTimetableView,
    DualExamTimetableDataView,
    DualExamUnifiedGroupsView,
    DualExamConflictCheckView,
)

urlpatterns += [
    # ── Main scheduler page ──────────────────────────────────────
    path(
        "exam/dual-campus/",
        dual_exam_scheduler_page,
        name="dual_exam_scheduler_page",
    ),

    # ── Start / cancel / progress ────────────────────────────────
    path(
        "exam/dual-campus/start/",
        StartDualExamSchedulerView.as_view(),
        name="start_dual_exam_scheduling",
    ),
    path(
        "exam/dual-campus/progress/",
        DualExamSchedulerProgressView.as_view(),
        name="dual_exam_scheduler_progress",
    ),
    path(
        "exam/dual-campus/cancel/",
        CancelDualExamSchedulerView.as_view(),
        name="cancel_dual_exam_scheduling",
    ),

    # ── Publish ──────────────────────────────────────────────────
    path(
        "exam/dual-campus/publish/main/",
        PublishMainExamTimetableView.as_view(),
        name="publish_main_exam_timetable",
    ),
    path(
        "exam/dual-campus/publish/campus/",
        PublishCampusExamTimetableView.as_view(),
        name="publish_campus_exam_timetable",
    ),

    # ── Data / API endpoints ─────────────────────────────────────
    path(
        "exam/dual-campus/data/",
        DualExamTimetableDataView.as_view(),
        name="dual_exam_timetable_data",
    ),
    path(
        "exam/dual-campus/unified-groups/",
        DualExamUnifiedGroupsView.as_view(),
        name="dual_exam_unified_groups",
    ),
    path(
        "exam/dual-campus/conflict-check/",
        DualExamConflictCheckView.as_view(),
        name="dual_exam_conflict_check",
    ),
]

from django.urls import path
from . import exam_timetable_panel

urlpatterns += [
    # ── Create groups ─────────────────────────────────────────
    path(
        "merged/create/",
        exam_timetable_panel.create_merged_group,
        name="create_merged_group",
    ),
    path(
        "shared-venue/create/",
        exam_timetable_panel.create_shared_venue_group,
        name="create_shared_venue_group",
    ),

    # ── Delete groups ─────────────────────────────────────────
    path(
        "shared-venue/delete/",
        exam_timetable_panel.delete_shared_venue_group,
        name="delete_shared_venue_group",
    ),

    # ── Toggle published status ───────────────────────────────
    path(
        "merged/toggle-published/",
        exam_timetable_panel.toggle_merged_published,
        name="toggle_merged_published",
    ),
    path(
        "shared-venue/toggle-published/",
        exam_timetable_panel.toggle_shared_published,
        name="toggle_shared_published",
    ),
]

from django.urls import path
from . import exam_timetable_panel

urlpatterns += [
    
   # Called when a course's student count exceeds a venue's exam capacity.
    # Receives: allocation_id, exam_date, slot_time, venue_codes (JSON list).
    # Creates a SharedVenueExamGroup spanning the chosen venues.
    path(
        'exam/entry/split-venue/',
        exam_timetable_panel.confirm_venue_split,
        name='confirm_venue_split',
    ),

    # ── Delete groups ─────────────────────────────────────────
    path(
        "shared-venue/delete/",
        exam_timetable_panel.delete_shared_venue_group,
        name="delete_shared_venue_group",
    ),
    
    # ── NEW: Bulk delete exam entries ────────────────────────
    path(
        "exam/timetable/bulk-delete/",
        exam_timetable_panel.bulk_delete_exam_entries,
        name="bulk_delete_exam_entries",
    ),

   
]