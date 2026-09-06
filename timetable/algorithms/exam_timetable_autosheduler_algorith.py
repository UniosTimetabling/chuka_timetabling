"""
Exam Auto-Scheduler Module (v50 — Comprehensive Pre-Scheduling Intelligence Engine)
====================================================================================
NEW in v50 — PreSchedulingIntelligence (Phase 0 Analysis)
──────────────────────────────────────────────────────────
Before a single exam is placed the algorithm now runs a deep feasibility
analysis that treats the data as REAL academic objects — not just numbers.

It answers five layered questions:

  1. CAPACITY FEASIBILITY (intelligent, not naive)
     ─ Total venue seats vs total students is NOT the check.
     ─ The real check is: for each cohort (program+year) can we find enough
       non-colliding slots across the exam period given room-isolation rules?
     ─ Venues are bucketed by size; courses are matched to the right bucket
       before checking scarcity.

  2. COHORT COLLISION PRESSURE
     ─ Counts how many courses each (program, year) cohort has.
     ─ Computes the minimum number of distinct slots needed for each cohort
       (= number of courses, since no two courses in the same cohort can
       share a slot).
     ─ Compares that demand against the available (days × slots) budget.
     ─ Flags cohorts whose demand exceeds 80% of the budget as HIGH PRESSURE.

  3. LECTURER OVERLOAD DETECTION
     ─ Counts how many exams each lecturer supervises.
     ─ Detects lecturers whose course-count ≥ available daytime slots
       (scheduling impossible without evening or overlap).
     ─ Flags multi-cohort lecturers (lecturer teaches Year 1 AND Year 2 of
       the same program) — these create hard cross-cohort constraints.

  4. VENUE SCARCITY FORECAST (per size-bucket, per day)
     ─ Computes the "seat pressure" = total student demand / total seat supply
       across the exam window.
     ─ Warns when pressure > 0.85 (≥85 % of all seats needed).
     ─ Identifies specific days that will be bottlenecks.
     ─ If seat pressure > 1.0 the algorithm switches into OVERFLOW MODE:
       it relaxes the near-fit threshold to allow slight over-assignment
       (up to OVERFLOW_NEAR_FIT_THRESHOLD) to ensure 100 % placement.

  5. STRATEGY SELECTION
     ─ Based on the analysis results the engine selects an initial scheduling
       strategy and communicates it to all phases:
       · NORMAL   — pressure ≤ 0.75, no overloaded cohorts
       · COMPACT  — pressure 0.75–0.90, spread load evenly across days
       · DENSE    — pressure 0.90–1.0, use evening slots aggressively
       · OVERFLOW — pressure > 1.0, activate near-fit relaxation

All v49 improvements (Best-Fit, Room Isolation, multi-pass saturation, etc.)
and all earlier bug fixes are preserved exactly.
"""

from __future__ import annotations

import datetime
import re
import logging
from collections import defaultdict
from functools import lru_cache
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

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
# PERF v46: Gate verbose per-course / per-family debug prints behind this flag.
# Set to True only for debugging small datasets; leave False in production so
# millions of print() calls do not dominate wall-clock time.
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
    """
    Return the effective exam capacity for a venue.

    HARD CAP: result can NEVER exceed the venue's own exam_capacity (or
    physical capacity if exam_capacity is unset).  spacing_ratio is only
    used to REDUCE capacity.  A ratio > 1.0 is clamped to 1.0.
    """
    ec = getattr(venue, "exam_capacity", None)
    phys = getattr(venue, "capacity", None)
    hard_cap = int(ec) if ec else (int(phys) if phys else 0)
    if hard_cap <= 0:
        return 0
    effective_ratio = min(float(spacing_ratio), 1.0)
    return max(1, int(hard_cap * effective_ratio))


@lru_cache(maxsize=4096)
def normalize_course_code(code: str) -> str:
    """
    Normalize course codes so ALL variants of the same course map to identical key.
    e.g. "EDFO 112(A)" -> "EDFO112", "EDFO 112(B)" -> "EDFO112"
    """
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
def get_course_family(course_code: str) -> str:
    return normalize_course_code(course_code)


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
    """
    FIX v43: Always read directly from CourseAllocation.number_of_students.
    Each variant (BEEM 111, BEEM 111(B), BEEM 111(C)) is a separate
    CourseAllocation row with its own number_of_students.
    We sum ALL of them for merged families — never use only the base course count.
    Minimum 1 to avoid zero-division elsewhere.
    """
    return max(int(getattr(course, "number_of_students", 0) or 0), 1)


def family_total_students(group_courses: List) -> int:
    """
    FIX v43: Correct total student count for a merged family.
    Sum number_of_students from EVERY CourseAllocation variant in the group.
    This is the value that must fit in the venue(s).
    """
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

    # CombinedCourseGroup: allocations from different departments combined by a COD
    # are intentionally examined together — never a collision.
    if _combined_group_are_paired(c1.id, c2.id):
        return True
    return False


def _min_slots_for_cohort(courses: List) -> int:
    """
    Greedy lower-bound estimate of how many DISTINCT exam slots a cohort
    (same program+year) actually needs, taking collision-exemptions into
    account instead of assuming every course needs its own slot.

    Courses that are mutually exam_is_collision_exempt (different
    SpecializationStems of the same category, electives / SelectionGroup
    members, CombinedCourseGroup pairs) are bin-packed into the same
    bucket wherever possible. This mirrors — cheaply — what the actual
    placement phases are able to do, so pre-scheduling pressure estimates
    stay realistic instead of over-counting exempt courses as separate
    demand.
    """
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
    # Combined group courses may differ in program/year but still be taught together
    if _combined_group_are_paired(c1.id, c2.id):
        return False
    return True


# ======================================================================
# SECTION 1b — CombinedCourseGroup awareness helpers
# ======================================================================
#
# CombinedCourseGroup (created in the COD panel) links two or more
# CourseAllocations from DIFFERENT departments / programs that are taught
# and examined together by the same lecturer in the same room.
#
# The scheduler must know about them so it can:
#   1. Never flag a lecturer or venue collision for combined pairs.
#   2. Treat combined group members as a "family" — schedule them in
#      the same slot (same date + start_time + venue).
#   3. Exclude non-primary members from the unscheduled pool once the
#      primary allocation is placed.
# ======================================================================

# Module-level cache: allocation_id -> frozenset of CombinedCourseGroup PKs
# Populated once in run_optimized_autoscheduler_thread and reused everywhere.
_combined_group_cache: dict[int, frozenset] = {}  # alloc_id -> frozenset of group PKs


def _build_combined_group_cache() -> dict[str, list[int]]:
    """
    Load all CombinedCourseGroup records and populate _combined_group_cache.
    Also returns a dict {group_code -> [allocation_id, ...]} that can be
    merged into DataAnalysisReport.shared_unit_groups so Phase 1 places
    them together.

    Called once at scheduler startup.
    """
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

        # Use the group_code as the family key so Phase 1 places them together
        key = f"__combined__{group.group_code}"
        combined_families[key] = alloc_ids

    print(
        f"[CombinedGroup] Loaded {len(groups)} combined groups covering "
        f"{len(_combined_group_cache)} allocations"
    )
    return combined_families


def _combined_group_ids_for(alloc_id: int) -> frozenset:
    """Return the frozenset of CombinedCourseGroup PKs for this allocation."""
    return _combined_group_cache.get(alloc_id, frozenset())


def _combined_group_are_paired(alloc_a_id: int, alloc_b_id: int) -> bool:
    """
    Return True if both allocations share at least one CombinedCourseGroup.
    Used to exempt the pair from every collision check.
    """
    a_groups = _combined_group_ids_for(alloc_a_id)
    if not a_groups:
        return False
    b_groups = _combined_group_ids_for(alloc_b_id)
    return bool(a_groups & b_groups)


CONSECUTIVE_GAP_SLOTS = 1


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
        # FIX v43: Store pre-computed family totals for logging/validation
        self.family_total_students: Dict[str, int] = {}

    def log(self, msg: str):
        self.analysis_log.append(msg)
        print(f"[DataAnalysis] {msg}")


def detect_and_deduplicate_courses(all_courses: List, report: DataAnalysisReport) -> List:
    seen_ids: Set[int] = set()
    deduplicated: List = []
    for c in all_courses:
        if c.id in seen_ids:
            report.duplicate_allocation_ids.add(c.id)
            report.log(f"  DUPLICATE: course_allocation_id={c.id} "
                       f"({getattr(c, 'course_code', '?')}) — skipped")
        else:
            seen_ids.add(c.id)
            deduplicated.append(c)
    n_dupes = len(report.duplicate_allocation_ids)
    if n_dupes:
        report.log(f"Removed {n_dupes} duplicate course_allocation entries")
    return deduplicated


def analyze_courses(all_courses: List) -> DataAnalysisReport:
    report = DataAnalysisReport()
    if not all_courses:
        return report

    all_courses = detect_and_deduplicate_courses(all_courses, report)
    report.log(f"Analysing {len(all_courses)} courses (after deduplication)...")

    n_grouped = 0
    n_mandatory = 0
    for c in all_courses:
        sg_id = _exam_get_selection_group_id(c)
        if sg_id is not None:
            report.sg_course_counts[sg_id] += 1
            n_grouped += 1
        else:
            n_mandatory += 1
    report.log(
        f"Selection groups: {len(report.sg_course_counts)} groups | "
        f"{n_grouped} grouped courses | {n_mandatory} mandatory courses"
    )

    by_norm: Dict[str, List] = defaultdict(list)
    norm_counts: Dict[str, int] = defaultdict(int)

    for c in all_courses:
        raw_code = getattr(c, "course_code", "") or ""
        norm_code = normalize_course_code(raw_code)
        if norm_code not in report.normalization_map:
            report.normalization_map[norm_code] = raw_code
        norm_counts[norm_code] += 1
        by_norm[norm_code].append(c)

    report.log(f"Normalization: {len(by_norm)} unique normalized codes from {len(all_courses)} courses")

    # Identify course families
    merged_count = 0
    merged_sections = 0
    for norm_code, group in by_norm.items():
        if len(group) < 2:
            continue
        raw_codes = set()
        # FIX v43: Compute TRUE total = sum of all variants' number_of_students
        true_total_students = family_total_students(group)
        report.family_total_students[norm_code] = true_total_students

        for c in group:
            raw_codes.add(getattr(c, "course_code", "") or "")

        if DEBUG_VERBOSE:
            report.log(
                f"  COURSE FAMILY '{norm_code}': {len(group)} variants ({', '.join(raw_codes)}) | "
                f"TRUE Total students (sum of all variants): {true_total_students} | "
                f"Per-variant: {[course_student_count(c) for c in group]}"
            )
        merged_count += 1
        merged_sections += len(group)
        report.shared_unit_groups[norm_code] = [c.id for c in group]

    report.log(f"Identified {merged_count} course families covering {merged_sections} sections")

    # ── CombinedCourseGroup families ──────────────────────────────────────
    # Each CombinedCourseGroup must be treated as a scheduling family so
    # Phase 1 places all its member allocations in the same exam slot.
    # We inject them into shared_unit_groups using the __combined__ prefix
    # so they are distinguishable but processed by the same family logic.
    combined_families = _build_combined_group_cache()
    for combined_key, cids in combined_families.items():
        # Only register allocations that are actually in all_courses
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

    if combined_families:
        report.log(
            f"Injected {len(combined_families)} CombinedCourseGroup families "
            f"into shared_unit_groups"
        )

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
    report.log(f"Cohorts found: {len(report.cohort_course_counts)}")

    # Build conflict graph
    # PERF v46: short-circuit on same-norm-code BEFORE calling exam_is_collision_exempt
    # so the heavier function is never called for family variants.
    total_edges = 0
    for pk, members in report.courses_by_py.items():
        n = len(members)
        if n < 2:
            continue
        # Pre-compute norm codes once per member to avoid repeated lru_cache misses
        norm_codes = [normalize_course_code(m.course_code or "") for m in members]
        for i in range(n):
            for j in range(i + 1, n):
                if norm_codes[i] == norm_codes[j]:
                    continue
                ci, cj = members[i], members[j]
                # CombinedCourseGroup members are never in conflict with each other
                if _combined_group_are_paired(ci.id, cj.id):
                    continue
                if exam_is_collision_exempt(ci, cj):
                    continue
                report.cohort_conflict_graph[ci.id].add(cj.id)
                report.cohort_conflict_graph[cj.id].add(ci.id)
                total_edges += 1

    report.log(f"Conflict graph: {total_edges} edges")

    for c in all_courses:
        report.conflict_degree[c.id] = len(report.cohort_conflict_graph.get(c.id, set()))

    max_cohort_load = max(cohort_counts.values()) if cohort_counts else 0
    report.total_cohort_slot_demand = max_cohort_load
    report.log(f"Max cohort load: {max_cohort_load} -> minimum slots required")

    return report


# ======================================================================
# SECTION 2b – Pre-Scheduling Intelligence Engine (v50)
# ======================================================================
#
# This engine runs ONCE before any placement begins.  It reads the full
# data set and produces a SchedulingStrategy that all later phases use.
#
# Key insight: capacity feasibility is NOT "total seats vs total students".
# It is a multi-dimensional problem involving:
#   - cohort isolation (same program+year students can't clash)
#   - lecturer uniqueness (one lecturer per slot)
#   - room isolation (one course per room per slot)
#   - day-spread constraints (cohorts spread across days to avoid fatigue)
# ======================================================================

# Seat-pressure thresholds that drive strategy selection
_PRESSURE_NORMAL   = 0.75
_PRESSURE_COMPACT  = 0.90
_PRESSURE_DENSE    = 1.00

# When overflow mode is active we relax the near-fit threshold
OVERFLOW_NEAR_FIT_THRESHOLD: int = 30   # default NEAR_FIT_THRESHOLD is 15


class SchedulingStrategy:
    """
    Encapsulates the strategy selected by PreSchedulingIntelligence.
    Phases read strategy flags to decide how aggressively they schedule.
    """
    NORMAL   = "NORMAL"
    COMPACT  = "COMPACT"
    DENSE    = "DENSE"
    OVERFLOW = "OVERFLOW"

    def __init__(self):
        self.mode: str = self.NORMAL
        self.seat_pressure: float = 0.0
        self.total_seat_supply: int = 0
        self.total_student_demand: int = 0
        self.high_pressure_cohorts: List[str] = []
        # FIX v51: cohort_pressure holds the ACTUAL ratio (min_slots_needed /
        # slot_budget) for every cohort, not just the two categorical lists
        # above. Previously this ratio was computed in _analyze_cohorts and
        # then discarded after logging — nothing downstream ever consulted
        # it, so a program-year with 2 exams and 15 free days competed for
        # the SAME early slots as a program-year with 12 exams and 12 days,
        # on a first-come-first-served / round-robin basis. That let
        # low-pressure ("could go on almost any day") cohorts squat on
        # scarce early-day capacity that high-pressure cohorts had zero
        # slack to give up, pushing the tight cohorts into fallback phases
        # (and sometimes leaving them unscheduled) for no structural reason.
        # cohort_pressure is now threaded into priority_score() (course/
        # family ORDERING) and into day-scan DIRECTION in
        # schedule_families_first / run_saturation_day, so tight cohorts
        # get first pick of early days and flexible cohorts are steered
        # toward the days tight cohorts don't need.
        self.cohort_pressure: Dict[str, float] = {}
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
        print(f"[PSI] {msg}")

    def warn(self, msg: str):
        self.warnings.append(msg)
        print(f"[PSI ⚠] {msg}")

    def summary(self) -> str:
        lines = [
            f"══════════════════════════════════════════",
            f"  PRE-SCHEDULING INTELLIGENCE — v50",
            f"══════════════════════════════════════════",
            f"  Strategy Mode  : {self.mode}",
            f"  Seat Pressure  : {self.seat_pressure:.1%}  "
            f"({self.total_student_demand} students / {self.total_seat_supply} seats)",
            f"  Near-Fit Thresh: {self.near_fit_threshold}",
            f"  Use Evenings   : {self.use_evening_slots}",
            f"  Relax Consec.  : {self.relax_consecutive}",
        ]
        if self.high_pressure_cohorts:
            lines.append(f"  High-Pressure Cohorts ({len(self.high_pressure_cohorts)}): "
                         f"{', '.join(self.high_pressure_cohorts[:5])}"
                         f"{'...' if len(self.high_pressure_cohorts) > 5 else ''}")
        if self.infeasible_cohorts:
            lines.append(f"  ⛔ INFEASIBLE Cohorts ({len(self.infeasible_cohorts)}): "
                         f"{', '.join(self.infeasible_cohorts[:5])}")
        if self.overloaded_lecturers:
            lines.append(f"  Overloaded Lecturers ({len(self.overloaded_lecturers)}): "
                         f"{', '.join(self.overloaded_lecturers[:5])}"
                         f"{'...' if len(self.overloaded_lecturers) > 5 else ''}")
        if self.multi_cohort_lecturers:
            lines.append(f"  Multi-Cohort Lecturers ({len(self.multi_cohort_lecturers)}): "
                         f"{', '.join(self.multi_cohort_lecturers[:5])}"
                         f"{'...' if len(self.multi_cohort_lecturers) > 5 else ''}")
        if self.bottleneck_days:
            lines.append(f"  Bottleneck Days: {', '.join(self.bottleneck_days[:5])}")
        if self.warnings:
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")
        lines.append(f"══════════════════════════════════════════")
        return "\n".join(lines)


