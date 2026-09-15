import datetime
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.utils.dateparse import parse_time
from django.utils import timezone

from timetable.models import LabExamTimetable, ExamSchedulerConfig
from course_allocation.models import LabAllocation
from room_management.models import LabVenue

# NOTE: The get_item template filter is provided by the project's existing
# dict_filters templatetag library ({% load dict_filters %} in the template).


# ─── Config helpers ──────────────────────────────────────────────────────────

def _get_scheduler_config():
    """
    Return the single ExamSchedulerConfig row (pk=1), creating it with
    sensible defaults if it does not yet exist.
    """
    cfg, _ = ExamSchedulerConfig.objects.get_or_create(
        pk=1,
        defaults={
            "start_date":    timezone.now().date(),
            "start_time":    datetime.time(8, 0),
            "end_time":      datetime.time(17, 0),
            "slot_size":     2,
            "max_exam_days": 14,
        },
    )
    return cfg


def _generate_time_slots(cfg):
    """
    Return list of (value, label) tuples, e.g. ("08:00", "08:00 – 10:00").
    `value` is what the form posts; `label` is what the template renders.
    """
    slots   = []
    today   = datetime.date.today()
    cur     = datetime.datetime.combine(today, cfg.start_time)
    end_dt  = datetime.datetime.combine(today, cfg.end_time)
    step    = datetime.timedelta(hours=cfg.slot_size)

    while cur + step <= end_dt:
        nxt   = cur + step
        label = f"{cur.strftime('%H:%M')} – {nxt.strftime('%H:%M')}"
        slots.append((cur.strftime("%H:%M"), label))
        cur   = nxt
    return slots


def _get_valid_date_range(cfg):
    """
    Delegate to ExamSchedulerConfig.get_excluded_date_range() which:
      • Walks forward from start_date
      • Collects exactly max_exam_days *working* dates
      • Skips all dates in cfg.excluded_days (weekends auto-populated on save,
        plus any manually added public holidays)
    Returns list of (YYYY-MM-DD, weekday_name) string tuples.
    """
    return cfg.get_excluded_date_range()


def _get_full_calendar_window(cfg):
    """
    Return every calendar date (including weekends and excluded days) from
    cfg.start_date up to and including the last date in the working date range.
    Each entry is a dict: {iso, day_name, is_weekend, is_excluded}.
    """
    excluded_set = set(
        d.strip() for d in (cfg.excluded_days or "").split(",") if d.strip()
    )
    working_dates = cfg.get_excluded_date_range()  # [(YYYY-MM-DD, day_name), ...]
    if not working_dates:
        return []

    last_date = datetime.datetime.strptime(working_dates[-1][0], "%Y-%m-%d").date()
    result = []
    cur = cfg.start_date
    while cur <= last_date:
        iso      = cur.strftime("%Y-%m-%d")
        day_name = cur.strftime("%A")
        result.append({
            "iso":         iso,
            "day_name":    day_name,
            "is_weekend":  day_name in ("Saturday", "Sunday"),
            "is_excluded": iso in excluded_set,
        })
        cur += datetime.timedelta(days=1)
    return result


# ─── Main panel view ─────────────────────────────────────────────────────────

@login_required
def lab_exam_timetable_panel(request):
    cfg        = _get_scheduler_config()
    time_slots = _generate_time_slots(cfg)
    date_range = _get_valid_date_range(cfg)
    full_calendar_window = _get_full_calendar_window(cfg)

    # Group timetable entries by date string for O(1) template look-ups.
    qs = LabExamTimetable.objects.select_related(
        "lab_allocation__program_course",
        "lab_allocation__lecturer",
        "lab_venue",
    ).prefetch_related("lab_allocation__venues")

    timetables_by_date = {}
    for t in qs:
        key = t.date.strftime("%Y-%m-%d")
        timetables_by_date.setdefault(key, []).append(t)

    all_allocations = LabAllocation.objects.select_related(
        "program_course", "lecturer"
    ).prefetch_related("venues")
    venues = LabVenue.objects.all().order_by("code")

    return render(request, "dashboard/lab_exam_timetable.html", {
        "heading":               "Lab Exam Timetable",
        "cfg":                   cfg,
        "time_slots":            time_slots,
        "date_range":            date_range,
        "timetables_by_date":    timetables_by_date,
        "all_allocations":       all_allocations,
        "venues":                venues,
        "full_calendar_window":  full_calendar_window,
        "excluded_days_count":   len([d for d in cfg.excluded_days.split(",") if d.strip()]) if cfg.excluded_days else 0,
    })


# ─── AJAX API ────────────────────────────────────────────────────────────────

