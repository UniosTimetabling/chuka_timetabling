"""
Dual-Campus Regular Timetable Scheduler (v13 - Fixed Duplicate Prevention with Slot Locks)
==========================================================================================
Critical fixes:
1. Slot-level locks prevent concurrent writes to same venue+slot
2. get_or_create fallback instead of save() to avoid IntegrityError spam
3. Double-check cache after acquiring lock
4. Check allocation already scheduled before writing
5. Optimized bulk create with proper filtering
"""

import random
import re
import os
import sys
import time
import threading
import traceback
import logging
from collections import defaultdict
from datetime import datetime, date, timedelta, time as dtime
from functools import wraps
from typing import Dict, List, Optional, Set, Tuple, Any
import heapq
from dataclasses import dataclass, field as _field

scheduler_logger = logging.getLogger("scheduler")

from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.db import IntegrityError, OperationalError, connection, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Count

from campuses_timetable.models import (
    Campus,
    CampusCourseAllocation,
    CampusSchedulerConfig,
    CampusTempTimetable,
    CampusTimetable,
)
from course_allocation.models import CourseAllocation, CombinedCourseGroup
from department_management.models import Department
from faculty_management.models import Faculty
from program_management.models import Program, ProgramCourse
from room_management.models import Venue, Building, VenueSpecialization
from timetable.models import (
    AutoMergedExamGroup,
    SchedulerConfig,
    TempTimetable,
    Timetable,
    MergedCourseGroupTimetable,
)
from notifications.models import Notification


# ─────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────
TRAVEL_GAP_HOURS   = 2
SLOT_BREAK_MINUTES = 0
MERGE_LIMIT        = 200
_MAX_LOG_BUFFER    = 500

# ─────────────────────────────────────────────────────────
# SLOT-LEVEL LOCKS (Critical for duplicate prevention)
# ─────────────────────────────────────────────────────────
_slot_locks: Dict[Tuple, threading.Lock] = {}
_slot_lock_lock = threading.Lock()


def _get_slot_lock(venue_id, day_str, start_time, end_time):
    """Get or create a lock for a specific (venue, day, start_time) combination."""
    key = (venue_id, day_str, start_time, end_time)
    with _slot_lock_lock:
        if key not in _slot_locks:
            _slot_locks[key] = threading.Lock()
        return _slot_locks[key]


# ─────────────────────────────────────────────────────────
# FILE-BASED RUN LOG
# ─────────────────────────────────────────────────────────
_ALGO_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR  = os.path.join(_ALGO_DIR, "logs")
_scheduler_log_fh = None


def _open_scheduler_log() -> None:
    global _scheduler_log_fh
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_path = os.path.join(_LOG_DIR, f"dual_campus_scheduler_{timestamp}.txt")
        if _scheduler_log_fh is not None:
            try:
                _scheduler_log_fh.close()
            except Exception:
                pass
        _scheduler_log_fh = open(log_path, "w", encoding="utf-8", buffering=1)
        _scheduler_log_fh.write(
            f"Dual-Campus Regular Timetable Scheduler — Run Log\n"
            f"Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Log file: {log_path}\n"
            f"{'=' * 70}\n\n"
        )
    except Exception as _e:
        try:
            sys.stdout.write(f"[RunLog] Could not open log file: {_e}\n")
        except Exception:
            pass


