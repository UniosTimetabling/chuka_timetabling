"""
Exam Auto-Scheduler Module (v52 — Smart Split Minimization)
============================================================
NEW in v52 — Split Logic Refinements:
─────────────────────────────────────

  1. SPLIT ONLY AS LAST RESORT
     ─ A course is ONLY split when NO single venue can accommodate the
       entire course (even with near-fit tolerance).
     ─ Once split is necessary, use the FEWEST number of venues possible
       (largest venues first) to cover the needed seats.
     ─ Each fragment should be as large as possible (fill venues
       completely before moving to the next).

  2. NO SPLITTING TO FILL LEFTOVER SPACE
     ─ Leftover capacity in a venue should ONLY be used by a course that
       can fit ENTIRELY in that remaining space.
     ─ Never split a course just to fill a partially-used venue.
     ─ A course is either placed whole in a venue, or split across the
       minimum number of venues needed for its total size.

  3. SHARED COURSE SPLIT RULES
     ─ Only shared courses (same norm_code across multiple programs)
       are eligible for splitting.
     ─ Program-specific courses are NEVER split — they must fit in one venue.
     ─ When a shared course is split, all variants of that course
       (all program sections) are placed together in the same slots.

  4. FRAGMENT COUNT MINIMIZATION
     ─ Use the largest available venues first when splitting.
     ─ Each venue is filled to capacity before using the next.
     ─ Result: 100 students → 60 in venue A, 40 in venue B (2 fragments)
       NOT 10 venues with 10 students each.
"""

from __future__ import annotations

import datetime
import re
import logging
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
from course_allocation.models import CourseAllocation, CombinedCourseGroup
from room_management.models import Venue
from core import scheduling_constraints as constraint_engine

# ---------------------------------------------------------------------------
# DEBUG flag
# ---------------------------------------------------------------------------
DEBUG_VERBOSE: bool = False


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
    """Generate non-overlapping time slots with 60-minute gaps."""
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
    """Return the effective exam capacity for a venue."""
    ec = getattr(venue, "exam_capacity", None)
    phys = getattr(venue, "capacity", None)
    hard_cap = int(ec) if ec else (int(phys) if phys else 0)
    if hard_cap <= 0:
        return 0
    effective_ratio = min(float(spacing_ratio), 1.0)
    return max(1, int(hard_cap * effective_ratio))


@lru_cache(maxsize=4096)
def normalize_course_code(code: str) -> str:
    """Normalize course codes so ALL variants map to identical key."""
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
    """Return number of students for a course allocation."""
    return max(int(getattr(course, "number_of_students", 0) or 0), 1)


def family_total_students(group_courses: List) -> int:
    """Sum number_of_students from EVERY CourseAllocation variant in the group."""
    return sum(course_student_count(c) for c in group_courses)


def _prog_year_key(course) -> str:
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
    return getattr(alloc, 'intake', 'normal') or 'normal'


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

        key = f"__combined__{group.group_code}"
        combined_families[key] = alloc_ids

    print(f"[CombinedGroup] Loaded {len(groups)} combined groups")
    return combined_families


def _combined_group_ids_for(alloc_id: int) -> frozenset:
    return _combined_group_cache.get(alloc_id, frozenset())


def exam_is_collision_exempt(c1, c2) -> bool:
    """
    True if c1 and c2 are allowed to sit in the same exam slot for the
    same student cohort — i.e. a student can only ever be sitting ONE of
    the two, so there's no real clash.

    IMPORTANT: SelectionGroup ("choose exactly one course from this
    group") and the plain is_elective flag are NOT the same thing. Per
    CourseAllocation.clean(), selection_group can only be set on an
    elective allocation, but a course can be is_elective=True and belong
    to NO group at all (a standalone elective) — that course is not
    automatically a mutual alternative to every other elective or
    grouped course in the system. Two allocations are only guaranteed
    mutually exclusive (a student picks one, so no clash) when they sit
    in the SAME SelectionGroup. Checking "is either one elective" or "does
    either one have *some* selection_group" (regardless of which group)
    wrongly exempts unrelated courses from colliding with each other —
    e.g. two different electives from two different, unrelated selection
    groups, or an elective vs. a normal mandatory course the same
    students are also taking.
    """
    if _exam_get_intake(c1) != _exam_get_intake(c2):
        return True

    st1 = _exam_get_specialization_stem_id(c1)
    st2 = _exam_get_specialization_stem_id(c2)
    if st1 is not None and st2 is not None and st1 == st2:
        return False
    if st1 is not None and st2 is not None and st1 != st2:
        cat1 = _exam_get_specialization_category_id(c1)
        cat2 = _exam_get_specialization_category_id(c2)
        if cat1 is not None and cat1 == cat2:
            return True

    sg1 = _exam_get_selection_group_id(c1)
    sg2 = _exam_get_selection_group_id(c2)
    if sg1 is not None and sg2 is not None and sg1 == sg2:
        # Same SelectionGroup: a student chooses exactly one course from
        # this group, so these two allocations can never both be sat by
        # the same student — safe to schedule together.
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
    """Check if two courses belong to the same exam group."""
    if course_a.id == course_b.id:
        return True
    nc_a = normalize_course_code(getattr(course_a, "course_code", "") or "")
    nc_b = normalize_course_code(getattr(course_b, "course_code", "") or "")
    if nc_a and nc_b and nc_a == nc_b:
        return True
    if _combined_group_are_paired(course_a.id, course_b.id):
        return True
    return False


