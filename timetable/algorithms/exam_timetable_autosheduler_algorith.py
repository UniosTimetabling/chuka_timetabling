"""
Exam Auto-Scheduler Module (v63 — Family Partial-Together Placement + Correct Stem-Aware Logic)
CRITICAL FIXES in v63:
─────────────────────────────────────────────────────────────────
FAMILY PARTIAL-TOGETHER PLACEMENT (v63):
When a family fails joint placement (can't fit ALL variants in one venue),
Phase 3b now tries to place as many variants as possible TOGETHER at the
same time before falling back to individual placement. This ensures courses
like ZOOL 443 (across Biological Sciences and Physical Sciences stems) are
scheduled together when possible, even if not all variants can fit.

CORRECT STEM-AWARE COLLISION LOGIC (from v62):
exam_is_collision_exempt() correctly handles:
  1. Same course code across different stems/programs → NOT exempt
     (must be scheduled together as a family)
  2. Different course codes from different stems → exempt
     (can be scheduled at same time, e.g. BOTA 474 vs ZOOL 443)

IMMEDIATE FAMILY EXHAUSTION (from v61):
When a family fails joint placement in Phase 3b, it is immediately
marked as exhausted BEFORE attempting individual placement.

STEM-AWARE DAILY LIMITS:
_daily_limit_key() uses program+year+stem so each stem gets its own
daily quota, matching how students are actually distributed.
"""
from __future__ import annotations
import datetime
import re
import logging
import threading
import inspect
import time
from pathlib import Path
from collections import defaultdict
from functools import lru_cache
from typing import Dict, FrozenSet, List, Optional, Set, Tuple, Any

scheduler_logger = logging.getLogger("scheduler")

from django.db import IntegrityError, transaction
from django.db import connection as _db_connection
from timetable.models import (
    ExamSchedulerConfig,
    ExamTempTimetable,
    MergedCourseGroup,
    SharedVenueExamGroup,
)
from course_allocation.models import CourseAllocation, CombinedCourseGroup, AllocationSet
from course_allocation.allocation_scope import tt_scope_q
from django.db.models import Q as _AllocSetQ
from room_management.models import Venue
from core import scheduling_constraints as constraint_engine

DEBUG_VERBOSE: bool = False

# --- Diagnostic tracer -------------------------------------------------
# Prints which function actually committed a placement for any course
# code listed here, so we stop guessing which code path handles a given
# course and can read it straight off the run log instead.
# Set to an empty set (or leave DEBUG_VERBOSE off) to disable — this has
# no effect on scheduling behavior either way, it only prints.
TRACE_COURSE_CODES: Set[str] = {
    "COMS 0100", "DTHM 0231", "DTHM 0121", "DTHM 0222",
    "DTHM 0221", "DTHM 0122", "DTHM 0111", "DTHM 0244",
    "COSC 0160", "COSC 0172",
}

def _trace_placement(course, venue, date, ss) -> None:
    if not TRACE_COURSE_CODES:
        return
    code = (getattr(course, "course_code", "") or "").strip()
    if code not in TRACE_COURSE_CODES:
        return
    # Walk a few frames up so we see the whole call chain (e.g. which
    # scheduling phase called into place_single/_commit_single_venue),
    # not just the function that called _trace_placement itself.
    frames = inspect.stack()[1:7]
    chain = " <- ".join(f.function for f in frames)
    vcode = getattr(venue, "code", venue)
    print(f"[TRACE] {code} -> venue={vcode} date={date} slot={ss} via: {chain}")

_log_file_handle = None
_log_file_path = None
_log_session_id = None
_log_lock = threading.Lock()
_builtin_print = print

def init_logger():
    global _log_file_handle, _log_file_path, _log_session_id
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _log_session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_file_path = log_dir / f"exam_scheduler_{_log_session_id}.txt"
    _log_file_handle = open(_log_file_path, 'w', encoding='utf-8')
    _write_log("=" * 80)
    _write_log("EXAM AUTO-SCHEDULER LOG v63")
    _write_log(f"Session: {_log_session_id}")
    _write_log(f"Started: {datetime.datetime.now().isoformat()}")
    _write_log("=" * 80)
    _write_log("")

def _write_log(message):
    global _log_file_handle
    if _log_file_handle:
        with _log_lock:
            try:
                _log_file_handle.write(str(message) + "\n")
                _log_file_handle.flush()
            except Exception:
                pass

def print(*args, **kwargs):
    sep = kwargs.get('sep', ' ')
    _write_log(sep.join(str(a) for a in args))
    _builtin_print(*args, **kwargs)

def close_logger():
    global _log_file_handle
    if _log_file_handle:
        try:
            _write_log("")
            _write_log(f"Session ended: {datetime.datetime.now().isoformat()}")
            _write_log("=" * 80)
            _log_file_handle.close()
        except Exception:
            pass
        _log_file_handle = None

def get_log_file_path():
    return str(_log_file_path) if _log_file_path else None

def get_log_session_id():
    return _log_session_id

# ======================================================================
# SECTION 1 – Utility helpers
# ======================================================================

def enable_wal_mode():
    engine = _db_connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine.lower():
        try:
            with _db_connection.cursor() as c:
                c.execute("PRAGMA journal_mode=WAL;")
                c.execute("PRAGMA synchronous=NORMAL;")
                c.execute("PRAGMA cache_size=-32000;")
                c.execute("PRAGMA temp_store=MEMORY;")
        except Exception as e:
            print(f"WAL mode note: {e}")

def generate_slots(start_time, end_time, slot_size_hours):
    slots = []
    today = datetime.date.today()
    current = datetime.datetime.combine(today, start_time)
    end = datetime.datetime.combine(today, end_time)
    delta = datetime.timedelta(hours=slot_size_hours)
    gap = datetime.timedelta(minutes=60)
    while current + delta <= end:
        slot_end = current + delta
        slots.append((current.time(), slot_end.time()))
        current = slot_end + gap
    return slots

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
    s = re.sub(r"[-_.]", " ", s)
    m = re.search(r"([A-Z]+)\s*(\d+)", s)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return re.sub(r"\s+", " ", s)

def get_program_year(course_allocation) -> str:
    pc = getattr(course_allocation, "program_course", None)
    if pc is not None:
        year_val = getattr(pc, "year", None)
        if year_val and 1 <= int(year_val) <= 6:
            return f"year_{int(year_val)}"
    code = str(getattr(course_allocation, "course_code", "") or "").strip()
    if code:
        m = re.search(r'\d{3,4}', code)
        if m:
            first_digit = int(m.group(0)[0])
            if 1 <= first_digit <= 6:
                return f"year_{first_digit}"
    return "unknown"

def course_student_count(course) -> int:
    return max(int(getattr(course, "number_of_students", 0) or 0), 1)

def family_total_students(group_courses: List) -> int:
    return sum(course_student_count(c) for c in group_courses)

def prog_year_key(course) -> str:
    prog = getattr(course, "program", None)
    if not prog:
        return ""
    return f"{prog.id}_{get_program_year(course)}"

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

def _exam_get_intake(alloc) -> str:
    """Distinguish a Special Intake cohort from the normal one so they are
    never treated as the same students just because they share
    program+year. Each SpecialIntakeGroup is a genuinely different set of
    physical students (own entry_year/semester), so it gets its own key;
    two different special groups are therefore also exempt from each
    other, not just from the normal cohort.
    Uses the _id shadow attribute so this never forces an extra query when
    special_intake_group hasn't been select_related'd."""
    special_group_id = getattr(alloc, "special_intake_group_id", None)
    if special_group_id:
        return f"special_{special_group_id}"
    return "normal"

def _exam_get_student_group_id(alloc) -> Optional[int]:
    return getattr(alloc, 'student_group_id', None)

def _combined_group_are_paired(alloc_a_id: int, alloc_b_id: int) -> bool:
    a_groups = _combined_group_ids_for(alloc_a_id)
    if not a_groups:
        return False
    b_groups = _combined_group_ids_for(alloc_b_id)
    return bool(a_groups & b_groups)

_combined_group_cache: dict[int, frozenset] = {}

def _build_combined_group_cache() -> dict[str, list[int]]:
    global _combined_group_cache
    _combined_group_cache = {}
    combined_families: dict[str, list[int]] = {}
    try:
        groups = list(
            CombinedCourseGroup.objects
            .prefetch_related('allocations')
            .select_related('primary_allocation')
        )
    except Exception as exc:
        print(f"[CombinedGroup] WARNING: could not load CombinedCourseGroup: {exc}")
        return {}
    for group in groups:
        alloc_ids = list(group.allocations.values_list('id', flat=True))
        gk = frozenset([group.id])
        for aid in alloc_ids:
            if aid in _combined_group_cache:
                _combined_group_cache[aid] = _combined_group_cache[aid] | gk
            else:
                _combined_group_cache[aid] = gk
        key = f"combined{group.group_code}"
        combined_families[key] = alloc_ids
    print(f"[CombinedGroup] Loaded {len(groups)} combined groups")
    return combined_families

def _combined_group_ids_for(alloc_id: int) -> frozenset:
    return _combined_group_cache.get(alloc_id, frozenset())

def exam_is_collision_exempt(c1, c2) -> bool:
    """
    CRITICAL (v63): Correct stem-aware collision logic.

    Rules (in priority order):
    1. Different intakes → exempt (different student populations)
    2. Same normalized course code → NOT exempt (must be scheduled
       together as a family, even across different stems/programs)
    3. Same stem → NOT exempt (same cohort shares students)
    4. Different stems with different course codes → exempt
       (different cohorts, no shared students)
    5. Different explicit student groups → exempt
    6. Same selection group → exempt
    7. Combined group pairing → exempt
    """
    # Different intakes are always exempt
    if _exam_get_intake(c1) != _exam_get_intake(c2):
        return True

    # CRITICAL: Check same course code FIRST — same codes must always
    # be scheduled together regardless of stem/program differences
    nc1 = normalize_course_code(getattr(c1, "course_code", "") or "")
    nc2 = normalize_course_code(getattr(c2, "course_code", "") or "")
    if nc1 and nc2 and nc1 == nc2:
        # Same course code → NOT exempt (must be scheduled together)
        return False

    # Now check stems for different course codes
    st1 = _exam_get_specialization_stem_id(c1)
    st2 = _exam_get_specialization_stem_id(c2)

    # Same stem → NOT exempt (they share students)
    if st1 is not None and st2 is not None and st1 == st2:
        return False

    # Different stems with different course codes → exempt
    if st1 is not None and st2 is not None and st1 != st2:
        return True

    # StudentGroup: different explicit groups within the same program/year
    stg1 = _exam_get_student_group_id(c1)
    stg2 = _exam_get_student_group_id(c2)
    if stg1 is not None and stg2 is not None and stg1 != stg2:
        return True

    sg1 = _exam_get_selection_group_id(c1)
    sg2 = _exam_get_selection_group_id(c2)
    if sg1 is not None and sg2 is not None and sg1 == sg2:
        return True

    if _combined_group_are_paired(c1.id, c2.id):
        return True

    return False

def courses_share_students(c1, c2) -> bool:
    p1 = getattr(c1, "program", None)
    p2 = getattr(c2, "program", None)
    if not p1 or not p2:
        return False
    if p1.id != p2.id:
        return False
    if get_program_year(c1) != get_program_year(c2):
        return False
    if exam_is_collision_exempt(c1, c2):
        return False
    if _combined_group_are_paired(c1.id, c2.id):
        return False
    return True

def _same_exam_group(course_a, course_b) -> bool:
    if course_a.id == course_b.id:
        return True
    nc_a = normalize_course_code(getattr(course_a, "course_code", "") or "")
    nc_b = normalize_course_code(getattr(course_b, "course_code", "") or "")
    if nc_a and nc_b and nc_a == nc_b:
        return True
    if _combined_group_are_paired(course_a.id, course_b.id):
        return True
    return False

def get_exam_group_key(course) -> str:
    nc = normalize_course_code(getattr(course, "course_code", "") or "")
    combined_groups = _combined_group_ids_for(course.id)
    if combined_groups:
        return f"combined_{next(iter(combined_groups))}"
    elif nc:
        return f"family_{nc}"
    else:
        return f"single_{course.id}"

def _min_slots_for_cohort(courses: List) -> int:
    remaining = list(courses)
    slots_needed = 0
    while remaining:
        slots_needed += 1
        bucket: List = []
        still_remaining: List = []
        for c in remaining:
            if all(exam_is_collision_exempt(c, b) for b in bucket):
                bucket.append(c)
            else:
                still_remaining.append(c)
        remaining = still_remaining
    return slots_needed

CONSECUTIVE_GAP_SLOTS = 1

# Venue exam-capacity below this is considered "small". Shared by
# schedule_small_courses_in_small_venues() and find_best_venue_no_split()
# so both agree on what counts as a small room when deciding where to
# consolidate small exams (see SECTION 6 for the strategic ordering).
SMALL_VENUE_CAP_THRESHOLD = 100

# ======================================================================
# SECTION 2 – Data Analysis
# ======================================================================

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
        self.shared_exams: Dict[str, int] = {}
        self.course_program_count: Dict[int, int] = {}

    def log(self, msg: str):
        self.analysis_log.append(msg)
        print(f"[DataAnalysis] {msg}")

def detect_and_deduplicate_courses(all_courses: List, report: DataAnalysisReport) -> List:
    seen_ids: Set[int] = set()
    deduplicated: List = []
    for c in all_courses:
        if c.id in seen_ids:
            report.duplicate_allocation_ids.add(c.id)
            report.log(f"  DUPLICATE: course_allocation_id={c.id} — skipped")
        else:
            seen_ids.add(c.id)
            deduplicated.append(c)
    return deduplicated

def analyze_courses(all_courses: List) -> DataAnalysisReport:
    report = DataAnalysisReport()
    if not all_courses:
        return report

    all_courses = detect_and_deduplicate_courses(all_courses, report)
    report.log(f"Analysing {len(all_courses)} courses...")

    by_norm: Dict[str, List] = defaultdict(list)
    for c in all_courses:
        raw_code = getattr(c, "course_code", "") or ""
        norm_code = normalize_course_code(raw_code)
        by_norm[norm_code].append(c)

    for norm_code, group in by_norm.items():
        programs = set()
        for c in group:
            prog = getattr(c, "program", None)
            if prog:
                programs.add(prog.id)
        report.shared_exams[norm_code] = len(programs)
        for c in group:
            report.course_program_count[c.id] = len(programs)

    # CRITICAL (v63): Group by normalized course code — same codes are
    # ALWAYS grouped together as a family, regardless of stems/programs.
    for norm_code, group in by_norm.items():
        if len(group) < 2:
            continue
        true_total_students = family_total_students(group)
        report.family_total_students[norm_code] = true_total_students
        report.shared_unit_groups[norm_code] = [c.id for c in group]

    # Combined groups
    combined_families = _build_combined_group_cache()
    for combined_key, cids in combined_families.items():
        valid_ids = [cid for cid in cids if any(c.id == cid for c in all_courses)]
        if len(valid_ids) >= 2:
            report.shared_unit_groups[combined_key] = valid_ids
            n_combined = sum(
                course_student_count(c)
                for c in all_courses if c.id in set(valid_ids)
            )
            report.family_total_students[combined_key] = n_combined
            report.shared_exams[combined_key] = len(valid_ids)

    cohort_counts: Dict[str, int] = defaultdict(int)
    for c in all_courses:
        pk = prog_year_key(c)
        if pk:
            cohort_counts[pk] += 1
        report.courses_by_py[pk].append(c)
        prog = getattr(c, "program", None)
        if prog:
            report.courses_by_program[str(prog.id)].append(c)

    report.cohort_course_counts = dict(cohort_counts)

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

    for c in all_courses:
        report.conflict_degree[c.id] = len(report.cohort_conflict_graph.get(c.id, set()))

    report.total_cohort_slot_demand = max(cohort_counts.values()) if cohort_counts else 0
    report.log(f"Found {len(report.shared_unit_groups)} families, {len(cohort_counts)} cohorts")

    return report

# ======================================================================
# SECTION 3 – SchedulerState
# ======================================================================

