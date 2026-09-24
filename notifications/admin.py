# notifications/admin.py
from django.contrib import admin
from django.utils import timezone
from django.db.models import Count
from .models import Notification

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    # Display fields in the list view
    list_display = [
        'id',
        'short_message', 
        'target_display',
        'is_read',
        'created_at',
        'age'
    ]
    
    # Fields that can be searched
    search_fields = [
        'message', 
        'target_user__username', 
        'target_user__email',
        'target_group__name', 
        'target_role'
    ]
    
    # Filters for the right sidebar
    list_filter = [
        'is_read',
        'created_at',
        'target_user',
        'target_group',
        'target_role'
    ]
    
    # Default ordering
    ordering = ['-created_at']
    
    # Fields to display in the detail view
    fieldsets = (
        ('Message Information', {
            'fields': ('message', 'is_read', 'created_at')
        }),
        ('Target Audience', {
            'fields': ('target_user', 'target_group', 'target_role'),
            'description': 'Leave all fields empty to send to all users'
        }),
    )
    
    # Read-only fields
    readonly_fields = ['created_at']
    
    # List per page
    list_per_page = 50
    
    # Date hierarchy for navigation
    date_hierarchy = 'created_at'
    
    # Actions dropdown
    actions = ['mark_as_read', 'mark_as_unread', 'delete_old_notifications']
    
    # List display custom methods
    def short_message(self, obj):
        """Truncate long messages"""
        return obj.message[:75] + '...' if len(obj.message) > 75 else obj.message
    short_message.short_description = 'Message'
    short_message.admin_order_field = 'message'
    
    def target_display(self, obj):
        """Display target with icon/emoji for better visualization"""
        if obj.target_user:
            return f'👤 {obj.target_user.username}'
        elif obj.target_group:
            return f'👥 {obj.target_group.name}'
        elif obj.target_role:
            return f'⭐ {obj.target_role}'
        else:
            return '🌍 All Users'
    target_display.short_description = 'Target'
    target_display.admin_order_field = 'target_user'
    
    def age(self, obj):
        """Show how old the notification is"""
        delta = timezone.now() - obj.created_at
        if delta.days > 0:
            return f'{delta.days} day(s)'
        elif delta.seconds > 3600:
            return f'{delta.seconds // 3600} hour(s)'
        elif delta.seconds > 60:
            return f'{delta.seconds // 60} minute(s)'
        else:
            return 'Just now'
    age.short_description = 'Age'
    age.admin_order_field = 'created_at'
    
    # Custom actions
    def mark_as_read(self, request, queryset):
        """Mark selected notifications as read"""
        updated = queryset.update(is_read=True)
        self.message_user(request, f'{updated} notification(s) marked as read.')
    mark_as_read.short_description = "Mark selected notifications as read"
    
    def mark_as_unread(self, request, queryset):
        """Mark selected notifications as unread"""
        updated = queryset.update(is_read=False)
        self.message_user(request, f'{updated} notification(s) marked as unread.')
    mark_as_unread.short_description = "Mark selected notifications as unread"
    
    def delete_old_notifications(self, request, queryset):
        """Delete notifications older than 30 days"""
        from django.conf import settings
        import datetime
        
        retention_days = getattr(settings, 'NOTIFICATION_RETENTION_DAYS', 30)
        cutoff_date = timezone.now() - datetime.timedelta(days=retention_days)
        
        old_notifications = queryset.filter(created_at__lt=cutoff_date)
        count = old_notifications.count()
        old_notifications.delete()
        
        self.message_user(request, f'Deleted {count} notification(s) older than {retention_days} days.')
    delete_old_notifications.short_description = "Delete selected notifications (if older than retention period)"
    
    # Customize queryset for better performance
    def get_queryset(self, request):
        """Optimize queryset with select_related"""
        return super().get_queryset(request).select_related(
            'target_user', 'target_group'
        )
    
    # Add some statistics to the changelist view
    def changelist_view(self, request, extra_context=None):
        # Get statistics
        total = Notification.objects.count()
        unread = Notification.objects.filter(is_read=False).count()
        read = total - unread
        
        # Target distribution
        target_stats = {
            'all': Notification.objects.filter(
                target_user__isnull=True,
                target_group__isnull=True,
                target_role__isnull=True
            ).count(),
            'users': Notification.objects.filter(target_user__isnull=False).count(),
            'groups': Notification.objects.filter(target_group__isnull=False).count(),
            'roles': Notification.objects.filter(target_role__isnull=False).count(),
        }
        
        # Add to context
        extra_context = extra_context or {}
        extra_context['total_notifications'] = total
        extra_context['unread_notifications'] = unread
        extra_context['read_notifications'] = read
        extra_context['target_stats'] = target_stats
        
        return super().changelist_view(request, extra_context=extra_context)


# Optional: Add a more compact inline view if you want to show notifications in User admin
class UserNotificationInline(admin.TabularInline):
    model = Notification
    fk_name = 'target_user'
    fields = ['message', 'is_read', 'created_at']
    readonly_fields = ['created_at']
    extra = 0
    max_num = 10
    can_delete = True
    show_change_link = True


# Optional: Add inline for Group admin
class GroupNotificationInline(admin.TabularInline):
    model = Notification
    fk_name = 'target_group'
    fields = ['message', 'is_read', 'created_at']
    readonly_fields = ['created_at']
    extra = 0
    max_num = 10
    can_delete = True
    show_change_link = True


# Optional: If you want to customize the admin site title for notifications
admin.site.site_header = 'Notifications Administration'
admin.site.site_title = 'Notifications Admin'


# If you want to add custom template for better visualization
"""
Create a template at: notifications/templates/admin/notifications/notification/change_list.html

{% extends "admin/change_list.html" %}

{% block content %}
    <div style="margin-bottom: 20px; padding: 10px; background: #f8f9fa; border-radius: 4px;">
        <h3>Notification Statistics</h3>
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px;">
            <div style="background: #fff; padding: 10px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                <strong>Total:</strong> {{ total_notifications }}
            </div>
            <div style="background: #fff; padding: 10px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                <strong>Unread:</strong> {{ unread_notifications }}
            </div>
            <div style="background: #fff; padding: 10px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                <strong>Read:</strong> {{ read_notifications }}
            </div>
        </div>
        <h4 style="margin-top: 10px;">Target Distribution</h4>
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px;">
            <div style="background: #e3f2fd; padding: 10px; border-radius: 4px;">All Users: {{ target_stats.all }}</div>
            <div style="background: #e8f5e8; padding: 10px; border-radius: 4px;">Users: {{ target_stats.users }}</div>
            <div style="background: #fff3e0; padding: 10px; border-radius: 4px;">Groups: {{ target_stats.groups }}</div>
            <div style="background: #f3e5f5; padding: 10px; border-radius: 4px;">Roles: {{ target_stats.roles }}</div>
        </div>
    </div>
    {{ block.super }}
{% endblock %}
"""