def _get_exam_group_key(course) -> str:
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
NEAR_FIT_THRESHOLD: int = 15
OVERFLOW_NEAR_FIT_THRESHOLD: int = 30


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

    # Count shared exams
    for norm_code, group in by_norm.items():
        programs = set()
        for c in group:
            prog = getattr(c, "program", None)
            if prog:
                programs.add(prog.id)
        report.shared_exams[norm_code] = len(programs)
        for c in group:
            report.course_program_count[c.id] = len(programs)

    # Identify course families
    for norm_code, group in by_norm.items():
        if len(group) < 2:
            continue
        true_total_students = family_total_students(group)
        report.family_total_students[norm_code] = true_total_students
        report.shared_unit_groups[norm_code] = [c.id for c in group]

    # CombinedCourseGroup families
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

    # Identify cohorts
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

    # Build conflict graph
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
        # norm_code -> list of (venue_ids: set[int], program_ids: set[int], course_ids: set[int])
        # scopes. A course is only allowed into a rule's venues if its
        # program_id or program_course_id matches that rule's scope — a
        # shared course code alone is NOT enough (see get_designated_venue_rules).
        self.designated_scope_by_norm_code: Dict[str, List[Tuple[Set[int], Set[int], Set[int]]]] = defaultdict(list)
        self.strict_norm_codes: Set[str] = set()
        self.strict_locked_ids: Set[int] = set()
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

        # Shared exam tracking
        self.highly_shared_exams: Set[str] = set()
        for norm_code, program_count in analysis.shared_exams.items():
            if program_count >= 3:
                self.highly_shared_exams.add(norm_code)

        # Venue usage with room sharing
        self.venue_usage: Dict[Tuple, int] = defaultdict(int)
        self.venue_occupants: Dict[Tuple, List[Tuple[int, int]]] = defaultdict(list)

        self._combined_group_venue: Dict[Tuple, int] = {}
        self._family_building: Dict[Tuple, str] = {}

        self.lecturer_busy: Dict[int, Set] = defaultdict(set)
        self.lecturer_exam_group: Dict[Tuple, str] = {}
        self.lecturer_blocked: Dict = {}

        self._py_busy: Dict[Tuple, Set[str]] = defaultdict(set)
        self._py_busy_allocs: Dict[Tuple, List] = {}

        self.family_slot: Dict[str, Tuple] = {}
        self.family_day: Dict[str, datetime.date] = {}
        self.shared_unit_lock: Dict[str, Tuple] = {}
        self.norm_code_day_lock: Dict[str, datetime.date] = {}
        self._cross_cohort_norm_codes: Set[str] = set()

        self.cohort_last_slot_idx: Dict[Tuple, int] = {}
        self.cohort_daily_count: Dict[Tuple, int] = defaultdict(int)
        self.daily_load: Dict[datetime.date, int] = defaultdict(int)

        self._fk_cache: Dict[int, str] = {}
        self._py_cache: Dict[int, str] = {}
        self._norm_code_cache: Dict[int, str] = {}
        self._lecturer_id_cache: Dict[int, Optional[int]] = {}
        self._priority_score_cache: Dict[int, float] = {}

        self.placed_families: Set[str] = set()

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

        self._sorted_dates = sorted(self.date_range, key=lambda dt: dt[0])
        self.lecturer_blocked = self._build_lecturer_blocked_map()

        # Initialize venue availability
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
        course_exam_group = _get_exam_group_key(course)
        return course_exam_group == current_exam_group

    def mark_lecturer_busy(self, lid, date, slot_start, course=None):
        if not lid:
            return
        self.lecturer_busy[lid].add((date, slot_start))
        if course:
            self.lecturer_exam_group[(lid, date, slot_start)] = _get_exam_group_key(course)

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
        if nc and nc in self._cross_cohort_norm_codes and nc not in self.shared_unit_lock:
            self.shared_unit_lock[nc] = (date, slot_start)

    def check_shared_unit_conflict(self, course, date, slot_start) -> bool:
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
        """
        Venue ids reserved for THIS specific allocation (by its program or
        program_course), not just any allocation sharing its course code.
        A common/shared course code that also belongs to other programs
        will correctly get [] here for those other programs' sections,
        so they fall through to the general venue pool instead of a venue
        reserved for a different program.
        """
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
        """
        Venue ids reserved for EVERY member of a merged/common-course
        family. If even one program's section in the group is not covered
        by the rule, the shared batch as a whole must not use that venue —
        only that program's own allocation would be eligible individually.
        """
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

    # ==================================================================
    # VENUE AVAILABILITY METHODS
    # ==================================================================

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

    def consume_venue(self, vid, date, slot_start, students: int, course=None):
        if students <= 0:
            return
        cap = self.venue_examcap.get(vid, 0)
        already_used = self.venue_usage.get((vid, date, slot_start), 0)
        
        if already_used + students > cap:
            students = max(0, cap - already_used)
            if students <= 0:
                return
        
        self.venue_usage[(vid, date, slot_start)] = already_used + students
        self._venue_avail[(vid, date, slot_start)] = max(0, cap - already_used - students)
        
        if course:
            self.venue_occupants[(vid, date, slot_start)].append((course.id, students))
        
        slot_key = (date, slot_start)
        self._day_slot_total[slot_key] = max(0, self._day_slot_total.get(slot_key, 0) - students)

    def slot_total_remaining(self, date, slot_start) -> int:
        return self._day_slot_total.get((date, slot_start), 0)

    def slot_has_any_venue_space(self, date, slot_start, needed: int = 1) -> bool:
        for v in self.venues:
            if self.venue_remaining(v.id, date, slot_start) >= needed:
                return True
        return False

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
        pk = self._py_key(course)
        if not pk:
            return False
        last_idx = self.cohort_last_slot_idx.get((pk, date))
        if last_idx is None:
            return False
        current_idx = self._slot_start_to_idx.get(slot_start)
        if current_idx is None:
            return False
        # Must be an ABSOLUTE gap: a cohort's exams on the same day can now be
        # seeded out of time order (Phase -1 fills each day's LAST slot
        # first), so "current - last" alone can go negative. A signed
        # comparison against a negative number is always <= gap, which was
        # locking every earlier slot on the day out for any cohort that got
        # a Phase -1 placement — exactly the "whole day empty except the
        # last slot" symptom. Comparing distance, not direction, fixes it.
        return abs(current_idx - last_idx) <= gap

    def mark_cohort_scheduled(self, course, date, slot_start):
        pk = self._py_key(course)
        if not pk:
            return
        idx = self._slot_start_to_idx.get(slot_start)
        if idx is not None:
            self.cohort_last_slot_idx[(pk, date)] = idx
        self.cohort_daily_count[(pk, date)] += 1

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


# ======================================================================
# SECTION 4 – Pre-Scheduling Intelligence (v50 - unchanged)
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
        self.near_fit_threshold = NEAR_FIT_THRESHOLD
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
            f"  PRE-SCHEDULING INTELLIGENCE — v52",
            f"══════════════════════════════════════════",
            f"  Strategy Mode  : {self.mode}",
            f"  Seat Pressure  : {self.seat_pressure:.1%}",
            f"  Near-Fit Thresh: {self.near_fit_threshold}",
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
            pk = _prog_year_key(c)
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
            s.near_fit_threshold = OVERFLOW_NEAR_FIT_THRESHOLD
        if s.infeasible_cohorts and s.mode != SchedulingStrategy.OVERFLOW:
            s.mode = SchedulingStrategy.DENSE
            s.use_evening_slots = True
            s.relax_consecutive = True


# ======================================================================
# SECTION 5 – VENUE SELECTION - SMART SPLIT MINIMIZATION
# ======================================================================

def _venue_building(venue) -> str:
    code = (getattr(venue, "code", None) or "").strip()
    return re.sub(r'[\d\s]+$', '', code).upper()