class SchedulerState:
    def __init__(self, config, analysis: DataAnalysisReport,
                 strategy=None,
                 disabled_constraints: Optional[Set[str]] = None):
        self.config = config
        self.analysis = analysis
        self.strategy = strategy
        self.disabled_constraints: Set[str] = disabled_constraints or set()

        _raw_venues: List = list(
            Venue.objects.filter(capacity__isnull=False, capacity__gt=0)
        )
        blocked_venue_ids = constraint_engine.get_blocked_venue_ids(
            self.disabled_constraints, scheduler_type="exam"
        )
        if blocked_venue_ids:
            _raw_venues = [v for v in _raw_venues if v.id not in blocked_venue_ids]

        exclusive_venue_ids = constraint_engine.get_exclusive_venue_ids(
            self.disabled_constraints, scheduler_type="exam"
        )
        self.exclusive_venue_ids: Set[int] = set(exclusive_venue_ids)
        _general_pool = [v for v in _raw_venues if v.id not in self.exclusive_venue_ids]

        self.designated_venues_by_norm_code: Dict[str, List[int]] = defaultdict(list)
        self.designated_scope_by_norm_code: Dict[str, List[Tuple[Set[int], Set[int], Set[int]]]] = defaultdict(list)
        self.strict_norm_codes: Set[str] = set()
        self.strict_locked_ids: Set[int] = set()
        self.allow_strict_rescue: bool = False

        for rule in constraint_engine.get_designated_venue_rules(
            self.disabled_constraints, scheduler_type="exam"
        ):
            rule_venue_ids = set(rule["venue_ids"])
            rule_program_ids = set(rule.get("program_ids") or ())
            rule_course_ids = set(rule.get("course_ids") or ())
            for raw_code in rule["codes"]:
                nc = normalize_course_code(raw_code)
                if not nc:
                    continue
                bucket = self.designated_venues_by_norm_code[nc]
                for vid in rule["venue_ids"]:
                    if vid not in bucket:
                        bucket.append(vid)
                self.designated_scope_by_norm_code[nc].append(
                    (rule_venue_ids, rule_program_ids, rule_course_ids)
                )
                if rule["strict"]:
                    self.strict_norm_codes.add(nc)

        self._spacing_ratio: float = float(getattr(config, "spacing_ratio", 1.0))
        self.venue_examcap: Dict[int, int] = {
            v.id: venue_exam_capacity(v, self._spacing_ratio) for v in _raw_venues
        }
        self.venues: List = sorted(
            _general_pool,
            key=lambda v: self.venue_examcap.get(v.id, 0),
            reverse=True,
        )
        self.venues_by_cap_desc: List = self.venues
        self.venue_by_id: Dict[int, Venue] = {v.id: v for v in _raw_venues}

        self.highly_shared_exams: Set[str] = set()
        for norm_code, program_count in analysis.shared_exams.items():
            if program_count >= 3:
                self.highly_shared_exams.add(norm_code)

        self.venue_usage: Dict[Tuple, int] = defaultdict(int)
        self.venue_occupants: Dict[Tuple, List[Tuple[int, int]]] = defaultdict(list)
        self._combined_group_venue: Dict[Tuple, int] = {}
        self._family_building: Dict[Tuple, str] = {}
        self.cross_building_splits: List[dict] = []
        self.lecturer_busy: Dict[int, Set] = defaultdict(set)
        self.lecturer_exam_group: Dict[Tuple, str] = {}
        self.lecturer_blocked: Dict = {}
        self._py_busy: Dict[Tuple, Set[str]] = defaultdict(set)
        self._py_busy_allocs: Dict[Tuple, List] = {}
        self.family_slot: Dict[str, Tuple] = {}
        self.family_day: Dict[str, datetime.date] = {}
        self.shared_unit_lock: Dict[str, Tuple] = {}
        self.family_exhausted: Set[str] = set()
        self.norm_code_day_lock: Dict[str, datetime.date] = {}
        self._cross_cohort_norm_codes: Set[str] = set()
        self.cohort_last_slot_idx: Dict[Tuple, int] = {}
        self.cohort_daily_count: Dict[Tuple, int] = defaultdict(int)
        self.daily_load: Dict[datetime.date, int] = defaultdict(int)
        self.lecturer_daily_count: Dict[Tuple, int] = defaultdict(int)
        self.cohort_daily_limits: Dict[str, Tuple[int, int]] = {}
        self.lecturer_daily_limits: Dict[int, Tuple[int, int]] = {}

        self.date_range = self._build_date_range()
        self.slots = generate_slots(config.start_time, config.end_time, config.slot_size)
        self.morning_slots = [(s, e) for s, e in self.slots if s < datetime.time(12, 0)]
        self.afternoon_slots = [(s, e) for s, e in self.slots if datetime.time(12, 0) <= s < datetime.time(17, 0)]
        self.evening_slots = [(s, e) for s, e in self.slots if is_evening_slot(s)]
        self.daytime_slots_list = self.morning_slots + self.afternoon_slots
        self.all_slots_ordered = self.daytime_slots_list + self.evening_slots
        self._slot_start_to_idx: Dict[datetime.time, int] = {
            ss: idx for idx, (ss, _) in enumerate(self.all_slots_ordered)
        }

        self._fk_cache: Dict[int, str] = {}
        self._py_cache: Dict[int, str] = {}
        self._daily_key_cache: Dict[int, str] = {}
        self._norm_code_cache: Dict[int, str] = {}
        self._lecturer_id_cache: Dict[int, Optional[int]] = {}
        self._priority_score_cache: Dict[int, float] = {}
        self.placed_families: Set[str] = set()

        self._compute_daily_limits(analysis)

        self._sorted_dates = sorted(self.date_range, key=lambda dt: dt[0])
        self.lecturer_blocked = self._build_lecturer_blocked_map()

        total_exam = sum(self.venue_examcap.get(v.id, 0) for v in self.venues)
        self._venue_avail: Dict[Tuple, int] = {}
        self._day_slot_total: Dict[Tuple, int] = {}
        self._day_any_cap: Dict[datetime.date, bool] = {}
        for date_obj, _ in self.date_range:
            self._day_any_cap[date_obj] = True
            for ss, _ in self.all_slots_ordered:
                self._day_slot_total[(date_obj, ss)] = total_exam
                for v in self.venues:
                    self._venue_avail[(v.id, date_obj, ss)] = self.venue_examcap[v.id]

        print(f"[SchedulerState] {len(self.venues)} venues, {len(self.date_range)} days")

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
            prog = getattr(course, "program", None)
            if prog:
                year = get_program_year(course)
                self._py_cache[cid] = f"{prog.id}_{year}"
            else:
                self._py_cache[cid] = ""
        return self._py_cache[cid]

    def _daily_limit_key(self, course) -> str:
        cid = course.id
        cached = self._daily_key_cache.get(cid)
        if cached is not None:
            return cached
        pk = self._py_key(course)
        stem_id = _exam_get_specialization_stem_id(course)
        key = f"{pk}_stem{stem_id}" if (pk and stem_id is not None) else pk
        intake = _exam_get_intake(course)
        if intake != "normal":
            # Give each Special Intake cohort its own daily quota bucket —
            # otherwise its exams silently eat into the Normal cohort's
            # per-day limit (and vice versa) even though they're different
            # physical students.
            key = f"{key}_{intake}"
        self._daily_key_cache[cid] = key
        return key

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

    def _build_lecturer_blocked_map(self) -> Dict:
        raw = constraint_engine.get_lecturer_blocked_ranges(
            self.disabled_constraints, scheduler_type="exam"
        )
        if not raw:
            return {}
        dates_by_weekday: Dict[str, List[datetime.date]] = defaultdict(list)
        for date_obj, weekday_name in self.date_range:
            dates_by_weekday[weekday_name].append(date_obj)
        blocked_map: Dict[int, Dict] = {}
        for lecturer_id, ranges in raw.items():
            date_map = blocked_map.setdefault(lecturer_id, {})
            for weekday_name, start, end in ranges:
                for date_obj in dates_by_weekday.get(weekday_name, []):
                    if date_map.get(date_obj) == 'ALL_DAY':
                        continue
                    if start is None or end is None:
                        date_map[date_obj] = 'ALL_DAY'
                        continue
                    idx_set = date_map.get(date_obj)
                    if not isinstance(idx_set, set):
                        idx_set = set()
                        date_map[date_obj] = idx_set
                    for s, e in self.slots:
                        if s < end and start < e:
                            idx_set.add(s)
        return blocked_map

    def lecturer_available(self, lid, date, slot_start, course=None) -> bool:
        if not lid:
            return True
        if (date, slot_start) not in self.lecturer_busy[lid]:
            return True
        if course is None:
            return False
        current_exam_group = self.lecturer_exam_group.get((lid, date, slot_start))
        if current_exam_group is None:
            return False
        course_exam_group = get_exam_group_key(course)
        return course_exam_group == current_exam_group

    def mark_lecturer_busy(self, lid, date, slot_start, course=None):
        if not lid:
            return
        self.lecturer_busy[lid].add((date, slot_start))
        if course:
            self.lecturer_exam_group[(lid, date, slot_start)] = get_exam_group_key(course)

    def family_key(self, course) -> str:
        cid = course.id
        if cid not in self._fk_cache:
            nc = self._norm_code(course)
            py = self._py_key(course)
            self._fk_cache[cid] = f"{nc}|{py}" if py else nc
        return self._fk_cache[cid]

    def bind_family(self, fk: str, date, slot_start):
        if fk and fk not in self.family_slot:
            self.family_slot[fk] = (date, slot_start)
            self.family_day[fk] = date

    def check_family_conflict(self, course, date, slot_start) -> bool:
        nc = self._norm_code(course)
        if nc in self.family_exhausted:
            return False
        fk = self.family_key(course)
        locked = self.family_slot.get(fk)
        if locked is None:
            return False
        return locked != (date, slot_start)

    def release_family_binding(self, course):
        fk = self.family_key(course)
        nc = self._norm_code(course)
        if fk in self.family_slot:
            locked_date, locked_slot = self.family_slot[fk]
            try:
                already_placed = ExamTempTimetable.objects.filter(
                    date=locked_date, start_time=locked_slot,
                ).values_list("course_allocation_id", flat=True)
                for aid in already_placed:
                    if self._fk_cache.get(aid) == fk:
                        return
            except Exception:
                pass
        self.family_slot.pop(fk, None)
        self.family_day.pop(fk, None)
        if nc:
            self.norm_code_day_lock.pop(nc, None)
            self.shared_unit_lock.pop(nc, None)

    def get_shared_unit_lock(self, course) -> Optional[Tuple]:
        nc = self._norm_code(course)
        if nc not in self._cross_cohort_norm_codes:
            return None
        return self.shared_unit_lock.get(nc)

    def bind_shared_unit(self, course, date, slot_start):
        nc = self._norm_code(course)
        if nc in self.family_exhausted:
            return
        if nc and nc in self._cross_cohort_norm_codes and nc not in self.shared_unit_lock:
            self.shared_unit_lock[nc] = (date, slot_start)

    def check_shared_unit_conflict(self, course, date, slot_start) -> bool:
        nc = self._norm_code(course)
        if nc in self.family_exhausted:
            return False
        locked = self.get_shared_unit_lock(course)
        if locked is None:
            return False
        return locked != (date, slot_start)

    def _norm_code(self, course) -> str:
        cid = course.id
        if cid not in self._norm_code_cache:
            self._norm_code_cache[cid] = normalize_course_code(
                getattr(course, "course_code", "") or ""
            )
        return self._norm_code_cache[cid]

    def _cached_lecturer_id(self, course) -> Optional[int]:
        cid = course.id
        if cid not in self._lecturer_id_cache:
            lec = getattr(course, "lecturer", None)
            self._lecturer_id_cache[cid] = getattr(lec, "id", None) if lec else None
        return self._lecturer_id_cache[cid]

    def designated_venues_for_course(self, course) -> List[int]:
        nc = self._norm_code(course)
        scopes = self.designated_scope_by_norm_code.get(nc)
        if not scopes:
            return []
        prog_id = getattr(getattr(course, "program", None), "id", None)
        pc = getattr(course, "program_course", None)
        pc_id = getattr(pc, "id", None) if pc else None
        result: List[int] = []
        for venue_ids, program_ids, course_ids in scopes:
            eligible = (
                (pc_id is not None and pc_id in course_ids)
                or (prog_id is not None and prog_id in program_ids)
            )
            if eligible:
                for vid in venue_ids:
                    if vid not in result:
                        result.append(vid)
        return result

    def designated_venues_for_family(self, group_courses: List) -> List[int]:
        if not group_courses:
            return []
        common: Optional[Set[int]] = None
        for course in group_courses:
            eligible = set(self.designated_venues_for_course(course))
            common = eligible if common is None else (common & eligible)
            if not common:
                return []
        return list(common) if common else []

    def bind_norm_code_day(self, course, date: datetime.date):
        nc = self._norm_code(course)
        if nc in self.family_exhausted:
            return
        if nc and nc in self._cross_cohort_norm_codes:
            if nc not in self.norm_code_day_lock:
                self.norm_code_day_lock[nc] = date

    def check_norm_code_day_conflict(self, course, date: datetime.date) -> bool:
        nc = self._norm_code(course)
        if not nc or nc not in self._cross_cohort_norm_codes:
            return False
        if nc in self.family_exhausted:
            return False
        locked_day = self.norm_code_day_lock.get(nc)
        if locked_day is None:
            return False
        return locked_day != date

    def venue_remaining(self, vid, date, slot_start) -> int:
        cap = self.venue_examcap.get(vid, 0)
        used = self.venue_usage.get((vid, date, slot_start), 0)
        return max(0, cap - used)

    def venue_can_accommodate(self, vid, date, slot_start, students: int) -> bool:
        return self.venue_remaining(vid, date, slot_start) >= students

    def venue_has_occupants(self, vid, date, slot_start) -> bool:
        return self.venue_usage.get((vid, date, slot_start), 0) > 0

    def get_venue_occupants(self, vid, date, slot_start) -> List[int]:
        return [cid for cid, _ in self.venue_occupants.get((vid, date, slot_start), [])]

    def consume_venue(self, vid, date, slot_start, students: int, course=None) -> bool:
        if students <= 0:
            return True
        cap = self.venue_examcap.get(vid, 0)
        already_used = self.venue_usage.get((vid, date, slot_start), 0)
        if already_used + students > cap:
            return False
        self.venue_usage[(vid, date, slot_start)] = already_used + students
        self._venue_avail[(vid, date, slot_start)] = max(0, cap - already_used - students)
        if course:
            self.venue_occupants[(vid, date, slot_start)].append((course.id, students))
        slot_key = (date, slot_start)
        self._day_slot_total[slot_key] = max(0, self._day_slot_total.get(slot_key, 0) - students)
        return True

    def release_venue(self, vid, date, slot_start, students: int, course=None) -> None:
        """Undo consume_venue's bookkeeping for one course.

        Needed whenever a course's venue is changed AFTER it was placed
        (venue consolidation, swap optimization) without going through
        place_single/try_place_course again. Without this, `state`'s
        venue_usage/venue_occupants/_day_slot_total dicts drift out of
        sync with the DB the moment a post-placement pass moves a course
        directly via `ExamTempTimetable.objects...update(venue_id=...)` —
        the donor venue still looks "full" to `state` even though the DB
        now shows it empty, which makes diagnose_unscheduled_courses (and
        any later placement attempt) misreport genuinely free venues as
        having "no space".
        """
        if students <= 0:
            return
        cap = self.venue_examcap.get(vid, 0)
        already_used = self.venue_usage.get((vid, date, slot_start), 0)
        new_used = max(0, already_used - students)
        self.venue_usage[(vid, date, slot_start)] = new_used
        self._venue_avail[(vid, date, slot_start)] = max(0, cap - new_used)
        if course:
            occupants = self.venue_occupants.get((vid, date, slot_start))
            if occupants:
                for i, (cid, cnt) in enumerate(occupants):
                    if cid == course.id:
                        occupants.pop(i)
                        break
        slot_key = (date, slot_start)
        self._day_slot_total[slot_key] = self._day_slot_total.get(slot_key, 0) + students

    def slot_total_remaining(self, date, slot_start) -> int:
        return self._day_slot_total.get((date, slot_start), 0)

    def audit_slot_vs_db(self, date, slot_start, label: str = "") -> list:
        """Ground-truth check: compare in-memory venue_usage against what
        ExamTempTimetable actually has committed for this exact slot.

        explain_no_venue_space only ever reads venue_usage — it has no idea
        whether that number still matches reality. If some earlier pass
        reserved capacity in `state` (e.g. a family commit that consumed
        total_needed under one course id, per _commit_single_venue) and a
        later pass moved/deleted one of the OTHER courses sharing that
        reservation without a matching release_venue, venue_usage keeps
        counting seats as taken that the DB no longer shows as taken. This
        prints every venue where the two disagree, and by how much, so a
        "genuinely exhausted" verdict can actually be trusted instead of
        assumed.
        """
        db_used: Dict[int, int] = defaultdict(int)
        rows = ExamTempTimetable.objects.filter(
            date=date, start_time=slot_start
        ).select_related("course_allocation")
        for row in rows:
            db_used[row.venue_id] += course_student_count(row.course_allocation)
        mismatches = []
        for v in self.venues:
            state_used = self.venue_usage.get((v.id, date, slot_start), 0)
            real_used = db_used.get(v.id, 0)
            if state_used != real_used:
                cap = self.venue_examcap.get(v.id, 0)
                mismatches.append((v.code, state_used, real_used, state_used - real_used, cap))
        tag = f" {label}" if label else ""
        print(f"  [AUDIT{tag}] {date} {slot_start} — state.venue_usage vs DB, "
              f"{len(mismatches)}/{len(self.venues)} venues disagree")
        if mismatches:
            mismatches.sort(key=lambda x: -abs(x[3]))
            phantom_total = sum(m[3] for m in mismatches if m[3] > 0)
            for code, s, r, diff, cap in mismatches[:15]:
                print(f"    {code} (cap={cap}): state_says_used={s}, db_actually_used={r}, "
                      f"phantom={diff:+d}")
            if phantom_total > 0:
                print(f"    -> {phantom_total} phantom seat(s) locked in state but not in the DB "
                      f"at this slot — that's capacity a real placement attempt would wrongly skip.")
        return mismatches

    def slot_has_any_venue_space(self, date, slot_start, needed: int = 1) -> bool:
        for v in self.venues:
            if self.venue_remaining(v.id, date, slot_start) >= needed:
                return True
        return False

    def slot_has_combined_venue_space(self, date, slot_start, needed: int, course=None) -> bool:
        total = 0
        for v in self.venues:
            rem = self.venue_remaining(v.id, date, slot_start)
            if rem <= 0:
                continue
            if course is not None and self.venue_has_occupants(v.id, date, slot_start):
                if not _can_share_venue(course, v.id, date, slot_start, self):
                    continue
            total += rem
            if total >= needed:
                return True
        return False

    def explain_no_venue_space(self, date, slot_start, needed: int, course=None) -> str:
        """Diagnostic-only companion to slot_has_combined_venue_space.

        When that check returns False, this breaks down WHY: how much
        capacity exists venue-by-venue at this slot regardless of
        compatibility, how much of that is excluded by _can_share_venue
        (occupied by an incompatible course), and how much is genuinely
        unused. This is the difference between "the exam period is
        actually full here" and "something's free but the compatibility
        filter is wrongly excluding it" — two very different bugs that
        look identical from a bare True/False result.
        """
        raw_total = 0
        excluded_incompatible = 0
        excluded_incompatible_seats = 0
        genuinely_free_total = 0
        best_free: Optional[Tuple[str, int]] = None  # (venue_code, rem)
        for v in self.venues:
            rem = self.venue_remaining(v.id, date, slot_start)
            if rem <= 0:
                continue
            raw_total += rem
            if course is not None and self.venue_has_occupants(v.id, date, slot_start):
                if not _can_share_venue(course, v.id, date, slot_start, self):
                    excluded_incompatible += 1
                    excluded_incompatible_seats += rem
                    continue
            genuinely_free_total += rem
            if best_free is None or rem > best_free[1]:
                best_free = (v.code, rem)
        if genuinely_free_total >= needed:
            # Shouldn't happen if this is only called after the check
            # failed, but report it plainly if it does — that itself
            # would indicate the two checks disagree, which is a bug.
            return (f"needed={needed} but genuinely_free_total={genuinely_free_total} "
                    f">= needed — slot_has_combined_venue_space and this diagnostic disagree!")
        if excluded_incompatible_seats > 0 and genuinely_free_total + excluded_incompatible_seats >= needed:
            return (f"needed={needed}, raw_total_remaining={raw_total} across all venues, but "
                    f"{excluded_incompatible} occupied venue(s) holding {excluded_incompatible_seats} "
                    f"compatible-looking seats were excluded by the student-sharing check — "
                    f"genuinely-free total is only {genuinely_free_total}. Worth checking whether "
                    f"those exclusions are real student overlaps or a courses_share_students bug.")
        return (f"needed={needed}, raw_total_remaining={raw_total} across all venues "
                f"(genuinely-free usable={genuinely_free_total}, "
                f"best single free venue={best_free}) — capacity is genuinely exhausted here.")

    def get_free_venues(self, date, slot_start) -> List[Tuple[Venue, int]]:
        result = []
        for v in self.venues_by_cap_desc:
            rem = self.venue_remaining(v.id, date, slot_start)
            if rem > 0:
                result.append((v, rem))
        return result

    def get_completely_free_venues(self, date, slot_start) -> List[Tuple[Venue, int]]:
        result = []
        for v in self.venues_by_cap_desc:
            cap = self.venue_examcap.get(v.id, 0)
            rem = self.venue_remaining(v.id, date, slot_start)
            if rem == cap and rem > 0:
                result.append((v, cap))
        return result

    def day_has_any_capacity(self, date) -> bool:
        return self._day_any_cap.get(date, True)

    def cohort_in_cooling(self, course, date, slot_start, gap: int = CONSECUTIVE_GAP_SLOTS) -> bool:
        pk = self._daily_limit_key(course)
        if not pk:
            return False
        last_idx = self.cohort_last_slot_idx.get((pk, date))
        if last_idx is None:
            return False
        current_idx = self._slot_start_to_idx.get(slot_start)
        if current_idx is None:
            return False
        return abs(current_idx - last_idx) <= gap

    def mark_cohort_scheduled(self, course, date, slot_start):
        pk = self._daily_limit_key(course)
        if not pk:
            return
        idx = self._slot_start_to_idx.get(slot_start)
        if idx is not None:
            self.cohort_last_slot_idx[(pk, date)] = idx
        self.cohort_daily_count[(pk, date)] += 1
        lid = self._cached_lecturer_id(course)
        if lid:
            self.lecturer_daily_count[(lid, date)] += 1

    def record_combined_group_venue(self, course, date, slot_start, venue_id):
        for gid in _combined_group_ids_for(course.id):
            self._combined_group_venue.setdefault((date, slot_start, gid), venue_id)

    def preferred_combined_venue_id(self, course, date, slot_start) -> Optional[int]:
        for gid in _combined_group_ids_for(course.id):
            vid = self._combined_group_venue.get((date, slot_start, gid))
            if vid is not None:
                return vid
        return None

    def record_family_building(self, nc: str, date, slot_start, building: str):
        if nc and building:
            self._family_building.setdefault((date, slot_start, nc), building)

    def preferred_family_building(self, nc: str, date, slot_start) -> Optional[str]:
        return self._family_building.get((date, slot_start, nc))

    def priority_score(self, course) -> float:
        cid = course.id
        if cid in self._priority_score_cache:
            return self._priority_score_cache[cid]
        n_students = course_student_count(course)
        conflict_deg = self.analysis.conflict_degree.get(cid, 0)
        nc = self._norm_code(course)
        is_shared = nc in self.analysis.shared_unit_groups
        cohort_load = self.analysis.cohort_course_counts.get(self._py_key(course), 0)
        cohort_pressure = getattr(self.strategy, 'cohort_pressure', {}).get(self._py_key(course), 0.0)
        if is_shared:
            family_total = self.analysis.family_total_students.get(nc, n_students)
        else:
            family_total = n_students
        program_count = self.analysis.course_program_count.get(cid, 1)
        shared_boost = 200.0 if program_count >= 3 else (100.0 if program_count >= 2 else 0.0)
        score = (
            family_total * 1.0
            + conflict_deg * 50.0
            + (500.0 if is_shared else 0.0)
            + cohort_load * 5.0
            + cohort_pressure * 400.0
            + shared_boost
        )
        self._priority_score_cache[cid] = score
        return score

    def _compute_daily_limits(self, analysis):
        slots_per_day = len(self.all_slots_ordered)
        total_days = max(1, len(self.date_range))
        daily_key_totals: Dict[str, int] = defaultdict(int)
        for pk, courses in analysis.courses_by_py.items():
            for course in courses:
                daily_key_totals[self._daily_limit_key(course)] += 1
        for key, total_courses in daily_key_totals.items():
            soft_limit = 2
            hard_limit = min(3, slots_per_day)
            if total_courses <= 2 * total_days:
                hard_limit = min(2, slots_per_day)
            self.cohort_daily_limits[key] = (soft_limit, hard_limit)
        lecturer_course_counts = defaultdict(int)
        for c_list in analysis.courses_by_py.values():
            for course in c_list:
                lid = self._cached_lecturer_id(course)
                if lid:
                    lecturer_course_counts[lid] += 1
        for lid, count in lecturer_course_counts.items():
            soft_limit = 2
            hard_limit = min(3, slots_per_day)
            if count <= 2 * total_days:
                hard_limit = min(2, slots_per_day)
            self.lecturer_daily_limits[lid] = (soft_limit, hard_limit)

    def is_daily_limit_hard_exceeded(self, course, date) -> bool:
        pk = self._daily_limit_key(course)
        if pk:
            _, hard_limit = self.cohort_daily_limits.get(pk, (2, 3))
            if self.cohort_daily_count.get((pk, date), 0) >= hard_limit:
                return True
        lid = self._cached_lecturer_id(course)
        if lid:
            _, hard_limit = self.lecturer_daily_limits.get(lid, (2, 3))
            if self.lecturer_daily_count.get((lid, date), 0) >= hard_limit:
                return True
        return False

    def get_daily_penalty(self, course, date) -> int:
        penalty = 0
        pk = self._daily_limit_key(course)
        if pk:
            soft_limit, _ = self.cohort_daily_limits.get(pk, (2, 3))
            count = self.cohort_daily_count.get((pk, date), 0)
            if count >= soft_limit:
                penalty += 1000 * (count - soft_limit + 1)
        lid = self._cached_lecturer_id(course)
        if lid:
            soft_limit, _ = self.lecturer_daily_limits.get(lid, (2, 3))
            count = self.lecturer_daily_count.get((lid, date), 0)
            if count >= soft_limit:
                penalty += 1000 * (count - soft_limit + 1)
        return penalty

    def get_sorted_dates(self, course=None) -> List[Tuple[datetime.date, str]]:
        if course is None:
            return self._sorted_dates
        return sorted(self._sorted_dates, key=lambda d: self.get_daily_penalty(course, d[0]))

    def get_sorted_dates_for_pool(self, dates, pool_courses) -> List[Tuple[datetime.date, str]]:
        if not pool_courses:
            return dates
        return sorted(dates, key=lambda d: sum(self.get_daily_penalty(c, d[0]) for c in pool_courses[:5]))