class PreSchedulingIntelligence:
    """
    Runs a comprehensive pre-scheduling analysis before any exam is placed.

    Inputs:
      - all_courses  : deduplicated list of CourseAllocation objects
      - analysis     : DataAnalysisReport already built by analyze_courses()
      - config       : ExamSchedulerConfig (dates, slots, spacing_ratio)
      - venues       : list of Venue objects

    Output:
      - SchedulingStrategy with mode, flags, warnings, and per-cohort/
        per-lecturer pressure data that scheduling phases use.
    """

    def __init__(self, all_courses: List, analysis: DataAnalysisReport,
                 config, venues: List):
        self.courses   = all_courses
        self.analysis  = analysis
        self.config    = config
        self.venues    = venues
        self.strategy  = SchedulingStrategy()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def run(self) -> SchedulingStrategy:
        s = self.strategy
        s.log("Starting comprehensive pre-scheduling analysis…")

        slots        = generate_slots(self.config.start_time,
                                      self.config.end_time,
                                      self.config.slot_size)
        spacing      = float(getattr(self.config, "spacing_ratio", 1.0))
        date_range   = self._build_date_range()
        n_days       = len(date_range)
        daytime      = [(ss, se) for ss, se in slots
                        if ss < datetime.time(17, 0)]
        evening      = [(ss, se) for ss, se in slots
                        if ss >= datetime.time(17, 0)]
        n_day_slots  = len(daytime)
        n_eve_slots  = len(evening)
        slot_budget  = n_days * n_day_slots            # daytime only baseline

        s.log(f"Exam window: {n_days} days | {n_day_slots} daytime slots/day "
              f"| {n_eve_slots} evening slots/day | Budget={slot_budget} slot-days")

        # ── 1. Seat supply (intelligent: respects spacing ratio) ──────
        total_seat_supply = sum(
            venue_exam_capacity(v, spacing) for v in self.venues
        )
        seats_per_slot = total_seat_supply           # same venues per slot
        total_slot_supply = seats_per_slot * slot_budget
        s.total_seat_supply = total_seat_supply
        s.log(f"Venue supply: {len(self.venues)} venues | "
              f"{total_seat_supply} seats/slot | "
              f"{total_slot_supply} total seat-slots over window")

        # ── 2. Student demand (per cohort — NOT a flat total) ─────────
        cohort_demand  = self._compute_cohort_demand()
        family_demand  = self._compute_family_demand()
        total_students = sum(cohort_demand.values())
        s.total_student_demand = total_students
        s.log(f"Total student-exam demand: {total_students} across "
              f"{len(cohort_demand)} cohorts")

        # ── 3. Seat pressure (meaningful ratio) ───────────────────────
        # Pressure is per-slot not aggregate; one slot is used by one course
        # from one cohort.  So pressure = how many slot-seats are needed
        # relative to supply over the whole window.
        n_courses = len(self.courses)
        # A rough but meaningful proxy: courses × avg_students vs slot-supply
        pressure = total_students / max(total_slot_supply, 1)
        s.seat_pressure = pressure
        s.log(f"Seat pressure (student-demand / slot-supply): {pressure:.3f}")

        # ── 4. Cohort collision analysis ──────────────────────────────
        self._analyze_cohorts(cohort_demand, n_days, n_day_slots, slot_budget,
                              n_eve_slots)

        # ── 5. Lecturer analysis ──────────────────────────────────────
        self._analyze_lecturers(slot_budget)

        # ── 6. Venue size-bucket scarcity ─────────────────────────────
        self._analyze_venue_scarcity(spacing, date_range, daytime, evening)

        # ── 7. Family (merged group) feasibility ─────────────────────
        self._analyze_families(family_demand)

        # ── 8. Strategy selection ─────────────────────────────────────
        self._select_strategy(pressure, n_days, n_day_slots, n_eve_slots)

        print(s.summary())
        return s

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_date_range(self) -> List[datetime.date]:
        excluded = set(self.config.excluded_date_list())
        result   = []
        for ds, _ in self.config.get_date_range():
            if ds in excluded:
                continue
            try:
                result.append(datetime.datetime.strptime(ds, "%Y-%m-%d").date())
            except Exception:
                pass
        return result

    def _compute_cohort_demand(self) -> Dict[str, int]:
        """
        For each (program, year) cohort, estimate the minimum number of
        DISTINCT exam slots actually required.

        FIX: previously this counted every course as needing its own slot,
        which ignores collision-exempt pairs — different SpecializationStems
        under the same category (a student only sits ONE stem), electives /
        SelectionGroup members (a student only sits ONE), and
        CombinedCourseGroup members (examined together by design). That
        over-count made cohorts look far tighter on slots than they really
        are, pushing the strategy selector into COMPACT/DENSE/OVERFLOW mode
        unnecessarily — which relaxes spacing and packs more concurrent
        exams (and therefore more simultaneous room demand) into fewer
        slots than the data actually requires. Using exam_is_collision_exempt
        (the same function the placement phases use) keeps this estimate
        consistent with what the scheduler can actually achieve.
        """
        by_cohort: Dict[str, List] = defaultdict(list)
        for c in self.courses:
            pk = _prog_year_key(c)
            if pk:
                by_cohort[pk].append(c)
        return {
            pk: _min_slots_for_cohort(courses)
            for pk, courses in by_cohort.items()
        }

    def _compute_family_demand(self) -> Dict[str, int]:
        """Return TRUE student total per course family."""
        course_by_id = {c.id: c for c in self.courses}
        result: Dict[str, int] = {}
        for nc, cids in self.analysis.shared_unit_groups.items():
            group = [course_by_id[cid] for cid in cids if cid in course_by_id]
            result[nc] = family_total_students(group)
        return result

    def _analyze_cohorts(self, cohort_demand: Dict[str, int],
                          n_days: int, n_day_slots: int,
                          slot_budget: int, n_eve_slots: int):
        s = self.strategy
        s.log(f"── Cohort collision analysis ({len(cohort_demand)} cohorts) ──")

        # Total slots available when evenings are included
        full_budget = n_days * (n_day_slots + n_eve_slots)

        for pk, n_courses in sorted(cohort_demand.items(),
                                    key=lambda x: -x[1]):
            # Minimum distinct slots needed = n_courses (no two can share a slot)
            min_slots_needed = n_courses

            # FIX v51: record the real pressure ratio for EVERY cohort
            # (not just the ones that cross a warning threshold) so it can
            # drive scheduling order/day-direction, not just diagnostics.
            # Ratio is against slot_budget (daytime only) — a cohort that
            # needs evenings to fit is, by definition, maximum pressure.
            ratio = (min_slots_needed / slot_budget) if slot_budget > 0 else 1.0
            s.cohort_pressure[pk] = ratio

            if min_slots_needed > full_budget:
                s.infeasible_cohorts.append(pk)
                s.cohort_pressure[pk] = 2.0  # flag as maximally tight
                s.warn(f"Cohort '{pk}' needs {min_slots_needed} slots "
                       f"but only {full_budget} available (inc. evenings) — "
                       f"INFEASIBLE without date-range extension")
            elif min_slots_needed > slot_budget:
                s.high_pressure_cohorts.append(pk)
                s.cohort_pressure[pk] = max(ratio, 1.0)
                s.warn(f"Cohort '{pk}' needs {min_slots_needed} slots, "
                       f"daytime budget={slot_budget} — evening slots REQUIRED")
                s.use_evening_slots = True
            elif min_slots_needed >= 0.8 * slot_budget:
                s.high_pressure_cohorts.append(pk)
                s.log(f"  High-pressure cohort '{pk}': "
                      f"{min_slots_needed}/{slot_budget} slots ({min_slots_needed/slot_budget:.0%})")

        if s.infeasible_cohorts:
            s.warn(f"{len(s.infeasible_cohorts)} cohorts are infeasible — "
                   f"consider extending the exam period or splitting cohorts")
        s.log(f"  High-pressure: {len(s.high_pressure_cohorts)} | "
              f"Infeasible: {len(s.infeasible_cohorts)}")

    def _analyze_lecturers(self, slot_budget: int):
        s = self.strategy
        # Build lecturer → {cohort_keys} and lecturer → course_count maps
        lec_courses: Dict[int, List] = defaultdict(list)
        lec_name:    Dict[int, str]  = {}
        for c in self.courses:
            lec = getattr(c, "lecturer", None)
            if not lec:
                continue
            lid = lec.id
            lec_courses[lid].append(c)
            lec_name[lid] = str(getattr(lec, "name", None) or
                                 getattr(lec, "username", None) or
                                 f"lec_{lid}")

        s.log(f"── Lecturer analysis ({len(lec_courses)} lecturers) ──")

        for lid, courses in lec_courses.items():
            n = len(courses)
            name = lec_name[lid]

            # Overload: lecturer has more exams than available slot-days
            if n > slot_budget:
                s.overloaded_lecturers.append(f"{name}({n})")
                s.warn(f"Lecturer '{name}' supervises {n} exams but only "
                       f"{slot_budget} slots exist — scheduling will require "
                       f"overlap or evening slots")

            # Multi-cohort: lecturer spans multiple (program, year) cohorts
            cohort_keys = set(_prog_year_key(c) for c in courses if _prog_year_key(c))
            if len(cohort_keys) > 1:
                # Check if any two cohorts conflict with each other
                cohort_list = list(cohort_keys)
                has_conflict = False
                for i in range(len(cohort_list)):
                    for j in range(i + 1, len(cohort_list)):
                        # Two different years of the same program conflict
                        prog_i = cohort_list[i].rsplit("_year_", 1)[0]
                        prog_j = cohort_list[j].rsplit("_year_", 1)[0]
                        if prog_i == prog_j:
                            has_conflict = True
                            break
                    if has_conflict:
                        break
                if has_conflict:
                    s.multi_cohort_lecturers.append(
                        f"{name}({','.join(sorted(cohort_keys))})"
                    )

        if s.multi_cohort_lecturers:
            s.log(f"  Multi-cohort lecturers (hard cross-cohort constraints): "
                  f"{len(s.multi_cohort_lecturers)}")

    def _analyze_venue_scarcity(self, spacing: float,
                                 date_range: List[datetime.date],
                                 daytime: List, evening: List):
        s = self.strategy
        s.log("── Venue size-bucket scarcity analysis ──")

        # Build size buckets: XS(<30), S(30-60), M(60-120), L(120-250), XL(>250)
        buckets: Dict[str, List[int]] = {
            "XS(<30)": [], "S(30-60)": [], "M(60-120)": [],
            "L(120-250)": [], "XL(>250)": [],
        }
        for v in self.venues:
            cap = venue_exam_capacity(v, spacing)
            if   cap < 30:    buckets["XS(<30)"].append(cap)
            elif cap < 60:    buckets["S(30-60)"].append(cap)
            elif cap < 120:   buckets["M(60-120)"].append(cap)
            elif cap < 250:   buckets["L(120-250)"].append(cap)
            else:             buckets["XL(>250)"].append(cap)

        for bname, caps in buckets.items():
            if caps:
                s.log(f"  Bucket {bname}: {len(caps)} venues, "
                      f"total={sum(caps)} seats, avg={sum(caps)//len(caps)}")

        # Per-day seat supply (all daytime slots combined)
        n_day_slots = len(daytime)
        seats_per_slot = sum(venue_exam_capacity(v, spacing) for v in self.venues)
        seats_per_day  = seats_per_slot * n_day_slots

        # Distribute student demand across days naively to find bottleneck days
        # (courses not yet scheduled so we distribute evenly as a forecast)
        total_students = s.total_student_demand
        n_days         = len(date_range)
        if n_days > 0 and seats_per_day > 0:
            avg_daily_demand = total_students / n_days
            for d in date_range:
                day_pressure = avg_daily_demand / seats_per_day
                if day_pressure > 0.85:
                    s.bottleneck_days.append(str(d))

        if s.bottleneck_days:
            s.warn(f"{len(s.bottleneck_days)} days forecast as capacity bottlenecks "
                   f"(>85% seat utilisation)")
        s.log(f"  Seats/slot={seats_per_slot} | Seats/day={seats_per_day} | "
              f"Days={n_days}")

    def _analyze_families(self, family_demand: Dict[str, int]):
        s = self.strategy
        if not family_demand:
            return
        spacing = float(getattr(self.config, "spacing_ratio", 1.0))
        max_single_venue = max(
            (venue_exam_capacity(v, spacing) for v in self.venues),
            default=0
        )
        oversized = {
            nc: total for nc, total in family_demand.items()
            if total > max_single_venue + OVERFLOW_NEAR_FIT_THRESHOLD
        }
        s.log(f"── Family feasibility: {len(family_demand)} families | "
              f"max_venue_cap={max_single_venue} ──")
        if oversized:
            s.log(f"  {len(oversized)} families exceed max venue capacity "
                  f"and will require split scheduling:")
            for nc, total in sorted(oversized.items(), key=lambda x: -x[1])[:10]:
                s.log(f"    '{nc}': {total} students > {max_single_venue} cap")

    def _select_strategy(self, pressure: float,
                          n_days: int, n_day_slots: int, n_eve_slots: int):
        s = self.strategy
        s.log(f"── Strategy selection (pressure={pressure:.3f}) ──")

        if pressure <= _PRESSURE_NORMAL:
            s.mode = SchedulingStrategy.NORMAL
            s.relax_consecutive = False
        elif pressure <= _PRESSURE_COMPACT:
            s.mode = SchedulingStrategy.COMPACT
            s.relax_consecutive = False
            s.log("  COMPACT mode: will spread load evenly across days")
        elif pressure <= _PRESSURE_DENSE:
            s.mode = SchedulingStrategy.DENSE
            s.use_evening_slots = True
            s.relax_consecutive = True
            s.log("  DENSE mode: evening slots activated, consecutive relaxed")
        else:
            s.mode = SchedulingStrategy.OVERFLOW
            s.use_evening_slots = True
            s.relax_consecutive = True
            s.near_fit_threshold = OVERFLOW_NEAR_FIT_THRESHOLD
            s.log(f"  OVERFLOW mode: near-fit threshold raised to "
                  f"{OVERFLOW_NEAR_FIT_THRESHOLD} to ensure full placement")
            s.warn("Capacity is TIGHT — overflow near-fit relaxation enabled. "
                   "Some venues may be slightly over their exam_capacity.")

        if s.infeasible_cohorts and s.mode != SchedulingStrategy.OVERFLOW:
            # Force at least DENSE when cohorts are infeasible
            s.mode = SchedulingStrategy.DENSE
            s.use_evening_slots = True
            s.relax_consecutive = True
            s.log("  Upgraded to DENSE due to infeasible cohorts")

        s.log(f"  Final mode: {s.mode}")


# ======================================================================
# SECTION 3 – SchedulerState
# ======================================================================

