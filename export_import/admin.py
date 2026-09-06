from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.http import HttpResponseRedirect
import logging
from .models import TimetablePdfTemplate, PdfSignatory, PDFDocument

logger = logging.getLogger(__name__)


class PdfSignatoryInline(admin.TabularInline):
    """Lets admins add/reorder/enable-disable the extra names that appear
    after the director's initials in the signature code, e.g. the 'fm' and
    'sk' in 'GAO/fm/sk' — without touching code."""
    model = PdfSignatory
    extra = 1
    fields = ('full_name', 'initials_code', 'order', 'is_active')
    ordering = ('order', 'id')


@admin.register(TimetablePdfTemplate)
class TimetablePdfTemplateAdmin(admin.ModelAdmin):
    """
    Admin configuration for Timetable PDF Template
    Ensures only one template instance exists
    """
    list_display = ['id', 'university_name', 'get_logo_preview', 'watermark_enabled', 'updated_at']
    list_display_links = ['id', 'university_name']
    readonly_fields = ['created_at', 'updated_at', 'get_logo_preview', 'get_signature_preview', 'get_watermark_preview']
    inlines = [PdfSignatoryInline]

    fieldsets = (
        ('University Logo', {
            'fields': ('university_logo', 'get_logo_preview'),
            'description': 'Upload university logo (recommended size: 150x150px, transparent PNG preferred)'
        }),
        ('University Information', {
            'fields': (
                'university_name', 
                ('motto_latin', 'motto_swahili'),
                'directorate_name',
                ('telephone', 'email'),
                ('address', 'website')
            ),
        }),
        ('Document Formatting', {
            'fields': (
                'reference_prefix',
                'title_format',
                'page_header_format',
            ),
            'description': 'Use {timetable_type} placeholder for dynamic content'
        }),
        ('Footer Content', {
            'fields': (
                'notes',
                'key_section',
                ('prepared_by_label', 'director_label'),
            ),
            'classes': ('wide',)
        }),
        ('Signature Block', {
            'fields': (
                'director_full_name',
                'director_initials',
                'get_signature_preview',
            ),
            'description': (
                "Controls the 'Prepared by ... Director ... GAO/fm/sk' block on generated PDFs. "
                "The director's full name/title is shown in full; add extra preparer names below "
                "as Signatories — each contributes its lowercase initials to the code, in order, "
                "and can be toggled off without deleting the record."
            ),
        }),
        ('Watermark', {
            'fields': (
                'watermark_enabled',
                'get_watermark_preview',
            ),
            'description': (
                "When enabled, generated PDFs carry a faint, tiled directorate watermark with a "
                "unique code per document, derived from a private key that is never shown here — "
                "so it can't be reproduced on a document that didn't come from this system."
            ),
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def get_signature_preview(self, obj):
        """Live preview of the exact 'Prepared by' block that will render on PDFs."""
        if not obj or not obj.pk:
            return "Save the template first to preview the signature block."
        code = obj.get_signature_code()
        return format_html(
            '<div style="font-family: Helvetica, Arial, sans-serif; line-height: 1.5;">'
            '<div>{}</div>'
            '<div><b>{}</b></div>'
            '<div><b>{}</b></div>'
            '<div>{}</div>'
            '</div>',
            obj.prepared_by_label,
            obj.director_full_name,
            obj.director_label,
            code,
        )
    get_signature_preview.short_description = "Signature block preview"

    def get_watermark_preview(self, obj):
        if not obj or not obj.pk:
            return "Save the template first to preview the watermark."
        if not obj.watermark_enabled:
            return "Watermark is currently disabled."
        sample_code = obj.generate_watermark_code("PREVIEW")
        return format_html(
            '<span style="color:#777;">Example code for a document reference of "PREVIEW": '
            '<code>{}</code>. Every real document gets its own code, derived from its own '
            'reference number.</span>',
            sample_code,
        )
    get_watermark_preview.short_description = "Watermark preview"

    def get_logo_preview(self, obj):
        """Display logo preview in admin"""
        if obj.university_logo and obj.university_logo.name:
            try:
                return format_html(
                    '<img src="{}" style="max-height: 100px; max-width: 100px;" />',
                    obj.university_logo.url
                )
            except (ValueError, OSError) as e:
                logger.debug("Logo preview unavailable for template #%s: %s", obj.pk, e)
                return "Logo file not found"
        return "No logo uploaded"
    get_logo_preview.short_description = "Logo Preview"
    
    def has_add_permission(self, request):
        """Prevent adding multiple template instances"""
        # Check if a template already exists
        if TimetablePdfTemplate.objects.exists():
            return False
        return super().has_add_permission(request)
    
    def has_delete_permission(self, request, obj=None):
        """Prevent deletion of the template"""
        return False
    
    def get_actions(self, request):
        """Remove delete action"""
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions


@admin.register(PDFDocument)
class PDFDocumentAdmin(admin.ModelAdmin):
    """
    Admin configuration for PDF Documents
    """
    list_display = [
        'title', 
        'document_type', 
        'academic_year', 
        'semester', 
        'version', 
        'status',
        'display_file_info',  # Changed from get_file_info
        'is_latest',
        'uploaded_at'
    ]
    list_filter = [
        'document_type', 
        'academic_year', 
        'semester', 
        'status', 
        'is_latest',
        'uploaded_at'
    ]
    search_fields = ['title', 'description', 'academic_year']
    readonly_fields = ['file_size', 'uploaded_at', 'updated_at', 'display_file_info', 'get_download_link']
    
    fieldsets = (
        ('Basic Information', {
            'fields': (
                'title',
                'document_type',
                ('academic_year', 'semester'),
                ('version', 'status'),
                'description',
            )
        }),
        ('PDF File', {
            'fields': (
                'pdf_file',
                'file_size',
                'display_file_info',  # Changed from get_file_info
                'get_download_link',
            ),
        }),
        ('Upload Information', {
            'fields': (
                'uploaded_by',
                ('uploaded_at', 'updated_at'),
            ),
            'classes': ('collapse',)
        }),
        ('Version Control', {
            'fields': (
                'is_latest',
                'previous_version',
            ),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['publish_documents', 'archive_documents', 'make_latest']
    
    def display_file_info(self, obj):
        """Display file information - now a method, not a field"""
        if obj.pdf_file:
            filename = obj.filename or "Unknown"
            if obj.file_size:
                # Convert bytes to human readable format
                size = obj.file_size
                for unit in ['B', 'KB', 'MB']:
                    if size < 1024.0:
                        return f"{filename} ({size:.1f} {unit})"
                    size /= 1024.0
                return f"{filename} ({size:.1f} GB)"
            return filename
        return "No file uploaded"
    display_file_info.short_description = "File Information"
    
    def get_download_link(self, obj):
        """Generate download link for admin"""
        if obj.pdf_file and obj.pdf_file.name:
            try:
                return format_html(
                    '<a href="{}" target="_blank" class="button">Download PDF</a>',
                    obj.pdf_file.url
                )
            except (ValueError, OSError) as e:
                logger.debug("Download link unavailable for PDFDocument #%s: %s", obj.pk, e)
                return "File not found"
        return "No file uploaded"
    get_download_link.short_description = "Download"
    
    def publish_documents(self, request, queryset):
        """Action to publish selected documents"""
        updated = queryset.update(status='PUBLISHED')
        self.message_user(request, f"{updated} document(s) marked as published.")
    publish_documents.short_description = "Mark selected as PUBLISHED"
    
    def archive_documents(self, request, queryset):
        """Action to archive selected documents"""
        updated = queryset.update(status='ARCHIVED', is_latest=False)
        self.message_user(request, f"{updated} document(s) archived.")
    archive_documents.short_description = "Mark selected as ARCHIVED"
    
    def make_latest(self, request, queryset):
        """Action to mark selected as latest (handles versioning)"""
        if queryset.count() > 1:
            self.message_user(request, "Please select only one document to mark as latest.", level='ERROR')
            return
        
        doc = queryset.first()
        
        # Update previous latest
        PDFDocument.objects.filter(
            academic_year=doc.academic_year,
            semester=doc.semester,
            document_type=doc.document_type,
            is_latest=True
        ).exclude(pk=doc.pk).update(is_latest=False)
        
        # Mark this as latest
        doc.is_latest = True
        doc.save()
        
        self.message_user(request, f"Document '{doc.title}' marked as latest version.")
    make_latest.short_description = "Mark selected as LATEST version"
    
    def save_model(self, request, obj, form, change):
        """Set uploaded_by on save"""
        if not obj.uploaded_by:
            obj.uploaded_by = request.user.username or request.user.get_full_name() or "System"
        super().save_model(request, obj, form, change)
    
    def get_queryset(self, request):
        """Optimize queryset with select_related"""
        return super().get_queryset(request).select_related('previous_version')