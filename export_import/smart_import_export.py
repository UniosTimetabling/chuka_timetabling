import io
import json
import logging
import pandas as pd
import numpy as np
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.apps import apps
from django import forms
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.contrib.auth.models import User
import re
from difflib import get_close_matches

logger = logging.getLogger(__name__)

# List of models we want to import/export with their display names
MODEL_MAP = {
    "Faculty": "faculty_management.Faculty",
    "Department": "department_management.Department",
    "Lecturer": "lecturer_portal.Lecturer",
    "Program": "program_management.Program",
    "ProgramCourse": "program_management.ProgramCourse",
    "ProgramCode": "program_management.ProgramCode",
    "Building": "room_management.Building",
    "Venue": "room_management.Venue",
    "LabVenue": "room_management.LabVenue",
}

# Foreign key mappings for each model - (target_model, lookup_field)
FOREIGN_KEY_MAPPINGS = {
    'Department': {
        'faculty': ('faculty_management.Faculty', 'name'),
        'leader': ('auth.User', 'username'),
    },
    'Lecturer': {
        'user': ('auth.User', 'username'),
        'department': ('department_management.Department', 'name'),
    },
    'Program': {
        'department': ('department_management.Department', 'name'),
    },
    'ProgramCourse': {
        'program': ('program_management.Program', 'name'),
    },
    'ProgramCode': {
        'program': ('program_management.Program', 'name'),
    },
    'Building': {
        'faculty': ('faculty_management.Faculty', 'name'),
    },
    'Venue': {
        'building': ('room_management.Building', 'code'),
    },
    'LabVenue': {},
    'Faculty': {},
}

# Required fields for each model
REQUIRED_FIELDS = {
    'Faculty': ['name'],
    'Department': ['name', 'faculty'],
    'Lecturer': ['payroll_number', 'name', 'email', 'designation'],
    'Program': ['name', 'department'],
    'ProgramCourse': ['program', 'course_code', 'course_name'],
    'ProgramCode': ['program', 'code'],
    'Building': ['name', 'code'],
    'Venue': ['code'],
    'LabVenue': ['code']
}

# Helper to get model class
def get_model(name):
    app_label, model_name = MODEL_MAP[name].split(".")
    return apps.get_model(app_label, model_name)

# Form for file upload
class ImportFileForm(forms.Form):
    model_name = forms.ChoiceField(
        choices=[(m, m) for m in MODEL_MAP.keys()],
        label="Select Model"
    )
    file = forms.FileField(label="Upload File (CSV, Excel, JSON)")

def detect_file_type(file):
    """Detect file type based on extension and content"""
    filename = file.name.lower()
    
    if filename.endswith('.csv'):
        return 'csv'
    elif filename.endswith(('.xls', '.xlsx')):
        return 'excel'
    elif filename.endswith('.json'):
        return 'json'
    else:
        return 'unknown'

def parse_uploaded_file(file, file_type):
    """Parse uploaded file based on detected type"""
    try:
        if file_type == 'csv':
            df = pd.read_csv(file)
        elif file_type == 'excel':
            df = pd.read_excel(file)
        elif file_type == 'json':
            content = json.load(file)
            if isinstance(content, list):
                df = pd.DataFrame(content)
            elif isinstance(content, dict):
                for key in ['data', 'records', 'rows']:
                    if key in content and isinstance(content[key], list):
                        df = pd.DataFrame(content[key])
                        break
                else:
                    df = pd.DataFrame([content])
            else:
                raise ValueError("Unsupported JSON format")
        else:
            raise ValueError("Unsupported file type")
        
        df = df.replace({np.nan: None})
        df.columns = [str(col).strip() for col in df.columns]
        
        return df
    
    except Exception as e:
        raise ValueError(f"Failed to parse file: {str(e)}")

def find_similar_fields(uploaded_fields, model_fields, threshold=0.6):
    """Find similar field names using fuzzy matching"""
    similar = {}
    for uf in uploaded_fields:
        matches = get_close_matches(uf.lower(), [f.lower() for f in model_fields], n=1, cutoff=threshold)
        if matches:
            for mf in model_fields:
                if mf.lower() == matches[0].lower():
                    similar[uf] = mf
                    break
    return similar

