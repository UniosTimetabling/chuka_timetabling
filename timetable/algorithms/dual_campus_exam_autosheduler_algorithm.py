"""
Dual-Campus Exam Auto-Scheduler (v53 — Comprehensive Logging with Text File Output)
==================================================================================
Enhanced with:
- Full logging to text file with timestamp-based naming
- Integration with progress tracking module
- Detailed decision logging
- Comprehensive audit trail
- Same logging approach as the regular scheduler
"""

import datetime
import json
import os
import re
import sys
import threading
import time
import traceback
import logging
from collections import defaultdict
from functools import lru_cache, wraps
from typing import Dict, List, Optional, Set, Tuple, Any
from pathlib import Path

scheduler_logger = logging.getLogger("scheduler")

from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.db import IntegrityError, OperationalError, connection, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from campuses_timetable.models import (
    Campus,
    CampusCourseAllocation,
    CampusExamSchedulerConfig,
    CampusExamTempTimetable,
    CampusExamTimetable,
)
from course_allocation.models import CourseAllocation, CombinedCourseGroup
from room_management.models import Venue
from timetable.models import (
    ExamSchedulerConfig,
    ExamTempTimetable,
    ExamTimetable,
    MergedCourseGroup,
    SharedVenueExamGroup,
)

# ─────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────
TRAVEL_GAP_HOURS = 2
SLOT_BREAK_MINUTES = 60
CONSECUTIVE_GAP_SLOTS = 1
NEAR_FIT_THRESHOLD = 15
OVERFLOW_NEAR_FIT_THRESHOLD = 30
_BULK_FLUSH_SIZE = 200
DEBUG_VERBOSE = False

# ─────────────────────────────────────────────────────────
# LOGGING SETUP - Uses same approach as regular scheduler
# ─────────────────────────────────────────────────────────

# Global log file
_log_file_handle = None
_log_file_path = None
_log_session_id = None
_log_lock = threading.Lock()

def init_logger():
    """Initialize the log file with timestamp."""
    global _log_file_handle, _log_file_path, _log_session_id
    
    # Create logs directory if it doesn't exist
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Create log file with timestamp
    _log_session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_file_path = log_dir / f"dual_scheduler_{_log_session_id}.txt"
    
    # Open log file
    _log_file_handle = open(_log_file_path, 'w', encoding='utf-8')
    
    # Write header
    _write_log("="*80)
    _write_log("DUAL-CAMPUS EXAM SCHEDULER LOG")
    _write_log(f"Session: {_log_session_id}")
    _write_log(f"Started: {datetime.datetime.now().isoformat()}")
    _write_log("="*80)
    _write_log("")

def _write_log(message):
    """Write a message to the log file."""
    global _log_file_handle
    if _log_file_handle:
        with _log_lock:
            try:
                _log_file_handle.write(message + "\n")
                _log_file_handle.flush()
            except Exception:
                pass

def log_info(message):
    """Log an informational message."""
    timestamp = datetime.datetime.now().isoformat()
    formatted = f"[{timestamp}] [INFO] {message}"
    _write_log(formatted)
    safe_print(formatted)

def log_warning(message):
    """Log a warning message."""
    timestamp = datetime.datetime.now().isoformat()
    formatted = f"[{timestamp}] [WARNING] {message}"
    _write_log(formatted)
    safe_print(formatted)

def log_error(message):
    """Log an error message."""
    timestamp = datetime.datetime.now().isoformat()
    formatted = f"[{timestamp}] [ERROR] {message}"
    _write_log(formatted)
    safe_print(formatted)

def log_debug(message):
    """Log a debug message."""
    if DEBUG_VERBOSE:
        timestamp = datetime.datetime.now().isoformat()
        formatted = f"[{timestamp}] [DEBUG] {message}"
        _write_log(formatted)
        safe_print(formatted)

def log_phase(phase_name):
    """Log a phase transition."""
    timestamp = datetime.datetime.now().isoformat()
    formatted = f"\n[{timestamp}] [PHASE] {'='*60}\n[{timestamp}] [PHASE] STARTING PHASE: {phase_name}\n[{timestamp}] [PHASE] {'='*60}"
    _write_log(formatted)
    safe_print(formatted)

def log_decision(course_id, course_code, action, reason, details=None):
    """Log a scheduling decision."""
    timestamp = datetime.datetime.now().isoformat()
    detail_str = f" | {json.dumps(details)}" if details else ""
    formatted = f"[{timestamp}] [DECISION] {course_code}({course_id}) → {action} | Reason: {reason}{detail_str}"
    _write_log(formatted)

def log_placement(course_id, course_code, venue_id, venue_code, date, slot, students, phase=None):
    """Log a successful placement."""
    timestamp = datetime.datetime.now().isoformat()
    phase_str = f" | Phase: {phase}" if phase else ""
    formatted = f"[{timestamp}] [PLACEMENT] {course_code}({course_id}) → {venue_code}({venue_id}) | Date: {date} | Slot: {slot} | Students: {students}{phase_str}"
    _write_log(formatted)

def log_conflict(conflict_type, entities, details=None):
    """Log a conflict detection."""
    timestamp = datetime.datetime.now().isoformat()
    formatted = f"[{timestamp}] [CONFLICT] {conflict_type} | Entities: {entities} | Details: {details or 'N/A'}"
    _write_log(formatted)

def log_summary(total_scheduled, total_allocations, success_rate, phase_stats):
    """Log the final summary."""
    timestamp = datetime.datetime.now().isoformat()
    _write_log("")
    _write_log("="*80)
    _write_log(f"SCHEDULING SUMMARY at {timestamp}")
    _write_log(f"  Total Allocations: {total_allocations}")
    _write_log(f"  Scheduled: {total_scheduled}")
    _write_log(f"  Success Rate: {success_rate:.1f}%")
    _write_log(f"  Phase Statistics:")
    for phase, count in phase_stats.items():
        _write_log(f"    {phase}: {count}")
    _write_log("="*80)

def close_logger():
    """Close the log file."""
    global _log_file_handle
    if _log_file_handle:
        try:
            _write_log("")
            _write_log(f"Session ended: {datetime.datetime.now().isoformat()}")
            _write_log("="*80)
            _log_file_handle.close()
        except Exception:
            pass
        _log_file_handle = None

def get_log_session_id():
    """Get the current log session ID."""
    return _log_session_id

def get_log_file_path():
    """Get the current log file path."""
    return str(_log_file_path) if _log_file_path else None


# ─────────────────────────────────────────────────────────
# PROGRESS TRACKING
# ─────────────────────────────────────────────────────────
dual_exam_progress: Dict = {
    "status": "idle", "progress": 0, "current_action": "",
    "scheduled_count": 0, "remaining_count": 0,
    "batch_info": "", "total_courses": 0, "message": "",
    "console_output": [], "main_scheduled": [], "campus_scheduled": [],
    "unscheduled": [], "unified_groups": [], "cross_campus_lecturers": [],
    "conflict_stats": {"detected": 0, "resolved": 0, "unresolved": 0, "resolution_rate": 0},
    "log_session": "", "log_file": "",
}
_progress_lock = threading.Lock()
_print_lock = threading.Lock()
_log_buffer: List[str] = []

# ─────────────────────────────────────────────────────────
# CombinedCourseGroup cache
# ─────────────────────────────────────────────────────────
_combined_group_cache: Dict[int, frozenset] = {}
_combined_families: Dict[str, List[int]] = {}


def _build_combined_group_cache():
    """Load all CombinedCourseGroup records and populate caches."""
    global _combined_group_cache, _combined_families
    _combined_group_cache = {}
    _combined_families = {}

    try:
        groups = list(
            CombinedCourseGroup.objects
            .prefetch_related('allocations')
            .select_related('primary_allocation')
        )
    except Exception as exc:
        safe_print(f"[CombinedGroup] WARNING: could not load CombinedCourseGroup: {exc}")
        log_warning(f"Failed to load CombinedCourseGroup: {exc}")
        return

    for group in groups:
        alloc_ids = list(group.allocations.values_list('id', flat=True))
        gk = frozenset([group.id])
        for aid in alloc_ids:
            if aid in _combined_group_cache:
                _combined_group_cache[aid] = _combined_group_cache[aid] | gk
            else:
                _combined_group_cache[aid] = gk

        key = f"__combined__{group.group_code}"
        _combined_families[key] = alloc_ids

    safe_print(
        f"[CombinedGroup] Loaded {len(groups)} combined groups covering "
        f"{len(_combined_group_cache)} allocations"
    )
    log_info(f"Loaded {len(groups)} combined groups covering {len(_combined_group_cache)} allocations")


def _combined_group_ids_for(alloc_id: int) -> frozenset:
    return _combined_group_cache.get(alloc_id, frozenset())


def _combined_group_are_paired(alloc_a_id: int, alloc_b_id: int) -> bool:
    a_groups = _combined_group_ids_for(alloc_a_id)
    if not a_groups:
        return False
    b_groups = _combined_group_ids_for(alloc_b_id)
    return bool(a_groups & b_groups)


# ─────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────
def safe_print(*args):
    global _log_buffer
    try:
        msg = " ".join(str(a) for a in args)
        with _print_lock:
            _log_buffer.append(msg)
            if len(_log_buffer) > 500:
                _log_buffer = _log_buffer[-500:]
        try:
            sys.stdout.write(msg + "\n")
            sys.stdout.flush()
        except Exception:
            pass
    except Exception:
        pass


def update_progress(progress, action, scheduled=None, remaining=None,
                    batch_info="", console_msg="", main_scheduled=None,
                    campus_scheduled=None, unscheduled=None, unified_groups=None,
                    cross_campus_lecturers=None, conflict_stats=None):
    with _progress_lock:
        dual_exam_progress["progress"] = int(progress)
        dual_exam_progress["current_action"] = action
        if scheduled is not None: dual_exam_progress["scheduled_count"] = scheduled
        if remaining is not None: dual_exam_progress["remaining_count"] = remaining
        if batch_info: dual_exam_progress["batch_info"] = batch_info
        if console_msg:
            dual_exam_progress["console_output"].append(console_msg)
            if len(dual_exam_progress["console_output"]) > 200:
                dual_exam_progress["console_output"] = (
                    dual_exam_progress["console_output"][-200:])
        if main_scheduled is not None:
            dual_exam_progress["main_scheduled"] = main_scheduled
        if campus_scheduled is not None:
            dual_exam_progress["campus_scheduled"] = campus_scheduled
        if unscheduled is not None:
            dual_exam_progress["unscheduled"] = unscheduled
        if unified_groups is not None:
            dual_exam_progress["unified_groups"] = unified_groups
        if cross_campus_lecturers is not None:
            dual_exam_progress["cross_campus_lecturers"] = cross_campus_lecturers
        if conflict_stats is not None:
            dual_exam_progress["conflict_stats"] = conflict_stats
        
        # Update log session info
        if get_log_session_id():
            dual_exam_progress["log_session"] = get_log_session_id()
        if get_log_file_path():
            dual_exam_progress["log_file"] = get_log_file_path()


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
            cursor.execute("PRAGMA cache_size=-32000;")
            cursor.execute("PRAGMA temp_store=MEMORY;")
        safe_print("WAL mode enabled")
        log_info("WAL mode enabled")
    except Exception as e:
        safe_print(f"WAL mode note: {e}")
        log_warning(f"WAL mode could not be enabled: {e}")


def generate_exam_slots(start_time, end_time, slot_hours):
    slots, today = [], datetime.date.today()
    cur = datetime.datetime.combine(today, start_time)
    end_dt = datetime.datetime.combine(today, end_time)
    delta = datetime.timedelta(hours=slot_hours)
    brk = datetime.timedelta(minutes=SLOT_BREAK_MINUTES)
    while cur + delta <= end_dt:
        slots.append((cur.time(), (cur + delta).time()))
        cur = cur + delta + brk
    return slots


@lru_cache(maxsize=4096)
def normalize_course_code(code: str) -> str:
    if not code:
        return ""
    s = str(code).strip().upper()
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"\s+[A-Z]$", "", s)
    s = re.sub(r"\s+[A-Z]\s*$", "", s)
    s = re.sub(r"-[A-Z]$", "", s)
    s = re.sub(r"_[A-Z]$", "", s)
    s = re.sub(r"[\-_.]", " ", s)
    m = re.search(r"([A-Z]+)\s*(\d+)", s)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return re.sub(r"\s+", "", s)


@lru_cache(maxsize=4096)
def is_postgraduate_course(course_code: str) -> bool:
    m = re.search(r"\d{3,4}", str(course_code or ""))
    if m:
        return int(m.group(0)) >= 700
    return False


def is_evening_slot(slot_start: datetime.time) -> bool:
    return slot_start >= datetime.time(17, 0)


def venue_exam_capacity(venue, spacing_ratio: float = 1.0) -> int:
    ec = getattr(venue, "exam_capacity", None)
    phys = getattr(venue, "capacity", None)
    hard_cap = int(ec) if ec else (int(phys) if phys else 0)
    if hard_cap <= 0:
        return 0
    effective_ratio = min(float(spacing_ratio), 1.0)
    return max(1, int(hard_cap * effective_ratio))


def course_student_count(course) -> int:
    return max(int(getattr(course, "number_of_students", 0) or 0), 1)


def family_total_students(group_courses: List) -> int:
    return sum(course_student_count(c) for c in group_courses)


def _prog_year_key(course) -> str:
    prog = getattr(course, "program", None)
    if not prog:
        return ""
    pc = getattr(course, "program_course", None)
    if pc is not None:
        year_val = getattr(pc, "year", None)
        if year_val and 1 <= int(year_val) <= 6:
            return f"{prog.id}_year_{int(year_val)}"
    code = str(getattr(course, "course_code", "") or "").strip()
    if code:
        m = re.search(r'\d{3,4}', code)
        if m:
            first_digit = int(m.group(0)[0])
            if 1 <= first_digit <= 6:
                return f"{prog.id}_year_{first_digit}"
    return f"{prog.id}_unknown"


def _exam_get_intake(alloc) -> str:
    return getattr(alloc, 'intake', 'normal') or 'normal'


def _exam_is_elective(alloc) -> bool:
    return bool(getattr(alloc, 'is_elective', False))


def _exam_get_selection_group_id(alloc) -> Optional[int]:
    try:
        sg = getattr(alloc, 'selection_group', None)
        return sg.id if sg else None
    except Exception:
        return None


def _exam_get_specialization_stem_id(alloc) -> Optional[int]:
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.id if st else None
    except Exception:
        return None


def _exam_get_specialization_category_id(alloc) -> Optional[int]:
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.category_id if st else None
    except Exception:
        return None


def exam_is_collision_exempt(c1, c2) -> bool:
    if _exam_get_intake(c1) != _exam_get_intake(c2):
        return True

    # ── SpecializationStem takes priority over everything else ────────────
    # A student picks ONE stem and sits the exam for EVERY course in it, so:
    #   * same stem            → NEVER exempt — must clash-check like ordinary
    #     mandatory exams, even if a course is *also* flagged Elective or sits
    #     in a SelectionGroup (checked here BEFORE those broader exemptions
    #     so a stem pairing can never slip through them).
    #   * different stems,
    #     same category        → exempt (student never sits both stems)
    #   * different categories
    #     / only one side has
    #     a stem               → falls through to the checks below
    st1 = _exam_get_specialization_stem_id(c1)
    st2 = _exam_get_specialization_stem_id(c2)
    if st1 is not None and st2 is not None and st1 == st2:
        return False
    if st1 is not None and st2 is not None and st1 != st2:
        cat1 = _exam_get_specialization_category_id(c1)
        cat2 = _exam_get_specialization_category_id(c2)
        if cat1 is not None and cat1 == cat2:
            return True

    # ── Elective / SelectionGroup ──────────────────────────────────────────
    # A student sits the exam for only ONE course in a selection group, so no
    # real student ever needs two SG exams at once — safe to overlap.
    # BUGFIX: this branch previously mirrored the SpecializationStem polarity
    # (same-group → False) instead of being inverted for SG semantics, which
    # forced same-group exam courses apart instead of letting them share a
    # slot — defeating the feature entirely. Also broadened to match the
    # regular/dual-campus teaching schedulers: any course carrying a
    # selection_group is exempt, not only against its exact group-mates.
    if _exam_is_elective(c1) or _exam_is_elective(c2):
        return True
    if _exam_get_selection_group_id(c1) is not None or _exam_get_selection_group_id(c2) is not None:
        return True

    if _combined_group_are_paired(c1.id, c2.id):
        return True
    return False


# ─────────────────────────────────────────────────────────
# DataAnalysisReport
# ─────────────────────────────────────────────────────────
class DataAnalysisReport:
    def __init__(self):
        self.shared_unit_groups: Dict[str, List[int]] = {}
        self.cohort_course_counts: Dict[str, int] = {}
        self.cohort_conflict_graph: Dict[int, Set[int]] = defaultdict(set)
        self.conflict_degree: Dict[int, int] = {}
        self.total_cohort_slot_demand: int = 0
        self.analysis_log: List[str] = []
        self.courses_by_program: Dict[str, List] = defaultdict(list)
        self.duplicate_allocation_ids: Set[int] = set()
        self.courses_by_py: Dict[str, List] = defaultdict(list)
        self.py_courses_per_day: Dict[str, int] = {}
        self.sg_course_counts: Dict[int, int] = defaultdict(int)
        self.normalization_map: Dict[str, str] = {}
        self.family_total_students: Dict[str, int] = {}

    def log(self, msg: str):
        self.analysis_log.append(msg)
        safe_print(f"[DataAnalysis] {msg}")
        log_debug(f"[DataAnalysis] {msg}")