def _close_scheduler_log(success: bool = True) -> None:
    global _scheduler_log_fh
    if _scheduler_log_fh is None:
        return
    try:
        status = "COMPLETED" if success else "ENDED WITH ERROR"
        _scheduler_log_fh.write(
            f"\n{'=' * 70}\n"
            f"Run {status} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        _scheduler_log_fh.flush()
        _scheduler_log_fh.close()
    except Exception:
        pass
    finally:
        _scheduler_log_fh = None


# ─────────────────────────────────────────────────────────
# PROGRESS TRACKING
# ─────────────────────────────────────────────────────────
dual_scheduler_progress: Dict = {
    "status": "idle", "progress": 0, "current_action": "",
    "scheduled_count": 0, "remaining_count": 0,
    "batch_info": "", "total_courses": 0, "current_batch": 0,
    "total_batches": 0, "message": "",
    "console_output": [], "main_scheduled": [], "campus_scheduled": [],
    "unscheduled": [], "cross_campus_lecturers": [],
    "conflict_stats": {"detected": 0, "resolved": 0,
                       "unresolved": 0, "resolution_rate": 0},
}
_progress_lock = threading.Lock()
_print_lock = threading.Lock()
_log_buffer: List[str] = []

_dual_cache = None
_cache_lock = threading.Lock()


def get_dual_cache():
    global _dual_cache
    with _cache_lock:
        if _dual_cache is None:
            _dual_cache = DualSchedulerCache()
        return _dual_cache


def safe_print(*args):
    global _log_buffer
    try:
        msg = " ".join(str(a) for a in args)
        with _print_lock:
            _log_buffer.append(msg)
            if len(_log_buffer) > _MAX_LOG_BUFFER:
                _log_buffer = _log_buffer[-_MAX_LOG_BUFFER:]
        try:
            sys.stdout.write(msg + "\n")
            sys.stdout.flush()
        except Exception:
            pass
        if _scheduler_log_fh is not None:
            try:
                ts = datetime.now().strftime("%H:%M:%S")
                _scheduler_log_fh.write(f"[{ts}] {msg}\n")
            except Exception:
                pass
    except Exception:
        pass


def update_progress(progress, action, scheduled=None, remaining=None,
                    batch_info="", current_batch=0, total_batches=0,
                    console_msg="", main_scheduled=None, campus_scheduled=None,
                    unscheduled=None, cross_campus_lecturers=None,
                    conflict_stats=None):
    with _progress_lock:
        dual_scheduler_progress["progress"] = int(progress)
        dual_scheduler_progress["current_action"] = action
        if scheduled is not None: dual_scheduler_progress["scheduled_count"] = scheduled
        if remaining is not None: dual_scheduler_progress["remaining_count"] = remaining
        if batch_info: dual_scheduler_progress["batch_info"] = batch_info
        dual_scheduler_progress["current_batch"] = current_batch
        dual_scheduler_progress["total_batches"] = total_batches
        if console_msg:
            dual_scheduler_progress["console_output"].append(console_msg)
            if len(dual_scheduler_progress["console_output"]) > 200:
                dual_scheduler_progress["console_output"] = (
                    dual_scheduler_progress["console_output"][-200:])
        if main_scheduled is not None:
            dual_scheduler_progress["main_scheduled"] = main_scheduled
        if campus_scheduled is not None:
            dual_scheduler_progress["campus_scheduled"] = campus_scheduled
        if unscheduled is not None:
            dual_scheduler_progress["unscheduled"] = unscheduled
        if cross_campus_lecturers is not None:
            dual_scheduler_progress["cross_campus_lecturers"] = cross_campus_lecturers
        if conflict_stats is not None:
            dual_scheduler_progress["conflict_stats"] = conflict_stats


# ─────────────────────────────────────────────────────────
# RETRY / WAL
# ─────────────────────────────────────────────────────────
def retry_on_lock(max_retries=5, delay=0.5):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except OperationalError as e:
                    if "database is locked" in str(e) and attempt < max_retries - 1:
                        time.sleep(delay * (attempt + 1))
                    else:
                        raise
        return wrapper
    return decorator


def enable_wal_mode():
    # SECURITY NOTE: all cursor.execute() calls below use fixed, hardcoded
    # PRAGMA strings — no user input, no string interpolation, no params
    # needed. Audited as SQL-injection-safe; do not add dynamic values here.
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" not in engine.lower():
        safe_print("Non-SQLite backend – WAL/PRAGMA skipped.")
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA cache_size=-20000;")
            cursor.execute("PRAGMA temp_store=MEMORY;")
        safe_print("WAL mode enabled")
    except Exception as e:
        safe_print(f"WAL note: {e}")


# ─────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────
def normalize_code(code: str) -> str:
    if not code:
        return ""
    s = str(code).strip().upper()
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"([A-Z])$", "", s)
    s = re.sub(r"[\-_.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    m = re.search(r"[A-Z]+\s*\d+", s)
    return m.group(0).replace(" ", "") if m else s.replace(" ", "")


def is_postgraduate(course_code: str) -> bool:
    if not course_code:
        return False
    raw = re.sub(r"\([^)]*\)", "", course_code.strip().upper())
    raw = re.sub(r"\s+", "", raw)
    m = re.search(r"(\d+)", raw)
    if not m:
        return False
    numeric = m.group(1)
    if numeric.startswith("0"):
        return False
    return int(numeric[0]) >= 6


def get_course_year(course_code: str) -> int:
    code = course_code.upper().strip()
    match = re.search(r"^[A-Z]*(\d)(?:\d{2})", code)
    if match:
        y = int(match.group(1))
        if 1 <= y <= 6:
            return y
    try:
        # Strip a trailing disambiguation tag (e.g. "ZOOL 143(COM)" -> "ZOOL 143")
        # added when a course code is shared by more than one program, so the
        # ProgramCourse lookup below still matches on the plain curriculum code.
        bare_code = re.sub(r'\([A-Z0-9]+\)\s*$', '', course_code, flags=re.IGNORECASE).strip()
        pc = ProgramCourse.objects.filter(course_code__iexact=bare_code).first()
        if pc and pc.year and str(pc.year).isdigit():
            return int(pc.year)
    except Exception:
        pass
    return 1


def slot_minutes(t: dtime) -> int:
    return t.hour * 60 + t.minute


def student_count(alloc) -> int:
    return max(getattr(alloc, "number_of_students", 0) or 1, 1)


def venue_exam_capacity(venue) -> int:
    ec = getattr(venue, "exam_capacity", None)
    if ec:
        return int(ec)
    raw = getattr(venue, "capacity", None)
    return int(raw) if raw else 0


def is_afternoon_course(course_code: str) -> bool:
    m = re.search(r"\d", course_code or "")
    return bool(m) and m.group(0) in ("8", "9")


def _fuzzy_slot_lookup(start_time: dtime, end_time: dtime,
                       slots: List[Tuple[dtime, dtime]],
                       tolerance_minutes: int = 2) -> Optional[int]:
    for idx, (s, e) in enumerate(slots):
        if s == start_time and e == end_time:
            return idx

    def _to_seconds(t) -> int:
        return t.hour * 3600 + t.minute * 60 + t.second

    tolerance_secs = tolerance_minutes * 60
    db_start_secs = _to_seconds(start_time)
    for idx, (s, e) in enumerate(slots):
        if abs(_to_seconds(s) - db_start_secs) <= tolerance_secs:
            return idx
    return None


def _is_elective(alloc) -> bool:
    return bool(getattr(alloc, 'is_elective', False))


def _get_selection_group_id(alloc) -> Optional[int]:
    try:
        sg = getattr(alloc, 'selection_group', None)
        return sg.id if sg else None
    except Exception:
        return None


def _get_specialization_stem_id(alloc) -> Optional[int]:
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.id if st else None
    except Exception:
        return None


def _get_specialization_category_id(alloc) -> Optional[int]:
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.category_id if st else None
    except Exception:
        return None


def _get_alloc_intake(alloc) -> str:
    return getattr(alloc, 'intake', 'normal') or 'normal'


def _get_alloc_semester(alloc):
    """Return the ProgramCourse semester (1 or 2) for an allocation, or None."""
    pc = getattr(alloc, 'program_course', None)
    return getattr(pc, 'semester', None) if pc else None


def is_program_year_collision_exempt(alloc_a, alloc_b) -> bool:
    # ── Different ProgramCourse semester (1 vs 2) — but ONLY exempt when
    # paired with a different intake (special is a shifted-semester cohort
    # of the same year). Same intake + different semester is NOT exempt.
    # Kept in sync with timetable.timetable_panel.is_scheduling_exempt.
    # Checked first since it's decisive regardless of stem/group below.
    sem_a = _get_alloc_semester(alloc_a)
    sem_b = _get_alloc_semester(alloc_b)
    if (
        sem_a is not None and sem_b is not None and sem_a != sem_b
        and _get_alloc_intake(alloc_a) != _get_alloc_intake(alloc_b)
    ):
        return True

    # ── SpecializationStem takes priority over everything else ────────────
    # A student picks ONE stem and takes EVERY course in it, so:
    #   * same stem            → NEVER exempt — must clash-check like ordinary
    #     mandatory courses, even if a course is *also* flagged Elective
    #     (e.g. a stem with 5 courses where only 4 are required: any two of
    #     the 5 can still end up in the same student's chosen subset, so
    #     every pair within the stem must still be treated as colliding).
    #   * different stems,
    #     same category        → exempt (student never takes both stems)
    #   * different categories
    #     / only one side has
    #     a stem               → falls through to the checks below
    st_a = _get_specialization_stem_id(alloc_a)
    st_b = _get_specialization_stem_id(alloc_b)
    if st_a is not None and st_b is not None and st_a == st_b:
        return False
    if st_a is not None and st_b is not None and st_a != st_b:
        cat_a = _get_specialization_category_id(alloc_a)
        cat_b = _get_specialization_category_id(alloc_b)
        if cat_a is not None and cat_a == cat_b:
            return True

    # ── StudentGroup: different groups within the same program/year are
    # different cohorts of students, so their compulsory courses can run
    # concurrently. The SAME group's own courses still clash-check normally,
    # exactly like ordinary mandatory courses — this only exempts pairs that
    # belong to two DIFFERENT, explicitly-set groups. A shared course
    # (student_group=None — electives/stem courses, or anything not yet
    # split into groups) is attended by everyone and must keep clashing with
    # every group's courses, so it deliberately falls through untouched here.
    sgrp_a = getattr(alloc_a, 'student_group_id', None)
    sgrp_b = getattr(alloc_b, 'student_group_id', None)
    if sgrp_a is not None and sgrp_b is not None and sgrp_a != sgrp_b:
        return True

    # Selection group — only exempt when BOTH sides sit in the SAME
    # SelectionGroup (students pick exactly one course from that group,
    # so two courses in it can never both be taken by one student).
    # Being merely `is_elective=True`, or belonging to two DIFFERENT
    # selection groups, is NOT exempt — a student can pick one course
    # from group A and one from group B, so those must still clash-check
    # normally.
    sg_a = _get_selection_group_id(alloc_a)
    sg_b = _get_selection_group_id(alloc_b)
    if sg_a is not None and sg_b is not None and sg_a == sg_b:
        return True

    if _get_alloc_intake(alloc_a) != _get_alloc_intake(alloc_b):
        return True
    return False


# ─────────────────────────────────────────────────────────
# DUPLICATE-ENTRY CACHE
# ─────────────────────────────────────────────────────────
class DualSchedulerCache:
    """Thread-safe cache to track all written entries to prevent duplicates."""
    def __init__(self):
        self.existing_main_entries: Set[Tuple] = set()
        self.existing_campus_entries: Set[Tuple] = set()
        self.scheduled_course_keys: Set[Tuple] = set()
        self._lock = threading.RLock()
        self._dirty = False

    def is_duplicate_main(self, venue_id, day_str, start_time, end_time) -> bool:
        key = (venue_id, day_str, start_time, end_time)
        with self._lock:
            return key in self.existing_main_entries

    def add_main_entry(self, venue_id, day_str, start_time, end_time):
        key = (venue_id, day_str, start_time, end_time)
        with self._lock:
            self.existing_main_entries.add(key)
            self._dirty = True

    def add_main_entries(self, entries: List[Tuple]):
        with self._lock:
            for venue_id, day_str, start_time, end_time in entries:
                self.existing_main_entries.add((venue_id, day_str, start_time, end_time))
            self._dirty = True

    def is_duplicate_campus(self, campus_id, day_str, start_time, end_time) -> bool:
        key = (campus_id, day_str, start_time, end_time)
        with self._lock:
            return key in self.existing_campus_entries

    def add_campus_entry(self, campus_id, day_str, start_time, end_time):
        key = (campus_id, day_str, start_time, end_time)
        with self._lock:
            self.existing_campus_entries.add(key)
            self._dirty = True

    def is_course_scheduled(self, course_code: str, lecturer_id, program_id) -> bool:
        key = (normalize_code(course_code), lecturer_id, program_id)
        with self._lock:
            return key in self.scheduled_course_keys

    def mark_course_scheduled(self, course_code: str, lecturer_id, program_id):
        key = (normalize_code(course_code), lecturer_id, program_id)
        with self._lock:
            self.scheduled_course_keys.add(key)

    def load_existing_entries(self):
        """Sync from DB — call this before any scheduling phase."""
        try:
            with self._lock:
                self.existing_main_entries = set(
                    TempTimetable.objects.values_list(
                        "venue_id", "day", "start_time", "end_time").distinct()
                )
                self.existing_campus_entries = set(
                    CampusTempTimetable.objects.values_list(
                        "campus_id", "day", "start_time", "end_time").distinct()
                )
                for tt in TempTimetable.objects.select_related('course_allocation').all():
                    ca = tt.course_allocation
                    if ca:
                        lid = ca.lecturer.id if ca.lecturer else None
                        pid = ca.program.id if ca.program else None
                        self.scheduled_course_keys.add(
                            (normalize_code(ca.course_code), lid, pid)
                        )
                for ct in CampusTempTimetable.objects.select_related('course_allocation').all():
                    ca = ct.course_allocation
                    if ca:
                        lid = ca.lecturer.id if ca.lecturer else None
                        pid = ca.program.id if ca.program else None
                        self.scheduled_course_keys.add(
                            (normalize_code(ca.course_code), lid, pid)
                        )
            safe_print(f"[DualCache] Loaded {len(self.existing_main_entries)} main entries, "
                      f"{len(self.existing_campus_entries)} campus entries, "
                      f"{len(self.scheduled_course_keys)} course keys")
        except Exception as e:
            safe_print(f"[DualCache] load_existing_entries error: {e}")

    def clear(self):
        with self._lock:
            self.existing_main_entries.clear()
            self.existing_campus_entries.clear()
            self.scheduled_course_keys.clear()
            self._dirty = False
        safe_print("[DualCache] Cache cleared")


# ─────────────────────────────────────────────────────────
# SAFE BULK CREATE (WITH SLOT LOCKS)
# ─────────────────────────────────────────────────────────
def safe_bulk_create_main_entries(entries: List["TempTimetable"],
                                   cache: "DualSchedulerCache",
                                   batch_size: int = 100) -> int:
    """
    Thread-safe bulk create with slot-level locks to prevent duplicate entries.
    Uses get_or_create fallback to avoid IntegrityError spam.
    """
    if not entries:
        return 0

    # Filter entries that are already in cache or already scheduled
    filtered = []
    for e in entries:
        if cache.is_duplicate_main(e.venue_id, e.day, e.start_time, e.end_time):
            continue
        # Check if this allocation is already scheduled anywhere
        if TempTimetable.objects.filter(course_allocation=e.course_allocation).exists():
            continue
        filtered.append(e)

    if not filtered:
        return 0

    # Group by venue+slot to use locks
    by_slot: Dict[Tuple, List] = defaultdict(list)
    for e in filtered:
        key = (e.venue_id, e.day, e.start_time, e.end_time)
        by_slot[key].append(e)

    created_count = 0

    for slot_key, slot_entries in by_slot.items():
        venue_id, day_str, start_time, end_time = slot_key
        lock = _get_slot_lock(venue_id, day_str, start_time, end_time)

        with lock:
            # Double-check cache after acquiring lock
            if cache.is_duplicate_main(venue_id, day_str, start_time, end_time):
                continue

            # Remove entries that might have been scheduled since filtering
            to_create = []
            for e in slot_entries:
                if not TempTimetable.objects.filter(course_allocation=e.course_allocation).exists():
                    to_create.append(e)

            if not to_create:
                continue

            for i in range(0, len(to_create), batch_size):
                batch = to_create[i:i + batch_size]
                try:
                    with transaction.atomic():
                        TempTimetable.objects.bulk_create(batch, ignore_conflicts=True)
                        for e in batch:
                            cache.add_main_entry(e.venue_id, e.day, e.start_time, e.end_time)
                            created_count += 1
                except Exception as ex:
                    # Fallback to get_or_create for each entry
                    for e in batch:
                        try:
                            obj, created = TempTimetable.objects.get_or_create(
                                course_allocation=e.course_allocation,
                                venue=e.venue,
                                day=e.day,
                                start_time=e.start_time,
                                end_time=e.end_time,
                            )
                            if created:
                                cache.add_main_entry(e.venue_id, e.day, e.start_time, e.end_time)
                                created_count += 1
                        except Exception:
                            pass
                time.sleep(0.02)

    return created_count


def safe_bulk_create_campus_entries(entries: List["CampusTempTimetable"],
                                     cache: "DualSchedulerCache",
                                     batch_size: int = 100) -> int:
    """
    Thread-safe bulk create for campus entries with slot-level locks.
    """
    if not entries:
        return 0

    # Filter entries that are already in cache or already scheduled
    filtered = []
    for e in entries:
        campus_id = e.campus_id if hasattr(e, 'campus_id') else (e.campus.id if e.campus else 0)
        if cache.is_duplicate_campus(campus_id, e.day, e.start_time, e.end_time):
            continue
        if CampusTempTimetable.objects.filter(course_allocation=e.course_allocation).exists():
            continue
        filtered.append(e)

    if not filtered:
        return 0

    # Group by campus+slot to use locks
    by_slot: Dict[Tuple, List] = defaultdict(list)
    for e in filtered:
        campus_id = e.campus_id if hasattr(e, 'campus_id') else (e.campus.id if e.campus else 0)
        key = (campus_id, e.day, e.start_time, e.end_time)
        by_slot[key].append(e)

    created_count = 0

    for slot_key, slot_entries in by_slot.items():
        campus_id, day_str, start_time, end_time = slot_key
        lock = _get_slot_lock(campus_id, day_str, start_time, end_time)

        with lock:
            if cache.is_duplicate_campus(campus_id, day_str, start_time, end_time):
                continue

            to_create = []
            for e in slot_entries:
                if not CampusTempTimetable.objects.filter(course_allocation=e.course_allocation).exists():
                    to_create.append(e)

            if not to_create:
                continue

            for i in range(0, len(to_create), batch_size):
                batch = to_create[i:i + batch_size]
                try:
                    with transaction.atomic():
                        CampusTempTimetable.objects.bulk_create(batch, ignore_conflicts=True)
                        for e in batch:
                            campus_id_e = e.campus_id if hasattr(e, 'campus_id') else (e.campus.id if e.campus else 0)
                            cache.add_campus_entry(campus_id_e, e.day, e.start_time, e.end_time)
                            created_count += 1
                except Exception as ex:
                    for e in batch:
                        try:
                            obj, created = CampusTempTimetable.objects.get_or_create(
                                course_allocation=e.course_allocation,
                                campus=e.campus,
                                day=e.day,
                                start_time=e.start_time,
                                end_time=e.end_time,
                            )
                            if created:
                                campus_id_e = e.campus_id if hasattr(e, 'campus_id') else (e.campus.id if e.campus else 0)
                                cache.add_campus_entry(campus_id_e, e.day, e.start_time, e.end_time)
                                created_count += 1
                        except Exception:
                            pass
                time.sleep(0.02)

    return created_count


# ─────────────────────────────────────────────────────────
# SLOT GENERATION
# ─────────────────────────────────────────────────────────
def generate_slots(start: dtime, end: dtime, slot_hours: int) -> List[Tuple[dtime, dtime]]:
    slots, today = [], date.today()
    cur = datetime.combine(today, start)
    end_dt = datetime.combine(today, end)
    delta = timedelta(hours=slot_hours)
    brk = timedelta(minutes=SLOT_BREAK_MINUTES)
    while cur + delta <= end_dt:
        slots.append((cur.time(), (cur + delta).time()))
        cur = cur + delta + brk
    return slots


# ─────────────────────────────────────────────────────────
# VENUE SPECIALIZATION HELPERS
# ─────────────────────────────────────────────────────────
def build_specialization_index():
    course_to_venues: Dict[str, list] = defaultdict(list)
    specialization_venue_ids: set = set()

    active_rules = (
        VenueSpecialization.objects
        .filter(is_active=True)
        .prefetch_related('venues', 'courses', 'programs__courses', 'departments__programs__courses')
        .order_by('priority')
    )

    for rule in active_rules:
        rule_venues = list(rule.venues.all())
        for v in rule_venues:
            specialization_venue_ids.add(v.id)

        codes: set = set()
        for c in rule.courses.all():
            codes.add(normalize_code(c.course_code))
        for prog in rule.programs.all():
            for pc in prog.courses.all():
                codes.add(normalize_code(pc.course_code))
        for dept in rule.departments.all():
            for prog in dept.programs.all():
                for pc in prog.courses.all():
                    codes.add(normalize_code(pc.course_code))

        for code in codes:
            for v in rule_venues:
                course_to_venues[code].append((v, rule))

    return dict(course_to_venues), specialization_venue_ids


# ─────────────────────────────────────────────────────────
# UNIFIED COURSE GROUP
# ─────────────────────────────────────────────────────────
class UnifiedCourseGroup:
    def __init__(self, norm_code: str):
        self.norm_code = norm_code
        self.main_allocs: List[CourseAllocation] = []
        self.campus_allocs: List[CampusCourseAllocation] = []
        self.scheduled_date: Optional[date] = None
        self.scheduled_slot_start: Optional[dtime] = None
        self.scheduled_slot_end: Optional[dtime] = None

    @property
    def all_allocs(self):
        return self.main_allocs + self.campus_allocs

    @property
    def total_main_students(self):
        return sum(student_count(a) for a in self.main_allocs)

    @property
    def total_campus_students(self):
        return sum(student_count(a) for a in self.campus_allocs)

    @property
    def total_students(self):
        return self.total_main_students + self.total_campus_students

    @property
    def is_cross_campus(self):
        return bool(self.main_allocs) and bool(self.campus_allocs)

    @property
    def is_mergeable(self):
        if len(self.all_allocs) <= 1:
            return False
        lec_ids = {a.lecturer.id for a in self.all_allocs if getattr(a, "lecturer", None)}
        if len(lec_ids) != 1:
            return False
        total = sum(student_count(a) for a in self.all_allocs)
        return total <= MERGE_LIMIT

    @property
    def is_shareable(self):
        return self.total_main_students <= 60

    def is_scheduled(self):
        return self.scheduled_date is not None

    def assign_slot(self, date_obj, slot_start, slot_end):
        self.scheduled_date = date_obj
        self.scheduled_slot_start = slot_start
        self.scheduled_slot_end = slot_end

    def reset_slot(self):
        self.scheduled_date = self.scheduled_slot_start = self.scheduled_slot_end = None

    def is_pg(self):
        rep = (self.main_allocs or self.campus_allocs)
        return bool(rep) and is_postgraduate(rep[0].course_code)

    def is_evening_weekend(self):
        return all(getattr(a, 'is_evening_weekend', False) for a in self.all_allocs)

    def __repr__(self):
        return (f"<UCG {self.norm_code} main={len(self.main_allocs)} "
                f"campus={len(self.campus_allocs)} sched={self.scheduled_date}>")


def build_unified_course_groups(main_allocs, campus_allocs) -> Dict[str, UnifiedCourseGroup]:
    groups: Dict[str, UnifiedCourseGroup] = {}
    for a in main_allocs:
        code = normalize_code(a.course_code)
        if code not in groups:
            groups[code] = UnifiedCourseGroup(code)
        groups[code].main_allocs.append(a)
    for a in campus_allocs:
        code = normalize_code(a.course_code)
        if code not in groups:
            groups[code] = UnifiedCourseGroup(code)
        groups[code].campus_allocs.append(a)
    cross = sum(1 for g in groups.values() if g.is_cross_campus)
    safe_print(f"Built {len(groups)} unified course groups | cross-campus: {cross}")
    return groups


def _task_allocs(task):
    if isinstance(task, dict):
        return task.get('merged', [])
    return [task]


def _task_students(task):
    if isinstance(task, dict):
        return task.get('total_students', 0)
    return student_count(task)


def _representative(task):
    if isinstance(task, dict):
        return task['merged'][0]
    return task


def _task_is_pg(task):
    return is_postgraduate(_representative(task).course_code)


def _task_is_evening_weekend(task):
    return all(getattr(a, 'is_evening_weekend', False) for a in _task_allocs(task))


def merged_course_label(task) -> str:
    allocs = _task_allocs(task)
    if len(allocs) <= 1:
        return allocs[0].course_code if allocs else ""
    return "/".join(a.course_code for a in allocs)


# ─────────────────────────────────────────────────────────
# BUILD GLOBAL MERGED TASKS
# ─────────────────────────────────────────────────────────
def build_global_merged_tasks(allocs, merge_limit: int = 200):
    alloc_by_id = {a.id: a for a in allocs}
    tasks = []
    combined_alloc_ids: Set[int] = set()

    try:
        combined_groups = list(CombinedCourseGroup.objects.prefetch_related('allocations').all())
    except Exception as exc:
        safe_print(f"[CombinedCourseGroup] WARNING – could not load groups: {exc}")
        combined_groups = []

    for cg in combined_groups:
        group_allocs = [alloc_by_id[a.id] for a in cg.allocations.all() if a.id in alloc_by_id]
        if not group_allocs:
            continue

        total_students = sum(a.number_of_students or 0 for a in group_allocs)
        norm_code = normalize_code(cg.base_course_code)

        safe_print(f"[CombinedCourseGroup] '{cg.group_code}' ({norm_code}) "
                   f"x{len(group_allocs)} allocations, {total_students} students → ONE slot")

        if len(group_allocs) == 1:
            tasks.append(group_allocs[0])
        else:
            tasks.append({
                'merged': group_allocs,
                'total_students': total_students,
                'group_id': cg.id,
                'norm_code': norm_code,
                'combined_group': cg,
            })

        for a in group_allocs:
            combined_alloc_ids.add(a.id)

    remaining = [a for a in allocs if a.id not in combined_alloc_ids]

    grouped: Dict[Tuple, List] = defaultdict(list)
    for alloc in remaining:
        lect_id = alloc.lecturer.id if alloc.lecturer else None
        norm_code = normalize_code(alloc.course_code)
        grouped[(norm_code, lect_id)].append(alloc)

    for (norm_code, lect_id), group in grouped.items():
        if len(group) == 1:
            tasks.append(group[0])
            continue

        total_students = sum(a.number_of_students or 0 for a in group)

        if total_students <= merge_limit:
            names = set((a.course_name or "").strip().upper()[:30] for a in group)
            if len(names) > 1:
                safe_print(f"MERGE SKIPPED (different names): '{norm_code}' → individual")
                tasks.extend(group)
                continue

            safe_print(f"MERGE: '{norm_code}' x{len(group)} sections, "
                       f"combined={total_students}/{merge_limit} students → ONE slot")
            tasks.append({
                'merged': group,
                'total_students': total_students,
                'group_id': None,
                'norm_code': norm_code,
            })
        else:
            safe_print(f"MERGE SKIPPED (total {total_students} > {merge_limit}): "
                       f"'{norm_code}' x{len(group)} → individual")
            tasks.extend(group)

    return tasks


@retry_on_lock(max_retries=3, delay=0.2)
def _add_merged_course_mg(mg, alloc):
    mg.merged_courses.add(alloc)


def create_merged_group_db(task: dict) -> Optional[int]:
    allocs = task['merged']
    norm_code = task.get('norm_code', normalize_code(allocs[0].course_code))
    total = task['total_students']
    base = allocs[0]
    try:
        with transaction.atomic():
            mg = MergedCourseGroupTimetable.objects.create(
                base_course=base,
                merged_code=norm_code,
                total_students=total,
            )
        for alloc in allocs:
            _add_merged_course_mg(mg, alloc)
        return mg.id
    except Exception as e:
        safe_print(f"Error creating merged group for {norm_code}: {e}")
        return None


# ─────────────────────────────────────────────────────────
# CROSS-CAMPUS LECTURER DETECTION
# ─────────────────────────────────────────────────────────
def identify_cross_campus_lecturers(main_allocs, campus_allocs):
    main_map, campus_map = defaultdict(list), defaultdict(list)
    for a in main_allocs:
        if a.lecturer:
            main_map[a.lecturer.id].append(a)
    for a in campus_allocs:
        if a.lecturer:
            campus_map[a.lecturer.id].append(a)

    cross, main_only, campus_only = {}, {}, {}
    all_ids = set(main_map) | set(campus_map)
    for lid in all_ids:
        hm, hc = lid in main_map, lid in campus_map
        if hm and hc:
            cross[lid] = {"main": main_map[lid], "campus": campus_map[lid]}
        elif hm:
            main_only[lid] = main_map[lid]
        else:
            campus_only[lid] = campus_map[lid]
    return cross, main_only, campus_only


# ─────────────────────────────────────────────────────────
# MAIN CAMPUS CONFLICT TRACKER
# ─────────────────────────────────────────────────────────
class MainCampusTracker:
    def __init__(self):
        raw_venues = list(
            Venue.objects.filter(capacity__isnull=False, capacity__gt=0)
            .select_related("building__faculty")
        )
        self.venue_examcap: Dict[int, int] = {
            v.id: venue_exam_capacity(v) for v in raw_venues
        }
        self.venue_rawcap: Dict[int, int] = {v.id: (v.capacity or 0) for v in raw_venues}
        self.venues = sorted(raw_venues, key=lambda v: self.venue_examcap.get(v.id, 0))

        self.lecturer_busy: Dict[int, Set] = defaultdict(set)
        self.venue_usage: Dict[Tuple, int] = defaultdict(int)
        self.code_slot: Dict[str, Tuple] = {}
        self.lecturer_campus_day: Dict[int, Dict] = defaultdict(lambda: defaultdict(list))

        self.program_year_schedule: Dict[Tuple, Dict] = defaultdict(lambda: defaultdict(set))
        self.program_year_daily_count: Dict[Tuple, Dict] = defaultdict(lambda: defaultdict(int))
        self.program_year_slot_allocs: Dict[Tuple, List] = defaultdict(list)
        self.venue_schedule: Dict[int, Dict] = defaultdict(lambda: defaultdict(set))

        self.total_conflicts = 0
        self.collisions_detected = 0
        self.collisions_resolved = 0
        self._date_range: List[Tuple] = []
        self._slots: List[Tuple] = []

    def venue_remaining(self, vid, date_obj, slot, use_raw=False):
        cap = self.venue_rawcap.get(vid, 0) if use_raw else self.venue_examcap.get(vid, 0)
        return max(0, cap - self.venue_usage[(vid, date_obj, slot)])

    def total_venue_capacity(self, date_obj, slot, use_raw=False):
        return sum(self.venue_remaining(v.id, date_obj, slot, use_raw) for v in self.venues)

    def use_venue(self, vid, date_obj, slot, students):
        self.venue_usage[(vid, date_obj, slot)] += students
        self.venue_schedule[vid][date_obj].add(slot)

    def travel_ok(self, lid, date_obj, slot_start, slot_end, campus_type):
        if not lid:
            return True
        travel_m = TRAVEL_GAP_HOURS * 60
        ns, ne = slot_minutes(slot_start), slot_minutes(slot_end)
        for (etype, es, ee) in self.lecturer_campus_day[lid].get(date_obj, []):
            if etype == campus_type:
                continue
            esm, eem = slot_minutes(es), slot_minutes(ee)
            if ns >= eem:
                if ns - eem < travel_m:
                    return False
            elif ne <= esm:
                if esm - ne < travel_m:
                    return False
            else:
                return False
        return True

    def lecturer_ok(self, lid, date_obj, slot_start):
        return not lid or (date_obj, slot_start) not in self.lecturer_busy[lid]

    def has_venue_conflict(self, vid, date_obj, slot_start) -> bool:
        return slot_start in self.venue_schedule[vid][date_obj]

    def has_program_conflict(self, pid, year, date_obj, slot_start, new_alloc=None) -> bool:
        if not pid:
            return False
        if slot_start not in self.program_year_schedule[(pid, year)][date_obj]:
            return False
        if new_alloc is None:
            return True
        existing = self.program_year_slot_allocs.get((pid, year, date_obj, slot_start), [])
        for ea in existing:
            if not is_program_year_collision_exempt(new_alloc, ea):
                return True
        return False

    def can_schedule_program_year(self, pid, year, date_obj, max_per_day=2) -> bool:
        if not pid:
            return True
        return self.program_year_daily_count[(pid, year)][date_obj] < max_per_day

    def get_program_year_day_load(self, pid, year, date_obj) -> int:
        if not pid:
            return 0
        return self.program_year_daily_count[(pid, year)][date_obj]

    def alloc_ok(self, alloc: CourseAllocation, date_obj, slot_start, slot_end, relax_lecturer=False) -> bool:
        lid = alloc.lecturer.id if alloc.lecturer else None
        pid = alloc.program.id if alloc.program else None
        year = get_course_year(alloc.course_code) if alloc.course_code else 1
        if not relax_lecturer:
            if not self.lecturer_ok(lid, date_obj, slot_start):
                self.total_conflicts += 1
                return False
            if not self.travel_ok(lid, date_obj, slot_start, slot_end, "main"):
                self.total_conflicts += 1
                return False
        if self.has_program_conflict(pid, year, date_obj, slot_start, new_alloc=alloc):
            self.total_conflicts += 1
            return False
        return True

    def group_main_ok(self, allocs, date_obj, slot_start, slot_end, relax_lecturer=False) -> bool:
        return all(self.alloc_ok(a, date_obj, slot_start, slot_end, relax_lecturer) for a in allocs)

    def resolve_main_conflict(self, pid, year, date_obj, slot_start,
                              lid=None, new_alloc=None, date_range=None, slots=None):
        if date_range is None:
            date_range = self._date_range
        if slots is None:
            slots = self._slots

        self.collisions_detected += 1
        all_slot_starts = [s for s, e in slots]
        n_slots = len(all_slot_starts)
        cur_slot_idx = (all_slot_starts.index(slot_start) if slot_start in all_slot_starts else 0)

        for offset in [1, -1, 2, -2, 3, -3]:
            new_idx = cur_slot_idx + offset
            if 0 <= new_idx < n_slots:
                ns = all_slot_starts[new_idx]
                ne = slots[new_idx][1]
                if (not self.has_program_conflict(pid, year, date_obj, ns, new_alloc=new_alloc) and
                        (not lid or self.lecturer_ok(lid, date_obj, ns)) and
                        (not lid or self.travel_ok(lid, date_obj, ns, ne, "main")) and
                        self.can_schedule_program_year(pid, year, date_obj)):
                    self.collisions_resolved += 1
                    return True, date_obj, ns

        cur_date_idx = next((i for i, (d, _) in enumerate(date_range) if d == date_obj), 0)
        for offset_r in range(1, len(date_range)):
            for direction in [1, -1]:
                ni = (cur_date_idx + offset_r * direction) % len(date_range)
                nd = date_range[ni][0]
                ne_slot = slots[cur_slot_idx][1]
                if (not self.has_program_conflict(pid, year, nd, slot_start, new_alloc=new_alloc) and
                        (not lid or self.lecturer_ok(lid, nd, slot_start)) and
                        (not lid or self.travel_ok(lid, nd, slot_start, ne_slot, "main")) and
                        self.can_schedule_program_year(pid, year, nd)):
                    self.collisions_resolved += 1
                    return True, nd, slot_start

        sorted_dates = sorted(date_range, key=lambda x: self.get_program_year_day_load(pid, year, x[0]))
        for td, _ in sorted_dates:
            for tidx, (ts, te) in enumerate(slots):
                if td == date_obj and ts == slot_start:
                    continue
                if (not self.has_program_conflict(pid, year, td, ts, new_alloc=new_alloc) and
                        (not lid or self.lecturer_ok(lid, td, ts)) and
                        (not lid or self.travel_ok(lid, td, ts, te, "main")) and
                        self.can_schedule_program_year(pid, year, td)):
                    self.collisions_resolved += 1
                    return True, td, ts

        return False, date_obj, slot_start

    def mark_main_alloc(self, alloc: CourseAllocation, date_obj, slot_start, slot_end):
        lid = alloc.lecturer.id if alloc.lecturer else None
        pid = alloc.program.id if alloc.program else None
        year = get_course_year(alloc.course_code) if alloc.course_code else 1
        if lid:
            self.lecturer_busy[lid].add((date_obj, slot_start))
            self.lecturer_campus_day[lid][date_obj].append(("main", slot_start, slot_end))
        if pid:
            key = (pid, year, date_obj, slot_start)
            prev_len = len(self.program_year_slot_allocs[key])
            self.program_year_schedule[(pid, year)][date_obj].add(slot_start)
            self.program_year_slot_allocs[key].append(alloc)
            if prev_len == 0:
                self.program_year_daily_count[(pid, year)][date_obj] += 1

    def mark_main_group(self, allocs: List[CourseAllocation], date_obj, slot_start, slot_end):
        seen_py: Set[Tuple] = set()
        for alloc in allocs:
            lid = alloc.lecturer.id if alloc.lecturer else None
            pid = alloc.program.id if alloc.program else None
            year = get_course_year(alloc.course_code) if alloc.course_code else 1
            if lid:
                self.lecturer_busy[lid].add((date_obj, slot_start))
                self.lecturer_campus_day[lid][date_obj].append(("main", slot_start, slot_end))
            if pid:
                py_key = (pid, year, date_obj, slot_start)
                self.program_year_schedule[(pid, year)][date_obj].add(slot_start)
                self.program_year_slot_allocs[py_key].append(alloc)
                if py_key not in seen_py:
                    self.program_year_daily_count[(pid, year)][date_obj] += 1
                    seen_py.add(py_key)

    def mark_cross_campus_lecturer(self, lid, date_obj, slot_start, slot_end):
        if lid:
            self.lecturer_campus_day[lid][date_obj].append(("campus", slot_start, slot_end))

    def has_cross_campus_travel_conflict(self, lid, date_obj, slot_start, slot_end):
        return not self.travel_ok(lid, date_obj, slot_start, slot_end, "campus")

    def get_pinned_slot(self, norm_code):
        return self.code_slot.get(norm_code)

    def pin_slot(self, norm_code, date_obj, slot_start, slot_end):
        if norm_code not in self.code_slot:
            self.code_slot[norm_code] = (date_obj, slot_start, slot_end)

    def force_pin_slot(self, norm_code, date_obj, slot_start, slot_end):
        self.code_slot[norm_code] = (date_obj, slot_start, slot_end)

    def find_best_venue(self, students, date_obj, slot_start, faculty_venues=None,
                        prefer_occupied=False, use_raw=False):
        pool = faculty_venues if faculty_venues else self.venues
        for v in pool:
            rem = self.venue_remaining(v.id, date_obj, slot_start, use_raw)
            if rem >= students:
                if prefer_occupied and self.venue_usage[(v.id, date_obj, slot_start)] > 0:
                    return v
        for v in pool:
            if self.venue_remaining(v.id, date_obj, slot_start, use_raw) >= students:
                return v
        if faculty_venues and faculty_venues is not self.venues:
            return self.find_best_venue(students, date_obj, slot_start, None, prefer_occupied, use_raw)
        return None

    def find_combined(self, students, date_obj, slot, use_raw=False):
        avail = sorted(
            [(v, self.venue_remaining(v.id, date_obj, slot, use_raw))
             for v in self.venues
             if self.venue_remaining(v.id, date_obj, slot, use_raw) > 0],
            key=lambda x: x[1], reverse=True)
        chosen, total = [], 0
        for v, cap in avail:
            chosen.append(v)
            total += cap
            if total >= students:
                return chosen
        return []

    def get_faculty_venues(self, alloc) -> List[Venue]:
        faculty = None
        try:
            if alloc.program and alloc.program.department:
                faculty = alloc.program.department.faculty
        except Exception:
            pass
        if not faculty:
            try:
                if alloc.lecturer and alloc.lecturer.department:
                    faculty = alloc.lecturer.department.faculty
            except Exception:
                pass
        if not faculty:
            return self.venues
        result = [v for v in self.venues if v.building and v.building.faculty == faculty]
        return result if result else self.venues

    def get_collision_stats(self):
        return {
            "detected": self.collisions_detected,
            "resolved": self.collisions_resolved,
            "unresolved": max(0, self.collisions_detected - self.collisions_resolved),
            "resolution_rate": (
                self.collisions_resolved / self.collisions_detected * 100
                if self.collisions_detected > 0 else 100.0
            ),
        }

    def stats(self):
        cs = self.get_collision_stats()
        cs["detected"] = max(cs["detected"], self.total_conflicts)
        return cs


# ─────────────────────────────────────────────────────────
# CAMPUS CONFLICT TRACKER
# ─────────────────────────────────────────────────────────
class CampusTracker:
    def __init__(self):
        self.lecturer_busy: Dict[int, Dict[int, Set]] = defaultdict(lambda: defaultdict(set))
        self.program_year_schedule: Dict[Tuple, Dict] = defaultdict(lambda: defaultdict(set))
        self.program_year_daily_count: Dict[Tuple, Dict] = defaultdict(lambda: defaultdict(int))
        self.program_year_slot_allocs: Dict[Tuple, List] = defaultdict(list)
        self.total_conflicts = 0

    def has_program_conflict(self, campus_id, pid, year, date_obj, slot_start, new_alloc=None) -> bool:
        if not pid:
            return False
        key = (campus_id, pid, year)
        if slot_start not in self.program_year_schedule[key][date_obj]:
            return False
        if new_alloc is None:
            return True
        existing = self.program_year_slot_allocs.get((campus_id, pid, year, date_obj, slot_start), [])
        for ea in existing:
            if not is_program_year_collision_exempt(new_alloc, ea):
                return True
        return False

    def can_schedule_program_year(self, campus_id, pid, year, date_obj, max_per_day=2) -> bool:
        if not pid:
            return True
        return self.program_year_daily_count[(campus_id, pid, year)][date_obj] < max_per_day

    def get_program_year_day_load(self, campus_id, pid, year, date_obj) -> int:
        if not pid:
            return 0
        return self.program_year_daily_count[(campus_id, pid, year)][date_obj]

    def alloc_ok(self, alloc: CampusCourseAllocation,
                 date_obj, slot_start, slot_end,
                 main_tracker: MainCampusTracker,
                 cross_campus_lids: Set[int],
                 relax_lecturer=False) -> bool:
        campus_id = alloc.campus.id if alloc.campus else 0
        lid = alloc.lecturer.id if alloc.lecturer else None
        pid = alloc.program.id if alloc.program else None
        year = get_course_year(alloc.course_code) if alloc.course_code else 1

        if not relax_lecturer and lid:
            if (date_obj, slot_start) in self.lecturer_busy[campus_id][lid]:
                self.total_conflicts += 1
                return False
            if lid in cross_campus_lids:
                if main_tracker.has_cross_campus_travel_conflict(lid, date_obj, slot_start, slot_end):
                    self.total_conflicts += 1
                    return False

        if self.has_program_conflict(campus_id, pid, year, date_obj, slot_start, new_alloc=alloc):
            self.total_conflicts += 1
            return False
        return True

    def group_campus_ok(self, allocs, date_obj, slot_start, slot_end,
                        main_tracker, cross_campus_lids, relax_lecturer=False) -> bool:
        return all(self.alloc_ok(a, date_obj, slot_start, slot_end,
                                  main_tracker, cross_campus_lids, relax_lecturer)
                   for a in allocs)

    def mark_alloc(self, alloc: CampusCourseAllocation,
                   date_obj, slot_start, slot_end,
                   main_tracker: MainCampusTracker,
                   cross_campus_lids: Set[int]):
        campus_id = alloc.campus.id if alloc.campus else 0
        lid = alloc.lecturer.id if alloc.lecturer else None
        pid = alloc.program.id if alloc.program else None
        year = get_course_year(alloc.course_code) if alloc.course_code else 1
        if lid:
            self.lecturer_busy[campus_id][lid].add((date_obj, slot_start))
            if lid in cross_campus_lids:
                main_tracker.mark_cross_campus_lecturer(lid, date_obj, slot_start, slot_end)
        if pid:
            key = (campus_id, pid, year)
            slot_key = (campus_id, pid, year, date_obj, slot_start)
            self.program_year_schedule[key][date_obj].add(slot_start)
            self.program_year_slot_allocs[slot_key].append(alloc)
            if len(self.program_year_slot_allocs[slot_key]) == 1:
                self.program_year_daily_count[key][date_obj] += 1

    def mark_group(self, allocs, date_obj, slot_start, slot_end,
                   main_tracker, cross_campus_lids):
        seen_py: Set[Tuple] = set()
        for alloc in allocs:
            campus_id = alloc.campus.id if alloc.campus else 0
            lid = alloc.lecturer.id if alloc.lecturer else None
            pid = alloc.program.id if alloc.program else None
            year = get_course_year(alloc.course_code) if alloc.course_code else 1
            if lid:
                self.lecturer_busy[campus_id][lid].add((date_obj, slot_start))
                if lid in cross_campus_lids:
                    main_tracker.mark_cross_campus_lecturer(lid, date_obj, slot_start, slot_end)
            if pid:
                key = (campus_id, pid, year)
                slot_key = (campus_id, pid, year, date_obj, slot_start)
                self.program_year_schedule[key][date_obj].add(slot_start)
                self.program_year_slot_allocs[slot_key].append(alloc)
                if slot_key not in seen_py:
                    self.program_year_daily_count[key][date_obj] += 1
                    seen_py.add(slot_key)


# ─────────────────────────────────────────────────────────
# PLACEMENT PRIMITIVES (WITH SLOT LOCKS)
# ─────────────────────────────────────────────────────────
def _write_main_entries(allocs, venue, date_obj, slot_start, slot_end,
                        main_tracker: MainCampusTracker,
                        scheduled_ids: Set[int]) -> int:
    cache = get_dual_cache()
    day_str = date_obj.strftime("%A")

    # Use slot lock to prevent concurrent writes to same venue+slot
    lock = _get_slot_lock(venue.id, day_str, slot_start, slot_end)
    with lock:
        # Double-check cache after acquiring lock
        if cache.is_duplicate_main(venue.id, day_str, slot_start, slot_end):
            return 0

        to_write = [a for a in allocs if a.id not in scheduled_ids]
        if not to_write:
            return 0

        # Check if any allocation is already scheduled
        to_write = [a for a in to_write if not TempTimetable.objects.filter(course_allocation=a).exists()]
        if not to_write:
            return 0

        entries = [
            TempTimetable(
                course_allocation=a,
                venue=venue,
                day=day_str,
                start_time=slot_start,
                end_time=slot_end,
            )
            for a in to_write
        ]

        written = safe_bulk_create_main_entries(entries, cache)
        if written == 0:
            return 0

        main_tracker.use_venue(venue.id, date_obj, slot_start,
                               sum(student_count(a) for a in to_write))
        main_tracker.mark_main_group(to_write, date_obj, slot_start, slot_end)
        for a in to_write:
            scheduled_ids.add(a.id)
            cache.mark_course_scheduled(a.course_code,
                                        a.lecturer.id if a.lecturer else None,
                                        a.program.id if a.program else None)
        return written


def _write_campus_entries(allocs, date_obj, slot_start, slot_end,
                          campus_tracker: CampusTracker,
                          main_tracker: MainCampusTracker,
                          cross_campus_lids: Set[int],
                          scheduled_ids: Set[int]) -> int:
    cache = get_dual_cache()
    day_str = date_obj.strftime("%A")

    to_write = [a for a in allocs if a.id not in scheduled_ids]
    if not to_write:
        return 0

    campus_id = to_write[0].campus.id if to_write[0].campus else 0

    # Use slot lock to prevent concurrent writes to same campus+slot
    lock = _get_slot_lock(campus_id, day_str, slot_start, slot_end)
    with lock:
        if cache.is_duplicate_campus(campus_id, day_str, slot_start, slot_end):
            return 0

        # Check if any allocation is already scheduled
        to_write = [a for a in to_write if not CampusTempTimetable.objects.filter(course_allocation=a).exists()]
        if not to_write:
            return 0

        entries = [
            CampusTempTimetable(
                course_allocation=a,
                campus=a.campus,
                day=day_str,
                start_time=slot_start,
                end_time=slot_end,
            )
            for a in to_write
        ]

        written = safe_bulk_create_campus_entries(entries, cache)
        if written == 0:
            return 0

        campus_tracker.mark_group(to_write, date_obj, slot_start, slot_end,
                                  main_tracker, cross_campus_lids)
        for a in to_write:
            scheduled_ids.add(a.id)
            cache.mark_course_scheduled(a.course_code,
                                        a.lecturer.id if a.lecturer else None,
                                        a.program.id if a.program else None)
        return written


def place_main_group(allocs: List[CourseAllocation],
                     date_obj, slot_start, slot_end,
                     main_tracker: MainCampusTracker,
                     scheduled_ids: Set[int],
                     prefer_occupied=False,
                     use_raw=False) -> Tuple[int, int]:
    placed = failed = 0

    for alloc in allocs:
        if alloc.id in scheduled_ids:
            continue

        students = student_count(alloc)
        venue = main_tracker.find_best_venue(
            students, date_obj, slot_start,
            faculty_venues=main_tracker.get_faculty_venues(alloc),
            prefer_occupied=prefer_occupied,
            use_raw=use_raw)

        if venue:
            if _write_main_entries([alloc], venue, date_obj, slot_start, slot_end,
                                   main_tracker, scheduled_ids):
                placed += 1
                continue

        rooms = main_tracker.find_combined(students, date_obj, slot_start, use_raw)
        if not rooms:
            failed += 1
            continue

        remaining = students
        ok = False
        for room in rooms:
            chunk = min(main_tracker.venue_remaining(room.id, date_obj, slot_start, use_raw), remaining)
            if chunk <= 0:
                continue
            if _write_main_entries([alloc], room, date_obj, slot_start, slot_end,
                                   main_tracker, scheduled_ids):
                remaining -= chunk
                if remaining <= 0:
                    ok = True
                    break

        if ok:
            placed += 1
        else:
            failed += 1

    return placed, failed


def place_campus_group(allocs: List[CampusCourseAllocation],
                       date_obj, slot_start, slot_end,
                       campus_tracker: CampusTracker,
                       main_tracker: MainCampusTracker,
                       cross_campus_lids: Set[int],
                       scheduled_ids: Set[int],
                       relax_lecturer=False) -> Tuple[int, int]:
    placed = failed = 0

    for alloc in allocs:
        if alloc.id in scheduled_ids:
            continue

        if not campus_tracker.alloc_ok(alloc, date_obj, slot_start, slot_end,
                                        main_tracker, cross_campus_lids, relax_lecturer):
            failed += 1
            continue

        written = _write_campus_entries([alloc], date_obj, slot_start, slot_end,
                                         campus_tracker, main_tracker,
                                         cross_campus_lids, scheduled_ids)
        if written:
            placed += 1
        else:
            failed += 1

    return placed, failed


# ─────────────────────────────────────────────────────────
# SLOT FINDERS
# ─────────────────────────────────────────────────────────
def _slot_order(slots, allocs):
    prefer_aft = any(is_afternoon_course(a.course_code) for a in allocs)
    morning = [(s, e) for s, e in slots if s < dtime(12, 0)]
    afternoon = [(s, e) for s, e in slots if s >= dtime(12, 0)]
    return (afternoon + morning) if prefer_aft else (morning + afternoon)


def _pg_slot_order(slots) -> List[Tuple[dtime, dtime]]:
    return list(reversed(slots))


def find_slot_for_main_group(allocs: List[CourseAllocation],
                             norm_code: str,
                             date_range, slots,
                             main_tracker: MainCampusTracker,
                             relax_lecturer=False) -> Optional[Tuple]:
    pin = main_tracker.get_pinned_slot(norm_code)
    if pin:
        dt, ss, se = pin
        if main_tracker.group_main_ok(allocs, dt, ss, se, relax_lecturer):
            return dt, ss, se
        if not relax_lecturer and main_tracker.group_main_ok(allocs, dt, ss, se, True):
            return dt, ss, se

    slot_ord = _slot_order(slots, allocs)
    total_st = sum(student_count(a) for a in allocs)

    for date_obj, _ in sorted(date_range, key=lambda d: main_tracker.get_program_year_day_load(
            allocs[0].program.id if allocs[0].program else None,
            get_course_year(allocs[0].course_code) if allocs[0].course_code else 1, d[0])):
        for ss, se in slot_ord:
            if main_tracker.total_venue_capacity(date_obj, ss) < total_st:
                continue
            if main_tracker.group_main_ok(allocs, date_obj, ss, se, relax_lecturer):
                return date_obj, ss, se
    return None


def find_slot_for_campus_group(allocs: List[CampusCourseAllocation],
                               norm_code: str,
                               date_range, slots,
                               main_tracker: MainCampusTracker,
                               campus_tracker: CampusTracker,
                               cross_campus_lids: Set[int],
                               relax_lecturer=False) -> Optional[Tuple]:
    pin = main_tracker.get_pinned_slot(norm_code)
    if pin:
        dt, ss, se = pin
        if campus_tracker.group_campus_ok(allocs, dt, ss, se,
                                           main_tracker, cross_campus_lids, relax_lecturer):
            return dt, ss, se
        if not relax_lecturer and campus_tracker.group_campus_ok(
                allocs, dt, ss, se, main_tracker, cross_campus_lids, True):
            return dt, ss, se

    slot_ord = _slot_order(slots, allocs)
    for date_obj, _ in sorted(date_range, key=lambda d: d[0]):
        for ss, se in slot_ord:
            if campus_tracker.group_campus_ok(allocs, date_obj, ss, se,
                                               main_tracker, cross_campus_lids, relax_lecturer):
                return date_obj, ss, se
    return None


def find_slot_for_pg_main(allocs, norm_code, date_range, slots,
                          main_tracker, free_fill=False) -> Optional[Tuple]:
    pg_slots = _pg_slot_order(slots)
    total_st = sum(student_count(a) for a in allocs)
    for date_obj, _ in sorted(date_range, key=lambda d: d[0]):
        for ss, se in pg_slots:
            if main_tracker.total_venue_capacity(date_obj, ss) < total_st:
                continue
            if main_tracker.group_main_ok(allocs, date_obj, ss, se, relax_lecturer=True):
                return date_obj, ss, se
    if free_fill:
        for date_obj, _ in sorted(date_range, key=lambda d: d[0]):
            for ss, se in slots:
                if main_tracker.total_venue_capacity(date_obj, ss) < total_st:
                    continue
                if main_tracker.group_main_ok(allocs, date_obj, ss, se, relax_lecturer=True):
                    return date_obj, ss, se
    return None


def find_slot_for_pg_campus(allocs, norm_code, date_range, slots,
                            main_tracker, campus_tracker, cross_campus_lids,
                            free_fill=False) -> Optional[Tuple]:
    pg_slots = _pg_slot_order(slots)
    for date_obj, _ in sorted(date_range, key=lambda d: d[0]):
        for ss, se in pg_slots:
            if campus_tracker.group_campus_ok(allocs, date_obj, ss, se,
                                               main_tracker, cross_campus_lids, True):
                return date_obj, ss, se
    if free_fill:
        for date_obj, _ in sorted(date_range, key=lambda d: d[0]):
            for ss, se in slots:
                if campus_tracker.group_campus_ok(allocs, date_obj, ss, se,
                                                   main_tracker, cross_campus_lids, True):
                    return date_obj, ss, se
    return None


# ─────────────────────────────────────────────────────────
# BATCH PROCESSING FUNCTIONS
# ─────────────────────────────────────────────────────────
def process_main_ug_batch(
        ug_tasks: List,
        date_range, slots,
        main_tracker: MainCampusTracker,
        scheduled_main_ids: Set[int],
        scheduled_list: List[str],
        unscheduled_list: List[str],
) -> Tuple[int, List]:
    placed = 0
    still = []
    cache = get_dual_cache()

    sorted_tasks = sorted(ug_tasks, key=lambda t: -_task_students(t))
    lecturer_day_load: Dict[int, Dict[date, int]] = defaultdict(lambda: defaultdict(int))

    for task in sorted_tasks:
        allocs = [a for a in _task_allocs(task) if a.id not in scheduled_main_ids]
        if not allocs:
            continue

        rep = allocs[0]
        lid = rep.lecturer.id if rep.lecturer else None
        pid = rep.program.id if rep.program else None
        year = get_course_year(rep.course_code) if rep.course_code else 1
        total_st = sum(student_count(a) for a in allocs)
        label = merged_course_label(task)

        fac_venues = main_tracker.get_faculty_venues(rep)
        if not fac_venues:
            still.append(task)
            unscheduled_list.append(f"{label} [MAIN] – no faculty venues")
            continue

        assigned = False
        for date_obj, _ in sorted(date_range, key=lambda d: lecturer_day_load[lid].get(d[0], 0) if lid else 0):
            if assigned:
                break
            if lid and lecturer_day_load[lid][date_obj] >= 4:
                continue
            if not main_tracker.can_schedule_program_year(pid, year, date_obj):
                continue
            for ss, se in slots:
                if assigned:
                    break
                pin = main_tracker.get_pinned_slot(task.get('norm_code', ''))
                if pin and pin != (date_obj, ss, se):
                    continue

                if main_tracker.has_program_conflict(pid, year, date_obj, ss, new_alloc=rep):
                    resolved, date_obj, ss = main_tracker.resolve_main_conflict(
                        pid, year, date_obj, ss, lid=lid, new_alloc=rep,
                        date_range=date_range, slots=slots)
                    se = next((e for s, e in slots if s == ss), se)
                    if not resolved:
                        continue

                if lid and not main_tracker.lecturer_ok(lid, date_obj, ss):
                    continue

                alloc_ok = True
                for a in allocs:
                    a_lid = a.lecturer.id if a.lecturer else None
                    a_pid = a.program.id if a.program else None
                    a_year = get_course_year(a.course_code) if a.course_code else 1
                    if a_lid and not main_tracker.lecturer_ok(a_lid, date_obj, ss):
                        alloc_ok = False
                        break
                    if main_tracker.has_program_conflict(a_pid, a_year, date_obj, ss, new_alloc=a):
                        alloc_ok = False
                        break
                if not alloc_ok:
                    continue

                venue = main_tracker.find_best_venue(total_st, date_obj, ss, fac_venues)
                if not venue:
                    continue

                day_str = date_obj.strftime("%A")
                if cache.is_duplicate_main(venue.id, day_str, ss, se):
                    continue

                written = _write_main_entries(allocs, venue, date_obj, ss, se,
                                               main_tracker, scheduled_main_ids)
                if written:
                    if lid:
                        lecturer_day_load[lid][date_obj] += 1
                    main_tracker.pin_slot(task.get('norm_code', ''), date_obj, ss, se)
                    placed += written
                    scheduled_list.append(
                        f"{label} [MAIN-BATCH {venue.code}→{date_obj} {ss.strftime('%H:%M')}]")
                    assigned = True

        if not assigned:
            still.append(task)
            unscheduled_list.append(f"{label} [MAIN] – no slot (batch)")

    return placed, still


def process_main_ug_fallback(
        ug_tasks: List,
        date_range, slots,
        main_tracker: MainCampusTracker,
        scheduled_main_ids: Set[int],
        scheduled_list: List[str],
        unscheduled_list: List[str],
) -> Tuple[int, List]:
    placed = 0
    still = []
    cache = get_dual_cache()

    sorted_tasks = sorted(ug_tasks, key=lambda t: -_task_students(t))

    for task in sorted_tasks:
        allocs = [a for a in _task_allocs(task) if a.id not in scheduled_main_ids]
        if not allocs:
            continue

        rep = allocs[0]
        lid = rep.lecturer.id if rep.lecturer else None
        pid = rep.program.id if rep.program else None
        year = get_course_year(rep.course_code) if rep.course_code else 1
        total_st = sum(student_count(a) for a in allocs)
        label = merged_course_label(task)

        assigned = False
        for date_obj, _ in sorted(date_range, key=lambda d: main_tracker.get_program_year_day_load(pid, year, d[0])):
            if assigned:
                break
            for ss, se in slots:
                if assigned:
                    break
                if main_tracker.has_program_conflict(pid, year, date_obj, ss, new_alloc=rep):
                    resolved, date_obj, ss = main_tracker.resolve_main_conflict(
                        pid, year, date_obj, ss, lid=lid, new_alloc=rep,
                        date_range=date_range, slots=slots)
                    se = next((e for s, e in slots if s == ss), se)
                    if not resolved:
                        continue

                if lid and not main_tracker.lecturer_ok(lid, date_obj, ss):
                    continue

                alloc_ok = True
                for a in allocs:
                    a_lid = a.lecturer.id if a.lecturer else None
                    a_pid = a.program.id if a.program else None
                    a_year = get_course_year(a.course_code) if a.course_code else 1
                    if a_lid and not main_tracker.lecturer_ok(a_lid, date_obj, ss):
                        alloc_ok = False
                        break
                    if main_tracker.has_program_conflict(a_pid, a_year, date_obj, ss, new_alloc=a):
                        alloc_ok = False
                        break
                if not alloc_ok:
                    continue

                venue = main_tracker.find_best_venue(total_st, date_obj, ss)
                if not venue:
                    continue

                day_str = date_obj.strftime("%A")
                if cache.is_duplicate_main(venue.id, day_str, ss, se):
                    continue

                written = _write_main_entries(allocs, venue, date_obj, ss, se,
                                               main_tracker, scheduled_main_ids)
                if written:
                    main_tracker.pin_slot(task.get('norm_code', ''), date_obj, ss, se)
                    placed += written
                    scheduled_list.append(
                        f"{label} [MAIN-FALLBACK {venue.code}→{date_obj} {ss.strftime('%H:%M')}]")
                    assigned = True

        if not assigned:
            still.append(task)
            unscheduled_list.append(f"{label} [MAIN] – no slot (fallback)")

    return placed, still


def process_main_ug_compression(
        ug_tasks: List,
        date_range, slots,
        main_tracker: MainCampusTracker,
        scheduled_main_ids: Set[int],
        scheduled_list: List[str],
        unscheduled_list: List[str],
) -> Tuple[int, List]:
    placed = 0
    still = []
    cache = get_dual_cache()

    def conflict_density(task):
        allocs = _task_allocs(task)
        if not allocs:
            return 0
        rep = allocs[0]
        if not rep.program:
            return 0
        pid = rep.program.id
        year = get_course_year(rep.course_code) if rep.course_code else 1
        return sum(main_tracker.get_program_year_day_load(pid, year, d) for d, _ in date_range)

    sorted_tasks = sorted(ug_tasks, key=conflict_density, reverse=True)

    for task in sorted_tasks:
        allocs = [a for a in _task_allocs(task) if a.id not in scheduled_main_ids]
        if not allocs:
            continue

        rep = allocs[0]
        lid = rep.lecturer.id if rep.lecturer else None
        pid = rep.program.id if rep.program else None
        year = get_course_year(rep.course_code) if rep.course_code else 1
        total_st = sum(student_count(a) for a in allocs)
        label = merged_course_label(task)

        assigned = False
        for date_obj, _ in date_range:
            if assigned:
                break
            for ss, se in slots:
                if assigned:
                    break
                if main_tracker.has_program_conflict(pid, year, date_obj, ss, new_alloc=rep):
                    resolved, date_obj, ss = main_tracker.resolve_main_conflict(
                        pid, year, date_obj, ss, lid=lid, new_alloc=rep,
                        date_range=date_range, slots=slots)
                    se = next((e for s, e in slots if s == ss), se)
                    if not resolved:
                        continue

                if lid and not main_tracker.lecturer_ok(lid, date_obj, ss):
                    continue

                alloc_ok = True
                for a in allocs:
                    a_lid = a.lecturer.id if a.lecturer else None
                    a_pid = a.program.id if a.program else None
                    a_year = get_course_year(a.course_code) if a.course_code else 1
                    if a_lid and not main_tracker.lecturer_ok(a_lid, date_obj, ss):
                        alloc_ok = False
                        break
                    if main_tracker.has_program_conflict(a_pid, a_year, date_obj, ss, new_alloc=a):
                        alloc_ok = False
                        break
                if not alloc_ok:
                    continue

                venue = main_tracker.find_best_venue(total_st, date_obj, ss)
                if not venue:
                    continue

                day_str = date_obj.strftime("%A")
                if cache.is_duplicate_main(venue.id, day_str, ss, se):
                    continue

                written = _write_main_entries(allocs, venue, date_obj, ss, se,
                                               main_tracker, scheduled_main_ids)
                if written:
                    main_tracker.pin_slot(task.get('norm_code', ''), date_obj, ss, se)
                    placed += written
                    scheduled_list.append(
                        f"{label} [MAIN-COMPRESS {venue.code}→{date_obj} {ss.strftime('%H:%M')}]")
                    assigned = True

        if not assigned:
            still.append(task)
            unscheduled_list.append(f"{label} [MAIN] – no slot (compression)")

    return placed, still


def process_campus_compression(
        campus_tasks: List,
        date_range, slots,
        main_tracker: MainCampusTracker,
        campus_tracker: CampusTracker,
        cross_campus_lids: Set[int],
        scheduled_campus_ids: Set[int],
        scheduled_list: List[str],
        unscheduled_list: List[str],
) -> Tuple[int, List]:
    placed = 0
    still = []

    def conflict_density(task):
        allocs = _task_allocs(task)
        if not allocs:
            return 0
        rep = allocs[0]
        if not rep.program or not rep.campus:
            return 0
        cid = rep.campus.id
        pid = rep.program.id
        year = get_course_year(rep.course_code) if rep.course_code else 1
        return sum(campus_tracker.get_program_year_day_load(cid, pid, year, d) for d, _ in date_range)

    sorted_tasks = sorted(campus_tasks, key=conflict_density, reverse=True)

    for task in sorted_tasks:
        allocs = [a for a in _task_allocs(task) if a.id not in scheduled_campus_ids]
        if not allocs:
            continue

        slot = find_slot_for_campus_group(
            allocs, task.get('norm_code', ''), date_range, slots,
            main_tracker, campus_tracker, cross_campus_lids, relax_lecturer=True)
        if slot:
            date_obj, ss, se = slot
            task['scheduled_date'] = date_obj
            main_tracker.pin_slot(task.get('norm_code', ''), date_obj, ss, se)
            pc, _ = place_campus_group(
                allocs, date_obj, ss, se,
                campus_tracker, main_tracker, cross_campus_lids,
                scheduled_campus_ids, relax_lecturer=True)
            placed += pc
            for a in allocs:
                if a.id in scheduled_campus_ids:
                    c = a.campus.code if a.campus else "?"
                    scheduled_list.append(
                        f"{a.course_code} [{c}-COMPRESS→{date_obj} {ss.strftime('%H:%M')}]")
        else:
            still.append(task)
            for a in allocs:
                c = a.campus.code if a.campus else "?"
                unscheduled_list.append(f"{a.course_code} [{c} – no slot (compression)]")

    return placed, still


# ─────────────────────────────────────────────────────────
# EVENING/WEEKEND OVERFLOW PASS
# ─────────────────────────────────────────────────────────
def process_evening_weekend_dual(
    tasks: List,
    main_tracker: MainCampusTracker,
    campus_tracker: CampusTracker,
    cross_campus_lids: Set[int],
    scheduled_main_ids: Set[int],
    scheduled_campus_ids: Set[int],
    date_range, slots,
    config,
    scheduled_list: List[str],
    unscheduled_list: List[str],
) -> Tuple[int, List]:
    enable_evening = getattr(config, 'enable_evening_classes', False)
    enable_weekend = getattr(config, 'enable_weekend_classes', False)

    if not enable_evening and not enable_weekend:
        safe_print("[EveningWeekend] Both passes disabled — skipping.")
        return 0, tasks

    overflow_windows: List[tuple] = []
    regular_days = getattr(config, 'days', None) or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    if enable_evening:
        ev_start = getattr(config, 'evening_start_time', dtime(19, 0))
        ev_end = getattr(config, 'evening_end_time', dtime(21, 0))
        ev_count = int(getattr(config, 'evening_slot_count', 1))
        slot_size = int(getattr(config, 'slot_size', 3))
        ev_slots = generate_slots(ev_start, ev_end, slot_size)[:ev_count]
        for day in regular_days:
            for s, e in ev_slots:
                overflow_windows.append((day, s, e))

    if enable_weekend:
        wk_start = getattr(config, 'weekend_start_time', dtime(9, 0))
        wk_end = getattr(config, 'weekend_end_time', dtime(17, 0))
        wk_slot_size = int(getattr(config, 'weekend_slot_size', 3))
        wk_slots = generate_slots(wk_start, wk_end, wk_slot_size)
        for day in ["Saturday"]:
            for s, e in wk_slots:
                overflow_windows.append((day, s, e))

    if not overflow_windows:
        return 0, tasks

    placed = 0
    still = []
    cache = get_dual_cache()

    for task in tasks:
        main_allocs = [a for a in task.main_allocs if a.id not in scheduled_main_ids]
        campus_allocs = [a for a in task.campus_allocs if a.id not in scheduled_campus_ids]
        if not main_allocs and not campus_allocs:
            continue

        assigned = False
        for day, start, end in overflow_windows:
            if assigned:
                break

            if main_allocs:
                rep = main_allocs[0]
                lid = rep.lecturer.id if rep.lecturer else None
                pid = rep.program.id if rep.program else None
                year = get_course_year(rep.course_code) if rep.course_code else 1
                total_st = sum(student_count(a) for a in main_allocs)

                has_conflict = False
                for a in main_allocs:
                    a_lid = a.lecturer.id if a.lecturer else None
                    a_pid = a.program.id if a.program else None
                    a_year = get_course_year(a.course_code) if a.course_code else 1
                    if a_lid and not main_tracker.lecturer_ok(a_lid, date_obj, start):
                        has_conflict = True
                        break
                    if main_tracker.has_program_conflict(a_pid, a_year, date_obj, start, new_alloc=a):
                        has_conflict = True
                        break
                if has_conflict:
                    continue

                venue = main_tracker.find_best_venue(total_st, date_obj, start)
                if not venue:
                    continue

                day_str = date_obj.strftime("%A")
                if cache.is_duplicate_main(venue.id, day_str, start, end):
                    continue

                written = _write_main_entries(main_allocs, venue, date_obj, start, end,
                                               main_tracker, scheduled_main_ids)
                if written:
                    placed += written
                    scheduled_list.append(
                        f"{task.norm_code} [MAIN-EVENING {venue.code}→{date_obj} {start.strftime('%H:%M')}]")
                    assigned = True
                    continue

            if campus_allocs and not assigned:
                slot_ok = campus_tracker.group_campus_ok(
                    campus_allocs, date_obj, start, end,
                    main_tracker, cross_campus_lids, relax_lecturer=True)
                if slot_ok:
                    written = _write_campus_entries(
                        campus_allocs, date_obj, start, end,
                        campus_tracker, main_tracker, cross_campus_lids,
                        scheduled_campus_ids)
                    if written:
                        placed += written
                        c = campus_allocs[0].campus.code if campus_allocs[0].campus else "?"
                        scheduled_list.append(
                            f"{task.norm_code} [{c}-EVENING→{date_obj} {start.strftime('%H:%M')}]")
                        assigned = True

        if not assigned:
            still.append(task)
            unscheduled_list.append(f"{task.norm_code} [Evening/Weekend] – no slot")

    return placed, still


# ─────────────────────────────────────────────────────────
# EXHAUSTIVE SWEEP WITH DISSOLVE
# ─────────────────────────────────────────────────────────
def _rebuild_sweep_tasks_from_db(
    all_groups: Dict[str, UnifiedCourseGroup],
    scheduled_main_ids: Set[int],
    scheduled_campus_ids: Set[int],
    merge_limit: int = 200,
) -> List[UnifiedCourseGroup]:
    placed_main_ids: Set[int] = set(
        TempTimetable.objects.values_list("course_allocation_id", flat=True).distinct()
    )
    placed_campus_ids: Set[int] = set(
        CampusTempTimetable.objects.values_list("course_allocation_id", flat=True).distinct()
    )
    scheduled_main_ids.update(placed_main_ids)
    scheduled_campus_ids.update(placed_campus_ids)

    unscheduled_groups = []
    covered_ids: Set[int] = set()

    for grp in all_groups.values():
        missing_main = [a for a in grp.main_allocs if a.id not in placed_main_ids]
        missing_campus = [a for a in grp.campus_allocs if a.id not in placed_campus_ids]

        if not missing_main and not missing_campus:
            continue

        covered_ids.update(a.id for a in grp.main_allocs)
        covered_ids.update(a.id for a in grp.campus_allocs)

        if len(missing_main) < len(grp.main_allocs) or len(missing_campus) < len(grp.campus_allocs):
            from copy import copy
            partial = copy(grp)
            partial.main_allocs = missing_main
            partial.campus_allocs = missing_campus
            unscheduled_groups.append(partial)
        else:
            unscheduled_groups.append(grp)

    all_main_allocs = [a for g in all_groups.values() for a in g.main_allocs]
    all_campus_allocs = [a for g in all_groups.values() for a in g.campus_allocs]

    orphans_main = [a for a in all_main_allocs if a.id not in placed_main_ids and a.id not in covered_ids]
    orphans_campus = [a for a in all_campus_allocs if a.id not in placed_campus_ids and a.id not in covered_ids]

    for a in orphans_main:
        synthetic = UnifiedCourseGroup(normalize_code(a.course_code))
        synthetic.main_allocs = [a]
        unscheduled_groups.append(synthetic)

    for a in orphans_campus:
        synthetic = UnifiedCourseGroup(normalize_code(a.course_code))
        synthetic.campus_allocs = [a]
        unscheduled_groups.append(synthetic)

    return unscheduled_groups


def process_exhaustive_sweep_dual(
        all_groups: Dict[str, UnifiedCourseGroup],
        date_range, slots,
        main_tracker: MainCampusTracker,
        campus_tracker: CampusTracker,
        cross_campus_lids: Set[int],
        scheduled_main_ids: Set[int],
        scheduled_campus_ids: Set[int],
        scheduled_list: List[str],
        unscheduled_list: List[str],
) -> Tuple[int, List[UnifiedCourseGroup]]:
    cache = get_dual_cache()
    cache.load_existing_entries()

    safe_print("[DualSweep] Rebuilding tracker from DB…")
    main_tracker.lecturer_busy.clear()
    main_tracker.program_year_schedule.clear()
    main_tracker.program_year_daily_count.clear()
    main_tracker.program_year_slot_allocs.clear()
    main_tracker.venue_usage.clear()
    main_tracker.venue_schedule.clear()

    for entry in TempTimetable.objects.select_related('course_allocation__lecturer', 'course_allocation__program', 'venue').all():
        a = entry.course_allocation
        if entry.venue:
            main_tracker.use_venue(entry.venue.id, entry.day, entry.start_time, student_count(a))
        main_tracker.mark_main_alloc(a, entry.day, entry.start_time, entry.end_time)

    unscheduled_groups = _rebuild_sweep_tasks_from_db(
        all_groups, scheduled_main_ids, scheduled_campus_ids, MERGE_LIMIT)

    if not unscheduled_groups:
        safe_print("[DualSweep] Nothing to sweep.")
        return 0, []

    unscheduled_groups = sorted(unscheduled_groups, key=lambda g: -g.total_students)
    venues_asc = sorted(main_tracker.venues, key=lambda v: main_tracker.venue_examcap.get(v.id, 0))

    safe_print(f"[DualSweep] {len(unscheduled_groups)} groups | 3 passes")
    placed = 0
    truly_unscheduled = []

    for grp in unscheduled_groups:
        main_unplaced = [a for a in grp.main_allocs if a.id not in scheduled_main_ids]
        campus_unplaced = [a for a in grp.campus_allocs if a.id not in scheduled_campus_ids]

        if not main_unplaced and not campus_unplaced:
            continue

        assigned_main = False
        assigned_campus = False

        if main_unplaced:
            rep = main_unplaced[0]
            lid = rep.lecturer.id if rep.lecturer else None
            pid = rep.program.id if rep.program else None
            year = get_course_year(rep.course_code) if rep.course_code else 1
            total_st = sum(student_count(a) for a in main_unplaced)
            label = grp.norm_code

            for sweep_pass in range(1, 4):
                if assigned_main:
                    break
                check_lecturer = (sweep_pass <= 2)
                check_program_yr = (sweep_pass == 1)

                for date_obj, _ in date_range:
                    if assigned_main:
                        break
                    for ss, se in slots:
                        if assigned_main:
                            break

                        if check_lecturer and lid and not main_tracker.lecturer_ok(lid, date_obj, ss):
                            continue
                        if check_program_yr and main_tracker.has_program_conflict(
                                pid, year, date_obj, ss, new_alloc=rep):
                            continue

                        alloc_ok = True
                        for a in main_unplaced:
                            a_lid = a.lecturer.id if a.lecturer else None
                            a_pid = a.program.id if a.program else None
                            a_year = get_course_year(a.course_code) if a.course_code else 1
                            if check_lecturer and a_lid and not main_tracker.lecturer_ok(a_lid, date_obj, ss):
                                alloc_ok = False
                                break
                            if check_program_yr and main_tracker.has_program_conflict(
                                    a_pid, a_year, date_obj, ss, new_alloc=a):
                                alloc_ok = False
                                break
                        if not alloc_ok:
                            continue

                        venue = main_tracker.find_best_venue(total_st, date_obj, ss)
                        if not venue:
                            continue

                        day_str = date_obj.strftime("%A")
                        if cache.is_duplicate_main(venue.id, day_str, ss, se):
                            continue

                        written = _write_main_entries(main_unplaced, venue, date_obj, ss, se,
                                                       main_tracker, scheduled_main_ids)
                        if written:
                            placed += written
                            main_tracker.pin_slot(grp.norm_code, date_obj, ss, se)
                            grp.assign_slot(date_obj, ss, se)
                            pass_label = f"pass={sweep_pass}" if sweep_pass > 1 else "strict"
                            scheduled_list.append(
                                f"{label} [MAIN-SWEEP/{pass_label} {venue.code}→{date_obj} {ss.strftime('%H:%M')}]")
                            assigned_main = True

            if not assigned_main:
                if len(main_unplaced) > 1:
                    for a in main_unplaced:
                        if a.id in scheduled_main_ids:
                            continue
                        for sweep_pass in range(1, 4):
                            if a.id in scheduled_main_ids:
                                break
                            check_lecturer = (sweep_pass <= 2)
                            check_program_yr = (sweep_pass == 1)
                            a_lid = a.lecturer.id if a.lecturer else None
                            a_pid = a.program.id if a.program else None
                            a_year = get_course_year(a.course_code) if a.course_code else 1
                            a_students = student_count(a)

                            for date_obj, _ in date_range:
                                if a.id in scheduled_main_ids:
                                    break
                                for ss, se in slots:
                                    if a.id in scheduled_main_ids:
                                        break
                                    if check_lecturer and a_lid and not main_tracker.lecturer_ok(a_lid, date_obj, ss):
                                        continue
                                    if check_program_yr and main_tracker.has_program_conflict(
                                            a_pid, a_year, date_obj, ss, new_alloc=a):
                                        continue

                                    venue = main_tracker.find_best_venue(a_students, date_obj, ss)
                                    if not venue:
                                        continue

                                    day_str = date_obj.strftime("%A")
                                    if cache.is_duplicate_main(venue.id, day_str, ss, se):
                                        continue

                                    written = _write_main_entries([a], venue, date_obj, ss, se,
                                                                   main_tracker, scheduled_main_ids)
                                    if written:
                                        placed += written
                                        scheduled_list.append(
                                            f"{a.course_code} [MAIN-SWEEP/DISSOLVED→{date_obj} {ss.strftime('%H:%M')}]")
                                        break

                remaining = [a for a in main_unplaced if a.id not in scheduled_main_ids]
                if remaining:
                    for a in remaining:
                        unscheduled_list.append(f"{a.course_code} [MAIN-SWEEP – truly unschedulable]")

        if campus_unplaced:
            for sweep_pass in range(1, 4):
                if assigned_campus:
                    break
                relax_lect = (sweep_pass == 3)
                relax_prog_yr = (sweep_pass >= 2)

                slot = find_slot_for_campus_group(
                    campus_unplaced, grp.norm_code, date_range, slots,
                    main_tracker, campus_tracker, cross_campus_lids,
                    relax_lecturer=relax_lect)
                if slot:
                    date_obj, ss, se = slot
                    pc, _ = place_campus_group(
                        campus_unplaced, date_obj, ss, se,
                        campus_tracker, main_tracker, cross_campus_lids,
                        scheduled_campus_ids, relax_lecturer=True)
                    if pc:
                        placed += pc
                        main_tracker.pin_slot(grp.norm_code, date_obj, ss, se)
                        grp.assign_slot(date_obj, ss, se)
                        pass_label = f"pass={sweep_pass}" if sweep_pass > 1 else "strict"
                        for a in campus_unplaced:
                            if a.id in scheduled_campus_ids:
                                c = a.campus.code if a.campus else "?"
                                scheduled_list.append(
                                    f"{a.course_code} [{c}-SWEEP/{pass_label}→{date_obj} {ss.strftime('%H:%M')}]")
                        assigned_campus = True
                        break

            if not assigned_campus:
                if len(campus_unplaced) > 1:
                    for a in campus_unplaced:
                        if a.id in scheduled_campus_ids:
                            continue
                        for sweep_pass in range(1, 4):
                            if a.id in scheduled_campus_ids:
                                break
                            relax_lect = (sweep_pass == 3)
                            relax_prog_yr = (sweep_pass >= 2)
                            slot = find_slot_for_campus_group(
                                [a], grp.norm_code, date_range, slots,
                                main_tracker, campus_tracker, cross_campus_lids,
                                relax_lecturer=relax_lect)
                            if slot:
                                date_obj, ss, se = slot
                                pc, _ = place_campus_group(
                                    [a], date_obj, ss, se,
                                    campus_tracker, main_tracker, cross_campus_lids,
                                    scheduled_campus_ids, relax_lecturer=True)
                                if pc:
                                    placed += pc
                                    c = a.campus.code if a.campus else "?"
                                    scheduled_list.append(
                                        f"{a.course_code} [{c}-SWEEP/DISSOLVED→{date_obj} {ss.strftime('%H:%M')}]")
                                    break

                remaining = [a for a in campus_unplaced if a.id not in scheduled_campus_ids]
                if remaining:
                    for a in remaining:
                        c = a.campus.code if a.campus else "?"
                        unscheduled_list.append(f"{a.course_code} [{c}-SWEEP – truly unschedulable]")

    safe_print(f"[DualSweep] Placed: {placed}")
    return placed, truly_unscheduled


# ─────────────────────────────────────────────────────────
# ZERO-STUDENT COURSES PASS
# ─────────────────────────────────────────────────────────
def process_zero_student_dual(
    zero_main: List[CourseAllocation],
    zero_campus: List[CampusCourseAllocation],
    date_range, slots,
    main_tracker: MainCampusTracker,
    campus_tracker: CampusTracker,
    cross_campus_lids: Set[int],
    scheduled_main_ids: Set[int],
    scheduled_campus_ids: Set[int],
    scheduled_list: List[str],
    unscheduled_list: List[str],
) -> int:
    placed = 0
    cache = get_dual_cache()
    venues_asc = sorted(main_tracker.venues, key=lambda v: v.capacity or 0)

    for alloc in zero_main:
        if alloc.id in scheduled_main_ids:
            continue
        lid = alloc.lecturer.id if alloc.lecturer else None
        pid = alloc.program.id if alloc.program else None
        year = get_course_year(alloc.course_code) if alloc.course_code else 1

        placed_alloc = False
        for date_obj, _ in date_range:
            if placed_alloc:
                break
            for ss, se in slots:
                if placed_alloc:
                    break
                if lid and not main_tracker.lecturer_ok(lid, date_obj, ss):
                    continue
                if main_tracker.has_program_conflict(pid, year, date_obj, ss, new_alloc=alloc):
                    continue
                for v in venues_asc:
                    if main_tracker.has_venue_conflict(v.id, date_obj, ss):
                        continue
                    day_str = date_obj.strftime("%A")
                    if cache.is_duplicate_main(v.id, day_str, ss, se):
                        continue
                    written = _write_main_entries([alloc], v, date_obj, ss, se,
                                                   main_tracker, scheduled_main_ids)
                    if written:
                        placed += 1
                        placed_alloc = True
                        scheduled_list.append(
                            f"{alloc.course_code} [MAIN-ZERO→{v.code} {date_obj} {ss.strftime('%H:%M')}]")
                        break

        if not placed_alloc:
            unscheduled_list.append(f"{alloc.course_code} [MAIN-ZERO – no slot]")

    for alloc in zero_campus:
        if alloc.id in scheduled_campus_ids:
            continue

        placed_alloc = False
        for date_obj, _ in date_range:
            if placed_alloc:
                break
            for ss, se in slots:
                if placed_alloc:
                    break
                if campus_tracker.group_campus_ok(
                        [alloc], date_obj, ss, se,
                        main_tracker, cross_campus_lids, relax_lecturer=True):
                    written = _write_campus_entries(
                        [alloc], date_obj, ss, se,
                        campus_tracker, main_tracker, cross_campus_lids,
                        scheduled_campus_ids)
                    if written:
                        placed += 1
                        placed_alloc = True
                        c = alloc.campus.code if alloc.campus else "?"
                        scheduled_list.append(
                            f"{alloc.course_code} [{c}-ZERO→{date_obj} {ss.strftime('%H:%M')}]")
                        break

        if not placed_alloc:
            c = alloc.campus.code if alloc.campus else "?"
            unscheduled_list.append(f"{alloc.course_code} [{c}-ZERO – no slot]")

    return placed


# ─────────────────────────────────────────────────────────
# CLEAR TEMP TABLES
# ─────────────────────────────────────────────────────────
@retry_on_lock(max_retries=5, delay=0.3)
def clear_temp_tables():
    with transaction.atomic():
        TempTimetable.objects.all().delete()
        CampusTempTimetable.objects.all().delete()
        AutoMergedExamGroup.objects.all().delete()
        MergedCourseGroupTimetable.objects.all().delete()

    cache = get_dual_cache()
    cache.clear()
    safe_print("Temp tables cleared and cache reset")


# ─────────────────────────────────────────────────────────
# VENUE CAPACITY OPTIMISATION
# ─────────────────────────────────────────────────────────
def optimize_venue_assignments_main(
        days: List[str],
        slots: List[Tuple[dtime, dtime]],
) -> Dict[str, int]:
    safe_print("\n" + "=" * 70)
    safe_print("PHASE 8: Main-Campus Venue Capacity Optimisation")
    safe_print("=" * 70)

    try:
        merged_alloc_ids: Set[int] = set(
            MergedCourseGroupTimetable.objects.values_list(
                'merged_courses__id', flat=True
            ).distinct()
        )
        safe_print(f"[VenueOpt] Merged-group alloc IDs: {len(merged_alloc_ids)}")
    except Exception as exc:
        safe_print(f"[VenueOpt] ERROR loading merged alloc IDs: {exc}")
        return {'timeslots_examined': 0, 'timeslots_optimised': 0,
                'swaps_made': 0, 'errors': 1}

    timeslots_examined = 0
    timeslots_optimised = 0
    swaps_made = 0
    errors = 0
    cache = get_dual_cache()

    for day in days:
        for slot_start, slot_end in slots:
            timeslots_examined += 1
            try:
                slot_entries = list(
                    TempTimetable.objects.select_related(
                        'course_allocation', 'venue'
                    ).filter(day=day, start_time=slot_start, end_time=slot_end)
                )
                if not slot_entries:
                    continue

                eligible = [
                    e for e in slot_entries
                    if e.course_allocation_id not in merged_alloc_ids
                ]
                if len(eligible) < 2:
                    continue

                venue_pool: Dict[int, object] = {
                    e.venue_id: e.venue
                    for e in eligible
                    if e.venue is not None
                }
                if len(venue_pool) < 2:
                    continue

                eligible_sorted = sorted(
                    eligible,
                    key=lambda e: (e.course_allocation.number_of_students or 0),
                    reverse=True,
                )
                venues_sorted = sorted(
                    venue_pool.values(),
                    key=lambda v: v.capacity or 0,
                    reverse=True,
                )
                n_venues = len(venues_sorted)

                optimal = [
                    venues_sorted[i % n_venues]
                    for i in range(len(eligible_sorted))
                ]

                changes = []
                for entry, new_venue in zip(eligible_sorted, optimal):
                    if entry.venue_id != new_venue.id:
                        changes.append((entry, new_venue))

                if not changes:
                    continue

                with transaction.atomic():
                    for entry, new_venue in changes:
                        day_str = entry.day
                        if cache.is_duplicate_main(new_venue.id, day_str, slot_start, slot_end):
                            continue
                        entry.venue = new_venue
                        entry.venue_id = new_venue.id
                        entry.save(update_fields=['venue', 'venue_id'])
                        swaps_made += 1
                        cache.add_main_entry(new_venue.id, day_str, slot_start, slot_end)

                timeslots_optimised += 1

                if timeslots_optimised <= 10 or timeslots_optimised % 50 == 0:
                    safe_print(f"[VenueOpt] Optimised {day} {slot_start}–{slot_end}: "
                               f"{len(eligible_sorted)} entries re-matched")

            except Exception as exc:
                errors += 1
                if errors <= 10:
                    safe_print(f"[VenueOpt] ERROR at {day} {slot_start}–{slot_end}: {exc}")

    safe_print(f"[VenueOpt] Done — examined={timeslots_examined} | "
               f"optimised={timeslots_optimised} | swaps={swaps_made} | errors={errors}")
    return {
        'timeslots_examined': timeslots_examined,
        'timeslots_optimised': timeslots_optimised,
        'swaps_made': swaps_made,
        'errors': errors,
    }


# ─────────────────────────────────────────────────────────
# MAIN SCHEDULING THREAD
# ─────────────────────────────────────────────────────────
def run_dual_campus_scheduler():
    _open_scheduler_log()

    main_scheduled_list: List[str] = []
    campus_scheduled_list: List[str] = []
    unscheduled_list: List[str] = []
    cross_campus_labels: List[str] = []

    try:
        enable_wal_mode()
        update_progress(2, "Clearing temp tables…", console_msg="Removing previous draft")
        clear_temp_tables()

        _dual_cache_instance = get_dual_cache()
        _dual_cache_instance.load_existing_entries()

        # ── Config ───────────────────────────────────────
        update_progress(4, "Loading configuration…")
        main_config = SchedulerConfig.objects.first()
        if not main_config:
            main_config = SchedulerConfig.objects.create()

        main_start = getattr(main_config, "start_time", dtime(7, 0))
        main_end = getattr(main_config, "end_time", dtime(19, 0))
        main_slot_size = int(getattr(main_config, "slot_size", 3))
        merge_limit = getattr(main_config, "merge_limit", MERGE_LIMIT)
        max_sched_days = getattr(main_config, "max_exam_days",
                         getattr(main_config, "max_days", 30))

        # ── Build date range ─────────────────────────────
        date_range = []
        if hasattr(main_config, "get_date_range"):
            try:
                excluded_set = set()
                if hasattr(main_config, "excluded_date_list"):
                    excluded_set = set(main_config.excluded_date_list())
                for ds, weekday_name in main_config.get_date_range():
                    if ds not in excluded_set:
                        try:
                            date_range.append(
                                (datetime.strptime(ds, "%Y-%m-%d").date(), weekday_name))
                        except Exception:
                            pass
            except Exception:
                date_range = []

        if not date_range:
            excluded = set()
            if hasattr(main_config, "excluded_date_list"):
                try:
                    excluded |= set(main_config.excluded_date_list())
                except Exception:
                    pass
            excl_raw = getattr(main_config, "excluded_days", "")
            if excl_raw and isinstance(excl_raw, str):
                excluded |= {d.strip() for d in excl_raw.split(",") if d.strip()}

            skip_weekends = getattr(main_config, "skip_weekends", True)
            start_date = getattr(main_config, "start_date", date.today())
            end_date = getattr(main_config, "end_date", None)
            collected = offset = 0
            while collected < max_sched_days:
                d = start_date + timedelta(days=offset)
                offset += 1
                if offset > 365:
                    break
                if end_date and d > end_date:
                    break
                ds = d.strftime("%Y-%m-%d")
                if ds in excluded:
                    continue
                if skip_weekends and d.weekday() >= 5:
                    continue
                date_range.append((d, d.strftime("%A")))
                collected += 1

        slots = generate_slots(main_start, main_end, main_slot_size)

        if not date_range or not slots:
            with _progress_lock:
                dual_scheduler_progress["status"] = "error"
                dual_scheduler_progress["message"] = "No valid dates or slots in config."
            return

        safe_print(f"Dates: {len(date_range)} | Slots/day: {len(slots)}")

        # ── Load allocations ─────────────────────────────
        update_progress(6, "Loading allocations…")
        main_allocs = list(CourseAllocation.objects.select_related(
            "program", "lecturer", "department",
            "program__department__faculty",
            "lecturer__department__faculty",
        ).filter(submitted_to_tt=True))
        if not main_allocs:
            main_allocs = list(CourseAllocation.objects.select_related(
                "program", "lecturer", "department",
                "program__department__faculty",
                "lecturer__department__faculty",
            ).filter(approved_by_dvc=True))

        campus_allocs = list(CampusCourseAllocation.objects.select_related(
            "program", "lecturer", "campus", "department",
            "program__department__faculty",
        ).filter(submitted_to_tt=True))
        if not campus_allocs:
            campus_allocs = list(CampusCourseAllocation.objects.select_related(
                "program", "lecturer", "campus", "department",
                "program__department__faculty",
            ).filter(approved_by_dvc=True))

        # Separate zero-student courses
        zero_student_main = [a for a in main_allocs if (a.number_of_students or 0) == 0]
        zero_student_campus = [a for a in campus_allocs if (a.number_of_students or 0) == 0]
        main_allocs = [a for a in main_allocs if (a.number_of_students or 0) > 0]
        campus_allocs = [a for a in campus_allocs if (a.number_of_students or 0) > 0]

        total_main = len(main_allocs)
        total_campus = len(campus_allocs)
        total_allocs = total_main + total_campus + len(zero_student_main) + len(zero_student_campus)
        safe_print(f"Main: {total_main} | Campus: {total_campus} | Zero-main: {len(zero_student_main)} | Zero-campus: {len(zero_student_campus)}")

        if total_allocs == 0:
            with _progress_lock:
                dual_scheduler_progress["status"] = "completed"
                dual_scheduler_progress["message"] = "No allocations to schedule."
            return

        update_progress(8, f"Found {total_allocs} allocations",
                        scheduled=0, remaining=total_allocs)

        # ════════════════════════════════════════════════
        # STEP 1 – Phase 0: Build unified course groups
        # ════════════════════════════════════════════════
        update_progress(10, "Phase 0: Building unified groups…")
        groups = build_unified_course_groups(main_allocs, campus_allocs)

        evening_weekend_groups = {k: g for k, g in groups.items() if g.is_evening_weekend()}
        regular_groups = {k: g for k, g in groups.items() if not g.is_evening_weekend()}

        cross_campus_groups = {k: g for k, g in regular_groups.items() if g.is_cross_campus}
        single_campus_groups = {k: g for k, g in regular_groups.items() if not g.is_cross_campus}

        cross_lect, _, _ = identify_cross_campus_lecturers(main_allocs, campus_allocs)
        cross_campus_lids: Set[int] = set(cross_lect.keys())

        for lid, info in cross_lect.items():
            lname = (info["main"][0].lecturer.display_name
                     if info["main"] and info["main"][0].lecturer else "Unknown")
            cross_campus_labels.append(
                f"{lname} | main:{len(info['main'])} campus:{len(info['campus'])}")

        # Venue Specialization
        update_progress(12, "Phase 0b: Building venue specialization index…")
        course_to_venues, specialization_venue_ids = build_specialization_index()
        safe_print(f"Specialization: {len(course_to_venues)} codes, {len(specialization_venue_ids)} venues")

        update_progress(14,
            f"Phase 0: {len(groups)} groups | {len(cross_campus_groups)} cross-campus",
            cross_campus_lecturers=cross_campus_labels,
            console_msg=f"{len(cross_campus_groups)} cross-campus | {len(cross_lect)} cross-campus lecturers")

        main_tracker = MainCampusTracker()
        main_tracker._date_range = date_range
        main_tracker._slots = slots
        campus_tracker = CampusTracker()
        scheduled_main_ids: Set[int] = set()
        scheduled_campus_ids: Set[int] = set()
        failed_groups: List[UnifiedCourseGroup] = []

        # Separate UG and PG groups
        ug_groups = {k: g for k, g in regular_groups.items() if not g.is_pg()}
        pg_groups = {k: g for k, g in regular_groups.items() if g.is_pg()}
        safe_print(f"UG groups: {len(ug_groups)} | PG groups: {len(pg_groups)} | Evening/Weekend: {len(evening_weekend_groups)}")

        # ════════════════════════════════════════════════
        # STEP 2 – Phase 1: Cross-campus UG groups (pin slot)
        # ════════════════════════════════════════════════
        update_progress(15, "Phase 1: Cross-campus unified groups…")
        cc_ug = sorted(
            [g for g in cross_campus_groups.values() if not g.is_pg()],
            key=lambda g: (-g.is_mergeable, -g.total_students))
        cc_placed_main = cc_placed_campus = 0

        for i, grp in enumerate(cc_ug):
            update_progress(
                15 + int(i / max(len(cc_ug), 1) * 15),
                f"Phase 1: {grp.norm_code} ({grp.total_students}st)")
            relax = grp.is_mergeable or grp.is_shareable

            slot = find_slot_for_main_group(
                grp.main_allocs, grp.norm_code, date_range, slots, main_tracker, relax)
            if not slot and not relax:
                slot = find_slot_for_main_group(
                    grp.main_allocs, grp.norm_code, date_range, slots, main_tracker, True)

            if slot:
                date_obj, ss, se = slot
                grp.assign_slot(date_obj, ss, se)
                main_tracker.force_pin_slot(grp.norm_code, date_obj, ss, se)
                pm, _ = place_main_group(grp.main_allocs, date_obj, ss, se,
                                          main_tracker, scheduled_main_ids,
                                          prefer_occupied=relax)
                pc, _ = place_campus_group(grp.campus_allocs, date_obj, ss, se,
                                            campus_tracker, main_tracker,
                                            cross_campus_lids, scheduled_campus_ids,
                                            relax_lecturer=relax)
                cc_placed_main += pm
                cc_placed_campus += pc
                for a in grp.main_allocs:
                    main_scheduled_list.append(
                        f"{a.course_code} [MAIN→{date_obj} {ss.strftime('%H:%M')}]")
                for a in grp.campus_allocs:
                    c = a.campus.code if a.campus else "?"
                    campus_scheduled_list.append(
                        f"{a.course_code} [{c}→{date_obj} {ss.strftime('%H:%M')}]")
            else:
                failed_groups.append(grp)
                for a in grp.all_allocs:
                    unscheduled_list.append(f"{a.course_code} [cross-campus – no slot]")

        update_progress(30,
            f"Phase 1: {cc_placed_main}m + {cc_placed_campus}c",
            scheduled=len(scheduled_main_ids) + len(scheduled_campus_ids),
            remaining=total_allocs - len(scheduled_main_ids) - len(scheduled_campus_ids))

        # ════════════════════════════════════════════════
        # STEP 3 – Phase 2: Main UG scheduling
        # ════════════════════════════════════════════════

        # --- Phase 2A: Batch scheduling ---
        update_progress(31, "Phase 2A: Main UG batch scheduling…")
        main_ug_tasks = [
            {'merged': g.main_allocs, 'total_students': g.total_main_students,
             'norm_code': g.norm_code}
            for g in single_campus_groups.values()
            if g.main_allocs and not g.is_pg()
        ]
        main_ug_tasks = sorted(main_ug_tasks, key=lambda t: -t['total_students'])

        p2a, still_after_batch = process_main_ug_batch(
            main_ug_tasks, date_range, slots,
            main_tracker, scheduled_main_ids,
            main_scheduled_list, unscheduled_list)
        safe_print(f"Phase 2A: {p2a} placed | {len(still_after_batch)} still unscheduled")

        # --- Phase 2B: Shared-venue pre-fill ---
        update_progress(43, "Phase 2B: Shared-venue pre-fill…")
        p2b = 0
        safe_print(f"Phase 2B: {p2b} placed")

        # --- Phase 2C: Fallback ---
        update_progress(50, "Phase 2C: Main UG fallback…")
        p2c, still_after_fallback = process_main_ug_fallback(
            still_after_batch, date_range, slots,
            main_tracker, scheduled_main_ids,
            main_scheduled_list, unscheduled_list)
        safe_print(f"Phase 2C: {p2c} placed | {len(still_after_fallback)} still unscheduled")

        # --- Phase 2D: Compression ---
        p2d = 0
        if still_after_fallback:
            update_progress(58, "Phase 2D: Main UG compression…")
            p2d, still_after_compression = process_main_ug_compression(
                still_after_fallback, date_range, slots,
                main_tracker, scheduled_main_ids,
                main_scheduled_list, unscheduled_list)
            safe_print(f"Phase 2D: {p2d} placed | {len(still_after_compression)} unscheduled")
        else:
            still_after_compression = []

        safe_print(f"Phase 2 total: {p2a + p2b + p2c + p2d} main UG allocs placed")

        update_progress(62,
            f"Phase 2 done: {p2a + p2b + p2c + p2d} main UG placed",
            scheduled=len(scheduled_main_ids) + len(scheduled_campus_ids),
            remaining=total_allocs - len(scheduled_main_ids) - len(scheduled_campus_ids),
            main_scheduled=main_scheduled_list)

        # ════════════════════════════════════════════════
        # STEP 4 – Phase 3: Branch campus UG scheduling
        # ════════════════════════════════════════════════
        update_progress(63, "Phase 3A: Branch UG groups…")

        campus_ug_tasks = [
            {'merged': g.campus_allocs, 'total_students': g.total_campus_students,
             'norm_code': g.norm_code}
            for g in single_campus_groups.values()
            if g.campus_allocs and not g.is_pg()
        ]
        campus_ug_tasks = sorted(campus_ug_tasks, key=lambda t: -t['total_students'])

        p3a = 0
        for task in campus_ug_tasks:
            allocs = [a for a in task['merged'] if a.id not in scheduled_campus_ids]
            if not allocs:
                continue
            slot = find_slot_for_campus_group(
                allocs, task['norm_code'], date_range, slots,
                main_tracker, campus_tracker, cross_campus_lids, relax_lecturer=True)
            if slot:
                date_obj, ss, se = slot
                main_tracker.pin_slot(task['norm_code'], date_obj, ss, se)
                pc, _ = place_campus_group(
                    allocs, date_obj, ss, se,
                    campus_tracker, main_tracker, cross_campus_lids,
                    scheduled_campus_ids, relax_lecturer=True)
                p3a += pc
                for a in allocs:
                    if a.id in scheduled_campus_ids:
                        c = a.campus.code if a.campus else "?"
                        campus_scheduled_list.append(
                            f"{a.course_code} [{c}→{date_obj} {ss.strftime('%H:%M')}]")
            else:
                for a in allocs:
                    c = a.campus.code if a.campus else "?"
                    unscheduled_list.append(f"{a.course_code} [{c} – no slot]")
        safe_print(f"Phase 3A: {p3a} placed")

        # Phase 3C: Campus fill pass
        update_progress(75, "Phase 3C: Campus fill pass…")
        p3c = 0
        for grp in single_campus_groups.values():
            if grp.is_pg() or not grp.campus_allocs:
                continue
            unplaced = [a for a in grp.campus_allocs if a.id not in scheduled_campus_ids]
            if not unplaced:
                continue
            pin = main_tracker.get_pinned_slot(grp.norm_code)
            if not pin:
                continue
            date_obj, ss, se = pin
            pc, _ = place_campus_group(
                unplaced, date_obj, ss, se,
                campus_tracker, main_tracker, cross_campus_lids,
                scheduled_campus_ids, relax_lecturer=True)
            p3c += pc
            for a in unplaced:
                if a.id in scheduled_campus_ids:
                    c = a.campus.code if a.campus else "?"
                    campus_scheduled_list.append(
                        f"{a.course_code} [{c}-FILL→{date_obj} {ss.strftime('%H:%M')}]")

        # Phase 3D: Campus compression
        campus_still = [
            g for g in single_campus_groups.values()
            if g.campus_allocs and not g.is_pg()
            and any(a.id not in scheduled_campus_ids for a in g.campus_allocs)
        ]
        p3d = 0
        if campus_still:
            update_progress(76, "Phase 3D: Campus compression…")
            p3d, _ = process_campus_compression(
                [{'merged': g.campus_allocs, 'total_students': g.total_campus_students,
                  'norm_code': g.norm_code}
                 for g in campus_still],
                date_range, slots,
                main_tracker, campus_tracker, cross_campus_lids,
                scheduled_campus_ids, campus_scheduled_list, unscheduled_list)
            safe_print(f"Phase 3D compression: {p3d} placed")

        safe_print(f"Phase 3: 3A:{p3a} 3C:{p3c} 3D:{p3d}")
        update_progress(78,
            f"Phase 3: {p3a + p3c + p3d} campus placed",
            scheduled=len(scheduled_main_ids) + len(scheduled_campus_ids),
            remaining=total_allocs - len(scheduled_main_ids) - len(scheduled_campus_ids),
            campus_scheduled=campus_scheduled_list)

        # ════════════════════════════════════════════════
        # STEP 5 – Phase 4: PG scheduling
        # ════════════════════════════════════════════════
        update_progress(79, "Phase 4: PG scheduling (deferred, last)…")

        ug_unscheduled_count = (
            sum(1 for a in main_allocs if a.id not in scheduled_main_ids
                and not is_postgraduate(a.course_code)) +
            sum(1 for a in campus_allocs if a.id not in scheduled_campus_ids
                and not is_postgraduate(a.course_code))
        )
        free_fill = (ug_unscheduled_count == 0)
        safe_print(f"PG free-fill: {'ON' if free_fill else 'OFF'}")

        pg_groups_list = sorted(pg_groups.values(), key=lambda g: -g.total_students)

        for grp in pg_groups_list:
            # Main PG
            if grp.main_allocs:
                unplaced = [a for a in grp.main_allocs if a.id not in scheduled_main_ids]
                if unplaced:
                    slot = find_slot_for_pg_main(
                        unplaced, grp.norm_code, date_range, slots, main_tracker, free_fill)
                    if slot:
                        date_obj, ss, se = slot
                        grp.assign_slot(date_obj, ss, se)
                        main_tracker.pin_slot(grp.norm_code, date_obj, ss, se)
                        pm, _ = place_main_group(unplaced, date_obj, ss, se,
                                                  main_tracker, scheduled_main_ids)
                        for a in unplaced:
                            if a.id in scheduled_main_ids:
                                main_scheduled_list.append(
                                    f"{a.course_code} [PG-MAIN→{date_obj} {ss.strftime('%H:%M')}]")
                    else:
                        for a in unplaced:
                            unscheduled_list.append(f"{a.course_code} [PG MAIN – no slot]")

            # Campus PG
            if grp.campus_allocs:
                unplaced = [a for a in grp.campus_allocs if a.id not in scheduled_campus_ids]
                if unplaced:
                    pin = main_tracker.get_pinned_slot(grp.norm_code)
                    if pin:
                        date_obj, ss, se = pin
                        pc, _ = place_campus_group(
                            unplaced, date_obj, ss, se,
                            campus_tracker, main_tracker, cross_campus_lids,
                            scheduled_campus_ids, relax_lecturer=True)
                        for a in unplaced:
                            if a.id in scheduled_campus_ids:
                                c = a.campus.code if a.campus else "?"
                                campus_scheduled_list.append(
                                    f"{a.course_code} [{c}-PG→{date_obj} {ss.strftime('%H:%M')}]")
                        unplaced = [a for a in unplaced if a.id not in scheduled_campus_ids]

                    if unplaced:
                        slot = find_slot_for_pg_campus(
                            unplaced, grp.norm_code, date_range, slots,
                            main_tracker, campus_tracker, cross_campus_lids, free_fill)
                        if slot:
                            date_obj, ss, se = slot
                            pc, _ = place_campus_group(
                                unplaced, date_obj, ss, se,
                                campus_tracker, main_tracker, cross_campus_lids,
                                scheduled_campus_ids, relax_lecturer=True)
                            for a in unplaced:
                                if a.id in scheduled_campus_ids:
                                    c = a.campus.code if a.campus else "?"
                                    campus_scheduled_list.append(
                                        f"{a.course_code} [{c}-PG→{date_obj} {ss.strftime('%H:%M')}]")
                        else:
                            for a in unplaced:
                                c = a.campus.code if a.campus else "?"
                                unscheduled_list.append(f"{a.course_code} [PG {c} – no slot]")

        update_progress(88,
            "Phase 4 done: PG scheduling complete",
            scheduled=len(scheduled_main_ids) + len(scheduled_campus_ids),
            remaining=total_allocs - len(scheduled_main_ids) - len(scheduled_campus_ids))

        # ════════════════════════════════════════════════
        # STEP 6 – Phase 5: Exhaustive sweep
        # ════════════════════════════════════════════════
        update_progress(89, "Phase 5: Multi-pass exhaustive sweep…")
        sweep_placed, _ = process_exhaustive_sweep_dual(
            regular_groups, date_range, slots,
            main_tracker, campus_tracker, cross_campus_lids,
            scheduled_main_ids, scheduled_campus_ids,
            main_scheduled_list, unscheduled_list)
        safe_print(f"Phase 5 sweep: {sweep_placed} placed")

        update_progress(93, "Phase 5 sweep complete.",
            scheduled=len(scheduled_main_ids) + len(scheduled_campus_ids),
            remaining=total_allocs - len(scheduled_main_ids) - len(scheduled_campus_ids))

        # ════════════════════════════════════════════════
        # STEP 7 – Phase 6: Evening/Weekend pass
        # ════════════════════════════════════════════════
        if evening_weekend_groups:
            update_progress(94, "Phase 6: Evening/Weekend class pass…")
            ew_tasks = list(evening_weekend_groups.values())
            ew_placed, ew_still = process_evening_weekend_dual(
                ew_tasks, main_tracker, campus_tracker, cross_campus_lids,
                scheduled_main_ids, scheduled_campus_ids,
                date_range, slots, main_config,
                main_scheduled_list, unscheduled_list)
            safe_print(f"Phase 6 evening/weekend: {ew_placed} placed, {len(ew_still)} still")

        # ════════════════════════════════════════════════
        # STEP 8 – Phase 7: Zero-student pass
        # ════════════════════════════════════════════════
        if zero_student_main or zero_student_campus:
            update_progress(96, "Phase 7: Zero-student courses pass…")
            zero_placed = process_zero_student_dual(
                zero_student_main, zero_student_campus,
                date_range, slots,
                main_tracker, campus_tracker, cross_campus_lids,
                scheduled_main_ids, scheduled_campus_ids,
                main_scheduled_list, unscheduled_list)
            safe_print(f"Phase 7 zero-student: {zero_placed} placed")

        # ════════════════════════════════════════════════
        # STEP 9 – Phase 8: Venue capacity optimisation
        # ════════════════════════════════════════════════
        update_progress(97, "Phase 8: Venue capacity optimisation…")
        day_names = [day_name for _, day_name in date_range]
        seen_days: Set[str] = set()
        unique_day_names: List[str] = []
        for dn in day_names:
            if dn not in seen_days:
                unique_day_names.append(dn)
                seen_days.add(dn)

        venue_opt_stats = optimize_venue_assignments_main(unique_day_names, slots)
        safe_print(
            f"Phase 8 venue opt: {venue_opt_stats['swaps_made']} swaps in "
            f"{venue_opt_stats['timeslots_optimised']} timeslots"
        )

        # ── Final summary ──────────────────────────────
        total_sched = len(scheduled_main_ids) + len(scheduled_campus_ids)
        success_rate = total_sched / max(total_allocs, 1) * 100
        cs = main_tracker.stats()
        cs["detected"] = cs.get("detected", 0) + campus_tracker.total_conflicts

        final_msg = (
            f"DONE: {total_sched}/{total_allocs} ({success_rate:.1f}%) | "
            f"Main: {len(scheduled_main_ids)} | Campus: {len(scheduled_campus_ids)} | "
            f"Unscheduled: {len(unscheduled_list)} | "
            f"Conflicts resolved: {cs.get('resolved', 0)} | "
            f"Sweep: {sweep_placed} | "
            f"VenueOpt: {venue_opt_stats['swaps_made']} swaps"
        )

        update_progress(100, "Scheduling complete!",
                        scheduled=total_sched, remaining=len(unscheduled_list),
                        console_msg=final_msg,
                        main_scheduled=main_scheduled_list,
                        campus_scheduled=campus_scheduled_list,
                        unscheduled=unscheduled_list,
                        cross_campus_lecturers=cross_campus_labels,
                        conflict_stats=cs)
        with _progress_lock:
            dual_scheduler_progress["status"] = "completed"
            dual_scheduler_progress["message"] = final_msg
        safe_print(f"\n{'='*60}\n{final_msg}\n{'='*60}")

    except Exception as e:
        err = traceback.format_exc()
        safe_print(f"SCHEDULER ERROR:\n{err}")
        scheduler_logger.error("Dual-campus REGULAR scheduler fatal error: %s\n%s", e, err)
        update_progress(0, f"Error: {str(e)}", console_msg=f"Fatal: {str(e)}")
        with _progress_lock:
            dual_scheduler_progress["status"] = "error"
            dual_scheduler_progress["message"] = str(e)
    finally:
        _close_scheduler_log(success=(dual_scheduler_progress.get("status") == "completed"))


# ─────────────────────────────────────────────────────────
# PUBLISH FUNCTIONS
# ─────────────────────────────────────────────────────────
@retry_on_lock(max_retries=5, delay=0.5)
def publish_main_timetable() -> int:
    with transaction.atomic():
        Timetable.objects.all().delete()
        entries = list(TempTimetable.objects.select_related("course_allocation", "venue").all())
        Timetable.objects.bulk_create([
            Timetable(
                course_allocation=t.course_allocation,
                venue=t.venue,
                day=t.day,
                start_time=t.start_time,
                end_time=t.end_time,
            )
            for t in entries
        ])
        return len(entries)


@retry_on_lock(max_retries=5, delay=0.5)
def publish_campus_timetable(campus_id=None) -> int:
    with transaction.atomic():
        qs = CampusTimetable.objects
        if campus_id:
            qs = qs.filter(campus_id=campus_id)
        qs.all().delete()
        tmp_qs = CampusTempTimetable.objects.select_related("course_allocation", "campus")
        if campus_id:
            tmp_qs = tmp_qs.filter(campus_id=campus_id)
        tmp = list(tmp_qs)
        CampusTimetable.objects.bulk_create([
            CampusTimetable(
                course_allocation=t.course_allocation,
                campus=t.campus,
                day=t.day,
                start_time=t.start_time,
                end_time=t.end_time,
            )
            for t in tmp
        ])
        return len(tmp)


# ─────────────────────────────────────────────────────────
# DJANGO VIEWS
# ─────────────────────────────────────────────────────────
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def dual_scheduler_page(request):
    config = SchedulerConfig.objects.first()
    days = getattr(config, "days", None) or [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
    ]

    if request.method == "POST" and "reset_config" in request.POST:
        if config:
            config.start_time = request.POST.get("start_time", "07:00")
            config.end_time = request.POST.get("end_time", "19:00")
            config.slot_size = request.POST.get("slot_size", 3)
            config.save()

    context = {
        "heading": "Dual-Campus Scheduler",
        "campuses": Campus.objects.filter(is_active=True),
        "days": days,
        "config": config,
        "campus_config": CampusSchedulerConfig.objects.first(),
        "main_temp_count": TempTimetable.objects.count(),
        "campus_temp_count": CampusTempTimetable.objects.count(),
        "main_published_count": Timetable.objects.count(),
        "campus_published_count": CampusTimetable.objects.count(),
        "cross_campus_count": len(dual_scheduler_progress.get("cross_campus_lecturers", [])),
        "default_start": (config.start_time.strftime("%H:%M") if config and config.start_time else "07:00"),
        "default_end": (config.end_time.strftime("%H:%M") if config and config.end_time else "19:00"),
        "default_slot": config.slot_size if config else 3,
    }
    return render(request, "dashboard/dual_scheduler.html", context)


@method_decorator(csrf_exempt, name="dispatch")
class StartDualSchedulerView(View):
    def post(self, request):
        with _progress_lock:
            if dual_scheduler_progress.get("status") == "running":
                return JsonResponse({"status": "already_running",
                                     "message": "Scheduler is already running."})
            dual_scheduler_progress.update({
                "status": "running", "progress": 0,
                "current_action": "Initialising…",
                "scheduled_count": 0, "remaining_count": 0,
                "batch_info": "", "total_courses": 0,
                "current_batch": 0, "total_batches": 0, "message": "",
                "console_output": [], "main_scheduled": [], "campus_scheduled": [],
                "unscheduled": [], "cross_campus_lecturers": [],
                "conflict_stats": {"detected": 0, "resolved": 0,
                                   "unresolved": 0, "resolution_rate": 0},
            })
        threading.Thread(target=run_dual_campus_scheduler, daemon=True).start()
        return JsonResponse({"status": "started",
                             "message": "Dual-campus scheduler started."})


class DualSchedulerProgressView(View):
    def get(self, request):
        return JsonResponse(dual_scheduler_progress)


@method_decorator(csrf_exempt, name="dispatch")
class CancelDualSchedulerView(View):
    def post(self, request):
        with _progress_lock:
            dual_scheduler_progress["status"] = "cancelled"
            dual_scheduler_progress["current_action"] = "Cancelled by user"
        return JsonResponse({"status": "cancelled"})


@method_decorator(csrf_exempt, name="dispatch")
class PublishMainTimetableView(View):
    def post(self, request):
        try:
            count = publish_main_timetable()
            return JsonResponse({"status": "success",
                                 "message": f"Published {count} main entries.",
                                 "count": count})
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)


