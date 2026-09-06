from django.urls import path
from . import regular_timetable_apis,course_codes_api
from . import exam_timetable_apis,cot_exam_api
from . import conflicts_report_apis,lab_timetable_api
from . import shared_venue_group_api,lecturer_api,ajax_program_courses
urlpatterns=[
      path('api/autoscheduler/venues/', regular_timetable_apis.api_get_venues, name='api_get_venues'),
    path('api/autoscheduler/timetable/', regular_timetable_apis.api_get_timetable, name='api_get_timetable'),
     #path("auto_schedule/merged/api/", regular_timetable_apis.merged_api, name="merged_api"),
 path("api/exam/venues/", exam_timetable_apis.api_exam_venues, name="api_exam_venues"),
    path("api/exam/temp/", exam_timetable_apis.api_exam_temp_data, name="api_exam_temp_data"),
    path("api/exam/merged/", exam_timetable_apis.api_exam_merged_groups, name="api_exam_merged_groups"),
    path("api/exam/shared/", exam_timetable_apis.api_exam_shared_venues, name="api_exam_shared_venues"),
     path("api/collision-report/", conflicts_report_apis.collision_report_api, name="collision_report_api"),
    path('api/timetable/conflicts/', conflicts_report_apis.timetable_conflicts_api, name='timetable_conflicts_api'),
    path("api/unscheduled-courses/", conflicts_report_apis.unscheduled_courses_json, name="api_unscheduled_courses"),

  path("shared_venue_group_api/", shared_venue_group_api.shared_venue_group_api, name="shared_venue_group_api"),
  path("lecturers/api/", lecturer_api.lecturer_api, name="lecturer_api"),
   path("ajax/program-courses/", ajax_program_courses.ajax_program_courses, name="ajax_program_courses"),
    path("lab/timetable/api/", lab_timetable_api.lab_timetable_api, name="lab_timetable_api"),
    path("cot/exam-api/", cot_exam_api.cot_exam_api, name="cot_exam_api"),
     path("api/course-codes/", course_codes_api.api_course_codes, name="api_course_codes"),

]