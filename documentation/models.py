from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.contrib.auth.models import User
from django.utils import timezone

class DocumentationCategory(models.Model):
    """Category for organizing internal documentation"""
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, max_length=200)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, default='fas fa-folder')
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    access_level = models.CharField(max_length=20, choices=[
        ('all', 'All Staff'),
        ('department', 'Department'),
        ('management', 'Management'),
        ('admin', 'Administrators')
    ], default='all')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = "Documentation Categories"
        ordering = ['order', 'name']
        indexes = [
            models.Index(fields=['slug', 'is_active']),
            models.Index(fields=['order']),
            models.Index(fields=['access_level']),
        ]
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)
    
    def get_absolute_url(self):
        return reverse('documentation:category', kwargs={'slug': self.slug})
    
    def get_published_pages_count(self):
        return self.pages.filter(is_published=True).count()
    
    def get_published_pages(self):
        return self.pages.filter(is_published=True).order_by('order', 'title')
    
    def can_access(self, user):
        """Check if user can access this category."""
        from core.rbac import MANAGEMENT_ROLES, user_has_role
        if self.access_level == 'all':
            return True
        elif self.access_level == 'admin':
            return user.is_superuser
        elif self.access_level == 'management':
            return user.is_superuser or user_has_role(user, *MANAGEMENT_ROLES)
        elif self.access_level == 'department':
            return True
        return False

class DocumentationPage(models.Model):
    """Internal documentation page"""
    PAGE_TYPES = [
        ('guide', '📘 User Guide'),
        ('tutorial', '🎓 Tutorial'),
        ('reference', '🔧 Technical Reference'),
        ('process', '⚙️ Business Process'),
        ('policy', '📜 Policy'),
        ('faq', '❓ FAQ'),
        ('troubleshooting', '🔧 Troubleshooting'),
        ('api', '🔌 API Documentation'),
        ('setup', '⚡ Setup Guide'),
        ('security', '🔒 Security Protocol'),
    ]
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('review', 'Under Review'),
        ('published', 'Published'),
        ('archived', 'Archived'),
    ]
    
    DIFFICULTY_CHOICES = [
        ('beginner', 'Beginner'),
        ('intermediate', 'Intermediate'),
        ('advanced', 'Advanced'),
    ]
    
    # Basic Information
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, max_length=250)
    short_description = models.TextField(max_length=500)
    content = models.TextField()
    category = models.ForeignKey(DocumentationCategory, on_delete=models.CASCADE, related_name='pages')
    
    # Metadata
    page_type = models.CharField(max_length=20, choices=PAGE_TYPES, default='guide')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    difficulty = models.CharField(max_length=20, choices=DIFFICULTY_CHOICES, default='beginner')
    order = models.IntegerField(default=0)
    is_published = models.BooleanField(default=False)
    
    # Access Control
    requires_login = models.BooleanField(default=True)
    access_level = models.CharField(max_length=20, choices=[
        ('all', 'All Staff'),
        ('department', 'Department'),
        ('management', 'Management'),
        ('admin', 'Administrators')
    ], default='all')
    
    # Audit Fields
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='authored_pages')
    last_edited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='edited_pages')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)
    
    # Tracking
    views = models.IntegerField(default=0)
    estimated_read_time = models.IntegerField(default=5, help_text="In minutes")
    version = models.CharField(max_length=20, default='1.0')
    
    # Related Information
    prerequisites = models.ManyToManyField('self', symmetrical=False, blank=True, 
                                          related_name='required_for')
    related_pages = models.ManyToManyField('self', symmetrical=True, blank=True)
    
    # Approval
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, 
                                   related_name='approved_pages')
    approved_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['category__order', 'order', 'title']
        indexes = [
            models.Index(fields=['slug', 'is_published']),
            models.Index(fields=['category', 'order']),
            models.Index(fields=['page_type', 'is_published']),
            models.Index(fields=['status']),
            models.Index(fields=['views']),
            models.Index(fields=['created_at']),
            models.Index(fields=['updated_at']),
            models.Index(fields=['access_level']),
        ]
        permissions = [
            ('can_publish_docs', 'Can publish documentation'),
            ('can_approve_docs', 'Can approve documentation'),
            ('can_manage_categories', 'Can manage documentation categories'),
        ]
    
    def __str__(self):
        return f"{self.title} ({self.get_page_type_display()})"
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        
        # Set published_at when page is published
        if self.is_published and not self.published_at:
            self.published_at = timezone.now()
        elif not self.is_published:
            self.published_at = None
        
        super().save(*args, **kwargs)
    
    def get_absolute_url(self):
        return reverse('documentation:page', kwargs={'slug': self.slug})
    
    def increment_views(self):
        self.views += 1
        self.save(update_fields=['views'])
    
    def get_sections(self):
        return self.sections.filter(is_active=True).order_by('order')
    
    def can_access(self, user):
        """Check if user can access this page."""
        from core.rbac import MANAGEMENT_ROLES, user_has_role
        if not self.is_published:
            return False

        if not self.requires_login or user.is_authenticated:
            if self.access_level == 'all':
                return True
            elif self.access_level == 'admin':
                return user.is_superuser
            elif self.access_level == 'management':
                return user.is_superuser or user_has_role(user, *MANAGEMENT_ROLES)
            elif self.access_level == 'department':
                return True

        return False
    
    def get_page_type_icon(self):
        icons = {
            'guide': 'fas fa-book',
            'tutorial': 'fas fa-graduation-cap',
            'reference': 'fas fa-code',
            'process': 'fas fa-cogs',
            'policy': 'fas fa-file-contract',
            'faq': 'fas fa-question-circle',
            'troubleshooting': 'fas fa-wrench',
            'api': 'fas fa-plug',
            'setup': 'fas fa-cogs',
            'security': 'fas fa-shield-alt',
        }
        return icons.get(self.page_type, 'fas fa-file')