# ======================================================================
# SECTION 4 – Pre-Scheduling Intelligence
# ======================================================================

class SchedulingStrategy:
    NORMAL = "NORMAL"
    COMPACT = "COMPACT"
    DENSE = "DENSE"
    OVERFLOW = "OVERFLOW"

    def __init__(self):
        self.mode = self.NORMAL
        self.seat_pressure = 0.0
        self.total_seat_supply = 0
        self.total_student_demand = 0
        self.high_pressure_cohorts = []
        self.cohort_pressure = {}
        self.overloaded_lecturers = []
        self.multi_cohort_lecturers = []
        self.bottleneck_days = []
        self.use_evening_slots = False
        self.relax_consecutive = False
        self.warnings = []
        self.infeasible_cohorts = []
        self.analysis_log = []

    def log(self, msg: str):
        self.analysis_log.append(msg)
        print(f"[PSI] {msg}")

    def warn(self, msg: str):
        self.warnings.append(msg)
        print(f"[PSI ⚠] {msg}")

    def summary(self) -> str:
        lines = [
            f"══════════════════════════════════════════",
            f"  PRE-SCHEDULING INTELLIGENCE — v63",
            f"══════════════════════════════════════════",
            f"  Strategy Mode  : {self.mode}",
            f"  Seat Pressure  : {self.seat_pressure:.1%}",
            f"  Use Evenings   : {self.use_evening_slots}",
        ]
        if self.high_pressure_cohorts:
            lines.append(f"  High-Pressure Cohorts: {len(self.high_pressure_cohorts)}")
        if self.infeasible_cohorts:
            lines.append(f"  ⛔ INFEASIBLE Cohorts: {len(self.infeasible_cohorts)}")
        return "\n".join(lines)

class PreSchedulingIntelligence:
    def __init__(self, all_courses: List, analysis: DataAnalysisReport, config, venues: List):
        self.courses = all_courses
        self.analysis = analysis
        self.config = config
        self.venues = venues
        self.strategy = SchedulingStrategy()

    def run(self) -> SchedulingStrategy:
        s = self.strategy
        slots = generate_slots(self.config.start_time, self.config.end_time, self.config.slot_size)
        spacing = float(getattr(self.config, "spacing_ratio", 1.0))
        date_range = self._build_date_range()
        n_days = len(date_range)
        daytime = [(ss, se) for ss, se in slots if ss < datetime.time(17, 0)]
        evening = [(ss, se) for ss, se in slots if ss >= datetime.time(17, 0)]
        n_day_slots = len(daytime)
        slot_budget = n_days * n_day_slots
        total_seat_supply = sum(venue_exam_capacity(v, spacing) for v in self.venues)
        total_slot_supply = total_seat_supply * slot_budget
        s.total_seat_supply = total_seat_supply
        cohort_demand = self._compute_cohort_demand()
        total_students = sum(cohort_demand.values())
        s.total_student_demand = total_students
        n_courses = len(self.courses)
        pressure = total_students / max(total_slot_supply, 1)
        s.seat_pressure = pressure
        self._analyze_cohorts(cohort_demand, n_days, n_day_slots, slot_budget, len(evening))
        self._analyze_lecturers(slot_budget)
        self._select_strategy(pressure, n_days, n_day_slots, len(evening))
        print(s.summary())
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
        by_cohort: Dict[str, List] = defaultdict(list)
        for c in self.courses:
            pk = prog_year_key(c)
            if pk:
                by_cohort[pk].append(c)
        return {pk: _min_slots_for_cohort(courses) for pk, courses in by_cohort.items()}

    def _analyze_cohorts(self, cohort_demand: Dict[str, int], n_days: int, n_day_slots: int, slot_budget: int, n_eve_slots: int):
        s = self.strategy
        full_budget = n_days * (n_day_slots + n_eve_slots)
        for pk, n_courses in sorted(cohort_demand.items(), key=lambda x: -x[1]):
            min_slots_needed = n_courses
            ratio = (min_slots_needed / slot_budget) if slot_budget > 0 else 1.0
            s.cohort_pressure[pk] = ratio
            if min_slots_needed > full_budget:
                s.infeasible_cohorts.append(pk)
                s.warn(f"Cohort '{pk}' needs {min_slots_needed} slots but only {full_budget} available")
            elif min_slots_needed > slot_budget:
                s.high_pressure_cohorts.append(pk)
                s.use_evening_slots = True
            elif min_slots_needed >= 0.8 * slot_budget:
                s.high_pressure_cohorts.append(pk)

    def _analyze_lecturers(self, slot_budget: int):
        s = self.strategy
        lec_courses: Dict[int, List] = defaultdict(list)
        for c in self.courses:
            lec = getattr(c, "lecturer", None)
            if lec:
                lec_courses[lec.id].append(c)
        for lid, courses in lec_courses.items():
            if len(courses) > slot_budget:
                s.overloaded_lecturers.append(str(lid))

    def _select_strategy(self, pressure: float, n_days: int, n_day_slots: int, n_eve_slots: int):
        s = self.strategy
        if pressure <= 0.75:
            s.mode = SchedulingStrategy.NORMAL
        elif pressure <= 0.90:
            s.mode = SchedulingStrategy.COMPACT
        elif pressure <= 1.0:
            s.mode = SchedulingStrategy.DENSE
            s.use_evening_slots = True
            s.relax_consecutive = True
        else:
            s.mode = SchedulingStrategy.OVERFLOW
            s.use_evening_slots = True
            s.relax_consecutive = True
        if s.infeasible_cohorts and s.mode != SchedulingStrategy.OVERFLOW:
            s.mode = SchedulingStrategy.DENSE
            s.use_evening_slots = True
            s.relax_consecutive = True

# ======================================================================
# SECTION 5 – VENUE SELECTION - STRICT CAPACITY (NO OVERFLOW)
# ======================================================================

def _venue_building(venue) -> str:
    code = (getattr(venue, "code", None) or "").strip()
    return re.sub(r'[\d\s]+$', '', code).upper()

def _building_subset_for_split(pool, total_needed, preferred_building=None):
    """
    Policy: a split family should stay inside ONE building whenever a
    single building's free/designated venues can hold it; only a
    cross-building split is allowed to reach the caller when no single
    building has enough combined capacity ("unless otherwise").

    pool is a list of [venue, rem, cap] rows. Returns a NEW list of
    (copied) rows restricted to one building, or None if no single
    building suffices — callers should fall back to the full pool in
    that case, which may then legitimately cross buildings.
    """
    groups = defaultdict(list)
    for row in pool:
        groups[_venue_building(row[0])].append(row)
    viable = [(b, rows) for b, rows in groups.items() if sum(r[1] for r in rows) >= total_needed]
    if not viable:
        return None
    if preferred_building:
        for b, rows in viable:
            if b == preferred_building:
                return [list(r) for r in rows]
    # Prefer the building that can satisfy the need with the fewest/largest
    # rooms (biggest single venue first, then biggest total), so we don't
    # favor a building that only clears the bar via many tiny rooms when
    # another building could do it in one or two.
    viable.sort(key=lambda item: (-max(r[1] for r in item[1]), -sum(r[1] for r in item[1])))
    return [list(r) for r in viable[0][1]]

def _find_course_by_id(course_id, state) -> Optional[Any]:
    """Look up a course object by id from the analysis index. Shared by
    _can_share_venue and the same-lecturer consolidation check below so the
    scan logic lives in one place."""
    for c_list in state.analysis.courses_by_py.values():
        for occ in c_list:
            if occ.id == course_id:
                return occ
    return None

def _can_share_venue(course, venue_id, date, slot_start, state) -> bool:
    if not state.venue_has_occupants(venue_id, date, slot_start):
        return True
    occupants = state.get_venue_occupants(venue_id, date, slot_start)
    for occ_course_id in occupants:
        occ_course = _find_course_by_id(occ_course_id, state)
        if occ_course:
            if normalize_course_code(getattr(course, "course_code", "") or "") == \
               normalize_course_code(getattr(occ_course, "course_code", "") or ""):
                continue
            if _combined_group_are_paired(course.id, occ_course.id):
                continue
            if courses_share_students(course, occ_course):
                return False
    return True

def _venue_same_lecturer_occupant(course, venue_id, date, slot_start, state) -> bool:
    """True if the venue already holds an occupant (any course code) taught
    by the SAME lecturer as `course` in this date/slot. Used to consolidate
    a lecturer's exams into fewer venues instead of always grabbing a fresh
    empty room — this frees other venues for courses that actually need
    them and cuts down on splits/collisions elsewhere."""
    lid = state._cached_lecturer_id(course)
    if not lid:
        return False
    if not state.venue_has_occupants(venue_id, date, slot_start):
        return False
    for occ_course_id in state.get_venue_occupants(venue_id, date, slot_start):
        occ_course = _find_course_by_id(occ_course_id, state)
        if occ_course and state._cached_lecturer_id(occ_course) == lid:
            return True
    return False

def _venue_is_small(v, state: SchedulerState) -> bool:
    cap = state.venue_examcap.get(v.id, 0)
    return 0 < cap < SMALL_VENUE_CAP_THRESHOLD

def find_best_venue_no_split(needed: int, date, slot_start, state: SchedulerState,
                             course=None) -> Optional[Venue]:
    """
    Venue selection, in strategic priority order:

      1. Occupied-but-compatible SMALL venues with room (tightest sufficient
         fit first; same-lecturer occupant preferred on ties). Packs more
         small exams onto a small room that's already in use instead of
         opening a fresh one.
      2. Completely free SMALL venues (smallest sufficient capacity first).
         Opens a fresh small room only once no already-occupied small venue
         can take this course.
      3. Occupied-but-compatible LARGE venues with room (tightest sufficient
         fit first; same-lecturer occupant preferred on ties). Only reached
         once every small venue this slot is either full or incompatible.
      4. Completely free venues of any size (smallest sufficient capacity
         first) — final fallback, covers courses too big for any small venue
         or slots where every small venue is already exhausted.

    This keeps small classes consolidating onto small venues first (so a
    handful of ~10-student exams pack into a 25/50-cap room together
    instead of each grabbing its own room while a big lecture hall sits
    half-empty), and only spills into large venues once the slot's small
    venues genuinely can't absorb more. Existing student-conflict /
    combined-group rules in _can_share_venue still apply throughout, so
    this never overrides a real collision — it only chooses among venues
    where sharing is already safe.
    """
    occupied_small: List[Tuple[Venue, int, bool]] = []
    occupied_large: List[Tuple[Venue, int, bool]] = []
    if course:
        for v in state.venues_by_cap_desc:
            rem = state.venue_remaining(v.id, date, slot_start)
            if rem <= 0 or rem < needed:
                continue
            if not state.venue_has_occupants(v.id, date, slot_start):
                continue
            if not _can_share_venue(course, v.id, date, slot_start, state):
                continue
            same_lecturer = _venue_same_lecturer_occupant(course, v.id, date, slot_start, state)
            entry = (v, rem, same_lecturer)
            if _venue_is_small(v, state):
                occupied_small.append(entry)
            else:
                occupied_large.append(entry)

        if occupied_small:
            # tightest sufficient room first; same-lecturer occupant wins ties
            occupied_small.sort(key=lambda x: (x[1], not x[2]))
            return occupied_small[0][0]

    free_venues = state.get_completely_free_venues(date, slot_start)

    free_small = [(v, cap) for v, cap in free_venues if cap >= needed and _venue_is_small(v, state)]
    if free_small:
        free_small.sort(key=lambda x: x[1])
        return free_small[0][0]

    if course and occupied_large:
        occupied_large.sort(key=lambda x: (x[1], not x[2]))
        return occupied_large[0][0]

    free_venues_asc = sorted(free_venues, key=lambda x: x[1])
    for v, cap in free_venues_asc:
        if cap >= needed:
            return v
    return None

def find_minimal_split_venues(needed: int, date, slot_start, state: SchedulerState,
                              course=None) -> List[Venue]:
    if not course:
        return []
    available = []
    for v in state.venues_by_cap_desc:
        cap = state.venue_examcap.get(v.id, 0)
        if cap <= 0:
            continue
        rem = state.venue_remaining(v.id, date, slot_start)
        if rem <= 0:
            continue
        if state.venue_has_occupants(v.id, date, slot_start):
            if not _can_share_venue(course, v.id, date, slot_start, state):
                continue
        available.append((v, rem))
    if not available:
        return []
    available.sort(key=lambda x: -x[1])
    for v, rem in available:
        if rem >= needed:
            return []
    chosen, remaining_needed = [], needed
    for v, rem in available:
        if remaining_needed <= 0:
            break
        take = min(rem, remaining_needed)
        chosen.append(v)
        remaining_needed -= take
    if remaining_needed > 0:
        return []
    return chosen

def place_course_no_split(course, date, slot_start, slot_end,
                          state: SchedulerState, scheduled_ids: Set[int],
                          relax_consecutive=False) -> bool:
    if course.id in scheduled_ids:
        return True
    needed = course_student_count(course)
    reason = _check_hard_constraints(course, date, slot_start, state)
    if reason:
        return False
    if not relax_consecutive and state.cohort_in_cooling(course, date, slot_start):
        return False
    if _course_already_in_db(course):
        scheduled_ids.add(course.id)
        return True
    preferred_vid = state.preferred_combined_venue_id(course, date, slot_start)
    if preferred_vid is not None:
        preferred_venue = state.venue_by_id.get(preferred_vid)
        if preferred_venue is not None and state.venue_remaining(preferred_vid, date, slot_start) >= needed:
            if place_single(course, preferred_venue, date, slot_start, slot_end,
                            state, scheduled_ids, relax_consecutive=relax_consecutive,
                            allow_room_sharing=True, allow_split=False):
                return True
    venue = find_best_venue_no_split(needed, date, slot_start, state, course)
    if venue and place_single(course, venue, date, slot_start, slot_end,
                              state, scheduled_ids, relax_consecutive=relax_consecutive,
                              allow_room_sharing=True, allow_split=False):
        return True
    return False