def find_best_venue_no_split(needed: int, date, slot_start, state: SchedulerState, 
                              course=None, near_fit_override: int = 0) -> Optional[Venue]:
    """
    Find the best venue for a course WITHOUT splitting.
    
    KEY RULES:
    1. Course must fit ENTIRELY in one venue
    2. Use the SMALLEST venue that fits (best-fit matching)
    3. Near-fit tolerance allows slight overflow (within threshold)
    4. Completely free venues are preferred, but partially used venues
       can be used if the course fits in the remaining space AND
       doesn't cause student conflicts
    5. NEVER split a course just to fill leftover space
    """
    threshold = near_fit_override or getattr(state.strategy, 'near_fit_threshold', NEAR_FIT_THRESHOLD)
    
    # TIER 1: Completely free venues - best fit (smallest that fits)
    free_venues = state.get_completely_free_venues(date, slot_start)
    free_venues_asc = sorted(free_venues, key=lambda x: x[1])
    
    for v, cap in free_venues_asc:
        if cap >= needed:
            return v
    
    # TIER 2: Near-fit - largest venue where overflow <= threshold
    for v, cap in reversed(free_venues_asc):
        overflow = needed - cap
        if 0 < overflow <= threshold:
            return v
    
    # TIER 3: Partially used venues (room sharing) - only if course fits entirely
    # in the remaining space AND no student conflict
    if course:
        free_with_occupants = []
        for v in state.venues_by_cap_desc:
            rem = state.venue_remaining(v.id, date, slot_start)
            if rem > 0 and state.venue_has_occupants(v.id, date, slot_start):
                # Check if this course can share with existing occupants
                occupants = state.get_venue_occupants(v.id, date, slot_start)
                can_share = True
                for occ_course_id in occupants:
                    occ_course = None
                    for c_list in state.analysis.courses_by_py.values():
                        for occ in c_list:
                            if occ.id == occ_course_id:
                                occ_course = occ
                                break
                        if occ_course:
                            break
                    if occ_course:
                        if normalize_course_code(getattr(course, "course_code", "") or "") == \
                           normalize_course_code(getattr(occ_course, "course_code", "") or ""):
                            continue
                        if _combined_group_are_paired(course.id, occ_course.id):
                            continue
                        if courses_share_students(course, occ_course):
                            can_share = False
                            break
                if can_share and rem >= needed:
                    free_with_occupants.append((v, rem))
        
        # Sort by remaining capacity (smallest that fits)
        free_with_occupants_asc = sorted(free_with_occupants, key=lambda x: x[1])
        for v, rem in free_with_occupants_asc:
            if rem >= needed:
                return v
    
    # TIER 4: Near-fit with partially used venues
    if course:
        for v, rem in sorted([(v, state.venue_remaining(v.id, date, slot_start)) 
                              for v in state.venues_by_cap_desc 
                              if state.venue_remaining(v.id, date, slot_start) > 0],
                             key=lambda x: -x[1]):
            overflow = needed - rem
            if 0 < overflow <= threshold:
                occupants = state.get_venue_occupants(v.id, date, slot_start)
                can_share = True
                for occ_course_id in occupants:
                    occ_course = None
                    for c_list in state.analysis.courses_by_py.values():
                        for occ in c_list:
                            if occ.id == occ_course_id:
                                occ_course = occ
                                break
                        if occ_course:
                            break
                    if occ_course:
                        if normalize_course_code(getattr(course, "course_code", "") or "") == \
                           normalize_course_code(getattr(occ_course, "course_code", "") or ""):
                            continue
                        if _combined_group_are_paired(course.id, occ_course.id):
                            continue
                        if courses_share_students(course, occ_course):
                            can_share = False
                            break
                if can_share:
                    return v
    
    return None


def find_minimal_split_venues(needed: int, date, slot_start, state: SchedulerState,
                               course=None) -> List[Venue]:
    """
    Find the MINIMUM number of venues needed to accommodate a course.
    
    KEY RULES:
    1. Use the FEWEST venues possible (largest venues first)
    2. Fill each venue to capacity before using the next
    3. Result: 100 students → [60-cap venue, 40-cap venue] (2 venues)
       NOT [10, 10, 10, ...] (10 venues)
    4. Only shared courses (same norm_code across programs) can be split
    5. Program-specific courses are NEVER split
    """
    if not course:
        return []
    
    # NEW v52: Check if this course is eligible for splitting
    nc = normalize_course_code(getattr(course, "course_code", "") or "")
    is_shared = nc in state.analysis.shared_unit_groups
    program_count = state.analysis.course_program_count.get(course.id, 1)
    
    # Only shared courses (>= 2 programs) can be split
    if not is_shared or program_count < 2:
        return []
    
    # Get all venues with available capacity, sorted by capacity descending
    available = []
    for v in state.venues_by_cap_desc:
        cap = state.venue_examcap.get(v.id, 0)
        if cap <= 0:
            continue
        rem = state.venue_remaining(v.id, date, slot_start)
        if rem <= 0:
            continue
        
        # Check if this course can share with existing occupants
        if state.venue_has_occupants(v.id, date, slot_start):
            occupants = state.get_venue_occupants(v.id, date, slot_start)
            can_share = True
            for occ_course_id in occupants:
                occ_course = None
                for c_list in state.analysis.courses_by_py.values():
                    for occ in c_list:
                        if occ.id == occ_course_id:
                            occ_course = occ
                            break
                    if occ_course:
                        break
                if occ_course:
                    if normalize_course_code(getattr(course, "course_code", "") or "") == \
                       normalize_course_code(getattr(occ_course, "course_code", "") or ""):
                        continue
                    if _combined_group_are_paired(course.id, occ_course.id):
                        continue
                    if courses_share_students(course, occ_course):
                        can_share = False
                        break
            if not can_share:
                continue
        
        available.append((v, rem))
    
    if not available:
        return []
    
    # Sort by capacity descending (largest first) to minimize fragment count
    available.sort(key=lambda x: -x[1])
    
    chosen, remaining_needed = [], needed
    for v, rem in available:
        if remaining_needed <= 0:
            break
        take = min(rem, remaining_needed)
        chosen.append(v)
        remaining_needed -= take
    
    if remaining_needed > 0:
        return []  # Not enough capacity even with splitting
    
    return chosen


def place_course_no_split(course, date, slot_start, slot_end,
                          state: SchedulerState, scheduled_ids: Set[int],
                          relax_consecutive=False) -> bool:
    """
    Place a course WITHOUT splitting - must fit entirely in one venue.
    """
    if course.id in scheduled_ids:
        return True
    
    needed = course_student_count(course)
    
    # Check hard constraints
    reason = _check_hard_constraints(course, date, slot_start, state)
    if reason:
        return False
    if not relax_consecutive and state.cohort_in_cooling(course, date, slot_start):
        return False
    if _course_already_in_db(course):
        scheduled_ids.add(course.id)
        return True
    
    # Try preferred venue from combined group
    preferred_vid = state.preferred_combined_venue_id(course, date, slot_start)
    if preferred_vid is not None:
        preferred_venue = state.venue_by_id.get(preferred_vid)
        if preferred_venue is not None and state.venue_remaining(preferred_vid, date, slot_start) >= needed:
            if place_single(course, preferred_venue, date, slot_start, slot_end,
                            state, scheduled_ids, relax_consecutive=relax_consecutive,
                            allow_room_sharing=True, allow_split=False):
                return True
    
    # Find best venue without splitting
    venue = find_best_venue_no_split(needed, date, slot_start, state, course)
    if venue and place_single(course, venue, date, slot_start, slot_end,
                              state, scheduled_ids, relax_consecutive=relax_consecutive,
                              allow_room_sharing=True, allow_split=False):
        return True
    
    return False