def analyze_courses(all_courses: List) -> DataAnalysisReport:
    report = DataAnalysisReport()
    
    if not all_courses:
        log_info("No courses to analyze")
        return report

    seen_ids: Set[int] = set()
    deduplicated = []
    for c in all_courses:
        if c.id in seen_ids:
            report.duplicate_allocation_ids.add(c.id)
            report.log(f"  DUPLICATE: course_allocation_id={c.id}")
            log_warning(f"Duplicate course allocation found: {c.id}")
        else:
            seen_ids.add(c.id)
            deduplicated.append(c)

    report.log(f"Analysing {len(deduplicated)} courses (after deduplication)...")
    log_info(f"Analyzing {len(deduplicated)} courses")
    all_courses = deduplicated

    by_norm: Dict[str, List] = defaultdict(list)
    for c in all_courses:
        raw_code = getattr(c, "course_code", "") or ""
        norm_code = normalize_course_code(raw_code)
        if norm_code not in report.normalization_map:
            report.normalization_map[norm_code] = raw_code
        by_norm[norm_code].append(c)

    for norm_code, group in by_norm.items():
        if len(group) < 2:
            continue
        true_total = family_total_students(group)
        report.family_total_students[norm_code] = true_total
        if DEBUG_VERBOSE:
            report.log(
                f"  FAMILY '{norm_code}': {len(group)} variants | "
                f"TRUE total={true_total} | per-variant={[course_student_count(c) for c in group]}"
            )
        report.shared_unit_groups[norm_code] = [c.id for c in group]

    for combined_key, cids in _combined_families.items():
        valid_ids = [cid for cid in cids if any(c.id == cid for c in all_courses)]
        if len(valid_ids) >= 2:
            report.shared_unit_groups[combined_key] = valid_ids
            n_combined = sum(
                course_student_count(c)
                for c in all_courses if c.id in set(valid_ids)
            )
            report.family_total_students[combined_key] = n_combined
            if DEBUG_VERBOSE:
                report.log(f"  COMBINED GROUP '{combined_key}': {len(valid_ids)} members | "
                           f"total students={n_combined}")
            log_info(f"Combined group '{combined_key}': {len(valid_ids)} members, {n_combined} students")

    cohort_counts: Dict[str, int] = defaultdict(int)
    for c in all_courses:
        pk = _prog_year_key(c)
        if pk:
            cohort_counts[pk] += 1
            report.courses_by_py[pk].append(c)
        prog = getattr(c, "program", None)
        if prog:
            report.courses_by_program[str(prog.id)].append(c)

    report.cohort_course_counts = dict(cohort_counts)

    total_edges = 0
    for pk, members in report.courses_by_py.items():
        n = len(members)
        if n < 2:
            continue
        norm_codes = [normalize_course_code(m.course_code or "") for m in members]
        for i in range(n):
            for j in range(i + 1, n):
                if norm_codes[i] == norm_codes[j]:
                    continue
                ci, cj = members[i], members[j]
                if _combined_group_are_paired(ci.id, cj.id):
                    continue
                if exam_is_collision_exempt(ci, cj):
                    continue
                report.cohort_conflict_graph[ci.id].add(cj.id)
                report.cohort_conflict_graph[cj.id].add(ci.id)
                total_edges += 1

    report.log(f"Conflict graph: {total_edges} edges")
    if total_edges > 0:
        log_info(f"Conflict graph built with {total_edges} edges")

    for c in all_courses:
        report.conflict_degree[c.id] = len(report.cohort_conflict_graph.get(c.id, set()))

    max_cohort_load = max(cohort_counts.values()) if cohort_counts else 0
    report.total_cohort_slot_demand = max_cohort_load
    report.log(f"Max cohort load: {max_cohort_load} -> minimum slots required")

    return report


# ─────────────────────────────────────────────────────────
# SchedulingStrategy (PreSchedulingIntelligence)
# ─────────────────────────────────────────────────────────
class SchedulingStrategy:
    NORMAL = "NORMAL"
    COMPACT = "COMPACT"
    DENSE = "DENSE"
    OVERFLOW = "OVERFLOW"

    def __init__(self):
        self.mode: str = self.NORMAL
        self.seat_pressure: float = 0.0
        self.total_seat_supply: int = 0
        self.total_student_demand: int = 0
        self.high_pressure_cohorts: List[str] = []
        self.overloaded_lecturers: List[str] = []
        self.multi_cohort_lecturers: List[str] = []
        self.bottleneck_days: List[str] = []
        self.near_fit_threshold: int = NEAR_FIT_THRESHOLD
        self.use_evening_slots: bool = False
        self.relax_consecutive: bool = False
        self.warnings: List[str] = []
        self.infeasible_cohorts: List[str] = []
        self.analysis_log: List[str] = []

    def log(self, msg: str):
        self.analysis_log.append(msg)
        safe_print(f"[PSI] {msg}")
        log_debug(f"[PSI] {msg}")

    def warn(self, msg: str):
        self.warnings.append(msg)
        safe_print(f"[PSI ⚠] {msg}")
        log_warning(f"[PSI] {msg}")

    def summary(self) -> str:
        lines = [
            f"══════════════════════════════════════════",
            f"  PRE-SCHEDULING INTELLIGENCE — v53",
            f"══════════════════════════════════════════",
            f"  Strategy Mode  : {self.mode}",
            f"  Seat Pressure  : {self.seat_pressure:.1%}",
            f"  Near-Fit Thresh: {self.near_fit_threshold}",
            f"  Use Evenings   : {self.use_evening_slots}",
            f"  Relax Consec.  : {self.relax_consecutive}",
        ]
        if self.warnings:
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")
        lines.append(f"══════════════════════════════════════════")
        return "\n".join(lines)


class PreSchedulingIntelligence:
    def __init__(self, all_courses: List, analysis: DataAnalysisReport,
                 config, venues: List):
        self.courses = all_courses
        self.analysis = analysis
        self.config = config
        self.venues = venues
        self.strategy = SchedulingStrategy()

    def run(self) -> SchedulingStrategy:
        s = self.strategy
        s.log("Starting comprehensive pre-scheduling analysis…")
        log_info("Starting Pre-Scheduling Intelligence analysis")

        slots = generate_exam_slots(self.config.start_time,
                                    self.config.end_time,
                                    self.config.slot_size)
        spacing = float(getattr(self.config, "spacing_ratio", 1.0))
        date_range = self._build_date_range()
        n_days = len(date_range)
        daytime = [(ss, se) for ss, se in slots if ss < datetime.time(17, 0)]
        evening = [(ss, se) for ss, se in slots if ss >= datetime.time(17, 0)]
        n_day_slots = len(daytime)
        n_eve_slots = len(evening)
        slot_budget = n_days * n_day_slots

        s.log(f"Exam window: {n_days} days | {n_day_slots} daytime slots/day "
              f"| {n_eve_slots} evening slots/day")
        log_info(f"Exam window: {n_days} days, {slot_budget} total slots")

        total_seat_supply = sum(venue_exam_capacity(v, spacing) for v in self.venues)
        seats_per_slot = total_seat_supply
        total_slot_supply = seats_per_slot * slot_budget
        s.total_seat_supply = total_seat_supply
        s.log(f"Venue supply: {len(self.venues)} venues | {total_seat_supply} seats/slot")
        log_info(f"Venue supply: {len(self.venues)} venues, {total_seat_supply} seats/slot")

        cohort_demand = self._compute_cohort_demand()
        total_students = sum(cohort_demand.values())
        s.total_student_demand = total_students
        s.log(f"Total student-exam demand: {total_students} across {len(cohort_demand)} cohorts")
        log_info(f"Total student-exam demand: {total_students} across {len(cohort_demand)} cohorts")

        pressure = total_students / max(total_slot_supply, 1)
        s.seat_pressure = pressure
        s.log(f"Seat pressure: {pressure:.3f}")
        log_info(f"Seat pressure: {pressure:.3f}")

        self._analyze_cohorts(cohort_demand, n_days, n_day_slots, slot_budget, n_eve_slots)
        self._analyze_lecturers(slot_budget)
        self._analyze_venue_scarcity(spacing, date_range, daytime, evening)
        self._select_strategy(pressure, n_days, n_day_slots, n_eve_slots)

        safe_print(s.summary())
        log_info(f"Selected strategy: {s.mode}")
        for warning in s.warnings:
            log_warning(warning)
        
        return s

    def _build_date_range(self) -> List[datetime.date]:
        excluded = set(self.config.excluded_date_list())
        result = []
        for ds, _ in self.config.get_date_range():
            if ds in excluded:
                continue
            try:
                result.append(datetime.datetime.strptime(ds, "%Y-%m-%d").date())
            except Exception:
                pass
        return result

    def _compute_cohort_demand(self) -> Dict[str, int]:
        demand: Dict[str, int] = defaultdict(int)
        for c in self.courses:
            pk = _prog_year_key(c)
            if pk:
                demand[pk] += 1
        return dict(demand)

    def _analyze_cohorts(self, cohort_demand: Dict[str, int],
                         n_days: int, n_day_slots: int,
                         slot_budget: int, n_eve_slots: int):
        s = self.strategy
        full_budget = n_days * (n_day_slots + n_eve_slots)

        for pk, n_courses in sorted(cohort_demand.items(), key=lambda x: -x[1]):
            min_slots_needed = n_courses
            if min_slots_needed > full_budget:
                s.infeasible_cohorts.append(pk)
                s.warn(f"Cohort '{pk}' needs {min_slots_needed} slots "
                       f"but only {full_budget} available — INFEASIBLE")
                log_warning(f"Cohort '{pk}' infeasible: needs {min_slots_needed}, has {full_budget}")
            elif min_slots_needed > slot_budget:
                s.high_pressure_cohorts.append(pk)
                s.warn(f"Cohort '{pk}' needs {min_slots_needed} slots, "
                       f"daytime budget={slot_budget} — evening slots REQUIRED")
                s.use_evening_slots = True
                log_info(f"Cohort '{pk}' requires evening slots: needs {min_slots_needed}")

    def _analyze_lecturers(self, slot_budget: int):
        s = self.strategy
        lec_courses: Dict[int, List] = defaultdict(list)
        lec_name: Dict[int, str] = {}

        for c in self.courses:
            lec = getattr(c, "lecturer", None)
            if not lec:
                continue
            lid = lec.id
            lec_courses[lid].append(c)
            lec_name[lid] = str(getattr(lec, "name", None) or
                               getattr(lec, "username", None) or f"lec_{lid}")

        for lid, courses in lec_courses.items():
            n = len(courses)
            name = lec_name[lid]
            if n > slot_budget:
                s.overloaded_lecturers.append(f"{name}({n})")
                s.warn(f"Lecturer '{name}' supervises {n} exams but only "
                       f"{slot_budget} slots exist")
                log_warning(f"Lecturer '{name}' overloaded: {n} exams, {slot_budget} slots")

    def _analyze_venue_scarcity(self, spacing: float,
                                date_range: List[datetime.date],
                                daytime: List, evening: List):
        s = self.strategy
        n_day_slots = len(daytime)
        seats_per_slot = sum(venue_exam_capacity(v, spacing) for v in self.venues)
        seats_per_day = seats_per_slot * n_day_slots
        n_days = len(date_range)

        if n_days > 0 and seats_per_day > 0:
            total_students = s.total_student_demand
            avg_daily_demand = total_students / n_days
            for d in date_range:
                day_pressure = avg_daily_demand / seats_per_day
                if day_pressure > 0.85:
                    s.bottleneck_days.append(str(d))
                    log_info(f"Bottleneck day detected: {d}, pressure={day_pressure:.1%}")

        s.log(f"Seats/slot={seats_per_slot} | Seats/day={seats_per_day} | Days={n_days}")

    def _select_strategy(self, pressure: float,
                         n_days: int, n_day_slots: int, n_eve_slots: int):
        s = self.strategy

        if pressure <= 0.75:
            s.mode = SchedulingStrategy.NORMAL
        elif pressure <= 0.90:
            s.mode = SchedulingStrategy.COMPACT
            s.log("  COMPACT mode: spread load evenly across days")
        elif pressure <= 1.00:
            s.mode = SchedulingStrategy.DENSE
            s.use_evening_slots = True
            s.relax_consecutive = True
            s.log("  DENSE mode: evening slots activated, consecutive relaxed")
        else:
            s.mode = SchedulingStrategy.OVERFLOW
            s.use_evening_slots = True
            s.relax_consecutive = True
            s.near_fit_threshold = OVERFLOW_NEAR_FIT_THRESHOLD
            s.log(f"  OVERFLOW mode: near-fit threshold raised to {OVERFLOW_NEAR_FIT_THRESHOLD}")
            s.warn("Capacity is TIGHT — overflow near-fit relaxation enabled")

        if s.infeasible_cohorts and s.mode != SchedulingStrategy.OVERFLOW:
            s.mode = SchedulingStrategy.DENSE
            s.use_evening_slots = True
            s.relax_consecutive = True
            s.log("  Upgraded to DENSE due to infeasible cohorts")
            log_info("Upgraded to DENSE mode due to infeasible cohorts")

        log_info(f"Final strategy mode: {s.mode}")
        log_info(f"Use evening slots: {s.use_evening_slots}")
        log_info(f"Relax consecutive: {s.relax_consecutive}")
        log_info(f"Near-fit threshold: {s.near_fit_threshold}")


