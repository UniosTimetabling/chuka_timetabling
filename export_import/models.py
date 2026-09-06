from django.db import models
from django.conf import settings
import os
import logging
import hmac
import hashlib
import secrets
from django.core.files.storage import FileSystemStorage
from django.core.files.base import ContentFile
import shutil

logger = logging.getLogger(__name__)

def logo_upload_path(instance, filename):
    """Generate upload path for logos"""
    ext = filename.split('.')[-1]
    filename = f'university_logo.{ext}'
    return os.path.join('timetable_logos', filename)

class CustomLogoStorage(FileSystemStorage):
    """Custom storage to handle logo overwrites"""
    def get_available_name(self, name, max_length=None):
        # Delete existing file if it exists
        if self.exists(name):
            self.delete(name)
        return name

class TimetablePdfTemplate(models.Model):
    """Model to store the single PDF template configuration for timetables"""
    
    # LOGO FIELD (NEW)
    university_logo = models.ImageField(
        upload_to=logo_upload_path,
        storage=CustomLogoStorage(),
        blank=True,
        null=True,
        help_text="University logo (recommended size: 150x150px, transparent PNG preferred)"
    )
    
    # UNIVERSITY HEADER INFORMATION
    university_name = models.CharField(
        max_length=500,
        default="Chuka University",
        help_text="University name"
    )
    motto_latin = models.CharField(
        max_length=255,
        default="Sapientia divitia est",
        help_text="Latin motto"
    )
    motto_swahili = models.CharField(
        max_length=255,
        default="Akili ni Mali",
        help_text="Swahili motto"
    )
    directorate_name = models.CharField(
        max_length=500,
        default="DIRECTORATE OF EXAMINATIONS AND TIMETABLING",
        help_text="Directorate name"
    )
    telephone = models.CharField(
        max_length=100,
        default="020-2310512/18",
        help_text="Telephone number"
    )
    address = models.CharField(
        max_length=500,
        default="P. O. Box 109-60400, Chuka",
        help_text="Physical address"
    )
    email = models.EmailField(
        default="extt@chuka.ac.ke",
        help_text="Contact email"
    )
    website = models.URLField(
        default="https://www.chuka.ac.ke",
        help_text="University website"
    )
    
    # REFERENCE AND DATE FORMATS
    reference_prefix = models.CharField(
        max_length=100,
        default="Ref: CU/EXTT/",
        help_text="Prefix for reference numbers"
    )
    
    # TITLE FORMAT - Will use {timetable_type} placeholder
    title_format = models.CharField(
        max_length=500,
        default="{timetable_type} Timetable",
        help_text="Title format. Use {timetable_type} placeholder"
    )
    
    # PAGE HEADER FORMAT
    page_header_format = models.CharField(
        max_length=500,
        default="{timetable_type} Page {page} of {total_pages}",
        help_text="Page header format"
    )
    
    # FOOTER NOTES
    notes = models.TextField(
        default="NB: All queries to be channeled via email address (extt@chuka.ac.ke) or visit the office at Science Complex (S102)",
        help_text="Important notes/instructions at bottom"
    )
    
    # KEY/LEGEND SECTION
    key_section = models.TextField(
        default="""KEY: 
- PAV H (PAVILION HALL UPSTAIRS)
- MSH 03 - 10 (MALE STUDENTS' HOSTEL COMMON ROOMS)
- MS (01- 34) - MEDIA SCHOOL COMPLEX
- S (SGT1 - S602) - SCIENCE COMPLEX
- BSL (BSL 001- 503) BUSINESS SCHOOL LEFT WING
- BSR (BSR 001- 503) BUSINESS SCHOOL RIGHT WING
- LLB 1- 3: FACULTY OF LAW (BSRC COMPLEX)
- L1 - L4: AREA NEAR EGERTON HOUSE
- FTC B01 - FTC 605: FOOD TECHNOLOGY CENTRE
- SRP B 01- SRP 305: SCIENCE RESEARCH PARK COMPLEX""",
        help_text="Key/legend for room codes"
    )
    
    # SIGNATURE SECTION
    prepared_by_label = models.CharField(
        max_length=100,
        default="Prepared by:",
        help_text="Label for prepared by section"
    )
    director_label = models.CharField(
        max_length=200,
        default="Director (Examinations and Timetabling)",
        help_text="Label for director signature"
    )
    director_full_name = models.CharField(
        max_length=255,
        default="Prof. Grace Abucheli, Ph.D",
        help_text="Director's full name with title/qualifications, as it should appear on the "
                   "signature line, e.g. 'Prof. Grace Abucheli, Ph.D'"
    )
    director_initials = models.CharField(
        max_length=10,
        default="GAO",
        help_text="Director's initials shown UPPERCASE at the start of the reference code, "
                   "e.g. 'GAO' in 'GAO/fm/sk'"
    )

    # WATERMARK
    watermark_enabled = models.BooleanField(
        default=True,
        help_text="Show the directorate watermark on generated PDFs"
    )
    watermark_secret = models.CharField(
        max_length=64,
        blank=True,
        editable=False,
        help_text="Auto-generated secret key used to derive a unique, non-forgeable watermark "
                   "code per document. Never exposed in full; not manually editable."
    )

    # METADATA
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Timetable PDF Template"
        verbose_name_plural = "Timetable PDF Template"
    
    def __str__(self):
        return f"Timetable PDF Template (Updated: {self.updated_at.date()})"
    
    def save(self, *args, **kwargs):
        """Override save to set default logo if not exists, and to make sure
        every template row has its own private watermark secret."""
        is_new = self.pk is None

        if not self.watermark_secret:
            self.watermark_secret = secrets.token_hex(24)

        # Call parent save first
        super().save(*args, **kwargs)
        
        # If this is a new instance and has no logo, set default
        if is_new and not self.university_logo:
            self.set_default_logo()
    
    def set_default_logo(self):
        """Set a default logo from static files"""
        try:
            # Path to default logo in static files
            static_dir = os.path.join(settings.BASE_DIR, 'static')
            default_logo_path = os.path.join(static_dir, 'images', 'chuka.png')
            
            # Check if default logo exists in static
            if os.path.exists(default_logo_path):
                # Open the default logo file
                with open(default_logo_path, 'rb') as f:
                    # Save to the ImageField
                    filename = os.path.basename(default_logo_path)
                    self.university_logo.save(filename, ContentFile(f.read()), save=True)
                return True
            else:
                # Create a simple text-based logo as fallback
                self.create_text_logo()
                return True
        except Exception as e:
            print(f"Error setting default logo: {e}")
            return False
    
    def create_text_logo(self):
        """Create a simple text-based logo as fallback"""
        try:
            # Create a simple text logo using PIL (if available) or just save an empty file
            # We'll create a simple text file as fallback
            logo_content = b"Chuka University Logo"
            self.university_logo.save(
                'default_logo.txt', 
                ContentFile(logo_content), 
                save=True
            )
            return True
        except Exception as e:
            logger.exception("create_text_logo: all fallbacks failed for template #%s: %s", self.pk, e)
            # If all else fails, just save with empty file
            self.university_logo.save('logo.png', ContentFile(b''), save=False)
            super().save(update_fields=['university_logo'])
            return False
    
    @classmethod
    def get_template(cls):
        """Get the single template instance, create if doesn't exist"""
        try:
            template = cls.objects.get(id=1)
        except cls.DoesNotExist:
            # Create the template instance
            template = cls.objects.create(
                id=1,
                university_name='Chuka University',
                motto_latin='Sapientia divitia est',
                motto_swahili='Akili ni Mali',
                directorate_name='DIRECTORATE OF EXAMINATIONS AND TIMETABLING',
                telephone='020-2310512/18',
                address='P. O. Box 109-60400, Chuka',
                email='extt@chuka.ac.ke',
                website='https://www.chuka.ac.ke',
                reference_prefix='Ref: CU/EXTT/',
                title_format='{timetable_type} Timetable',
                page_header_format='{timetable_type} Page {page} of {total_pages}',
                notes='NB: All queries to be channeled via email address (extt@chuka.ac.ke) or visit the office at Science Complex (S102)',
                key_section="""KEY: 
- PAV H (PAVILION HALL UPSTAIRS)
- MSH 03 - 10 (MALE STUDENTS' HOSTEL COMMON ROOMS)
- MS (01- 34) - MEDIA SCHOOL COMPLEX
- S (SGT1 - S602) - SCIENCE COMPLEX
- BSL (BSL 001- 503) BUSINESS SCHOOL LEFT WING
- BSR (BSR 001- 503) BUSINESS SCHOOL RIGHT WING
- LLB 1- 3: FACULTY OF LAW (BSRC COMPLEX)
- L1 - L4: AREA NEAR EGERTON HOUSE
- FTC B01 - FTC 605: FOOD TECHNOLOGY CENTRE
- SRP B 01- SRP 305: SCIENCE RESEARCH PARK COMPLEX""",
                prepared_by_label='Prepared by:',
                director_label='Director (Examinations and Timetabling)',
            )
            
            # Try to set default logo after creation
            if not template.university_logo:
                template.set_default_logo()
        
        return template
    
    def get_logo_url(self):
        """Get logo URL safely"""
        if self.university_logo and self.university_logo.name:
            try:
                return self.university_logo.url
            except (ValueError, AttributeError):
                # Return path to default static logo if no logo uploaded
                return '/static/images/chuka.png'
        # Return default static logo path
        return '/static/images/chuka.png'
    
    def get_header_data(self, timetable_type="Regular"):
        """Get formatted header data with timetable type"""
        return {
            'logo_url': self.get_logo_url(),
            'logo': self.university_logo,  # Keep for backward compatibility
            'logo_text': 'Knowledge is Wealth',
            'motto_latin': self.motto_latin,
            'motto_swahili': self.motto_swahili,
            'university_name': self.university_name,
            'directorate_name': self.directorate_name,
            'telephone': self.telephone,
            'address': self.address,
            'email': self.email,
            'website': self.website,
            'title': self.title_format.format(timetable_type=timetable_type),
        }
    
    def get_footer_data(self, prepared_by="", director_initials=None, sub_director_initials=None):
        """
        Get formatted footer data for the signature block.

        Historically callers passed hardcoded director_initials/sub_director_initials
        to build a fake "DIR/EXT"-style code. Those params are kept for backward
        compatibility (pass them to force a specific 2-part code), but by default
        the reference code is now built from the director's real initials plus
        the active PdfSignatory rows configured in admin, e.g. "GAO/fm/sk".
        `prepared_by` now defaults to the director's full name/designation rather
        than a raw directorate string.
        """
        if director_initials is not None or sub_director_initials is not None:
            parts = [director_initials or self.director_initials, sub_director_initials or ""]
            signature = "/".join(p for p in parts if p)
        else:
            signature = self.get_signature_code()

        return {
            'notes': self.notes,
            'key_section': self.key_section,
            'prepared_by_label': self.prepared_by_label,
            'prepared_by': prepared_by or self.director_full_name,
            'director_full_name': self.director_full_name,
            'director_label': self.director_label,
            'signature': signature,
            'signature_code': signature,
        }

    def get_signature_code(self):
        """
        Build the "GAO/fm/sk" style reference code: the director's initials
        in UPPERCASE, followed by each active PdfSignatory's lowercase
        initials, in their configured order.
        """
        parts = [(self.director_initials or "").strip().upper()]
        parts += [
            s.initials_code
            for s in self.signatories.filter(is_active=True).order_by('order', 'id')
            if s.initials_code
        ]
        return "/".join(p for p in parts if p)

    def get_signature_block(self, prepared_by=None):
        """Convenience accessor bundling everything a PDF template needs to
        render the 'Prepared by ... Director ... GAO/fm/sk' block."""
        return {
            'prepared_by_label': self.prepared_by_label,
            'director_full_name': self.director_full_name,
            'director_label': self.director_label,
            'signature_code': self.get_signature_code(),
        }

    # ── WATERMARK ────────────────────────────────────────────────────────
    def generate_watermark_code(self, doc_ref=""):
        """
        Derive a short, unique watermark code from this template's private
        secret plus a document reference (e.g. the PDF's reference number).
        Because the secret is never exposed, nobody can reproduce a valid
        code for a document without generating it through this app first,
        which is what makes the watermark hard to copy onto another PDF.
        """
        key = (self.watermark_secret or "").encode("utf-8")
        msg = str(doc_ref or "").encode("utf-8")
        digest = hmac.new(key, msg, hashlib.sha256).hexdigest().upper()
        return f"CU-EXTT-{digest[:10]}"

    def verify_watermark_code(self, doc_ref, code):
        """Check whether `code` is the genuine watermark code for `doc_ref`
        under this template's secret. Useful for a future 'verify this PDF'
        endpoint."""
        expected = self.generate_watermark_code(doc_ref)
        return hmac.compare_digest(expected, (code or "").strip().upper())

    def get_reference_number(self, date_str):
        """Generate reference number with date"""
        return f"{self.reference_prefix}{date_str}"
    
    def get_page_header(self, timetable_type, page, total_pages):
        """Generate page header"""
        return self.page_header_format.format(
            timetable_type=timetable_type,
            page=page,
            total_pages=total_pages
        )
    
