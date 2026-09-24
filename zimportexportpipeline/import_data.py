#!/usr/bin/env python
"""
Custom Django data import script with dependency handling.
Imports each model from its JSON file in the correct order.

Usage: python manage.py runscript import_data
or: python manage.py shell < import_data.py
"""

import os
import json
from django.apps import apps
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from datetime import datetime
import sys


class DataImporter:
    """Handles importing data with proper dependency resolution."""
    
    def __init__(self, import_dir='exports'):
        self.import_dir = import_dir
        self.imported_ids = {}  # Track which objects have been imported
        self.imported_models = set()  # Track which models have been processed
        self.stats = {
            'total_models': 0,
            'imported_models': 0,
            'total_records': 0,
            'imported_records': 0,
            'skipped_records': 0,
            'errors': []
        }
    
    def get_model_from_string(self, model_str):
        """Convert 'app.Model' string to actual model class."""
        try:
            app_label, model_name = model_str.split('.')
            return apps.get_model(app_label, model_name)
        except (ValueError, LookupError) as e:
            return None
    
    def get_import_order(self):
        """Determine import order based on dependencies."""
        manifest_path = os.path.join(self.import_dir, 'manifest.json')
        
        if os.path.exists(manifest_path):
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
            
            # Get models from manifest
            model_files = manifest.get('models', [])
            
            # Build dependency graph
            model_deps = {}
            for entry in model_files:
                model_path = entry['path']
                model = self.get_model_from_string(model_path)
                if model:
                    # Get dependencies from the model's JSON file
                    json_file = os.path.join(self.import_dir, entry['file'])
                    if os.path.exists(json_file):
                        with open(json_file, 'r') as f:
                            data = json.load(f)
                            deps = data.get('dependency', [])
                            model_deps[model_path] = deps
                    else:
                        model_deps[model_path] = []
            
            # Topological sort based on dependencies
            return self.topological_sort(model_deps)
        
        # Fallback: scan directory and try to guess order
        return self.scan_import_dir()
    
    def topological_sort(self, deps_graph):
        """Sort models so dependencies come first."""
        result = []
        visited = set()
        temp = set()
        
        def visit(node):
            if node in temp:
                raise ValueError(f"Circular dependency detected: {node}")
            if node in visited:
                return
            
            temp.add(node)
            for dep in deps_graph.get(node, []):
                # If dependency is in our graph, process it first
                if dep in deps_graph:
                    visit(dep)
            temp.remove(node)
            visited.add(node)
            result.append(node)
        
        for node in deps_graph:
            if node not in visited:
                visit(node)
        
        return result
    
    def scan_import_dir(self):
        """Scan import directory and return files in alphabetical order."""
        if not os.path.exists(self.import_dir):
            return []
        
        files = [f for f in os.listdir(self.import_dir) 
                if f.endswith('.json') and f != 'manifest.json']
        files.sort()
        
        # Extract model paths from filenames
        model_paths = []
        for f in files:
            # Convert filename back to model path
            # e.g., auth_user_data.json -> auth.User
            name = f.replace('_data.json', '')
            parts = name.split('_')
            if len(parts) >= 2:
                app_label = parts[0]
                model_name = ''.join(parts[1:])
                model_path = f"{app_label}.{model_name}"
                model_paths.append(model_path)
        
        return model_paths
    
    def resolve_related_object(self, model, data, field_name, related_value):
        """Resolve a related object reference."""
        if related_value is None:
            return None
        
        # If related_value is already a dictionary with id, try to import/use it
        if isinstance(related_value, dict) and 'id' in related_value:
            # Check if it's already imported
            related_model = model._meta.get_field(field_name).related_model
            model_key = f"{related_model._meta.app_label}.{related_model._meta.object_name}"
            
            obj_id = related_value['id']
            key = f"{model_key}.{obj_id}"
            
            if key in self.imported_ids:
                return self.imported_ids[key]
            
            # Try to find or create it
            try:
                return self.create_or_get_object(related_model, related_value)
            except Exception as e:
                print(f"  Warning: Could not resolve related object: {e}")
                return None
        
        # If it's just an ID, try to look it up
        if isinstance(related_value, (int, str)):
            related_model = model._meta.get_field(field_name).related_model
            try:
                return related_model.objects.get(pk=related_value)
            except ObjectDoesNotExist:
                # Try to find by other fields if it's a string
                if isinstance(related_value, str):
                    try:
                        # Try to find by name or code
                        for field in ['name', 'code', 'username', 'email']:
                            if hasattr(related_model, field):
                                try:
                                    return related_model.objects.get(**{field: related_value})
                                except (ObjectDoesNotExist, MultipleObjectsReturned):
                                    continue
                    except:
                        pass
                return None
        
        return None
    
    def create_or_get_object(self, model, data):
        """Create or get an object from data."""
        # Build the lookup
        lookup = {}
        for field_name, value in data.items():
            field = model._meta.get_field(field_name)
            
            # Skip related fields for lookup - we'll handle them after creation
            if isinstance(field, (models.ForeignKey, models.OneToOneField)):
                continue
            if field_name in ['id']:
                continue
            
            # For ManyToMany, skip lookup
            if isinstance(field, models.ManyToManyField):
                continue
            
            # Process date/time fields
            if isinstance(value, str) and isinstance(field, (models.DateField, models.DateTimeField)):
                try:
                    lookup[field_name] = datetime.fromisoformat(value)
                except:
                    lookup[field_name] = value
            elif value is not None:
                lookup[field_name] = value
        
        # Try to find existing object
        obj = None
        if lookup:
            try:
                obj = model.objects.get(**lookup)
                model_key = f"{model._meta.app_label}.{model._meta.object_name}.{obj.pk}"
                self.imported_ids[model_key] = obj
                return obj
            except ObjectDoesNotExist:
                pass
            except MultipleObjectsReturned:
                obj = model.objects.filter(**lookup).first()
                if obj:
                    model_key = f"{model._meta.app_label}.{model._meta.object_name}.{obj.pk}"
                    self.imported_ids[model_key] = obj
                    return obj
        
        # Create new object
        obj_data = {}
        
        for field_name, value in data.items():
            field = model._meta.get_field(field_name)
            
            # Skip ManyToMany for creation
            if isinstance(field, models.ManyToManyField):
                continue
            
            # Handle foreign keys
            if isinstance(field, (models.ForeignKey, models.OneToOneField)):
                if value:
                    related_obj = self.resolve_related_object(model, data, field_name, value)
                    if related_obj:
                        obj_data[field_name] = related_obj
                    else:
                        # Try to find by ID in the data
                        if isinstance(value, dict) and 'id' in value:
                            rel_model = field.related_model
                            try:
                                rel_obj = rel_model.objects.get(pk=value['id'])
                                obj_data[field_name] = rel_obj
                            except ObjectDoesNotExist:
                                # Try to create it if it's a dictionary
                                if isinstance(value, dict):
                                    try:
                                        rel_obj = self.create_or_get_object(rel_model, value)
                                        if rel_obj:
                                            obj_data[field_name] = rel_obj
                                    except Exception as e:
                                        print(f"    Could not create related object: {e}")
                continue
            
            # Process date/time fields
            if isinstance(value, str) and isinstance(field, (models.DateField, models.DateTimeField)):
                try:
                    obj_data[field_name] = datetime.fromisoformat(value)
                except:
                    obj_data[field_name] = value
            elif value is not None:
                obj_data[field_name] = value
        
        # Create the object
        try:
            obj = model.objects.create(**obj_data)
            model_key = f"{model._meta.app_label}.{model._meta.object_name}.{obj.pk}"
            self.imported_ids[model_key] = obj
            
            # Handle ManyToMany fields
            for field_name, value in data.items():
                field = model._meta.get_field(field_name)
                if isinstance(field, models.ManyToManyField):
                    if value and isinstance(value, list):
                        related_model = field.related_model
                        for rel_id in value:
                            try:
                                rel_obj = related_model.objects.get(pk=rel_id)
                                getattr(obj, field_name).add(rel_obj)
                            except ObjectDoesNotExist:
                                # Try to find by other criteria
                                try:
                                    # Look for the object that matches this ID in imported_ids
                                    found = False
                                    for key, imported_obj in self.imported_ids.items():
                                        if (imported_obj._meta.app_label == related_model._meta.app_label and
                                            imported_obj._meta.object_name == related_model._meta.object_name and
                                            imported_obj.pk == rel_id):
                                            getattr(obj, field_name).add(imported_obj)
                                            found = True
                                            break
                                    if not found:
                                        print(f"    Could not find related object with id {rel_id} for {field_name}")
                                except Exception as e:
                                    print(f"    Error adding many-to-many relation: {e}")
            
            return obj
            
        except Exception as e:
            print(f"  Error creating {model.__name__}: {e}")
            print(f"  Data: {data}")
            raise
    
    def import_model(self, model_path):
        """Import a single model from its JSON file."""
        filename = f"{model_path.replace('.', '_')}_data.json"
        filepath = os.path.join(self.import_dir, filename)
        
        if not os.path.exists(filepath):
            print(f"  File not found: {filename}")
            return False
        
        print(f"Importing {model_path} from {filename}...")
        
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            model = self.get_model_from_string(model_path)
            if not model:
                print(f"  ✗ Model not found: {model_path}")
                return False
            
            records = data.get('data', [])
            count = len(records)
            imported = 0
            
            if count == 0:
                print(f"  → No records to import")
                self.stats['imported_models'] += 1
                return True
            
            # Import each record
            with transaction.atomic():
                for record in records:
                    try:
                        # Skip if already imported
                        obj_id = record.get('id')
                        if obj_id:
                            model_key = f"{model_path}.{obj_id}"
                            if model_key in self.imported_ids:
                                print(f"    Skipping already imported record {obj_id}")
                                self.stats['skipped_records'] += 1
                                continue
                        
                        self.create_or_get_object(model, record)
                        imported += 1
                        
                    except Exception as e:
                        print(f"    ✗ Error importing record {record.get('id')}: {e}")
                        self.stats['errors'].append({
                            'model': model_path,
                            'record': record.get('id'),
                            'error': str(e)
                        })
            
            print(f"  ✓ Imported {imported}/{count} records")
            self.stats['imported_records'] += imported
            self.stats['total_records'] += count
            self.stats['imported_models'] += 1
            
            return True
            
        except Exception as e:
            print(f"  ✗ Error importing {model_path}: {e}")
            self.stats['errors'].append({
                'model': model_path,
                'error': str(e)
            })
            return False
    
    def import_all(self):
        """Import all models in the correct order."""
        print(f"Starting import from {self.import_dir}/")
        print("-" * 50)
        
        import_order = self.get_import_order()
        
        if not import_order:
            print("No import files found.")
            return
        
        self.stats['total_models'] = len(import_order)
        
        for model_path in import_order:
            self.import_model(model_path)
        
        print("-" * 50)
        print("Import Summary:")
        print(f"  Total Models: {self.stats['total_models']}")
        print(f"  Imported Models: {self.stats['imported_models']}")
        print(f"  Total Records: {self.stats['total_records']}")
        print(f"  Imported Records: {self.stats['imported_records']}")
        print(f"  Skipped Records: {self.stats['skipped_records']}")
        
        if self.stats['errors']:
            print(f"  Errors: {len(self.stats['errors'])}")
            for error in self.stats['errors'][:5]:
                print(f"    - {error.get('model')}: {error.get('error')}")
            if len(self.stats['errors']) > 5:
                print(f"    ... and {len(self.stats['errors']) - 5} more")
        
        print("\nImport complete!")


def main():
    """Main entry point."""
    import_dir = 'exports'
    importer = DataImporter(import_dir)
    importer.import_all()


if __name__ == "__main__":
    main()