def place_course_with_minimal_split(course, date, slot_start, slot_end,
                                    state: SchedulerState, scheduled_ids: Set[int],
                                    relax_consecutive=False) -> bool:
    if course.id in scheduled_ids:
        return True
    if place_course_no_split(course, date, slot_start, slot_end, state, scheduled_ids, relax_consecutive):
        return True
    needed = course_student_count(course)
    if _check_hard_constraints(course, date, slot_start, state):
        return False
    if not relax_consecutive and state.cohort_in_cooling(course, date, slot_start):
        return False
    if DEBUG_VERBOSE:
        # Ground-truth check BEFORE we commit to splitting: is a single
        # sufficient venue really unavailable, or does state.venue_usage
        # merely believe it's unavailable? We've already found two separate
        # drift bugs (family occupant-tracking, duplicate-placement audit)
        # that made "exhausted" a lie. Print the real picture every time a
        # course is about to be split, so an over-split can be confirmed as
        # a genuine capacity shortage or traced back to phantom usage
        # instead of assumed.
        single_ok = state.slot_has_combined_venue_space(date, slot_start, 1, course=None)
        best_single = None
        for v in state.venues_by_cap_desc:
            rem = state.venue_remaining(v.id, date, slot_start)
            if rem >= needed:
                best_single = (v.code, rem, state.venue_examcap.get(v.id, 0))
                break
        if best_single:
            print(f"  [Split-Check] {course.course_code} needs {needed} — "
                  f"state says {best_single[0]} alone has {best_single[1]}/{best_single[2]} free "
                  f"but no-split placement still failed (constraint/compatibility, not capacity)")
        else:
            print(f"  [Split-Check] {course.course_code} needs {needed} — "
                  f"{state.explain_no_venue_space(date, slot_start, needed, course)}")
            mismatches = state.audit_slot_vs_db(date, slot_start, label=course.course_code)
            if mismatches:
                print(f"  [Split-Check] ^ {len(mismatches)} venue(s) disagree with DB at this slot — "
                      f"phantom occupancy may be forcing this split")
    venues = find_minimal_split_venues(needed, date, slot_start, state, course)
    if not venues:
        return False
    if place_multi_venue(course, venues, date, slot_start, slot_end, state, scheduled_ids,
                         relax_consecutive=relax_consecutive, allow_room_sharing=True):
        if DEBUG_VERBOSE:
            print(f"  [Split] {course.course_code} ({needed} students) split across {len(venues)} venues")
        return True
    return False

# ======================================================================
# SECTION 6 – Core Placement Primitives
# ======================================================================

_already_scheduled_cache: Set[int] = set()
_bulk_buffer: List[Tuple] = []

def _flush_bulk_buffer(state: SchedulerState, scheduled_ids: Set[int]) -> int:
    global _bulk_buffer
    if not _bulk_buffer:
        return 0
    entries = []
    post_place_items = []
    for course, venue, date, ss, se in _bulk_buffer:
        if course.id in scheduled_ids:
            continue
        entries.append(
            ExamTempTimetable(
                course_allocation=course,
                venue=venue,
                date=date,
                day=date.strftime("%A"),
                start_time=ss,
                end_time=se,
            )
        )
        post_place_items.append((course, venue.id, course_student_count(course), date, ss, se))
    if not entries:
        _bulk_buffer = []
        return 0
    try:
        with transaction.atomic():
            ExamTempTimetable.objects.bulk_create(entries, ignore_conflicts=True)
    except Exception as exc:
        print(f"[BulkFlush] Error: {exc}")
        _bulk_buffer = []
        return 0
    for course, venue_id, students, date, ss, se in post_place_items:
        _post_place(course, venue_id, students, date, ss, se, state, scheduled_ids)
    n = len(post_place_items)
    _bulk_buffer = []
    return n

def _course_already_in_db(course) -> bool:
    if course.id in _already_scheduled_cache:
        return True
    exists = ExamTempTimetable.objects.filter(course_allocation=course).exists()
    if exists:
        _already_scheduled_cache.add(course.id)
        return exists
    return False

def _post_place(course, venue_id: int, students: int,
                date, slot_start, slot_end,
                state: SchedulerState, scheduled_ids: Set[int]) -> bool:
    if students <= 0:
        return False
    if not state.consume_venue(venue_id, date, slot_start, students, course):
        return False
    state.record_combined_group_venue(course, date, slot_start, venue_id)
    state.mark_students_busy(course, date, slot_start)
    lid = state._cached_lecturer_id(course)
    state.mark_lecturer_busy(lid, date, slot_start, course)
    state.bind_family(state.family_key(course), date, slot_start)
    state.bind_shared_unit(course, date, slot_start)
    state.bind_norm_code_day(course, date)
    state.mark_cohort_scheduled(course, date, slot_start)
    state.daily_load[date] += 1
    scheduled_ids.add(course.id)
    _already_scheduled_cache.add(course.id)
    return True

def _check_hard_constraints(course, date, slot_start, state: SchedulerState) -> Optional[str]:
    if course.id in state.strict_locked_ids and not state.allow_strict_rescue:
        return "strict-designated-venue-unavailable"
    if not state.students_available(course, date, slot_start):
        return "student-conflict"
    if state.check_family_conflict(course, date, slot_start):
        return "family-conflict"
    if state.check_shared_unit_conflict(course, date, slot_start):
        return "shared-unit-conflict"
    if state.check_norm_code_day_conflict(course, date):
        return "norm-code-day-conflict"
    lid = state._cached_lecturer_id(course)
    if lid and not state.lecturer_available(lid, date, slot_start, course):
        return "lecturer-conflict"
    if state.is_daily_limit_hard_exceeded(course, date):
        return "daily-limit-hard-exceeded"
    return None

def place_single(course, venue, date, slot_start, slot_end,
                 state: SchedulerState, scheduled_ids: Set[int],
                 relax_consecutive=False, ignore_capacity=False,
                 allow_room_sharing=True, allow_split=False) -> bool:
    if course.id in scheduled_ids:
        return True
    needed = course_student_count(course)
    cap = state.venue_examcap.get(venue.id, 0)
    rem = state.venue_remaining(venue.id, date, slot_start)
    has_occupants = state.venue_has_occupants(venue.id, date, slot_start)
    if not ignore_capacity:
        if rem < needed:
            return False
        if has_occupants and allow_room_sharing:
            if not _can_share_venue(course, venue.id, date, slot_start, state):
                return False
    reason = _check_hard_constraints(course, date, slot_start, state)
    if reason:
        return False
    if not relax_consecutive and state.cohort_in_cooling(course, date, slot_start):
        return False
    if _course_already_in_db(course):
        scheduled_ids.add(course.id)
        return True
    try:
        with transaction.atomic():
            ExamTempTimetable.objects.create(
                course_allocation=course, venue=venue,
                date=date, day=date.strftime("%A"),
                start_time=slot_start, end_time=slot_end,
            )
    except IntegrityError as e:
        code = (getattr(course, "course_code", "") or "").strip()
        if code in TRACE_COURSE_CODES:
            print(f"[TRACE] {code} -> IntegrityError in place_single() at "
                  f"venue={getattr(venue, 'code', venue)} date={date} slot={slot_start}: {e}")
        if ExamTempTimetable.objects.filter(
            course_allocation=course, date=date, start_time=slot_start
        ).exists():
            scheduled_ids.add(course.id)
            return True
        return False
    if not _post_place(course, venue.id, needed, date, slot_start, slot_end, state, scheduled_ids):
        ExamTempTimetable.objects.filter(
            course_allocation=course, date=date, start_time=slot_start
        ).delete()
        return False
    _trace_placement(course, venue, date, slot_start)
    return True

def place_multi_venue(course, venues, date, slot_start, slot_end,
                      state: SchedulerState, scheduled_ids: Set[int],
                      relax_consecutive=False, allow_room_sharing=True) -> bool:
    if course.id in scheduled_ids:
        return True
    needed = course_student_count(course)
    reason = _check_hard_constraints(course, date, slot_start, state)
    if reason:
        return False
    if not relax_consecutive and state.cohort_in_cooling(course, date, slot_start):
        return False
    if _course_already_in_db(course):
        scheduled_ids.add(course.id)
        return True
    assignments, remaining = [], needed
    for v in venues:
        if remaining <= 0:
            break
        cap = state.venue_examcap.get(v.id, 0)
        rem = state.venue_remaining(v.id, date, slot_start)
        if allow_room_sharing and state.venue_has_occupants(v.id, date, slot_start):
            if not _can_share_venue(course, v.id, date, slot_start, state):
                continue
        take = min(rem, remaining)
        if take > 0:
            assignments.append((v, take))
            remaining -= take
    if remaining > 0:
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
        return False
    for v, students in assignments:
        if not state.consume_venue(v.id, date, slot_start, students, course):
            ExamTempTimetable.objects.filter(
                course_allocation=course, date=date, start_time=slot_start
            ).delete()
            return False
        state.record_combined_group_venue(course, date, slot_start, v.id)
        state.mark_students_busy(course, date, slot_start)
        lid = state._cached_lecturer_id(course)
        state.mark_lecturer_busy(lid, date, slot_start, course)
        state.bind_family(state.family_key(course), date, slot_start)
        state.bind_shared_unit(course, date, slot_start)
        state.bind_norm_code_day(course, date)
        state.mark_cohort_scheduled(course, date, slot_start)
        state.daily_load[date] += 1
        scheduled_ids.add(course.id)
        _already_scheduled_cache.add(course.id)
        _trace_placement(course, v, date, slot_start)
    return True

def try_place_course(course, date, slot_start, slot_end,
                     state: SchedulerState, scheduled_ids: Set[int],
                     relax_consecutive=False, allow_split=False,
                     allow_room_sharing=True) -> bool:
    nc = normalize_course_code(course.course_code or "")
    if nc in state.analysis.shared_unit_groups and nc not in state.family_exhausted:
        family_member_ids = state.analysis.shared_unit_groups.get(nc, [])
        placed_family_members = [cid for cid in family_member_ids if cid in scheduled_ids]
        if placed_family_members and len(placed_family_members) < len(family_member_ids):
            return False
    if allow_split:
        return place_course_with_minimal_split(course, date, slot_start, slot_end,
                                               state, scheduled_ids, relax_consecutive)
    else:
        return place_course_no_split(course, date, slot_start, slot_end,
                                     state, scheduled_ids, relax_consecutive)

# ======================================================================
# SECTION 7 – Family Placement
# ======================================================================

def _family_constraints_ok(group_courses, date, slot_start, state):
    for c in group_courses:
        if c.id in state.strict_locked_ids and not state.allow_strict_rescue:
            return False
        if not state.students_available(c, date, slot_start):
            return False
        if state.check_norm_code_day_conflict(c, date):
            return False
        if state.check_shared_unit_conflict(c, date, slot_start):
            return False
        if state.check_family_conflict(c, date, slot_start):
            return False
        lid = state._cached_lecturer_id(c)
        if lid and not state.lecturer_available(lid, date, slot_start, c):
            return False
    return True

def place_merged_family(group_courses, nc, date, ss, se, state, scheduled_ids) -> bool:
    group_courses = [c for c in group_courses if c.id not in scheduled_ids]
    if not group_courses:
        state.placed_families.add(nc)
        return True
    total_needed = family_total_students(group_courses)
    if DEBUG_VERBOSE:
        print(f"\n[Family] '{nc}' | {len(group_courses)} variants | total={total_needed}")
    if not _family_constraints_ok(group_courses, date, ss, state):
        return False
    venue_ids = state.designated_venues_for_family(group_courses)
    if venue_ids:
        is_strict = nc in state.strict_norm_codes
        # "strict" means this family may ONLY use venues from its own
        # designated pool (never spill into the general venue list below) —
        # it does NOT mean the venue must be left solely to this one course.
        # Two designated families can still share a designated room with
        # each other, exactly like non-strict families already can, as long
        # as _can_share_venue confirms there's no real student conflict.
        # Previously "strict" blocked ANY sharing outright, so each small
        # designated course claimed a whole room to itself and the pool got
        # burned through one room per course even when earlier rooms still
        # had plenty of space (e.g. four ~10-student exams each grabbing
        # their own 25/50-cap room instead of packing together).
        #
        # Ordering strategy (matches find_best_venue_no_split): small
        # designated venues with a compatible occupant first (pack in),
        # then small empty designated venues, then large occupied, then
        # large empty — tightest sufficient fit within each bucket.
        candidates = []
        for vid in venue_ids:
            v = state.venue_by_id.get(vid)
            if not v:
                continue
            rem = state.venue_remaining(vid, date, ss)
            if rem < total_needed:
                continue
            occupied = state.venue_has_occupants(vid, date, ss)
            if occupied and not all(
                _can_share_venue(c, vid, date, ss, state) for c in group_courses
            ):
                continue
            candidates.append((v, rem, occupied, _venue_is_small(v, state)))
        candidates.sort(key=lambda x: (not x[3], not x[2], x[1]))
        for v, rem, occupied, is_small in candidates:
            if _commit_single_venue(group_courses, nc, v, total_needed,
                                    date, ss, se, state, scheduled_ids):
                return True

        designated_pool = []
        for vid in venue_ids:
            v = state.venue_by_id.get(vid)
            if not v:
                continue
            rem = state.venue_remaining(vid, date, ss)
            if rem <= 0:
                continue
            if state.venue_has_occupants(vid, date, ss) and not all(
                _can_share_venue(c, vid, date, ss, state) for c in group_courses
            ):
                continue
            designated_pool.append([v, rem, state.venue_examcap.get(vid, 0)])
        if designated_pool and sum(row[1] for row in designated_pool) >= total_needed:
            same_building_pool = _building_subset_for_split(designated_pool, total_needed)
            if same_building_pool and _commit_distributed_minimal(
                    group_courses, nc, same_building_pool, total_needed,
                    date, ss, se, state, scheduled_ids):
                return True
            if _commit_distributed_minimal(group_courses, nc, designated_pool, total_needed,
                                           date, ss, se, state, scheduled_ids):
                return True
        if is_strict and not state.allow_strict_rescue:
            return False
    free_venues = state.get_free_venues(date, ss)
    free_with_caps = []
    for v, rem in free_venues:
        cap = state.venue_examcap.get(v.id, 0)
        occupied = state.venue_has_occupants(v.id, date, ss)
        free_with_caps.append([v, rem, cap, occupied])
    known_building = state.preferred_family_building(nc, date, ss)

    def _sort_key(row):
        v, rem, cap, occupied = row
        is_small = 0 < cap < SMALL_VENUE_CAP_THRESHOLD
        building_match = 0 if (known_building and _venue_building(v) == known_building) else 1
        return (not is_small, not occupied, building_match, rem)

    free_with_caps.sort(key=_sort_key)
    for row in free_with_caps:
        v, remaining, cap, occupied = row
        if remaining >= total_needed:
            # Try the commit; on failure (e.g. a late-detected student
            # conflict) fall through to the next candidate instead of
            # giving up on the whole family — the old code returned
            # immediately on the first sufficiently-large venue even when
            # the commit itself failed.
            if _commit_single_venue(group_courses, nc, v, total_needed,
                                    date, ss, se, state, scheduled_ids):
                return True
    total_free = sum(row[1] for row in free_with_caps)
    if total_free >= total_needed:
        free_with_caps_for_split = [[v, rem, cap] for v, rem, cap, _occ in free_with_caps]
        same_building_pool = _building_subset_for_split(
            free_with_caps_for_split, total_needed, preferred_building=known_building)
        if same_building_pool and _commit_distributed_minimal(
                group_courses, nc, same_building_pool, total_needed,
                date, ss, se, state, scheduled_ids, preferred_building=known_building):
            return True
        if _commit_distributed_minimal(group_courses, nc, free_with_caps_for_split, total_needed,
                                       date, ss, se, state, scheduled_ids,
                                       preferred_building=known_building):
            return True
    return False

def _commit_single_venue(group_courses, nc, venue, total_needed,
                         date, ss, se, state, scheduled_ids):
    cap = state.venue_examcap.get(venue.id, 0)
    remaining = state.venue_remaining(venue.id, date, ss)
    if remaining < total_needed:
        return False
    if state.venue_has_occupants(venue.id, date, ss):
        for course in group_courses:
            if not _can_share_venue(course, venue.id, date, ss, state):
                return False
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
        print(f"  [FAIL] DB commit error: {e}")
        return False
    # Consume per-course, not as one lump under group_courses[0] — every
    # member of the family needs its own venue_occupants entry, or later
    # passes (release_venue, _can_share_venue) can't see/undo that specific
    # course's share of the room. A partial-consume failure partway through
    # is rolled back by releasing whatever this loop already committed.
    consumed_so_far = []
    consume_ok = True
    for c in group_courses:
        students = course_student_count(c)
        if not state.consume_venue(venue.id, date, ss, students, c):
            consume_ok = False
            break
        consumed_so_far.append((c, students))
    if not consume_ok:
        for c, students in consumed_so_far:
            state.release_venue(venue.id, date, ss, students, c)
        ExamTempTimetable.objects.filter(
            course_allocation__in=group_courses, date=date, start_time=ss
        ).delete()
        MergedCourseGroup.objects.filter(
            base_course=group_courses[0], date=date, start_time=ss
        ).delete()
        return False
    for c in group_courses:
        state.record_combined_group_venue(c, date, ss, venue.id)
        state.mark_students_busy(c, date, ss)
        lid = state._cached_lecturer_id(c)
        state.mark_lecturer_busy(lid, date, ss, c)
        state.bind_family(state.family_key(c), date, ss)
        state.bind_shared_unit(c, date, ss)
        state.bind_norm_code_day(c, date)
        state.mark_cohort_scheduled(c, date, ss)
        scheduled_ids.add(c.id)
        _already_scheduled_cache.add(c.id)
        state.daily_load[date] += 1
    state.shared_unit_lock[nc] = (date, ss)
    state.norm_code_day_lock[nc] = date
    state.record_family_building(nc, date, ss, _venue_building(venue))
    if DEBUG_VERBOSE:
        print(f"  [Family] '{nc}' → {venue.code} (cap={cap}, total={total_needed})")
    for c in group_courses:
        _trace_placement(c, venue, date, ss)
    return True

def _commit_distributed_minimal(group_courses, nc, pool, total_needed,
                                date, ss, se, state, scheduled_ids,
                                consume_fn=None, preferred_building=None):
    consume = consume_fn or state.consume_venue
    pool_sorted = sorted(pool, key=lambda row: -row[1])
    if preferred_building:
        same = [r for r in pool_sorted if _venue_building(r[0]) == preferred_building]
        other = [r for r in pool_sorted if _venue_building(r[0]) != preferred_building]
        pool_sorted = same + other
    # Cluster same-labeled variants (identical raw course_code, e.g. "COSC 103-D")
    # together and adjacent in the fill order. The venue-fill loop below walks
    # remaining_courses once per venue, taking from whichever course still needs
    # seats; if same-label variants sit next to each other in this list, they
    # keep landing in the same venue (as long as it fits) instead of being
    # scattered across different rooms just because a same-sized variant of a
    # different label happened to be adjacent in a pure size sort.
    def _variant_label(c):
        return (getattr(c, "course_code", "") or "").strip()

    label_totals = defaultdict(int)
    for c in group_courses:
        label_totals[_variant_label(c)] += course_student_count(c)

    remaining_courses = sorted(
        group_courses,
        key=lambda c: (-label_totals[_variant_label(c)], _variant_label(c), -course_student_count(c))
    )
    course_left = {c.id: course_student_count(c) for c in remaining_courses}
    assignments = []
    for row in pool_sorted:
        venue, rem, cap = row
        if rem <= 0:
            continue
        for course in remaining_courses:
            left = course_left[course.id]
            if left <= 0:
                continue
            if rem <= 0:
                break
            take = min(rem, left)
            assignments.append((course, venue, take))
            course_left[course.id] -= take
            rem -= take
        row[1] = rem
    if any(v > 0 for v in course_left.values()):
        return False
    entries = []
    for course, venue, seats in assignments:
        entries.append(ExamTempTimetable(
            course_allocation=course,
            venue=venue,
            date=date,
            day=date.strftime("%A"),
            start_time=ss,
            end_time=se,
        ))
    primary_venue = max(assignments, key=lambda a: state.venue_examcap.get(a[1].id, 0))[1]
    try:
        with transaction.atomic():
            ExamTempTimetable.objects.bulk_create(entries, ignore_conflicts=True)
            merged = MergedCourseGroup.objects.create(
                base_course=group_courses[0],
                merged_code=nc,
                total_students=total_needed,
                date=date, start_time=ss, end_time=se,
                venue=primary_venue,
            )
            merged.merged_courses.set(group_courses)
            state.placed_families.add(nc)
    except Exception as e:
        print(f"  [FAIL] DB commit error: {e}")
        return False
    for course, venue, seats in assignments:
        if not consume(venue.id, date, ss, seats, course):
            ExamTempTimetable.objects.filter(
                course_allocation__in=group_courses, date=date, start_time=ss
            ).delete()
            MergedCourseGroup.objects.filter(
                base_course=group_courses[0], date=date, start_time=ss
            ).delete()
            return False
        state.record_combined_group_venue(course, date, ss, venue.id)
        state.record_family_building(nc, date, ss, _venue_building(venue))
    buildings_used = sorted({_venue_building(venue) for _course, venue, _seats in assignments})
    if len(buildings_used) > 1:
        detail = {
            "family": nc, "date": date, "slot": ss,
            "buildings": buildings_used,
            "venues": [f"{v.code} ({s} students)" for _c, v, s in assignments],
        }
        state.cross_building_splits.append(detail)
        print(f"  [POLICY-WARN] '{nc}' on {date} {ss} split across buildings "
              f"{buildings_used}: {detail['venues']} — no single building had "
              f"enough free capacity ({total_needed} needed).")
    for course in group_courses:
        state.mark_students_busy(course, date, ss)
        lid = state._cached_lecturer_id(course)
        state.mark_lecturer_busy(lid, date, ss, course)
        state.bind_family(state.family_key(course), date, ss)
        state.bind_shared_unit(course, date, ss)
        state.bind_norm_code_day(course, date)
        state.mark_cohort_scheduled(course, date, ss)
        scheduled_ids.add(course.id)
        _already_scheduled_cache.add(course.id)
    state.daily_load[date] += 1
    state.shared_unit_lock[nc] = (date, ss)
    state.norm_code_day_lock[nc] = date
    if DEBUG_VERBOSE:
        venues_used = {a[1].code: a[2] for a in assignments}
        print(f"  [Family-Split] '{nc}' → {len(set(a[1].id for a in assignments))} venues: {venues_used}")
    for course, venue, seats in assignments:
        _trace_placement(course, venue, date, ss)
    return True