@login_required
@require_http_methods(["POST"])
def lab_exam_timetable_api(request):
    action = request.POST.get("action")

    # ── update_config ────────────────────────────────────────────────────────
    if action == "update_config":
        cfg = _get_scheduler_config()

        start_date_str = request.POST.get("start_date", "").strip()
        if start_date_str:
            try:
                cfg.start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
            except ValueError:
                return JsonResponse(
                    {"status": "error", "message": "Invalid start date — use YYYY-MM-DD."}, status=400
                )

        # ExamSchedulerConfig has NO end_date field; range is controlled by
        # max_exam_days.  We silently ignore any posted end_date.

        start_time_str = request.POST.get("start_time", "").strip()
        if start_time_str:
            parsed = parse_time(start_time_str)
            if not parsed:
                return JsonResponse(
                    {"status": "error", "message": "Invalid start time."}, status=400
                )
            cfg.start_time = parsed

        end_time_str = request.POST.get("end_time", "").strip()
        if end_time_str:
            parsed = parse_time(end_time_str)
            if not parsed:
                return JsonResponse(
                    {"status": "error", "message": "Invalid end time."}, status=400
                )
            cfg.end_time = parsed

        slot_size_str = request.POST.get("slot_size", "").strip()
        if slot_size_str:
            try:
                val = int(slot_size_str)
                if not (1 <= val <= 12):
                    raise ValueError
                cfg.slot_size = val
            except ValueError:
                return JsonResponse(
                    {"status": "error", "message": "Slot size must be 1–12 hours."}, status=400
                )

        max_days_str = request.POST.get("max_exam_days", "").strip()
        if max_days_str:
            try:
                val = int(max_days_str)
                if not (1 <= val <= 60):
                    raise ValueError
                cfg.max_exam_days = val
            except ValueError:
                return JsonResponse(
                    {"status": "error", "message": "Max exam days must be 1–60."}, status=400
                )

        # cfg.save() auto-populates excluded_days with all weekend dates in the
        # lookahead window (ExamSchedulerConfig.save() handles this).
        cfg.save()
        return JsonResponse({"status": "success", "message": "Configuration saved."})

    # ── update_excluded_days ──────────────────────────────────────────────────
    if action == "update_excluded_days":
        cfg = _get_scheduler_config()
        raw = request.POST.get("excluded_days", "").strip()
        # Normalise: split on commas, strip whitespace, validate each token
        # as YYYY-MM-DD, then re-join sorted.
        tokens = [t.strip() for t in raw.split(",") if t.strip()]
        validated = []
        for tok in tokens:
            try:
                datetime.datetime.strptime(tok, "%Y-%m-%d")
                validated.append(tok)
            except ValueError:
                return JsonResponse(
                    {"status": "error",
                     "message": f"Invalid date format '{tok}' — use YYYY-MM-DD."},
                    status=400,
                )
        cfg.excluded_days = ", ".join(sorted(validated))
        cfg.save()
        return JsonResponse({"status": "success", "message": "Excluded days updated."})

    # ── create_entry ─────────────────────────────────────────────────────────
    if action == "create_entry":
        try:
            alloc      = get_object_or_404(LabAllocation, pk=request.POST.get("allocation_id"))
            venue      = get_object_or_404(LabVenue, code=request.POST.get("venue"))
            date_str   = request.POST.get("date", "")
            date       = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            start_time = parse_time(request.POST.get("start_time", ""))
            if not start_time:
                return JsonResponse({"status": "error", "message": "Invalid start time."}, status=400)

            cfg      = _get_scheduler_config()
            end_time = (
                datetime.datetime.combine(date, start_time)
                + datetime.timedelta(hours=cfg.slot_size)
            ).time()

            # Conflict check — same venue overlapping slot
            if LabExamTimetable.objects.filter(
                lab_venue=venue, date=date,
                start_time__lt=end_time, end_time__gt=start_time,
            ).exists():
                return JsonResponse(
                    {"status": "error",
                     "message": f"Venue {venue.code} is already booked during this slot."},
                    status=400,
                )

            entry, created = LabExamTimetable.objects.get_or_create(
                lab_allocation=alloc,
                lab_venue=venue,
                date=date,
                day=date.strftime("%A"),
                start_time=start_time,
                end_time=end_time,
            )
            if created:
                return JsonResponse({"status": "success", "id": entry.id, "message": "Entry created."})
            return JsonResponse({"status": "error", "message": "Entry already exists."}, status=400)

        except Exception as exc:
            return JsonResponse({"status": "error", "message": str(exc)}, status=400)

    # ── delete_entry ─────────────────────────────────────────────────────────
    if action == "delete_entry":
        try:
            entry = get_object_or_404(LabExamTimetable, pk=request.POST.get("entry_id"))
            entry.delete()
            return JsonResponse({"status": "success", "message": "Entry deleted."})
        except Exception as exc:
            return JsonResponse({"status": "error", "message": str(exc)}, status=400)

    return JsonResponse({"status": "error", "message": "Invalid action."}, status=400)