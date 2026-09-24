from django.urls import path
from . import cod_panel, get_course_name, safe_undo, allocation_set_panel, course_allocation_excel
from course_allocation import base_selection_views
from course_allocation import specialization_stem_views
from course_allocation import groups_electives_page, groups_electives_ajax

urlpatterns = [
    path("cod/", cod_panel.cod_panel, name="cod_panel"),

    # ── Concurrent Allocation Sets ──────────────────────────────────────────
    path("cod/allocations/", allocation_set_panel.allocation_picker, name="allocation_picker"),
    path("cod/allocations/new/", allocation_set_panel.create_allocation_step1, name="create_allocation_step1"),
    path("cod/allocations/<int:allocation_set_id>/edit/", allocation_set_panel.edit_allocation_details, name="edit_allocation_details"),
    path("cod/allocations/<int:allocation_set_id>/hide/", allocation_set_panel.hide_allocation_set, name="hide_allocation_set"),
    path("cod/allocations/<int:allocation_set_id>/unhide/", allocation_set_panel.unhide_allocation_set, name="unhide_allocation_set"),
    path("cod/allocations/new/compose/<int:allocation_set_id>/", allocation_set_panel.create_allocation_step2, name="create_allocation_step2"),
    path("cod/allocations/<int:allocation_set_id>/select-courses/", allocation_set_panel.select_allocation_courses, name="select_allocation_courses"),
    path("cod/allocations/switch/", allocation_set_panel.switch_allocation_set, name="switch_allocation_set"),
    path("cod/allocations/<int:allocation_set_id>/submit/", allocation_set_panel.submit_allocation_set, name="submit_allocation_set"),

    path(
        "cod/course-allocation-template/",
        course_allocation_excel.course_allocation_template_page,
        name="course_allocation_template_page",
    ),
    path(
        "cod/course-allocation-template/export/",
        course_allocation_excel.export_course_allocation_template,
        name="export_course_allocation_template",
    ),
    path(
        "cod/course-allocation-template/import/",
        course_allocation_excel.import_course_allocation_template,
        name="import_course_allocation_template",
    ),
    path(
        "cod/course-allocation-template/share-email/",
        course_allocation_excel.share_course_allocation_template_email,
        name="share_course_allocation_template_email",
    ),
    # ── Student Groups / Combination Stems / Electives — one form+list page ──
    path("groups-electives/", groups_electives_page.groups_electives_page, name="groups_electives_page"),
    path("ajax/groups-electives/", groups_electives_ajax.ajax_groups_electives, name="ajax_groups_electives"),
    path("groups-electives/export-stems/", groups_electives_page.export_combination_stems_pdf, name="export_combination_stems_pdf"),

    path("cod/base-selections/", base_selection_views.base_selections_page, name="base_selections"),
    path("cod/base-selections/add/", base_selection_views.add_selection_group, name="add_selection_group"),
    path("cod/safe-undo/", safe_undo.safe_undo_page, name="safe_undo_page"),
    path("cod/safe-undo/action/", safe_undo.safe_undo_action, name="safe_undo_action"),
    path("ajax/get-course-name/", get_course_name.get_course_name, name="get_course_name"),
    
    # New endpoints for course combinations
    path("ajax/get-program-courses/", base_selection_views.get_program_courses_json, name="get_program_courses_json"),
    path("ajax/selection-groups/", base_selection_views.get_selection_groups_json, name="get_selection_groups_json"),
    path("selection-group/delete/<int:group_id>/", base_selection_views.delete_selection_group, name="delete_selection_group"),
    path("selection-group/update/<int:group_id>/", base_selection_views.update_selection_group, name="update_selection_group"),
    path("selection-group/add-courses/<int:group_id>/", base_selection_views.add_courses_to_group, name="add_courses_to_group"),
    path("selection-group/remove-course/<int:group_id>/<int:course_id>/", base_selection_views.remove_course_from_group, name="remove_course_from_group"),
    path("selection-group/detail/<int:group_id>/", base_selection_views.group_detail, name="group_detail"),

    # ── Student Group <-> Selection Group (elective pool) mapping ──────────
    path("ajax/selection-groups-for-student-group/", base_selection_views.get_selection_groups_for_student_group, name="get_selection_groups_for_student_group"),
    path("selection-group/map-groups/<int:group_id>/", base_selection_views.map_selection_group_groups, name="map_selection_group_groups"),
    path("selection-group/toggle-group/<int:group_id>/", base_selection_views.toggle_selection_group_group, name="toggle_selection_group_group"),
    path("student-group/quick-create-selection-group/", base_selection_views.quick_create_selection_group_for_student_group, name="quick_create_selection_group_for_student_group"),

    # ── Stem <-> Selection Group: pick-ONE pools NESTED inside a stem ───────
    path("ajax/selection-groups-for-stem/", base_selection_views.get_selection_groups_for_stem, name="get_selection_groups_for_stem"),
    path("selection-group/toggle-stem/<int:group_id>/", base_selection_views.toggle_selection_group_stem, name="toggle_selection_group_stem"),
    path("stem/quick-create-selection-group/", base_selection_views.quick_create_selection_group_for_stem, name="quick_create_selection_group_for_stem"),
    
    # Combined Course Group API endpoints are handled through the main cod_panel POST
    # view (action= parameter). Direct URL registration of the static methods is removed
    # because those bypass @login_required / @group_required decorators.

    # ── Specialization Stems (pick-ONE-stem, take-ALL-courses-in-stem) ──────
    path("cod/specialization-stems/", specialization_stem_views.specialization_stems_page, name="specialization_stems"),
    path("cod/specialization-stems/add-category/", specialization_stem_views.add_specialization_category, name="add_specialization_category"),
    path("ajax/specialization-tree/", specialization_stem_views.get_specialization_tree_json, name="get_specialization_tree_json"),
    path("specialization-category/delete/<int:category_id>/", specialization_stem_views.delete_specialization_category, name="delete_specialization_category"),
    path("specialization-category/add-stem/<int:category_id>/", specialization_stem_views.add_specialization_stem, name="add_specialization_stem"),
    path("specialization-stem/delete/<int:stem_id>/", specialization_stem_views.delete_specialization_stem, name="delete_specialization_stem"),
    path("specialization-stem/update/<int:stem_id>/", specialization_stem_views.update_specialization_stem, name="update_specialization_stem"),
    path("specialization-stem/add-courses/<int:stem_id>/", specialization_stem_views.add_courses_to_stem, name="add_courses_to_stem"),
    path("specialization-stem/remove-course/<int:stem_id>/<int:course_id>/", specialization_stem_views.remove_course_from_stem, name="remove_course_from_stem"),

    # ── Student Group <-> Specialization Stem mapping ───────────────────────
    path("ajax/stems-for-student-group/", specialization_stem_views.get_stems_for_student_group, name="get_stems_for_student_group"),
    path("specialization-stem/map-groups/<int:stem_id>/", specialization_stem_views.map_specialization_stem_groups, name="map_specialization_stem_groups"),
    path("specialization-stem/toggle-group/<int:stem_id>/", specialization_stem_views.toggle_specialization_stem_group, name="toggle_specialization_stem_group"),
    path("student-group/quick-create-stem/", specialization_stem_views.quick_create_stem_for_student_group, name="quick_create_stem_for_student_group"),
]

from . import department_timetable as views

urlpatterns += [
    # Main pages
    path('department/', views.department_timetable_view, name='department_timetable'),
    path('department/print/', views.export_department_timetable_pdf, name='department_timetable_pdf'),
    path('select-department/', views.select_department_view, name='select_department'),
    path('clear-selected-department/', views.clear_selected_department, name='clear_selected_department'),
    
    # API endpoints - BOTH patterns for compatibility
    path('api/timetable/department-data/', views.get_department_timetable_data, name='get_department_timetable_data'),
    path('api/timetable/department-timetable-data/', views.get_department_timetable_data, name='get_department_timetable_data_alt'),
    
    # These you already have:
    path('api/timetable/department-programs/', views.get_department_programs, name='get_department_programs'),
    path('api/timetable/department-courses/', views.get_department_courses, name='get_department_courses'),
    path('api/timetable/user-department-info/', views.user_department_info, name='user_department_info'),
    path('api/timetable/selected-department-data/', views.get_selected_department_data, name='get_selected_department_data'),
]