# core/admin.py
# ===================================================================
#  FULL PRODUCTION-READY ADMIN FILE
#  Contains all admin registrations for core models.
#  Lecturer constraint models are imported from timetable.models
#  where they belong.
# ===================================================================

import json

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils.html import format_html
from django.db.models.fields.files import FieldFile   # ← for safe file checks

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget

# ── Core Models ──────────────────────────────────────────────────────────────
from .models import (
    OrgRole,
    CotUserProfile,
    ActivityLog,
    SiteSettings,
    SitePhoto,
    AIProviderSettings,
    SchedulerConstraintToggle,
    SyncNode,
    SyncRun,
    SyncModelLog,
    SyncRecordState,
)

# ── Timetable Models (Lecturer Constraints are here) ──────────────────────
from timetable.models import (
    LecturerBlockedSlot,
    LecturerTimePreference,
    LecturerTimePreferenceSlot,
    LecturerVenuePreference,
)

# ── Department Management ──────────────────────────────────────────────────
from department_management.models import Department


# =====================================================
# CUSTOM USER ADMIN — fixes MySQL FK constraint error
# when deleting users that have ActivityLog rows.
#
# Root cause: Django admin uses queryset.delete() (bulk
# delete) which bypasses per-instance signals entirely.
# MySQL enforces the FK constraint before Django's ORM
# SET_NULL cascade can run, causing an IntegrityError.
#
# Fix: override delete_queryset() to nullify the user FK
# on all related ActivityLog rows BEFORE the bulk delete
# hits the database, so MySQL sees no constraint violation.
# =====================================================

class SafeDeleteUserAdmin(UserAdmin):
    """
    MySQL enforces FK constraints at the DB level before Django's ORM
    cascade/SET_NULL logic can run, causing IntegrityError on user delete.

    Fix: temporarily disable FK checks for the duration of the delete,
    letting MySQL's own ON DELETE CASCADE / SET NULL rules handle cleanup
    exactly as the schema intends — without Django's collector getting
    in the way.
    """

    def _delete_users(self, queryset):
        from django.db import connection
        user_ids = list(queryset.values_list("pk", flat=True))
        if not user_ids:
            return
        with connection.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
            try:
                placeholders = ",".join(["%s"] * len(user_ids))
                cursor.execute(
                    f"DELETE FROM auth_user WHERE id IN ({placeholders})",
                    user_ids,
                )
            finally:
                cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")

    def delete_queryset(self, request, queryset):
        self._delete_users(queryset)

    def delete_model(self, request, obj):
        self._delete_users(obj.__class__.objects.filter(pk=obj.pk))


admin.site.unregister(User)
admin.site.register(User, SafeDeleteUserAdmin)


# =====================================================
# HELPERS
# =====================================================

def _field_url(field):
    """
    Safely return the URL of a FieldFile, or None if no file is attached.

    Why not `if field:` or `if field.name:`?
    - `bool(field)` calls FieldFile.__bool__ which calls _require_file()
      → raises ValueError on an empty field.
    - `hasattr(field, "url")` only suppresses AttributeError; Django raises
      ValueError from FieldFile.url when empty, so hasattr lets it through.

    The only safe pattern is:
        isinstance(field, FieldFile) and bool(field.name)
    because .name is a plain string attribute that never raises.
    """
    if isinstance(field, FieldFile) and field.name:
        try:
            return field.url
        except Exception:
            return None
    return None


# =====================================================
# ADMIN ROW ACTION MIXIN
# =====================================================

class RowActionMixin:
    """Adds Edit / Delete buttons on the right side of admin list rows."""

    def row_actions(self, obj):
        opts = self.model._meta
        edit_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_change",
            args=[obj.pk],
        )
        delete_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_delete",
            args=[obj.pk],
        )
        return format_html(
            """
            <div class="row-actions">
                <a class="button edit" href="{}">Edit</a>
                <a class="button delete" href="{}">Delete</a>
            </div>
            """,
            edit_url,
            delete_url,
        )

    row_actions.short_description = "Actions"


