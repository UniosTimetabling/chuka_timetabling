"""
Regular Timetable Auto-Scheduler
=================================
Assigns every CourseAllocation to a (day, time-slot, venue) triple for the
regular teaching timetable and persists results to TempTimetable.
"""

import random
import re
import os
import sys
import time
import threading
import traceback
from collections import defaultdict
from dataclasses import dataclass, field as _field
from datetime import datetime, date, timedelta, time as dtime
from typing import List, Tuple, Optional, Dict, Set, Any, NamedTuple
import heapq
from functools import wraps

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views import View
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.db import transaction, IntegrityError, OperationalError, connection
from django.db.models import Q, Count

from timetable.models import (
    TempTimetable,
    SchedulerConfig,
    MergedCourseGroupTimetable,
    AutoMergedExamGroup,
)
from notifications.models import Notification
from course_allocation.models import CourseAllocation, CombinedCourseGroup
from room_management.models import Venue, Building, VenueSpecialization
from program_management.models import ProgramCourse, Program
from faculty_management.models import Faculty
from department_management.models import Department
from core import scheduling_constraints as constraint_engine


# ═══════════════════════════════════════════════════════════════════════════════
# PRE-SCHEDULING ANALYSIS ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CohortProfile:
    program_id: int
    program_name: str
    year: int
    student_count: int
    course_count: int
    lecturer_ids: Set[int]
    shared_lecturers: Dict[tuple, Set[int]]
    available_slot_count: int = 0
    adequate_venue_slot_count: int = 0
    risk_level: str = "LOW"
    risk_reasons: List[str] = _field(default_factory=list)
    recommended_max_per_day: int = 2


@dataclass
class LecturerProfile:
    lecturer_id: int
    lecturer_name: str
    course_count: int
    cohorts: List[tuple]
    busy_slots_estimate: int = 0
    risk_level: str = "LOW"
    risk_reasons: List[str] = _field(default_factory=list)


@dataclass
class CourseConstraintProfile:
    norm_code: str
    student_count: int
    program_id: Optional[int]
    year: int
    lecturer_id: Optional[int]
    is_pg: bool
    collision_peers: int
    capacity_shortage: bool
    difficulty_score: float


@dataclass
class SchedulingAnalysisReport:
    total_courses: int = 0
    total_students_demand: int = 0
    total_venue_capacity: int = 0
    total_available_seat_slots: int = 0
    cohort_profiles: Dict[tuple, CohortProfile] = _field(default_factory=dict)
    lecturer_profiles: Dict[int, LecturerProfile] = _field(default_factory=dict)
    course_difficulty_order: List[CourseConstraintProfile] = _field(default_factory=list)
    global_seat_deficit: int = 0
    marginal_courses: List[str] = _field(default_factory=list)
    overflow_candidates: List[str] = _field(default_factory=list)
    cross_cohort_collision_pairs: int = 0
    high_collision_programs: List[str] = _field(default_factory=list)
    cohort_min_days_needed: Dict[tuple, int] = _field(default_factory=dict)
    is_feasible: bool = True
    feasibility_score: float = 1.0
    critical_issues: List[str] = _field(default_factory=list)
    warnings: List[str] = _field(default_factory=list)
    recommendations: List[str] = _field(default_factory=list)
    analysis_notes: List[str] = _field(default_factory=list)

    def summary_lines(self) -> List[str]:
        lines = [
            "=" * 70,
            "PRE-SCHEDULING ANALYSIS REPORT",
            "=" * 70,
            f"  Total allocations      : {self.total_courses}",
            f"  Total student demand   : {self.total_students_demand} seat-needs",
            f"  Total venue capacity   : {self.total_venue_capacity} seats",
            f"  Available seat-slots   : {self.total_available_seat_slots}",
            f"  Global seat deficit    : {self.global_seat_deficit}",
            f"  Cohorts analysed       : {len(self.cohort_profiles)}",
            f"  Lecturers analysed     : {len(self.lecturer_profiles)}",
            f"  Cross-cohort collisions: {self.cross_cohort_collision_pairs} shared-lecturer pairs",
            f"  Marginal cap courses   : {len(self.marginal_courses)}",
            f"  Overflow candidates    : {len(self.overflow_candidates)}",
            f"  Feasibility score      : {self.feasibility_score:.3f}",
            f"  Feasible               : {'YES' if self.is_feasible else 'NO — see critical issues'}",
        ]
        if self.critical_issues:
            lines.append("  CRITICAL ISSUES:")
            for ci in self.critical_issues:
                lines.append(f"    ✗ {ci}")
        if self.warnings:
            lines.append("  WARNINGS:")
            for w in self.warnings[:10]:
                lines.append(f"    ⚠ {w}")
        if self.recommendations:
            lines.append("  RECOMMENDATIONS:")
            for r in self.recommendations[:10]:
                lines.append(f"    → {r}")
        lines.append("=" * 70)
        return lines


def _effective_window_demand(allocs: List) -> int:
    """How many DISTINCT time-windows a (program, year) cohort actually needs.

    A naive `len(allocs)` overcounts badly, because many allocations in the
    same cohort are mutually exempt from clashing (see
    `is_program_year_collision_exempt`, defined further below) and can
    legitimately share a slot with each other:

      * different `intake` (normal vs special)      → never compete
      * different, explicitly-set `student_group`   → never compete
      * electives / selection-group courses          → never compete with
        anything (a student only ever sits ONE elective at a time)
      * different `specialization_stem` in the same
        category                                     → never compete
        (a student picks exactly one stem)

    This mirrors that logic to estimate the true peak concurrent-course
    count, instead of just summing every allocation in the cohort.
    """
    if not allocs:
        return 0

    # Bucket by (intake, student_group_id) — courses in different buckets
    # never compete for the same window.
    buckets: Dict[Tuple[str, Optional[int]], List] = defaultdict(list)
    for a in allocs:
        buckets[(_get_alloc_intake(a), getattr(a, 'student_group_id', None))].append(a)

    peak = 0
    for bucket_allocs in buckets.values():
        # Electives / selection-group courses are exempt from clashing with
        # anything at all, so they cost nothing towards the window count.
        core = [
            a for a in bucket_allocs
            if not _is_elective(a) and _get_selection_group_id(a) is None
        ]

        # Group remaining courses by specialization-stem category: courses
        # with no stem always compete; courses in the same stem always
        # compete with each other; courses in different stems of the same
        # category never compete (student picks one stem), so only the
        # largest stem in each category counts.
        no_stem_count = 0
        stems_by_category: Dict[Any, Dict[Any, int]] = defaultdict(lambda: defaultdict(int))
        for a in core:
            stem_id = _get_specialization_stem_id(a)
            if stem_id is None:
                no_stem_count += 1
            else:
                cat_id = _get_specialization_category_id(a)
                stems_by_category[cat_id][stem_id] += 1

        bucket_demand = no_stem_count
        for cat_id, stem_counts in stems_by_category.items():
            bucket_demand += max(stem_counts.values())

        peak = max(peak, bucket_demand)

    return peak


def analyse_scheduling_data(
    all_courses: List,
    all_venues: List,
    days: List[str],
    slots: List[Tuple[dtime, dtime]],
    cache,
    merge_limit: int = 200,
    max_per_day: int = 2,
) -> SchedulingAnalysisReport:
    """Pre-scheduling analysis - comprehensive constraint examination.

    NOTE: `max_per_day` is advisory only (used for the difficulty-score
    denominator and the reported `recommended_max_per_day`). It is no
    longer enforced as a hard cap anywhere in the scheduler — a
    program-year cohort may be scheduled more than this many times in
    a day when the course load requires it.
    """
    report = SchedulingAnalysisReport()
    n_days = len(days)
    n_slots = len(slots)

    safe_print("\n" + "=" * 70)
    safe_print("PRE-SCHEDULING ANALYSIS — collecting data …")
    safe_print("=" * 70)

    report.total_courses = len(all_courses)
    report.total_venue_capacity = sum(v.capacity or 0 for v in all_venues)
    report.total_available_seat_slots = report.total_venue_capacity * n_days * n_slots
    largest_venue_cap = max((v.capacity or 0 for v in all_venues), default=0)

    safe_print(f"[Analysis] {report.total_courses} allocations | "
               f"{len(all_venues)} venues | "
               f"{n_days} days × {n_slots} slots = "
               f"{n_days * n_slots} time-windows")

    # Build cohort → courses map
    cohort_courses: Dict[tuple, list] = defaultdict(list)
    cohort_student_max: Dict[tuple, int] = defaultdict(int)
    lecturer_cohorts: Dict[int, Set[tuple]] = defaultdict(set)

    for alloc in all_courses:
        pid = alloc.program.id if alloc.program else None
        lid = alloc.lecturer.id if alloc.lecturer else None
        try:
            yr = cache.get_course_year(alloc)
        except Exception:
            yr = 1
        n_students = alloc.number_of_students or 0

        report.total_students_demand += n_students

        if pid:
            key = (pid, yr)
            cohort_courses[key].append(alloc)
            if n_students > cohort_student_max[key]:
                cohort_student_max[key] = n_students

        if lid and pid:
            lecturer_cohorts[lid].add((pid, yr))

    safe_print(f"[Analysis] {len(cohort_courses)} distinct (program×year) cohorts found")
    safe_print(f"[Analysis] {len(lecturer_cohorts)} lecturers with ≥1 allocation")
    safe_print(f"[Analysis] Total student-seat demand: {report.total_students_demand}")

    # Build cohort profiles
    for (pid, yr), allocs in cohort_courses.items():
        prog_name = allocs[0].program.name if allocs[0].program else f"prog_{pid}"
        n_students = cohort_student_max[(pid, yr)]
        lect_ids = set()
        for a in allocs:
            if a.lecturer:
                lect_ids.add(a.lecturer.id)

        total_course_count = _effective_window_demand(allocs)
        max_placeable = n_days * n_slots  # bounded by real slots, not an artificial per-day cap
        slot_pool = n_days * n_slots

        adequate_venues = [v for v in all_venues if (v.capacity or 0) >= n_students]
        adequate_slot_count = len(adequate_venues) * n_days * n_slots

        risk_reasons = []
        if total_course_count > max_placeable:
            risk_reasons.append(
                f"Course count ({total_course_count}) exceeds total available "
                f"time-windows ({n_days} days × {n_slots} slots = {max_placeable})"
            )
        if not adequate_venues:
            risk_reasons.append(
                f"NO venue can seat {n_students} students (largest={largest_venue_cap})"
            )
        elif len(adequate_venues) * n_days < total_course_count:
            risk_reasons.append(
                f"Only {len(adequate_venues)} venue(s) can seat {n_students} students; "
                f"with {n_days} days that gives {len(adequate_venues) * n_days} unique slots "
                f"but {total_course_count} courses need placing"
            )
        if n_students > largest_venue_cap:
            risk_reasons.append(
                f"Student count ({n_students}) exceeds largest venue ({largest_venue_cap})"
            )

        if n_students > largest_venue_cap or total_course_count > max_placeable:
            risk = "CRITICAL"
        elif len(adequate_venues) == 0:
            risk = "CRITICAL"
        elif (len(adequate_venues) * n_days) < (total_course_count * 1.5):
            risk = "HIGH"
        elif n_students > largest_venue_cap * 0.90:
            risk = "HIGH"
        elif n_students > largest_venue_cap * 0.75:
            risk = "MEDIUM"
        else:
            risk = "LOW"

        profile = CohortProfile(
            program_id=pid,
            program_name=prog_name,
            year=yr,
            student_count=n_students,
            course_count=total_course_count,
            lecturer_ids=lect_ids,
            shared_lecturers={},
            available_slot_count=slot_pool,
            adequate_venue_slot_count=adequate_slot_count,
            risk_level=risk,
            risk_reasons=risk_reasons,
            recommended_max_per_day=max_per_day,
        )
        report.cohort_profiles[(pid, yr)] = profile

    # Lecturer profiles + shared-lecturer detection
    for lid, cohort_set in lecturer_cohorts.items():
        sample = next(
            (a for a in all_courses if a.lecturer and a.lecturer.id == lid),
            None
        )
        lname = str(sample.lecturer) if sample and sample.lecturer else f"lect_{lid}"
        n_courses = sum(1 for a in all_courses if a.lecturer and a.lecturer.id == lid)

        busy_estimate = len(cohort_set)
        risk_reasons_l = []
        if len(cohort_set) > n_days * n_slots:
            risk_reasons_l.append(
                f"Lecturer teaches {len(cohort_set)} cohorts but only "
                f"{n_days * n_slots} total slots exist — impossible to avoid overlap"
            )
        if n_courses > (n_days * n_slots * 0.7):
            risk_reasons_l.append(
                f"Lecturer has {n_courses} courses covering "
                f"{n_courses / (n_days * n_slots) * 100:.0f}% of all time windows"
            )

        risk_l = "LOW"
        if risk_reasons_l:
            risk_l = "CRITICAL" if len(cohort_set) > n_days * n_slots else "HIGH"
        elif len(cohort_set) > n_days:
            risk_l = "MEDIUM"

        report.lecturer_profiles[lid] = LecturerProfile(
            lecturer_id=lid,
            lecturer_name=lname,
            course_count=n_courses,
            cohorts=list(cohort_set),
            busy_slots_estimate=busy_estimate,
            risk_level=risk_l,
            risk_reasons=risk_reasons_l,
        )

    # Cross-cohort shared-lecturer analysis
    cohort_keys = list(cohort_courses.keys())
    for i in range(len(cohort_keys)):
        for j in range(i + 1, len(cohort_keys)):
            ka, kb = cohort_keys[i], cohort_keys[j]
            pa = report.cohort_profiles.get(ka)
            pb = report.cohort_profiles.get(kb)
            if pa is None or pb is None:
                continue
            shared = pa.lecturer_ids & pb.lecturer_ids
            if shared:
                report.cross_cohort_collision_pairs += 1
                pa.shared_lecturers[kb] = shared
                pb.shared_lecturers[ka] = shared

    safe_print(
        f"[Analysis] {report.cross_cohort_collision_pairs} "
        f"cross-cohort shared-lecturer constraint pairs"
    )

    high_col = [
        f"{p.program_name} Yr{p.year} (shares lecturers with "
        f"{len(p.shared_lecturers)} other cohorts)"
        for p in report.cohort_profiles.values()
        if len(p.shared_lecturers) >= 3
    ]
    report.high_collision_programs = high_col[:20]

    # Per-course difficulty scoring
    course_profiles: List[CourseConstraintProfile] = []

    for alloc in all_courses:
        pid = alloc.program.id if alloc.program else None
        lid = alloc.lecturer.id if alloc.lecturer else None
        try:
            yr = cache.get_course_year(alloc)
        except Exception:
            yr = 1
        n_students = alloc.number_of_students or 0
        norm_code = normalize_course_code(alloc.course_code)
        is_pg = is_postgraduate_course(alloc)

        key = (pid, yr)
        collision_peers = len(cohort_courses.get(key, [])) - 1
        cap_short = n_students > largest_venue_cap

        shared_lect_penalty = 0
        if pid and (pid, yr) in report.cohort_profiles:
            shared_lect_penalty = len(report.cohort_profiles[(pid, yr)].shared_lecturers)

        score = (
            (n_students / max(largest_venue_cap, 1)) * 40.0
            + (collision_peers / max(n_days * max_per_day, 1)) * 30.0
            + (shared_lect_penalty / max(n_days, 1)) * 20.0
            + (10.0 if cap_short else 0.0)
            - (5.0 if is_pg else 0.0)
        )

        course_profiles.append(CourseConstraintProfile(
            norm_code=norm_code,
            student_count=n_students,
            program_id=pid,
            year=yr,
            lecturer_id=lid,
            is_pg=is_pg,
            collision_peers=collision_peers,
            capacity_shortage=cap_short,
            difficulty_score=score,
        ))

        if cap_short:
            report.marginal_courses.append(
                f"{norm_code} ({n_students} students, largest venue cap {largest_venue_cap})"
            )
        elif n_students > largest_venue_cap * 0.90:
            report.overflow_candidates.append(
                f"{norm_code} ({n_students}/{largest_venue_cap} = "
                f"{n_students/largest_venue_cap*100:.0f}% of largest venue)"
            )

    course_profiles.sort(key=lambda cp: cp.difficulty_score, reverse=True)
    report.course_difficulty_order = course_profiles

    # Global seat deficit
    total_needed = report.total_students_demand
    total_cap_slots = report.total_available_seat_slots
    report.global_seat_deficit = max(0, total_needed - total_cap_slots)

    # Feasibility scoring
    deductions = 0.0

    if report.marginal_courses:
        deductions += min(0.4, len(report.marginal_courses) * 0.05)
        for mc in report.marginal_courses[:5]:
            report.critical_issues.append(f"No venue large enough: {mc}")

    if report.global_seat_deficit > 0:
        deficit_ratio = report.global_seat_deficit / max(total_needed, 1)
        deductions += min(0.5, deficit_ratio)
        report.critical_issues.append(
            f"Global seat deficit: {report.global_seat_deficit} seats "
            f"({deficit_ratio*100:.1f}% of demand uncoverable)"
        )

    critical_cohorts = [
        p for p in report.cohort_profiles.values()
        if p.risk_level == "CRITICAL"
    ]
    if critical_cohorts:
        deductions += min(0.3, len(critical_cohorts) * 0.05)
        for cp in critical_cohorts[:5]:
            for rr in cp.risk_reasons:
                report.critical_issues.append(
                    f"{cp.program_name} Yr{cp.year}: {rr}"
                )

    overloaded_lects = [
        lp for lp in report.lecturer_profiles.values()
        if lp.risk_level in ("CRITICAL", "HIGH")
    ]
    if overloaded_lects:
        deductions += min(0.2, len(overloaded_lects) * 0.03)
        for lp in overloaded_lects[:5]:
            for rr in lp.risk_reasons:
                report.warnings.append(
                    f"Lecturer {lp.lecturer_name}: {rr}"
                )

    if report.overflow_candidates:
        report.warnings.append(
            f"{len(report.overflow_candidates)} courses at >90% of largest venue — "
            f"will be prioritised for early placement"
        )

    if report.high_collision_programs:
        for hc in report.high_collision_programs[:5]:
            report.warnings.append(f"High-collision cohort: {hc}")

    report.feasibility_score = max(0.0, 1.0 - deductions)
    report.is_feasible = report.feasibility_score > 0.30

    # Recommendations
    if report.marginal_courses:
        report.recommendations.append(
            "Consider splitting large cohorts OR adding a venue with capacity ≥ "
            f"{max(c.student_count for c in course_profiles if c.capacity_shortage)} seats"
        )
    if len(report.overflow_candidates) > 3:
        report.recommendations.append(
            f"{len(report.overflow_candidates)} courses are near-capacity; "
            f"schedule them in the first pass to claim large venues early"
        )
    if report.cross_cohort_collision_pairs > n_days * 3:
        report.recommendations.append(
            f"High cross-cohort lecturer sharing ({report.cross_cohort_collision_pairs} pairs). "
            f"Consider adding more days or time-slots to reduce bottleneck."
        )
    if report.global_seat_deficit > 0:
        report.recommendations.append(
            "Global seat deficit detected.  Adding even 1 large venue or 1 extra day "
            "significantly increases schedulability."
        )
    if not report.recommendations:
        report.recommendations.append(
            "Data looks well-configured.  Scheduler should achieve high placement rate."
        )

    for p in sorted(
        report.cohort_profiles.values(),
        key=lambda x: getattr(x, 'course_count', 0),
        reverse=True
    )[:20]:
        note = (
            f"Cohort [{p.program_name} Yr{p.year}]: "
            f"{p.course_count} courses, {p.student_count} students, "
            f"risk={p.risk_level}, "
            f"shared-lect-pairs={len(p.shared_lecturers)}"
        )
        report.analysis_notes.append(note)

    for line in report.summary_lines():
        safe_print(line)

    return report


def build_difficulty_ordered_tasks(
    all_tasks: List,
    report: SchedulingAnalysisReport,
    cache,
) -> List:
    """Reorder tasks by difficulty - hardest first."""
    score_map: Dict[tuple, float] = {}
    for cp in report.course_difficulty_order:
        key = (cp.norm_code, cp.program_id, cp.year)
        score_map[key] = max(score_map.get(key, 0.0), cp.difficulty_score)

    def task_score(task) -> float:
        allocs = _task_allocs(task)
        scores = []
        for a in allocs:
            try:
                yr = cache.get_course_year(a)
            except Exception:
                yr = 1
            pid = a.program.id if a.program else None
            nc = normalize_course_code(a.course_code)
            key = (nc, pid, yr)
            scores.append(score_map.get(key, 0.0))
        base = max(scores) if scores else 0.0
        if _task_is_pg(task):
            base -= 1000.0
        return base

    return sorted(all_tasks, key=task_score, reverse=True)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

_print_lock = threading.Lock()
_log_buffer = []
_MAX_LOG_BUFFER = 200

# ── File-based run log ────────────────────────────────────────────────────────
# Each scheduler run writes a timestamped .txt file to a `logs/` folder
# that sits alongside this algorithm file.  If the folder does not exist it is
# created automatically.  The handle is module-level so safe_print() can write
# to it without passing it everywhere.
_ALGO_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR  = os.path.join(_ALGO_DIR, "logs")
_scheduler_log_fh = None   # file handle; None when no run is active


def _open_scheduler_log() -> None:
    """
    Create (or re-create) the per-run log file.
    Called once at the very start of run_optimized_autoscheduler_thread().
    File name: regular_timetable_scheduler_YYYY-MM-DD_HH-MM-SS.txt
    """
    global _scheduler_log_fh
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_path  = os.path.join(
            _LOG_DIR,
            f"regular_timetable_scheduler_{timestamp}.txt"
        )
        # Close any stale handle from a previous run that ended abruptly
        if _scheduler_log_fh is not None:
            try:
                _scheduler_log_fh.close()
            except Exception:
                pass
        _scheduler_log_fh = open(log_path, "w", encoding="utf-8", buffering=1)
        _scheduler_log_fh.write(
            f"Regular Timetable Auto-Scheduler — Run Log\n"
            f"Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Log file: {log_path}\n"
            f"{'=' * 70}\n\n"
        )
    except Exception as _e:
        # Logging must never crash the scheduler
        try:
            sys.stdout.write(f"[RunLog] Could not open log file: {_e}\n")
        except Exception:
            pass


def _close_scheduler_log(success: bool = True) -> None:
    """
    Flush and close the per-run log file.
    Called from the finally block of run_optimized_autoscheduler_thread().
    """
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
        # ── Write to run log file ─────────────────────────────────────────
        if _scheduler_log_fh is not None:
            try:
                ts = datetime.now().strftime("%H:%M:%S")
                _scheduler_log_fh.write(f"[{ts}] {message}\n")
            except Exception:
                pass
    except Exception:
        pass


scheduler_progress = {
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
    'unscheduled_courses': []
}

progress_lock = threading.Lock()


def update_progress(progress: int,
                    current_action: str,
                    scheduled_count: int,
                    remaining_count: int,
                    batch_info: str = "",
                    current_batch: int = 0,
                    total_batches: int = 0,
                    console_message: str = "",
                    scheduled_courses: List = None,
                    unscheduled_courses: List = None,
                    status: Optional[str] = None) -> None:
    with progress_lock:
        scheduler_progress.update({
            'progress': int(progress),
            'current_action': current_action,
            'scheduled_count': scheduled_count,
            'remaining_count': remaining_count,
            'batch_info': batch_info,
            'current_batch': current_batch,
            'total_batches': total_batches,
        })
        if status is not None:
            scheduler_progress['status'] = status
        if console_message:
            scheduler_progress['console_output'].append(console_message)
            if len(scheduler_progress['console_output']) > 50:
                scheduler_progress['console_output'] = scheduler_progress['console_output'][-50:]
        if scheduled_courses is not None:
            scheduler_progress['scheduled_courses'] = scheduled_courses
        if unscheduled_courses is not None:
            scheduler_progress['unscheduled_courses'] = unscheduled_courses


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