class DocumentationSection(models.Model):
    """Section within a documentation page"""
    page = models.ForeignKey(DocumentationPage, on_delete=models.CASCADE, related_name='sections')
    title = models.CharField(max_length=200)
    content = models.TextField()
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    slug = models.SlugField(max_length=250, blank=True)
    
    class Meta:
        ordering = ['order']
        unique_together = ['page', 'slug']
    
    def __str__(self):
        return f"{self.page.title} - {self.title}"
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)
    
    def get_absolute_url(self):
        return f"{self.page.get_absolute_url()}#section-{self.id}"

class CodeExample(models.Model):
    """Code examples for technical documentation"""
    LANGUAGES = [
        ('python', 'Python'),
        ('javascript', 'JavaScript'),
        ('sql', 'SQL'),
        ('bash', 'Bash/Shell'),
        ('json', 'JSON'),
        ('yaml', 'YAML'),
        ('django', 'Django Template'),
        ('html', 'HTML/CSS'),
        ('powershell', 'PowerShell'),
        ('docker', 'Dockerfile'),
    ]
    
    section = models.ForeignKey(DocumentationSection, on_delete=models.CASCADE, related_name='code_examples')
    title = models.CharField(max_length=200, blank=True)
    code = models.TextField()
    language = models.CharField(max_length=20, choices=LANGUAGES, default='python')
    order = models.IntegerField(default=0)
    description = models.TextField(blank=True)
    
    class Meta:
        ordering = ['order']
    
    def __str__(self):
        return self.title or f"Code Example ({self.language})"

class InternalLink(models.Model):
    """Internal links between documentation pages"""
    from_page = models.ForeignKey(DocumentationPage, on_delete=models.CASCADE, related_name='outgoing_links')
    to_page = models.ForeignKey(DocumentationPage, on_delete=models.CASCADE, related_name='incoming_links')
    description = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['from_page', 'to_page']
    
    def __str__(self):
        return f"{self.from_page.title} → {self.to_page.title}"

class DocumentationViewLog(models.Model):
    """Log each view of documentation for analytics"""
    page = models.ForeignKey(DocumentationPage, on_delete=models.CASCADE, related_name='view_logs')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    time_spent = models.IntegerField(default=0, help_text="Time spent in seconds")
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['page', 'timestamp']),
            models.Index(fields=['user', 'timestamp']),
        ]
    
    def __str__(self):
        return f"{self.page.title} viewed at {self.timestamp}"