# =====================================================
# BASE IMPORT-EXPORT RESOURCE
# =====================================================

class SafeBaseResource(resources.ModelResource):
    """
    - Ignores extra columns in import files
    - Normalises string values by stripping whitespace
    """

    def before_import_row(self, row, **kwargs):
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.strip()


# ==============================
# RESOURCE CLASSES
# ==============================

class OrgRoleResource(SafeBaseResource):
    user = fields.Field(
        column_name="user",
        attribute="user",
        widget=ForeignKeyWidget(User, "username"),
    )

    class Meta:
        model = OrgRole
        import_id_fields = ("title",)
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        super().before_import_row(row, **kwargs)
        if row.get("title"):
            row["title"] = row["title"].upper()
        if row.get("user"):
            row["user"] = row["user"].lower()


class CotUserProfileResource(SafeBaseResource):
    user = fields.Field(
        column_name="user",
        attribute="user",
        widget=ForeignKeyWidget(User, "username"),
    )
    department = fields.Field(
        column_name="department",
        attribute="department",
        widget=ForeignKeyWidget(Department, "name"),
    )

    class Meta:
        model = CotUserProfile
        import_id_fields = ("user",)
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        super().before_import_row(row, **kwargs)
        if row.get("user"):
            row["user"] = row["user"].lower()
        if row.get("department"):
            row["department"] = row["department"].title()


class ActivityLogResource(SafeBaseResource):
    user = fields.Field(
        column_name="user",
        attribute="user",
        widget=ForeignKeyWidget(User, "username"),
    )

    class Meta:
        model = ActivityLog
        exclude = ("id",)
        skip_unchanged = True
        report_skipped = True
        export_order_by = ("-timestamp",)

    def before_import_row(self, row, **kwargs):
        super().before_import_row(row, **kwargs)
        if not row.get("user"):
            row["user"] = None
        if row.get("action"):
            row["action"] = row["action"].upper()
        if row.get("app_label"):
            row["app_label"] = row["app_label"].lower()
        if row.get("model_name"):
            row["model_name"] = row["model_name"].lower()


# ==============================
# EXISTING MODEL ADMINS
# ==============================

@admin.register(OrgRole)
class OrgRoleAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = OrgRoleResource

    list_display = (
        "title",
        "user_display",
        "user_email",
        "department",
        "faculty",
        "date_joined",
        "row_actions",
    )
    list_display_links = ("title",)
    list_filter = ("department", "faculty")
    search_fields = (
        "title",
        "user__username",
        "user__email",
        "user__first_name",
        "user__last_name",
        "department__name",
        "faculty__name",
    )
    ordering = ("title",)
    list_per_page = 25
    readonly_fields = ("user_info",)
    autocomplete_fields = ("department", "faculty")

    # This is where a COD Admin / Dean Admin actually gets scoped when
    # created straight from Django admin — previously there was NO field
    # here to pick a department/faculty at all, so admin-created accounts
    # had no way to be linked to the org unit they administer, and were
    # later rejected by department-scoped views with
    # "No department associated with your account."
    fieldsets = (
        ("Role", {"fields": ("title",)}),
        ("User Assignment", {"fields": ("user", "user_info")}),
        ("Scope", {
            "fields": ("department", "faculty"),
            "description": (
                "Set the department this role administers (COD / COD Admin), "
                "or the faculty (Dean / Dean Admin). Leave both blank for "
                "roles that aren't department/faculty-scoped (DVC, Timetabler, etc.)."
            ),
        }),
    )

    def user_display(self, obj):
        if obj.user:
            name = obj.user.get_full_name()
            return f"{obj.user.username} ({name})" if name else obj.user.username
        return "-"
    user_display.short_description = "User"

    def user_email(self, obj):
        return obj.user.email if obj.user else "-"
    user_email.short_description = "Email"

    def date_joined(self, obj):
        return obj.user.date_joined.date() if obj.user else "-"
    date_joined.short_description = "Joined"

    def user_info(self, obj):
        if not obj.user:
            return "No user assigned"
        return format_html(
            "<br>".join([
                f"<b>Username:</b> {obj.user.username}",
                f"<b>Full name:</b> {obj.user.get_full_name()}",
                f"<b>Email:</b> {obj.user.email}",
                f"<b>Joined:</b> {obj.user.date_joined}",
                f"<b>Last login:</b> {obj.user.last_login or 'Never'}",
            ])
        )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


