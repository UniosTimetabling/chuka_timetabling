import math
import datetime
import random

from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.views.decorators.http import require_POST
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone

from timetable.models import LabExamTimetable, ExamTimetable, ExamSchedulerConfig
from course_allocation.models import LabAllocation


# ── helpers ───────────────────────────────────────────────────────────────────

def _to_time(value):
    if isinstance(value, datetime.time):
        return value
    if isinstance(value, str):
        parts = value.strip().split(":")
        try:
            return datetime.time(
                int(parts[0]),
                int(parts[1]) if len(parts) > 1 else 0,
                int(parts[2]) if len(parts) > 2 else 0,
            )
        except (ValueError, IndexError):
            pass
    return datetime.time(8, 0)


def _get_scheduler_config():
    cfg, _ = ExamSchedulerConfig.objects.get_or_create(
        pk=1,
        defaults={
            "start_date": timezone.now().date(),
            "start_time": datetime.time(8, 0),
            "end_time": datetime.time(17, 0),
            "slot_size": 2,
            "max_exam_days": 14,
        },
    )
    return cfg


def _generate_time_slots(cfg):
    """Return list of (start_time, end_time) datetime.time pairs."""
    start_t = _to_time(cfg.start_time)
    end_t = _to_time(cfg.end_time)
    try:
        slot_hours = int(cfg.slot_size)
    except (TypeError, ValueError):
        slot_hours = 2

    step = datetime.timedelta(hours=slot_hours)
    today = datetime.date.today()
    cur = datetime.datetime.combine(today, start_t)
    end_dt = datetime.datetime.combine(today, end_t)

    slots = []
    while cur + step <= end_dt:
        slots.append((cur.time(), (cur + step).time()))
        cur += step
    return slots


def _get_valid_date_range(cfg):
    """
    Return list of (datetime.date, weekday_str) tuples.

    cfg.get_excluded_date_range() returns (YYYY-MM-DD str, day str) pairs.
    We convert the string back to a date object so the scheduler can pass
    it directly to ORM queries and model creates.
    """
    raw = cfg.get_excluded_date_range()          # [(str, str), ...]
    result = []
    for date_str, day in raw:
        try:
            date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        result.append((date_obj, day))
    return result


# ── conflict-check helpers ────────────────────────────────────────────────────

def _venue_free(venue, date, st, et):
    return not LabExamTimetable.objects.filter(
        lab_venue=venue,
        date=date,
        start_time__lt=et,
        end_time__gt=st,
    ).exists()


def _lecturer_free(lecturer, date, st, et):
    if not lecturer:
        return True
    return not (
        LabExamTimetable.objects.filter(
            lab_allocation__lecturer=lecturer,
            date=date,
            start_time__lt=et,
            end_time__gt=st,
        ).exists()
        or ExamTimetable.objects.filter(
            course_allocation__lecturer=lecturer,
            date=date,
            start_time__lt=et,
            end_time__gt=st,
        ).exists()
    )


def _program_free(program, date, st, et):
    """
    Students in a program cannot sit two exams simultaneously — block both
    lab exams and regular exams for the same program at this slot.
    """
    if program is None:
        return True
    return not (
        LabExamTimetable.objects.filter(
            lab_allocation__program_course__program=program,
            date=date,
            start_time__lt=et,
            end_time__gt=st,
        ).exists()
        or ExamTimetable.objects.filter(
            course_allocation__program=program,
            date=date,
            start_time__lt=et,
            end_time__gt=st,
        ).exists()
    )


def _pick_venues(all_venues, students, date, st, et):
    """
    Return a list of free venue(s) that can accommodate `students`.
    Returns [] only if every candidate venue is already occupied.

    Strategy (in order):
      1. Single venue that fits exactly.
      2. Single venue with ≤10 overflow.
      3. Split across multiple free venues.
    """
    free = [v for v in all_venues if _venue_free(v, date, st, et)]
    if not free:
        return []

    free_desc = sorted(free, key=lambda v: v.capacity or 0, reverse=True)
    largest = free_desc[0].capacity or 0

    # 1. Perfect / sufficient fit.
    for v in free_desc:
        if (v.capacity or 0) >= students:
            return [v]

    # 2. Tolerable overflow (≤10 students over capacity).
    for v in free_desc:
        if students - (v.capacity or 0) <= 10:
            return [v]

    # 3. Split across multiple venues.
    if largest == 0:
        return [free_desc[0]]

    num_groups = math.ceil(students / largest)
    group_size = math.ceil(students / num_groups)

    chosen = [v for v in free_desc if (v.capacity or 0) >= group_size]
    if len(chosen) >= num_groups:
        return chosen[:num_groups]

    return free_desc[: min(num_groups, len(free_desc))]


