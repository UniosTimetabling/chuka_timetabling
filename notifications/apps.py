# notifications/apps.py
from django.apps import AppConfig

class NotificationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'notifications'
    
    def ready(self):
        # Import signals when the app is ready
        import notifications.signals  # or from . import signals