# ─────────────────────────────────────────────────────────
# SchedulerState
# ─────────────────────────────────────────────────────────
class SchedulerState:
    def __init__(self, config, analysis: DataAnalysisReport,
                 strategy: SchedulingStrategy = None):
        self.config = config
        self.analysis = analysis
        self.strategy = strategy or SchedulingStrategy()

        spacing_ratio = float(getattr(config, "spacing_ratio", 1.0))
        raw_venues = list(Venue.objects.filter(capacity__isnull=False, capacity__gt=0))

        self.venue_examcap: Dict[int, int] = {
            v.id: venue_exam_capacity(v, spacing_ratio) for v in raw_venues
        }
        self.venue_rawcap: Dict[int, int] = {v.id: (v.capacity or 0) for v in raw_venues}
        self.venues = sorted(raw_venues, key=lambda v: self.venue_examcap.get(v.id, 0), reverse=True)
        self.venues_by_cap_desc = self.venues
        self.venues_by_cap_asc = list(reversed(self.venues))

        self.venue_usage: Dict[Tuple, int] = defaultdict(int)
        self.lecturer_busy: Dict[int, Set] = defaultdict(set)
        self._py_busy: Dict[Tuple, Set[str]] = defaultdict(set)
        self._py_busy_allocs: Dict[Tuple, List] = {}

        self.family_slot: Dict[str, Tuple] = {}
        self.family_day: Dict[str, datetime.date] = {}
        self.family_day_assignments: Dict[str, datetime.date] = {}
        self.day_family_count: Dict[datetime.date, int] = defaultdict(int)

        self.shared_unit_lock: Dict[str, Tuple] = {}
        self.norm_code_day_lock: Dict[str, datetime.date] = {}
        self._cross_cohort_norm_codes: Set[str] = set()

        self.cohort_last_slot_idx: Dict[Tuple, int] = {}
        self.cohort_daily_count: Dict[Tuple, int] = defaultdict(int)

        self.daily_load: Dict[datetime.date, int] = defaultdict(int)
        self._fk_cache: Dict[int, str] = {}
        self._py_cache: Dict[int, str] = {}
        self._norm_code_cache: Dict[int, str] = {}

        self.placed_families: Set[str] = set()

        self.date_range = self._build_date_range()
        self.slots = generate_exam_slots(config.start_time, config.end_time, config.slot_size)
        self.morning_slots = [(s, e) for s, e in self.slots if s < datetime.time(12, 0)]
        self.afternoon_slots = [
            (s, e) for s, e in self.slots
            if datetime.time(12, 0) <= s < datetime.time(17, 0)
        ]
        self.evening_slots = [(s, e) for s, e in self.slots if is_evening_slot(s)]
        self.daytime_slots_list = self.morning_slots + self.afternoon_slots
        self.all_slots_ordered = self.daytime_slots_list + self.evening_slots
        self._slot_start_to_idx: Dict[datetime.time, int] = {
            ss: idx for idx, (ss, _) in enumerate(self.all_slots_ordered)
        }

        self._sorted_dates = sorted(self.date_range, key=lambda dt: dt[0])

        total_exam = sum(self.venue_examcap.values())
        self._cap_cache: Dict[Tuple, int] = {}
        self._venue_avail: Dict[Tuple, int] = {}
        self._day_slot_total: Dict[Tuple, int] = {}
        self._day_any_cap: Dict[datetime.date, bool] = {}

        for date_obj, _ in self.date_range:
            self._day_any_cap[date_obj] = True
            for ss, _ in self.all_slots_ordered:
                self._cap_cache[(date_obj, ss)] = total_exam
                self._day_slot_total[(date_obj, ss)] = total_exam
                for v in self.venues:
                    self._venue_avail[(v.id, date_obj, ss)] = self.venue_examcap[v.id]

        n_days = len(self.date_range)
        n_day_slots = len(self.daytime_slots_list)
        budget = n_days * n_day_slots
        safe_print(f"[SchedulerState] Slot budget={budget} | "
                   f"Venues={len(self.venues)} | SpacingRatio={spacing_ratio:.0%}")
        log_info(f"SchedulerState initialized: {len(self.venues)} venues, {len(self.date_range)} days, {budget} slots")

    def _build_date_range(self):
        excluded = set(self.config.excluded_date_list())
        date_range = []
        for ds, weekday_name in self.config.get_date_range():
            if ds in excluded:
                continue
            try:
                date_obj = datetime.datetime.strptime(ds, "%Y-%m-%d").date()
                date_range.append((date_obj, weekday_name))
            except Exception:
                continue
        return date_range

    def dates_in_order(self):
        return self._sorted_dates

    def _py_key(self, course) -> str:
        cid = course.id
        if cid not in self._py_cache:
            self._py_cache[cid] = _prog_year_key(course)
        return self._py_cache[cid]

    def _norm_code(self, course) -> str:
        cid = course.id
        if cid not in self._norm_code_cache:
            self._norm_code_cache[cid] = normalize_course_code(
                getattr(course, "course_code", "") or ""
            )
        return self._norm_code_cache[cid]

    def _cached_lecturer_id(self, course) -> Optional[int]:
        lec = getattr(course, "lecturer", None)
        return lec.id if lec else None

    def family_key(self, course) -> str:
        cid = course.id
        if cid not in self._fk_cache:
            nc = self._norm_code(course)
            py = self._py_key(course)
            self._fk_cache[cid] = f"{nc}|{py}" if py else nc
        return self._fk_cache[cid]

    def students_available(self, course, date, slot_start) -> bool:
        pk = self._py_key(course)
        if not pk:
            return True
        occupied = self._py_busy.get((date, slot_start), set())
        if pk not in occupied:
            return True
        existing = self._py_busy_allocs.get((date, slot_start, pk), [])
        for existing_course in existing:
            if not exam_is_collision_exempt(course, existing_course):
                return False
        return True

    def mark_students_busy(self, course, date, slot_start):
        pk = self._py_key(course)
        if pk:
            self._py_busy[(date, slot_start)].add(pk)
            self._py_busy_allocs.setdefault((date, slot_start, pk), []).append(course)

    def lecturer_available(self, lid, date, slot_start) -> bool:
        return not lid or (date, slot_start) not in self.lecturer_busy[lid]

    def mark_lecturer_busy(self, lid, date, slot_start):
        if lid:
            self.lecturer_busy[lid].add((date, slot_start))

    def bind_family(self, fk: str, date, slot_start):
        if fk and fk not in self.family_slot:
            self.family_slot[fk] = (date, slot_start)
            self.family_day[fk] = date

    def check_family_conflict(self, course, date, slot_start) -> bool:
        fk = self.family_key(course)
        locked = self.family_slot.get(fk)
        if locked is None:
            return False
        return locked != (date, slot_start)

    def release_family_binding(self, course):
        fk = self.family_key(course)
        if fk not in self.family_slot:
            return
        locked_date, locked_slot = self.family_slot[fk]
        try:
            already_placed = ExamTempTimetable.objects.filter(
                date=locked_date, start_time=locked_slot,
            ).values_list("course_allocation_id", flat=True)
            for aid in already_placed:
                if aid != course.id:
                    cached_fk = self._fk_cache.get(aid)
                    if cached_fk == fk:
                        return
        except Exception:
            pass
        self.family_slot.pop(fk, None)
        self.family_day.pop(fk, None)
        nc = self._norm_code(course)
        if nc:
            self.norm_code_day_lock.pop(nc, None)
            self.shared_unit_lock.pop(nc, None)

    def bind_shared_unit(self, course, date, slot_start):
        nc = self._norm_code(course)
        if nc and nc in self._cross_cohort_norm_codes and nc not in self.shared_unit_lock:
            self.shared_unit_lock[nc] = (date, slot_start)

    def check_shared_unit_conflict(self, course, date, slot_start) -> bool:
        nc = self._norm_code(course)
        if not nc or nc not in self._cross_cohort_norm_codes:
            return False
        locked = self.shared_unit_lock.get(nc)
        if locked is None:
            return False
        return locked != (date, slot_start)

    def bind_norm_code_day(self, course, date: datetime.date):
        nc = self._norm_code(course)
        if nc and nc in self._cross_cohort_norm_codes:
            if nc not in self.norm_code_day_lock:
                self.norm_code_day_lock[nc] = date

    def check_norm_code_day_conflict(self, course, date: datetime.date) -> bool:
        nc = self._norm_code(course)
        if not nc or nc not in self._cross_cohort_norm_codes:
            return False
        locked_day = self.norm_code_day_lock.get(nc)
        if locked_day is None:
            return False
        return locked_day != date

    def venue_remaining(self, vid, date, slot_start) -> int:
        return self._venue_avail.get((vid, date, slot_start), 0)

    def consume_venue(self, vid, date, slot_start, students: int):
        cap = self.venue_examcap.get(vid, 0)
        already_used = self.venue_usage.get((vid, date, slot_start), 0)
        if already_used >= cap:
            return
        max_takeable = max(0, cap - already_used)
        if students > max_takeable:
            students = max_takeable
        if students <= 0:
            return
        avail_key = (vid, date, slot_start)
        self._venue_avail[avail_key] = max(0, self._venue_avail.get(avail_key, 0) - students)
        self.venue_usage[(vid, date, slot_start)] += students

    def slot_total_remaining(self, date, slot_start) -> int:
        return self._day_slot_total.get((date, slot_start), 0)

    def _recompute_cap_cache(self):
        for date_obj, _ in self.date_range:
            day_has_any = False
            for ss, _ in self.all_slots_ordered:
                slot_total = 0
                for v in self.venues:
                    used = self.venue_usage[(v.id, date_obj, ss)]
                    avail = max(0, self.venue_examcap[v.id] - used)
                    self._venue_avail[(v.id, date_obj, ss)] = avail
                    slot_total += avail
                self._cap_cache[(date_obj, ss)] = slot_total
                self._day_slot_total[(date_obj, ss)] = slot_total
                if slot_total > 0:
                    day_has_any = True
            self._day_any_cap[date_obj] = day_has_any

    def slot_has_any_venue_space(self, date, slot_start, needed: int = 1) -> bool:
        if self._day_slot_total.get((date, slot_start), 0) < needed:
            return False
        free_total = 0
        for v in self.venues_by_cap_desc:
            cap = self.venue_examcap.get(v.id, 0)
            rem = self._venue_avail.get((v.id, date, slot_start), 0)
            if rem == cap and rem > 0:
                free_total += cap
                if free_total >= needed:
                    return True
        return False

    def day_has_any_capacity(self, date) -> bool:
        return self._day_any_cap.get(date, True)

    def cohort_in_cooling(self, course, date, slot_start, gap: int = CONSECUTIVE_GAP_SLOTS) -> bool:
        pk = self._py_key(course)
        if not pk:
            return False
        key = (pk, date)
        last_idx = self.cohort_last_slot_idx.get(key)
        if last_idx is None:
            return False
        current_idx = self._slot_start_to_idx.get(slot_start)
        if current_idx is None:
            return False
        return (current_idx - last_idx) <= gap

    def mark_cohort_scheduled(self, course, date, slot_start):
        pk = self._py_key(course)
        if not pk:
            return
        idx = self._slot_start_to_idx.get(slot_start)
        if idx is not None:
            key = (pk, date)
            existing = self.cohort_last_slot_idx.get(key)
            if existing is None or idx > existing:
                self.cohort_last_slot_idx[key] = idx
        self.cohort_daily_count[(pk, date)] += 1

    def priority_score(self, course) -> float:
        n_students = course_student_count(course)
        conflict_deg = self.analysis.conflict_degree.get(course.id, 0)
        nc = self._norm_code(course)
        is_shared = nc in self.analysis.shared_unit_groups
        cohort_load = self.analysis.cohort_course_counts.get(self._py_key(course), 0)
        if is_shared:
            family_total = self.analysis.family_total_students.get(nc, n_students)
        else:
            family_total = n_students
        return family_total * 1.0 + conflict_deg * 50.0 + (500.0 if is_shared else 0.0) + cohort_load * 10.0


# ─────────────────────────────────────────────────────────
# VENUE SELECTION
# ─────────────────────────────────────────────────────────
def _free_venues_for_slot(date, slot_start, state: SchedulerState) -> List[Tuple]:
    result = []
    for v in state.venues_by_cap_desc:
        cap = state.venue_examcap.get(v.id, 0)
        if cap <= 0:
            continue
        rem = state._venue_avail.get((v.id, date, slot_start), 0)
        if rem == cap:
            result.append((v, cap))
    result.sort(key=lambda x: x[1])
    return result


def find_best_venue(needed: int, date, slot_start, state: SchedulerState,
                    near_fit_override: int = 0):
    threshold = near_fit_override or state.strategy.near_fit_threshold
    free = _free_venues_for_slot(date, slot_start, state)

    for v, cap in free:
        if cap >= needed:
            return v

    for v, cap in reversed(free):
        overflow = needed - cap
        if 0 < overflow <= threshold:
            return v

    return None


def find_venues_to_cover(needed: int, date, slot_start, state: SchedulerState) -> List:
    free = _free_venues_for_slot(date, slot_start, state)
    free_desc = list(reversed(free))
    chosen, total = [], 0
    for v, cap in free_desc:
        chosen.append(v)
        total += cap
        if total >= needed:
            return chosen
    return []


def _ultimate_find_room(needed: int, date, slot_start, state: SchedulerState,
                        exclude_ids: Set[int] = None):
    exclude_ids = exclude_ids or set()

    free_asc = sorted(
        [(v, state.venue_examcap.get(v.id, 0))
         for v in state.venues_by_cap_desc
         if v.id not in exclude_ids
         and state.venue_examcap.get(v.id, 0) > 0
         and state._venue_avail.get((v.id, date, slot_start), 0)
            == state.venue_examcap.get(v.id, 0)],
        key=lambda x: x[1]
    )

    for v, cap in free_asc:
        if cap >= needed:
            return v
    if free_asc:
        return free_asc[-1][0]

    any_avail = sorted(
        [(v, state._venue_avail.get((v.id, date, slot_start), 0))
         for v in state.venues_by_cap_desc
         if v.id not in exclude_ids
         and state._venue_avail.get((v.id, date, slot_start), 0) > 0],
        key=lambda x: x[1]
    )
    for v, rem in any_avail:
        if rem >= needed:
            return v
    if any_avail:
        return any_avail[-1][0]

    return None


# ─────────────────────────────────────────────────────────
# PLACEMENT PRIMITIVES
# ─────────────────────────────────────────────────────────
_already_scheduled_cache: Set[int] = set()


def _check_hard_constraints(course, date, slot_start, state: SchedulerState) -> Optional[str]:
    if not state.students_available(course, date, slot_start):
        return "student-conflict"
    if state.check_family_conflict(course, date, slot_start):
        return "family-conflict"
    if state.check_shared_unit_conflict(course, date, slot_start):
        return "shared-unit-conflict"
    if state.check_norm_code_day_conflict(course, date):
        return "norm-code-day-conflict"
    lid = state._cached_lecturer_id(course)
    if lid and not state.lecturer_available(lid, date, slot_start):
        return "lecturer-conflict"
    return None


def _post_place(course, venue_id: int, students: int,
                date, slot_start, slot_end,
                state: SchedulerState, scheduled_ids: Set[int]):
    if students > 0:
        state.consume_venue(venue_id, date, slot_start, students)
    state.mark_students_busy(course, date, slot_start)
    lid = state._cached_lecturer_id(course)
    state.mark_lecturer_busy(lid, date, slot_start)
    state.bind_family(state.family_key(course), date, slot_start)
    state.bind_shared_unit(course, date, slot_start)
    state.bind_norm_code_day(course, date)
    state.mark_cohort_scheduled(course, date, slot_start)
    state.daily_load[date] += 1
    scheduled_ids.add(course.id)
    _already_scheduled_cache.add(course.id)


def _course_already_in_db(course) -> bool:
    if course.id in _already_scheduled_cache:
        return True
    exists = ExamTempTimetable.objects.filter(course_allocation=course).exists()
    if exists:
        _already_scheduled_cache.add(course.id)
    return exists


def place_single(course, venue, date, slot_start, slot_end,
                 state: SchedulerState, scheduled_ids: Set[int],
                 relax_consecutive=False,
                 ignore_capacity=False) -> bool:
    if course.id in scheduled_ids:
        return True
    needed = course_student_count(course)

    cap = state.venue_examcap.get(venue.id, 0)
    rem = state.venue_remaining(venue.id, date, slot_start)

    if not ignore_capacity:
        if rem != cap:
            log_debug(f"Single placement failed {course.course_code}: venue {venue.code} not free (rem={rem}, cap={cap})")
            return False
        threshold = state.strategy.near_fit_threshold
        overflow = needed - cap
        if overflow > threshold:
            log_debug(f"Single placement failed {course.course_code}: overflow {overflow} > threshold {threshold}")
            return False

    effective_seats = min(needed, cap) if cap > 0 else needed

    reason = _check_hard_constraints(course, date, slot_start, state)
    if reason:
        log_debug(f"Single placement failed {course.course_code}: {reason}")
        return False
    if not relax_consecutive and state.cohort_in_cooling(course, date, slot_start):
        log_debug(f"Single placement failed {course.course_code}: cohort in cooling period")
        return False
    if _course_already_in_db(course):
        real = ExamTempTimetable.objects.filter(
            course_allocation=course
        ).values("date", "start_time").first()
        if real:
            state.bind_family(state.family_key(course), real["date"], real["start_time"])
        scheduled_ids.add(course.id)
        log_info(f"Course {course.course_code} already in DB, skipped duplicate")
        return True

    try:
        with transaction.atomic():
            ExamTempTimetable.objects.create(
                course_allocation=course, venue=venue,
                date=date, day=date.strftime("%A"),
                start_time=slot_start, end_time=slot_end,
            )
    except IntegrityError:
        if ExamTempTimetable.objects.filter(
            course_allocation=course, date=date, start_time=slot_start
        ).exists():
            scheduled_ids.add(course.id)
            return True
        log_error(f"IntegrityError placing {course.course_code} at {date} {slot_start}")
        return False

    _post_place(course, venue.id, effective_seats, date, slot_start, slot_end, state, scheduled_ids)
    log_placement(
        course.id, course.course_code,
        venue.id, venue.code,
        str(date), f"{slot_start.strftime('%H:%M')}-{slot_end.strftime('%H:%M')}",
        effective_seats
    )
    return True


def place_multi_venue(course, venues, date, slot_start, slot_end,
                      state: SchedulerState, scheduled_ids: Set[int],
                      relax_consecutive=False) -> bool:
    if course.id in scheduled_ids:
        return True
    needed = course_student_count(course)

    reason = _check_hard_constraints(course, date, slot_start, state)
    if reason:
        log_debug(f"Multi-venue placement failed {course.course_code}: {reason}")
        return False
    if not relax_consecutive and state.cohort_in_cooling(course, date, slot_start):
        return False
    if _course_already_in_db(course):
        scheduled_ids.add(course.id)
        return True

    free_sorted = sorted(
        [v for v in venues
         if state._venue_avail.get((v.id, date, slot_start), 0)
            == state.venue_examcap.get(v.id, 0)
         and state.venue_examcap.get(v.id, 0) > 0],
        key=lambda v: state.venue_examcap.get(v.id, 0),
        reverse=True
    )

    assignments, remaining = [], needed
    for v in free_sorted:
        if remaining <= 0:
            break
        cap = state.venue_examcap.get(v.id, 0)
        take = min(cap, remaining)
        assignments.append((v, take))
        remaining -= take

    if remaining > 0:
        log_debug(f"Multi-venue failed {course.course_code}: insufficient capacity, remaining {remaining}")
        return False

    try:
        with transaction.atomic():
            for v, _ in assignments:
                ExamTempTimetable.objects.create(
                    course_allocation=course, venue=v,
                    date=date, day=date.strftime("%A"),
                    start_time=slot_start, end_time=slot_end,
                )
    except IntegrityError:
        if ExamTempTimetable.objects.filter(
            course_allocation=course, date=date, start_time=slot_start
        ).exists():
            scheduled_ids.add(course.id)
            return True
        log_error(f"IntegrityError in multi-venue placement for {course.course_code}")
        return False

    for v, students in assignments:
        state.consume_venue(v.id, date, slot_start, students)
    scheduled_ids.add(course.id)
    _post_place(course, assignments[0][0].id, 0, date, slot_start, slot_end, state, scheduled_ids)
    
    venues_str = ", ".join(f"{v.code}({s}stu)" for v, s in assignments)
    log_info(f"Multi-venue placement: {course.course_code} → {venues_str} at {date} {slot_start}")
    
    return True


def try_place_course(course, date, slot_start, slot_end,
                     state: SchedulerState, scheduled_ids: Set[int],
                     relax_consecutive=False) -> bool:
    nc = normalize_course_code(course.course_code or "")
    if nc in state.analysis.shared_unit_groups:
        family_member_ids = state.analysis.shared_unit_groups.get(nc, [])
        placed_family_members = [cid for cid in family_member_ids if cid in scheduled_ids]
        if placed_family_members and len(placed_family_members) < len(family_member_ids):
            return False

    needed = course_student_count(course)
    v = find_best_venue(needed, date, slot_start, state)
    if v and place_single(course, v, date, slot_start, slot_end,
                          state, scheduled_ids, relax_consecutive=relax_consecutive):
        return True
    return False


# ─────────────────────────────────────────────────────────
# FAMILY PLACEMENT
# ─────────────────────────────────────────────────────────
def _family_constraints_ok(group_courses, date, slot_start, state):
    for c in group_courses:
        if not state.students_available(c, date, slot_start):
            return False
        if state.check_norm_code_day_conflict(c, date):
            return False
        if state.check_shared_unit_conflict(c, date, slot_start):
            return False
        if state.check_family_conflict(c, date, slot_start):
            return False
        lid = state._cached_lecturer_id(c)
        if lid and not state.lecturer_available(lid, date, slot_start):
            return False
    return True


def _build_venue_pool(date, slot_start, state, free_only: bool = True):
    pool = []
    for v in state.venues_by_cap_desc:
        cap = state.venue_examcap.get(v.id, 0)
        if cap <= 0:
            continue
        remaining = min(state.venue_remaining(v.id, date, slot_start), cap)
        if remaining <= 0:
            continue
        if free_only and remaining != cap:
            continue
        pool.append([v, remaining, cap])
    return pool


