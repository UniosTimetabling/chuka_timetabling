"""
timetable/algorithms/campus_autosheduler_algorithm.py
=======================================================
Pure scheduling engine for the Campus (campuses_timetable) module.

Contains ONLY scheduling logic — no Django views, no HTTP, no rendering.
Imported by campuses_timetable/automatic_views.py for the actual HTTP layer.

Classes
-------
TimetableScheduler  — shared base (conflict checks, slot generation)
ClassScheduler      — class-timetable engine
ExamScheduler       — exam-timetable engine

Each scheduler exposes a single .run() method and populates:
  .scheduled  — list of successfully placed allocations
  .conflicts  — list of human-readable failure messages
"""
from __future__ import annotations

import logging
import random
from collections import defaultdict
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy model imports — resolved at call time so this module can be imported
# without a fully-initialised Django app registry (e.g. during tests).
# ---------------------------------------------------------------------------

def _campus_models():
    from campuses_timetable.models import (
        CampusCourseAllocation,
        CampusTimetable,
        CampusTempTimetable,
        CampusExamTimetable,
        CampusExamTempTimetable,
        CampusSchedulerConfig,
        CampusExamSchedulerConfig,
        CampusTimetableArchive,
    )
    return (
        CampusCourseAllocation,
        CampusTimetable,
        CampusTempTimetable,
        CampusExamTimetable,
        CampusExamTempTimetable,
        CampusSchedulerConfig,
        CampusExamSchedulerConfig,
        CampusTimetableArchive,
    )


# ---------------------------------------------------------------------------
# Base scheduler
# ---------------------------------------------------------------------------