def place_course_with_minimal_split(course, date, slot_start, slot_end,
                                     state: SchedulerState, scheduled_ids: Set[int],
                                     relax_consecutive=False) -> bool:
    """
    Place a course with MINIMAL splitting (only if absolutely necessary).
    
    KEY RULES:
    1. First try to place without splitting (find_best_venue_no_split)
    2. Only split if NO single venue can fit the course
    3. When splitting, use the MINIMUM number of venues (largest first)
    4. Fill each venue to capacity before using the next
    5. Only shared courses are eligible for splitting
    """
    if course.id in scheduled_ids:
        return True
    
    # First try: no split
    if place_course_no_split(course, date, slot_start, slot_end, state, scheduled_ids, relax_consecutive):
        return True
    
    # Second try: minimal split (only for shared courses)
    needed = course_student_count(course)
    nc = normalize_course_code(getattr(course, "course_code", "") or "")
    is_shared = nc in state.analysis.shared_unit_groups
    program_count = state.analysis.course_program_count.get(course.id, 1)
    
    # Only shared courses can be split
    if not is_shared or program_count < 2:
        return False
    
    # Check hard constraints again (they may have changed)
    if _check_hard_constraints(course, date, slot_start, state):
        return False
    if not relax_consecutive and state.cohort_in_cooling(course, date, slot_start):
        return False
    
    # Find minimal venues for split
    venues = find_minimal_split_venues(needed, date, slot_start, state, course)
    if not venues:
        return False
    
    # Place using the minimal venues
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


def _post_place(course, venue_id: int, students: int,
                date, slot_start, slot_end,
                state: SchedulerState, scheduled_ids: Set[int]):
    if students > 0:
        state.consume_venue(venue_id, date, slot_start, students, course)
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


def _check_hard_constraints(course, date, slot_start, state: SchedulerState) -> Optional[str]:
    if course.id in state.strict_locked_ids:
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
    return None


def place_single(course, venue, date, slot_start, slot_end,
                  state: SchedulerState, scheduled_ids: Set[int],
                  relax_consecutive=False, ignore_capacity=False,
                  allow_room_sharing=True, allow_split=False) -> bool:
    """
    Place a single course in one venue.
    
    allow_split=False: Will NOT split this course (used for program-specific courses)
    allow_split=True: May split if absolutely necessary (used for shared courses)
    """
    if course.id in scheduled_ids:
        return True
    
    needed = course_student_count(course)
    cap = state.venue_examcap.get(venue.id, 0)
    rem = state.venue_remaining(venue.id, date, slot_start)
    
    # Check if venue has occupants
    has_occupants = state.venue_has_occupants(venue.id, date, slot_start)
    
    # Capacity check
    if not ignore_capacity:
        if rem < needed:
            return False
        
        # If room has occupants, check sharing constraints
        if has_occupants and allow_room_sharing:
            occupants = state.get_venue_occupants(venue.id, date, slot_start)
            for occ_course_id in occupants:
                occ_course = None
                for c_list in state.analysis.courses_by_py.values():
                    for occ in c_list:
                        if occ.id == occ_course_id:
                            occ_course = occ
                            break
                    if occ_course:
                        break
                if occ_course:
                    if normalize_course_code(getattr(course, "course_code", "") or "") == \
                       normalize_course_code(getattr(occ_course, "course_code", "") or ""):
                        continue
                    if _combined_group_are_paired(course.id, occ_course.id):
                        continue
                    if courses_share_students(course, occ_course):
                        return False
    
    # Hard constraints
    reason = _check_hard_constraints(course, date, slot_start, state)
    if reason:
        return False
    if not relax_consecutive and state.cohort_in_cooling(course, date, slot_start):
        return False
    
    if _course_already_in_db(course):
        scheduled_ids.add(course.id)
        return True
    
    effective_seats = min(needed, cap) if cap > 0 else needed
    
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
        return False
    
    _post_place(course, venue.id, effective_seats, date, slot_start, slot_end, state, scheduled_ids)
    return True


def place_multi_venue(course, venues, date, slot_start, slot_end,
                       state: SchedulerState, scheduled_ids: Set[int],
                       relax_consecutive=False, allow_room_sharing=True) -> bool:
    """
    Place a course across MULTIPLE venues (MINIMAL splitting).
    
    KEY: Use the fewest venues possible (venues are already sorted largest first).
    """
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
    
    # Assign students to venues, filling each to capacity
    assignments, remaining = [], needed
    for v in venues:
        if remaining <= 0:
            break
        cap = state.venue_examcap.get(v.id, 0)
        rem = state.venue_remaining(v.id, date, slot_start)
        
        # Check sharing constraints if venue has occupants
        if allow_room_sharing and state.venue_has_occupants(v.id, date, slot_start):
            occupants = state.get_venue_occupants(v.id, date, slot_start)
            can_share = True
            for occ_course_id in occupants:
                occ_course = None
                for c_list in state.analysis.courses_by_py.values():
                    for occ in c_list:
                        if occ.id == occ_course_id:
                            occ_course = occ
                            break
                    if occ_course:
                        break
                if occ_course:
                    if normalize_course_code(getattr(course, "course_code", "") or "") == \
                       normalize_course_code(getattr(occ_course, "course_code", "") or ""):
                        continue
                    if _combined_group_are_paired(course.id, occ_course.id):
                        continue
                    if courses_share_students(course, occ_course):
                        can_share = False
                        break
            if not can_share:
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
        state.consume_venue(v.id, date, slot_start, students, course)
    
    scheduled_ids.add(course.id)
    _already_scheduled_cache.add(course.id)
    return True


def try_place_course(course, date, slot_start, slot_end,
                      state: SchedulerState, scheduled_ids: Set[int],
                      relax_consecutive=False, allow_split=False,
                      allow_room_sharing=True) -> bool:
    """
    Try to place a course with minimal splitting.
    
    allow_split=False: Never split (for program-specific courses)
    allow_split=True: Only split if absolutely necessary (for shared courses)
    """
    nc = normalize_course_code(course.course_code or "")
    if nc in state.analysis.shared_unit_groups:
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
# SECTION 7 – Family Placement (Shared Course Splitting)
# ======================================================================