def _commit_single_venue(group_courses, nc, venue, total_needed,
                         date, ss, se, state, scheduled_ids):
    cap = state.venue_examcap.get(venue.id, 0)
    remaining = state.venue_remaining(venue.id, date, ss)

    if remaining != cap:
        return False
    if (total_needed - cap) > state.strategy.near_fit_threshold:
        log_debug(f"Family {nc} exceeds venue capacity by {total_needed - cap}")
        return False

    effective_seats = min(total_needed, cap)

    already = [c for c in group_courses if c.id in scheduled_ids]
    group_courses = [c for c in group_courses if c.id not in scheduled_ids]
    if not group_courses:
        state.placed_families.add(nc)
        return True

    entries = [
        ExamTempTimetable(
            course_allocation=c, venue=venue,
            date=date, day=date.strftime("%A"),
            start_time=ss, end_time=se,
        )
        for c in group_courses
    ]

    try:
        with transaction.atomic():
            ExamTempTimetable.objects.bulk_create(entries, ignore_conflicts=True)
            merged = MergedCourseGroup.objects.create(
                base_course=group_courses[0],
                merged_code=nc,
                total_students=total_needed,
                date=date, start_time=ss, end_time=se,
                venue=venue,
            )
            merged.merged_courses.set(group_courses)
            state.placed_families.add(nc)
    except Exception as e:
        safe_print(f"  [FAIL] DB commit error: {e}")
        log_error(f"Family {nc} DB commit error: {e}")
        return False

    state.consume_venue(venue.id, date, ss, effective_seats)
    for c in group_courses:
        state.mark_students_busy(c, date, ss)
        lid = state._cached_lecturer_id(c)
        state.mark_lecturer_busy(lid, date, ss)
        state.bind_family(state.family_key(c), date, ss)
        state.bind_shared_unit(c, date, ss)
        state.bind_norm_code_day(c, date)
        state.mark_cohort_scheduled(c, date, ss)
        scheduled_ids.add(c.id)
        _already_scheduled_cache.add(c.id)

    state.daily_load[date] += 1
    state.shared_unit_lock[nc] = (date, ss)
    state.norm_code_day_lock[nc] = date
    
    log_info(f"Family placed: {nc} with {len(group_courses)} courses at {date} {ss} in {venue.code}")
    for c in group_courses:
        log_placement(
            c.id, c.course_code,
            venue.id, venue.code,
            str(date), f"{ss.strftime('%H:%M')}-{se.strftime('%H:%M')}",
            course_student_count(c)
        )
    
    return True


def place_merged_family(group_courses, nc, date, ss, se,
                        state, scheduled_ids):
    group_courses = [c for c in group_courses if c.id not in scheduled_ids]
    if not group_courses:
        state.placed_families.add(nc)
        return True

    total_needed = family_total_students(group_courses)

    if not _family_constraints_ok(group_courses, date, ss, state):
        log_debug(f"Family {nc} constraints failed at {date} {ss}")
        return False

    pool = _build_venue_pool(date, ss, state, free_only=True)

    single_venue = None
    for row in pool:
        v, remaining, cap = row
        if remaining >= total_needed:
            single_venue = v
            break
        overflow = total_needed - cap
        if 0 < overflow <= state.strategy.near_fit_threshold:
            single_venue = v
            break

    if single_venue:
        return _commit_single_venue(
            group_courses, nc, single_venue, total_needed,
            date, ss, se, state, scheduled_ids
        )

    log_debug(f"Family {nc} no suitable venue found at {date} {ss}")
    return False


def place_merged_group(group_courses, group_key, venue, date, slot_start, slot_end,
                       state, scheduled_ids):
    group_courses = [c for c in group_courses if c.id not in scheduled_ids]
    if not group_courses:
        state.placed_families.add(group_key)
        return True

    total = family_total_students(group_courses)
    cap = state.venue_examcap.get(venue.id, 0)
    remaining = state.venue_remaining(venue.id, date, slot_start)

    if cap < total or remaining < total:
        return place_merged_family(
            group_courses, group_key, date, slot_start, slot_end,
            state, scheduled_ids
        )

    return _commit_single_venue(
        group_courses, group_key, venue, total,
        date, slot_start, slot_end, state, scheduled_ids
    )


# ─────────────────────────────────────────────────────────
# PHASE 1: FAMILIES FIRST
# ─────────────────────────────────────────────────────────
def schedule_families_first(all_courses, state, scheduled_ids):
    if not state.analysis.shared_unit_groups:
        safe_print("[Phase1] No families — skipping")
        log_info("Phase 1: No families found, skipping")
        return 0

    course_by_id = {c.id: c for c in all_courses}
    dates = state.dates_in_order()
    total_days = len(dates)

    sorted_families = sorted(
        state.analysis.shared_unit_groups.items(),
        key=lambda kv: family_total_students(
            [course_by_id[cid] for cid in kv[1] if cid in course_by_id]
        ),
        reverse=True,
    )

    safe_print(f"\n[Phase1] Scheduling {len(sorted_families)} families FIRST")
    log_info(f"Phase 1: Scheduling {len(sorted_families)} families first")
    for nc, cids in sorted_families:
        log_debug(f"Family {nc}: {len(cids)} members")

    placed_total = 0
    day_pointer = 0

    for nc, cids in sorted_families:
        if nc in state.placed_families:
            continue

        group = [course_by_id[cid] for cid in cids
                 if cid in course_by_id and cid not in scheduled_ids]
        if not group:
            state.placed_families.add(nc)
            continue

        true_total = family_total_students(group)
        if DEBUG_VERBOSE:
            safe_print(f"[Phase1] '{nc}' | {len(group)} variants | TRUE total={true_total}")
            log_debug(f"Family {nc}: {len(group)} variants, {true_total} students")

        family_placed = False
        for day_offset in range(total_days):
            if family_placed:
                break
            day_idx = (day_pointer + day_offset) % total_days
            date_obj, wday = dates[day_idx]

            if not state.day_has_any_capacity(date_obj):
                continue

            for ss, se in state.daytime_slots_list + state.evening_slots:
                if family_placed:
                    break
                if not _family_constraints_ok(group, date_obj, ss, state):
                    continue
                pool = _build_venue_pool(date_obj, ss, state)
                total_remaining = sum(row[1] for row in pool)
                if total_remaining < true_total:
                    continue
                if place_merged_family(group, nc, date_obj, ss, se, state, scheduled_ids):
                    placed_total += len(group)
                    state.family_day_assignments[nc] = date_obj
                    state.day_family_count[date_obj] += 1
                    day_pointer = (day_idx + 1) % total_days
                    family_placed = True
                    log_info(f"Family {nc} placed at {date_obj} {ss} (day {day_idx+1})")

        if not family_placed:
            if DEBUG_VERBOSE:
                safe_print(f"  [WARN] Could not place '{nc}' (TRUE total={true_total})")
            log_warning(f"Family {nc} could not be placed after trying all slots")

    safe_print(f"[Phase1] DONE: {len(state.placed_families)}/{len(state.analysis.shared_unit_groups)} families")
    log_info(f"Phase 1 completed: {len(state.placed_families)} families placed")
    
    return placed_total


# ─────────────────────────────────────────────────────────
# PHASE 2: SATURATION LOOP
# ─────────────────────────────────────────────────────────
def _fill_slot_with_program(date, slot_start, slot_end,
                            program_courses: List,
                            state: SchedulerState,
                            scheduled_ids: Set[int],
                            relax_consecutive=False) -> int:
    placed = 0
    sorted_courses = sorted(
        [c for c in program_courses if c.id not in scheduled_ids],
        key=lambda c: course_student_count(c), reverse=True
    )

    placed_in_slot = defaultdict(list)

    for course in sorted_courses:
        if course.id in scheduled_ids:
            continue
        nc = normalize_course_code(course.course_code or "")
        if nc in state.analysis.shared_unit_groups:
            continue
        if not state.slot_has_any_venue_space(date, slot_start, 1):
            break

        py_key = state._py_key(course)
        if py_key and py_key in placed_in_slot:
            conflict = False
            for placed_c in placed_in_slot[py_key]:
                if not exam_is_collision_exempt(course, placed_c):
                    conflict = True
                    break
            if conflict:
                continue

        reason = _check_hard_constraints(course, date, slot_start, state)
        if reason:
            continue
        if not relax_consecutive and state.cohort_in_cooling(course, date, slot_start):
            continue

        if try_place_course(course, date, slot_start, slot_end,
                            state, scheduled_ids,
                            relax_consecutive=relax_consecutive):
            placed += 1
            if py_key:
                placed_in_slot[py_key].append(course)

    if placed > 0:
        log_debug(f"Filled slot {date} {slot_start} with {placed} courses")
    
    return placed


def run_saturation_day(date: datetime.date,
                       pending_by_program: Dict[str, List],
                       state: SchedulerState,
                       scheduled_ids: Set[int],
                       all_courses: List,
                       force_evenings: bool = False) -> int:
    placed_today = 0
    day_slots = state.daytime_slots_list
    eve_slots = state.evening_slots

    program_order = sorted(
        pending_by_program.items(),
        key=lambda kv: len([c for c in kv[1] if c.id not in scheduled_ids]),
        reverse=True,
    )

    max_rounds = 6 if force_evenings else 4

    for round_idx in range(max_rounds):
        progress_this_round = 0

        for prog_id, prog_courses in program_order:
            active = [c for c in prog_courses if c.id not in scheduled_ids]
            if not active:
                continue

            for ss, se in day_slots:
                active = [c for c in prog_courses if c.id not in scheduled_ids]
                if not active:
                    break
                if not state.slot_has_any_venue_space(date, ss, 1):
                    continue
                n = _fill_slot_with_program(
                    date, ss, se, active, state, scheduled_ids,
                    relax_consecutive=(round_idx > 0))
                if n > 0:
                    placed_today += n
                    progress_this_round += n

            is_pg_prog = all(
                is_postgraduate_course(getattr(c, "course_code", "") or "")
                for c in prog_courses[:3]
            )
            active = [c for c in prog_courses if c.id not in scheduled_ids]
            if (is_pg_prog or force_evenings) and active:
                for ss, se in eve_slots:
                    active = [c for c in prog_courses if c.id not in scheduled_ids]
                    if not active:
                        break
                    if not state.slot_has_any_venue_space(date, ss, 1):
                        continue
                    n = _fill_slot_with_program(
                        date, ss, se, active, state, scheduled_ids,
                        relax_consecutive=True)
                    if n > 0:
                        placed_today += n
                        progress_this_round += n

        all_pending = [c for c in all_courses if c.id not in scheduled_ids]
        if not all_pending:
            break

        all_pending_sorted = sorted(
            all_pending,
            key=lambda c: -course_student_count(c)
        )

        use_eve = eve_slots if force_evenings else []
        for ss, se in day_slots + use_eve:
            if not state.slot_has_any_venue_space(date, ss, 1):
                continue
            placed_in_slot = defaultdict(list)
            for course in all_pending_sorted:
                if course.id in scheduled_ids:
                    continue
                nc = normalize_course_code(course.course_code or "")
                if nc in state.analysis.shared_unit_groups:
                    continue
                if not state.slot_has_any_venue_space(date, ss, 1):
                    break

                py_key = state._py_key(course)
                if py_key and py_key in placed_in_slot:
                    conflict = False
                    for placed_c in placed_in_slot[py_key]:
                        if not exam_is_collision_exempt(course, placed_c):
                            conflict = True
                            break
                    if conflict:
                        continue

                reason = _check_hard_constraints(course, date, ss, state)
                if reason:
                    continue
                if try_place_course(course, date, ss, se, state, scheduled_ids,
                                    relax_consecutive=(round_idx > 0)):
                    placed_today += 1
                    progress_this_round += 1
                    if py_key:
                        placed_in_slot[py_key].append(course)

        if progress_this_round == 0:
            break

    if placed_today > 0:
        log_info(f"Saturation day {date}: placed {placed_today} courses")
    
    return placed_today


def schedule_saturation_loop(all_courses, state, scheduled_ids):
    dates = state.dates_in_order()
    total_days = len(dates)
    strategy = state.strategy

    pending_by_program: Dict[str, List] = defaultdict(list)
    for c in all_courses:
        prog = getattr(c, "program", None)
        prog_id = str(prog.id) if prog else "no_program"
        pending_by_program[prog_id].append(c)

    safe_print(f"\n[Phase2] Saturation: {len(all_courses)} total | {len(pending_by_program)} programs")
    log_info(f"Phase 2: Saturation loop with {len(all_courses)} courses, {len(pending_by_program)} programs")

    max_passes = 6 if strategy.mode in (SchedulingStrategy.DENSE, SchedulingStrategy.OVERFLOW) else 4

    for pass_idx in range(max_passes):
        placed_this_pass = 0
        for day_idx, (date_obj, weekday_name) in enumerate(dates):
            if not state.day_has_any_capacity(date_obj):
                continue
            if len(scheduled_ids) == len(all_courses):
                break

            day_pending_by_program = {
                prog_id: [c for c in prog_courses if c.id not in scheduled_ids]
                for prog_id, prog_courses in pending_by_program.items()
            }
            n = run_saturation_day(date_obj, day_pending_by_program, state,
                                   scheduled_ids, all_courses,
                                   force_evenings=strategy.use_evening_slots)
            placed_this_pass += n
            if n > 0:
                safe_print(f"  Pass {pass_idx+1} Day {day_idx+1}/{total_days} {date_obj}: +{n}")
                log_info(f"Phase 2 pass {pass_idx+1} day {day_idx+1}: +{n}")

        safe_print(f"[Phase2] Pass {pass_idx+1} complete: +{placed_this_pass}")
        log_info(f"Phase 2 pass {pass_idx+1} complete: +{placed_this_pass}")
        if placed_this_pass == 0:
            break

    return {}


# ─────────────────────────────────────────────────────────
# PHASE 3: CROSS-DAY FILL
# ─────────────────────────────────────────────────────────
def cross_day_fill_pass(all_courses, state, scheduled_ids):
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0

    safe_print(f"\n[Phase3] Cross-day fill: {len(unscheduled)} unscheduled")
    log_info(f"Phase 3: Cross-day fill with {len(unscheduled)} unscheduled")

    placed = 0
    dates = state.dates_in_order()
    course_by_id = {c.id: c for c in all_courses}

    for nc, cids in state.analysis.shared_unit_groups.items():
        if nc in state.placed_families:
            continue
        stuck = [course_by_id[cid] for cid in cids
                 if cid in course_by_id and cid not in scheduled_ids]
        if not stuck:
            state.placed_families.add(nc)
            continue

        family_placed = False
        for d, _ in dates:
            if family_placed:
                break
            for ss, se in state.daytime_slots_list + state.evening_slots:
                if family_placed:
                    break
                if place_merged_family(stuck, nc, d, ss, se, state, scheduled_ids):
                    placed += len(stuck)
                    family_placed = True
                    log_info(f"Phase 3 family {nc} placed at {d} {ss}")

    unscheduled_individual = sorted(
        [c for c in all_courses if c.id not in scheduled_ids and
         normalize_course_code(c.course_code or "") not in state.analysis.shared_unit_groups],
        key=lambda c: -state.priority_score(c)
    )

    for date_obj, _ in dates:
        for ss, se in state.daytime_slots_list + state.evening_slots:
            if not state.slot_has_any_venue_space(date_obj, ss, 1):
                continue
            placed_in_slot = defaultdict(list)
            for course in unscheduled_individual:
                if course.id in scheduled_ids:
                    continue
                pk = state._py_key(course)
                if pk and pk in placed_in_slot:
                    conflict = False
                    for placed_c in placed_in_slot[pk]:
                        if not exam_is_collision_exempt(course, placed_c):
                            conflict = True
                            break
                    if conflict:
                        continue
                needed = course_student_count(course)
                if not state.slot_has_any_venue_space(date_obj, ss, needed):
                    continue
                if _check_hard_constraints(course, date_obj, ss, state):
                    continue
                if try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                    relax_consecutive=True):
                    placed += 1
                    if pk:
                        placed_in_slot[pk].append(course)

    safe_print(f"[Phase3] Placed {placed}")
    log_info(f"Phase 3 completed: placed {placed}")
    
    return placed


# ─────────────────────────────────────────────────────────
# PHASE 3B: FAMILY SPLIT RESCUE
# ─────────────────────────────────────────────────────────
def _venue_building(venue) -> str:
    code = (getattr(venue, "code", None) or "").strip()
    return re.sub(r'[\d\s]+$', '', code).upper()


def _best_fit_free_room(needed: int, candidates: List, state, date, slot_start):
    threshold = state.strategy.near_fit_threshold
    exact = [(v, cap) for v, cap in candidates if cap >= needed]
    if exact:
        return min(exact, key=lambda x: x[1])[0]
    near = [(v, cap) for v, cap in candidates if 0 < needed - cap <= threshold]
    if near:
        return max(near, key=lambda x: x[1])[0]
    return None