# ======================================================================
# SECTION 8 – Scheduling Phases
# ======================================================================

def schedule_small_courses_in_small_venues(all_courses, state, scheduled_ids):
    SMALL_STUDENT_THRESHOLD = 100
    SMALL_VENUE_THRESHOLD = SMALL_VENUE_CAP_THRESHOLD  # shared with find_best_venue_no_split
    py_total_counts = defaultdict(int)
    for c in all_courses:
        pk = state._py_key(c)
        if pk:
            py_total_counts[pk] += 1
    small_courses = [
        c for c in all_courses
        if c.id not in scheduled_ids
        and course_student_count(c) < SMALL_STUDENT_THRESHOLD
        and normalize_course_code(getattr(c, "course_code", "") or "") not in state.analysis.shared_unit_groups
    ]
    small_courses.sort(key=lambda c: course_student_count(c))
    small_venues = [
        v for v in state.venues
        if state.venue_examcap.get(v.id, 0) < SMALL_VENUE_THRESHOLD and state.venue_examcap.get(v.id, 0) > 0
    ]
    small_venues.sort(key=lambda v: state.venue_examcap.get(v.id, 0))
    if not small_courses or not small_venues:
        return 0
    dates = state.dates_in_order()
    sorted_dates = state.get_sorted_dates_for_pool(dates, small_courses)
    target_slots = state.all_slots_ordered[:2] if len(state.all_slots_ordered) >= 2 else state.all_slots_ordered
    placed_total = 0
    print(f"\n[PhaseA-SmallFirst] {len(small_courses)} small courses, {len(small_venues)} small venues")
    for date_obj, _ in sorted_dates:
        small_courses = [c for c in small_courses if c.id not in scheduled_ids]
        if not small_courses:
            break
        py_day_count = defaultdict(int)
        for ss, se in target_slots:
            small_courses = [c for c in small_courses if c.id not in scheduled_ids]
            if not small_courses:
                break
            for venue in small_venues:
                small_courses = [c for c in small_courses if c.id not in scheduled_ids]
                if not small_courses:
                    break
                rem_cap = state.venue_remaining(venue.id, date_obj, ss)
                if rem_cap <= 0:
                    continue
                for i, course in enumerate(small_courses):
                    if course.id in scheduled_ids:
                        continue
                    needed = course_student_count(course)
                    if needed > rem_cap:
                        continue
                    py_key = state._py_key(course)
                    if py_day_count[(date_obj, py_key)] >= 1 and py_total_counts.get(py_key, 0) <= 2:
                        continue
                    if _check_hard_constraints(course, date_obj, ss, state):
                        continue
                    if state.cohort_in_cooling(course, date_obj, ss):
                        continue
                    if place_single(course, venue, date_obj, ss, se, state, scheduled_ids,
                                    allow_room_sharing=True, allow_split=False):
                        placed_total += 1
                        scheduled_ids.add(course.id)
                        rem_cap -= needed
                        py_day_count[(date_obj, py_key)] += 1
                for j in range(len(small_courses) - 1, -1, -1):
                    if rem_cap <= 0:
                        break
                    other_course = small_courses[j]
                    if other_course.id in scheduled_ids:
                        continue
                    other_needed = course_student_count(other_course)
                    if other_needed > rem_cap:
                        continue
                    other_py = state._py_key(other_course)
                    if py_day_count[(date_obj, other_py)] >= 1 and py_total_counts.get(other_py, 0) <= 2:
                        continue
                    if _check_hard_constraints(other_course, date_obj, ss, state):
                        continue
                    if place_single(other_course, venue, date_obj, ss, se, state, scheduled_ids,
                                    allow_room_sharing=True, allow_split=False):
                        placed_total += 1
                        scheduled_ids.add(other_course.id)
                        rem_cap -= other_needed
                        py_day_count[(date_obj, other_py)] += 1
                        break
    print(f"[PhaseA-SmallFirst] Placed {placed_total} small courses")
    return placed_total

def schedule_common_courses_priority_pass(all_courses, state, scheduled_ids):
    if not state.analysis.shared_unit_groups:
        return 0
    course_by_id = {c.id: c for c in all_courses}
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    if not dates or not all_slots:
        return 0
    def _live_group(cids):
        return [course_by_id[cid] for cid in cids
                if cid in course_by_id and cid not in scheduled_ids]
    pending: List[Tuple[str, List]] = []
    for nc, cids in state.analysis.shared_unit_groups.items():
        if nc in state.placed_families:
            continue
        group = _live_group(cids)
        if group:
            pending.append((nc, group))
    pending.sort(key=lambda kv: (-len(kv[1]), -family_total_students(kv[1])))
    print(f"\n[PhaseB-CommonLast] {len(pending)} common-course families, "
          f"working {len(all_slots)} slots from last to first")
    placed_total = 0
    pool_courses = [group[0] for _, group in pending if group]
    sorted_dates = state.get_sorted_dates_for_pool(dates, pool_courses)
    for round_idx in range(len(all_slots) - 1, -1, -1):
        if not pending:
            break
        ss, se = all_slots[round_idx]
        made_progress_this_round = True
        while pending and made_progress_this_round:
            made_progress_this_round = False
            for date_obj, _ in sorted_dates:
                if not pending:
                    break
                if not state.day_has_any_capacity(date_obj):
                    continue
                packed_this_slot = True
                while pending and packed_this_slot:
                    packed_this_slot = False
                    for idx in range(len(pending)):
                        nc, group = pending[idx]
                        group = [c for c in group if c.id not in scheduled_ids]
                        if not group:
                            pending.pop(idx)
                            state.placed_families.add(nc)
                            packed_this_slot = True
                            made_progress_this_round = True
                            break
                        true_total = family_total_students(group)
                        if not _family_constraints_ok(group, date_obj, ss, state):
                            continue
                        if state.slot_total_remaining(date_obj, ss) < true_total:
                            continue
                        if place_merged_family(group, nc, date_obj, ss, se, state, scheduled_ids):
                            placed_total += len(group)
                            state.placed_families.add(nc)
                            pending.pop(idx)
                            packed_this_slot = True
                            made_progress_this_round = True
                            print(f"  [PhaseB-CommonLast] '{nc}' (x{len(group)}) -> "
                                  f"{date_obj} {ss}")
                            break
            remaining = len(pending)
            if remaining:
                print(f"[PhaseB-CommonLast] {remaining} common families still pending "
                      f"after all slots — later phases will retry them")
    print(f"[PhaseB-CommonLast] Placed {placed_total} variants")
    return placed_total

def schedule_families_first(all_courses, state, scheduled_ids):
    if not state.analysis.shared_unit_groups:
        return 0
    course_by_id = {c.id: c for c in all_courses}
    dates = state.dates_in_order()
    sorted_families = sorted(
        state.analysis.shared_unit_groups.items(),
        key=lambda kv: (
            -state.analysis.shared_exams.get(kv[0], 0),
            -family_total_students([course_by_id[cid] for cid in kv[1] if cid in course_by_id]),
        ),
    )
    print(f"\n[Phase1] Scheduling {len(sorted_families)} families")
    placed_total = 0
    for nc, cids in sorted_families:
        if nc in state.placed_families:
            continue
        group = [course_by_id[cid] for cid in cids if cid in course_by_id and cid not in scheduled_ids]
        if not group:
            state.placed_families.add(nc)
            continue
        true_total = family_total_students(group)
        family_placed = False
        sorted_dates = state.get_sorted_dates(group[0])
        if state.analysis.shared_exams.get(nc, 0) >= 3 and state.evening_slots:
            for date_obj, _ in sorted_dates:
                if family_placed:
                    break
                for ss, se in state.evening_slots:
                    if family_placed:
                        break
                    if not state.day_has_any_capacity(date_obj):
                        continue
                    if not _family_constraints_ok(group, date_obj, ss, state):
                        continue
                    if state.slot_total_remaining(date_obj, ss) < true_total:
                        continue
                    if place_merged_family(group, nc, date_obj, ss, se, state, scheduled_ids):
                        placed_total += len(group)
                        family_placed = True
        if not family_placed:
            for date_obj, _ in sorted_dates:
                if family_placed:
                    break
                for ss, se in state.daytime_slots_list + state.evening_slots:
                    if family_placed:
                        break
                    if not state.day_has_any_capacity(date_obj):
                        continue
                    if not _family_constraints_ok(group, date_obj, ss, state):
                        continue
                    if state.slot_total_remaining(date_obj, ss) < true_total:
                        continue
                    if place_merged_family(group, nc, date_obj, ss, se, state, scheduled_ids):
                        placed_total += len(group)
                        family_placed = True
        if not family_placed and nc in state.strict_norm_codes:
            for c in group:
                state.strict_locked_ids.add(c.id)
            print(f"  [WARN] '{nc}' STRICTLY designated — left unscheduled")
    print(f"[Phase1] Placed {placed_total} variants")
    return placed_total

def _fill_slot_with_program(date, slot_start, slot_end, program_courses,
                            state, scheduled_ids, relax_consecutive=False,
                            placed_in_slot=None, allow_split=False,
                            allow_room_sharing=True):
    if placed_in_slot is None:
        placed_in_slot = defaultdict(list)
    placed = 0
    sorted_courses = sorted(
        [c for c in program_courses if c.id not in scheduled_ids],
        key=lambda c: course_student_count(c), reverse=True
    )
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
        should_allow_split = allow_split
        if try_place_course(course, date, slot_start, slot_end, state, scheduled_ids,
                            relax_consecutive=relax_consecutive,
                            allow_split=should_allow_split,
                            allow_room_sharing=allow_room_sharing):
            placed += 1
            if py_key:
                placed_in_slot[py_key].append(course)
    return placed

def run_saturation_day(date, pending_by_program, state, scheduled_ids,
                       all_courses, force_evenings=False,
                       allow_room_sharing=True, allow_split=False):
    placed_today = 0
    day_slots = state.daytime_slots_list
    eve_slots = state.evening_slots
    program_order = sorted(
        pending_by_program.items(),
        key=lambda kv: -len([c for c in kv[1] if c.id not in scheduled_ids]),
    )
    for round_idx in range(3):
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
                placed_in_slot = defaultdict(list)
                n = _fill_slot_with_program(
                    date, ss, se, active, state, scheduled_ids,
                    relax_consecutive=(round_idx > 0),
                    placed_in_slot=placed_in_slot,
                    allow_split=allow_split,
                    allow_room_sharing=allow_room_sharing,
                )
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
                    placed_in_slot = defaultdict(list)
                    n = _fill_slot_with_program(
                        date, ss, se, active, state, scheduled_ids,
                        relax_consecutive=True,
                        placed_in_slot=placed_in_slot,
                        allow_split=allow_split,
                        allow_room_sharing=allow_room_sharing,
                    )
                    if n > 0:
                        placed_today += n
                        progress_this_round += n
        all_pending = [c for c in all_courses if c.id not in scheduled_ids]
        if not all_pending:
            break
        all_pending_sorted = sorted(
            all_pending,
            key=lambda c: (-course_student_count(c), -state.priority_score(c))
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
                should_allow_split = allow_split
                if try_place_course(course, date, ss, se, state, scheduled_ids,
                                    relax_consecutive=(round_idx > 0),
                                    allow_split=should_allow_split,
                                    allow_room_sharing=allow_room_sharing):
                    placed_today += 1
                    progress_this_round += 1
                    if py_key:
                        placed_in_slot[py_key].append(course)
        if progress_this_round == 0:
            break
    return placed_today

def schedule_saturation_loop(all_courses, state, scheduled_ids):
    stats = {"placed": 0}
    dates = state.dates_in_order()
    strategy = state.strategy
    pending_by_program: Dict[str, List] = defaultdict(list)
    for c in all_courses:
        prog = getattr(c, "program", None)
        prog_id = str(prog.id) if prog else "no_program"
        pending_by_program[prog_id].append(c)
    print(f"\n[PhaseC-Saturation] Remaining courses saturation loop")
    max_passes = 6 if getattr(strategy, 'mode', 'NORMAL') in ('DENSE', 'OVERFLOW') else 4
    for pass_idx in range(max_passes):
        placed_this_pass = 0
        all_pending = [c for c in all_courses if c.id not in scheduled_ids]
        sorted_dates = state.get_sorted_dates_for_pool(dates, all_pending)
        for day_idx, (date_obj, _) in enumerate(sorted_dates):
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
                                   force_evenings=getattr(strategy, 'use_evening_slots', False),
                                   allow_room_sharing=True,
                                   allow_split=(pass_idx >= 2))
            placed_this_pass += n
            if n > 0:
                print(f"  Pass {pass_idx+1} Day {day_idx+1}: +{n}")
        print(f"[PhaseC-Saturation] Pass {pass_idx+1}: +{placed_this_pass}")
        if placed_this_pass == 0:
            break
    return stats

def cross_day_fill_pass(all_courses, state, scheduled_ids):
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0
    print(f"\n[Phase3] Cross-day fill: {len(unscheduled)} unscheduled")
    placed = 0
    dates = state.dates_in_order()
    course_by_id = {c.id: c for c in all_courses}
    for nc, cids in state.analysis.shared_unit_groups.items():
        if nc in state.placed_families:
            continue
        stuck = [course_by_id[cid] for cid in cids if cid in course_by_id and cid not in scheduled_ids]
        if not stuck:
            state.placed_families.add(nc)
            continue
        family_placed = False
        sorted_dates = state.get_sorted_dates(stuck[0])
        for d, _ in sorted_dates:
            if family_placed:
                break
            for ss, se in state.daytime_slots_list + state.evening_slots:
                if family_placed:
                    break
                if place_merged_family(stuck, nc, d, ss, se, state, scheduled_ids):
                    placed += len(stuck)
                    family_placed = True
    unscheduled_individual = sorted(
        [c for c in all_courses if c.id not in scheduled_ids and
         normalize_course_code(c.course_code or "") not in state.analysis.shared_unit_groups],
        key=lambda c: -state.priority_score(c)
    )
    sorted_dates = state.get_sorted_dates_for_pool(dates, unscheduled_individual)
    for date_obj, _ in sorted_dates:
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
                if not state.slot_has_combined_venue_space(date_obj, ss, needed, course):
                    continue
                if _check_hard_constraints(course, date_obj, ss, state):
                    continue
                if try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                    relax_consecutive=True, allow_split=True,
                                    allow_room_sharing=True):
                    placed += 1
                    if pk:
                        placed_in_slot[pk].append(course)
    print(f"[Phase3] Placed {placed}")
    return placed

def family_split_rescue_pass(all_courses, state, scheduled_ids):
    """
    CRITICAL FIX (v63): When a family fails joint placement, try to place
    as many variants as possible TOGETHER at the same time before falling
    back to individual placement. This ensures courses like ZOOL 443 (from
    different programs but same code) are scheduled together when possible.
    """
    course_by_id = {c.id: c for c in all_courses}
    unplaced_families = {}
    for nc, cids in state.analysis.shared_unit_groups.items():
        stuck = [course_by_id[cid] for cid in cids if cid in course_by_id and cid not in scheduled_ids]
        if stuck:
            unplaced_families[nc] = stuck
    if not unplaced_families:
        return 0
    print(f"\n[Phase3b] Family rescue: {len(unplaced_families)} families")
    placed_total = 0
    dates = state.dates_in_order()

    for nc, variants in unplaced_families.items():
        variants = [c for c in variants if c.id not in scheduled_ids]
        if not variants:
            continue

        rescued = False
        sorted_dates = state.get_sorted_dates(variants[0])

        # Try joint placement first
        for date_obj, _ in sorted_dates:
            if rescued:
                break
            for ss, se in state.daytime_slots_list + state.evening_slots:
                if rescued:
                    break
                if not _family_constraints_ok(variants, date_obj, ss, state):
                    continue
                if place_merged_family(variants, nc, date_obj, ss, se, state, scheduled_ids):
                    placed_total += len(variants)
                    rescued = True
                    print(f"  [Phase3b] '{nc}' → {date_obj} {ss}")
                    break

        # CRITICAL FIX (v63): If joint placement failed, try to place as many
        # variants as possible TOGETHER before individual placement
        if not rescued:
            state.family_exhausted.add(nc)
            print(f"  [Phase3b] '{nc}' failed joint placement — trying partial together placement")

            # Try to place multiple variants together at the same time
            for date_obj, _ in sorted_dates:
                remaining = [c for c in variants if c.id not in scheduled_ids]
                if not remaining:
                    break

                for ss, se in state.daytime_slots_list + state.evening_slots:
                    # Try to place all remaining variants together
                    placed_together = []
                    for course in remaining:
                        if try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                            relax_consecutive=True, allow_split=True,
                                            allow_room_sharing=True):
                            placed_together.append(course)

                    if len(placed_together) > 1:
                        placed_total += len(placed_together)
                        print(f"  [Phase3b-Together] '{nc}': placed {len(placed_together)} variants at {date_obj} {ss}")
                        break

                # If we placed some together, continue with remaining
                remaining = [c for c in variants if c.id not in scheduled_ids]
                if not remaining:
                    break

            # Place any remaining variants individually
            for course in variants:
                if course.id in scheduled_ids:
                    continue
                course_sorted_dates = state.get_sorted_dates(course)
                placed_solo = False
                for d_obj, _ in course_sorted_dates:
                    if placed_solo:
                        break
                    for c_ss, c_se in state.daytime_slots_list + state.evening_slots:
                        if try_place_course(course, d_obj, c_ss, c_se, state, scheduled_ids,
                                            relax_consecutive=True, allow_split=True,
                                            allow_room_sharing=True):
                            placed_total += 1
                            placed_solo = True
                            print(f"  [Phase3b-Individual] {course.course_code} → {d_obj} {c_ss}")
                            break

            if not any(c.id in scheduled_ids for c in variants):
                print(f"  [WARN] '{nc}' STILL unplaced after all attempts")

    print(f"[Phase3b] Rescued {placed_total} variants")
    return placed_total

