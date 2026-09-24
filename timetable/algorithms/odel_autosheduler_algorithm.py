"""
timetable/algorithms/odel_autosheduler_algorithm.py
=====================================================
Pure scheduling engine for the ODEL (odel_system) module.

Contains ONLY scheduling logic — no Django views, no HTTP, no rendering.
Imported by odel_system/views_auto.py for the actual HTTP layer.

Public API
----------
generate_class_time_slots(config, date)  → list[dict]
generate_exam_time_slots(config, date)   → list[dict]
check_conflicts(allocation_id, venue_id, date, start_time, end_time,
                *, exam_mode=False)       → list[str]
run_class_scheduler(allocations, venues, dates, time_slots,
                    config, mode, user)   → dict
run_exam_scheduler(allocations, venues, exam_dates, time_slots,
                   config, mode)         → dict
"""
from __future__ import annotations

import datetime
import logging
import random

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy model imports
# ---------------------------------------------------------------------------

def _odel_models():
    from odel_system.models import (
        ODELCourseAllocation,
        ODELTimetableConfig,
        ODELTempTimetable,
        ODELTimetable,
        ODELExamTempTimetable,
        ODELExamTimetable,
    )
    return (
        ODELCourseAllocation,
        ODELTimetableConfig,
        ODELTempTimetable,
        ODELTimetable,
        ODELExamTempTimetable,
        ODELExamTimetable,
    )


# ---------------------------------------------------------------------------
# Slot generators
# ---------------------------------------------------------------------------

def generate_class_time_slots(config, ref_date: datetime.date) -> list[dict]:
    """
    Return a list of class time-slot dicts for *ref_date* based on *config*.

    Each dict: {start, end, display, slot_number}
    """
    if not config:
        return []

    start_dt = datetime.datetime.combine(ref_date, config.day_start_time)
    end_dt = datetime.datetime.combine(ref_date, config.day_end_time)

    total_minutes = int((end_dt - start_dt).total_seconds() / 60)
    slot_count = config.class_slot_size
    if slot_count <= 0:
        return []
    slot_duration = total_minutes // slot_count

    slots: list[dict] = []
    current = start_dt
    for i in range(slot_count):
        slot_end = current + datetime.timedelta(minutes=slot_duration)
        if slot_end > end_dt:
            break
        slots.append({
            "start": current.time(),
            "end": slot_end.time(),
            "display": f"{current.strftime('%H:%M')} - {slot_end.strftime('%H:%M')}",
            "slot_number": i + 1,
        })
        current = slot_end

    return slots


def generate_exam_time_slots(config, ref_date: datetime.date) -> list[dict]:
    """
    Return a list of exam time-slot dicts with break gaps applied.

    Each dict: {start, end, display, slot_number}
    """
    if not config:
        return []

    start_dt = datetime.datetime.combine(ref_date, config.day_start_time)
    end_dt = datetime.datetime.combine(ref_date, config.day_end_time)

    total_minutes = int((end_dt - start_dt).total_seconds() / 60)
    slot_count = config.exam_slot_size
    break_mins = getattr(config, "exam_break_duration", 0) or 0

    if slot_count <= 0:
        return []

    total_exam_minutes = total_minutes - break_mins * (slot_count - 1)
    exam_duration = total_exam_minutes // slot_count

    slots: list[dict] = []
    current = start_dt
    for i in range(slot_count):
        slot_end = current + datetime.timedelta(minutes=exam_duration)
        if slot_end > end_dt:
            break
        slots.append({
            "start": current.time(),
            "end": slot_end.time(),
            "display": f"{current.strftime('%H:%M')} - {slot_end.strftime('%H:%M')}",
            "slot_number": i + 1,
        })
        if i < slot_count - 1:
            current = slot_end + datetime.timedelta(minutes=break_mins)
        else:
            current = slot_end

    return slots


# ---------------------------------------------------------------------------
# Conflict checker
# ---------------------------------------------------------------------------