class UserBookmark(models.Model):
    """User bookmarks for documentation pages"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookmarks')
    page = models.ForeignKey(DocumentationPage, on_delete=models.CASCADE, related_name='bookmarked_by')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['user', 'page']
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.username} bookmarked {self.page.title}"

class DocumentationTag(models.Model):
    """Tags for categorizing documentation"""
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(unique=True, max_length=100)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=7, default='#007bff')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

class PageTag(models.Model):
    """Many-to-many relationship between pages and tags"""
    page = models.ForeignKey(DocumentationPage, on_delete=models.CASCADE, related_name='page_tags')
    tag = models.ForeignKey(DocumentationTag, on_delete=models.CASCADE, related_name='tagged_pages')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['page', 'tag']
        ordering = ['tag__name']
    
    def __str__(self):
        return f"{self.page.title} - {self.tag.name}"

class DocumentationImage(models.Model):
    """Images for documentation content"""
    page = models.ForeignKey(DocumentationPage, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='documentation/images/%Y/%m/')
    caption = models.CharField(max_length=200, blank=True)
    alt_text = models.CharField(max_length=200)
    order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['order']
    
    def __str__(self):
        return self.caption or f"Image for {self.page.title}"

class PageComment(models.Model):
    """Internal comments on documentation pages"""
    page = models.ForeignKey(DocumentationPage, on_delete=models.CASCADE, related_name='comments')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='doc_comments')
    content = models.TextField()
    is_resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, 
                                   related_name='resolved_comments')
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Comment by {self.user.username} on {self.page.title}"

class DocumentationChangeLog(models.Model):
    """Track changes to documentation pages"""
    ACTION_CHOICES = [
        ('create', 'Created'),
        ('update', 'Updated'),
        ('publish', 'Published'),
        ('unpublish', 'Unpublished'),
        ('archive', 'Archived'),
        ('delete', 'Deleted'),
    ]
    
    page = models.ForeignKey(DocumentationPage, on_delete=models.CASCADE, related_name='change_logs')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    changes = models.JSONField(blank=True, null=True)
    previous_version = models.JSONField(blank=True, null=True)
    new_version = models.JSONField(blank=True, null=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['page', 'timestamp']),
            models.Index(fields=['action', 'timestamp']),
        ]
    
    def __str__(self):
        return f"{self.action} - {self.page.title}"

class DocumentationRating(models.Model):
    """Internal ratings for documentation pages"""
    page = models.ForeignKey(DocumentationPage, on_delete=models.CASCADE, related_name='ratings')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='doc_ratings')
    rating = models.IntegerField(choices=[(i, i) for i in range(1, 6)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['page', 'user']
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.username} rated {self.page.title}: {self.rating}/5"

class DocumentationAttachment(models.Model):
    """File attachments for documentation"""
    page = models.ForeignKey(DocumentationPage, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='documentation/attachments/%Y/%m/')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    file_type = models.CharField(max_length=50, blank=True)
    file_size = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.file_type:
            self.file_type = self.file.name.split('.')[-1].lower()
        self.file_size = self.file.size
        super().save(*args, **kwargs)

class UserReadingProgress(models.Model):
    """Track user reading progress"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reading_progress')
    page = models.ForeignKey(DocumentationPage, on_delete=models.CASCADE, related_name='reading_progress')
    last_read = models.DateTimeField(auto_now=True)
    progress_percentage = models.IntegerField(default=0)
    is_completed = models.BooleanField(default=False)
    time_spent = models.IntegerField(default=0, help_text="Total time spent in seconds")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['user', 'page']
        ordering = ['-last_read']
    
    def __str__(self):
        return f"{self.user.username} - {self.page.title} ({self.progress_percentage}%)"

class DocumentationSearchLog(models.Model):
    """Log search queries for analytics"""
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    query = models.CharField(max_length=500)
    results_count = models.IntegerField(default=0)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['query', 'timestamp']),
            models.Index(fields=['user', 'timestamp']),
        ]
    
    def __str__(self):
        return f"Search: {self.query}"

# Signals
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
import json

@receiver(pre_save, sender=DocumentationPage)
def update_published_date(sender, instance, **kwargs):
    """Update published_at when page is published"""
    if instance.id:
        try:
            old_instance = DocumentationPage.objects.get(id=instance.id)
            if not old_instance.is_published and instance.is_published:
                instance.published_at = timezone.now()
        except DocumentationPage.DoesNotExist:
            pass

@receiver(pre_save, sender=DocumentationPage)
def track_changes(sender, instance, **kwargs):
    """Track changes for change log"""
    if instance.id:
        try:
            old_instance = DocumentationPage.objects.get(id=instance.id)
            changes = {}
            
            # Track field changes
            fields_to_track = ['title', 'content', 'status', 'is_published', 'version']
            for field in fields_to_track:
                old_value = getattr(old_instance, field)
                new_value = getattr(instance, field)
                if old_value != new_value:
                    changes[field] = {'old': str(old_value), 'new': str(new_value)}
            
            if changes:
                instance._changes = changes
                instance._previous_state = {
                    'title': old_instance.title,
                    'content': old_instance.content[:500],  # Store first 500 chars
                    'status': old_instance.status,
                    'version': old_instance.version,
                }
        except DocumentationPage.DoesNotExist:
            pass

@receiver(post_save, sender=DocumentationPage)
def create_change_log(sender, instance, created, **kwargs):
    """Create change log entry after saving page"""
    if created:
        action = 'create'
    elif hasattr(instance, '_changes') and instance._changes:
        if 'is_published' in instance._changes:
            if instance.is_published:
                action = 'publish'
            else:
                action = 'unpublish'
        else:
            action = 'update'
    else:
        return
    
    # Get user from request if available
    from django.contrib.auth.models import AnonymousUser
    user = None
    if hasattr(instance, '_request_user'):
        user = instance._request_user if not isinstance(instance._request_user, AnonymousUser) else None
    
    DocumentationChangeLog.objects.create(
        page=instance,
        user=user,
        action=action,
        changes=getattr(instance, '_changes', None),
        previous_version=getattr(instance, '_previous_state', None),
        new_version={
            'title': instance.title,
            'content': instance.content[:500],
            'status': instance.status,
            'version': instance.version,
        } if not created else None
    )

@receiver(post_save, sender=DocumentationPage)
def create_initial_view_log(sender, instance, created, **kwargs):
    """Create initial view log for new published pages"""
    if created and instance.is_published:
        DocumentationViewLog.objects.create(page=instance)

@receiver(post_delete, sender=DocumentationPage)
def create_delete_log(sender, instance, **kwargs):
    """Create delete log when page is deleted"""
    DocumentationChangeLog.objects.create(
        page=instance,
        action='delete',
        previous_version={
            'title': instance.title,
            'content': instance.content[:500],
            'status': instance.status,
            'version': instance.version,
        }
    )