def forced_fallback_pass(all_courses, state, scheduled_ids):
    total_placed = 0
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    for sweep in range(1, 5):
        unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
        if not unscheduled:
            break
        print(f"\n[Phase4] Sweep {sweep}: {len(unscheduled)} unscheduled")
        before_sweep = len(scheduled_ids)
        unscheduled = sorted(unscheduled, key=lambda c: -state.priority_score(c))
        if sweep == 2:
            for c in unscheduled:
                state.release_family_binding(c)
        if sweep == 3:
            for nc, cids in state.analysis.shared_unit_groups.items():
                if nc in state.placed_families:
                    continue
                group_unsched = [c for c in all_courses if c.id in cids and c.id not in scheduled_ids]
                if not group_unsched:
                    state.placed_families.add(nc)
                    continue
                sorted_dates = state.get_sorted_dates(group_unsched[0])
                for date_obj, _ in sorted_dates:
                    if nc in state.placed_families:
                        break
                    for ss, se in all_slots:
                        if nc in state.placed_families:
                            break
                        if place_merged_family(group_unsched, nc, date_obj, ss, se, state, scheduled_ids):
                            state.placed_families.add(nc)
        sorted_dates = state.get_sorted_dates_for_pool(dates, unscheduled)
        for date_obj, _ in sorted_dates:
            for ss, se in all_slots:
                if not state.slot_has_any_venue_space(date_obj, ss, 1):
                    continue
                placed_in_slot = defaultdict(list)
                for course in unscheduled:
                    if course.id in scheduled_ids:
                        continue
                    nc = normalize_course_code(course.course_code or "")
                    if nc in state.analysis.shared_unit_groups and nc not in state.family_exhausted:
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
                    if not state.slot_has_combined_venue_space(date_obj, ss, needed, course):
                        continue
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
                    should_allow_split = (sweep >= 4)
                    if try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                        relax_consecutive=True,
                                        allow_split=should_allow_split,
                                        allow_room_sharing=True):
                        if pk:
                            placed_in_slot[pk].append(course)
        newly_placed = len(scheduled_ids) - before_sweep
        total_placed += newly_placed
        print(f"[Phase4] Sweep {sweep}: +{newly_placed}")
        if newly_placed == 0 and sweep < 4:
            print(f"[Phase4] No progress — escalating")
    return total_placed

def db_driven_fallback_pass(all_courses, state, scheduled_ids, _progress_fn=None):
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0
    print(f"\n[Phase5-DB] {len(unscheduled)} unscheduled")
    free_slots = rebuild_state_from_db(state, all_courses)
    sync_scheduled_ids_from_db(scheduled_ids)
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled or not free_slots:
        return 0
    placed = 0
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    unscheduled = sorted(unscheduled, key=lambda c: -state.priority_score(c))
    sorted_dates = state.get_sorted_dates_for_pool(dates, unscheduled)
    for date_idx, (date_obj, _) in enumerate(sorted_dates):
        for ss, se in all_slots:
            if not state.slot_has_any_venue_space(date_obj, ss, 1):
                continue
            placed_in_slot = defaultdict(list)
            for course in unscheduled:
                if course.id in scheduled_ids:
                    continue
                nc = normalize_course_code(course.course_code or "")
                if nc in state.analysis.shared_unit_groups and nc not in state.family_exhausted:
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
                if not state.slot_has_combined_venue_space(date_obj, ss, needed, course):
                    continue
                if _check_hard_constraints(course, date_obj, ss, state):
                    continue
                should_allow_split = True
                if try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                    relax_consecutive=True,
                                    allow_split=should_allow_split,
                                    allow_room_sharing=True):
                    placed += 1
                    if pk:
                        placed_in_slot[pk].append(course)
    print(f"[Phase5-DB] Placed {placed}")
    return placed

def nuclear_fallback_pass(all_courses, state, scheduled_ids, _progress_fn=None):
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0
    print(f"\n[Phase7-Nuclear] {len(unscheduled)} unscheduled")
    placed = 0
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    unscheduled_sorted = sorted(unscheduled, key=lambda c: -state.priority_score(c))
    sorted_dates = state.get_sorted_dates_for_pool(dates, unscheduled_sorted)
    for date_obj, _ in sorted_dates:
        for ss, se in all_slots:
            if not state.slot_has_any_venue_space(date_obj, ss, 1):
                continue
            placed_in_slot = defaultdict(list)
            for course in unscheduled_sorted:
                if course.id in scheduled_ids:
                    continue
                nc = normalize_course_code(course.course_code or "")
                if nc in state.analysis.shared_unit_groups and nc not in state.family_exhausted:
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
                fk = state.family_key(course)
                if fk in state.family_slot:
                    nc_check = state._norm_code(course)
                    sibling_ids = state.analysis.shared_unit_groups.get(nc_check, [])
                    sibling_placed = any(sid != course.id and sid in scheduled_ids for sid in sibling_ids)
                    if not sibling_placed:
                        state.family_slot.pop(fk, None)
                        state.family_day.pop(fk, None)
                        if nc_check:
                            state.norm_code_day_lock.pop(nc_check, None)
                            state.shared_unit_lock.pop(nc_check, None)
                needed = course_student_count(course)
                if not state.slot_has_combined_venue_space(date_obj, ss, needed, course):
                    continue
                if _check_hard_constraints(course, date_obj, ss, state):
                    continue
                should_allow_split = True
                if try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                    relax_consecutive=True,
                                    allow_split=should_allow_split,
                                    allow_room_sharing=True):
                    placed += 1
                    if pk:
                        placed_in_slot[pk].append(course)
                    break
    print(f"[Phase7-Nuclear] Placed {placed}")
    return placed

def _describe_student_conflict_blockers(course, date, slot_start, state) -> str:
    """Name the already-scheduled course(s) that are occupying this
    course's own student cohort (program+year[+stem]) at date/slot_start —
    i.e. what's actually causing a 'student-conflict' rejection, so it can
    be checked as real vs. a bug rather than left as a bare count."""
    pk = state._py_key(course)
    if not pk:
        return "(no program+year key on this course — can't resolve)"
    blockers = state._py_busy_allocs.get((date, slot_start, pk), [])
    codes: List[str] = []
    seen: Set[str] = set()
    for b in blockers:
        if exam_is_collision_exempt(course, b):
            continue
        code = getattr(b, "course_code", None) or f"id={b.id}"
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return ", ".join(codes) if codes else "(cohort marked busy but no non-exempt blocker found — check exam_is_collision_exempt)"

def _describe_lecturer_conflict_blocker(course, date, slot_start, state, lid) -> str:
    """Name the already-scheduled course that has this same lecturer busy
    at date/slot_start, so a reported 'lecturer-conflict' can be checked
    against the real timetable rather than trusted blindly."""
    if not lid:
        return ""
    try:
        rows = ExamTempTimetable.objects.filter(
            date=date, start_time=slot_start
        ).select_related("course_allocation")
    except Exception as exc:
        return f"(lookup failed: {exc})"
    for row in rows:
        occ_course = row.course_allocation
        if occ_course and state._cached_lecturer_id(occ_course) == lid:
            return getattr(occ_course, "course_code", None) or f"id={occ_course.id}"
    return "(marked busy but no matching DB row found — check lecturer_busy/mark_lecturer_busy)"

def diagnose_unscheduled_courses(all_courses, state, scheduled_ids, sample_size: int = 25) -> None:
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    by_prog_year: Dict[str, List] = defaultdict(list)
    for c in unscheduled:
        prog = getattr(c, "program", None)
        prog_name = getattr(prog, "name", None) or "Unknown program"
        by_prog_year[f"{prog_name} — {get_program_year(c)}"].append(c)
    print(f"\n══════════════════════════════════════════")
    print(f"  UNSCHEDULED BY PROGRAM+YEAR ({len(unscheduled)} total)")
    print(f"══════════════════════════════════════════")
    for key, courses in sorted(by_prog_year.items(), key=lambda kv: -len(kv[1])):
        print(f"  {key}: {len(courses)} unscheduled")
    per_group_cap = max(1, sample_size // max(1, len(by_prog_year)))
    sample: List = []
    seen_ids: Set[int] = set()
    for _, courses in sorted(by_prog_year.items(), key=lambda kv: -len(kv[1])):
        group_sorted = sorted(courses, key=lambda c: -state.priority_score(c))
        for c in group_sorted[:per_group_cap]:
            if c.id not in seen_ids:
                sample.append(c)
                seen_ids.add(c.id)
    if len(sample) < sample_size:
        remainder = sorted(
            [c for c in unscheduled if c.id not in seen_ids],
            key=lambda c: -state.priority_score(c),
        )
        for c in remainder:
            if len(sample) >= sample_size:
                break
            sample.append(c)
            seen_ids.add(c.id)
    unscheduled = sample[:sample_size]
    print(f"\n══════════════════════════════════════════")
    print(f"  DIAGNOSTIC — why are courses still stuck?")
    print(f"  (sampling {len(unscheduled)} of {len(by_prog_year)} program+year groups)")
    print(f"══════════════════════════════════════════")
    for course in unscheduled:
        needed = course_student_count(course)
        lid = state._cached_lecturer_id(course)
        nc = normalize_course_code(getattr(course, "course_code", "") or "")
        code = (getattr(course, "course_code", "") or "").strip()
        trace_this = code in TRACE_COURSE_CODES
        reason_counts: Dict[str, int] = defaultdict(int)
        example_slot_by_reason: Dict[str, Tuple] = {}
        slots_with_space = 0
        first_space_no_block = None
        per_slot_detail: List[Tuple] = []  # (date_obj, ss, reason_or_None, extra)
        for date_obj, _ in dates:
            for ss, se in all_slots:
                if not state.slot_has_combined_venue_space(date_obj, ss, needed, course):
                    reason_counts["no-venue-space-for-full-count"] += 1
                    if trace_this:
                        detail = state.explain_no_venue_space(date_obj, ss, needed, course)
                        per_slot_detail.append((date_obj, ss, "no-venue-space-for-full-count", detail))
                        if "genuinely exhausted" in detail:
                            # Don't just trust venue_usage's "exhausted"
                            # verdict — cross-check it against the actual
                            # DB rows for this slot. If the manual/UI
                            # picture shows this slot as free, this is
                            # where that contradiction gets surfaced.
                            state.audit_slot_vs_db(date_obj, ss, label=code)
                    continue
                slots_with_space += 1
                reason = _check_hard_constraints(course, date_obj, ss, state)
                if reason:
                    reason_counts[reason] += 1
                    example_slot_by_reason.setdefault(reason, (date_obj, ss))
                    if trace_this:
                        extra = ""
                        if reason == "student-conflict":
                            extra = _describe_student_conflict_blockers(course, date_obj, ss, state)
                        per_slot_detail.append((date_obj, ss, reason, extra))
                    continue
                if lid and not state.lecturer_available(lid, date_obj, ss, course):
                    reason_counts["lecturer-conflict"] += 1
                    example_slot_by_reason.setdefault("lecturer-conflict", (date_obj, ss))
                    if trace_this:
                        blocker = _describe_lecturer_conflict_blocker(course, date_obj, ss, state, lid)
                        per_slot_detail.append((date_obj, ss, "lecturer-conflict", blocker))
                    continue
                if first_space_no_block is None:
                    first_space_no_block = (date_obj, ss)
                if trace_this:
                    per_slot_detail.append((date_obj, ss, None, ""))
        print(f"\n[{course.course_code}] id={course.id} needed={needed} "
              f"lecturer_id={lid} shared={nc in state.analysis.shared_unit_groups}")
        print(f"  Slots with enough venue space at all: {slots_with_space}")
        if reason_counts:
            top = sorted(reason_counts.items(), key=lambda kv: -kv[1])
            print(f"  Rejection reasons (count): {top}")
            # Name the actual blocking course for the two reasons that are
            # otherwise just bare counts — lets you verify a real clash vs.
            # a bug (a phantom conflict with no real blocker behind it).
            for reason, _cnt in top:
                ex = example_slot_by_reason.get(reason)
                if ex is None:
                    continue
                ex_date, ex_ss = ex
                if reason == "student-conflict":
                    blockers = _describe_student_conflict_blockers(course, ex_date, ex_ss, state)
                    print(f"    ↳ e.g. {ex_date} {ex_ss}: same cohort already sitting [{blockers}]")
                elif reason == "lecturer-conflict":
                    blocker = _describe_lecturer_conflict_blocker(course, ex_date, ex_ss, state, lid)
                    print(f"    ↳ e.g. {ex_date} {ex_ss}: lecturer_id={lid} already teaching {blocker}")
        if first_space_no_block:
            print(f"  ⚠ FREE & UNBLOCKED slot exists at {first_space_no_block} but course "
                  f"is still unscheduled — this is a placement-logic bug.")
        elif slots_with_space == 0:
            print(f"  → Genuinely no venue anywhere has {needed} free seats in any slot.")
        else:
            print(f"  → Every slot with enough space is blocked by the reasons above.")
        if trace_this and per_slot_detail:
            # Full per-slot breakdown (every date/slot, not just one example
            # per reason bucket) — this is what actually lets you check a
            # specific slot you're looking at in the UI (e.g. "Thursday
            # 11:30") against what the scheduler believes about it, instead
            # of hoping the one sampled example happens to be that slot.
            print(f"  Full per-slot breakdown ({code}):")
            for date_obj, ss, reason, extra in per_slot_detail:
                if reason is None:
                    print(f"    {date_obj} {ss}: FREE & unblocked (should have been used!)")
                elif reason == "no-venue-space-for-full-count":
                    print(f"    {date_obj} {ss}: no-venue-space-for-full-count ({extra})")
                elif extra:
                    print(f"    {date_obj} {ss}: {reason} — {extra}")
                else:
                    print(f"    {date_obj} {ss}: {reason}")
    print(f"══════════════════════════════════════════\n")

def ultimate_fallback_pass(all_courses, state, scheduled_ids):
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0
    print(f"\n[Phase8-Ultimate] {len(unscheduled)} unscheduled")
    state.allow_strict_rescue = True
    placed_before = len(scheduled_ids)
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    course_by_id = {c.id: c for c in all_courses}
    for nc, cids in state.analysis.shared_unit_groups.items():
        variants = [course_by_id[cid] for cid in cids if cid in course_by_id and cid not in scheduled_ids]
        if not variants:
            continue
        rescued = False
        sorted_dates = state.get_sorted_dates(variants[0])
        for date_obj, _ in sorted_dates:
            if rescued:
                break
            for ss, se in all_slots:
                if rescued:
                    break
                if not _family_constraints_ok(variants, date_obj, ss, state):
                    continue
                if place_merged_family(variants, nc, date_obj, ss, se, state, scheduled_ids):
                    rescued = True
                    break
    individual_remaining = sorted(
        [c for c in all_courses if c.id not in scheduled_ids],
        key=lambda c: -course_student_count(c)
    )
    sorted_dates = state.get_sorted_dates_for_pool(dates, individual_remaining)
    for course in individual_remaining:
        if course.id in scheduled_ids:
            continue
        if _course_already_in_db(course):
            scheduled_ids.add(course.id)
            continue
        needed = course_student_count(course)
        lid = state._cached_lecturer_id(course)
        nc = normalize_course_code(course.course_code or "")
        should_allow_split = True
        for date_obj, _ in sorted_dates:
            if course.id in scheduled_ids:
                break
            for ss, se in all_slots:
                if course.id in scheduled_ids:
                    break
                if lid and not state.lecturer_available(lid, date_obj, ss, course):
                    continue
                if not state.students_available(course, date_obj, ss):
                    continue
                if state.check_norm_code_day_conflict(course, date_obj):
                    continue
                if state.check_shared_unit_conflict(course, date_obj, ss):
                    continue
                if state.check_family_conflict(course, date_obj, ss):
                    continue
                if not state.slot_has_any_venue_space(date_obj, ss, 1):
                    continue
                if try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                    relax_consecutive=True,
                                    allow_split=should_allow_split,
                                    allow_room_sharing=True):
                    break
    placed = len(scheduled_ids) - placed_before
    print(f"[Phase8-Ultimate] Placed {placed}")
    state.allow_strict_rescue = False
    return placed

def post_consolidation_rescue_pass(all_courses, state, scheduled_ids):
    """Retry placement for whatever is still unscheduled, after venue
    consolidation/swap have run.

    Why this needs to exist at all: consolidate_underfilled_small_venues_pass
    and post_placement_swap_optimization only ever rearrange courses that
    are ALREADY placed — they can free up a whole venue (when a donor empties
    out) or open remaining capacity in a slot, but neither pass ever looks at
    the unscheduled pile to see if that newly-freed space could seat one of
    them. So a course can end up unscheduled purely because of *timing* —
    every fallback pass ran and failed before that space existed — even
    though a perfectly good, empty venue+slot sits there afterward. That's
    exactly the kind of case where the log said "no venue space" but the
    person looking at the actual timetable can see a free room at that time.

    This is a full state resync (via rebuild_state_from_db) rather than a
    trust-the-incremental-bookkeeping approach, specifically because it runs
    right after two passes that mutate the DB directly — cheap insurance
    against any bookkeeping drift between state and the DB before we make
    the final placement attempt and before diagnose_unscheduled_courses
    reports on what's "really" stuck.
    """
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0
    print(f"\n[Phase9b-PostConsolidationRescue] {len(unscheduled)} unscheduled — "
          f"resyncing state from DB to catch space freed by consolidation/swap")
    free_slots = rebuild_state_from_db(state, all_courses)
    sync_scheduled_ids_from_db(scheduled_ids)
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled or not free_slots:
        print(f"[Phase9b-PostConsolidationRescue] Placed 0 (no free slots after resync)")
        return 0
    placed = 0
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    unscheduled_sorted = sorted(unscheduled, key=lambda c: -state.priority_score(c))
    sorted_dates = state.get_sorted_dates_for_pool(dates, unscheduled_sorted)
    for date_obj, _ in sorted_dates:
        for ss, se in all_slots:
            if not state.slot_has_any_venue_space(date_obj, ss, 1):
                continue
            placed_in_slot = defaultdict(list)
            for course in unscheduled_sorted:
                if course.id in scheduled_ids:
                    continue
                nc = normalize_course_code(course.course_code or "")
                if nc in state.analysis.shared_unit_groups and nc not in state.family_exhausted:
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
                if not state.slot_has_combined_venue_space(date_obj, ss, needed, course):
                    continue
                if _check_hard_constraints(course, date_obj, ss, state):
                    continue
                if try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                    relax_consecutive=True,
                                    allow_split=True,
                                    allow_room_sharing=True):
                    placed += 1
                    if pk:
                        placed_in_slot[pk].append(course)
    print(f"[Phase9b-PostConsolidationRescue] Placed {placed}")
    return placed

# ======================================================================
# SECTION 9 – Post-Placement Swap Optimization
# ======================================================================