def family_split_rescue_pass(all_courses, state, scheduled_ids):
    course_by_id = {c.id: c for c in all_courses}
    unplaced_families = {}

    for nc, cids in state.analysis.shared_unit_groups.items():
        stuck = [course_by_id[cid] for cid in cids
                 if cid in course_by_id and cid not in scheduled_ids]
        if stuck:
            unplaced_families[nc] = stuck

    if not unplaced_families:
        return 0

    safe_print(f"\n[Phase3b] Family split rescue: {len(unplaced_families)} families")
    log_info(f"Phase 3b: Family split rescue for {len(unplaced_families)} families")

    placed_total = 0
    dates = state.dates_in_order()

    for nc, variants in unplaced_families.items():
        variants = [c for c in variants if c.id not in scheduled_ids]
        if not variants:
            continue

        rescued = False
        for date_obj, _ in dates:
            if rescued:
                break
            for ss, se in state.daytime_slots_list + state.evening_slots:
                if rescued:
                    break

                if not _family_constraints_ok(variants, date_obj, ss, state):
                    continue

                free_rooms = [
                    (v, state.venue_examcap.get(v.id, 0))
                    for v in state.venues_by_cap_desc
                    if state.venue_examcap.get(v.id, 0) > 0
                    and state._venue_avail.get((v.id, date_obj, ss), 0)
                       == state.venue_examcap.get(v.id, 0)
                ]
                if len(free_rooms) < len(variants):
                    continue

                largest_variant = max(variants, key=lambda c: course_student_count(c))
                best_room = _best_fit_free_room(
                    course_student_count(largest_variant), free_rooms, state, date_obj, ss
                )
                preferred_building = _venue_building(best_room) if best_room else ""

                def room_sort_key(vc):
                    v, cap = vc
                    building_match = 0 if _venue_building(v) == preferred_building else 1
                    return (building_match, cap)

                free_rooms_sorted = sorted(free_rooms, key=room_sort_key)
                variants_sorted = sorted(variants, key=lambda c: -course_student_count(c))
                assignment = {}
                remaining_rooms = list(free_rooms_sorted)

                all_assigned = True
                for course in variants_sorted:
                    needed = course_student_count(course)
                    chosen = _best_fit_free_room(needed, remaining_rooms, state, date_obj, ss)
                    if chosen is None:
                        all_assigned = False
                        break
                    assignment[course.id] = chosen
                    remaining_rooms = [(v, cap) for v, cap in remaining_rooms if v.id != chosen.id]

                if not all_assigned:
                    continue

                entries = [
                    ExamTempTimetable(
                        course_allocation=course, venue=assignment[course.id],
                        date=date_obj, day=date_obj.strftime("%A"),
                        start_time=ss, end_time=se,
                    )
                    for course in variants
                ]

                try:
                    with transaction.atomic():
                        ExamTempTimetable.objects.bulk_create(entries, ignore_conflicts=True)
                except Exception as exc:
                    safe_print(f"  [Phase3b] DB error '{nc}': {exc}")
                    log_error(f"Phase 3b DB error for {nc}: {exc}")
                    continue

                for course in variants:
                    venue = assignment[course.id]
                    needed = course_student_count(course)
                    effective = min(needed, state.venue_examcap.get(venue.id, 0))
                    state.consume_venue(venue.id, date_obj, ss, effective)
                    state.mark_students_busy(course, date_obj, ss)
                    lid = state._cached_lecturer_id(course)
                    state.mark_lecturer_busy(lid, date_obj, ss)
                    state.bind_family(state.family_key(course), date_obj, ss)
                    state.bind_shared_unit(course, date_obj, ss)
                    state.bind_norm_code_day(course, date_obj)
                    state.mark_cohort_scheduled(course, date_obj, ss)
                    scheduled_ids.add(course.id)
                    _already_scheduled_cache.add(course.id)
                    
                    log_placement(
                        course.id, course.course_code,
                        venue.id, venue.code,
                        str(date_obj), f"{ss.strftime('%H:%M')}-{se.strftime('%H:%M')}",
                        effective
                    )

                state.daily_load[date_obj] += 1
                state.placed_families.add(nc)
                placed_total += len(variants)
                rescued = True

                rooms_str = ", ".join(
                    f"{assignment[c.id].code}({course_student_count(c)}stu)"
                    for c in variants
                )
                safe_print(f"  [Phase3b] '{nc}' → {date_obj} {ss} | {rooms_str}")
                log_info(f"Phase 3b rescued {nc} with rooms: {rooms_str}")

    safe_print(f"[Phase3b] Rescued {placed_total} variants")
    log_info(f"Phase 3b completed: rescued {placed_total}")
    
    return placed_total


# ─────────────────────────────────────────────────────────
# PHASE 4: FORCED FALLBACK
# ─────────────────────────────────────────────────────────
def forced_fallback_pass(all_courses, state, scheduled_ids):
    total_placed = 0
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    by_norm: Dict[str, List] = defaultdict(list)

    for c in all_courses:
        nc = normalize_course_code(getattr(c, "course_code", "") or "")
        by_norm[nc].append(c)

    for sweep in range(1, 5):
        unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
        if not unscheduled:
            break

        safe_print(f"\n[Phase4] Sweep {sweep}: {len(unscheduled)} unscheduled")
        log_info(f"Phase 4 sweep {sweep}: {len(unscheduled)} unscheduled")
        
        before_sweep = len(scheduled_ids)
        unscheduled = sorted(unscheduled, key=lambda c: -state.priority_score(c))

        if sweep == 2:
            for c in unscheduled:
                state.release_family_binding(c)
            log_info("Phase 4 sweep 2: released family bindings")

        if sweep == 3:
            for nc, group in by_norm.items():
                if nc in state.placed_families:
                    continue
                group_unsched = [c for c in group if c.id not in scheduled_ids]
                if not group_unsched:
                    if nc in state.analysis.shared_unit_groups:
                        state.placed_families.add(nc)
                    continue
                if nc not in state.analysis.shared_unit_groups:
                    continue
                fam_placed = False
                for date_obj, _ in dates:
                    if fam_placed:
                        break
                    for ss, se in all_slots:
                        if fam_placed:
                            break
                        if place_merged_family(group_unsched, nc, date_obj, ss, se, state, scheduled_ids):
                            fam_placed = True
                            log_info(f"Phase 4 sweep 3 placed family {nc}")

        for date_obj, _ in dates:
            for ss, se in all_slots:
                if not state.slot_has_any_venue_space(date_obj, ss, 1):
                    continue
                placed_in_slot = defaultdict(list)
                for course in unscheduled:
                    if course.id in scheduled_ids:
                        continue
                    nc = normalize_course_code(course.course_code or "")
                    if nc in state.analysis.shared_unit_groups:
                        continue
                    pk = state._py_key(course)
                    if pk and pk in placed_in_slot:
                        conflict = False
                        for placed_c in placed_in_slot[pk]:
                            if not exam_is_collision_exempt(course, placed_c):
                                conflict = True
                                break
                        if conflict:
                            continue
                    needed = course_student_count(course)
                    if not state.slot_has_any_venue_space(date_obj, ss, needed):
                        continue
                    # BUGFIX (same as single-campus exam scheduler): don't release
                    # the family/day lock and then place anyway — that's how the
                    # same course_allocation ended up with two ExamTempTimetable
                    # rows on two different dates. Only proceed if the lock is
                    # genuinely gone after the release attempt (i.e. no real
                    # sibling row exists at the locked slot); otherwise skip this
                    # course for this slot.
                    if sweep >= 2 and state.check_family_conflict(course, date_obj, ss):
                        state.release_family_binding(course)
                        if state.check_family_conflict(course, date_obj, ss):
                            continue
                    if sweep >= 2 and state.check_norm_code_day_conflict(course, date_obj):
                        state.release_family_binding(course)
                        if state.check_norm_code_day_conflict(course, date_obj):
                            continue
                    if _check_hard_constraints(course, date_obj, ss, state):
                        continue
                    if try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                        relax_consecutive=True):
                        if pk:
                            placed_in_slot[pk].append(course)

        newly_placed = len(scheduled_ids) - before_sweep
        total_placed += newly_placed
        safe_print(f"[Phase4] Sweep {sweep}: +{newly_placed}")
        log_info(f"Phase 4 sweep {sweep}: +{newly_placed}")
        
        if newly_placed == 0 and sweep < 4:
            safe_print(f"[Phase4] No progress — escalating")
            log_warning("Phase 4 no progress, escalating")

    return total_placed


# ─────────────────────────────────────────────────────────
# PHASE 5: DB-DRIVEN FALLBACK
# ─────────────────────────────────────────────────────────
def sync_scheduled_ids_from_db(scheduled_ids: Set[int]):
    db_ids = set(
        ExamTempTimetable.objects.values_list("course_allocation_id", flat=True).distinct()
    )
    before = len(scheduled_ids)
    scheduled_ids.update(db_ids)
    _already_scheduled_cache.update(db_ids)
    return len(scheduled_ids) - before


def rebuild_state_from_db(state: SchedulerState, all_courses: List):
    safe_print("[DBRebuild] Rebuilding scheduler state from DB...")
    log_info("Rebuilding scheduler state from DB")

    state.venue_usage.clear()
    state._py_busy.clear()
    state._py_busy_allocs.clear()
    state.lecturer_busy.clear()
    state.cohort_last_slot_idx.clear()
    state.cohort_daily_count.clear()
    state.family_slot.clear()
    state.family_day.clear()
    state.shared_unit_lock.clear()
    state.norm_code_day_lock.clear()

    course_by_id = {c.id: c for c in all_courses}
    entries = list(
        ExamTempTimetable.objects.values("course_allocation_id", "venue_id", "date", "start_time")
    )
    safe_print(f"[DBRebuild] Found {len(entries)} entries in DB")
    log_info(f"DB rebuild found {len(entries)} entries")

    for e in entries:
        vid = e["venue_id"]
        cid = e["course_allocation_id"]
        date_obj = e["date"]
        ss = e["start_time"]
        course = course_by_id.get(cid)
        n = course_student_count(course) if course else 1

        cap = state.venue_examcap.get(vid, 0)
        current_usage = state.venue_usage[(vid, date_obj, ss)]
        if current_usage < cap:
            take = min(n, cap - current_usage)
            state.venue_usage[(vid, date_obj, ss)] += take

        if course:
            pk = state._py_key(course)
            if pk:
                state._py_busy.setdefault((date_obj, ss), set()).add(pk)
                state._py_busy_allocs.setdefault((date_obj, ss, pk), []).append(course)
            lid = state._cached_lecturer_id(course)
            if lid:
                state.lecturer_busy[lid].add((date_obj, ss))
            idx = state._slot_start_to_idx.get(ss)
            if idx is not None and pk:
                key = (pk, date_obj)
                prev = state.cohort_last_slot_idx.get(key)
                if prev is None or idx > prev:
                    state.cohort_last_slot_idx[key] = idx
                state.cohort_daily_count[(pk, date_obj)] += 1

            fk = state.family_key(course)
            if fk and fk not in state.family_slot:
                state.family_slot[fk] = (date_obj, ss)
                state.family_day[fk] = date_obj
            nc = state._norm_code(course)
            if nc and nc in state._cross_cohort_norm_codes:
                if nc not in state.shared_unit_lock:
                    state.shared_unit_lock[nc] = (date_obj, ss)
                if nc not in state.norm_code_day_lock:
                    state.norm_code_day_lock[nc] = date_obj

    state._recompute_cap_cache()

    free_slots = []
    for date_obj, wday in state.dates_in_order():
        for ss, se in state.all_slots_ordered:
            avail = sum(state.venue_remaining(v.id, date_obj, ss) for v in state.venues)
            if avail > 0:
                free_slots.append((date_obj, ss, se, avail))

    safe_print(f"[DBRebuild] Free slot-venues: {len(free_slots)}")
    log_info(f"DB rebuild: {len(free_slots)} free slot-venues found")
    
    return free_slots


def db_driven_fallback_pass(all_courses, state, scheduled_ids):
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0

    safe_print(f"\n[Phase5-DB] {len(unscheduled)} courses unscheduled")
    log_info(f"Phase 5 DB-driven fallback: {len(unscheduled)} unscheduled")

    free_slots = rebuild_state_from_db(state, all_courses)
    sync_scheduled_ids_from_db(scheduled_ids)
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]

    if not unscheduled or not free_slots:
        log_info("Phase 5: no unscheduled courses or no free slots")
        return 0

    placed = 0
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    unscheduled = sorted(unscheduled, key=lambda c: -state.priority_score(c))

    for date_obj, _ in dates:
        for ss, se in all_slots:
            if not state.slot_has_any_venue_space(date_obj, ss, 1):
                continue

            slot_free_venues = sorted(
                [
                    (v, state.venue_remaining(v.id, date_obj, ss))
                    for v in state.venues_by_cap_desc
                    if state.venue_remaining(v.id, date_obj, ss) > 0
                    and state.venue_examcap.get(v.id, 0) > 0
                ],
                key=lambda x: state.venue_examcap.get(x[0].id, 0),
                reverse=True,
            )
            if not slot_free_venues:
                continue
            total_free_slot = sum(
                min(rem, state.venue_examcap.get(v.id, 0))
                for v, rem in slot_free_venues
            )
            placed_in_slot = defaultdict(list)

            for course in unscheduled:
                if course.id in scheduled_ids:
                    continue
                nc = normalize_course_code(course.course_code or "")
                if nc in state.analysis.shared_unit_groups:
                    continue
                pk = state._py_key(course)
                if pk and pk in placed_in_slot:
                    conflict = False
                    for placed_c in placed_in_slot[pk]:
                        if not exam_is_collision_exempt(course, placed_c):
                            conflict = True
                            break
                    if conflict:
                        continue
                needed = course_student_count(course)
                if total_free_slot < needed:
                    continue
                if _check_hard_constraints(course, date_obj, ss, state):
                    continue

                free_venues = [
                    (v, state.venue_remaining(v.id, date_obj, ss))
                    for v, _ in slot_free_venues
                    if state.venue_remaining(v.id, date_obj, ss) > 0
                ]
                for v, rem in free_venues:
                    venue_capacity = state.venue_examcap.get(v.id, 0)
                    if venue_capacity >= needed and rem >= needed:
                        if place_single(course, v, date_obj, ss, se,
                                        state, scheduled_ids, relax_consecutive=True):
                            placed += 1
                            total_free_slot -= needed
                            if pk:
                                placed_in_slot[pk].append(course)
                            break

    safe_print(f"[Phase5-DB] Placed {placed}")
    log_info(f"Phase 5 completed: placed {placed}")
    
    return placed


# ─────────────────────────────────────────────────────────
# PHASE 6: ULTIMATE FALLBACK
# ─────────────────────────────────────────────────────────
def _place_one_course_ultimate(course, dates, all_slots, state, scheduled_ids):
    # BUGFIX (same as single-campus exam scheduler): guard against writing a
    # second ExamTempTimetable row for a course that already has one — every
    # other placement function here (place_single, place_multi_venue,
    # _commit_single_venue) checks _course_already_in_db() first; this last-
    # resort function didn't, which is how a course could end up scheduled on
    # two different dates if scheduled_ids ever fell out of sync with the DB.
    if _course_already_in_db(course):
        real = ExamTempTimetable.objects.filter(
            course_allocation=course
        ).values("date", "start_time").first()
        if real:
            state.bind_family(state.family_key(course), real["date"], real["start_time"])
            state.bind_shared_unit(course, real["date"], real["start_time"])
            state.bind_norm_code_day(course, real["date"])
        scheduled_ids.add(course.id)
        return True

    needed = course_student_count(course)
    lid = state._cached_lecturer_id(course)

    for date_obj, _ in dates:
        if course.id in scheduled_ids:
            return True
        for ss, se in all_slots:
            if lid and not state.lecturer_available(lid, date_obj, ss):
                continue
            if not state.students_available(course, date_obj, ss):
                continue
            if state.check_norm_code_day_conflict(course, date_obj):
                continue
            if state.check_shared_unit_conflict(course, date_obj, ss):
                continue
            if state.check_family_conflict(course, date_obj, ss):
                continue

            room = _ultimate_find_room(needed, date_obj, ss, state)
            if room is None:
                continue

            try:
                with transaction.atomic():
                    ExamTempTimetable.objects.create(
                        course_allocation=course, venue=room,
                        date=date_obj, day=date_obj.strftime("%A"),
                        start_time=ss, end_time=se,
                    )
            except Exception:
                continue

            cap = state.venue_examcap.get(room.id, 0)
            effective = min(needed, cap) if cap else needed
            state.consume_venue(room.id, date_obj, ss, effective)
            state.mark_students_busy(course, date_obj, ss)
            state.mark_lecturer_busy(lid, date_obj, ss)
            state.bind_family(state.family_key(course), date_obj, ss)
            state.bind_shared_unit(course, date_obj, ss)
            state.bind_norm_code_day(course, date_obj)
            state.mark_cohort_scheduled(course, date_obj, ss)
            scheduled_ids.add(course.id)
            _already_scheduled_cache.add(course.id)
            state.daily_load[date_obj] += 1
            
            log_placement(
                course.id, course.course_code,
                room.id, room.code,
                str(date_obj), f"{ss.strftime('%H:%M')}-{se.strftime('%H:%M')}",
                effective
            )
            log_info(f"Ultimate fallback placed {course.course_code} at {date_obj} {ss}")
            
            return True
    return False