class PdfSignatory(models.Model):
    """
    An additional name to include in the PDF signature block, appended as
    lowercase initials after the director's code — e.g. the 'fm' and 'sk'
    in 'GAO/fm/sk'. Untick `is_active` to remove someone from the code
    without deleting their record (so it can be re-enabled later).
    """
    template = models.ForeignKey(
        TimetablePdfTemplate,
        on_delete=models.CASCADE,
        related_name='signatories',
    )
    full_name = models.CharField(
        max_length=200,
        help_text="Full name, for reference only, e.g. 'Fred Mwangi'"
    )
    initials_code = models.CharField(
        max_length=6,
        help_text="Lowercase initials as they should appear in the signature code, e.g. 'fm'"
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Controls left-to-right position after the director's initials"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Untick to exclude this person from the signature code without deleting them"
    )

    class Meta:
        ordering = ['order', 'id']
        verbose_name = "PDF Signatory"
        verbose_name_plural = "PDF Signatories"

    def __str__(self):
        return f"{self.full_name} ({self.initials_code})"

    def save(self, *args, **kwargs):
        if self.initials_code:
            self.initials_code = self.initials_code.strip().lower()
        super().save(*args, **kwargs)


class PDFDocument(models.Model):
    """
    Stores PDF documents for both examination timetables and regular timetables.
    Provides easy access to uploaded PDF files with metadata tracking.
    """
    
    DOCUMENT_TYPES = [
        ('EXAM', 'Examination Timetable'),
        ('REGULAR', 'Regular Timetable'),
    ]
    
    SEMESTER_CHOICES = [
        ('1', '1st Semester'),
        ('2', '2nd Semester'),
    ]
    
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('PUBLISHED', 'Published'),
        ('ARCHIVED', 'Archived'),
        ('SUPERSEDED', 'Superseded'),
    ]
    
    # Core fields
    title = models.CharField(
        max_length=200,
        help_text="Title of the document (e.g., '2024/2025 1st Semester Examination Timetable')"
    )
    document_type = models.CharField(
        max_length=20,
        choices=DOCUMENT_TYPES,
        help_text="Type of timetable document"
    )
    
    # PDF file storage
    pdf_file = models.FileField(
        upload_to='timetables/pdfs/%Y/%m/%d/',
        help_text="Upload the PDF document"
    )
    file_size = models.PositiveIntegerField(
        default=0,
        help_text="File size in bytes"
    )
    
    # Metadata
    academic_year = models.CharField(
        max_length=15,
        help_text="e.g., 2024/2025"
    )
    semester = models.CharField(
        max_length=2,
        choices=SEMESTER_CHOICES
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number of the document"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='DRAFT'
    )
    
    # Upload information
    uploaded_by = models.CharField(
        max_length=100,
        blank=True,
        help_text="Name or username of the person who uploaded the document"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Document description and notes
    description = models.TextField(
        blank=True,
        help_text="Brief description or notes about this document"
    )
    
    # For document management
    is_latest = models.BooleanField(
        default=True,
        help_text="Is this the latest version of this document type for this academic year/semester?"
    )
    previous_version = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='next_version',
        help_text="Previous version of this document"
    )
    
    class Meta:
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['academic_year', 'semester', 'document_type']),
            models.Index(fields=['status', 'uploaded_at']),
            models.Index(fields=['is_latest']),
        ]
        verbose_name = "PDF Document"
        verbose_name_plural = "PDF Documents"
        permissions = [
            ("can_upload_pdf", "Can upload PDF documents"),
            ("can_publish_pdf", "Can publish PDF documents"),
            ("can_archive_pdf", "Can archive PDF documents"),
        ]
    
    def __str__(self):
        return f"{self.title} (v{self.version}) - {self.get_status_display()}"
    
    def save(self, *args, **kwargs):
        """Override save to handle file size and versioning."""
        if self.pdf_file and not self.file_size:
            try:
                self.file_size = self.pdf_file.size
            except (OSError, ValueError) as e:
                logger.debug("Could not determine file_size for PDFDocument: %s", e)
        
        # Handle versioning and latest flag
        if not self.pk:  # New document
            # Check if this should be a new version
            latest = PDFDocument.objects.filter(
                academic_year=self.academic_year,
                semester=self.semester,
                document_type=self.document_type,
                is_latest=True
            ).first()
            
            if latest:
                self.version = latest.version + 1
                self.previous_version = latest
                latest.is_latest = False
                latest.save()
        
        super().save(*args, **kwargs)
    
    def get_download_url(self):
        """Return the URL to download the PDF."""
        if self.pdf_file:
            return self.pdf_file.url
        return None
    
    def get_file_extension(self):
        """Return the file extension."""
        if self.pdf_file:
            name = self.pdf_file.name
            if '.' in name:
                return name.split('.')[-1].lower()
        return None
    
    @property
    def filename(self):
        """Return the filename of the PDF."""
        if self.pdf_file:
            return self.pdf_file.name.split('/')[-1]
        return None
    
    @classmethod
    def get_latest_for_semester(cls, academic_year, semester, document_type='EXAM'):
        """Get the latest published PDF for a specific semester and document type."""
        return cls.objects.filter(
            academic_year=academic_year,
            semester=semester,
            document_type=document_type,
            status='PUBLISHED',
            is_latest=True
        ).first()