def _fuzzy_slot_lookup(start_time, end_time, slots: List[Tuple[dtime, dtime]],
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


def is_postgraduate_course(course_allocation) -> bool:
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


def get_pg_slot_preference(slots: List[Tuple[dtime, dtime]]) -> List[int]:
    n = len(slots)
    if n == 0:
        return []
    return list(range(n - 1, -1, -1))


def get_afternoon_slots(slots: List[Tuple[dtime, dtime]]) -> List[int]:
    n = len(slots)
    start = max(0, n // 2)
    return list(range(n - 1, start - 1, -1))


def get_course_year(course_allocation) -> int:
    course_code = course_allocation.course_code or ""
    program = course_allocation.program

    if not course_code:
        raise ValueError(f"Missing course code for allocation {course_allocation.id}")

    try:
        # Preferred path: the direct program_course FK. This is exact and
        # doesn't care what's stored in course_code (e.g. a disambiguated
        # label like "ZOOL 143(COM)" for a course shared by multiple
        # programs) -- it points straight at the curriculum row.
        pc = getattr(course_allocation, "program_course", None)
        if pc and pc.year and str(pc.year).isdigit():
            year = int(pc.year)
            if 1 <= year <= 6:
                return year

        # Fallback for any legacy allocation without a program_course link:
        # strip a trailing "(TAG)" disambiguation suffix, if present, before
        # matching against ProgramCourse's plain course_code.
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
            return 6

        m = re.search(r'0(\d)\d{2}', normalized_code)
        if m:
            year = int(m.group(1))
            if 1 <= year <= 6:
                return year

        return 1

    except Exception as e:
        safe_print(f"Year detection warning for {course_code}: {e}")
        return 1


def lecturer_priority(lecturer) -> int:
    if not lecturer:
        return 3
    if getattr(lecturer, "designation", "").lower() in ["prof", "dr"]:
        return 1
    return 2


def _collapse_redundant_zero_padding(digits: str) -> str:
    """Return the digit run UNCHANGED — the count of leading zeros is a
    meaningful part of a Chuka course code, not padding, and must never be
    altered.

    Chuka course codes encode the level of study by the number of leading
    zeros on the numeric part:
        'SOIL 271'    -> degree      (no leading zero)
        'SOIL 0100'   -> diploma     (one leading zero)
        'SOIL 00100'  -> certificate (two leading zeros)
    These are three DIFFERENT courses, with different students and often
    different lecturers/venues. An earlier version of this function
    collapsed any extra leading zeros down to a single one on the (wrong)
    assumption that anything beyond one zero was just inconsistent typing
    of the same diploma code. That assumption was incorrect: it caused
    'SOIL 0100' (diploma) and 'SOIL 00100' (certificate) to normalize to
    the identical key 'SOIL0100' and get silently merged into one class.
    The fix is to not touch the digit run at all — every leading zero is
    kept exactly as typed, so codes that differ only in zero-count remain
    distinct.
    """
    return digits


_SECTION_SUFFIX_RE = re.compile(r"([-_][A-Z0-9]{1,3})$")

# Word-level section/stream tags that data entry writes as separate,
# space-delimited words rather than a '-A'/'_C' style suffix — e.g.
# "ECON 111 GROUP C", "ECON 313 GRP B", "ECON 443 SECTION A". These never
# get caught by _SECTION_SUFFIX_RE because there is no '-' or '_' before
# them, so a course that is otherwise identical (same base code, same
# lecturer) silently normalizes to a DIFFERENT key just because one row
# says "GROUP C" and another says nothing at all — which is exactly what
# was defeating merges of ECON111/ECON313/ECON443 sections above that were
# well under merge_limit and taught by the same lecturer. Matched on the
# raw, still-spaced code (before split_course_code_suffix runs) so the
# word boundary is still visible.
_GROUP_WORD_SUFFIX_RE = re.compile(
    r"\s+(?:GROUP|GRP|SECTION|SEC|STREAM)\s*[A-Z0-9]{1,3}$", re.IGNORECASE
)
# A single trailing bare letter/short alnum token used as an ad-hoc section
# tag with no keyword at all — e.g. "ECON 313 b", "ECON 443 A". Only
# matched when it's a separate word (whitespace before it) so it can never
# eat part of a genuine numeric course number like "ECON 313".
_BARE_TRAILING_SECTION_RE = re.compile(r"\s+[A-Za-z][A-Za-z0-9]?$")


def strip_group_section_words(raw_code: str) -> str:
    """Strip a trailing word-style section/stream tag ("GROUP C", "GRP B",
    "SECTION A", or a bare trailing letter like "b"/"A") from a raw course
    code string that still has its original spacing, so merge-matching can
    see 'ECON 313', 'ECON 313 GROUP B' and 'ECON 313 b' as the same base
    course. Only strips one such trailing tag, and only when there is a
    real course code before it, so it never eats the whole string.
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
    introduced by '-' or '_' — e.g. 'COSC00101-A' → ('COSC00101', '-A'),
    'COSC00011_C' → ('COSC00011', '_C'). Returns (code, '') if there is no
    such trailing tag.
    """
    m = _SECTION_SUFFIX_RE.search(code)
    if not m:
        return code, ""
    return code[:m.start()], m.group(1)


def normalize_course_code(raw_code: str) -> str:
    if not raw_code:
        return ""
    code = raw_code.strip().upper()
    code = re.sub(r"\([^)]*\)", "", code)
    code = re.sub(r"\s+", "", code)
    # Split off any trailing section/stream suffix (e.g. '-A', '_C') FIRST,
    # so the zero-padding normalization below runs on the base code only —
    # never on the suffix — and after that base normalization the suffix is
    # simply re-attached exactly as typed. This is deliberate ordering:
    # 'COSC 00101-A' normalizes its base to 'COSC0101' and then reattaches
    # '-A' untouched; it is never merged into, or confused with, the digit
    # run being collapsed. Two different section tags ('-A' vs '-B') on the
    # same base code therefore still produce two DIFFERENT normalized keys
    # here (sections are distinct classes) — the only place that
    # deliberately looks past the suffix is normalize_course_code_base,
    # used for unassigned-lecturer common-unit merging (see
    # build_global_merged_tasks).
    base, suffix = split_course_code_suffix(code)
    # The digit run (including its leading zeros) is left exactly as typed:
    # the number of leading zeros distinguishes course level at Chuka
    # (e.g. 'SOIL271' degree vs 'SOIL0100' diploma vs 'SOIL00100'
    # certificate), so it must never be collapsed or otherwise altered —
    # see _collapse_redundant_zero_padding for why. This call is kept as a
    # deliberate no-op pass-through (rather than deleted outright) so the
    # invariant is documented in one obvious place and can't be quietly
    # reintroduced by a future "helpful" cleanup.
    base = re.sub(r"\d+", lambda m: _collapse_redundant_zero_padding(m.group()), base)
    return base + suffix


def normalize_course_code_base(raw_code: str) -> str:
    """Like normalize_course_code, but with any trailing section/stream tag
    stripped entirely — both the '-A'/'_C' style and the word-style
    'GROUP C' / 'GRP B' / 'SECTION A' / bare-letter 'b' style that data
    entry uses just as often (see strip_group_section_words).

    Used for merge-matching in build_global_merged_tasks and
    _validate_and_rebuild_merged_group. Sections of the same course that
    differ only by a section tag — 'COMS101-A' vs 'COMS101-B', or
    'ECON 313' vs 'ECON 313 GROUP B' vs 'ECON 313 b' — are the same class
    content-wise. This is used regardless of whether a lecturer is assigned:
    the (norm_code, lecturer_id) grouping key in build_global_merged_tasks
    already keeps sections taught by different lecturers apart, so once the
    lecturer matches too, a bare section tag is the only thing that would
    otherwise keep two (or more) small sections of the same common unit from
    combining into one shared slot — which is exactly the "wasted space"
    case where several sub-200-student sections of the same course, same
    lecturer, sat in separate venues instead of one merged one.
    """
    stripped = strip_group_section_words(raw_code)
    base, _suffix = split_course_code_suffix(normalize_course_code(stripped))
    return base


# ═══════════════════════════════════════════════════════════════════════════════
# DUPLICATE PREVENTION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _normalized_course_name(raw_name: Optional[str]) -> str:
    """Collapse a course NAME down to letters/digits only for merge-name
    comparison, so cosmetic differences — extra spaces, punctuation,
    case, a trailing '(Group B)' or department tag — don't make two rows
    of the SAME course look like different courses and block a legitimate
    merge. Returns '' for missing/blank names (callers should treat '' as
    a wildcard that never blocks a merge, since a blank name carries no
    information either way).
    """
    if not raw_name:
        return ""
    return re.sub(r"[^A-Z0-9]+", "", raw_name.strip().upper())


def make_course_schedule_key(course_code: str, lecturer_id, program_id, student_group_id=None) -> tuple:
    # NOTE: student_group_id is included so that different student groups
    # (e.g. "Group A" vs "Group B") taking the *same* course, under the same
    # program, with the same lecturer, are NOT treated as duplicates of one
    # another. Without this, once Group A's allocation for a course is
    # scheduled, Group B's allocation for that same course would be silently
    # skipped as "canonical key already placed" — even though it is a
    # distinct cohort that still needs its own timetable slot.
    return (normalize_course_code(course_code), lecturer_id, program_id, student_group_id)


def deduplicate_timetable_entries() -> int:
    duplicates = (
        TempTimetable.objects
        .values('course_allocation_id', 'day', 'start_time', 'end_time')
        .annotate(cnt=Count('id'))
        .filter(cnt__gt=1)
    )

    removed = 0
    for dup in duplicates:
        entries = TempTimetable.objects.filter(
            course_allocation_id=dup['course_allocation_id'],
            day=dup['day'],
            start_time=dup['start_time'],
            end_time=dup['end_time'],
        ).order_by('id')

        ids_to_delete = list(entries.values_list('id', flat=True)[1:])
        if ids_to_delete:
            try:
                with transaction.atomic():
                    deleted, _ = TempTimetable.objects.filter(
                        id__in=ids_to_delete
                    ).delete()
                    removed += deleted
            except Exception as e:
                safe_print(f"[Dedup] Error removing duplicates: {e}")

    if removed:
        safe_print(f"[Dedup] Removed {removed} duplicate TempTimetable row(s)")
    else:
        safe_print("[Dedup] No duplicates found — timetable is clean")

    return removed


# ═══════════════════════════════════════════════════════════════════════════════
# VENUE SPECIALIZATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def build_specialization_index():
    """
    Build the course→venue lookup used by the specialization ("SpecPass") pass.

    IMPORTANT — program scoping:
    A rule that reserves venues "for Program X" is expanded from
    Program X's curriculum (rule.programs__courses / rule.departments…).
    That curriculum can include *service courses* that are also taught,
    under the same course_code, to entirely different programs (e.g.
    COSC 103 is on the Law Year 1 curriculum but is primarily a
    Computer Science course also taken by several other programs).

    A rule expanded from a program/department must therefore only match
    allocations that actually BELONG to that program — never every
    allocation anywhere with a matching course_code. Only rules built
    from an EXPLICIT course list (rule.courses) are program-agnostic,
    because the admin picked those specific courses deliberately.

    The index key is now (norm_code, program_id) for program/department
    derived entries, and (norm_code, None) for explicit-course entries.
    """
    course_to_venues: Dict[Tuple[str, Optional[int]], list] = defaultdict(list)
    specialization_venue_ids: set = set()

    active_rules = (
        VenueSpecialization.objects
        .filter(is_active=True)
        .prefetch_related(
            'venues',
            'courses',
            'programs__courses',
            'departments__programs__courses',
        )
        .order_by('priority')
    )

    for rule in active_rules:
        rule_venues = list(rule.venues.all())
        for v in rule_venues:
            specialization_venue_ids.add(v.id)

        # Explicit courses picked directly on the rule: program-agnostic,
        # the admin named these courses on purpose.
        explicit_codes: set = set()
        for c in rule.courses.all():
            explicit_codes.add(normalize_course_code(c.course_code))

        # Courses reached via a program (or a department's programs):
        # only valid for allocations that belong to THAT program.
        program_scoped_codes: Dict[int, set] = defaultdict(set)

        for prog in rule.programs.all():
            for pc in prog.courses.all():
                program_scoped_codes[prog.id].add(normalize_course_code(pc.course_code))

        for dept in rule.departments.all():
            for prog in dept.programs.all():
                for pc in prog.courses.all():
                    program_scoped_codes[prog.id].add(normalize_course_code(pc.course_code))

        for code in explicit_codes:
            for v in rule_venues:
                course_to_venues[(code, None)].append((v, rule))

        for prog_id, codes in program_scoped_codes.items():
            for code in codes:
                for v in rule_venues:
                    course_to_venues[(code, prog_id)].append((v, rule))

    return dict(course_to_venues), specialization_venue_ids


def get_specialized_venues_for_task(task, course_to_venues: dict):
    allocs = _task_allocs(task) if not isinstance(task, dict) else task.get('merged', [task])
    matched: list = []
    seen_venue_ids: set = set()

    for alloc in allocs:
        norm = normalize_course_code(alloc.course_code)
        alloc_program_id = alloc.program_id

        # Program-scoped match: only fires when the allocation's own
        # program is the one the rule was actually built for.
        candidates = list(course_to_venues.get((norm, alloc_program_id), []))
        # Explicit-course match: program-agnostic, always applies.
        candidates += course_to_venues.get((norm, None), [])

        for venue, rule in candidates:
            if venue.id not in seen_venue_ids:
                seen_venue_ids.add(venue.id)
                matched.append((venue, rule))

    return matched


def process_specialized_pass(
    tasks,
    course_to_venues,
    specialization_venue_ids,
    days, slots,
    conflict_tracker,
    cache,
    globally_scheduled_alloc_ids,
):
    if not course_to_venues:
        return 0, [], [], []

    scheduled_count = 0
    unscheduled_strict: list = []
    scheduled_list: list = []
    unscheduled_list: list = []

    batch_entry_keys: set = set()

    safe_print(f"[SpecPass] Starting specialization priority pass for {len(tasks)} tasks …")

    for task in tasks:
        allocs = _task_allocs(task)

        if all(a.id in globally_scheduled_alloc_ids for a in allocs):
            continue

        designated = get_specialized_venues_for_task(task, course_to_venues)
        if not designated:
            continue

        is_merged = isinstance(task, dict)
        total_students = _task_students(task)
        rep = _representative(task)
        lecturer_id = rep.lecturer.id if rep.lecturer else None
        program_id = rep.program.id if rep.program else None
        student_group_id = getattr(rep, 'student_group_id', None)
        code = merged_course_label(task)

        if cache.is_course_already_scheduled(
            rep.course_code, lecturer_id, program_id, student_group_id
        ):
            safe_print(f"[SpecPass] SKIP {code}: course offering already scheduled")
            continue

        try:
            year = cache.get_course_year(rep)
        except Exception:
            year = 1

        is_strict = any(rule.strict for _, rule in designated)

        assigned = False

        # ── Spread across the week instead of always starting at Monday ──
        # Fixed day-order here previously meant every specialized-venue
        # course (dedicated venues, e.g. Law) piled onto Mon/Tue and never
        # even looked at Thu/Fri until those two days were completely full.
        # Order candidate days by this cohort's current load (lightest
        # first), with a random shuffle for ties, so courses actually
        # distribute across the whole week when multiple days are free —
        # exactly like the general (non-specialized) pass already does.
        day_order = list(days)
        random.shuffle(day_order)
        if program_id:
            day_order.sort(
                key=lambda d: conflict_tracker.get_program_year_day_load(program_id, year, d)
            )

        for venue, rule in designated:
            if assigned:
                break
            if (venue.capacity or 0) < total_students:
                safe_print(
                    f"[SpecPass] {code}: designated venue {venue.code} "
                    f"(cap {venue.capacity}) too small for {total_students} students — skipping"
                )
                continue

            for day in day_order:
                if assigned:
                    break
                for slot_idx, (start, end) in enumerate(slots):
                    if assigned:
                        break

                    if lecturer_id and conflict_tracker.has_lecturer_conflict(lecturer_id, day, slot_idx):
                        continue
                    if program_id and conflict_tracker.has_program_conflict(program_id, year, day, slot_idx, new_alloc=rep):
                        continue
                    if conflict_tracker.has_venue_conflict(venue.id, day, slot_idx):
                        continue

                    if is_merged:
                        skip = False
                        for alloc in allocs:
                            a_lid = alloc.lecturer.id if alloc.lecturer else None
                            a_pid = alloc.program.id if alloc.program else None
                            try:
                                a_yr = cache.get_course_year(alloc)
                            except Exception:
                                skip = True
                                break
                            if a_lid and conflict_tracker.has_lecturer_conflict(a_lid, day, slot_idx):
                                skip = True
                                break
                            if a_pid and conflict_tracker.has_program_conflict(a_pid, a_yr, day, slot_idx, new_alloc=alloc):
                                skip = True
                                break
                        if skip:
                            continue

                    entry_key = (venue.id, day, start, end)
                    if entry_key in batch_entry_keys:
                        continue
                    if cache.is_duplicate_entry(venue.id, day, start, end):
                        continue

                    entries_to_write = [
                        TempTimetable(
                            course_allocation=alloc,
                            venue=venue,
                            day=day,
                            start_time=start,
                            end_time=end,
                        )
                        for alloc in allocs
                    ]

                    rows_written = safe_bulk_create_timetable_entries(entries_to_write, cache)
                    if rows_written == 0:
                        safe_print(
                            f"[SpecPass] DB write failed for {code} @ "
                            f"{venue.code} {day} {start}–{end} — trying next slot"
                        )
                        continue

                    batch_entry_keys.add(entry_key)
                    conflict_tracker.add_merged_schedule(allocs, venue.id, day, slot_idx, cache)

                    for alloc in allocs:
                        globally_scheduled_alloc_ids.add(alloc.id)
                    cache.mark_course_scheduled(rep.course_code, lecturer_id, program_id, student_group_id)

                    scheduled_count += len(allocs)
                    assigned = True
                    label = (
                        f"{code} (Year {year}) → {venue.code} "
                        f"[SPECIALIZED/{rule.name}] ({day} {start}–{end})"
                    )
                    scheduled_list.append(label)
                    safe_print(f"[SpecPass] PLACED: {label}")

        if not assigned:
            msg = (
                f"{code} (Year {year}, {total_students} students) "
                f"— no designated venue free"
                f"{' [STRICT: will NOT fall back to general venues]' if is_strict else ' [soft: will attempt general phases]'}"
            )
            unscheduled_list.append(msg)
            safe_print(f"[SpecPass] UNPLACED: {msg}")
            if is_strict:
                unscheduled_strict.append(task)

    safe_print(
        f"[SpecPass] Done: {scheduled_count} allocs placed, "
        f"{len(unscheduled_strict)} strict-rule failures, "
        f"{len(unscheduled_list) - len(unscheduled_strict)} soft-rule pass-through"
    )
    return scheduled_count, unscheduled_strict, scheduled_list, unscheduled_list


# ═══════════════════════════════════════════════════════════════════════════════
# SMART CONFLICT EXEMPTION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_alloc_intake(alloc) -> str:
    return getattr(alloc, 'intake', 'normal') or 'normal'


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


def is_program_year_collision_exempt(alloc_a, alloc_b) -> bool:
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

    if _is_elective(alloc_a) or _is_elective(alloc_b):
        return True
    sg_a = _get_selection_group_id(alloc_a)
    sg_b = _get_selection_group_id(alloc_b)
    if sg_a is not None or sg_b is not None:
        return True

    if _get_alloc_intake(alloc_a) != _get_alloc_intake(alloc_b):
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# FIXED: build_global_merged_tasks — Simple merge only (no sub-group merging)
# ═══════════════════════════════════════════════════════════════════════════════

def _pack_into_merge_bins(allocs: List, merge_limit: int) -> List[List]:
    """Pack same-course/same-lecturer allocations into as few shared-venue
    bins as possible, each bin's combined student total <= merge_limit.

    This is the "check the courses to combine and possible combinations"
    pass: the old behaviour, when a course's sections didn't ALL fit
    together under merge_limit, was to give up entirely and schedule every
    single section on its own — which is exactly what was fragmenting
    things like ECON 313 (5 sections, ~178 students total) into up to 5
    separate venues/slots, several of which then had nowhere to go and
    landed Unscheduled. Instead, before falling back to individual
    scheduling, actively search for combinable subsets: sort sections
    largest-first and best-fit each one into whichever open bin has the
    least room left that can still hold it (best-fit-decreasing), opening
    a new bin only when nothing existing fits. This minimises the number
    of resulting venues/slots for a given merge_limit, e.g. 5 ECON 313
    sections of [35, 15, 70, 53, 5] (178 total) under a 200 cap pack into
    1 bin ([70, 53, 35, 15, 5] = 178) instead of fragmenting across
    multiple venues/slots.

    Returns a list of bins; a bin of length 1 is just that one allocation
    on its own (no combinable partner was found for it).
    """
    if not allocs:
        return []

    sized = sorted(allocs, key=lambda a: (a.number_of_students or 0), reverse=True)
    bins: List[List] = []
    bin_totals: List[int] = []

    for a in sized:
        students = a.number_of_students or 0
        best_idx = -1
        best_remaining = None
        for i, total in enumerate(bin_totals):
            remaining = merge_limit - (total + students)
            if remaining >= 0 and (best_remaining is None or remaining < best_remaining):
                best_idx = i
                best_remaining = remaining
        if best_idx >= 0:
            bins[best_idx].append(a)
            bin_totals[best_idx] += students
        else:
            bins.append([a])
            bin_totals.append(students)

    return bins


def build_global_merged_tasks(allocations, merge_limit: int = 200):
    """Group allocations into tasks, merging only when total fits in merge_limit.

    CombinedCourseGroup awareness
    ──────────────────────────────
    Allocations that belong to a CombinedCourseGroup (created by the COD panel)
    are always scheduled together as one task, regardless of whether their
    normalised codes happen to match any other group.  This prevents the
    algorithm from accidentally splitting a combined group across different time
    slots (a silent collision) or, conversely, double-merging them with
    unrelated sections.

    The function proceeds in two passes:
      1. Pull all CombinedCourseGroups and pre-assign their allocation IDs to a
         dedicated task.  These IDs are removed from the general pool so the
         second pass never touches them again.
      2. Run the original normalised-code grouping on the remaining allocations.
    """
    alloc_by_id = {a.id: a for a in allocations}
    tasks = []
    combined_alloc_ids: Set[int] = set()  # IDs already claimed by a combined group

    # ── Pass 1: honour CombinedCourseGroups ───────────────────────────────────
    try:
        combined_groups = list(
            CombinedCourseGroup.objects.prefetch_related('allocations').all()
        )
    except Exception as exc:
        safe_print(f"[CombinedCourseGroup] WARNING – could not load groups: {exc}")
        combined_groups = []

    for cg in combined_groups:
        group_allocs = [
            alloc_by_id[a.id]
            for a in cg.allocations.all()
            if a.id in alloc_by_id
        ]
        if not group_allocs:
            continue  # no allocations from this scheduling run are in the group

        total_students = sum(a.number_of_students or 0 for a in group_allocs)
        norm_code = normalize_course_code(cg.base_course_code)

        safe_print(
            f"[CombinedCourseGroup] '{cg.group_code}' ({norm_code}) "
            f"x{len(group_allocs)} allocations, {total_students} students total → ONE slot"
        )

        if len(group_allocs) == 1:
            # Only one allocation from this group is in the run; treat normally
            tasks.append(group_allocs[0])
        else:
            tasks.append({
                'merged': group_allocs,
                'total_students': total_students,
                'group_id': cg.id,
                'norm_code': norm_code,
                'combined_group': cg,       # carried through for TempTimetable notes
            })

        for a in group_allocs:
            combined_alloc_ids.add(a.id)

    # ── Pass 2: standard normalised-code grouping for the remaining allocations ─
    remaining = [a for a in allocations if a.id not in combined_alloc_ids]

    grouped: Dict[Tuple, List] = defaultdict(list)
    for alloc in remaining:
        lect_id = alloc.lecturer.id if alloc.lecturer else None
        # Always group by the course's BASE code (any section/stream tag —
        # '-A'/'_C' style OR word-style 'GROUP C'/'GRP B'/bare 'b' — stripped),
        # not the full normalized code. This used to only happen when no
        # lecturer was assigned yet; but a section tag doesn't stop being
        # cosmetic just because a lecturer IS known. The (norm_code, lect_id)
        # key below still keeps sections taught by different lecturers apart,
        # so once the lecturer matches too, a bare section label was the only
        # thing left keeping e.g. 'ECON 313', 'ECON 313 GROUP B' and
        # 'ECON 313 b' — same course, same lecturer — from combining into one
        # shared slot, even when their combined enrolment was well under
        # merge_limit. See normalize_course_code_base's docstring for more.
        norm_code = normalize_course_code_base(alloc.course_code)
        grouped[(norm_code, lect_id)].append(alloc)

    for (norm_code, lect_id), allocs in grouped.items():
        if len(allocs) == 1:
            tasks.append(allocs[0])
            continue

        total_students = sum(a.number_of_students or 0 for a in allocs)

        names = {_normalized_course_name(a.course_name) for a in allocs}
        names.discard("")  # a missing/blank name on one row never blocks a merge
        if len(names) > 1:
            safe_print(
                f"MERGE SKIPPED (different names): '{norm_code}' → individual "
                f"({sorted(names)})"
            )
            tasks.extend(allocs)
            continue

        unassigned_note = " [unassigned lecturer, base-code merge]" if lect_id is None else ""

        if total_students <= merge_limit:
            safe_print(
                f"MERGE: '{norm_code}' x{len(allocs)} sections, "
                f"combined={total_students}/{merge_limit} students → ONE slot"
                f"{unassigned_note}"
            )
            tasks.append({
                'merged': allocs,
                'total_students': total_students,
                'group_id': None,
                'norm_code': norm_code,
            })
        else:
            # Combined total doesn't fit one venue — before giving up and
            # scheduling every section individually, check for combinable
            # subsets and pack into as few merged bins as possible.
            bins = _pack_into_merge_bins(allocs, merge_limit)
            bin_totals = [sum(a.number_of_students or 0 for a in b) for b in bins]
            safe_print(
                f"MERGE PACKED (total {total_students} > {merge_limit}): "
                f"'{norm_code}' x{len(allocs)} sections → {len(bins)} bin(s) "
                f"{bin_totals}{unassigned_note}"
            )
            for b in bins:
                if len(b) == 1:
                    tasks.append(b[0])
                else:
                    bin_total = sum(a.number_of_students or 0 for a in b)
                    tasks.append({
                        'merged': b,
                        'total_students': bin_total,
                        'group_id': None,
                        'norm_code': norm_code,
                    })

    return tasks


@retry_on_lock(max_retries=3, delay=0.2)
def _add_merged_course(mg, alloc):
    mg.merged_courses.add(alloc)


def create_merged_group_db(task: dict) -> Optional[int]:
    allocs = task['merged']
    norm_code = task.get('norm_code', normalize_course_code(allocs[0].course_code))
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
            _add_merged_course(mg, alloc)
        return mg.id
    except Exception as e:
        safe_print(f"Error creating merged group for {norm_code}: {e}")
        return None


class SchedulerCache:
    def __init__(self):
        self.venue_cache = None
        self.faculty_venues_map = None
        self.program_courses_cache = None
        self.course_year_cache = {}
        self.existing_timetable_entries = set()
        self.scheduled_course_keys: Set[tuple] = set()

    def clear(self):
        self.venue_cache = None
        self.faculty_venues_map = None
        self.program_courses_cache = None
        self.course_year_cache.clear()
        self.existing_timetable_entries.clear()
        self.scheduled_course_keys.clear()

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

    def load_existing_timetable_entries(self):
        entries = TempTimetable.objects.values_list('venue_id', 'day', 'start_time', 'end_time')
        self.existing_timetable_entries = set(entries)

    def is_course_already_scheduled(
        self, course_code: str, lecturer_id, program_id, student_group_id=None
    ) -> bool:
        key = make_course_schedule_key(course_code, lecturer_id, program_id, student_group_id)
        return key in self.scheduled_course_keys

    def mark_course_scheduled(
        self, course_code: str, lecturer_id, program_id, student_group_id=None
    ) -> None:
        key = make_course_schedule_key(course_code, lecturer_id, program_id, student_group_id)
        self.scheduled_course_keys.add(key)

    def sync_scheduled_keys_from_alloc_ids(
        self, allocs: List, globally_scheduled_alloc_ids: Set[int]
    ) -> None:
        for alloc in allocs:
            if alloc.id in globally_scheduled_alloc_ids:
                lid = alloc.lecturer.id if alloc.lecturer else None
                pid = alloc.program.id if alloc.program else None
                sgid = getattr(alloc, 'student_group_id', None)
                self.mark_course_scheduled(alloc.course_code, lid, pid, sgid)


class ConflictTracker:
    def __init__(self, days, slots, lecturer_blocked: Optional[Dict] = None):
        self.days = days
        self.slots = slots
        self.lecturer_schedule = defaultdict(lambda: defaultdict(set))
        self.program_year_schedule = defaultdict(lambda: defaultdict(set))
        self.venue_schedule = defaultdict(lambda: defaultdict(set))
        self.program_year_daily_count = defaultdict(lambda: defaultdict(int))
        self.program_year_slot_allocs: Dict[Tuple, List] = defaultdict(list)
        self.collisions_detected = 0
        self.collisions_resolved = 0
        # ── Hard lecturer-blocked day/slot constraints ──────────────────────
        # {lecturer_id: {day: 'ALL_DAY' | {slot_index, ...}}}
        # Built once (see build_lecturer_blocked_slot_map) and consulted by
        # has_lecturer_conflict below — the single choke point every
        # scheduling phase already calls before placing a lecturer, so a
        # blocked day/slot is respected everywhere automatically with no
        # per-phase changes needed.
        self.lecturer_blocked: Dict = lecturer_blocked or {}

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

    def has_lecturer_hard_block(self, lecturer_id, day, slot_index) -> bool:
        """Hard LecturerBlockedSlot check ONLY — deliberately excludes the
        double-booking half of has_lecturer_conflict. Passes that relax a
        lecturer's own double-booking as a last resort (OverloadRelief Pass
        B, FinalSafetyNet Pass B/C) must still never place them in a slot
        they've been hard-blocked from; calling has_lecturer_conflict there
        would relax BOTH checks at once since they're combined, silently
        overriding the block along with the double-booking. Use this
        instead in any pass that intentionally skips has_lecturer_conflict.
        """
        if not lecturer_id:
            return False
        day_block = self.lecturer_blocked.get(lecturer_id, {}).get(day)
        if day_block == 'ALL_DAY':
            return True
        if day_block and slot_index in day_block:
            return True
        return False

    def has_program_conflict(self, program_id, year, day, slot_index,
                              new_alloc=None) -> bool:
        if not program_id:
            return False
        if slot_index not in self.program_year_schedule[(program_id, year)][day]:
            return False
        if new_alloc is None:
            return True
        existing = self.program_year_slot_allocs.get((program_id, year, day, slot_index), [])
        for existing_alloc in existing:
            if not is_program_year_collision_exempt(new_alloc, existing_alloc):
                return True
        return False

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
        seen_prog_year_slot: Set[Tuple] = set()
        for alloc in allocs:
            lid = alloc.lecturer.id if alloc.lecturer else None
            pid = alloc.program.id if alloc.program else None
            yr = cache.get_course_year(alloc)

            if lid:
                self.lecturer_schedule[lid][day].add(slot_index)
            if venue_id:
                self.venue_schedule[venue_id][day].add(slot_index)

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
        attempts = 0

        # NOTE: this used to also require can_schedule_program_year(...)
        # (a hard 2-courses/day cap per program-year cohort). That cap is
        # removed — a cohort can be scheduled more than twice a day when
        # needed. day_scores below still orders candidates by current
        # load, so the resolver still *prefers* lighter days first; it
        # just no longer refuses a day solely for being at 2 already.
        for offset in [1, -1, 2, -2, 3, -3]:
            new_slot = slot_index + offset
            if 0 <= new_slot < len(self.slots):
                if (not self.has_program_conflict(program_id, year, day, new_slot, new_alloc=new_alloc) and
                        (not lecturer_id or not self.has_lecturer_conflict(lecturer_id, day, new_slot))):
                    self.collisions_resolved += 1
                    return True, day, new_slot
            attempts += 1

        current_day_index = self.days.index(day) if day in self.days else 0
        for offset_range in range(1, len(self.days)):
            for direction in [1, -1]:
                new_index = (current_day_index + (offset_range * direction)) % len(self.days)
                new_day = self.days[new_index]
                if (not self.has_program_conflict(program_id, year, new_day, slot_index, new_alloc=new_alloc) and
                        (not lecturer_id or not self.has_lecturer_conflict(lecturer_id, new_day, slot_index))):
                    self.collisions_resolved += 1
                    return True, new_day, slot_index

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
                        (not lecturer_id or not self.has_lecturer_conflict(lecturer_id, test_day, test_slot))):
                    self.collisions_resolved += 1
                    return True, test_day, test_slot

        return False, day, slot_index

    def get_collision_stats(self):
        return {
            'detected': self.collisions_detected,
            'resolved': self.collisions_resolved,
            'unresolved': self.collisions_detected - self.collisions_resolved,
            'resolution_rate': (self.collisions_resolved / self.collisions_detected * 100)
                               if self.collisions_detected > 0 else 100.0
        }


def get_course_faculty(course_allocation) -> Optional[Faculty]:
    try:
        if course_allocation.program and course_allocation.program.department:
            return course_allocation.program.department.faculty
        if course_allocation.lecturer and course_allocation.lecturer.department:
            return course_allocation.lecturer.department.faculty
        if course_allocation.department:
            return course_allocation.department.faculty
    except Exception:
        pass
    return None


def get_course_department_id(course_allocation) -> Optional[int]:
    """Resolve a course allocation's department, same fallback order as
    get_course_faculty (allocation's own department field first, since
    that's the most direct/explicit source; then program's department;
    then lecturer's department). Used to group and prioritize scheduling
    by department size — see DEPARTMENT-SIZE PRIORITY note where this is
    consumed.
    """
    try:
        if course_allocation.department:
            return course_allocation.department.id
        if course_allocation.program and course_allocation.program.department:
            return course_allocation.program.department.id
        if course_allocation.lecturer and course_allocation.lecturer.department:
            return course_allocation.lecturer.department.id
    except Exception:
        pass
    return None


def get_faculty_venues_for_course(course_allocation, faculty_venues_map: Dict) -> List[Venue]:
    faculty = get_course_faculty(course_allocation)
    if faculty and faculty in faculty_venues_map:
        return faculty_venues_map[faculty]
    return []


def generate_slots(start_time: dtime, end_time: dtime, slot_size_hours: int) -> List[Tuple[dtime, dtime]]:
    slots = []
    today = date.today()
    current = datetime.combine(today, start_time)
    end_dt = datetime.combine(today, end_time)
    while current + timedelta(hours=slot_size_hours) <= end_dt:
        nxt = current + timedelta(hours=slot_size_hours)
        slots.append((current.time(), nxt.time()))
        current = nxt
    return slots


def slot_key(day: str, start: dtime, end: dtime) -> str:
    return f"{day}_{start.isoformat()}_{end.isoformat()}"


def build_lecturer_blocked_slot_map(
    days: List[str],
    slots: List[Tuple[dtime, dtime]],
    disabled_constraints: Optional[Set[str]] = None,
) -> Dict[int, Dict[str, Any]]:
    """
    Converts core.scheduling_constraints.get_lecturer_blocked_ranges() (raw
    day/time ranges) into {lecturer_id: {day: 'ALL_DAY' | {slot_index,...}}},
    matched against this run's own generated `slots` list, for
    ConflictTracker.has_lecturer_conflict to consult directly.
    """
    raw = constraint_engine.get_lecturer_blocked_ranges(disabled_constraints)
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


# ═══════════════════════════════════════════════════════════════════════════════
# EVENING & WEEKEND OVERFLOW PASS
# ═══════════════════════════════════════════════════════════════════════════════

def process_evening_weekend_overflow(
    unscheduled_tasks: List,
    all_venues: List,
    config,
    conflict_tracker,
    cache,
    globally_scheduled_alloc_ids: set,
) -> tuple:
    """
    Schedule courses flagged is_evening_weekend=True.

    These courses are ONLY placed in:
      1. Evening slots on weekdays if enable_evening_classes is True
         (default: 19:00–21:00, 1 slot/day).
      2. Weekend slots on Sat/Sun if enable_weekend_classes is True
         (default: 09:00–17:00, 3-hour slots).

    Regular (non-flagged) courses are NEVER touched here.
    Returns (placed_count, still_unscheduled, scheduled_list, unscheduled_list).
    """
    enable_evening = getattr(config, 'enable_evening_classes', False)
    enable_weekend = getattr(config, 'enable_weekend_classes', False)

    if not enable_evening and not enable_weekend:
        safe_print("[EveningWeekend] Both passes disabled — skipping.")
        return 0, unscheduled_tasks, [], []

    # ── Build slot pools ─────────────────────────────────────────────────────
    overflow_windows: List[tuple] = []   # (day, start, end)

    regular_days = getattr(config, 'days', None) or [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
    ]

    if enable_evening:
        ev_start  = getattr(config, 'evening_start_time',  dtime(19, 0))
        ev_end    = getattr(config, 'evening_end_time',    dtime(21, 0))
        ev_count  = int(getattr(config, 'evening_slot_count', 1))
        slot_size = int(getattr(config, 'slot_size', 3))
        ev_slots  = generate_slots(ev_start, ev_end, slot_size)[:ev_count]
        safe_print(
            f"[EveningWeekend] Evening: {len(ev_slots)} slot(s) per weekday "
            f"({ev_start}–{ev_end}, up to {ev_count})"
        )
        for day in regular_days:
            for s, e in ev_slots:
                overflow_windows.append((day, s, e))

    if enable_weekend:
        wk_start     = getattr(config, 'weekend_start_time', dtime(9,  0))
        wk_end       = getattr(config, 'weekend_end_time',   dtime(17, 0))
        wk_slot_size = int(getattr(config, 'weekend_slot_size', 3))
        wk_slots     = generate_slots(wk_start, wk_end, wk_slot_size)
        weekend_days = ["Saturday"]
        safe_print(
            f"[EveningWeekend] Weekend: {len(wk_slots)} slot(s) per day "
            f"({wk_start}–{wk_end}, {wk_slot_size}-hr slots)"
        )
        for day in weekend_days:
            for s, e in wk_slots:
                overflow_windows.append((day, s, e))

    if not overflow_windows:
        safe_print("[EveningWeekend] No overflow windows generated — skipping.")
        return 0, unscheduled_tasks, [], []

    safe_print(
        f"[EveningWeekend] Starting overflow pass: "
        f"{len(unscheduled_tasks)} tasks × {len(overflow_windows)} windows"
    )

    placed_count   = 0
    still_unsched  = []
    scheduled_list : List[str] = []
    unsched_list   : List[str] = []

    venues_asc = sorted(all_venues, key=lambda v: v.capacity or 0)

    for task in unscheduled_tasks:
        skip, task = _should_skip_task(task, globally_scheduled_alloc_ids, cache, "EveWknd")
        if skip:
            continue

        is_merged      = isinstance(task, dict)
        allocs         = _task_allocs(task)
        total_students = _task_students(task)
        rep            = _representative(task)
        lecturer_id    = rep.lecturer.id if rep.lecturer else None
        program_id     = rep.program.id  if rep.program  else None
        code           = merged_course_label(task)
        try:
            year = cache.get_course_year(rep)
        except Exception:
            year = 1

        assigned = False
        placement_note = ""

        # Three escalating passes — mirrors the Phase 5 exhaustive sweep.
        # HARD RULE — NEVER RELAXED, in any pass: lecturer double-booking and
        # program-year collision. Only VENUE CAPACITY escalates:
        #   Pass 1 — exact fit (venue capacity >= demand)
        #   Pass 2 — up to 10% capacity overflow
        #   Pass 3 — capacity is NOT a placement gate at all: any genuinely
        #            free (collision-free) venue is used, largest first, so
        #            an evening/weekend course is never left unscheduled
        #            purely because every room is a little too small — that
        #            is a venue-supply problem to flag for review, not a
        #            reason to leave students without a class.
        for ew_pass in range(1, 4):
            if assigned:
                break

            if ew_pass == 1:
                placement_note = ""
                venue_pool = venues_asc
            elif ew_pass == 2:
                placement_note = " [INFO: capacity overflow ≤10% — no exact-fit venue was free]"
                venue_pool = venues_asc
            else:
                placement_note = (
                    " [INFO: CAPACITY LAST RESORT — placed in largest free venue "
                    "regardless of size; review venue capacity/supply]"
                )
                venue_pool = sorted(all_venues, key=lambda v: v.capacity or 0, reverse=True)

            for w_idx, (day, start, end) in enumerate(overflow_windows):
                if assigned:
                    break

                # Map (start, end) to a virtual slot index based on position in window list
                # We use a simple index so conflict-tracker works across regular + overflow
                ev_slot_idx = 1000 + w_idx

                if lecturer_id and conflict_tracker.has_lecturer_conflict(lecturer_id, day, ev_slot_idx):
                    continue
                if program_id and conflict_tracker.has_program_conflict(
                        program_id, year, day, ev_slot_idx, new_alloc=rep):
                    continue

                if is_merged:
                    any_conflict = False
                    for alloc in allocs:
                        a_lid = alloc.lecturer.id if alloc.lecturer else None
                        a_pid = alloc.program.id  if alloc.program  else None
                        try:
                            a_yr = cache.get_course_year(alloc)
                        except Exception:
                            any_conflict = True
                            break
                        if a_lid and conflict_tracker.has_lecturer_conflict(a_lid, day, ev_slot_idx):
                            any_conflict = True
                            break
                        if a_pid and conflict_tracker.has_program_conflict(
                                a_pid, a_yr, day, ev_slot_idx, new_alloc=alloc):
                            any_conflict = True
                            break
                    if any_conflict:
                        continue

                # Find venue
                chosen_venue = None
                for v in venue_pool:
                    if ew_pass == 1 and (v.capacity or 0) < total_students:
                        continue
                    if ew_pass == 2 and (v.capacity or 0) < int(total_students * 0.90):
                        continue
                    # ew_pass == 3: no capacity filter — any free venue qualifies.
                    if conflict_tracker.has_venue_conflict(v.id, day, ev_slot_idx):
                        continue
                    if cache.is_duplicate_entry(v.id, day, start, end):
                        continue
                    chosen_venue = v
                    break

                if chosen_venue is None:
                    continue

                if chosen_venue.capacity and chosen_venue.capacity < total_students:
                    overflow_pct = (total_students - chosen_venue.capacity) / total_students * 100
                    safe_print(
                        f"[EveningWeekend] CAPACITY OVERFLOW PLACEMENT: {code} "
                        f"({total_students} students → {chosen_venue.code} "
                        f"cap={chosen_venue.capacity}, {overflow_pct:.1f}% over capacity)"
                    )

                entries = [
                    TempTimetable(
                        course_allocation=alloc,
                        venue=chosen_venue,
                        day=day,
                        start_time=start,
                        end_time=end,
                    )
                    for alloc in allocs
                ]
                rows = safe_bulk_create_timetable_entries(entries, cache)
                if rows == 0:
                    continue

                conflict_tracker.add_merged_schedule(allocs, chosen_venue.id, day, ev_slot_idx, cache)
                _register_placed_task(task, allocs, globally_scheduled_alloc_ids, cache)

                kind = "EVENING" if day not in ["Saturday", "Sunday"] else "WEEKEND"
                pass_label = f"pass={ew_pass}" if ew_pass > 1 else "strict"
                label = (
                    f"{code} (Year {year}, {total_students} students) "
                    f"→ {chosen_venue.code} (cap {chosen_venue.capacity}) [{kind}/{pass_label}] "
                    f"({day} {start}–{end}){placement_note}"
                )
                placed_count += len(allocs)
                assigned = True
                scheduled_list.append(label)
                safe_print(f"[EveningWeekend] PLACED: {label}")

        if not assigned:
            still_unsched.append(task)
            unsched_list.append(
                f"{code} (Year {year}, {total_students} students) "
                f"— no evening/weekend slot available (no lecturer/program-year-"
                f"conflict-free window existed, even ignoring venue capacity)"
            )
            safe_print(f"[EveningWeekend] UNPLACED: {code}")

    safe_print(
        f"[EveningWeekend] Done: placed={placed_count}, "
        f"still_unscheduled={len(still_unsched)}"
    )
    return placed_count, still_unsched, scheduled_list, unsched_list


class RegularFeasibilityMetrics:
    __slots__ = (
        "total_demand", "total_supply", "seat_deficit",
        "conflict_density_ratio", "room_utilization_rate",
        "seat_waste_percentage", "merge_efficiency_score",
        "unscheduled_ug", "unscheduled_pg", "notes",
    )

    def __init__(self):
        self.total_demand: int = 0
        self.total_supply: int = 0
        self.seat_deficit: int = 0
        self.conflict_density_ratio: float = 0.0
        self.room_utilization_rate: float = 0.0
        self.seat_waste_percentage: float = 0.0
        self.merge_efficiency_score: float = 0.0
        self.unscheduled_ug: int = 0
        self.unscheduled_pg: int = 0
        self.notes: List[str] = []

    def log(self, msg: str):
        self.notes.append(msg)
        safe_print(f"[Metrics] {msg}")

    def to_dict(self) -> dict:
        return {
            "total_demand": self.total_demand,
            "total_supply": self.total_supply,
            "seat_deficit": self.seat_deficit,
            "conflict_density_ratio": round(self.conflict_density_ratio, 4),
            "room_utilization_rate": round(self.room_utilization_rate, 4),
            "seat_waste_pct": round(self.seat_waste_percentage, 2),
            "merge_efficiency_score": round(self.merge_efficiency_score, 4),
            "unscheduled_ug": self.unscheduled_ug,
            "unscheduled_pg": self.unscheduled_pg,
        }


def compute_regular_feasibility_metrics(
    all_tasks: List,
    all_venues: List,
    days: List[str],
    slots: List[Tuple[dtime, dtime]],
    conflict_tracker,
    scheduled_count: int,
    unscheduled_ug: int,
    unscheduled_pg: int,
    merge_attempts: int = 0,
    merge_successes: int = 0,
) -> "RegularFeasibilityMetrics":
    m = RegularFeasibilityMetrics()
    m.unscheduled_ug = unscheduled_ug
    m.unscheduled_pg = unscheduled_pg

    m.total_demand = sum(_task_students(t) for t in all_tasks)

    total_venue_cap = sum((v.capacity or 0) for v in all_venues)
    m.total_supply = total_venue_cap * len(days) * len(slots)
    m.seat_deficit = max(0, m.total_demand - m.total_supply)

    total_py_slots = 0
    actual_conflicts = 0
    for (pid, yr), day_map in conflict_tracker.program_year_schedule.items():
        for day, slot_set in day_map.items():
            n = len(slot_set)
            total_py_slots += n
            if n > 1:
                actual_conflicts += n * (n - 1) // 2
    max_conflicts = total_py_slots * (total_py_slots - 1) // 2 if total_py_slots > 1 else 1
    m.conflict_density_ratio = actual_conflicts / max(max_conflicts, 1)

    # ── Room utilization / seat waste ────────────────────────────────────
    # Two bugs fixed here vs. the old version:
    #   1) The old room_utilization_rate divided a plain COUNT of booked
    #      (venue, slot) pairs by a CAPACITY-WEIGHTED total supply
    #      (total_venue_cap * days * slots) — comparing a count to a
    #      seat-count produced a near-zero ratio (0.9%) regardless of how
    #      full the rooms actually were. Fixed to count/count: booked
    #      room-timeslots ÷ total room-timeslots available.
    #   2) It read from conflict_tracker.venue_schedule, which every
    #      post-schedule pass (Phase 6/6B/6B2/6C/6.5) explicitly stops
    #      updating and instead writes straight to TempTimetable — by the
    #      time this runs (after Phase 7), that in-memory index is stale.
    #      Reading fresh from TempTimetable here matches how every other
    #      late-stage pass in this file already treats the DB as the
    #      source of truth.
    all_physical_venues = list(Venue.objects.all())
    total_room_timeslots = len(all_physical_venues) * len(days) * len(slots)

    booked_combo_ids: Set[Tuple[int, str, dtime, dtime]] = set()
    total_booked_seat_capacity = 0
    total_students_seated = 0
    for e in TempTimetable.objects.select_related('venue', 'course_allocation').all():
        if not e.venue_id:
            continue
        if e.course_allocation:
            total_students_seated += e.course_allocation.number_of_students or 0
        combo = (e.venue_id, e.day, e.start_time, e.end_time)
        if combo not in booked_combo_ids:
            booked_combo_ids.add(combo)
            total_booked_seat_capacity += e.venue.capacity or 0

    used_venue_slots = len(booked_combo_ids)
    m.room_utilization_rate = used_venue_slots / max(total_room_timeslots, 1)

    if total_booked_seat_capacity > 0:
        m.seat_waste_percentage = max(
            0.0,
            (1.0 - (total_students_seated / total_booked_seat_capacity)) * 100.0
        )
    else:
        m.seat_waste_percentage = 0.0

    m.merge_efficiency_score = (
        merge_successes / merge_attempts if merge_attempts > 0 else 0.0
    )

    m.log(
        f"demand={m.total_demand} supply={m.total_supply} "
        f"deficit={m.seat_deficit} "
        f"conflict_density={m.conflict_density_ratio:.3f} "
        f"room_util={m.room_utilization_rate:.3f} "
        f"seat_waste={m.seat_waste_percentage:.1f}% "
        f"merge_eff={m.merge_efficiency_score:.3f} "
        f"unsched_ug={unscheduled_ug} unsched_pg={unscheduled_pg}"
    )
    return m


class VenueAllocator:
    def __init__(self, venues, days, slots, conflict_tracker):
        self.venues = venues
        self.days = days
        self.slots = slots
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


def _rescue_colliding_entry(entry, cache) -> bool:
    """
    Called only when an entry has genuinely lost the (venue, day, start, end)
    slot to another entry (unique_together collision) and a plain .save()
    has already failed once. Rather than dropping the course, look for any
    OTHER venue that is free at this exact day/slot — first among venues
    big enough for the course, then (as a last resort) any free venue at
    all — and re-home the entry there instead of losing it.

    Returns True if the entry was saved somewhere, False if truly no venue
    at this day/slot was free (which can only happen if every single venue
    already has something else scheduled in this exact window).
    """
    needed = getattr(entry.course_allocation, 'number_of_students', 0) or 0

    candidate_qs = Venue.objects.filter(capacity__gte=needed).order_by('capacity')
    fallback_qs = Venue.objects.order_by('-capacity')

    for qs in (candidate_qs, fallback_qs):
        for v in qs:
            key = (v.id, entry.day, entry.start_time, entry.end_time)
            if key in cache.existing_timetable_entries:
                continue
            try:
                with transaction.atomic():
                    entry.venue = v
                    entry.venue_id = v.id
                    entry.save()
                    cache.existing_timetable_entries.add(key)
                return True
            except IntegrityError:
                # Someone else's rescue attempt beat us to this venue in
                # this same pass — try the next candidate.
                continue
    return False


def safe_bulk_create_timetable_entries(entries, cache, batch_size=50):
    if not entries:
        return 0
    created_count = 0
    lost_entries = []
    for i in range(0, len(entries), batch_size):
        batch = entries[i:i + batch_size]
        retry_count = 0
        max_retries = 3
        while retry_count < max_retries:
            try:
                with transaction.atomic():
                    TempTimetable.objects.bulk_create(batch)
                    for entry in batch:
                        cache.existing_timetable_entries.add(
                            (entry.venue_id, entry.day, entry.start_time, entry.end_time)
                        )
                    created_count += len(batch)
                    break
            except IntegrityError as e:
                safe_print(
                    f"[DataLoss-Guard] IntegrityError in batch of {len(batch)} — "
                    f"falling back to per-entry save with rescue ({e})"
                )
                for entry in batch:
                    try:
                        with transaction.atomic():
                            entry.save()
                            cache.existing_timetable_entries.add(
                                (entry.venue_id, entry.day, entry.start_time, entry.end_time)
                            )
                            created_count += 1
                    except IntegrityError:
                        # unique_together now includes course_allocation, so
                        # this can ONLY mean the exact same course was
                        # already written to this exact (venue, day, start,
                        # end) — e.g. a retry after a partial batch failure.
                        # It does NOT mean a combined-group collision (two
                        # different courses sharing a venue/slot is now a
                        # legitimate, allowed state). Treat a true duplicate
                        # as already-scheduled rather than rescuing it into
                        # a different venue, which would incorrectly give
                        # the same course two venues at once.
                        course_code = getattr(entry.course_allocation, 'course_code', 'UNKNOWN')
                        already_present = TempTimetable.objects.filter(
                            course_allocation_id=entry.course_allocation_id,
                            venue_id=entry.venue_id,
                            day=entry.day,
                            start_time=entry.start_time,
                            end_time=entry.end_time,
                        ).exists()
                        if already_present:
                            cache.existing_timetable_entries.add(
                                (entry.venue_id, entry.day, entry.start_time, entry.end_time)
                            )
                            created_count += 1
                            continue
                        if _rescue_colliding_entry(entry, cache):
                            created_count += 1
                            safe_print(
                                f"[DataLoss-Guard] RESCUED: {course_code} moved to venue "
                                f"{entry.venue_id} ({entry.day} {entry.start_time}-{entry.end_time})"
                            )
                        else:
                            safe_print(
                                f"[DataLoss-Guard] UNSCHEDULED: {course_code} could not be placed "
                                f"anywhere at {entry.day} {entry.start_time}-{entry.end_time} — "
                                f"every venue is already occupied in this exact slot"
                            )
                            lost_entries.append(entry)
                break
            except OperationalError as e:
                if 'database is locked' in str(e) and retry_count < max_retries - 1:
                    retry_count += 1
                    wait_time = 0.5 * (2 ** retry_count)
                    safe_print(f"DB locked, retrying in {wait_time:.1f}s")
                    time.sleep(wait_time)
                else:
                    for entry in batch:
                        try:
                            time.sleep(0.1)
                            with transaction.atomic():
                                entry.save()
                                cache.existing_timetable_entries.add(
                                    (entry.venue_id, entry.day, entry.start_time, entry.end_time)
                                )
                                created_count += 1
                        except Exception:
                            pass
                    break
            except Exception as e:
                safe_print(f"Unexpected error in batch create: {e}")
                break
        if i + batch_size < len(entries):
            time.sleep(0.1)
    if lost_entries:
        safe_print(
            f"[DataLoss-Guard] {len(lost_entries)} of {len(entries)} entries could NOT be "
            f"scheduled after rescue attempts — every venue was already booked in their exact "
            f"slot. Codes: "
            + ", ".join(
                getattr(e.course_allocation, 'course_code', 'UNKNOWN') for e in lost_entries[:15]
            )
            + (" ..." if len(lost_entries) > 15 else "")
        )
    return created_count


@retry_on_lock(max_retries=3, delay=0.3)
def safe_update_merged_groups(merged_groups, batch_size=50):
    if not merged_groups:
        return

    base_ids = [mg.base_course_id for mg in merged_groups if mg.base_course_id]
    temp_map: Dict[int, object] = {}
    if base_ids:
        for tt in TempTimetable.objects.filter(course_allocation_id__in=base_ids):
            if tt.course_allocation_id not in temp_map:
                temp_map[tt.course_allocation_id] = tt

    for mg in merged_groups:
        mg.temp_timetable_entry = temp_map.get(mg.base_course_id)
        mg.timetable_entry = None

    for i in range(0, len(merged_groups), batch_size):
        batch = merged_groups[i:i + batch_size]
        try:
            with transaction.atomic():
                MergedCourseGroupTimetable.objects.bulk_update(
                    batch,
                    ['date', 'start_time', 'end_time', 'venue',
                     'temp_timetable_entry', 'timetable_entry'],
                )
        except Exception as e:
            safe_print(f"Error updating merged groups: {e}")
            for mg in batch:
                try:
                    with transaction.atomic():
                        mg.save()
                except Exception:
                    pass
        time.sleep(0.05)


def clear_tables_safely():
    try:
        with transaction.atomic():
            TempTimetable.objects.all().delete()
            MergedCourseGroupTimetable.objects.all().delete()
        safe_print("Tables cleared successfully")
    except OperationalError as e:
        if 'database is locked' in str(e):
            safe_print("DB locked, using incremental deletion...")
            while TempTimetable.objects.exists():
                ids = TempTimetable.objects.values_list('id', flat=True)[:100]
                with transaction.atomic():
                    TempTimetable.objects.filter(id__in=list(ids)).delete()
                time.sleep(0.1)
            while MergedCourseGroupTimetable.objects.exists():
                ids = MergedCourseGroupTimetable.objects.values_list('id', flat=True)[:100]
                with transaction.atomic():
                    MergedCourseGroupTimetable.objects.filter(id__in=list(ids)).delete()
                time.sleep(0.1)
            safe_print("Incremental deletion complete")
        else:
            raise


def _task_allocs(task):
    return task['merged'] if isinstance(task, dict) else [task]


def _task_students(task):
    return task['total_students'] if isinstance(task, dict) else (task.number_of_students or 0)


def _representative(task):
    return task['merged'][0] if isinstance(task, dict) else task


def _task_is_pg(task) -> bool:
    return is_postgraduate_course(_representative(task))


def _task_is_evening_weekend(task) -> bool:
    """Return True when ALL allocations in the task are flagged is_evening_weekend."""
    return all(getattr(a, 'is_evening_weekend', False) for a in _task_allocs(task))


def _interleave_group(tasks, cache, size_desc: bool = False):
    """Fairly interleave ONE pool of tasks across (program, year) cohorts.

    Core round-robin logic factored out of interleave_by_program_year so it
    can be applied twice — once to known-count tasks, once to 0/NULL tasks —
    when known_count_first=True (see interleave_by_program_year).
    """
    groups: Dict[Tuple, List] = defaultdict(list)
    for t in tasks:
        rep = _representative(t)
        program_id = rep.program.id if rep.program else 0
        try:
            yr = cache.get_course_year(rep)
        except Exception:
            yr = 1
        groups[(program_id, yr)].append(t)

    for g in groups.values():
        if size_desc:
            g.sort(key=lambda t: (-_task_students(t), random.random()))
        else:
            random.shuffle(g)

    group_keys = list(groups.keys())
    random.shuffle(group_keys)

    ordered = []
    idx = 0
    progressed = True
    while progressed:
        progressed = False
        for k in group_keys:
            bucket = groups[k]
            if idx < len(bucket):
                ordered.append(bucket[idx])
                progressed = True
        idx += 1
    return ordered


def interleave_by_program_year(tasks, cache, size_desc: bool = False,
                                known_count_first: bool = False):
    """Fairly interleave tasks across (program, year) cohorts.

    This replaces the old department-size ordering. Departments/programs no
    longer get scheduled smallest-first / biggest-last as a block — instead
    every program-year cohort gets an equal, round-robin turn in the
    ordering. Concretely: take one task from cohort A, one from cohort B,
    one from cohort C, … then loop back for a second task from each, and so
    on. This is what actually produces same-day distribution of different
    courses/departments across the university, instead of one big
    department's entire course list being pushed through solid before a
    smaller department even gets a look-in (or vice versa).

    When size_desc=True, each cohort's own tasks are pre-sorted
    largest-class-first before interleaving. Bigger classes have fewer
    venues that can fit them, so offering them up first — before smaller
    classes have had a chance to grab the scarce big-capacity rooms — is
    what keeps bigger courses landing in appropriately bigger venues rather
    than losing the room to a smaller class that simply got processed
    earlier.

    When known_count_first=True, tasks are first split into a known-count
    pool (_task_students(t) > 0) and a 0/NULL pool, each interleaved fairly
    on its own, then concatenated known-first. This gives real, known
    enrollment numbers first pick of the scarce early slots/venues, while
    0/NULL allocations — genuinely uncertain data, not necessarily large —
    fill in afterward on an equal, fairly-interleaved footing among
    themselves. This is still ONE pass, not a separate deferred phase: the
    0/NULL tasks are simply ordered later within the very same batch/step,
    so they still get every fallback and retry that pass offers — they just
    no longer compete on equal terms with known counts for first pick.
    """
    if known_count_first:
        known = [t for t in tasks if _task_students(t) > 0]
        unknown = [t for t in tasks if _task_students(t) <= 0]
        return (
            _interleave_group(known, cache, size_desc)
            + _interleave_group(unknown, cache, size_desc)
        )
    return _interleave_group(tasks, cache, size_desc)


def merged_course_label(task) -> str:
    allocs = _task_allocs(task)
    if len(allocs) <= 1:
        return allocs[0].course_code if allocs else ""
    return "/".join(a.course_code for a in allocs)


def compute_capacity_strategy(
    task,
    all_venues: List,
    report: SchedulingAnalysisReport,
    cache,
    overflow_tolerance: float = 0.10,
) -> dict:
    total_students = _task_students(task)
    rep = _representative(task)
    try:
        yr = cache.get_course_year(rep)
    except Exception:
        yr = 1
    pid = rep.program.id if rep.program else None

    fitting = [v for v in all_venues if (v.capacity or 0) >= total_students]

    if fitting:
        fitting_sorted = sorted(fitting, key=lambda v: v.capacity or 0)
        return {
            'strategy': 'exact',
            'overflow_pct': 0.0,
            'venue_candidates': fitting_sorted,
            'note': f"Exact fit: {len(fitting)} venues ≥ {total_students} students",
        }

    min_needed = int(total_students * (1.0 - overflow_tolerance))
    overflow_venues = [
        v for v in all_venues
        if (v.capacity or 0) >= min_needed
    ]

    if overflow_venues:
        overflow_sorted = sorted(overflow_venues, key=lambda v: v.capacity or 0, reverse=True)
        pct = (total_students - overflow_sorted[0].capacity) / total_students * 100
        return {
            'strategy': 'overflow',
            'overflow_pct': overflow_tolerance,
            'venue_candidates': overflow_sorted,
            'note': (
                f"Overflow strategy: {total_students} students, "
                f"best venue cap={overflow_sorted[0].capacity or 0} "
                f"({pct:.1f}% over capacity — within {overflow_tolerance*100:.0f}% tolerance)"
            ),
        }

    return {
        'strategy': 'split_recommend',
        'overflow_pct': 0.0,
        'venue_candidates': sorted(all_venues, key=lambda v: v.capacity or 0, reverse=True),
        'note': (
            f"SPLIT RECOMMENDED: {total_students} students exceed all venues "
            f"(largest={max((v.capacity or 0) for v in all_venues) if all_venues else 0}) "
            f"even with {overflow_tolerance*100:.0f}% tolerance"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DUPLICATE-PREVENTION HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def _should_skip_task(
    task,
    globally_scheduled_alloc_ids: Set[int],
    cache: SchedulerCache,
    phase_label: str,
) -> Tuple[bool, Optional[Any]]:
    allocs = _task_allocs(task)

    if all(a.id in globally_scheduled_alloc_ids for a in allocs):
        safe_print(f"[{phase_label}] SKIP {merged_course_label(task)}: fully globally scheduled")
        return True, None

    unplaced_allocs = []
    for a in allocs:
        if a.id in globally_scheduled_alloc_ids:
            continue
        a_lid = a.lecturer.id if a.lecturer else None
        a_pid = a.program.id if a.program else None
        a_sgid = getattr(a, 'student_group_id', None)
        if cache.is_course_already_scheduled(a.course_code, a_lid, a_pid, a_sgid):
            safe_print(
                f"[{phase_label}] SKIP alloc {a.course_code} "
                f"(prog={a_pid}, lect={a_lid}, group={a_sgid}): canonical key already placed"
            )
            continue
        unplaced_allocs.append(a)

    if not unplaced_allocs:
        safe_print(
            f"[{phase_label}] SKIP {merged_course_label(task)}: "
            f"all allocs accounted for (by ID or canonical key)"
        )
        return True, None

    if len(unplaced_allocs) == len(allocs):
        return False, task

    if isinstance(task, dict):
        trimmed = dict(task)
        trimmed['merged'] = unplaced_allocs
        trimmed['total_students'] = sum(a.number_of_students or 0 for a in unplaced_allocs)
        safe_print(
            f"[{phase_label}] PARTIAL {merged_course_label(task)}: "
            f"{len(unplaced_allocs)}/{len(allocs)} allocs remain — reconstructing task"
        )
        return False, trimmed
    else:
        return False, task


def _register_placed_task(
    task,
    allocs: List,
    globally_scheduled_alloc_ids: Set[int],
    cache: SchedulerCache,
) -> None:
    rep = allocs[0]
    lid = rep.lecturer.id if rep.lecturer else None
    pid = rep.program.id if rep.program else None
    sgid = getattr(rep, 'student_group_id', None)

    for alloc in allocs:
        globally_scheduled_alloc_ids.add(alloc.id)

    cache.mark_course_scheduled(rep.course_code, lid, pid, sgid)


# ═══════════════════════════════════════════════════════════════════════════════
# process_ug_batch - No internal dissolve (let sweep handle it)
# ═══════════════════════════════════════════════════════════════════════════════

def process_ug_batch(batch_tasks, config, faculty_venues_map, all_venues, days, slots,
                     batch_num, total_batches, conflict_tracker, venue_allocator, cache,
                     program_day_assignments, merged_group_db_ids,
                     globally_scheduled_alloc_ids: set = None):
    MERGE_LIMIT = getattr(config, 'merge_limit', 200)
    scheduled_count = 0
    unscheduled_in_batch = []
    scheduled_courses_list = []
    unscheduled_courses_list = []

    merged_group_updates = []
    batch_entry_keys = set()

    if globally_scheduled_alloc_ids is None:
        globally_scheduled_alloc_ids = set()

    def task_priority(task):
        rep = _representative(task)
        try:
            yr = cache.get_course_year(rep)
        except Exception:
            yr = 1
        program_id = rep.program.id if rep.program else 0
        dist_score = sum(conflict_tracker.get_program_year_day_load(program_id, yr, d) for d in days)
        # No department-size key here any more — batches are already fairly
        # interleaved across program-years before they ever reach this
        # function (see interleave_by_program_year), so no department gets
        # priority over another here.
        #
        # Class size is a PLAIN (not negated) key here, sorted descending
        # via reverse=True below, so the BIGGEST classes are offered a venue
        # first. Bigger classes have fewer rooms that can fit them, so
        # placing them before smaller classes have a chance to occupy those
        # scarce big-capacity rooms is what keeps big courses landing in
        # appropriately big venues instead of losing the room to a smaller
        # class that simply got processed earlier.
        return (yr, dist_score, (rep.number_of_students or 0), random.random())

    sorted_tasks = sorted(batch_tasks, key=task_priority, reverse=True)

    lecturer_day_count = defaultdict(lambda: defaultdict(int))

    for task in sorted_tasks:
        skip, task = _should_skip_task(task, globally_scheduled_alloc_ids, cache, "Batch")
        if skip:
            continue

        is_merged = isinstance(task, dict)
        allocs = _task_allocs(task)
        total_students = _task_students(task)
        rep = _representative(task)
        lecturer = rep.lecturer
        lecturer_id = lecturer.id if lecturer else None
        code = merged_course_label(task)
        program = rep.program
        program_id = program.id if program else None

        try:
            year = cache.get_course_year(rep)
        except Exception as e:
            unscheduled_courses_list.append(f"{code} - Year detection failed: {e}")
            unscheduled_in_batch.append(task)
            continue

        lect_key = lecturer_id if lecturer_id else 'no_lecturer'
        assigned = False

        faculty_venues = get_faculty_venues_for_course(rep, faculty_venues_map)
        if not faculty_venues and is_merged:
            combined_venue_ids: Set[int] = set()
            for _a in allocs:
                for _fv in get_faculty_venues_for_course(_a, faculty_venues_map):
                    combined_venue_ids.add(_fv.id)
            if combined_venue_ids:
                faculty_venues = [v for v in all_venues if v.id in combined_venue_ids]
                safe_print(
                    f"[Batch] Rep faculty had no venues for {code}; "
                    f"using union of all member faculties → {len(faculty_venues)} venue(s)"
                )
        if not faculty_venues:
            unscheduled_courses_list.append(f"{code} ({total_students} students) - No faculty venues")
            unscheduled_in_batch.append(task)
            continue

        preferred_days = days.copy()
        course_id = rep.id
        if course_id in program_day_assignments:
            pref_day = program_day_assignments[course_id]
            if pref_day in preferred_days:
                preferred_days.remove(pref_day)
                preferred_days.insert(0, pref_day)
        else:
            random.shuffle(preferred_days)
            # Soft preference only: nudge toward the cohort's currently
            # least-loaded days so courses spread out across the week
            # instead of stacking. This never excludes a day — a cohort
            # can still take more than 2 courses on the same day when
            # that's what the data requires (e.g. high-unit programs).
            if program_id:
                preferred_days.sort(
                    key=lambda d: conflict_tracker.get_program_year_day_load(program_id, year, d)
                )

        for day in preferred_days:
            if assigned:
                break
            if lecturer_day_count[lect_key].get(day, 0) >= 4:
                continue
            # NOTE: previously hard-capped a program-year cohort to 2
            # courses/day (5 days × 2/day = 10/week). Removed — many
            # students legitimately need more than that. Day load is
            # now only used above as a soft ordering preference, never
            # a hard block.

            for slot_idx in range(len(slots)):
                start, end = slots[slot_idx]
                current_day = day
                current_slot_idx = slot_idx
                resolution_attempted = False

                lecturer_conflict = lecturer_id and conflict_tracker.has_lecturer_conflict(lecturer_id, current_day, current_slot_idx)
                program_conflict = program_id and conflict_tracker.has_program_conflict(program_id, year, current_day, current_slot_idx, new_alloc=rep)

                if program_conflict:
                    resolved, new_day, new_slot_idx = conflict_tracker.resolve_program_conflict(
                        program_id, year, current_day, current_slot_idx, lecturer_id, new_alloc=rep
                    )
                    if resolved:
                        current_day = new_day
                        current_slot_idx = new_slot_idx
                        start, end = slots[current_slot_idx]
                        resolution_attempted = True
                        lecturer_conflict = lecturer_id and conflict_tracker.has_lecturer_conflict(lecturer_id, current_day, current_slot_idx)
                        program_conflict = False

                if lecturer_conflict or program_conflict:
                    continue

                if is_merged:
                    individual_conflict = False
                    for alloc in allocs:
                        a_lid = alloc.lecturer.id if alloc.lecturer else None
                        a_pid = alloc.program.id if alloc.program else None
                        try:
                            a_yr = cache.get_course_year(alloc)
                        except Exception:
                            individual_conflict = True
                            break
                        if a_lid and conflict_tracker.has_lecturer_conflict(a_lid, current_day, current_slot_idx):
                            individual_conflict = True
                            break
                        if a_pid and conflict_tracker.has_program_conflict(a_pid, a_yr, current_day, current_slot_idx, new_alloc=alloc):
                            individual_conflict = True
                            break
                    if individual_conflict:
                        continue

                venue = venue_allocator.find_efficient_venue(total_students, faculty_venues, current_day, current_slot_idx, "best_fit")
                if venue:
                    entry_key = (venue.id, current_day, start, end)
                    if entry_key in batch_entry_keys:
                        continue
                    if cache.is_duplicate_entry(venue.id, current_day, start, end):
                        continue

                    batch_entry_keys.add(entry_key)
                    entries_to_write = [
                        TempTimetable(
                            course_allocation=alloc,
                            venue=venue,
                            day=current_day,
                            start_time=start,
                            end_time=end
                        )
                        for alloc in allocs
                    ]
                    rows_written = safe_bulk_create_timetable_entries(entries_to_write, cache)
                    if rows_written == 0:
                        batch_entry_keys.discard(entry_key)
                        safe_print(
                            f"[Batch] DB write failed for {code} @ "
                            f"{venue.code} {current_day} {start}–{end} — skipping"
                        )
                        continue

                    _register_placed_task(task, allocs, globally_scheduled_alloc_ids, cache)

                    group_id = task.get('group_id') if is_merged else None
                    if group_id and group_id in merged_group_db_ids:
                        mg = merged_group_db_ids[group_id]
                        mg.date = current_day
                        mg.start_time = start
                        mg.end_time = end
                        mg.venue = venue
                        merged_group_updates.append(mg)

                    conflict_tracker.add_merged_schedule(allocs, venue.id, current_day, current_slot_idx, cache)

                    lecturer_day_count[lect_key][current_day] += 1
                    scheduled_count += len(allocs)

                    res_note = f" [RESOLVED: {day}[{slot_idx}]→{current_day}[{current_slot_idx}]]" if resolution_attempted else ""
                    scheduled_courses_list.append(
                        f"{code} (Year {year}) in {venue.code} [FACULTY]{res_note} ({current_day} {start}-{end})"
                    )
                    assigned = True
                    break

        if not assigned:
            unscheduled_courses_list.append(f"{code} (Year {year}, {total_students} students) - Faculty venues full")
            unscheduled_in_batch.append(task)

    if merged_group_updates:
        safe_update_merged_groups(merged_group_updates)

    return scheduled_count, unscheduled_in_batch, scheduled_courses_list, unscheduled_courses_list


def process_ug_fallback(unscheduled_tasks, all_venues, days, slots,
                        conflict_tracker, venue_allocator, cache,
                        globally_scheduled_alloc_ids: set = None):
    if not unscheduled_tasks:
        return 0, [], [], []

    if globally_scheduled_alloc_ids is None:
        globally_scheduled_alloc_ids = set()

    scheduled_count = 0
    still_unscheduled = []
    scheduled_list = []
    unscheduled_list_out = []

    batch_entry_keys = set()

    for task in unscheduled_tasks:
        skip, task = _should_skip_task(task, globally_scheduled_alloc_ids, cache, "Fallback")
        if skip:
            continue

        assigned = False
        is_merged = isinstance(task, dict)
        allocs = _task_allocs(task)
        total_students = _task_students(task)
        rep = _representative(task)
        lecturer = rep.lecturer
        lecturer_id = lecturer.id if lecturer else None
        program = rep.program
        program_id = program.id if program else None
        code = merged_course_label(task)

        try:
            year = cache.get_course_year(rep)
        except Exception:
            unscheduled_list_out.append(f"{code} - Year detection failed")
            still_unscheduled.append(task)
            continue

        preferred_days = [conflict_tracker.get_best_day_for_program_year(program_id, year, d) for d in days]
        seen = set()
        preferred_days = [d for d in preferred_days if not (d in seen or seen.add(d))]

        for day in preferred_days:
            if assigned:
                break
            for slot_idx in range(len(slots)):
                start, end = slots[slot_idx]
                current_day = day
                current_slot_idx = slot_idx

                lecturer_conflict = lecturer_id and conflict_tracker.has_lecturer_conflict(lecturer_id, current_day, current_slot_idx)
                program_conflict = program_id and conflict_tracker.has_program_conflict(program_id, year, current_day, current_slot_idx, new_alloc=rep)

                if program_conflict:
                    resolved, new_day, new_slot_idx = conflict_tracker.resolve_program_conflict(
                        program_id, year, current_day, current_slot_idx, lecturer_id, new_alloc=rep
                    )
                    if resolved:
                        current_day = new_day
                        current_slot_idx = new_slot_idx
                        start, end = slots[current_slot_idx]
                        lecturer_conflict = lecturer_id and conflict_tracker.has_lecturer_conflict(lecturer_id, current_day, current_slot_idx)
                        program_conflict = False

                if lecturer_conflict or program_conflict:
                    continue

                if is_merged:
                    individual_conflict = False
                    for alloc in allocs:
                        a_lid = alloc.lecturer.id if alloc.lecturer else None
                        a_pid = alloc.program.id if alloc.program else None
                        try:
                            a_yr = cache.get_course_year(alloc)
                        except Exception:
                            individual_conflict = True
                            break
                        if a_lid and conflict_tracker.has_lecturer_conflict(a_lid, current_day, current_slot_idx):
                            individual_conflict = True
                            break
                        if a_pid and conflict_tracker.has_program_conflict(a_pid, a_yr, current_day, current_slot_idx, new_alloc=alloc):
                            individual_conflict = True
                            break
                    if individual_conflict:
                        continue

                venue = venue_allocator.find_efficient_venue(total_students, all_venues, current_day, current_slot_idx, "best_fit")
                if venue:
                    entry_key = (venue.id, current_day, start, end)
                    if entry_key in batch_entry_keys:
                        continue
                    if cache.is_duplicate_entry(venue.id, current_day, start, end):
                        continue

                    entries_to_write = [
                        TempTimetable(
                            course_allocation=alloc,
                            venue=venue,
                            day=current_day,
                            start_time=start,
                            end_time=end
                        )
                        for alloc in allocs
                    ]
                    rows_written = safe_bulk_create_timetable_entries(entries_to_write, cache)
                    if rows_written == 0:
                        safe_print(
                            f"[Fallback] DB write failed for {code} @ "
                            f"{venue.code} {current_day} {start}–{end}"
                        )
                        continue

                    batch_entry_keys.add(entry_key)
                    conflict_tracker.add_merged_schedule(allocs, venue.id, current_day, current_slot_idx, cache)

                    _register_placed_task(task, allocs, globally_scheduled_alloc_ids, cache)

                    scheduled_count += len(allocs)
                    assigned = True
                    scheduled_list.append(
                        f"{code} (Year {year}) in {venue.code} [FALLBACK] ({current_day} {start}-{end})"
                    )
                    break

        if not assigned:
            still_unscheduled.append(task)
            try:
                unscheduled_list_out.append(f"{code} (Year {year}, {total_students} students) - UNSCHEDULABLE")
            except Exception:
                unscheduled_list_out.append(f"{code} ({total_students} students) - UNSCHEDULABLE")

    return scheduled_count, still_unscheduled, scheduled_list, unscheduled_list_out


def compute_task_conflict_density(task, conflict_tracker, cache) -> int:
    rep = _representative(task)
    program = rep.program
    if not program:
        return 0
    try:
        yr = cache.get_course_year(rep)
    except Exception:
        return 0
    pid = program.id
    return sum(
        len(slot_set)
        for slot_set in conflict_tracker.program_year_schedule.get(
            (pid, yr), {}
        ).values()
    )


def compute_lecturer_load(conflict_tracker) -> Dict[int, int]:
    load: Dict[int, int] = defaultdict(int)
    for lid, day_map in conflict_tracker.lecturer_schedule.items():
        for slot_set in day_map.values():
            load[lid] += len(slot_set)
    return dict(load)


def process_ug_compression(
    unscheduled_tasks: List,
    all_venues: List,
    days: List[str],
    slots: List[Tuple[dtime, dtime]],
    conflict_tracker,
    venue_allocator,
    cache,
    globally_scheduled_alloc_ids: set = None,
) -> Tuple[int, List, List, List]:
    if not unscheduled_tasks:
        return 0, [], [], []

    if globally_scheduled_alloc_ids is None:
        globally_scheduled_alloc_ids = set()

    safe_print(f"[Phase3-Compression] Starting: {len(unscheduled_tasks)} tasks")

    sorted_tasks = sorted(
        unscheduled_tasks,
        key=lambda t: compute_task_conflict_density(t, conflict_tracker, cache),
        reverse=True,
    )

    lecturer_load = compute_lecturer_load(conflict_tracker)

    scheduled_count = 0
    still_unscheduled = []
    scheduled_list = []
    unscheduled_list_out = []
    batch_entry_keys: Set = set()

    for task in sorted_tasks:
        skip, task = _should_skip_task(task, globally_scheduled_alloc_ids, cache, "Compression")
        if skip:
            continue

        assigned = False
        is_merged = isinstance(task, dict)
        allocs = _task_allocs(task)
        total_students = _task_students(task)
        rep = _representative(task)
        lecturer = rep.lecturer
        lecturer_id = lecturer.id if lecturer else None
        program = rep.program
        program_id = program.id if program else None
        code = merged_course_label(task)

        try:
            year = cache.get_course_year(rep)
        except Exception:
            still_unscheduled.append(task)
            unscheduled_list_out.append(f"{code} - Year detection failed (compression)")
            continue

        lect_load_by_day: Dict[str, int] = defaultdict(int)
        if lecturer_id:
            for day in days:
                lect_load_by_day[day] = len(
                    conflict_tracker.lecturer_schedule.get(lecturer_id, {}).get(day, set())
                )
        preferred_days = sorted(days, key=lambda d: lect_load_by_day.get(d, 0))

        for day in preferred_days:
            if assigned:
                break
            for slot_idx in range(len(slots)):
                start, end = slots[slot_idx]

                lecturer_conflict = (
                    lecturer_id and
                    conflict_tracker.has_lecturer_conflict(lecturer_id, day, slot_idx)
                )
                program_conflict = (
                    program_id and
                    conflict_tracker.has_program_conflict(
                        program_id, year, day, slot_idx, new_alloc=rep
                    )
                )

                if program_conflict:
                    resolved, new_day, new_slot_idx = conflict_tracker.resolve_program_conflict(
                        program_id, year, day, slot_idx, lecturer_id, new_alloc=rep
                    )
                    if resolved:
                        day = new_day
                        slot_idx = new_slot_idx
                        start, end = slots[slot_idx]
                        lecturer_conflict = (
                            lecturer_id and
                            conflict_tracker.has_lecturer_conflict(lecturer_id, day, slot_idx)
                        )
                        program_conflict = False

                if lecturer_conflict or program_conflict:
                    continue

                if is_merged:
                    individual_conflict = False
                    for alloc in allocs:
                        a_lid = alloc.lecturer.id if alloc.lecturer else None
                        a_pid = alloc.program.id if alloc.program else None
                        try:
                            a_yr = cache.get_course_year(alloc)
                        except Exception:
                            individual_conflict = True
                            break
                        if a_lid and conflict_tracker.has_lecturer_conflict(a_lid, day, slot_idx):
                            individual_conflict = True
                            break
                        if a_pid and conflict_tracker.has_program_conflict(
                            a_pid, a_yr, day, slot_idx, new_alloc=alloc
                        ):
                            individual_conflict = True
                            break
                    if individual_conflict:
                        continue

                venue = venue_allocator.find_efficient_venue(
                    total_students, all_venues, day, slot_idx, "best_fit"
                )
                if venue:
                    entry_key = (venue.id, day, start, end)
                    if entry_key in batch_entry_keys:
                        continue
                    if cache.is_duplicate_entry(venue.id, day, start, end):
                        continue

                    entries_to_write = [
                        TempTimetable(
                            course_allocation=alloc,
                            venue=venue,
                            day=day,
                            start_time=start,
                            end_time=end,
                        )
                        for alloc in allocs
                    ]
                    rows_written = safe_bulk_create_timetable_entries(entries_to_write, cache)
                    if rows_written == 0:
                        safe_print(
                            f"[Compression] DB write failed for {code} @ "
                            f"{venue.code} {day} {start}–{end}"
                        )
                        continue

                    batch_entry_keys.add(entry_key)
                    conflict_tracker.add_merged_schedule(allocs, venue.id, day, slot_idx, cache)

                    _register_placed_task(task, allocs, globally_scheduled_alloc_ids, cache)

                    scheduled_count += len(allocs)
                    assigned = True
                    scheduled_list.append(
                        f"{code} (Year {year}) in {venue.code} [COMPRESSION] ({day} {start}-{end})"
                    )
                    break

        if not assigned:
            still_unscheduled.append(task)
            unscheduled_list_out.append(
                f"{code} (Year {year}, {total_students} students) - UNSCHEDULABLE (compression)"
            )

    safe_print(f"[Phase3-Compression] Placed {scheduled_count}, "
               f"still unscheduled: {len(still_unscheduled)}")
    return scheduled_count, still_unscheduled, scheduled_list, unscheduled_list_out


def process_pg_phase(pg_tasks, all_venues, days, slots,
                     conflict_tracker, venue_allocator, cache,
                     all_ug_scheduled: bool,
                     globally_scheduled_alloc_ids: set = None):
    if not pg_tasks:
        return 0, [], [], []

    if globally_scheduled_alloc_ids is None:
        globally_scheduled_alloc_ids = set()

    scheduled_count = 0
    still_unscheduled = []
    scheduled_list = []
    unscheduled_list_out = []

    batch_entry_keys = set()

    n = len(slots)

    if all_ug_scheduled:
        slot_preference = list(range(n - 1, -1, -1))
        safe_print("PG Phase: free-slot fill ON (all UG scheduled)")
    else:
        afternoon_start = max(0, n // 2)
        slot_preference = list(range(n - 1, afternoon_start - 1, -1))
        safe_print(f"PG Phase: afternoon-only slots {slot_preference} (some UG unscheduled)")

    for task in pg_tasks:
        skip, task = _should_skip_task(task, globally_scheduled_alloc_ids, cache, "PG")
        if skip:
            continue

        assigned = False
        is_merged = isinstance(task, dict)
        allocs = _task_allocs(task)
        total_students = _task_students(task)
        rep = _representative(task)
        lecturer = rep.lecturer
        lecturer_id = lecturer.id if lecturer else None
        program = rep.program
        program_id = program.id if program else None
        code = merged_course_label(task)
        try:
            year = cache.get_course_year(rep)
        except Exception:
            year = 6

        for day in days:
            if assigned:
                break
            for slot_idx in slot_preference:
                if slot_idx >= n:
                    continue
                start, end = slots[slot_idx]
                current_day = day
                current_slot_idx = slot_idx

                lecturer_conflict = lecturer_id and conflict_tracker.has_lecturer_conflict(lecturer_id, current_day, current_slot_idx)
                program_conflict = program_id and conflict_tracker.has_program_conflict(program_id, year, current_day, current_slot_idx, new_alloc=rep)

                if lecturer_conflict or program_conflict:
                    continue

                if is_merged:
                    individual_conflict = False
                    for alloc in allocs:
                        a_lid = alloc.lecturer.id if alloc.lecturer else None
                        a_pid = alloc.program.id if alloc.program else None
                        try:
                            a_yr = cache.get_course_year(alloc)
                        except Exception:
                            individual_conflict = True
                            break
                        if a_lid and conflict_tracker.has_lecturer_conflict(a_lid, current_day, current_slot_idx):
                            individual_conflict = True
                            break
                        if a_pid and conflict_tracker.has_program_conflict(a_pid, a_yr, current_day, current_slot_idx, new_alloc=alloc):
                            individual_conflict = True
                            break
                    if individual_conflict:
                        continue

                venue = venue_allocator.find_efficient_venue(total_students, all_venues, current_day, current_slot_idx, "best_fit")
                if venue:
                    entry_key = (venue.id, current_day, start, end)
                    if entry_key in batch_entry_keys:
                        continue
                    if cache.is_duplicate_entry(venue.id, current_day, start, end):
                        continue

                    entries_to_write = [
                        TempTimetable(
                            course_allocation=alloc,
                            venue=venue,
                            day=current_day,
                            start_time=start,
                            end_time=end
                        )
                        for alloc in allocs
                    ]
                    rows_written = safe_bulk_create_timetable_entries(entries_to_write, cache)
                    if rows_written == 0:
                        safe_print(
                            f"[PG] DB write failed for {code} @ "
                            f"{venue.code} {current_day} {start}–{end}"
                        )
                        continue

                    batch_entry_keys.add(entry_key)
                    conflict_tracker.add_merged_schedule(allocs, venue.id, current_day, current_slot_idx, cache)

                    _register_placed_task(task, allocs, globally_scheduled_alloc_ids, cache)

                    scheduled_count += len(allocs)
                    assigned = True
                    scheduled_list.append(
                        f"{code} [PG] in {venue.code} ({current_day} {start}-{end})"
                    )
                    break

        if not assigned:
            still_unscheduled.append(task)
            unscheduled_list_out.append(
                f"{code} [PG, {total_students} students] - no free afternoon/evening slot"
            )
            safe_print(f"PG UNSCHEDULED: {code}")

    return scheduled_count, still_unscheduled, scheduled_list, unscheduled_list_out


def _validate_and_rebuild_merged_group(missing_allocs: List, original_task: Dict,
                                        merge_limit: int) -> List:
    # A COD-defined CombinedCourseGroup must never be split apart by
    # regrouping on (course_code, lecturer) below — its sections often have
    # different lecturers by design, and that's exactly the case the
    # (norm_code, lect_id) grouping would blow apart into singletons. Keep
    # every still-missing allocation together as one task (even if only one
    # remains) so it's retried as a whole, or reported unschedulable as a
    # whole — never dissolved. Checked before the len==1 shortcut below so a
    # lone remaining section doesn't lose its combined_group tag either.
    if original_task.get('combined_group'):
        total_students = sum(a.number_of_students or 0 for a in missing_allocs)
        return [{
            'merged': missing_allocs,
            'total_students': total_students,
            'group_id': original_task.get('group_id'),
            'norm_code': original_task.get('norm_code'),
            'combined_group': original_task.get('combined_group'),
        }]

    if len(missing_allocs) == 1:
        return [missing_allocs[0]]

    regrouped = defaultdict(list)
    for a in missing_allocs:
        # Same base-code grouping as build_global_merged_tasks — strip
        # word-style ('GROUP C'/'GRP B') and bare-letter section tags too,
        # not just '-A'/'_C', so a still-unplaced 'ECON 313 b' regroups
        # with the still-unplaced 'ECON 313' from the same lecturer instead
        # of being re-split into singletons here.
        norm = normalize_course_code_base(a.course_code)
        lect_id = a.lecturer.id if a.lecturer else None
        regrouped[(norm, lect_id)].append(a)

    result_tasks = []

    for (norm_code, lect_id), group in regrouped.items():
        if len(group) == 1:
            result_tasks.append(group[0])
            safe_print(f"    → Adding individual: {group[0].course_code} (lecturer={lect_id})")
        else:
            total_students = sum(a.number_of_students or 0 for a in group)
            names = {_normalized_course_name(a.course_name) for a in group}
            names.discard("")

            if len(names) > 1:
                safe_print(f"    → CANNOT MERGE {norm_code}: different course names - adding individually")
                for a in group:
                    result_tasks.append(a)
                continue

            if total_students <= merge_limit:
                partial_task = {
                    'merged': group,
                    'total_students': total_students,
                    'group_id': original_task.get('group_id'),
                    'norm_code': norm_code,
                }
                result_tasks.append(partial_task)
                safe_print(
                    f"    → Adding VALID merged group: {norm_code} x{len(group)} "
                    f"(lecturer={lect_id}, students={total_students})"
                )
            else:
                # Doesn't all fit in one venue — pack into as few merged
                # bins as possible instead of falling back to individual
                # scheduling for every section (see _pack_into_merge_bins).
                bins = _pack_into_merge_bins(group, merge_limit)
                bin_totals = [sum(a.number_of_students or 0 for a in b) for b in bins]
                safe_print(
                    f"    → PACKED {norm_code} x{len(group)} (students={total_students}>"
                    f"{merge_limit}) into {len(bins)} bin(s) {bin_totals} "
                    f"(lecturer={lect_id})"
                )
                for b in bins:
                    if len(b) == 1:
                        result_tasks.append(b[0])
                    else:
                        bin_total = sum(a.number_of_students or 0 for a in b)
                        result_tasks.append({
                            'merged': b,
                            'total_students': bin_total,
                            'group_id': original_task.get('group_id'),
                            'norm_code': norm_code,
                        })

    return result_tasks


def _rebuild_sweep_tasks_from_db(
    all_tasks: List,
    all_courses: List,
    placed_ids: Set[int],
    merge_limit: int = 200,
) -> List:
    alloc_id_to_task: Dict[int, object] = {}
    for t in all_tasks:
        for a in _task_allocs(t):
            alloc_id_to_task[a.id] = t

    unscheduled_tasks = []

    for t in all_tasks:
        allocs = _task_allocs(t)
        missing = [a for a in allocs if a.id not in placed_ids]

        if not missing:
            continue

        if isinstance(t, dict):
            if len(missing) == len(allocs):
                unscheduled_tasks.append(t)
                safe_print(
                    f"[Phase5-Sweep] Full merge group unscheduled: "
                    f"{t.get('norm_code', '?')} x{len(allocs)}"
                )
            else:
                safe_print(
                    f"[Phase5-Sweep] Validating partial merge: {len(missing)}/{len(allocs)} allocs "
                    f"unplaced for '{t.get('norm_code', t.get('code', '?'))}'"
                )
                validated_tasks = _validate_and_rebuild_merged_group(missing, t, merge_limit)
                unscheduled_tasks.extend(validated_tasks)
        else:
            unscheduled_tasks.append(t)

    ids_covered_by_tasks = set(alloc_id_to_task.keys())
    orphans = [
        c for c in all_courses
        if c.id not in placed_ids and c.id not in ids_covered_by_tasks
    ]

    if orphans:
        safe_print(
            f"[Phase5-Sweep] PRE-SWEEP VALIDATION: found {len(orphans)} orphaned "
            f"allocation(s) — injecting as individual tasks."
        )
        for c in orphans:
            safe_print(f"  → Injecting orphan: {getattr(c, 'course_code', c.id)}")
            unscheduled_tasks.append(c)

    return unscheduled_tasks


def _dummy_report() -> SchedulingAnalysisReport:
    return SchedulingAnalysisReport()


# ═══════════════════════════════════════════════════════════════════════════════
# FIXED: process_exhaustive_sweep - With DISSOLVE for merged groups
# ═══════════════════════════════════════════════════════════════════════════════

def process_exhaustive_sweep(
    all_tasks: List,
    all_venues: List,
    days: List[str],
    slots: List[Tuple[dtime, dtime]],
    conflict_tracker,
    venue_allocator,
    cache,
    all_courses: List = None,
    merge_limit: int = 200,
    globally_scheduled_alloc_ids: set = None,
    analysis_report: 'SchedulingAnalysisReport' = None,
) -> Tuple[int, List[str], List[str]]:
    if globally_scheduled_alloc_ids is None:
        globally_scheduled_alloc_ids = set()

    safe_print("[Phase5-Sweep] Rebuilding conflict tracker from DB for accurate baseline…")
    conflict_tracker.lecturer_schedule.clear()
    conflict_tracker.program_year_schedule.clear()
    conflict_tracker.venue_schedule.clear()
    conflict_tracker.program_year_daily_count.clear()
    conflict_tracker.program_year_slot_allocs.clear()

    _slot_lookup: Dict[Tuple, int] = {
        (s, e): idx for idx, (s, e) in enumerate(slots)
    }
    try:
        _db_entries = list(
            TempTimetable.objects.select_related(
                'course_allocation__lecturer',
                'course_allocation__program',
            ).all()
        )
        for _entry in _db_entries:
            _alloc = _entry.course_allocation
            _lid = _alloc.lecturer.id if _alloc.lecturer else None
            _pid = _alloc.program.id if _alloc.program else None
            _sidx = _slot_lookup.get((_entry.start_time, _entry.end_time))
            if _sidx is None:
                _sidx = _fuzzy_slot_lookup(_entry.start_time, _entry.end_time, slots)
            if _sidx is None:
                safe_print(
                    f"[Phase5-Sweep] Cannot map DB time {_entry.start_time}-"
                    f"{_entry.end_time} to any slot — skipping"
                )
                continue
            try:
                _yr = cache.get_course_year(_alloc)
            except Exception:
                _yr = 1
            conflict_tracker.add_schedule(
                _lid, _pid, _yr, _entry.venue_id,
                _entry.day, _sidx,
                _alloc.course_code, alloc=_alloc,
            )
        safe_print(
            f"[Phase5-Sweep] Conflict tracker rebuilt from {len(_db_entries)} DB entries."
        )
    except Exception as _e:
        safe_print(f"[Phase5-Sweep] WARNING: tracker rebuild error: {_e}.")

    placed_ids: Set[int] = set(
        TempTimetable.objects.values_list("course_allocation_id", flat=True).distinct()
    )
    globally_scheduled_alloc_ids.update(placed_ids)

    if all_courses:
        cache.sync_scheduled_keys_from_alloc_ids(all_courses, globally_scheduled_alloc_ids)

    unscheduled_tasks = _rebuild_sweep_tasks_from_db(
        all_tasks,
        all_courses if all_courses is not None else [],
        placed_ids,
        merge_limit,
    )

    filtered_tasks = []
    for t in unscheduled_tasks:
        rep = _representative(t)
        lid = rep.lecturer.id if rep.lecturer else None
        pid = rep.program.id if rep.program else None
        sgid = getattr(rep, 'student_group_id', None)
        if cache.is_course_already_scheduled(rep.course_code, lid, pid, sgid):
            safe_print(
                f"[Phase5-Sweep] PRE-FILTER SKIP {merged_course_label(t)}: "
                f"canonical key already placed"
            )
            continue
        filtered_tasks.append(t)

    unscheduled_tasks = filtered_tasks

    if not unscheduled_tasks:
        safe_print("[Phase5-Sweep] No unscheduled tasks — sweep skipped.")
        return 0, [], []

    safe_print(
        f"[Phase5-Sweep] Starting exhaustive sweep for "
        f"{len(unscheduled_tasks)} unscheduled task(s) | "
        f"{len(days)} days × {len(slots)} slots × {len(all_venues)} venues"
    )

    merged_count = sum(1 for t in unscheduled_tasks if isinstance(t, dict))
    indiv_count = len(unscheduled_tasks) - merged_count
    safe_print(
        f"[Phase5-Sweep] Task composition: {merged_count} merged groups, "
        f"{indiv_count} individual courses"
    )

    cache.load_existing_timetable_entries()

    unscheduled_tasks = sorted(
        unscheduled_tasks,
        key=lambda t: -_task_students(t),
    )

    venues_asc: List = sorted(all_venues, key=lambda v: v.capacity or 0)

    placed_count = 0
    scheduled_list: List[str] = []
    unschedulable_list: List[str] = []

    def _try_place_group_in_slot(sub_allocs: List, group_label: Optional[str] = None):
        """Attempt to place `sub_allocs` together in ONE free day/slot/venue
        using the same escalating 3-pass search as the main sweep loop
        (exact fit → ≤10% overflow → any free venue). On success this does
        the DB write and all conflict-tracker/cache bookkeeping itself and
        returns (True, label); on failure returns (False, None).

        Used for the top-level swept task's own search AND, from the
        DISSOLVE path below, for re-packed sub-bins of a merged group that
        didn't fit whole — so a group can shed down to 2-3 shared-venue
        chunks instead of unraveling all the way to one lecturer-slot per
        section, which is what starves an already heavily-booked lecturer
        (see the load-warning logged at analysis time) of a free slot for
        whatever section is left over last.
        """
        sub_is_merged = len(sub_allocs) > 1
        sub_task = (
            {
                'merged': sub_allocs,
                'total_students': sum(a.number_of_students or 0 for a in sub_allocs),
                'group_id': None,
                'norm_code': None,
            }
            if sub_is_merged else sub_allocs[0]
        )
        sub_total = _task_students(sub_task)
        sub_rep = _representative(sub_task)
        sub_lecturer_id = sub_rep.lecturer.id if sub_rep.lecturer else None
        sub_program_id = sub_rep.program.id if sub_rep.program else None
        sub_code = group_label or merged_course_label(sub_task)
        try:
            sub_year = cache.get_course_year(sub_rep)
        except Exception:
            return False, None

        for sub_pass in range(1, 4):
            check_program_yr = True  # NEVER relaxed, in any pass.

            if sub_pass < 3:
                _overflow_tol = 0.10 if sub_pass == 2 else 0.0
                _cap_strat = compute_capacity_strategy(
                    sub_task, all_venues,
                    analysis_report if analysis_report is not None else _dummy_report(),
                    cache,
                    overflow_tolerance=_overflow_tol,
                )
                _venue_pool = _cap_strat['venue_candidates']
                if _cap_strat['strategy'] == 'split_recommend':
                    continue
            else:
                _venue_pool = sorted(all_venues, key=lambda v: v.capacity or 0, reverse=True)

            for day in days:
                for slot_idx, (start, end) in enumerate(slots):
                    if sub_lecturer_id and \
                            conflict_tracker.has_lecturer_conflict(sub_lecturer_id, day, slot_idx):
                        continue
                    if check_program_yr and sub_program_id and \
                            conflict_tracker.has_program_conflict(
                                sub_program_id, sub_year, day, slot_idx, new_alloc=sub_rep):
                        continue

                    if sub_is_merged:
                        conflict = False
                        for alloc in sub_allocs:
                            a_lid = alloc.lecturer.id if alloc.lecturer else None
                            a_pid = alloc.program.id if alloc.program else None
                            try:
                                a_yr = cache.get_course_year(alloc)
                            except Exception:
                                conflict = True
                                break
                            if a_lid and \
                                    conflict_tracker.has_lecturer_conflict(a_lid, day, slot_idx):
                                conflict = True
                                break
                            if check_program_yr and a_pid and \
                                    conflict_tracker.has_program_conflict(
                                        a_pid, a_yr, day, slot_idx, new_alloc=alloc):
                                conflict = True
                                break
                        if conflict:
                            continue

                    chosen_venue: Optional[Venue] = None
                    for v in _venue_pool:
                        if sub_pass == 1 and (v.capacity or 0) < sub_total:
                            continue
                        if sub_pass == 2 and (v.capacity or 0) < int(sub_total * 0.90):
                            continue
                        if conflict_tracker.has_venue_conflict(v.id, day, slot_idx):
                            continue
                        if cache.is_duplicate_entry(v.id, day, start, end):
                            continue
                        chosen_venue = v
                        break

                    if chosen_venue is None:
                        continue

                    entries_to_write = [
                        TempTimetable(
                            course_allocation=a,
                            venue=chosen_venue,
                            day=day,
                            start_time=start,
                            end_time=end,
                        )
                        for a in sub_allocs
                    ]
                    rows_written = safe_bulk_create_timetable_entries(entries_to_write, cache)
                    if rows_written == 0:
                        continue

                    conflict_tracker.add_merged_schedule(
                        sub_allocs, chosen_venue.id, day, slot_idx, cache
                    )
                    _register_placed_task(sub_task, sub_allocs, globally_scheduled_alloc_ids, cache)

                    pass_label = f"pass={sub_pass}" if sub_pass > 1 else "strict"
                    label = (
                        f"{sub_code} (Year {sub_year}, {sub_total} students) "
                        f"→ {chosen_venue.code} (cap {chosen_venue.capacity}) "
                        f"[SWEEP/REPACK/{pass_label}] ({day} {start}–{end})"
                    )
                    return True, label

        return False, None

    for task_idx, task in enumerate(unscheduled_tasks):
        skip, task = _should_skip_task(task, globally_scheduled_alloc_ids, cache, "Sweep")
        if skip:
            continue

        is_merged = isinstance(task, dict)
        allocs = _task_allocs(task)
        total_students = _task_students(task)
        rep = _representative(task)
        lecturer_id = rep.lecturer.id if rep.lecturer else None
        program_id = rep.program.id if rep.program else None
        code = merged_course_label(task)

        try:
            year = cache.get_course_year(rep)
        except Exception:
            unschedulable_list.append(f"{code} [SWEEP] — year detection failed")
            safe_print(f"[Phase5-Sweep] SKIP {code}: year detection failed")
            continue

        safe_print(
            f"[Phase5-Sweep] Testing ({task_idx + 1}/{len(unscheduled_tasks)}): "
            f"{code}  students={total_students}  year={year}"
        )

        assigned = False
        tested_triples = 0
        placement_note = ""

        # Three escalating passes.
        # HARD RULE — NEVER RELAXED, in any pass: lecturer double-booking and
        # program-year collision. Two courses for the same program-year (or
        # the same lecturer) are never allowed to share a slot, regardless of
        # recorded enrollment numbers.
        # The only thing that escalates across passes is VENUE CAPACITY:
        #   Pass 1 — exact fit (venue capacity >= demand)
        #   Pass 2 — up to 10% capacity overflow
        #   Pass 3 — capacity is NOT a placement gate at all: any genuinely
        #            free (collision-free) venue is used, largest first. This
        #            guarantees a course is never left unscheduled purely
        #            because every room is a little too small — that's a
        #            venue-supply problem to flag for review, not a reason to
        #            leave students without a class.
        for sweep_pass in range(1, 4):
            if assigned:
                break

            check_program_yr = True  # NEVER relaxed, in any pass.

            if sweep_pass == 1:
                placement_note = ""
            elif sweep_pass == 2:
                placement_note = " [INFO: capacity overflow ≤10% — no exact-fit venue was free]"
            else:
                placement_note = (
                    " [INFO: CAPACITY LAST RESORT — placed in largest free venue "
                    "regardless of size; review venue capacity/supply]"
                )

            if sweep_pass < 3:
                _overflow_tol = 0.10 if sweep_pass == 2 else 0.0
                _cap_strat = compute_capacity_strategy(
                    task, all_venues,
                    analysis_report if analysis_report is not None else _dummy_report(),
                    cache,
                    overflow_tolerance=_overflow_tol,
                )
                _venue_pool = _cap_strat['venue_candidates']

                if _cap_strat['strategy'] == 'split_recommend':
                    safe_print(
                        f"[Phase5-Sweep] Pass {sweep_pass}: no venue fits within "
                        f"{_overflow_tol*100:.0f}% tolerance for {code} — advancing to next pass"
                    )
                    continue
            else:
                # Last-resort pass: capacity is never a reason to leave a
                # course unscheduled. Every venue is a candidate, largest
                # first, so we minimise (but don't require) overflow.
                _venue_pool = sorted(all_venues, key=lambda v: v.capacity or 0, reverse=True)

            for day in days:
                if assigned:
                    break

                for slot_idx, (start, end) in enumerate(slots):
                    if assigned:
                        break

                    if lecturer_id and \
                            conflict_tracker.has_lecturer_conflict(lecturer_id, day, slot_idx):
                        continue

                    if check_program_yr and program_id and \
                            conflict_tracker.has_program_conflict(
                                program_id, year, day, slot_idx, new_alloc=rep):
                        continue

                    if is_merged:
                        slot_has_individual_conflict = False
                        for alloc in allocs:
                            a_lid = alloc.lecturer.id if alloc.lecturer else None
                            a_pid = alloc.program.id if alloc.program else None
                            try:
                                a_yr = cache.get_course_year(alloc)
                            except Exception:
                                slot_has_individual_conflict = True
                                break
                            if a_lid and \
                                    conflict_tracker.has_lecturer_conflict(a_lid, day, slot_idx):
                                slot_has_individual_conflict = True
                                break
                            if check_program_yr and a_pid and \
                                    conflict_tracker.has_program_conflict(
                                        a_pid, a_yr, day, slot_idx, new_alloc=alloc):
                                slot_has_individual_conflict = True
                                break
                        if slot_has_individual_conflict:
                            continue

                    tested_triples += 1
                    chosen_venue: Optional[Venue] = None

                    for v in _venue_pool:
                        if sweep_pass == 1 and (v.capacity or 0) < total_students:
                            continue
                        if sweep_pass == 2 and (v.capacity or 0) < int(total_students * 0.90):
                            continue
                        # sweep_pass == 3: no capacity filter — any free venue qualifies.
                        if conflict_tracker.has_venue_conflict(v.id, day, slot_idx):
                            continue
                        if cache.is_duplicate_entry(v.id, day, start, end):
                            continue
                        chosen_venue = v
                        break

                    if chosen_venue is None:
                        continue

                    if chosen_venue.capacity and chosen_venue.capacity < total_students:
                        overflow_pct = (total_students - chosen_venue.capacity) / total_students * 100
                        safe_print(
                            f"[Phase5-Sweep] CAPACITY OVERFLOW PLACEMENT: {code} "
                            f"({total_students} students → {chosen_venue.code} "
                            f"cap={chosen_venue.capacity}, "
                            f"{overflow_pct:.1f}% over capacity)"
                        )

                    entries_to_write = [
                        TempTimetable(
                            course_allocation=alloc,
                            venue=chosen_venue,
                            day=day,
                            start_time=start,
                            end_time=end,
                        )
                        for alloc in allocs
                    ]

                    rows_written = safe_bulk_create_timetable_entries(entries_to_write, cache)

                    if rows_written == 0:
                        safe_print(
                            f"[Phase5-Sweep] DB write rejected for {code} @ "
                            f"{chosen_venue.code} {day} {start}-{end} — continuing"
                        )
                        continue

                    conflict_tracker.add_merged_schedule(
                        allocs, chosen_venue.id, day, slot_idx, cache
                    )

                    _register_placed_task(task, allocs, globally_scheduled_alloc_ids, cache)

                    placed_count += len(allocs)
                    assigned = True

                    pass_label = f"pass={sweep_pass}" if sweep_pass > 1 else "strict"
                    label = (
                        f"{code} (Year {year}, {total_students} students) "
                        f"→ {chosen_venue.code} (cap {chosen_venue.capacity}) "
                        f"[SWEEP/{pass_label}] ({day} {start}–{end})"
                        f"{placement_note}"
                    )
                    scheduled_list.append(label)
                    safe_print(f"[Phase5-Sweep] PLACED: {label}")
                    break

        # If merged group failed all 3 passes, DISSOLVE and retry each section
        # individually — but NEVER for a COD-defined CombinedCourseGroup.
        # Those groups (task['combined_group'] set) represent a deliberate
        # curriculum decision that these sections are ONE class in ONE room;
        # auto-merges from same-course/same-lecturer convenience grouping
        # (combined_group is None) carry no such requirement and are safe to
        # split back into individual sections when they don't fit together.
        is_combined_course_group = bool(task.get('combined_group')) if isinstance(task, dict) else False

        if not assigned and is_merged and len(allocs) > 1 and is_combined_course_group:
            msg = (
                f"{code} (Year {year}, {total_students} students) "
                f"[SWEEP] — Truly Unschedulable after testing "
                f"{tested_triples} (day×slot) combinations × "
                f"{len(all_venues)} venues each (CombinedCourseGroup — never "
                f"split into individual sections; needs a bigger venue/slot "
                f"or the COD panel combination should be revisited)"
            )
            unschedulable_list.append(msg)
            safe_print(
                f"[Phase5-Sweep] TRULY UNSCHEDULABLE: {code} "
                f"(CombinedCourseGroup — dissolve is disabled for this task)"
            )

        elif not assigned and is_merged and len(allocs) > 1:
            any_individual_placed = False
            placed_count_before_dissolve = placed_count
            remaining_after_repack = list(allocs)

            # Before fully dissolving into len(allocs) separate lecturer
            # slots, try repacking into a couple of smaller shared-venue
            # chunks. A full N-way dissolve needs N distinct free
            # lecturer-slots where the whole merge only needed 1 — that's
            # exactly what leaves a leftover section unplaceable when the
            # lecturer is already heavily booked (see the load-warning
            # logged at analysis time). Splitting into ~2 chunks instead
            # still shares venues for most sections while needing far
            # fewer free slots than full dissolve.
            if len(allocs) > 2:
                largest_single = max((a.number_of_students or 0) for a in allocs)
                half_cap = max(largest_single, (total_students + 1) // 2)
                repack_bins = _pack_into_merge_bins(allocs, half_cap)
            else:
                repack_bins = [[a] for a in allocs]  # already small; go straight to per-section

            if len(repack_bins) > 1:
                safe_print(
                    f"[Phase5-Sweep] Merged group {code} didn't fit as one block "
                    f"({total_students} students) — trying {len(repack_bins)} "
                    f"repacked chunk(s) before falling back to per-section dissolve"
                )
                still_remaining = []
                for rb in repack_bins:
                    if len(rb) == 1:
                        still_remaining.extend(rb)
                        continue
                    rb_label = "/".join(a.course_code for a in rb)
                    rb_success, rb_out_label = _try_place_group_in_slot(rb, group_label=rb_label)
                    if rb_success:
                        placed_count += len(rb)
                        any_individual_placed = True
                        scheduled_list.append(rb_out_label)
                        safe_print(f"[Phase5-Sweep/REPACK] PLACED: {rb_out_label}")
                    else:
                        safe_print(
                            f"[Phase5-Sweep/REPACK] Could not place repacked chunk "
                            f"{rb_label} ({sum(a.number_of_students or 0 for a in rb)} "
                            f"students) — falling back to per-section dissolve for it"
                        )
                        still_remaining.extend(rb)
                remaining_after_repack = still_remaining

            if not remaining_after_repack:
                assigned = True

            safe_print(
                f"[Phase5-Sweep] DISSOLVING merged group {code} — "
                f"retrying {len(remaining_after_repack)} section(s) individually"
            )

            # Try each individual allocation separately
            for ind_alloc in remaining_after_repack:
                if ind_alloc.id in globally_scheduled_alloc_ids:
                    continue
                    
                ind_students = ind_alloc.number_of_students or 0
                ind_lid = ind_alloc.lecturer.id if ind_alloc.lecturer else None
                ind_pid = ind_alloc.program.id if ind_alloc.program else None
                try:
                    ind_yr = cache.get_course_year(ind_alloc)
                except Exception:
                    ind_yr = 1
                
                ind_placed = False
                ind_code = ind_alloc.course_code
                
                # Try each pass for the individual section.
                # HARD RULE — NEVER RELAXED: lecturer and program-year conflict
                # checks are identical across all three passes. Only venue
                # capacity escalates, ending in a pass-3 "any free venue,
                # largest first" last resort so a dissolved section is never
                # left unplaced purely because rooms are undersized.
                for ind_pass in range(1, 4):
                    if ind_placed:
                        break
                    
                    ind_check_py = True  # NEVER relaxed.
                    
                    # Build venue candidates for this individual section
                    if ind_pass == 1:
                        ind_fitting = [v for v in all_venues if (v.capacity or 0) >= ind_students]
                    elif ind_pass == 2:
                        min_needed = int(ind_students * 0.90)
                        ind_fitting = [v for v in all_venues if (v.capacity or 0) >= min_needed]
                    else:
                        # Last resort: capacity is not a gate — every venue qualifies.
                        ind_fitting = list(all_venues)
                    
                    if not ind_fitting:
                        continue
                    
                    # Sort by best fit (smallest adequate room first) for passes
                    # 1-2; largest-first for the capacity-agnostic last resort.
                    if ind_pass < 3:
                        ind_fitting_sorted = sorted(ind_fitting, key=lambda v: v.capacity or 0)
                    else:
                        ind_fitting_sorted = sorted(ind_fitting, key=lambda v: v.capacity or 0, reverse=True)
                    
                    for ind_day in days:
                        if ind_placed:
                            break
                        for ind_sidx, (ind_s, ind_e) in enumerate(slots):
                            if ind_placed:
                                break
                            
                            # Lecturer conflict - HARD constraint, never relaxed
                            if ind_lid and conflict_tracker.has_lecturer_conflict(ind_lid, ind_day, ind_sidx):
                                continue
                            # Program-year conflict - HARD constraint, never relaxed
                            if ind_check_py and ind_pid and conflict_tracker.has_program_conflict(
                                    ind_pid, ind_yr, ind_day, ind_sidx, new_alloc=ind_alloc):
                                continue
                            
                            # Find venue
                            ind_venue = None
                            for v in ind_fitting_sorted:
                                if conflict_tracker.has_venue_conflict(v.id, ind_day, ind_sidx):
                                    continue
                                if cache.is_duplicate_entry(v.id, ind_day, ind_s, ind_e):
                                    continue
                                ind_venue = v
                                break
                            
                            if ind_venue is None:
                                continue
                            
                            rows = safe_bulk_create_timetable_entries(
                                [TempTimetable(
                                    course_allocation=ind_alloc,
                                    venue=ind_venue,
                                    day=ind_day,
                                    start_time=ind_s,
                                    end_time=ind_e,
                                )],
                                cache,
                            )
                            if rows == 0:
                                continue
                            
                            conflict_tracker.add_merged_schedule(
                                [ind_alloc], ind_venue.id, ind_day, ind_sidx, cache
                            )
                            globally_scheduled_alloc_ids.add(ind_alloc.id)
                            ind_sgid = getattr(ind_alloc, 'student_group_id', None)
                            cache.mark_course_scheduled(ind_alloc.course_code, ind_lid, ind_pid, ind_sgid)
                            placed_count += 1
                            any_individual_placed = True
                            ind_placed = True
                            
                            ind_pass_label = f"pass={ind_pass}" if ind_pass > 1 else "strict"
                            scheduled_list.append(
                                f"{ind_code} (Year {ind_yr}, {ind_students} students) → {ind_venue.code} "
                                f"(cap {ind_venue.capacity}) [SWEEP/DISSOLVED/{ind_pass_label}] "
                                f"({ind_day} {ind_s}–{ind_e})"
                            )
                            safe_print(
                                f"[Phase5-Sweep/DISSOLVE] PLACED: {ind_code} → {ind_venue.code} "
                                f"({ind_day} {ind_s}–{ind_e}) pass={ind_pass}"
                            )
                # end individual pass loop
                if not ind_placed:
                    safe_print(
                        f"[Phase5-Sweep/DISSOLVE] Could not place {ind_code} individually either"
                    )
            # end per-alloc loop
            
            if any_individual_placed:
                assigned = True
                safe_print(f"[Phase5-Sweep/DISSOLVE] Successfully placed {placed_count - (placed_count_before_dissolve) if 'placed_count_before_dissolve' in dir() else 'some'} individual sections from dissolved group")
            else:
                # No individual sections could be placed - mark entire group as unschedulable
                msg = (
                    f"{code} (Year {year}, {total_students} students) "
                    f"[SWEEP] — Truly Unschedulable after testing "
                    f"{tested_triples} (day×slot) combinations × "
                    f"{len(all_venues)} venues each (all 3 passes exhausted, and dissolve retry failed)"
                )
                unschedulable_list.append(msg)
                safe_print(f"[Phase5-Sweep] TRULY UNSCHEDULABLE: {code} (dissolve retry also failed)")
        
        elif not assigned and not is_merged:
            # Individual course that failed
            msg = (
                f"{code} (Year {year}, {total_students} students) "
                f"[SWEEP] — Truly Unschedulable after testing "
                f"{tested_triples} (day×slot) combinations × "
                f"{len(all_venues)} venues each (all 3 passes exhausted)"
            )
            unschedulable_list.append(msg)
            safe_print(f"[Phase5-Sweep] TRULY UNSCHEDULABLE: {code} "
                       f"(tested {tested_triples} slot combos, 3 passes)")
        
        elif assigned and is_merged:
            safe_print(f"[Phase5-Sweep] SUCCESSFULLY PLACED merged group: {code}")

    safe_print(
        f"[Phase5-Sweep] Complete — placed={placed_count} | "
        f"truly_unschedulable={len(unschedulable_list)}"
    )
    return placed_count, scheduled_list, unschedulable_list


def process_lecturer_overload_relief(
    all_tasks: List,
    all_courses: List,
    all_venues: List,
    days: List[str],
    slots: List[Tuple[dtime, dtime]],
    conflict_tracker,
    venue_allocator,
    cache,
    globally_scheduled_alloc_ids: set,
    merge_limit: int = 200,
    overload_threshold: int = 10,
    protected_quota: int = 10,
) -> Tuple[int, List[str], List[str]]:
    """Last-resort pass for allocations still unscheduled purely because
    their lecturer's total course load is abnormal.

    A normal teaching load tops out around 7 units; collision-free
    scheduling is mathematically impossible once one lecturer is attached
    to more allocations than there are safe non-overlapping slots. In
    practice this pattern (e.g. one lecturer carrying 30, 20, 15 students
    across near-identical entries) is almost always a data-entry issue —
    duplicate sections that should have been merged into one course
    allocation and never were.

    Rather than silently guessing which allocations to merge (risky — a
    wrong auto-merge is worse than an unplaced course), this pass:
      1. Only considers lecturers whose total allocation count exceeds
         `overload_threshold`. Every other lecturer is untouched.
      2. Retries those allocations with full, NEVER-RELAXED conflict
         checking (lecturer double-booking and program-year collision are
         both hard constraints here, exactly as everywhere else in the
         algorithm). Venue CAPACITY is allowed to give: `find_efficient_venue`
         already falls back to the largest free venue when nothing fits the
         class size.
      3. PROTECTED QUOTA (`protected_quota`, default 10): the first
         `protected_quota` units this lecturer has already had scheduled
         collision-free anywhere in this run stay fully protected — for
         those, lecturer double-booking is NEVER forced. If no
         collision-free slot exists for one of those, it is left genuinely
         unscheduled and flagged '[OVERLOAD-DATA-FLAG]' for manual review,
         exactly as before.
      4. OVERFLOW BEYOND THE QUOTA: once a lecturer already has
         `protected_quota` or more units safely on the grid, any further
         units for that same lecturer are allowed — as a last resort, only
         after a genuinely collision-free slot search comes up empty — to
         be force-placed into a slot where the ONLY thing being relaxed is
         that lecturer's own double-booking. Program-year collision and
         venue double-booking are still never relaxed, even here — a
         cohort of students never gets double-booked, and two different
         courses never share a room. Every forced placement is tagged
         '[LECTURER-DOUBLE-BOOKED]' in the returned messages so admins can
         immediately see and follow up on it — this reflects an unfixed
         data problem (the lecturer's units genuinely don't fit in the
         week), it is not being hidden, just surfaced on the timetable
         instead of vanishing from it entirely.
    """
    if globally_scheduled_alloc_ids is None:
        globally_scheduled_alloc_ids = set()

    placed_ids: Set[int] = set(
        TempTimetable.objects.values_list("course_allocation_id", flat=True).distinct()
    )
    globally_scheduled_alloc_ids.update(placed_ids)

    remaining = _rebuild_sweep_tasks_from_db(all_tasks, all_courses, placed_ids, merge_limit)
    if not remaining:
        return 0, [], []

    lecturer_total_count: Dict[int, int] = defaultdict(int)
    for c in all_courses:
        if c.lecturer:
            lecturer_total_count[c.lecturer.id] += 1

    # Seed each lecturer's already-scheduled unit count from everything the
    # normal passes (Steps 1-5b) already placed collision-free — this is
    # what "first N units" is measured against.
    lecturer_scheduled_count: Dict[int, int] = defaultdict(int)
    for c in all_courses:
        if c.lecturer and c.id in placed_ids:
            lecturer_scheduled_count[c.lecturer.id] += 1

    overloaded_tasks = []
    for t in remaining:
        rep = _representative(t)
        lid = rep.lecturer.id if rep.lecturer else None
        if lid and lecturer_total_count.get(lid, 0) > overload_threshold:
            overloaded_tasks.append(t)

    if not overloaded_tasks:
        return 0, [], []

    safe_print(
        f"[OverloadRelief] {len(overloaded_tasks)} task(s) still blocked, all "
        f"belonging to lecturers with >{overload_threshold} total units — "
        f"attempting relief pass (first {protected_quota} units/lecturer stay "
        f"collision-free; overflow beyond that may be force-placed)"
    )

    venues_asc = sorted(all_venues, key=lambda v: v.capacity or 0)
    placed_count = 0
    scheduled_list: List[str] = []
    unschedulable_list: List[str] = []

    for task in sorted(overloaded_tasks, key=lambda t: -_task_students(t)):
        skip, task = _should_skip_task(task, globally_scheduled_alloc_ids, cache, "OverloadRelief")
        if skip:
            continue

        allocs = _task_allocs(task)
        total_students = _task_students(task)
        rep = _representative(task)
        lecturer_id = rep.lecturer.id if rep.lecturer else None
        program_id = rep.program.id if rep.program else None
        code = merged_course_label(task)
        try:
            year = cache.get_course_year(rep)
        except Exception:
            year = 1

        assigned = False
        day_order = list(days)
        random.shuffle(day_order)
        if program_id:
            day_order.sort(
                key=lambda d: conflict_tracker.get_program_year_day_load(program_id, year, d)
            )

        # ── PASS A: fully collision-free (lecturer + program-year), exactly
        # as before. Always attempted first regardless of quota status. ──
        for day in day_order:
            if assigned:
                break
            for slot_idx, (start, end) in enumerate(slots):
                if assigned:
                    break
                if lecturer_id and conflict_tracker.has_lecturer_conflict(
                        lecturer_id, day, slot_idx):
                    continue
                if program_id and conflict_tracker.has_program_conflict(
                        program_id, year, day, slot_idx, new_alloc=rep):
                    continue
                venue = venue_allocator.find_efficient_venue(
                    total_students, venues_asc, day, slot_idx, "best_fit"
                )
                if not venue:
                    continue
                if cache.is_duplicate_entry(venue.id, day, start, end):
                    continue
                entries = [
                    TempTimetable(course_allocation=a, venue=venue, day=day,
                                  start_time=start, end_time=end)
                    for a in allocs
                ]
                rows = safe_bulk_create_timetable_entries(entries, cache)
                if rows == 0:
                    continue
                conflict_tracker.add_merged_schedule(allocs, venue.id, day, slot_idx, cache)
                _register_placed_task(task, allocs, globally_scheduled_alloc_ids, cache)
                placed_count += len(allocs)
                lecturer_scheduled_count[lecturer_id] = lecturer_scheduled_count.get(lecturer_id, 0) + len(allocs)
                assigned = True
                cap_note = ""
                if venue.capacity and venue.capacity < total_students:
                    cap_note = (
                        f" [CAPACITY OVERFLOW: {total_students} students → venue cap "
                        f"{venue.capacity} — no larger free venue at this time]"
                    )
                scheduled_list.append(
                    f"{code} (Year {year}) → {venue.code} ({day} {start}-{end}) "
                    f"[OVERLOAD-DATA-FLAG: lecturer has {lecturer_total_count.get(lecturer_id, 0)} "
                    f"units (> {overload_threshold}) — review for a forgotten merge/split of "
                    f"duplicate sections]{cap_note}"
                )
                safe_print(f"[OverloadRelief] PLACED: {scheduled_list[-1]}")
                break

        # ── PASS B: only reached if Pass A found nothing AND this lecturer
        # has already used up their protected quota of collision-free units.
        # Program-year collision and venue conflict remain hard; only the
        # lecturer's own double-booking is allowed to give here — a hard
        # LecturerBlockedSlot rule is NOT double-booking and must still be
        # respected, so it's checked separately via has_lecturer_hard_block
        # rather than skipping has_lecturer_conflict (which would relax
        # both at once). ──
        if not assigned and lecturer_id:
            already_protected = lecturer_scheduled_count.get(lecturer_id, 0)
            if already_protected >= protected_quota:
                for day in day_order:
                    if assigned:
                        break
                    for slot_idx, (start, end) in enumerate(slots):
                        if assigned:
                            break
                        if conflict_tracker.has_lecturer_hard_block(lecturer_id, day, slot_idx):
                            continue
                        if program_id and conflict_tracker.has_program_conflict(
                                program_id, year, day, slot_idx, new_alloc=rep):
                            continue
                        venue = venue_allocator.find_efficient_venue(
                            total_students, venues_asc, day, slot_idx, "best_fit"
                        )
                        if not venue:
                            continue
                        if cache.is_duplicate_entry(venue.id, day, start, end):
                            continue
                        entries = [
                            TempTimetable(course_allocation=a, venue=venue, day=day,
                                          start_time=start, end_time=end)
                            for a in allocs
                        ]
                        rows = safe_bulk_create_timetable_entries(entries, cache)
                        if rows == 0:
                            continue
                        conflict_tracker.add_merged_schedule(allocs, venue.id, day, slot_idx, cache)
                        _register_placed_task(task, allocs, globally_scheduled_alloc_ids, cache)
                        placed_count += len(allocs)
                        lecturer_scheduled_count[lecturer_id] = lecturer_scheduled_count.get(lecturer_id, 0) + len(allocs)
                        assigned = True
                        scheduled_list.append(
                            f"{code} (Year {year}) → {venue.code} ({day} {start}-{end}) "
                            f"[LECTURER-DOUBLE-BOOKED: lecturer has {lecturer_total_count.get(lecturer_id, 0)} "
                            f"units (> {overload_threshold}); first {protected_quota} were kept collision-free, "
                            f"this is unit #{already_protected + len(allocs)} and had to overlap another of this "
                            f"lecturer's classes to avoid disappearing from the timetable — manual review required]"
                        )
                        safe_print(f"[OverloadRelief] FORCED (lecturer collision): {scheduled_list[-1]}")
                        break

        if not assigned:
            quota_note = (
                f"protected (within first {protected_quota} units)"
                if lecturer_scheduled_count.get(lecturer_id, 0) < protected_quota
                else "quota exceeded but still no program-year/venue-free slot existed"
            )
            unschedulable_list.append(
                f"{code} (Year {year}, {total_students} students) — lecturer load "
                f"{lecturer_total_count.get(lecturer_id, 0)} exceeds {overload_threshold} and "
                f"no usable slot was available ({quota_note}); this lecturer's duplicate/unmerged "
                f"sections need manual review and merging"
            )
            safe_print(f"[OverloadRelief] STILL UNPLACED: {unschedulable_list[-1]}")

    safe_print(
        f"[OverloadRelief] Complete — placed={placed_count} | "
        f"still_unschedulable={len(unschedulable_list)}"
    )
    return placed_count, scheduled_list, unschedulable_list


def process_sibling_colocation_force(
    all_tasks: List,
    all_courses: List,
    all_venues: List,
    days: List[str],
    slots: List[Tuple[dtime, dtime]],
    conflict_tracker,
    venue_allocator,
    cache,
    globally_scheduled_alloc_ids: set,
    merge_limit: int = 200,
) -> Tuple[int, List[str], List[str]]:
    """
    STEP 5a1 — SIBLING CO-LOCATION FORCE PASS.

    STEP 1's global merge (build_global_merged_tasks) already catches the
    common case — the same course, same lecturer, several sections — and
    merges them into ONE task before scheduling ever starts. But a section
    can still slip through that net and reach this point still unscheduled:
    a mismatched course_name blocks the merge check, the combined total blew
    past merge_limit and _pack_into_merge_bins opened a second bin that
    later failed to place, or the section only became "the same lecturer"
    after a late allocation edit. Whatever the reason, if this lecturer is
    ALREADY teaching this exact course somewhere on the grid, there is no
    good reason to burn a second venue and a second timeslot on a second
    lecture of the same material by the same person — the two sections
    should sit in the room together, exactly like a merge discovered late.

    This is intentionally much safer than every other forced pass below it:
    it never relaxes lecturer-double-booking or venue-double-booking against
    an UNRELATED course — the only thing it ever forces is co-locating a
    section with ITS OWN sibling (same normalised base course code + same
    lecturer_id), which was never a real collision to begin with; it's one
    person teaching one class. Venue capacity is allowed to be exceeded here
    exactly like everywhere else in this file (Phase 6/6B fix capacity
    afterward) — a lecturer with 4 students in one room and 20 in another,
    same unit, is exactly the "waste a venue" scenario this pass exists to
    close, whether the fit is small-into-big or the combined total is well
    over one room's rated capacity.

      PASS A — co-locate at whichever sibling slot causes no NEW program-
               year collision for an unrelated cohort.
      PASS B — no clean sibling slot exists — force it into the sibling's
               first slot anyway, tagged '[SIBLING-COLOCATION/
               PROGRAM-YEAR-OVERRIDE]' for manual review. This is exactly
               the kind of leftover collision process_post_schedule_
               collision_swap (the final pass, after venue optimisation)
               later tries to clear by relocating or swapping one side.

    Venue double-booking is NEVER relaxed against anything OTHER than the
    sibling itself — TempTimetable's unique_together deliberately includes
    course_allocation precisely so two different allocations legitimately
    sharing one (venue, day, slot) — a merge, a combined group, or this
    pass — is allowed, while the same allocation being written twice is
    still caught.
    """
    if globally_scheduled_alloc_ids is None:
        globally_scheduled_alloc_ids = set()

    placed_ids: Set[int] = set(
        TempTimetable.objects.values_list("course_allocation_id", flat=True).distinct()
    )
    globally_scheduled_alloc_ids.update(placed_ids)

    remaining = _rebuild_sweep_tasks_from_db(all_tasks, all_courses, placed_ids, merge_limit)
    if not remaining:
        return 0, [], []

    # ── Blocked / exclusive venue guard ─────────────────────────────────────
    # This pass previously indexed sibling slots straight off TempTimetable
    # with NO awareness of VenueBlock/exclusive specialization at all — a
    # hard-blocked or exclusive-specialization venue could still be offered
    # up as a "sibling slot" to join, silently reintroducing a course into a
    # venue that was supposed to be off the table for this scheduler run.
    # Excluded here, at index-build time, so a blocked venue can never even
    # be considered a candidate below.
    try:
        blocked_venue_ids = constraint_engine.get_blocked_venue_ids(scheduler_type="regular")
        exclusive_venue_ids = constraint_engine.get_exclusive_venue_ids(scheduler_type="regular")
    except Exception:
        blocked_venue_ids = set()
        exclusive_venue_ids = set()
    off_limits_venue_ids = blocked_venue_ids | exclusive_venue_ids

    # ── Index every (base course code, lecturer) already on the grid to
    # every distinct (day, start, end, venue) that lecturer's OWN sections
    # of that course currently occupy. ──────────────────────────────────────
    sibling_index: Dict[Tuple[str, int], List[Tuple[str, dtime, dtime, Any]]] = defaultdict(list)
    seen_slot_combo: Set[Tuple[str, int, str, dtime, dtime, int]] = set()

    # Running per-(family, slot) student total, keyed the same way as
    # combo_key below. Used to enforce merge_limit on every colocation
    # decision — this pass used to have NO cap at all, so a lecturer's
    # sections could keep piling onto the same sibling slot indefinitely
    # (one lecturer's ECON 313 reaching 300-400+ students in a single room
    # was exactly this: task after task forced into the same first-found
    # sibling slot with nothing ever checking the running total).
    slot_family_totals: Dict[Tuple[str, int, str, dtime, dtime, int], int] = defaultdict(int)

    existing_entries = TempTimetable.objects.select_related(
        'course_allocation', 'course_allocation__lecturer', 'venue'
    ).all()
    for e in existing_entries:
        alloc = e.course_allocation
        if not alloc or not alloc.lecturer_id or not e.venue:
            continue
        if e.venue_id in off_limits_venue_ids:
            continue  # never treat a blocked/exclusive venue as a joinable sibling slot
        norm = normalize_course_code_base(alloc.course_code)
        key = (norm, alloc.lecturer_id)
        combo_key = (norm, alloc.lecturer_id, e.day, e.start_time, e.end_time, e.venue_id)
        slot_family_totals[combo_key] += alloc.number_of_students or 0
        if combo_key in seen_slot_combo:
            continue
        seen_slot_combo.add(combo_key)
        sibling_index[key].append((e.day, e.start_time, e.end_time, e.venue))

    if not sibling_index:
        return 0, [], []

    placed_count = 0
    scheduled_list: List[str] = []
    unschedulable_list: List[str] = []

    for task in remaining:
        skip, task = _should_skip_task(task, globally_scheduled_alloc_ids, cache, "SiblingColocation")
        if skip:
            continue

        allocs = _task_allocs(task)
        total_students = _task_students(task)
        rep = _representative(task)
        lecturer_id = rep.lecturer.id if rep.lecturer else None
        program_id = rep.program.id if rep.program else None
        code = merged_course_label(task)

        if not lecturer_id:
            continue  # nothing to match "same lecturer" against

        norm = normalize_course_code_base(rep.course_code)
        candidates = sibling_index.get((norm, lecturer_id))
        if not candidates:
            continue

        try:
            year = cache.get_course_year(rep)
        except Exception:
            year = 1

        assigned = False

        # ── PASS A: clean sibling slot (no unrelated program-year clash) ──
        for day, start, end, venue in candidates:
            if assigned:
                break
            slot_idx = _fuzzy_slot_lookup(start, end, slots)
            if slot_idx is None:
                continue
            combo_key = (norm, lecturer_id, day, start, end, venue.id)
            projected_total = slot_family_totals.get(combo_key, 0) + total_students
            if projected_total > merge_limit:
                safe_print(
                    f"[SiblingColocation] SKIP {venue.code} ({day} {start}-{end}) for "
                    f"{code}: joining would grow the room to {projected_total} students, "
                    f"over merge_limit ({merge_limit}) — trying another sibling slot "
                    f"instead of overpacking this one"
                )
                continue
            if program_id and conflict_tracker.has_program_conflict(
                    program_id, year, day, slot_idx, new_alloc=rep):
                continue
            entries = [
                TempTimetable(course_allocation=a, venue=venue, day=day,
                              start_time=start, end_time=end)
                for a in allocs
            ]
            rows = safe_bulk_create_timetable_entries(entries, cache)
            if rows == 0:
                continue
            conflict_tracker.add_merged_schedule(allocs, venue.id, day, slot_idx, cache)
            _register_placed_task(task, allocs, globally_scheduled_alloc_ids, cache)
            placed_count += len(allocs)
            slot_family_totals[combo_key] = projected_total
            assigned = True
            cap_note = ""
            if venue.capacity and venue.capacity < total_students:
                cap_note = f" [combined with sibling exceeds venue cap {venue.capacity}]"
            scheduled_list.append(
                f"{code} (Year {year}) → {venue.code} ({day} {start}-{end}) "
                f"[SIBLING-COLOCATION: joined an existing {norm} section already "
                f"taught by this lecturer at this slot — same class, shared room, "
                f"{projected_total}/{merge_limit} students]{cap_note}"
            )
            safe_print(f"[SiblingColocation] PLACED (clean): {scheduled_list[-1]}")

        # ── PASS B: force into the first sibling slot that still has room
        # under merge_limit, relaxing program-year instead of capacity ─────
        if not assigned:
            for day, start, end, venue in candidates:
                combo_key = (norm, lecturer_id, day, start, end, venue.id)
                projected_total = slot_family_totals.get(combo_key, 0) + total_students
                if projected_total > merge_limit:
                    continue
                slot_idx = _fuzzy_slot_lookup(start, end, slots)
                if slot_idx is None:
                    continue
                entries = [
                    TempTimetable(course_allocation=a, venue=venue, day=day,
                                  start_time=start, end_time=end)
                    for a in allocs
                ]
                rows = safe_bulk_create_timetable_entries(entries, cache)
                if rows == 0:
                    continue
                conflict_tracker.add_merged_schedule(allocs, venue.id, day, slot_idx, cache)
                _register_placed_task(task, allocs, globally_scheduled_alloc_ids, cache)
                placed_count += len(allocs)
                slot_family_totals[combo_key] = projected_total
                assigned = True
                scheduled_list.append(
                    f"{code} (Year {year}) → {venue.code} ({day} {start}-{end}) "
                    f"[SIBLING-COLOCATION/PROGRAM-YEAR-OVERRIDE: forced alongside an "
                    f"existing {norm} section for this lecturer — overlaps another "
                    f"program-year's slot here ({projected_total}/{merge_limit} students); "
                    f"the final collision-swap pass will try to clear this]"
                )
                safe_print(f"[SiblingColocation] FORCED: {scheduled_list[-1]}")
                break

        if not assigned:
            unschedulable_list.append(
                f"{code} (Year {year}, {total_students} students) — sibling {norm} "
                f"section(s) exist for this lecturer but every one is either off-limits, "
                f"already at merge_limit ({merge_limit}), or the co-location write failed; "
                f"leaving it for OverloadRelief/FinalSafetyNet to open a fresh venue instead "
                f"of overpacking an existing one"
            )
            safe_print(f"[SiblingColocation] STILL UNPLACED: {unschedulable_list[-1]}")

    safe_print(
        f"[SiblingColocation] Complete — placed={placed_count} | "
        f"still_unschedulable={len(unschedulable_list)}"
    )
    return placed_count, scheduled_list, unschedulable_list


def process_universal_safety_net(
    all_courses: List,
    all_venues: List,
    days: List[str],
    slots: List[Tuple[dtime, dtime]],
    conflict_tracker,
    venue_allocator,
    cache,
    globally_scheduled_alloc_ids: set,
) -> Tuple[int, List[str], List[str]]:
    """Absolute final catch-all. Runs dead last, after Sweep, OverloadRelief,
    EveningWeekend and ZeroStudent have all had their shot.

    OverloadRelief already proves that forcing a lecturer's OWN double-
    booking (never program-year, never venue — those stay hard) is enough
    to rescue almost every course that survives the exhaustive sweep. But
    it only runs for allocations whose lecturer carries an abnormal load
    (> overload_threshold units), and it silently skips anything with
    lecturer_id = None, because there's nothing for it to consider
    "overloaded". A perfectly ordinary course taught by a lecturer under
    the threshold — or with no lecturer assigned yet at all — that still
    happens to be stuck (every venue booked in every window its
    program-year is free) currently has NO further recovery step at all.

    This pass closes that gap: it targets literally every allocation still
    missing from the timetable at this point, no threshold, no lecturer
    requirement.
      PASS A — retry a fully collision-free placement (costs nothing; other
               phases may have freed something up since this course was
               last attempted).
      PASS B — if the allocation HAS a lecturer, allow that lecturer's own
               double-booking as a last resort (program-year and venue
               remain hard, exactly as everywhere else). Tagged
               '[FINAL-SAFETY-NET/LECTURER-DOUBLE-BOOKED]' for review.
      PASS C — ABSOLUTE last resort: also relax the program-year conflict,
               so the course goes into the first day/slot where the VENUE
               itself is physically free, full stop. This is what makes
               "there are free venues, put the course there" literally
               true — a course no longer sits unplaced just because its
               own cohort has run out of windows, as long as some room
               somewhere is empty at that time. Tagged
               '[FINAL-SAFETY-NET/FORCED-OVERRIDE]' for review.

    The ONE thing that is never relaxed, in any pass: venue double-booking.
    Two classes can never share the same room at the same time — that's
    enforced by find_efficient_venue()'s has_venue_conflict() check plus
    is_duplicate_entry(), on every single pass including Pass C.

    Anything still unplaced after all three passes means every venue is
    double-booked in every day/slot window that exists — a genuine total
    venue-supply shortage (more courses than venues × days × slots can
    physically hold), not a rule the scheduler is refusing to relax. It's
    reported clearly so it can be resolved by adding venues/days/slots,
    rather than silently vanishing from the timetable.
    """
    if globally_scheduled_alloc_ids is None:
        globally_scheduled_alloc_ids = set()

    placed_ids: Set[int] = set(
        TempTimetable.objects.values_list("course_allocation_id", flat=True).distinct()
    )
    globally_scheduled_alloc_ids.update(placed_ids)

    remaining = [a for a in all_courses if a.id not in globally_scheduled_alloc_ids]
    if not remaining:
        return 0, [], []

    safe_print(
        f"[FinalSafetyNet] {len(remaining)} allocation(s) still unplaced after "
        f"every prior phase — running absolute last-resort pass (no lecturer-load "
        f"threshold, no lecturer requirement)"
    )

    venues_asc = sorted(all_venues, key=lambda v: v.capacity or 0)
    placed_count = 0
    scheduled_list: List[str] = []
    unschedulable_list: List[str] = []

    for alloc in sorted(remaining, key=lambda a: -(a.number_of_students or 0)):
        if alloc.id in globally_scheduled_alloc_ids:
            continue

        lecturer_id = alloc.lecturer.id if alloc.lecturer else None
        program_id = alloc.program.id if alloc.program else None
        student_group_id = getattr(alloc, 'student_group_id', None)
        total_students = alloc.number_of_students or 0
        code = alloc.course_code

        if cache.is_course_already_scheduled(alloc.course_code, lecturer_id, program_id, student_group_id):
            globally_scheduled_alloc_ids.add(alloc.id)
            continue

        try:
            year = cache.get_course_year(alloc)
        except Exception:
            year = 1

        day_order = list(days)
        random.shuffle(day_order)
        if program_id:
            day_order.sort(
                key=lambda d: conflict_tracker.get_program_year_day_load(program_id, year, d)
            )

        assigned = False

        # ── PASS A: fully collision-free, capacity ignored ──────────────────
        for day in day_order:
            if assigned:
                break
            for slot_idx, (start, end) in enumerate(slots):
                if assigned:
                    break
                if lecturer_id and conflict_tracker.has_lecturer_conflict(lecturer_id, day, slot_idx):
                    continue
                if program_id and conflict_tracker.has_program_conflict(
                        program_id, year, day, slot_idx, new_alloc=alloc):
                    continue
                venue = venue_allocator.find_efficient_venue(
                    total_students, venues_asc, day, slot_idx, "best_fit"
                )
                if not venue:
                    continue
                if cache.is_duplicate_entry(venue.id, day, start, end):
                    continue
                entry = TempTimetable(course_allocation=alloc, venue=venue, day=day,
                                       start_time=start, end_time=end)
                rows = safe_bulk_create_timetable_entries([entry], cache)
                if rows == 0:
                    continue
                conflict_tracker.add_merged_schedule([alloc], venue.id, day, slot_idx, cache)
                globally_scheduled_alloc_ids.add(alloc.id)
                cache.mark_course_scheduled(alloc.course_code, lecturer_id, program_id, student_group_id)
                placed_count += 1
                assigned = True
                scheduled_list.append(
                    f"{code} (Year {year}, {total_students} students) → {venue.code} "
                    f"(cap {venue.capacity}) [FINAL-SAFETY-NET/clean] ({day} {start}-{end})"
                )
                safe_print(f"[FinalSafetyNet] PLACED (clean): {scheduled_list[-1]}")

        # ── PASS B: lecturer double-booking allowed (only if a lecturer is
        # actually attached — nothing to relax otherwise). Program-year and
        # venue conflicts remain hard, never relaxed. A hard
        # LecturerBlockedSlot rule is also never relaxed here — it's not
        # double-booking, it's an explicit "never place this lecturer here"
        # rule, checked separately since has_lecturer_conflict is
        # intentionally skipped in this pass. ──────────────────────────────
        if not assigned and lecturer_id:
            for day in day_order:
                if assigned:
                    break
                for slot_idx, (start, end) in enumerate(slots):
                    if assigned:
                        break
                    if conflict_tracker.has_lecturer_hard_block(lecturer_id, day, slot_idx):
                        continue
                    if program_id and conflict_tracker.has_program_conflict(
                            program_id, year, day, slot_idx, new_alloc=alloc):
                        continue
                    venue = venue_allocator.find_efficient_venue(
                        total_students, venues_asc, day, slot_idx, "best_fit"
                    )
                    if not venue:
                        continue
                    if cache.is_duplicate_entry(venue.id, day, start, end):
                        continue
                    entry = TempTimetable(course_allocation=alloc, venue=venue, day=day,
                                           start_time=start, end_time=end)
                    rows = safe_bulk_create_timetable_entries([entry], cache)
                    if rows == 0:
                        continue
                    conflict_tracker.add_merged_schedule([alloc], venue.id, day, slot_idx, cache)
                    globally_scheduled_alloc_ids.add(alloc.id)
                    cache.mark_course_scheduled(alloc.course_code, lecturer_id, program_id, student_group_id)
                    placed_count += 1
                    assigned = True
                    scheduled_list.append(
                        f"{code} (Year {year}, {total_students} students) → {venue.code} "
                        f"(cap {venue.capacity}) [FINAL-SAFETY-NET/LECTURER-DOUBLE-BOOKED] "
                        f"({day} {start}-{end}) — this lecturer now teaches two classes at "
                        f"once; manual review required"
                    )
                    safe_print(f"[FinalSafetyNet] FORCED (lecturer collision): {scheduled_list[-1]}")

        # ── PASS C: ABSOLUTE LAST RESORT — any physically free venue,
        # program-year conflict relaxed too. This is what actually
        # satisfies "if there's a free venue anywhere, use it": Pass A
        # and Pass B both still refuse to place a course into a window
        # its own program-year cohort has already filled, even when the
        # venue itself is sitting completely empty at that exact
        # day/slot. That's the gap that let a course sit unplaced with
        # room_utilization near 0% — plenty of free venues, just none in
        # a window the cohort was "free" in.
        #
        # The ONLY things that stay hard here are venue double-booking
        # (find_efficient_venue + is_duplicate_entry both still check
        # it) and a hard LecturerBlockedSlot rule (checked explicitly via
        # has_lecturer_hard_block, since a LecturerBlockedSlot is an
        # explicit "never schedule this lecturer here" admin setting, not
        # a soft double-booking collision — it's meant to survive even the
        # absolute last resort). Program-year and lecturer DOUBLE-BOOKING
        # are both allowed so the course lands on the grid somewhere
        # rather than vanishing, and every entry placed this way is
        # tagged unmistakably for manual review/re-timetabling.
        if not assigned:
            for day in day_order:
                if assigned:
                    break
                for slot_idx, (start, end) in enumerate(slots):
                    if assigned:
                        break
                    if lecturer_id and conflict_tracker.has_lecturer_hard_block(lecturer_id, day, slot_idx):
                        continue
                    venue = venue_allocator.find_efficient_venue(
                        total_students, venues_asc, day, slot_idx, "best_fit"
                    )
                    if not venue:
                        continue
                    if cache.is_duplicate_entry(venue.id, day, start, end):
                        continue
                    collisions = []
                    if lecturer_id and conflict_tracker.has_lecturer_conflict(lecturer_id, day, slot_idx):
                        collisions.append("lecturer")
                    if program_id and conflict_tracker.has_program_conflict(
                            program_id, year, day, slot_idx, new_alloc=alloc):
                        collisions.append("program-year")
                    entry = TempTimetable(course_allocation=alloc, venue=venue, day=day,
                                           start_time=start, end_time=end)
                    rows = safe_bulk_create_timetable_entries([entry], cache)
                    if rows == 0:
                        continue
                    conflict_tracker.add_merged_schedule([alloc], venue.id, day, slot_idx, cache)
                    globally_scheduled_alloc_ids.add(alloc.id)
                    cache.mark_course_scheduled(alloc.course_code, lecturer_id, program_id, student_group_id)
                    placed_count += 1
                    assigned = True
                    collision_note = (
                        f"causes a {'/'.join(collisions)} clash" if collisions
                        else "no collision, just hadn't been reached yet"
                    )
                    scheduled_list.append(
                        f"{code} (Year {year}, {total_students} students) → {venue.code} "
                        f"(cap {venue.capacity}) [FINAL-SAFETY-NET/FORCED-OVERRIDE] "
                        f"({day} {start}-{end}) — {collision_note}; placed only because "
                        f"a physical venue was free — manual re-timetabling recommended"
                    )
                    safe_print(f"[FinalSafetyNet] FORCED (venue-only, {collision_note}): {scheduled_list[-1]}")

        if not assigned:
            unschedulable_list.append(
                f"{code} (Year {year}, {total_students} students) — every single venue "
                f"is double-booked in every day/slot window that exists; this is a "
                f"genuine total venue-supply shortage (more courses this run than "
                f"(venues × days × slots) can hold), not a rule the scheduler is "
                f"refusing to relax — add more venues/days/slots to fix it"
            )
            safe_print(f"[FinalSafetyNet] STILL UNPLACED (venue supply exhausted): {code}")

    safe_print(
        f"[FinalSafetyNet] Complete — placed={placed_count} | "
        f"still_unschedulable={len(unschedulable_list)}"
    )
    return placed_count, scheduled_list, unschedulable_list


def optimize_venue_assignments(days: List[str], slots: List[Tuple[dtime, dtime]]) -> Dict[str, int]:
    safe_print("\n" + "=" * 70)
    safe_print("PHASE 6: Venue Capacity Optimisation — re-matching rooms to class sizes")
    safe_print("=" * 70)

    # ── PHASE 5.5: Clean up orphaned entries with NULL venue_id ──────────────
    safe_print("\n[Cleanup] Checking for orphaned timetable entries with NULL venue_id…")
    try:
        orphaned = list(TempTimetable.objects.filter(venue_id__isnull=True))
        if orphaned:
            safe_print(f"[Cleanup] Found {len(orphaned)} orphaned entries with NULL venue_id")
            removed_count = 0
            for entry in orphaned:
                try:
                    # Attempt to find a viable venue for this entry
                    # (Venue is already imported at module level — do not
                    # re-import it locally here: doing so makes Python
                    # treat it as a local name for this ENTIRE function,
                    # which breaks every other use of Venue below whenever
                    # this branch doesn't happen to run first.)
                    viable_venues = Venue.objects.filter(
                        campus_id=entry.course_allocation.campus_id
                    ).exclude(
                        id__in=TempTimetable.objects.filter(
                            day=entry.day,
                            start_time=entry.start_time,
                            end_time=entry.end_time
                        ).values_list('venue_id', flat=True).distinct()
                    ).order_by('capacity')
                    
                    if viable_venues.exists():
                        # Assign to smallest viable venue
                        venue = viable_venues.first()
                        TempTimetable.objects.filter(id=entry.id).update(venue=venue)
                        safe_print(
                            f"[Cleanup] Recovered: {entry.course_allocation.course_code} "
                            f"@ {venue.code}"
                        )
                    else:
                        # No viable venue — delete orphaned entry
                        course_code = entry.course_allocation.course_code if entry.course_allocation else "UNKNOWN"
                        entry.delete()
                        removed_count += 1
                        safe_print(
                            f"[Cleanup] Deleted orphaned {course_code} "
                            f"({entry.day} {entry.start_time}–{entry.end_time}, no viable venue)"
                        )
                except Exception as e:
                    safe_print(f"[Cleanup] Error processing orphaned entry {entry.id}: {e}")
                    try:
                        entry.delete()
                        removed_count += 1
                    except:
                        pass
            safe_print(f"[Cleanup] Complete: removed={removed_count}")
        else:
            safe_print("[Cleanup] No orphaned entries found — database is clean")
    except Exception as exc:
        safe_print(f"[Cleanup] WARNING: {exc} — continuing anyway")
    safe_print("=" * 70)

    try:
        merged_alloc_ids: Set[int] = set(
            MergedCourseGroupTimetable.objects.values_list(
                'merged_courses__id', flat=True
            ).distinct()
        )
        safe_print(
            f"[VenueOpt] Merged-group alloc IDs loaded: {len(merged_alloc_ids)} "
            f"(exempted from swapping)"
        )
    except Exception as exc:
        safe_print(f"[VenueOpt] ERROR loading merged alloc IDs: {exc} — aborting")
        return {'timeslots_examined': 0, 'timeslots_optimised': 0,
                'swaps_made': 0, 'errors': 1}

    # ── Specialization / exclusivity / hard-block guards ────────────────────
    #
    # Phase 6 previously had NO awareness of VenueSpecialization or
    # VenueBlock at all — it pulled every occupied venue AND every venue
    # sitting empty at a slot straight into the candidate pool, including
    # Law-only exclusive venues and hard-blocked venues, and could even use
    # one of them as a temporary "parking" spot mid-swap. That's what let
    # a dedicated/blocked venue get handed to an unrelated course, and let
    # a course that legitimately belongs in its dedicated venue get swapped
    # away from it.
    #
    #   blocked_venue_ids    : hard-blocked (VenueBlock) — never usable at all
    #   exclusive_venue_ids  : belongs to an exclusive=True specialization
    #                          rule (e.g. Law) — never offered to any other
    #                          course, occupied or empty, ever
    #   course_to_venues /
    #   specialization_venue_ids : full designated-course → venue map
    #                              (soft + exclusive), used to work out which
    #                              individual entries are "home" in their
    #                              current venue and must not be moved
    try:
        blocked_venue_ids = constraint_engine.get_blocked_venue_ids(scheduler_type="regular")
        exclusive_venue_ids = constraint_engine.get_exclusive_venue_ids(scheduler_type="regular")
        course_to_venues, specialization_venue_ids = build_specialization_index()
        safe_print(
            f"[VenueOpt] Guards loaded: {len(blocked_venue_ids)} hard-blocked venues, "
            f"{len(exclusive_venue_ids)} exclusive venues, "
            f"{len(specialization_venue_ids)} specialized venues total"
        )
    except Exception as exc:
        safe_print(f"[VenueOpt] WARNING: could not load venue guards ({exc}) — "
                    f"proceeding without specialization/block protection")
        blocked_venue_ids = set()
        exclusive_venue_ids = set()
        course_to_venues = {}
        specialization_venue_ids = set()

    off_limits_venue_ids = blocked_venue_ids | exclusive_venue_ids

    def _entry_home_venue_ids(entry) -> Set[int]:
        """Venue IDs this entry's course is specifically designated to."""
        alloc = entry.course_allocation
        if not alloc or not course_to_venues:
            return set()
        norm = normalize_course_code(alloc.course_code)
        prog_id = alloc.program_id
        candidates = course_to_venues.get((norm, prog_id), []) + course_to_venues.get((norm, None), [])
        return {v.id for v, _rule in candidates}

    timeslots_examined = 0
    timeslots_optimised = 0
    swaps_made = 0
    errors = 0
    double_bookings: List[Dict] = []

    for day in days:
        for slot_start, slot_end in slots:
            timeslots_examined += 1

            try:
                slot_entries = list(
                    TempTimetable.objects.select_related(
                        'course_allocation', 'venue'
                    ).filter(
                        day=day,
                        start_time=slot_start,
                        end_time=slot_end,
                    )
                )

                if not slot_entries:
                    continue

                # Report (but don't yet act on) any venue already shared by
                # two different course allocations this slot — a straight
                # data conflict from elsewhere. The reassignment logic below
                # will usually resolve it as a side-effect (both entries end
                # up as separate `eligible` rows competing for the same
                # venue_pool key, so at most one keeps it and the rest get
                # reassigned) but we still want it visible either way.
                by_venue: Dict[int, list] = defaultdict(list)
                for e in slot_entries:
                    if e.venue_id:
                        by_venue[e.venue_id].append(e)
                for vid, group in by_venue.items():
                    distinct_allocs = {g.course_allocation_id for g in group if g.course_allocation_id}
                    if len(distinct_allocs) > 1:
                        double_bookings.append({
                            'day': day,
                            'slot': f'{slot_start}–{slot_end}',
                            'venue': group[0].venue.code if group[0].venue else vid,
                            'courses': sorted({
                                (g.course_allocation.course_code or '?').strip().upper()
                                for g in group if g.course_allocation
                            }),
                        })

                # Entries "at home": their current venue is one of THEIR
                # OWN designated venues (whether that rule is exclusive or
                # soft). These never move — mirrors the manual swap panel's
                # per-cell lock — and are excluded from `eligible` the same
                # way merged-group entries already are.
                locked_ids: Set[int] = {
                    e.id for e in slot_entries
                    if e.venue_id and e.venue_id in _entry_home_venue_ids(e)
                }

                eligible = [
                    e for e in slot_entries
                    if e.course_allocation_id not in merged_alloc_ids
                    and e.id not in locked_ids
                ]

                if len(eligible) < 2:
                    continue

                # Candidate venues for this slot = venues eligible courses
                # already occupy, PLUS any venue that is sitting completely
                # empty at this exact day/slot — EXCLUDING hard-blocked and
                # exclusive (e.g. Law-only) venues, which must never be
                # offered to anyone else, occupied or not.
                occupied_by_anyone: Set[int] = {
                    e.venue_id for e in slot_entries if e.venue_id
                }
                empty_at_this_slot = list(
                    Venue.objects
                    .exclude(id__in=occupied_by_anyone)
                    .exclude(id__in=off_limits_venue_ids)
                )

                venue_pool: Dict[int, 'Venue'] = {
                    e.venue_id: e.venue
                    for e in eligible
                    if e.venue is not None and e.venue_id not in off_limits_venue_ids
                }
                for v in empty_at_this_slot:
                    venue_pool[v.id] = v

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

                # Build a safe 1-to-1 assignment: courses sorted largest-first,
                # venues sorted largest-first (now including empty venues at
                # this slot, not just already-occupied ones). Only assign as
                # many courses as there are distinct venues to avoid reuse
                # within this slot. Courses beyond the venue count keep their
                # current venue.
                optimal_assignments = []
                for i, entry in enumerate(eligible_sorted):
                    if i < n_venues:
                        optimal_assignments.append(venues_sorted[i])
                    else:
                        # No venue left — keep current assignment (no swap)
                        optimal_assignments.append(entry.venue)

                needs_update = any(
                    entry.venue_id != venue.id
                    for entry, venue in zip(eligible_sorted, optimal_assignments)
                )

                if not needs_update:
                    continue

                # Safe swap without ever writing NULL into venue_id (that
                # column is NOT NULL — the previous 'set to NULL, then set
                # final value' approach violated that on every single
                # timeslot, which is why Phase 6 always logged
                # "Column 'venue_id' cannot be null" and made zero swaps).
                #
                # Instead: reassignments here are always a permutation of
                # currently-occupied venues at this exact day/time, so a
                # direct entry-by-entry update can collide with a row that
                # hasn't been moved yet. We break the permutation into
                # cycles and, for each cycle, park the first entry on a
                # venue that is genuinely free at this day/time (never used
                # anywhere in this slot) instead of NULL — then walk the
                # rest of the cycle normally, and finally move the parked
                # entry into the slot vacated by the last step. If no truly
                # free venue exists at this slot, the swap is skipped
                # (logged) rather than attempted unsafely.
                with transaction.atomic():
                    changing = {
                        entry.id: (entry, new_venue)
                        for entry, new_venue in zip(eligible_sorted, optimal_assignments)
                        if entry.venue_id != new_venue.id
                    }

                    if changing:
                        occupied_venue_ids = {e.venue_id for e in slot_entries if e.venue_id}
                        parking_venue = (
                            Venue.objects.exclude(id__in=occupied_venue_ids)
                            .exclude(id__in=off_limits_venue_ids)
                            .order_by('id')
                            .first()
                        )

                        if parking_venue is None:
                            safe_print(
                                f"[VenueOpt] SKIP {day} {slot_start}–{slot_end}: "
                                f"no free venue available to use as a swap park spot"
                            )
                        else:
                            # old_venue_id -> entry currently sitting there
                            entry_by_current_venue = {
                                entry.venue_id: entry for entry, _ in changing.values()
                            }
                            visited: Set[int] = set()

                            for start_id, (start_entry, _) in list(changing.items()):
                                if start_id in visited:
                                    continue
                                # Walk the cycle starting at start_entry.
                                cycle = []
                                cur = start_entry
                                while cur.id not in visited:
                                    visited.add(cur.id)
                                    cycle.append(cur)
                                    _, next_target_venue = changing[cur.id]
                                    nxt = entry_by_current_venue.get(next_target_venue.id)
                                    if nxt is None or nxt.id not in changing:
                                        break
                                    cur = nxt

                                if len(cycle) == 1:
                                    only_entry, only_target = changing[cycle[0].id]
                                    TempTimetable.objects.filter(id=only_entry.id).update(
                                        venue=only_target
                                    )
                                    only_entry.venue = only_target
                                    only_entry.venue_id = only_target.id
                                    swaps_made += 1
                                    continue

                                # Park the first entry's original venue slot
                                # by moving it to the free parking venue.
                                first_entry = cycle[0]
                                TempTimetable.objects.filter(id=first_entry.id).update(
                                    venue=parking_venue
                                )
                                # Walk the rest of the cycle in REVERSE.
                                #
                                # `cycle` was built by following "whoever is
                                # currently sitting in my target venue" —
                                # cycle[1]'s target venue is where cycle[2]
                                # currently sits, cycle[2]'s target is where
                                # cycle[3] sits, and so on, until the last
                                # entry's target is the venue we just parked
                                # (first_entry's old spot).
                                #
                                # That's the right order to *discover* the
                                # cycle, but moving entries in that same
                                # (forward) order means cycle[1] gets moved
                                # into a venue cycle[2] is still occupying —
                                # an immediate uniqueness-constraint crash,
                                # every time, for any cycle longer than 2.
                                # Processing in reverse means each entry
                                # always moves into a venue the entry after
                                # it in the cycle has *already* vacated:
                                # the last entry (whose target is the
                                # parked venue's original occupant slot)
                                # goes first, freeing the venue the
                                # second-to-last entry needs, and so on
                                # back up to cycle[1].
                                for entry in reversed(cycle[1:]):
                                    _, target_venue = changing[entry.id]
                                    TempTimetable.objects.filter(id=entry.id).update(
                                        venue=target_venue
                                    )
                                    entry.venue = target_venue
                                    entry.venue_id = target_venue.id
                                    swaps_made += 1
                                # Finally move the first (parked) entry to
                                # its real target, now vacated.
                                _, first_target = changing[first_entry.id]
                                TempTimetable.objects.filter(id=first_entry.id).update(
                                    venue=first_target
                                )
                                first_entry.venue = first_target
                                first_entry.venue_id = first_target.id
                                swaps_made += 1

                timeslots_optimised += 1

                if timeslots_optimised <= 10 or timeslots_optimised % 50 == 0:
                    safe_print(
                        f"[VenueOpt] Optimised {day} {slot_start}–{slot_end}: "
                        f"{len(eligible_sorted)} entries re-matched across "
                        f"{n_venues} venues"
                    )

            except Exception as exc:
                errors += 1
                safe_print(f"[VenueOpt] ERROR processing {day} {slot_start}–{slot_end}: {exc}")
                continue

    safe_print(
        f"[VenueOpt] Complete — examined={timeslots_examined} | "
        f"optimised={timeslots_optimised} | swaps={swaps_made} | errors={errors} | "
        f"double_bookings_seen={len(double_bookings)}"
    )
    if double_bookings:
        safe_print(f"[VenueOpt] Double-bookings detected (see returned dict for full list):")
        for db in double_bookings[:20]:
            safe_print(f"    {db['venue']} {db['day']} {db['slot']} — {db['courses']}")
    safe_print("=" * 70)

    return {
        'timeslots_examined': timeslots_examined,
        'timeslots_optimised': timeslots_optimised,
        'swaps_made': swaps_made,
        'errors': errors,
        'double_bookings': double_bookings,
    }


def rebalance_oversized_venue_assignments(
    days: List[str],
    slots: List[Tuple[dtime, dtime]],
    min_utilisation: float = 0.35,
    min_wasted_seats: int = 20,
) -> Dict[str, int]:
    """
    PHASE 6B — Cross-timeslot venue rebalancing (fixes lone-oversized-room
    cases Phase 6 can't touch).

    optimize_venue_assignments (Phase 6) only re-matches courses to venues
    AMONG the courses already competing for the exact same (day, slot). If a
    4-student class is the ONLY thing scheduled in a 100-capacity venue at
    that slot — nobody else booked that slot to swap with — Phase 6 has
    nothing to work with and leaves the mismatch exactly as it is. This pass
    looks ACROSS every other day/slot for a genuinely better-fitting,
    conflict-free venue and physically relocates the course there, freeing
    the oversized room instead of quietly wasting it.

    Handling of 0 / NULL number_of_students (updated per user request — no
    longer given automatic protection): a 0/NULL count is treated exactly
    like any other count value here, using `alloc.number_of_students or 0`.
    A 0/NULL course is real data uncertainty — it may or may not actually
    have students — so it no longer gets blanket immunity from relocation
    just because the number is missing. It is evaluated by the same
    utilisation/wasted-seats test as everything else and, since
    `0 / capacity == 0` utilisation, a 0/NULL course sitting alone in a big
    room will now normally qualify as a relocation candidate too, exactly
    like a known small class would. It moves through the same best-fit
    candidate-venue search as any other entry — no reserved treatment,
    no exemption.

    This pass is intentionally ONE-DIRECTIONAL (relocate the small class
    away) rather than a full two-way swap: a two-way swap across two
    different days/times has to re-validate hard constraints for BOTH
    entries in both directions, which is a much larger surface for
    accidentally introducing a new collision. A one-directional move only
    ever has to prove the ONE course being moved is conflict-free at its new
    (day, slot, venue) — everything else on the grid is left untouched — so
    it can never make an already-valid timetable invalid.

    Safety rules, all hard (never traded away for a better capacity fit):
      - the course's lecturer (if any) must have no other class at the
        destination (day, slot)
      - the destination (day, slot) must have no program-year collision for
        this course's program/year (same collision-exemption rules used
        everywhere else in this file — see is_program_year_collision_exempt)
      - the destination venue must be genuinely free at that (day, slot)
      - merged-group entries are never touched
      - a course already sitting in one of ITS OWN designated/specialized
        venues is never moved out of it
      - blocked/exclusive venues are never offered as a destination
    """
    stats = {'entries_scanned': 0, 'oversized_candidates': 0, 'relocated': 0, 'errors': 0}

    safe_print("\n" + "=" * 70)
    safe_print("PHASE 6B: Cross-Timeslot Venue Rebalancing — freeing oversized rooms")
    safe_print("=" * 70)

    try:
        merged_alloc_ids: Set[int] = set(
            MergedCourseGroupTimetable.objects.values_list('merged_courses__id', flat=True).distinct()
        )
    except Exception:
        merged_alloc_ids = set()

    try:
        blocked_venue_ids = constraint_engine.get_blocked_venue_ids(scheduler_type="regular")
        exclusive_venue_ids = constraint_engine.get_exclusive_venue_ids(scheduler_type="regular")
        course_to_venues, _specialization_venue_ids = build_specialization_index()
    except Exception as exc:
        safe_print(
            f"[VenueRebalance] WARNING: could not load venue guards ({exc}) — "
            f"proceeding without specialization/block protection"
        )
        blocked_venue_ids = set()
        exclusive_venue_ids = set()
        course_to_venues = {}

    off_limits_venue_ids = blocked_venue_ids | exclusive_venue_ids

    def _entry_home_venue_ids(alloc) -> Set[int]:
        if not alloc or not course_to_venues:
            return set()
        norm = normalize_course_code(alloc.course_code)
        candidates = (
            course_to_venues.get((norm, alloc.program_id), [])
            + course_to_venues.get((norm, None), [])
        )
        return {v.id for v, _rule in candidates}

    def _safe_year(alloc) -> int:
        try:
            return get_course_year(alloc)
        except Exception:
            return 1

    all_entries = list(
        TempTimetable.objects.select_related(
            'course_allocation', 'course_allocation__lecturer',
            'course_allocation__program', 'venue'
        ).all()
    )
    all_venues = list(Venue.objects.all())

    # ── Occupancy indexes for fast, direct conflict checks ──────────────────
    venue_busy: Set[Tuple[int, str, dtime, dtime]] = set()
    lecturer_busy: Set[Tuple[int, str, dtime, dtime]] = set()
    program_year_slot_allocs: Dict[Tuple, List] = defaultdict(list)

    for e in all_entries:
        if e.venue_id:
            venue_busy.add((e.venue_id, e.day, e.start_time, e.end_time))
        alloc = e.course_allocation
        if not alloc:
            continue
        if alloc.lecturer_id:
            lecturer_busy.add((alloc.lecturer_id, e.day, e.start_time, e.end_time))
        if alloc.program_id:
            program_year_slot_allocs[
                (alloc.program_id, _safe_year(alloc), e.day, e.start_time, e.end_time)
            ].append(alloc)

    def _lecturer_free(lecturer_id, day, start, end) -> bool:
        return not lecturer_id or (lecturer_id, day, start, end) not in lecturer_busy

    def _program_year_free(program_id, year, day, start, end, new_alloc) -> bool:
        if not program_id:
            return True
        for existing_alloc in program_year_slot_allocs.get((program_id, year, day, start, end), []):
            if not is_program_year_collision_exempt(new_alloc, existing_alloc):
                return False
        return True

    def _venue_free(venue_id, day, start, end) -> bool:
        return (venue_id, day, start, end) not in venue_busy

    relocations = []

    for e in all_entries:
        stats['entries_scanned'] += 1
        alloc = e.course_allocation
        if not alloc or not e.venue:
            continue
        if alloc.id in merged_alloc_ids:
            continue
        if e.venue_id in _entry_home_venue_ids(alloc):
            continue  # never move a course out of its own designated venue

        # 0/NULL is no longer a protected special case — treated as 0,
        # same as any other known count would be treated at that value.
        students = alloc.number_of_students or 0

        capacity = e.venue.capacity or 0
        if capacity <= 0:
            continue

        utilisation = students / capacity
        wasted = capacity - students
        if utilisation >= min_utilisation or wasted < min_wasted_seats:
            continue  # not meaningfully oversized — leave it exactly where it is

        stats['oversized_candidates'] += 1

        # Best-fit destination candidates: smaller than the current venue,
        # but still large enough to actually hold this class.
        candidate_venues = sorted(
            (v for v in all_venues
             if (v.capacity or 0) >= students
             and (v.capacity or 0) < capacity
             and v.id not in off_limits_venue_ids),
            key=lambda v: v.capacity or 0,
        )
        if not candidate_venues:
            continue

        lecturer_id = alloc.lecturer_id
        program_id = alloc.program_id
        year = _safe_year(alloc)

        best_move = None
        for day in days:
            for start, end in slots:
                if day == e.day and start == e.start_time and end == e.end_time:
                    continue  # same slot is Phase 6's job, not this pass's
                if not _lecturer_free(lecturer_id, day, start, end):
                    continue
                if not _program_year_free(program_id, year, day, start, end, alloc):
                    continue
                for v in candidate_venues:
                    if not _venue_free(v.id, day, start, end):
                        continue
                    best_move = (v, day, start, end)
                    break
                if best_move:
                    break
            if best_move:
                break

        if best_move:
            new_venue, new_day, new_start, new_end = best_move

            # ── Reserve the destination THE INSTANT it's chosen ────────────
            # This is the fix: previously venue_busy/lecturer_busy/
            # program_year_slot_allocs were only updated in a *separate*
            # apply loop that ran after every entry had already picked its
            # best_move against one frozen snapshot. That let dozens of
            # different oversized entries independently "discover" the same
            # empty (venue, day, slot) as free and all pick it, since none
            # of them could see each other's in-progress picks. Reserving
            # right here — inside the same scan loop, before the next
            # entry's search runs — makes each pick immediately visible to
            # every subsequent _venue_free / _lecturer_free /
            # _program_year_free check, so two entries can never land on the
            # same destination.
            venue_busy.add((new_venue.id, new_day, new_start, new_end))
            if lecturer_id:
                lecturer_busy.add((lecturer_id, new_day, new_start, new_end))
            if program_id:
                program_year_slot_allocs[
                    (program_id, year, new_day, new_start, new_end)
                ].append(alloc)

            relocations.append((e, alloc, best_move))

    # ── Apply relocations (persist to DB). The in-memory indexes were
    #    already reserved at selection time above, so this loop only needs
    #    to write the DB rows and free up each entry's OLD slot (which is
    #    genuinely vacated once the move is committed) — it must NOT re-add
    #    the new slot, since that was already reserved during the scan and
    #    doing it twice was never the bug, but is redundant and confusing. ──
    for entry, alloc, (new_venue, new_day, new_start, new_end) in relocations:
        old_venue = entry.venue
        old_day, old_start, old_end = entry.day, entry.start_time, entry.end_time
        try:
            with transaction.atomic():
                TempTimetable.objects.filter(id=entry.id).update(
                    venue=new_venue, day=new_day, start_time=new_start, end_time=new_end
                )

            venue_busy.discard((old_venue.id, old_day, old_start, old_end))
            if alloc.lecturer_id:
                lecturer_busy.discard((alloc.lecturer_id, old_day, old_start, old_end))
            if alloc.program_id:
                year = _safe_year(alloc)
                old_bucket = program_year_slot_allocs.get(
                    (alloc.program_id, year, old_day, old_start, old_end), []
                )
                if alloc in old_bucket:
                    old_bucket.remove(alloc)

            stats['relocated'] += 1
            safe_print(
                f"[VenueRebalance] MOVED: {alloc.course_code} "
                f"({alloc.number_of_students if alloc.number_of_students is not None else '0/NULL'} students) "
                f"{old_venue.code} [{old_venue.capacity}] ({old_day} {old_start}-{old_end}) → "
                f"{new_venue.code} [{new_venue.capacity}] ({new_day} {new_start}-{new_end}) "
                f"— freed a {old_venue.capacity}-capacity room"
            )
        except Exception as exc:
            stats['errors'] += 1
            safe_print(f"[VenueRebalance] ERROR relocating entry {entry.id}: {exc}")

    safe_print(
        f"[VenueRebalance] Complete — scanned={stats['entries_scanned']} | "
        f"oversized_candidates={stats['oversized_candidates']} | "
        f"relocated={stats['relocated']} | errors={stats['errors']}"
    )
    safe_print("=" * 70)

    return stats


def process_post_schedule_lecturer_course_consolidation(
    days: List[str],
    slots: List[Tuple[dtime, dtime]],
    merge_limit: int = 200,
) -> Dict[str, int]:
    """
    PHASE 6B2 — POST-SCHEDULE SAME-LECTURER/SAME-COURSE CONSOLIDATION.

    STEP 1 groups same-course/same-lecturer sections into merge bins BEFORE
    scheduling starts (build_global_merged_tasks + _pack_into_merge_bins).
    But process_exhaustive_sweep is allowed to DISSOLVE a merge bin back
    into smaller pieces when no single collision-free venue is free for the
    whole bin at the time it's tried — each dissolved piece then gets
    scheduled independently and can land on a completely different
    day/time. The result actually seen in production: one lecturer's ECON
    313 sections — grouped by STEP 1 into 2 planned bins ({70,53,35}=158,
    {15,5}=20) — came out the other end scattered across THREE separate
    rooms/timeslots instead, because the sweep couldn't find one free venue
    for the 158-student bin exactly when it looked. That's a wasted
    room+timeslot the university didn't need to spend.

    This pass runs LATE, after Phase 6B has already relocated small classes
    out of oversized rooms and genuinely freed some bigger venues up, and
    looks across the WHOLE finished timetable for exactly this pattern: the
    same normalised base course code taught by the same lecturer sitting in
    more than one distinct (day, slot). For each such lecturer+course
    family, it re-runs the same best-fit-decreasing bin-packing STEP 1
    already uses (_pack_into_merge_bins) — but on the CLUSTERS actually on
    the grid, not the raw allocations — and, for every bin that packs down
    to fewer clusters than currently exist, physically moves the smaller
    cluster(s) into the largest cluster's (day, slot), upgrading to a
    bigger free venue at that same slot if the current one can't hold the
    combined total (soft — capacity overflow is still allowed as an
    absolute last resort exactly like everywhere else in this file, tagged
    for review, never silently dropped).

    Two clusters sharing the same normalised base code + lecturer are, by
    definition, never a program-year collision with each other — they are
    one class taught jointly, exactly like a STEP 1 merge (see
    build_global_merged_tasks/is_program_year_collision_exempt's own
    reasoning). The only thing ever checked before folding one cluster into
    another is whether some genuinely UNRELATED course already sitting at
    the destination slot/venue would collide with the incoming cluster's
    program/year — that check is never skipped or relaxed.

    A whole merged-group cluster (multiple course_allocations already
    sharing one TempTimetable slot+venue from an earlier merge) is always
    moved as one atomic unit, so this pass can never split a merge apart —
    only fold separate clusters together.

    Every (day, slot, venue) fully vacated by a fold is reported so a
    later rescue pass can offer that freed capacity to courses still
    unplaced.
    """
    stats = {
        'families_scanned': 0, 'families_consolidated': 0,
        'clusters_folded': 0, 'slots_freed': 0, 'venue_upgrades': 0, 'errors': 0,
    }

    safe_print("\n" + "=" * 70)
    safe_print("PHASE 6B2: Post-Schedule Same-Lecturer/Same-Course Consolidation")
    safe_print("=" * 70)

    all_entries = list(
        TempTimetable.objects.select_related(
            'course_allocation', 'course_allocation__lecturer', 'venue'
        ).all()
    )
    all_venues = list(Venue.objects.all())

    try:
        blocked_venue_ids = constraint_engine.get_blocked_venue_ids(scheduler_type="regular")
        exclusive_venue_ids = constraint_engine.get_exclusive_venue_ids(scheduler_type="regular")
        course_to_venues, _specialization_venue_ids = build_specialization_index()
    except Exception:
        blocked_venue_ids = set()
        exclusive_venue_ids = set()
        course_to_venues = {}
    off_limits_venue_ids = blocked_venue_ids | exclusive_venue_ids

    def _entry_home_venue_ids(alloc) -> Set[int]:
        """Venue IDs this alloc's course is specifically designated to —
        mirrors the same lock used by optimize_venue_assignments /
        rebalance_oversized_venue_assignments. A cluster currently sitting
        in one of its own designated venues must never be folded away from
        it by this pass, exclusive or not: doing so silently vacates a
        reserved venue, which is exactly what then lets Phase 6C or any
        later pass hand it to an unrelated course."""
        if not alloc or not course_to_venues:
            return set()
        norm = normalize_course_code(alloc.course_code)
        candidates = (
            course_to_venues.get((norm, alloc.program_id), [])
            + course_to_venues.get((norm, None), [])
        )
        return {v.id for v, _rule in candidates}

    # ── Global occupancy: (venue_id, day, start, end) -> list of allocs
    # currently sitting there, used ONLY to detect genuinely unrelated
    # courses at a prospective destination. ─────────────────────────────────
    slot_occupants: Dict[Tuple[int, str, dtime, dtime], List] = defaultdict(list)
    venue_busy: Set[Tuple[int, str, dtime, dtime]] = set()
    for e in all_entries:
        if not e.course_allocation or not e.venue:
            continue
        slot_occupants[(e.venue_id, e.day, e.start_time, e.end_time)].append(e.course_allocation)
        venue_busy.add((e.venue_id, e.day, e.start_time, e.end_time))

    # ── Group entries into (norm_code, lecturer_id) families, then into
    # clusters keyed by the exact (day, start, end, venue_id) they occupy. ──
    families: Dict[Tuple[str, int], Dict[Tuple[str, dtime, dtime, int], List]] = defaultdict(
        lambda: defaultdict(list)
    )
    for e in all_entries:
        alloc = e.course_allocation
        if not alloc or not alloc.lecturer_id or not e.venue:
            continue
        norm = normalize_course_code_base(alloc.course_code)
        fam_key = (norm, alloc.lecturer_id)
        cluster_key = (e.day, e.start_time, e.end_time, e.venue_id)
        families[fam_key][cluster_key].append(e)

    for fam_key, cluster_map in families.items():
        if len(cluster_map) < 2:
            continue  # already one slot — nothing to consolidate
        stats['families_scanned'] += 1
        norm_code, lecturer_id = fam_key

        clusters = []  # [{'key':..., 'entries':[...], 'students': int}]
        for cluster_key, entries in cluster_map.items():
            students = sum((e.course_allocation.number_of_students or 0) for e in entries)
            clusters.append({'key': cluster_key, 'entries': entries, 'students': students})

        # ── Best-fit-decreasing repack of CLUSTERS (mirrors
        # _pack_into_merge_bins, but the "items" are already-scheduled
        # clusters rather than raw allocations). ──────────────────────────
        clusters_sorted = sorted(clusters, key=lambda c: c['students'], reverse=True)
        bins: List[List[dict]] = []
        bin_totals: List[int] = []
        for c in clusters_sorted:
            best_idx, best_remaining = -1, None
            for i, total in enumerate(bin_totals):
                remaining = merge_limit - (total + c['students'])
                if remaining >= 0 and (best_remaining is None or remaining < best_remaining):
                    best_idx, best_remaining = i, remaining
            if best_idx >= 0:
                bins[best_idx].append(c)
                bin_totals[best_idx] += c['students']
            else:
                bins.append([c])
                bin_totals.append(c['students'])

        for b_idx, bin_clusters in enumerate(bins):
            if len(bin_clusters) < 2:
                continue  # this bin didn't consolidate anything

            # Anchor must itself be a usable (non-blocked, non-exclusive)
            # venue — previously this picked the largest cluster's venue
            # unconditionally, so a bin whose biggest cluster happened to
            # already be sitting in an off-limits venue would fold every
            # other cluster INTO that off-limits room instead of away from
            # it. Restrict anchor candidates to clusters already sitting in
            # an allowed venue; only their own capacity/upgrade logic below
            # ever decides the final venue.
            anchor_candidates = [
                c for c in bin_clusters if c['key'][3] not in off_limits_venue_ids
            ]
            if not anchor_candidates:
                safe_print(
                    f"[Consolidation] SKIPPED bin for {norm_code} (lecturer {lecturer_id}): "
                    f"every cluster in this bin sits in an off-limits venue — nothing safe "
                    f"to anchor on"
                )
                continue

            anchor = max(anchor_candidates, key=lambda c: c['students'])

            # Any OTHER cluster in this bin that is itself sitting "at
            # home" (its venue is one of ITS OWN alloc's designated
            # specialization venues) must never be folded away from it —
            # same rule as the anchor guard above, just applied to every
            # cluster instead of only the anchor. Previously only the
            # anchor was protected, so a scattered designated course could
            # still have its non-anchor cluster silently emptied out of a
            # reserved venue by this pass.
            others_all = [c for c in bin_clusters if c is not anchor]
            others = []
            for c in others_all:
                home_ids = _entry_home_venue_ids(c['entries'][0].course_allocation) if c['entries'] else set()
                if c['key'][3] in home_ids:
                    safe_print(
                        f"[Consolidation] SKIPPED cluster for {norm_code} (lecturer {lecturer_id}) "
                        f"at {c['key']}: sits in its own designated venue — never folded away from it"
                    )
                    continue
                others.append(c)
            if not others:
                continue  # nothing left in this bin that's safe to fold

            a_day, a_start, a_end, a_venue_id = anchor['key']
            bin_total = anchor['students'] + sum(c['students'] for c in others)

            anchor_venue = next((v for v in all_venues if v.id == a_venue_id), None)
            if anchor_venue is None:
                continue
            final_venue = anchor_venue

            if (anchor_venue.capacity or 0) < bin_total:
                # try to upgrade to a bigger free venue at the SAME slot
                bigger = sorted(
                    (v for v in all_venues
                     if (v.capacity or 0) >= bin_total
                     and v.id not in off_limits_venue_ids
                     and (v.id, a_day, a_start, a_end) not in venue_busy),
                    key=lambda v: v.capacity or 0,
                )
                if bigger:
                    final_venue = bigger[0]

            # ── Verify every "other" cluster is safe to fold in: only
            # genuinely unrelated occupants at the destination matter — our
            # own family's allocs never collide with each other. ──────────
            fold_ok = True
            dest_key = (final_venue.id, a_day, a_start, a_end)
            existing_dest_others = [
                al for al in slot_occupants.get(dest_key, [])
                if (normalize_course_code_base(al.course_code), al.lecturer_id) != fam_key
            ]
            for c in others:
                for e in c['entries']:
                    alloc = e.course_allocation
                    for other_alloc in existing_dest_others:
                        if not is_program_year_collision_exempt(alloc, other_alloc):
                            fold_ok = False
                            break
                    if not fold_ok:
                        break
                if not fold_ok:
                    break
            if not fold_ok:
                safe_print(
                    f"[Consolidation] SKIPPED bin for {norm_code} (lecturer {lecturer_id}): "
                    f"destination slot has an unrelated program-year clash"
                )
                continue

            # ── Apply: move anchor (if venue upgraded) and every other
            # cluster's entries onto (a_day, a_start, a_end, final_venue). ──
            try:
                with transaction.atomic():
                    if final_venue.id != anchor_venue.id:
                        anchor_ids = [e.id for e in anchor['entries']]
                        TempTimetable.objects.filter(id__in=anchor_ids).update(venue=final_venue)
                        stats['venue_upgrades'] += 1
                        venue_busy.discard((anchor_venue.id, a_day, a_start, a_end))
                        venue_busy.add((final_venue.id, a_day, a_start, a_end))

                    for c in others:
                        old_key = c['key']
                        entry_ids = [e.id for e in c['entries']]
                        TempTimetable.objects.filter(id__in=entry_ids).update(
                            venue=final_venue, day=a_day, start_time=a_start, end_time=a_end,
                        )
                        old_venue_id = old_key[3]
                        venue_busy.discard((old_venue_id, old_key[0], old_key[1], old_key[2]))
                        stats['clusters_folded'] += 1
                        stats['slots_freed'] += 1
                        old_venue_obj = next((v for v in all_venues if v.id == old_venue_id), None)
                        old_venue_code = old_venue_obj.code if old_venue_obj else old_venue_id
                        safe_print(
                            f"[Consolidation] FOLDED: {norm_code} ({c['students']} students) "
                            f"{old_venue_code} ({old_key[0]} {old_key[1]}-{old_key[2]}) → "
                            f"{final_venue.code} ({a_day} {a_start}-{a_end}) — freed "
                            f"{old_venue_code} at that slot for another course"
                        )

                stats['families_consolidated'] += 1
            except Exception as exc:
                stats['errors'] += 1
                safe_print(
                    f"[Consolidation] ERROR consolidating {norm_code} "
                    f"(lecturer {lecturer_id}), bin {b_idx}: {exc}"
                )

    safe_print(
        f"[Consolidation] Complete — families_scanned={stats['families_scanned']} | "
        f"families_consolidated={stats['families_consolidated']} | "
        f"clusters_folded={stats['clusters_folded']} | "
        f"slots_freed={stats['slots_freed']} | "
        f"venue_upgrades={stats['venue_upgrades']} | errors={stats['errors']}"
    )
    safe_print("=" * 70)

    return stats


def process_post_schedule_collision_swap(
    days: List[str],
    slots: List[Tuple[dtime, dtime]],
) -> Dict[str, int]:
    """
    FINAL PASS — POST-SCHEDULE PROGRAM-YEAR COLLISION SWAP.

    Runs dead last, after every placement pass AND Phase 6 / 6B venue
    optimisation, directly against the final TempTimetable state — mirrors
    optimize_venue_assignments / rebalance_oversized_venue_assignments's
    convention of re-reading the DB fresh rather than trusting the
    in-memory conflict_tracker, which those two phases have already moved
    entries behind.

    Every genuine program-year collision left on the timetable at this
    point is the residue of a FORCED pass (SiblingColocation Pass B,
    OverloadRelief Pass B, or FinalSafetyNet Pass B/C) that had to relax
    the program-year check because nothing else was free AT THE TIME it
    ran. Now that the entire grid has been filled in — including Phase 6B
    freeing up rooms that were previously sitting occupied — a genuinely
    clean slot may exist for one side of the collision, or another course
    belonging to a different program/year may be sitting somewhere that
    would happily trade places with it.

    For every colliding entry:
      PASS A — MOVE ONLY: relocate it to any day/slot/venue where its own
               lecturer, its own program-year, and the venue are all
               simultaneously free. Cheapest and safest — touches only the
               one entry being moved.
      PASS B — TWO-WAY SWAP: if no clean empty destination exists, look for
               another scheduled, non-merged entry Y belonging to a
               DIFFERENT program/year such that the colliding entry would
               be clean at Y's (day, slot, venue) AND Y would be clean at
               the colliding entry's current (day, slot, venue) — then
               exchange their day/time/venue. This can never introduce a
               NEW venue double-booking: each vacated (venue, day, slot) is
               immediately refilled by the other party and nothing else
               touches it.

    Merged-group entries (MergedCourseGroupTimetable) are never touched —
    moving or swapping only one allocation out of a merged group would
    silently split it across two different times/rooms.
    """
    stats = {
        'collisions_found': 0, 'resolved_by_move': 0, 'resolved_by_swap': 0,
        'unresolved': 0, 'errors': 0,
    }

    safe_print("\n" + "=" * 70)
    safe_print("FINAL PASS: Post-Schedule Program-Year Collision Swap")
    safe_print("=" * 70)

    try:
        merged_alloc_ids: Set[int] = set(
            MergedCourseGroupTimetable.objects.values_list('merged_courses__id', flat=True).distinct()
        )
    except Exception:
        merged_alloc_ids = set()

    # ── Blocked / exclusive / home-venue guard ──────────────────────────────
    # This pass previously had NO awareness of VenueBlock or exclusive
    # VenueSpecialization at all — `all_venues` was pulled straight from
    # Venue.objects.all() with nothing excluded, and PASS B swapped any two
    # entries' venues with no check on whether either side was sitting in
    # its own designated venue. Since this is the LAST venue-mutating pass
    # in the whole pipeline (after Phase 6/6B/6B2), that let an unrelated
    # course get relocated straight into a reserved venue (PASS A, if it
    # happened to be empty), or get swapped directly with a designated
    # course's rightful occupant (PASS B) — silently undoing every
    # specialization guard every earlier pass took care to respect.
    try:
        blocked_venue_ids = constraint_engine.get_blocked_venue_ids(scheduler_type="regular")
        exclusive_venue_ids = constraint_engine.get_exclusive_venue_ids(scheduler_type="regular")
        course_to_venues, _specialization_venue_ids = build_specialization_index()
    except Exception:
        blocked_venue_ids = set()
        exclusive_venue_ids = set()
        course_to_venues = {}
    off_limits_venue_ids = blocked_venue_ids | exclusive_venue_ids

    def _entry_home_venue_ids(alloc) -> Set[int]:
        if not alloc or not course_to_venues:
            return set()
        norm = normalize_course_code(alloc.course_code)
        candidates = (
            course_to_venues.get((norm, alloc.program_id), [])
            + course_to_venues.get((norm, None), [])
        )
        return {v.id for v, _rule in candidates}

    def _venue_allowed_for(alloc, venue_id) -> bool:
        """A blocked/exclusive venue may only ever be used by an alloc that
        is itself designated to it. Any other venue is always fine."""
        if venue_id not in off_limits_venue_ids:
            return True
        return venue_id in _entry_home_venue_ids(alloc)

    def _safe_year(alloc) -> int:
        try:
            return get_course_year(alloc)
        except Exception:
            return 1

    all_entries = list(
        TempTimetable.objects.select_related(
            'course_allocation', 'course_allocation__lecturer',
            'course_allocation__program', 'venue'
        ).all()
    )
    all_venues = list(Venue.objects.all())

    # ── Fresh occupancy indexes, keyed by (day, start, end) tuples ─────────
    venue_busy: Set[Tuple[int, str, dtime, dtime]] = set()
    lecturer_busy: Set[Tuple[int, str, dtime, dtime]] = set()
    program_year_slot_allocs: Dict[Tuple, List] = defaultdict(list)
    prog_year_slot_entries: Dict[Tuple, List] = defaultdict(list)
    entries_by_slot: Dict[Tuple[str, dtime, dtime], List] = defaultdict(list)

    for e in all_entries:
        alloc = e.course_allocation
        if not alloc or not e.venue:
            continue
        venue_busy.add((e.venue_id, e.day, e.start_time, e.end_time))
        entries_by_slot[(e.day, e.start_time, e.end_time)].append(e)
        if alloc.lecturer_id:
            lecturer_busy.add((alloc.lecturer_id, e.day, e.start_time, e.end_time))
        if alloc.program_id:
            year = _safe_year(alloc)
            key = (alloc.program_id, year, e.day, e.start_time, e.end_time)
            program_year_slot_allocs[key].append(alloc)
            prog_year_slot_entries[key].append(e)

    def _lecturer_free(lecturer_id, day, start, end) -> bool:
        return not lecturer_id or (lecturer_id, day, start, end) not in lecturer_busy

    def _program_year_free(program_id, year, day, start, end, new_alloc, exclude_alloc=None) -> bool:
        if not program_id:
            return True
        for existing_alloc in program_year_slot_allocs.get((program_id, year, day, start, end), []):
            if exclude_alloc is not None and existing_alloc.id == exclude_alloc.id:
                continue
            if not is_program_year_collision_exempt(new_alloc, existing_alloc):
                return False
        return True

    def _venue_free(venue_id, day, start, end) -> bool:
        return (venue_id, day, start, end) not in venue_busy

    # ── Identify genuine collisions (non-exempt pairs sharing a slot) ──────
    colliding_entries = []  # [(entry, alloc, year), ...]
    for key, allocs_here in program_year_slot_allocs.items():
        if len(allocs_here) < 2:
            continue
        program_id, year, day, start, end = key
        has_real_collision = any(
            not is_program_year_collision_exempt(allocs_here[i], allocs_here[j])
            for i in range(len(allocs_here))
            for j in range(i + 1, len(allocs_here))
        )
        if not has_real_collision:
            continue
        stats['collisions_found'] += 1
        # Leave the oldest entry at this slot untouched; try to relocate
        # every OTHER entry sharing it.
        entries_here = sorted(prog_year_slot_entries[key], key=lambda e: e.id)
        for e in entries_here[1:]:
            if e.course_allocation_id in merged_alloc_ids:
                continue
            colliding_entries.append((e, e.course_allocation, year))

    if not colliding_entries:
        safe_print("[CollisionSwap] No genuine leftover program-year collisions found.")
        return stats

    safe_print(
        f"[CollisionSwap] {stats['collisions_found']} colliding slot(s) found, "
        f"{len(colliding_entries)} entr(y/ies) to attempt relocating/swapping"
    )

    for entry, alloc, year in colliding_entries:
        lecturer_id = alloc.lecturer_id
        program_id = alloc.program_id
        old_day, old_start, old_end, old_venue = entry.day, entry.start_time, entry.end_time, entry.venue
        old_slot_key = (old_day, old_start, old_end)
        resolved = False

        # ── PASS A: relocate to any clean empty destination ────────────────
        for day in days:
            if resolved:
                break
            for start, end in slots:
                if resolved:
                    break
                if day == old_day and start == old_start and end == old_end:
                    continue
                if not _lecturer_free(lecturer_id, day, start, end):
                    continue
                if not _program_year_free(program_id, year, day, start, end, alloc, exclude_alloc=alloc):
                    continue
                dest_venue = next(
                    (v for v in all_venues
                     if _venue_free(v.id, day, start, end)
                     and _venue_allowed_for(alloc, v.id)),
                    None
                )
                if not dest_venue:
                    continue
                try:
                    with transaction.atomic():
                        TempTimetable.objects.filter(id=entry.id).update(
                            venue=dest_venue, day=day, start_time=start, end_time=end
                        )
                    venue_busy.discard((old_venue.id, old_day, old_start, old_end))
                    venue_busy.add((dest_venue.id, day, start, end))
                    entries_by_slot[old_slot_key] = [
                        x for x in entries_by_slot[old_slot_key] if x.id != entry.id
                    ]
                    entries_by_slot[(day, start, end)].append(entry)
                    if lecturer_id:
                        lecturer_busy.discard((lecturer_id, old_day, old_start, old_end))
                        lecturer_busy.add((lecturer_id, day, start, end))
                    if program_id:
                        old_bucket = program_year_slot_allocs.get(
                            (program_id, year, old_day, old_start, old_end), []
                        )
                        if alloc in old_bucket:
                            old_bucket.remove(alloc)
                        program_year_slot_allocs[(program_id, year, day, start, end)].append(alloc)
                    stats['resolved_by_move'] += 1
                    resolved = True
                    safe_print(
                        f"[CollisionSwap] MOVED: {alloc.course_code} away from a program-year "
                        f"clash — {old_venue.code} ({old_day} {old_start}-{old_end}) → "
                        f"{dest_venue.code} ({day} {start}-{end})"
                    )
                except Exception as exc:
                    stats['errors'] += 1
                    safe_print(f"[CollisionSwap] ERROR moving entry {entry.id}: {exc}")

        if resolved:
            continue

        # ── PASS B: two-way swap with an unrelated, non-merged course ──────
        for day in days:
            if resolved:
                break
            for start, end in slots:
                if resolved:
                    break
                if day == old_day and start == old_start and end == old_end:
                    continue
                for y_entry in list(entries_by_slot.get((day, start, end), [])):
                    y_alloc = y_entry.course_allocation
                    if not y_alloc or y_entry.id == entry.id:
                        continue
                    if y_entry.course_allocation_id in merged_alloc_ids:
                        continue
                    if y_alloc.program_id == program_id and _safe_year(y_alloc) == year:
                        continue  # same cohort — swapping wouldn't clear anything
                    y_lecturer_id = y_alloc.lecturer_id
                    y_program_id = y_alloc.program_id
                    y_year = _safe_year(y_alloc)

                    c_fits_there = (
                        _lecturer_free(lecturer_id, day, start, end)
                        and _program_year_free(program_id, year, day, start, end, alloc, exclude_alloc=y_alloc)
                    )
                    y_fits_here = (
                        _lecturer_free(y_lecturer_id, old_day, old_start, old_end)
                        and _program_year_free(
                            y_program_id, y_year, old_day, old_start, old_end, y_alloc, exclude_alloc=alloc
                        )
                    )
                    if not (c_fits_there and y_fits_here):
                        continue

                    y_old_venue = y_entry.venue

                    # ── Venue-specialization guard ───────────────────────
                    # Never pull y_entry out of ITS OWN designated venue
                    # (that would silently vacate a reserved room), and
                    # never drop either side into a blocked/exclusive venue
                    # it isn't itself designated to.
                    if y_entry.venue_id in _entry_home_venue_ids(y_alloc):
                        continue
                    if entry.venue_id in _entry_home_venue_ids(alloc):
                        continue
                    if not _venue_allowed_for(y_alloc, old_venue.id):
                        continue
                    if not _venue_allowed_for(alloc, y_old_venue.id):
                        continue
                    try:
                        with transaction.atomic():
                            TempTimetable.objects.filter(id=entry.id).update(
                                venue=y_old_venue, day=day, start_time=start, end_time=end
                            )
                            TempTimetable.objects.filter(id=y_entry.id).update(
                                venue=old_venue, day=old_day, start_time=old_start, end_time=old_end
                            )
                        # ── swap occupancy indexes ──────────────────────────
                        venue_busy.discard((old_venue.id, old_day, old_start, old_end))
                        venue_busy.discard((y_old_venue.id, day, start, end))
                        venue_busy.add((y_old_venue.id, day, start, end))
                        venue_busy.add((old_venue.id, old_day, old_start, old_end))

                        entries_by_slot[old_slot_key] = [
                            x for x in entries_by_slot[old_slot_key] if x.id != entry.id
                        ]
                        entries_by_slot[(day, start, end)] = [
                            x for x in entries_by_slot[(day, start, end)] if x.id != y_entry.id
                        ]
                        entries_by_slot[(day, start, end)].append(entry)
                        entries_by_slot[old_slot_key].append(y_entry)

                        if lecturer_id:
                            lecturer_busy.discard((lecturer_id, old_day, old_start, old_end))
                            lecturer_busy.add((lecturer_id, day, start, end))
                        if y_lecturer_id:
                            lecturer_busy.discard((y_lecturer_id, day, start, end))
                            lecturer_busy.add((y_lecturer_id, old_day, old_start, old_end))

                        if program_id:
                            bucket = program_year_slot_allocs.get(
                                (program_id, year, old_day, old_start, old_end), []
                            )
                            if alloc in bucket:
                                bucket.remove(alloc)
                            program_year_slot_allocs[(program_id, year, day, start, end)].append(alloc)
                        if y_program_id:
                            y_bucket = program_year_slot_allocs.get(
                                (y_program_id, y_year, day, start, end), []
                            )
                            if y_alloc in y_bucket:
                                y_bucket.remove(y_alloc)
                            program_year_slot_allocs[
                                (y_program_id, y_year, old_day, old_start, old_end)
                            ].append(y_alloc)

                        stats['resolved_by_swap'] += 1
                        resolved = True
                        safe_print(
                            f"[CollisionSwap] SWAPPED: {alloc.course_code} "
                            f"({old_venue.code} {old_day} {old_start}-{old_end}) ↔ "
                            f"{y_alloc.course_code} ({y_old_venue.code} {day} {start}-{end}) "
                            f"— cleared a program-year clash for both"
                        )
                    except Exception as exc:
                        stats['errors'] += 1
                        safe_print(f"[CollisionSwap] ERROR swapping entries {entry.id}/{y_entry.id}: {exc}")
                    break

        if not resolved:
            stats['unresolved'] += 1
            safe_print(
                f"[CollisionSwap] UNRESOLVED: {alloc.course_code} still colliding at "
                f"{old_venue.code} ({old_day} {old_start}-{old_end}) — no clean move or "
                f"swap partner found; needs manual review"
            )

    safe_print(
        f"[CollisionSwap] Complete — collisions_found={stats['collisions_found']} | "
        f"resolved_by_move={stats['resolved_by_move']} | "
        f"resolved_by_swap={stats['resolved_by_swap']} | "
        f"unresolved={stats['unresolved']} | errors={stats['errors']}"
    )
    safe_print("=" * 70)

    return stats


def apply_lecturer_soft_preferences_pass(
    days: List[str],
    slots: List[Tuple[dtime, dtime]],
    disabled_constraints: Optional[Set[str]] = None,
) -> Dict[str, int]:
    """
    PHASE 6.5 — Best-effort relocation for SOFT lecturer preferences
    (preferred day/time, preferred venue). Runs after the main scheduling
    phases and the venue-capacity optimisation pass, directly against the
    persisted TempTimetable rows (the DB is the source of truth by this
    point, not the in-memory ConflictTracker).

    Safety rules — this pass NEVER creates a new conflict or leaves a
    course unscheduled to satisfy a preference:
      - the target (day, slot) must have no other class for that lecturer
      - the target venue (current or preferred) must be free at that
        (day, slot)
      - the target (day, slot) must not already hold another class for the
        same program (a conservative check — safe even without exact-year
        granularity)
      - merged-group entries are exempted, same as the venue-capacity pass

    Anything that fails any check is simply left where it already is.
    Reusable design: this operates purely on TempTimetable + the raw
    preference data from core.scheduling_constraints, so an exam-scheduler
    equivalent could reuse the same shape against its own timetable model.
    """
    stats = {'lecturers_considered': 0, 'entries_moved': 0, 'entries_left_in_place': 0, 'errors': 0}

    time_prefs = constraint_engine.get_lecturer_time_preferences(disabled_constraints)
    venue_prefs = constraint_engine.get_lecturer_venue_preferences(disabled_constraints)
    blocked_ranges = constraint_engine.get_lecturer_blocked_ranges(disabled_constraints)

    # A LecturerVenuePreference must never win over the Blocked Venues /
    # exclusive-specialization constraint — this pass runs dead last in the
    # whole pipeline, so a preferred venue that's since been hard-blocked
    # (or belongs to another department's exclusive specialization) would
    # otherwise get silently reintroduced with nothing downstream left to
    # catch it. Same treatment as every other venue-choosing pass in this
    # file.
    try:
        blocked_venue_ids = constraint_engine.get_blocked_venue_ids(disabled_constraints)
        exclusive_venue_ids = constraint_engine.get_exclusive_venue_ids(disabled_constraints)
    except Exception:
        blocked_venue_ids = set()
        exclusive_venue_ids = set()
    off_limits_venue_ids = blocked_venue_ids | exclusive_venue_ids

    lecturer_ids = set(time_prefs.keys()) | set(venue_prefs.keys())
    if not lecturer_ids:
        safe_print("[LecturerPrefs] No active soft preferences — skipping Phase 6.5")
        return stats

    safe_print("\n" + "=" * 70)
    safe_print(f"PHASE 6.5: Lecturer Soft Preferences — {len(lecturer_ids)} lecturer(s) with preferences")
    safe_print("=" * 70)

    try:
        merged_alloc_ids: Set[int] = set(
            MergedCourseGroupTimetable.objects.values_list('merged_courses__id', flat=True).distinct()
        )
    except Exception:
        merged_alloc_ids = set()

    def _is_hard_blocked(lecturer_id, day, start, end) -> bool:
        """
        A soft (day, start, end) candidate must never be used to relocate a
        lecturer into a slot that a LecturerBlockedSlot hard rule forbids —
        a contradictory admin setup (e.g. blocked all day Thursday AND a
        Thursday time preference) must lose to the hard block, not silently
        override it.
        """
        for b_day, b_start, b_end in blocked_ranges.get(lecturer_id, []):
            if b_day != day:
                continue
            if b_start is None or b_end is None:
                return True  # whole day blocked
            if b_start < end and start < b_end:
                return True  # overlap
        return False

    def _matching_slot_indices(day_time_ranges):
        """(day, start, end) prefs -> list of (day, slot_start, slot_end) candidates, preferred day first."""
        candidates = []
        for day, start, end in day_time_ranges:
            if day not in days:
                continue
            if start is None or end is None:
                for s, e in slots:
                    candidates.append((day, s, e))
            else:
                for s, e in slots:
                    if s < end and start < e:
                        candidates.append((day, s, e))
        return candidates

    for lecturer_id in lecturer_ids:
        stats['lecturers_considered'] += 1
        preferred_slots = [
            (day, s, e) for (day, s, e) in _matching_slot_indices(time_prefs.get(lecturer_id, []))
            if not _is_hard_blocked(lecturer_id, day, s, e)
        ]
        # Hard block always wins over a soft venue preference too — drop
        # any preferred venue that's blocked/exclusive rather than treating
        # it as usable.
        preferred_venue_ids = venue_prefs.get(lecturer_id, set()) - off_limits_venue_ids


        try:
            entries = list(
                TempTimetable.objects.select_related('course_allocation', 'venue')
                .filter(course_allocation__lecturer_id=lecturer_id)
                .exclude(course_allocation_id__in=merged_alloc_ids)
            )
        except Exception as exc:
            stats['errors'] += 1
            safe_print(f"[LecturerPrefs] ERROR loading entries for lecturer {lecturer_id}: {exc}")
            continue

        for entry in entries:
            already_ok_day = (not preferred_slots) or any(
                entry.day == d and entry.start_time == s and entry.end_time == e
                for d, s, e in preferred_slots
            )
            already_ok_venue = (not preferred_venue_ids) or (entry.venue_id in preferred_venue_ids)
            if already_ok_day and already_ok_venue:
                continue

            candidate_slots = preferred_slots or [(entry.day, entry.start_time, entry.end_time)]
            candidate_venue_ids = list(preferred_venue_ids) if preferred_venue_ids else (
                [entry.venue_id] if entry.venue_id else []
            )
            if not candidate_venue_ids:
                stats['entries_left_in_place'] += 1
                continue

            moved = False
            try:
                for day, s_start, s_end in candidate_slots:
                    if moved:
                        break
                    lecturer_busy = TempTimetable.objects.filter(
                        course_allocation__lecturer_id=lecturer_id,
                        day=day, start_time=s_start, end_time=s_end,
                    ).exclude(id=entry.id).exists()
                    if lecturer_busy:
                        continue

                    program_id = entry.course_allocation.program_id if entry.course_allocation else None
                    if program_id:
                        program_busy = TempTimetable.objects.filter(
                            day=day, start_time=s_start, end_time=s_end,
                            course_allocation__program_id=program_id,
                        ).exclude(id=entry.id).exists()
                        if program_busy:
                            continue

                    for venue_id in candidate_venue_ids:
                        venue_busy = TempTimetable.objects.filter(
                            venue_id=venue_id, day=day, start_time=s_start, end_time=s_end,
                        ).exclude(id=entry.id).exists()
                        if venue_busy:
                            continue

                        with transaction.atomic():
                            TempTimetable.objects.filter(id=entry.id).update(
                                venue_id=venue_id, day=day, start_time=s_start, end_time=s_end,
                            )
                        stats['entries_moved'] += 1
                        moved = True
                        break
            except Exception as exc:
                stats['errors'] += 1
                safe_print(f"[LecturerPrefs] ERROR relocating entry {entry.id}: {exc}")
                continue

            if not moved:
                stats['entries_left_in_place'] += 1

    safe_print(
        f"[LecturerPrefs] Complete — lecturers={stats['lecturers_considered']} | "
        f"moved={stats['entries_moved']} | left_in_place={stats['entries_left_in_place']} | "
        f"errors={stats['errors']}"
    )
    safe_print("=" * 70)
    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN SCHEDULER THREAD
# ═══════════════════════════════════════════════════════════════════════════════

def run_optimized_autoscheduler_thread(disabled_constraints: Optional[Set[str]] = None):
    """
    disabled_constraints: constraint-category keys (see
    core.scheduling_constraints.CONSTRAINT_DEFS) unchecked by the admin on
    the pre-run confirmation screen — disabled for THIS run only, without
    changing the persisted SchedulerConstraintToggle defaults.
    """
    disabled_constraints = disabled_constraints or set()
    total_courses = 0
    all_scheduled_courses = []
    all_unscheduled_courses = []

    _open_scheduler_log()   # open per-run log file in algorithms/logs/
    cache = SchedulerCache()

    try:
        enable_wal_mode()
        clear_tables_safely()

        config = SchedulerConfig.objects.first() or SchedulerConfig.objects.create()
        start_time = getattr(config, 'start_time', dtime(hour=7, minute=0))
        end_time = getattr(config, 'end_time', dtime(hour=19, minute=0))
        slot_size = int(getattr(config, 'slot_size', 3))
        merge_limit = getattr(config, 'merge_limit', 200)
        days = getattr(config, 'days', None) or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        slots = generate_slots(start_time, end_time, slot_size)

        all_venues = cache.get_all_venues()

        # ── Constraint: Blocked Venues ───────────────────────────────────────
        # Removed from the shared venue pool ONCE, right here — every phase
        # below (batch, fallback, compression, PG, sweep, evening/weekend,
        # zero-student) receives this same `all_venues` list, so a blocked
        # venue is unavailable everywhere with no per-phase changes needed.
        blocked_venue_ids = constraint_engine.get_blocked_venue_ids(disabled_constraints)
        if blocked_venue_ids:
            before_count = len(all_venues)
            all_venues = [v for v in all_venues if v.id not in blocked_venue_ids]
            safe_print(
                f"[Constraints] Blocked Venues: removed {before_count - len(all_venues)} "
                f"venue(s) from the candidate pool ({len(all_venues)} remain)."
            )

        faculty_venues_map = cache.get_faculty_venues_map()
        if blocked_venue_ids:
            # get_faculty_venues_for_course() reads this map directly (not
            # the all_venues list above), so it needs its own filter pass.
            faculty_venues_map = {
                fac: [v for v in vlist if v.id not in blocked_venue_ids]
                for fac, vlist in faculty_venues_map.items()
            }

        # ── Constraint: Lecturer Blocked Days/Times (hard) ──────────────────
        lecturer_blocked_map = build_lecturer_blocked_slot_map(days, slots, disabled_constraints)
        if lecturer_blocked_map:
            safe_print(
                f"[Constraints] Lecturer Blocked Days/Times: loaded for "
                f"{len(lecturer_blocked_map)} lecturer(s)."
            )

        conflict_tracker = ConflictTracker(days, slots, lecturer_blocked=lecturer_blocked_map)
        venue_allocator = VenueAllocator(all_venues, days, slots, conflict_tracker)

        all_courses = list(CourseAllocation.objects.select_related(
            'program', 'lecturer', 'department',
            'program__department', 'program__department__faculty',
            'lecturer__department', 'lecturer__department__faculty'
        ).all())

        total_courses = len(all_courses)
        if total_courses == 0:
            update_progress(100, "No courses found to schedule", 0, 0,
                            console_message="No courses found",
                            scheduled_courses=[], unscheduled_courses=[])
            return

        # ── PROGRAM-YEAR FAIRNESS ───────────────────────────────────────────
        # Department-size priority has been removed: departments no longer
        # get scheduled smallest-first/biggest-last as a block. Ordering is
        # now handled by interleave_by_program_year (see STEP 2/STEP 3 below)
        # which round-robins every program-year cohort an equal turn, so the
        # day's classes end up genuinely distributed across departments
        # instead of one department's courses dominating first or being
        # dumped last.

        # ── CRISIS FIX (do not reintroduce the old dead-last deferral) ──────────
        # Previously, every allocation with number_of_students 0/NULL was pulled
        # OUT of all_courses here and held back for a single dedicated pass
        # (formerly "STEP 5c") that only ran after every other phase — merged
        # UG, individual UG, PG, exhaustive sweep, overload relief, evening/
        # weekend — had already claimed the conflict-free program-year day/slot
        # combinations. In practice number_of_students=0/NULL almost always just
        # means enrollment hasn't been captured yet for that allocation, not
        # that the class is empty — so this was an entire cohort's worth of real
        # courses being punished to go last, and by the time their one shot
        # came up, busy programs had no conflict-free slot left at all — whole
        # programs came back unscheduled. That is the crisis being fixed here.
        #
        # Fix: zero/NULL-enrollment allocations now stay IN all_courses and flow
        # through the normal pipeline (merge → merged UG → individual UG → PG →
        # exhaustive sweep → overload relief), getting the same multiple
        # chances as everything else instead of one last-ditch pass. Updated
        # per user request: within STEP 2/STEP 3, ordering is no longer
        # perfectly equal-footing — interleave_by_program_year(...,
        # known_count_first=True) now orders courses with a real, known
        # student count ahead of 0/NULL courses, so known numbers get first
        # pick of scarce slots/venues while 0/NULL fills in right after, in
        # the SAME pass (never deferred to a separate dead-last phase — that
        # was the original crisis). Collision safety is unaffected either
        # way: (1) no lecturer conflict, (2) no program-year collision, (3) no
        # blocked/exclusive venue — all enforced identically for every course
        # regardless of enrollment count or ordering position. Venue
        # CAPACITY fit is intentionally the lowest priority everywhere in this
        # file (find_efficient_venue always falls back to any free venue rather
        # than leaving a course unplaced) — that's deliberate right now: get
        # every course onto the grid with zero collisions first, then rebalance
        # capacity afterward via the venue-swap optimizer, which is far easier
        # than trying to fix a partially-unscheduled live timetable by hand.
        zero_student_alloc_ids = {
            a.id for a in all_courses
            if (a.number_of_students is None or a.number_of_students == 0)
            and not getattr(a, 'is_evening_weekend', False)
        }
        n_zero = len(zero_student_alloc_ids)
        if n_zero:
            safe_print(
                f"[DataQuality] {n_zero} allocation(s) have number_of_students=0/NULL "
                f"and will be scheduled in the NORMAL passes alongside everything else "
                f"(no longer deferred to a dead-last pass)."
            )
        total_courses = len(all_courses)

        cache.load_existing_timetable_entries()

        # STEP 0 — PRE-SCHEDULING ANALYSIS
        safe_print("\nSTEP 0 — PRE-SCHEDULING ANALYSIS: examining data constraints …")
        update_progress(2, "STEP 0: Pre-scheduling analysis — examining all constraints …",
                        0, total_courses,
                        console_message="Analysing cohort sizes, lecturer loads, venue capacities …")

        analysis_report = analyse_scheduling_data(
            all_courses=all_courses,
            all_venues=all_venues,
            days=days,
            slots=slots,
            cache=cache,
            merge_limit=merge_limit,
            max_per_day=2,
        )

        update_progress(
            4,
            (f"STEP 0 done: feasibility={analysis_report.feasibility_score:.2f}, "
             f"marginal={len(analysis_report.marginal_courses)} courses, "
             f"collision-pairs={analysis_report.cross_cohort_collision_pairs}"),
            0, total_courses,
            console_message=(
                f"Analysis complete — feasibility score: {analysis_report.feasibility_score:.2f} "
                f"({'OK' if analysis_report.is_feasible else 'RISK'}) | "
                f"Marginal-cap courses: {len(analysis_report.marginal_courses)} | "
                f"Overflow candidates: {len(analysis_report.overflow_candidates)} | "
                f"Cross-cohort collision pairs: {analysis_report.cross_cohort_collision_pairs}"
            )
        )

        if analysis_report.critical_issues:
            safe_print("[Analysis] *** CRITICAL ISSUES DETECTED ***")
            for ci in analysis_report.critical_issues:
                safe_print(f"[Analysis] CRITICAL: {ci}")

        if analysis_report.warnings:
            safe_print("[Analysis] Warnings:")
            for w in analysis_report.warnings[:10]:
                safe_print(f"[Analysis] WARNING: {w}")

        if analysis_report.recommendations:
            safe_print("[Analysis] Recommendations:")
            for r in analysis_report.recommendations:
                safe_print(f"[Analysis] RECOMMEND: {r}")

        # STEP 1: GLOBAL MERGE IDENTIFICATION
        safe_print(f"STEP 1: Global merge scan of {total_courses} allocations...")
        update_progress(5, "STEP 1: Identifying all mergeable sections globally...",
                        0, total_courses,
                        console_message=f"Scanning {total_courses} allocations for merge candidates")

        all_tasks = build_global_merged_tasks(all_courses, merge_limit)

        n_merged_groups = sum(1 for t in all_tasks if isinstance(t, dict))
        n_merged_allocs = sum(len(t['merged']) for t in all_tasks if isinstance(t, dict))

        safe_print(
            f"STEP 1 done: {total_courses} allocations → {len(all_tasks)} tasks | "
            f"{n_merged_groups} merged groups covering {n_merged_allocs} sections"
        )
        update_progress(8, f"STEP 1 done: {n_merged_groups} merge groups found",
                        0, total_courses,
                        console_message=f"{n_merged_groups} merged groups ({n_merged_allocs} sections merged)")

        # Apply difficulty ordering
        safe_print("[Analysis] Applying difficulty-ordered task sequencing from pre-analysis …")
        all_tasks = build_difficulty_ordered_tasks(all_tasks, analysis_report, cache)
        safe_print(
            f"[Analysis] Tasks re-ordered: hardest-first sequencing applied "
            f"({len(all_tasks)} tasks)"
        )

        # Create MergedCourseGroupTimetable DB records
        merged_group_db_ids = {}
        for task in all_tasks:
            if isinstance(task, dict) and task.get('group_id') is None:
                gid = create_merged_group_db(task)
                if gid:
                    task['group_id'] = gid
                    merged_group_db_ids[gid] = MergedCourseGroupTimetable.objects.get(id=gid)

        # Separate into merged UG / individual UG / PG
        # ── Evening/Weekend tasks are EXCLUDED from all regular passes ────────
        # Courses with is_evening_weekend=True are ONLY scheduled in the
        # dedicated evening/weekend pass (Step 5b). They must never compete
        # for regular daytime slots.
        evening_weekend_tasks = [t for t in all_tasks if _task_is_evening_weekend(t)]
        regular_tasks         = [t for t in all_tasks if not _task_is_evening_weekend(t)]
        all_tasks             = regular_tasks  # all subsequent passes use regular_tasks only

        n_evening_weekend = len(evening_weekend_tasks)
        n_ew_allocs       = sum(len(_task_allocs(t)) for t in evening_weekend_tasks)
        safe_print(
            f"[Split] Evening/Weekend tasks: {n_evening_weekend} ({n_ew_allocs} allocs) "
            f"→ reserved for Step 5b | Regular tasks: {len(all_tasks)}"
        )

        merged_ug_tasks = [t for t in all_tasks if isinstance(t, dict) and not _task_is_pg(t)]
        individual_ug_tasks = [t for t in all_tasks if not isinstance(t, dict) and not _task_is_pg(t)]
        pg_tasks = [t for t in all_tasks if _task_is_pg(t)]

        n_merged_ug = len(merged_ug_tasks)
        n_individual = len(individual_ug_tasks)
        n_pg = len(pg_tasks)
        n_pg_allocs = sum(len(_task_allocs(t)) for t in pg_tasks)
        n_ug_allocs = sum(len(_task_allocs(t)) for t in merged_ug_tasks + individual_ug_tasks)

        globally_scheduled_alloc_ids: Set[int] = set()

        safe_print(f"Merged UG groups: {n_merged_ug} | Individual UG: {n_individual} | PG: {n_pg}")

        update_progress(10, f"Separated: {n_merged_ug} merged UG, {n_individual} individual UG, {n_pg} PG",
                        0, total_courses,
                        console_message=f"Merged UG: {n_merged_ug}, Individual UG: {n_individual}, PG: {n_pg}")

        # STEP 0 + 0b: VENUE SPECIALIZATION PRIORITY PASS
        safe_print("\nSTEP 0: Building venue specialization index …")
        update_progress(11, "STEP 0: Loading venue specialization rules …",
                        0, total_courses,
                        console_message="Building specialization index")

        if constraint_engine.is_enabled("venue_specialization", disabled_constraints):
            course_to_venues, specialization_venue_ids = build_specialization_index()
        else:
            safe_print("[Constraints] Venue Specialization disabled for this run — skipping.")
            course_to_venues, specialization_venue_ids = {}, set()
        n_spec_rules = len(course_to_venues)
        n_spec_venues = len(specialization_venue_ids)

        safe_print(f"STEP 0 done: {n_spec_rules} specialised codes, {n_spec_venues} reserved venues")

        strict_fail_alloc_ids: set = set()
        spec_scheduled = 0

        if course_to_venues:
            update_progress(11, "STEP 0b: Specialization priority pass …",
                            0, total_courses,
                            console_message=f"{n_spec_rules} specialised codes → {n_spec_venues} reserved venues")

            spec_scheduled, spec_strict_fails, spec_sl, spec_ul = process_specialized_pass(
                all_tasks,
                course_to_venues,
                specialization_venue_ids,
                days, slots,
                conflict_tracker,
                cache,
                globally_scheduled_alloc_ids,
            )
            all_scheduled_courses.extend(spec_sl)
            all_unscheduled_courses.extend(spec_ul)
            for t in spec_strict_fails:
                for a in _task_allocs(t):
                    strict_fail_alloc_ids.add(a.id)

            safe_print(f"STEP 0b done: {spec_scheduled} allocs placed via specialization")
            update_progress(12, "STEP 0b done: Specialization pass complete",
                            spec_scheduled, total_courses - spec_scheduled,
                            console_message=(
                                f"Spec pass: {spec_scheduled} placed, "
                                f"{len(spec_strict_fails)} strict failures"
                            ),
                            scheduled_courses=all_scheduled_courses,
                            unscheduled_courses=all_unscheduled_courses)
        else:
            safe_print("STEP 0b: No active specialization rules — skipping pass")
            update_progress(12, "STEP 0b: No specialization rules — skipped", 0, total_courses)

        # Previously: strict_fail_alloc_ids were folded into
        # globally_scheduled_alloc_ids here, which every later phase reads
        # as "already handled — skip forever" (Sweep, OverloadRelief,
        # EveningWeekend, ZeroStudent, and even FinalSafetyNet's PASS C,
        # which forces courses into ANY physically free venue as an
        # absolute last resort). That meant a course whose *only* problem
        # was "my designated venue was already taken by another
        # same-rule course" got permanently exiled from the timetable
        # the moment the specialization pass failed — even when the rest
        # of the campus had rooms sitting empty all week. `strict` was
        # only ever meant to say "you may not use a NON-designated venue
        # while a designated one has room" — not "you may never be
        # scheduled at all if your designated venue fills up."
        #
        # Fix: do NOT block these allocs. Let them fall through into the
        # normal scheduling cascade (Sweep → OverloadRelief →
        # EveningWeekend → ZeroStudent → FinalSafetyNet) exactly like any
        # other course that didn't get placed on its first attempt. They
        # still never get to use an exclusive-designated venue (those stay
        # off_limits_venue_ids-filtered out of the general pool
        # regardless), so a strict+exclusive rule's reservation is still
        # fully honored — this only stops orphaning courses whose rule was
        # strict but NOT exclusive, or that simply lost a capacity race
        # against a sibling course for the same designated room.
        if strict_fail_alloc_ids:
            safe_print(
                f"[SpecPass] {len(strict_fail_alloc_ids)} strict-fail alloc ID(s) could not "
                f"get a designated venue — falling through to the normal scheduling cascade "
                f"(general, non-exclusive venues only) instead of being permanently blocked."
            )

        # ── Constraint: Exclusive Venue Restrictions ─────────────────────────
        # Venues attached to an active, exclusive=True specialization rule are
        # removed from the general `all_venues` pool from this point forward.
        # The specialized pass above already had its shot at placing their
        # designated courses using the FULL venue list; every phase that runs
        # after this line (fallback, compression, PG, sweep, evening/weekend,
        # zero-student) shares this same `all_venues` reference, so excluding
        # exclusive venues here — once — keeps them off-limits to every other
        # course for the rest of the run with no per-phase changes needed.
        exclusive_venue_ids = constraint_engine.get_exclusive_venue_ids(disabled_constraints)
        if exclusive_venue_ids:
            before_count = len(all_venues)
            all_venues = [v for v in all_venues if v.id not in exclusive_venue_ids]
            faculty_venues_map = {
                fac: [v for v in vlist if v.id not in exclusive_venue_ids]
                for fac, vlist in faculty_venues_map.items()
            }
            safe_print(
                f"[Constraints] Exclusive Venue Restrictions: removed "
                f"{before_count - len(all_venues)} venue(s) from the general pool "
                f"({len(all_venues)} remain for non-designated courses)."
            )

        # Program day distribution for individual UG
        program_course_map = defaultdict(list)
        for task in individual_ug_tasks:
            rep = _representative(task)
            if rep.program:
                program_course_map[rep.program.id].append(task)

        program_day_assignments = {}
        for program_id, task_list in program_course_map.items():
            if len(task_list) > 1:
                shuffled = list(days) * 3
                random.shuffle(shuffled)
                for i, t in enumerate(task_list):
                    rep = _representative(t)
                    program_day_assignments[rep.id] = shuffled[i % len(days)]

        # STEP 2: SCHEDULE INDIVIDUAL COURSES FIRST
        # Individual (non-shared) UG courses are now scheduled BEFORE the
        # cross-program common/merged units, so every individual course —
        # plus its fallback and compression passes below — gets first pick
        # of slots and venues and the best possible chance of actually
        # landing a venue. Common units (courses shared across multiple
        # programs, merged into one cross-program group) are scheduled
        # afterwards in STEP 3, once individual demand has already claimed
        # its slots.
        # Fairly interleaved across (program, year) cohorts BEFORE batching,
        # so batches themselves are drawn from an already-mixed order (not
        # just re-sorted within each batch) — this is what actually
        # guarantees every program-year gets an equal, evenly-distributed
        # share of early batches instead of one department running solid
        # through the whole run. Largest-class-first within each cohort so
        # bigger courses get first pick of a fitting venue.
        # known_count_first=True: same STEP 2 pass, but courses with a real,
        # known student count are ordered into the earlier batches; 0/NULL
        # courses fill in during the later batches of this same step rather
        # than competing for the same early slots on equal terms.
        individual_ug_tasks = interleave_by_program_year(
            individual_ug_tasks, cache, size_desc=True, known_count_first=True
        )

        # Common/merged-unit scheduling hasn't run yet (it's now STEP 3) —
        # initialised here so the running-total progress messages below
        # still make sense while individual courses are placed first.
        merged_scheduled = 0

        safe_print(f"\nSTEP 2: Scheduling {n_individual} individual courses FIRST...")
        update_progress(12, f"STEP 2: Scheduling {n_individual} individual courses FIRST...",
                        0, n_individual,
                        console_message=f"Individual courses: {n_individual} courses")

        indiv_scheduled = 0

        INDIV_BATCH = min(50, max(20, n_individual // 8 or 20))
        indiv_batches = max(1, (n_individual + INDIV_BATCH - 1) // INDIV_BATCH)
        indiv_still_unscheduled = []

        for b_idx, i in enumerate(range(0, n_individual, INDIV_BATCH), 1):
            batch = individual_ug_tasks[i:i + INDIV_BATCH]
            prog = 12 + int((i + len(batch)) / max(n_individual, 1) * 20)
            update_progress(min(32, prog),
                            f"STEP 2: Individual batch {b_idx}/{indiv_batches}",
                            indiv_scheduled,
                            n_individual - indiv_scheduled,
                            batch_info=f"Batch {b_idx}/{indiv_batches}",
                            current_batch=b_idx, total_batches=indiv_batches,
                            console_message=f"Individual batch {b_idx}: {len(batch)} courses",
                            scheduled_courses=all_scheduled_courses,
                            unscheduled_courses=all_unscheduled_courses)

            sc, un, sl, ul = process_ug_batch(
                batch, config, faculty_venues_map, all_venues, days, slots,
                b_idx, indiv_batches, conflict_tracker, venue_allocator,
                cache, program_day_assignments, merged_group_db_ids,
                globally_scheduled_alloc_ids
            )
            indiv_scheduled += sc
            indiv_still_unscheduled.extend(un)
            all_scheduled_courses.extend(sl)
            all_unscheduled_courses.extend(ul)

        if indiv_still_unscheduled:
            update_progress(33, "STEP 2: Individual fallback (all venues)...",
                            indiv_scheduled,
                            len(indiv_still_unscheduled),
                            console_message=f"Individual fallback: {len(indiv_still_unscheduled)} courses",
                            scheduled_courses=all_scheduled_courses,
                            unscheduled_courses=all_unscheduled_courses)

            sc3, still3, sl3, ul3 = process_ug_fallback(
                indiv_still_unscheduled, all_venues, days, slots,
                conflict_tracker, venue_allocator, cache,
                globally_scheduled_alloc_ids
            )
            indiv_scheduled += sc3
            indiv_unscheduled_after_fallback = still3
            all_scheduled_courses.extend(sl3)
            all_unscheduled_courses.extend(ul3)
        else:
            indiv_unscheduled_after_fallback = []

        # Phase 3 Compression Mode
        compression_scheduled = 0
        indiv_unscheduled = indiv_unscheduled_after_fallback
        if indiv_unscheduled_after_fallback:
            update_progress(35, "STEP 2b: Phase 3 Compression...",
                            indiv_scheduled,
                            len(indiv_unscheduled_after_fallback),
                            console_message=f"Compression: {len(indiv_unscheduled_after_fallback)} courses remain",
                            scheduled_courses=all_scheduled_courses,
                            unscheduled_courses=all_unscheduled_courses)

            sc_comp, still_comp, sl_comp, ul_comp = process_ug_compression(
                indiv_unscheduled_after_fallback, all_venues, days, slots,
                conflict_tracker, venue_allocator, cache,
                globally_scheduled_alloc_ids
            )
            compression_scheduled = sc_comp
            indiv_scheduled += sc_comp
            indiv_unscheduled = still_comp
            all_scheduled_courses.extend(sl_comp)
            all_unscheduled_courses.extend(ul_comp)
            safe_print(f"Compression placed: {sc_comp}, still unscheduled: {len(still_comp)}")

        indiv_total = sum(len(_task_allocs(t)) for t in individual_ug_tasks)
        indiv_rate = (indiv_scheduled / max(indiv_total, 1)) * 100
        safe_print(
            f"STEP 2 done: {indiv_scheduled}/{indiv_total} individual UG allocs scheduled ({indiv_rate:.1f}%)"
        )
        update_progress(37, "STEP 2 done: Individual UG scheduling complete",
                        indiv_scheduled, len(indiv_unscheduled),
                        console_message=f"Individual UG: {indiv_scheduled}/{indiv_total} ({indiv_rate:.1f}%) | "
                                        f"Compression placed: {compression_scheduled}",
                        scheduled_courses=all_scheduled_courses,
                        unscheduled_courses=all_unscheduled_courses)

        # STEP 3: SCHEDULE COMMON/MERGED UNITS LAST
        # Common units — courses shared across multiple programs and merged
        # into a single cross-program group — are now scheduled LAST, after
        # every individual course has already been given its slot and
        # venue. Fairly interleaved across (program, year) cohorts — no
        # department gets priority by size any more — with each cohort's
        # own groups sorted largest-first so the biggest merged classes get
        # first pick of whatever venues remain (see
        # interleave_by_program_year docstring).
        # known_count_first=True: groups with a real, known student count
        # are ordered ahead of 0/NULL groups, so known numbers get first
        # pick of the remaining slots — 0/NULL still runs in this same
        # STEP 3 pass right after, not deferred to a separate dead-last
        # phase.
        merged_ug_tasks_sorted = interleave_by_program_year(
            merged_ug_tasks, cache, size_desc=True, known_count_first=True
        )

        safe_print(f"\nSTEP 3: Scheduling {n_merged_ug} common/merged unit groups (largest first)...")
        update_progress(39, f"STEP 3: Scheduling {n_merged_ug} common/merged unit groups LAST...",
                        indiv_scheduled, n_merged_ug,
                        console_message=f"Common-unit scheduling: {n_merged_ug} groups")

        merged_scheduled = 0
        merged_unscheduled = []

        BATCH_SIZE = min(50, max(10, n_merged_ug // 4 or 10))
        merged_batches = max(1, (n_merged_ug + BATCH_SIZE - 1) // BATCH_SIZE)
        merged_still_unscheduled = []

        for b_idx, i in enumerate(range(0, n_merged_ug, BATCH_SIZE), 1):
            batch = merged_ug_tasks_sorted[i:i + BATCH_SIZE]
            prog = 39 + int((i + len(batch)) / max(n_merged_ug, 1) * 26)
            update_progress(min(65, prog),
                            f"STEP 3: Common-unit batch {b_idx}/{merged_batches}",
                            indiv_scheduled + merged_scheduled,
                            n_merged_ug - merged_scheduled,
                            console_message=f"Common-unit batch {b_idx}: {len(batch)} groups")

            sc, un, sl, ul = process_ug_batch(
                batch, config, faculty_venues_map, all_venues, days, slots,
                b_idx, merged_batches, conflict_tracker, venue_allocator,
                cache, {}, merged_group_db_ids,
                globally_scheduled_alloc_ids
            )
            merged_scheduled += sc
            merged_still_unscheduled.extend(un)
            all_scheduled_courses.extend(sl)
            all_unscheduled_courses.extend(ul)

        if merged_still_unscheduled:
            update_progress(68, "STEP 3: Common-unit fallback (all venues)...",
                            indiv_scheduled + merged_scheduled,
                            len(merged_still_unscheduled),
                            console_message=f"Common-unit fallback: {len(merged_still_unscheduled)} groups")
            sc2, still2, sl2, ul2 = process_ug_fallback(
                merged_still_unscheduled, all_venues, days, slots,
                conflict_tracker, venue_allocator, cache,
                globally_scheduled_alloc_ids
            )
            merged_scheduled += sc2
            merged_unscheduled.extend(still2)
            all_scheduled_courses.extend(sl2)
            all_unscheduled_courses.extend(ul2)

        merged_rate = (merged_scheduled / max(n_merged_allocs, 1)) * 100
        safe_print(
            f"STEP 3 done: {merged_scheduled} common/merged unit allocs scheduled "
            f"({merged_rate:.1f}%), {len(merged_unscheduled)} groups unschedulable"
        )
        update_progress(80, "STEP 3 done: Common/merged unit scheduling complete",
                        indiv_scheduled + merged_scheduled, len(merged_unscheduled),
                        console_message=f"Common units: {merged_scheduled}/{n_merged_allocs} ({merged_rate:.1f}%)",
                        scheduled_courses=all_scheduled_courses,
                        unscheduled_courses=all_unscheduled_courses)

        total_ug_scheduled = merged_scheduled + indiv_scheduled
        ug_unschedulable = merged_unscheduled + indiv_unscheduled
        all_ug_scheduled = (len(ug_unschedulable) == 0)

        # Intelligence Metrics pre-PG
        metrics_pre_pg = compute_regular_feasibility_metrics(
            all_tasks, all_venues, days, slots,
            conflict_tracker,
            scheduled_count=total_ug_scheduled,
            unscheduled_ug=len(ug_unschedulable),
            unscheduled_pg=n_pg_allocs,
            merge_attempts=n_merged_groups,
            merge_successes=merged_scheduled // max(
                len([t for t in all_tasks if isinstance(t, dict)]), 1) if n_merged_groups > 0 else 0,
        )
        safe_print(f"[Metrics pre-PG] {metrics_pre_pg.to_dict()}")

        # STEP 4: PG SCHEDULING
        update_progress(82, "STEP 4: Scheduling PG courses (deferred, last)...",
                        total_ug_scheduled, n_pg,
                        console_message=f"PG: {n_pg} tasks ({n_pg_allocs} allocs) | "
                                        f"free-slot-fill={'ON' if all_ug_scheduled else 'OFF'}",
                        scheduled_courses=all_scheduled_courses,
                        unscheduled_courses=all_unscheduled_courses)

        pg_scheduled = 0
        deferred_pg_list: List[str] = []

        if pg_tasks:
            sc4, still_pg, sl4, ul4 = process_pg_phase(
                pg_tasks, all_venues, days, slots,
                conflict_tracker, venue_allocator, cache,
                all_ug_scheduled=all_ug_scheduled,
                globally_scheduled_alloc_ids=globally_scheduled_alloc_ids,
            )
            pg_scheduled = sc4
            all_scheduled_courses.extend(sl4)
            all_unscheduled_courses.extend(ul4)

            pg_rate = (pg_scheduled / max(n_pg_allocs, 1)) * 100
            safe_print(f"STEP 4 done: {pg_scheduled}/{n_pg_allocs} PG allocs scheduled ({pg_rate:.1f}%)")
            update_progress(90, "STEP 4 done: PG scheduling complete",
                            total_ug_scheduled + pg_scheduled, len(still_pg),
                            console_message=f"PG: {pg_scheduled}/{n_pg_allocs} ({pg_rate:.1f}%)",
                            scheduled_courses=all_scheduled_courses,
                            unscheduled_courses=all_unscheduled_courses)

        # STEP 5: EXHAUSTIVE FREE-SLOT SWEEP (with dissolve logic)
        sweep_placed = 0
        sweep_truly_unschedulable: List[str] = []

        update_progress(
            95,
            "STEP 5: Exhaustive Free-Slot Sweep…",
            total_ug_scheduled + pg_scheduled,
            total_courses - (total_ug_scheduled + pg_scheduled),
            console_message="Phase 5 Sweep: exhaustive day×slot×venue search with dissolve for merged groups",
            scheduled_courses=all_scheduled_courses,
            unscheduled_courses=all_unscheduled_courses,
        )

        sweep_placed, sweep_sl, sweep_ul = process_exhaustive_sweep(
            all_tasks, all_venues, days, slots,
            conflict_tracker, venue_allocator, cache,
            all_courses=all_courses,
            merge_limit=merge_limit,
            globally_scheduled_alloc_ids=globally_scheduled_alloc_ids,
            analysis_report=analysis_report,
        )
        all_scheduled_courses.extend(sweep_sl)
        sweep_truly_unschedulable = sweep_ul
        all_unscheduled_courses = sweep_ul

        safe_print(
            f"STEP 5 done: sweep placed={sweep_placed} | "
            f"truly unschedulable={len(sweep_truly_unschedulable)}"
        )
        update_progress(
            98,
            "STEP 5 done: Exhaustive sweep complete",
            total_ug_scheduled + pg_scheduled + sweep_placed,
            len(sweep_truly_unschedulable),
            console_message=(
                f"Sweep placed: {sweep_placed} | "
                f"Truly unschedulable: {len(sweep_truly_unschedulable)}"
            ),
            scheduled_courses=all_scheduled_courses,
            unscheduled_courses=all_unscheduled_courses,
        )

        # STEP 5a1: SIBLING CO-LOCATION FORCE PASS
        # Runs BEFORE overload relief, deliberately — it's the cheapest and
        # safest recovery available: if this exact lecturer is already
        # teaching this exact course somewhere on the grid, join the
        # sibling's slot instead of burning a second venue/timeslot. See
        # process_sibling_colocation_force docstring for the full rationale
        # (this is the "force it into the sibling's slot" behaviour).
        sibling_colocation_placed = 0
        try:
            sibling_colocation_placed, sib_sl, sib_ul = process_sibling_colocation_force(
                all_tasks, all_courses, all_venues, days, slots,
                conflict_tracker, venue_allocator, cache,
                globally_scheduled_alloc_ids,
                merge_limit=merge_limit,
            )
            if sib_sl:
                all_scheduled_courses.extend(sib_sl)
            if sib_ul:
                sweep_truly_unschedulable = [
                    m for m in sweep_truly_unschedulable
                    if not any(m.startswith(u.split(' —')[0]) for u in sib_ul)
                ] + sib_ul
                all_unscheduled_courses = sweep_truly_unschedulable
            sweep_placed += sibling_colocation_placed
            safe_print(
                f"STEP 5a1 done: sibling-colocation placed={sibling_colocation_placed}"
            )
        except Exception as _sib_exc:
            safe_print(f"[SiblingColocation] Pass failed, skipping: {_sib_exc}")

        # STEP 5a2: LECTURER-OVERLOAD RELIEF PASS
        # Targets only the subset of still-unscheduled courses whose
        # lecturer carries an abnormal total load (> overload_threshold
        # units — a normal load tops out around 7). These are usually
        # unplaceable purely because one lecturer's units mathematically
        # can't all avoid overlapping, often because near-duplicate
        # sections were never merged. See process_lecturer_overload_relief
        # docstring for exactly what it does and does not relax.
        overload_relief_placed = 0
        try:
            overload_relief_placed, relief_sl, relief_ul = process_lecturer_overload_relief(
                all_tasks, all_courses, all_venues, days, slots,
                conflict_tracker, venue_allocator, cache,
                globally_scheduled_alloc_ids,
                merge_limit=merge_limit,
                overload_threshold=10,
            )
            if relief_sl:
                all_scheduled_courses.extend(relief_sl)
            if relief_ul:
                sweep_truly_unschedulable = [
                    m for m in sweep_truly_unschedulable
                    if not any(m.startswith(u.split(' —')[0]) for u in relief_ul)
                ] + relief_ul
                all_unscheduled_courses = sweep_truly_unschedulable
            sweep_placed += overload_relief_placed
            safe_print(
                f"STEP 5a2 done: overload-relief placed={overload_relief_placed}"
            )
        except Exception as _relief_exc:
            safe_print(f"[OverloadRelief] Pass failed, skipping: {_relief_exc}")

        # STEP 5b: EVENING / WEEKEND DEDICATED PASS
        # Only schedules courses with is_evening_weekend=True.
        # These courses were separated out at the start and NEVER attempted
        # in any regular (Steps 1-5) pass.
        evening_weekend_placed = 0

        if evening_weekend_tasks:
            update_progress(
                98,
                "STEP 5b: Evening/Weekend class pass…",
                total_ug_scheduled + pg_scheduled + sweep_placed,
                n_ew_allocs,
                console_message=(
                    f"Evening/Weekend pass: {n_evening_weekend} tasks "
                    f"({n_ew_allocs} allocs) flagged is_evening_weekend=True"
                ),
                scheduled_courses=all_scheduled_courses,
                unscheduled_courses=all_unscheduled_courses,
            )

            ew_placed, ew_still, ew_sl, ew_ul = process_evening_weekend_overflow(
                evening_weekend_tasks,   # ONLY the flagged tasks
                all_venues,
                config,
                conflict_tracker,
                cache,
                globally_scheduled_alloc_ids,
            )
            evening_weekend_placed = ew_placed
            all_scheduled_courses.extend(ew_sl)
            # Merge EW unscheduled with the regular sweep unschedulable list
            all_unscheduled_courses = sweep_truly_unschedulable + ew_ul
            sweep_truly_unschedulable = all_unscheduled_courses

            safe_print(
                f"STEP 5b done: evening/weekend placed={ew_placed} | "
                f"still unscheduled (EW)={len(ew_still)}"
            )
            update_progress(
                99,
                "STEP 5b done: Evening/Weekend pass complete",
                total_ug_scheduled + pg_scheduled + sweep_placed + ew_placed,
                len(ew_still),
                console_message=(
                    f"Evening/Weekend placed: {ew_placed} | "
                    f"EW unschedulable: {len(ew_still)}"
                ),
                scheduled_courses=all_scheduled_courses,
                unscheduled_courses=all_unscheduled_courses,
            )

        # STEP 5c: ZERO-STUDENT SAFETY NET (not the primary route anymore)
        # Zero/NULL-enrollment allocations now go through the SAME passes as
        # everything else (Steps 1-5a2 above) with equal priority, so the vast
        # majority are already scheduled by this point. This pass is just a
        # final catch-all for any that are still genuinely unplaced afterward
        # — same collision rules as always, never relaxed.
        zero_placed = 0
        zero_scheduled_list: List[str] = []
        zero_unscheduled_list: List[str] = []

        zero_student_courses = [
            a for a in all_courses if a.id in zero_student_alloc_ids
        ]
        still_unplaced_zero = [
            a for a in zero_student_courses if a.id not in globally_scheduled_alloc_ids
        ]

        if still_unplaced_zero:
            safe_print(
                f"\nSTEP 5c: Safety-net pass for {len(still_unplaced_zero)} "
                f"still-unscheduled zero/NULL-enrollment allocation(s) "
                f"(out of {len(zero_student_courses)} total) …"
            )
            update_progress(
                99,
                f"STEP 5c: Safety net for {len(still_unplaced_zero)} remaining allocations …",
                total_ug_scheduled + pg_scheduled + sweep_placed + evening_weekend_placed,
                len(still_unplaced_zero),
                console_message=f"Zero-enrollment safety net: {len(still_unplaced_zero)} remaining",
                scheduled_courses=all_scheduled_courses,
                unscheduled_courses=all_unscheduled_courses,
            )

            venues_asc_zero = sorted(all_venues, key=lambda v: v.capacity or 0)

            for alloc in still_unplaced_zero:
                if alloc.id in globally_scheduled_alloc_ids:
                    continue

                lecturer_id = alloc.lecturer.id if alloc.lecturer else None
                program_id = alloc.program.id if alloc.program else None
                student_group_id = getattr(alloc, 'student_group_id', None)

                if cache.is_course_already_scheduled(alloc.course_code, lecturer_id, program_id, student_group_id):
                    globally_scheduled_alloc_ids.add(alloc.id)
                    continue

                try:
                    year = cache.get_course_year(alloc)
                except Exception:
                    year = 1

                # Spread across the week (lightest-loaded day first) instead
                # of always trying Monday first — this pass runs dead last,
                # after every other phase has already eaten most Mon/Tue
                # slots, so a fixed day order here caused avoidable
                # UNPLACED results even when Thu/Fri were wide open.
                day_order_zero = list(days)
                random.shuffle(day_order_zero)
                if program_id:
                    day_order_zero.sort(
                        key=lambda d: conflict_tracker.get_program_year_day_load(program_id, year, d)
                    )

                placed = False
                # HARD RULE — NEVER RELAXED: a course with number_of_students=0/NULL
                # almost always means enrollment data was never captured for that
                # allocation, NOT that no real students attend it. Treating it as
                # "nobody is really sitting in the room" and letting it silently
                # overlap another course in the same program-year was creating real
                # student timetable clashes. So this pass uses exactly the same
                # lecturer- and program-year-collision checks as every other phase,
                # with no relaxation fallback. If no genuinely free slot exists, the
                # allocation is left unplaced and flagged for manual review instead
                # of being force-placed on top of another class.
                for day in day_order_zero:
                    if placed:
                        break
                    for slot_idx, (start, end) in enumerate(slots):
                        if placed:
                            break
                        if lecturer_id and conflict_tracker.has_lecturer_conflict(
                                lecturer_id, day, slot_idx):
                            continue
                        if program_id and conflict_tracker.has_program_conflict(
                                program_id, year, day, slot_idx, new_alloc=alloc):
                            continue
                        for v in venues_asc_zero:
                            if conflict_tracker.has_venue_conflict(v.id, day, slot_idx):
                                continue
                            if cache.is_duplicate_entry(v.id, day, start, end):
                                continue
                            entry = TempTimetable(
                                course_allocation=alloc,
                                venue=v,
                                day=day,
                                start_time=start,
                                end_time=end,
                            )
                            rows = safe_bulk_create_timetable_entries([entry], cache)
                            if rows == 0:
                                continue
                            conflict_tracker.add_merged_schedule(
                                [alloc], v.id, day, slot_idx, cache)
                            globally_scheduled_alloc_ids.add(alloc.id)
                            zero_placed += 1
                            placed = True
                            zero_scheduled_list.append(
                                f"{alloc.course_code} (0 students recorded) → {v.code} ({day} {start}–{end})"
                            )
                            safe_print(
                                f"[ZeroStudent] PLACED: {alloc.course_code} → {v.code} "
                                f"({day} {start}–{end})"
                            )
                            break

                if not placed:
                    zero_unscheduled_list.append(
                        f"{alloc.course_code} (0 students recorded) — no lecturer/program-year-"
                        f"conflict-free slot available (collision checks are never relaxed; "
                        f"verify this allocation's enrollment number and/or free up venue capacity)"
                    )
                    safe_print(f"[ZeroStudent] UNPLACED: {alloc.course_code}")

            all_scheduled_courses.extend(zero_scheduled_list)
            if zero_unscheduled_list:
                all_unscheduled_courses = all_unscheduled_courses + zero_unscheduled_list

            safe_print(
                f"STEP 5c done: safety-net placed={zero_placed} | "
                f"still unplaced={len(zero_unscheduled_list)}"
            )

        # STEP 5d: UNIVERSAL FINAL SAFETY NET
        # Catches anything Sweep + OverloadRelief + EveningWeekend + ZeroStudent
        # all missed — in particular allocations with NO lecturer assigned
        # (lecturer_id=None), which OverloadRelief structurally can never
        # touch since it only acts on lecturers over the overload threshold.
        # No threshold, no lecturer requirement: every allocation still
        # missing from the timetable gets one more attempt here.
        final_safety_placed = 0
        try:
            final_safety_placed, fsn_sl, fsn_ul = process_universal_safety_net(
                all_courses, all_venues, days, slots,
                conflict_tracker, venue_allocator, cache,
                globally_scheduled_alloc_ids,
            )
            if fsn_sl:
                all_scheduled_courses.extend(fsn_sl)
            if fsn_ul:
                placed_codes = {m.split(' (Year')[0].strip() for m in fsn_sl}
                sweep_truly_unschedulable = [
                    m for m in sweep_truly_unschedulable
                    if m.split(' (Year')[0].strip() not in placed_codes
                ] + fsn_ul
                all_unscheduled_courses = sweep_truly_unschedulable
            elif final_safety_placed:
                placed_codes = {m.split(' (Year')[0].strip() for m in fsn_sl}
                sweep_truly_unschedulable = [
                    m for m in sweep_truly_unschedulable
                    if m.split(' (Year')[0].strip() not in placed_codes
                ]
                all_unscheduled_courses = sweep_truly_unschedulable
            sweep_placed += final_safety_placed
            safe_print(f"STEP 5d done: final safety-net placed={final_safety_placed}")
        except Exception as _fsn_exc:
            safe_print(f"[FinalSafetyNet] Pass failed, skipping: {_fsn_exc}")

        # ── Fire role-aware data-quality notifications with PDF report URL ────
        # Runs whenever any zero/NULL-enrollment allocations exist at all, not
        # just when the safety net had to catch stragglers — CODs/timetablers
        # should know the enrollment data needs fixing either way, even if the
        # course got scheduled successfully through the normal passes above.
        if zero_student_courses:
            already_placed_zero = len(zero_student_courses) - len(still_unplaced_zero)

            from django.urls import reverse
            from django.contrib.auth.models import User, Group
            from collections import defaultdict as _dd

            try:
                report_url = reverse('zero_student_report_pdf')
            except Exception:
                report_url = '/export/zero-student-report/'

            # ── Group zero-student courses by department ──────────────────
            dept_courses = _dd(list)
            for _alloc in zero_student_courses:
                _dept = getattr(_alloc, 'department', None)
                if _dept:
                    dept_courses[_dept].append(_alloc)

            # ── Notify each COD about their own department's zero courses ──
            try:
                from course_allocation.detect_user_department import detect_user_department as _dud
                cod_users = User.objects.filter(
                    groups__name__in=["COD", "COD Admins"]
                ).distinct()
                notified_depts = set()
                for cod_user in cod_users:
                    try:
                        cod_dept = _dud(cod_user)
                    except Exception:
                        cod_dept = None
                    if (
                        cod_dept
                        and cod_dept in dept_courses
                        and cod_dept.id not in notified_depts
                    ):
                        courses_here = dept_courses[cod_dept]
                        msg = (
                            f"⚠️ Zero-Student Courses — {cod_dept.name}: "
                            f"{len(courses_here)} course allocation(s) had 0 students "
                            f"recorded (enrollment likely not yet entered). "
                            f"Download department report: {report_url}"
                        )
                        Notification.create_for_user(msg, cod_user)
                        notified_depts.add(cod_dept.id)
                safe_print(
                    f"[ZeroStudent] COD notifications sent to "
                    f"{len(notified_depts)} department(s)."
                )
            except Exception as e:
                safe_print(f"[ZeroStudent] COD notifications failed: {e}")

            # ── Notify all timetablers with the full picture ───────────────
            try:
                tt_msg = (
                    f"⚠️ Zero-Student-Recorded Courses: {len(zero_student_courses)} "
                    f"allocation(s) had 0 students recorded (enrollment data likely "
                    f"incomplete). {already_placed_zero} scheduled normally, "
                    f"{zero_placed} placed by the safety-net pass, "
                    f"{len(zero_unscheduled_list)} still unplaced. "
                    f"Download full report: {report_url}"
                )
                for tt_group_name in ["Director Timetable", "Timetable Admins"]:
                    try:
                        grp = Group.objects.get(name=tt_group_name)
                        Notification.create_for_group(tt_msg, grp)
                    except Group.DoesNotExist:
                        pass
                safe_print(f"[ZeroStudent] Timetabler group notifications sent.")
            except Exception as e:
                safe_print(f"[ZeroStudent] Timetabler notifications failed: {e}")

        # PHASE 6: VENUE CAPACITY OPTIMISATION
        update_progress(
            99,
            "PHASE 6: Optimising venue–course capacity matching…",
            total_ug_scheduled + pg_scheduled + sweep_placed,
            len(sweep_truly_unschedulable),
            console_message="Phase 6: re-matching room sizes to class sizes",
            scheduled_courses=all_scheduled_courses,
            unscheduled_courses=all_unscheduled_courses,
        )

        venue_opt_stats = optimize_venue_assignments(days, slots)

        safe_print(
            f"PHASE 6 done: swaps={venue_opt_stats['swaps_made']} | "
            f"timeslots_optimised={venue_opt_stats['timeslots_optimised']}"
        )

        # PHASE 6B: CROSS-TIMESLOT VENUE REBALANCING
        # Phase 6 above only swaps venues among courses sharing the exact
        # same (day, slot) — a class sitting alone in an oversized room,
        # with nothing else scheduled that slot to swap with, is invisible
        # to it. This pass looks across every OTHER slot for a genuinely
        # better-fitting, conflict-free venue and relocates the course
        # there, so an oversized room actually gets freed up instead of
        # being quietly wasted on a small class all week.
        update_progress(
            99,
            "PHASE 6B: Rebalancing oversized venue assignments…",
            total_ug_scheduled + pg_scheduled + sweep_placed,
            len(sweep_truly_unschedulable),
            console_message="Phase 6B: freeing oversized rooms via cross-timeslot moves",
            scheduled_courses=all_scheduled_courses,
            unscheduled_courses=all_unscheduled_courses,
        )

        rebalance_stats = rebalance_oversized_venue_assignments(days, slots)

        safe_print(
            f"PHASE 6B done: relocated={rebalance_stats['relocated']} | "
            f"oversized_candidates={rebalance_stats['oversized_candidates']}"
        )

        # PHASE 6B2: SAME-LECTURER/SAME-COURSE CONSOLIDATION
        # Runs right after 6B (which may have just freed up larger venues)
        # and BEFORE STEP 5e's rescue retry, so any slot this pass frees up
        # by folding scattered sections back together is immediately
        # available for rescue to offer to a still-unplaced course. See
        # process_post_schedule_lecturer_course_consolidation's docstring —
        # this is what re-merges e.g. ECON 313 sections that STEP 1 planned
        # to combine but the sweep's dissolve logic later scattered across
        # separate rooms/timeslots.
        update_progress(
            99,
            "PHASE 6B2: Consolidating scattered same-lecturer sections…",
            total_ug_scheduled + pg_scheduled + sweep_placed,
            len(sweep_truly_unschedulable),
            console_message="Phase 6B2: re-merging dissolved same-course/same-lecturer sections",
            scheduled_courses=all_scheduled_courses,
            unscheduled_courses=all_unscheduled_courses,
        )
        try:
            consolidation_stats = process_post_schedule_lecturer_course_consolidation(
                days, slots, merge_limit=merge_limit
            )
        except Exception as _consol_exc:
            consolidation_stats = {
                'families_scanned': 0, 'families_consolidated': 0,
                'clusters_folded': 0, 'slots_freed': 0, 'venue_upgrades': 0, 'errors': 0,
            }
            safe_print(f"[Consolidation] Pass failed, skipping: {_consol_exc}")

        # STEP 5e: POST-REBALANCE RESCUE RETRY
        # Why this exists: process_universal_safety_net (STEP 5d) can only
        # place a course into a venue that is ALREADY sitting empty in a
        # window where its lecturer + program-year are free. If the only
        # big-enough room in that window is occupied by some unrelated,
        # much smaller class, the safety net sees "no venue available" and
        # gives up — even though that room is a bad fit for what's in it
        # and would easily hold the stuck course.
        #
        # PHASE 6B (just above) is precisely the pass that fixes that
        # mismatch: it relocates small classes out of oversized rooms into
        # better-fitting ones elsewhere, freeing the big room up. But it
        # runs AFTER STEP 5d, so any course STEP 5d had already given up on
        # never got to benefit from rooms PHASE 6B frees. This step closes
        # that gap: it re-runs the exact same last-resort logic, but only
        # for allocations still missing from the timetable at this point —
        # now that PHASE 6B may have freed exactly the room they needed.
        rescue_placed = 0
        try:
            still_missing = TempTimetable.objects.values_list(
                "course_allocation_id", flat=True
            ).distinct()
            globally_scheduled_alloc_ids.update(still_missing)
            remaining_before_rescue = [
                a for a in all_courses if a.id not in globally_scheduled_alloc_ids
            ]
            if remaining_before_rescue:
                safe_print(
                    f"\nSTEP 5e: Post-rebalance rescue retry for "
                    f"{len(remaining_before_rescue)} still-unplaced allocation(s) "
                    f"— rooms PHASE 6B just freed may now fit them…"
                )
                rescue_placed, rescue_sl, rescue_ul = process_universal_safety_net(
                    all_courses, all_venues, days, slots,
                    conflict_tracker, venue_allocator, cache,
                    globally_scheduled_alloc_ids,
                )
                if rescue_sl:
                    all_scheduled_courses.extend(rescue_sl)
                    placed_codes = {m.split(' (Year')[0].strip() for m in rescue_sl}
                    all_unscheduled_courses = [
                        m for m in all_unscheduled_courses
                        if m.split(' (Year')[0].strip() not in placed_codes
                    ]
                    sweep_truly_unschedulable = [
                        m for m in sweep_truly_unschedulable
                        if m.split(' (Year')[0].strip() not in placed_codes
                    ]
                sweep_placed += rescue_placed
                safe_print(
                    f"STEP 5e done: rescue-retry placed={rescue_placed} | "
                    f"still unplaced={len(remaining_before_rescue) - rescue_placed}"
                )
            else:
                safe_print("STEP 5e: nothing left to rescue — skipped.")
        except Exception as _rescue_exc:
            safe_print(f"[RescueRetry] Pass failed, skipping: {_rescue_exc}")

        # PHASE 6C: POST-SCHEDULE PROGRAM-YEAR COLLISION SWAP
        # Runs last of all the collision-affecting passes — after Sweep,
        # OverloadRelief, SiblingColocation, FinalSafetyNet and the
        # post-rebalance rescue retry, and after Phase 6/6B venue
        # optimisation has finished moving things around. Every genuine
        # program-year collision still on the grid at this point is
        # leftover residue from a forced pass; this is the "check after
        # finishing which courses can be swapped" step — it looks for a
        # clean empty slot to relocate the colliding course into, or an
        # unrelated course elsewhere it can trade places with. See
        # process_post_schedule_collision_swap's docstring for detail.
        update_progress(
            99,
            "PHASE 6C: Resolving leftover program-year collisions…",
            total_ug_scheduled + pg_scheduled + sweep_placed,
            len(sweep_truly_unschedulable),
            console_message="Phase 6C: relocating/swapping courses to clear forced collisions",
            scheduled_courses=all_scheduled_courses,
            unscheduled_courses=all_unscheduled_courses,
        )
        try:
            collision_swap_stats = process_post_schedule_collision_swap(days, slots)
            safe_print(
                f"PHASE 6C done: collisions_found={collision_swap_stats['collisions_found']} | "
                f"resolved_by_move={collision_swap_stats['resolved_by_move']} | "
                f"resolved_by_swap={collision_swap_stats['resolved_by_swap']} | "
                f"unresolved={collision_swap_stats['unresolved']}"
            )
        except Exception as _swap_exc:
            collision_swap_stats = {
                'collisions_found': 0, 'resolved_by_move': 0,
                'resolved_by_swap': 0, 'unresolved': 0, 'errors': 0,
            }
            safe_print(f"[CollisionSwap] Pass failed, skipping: {_swap_exc}")

        # PHASE 6.5: LECTURER SOFT PREFERENCES (day/time + venue)
        update_progress(
            99,
            "PHASE 6.5: Applying lecturer day/time & venue preferences…",
            total_ug_scheduled + pg_scheduled + sweep_placed,
            len(sweep_truly_unschedulable),
            console_message="Phase 6.5: honouring lecturer preferences where safe",
            scheduled_courses=all_scheduled_courses,
            unscheduled_courses=all_unscheduled_courses,
        )
        lecturer_pref_stats = apply_lecturer_soft_preferences_pass(days, slots, disabled_constraints)
        safe_print(
            f"PHASE 6.5 done: moved={lecturer_pref_stats['entries_moved']} | "
            f"left_in_place={lecturer_pref_stats['entries_left_in_place']}"
        )

        # PHASE 7: POST-SCHEDULING DEDUPLICATION SAFETY NET
        safe_print("\nPHASE 7: Post-scheduling deduplication safety net…")
        dedup_removed = deduplicate_timetable_entries()
        safe_print(f"PHASE 7 done: {dedup_removed} duplicate row(s) removed from DB")

        # Final summary
        total_scheduled = (
            total_ug_scheduled + pg_scheduled + sweep_placed
            + evening_weekend_placed + zero_placed
        )
        total_scheduled = max(0, total_scheduled - dedup_removed)
        overall_rate = (total_scheduled / max(total_courses, 1)) * 100
        collision_stats = conflict_tracker.get_collision_stats()

        final_metrics = compute_regular_feasibility_metrics(
            all_tasks, all_venues, days, slots,
            conflict_tracker,
            scheduled_count=total_scheduled,
            unscheduled_ug=max(0, len(ug_unschedulable) - sweep_placed),
            unscheduled_pg=max(0, n_pg_allocs - pg_scheduled - sweep_placed),
            merge_attempts=n_merged_groups,
            merge_successes=merged_scheduled,
        )

        if overall_rate >= 90:
            status = "SUCCESS"
        elif overall_rate >= 70:
            status = "PARTIAL SUCCESS"
        else:
            status = "INCOMPLETE — consider adding more venues/days"

        final_message = (
            f"{status}: {total_scheduled}/{total_courses} allocs scheduled ({overall_rate:.1f}%) | "
            f"Specialized: {spec_scheduled} | "
            f"Merged UG: {merged_scheduled}/{n_merged_allocs} | "
            f"Individual UG: {indiv_scheduled}/{indiv_total} | "
            f"Compression: {compression_scheduled} | "
            f"PG: {pg_scheduled}/{n_pg_allocs} | "
            f"Sweep (Phase 5): {sweep_placed} | "
            f"Evening/Weekend (Phase 5b): {evening_weekend_placed} | "
            f"Zero-Student (Phase 5c): placed={zero_placed}, unplaced={len(zero_unscheduled_list)} | "
            f"Truly Unschedulable: {len(sweep_truly_unschedulable)} | "
            f"Dedup removed: {dedup_removed} | "
            f"Merged groups: {n_merged_groups} ({n_merged_allocs} sections) | "
            f"Collisions: {collision_stats['detected']} detected, "
            f"{collision_stats['resolved']} resolved ({collision_stats['resolution_rate']:.1f}%) | "
            f"RoomUtil: {final_metrics.room_utilization_rate:.1%} | "
            f"SeatWaste: {final_metrics.seat_waste_percentage:.1f}% | "
            f"VenueOpt (Phase 6): {venue_opt_stats['swaps_made']} swaps across "
            f"{venue_opt_stats['timeslots_optimised']} timeslots"
        )

        update_progress(100, f"Scheduling complete ({status})",
                        total_scheduled, total_courses - total_scheduled,
                        console_message=final_message,
                        scheduled_courses=all_scheduled_courses,
                        unscheduled_courses=all_unscheduled_courses,
                        status='completed')

        safe_print(f"\n{'=' * 70}")
        safe_print("SCHEDULING COMPLETED")
        safe_print(f"{'=' * 70}")
        safe_print(f"Total allocations:       {total_courses}")
        safe_print(f"Specialization placed:   {spec_scheduled}")
        safe_print(f"Merged groups:           {n_merged_groups} groups covering {n_merged_allocs} sections")
        safe_print(f"Merged UG scheduled:     {merged_scheduled}/{n_merged_allocs}")
        safe_print(f"Individual UG sched:     {indiv_scheduled}/{indiv_total}")
        safe_print(f"Compression placed:      {compression_scheduled}")
        safe_print(f"PG scheduled:            {pg_scheduled}/{n_pg_allocs}")
        safe_print(f"Sweep (Phase 5) placed:  {sweep_placed}")
        safe_print(f"Evening/Weekend placed:  {evening_weekend_placed}")
        safe_print(f"Zero-student placed:     {zero_placed} / {n_zero} (unplaced: {len(zero_unscheduled_list)})")
        safe_print(f"Dedup rows removed:      {dedup_removed}")
        safe_print(f"Truly unschedulable:     {len(sweep_truly_unschedulable)}")
        safe_print(f"Overall rate:            {overall_rate:.1f}%")
        safe_print(f"Room utilization:        {final_metrics.room_utilization_rate:.1%}")
        safe_print(f"Seat waste:              {final_metrics.seat_waste_percentage:.1f}%")
        safe_print(f"Collision stats:         {collision_stats}")
        safe_print(f"Venue optimisation:      {venue_opt_stats['swaps_made']} swaps in "
                   f"{venue_opt_stats['timeslots_optimised']}/"
                   f"{venue_opt_stats['timeslots_examined']} timeslots")
        safe_print(f"{'=' * 70}")

        return {
            'status': 'completed',
            'message': final_message,
            'scheduled_count': total_scheduled,
            'remaining_count': total_courses - total_scheduled,
            'success_rate': overall_rate,
            'collision_stats': collision_stats,
            'intelligence_metrics': final_metrics.to_dict(),
            'pre_analysis': {
                'feasibility_score': analysis_report.feasibility_score,
                'is_feasible': analysis_report.is_feasible,
                'global_seat_deficit': analysis_report.global_seat_deficit,
                'marginal_courses': analysis_report.marginal_courses[:10],
                'overflow_candidates': analysis_report.overflow_candidates[:10],
                'cross_cohort_collisions': analysis_report.cross_cohort_collision_pairs,
                'high_collision_programs': analysis_report.high_collision_programs[:5],
                'critical_issues': analysis_report.critical_issues[:10],
                'warnings': analysis_report.warnings[:10],
                'recommendations': analysis_report.recommendations[:5],
                'cohorts_analysed': len(analysis_report.cohort_profiles),
                'lecturers_analysed': len(analysis_report.lecturer_profiles),
            },
            'deferred_pg': deferred_pg_list,
            'dedup_removed': dedup_removed,
            'phase_stats': {
                'specialization': spec_scheduled,
                'merged_ug': merged_scheduled,
                'individual_ug': indiv_scheduled,
                'compression': compression_scheduled,
                'pg_total': pg_scheduled,
                'sweep_phase5': sweep_placed,
                'evening_weekend_phase5b': evening_weekend_placed,
                'zero_student_phase5c_placed': zero_placed,
                'zero_student_phase5c_unplaced': len(zero_unscheduled_list),
                'zero_student_total': n_zero,
                'truly_unschedulable': len(sweep_truly_unschedulable),
                'dedup_removed': dedup_removed,
                'venue_opt_swaps': venue_opt_stats['swaps_made'],
                'venue_opt_timeslots': venue_opt_stats['timeslots_optimised'],
            },
            'scheduled_courses': all_scheduled_courses,
            'unscheduled_courses': sweep_truly_unschedulable,
        }

    except Exception as e:
        error_details = traceback.format_exc()
        error_msg = f"Scheduling failed: {str(e)}"
        safe_print(f"Scheduler error:\n{error_details}")
        update_progress(0, f"Error: {str(e)}", 0, total_courses or 0,
                        console_message=error_msg,
                        scheduled_courses=all_scheduled_courses,
                        unscheduled_courses=all_unscheduled_courses,
                        status='error')
        _close_scheduler_log(success=False)
        return {
            'status': 'error',
            'message': error_msg,
            'scheduled_count': 0,
            'remaining_count': total_courses or 0,
            'scheduled_courses': all_scheduled_courses,
            'unscheduled_courses': all_unscheduled_courses
        }
    finally:
        with progress_lock:
            if scheduler_progress.get('status') == 'running':
                scheduler_progress['status'] = 'completed'
        _close_scheduler_log(success=True)  # flush & close the run log
        if 'cache' in locals():
            cache.clear()


class SchedulerProgressView(View):
    def get(self, request):
        return JsonResponse(scheduler_progress)


@method_decorator(csrf_exempt, name='dispatch')
class CancelSchedulingView(View):
    def post(self, request):
        with progress_lock:
            scheduler_progress.update({
                'status': 'cancelled',
                'current_action': 'Scheduling cancelled by user',
                'message': 'Scheduling was cancelled by user'
            })
        return JsonResponse({'status': 'cancelled', 'message': 'Scheduling cancelled'})


@method_decorator(csrf_exempt, name='dispatch')
class StartSchedulingView(View):
    def post(self, request):
        global scheduler_progress
        with progress_lock:
            scheduler_progress.update({
                'status': 'running',
                'progress': 0,
                'current_action': 'Initializing smart scheduler...',
                'scheduled_count': 0,
                'remaining_count': 0,
                'batch_info': '',
                'total_courses': 0,
                'current_batch': 0,
                'total_batches': 0,
                'message': '',
                'console_output': [],
                'scheduled_courses': [],
                'unscheduled_courses': []
            })

        disabled_constraints: Set[str] = set()
        try:
            import json as _json
            body = _json.loads(request.body or b"{}")
            disabled_constraints = set(body.get('disabled_constraints') or [])
        except Exception:
            disabled_constraints = set()
        if disabled_constraints:
            safe_print(f"[Constraints] Disabled for this run only: {sorted(disabled_constraints)}")

        scheduler_thread = threading.Thread(
            target=run_optimized_autoscheduler_thread,
            args=(disabled_constraints,),
        )
        scheduler_thread.daemon = True
        scheduler_thread.start()

        return JsonResponse({'status': 'started', 'message': 'Smart scheduling started'})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def run_autoscheduler(request):
    StartSchedulingView.as_view()(request)
    return redirect('autoscheduler_progress_page')