@admin.register(CotUserProfile)
class CotUserProfileAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = CotUserProfileResource

    list_display = (
        "user_display",
        "department",
        "user_email",
        "row_actions",
    )
    list_display_links = ("user_display",)
    list_filter = ("department",)
    search_fields = (
        "user__username",
        "user__email",
        "user__first_name",
        "user__last_name",
        "department__name",
    )
    list_per_page = 25
    readonly_fields = ("user_info",)

    fieldsets = (
        ("User", {"fields": ("user", "user_info")}),
        ("Department", {"fields": ("department",)}),
    )

    def user_display(self, obj):
        if obj.user:
            name = obj.user.get_full_name()
            return f"{obj.user.username} ({name})" if name else obj.user.username
        return "-"
    user_display.short_description = "User"

    def user_email(self, obj):
        return obj.user.email if obj.user else "-"
    user_email.short_description = "Email"

    def user_info(self, obj):
        if not obj.user:
            return "No user"
        return format_html(
            "<br>".join([
                f"<b>Username:</b> {obj.user.username}",
                f"<b>Full name:</b> {obj.user.get_full_name()}",
                f"<b>Email:</b> {obj.user.email}",
                f"<b>Joined:</b> {obj.user.date_joined}",
            ])
        )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user", "department")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


@admin.register(ActivityLog)
class ActivityLogAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = ActivityLogResource

    list_display = (
        "timestamp",
        "user_display",
        "action_badge",
        "model_display",
        "data_preview",
        "row_actions",
    )
    list_display_links = ("timestamp",)
    list_filter = ("action", "app_label", "model_name", "timestamp")
    search_fields = (
        "user__username",
        "action",
        "app_label",
        "model_name",
        "object_id",
    )
    ordering = ("-timestamp",)
    list_per_page = 25
    readonly_fields = ("timestamp", "formatted_data")

    def user_display(self, obj):
        return obj.user.username if obj.user else "System"
    user_display.short_description = "User"

    def action_badge(self, obj):
        colors = {
            "CREATE": "#4CAF50",
            "UPDATE": "#2196F3",
            "DELETE": "#F44336",
            "LOGIN": "#9C27B0",
            "LOGOUT": "#607D8B",
        }
        return format_html(
            '<span style="background:{};color:white;padding:3px 10px;border-radius:12px;">{}</span>',
            colors.get(obj.action, "#757575"),
            obj.action,
        )
    action_badge.short_description = "Action"

    def model_display(self, obj):
        return f"{obj.app_label}.{obj.model_name} (#{obj.object_id})"
    model_display.short_description = "Object"

    def data_preview(self, obj):
        if obj.data:
            text = json.dumps(obj.data, ensure_ascii=False)
            return text[:80] + "..." if len(text) > 80 else text
        return "-"
    data_preview.short_description = "Data"

    def formatted_data(self, obj):
        if obj.data:
            return format_html(
                "<pre style='background:#f5f5f5;padding:10px;border-radius:6px'>{}</pre>",
                json.dumps(obj.data, indent=2, ensure_ascii=False),
            )
        return "No data"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


# ==============================
# SITE SETTINGS ADMIN
# ==============================