def _family_constraints_ok(group_courses, date, slot_start, state):
    for c in group_courses:
        if c.id in state.strict_locked_ids:
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
    """
    Place a merged family (shared course across programs).
    
    KEY RULES for shared course splitting:
    1. Try to fit the ENTIRE family in one venue (no split)
    2. If no single venue fits, split across the MINIMUM number of venues
    3. Use largest venues first, fill each to capacity
    4. All variants of the course are placed together in the same slots
    """
    group_courses = [c for c in group_courses if c.id not in scheduled_ids]
    if not group_courses:
        state.placed_families.add(nc)
        return True
    
    total_needed = family_total_students(group_courses)
    
    if DEBUG_VERBOSE:
        print(f"\n[Family] '{nc}' | {len(group_courses)} variants | total={total_needed}")
    
    if not _family_constraints_ok(group_courses, date, ss, state):
        return False
    
    # Check designated venues first — but ONLY venues every member of this
    # merged family is actually scoped to (see designated_venues_for_family).
    # A venue reserved for one program (e.g. S3 reserved for Law) must never
    # absorb a shared course's OTHER program sections just because the
    # course code matches; those sections are not eligible here and this
    # block is skipped for them, falling through to the general pool below.
    venue_ids = state.designated_venues_for_family(group_courses)
    if venue_ids:
        is_strict = nc in state.strict_norm_codes
        if not is_strict or not state.venue_has_occupants(venue_ids[0], date, ss):
            for vid in venue_ids:
                v = state.venue_by_id.get(vid)
                if v and state.venue_remaining(vid, date, ss) >= total_needed:
                    return _commit_single_venue(group_courses, nc, v, total_needed,
                                                date, ss, se, state, scheduled_ids)
        if is_strict:
            return False
    
    # Strategy A: Find ONE venue that can fit the entire family
    free_venues = state.get_free_venues(date, ss)
    free_with_caps = []
    for v, rem in free_venues:
        cap = state.venue_examcap.get(v.id, 0)
        free_with_caps.append([v, rem, cap])
    
    # Check building preference
    known_building = state.preferred_family_building(nc, date, ss)
    if known_building:
        free_with_caps = sorted(
            free_with_caps,
            key=lambda row: 0 if _venue_building(row[0]) == known_building else 1,
        )
    
    # Try to find a single venue for the whole family
    for row in free_with_caps:
        v, remaining, cap = row
        if remaining >= total_needed:
            return _commit_single_venue(group_courses, nc, v, total_needed,
                                        date, ss, se, state, scheduled_ids)
        overflow = total_needed - cap
        if 0 < overflow <= getattr(state.strategy, 'near_fit_threshold', NEAR_FIT_THRESHOLD):
            return _commit_single_venue(group_courses, nc, v, total_needed,
                                        date, ss, se, state, scheduled_ids)
    
    # Strategy B: Split the family across venues (MINIMAL split)
    # Only if the family is shared across programs
    program_count = state.analysis.shared_exams.get(nc, 1)
    if program_count < 2:
        return False
    
    total_free = sum(row[1] for row in free_with_caps)
    if total_free >= total_needed:
        return _commit_distributed_minimal(group_courses, nc, free_with_caps, total_needed,
                                           date, ss, se, state, scheduled_ids,
                                           preferred_building=known_building)
    
    return False


def _commit_single_venue(group_courses, nc, venue, total_needed,
                          date, ss, se, state, scheduled_ids):
    """Commit all variants to a single venue."""
    cap = state.venue_examcap.get(venue.id, 0)
    remaining = state.venue_remaining(venue.id, date, ss)
    
    if remaining < total_needed:
        return False
    
    effective_seats = min(total_needed, cap) if cap > 0 else total_needed
    
    # Check sharing constraints if venue already has occupants
    if state.venue_has_occupants(venue.id, date, ss):
        occupants = state.get_venue_occupants(venue.id, date, ss)
        for course in group_courses:
            for occ_course_id in occupants:
                occ_course = None
                for c_list in state.analysis.courses_by_py.values():
                    for occ in c_list:
                        if occ.id == occ_course_id:
                            occ_course = occ
                            break
                    if occ_course:
                        break
                if occ_course:
                    if normalize_course_code(getattr(course, "course_code", "") or "") == \
                       normalize_course_code(getattr(occ_course, "course_code", "") or ""):
                        continue
                    if _combined_group_are_paired(course.id, occ_course.id):
                        continue
                    if courses_share_students(course, occ_course):
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
    
    state.consume_venue(venue.id, date, ss, effective_seats, group_courses[0])
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
    return True


def _commit_distributed_minimal(group_courses, nc, pool, total_needed,
                                 date, ss, se, state, scheduled_ids,
                                 consume_fn=None, preferred_building=None):
    """
    Distribute a family across venues with MINIMAL splitting.
    
    KEY: Use the fewest venues possible. Fill each venue to capacity
    before using the next. All variants are placed together.
    """
    consume = consume_fn or state.consume_venue
    
    # Sort venues by capacity descending (largest first)
    pool_sorted = sorted(pool, key=lambda row: -row[2])  # row = [v, rem, cap]
    
    # Organize by building preference
    if preferred_building:
        same = [r for r in pool_sorted if _venue_building(r[0]) == preferred_building]
        other = [r for r in pool_sorted if _venue_building(r[0]) != preferred_building]
        pool_sorted = same + other
    
    # Assign variants to venues using BEST-FIT: each course goes whole into the
    # smallest venue that can still hold it. A course is only split across
    # multiple venues when NO single venue in the pool has enough remaining
    # capacity to take it whole. This is what the old streaming/pool_idx
    # approach got wrong: it filled whatever venue happened to be "current"
    # and only ever moved forward, so a small course landing on a
    # partially-filled venue got sliced up even when an untouched, big-enough
    # room was sitting later in the pool.
    assignments = []  # (course, venue, seats)
    remaining_courses = sorted(group_courses, key=lambda c: -course_student_count(c))
    
    for course in remaining_courses:
        needed = course_student_count(course)
        
        # --- Try to fit this course WHOLE into a single venue first ---
        # Pick the smallest venue that still has enough remaining space, so
        # large venues are conserved for courses/variants that actually need them.
        best_idx = None
        for idx, row in enumerate(pool_sorted):
            rem = row[1]
            if rem >= needed:
                if best_idx is None or rem < pool_sorted[best_idx][1]:
                    best_idx = idx
        
        if best_idx is not None:
            pool_sorted[best_idx][1] -= needed
            assignments.append((course, pool_sorted[best_idx][0], needed))
            continue
        
        # --- Genuinely doesn't fit anywhere whole: split as a last resort ---
        # Drain the venues with the MOST remaining space first, so we use the
        # fewest possible venues for the split (minimal fragmentation).
        needed_left = needed
        for row in sorted(pool_sorted, key=lambda r: -r[1]):
            if needed_left <= 0:
                break
            rem = row[1]
            if rem <= 0:
                continue
            take = min(rem, needed_left)
            assignments.append((course, row[0], take))
            row[1] -= take
            needed_left -= take
        
        if needed_left > 0:
            return False  # Not enough capacity anywhere in the pool
    
    # Commit all assignments
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
    
    # Update state
    for course, venue, seats in assignments:
        consume(venue.id, date, ss, seats, course)
        state.record_combined_group_venue(course, date, ss, venue.id)
        state.mark_students_busy(course, date, ss)
        lid = state._cached_lecturer_id(course)
        state.mark_lecturer_busy(lid, date, ss, course)
        state.bind_family(state.family_key(course), date, ss)
        state.bind_shared_unit(course, date, ss)
        state.bind_norm_code_day(course, date)
        state.mark_cohort_scheduled(course, date, ss)
        scheduled_ids.add(course.id)
        _already_scheduled_cache.add(course.id)
        state.record_family_building(nc, date, ss, _venue_building(venue))
    
    state.daily_load[date] += 1
    state.shared_unit_lock[nc] = (date, ss)
    state.norm_code_day_lock[nc] = date
    
    if DEBUG_VERBOSE:
        venues_used = {a[1].code: a[2] for a in assignments}
        print(f"  [Family-Split] '{nc}' → {len(set(a[1].id for a in assignments))} venues: {venues_used}")
    return True


