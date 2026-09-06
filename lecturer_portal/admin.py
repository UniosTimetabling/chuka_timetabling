from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget

from .models import Lecturer
from department_management.models import Department

from django.contrib.auth.models import User
# =========================
# Import / Export Resource
# =========================

class LecturerResource(resources.ModelResource):
    user = fields.Field(
        column_name='user',
        attribute='user',
        widget=ForeignKeyWidget(User, 'username'),
        default=None
    )

    department = fields.Field(
        column_name='department',
        attribute='department',
        widget=ForeignKeyWidget(Department, 'name'),
        default=None
    )

    user_details = fields.Field(
        column_name='user_details',
        attribute='user',
        readonly=True
    )

    class Meta:
        model = Lecturer

        # 🔑 USE EMAIL AS UNIQUE IDENTIFIER
        import_id_fields = ('email',)

        fields = (
            'payroll_number',
            'name',
            'email',
            'designation',
            'department',
            'user',
            'user_details',
        )

        export_order = (
            'payroll_number',
            'name',
            'email',
            'designation',
            'department',
            'user',
        )

        skip_unchanged = True
        report_skipped = True
        clean_model_instances = True
        skip_diff = True

    # -------------------------
    # Export helpers
    # -------------------------

    def dehydrate_user_details(self, obj):
        if obj.user:
            return f"{obj.user.username} ({obj.user.email})"
        return "No user linked"

    # -------------------------
    # Import cleaning (ROBUST)
    # -------------------------

    def before_import_row(self, row, **kwargs):
        """
        - Updates existing lecturers instead of failing
        - Allows missing fields
        - Ignores extra fields
        """

        if row.get('email'):
            row['email'] = row['email'].strip().lower()
        else:
            # Email is REQUIRED
            raise ValueError("Email is required to import Lecturer")

        if row.get('payroll_number'):
            row['payroll_number'] = row['payroll_number'].strip().upper()

        if row.get('name'):
            row['name'] = row['name'].strip().title()

        if row.get('designation'):
            row['designation'] = row['designation'].strip()

        # Optional FKs
        if not row.get('user'):
            row['user'] = None
        else:
            row['user'] = row['user'].strip()

        if not row.get('department'):
            row['department'] = None
        else:
            row['department'] = row['department'].strip().title()


# =========================
# Admin Configuration
# =========================

@admin.register(Lecturer)
class LecturerAdmin(ImportExportModelAdmin):
    resource_class = LecturerResource

    list_display = (
        'payroll_number',
        'display_name_with_designation',
        'email_link',
        'department_display',
        'user_linked',
        'courses_count',
        'action_buttons',  # Add Edit/Delete action buttons
    )

    list_filter = ('designation', 'department')
    search_fields = (
        'payroll_number',
        'name',
        'email',
        'department__name',
        'user__username',
    )

    ordering = ('payroll_number',)
    list_per_page = 25

    fieldsets = (
        ('Personal Information', {
            'fields': ('payroll_number', 'name', 'email', 'designation')
        }),
        ('Department & User', {
            'fields': ('department', 'user')
        }),
    )

    # -------------------------
    # Display helpers
    # -------------------------

    def display_name_with_designation(self, obj):
        if obj.user and obj.user.get_full_name():
            return f"{obj.designation} {obj.user.get_full_name()}"
        return f"{obj.designation} {obj.name}"
    display_name_with_designation.short_description = 'Lecturer'

    def email_link(self, obj):
        return format_html('<a href="mailto:{}">{}</a>', obj.email, obj.email)
    email_link.short_description = 'Email'

    def department_display(self, obj):
        return obj.department.name if obj.department else "-"
    department_display.short_description = 'Department'

    def user_linked(self, obj):
        if obj.user:
            return format_html(
                '<span style="color: #4CAF50; font-weight: bold;">✓ {}</span>',
                obj.user.username
            )
        return format_html(
            '<span style="color: #FF9800;">No user linked</span>'
        )
    user_linked.short_description = 'User Account'

    def courses_count(self, obj):
        if hasattr(obj, 'course_allocations'):
            return f"{obj.course_allocations.count()} courses"
        return "0 courses"
    courses_count.short_description = 'Courses'

    # -------------------------
    # Edit and Delete Buttons
    # -------------------------

    def action_buttons(self, obj):
        buttons = []

        if self.has_change_permission(self.request, obj):
            edit_url = reverse(
                "admin:%s_%s_change"
                % (obj._meta.app_label, obj._meta.model_name),
                args=[obj.pk],
            )
            buttons.append(
                f'<a class="button" href="{edit_url}">Edit</a>'
            )

        if self.has_delete_permission(self.request, obj):
            delete_url = reverse(
                "admin:%s_%s_delete"
                % (obj._meta.app_label, obj._meta.model_name),
                args=[obj.pk],
            )
            buttons.append(
                f'<a class="button" style="color:red" href="{delete_url}">Delete</a>'
            )

        return format_html(" ".join(buttons))

    action_buttons.short_description = "Actions"

    # -------------------------
    # Optimizing Queryset
    # -------------------------

    def get_queryset(self, request):
        self.request = request  # needed for permission checks
        return super().get_queryset(request).select_related('user', 'department')
