from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget
from .models import Food, FoodImage, FoodOption, Offer

# ==============================
# INLINE ADMIN CLASSES
# ==============================

class FoodImageInline(admin.TabularInline):
    model = FoodImage
    extra = 1
    fields = ('image', 'image_preview')
    readonly_fields = ('image_preview',)
    
    def image_preview(self, obj):
        if obj.image:
            return format_html(f'<img src="{obj.image.url}" style="max-height: 100px;" />')
        return "No image"
    image_preview.short_description = 'Preview'


class FoodOptionInline(admin.TabularInline):
    model = FoodOption
    extra = 1


# ==============================
# RESOURCE CLASSES
# ==============================

class FoodResource(resources.ModelResource):
    class Meta:
        model = Food
        import_id_fields = ['name']
        fields = ('name', 'description', 'price', 'created_at')
        export_order = ('name', 'description', 'price', 'created_at')
        skip_unchanged = True
        report_skipped = True
    
    def before_import_row(self, row, **kwargs):
        """Clean import data"""
        if 'name' in row:
            row['name'] = row['name'].strip().title()
        if 'description' in row:
            row['description'] = row['description'].strip()
        if 'price' in row:
            try:
                row['price'] = float(row['price'])
            except (ValueError, TypeError):
                row['price'] = 0.0


class FoodImageResource(resources.ModelResource):
    food = fields.Field(
        column_name='food',
        attribute='food',
        widget=ForeignKeyWidget(Food, 'name')
    )
    
    class Meta:
        model = FoodImage
        import_id_fields = ['food', 'image']
        fields = ('food', 'image')
        export_order = ('food', 'image')
        skip_unchanged = True
        report_skipped = True
    
    def before_import_row(self, row, **kwargs):
        """Clean import data"""
        if 'food' in row:
            row['food'] = row['food'].strip().title()


class FoodOptionResource(resources.ModelResource):
    food = fields.Field(
        column_name='food',
        attribute='food',
        widget=ForeignKeyWidget(Food, 'name')
    )
    
    class Meta:
        model = FoodOption
        import_id_fields = ['food', 'option']
        fields = ('food', 'option')
        export_order = ('food', 'option')
        skip_unchanged = True
        report_skipped = True
    
    def before_import_row(self, row, **kwargs):
        """Clean import data"""
        if 'food' in row:
            row['food'] = row['food'].strip().title()
        if 'option' in row:
            row['option'] = row['option'].strip()


class OfferResource(resources.ModelResource):
    class Meta:
        model = Offer
        import_id_fields = ['title']
        fields = ('title', 'description', 'discount', 'image', 
                 'start_date', 'end_date', 'is_active', 'created_at')
        export_order = ('title', 'description', 'discount', 'start_date',
                       'end_date', 'is_active', 'created_at')
        skip_unchanged = True
        report_skipped = True
    
    def before_import_row(self, row, **kwargs):
        """Clean import data"""
        if 'title' in row:
            row['title'] = row['title'].strip().title()
        if 'description' in row:
            row['description'] = row['description'].strip()
        if 'discount' in row and row['discount']:
            try:
                row['discount'] = float(row['discount'])
            except (ValueError, TypeError):
                row['discount'] = None
        if 'is_active' not in row or row['is_active'] == '':
            row['is_active'] = True


# ==============================
# ADMIN CLASSES
# ==============================