# ======================================================================
# SECTION 8 – Scheduling Phases
# ======================================================================

def schedule_common_courses_priority_pass(all_courses, state, scheduled_ids):
    """
    Phase 0 (NEW) — Common-course-first, last-slot-inward placement.

    "Common" courses are course-code families with 2+ members in
    state.analysis.shared_unit_groups — i.e. the same course code repeated
    either across different programs OR repeated within the same program.
    Families are ranked by how many times they repeat (most-repeated
    first); ties broken by total enrolled students.

    Slot order: start at the LAST slot of the timetable day (which, by
    construction, is always the last evening slot — see
    SchedulerState.all_slots_ordered) and work inward one slot at a time
    towards the first morning slot. Only ONE slot "column" is worked at a
    time; every other slot is left untouched until this one is exhausted.

    Within a slot column:
      - Walk the days first → last, packing as many common-course families
        as will fit into that (day, slot) — several families can share a
        single slot across different venues as long as none of them
        collide (see _family_constraints_ok: no lecturer double-booked on
        two DIFFERENT courses at once, same-family/same-course repeats are
        fine, no student-cohort collision).
      - After a full day-by-day sweep, if anything got placed, sweep the
        days again from the top — a family that didn't fit earlier in the
        column may fit now that other families vacated space or once the
        list has shrunk. Keep re-sweeping until one full pass places
        nothing more.
      - When a family at the front of the priority list can't fit in a
        given (day, slot), it is skipped (not removed) so lower-priority
        families behind it still get a chance to fill that same slot.
    Only once a slot column is fully exhausted (no family fits anywhere in
    it, on any day) does the pass move one slot inward (day-time slots are
    reached only after every evening slot has been tried this way).

    Venue placement (via place_merged_family) never lands a family in a
    blocked/exclusive venue unless every member of that family is
    individually scoped to it (see designated_venues_for_family) — so a
    room reserved for one program's course never absorbs another
    program's section of a same-named shared course, even if the room
    would otherwise be big enough.
    """
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

    # Most-repeated family first (across programs OR within one program);
    # ties broken by total enrolled students.
    pending.sort(key=lambda kv: (-len(kv[1]), -family_total_students(kv[1])))

    print(f"\n[Phase0-CommonFirst] {len(pending)} common-course families, "
          f"working {len(all_slots)} slots from last to first")
    placed_total = 0

    for round_idx in range(len(all_slots) - 1, -1, -1):
        if not pending:
            break
        ss, se = all_slots[round_idx]

        made_progress_this_round = True
        while pending and made_progress_this_round:
            made_progress_this_round = False
            for date_obj, _ in dates:
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
                            print(f"  [Phase0-CommonFirst] '{nc}' (x{len(group)}) -> "
                                  f"{date_obj} {ss}")
                            break
                        # else: this family refuses this slot — leave it in
                        # place and let the next (lower-priority) family in
                        # the list get a chance to fit here instead.

    remaining = len(pending)
    if remaining:
        print(f"[Phase0-CommonFirst] {remaining} common families still pending "
              f"after all slots — later phases will retry them")
    print(f"[Phase0-CommonFirst] Placed {placed_total} variants")
    return placed_total


def schedule_families_first(all_courses, state, scheduled_ids):
    """Phase 1: Schedule all families first."""
    if not state.analysis.shared_unit_groups:
        return 0
    
    course_by_id = {c.id: c for c in all_courses}
    dates = state.dates_in_order()
    total_days = len(dates)
    
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
        
        # Try evening slots for highly shared exams
        if state.analysis.shared_exams.get(nc, 0) >= 3 and state.evening_slots:
            for date_obj, _ in dates:
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
        
        # Try all slots
        if not family_placed:
            for date_obj, _ in dates:
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
        
        # Try to place with minimal splitting (only if shared)
        should_allow_split = allow_split and nc in state.analysis.shared_unit_groups
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
                should_allow_split = allow_split and nc in state.analysis.shared_unit_groups
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
    
    print(f"\n[Phase2] Saturation loop")
    max_passes = 6 if getattr(strategy, 'mode', 'NORMAL') in ('DENSE', 'OVERFLOW') else 4
    
    for pass_idx in range(max_passes):
        placed_this_pass = 0
        for day_idx, (date_obj, _) in enumerate(dates):
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
                                   allow_split=(pass_idx >= 2))  # Only allow split after 2 passes
            placed_this_pass += n
            if n > 0:
                print(f"  Pass {pass_idx+1} Day {day_idx+1}: +{n}")
        print(f"[Phase2] Pass {pass_idx+1}: +{placed_this_pass}")
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
    
    # Try family rescue
    for nc, cids in state.analysis.shared_unit_groups.items():
        if nc in state.placed_families:
            continue
        stuck = [course_by_id[cid] for cid in cids if cid in course_by_id and cid not in scheduled_ids]
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
    
    # Individual courses
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
                                    relax_consecutive=True, allow_split=False,
                                    allow_room_sharing=True):
                    placed += 1
                    if pk:
                        placed_in_slot[pk].append(course)
    
    print(f"[Phase3] Placed {placed}")
    return placed


def family_split_rescue_pass(all_courses, state, scheduled_ids):
    """Phase 3b: Rescue family variants with minimal splitting."""
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
        for date_obj, _ in dates:
            if rescued:
                break
            for ss, se in state.daytime_slots_list + state.evening_slots:
                if rescued:
                    break
                
                if not _family_constraints_ok(variants, date_obj, ss, state):
                    continue
                
                # Try to place as a merged family first
                if place_merged_family(variants, nc, date_obj, ss, se, state, scheduled_ids):
                    placed_total += len(variants)
                    rescued = True
                    print(f"  [Phase3b] '{nc}' → {date_obj} {ss}")
                    break
                
                # If merged family didn't work, try individual placement with split
                for course in variants:
                    if course.id in scheduled_ids:
                        continue
                    if try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                        relax_consecutive=True, allow_split=True,
                                        allow_room_sharing=True):
                        placed_total += 1
        
        if not rescued:
            print(f"  [WARN] '{nc}' STILL unplaced")
    
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
        
        # Try family re-merge on sweep 3
        if sweep == 3:
            for nc, cids in state.analysis.shared_unit_groups.items():
                if nc in state.placed_families:
                    continue
                group_unsched = [c for c in all_courses if c.id in cids and c.id not in scheduled_ids]
                if not group_unsched:
                    state.placed_families.add(nc)
                    continue
                for date_obj, _ in dates:
                    if nc in state.placed_families:
                        break
                    for ss, se in all_slots:
                        if nc in state.placed_families:
                            break
                        if place_merged_family(group_unsched, nc, date_obj, ss, se, state, scheduled_ids):
                            state.placed_families.add(nc)
        
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
                    
                    should_allow_split = (sweep >= 4) and nc in state.analysis.shared_unit_groups
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
    
    for date_idx, (date_obj, _) in enumerate(dates):
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
                if _check_hard_constraints(course, date_obj, ss, state):
                    continue
                
                should_allow_split = nc in state.analysis.shared_unit_groups
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
                if not state.slot_has_any_venue_space(date_obj, ss, needed):
                    continue
                if _check_hard_constraints(course, date_obj, ss, state):
                    continue
                
                should_allow_split = nc in state.analysis.shared_unit_groups
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


