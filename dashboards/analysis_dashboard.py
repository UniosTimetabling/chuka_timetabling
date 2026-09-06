import json
from django.shortcuts import render
from django.apps import apps
from django.contrib.auth.decorators import login_required
from django.db.models import ForeignKey, ManyToManyField, OneToOneField
from django.utils.safestring import mark_safe
from django.utils.dateformat import format
from datetime import datetime


@login_required
def analysis_dashboard(request):
    """
    Simple dashboard for Timetable Department to understand data structure
    """
    app_label = "timetable"  # change if your app label differs
    try:
        app_config = apps.get_app_config(app_label)
    except LookupError:
        # If app label differs, try lowercase fallback
        app_config = apps.get_app_config(app_label.lower())

    models = list(app_config.get_models())

    model_summaries = []
    total_records = 0

    # Get current date for display
    current_date = datetime.now()
    formatted_date = format(current_date, 'F d, Y')
    formatted_time = format(current_date, 'h:i A')

    for model in models:
        model_name = model._meta.object_name
        
        # Simplify model names for non-technical users
        friendly_name = model_name.replace('_', ' ').title()
        
        try:
            count = model.objects.count()
        except Exception:
            count = 0

        total_records += count

        relations = []

        # Forward fields (FK, M2M, O2O) - simplified for clarity
        for field in model._meta.get_fields():
            if isinstance(field, ForeignKey):
                related_model = field.related_model
                friendly_field = field.name.replace('_', ' ').title()
                try:
                    related_count = related_model.objects.count()
                except Exception:
                    related_count = 0
                relations.append({
                    "name": f"{friendly_field}",
                    "type": "Linked To",
                    "related_model": related_model._meta.object_name.replace('_', ' ').title(),
                    "related_count": related_count,
                })

            elif isinstance(field, ManyToManyField):
                related_model = field.remote_field.model
                friendly_field = field.name.replace('_', ' ').title()
                try:
                    related_count = related_model.objects.count()
                except Exception:
                    related_count = 0
                relations.append({
                    "name": f"{friendly_field}",
                    "type": "Connected To",
                    "related_model": related_model._meta.object_name.replace('_', ' ').title(),
                    "related_count": related_count,
                })

        # Only show top 3 relations to avoid clutter
        relations = sorted(relations, key=lambda x: x['related_count'], reverse=True)[:3]

        model_summaries.append({
            "model": friendly_name,
            "technical_name": model_name,
            "app_label": model._meta.app_label,
            "count": count,
            "relations": relations,
            "has_data": count > 0,
        })

    # Sort models by count (highest first)
    model_summaries.sort(key=lambda x: x['count'], reverse=True)

    # Prepare data for charts
    model_names = [m["model"] for m in model_summaries]
    model_counts = [m["count"] for m in model_summaries]

    # Calculate some statistics
    avg_records = total_records // max(len(models), 1)
    models_with_data = sum(1 for m in model_summaries if m['has_data'])

    context = {
        "total_models": len(models),
        "total_records": total_records,
        "model_summaries": model_summaries,
        "models_with_data": models_with_data,
        "avg_records": avg_records,
        "current_date": formatted_date,
        "current_time": formatted_time,
        # JSON for charts
        "chart_models_json": mark_safe(json.dumps(model_names)),
        "chart_counts_json": mark_safe(json.dumps(model_counts)),
        "model_summaries_json": mark_safe(json.dumps(model_summaries)),
    }

    return render(request, "dashboard/analysis_dashboard.html", context)