def consolidate_underfilled_small_venues_pass(all_courses, state, scheduled_ids):
    """
    Post-placement consolidation: within each (date, slot), merge courses
    that ended up alone in their own small venue into fewer, already-in-use
    small venues, freeing the vacated ones.

    This is the gap that let e.g. four ~10-student exams each keep their
    own 25/50-cap room after placement: schedule_small_courses_in_small_venues
    packs venues WHILE it places courses, but by the time some of these
    courses got their turn the rest of that day/slot's small-course pool
    was already exhausted, so each ended up alone. post_placement_swap_
    optimization only ever exchanges two courses' venues 1-for-1 — it can
    reduce wasted seats a little but can never reduce the NUMBER of venues
    used, because a swap always leaves exactly one course per venue.

    This pass runs after every placement phase and before swap
    optimization, and actually merges: for each slot, sort the small
    venues in use smallest-first, and for each one with room to spare, try
    to pull in a whole other small venue's course(s) — smallest venue
    (by remaining capacity used, i.e. tightest current occupant) first —
    as long as _can_share_venue-style rules confirm no real student
    conflict. A donor venue that ends up with zero occupants left is
    freed automatically (no entries reference it any more for that slot).
    """
    print(f"\n[Phase9a-VenueConsolidation] Consolidating underfilled small venues")
    entries = list(ExamTempTimetable.objects.values(
        "id", "course_allocation_id", "venue_id", "date", "start_time", "end_time"
    ))
    if not entries:
        print(f"[Phase9a-VenueConsolidation] No placements to consolidate")
        return 0

    course_by_id = {c.id: c for c in all_courses}
    slot_groups = defaultdict(list)
    for e in entries:
        slot_groups[(e["date"], e["start_time"])].append(e)

    total_slots = len(slot_groups)
    slots_done = 0
    pass_start = time.time()
    last_heartbeat = pass_start
    print(f"[Phase9a-VenueConsolidation] {total_slots} (date, slot) groups to scan")

    def _compatible(course_a, course_b) -> bool:
        if course_a.id == course_b.id:
            return True
        if normalize_course_code(getattr(course_a, "course_code", "") or "") == \
           normalize_course_code(getattr(course_b, "course_code", "") or ""):
            return True
        if _combined_group_are_paired(course_a.id, course_b.id):
            return True
        return not courses_share_students(course_a, course_b)

    moves_performed = 0
    for (date_obj, ss), slot_entries in slot_groups.items():
        slot_start_t = time.time()
        slots_done += 1
        now = time.time()
        if now - last_heartbeat > 5:
            # Heartbeat so a "hung" run shows visible progress in the log
            # instead of going silent between the phase-start and
            # phase-end lines — if this stops incrementing, it's stuck
            # inside a single slot's while-loop below, not "processing".
            elapsed = now - pass_start
            print(f"  [Phase9a-Heartbeat] {slots_done}/{total_slots} slots scanned, "
                  f"{moves_performed} moves so far, {elapsed:.1f}s elapsed")
            last_heartbeat = now

        by_venue = defaultdict(list)
        for e in slot_entries:
            by_venue[e["venue_id"]].append(e)
        if len(by_venue) < 2:
            continue

        venues_in_slot = []
        for vid, ventries in by_venue.items():
            v = state.venue_by_id.get(vid)
            if not v:
                continue
            cap = state.venue_examcap.get(vid, 0)
            if cap <= 0 or cap >= SMALL_VENUE_CAP_THRESHOLD:
                continue  # only consolidating within small venues, per strategy
            used = sum(
                course_student_count(course_by_id[e["course_allocation_id"]])
                for e in ventries if e["course_allocation_id"] in course_by_id
            )
            venues_in_slot.append({"id": vid, "venue": v, "cap": cap,
                                   "entries": ventries, "used": used})
        if len(venues_in_slot) < 2:
            continue

        # Receiving venues tried smallest-capacity first (fill small rooms
        # before ever leaning on a bigger one); donor venues offered up
        # smallest-occupant-count first, so a donor fully empties out
        # quickly instead of being left half-drained.
        venues_in_slot.sort(key=lambda row: row["cap"])
        # Single forward pass over venues_in_slot, smallest-capacity first.
        # `moved_entry_ids` guarantees every entry can be relocated AT MOST
        # ONCE per slot — this is what actually fixes the loop problem.
        #
        # The previous version restarted the whole target scan from the top
        # after every successful move (a `while changed` loop). That let a
        # venue which just RECEIVED a course become a valid DONOR again on
        # the very next restart (it now has an occupant, often the least-
        # used one around), so the same course could get pulled straight
        # back out to fill a different small venue, which could then get
        # raided by the next restart, and so on — real oscillation, not
        # gradual convergence. That's exactly what the guard/heartbeat
        # logging below caught: ~14,000 moves for ~2,500 courses (5-6 moves
        # per course on average) and the guard tripping at its limit on 10
        # separate slots. A single forward pass with a per-entry "moved
        # once" lock makes that class of bug structurally impossible: total
        # moves this slot can never exceed the number of entries in it.
        moved_entry_ids: Set[int] = set()
        guard = 0  # kept only for the slow-slot log line below
        for target in venues_in_slot:
            if not target["entries"] and target["cap"] <= 0:
                continue
            rem = target["cap"] - target["used"]
            if rem <= 0:
                continue
            target_courses = [
                course_by_id[e["course_allocation_id"]] for e in target["entries"]
                if e["course_allocation_id"] in course_by_id
            ]
            donors = sorted(
                (row for row in venues_in_slot if row["id"] != target["id"] and row["entries"]),
                key=lambda row: row["used"],
            )
            for donor in donors:
                if rem <= 0:
                    break
                for e in list(donor["entries"]):
                    if rem <= 0:
                        break
                    if e["id"] in moved_entry_ids:
                        continue  # already relocated once this slot — never move it again
                    cid = e["course_allocation_id"]
                    course = course_by_id.get(cid)
                    if not course:
                        continue
                    needed = course_student_count(course)
                    if needed > rem:
                        continue
                    if not all(_compatible(course, tc) for tc in target_courses):
                        continue
                    try:
                        with transaction.atomic():
                            ExamTempTimetable.objects.filter(id=e["id"]).update(
                                venue_id=target["id"]
                            )
                    except Exception as ex:
                        print(f"  [Consolidate] Error moving course {cid}: {ex}")
                        continue
                    # Keep state's venue accounting in sync with the DB move —
                    # without this, `state` still believes the donor venue is
                    # full and the target isn't fuller, so later reads of
                    # venue_remaining() (including the unscheduled-course
                    # diagnostic) are wrong for both venues at this slot.
                    state.release_venue(donor["id"], date_obj, ss, needed, course)
                    state.consume_venue(target["id"], date_obj, ss, needed, course)
                    donor["entries"].remove(e)
                    donor["used"] -= needed
                    target["entries"].append(e)
                    target["used"] += needed
                    target_courses.append(course)
                    rem -= needed
                    moved_entry_ids.add(e["id"])
                    moves_performed += 1
                    guard += 1
                    code = (getattr(course, "course_code", "") or "").strip()
                    if code in TRACE_COURSE_CODES:
                        print(f"[TRACE] {code} -> CONSOLIDATED {donor['venue'].code} -> "
                              f"{target['venue'].code} date={date_obj} slot={ss}")
                    if DEBUG_VERBOSE:
                        print(f"  [Consolidate] {course.course_code} ({needed} students): "
                              f"{donor['venue'].code} → {target['venue'].code}")

        slot_elapsed = time.time() - slot_start_t
        if slot_elapsed > 3:
            # A single slot taking multiple seconds is the real signature of
            # a loop problem (as opposed to the whole phase just being slow
            # because there are many slots) — flag it with enough detail to
            # go find that exact date/slot in the data.
            print(f"  [Phase9a-WARN] Slot date={date_obj} slot={ss} took "
                  f"{slot_elapsed:.2f}s ({len(venues_in_slot)} small venues, "
                  f"{guard} moves) — investigate this slot "
                  f"if it recurs on every run.")

    total_elapsed = time.time() - pass_start
    print(f"[Phase9a-VenueConsolidation] Moved {moves_performed} courses into fewer venues "
          f"in {total_elapsed:.2f}s across {total_slots} slots")
    return moves_performed

def post_placement_swap_optimization(all_courses, state, scheduled_ids):
    print(f"\n[Phase9-SwapOptimization] Starting post-placement swap optimization")
    entries = list(ExamTempTimetable.objects.values(
        "id", "course_allocation_id", "venue_id", "date", "start_time", "end_time"
    ))
    if not entries:
        print(f"[Phase9-SwapOptimization] No placements to optimize")
        return 0
    course_by_id = {c.id: c for c in all_courses}
    slot_groups = defaultdict(list)
    for e in entries:
        slot_groups[(e["date"], e["start_time"])].append(e)
    swaps_performed = 0
    for (date_obj, ss), slot_entries in slot_groups.items():
        if len(slot_entries) < 2:
            continue
        for i in range(len(slot_entries)):
            for j in range(i + 1, len(slot_entries)):
                entry_a = slot_entries[i]
                entry_b = slot_entries[j]
                course_a = course_by_id.get(entry_a["course_allocation_id"])
                course_b = course_by_id.get(entry_b["course_allocation_id"])
                if not course_a or not course_b:
                    continue
                if course_a.id == course_b.id:
                    continue
                venue_a = state.venue_by_id.get(entry_a["venue_id"])
                venue_b = state.venue_by_id.get(entry_b["venue_id"])
                if not venue_a or not venue_b:
                    continue
                if venue_a.id == venue_b.id:
                    continue
                students_a = course_student_count(course_a)
                students_b = course_student_count(course_b)
                cap_a = state.venue_examcap.get(venue_a.id, 0)
                cap_b = state.venue_examcap.get(venue_b.id, 0)
                # This pass only reasons about the two courses being swapped —
                # it has no model of OTHER courses that may already share
                # venue_a/venue_b at this slot (room-sharing is allowed
                # everywhere else in this scheduler). Comparing against raw
                # cap_a/cap_b, as before, silently assumes each venue holds
                # exactly one course; if either venue is actually shared, a
                # swap can push real DB usage past capacity without this
                # function ever knowing. Only allow the swap when each venue's
                # ENTIRE current usage is accounted for by the one course
                # being removed — i.e. it's genuinely a single-occupant venue,
                # which is the only case this pairwise logic is valid for.
                used_a_total = cap_a - state.venue_remaining(venue_a.id, date_obj, ss)
                used_b_total = cap_b - state.venue_remaining(venue_b.id, date_obj, ss)
                if used_a_total != students_a or used_b_total != students_b:
                    continue
                current_waste_a = cap_a - students_a
                current_waste_b = cap_b - students_b
                current_total_waste = current_waste_a + current_waste_b
                if students_a > cap_b or students_b > cap_a:
                    continue
                new_waste_a = cap_b - students_a
                new_waste_b = cap_a - students_b
                new_total_waste = new_waste_a + new_waste_b
                if new_total_waste < current_total_waste:
                    try:
                        with transaction.atomic():
                            ExamTempTimetable.objects.filter(id=entry_a["id"]).update(venue=venue_b)
                            ExamTempTimetable.objects.filter(id=entry_b["id"]).update(venue=venue_a)
                        # Same reason as the consolidation pass: the DB now
                        # has course_a in venue_b and course_b in venue_a,
                        # but `state`'s venue_usage/venue_occupants still
                        # reflect the pre-swap assignment unless we mirror
                        # the move here too.
                        state.release_venue(venue_a.id, date_obj, ss, students_a, course_a)
                        state.release_venue(venue_b.id, date_obj, ss, students_b, course_b)
                        ok_b = state.consume_venue(venue_b.id, date_obj, ss, students_a, course_a)
                        ok_a = state.consume_venue(venue_a.id, date_obj, ss, students_b, course_b)
                        if not (ok_a and ok_b):
                            # state refused to record this (would exceed
                            # capacity) — the DB write already happened, so
                            # this is not optional cleanup: without undoing
                            # it here, the DB ends up overbooked while state
                            # stays clean, which is exactly the drift that
                            # made the final capacity-violation count read 0
                            # despite real overcapacity in the DB. Undo both
                            # the state accounting attempted above and the
                            # DB move itself.
                            if ok_b:
                                state.release_venue(venue_b.id, date_obj, ss, students_a, course_a)
                            if ok_a:
                                state.release_venue(venue_a.id, date_obj, ss, students_b, course_b)
                            state.consume_venue(venue_a.id, date_obj, ss, students_a, course_a)
                            state.consume_venue(venue_b.id, date_obj, ss, students_b, course_b)
                            with transaction.atomic():
                                ExamTempTimetable.objects.filter(id=entry_a["id"]).update(venue=venue_a)
                                ExamTempTimetable.objects.filter(id=entry_b["id"]).update(venue=venue_b)
                            continue
                        swaps_performed += 1
                        if DEBUG_VERBOSE:
                            print(f"  [Swap] {course_a.course_code} ({students_a} students): "
                                  f"{venue_a.code} (cap={cap_a}) → {venue_b.code} (cap={cap_b})")
                            print(f"  [Swap] {course_b.course_code} ({students_b} students): "
                                  f"{venue_b.code} (cap={cap_b}) → {venue_a.code} (cap={cap_a})")
                    except Exception as e:
                        print(f"  [Swap] Error: {e}")
    print(f"[Phase9-SwapOptimization] Performed {swaps_performed} beneficial swaps")
    return swaps_performed

# ======================================================================
# SECTION 10 – DB Helpers
# ======================================================================

def sync_scheduled_ids_from_db(scheduled_ids: Set[int], force: bool = False):
    if not force:
        cached_count = len(_already_scheduled_cache)
        db_count = ExamTempTimetable.objects.values("course_allocation_id").distinct().count()
        if db_count == cached_count:
            scheduled_ids.update(_already_scheduled_cache)
            return 0
    db_ids = set(ExamTempTimetable.objects.values_list("course_allocation_id", flat=True).distinct())
    scheduled_ids.update(db_ids)
    _already_scheduled_cache.update(db_ids)
    return len(db_ids)

def rebuild_state_from_db(state: SchedulerState, all_courses: List) -> List:
    state.venue_usage.clear()
    state._py_busy.clear()
    state._py_busy_allocs.clear()
    state.lecturer_busy.clear()
    state.lecturer_exam_group.clear()
    state.cohort_last_slot_idx.clear()
    state.cohort_daily_count.clear()
    state.lecturer_daily_count.clear()
    state.family_slot.clear()
    state.family_day.clear()
    state.shared_unit_lock.clear()
    state.norm_code_day_lock.clear()
    state.venue_occupants.clear()
    course_by_id = {c.id: c for c in all_courses}
    entries = list(ExamTempTimetable.objects.values("course_allocation_id", "venue_id", "date", "start_time"))
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
                state.venue_occupants[(vid, date_obj, ss)].append((cid, take))
        if course:
            pk = state._py_key(course)
            if pk:
                state._py_busy.setdefault((date_obj, ss), set()).add(pk)
                state._py_busy_allocs.setdefault((date_obj, ss, pk), []).append(course)
            lid = state._cached_lecturer_id(course)
            if lid:
                state.lecturer_busy[lid].add((date_obj, ss))
                state.lecturer_exam_group[(lid, date_obj, ss)] = get_exam_group_key(course)
                state.lecturer_daily_count[(lid, date_obj)] += 1
            idx = state._slot_start_to_idx.get(ss)
            if idx is not None and pk:
                daily_key = state._daily_limit_key(course)
                state.cohort_last_slot_idx[(daily_key, date_obj)] = idx
                state.cohort_daily_count[(daily_key, date_obj)] += 1
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
    for date_obj, _ in state.date_range:
        for ss, _ in state.all_slots_ordered:
            for v in state.venues:
                used = state.venue_usage[(v.id, date_obj, ss)]
                state._venue_avail[(v.id, date_obj, ss)] = max(0, state.venue_examcap.get(v.id, 0) - used)
    free_slots = []
    for date_obj, _ in state.dates_in_order():
        for ss, se in state.all_slots_ordered:
            if state.slot_has_any_venue_space(date_obj, ss):
                free_slots.append((date_obj, ss, se))
    return free_slots

def sync_lecturer_busy_from_db(state, all_courses):
    course_by_id = {c.id: c for c in all_courses}
    entries = list(ExamTempTimetable.objects.values("course_allocation_id", "date", "start_time"))
    for e in entries:
        course = course_by_id.get(e["course_allocation_id"])
        if course:
            lid = state._cached_lecturer_id(course)
            if lid:
                state.lecturer_busy[lid].add((e["date"], e["start_time"]))
                state.lecturer_exam_group[(lid, e["date"], e["start_time"])] = get_exam_group_key(course)

# ======================================================================
# SECTION 11 – Auditing
# ======================================================================

def _audit_lecturer_conflicts(state, all_courses):
    from collections import Counter
    entries = list(ExamTempTimetable.objects.values("course_allocation_id", "date", "start_time"))
    course_by_id = {c.id: c for c in all_courses}
    slot_lecturers = defaultdict(list)
    slot_alloc_ids = defaultdict(list)
    for e in entries:
        course = course_by_id.get(e["course_allocation_id"])
        if course:
            lid = getattr(course.lecturer, "id", None) if course.lecturer else None
            key = (e["date"], e["start_time"])
            if lid:
                slot_lecturers[key].append(lid)
                slot_alloc_ids[key].append(e["course_allocation_id"])
    violations = 0
    for (date, ss), lids in slot_lecturers.items():
        for lid, count in Counter(lids).items():
            if count > 1:
                exam_groups = set()
                for aid in slot_alloc_ids[(date, ss)]:
                    course = course_by_id.get(aid)
                    if course and getattr(course.lecturer, "id", None) == lid:
                        exam_groups.add(get_exam_group_key(course))
                if len(exam_groups) > 1:
                    violations += 1
                    print(f"[Audit-LECTURER] VIOLATION: lecturer={lid} at {date} {ss}")
    return violations

def _audit_student_conflicts(state, all_courses):
    entries = list(ExamTempTimetable.objects.values("course_allocation_id", "date", "start_time"))
    course_by_id = {c.id: c for c in all_courses}
    slot_cohorts = defaultdict(list)
    for e in entries:
        course = course_by_id.get(e["course_allocation_id"])
        if course:
            pk = prog_year_key(course)
            if pk:
                slot_cohorts[(e["date"], e["start_time"])].append((pk, course))
    violations = 0
    for (date, ss), cohort_courses in slot_cohorts.items():
        by_pk = defaultdict(list)
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
                        print(f"[Audit-STUDENT] VIOLATION: {pk}")
    return violations

def _audit_venue_capacity_with_sharing(state, all_courses):
    """Ground-truth capacity check.

    This USED to sum state.venue_occupants and compare against cap — but
    state.venue_usage is only ever updated through consume_venue(), which
    already refuses to record anything past capacity. That makes state
    structurally incapable of ever reflecting an overbooked venue, even
    when one exists: any code path that writes to ExamTempTimetable
    directly (or updates state's bookkeeping without checking consume_venue's
    return value, as post_placement_swap_optimization used to) can leave the
    DB overbooked while state stays "clean". Checking state here means this
    audit was reporting 0 violations by construction, regardless of what the
    DB actually held. Query the DB directly instead — the same ground truth
    audit_slot_vs_db already uses elsewhere in this file.
    """
    course_by_id = {c.id: c for c in all_courses}
    usage: Dict[Tuple[int, object, object], int] = defaultdict(int)
    rows = ExamTempTimetable.objects.values("venue_id", "date", "start_time", "course_allocation_id")
    for r in rows:
        course = course_by_id.get(r["course_allocation_id"])
        n = course_student_count(course) if course else 0
        usage[(r["venue_id"], r["date"], r["start_time"])] += n
    violations = 0
    for (vid, date, ss), total_assigned in usage.items():
        cap = state.venue_examcap.get(vid, 0)
        if total_assigned > cap:
            violations += 1
            print(f"[Audit-OVERCAP] VIOLATION: venue_id={vid} date={date} slot={ss} | "
                  f"assigned={total_assigned} > cap={cap} (over by {total_assigned - cap})")
    return violations