def diagnose_unscheduled_courses(all_courses, state, scheduled_ids, sample_size: int = 25) -> None:
    """
    Explains WHY specific unscheduled courses can't be placed, by walking every
    (date, slot) combination and reporting exactly which check rejected it.

    This is read-only — it does not place anything or mutate state. It exists
    to answer "there's free venues/slots, why is this course still stuck?"
    without guessing.
    """
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return

    unscheduled = sorted(unscheduled, key=lambda c: -state.priority_score(c))[:sample_size]
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered

    print(f"\n══════════════════════════════════════════")
    print(f"  DIAGNOSTIC — why are courses still stuck?")
    print(f"  (sampling {len(unscheduled)} of {len(unscheduled)} unscheduled)")
    print(f"══════════════════════════════════════════")

    for course in unscheduled:
        needed = course_student_count(course)
        lid = state._cached_lecturer_id(course)
        nc = normalize_course_code(getattr(course, "course_code", "") or "")

        reason_counts: Dict[str, int] = defaultdict(int)
        slots_with_space = 0
        first_space_no_block = None

        for date_obj, _ in dates:
            for ss, se in all_slots:
                if not state.slot_has_any_venue_space(date_obj, ss, needed):
                    reason_counts["no-venue-space-for-full-count"] += 1
                    continue

                slots_with_space += 1
                reason = _check_hard_constraints(course, date_obj, ss, state)
                if reason:
                    reason_counts[reason] += 1
                    continue

                if lid and not state.lecturer_available(lid, date_obj, ss, course):
                    reason_counts["lecturer-conflict"] += 1
                    continue

                if first_space_no_block is None:
                    first_space_no_block = (date_obj, ss)

        print(f"\n[{course.course_code}] id={course.id} needed={needed} "
              f"lecturer_id={lid} shared={nc in state.analysis.shared_unit_groups}")
        print(f"  Slots with enough venue space at all: {slots_with_space}")
        if reason_counts:
            top = sorted(reason_counts.items(), key=lambda kv: -kv[1])
            print(f"  Rejection reasons (count): {top}")
        if first_space_no_block:
            print(f"  ⚠ FREE & UNBLOCKED slot exists at {first_space_no_block} but course "
                  f"is still unscheduled — this is a placement-logic bug (e.g. venue "
                  f"selection/split-eligibility failing in try_place_course), not a "
                  f"constraint. Investigate place_course_no_split / "
                  f"place_course_with_minimal_split for this course.")
        elif slots_with_space == 0:
            print(f"  → Genuinely no venue anywhere has {needed} free seats in any slot. "
                  f"Not a bug — venue capacity is the real bottleneck for this course.")
        else:
            print(f"  → Every slot with enough space is blocked by the reasons above "
                  f"(family/shared-unit/norm-code locks, student or lecturer clashes).")

    print(f"══════════════════════════════════════════\n")


def ultimate_fallback_pass(all_courses, state, scheduled_ids):
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0
    
    print(f"\n[Phase8-Ultimate] {len(unscheduled)} unscheduled")
    placed_before = len(scheduled_ids)
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    course_by_id = {c.id: c for c in all_courses}
    
    # Family rescue with minimal splitting
    for nc, cids in state.analysis.shared_unit_groups.items():
        variants = [course_by_id[cid] for cid in cids if cid in course_by_id and cid not in scheduled_ids]
        if not variants:
            continue
        
        rescued = False
        for date_obj, _ in dates:
            if rescued:
                break
            for ss, se in all_slots:
                if rescued:
                    break
                
                if not _family_constraints_ok(variants, date_obj, ss, state):
                    continue
                
                # Try merged family
                if place_merged_family(variants, nc, date_obj, ss, se, state, scheduled_ids):
                    rescued = True
                    break
    
    # Individual courses with minimal splitting
    individual_remaining = sorted(
        [c for c in all_courses if c.id not in scheduled_ids],
        key=lambda c: -course_student_count(c)
    )
    
    for course in individual_remaining:
        if course.id in scheduled_ids:
            continue
        if course.id in state.strict_locked_ids:
            continue
        
        if _course_already_in_db(course):
            scheduled_ids.add(course.id)
            continue
        
        needed = course_student_count(course)
        lid = state._cached_lecturer_id(course)
        nc = normalize_course_code(course.course_code or "")
        should_allow_split = nc in state.analysis.shared_unit_groups
        
        for date_obj, _ in dates:
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
    return placed


# ======================================================================
# SECTION 9 – DB Helpers
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
                state.lecturer_exam_group[(lid, date_obj, ss)] = _get_exam_group_key(course)
            idx = state._slot_start_to_idx.get(ss)
            if idx is not None and pk:
                state.cohort_last_slot_idx[(pk, date_obj)] = idx
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
    
    # Recompute venue availability
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
                state.lecturer_exam_group[(lid, e["date"], e["start_time"])] = _get_exam_group_key(course)


# ======================================================================
# SECTION 10 – Auditing
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
                # Check if all are the same exam group
                exam_groups = set()
                for aid in slot_alloc_ids[(date, ss)]:
                    course = course_by_id.get(aid)
                    if course and getattr(course.lecturer, "id", None) == lid:
                        exam_groups.add(_get_exam_group_key(course))
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
            pk = _prog_year_key(course)
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
    entries = list(ExamTempTimetable.objects.values("venue_id", "date", "start_time", "course_allocation_id"))
    course_by_id = {c.id: c for c in all_courses}
    
    slot_venue_students = defaultdict(int)
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
            print(f"[Audit-OVERCAP] VIOLATION: venue_id={vid} date={date} slot={ss} | assigned={total_assigned} > cap={cap}")
    
    return violations


def _audit_and_fix_duplicate_placements(state, all_courses):
    course_by_id = {c.id: c for c in all_courses}
    entries = list(ExamTempTimetable.objects.values("id", "course_allocation_id", "date", "start_time"))
    by_course = defaultdict(list)
    for e in entries:
        by_course[e["course_allocation_id"]].append(e)
    
    fixed = 0
    for cid, rows in by_course.items():
        distinct_slots = {(r["date"], r["start_time"]) for r in rows}
        if len(distinct_slots) <= 1:
            continue
        keep_slot = min(distinct_slots)
        drop_ids = [r["id"] for r in rows if (r["date"], r["start_time"]) != keep_slot]
        ExamTempTimetable.objects.filter(id__in=drop_ids).delete()
        fixed += 1
    
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
# SECTION 11 – Main Entry Point
# ======================================================================

