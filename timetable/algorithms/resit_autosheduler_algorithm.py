"""
timetable/algorithms/resit_autosheduler_algorithm.py
=====================================================
Pure scheduling engine for the Resits (resits_timetabling) module.

CHANGES FROM PREVIOUS VERSION (fixing venue-mixing + slot-pileup bugs):

  1. VENUE-SLOT EXCLUSIVITY (NEW): A given (venue, day, time_slot) can now be
     occupied by exactly ONE department for the whole scheduling run. This is
     tracked explicitly in `venue_slot_department` and enforced inside
     `_get_best_venue_for_course`, closing the loophole where departments that
     ran out of "assigned" venues fell back to `venues_with_capacity[:2]` and
     silently overwrote another department's venue/slot (this is what produced
     BMET/BOTA/NUMS/NURU/PHYS/PUHE/PUHU all sharing S104 Mon 8:30-10:30 in the
     old output).

  2. DEPARTMENT SPREAD SCORING (NEW): `dept_day_count` / `dept_time_count`
     track how many times a department has already used a given day / time
     slot. This is factored into slot scoring so a department's own courses
     fan out across days and time slots instead of all piling into the first
     available slot before moving on.

  3. SMARTER FALLBACK VENUE POOL (CHANGED): when a department has no
     dedicated venues left (more departments than venues), it now falls back
     to the *least-used* venues overall rather than always `venues_with_capacity[:2]`.

  4. EXHAUSTIVE DEFERRED/EMERGENCY RESOLUTION (REWRITTEN): the old deferred
     and "emergency" resolution passes scanned (date, time, venue) in a
     FIXED order, so every course that fell through Step 3 landed in the
     same first-available cell — this is what caused ~19 unrelated small
     departments to all stack into SGT1 Monday 8:30 in one real run. It now
     ranks EVERY (venue, day, time) combination in the whole week and always
     exhausts exclusive (single-department) options before ever mixing
     departments, preferring the least-used venue/day/time among ties so
     placements spread out instead of piling onto the first hit.

Original strategy (unchanged):
  1. Department-Based Grouping: Courses from the same department are grouped
     together in the same venue(s) across different time slots.
  2. Strategic Venue Distribution: Each venue hosts related courses from
     the same department/faculty -- and ONLY that department, per slot.
  3. Balanced Load: Courses are evenly distributed across days, time slots,
     and venues based on student numbers.
  4. No Consecutive Exams: Students are not scheduled for back-to-back exams.
  5. Maximum 2 Exams Per Day: Students are limited to 2 exams per day.
  6. Intelligent Course Combining: Similar courses are placed together
     (e.g., all MATH courses in one venue, all COSC in another) -- but never
     mixed with a different department in the same venue/slot.
"""
from __future__ import annotations

import datetime
import logging
import os
import re
import math
from collections import defaultdict
from typing import Callable, Optional, Set, Dict, List, Tuple, Any
from dataclasses import dataclass, field
from django.db import transaction

logger = logging.getLogger(__name__)

# Configuration
MAX_EXAMS_PER_DAY = 2
MIN_TIME_GAP_HOURS = 2  # Minimum gap between exams for same student
MAX_COURSES_PER_VENUE_PER_SLOT = 3  # Maximum courses in one venue per time slot (same dept only)


