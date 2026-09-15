from admins.forms import SchedulerConfigForm
from timetable.models import SchedulerConfig
from django.shortcuts import render, redirect
from django.contrib import messages

# ── Field groups for each sub-form ──────────────────────────────────────────
_REGULAR_FIELDS  = {"start_time", "end_time", "slot_size"}
_EVENING_FIELDS  = {"enable_evening_classes", "evening_start_time", "evening_end_time", "evening_slot_count"}
_WEEKEND_FIELDS  = {"enable_weekend_classes", "weekend_start_time", "weekend_end_time", "weekend_slot_size"}


def _make_form(post_data, config, allowed_fields):
    """Return a SchedulerConfigForm scoped to only the given fields."""
    form = SchedulerConfigForm(post_data, instance=config)
    # Remove fields not in this sub-form so validation only covers the active section
    for field_name in list(form.fields.keys()):
        if field_name not in allowed_fields:
            del form.fields[field_name]
    return form


def update_scheduler_config(request):
    config = SchedulerConfig.objects.first()
    if not config:
        config = SchedulerConfig.objects.create()

    # ── Identify which sub-form was submitted ───────────────────────────────
    submitted = None
    if request.method == "POST":
        if "save_regular" in request.POST:
            submitted = "regular"
        elif "save_evening" in request.POST:
            submitted = "evening"
        elif "save_weekend" in request.POST:
            submitted = "weekend"

    # ── Build forms (POST data only for the submitted section) ──────────────
    if submitted == "regular":
        form_regular  = _make_form(request.POST, config, _REGULAR_FIELDS)
        form_evening  = SchedulerConfigForm(instance=config)
        form_weekend  = SchedulerConfigForm(instance=config)
        for f in list(form_evening.fields): 
            if f not in _EVENING_FIELDS: del form_evening.fields[f]
        for f in list(form_weekend.fields): 
            if f not in _WEEKEND_FIELDS: del form_weekend.fields[f]

        if form_regular.is_valid():
            form_regular.save()
            messages.success(request, "Regular session config saved.")
            return redirect("update_scheduler_config")

    elif submitted == "evening":
        form_regular  = SchedulerConfigForm(instance=config)
        form_evening  = _make_form(request.POST, config, _EVENING_FIELDS)
        form_weekend  = SchedulerConfigForm(instance=config)
        for f in list(form_regular.fields): 
            if f not in _REGULAR_FIELDS: del form_regular.fields[f]
        for f in list(form_weekend.fields): 
            if f not in _WEEKEND_FIELDS: del form_weekend.fields[f]

        if form_evening.is_valid():
            form_evening.save()
            messages.success(request, "Evening classes config saved.")
            return redirect("update_scheduler_config")

    elif submitted == "weekend":
        form_regular  = SchedulerConfigForm(instance=config)
        form_evening  = SchedulerConfigForm(instance=config)
        form_weekend  = _make_form(request.POST, config, _WEEKEND_FIELDS)
        for f in list(form_regular.fields): 
            if f not in _REGULAR_FIELDS: del form_regular.fields[f]
        for f in list(form_evening.fields): 
            if f not in _EVENING_FIELDS: del form_evening.fields[f]

        if form_weekend.is_valid():
            form_weekend.save()
            messages.success(request, "Weekend classes config saved.")
            return redirect("update_scheduler_config")

    else:
        # GET — all three forms pre-filled from the DB
        form_regular = SchedulerConfigForm(instance=config)
        form_evening = SchedulerConfigForm(instance=config)
        form_weekend = SchedulerConfigForm(instance=config)
        for f in list(form_regular.fields):
            if f not in _REGULAR_FIELDS: del form_regular.fields[f]
        for f in list(form_evening.fields):
            if f not in _EVENING_FIELDS: del form_evening.fields[f]
        for f in list(form_weekend.fields):
            if f not in _WEEKEND_FIELDS: del form_weekend.fields[f]

    return render(request, "timetable/config_form.html", {
        "form_regular": form_regular,
        "form_evening": form_evening,
        "form_weekend": form_weekend,
    })
