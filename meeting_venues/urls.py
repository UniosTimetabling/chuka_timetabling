from django.urls import path
from . import views

urlpatterns = [
    path("meeting-venues/", views.meeting_venues_panel, name="meeting_venues_panel"),
    path("meeting-venues/public/book/", views.public_book_meeting_venue, name="public_book_meeting_venue"),
    path("meeting-venues/public/unbook-request/", views.public_request_unbook, name="public_request_unbook_meeting_venue"),
]