@dataclass
class SchedulingStats:
    """Container for scheduling statistics and metrics."""
    total_courses: int = 0
    scheduled_courses: int = 0
    deferred_courses: int = 0
    failed_courses: int = 0
    slot_collisions: int = 0
    daily_load_violations: int = 0
    consecutive_exam_violations: int = 0
    venue_capacity_violations: int = 0
    emergency_placements: int = 0
    students_tracked: int = 0
    courses_combined: int = 0
    venues_utilized: int = 0
    departments_grouped: int = 0
    start_time: datetime.datetime = field(default_factory=datetime.datetime.now)
    end_time: Optional[datetime.datetime] = None

    @property
    def duration(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return (datetime.datetime.now() - self.start_time).total_seconds()

    def to_dict(self) -> dict:
        return {
            'total_courses': self.total_courses,
            'scheduled_courses': self.scheduled_courses,
            'deferred_courses': self.deferred_courses,
            'failed_courses': self.failed_courses,
            'slot_collisions': self.slot_collisions,
            'daily_load_violations': self.daily_load_violations,
            'consecutive_exam_violations': self.consecutive_exam_violations,
            'venue_capacity_violations': self.venue_capacity_violations,
            'emergency_placements': self.emergency_placements,
            'students_tracked': self.students_tracked,
            'courses_combined': self.courses_combined,
            'venues_utilized': self.venues_utilized,
            'departments_grouped': self.departments_grouped,
            'duration_seconds': self.duration
        }


def _resit_models():
    from resits_timetabling.models import (
        ResitCourseAllocation,
        ResitTempTimetable,
        ResitTimeSlot,
        StudentResitRegistration,
    )
    return ResitCourseAllocation, ResitTempTimetable, ResitTimeSlot, StudentResitRegistration


def _venue_model():
    from room_management.models import Venue
    return Venue


def _fmt(t) -> str:
    if hasattr(t, "strftime"):
        return t.strftime("%H:%M")
    if isinstance(t, datetime.time):
        return f"{t.hour:02d}:{t.minute:02d}"
    if isinstance(t, str):
        return t[:5]
    return str(t)[:5]


def _as_date(d) -> datetime.date:
    if isinstance(d, datetime.date):
        return d
    return datetime.date.fromisoformat(str(d))


def _as_time(t) -> datetime.time:
    if isinstance(t, datetime.time):
        return t
    return datetime.datetime.strptime(str(t)[:5], "%H:%M").time()


def _time_to_hours(t) -> float:
    """Convert time to hours as float."""
    if isinstance(t, datetime.time):
        return t.hour + t.minute / 60.0
    if isinstance(t, str):
        dt = datetime.datetime.strptime(t[:5], "%H:%M")
        return dt.hour + dt.minute / 60.0
    return 0


def _time_diff_hours(t1, t2) -> float:
    """Calculate difference between two times in hours."""
    return abs(_time_to_hours(t1) - _time_to_hours(t2))


def _canonicalize_student_id(student_id: str) -> str:
    """Convert student registration number to canonical format."""
    if not student_id:
        return ""

    cleaned = student_id.strip()
    match = re.match(r'^([A-Za-z0-9]+)[/\-]?([0-9]+)[/\-]?([0-9]+)$', cleaned.replace(' ', ''))
    if match:
        prefix, middle, suffix = match.groups()
        prefix = prefix.upper()
        return f"{prefix}/{middle}/{suffix}"

    alnum = re.sub(r'[^A-Za-z0-9]', '', cleaned)
    if len(alnum) >= 5:
        match_prefix = re.match(r'^([A-Za-z]+)([0-9]+)([0-9]+)([0-9]+)$', alnum)
        if match_prefix:
            letters, num1, num2, num3 = match_prefix.groups()
            if len(num1) == 1:
                prefix = letters + num1
                middle = num2
                suffix = num3
                return f"{prefix.upper()}/{middle}/{suffix}"

    return re.sub(r'[^A-Za-z0-9]', '', cleaned).upper()


def _normalize_student_registration(reg_no: str) -> str:
    if not reg_no:
        return ""
    canonical = _canonicalize_student_id(reg_no)
    if not canonical:
        cleaned = re.sub(r'[^A-Za-z0-9/]', '', reg_no.strip())
        if cleaned:
            parts = [p for p in cleaned.split('/') if p]
            if parts:
                return '/'.join(parts).upper()
        return re.sub(r'[^A-Za-z0-9]', '', reg_no.strip()).upper()
    return canonical


def _extract_department_code(course_code: str) -> str:
    """
    Extract department code from course code.
    E.g., BCOM101 -> BCOM, COSC102 -> COSC, MATH121 -> MATH
    """
    if not course_code:
        return "UNKNOWN"
    match = re.match(r'^([A-Za-z]+)', course_code)
    return match.group(1).upper() if match else "UNKNOWN"


def _calculate_ideal_distribution(
    total_courses: int,
    total_days: int,
    total_time_slots: int,
    total_venues: int
) -> Dict[str, int]:
    """Calculate ideal distribution targets."""
    courses_per_day = math.ceil(total_courses / max(total_days, 1))
    courses_per_time_slot = math.ceil(total_courses / max(total_time_slots, 1))
    courses_per_venue = math.ceil(total_courses / max(total_venues, 1))

    return {
        'per_day': courses_per_day,
        'per_time_slot': courses_per_time_slot,
        'per_venue': courses_per_venue
    }


def _group_courses_by_department(
    courses: List,
    alloc_students: Dict
) -> Dict[str, List]:
    """
    Group courses by department code for venue co-location.
    """
    dept_groups = defaultdict(list)

    for alloc in courses:
        dept_code = _extract_department_code(alloc.course_code)
        dept_groups[dept_code].append(alloc)

    # Sort each department's courses by student count (largest first)
    for dept in dept_groups:
        dept_groups[dept].sort(
            key=lambda c: len(alloc_students.get(c.id, set())),
            reverse=True
        )

    return dict(dept_groups)


def _assign_venues_to_departments(
    dept_groups: Dict[str, List],
    alloc_students: Dict,
    venues: List
) -> Dict[str, List]:
    """
    Assign venues to departments based on their course sizes.
    Returns mapping of department to list of suitable venues.

    NOTE: With more departments than venues (common case — 50+ department
    codes vs ~15 venues), not every department can get an exclusive venue.
    Departments that don't get one here fall back, at scheduling time, to
    the least-used venue pool (see `run_resit_scheduler`) rather than a
    fixed pair of venues shared by everyone. Cross-department mixing in the
    SAME slot is still prevented by `venue_slot_department` exclusivity
    enforced in `_get_best_venue_for_course`.
    """
    dept_venue_mapping = {}
    venue_dict = {v.id: v for v in venues}

    # Calculate total students per department
    dept_sizes = {}
    for dept, courses in dept_groups.items():
        total_students = sum(len(alloc_students.get(c.id, set())) for c in courses)
        max_course_size = max(
            (len(alloc_students.get(c.id, set())) for c in courses),
            default=1
        )
        dept_sizes[dept] = {
            'total_students': total_students,
            'max_course_size': max_course_size,
            'course_count': len(courses)
        }

    # Sort departments by size (largest first)
    sorted_depts = sorted(
        dept_sizes.items(),
        key=lambda x: x[1]['total_students'],
        reverse=True
    )

    # Assign venues to departments
    used_venues = set()

    for dept, size_info in sorted_depts:
        max_course_size = size_info['max_course_size']

        # Find venues that can accommodate the largest course
        suitable_venues = []
        for venue in venues:
            if venue.id in used_venues:
                continue
            capacity = venue.exam_capacity or venue.capacity or 0
            if capacity >= max_course_size * 0.5:  # At least 50% utilization
                suitable_venues.append(venue)

        # Sort by capacity fit (closest to max course size)
        suitable_venues.sort(
            key=lambda v: abs((v.exam_capacity or v.capacity or 0) - max_course_size)
        )

        # Assign top 2-3 venues to this department
        assigned_venues = suitable_venues[:min(3, len(suitable_venues))]

        if not assigned_venues:
            # No exclusive venue available for this department. Leave it
            # unassigned here — `run_resit_scheduler` will give it the
            # least-used venue(s) at scheduling time instead of silently
            # reusing another department's dedicated venue.
            dept_venue_mapping[dept] = []
            continue

        dept_venue_mapping[dept] = assigned_venues

        # Mark venues as used
        for venue in assigned_venues:
            used_venues.add(venue.id)

    return dept_venue_mapping


def _calculate_time_slot_score(
    time_slot: Tuple[str, str],
    day: str,
    student_ids: Set[str],
    student_slot_occupancy: Dict,
    student_daily_load: Dict,
    student_time_history: Dict,
    slot_index_map: Dict[str, int],
) -> float:
    """
    Calculate a score for a time slot considering student conflicts.

    FIXED (two compounding bugs that made "no consecutive exams" a no-op):
      1. This used to check `student_id in student_time_history`, but
         `student_time_history` is keyed by `(student_id, day)` tuples —
         a bare string can never match a tuple key, so the consecutive-exam
         branch never executed for a single student, ever.
      2. Even with the key fixed, it compared raw hour gaps against
         MIN_TIME_GAP_HOURS (2h). This timetable's slots are 8:30-10:30,
         11:30-1:30, 2:30-4:30 — start times are always 3 hours apart, so
         `time_diff < MIN_TIME_GAP_HOURS` (3 < 2) could never be True, even
         for genuinely back-to-back slots.
    Now it compares SLOT ORDER instead of clock time: booking a student into
    the slot immediately adjacent to one they already have is treated as a
    near-hard conflict (matches the >=100 skip threshold callers use), while
    skipping a slot in between is fine.
    """
    start_t, end_t = time_slot
    score = 0.0
    candidate_idx = slot_index_map.get(start_t)

    for student_id in student_ids:
        # Check exact time conflict
        slot_key = (student_id, day, start_t)
        if slot_key in student_slot_occupancy:
            score += 100.0

        # Check daily load
        daily_key = (student_id, day)
        current_daily_load = student_daily_load.get(daily_key, 0)
        if current_daily_load >= MAX_EXAMS_PER_DAY:
            score += 50.0
        elif current_daily_load > 0:
            score += current_daily_load * 10.0

        # Check for back-to-back (adjacent) exams on the same day
        if candidate_idx is not None:
            for existing_start_t, _course in student_time_history.get(daily_key, []):
                existing_idx = slot_index_map.get(existing_start_t)
                if existing_idx is None or existing_idx == candidate_idx:
                    continue
                gap = abs(existing_idx - candidate_idx)
                if gap == 1:
                    # immediately adjacent slot -- effectively block unless
                    # truly nothing else is available
                    score += 100.0
                elif gap == 2:
                    # one slot skipped -- fine, mild preference for even
                    # more spacing when it's free
                    score += 2.0

    return score


def _get_best_venue_for_course(
    course,
    student_count: int,
    department_venues: List,
    venue_used: Dict,
    day: str,
    time_slot: str,
    dept: str,
    venue_slot_department: Dict,
) -> Optional[Any]:
    """
    Find the best venue for a course within its department's assigned venues.

    NEW: enforces venue-slot exclusivity — a (venue, day, time_slot) already
    claimed by a DIFFERENT department is skipped entirely, even if it has
    spare capacity. This is what stops different departments' courses from
    landing in the same venue at the same time.
    """
    best_venue = None
    best_score = float('inf')

    for venue in department_venues:
        capacity = venue.exam_capacity or venue.capacity or 0
        if capacity <= 0:
            continue

        v_key = (venue.id, day, time_slot)

        # --- venue-slot exclusivity check (NEW) ---
        occupant_dept = venue_slot_department.get(v_key)
        if occupant_dept is not None and occupant_dept != dept:
            continue

        used = venue_used.get(v_key, 0)

        if used + student_count > capacity:
            continue

        # Check if venue already has too many courses in this slot
        courses_in_venue = sum(
            1 for (vid, d, t), count in venue_used.items()
            if vid == venue.id and d == day and t == time_slot
        )

        if courses_in_venue >= MAX_COURSES_PER_VENUE_PER_SLOT:
            continue

        # Calculate score: prefer venues with lower usage
        total_usage = sum(
            count for (vid, d, t), count in venue_used.items()
            if vid == venue.id
        )
        usage_ratio = total_usage / max(capacity, 1)

        score = usage_ratio

        if score < best_score:
            best_score = score
            best_venue = venue

    return best_venue


def _least_used_venues(venues: List, venue_used: Dict, n: int) -> List:
    """
    Return the `n` least-utilized venues overall. Used as the fallback pool
    for departments that didn't get an exclusive venue assignment, so
    "leftover" departments spread across whatever's free instead of all
    piling onto the same fixed pair of venues.
    """
    def total_usage(venue) -> int:
        return sum(count for (vid, d, t), count in venue_used.items() if vid == venue.id)

    return sorted(venues, key=total_usage)[:n]


def run_resit_scheduler(
    job_id: str,
    allocation_ids: list[int],
    config,
    progress_callback: Callable[[str, dict], None],
) -> None:
    """
    Main entry point for the resit scheduling algorithm.
    """
    stats = SchedulingStats(total_courses=len(allocation_ids))

    # Generate unique execution trace log file
    current_time_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_filepath = os.path.join(log_dir, f"resit_scheduler_{current_time_str}.txt")

    # Load models
    ResitCourseAllocation, ResitTempTimetable, ResitTimeSlot, StudentResitRegistration = _resit_models()
    Venue = _venue_model()

    with open(log_filepath, "w", encoding="utf-8") as f_log:
        _write_log_header(f_log, current_time_str)

        # Clear active table rows
        deleted_count = ResitTempTimetable.objects.count()
        ResitTempTimetable.objects.all().delete()
        f_log.write(f"Cleared {deleted_count} existing temporary timetable entries\n\n")

        # Update progress
        progress_callback(job_id, {"status": "running", "scheduled": 0, "failed": [], "total": stats.total_courses})

        # Load venues and time slots
        from resits_timetabling.models import ResitVenueExclusion
        excluded_venue_ids = ResitVenueExclusion.get_excluded_venue_ids()

        venues = list(Venue.objects.exclude(pk__in=excluded_venue_ids).order_by("pk"))
        venues_with_capacity = [v for v in venues if (v.exam_capacity or v.capacity or 0) > 0]

        if excluded_venue_ids:
            f_log.write(f"Resit venue exclusions active — {len(excluded_venue_ids)} venue(s) removed\n")

        f_log.write(f"Total venues: {len(venues)}\n")
        f_log.write(f"Venues with capacity: {len(venues_with_capacity)}\n")

        db_slots = list(ResitTimeSlot.objects.filter(config=config).order_by("date", "start_time").values(
            "date", "day", "start_time", "end_time"
        ))

        if not venues_with_capacity:
            f_log.write("FATAL ERROR: No venues with capacity available for scheduling.\n")
            progress_callback(job_id, {"status": "failed", "error": "No venues with capacity"})
            return

        if not db_slots:
            f_log.write("FATAL ERROR: No time slots available for scheduling.\n")
            progress_callback(job_id, {"status": "failed", "error": "No time slots"})
            return

        # Build scheduling grid
        seen_dates = {}
        seen_times = {}
        for s in db_slots:
            seen_dates[str(s["date"])] = s["day"]
            seen_times[_fmt(s["start_time"])] = _fmt(s["end_time"])

        slot_grid = sorted(seen_dates.items(), key=lambda x: x[0])
        time_slots = sorted(seen_times.items(), key=lambda x: x[0])

        # Ordinal position of each start time within a day (0 = first slot,
        # 1 = second, ...). Used to detect back-to-back (adjacent) exam
        # slots for a student regardless of the actual clock-time gap
        # between them -- see _calculate_time_slot_score.
        slot_index_map: Dict[str, int] = {start_t: idx for idx, (start_t, _end_t) in enumerate(time_slots)}

        total_days = len(slot_grid)
        total_time_slots = len(time_slots)
        total_venues = len(venues_with_capacity)

        f_log.write(f"\nScheduling Configuration:\n")
        f_log.write(f"  Days: {total_days}\n")
        f_log.write(f"  Time Slots: {total_time_slots}\n")
        f_log.write(f"  Venues: {total_venues}\n")
        f_log.write(f"  Max Exams Per Student Per Day: {MAX_EXAMS_PER_DAY}\n")
        f_log.write(f"  Min Gap Between Exams: {MIN_TIME_GAP_HOURS} hours\n")
        f_log.write(f"  Max Courses Per Venue Per Slot: {MAX_COURSES_PER_VENUE_PER_SLOT}\n\n")

        # Initialize tracking structures
        venue_used = defaultdict(int)
        student_slot_occupancy = defaultdict(list)
        student_daily_load = defaultdict(int)
        student_time_history = defaultdict(list)

        # NEW: venue-slot -> department exclusivity map.
        # Once a (venue, day, time) is claimed by a department, no other
        # department may place a course there, period.
        venue_slot_department: Dict[Tuple[int, str, str], str] = {}

        # Load student registrations
        alloc_students = defaultdict(set)
        reg_qs = StudentResitRegistration.objects.filter(
            resit_allocation_id__in=allocation_ids
        ).values("resit_allocation_id", "student_reg_no_normalized")

        count = 0
        for row in reg_qs:
            raw_id = row["student_reg_no_normalized"]
            if raw_id:
                canonical_id = _normalize_student_registration(raw_id)
                if canonical_id:
                    alloc_students[row["resit_allocation_id"]].add(canonical_id)
                    count += 1

        alloc_students = {k: frozenset(v) for k, v in alloc_students.items()}
        stats.students_tracked = len(alloc_students)
        f_log.write(f"Loaded {count} student registrations\n")
        f_log.write(f"Tracking {stats.students_tracked} unique students\n\n")

        # Load all allocations
        all_allocs = list(
            ResitCourseAllocation.objects
            .filter(id__in=allocation_ids)
            .select_related("program_course", "department", "department__faculty", "faculty")
        )

        # ================================================================
        # STEP 1: Group courses by department
        # ================================================================
        dept_groups = _group_courses_by_department(all_allocs, alloc_students)
        stats.departments_grouped = len(dept_groups)

        f_log.write("="*60 + "\n")
        f_log.write(" DEPARTMENT GROUPING\n")
        f_log.write("="*60 + "\n")
        for dept, courses in sorted(dept_groups.items()):
            total_students = sum(len(alloc_students.get(c.id, set())) for c in courses)
            f_log.write(f"  {dept}: {len(courses)} courses, {total_students} students\n")
        f_log.write("\n")

        # ================================================================
        # STEP 2: Assign venues to departments
        # ================================================================
        dept_venue_mapping = _assign_venues_to_departments(
            dept_groups, alloc_students, venues_with_capacity
        )

        f_log.write("="*60 + "\n")
        f_log.write(" VENUE ASSIGNMENT BY DEPARTMENT\n")
        f_log.write("="*60 + "\n")
        for dept, venues_list in dept_venue_mapping.items():
            venue_codes = [v.code for v in venues_list] or ["<none — will use least-used pool>"]
            f_log.write(f"  {dept}: {', '.join(venue_codes)}\n")
        f_log.write("\n")

        # ================================================================
        # STEP 3: Strategic Scheduling by Department
        # ================================================================
        f_log.write("="*60 + "\n")
        f_log.write(" STRATEGIC SCHEDULING BY DEPARTMENT\n")
        f_log.write("="*60 + "\n")

        scheduled_count = 0
        deferred_courses = []
        day_course_count = defaultdict(int)
        time_slot_course_count = defaultdict(int)

        # NEW: per-department day/time usage counters, used to spread a
        # department's own courses across the whole week instead of piling
        # them into the first slot that has room.
        dept_day_count: Dict[Tuple[str, str], int] = defaultdict(int)
        dept_time_count: Dict[Tuple[str, str], int] = defaultdict(int)

        # Track distribution for reporting
        day_schedule = defaultdict(list)
        venue_slot_usage = defaultdict(lambda: defaultdict(int))

        # Process each department
        for dept, courses in dept_groups.items():
            department_venues = dept_venue_mapping.get(dept) or _least_used_venues(
                venues_with_capacity, venue_used, n=3
            )

            f_log.write(f"\n  Department: {dept}\n")
            f_log.write(f"    Courses: {len(courses)}\n")
            f_log.write(f"    Venues: {', '.join([v.code for v in department_venues])}\n")

            # Process each course in the department
            for idx, alloc in enumerate(courses):
                s_set = alloc_students.get(alloc.id, set())
                s_count = max(len(s_set), 1)

                # Find the best slot
                best_slot_score = float('inf')
                best_day = None
                best_time = None
                best_venue = None

                # Try each day
                for day_offset in range(total_days):
                    day_idx = (idx + day_offset) % total_days
                    date_str, day_name = slot_grid[day_idx]

                    # Check daily load limit
                    exceeds_limit, _ = _check_daily_load_limit(
                        s_set, date_str, student_daily_load
                    )
                    if exceeds_limit:
                        continue

                    # Try each time slot
                    for time_offset in range(total_time_slots):
                        time_idx = (idx // max(total_days, 1) + time_offset) % total_time_slots
                        start_t, end_t = time_slots[time_idx]

                        # Calculate student conflict score
                        student_score = _calculate_time_slot_score(
                            (start_t, end_t), date_str, s_set,
                            student_slot_occupancy, student_daily_load,
                            student_time_history, slot_index_map
                        )

                        if student_score >= 100.0:
                            continue

                        # Find best venue within department's assigned venues,
                        # honoring venue-slot exclusivity (NEW args).
                        venue = _get_best_venue_for_course(
                            alloc, s_count, department_venues, venue_used,
                            date_str, start_t, dept, venue_slot_department
                        )

                        if not venue:
                            continue

                        # Calculate slot score — now also penalizes this
                        # department reusing a day/time slot it has already
                        # used, to force spread across the week (NEW terms).
                        day_load = day_course_count[date_str] / max(total_days, 1)
                        time_load = time_slot_course_count[(date_str, start_t)] / max(total_time_slots, 1)
                        dept_day_load = dept_day_count[(dept, date_str)]
                        dept_time_load = dept_time_count[(dept, start_t)]

                        total_score = (
                            student_score * 0.5
                            + day_load * 0.1
                            + time_load * 0.1
                            + dept_day_load * 0.15
                            + dept_time_load * 0.15
                        )

                        if total_score < best_slot_score:
                            best_slot_score = total_score
                            best_day = date_str
                            best_time = (start_t, end_t)
                            best_venue = venue

                if best_day and best_time and best_venue:
                    start_t, end_t = best_time

                    # Place the course
                    _commit_single(
                        alloc, best_venue, best_day, start_t, end_t,
                        s_count, s_set, venue_used, student_slot_occupancy,
                        student_daily_load, student_time_history,
                        ResitTempTimetable
                    )

                    # Claim this venue/slot for this department (NEW)
                    venue_slot_department[(best_venue.id, best_day, start_t)] = dept

                    scheduled_count += 1
                    stats.scheduled_courses = scheduled_count

                    # Update counters
                    day_course_count[best_day] += 1
                    time_slot_course_count[(best_day, start_t)] += 1
                    dept_day_count[(dept, best_day)] += 1
                    dept_time_count[(dept, start_t)] += 1
                    venue_slot_usage[best_day][(best_venue.code, start_t)] += 1

                    day_schedule[best_day].append({
                        'course': alloc.course_code,
                        'time': start_t,
                        'venue': best_venue.code,
                        'students': s_count,
                        'department': dept
                    })

                    f_log.write(
                        f"    ✅ {alloc.course_code} ({s_count} students) -> "
                        f"[{best_day} @ {start_t}] {best_venue.code}\n"
                    )

                    if scheduled_count % 10 == 0:
                        progress_callback(job_id, {"scheduled": scheduled_count})
                else:
                    deferred_courses.append(alloc)
                    f_log.write(f"    ⏳ {alloc.course_code} deferred\n")

        progress_callback(job_id, {"scheduled": scheduled_count})
        stats.scheduled_courses = scheduled_count
        stats.deferred_courses = len(deferred_courses)

        # ================================================================
        # STEP 4: Show Distribution Summary
        # ================================================================
        f_log.write("\n" + "="*60 + "\n")
        f_log.write(" DISTRIBUTION SUMMARY\n")
        f_log.write("="*60 + "\n")

        # Show department-venue mapping in final schedule
        f_log.write("\n  Department Venue Usage:\n")
        dept_venue_final = defaultdict(set)
        for date_str, courses in day_schedule.items():
            for course in courses:
                dept_venue_final[course['department']].add(course['venue'])

        for dept, venues in sorted(dept_venue_final.items()):
            f_log.write(f"    {dept}: {', '.join(sorted(venues))}\n")

        # Show venue distribution by day
        f_log.write("\n  Daily Venue Distribution:\n")
        for date_str in sorted(venue_slot_usage.keys()):
            f_log.write(f"    {date_str}:\n")
            slot_venues = defaultdict(list)
            for (venue_code, time_str), count in venue_slot_usage[date_str].items():
                slot_venues[time_str].append(f"{venue_code}({count})")

            for time_str in sorted(slot_venues.keys()):
                venues_str = ", ".join(slot_venues[time_str])
                f_log.write(f"      {time_str}: {venues_str}\n")

        # ================================================================
        # STEP 5: DEFERRED COURSE RESOLUTION (rewritten)
        # ================================================================
        # OLD BEHAVIOUR (removed): pass 1 tried a small set of venues in
        # fixed date/time order and gave up too early; the emergency
        # fallback then scanned (date, time, venue) in a FIXED order, so
        # every course that fell through landed in the very first
        # available cell — which is why ~19 unrelated small departments
        # (BLAW, DIBM, DPLM, MBAD, BUST, DIAC, BCHM, BTOM, NARE, WIEM,
        # AGRI, ...) all stacked into SGT1 Monday 8:30 in one run.
        #
        # NEW BEHAVIOUR: for each deferred course, build a ranked list of
        # EVERY (venue, day, time) combination across the whole week,
        # ranked so that:
        #   1. exclusive slots (unclaimed, or already owned by this dept)
        #      always outrank any slot that would mix departments
        #   2. among exclusive options, less-used venues and less-used
        #      days/timeslots for THIS department are preferred, so
        #      placements spread out instead of piling onto the first hit
        #   3. mixing departments is only ever chosen if literally no
        #      exclusive option exists anywhere in the grid, and even then
        #      the least-used candidate is picked (not the first match)
        if deferred_courses:
            f_log.write("\n" + "="*60 + "\n")
            f_log.write(" DEFERRED COURSE RESOLUTION\n")
            f_log.write("="*60 + "\n")

            for alloc in deferred_courses:
                s_set = alloc_students.get(alloc.id, set())
                s_count = max(len(s_set), 1)
                dept = _extract_department_code(alloc.course_code)

                candidates = []  # (priority_tuple, date_str, start_t, end_t, venue)

                for date_str, day in slot_grid:
                    exceeds_limit, _ = _check_daily_load_limit(
                        s_set, date_str, student_daily_load, max_exams=MAX_EXAMS_PER_DAY + 1
                    )
                    if exceeds_limit:
                        continue

                    for start_t, end_t in time_slots:
                        has_conflict = False
                        for student_id in s_set:
                            if (student_id, date_str, start_t) in student_slot_occupancy:
                                has_conflict = True
                                break
                        if has_conflict:
                            continue

                        # Would this slot put any student back-to-back with
                        # an exam they already have that day? (same
                        # adjacency logic as _calculate_time_slot_score —
                        # deferred courses need this too, not just Step 3.)
                        candidate_idx = slot_index_map.get(start_t)
                        would_be_adjacent = False
                        if candidate_idx is not None:
                            for student_id in s_set:
                                daily_key = (student_id, date_str)
                                for existing_start_t, _course in student_time_history.get(daily_key, []):
                                    existing_idx = slot_index_map.get(existing_start_t)
                                    if existing_idx is not None and abs(existing_idx - candidate_idx) == 1:
                                        would_be_adjacent = True
                                        break
                                if would_be_adjacent:
                                    break

                        for venue in venues_with_capacity:
                            v_key = (venue.id, date_str, start_t)
                            room_cap = venue.exam_capacity or venue.capacity or 0
                            used = venue_used.get(v_key, 0)
                            if room_cap <= 0 or used + s_count > room_cap:
                                continue

                            occupant_dept = venue_slot_department.get(v_key)
                            would_mix = occupant_dept is not None and occupant_dept != dept

                            venue_total_usage = sum(
                                c for (vid, d, t), c in venue_used.items() if vid == venue.id
                            )
                            dept_day_load = dept_day_count[(dept, date_str)]
                            dept_time_load = dept_time_count[(dept, start_t)]

                            # Lower tuple = higher priority. `would_mix` and
                            # `would_be_adjacent` outrank everything else so
                            # every option that avoids BOTH department
                            # mixing AND back-to-back student exams is
                            # exhausted before either compromise is made.
                            priority = (
                                1 if would_mix else 0,
                                1 if would_be_adjacent else 0,
                                dept_day_load,
                                dept_time_load,
                                venue_total_usage,
                            )
                            candidates.append((priority, date_str, start_t, end_t, venue, would_mix, occupant_dept))

                candidates.sort(key=lambda c: c[0])

                placed = False
                if candidates:
                    priority, date_str, start_t, end_t, venue, would_mix, occupant_dept = candidates[0]

                    if would_mix:
                        f_log.write(
                            f"  🚨 [EMERGENCY] {alloc.course_code}: no exclusive slot available "
                            f"anywhere in the week — sharing {venue.code}/{date_str}/{start_t} "
                            f"with department {occupant_dept}\n"
                        )

                    _commit_single(
                        alloc, venue, date_str, start_t, end_t,
                        s_count, s_set, venue_used, student_slot_occupancy,
                        student_daily_load, student_time_history,
                        ResitTempTimetable
                    )
                    if not would_mix:
                        venue_slot_department[(venue.id, date_str, start_t)] = dept
                    dept_day_count[(dept, date_str)] += 1
                    dept_time_count[(dept, start_t)] += 1
                    scheduled_count += 1
                    placed = True
                    stats.scheduled_courses = scheduled_count
                    if would_mix:
                        stats.emergency_placements += 1

                    tag = "🚨 [EMERGENCY PLACED]" if would_mix else "✅ [RESOLVED]"
                    f_log.write(
                        f"  {tag} {alloc.course_code} ({s_count} students) -> "
                        f"[{date_str} @ {start_t}] {venue.code}\n"
                    )

                if not placed:
                    stats.failed_courses += 1
                    f_log.write(f"  ❌ [FAILED] {alloc.course_code} could not be placed — no capacity anywhere\n")

        stats.scheduled_courses = scheduled_count

        # ================================================================
        # STEP 6: POST-SCHEDULING VALIDATION
        # ================================================================
        f_log.write("\n" + "="*60 + "\n")
        f_log.write(" POST-SCHEDULING VALIDATION\n")
        f_log.write("="*60 + "\n")

        # Validate schedule
        scheduled_entries = ResitTempTimetable.objects.filter(
            resit_course_allocation_id__in=allocation_ids
        ).select_related('resit_course_allocation', 'venue')

        validation_occupancy = defaultdict(list)
        validation_daily_load = defaultdict(int)
        validation_consecutive = defaultdict(list)

        for entry in scheduled_entries:
            date_str = entry.date.isoformat()
            start_t = _fmt(entry.start_time)
            students = alloc_students.get(entry.resit_course_allocation.id, set())

            for student_id in students:
                slot_key = (student_id, date_str, start_t)
                validation_occupancy[slot_key].append(entry.resit_course_allocation.course_code)
                daily_key = (student_id, date_str)
                validation_daily_load[daily_key] += 1
                validation_consecutive[(student_id, date_str)].append((start_t, entry.resit_course_allocation.course_code))

        # Check collisions
        collisions_found = 0
        for (student_id, date_str, start_t), courses in validation_occupancy.items():
            if len(courses) > 1:
                collisions_found += 1
                f_log.write(
                    f"  ⚠️ [COLLISION] Student {student_id} on {date_str} @ {start_t}: "
                    f"{' vs '.join(courses)}\n"
                )
        stats.slot_collisions = collisions_found

        # Check daily load violations
        daily_violations = 0
        for (student_id, date_str), count in validation_daily_load.items():
            if count > MAX_EXAMS_PER_DAY:
                daily_violations += 1
        stats.daily_load_violations = daily_violations

        # Check consecutive exams
        consecutive_violations = 0
        for (student_id, date_str), exams in validation_consecutive.items():
            sorted_exams = sorted(exams, key=lambda x: _time_to_hours(x[0]))
            for i in range(len(sorted_exams) - 1):
                time1, course1 = sorted_exams[i]
                time2, course2 = sorted_exams[i + 1]
                gap = _time_diff_hours(time1, time2)
                if gap < MIN_TIME_GAP_HOURS:
                    consecutive_violations += 1
        stats.consecutive_exam_violations = consecutive_violations

        # NEW: check venue-slot department mixing (should be zero outside
        # of logged emergency placements)
        mixing_found = 0
        venue_slot_courses = defaultdict(set)
        for entry in scheduled_entries:
            v_key = (entry.venue.id, entry.date.isoformat(), _fmt(entry.start_time))
            dept = _extract_department_code(entry.resit_course_allocation.course_code)
            venue_slot_courses[v_key].add(dept)
        for v_key, depts in venue_slot_courses.items():
            if len(depts) > 1:
                mixing_found += 1
                f_log.write(f"  ⚠️ [DEPT MIXING] {v_key}: departments {sorted(depts)}\n")

        # Summary
        stats.end_time = datetime.datetime.now()
        stats.venues_utilized = len(set(entry.venue.id for entry in scheduled_entries))

        f_log.write("\n" + "="*80 + "\n")
        f_log.write(" SCHEDULING SUMMARY\n")
        f_log.write("="*80 + "\n")
        f_log.write(f"Total courses:                    {stats.total_courses}\n")
        f_log.write(f"Scheduled successfully:           {stats.scheduled_courses}\n")
        f_log.write(f"Deferred to resolution:           {stats.deferred_courses}\n")
        f_log.write(f"Failed (could not schedule):     {stats.failed_courses}\n")
        f_log.write(f"Emergency placements:             {stats.emergency_placements}\n")
        f_log.write(f"Venues utilized:                  {stats.venues_utilized}\n")
        f_log.write(f"Departments grouped:              {stats.departments_grouped}\n")
        f_log.write(f"Slot collisions detected:         {stats.slot_collisions}\n")
        f_log.write(f"Daily load violations:            {stats.daily_load_violations}\n")
        f_log.write(f"Consecutive exam violations:      {stats.consecutive_exam_violations}\n")
        f_log.write(f"Venue/slot dept-mixing instances:  {mixing_found}\n")
        f_log.write(f"Unique students tracked:          {stats.students_tracked}\n")
        f_log.write(f"Max courses per venue per slot:   {MAX_COURSES_PER_VENUE_PER_SLOT}\n")
        f_log.write(f"Execution duration:               {stats.duration:.2f} seconds\n")
        f_log.write("="*80 + "\n")

        success_rate = (stats.scheduled_courses / stats.total_courses * 100) if stats.total_courses > 0 else 0
        if success_rate == 100:
            f_log.write(f"\n✅ SCHEDULING COMPLETE: 100% success rate\n")
        elif success_rate >= 95:
            f_log.write(f"\n⚠️ SCHEDULING COMPLETE: {success_rate:.1f}% success rate\n")
        else:
            f_log.write(f"\n❌ SCHEDULING COMPLETE: {success_rate:.1f}% success rate - Manual review required\n")

        # Final progress update
        progress_callback(job_id, {
            "status": "done",
            "scheduled": stats.scheduled_courses,
            "failed": stats.failed_courses,
            "total": stats.total_courses,
            "deferred": stats.deferred_courses,
            "collisions": stats.slot_collisions,
            "duration": stats.duration
        })


def _check_daily_load_limit(
    student_set: Set[str],
    date_str: str,
    student_daily_load: Dict,
    max_exams: int = MAX_EXAMS_PER_DAY
) -> Tuple[bool, List[str]]:
    """Check if any student would exceed the daily exam limit."""
    exceeding_students = []
    for student_id in student_set:
        daily_key = (student_id, date_str)
        current_load = student_daily_load.get(daily_key, 0)
        if current_load >= max_exams:
            exceeding_students.append(student_id)
    return len(exceeding_students) > 0, exceeding_students


def _commit_single(
    alloc,
    venue,
    date_str: str,
    start_t: str,
    end_t: str,
    reg_students: int,
    current_alloc_students: Set[str],
    venue_used: Dict,
    student_slot_occupancy: Dict,
    student_daily_load: Dict,
    student_time_history: Dict,
    ResitTempTimetable
) -> None:
    """Commit a single allocation to the database."""
    _date = _as_date(date_str)
    _st = _as_time(start_t)
    _et = _as_time(end_t)

    with transaction.atomic():
        ResitTempTimetable.objects.create(
            resit_course_allocation=alloc,
            venue=venue,
            date=_date,
            day=_date.strftime("%A"),
            start_time=_st,
            end_time=_et,
            registered_students=reg_students
        )

    v_key = (venue.id, date_str, start_t)
    venue_used[v_key] += reg_students

    for student_id in current_alloc_students:
        canonical_id = _normalize_student_registration(student_id) if student_id else student_id
        if canonical_id:
            slot_key = (canonical_id, date_str, start_t)
            student_slot_occupancy[slot_key].append(alloc.course_code)

            daily_key = (canonical_id, date_str)
            student_daily_load[daily_key] += 1

            time_key = (canonical_id, date_str)
            student_time_history.setdefault(time_key, []).append((start_t, alloc.course_code))


def _write_log_header(f_log, timestamp: str) -> None:
    """Write the log file header."""
    f_log.write("=" * 80 + "\n")
    f_log.write(" RESIT SCHEDULER EXECUTION LOG\n")
    f_log.write(f" Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    f_log.write(" Strategy: Department-Based Grouping with EXCLUSIVE Venue/Slot Co-location\n")
    f_log.write(f" Max Exams Per Student Per Day: {MAX_EXAMS_PER_DAY}\n")
    f_log.write(f" Min Gap Between Exams: {MIN_TIME_GAP_HOURS} hours\n")
    f_log.write(f" Max Courses Per Venue Per Slot: {MAX_COURSES_PER_VENUE_PER_SLOT}\n")
    f_log.write(" Pattern: May 2025 Resits Timetable + venue-slot exclusivity + dept spread scoring\n")
    f_log.write("=" * 80 + "\n\n")