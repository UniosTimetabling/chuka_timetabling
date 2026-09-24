from django.urls import path
from . import views,auto_allocate_courses,cod_panel_clear,lab_allocations,course_allocations_list,lab_safe_undo
from . import view_course_allocations,toggle_submission_to_dvc,toggle_submission_to_tt
from . import lab_auto_allocate

urlpatterns = [
    path('auto-allocate-courses/', auto_allocate_courses.auto_allocate_all_page, name='auto_allocate_courses'),
    path("cod/clear/", cod_panel_clear.cod_panel_clear, name="cod_panel_clear"),
    path("course-allocations/", view_course_allocations.course_allocations_page, name="course_allocations_page"),
    path("course-allocations/data/", view_course_allocations.course_allocations_data, name="course_allocations_data"),
    path("course-allocations/save/", view_course_allocations.save_course_allocation, name="save_course_allocation"),
    path("course-allocations/delete/<int:pk>/", view_course_allocations.delete_course_allocation, name="delete_course_allocation"),
    path("auto-allocate-all/", auto_allocate_courses.auto_allocate_all_page, name="auto_allocate_all_page"),
    path("ajax/auto-allocate-all/", auto_allocate_courses.auto_allocate_all_ajax, name="auto_allocate_all_ajax"),
    
    # ── Get allocation config ──────────────────────────────────────────
    path("get-allocation-config/", auto_allocate_courses.get_allocation_config, name="get_allocation_config"),
    
    path("toggle/dvc/", toggle_submission_to_dvc.toggle_submission_to_dvc, name="toggle_submission_to_dvc"),
    path("toggle/tt/", toggle_submission_to_tt.toggle_submission_to_tt, name="toggle_submission_to_tt"),
    path("lab_allocations/", lab_allocations.lab_allocations, name="lab_allocations"),
    path("lab_allocations/auto-allocate/tree/", lab_auto_allocate.get_allocation_tree, name="api_lab_auto_allocate_tree"),
    path("lab_allocations/auto-allocate/run/", lab_auto_allocate.run_allocation, name="api_lab_auto_allocate_run"),
    path("lab_allocations/safe-undo/", lab_safe_undo.lab_safe_undo_page, name="lab_safe_undo_page"),
    path("lab_allocations/safe-undo/action/", lab_safe_undo.lab_safe_undo_action, name="lab_safe_undo_action"),
    path("allocations/view/", course_allocations_list.course_allocations_list, name="course_allocations_view"),
]

from django.urls import path
from . import auto_allocate_courses

urlpatterns += [
    # ------------------------------------------------------------------
    # Lecturer Course Mapping
    # ------------------------------------------------------------------
    path("lecturer-course-mapping/",
         auto_allocate_courses.lecturer_course_mapping,
         name="lecturer_course_mapping"),
    path("save-lecturer-mapping/",
         auto_allocate_courses.save_lecturer_mapping,
         name="save_lecturer_mapping"),
    path("delete-lecturer-mapping/",
         auto_allocate_courses.delete_lecturer_mapping,
         name="delete_lecturer_mapping"),
    path("get-lecturer-mapping/",
         auto_allocate_courses.get_lecturer_mapping,
         name="get_lecturer_mapping"),
    # ------------------------------------------------------------------
    # Auto Allocation
    # ------------------------------------------------------------------
    path("auto-allocate/",
         auto_allocate_courses.auto_allocate_all_page,
         name="auto_allocate_all_page"),
    path("auto-allocate/run/",
         auto_allocate_courses.auto_allocate_all_ajax,
         name="auto_allocate_all_ajax"),
    path("get-department-allocations/",
         auto_allocate_courses.get_department_allocations,
         name="get_department_allocations"),
]

from django.urls import path
from course_allocation import program_enrollment as enrollment_views  

urlpatterns += [
    # ── Program Enrollment ──────────────────────────────────────────
    path("program-enrollment/", enrollment_views.program_enrollment_page, name="program_enrollment_page"),
    path("save-program-enrollment/", enrollment_views.save_program_enrollment, name="save_program_enrollment"),
    path("save-enrollment-sem/", enrollment_views.save_enrollment_single_sem, name="save_enrollment_single_sem"),
    path("delete-program-enrollment/", enrollment_views.delete_program_enrollment, name="delete_program_enrollment"),
    path("get-program-enrollments/", enrollment_views.get_program_enrollments, name="get_program_enrollments"),
    path("advance-academic-year/", enrollment_views.advance_academic_year, name="advance_academic_year"),
    path("shift-enrollment-year/", enrollment_views.shift_enrollment_year, name="shift_enrollment_year"),
    path("upload-enrollment/", enrollment_views.upload_enrollment_file, name="upload_enrollment_file"),
    path("enrollment-template/", enrollment_views.enrollment_template, name="enrollment_template"),
]