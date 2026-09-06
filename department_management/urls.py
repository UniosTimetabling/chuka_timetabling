from django.urls import path
from . import dean_panel
urlpatterns=[
     path("faculty/", dean_panel.dean_panel, name="deans_panel"),
]