@method_decorator(csrf_exempt, name="dispatch")
class PublishCampusTimetableView(View):
    def post(self, request):
        campus_id = (request.POST.get("campus_id") or
                     request.GET.get("campus_id"))
        try:
            count = publish_campus_timetable(campus_id=campus_id or None)
            label = f"campus {campus_id}" if campus_id else "all branch campuses"
            return JsonResponse({"status": "success",
                                 "message": f"Published {count} entries for {label}.",
                                 "count": count})
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)


class DualTimetableDataView(View):
    def get(self, request):
        view_type = request.GET.get("type", "main")
        campus_id = request.GET.get("campus_id")
        data = []
        if view_type == "main":
            for e in TempTimetable.objects.select_related(
                    "course_allocation", "course_allocation__lecturer",
                    "course_allocation__program", "venue").order_by("day", "start_time"):
                ca = e.course_allocation
                data.append({
                    "id": e.id,
                    "course_code": ca.course_code,
                    "course_name": ca.course_name,
                    "lecturer": (ca.lecturer.display_name if ca.lecturer else "TBA"),
                    "program": ca.program.name if ca.program else "",
                    "students": ca.number_of_students,
                    "venue": e.venue.code if e.venue else "",
                    "venue_capacity": e.venue.capacity if e.venue else 0,
                    "venue_exam_capacity": venue_exam_capacity(e.venue) if e.venue else 0,
                    "date": e.day,
                    "day": e.day,
                    "start_time": e.start_time.strftime("%H:%M"),
                    "end_time": e.end_time.strftime("%H:%M"),
                    "type": "main",
                    "norm_code": normalize_code(ca.course_code),
                })
        else:
            qs = CampusTempTimetable.objects.select_related(
                "course_allocation", "course_allocation__lecturer",
                "course_allocation__program", "campus").order_by("day", "start_time")
            if campus_id:
                qs = qs.filter(campus_id=campus_id)
            for e in qs:
                ca = e.course_allocation
                data.append({
                    "id": e.id,
                    "course_code": ca.course_code,
                    "course_name": ca.course_name,
                    "lecturer": ca.lecturer.display_name if ca.lecturer else "TBA",
                    "program": ca.program.name if ca.program else "",
                    "students": ca.number_of_students,
                    "campus": e.campus.name if e.campus else "",
                    "campus_code": e.campus.code if e.campus else "",
                    "date": str(getattr(e, "date", "")),
                    "day": e.day,
                    "start_time": str(e.start_time),
                    "end_time": str(e.end_time),
                    "type": "campus",
                    "norm_code": normalize_code(ca.course_code),
                })
        return JsonResponse({"status": "ok", "data": data, "count": len(data)})


