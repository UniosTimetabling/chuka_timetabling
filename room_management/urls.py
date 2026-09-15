from django.urls import path
from . import lab_venues

urlpatterns=[
     path("lab-venues/", lab_venues.lab_venues, name="lab_venues"),
]