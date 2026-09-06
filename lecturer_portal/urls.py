from django.urls import path

from lecturer_portal import lecturer_panel    
from . import department_lecturers_allocations
urlpatterns=[
      path("lecturers/",lecturer_panel.lecturer_panel, name="lecturer_panel"),
       path("lecture-allocator/", department_lecturers_allocations.lecture_allocator_panel, name="lecture_allocator_panel"),
]