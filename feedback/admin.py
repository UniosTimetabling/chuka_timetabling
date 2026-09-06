from django.contrib import admin
from django.utils.html import format_html

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import BooleanWidget, DateTimeWidget

from .models import Feedback, FeedbackAttachment
from .chatbot_models import (
    ChatConversationLog,
    ChatbotPublication,
    ChatbotTimetableEntry,
)


# ─────────────────────────────────────────────────────────────────────────────
#  IMPORT / EXPORT RESOURCES
# ─────────────────────────────────────────────────────────────────────────────

class FeedbackResource(resources.ModelResource):
    created_at = fields.Field(
        attribute="created_at",
        column_name="created_at",
        widget=DateTimeWidget(),
    )

    class Meta:
        model   = Feedback
        import_id_fields = ("id",)
        fields  = (
            "id", "full_name", "email", "admission_number",
            "message", "status", "created_at",
        )
        export_order = fields
        skip_unchanged  = True
        report_skipped  = True

    def before_import_row(self, row, **kwargs):
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.strip()


class ChatConversationLogResource(resources.ModelResource):
    bot_failed = fields.Field(
        attribute="bot_failed",
        column_name="bot_failed",
        widget=BooleanWidget(),
    )
    created_at = fields.Field(
        attribute="created_at",
        column_name="created_at",
        widget=DateTimeWidget(),
    )

    class Meta:
        model  = ChatConversationLog
        import_id_fields = ("id",)
        fields = (
            "id", "session_id", "user_message", "bot_reply",
            "topic", "query_type", "bot_failed", "created_at",
        )
        export_order = fields
        skip_unchanged  = True
        report_skipped  = True

    def before_import_row(self, row, **kwargs):
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.strip()


class ChatbotPublicationResource(resources.ModelResource):
    is_latest = fields.Field(
        attribute="is_latest",
        column_name="is_latest",
        widget=BooleanWidget(),
    )
    published_at = fields.Field(
        attribute="published_at",
        column_name="published_at",
        widget=DateTimeWidget(),
    )

    class Meta:
        model  = ChatbotPublication
        import_id_fields = ("id",)
        fields = (
            "id", "timetable_type", "academic_year", "semester",
            "is_latest", "published_by", "published_at", "pdf_document_id",
        )
        export_order = fields
        skip_unchanged  = True
        report_skipped  = True

    def before_import_row(self, row, **kwargs):
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.strip()


class ChatbotTimetableEntryResource(resources.ModelResource):
    class Meta:
        model  = ChatbotTimetableEntry
        import_id_fields = ("id",)
        fields = (
            "id", "publication", "course_code", "course_name",
            "program_name", "department_name", "year_of_study",
            "day", "date", "start_time", "end_time",
            "venue_code", "venue_name", "lecturer_name", "merged_codes",
        )
        export_order = fields
        skip_unchanged  = True
        report_skipped  = True

    def before_import_row(self, row, **kwargs):
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.strip()

    def dehydrate_publication(self, entry):
        return str(entry.publication) if entry.publication else ""


# ─────────────────────────────────────────────────────────────────────────────
#  FEEDBACK
# ─────────────────────────────────────────────────────────────────────────────

class FeedbackAttachmentInline(admin.TabularInline):
    model = FeedbackAttachment
    extra = 0
    fields = ("file", "name", "mime_type")
    readonly_fields = ("mime_type",)


