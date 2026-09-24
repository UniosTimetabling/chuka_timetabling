from django.http import JsonResponse
import datetime
from timetable.models import ExamSchedulerConfig
def update_exam_config(request):
    """AJAX: Update ExamSchedulerConfig"""
    if request.method == "POST":
        config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)

        start_date = request.POST.get("start_date")
        start_time = request.POST.get("start_time")
        end_time = request.POST.get("end_time")
        slot_size = request.POST.get("slot_size")
        excluded_days = request.POST.getlist("excluded_days[]")
        max_exam_days = request.POST.get("max_exam_days")

        if start_date:
            config.start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        if start_time:
            config.start_time = datetime.datetime.strptime(start_time, "%H:%M").time()
        if end_time:
            config.end_time = datetime.datetime.strptime(end_time, "%H:%M").time()
        if slot_size:
            config.slot_size = int(slot_size)
        if max_exam_days:
            config.max_exam_days = int(max_exam_days)
        config.excluded_days = ",".join(excluded_days)

        config.save()
        return JsonResponse({"status": "success", "message": "Configuration saved!"})

    return JsonResponse({"status": "error", "message": "Invalid method"})