class SchedulerState:
    def __init__(self, config, analysis: DataAnalysisReport,
                 strategy: "SchedulingStrategy | None" = None,
                 disabled_constraints: Optional[Set[str]] = None):
        self.config = config
        self.analysis = analysis
        self.strategy: "SchedulingStrategy" = strategy or SchedulingStrategy()
        self.disabled_constraints: Set[str] = disabled_constraints or set()

        _raw_venues: List = list(
            Venue.objects.filter(capacity__isnull=False, capacity__gt=0)
        )

        # ── Constraint: Blocked Venues ────────────────────────────────────
        # Removed from the pool ONCE, right here — self.venues is the single
        # list every placement function in this module draws from
        # (_free_venues_for_slot, find_best_venue, _build_venue_pool, the
        # ultimate/nuclear fallback passes, etc.), so a blocked venue is
        # unavailable everywhere with no per-phase changes needed.
        blocked_venue_ids = constraint_engine.get_blocked_venue_ids(
            self.disabled_constraints, scheduler_type="exam"
        )
        if blocked_venue_ids:
            before = len(_raw_venues)
            _raw_venues = [v for v in _raw_venues if v.id not in blocked_venue_ids]
            print(
                f"[Constraints] Blocked Venues: removed {before - len(_raw_venues)} "
                f"venue(s) from the exam candidate pool ({len(_raw_venues)} remain)."
            )

        # ── Constraint: Exclusive Venue Restrictions ────────────────────────
        # NOTE: the exam scheduler has no soft "priority pass" for
        # VenueSpecialization the way the regular timetable scheduler does
        # (there is currently no mechanism that gives designated
        # courses/programs first claim on a room before general placement).
        # Until that priority pass exists here, an EXCLUSIVE specialization
        # rule is enforced the only safe way available: the venue is removed
        # from the exam pool entirely, the same as a full block. Non-exclusive
        # specialization rules currently have no effect on exam scheduling.
        exclusive_venue_ids = constraint_engine.get_exclusive_venue_ids(
            self.disabled_constraints, scheduler_type="exam"
        )
        if exclusive_venue_ids:
            before = len(_raw_venues)
            _raw_venues = [v for v in _raw_venues if v.id not in exclusive_venue_ids]
            print(
                f"[Constraints] Exclusive Venue Restrictions: removed "
                f"{before - len(_raw_venues)} venue(s) from the exam pool "
                f"({len(_raw_venues)} remain)."
            )

        self._spacing_ratio: float = float(getattr(config, "spacing_ratio", 1.0))
        self.venue_examcap: Dict[int, int] = {
            v.id: venue_exam_capacity(v, self._spacing_ratio) for v in _raw_venues
        }
        self.venue_rawcap: Dict[int, int] = {
            v.id: (v.capacity or 0) for v in _raw_venues
        }
        # Sort venues DESCENDING by exam capacity
        self.venues: List = sorted(
            _raw_venues,
            key=lambda v: self.venue_examcap.get(v.id, 0),
            reverse=True,
        )
        self.venues_by_cap_desc: List = self.venues
        self.venues_by_cap_asc: List = list(reversed(self.venues))
        self.venue_by_id: Dict[int, "Venue"] = {v.id: v for v in _raw_venues}

        # ── Soft constraint: CombinedCourseGroup venue co-location ────────
        # (date, slot_start, combined_group_id) -> venue_id already hosting
        # a member of that CombinedCourseGroup. Populated whenever a member
        # is placed (family merge OR individual fallback placement) so that
        # later-placed group-mates can be steered toward the same room
        # instead of landing in an arbitrary free venue. This is a
        # preference, never a hard requirement — normal capacity/isolation
        # checks still apply when co-location isn't possible.
        self._combined_group_venue: Dict[Tuple, int] = {}

        if self.venues:
            print(
                f"[SchedulerState] {len(self.venues)} venues loaded, "
                f"largest exam cap = {self.venue_examcap.get(self.venues[0].id, 0)}, "
                f"smallest exam cap = {self.venue_examcap.get(self.venues[-1].id, 0)}"
            )

        self.venue_usage: Dict[Tuple, int] = defaultdict(int)
        self.lecturer_busy: Dict[int, Set] = defaultdict(set)
        # {lecturer_id: {date: 'ALL_DAY' | {slot_start, ...}}} — built below,
        # once self.date_range/self.slots exist (needs to map each
        # LecturerBlockedSlot's weekday name onto the actual exam dates).
        self.lecturer_blocked: Dict = {}

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
        self._is_pg_cache: Dict[int, bool] = {}
        self._priority_score_cache: Dict[int, float] = {}
        self._lecturer_id_cache: Dict[int, Optional[int]] = {}
        self._norm_code_cache: Dict[int, str] = {}

        self.placed_families: Set[str] = set()

        self.date_range = self._build_date_range()
        self.slots = generate_slots(config.start_time, config.end_time, config.slot_size)
        self.morning_slots = [(s, e) for s, e in self.slots if s < datetime.time(12, 0)]
        self.afternoon_slots = [
            (s, e) for s, e in self.slots
            if datetime.time(12, 0) <= s < datetime.time(17, 0)
        ]
        self.evening_slots = [
            (s, e) for s, e in self.slots if is_evening_slot(s)
        ]
        self.daytime_slots_list = self.morning_slots + self.afternoon_slots
        self.all_slots_ordered = self.daytime_slots_list + self.evening_slots
        self._slot_start_to_idx: Dict[datetime.time, int] = {
            ss: idx for idx, (ss, _) in enumerate(self.all_slots_ordered)
        }

        self._sorted_dates = sorted(self.date_range, key=lambda dt: dt[0])

        # ── Constraint: Lecturer Blocked Days/Times (hard) ──────────────────
        # LecturerBlockedSlot rules use a generic weekday name (e.g.
        # "Thursday"); here we map that onto every actual exam date whose
        # weekday matches, using self.date_range (already built above).
        self.lecturer_blocked = self._build_lecturer_blocked_map()

        total_exam = sum(self.venue_examcap.values())
        total_raw = sum(self.venue_rawcap.values())
        self._cap_cache: Dict[Tuple, int] = {}
        self._raw_cap_cache: Dict[Tuple, int] = {}
        self._venue_avail: Dict[Tuple, int] = {}
        self._day_slot_total: Dict[Tuple, int] = {}
        self._day_any_cap: Dict[datetime.date, bool] = {}
        self._metrics_total_used_seats: int = 0
        self._metrics_total_booked_cap: int = 0
        self._metrics_total_waste: int = 0

        for date_obj, _ in self.date_range:
            self._day_any_cap[date_obj] = True
            for ss, _ in self.all_slots_ordered:
                self._cap_cache[(date_obj, ss)] = total_exam
                self._raw_cap_cache[(date_obj, ss)] = total_raw
                self._day_slot_total[(date_obj, ss)] = total_exam
                for v in self.venues:
                    self._venue_avail[(v.id, date_obj, ss)] = self.venue_examcap[v.id]

        n_days = len(self.date_range)
        n_day_slots = len(self.daytime_slots_list)
        budget = n_days * n_day_slots
        demand = analysis.total_cohort_slot_demand
        print(
            f"[SchedulerState] "
            f"Slot budget={budget} (days={n_days} x daytime_slots={n_day_slots}) | "
            f"Min cohort demand={demand} | "
            f"SpacingRatio={getattr(config, 'spacing_ratio', 1.0):.0%}"
        )

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
        """
        Converts core.scheduling_constraints.get_lecturer_blocked_ranges()
        (generic weekday + time ranges) into
        {lecturer_id: {date: 'ALL_DAY' | {slot_start, ...}}} matched against
        this run's actual exam dates (self.date_range) and slots (self.slots).
        """
        raw = constraint_engine.get_lecturer_blocked_ranges(
            self.disabled_constraints, scheduler_type="exam"
        )
        if not raw:
            return {}

        # weekday name -> list of actual exam dates falling on that weekday
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
                        if s < end and start < e:   # overlap test
                            idx_set.add(s)
        if blocked_map:
            print(f"[Constraints] Lecturer Blocked Days/Times: loaded for {len(blocked_map)} lecturer(s).")
        return blocked_map

    def lecturer_available(self, lid, date, slot_start) -> bool:
        if not lid:
            return True
        if (date, slot_start) in self.lecturer_busy[lid]:
            return False
        date_block = self.lecturer_blocked.get(lid, {}).get(date)
        if date_block == 'ALL_DAY':
            return False
        if date_block and slot_start in date_block:
            return False
        return True

    def mark_lecturer_busy(self, lid, date, slot_start):
        if lid:
            self.lecturer_busy[lid].add((date, slot_start))

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
        has_family_lock = fk in self.family_slot
        has_day_lock = nc and nc in self.norm_code_day_lock
        has_unit_lock = nc and nc in self.shared_unit_lock

        if not has_family_lock and not has_day_lock and not has_unit_lock:
            return

        # Check if any sibling is already placed in DB — if so, keep the locks
        if has_family_lock:
            locked_date, locked_slot = self.family_slot[fk]
            try:
                already_placed = ExamTempTimetable.objects.filter(
                    date=locked_date, start_time=locked_slot,
                ).values_list("course_allocation_id", flat=True)
                for aid in already_placed:
                    cached_fk = self._fk_cache.get(aid)
                    if cached_fk == fk:
                        return  # a sibling is actually placed — keep locks intact
            except Exception:
                pass

        # No sibling placed — safe to release all locks for this family
        old = self.family_slot.pop(fk, None)
        self.family_day.pop(fk, None)
        if nc:
            self.norm_code_day_lock.pop(nc, None)
            self.shared_unit_lock.pop(nc, None)
        if DEBUG_VERBOSE:
            print(f"[FamilyRelease] Released '{fk}' / '{nc}' (was {old})")

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

    def record_combined_group_venue(self, course, date, slot_start, venue_id):
        """
        Remember that `course` — a CombinedCourseGroup member — landed in
        `venue_id` for this slot, so group-mates placed afterwards (in a
        later phase, or later in the same phase) can be steered toward the
        same room. Soft bookkeeping only; never blocks placement on its own.
        """
        group_ids = _combined_group_ids_for(course.id)
        for gid in group_ids:
            self._combined_group_venue.setdefault((date, slot_start, gid), venue_id)

    def preferred_combined_venue_id(self, course, date, slot_start) -> Optional[int]:
        """
        Return the venue_id already hosting a CombinedCourseGroup group-mate
        of `course` at this exact slot, if any. None means no preference.
        """
        group_ids = _combined_group_ids_for(course.id)
        if not group_ids:
            return None
        for gid in group_ids:
            vid = self._combined_group_venue.get((date, slot_start, gid))
            if vid is not None:
                return vid
        return None

    def consume_venue(self, vid, date, slot_start, students: int):
        cap = self.venue_examcap.get(vid, 0)
        already_used = self.venue_usage.get((vid, date, slot_start), 0)

        # FIX v45: Hard guard — never allow usage to exceed exam_capacity.
        # If the venue is already full, reject outright instead of clamping,
        # preventing phantom overcapacity in the post-run audit.
        if already_used >= cap:
            if DEBUG_VERBOSE:
                print(
                    f"  [consume_venue FULL] venue={vid} cap={cap} "
                    f"already_used={already_used} requested={students} — rejected (venue full)"
                )
            return

        max_takeable = max(0, cap - already_used)
        if students > max_takeable:
            if DEBUG_VERBOSE:
                print(
                    f"  [consume_venue OVERFLOW] venue={vid} cap={cap} "
                    f"already_used={already_used} requested={students} "
                    f"clamped to {max_takeable}"
                )
            students = max_takeable
        if students <= 0:
            return
        avail_key = (vid, date, slot_start)
        new_avail = max(0, self._venue_avail.get(avail_key, 0) - students)
        self._venue_avail[avail_key] = new_avail
        self.venue_usage[(vid, date, slot_start)] += students
        slot_key = (date, slot_start)
        self._day_slot_total[slot_key] = max(
            0, self._day_slot_total.get(slot_key, 0) - students
        )
        self._cap_cache[slot_key] = self._day_slot_total[slot_key]
        self._raw_cap_cache[slot_key] = max(0, self._raw_cap_cache.get(slot_key, 0) - students)
        if self._day_slot_total.get(slot_key, 0) <= 0:
            self._day_any_cap[date] = any(
                self._day_slot_total.get((date, ss), 0) > 0
                for ss, _ in self.all_slots_ordered
            )

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
        """
        v49: Check if this slot has enough completely-free room capacity.
        A room only counts if it is fully free (remaining == cap) — room isolation rule.
        For small needed (1), just check aggregate; for larger needed also check
        whether any single free room or combined free rooms can hold it.
        """
        # Fast path: check aggregate remaining first
        if self._day_slot_total.get((date, slot_start), 0) < needed:
            return False
        # Check free rooms only (room isolation)
        free_total = 0
        for v in self.venues_by_cap_desc:
            cap = self.venue_examcap.get(v.id, 0)
            rem = self._venue_avail.get((v.id, date, slot_start), 0)
            if rem == cap and rem > 0:   # completely free
                free_total += cap
                if free_total >= needed:
                    return True
        return False

    def day_has_any_capacity(self, date) -> bool:
        return self._day_any_cap.get(date, True)

    def cohort_in_cooling(self, course, date, slot_start,
                          gap: int = CONSECUTIVE_GAP_SLOTS) -> bool:
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
        cid = course.id
        if cid in self._priority_score_cache:
            return self._priority_score_cache[cid]
        n_students = course_student_count(course)
        conflict_deg = self.analysis.conflict_degree.get(cid, 0)
        nc = self._norm_code(course)
        is_shared = nc in self.analysis.shared_unit_groups
        cohort_load = self.analysis.cohort_course_counts.get(self._py_key(course), 0)
        # FIX v51: cohort_load alone (raw course count) doesn't distinguish
        # "12 exams, 12 days available — zero slack" from "12 exams, 40 days
        # available — plenty of slack". Use the real pressure RATIO computed
        # by PreSchedulingIntelligence (min_slots_needed / slot_budget) —
        # this is what should drive scheduling urgency, not the raw count.
        # Falls back to a small cohort_load-based nudge if no pressure data
        # is available (e.g. this SchedulerState was built without running
        # PSI first).
        cohort_pressure = self.strategy.cohort_pressure.get(self._py_key(course), 0.0)
        # FIX v43: For families, use the true family total in priority scoring
        if is_shared:
            family_total = self.analysis.family_total_students.get(nc, n_students)
        else:
            family_total = n_students
        score = (
            family_total * 1.0
            + conflict_deg * 50.0
            + (500.0 if is_shared else 0.0)
            + cohort_load * 5.0
            + cohort_pressure * 400.0
        )
        self._priority_score_cache[cid] = score
        return score


# ======================================================================
# SECTION 4 – Venue Selection Helpers
# ======================================================================

# ======================================================================
# SECTION 4 – Venue Selection  (v49 — Smart Best-Fit + Room Isolation)
# ======================================================================
#
# DESIGN PRINCIPLES:
#   1. BEST-FIT MATCHING  — sort venues by capacity, match course size to
#      room size.  Small courses → small rooms; large courses → large rooms.
#      Never waste a 600-seat hall on a 30-student course.
#
#   2. NEAR-FIT TOLERANCE — if the best free room is slightly too small
#      (overflow = needed − cap ≤ NEAR_FIT_THRESHOLD), place the course
#      there anyway.  A 5-student overflow does NOT justify a split.
#
#   3. ROOM ISOLATION — a room is only eligible if it is COMPLETELY FREE
#      (remaining == cap) for the slot.  Two different courses must never
#      share the same room in the same slot — not even as a side-effect
#      of splitting.
#
#   4. SPLIT ONLY AS LAST RESORT — splitting is only allowed when the
#      course genuinely exceeds every single room's capacity by more than
#      NEAR_FIT_THRESHOLD.  The split then uses the fewest rooms possible
#      (largest rooms first) and only completely-free rooms.
#
#   5. FAMILY / MERGED GROUPS — treated as one unit.  If the combined
#      student count fits in one free room (within near-fit tolerance),
#      use that room.  If no single free room can hold the merged total,
#      do NOT merge into one slot — let each variant be scheduled
#      independently in its own appropriately-sized room.
# ======================================================================

NEAR_FIT_THRESHOLD: int = 15   # max overflow that avoids a split


def _free_venues_for_slot(date, slot_start, state: SchedulerState) -> List[Tuple]:
    """
    Return list of (venue, cap) for all completely-free rooms in this slot,
    sorted by cap ASCENDING (smallest first — used for best-fit matching).
    A room is free when remaining == exam_cap (no other course in it).
    """
    result = []
    for v in state.venues_by_cap_desc:   # desc order — we reverse at end
        cap = state.venue_examcap.get(v.id, 0)
        if cap <= 0:
            continue
        rem = state._venue_avail.get((v.id, date, slot_start), 0)
        if rem == cap:   # completely free
            result.append((v, cap))
    result.sort(key=lambda x: x[1])   # ascending: smallest room first
    return result


def find_venues_to_cover(needed: int, date, slot_start, state: SchedulerState) -> List:
    """
    Last-resort split: collect completely-free rooms (largest first) until
    their combined capacity >= needed.  Returns [] if not enough free rooms
    exist — caller defers to another slot/date.
    No partially-occupied room is ever included.
    """
    free = _free_venues_for_slot(date, slot_start, state)   # asc by cap
    free_desc = list(reversed(free))                         # largest first
    chosen, total = [], 0
    for v, cap in free_desc:
        chosen.append(v)
        total += cap
        if total >= needed:
            return chosen
    return []   # not enough free rooms — defer


def find_best_venue(needed: int, date, slot_start, state: SchedulerState,
                    near_fit_override: int = 0):
    """
    Best-fit venue selection from completely-free rooms only.

    Tier 1 — EXACT FIT:   smallest free room whose cap >= needed.
    Tier 2 — NEAR-FIT:    largest free room where needed - cap <= threshold.
    The threshold is taken from state.strategy.near_fit_threshold when
    near_fit_override is 0 (default), letting OVERFLOW mode relax the limit.
    Returns None if no room qualifies (caller must try split or defer).
    """
    threshold = near_fit_override or state.strategy.near_fit_threshold
    free = _free_venues_for_slot(date, slot_start, state)   # asc by cap

    # Tier 1: smallest room that fits exactly (best-fit ascending)
    for v, cap in free:
        if cap >= needed:
            if DEBUG_VERBOSE:
                print(f"  [BestFit] {v.code} cap={cap} needed={needed} (exact)")
            return v

    # Tier 2: near-fit — largest room where overflow <= threshold
    for v, cap in reversed(free):   # largest first
        overflow = needed - cap
        if 0 < overflow <= threshold:
            if DEBUG_VERBOSE:
                print(f"  [NearFit] {v.code} cap={cap} needed={needed} overflow={overflow}")
            return v

    return None