class TimetableScheduler:
    """Shared helpers used by both class and exam schedulers."""

    def __init__(self, mode: str = "balanced"):
        self.mode = mode
        self.conflicts: list[str] = []
        self.scheduled: list = []

    # ------------------------------------------------------------------ #
    # Conflict checks                                                      #
    # ------------------------------------------------------------------ #

    def check_lecturer_conflict(self, lecturer, day_or_date, start_time, end_time, *, is_exam: bool = False) -> bool:
        """Return True if the lecturer already has a clashing slot."""
        if not lecturer:
            return False
        (
            _, _, CampusTempTimetable, _, CampusExamTempTimetable, _, _, _
        ) = _campus_models()
        try:
            if is_exam:
                return CampusExamTempTimetable.objects.filter(
                    course_allocation__lecturer=lecturer,
                    date=day_or_date,
                    start_time__lt=end_time,
                    end_time__gt=start_time,
                ).exists()
            else:
                return CampusTempTimetable.objects.filter(
                    course_allocation__lecturer=lecturer,
                    day=day_or_date,
                    start_time__lt=end_time,
                    end_time__gt=start_time,
                ).exists()
        except Exception as exc:
            logger.error("check_lecturer_conflict: %s", exc)
            return True  # fail-safe: treat as conflict

    def check_course_conflict(self, course_allocation, day_or_date, start_time, end_time, *, is_exam: bool = False) -> bool:
        """Return True if the course is already placed in an overlapping slot."""
        (
            _, _, CampusTempTimetable, _, CampusExamTempTimetable, _, _, _
        ) = _campus_models()
        try:
            if is_exam:
                return CampusExamTempTimetable.objects.filter(
                    course_allocation=course_allocation,
                    date=day_or_date,
                ).exists()
            else:
                return CampusTempTimetable.objects.filter(
                    course_allocation=course_allocation,
                    day=day_or_date,
                    start_time__lt=end_time,
                    end_time__gt=start_time,
                ).exists()
        except Exception as exc:
            logger.error("check_course_conflict: %s", exc)
            return True

    # ------------------------------------------------------------------ #
    # Slot generation                                                     #
    # ------------------------------------------------------------------ #

    def get_available_slots(self, date_obj: date, day_name: str, *, is_exam: bool = False) -> list[dict]:
        """
        Build a list of time-slot dicts for *date_obj*.

        Each dict: {start, end, display, available}
        """
        (
            _, _, _, _, _, CampusSchedulerConfig, CampusExamSchedulerConfig, _
        ) = _campus_models()
        try:
            if is_exam:
                config = CampusExamSchedulerConfig.objects.first()
            else:
                config = CampusSchedulerConfig.objects.first()

            if not config:
                logger.error("Scheduler config not found (is_exam=%s)", is_exam)
                return []

            start_time = config.start_time
            end_time = config.end_time
            slot_hours = config.slot_size

            slots: list[dict] = []
            current = datetime.combine(date_obj, start_time)
            end_dt = datetime.combine(date_obj, end_time)

            while current + timedelta(hours=slot_hours) <= end_dt:
                slot_end = current + timedelta(hours=slot_hours)
                slots.append({
                    "start": current.time(),
                    "end": slot_end.time(),
                    "display": f"{current.strftime('%H:%M')} - {slot_end.strftime('%H:%M')}",
                    "available": True,
                })
                current = slot_end

            logger.debug("Generated %d slots for %s", len(slots), date_obj)
            return slots
        except Exception as exc:
            logger.error("get_available_slots: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Class scheduler
# ---------------------------------------------------------------------------

class ClassScheduler(TimetableScheduler):
    """
    Schedules regular class timetable entries into CampusTempTimetable.

    Usage::

        scheduler = ClassScheduler(mode="balanced")
        ok = scheduler.run()
        print(scheduler.scheduled, scheduler.conflicts)
    """

    WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    def run(self) -> bool:
        (
            CampusCourseAllocation, _, CampusTempTimetable, _, _,
            CampusSchedulerConfig, _, _
        ) = _campus_models()
        try:
            logger.info("ClassScheduler.run() — mode=%s", self.mode)

            # Clear existing drafts
            deleted, _ = CampusTempTimetable.objects.all().delete()
            logger.info("Cleared %d existing class drafts", deleted)

            allocations = CampusCourseAllocation.objects.filter(
                approved_by_dvc=True,
                rejected_by_dvc=False,
            ).select_related("lecturer", "campus")

            if not allocations.exists():
                self.conflicts.append("No approved allocations found")
                return False

            config = CampusSchedulerConfig.objects.first()
            if not config:
                self.conflicts.append("Class scheduler configuration not found")
                return False

            # Order allocations by mode
            if self.mode == "compact":
                allocations = allocations.order_by("-number_of_students")
            elif self.mode == "spread":
                allocations = list(allocations)
                random.shuffle(allocations)
            else:
                allocations = allocations.order_by("course_code")

            # Group by campus
            campus_groups: dict = defaultdict(list)
            for alloc in allocations:
                campus_groups[alloc.campus_id if alloc.campus_id else "none"].append(alloc)

            for campus_key, campus_allocs in campus_groups.items():
                self._schedule_campus(campus_key, campus_allocs)

            logger.info("ClassScheduler done — placed=%d", len(self.scheduled))
            return bool(self.scheduled)

        except Exception as exc:
            logger.exception("ClassScheduler.run() fatal error")
            self.conflicts.append(f"Fatal error: {exc}")
            return False

    def _schedule_campus(self, campus_key, allocations):
        (
            _, _, CampusTempTimetable, _, _, _, _, _
        ) = _campus_models()

        # Build slot grid (one dummy date per weekday — classes use day names)
        dummy = date.today()
        slots_by_day = {
            day: self.get_available_slots(dummy, day, is_exam=False)
            for day in self.WEEKDAYS
        }

        for alloc in allocations:
            if alloc in self.scheduled:
                continue
            placed = False
            for day in self.WEEKDAYS:
                if placed:
                    break
                for slot in slots_by_day[day]:
                    if not slot["available"]:
                        continue
                    if self.check_lecturer_conflict(alloc.lecturer, day, slot["start"], slot["end"], is_exam=False):
                        continue
                    if self.check_course_conflict(alloc, day, slot["start"], slot["end"], is_exam=False):
                        continue
                    try:
                        CampusTempTimetable.objects.create(
                            course_allocation=alloc,
                            day=day,
                            start_time=slot["start"],
                            end_time=slot["end"],
                            campus=alloc.campus,
                        )
                        slot["available"] = False
                        self.scheduled.append(alloc)
                        placed = True
                        logger.debug("Placed %s on %s %s", alloc.course_code, day, slot["start"])
                        break
                    except Exception as exc:
                        logger.error("DB write failed for %s: %s", alloc.course_code, exc)

            if not placed:
                self.conflicts.append(f"Could not schedule {alloc.course_code} — no available slot")
                logger.warning("Failed to place %s (campus=%s)", alloc.course_code, campus_key)


# ---------------------------------------------------------------------------
# Exam scheduler
# ---------------------------------------------------------------------------

class ExamScheduler(TimetableScheduler):
    """
    Schedules exam timetable entries into CampusExamTempTimetable.

    Usage::

        scheduler = ExamScheduler(mode="balanced")
        ok = scheduler.run()
    """

    def run(self) -> bool:
        (
            CampusCourseAllocation, _, _, _, CampusExamTempTimetable,
            _, CampusExamSchedulerConfig, _
        ) = _campus_models()
        try:
            logger.info("ExamScheduler.run() — mode=%s", self.mode)

            deleted, _ = CampusExamTempTimetable.objects.all().delete()
            logger.info("Cleared %d existing exam drafts", deleted)

            allocations = CampusCourseAllocation.objects.filter(
                approved_by_dvc=True,
                rejected_by_dvc=False,
            ).select_related("lecturer", "campus")

            if not allocations.exists():
                self.conflicts.append("No approved allocations found")
                return False

            config = CampusExamSchedulerConfig.objects.first()
            if not config:
                self.conflicts.append("Exam scheduler configuration not found")
                return False

            # Build available dates
            dates: list[date] = []
            excluded = set(config.excluded_date_list()) if hasattr(config, "excluded_date_list") else set()
            for i in range(config.max_exam_days):
                d = config.start_date + timedelta(days=i)
                if str(d) not in excluded:
                    dates.append(d)

            if not dates:
                self.conflicts.append("No available dates in exam period")
                return False

            # Order allocations by mode
            if self.mode == "compact":
                allocations = allocations.order_by("-number_of_students")
            elif self.mode == "spread":
                allocations = list(allocations)
                random.shuffle(allocations)
            else:
                allocations = allocations.order_by("course_code")

            campus_groups: dict = defaultdict(list)
            for alloc in allocations:
                campus_groups[alloc.campus_id if alloc.campus_id else "none"].append(alloc)

            for campus_key, campus_allocs in campus_groups.items():
                self._schedule_campus(campus_key, campus_allocs, config, dates)

            logger.info("ExamScheduler done — placed=%d", len(self.scheduled))
            return bool(self.scheduled)

        except Exception as exc:
            logger.exception("ExamScheduler.run() fatal error")
            self.conflicts.append(f"Fatal error: {exc}")
            return False

    def _schedule_campus(self, campus_key, allocations, config, dates: list[date]):
        (
            _, _, _, _, CampusExamTempTimetable, _, _, _
        ) = _campus_models()

        # Build slot grid per date
        date_slots = {}
        for exam_date in dates:
            day_name = exam_date.strftime("%A")
            date_slots[exam_date] = {
                "day": day_name,
                "slots": self.get_available_slots(exam_date, day_name, is_exam=True),
            }

        for alloc in allocations:
            if alloc in self.scheduled:
                continue
            placed = False
            for exam_date, info in date_slots.items():
                if placed:
                    break
                for slot in info["slots"]:
                    if not slot["available"]:
                        continue
                    if self.check_lecturer_conflict(alloc.lecturer, exam_date, slot["start"], slot["end"], is_exam=True):
                        continue
                    if self.check_course_conflict(alloc, exam_date, slot["start"], slot["end"], is_exam=True):
                        continue
                    try:
                        CampusExamTempTimetable.objects.create(
                            course_allocation=alloc,
                            day=info["day"],
                            date=exam_date,
                            start_time=slot["start"],
                            end_time=slot["end"],
                            campus=alloc.campus,
                        )
                        slot["available"] = False
                        self.scheduled.append(alloc)
                        placed = True
                        logger.debug("Placed exam %s on %s %s", alloc.course_code, exam_date, slot["start"])
                        break
                    except Exception as exc:
                        logger.error("DB write failed for %s: %s", alloc.course_code, exc)

            if not placed:
                self.conflicts.append(
                    f"Could not schedule {alloc.course_code} — no available exam slot"
                )
                logger.warning("Failed to place exam %s (campus=%s)", alloc.course_code, campus_key)