def ultimate_fallback_pass(all_courses, state, scheduled_ids):
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0

    safe_print(f"\n[Phase6-Ultimate] {len(unscheduled)} unscheduled — relaxing capacity")
    log_info(f"Phase 6 Ultimate fallback: {len(unscheduled)} unscheduled")

    placed_before = len(scheduled_ids)
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    course_by_id = {c.id: c for c in all_courses}

    # Step 1: Shared families
    family_residuals = {}
    for nc, cids in state.analysis.shared_unit_groups.items():
        stuck = [course_by_id[cid] for cid in cids
                 if cid in course_by_id and cid not in scheduled_ids]
        if stuck:
            family_residuals[nc] = stuck

    for nc, variants in family_residuals.items():
        variants = [c for c in variants if c.id not in scheduled_ids]
        if not variants:
            continue

        rescued = False
        for date_obj, _ in dates:
            if rescued:
                break
            for ss, se in all_slots:
                if rescued:
                    break

                slot_ok = True
                for c in variants:
                    lid = state._cached_lecturer_id(c)
                    if lid and not state.lecturer_available(lid, date_obj, ss):
                        slot_ok = False
                        break
                    if not state.students_available(c, date_obj, ss):
                        slot_ok = False
                        break
                    if state.check_norm_code_day_conflict(c, date_obj):
                        slot_ok = False
                        break
                    if state.check_shared_unit_conflict(c, date_obj, ss):
                        slot_ok = False
                        break
                    if state.check_family_conflict(c, date_obj, ss):
                        slot_ok = False
                        break
                if not slot_ok:
                    continue

                assigned = {}
                used_ids: Set[int] = set()
                all_ok = True
                for c in sorted(variants, key=lambda c: -course_student_count(c)):
                    room = _ultimate_find_room(
                        course_student_count(c), date_obj, ss, state, exclude_ids=used_ids
                    )
                    if room is None:
                        all_ok = False
                        break
                    assigned[c.id] = room
                    used_ids.add(room.id)

                if not all_ok:
                    continue

                entries = [
                    ExamTempTimetable(
                        course_allocation=c, venue=assigned[c.id],
                        date=date_obj, day=date_obj.strftime("%A"),
                        start_time=ss, end_time=se,
                    )
                    for c in variants
                ]
                try:
                    with transaction.atomic():
                        ExamTempTimetable.objects.bulk_create(entries, ignore_conflicts=True)
                except Exception as exc:
                    safe_print(f"  [Phase6] DB error '{nc}': {exc}")
                    log_error(f"Phase 6 DB error for {nc}: {exc}")
                    continue

                for c in variants:
                    room = assigned[c.id]
                    cap = state.venue_examcap.get(room.id, 0)
                    effective = min(course_student_count(c), cap) if cap else course_student_count(c)
                    state.consume_venue(room.id, date_obj, ss, effective)
                    state.mark_students_busy(c, date_obj, ss)
                    lid = state._cached_lecturer_id(c)
                    state.mark_lecturer_busy(lid, date_obj, ss)
                    state.bind_family(state.family_key(c), date_obj, ss)
                    state.bind_shared_unit(c, date_obj, ss)
                    state.bind_norm_code_day(c, date_obj)
                    state.mark_cohort_scheduled(c, date_obj, ss)
                    scheduled_ids.add(c.id)
                    _already_scheduled_cache.add(c.id)

                state.daily_load[date_obj] += 1
                state.placed_families.add(nc)
                rescued = True
                rooms_str = ", ".join(
                    f"{assigned[c.id].code}({course_student_count(c)}stu)" for c in variants
                )
                safe_print(f"  [Phase6-Family] '{nc}' → {date_obj} {ss} | {rooms_str}")
                log_info(f"Phase 6 rescued family {nc} at {date_obj} {ss} with {rooms_str}")

        if not rescued:
            for c in variants:
                if c.id in scheduled_ids:
                    continue
                _place_one_course_ultimate(c, dates, all_slots, state, scheduled_ids)

    # Step 2: Individual courses
    individual_remaining = sorted(
        [c for c in all_courses
         if c.id not in scheduled_ids
         and normalize_course_code(c.course_code or "") not in state.analysis.shared_unit_groups],
        key=lambda c: -course_student_count(c)
    )

    for course in individual_remaining:
        if course.id in scheduled_ids:
            continue
        _place_one_course_ultimate(course, dates, all_slots, state, scheduled_ids)

    still_unplaced = [c for c in individual_remaining if c.id not in scheduled_ids]
    if still_unplaced:
        safe_print(f"  [Phase6] WARNING: {len(still_unplaced)} courses could not be placed")
        log_warning(f"Phase 6: {len(still_unplaced)} courses could not be placed")

    placed = len(scheduled_ids) - placed_before
    safe_print(f"[Phase6-Ultimate] Placed {placed}")
    log_info(f"Phase 6 completed: placed {placed}")
    
    return placed


# ─────────────────────────────────────────────────────────
# PHASE 7: NUCLEAR FALLBACK
# ─────────────────────────────────────────────────────────
def nuclear_fallback_pass(all_courses, state, scheduled_ids):
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0

    safe_print(f"\n[Phase7-Nuclear] {len(unscheduled)} unscheduled")
    log_info(f"Phase 7 Nuclear fallback: {len(unscheduled)} unscheduled")

    placed = 0
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered

    unscheduled_sorted = sorted(unscheduled, key=lambda c: -state.priority_score(c))

    for date_obj, _ in dates:
        for ss, se in all_slots:
            if not state.slot_has_any_venue_space(date_obj, ss, 1):
                continue
            placed_in_slot = defaultdict(list)

            for course in unscheduled_sorted:
                if course.id in scheduled_ids:
                    continue
                nc = normalize_course_code(course.course_code or "")
                if nc in state.analysis.shared_unit_groups:
                    continue
                pk = state._py_key(course)
                if pk and pk in placed_in_slot:
                    conflict = False
                    for placed_c in placed_in_slot[pk]:
                        if not exam_is_collision_exempt(course, placed_c):
                            conflict = True
                            break
                    if conflict:
                        continue

                # Release stale locks
                fk = state.family_key(course)
                locked_fam = state.family_slot.get(fk)
                if locked_fam:
                    sibling_ids = state.analysis.shared_unit_groups.get(nc, [])
                    sibling_placed = any(
                        sid != course.id and sid in scheduled_ids
                        for sid in sibling_ids
                    )
                    if not sibling_placed:
                        state.family_slot.pop(fk, None)
                        state.family_day.pop(fk, None)
                        if nc:
                            state.norm_code_day_lock.pop(nc, None)
                            state.shared_unit_lock.pop(nc, None)

                needed = course_student_count(course)
                if not state.slot_has_any_venue_space(date_obj, ss, needed):
                    continue
                if _check_hard_constraints(course, date_obj, ss, state):
                    continue
                if try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                    relax_consecutive=True):
                    placed += 1
                    if pk:
                        placed_in_slot[pk].append(course)
                    break

    safe_print(f"[Phase7-Nuclear] Placed {placed}")
    log_info(f"Phase 7 completed: placed {placed}")
    
    return placed


# ─────────────────────────────────────────────────────────
# AUDITING
# ─────────────────────────────────────────────────────────
def _audit_lecturer_conflicts(state, all_courses):
    from collections import Counter
    
    entries = list(ExamTempTimetable.objects.values("course_allocation_id", "date", "start_time"))
    course_by_id = {c.id: c for c in all_courses}
    slot_lecturers: Dict[Tuple, List[int]] = defaultdict(list)

    for e in entries:
        course = course_by_id.get(e["course_allocation_id"])
        if not course:
            continue
        lid = state._cached_lecturer_id(course)
        if lid:
            slot_lecturers[(e["date"], e["start_time"])].append(lid)

    violations = 0
    for (date, ss), lids in slot_lecturers.items():
        for lid, count in Counter(lids).items():
            if count > 1:
                violations += 1
                safe_print(f"[Audit-LECTURER] VIOLATION: lecturer={lid} x{count} at {date} {ss}")
                log_conflict("lecturer", [lid], f"{count} exams at {date} {ss}")
                log_error(f"Lecturer conflict: {lid} has {count} exams at {date} {ss}")

    if violations == 0:
        log_info("Audit: No lecturer conflicts found")
    
    return violations


def _audit_student_conflicts(state, all_courses):
    entries = list(ExamTempTimetable.objects.values("course_allocation_id", "date", "start_time"))
    course_by_id = {c.id: c for c in all_courses}
    slot_cohorts: Dict[Tuple, List] = defaultdict(list)

    for e in entries:
        course = course_by_id.get(e["course_allocation_id"])
        if not course:
            continue
        pk = _prog_year_key(course)
        if pk:
            slot_cohorts[(e["date"], e["start_time"])].append((pk, course))

    violations = 0
    for (date, ss), cohort_courses in slot_cohorts.items():
        by_pk: Dict[str, List] = defaultdict(list)
        for pk, course in cohort_courses:
            by_pk[pk].append(course)
        for pk, courses in by_pk.items():
            if len(courses) < 2:
                continue
            for i in range(len(courses)):
                for j in range(i + 1, len(courses)):
                    if normalize_course_code(courses[i].course_code or "") == normalize_course_code(courses[j].course_code or ""):
                        continue
                    if _combined_group_are_paired(courses[i].id, courses[j].id):
                        continue
                    if not exam_is_collision_exempt(courses[i], courses[j]):
                        violations += 1
                        safe_print(f"[Audit-STUDENT] VIOLATION: {pk} has "
                                   f"{courses[i].course_code} + {courses[j].course_code} "
                                   f"at {date} {ss}")
                        log_conflict(
                            "student", [courses[i].course_code, courses[j].course_code],
                            f"{pk} at {date} {ss}"
                        )

    if violations == 0:
        log_info("Audit: No student conflicts found")
    
    return violations


def _audit_and_fix_duplicate_placements(state: SchedulerState, all_courses: List) -> int:
    """
    Safety net (same as single-campus exam scheduler): find any course
    scheduled on more than one distinct (date, start_time) and repair it by
    keeping only the earliest slot. A course legitimately having multiple
    rows at the SAME (date, start_time) but different venues — a multi-venue
    split for an oversized course — is left untouched.
    """
    course_by_id = {c.id: c for c in all_courses}
    entries = list(
        ExamTempTimetable.objects
        .values("id", "course_allocation_id", "date", "start_time")
        .order_by("date", "start_time", "id")
    )
    by_course: Dict[int, list] = defaultdict(list)
    for e in entries:
        by_course[e["course_allocation_id"]].append(e)

    fixed = 0
    for cid, rows in by_course.items():
        distinct_slots = {(r["date"], r["start_time"]) for r in rows}
        if len(distinct_slots) <= 1:
            continue

        keep_slot = min(distinct_slots)
        drop_ids = [r["id"] for r in rows if (r["date"], r["start_time"]) != keep_slot]
        course = course_by_id.get(cid)
        code = getattr(course, "course_code", "?") if course else "?"
        safe_print(
            f"[Audit-DUPLICATE] FIXED: course_allocation_id={cid} ({code}) was "
            f"scheduled on {sorted(distinct_slots)} — kept {keep_slot}, "
            f"removed {len(drop_ids)} duplicate row(s)"
        )
        log_conflict(
            "duplicate_date", [cid],
            f"{code} was on {sorted(distinct_slots)} — kept {keep_slot}, "
            f"removed {len(drop_ids)} row(s)"
        )
        ExamTempTimetable.objects.filter(id__in=drop_ids).delete()
        if course:
            date_obj, ss = keep_slot
            state.bind_family(state.family_key(course), date_obj, ss)
            state.bind_shared_unit(course, date_obj, ss)
            state.bind_norm_code_day(course, date_obj)
        fixed += 1

    if fixed == 0:
        safe_print("[Audit-DUPLICATE] ✓ No course scheduled on more than one date")
        log_info("Audit: No duplicate course dates found")
    return fixed


def _audit_venue_overcapacity(state: SchedulerState, all_courses: List) -> int:
    entries = list(
        ExamTempTimetable.objects.values(
            "venue_id", "date", "start_time", "course_allocation_id"
        )
    )
    course_by_id = {c.id: c for c in all_courses}

    slot_venue_students: Dict[Tuple, int] = defaultdict(int)
    for e in entries:
        key = (e["venue_id"], e["date"], e["start_time"])
        course = course_by_id.get(e["course_allocation_id"])
        n = course_student_count(course) if course else 1
        slot_venue_students[key] += n

    violations = 0
    for (vid, date, ss), total_assigned in slot_venue_students.items():
        cap = state.venue_examcap.get(vid, 0)
        if total_assigned > cap:
            violations += 1
            safe_print(f"[Audit-OVERCAP] VIOLATION: venue_id={vid} date={date} slot={ss} | "
                       f"assigned={total_assigned} > exam_cap={cap}")
            log_conflict(
                "overcapacity", [vid],
                f"assigned={total_assigned}, capacity={cap} at {date} {ss}"
            )

    if violations == 0:
        safe_print("[Audit-OVERCAP] ✓ Zero over-capacity venue assignments")
        log_info("Audit: No venue overcapacity violations found")
    
    return violations


def _build_shared_venue_exam_groups() -> int:
    slot_map: Dict[Tuple, List] = defaultdict(list)
    entries = list(
        ExamTempTimetable.objects.select_related("course_allocation", "venue")
        .values("id", "venue_id", "date", "start_time", "end_time",
                "course_allocation_id", "day")
    )

    for e in entries:
        key = (e["venue_id"], e["date"], e["start_time"])
        slot_map[key].append(e)

    created = 0
    for (vid, date_val, start_val), group_entries in slot_map.items():
        if len(group_entries) < 2:
            continue
        end_val = group_entries[0]["end_time"]
        day_val = group_entries[0]["day"]

        try:
            with transaction.atomic():
                venue_obj = Venue.objects.get(pk=vid)
                svg, _ = SharedVenueExamGroup.objects.get_or_create(
                    venue=venue_obj,
                    date=date_val,
                    start_time=start_val,
                    end_time=end_val,
                    defaults={"day": day_val},
                )
                cids = [e["course_allocation_id"] for e in group_entries]
                cas = list(CourseAllocation.objects.filter(pk__in=cids))
                svg.course_allocations.set(cas)
                svg.total_students = sum(int(ca.number_of_students or 0) for ca in cas)
                base_entry = ExamTempTimetable.objects.filter(
                    venue_id=vid, date=date_val, start_time=start_val
                ).first()
                if base_entry:
                    svg.exam_temp_timetable_entry = base_entry
                svg.save()
                created += 1
                log_info(f"Created SharedVenueExamGroup: venue {venue_obj.code} at {date_val} {start_val} with {len(cas)} courses")
        except Exception as exc:
            safe_print(f"[SharedVenueGroup] Error venue={vid} date={date_val}: {exc}")
            log_error(f"SharedVenueGroup error venue {vid} at {date_val}: {exc}")

    safe_print(f"[SharedVenueGroup] Created/updated {created} records")
    log_info(f"SharedVenueExamGroup: {created} records created/updated")
    
    return created


# ─────────────────────────────────────────────────────────
# CLEAR TEMP TABLES
# ─────────────────────────────────────────────────────────
@retry_on_lock(max_retries=5, delay=0.3)
def clear_exam_temp_tables():
    with transaction.atomic():
        count1 = ExamTempTimetable.objects.count()
        count2 = CampusExamTempTimetable.objects.count()
        count3 = MergedCourseGroup.objects.filter(published=False).count()
        count4 = SharedVenueExamGroup.objects.count()
        
        ExamTempTimetable.objects.all().delete()
        CampusExamTempTimetable.objects.all().delete()
        MergedCourseGroup.objects.filter(published=False).delete()
        try:
            SharedVenueExamGroup.objects.all().delete()
        except Exception:
            pass
    
    safe_print(f"Exam temp tables cleared: ExamTemp({count1}), CampusExamTemp({count2}), "
               f"Merged({count3}), SharedVenue({count4})")
    log_info(f"Cleared temp tables: ExamTemp({count1}), CampusExamTemp({count2}), "
             f"Merged({count3}), SharedVenue({count4})")