def prepare_preview_data(df, uploaded_fields, limit=10):
    """Prepare preview data in a template-friendly format"""
    preview_rows = []
    for index, row in df.head(limit).iterrows():
        row_data = []
        for field in uploaded_fields:
            value = row.get(field)
            if pd.isna(value) or value is None:
                row_data.append('')
            else:
                str_value = str(value)
                if len(str_value) > 50:
                    row_data.append(str_value[:47] + "...")
                else:
                    row_data.append(str_value)
        preview_rows.append(row_data)
    return preview_rows

def resolve_foreign_key_value(field_name, value, model_name):
    """Resolve a foreign key string value to an actual model instance"""
    if model_name not in FOREIGN_KEY_MAPPINGS:
        return value
    
    if field_name not in FOREIGN_KEY_MAPPINGS[model_name]:
        return value
    
    if not value or pd.isna(value):
        return None
    
    target_model_path, lookup_field = FOREIGN_KEY_MAPPINGS[model_name][field_name]
    
    try:
        # Get the target model
        app_label, model_class_name = target_model_path.split('.')
        target_model = apps.get_model(app_label, model_class_name)
        
        # Clean the lookup value
        lookup_value = str(value).strip()
        
        # Try to find the object (case-insensitive for name fields)
        if lookup_field == 'name':
            obj = target_model.objects.filter(name__iexact=lookup_value).first()
            if not obj:
                # Try with different variations
                variations = [
                    lookup_value,
                    lookup_value.title(),
                    lookup_value.upper(),
                    lookup_value.lower(),
                ]
                for var in variations:
                    obj = target_model.objects.filter(name__iexact=var).first()
                    if obj:
                        break
        elif lookup_field == 'username':
            obj = target_model.objects.filter(username=lookup_value).first()
        elif lookup_field == 'code':
            obj = target_model.objects.filter(code=lookup_value).first()
        else:
            obj = target_model.objects.filter(**{lookup_field: lookup_value}).first()
        
        if not obj:
            # Try partial match for name fields
            if lookup_field == 'name':
                obj = target_model.objects.filter(name__icontains=lookup_value).first()
                if not obj:
                    # Try splitting and looking for first word
                    first_word = lookup_value.split()[0] if ' ' in lookup_value else lookup_value
                    obj = target_model.objects.filter(name__icontains=first_word).first()
            
            if not obj:
                raise ValueError(f"Cannot find {target_model.__name__} with {lookup_field}='{lookup_value}'. "
                               f"Please ensure this {target_model.__name__} exists before importing.")
        
        return obj
        
    except Exception as e:
        raise ValueError(f"Error resolving foreign key {field_name}='{value}': {str(e)}")

def resolve_all_foreign_keys(row_dict, model_name):
    """Resolve all foreign key fields in a row"""
    resolved_row = {}
    errors = []
    
    for field_name, value in row_dict.items():
        try:
            if field_name in FOREIGN_KEY_MAPPINGS.get(model_name, {}):
                resolved_row[field_name] = resolve_foreign_key_value(field_name, value, model_name)
            else:
                resolved_row[field_name] = value
        except ValueError as e:
            errors.append(str(e))
            resolved_row[field_name] = None
    
    return resolved_row, errors

def find_existing_object(model, row_dict, model_name):
    """Find existing object based on unique/identifying fields"""
    # Try primary key or id field first
    if 'id' in row_dict and row_dict['id']:
        try:
            return model.objects.get(id=row_dict['id'])
        except (ObjectDoesNotExist, ValueError):
            pass
    
    # Try unique fields based on model type
    if model_name == 'Faculty' and 'name' in row_dict:
        return model.objects.filter(name__iexact=row_dict['name']).first()
    
    elif model_name == 'Department' and 'name' in row_dict:
        return model.objects.filter(name__iexact=row_dict['name']).first()
    
    elif model_name == 'Lecturer':
        if 'payroll_number' in row_dict and row_dict['payroll_number']:
            return model.objects.filter(payroll_number=row_dict['payroll_number']).first()
        elif 'email' in row_dict and row_dict['email']:
            return model.objects.filter(email__iexact=row_dict['email']).first()
    
    elif model_name == 'Program' and 'name' in row_dict:
        return model.objects.filter(name__iexact=row_dict['name']).first()
    
    elif model_name == 'ProgramCourse':
        if 'course_code' in row_dict and row_dict['course_code']:
            return model.objects.filter(course_code=row_dict['course_code']).first()
    
    elif model_name == 'ProgramCode' and 'code' in row_dict:
        return model.objects.filter(code=row_dict['code']).first()
    
    elif model_name == 'Building':
        if 'code' in row_dict and row_dict['code']:
            return model.objects.filter(code=row_dict['code']).first()
        elif 'name' in row_dict and row_dict['name']:
            return model.objects.filter(name__iexact=row_dict['name']).first()
    
    elif model_name == 'Venue' and 'code' in row_dict:
        return model.objects.filter(code=row_dict['code']).first()
    
    elif model_name == 'LabVenue' and 'code' in row_dict:
        return model.objects.filter(code=row_dict['code']).first()
    
    return None