# ======================================================================
# SECTION 5 – Core placement primitives
# ======================================================================

_already_scheduled_cache: Set[int] = set()

# ──────────────────────────────────────────────────────────────────────────────
# Bulk-write buffer
# ──────────────────────────────────────────────────────────────────────────────
# Instead of one INSERT per course, callers append (course, venue, date,
# slot_start, slot_end) tuples here and flush via _flush_bulk_buffer().
_bulk_buffer: List[Tuple] = []
_BULK_FLUSH_SIZE = 200          # PERF v46: larger batches → fewer transactions


def _flush_bulk_buffer(state: "SchedulerState", scheduled_ids: Set[int]) -> int:
    """
    Write all buffered (course, venue, date, ss, se) tuples to the DB in a
    single bulk_create, update in-memory state, and clear the buffer.
    Returns the number of rows written.
    """
    global _bulk_buffer
    if not _bulk_buffer:
        return 0

    entries = []
    post_place_items = []   # (course, venue_id, students, date, ss, se)

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
    """
    Use in-memory cache first (O(1)) to avoid DB lookups in tight inner loops.
    Only falls back to the DB when the id is not already in the cache.
    """
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
        state.consume_venue(venue_id, date, slot_start, students)
    state.record_combined_group_venue(course, date, slot_start, venue_id)
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
        # ROOM ISOLATION: reject if another course is already in this room
        if rem != cap:
            return False
        # Accept exact fit or near-fit; reject if overflow > threshold
        threshold = state.strategy.near_fit_threshold
        overflow = needed - cap
        if overflow > threshold:
            return False

    # Consume only what the room physically has
    effective_seats = min(needed, cap) if cap > 0 else needed

    reason = _check_hard_constraints(course, date, slot_start, state)
    if reason:
        return False
    if not relax_consecutive and state.cohort_in_cooling(course, date, slot_start):
        return False
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
            state.bind_family(state.family_key(course), date, slot_start)
            state.bind_shared_unit(course, date, slot_start)
            state.bind_norm_code_day(course, date)
            return True
        return False
    _post_place(course, venue.id, effective_seats, date, slot_start, slot_end, state, scheduled_ids)
    return True


