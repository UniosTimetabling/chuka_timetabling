from django.shortcuts import render, redirect
from django.contrib import messages
from timetable.models import SchedulerConfig
import datetime

# ======== BASE PAGE VIEW ========
def autoscheduler_home(request):
    """Render base page. POST saves the quick config strip (start/end/slot_size)."""
    config = SchedulerConfig.objects.first()
    if not config:
        config = SchedulerConfig.objects.create(start_time="08:00", end_time="17:00", slot_size=1)

    if request.method == "POST" and "save_timetable_config" in request.POST:
        start_time_raw = request.POST.get("start_time", "").strip()
        end_time_raw   = request.POST.get("end_time", "").strip()
        slot_size_raw  = request.POST.get("slot_size", "").strip()

        errors = []
        try:
            start_time = datetime.datetime.strptime(start_time_raw, "%H:%M").time()
        except ValueError:
            errors.append("Invalid start time.")
        try:
            end_time = datetime.datetime.strptime(end_time_raw, "%H:%M").time()
        except ValueError:
            errors.append("Invalid end time.")
        try:
            slot_size = int(slot_size_raw)
            if slot_size < 1:
                raise ValueError
        except (ValueError, TypeError):
            errors.append("Slot size must be a positive integer.")

        if not errors:
            if start_time >= end_time:
                errors.append("Start time must be before end time.")

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            config.start_time = start_time
            config.end_time   = end_time
            config.slot_size  = slot_size
            config.save(update_fields=["start_time", "end_time", "slot_size"])
            messages.success(request, "Timetable config saved.")
            return redirect("autoscheduler_home")

    context = {
        "default_start": config.start_time.strftime("%H:%M"),
        "default_end":   config.end_time.strftime("%H:%M"),
        "default_slot":  config.slot_size,
        "config": config,
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        "navbar_links": {
            "Go to Schedule Exam Timetable": "exam_autoscheduler_home",
            "Publish to Main Timetable":     "publish_to_main",
        },
    }
    return render(request, "dashboard/autoscheduler.html", context)