def run_optimized_autoscheduler_thread(disabled_constraints: Optional[Set[str]] = None):
    """
    Exam Auto-Scheduler v52 — Smart Split Minimization.
    
    KEY IMPROVEMENTS:
    1. Courses are ONLY split when NO single venue can fit them
    2. Splits use the MINIMUM number of venues (largest first)
    3. Program-specific courses are NEVER split
    4. Only shared courses (≥2 programs) can be split
    5. Leftover space is NOT used to split courses — only for courses that fit entirely
    """
    disabled_constraints = disabled_constraints or set()
    total_courses = 0
    
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
            return {"status": "error", "message": "No ExamSchedulerConfig found."}
        
        raw_courses = list(CourseAllocation.objects.all().select_related("lecturer", "program"))
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
            return {"status": "completed", "message": "No courses to schedule."}
        
        # Pre-scheduling intelligence
        _raw_venues = list(Venue.objects.filter(capacity__isnull=False, capacity__gt=0))
        psi = PreSchedulingIntelligence(all_courses, analysis, config, _raw_venues)
        strategy = psi.run()
        
        state = SchedulerState(config, analysis, strategy, disabled_constraints)
        state._cross_cohort_norm_codes = set(analysis.shared_unit_groups.keys())
        
        if not state.date_range or not state.venues or not state.slots:
            return {"status": "error", "message": "Invalid config."}
        
        scheduled_ids: Set[int] = set()
        
        print(f"\n[AutoScheduler v52] {total_courses} courses, {len(state.venues)} venues")
        print(f"[AutoScheduler v52] Shared families: {len(state.analysis.shared_unit_groups)}")
        print(f"[AutoScheduler v52] Split eligible: only shared courses (≥2 programs)")
        print(f"[AutoScheduler v52] Split rule: NO split if a single venue fits")
        print(f"[AutoScheduler v52] Split rule: MINIMUM venues when split is necessary")
        
        # Phase -1: Common courses first — last slot inward, most-repeated first
        p_common_placed = schedule_common_courses_priority_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        sync_lecturer_busy_from_db(state, all_courses)
        
        # Phase 1: Families first (mops up any common families the priority
        # pass above couldn't fit into a last-inward slot)
        p1_placed = schedule_families_first(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        sync_lecturer_busy_from_db(state, all_courses)
        
        # Phase 0: Designated venues
        p0_placed = designated_venue_priority_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        
        # Phase 2: Saturation
        schedule_saturation_loop(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        sync_lecturer_busy_from_db(state, all_courses)
        
        # Phase 3: Cross-day fill
        p3_placed = cross_day_fill_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        
        # Phase 3b: Family split rescue
        p3b_placed = family_split_rescue_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        
        # Phase 4: Forced fallback
        p4_placed = forced_fallback_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        
        # Phase 5: DB fallback
        p5_placed = db_driven_fallback_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        
        # Phase 7: Nuclear fallback
        p7_placed = 0
        if len(all_courses) - len(scheduled_ids) > 0:
            p7_placed = nuclear_fallback_pass(all_courses, state, scheduled_ids)
            sync_scheduled_ids_from_db(scheduled_ids)
        
        # Phase 8: Ultimate fallback
        p8_placed = 0
        if len(all_courses) - len(scheduled_ids) > 0:
            p8_placed = ultimate_fallback_pass(all_courses, state, scheduled_ids)
            sync_scheduled_ids_from_db(scheduled_ids)
        
        # Build shared venue groups
        _build_shared_venue_exam_groups()
        
        # Audits
        actual_scheduled = ExamTempTimetable.objects.values("course_allocation_id").distinct().count()
        remaining_count = total_courses - actual_scheduled

        if remaining_count > 0:
            diagnose_unscheduled_courses(all_courses, state, scheduled_ids)
        
        lecturer_violations = _audit_lecturer_conflicts(state, all_courses)
        student_violations = _audit_student_conflicts(state, all_courses)
        venue_violations = _audit_venue_capacity_with_sharing(state, all_courses)
        duplicate_fixes = _audit_and_fix_duplicate_placements(state, all_courses)
        
        if remaining_count == 0:
            message = f"SUCCESS! All {actual_scheduled} courses scheduled. Strategy={getattr(strategy, 'mode', 'NORMAL')}"
        else:
            message = f"Scheduled {actual_scheduled}/{total_courses}. {remaining_count} unscheduled."
        
        print(f"\n[AutoScheduler v52] {message}")
        print(f"[AutoScheduler v52] Lecturer violations: {lecturer_violations}")
        print(f"[AutoScheduler v52] Student violations: {student_violations}")
        print(f"[AutoScheduler v52] Venue capacity violations: {venue_violations}")
        
        return {
            "status": "completed" if remaining_count == 0 else "partial",
            "message": message,
            "scheduled_count": actual_scheduled,
            "remaining_count": remaining_count,
            "phase_stats": {
                "common_courses_priority": p_common_placed,
                "families_first": p1_placed,
                "designated_venue": p0_placed,
                "fill": p3_placed,
                "family_rescue": p3b_placed,
                "forced": p4_placed,
                "db_fallback": p5_placed,
                "nuclear": p7_placed,
                "ultimate": p8_placed,
            },
            "audit": {
                "lecturer_violations": lecturer_violations,
                "student_violations": student_violations,
                "venue_capacity_violations": venue_violations,
                "duplicate_placements_fixed": duplicate_fixes,
            },
        }
    
    except Exception as exc:
        import traceback
        print(f"[AutoScheduler v52] Fatal: {exc}\n{traceback.format_exc()}")
        return {
            "status": "error",
            "message": f"Scheduling failed: {exc}",
            "scheduled_count": 0,
            "remaining_count": total_courses,
        }


# ======================================================================
# Aliases for compatibility
# ======================================================================

def designated_venue_priority_pass(all_courses, state, scheduled_ids):
    """Phase 0: Designated venue priority pass."""
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
        # Scoped to THIS allocation's program/program_course — not just any
        # allocation sharing the course code — so a venue reserved for one
        # program is never handed to a different program's section.
        venue_ids = state.designated_venues_for_course(course)
        venues = [state.venue_by_id[vid] for vid in venue_ids if vid in state.venue_by_id]
        if not venues:
            continue
        
        needed = course_student_count(course)
        lid = state._cached_lecturer_id(course)
        placed_this = False
        
        for date_obj, _ in dates:
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
                
                # Hard capacity gate — mirrors place_single()'s `if rem < needed:
                # return False`. A designated course is only placed when a
                # designated venue can seat it IN FULL at this slot. We never
                # fall back to a venue with leftover-but-insufficient space:
                # doing so still writes the whole course into that venue/slot
                # in the DB, even though consume_venue() would silently clamp
                # its own internal usage counter — producing exactly the
                # combined-over-capacity rooms (e.g. 80 + 126 into a 150-cap
                # room) this pass was previously causing.
                room = None
                for v in venues:
                    if state.venue_remaining(v.id, date_obj, ss) >= needed:
                        room = v
                        break
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
                state.consume_venue(room.id, date_obj, ss, effective, course)
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
    
    print(f"[Phase0-Designated] Placed {placed}")
    return placed