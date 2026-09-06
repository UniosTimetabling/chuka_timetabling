"""
ENHANCED STABLE SCHEDULING ALGORITHMS for TT_APP
Two distinct modes:

MODE 1 - BASIC STABLE: Preserves existing approved schedules
- First year courses only can change
- Other years keep their slots unless skipped
- Only schedules first year + skipped courses

MODE 2 - ADVANCED STABLE: Handles skipped courses and optimizes allocations
- Identifies and reschedules courses with room capacity issues
- Resolves conflicts in existing schedules
- Optimizes venue utilization
- Maintains stability while improving efficiency

SCHEDULING LOGIC ALIGNED WITH MAIN SCHEDULER (regular_timetable_autosheduler):
- FIXED Postgraduate Detection: code-only, leading-zero = diploma NOT PG, first digit ≥ 6 = PG
- FIXED get_course_year: robust, never crashes (no require-program, supports leading-zero diplomas, returns 1 as default)
- FIXED normalize_course_code: strips ALL parentheticals (not just trailing), AGEN0241(A) → AGEN0241
- ENHANCED ConflictTracker:
    - Direct slot index lookup (no precomputed overlap matrix)
    - Elective/selection group/intake exemptions (is_program_year_collision_exempt)
    - program_year_slot_allocs tracking for per-alloc exemption checks
    - add_merged_schedule() with deduplication for merged groups
    - Smart 3-strategy conflict resolution with collision statistics
- ENHANCED VenueAllocator: capacity-relaxed fallback (largest available when nothing fits)
- ENHANCED SchedulerCache: no error-tracking set (allows year detection retries)
- SMART MERGING: validates course names (first 30 chars) before merging, uses norm_code key
- INFRASTRUCTURE: retry_on_lock decorator, safe_print, enable_wal_mode (SQLite WAL)
- PG SLOT PREFERENCE: last slot first (not first-half cutoff) matching main scheduler
"""

import random
import re
import sys
import os
import time
import threading
import traceback
import logging
from collections import defaultdict
from datetime import datetime, date, timedelta, time as dtime
from typing import List, Tuple, Optional, Dict, Set, Any
import heapq
from functools import wraps

scheduler_logger = logging.getLogger("scheduler")

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views import View
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.db import transaction, IntegrityError, OperationalError, connection
from django.db.models import Q, F, Count

from timetable.models import (
    TempTimetable,
    Timetable,
    SchedulerConfig,
    AutoMergedExamGroup,
)
from course_allocation.models import CourseAllocation, CombinedCourseGroup
from room_management.models import Venue
from program_management.models import ProgramCourse, Program
from faculty_management.models import Faculty
from core import scheduling_constraints as constraint_engine

# -----------------------
# Database Lock Retry Decorator
# -----------------------

