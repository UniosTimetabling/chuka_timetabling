from django.contrib import admin
from django.utils.html import format_html
from .models import (
    DocumentationCategory,
    DocumentationPage,
    DocumentationSection,
    CodeExample,
    InternalLink,
    DocumentationViewLog
)

@admin.register(DocumentationCategory)
class DocumentationCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'order', 'is_active', 'access_level', 'page_count']
    list_filter = ['is_active', 'access_level']
    search_fields = ['name', 'description']
    prepopulated_fields = {'slug': ('name',)}
    ordering = ['order', 'name']
    
    def page_count(self, obj):
        return obj.get_published_pages_count()
    page_count.short_description = 'Pages'

@admin.register(DocumentationPage)
class DocumentationPageAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'page_type', 'status', 'is_published', 'views', 'updated_at']
    list_filter = ['category', 'page_type', 'status', 'is_published', 'access_level']
    search_fields = ['title', 'short_description', 'content']
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ['views', 'created_at', 'updated_at', 'published_at']
    filter_horizontal = ['prerequisites', 'related_pages']
    ordering = ['category__order', 'order', 'title']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('title', 'slug', 'short_description', 'content', 'category')
        }),
        ('Metadata', {
            'fields': ('page_type', 'status', 'difficulty', 'order', 'estimated_read_time', 'version')
        }),
        ('Access Control', {
            'fields': ('is_published', 'requires_login', 'access_level')
        }),
        ('Relations', {
            'fields': ('prerequisites', 'related_pages')
        }),
        ('Audit Information', {
            'fields': ('author', 'last_edited_by', 'approved_by', 'approved_at',
                      'views', 'created_at', 'updated_at', 'published_at')
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not obj.author_id:
            obj.author = request.user
        obj.last_edited_by = request.user
        super().save_model(request, obj, form, change)

@admin.register(DocumentationSection)
class DocumentationSectionAdmin(admin.ModelAdmin):
    list_display = ['title', 'page', 'order', 'is_active']
    list_filter = ['page', 'is_active']
    search_fields = ['title', 'content']
    ordering = ['page', 'order']

@admin.register(CodeExample)
class CodeExampleAdmin(admin.ModelAdmin):
    list_display = ['title', 'section', 'language', 'order']
    list_filter = ['language', 'section__page']
    search_fields = ['title', 'code', 'description']
    ordering = ['section', 'order']

@admin.register(DocumentationViewLog)
class DocumentationViewLogAdmin(admin.ModelAdmin):
    list_display = ['page', 'user', 'timestamp', 'time_spent']
    list_filter = ['timestamp', 'page']
    search_fields = ['page__title', 'user__username']
    readonly_fields = ['page', 'user', 'ip_address', 'user_agent', 'timestamp']
    ordering = ['-timestamp']
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False

@admin.register(InternalLink)
class InternalLinkAdmin(admin.ModelAdmin):
    list_display = ['from_page', 'to_page', 'description']
    list_filter = ['from_page', 'to_page']
    search_fields = ['description', 'from_page__title', 'to_page__title']