@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """
    Singleton admin — only pk=1 ever exists.
    'Add' button is hidden; 'Delete' is disabled.
    changelist_view auto-redirects to the single settings change form.
    """

    fieldsets = (
        ("University Identity", {
            "fields": ("university_name", "tagline", "logo", "logo_preview"),
        }),
        ("Mobile App", {
            "fields": ("mobile_apk", "mobile_apk_version", "mobile_apk_preview"),
        }),
        ("Contact Details", {
            "fields": (
                "contact_email",
                "contact_phone",
                "contact_location",
                "website_url",
            ),
        }),
        ("Social Media", {
            "classes": ("collapse",),
            "fields": (
                "twitter_url",
                "facebook_url",
                "linkedin_url",
                "youtube_url",
            ),
        }),
        ("Meta", {
            "classes": ("collapse",),
            "fields": ("updated_at",),
        }),
    )

    readonly_fields = ("updated_at", "logo_preview", "mobile_apk_preview")

    # ── helpers ──────────────────────────────────────────────────────────

    def mobile_apk_preview(self, obj):
        """
        Show the currently-uploaded APK's size and a direct link, or a note
        that none is uploaded yet. Uses _field_url() for the same reason
        logo_preview does — an empty FileField raises on direct access.
        """
        url = _field_url(obj.mobile_apk)
        if url:
            try:
                size_mb = obj.mobile_apk.size / (1024 * 1024)
                size_label = f"{size_mb:.1f} MB"
            except (OSError, ValueError):
                size_label = "size unknown"
            return format_html(
                '<a href="{}" target="_blank" rel="noopener">{}</a> &nbsp; '
                '<span style="color:#888;font-size:12px">({})</span>',
                url, url.rsplit("/", 1)[-1], size_label,
            )
        return format_html(
            '<p style="color:#888;font-size:12px;margin:0">No APK uploaded yet — '
            'the "Download the App" link on the timetable page stays hidden until one is.</p>'
        )
    mobile_apk_preview.short_description = "Current APK"

    def logo_preview(self, obj):
        """
        Render current logo thumbnail, or a fallback SVG badge.
        Uses _field_url() so accessing an empty ImageField never raises.
        """
        url = _field_url(obj.logo)
        if url:
            return format_html(
                '<img src="{}" style="max-height:80px;border-radius:6px;'
                'border:1px solid #ddd;padding:4px;background:#fff">',
                url,
            )
        return format_html(
            '<svg width="60" height="60" viewBox="0 0 60 60" '
            'xmlns="http://www.w3.org/2000/svg" style="border-radius:8px">'
            '<rect width="60" height="60" rx="8" fill="#005a48"/>'
            '<text x="30" y="38" text-anchor="middle" font-family="sans-serif" '
            'font-weight="700" font-size="18" fill="#b38b4a">CU</text>'
            '</svg>'
            '<p style="color:#888;font-size:12px;margin-top:6px">No logo uploaded yet</p>'
        )
    logo_preview.short_description = "Current logo"

    # ── singleton enforcement ─────────────────────────────────────────────

    def has_add_permission(self, request):
        """Prevent creating a second row."""
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        """Never allow deleting the settings row."""
        return False

    def changelist_view(self, request, extra_context=None):
        """
        Auto-redirect to the single SiteSettings change form.

        Avoids get_or_create() because that path calls ORM create() which
        fires post_save → log_activity → make_json_safe → FieldFile.url
        → ValueError when no logo exists yet (before signals.py is patched).

        Using a plain get() / save() is safer and avoids that signal chain
        on first boot while the table row doesn't exist yet.
        """
        from django.shortcuts import redirect

        try:
            obj = SiteSettings.objects.get(pk=1)
        except SiteSettings.DoesNotExist:
            obj = SiteSettings(pk=1)
            obj.save()   # signals.py fix means this is now safe too

        return redirect(
            reverse("admin:core_sitesettings_change", args=[obj.pk])
        )


# ==============================
# SITE PHOTO ADMIN
# ==============================

class SitePhotoInline(admin.TabularInline):
    model = SitePhoto
    extra = 1
    fields = ("image", "caption", "order", "is_active")