def check_conflicts(
    allocation_id: int,
    venue_id: int,
    date,
    start_time,
    end_time,
    *,
    exam_mode: bool = False,
    exclude_id: int | None = None,
) -> list[str]:
    """
    Return a list of conflict description strings (empty = no conflicts).

    *date*, *start_time*, *end_time* accept either strings or Python
    date/time objects.
    """
    (
        ODELCourseAllocation, _, ODELTempTimetable, ODELTimetable,
        ODELExamTempTimetable, ODELExamTimetable,
    ) = _odel_models()

    conflicts: list[str] = []

    try:
        alloc_obj = ODELCourseAllocation.objects.get(id=allocation_id)
    except ODELCourseAllocation.DoesNotExist:
        return ["Course allocation not found"]

    if not alloc_obj.submitted_to_tt:
        return ["This course allocation has not been submitted to timetabling"]

    base_filters = {
        "date": date,
        "start_time__lt": end_time,
        "end_time__gt": start_time,
    }

    # Venue conflict
    if exam_mode:
        venue_clash = (
            ODELExamTempTimetable.objects.filter(venue_id=venue_id, **base_filters).exists()
            or ODELExamTimetable.objects.filter(venue_id=venue_id, **base_filters).exists()
        )
    else:
        venue_clash = (
            ODELTempTimetable.objects.filter(venue_id=venue_id, **base_filters).exists()
            or ODELTimetable.objects.filter(venue_id=venue_id, **base_filters).exists()
        )
    if venue_clash:
        conflicts.append("Venue is already booked at this time")

    # Lecturer conflict
    if alloc_obj.lecturer:
        lec_filter = {"course_allocation__lecturer": alloc_obj.lecturer, **base_filters}
        if exam_mode:
            lec_clash = (
                ODELExamTempTimetable.objects.filter(**lec_filter).exists()
                or ODELExamTimetable.objects.filter(**lec_filter).exists()
            )
        else:
            lec_clash = (
                ODELTempTimetable.objects.filter(**lec_filter).exists()
                or ODELTimetable.objects.filter(**lec_filter).exists()
            )
        if lec_clash:
            conflicts.append(
                f"Lecturer is already assigned to another {'exam' if exam_mode else 'class'} at this time"
            )

    # Program conflict
    if alloc_obj.program_course and alloc_obj.program_course.program:
        program = alloc_obj.program_course.program
        program_alloc_ids = list(
            ODELCourseAllocation.objects.filter(
                program_course__program=program
            ).values_list("id", flat=True)
        )
        prog_filter = {"course_allocation_id__in": program_alloc_ids, **base_filters}
        if exam_mode:
            prog_clash = (
                ODELExamTempTimetable.objects.filter(**prog_filter).exists()
                or ODELExamTimetable.objects.filter(**prog_filter).exists()
            )
        else:
            prog_clash = (
                ODELTempTimetable.objects.filter(**prog_filter).exists()
                or ODELTimetable.objects.filter(**prog_filter).exists()
            )
        if prog_clash:
            conflicts.append(
                f"Program already has another {'exam' if exam_mode else 'class'} scheduled at this time"
            )

    return conflicts


# ---------------------------------------------------------------------------
# Class scheduler engine
# ---------------------------------------------------------------------------