def retry_on_lock(max_retries=5, delay=0.5):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except OperationalError as e:
                    if 'database is locked' in str(e) and attempt < max_retries - 1:
                        safe_print(f"Database locked, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(delay * (attempt + 1))
                    else:
                        raise
        return wrapper
    return decorator


# -----------------------
# Safe Non-Blocking Print
# -----------------------

_print_lock = threading.Lock()
_log_buffer = []
_MAX_LOG_BUFFER = 200


def safe_print(*args, **kwargs):
    global _log_buffer
    try:
        message = " ".join(str(a) for a in args)
        with _print_lock:
            _log_buffer.append(message)
            if len(_log_buffer) > _MAX_LOG_BUFFER:
                _log_buffer = _log_buffer[-_MAX_LOG_BUFFER:]
        try:
            sys.stdout.write(message + "\n")
            sys.stdout.flush()
        except (IOError, OSError, BlockingIOError):
            pass
        # ── Write to run log file (mirrors the regular scheduler's audit
        # log so Basic/Advanced Stable runs can be diffed the same way) ──
        if _scheduler_log_fh is not None:
            try:
                ts = datetime.now().strftime("%H:%M:%S")
                _scheduler_log_fh.write(f"[{ts}] {message}\n")
            except Exception:
                pass
    except Exception:
        pass


# -----------------------
# Per-run audit log (Basic/Advanced Stable)
# -----------------------
# Every run of either mode writes its own timestamped log file into a
# `logs/` folder alongside this algorithm file. Comparing two consecutive
# Advanced Stable log files is the easiest way to confirm it behaved like a
# "final draft" pass (few/no changes) rather than reshuffling everything.
_ALGO_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR  = os.path.join(_ALGO_DIR, "logs")
_scheduler_log_fh = None   # file handle; None when no run is active


def _open_scheduler_log(mode_label: str) -> None:
    """
    Create (or re-create) the per-run log file.
    File name: stable_scheduler_<mode_label>_YYYY-MM-DD_HH-MM-SS.txt
    """
    global _scheduler_log_fh
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_path = os.path.join(
            _LOG_DIR,
            f"stable_scheduler_{mode_label}_{timestamp}.txt"
        )
        if _scheduler_log_fh is not None:
            try:
                _scheduler_log_fh.close()
            except Exception:
                pass
        _scheduler_log_fh = open(log_path, "w", encoding="utf-8", buffering=1)
        _scheduler_log_fh.write(
            f"Stable Scheduler ({mode_label}) — Run Log\n"
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
    """Flush and close the per-run log file."""
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


# -----------------------
# Enable SQLite WAL Mode
# -----------------------

def enable_wal_mode():
    # SECURITY NOTE: all cursor.execute() calls below use fixed, hardcoded
    # PRAGMA strings — no user input, no string interpolation, no params
    # needed. Audited as SQL-injection-safe; do not add dynamic values here.
    try:
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA cache_size=-20000;")
            cursor.execute("PRAGMA temp_store=MEMORY;")
        safe_print("SQLite WAL mode enabled")
    except Exception as e:
        safe_print(f"Could not enable WAL mode: {e}")


# ============================================
# MODE 1: BASIC STABLE SCHEDULER
# ============================================

# -----------------------
# Progress Tracking for Basic Stable Scheduler
# -----------------------
basic_stable_progress = {
    'status': 'idle',
    'progress': 0,
    'current_action': '',
    'scheduled_count': 0,
    'remaining_count': 0,
    'batch_info': '',
    'total_courses': 0,
    'current_batch': 0,
    'total_batches': 0,
    'message': '',
    'console_output': [],
    'scheduled_courses': [],
    'unscheduled_courses': [],
    'stable_courses_preserved': 0,
    'first_year_scheduled': 0,
    'skipped_rescheduled': 0
}

basic_stable_lock = threading.Lock()


def update_basic_stable_progress(progress: int,
                                current_action: str,
                                scheduled_count: int,
                                remaining_count: int,
                                batch_info: str = "",
                                current_batch: int = 0,
                                total_batches: int = 0,
                                console_message: str = "",
                                scheduled_courses: List = None,
                                unscheduled_courses: List = None,
                                stable_preserved: int = 0,
                                first_year_scheduled: int = 0,
                                skipped_rescheduled: int = 0) -> None:
    """Update the basic stable scheduler progress state."""
    with basic_stable_lock:
        basic_stable_progress.update({
            'progress': int(progress),
            'current_action': current_action,
            'scheduled_count': scheduled_count,
            'remaining_count': remaining_count,
            'batch_info': batch_info,
            'current_batch': current_batch,
            'total_batches': total_batches,
            'stable_courses_preserved': stable_preserved,
            'first_year_scheduled': first_year_scheduled,
            'skipped_rescheduled': skipped_rescheduled
        })

        if console_message:
            basic_stable_progress['console_output'].append(console_message)
            if len(basic_stable_progress['console_output']) > 50:
                basic_stable_progress['console_output'] = basic_stable_progress['console_output'][-50:]
        
        if scheduled_courses is not None:
            basic_stable_progress['scheduled_courses'] = scheduled_courses
        
        if unscheduled_courses is not None:
            basic_stable_progress['unscheduled_courses'] = unscheduled_courses


# ============================================
# MODE 2: ADVANCED STABLE SCHEDULER (Optimized)
# ============================================

# -----------------------
# Progress Tracking for Advanced Stable Scheduler
# -----------------------
advanced_stable_progress = {
    'status': 'idle',
    'progress': 0,
    'current_action': '',
    'scheduled_count': 0,
    'remaining_count': 0,
    'batch_info': '',
    'total_courses': 0,
    'current_batch': 0,
    'total_batches': 0,
    'message': '',
    'console_output': [],
    'scheduled_courses': [],
    'unscheduled_courses': [],
    'stable_courses_preserved': 0,
    'courses_optimized': 0,
    'capacity_issues_fixed': 0,
    'conflicts_resolved': 0,
    'skipped_rescheduled': 0
}

advanced_stable_lock = threading.Lock()


def update_advanced_stable_progress(progress: int,
                                   current_action: str,
                                   scheduled_count: int,
                                   remaining_count: int,
                                   batch_info: str = "",
                                   current_batch: int = 0,
                                   total_batches: int = 0,
                                   console_message: str = "",
                                   scheduled_courses: List = None,
                                   unscheduled_courses: List = None,
                                   stable_preserved: int = 0,
                                   courses_optimized: int = 0,
                                   capacity_issues_fixed: int = 0,
                                   conflicts_resolved: int = 0,
                                   skipped_rescheduled: int = 0) -> None:
    """Update the advanced stable scheduler progress state."""
    with advanced_stable_lock:
        advanced_stable_progress.update({
            'progress': int(progress),
            'current_action': current_action,
            'scheduled_count': scheduled_count,
            'remaining_count': remaining_count,
            'batch_info': batch_info,
            'current_batch': current_batch,
            'total_batches': total_batches,
            'stable_courses_preserved': stable_preserved,
            'courses_optimized': courses_optimized,
            'capacity_issues_fixed': capacity_issues_fixed,
            'conflicts_resolved': conflicts_resolved,
            'skipped_rescheduled': skipped_rescheduled
        })

        if console_message:
            advanced_stable_progress['console_output'].append(console_message)
            if len(advanced_stable_progress['console_output']) > 50:
                advanced_stable_progress['console_output'] = advanced_stable_progress['console_output'][-50:]
        
        if scheduled_courses is not None:
            advanced_stable_progress['scheduled_courses'] = scheduled_courses
        
        if unscheduled_courses is not None:
            advanced_stable_progress['unscheduled_courses'] = unscheduled_courses


# -----------------------
# Common Helper Functions (shared by both modes)
# -----------------------

def is_postgraduate_course(course_allocation) -> bool:
    """
    Determine if a course is postgraduate based SOLELY on its course code.

    Rules:
    1. Strip whitespace, uppercase.
    2. Remove ALL parenthetical groups: (A), (B), (HONS), etc.
    3. Find the FIRST contiguous digit sequence.
    4. If it starts with a leading zero → diploma/certificate → NOT PG.
    5. If first digit >= 6 → PG.
    6. Otherwise → NOT PG.

    Examples:
      PG:     COSC601, MBAD801, CPSY821, DBAM930, MSCF813
      NOT PG: BCOM112, ECON222, AGEN0241, AGEN0241(A), DIBM0103
    """
    course_code = course_allocation.course_code or ""
    if not course_code:
        return False

    raw = course_code.strip().upper()
    cleaned = re.sub(r'\([^)]*\)', '', raw)
    cleaned = re.sub(r'\s+', '', cleaned)

    m = re.search(r'(\d+)', cleaned)
    if not m:
        return False

    numeric_part = m.group(1)
    if numeric_part.startswith('0'):
        return False

    return int(numeric_part[0]) >= 6


def get_course_year(course_allocation) -> int:
    """
    Robust course year detection.
    Returns int 1-6. Returns 1 as default only after all strategies exhausted.
    Raises ValueError only if truly impossible (missing code and program).
    """
    course_code = course_allocation.course_code or ""
    program     = course_allocation.program

    if not course_code:
        raise ValueError(f"Missing course code for allocation {course_allocation.id}")

    try:
        # Preferred: direct program_course FK -- exact regardless of what's
        # stored in course_code (e.g. a disambiguated "ZOOL 143(COM)" label
        # for a course shared by multiple programs).
        pc = getattr(course_allocation, "program_course", None)
        if pc and pc.year and str(pc.year).isdigit():
            year = int(pc.year)
            if 1 <= year <= 6:
                return year

        # Fallback for legacy allocations without a program_course link:
        # strip a trailing "(TAG)" disambiguation suffix before matching.
        bare_code = re.sub(r'\([A-Z0-9]+\)\s*$', '', course_code, flags=re.IGNORECASE).strip()

        if program:
            program_courses = ProgramCourse.objects.filter(
                program=program,
                course_code__iexact=bare_code
            )
            if program_courses.exists():
                pc = program_courses.first()
                if pc.year and str(pc.year).isdigit():
                    year = int(pc.year)
                    if 1 <= year <= 6:
                        return year

        normalized_code = bare_code.upper().strip()
        year_match = re.search(r'^[A-Z]*(\d)(?:\d{2})', normalized_code)
        if year_match:
            year = int(year_match.group(1))
            if 1 <= year <= 6:
                return year

        if is_postgraduate_course(course_allocation):
            return 6  # PG: treat as year 6

        # Fallback: diploma/certificate codes with 4-digit numbers starting with 0
        m = re.search(r'0(\d)\d{2}', normalized_code)
        if m:
            year = int(m.group(1))
            if 1 <= year <= 6:
                return year

        # Default for unresolvable cases
        return 1

    except Exception as e:
        safe_print(f"Year detection warning for {course_code}: {e}")
        return 1


def get_course_slot_preference(course_allocation, slots: List[Tuple[dtime, dtime]]) -> List[int]:
    """
    Determine preferred time slots based on course level.
    PG courses: last slot first, working backwards (afternoon preference).
    UG courses: all slots in order.
    """
    n = len(slots)
    if is_postgraduate_course(course_allocation):
        # Last slot first → second-to-last → ... → first (afternoon preference)
        return list(range(n - 1, -1, -1))
    else:
        return list(range(n))


def lecturer_priority(lecturer) -> int:
    """Prof/Dr → higher priority (1)."""
    if not lecturer:
        return 3
    if getattr(lecturer, "designation", "").lower() in ["prof", "dr"]:
        return 1
    return 2


def get_course_faculty(course_allocation) -> Optional[Faculty]:
    """Determine the faculty for a course allocation."""
    try:
        if course_allocation.program and course_allocation.program.department:
            return course_allocation.program.department.faculty
        
        if course_allocation.lecturer and course_allocation.lecturer.department:
            return course_allocation.lecturer.department.faculty
            
        if course_allocation.department:
            return course_allocation.department.faculty
            
    except Exception as e:
        print(f"Warning: Could not determine faculty for course {course_allocation.course_code}: {e}")
    return None


def normalize_course_code(raw_code: str) -> str:
    """
    Normalise a course code for merge grouping.
    Strips ALL parenthetical suffixes and whitespace.
    AGEN0241(A) → AGEN0241
    COSC 312 (C) → COSC312
    """
    if not raw_code:
        return ""
    code = raw_code.strip().upper()
    code = re.sub(r"\([^)]*\)", "", code)   # Remove ALL parenthetical groups
    code = re.sub(r"\s+", "", code)
    return code


# ── Section/stream suffix helpers — ported from the regular scheduler's
# normalize_course_code_base machinery so the stable scheduler recognises
# split sections of ONE course (e.g. 'COSC 103-D' / 'COSC 103-F', or
# 'ECON 313 GROUP B' / 'ECON 313 b') as the SAME course instead of two
# different ones. Kept as new, additive helpers rather than edits to
# normalize_course_code() above, which is left untouched to avoid changing
# its existing merge-grouping behaviour. ──────────────────────────────────
_SECTION_SUFFIX_RE = re.compile(r"([-_][A-Z0-9]{1,3})$")

_GROUP_WORD_SUFFIX_RE = re.compile(
    r"\s+(?:GROUP|GRP|SECTION|SEC|STREAM)\s*[A-Z0-9]{1,3}$", re.IGNORECASE
)
_BARE_TRAILING_SECTION_RE = re.compile(r"\s+[A-Za-z][A-Za-z0-9]?$")


def strip_group_section_words(raw_code: str) -> str:
    """Strip a trailing word-style section/stream tag ("GROUP C", "GRP B",
    "SECTION A", or a bare trailing letter like "b"/"A") from a raw course
    code string that still has its original spacing. Mirrors
    strip_group_section_words in the regular scheduler.
    """
    if not raw_code:
        return raw_code
    code = raw_code.strip()
    m = _GROUP_WORD_SUFFIX_RE.search(code)
    if m and code[:m.start()].strip():
        return code[:m.start()]
    m = _BARE_TRAILING_SECTION_RE.search(code)
    if m and code[:m.start()].strip():
        return code[:m.start()]
    return code


def split_course_code_suffix(code: str) -> Tuple[str, str]:
    """Split an already-uppercased, whitespace-stripped code into
    (base_code, suffix), where `suffix` is a trailing section/stream tag
    introduced by '-' or '_'. Mirrors split_course_code_suffix in the
    regular scheduler.
    """
    m = _SECTION_SUFFIX_RE.search(code)
    if not m:
        return code, ""
    return code[:m.start()], m.group(1)


def normalize_course_code_base(raw_code: str) -> str:
    """Like normalize_course_code(), but with any trailing section/stream
    tag stripped entirely — both '-A'/'_C' style and word-style 'GROUP C' /
    'GRP B' / 'SECTION A' / bare-letter 'b' style. Used ONLY to detect
    split sections of the same course for collision-exemption purposes
    (see _is_same_base_course_pair); normalize_course_code() itself is
    left unchanged so existing merge-grouping behaviour is unaffected.
    """
    stripped = strip_group_section_words(raw_code)
    base, _suffix = split_course_code_suffix(normalize_course_code(stripped))
    return base


def _is_same_base_course_pair(alloc_a, alloc_b) -> bool:
    """
    True when alloc_a and alloc_b share the same base course code — e.g.
    'COSC 103-D' / 'COSC 103-F', or two rows both simply coded
    'COMS 101'. This is one course SPLIT into multiple sections/streams,
    not two different courses — every section teaches a disjoint subset
    of the same program-year's students, so sections running concurrently
    is never a program-year collision.

    Kept IN SYNC with `_is_same_base_course_pair` in the regular
    scheduler (regular_timetable_autosheduler_algorithm.py) and
    timetable/timetable_panel.py.
    """
    code_a = getattr(alloc_a, 'course_code', None)
    code_b = getattr(alloc_b, 'course_code', None)
    if not code_a or not code_b:
        return False
    return normalize_course_code_base(code_a) == normalize_course_code_base(code_b)


def apply_combined_course_group_pass(courses_to_schedule: List) -> Tuple[List[Dict], Set[int]]:
    """
    Honour COD-panel-defined CombinedCourseGroup records BEFORE any
    heuristic (norm_code, lecturer) merging runs.

    The stable scheduler previously had no awareness of CombinedCourseGroup
    at all — it only ever inferred merges from matching normalized course
    code + lecturer, which misses cross-listed sections that share a
    lecture but have *different* lecturers per specialization (a common
    pattern here), and can also merge things that were never meant to be
    merged. This mirrors the regular scheduler's build_global_merged_tasks
    Pass 1: CombinedCourseGroup is the single source of truth for "these
    allocations are one physical lecture."

    Returns:
        (combined_tasks, consumed_alloc_ids)
        combined_tasks     — task dicts for groups with >=2 allocations
                              present in this run, each carrying
                              'combined_group': True so downstream code
                              knows NOT to create a duplicate
                              AutoMergedExamGroup for it and Phase 6 knows
                              to exempt it from venue swapping.
        consumed_alloc_ids — every CourseAllocation id claimed by a group,
                              to be removed from the pool before the
                              heuristic merge pass runs on what's left.
    """
    alloc_by_id = {a.id: a for a in courses_to_schedule}
    combined_tasks: List[Dict] = []
    consumed_alloc_ids: Set[int] = set()

    try:
        combined_groups = list(
            CombinedCourseGroup.objects.prefetch_related('allocations').all()
        )
    except Exception as exc:
        safe_print(f"[CombinedCourseGroup] WARNING – could not load groups: {exc}")
        return combined_tasks, consumed_alloc_ids

    for cg in combined_groups:
        group_allocs = [
            alloc_by_id[a.id] for a in cg.allocations.all() if a.id in alloc_by_id
        ]
        if len(group_allocs) < 2:
            # 0 or 1 allocations from this group are in the current run —
            # nothing to merge; leave it for normal individual scheduling.
            continue

        total_students = sum(a.number_of_students or 0 for a in group_allocs)
        norm_code = normalize_course_code(cg.base_course_code)

        safe_print(
            f"[CombinedCourseGroup] '{cg.group_code}' ({norm_code}) "
            f"x{len(group_allocs)} allocations, {total_students} students total → ONE slot"
        )

        combined_tasks.append({
            'merged': group_allocs,
            'total_students': total_students,
            'group_id': cg.id,
            'norm_code': norm_code,
            'combined_group': cg,
        })
        for a in group_allocs:
            consumed_alloc_ids.add(a.id)

    if combined_tasks:
        safe_print(
            f"[CombinedCourseGroup] {len(combined_tasks)} combined group(s) honoured, "
            f"{len(consumed_alloc_ids)} allocation(s) removed from the heuristic-merge pool"
        )

    return combined_tasks, consumed_alloc_ids


def get_combined_course_group_alloc_ids() -> Set[int]:
    """Every CourseAllocation ID that belongs to a CombinedCourseGroup.
    Used to exempt these from Phase 6 venue swapping, same treatment as
    AutoMergedExamGroup entries."""
    try:
        return set(
            CombinedCourseGroup.objects.values_list('allocations__id', flat=True).distinct()
        )
    except Exception:
        return set()


def build_lecturer_blocked_slot_map(
    days: List[str],
    slots: List[Tuple[dtime, dtime]],
) -> Dict[int, Dict[str, Any]]:
    """
    Converts core.scheduling_constraints.get_lecturer_blocked_ranges() (raw
    day/time ranges) into {lecturer_id: {day: 'ALL_DAY' | {slot_index,...}}},
    matched against this run's own generated `slots` list, for
    ConflictTracker.has_lecturer_conflict to consult directly.

    Kept in parity with the regular scheduler's build_lecturer_blocked_slot_map
    so the stable scheduler (Basic and Advanced) never places a lecturer in a
    slot the regular scheduler would treat as hard-blocked.
    """
    try:
        raw = constraint_engine.get_lecturer_blocked_ranges()
    except Exception as e:
        safe_print(f"Warning: could not load lecturer-blocked ranges: {e}")
        return {}

    blocked_map: Dict[int, Dict[str, Any]] = {}
    for lecturer_id, ranges in raw.items():
        day_map = blocked_map.setdefault(lecturer_id, {})
        for day, start, end in ranges:
            if day not in days:
                continue
            if day_map.get(day) == 'ALL_DAY':
                continue
            if start is None or end is None:
                day_map[day] = 'ALL_DAY'
                continue
            idx_set = day_map.get(day)
            if not isinstance(idx_set, set):
                idx_set = set()
                day_map[day] = idx_set
            for idx, (s, e) in enumerate(slots):
                if s < end and start < e:   # overlap test
                    idx_set.add(idx)
    return blocked_map


def generate_slots(start_time: dtime, end_time: dtime, slot_size_hours: int) -> List[Tuple[dtime, dtime]]:
    """Generate all possible time slots."""
    slots: List[Tuple[dtime, dtime]] = []
    today = date.today()
    current = datetime.combine(today, start_time)
    end_dt = datetime.combine(today, end_time)
    while current + timedelta(hours=slot_size_hours) <= end_dt:
        nxt = current + timedelta(hours=slot_size_hours)
        slots.append((current.time(), nxt.time()))
        current = nxt
    return slots


# -----------------------
# Scheduler Cache Class
# -----------------------

class SchedulerCache:
    def __init__(self):
        self.venue_cache            = None
        self.faculty_venues_map     = None
        self.program_courses_cache  = None
        self.course_year_cache      = {}
        self.existing_timetable_entries = set()

    def clear(self):
        self.venue_cache            = None
        self.faculty_venues_map     = None
        self.program_courses_cache  = None
        self.course_year_cache.clear()
        self.existing_timetable_entries.clear()

    def get_all_venues(self):
        if self.venue_cache is None:
            self.venue_cache = list(
                Venue.objects.select_related('building__faculty').all().order_by('capacity')
            )
        return self.venue_cache

    def get_faculty_venues_map(self):
        if self.faculty_venues_map is None:
            venues_map = defaultdict(list)
            for venue in self.get_all_venues():
                if venue.building and venue.building.faculty:
                    venues_map[venue.building.faculty].append(venue)
            self.faculty_venues_map = dict(venues_map)
        return self.faculty_venues_map

    def get_course_year(self, course_allocation) -> int:
        cache_key = str(course_allocation.id)
        if cache_key in self.course_year_cache:
            return self.course_year_cache[cache_key]
        year = get_course_year(course_allocation)
        self.course_year_cache[cache_key] = year
        return year

    def is_duplicate_entry(self, venue_id, day, start_time, end_time) -> bool:
        return (venue_id, day, start_time, end_time) in self.existing_timetable_entries

    def load_existing_temp_entries(self):
        entries = TempTimetable.objects.values_list('venue_id', 'day', 'start_time', 'end_time')
        self.existing_timetable_entries = set(entries)

    def add_to_temp_cache(self, venue_id, day, start_time, end_time):
        """Add a new entry to the cache after successful insertion"""
        self.existing_timetable_entries.add((venue_id, day, start_time, end_time))


# -----------------------
# Conflict Tracker Class
# -----------------------

# Smart Conflict Exemption Helpers
def _get_alloc_intake(alloc) -> str:
    """Return the intake value ('normal'/'special') for this allocation."""
    return getattr(alloc, 'intake', 'normal') or 'normal'


def _is_elective(alloc) -> bool:
    """Return True if this allocation is marked as an elective/selection course."""
    return bool(getattr(alloc, 'is_elective', False))


def _get_selection_group_id(alloc) -> Optional[int]:
    """Return the SelectionGroup PK for this allocation, or None."""
    try:
        sg = getattr(alloc, 'selection_group', None)
        return sg.id if sg else None
    except Exception:
        return None


def _get_specialization_stem_id(alloc) -> Optional[int]:
    """Return the SpecializationStem PK for this allocation, or None."""
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.id if st else None
    except Exception:
        return None


def _get_specialization_category_id(alloc) -> Optional[int]:
    """Return the SpecializationStem's category PK for this allocation, or None."""
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.category_id if st else None
    except Exception:
        return None


def _get_alloc_semester(alloc):
    """Return the ProgramCourse semester (1 or 2) for an allocation, or None."""
    pc = getattr(alloc, 'program_course', None)
    return getattr(pc, 'semester', None) if pc else None


def is_program_year_collision_exempt(alloc_a, alloc_b) -> bool:
    """
    Return True when placing alloc_a and alloc_b in the SAME timeslot does NOT
    constitute a program-year collision.

    Exemption rules (checked in this order):
    0. SAME BASE COURSE (split section) — checked FIRST and wins outright,
       overriding every other rule below. See _is_same_base_course_pair.
    1. SPECIALIZATION STEM (Combination Set) — takes priority over everything
       below. A student picks ONE stem and takes EVERY course in it, so:
         * same stem            → NEVER exempt, even if a course is *also*
           flagged Elective (any two courses in a stem can still land in the
           same student's chosen subset, no matter how many of the stem's
           courses are individually optional).
         * different stems, same category → exempt (student never takes
           both stems).
         * different categories / only one side has a stem → falls through.
    1b. STUDENT GROUP — if both courses have a student_group set and it
        differs between them, they belong to different cohorts of the same
        program/year (e.g. Group A vs Group B) and never share students →
        exempt. Same group on both sides, or either side shared
        (student_group=None), falls through to the checks below.
    2. ELECTIVE / SELECTION GROUP — if either course is an elective OR if
       either course belongs to any SelectionGroup, students pick at most one.
    3. DIFFERENT INTAKE — same program-year but different intake cohorts
       (normal vs special) never share students.
    """
    # Rule 0: Same base course code — a split section of ONE course, not a
    # collision between two different courses. Checked first and wins
    # outright, overriding every other rule below.
    if _is_same_base_course_pair(alloc_a, alloc_b):
        return True

    # Different ProgramCourse semester (1 vs 2) — but ONLY exempt when
    # paired with a different intake (special is a shifted-semester
    # cohort of the same year). Same intake + different semester is NOT
    # exempt. Kept in sync with timetable.timetable_panel.is_scheduling_exempt.
    sem_a = _get_alloc_semester(alloc_a)
    sem_b = _get_alloc_semester(alloc_b)
    if (
        sem_a is not None and sem_b is not None and sem_a != sem_b
        and _get_alloc_intake(alloc_a) != _get_alloc_intake(alloc_b)
    ):
        return True

    # Rule 1: Specialization stem (priority — see docstring)
    st_a = _get_specialization_stem_id(alloc_a)
    st_b = _get_specialization_stem_id(alloc_b)
    if st_a is not None and st_b is not None and st_a == st_b:
        return False
    if st_a is not None and st_b is not None and st_a != st_b:
        cat_a = _get_specialization_category_id(alloc_a)
        cat_b = _get_specialization_category_id(alloc_b)
        if cat_a is not None and cat_a == cat_b:
            return True

    # Rule 1b: StudentGroup — different groups within the same program/year
    # are different cohorts, so their compulsory courses can run concurrently.
    # The SAME group's own courses still clash-check normally. A shared course
    # (student_group=None) is attended by everyone and keeps clashing with
    # every group's courses, so it deliberately falls through untouched here.
    sgrp_a = getattr(alloc_a, 'student_group_id', None)
    sgrp_b = getattr(alloc_b, 'student_group_id', None)
    if sgrp_a is not None and sgrp_b is not None and sgrp_a != sgrp_b:
        return True

    # Rule 2: Selection group — only exempt when BOTH sides sit in the
    # SAME SelectionGroup (students pick exactly one course from that
    # group, so two courses in it can never both be taken by one
    # student). Being merely `is_elective=True`, or belonging to two
    # DIFFERENT selection groups, is NOT exempt — a student can pick one
    # course from group A and one from group B, so those must still
    # clash-check normally.
    sg_a = _get_selection_group_id(alloc_a)
    sg_b = _get_selection_group_id(alloc_b)
    if sg_a is not None and sg_b is not None and sg_a == sg_b:
        return True

    # Rule 3: Different intake
    if _get_alloc_intake(alloc_a) != _get_alloc_intake(alloc_b):
        return True

    return False


class ConflictTracker:
    def __init__(self, days, slots, lecturer_blocked: Optional[Dict] = None):
        self.days  = days
        self.slots = slots
        self.lecturer_schedule         = defaultdict(lambda: defaultdict(set))
        self.program_year_schedule     = defaultdict(lambda: defaultdict(set))
        self.venue_schedule            = defaultdict(lambda: defaultdict(set))
        self.program_year_daily_count  = defaultdict(lambda: defaultdict(int))
        # Track which allocations occupy each (program, year, day, slot)
        # so exemption rules can be evaluated per-alloc
        self.program_year_slot_allocs: Dict[tuple, list] = defaultdict(list)
        self.collisions_detected = 0
        self.collisions_resolved = 0
        # ── Hard lecturer-blocked day/slot constraints (kept in sync with the
        # regular scheduler's build_lecturer_blocked_slot_map) ─────────────
        # {lecturer_id: {day: 'ALL_DAY' | {slot_index, ...}}}
        self.lecturer_blocked: Dict = lecturer_blocked or {}
        safe_print(f"ConflictTracker initialized with {len(days)} days and {len(slots)} slots")

    def has_lecturer_conflict(self, lecturer_id, day, slot_index) -> bool:
        if not lecturer_id:
            return False
        if slot_index in self.lecturer_schedule[lecturer_id][day]:
            return True
        day_block = self.lecturer_blocked.get(lecturer_id, {}).get(day)
        if day_block == 'ALL_DAY':
            return True
        if day_block and slot_index in day_block:
            return True
        return False

    def has_program_conflict(self, program_id, year, day, slot_index,
                              new_alloc=None) -> bool:
        """
        Return True if scheduling new_alloc (or any course for program_id/year)
        at (day, slot_index) would cause a REAL program-year collision.

        If new_alloc is provided and all existing occupants at that slot are
        exempt from collision (different intake / elective / selection group),
        the slot is considered free for this allocation.
        """
        if not program_id:
            return False
        if slot_index not in self.program_year_schedule[(program_id, year)][day]:
            return False
        # Fast path: if no alloc context given, treat as conflict (conservative)
        if new_alloc is None:
            return True
        # Check every existing allocation at this slot for exemption
        existing = self.program_year_slot_allocs.get((program_id, year, day, slot_index), [])
        for existing_alloc in existing:
            if not is_program_year_collision_exempt(new_alloc, existing_alloc):
                return True  # At least one non-exempt occupant -> real conflict
        return False  # All occupants are exempt -> no conflict

    def has_venue_conflict(self, venue_id, day, slot_index) -> bool:
        if not venue_id:
            return False
        return slot_index in self.venue_schedule[venue_id][day]

    def can_schedule_program_year(self, program_id, year, day, max_per_day=2) -> bool:
        if not program_id:
            return True
        return self.program_year_daily_count[(program_id, year)][day] < max_per_day

    def get_program_year_day_load(self, program_id, year, day) -> int:
        if not program_id:
            return 0
        return self.program_year_daily_count[(program_id, year)][day]

    def get_best_day_for_program_year(self, program_id, year, preferred_day) -> str:
        if not program_id:
            return preferred_day
        min_load = float('inf')
        best_day = preferred_day
        for day in self.days:
            load = self.get_program_year_day_load(program_id, year, day)
            if load < min_load:
                min_load = load
                best_day = day
        return best_day

    def add_schedule(self, lecturer_id, program_id, year, venue_id, day, slot_index,
                     course_code=None, alloc=None):
        if lecturer_id:
            self.lecturer_schedule[lecturer_id][day].add(slot_index)
        if program_id:
            self.program_year_schedule[(program_id, year)][day].add(slot_index)
            self.program_year_daily_count[(program_id, year)][day] += 1
            if alloc is not None:
                self.program_year_slot_allocs[(program_id, year, day, slot_index)].append(alloc)
        if venue_id:
            self.venue_schedule[venue_id][day].add(slot_index)

    def add_merged_schedule(self, allocs, venue_id, day, slot_index, cache):
        """Register all allocations in a merged group.

        For merged groups where multiple allocs share the same (program_id, year),
        the daily_count and slot-entry for a (program, year, day, slot) combination
        is only registered ONCE to prevent false conflicts in future scheduling.
        """
        seen_prog_year_slot: Set[tuple] = set()
        for alloc in allocs:
            lid = alloc.lecturer.id if alloc.lecturer else None
            pid = alloc.program.id if alloc.program else None
            yr  = cache.get_course_year(alloc)

            # Always register lecturer and venue
            if lid:
                self.lecturer_schedule[lid][day].add(slot_index)
            if venue_id:
                self.venue_schedule[venue_id][day].add(slot_index)

            # Register program-year ONCE per (program, year, day, slot) combination
            if pid:
                prog_slot_key = (pid, yr, day, slot_index)
                self.program_year_schedule[(pid, yr)][day].add(slot_index)
                self.program_year_slot_allocs[prog_slot_key].append(alloc)
                if prog_slot_key not in seen_prog_year_slot:
                    self.program_year_daily_count[(pid, yr)][day] += 1
                    seen_prog_year_slot.add(prog_slot_key)

    def resolve_program_conflict(self, program_id, year, day, slot_index,
                                   lecturer_id=None, new_alloc=None):
        self.collisions_detected += 1
        max_attempts = 50
        attempts     = 0

        # Strategy 1: Same day, nearby slots
        for offset in [1, -1, 2, -2, 3, -3]:
            new_slot = slot_index + offset
            if 0 <= new_slot < len(self.slots):
                if (not self.has_program_conflict(program_id, year, day, new_slot, new_alloc=new_alloc) and
                        (not lecturer_id or not self.has_lecturer_conflict(lecturer_id, day, new_slot)) and
                        self.can_schedule_program_year(program_id, year, day)):
                    self.collisions_resolved += 1
                    return True, day, new_slot
            attempts += 1

        # Strategy 2: Different days, same slot
        current_day_index = self.days.index(day) if day in self.days else 0
        for offset_range in range(1, len(self.days)):
            for direction in [1, -1]:
                new_index = (current_day_index + (offset_range * direction)) % len(self.days)
                new_day   = self.days[new_index]
                if (not self.has_program_conflict(program_id, year, new_day, slot_index, new_alloc=new_alloc) and
                        (not lecturer_id or not self.has_lecturer_conflict(lecturer_id, new_day, slot_index)) and
                        self.can_schedule_program_year(program_id, year, new_day)):
                    self.collisions_resolved += 1
                    return True, new_day, slot_index

        # Strategy 3: Full scan
        day_scores = sorted(
            [(self.get_program_year_day_load(program_id, year, d), d) for d in self.days]
        )
        for _, test_day in day_scores:
            for test_slot in range(len(self.slots)):
                attempts += 1
                if attempts > max_attempts:
                    return False, day, slot_index
                if test_day == day and test_slot == slot_index:
                    continue
                if (not self.has_program_conflict(program_id, year, test_day, test_slot, new_alloc=new_alloc) and
                        (not lecturer_id or not self.has_lecturer_conflict(lecturer_id, test_day, test_slot)) and
                        self.can_schedule_program_year(program_id, year, test_day)):
                    self.collisions_resolved += 1
                    return True, test_day, test_slot

        return False, day, slot_index

    def get_collision_stats(self):
        return {
            'detected':        self.collisions_detected,
            'resolved':        self.collisions_resolved,
            'unresolved':      self.collisions_detected - self.collisions_resolved,
            'resolution_rate': (self.collisions_resolved / self.collisions_detected * 100)
                               if self.collisions_detected > 0 else 100.0
        }


# -----------------------
# Venue Allocator Class
# -----------------------

class VenueAllocator:
    def __init__(self, venues, days, slots, conflict_tracker):
        self.venues          = venues
        self.days            = days
        self.slots           = slots
        self.conflict_tracker = conflict_tracker
        self.venues_by_capacity = sorted(venues, key=lambda x: x.capacity or 0)

    def find_efficient_venue(self, total_students: int, available_venues: List[Venue],
                             day: str, slot_index: int, preference: str = "best_fit") -> Optional[Venue]:
        if not available_venues:
            return None
        suitable = [
            v for v in available_venues
            if (v.capacity or 0) >= total_students
            and not self.conflict_tracker.has_venue_conflict(v.id, day, slot_index)
        ]
        if not suitable:
            # Capacity-relaxed fallback: largest available
            any_free = [
                v for v in available_venues
                if not self.conflict_tracker.has_venue_conflict(v.id, day, slot_index)
            ]
            if any_free:
                return max(any_free, key=lambda x: x.capacity or 0)
            return None

        if preference == "best_fit":
            return min(suitable, key=lambda x: x.capacity or float('inf'))
        elif preference == "largest_first":
            return max(suitable, key=lambda x: x.capacity or 0)
        else:
            return random.choice(suitable)




# -----------------------
# Basic Stable Schedule Loader (Mode 1)
# -----------------------

class BasicStableScheduleLoader:
    """Loads and preserves existing stable schedules from Timetable model"""
    
    def __init__(self, conflict_tracker, cache):
        self.conflict_tracker = conflict_tracker
        self.cache = cache
        self.stable_courses = {}  # course_allocation_id -> (day, slot_index, venue, start_time, end_time)
        self.stable_merged_groups = {}  # merged_group_id -> course_ids
        self.stable_count = 0
        self.first_year_stable = 0
        self.other_year_stable = 0
        
    def load_stable_schedules(self):
        """
        Load all approved schedules from Timetable model
        These are considered STABLE and should be preserved
        """
        print("Loading stable schedules from Timetable...")
        
        # Load individual course allocations from Timetable
        timetable_entries = Timetable.objects.select_related(
            'course_allocation', 'venue', 'course_allocation__program'
        ).all()
        
        for entry in timetable_entries:
            course = entry.course_allocation
            if not course:
                continue
                
            try:
                year = self.cache.get_course_year(course)
                
                # Find slot index
                slot_index = None
                for idx, (slot_start, slot_end) in enumerate(self.conflict_tracker.slots):
                    if (entry.start_time == slot_start and entry.end_time == slot_end):
                        slot_index = idx
                        break
                
                if slot_index is None:
                    print(f"Warning: Could not find slot index for {course.course_code} at {entry.start_time}-{entry.end_time}")
                    continue
                
                # Store stable course info
                self.stable_courses[course.id] = {
                    'day': entry.day,
                    'slot_index': slot_index,
                    'venue': entry.venue,
                    'start_time': entry.start_time,
                    'end_time': entry.end_time,
                    'year': year
                }
                
                # Add to conflict tracker to preserve constraints
                lecturer_id = course.lecturer.id if course.lecturer else None
                program_id = course.program.id if course.program else None
                
                self.conflict_tracker.add_schedule(
                    lecturer_id,
                    program_id,
                    year,
                    entry.venue.id,
                    entry.day,
                    slot_index,
                    course.course_code,
                    alloc=course
                )
                
                self.stable_count += 1
                if year == 1:
                    self.first_year_stable += 1
                else:
                    self.other_year_stable += 1
                    
            except Exception as e:
                print(f"Error loading stable course {course.course_code}: {e}")
        
        # Load merged exam groups
        merged_groups = AutoMergedExamGroup.objects.filter(
            date__isnull=False,
            start_time__isnull=False,
            end_time__isnull=False,
            venue__isnull=False
        ).prefetch_related('merged_courses')
        
        for group in merged_groups:
            try:
                # Find slot index
                slot_index = None
                for idx, (slot_start, slot_end) in enumerate(self.conflict_tracker.slots):
                    if group.start_time and group.end_time:
                        if group.start_time == slot_start and group.end_time == slot_end:
                            slot_index = idx
                            break
                
                if slot_index is None or not group.date or not group.venue:
                    continue
                
                # Store merged group info
                course_ids = [c.id for c in group.merged_courses.all()]
                self.stable_merged_groups[group.id] = course_ids
                
                # Add each course in merged group to conflict tracker
                for course in group.merged_courses.all():
                    try:
                        year = self.cache.get_course_year(course)
                        lecturer_id = course.lecturer.id if course.lecturer else None
                        program_id = course.program.id if course.program else None
                        
                        self.conflict_tracker.add_schedule(
                            lecturer_id,
                            program_id,
                            year,
                            group.venue.id,
                            group.date,  # Using date as day identifier
                            slot_index,
                            course.course_code,
                            alloc=course
                        )
                        
                        # Also store as stable if not already in individual entries
                        if course.id not in self.stable_courses:
                            self.stable_courses[course.id] = {
                                'day': group.date,
                                'slot_index': slot_index,
                                'venue': group.venue,
                                'start_time': group.start_time,
                                'end_time': group.end_time,
                                'year': year,
                                'merged_group_id': group.id
                            }
                            self.stable_count += 1
                            if year == 1:
                                self.first_year_stable += 1
                            else:
                                self.other_year_stable += 1
                                
                    except Exception as e:
                        print(f"Error processing merged course: {e}")
                        
            except Exception as e:
                print(f"Error loading merged group {group.id}: {e}")
        
        safe_print(f"Loaded {self.stable_count} stable schedules (Year 1: {self.first_year_stable}, Other: {self.other_year_stable})")
        return self.stable_courses, self.stable_merged_groups
    
    def is_course_stable(self, course_allocation_id):
        """Check if a course already has a stable schedule"""
        return course_allocation_id in self.stable_courses
    
    def get_stable_schedule(self, course_allocation_id):
        """Get stable schedule for a course"""
        return self.stable_courses.get(course_allocation_id)


# -----------------------
# Advanced Stable Schedule Loader with Optimization (Mode 2)
# -----------------------

class AdvancedStableScheduleLoader:
    """
    Advanced loader that identifies courses needing optimization:
    - Courses with room capacity issues (too small or too large gap)
    - Courses with conflicts
    - Skipped courses
    - Courses whose underlying allocation changed since the last publish
      (added, split into a different set of groups, merged, or reassigned
      to a different lecturer) — detected by comparing each course's
      CURRENT expected merge-partner set against what's actually reflected
      on the Timetable/AutoMergedExamGroup entry.

    IMPORTANT: First-year courses are NOT automatically forced into
    "needs optimization" here. Advanced Stable is meant to behave like a
    final-draft pass — on a second consecutive run (no underlying data
    changes), it should find nearly everything already stable and leave it
    alone. Year 1 gets no special treatment in this loader; only genuine
    capacity issues, conflicts, or allocation changes trigger a rebuild.
    """
    
    def __init__(self, conflict_tracker, cache, merge_limit: int = 200):
        self.conflict_tracker = conflict_tracker
        self.cache = cache
        self.merge_limit = merge_limit
        self.stable_courses = {}  # Perfectly scheduled courses to preserve
        self.courses_to_optimize = {}  # Courses needing optimization
        self.stable_merged_groups = {}  # Perfect merged groups to preserve
        self.merged_groups_to_optimize = {}  # Merged groups needing optimization
        # course_id -> set of other course_ids it should currently be
        # merged with (same normalized code + same lecturer, within
        # merge_limit combined students). Built lazily from live
        # CourseAllocation data the first time it's needed.
        self._expected_merge_partners: Optional[Dict[int, Set[int]]] = None
        
        self.stable_count = 0
        self.optimize_count = 0
        self.capacity_issues = 0
        self.conflict_issues = 0
        
    def check_capacity_efficiency(self, course, venue, students):
        """Check if venue capacity is efficient (not too small, not too large gap)"""
        if not venue or not venue.capacity:
            return False, "No venue capacity data"
        
        capacity = venue.capacity
        gap = capacity - students
        
        # Define efficiency thresholds
        if students > capacity:
            return False, f"OVERFLOW: {students} > {capacity}"
        elif gap > 50:  # Too much wasted space
            return False, f"INEFFICIENT: {gap} seats wasted"
        elif gap < 0:  # Negative gap handled above
            return False, "INVALID"
        else:
            return True, f"EFFICIENT: {gap} seats unused"
    
    def check_for_conflicts(self, course, entry):
        """Check if this schedule creates any conflicts"""
        lecturer_id = course.lecturer.id if course.lecturer else None
        program_id = course.program.id if course.program else None
        year = self.cache.get_course_year(course)
        
        # Check if adding this would create conflicts (though it's already scheduled)
        # This helps identify existing conflicts in Timetable
        has_conflict = False
        
        if lecturer_id and self.conflict_tracker.has_lecturer_conflict(lecturer_id, entry.day, entry.slot_index):
            has_conflict = True
        
        if program_id and self.conflict_tracker.has_program_conflict(
                program_id, year, entry.day, entry.slot_index, new_alloc=course):
            has_conflict = True
            
        return has_conflict
    
    def _build_expected_merge_partners(self) -> Dict[int, Set[int]]:
        """
        Group all CURRENT CourseAllocations by (normalized code, lecturer_id)
        the same way the scheduler does when merging sections into one slot.
        Used to detect allocation changes (added sections, split groups,
        merges, or a lecturer swap) that the previous stable run doesn't
        know about yet, without relying on a blunt "always touch Year 1"
        rule.
        """
        groups: Dict[tuple, list] = defaultdict(list)
        for course in CourseAllocation.objects.select_related('lecturer').all():
            lect_id = course.lecturer.id if course.lecturer else None
            norm_code = normalize_course_code(course.course_code)
            groups[(norm_code, lect_id)].append(course)

        partners: Dict[int, Set[int]] = {}
        for (norm_code, lect_id), allocs in groups.items():
            if len(allocs) <= 1:
                continue
            total_students = sum(a.number_of_students or 0 for a in allocs)
            if total_students > self.merge_limit:
                continue
            names = set((a.course_name or "").strip().upper()[:30] for a in allocs)
            if len(names) > 1:
                continue
            ids = {a.id for a in allocs}
            for a in allocs:
                partners[a.id] = ids - {a.id}
        return partners

    def get_expected_merge_partners(self, course_id: int) -> Set[int]:
        if self._expected_merge_partners is None:
            self._expected_merge_partners = self._build_expected_merge_partners()
        return self._expected_merge_partners.get(course_id, set())

    def has_allocation_changed(self, course_id, entry_day, entry_slot_index, entry_venue_id) -> bool:
        """
        True when this course's live data no longer matches what's on the
        Timetable entry we're about to preserve as "stable" — i.e. it now
        has merge partners it isn't actually co-scheduled with (a new
        section was added, a merge/split happened, or the lecturer changed
        such that a new merge grouping now applies).
        """
        expected_partners = self.get_expected_merge_partners(course_id)
        if not expected_partners:
            return False
        for partner_id in expected_partners:
            partner_schedule = self.stable_courses.get(partner_id)
            if partner_schedule is None:
                # Partner not yet confirmed stable at this same slot/venue
                return True
            same_slot = (
                partner_schedule.get('day') == entry_day
                and partner_schedule.get('slot_index') == entry_slot_index
                and getattr(partner_schedule.get('venue'), 'id', None) == entry_venue_id
            )
            if not same_slot:
                return True
        return False

    def load_and_analyze_schedules(self):
        """
        Load all schedules from Timetable and analyze them
        Classify as STABLE (perfect) or NEEDS_OPTIMIZATION
        """
        print("Loading and analyzing schedules from Timetable...")
        
        # Load individual course allocations from Timetable
        timetable_entries = Timetable.objects.select_related(
            'course_allocation', 'venue', 'course_allocation__program'
        ).all()
        
        for entry in timetable_entries:
            course = entry.course_allocation
            if not course:
                continue
                
            try:
                year = self.cache.get_course_year(course)
                students = course.number_of_students or 0
                
                # Find slot index
                slot_index = None
                for idx, (slot_start, slot_end) in enumerate(self.conflict_tracker.slots):
                    if (entry.start_time == slot_start and entry.end_time == slot_end):
                        slot_index = idx
                        break
                
                if slot_index is None:
                    print(f"Warning: Could not find slot index for {course.course_code}")
                    self.courses_to_optimize[course.id] = {
                        'day': entry.day,
                        'slot_index': None,
                        'venue': entry.venue,
                        'start_time': entry.start_time,
                        'end_time': entry.end_time,
                        'year': year,
                        'reason': 'Invalid slot time'
                    }
                    self.optimize_count += 1
                    continue
                
                # Check capacity efficiency
                capacity_ok, capacity_msg = self.check_capacity_efficiency(course, entry.venue, students)
                
                # Temporarily add to tracker to check for conflicts
                lecturer_id = course.lecturer.id if course.lecturer else None
                program_id = course.program.id if course.program else None
                
                # Check if this would conflict with already processed courses
                has_conflict = False
                if lecturer_id and self.conflict_tracker.has_lecturer_conflict(lecturer_id, entry.day, slot_index):
                    has_conflict = True
                    self.conflict_issues += 1
                
                if program_id and self.conflict_tracker.has_program_conflict(
                        program_id, year, entry.day, slot_index, new_alloc=course):
                    has_conflict = True
                    self.conflict_issues += 1
                
                # Determine if course should be optimized
                needs_optimization = False
                reason = []
                
                if not capacity_ok:
                    needs_optimization = True
                    self.capacity_issues += 1
                    reason.append(capacity_msg)
                
                if has_conflict:
                    needs_optimization = True
                    reason.append("Has conflicts")
                
                schedule_info = {
                    'day': entry.day,
                    'slot_index': slot_index,
                    'venue': entry.venue,
                    'start_time': entry.start_time,
                    'end_time': entry.end_time,
                    'year': year,
                    'students': students
                }
                
                if needs_optimization:
                    schedule_info['reason'] = ', '.join(reason)
                    self.courses_to_optimize[course.id] = schedule_info
                    self.optimize_count += 1
                else:
                    # Perfectly scheduled - preserve
                    self.stable_courses[course.id] = schedule_info
                    self.stable_count += 1
                    
                    # Add to conflict tracker
                    self.conflict_tracker.add_schedule(
                        lecturer_id,
                        program_id,
                        year,
                        entry.venue.id,
                        entry.day,
                        slot_index,
                        course.course_code,
                        alloc=course
                    )
                    
            except Exception as e:
                print(f"Error analyzing course {course.course_code}: {e}")
                self.courses_to_optimize[course.id] = {
                    'day': entry.day if entry else None,
                    'slot_index': None,
                    'venue': entry.venue if entry else None,
                    'year': 1,
                    'reason': f'Error: {str(e)}'
                }
                self.optimize_count += 1
        
        # Second pass: now that stable_courses is fully populated for this
        # run, re-check each "stable" course against its CURRENT expected
        # merge partners. A course only fails this check if the live
        # CourseAllocation data has actually changed since the schedule was
        # last published (a section was added/removed, a merge/split
        # happened, or the lecturer changed) — this replaces the old blunt
        # "always touch Year 1" rule and is what keeps Advanced Stable
        # idempotent: running it twice in a row with no data changes should
        # move nothing out of stable_courses here.
        changed_course_ids = []
        for course_id, schedule_info in self.stable_courses.items():
            venue_id = getattr(schedule_info.get('venue'), 'id', None)
            if self.has_allocation_changed(
                course_id,
                schedule_info.get('day'),
                schedule_info.get('slot_index'),
                venue_id,
            ):
                changed_course_ids.append(course_id)

        for course_id in changed_course_ids:
            schedule_info = self.stable_courses.pop(course_id)
            self.stable_count -= 1
            existing_reason = schedule_info.get('reason', '')
            reason_parts = [existing_reason] if existing_reason else []
            reason_parts.append("Course allocation changed (merge/split/lecturer)")
            schedule_info['reason'] = ', '.join(p for p in reason_parts if p)
            self.courses_to_optimize[course_id] = schedule_info
            self.optimize_count += 1
        
        # Analyze merged groups similarly
        merged_groups = AutoMergedExamGroup.objects.filter(
            date__isnull=False,
            start_time__isnull=False,
            end_time__isnull=False,
            venue__isnull=False
        ).prefetch_related('merged_courses')
        
        for group in merged_groups:
            try:
                # Find slot index
                slot_index = None
                for idx, (slot_start, slot_end) in enumerate(self.conflict_tracker.slots):
                    if group.start_time and group.end_time:
                        if group.start_time == slot_start and group.end_time == slot_end:
                            slot_index = idx
                            break
                
                if slot_index is None or not group.date or not group.venue:
                    continue
                
                # Check if merged group has any courses that need optimization
                group_needs_optimization = False
                group_reasons = set()
                course_ids = []
                
                for course in group.merged_courses.all():
                    course_ids.append(course.id)
                    if course.id in self.courses_to_optimize:
                        group_needs_optimization = True
                        reason = self.courses_to_optimize[course.id].get('reason', '')
                        group_reasons.add(reason)
                
                if group_needs_optimization:
                    self.merged_groups_to_optimize[group.id] = {
                        'course_ids': course_ids,
                        'reason': ', '.join(group_reasons)
                    }
                else:
                    self.stable_merged_groups[group.id] = course_ids
                    
            except Exception as e:
                print(f"Error analyzing merged group {group.id}: {e}")
        
        safe_print(f"Analysis complete: {self.stable_count} stable, {self.optimize_count} need optimization")
        safe_print(f"  - Capacity issues: {self.capacity_issues}")
        safe_print(f"  - Conflict issues: {self.conflict_issues}")
        safe_print(f"  - Allocation changes (merge/split/lecturer) detected this run: "
                    f"{sum(1 for v in self.courses_to_optimize.values() if 'allocation changed' in v.get('reason', '').lower())}")
        
        return (self.stable_courses, self.courses_to_optimize, 
                self.stable_merged_groups, self.merged_groups_to_optimize)
    
    def is_course_stable(self, course_allocation_id):
        """Check if a course is perfectly scheduled and should be preserved"""
        return course_allocation_id in self.stable_courses
    
    def should_optimize_course(self, course_allocation_id):
        """Check if a course needs optimization"""
        return course_allocation_id in self.courses_to_optimize


# ============================================
# MODE 1: BASIC STABLE SCHEDULER IMPLEMENTATION
# ============================================

def run_basic_stable_scheduler_thread():
    """
    Basic stable scheduler:
    1. Load existing stable schedules from Timetable
    2. Preserve them in TempTimetable
    3. Only schedule first year courses and any skipped courses
    """
    total_courses = 0
    all_scheduled_courses = []
    all_unscheduled_courses = []
    stable_preserved = 0
    first_year_scheduled = 0
    skipped_rescheduled = 0
    
    # Initialize caches and trackers
    cache = SchedulerCache()
    conflict_tracker = None
    venue_allocator = None
    
    try:
        _open_scheduler_log("basic")
        enable_wal_mode()
        # Clear TempTimetable but preserve Timetable (we'll copy from it)
        with transaction.atomic():
            TempTimetable.objects.all().delete()
            # Don't delete AutoMergedExamGroup - we'll reference existing ones
        
        config = SchedulerConfig.objects.first() or SchedulerConfig.objects.create()
        
        # Get configuration
        start_time = getattr(config, 'start_time', dtime(hour=8, minute=0))
        end_time = getattr(config, 'end_time', dtime(hour=17, minute=0))
        slot_size = int(getattr(config, 'slot_size', 2))
        
        # Get venues
        all_venues = list(Venue.objects.select_related('building__faculty').all().order_by('capacity'))

        # ── Constraint: Blocked Venues + Exclusive Venue Restrictions ────────
        # Previously stable never consulted the shared constraint engine at
        # all, so it would happily place courses in hard-blocked venues
        # (maintenance/disabled) and in venues reserved exclusively for a
        # specialization rule — both are recognised drivers of the venue
        # double-bookings seen in collision reports. Same engine, same
        # persisted toggles the regular scheduler already uses; stable just
        # scopes it as scheduler_type="stable" so admins can control it
        # independently if they choose to.
        blocked_venue_ids = constraint_engine.get_blocked_venue_ids(scheduler_type="stable")
        exclusive_venue_ids = constraint_engine.get_exclusive_venue_ids(scheduler_type="stable")
        removed_venue_ids = blocked_venue_ids | exclusive_venue_ids
        if removed_venue_ids:
            before_count = len(all_venues)
            all_venues = [v for v in all_venues if v.id not in removed_venue_ids]
            safe_print(
                f"[Constraints] Removed {before_count - len(all_venues)} venue(s) from the "
                f"general pool ({len(blocked_venue_ids)} blocked, {len(exclusive_venue_ids)} "
                f"exclusive) — {len(all_venues)} remain."
            )

        # Faculty venues map
        faculty_venues_map = defaultdict(list)
        for venue in all_venues:
            if venue.building and venue.building.faculty:
                faculty_venues_map[venue.building.faculty].append(venue)
        
        # Days configuration
        days = getattr(config, 'days', None) or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        slots = generate_slots(start_time, end_time, slot_size)
        
        # Hard lecturer-blocked day/slot constraints (kept in sync with the
        # regular scheduler) so stable runs never place a lecturer in a
        # slot they've been marked unavailable for.
        lecturer_blocked = build_lecturer_blocked_slot_map(days, slots)
        
        # Initialize conflict tracker
        conflict_tracker = ConflictTracker(days, slots, lecturer_blocked=lecturer_blocked)
        venue_allocator = VenueAllocator(all_venues, days, slots, conflict_tracker)
        
        # STEP 1: Load and preserve stable schedules
        update_basic_stable_progress(5, "Loading stable schedules from Timetable...", 0, 0,
                                    console_message="Loading existing approved schedules...")
        
        stable_loader = BasicStableScheduleLoader(conflict_tracker, cache)
        stable_courses, stable_merged_groups = stable_loader.load_stable_schedules()
        stable_preserved = len(stable_courses)
        
        # Load existing TempTimetable entries into cache
        cache.load_existing_temp_entries()
        
        # Copy stable schedules to TempTimetable with duplicate prevention
        temp_entries = []
        duplicate_count = 0
        
        for course_id, schedule in stable_courses.items():
            try:
                course = CourseAllocation.objects.get(id=course_id)
                
                # Check for duplicate before adding
                if cache.is_duplicate_entry(
                    schedule['venue'].id, 
                    schedule['day'], 
                    schedule['start_time'], 
                    schedule['end_time']
                ):
                    print(f"DUPLICATE SKIPPED: {course.course_code} - {schedule['venue'].code} {schedule['day']} {schedule['start_time']}-{schedule['end_time']}")
                    duplicate_count += 1
                    continue
                
                temp_entries.append(TempTimetable(
                    course_allocation=course,
                    venue=schedule['venue'],
                    day=schedule['day'],
                    start_time=schedule['start_time'],
                    end_time=schedule['end_time']
                ))
                
            except CourseAllocation.DoesNotExist:
                print(f"Warning: Course {course_id} not found in CourseAllocation")
        
        # Save in batches to avoid large transactions
        if temp_entries:
            batch_size = 100
            for i in range(0, len(temp_entries), batch_size):
                batch = temp_entries[i:i + batch_size]
                try:
                    TempTimetable.objects.bulk_create(batch)
                    # Update cache with successfully inserted entries
                    for entry in batch:
                        cache.add_to_temp_cache(
                            entry.venue_id, 
                            entry.day, 
                            entry.start_time, 
                            entry.end_time
                        )
                    print(f"Copied batch {i//batch_size + 1} of {len(temp_entries)//batch_size + 1} stable schedules to TempTimetable")
                except IntegrityError as e:
                    print(f"IntegrityError in batch: {e}")
                    # Try one by one for this batch
                    for entry in batch:
                        try:
                            if not cache.is_duplicate_entry(entry.venue_id, entry.day, entry.start_time, entry.end_time):
                                entry.save()
                                cache.add_to_temp_cache(entry.venue_id, entry.day, entry.start_time, entry.end_time)
                            else:
                                duplicate_count += 1
                        except IntegrityError:
                            duplicate_count += 1
                            print(f"Could not save duplicate entry: {entry}")
            
            print(f"Successfully copied {len(temp_entries)} stable schedules to TempTimetable (skipped {duplicate_count} duplicates)")
        else:
            print("No new stable schedules to copy")
        
        # Adjust stable_preserved to reflect actual saved entries
        stable_preserved = len(temp_entries)
        
        # STEP 2: Get all courses that need scheduling
        all_courses = list(CourseAllocation.objects.select_related(
            'program', 'lecturer', 'department',
            'program__department', 'program__department__faculty'
        ).all())
        
        total_courses = len(all_courses)
        
        # Separate courses by scheduling need
        courses_to_schedule = []  # First year + any unscheduled courses
        already_scheduled = []     # Already have stable schedules
        
        for course in all_courses:
            try:
                year = cache.get_course_year(course)
                
                if course.id in stable_courses:
                    already_scheduled.append(course)
                elif year == 1:
                    # First year courses - always schedule (can change)
                    courses_to_schedule.append(course)
                else:
                    # Other years - check if they need scheduling (skipped)
                    courses_to_schedule.append(course)
                    
            except Exception as e:
                print(f"Error processing course {course.course_code}: {e}")
                # Include in scheduling if we can't determine year
                courses_to_schedule.append(course)
        
        working_courses_count = len(courses_to_schedule)
        
        update_basic_stable_progress(10, f"Found {working_courses_count} courses to schedule", 
                                    0, working_courses_count,
                                    console_message=f"Stable: {stable_preserved} preserved, Need scheduling: {working_courses_count}",
                                    stable_preserved=stable_preserved)
        
        if working_courses_count == 0:
            update_basic_stable_progress(100, "All courses already have stable schedules", 
                                        0, 0,
                                        console_message="No courses need scheduling - all stable schedules preserved",
                                        stable_preserved=stable_preserved)
            _close_scheduler_log(success=True)
            return {
                'status': 'completed',
                'message': f'All {stable_preserved} courses have stable schedules preserved',
                'scheduled_count': 0,
                'remaining_count': 0,
                'stable_preserved': stable_preserved,
                'first_year_scheduled': 0,
                'skipped_rescheduled': 0
            }
        
        # STEP 3: Prioritize courses for scheduling
        def get_scheduling_priority(course):
            """First year courses get highest priority for scheduling"""
            try:
                year = cache.get_course_year(course)
                # First year courses = highest priority
                # Others (skipped) = lower priority
                return (1 if year == 1 else 2, year)
            except Exception as e:
                scheduler_logger.debug("get_scheduling_priority: year lookup failed for course #%s: %s", getattr(course, "id", "?"), e)
                return (3, 1)  # Default if year unknown
        
        courses_to_schedule.sort(key=get_scheduling_priority)
        
        # STEP 4: Process courses in batches
        BATCH_SIZE = min(50, max(20, working_courses_count // 8 or 20))
        total_scheduled = 0
        all_unscheduled = []
        total_batches = (working_courses_count + BATCH_SIZE - 1) // BATCH_SIZE
        
        # ── Pass 1: honour CombinedCourseGroup ONLY (COD-panel-defined) ──────
        # These take priority over the heuristic merge below and are removed
        # from the pool before it runs, so the heuristic never re-splits or
        # double-handles an allocation that's already properly grouped.
        tasks, combined_alloc_ids = apply_combined_course_group_pass(courses_to_schedule)
        remaining_for_heuristic = [
            c for c in courses_to_schedule if c.id not in combined_alloc_ids
        ]

        # ── Pass 2: heuristic merge (GLOBAL: same norm code + same lecturer) ─
        # for everything NOT already claimed by a CombinedCourseGroup.
        grouped = defaultdict(list)
        for course in remaining_for_heuristic:
            lect_id = course.lecturer.id if course.lecturer else None
            norm_code = normalize_course_code(course.course_code)
            grouped[(norm_code, lect_id)].append(course)
        
        MERGE_LIMIT = getattr(config, 'merge_limit', 200)

        for (norm_code, lect_id), allocs in grouped.items():
            if len(allocs) == 1:
                tasks.append(allocs[0])
                continue
            
            total_students = sum(a.number_of_students or 0 for a in allocs)
            if total_students <= MERGE_LIMIT:
                # Safety: verify all share the same course name (first 30 chars)
                names = set((a.course_name or "").strip().upper()[:30] for a in allocs)
                if len(names) > 1:
                    safe_print(
                        f"MERGE SKIPPED (different course names): '{norm_code}' "
                        f"names={names} → scheduling individually"
                    )
                    tasks.extend(allocs)
                    continue
                safe_print(
                    f"MERGE: '{norm_code}' x{len(allocs)} sections, "
                    f"combined={total_students}/{MERGE_LIMIT} students → ONE slot"
                )
                tasks.append({
                    "merged": allocs,
                    "total_students": total_students,
                    "norm_code": norm_code,
                })
            else:
                tasks.extend(allocs)
        
        # Process batches
        update_basic_stable_progress(15, "Phase 1: Scheduling first year and skipped courses", 
                                     0, working_courses_count,
                                     console_message=f"Starting scheduling for {working_courses_count} courses")
        
        for batch_num, i in enumerate(range(0, len(tasks), BATCH_SIZE), 1):
            batch = tasks[i:i + BATCH_SIZE]
            progress = min(70, 15 + int((i + len(batch)) / len(tasks) * 55))
            
            update_basic_stable_progress(
                progress,
                f"Scheduling batch {batch_num}/{total_batches}",
                total_scheduled,
                working_courses_count - total_scheduled,
                batch_info=f"Batch {batch_num}/{total_batches}",
                current_batch=batch_num,
                total_batches=total_batches,
                console_message=f"Batch {batch_num}/{total_batches}: Processing {len(batch)} items"
            )
            
            # Process batch
            scheduled_in_batch, unscheduled_in_batch, batch_scheduled, batch_unscheduled = process_basic_stable_batch(
                batch, config, faculty_venues_map, days, slots,
                conflict_tracker, venue_allocator, cache, stable_loader
            )
            
            total_scheduled += scheduled_in_batch
            all_unscheduled.extend(unscheduled_in_batch)
            all_scheduled_courses.extend(batch_scheduled)
            all_unscheduled_courses.extend(batch_unscheduled)
            
            # Count first year vs skipped
            for item in batch_scheduled:
                if "[FIRST YEAR]" in item:
                    first_year_scheduled += 1
                elif "[SKIPPED]" in item:
                    skipped_rescheduled += 1
            
            update_basic_stable_progress(
                progress,
                f"Batch {batch_num} completed",
                total_scheduled,
                working_courses_count - total_scheduled,
                console_message=f"Batch {batch_num}: {scheduled_in_batch} scheduled",
                scheduled_courses=all_scheduled_courses,
                unscheduled_courses=all_unscheduled_courses,
                stable_preserved=stable_preserved,
                first_year_scheduled=first_year_scheduled,
                skipped_rescheduled=skipped_rescheduled
            )
        
        # STEP 5: Fallback for remaining courses
        if all_unscheduled:
            update_basic_stable_progress(75, "Phase 2: Fallback scheduling", 
                                         total_scheduled, len(all_unscheduled),
                                         console_message=f"Fallback for {len(all_unscheduled)} courses")
            
            scheduled_in_fallback, still_unscheduled, fallback_scheduled, fallback_unscheduled = process_basic_stable_fallback(
                all_unscheduled, config, all_venues, days, slots,
                conflict_tracker, venue_allocator, cache, stable_loader
            )
            
            total_scheduled += scheduled_in_fallback
            all_unscheduled = still_unscheduled
            all_scheduled_courses.extend(fallback_scheduled)
            all_unscheduled_courses.extend(fallback_unscheduled)
            
            # Count fallback schedules
            for item in fallback_scheduled:
                if "[FIRST YEAR]" in item:
                    first_year_scheduled += 1
                elif "[SKIPPED]" in item:
                    skipped_rescheduled += 1
        
        # Final results
        final_success_rate = (total_scheduled / working_courses_count * 100) if working_courses_count > 0 else 100

        # ── Phase 6: Venue Capacity Optimisation ──────────────────────
        update_basic_stable_progress(
            96,
            "Phase 6: Optimising venue–course capacity matching per timeslot…",
            total_scheduled,
            len(all_unscheduled),
            console_message=(
                "Phase 6: re-matching room sizes to class sizes "
                "(merged-group venues exempted)"
            ),
            scheduled_courses=all_scheduled_courses,
            unscheduled_courses=all_unscheduled_courses,
            stable_preserved=stable_preserved,
            first_year_scheduled=first_year_scheduled,
            skipped_rescheduled=skipped_rescheduled,
        )
        venue_opt_stats = optimize_venue_assignments(days, slots)
        safe_print(
            f"Phase 6 done: timeslots_examined={venue_opt_stats['timeslots_examined']} | "
            f"optimised={venue_opt_stats['timeslots_optimised']} | "
            f"swaps={venue_opt_stats['swaps_made']} | errors={venue_opt_stats['errors']}"
        )

        update_basic_stable_progress(
            100,
            "Stable scheduling completed",
            total_scheduled,
            len(all_unscheduled),
            console_message=(
                f"Complete: {stable_preserved} stable preserved, "
                f"{first_year_scheduled} first year scheduled, "
                f"{skipped_rescheduled} skipped rescheduled | "
                f"VenueOpt: {venue_opt_stats['swaps_made']} swaps in "
                f"{venue_opt_stats['timeslots_optimised']} timeslots"
            ),
            scheduled_courses=all_scheduled_courses,
            unscheduled_courses=all_unscheduled_courses,
            stable_preserved=stable_preserved,
            first_year_scheduled=first_year_scheduled,
            skipped_rescheduled=skipped_rescheduled
        )

        _close_scheduler_log(success=True)
        return {
            'status': 'completed',
            'message': f'Basic stable scheduling completed',
            'stable_preserved': stable_preserved,
            'first_year_scheduled': first_year_scheduled,
            'skipped_rescheduled': skipped_rescheduled,
            'total_scheduled': total_scheduled,
            'remaining_count': len(all_unscheduled),
            'success_rate': final_success_rate,
            'venue_opt_swaps': venue_opt_stats['swaps_made'],
            'venue_opt_timeslots': venue_opt_stats['timeslots_optimised'],
        }
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"Basic stable scheduler error:\n{error_details}")
        scheduler_logger.error("Basic stable scheduler fatal error: %s\n%s", e, error_details)
        update_basic_stable_progress(0, f"Error: {str(e)}", 0, 0,
                                     console_message=f"Error: {str(e)}")
        _close_scheduler_log(success=False)
        return {
            'status': 'error',
            'message': str(e),
            'stable_preserved': stable_preserved,
            'first_year_scheduled': first_year_scheduled,
            'skipped_rescheduled': skipped_rescheduled
        }


def process_basic_stable_batch(batch_tasks, config, faculty_venues_map, days, slots,
                               conflict_tracker, venue_allocator, cache, stable_loader):
    """Process a batch of tasks for basic stable scheduling"""
    scheduled_count = 0
    unscheduled_in_batch = []
    scheduled_courses_list = []
    unscheduled_courses_list = []
    
    timetable_entries = []
    batch_entry_keys = set()  # Track entries in current batch to prevent duplicates
    new_merged_groups = []
    
    for task in batch_tasks:
        is_merged = isinstance(task, dict)
        allocs = task['merged'] if is_merged else [task]
        total_students = task['total_students'] if is_merged else (allocs[0].number_of_students or 0)
        code = allocs[0].course_code
        lecturer = allocs[0].lecturer
        lecturer_id = lecturer.id if lecturer else None
        program = allocs[0].program
        program_id = program.id if program else None
        
        try:
            year = cache.get_course_year(allocs[0])
        except Exception as e:
            unscheduled_courses_list.append(f"{code} - Year detection failed: {str(e)}")
            unscheduled_in_batch.append(task)
            continue
        
        # Determine if this is first year or skipped
        is_first_year = (year == 1)
        course_type = "FIRST YEAR" if is_first_year else "SKIPPED"
        
        assigned = False
        
        # Get slot preference
        preferred_slots = get_course_slot_preference(allocs[0], slots)
        
        # Get faculty venues
        faculty_venues = []
        faculty = get_course_faculty(allocs[0])
        if faculty and faculty in faculty_venues_map:
            faculty_venues = faculty_venues_map[faculty]
        
        if not faculty_venues:
            unscheduled_courses_list.append(f"{code} ({course_type}) - No faculty venues")
            unscheduled_in_batch.append(task)
            continue
        
        # Try to schedule
        preferred_days = days.copy()
        random.shuffle(preferred_days)
        
        for day in preferred_days:
            if assigned:
                break
            
            for slot_idx in preferred_slots:
                start, end = slots[slot_idx]
                
                # Check conflicts
                lecturer_conflict = lecturer_id and conflict_tracker.has_lecturer_conflict(lecturer_id, day, slot_idx)
                program_conflict = program_id and conflict_tracker.has_program_conflict(program_id, year, day, slot_idx, new_alloc=allocs[0])
                
                if lecturer_conflict or program_conflict:
                    continue
                
                # Find venue
                venue = venue_allocator.find_efficient_venue(total_students, faculty_venues, day, slot_idx, "best_fit")
                
                if venue:
                    # Check for duplicate before adding to batch
                    entry_key = (venue.id, day, start, end)
                    
                    if entry_key in batch_entry_keys:
                        safe_print(f"DUPLICATE PREVENTED (in batch): {code} - {venue.code} {day} {start}-{end}")
                        continue
                    
                    if cache.is_duplicate_entry(venue.id, day, start, end):
                        safe_print(f"DUPLICATE PREVENTED (in DB): {code} - {venue.code} {day} {start}-{end}")
                        continue
                    
                    batch_entry_keys.add(entry_key)
                    
                    # Create timetable entries
                    for alloc in allocs:
                        timetable_entries.append(TempTimetable(
                            course_allocation=alloc,
                            venue=venue,
                            day=day,
                            start_time=start,
                            end_time=end
                        ))
                    
                    # Update conflict tracker (use add_merged_schedule for correct deduplication)
                    conflict_tracker.add_merged_schedule(allocs, venue.id, day, slot_idx, cache)

                    scheduled_count += len(allocs)
                    assigned = True

                    # Create merged group if needed — but NOT for tasks already
                    # backed by a CombinedCourseGroup, which is itself the
                    # source of truth for this group and must not get a
                    # duplicate AutoMergedExamGroup layered on top.
                    if (is_merged and not task.get('combined_group')
                            and not stable_loader.is_course_stable(allocs[0].id)):
                        norm = task.get('norm_code', normalize_course_code(code))
                        mg = AutoMergedExamGroup(
                            base_course=allocs[0],
                            merged_code=norm,
                            total_students=total_students,
                            date=day,
                            start_time=start,
                            end_time=end,
                            venue=venue
                        )
                        new_merged_groups.append((mg, allocs))
                    
                    scheduled_courses_list.append(
                        f"{code} (Year {year}, [{course_type}]) in {venue.code} ({day} {start}-{end})"
                    )
                    break
        
        if not assigned:
            unscheduled_courses_list.append(f"{code} (Year {year}, {course_type}) - Could not schedule")
            unscheduled_in_batch.append(task)
    
    # Save to database with duplicate prevention
    if timetable_entries:
        # Final duplicate check
        final_entries = []
        for entry in timetable_entries:
            key = (entry.venue_id, entry.day, entry.start_time, entry.end_time)
            if not cache.is_duplicate_entry(entry.venue_id, entry.day, entry.start_time, entry.end_time):
                final_entries.append(entry)
            else:
                print(f"FINAL DUPLICATE REMOVED: {entry}")
        
        if final_entries:
            try:
                TempTimetable.objects.bulk_create(final_entries)
                # Update cache
                for entry in final_entries:
                    cache.add_to_temp_cache(entry.venue_id, entry.day, entry.start_time, entry.end_time)
            except IntegrityError as e:
                print(f"IntegrityError in bulk_create: {e}")
                # Try one by one
                for entry in final_entries:
                    try:
                        if not cache.is_duplicate_entry(entry.venue_id, entry.day, entry.start_time, entry.end_time):
                            entry.save()
                            cache.add_to_temp_cache(entry.venue_id, entry.day, entry.start_time, entry.end_time)
                    except IntegrityError:
                        print(f"Could not save duplicate: {entry}")
    
    for mg, allocs in new_merged_groups:
        try:
            mg.save()
            mg.merged_courses.set(allocs)
        except Exception as e:
            print(f"Error saving merged group: {e}")
    
    return scheduled_count, unscheduled_in_batch, scheduled_courses_list, unscheduled_courses_list


def process_basic_stable_fallback(unscheduled_tasks, config, all_venues, days, slots,
                                  conflict_tracker, venue_allocator, cache, stable_loader):
    """Fallback processing for courses that couldn't be scheduled in faculty venues"""
    if not unscheduled_tasks:
        return 0, [], [], []
    
    scheduled_count = 0
    still_unscheduled = []
    scheduled_courses_list = []
    unscheduled_courses_list = []
    
    timetable_entries = []
    batch_entry_keys = set()
    
    # Flatten tasks
    flat_courses = []
    for task in unscheduled_tasks:
        if isinstance(task, dict):
            flat_courses.extend(task['merged'])
        else:
            flat_courses.append(task)
    
    for course in flat_courses:
        try:
            year = cache.get_course_year(course)
        except Exception:
            unscheduled_courses_list.append(f"{course.course_code} - Year detection failed")
            still_unscheduled.append(course)
            continue
        
        is_first_year = (year == 1)
        course_type = "FIRST YEAR" if is_first_year else "SKIPPED"
        
        total_students = course.number_of_students or 0
        lecturer_id = course.lecturer.id if course.lecturer else None
        program_id = course.program.id if course.program else None
        
        assigned = False
        preferred_slots = get_course_slot_preference(course, slots)
        preferred_days = days.copy()
        random.shuffle(preferred_days)
        
        for day in preferred_days:
            if assigned:
                break
            
            for slot_idx in preferred_slots:
                start, end = slots[slot_idx]
                
                lecturer_conflict = lecturer_id and conflict_tracker.has_lecturer_conflict(lecturer_id, day, slot_idx)
                program_conflict = program_id and conflict_tracker.has_program_conflict(program_id, year, day, slot_idx, new_alloc=course)
                
                if lecturer_conflict or program_conflict:
                    continue
                
                venue = venue_allocator.find_efficient_venue(total_students, all_venues, day, slot_idx, "best_fit")
                
                if venue:
                    # Check for duplicate
                    entry_key = (venue.id, day, start, end)
                    
                    if entry_key in batch_entry_keys:
                        continue
                    
                    if cache.is_duplicate_entry(venue.id, day, start, end):
                        continue
                    
                    batch_entry_keys.add(entry_key)
                    
                    timetable_entries.append(TempTimetable(
                        course_allocation=course,
                        venue=venue,
                        day=day,
                        start_time=start,
                        end_time=end
                    ))
                    
                    conflict_tracker.add_schedule(
                        lecturer_id, program_id, year, venue.id, day, slot_idx,
                        course.course_code, alloc=course
                    )
                    
                    scheduled_count += 1
                    assigned = True
                    scheduled_courses_list.append(
                        f"{course.course_code} (Year {year}, [{course_type}]) in {venue.code} [FALLBACK] ({day} {start}-{end})"
                    )
                    break
        
        if not assigned:
            unscheduled_courses_list.append(f"{course.course_code} (Year {year}, {course_type}) - UNSCHEDULABLE")
            still_unscheduled.append(course)
    
    if timetable_entries:
        # Final duplicate check
        final_entries = []
        for entry in timetable_entries:
            if not cache.is_duplicate_entry(entry.venue_id, entry.day, entry.start_time, entry.end_time):
                final_entries.append(entry)
        
        if final_entries:
            try:
                TempTimetable.objects.bulk_create(final_entries)
                for entry in final_entries:
                    cache.add_to_temp_cache(entry.venue_id, entry.day, entry.start_time, entry.end_time)
            except IntegrityError:
                # Try one by one
                for entry in final_entries:
                    try:
                        if not cache.is_duplicate_entry(entry.venue_id, entry.day, entry.start_time, entry.end_time):
                            entry.save()
                            cache.add_to_temp_cache(entry.venue_id, entry.day, entry.start_time, entry.end_time)
                    except IntegrityError:
                        pass
    
    return scheduled_count, still_unscheduled, scheduled_courses_list, unscheduled_courses_list


# ============================================
# MODE 2: ADVANCED STABLE SCHEDULER IMPLEMENTATION
# ============================================

def run_advanced_stable_scheduler_thread():
    """
    Advanced stable scheduler:
    1. Analyze existing schedules for issues (capacity, conflicts)
    2. Preserve perfectly scheduled courses
    3. Optimize courses with issues
    4. Schedule first year and skipped courses
    """
    total_courses = 0
    all_scheduled_courses = []
    all_unscheduled_courses = []
    stable_preserved = 0
    courses_optimized = 0
    capacity_issues_fixed = 0
    conflicts_resolved = 0
    skipped_rescheduled = 0
    
    # Initialize caches and trackers
    cache = SchedulerCache()
    conflict_tracker = None
    venue_allocator = None
    
    try:
        _open_scheduler_log("advanced")
        enable_wal_mode()
        # Clear TempTimetable but preserve Timetable (we'll copy from it)
        with transaction.atomic():
            TempTimetable.objects.all().delete()
            # Don't delete AutoMergedExamGroup - we'll reference existing ones
        
        config = SchedulerConfig.objects.first() or SchedulerConfig.objects.create()
        
        # Get configuration
        start_time = getattr(config, 'start_time', dtime(hour=8, minute=0))
        end_time = getattr(config, 'end_time', dtime(hour=17, minute=0))
        slot_size = int(getattr(config, 'slot_size', 2))
        
        # Get venues
        all_venues = list(Venue.objects.select_related('building__faculty').all().order_by('capacity'))

        # ── Constraint: Blocked Venues + Exclusive Venue Restrictions ────────
        # See basic-mode note above — same shared constraint engine, same
        # persisted toggles, scoped as scheduler_type="stable".
        blocked_venue_ids = constraint_engine.get_blocked_venue_ids(scheduler_type="stable")
        exclusive_venue_ids = constraint_engine.get_exclusive_venue_ids(scheduler_type="stable")
        removed_venue_ids = blocked_venue_ids | exclusive_venue_ids
        if removed_venue_ids:
            before_count = len(all_venues)
            all_venues = [v for v in all_venues if v.id not in removed_venue_ids]
            safe_print(
                f"[Constraints] Removed {before_count - len(all_venues)} venue(s) from the "
                f"general pool ({len(blocked_venue_ids)} blocked, {len(exclusive_venue_ids)} "
                f"exclusive) — {len(all_venues)} remain."
            )

        # Faculty venues map
        faculty_venues_map = defaultdict(list)
        for venue in all_venues:
            if venue.building and venue.building.faculty:
                faculty_venues_map[venue.building.faculty].append(venue)
        
        # Days configuration
        days = getattr(config, 'days', None) or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        slots = generate_slots(start_time, end_time, slot_size)
        
        # Hard lecturer-blocked day/slot constraints (kept in sync with the
        # regular scheduler) so stable runs never place a lecturer in a
        # slot they've been marked unavailable for.
        lecturer_blocked = build_lecturer_blocked_slot_map(days, slots)
        
        # Initialize conflict tracker
        conflict_tracker = ConflictTracker(days, slots, lecturer_blocked=lecturer_blocked)
        venue_allocator = VenueAllocator(all_venues, days, slots, conflict_tracker)
        
        # Load existing TempTimetable entries into cache
        cache.load_existing_temp_entries()
        
        # STEP 1: Load, analyze, and classify schedules
        update_advanced_stable_progress(5, "Analyzing existing schedules...", 0, 0,
                                       console_message="Analyzing schedules for optimization opportunities...")
        
        advanced_loader = AdvancedStableScheduleLoader(
            conflict_tracker, cache,
            merge_limit=getattr(config, 'merge_limit', 200)
        )
        (stable_courses, courses_to_optimize, 
         stable_merged_groups, merged_groups_to_optimize) = advanced_loader.load_and_analyze_schedules()
        
        stable_preserved = len(stable_courses)
        courses_to_optimize_count = len(courses_to_optimize)
        
        update_advanced_stable_progress(10, f"Analysis complete", 0, 0,
                                       console_message=f"Found {stable_preserved} perfectly scheduled courses, {courses_to_optimize_count} need optimization",
                                       stable_preserved=stable_preserved,
                                       courses_optimized=0,
                                       capacity_issues_fixed=0,
                                       conflicts_resolved=0)
        
        # Copy perfectly stable schedules to TempTimetable with duplicate prevention
        temp_entries = []
        duplicate_count = 0
        
        for course_id, schedule in stable_courses.items():
            try:
                course = CourseAllocation.objects.get(id=course_id)
                
                # Check for duplicate
                if cache.is_duplicate_entry(
                    schedule['venue'].id,
                    schedule['day'],
                    schedule['start_time'],
                    schedule['end_time']
                ):
                    duplicate_count += 1
                    continue
                
                temp_entries.append(TempTimetable(
                    course_allocation=course,
                    venue=schedule['venue'],
                    day=schedule['day'],
                    start_time=schedule['start_time'],
                    end_time=schedule['end_time']
                ))
            except CourseAllocation.DoesNotExist:
                print(f"Warning: Course {course_id} not found in CourseAllocation")
        
        if temp_entries:
            # Save in batches
            batch_size = 100
            for i in range(0, len(temp_entries), batch_size):
                batch = temp_entries[i:i + batch_size]
                try:
                    TempTimetable.objects.bulk_create(batch)
                    for entry in batch:
                        cache.add_to_temp_cache(entry.venue_id, entry.day, entry.start_time, entry.end_time)
                    print(f"Copied batch {i//batch_size + 1} of {len(temp_entries)//batch_size + 1} stable schedules to TempTimetable")
                except IntegrityError:
                    # Try one by one
                    for entry in batch:
                        try:
                            if not cache.is_duplicate_entry(entry.venue_id, entry.day, entry.start_time, entry.end_time):
                                entry.save()
                                cache.add_to_temp_cache(entry.venue_id, entry.day, entry.start_time, entry.end_time)
                        except IntegrityError:
                            duplicate_count += 1
            
            print(f"Copied {len(temp_entries)} stable schedules to TempTimetable (skipped {duplicate_count} duplicates)")
        
        # Adjust stable_preserved
        stable_preserved = len(temp_entries)
        
        # STEP 2: Get all courses that need scheduling/optimization
        all_courses = list(CourseAllocation.objects.select_related(
            'program', 'lecturer', 'department',
            'program__department', 'program__department__faculty'
        ).all())
        
        total_courses = len(all_courses)
        
        # Separate courses by scheduling need
        courses_to_schedule = []  # Courses needing scheduling/optimization
        already_scheduled = []     # Perfectly scheduled courses
        
        for course in all_courses:
            try:
                year = cache.get_course_year(course)
                
                if course.id in stable_courses:
                    already_scheduled.append(course)
                elif course.id in courses_to_optimize:
                    # Course needs optimization - include in scheduling
                    courses_to_schedule.append(course)
                    reason = courses_to_optimize[course.id].get('reason', '')
                    if 'INEFFICIENT' in reason or 'OVERFLOW' in reason:
                        capacity_issues_fixed += 1
                    if 'conflict' in reason.lower():
                        conflicts_resolved += 1
                elif year == 1:
                    # First year courses - always schedule
                    courses_to_schedule.append(course)
                else:
                    # Other years - check if they were skipped
                    courses_to_schedule.append(course)
                    skipped_rescheduled += 1
                    
            except Exception as e:
                print(f"Error processing course {course.course_code}: {e}")
                courses_to_schedule.append(course)
        
        working_courses_count = len(courses_to_schedule)
        
        update_advanced_stable_progress(15, f"Found {working_courses_count} courses to schedule/optimize", 
                                       0, working_courses_count,
                                       console_message=f"Perfect: {stable_preserved} preserved, Need work: {working_courses_count}",
                                       stable_preserved=stable_preserved,
                                       courses_optimized=courses_to_optimize_count,
                                       capacity_issues_fixed=capacity_issues_fixed,
                                       conflicts_resolved=conflicts_resolved,
                                       skipped_rescheduled=skipped_rescheduled)
        
        if working_courses_count == 0:
            update_advanced_stable_progress(100, "All courses are perfectly scheduled", 
                                           0, 0,
                                           console_message="No courses need optimization or scheduling",
                                           stable_preserved=stable_preserved,
                                           courses_optimized=0,
                                           capacity_issues_fixed=0,
                                           conflicts_resolved=0,
                                           skipped_rescheduled=0)
            _close_scheduler_log(success=True)
            return {
                'status': 'completed',
                'message': f'All {stable_preserved} courses are perfectly scheduled',
                'stable_preserved': stable_preserved,
                'courses_optimized': 0,
                'capacity_issues_fixed': 0,
                'conflicts_resolved': 0,
                'skipped_rescheduled': 0,
                'total_scheduled': 0,
                'remaining_count': 0,
                'success_rate': 100
            }
        
        # STEP 3: Prioritize courses for scheduling
        def get_optimization_priority(course):
            """Courses with issues get highest priority"""
            try:
                year = cache.get_course_year(course)
                # Priority: 1. Capacity issues, 2. Conflicts, 3. First year, 4. Skipped
                if course.id in courses_to_optimize:
                    reason = courses_to_optimize[course.id].get('reason', '')
                    if 'OVERFLOW' in reason:
                        return (1, year)  # Highest priority - overflow
                    elif 'INEFFICIENT' in reason:
                        return (2, year)  # High priority - inefficiency
                    elif 'conflict' in reason.lower():
                        return (3, year)  # Medium priority - conflicts
                    else:
                        return (4, year)  # Other issues
                elif year == 1:
                    return (5, year)  # First year courses
                else:
                    return (6, year)  # Skipped courses
            except Exception as e:
                scheduler_logger.debug("get_optimization_priority: lookup failed for course #%s: %s", getattr(course, "id", "?"), e)
                return (7, 1)
        
        courses_to_schedule.sort(key=get_optimization_priority)
        
        # STEP 4: Process courses in batches
        BATCH_SIZE = min(50, max(20, working_courses_count // 8 or 20))
        total_scheduled = 0
        all_unscheduled = []
        total_batches = (working_courses_count + BATCH_SIZE - 1) // BATCH_SIZE
        
        # ── Pass 1: honour CombinedCourseGroup ONLY (COD-panel-defined) ──────
        # These take priority over the heuristic merge below and are removed
        # from the pool before it runs, so the heuristic never re-splits or
        # double-handles an allocation that's already properly grouped.
        tasks, combined_alloc_ids = apply_combined_course_group_pass(courses_to_schedule)
        remaining_for_heuristic = [
            c for c in courses_to_schedule if c.id not in combined_alloc_ids
        ]

        # ── Pass 2: heuristic merge (GLOBAL: same norm code + same lecturer) ─
        # for everything NOT already claimed by a CombinedCourseGroup.
        grouped = defaultdict(list)
        for course in remaining_for_heuristic:
            lect_id = course.lecturer.id if course.lecturer else None
            norm_code = normalize_course_code(course.course_code)
            grouped[(norm_code, lect_id)].append(course)
        
        MERGE_LIMIT = getattr(config, 'merge_limit', 200)

        for (norm_code, lect_id), allocs in grouped.items():
            if len(allocs) == 1:
                tasks.append(allocs[0])
                continue
            
            total_students = sum(a.number_of_students or 0 for a in allocs)
            if total_students <= MERGE_LIMIT:
                # Safety: verify all share the same course name (first 30 chars)
                names = set((a.course_name or "").strip().upper()[:30] for a in allocs)
                if len(names) > 1:
                    safe_print(
                        f"MERGE SKIPPED (different course names): '{norm_code}' "
                        f"names={names} → scheduling individually"
                    )
                    tasks.extend(allocs)
                    continue
                safe_print(
                    f"MERGE: '{norm_code}' x{len(allocs)} sections, "
                    f"combined={total_students}/{MERGE_LIMIT} students → ONE slot"
                )
                tasks.append({
                    "merged": allocs,
                    "total_students": total_students,
                    "norm_code": norm_code,
                })
            else:
                tasks.extend(allocs)
        
        # Process batches
        update_advanced_stable_progress(20, "Phase 1: Optimizing and scheduling courses", 
                                        0, working_courses_count,
                                        console_message=f"Starting optimization for {working_courses_count} courses")
        
        batch_optimized_count = 0
        batch_capacity_fixed = 0
        batch_conflicts_resolved = 0
        
        for batch_num, i in enumerate(range(0, len(tasks), BATCH_SIZE), 1):
            batch = tasks[i:i + BATCH_SIZE]
            progress = min(70, 20 + int((i + len(batch)) / len(tasks) * 50))
            
            update_advanced_stable_progress(
                progress,
                f"Processing batch {batch_num}/{total_batches}",
                total_scheduled,
                working_courses_count - total_scheduled,
                batch_info=f"Batch {batch_num}/{total_batches}",
                current_batch=batch_num,
                total_batches=total_batches,
                console_message=f"Batch {batch_num}/{total_batches}: Processing {len(batch)} items",
                stable_preserved=stable_preserved,
                courses_optimized=batch_optimized_count,
                capacity_issues_fixed=batch_capacity_fixed,
                conflicts_resolved=batch_conflicts_resolved,
                skipped_rescheduled=skipped_rescheduled
            )
            
            # Process batch
            (scheduled_in_batch, unscheduled_in_batch, 
             batch_scheduled, batch_unscheduled, 
             opt_count, cap_fixed, conf_resolved) = process_advanced_stable_batch(
                batch, config, faculty_venues_map, days, slots,
                conflict_tracker, venue_allocator, cache, courses_to_optimize
            )
            
            total_scheduled += scheduled_in_batch
            all_unscheduled.extend(unscheduled_in_batch)
            all_scheduled_courses.extend(batch_scheduled)
            all_unscheduled_courses.extend(batch_unscheduled)
            
            batch_optimized_count += opt_count
            batch_capacity_fixed += cap_fixed
            batch_conflicts_resolved += conf_resolved
            
            update_advanced_stable_progress(
                progress,
                f"Batch {batch_num} completed",
                total_scheduled,
                working_courses_count - total_scheduled,
                console_message=f"Batch {batch_num}: {scheduled_in_batch} scheduled",
                scheduled_courses=all_scheduled_courses,
                unscheduled_courses=all_unscheduled_courses,
                stable_preserved=stable_preserved,
                courses_optimized=batch_optimized_count,
                capacity_issues_fixed=batch_capacity_fixed,
                conflicts_resolved=batch_conflicts_resolved,
                skipped_rescheduled=skipped_rescheduled
            )
        
        # STEP 5: Fallback for remaining courses
        if all_unscheduled:
            update_advanced_stable_progress(75, "Phase 2: Fallback scheduling", 
                                            total_scheduled, len(all_unscheduled),
                                            console_message=f"Fallback for {len(all_unscheduled)} courses")
            
            (scheduled_in_fallback, still_unscheduled, 
             fallback_scheduled, fallback_unscheduled,
             opt_count, cap_fixed, conf_resolved) = process_advanced_stable_fallback(
                all_unscheduled, config, all_venues, days, slots,
                conflict_tracker, venue_allocator, cache, courses_to_optimize
            )
            
            total_scheduled += scheduled_in_fallback
            all_unscheduled = still_unscheduled
            all_scheduled_courses.extend(fallback_scheduled)
            all_unscheduled_courses.extend(fallback_unscheduled)
            
            batch_optimized_count += opt_count
            batch_capacity_fixed += cap_fixed
            batch_conflicts_resolved += conf_resolved
        
        # Final results
        final_success_rate = (total_scheduled / working_courses_count * 100) if working_courses_count > 0 else 100

        # ── Phase 6: Venue Capacity Optimisation ──────────────────────
        update_advanced_stable_progress(
            96,
            "Phase 6: Optimising venue–course capacity matching per timeslot…",
            total_scheduled,
            len(all_unscheduled),
            console_message=(
                "Phase 6: re-matching room sizes to class sizes "
                "(merged-group venues exempted)"
            ),
            scheduled_courses=all_scheduled_courses,
            unscheduled_courses=all_unscheduled_courses,
            stable_preserved=stable_preserved,
            courses_optimized=batch_optimized_count,
            capacity_issues_fixed=batch_capacity_fixed,
            conflicts_resolved=batch_conflicts_resolved,
            skipped_rescheduled=skipped_rescheduled,
        )
        venue_opt_stats = optimize_venue_assignments(days, slots)
        safe_print(
            f"Phase 6 done: timeslots_examined={venue_opt_stats['timeslots_examined']} | "
            f"optimised={venue_opt_stats['timeslots_optimised']} | "
            f"swaps={venue_opt_stats['swaps_made']} | errors={venue_opt_stats['errors']}"
        )

        update_advanced_stable_progress(
            100,
            "Advanced stable scheduling completed",
            total_scheduled,
            len(all_unscheduled),
            console_message=(
                f"Complete: {stable_preserved} perfect, {batch_optimized_count} optimized, "
                f"{batch_capacity_fixed} capacity fixed, {batch_conflicts_resolved} conflicts resolved | "
                f"VenueOpt: {venue_opt_stats['swaps_made']} swaps in "
                f"{venue_opt_stats['timeslots_optimised']} timeslots"
            ),
            scheduled_courses=all_scheduled_courses,
            unscheduled_courses=all_unscheduled_courses,
            stable_preserved=stable_preserved,
            courses_optimized=batch_optimized_count,
            capacity_issues_fixed=batch_capacity_fixed,
            conflicts_resolved=batch_conflicts_resolved,
            skipped_rescheduled=skipped_rescheduled
        )

        _close_scheduler_log(success=True)
        return {
            'status': 'completed',
            'message': f'Advanced stable scheduling completed',
            'stable_preserved': stable_preserved,
            'courses_optimized': batch_optimized_count,
            'capacity_issues_fixed': batch_capacity_fixed,
            'conflicts_resolved': batch_conflicts_resolved,
            'skipped_rescheduled': skipped_rescheduled,
            'total_scheduled': total_scheduled,
            'remaining_count': len(all_unscheduled),
            'success_rate': final_success_rate,
            'venue_opt_swaps': venue_opt_stats['swaps_made'],
            'venue_opt_timeslots': venue_opt_stats['timeslots_optimised'],
        }
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"Advanced stable scheduler error:\n{error_details}")
        scheduler_logger.error("Advanced stable scheduler fatal error: %s\n%s", e, error_details)
        update_advanced_stable_progress(0, f"Error: {str(e)}", 0, 0,
                                       console_message=f"Error: {str(e)}")
        _close_scheduler_log(success=False)
        return {
            'status': 'error',
            'message': str(e),
            'stable_preserved': stable_preserved,
            'courses_optimized': 0,
            'capacity_issues_fixed': 0,
            'conflicts_resolved': 0,
            'skipped_rescheduled': 0
        }


def process_advanced_stable_batch(batch_tasks, config, faculty_venues_map, days, slots,
                                  conflict_tracker, venue_allocator, cache, courses_to_optimize):
    """Process a batch of tasks for advanced stable scheduling with optimization tracking"""
    scheduled_count = 0
    unscheduled_in_batch = []
    scheduled_courses_list = []
    unscheduled_courses_list = []
    
    optimized_count = 0
    capacity_fixed = 0
    conflicts_resolved = 0
    
    timetable_entries = []
    batch_entry_keys = set()
    new_merged_groups = []
    
    for task in batch_tasks:
        is_merged = isinstance(task, dict)
        allocs = task['merged'] if is_merged else [task]
        total_students = task['total_students'] if is_merged else (allocs[0].number_of_students or 0)
        code = allocs[0].course_code
        lecturer = allocs[0].lecturer
        lecturer_id = lecturer.id if lecturer else None
        program = allocs[0].program
        program_id = program.id if program else None
        
        try:
            year = cache.get_course_year(allocs[0])
        except Exception as e:
            unscheduled_courses_list.append(f"{code} - Year detection failed: {str(e)}")
            unscheduled_in_batch.append(task)
            continue
        
        # Check if this course is being optimized
        is_optimized = False
        is_capacity_issue = False
        is_conflict_issue = False
        
        if allocs[0].id in courses_to_optimize:
            is_optimized = True
            reason = courses_to_optimize[allocs[0].id].get('reason', '')
            if 'INEFFICIENT' in reason or 'OVERFLOW' in reason:
                is_capacity_issue = True
            if 'conflict' in reason.lower():
                is_conflict_issue = True
        
        # Determine course type for display
        if is_optimized:
            if is_capacity_issue:
                course_type = "OPTIMIZE-CAPACITY"
            elif is_conflict_issue:
                course_type = "OPTIMIZE-CONFLICT"
            else:
                course_type = "OPTIMIZE"
        elif year == 1:
            course_type = "FIRST YEAR"
        else:
            course_type = "SKIPPED"
        
        assigned = False
        
        # Get slot preference
        preferred_slots = get_course_slot_preference(allocs[0], slots)
        
        # Get faculty venues
        faculty_venues = []
        faculty = get_course_faculty(allocs[0])
        if faculty and faculty in faculty_venues_map:
            faculty_venues = faculty_venues_map[faculty]
        
        if not faculty_venues:
            unscheduled_courses_list.append(f"{code} ({course_type}) - No faculty venues")
            unscheduled_in_batch.append(task)
            continue
        
        # Try to schedule
        preferred_days = days.copy()
        random.shuffle(preferred_days)
        
        for day in preferred_days:
            if assigned:
                break
            
            for slot_idx in preferred_slots:
                start, end = slots[slot_idx]
                
                # Check conflicts
                lecturer_conflict = lecturer_id and conflict_tracker.has_lecturer_conflict(lecturer_id, day, slot_idx)
                program_conflict = program_id and conflict_tracker.has_program_conflict(program_id, year, day, slot_idx, new_alloc=allocs[0])
                
                if lecturer_conflict or program_conflict:
                    continue
                
                # Find venue
                venue = venue_allocator.find_efficient_venue(total_students, faculty_venues, day, slot_idx, "best_fit")
                
                if venue:
                    # Check for duplicate
                    entry_key = (venue.id, day, start, end)
                    
                    if entry_key in batch_entry_keys:
                        continue
                    
                    if cache.is_duplicate_entry(venue.id, day, start, end):
                        continue
                    
                    batch_entry_keys.add(entry_key)
                    
                    # Create timetable entries
                    for alloc in allocs:
                        timetable_entries.append(TempTimetable(
                            course_allocation=alloc,
                            venue=venue,
                            day=day,
                            start_time=start,
                            end_time=end
                        ))
                    
                    # Update conflict tracker (use add_merged_schedule for correct deduplication)
                    conflict_tracker.add_merged_schedule(allocs, venue.id, day, slot_idx, cache)
                    
                    scheduled_count += len(allocs)
                    assigned = True
                    
                    if is_optimized:
                        optimized_count += 1
                        if is_capacity_issue:
                            capacity_fixed += 1
                        if is_conflict_issue:
                            conflicts_resolved += 1
                    
                    # Create merged group if needed — but NOT for tasks already
                    # backed by a CombinedCourseGroup (see basic-mode note).
                    if is_merged and not task.get('combined_group'):
                        norm = task.get('norm_code', normalize_course_code(code))
                        mg = AutoMergedExamGroup(
                            base_course=allocs[0],
                            merged_code=norm,
                            total_students=total_students,
                            date=day,
                            start_time=start,
                            end_time=end,
                            venue=venue
                        )
                        new_merged_groups.append((mg, allocs))
                    
                    scheduled_courses_list.append(
                        f"{code} (Year {year}, [{course_type}]) in {venue.code} ({day} {start}-{end})"
                    )
                    break
        
        if not assigned:
            unscheduled_courses_list.append(f"{code} (Year {year}, {course_type}) - Could not schedule")
            unscheduled_in_batch.append(task)
    
    # Save to database
    if timetable_entries:
        # Final duplicate check
        final_entries = []
        for entry in timetable_entries:
            if not cache.is_duplicate_entry(entry.venue_id, entry.day, entry.start_time, entry.end_time):
                final_entries.append(entry)
        
        if final_entries:
            try:
                TempTimetable.objects.bulk_create(final_entries)
                for entry in final_entries:
                    cache.add_to_temp_cache(entry.venue_id, entry.day, entry.start_time, entry.end_time)
            except IntegrityError:
                for entry in final_entries:
                    try:
                        if not cache.is_duplicate_entry(entry.venue_id, entry.day, entry.start_time, entry.end_time):
                            entry.save()
                            cache.add_to_temp_cache(entry.venue_id, entry.day, entry.start_time, entry.end_time)
                    except IntegrityError:
                        pass
    
    for mg, allocs in new_merged_groups:
        try:
            mg.save()
            mg.merged_courses.set(allocs)
        except Exception as e:
            print(f"Error saving merged group: {e}")
    
    return (scheduled_count, unscheduled_in_batch, scheduled_courses_list, 
            unscheduled_courses_list, optimized_count, capacity_fixed, conflicts_resolved)


def process_advanced_stable_fallback(unscheduled_tasks, config, all_venues, days, slots,
                                     conflict_tracker, venue_allocator, cache, courses_to_optimize):
    """Fallback processing for advanced stable scheduler"""
    if not unscheduled_tasks:
        return 0, [], [], [], 0, 0, 0
    
    scheduled_count = 0
    still_unscheduled = []
    scheduled_courses_list = []
    unscheduled_courses_list = []
    
    optimized_count = 0
    capacity_fixed = 0
    conflicts_resolved = 0
    
    timetable_entries = []
    batch_entry_keys = set()
    
    # Flatten tasks
    flat_courses = []
    for task in unscheduled_tasks:
        if isinstance(task, dict):
            flat_courses.extend(task['merged'])
        else:
            flat_courses.append(task)
    
    for course in flat_courses:
        try:
            year = cache.get_course_year(course)
        except Exception:
            unscheduled_courses_list.append(f"{course.course_code} - Year detection failed")
            still_unscheduled.append(course)
            continue
        
        # Check if this course is being optimized
        is_optimized = False
        is_capacity_issue = False
        is_conflict_issue = False
        
        if course.id in courses_to_optimize:
            is_optimized = True
            reason = courses_to_optimize[course.id].get('reason', '')
            if 'INEFFICIENT' in reason or 'OVERFLOW' in reason:
                is_capacity_issue = True
            if 'conflict' in reason.lower():
                is_conflict_issue = True
        
        if is_optimized:
            if is_capacity_issue:
                course_type = "OPTIMIZE-CAPACITY"
            elif is_conflict_issue:
                course_type = "OPTIMIZE-CONFLICT"
            else:
                course_type = "OPTIMIZE"
        elif year == 1:
            course_type = "FIRST YEAR"
        else:
            course_type = "SKIPPED"
        
        total_students = course.number_of_students or 0
        lecturer_id = course.lecturer.id if course.lecturer else None
        program_id = course.program.id if course.program else None
        
        assigned = False
        preferred_slots = get_course_slot_preference(course, slots)
        preferred_days = days.copy()
        random.shuffle(preferred_days)
        
        for day in preferred_days:
            if assigned:
                break
            
            for slot_idx in preferred_slots:
                start, end = slots[slot_idx]
                
                lecturer_conflict = lecturer_id and conflict_tracker.has_lecturer_conflict(lecturer_id, day, slot_idx)
                program_conflict = program_id and conflict_tracker.has_program_conflict(program_id, year, day, slot_idx, new_alloc=course)
                
                if lecturer_conflict or program_conflict:
                    continue
                
                venue = venue_allocator.find_efficient_venue(total_students, all_venues, day, slot_idx, "best_fit")
                
                if venue:
                    # Check for duplicate
                    entry_key = (venue.id, day, start, end)
                    
                    if entry_key in batch_entry_keys:
                        continue
                    
                    if cache.is_duplicate_entry(venue.id, day, start, end):
                        continue
                    
                    batch_entry_keys.add(entry_key)
                    
                    timetable_entries.append(TempTimetable(
                        course_allocation=course,
                        venue=venue,
                        day=day,
                        start_time=start,
                        end_time=end
                    ))
                    
                    conflict_tracker.add_schedule(
                        lecturer_id, program_id, year, venue.id, day, slot_idx,
                        course.course_code, alloc=course
                    )
                    
                    scheduled_count += 1
                    assigned = True
                    
                    if is_optimized:
                        optimized_count += 1
                        if is_capacity_issue:
                            capacity_fixed += 1
                        if is_conflict_issue:
                            conflicts_resolved += 1
                    
                    scheduled_courses_list.append(
                        f"{course.course_code} (Year {year}, [{course_type}]) in {venue.code} [FALLBACK] ({day} {start}-{end})"
                    )
                    break
        
        if not assigned:
            unscheduled_courses_list.append(f"{course.course_code} (Year {year}, {course_type}) - UNSCHEDULABLE")
            still_unscheduled.append(course)
    
    if timetable_entries:
        # Final duplicate check
        final_entries = []
        for entry in timetable_entries:
            if not cache.is_duplicate_entry(entry.venue_id, entry.day, entry.start_time, entry.end_time):
                final_entries.append(entry)
        
        if final_entries:
            try:
                TempTimetable.objects.bulk_create(final_entries)
                for entry in final_entries:
                    cache.add_to_temp_cache(entry.venue_id, entry.day, entry.start_time, entry.end_time)
            except IntegrityError:
                for entry in final_entries:
                    try:
                        if not cache.is_duplicate_entry(entry.venue_id, entry.day, entry.start_time, entry.end_time):
                            entry.save()
                            cache.add_to_temp_cache(entry.venue_id, entry.day, entry.start_time, entry.end_time)
                    except IntegrityError:
                        pass
    
    return (scheduled_count, still_unscheduled, scheduled_courses_list, 
            unscheduled_courses_list, optimized_count, capacity_fixed, conflicts_resolved)


# ============================================
# Phase: Venue Capacity Optimisation
# Shared by both Basic and Advanced stable modes.
# ============================================

def optimize_venue_assignments(days: List[str], slots: List[Tuple[dtime, dtime]]) -> Dict[str, int]:
    """
    Venue Capacity Optimisation — runs after all scheduling is done.

    Within every (day, slot) timeslot, non-merged TempTimetable entries are
    re-matched to venues so that the largest course gets the largest room and
    the smallest gets the smallest room.  Merged-group entries (tracked via
    AutoMergedExamGroup) are exempted from swapping to preserve their integrity.

    Algorithm per timeslot
    ----------------------
    1. Fetch all TempTimetable rows for (day, slot_start, slot_end).
    2. Partition into exempt (merged) and eligible (free to swap).
    3. Collect distinct venues used by eligible rows only.
    4. Sort eligible rows by student count descending.
    5. Sort venues by capacity descending.
    6. Index-match: biggest course → biggest room, smallest → smallest.
    7. Skip DB write if pairing is already optimal.
    8. Otherwise apply new venue_id per eligible row inside transaction.atomic().

    Returns a summary dict:
        timeslots_examined, timeslots_optimised, swaps_made, errors.
    """
    safe_print("\n" + "=" * 70)
    safe_print("VENUE OPTIMISATION: re-matching room sizes to class sizes per timeslot")
    safe_print("=" * 70)

    # Build exempt alloc-ID set from all merged exam groups AND all
    # CombinedCourseGroups — both represent allocations that must share one
    # venue/slot and must never be individually reassigned by the swap pass.
    try:
        merged_alloc_ids: Set[int] = set(
            AutoMergedExamGroup.objects.values_list(
                'merged_courses__id', flat=True
            ).distinct()
        )
        merged_alloc_ids |= get_combined_course_group_alloc_ids()
        safe_print(
            f"[VenueOpt] Merged-group alloc IDs: {len(merged_alloc_ids)} "
            f"(exempted from swapping)"
        )
    except Exception as exc:
        safe_print(f"[VenueOpt] ERROR loading merged alloc IDs: {exc} — aborting")
        return {'timeslots_examined': 0, 'timeslots_optimised': 0,
                'swaps_made': 0, 'errors': 1}

    timeslots_examined  = 0
    timeslots_optimised = 0
    swaps_made          = 0
    errors              = 0

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

                # CRITICAL: never assign the same venue to two entries in this
                # timeslot. Index-match 1:1 up to n_venues; any entries beyond
                # that (more courses than distinct venues available to swap
                # among) are left on their current venue untouched rather than
                # wrapping around and colliding with an earlier entry.
                if len(eligible_sorted) > n_venues:
                    safe_print(
                        f"[VenueOpt] WARNING {day} {slot_start}-{slot_end}: "
                        f"{len(eligible_sorted)} eligible entries but only "
                        f"{n_venues} distinct venues in pool — "
                        f"{len(eligible_sorted) - n_venues} entries left "
                        f"unswapped to avoid creating a double-booking"
                    )
                pairs = list(zip(eligible_sorted, venues_sorted))  # 1:1, no wraparound

                if not any(e.venue_id != v.id for e, v in pairs):
                    continue

                with transaction.atomic():
                    for entry, new_venue in pairs:
                        if entry.venue_id != new_venue.id:
                            entry.venue    = new_venue
                            entry.venue_id = new_venue.id
                            entry.save(update_fields=['venue'])
                            swaps_made += 1

                timeslots_optimised += 1
                if timeslots_optimised <= 10 or timeslots_optimised % 50 == 0:
                    safe_print(
                        f"[VenueOpt] Optimised {day} {slot_start}–{slot_end}: "
                        f"{len(eligible_sorted)} entries across {n_venues} venues"
                    )

            except Exception as exc:
                errors += 1
                safe_print(f"[VenueOpt] ERROR at {day} {slot_start}–{slot_end}: {exc}")

    safe_print(
        f"[VenueOpt] Done — examined={timeslots_examined} | "
        f"optimised={timeslots_optimised} | swaps={swaps_made} | errors={errors}"
    )
    safe_print("=" * 70)
    return {
        'timeslots_examined':  timeslots_examined,
        'timeslots_optimised': timeslots_optimised,
        'swaps_made':          swaps_made,
        'errors':              errors,
    }


# ============================================
# Django Views for Both Scheduler Modes
# ============================================

# BASIC STABLE VIEWS
class BasicStableProgressView(View):
    """View to get basic stable scheduler progress"""
    def get(self, request):
        return JsonResponse(basic_stable_progress)


@method_decorator(csrf_exempt, name='dispatch')
class StartBasicStableSchedulingView(View):
    """Start the basic stable scheduling process"""
    def post(self, request):
        global basic_stable_progress
        with basic_stable_lock:
            basic_stable_progress.update({
                'status': 'running',
                'progress': 0,
                'current_action': 'Initializing basic stable scheduler...',
                'scheduled_count': 0,
                'remaining_count': 0,
                'batch_info': '',
                'total_courses': 0,
                'current_batch': 0,
                'total_batches': 0,
                'message': '',
                'console_output': [],
                'scheduled_courses': [],
                'unscheduled_courses': [],
                'stable_courses_preserved': 0,
                'first_year_scheduled': 0,
                'skipped_rescheduled': 0
            })
        
        scheduler_thread = threading.Thread(target=run_basic_stable_scheduler_thread)
        scheduler_thread.daemon = True
        scheduler_thread.start()
        
        return JsonResponse({
            'status': 'started',
            'message': 'Basic stable scheduling started - preserving existing schedules, only scheduling first year and skipped courses'
        })


@method_decorator(csrf_exempt, name='dispatch')
class CancelBasicStableSchedulingView(View):
    """Cancel the basic stable scheduling process"""
    def post(self, request):
        with basic_stable_lock:
            basic_stable_progress.update({
                'status': 'cancelled',
                'current_action': 'Scheduling cancelled by user',
                'message': 'Basic stable scheduling was cancelled by user'
            })
        return JsonResponse({'status': 'cancelled', 'message': 'Basic stable scheduling cancelled'})


# ADVANCED STABLE VIEWS
class AdvancedStableProgressView(View):
    """View to get advanced stable scheduler progress"""
    def get(self, request):
        return JsonResponse(advanced_stable_progress)


@method_decorator(csrf_exempt, name='dispatch')
class StartAdvancedStableSchedulingView(View):
    """Start the advanced stable scheduling process"""
    def post(self, request):
        global advanced_stable_progress
        with advanced_stable_lock:
            advanced_stable_progress.update({
                'status': 'running',
                'progress': 0,
                'current_action': 'Initializing advanced stable scheduler...',
                'scheduled_count': 0,
                'remaining_count': 0,
                'batch_info': '',
                'total_courses': 0,
                'current_batch': 0,
                'total_batches': 0,
                'message': '',
                'console_output': [],
                'scheduled_courses': [],
                'unscheduled_courses': [],
                'stable_courses_preserved': 0,
                'courses_optimized': 0,
                'capacity_issues_fixed': 0,
                'conflicts_resolved': 0,
                'skipped_rescheduled': 0
            })
        
        scheduler_thread = threading.Thread(target=run_advanced_stable_scheduler_thread)
        scheduler_thread.daemon = True
        scheduler_thread.start()
        
        return JsonResponse({
            'status': 'started',
            'message': 'Advanced stable scheduling started - optimizing courses with issues, preserving perfect schedules'
        })


@method_decorator(csrf_exempt, name='dispatch')
class CancelAdvancedStableSchedulingView(View):
    """Cancel the advanced stable scheduling process"""
    def post(self, request):
        with advanced_stable_lock:
            advanced_stable_progress.update({
                'status': 'cancelled',
                'current_action': 'Scheduling cancelled by user',
                'message': 'Advanced stable scheduling was cancelled by user'
            })
        return JsonResponse({'status': 'cancelled', 'message': 'Advanced stable scheduling cancelled'})


# Function-based views for easy URL mapping
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@transaction.atomic
def run_basic_stable_scheduler(request):
    """Start the basic stable scheduler and redirect to progress page"""
    StartBasicStableSchedulingView.as_view()(request)
    return redirect('basic_stable_progress_page')


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@transaction.atomic
def run_advanced_stable_scheduler(request):
    """Start the advanced stable scheduler and redirect to progress page"""
    StartAdvancedStableSchedulingView.as_view()(request)
    return redirect('advanced_stable_progress_page')