@admin.register(SitePhoto)
class SitePhotoAdmin(admin.ModelAdmin):
    """
    Gallery photo admin.
    - Thumbnails in list view
    - order and is_active are editable directly on the list page
    - Sorted by order so staff see exactly what the carousel shows
    """

    list_display = ("thumb", "caption", "order", "is_active", "uploaded_at", "photo_actions")
    list_editable = ("order", "is_active")
    list_display_links = ("thumb", "caption")
    ordering = ("order", "uploaded_at")
    search_fields = ("caption", "alt_text")
    list_filter = ("is_active",)
    list_per_page = 20

    fieldsets = (
        ("Image", {"fields": ("image", "image_preview")}),
        ("Details", {"fields": ("caption", "alt_text")}),
        ("Display", {"fields": ("order", "is_active")}),
        ("Meta", {"classes": ("collapse",), "fields": ("uploaded_at",)}),
    )

    readonly_fields = ("uploaded_at", "image_preview")

    # ── helpers ──────────────────────────────────────────────────────────

    def thumb(self, obj):
        url = _field_url(obj.image)
        if url:
            return format_html(
                '<img src="{}" style="height:48px;width:72px;object-fit:cover;'
                'border-radius:6px;border:1px solid #ddd">',
                url,
            )
        return format_html(
            '<div style="height:48px;width:72px;background:#e0eff2;border-radius:6px;'
            'display:flex;align-items:center;justify-content:center;'
            'font-size:11px;color:#6a8f9a">No image</div>'
        )
    thumb.short_description = "Preview"

    def image_preview(self, obj):
        url = _field_url(obj.image)
        if url:
            return format_html(
                '<img src="{}" style="max-height:200px;max-width:400px;'
                'border-radius:10px;border:1px solid #ddd">',
                url,
            )
        return "No image uploaded"
    image_preview.short_description = "Preview"

    def photo_actions(self, obj):
        opts = self.model._meta
        edit_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_change",
            args=[obj.pk],
        )
        delete_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_delete",
            args=[obj.pk],
        )
        return format_html(
            '<div class="row-actions">'
            '<a class="button edit" href="{}">Edit</a>'
            '<a class="button delete" href="{}">Delete</a>'
            '</div>',
            edit_url,
            delete_url,
        )
    photo_actions.short_description = "Actions"


# ==============================
# AI PROVIDER SETTINGS ADMIN
# ==============================

class AIProviderKeyForm(forms.ModelForm):
    """
    Custom form so the API key is never round-tripped to the browser in
    plaintext. The field shown to the admin is a separate, blank
    password input (`api_key_plain`) — submitting it encrypts and
    overwrites the stored key. Leaving it blank on an edit keeps the
    existing encrypted key untouched.
    """
    api_key_plain = forms.CharField(
        label="API Key",
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Enter to set/replace the key. Leave blank to keep the current key unchanged. "
                   "Not needed for local Ollama.",
    )

    class Meta:
        model = AIProviderSettings
        exclude = ("api_key_encrypted",)

    def save(self, commit=True):
        obj = super().save(commit=False)
        raw = self.cleaned_data.get("api_key_plain", "")
        if raw:
            obj.set_api_key(raw)
        if commit:
            obj.save()
        return obj