class CrossCampusLecturersView(View):
    def get(self, request):
        main_allocs = list(CourseAllocation.objects.select_related(
            "lecturer").filter(approved_by_dvc=True))
        campus_allocs = list(CampusCourseAllocation.objects.select_related(
            "lecturer", "campus").filter(approved_by_dvc=True))
        cross, main_only, campus_only = identify_cross_campus_lecturers(
            main_allocs, campus_allocs)

        result = []
        for lid, data in cross.items():
            lname = (data["main"][0].lecturer.display_name
                     if data["main"] and data["main"][0].lecturer else "Unknown")
            result.append({
                "lecturer_id": lid,
                "lecturer_name": lname,
                "main_courses": [a.course_code for a in data["main"]],
                "campus_courses": [a.course_code for a in data["campus"]],
                "campus_names": list({a.campus.name for a in data["campus"] if a.campus}),
                "main_count": len(data["main"]),
                "campus_count": len(data["campus"]),
            })
        return JsonResponse({
            "status": "ok",
            "cross_campus_lecturers": result,
            "main_only_lecturers": len(main_only),
            "campus_only_lecturers": len(campus_only),
        })


class ConflictStatsView(View):
    def get(self, request):
        with _progress_lock:
            return JsonResponse({
                "status": "ok",
                "conflict_stats": dual_scheduler_progress.get("conflict_stats", {
                    "detected": 0, "resolved": 0,
                    "unresolved": 0, "resolution_rate": 0,
                }),
            })