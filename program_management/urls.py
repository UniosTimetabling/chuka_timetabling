from django.urls import path

from program_management import programs_page
from program_management import export_import_programs
from program_management import course_master_rollback
urlpatterns=[
 path("programs/",programs_page.programs_page, name="programs_page"),
 path("programs/export/", export_import_programs.export_programs, name="export_programs"),
 path("programs/import/", export_import_programs.import_programs, name="import_programs"),
 path("programs/import/apply/", export_import_programs.import_programs_apply, name="import_programs_apply"),
 path("programs/rollback/", course_master_rollback.course_master_rollback_page, name="course_master_rollback"),
 path("programs/rollback/action/", course_master_rollback.course_master_rollback_action, name="course_master_rollback_action"),
]