def program_year_pdf_upload_path(instance, filename):
    """timetables/generated/<department_id>/<program_id>/<year>/<class|exam>.pdf"""
    return (
        f"timetables/generated/{instance.department_id}/{instance.program_id}/"
        f"{instance.year}/{instance.timetable_type}.pdf"
    )


class GeneratedTimetablePDF(models.Model):
    """
    Per (department, program, year) cached timetable PDF — built so a
    request for "the Year 2 Computer Science timetable" can be handed
    back instantly instead of being built on every single request.

    Used by the REMOTE side of the host/remote sync feature (see
    core.models.SyncNode / export_import.sync_engine): whenever fresh
    data lands — either pushed in from a HOST via receive_sync, or
    edited locally — the affected scope's row here is deleted and
    regenerated in the background (export_import.program_year_pdf_cache).
    The public-facing endpoint only ever serves a row that exists and
    is marked ready, so it never hands out a URL to a file that isn't
    actually there yet.
    """
    TYPE_CHOICES = [
        ("class", "Class Timetable"),
        ("exam", "Exam Timetable"),
    ]

    department = models.ForeignKey(
        "department_management.Department", on_delete=models.CASCADE,
        related_name="generated_timetable_pdfs",
    )
    program = models.ForeignKey(
        "program_management.Program", on_delete=models.CASCADE,
        related_name="generated_timetable_pdfs",
    )
    year = models.PositiveSmallIntegerField()
    timetable_type = models.CharField(max_length=10, choices=TYPE_CHOICES)

    pdf_file = models.FileField(upload_to=program_year_pdf_upload_path, blank=True, null=True)
    is_ready = models.BooleanField(
        default=False,
        help_text="False while a (re)generation is in progress — the serving "
                   "view will not hand out a not-yet-written file.",
    )
    row_count = models.PositiveIntegerField(
        default=0, help_text="Number of scheduled entries in the last successful build."
    )
    content_hash = models.CharField(
        max_length=64, blank=True, default="",
        help_text="Hash of the source rows used for this build, so an unchanged "
                   "scope can be skipped during a full regeneration sweep.",
    )
    generated_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("department", "program", "year", "timetable_type")
        indexes = [models.Index(fields=["department", "program", "year", "timetable_type"])]
        verbose_name = "Generated Timetable PDF"
        verbose_name_plural = "Generated Timetable PDFs"

    def __str__(self):
        state = "ready" if self.is_ready and self.pdf_file else "pending"
        return f"{self.department.name} / {self.program.name} / Yr{self.year} / {self.timetable_type} ({state})"

