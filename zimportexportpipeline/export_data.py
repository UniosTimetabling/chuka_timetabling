#!/usr/bin/env python
"""
Custom Django data export script with dependency ordering.
Exports each model to its own JSON file in the 'exports' directory.
Files are named: <app_label>_<model_name>_data.json

Usage: python manage.py runscript export_data
or: python manage.py shell < export_data.py
"""

import os
import json
from django.apps import apps
from django.core.serializers import serialize
from django.db import models
from django.core.management.base import BaseCommand
from datetime import datetime
import sys

# Model dependency order - models that are referenced by others must come first
DEPENDENCY_ORDER = [
    # Department Management (highest level)
    'department_management.Department',
    
    # User/Auth
    'auth.User',
    
    # Lecturer Portal
    'lecturer_portal.Lecturer',
    
    # Program Management
    'program_management.Program',
    'program_management.ProgramCourse',
    'program_management.ProgramCode',
    'program_management.ImportJob',
    
    # Room Management
    'room_management.Building',
    'room_management.Venue',
    'room_management.VenueSpecialization',
    'room_management.VenueBlock',
    'room_management.LabVenue',
    
    # Course Allocation
    'course_allocation.StudentGroup',
    'course_allocation.GroupingTemplate',
    'course_allocation.GroupingTemplateGroup',
    'course_allocation.GroupingTemplateCourse',
    'course_allocation.SpecializationCategory',
    'course_allocation.SpecializationStem',
    'course_allocation.SelectionGroup',
    'course_allocation.SpecialIntakeGroup',
    'course_allocation.CourseAllocation',
    'course_allocation.BaseSelection',
    'course_allocation.AllocationConfig',
    'course_allocation.LecturerCourseMapping',
    'course_allocation.AcademicYearTracker',
    'course_allocation.ProgramEnrollment',
    'course_allocation.LabAllocation',
    'course_allocation.SubmissionControl',
    'course_allocation.ArchivedCourseAllocation',
    'course_allocation.CombinedCourseGroup',
    'course_allocation.CourseCombinationTemplate',
    'course_allocation.CourseCombinationTemplateProgram',
    'course_allocation.DVCActionLog',
    
    # Timetable (depends on CourseAllocation and Venue)
    'timetable.Timetable',
    'timetable.TempTimetable',
    'timetable.ExamTimetable',
    'timetable.ExamTempTimetable',
    'timetable.LabTimetable',
    'timetable.LabExamTimetable',
    'timetable.TimetableArchive',
    'timetable.SharedVenueExamGroup',
    'timetable.MergedCourseGroup',
    'timetable.MergedCourseGroupTimetable',
    'timetable.AutoMergedExamGroup',
    'timetable.LecturerBlockedSlot',
    'timetable.LecturerTimePreference',
    'timetable.LecturerTimePreferenceSlot',
    'timetable.LecturerVenuePreference',
    'timetable.SchedulerConfig',
    'timetable.ExamSchedulerConfig',
    'timetable.LabSchedulerConfig',
]

# Models that should be completely excluded from export
EXCLUDED_MODELS = [
    'django.contrib.sessions.Session',
    'django.contrib.admin.LogEntry',
    'django.contrib.contenttypes.ContentType',
]

# Models that should export with limited fields (for security or size)
LIMITED_FIELDS = {
    'auth.User': ['id', 'username', 'email', 'first_name', 'last_name', 'is_active', 'date_joined'],
}


def get_model_from_string(model_str):
    """Convert 'app.Model' string to actual model class."""
    try:
        app_label, model_name = model_str.split('.')
        return apps.get_model(app_label, model_name)
    except (ValueError, LookupError):
        return None


def get_exportable_models():
    """Get all models in dependency order, excluding unwanted ones."""
    exported = []
    seen = set()
    
    # First, add models in explicit dependency order
    for model_path in DEPENDENCY_ORDER:
        model = get_model_from_string(model_path)
        if model and model_path not in seen:
            seen.add(model_path)
            exported.append((model_path, model))
    
    # Then, add any remaining models not in the dependency order
    all_models = apps.get_models()
    for model in all_models:
        model_path = f"{model._meta.app_label}.{model._meta.object_name}"
        if model_path not in seen and model_path not in EXCLUDED_MODELS:
            # Check if this is in excluded list by wildcard
            excluded = False
            for excl in EXCLUDED_MODELS:
                if model_path.startswith(excl.split('.')[0]):
                    excluded = True
                    break
            if not excluded:
                seen.add(model_path)
                exported.append((model_path, model))
    
    return exported


def get_related_field_names(model):
    """Get all field names that are ForeignKey or OneToOneField."""
    fields = []
    for field in model._meta.get_fields():
        if isinstance(field, (models.ForeignKey, models.OneToOneField)):
            fields.append(field.name)
    return fields