@transaction.atomic
def import_data(model, df, model_name):
    """Import data with validation and error handling"""
    created = 0
    updated = 0
    errors = []
    
    for index, row in df.iterrows():
        try:
            row_num = index + 2
            # Convert row to dict
            row_dict = {}
            for col in df.columns:
                value = row[col]
                if pd.isna(value):
                    row_dict[col] = None
                elif isinstance(value, str):
                    row_dict[col] = value.strip()
                else:
                    row_dict[col] = value
            
            # Check required fields
            required = REQUIRED_FIELDS.get(model_name, [])
            missing_fields = []
            for field in required:
                if field not in row_dict or row_dict[field] is None or (isinstance(row_dict[field], str) and not row_dict[field].strip()):
                    missing_fields.append(field)
            
            if missing_fields:
                errors.append(f"Row {row_num}: Missing required fields: {', '.join(missing_fields)}")
                continue
            
            # Resolve foreign keys
            resolved_row, fk_errors = resolve_all_foreign_keys(row_dict, model_name)
            if fk_errors:
                errors.extend([f"Row {row_num}: {err}" for err in fk_errors])
                continue
            
            # Find existing object
            existing_obj = find_existing_object(model, resolved_row, model_name)
            
            if existing_obj:
                # Update existing object
                for key, value in resolved_row.items():
                    if key != 'id' and hasattr(existing_obj, key) and value is not None:
                        setattr(existing_obj, key, value)
                existing_obj.save()
                updated += 1
            else:
                # Create new object
                # Remove any None values that might cause issues
                create_data = {k: v for k, v in resolved_row.items() if v is not None}
                model.objects.create(**create_data)
                created += 1
                
        except Exception as e:
            errors.append(f"Row {index + 2}: {str(e)}")
    
    return created, updated, errors

def export_model(model, format='csv'):
    """Export model data to specified format"""
    fields = [f.name for f in model._meta.get_fields() 
              if not f.many_to_many and not f.auto_created and f.name != 'password']
    
    data = []
    for obj in model.objects.all():
        row = {}
        for field in fields:
            try:
                value = getattr(obj, field)
                if hasattr(value, '__class__') and hasattr(value, '_meta'):
                    # It's a model instance (foreign key)
                    if hasattr(value, 'name'):
                        row[field] = value.name
                    elif hasattr(value, 'username'):
                        row[field] = value.username
                    elif hasattr(value, 'code'):
                        row[field] = value.code
                    else:
                        row[field] = str(value.id) if hasattr(value, 'id') else str(value)
                else:
                    row[field] = value
            except Exception as e:
                logger.debug(
                    "export_model: could not serialize field '%s' on %s #%s: %s",
                    field, model.__name__, getattr(obj, "pk", "?"), e,
                )
                row[field] = ''
        data.append(row)
    
    df = pd.DataFrame(data, columns=fields)
    
    if format == 'csv':
        buffer = io.BytesIO()
        df.to_csv(buffer, index=False, encoding='utf-8')
        buffer.seek(0)
        content_type = 'text/csv'
        ext = 'csv'
    elif format == 'excel':
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Data')
        buffer.seek(0)
        content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ext = 'xlsx'
    elif format == 'json':
        buffer = io.BytesIO()
        json_str = df.to_json(orient='records', indent=2)
        buffer.write(json_str.encode('utf-8'))
        buffer.seek(0)
        content_type = 'application/json'
        ext = 'json'
    
    return buffer, content_type, ext