# ─────────────────────────────────────────────────────────
# MAIN SCHEDULING THREAD
# ─────────────────────────────────────────────────────────
def run_dual_campus_exam_scheduler():
    global _log_file_handle
    
    main_scheduled_list: List[str] = []
    campus_scheduled_list: List[str] = []
    unscheduled_list: List[str] = []
    unified_group_labels: List[str] = []
    cross_campus_labels: List[str] = []

    try:
        # Initialize logger
        init_logger()
        log_info("="*60)
        log_info("STARTING DUAL-CAMPUS EXAM SCHEDULER")
        log_info("="*60)
        
        # Update progress with log session info
        with _progress_lock:
            dual_exam_progress["log_session"] = get_log_session_id()
            dual_exam_progress["log_file"] = get_log_file_path()
        
        enable_wal_mode()
        update_progress(2, "Clearing temp tables…", console_msg="Removing previous draft")
        clear_exam_temp_tables()

        # Build CombinedCourseGroup cache
        _build_combined_group_cache()

        # ── Config ───────────────────────────────────────
        update_progress(4, "Loading configuration…")
        main_config = ExamSchedulerConfig.objects.first()
        if not main_config:
            with _progress_lock:
                dual_exam_progress["status"] = "error"
                dual_exam_progress["message"] = "No ExamSchedulerConfig found."
            log_error("No ExamSchedulerConfig found")
            return

        slot_size = main_config.slot_size
        max_exam_days = main_config.max_exam_days
        log_info(f"Config loaded: slot_size={slot_size}, max_exam_days={max_exam_days}")

        # ── Build date range ──────────────────────────────
        date_range = []
        if hasattr(main_config, "get_date_range"):
            try:
                excluded_set = set(main_config.excluded_date_list()) if hasattr(
                    main_config, "excluded_date_list") else set()
                for ds, weekday_name in main_config.get_date_range():
                    if ds not in excluded_set:
                        try:
                            date_obj = datetime.datetime.strptime(ds, "%Y-%m-%d").date()
                            date_range.append((date_obj, weekday_name))
                        except Exception:
                            pass
            except Exception:
                date_range = []

        if not date_range:
            excluded = set()
            excl_raw = getattr(main_config, "excluded_days", "")
            if excl_raw and isinstance(excl_raw, str):
                excluded = {d.strip() for d in excl_raw.split(",") if d.strip()}
            if hasattr(main_config, "excluded_date_list"):
                try:
                    excluded |= set(main_config.excluded_date_list())
                except Exception:
                    pass
            collected = 0
            offset = 0
            end_date = getattr(main_config, "end_date", None)
            while collected < max_exam_days:
                d = main_config.start_date + datetime.timedelta(days=offset)
                offset += 1
                if end_date and d > end_date:
                    break
                if offset > 365:
                    break
                ds = d.strftime("%Y-%m-%d")
                if ds in excluded:
                    continue
                skip_weekends = getattr(main_config, "skip_weekends", True)
                if skip_weekends and d.weekday() >= 5:
                    continue
                date_range.append((d, d.strftime("%A")))
                collected += 1

        slots = generate_exam_slots(main_config.start_time, main_config.end_time, slot_size)
        if not date_range or not slots:
            with _progress_lock:
                dual_exam_progress["status"] = "error"
                dual_exam_progress["message"] = "No valid dates or slots in config."
            log_error("No valid dates or slots in config")
            return

        log_info(f"Date range: {len(date_range)} days, {len(slots)} slots per day")

        morning_slots = [(s, e) for s, e in slots if s < datetime.time(12, 0)]
        afternoon_slots = [(s, e) for s, e in slots
                           if datetime.time(12, 0) <= s < datetime.time(17, 0)]
        evening_slots = [(s, e) for s, e in slots if is_evening_slot(s)]
        day_slots = morning_slots + afternoon_slots
        all_slots_ordered = day_slots + evening_slots

        safe_print(f"Dates: {len(date_range)} | Slots/day: {len(slots)}")
        log_info(f"Slots: {len(morning_slots)} morning, {len(afternoon_slots)} afternoon, {len(evening_slots)} evening")

        # ── Load allocations ─────────────────────────────
        update_progress(6, "Loading allocations…")
        main_allocs = list(CourseAllocation.objects.select_related(
            "program", "lecturer", "department", "program_course", "selection_group",
            "specialization_stem", "specialization_stem__category"
        ).filter(submitted_to_tt=True))
        if not main_allocs:
            main_allocs = list(CourseAllocation.objects.select_related(
                "program", "lecturer", "department", "program_course", "selection_group",
                "specialization_stem", "specialization_stem__category"
            ).filter(approved_by_dvc=True))

        campus_allocs = list(CampusCourseAllocation.objects.select_related(
            "program", "lecturer", "campus", "department"
        ).filter(submitted_to_tt=True))
        if not campus_allocs:
            campus_allocs = list(CampusCourseAllocation.objects.select_related(
                "program", "lecturer", "campus", "department"
            ).filter(approved_by_dvc=True))

        total_main = len(main_allocs)
        total_campus = len(campus_allocs)
        total_allocs = total_main + total_campus
        safe_print(f"Main: {total_main} | Campus: {total_campus} | Total: {total_allocs}")
        log_info(f"Loaded allocations: Main({total_main}), Campus({total_campus}), Total({total_allocs})")

        if total_allocs == 0:
            with _progress_lock:
                dual_exam_progress["status"] = "completed"
                dual_exam_progress["message"] = "No allocations to schedule."
            log_info("No allocations to schedule")
            return

        update_progress(8, f"Found {total_allocs} allocations",
                        scheduled=0, remaining=total_allocs)

        # ── Build unified exam groups ─────────────────────
        update_progress(10, "Phase 0: Building unified groups…")
        log_phase("BUILD_UNIFIED_GROUPS")
        groups = build_unified_exam_groups(main_allocs, campus_allocs)
        cross_campus_groups = {k: g for k, g in groups.items() if g.is_cross_campus}
        single_campus_groups = {k: g for k, g in groups.items() if not g.is_cross_campus}

        cross_lect = identify_cross_campus_lecturers(main_allocs, campus_allocs)
        cross_campus_lids: Set[int] = set(cross_lect.keys())

        for code, grp in cross_campus_groups.items():
            unified_group_labels.append(
                f"{code} | main×{len(grp.main_allocs)} "
                f"campus×{len(grp.campus_allocs)} | {grp.total_students}st")
            log_info(f"Cross-campus group: {code} with {len(grp.main_allocs)} main, "
                    f"{len(grp.campus_allocs)} campus, {grp.total_students} students")
        
        for lid, info in cross_lect.items():
            cross_campus_labels.append(
                f"{info['name']} | main:{len(info['main'])} campus:{len(info['campus'])}")
            log_info(f"Cross-campus lecturer: {info['name']} with {len(info['main'])} main, "
                    f"{len(info['campus'])} campus")

        log_info(f"Unified groups: {len(groups)} total, {len(cross_campus_groups)} cross-campus")

        # ── Data Analysis ──────────────────────────────────
        update_progress(12, "Phase 0: Data analysis…")
        log_phase("DATA_ANALYSIS")
        main_analysis = analyze_courses(main_allocs)

        # Detect cross-cohort norm codes
        by_norm_main: Dict[str, List] = defaultdict(list)
        for a in main_allocs:
            nc = normalize_course_code(a.course_code)
            by_norm_main[nc].append(a)

        cross_cohort_codes: Set[str] = set()
        for nc, allocs_in_group in by_norm_main.items():
            py_groups: Dict[str, List] = defaultdict(list)
            for a in allocs_in_group:
                py_groups[_prog_year_key(a)].append(a)
            if len([py for py in py_groups.keys() if py]) >= 2:
                cross_cohort_codes.add(nc)
                log_info(f"Cross-cohort course: {nc}")
        cross_cohort_codes.update(main_analysis.shared_unit_groups.keys())

        # ── Pre-Scheduling Intelligence ──────────────────
        update_progress(14, "Phase 0: Pre-Scheduling Intelligence…")
        log_phase("PRE_SCHEDULING_INTELLIGENCE")
        raw_venues = list(Venue.objects.filter(capacity__isnull=False, capacity__gt=0))
        psi = PreSchedulingIntelligence(
            all_courses=main_allocs,
            analysis=main_analysis,
            config=main_config,
            venues=raw_venues,
        )
        strategy = psi.run()

        for w in strategy.warnings:
            safe_print(f"[PSI ⚠] {w}")
            log_warning(w)

        # ── Scheduler State ──────────────────────────────
        state = SchedulerState(main_config, main_analysis, strategy)
        state._cross_cohort_norm_codes = cross_cohort_codes

        if not state.date_range or not state.venues or not state.slots:
            with _progress_lock:
                dual_exam_progress["status"] = "error"
                dual_exam_progress["message"] = "Invalid config: missing dates, venues, or slots."
            log_error("Invalid config: missing dates, venues, or slots")
            return

        scheduled_main_ids: Set[int] = set()

        update_progress(16,
            f"Phase 0: {len(groups)} groups | {len(cross_campus_groups)} cross",
            unified_groups=unified_group_labels,
            cross_campus_lecturers=cross_campus_labels,
            console_msg=f"Strategy={strategy.mode} Pressure={strategy.seat_pressure:.1%}")

        # ──────────────────────────────────────────────────────
        # PHASE 1: Families First
        # ──────────────────────────────────────────────────────
        log_phase("PHASE_1_FAMILIES_FIRST")
        update_progress(18, "Phase 1: Families first (TRUE student sums)…",
                        scheduled=len(scheduled_main_ids))
        p1_placed = schedule_families_first(main_allocs, state, scheduled_main_ids)
        sync_scheduled_ids_from_db(scheduled_main_ids)

        # ──────────────────────────────────────────────────────
        # PHASE 2: Saturation Loop
        # ──────────────────────────────────────────────────────
        log_phase("PHASE_2_SATURATION")
        update_progress(35, f"Phase 2: Saturation ({len(main_allocs) - len(scheduled_main_ids)} remaining)…",
                        scheduled=len(scheduled_main_ids),
                        remaining=len(main_allocs) - len(scheduled_main_ids))
        schedule_saturation_loop(main_allocs, state, scheduled_main_ids)
        sync_scheduled_ids_from_db(scheduled_main_ids)
        phase2_count = len(scheduled_main_ids)

        # ──────────────────────────────────────────────────────
        # PHASE 3: Cross-day Fill
        # ──────────────────────────────────────────────────────
        log_phase("PHASE_3_CROSS_DAY_FILL")
        update_progress(55, f"Phase 3: Cross-day fill ({len(main_allocs) - len(scheduled_main_ids)} remaining)…",
                        scheduled=len(scheduled_main_ids),
                        remaining=len(main_allocs) - len(scheduled_main_ids))
        p3_placed = cross_day_fill_pass(main_allocs, state, scheduled_main_ids)
        sync_scheduled_ids_from_db(scheduled_main_ids)

        # ──────────────────────────────────────────────────────
        # PHASE 3b: Family Split Rescue
        # ──────────────────────────────────────────────────────
        log_phase("PHASE_3B_FAMILY_SPLIT_RESCUE")
        update_progress(65, f"Phase 3b: Family split rescue ({len(main_allocs) - len(scheduled_main_ids)} remaining)…",
                        scheduled=len(scheduled_main_ids),
                        remaining=len(main_allocs) - len(scheduled_main_ids))
        p3b_placed = family_split_rescue_pass(main_allocs, state, scheduled_main_ids)
        sync_scheduled_ids_from_db(scheduled_main_ids)

        # ──────────────────────────────────────────────────────
        # PHASE 4: Forced Fallback
        # ──────────────────────────────────────────────────────
        log_phase("PHASE_4_FORCED_FALLBACK")
        update_progress(75, f"Phase 4: Forced fallback ({len(main_allocs) - len(scheduled_main_ids)} remaining)…",
                        scheduled=len(scheduled_main_ids),
                        remaining=len(main_allocs) - len(scheduled_main_ids))
        p4_placed = forced_fallback_pass(main_allocs, state, scheduled_main_ids)
        sync_scheduled_ids_from_db(scheduled_main_ids)

        # ──────────────────────────────────────────────────────
        # PHASE 5: DB-Driven Fallback
        # ──────────────────────────────────────────────────────
        log_phase("PHASE_5_DB_FALLBACK")
        update_progress(83, f"Phase 5: DB fallback ({len(main_allocs) - len(scheduled_main_ids)} remaining)…",
                        scheduled=len(scheduled_main_ids),
                        remaining=len(main_allocs) - len(scheduled_main_ids))
        p5_placed = db_driven_fallback_pass(main_allocs, state, scheduled_main_ids)
        sync_scheduled_ids_from_db(scheduled_main_ids)

        # ──────────────────────────────────────────────────────
        # PHASE 6: Ultimate Fallback
        # ──────────────────────────────────────────────────────
        log_phase("PHASE_6_ULTIMATE_FALLBACK")
        update_progress(91, f"Phase 6: Ultimate fallback ({len(main_allocs) - len(scheduled_main_ids)} remaining)…",
                        scheduled=len(scheduled_main_ids),
                        remaining=len(main_allocs) - len(scheduled_main_ids))
        p6_placed = ultimate_fallback_pass(main_allocs, state, scheduled_main_ids)
        sync_scheduled_ids_from_db(scheduled_main_ids)

        # ──────────────────────────────────────────────────────
        # PHASE 7: Nuclear Fallback
        # ──────────────────────────────────────────────────────
        if len(main_allocs) - len(scheduled_main_ids) > 0:
            log_phase("PHASE_7_NUCLEAR_FALLBACK")
            update_progress(94, f"Phase 7: Nuclear fallback ({len(main_allocs) - len(scheduled_main_ids)} remaining)…",
                            scheduled=len(scheduled_main_ids),
                            remaining=len(main_allocs) - len(scheduled_main_ids))
            p7_placed = nuclear_fallback_pass(main_allocs, state, scheduled_main_ids)
            sync_scheduled_ids_from_db(scheduled_main_ids)
        else:
            p7_placed = 0

        # ──────────────────────────────────────────────────────
        # PHASE 8: Campus Allocations (Branch)
        # ──────────────────────────────────────────────────────
        log_phase("PHASE_8_CAMPUS_ALLOCATIONS")
        
        scheduled_campus_ids: Set[int] = set()
        campus_tracker = CampusTracker()

        # Campus merged groups
        campus_merged = sorted(
            [g for g in single_campus_groups.values()
             if g.campus_allocs and g.is_mergeable],
            key=lambda g: -g.total_campus_students)
        p8a = 0
        for grp in campus_merged:
            if all(a.id in scheduled_campus_ids for a in grp.campus_allocs):
                continue
            unplaced = [a for a in grp.campus_allocs if a.id not in scheduled_campus_ids]
            slot = find_slot_for_campus_group(
                unplaced, grp.norm_code, date_range, slots,
                state, campus_tracker, cross_campus_lids, relax_lecturer=True)
            if slot:
                date_obj, ss, se = slot
                grp.assign_slot(date_obj, ss, se)
                pc, _ = place_campus_group(
                    unplaced, date_obj, ss, se,
                    campus_tracker, state, cross_campus_lids,
                    scheduled_campus_ids, relax_lecturer=True)
                p8a += pc
                for a in unplaced:
                    if a.id in scheduled_campus_ids:
                        c = a.campus.code if a.campus else "?"
                        campus_scheduled_list.append(
                            f"{a.course_code} [{c}-EXAM-MERGED→{date_obj} {ss.strftime('%H:%M')}]")
                        log_info(f"Campus merged: {a.course_code} at {c} {date_obj} {ss}")

        # Campus remaining groups
        campus_remaining = sorted(
            [g for g in single_campus_groups.values()
             if g.campus_allocs and any(a.id not in scheduled_campus_ids for a in g.campus_allocs)],
            key=lambda g: -g.total_campus_students)
        p8b = 0
        for grp in campus_remaining:
            unplaced = [a for a in grp.campus_allocs if a.id not in scheduled_campus_ids]
            slot = find_slot_for_campus_group(
                unplaced, grp.norm_code, date_range, slots,
                state, campus_tracker, cross_campus_lids, relax_lecturer=True)
            if slot:
                date_obj, ss, se = slot
                grp.assign_slot(date_obj, ss, se)
                pc, _ = place_campus_group(
                    unplaced, date_obj, ss, se,
                    campus_tracker, state, cross_campus_lids,
                    scheduled_campus_ids, relax_lecturer=True)
                p8b += pc
                for a in unplaced:
                    if a.id in scheduled_campus_ids:
                        c = a.campus.code if a.campus else "?"
                        campus_scheduled_list.append(
                            f"{a.course_code} [{c}-EXAM→{date_obj} {ss.strftime('%H:%M')}]")
                        log_info(f"Campus placed: {a.course_code} at {c} {date_obj} {ss}")

        safe_print(f"Phase 8: 8A:{p8a} 8B:{p8b}")
        log_info(f"Phase 8: Campus merged={p8a}, Campus remaining={p8b}")

        # ──────────────────────────────────────────────────────
        # Build SharedVenueExamGroup records
        # ──────────────────────────────────────────────────────
        log_phase("BUILD_SHARED_VENUE_GROUPS")
        update_progress(97, "Building shared venue exam groups…",
                        scheduled=len(scheduled_main_ids) + len(scheduled_campus_ids),
                        remaining=total_allocs - len(scheduled_main_ids) - len(scheduled_campus_ids))
        _build_shared_venue_exam_groups()

        # ──────────────────────────────────────────────────────
        # Audits
        # ──────────────────────────────────────────────────────
        log_phase("AUDIT")
        update_progress(98, "Auditing: checking conflicts…",
                        scheduled=len(scheduled_main_ids) + len(scheduled_campus_ids),
                        remaining=total_allocs - len(scheduled_main_ids) - len(scheduled_campus_ids))

        lecturer_violations = _audit_lecturer_conflicts(state, main_allocs)
        student_violations = _audit_student_conflicts(state, main_allocs)
        overcap_violations = _audit_venue_overcapacity(state, main_allocs)
        duplicate_fixes = _audit_and_fix_duplicate_placements(state, main_allocs)

        # ──────────────────────────────────────────────────────
        # Final Summary
        # ──────────────────────────────────────────────────────
        total_sched = len(scheduled_main_ids) + len(scheduled_campus_ids)
        success_rate = total_sched / max(total_allocs, 1) * 100

        # Build unscheduled list
        for a in main_allocs:
            if a.id not in scheduled_main_ids:
                unscheduled_list.append(f"{a.course_code} [MAIN] – no slot")
                log_warning(f"Unscheduled main: {a.course_code}")
        for a in campus_allocs:
            if a.id not in scheduled_campus_ids:
                c = a.campus.code if a.campus else "?"
                unscheduled_list.append(f"{a.course_code} [{c}] – no slot")
                log_warning(f"Unscheduled campus: {a.course_code} [{c}]")

        phase_stats = {
            "Phase 1 (Families)": p1_placed,
            "Phase 2 (Saturation)": phase2_count,
            "Phase 3 (Cross-day)": p3_placed,
            "Phase 3b (Split Rescue)": p3b_placed,
            "Phase 4 (Forced)": p4_placed,
            "Phase 5 (DB)": p5_placed,
            "Phase 6 (Ultimate)": p6_placed,
            "Phase 7 (Nuclear)": p7_placed,
            "Phase 8 (Campus)": p8a + p8b,
        }

        final_msg = (
            f"DONE: {total_sched}/{total_allocs} ({success_rate:.1f}%) | "
            f"Main: {len(scheduled_main_ids)} | Campus: {len(scheduled_campus_ids)} | "
            f"Unscheduled: {len(unscheduled_list)} | "
            f"Strategy={strategy.mode} Pressure={strategy.seat_pressure:.1%} | "
            f"Families={p1_placed} Sat={phase2_count} Fill={p3_placed} "
            f"FamilyRescue={p3b_placed} Forced={p4_placed} DB={p5_placed} "
            f"Ultimate={p6_placed} Nuclear={p7_placed} Campus={p8a+p8b} | "
            f"LecturerViolations={lecturer_violations} | "
            f"StudentViolations={student_violations} | "
            f"OverCapViolations={overcap_violations} | "
            f"DuplicatesFixed={duplicate_fixes}"
        )

        log_summary(total_sched, total_allocs, success_rate, phase_stats)
        log_info(final_msg)
        log_info("="*60)
        log_info("SCHEDULING COMPLETE")
        log_info("="*60)

        update_progress(100, "Exam scheduling complete!",
                        scheduled=total_sched, remaining=len(unscheduled_list),
                        console_msg=final_msg,
                        main_scheduled=main_scheduled_list,
                        campus_scheduled=campus_scheduled_list,
                        unscheduled=unscheduled_list,
                        unified_groups=unified_group_labels,
                        cross_campus_lecturers=cross_campus_labels,
                        conflict_stats={"detected": lecturer_violations + student_violations + overcap_violations,
                                        "resolved": 0,
                                        "unresolved": lecturer_violations + student_violations + overcap_violations,
                                        "resolution_rate": 0})
        with _progress_lock:
            dual_exam_progress["status"] = "completed"
            dual_exam_progress["message"] = final_msg
        safe_print(f"\n{'='*60}\n{final_msg}\n{'='*60}")

    except Exception as e:
        err = traceback.format_exc()
        safe_print(f"SCHEDULER ERROR:\n{err}")
        log_error(f"Scheduler error: {str(e)}")
        log_error(f"Traceback: {err}")
        scheduler_logger.error("Dual-campus EXAM scheduler fatal error: %s\n%s", e, err)
        
        update_progress(0, f"Error: {str(e)}", console_msg=f"Fatal: {str(e)}")
        with _progress_lock:
            dual_exam_progress["status"] = "error"
            dual_exam_progress["message"] = str(e)
    finally:
        # Close logger
        close_logger()