def place_multi_venue(course, venues, date, slot_start, slot_end,
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
        real = ExamTempTimetable.objects.filter(
            course_allocation=course
        ).values("date", "start_time").first()
        if real:
            state.bind_family(state.family_key(course), real["date"], real["start_time"])
            state.bind_shared_unit(course, real["date"], real["start_time"])
            state.bind_norm_code_day(course, real["date"])
        scheduled_ids.add(course.id)
        return True

    # ROOM ISOLATION: only completely free rooms, largest first
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
        if DEBUG_VERBOSE:
            print(f"  [MultiVenue FAIL] {course.course_code} needs {needed}, short {remaining}")
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
            _already_scheduled_cache.add(course.id)
            state.bind_family(state.family_key(course), date, slot_start)
            state.bind_shared_unit(course, date, slot_start)
            state.bind_norm_code_day(course, date)
            return True
        return False
    for v, students in assignments:
        state.consume_venue(v.id, date, slot_start, students)
    # Mark scheduled immediately before _post_place to prevent re-entry
    scheduled_ids.add(course.id)
    _already_scheduled_cache.add(course.id)
    _post_place(course, assignments[0][0].id, 0, date, slot_start, slot_end, state, scheduled_ids)
    return True


def try_place_course(
    course, date, slot_start, slot_end,
    state: SchedulerState, scheduled_ids: Set[int],
    relax_consecutive=False,
    allow_split: bool = False,   # kept for signature compat — IGNORED for non-family courses
) -> bool:
    """
    Place an individual (non-family) course into ONE free room.

    SPLITTING IS NEVER DONE HERE.
    A lecturer cannot physically be in two rooms at once.
    Splitting is only used for shared course families (place_merged_family /
    family_split_rescue_pass), where a different lecturer can cover each variant.

    Steps:
      1. Best-fit single completely-free room (exact or near-fit ≤ NEAR_FIT_THRESHOLD).
      2. If none found → return False (caller tries another slot/date).
    """
    nc = normalize_course_code(course.course_code or "")
    if nc in state.analysis.shared_unit_groups:
        family_member_ids = state.analysis.shared_unit_groups.get(nc, [])
        placed_family_members = [cid for cid in family_member_ids if cid in scheduled_ids]
        if placed_family_members and len(placed_family_members) < len(family_member_ids):
            return False

    needed = course_student_count(course)

    # ── Soft constraint: CombinedCourseGroup venue co-location ─────────
    # If a group-mate is already sitting in a venue at this exact slot and
    # there's room left for this course too, prefer that venue over an
    # arbitrary free one — combined groups are meant to sit together.
    # This never overrides a hard constraint: capacity is checked first,
    # and if it doesn't fit (or no group-mate is placed yet) we fall
    # through to the normal best-fit search unchanged.
    preferred_vid = state.preferred_combined_venue_id(course, date, slot_start)
    if preferred_vid is not None:
        preferred_venue = state.venue_by_id.get(preferred_vid)
        if (preferred_venue is not None
                and state.venue_remaining(preferred_vid, date, slot_start) >= needed):
            if place_single(course, preferred_venue, date, slot_start, slot_end,
                            state, scheduled_ids,
                            relax_consecutive=relax_consecutive,
                            ignore_capacity=True):
                return True
            # Fall through to normal best-fit if co-location placement failed
            # for some other reason (hard constraint, cooling period, etc.)

    v = find_best_venue(needed, date, slot_start, state)
    if v and place_single(course, v, date, slot_start, slot_end,
                          state, scheduled_ids,
                          relax_consecutive=relax_consecutive):
        return True
    return False


# ======================================================================
# SECTION 6 – Smart Family Placement (v43 FIX)
# ======================================================================
#
# KEY FIX v43:
#   total_needed = SUM of number_of_students from ALL CourseAllocation
#   variants in the family (not just the base course, not a cached value).
#
#   Before committing to any venue/set-of-venues, we verify:
#     single_venue.exam_capacity >= total_needed   (Strategy A)
#     sum(all venue remaining seats) >= total_needed  (Strategy B)
#
#   MergedCourseGroup.total_students is set to this true sum.
# ======================================================================


def _family_constraints_ok(group_courses, date, slot_start, state):
    # Collect lecturer IDs that are about to be made busy by earlier members
    # of this same group — combined group members share a lecturer intentionally.
    is_combined_family = (
        len(group_courses) >= 2
        and all(
            _combined_group_are_paired(group_courses[0].id, c.id)
            for c in group_courses[1:]
        )
    )
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
            # For CombinedCourseGroup families the same lecturer teaches every member —
            # the lecturer becomes "busy" after the first member is placed, so we must
            # not block subsequent members of the same group on this check.
            if not is_combined_family:
                return False
    return True


def _build_venue_pool(date, slot_start, state, free_only: bool = True):
    """
    Return list of [venue, remaining_seats, exam_cap] sorted desc by exam_cap.
    free_only=True (default): only completely-free rooms (remaining == cap).
    free_only=False: all rooms with any remaining space (nuclear fallback only).
    """
    pool = []
    for v in state.venues_by_cap_desc:
        cap = state.venue_examcap.get(v.id, 0)
        if cap <= 0:
            continue
        remaining = min(state.venue_remaining(v.id, date, slot_start), cap)
        if remaining <= 0:
            continue
        if free_only and remaining != cap:
            continue   # room already has another course — skip
        pool.append([v, remaining, cap])
    return pool


def place_merged_family(
    group_courses, nc, date, ss, se,
    state, scheduled_ids,
):
    """
    v51 — Smart family placement with merge/overflow/no-merge decision tree.

    DECISION TREE:
      1. Compute total_needed = sum of all variant student counts.
      2. Look for a single completely-free room that fits total_needed
         (exact or near-fit within NEAR_FIT_THRESHOLD).
         → If found: place ALL variants in that one room (Strategy A).
      3. No single room fits the total, but the combined capacity of ALL
         completely-free rooms in this slot does:
         → Overflow the family across as many free rooms as needed —
           e.g. COMS101 fills Hall A, the remainder spills into Hall B,
           and so on until every variant is placed (Strategy B).
      4. Even every free room in the slot together can't hold the family:
         → Do NOT merge into this slot — return False so each variant gets
           scheduled independently, possibly in a later slot/day.
    """
    group_courses = [c for c in group_courses if c.id not in scheduled_ids]
    if not group_courses:
        state.placed_families.add(nc)
        return True

    total_needed = family_total_students(group_courses)

    if DEBUG_VERBOSE:
        per_variant = {c.course_code: course_student_count(c) for c in group_courses}
        print(
            f"\n[Family v49] '{nc}' | {len(group_courses)} variants | "
            f"total_needed={total_needed} | per-variant={per_variant}"
        )

    if not _family_constraints_ok(group_courses, date, ss, state):
        return False

    # Build pool of completely-free rooms only
    pool = _build_venue_pool(date, ss, state, free_only=True)

    # ----------------------------------------------------------------
    # Strategy A: find ONE free room for the entire merged group
    # (exact fit or near-fit within threshold)
    # ----------------------------------------------------------------
    single_venue = None
    for row in pool:
        v, remaining, cap = row
        # remaining == cap guaranteed by free_only pool
        if remaining >= total_needed:
            single_venue = v   # exact fit
            break
        overflow = total_needed - cap
        if 0 < overflow <= state.strategy.near_fit_threshold:
            single_venue = v   # near-fit
            break

    if single_venue:
        if DEBUG_VERBOSE:
            cap = state.venue_examcap.get(single_venue.id, 0)
            print(f"  [StrategyA] Single room {single_venue.code} cap={cap} fits {total_needed} ✓")
        return _commit_single_venue(
            group_courses, nc, single_venue, total_needed,
            date, ss, se, state, scheduled_ids
        )

    # ----------------------------------------------------------------
    # Strategy B: no single room fits — overflow into as many free
    # rooms as needed within THIS SAME slot (e.g. COMS101 fills Hall A,
    # the overflow spills into Hall B, etc.) rather than skipping the
    # slot entirely. Only free (completely empty) rooms are used, so
    # this never bumps an already-placed course out of its room.
    # ----------------------------------------------------------------
    total_free_capacity = sum(row[1] for row in pool)
    if total_free_capacity >= total_needed:
        if DEBUG_VERBOSE:
            print(
                f"  [StrategyB OVERFLOW] No single free room fits {total_needed} students — "
                f"spreading across {len(pool)} free rooms (total free cap={total_free_capacity})."
            )
        if _commit_distributed(
            group_courses, nc, pool, total_needed,
            date, ss, se, state, scheduled_ids,
        ):
            return True

    # ----------------------------------------------------------------
    # Strategy C: even every free room in this slot combined can't hold
    # the family — do NOT merge. Return False; variants will be
    # scheduled independently, possibly on a different day/slot.
    # ----------------------------------------------------------------
    if DEBUG_VERBOSE:
        max_free_cap = pool[0][2] if pool else 0
        print(
            f"  [StrategyC SKIP] Not enough free capacity in this slot for {total_needed} students "
            f"(free rooms total={total_free_capacity}, max single room={max_free_cap}). "
            f"Variants will be placed independently."
        )
    return False


def _commit_single_venue(
    group_courses, nc, venue, total_needed,
    date, ss, se, state, scheduled_ids,
):
    """Write all variants to one venue atomically."""
    cap = state.venue_examcap.get(venue.id, 0)
    remaining = state.venue_remaining(venue.id, date, ss)

    # ROOM ISOLATION: reject if another course is already in this room
    if remaining != cap:
        if DEBUG_VERBOSE:
            print(f"  [ABORT] {venue.code} not free (rem={remaining} cap={cap})")
        return False

    # Near-fit guard
    if (total_needed - cap) > state.strategy.near_fit_threshold:
        if DEBUG_VERBOSE:
            print(f"  [ABORT] {venue.code} cap={cap} too small for {total_needed}")
        return False

    effective_seats = min(total_needed, cap)

    if DEBUG_VERBOSE:
        print(
            f"  [StrategyA] Committing to {venue.code} "
            f"(exam_cap={cap}, remaining={remaining}, need={total_needed})"
        )

    already = [c for c in group_courses if _course_already_in_db(c)]
    for c in already:
        scheduled_ids.add(c.id)
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
            # FIX v43: total_students = true sum, not just base course count
            merged = MergedCourseGroup.objects.create(
                base_course=group_courses[0],
                merged_code=nc,
                total_students=total_needed,   # TRUE sum of all variants
                date=date, start_time=ss, end_time=se,
                venue=venue,
            )
            merged.merged_courses.set(group_courses)
            state.placed_families.add(nc)
    except Exception as e:
        print(f"  [FAIL] DB commit error: {e}")
        return False

    state.consume_venue(venue.id, date, ss, effective_seats)
    for c in group_courses:
        state.record_combined_group_venue(c, date, ss, venue.id)
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
    if DEBUG_VERBOSE:
        print(
            f"  [OK v46] '{nc}' -> {venue.code} | "
            f"exam_cap={cap} | total_students={total_needed} ✓"
        )
    return True


def _commit_distributed(
    group_courses, nc, pool, total_needed,
    date, ss, se, state, scheduled_ids,
):
    """
    Assign each variant to venue(s) with enough REMAINING seats.
    Largest variants first, largest rooms first.
    Respects per-venue exam_capacity hard cap.
    MergedCourseGroup.total_students = true sum.
    """
    courses_sorted = sorted(group_courses, key=lambda c: -course_student_count(c))
    assignments = []  # (course, venue, seats)

    for course in courses_sorted:
        needed = course_student_count(course)
        placed = False

        # Try single venue for this variant
        for row in pool:
            v, remaining, cap = row
            # FIX v43: venue cap must hold this variant alone too
            if cap >= needed and remaining >= needed:
                assignments.append((course, v, needed))
                row[1] -= needed
                placed = True
                if DEBUG_VERBOSE:
                    print(f"    {course.course_code} ({needed} stu) -> {v.code} "
                          f"[exam_cap={cap}] (rem was {remaining+needed}, now {row[1]})")
                break

        if not placed:
            # Split variant across multiple venues
            left = needed
            if DEBUG_VERBOSE:
                print(f"    {course.course_code} ({needed} stu) — splitting across venues:")
            for row in pool:
                if left <= 0:
                    break
                v, remaining, cap = row
                if remaining <= 0:
                    continue
                take = min(remaining, left)
                assignments.append((course, v, take))
                row[1] -= take
                left -= take
                if DEBUG_VERBOSE:
                    print(f"      -> {v.code} [exam_cap={cap}] takes {take} "
                          f"(remaining now {row[1]})")
            if left > 0:
                if DEBUG_VERBOSE:
                    print(f"  [FAIL] Still short {left} seats for {course.course_code}")
                return False

    entries = [
        ExamTempTimetable(
            course_allocation=course, venue=venue,
            date=date, day=date.strftime("%A"),
            start_time=ss, end_time=se,
        )
        for course, venue, seats in assignments
    ]

    primary_venue = max(
        assignments,
        key=lambda a: state.venue_examcap.get(a[1].id, 0)
    )[1]

    try:
        with transaction.atomic():
            ExamTempTimetable.objects.bulk_create(entries, ignore_conflicts=True)
            # FIX v43: total_students = true sum of all variants
            merged = MergedCourseGroup.objects.create(
                base_course=group_courses[0],
                merged_code=nc,
                total_students=total_needed,   # TRUE sum
                date=date, start_time=ss, end_time=se,
                venue=primary_venue,
            )
            merged.merged_courses.set(group_courses)
            dist = {}
            for _, v, seats in assignments:
                dist[v.code] = dist.get(v.code, 0) + seats
            if DEBUG_VERBOSE:
                print(f"  [Meta v46] primary={primary_venue.code} dist={dist} "
                      f"total_students={total_needed}")
            state.placed_families.add(nc)
    except Exception as e:
        print(f"  [FAIL] DB commit error: {e}")
        return False

    for course, venue, seats in assignments:
        state.consume_venue(venue.id, date, ss, seats)
        state.record_combined_group_venue(course, date, ss, venue.id)
        state.mark_students_busy(course, date, ss)
        lid = state._cached_lecturer_id(course)
        state.mark_lecturer_busy(lid, date, ss)
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
        dist = {}
        for _, v, seats in assignments:
            dist[v.code] = dist.get(v.code, 0) + seats
        print(f"  [OK v46 Distributed] '{nc}' -> {dist} | total_students={total_needed} ✓")
    return True


def place_merged_group(group_courses, group_key, venue, date, slot_start, slot_end,
                       state, scheduled_ids):
    """Legacy entry point: delegates to place_merged_family."""
    group_courses = [c for c in group_courses if c.id not in scheduled_ids]
    if not group_courses:
        state.placed_families.add(group_key)
        return True

    # FIX v43: Use true sum for capacity check
    total = family_total_students(group_courses)
    cap = state.venue_examcap.get(venue.id, 0)
    remaining = state.venue_remaining(venue.id, date, slot_start)

    if cap < total or remaining < total:
        if DEBUG_VERBOSE:
            print(
                f"[Merge v46] {venue.code} exam_cap={cap} remaining={remaining} "
                f"< TRUE needed={total} — distributing instead"
            )
        return place_merged_family(
            group_courses, group_key, date, slot_start, slot_end,
            state, scheduled_ids
        )

    return _commit_single_venue(
        group_courses, group_key, venue, total,
        date, slot_start, slot_end, state, scheduled_ids
    )


def place_distributed_family(group_courses, nc, date, ss, se, state, scheduled_ids):
    """Legacy entry point — delegates to place_merged_family."""
    return place_merged_family(group_courses, nc, date, ss, se, state, scheduled_ids)


# ======================================================================
# SECTION 7 – DB helpers
# ======================================================================

def sync_lecturer_busy_from_db(state, all_courses):
    course_by_id = {c.id: c for c in all_courses}
    entries = list(
        ExamTempTimetable.objects.values("course_allocation_id", "date", "start_time")
    )
    for e in entries:
        course = course_by_id.get(e["course_allocation_id"])
        if not course:
            continue
        lid = state._cached_lecturer_id(course)
        if lid:
            state.lecturer_busy[lid].add((e["date"], e["start_time"]))


# ======================================================================
# SECTION 8 – Phase 1: Schedule all families first
# ======================================================================

def schedule_families_first(all_courses, state, scheduled_ids):
    if not state.analysis.shared_unit_groups:
        print("[Phase1] No families — skipping")
        return 0

    course_by_id = {c.id: c for c in all_courses}
    dates = state.dates_in_order()
    total_days = len(dates)

    # FIX v51: a family's "pressure" = the highest cohort_pressure among the
    # program-year cohorts it touches. A family shared mainly by cohorts
    # with tight day budgets (few free days relative to their exam count)
    # is itself tight — it needs to claim an early day NOW, before those
    # days get consumed by something that could have gone anywhere.
    def _family_pressure(group: List) -> float:
        pressures = [
            state.strategy.cohort_pressure.get(state._py_key(c), 0.0)
            for c in group
        ]
        return max(pressures) if pressures else 0.0

    # FIX v43: Sort families by TRUE total student count descending
    # FIX v51: ...but pressure comes first. A small, tight-cohort family
    # must be placed (and given first pick of days) before a huge but
    # flexible one, or the flexible one's greedy day_pointer walk can burn
    # through the very days the tight cohort has no alternative for.
    sorted_families = sorted(
        state.analysis.shared_unit_groups.items(),
        key=lambda kv: (
            _family_pressure([course_by_id[cid] for cid in kv[1] if cid in course_by_id]),
            family_total_students(
                [course_by_id[cid] for cid in kv[1] if cid in course_by_id]
            ),
        ),
        reverse=True,
    )

    print(
        f"\n[Phase1 v51] Scheduling {len(sorted_families)} families FIRST. "
        f"Pressure-first ordering, TRUE student sums for venue selection."
    )

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
        pressure = _family_pressure(group)
        if DEBUG_VERBOSE:
            print(
                f"\n[Phase1 v51] '{nc}' | {len(group)} variants | "
                f"TRUE total={true_total} students | pressure={pressure:.2f}"
            )

        family_placed = False

        # FIX v51: direction of the day scan now depends on pressure.
        # High-pressure families (little/no slack) scan chronologically
        # forward from day_pointer, same as before — they need to claim a
        # day as early as possible while capacity still exists everywhere.
        # Low-pressure families (could sit almost any day without ever
        # being at risk of running out of room) scan BACKWARD from the end
        # of the exam period instead. This keeps flexible families out of
        # the early days that tight cohorts are competing hardest for,
        # without ever refusing them a slot — they still get placed, just
        # steered toward less-contested days first.
        if pressure >= 0.7:
            day_order = [(day_pointer + off) % total_days for off in range(total_days)]
        else:
            day_order = list(range(total_days - 1, -1, -1))

        for day_idx in day_order:
            if family_placed:
                break
            date_obj, wday = dates[day_idx]

            if not state.day_has_any_capacity(date_obj):
                continue

            for ss, se in state.daytime_slots_list + state.evening_slots:
                if family_placed:
                    break

                if not _family_constraints_ok(group, date_obj, ss, state):
                    continue

                # Quick check: enough total remaining for the true sum?
                pool = _build_venue_pool(date_obj, ss, state)
                total_remaining = sum(row[1] for row in pool)
                if total_remaining < true_total:
                    continue

                if place_merged_family(
                    group, nc, date_obj, ss, se, state, scheduled_ids
                ):
                    placed_total += len(group)
                    state.family_day_assignments[nc] = date_obj
                    state.day_family_count[date_obj] += 1
                    if pressure >= 0.7:
                        day_pointer = (day_idx + 1) % total_days
                    family_placed = True

        if not family_placed:
            if DEBUG_VERBOSE:
                print(
                    f"  [WARN] Could not place '{nc}' "
                    f"(TRUE total={true_total} students) — will retry later"
                )

    print(
        f"\n[Phase1 v50] DONE: "
        f"{len(state.placed_families)}/{len(state.analysis.shared_unit_groups)} families | "
        f"{placed_total} variants placed"
    )
    return placed_total


# ======================================================================
# SECTION 9 – Phase 2: Saturation loop (unchanged logic, kept intact)
# ======================================================================

def _fill_slot_with_program(
    date, slot_start, slot_end,
    program_courses: List,
    state: SchedulerState,
    scheduled_ids: Set[int],
    relax_consecutive=False,
    placed_in_slot: Dict[str, List] = None,
) -> int:
    if placed_in_slot is None:
        placed_in_slot = defaultdict(list)

    placed = 0
    # PERF v46: sort once, outside the per-course check
    sorted_courses = sorted(
        [c for c in program_courses if c.id not in scheduled_ids],
        key=lambda c: course_student_count(c), reverse=True
    )

    # PERF v46: maintain a set of (pk, nc) pairs that block further courses
    # in this slot so the conflict check is O(1) instead of O(N)
    blocked_pk_nc: Set[Tuple[str, str]] = set()

    for course in sorted_courses:
        if course.id in scheduled_ids:
            continue
        nc = normalize_course_code(course.course_code or "")
        if nc in state.analysis.shared_unit_groups:
            continue
        if not state.slot_has_any_venue_space(date, slot_start, 1):
            break
        py_key = state._py_key(course)
        # PERF v46: fast-path using blocked set; fall back to exempt check only
        # when we have a same-cohort course in this slot already
        if py_key and py_key in placed_in_slot:
            # Check if any placed course for this cohort is not exempt
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

    return placed


def run_saturation_day(
    date: datetime.date,
    pending_by_program: Dict[str, List],
    state: SchedulerState,
    scheduled_ids: Set[int],
    all_courses: List,
    force_evenings: bool = False,
) -> int:
    placed_today = 0
    day_slots = state.daytime_slots_list
    eve_slots = state.evening_slots

    # FIX v51: raw pending-count alone doesn't tell you which program is
    # actually SHORT on days — a program with 20 pending courses and 20
    # free days is fine; a program with 5 pending courses and 5 free days
    # is desperate. Blend in the real per-cohort pressure ratio (computed
    # by PreSchedulingIntelligence) so cohorts with the least slack get
    # first pick of each day's rooms before flexible ones fill them in.
    def _program_pressure(courses: List) -> float:
        active = [c for c in courses if c.id not in scheduled_ids]
        if not active:
            return 0.0
        ratios = [
            state.strategy.cohort_pressure.get(state._py_key(c), 0.0)
            for c in active
        ]
        return max(ratios) if ratios else 0.0

    program_order = sorted(
        pending_by_program.items(),
        key=lambda kv: (
            _program_pressure(kv[1]),
            len([c for c in kv[1] if c.id not in scheduled_ids]),
        ),
        reverse=True,
    )

    max_rounds = 3
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
                placed_in_slot = defaultdict(list)
                n = _fill_slot_with_program(
                    date, ss, se, active, state, scheduled_ids,
                    relax_consecutive=(round_idx > 0),
                    placed_in_slot=placed_in_slot,
                )
                if n > 0:
                    placed_today += n
                    progress_this_round += n

            is_pg_prog = all(
                is_postgraduate_course(getattr(c, "course_code", "") or "")
                for c in prog_courses[:3]
            )
            # Use evening slots for PG programs always, OR for all programs
            # when force_evenings is set (DENSE/OVERFLOW strategy)
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
                    )
                    if n > 0:
                        placed_today += n
                        progress_this_round += n

        all_pending = [c for c in all_courses if c.id not in scheduled_ids]
        if not all_pending:
            break

        all_pending_sorted = sorted(
            all_pending,
            key=lambda c: (
                0 if is_postgraduate_course(getattr(c, "course_code", "") or "") and round_idx == 0 else 1,
                -course_student_count(c),
            )
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
                if try_place_course(course, date, ss, se,
                                    state, scheduled_ids,
                                    relax_consecutive=(round_idx > 0)):
                    placed_today += 1
                    progress_this_round += 1
                    if py_key:
                        placed_in_slot[py_key].append(course)

        if progress_this_round == 0:
            break

    return placed_today


def schedule_saturation_loop(all_courses, state, scheduled_ids):
    stats = {"ug_placed": 0, "pg_placed": 0}
    dates = state.dates_in_order()
    total_days = len(dates)
    strategy = state.strategy

    # Build pending_by_program once
    pending_by_program: Dict[str, List] = defaultdict(list)
    for c in all_courses:
        prog = getattr(c, "program", None)
        prog_id = str(prog.id) if prog else "no_program"
        pending_by_program[prog_id].append(c)

    print(
        f"\n[Phase2] Saturation: {len(all_courses)} total | "
        f"{len(pending_by_program)} programs | {total_days} days | "
        f"mode={strategy.mode}"
    )

    # In DENSE/OVERFLOW mode, run more passes and allow evenings immediately
    max_passes = 6 if strategy.mode in (
        SchedulingStrategy.DENSE, SchedulingStrategy.OVERFLOW
    ) else 4

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
                print(
                    f"  Pass {pass_idx+1} Day {day_idx+1}/{total_days} "
                    f"{date_obj} ({weekday_name}): "
                    f"+{n} | Total={len(scheduled_ids)}/{len(all_courses)}"
                )
        print(f"[Phase2] Pass {pass_idx+1} complete: +{placed_this_pass} placed")
        if placed_this_pass == 0:
            break   # no progress in full pass — stop

    return stats


# ======================================================================
# SECTION 10 – Phase 3: Cross-day fill
# ======================================================================

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
        stuck = [course_by_id[cid] for cid in cids
                 if cid in course_by_id and cid not in scheduled_ids]
        if not stuck:
            state.placed_families.add(nc)
            continue

        true_total = family_total_students(stuck)
        if DEBUG_VERBOSE:
            print(f"[Phase3-FamilyRetry] '{nc}': {len(stuck)} variants, TRUE total={true_total}")

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
        if not family_placed:
            if DEBUG_VERBOSE:
                print(f"[Phase3-FamilyRetry] '{nc}' STILL unplaced")

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

    print(f"[Phase3] Placed {placed}. Remaining: {len(all_courses) - len(scheduled_ids)}")
    return placed


# ======================================================================
# SECTION 10b – Phase 3b: Family Split Rescue
# ======================================================================
#
# For families where NO single room fits the merged total, each variant
# gets its own free room in the SAME day+slot.
#
# Algorithm:
#   For each unscheduled family:
#     For each (date, slot):
#       1. Check all variant constraints pass (students free, lecturer free).
#       2. Collect free rooms for that slot sorted by:
#            a. Same building as the first variant's best room (preferred)
#            b. Best-fit size for each variant
#       3. Greedily assign one free room per variant (no sharing).
#       4. If all variants are assigned → commit atomically.
#
# Building is inferred from the venue code prefix (strip trailing digits).
# e.g. BSL502 → "BSL", FTC201 → "FTC", LL3 → "LL"
# ======================================================================

def _venue_building(venue) -> str:
    """Extract building prefix from venue code, e.g. 'BSL502' → 'BSL'."""
    code = (getattr(venue, "code", None) or "").strip()
    # Strip trailing digits and spaces
    return re.sub(r'[\d\s]+$', '', code).upper()


def _best_fit_free_room(needed: int, candidates: List, state, date, slot_start):
    """
    From a list of (venue, cap) completely-free rooms, return the
    best-fit venue for `needed` students:
      - Smallest room whose cap >= needed (exact fit).
      - Failing that, largest room where needed - cap <= strategy threshold.
      - Returns None if nothing fits.
    """
    threshold = state.strategy.near_fit_threshold
    # Exact fit: smallest cap >= needed
    exact = [(v, cap) for v, cap in candidates if cap >= needed]
    if exact:
        return min(exact, key=lambda x: x[1])[0]
    # Near-fit: largest cap where overflow <= threshold
    near = [(v, cap) for v, cap in candidates
            if 0 < needed - cap <= threshold]
    if near:
        return max(near, key=lambda x: x[1])[0]
    return None


def family_split_rescue_pass(all_courses, state, scheduled_ids):
    """
    Phase 3b — rescue leftover family variants by placing each in its own
    room within the same day+slot, prioritising rooms in the same building.
    """
    # Collect families that still have unscheduled variants
    course_by_id = {c.id: c for c in all_courses}
    unplaced_families = {}   # nc → [course, ...]
    for nc, cids in state.analysis.shared_unit_groups.items():
        stuck = [course_by_id[cid] for cid in cids
                 if cid in course_by_id and cid not in scheduled_ids]
        if stuck:
            unplaced_families[nc] = stuck

    if not unplaced_families:
        return 0

    print(f"\n[Phase3b] Family split rescue: {len(unplaced_families)} families, "
          f"{sum(len(v) for v in unplaced_families.values())} variants")

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

                # 1. Check all variant hard constraints pass for this slot
                if not _family_constraints_ok(variants, date_obj, ss, state):
                    continue

                # 2. Build free rooms for this slot: (venue, cap), asc by cap
                free_rooms = [
                    (v, state.venue_examcap.get(v.id, 0))
                    for v in state.venues_by_cap_desc
                    if state.venue_examcap.get(v.id, 0) > 0
                    and state._venue_avail.get((v.id, date_obj, ss), 0)
                       == state.venue_examcap.get(v.id, 0)   # completely free
                ]
                if len(free_rooms) < len(variants):
                    continue   # not enough free rooms for all variants

                # 3. Determine preferred building from best room for largest variant
                largest_variant = max(variants, key=lambda c: course_student_count(c))
                best_room = _best_fit_free_room(
                    course_student_count(largest_variant), free_rooms, state, date_obj, ss
                )
                preferred_building = _venue_building(best_room) if best_room else ""

                # Sort free rooms: same building first, then best-fit ascending
                def room_sort_key(vc):
                    v, cap = vc
                    building_match = 0 if _venue_building(v) == preferred_building else 1
                    return (building_match, cap)
                free_rooms_sorted = sorted(free_rooms, key=room_sort_key)

                # 4. Greedy assignment: one room per variant, no sharing
                # Sort variants largest-first so they get first pick
                variants_sorted = sorted(variants, key=lambda c: -course_student_count(c))
                assignment = {}   # course.id → venue
                remaining_rooms = list(free_rooms_sorted)   # mutable copy

                all_assigned = True
                for course in variants_sorted:
                    needed = course_student_count(course)
                    chosen = _best_fit_free_room(needed, remaining_rooms, state, date_obj, ss)
                    if chosen is None:
                        all_assigned = False
                        break
                    assignment[course.id] = chosen
                    # Remove chosen room from pool so next variant can't reuse it
                    remaining_rooms = [(v, cap) for v, cap in remaining_rooms
                                       if v.id != chosen.id]

                if not all_assigned:
                    continue   # couldn't find a room for every variant in this slot

                # 5. Commit all variants atomically
                entries = []
                for course in variants:
                    venue = assignment[course.id]
                    entries.append(ExamTempTimetable(
                        course_allocation=course,
                        venue=venue,
                        date=date_obj,
                        day=date_obj.strftime("%A"),
                        start_time=ss,
                        end_time=se,
                    ))

                try:
                    with transaction.atomic():
                        ExamTempTimetable.objects.bulk_create(entries, ignore_conflicts=True)
                except Exception as exc:
                    print(f"  [Phase3b] DB error for '{nc}': {exc}")
                    continue

                # Update state for each variant
                buildings_used = set()
                for course in variants:
                    venue = assignment[course.id]
                    needed = course_student_count(course)
                    effective = min(needed, state.venue_examcap.get(venue.id, 0))
                    state.consume_venue(venue.id, date_obj, ss, effective)
                    state.record_combined_group_venue(course, date_obj, ss, venue.id)
                    state.mark_students_busy(course, date_obj, ss)
                    lid = state._cached_lecturer_id(course)
                    state.mark_lecturer_busy(lid, date_obj, ss)
                    state.bind_family(state.family_key(course), date_obj, ss)
                    state.bind_shared_unit(course, date_obj, ss)
                    state.bind_norm_code_day(course, date_obj)
                    state.mark_cohort_scheduled(course, date_obj, ss)
                    scheduled_ids.add(course.id)
                    _already_scheduled_cache.add(course.id)
                    buildings_used.add(_venue_building(venue))

                state.daily_load[date_obj] += 1
                state.placed_families.add(nc)
                placed_total += len(variants)
                rescued = True

                rooms_str = ", ".join(
                    f"{assignment[c.id].code}({course_student_count(c)}stu)"
                    for c in variants
                )
                print(f"  [Phase3b] '{nc}' → {date_obj} {ss} | "
                      f"buildings={buildings_used} | {rooms_str}")

        if not rescued:
            if DEBUG_VERBOSE:
                print(f"  [Phase3b] '{nc}' STILL unplaced after all slots")

    print(f"[Phase3b] Rescued {placed_total} variants across "
          f"{len(unplaced_families)} families")
    return placed_total

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

        print(f"\n[Phase4] Sweep {sweep}: {len(unscheduled)} unscheduled")
        before_sweep = len(scheduled_ids)
        unscheduled = sorted(unscheduled, key=lambda c: -state.priority_score(c))

        if sweep == 2:
            for c in unscheduled:
                state.release_family_binding(c)

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
                    # BUGFIX: this used to call release_family_binding() and then
                    # fall straight through to placement whenever a family/day
                    # lock conflicted with (date_obj, ss) — i.e. it would forget
                    # a course's committed exam date the instant a fallback sweep
                    # wanted a different one, then place it there anyway. That is
                    # exactly how the same course_allocation ended up with two
                    # ExamTempTimetable rows on two different dates for the same
                    # program-year. release_family_binding() only *actually*
                    # releases when it can't find a real sibling row already
                    # sitting in the DB at the locked slot — so we now respect
                    # its verdict: try to release (harmless no-op if a sibling is
                    # genuinely there), then re-check. If the lock is still live
                    # (a sibling really is scheduled elsewhere), skip this course
                    # for this slot instead of scheduling a duplicate.
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
                    if try_place_course(course, date_obj, ss, se,
                                        state, scheduled_ids,
                                        relax_consecutive=True,
                                        allow_split=(sweep >= 4)):
                        if pk:
                            placed_in_slot[pk].append(course)

        newly_placed = len(scheduled_ids) - before_sweep
        total_placed += newly_placed
        print(f"[Phase4] Sweep {sweep}: +{newly_placed} | Total={len(scheduled_ids)}/{len(all_courses)}")
        if newly_placed == 0 and sweep < 4:
            print(f"[Phase4] No progress — escalating")

    return total_placed


# ======================================================================
# SECTION 12 – DB sync helpers
# ======================================================================

def sync_scheduled_ids_from_db(scheduled_ids: Set[int], force: bool = False):
    """
    Sync scheduled_ids with the DB.
    Skip the DB round-trip when force=False and the local cache already
    agrees with what has been written (common case after a successful phase).
    """
    # Fast path: if nothing is pending in the DB beyond what we already know, skip
    if not force:
        cached_count = len(_already_scheduled_cache)
        db_count = ExamTempTimetable.objects.values("course_allocation_id").distinct().count()
        if db_count == cached_count:
            scheduled_ids.update(_already_scheduled_cache)
            return 0

    db_ids = set(
        ExamTempTimetable.objects
        .values_list("course_allocation_id", flat=True)
        .distinct()
    )
    before = len(scheduled_ids)
    scheduled_ids.update(db_ids)
    _already_scheduled_cache.update(db_ids)
    added = len(scheduled_ids) - before
    if added:
        print(f"[SyncFromDB] +{added} (total {len(scheduled_ids)})")
    return added


def rebuild_state_from_db(state: SchedulerState, all_courses: List) -> List:
    """
    FIX v45: Rebuild all in-memory scheduling state cleanly from the DB.

    The old implementation manually decremented _cap_cache inside the per-entry
    loop starting from the total-exam baseline.  Because multiple entries share
    the same (date, slot) key, this produced wrong intermediate values and caused
    _venue_avail to be stale when venue_remaining() was called later in the loop
    to build free_slots.

    New approach:
      1. Clear all usage / busy dictionaries.
      2. Accumulate venue_usage[(vid, date, ss)] from DB entries — no cache
         manipulation inside the loop.
      3. Call _recompute_cap_cache() ONCE at the end; it sets _venue_avail,
         _cap_cache, _day_slot_total, and _day_any_cap consistently.
      4. Build free_slots AFTER the recompute so venue_remaining() is correct.
    """
    print("[DBRebuild] Rebuilding scheduler state from DB...")

    # Reset all usage and busy tracking — _recompute_cap_cache will repopulate
    state.venue_usage.clear()
    state._py_busy.clear()
    state._py_busy_allocs.clear()
    state.lecturer_busy.clear()
    state.cohort_last_slot_idx.clear()
    state.cohort_daily_count.clear()
    # Also reset family/norm-code locks — will be repopulated from DB entries below
    state.family_slot.clear()
    state.family_day.clear()
    state.shared_unit_lock.clear()
    state.norm_code_day_lock.clear()

    course_by_id = {c.id: c for c in all_courses}
    entries = list(
        ExamTempTimetable.objects
        .values("course_allocation_id", "venue_id", "date", "start_time")
    )
    print(f"[DBRebuild] Found {len(entries)} entries in DB")

    for e in entries:
        vid = e["venue_id"]
        cid = e["course_allocation_id"]
        date_obj = e["date"]
        ss = e["start_time"]
        course = course_by_id.get(cid)
        n = course_student_count(course) if course else 1

        # FIX v45: Only accumulate venue_usage here.
        # Do NOT touch _cap_cache/_venue_avail — _recompute_cap_cache() does that.
        cap = state.venue_examcap.get(vid, 0)
        current_usage = state.venue_usage[(vid, date_obj, ss)]
        if current_usage < cap:
            # Only count students up to the venue's hard cap
            take = min(n, cap - current_usage)
            state.venue_usage[(vid, date_obj, ss)] += take
        # (If already at cap, the entry represents an overcap write from a
        #  previous buggy run; skip it so the rebuilt state is clean.)

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

            # Rebuild family/norm-code locks from placed entries
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

    # FIX v45: Single authoritative recompute — sets _venue_avail, _cap_cache,
    # _day_slot_total, and _day_any_cap from venue_usage in one clean pass.
    state._recompute_cap_cache()

    # Build free_slots AFTER recompute so venue_remaining() is accurate
    free_slots = []
    for date_obj, wday in state.dates_in_order():
        for ss, se in state.all_slots_ordered:
            avail = sum(state.venue_remaining(v.id, date_obj, ss) for v in state.venues)
            if avail > 0:
                free_slots.append((date_obj, ss, se, avail))

    print(f"[DBRebuild] Free slot-venues: {len(free_slots)}")
    return free_slots


def db_driven_fallback_pass(all_courses, state, scheduled_ids, _progress_fn=None):
    """
    FIX v45: Accepts optional _progress_fn so the caller can push live UI
    updates on every date iteration, preventing the UI from freezing at 83%%.
    """
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0

    print(f"\n[Phase5-DB] {len(unscheduled)} courses unscheduled")
    free_slots = rebuild_state_from_db(state, all_courses)
    sync_scheduled_ids_from_db(scheduled_ids)
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]

    if not unscheduled or not free_slots:
        return 0

    placed = 0
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    total_dates = max(len(dates), 1)
    unscheduled = sorted(unscheduled, key=lambda c: -state.priority_score(c))

    for date_idx, (date_obj, _) in enumerate(dates):
        # FIX v45: emit live progress ping per date so UI does not freeze at 83%%
        if _progress_fn:
            pct = 83 + int(10 * date_idx / total_dates)
            _progress_fn(
                pct,
                f"Phase 5: DB fallback — {date_obj} ({placed} placed, "
                f"{len(unscheduled) - placed} left)...",
                len(scheduled_ids),
                len(all_courses) - len(scheduled_ids),
            )
        for ss, se in all_slots:
            if not state.slot_has_any_venue_space(date_obj, ss, 1):
                continue
            # PERF v46: build the ordered venue list once per slot, not per course
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
                # Refresh per-course remaining from state (may have changed after prior placements)
                free_venues = [
                    (v, state.venue_remaining(v.id, date_obj, ss))
                    for v, _ in slot_free_venues
                    if state.venue_remaining(v.id, date_obj, ss) > 0
                ]
                placed_course = False
                # Soft constraint: prefer a venue already hosting a
                # CombinedCourseGroup group-mate of this course, if it has
                # room, before falling back to plain best-fit-by-capacity.
                preferred_vid = state.preferred_combined_venue_id(course, date_obj, ss)
                if preferred_vid is not None:
                    preferred_rem = state.venue_remaining(preferred_vid, date_obj, ss)
                    if preferred_rem >= needed:
                        preferred_venue = state.venue_by_id.get(preferred_vid)
                        if preferred_venue is not None and place_single(
                                course, preferred_venue, date_obj, ss, se,
                                state, scheduled_ids, relax_consecutive=True,
                                ignore_capacity=True):
                            placed += 1
                            total_free_slot -= needed
                            placed_course = True
                            if pk:
                                placed_in_slot[pk].append(course)
                if not placed_course:
                    for v, rem in free_venues:
                        venue_capacity = state.venue_examcap.get(v.id, 0)
                        if venue_capacity >= needed and rem >= needed:
                            if place_single(course, v, date_obj, ss, se,
                                            state, scheduled_ids, relax_consecutive=True):
                                placed += 1
                                total_free_slot -= needed
                                placed_course = True
                                if pk:
                                    placed_in_slot[pk].append(course)
                                break
                # NO split for individual courses — lecturer can't be in two rooms

    print(f"[Phase5-DB] Placed {placed}. Remaining: {len(all_courses) - len(scheduled_ids)}")
    return placed