def get_template(model):
    """Generate template with sample data"""
    model_name = model.__name__
    fields = [f.name for f in model._meta.get_fields() 
              if not f.many_to_many and not f.auto_created and f.name != 'password']
    
    # Create sample data based on model type
    sample_data = []
    
    if model_name == 'Faculty':
        sample_data = [{'name': 'Faculty of Science', 'description': 'Science faculty description'}]
    
    elif model_name == 'Department':
        # Need to reference an existing faculty
        faculty = apps.get_model('faculty_management', 'Faculty').objects.first()
        if faculty:
            sample_data = [{
                'name': 'Computer Science Department',
                'faculty': faculty.name,
                'description': 'Department description here'
            }]
        else:
            sample_data = [{
                'name': 'Computer Science Department',
                'faculty': 'Faculty of Science',
                'description': 'Department description here'
            }]
    
    elif model_name == 'Lecturer':
        sample_data = [{
            'payroll_number': 'EMP001',
            'name': 'John Doe',
            'email': 'john.doe@university.edu',
            'designation': 'Dr',
        }]
    
    elif model_name == 'Program':
        # Need to reference an existing department
        department = apps.get_model('department_management', 'Department').objects.first()
        if department:
            sample_data = [{
                'name': 'Bachelor of Computer Science',
                'department': department.name,
                'description': 'Program description here'
            }]
        else:
            sample_data = [{
                'name': 'Bachelor of Computer Science',
                'department': 'Computer Science Department',
                'description': 'Program description here'
            }]
    
    elif model_name == 'ProgramCourse':
        program = apps.get_model('program_management', 'Program').objects.first()
        if program:
            sample_data = [{
                'program': program.name,
                'course_code': 'COSC101',
                'course_name': 'Introduction to Programming',
                'year': 1,
                'semester': 1
            }]
        else:
            sample_data = [{
                'program': 'Bachelor of Computer Science',
                'course_code': 'COSC101',
                'course_name': 'Introduction to Programming',
                'year': 1,
                'semester': 1
            }]
    
    elif model_name == 'ProgramCode':
        program = apps.get_model('program_management', 'Program').objects.first()
        if program:
            sample_data = [{
                'program': program.name,
                'code': 'BSC-CS'
            }]
        else:
            sample_data = [{
                'program': 'Bachelor of Computer Science',
                'code': 'BSC-CS'
            }]
    
    elif model_name == 'Building':
        faculty = apps.get_model('faculty_management', 'Faculty').objects.first()
        if faculty:
            sample_data = [{
                'name': 'Science Building',
                'code': 'SB',
                'faculty': faculty.name,
                'description': 'Building description'
            }]
        else:
            sample_data = [{
                'name': 'Science Building',
                'code': 'SB',
                'faculty': 'Faculty of Science',
                'description': 'Building description'
            }]
    
    elif model_name == 'Venue':
        building = apps.get_model('room_management', 'Building').objects.first()
        if building:
            sample_data = [{
                'code': 'SB-101',
                'building': building.code,
                'capacity': 50,
                'description': 'Lecture hall'
            }]
        else:
            sample_data = [{
                'code': 'SB-101',
                'building': 'SB',
                'capacity': 50,
                'description': 'Lecture hall'
            }]
    
    elif model_name == 'LabVenue':
        sample_data = [{
            'code': 'CS-LAB1',
            'capacity': 30,
            'description': 'Computer lab',
            'equipment': '40 PCs, projector'
        }]
    
    else:
        sample_data = [{}]
    
    # Create DataFrame
    df = pd.DataFrame(sample_data)
    
    # Ensure all fields are present
    for field in fields:
        if field not in df.columns:
            df[field] = ''
    
    # Reorder to match model fields
    df = df[fields]
    
    buffer = io.BytesIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)
    
    return buffer