@admin.register(Food)
class FoodAdmin(ImportExportModelAdmin):
    resource_class = FoodResource
    inlines = [FoodImageInline, FoodOptionInline]
    
    list_display = ('name', 'price_display', 'description_preview', 
                    'first_image_preview', 'options_count', 'created_date', 'edit_button', 'delete_button')
    list_filter = ('created_at',)
    search_fields = ('name', 'description')
    list_per_page = 25
    
    fieldsets = (
        ('Food Information', {
            'fields': ('name', 'description', 'price')
        }),
    )
    
    readonly_fields = ('created_at',)
    
    def price_display(self, obj):
        return f"${obj.price:.2f}"
    price_display.short_description = 'Price'
    
    def description_preview(self, obj):
        if obj.description:
            return (obj.description[:60] + '...') if len(obj.description) > 60 else obj.description
        return "-"
    description_preview.short_description = 'Description'
    
    def first_image_preview(self, obj):
        first_image = obj.first_image()
        if first_image:
            return format_html(f'<img src="{first_image}" style="max-height: 50px;" />')
        return format_html('<span style="color: #999;">No image</span>')
    first_image_preview.short_description = 'Image'
    
    def options_count(self, obj):
        count = obj.options.count()
        return f"{count} options"
    options_count.short_description = 'Options'
    
    def created_date(self, obj):
        return obj.created_at.strftime('%Y-%m-%d')
    created_date.short_description = 'Created'
    
    def edit_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Edit</a>',
            reverse('admin:%s_%s_change' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    edit_button.short_description = 'Edit'
    edit_button.allow_tags = True

    def delete_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Delete</a>',
            reverse('admin:%s_%s_delete' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    delete_button.short_description = 'Delete'
    delete_button.allow_tags = True


@admin.register(FoodImage)
class FoodImageAdmin(ImportExportModelAdmin):
    resource_class = FoodImageResource
    
    list_display = ('food', 'image_preview', 'upload_date', 'edit_button', 'delete_button')
    list_filter = ('food',)
    search_fields = ('food__name',)
    list_per_page = 25
    
    readonly_fields = ('image_preview_large',)
    
    def image_preview(self, obj):
        if obj.image:
            return format_html(f'<img src="{obj.image.url}" style="max-height: 50px;" />')
        return "No image"
    image_preview.short_description = 'Preview'
    
    def image_preview_large(self, obj):
        if obj.image:
            return format_html(f'<img src="{obj.image.url}" style="max-height: 300px;" />')
        return "No image"
    image_preview_large.short_description = 'Large Preview'
    
    def upload_date(self, obj):
        return "-"
    upload_date.short_description = 'Uploaded'

    def edit_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Edit</a>',
            reverse('admin:%s_%s_change' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    edit_button.short_description = 'Edit'
    edit_button.allow_tags = True

    def delete_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Delete</a>',
            reverse('admin:%s_%s_delete' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    delete_button.short_description = 'Delete'
    delete_button.allow_tags = True


@admin.register(FoodOption)
class FoodOptionAdmin(ImportExportModelAdmin):
    resource_class = FoodOptionResource
    
    list_display = ('food', 'option', 'food_price', 'edit_button', 'delete_button')
    list_filter = ('food',)
    search_fields = ('food__name', 'option')
    list_per_page = 25
    
    def food_price(self, obj):
        return f"${obj.food.price:.2f}" if obj.food.price else "-"
    food_price.short_description = 'Food Price'
    
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related('food')

    def edit_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Edit</a>',
            reverse('admin:%s_%s_change' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    edit_button.short_description = 'Edit'
    edit_button.allow_tags = True

    def delete_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Delete</a>',
            reverse('admin:%s_%s_delete' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    delete_button.short_description = 'Delete'
    delete_button.allow_tags = True


@admin.register(Offer)
class OfferAdmin(ImportExportModelAdmin):
    resource_class = OfferResource
    
    list_display = ('title', 'discount_display', 'start_date', 'end_date', 
                    'active_status', 'days_remaining', 'created_date', 'edit_button', 'delete_button')
    list_filter = ('is_active', 'start_date', 'end_date')
    search_fields = ('title', 'description')
    list_per_page = 25
    
    fieldsets = (
        ('Offer Information', {
            'fields': ('title', 'description', 'discount', 'image')
        }),
        ('Validity Period', {
            'fields': ('start_date', 'end_date', 'is_active')
        }),
    )
    
    readonly_fields = ('created_at', 'image_preview')
    
    def discount_display(self, obj):
        if obj.discount:
            return f"{obj.discount}% off"
        return "No discount"
    discount_display.short_description = 'Discount'
    
    def active_status(self, obj):
        if obj.is_active:
            return format_html(
                '<span style="background-color: #4CAF50; color: white; padding: 3px 8px; '
                'border-radius: 12px; font-size: 12px;">Active</span>'
            )
        return format_html(
            '<span style="background-color: #F44336; color: white; padding: 3px 8px; '
            'border-radius: 12px; font-size: 12px;">Inactive</span>'
        )
    active_status.short_description = 'Status'
    
    def days_remaining(self, obj):
        from datetime import date
        if obj.end_date:
            remaining = (obj.end_date - date.today()).days
            if remaining > 0:
                return f"{remaining} days"
            elif remaining == 0:
                return "Ends today"
            else:
                return "Expired"
        return "-"
    days_remaining.short_description = 'Days Left'
    
    def created_date(self, obj):
        return obj.created_at.strftime('%Y-%m-%d')
    created_date.short_description = 'Created'
    
    def image_preview(self, obj):
        if obj.image:
            return format_html(f'<img src="{obj.image.url}" style="max-height: 200px;" />')
        return "No image"
    image_preview.short_description = 'Preview'
    
    def edit_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Edit</a>',
            reverse('admin:%s_%s_change' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    edit_button.short_description = 'Edit'
    edit_button.allow_tags = True

    def delete_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Delete</a>',
            reverse('admin:%s_%s_delete' % (obj._meta.app_label, obj._meta.model_name), args=[obj.pk])
        )
    delete_button.short_description = 'Delete'
    delete_button.allow_tags = True