# ── main view ─────────────────────────────────────────────────────────────────

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
@transaction.atomic
def run_lab_exam_autoscheduler(request):
    """
    Wipe and regenerate the entire LabExamTimetable from scratch.

    A slot is rejected for a given allocation only when:
      1. The allocation's PROGRAM already has a lab or regular exam at that slot.
      2. The LECTURER is already supervising a lab or regular exam at that slot.
      3. ALL candidate VENUES for the allocation are occupied at that slot.

    Each check is scoped to the specific allocation — other allocations
    sharing the same date/slot in different venues are unaffected.
    """
    LabExamTimetable.objects.all().delete()

    cfg = _get_scheduler_config()

    time_slots = _generate_time_slots(cfg)
    valid_dates = _get_valid_date_range(cfg)       # [(date_obj, day_str), ...]

    if not time_slots:
        return JsonResponse(
            {"status": "error",
             "message": "No time slots generated — check start/end time and slot size."},
            status=400,
        )
    if not valid_dates:
        return JsonResponse(
            {"status": "error",
             "message": "No valid exam dates — check start_date and max_exam_days."},
            status=400,
        )

    allocations = list(
        LabAllocation.objects
        .select_related("program_course", "program_course__program", "lecturer")
        .prefetch_related("venues")
    )

    if not allocations:
        return JsonResponse(
            {"status": "error", "message": "No lab allocations found."},
            status=400,
        )

    random.shuffle(allocations)

    created_count = 0
    scheduled_ids = set()
    skipped_no_venue = []
    skipped_no_slot = []

    for alloc in allocations:
        if alloc.id in scheduled_ids:
            continue

        students = alloc.number_of_students or 0
        lecturer = alloc.lecturer
        program = alloc.program_course.program if alloc.program_course else None
        all_venues = sorted(list(alloc.venues.all()), key=lambda v: v.capacity or 0)

        if not all_venues:
            skipped_no_venue.append(alloc.all_course_codes())
            continue

        placed = False

        for date, day in valid_dates:            # date is a datetime.date object
            if placed:
                break
            for st, et in time_slots:
                if placed:
                    break

                if not _program_free(program, date, st, et):
                    continue
                if not _lecturer_free(lecturer, date, st, et):
                    continue

                chosen_venues = _pick_venues(all_venues, students, date, st, et)
                if not chosen_venues:
                    continue

                for venue in chosen_venues:
                    LabExamTimetable.objects.create(
                        lab_allocation=alloc,
                        lab_venue=venue,
                        date=date,
                        day=day,
                        start_time=st,
                        end_time=et,
                    )
                    created_count += 1

                scheduled_ids.add(alloc.id)
                placed = True

        if not placed:
            skipped_no_slot.append(alloc.all_course_codes())

    # ── Response ──────────────────────────────────────────────────────────────
    if created_count == 0:
        parts = ["No exam sessions scheduled."]
        if skipped_no_venue:
            parts.append(f"No venues configured: {', '.join(skipped_no_venue)}.")
        if skipped_no_slot:
            parts.append(f"No free slot found: {', '.join(skipped_no_slot)}.")
        return JsonResponse({"status": "warning", "message": " ".join(parts)})

    msg = f"{created_count} lab/workshop exam session(s) scheduled successfully."
    if skipped_no_venue:
        msg += f" No venues configured: {', '.join(skipped_no_venue)}."
    if skipped_no_slot:
        msg += f" No free slot found: {', '.join(skipped_no_slot)}."
    return JsonResponse({"status": "success", "message": msg})