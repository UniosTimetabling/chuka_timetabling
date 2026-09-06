from django.urls import path
from . import notification_apis
urlpatterns=[
    path('api/notifications/', notification_apis.get_notifications, name='get_notifications'),
    path('api/notifications/read/', notification_apis.mark_notification_read, name='mark_notification_read'),
]