# ======================================================================
# SECTION 13 – Nuclear fallback
# ======================================================================

def nuclear_fallback_pass(all_courses, state, scheduled_ids, _progress_fn=None):
    """
    FIX v45: Accepts optional _progress_fn for live UI updates per date.
    PERF v46: Sort priority list once (not per slot); replace per-course DB
    sibling query with in-memory family_slot / _already_scheduled_cache lookup.
    """
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0

    print(f"\n[Phase7-Nuclear] {len(unscheduled)} unscheduled")
    placed = 0
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    total_dates = max(len(dates), 1)

    # PERF v46: sort once outside the date/slot loops
    unscheduled_sorted = sorted(unscheduled, key=lambda c: -state.priority_score(c))

    for date_idx, (date_obj, _) in enumerate(dates):
        if _progress_fn:
            pct = 94 + int(5 * date_idx / total_dates)
            _progress_fn(
                pct,
                f"Phase 7: Nuclear fallback — {date_obj} ({placed} placed)...",
                len(scheduled_ids),
                len(all_courses) - len(scheduled_ids),
            )
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
                locked_fam = state.family_slot.get(fk)
                if locked_fam:
                    locked_date, locked_ss = locked_fam
                    # PERF v46: use in-memory cache instead of DB query per course
                    sibling_placed = any(
                        state.family_key(
                            type('_Stub', (), {'id': sid, 'course_code': '', 'program': None})()
                        ) == fk
                        for sid in _already_scheduled_cache
                        if sid != course.id
                    )
                    # Simpler and correct: check if any OTHER course in this family
                    # is already placed (present in scheduled_ids) at the locked slot.
                    # We do this via the family_slot binding itself — if the lock exists
                    # AND the locking course is still in scheduled_ids, the sibling is placed.
                    family_ids = state.analysis.cohort_conflict_graph  # not family ids
                    # Direct check: if any sibling (same family key) is scheduled, the lock is valid
                    nc_check = state._norm_code(course)
                    sibling_ids = state.analysis.shared_unit_groups.get(nc_check, [])
                    sibling_placed = any(
                        sid != course.id and sid in scheduled_ids
                        for sid in sibling_ids
                    )
                    if not sibling_placed:
                        state.family_slot.pop(fk, None)
                        state.family_day.pop(fk, None)
                        nc_release = state._norm_code(course)
                        if nc_release:
                            state.norm_code_day_lock.pop(nc_release, None)
                            state.shared_unit_lock.pop(nc_release, None)
                needed = course_student_count(course)
                if not state.slot_has_any_venue_space(date_obj, ss, needed):
                    continue
                if _check_hard_constraints(course, date_obj, ss, state):
                    continue
                if try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                    relax_consecutive=True, allow_split=True):
                    placed += 1
                    if pk:
                        placed_in_slot[pk].append(course)
                    break

    print(f"[Phase7-Nuclear] Placed {placed}. Remaining: {len(all_courses) - len(scheduled_ids)}")
    return placed


# ======================================================================
# SECTION 13b – Phase 8: Ultimate Fallback ("Place Everything")
# ======================================================================
#
# By this point every normal constraint-checking phase has run.
# Remaining unscheduled courses are placed with relaxed rules:
#
#   INDIVIDUAL COURSES:
#     • Still NO splitting — lecturer must be in one room.
#     • Still NO lecturer double-booking.
#     • Still NO same-cohort student clash in same slot.
#     • Capacity check RELAXED — a course can go into any free room
#       even if the room is technically smaller (overcap noted in log).
#     • Cooling period IGNORED.
#     • Room isolation RELAXED — a room may already have another course
#       (different cohort), as long as the combined students fit ≤ 2× cap.
#     • Best-fit matching still applied: small courses → small rooms.
#
#   SHARED FAMILY COURSES (same norm code, different sections):
#     • Treated as one unit — all variants in same slot.
#     • Each variant gets its own room (no lecturer clash).
#     • Same building preferred, best-fit per variant.
#     • If all variants can't fit in any slot → place them individually
#       (each in its own best-fit room in any slot that avoids lecturer clash).
# ======================================================================

def _ultimate_find_room(needed: int, date, slot_start, state: SchedulerState,
                        exclude_ids: Set[int] = None):
    """
    Find best-fit room for `needed` students, relaxed capacity mode.
    Preference order:
      1. Completely free room, best-fit (smallest cap >= needed).
      2. Completely free room — largest available (near-fit or overflow).
      3. Any room with any remaining space, best-fit by remaining.
    Never returns a room in exclude_ids.
    """
    exclude_ids = exclude_ids or set()

    # Collect completely-free rooms (remaining == cap), ascending by cap
    free_asc = sorted(
        [(v, state.venue_examcap.get(v.id, 0))
         for v in state.venues_by_cap_desc
         if v.id not in exclude_ids
         and state.venue_examcap.get(v.id, 0) > 0
         and state._venue_avail.get((v.id, date, slot_start), 0)
            == state.venue_examcap.get(v.id, 0)],
        key=lambda x: x[1]
    )

    # Tier 1: smallest free room that fits exactly
    for v, cap in free_asc:
        if cap >= needed:
            return v

    # Tier 2: largest free room (accepts overcap)
    if free_asc:
        return free_asc[-1][0]

    # Tier 3: any room with any remaining space (relaxed room isolation)
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