@admin.register(Feedback)
class FeedbackAdmin(ImportExportModelAdmin):
    resource_class  = FeedbackResource
    list_display    = ("full_name", "email", "admission_number", "source_badge", "status_badge", "created_at")
    list_filter     = ("status", "source", "role")
    search_fields   = ("full_name", "email", "message", "mobile_user_id")
    ordering        = ("-created_at",)
    readonly_fields = ("created_at", "source", "role", "mobile_user_id")
    date_hierarchy  = "created_at"
    inlines         = [FeedbackAttachmentInline]

    fieldsets = (
        ("Contact", {
            "fields": ("full_name", "email", "admission_number"),
        }),
        ("Message", {
            "fields": ("message", "status"),
        }),
        ("Submitted via", {
            "fields": ("source", "role", "mobile_user_id"),
        }),
        ("Meta", {
            "fields": ("created_at",),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="Source")
    def source_badge(self, obj):
        colour = "#1565c0" if obj.source == Feedback.SOURCE_MOBILE else "#616161"
        return format_html(
            '<span style="background:{};color:white;padding:3px 10px;'
            'border-radius:12px;font-size:11px;">{}</span>',
            colour,
            obj.get_source_display(),
        )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colours = {
            "unseen":   "#FF9800",
            "seen":     "#2196F3",
            "attended": "#9C27B0",
            "solved":   "#4CAF50",
        }
        colour = colours.get(obj.status, "#9E9E9E")
        return format_html(
            '<span style="background:{};color:white;padding:3px 10px;'
            'border-radius:12px;font-size:11px;">{}</span>',
            colour,
            obj.get_status_display(),
        )

    def get_export_filename(self, request, queryset, file_format):
        return f"feedback.{file_format.get_extension()}"


# ─────────────────────────────────────────────────────────────────────────────
#  CHAT CONVERSATION LOGS
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(ChatConversationLog)
class ChatConversationLogAdmin(ImportExportModelAdmin):
    resource_class  = ChatConversationLogResource
    list_display    = (
        "short_session_id", "user_message_preview", "bot_reply_preview",
        "topic", "query_type", "bot_failed", "created_at",
    )
    list_filter     = ("bot_failed", "query_type", "topic")
    search_fields   = ("session_id", "user_message", "bot_reply", "topic")
    ordering        = ("-created_at",)
    readonly_fields = (
        "session_id", "user_message", "bot_reply",
        "topic", "query_type", "bot_failed", "created_at",
    )
    date_hierarchy  = "created_at"
    actions         = ["purge_older_than_two_weeks"]

    fieldsets = (
        ("Session", {
            "fields": ("session_id", "created_at"),
        }),
        ("Conversation", {
            "fields": ("user_message", "bot_reply"),
        }),
        ("Classification", {
            "fields": ("topic", "query_type", "bot_failed"),
        }),
    )

    @admin.display(description="Session ID")
    def short_session_id(self, obj):
        return obj.session_id[:12] + "…" if len(obj.session_id) > 12 else obj.session_id

    @admin.display(description="User message")
    def user_message_preview(self, obj):
        return obj.user_message[:80] + "…" if len(obj.user_message) > 80 else obj.user_message

    @admin.display(description="Bot reply")
    def bot_reply_preview(self, obj):
        return obj.bot_reply[:80] + "…" if len(obj.bot_reply) > 80 else obj.bot_reply

    @admin.action(description="Purge ALL logs older than 2 weeks")
    def purge_older_than_two_weeks(self, request, queryset):
        deleted = ChatConversationLog.purge_old()
        self.message_user(request, f"Purged {deleted} chat log(s) older than 2 weeks.")

    def get_export_filename(self, request, queryset, file_format):
        return f"chat_conversation_logs.{file_format.get_extension()}"


# ─────────────────────────────────────────────────────────────────────────────
#  CHATBOT — PUBLICATION
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(ChatbotPublication)
class ChatbotPublicationAdmin(ImportExportModelAdmin):
    resource_class  = ChatbotPublicationResource
    list_display    = (
        "timetable_type", "academic_year", "semester",
        "is_latest", "published_by", "published_at", "entry_count",
    )
    list_filter     = ("timetable_type", "is_latest", "semester")
    ordering        = ("-published_at",)
    readonly_fields = ("published_at", "pdf_document_id")

    @admin.display(description="# Entries")
    def entry_count(self, obj):
        return obj.entries.count()

    def get_export_filename(self, request, queryset, file_format):
        return f"chatbot_publications.{file_format.get_extension()}"


# ─────────────────────────────────────────────────────────────────────────────
#  CHATBOT — TIMETABLE ENTRY
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(ChatbotTimetableEntry)
class ChatbotTimetableEntryAdmin(ImportExportModelAdmin):
    resource_class  = ChatbotTimetableEntryResource
    list_display    = (
        "course_code", "course_name", "program_name", "year_of_study",
        "day", "date", "start_time", "venue_code", "lecturer_name",
        "publication",
    )
    list_filter     = (
        "publication__timetable_type",
        "publication__is_latest",
        "year_of_study",
        "day",
    )
    search_fields   = (
        "course_code", "course_name", "program_name",
        "lecturer_name", "venue_code",
    )
    ordering        = ("publication", "day", "date", "start_time")
    readonly_fields = ("publication",)
    date_hierarchy  = "date"

    fieldsets = (
        ("Course", {
            "fields": (
                "course_code", "course_name", "program_name",
                "department_name", "year_of_study", "merged_codes",
            ),
        }),
        ("Schedule", {
            "fields": ("day", "date", "start_time", "end_time", "venue_code", "venue_name"),
        }),
        ("Staff & Publication", {
            "fields": ("lecturer_name", "publication"),
        }),
    )

    def get_export_filename(self, request, queryset, file_format):
        return f"chatbot_timetable_entries.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("publication")