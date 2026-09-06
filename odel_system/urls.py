from django.urls import path
from . import views_allocation, views_manual, views_auto, safe_undo, auto_allocate

app_name = 'odel_system'

urlpatterns = [
    # === MAIN PAGES ===
    path('allocations/', views_allocation.allocation_page, name='allocation_page'),
    path('allocations/safe-undo/', safe_undo.odel_safe_undo_page, name='odel_safe_undo_page'),
    path('allocations/safe-undo/action/', safe_undo.odel_safe_undo_action, name='odel_safe_undo_action'),
    path('manual/', views_manual.manual_scheduling_page, name='manual_page'),
    path('auto/', views_auto.auto_scheduling_page, name='auto_page'),
    
    # === ALLOCATION AJAX ENDPOINTS ===
    path('allocations/create/', views_allocation.allocation_create, name='api_allocation_create'),
    path('allocations/sr-action/', views_allocation.sr_action, name='api_sr_action'),
    path('allocations/<int:pk>/update/', views_allocation.allocation_update, name='api_allocation_update'),
    path('allocations/<int:pk>/delete/', views_allocation.allocation_delete, name='api_allocation_delete'),
    path('allocations/<int:pk>/detail/', views_allocation.get_allocation_detail, name='api_allocation_detail'),
    path('allocations/<int:pk>/approve/', views_allocation.allocation_approve, name='api_allocation_approve'),
    path('allocations/<int:pk>/forward-tt/', views_allocation.allocation_forward_tt, name='api_allocation_forward'),
    path('get-program-courses/', views_allocation.get_program_courses, name='api_get_program_courses'),
    path('allocations/auto-allocate/tree/', auto_allocate.get_allocation_tree, name='api_auto_allocate_tree'),
    path('allocations/auto-allocate/run/', auto_allocate.run_allocation, name='api_auto_allocate_run'),
    path('get-lecturers/', views_allocation.get_lecturers, name='api_get_lecturers'),
    path('api/bulk-submit/', views_allocation.bulk_submit_allocations, name='api_bulk_submit'),
    path('api/submission-stats/', views_allocation.submission_stats, name='api_submission_stats'),
    path('allocations/download-pdf/', views_allocation.download_allocations_pdf, name='download_allocations_pdf'),
    
    # === AUTO SCHEDULING AJAX ENDPOINTS ===
    path('auto/class/run/', views_auto.run_class_scheduler, name='api_run_class_scheduler'),
    path('auto/exam/run/', views_auto.run_exam_scheduler, name='api_run_exam_scheduler'),
    path('auto/class/publish/', views_auto.publish_class_timetable, name='api_publish_class'),
    path('auto/exam/publish/', views_auto.publish_exam_timetable, name='api_publish_exam'),
    path('auto/config/update/', views_auto.update_config, name='api_update_config'),
    path('auto/status/', views_auto.get_scheduler_status, name='api_scheduler_status'),
    
    # === MANUAL SCHEDULING AJAX ENDPOINTS ===
    path('manual/class/create/', views_manual.create_class_entry, name='api_create_class'),
    path('manual/exam/create/', views_manual.create_exam_entry, name='api_create_exam'),
    path('manual/class/update/<int:pk>/', views_manual.update_class_entry, name='api_update_class'),
    path('manual/exam/update/<int:pk>/', views_manual.update_exam_entry, name='api_update_exam'),
    path('manual/class/delete/<int:pk>/', views_manual.delete_class_entry, name='api_delete_class'),
    path('manual/exam/delete/<int:pk>/', views_manual.delete_exam_entry, name='api_delete_exam'),
    path('manual/clear/', views_manual.clear_timetable, name='api_clear_timetable'),
    path('manual/timetable-data/', views_manual.get_timetable_data, name='api_get_timetable_data'),
    path('manual/check-availability/', views_manual.check_availability, name='api_check_availability'),
    path('manual/time-slots/', views_manual.get_time_slots, name='api_get_time_slots'),
    path('manual/config/update/', views_manual.update_timetable_config, name='api_update_config'),
]

# PDF Management URLs
from . import pdf_management_views as views

urlpatterns += [
    # Generate PDF endpoints
    path('odel/pdf/class/generate/', 
         views.generate_class_timetable_pdf, 
         name='odel_generate_class_pdf'),
    path('odel/pdf/exam/generate/', 
         views.generate_exam_timetable_pdf, 
         name='odel_generate_exam_pdf'),

    # Preview PDF endpoints
    path('odel/pdf/class/preview/', 
         views.preview_class_timetable_pdf, 
         name='odel_preview_class_pdf'),
    path('odel/pdf/exam/preview/', 
         views.preview_exam_timetable_pdf, 
         name='odel_preview_exam_pdf'),

    # Publish endpoints
    path('odel/publish/class/', 
         views.publish_class_timetable, 
         name='odel_publish_class'),
    path('odel/publish/exam/', 
         views.publish_exam_timetable, 
         name='odel_publish_exam'),

    # Download published endpoints
    path('odel/download/class/latest/', 
         views.download_latest_class_timetable, 
         name='odel_download_latest_class'),
    path('odel/download/exam/latest/', 
         views.download_latest_exam_timetable, 
         name='odel_download_latest_exam'),
    path('odel/download/<int:pk>/', 
         views.download_published_timetable, 
         name='odel_download_published'),

    # View published timetables
    path('odel/published/', 
         views.view_published_timetables, 
         name='odel_published_list'),
    path('odel/published/<int:pk>/info/', 
         views.get_published_info, 
         name='odel_published_info'),
]