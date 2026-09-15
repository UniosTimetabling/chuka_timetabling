from .models import Notification
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
import datetime

@receiver(post_save, sender=Notification)
def cleanup_old_notifications(sender, **kwargs):
    """
    Optional: Automatically delete notifications older than 30 days
    You can adjust the retention period in settings
    """
    if getattr(settings, 'AUTO_CLEANUP_NOTIFICATIONS', False):
        retention_days = getattr(settings, 'NOTIFICATION_RETENTION_DAYS', 30)
        cutoff_date = timezone.now() - datetime.timedelta(days=retention_days)
        Notification.objects.filter(created_at__lt=cutoff_date).delete()