@admin.register(AIProviderSettings)
class AIProviderSettingsAdmin(admin.ModelAdmin):
    """
    Central registry for any external AI provider used anywhere in the
    project (Smart Importer today, future modules later). Only one row
    may be active; activating a row here automatically deactivates the
    others (enforced in the model's save()).

    SECURITY: the raw key is write-only via `api_key_plain` and is
    never shown back in the form or list view — only a masked preview.
    """
    form = AIProviderKeyForm

    class Media:
        js = ("core/js/ai_provider_admin.js",)

    list_display = ("name", "provider", "model_name", "active_badge", "key_status", "updated_at")
    list_filter = ("provider", "is_active")
    search_fields = ("name", "model_name")
    readonly_fields = ("key_preview", "created_at", "updated_at")

    fieldsets = (
        ("Provider", {
            "fields": ("name", "provider", "model_name", "is_active"),
            "description": "Adding a brand-new AI service — DeepSeek, BlackBox, or anything "
                            "that launches next year — never needs a code change. Almost every "
                            "new provider speaks the OpenAI chat-completions format, so pick "
                            "'OpenAI-compatible endpoint' below and just set its base URL "
                            "(e.g. DeepSeek: https://api.deepseek.com/v1, "
                            "BlackBox: https://api.blackbox.ai/v1, "
                            "Groq: https://api.groq.com/openai/v1, "
                            "OpenRouter: https://openrouter.ai/api/v1). "
                            "If a provider ISN'T OpenAI-shaped, pick 'Generic / fully custom API' "
                            "and fill in the Generic API Template section below instead.",
        }),
        ("Credentials", {
            "fields": ("api_key_plain", "key_preview", "base_url"),
            "description": "The API key is encrypted at rest and never displayed in full. "
                            "Not needed for local Ollama.",
        }),
        ("Generic API Template (only used when Provider = 'Generic / fully custom API')", {
            "fields": (
                "generic_http_method",
                "generic_headers_template",
                "generic_body_template",
                "generic_response_text_path",
            ),
            "classes": ("collapse",),
            "description": "This is how you plug in ANY AI API — including ones invented after "
                            "this app was built — with zero code changes. Set 'base_url' above "
                            "to the full endpoint (e.g. https://api.example.com/v1/chat). "
                            "Use {{prompt}}, {{model}} and {{api_key}} as placeholders in the "
                            "templates below; they're substituted safely as JSON strings. "
                            "'Response text path' tells us where in the JSON reply the generated "
                            "text lives, e.g. choices.0.message.content (OpenAI-style), "
                            "content.0.text (Anthropic-style), or "
                            "candidates.0.content.parts.0.text (Gemini-style).",
        }),
        ("Advanced", {"fields": ("request_timeout_seconds",), "classes": ("collapse",)}),
        ("Meta", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def active_badge(self, obj):
        if obj.is_active:
            return format_html('<span style="background:#1e8449;color:#fff;padding:2px 10px;border-radius:12px;font-size:11px;">ACTIVE</span>')
        return format_html('<span style="color:#9aabb8;font-size:11px;">inactive</span>')
    active_badge.short_description = "Status"

    def key_status(self, obj):
        if obj.api_key_encrypted:
            return format_html('<span style="color:#1565c0;">🔒 key set</span>')
        return format_html('<span style="color:#b35400;">no key</span>')
    key_status.short_description = "Credential"

    def key_preview(self, obj):
        if not obj.pk or not obj.api_key_encrypted:
            return "No key stored."
        from core.ai_registry import mask_key
        try:
            return mask_key(obj.get_api_key())
        except Exception:
            return "⚠️ Could not decrypt stored key (SECRET_KEY may have changed)."
    key_preview.short_description = "Current key (masked)"

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


# =====================================================
# AUTOSCHEDULER CONSTRAINT ENGINE — admin
#
# The pre-run confirmation screen on the autoscheduler page is the primary
# way to disable a constraint *for a single run*. This admin is for the
# persisted defaults and for entering individual lecturer rules (blocked
# days/times, soft day/time & venue preferences). Venue-side rules (blocks,
# specialization/exclusive) are managed in the in-app Venues panel.
# =====================================================

@admin.register(SchedulerConstraintToggle)
class SchedulerConstraintToggleAdmin(admin.ModelAdmin):
    """
    Constraint category toggles — these control which categories of
    constraints are enabled by default across the autoscheduler.
    """
    list_display = ("label", "key", "applies_to", "is_enabled", "updated_at")
    list_filter = ("applies_to", "is_enabled")
    list_editable = ("is_enabled",)
    readonly_fields = ("key", "applies_to")
    search_fields = ("key", "label")

    def has_add_permission(self, request):
        # Rows are seeded automatically from CONSTRAINT_DEFS
        # (core.scheduling_constraints) — no ad-hoc keys.
        return False


@admin.register(LecturerBlockedSlot)
class LecturerBlockedSlotAdmin(admin.ModelAdmin):
    """
    HARD constraint: lecturer is never scheduled on a given day/time.
    """
    list_display = ("lecturer", "day", "start_time", "end_time", "is_active", "reason")
    list_filter = ("day", "is_active")
    search_fields = ("lecturer__name", "lecturer__payroll_number", "reason")
    autocomplete_fields = ("lecturer",)
    list_editable = ("is_active",)
    ordering = ("lecturer__name", "day", "start_time")

    fieldsets = (
        ("Lecturer", {"fields": ("lecturer",)}),
        ("Block Period", {"fields": ("day", "start_time", "end_time")}),
        ("Details", {"fields": ("reason", "is_active")}),
    )


@admin.register(LecturerTimePreference)
class LecturerTimePreferenceAdmin(admin.ModelAdmin):
    """
    SOFT constraint: lecturer prefers specific days/times.
    Supports multi-day/multi-timeslot via LecturerTimePreferenceSlot.
    """
    list_display = ("lecturer", "slot_summary", "is_active", "notes")
    list_filter = ("is_active",)
    search_fields = ("lecturer__name", "lecturer__payroll_number", "notes")
    autocomplete_fields = ("lecturer",)
    list_editable = ("is_active",)
    ordering = ("lecturer__name",)

    fieldsets = (
        ("Lecturer", {"fields": ("lecturer",)}),
        ("Details", {"fields": ("notes", "is_active")}),
        ("Slots", {
            "fields": ("slots_display",),
            "description": "Each slot represents a day and a specific timeslot, or 'Whole Day'.",
        }),
    )

    readonly_fields = ("slots_display",)

    def slot_summary(self, obj):
        """Display a summary of all slots for this preference."""
        slots = obj.slots.all()
        if not slots:
            return "No slots"
        parts = []
        for day in slots.values_list('day', flat=True).distinct():
            day_slots = slots.filter(day=day)
            if day_slots.filter(is_whole_day=True).exists():
                parts.append(f"{day} (Whole Day)")
            else:
                times = [f"{s.start_time.strftime('%H:%M')}–{s.end_time.strftime('%H:%M')}"
                        for s in day_slots.filter(is_whole_day=False).order_by('start_time')]
                if times:
                    parts.append(f"{day}: {', '.join(times)}")
        return ", ".join(parts)
    slot_summary.short_description = "Preferred Days/Times"

    def slots_display(self, obj):
        """Display slots in a formatted way for the change form."""
        slots = obj.slots.all().order_by('day', 'start_time')
        if not slots:
            return "No slots configured"

        html = '<ul style="margin:0;padding-left:20px;">'
        for slot in slots:
            if slot.is_whole_day:
                html += f'<li><strong>{slot.day}</strong> — Whole Day</li>'
            else:
                html += f'<li><strong>{slot.day}</strong> — {slot.start_time.strftime("%H:%M")}–{slot.end_time.strftime("%H:%M")}</li>'
        html += '</ul>'
        return format_html(html)
    slots_display.short_description = "Slots"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("lecturer").prefetch_related("slots")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


@admin.register(LecturerTimePreferenceSlot)
class LecturerTimePreferenceSlotAdmin(admin.ModelAdmin):
    """
    Individual slot for a lecturer time preference.
    Usually managed through the parent LecturerTimePreference admin.
    """
    list_display = ("preference", "day", "time_display", "is_whole_day")
    list_filter = ("day", "is_whole_day")
    search_fields = ("preference__lecturer__name", "day")
    autocomplete_fields = ("preference",)
    ordering = ("preference__lecturer__name", "day", "start_time")

    def time_display(self, obj):
        return obj.time_display
    time_display.short_description = "Time"


@admin.register(LecturerVenuePreference)
class LecturerVenuePreferenceAdmin(admin.ModelAdmin):
    """
    SOFT constraint: lecturer prefers specific venues.
    """
    list_display = ("lecturer", "venue_list", "is_active", "notes")
    list_filter = ("is_active",)
    search_fields = ("lecturer__name", "lecturer__payroll_number", "notes")
    autocomplete_fields = ("lecturer",)
    filter_horizontal = ("venues",)
    list_editable = ("is_active",)
    ordering = ("lecturer__name",)

    fieldsets = (
        ("Lecturer", {"fields": ("lecturer",)}),
        ("Preferred Venues", {"fields": ("venues",)}),
        ("Details", {"fields": ("notes", "is_active")}),
    )

    def venue_list(self, obj):
        venues = obj.venues.all()
        if not venues:
            return "None"
        return ", ".join(v.code for v in venues[:6]) + (f" +{venues.count() - 6} more" if venues.count() > 6 else "")
    venue_list.short_description = "Preferred Venues"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("lecturer").prefetch_related("venues")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)

# ===================================================================
#  HOST / REMOTE DATA SYNC
# ===================================================================
class SyncModelLogInline(admin.TabularInline):
    model = SyncModelLog
    extra = 0
    can_delete = False
    fields = ("order", "app_label", "model_name", "status", "records_changed", "records_total", "error_message")
    readonly_fields = fields
    ordering = ("order",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SyncNode)
class SyncNodeAdmin(admin.ModelAdmin):
    """
    Singleton settings row (always pk=1) controlling whether this
    installation is the HOST (pushes data out) or the REMOTE (receives
    pushes) for the export_import host/remote sync feature, plus the
    remote URL and shared token used to authenticate the push.
    """
    list_display = ("mode", "is_enabled", "remote_url", "last_sync_status", "last_sync_finished_at")

    fieldsets = (
        ("Mode", {"fields": ("mode", "is_enabled")}),
        ("Remote connection (HOST only)", {
            "fields": ("remote_url", "raw_token", "sync_batch_delay_seconds"),
            "description": "On the REMOTE machine, set the same shared token here so it can "
                            "verify pushes coming from the host. remote_url is only used on the HOST.",
        }),
        ("Last run", {"fields": ("last_sync_started_at", "last_sync_finished_at", "last_sync_status")}),
    )
    readonly_fields = ("last_sync_started_at", "last_sync_finished_at", "last_sync_status")

    def raw_token(self, obj):
        return obj.get_token()
    raw_token.short_description = "Shared token"

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Swap the readonly display method for a real writable input by
        # adding a plain CharField and handling it in save_model().
        form.base_fields["raw_token"] = forms.CharField(
            required=False,
            widget=forms.TextInput(attrs={"size": 60}),
            help_text="Shared secret — must be identical on the host and the remote. "
                      "Leave blank to keep the current token unchanged.",
        )
        if obj is not None:
            form.base_fields["raw_token"].initial = obj.get_token()
        return form

    def save_model(self, request, obj, form, change):
        raw_token = form.cleaned_data.get("raw_token", "")
        if raw_token:
            obj.set_token(raw_token)
        super().save_model(request, obj, form, change)

    def has_add_permission(self, request):
        # Singleton — only ever one row (pk=1).
        return not SyncNode.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    list_display = ("id", "started_at", "finished_at", "status", "triggered_by", "models_completed", "total_models", "records_sent")
    list_filter = ("status",)
    readonly_fields = [f.name for f in SyncRun._meta.fields]
    inlines = [SyncModelLogInline]
    ordering = ("-started_at",)

    def has_add_permission(self, request):
        return False


@admin.register(SyncRecordState)
class SyncRecordStateAdmin(admin.ModelAdmin):
    list_display = ("app_label", "model_name", "object_id", "content_hash", "last_synced_at")
    list_filter = ("app_label", "model_name")
    search_fields = ("object_id", "content_hash")
    ordering = ("-last_synced_at",)