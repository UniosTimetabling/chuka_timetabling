from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User, Group
from django.db.models import Q

class Notification(models.Model):
    # Message content
    message = models.TextField()
    
    # Read status (for individual notifications)
    is_read = models.BooleanField(default=False)
    
    # Timestamp
    created_at = models.DateTimeField(default=timezone.now)
    
    # Target fields - all optional, default to all if none specified
    target_user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True,
        related_name='notifications'
    )
    target_group = models.ForeignKey(
        Group, 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True,
        related_name='notifications'
    )
    target_role = models.CharField(
        max_length=50, 
        null=True, 
        blank=True,
        help_text="Target specific role (e.g., 'admin', 'manager', 'staff')"
    )
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['target_user']),
            models.Index(fields=['target_group']),
            models.Index(fields=['target_role']),
            models.Index(fields=['is_read']),
        ]
    
    def __str__(self):
        return self.message[:50]
    
    def is_for_all(self):
        """Check if notification is for all users"""
        return not (self.target_user or self.target_group or self.target_role)
    
    def get_target_display(self):
        """Get human-readable target description"""
        if self.target_user:
            return f"User: {self.target_user.username}"
        elif self.target_group:
            return f"Group: {self.target_group.name}"
        elif self.target_role:
            return f"Role: {self.target_role}"
        else:
            return "All Users"
    
    def mark_as_read(self):
        """Mark this notification as read"""
        self.is_read = True
        self.save(update_fields=['is_read'])
    
    def mark_as_unread(self):
        """Mark this notification as unread"""
        self.is_read = False
        self.save(update_fields=['is_read'])
    
    @classmethod
    def create_for_user(cls, message, user):
        """Create a notification for a specific user"""
        return cls.objects.create(
            message=message,
            target_user=user
        )
    
    @classmethod
    def create_for_group(cls, message, group):
        """Create a notification for a specific group"""
        return cls.objects.create(
            message=message,
            target_group=group
        )
    
    @classmethod
    def create_for_role(cls, message, role):
        """Create a notification for users with a specific role"""
        return cls.objects.create(
            message=message,
            target_role=role
        )
    
    @classmethod
    def create_for_all(cls, message):
        """Create a notification for all users"""
        return cls.objects.create(message=message)


class NotificationManager(models.Manager):
    def for_user(self, user):
        """
        Get notifications intended for a specific user.
        Includes: 
        - Notifications specifically for this user
        - Notifications for user's groups
        - Notifications for user's role
        - Notifications for all users
        """
        if not user or not user.is_authenticated:
            return self.none()
        
        # Get user's groups
        user_groups = user.groups.all()
        
        # Build query for notifications
        # Start with notifications for all users
        query = Q(target_user__isnull=True, 
                  target_group__isnull=True, 
                  target_role__isnull=True)
        
        # Add notifications specifically for this user
        query |= Q(target_user=user)
        
        # Add notifications for user's groups
        if user_groups.exists():
            query |= Q(target_group__in=user_groups)
        
        # Add notifications for user's role (if you have a role system)
        # You may need to customize this based on your role implementation
        if hasattr(user, 'profile') and hasattr(user.profile, 'role'):
            query |= Q(target_role=user.profile.role)
        
        return self.filter(query).distinct()
    
    def unread_for_user(self, user):
        """Get unread notifications for a specific user"""
        return self.for_user(user).filter(is_read=False)
    
    def read_for_user(self, user):
        """Get read notifications for a specific user"""
        return self.for_user(user).filter(is_read=True)
    
    def mark_all_as_read(self, user):
        """Mark all notifications as read for a user"""
        return self.for_user(user).update(is_read=True)
    
    def mark_all_as_unread(self, user):
        """Mark all notifications as unread for a user"""
        return self.for_user(user).update(is_read=False)
    
    def delete_all_for_user(self, user):
        """Delete all notifications for a user"""
        # Note: This will delete the actual notification objects
        # If you want to keep notifications but hide them, consider adding a soft delete
        notifications = self.for_user(user)
        count = notifications.count()
        notifications.delete()
        return count


# Attach the manager to the Notification class
Notification.add_to_class('objects', NotificationManager())


