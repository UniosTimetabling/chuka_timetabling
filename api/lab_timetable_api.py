import datetime
from django.shortcuts import  get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.utils.dateparse import parse_time

from timetable.models import (
    LabTimetable,LabSchedulerConfig
)
from course_allocation.models import LabAllocation
from room_management.models import LabVenue
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def _get_scheduler_config():
    cfg, _ = LabSchedulerConfig.objects.get_or_create(
        pk=1,
        defaults={"start_time": "07:00", "end_time": "19:00", "slot_size": 2},
    )
    return cfg


def _cfg_to_dict(cfg):
    return {
        "id": cfg.id,
        "start_time": cfg.start_time.strftime("%H:%M"),
        "end_time": cfg.end_time.strftime("%H:%M"),
        "slot_size": cfg.slot_size,
    }





@login_required
@require_http_methods(["POST"])
def lab_timetable_api(request):
    action = request.POST.get("action")

    if action == "update_config":
        start_time = parse_time(request.POST.get("start_time"))
        end_time = parse_time(request.POST.get("end_time"))
        slot_size = int(request.POST.get("slot_size"))
        cfg = _get_scheduler_config()
        cfg.start_time, cfg.end_time, cfg.slot_size = start_time, end_time, slot_size
        cfg.save()
        return JsonResponse({"status": "success", "config": _cfg_to_dict(cfg)})

    if action == "create_entry":
        alloc = get_object_or_404(LabAllocation, pk=request.POST.get("allocation_id"))
        venue = get_object_or_404(LabVenue, code=request.POST.get("venue"))
        day = request.POST.get("day")
        start_time = parse_time(request.POST.get("start_time"))
        cfg = _get_scheduler_config()
        end_time = (datetime.datetime.combine(datetime.date.today(), start_time)
                    + datetime.timedelta(hours=cfg.slot_size)).time()
        entry, created = LabTimetable.objects.get_or_create(
            lab_allocation=alloc,
            lab_venue=venue,
            day=day,
            start_time=start_time,
            end_time=end_time,
        )
        return JsonResponse({"status": "success", "id": entry.id})

    if action == "delete_entry":
        entry = get_object_or_404(LabTimetable, pk=request.POST.get("entry_id"))
        entry.delete()
        return JsonResponse({"status": "success"})

    return JsonResponse({"status": "error", "message": "Invalid action"}, status=400)