def _place_one_course_ultimate(course, dates, all_slots, state, scheduled_ids,
                               relax_student_conflict=False):
    """
    Place a single course in the best available slot+room.
    Hard constraints always enforced:
      - Lecturer not double-booked
      - Same program/year cohort not in same slot (student conflict — never relaxed)
      - Norm-code day lock (family variants must share the same day)
    Capacity: relaxed — accepts any free room, logs overcap.
    Returns True if placed.
    """
    # BUGFIX: every other placement function (place_single, place_multi_venue,
    # _commit_single_venue, _commit_distributed) checks _course_already_in_db()
    # before writing a new ExamTempTimetable row, so a course that already has
    # a real DB row never gets a second one. This last-resort function was
    # missing that guard entirely, so if scheduled_ids ever fell out of sync
    # with the DB (e.g. a row written by an earlier phase whose scheduled_ids
    # update got lost) Phase 8 would happily create a SECOND row for the same
    # course_allocation on a brand-new date — precisely the "scheduled twice"
    # bug. Bind to the real existing entry instead of creating a duplicate.
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
            # HARD: lecturer conflict — never allow
            if lid and not state.lecturer_available(lid, date_obj, ss):
                continue
            # HARD: student/cohort conflict — always enforced
            if not state.students_available(course, date_obj, ss):
                continue
            # HARD: norm-code day lock — family variants must share the same day
            if state.check_norm_code_day_conflict(course, date_obj):
                continue
            # HARD: shared-unit slot lock — family variants must share the same slot
            if state.check_shared_unit_conflict(course, date_obj, ss):
                continue
            # HARD: family slot lock — same family must share slot
            if state.check_family_conflict(course, date_obj, ss):
                continue

            room = None
            # Soft constraint: prefer a venue already hosting a
            # CombinedCourseGroup group-mate of this course, if it still
            # has room, even in this last-resort phase.
            preferred_vid = state.preferred_combined_venue_id(course, date_obj, ss)
            if preferred_vid is not None:
                preferred_rem = state._venue_avail.get((preferred_vid, date_obj, ss), 0)
                if preferred_rem >= needed:
                    room = state.venue_by_id.get(preferred_vid)
            if room is None:
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
            state.record_combined_group_venue(course, date_obj, ss, room.id)
            state.mark_students_busy(course, date_obj, ss)
            state.mark_lecturer_busy(lid, date_obj, ss)
            state.bind_family(state.family_key(course), date_obj, ss)
            state.bind_shared_unit(course, date_obj, ss)
            state.bind_norm_code_day(course, date_obj)
            state.mark_cohort_scheduled(course, date_obj, ss)
            scheduled_ids.add(course.id)
            _already_scheduled_cache.add(course.id)
            state.daily_load[date_obj] += 1
            overcap = needed - cap if cap and needed > cap else 0
            tag = f" [OVERCAP+{overcap}]" if overcap > 0 else ""
            print(f"  [Phase8] {course.course_code} {needed}stu → "
                  f"{date_obj} {ss} {room.code}(cap={cap}){tag}")
            return True
    return False


def ultimate_fallback_pass(all_courses, state, scheduled_ids):
    """
    Phase 8 — guarantee every course gets placed.

    CONSTRAINTS ALWAYS ENFORCED (even here):
      • Lecturer double-booking — a lecturer cannot be in two rooms at once.
      • Same-cohort student clash — students cannot sit two exams at same time.

    CONSTRAINTS RELAXED:
      • Room capacity — a course may go into a smaller room (logged as OVERCAP).
      • Cooling period — ignored.
      • Room isolation — a free room is used even if it's the only one left.

    NEVER:
      • Splitting individual courses across multiple rooms.

    For shared families: all variants in same slot, each in own room.
    If no slot works for all variants together → each placed individually
    (student + lecturer constraints still enforced).
    """
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0

    print(f"\n[Phase8-Ultimate] {len(unscheduled)} unscheduled — enforcing lecturer+student constraints, relaxing capacity")
    placed_before = len(scheduled_ids)
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    course_by_id = {c.id: c for c in all_courses}

    # ----------------------------------------------------------------
    # Step 1: Shared families — all variants same slot, own room each
    # ----------------------------------------------------------------
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

                # Lecturer + student + all family slot constraints for every variant
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

                # Assign one free room per variant (no sharing)
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

                # Commit atomically
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
                    print(f"  [Phase8] DB error '{nc}': {exc}")
                    continue

                for c in variants:
                    room = assigned[c.id]
                    cap = state.venue_examcap.get(room.id, 0)
                    effective = min(course_student_count(c), cap) if cap else course_student_count(c)
                    state.consume_venue(room.id, date_obj, ss, effective)
                    state.record_combined_group_venue(c, date_obj, ss, room.id)
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
                print(f"  [Phase8-Family] '{nc}' → {date_obj} {ss} | {rooms_str}")

        if not rescued:
            # Individual fallback per variant — still no lecturer/student clash
            for c in variants:
                if c.id in scheduled_ids:
                    continue
                ok = _place_one_course_ultimate(c, dates, all_slots, state, scheduled_ids,
                                                relax_student_conflict=False)
                if not ok:
                    print(f"  [Phase8] WARNING: Could not place family variant "
                          f"{c.course_code} without creating a student conflict — left unscheduled.")

    # ----------------------------------------------------------------
    # Step 2: Individual non-family courses
    # Sorted largest-first so big courses get first pick of rooms.
    # Pass 1: enforce all constraints.
    # Pass 2: relax student conflict only if still unplaced after pass 1.
    # ----------------------------------------------------------------
    individual_remaining = sorted(
        [c for c in all_courses
         if c.id not in scheduled_ids
         and normalize_course_code(c.course_code or "") not in state.analysis.shared_unit_groups],
        key=lambda c: -course_student_count(c)
    )

    # Pass 1: student + lecturer constraints enforced
    for course in individual_remaining:
        if course.id in scheduled_ids:
            continue
        _place_one_course_ultimate(course, dates, all_slots, state, scheduled_ids,
                                   relax_student_conflict=False)

    # Pass 2: log any still-unplaced courses — never relax student conflicts
    still_unplaced = [c for c in individual_remaining if c.id not in scheduled_ids]
    if still_unplaced:
        print(f"  [Phase8] WARNING: {len(still_unplaced)} courses could not be placed "
              f"without creating student conflicts — left unscheduled:")
        for course in still_unplaced:
            print(f"    - {course.course_code}")

    placed = len(scheduled_ids) - placed_before
    print(f"[Phase8-Ultimate] Placed {placed}. "
          f"Remaining: {len(all_courses) - len(scheduled_ids)}")
    return placed


# ======================================================================
# SECTION 14 – Auditing (v43: added over-capacity venue check)
# ======================================================================

def _audit_lecturer_conflicts(state, all_courses):
    from collections import Counter
    entries = list(ExamTempTimetable.objects.values("course_allocation_id", "date", "start_time"))
    course_by_id = {c.id: c for c in all_courses}
    slot_lecturers: Dict[Tuple, List[int]] = defaultdict(list)
    slot_alloc_ids: Dict[Tuple, List[int]] = defaultdict(list)
    for e in entries:
        course = course_by_id.get(e["course_allocation_id"])
        if not course:
            continue
        lid = getattr(course.lecturer, "id", None) if course.lecturer else None
        key = (e["date"], e["start_time"])
        if lid:
            slot_lecturers[key].append(lid)
            slot_alloc_ids[key].append(e["course_allocation_id"])
    violations = 0
    for (date, ss), lids in slot_lecturers.items():
        for lid, count in Counter(lids).items():
            if count > 1:
                # Collect alloc_ids for this lecturer in this slot
                alloc_ids_in_slot = [
                    aid for aid in slot_alloc_ids[(date, ss)]
                    if course_by_id.get(aid) and
                       getattr(getattr(course_by_id[aid], 'lecturer', None), 'id', None) == lid
                ]
                # If every pair of allocations is in the same CombinedCourseGroup, not a violation
                all_combined = True
                for i in range(len(alloc_ids_in_slot)):
                    for j in range(i + 1, len(alloc_ids_in_slot)):
                        if not _combined_group_are_paired(alloc_ids_in_slot[i], alloc_ids_in_slot[j]):
                            all_combined = False
                            break
                    if not all_combined:
                        break
                if all_combined and len(alloc_ids_in_slot) >= 2:
                    continue  # combined group — intentional shared lecturer
                violations += 1
                print(f"[Audit-LECTURER] VIOLATION: lecturer={lid} x{count} at {date} {ss}")
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
                    # CombinedCourseGroup: paired allocations are intentionally together
                    if _combined_group_are_paired(courses[i].id, courses[j].id):
                        continue
                    if not exam_is_collision_exempt(courses[i], courses[j]):
                        violations += 1
                        print(f"[Audit-STUDENT] VIOLATION: {pk} has "
                              f"{courses[i].course_code} + {courses[j].course_code} "
                              f"at {date} {ss}")
    return violations