def collect_data_with_relations(model, queryset, depth=0, max_depth=3, visited=None):
    """
    Recursively collect related data for a model instance.
    Handles circular references by tracking visited objects.
    """
    if visited is None:
        visited = set()
    
    data = []
    
    for obj in queryset:
        obj_key = f"{model._meta.app_label}.{model._meta.object_name}.{obj.pk}"
        if obj_key in visited:
            continue
        visited.add(obj_key)
        
        # Get the object data
        obj_data = {}
        for field in model._meta.get_fields():
            if field.name in ['id']:
                obj_data[field.name] = getattr(obj, field.name)
            elif isinstance(field, (models.CharField, models.TextField, models.IntegerField,
                                   models.BooleanField, models.DateField, models.DateTimeField,
                                   models.DecimalField, models.FloatField, models.TimeField,
                                   models.EmailField, models.URLField, models.SlugField,
                                   models.JSONField)):
                value = getattr(obj, field.name)
                if isinstance(value, (datetime,)):
                    obj_data[field.name] = value.isoformat()
                elif value is not None:
                    obj_data[field.name] = value
                else:
                    obj_data[field.name] = None
            elif isinstance(field, models.ForeignKey):
                if depth < max_depth:
                    related_obj = getattr(obj, field.name)
                    if related_obj:
                        related_model = field.related_model
                        related_path = f"{related_model._meta.app_label}.{related_model._meta.object_name}"
                        rel_data = collect_data_with_relations(
                            related_model,
                            related_model.objects.filter(pk=related_obj.pk),
                            depth + 1,
                            max_depth,
                            visited
                        )
                        if rel_data:
                            obj_data[field.name] = rel_data[0]
                        else:
                            obj_data[field.name] = None
                else:
                    obj_data[field.name] = getattr(obj, f"{field.name}_id")
            elif isinstance(field, models.ManyToManyField):
                # Store many-to-many as list of IDs
                obj_data[field.name] = list(getattr(obj, field.name).values_list('id', flat=True))
            elif isinstance(field, models.OneToOneField):
                if depth < max_depth:
                    try:
                        related_obj = getattr(obj, field.name)
                        if related_obj:
                            related_model = field.related_model
                            rel_data = collect_data_with_relations(
                                related_model,
                                related_model.objects.filter(pk=related_obj.pk),
                                depth + 1,
                                max_depth,
                                visited
                            )
                            if rel_data:
                                obj_data[field.name] = rel_data[0]
                    except related_model.DoesNotExist:
                        obj_data[field.name] = None
                else:
                    obj_data[field.name] = getattr(obj, f"{field.name}_id")
        
        data.append(obj_data)
    
    return data


def export_model(model, model_path, export_dir='exports'):
    """Export a single model to a JSON file with dependency-aware data."""
    os.makedirs(export_dir, exist_ok=True)
    
    filename = f"{model_path.replace('.', '_')}_data.json"
    filepath = os.path.join(export_dir, filename)
    
    print(f"Exporting {model_path} to {filename}...")
    
    try:
        # Get all objects
        queryset = model.objects.all()
        count = queryset.count()
        
        if count == 0:
            print(f"  → No data found, creating empty file")
            with open(filepath, 'w') as f:
                json.dump({
                    'model': model_path,
                    'count': 0,
                    'data': []
                }, f, indent=2, default=str)
            return
        
        # Collect data with relations (limited depth to avoid circular issues)
        data = collect_data_with_relations(model, queryset, max_depth=2)
        
        # Create export JSON
        export_data = {
            'model': model_path,
            'model_name': model._meta.object_name,
            'app_label': model._meta.app_label,
            'count': len(data),
            'fields': [f.name for f in model._meta.get_fields()],
            'related_fields': get_related_field_names(model),
            'dependency': get_dependency_for_model(model),
            'data': data
        }
        
        with open(filepath, 'w') as f:
            json.dump(export_data, f, indent=2, default=str)
        
        print(f"  ✓ Exported {len(data)} records")
        
    except Exception as e:
        print(f"  ✗ Error exporting {model_path}: {str(e)}")
        raise


def get_dependency_for_model(model):
    """Get the dependencies for this model based on its fields."""
    deps = []
    for field in model._meta.get_fields():
        if isinstance(field, models.ForeignKey):
            related = field.related_model
            deps.append(f"{related._meta.app_label}.{related._meta.object_name}")
        elif isinstance(field, models.OneToOneField):
            related = field.related_model
            deps.append(f"{related._meta.app_label}.{related._meta.object_name}")
    return list(set(deps))


def export_all_data(export_dir='exports'):
    """Export all models with proper dependency ordering."""
    print(f"Starting export to {export_dir}/")
    print("-" * 50)
    
    exported_models = get_exportable_models()
    
    # Track which models were exported
    exported_count = 0
    
    for model_path, model in exported_models:
        try:
            export_model(model, model_path, export_dir)
            exported_count += 1
        except Exception as e:
            print(f"ERROR exporting {model_path}: {e}")
    
    print("-" * 50)
    print(f"Export complete! Exported {exported_count} models to {export_dir}/")
    
    # Create a manifest file listing all exported files
    manifest = {
        'exported_at': datetime.now().isoformat(),
        'models': [{'path': path, 'file': f"{path.replace('.', '_')}_data.json"} 
                   for path, _ in exported_models],
        'total_models': len(exported_models)
    }
    
    manifest_path = os.path.join(export_dir, 'manifest.json')
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2, default=str)
    
    print(f"Manifest saved to {manifest_path}")


def main():
    """Main entry point for script execution."""
    export_dir = 'exports'
    export_all_data(export_dir)


if __name__ == "__main__":
    main()