# Main view
def import_export_view(request):
    context = {
        "form": ImportFileForm(),
        "models": list(MODEL_MAP.keys()),
        "page_title": "Smart Data Import/Export",
        "REQUIRED_FIELDS": REQUIRED_FIELDS,
    }
    
    # Handle file upload and preview
    if request.method == "POST":
        form = ImportFileForm(request.POST, request.FILES)
        
        if form.is_valid():
            model_name = form.cleaned_data["model_name"]
            file = request.FILES["file"]
            
            model = get_model(model_name)
            
            try:
                # Detect file type
                file_type = detect_file_type(file)
                if file_type == 'unknown':
                    messages.error(request, "Unsupported file type. Please upload CSV, Excel, or JSON.")
                    return redirect(request.path)
                
                # Parse file
                df = parse_uploaded_file(file, file_type)
                
                if df.empty:
                    messages.warning(request, "The uploaded file is empty.")
                    return redirect(request.path)
                
                # Get model fields
                model_fields = [f.name for f in model._meta.get_fields() 
                              if not f.many_to_many and not f.auto_created and f.name != 'password']
                uploaded_fields = list(df.columns)
                
                # Find similar field names
                similar_fields = find_similar_fields(uploaded_fields, model_fields)
                
                # Check for missing required fields
                required_fields = REQUIRED_FIELDS.get(model_name, [])
                missing_required = [f for f in required_fields if f not in uploaded_fields and f not in similar_fields.values()]
                
                # Prepare preview data
                preview_rows = prepare_preview_data(df, uploaded_fields)
                
                # Store data in session for confirmation
                request.session['import_data'] = {
                    'model': model_name,
                    'data': df.to_dict('records'),
                    'columns': uploaded_fields,
                    'file_name': file.name,
                    'similar_fields': similar_fields
                }
                
                context.update({
                    "preview_mode": True,
                    "model_name": model_name,
                    "uploaded_fields": uploaded_fields,
                    "model_fields": model_fields,
                    "similar_fields": similar_fields,
                    "missing_required": missing_required,
                    "preview_rows": preview_rows,
                    "uploaded_fields_list": uploaded_fields,
                    "total_rows": len(df),
                    "file_type": file_type,
                    "file_name": file.name,
                })
                
                if similar_fields:
                    messages.info(request, f"Found similar fields: {', '.join([f'{k} → {v}' for k, v in similar_fields.items()])}")
                
                if missing_required:
                    messages.warning(request, f"Missing required fields: {', '.join(missing_required)}")
                
            except Exception as e:
                messages.error(request, f"Error processing file: {str(e)}")
                return redirect(request.path)
        else:
            messages.error(request, "Form is invalid. Please check your inputs.")
    
    # Handle import confirmation
    elif request.GET.get('action') == 'confirm_import':
        import_data_info = request.session.get('import_data')
        
        if not import_data_info:
            messages.error(request, "No import data found. Please upload a file first.")
            return redirect(request.path)
        
        model_name = import_data_info['model']
        model = get_model(model_name)
        data = import_data_info['data']
        similar_fields = import_data_info.get('similar_fields', {})
        
        # Apply field mapping from similar fields
        df_data = []
        for row in data:
            new_row = {}
            for key, value in row.items():
                # Map similar field names
                if key in similar_fields:
                    new_key = similar_fields[key]
                else:
                    new_key = key
                new_row[new_key] = value
            df_data.append(new_row)
        
        df = pd.DataFrame(df_data)
        
        # Import data
        created, updated, errors = import_data(model, df, model_name)
        
        if errors:
            messages.error(request, f"Import completed with {len(errors)} errors.")
            request.session['import_errors'] = errors[:20]  # Show first 20 errors
        else:
            messages.success(request, f"Import successful: {created} created, {updated} updated.")
        
        request.session.pop('import_data', None)
        return redirect(request.path)
    
    # Handle export
    elif request.GET.get('action') == 'export':
        model_name = request.GET.get('model')
        format = request.GET.get('format', 'csv')
        
        if model_name not in MODEL_MAP:
            messages.error(request, "Invalid model specified.")
            return redirect(request.path)
        
        model = get_model(model_name)
        
        try:
            buffer, content_type, ext = export_model(model, format)
            
            response = HttpResponse(buffer, content_type=content_type)
            response['Content-Disposition'] = f'attachment; filename="{model_name}_export.{ext}"'
            return response
            
        except Exception as e:
            messages.error(request, f"Export failed: {str(e)}")
            return redirect(request.path)
    
    # Handle template download
    elif request.GET.get('action') == 'template':
        model_name = request.GET.get('model')
        
        if model_name not in MODEL_MAP:
            messages.error(request, "Invalid model specified.")
            return redirect(request.path)
        
        model = get_model(model_name)
        buffer = get_template(model)
        
        response = HttpResponse(buffer, content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{model_name}_template.csv"'
        return response
    
    # Display import errors if any
    import_errors = request.session.pop('import_errors', None)
    if import_errors:
        context['import_errors'] = import_errors
    
    return render(request, "export/smart_import_export_page.html", context)