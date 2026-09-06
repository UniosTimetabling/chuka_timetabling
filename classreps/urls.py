from django.urls import path
from . import classrep_login,classrep_dashboard,publish_minimal_timetable,edit_minimal_entry

urlpatterns = [
    path("classrep/login/", classrep_login.classrep_login, name="classrep_login"),
    path("classrep_dashboard/", classrep_dashboard.classrep_dashboard, name="classrep_dashboard"),
    path("classrep/publish/", publish_minimal_timetable.publish_minimal_timetable, name="publish_minimal_timetable"),
    path("classrep/edit/<int:entry_id>/", edit_minimal_entry.edit_minimal_entry, name="edit_minimal_entry"),
]