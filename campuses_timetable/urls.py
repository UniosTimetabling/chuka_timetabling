from django.urls import path
from . import course_allocation_views, safe_undo, auto_allocate

app_name = 'campuses_timetable'

urlpatterns = [
    path('panel/', course_allocation_views.course_allocation_panel, name='course_allocation_panel'),
    path('panel/safe-undo/', safe_undo.campus_safe_undo_page, name='campus_safe_undo_page'),
    path('panel/safe-undo/action/', safe_undo.campus_safe_undo_action, name='campus_safe_undo_action'),
    path('api/allocation-action/', course_allocation_views.allocation_action, name='allocation_action'),
    path('api/course-codes/', course_allocation_views.api_course_codes, name='api_course_codes'),
    path('api/get-course-name/', course_allocation_views.get_course_name, name='get_course_name'),
    path('api/auto-allocate/tree/', auto_allocate.get_allocation_tree, name='api_auto_allocate_tree'),
    path('api/auto-allocate/run/', auto_allocate.run_allocation, name='api_auto_allocate_run'),
    path('toggle/dvc/', course_allocation_views.toggle_submission_to_dvc, name='toggle_submission_to_dvc'),
    path('toggle/tt/', course_allocation_views.toggle_submission_to_tt, name='toggle_submission_to_tt'),
    path('download/pdf/', course_allocation_views.download_allocations_pdf, name='download_allocations_pdf'),

    # ── Program Year Tracker ──────────────────────────────────────────────
    path('tracker/', course_allocation_views.tracker_dashboard, name='tracker_dashboard'),
    path('tracker/api/', course_allocation_views.tracker_api, name='tracker_api'),
    path('tracker/<int:tracker_id>/resend/', course_allocation_views.resend_gap_notification, name='resend_gap_notification'),
]

from django.urls import path
from . import manual_views

app_name = 'campuses_timetable'

urlpatterns += [
    # Manual timetable views
    path('manual/', manual_views.manual_timetable, name='manual_timetable'),
    path('manual/time-slots/', manual_views.get_time_slots, name='get_time_slots'),
    path('manual/timetable-data/', manual_views.get_timetable_data, name='get_timetable_data'),
    path('manual/check-availability/', manual_views.check_availability, name='check_availability'),
    path('manual/class/create/', manual_views.create_class_entry, name='create_class_entry'),
    path('manual/exam/create/', manual_views.create_exam_entry, name='create_exam_entry'),
    path('manual/class/delete/<int:entry_id>/', manual_views.delete_class_entry, name='delete_class_entry'),
    path('manual/exam/delete/<int:entry_id>/', manual_views.delete_exam_entry, name='delete_exam_entry'),
    path('manual/clear/', manual_views.clear_timetable, name='clear_timetable'),
    path('manual/config/update/', manual_views.update_config, name='update_config'),
]


from django.urls import path
from . import automatic_views,pdf_views

app_name = 'campuses_timetable'

urlpatterns += [
    
    # Auto scheduler URLs
    path('auto/', automatic_views.auto_scheduler, name='auto_scheduler'),
    path('auto/run-class/', automatic_views.run_class_scheduler, name='run_class_scheduler'),
    path('auto/run-exam/', automatic_views.run_exam_scheduler, name='run_exam_scheduler'),
    path('auto/publish-class/', automatic_views.publish_class_timetable, name='publish_class_timetable'),
    path('auto/publish-exam/', automatic_views.publish_exam_timetable, name='publish_exam_timetable'),
    path('auto/clear/', automatic_views.clear_timetable_drafts, name='clear_timetable_drafts'),
    path('auto/config/update/', automatic_views.update_scheduler_config, name='update_scheduler_config'),
    path('auto/status/', automatic_views.scheduler_status, name='scheduler_status'),

# Generate PDF endpoints
path('campuses/pdf/class/generate/', 
     pdf_views.generate_class_timetable_pdf, 
     name='campuses_generate_class_pdf'),
path('campuses/pdf/exam/generate/', 
     pdf_views.generate_exam_timetable_pdf, 
     name='campuses_generate_exam_pdf'),
path('campuses/pdf/allocations/generate/', 
     pdf_views.generate_allocations_pdf, 
     name='campuses_generate_allocations_pdf'),

# Preview PDF endpoints
path('campuses/pdf/class/preview/', 
     pdf_views.preview_class_timetable_pdf, 
     name='campuses_preview_class_pdf'),
path('campuses/pdf/exam/preview/', 
     pdf_views.preview_exam_timetable_pdf, 
     name='campuses_preview_exam_pdf'),

# Publish endpoints
path('campuses/publish/class/', 
     pdf_views.publish_class_timetable, 
     name='campuses_publish_class'),
path('campuses/publish/exam/', 
     pdf_views.publish_exam_timetable, 
     name='campuses_publish_exam'),

# Download published endpoints
path('campuses/download/class/latest/', 
     pdf_views.download_latest_class_timetable, 
     name='campuses_download_latest_class'),
path('campuses/download/exam/latest/', 
     pdf_views.download_latest_exam_timetable, 
     name='campuses_download_latest_exam'),
path('campuses/download/<int:pk>/', 
     pdf_views.download_published_timetable, 
     name='campuses_download_published'),

# View published timetables
path('campuses/published/', 
     pdf_views.view_published_timetables, 
     name='campuses_published_list'),
path('campuses/published/<int:pk>/info/', 
     pdf_views.get_published_info, 
     name='campuses_published_info'),
]