def _audit_and_fix_duplicate_placements(state: SchedulerState, all_courses: List) -> int:
    """
    Safety net: detect any course_allocation that ended up with
    ExamTempTimetable rows on more than one distinct (date, start_time) —
    i.e. the "same course scheduled twice" bug — and repair it by keeping
    only the earliest slot and deleting the rest.

    NOTE: a course_allocation legitimately having MULTIPLE rows at the
    *same* (date, start_time) but different venues is fine and expected —
    that's how a single oversized course gets split across several exam
    halls simultaneously (see _commit_distributed). Only rows for the same
    course spread across DIFFERENT dates/times are ever a bug; those are
    what this function finds and removes.
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
            continue  # zero or one slot — fine, including multi-venue splits

        # Real duplicate across dates/times: keep the earliest slot's row(s),
        # drop the rest, and re-point the in-memory locks at the kept slot.
        keep_slot = min(distinct_slots)
        drop_ids = [r["id"] for r in rows if (r["date"], r["start_time"]) != keep_slot]
        course = course_by_id.get(cid)
        code = getattr(course, "course_code", "?") if course else "?"
        print(
            f"[Audit-DUPLICATE] FIXED: course_allocation_id={cid} ({code}) was "
            f"scheduled on {sorted(distinct_slots)} — kept {keep_slot}, "
            f"removed {len(drop_ids)} duplicate row(s)"
        )
        ExamTempTimetable.objects.filter(id__in=drop_ids).delete()
        if course:
            date_obj, ss = keep_slot
            state.bind_family(state.family_key(course), date_obj, ss)
            state.bind_shared_unit(course, date_obj, ss)
            state.bind_norm_code_day(course, date_obj)
        fixed += 1

    if fixed == 0:
        print("[Audit-DUPLICATE] ✓ No course scheduled on more than one date")
    return fixed


def _audit_venue_overcapacity(state: SchedulerState, all_courses: List) -> int:
    """
    FIX v43: New audit — detect any venue assigned more students than its exam_capacity.
    This catches the root bug from the screenshots.
    """
    entries = list(
        ExamTempTimetable.objects.values(
            "venue_id", "date", "start_time", "course_allocation_id"
        )
    )
    course_by_id = {c.id: c for c in all_courses}

    # Group by (venue, date, slot)
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
            print(
                f"[Audit-OVERCAP] VIOLATION: venue_id={vid} date={date} slot={ss} | "
                f"assigned={total_assigned} > exam_cap={cap} | "
                f"overflow={total_assigned - cap}"
            )
    if violations == 0:
        print("[Audit-OVERCAP] ✓ Zero over-capacity venue assignments")
    return violations


def classify_courses(all_courses) -> Tuple[List, List]:
    ug, pg = [], []
    for c in all_courses:
        code = getattr(c, "course_code", "") or ""
        (pg if is_postgraduate_course(code) else ug).append(c)
    return ug, pg


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
                from room_management.models import Venue as _Venue
                from course_allocation.models import CourseAllocation as _CA
                venue_obj = _Venue.objects.get(pk=vid)
                svg, _ = SharedVenueExamGroup.objects.get_or_create(
                    venue=venue_obj,
                    date=date_val,
                    start_time=start_val,
                    end_time=end_val,
                    defaults={"day": day_val},
                )
                cids = [e["course_allocation_id"] for e in group_entries]
                cas = list(_CA.objects.filter(pk__in=cids))
                svg.course_allocations.set(cas)
                # FIX v43: total_students = true sum from CourseAllocation records
                svg.total_students = sum(
                    int(ca.number_of_students or 0) for ca in cas
                )
                base_entry = ExamTempTimetable.objects.filter(
                    venue_id=vid, date=date_val, start_time=start_val
                ).first()
                if base_entry:
                    svg.exam_temp_timetable_entry = base_entry
                svg.save()
                created += 1
        except Exception as exc:
            print(f"[SharedVenueGroup] Error venue={vid} date={date_val}: {exc}")

    print(f"[SharedVenueGroup] Created/updated {created} records")
    return created


# ======================================================================
# SECTION 15 – Main entry point
# ======================================================================

def apply_exam_lecturer_soft_preferences_pass(
    state: "SchedulerState",
    disabled_constraints: Optional[Set[str]] = None,
) -> Dict[str, int]:
    """
    Best-effort relocation for SOFT lecturer preferences (preferred
    day/time, preferred venue), run after the main exam-scheduling phases
    directly against the persisted ExamTempTimetable rows.

    Mirrors apply_lecturer_soft_preferences_pass() in the regular
    timetable scheduler, adapted for exams:
      - LecturerTimePreference's weekday name (e.g. "Wednesday") is mapped
        onto every matching actual exam date via state.date_range.
      - Exam sessions placed across MULTIPLE venues (shared/family
        placements — several ExamTempTimetable rows sharing the same
        course_allocation/date/start_time) are skipped entirely; moving
        one row of a multi-venue family without moving the others would
        break the shared session. Only single-venue placements are
        relocated.

    Safety rules — never creates a new conflict or unschedules a course:
      - target (date, slot) must have no other exam for that lecturer
      - target venue (current or preferred) must be free at that
        (date, slot)
      - target (date, slot) must not already hold another exam for the
        same program (conservative — safe even without exact-year detail)
    """
    stats = {'lecturers_considered': 0, 'entries_moved': 0, 'entries_left_in_place': 0, 'errors': 0}

    time_prefs = constraint_engine.get_lecturer_time_preferences(disabled_constraints, scheduler_type="exam")
    venue_prefs = constraint_engine.get_lecturer_venue_preferences(disabled_constraints, scheduler_type="exam")
    blocked_ranges = constraint_engine.get_lecturer_blocked_ranges(disabled_constraints, scheduler_type="exam")
    lecturer_ids = set(time_prefs.keys()) | set(venue_prefs.keys())
    if not lecturer_ids:
        print("[LecturerPrefs] No active soft preferences — skipping exam soft-preference pass")
        return stats

    print(f"[LecturerPrefs] Exam soft preferences — {len(lecturer_ids)} lecturer(s) with preferences")

    dates_by_weekday: Dict[str, List[datetime.date]] = defaultdict(list)
    for date_obj, weekday_name in state.date_range:
        dates_by_weekday[weekday_name].append(date_obj)

    def _is_hard_blocked(lecturer_id, date_obj, start, end) -> bool:
        """
        A soft (date, start, end) candidate must never be used to relocate a
        lecturer into a slot that a LecturerBlockedSlot hard rule forbids for
        that weekday — a contradictory admin setup (e.g. blocked all day
        Thursday AND a Thursday exam time preference) must lose to the hard
        block, not silently override it.
        """
        weekday_name = date_obj.strftime('%A')
        for b_day, b_start, b_end in blocked_ranges.get(lecturer_id, []):
            if b_day != weekday_name:
                continue
            if b_start is None or b_end is None:
                return True  # whole day blocked
            if b_start < end and start < b_end:
                return True  # overlap
        return False

    def _matching_date_slots(day_time_ranges):
        candidates = []
        for weekday_name, start, end in day_time_ranges:
            for date_obj in dates_by_weekday.get(weekday_name, []):
                if start is None or end is None:
                    for s, e in state.slots:
                        candidates.append((date_obj, s, e))
                else:
                    for s, e in state.slots:
                        if s < end and start < e:
                            candidates.append((date_obj, s, e))
        return candidates

    for lecturer_id in lecturer_ids:
        stats['lecturers_considered'] += 1
        preferred_slots = [
            (d, s, e) for (d, s, e) in _matching_date_slots(time_prefs.get(lecturer_id, []))
            if not _is_hard_blocked(lecturer_id, d, s, e)
        ]
        preferred_venue_ids = venue_prefs.get(lecturer_id, set())

        try:
            entries = list(
                ExamTempTimetable.objects.select_related('course_allocation', 'venue')
                .filter(course_allocation__lecturer_id=lecturer_id)
            )
        except Exception as exc:
            stats['errors'] += 1
            print(f"[LecturerPrefs] ERROR loading exam entries for lecturer {lecturer_id}: {exc}")
            continue

        # Multi-venue family/shared placements share (course_allocation, date,
        # start_time) across several rows — skip those entirely for safety.
        session_counts: Dict[Tuple, int] = defaultdict(int)
        for e in entries:
            session_counts[(e.course_allocation_id, e.date, e.start_time)] += 1

        for entry in entries:
            session_key = (entry.course_allocation_id, entry.date, entry.start_time)
            if session_counts[session_key] > 1:
                continue

            already_ok_day = (not preferred_slots) or any(
                entry.date == d and entry.start_time == s and entry.end_time == e
                for d, s, e in preferred_slots
            )
            already_ok_venue = (not preferred_venue_ids) or (entry.venue_id in preferred_venue_ids)
            if already_ok_day and already_ok_venue:
                continue

            candidate_slots = preferred_slots or [(entry.date, entry.start_time, entry.end_time)]
            candidate_venue_ids = list(preferred_venue_ids) if preferred_venue_ids else (
                [entry.venue_id] if entry.venue_id else []
            )
            if not candidate_venue_ids:
                stats['entries_left_in_place'] += 1
                continue

            moved = False
            try:
                for date_obj, s_start, s_end in candidate_slots:
                    if moved:
                        break
                    lecturer_busy = ExamTempTimetable.objects.filter(
                        course_allocation__lecturer_id=lecturer_id,
                        date=date_obj, start_time=s_start, end_time=s_end,
                    ).exclude(id=entry.id).exists()
                    if lecturer_busy:
                        continue

                    program_id = entry.course_allocation.program_id if entry.course_allocation else None
                    if program_id:
                        program_busy = ExamTempTimetable.objects.filter(
                            date=date_obj, start_time=s_start, end_time=s_end,
                            course_allocation__program_id=program_id,
                        ).exclude(id=entry.id).exists()
                        if program_busy:
                            continue

                    for venue_id in candidate_venue_ids:
                        venue_busy = ExamTempTimetable.objects.filter(
                            venue_id=venue_id, date=date_obj, start_time=s_start, end_time=s_end,
                        ).exclude(id=entry.id).exists()
                        if venue_busy:
                            continue

                        weekday_name = date_obj.strftime('%A')
                        with transaction.atomic():
                            ExamTempTimetable.objects.filter(id=entry.id).update(
                                venue_id=venue_id, date=date_obj, day=weekday_name,
                                start_time=s_start, end_time=s_end,
                            )
                        stats['entries_moved'] += 1
                        moved = True
                        break
            except Exception as exc:
                stats['errors'] += 1
                print(f"[LecturerPrefs] ERROR relocating exam entry {entry.id}: {exc}")
                continue

            if not moved:
                stats['entries_left_in_place'] += 1

    print(
        f"[LecturerPrefs] Exam pass complete — lecturers={stats['lecturers_considered']} | "
        f"moved={stats['entries_moved']} | left_in_place={stats['entries_left_in_place']} | "
        f"errors={stats['errors']}"
    )
    return stats


def run_optimized_autoscheduler_thread(disabled_constraints: Optional[Set[str]] = None):
    """
    Exam Auto-Scheduler v50 — Comprehensive Pre-Scheduling Intelligence Engine.

    disabled_constraints: constraint-category keys (see
    core.scheduling_constraints.CONSTRAINT_DEFS) unchecked by the admin on
    the pre-run confirmation screen — disabled for THIS run only, without
    changing the persisted SchedulerConstraintToggle defaults.

    NEW Phase 0b: PreSchedulingIntelligence runs a deep feasibility analysis
    before any placement begins:
      - Cohort collision pressure (slots needed vs slots available per cohort)
      - Lecturer overload detection (multi-cohort, over-assigned lecturers)
      - Venue size-bucket scarcity (per bucket, per day forecast)
      - Family oversizing (families that can't fit in any single room)
      - Strategy selection: NORMAL / COMPACT / DENSE / OVERFLOW

    When OVERFLOW mode is activated (seat_pressure > 1.0) the near-fit
    threshold is automatically relaxed from 15 to 30, ensuring full
    placement even when venue capacity is slightly insufficient.

    All v49 improvements and earlier bug fixes are preserved exactly.
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
            return {
                "status": "error",
                "message": "No ExamSchedulerConfig found.",
                "scheduled_count": 0,
                "remaining_count": 0,
            }

        raw_courses = list(
            CourseAllocation.objects.all()
            .select_related("lecturer", "program", "department",
                            "program_course", "selection_group",
                            "specialization_stem", "specialization_stem__category")
        )
        total_raw = len(raw_courses)

        # Build the CombinedCourseGroup cache BEFORE analyze_courses so that
        # _combined_group_are_paired() works during conflict-graph construction
        # and the combined families are injected into shared_unit_groups.
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
            return {
                "status": "completed",
                "message": "No courses to schedule.",
                "scheduled_count": 0,
                "remaining_count": 0,
            }

        try:
            from .progress_tracking_autosheduler import (
                update_progress,
                log_debug,
                log_warn,
                log_error,
                is_cancelled,
            )
            _has_progress = True
        except ImportError:
            _has_progress = False
            def log_debug(m): pass
            def log_warn(m): pass
            def log_error(m): pass
            def is_cancelled(): return False

        def _progress(pct, msg, sched=0, rem=0):
            if _has_progress:
                try:
                    update_progress(pct, msg, sched, rem)
                except Exception:
                    pass
            print(f"[AutoScheduler v50 {pct:3d}%] {msg}")

        def _log(msg, level="debug"):
            """Mirror a verbose print to the frontend log feed."""
            print(f"[AutoScheduler v50] {msg}")
            if _has_progress:
                try:
                    {"debug": log_debug, "warn": log_warn, "error": log_error}.get(
                        level, log_debug
                    )(msg)
                except Exception:
                    pass

        _progress(2, f"Phase 0: {total_courses} courses analysed "
                     f"({total_raw - total_courses} dupes removed)",
                  0, total_courses)

        # ──────────────────────────────────────────────────────────────
        # Phase 0b: Pre-Scheduling Intelligence — comprehensive analysis
        # ──────────────────────────────────────────────────────────────
        _progress(3, "Phase 0b: Pre-Scheduling Intelligence — deep analysis…",
                  0, total_courses)
        _raw_venues_for_psi = list(
            Venue.objects.filter(capacity__isnull=False, capacity__gt=0)
        )
        psi = PreSchedulingIntelligence(
            all_courses=all_courses,
            analysis=analysis,
            config=config,
            venues=_raw_venues_for_psi,
        )
        strategy = psi.run()

        # Log any critical warnings to the progress feed
        for w in strategy.warnings:
            _log(f"[PSI ⚠] {w}", "warn")
        if strategy.infeasible_cohorts:
            _log(f"[PSI ⛔] Infeasible cohorts detected — "
                 f"consider extending exam period: {strategy.infeasible_cohorts}", "warn")

        state = SchedulerState(config, analysis, strategy, disabled_constraints)
        state._cross_cohort_norm_codes = set(analysis.shared_unit_groups.keys())

        if not state.date_range or not state.venues or not state.slots:
            return {
                "status": "error",
                "message": "Invalid config: missing dates, venues, or slots.",
                "scheduled_count": 0,
                "remaining_count": 0,
            }

        ug_courses, pg_courses = classify_courses(all_courses)
        scheduled_ids: Set[int] = set()

        print(
            f"[AutoScheduler v50] Courses={total_courses} "
            f"(UG={len(ug_courses)}, PG={len(pg_courses)}) | "
            f"Venues={len(state.venues)} | "
            f"Days={len(state.date_range)} | "
            f"Families={len(analysis.shared_unit_groups)} | "
            f"Strategy={strategy.mode} | "
            f"Pressure={strategy.seat_pressure:.1%}"
        )

        # Log family student totals for verification (verbose mode only)
        if DEBUG_VERBOSE:
            for nc, cids in analysis.shared_unit_groups.items():
                group = [c for c in all_courses if c.id in set(cids)]
                true_total = family_total_students(group)
                print(
                    f"[v50 FamilyCheck] '{nc}': {len(group)} variants | "
                    f"TRUE total={true_total} | "
                    f"per-variant={[f'{c.course_code}={course_student_count(c)}' for c in group]}"
                )

        # Phase 1
        _progress(8, f"Phase 1: Families first (TRUE student sums for venue selection)...",
                  0, total_courses)
        if is_cancelled():
            return {"status": "cancelled", "message": "Cancelled before Phase 1",
                    "scheduled_count": 0, "remaining_count": total_courses}
        p1_placed = schedule_families_first(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        sync_lecturer_busy_from_db(state, all_courses)

        # Phase 2
        _progress(25, f"Phase 2: Saturation ({total_courses - len(scheduled_ids)} remaining)...",
                  len(scheduled_ids), total_courses)
        if is_cancelled():
            return {"status": "cancelled", "message": "Cancelled before Phase 2",
                    "scheduled_count": len(scheduled_ids), "remaining_count": total_courses - len(scheduled_ids)}
        schedule_saturation_loop(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        sync_lecturer_busy_from_db(state, all_courses)
        phase2_count = len(scheduled_ids)

        # Phase 3
        _progress(55, f"Phase 3: Cross-day fill ({total_courses - len(scheduled_ids)} remaining)...",
                  len(scheduled_ids), total_courses)
        if is_cancelled():
            return {"status": "cancelled", "message": "Cancelled before Phase 3",
                    "scheduled_count": len(scheduled_ids), "remaining_count": total_courses - len(scheduled_ids)}
        p3_placed = cross_day_fill_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)

        # Phase 3b — family split rescue
        _progress(65, f"Phase 3b: Family split rescue ({total_courses - len(scheduled_ids)} remaining)...",
                  len(scheduled_ids), total_courses)
        if not is_cancelled():
            p3b_placed = family_split_rescue_pass(all_courses, state, scheduled_ids)
            sync_scheduled_ids_from_db(scheduled_ids)
        else:
            p3b_placed = 0

        # Phase 4
        _progress(75, f"Phase 4: Forced fallback ({total_courses - len(scheduled_ids)} remaining)...",
                  len(scheduled_ids), total_courses)
        if is_cancelled():
            return {"status": "cancelled", "message": "Cancelled before Phase 4",
                    "scheduled_count": len(scheduled_ids), "remaining_count": total_courses - len(scheduled_ids)}
        p4_placed = forced_fallback_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)

        # Phase 5
        _progress(83, f"Phase 5: DB fallback ({total_courses - len(scheduled_ids)} remaining)...",
                  len(scheduled_ids), total_courses)
        if is_cancelled():
            return {"status": "cancelled", "message": "Cancelled before Phase 5",
                    "scheduled_count": len(scheduled_ids), "remaining_count": total_courses - len(scheduled_ids)}
        p5_placed = db_driven_fallback_pass(all_courses, state, scheduled_ids, _progress_fn=_progress)
        sync_scheduled_ids_from_db(scheduled_ids)

        # Phase 7
        p7_placed = 0
        if len(all_courses) - len(scheduled_ids) > 0:
            _progress(94, f"Phase 7: Nuclear fallback ({len(all_courses) - len(scheduled_ids)} remaining)...",
                      len(scheduled_ids), total_courses)
            if not is_cancelled():
                p7_placed = nuclear_fallback_pass(all_courses, state, scheduled_ids, _progress_fn=_progress)
                sync_scheduled_ids_from_db(scheduled_ids)

        # Phase 8 — ultimate fallback: place everything, relaxed capacity
        p8_placed = 0
        if len(all_courses) - len(scheduled_ids) > 0:
            _progress(97, f"Phase 8: Ultimate fallback ({len(all_courses) - len(scheduled_ids)} remaining)...",
                      len(scheduled_ids), total_courses)
            if not is_cancelled():
                p8_placed = ultimate_fallback_pass(all_courses, state, scheduled_ids)
                sync_scheduled_ids_from_db(scheduled_ids)

        _progress(98, "Building shared venue exam groups…",
                  len(scheduled_ids), total_courses - len(scheduled_ids))
        _build_shared_venue_exam_groups()

        _progress(98, "Applying lecturer day/time & venue preferences…",
                  len(scheduled_ids), total_courses - len(scheduled_ids))
        lecturer_pref_stats = apply_exam_lecturer_soft_preferences_pass(state, disabled_constraints)
        if lecturer_pref_stats['entries_moved']:
            print(f"[LecturerPrefs] Relocated {lecturer_pref_stats['entries_moved']} exam entr(y/ies) to honour preferences.")

        # Audit + auto-repair: same course scheduled on 2+ different dates.
        # Runs BEFORE the scheduled/remaining counts below so those numbers
        # reflect the cleaned-up data, and before the other audits since a
        # leftover duplicate row would otherwise get double-counted by them.
        _progress(98, "Audit: checking for duplicate course placements…",
                  len(scheduled_ids), total_courses - len(scheduled_ids))
        duplicate_fixes = _audit_and_fix_duplicate_placements(state, all_courses)

        actual_scheduled = (
            ExamTempTimetable.objects.values("course_allocation_id").distinct().count()
        )
        remaining_count = total_courses - actual_scheduled

        # Audits — each is a full DB scan on large datasets; emit a progress ping
        # before each one so the frontend bar advances past 97% while they run.
        _progress(98, "Audit: checking lecturer conflicts…",
                  actual_scheduled, remaining_count)
        lecturer_violations = _audit_lecturer_conflicts(state, all_courses)

        _progress(99, "Audit: checking student conflicts…",
                  actual_scheduled, remaining_count)
        student_violations = _audit_student_conflicts(state, all_courses)

        _progress(99, "Audit: checking venue capacity…",
                  actual_scheduled, remaining_count)
        overcap_violations = _audit_venue_overcapacity(state, all_courses)

        if lecturer_violations == 0:
            print("[Audit] ✓ Zero lecturer double-bookings")
        if student_violations == 0:
            print("[Audit] ✓ Zero student conflicts")

        family_status: Dict[str, List] = defaultdict(list)
        for c in all_courses:
            if c.id in scheduled_ids:
                nc = normalize_course_code(c.course_code or "")
                if nc in analysis.shared_unit_groups:
                    family_status[nc].append(c.id)

        split_families = [
            nc for nc, placed_ids in family_status.items()
            if len(placed_ids) < len(analysis.shared_unit_groups.get(nc, []))
        ]

        if not split_families:
            print("[v50 VALIDATION] ✓ All course families intact")

        families_placed_count = len(state.placed_families)

        if remaining_count == 0:
            message = (
                f"SUCCESS! All {actual_scheduled} courses scheduled. "
                f"Strategy={strategy.mode} Pressure={strategy.seat_pressure:.1%} | "
                f"Families={p1_placed} Saturation={phase2_count} "
                f"Fill={p3_placed} FamilyRescue={p3b_placed} Forced={p4_placed} "
                f"DB={p5_placed} Nuclear={p7_placed} Ultimate={p8_placed} | "
                f"FamiliesComplete={families_placed_count}/{len(analysis.shared_unit_groups)} | "
                f"SplitFamilies={len(split_families)} | "
                f"LecturerViolations={lecturer_violations} | "
                f"StudentViolations={student_violations} | "
                f"OverCapViolations={overcap_violations} | "
                f"DuplicatesFixed={duplicate_fixes}"
            )
        else:
            message = (
                f"Scheduled {actual_scheduled}/{total_courses}. "
                f"{remaining_count} unscheduled. "
                f"Strategy={strategy.mode} Pressure={strategy.seat_pressure:.1%} | "
                f"FamilyRescue={p3b_placed} Nuclear={p7_placed} Ultimate={p8_placed} | "
                f"FamiliesComplete={families_placed_count}/{len(analysis.shared_unit_groups)} | "
                f"SplitFamilies={len(split_families)} | "
                f"LecturerViolations={lecturer_violations} | "
                f"StudentViolations={student_violations} | "
                f"OverCapViolations={overcap_violations} | "
                f"DuplicatesFixed={duplicate_fixes}"
            )

        _progress(100, message, actual_scheduled, remaining_count)

        return {
            "status": "completed" if remaining_count == 0 else "partial",
            "message": message,
            "scheduled_count": actual_scheduled,
            "remaining_count": remaining_count,
            "phase_stats": {
                "families_first": p1_placed,
                "saturation": phase2_count,
                "fill": p3_placed,
                "family_rescue": p3b_placed,
                "forced": p4_placed,
                "db_fallback": p5_placed,
                "nuclear": p7_placed,
                "ultimate": p8_placed,
            },
            "family_stats": {
                "total_families": len(analysis.shared_unit_groups),
                "families_placed": families_placed_count,
                "split_families": len(split_families),
                "split_family_codes": split_families[:10],
            },
            "audit": {
                "lecturer_violations": lecturer_violations,
                "student_violations": student_violations,
                "overcapacity_violations": overcap_violations,
                "duplicates_removed": total_raw - total_courses,
                "duplicate_placements_fixed": duplicate_fixes,
                "split_families": len(split_families),
            },
            "pre_scheduling_intelligence": {
                "strategy_mode": strategy.mode,
                "seat_pressure": round(strategy.seat_pressure, 4),
                "total_seat_supply": strategy.total_seat_supply,
                "total_student_demand": strategy.total_student_demand,
                "near_fit_threshold": strategy.near_fit_threshold,
                "use_evening_slots": strategy.use_evening_slots,
                "high_pressure_cohorts": strategy.high_pressure_cohorts,
                "infeasible_cohorts": strategy.infeasible_cohorts,
                "overloaded_lecturers": strategy.overloaded_lecturers,
                "multi_cohort_lecturers": strategy.multi_cohort_lecturers,
                "bottleneck_days": strategy.bottleneck_days,
                "warnings": strategy.warnings,
            },
        }

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        print(f"[AutoScheduler v50] Fatal:\n{tb}")
        scheduler_logger.error("Exam autoscheduler v50 fatal error: %s\n%s", exc, tb)
        return {
            "status": "error",
            "message": f"Scheduling failed: {exc}",
            "scheduled_count": 0,
            "remaining_count": total_courses,
        }