def _audit_and_fix_duplicate_placements(state, all_courses):
    """Remove genuine duplicate placements — a course scheduled at more
    than one distinct (date, start_time) — WITHOUT touching legitimate
    multi-venue split placements, where the same course intentionally has
    several ExamTempTimetable rows at the SAME (date, start_time) across
    different venues (see place_multi_venue / _commit_distributed_minimal,
    used for e.g. the EENG/BLAW designated splits).

    The old version grouped by course_allocation_id alone, so any split
    placement — which is just as "len(rows) > 1 for this course" as a real
    duplicate — got its extra venue rows silently deleted, keeping only
    one. Worse, it never told `state` those seats were freed, so
    state.venue_usage kept counting them as consumed long after the DB
    showed them empty — exactly the phantom-capacity pattern the
    audit_slot_vs_db check surfaced (state showing a venue 100% full while
    the DB shows 0 rows there).

    Now: group by (course_allocation_id, date, start_time) first — that's
    one placement EVENT, however many venues it spans. A course is only a
    genuine duplicate if it has more than one such event (i.e. scheduled
    on two different days/slots). Only then do we drop the extra event's
    rows, and we call state.release_venue() for each one so state and the
    DB agree afterward instead of drifting apart.
    """
    course_by_id = {c.id: c for c in all_courses}
    entries = list(ExamTempTimetable.objects.values(
        "id", "course_allocation_id", "date", "start_time", "venue_id"
    ))
    by_course = defaultdict(list)
    for e in entries:
        by_course[e["course_allocation_id"]].append(e)
    fixed = 0
    drop_ids: List[int] = []
    for cid, rows in by_course.items():
        events = defaultdict(list)
        for r in rows:
            events[(r["date"], r["start_time"])].append(r)
        if len(events) <= 1:
            continue  # single event, however many venues it spans — not a duplicate
        event_keys_sorted = sorted(events.keys())
        keep_key = event_keys_sorted[0]
        course = course_by_id.get(cid)
        for key in event_keys_sorted[1:]:
            for r in events[key]:
                drop_ids.append(r["id"])
                if course is not None:
                    students = course_student_count(course)
                    n_rows_in_event = len(events[key])
                    per_row = students // n_rows_in_event if n_rows_in_event else students
                    state.release_venue(r["venue_id"], r["date"], r["start_time"], per_row, course)
        fixed += 1
    if drop_ids:
        ExamTempTimetable.objects.filter(id__in=drop_ids).delete()
    # Raw deletes above only fix the events we identified as duplicates;
    # cheap insurance against any other DB/state drift (e.g. from earlier
    # consolidation/swap passes) is a full resync right before the
    # diagnostic runs, so "no venue space" verdicts can actually be
    # trusted instead of reflecting stale bookkeeping.
    rebuild_state_from_db(state, all_courses)
    return fixed

def classify_courses(all_courses) -> Tuple[List, List]:
    ug, pg = [], []
    for c in all_courses:
        code = getattr(c, "course_code", "") or ""
        (pg if is_postgraduate_course(code) else ug).append(c)
    return ug, pg

def _build_shared_venue_exam_groups() -> int:
    slot_map = defaultdict(list)
    entries = list(ExamTempTimetable.objects.values("id", "venue_id", "date", "start_time", "end_time", "course_allocation_id", "day"))
    for e in entries:
        slot_map[(e["venue_id"], e["date"], e["start_time"])].append(e)
    created = 0
    for (vid, date_val, start_val), group_entries in slot_map.items():
        if len(group_entries) < 2:
            continue
        try:
            with transaction.atomic():
                from room_management.models import Venue as _Venue
                from course_allocation.models import CourseAllocation as _CA
                venue_obj = _Venue.objects.get(pk=vid)
                svg, _ = SharedVenueExamGroup.objects.get_or_create(
                    venue=venue_obj, date=date_val, start_time=start_val,
                    defaults={"day": group_entries[0]["day"], "end_time": group_entries[0]["end_time"]}
                )
                cids = [e["course_allocation_id"] for e in group_entries]
                svg.course_allocations.set(_CA.objects.filter(pk__in=cids))
                svg.total_students = sum(ca.number_of_students for ca in _CA.objects.filter(pk__in=cids))
                svg.save()
                created += 1
        except Exception as exc:
            print(f"[SharedVenueGroup] Error: {exc}")
    return created

# ======================================================================
# SECTION 12 – Main Entry Point
# ======================================================================

# ======================================================================
# Phase timing instrumentation
# ======================================================================
# Wraps each top-level phase call so the log shows exactly how long every
# phase took and, if a phase is unusually slow, when it started/ended —
# so a "stuck on phase 9" report can be checked against real numbers
# instead of guesswork about whether it's a loop or just a slow pass.

class _PhaseTimer:
    """Context manager + running history for per-phase timing.

    Usage:
        with _PhaseTimer("Phase9a-VenueConsolidation"):
            consolidate_underfilled_small_venues_pass(...)

    Every phase's elapsed time is recorded in `_phase_history` so a full
    summary table (slowest phase first) can be printed once at the end of
    the run via `_PhaseTimer.summary()`. This never changes scheduling
    behaviour — it only measures and logs.
    """
    _phase_history: List[Tuple[str, float]] = []
    _run_start: Optional[float] = None

    def __init__(self, name: str):
        self.name = name
        self.start = 0.0

    def __enter__(self):
        if _PhaseTimer._run_start is None:
            _PhaseTimer._run_start = time.time()
        self.start = time.time()
        since_start = self.start - _PhaseTimer._run_start
        print(f"[TIMING] >>> {self.name} started "
              f"(t+{since_start:.1f}s since run began)")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = time.time() - self.start
        _PhaseTimer._phase_history.append((self.name, elapsed))
        if exc_type is not None:
            print(f"[TIMING] <<< {self.name} RAISED {exc_type.__name__} "
                  f"after {elapsed:.2f}s")
        elif elapsed > 30:
            print(f"[TIMING] <<< {self.name} finished in {elapsed:.2f}s "
                  f"⚠ SLOW (>30s) — check for an unbounded/looping pass here")
        else:
            print(f"[TIMING] <<< {self.name} finished in {elapsed:.2f}s")
        return False  # never suppress exceptions

    @classmethod
    def reset(cls):
        cls._phase_history = []
        cls._run_start = None

    @classmethod
    def summary(cls):
        if not cls._phase_history:
            return
        total = sum(t for _, t in cls._phase_history) or 1e-9
        print("\n" + "=" * 60)
        print("  PHASE TIMING SUMMARY (slowest first)")
        print("=" * 60)
        for name, t in sorted(cls._phase_history, key=lambda x: -x[1]):
            pct = (t / total) * 100
            bar = "#" * max(1, int(pct / 2))
            flag = "  <-- dominant phase" if pct > 40 else ""
            print(f"  {name:<32} {t:>8.2f}s  {pct:5.1f}%  {bar}{flag}")
        print(f"  {'TOTAL':<32} {total:>8.2f}s")
        print("=" * 60)


def run_optimized_autoscheduler_thread(disabled_constraints: Optional[Set[str]] = None, tt_scope: Optional[dict] = None):
    disabled_constraints = disabled_constraints or set()
    total_courses = 0
    init_logger()
    _PhaseTimer.reset()
    print(f"[AutoScheduler v63] Log file: {get_log_file_path()}")
    try:
        enable_wal_mode()
        with transaction.atomic():
            ExamTempTimetable.objects.all().delete()
            MergedCourseGroup.objects.all().delete()
            SharedVenueExamGroup.objects.all().delete()
            _already_scheduled_cache.clear()
            _bulk_buffer.clear()
        config = ExamSchedulerConfig.objects.first()
        if not config:
            close_logger()
            return {
                "status": "error",
                "message": "No ExamSchedulerConfig found.",
                "scheduled_count": 0,
                "remaining_count": 0,
            }
        raw_courses = list(
            CourseAllocation.objects
            .filter(tt_scope_q(tt_scope))
            .select_related("lecturer", "program", "department",
                            "program_course", "selection_group",
                            "specialization_stem", "specialization_stem__category")
        )
        total_raw = len(raw_courses)
        _build_combined_group_cache()
        analysis = analyze_courses(raw_courses)
        seen = set()
        all_courses = []
        for c in raw_courses:
            if c.id not in seen:
                seen.add(c.id)
                all_courses.append(c)
        total_courses = len(all_courses)
        if total_courses == 0:
            close_logger()
            return {"status": "completed", "message": "No courses to schedule."}
        _raw_venues = list(Venue.objects.filter(capacity__isnull=False, capacity__gt=0))
        psi = PreSchedulingIntelligence(all_courses, analysis, config, _raw_venues)
        strategy = psi.run()
        state = SchedulerState(config, analysis, strategy, disabled_constraints)
        state._cross_cohort_norm_codes = set(analysis.shared_unit_groups.keys())
        if not state.date_range or not state.venues or not state.slots:
            close_logger()
            return {"status": "error", "message": "Invalid config."}
        scheduled_ids: Set[int] = set()
        print(f"\n[AutoScheduler v63] {total_courses} courses, {len(state.venues)} venues")
        print(f"[AutoScheduler v63] CORRECT STEM-AWARE COLLISION LOGIC enabled")
        print(f"[AutoScheduler v63] Same course codes always grouped as families")
        print(f"[AutoScheduler v63] Different codes from different stems can share slots")
        print(f"[AutoScheduler v63] FAMILY PARTIAL-TOGETHER PLACEMENT enabled")
        print(f"[AutoScheduler v63] IMMEDIATE FAMILY EXHAUSTION enabled")
        print(f"[AutoScheduler v63] DYNAMIC DAILY LIMITS: Soft=2, Hard=3 (avoids 4)")
        print(f"[AutoScheduler v63] STRICT CAPACITY: No overflow allowed")
        print(f"[AutoScheduler v63] BEST-FIT: Smallest venue that fits")
        with _PhaseTimer("PhaseA-SmallFirst"):
            p_small_placed = schedule_small_courses_in_small_venues(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        sync_lecturer_busy_from_db(state, all_courses)
        with _PhaseTimer("PhaseB-CommonLast"):
            p_common_placed = schedule_common_courses_priority_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        sync_lecturer_busy_from_db(state, all_courses)
        with _PhaseTimer("Phase1-FamiliesFirst"):
            p1_placed = schedule_families_first(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        sync_lecturer_busy_from_db(state, all_courses)
        with _PhaseTimer("Phase0-Designated"):
            p0_placed = designated_venue_priority_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        with _PhaseTimer("PhaseC-Saturation"):
            schedule_saturation_loop(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        sync_lecturer_busy_from_db(state, all_courses)
        with _PhaseTimer("Phase3-CrossDayFill"):
            p3_placed = cross_day_fill_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        with _PhaseTimer("Phase3b-FamilyRescue"):
            p3b_placed = family_split_rescue_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        for _nc, _cids in state.analysis.shared_unit_groups.items():
            if any(_cid not in scheduled_ids for _cid in _cids):
                state.family_exhausted.add(_nc)
        with _PhaseTimer("Phase4-ForcedFallback"):
            p4_placed = forced_fallback_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        with _PhaseTimer("Phase5-DBFallback"):
            p5_placed = db_driven_fallback_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        p7_placed = 0
        if len(all_courses) - len(scheduled_ids) > 0:
            with _PhaseTimer("Phase7-Nuclear"):
                p7_placed = nuclear_fallback_pass(all_courses, state, scheduled_ids)
            sync_scheduled_ids_from_db(scheduled_ids)
        p8_placed = 0
        if len(all_courses) - len(scheduled_ids) > 0:
            with _PhaseTimer("Phase8-Ultimate"):
                p8_placed = ultimate_fallback_pass(all_courses, state, scheduled_ids)
            sync_scheduled_ids_from_db(scheduled_ids)
        with _PhaseTimer("Phase9a-VenueConsolidation"):
            p9a_moves = consolidate_underfilled_small_venues_pass(all_courses, state, scheduled_ids)
        with _PhaseTimer("Phase9-SwapOptimization"):
            p9_swaps = post_placement_swap_optimization(all_courses, state, scheduled_ids)
        p9b_placed = 0
        if len(all_courses) - len(scheduled_ids) > 0:
            with _PhaseTimer("Phase9b-PostConsolidationRescue"):
                p9b_placed = post_consolidation_rescue_pass(all_courses, state, scheduled_ids)
        with _PhaseTimer("Audit-DuplicateFixes"):
            duplicate_fixes = _audit_and_fix_duplicate_placements(state, all_courses)
        with _PhaseTimer("Build-SharedVenueGroups"):
            _build_shared_venue_exam_groups()
        _PhaseTimer.summary()
        actual_scheduled = ExamTempTimetable.objects.values("course_allocation_id").distinct().count()
        remaining_count = total_courses - actual_scheduled
        if remaining_count > 0:
            diagnose_unscheduled_courses(all_courses, state, scheduled_ids, sample_size=60)
        lecturer_violations = _audit_lecturer_conflicts(state, all_courses)
        student_violations = _audit_student_conflicts(state, all_courses)
        venue_violations = _audit_venue_capacity_with_sharing(state, all_courses)
        if remaining_count == 0:
            message = f"SUCCESS! All {actual_scheduled} courses scheduled. Strategy={getattr(strategy, 'mode', 'NORMAL')}"
        else:
            message = f"Scheduled {actual_scheduled}/{total_courses}. {remaining_count} unscheduled."
        print(f"\n[AutoScheduler v63] {message}")
        print(f"[AutoScheduler v63] Lecturer violations: {lecturer_violations}")
        print(f"[AutoScheduler v63] Student violations: {student_violations}")
        print(f"[AutoScheduler v63] Venue capacity violations: {venue_violations}")
        print(f"[AutoScheduler v63] Post-placement swaps: {p9_swaps}")
        print(f"[AutoScheduler v63] Cross-building family splits: {len(state.cross_building_splits)}")
        if state.cross_building_splits:
            print("[AutoScheduler v63] These need manual review — no single building had "
                  "enough free capacity for the family at that slot:")
            for entry in state.cross_building_splits:
                print(f"    - {entry['family']} on {entry['date']} {entry['slot']}: "
                      f"{entry['buildings']} -> {entry['venues']}")
        result = {
            "status": "completed" if remaining_count == 0 else "partial",
            "message": message,
            "scheduled_count": actual_scheduled,
            "remaining_count": remaining_count,
            "log_file": get_log_file_path(),
            "phase_stats": {
                "small_courses_first": p_small_placed,
                "common_courses_last": p_common_placed,
                "families_first": p1_placed,
                "designated_venue": p0_placed,
                "saturation": schedule_saturation_loop.__name__,
                "fill": p3_placed,
                "family_rescue": p3b_placed,
                "forced": p4_placed,
                "db_fallback": p5_placed,
                "nuclear": p7_placed,
                "ultimate": p8_placed,
                "swap_optimization": p9_swaps,
                "post_consolidation_rescue": p9b_placed,
            },
            "audit": {
                "lecturer_violations": lecturer_violations,
                "student_violations": student_violations,
                "venue_capacity_violations": venue_violations,
                "duplicate_placements_fixed": duplicate_fixes,
            },
        }
        close_logger()
        return result
    except Exception as exc:
        import traceback
        print(f"[AutoScheduler v63] Fatal: {exc}\n{traceback.format_exc()}")
        _PhaseTimer.summary()  # show which phases finished (and how long) before the crash
        result = {
            "status": "error",
            "message": f"Scheduling failed: {exc}",
            "scheduled_count": 0,
            "remaining_count": total_courses,
            "log_file": get_log_file_path(),
        }
        close_logger()
        return result

# ======================================================================
# Aliases for compatibility
# ======================================================================

def designated_venue_priority_pass(all_courses, state, scheduled_ids):
    if not state.designated_venues_by_norm_code:
        return 0
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    placed = 0
    candidates = [
        c for c in all_courses
        if c.id not in scheduled_ids
        and normalize_course_code(getattr(c, "course_code", "") or "")
        in state.designated_venues_by_norm_code
        and normalize_course_code(getattr(c, "course_code", "") or "")
        not in state._cross_cohort_norm_codes
        and (
            normalize_course_code(getattr(c, "course_code", "") or "")
            not in state.analysis.shared_unit_groups
            or normalize_course_code(getattr(c, "course_code", "") or "")
            in state.family_exhausted
        )
    ]
    candidates.sort(key=lambda c: -state.priority_score(c))
    print(f"\n[Phase0-Designated] {len(candidates)} designated courses")
    for course in candidates:
        if course.id in scheduled_ids:
            continue
        if _course_already_in_db(course):
            scheduled_ids.add(course.id)
            continue
        nc = normalize_course_code(course.course_code or "")
        venue_ids = state.designated_venues_for_course(course)
        venues = [state.venue_by_id[vid] for vid in venue_ids if vid in state.venue_by_id]
        if not venues:
            continue
        needed = course_student_count(course)
        lid = state._cached_lecturer_id(course)
        placed_this = False
        sorted_dates = state.get_sorted_dates(course)
        for date_obj, _ in sorted_dates:
            if placed_this:
                break
            for ss, se in all_slots:
                if lid and not state.lecturer_available(lid, date_obj, ss, course):
                    continue
                if not state.students_available(course, date_obj, ss):
                    continue
                if state.check_norm_code_day_conflict(course, date_obj):
                    continue
                if state.check_shared_unit_conflict(course, date_obj, ss):
                    continue
                if state.check_family_conflict(course, date_obj, ss):
                    continue
                room = None
                for v in venues:
                    if state.venue_remaining(v.id, date_obj, ss) >= needed:
                        room = v
                        break
                if room is None:
                    venues_desc = sorted(
                        venues, key=lambda v: -state.venue_remaining(v.id, date_obj, ss)
                    )
                    total_rem = sum(state.venue_remaining(v.id, date_obj, ss) for v in venues_desc)
                    if total_rem >= needed and place_multi_venue(
                        course, venues_desc, date_obj, ss, se, state, scheduled_ids
                    ):
                        placed += 1
                        placed_this = True
                        print(f"  [Phase0-Split] {course.course_code} → {date_obj} {ss} "
                              f"across {[v.code for v in venues_desc]}")
                        break
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
                if not state.consume_venue(room.id, date_obj, ss, effective, course):
                    ExamTempTimetable.objects.filter(
                        course_allocation=course, date=date_obj, start_time=ss
                    ).delete()
                    continue
                state.record_combined_group_venue(course, date_obj, ss, room.id)
                state.mark_students_busy(course, date_obj, ss)
                state.mark_lecturer_busy(lid, date_obj, ss, course)
                state.bind_family(state.family_key(course), date_obj, ss)
                state.bind_shared_unit(course, date_obj, ss)
                state.bind_norm_code_day(course, date_obj)
                state.mark_cohort_scheduled(course, date_obj, ss)
                scheduled_ids.add(course.id)
                _already_scheduled_cache.add(course.id)
                placed += 1
                placed_this = True
                print(f"  [Phase0] {course.course_code} → {date_obj} {ss} {room.code}")
                break
        if not placed_this and nc in state.strict_norm_codes:
            state.strict_locked_ids.add(course.id)
            print(f"  [WARN] '{nc}' STRICTLY designated — left unscheduled")
    print(f"[Phase0-Designated] Placed {placed}")
    return placed