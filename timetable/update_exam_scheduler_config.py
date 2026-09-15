from django.http import JsonResponse
import json
from datetime import date, time as time_type
from timetable.models import ExamSchedulerConfig

# ---------- UPDATE CONFIG ----------
def update_scheduler_config(request):
    """AJAX config save handler"""
    if request.method == "POST":
        try:
            config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)

            # Handle form-encoded or JSON
            data = request.POST or json.loads(request.body.decode("utf-8"))

            # start_date — string "YYYY-MM-DD" -> date object
            raw_start_date = data.get("start_date")
            if raw_start_date:
                config.start_date = date.fromisoformat(raw_start_date)

            # start_time — string "HH:MM" -> time object
            raw_start_time = data.get("start_time")
            if raw_start_time:
                h, m = raw_start_time.split(":")[:2]
                config.start_time = time_type(int(h), int(m))

            # end_time — string "HH:MM" -> time object
            raw_end_time = data.get("end_time")
            if raw_end_time:
                h, m = raw_end_time.split(":")[:2]
                config.end_time = time_type(int(h), int(m))

            # slot_size — string "3" or "3.0" -> float
            # Must be float (not Decimal/str) so timedelta(hours=slot_size) never fails
            raw_slot_size = data.get("slot_size")
            if raw_slot_size not in (None, ""):
                config.slot_size = float(raw_slot_size)

            # max_exam_days — string -> int
            raw_max_days = data.get("max_exam_days")
            if raw_max_days not in (None, ""):
                config.max_exam_days = int(raw_max_days)

            excluded = data.getlist("excluded_days[]") if hasattr(data, "getlist") else data.get("excluded_days", [])
            if excluded:
                config.excluded_days = ",".join(excluded)

            config.save()
            return JsonResponse({"status": "success"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)})
    return JsonResponse({"status": "error", "message": "Invalid request"}, status=405)