# ─────────────────────────────────────────────────────────
# HELPER FUNCTIONS FOR CAMPUS SCHEDULING
# ─────────────────────────────────────────────────────────
class CampusTracker:
    def __init__(self):
        self.lecturer_busy: Dict[int, Dict[int, Set]] = defaultdict(lambda: defaultdict(set))
        self.program_busy: Dict[int, Dict[int, Set]] = defaultdict(lambda: defaultdict(set))
        self.total_conflicts = 0

    def alloc_ok(self, alloc: CampusCourseAllocation,
                 date, slot_start, slot_end,
                 main_tracker: SchedulerState,
                 cross_campus_lids: Set[int],
                 relax_lecturer=False) -> bool:
        campus_id = alloc.campus.id if alloc.campus else 0
        lid = alloc.lecturer.id if alloc.lecturer else None
        pid = alloc.program.id if alloc.program else None

        if not relax_lecturer and lid:
            if (date, slot_start) in self.lecturer_busy[campus_id][lid]:
                self.total_conflicts += 1
                log_conflict("campus_lecturer", [lid], f"{alloc.course_code} at {date} {slot_start}")
                return False
            if lid in cross_campus_lids:
                if main_tracker.has_cross_campus_travel_conflict(lid, date, slot_start, slot_end):
                    self.total_conflicts += 1
                    log_conflict("cross_campus_travel", [lid], f"{alloc.course_code} at {date} {slot_start}")
                    return False

        if pid and (date, slot_start) in self.program_busy[campus_id][pid]:
            self.total_conflicts += 1
            log_conflict("campus_program", [pid], f"{alloc.course_code} at {date} {slot_start}")
            return False

        return True

    def group_campus_ok(self, allocs, date, slot_start, slot_end,
                        main_tracker, cross_campus_lids,
                        relax_lecturer=False) -> bool:
        for a in allocs:
            if not self.alloc_ok(a, date, slot_start, slot_end,
                                  main_tracker, cross_campus_lids, relax_lecturer):
                return False
        return True

    def mark_alloc(self, alloc: CampusCourseAllocation,
                   date, slot_start, slot_end,
                   main_tracker: SchedulerState,
                   cross_campus_lids: Set[int]):
        campus_id = alloc.campus.id if alloc.campus else 0
        lid = alloc.lecturer.id if alloc.lecturer else None
        pid = alloc.program.id if alloc.program else None
        if lid:
            self.lecturer_busy[campus_id][lid].add((date, slot_start))
            if lid in cross_campus_lids:
                main_tracker.mark_cross_campus_lecturer(lid, date, slot_start, slot_end)
        if pid:
            self.program_busy[campus_id][pid].add((date, slot_start))


def build_unified_exam_groups(main_allocs, campus_allocs) -> Dict[str, "UnifiedExamGroup"]:
    class UnifiedExamGroup:
        def __init__(self, norm_code: str):
            self.norm_code = norm_code
            self.main_allocs: List[CourseAllocation] = []
            self.campus_allocs: List[CampusCourseAllocation] = []
            self.scheduled_date = None
            self.scheduled_slot_start = None
            self.scheduled_slot_end = None

        @property
        def all_allocs(self):
            return self.main_allocs + self.campus_allocs

        @property
        def total_main_students(self):
            return sum(course_student_count(a) for a in self.main_allocs)

        @property
        def total_campus_students(self):
            return sum(course_student_count(a) for a in self.campus_allocs)

        @property
        def total_students(self):
            return self.total_main_students + self.total_campus_students

        @property
        def is_cross_campus(self):
            return bool(self.main_allocs) and bool(self.campus_allocs)

        @property
        def is_mergeable(self):
            return len(self.all_allocs) > 1

        @property
        def is_shareable(self):
            return self.total_main_students <= 60

        def assign_slot(self, date_obj, slot_start, slot_end):
            self.scheduled_date = date_obj
            self.scheduled_slot_start = slot_start
            self.scheduled_slot_end = slot_end

        def reset_slot(self):
            self.scheduled_date = None
            self.scheduled_slot_start = None
            self.scheduled_slot_end = None

    groups: Dict[str, UnifiedExamGroup] = {}
    for a in main_allocs:
        code = normalize_course_code(a.course_code)
        if code not in groups:
            groups[code] = UnifiedExamGroup(code)
        groups[code].main_allocs.append(a)
    for a in campus_allocs:
        code = normalize_course_code(a.course_code)
        if code not in groups:
            groups[code] = UnifiedExamGroup(code)
        groups[code].campus_allocs.append(a)
    return groups


def identify_cross_campus_lecturers(main_allocs, campus_allocs):
    main_map, campus_map = defaultdict(list), defaultdict(list)
    for a in main_allocs:
        if a.lecturer:
            main_map[a.lecturer.id].append(a)
    for a in campus_allocs:
        if a.lecturer:
            campus_map[a.lecturer.id].append(a)
    cross = {}
    for lid in set(main_map) & set(campus_map):
        name = main_map[lid][0].lecturer.display_name if main_map[lid] else "Unknown"
        cross[lid] = {"name": name, "main": main_map[lid], "campus": campus_map[lid]}
    return cross


def find_slot_for_campus_group(allocs, norm_code, date_range, slots,
                               main_tracker, campus_tracker, cross_campus_lids,
                               relax_lecturer=False):
    for date_obj, _ in date_range:
        for ss, se in slots:
            if campus_tracker.group_campus_ok(allocs, date_obj, ss, se,
                                               main_tracker, cross_campus_lids, relax_lecturer):
                return date_obj, ss, se
    return None


def place_campus_group(allocs, date_obj, ss, se,
                       campus_tracker, main_tracker, cross_campus_lids,
                       scheduled_ids, relax_lecturer=False):
    placed = 0
    
    for alloc in allocs:
        if alloc.id in scheduled_ids:
            continue
        if not campus_tracker.alloc_ok(alloc, date_obj, ss, se,
                                        main_tracker, cross_campus_lids, relax_lecturer):
            continue
        try:
            with transaction.atomic():
                CampusExamTempTimetable.objects.create(
                    course_allocation=alloc, campus=alloc.campus,
                    date=date_obj, day=date_obj.strftime("%A"),
                    start_time=ss, end_time=se)
            campus_tracker.mark_alloc(alloc, date_obj, ss, se,
                                       main_tracker, cross_campus_lids)
            scheduled_ids.add(alloc.id)
            placed += 1
            log_info(f"Campus placement: {alloc.course_code} at {date_obj} {ss}")
        except IntegrityError:
            log_warning(f"Campus placement integrity error for {alloc.course_code}")
            pass
    return placed, 0


# Add travel gap methods to SchedulerState
def _add_travel_methods():
    def has_cross_campus_travel_conflict(self, lid, date, slot_start, slot_end):
        return False

    def mark_cross_campus_lecturer(self, lid, date, slot_start, slot_end):
        pass

    SchedulerState.has_cross_campus_travel_conflict = has_cross_campus_travel_conflict
    SchedulerState.mark_cross_campus_lecturer = mark_cross_campus_lecturer


_add_travel_methods()


# ─────────────────────────────────────────────────────────
# PUBLISH FUNCTIONS
# ─────────────────────────────────────────────────────────
@retry_on_lock(max_retries=5, delay=0.5)
def publish_main_exam_timetable() -> int:
    with transaction.atomic():
        count_before = ExamTimetable.objects.count()
        ExamTimetable.objects.all().delete()
        entries = list(ExamTempTimetable.objects.select_related("course_allocation", "venue").all())
        ExamTimetable.objects.bulk_create([
            ExamTimetable(course_allocation=t.course_allocation, venue=t.venue,
                          day=t.day, date=t.date, start_time=t.start_time, end_time=t.end_time)
            for t in entries])
        count = len(entries)
        log_info(f"Published main exam timetable: {count} entries (deleted {count_before} old)")
        return count


@retry_on_lock(max_retries=5, delay=0.5)
def publish_campus_exam_timetable(campus_id=None) -> int:
    with transaction.atomic():
        qs = CampusExamTimetable.objects
        if campus_id:
            qs = qs.filter(campus_id=campus_id)
        count_before = qs.count()
        qs.all().delete()
        tmp = list(CampusExamTempTimetable.objects.select_related("course_allocation", "campus")
                   .filter(**({"campus_id": campus_id} if campus_id else {})))
        CampusExamTimetable.objects.bulk_create([
            CampusExamTimetable(course_allocation=t.course_allocation, campus=t.campus,
                                day=t.day, date=t.date,
                                start_time=t.start_time, end_time=t.end_time)
            for t in tmp])
        count = len(tmp)
        label = f"campus {campus_id}" if campus_id else "all branch campuses"
        log_info(f"Published campus exam timetable for {label}: {count} entries (deleted {count_before} old)")
        return count


# ─────────────────────────────────────────────────────────
# DJANGO VIEWS
# ─────────────────────────────────────────────────────────
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def dual_exam_scheduler_page(request):
    context = {
        "campuses": Campus.objects.filter(is_active=True),
        "main_config": ExamSchedulerConfig.objects.first(),
        "campus_config": CampusExamSchedulerConfig.objects.first(),
        "main_temp_count": ExamTempTimetable.objects.count(),
        "campus_temp_count": CampusExamTempTimetable.objects.count(),
        "main_pub_count": ExamTimetable.objects.count(),
        "campus_pub_count": CampusExamTimetable.objects.count(),
        "cross_count": len(dual_exam_progress.get("cross_campus_lecturers", [])),
        "unified_count": len(dual_exam_progress.get("unified_groups", [])),
        "log_session": dual_exam_progress.get("log_session", ""),
        "log_file": dual_exam_progress.get("log_file", ""),
    }
    return render(request, "dashboard/dual_exam_scheduler.html", context)


@method_decorator(csrf_exempt, name="dispatch")
class StartDualExamSchedulerView(View):
    def post(self, request):
        with _progress_lock:
            if dual_exam_progress.get("status") == "running":
                return JsonResponse({"status": "already_running",
                                     "message": "Scheduler is already running."})
            dual_exam_progress.update({
                "status": "running", "progress": 0,
                "current_action": "Initialising…",
                "scheduled_count": 0, "remaining_count": 0,
                "batch_info": "", "total_courses": 0, "message": "",
                "console_output": [], "main_scheduled": [], "campus_scheduled": [],
                "unscheduled": [], "unified_groups": [], "cross_campus_lecturers": [],
                "conflict_stats": {"detected": 0, "resolved": 0,
                                   "unresolved": 0, "resolution_rate": 0},
                "log_session": "", "log_file": "",
            })
        threading.Thread(target=run_dual_campus_exam_scheduler, daemon=True).start()
        return JsonResponse({"status": "started",
                             "message": "Dual-campus exam scheduler started."})


class DualExamSchedulerProgressView(View):
    def get(self, request):
        return JsonResponse(dual_exam_progress)


@method_decorator(csrf_exempt, name="dispatch")
class CancelDualExamSchedulerView(View):
    def post(self, request):
        with _progress_lock:
            dual_exam_progress["status"] = "cancelled"
            dual_exam_progress["current_action"] = "Cancelled by user"
        return JsonResponse({"status": "cancelled"})


@method_decorator(csrf_exempt, name="dispatch")
class PublishMainExamTimetableView(View):
    def post(self, request):
        try:
            count = publish_main_exam_timetable()
            return JsonResponse({"status": "success",
                                 "message": f"Published {count} main exam entries.",
                                 "count": count})
        except Exception as e:
            log_error(f"Publish main timetable error: {e}")
            return JsonResponse({"status": "error", "message": str(e)}, status=500)


@method_decorator(csrf_exempt, name="dispatch")
class PublishCampusExamTimetableView(View):
    def post(self, request):
        import json as _json
        try:
            body = _json.loads(request.body or "{}")
        except Exception:
            body = {}
        campus_id = (body.get("campus_id") or request.POST.get("campus_id")
                     or request.GET.get("campus_id"))
        try:
            count = publish_campus_exam_timetable(campus_id=campus_id or None)
            label = f"campus {campus_id}" if campus_id else "all branch campuses"
            return JsonResponse({"status": "success",
                                 "message": f"Published {count} entries for {label}.",
                                 "count": count})
        except Exception as e:
            log_error(f"Publish campus timetable error: {e}")
            return JsonResponse({"status": "error", "message": str(e)}, status=500)


class DualExamTimetableDataView(View):
    def get(self, request):
        view_type = request.GET.get("type", "main")
        campus_id = request.GET.get("campus_id")
        data = []
        if view_type == "main":
            for e in ExamTempTimetable.objects.select_related(
                    "course_allocation", "course_allocation__lecturer",
                    "course_allocation__program", "venue").order_by("date", "start_time"):
                ca = e.course_allocation
                data.append({
                    "id": e.id, "course_code": ca.course_code, "course_name": ca.course_name,
                    "lecturer": ca.lecturer.display_name if ca.lecturer else "TBA",
                    "program": ca.program.name if ca.program else "",
                    "students": ca.number_of_students,
                    "venue": e.venue.code,
                    "venue_exam_capacity": venue_exam_capacity(e.venue),
                    "venue_capacity": e.venue.capacity,
                    "date": str(e.date),
                    "day": e.day, "start_time": str(e.start_time), "end_time": str(e.end_time),
                    "type": "main", "norm_code": normalize_course_code(ca.course_code),
                })
        else:
            qs = CampusExamTempTimetable.objects.select_related(
                "course_allocation", "course_allocation__lecturer",
                "course_allocation__program", "campus").order_by("date", "start_time")
            if campus_id:
                qs = qs.filter(campus_id=campus_id)
            for e in qs:
                ca = e.course_allocation
                data.append({
                    "id": e.id, "course_code": ca.course_code, "course_name": ca.course_name,
                    "lecturer": ca.lecturer.display_name if ca.lecturer else "TBA",
                    "program": ca.program.name if ca.program else "",
                    "students": ca.number_of_students,
                    "campus": e.campus.name if e.campus else "",
                    "campus_code": e.campus.code if e.campus else "",
                    "date": str(e.date), "day": e.day,
                    "start_time": str(e.start_time), "end_time": str(e.end_time),
                    "type": "campus", "norm_code": normalize_course_code(ca.course_code),
                })
        return JsonResponse({"status": "ok", "data": data, "count": len(data)})


class DualExamUnifiedGroupsView(View):
    def get(self, request):
        main_a = list(CourseAllocation.objects.select_related("program", "lecturer")
                      .filter(approved_by_dvc=True))
        campus_a = list(CampusCourseAllocation.objects.select_related(
            "program", "lecturer", "campus").filter(approved_by_dvc=True))
        groups = build_unified_exam_groups(main_a, campus_a)
        result = sorted([{
            "norm_code": code, "total_students": grp.total_students,
            "main_programs": list({a.program.name for a in grp.main_allocs if a.program}),
            "campus_programs": list({a.program.name for a in grp.campus_allocs if a.program}),
            "campuses": list({a.campus.name for a in grp.campus_allocs if a.campus}),
            "is_cross_campus": grp.is_cross_campus, "is_mergeable": grp.is_mergeable,
            "is_shareable": grp.is_shareable,
            "main_count": len(grp.main_allocs), "campus_count": len(grp.campus_allocs),
        } for code, grp in groups.items()],
            key=lambda x: (-x["is_cross_campus"], -x["is_mergeable"], -x["total_students"]))
        return JsonResponse({
            "status": "ok", "unified_groups": result,
            "cross_campus_count": sum(1 for r in result if r["is_cross_campus"]),
            "mergeable_count": sum(1 for r in result if r["is_mergeable"]),
            "shareable_count": sum(1 for r in result if r["is_shareable"]),
            "total_groups": len(result),
        })


class DualExamConflictCheckView(View):
    def get(self, request):
        main_a = list(CourseAllocation.objects.select_related("program", "lecturer")
                      .filter(approved_by_dvc=True))
        campus_a = list(CampusCourseAllocation.objects.select_related(
            "program", "lecturer", "campus").filter(approved_by_dvc=True))
        cross = identify_cross_campus_lecturers(main_a, campus_a)
        groups = build_unified_exam_groups(main_a, campus_a)
        issues = []
        for lid, info in cross.items():
            issues.append({
                "type": "cross_campus_lecturer", "lecturer": info["name"],
                "main_courses": [a.course_code for a in info["main"]],
                "campus_courses": [a.course_code for a in info["campus"]],
                "severity": "warning",
                "message": f"{info['name']} teaches at both campuses. Travel gap enforced.",
            })
        for code, grp in groups.items():
            if grp.is_cross_campus:
                issues.append({
                    "type": "unified_exam_group", "norm_code": code,
                    "main_count": len(grp.main_allocs),
                    "campus_count": len(grp.campus_allocs),
                    "total_students": grp.total_students,
                    "is_mergeable": grp.is_mergeable, "severity": "info",
                    "message": f"{code} unified across {len(grp.all_allocs)} allocs – same slot.",
                })
        return JsonResponse({
            "status": "ok", "issues": issues,
            "cross_campus_lecturers": len(cross),
            "unified_groups": len([g for g in groups.values() if g.is_cross_campus]),
            "merged_groups": len([g for g in groups.values() if g.is_mergeable]),
            "total_allocations": len(main_a) + len(campus_a),
        })