def run_class_scheduler(
    allocations: list,
    venues: list,
    dates: list[datetime.date],
    time_slots: list[dict],
    config,
    mode: str,
    user,
) -> dict:
    """
    Schedule *allocations* into ODELTempTimetable draft rows.

    Returns a result dict: {scheduled, conflicts, conflict_list}
    """
    (
        _, _, ODELTempTimetable, _, _, _
    ) = _odel_models()

    # Sort allocations by mode
    if mode == "compact":
        allocations = sorted(allocations, key=lambda a: a.number_of_students, reverse=True)
    elif mode == "spread":
        allocations = list(allocations)
        random.shuffle(allocations)
        random.shuffle(dates)
    else:  # balanced
        allocations = sorted(allocations, key=lambda a: a.number_of_students, reverse=True)
        dates = list(dates)
        random.shuffle(dates)

    scheduled = 0
    conflicts: list[str] = []
    venue_usage: dict[str, bool] = {}

    for alloc in allocations:
        placed = False
        for date_obj in dates:
            if placed:
                break
            for slot in time_slots:
                if placed:
                    break
                for venue in venues:
                    if venue.capacity < alloc.number_of_students:
                        continue
                    usage_key = f"{venue.id}_{date_obj}_{slot['start']}"
                    if usage_key in venue_usage:
                        continue
                    clash = check_conflicts(
                        allocation_id=alloc.id,
                        venue_id=venue.id,
                        date=date_obj.strftime("%Y-%m-%d"),
                        start_time=slot["start"].strftime("%H:%M"),
                        end_time=slot["end"].strftime("%H:%M"),
                        exam_mode=False,
                    )
                    if clash:
                        continue
                    try:
                        ODELTempTimetable.objects.create(
                            course_allocation=alloc,
                            venue=venue,
                            date=date_obj,
                            start_time=slot["start"],
                            end_time=slot["end"],
                            created_by=user,
                        )
                        venue_usage[usage_key] = True
                        scheduled += 1
                        placed = True
                        logger.debug(
                            "ODEL class placed: %s @ %s on %s %s",
                            alloc.course_code, venue.code, date_obj, slot["start"],
                        )
                        break
                    except Exception as exc:
                        logger.error("DB write failed %s: %s", alloc.course_code, exc)

        if not placed:
            conflicts.append(alloc.course_code)
            logger.warning("ODEL class not placed: %s", alloc.course_code)

    return {
        "scheduled": scheduled,
        "conflicts": len(conflicts),
        "conflict_list": conflicts[:10],
    }


# ---------------------------------------------------------------------------
# Exam scheduler engine
# ---------------------------------------------------------------------------

def run_exam_scheduler(
    allocations: list,
    venues: list,
    exam_dates: list[datetime.date],
    time_slots: list[dict],
    config,
    mode: str,
) -> dict:
    """
    Schedule *allocations* into ODELExamTempTimetable draft rows.

    Returns a result dict: {scheduled, conflicts, conflict_list}
    """
    (
        _, _, _, _, ODELExamTempTimetable, _
    ) = _odel_models()

    if mode == "compact":
        allocations = sorted(allocations, key=lambda a: a.number_of_students, reverse=True)
    elif mode == "spread":
        allocations = list(allocations)
        random.shuffle(allocations)
    else:  # balanced
        allocations = list(allocations)
        random.shuffle(allocations)

    scheduled = 0
    conflicts: list[str] = []
    venue_usage: dict[str, bool] = {}

    for alloc in allocations:
        placed = False
        for date_obj in exam_dates:
            if placed:
                break
            for slot in time_slots:
                if placed:
                    break
                for venue in venues:
                    if venue.capacity < alloc.number_of_students:
                        continue
                    usage_key = f"{venue.id}_{date_obj}_{slot['start']}"
                    if usage_key in venue_usage:
                        continue
                    clash = check_conflicts(
                        allocation_id=alloc.id,
                        venue_id=venue.id,
                        date=date_obj.strftime("%Y-%m-%d"),
                        start_time=slot["start"].strftime("%H:%M"),
                        end_time=slot["end"].strftime("%H:%M"),
                        exam_mode=True,
                    )
                    if clash:
                        continue
                    try:
                        ODELExamTempTimetable.objects.create(
                            course_allocation=alloc,
                            venue=venue,
                            date=date_obj,
                            start_time=slot["start"],
                            end_time=slot["end"],
                        )
                        venue_usage[usage_key] = True
                        scheduled += 1
                        placed = True
                        logger.debug(
                            "ODEL exam placed: %s @ %s on %s %s",
                            alloc.course_code, venue.code, date_obj, slot["start"],
                        )
                        break
                    except Exception as exc:
                        logger.error("DB write failed %s: %s", alloc.course_code, exc)

        if not placed:
            conflicts.append(alloc.course_code)
            logger.warning("ODEL exam not placed: %s", alloc.course_code)

    return {
        "scheduled": scheduled,
        "conflicts": len(conflicts),
        "conflict_list": conflicts[:10],
    }