from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django import forms
from timetable.models import Timetable, ExamTimetable, TempTimetable, ExamTempTimetable

# Map model names to classes
MODEL_MAP = {
    "timetable": Timetable,
    "exam_timetable": ExamTimetable,
    "temp_timetable": TempTimetable,
    "exam_temp_timetable": ExamTempTimetable,
}

# Generic form builder
def get_timetable_form(model_class):
    class _TimetableForm(forms.ModelForm):
        class Meta:
            model = model_class
            fields = ["venue", "day", "start_time", "end_time"]
    return _TimetableForm

# Edit entry
def edit_timetable_entry(request, model_name, entry_id):
    model_class = MODEL_MAP.get(model_name)
    entry = get_object_or_404(model_class, id=entry_id)

    FormClass = get_timetable_form(model_class)
    if request.method == "POST":
        form = FormClass(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            messages.success(request, f"{model_name.replace('_',' ').title()} entry updated.")
    return redirect(f"{request.META.get('HTTP_REFERER', '/timetable/editor/')}?model={model_name}")

# Delete entry
def delete_timetable_entry(request, model_name, entry_id):
    model_class = MODEL_MAP.get(model_name)
    entry = get_object_or_404(model_class, id=entry_id)

    if request.method == "POST":
        entry.delete()
        messages.success(request, f"{model_name.replace('_',' ').title()} entry deleted.")
    return redirect(f"{request.META.get('HTTP_REFERER', '/timetable/editor/')}?model={model_name}")
