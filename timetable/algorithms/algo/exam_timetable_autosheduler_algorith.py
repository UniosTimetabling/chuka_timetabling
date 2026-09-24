"""
Exam Auto-Scheduler Module (v77 — "1,1,1,1" rule for designated-venue courses)
CHANGES in v77:
─────────────────────────────────────────────────────────────────
Courses that are tied to designated venues (the "specials": labs etc.) are
scarce-room exams. For them the cohort pattern is now ONE designated exam per
day on CONSECUTIVE exam days (1,1,1,1,...):
  * a programme-year never gets a 2nd designated exam on a day that already
    has one of its designated exams;
  * each new designated exam must sit on the exam-day right before or right
    after a day the programme-year already uses for a designated exam (no
    empty day in between) - the block grows one day at a time;
  * applies to every programme-year regardless of size (the 15-unit "light"
    limit does not matter here); family / common courses are excluded;
  * it is level 2 of the relaxation ladder (state.compact_level): Phase0 tries
    it first, then the ordinary compact rule (level 1), then the old
    behaviour (level 0), so a designated course is never left unscheduled
    only because of this rule.
  * Phase0 places each programme-year's designated courses back-to-back,
    programme-years with the fewest designated courses first.
Switch: COMPACT_SPECIAL_ONE_PER_DAY = False restores v76 behaviour.

Exam Auto-Scheduler Module (v76 — Phase0-Designated obeys the compact rules)
CHANGES in v76 (found from a real result: 6 COSC Year-4 exams on Dec 8, 10,
14, 15, 15, 16 = 1/0/1/0/1/2/1 over 7 exam days, all in designated SRP labs):
─────────────────────────────────────────────────────────────────
 * ROOT CAUSE. designated_venue_priority_pass (Phase0) does NOT call
   _check_hard_constraints - it runs its own shorter list of checks (lecturer,
   student clash, code-day, family). So it skipped the daily cap, the cooling
   rule and (since v74) the compactness gate. It also walked courses one at a
   time in global priority order, so each course of a programme-year took
   whatever lab slot was still free at that moment; the year's exams got
   interleaved with every other programme's and scattered.
 * FIX 1. Phase0 candidates are now ordered light-programme-year first and
   GROUPED by programme-year, so a year's designated exams are placed
   back-to-back.
 * FIX 2. Phase0 now tries three levels per course before giving up on
   compactness: level 2 (daily cap + cooling + gap + cap + pair-packing),
   level 1 (same without pair-packing), level 0 (the old behaviour). A course
   is only scattered when no compact slot exists in any designated room.

Exam Auto-Scheduler Module (v75 — Even Pairs, Programme-Year Tracking)
CHANGES in v75 (fixes seen on a real 6-unit result: 1 / 3 / 1 / 1 over 4 days):
─────────────────────────────────────────────────────────────────
 1. Compaction now tracks the PROGRAMME-YEAR (state._py_key), not the
    stem-split daily-limit key. A 6-unit year split into stem sub-cohorts
    looked like several tiny cohorts, each "legally" allowed 2-3 exams a day,
    so together they piled 3 exams on one day. Units and day-usage are now
    counted over the whole programme-year.
 2. PER-DAY CAP for light cohorts: never more than
    max(COMPACT_TARGET_PER_DAY, ceil(units / exam_days)) exams a day.
    (5-6 units => 2 a day, so 1/3/1/1 becomes 2/2/2 or 2/2/1/1.)
 3. PAIR-PACKING: a light cohort may open a NEW day only when every day it
    already uses is full (has the per-day target). Days fill 2,2,2,... in
    order instead of 1,1,1,1.
 4. Relaxation ladder (compact_level): 2 = gap + cap + pair-packing
    (PhaseA, Phase0, PhaseC passes 1-2); 1 = gap + cap (PhaseC pass 3+,
    Phase3-5); 0 = off (Phase7+ leftovers). Nothing is left unscheduled
    because of compactness.
 5. Report prints each light cohort's per-day pattern, e.g. "2-2-1".

Exam Auto-Scheduler Module (v74 — Compact, Consecutive-Day Cohort Scheduling)
CHANGES in v74:
─────────────────────────────────────────────────────────────────
A programme-year ("cohort") with few units (<= LIGHT_COHORT_MAX_UNITS) used
to get its exams wherever a free slot happened to appear - e.g. 1 exam on
day 1 and the other 4 at the very end. Now:
  1. LIGHT-COHORT-FIRST: in PhaseA and the saturation/fill loops, cohorts
     with the fewest units get first pick of each day, so they start early
     and finish early (state.cohort_rank()).
  2. CONSECUTIVE-DAY GATE (state.compact_gap_ok(), enforced inside
     _check_hard_constraints for every non-family course): once a light
     cohort has an exam on some exam-day, its next exams must land
       - on a day at most COMPACT_MAX_DAY_GAP exam-days from a day it
         already uses (2 => same day, next day, or ONE empty day between),
       - and keep the overall span within
         ceil(units / COMPACT_TARGET_PER_DAY) + COMPACT_SPAN_SLACK days
         (never wider than the span it already has, so cohorts whose
         family/common courses are already spread out are not blocked
         from filling the days in between).
     Example: 5 units -> 2 + 2 + 1 over consecutive days (or 2, gap, 2, 1),
     never 1 on day 1 and the rest on days 13-14.
     "Days" are EXAM days (weekends / excluded dates are skipped).
  3. It is a preference ladder, not a wall: the gate is on for every phase
     up to Phase5, and switched off from Phase7-Nuclear onward so a course
     that cannot be placed compactly is still placed (scattered) rather
     than left unscheduled. Family / common courses are unaffected but
     their days count as anchors for the cohort.
  4. A compactness report is printed after the run.
Tunables: COMPACT_COHORT_SCHEDULING, LIGHT_COHORT_MAX_UNITS,
COMPACT_TARGET_PER_DAY, COMPACT_MAX_DAY_GAP, COMPACT_SPAN_SLACK.

Exam Auto-Scheduler Module (v72 — Middle Slot Priority for Individual Courses)
CHANGES in v72:
─────────────────────────────────────────────────────────────────
Individual (non-family, non-common) courses now try the MIDDLE slot first,
then morning, then evening, in EVERY individual-placement phase (PhaseA,
Phase0, PhaseC, Phase3, Phase4, Phase5, Phase7, Phase8, Phase9b, Phase9d).
Root cause of the empty middle slot, fixed here: the cohort "gap" rule
forbade back-to-back slots, so any cohort with an exam in the first or last
slot was blocked from the middle slot (its neighbour) and pushed to the
opposite edge. ALLOW_ADJACENT_WITH_MIDDLE_SLOT=True now allows adjacent
exams whenever one of the two slots is a middle slot (morning+middle,
middle+evening); daily hard limits are unchanged.

Exam Auto-Scheduler Module (v71 — Staged Day-Window Placement for Every Family)
CHANGES in v71:
─────────────────────────────────────────────────────────────────
Shared / common / family courses now follow ONE placement order in EVERY
phase that seats a whole family (PhaseB, Phase1, Phase3, Phase3b,
Phase4-sweep3, Phase8), implemented once in _family_stage_attempts() /
_place_family_in_stages():
  Stage A  first HALF of the days (calendar order), edge slots only
           (each day: LAST slot, then FIRST slot).
  Stage B  add the next QUARTER of days (-> 3/4), edge slots only.
  Stage C  go back to day 1 and walk up to the 3/4 mark, now trying the
           MIDDLE slots of every day.
  Stage D  the remaining last QUARTER: ANY timeslot, day and slot equal
           priority (diagonal round-robin).
  Stage E  safety net: every day / every slot not yet tried.
Why: PhaseB used to sweep slots last->first across EVERY date before
Phase1's windows were consulted, so most families were spread over the
whole exam period and the half/quarter windows almost never applied.
Days are now taken in calendar order (state.dates_in_order()), not in
daily-load-penalty order, so "first half" means the first half of the
exam period. All-or-nothing family placement is unchanged.

Exam Auto-Scheduler Module (v69 — Per-Course Decision Trace)
CHANGES in v69 (item 2 is a scheduling change; everything else is diagnostics):
─────────────────────────────────────────────────────────────────
0. The tracer no longer depends on a hard-coded TRACE_COURSE_CODES list.
   EVERY course now keeps a decision journal for the whole run (each slot
   check that rejected it, with phase / function / reason; each placement
   with venue, phase, call chain and why that venue was chosen; each later
   move). After the last phase, write_course_trace_reports() writes
   logs/exam_scheduler_trace_<session>.txt: a full day-by-day,
   timeslot-by-timeslot breakdown for every UNSCHEDULED course (final-state
   blockers, what each phase recorded, room-by-room picture) and a
   where/when/why entry for every scheduled one. Unscheduled dossiers are
   also written to the main run log. TRACE_COURSE_CODES is now optional and
   only adds a live console echo for the codes listed.
2. DAILY-LIMIT LAST-RESORT RELAXATION (Phase9d-DailyLimitRelaxation). The hard
   per-day cap on a cohort / lecturer (2-3 exams) was enforced in
   _check_hard_constraints and NEVER relaxed by any phase, so a course whose
   only obstacle was that cap stayed unscheduled forever. After every normal
   phase has run, the new pass retries each still-unscheduled non-family
   course with ONLY that cap lifted: every other rule (student/lecturer clash,
   family & shared-unit locks, venue space) must still pass, the LEAST-
   overloaded slot is chosen, and each relaxed placement is logged. Normal
   phases still enforce the cap. DAILY_LIMIT_RELAX_MAX_EXTRA caps how far over
   the limit it may go (None = no cap).
1. _explain_collision_reason() (diagnostic mirror of exam_is_collision_exempt)
   still carried the OLD category gate after the v68 stem fix, so it reported
   "NOT EXEMPT" for different-stem pairs the scheduler now exempts. Synced to
   the v68 rule (different stems => exempt, category is not a gate). The trace
   explains every student-conflict via the live rule and flags any drift.

Exam Auto-Scheduler Module (v66 — Multi-Group Student-Overlap Fix)
CRITICAL FIX in v66:
─────────────────────────────────────────────────────────────────
0. exam_is_collision_exempt()'s StudentGroup check (and the mirrored
   _explain_collision_reason() diagnostic) used to read only the single
   primary `student_group_id` FK on each course_allocation. But
   CourseAllocation also carries `additional_student_groups` (M2M) for
   the COD panel's bulk multi-group mapping flow, and exposes the
   combined set via `all_mapped_student_groups`. A row mapped to more
   than one StudentGroup was invisible to the old single-FK compare —
   its real cohort membership could overlap the other course's even
   when the two *primary* FKs differed, or could be wrongly treated as
   overlapping when they happened to share just the primary FK while
   the actual sets didn't overlap at all. This directly affects any
   different-stem/different-category pair (e.g. cross-category subject
   combinations) that falls through to the StudentGroup check to decide
   real overlap.
   Fixed by _exam_get_student_group_ids(), which reads the full
   `all_mapped_student_groups` set on each side; two courses are now
   exempt via this rule only when both sides have at least one group
   mapped AND the two sets share none, and are correctly kept
   NOT-exempt whenever any group is common to both sets.

Exam Auto-Scheduler Module (v65 — One-Course-One-Family Fix)
CRITICAL FIX in v65:
─────────────────────────────────────────────────────────────────
0. analyze_courses() no longer lets a course_allocation belong to TWO
   overlapping "family" entries at once. Same course code across the
   WHOLE UNIVERSITY is still one family that must sit in one timeslot
   (v63 behavior, unchanged) — but a CombinedCourseGroup that touches
   rows already inside that family used to get registered as a SECOND,
   parallel shared_unit_groups entry for the same rows. Every fallback
   phase treats each shared_unit_groups entry as its own atomic
   all-or-nothing family, so that smaller overlapping entry could get
   placed and committed on its own before the real, full family ever
   got its one shot — which is exactly how e.g. 3 of 14 BOTA 111
   sections landed on the timetable while the other 11 (same exam,
   same course code) were stranded with no room left. Overlapping
   CombinedCourseGroup members are now unioned into the one existing
   norm-code family (repointing every touched code at the merged
   member list) instead of creating a second entry; a combined group is
   only registered standalone when none of its members already belong
   to a code-wide family.

Exam Auto-Scheduler Module (v64 — No-Unnecessary-Split + Seat-Accounting Fixes)
CRITICAL FIXES in v64:
─────────────────────────────────────────────────────────────────
1. FAMILY SEATING PLANNER (_plan_family_seating, used by
   _commit_distributed_minimal).
   Every course variant is first offered ONE whole venue (best-fit
   decreasing: biggest variant first, tightest sufficient room, same-label
   variants preferred in the same room, preferred building first). Only a
   variant that genuinely fits no single remaining venue is split, and it
   is split across as FEW venues as possible. Occupied venues are checked
   with _can_share_venue for every variant (the old code skipped that
   check for the general pool).  A 165-student course now lands in one
   200-seat room instead of being sliced across two.

1b. NO SPACING RATIO. Exam seats = Venue.exam_capacity exactly. The old
   exam_capacity * ExamSchedulerConfig.spacing_ratio (default 0.7) shrank
   a 200-seat room to 140, forcing splits. Venues without an exam_capacity
   are no longer loaded or used at all (no fallback to physical capacity).

2. TRANSACTION-SAFE COMMIT.
   If a consume_venue() fails part-way through a family commit, seats that
   were already consumed are now released and state.placed_families is
   rolled back (previously the state leaked phantom occupancy).
   place_multi_venue got the same rollback, and no longer counts one split
   course several times toward daily_load / lecturer counters.

3. SEAT ACCOUNTING USES allocated_students (migration 0007).
   rebuild_state_from_db, audit_slot_vs_db, the capacity audit, the
   duplicate-placement audit and the small-venue consolidation pass used
   course_student_count() for EVERY row. For a course split across two
   rooms that charged the FULL enrollment to each room, so after any
   rebuild those rooms looked full and later passes were forced into even
   more splits.  Rows now use allocated_students, falling back to the
   course total only for legacy NULL rows.

4. merge_back_split_placements_pass FIXED + EXTENDED.
   * It used only the FIRST old venue's seats for the whole course and
     wrote that number into the merged row (wrong allocated_students).
   * It only merged when the ENTIRE family fit one room. It now also runs
     a per-course stage: any single course still split across rooms is
     pulled into one room if one can hold all of it.

5. consolidate_underfilled_small_venues_pass no longer moves a row that
   belongs to a split course (moving one half breaks the split).

Carried over from v63: family partial-together placement, stem-aware
collision logic (v62), immediate family exhaustion (v61), stem-aware daily
limits.
"""
from __future__ import annotations
import datetime
import re
import logging
import threading
import inspect
import sys as _sys
import time
import math
from pathlib import Path
from collections import defaultdict
from functools import lru_cache
from typing import Dict, FrozenSet, List, Optional, Set, Tuple, Any

scheduler_logger = logging.getLogger("scheduler")

from django.db import IntegrityError, OperationalError, transaction
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

DEBUG_VERBOSE: bool = True

# ======================================================================
# Course decision trace (v69)
# ======================================================================
# Every course now keeps its own decision journal for the whole run:
#   * every slot check that REJECTED it (date, slot, reason, which phase,
#     which function, and any cheap detail such as the venue involved),
#   * every placement that SEATED it (venue, date, slot, which phase and
#     call chain committed it, and WHY that venue was picked),
#   * every later move (evacuation / consolidation / swap / merge-back).
# After the last phase, write_course_trace_reports() turns the journals
# into a per-course dossier:
#   * UNSCHEDULED courses get a full day-by-day, timeslot-by-timeslot
#     breakdown: the blockers that apply to that slot in the FINAL state
#     ("now"), what every phase recorded against that slot during the run
#     ("run"), and a venue-by-venue picture of why no room could take it.
#   * SCHEDULED courses get where/when/why they were placed and what was
#     rejected on the way there.
# Nothing here changes scheduling decisions — it only records and prints.
#
# TRACE_COURSE_CODES is now OPTIONAL: any code listed also gets a live
# console line for each event as it happens. The dossiers above do NOT
# depend on it — they cover every course.
TRACE_ENABLED: bool = True
TRACE_UNSCHEDULED_FULL: bool = True          # full slot-by-slot dossier for EVERY unscheduled course
TRACE_SCHEDULED_DOSSIERS: bool = True        # placement dossier for every scheduled course (trace file only)
TRACE_ECHO_UNSCHEDULED_TO_MAIN_LOG: bool = True   # also write unscheduled dossiers to the main run log (file only, not console)
TRACE_VENUE_DETAIL: bool = True              # per-slot venue-by-venue picture for unscheduled courses
TRACE_MAX_KEYS_PER_COURSE: int = 2000        # memory guard: distinct (date, slot, reason) rows kept per course
TRACE_REPORT_TIME_BUDGET_SEC: float = 600.0  # after this, venue detail is skipped for the remaining courses
TRACE_COURSE_CODES: Set[str] = set()         # optional live-echo filter (empty = no live echo)

_JOURNAL: Dict[int, "_CourseJournal"] = {}
_CURRENT_PHASE: str = "pre-run"
_PHASE_ORDER: List[str] = []
_PICK_NOTE: Dict[int, str] = {}
_EXTRA_NOTE: Dict[int, str] = {}   # e.g. 'daily limit relaxed' - appended to the venue note
_TRACE_SUSPENDED: int = 0
_trace_file_handle = None
_trace_file_path = None


class _CourseJournal:
    __slots__ = ("rejects", "placements", "moves", "phase_notes", "overflow")

    def __init__(self):
        # (date, slot_start, reason) -> [count, {(phase, where): n}, [details] or None]
        self.rejects: Dict[Tuple, list] = {}
        self.placements: List[dict] = []
        self.moves: List[dict] = []
        # (phase, reason) -> count   (slot-independent notes)
        self.phase_notes: Dict[Tuple[str, str], int] = {}
        self.overflow: int = 0


class _trace_suspended:
    """Context manager: journaling is paused (used while diagnostics
    re-run the same checks the phases use, so they don't pollute the
    record of what the phases actually did)."""
    def __enter__(self):
        global _TRACE_SUSPENDED
        _TRACE_SUSPENDED += 1
        return self

    def __exit__(self, *exc):
        global _TRACE_SUSPENDED
        _TRACE_SUSPENDED -= 1
        return False


def _trace_reset() -> None:
    global _CURRENT_PHASE
    _JOURNAL.clear()
    _PHASE_ORDER.clear()
    _PICK_NOTE.clear()
    _CURRENT_PHASE = "pre-run"


def _trace_set_phase(name: str) -> None:
    global _CURRENT_PHASE
    _CURRENT_PHASE = name
    if name and name not in _PHASE_ORDER and not name.startswith("between"):
        _PHASE_ORDER.append(name)


def _journal_for(course) -> "_CourseJournal":
    j = _JOURNAL.get(course.id)
    if j is None:
        j = _JOURNAL[course.id] = _CourseJournal()
    return j


def _tcode(course) -> str:
    return (getattr(course, "course_code", "") or "").strip() or f"id={getattr(course, 'id', '?')}"


def _jr(course, date, ss, reason: str, where: str = "", detail: str = "") -> None:
    """Record ONE rejection. date/ss = None records a slot-independent
    note for the current phase (e.g. 'family member skipped')."""
    if not TRACE_ENABLED or _TRACE_SUSPENDED:
        return
    try:
        j = _journal_for(course)
        if date is None:
            k = (_CURRENT_PHASE, reason)
            j.phase_notes[k] = j.phase_notes.get(k, 0) + 1
            return
        key = (date, ss, reason)
        rec = j.rejects.get(key)
        if rec is None:
            if len(j.rejects) >= TRACE_MAX_KEYS_PER_COURSE:
                j.overflow += 1
                return
            rec = j.rejects[key] = [0, {}, None]
            if TRACE_COURSE_CODES and _tcode(course) in TRACE_COURSE_CODES:
                print(f"[TRACE] {_tcode(course)} x REJECTED date={date} slot={ss} "
                      f"reason={reason} phase={_CURRENT_PHASE} via={where or '-'} {detail}")
        rec[0] += 1
        pw = (_CURRENT_PHASE, where)
        d = rec[1]
        d[pw] = d.get(pw, 0) + 1
        if detail:
            dl = rec[2]
            if dl is None:
                rec[2] = [detail]
            elif len(dl) < 4 and detail not in dl:
                dl.append(detail)
    except Exception:
        pass  # tracing must never be able to break scheduling


def _jr_family(group_courses, offender, date, ss, reason: str, where: str = "") -> None:
    """A family is all-or-nothing: one member's blocker blocks everyone.
    The offender gets the real reason; the rest get 'family-blocked-by-member'."""
    if not TRACE_ENABLED or _TRACE_SUSPENDED:
        return
    off_txt = f"{_tcode(offender)}: {reason}"
    for m in group_courses:
        if m is offender or m.id == offender.id:
            _jr(m, date, ss, reason, where)
        else:
            _jr(m, date, ss, "family-blocked-by-member", where, off_txt)


def _jr_all(group_courses, date, ss, reason: str, where: str = "", detail: str = "") -> None:
    for m in group_courses:
        _jr(m, date, ss, reason, where, detail)


def _note_pick(course, text: str) -> None:
    if TRACE_ENABLED and not _TRACE_SUSPENDED and course is not None:
        _PICK_NOTE[course.id] = text


def _jr_move(course, date, ss, from_venue, to_venue, kind: str, why: str = "") -> None:
    if not TRACE_ENABLED or _TRACE_SUSPENDED:
        return
    try:
        _journal_for(course).moves.append({
            "phase": _CURRENT_PHASE, "kind": kind, "date": date, "ss": ss,
            "from": getattr(from_venue, "code", from_venue),
            "to": getattr(to_venue, "code", to_venue), "why": why,
        })
        if TRACE_COURSE_CODES and _tcode(course) in TRACE_COURSE_CODES:
            print(f"[TRACE] {_tcode(course)} -> {kind} "
                  f"{getattr(from_venue, 'code', from_venue)} -> {getattr(to_venue, 'code', to_venue)} "
                  f"date={date} slot={ss} phase={_CURRENT_PHASE} {why}")
    except Exception:
        pass


def _trace_placement(course, venue, date, ss) -> None:
    """Called by every commit site. Journals the placement for EVERY
    course (phase, call chain, why this venue); live-echoes only for
    codes listed in TRACE_COURSE_CODES."""
    if not TRACE_ENABLED or _TRACE_SUSPENDED:
        return
    try:
        chain: List[str] = []
        f = _sys._getframe(1)
        while f is not None and len(chain) < 7:
            n = f.f_code.co_name
            if n != "_trace_placement":
                chain.append(n)
            f = f.f_back
        vcode = getattr(venue, "code", venue)
        note = _PICK_NOTE.pop(course.id, "")
        _extra = _EXTRA_NOTE.pop(course.id, "")
        if _extra:
            note = f"{note} | {_extra}" if note else _extra
        _journal_for(course).placements.append({
            "phase": _CURRENT_PHASE, "chain": chain, "venue": vcode,
            "date": date, "ss": ss, "note": note,
        })
        code = _tcode(course)
        if TRACE_COURSE_CODES and code in TRACE_COURSE_CODES:
            print(f"[TRACE] {code} -> venue={vcode} date={date} slot={ss} "
                  f"phase={_CURRENT_PHASE} via: {' <- '.join(chain)}"
                  + (f" | why: {note}" if note else ""))
    except Exception:
        pass


# ---- trace file -----------------------------------------------------

def _trace_open():
    global _trace_file_handle, _trace_file_path
    if _trace_file_handle:
        return _trace_file_handle
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    sid = _log_session_id or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _trace_file_path = log_dir / f"exam_scheduler_trace_{sid}.txt"
    _trace_file_handle = open(_trace_file_path, "w", encoding="utf-8")
    return _trace_file_handle


def _trace_emit(lines, main_log: bool = False) -> None:
    try:
        fh = _trace_open()
        txt = "\n".join(lines) if not isinstance(lines, str) else lines
        fh.write(txt + "\n")
        if main_log:
            _write_log(txt)
    except Exception:
        pass


def _trace_close() -> None:
    global _trace_file_handle
    if _trace_file_handle:
        try:
            _trace_file_handle.flush()
            _trace_file_handle.close()
        except Exception:
            pass
        _trace_file_handle = None


def get_trace_file_path():
    return str(_trace_file_path) if _trace_file_path else None


# ---- report helpers -------------------------------------------------

_PLACEMENT_PHASES = [
    "PhaseA-SmallFirst", "PhaseB-CommonLast", "Phase1-FamiliesFirst",
    "Phase3b-FamilyRescue", "Phase0-Designated", "PhaseC-Saturation",
    "Phase3-CrossDayFill", "Phase4-ForcedFallback", "Phase5-DBFallback",
    "Phase7-Nuclear", "Phase8-Ultimate", "Phase8b-SharedRoomLastResort",
    "Phase9b-PostConsolidationRescue",
    "Phase9d-DailyLimitRelaxation",
]


def _phase_eligibility(phase: str, course, is_family: bool, needed: int, state) -> Optional[str]:
    """Why a phase would never have looked at this course (by design in
    the phase's own filter). None = the course was eligible."""
    nc = state._norm_code(course)
    fam_only_atomic = ("family member — families are only ever placed whole, "
                       "in one atomic all-or-nothing step, never one section at a time")
    if phase == "PhaseA-SmallFirst":
        if is_family:
            return "not eligible: " + fam_only_atomic
        if needed >= 100:
            return f"not eligible: {needed} students (this phase only takes courses under 100 students)"
        return None
    if phase in ("PhaseB-CommonLast", "Phase1-FamiliesFirst", "Phase3b-FamilyRescue"):
        if not is_family:
            return "not eligible: not part of a same-code family (this phase only places families)"
        return None
    if phase == "Phase0-Designated":
        if is_family:
            return "not eligible: " + fam_only_atomic
        if nc not in state.designated_venues_by_norm_code:
            return "not eligible: no designated-venue rule exists for this course code"
        return None
    if phase in ("PhaseC-Saturation", "Phase3-CrossDayFill", "Phase4-ForcedFallback",
                 "Phase5-DBFallback", "Phase7-Nuclear", "Phase9b-PostConsolidationRescue"):
        if is_family:
            return ("individual pass skipped this course: " + fam_only_atomic +
                    " (its family is retried whole by the family phases)")
        return None
    if phase == "Phase9d-DailyLimitRelaxation":
        if is_family:
            return ("not eligible: family members are never blocked by the per-day cap "
                    "(the family placement path does not check it)")
        return None
    return None


def _fmt_date(d) -> str:
    try:
        return d.strftime("%a %Y-%m-%d")
    except Exception:
        return str(d)


def _fmt_slot(ss, se_map) -> str:
    se = se_map.get(ss)
    try:
        return f"{ss.strftime('%H:%M')}-{se.strftime('%H:%M')}" if se else ss.strftime("%H:%M")
    except Exception:
        return str(ss)


def _reason_summary(reason_counts: Dict[str, int], limit: int = 6) -> str:
    items = sorted(reason_counts.items(), key=lambda kv: -kv[1])[:limit]
    return ", ".join(f"{r}×{n}" for r, n in items) if items else "-"


def _rec_phases(rec) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for (ph, _w), n in rec[1].items():
        out[ph] = out.get(ph, 0) + n
    return out


def _rec_wheres(rec) -> List[str]:
    seen: List[str] = []
    for (_ph, w), _n in rec[1].items():
        if w and w not in seen:
            seen.append(w)
    return seen


def _share_blocker_fast(course, vid, date, ss, state, course_by_id) -> Optional[str]:
    """Same decision as _can_share_venue, but O(1) course lookup (the
    original scans every course per occupant — far too slow to call for
    every venue of every slot of every unscheduled course). Returns None
    if the course COULD share the venue, else the blocking occupant's code."""
    if not state.venue_has_occupants(vid, date, ss):
        return None
    my_nc = normalize_course_code(getattr(course, "course_code", "") or "")
    for occ_id in state.get_venue_occupants(vid, date, ss):
        occ = course_by_id.get(occ_id)
        if not occ:
            continue
        if my_nc == normalize_course_code(getattr(occ, "course_code", "") or ""):
            continue
        if _combined_group_are_paired(course.id, occ.id):
            continue
        if not state.allow_cross_course_sharing:
            return _tcode(occ)          # v73: different course in the room
        if courses_share_students(course, occ):
            return _tcode(occ)
    return None


def _short_collision_why(c1, c2) -> str:
    """One-line reason a pair of courses is treated as sharing students,
    taken from _explain_collision_reason (which mirrors the live stem-aware
    exemption rules). If the mirror says EXEMPT for a pair the scheduler
    actually blocked on, that is a drift bug between the two — flag it."""
    try:
        r = _explain_collision_reason(c1, c2)
    except Exception as exc:
        return f"(could not explain: {exc})"
    if r.startswith("NOT EXEMPT (default)"):
        return "same programme+year, no stem / student-group / selection-group separation"
    if r.startswith("EXEMPT"):
        return f"⚠ diagnostic says '{r[:110]}' but exam_is_collision_exempt still blocked it — rules have drifted"
    return r if len(r) <= 200 else r[:197] + "..."


def _student_blockers_explained(course, date, ss, state, limit: int = 3) -> str:
    """Names the already-seated course(s) that clash with this course's
    cohort at date/ss AND says why they count as sharing students, using
    the live exam_is_collision_exempt / _explain_collision_reason rules
    (so any change to the stem logic shows up here automatically)."""
    pk = state._py_key(course)
    if not pk:
        return "(no programme+year key on this course)"
    out: List[str] = []
    seen: Set[str] = set()
    extra = 0
    for b in state._py_busy_allocs.get((date, ss, pk), []):
        try:
            if exam_is_collision_exempt(course, b):
                continue
        except Exception:
            pass
        code = _tcode(b)
        if code in seen:
            continue
        seen.add(code)
        if len(out) < limit:
            out.append(f"{code} ({_short_collision_why(course, b)})")
        else:
            extra += 1
    if not out:
        return _describe_student_conflict_blockers(course, date, ss, state)
    return "; ".join(out) + (f"; (+{extra} more)" if extra else "")


def _cohort_profile(course) -> str:
    try:
        stems = sorted(_exam_get_specialization_stem_ids(course))
        groups = sorted(_exam_get_student_group_ids(course))
        sg = _exam_get_selection_group_id(course)
        intake = _exam_get_intake(course)
    except Exception as exc:
        return f"(unavailable: {exc})"
    return f"stems={stems or 'none'}  student_groups={groups or 'none'}  selection_group={sg}  intake={intake}"


def _venue_picture(courses, date, ss, state, need: int, course_by_id, only_vids=None) -> dict:
    """Venue-by-venue view of one slot for these courses (one course, or
    every unscheduled member of a family, all of which must fit)."""
    usable_total = 0
    best = None
    excluded = []
    full = 0
    for v in state.venues_by_cap_desc:
        if only_vids is not None and v.id not in only_vids:
            continue
        rem = state.venue_remaining(v.id, date, ss)
        if rem <= 0:
            full += 1
            continue
        blocker = None
        if state.venue_has_occupants(v.id, date, ss):
            for c in courses:
                b = _share_blocker_fast(c, v.id, date, ss, state, course_by_id)
                if b:
                    blocker = (c, b)
                    break
        if blocker:
            excluded.append((v.code, rem, _tcode(blocker[0]), blocker[1]))
            continue
        usable_total += rem
        if best is None or rem > best[1]:
            best = (v.code, rem, state.venue_examcap.get(v.id, 0))
    return {"usable_total": usable_total, "best": best, "excluded": excluded, "full": full}


def _evaluate_slot(course, date, ss, state, lid, is_family: bool, lec_map) -> Tuple[list, list]:
    """(hard, soft) reasons that apply to this course at this slot in the
    FINAL state. Unlike _check_hard_constraints this does NOT stop at the
    first failing rule — every applicable rule is listed."""
    hard: List[Tuple[str, str]] = []
    soft: List[Tuple[str, str]] = []
    nc = state._norm_code(course)
    if course.id in state.strict_locked_ids and not state.allow_strict_rescue:
        hard.append(("strict-designated-venue-unavailable",
                     "code is STRICTLY tied to its designated venues and none could take it"))
    if not state.students_available(course, date, ss):
        hard.append(("student-conflict",
                     "same cohort already sitting: " + _student_blockers_explained(course, date, ss, state)))
    if state.check_family_conflict(course, date, ss):
        locked = state.family_slot.get(state.family_key(course))
        hard.append(("family-conflict",
                     f"this course+programme is already bound to {_fmt_date(locked[0])} {locked[1]}" if locked else "bound elsewhere"))
    if state.check_shared_unit_conflict(course, date, ss):
        locked = state.get_shared_unit_lock(course)
        hard.append(("shared-unit-conflict",
                     f"code '{nc}' is locked to {_fmt_date(locked[0])} {locked[1]} (all sections must sit together)" if locked else "locked elsewhere"))
    if state.check_norm_code_day_conflict(course, date):
        ld = state.norm_code_day_lock.get(nc)
        hard.append(("norm-code-day-conflict",
                     f"code '{nc}' is locked to day {_fmt_date(ld)}" if ld else "day locked elsewhere"))
    if lid and not state.lecturer_available(lid, date, ss, course):
        who = lec_map.get((date, ss, lid), "(marked busy but no matching timetable row — check lecturer bookkeeping)")
        hard.append(("lecturer-conflict", f"lecturer_id={lid} already examining {who}"))
    if not is_family:
        if state.is_daily_limit_hard_exceeded(course, date):
            pk = state._daily_limit_key(course)
            parts = []
            if pk:
                _, hl = state.cohort_daily_limits.get(pk, (2, 3))
                cnt = state.cohort_daily_count.get((pk, date), 0)
                if cnt >= hl:
                    parts.append(f"cohort already has {cnt} exam(s) that day (hard limit {hl})")
            if lid:
                _, lhl = state.lecturer_daily_limits.get(lid, (2, 3))
                lcnt = state.lecturer_daily_count.get((lid, date), 0)
                if lcnt >= lhl:
                    parts.append(f"lecturer_id={lid} already has {lcnt} exam(s) that day (hard limit {lhl})")
            hard.append(("daily-limit-hard-exceeded", "; ".join(parts) or "daily limit reached"))
        if state.cohort_in_cooling(course, date, ss):
            soft.append(("cohort-cooling",
                         "cohort has an exam in an adjacent slot (only blocks phases that don't relax the consecutive-slot rule)"))
    return hard, soft


def _phase_history_lines(course, j, is_family: bool, needed: int, state) -> List[str]:
    L: List[str] = ["  PHASE HISTORY  (what each phase did with this course)"]
    by_phase: Dict[str, Dict[str, int]] = {}
    for (d, sl, r), rec in j.rejects.items():
        for ph, n in _rec_phases(rec).items():
            by_phase.setdefault(ph, {})
            by_phase[ph][r] = by_phase[ph].get(r, 0) + n
    notes_by_phase: Dict[str, List[str]] = {}
    for (ph, r), n in j.phase_notes.items():
        notes_by_phase.setdefault(ph, []).append(f"{r}×{n}")
    phases_to_show = [p for p in _PHASE_ORDER if p in _PLACEMENT_PHASES or p in by_phase or p in notes_by_phase]
    blockers: List[str] = []
    for (d, sl, r), rec in j.rejects.items():
        if r == "family-blocked-by-member" and rec[2]:
            for det in rec[2]:
                if det not in blockers and len(blockers) < 4:
                    blockers.append(det)
    for ph in phases_to_show:
        if ph in by_phase:
            total = sum(by_phase[ph].values())
            extra = f"; notes: {', '.join(notes_by_phase[ph])}" if ph in notes_by_phase else ""
            if "family-blocked-by-member" in by_phase[ph] and blockers:
                extra += f"; blocked by another section — {' | '.join(blockers)}"
            L.append(f"    {ph:<34} {total} rejected check(s): {_reason_summary(by_phase[ph])}{extra}")
        else:
            why = _phase_eligibility(ph, course, is_family, needed, state)
            if ph in notes_by_phase:
                L.append(f"    {ph:<34} {', '.join(notes_by_phase[ph])}")
            elif why:
                L.append(f"    {ph:<34} {why}")
            else:
                L.append(f"    {ph:<34} eligible, but no slot check was recorded against this course "
                         f"(every slot was filtered out earlier — e.g. no free venue space at all, or the "
                         f"course was already skipped by the phase's own pre-filter)")
    if j.overflow:
        L.append(f"    (journal capped: {j.overflow} further distinct rejection rows were not stored)")
    for mv in j.moves:
        L.append(f"    {mv['phase']:<34} {mv['kind']}: {mv['from']} -> {mv['to']} {mv['why']}")
    return L


def _build_dossier_unscheduled(course, state, course_by_id, final_placements, lec_map,
                               all_unscheduled_ids, deadline: float, family_ref: Optional[str] = None) -> List[str]:
    j = _JOURNAL.get(course.id) or _CourseJournal()
    code = _tcode(course)
    needed = course_student_count(course)
    lid = state._cached_lecturer_id(course)
    nc = state._norm_code(course)
    fam_ids = state.analysis.shared_unit_groups.get(nc)
    is_family = bool(fam_ids)
    se_map = {s: e for s, e in state.all_slots_ordered}
    prog = getattr(course, "program", None)
    prog_name = getattr(prog, "name", None) or "?"

    fam_members: List = [course]
    fam_need = needed
    if is_family:
        fam_members = [course_by_id[cid] for cid in fam_ids
                       if cid in course_by_id and cid in all_unscheduled_ids]
        if not fam_members:
            fam_members = [course]
        fam_need = family_total_students(fam_members)

    L: List[str] = []
    L.append("=" * 100)
    L.append(f"[UNSCHEDULED] {code}   id={course.id}   students={needed}   "
             f"programme={prog_name} / {get_program_year(course)}   lecturer_id={lid}")
    if is_family:
        L.append(f"  FAMILY: code '{nc}' has {len(fam_ids)} section(s) across programmes; "
                 f"{len(fam_members)} still unscheduled ({fam_need} students in total).")
        L.append("          A family sits in ONE slot, all sections together, or not at all — so every "
                 "slot below is judged for the WHOLE family, and one section's blocker blocks all of them.")
    if family_ref:
        # The family verdict (every slot, every section's blockers) was already printed in full for the
        # first unscheduled section — repeating it per section would only bloat the file.
        L.append(f"  → Same FAMILY verdict as {family_ref}: the slot-by-slot breakdown for the whole family "
                 f"(all sections, tagged by section code) is printed under that entry.")
        L.append("")
        L.extend(_phase_history_lines(course, j, is_family, needed, state))
        L.append("")
        return L
    L.append(f"  COHORT PROFILE: {_cohort_profile(course)}")
    L.append("    (two courses in the same programme+year share students unless: different stems, disjoint "
             "student groups, same selection group, distinct special intakes, or combined-group pairing)")
    desig = state.designated_venues_for_course(course)
    strict = nc in state.strict_norm_codes
    only_vids = None
    if desig:
        codes = [state.venue_by_id[v].code for v in desig if v in state.venue_by_id]
        L.append(f"  DESIGNATED VENUES: {codes} ({'STRICT — may use only these' if strict else 'preferred, not exclusive'})")
        if strict and not state.allow_strict_rescue:
            only_vids = set(desig)

    dates = state.dates_in_order()
    slots = state.all_slots_ordered
    grid = []   # (date, ss, hard, soft, venue_pic, need_here)
    reason_slot_counts: Dict[str, int] = {}
    open_slots = 0
    nearest: List[Tuple] = []
    with_venue = TRACE_VENUE_DETAIL and time.time() < deadline
    for date_obj, _wd in dates:
        for ss, _se in slots:
            hard, soft = [], []
            if is_family:
                for m in fam_members:
                    mh, _ms = _evaluate_slot(m, date_obj, ss, state, state._cached_lecturer_id(m), True, lec_map)
                    for r, d in mh:
                        hard.append((r, (f"[{_tcode(m)}] " if len(fam_members) > 1 else "") + d))
                # keep the list readable: one line per distinct reason (+ count of members)
                merged: Dict[str, List[str]] = {}
                for r, d in hard:
                    merged.setdefault(r, []).append(d)
                hard = []
                for r, ds in merged.items():
                    shown = "; ".join(ds[:3]) + (f"; (+{len(ds) - 3} more section(s))" if len(ds) > 3 else "")
                    hard.append((r, shown))
            else:
                hard, soft = _evaluate_slot(course, date_obj, ss, state, lid, False, lec_map)
            pic = None
            if with_venue:
                pic = _venue_picture(fam_members if is_family else [course], date_obj, ss, state,
                                     fam_need if is_family else needed, course_by_id, only_vids)
            need_here = fam_need if is_family else needed
            venue_block = None
            if pic is not None:
                if pic["usable_total"] < need_here:
                    venue_block = "no-venue-space-for-full-count"
                elif not is_family and (pic["best"] is None or pic["best"][1] < need_here):
                    soft.append(("needs-split",
                                 f"no single venue has {need_here} free (largest usable: {pic['best'][0]} {pic['best'][1]})"
                                 if pic["best"] else "no single venue has room"))
            all_block = [r for r, _ in hard] + ([venue_block] if venue_block else [])
            for r in all_block:
                reason_slot_counts[r] = reason_slot_counts.get(r, 0) + 1
            if not all_block:
                open_slots += 1
            elif len(all_block) == 1:
                nearest.append((date_obj, ss, all_block[0]))
            grid.append((date_obj, ss, hard, soft, pic, venue_block, need_here))

    total_slots = len(grid)
    L.append("")
    L.append(f"  SUMMARY  ({len(dates)} exam day(s) × {len(slots)} slot(s) = {total_slots} slots checked against the final timetable)")
    if open_slots == 0:
        L.append(f"    Every slot is blocked. Slots affected, by reason: {_reason_summary(reason_slot_counts, 10)}")
        if reason_slot_counts:
            everywhere = sorted(r for r, n in reason_slot_counts.items() if n == total_slots)
            if len(everywhere) == 1:
                L.append(f"    → '{everywhere[0]}' alone rules out ALL {total_slots} slots — that is the root cause.")
            elif everywhere:
                L.append(f"    → each of these rules out ALL {total_slots} slots on its own: {', '.join(everywhere)} "
                         f"— all of them would have to be resolved.")
            else:
                top_r, top_n = max(reason_slot_counts.items(), key=lambda kv: kv[1])
                L.append(f"    → biggest blocker: '{top_r}' ({top_n}/{total_slots} slots).")
    else:
        L.append(f"    ⚠ {open_slots}/{total_slots} slot(s) show NO blocker in the final state, yet the course was not placed. "
                 f"Either state changed after the phases ran (space freed late) or a placement rule rejected it "
                 f"for a reason not modelled here — see the 'run' lines on those slots.")
        L.append(f"    Other slots blocked by: {_reason_summary(reason_slot_counts, 10)}")
    nearest = [n for n in nearest if n[2] != "no-venue-space-for-full-count"]
    if nearest and len(nearest) < total_slots:
        L.append("    Nearest misses (exactly ONE constraint blocks the slot — cheapest to fix by hand):")
        for d, ss, r in nearest[:6]:
            L.append(f"      - {_fmt_date(d)} {_fmt_slot(ss, se_map)} : only '{r}'")

    L.append("")
    L.extend(_phase_history_lines(course, j, is_family, needed, state))

    # ---- day-by-day
    L.append("")
    L.append("  DAY-BY-DAY, TIMESLOT-BY-TIMESLOT")
    current_date = None
    for date_obj, ss, hard, soft, pic, venue_block, need_here in grid:
        if date_obj != current_date:
            current_date = date_obj
            L.append(f"  ── {_fmt_date(date_obj)} ──")
        blocked = bool(hard or venue_block)
        tag = "BLOCKED" if blocked else ("OPEN (soft issues only)" if soft else "OPEN")
        L.append(f"    {_fmt_slot(ss, se_map)}  {tag}")
        for r, d in hard:
            L.append(f"        now : ✗ {r} — {d}")
        if venue_block and pic is not None:
            msg = (f"needs {need_here} seats, only {pic['usable_total']} usable across all venues "
                   f"({pic['full']} venue(s) already full")
            if pic["excluded"]:
                msg += f", {len(pic['excluded'])} excluded because their occupant shares students"
            msg += ")"
            L.append(f"        now : ✗ no-venue-space-for-full-count — {msg}")
        for r, d in soft:
            L.append(f"        now : ~ {r} — {d}")
        if pic is not None and (blocked or soft):
            best_txt = (f"best usable single room {pic['best'][0]} has {pic['best'][1]}/{pic['best'][2]} free"
                        if pic["best"] else "no usable room with free seats")
            if hard:
                # Slot is already ruled out by a non-venue constraint: rooms are secondary, keep it to one line.
                ex = f"; {len(pic['excluded'])} room(s) also excluded by shared-student occupants" if pic["excluded"] else ""
                L.append(f"        venues (secondary — slot already ruled out above): {best_txt}{ex}")
            else:
                L.append(f"        venues: {best_txt}")
                for vcode, rem, mycode, occ in pic["excluded"][:6]:
                    L.append(f"        venues: {vcode} ({rem} seats free) unusable — its occupant {occ} shares students with {mycode}")
                if len(pic["excluded"]) > 6:
                    L.append(f"        venues: (+{len(pic['excluded']) - 6} more room(s) excluded the same way)")
        # what the phases recorded at this slot during the run
        run_rows = [(r, rec) for (d, s, r), rec in j.rejects.items() if d == date_obj and s == ss]
        if run_rows:
            for r, rec in sorted(run_rows, key=lambda x: -x[1][0]):
                phs = ", ".join(f"{p}×{n}" for p, n in _rec_phases(rec).items())
                via = ", ".join(_rec_wheres(rec))
                det = f" [{'; '.join(rec[2])}]" if rec[2] else ""
                L.append(f"        run : {r} ×{rec[0]} — phases: {phs}" + (f" — via {via}" if via else "") + det)
        else:
            L.append("        run : no phase recorded an attempt at this slot (it was filtered out before any per-slot "
                     "check — e.g. the slot had no free venue space, or the phase does not handle this course)")
    L.append("")
    return L


def _build_dossier_scheduled(course, state, course_by_id, final_placements) -> List[str]:
    j = _JOURNAL.get(course.id) or _CourseJournal()
    code = _tcode(course)
    se_map = {s: e for s, e in state.all_slots_ordered}
    L: List[str] = []
    L.append("-" * 100)
    L.append(f"[SCHEDULED] {code}   id={course.id}   students={course_student_count(course)}")
    fin = final_placements.get(course.id, [])
    for (d, ss, se, vcode, seats) in fin:
        L.append(f"  FINAL   : {_fmt_date(d)} {_fmt_slot(ss, se_map)}  venue {vcode}  ({seats} seat(s))")
    # journalled placements, grouped per slot
    seen = set()
    for p in j.placements:
        key = (p["date"], p["ss"], p["phase"], tuple(p["chain"]))
        if key in seen:
            continue
        seen.add(key)
        venues = sorted({q["venue"] for q in j.placements
                         if q["date"] == p["date"] and q["ss"] == p["ss"] and q["phase"] == p["phase"]})
        L.append(f"  PLACED  : {_fmt_date(p['date'])} {_fmt_slot(p['ss'], se_map)} in {', '.join(map(str, venues))} "
                 f"by {p['phase']}")
        L.append(f"            call chain: {' <- '.join(p['chain'])}")
        if p["note"]:
            L.append(f"            why this venue: {p['note']}")
    if not j.placements:
        L.append("  PLACED  : (no commit was journalled for this course — it was written by a path without a trace hook, "
                 "or was already in the timetable when the run started)")
    for mv in j.moves:
        L.append(f"  MOVED   : {mv['kind']} in {mv['phase']}: {mv['from']} -> {mv['to']} {mv['why']}")
    # rejections that happened before it landed
    final_keys = {(d, ss) for (d, ss, _se, _v, _s) in fin}
    rows = [(k, rec) for k, rec in j.rejects.items()]
    if rows:
        by_reason: Dict[str, List[Tuple]] = {}
        for (d, ss, r), rec in rows:
            by_reason.setdefault(r, []).append((d, ss, rec))
        total = sum(rec[0] for _k, rec in rows)
        L.append(f"  BEFORE  : {total} rejected check(s) across {len(rows)} slot/reason row(s) before it was seated:")
        for r, lst in sorted(by_reason.items(), key=lambda kv: -sum(x[2][0] for x in kv[1])):
            cnt = sum(x[2][0] for x in lst)
            slots_txt = ", ".join(
                f"{_fmt_date(d)} {_fmt_slot(ss, se_map)}" + ("*" if (d, ss) in final_keys else "")
                for d, ss, _rec in sorted(lst, key=lambda x: (x[0], x[1]))[:8])
            more = f" (+{len(lst) - 8} more)" if len(lst) > 8 else ""
            phs: Dict[str, int] = {}
            for _d, _s, rec in lst:
                for p, n in _rec_phases(rec).items():
                    phs[p] = phs.get(p, 0) + n
            L.append(f"            - {r} ×{cnt} [{', '.join(f'{p}×{n}' for p, n in phs.items())}]: {slots_txt}{more}")
        if final_keys:
            L.append("            (* = rejected earlier at the slot it finally got, e.g. by a stricter phase; a later phase accepted it)")
    return L


def write_course_trace_reports(all_courses, state) -> Optional[str]:
    """Write the per-course dossiers. Returns the trace file path."""
    if not TRACE_ENABLED:
        return None
    started = time.time()
    with _trace_suspended():
        try:
            rows = list(_temp_qs().values(
                "course_allocation_id", "date", "start_time", "end_time",
                "venue__code", "allocated_students",
                "course_allocation__course_code", "course_allocation__lecturer_id"))
        except Exception as exc:
            print(f"[Trace] could not read the final timetable: {exc}")
            return None
        final_placements: Dict[int, List[Tuple]] = defaultdict(list)
        lec_map: Dict[Tuple, str] = {}
        for r in rows:
            final_placements[r["course_allocation_id"]].append(
                (r["date"], r["start_time"], r["end_time"], r["venue__code"], r["allocated_students"]))
            lid = r.get("course_allocation__lecturer_id")
            if lid:
                lec_map[(r["date"], r["start_time"], lid)] = r["course_allocation__course_code"] or f"id={r['course_allocation_id']}"
        scheduled_final: Set[int] = set(final_placements.keys())
        course_by_id = {c.id: c for c in all_courses}
        unscheduled = [c for c in all_courses if c.id not in scheduled_final]
        scheduled = [c for c in all_courses if c.id in scheduled_final]
        unscheduled.sort(key=lambda c: -state.priority_score(c))
        unscheduled_ids = {c.id for c in unscheduled}

        header = [
            "=" * 100,
            "EXAM SCHEDULER — PER-COURSE DECISION TRACE",
            f"Generated: {datetime.datetime.now().isoformat()}",
            f"Courses: {len(all_courses)}   scheduled: {len(scheduled)}   UNSCHEDULED: {len(unscheduled)}",
            f"Phases that ran: {', '.join(_PHASE_ORDER) if _PHASE_ORDER else '(none recorded)'}",
            "",
            "How to read this file:",
            "  UNSCHEDULED courses: for every exam day and every timeslot —",
            "    now : the blockers that apply to that slot in the FINAL timetable state (✗ = hard, ~ = soft/only some phases)",
            "    run : what each phase actually recorded against that slot while it was trying (count, phase, function)",
            "    venues: the room-level picture (best room, rooms excluded because an occupant shares students)",
            "  SCHEDULED courses: final slot/venue, which phase + call chain committed it, why that venue, and what",
            "    was rejected on the way.",
            "=" * 100, "",
        ]
        _trace_emit(header)

        if unscheduled:
            listing = ["UNSCHEDULED COURSES (highest priority first):"]
            for c in unscheduled:
                listing.append(f"  - {_tcode(c)} (id={c.id}, {course_student_count(c)} students)")
            listing.append("")
            _trace_emit(listing, main_log=TRACE_ECHO_UNSCHEDULED_TO_MAIN_LOG)
            if TRACE_ECHO_UNSCHEDULED_TO_MAIN_LOG:
                _write_log("")
                _write_log("#" * 100)
                _write_log("# PER-COURSE TRACE: WHY EACH UNSCHEDULED COURSE WAS NOT PLACED (slot by slot)")
                _write_log("#" * 100)
        deadline = started + TRACE_REPORT_TIME_BUDGET_SEC
        if TRACE_UNSCHEDULED_FULL:
            family_first: Dict[str, str] = {}
            for c in unscheduled:
                try:
                    _nc = state._norm_code(c)
                    _ref = None
                    if _nc in state.analysis.shared_unit_groups:
                        if _nc in family_first:
                            _ref = family_first[_nc]
                        else:
                            family_first[_nc] = _tcode(c)
                    lines = _build_dossier_unscheduled(c, state, course_by_id, final_placements, lec_map,
                                                       unscheduled_ids, deadline, family_ref=_ref)
                except Exception as exc:
                    import traceback
                    lines = [f"[UNSCHEDULED] {_tcode(c)} id={c.id} — dossier failed: {exc}", traceback.format_exc()]
                _trace_emit(lines, main_log=TRACE_ECHO_UNSCHEDULED_TO_MAIN_LOG)
        if TRACE_SCHEDULED_DOSSIERS:
            _trace_emit(["", "#" * 100, "# SCHEDULED COURSES — where, when and why", "#" * 100])
            for c in sorted(scheduled, key=lambda c: (_tcode(c), c.id)):
                try:
                    lines = _build_dossier_scheduled(c, state, course_by_id, final_placements)
                except Exception as exc:
                    lines = [f"[SCHEDULED] {_tcode(c)} id={c.id} — dossier failed: {exc}"]
                _trace_emit(lines)
        _trace_emit(["", f"Trace report built in {time.time() - started:.1f}s."])
        _trace_close()
    print(f"[Trace] Per-course decision trace written: {get_trace_file_path()} "
          f"({len(unscheduled)} unscheduled dossier(s), {len(scheduled)} scheduled)")
    return get_trace_file_path()

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
    _write_log("EXAM AUTO-SCHEDULER LOG v71")
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
    _trace_close()
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

# v64: a venue's seating for exams is EXACTLY Venue.exam_capacity — no
# spacing ratio, no fallback to physical capacity. A venue with no
# exam_capacity (NULL or 0) has 0 exam seats and is never used by the
# exam scheduler. ExamSchedulerConfig.spacing_ratio is ignored here.
def venue_exam_capacity(venue) -> int:
    ec = getattr(venue, "exam_capacity", None)
    return int(ec) if ec and int(ec) > 0 else 0

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

def _row_seats(allocated_students, course) -> int:
    """Seats ONE ExamTempTimetable row accounts for.

    allocated_students (migration 0007) holds this venue's share of the
    course. Only legacy rows written before that column existed have NULL;
    for those (and only those) fall back to the course's full enrollment.
    Using course_student_count() unconditionally charges a split course's
    FULL enrollment to every venue it sits in.
    """
    if allocated_students:
        return int(allocated_students)
    return course_student_count(course) if course is not None else 0

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
    """DEPRECATED single-pointer read. `CourseAllocation.specialization_stem`
    is only the "primary" stem pointer, and course_allocation/
    specialization_stem_views.py deliberately clears it to None the moment a
    course belongs to MORE than one stem (a cross-listed course shown under
    several "Also in: ..." stems in the allocation UI — e.g. a course shared
    between "chemistry, physics option" and "Math/chemistry option"). Reading
    only this pointer makes such a course look stem-less, so it falls through
    to the plain program+year clash check and gets wrongly flagged as
    colliding with courses from unrelated stems/options it has nothing to do
    with. Use `_exam_get_specialization_stem_ids` (the real M2M membership)
    for any exemption decision — kept only for compatibility with any
    remaining non-decision callers (e.g. the daily-quota bucket key below)."""
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.id if st else None
    except Exception:
        return None

def _exam_get_specialization_category_id(alloc) -> Optional[int]:
    """DEPRECATED — see `_exam_get_specialization_stem_id` docstring."""
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.category_id if st else None
    except Exception:
        return None

_exam_stem_ids_cache: dict[int, frozenset] = {}
_exam_stem_category_ids_cache: dict[int, frozenset] = {}

def _exam_get_specialization_stem_ids(alloc) -> frozenset:
    """Real SpecializationStem MEMBERSHIP (M2M: `alloc.specialization_stems`,
    the reverse of `SpecializationStem.courses`) — reflects EVERY stem a
    course belongs to, unlike the often-nulled singular `specialization_stem`
    pointer read by `_exam_get_specialization_stem_id` above. This is what
    collision-exemption must use; same fix as `_exam_get_student_group_ids`
    already applies to student_group/additional_student_groups, and as
    `_specialization_stem_ids_of` applies in timetable_panel.py."""
    aid = getattr(alloc, "id", None)
    if aid is None:
        return frozenset()
    cached = _exam_stem_ids_cache.get(aid)
    if cached is not None:
        return cached
    try:
        ids = frozenset(alloc.specialization_stems.values_list('id', flat=True))
    except Exception:
        ids = frozenset()
    _exam_stem_ids_cache[aid] = ids
    return ids

def _exam_get_specialization_category_ids(alloc) -> frozenset:
    """Every SpecializationCategory reachable from this allocation's real
    stem membership (M2M-derived, plural)."""
    aid = getattr(alloc, "id", None)
    if aid is None:
        return frozenset()
    cached = _exam_stem_category_ids_cache.get(aid)
    if cached is not None:
        return cached
    try:
        ids = frozenset(
            cid for cid in alloc.specialization_stems.values_list('category_id', flat=True)
            if cid is not None
        )
    except Exception:
        ids = frozenset()
    _exam_stem_category_ids_cache[aid] = ids
    return ids

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

def _exam_get_student_group_ids(alloc) -> frozenset:
    """
    v66 FIX: every StudentGroup this allocation is actually mapped to —
    the primary `student_group` FK PLUS every group in
    `additional_student_groups` (the COD panel's bulk multi-group
    mapping flow — "map_allocation_groups_and_stems" — deliberately lets
    one lettered allocation carry more than one StudentGroup). The model
    already exposes this as the `all_mapped_student_groups` property for
    exactly this reason.

    Previously this scheduler (and the identical helper in the conflict
    panel) read `student_group_id` alone, so any row that reached the
    multi-group flow was invisible here: its real membership could
    include a group that overlaps the other course's cohort even though
    the single *primary* FK looked different, or vice versa — either
    direction silently mis-judges the exemption.
    """
    try:
        # v70: primary + additional groups, plus (for a course that only lives
        # in restricted elective pools) the groups those pools are restricted to.
        return _exh_group_ids(alloc)
    except Exception:
        gid = getattr(alloc, "student_group_id", None)
        return frozenset([gid]) if gid else frozenset()

from course_allocation.exemption_helpers import (
    share_pool as _exh_share_pool,
    effective_group_ids as _exh_group_ids,
    reset_membership_cache as _exh_reset,
)

def _combined_group_are_paired(alloc_a_id: int, alloc_b_id: int) -> bool:
    a_groups = _combined_group_ids_for(alloc_a_id)
    if not a_groups:
        return False
    b_groups = _combined_group_ids_for(alloc_b_id)
    return bool(a_groups & b_groups)

_combined_group_cache: dict[int, frozenset] = {}

def _build_combined_group_cache() -> dict[str, list[int]]:
    global _combined_group_cache
    _exh_reset()
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
    CRITICAL (v64.3): Correct stem-aware collision logic.

    Rules (in priority order):
    1. Same normalized course code → NOT exempt (must be scheduled
       together as a family, even across different stems/programs/intakes)
    2. Different intakes, BOTH explicitly tagged as special → exempt
       (two confirmed distinct, disjoint cohorts). A tagged course vs.
       an untagged ("normal") one does NOT exempt — "normal" is not a
       specific cohort, just "not flagged on this row".
    3. Same stem → NOT exempt (same cohort shares students)
    4. Different stems (any category, or unknown/mismatched category)
       → exempt unconditionally — a student picks exactly ONE stem,
       so two different stems are always disjoint cohorts (v68 FIX,
       see below)
    5. Different explicit student groups → exempt
    6. Same selection group → exempt
    7. Combined group pairing → exempt

    v64.1 FIX: rule 4/5 previously exempted ANY two courses with
    different (non-null) specialization_stem_id, with no regard for
    category. That let genuinely combinable stems (different
    categories, e.g. an Arts subject-combination pair) skip the
    student-conflict check entirely, silently double-booking real
    students. This now matches the category-aware logic already used
    in dual_campus_exam_autosheduler_algorithm.py and in the conflict
    report's exam_panel_is_exempt() (exam_timetable_panel.py), which
    is what was catching these as conflicts after the fact.

    v68 FIX: the v64.1 category gate over-corrected. Requiring BOTH
    stems' categories to overlap before exempting meant a different-
    stem pair whose categories differed, or where either stem had no
    category at all, fell through and got wrongly judged a real clash
    — even though different stems are non-overlapping cohorts by
    definition, independent of category. Category was never the right
    gate for this rule; it's removed. (Root-caused via the same bug in
    exam_panel_is_exempt() — see that function's v-numbered fix note in
    exam_timetable_panel.py.)

    v64.2 FIX: the same-course-code rule (1) used to run AFTER the
    intake rule (now 2), so two rows of the exact same course code but
    tagged with different intakes (e.g. one section moved onto a
    Special Intake group) were judged exempt via intake alone and never
    reached the family rule at all — see AGED 314C / BCOM 351-A.

    v64.3 FIX: rule 2 previously exempted whenever the two intake keys
    simply differed, which included "special_X" vs "normal". Since
    "normal" only means "not explicitly tagged on this row", that
    silently exempted any specially-tagged course from colliding with
    the entire untagged rest of its program/year — exactly what let
    BCOM 351-A (special_intake_group_id=9) sail past a real clash with
    AGED 314C (untagged) in the same Year 3 Agribusiness cohort. Now
    both sides must be explicitly, distinctly tagged before exempting.
    """
    # CRITICAL: Check same course code FIRST — same codes must always be
    # scheduled together as one family, regardless of intake, stem, or
    # program differences. (v64.2 FIX: this check used to run AFTER the
    # intake check below, despite this comment already saying it should
    # be first — so two rows of the exact same course code but tagged
    # with different intakes, e.g. one row moved onto a Special Intake
    # group, were judged "exempt" via intake alone and never reached this
    # rule at all. Same course code now always wins first, exactly as
    # documented.)
    nc1 = normalize_course_code(getattr(c1, "course_code", "") or "")
    nc2 = normalize_course_code(getattr(c2, "course_code", "") or "")
    if nc1 and nc2 and nc1 == nc2:
        # Same course code → NOT exempt (must be scheduled together)
        return False

    # Different intakes are exempt ONLY when BOTH sides are explicitly
    # tagged as special (only reached once we know the two courses are
    # NOT the same family).
    #
    # v64.3 FIX: this used to exempt whenever the two intake keys simply
    # differed, which includes "special_9" vs "normal". But "normal" just
    # means "not explicitly flagged on THIS row" — it is not itself a
    # specific, disjoint cohort, so a course tagged with a special intake
    # got silently exempted from colliding with the ENTIRE untagged rest
    # of that program/year's course list (e.g. BCOM 351-A tagged
    # special_intake_group_id=9 vs AGED 314C, untagged, both Year 3
    # Agribusiness Management). Only exempt when we can actually confirm
    # two distinct, explicitly-tagged cohorts on both sides.
    intake1 = _exam_get_intake(c1)
    intake2 = _exam_get_intake(c2)
    if intake1 != intake2 and intake1 != "normal" and intake2 != "normal":
        return True

    # v70 NESTED ELECTIVE GROUP: two alternatives of the same pick-one pool
    # are never both taken by one student, so they never clash — even when
    # both are members of the same stem (a stem of 12 units where 10 are core
    # and the student picks one of the last two). Checked BEFORE the shared-
    # stem rule; alternative vs core unit shares no pool so still clashes.
    if _exh_share_pool(c1, c2):
        return True

    # Now check stems for different course codes — against the FULL M2M
    # membership (v67 FIX), not the singular `specialization_stem` pointer:
    # a course cross-listed into more than one stem has that pointer
    # cleared to None, so reading only the pointer made it look stem-less
    # and left it wrongly clashing against unrelated stems/options (see
    # `_exam_get_specialization_stem_id` docstring).
    stems1 = _exam_get_specialization_stem_ids(c1)
    stems2 = _exam_get_specialization_stem_ids(c2)

    # Shares at least one stem → NOT exempt (they share students)
    if stems1 and stems2 and (stems1 & stems2):
        return False

    # v68 FIX: No shared stem → exempt UNCONDITIONALLY, no category check.
    # A student commits to exactly ONE stem in a program+year; two
    # DIFFERENT stems are, by definition, two disjoint groups of students,
    # regardless of which SpecializationCategory either stem happens to be
    # filed under. Category was never the right gate here — this mirrors
    # the same category-gate bug found and fixed in exam_panel_is_exempt()
    # (exam_timetable_panel.py): requiring cat1 & cat2 to overlap made
    # different-stem pairs with unknown/different categories fall through
    # and get wrongly treated as a real clash (e.g. ECON 446 vs ECON 442,
    # one stem-tagged, one in a bare "economics" grouping with no shared
    # category) — needlessly blocking placements the two exams could
    # safely share, or forcing them apart for no real reason.
    if stems1 and stems2:
        return True

    # StudentGroup: compare the FULL set of mapped groups on each side, not
    # just the single primary FK (v66 FIX — see _exam_get_student_group_ids).
    # Exempt only when BOTH sides have at least one explicit group mapped
    # AND the two sets share no group at all; any shared group means a real
    # cohort sits both, so it must NOT be exempt.
    stg1 = _exam_get_student_group_ids(c1)
    stg2 = _exam_get_student_group_ids(c2)
    if stg1 and stg2 and not (stg1 & stg2):
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

# Course codes to trace with a full, per-date/slot, human-readable failure
# explanation in schedule_families_first (see _diagnose_family_failure_detailed
# below) — naming exactly which other course/cohort blocked each attempt and
# why exam_is_collision_exempt judged the pair NOT exempt. Populate with any
# normalized code (no spaces, e.g. "ECON306") you need root-caused; leave
# empty to disable (adds no overhead when empty).
FAMILY_DIAGNOSE_CODES: Set[str] = {"ECON306"}

def _explain_collision_reason(c1, c2) -> str:
    """Mirrors exam_is_collision_exempt's own rule order, but returns WHY
    that verdict was reached instead of just True/False — so a "these two
    collide" result can be checked by hand against the actual stem/
    student-group/selection-group data instead of taken on faith."""
    nc1 = normalize_course_code(getattr(c1, "course_code", "") or "")
    nc2 = normalize_course_code(getattr(c2, "course_code", "") or "")
    if nc1 and nc2 and nc1 == nc2:
        return "same course code -> same family, must always collide"

    intake1 = _exam_get_intake(c1)
    intake2 = _exam_get_intake(c2)
    if intake1 != intake2 and intake1 != "normal" and intake2 != "normal":
        return f"EXEMPT — distinct tagged special intakes ({intake1} vs {intake2})"

    if _exh_share_pool(c1, c2):
        return "EXEMPT — same nested elective (pick-one) group -> a student takes only one"

    st1 = _exam_get_specialization_stem_ids(c1)
    st2 = _exam_get_specialization_stem_ids(c2)
    if st1 and st2 and (st1 & st2):
        return f"shares specialization_stem(s) {sorted(st1 & st2)} -> same cohort, real clash"

    if st1 and st2:
        # v68 stem fix (mirrors exam_is_collision_exempt): no shared stem and
        # both sides have stems => disjoint cohorts, category is irrelevant.
        return (f"EXEMPT — different stems ({sorted(st1)} vs {sorted(st2)}) -> a student "
                f"commits to exactly one stem, so different stems are disjoint cohorts "
                f"(category is not a gate)")

    stg1 = _exam_get_student_group_ids(c1)
    stg2 = _exam_get_student_group_ids(c2)
    if stg1 and stg2 and not (stg1 & stg2):
        return f"EXEMPT — no overlap in mapped student_groups ({sorted(stg1)} vs {sorted(stg2)})"
    if stg1 and stg2 and (stg1 & stg2):
        return (f"same student_group(s) overlap {sorted(stg1 & stg2)} "
                f"(full sets {sorted(stg1)} vs {sorted(stg2)}) -> real clash")

    sg1 = _exam_get_selection_group_id(c1)
    sg2 = _exam_get_selection_group_id(c2)
    if sg1 is not None and sg2 is not None and sg1 == sg2:
        return f"EXEMPT — same selection_group ({sg1}) -> alternative picks"

    if _combined_group_are_paired(c1.id, c2.id):
        return "EXEMPT — paired via CombinedCourseGroup"

    if not st1 and not st2:
        stem_note = "neither row belongs to any specialization_stem"
    elif not st1 or not st2:
        stem_note = f"only one side has specialization_stem(s) ({sorted(st1)} vs {sorted(st2)})"
    else:
        stem_note = f"stems on both sides ({sorted(st1)} vs {sorted(st2)}) — unreachable, handled above"
    return (f"NOT EXEMPT (default) — same program+year, {stem_note}, no "
            f"non-overlapping student_group mapping, no matching "
            f"selection_group, not combined-group paired -> treated as "
            f"sharing students. If this "
            f"pair genuinely has zero student overlap, the fix is to add the "
            f"missing stem/category, student_group, or selection_group tag "
            f"to the data, not to change this logic.")

def _cohort_label(course) -> str:
    prog = getattr(course, "program", None)
    prog_name = getattr(prog, "name", None) or (f"program_{prog.id}" if prog else "no_program")
    return f"{prog_name} {get_program_year(course)}"

def _diagnose_family_failure_detailed(nc, group_courses, state, all_courses,
                                      scheduled_ids, sorted_dates, slot_rounds):
    """Verbose, human-readable trace of exactly why `nc` could not be
    placed: for every date/slot attempted (same order the scheduler used),
    names which other already-placed course was occupying each member's
    cohort slot, how many students that blocking course has, and why
    exam_is_collision_exempt did not treat the pair as disjoint. Also
    reports, once, how many total exams each involved cohort already
    carries across the whole exam period. Gated behind FAMILY_DIAGNOSE_CODES
    — only runs for codes explicitly listed there."""
    print(f"\n[FamilyDiagnose] ===== '{nc}' — why every slot failed =====")

    # Cohort load summary: how many exams (placed + still pending) does
    # each involved program+year cohort carry in total?
    cohort_courses: Dict[str, List] = defaultdict(list)
    for c in all_courses:
        pk = state._py_key(c)
        if pk:
            cohort_courses[pk].append(c)
    involved_pks = {state._py_key(c) for c in group_courses if state._py_key(c)}
    for pk in involved_pks:
        members = cohort_courses.get(pk, [])
        placed_ct = sum(1 for m in members if m.id in scheduled_ids)
        label = _cohort_label(next((m for m in members if state._py_key(m) == pk), group_courses[0]))
        print(f"  [Cohort] {label} (key={pk}): {len(members)} total exams "
              f"this period, {placed_ct} already placed, {len(members) - placed_ct} still pending")

    # Per date/slot trace.
    for date_obj, _ in sorted_dates:
        for ss, se in slot_rounds:
            reason = _diagnose_family_constraints(group_courses, date_obj, ss, state)
            if reason is None:
                print(f"  {date_obj} {ss}: PASSED constraint checks — failure must be "
                      f"in venue placement itself (see place_merged_family / "
                      f"[Family-Seat] lines around this date/slot for the room math)")
                continue
            if reason == "student_cohort_conflict":
                for c in group_courses:
                    pk = state._py_key(c)
                    if not pk:
                        continue
                    existing = state._py_busy_allocs.get((date_obj, ss, pk), [])
                    for blocker in existing:
                        if blocker.id == c.id:
                            continue
                        if not exam_is_collision_exempt(c, blocker):
                            why = _explain_collision_reason(c, blocker)
                            print(f"  {date_obj} {ss}: {c.course_code} "
                                  f"({_cohort_label(c)}, {course_student_count(c)} students) "
                                  f"BLOCKED by {blocker.course_code} "
                                  f"({course_student_count(blocker)} students) "
                                  f"already sitting there -> {why}")
            elif reason == "lecturer_conflict":
                for c in group_courses:
                    lid = state._cached_lecturer_id(c)
                    if lid and not state.lecturer_available(lid, date_obj, ss, c):
                        print(f"  {date_obj} {ss}: {c.course_code} BLOCKED — lecturer "
                              f"(id={lid}) already teaching/invigilating another exam "
                              f"at this date/slot")
            else:
                print(f"  {date_obj} {ss}: rejected — {reason}")
    print(f"[FamilyDiagnose] ===== end trace for '{nc}' =====\n")

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

# v72: the cohort "gap" rule used to forbid ANY two back-to-back slots for a
# cohort on the same day. With 3 slots a day that made the MIDDLE slot
# unusable for every cohort that already had an exam in the first or last
# slot (both are its neighbours) - so the middle slot stayed empty while the
# last (evening) slot filled up. When True, two exams in adjacent slots are
# allowed whenever ONE of the two is a MIDDLE slot (morning+middle,
# middle+evening). The gap still applies between two adjacent EDGE slots
# (only possible on a 2-slot day). Daily hard limits are unchanged.
# Set to False to restore the old strict behaviour.
ALLOW_ADJACENT_WITH_MIDDLE_SLOT = True

# v74 — compact / consecutive-day scheduling for light cohorts (see header).
COMPACT_COHORT_SCHEDULING = True   # master switch
LIGHT_COHORT_MAX_UNITS = 15        # cohorts with <= this many units are "light"
COMPACT_TARGET_PER_DAY = 2         # ideal exams per day for a light cohort
COMPACT_MAX_DAY_GAP = 2            # max exam-day distance to nearest used day (2 = one empty day allowed)
COMPACT_SPAN_SLACK = 1             # extra days allowed beyond ceil(units/target_per_day)
COMPACT_SPECIAL_ONE_PER_DAY = True   # v77: designated-venue courses go 1 per day, consecutive days
COMPACT_PACK_PAIRS = True          # open a new day only when the cohort's used days are full

# Venue exam-capacity below this is considered "small". Shared by
# schedule_small_courses_in_small_venues() and find_best_venue_no_split()
# so both agree on what counts as a small room when deciding where to
# consolidate small exams (see SECTION 6 for the strategic ordering).
SMALL_VENUE_CAP_THRESHOLD = 100

# A single course (or a single variant/section within a split family) may
# never be fragmented across more than this many venues in one sitting,
# no matter how many small rooms happen to be free. Two rooms is already
# an operational strain (invigilators, question papers, mid-sitting
# supervision split between two locations); anything beyond that is not
# "minimal split", it's the scheduler papering over a real capacity
# shortage by scattering students across a dozen tiny rooms. Enforced at
# every venue-splitting decision point: find_minimal_split_venues() (lone
# course split), _plan_family_seating() (family-variant split), and
# place_multi_venue() (the shared commit primitive both funnel through,
# capped again defensively in case a future caller assembles its own
# venue list). A course/variant that cannot fit within this many venues
# is left unplaced by that path — same as any other genuine capacity
# shortfall — rather than being split further.
MAX_SPLIT_VENUES = 2

def _split_room_limit(seats_needed: int, room_seats) -> int:
    """How many rooms one course/section may be split across.

    MAX_SPLIT_VENUES (2) is the normal ceiling, but it must never make a
    course IMPOSSIBLE: a 1000-student ENSC 100 section with 300-seat rooms
    physically needs 4 rooms, and with a hard 2-room cap the whole family
    (placed all-or-nothing) stayed unscheduled on every day even though
    plenty of rooms were free. So the limit is max(MAX_SPLIT_VENUES, the
    MINIMUM number of rooms that can hold the course, taking the biggest
    available rooms first) — never more rooms than genuinely necessary.
    `room_seats` = free seats of each usable room (any order)."""
    total, k = 0, 0
    for seats in sorted((x for x in room_seats if x > 0), reverse=True):
        total += seats
        k += 1
        if total >= seats_needed:
            return max(MAX_SPLIT_VENUES, k)
    return MAX_SPLIT_VENUES

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

    # Combined groups — CRITICAL (v65): merge into the existing
    # same-course-code family instead of registering a second, parallel
    # entry for the same course_allocation rows.
    #
    # The old code let a CombinedCourseGroup (e.g. the 3 sections making
    # up "BOTA 111-B") sit as its own independent shared_unit_groups
    # entry ALONGSIDE the full norm-code family (all 14 BOTA 111
    # sections university-wide). Every phase iterates
    # shared_unit_groups.items() treating each entry as one atomic
    # all-or-nothing family — so the 3-member combined-group entry could
    # get placed and committed to the DB on its own, before the real
    # 14-member family ever got its one shot. That's exactly how 3
    # sections landed in the timetable while the other 11 (same course
    # code, same exam, genuinely inseparable) were left stranded: two
    # overlapping "families" sharing course_allocation rows silently
    # broke the whole-family-or-nothing guarantee.
    #
    # Fix: a course_allocation belongs to exactly ONE family. If a
    # CombinedCourseGroup touches any row that's already in a norm-code
    # family, union everything into that one family (repointing every
    # touched norm_code key at the same merged member list) instead of
    # creating a second entry. Only register a standalone combined_key
    # entry when none of its members already belong to a norm-code
    # family (a genuine cross-code pairing with no overlap risk).
    id_to_norm_family: Dict[int, str] = {}
    for norm_code, ids in report.shared_unit_groups.items():
        for cid in ids:
            id_to_norm_family[cid] = norm_code

    combined_families = _build_combined_group_cache()
    for combined_key, cids in combined_families.items():
        valid_ids = [cid for cid in cids if any(c.id == cid for c in all_courses)]
        if len(valid_ids) < 2:
            continue

        touched_norm_codes = {
            id_to_norm_family[cid] for cid in valid_ids if cid in id_to_norm_family
        }

        if touched_norm_codes:
            merged_ids = set(valid_ids)
            for nc in touched_norm_codes:
                merged_ids.update(report.shared_unit_groups[nc])
            merged_ids = sorted(merged_ids)
            merged_total = sum(
                course_student_count(c) for c in all_courses if c.id in set(merged_ids)
            )
            for nc in touched_norm_codes:
                report.shared_unit_groups[nc] = merged_ids
                report.family_total_students[nc] = merged_total
                report.shared_exams[nc] = len(merged_ids)
            for cid in merged_ids:
                id_to_norm_family[cid] = next(iter(touched_norm_codes))
            if len(touched_norm_codes) > 1:
                report.log(
                    f"  MERGED overlapping families via CombinedCourseGroup "
                    f"{combined_key}: {sorted(touched_norm_codes)} -> "
                    f"{len(merged_ids)} rows"
                )
        else:
            report.shared_unit_groups[combined_key] = valid_ids
            n_combined = sum(
                course_student_count(c)
                for c in all_courses if c.id in set(valid_ids)
            )
            report.family_total_students[combined_key] = n_combined
            report.shared_exams[combined_key] = len(valid_ids)
            for cid in valid_ids:
                id_to_norm_family[cid] = combined_key

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
            Venue.objects.select_related('building')
            .filter(exam_capacity__isnull=False, exam_capacity__gt=0)
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
        # v73: two DIFFERENT courses (different normalized codes, not a
        # paired combined group) may share a room ONLY while this is True.
        # It is False for every normal phase and switched on only by the
        # last-resort passes (Phase8b, Phase9b, Phase9d) once every day and
        # slot has been tried without sharing. Same-code sections (e.g.
        # MATH 302 from several programs) may always share.
        self.allow_cross_course_sharing: bool = False
        # Last-resort switch (see daily_limit_relaxation_pass): while True the
        # per-day hard cap on a cohort/lecturer is NOT enforced. Off by default.
        self.relax_daily_limit: bool = False
        self.daily_limit_relaxed: List[dict] = []

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

        self.venue_examcap: Dict[int, int] = {
            v.id: venue_exam_capacity(v) for v in _raw_venues
        }
        print(f"[Capacity] {len(_raw_venues)} venues with an exam_capacity loaded; "
              f"seats = exam_capacity exactly (no spacing ratio).")
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
        # v74: cohort daily-key -> {exam-day index: exams on that day}
        self.cohort_day_idx: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.cohort_units: Dict[str, int] = {}
        self.compact_level: int = 2 if COMPACT_COHORT_SCHEDULING else 0   # 2 strict, 1 gap+cap, 0 off
        self._date_to_idx: Dict[datetime.date, int] = {}
        self.cohort_special_idx: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self._special_cache: Dict[int, bool] = {}
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
        # v72 — indices of the MIDDLE slots (every slot but first and last),
        # used by cohort_in_cooling() to allow morning+middle / middle+evening.
        self._middle_slot_idx = set(range(1, len(self.all_slots_ordered) - 1))
        # v72 — individual (non-family) courses aren't fighting anyone for
        # the middle-of-day slot: families/common groups claim the EDGE
        # slots first (Stage A/B of _family_stage_attempts) and only touch
        # the middle slot much later (Stage C), so on most days the middle
        # slot sits open while evening gets contested. So for individual
        # placement, try the middle (afternoon) slot(s) FIRST, then
        # morning, and leave evening last since individuals don't need to
        # scramble for it the way families/PG cohorts do.
        self.individual_priority_slots = (
            self.afternoon_slots + self.morning_slots + self.evening_slots
        )

        self._fk_cache: Dict[int, str] = {}
        self._py_cache: Dict[int, str] = {}
        self._daily_key_cache: Dict[int, str] = {}
        self._norm_code_cache: Dict[int, str] = {}
        self._lecturer_id_cache: Dict[int, Optional[int]] = {}
        self._priority_score_cache: Dict[int, float] = {}
        self.placed_families: Set[str] = set()

        self._compute_daily_limits(analysis)

        self._sorted_dates = sorted(self.date_range, key=lambda dt: dt[0])
        self._date_to_idx = {d: i for i, (d, _) in enumerate(self._sorted_dates)}
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
        # NOTE: this check must fire even when the family's norm_code is in
        # `family_exhausted`. `family_exhausted` only means "stop requiring
        # a single joint placement for this family" (see try_place_course) --
        # it must NOT mean "stop enforcing the slot a sibling already
        # locked in". Bypassing this check on exhausted is what let related
        # sections (e.g. KISW 201-E / KISW 201-F) land on the same day at
        # different times: once exhausted was set, every later placement
        # attempt for the remaining siblings skipped this guard entirely.
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
                already_placed = _temp_qs().filter(
                    date=locked_date, start_time=locked_slot,
                ).values_list("course_allocation_id", flat=True)
                for aid in already_placed:
                    if self._fk_cache.get(aid) == fk:
                        return
            except Exception:
                pass
        self.family_slot.pop(fk, None)
        self.family_day.pop(fk, None)
        if not nc:
            return
        # BUG FIX: the fk-scoped guard above only protects a lock bound
        # under THIS course's own family_key (norm_code + program/year).
        # shared_unit_lock / norm_code_day_lock are bound at the coarser
        # norm_code level and can be locked by a SIBLING with a different
        # program (e.g. "CHEM 448(CHE)" locking the slot for plain
        # "CHEM 448", which has a different fk). Without checking that
        # separately, this function was unconditionally wiping the
        # nc-level lock even when a same-code sibling was already
        # committed in the DB at that slot -- which is exactly what let
        # the freed course drift to a different time later in the same
        # sweep. Check the DB for any already-placed course sharing this
        # normalized code before releasing the nc-level locks.
        nc_locked = self.shared_unit_lock.get(nc) or self.norm_code_day_lock.get(nc)
        if nc_locked:
            locked_date = nc_locked[0] if isinstance(nc_locked, tuple) else nc_locked
            locked_slot = nc_locked[1] if isinstance(nc_locked, tuple) else None
            try:
                qs = _temp_qs().filter(date=locked_date)
                if locked_slot is not None:
                    qs = qs.filter(start_time=locked_slot)
                placed_codes = qs.values_list(
                    "course_allocation__course_code", flat=True
                )
                for code in placed_codes:
                    if normalize_course_code(code or "") == nc:
                        return
            except Exception:
                pass
        self.norm_code_day_lock.pop(nc, None)
        self.shared_unit_lock.pop(nc, None)

    def get_shared_unit_lock(self, course) -> Optional[Tuple]:
        nc = self._norm_code(course)
        if nc not in self._cross_cohort_norm_codes:
            return None
        return self.shared_unit_lock.get(nc)

    def bind_shared_unit(self, course, date, slot_start):
        # NOTE: must NOT skip on `family_exhausted` (see check_family_conflict).
        # family_split_rescue_pass sets family_exhausted for a code BEFORE
        # any of its siblings actually commit to a slot (it's what lets
        # try_place_course attempt non-joint placement at all). If binding
        # bailed out here, shared_unit_lock would never get set for exactly
        # the shared/cross-program codes (COMS 101, SOCI 101, ZOOL 101,
        # etc.) that most need the lock — leaving check_shared_unit_conflict
        # with nothing to enforce and every later section free to land on
        # the same day at a different time.
        nc = self._norm_code(course)
        if nc and nc in self._cross_cohort_norm_codes and nc not in self.shared_unit_lock:
            self.shared_unit_lock[nc] = (date, slot_start)

    def check_shared_unit_conflict(self, course, date, slot_start) -> bool:
        # See check_family_conflict: a locked slot must stay enforced even
        # after `family_exhausted` is set, or shared-unit siblings can be
        # scattered across different times the same way family sections
        # were.
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
        # See bind_shared_unit: don't skip binding just because
        # family_exhausted is set, or the day-lock never gets established
        # either.
        nc = self._norm_code(course)
        if nc and nc in self._cross_cohort_norm_codes:
            if nc not in self.norm_code_day_lock:
                self.norm_code_day_lock[nc] = date

    def check_norm_code_day_conflict(self, course, date: datetime.date) -> bool:
        nc = self._norm_code(course)
        if not nc or nc not in self._cross_cohort_norm_codes:
            return False
        # See check_family_conflict: the day-lock must keep being enforced
        # even once `family_exhausted` is set, or the same-day guard is
        # silently disabled for exactly the families that need it most.
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
        rows = _temp_qs().filter(
            date=date, start_time=slot_start
        ).select_related("course_allocation")
        for row in rows:
            db_used[row.venue_id] += _row_seats(row.allocated_students, row.course_allocation)
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
        if abs(current_idx - last_idx) <= gap:
            if ALLOW_ADJACENT_WITH_MIDDLE_SLOT and current_idx != last_idx and (
                    current_idx in self._middle_slot_idx or last_idx in self._middle_slot_idx):
                return False
            return True
        return False

    def mark_cohort_scheduled(self, course, date, slot_start):
        pk = self._daily_limit_key(course)
        if not pk:
            return
        _cpk = self._py_key(course) or pk
        idx = self._slot_start_to_idx.get(slot_start)
        if idx is not None:
            self.cohort_last_slot_idx[(pk, date)] = idx
        self.cohort_daily_count[(pk, date)] += 1
        _di = self._date_to_idx.get(date)
        if _di is not None:
            self.cohort_day_idx[_cpk][_di] += 1
            if self.is_special_course(course):
                self.cohort_special_idx[_cpk][_di] += 1
        lid = self._cached_lecturer_id(course)
        if lid:
            self.lecturer_daily_count[(lid, date)] += 1

    # ---- v74/v75: compact / consecutive-day scheduling ----------------
    def cohort_rank(self, course) -> int:
        """Sort key: light cohorts first (fewest units first); heavy cohorts last."""
        units = self.cohort_units.get(self._py_key(course), 0)
        if units and units <= LIGHT_COHORT_MAX_UNITS:
            return units
        return 10_000

    def _compact_context(self, course):
        """(days_used {idx: count}, units) when the rule applies to this course, else None."""
        if not COMPACT_COHORT_SCHEDULING or self.compact_level <= 0:
            return None
        if self._norm_code(course) in self.analysis.shared_unit_groups:
            return None          # family / common courses are placed by the staged windows
        pk = self._py_key(course)
        if not pk:
            return None
        units = self.cohort_units.get(pk, 0)
        if not units or units > LIGHT_COHORT_MAX_UNITS:
            return None
        days = self.cohort_day_idx.get(pk)
        if not days:
            return None          # first exam of the cohort: earliest day wins by ordering
        return days, units

    def is_special_course(self, course) -> bool:
        """Designated-venue individual course (same test Phase0 uses to pick candidates)."""
        cid = course.id
        v = self._special_cache.get(cid)
        if v is None:
            nc = self._norm_code(course)
            v = bool(nc and nc not in self.analysis.shared_unit_groups
                     and self.designated_venues_for_course(course))
            self._special_cache[cid] = v
        return v

    def _special_ok(self, course, date):
        """True/False for a designated course under the 1,1,1,1 rule; None if not applicable."""
        if not (COMPACT_SPECIAL_ONE_PER_DAY and COMPACT_COHORT_SCHEDULING) or self.compact_level < 2:
            return None
        if not self.is_special_course(course):
            return None
        pk = self._py_key(course)
        d = self._date_to_idx.get(date)
        if not pk or d is None:
            return None
        days = self.cohort_special_idx.get(pk)
        if not days:
            return True                       # first designated exam: earliest day wins by ordering
        if days.get(d, 0) >= 1:
            return False                      # one designated exam per day
        return min(abs(d - x) for x in days) <= 1     # consecutive exam-days only

    def compact_day_cap(self, units: int) -> int:
        n_days = max(1, len(self._sorted_dates))
        return max(COMPACT_TARGET_PER_DAY, -(-units // n_days))

    def compact_gap_ok(self, course, date) -> bool:
        _sp = self._special_ok(course, date)
        if _sp is not None:
            return _sp
        ctx = self._compact_context(course)
        if ctx is None:
            return True
        days, units = ctx
        d = self._date_to_idx.get(date)
        if d is None:
            return True
        if days.get(d, 0) >= self.compact_day_cap(units):
            return False                         # no 3rd exam on a day for a light cohort
        if d in days:
            return True
        lo, hi = min(days), max(days)
        if min(abs(d - x) for x in days) > COMPACT_MAX_DAY_GAP:
            return False
        new_span = max(hi, d) - min(lo, d) + 1
        budget = -(-units // COMPACT_TARGET_PER_DAY) + COMPACT_SPAN_SLACK
        if new_span > max(budget, hi - lo + 1):
            return False
        if COMPACT_PACK_PAIRS and self.compact_level >= 2:
            if any(c < COMPACT_TARGET_PER_DAY for c in days.values()):
                return False                     # finish the half-full day before opening another
        return True

    def compact_penalty(self, course, date) -> int:
        """Soft cost used only to ORDER candidate days for one course."""
        if (COMPACT_SPECIAL_ONE_PER_DAY and COMPACT_COHORT_SCHEDULING and self.compact_level >= 2
                and self.is_special_course(course)):
            _sd = self.cohort_special_idx.get(self._py_key(course))
            _d = self._date_to_idx.get(date)
            if _sd and _d is not None:
                return (500 if _sd.get(_d, 0) >= 1 else 0) + 100 * max(0, min(abs(_d - x) for x in _sd) - 1)
        ctx = self._compact_context(course)
        if ctx is None:
            return 0
        days, units = ctx
        d = self._date_to_idx.get(date)
        if d is None:
            return 0
        if d in days:
            return 0 if days[d] < self.compact_day_cap(units) else 500
        nearest = min(abs(d - x) for x in days)
        new_span = max(max(days), d) - min(min(days), d) + 1
        budget = -(-units // COMPACT_TARGET_PER_DAY) + COMPACT_SPAN_SLACK
        return 100 * max(0, nearest - 1) + 50 * max(0, new_span - budget)

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
        _py_units: Dict[str, int] = defaultdict(int)
        for _pk, _cl in analysis.courses_by_py.items():
            for _c in _cl:
                _k = self._py_key(_c)
                if _k:
                    _py_units[_k] += 1
        self.cohort_units = dict(_py_units)      # v75: programme-year units
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
        if getattr(self, "relax_daily_limit", False):
            return False
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
        return sorted(self._sorted_dates, key=lambda d: self.get_daily_penalty(course, d[0]) + self.compact_penalty(course, d[0]))

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
            f"  PRE-SCHEDULING INTELLIGENCE — v64",
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
        date_range = self._build_date_range()
        n_days = len(date_range)
        daytime = [(ss, se) for ss, se in slots if ss < datetime.time(17, 0)]
        evening = [(ss, se) for ss, se in slots if ss >= datetime.time(17, 0)]
        n_day_slots = len(daytime)
        slot_budget = n_days * n_day_slots
        total_seat_supply = sum(venue_exam_capacity(v) for v in self.venues)
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
    """
    Return the venue's ACTUAL building, from the real Venue.building FK
    (Building.code, falling back to Building.name), not a guess.

    This used to strip trailing digits/spaces off the venue's own `code`
    string (e.g. "SRP B03" -> "SRP B", "LT 1" vs "LT2" -> "LT " vs "LT")
    and treat that as the building. That heuristic breaks for any code
    that doesn't end in a bare number, and produces inconsistent keys for
    rooms that are, in reality, in the very same building — which meant
    the same-building split pool routinely came up short (because the
    real building's rooms got scattered across several fake "building"
    buckets) and callers fell back to a cross-building split almost every
    time, even though a real single building could have held the exam.
    Using the actual FK is the fix: it's ground truth, not a string guess.
    Venues with no building assigned fall back to the old code-based
    guess so they still group with same-guess venues rather than each
    becoming an unmatched singleton.
    """
    building = getattr(venue, "building", None)
    if building is not None:
        key = (getattr(building, "code", None) or getattr(building, "name", None) or "").strip()
        if key:
            return key.upper()
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

def _occupant_norm_code(occ_id, state):
    """Normalized course code of an occupant, via a lazily built id map.
    None = occupant unknown to this run (e.g. another scope's exam)."""
    m = getattr(state, "_norm_by_id", None)
    if m is None:
        m = {}
        for c_list in state.analysis.courses_by_py.values():
            for occ in c_list:
                m[occ.id] = normalize_course_code(getattr(occ, "course_code", "") or "")
        state._norm_by_id = m
    return m.get(occ_id)


def _has_foreign_occupant(course, venue_id, date, slot_start, state) -> bool:
    """True if the room already hosts a DIFFERENT course: another normalized
    code that is not a paired combined group. Same-code sections are never
    foreign to each other."""
    if not state.venue_has_occupants(venue_id, date, slot_start):
        return False
    my_nc = normalize_course_code(getattr(course, "course_code", "") or "")
    for occ_id in state.get_venue_occupants(venue_id, date, slot_start):
        if occ_id == course.id:
            continue
        occ_nc = _occupant_norm_code(occ_id, state)
        if occ_nc is not None and occ_nc == my_nc:
            continue
        if _combined_group_are_paired(course.id, occ_id):
            continue
        return True
    return False


def _can_share_venue(course, venue_id, date, slot_start, state) -> bool:
    if not state.venue_has_occupants(venue_id, date, slot_start):
        return True
    # v73: different courses may only share a room in the last-resort phases.
    if not state.allow_cross_course_sharing and \
            _has_foreign_occupant(course, venue_id, date, slot_start, state):
        return False
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

      1. Completely free SMALL venues (smallest sufficient capacity first).
         A non-shared course gets its own room whenever one is genuinely
         free — combining it into someone else's room is never used just
         to "save" an empty room for later.
      2. Completely free LARGE venues (smallest sufficient capacity first),
         once no free small venue fits.
      3. Occupied-but-compatible SMALL venues with room (tightest sufficient
         fit first; same-lecturer occupant preferred on ties) — reached
         ONLY when no completely free venue anywhere fits this course.
      4. Occupied-but-compatible LARGE venues with room (tightest sufficient
         fit first; same-lecturer occupant preferred on ties) — final
         fallback.

    Sharing/combining a room between different courses is strictly a
    last-resort space measure now, not an efficiency shortcut: a course
    only lands in an already-occupied room when every free venue in the
    slot is either too small or already gone. Existing student-conflict /
    combined-group rules in _can_share_venue still apply throughout, so
    this never overrides a real collision — it only decides which safe
    venue to use, and it never chooses "shared" over "free" for a course
    that isn't actually part of a shared/combined unit.
    """
    free_venues = state.get_completely_free_venues(date, slot_start)

    free_small = [(v, cap) for v, cap in free_venues if cap >= needed and _venue_is_small(v, state)]
    if free_small:
        free_small.sort(key=lambda x: x[1])
        _note_pick(course, f"tier 1 — free venue preferred over sharing: opened smallest sufficient "
                   f"empty small room {free_small[0][0].code} (cap {free_small[0][1]}; "
                   f"{len(free_small)} empty small room(s) fit)")
        return free_small[0][0]

    free_large = [(v, cap) for v, cap in free_venues if cap >= needed and not _venue_is_small(v, state)]
    if free_large:
        free_large.sort(key=lambda x: x[1])
        _note_pick(course, f"tier 2 — free venue preferred over sharing: opened smallest sufficient "
                   f"empty room {free_large[0][0].code} (cap {free_large[0][1]}; "
                   f"{len(free_large)} empty room(s) fit)")
        return free_large[0][0]

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
            _pv = occupied_small[0]
            _note_pick(course, f"tier 3 — no free room fits; packed into already-used small room: {_pv[0].code} "
                       f"({_pv[1]} seats free, tightest fit of {len(occupied_small)} compatible small room(s)"
                       f"{'; same lecturer already there' if _pv[2] else ''})")
            return occupied_small[0][0]

        if occupied_large:
            occupied_large.sort(key=lambda x: (x[1], not x[2]))
            _pv = occupied_large[0]
            _note_pick(course, f"tier 4 — no free room fits; shared already-used large room {_pv[0].code} "
                       f"({_pv[1]} seats free, tightest of {len(occupied_large)} compatible large room(s))")
            return occupied_large[0][0]

    return None

def find_minimal_split_venues(needed: int, date, slot_start, state: SchedulerState,
                              course=None) -> List[Venue]:
    """
    When a course genuinely can't fit any single venue, choose which venues
    to split it across.

    Occupied-but-compatible venues (already hosting another exam this slot,
    with room to spare) are used up BEFORE any completely free venue is
    touched, and within each tier the biggest remaining space goes first
    (to minimise how many venues this split needs). This matters because a
    split's tail end is often small — e.g. 5 leftover students — and the
    previous version sorted every candidate together by raw remaining
    capacity, so a small leftover would routinely grab the biggest
    completely FRESH venue in the room (since an empty room's "remaining"
    is its full capacity, always the largest number in the list) just to
    seat a handful of people. That "opens" — commits, occupies for this
    slot — a pristine large venue for almost nothing, closing the door on
    a genuinely dedicated large course that comes along later in the same
    pass and now finds one fewer clean room to claim outright. Draining
    existing partially-used rooms first keeps as many completely free
    venues untouched as possible for whatever's scheduled next.
    """
    if not course:
        return []
    occupied, free = [], []
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
            occupied.append((v, rem))
        else:
            free.append((v, rem))
    occupied.sort(key=lambda x: -x[1])
    free.sort(key=lambda x: -x[1])
    available = occupied + free
    if not available:
        return []
    for v, rem in available:
        if rem >= needed:
            return []

    # Policy: a split must stay inside ONE building whenever a single
    # building's available rooms can cover it; only cross a building
    # boundary when no single building has enough combined capacity.
    # Splitting one exam sitting across two buildings is not a scheduling
    # inconvenience, it's operationally broken — invigilators, the exam
    # paper, and any mid-sitting supervision can't be in two buildings at
    # once, and students in one hall have no way to know a "second half"
    # of their own exam is running elsewhere. So this is a hard preference,
    # not a tiebreaker: raw remaining-capacity order (occupied-then-free,
    # biggest first) is only used to pick rooms WITHIN the chosen building.
    nc = normalize_course_code(getattr(course, "course_code", "") or "")
    preferred_building = state.preferred_family_building(nc, date, slot_start) if nc else None
    available_rows = [[v, rem, state.venue_examcap.get(v.id, 0)] for v, rem in available]
    same_building_pool = _building_subset_for_split(
        available_rows, needed, preferred_building=preferred_building)
    pool_to_use = same_building_pool if same_building_pool else available_rows

    # Hard cap: never split a single course across more than
    # MAX_SPLIT_VENUES rooms. pool_to_use is already biggest-remaining
    # first (within the chosen building), so taking the first
    # MAX_SPLIT_VENUES rows is the best-case combination for staying
    # inside the cap; if even that can't cover `needed`, this course
    # genuinely doesn't fit within the cap and is left unsplit here.
    chosen, remaining_needed = [], needed
    pool_to_use = sorted(pool_to_use, key=lambda r: -r[1])
    _limit = _split_room_limit(needed, [r[1] for r in pool_to_use])   # v73: oversized courses may need >2
    for row in pool_to_use[:_limit]:
        v, rem = row[0], row[1]
        if remaining_needed <= 0:
            break
        take = min(rem, remaining_needed)
        chosen.append(v)
        remaining_needed -= take
    if remaining_needed > 0:
        return []
    _note_pick(course, f"SPLIT — no single venue had {needed} free seats; split across "
               f"{[v.code for v in chosen]} ({'one building' if same_building_pool else 'CROSSES buildings'}, "
               f"{len(chosen)}/{_limit} venues used)")
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
            _note_pick(course, f"combined-course group already sits in {preferred_venue.code} this slot — "
                       f"kept together there")
            if place_single(course, preferred_venue, date, slot_start, slot_end,
                            state, scheduled_ids, relax_consecutive=relax_consecutive,
                            allow_room_sharing=True, allow_split=False):
                return True
    venue = find_best_venue_no_split(needed, date, slot_start, state, course)
    if venue is None:
        _best = None
        for _v in state.venues_by_cap_desc:
            _r = state.venue_remaining(_v.id, date, slot_start)
            if _best is None or _r > _best[1]:
                _best = (_v.code, _r)
        _jr(course, date, slot_start, "no-single-venue-fits", "place_course_no_split",
            f"needs {needed}; emptiest venue {_best[0]} has {_best[1]}" if _best else f"needs {needed}; no venues")
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
        _jr(course, date, slot_start, "cohort-cooling", "place_course_with_minimal_split")
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
        _jr(course, date, slot_start, "no-split-combination", "place_course_with_minimal_split",
            f"needs {needed}; free compatible rooms in this slot can't add up to it")
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

# Concurrent Allocation Sets: the AllocationSet(s) ticked on the timetable
# dashboard for the run currently in progress, set once at the top of
# run_optimized_autoscheduler_thread(). Every DB read below that rebuilds
# in-memory occupancy/conflict state (venue_usage, lecturer_busy, family
# locks, the drift-audit helpers) goes through _temp_qs() instead of
# `ExamTempTimetable.objects` directly, so a leftover draft row from a
# DIFFERENT AllocationSet — never wiped by clear_exam_tables_safely()
# because that function deliberately only clears the CURRENT set's own
# rows — can no longer be mistaken for real occupancy and force an
# unnecessary split (e.g. a genuinely free 200-seat venue reported as
# unavailable because of another department's stale rows in the same
# table). `_ACTIVE_TT_SCOPE = None` (a caller that hasn't set it) keeps
# the old unscoped-read behaviour, so nothing outside a real run changes.
_ACTIVE_TT_SCOPE: Optional[dict] = None

def _temp_qs():
    """ExamTempTimetable rows belonging to the CURRENT run's AllocationSet
    scope only — see _ACTIVE_TT_SCOPE above."""
    qs = ExamTempTimetable.objects.all()
    if _ACTIVE_TT_SCOPE is not None:
        qs = qs.filter(tt_scope_q(_ACTIVE_TT_SCOPE, prefix="course_allocation__allocation_set"))
    return qs


def _external_temp_qs():
    """ExamTempTimetable rows OUTSIDE the current run's tt_scope — exams a
    DIFFERENT department/AllocationSet run already committed to the DB.
    Strictly read-only: used only to make those rows' students/lecturers/
    venues visible to THIS run's collision checks, never to move, delete,
    or otherwise mutate them (that isolation is exactly what
    clear_exam_tables_safely()/_temp_qs() protect on purpose)."""
    if _ACTIVE_TT_SCOPE is None:
        # No scope narrowing this run (legacy "schedule everything at once"
        # mode) -> _temp_qs() already sees every row, so there is no
        # "external" set to add.
        return ExamTempTimetable.objects.none()
    return ExamTempTimetable.objects.exclude(
        tt_scope_q(_ACTIVE_TT_SCOPE, prefix="course_allocation__allocation_set")
    )


def preload_cross_scope_busy_state(state: "SchedulerState", all_courses: List) -> int:
    """
    CRITICAL FIX — cross-department combination-stem collision blindness.

    A "combination stem" course cohort (e.g. a B.Ed Science student doing a
    Biology/Geography or Biology/Computer-Science double subject) is made up
    of CourseAllocation rows spread across SEVERAL departments, and those
    departments' exam runs are frequently scoped to their own AllocationSet
    and triggered separately (tt_scope — see resolve_tt_scope/tt_scope_q).
    clear_exam_tables_safely() and _temp_qs() deliberately leave every OTHER
    department's already-committed ExamTempTimetable rows untouched so one
    department's run can never clobber another's draft — but until this fix,
    nothing ever fed those untouched rows INTO this run's `state` either.
    `all_courses` only ever contained THIS run's own scope, so a course from
    another department/AllocationSet was never marked busy anywhere, and
    students_available()/exam_is_collision_exempt() (the same stem-aware
    logic the /exam/timetable/ conflicts report uses — see
    exam_panel_is_exempt in exam_timetable_panel.py) never got a chance to
    compare against it. Two courses that genuinely share a combination stem
    could each be scheduled "safely" in their own isolated run and land in
    the exact same date/slot — precisely the class of collision the
    conflicts report catches only AFTER the fact.

    Fix: before any placement (and again after any full state rebuild, e.g.
    rebuild_state_from_db(), since that clears these same trackers), load
    every ExamTempTimetable row OUTSIDE this run's tt_scope whose course
    isn't already part of `all_courses`, and mark its venue/program-year/
    lecturer/slot busy in `state` exactly as if it had been placed by this
    run. These course ids are intentionally never added to `scheduled_ids`
    (they are not this run's courses to report on or re-place) and this
    function never writes to the DB.
    """
    all_course_ids = {c.id for c in all_courses}
    entries = list(
        _external_temp_qs()
        .exclude(course_allocation_id__in=all_course_ids)
        .values("course_allocation_id", "venue_id", "date", "start_time", "allocated_students")
    )
    if not entries:
        return 0
    ext_ids = {e["course_allocation_id"] for e in entries}
    ext_courses = {
        c.id: c for c in CourseAllocation.objects.filter(id__in=ext_ids)
        .select_related("lecturer", "program", "program_course",
                         "selection_group", "specialization_stem",
                         "specialization_stem__category", "student_group")
        .prefetch_related("specialization_stems", "specialization_stems__category")
    }
    marked = 0
    for e in entries:
        course = ext_courses.get(e["course_allocation_id"])
        if not course:
            continue
        vid, date_obj, ss = e["venue_id"], e["date"], e["start_time"]
        n = _row_seats(e.get("allocated_students"), course)
        cap = state.venue_examcap.get(vid, 0)
        current_usage = state.venue_usage[(vid, date_obj, ss)]
        if current_usage < cap:
            take = min(n, cap - current_usage)
            state.venue_usage[(vid, date_obj, ss)] += take
            state.venue_occupants[(vid, date_obj, ss)].append((course.id, take))
        pk = state._py_key(course)
        if pk:
            state._py_busy.setdefault((date_obj, ss), set()).add(pk)
            state._py_busy_allocs.setdefault((date_obj, ss, pk), []).append(course)
        lid = state._cached_lecturer_id(course)
        if lid:
            state.lecturer_busy[lid].add((date_obj, ss))
            state.lecturer_exam_group[(lid, date_obj, ss)] = get_exam_group_key(course)
        marked += 1
    if marked:
        for date_obj, _ in state.date_range:
            for ss, _ in state.all_slots_ordered:
                for v in state.venues:
                    used = state.venue_usage[(v.id, date_obj, ss)]
                    state._venue_avail[(v.id, date_obj, ss)] = max(
                        0, state.venue_examcap.get(v.id, 0) - used)
        print(f"[AutoScheduler] Cross-scope collision guard: {marked} exam(s) already "
              f"committed by other AllocationSet(s)/department(s) marked busy so this "
              f"run won't double-book their students, lecturer, or venue seats.")
    return marked

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
                allocated_students=course_student_count(course),
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

def _slot_space_ok(state, date, ss, needed, course) -> bool:
    """slot_has_combined_venue_space + a journal row when it says no."""
    ok = state.slot_has_combined_venue_space(date, ss, needed, course)
    if not ok:
        _jr(course, date, ss, "no-venue-space-for-full-count", "slot_has_combined_venue_space",
            f"needs {needed}")
    return ok

def _same_pass_conflict(course, placed_list, date, ss) -> bool:
    """The in-pass 'this cohort already got a course placed in this slot' guard,
    with a journal row when it rejects."""
    for placed_c in placed_list:
        if not exam_is_collision_exempt(course, placed_c):
            _jr(course, date, ss, "student-conflict", "same-pass-cohort-guard",
                f"cohort just seated {_tcode(placed_c)} in this slot")
            return True
    return False

def _check_hard_constraints(course, date, slot_start, state: SchedulerState) -> Optional[str]:
    reason = None
    if course.id in state.strict_locked_ids and not state.allow_strict_rescue:
        reason = "strict-designated-venue-unavailable"
    elif not state.students_available(course, date, slot_start):
        reason = "student-conflict"
    elif state.check_family_conflict(course, date, slot_start):
        reason = "family-conflict"
    elif state.check_shared_unit_conflict(course, date, slot_start):
        reason = "shared-unit-conflict"
    elif state.check_norm_code_day_conflict(course, date):
        reason = "norm-code-day-conflict"
    else:
        lid = state._cached_lecturer_id(course)
        if lid and not state.lecturer_available(lid, date, slot_start, course):
            reason = "lecturer-conflict"
        elif state.is_daily_limit_hard_exceeded(course, date):
            reason = "daily-limit-hard-exceeded"
        elif not state.compact_gap_ok(course, date):
            reason = "compactness-gap"
    if reason:
        _jr(course, date, slot_start, reason, "_check_hard_constraints")
    return reason

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
    # v73: never drop a course into a room another (different) course uses,
    # whatever allow_room_sharing says, unless we are in a last-resort phase.
    if has_occupants and not state.allow_cross_course_sharing and \
            _has_foreign_occupant(course, venue.id, date, slot_start, state):
        _jr(course, date, slot_start, "venue-shared-with-different-course", "place_single",
            f"{getattr(venue, 'code', venue)} already hosts a different course")
        return False
    if not ignore_capacity:
        if rem < needed:
            _jr(course, date, slot_start, "venue-too-small", "place_single",
                f"{getattr(venue, 'code', venue)} has {rem} free, needs {needed}")
            return False
        if has_occupants and allow_room_sharing:
            if not _can_share_venue(course, venue.id, date, slot_start, state):
                _jr(course, date, slot_start, "venue-shared-student-conflict", "place_single",
                    f"{getattr(venue, 'code', venue)} is occupied by a course sharing students")
                return False
    reason = _check_hard_constraints(course, date, slot_start, state)
    if reason:
        return False
    if not relax_consecutive and state.cohort_in_cooling(course, date, slot_start):
        _jr(course, date, slot_start, "cohort-cooling", "place_single")
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
                allocated_students=needed,
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
        _jr(course, date, slot_start, "db-integrity-error", "place_single", str(e)[:120])
        return False
    if not _post_place(course, venue.id, needed, date, slot_start, slot_end, state, scheduled_ids):
        ExamTempTimetable.objects.filter(
            course_allocation=course, date=date, start_time=slot_start
        ).delete()
        _jr(course, date, slot_start, "venue-seat-accounting-failed", "place_single",
            getattr(venue, 'code', str(venue)))
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
        _jr(course, date, slot_start, "cohort-cooling", "place_multi_venue")
        return False
    if _course_already_in_db(course):
        scheduled_ids.add(course.id)
        return True
    # Defensive cap: whatever pool a caller assembled, never actually
    # consume more than MAX_SPLIT_VENUES of it for one course. Callers
    # (find_minimal_split_venues, _plan_family_seating, Phase0's
    # designated-priority split pools) are expected to already respect
    # this, but this is the one primitive every split path commits
    # through, so it's the last line of defense against fragmentation.
    _cands = []
    for v in venues:
        rem = state.venue_remaining(v.id, date, slot_start)
        if state.venue_has_occupants(v.id, date, slot_start) and not state.allow_cross_course_sharing \
                and _has_foreign_occupant(course, v.id, date, slot_start, state):
            continue                    # v73: no different-course sharing
        if allow_room_sharing and state.venue_has_occupants(v.id, date, slot_start):
            if not _can_share_venue(course, v.id, date, slot_start, state):
                continue
        _cands.append((v, rem))
    _cands.sort(key=lambda x: -x[1])    # biggest free room first -> fewest rooms
    _limit = _split_room_limit(needed, [r for _v, r in _cands])
    assignments, remaining = [], needed
    for v, rem in _cands:
        if remaining <= 0 or len(assignments) >= _limit:
            break
        take = min(rem, remaining)
        if take > 0:
            assignments.append((v, take))
            remaining -= take
    if remaining > 0:
        _jr(course, date, slot_start, "split-venues-insufficient", "place_multi_venue",
            f"{min(len(venues), _limit)} of {len(venues)} candidate venue(s) tried "
            f"(capped at {_limit}) could only seat {needed - remaining}/{needed}")
        return False
    try:
        with transaction.atomic():
            for v, take in assignments:
                ExamTempTimetable.objects.create(
                    course_allocation=course, venue=v,
                    date=date, day=date.strftime("%A"),
                    start_time=slot_start, end_time=slot_end,
                    allocated_students=take,
                )
    except IntegrityError:
        if ExamTempTimetable.objects.filter(
            course_allocation=course, date=date, start_time=slot_start
        ).exists():
            scheduled_ids.add(course.id)
            return True
        return False
    consumed = []
    for v, students in assignments:
        if not state.consume_venue(v.id, date, slot_start, students, course):
            for cv, cs in consumed:
                state.release_venue(cv.id, date, slot_start, cs, course)
            ExamTempTimetable.objects.filter(
                course_allocation=course, date=date, start_time=slot_start
            ).delete()
            return False
        consumed.append((v, students))
    # Course-level bookkeeping happens ONCE per course, not once per venue
    # (doing it per venue counted a split course several times toward the
    # daily load and the lecturer/cohort counters).
    for v, _students in consumed:
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
    for v, _students in consumed:
        _trace_placement(course, v, date, slot_start)
    return True

def try_place_course(course, date, slot_start, slot_end,
                     state: SchedulerState, scheduled_ids: Set[int],
                     relax_consecutive=False, allow_split=False,
                     allow_room_sharing=True) -> bool:
    nc = normalize_course_code(course.course_code or "")
    # ABSOLUTE RULE: a course that belongs to a family (same normalized code
    # shared across programs/sections) may NEVER be placed one-at-a-time
    # through this generic entrypoint — not even after `family_exhausted`
    # is set. `family_exhausted` used to mean "give up on togetherness and
    # let members scatter", which is exactly what produced partial families
    # (e.g. 3 of 14 BOTA 111 sections seated while the other 11 sat
    # unscheduled forever, once the family lock from those 3 blocked them
    # from ever joining at a different time). A family's members must only
    # ever be written to the DB together, atomically, via place_merged_family
    # / _commit_distributed_minimal, which seat the WHOLE remaining group in
    # one transaction or seat nobody. If it can't fit anywhere as a whole,
    # the correct outcome is for the whole family to stay unscheduled and be
    # reported for a human to fix (more capacity, a different day), not for
    # some students to get an exam while their classmates don't.
    if nc in state.analysis.shared_unit_groups:
        family_member_ids = state.analysis.shared_unit_groups.get(nc, [])
        if any(cid not in scheduled_ids for cid in family_member_ids):
            _jr(course, None, None, "family-member-refused-individual-placement", "try_place_course")
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
        reason = None
        if c.id in state.strict_locked_ids and not state.allow_strict_rescue:
            reason = "strict-designated-venue-unavailable"
        elif not state.students_available(c, date, slot_start):
            reason = "student-conflict"
        elif state.check_norm_code_day_conflict(c, date):
            reason = "norm-code-day-conflict"
        elif state.check_shared_unit_conflict(c, date, slot_start):
            reason = "shared-unit-conflict"
        elif state.check_family_conflict(c, date, slot_start):
            reason = "family-conflict"
        else:
            lid = state._cached_lecturer_id(c)
            if lid and not state.lecturer_available(lid, date, slot_start, c):
                reason = "lecturer-conflict"
        if reason:
            _jr_family(group_courses, c, date, slot_start, reason, "_family_constraints_ok")
            return False
    return True

def _diagnose_family_constraints(group_courses, date, slot_start, state, journal: bool = False) -> Optional[str]:
    """Same checks as _family_constraints_ok, but returns WHICH check
    rejected the slot (or None if it passed), so a family that fails
    everywhere can be diagnosed instead of just silently disappearing.
    Used only for logging in schedule_families_first — never changes
    scheduling behavior."""
    for c in group_courses:
        _r = None
        if c.id in state.strict_locked_ids and not state.allow_strict_rescue:
            _r = ("strict_locked", "strict-designated-venue-unavailable")
        elif not state.students_available(c, date, slot_start):
            _r = ("student_cohort_conflict", "student-conflict")
        elif state.check_norm_code_day_conflict(c, date):
            _r = ("norm_code_day_conflict", "norm-code-day-conflict")
        elif state.check_shared_unit_conflict(c, date, slot_start):
            _r = ("shared_unit_conflict", "shared-unit-conflict")
        elif state.check_family_conflict(c, date, slot_start):
            _r = ("family_conflict", "family-conflict")
        else:
            lid = state._cached_lecturer_id(c)
            if lid and not state.lecturer_available(lid, date, slot_start, c):
                _r = ("lecturer_conflict", "lecturer-conflict")
        if _r:
            if journal:
                _jr_family(group_courses, c, date, slot_start, _r[1], "_diagnose_family_constraints")
            return _r[0]
    return None

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
    is_strict = bool(venue_ids) and nc in state.strict_norm_codes

    # ------------------------------------------------------------------
    # PASS 1 — single, stand-alone venue. A family should get ONE room to
    # itself whenever any single room (designated OR general) is big
    # enough, no matter how small the designated pool's individual rooms
    # are. Splitting is a last resort, not a shortcut taken just because
    # the designated pool alone happens to add up once distributed.
    # ------------------------------------------------------------------
    if venue_ids:
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
        # Ordering strategy (matches find_best_venue_no_split): small FREE
        # designated venues first, then small occupied-but-compatible ones
        # (packing is a last resort even for a shared/combined family — the
        # whole group should land in one empty room of its own whenever one
        # is available), then large free, then large occupied — tightest
        # sufficient fit within each bucket.
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
        candidates.sort(key=lambda x: (not x[3], x[2], x[1]))
        for v, rem, occupied, is_small in candidates:
            if _commit_single_venue(group_courses, nc, v, total_needed,
                                    date, ss, se, state, scheduled_ids):
                return True

    known_building = state.preferred_family_building(nc, date, ss)
    free_venues = state.get_free_venues(date, ss)
    free_with_caps = []
    for v, rem in free_venues:
        cap = state.venue_examcap.get(v.id, 0)
        occupied = state.venue_has_occupants(v.id, date, ss)
        free_with_caps.append([v, rem, cap, occupied])

    if not is_strict:
        # A designated pool that's too fragmented to hold the family alone
        # does NOT justify splitting yet — check whether one ordinary,
        # stand-alone venue elsewhere can take the whole family first.
        def _sort_key(row):
            v, rem, cap, occupied = row
            is_small = 0 < cap < SMALL_VENUE_CAP_THRESHOLD
            building_match = 0 if (known_building and _venue_building(v) == known_building) else 1
            return (not is_small, occupied, building_match, rem)   # v73: FREE rooms before occupied ones

        free_with_caps_sorted = sorted(free_with_caps, key=_sort_key)
        for row in free_with_caps_sorted:
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

    # ------------------------------------------------------------------
    # PASS 2 — no single venue anywhere fits. Only now do we split, still
    # preferring the designated pool over the general pool.
    # ------------------------------------------------------------------
    if venue_ids:
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
            _jr_all(group_courses, date, ss, "strict-designated-pool-cannot-hold-family", "place_merged_family",
                    f"family needs {total_needed}; designated pool has "
                    f"{sum(state.venue_remaining(_v, date, ss) for _v in venue_ids)} free and STRICT forbids other rooms")
            return False

    if not is_strict:
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
    _best_single = max((row[1] for row in free_with_caps), default=0)
    _total_free = sum(row[1] for row in free_with_caps)
    _jr_all(group_courses, date, ss, "family-venue-layout-failed", "place_merged_family",
            f"family needs {total_needed}; largest single free room {_best_single}, "
            f"total free {_total_free}, whole-family single room "
            f"{'available but commit failed' if _best_single >= total_needed else 'not available'}, "
            f"{'split possible on paper but seating plan/commit failed' if _total_free >= total_needed else 'not enough seats even split'}")
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
                _jr(course, date, ss, "venue-shared-student-conflict", "_commit_single_venue",
                    f"{venue.code} is occupied by a course sharing students")
                return False
    if TRACE_ENABLED:
        _des = venue.id in set(state.designated_venues_for_family(group_courses))
        _txt = (f"family '{nc}' ({len(group_courses)} section(s), {total_needed} students) seated TOGETHER in one "
                f"venue {venue.code} (cap {cap}, {remaining} free before); "
                f"{'designated-venue pool' if _des else 'general pool'}; "
                f"{'room already hosting compatible exams' if state.venue_has_occupants(venue.id, date, ss) else 'room was empty'}")
        for _c in group_courses:
            _note_pick(_c, _txt)
    entries = [
        ExamTempTimetable(
            course_allocation=c, venue=venue,
            date=date, day=date.strftime("%A"),
            start_time=ss, end_time=se,
            allocated_students=course_student_count(c),
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
        _jr_all(group_courses, date, ss, "family-db-commit-error", "_commit_single_venue", str(e)[:120])
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
        _jr_all(group_courses, date, ss, "family-seat-accounting-failed", "_commit_single_venue", venue.code)
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

def _plan_family_seating(group_courses, pool_sorted, date, ss, state,
                         preferred_building=None):
    """Decide which venue(s) each variant of a family sits in.

    Returns [(course, venue, seats), ...] or None if the pool cannot seat
    everyone. Pure planning: nothing is written to the DB or to `state`.

    Rules, in order of priority:
      1. A variant goes ENTIRELY into one venue whenever any venue in the
         pool still has room for all of it (best-fit decreasing: biggest
         variant first, tightest sufficient room).
      2. Among sufficient rooms prefer: preferred building, then a room
         already holding a variant with the SAME course_code label, then
         the smallest leftover space, then the pool's own order.
      3. A variant that fits NO single remaining room is split across as
         few rooms as possible (largest leftover space first).
      4. A room that already has occupants is only used when
         _can_share_venue confirms no student clash for THAT variant.
    """
    def _label(c):
        return (getattr(c, "course_code", "") or "").strip()

    room_left = {row[0].id: row[1] for row in pool_sorted}
    venue_of = {row[0].id: row[0] for row in pool_sorted}
    pool_rank = {row[0].id: i for i, row in enumerate(pool_sorted)}

    def _building_rank(vid):
        if preferred_building and _venue_building(venue_of[vid]) == preferred_building:
            return 0
        return 1

    share_cache: Dict[Tuple[int, int], bool] = {}

    def _usable(course, vid) -> bool:
        key = (course.id, vid)
        if key not in share_cache:
            share_cache[key] = (
                not state.venue_has_occupants(vid, date, ss)
                or _can_share_venue(course, vid, date, ss, state)
            )
        return share_cache[key]

    ordered = sorted(
        group_courses,
        key=lambda c: (-course_student_count(c), _label(c), c.id),
    )
    label_venues: Dict[str, Set[int]] = defaultdict(set)
    assignments: List[Tuple[Any, Any, int]] = []

    def _split_variant(course) -> bool:
        """Seat one variant across as few rooms as possible. False = cannot."""
        left = course_student_count(course)
        if DEBUG_VERBOSE:
            _big = max(room_left.values()) if room_left else 0
            print(f"  [Family-Seat] WARNING: {course.course_code} ({left} students) "
                  f"fits no single venue — splitting (largest room with space left "
                  f"in pool: {_big} seats; pool venues: {len(room_left)})")
        usable = sorted(
            (vid for vid in room_left if room_left[vid] > 0 and _usable(course, vid)),
            key=lambda vid: (_building_rank(vid), -room_left[vid], pool_rank[vid]),
        )
        # Room limit: normally MAX_SPLIT_VENUES, but never fewer than the
        # minimum number of rooms that can physically hold this variant
        # (see _split_room_limit) — otherwise an oversized section (e.g.
        # 1000 students, 300-seat rooms) could never be seated and its whole
        # family would stay unscheduled on every day.
        _limit = _split_room_limit(left, [room_left[v] for v in usable])
        if _limit > MAX_SPLIT_VENUES:
            usable = sorted(usable, key=lambda vid: (-room_left[vid], pool_rank[vid]))
        for vid in usable[:_limit]:
            if left <= 0:
                break
            take = min(room_left[vid], left)
            assignments.append((course, venue_of[vid], take))
            room_left[vid] -= take
            left -= take
        return left <= 0

    # PASS 0 — variants too big for ANY single room in the pool are seated
    # FIRST, while the biggest rooms are still free; otherwise smaller
    # variants would take those rooms and leave the oversized one no way out.
    pre_split = [c for c in ordered
                 if not any(room_left[vid] >= course_student_count(c) and _usable(c, vid)
                            for vid in room_left)]
    for course in pre_split:
        if not _split_variant(course):
            return None
    pre_ids = {c.id for c in pre_split}

    # PASS 1 — whole variant, one venue.
    must_split: List[Any] = []
    for course in ordered:
        if course.id in pre_ids:
            continue
        need = course_student_count(course)
        fits = [vid for vid in room_left
                if room_left[vid] >= need and _usable(course, vid)]
        if not fits:
            must_split.append(course)
            continue
        lbl = _label(course)
        best = min(fits, key=lambda vid: (
            _building_rank(vid),
            0 if vid in label_venues[lbl] else 1,
            room_left[vid] - need,
            pool_rank[vid],
        ))
        assignments.append((course, venue_of[best], need))
        room_left[best] -= need
        label_venues[lbl].add(best)
        if DEBUG_VERBOSE:
            print(f"  [Family-Seat] {course.course_code} ({need} students) -> "
                  f"{venue_of[best].code} (whole course, no split needed)")

    # PASS 2 — variants that lost their single room to earlier variants.
    for course in must_split:
        if not _split_variant(course):
            return None
    return assignments

def _commit_distributed_minimal(group_courses, nc, pool, total_needed,
                                date, ss, se, state, scheduled_ids,
                                consume_fn=None, preferred_building=None):
    """Seat a family across `pool` (rows of [venue, remaining, cap]) using
    _plan_family_seating, then commit atomically (DB + state)."""
    consume = consume_fn or state.consume_venue
    pool_sorted = sorted(pool, key=lambda row: -row[1])
    if preferred_building:
        same = [r for r in pool_sorted if _venue_building(r[0]) == preferred_building]
        other = [r for r in pool_sorted if _venue_building(r[0]) != preferred_building]
        pool_sorted = same + other

    assignments = _plan_family_seating(
        group_courses, pool_sorted, date, ss, state,
        preferred_building=preferred_building)
    if not assignments:
        _jr_all(group_courses, date, ss, "family-seating-plan-failed", "_commit_distributed_minimal",
                f"family needs {total_needed}; pool of {len(pool_sorted)} room(s) with "
                f"{sum(r[1] for r in pool_sorted)} usable seats could not seat every section "
                f"(sharing rules / fragmentation)")
        return False
    if TRACE_ENABLED:
        _pv: Dict[int, list] = defaultdict(list)
        for _c, _v, _n in assignments:
            _pv[_c.id].append(f"{_v.code}({_n})")
        for _c in group_courses:
            _rooms = _pv.get(_c.id, [])
            _txt = (f"family '{nc}' seated together, best-fit-decreasing plan: this section -> "
                    f"{' + '.join(_rooms)}"
                    + (" (whole section in one room)" if len(_rooms) == 1 else
                       " (SPLIT — no single remaining room in the pool could hold it)"))
            _note_pick(_c, _txt)

    if DEBUG_VERBOSE:
        per_course = defaultdict(list)
        for course, venue, seats in assignments:
            per_course[course.id].append((venue, seats))
        for cid, venue_list in per_course.items():
            if len(venue_list) > 1:
                course = next(c for c in group_courses if c.id == cid)
                print(f"  [Family-Split-WARN] {course.course_code} split across "
                      f"{len(venue_list)} venues: {[(v.code, n) for v, n in venue_list]}")

    entries = [
        ExamTempTimetable(
            course_allocation=course, venue=venue,
            date=date, day=date.strftime("%A"),
            start_time=ss, end_time=se,
            allocated_students=seats,
        )
        for course, venue, seats in assignments
    ]
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
    except Exception as e:
        print(f"  [FAIL] DB commit error: {e}")
        _jr_all(group_courses, date, ss, "family-db-commit-error", "_commit_distributed_minimal", str(e)[:120])
        return False

    # Consume every seat; on any failure release what was already consumed
    # and undo the DB rows so state and DB stay in agreement.
    consumed = []
    for course, venue, seats in assignments:
        if not consume(venue.id, date, ss, seats, course):
            for c, v, n in consumed:
                state.release_venue(v.id, date, ss, n, c)
            ExamTempTimetable.objects.filter(
                course_allocation__in=group_courses, date=date, start_time=ss
            ).delete()
            MergedCourseGroup.objects.filter(
                base_course=group_courses[0], date=date, start_time=ss
            ).delete()
            state.placed_families.discard(nc)
            _jr_all(group_courses, date, ss, "family-seat-accounting-failed", "_commit_distributed_minimal", venue.code)
            return False
        consumed.append((course, venue, seats))

    state.placed_families.add(nc)
    for course, venue, seats in assignments:
        state.record_combined_group_venue(course, date, ss, venue.id)
        state.record_family_building(nc, date, ss, _venue_building(venue))

    buildings_used = sorted({_venue_building(venue) for _c, venue, _s in assignments})
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
        venues_used: Dict[str, int] = defaultdict(int)
        for _c, v, n in assignments:
            venues_used[v.code] += n
        print(f"  [Family-Seat] '{nc}' -> {len(venues_used)} venue(s): {dict(venues_used)}")
    for course, venue, seats in assignments:
        _trace_placement(course, venue, date, ss)
    return True

# ======================================================================
# SECTION 8 – Scheduling Phases
# ======================================================================

def schedule_small_courses_in_small_venues(all_courses, state, scheduled_ids):
    """
    PhaseA — pack small stand-alone exams into small venues early, before
    anything else claims a room.

    STEM-SHARED COURSES ARE DEFERRED, NOT PLACED HERE (fix requested by
    user): a course's OWN row can look "small" (few students on this
    CourseAllocation) while it actually belongs to a SpecializationStem —
    a combination like Bio/Geography or Bio/Computer-Science — so its real
    cohort only becomes clear once its stem-mates are known too. This phase
    runs first, with zero lookahead: if it grabs a small venue/slot for a
    stem-linked course before the stem's other members are even considered,
    it can (a) box a later stem-mate out of the slot it needed and force it
    to split unnecessarily, and (b) waste a small venue's capacity on a
    course whose stem-mates end up needing a completely different day. This
    was already the rule for same-code "shared unit" families
    (`shared_unit_groups`, excluded below) — extending it to
    SpecializationStem membership closes the same gap for combination-stem
    courses.

    This does NOT lose the "still fits a small venue" case the user also
    flagged: deferred courses aren't barred from small venues later, they
    just go through find_best_venue_no_split()/find_minimal_split_venues()
    in whichever later phase places them — and those already try small,
    already-occupied-compatible venues FIRST (see find_best_venue_no_split's
    docstring), then free small venues, before ever spilling into a large
    room. So a stem course whose real merged need still doesn't reach a
    small venue's threshold still lands in one — just decided by a phase
    that has more of the picture, not blindly here.
    """
    SMALL_STUDENT_THRESHOLD = 100
    SMALL_VENUE_THRESHOLD = SMALL_VENUE_CAP_THRESHOLD  # shared with find_best_venue_no_split
    py_total_counts = defaultdict(int)
    for c in all_courses:
        pk = state._py_key(c)
        if pk:
            py_total_counts[pk] += 1
    eligible = [
        c for c in all_courses
        if c.id not in scheduled_ids
        and course_student_count(c) < SMALL_STUDENT_THRESHOLD
        and normalize_course_code(getattr(c, "course_code", "") or "") not in state.analysis.shared_unit_groups
    ]
    stem_deferred = [c for c in eligible if _exam_get_specialization_stem_ids(c)]
    small_courses = [c for c in eligible if not _exam_get_specialization_stem_ids(c)]
    if stem_deferred:
        print(f"[PhaseA-SmallFirst] Deferring {len(stem_deferred)} small-but-stem-linked "
              f"course(s) to later phases (combination-stem membership — real cohort not "
              f"yet known): {[getattr(c, 'course_code', '?') for c in stem_deferred[:15]]}"
              f"{' ...' if len(stem_deferred) > 15 else ''}")
    small_courses.sort(key=lambda c: (state.cohort_rank(c), course_student_count(c)))  # v74 light cohorts first
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
    # v72: middle slot first (see individual_priority_slots)
    _tset = set(target_slots)
    target_slots = [x for x in state.individual_priority_slots if x in _tset]
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
                        _jr(course, date_obj, ss, "cohort-cooling", "schedule_small_courses_in_small_venues")
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

# ----------------------------------------------------------------------
# v71 — ONE shared day-window staging for every shared / common / family
# course. Every phase that places a whole family (PhaseB, Phase1, Phase3,
# Phase3b, Phase4-sweep3, Phase8) now walks the SAME order, so no phase can
# scatter families across the whole exam period ahead of the windows.
#
#   Stage A  first HALF of the days (calendar order), EDGE slots only:
#            for each day try the LAST slot, then the FIRST slot.
#   Stage B  add the next QUARTER of days (now 3/4), EDGE slots only,
#            walking just the newly added days.
#   Stage C  go back to the FIRST day and walk up to where we had reached
#            (the 3/4 mark), now trying the MIDDLE slots of every day.
#   Stage D  the remaining (last) QUARTER of days: ANY timeslot, day and
#            slot given equal priority (diagonal round-robin).
#   Stage E  safety net: every day, every slot (only pairs not yet tried).
#
# Days are taken in CALENDAR order (state.dates_in_order()), not in the
# daily-load-penalty order used for individual courses, so "first half"
# really means the first half of the exam period.
# ----------------------------------------------------------------------
FAMILY_STAGE_LABELS = {
    "A": "A: first half of days, edge slots",
    "B": "B: added quarter, edge slots",
    "C": "C: back to day 1 up to 3/4 mark, middle slots",
    "D": "D: last quarter, any slot",
    "E": "E: safety net, any day/any slot",
}


def _family_stage_attempts(state):
    """Yield (stage, date, slot_start, slot_end) in the A->E order above.
    Lazy, so the caller stops generating the moment a family is placed.
    A (date, slot) pair already yielded is never yielded again."""
    dates_list = [d for d, _ in state.dates_in_order()]
    all_slots = list(state.all_slots_ordered)
    if not dates_list or not all_slots:
        return
    n_days = len(dates_list)
    half_n = math.ceil(n_days / 2)
    three_q_n = math.ceil(n_days * 3 / 4)
    half_window = dates_list[:half_n]
    added_quarter_window = dates_list[half_n:three_q_n]
    three_quarter_window = dates_list[:three_q_n]
    remaining_quarter_window = dates_list[three_q_n:]

    last_slot = all_slots[-1]
    first_slot = all_slots[0]
    edge_slots = [last_slot] if last_slot == first_slot else [last_slot, first_slot]
    middle_slots = all_slots[1:-1] if len(all_slots) > 2 else []

    tried: Set[Tuple] = set()

    def _once(stage, d, slot):
        key = (d, slot[0])
        if key in tried:
            return None
        tried.add(key)
        return (stage, d, slot[0], slot[1])

    # Stage A
    for d in half_window:
        for slot in edge_slots:
            r = _once("A", d, slot)
            if r:
                yield r
    # Stage B
    for d in added_quarter_window:
        for slot in edge_slots:
            r = _once("B", d, slot)
            if r:
                yield r
    # Stage C
    for d in three_quarter_window:
        for slot in middle_slots:
            r = _once("C", d, slot)
            if r:
                yield r
    # Stage D — diagonal round-robin over (day, slot)
    if remaining_quarter_window:
        max_round = len(remaining_quarter_window) + len(all_slots) - 1
        for rnd in range(max_round + 1):
            for di, d in enumerate(remaining_quarter_window):
                si = rnd - di
                if si < 0 or si >= len(all_slots):
                    continue
                r = _once("D", d, all_slots[si])
                if r:
                    yield r
    # Stage E
    for d in dates_list:
        for slot in all_slots:
            r = _once("E", d, slot)
            if r:
                yield r


def _place_family_in_stages(nc, group, state, scheduled_ids,
                            rejection_counts=None, where="family-stages"):
    """Try to seat the WHOLE family following the A->E windows.
    Returns (placed: bool, attempts: int, stage: Optional[str]).
    All-or-nothing is still enforced inside place_merged_family."""
    if rejection_counts is None:
        rejection_counts = defaultdict(int)
    true_total = family_total_students(group)
    attempts = 0
    for stage, date_obj, ss, se in _family_stage_attempts(state):
        live = [c for c in group if c.id not in scheduled_ids]
        if not live:
            return True, attempts, stage
        if not state.day_has_any_capacity(date_obj):
            rejection_counts["no_day_capacity"] += 1
            continue
        attempts += 1
        reason = _diagnose_family_constraints(live, date_obj, ss, state, journal=True)
        if reason:
            rejection_counts[reason] += 1
            continue
        if state.slot_total_remaining(date_obj, ss) < true_total:
            rejection_counts["insufficient_total_venue_capacity"] += 1
            _jr_all(live, date_obj, ss, "insufficient-total-venue-capacity", where,
                    f"family needs {true_total}; slot has "
                    f"{state.slot_total_remaining(date_obj, ss)} free in total")
            continue
        if place_merged_family(live, nc, date_obj, ss, se, state, scheduled_ids):
            return True, attempts, stage
        rejection_counts["venue_layout_could_not_fit"] += 1
    return False, attempts, None


def schedule_common_courses_priority_pass(all_courses, state, scheduled_ids):
    """PhaseB — common / shared-unit families, placed through the shared
    A->E day-window staging (v71). Previously this phase swept slots from
    last to first across EVERY date, which placed most families before
    Phase1's half/quarter windows were ever consulted."""
    if not state.analysis.shared_unit_groups:
        return 0
    course_by_id = {c.id: c for c in all_courses}
    dates = state.dates_in_order()
    all_slots = state.all_slots_ordered
    if not dates or not all_slots:
        return 0
    pending: List[Tuple[str, List]] = []
    for nc, cids in state.analysis.shared_unit_groups.items():
        if nc in state.placed_families:
            continue
        group = [course_by_id[cid] for cid in cids
                 if cid in course_by_id and cid not in scheduled_ids]
        if group:
            pending.append((nc, group))
    pending.sort(key=lambda kv: (-len(kv[1]), -family_total_students(kv[1])))
    print(f"\n[PhaseB-CommonLast] {len(pending)} common-course families — "
          f"staged windows: half of days (edge slots) -> +quarter -> middle slots "
          f"back from day 1 -> last quarter any slot")
    placed_total = 0
    stage_hits: Dict[str, int] = defaultdict(int)
    for nc, group in pending:
        group = [c for c in group if c.id not in scheduled_ids]
        if not group:
            state.placed_families.add(nc)
            continue
        ok, _attempts, stage = _place_family_in_stages(
            nc, group, state, scheduled_ids, where="schedule_common_courses_priority_pass")
        if ok:
            placed_total += len(group)
            state.placed_families.add(nc)
            stage_hits[stage or "?"] += 1
            print(f"  [PhaseB-CommonLast] '{nc}' (x{len(group)}) placed in stage "
                  f"{FAMILY_STAGE_LABELS.get(stage, stage)}")
    left = sum(1 for nc, g in pending if any(c.id not in scheduled_ids for c in g))
    if left:
        print(f"[PhaseB-CommonLast] {left} common families still pending "
              f"after all stages — later phases will retry them")
    print(f"[PhaseB-CommonLast] Placed {placed_total} variants; stages used: {dict(stage_hits)}")
    return placed_total


def schedule_families_first(all_courses, state, scheduled_ids):
    if not state.analysis.shared_unit_groups:
        return 0
    course_by_id = {c.id: c for c in all_courses}
    sorted_families = sorted(
        state.analysis.shared_unit_groups.items(),
        key=lambda kv: (
            -state.analysis.shared_exams.get(kv[0], 0),
            -family_total_students([course_by_id[cid] for cid in kv[1] if cid in course_by_id]),
        ),
    )
    print(f"\n[Phase1] Scheduling {len(sorted_families)} families "
          f"(staged: half of days edge slots -> +quarter -> middle slots -> last quarter any slot)")
    placed_total = 0
    stage_hits: Dict[str, int] = defaultdict(int)
    for nc, cids in sorted_families:
        if nc in state.placed_families:
            continue
        group = [course_by_id[cid] for cid in cids if cid in course_by_id and cid not in scheduled_ids]
        if not group:
            state.placed_families.add(nc)
            continue
        true_total = family_total_students(group)
        rejection_counts: Dict[str, int] = defaultdict(int)
        family_placed, attempts, stage = _place_family_in_stages(
            nc, group, state, scheduled_ids, rejection_counts, where="schedule_families_first")
        if family_placed:
            placed_total += len(group)
            state.placed_families.add(nc)
            stage_hits[stage or "?"] += 1
        else:
            top = sorted(rejection_counts.items(), key=lambda kv: -kv[1])
            print(f"  [Phase1-DIAGNOSIS] '{nc}' ({len(group)} variants, "
                  f"{true_total} students) UNPLACED after {attempts} date/slot "
                  f"attempts. Rejection breakdown: {dict(top)}")
            if nc in FAMILY_DIAGNOSE_CODES:
                _diagnose_family_failure_detailed(
                    nc, group, state, all_courses, scheduled_ids, state.dates_in_order(),
                    state.daytime_slots_list + state.evening_slots
                )
            if nc in state.strict_norm_codes:
                for c in group:
                    state.strict_locked_ids.add(c.id)
                print(f"  [WARN] '{nc}' STRICTLY designated — left unscheduled")
    print(f"[Phase1] Placed {placed_total} variants; stages used: {dict(stage_hits)}")
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
        key=lambda c: (state.cohort_rank(c), -course_student_count(c))   # v74 light cohorts first
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
            if _same_pass_conflict(course, placed_in_slot[py_key], date, slot_start):
                continue
        reason = _check_hard_constraints(course, date, slot_start, state)
        if reason:
            continue
        if not relax_consecutive and state.cohort_in_cooling(course, date, slot_start):
            _jr(course, date, slot_start, "cohort-cooling", "_fill_slot_with_program")
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
    # v72: middle (afternoon) slot first, then morning — see
    # individual_priority_slots for why.
    day_slots = state.afternoon_slots + state.morning_slots
    eve_slots = state.evening_slots
    program_order = sorted(
        pending_by_program.items(),
        key=lambda kv: (min([state.cohort_rank(c) for c in kv[1] if c.id not in scheduled_ids] or [10_000]),
                        -len([c for c in kv[1] if c.id not in scheduled_ids])),   # v74 light cohorts first
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
            key=lambda c: (state.cohort_rank(c), -course_student_count(c), -state.priority_score(c))
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
                    if _same_pass_conflict(course, placed_in_slot[py_key], date, ss):
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
        state.compact_level = (2 if pass_idx < 2 else 1) if COMPACT_COHORT_SCHEDULING else 0   # v75 ladder
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
        # v71: same A->E day-window staging as PhaseB / Phase1
        family_placed, _att, _stg = _place_family_in_stages(
            nc, stuck, state, scheduled_ids, where="cross_day_fill_pass")
        if family_placed:
            placed += len(stuck)
    unscheduled_individual = sorted(
        [c for c in all_courses if c.id not in scheduled_ids and
         normalize_course_code(c.course_code or "") not in state.analysis.shared_unit_groups],
        key=lambda c: -state.priority_score(c)
    )
    sorted_dates = state.get_sorted_dates_for_pool(dates, unscheduled_individual)
    for date_obj, _ in sorted_dates:
        # v72: middle-first for individuals — see individual_priority_slots.
        for ss, se in state.individual_priority_slots:
            if not state.slot_has_any_venue_space(date_obj, ss, 1):
                continue
            placed_in_slot = defaultdict(list)
            for course in unscheduled_individual:
                if course.id in scheduled_ids:
                    continue
                pk = state._py_key(course)
                if pk and pk in placed_in_slot:
                    if _same_pass_conflict(course, placed_in_slot[pk], date_obj, ss):
                        continue
                needed = course_student_count(course)
                if not _slot_space_ok(state, date_obj, ss, needed, course):
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
        sorted_dates = state.dates_in_order()

        # Try joint placement first — v71: same A->E day-window staging
        rescued, _att, _stg = _place_family_in_stages(
            nc, variants, state, scheduled_ids, where="family_split_rescue_pass")
        if rescued:
            placed_total += len(variants)
            print(f"  [Phase3b] '{nc}' placed in stage {FAMILY_STAGE_LABELS.get(_stg, _stg)}")

        # ALL-OR-NOTHING, NO EXCEPTIONS: if the joint pass above couldn't
        # seat every remaining variant of this family in one atomic
        # placement, the family stays fully unscheduled here. There used to
        # be a second-chance loop that placed "whichever variants currently
        # fit" together and then dropped any leftovers into individual
        # placement — that is precisely what produced partial families (e.g.
        # 3 of 14 BOTA 111 sections timetabled while the other 11 had no
        # exam slot at all, and once those 3 locked the family to that slot,
        # the rest could never join them there or anywhere else). Since the
        # same family, in the same students-share-the-same-physical-exam
        # sense, cannot legitimately sit two different times, seating a
        # subset is not a partial success — it is actively wrong, because it
        # tells some students they have an exam while their classmates in
        # the same course don't. We already tried every date and every slot
        # combination for a single-venue AND a split-across-venues joint
        # placement above (place_merged_family), so a further per-course
        # loop would not find any placement the joint pass didn't already
        # consider — it would only be able to seat a subset, which we must
        # not do. `family_exhausted` is kept purely as a diagnostic marker
        # now (it no longer grants any pass permission to scatter members).
        if not rescued:
            state.family_exhausted.add(nc)
            total_needed = family_total_students(variants)
            best_capacity_seen = 0
            # CRITICAL: don't just measure raw venue capacity — that alone
            # produced a misleading message (e.g. "9376 seats free, needs
            # capacity review" for a family that was actually blocked by
            # cohort/lecturer conflicts on every single slot, never by
            # capacity at all). Tally the SAME rejection reasons Phase1
            # diagnoses, so the message points at the real blocker.
            rejection_counts: Dict[str, int] = defaultdict(int)
            attempts = 0
            for date_obj, _ in sorted_dates:
                for ss, se in state.daytime_slots_list + state.evening_slots:
                    free_total = sum(rem for _v, rem in state.get_free_venues(date_obj, ss))
                    if free_total > best_capacity_seen:
                        best_capacity_seen = free_total
                    attempts += 1
                    reason = _diagnose_family_constraints(variants, date_obj, ss, state)
                    if reason:
                        rejection_counts[reason] += 1
                    elif free_total < total_needed:
                        rejection_counts["insufficient_total_venue_capacity"] += 1
                    else:
                        rejection_counts["venue_layout_could_not_fit"] += 1
            top = sorted(rejection_counts.items(), key=lambda kv: -kv[1])
            capacity_related = sum(
                n for r, n in rejection_counts.items()
                if r in ("insufficient_total_venue_capacity", "venue_layout_could_not_fit")
            )
            if top and top[0][0] not in ("insufficient_total_venue_capacity", "venue_layout_could_not_fit"):
                guidance = (
                    "This is a cohort/lecturer scheduling conflict, NOT a capacity "
                    "shortfall — venue capacity was sufficient in every slot checked. "
                    "Needs either more exam slots/days, or a review of which students "
                    "are double-booked across these sections."
                )
            elif capacity_related:
                guidance = "This needs manual capacity/venue review."
            else:
                guidance = "Cause unclear — review rejection breakdown below."
            print(f"  [WARN] '{nc}' ({len(variants)} sections, {total_needed} students) "
                  f"could not be seated TOGETHER on any date/slot and will remain fully "
                  f"UNSCHEDULED (no partial placement) after {attempts} attempts. "
                  f"Rejection breakdown: {dict(top)}. Largest combined free venue "
                  f"capacity seen at any single slot: {best_capacity_seen}. {guidance}")

    print(f"[Phase3b] Rescued {placed_total} variants")
    return placed_total

def forced_fallback_pass(all_courses, state, scheduled_ids):
    total_placed = 0
    dates = state.dates_in_order()
    # v72: middle-first for individuals — see individual_priority_slots.
    # Sweep 3 still runs the family staging (_place_family_in_stages) for
    # its own edge-first windows before this list is consulted, so family
    # placement is unaffected.
    all_slots = state.individual_priority_slots
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
                # v71: same A->E day-window staging
                _ok, _att, _stg = _place_family_in_stages(
                    nc, group_unsched, state, scheduled_ids, where="forced_fallback_pass")
                if _ok:
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
                    # Family-group members are NEVER placed one at a time here,
                    # regardless of family_exhausted — only the atomic
                    # place_merged_family / _commit_distributed_minimal path
                    # (used in Phase3b and Phase4-sweep3 above) is allowed to
                    # write family members to the DB, and only all together.
                    if nc in state.analysis.shared_unit_groups:
                        _jr(course, None, None, "skipped: family member (placed only whole, via the family phases)")
                        continue
                    pk = state._py_key(course)
                    if pk and pk in placed_in_slot:
                        if _same_pass_conflict(course, placed_in_slot[pk], date_obj, ss):
                            continue
                    needed = course_student_count(course)
                    if not _slot_space_ok(state, date_obj, ss, needed, course):
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
    # v72: middle-first for individuals — see individual_priority_slots.
    all_slots = state.individual_priority_slots
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
                # Family-group members are NEVER placed one at a time here,
                # regardless of family_exhausted — only the atomic
                # place_merged_family / _commit_distributed_minimal path is
                # allowed to write family members to the DB, and only all
                # together.
                if nc in state.analysis.shared_unit_groups:
                    _jr(course, None, None, "skipped: family member (placed only whole, via the family phases)")
                    continue
                pk = state._py_key(course)
                if pk and pk in placed_in_slot:
                    if _same_pass_conflict(course, placed_in_slot[pk], date_obj, ss):
                        continue
                needed = course_student_count(course)
                if not _slot_space_ok(state, date_obj, ss, needed, course):
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
    # v72: middle-first for individuals — see individual_priority_slots.
    all_slots = state.individual_priority_slots
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
                # Family-group members are NEVER placed one at a time here,
                # regardless of family_exhausted — only the atomic
                # place_merged_family / _commit_distributed_minimal path is
                # allowed to write family members to the DB, and only all
                # together.
                if nc in state.analysis.shared_unit_groups:
                    _jr(course, None, None, "skipped: family member (placed only whole, via the family phases)")
                    continue
                pk = state._py_key(course)
                if pk and pk in placed_in_slot:
                    if _same_pass_conflict(course, placed_in_slot[pk], date_obj, ss):
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
                if not _slot_space_ok(state, date_obj, ss, needed, course):
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
        rows = _temp_qs().filter(
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
    with _trace_suspended():
        _diagnose_unscheduled_courses_sampled(all_courses, state, scheduled_ids, sample_size)

def _diagnose_unscheduled_courses_sampled(all_courses, state, scheduled_ids, sample_size: int = 25) -> None:
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
                if not _slot_space_ok(state, date_obj, ss, needed, course):
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

def shared_room_last_resort_pass(all_courses, state, scheduled_ids):
    """Phase8b — LAST RESORT: let two DIFFERENT courses share a room.

    Every earlier phase refuses to put a course in a room another course
    (different normalized code) already uses; same-code sections (e.g.
    MATH 302 from several programs) may always share. Only when every day
    and every slot has been tried that way and a course is STILL unplaced
    does this pass allow cross-course sharing. It still honours every other
    rule (student/lecturer clashes, daily limits, capacity) and within a
    slot the venue choosers still prefer a free room to a shared one. Days
    with the FEWEST exams are tried first, so sharing lands on the quietest
    days rather than piling onto busy ones.
    """
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0
    print(f"\n[Phase8b-SharedRoomLastResort] {len(unscheduled)} unscheduled — allowing "
          f"different courses to share a room (last resort)")
    placed_before = len(scheduled_ids)
    state.allow_cross_course_sharing = True
    try:
        course_by_id = {c.id: c for c in all_courses}
        for nc, cids in state.analysis.shared_unit_groups.items():
            variants = [course_by_id[cid] for cid in cids
                        if cid in course_by_id and cid not in scheduled_ids]
            if variants:
                _place_family_in_stages(nc, variants, state, scheduled_ids,
                                        where="shared_room_last_resort_pass")
        dates = [d for d, _ in state.dates_in_order()]
        individuals = sorted(
            [c for c in all_courses if c.id not in scheduled_ids and
             normalize_course_code(c.course_code or "") not in state.analysis.shared_unit_groups],
            key=lambda c: -course_student_count(c))
        for course in individuals:
            if course.id in scheduled_ids:
                continue
            if _course_already_in_db(course):
                scheduled_ids.add(course.id)
                continue
            needed = course_student_count(course)
            day_order = sorted(dates, key=lambda d: (state.get_daily_penalty(course, d),
                                                     state.daily_load.get(d, 0)))
            done = False
            for date_obj in day_order:
                if done:
                    break
                for ss, se in state.individual_priority_slots:
                    if not _slot_space_ok(state, date_obj, ss, needed, course):
                        continue
                    if _check_hard_constraints(course, date_obj, ss, state):
                        continue
                    if try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                        relax_consecutive=True, allow_split=True,
                                        allow_room_sharing=True):
                        done = True
                        break
    finally:
        state.allow_cross_course_sharing = False
    placed = len(scheduled_ids) - placed_before
    print(f"[Phase8b-SharedRoomLastResort] Placed {placed}")
    return placed

def ultimate_fallback_pass(all_courses, state, scheduled_ids):
    unscheduled = [c for c in all_courses if c.id not in scheduled_ids]
    if not unscheduled:
        return 0
    print(f"\n[Phase8-Ultimate] {len(unscheduled)} unscheduled")
    state.allow_strict_rescue = True
    placed_before = len(scheduled_ids)
    dates = state.dates_in_order()
    # v72: middle-first for individuals — see individual_priority_slots.
    all_slots = state.individual_priority_slots
    course_by_id = {c.id: c for c in all_courses}
    for nc, cids in state.analysis.shared_unit_groups.items():
        variants = [course_by_id[cid] for cid in cids if cid in course_by_id and cid not in scheduled_ids]
        if not variants:
            continue
        # v71: same A->E day-window staging (strict rescue is honoured by
        # _diagnose_family_constraints via state.allow_strict_rescue)
        rescued, _att, _stg = _place_family_in_stages(
            nc, variants, state, scheduled_ids, where="ultimate_fallback_pass")
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
                    _jr(course, date_obj, ss, "lecturer-conflict", "ultimate_fallback_pass")
                    continue
                if not state.students_available(course, date_obj, ss):
                    _jr(course, date_obj, ss, "student-conflict", "ultimate_fallback_pass")
                    continue
                if state.check_norm_code_day_conflict(course, date_obj):
                    _jr(course, date_obj, ss, "norm-code-day-conflict", "ultimate_fallback_pass")
                    continue
                if state.check_shared_unit_conflict(course, date_obj, ss):
                    _jr(course, date_obj, ss, "shared-unit-conflict", "ultimate_fallback_pass")
                    continue
                if state.check_family_conflict(course, date_obj, ss):
                    _jr(course, date_obj, ss, "family-conflict", "ultimate_fallback_pass")
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
    # v72: middle-first for individuals — see individual_priority_slots.
    all_slots = state.individual_priority_slots
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
                # Family-group members are NEVER placed one at a time here,
                # regardless of family_exhausted — only the atomic
                # place_merged_family / _commit_distributed_minimal path is
                # allowed to write family members to the DB, and only all
                # together.
                if nc in state.analysis.shared_unit_groups:
                    _jr(course, None, None, "skipped: family member (placed only whole, via the family phases)")
                    continue
                pk = state._py_key(course)
                if pk and pk in placed_in_slot:
                    if _same_pass_conflict(course, placed_in_slot[pk], date_obj, ss):
                        continue
                needed = course_student_count(course)
                if not _slot_space_ok(state, date_obj, ss, needed, course):
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


# How far OVER the hard daily limit the last-resort pass may go (0 = never,
# 1 = at most one exam above the limit, None = no cap). It always picks the
# least-overloaded slot first, so a higher cap only matters when nothing
# gentler exists.
DAILY_LIMIT_RELAX_MAX_EXTRA: Optional[int] = None

def _daily_limit_overage(state, course, date) -> Tuple[int, str]:
    """How many exams OVER the hard daily limit placing `course` on `date`
    would be (0 = not blocked by the cap at all), plus a readable reason."""
    over = 0
    parts: List[str] = []
    pk = state._daily_limit_key(course)
    if pk:
        _, hl = state.cohort_daily_limits.get(pk, (2, 3))
        cnt = state.cohort_daily_count.get((pk, date), 0)
        if cnt >= hl:
            over = max(over, cnt - hl + 1)
            parts.append(f"cohort already has {cnt} exam(s) that day (hard limit {hl})")
    lid = state._cached_lecturer_id(course)
    if lid:
        _, lhl = state.lecturer_daily_limits.get(lid, (2, 3))
        lcnt = state.lecturer_daily_count.get((lid, date), 0)
        if lcnt >= lhl:
            over = max(over, lcnt - lhl + 1)
            parts.append(f"lecturer_id={lid} already has {lcnt} exam(s) that day (hard limit {lhl})")
    return over, "; ".join(parts)

def daily_limit_relaxation_pass(all_courses, state, scheduled_ids) -> int:
    """LAST RESORT. For every course still unscheduled after all normal
    phases, retry with ONLY the per-day hard cap lifted. Every other hard
    rule (student clash, lecturer clash, family/shared-unit/day locks,
    strict venues, venue space) must still pass. Among the slots that then
    work, the LEAST-overloaded one is chosen (fewest exams over the limit,
    then not adjacent to the cohort's other exams, then the lighter day,
    then earliest). Family members are skipped: the daily cap never applies
    to a family placement (_family_constraints_ok does not check it), so it
    cannot be what blocks one."""
    sync_scheduled_ids_from_db(scheduled_ids)
    unscheduled = [
        c for c in all_courses
        if c.id not in scheduled_ids
        and normalize_course_code(getattr(c, "course_code", "") or "")
        not in state.analysis.shared_unit_groups
    ]
    if not unscheduled:
        return 0
    print(f"\n[Phase9d-DailyLimitRelaxation] {len(unscheduled)} unscheduled non-family course(s) — "
          f"retrying with ONLY the per-day hard cap lifted (all other rules still enforced)")
    unscheduled.sort(key=lambda c: -state.priority_score(c))
    dates = state.dates_in_order()
    # v72: middle-first for individuals — see individual_priority_slots.
    all_slots = state.individual_priority_slots
    placed = 0
    for course in unscheduled:
        if course.id in scheduled_ids:
            continue
        if _course_already_in_db(course):
            scheduled_ids.add(course.id)
            continue
        needed = course_student_count(course)
        candidates: List[Tuple] = []
        state.relax_daily_limit = True
        try:
            for date_obj, _wd in dates:
                over, why = _daily_limit_overage(state, course, date_obj)
                for ss, se in all_slots:
                    if _check_hard_constraints(course, date_obj, ss, state):
                        continue          # some OTHER hard rule blocks this slot — never relaxed
                    if not _slot_space_ok(state, date_obj, ss, needed, course):
                        continue
                    cooling = state.cohort_in_cooling(course, date_obj, ss)
                    _edge_flag = 0 if state._slot_start_to_idx.get(ss, 0) in state._middle_slot_idx else 1
                    candidates.append((over, cooling, _edge_flag, state.daily_load.get(date_obj, 0),
                                       date_obj, state._slot_start_to_idx.get(ss, 0), ss, se, why))
        finally:
            state.relax_daily_limit = False
        if DAILY_LIMIT_RELAX_MAX_EXTRA is not None:
            candidates = [c for c in candidates if c[0] <= DAILY_LIMIT_RELAX_MAX_EXTRA]
        candidates.sort(key=lambda c: c[:6])
        done = False
        for over, cooling, _ef, _load, date_obj, _idx, ss, se, why in candidates:
            state.relax_daily_limit = True
            try:
                if over > 0:
                    _EXTRA_NOTE[course.id] = (f"DAILY LIMIT RELAXED (last resort): {why}; every other rule passed; "
                                              f"least-overloaded slot chosen (over the limit by {over})")
                ok = try_place_course(course, date_obj, ss, se, state, scheduled_ids,
                                      relax_consecutive=True, allow_split=True, allow_room_sharing=True)
            finally:
                state.relax_daily_limit = False
                _EXTRA_NOTE.pop(course.id, None)
            if ok:
                placed += 1
                done = True
                if over > 0:
                    state.daily_limit_relaxed.append({
                        "course": getattr(course, "course_code", ""), "date": date_obj, "slot": ss,
                        "over_by": over, "why": why})
                    print(f"  [Phase9d] WARNING {course.course_code} placed {date_obj} {ss} with the daily limit "
                          f"EXCEEDED by {over}: {why}")
                else:
                    print(f"  [Phase9d] {course.course_code} placed {date_obj} {ss} (cap not actually exceeded there)")
                break
        if not done:
            _jr(course, None, None, "daily-limit relaxation found no slot where every OTHER rule passes")
            print(f"  [Phase9d] {course.course_code} still unplaceable even with the daily limit lifted — "
                  f"another hard rule blocks every slot ({len(candidates)} candidate slot(s))")
    print(f"[Phase9d-DailyLimitRelaxation] Placed {placed}  "
          f"(over the daily limit: {len(state.daily_limit_relaxed)})")
    return placed

def evacuate_foreign_courses_from_split_venues_pass(all_courses, state, scheduled_ids) -> int:
    """
    Post-placement pass: clear unrelated ("foreign") courses out of any
    venue that is currently hosting part of a split, whenever a better
    home exists for them elsewhere in the same slot.

    WHY THIS EXISTS
    ─────────────────────────────────────────────────────────────────
    Courses are placed one at a time, in phase order, with no lookahead.
    find_best_venue_no_split() deliberately favours the TIGHTEST
    sufficient venue for whatever course is being placed right now
    (see its docstring) — a good rule in isolation, but it has no idea
    a much bigger course is coming later this same slot and will need
    to split. So a small, easily-movable course can grab the one
    mid-sized room (say, a 100-cap venue) that would have let a later
    160-student course split cleanly into two rooms instead of three,
    or let a 110-student course fit in ONE room instead of splitting
    at all. consolidate_underfilled_small_venues_pass() cannot fix
    this after the fact because it only ever looks at venues with
    cap < SMALL_VENUE_CAP_THRESHOLD — a 100-cap (or bigger) room
    hosting a split is invisible to it by design.

    This pass looks specifically at venues that ARE part of a split
    this slot, finds any OTHER course sharing that room (one that is
    NOT part of the split and not itself mid-split), and tries to
    relocate it to a different, compatible venue in the same slot that
    has room — freeing the split venue's capacity. It runs BEFORE
    Phase9c (merge_back_split_placements_pass) so a split that only
    needed that freed space can actually collapse into fewer rooms.

    This never touches the split's own rows, never moves a course into
    a real student/lecturer conflict (same compatibility rule as
    consolidate_underfilled_small_venues_pass), and only moves a course
    when a genuinely better slot exists — a course with nowhere better
    to go simply stays put.
    """
    print(f"\n[Phase9a2-EvacuateSplitVenues] Freeing split venues of unrelated courses")
    entries = list(_temp_qs().values(
        "id", "course_allocation_id", "venue_id", "date", "start_time", "end_time",
        "allocated_students"
    ))
    if not entries:
        print(f"[Phase9a2-EvacuateSplitVenues] No placements to check")
        return 0

    course_by_id = {c.id: c for c in all_courses}
    slot_groups = defaultdict(list)
    for e in entries:
        slot_groups[(e["date"], e["start_time"])].append(e)

    def _compatible(course_a, course_b) -> bool:
        if course_a.id == course_b.id:
            return True
        if normalize_course_code(getattr(course_a, "course_code", "") or "") == \
           normalize_course_code(getattr(course_b, "course_code", "") or ""):
            return True
        if _combined_group_are_paired(course_a.id, course_b.id):
            return True
        if not state.allow_cross_course_sharing:
            return False        # v73: never merge two different courses into one room
        return not courses_share_students(course_a, course_b)

    moves_performed = 0
    for (date_obj, ss), slot_entries in slot_groups.items():
        by_venue = defaultdict(list)
        course_venues = defaultdict(set)
        for e in slot_entries:
            by_venue[e["venue_id"]].append(e)
            course_venues[e["course_allocation_id"]].add(e["venue_id"])

        split_cids = {cid for cid, vids in course_venues.items() if len(vids) > 1}
        if not split_cids:
            continue  # nothing split this slot — this pass has nothing to do

        split_venue_ids = {
            vid for vid, ventries in by_venue.items()
            if any(e["course_allocation_id"] in split_cids for e in ventries)
        }
        if not split_venue_ids:
            continue

        # Build a live capacity picture for every venue used this slot, so a
        # moved-out course's new home is checked against real remaining
        # room (including other moves already made earlier in this loop).
        venue_used: Dict[int, int] = {}
        for vid, ventries in by_venue.items():
            venue_used[vid] = sum(
                _row_seats(e.get("allocated_students"), course_by_id[e["course_allocation_id"]])
                for e in ventries if e["course_allocation_id"] in course_by_id
            )

        for split_vid in sorted(split_venue_ids, key=lambda v: venue_used.get(v, 0), reverse=True):
            foreign_entries = [
                e for e in by_venue.get(split_vid, [])
                if e["course_allocation_id"] not in split_cids
            ]
            if not foreign_entries:
                continue
            for e in foreign_entries:
                cid = e["course_allocation_id"]
                course = course_by_id.get(cid)
                if not course:
                    continue
                needed = _row_seats(e.get("allocated_students"), course)

                # Candidate destinations: every OTHER venue used this slot,
                # plus completely free venues, ranked smallest-sufficient
                # first among already-occupied+compatible rooms (mirrors
                # find_best_venue_no_split's "tightest fit" preference) and
                # only falling back to a free venue if no occupied one fits.
                best_occupied = None
                for vid, cap in ((v.id, state.venue_examcap.get(v.id, 0)) for v in state.venues):
                    if vid == split_vid or vid in split_venue_ids:
                        continue
                    if cap <= 0:
                        continue
                    used = venue_used.get(vid)
                    if used is None:
                        continue  # not occupied this slot — handled below
                    rem = cap - used
                    if rem < needed:
                        continue
                    target_courses = [
                        course_by_id[oe["course_allocation_id"]]
                        for oe in by_venue.get(vid, [])
                        if oe["course_allocation_id"] in course_by_id
                    ]
                    if not all(_compatible(course, tc) for tc in target_courses):
                        continue
                    if best_occupied is None or rem < best_occupied[1]:
                        best_occupied = (vid, rem)

                target_vid = best_occupied[0] if best_occupied else None
                if target_vid is None:
                    rem_needed = needed
                    best_free = None
                    for v in state.venues_by_cap_desc:
                        if v.id == split_vid or v.id in split_venue_ids:
                            continue
                        if v.id in venue_used:
                            continue  # occupied — already considered above
                        cap = state.venue_examcap.get(v.id, 0)
                        if cap < rem_needed:
                            continue
                        state_rem = state.venue_remaining(v.id, date_obj, ss)
                        if state_rem < rem_needed:
                            continue
                        if best_free is None or cap < best_free[1]:
                            best_free = (v.id, cap)
                    if best_free:
                        target_vid = best_free[0]

                if target_vid is None:
                    continue  # nowhere better — leave this course where it is

                try:
                    with transaction.atomic():
                        ExamTempTimetable.objects.filter(id=e["id"]).update(venue_id=target_vid)
                except Exception as ex:
                    print(f"  [EvacuateSplit] Error moving course {cid}: {ex}")
                    continue

                state.release_venue(split_vid, date_obj, ss, needed, course)
                state.consume_venue(target_vid, date_obj, ss, needed, course)

                by_venue[split_vid].remove(e)
                by_venue.setdefault(target_vid, []).append(e)
                venue_used[split_vid] = venue_used.get(split_vid, 0) - needed
                venue_used[target_vid] = venue_used.get(target_vid, 0) + needed
                moves_performed += 1

                code = (getattr(course, "course_code", "") or "").strip()
                target_venue = state.venue_by_id.get(target_vid)
                _jr_move(course, date_obj, ss, state.venue_by_id.get(split_vid, split_vid),
                         target_venue or target_vid, "EVACUATED",
                         "moved out of a split venue to free room for the split course")
                if DEBUG_VERBOSE:
                    print(f"  [EvacuateSplit] {course.course_code} ({needed} students) moved out of "
                          f"split-venue -> {getattr(target_venue, 'code', target_vid)}, "
                          f"freeing room for the split")

    print(f"[Phase9a2-EvacuateSplitVenues] Moved {moves_performed} unrelated course(s) "
          f"out of split venues")
    return moves_performed

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
    entries = list(_temp_qs().values(
        "id", "course_allocation_id", "venue_id", "date", "start_time", "end_time",
        "allocated_students"
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
        if not state.allow_cross_course_sharing:
            return False        # v73: never merge two different courses into one room
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
        _course_venues = defaultdict(set)
        for e in slot_entries:
            by_venue[e["venue_id"]].append(e)
            _course_venues[e["course_allocation_id"]].add(e["venue_id"])
        # A course sitting in >1 venue this slot is a deliberate split;
        # moving just one half would corrupt it, so those rows stay put.
        split_cids = {cid for cid, vids in _course_venues.items() if len(vids) > 1}
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
                _row_seats(e.get("allocated_students"), course_by_id[e["course_allocation_id"]])
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
                    if cid in split_cids:
                        continue
                    needed = _row_seats(e.get("allocated_students"), course)
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
                    _jr_move(course, date_obj, ss, donor["venue"], target["venue"], "CONSOLIDATED",
                             "small under-filled venue emptied into a fuller one")
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
    entries = list(_temp_qs().values(
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
                        _jr_move(course_a, date_obj, ss, venue_a, venue_b, "SWAPPED", "swap reduces wasted seats")
                        _jr_move(course_b, date_obj, ss, venue_b, venue_a, "SWAPPED", "swap reduces wasted seats")
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
        db_count = _temp_qs().values("course_allocation_id").distinct().count()
        if db_count == cached_count:
            scheduled_ids.update(_already_scheduled_cache)
            return 0
    db_ids = set(_temp_qs().values_list("course_allocation_id", flat=True).distinct())
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
    state.cohort_day_idx.clear()
    state.cohort_special_idx.clear()
    state.lecturer_daily_count.clear()
    state.family_slot.clear()
    state.family_day.clear()
    state.shared_unit_lock.clear()
    state.norm_code_day_lock.clear()
    state.venue_occupants.clear()
    course_by_id = {c.id: c for c in all_courses}
    entries = list(_temp_qs().values(
        "course_allocation_id", "venue_id", "date", "start_time", "allocated_students"))
    for e in entries:
        vid = e["venue_id"]
        cid = e["course_allocation_id"]
        date_obj = e["date"]
        ss = e["start_time"]
        course = course_by_id.get(cid)
        # THIS venue's share of the course — not its full enrollment (see _row_seats).
        n = _row_seats(e.get("allocated_students"), course) if course else 1
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
                _di = state._date_to_idx.get(date_obj)
                if _di is not None:
                    state.cohort_day_idx[pk][_di] += 1
                    if state.is_special_course(course):
                        state.cohort_special_idx[pk][_di] += 1
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
    # rebuild_state_from_db() cleared every busy-tracker above and rebuilt
    # them purely from _temp_qs() (this run's own tt_scope). That wipes out
    # the cross-department/cross-AllocationSet awareness
    # preload_cross_scope_busy_state() set up before Phase A — re-apply it
    # now so a later fallback pass can't schedule straight into a slot
    # another department's exam already occupies. Must run BEFORE the
    # free_slots scan below, or a slot only "free" because we haven't
    # re-added the external courses yet would be wrongly offered up.
    preload_cross_scope_busy_state(state, all_courses)

    free_slots = []
    for date_obj, _ in state.dates_in_order():
        for ss, se in state.all_slots_ordered:
            if state.slot_has_any_venue_space(date_obj, ss):
                free_slots.append((date_obj, ss, se))
    return free_slots

def sync_lecturer_busy_from_db(state, all_courses):
    course_by_id = {c.id: c for c in all_courses}
    entries = list(_temp_qs().values("course_allocation_id", "date", "start_time"))
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
    entries = list(_temp_qs().values("course_allocation_id", "date", "start_time"))
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
    entries = list(_temp_qs().values("course_allocation_id", "date", "start_time"))
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
    rows = _temp_qs().values("venue_id", "date", "start_time",
                             "course_allocation_id", "allocated_students")
    for r in rows:
        course = course_by_id.get(r["course_allocation_id"])
        n = _row_seats(r.get("allocated_students"), course) if course else 0
        usage[(r["venue_id"], r["date"], r["start_time"])] += n
    violations = 0
    for (vid, date, ss), total_assigned in usage.items():
        cap = state.venue_examcap.get(vid, 0)
        if total_assigned > cap:
            violations += 1
            print(f"[Audit-OVERCAP] VIOLATION: venue_id={vid} date={date} slot={ss} | "
                  f"assigned={total_assigned} > cap={cap} (over by {total_assigned - cap})")
    return violations

def merge_back_split_placements_pass(all_courses, state, scheduled_ids) -> int:
    """
    Consolidation pass that undoes splits made under temporary capacity
    pressure once a single room can hold the thing after all.

    Stage 1 (unit level): a whole family / solo course that spans several
    rooms at one (date, slot) is collapsed into ONE room if one room can
    hold the whole unit.
    Stage 2 (course level): any individual course still split across rooms
    (typically one variant of a family whose TOTAL is too big for one room)
    is pulled into a single room if one can hold all of that course.

    Seat counts come from state.venue_occupants (the per-course ledger kept
    by consume_venue/release_venue). The moved row is written with the
    course's TOTAL seats in allocated_students; state is released per old
    venue using that venue's own share.

    Run AFTER the fallback / consolidation / swap passes and BEFORE
    _audit_and_fix_duplicate_placements.
    """
    course_by_id = {c.id: c for c in all_courses}

    def _course_seats_by_venue(cid, date, slot_start) -> Dict[int, int]:
        out: Dict[int, int] = {}
        for v in state.venues:
            for c2, s2 in state.venue_occupants.get((v.id, date, slot_start), []):
                if c2 == cid:
                    out[v.id] = out.get(v.id, 0) + s2
        return out

    def _move_course_to_venue(cid, oc, date, slot_start, target, per_venue) -> bool:
        total = sum(per_venue.values())
        old_rows = list(ExamTempTimetable.objects.filter(
            course_allocation_id=cid, date=date, start_time=slot_start))
        if not old_rows:
            return False
        slot_end = old_rows[0].end_time
        with transaction.atomic():
            ExamTempTimetable.objects.filter(
                course_allocation_id=cid, date=date, start_time=slot_start,
            ).exclude(venue_id=target.id).delete()
            updated = ExamTempTimetable.objects.filter(
                course_allocation_id=cid, venue_id=target.id,
                date=date, start_time=slot_start,
            ).update(allocated_students=total)
            if not updated:
                ExamTempTimetable.objects.create(
                    course_allocation=oc, venue=target, date=date,
                    day=date.strftime("%A"), start_time=slot_start, end_time=slot_end,
                    allocated_students=total,
                )
        for vid, seats in per_venue.items():
            state.release_venue(vid, date, slot_start, seats, oc)
        _jr_move(oc, date, slot_start,
                 "+".join(state.venue_by_id[v].code for v in per_venue if v in state.venue_by_id),
                 target, "MERGED-BACK", "split course pulled into one room")
        if not state.consume_venue(target.id, date, slot_start, total, oc):
            return False  # rebuild_state_from_db below resyncs from the DB
        state.record_combined_group_venue(oc, date, slot_start, target.id)
        return True

    fixed = 0

    # ---------------- Stage 1: whole family / solo course into one room ----
    merged_by_course: Dict[int, Tuple[Any, List[int]]] = {}
    for mg in MergedCourseGroup.objects.filter(
        date__isnull=False, start_time__isnull=False
    ).prefetch_related("merged_courses"):
        member_ids = [c.id for c in mg.merged_courses.all()]
        for cid in member_ids:
            merged_by_course[cid] = (mg, member_ids)

    units: Dict[Tuple, dict] = {}
    for (vid, date, slot_start), occupants in list(state.venue_occupants.items()):
        for course_id, _seats in occupants:
            course = course_by_id.get(course_id)
            if course is None:
                continue
            fam = merged_by_course.get(course_id)
            if fam is not None:
                mg, member_ids = fam
                key = ("family", mg.id, date, slot_start)
                unit_course_ids = set(member_ids)
            else:
                key = ("solo", course_id, date, slot_start)
                unit_course_ids = {course_id}
            if key in units:
                continue
            unit_courses = [course_by_id[cid] for cid in unit_course_ids if cid in course_by_id]
            if not unit_courses:
                continue
            units[key] = {
                "date": date, "slot_start": slot_start,
                "courses": unit_courses, "course_ids": unit_course_ids,
                "mg": fam[0] if fam else None,
                "total_needed": family_total_students(unit_courses) if fam else course_student_count(course),
            }

    for unit in units.values():
        date, slot_start = unit["date"], unit["slot_start"]
        unit_course_ids = unit["course_ids"]
        total_needed = unit["total_needed"]

        venue_seats: Dict[int, int] = defaultdict(int)
        for v in state.venues:
            for cid, seats in state.venue_occupants.get((v.id, date, slot_start), []):
                if cid in unit_course_ids:
                    venue_seats[v.id] += seats

        if len(venue_seats) <= 1:
            continue
        if sum(venue_seats.values()) < total_needed:
            continue

        candidates = []
        for v in state.venues:
            cap = state.venue_examcap.get(v.id, 0)
            if cap < total_needed:
                continue
            own_here = venue_seats.get(v.id, 0)
            others_here = state.venue_usage.get((v.id, date, slot_start), 0) - own_here
            if cap - others_here < total_needed:
                continue
            if v.id not in venue_seats and state.venue_has_occupants(v.id, date, slot_start):
                if any(not _can_share_venue(uc, v.id, date, slot_start, state) for uc in unit["courses"]):
                    continue
            candidates.append((cap, v))
        if not candidates:
            continue
        candidates.sort(key=lambda x: x[0])
        target = candidates[0][1]

        moved = False
        for cid in unit_course_ids:
            oc = course_by_id.get(cid)
            per_venue = _course_seats_by_venue(cid, date, slot_start)
            if not oc or not per_venue or set(per_venue) == {target.id}:
                continue
            if _move_course_to_venue(cid, oc, date, slot_start, target, per_venue):
                moved = True
        if unit["mg"] is not None and moved:
            unit["mg"].venue = target
            unit["mg"].save(update_fields=["venue"])
        if moved:
            fixed += 1
            codes = ", ".join(sorted({(c.course_code or "?") for c in unit["courses"]}))
            print(f"  [MergeBack] {codes} consolidated into {target.code} "
                  f"({total_needed} students) at {date} {slot_start}")

    # ---------------- Stage 2: individual course still split ---------------
    course_events = set()
    for (vid, date, slot_start), occupants in list(state.venue_occupants.items()):
        for cid, _seats in occupants:
            course_events.add((cid, date, slot_start))
    pending = []
    for cid, date, slot_start in course_events:
        per_venue = _course_seats_by_venue(cid, date, slot_start)
        if len(per_venue) > 1:
            pending.append((sum(per_venue.values()), cid, date, slot_start))
    pending.sort(key=lambda t: (-t[0], t[1]))

    for total, cid, date, slot_start in pending:
        oc = course_by_id.get(cid)
        if oc is None:
            continue
        per_venue = _course_seats_by_venue(cid, date, slot_start)  # may have changed
        if len(per_venue) <= 1:
            continue
        total = sum(per_venue.values())
        if total < course_student_count(oc):
            continue  # ledger doesn't cover the whole course — don't guess
        candidates = []
        for v in state.venues:
            cap = state.venue_examcap.get(v.id, 0)
            if cap < total:
                continue
            own_here = per_venue.get(v.id, 0)
            others_here = state.venue_usage.get((v.id, date, slot_start), 0) - own_here
            if cap - others_here < total:
                continue
            if v.id not in per_venue and state.venue_has_occupants(v.id, date, slot_start):
                if not _can_share_venue(oc, v.id, date, slot_start, state):
                    continue
            candidates.append((-own_here, cap, v))
        if not candidates:
            continue
        candidates.sort(key=lambda x: (x[0], x[1]))  # most seats already there, then smallest room
        target = candidates[0][2]
        old_codes = [state.venue_by_id[vid].code for vid in per_venue if vid in state.venue_by_id]
        if _move_course_to_venue(cid, oc, date, slot_start, target, per_venue):
            fixed += 1
            print(f"  [MergeBack-Course] {oc.course_code} ({total} students) "
                  f"{old_codes} -> {target.code} at {date} {slot_start}")

    if fixed:
        rebuild_state_from_db(state, all_courses)
    return fixed

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
    entries = list(_temp_qs().values(
        "id", "course_allocation_id", "date", "start_time", "venue_id", "allocated_students"
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
                    per_row = _row_seats(r.get("allocated_students"), course)
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

def _report_cohort_compactness(state) -> None:
    """v75 — per-day pattern of light cohorts; flags spread, holes and >cap days."""
    try:
        bad, light = [], 0
        for pk, days in state.cohort_day_idx.items():
            units = state.cohort_units.get(pk, 0)
            if not days or not units or units > LIGHT_COHORT_MAX_UNITS:
                continue
            light += 1
            ds = sorted(days)
            span = ds[-1] - ds[0] + 1
            hole = max((b - a - 1 for a, b in zip(ds, ds[1:])), default=0)
            budget = -(-units // COMPACT_TARGET_PER_DAY) + COMPACT_SPAN_SLACK
            over = max(days.values()) > state.compact_day_cap(units)
            if span > budget or hole > COMPACT_MAX_DAY_GAP - 1 or over:
                pattern = "-".join(str(days.get(i, 0)) for i in range(ds[0], ds[-1] + 1))
                bad.append((pk, units, ds[0] + 1, ds[-1] + 1, span, pattern))
        _sp_bad = []
        for pk, days in state.cohort_special_idx.items():
            ds = sorted(days)
            if not ds:
                continue
            hole = max((b - a - 1 for a, b in zip(ds, ds[1:])), default=0)
            if hole > 0 or max(days.values()) > 1:
                _sp_bad.append((pk, "-".join(str(days.get(i, 0)) for i in range(ds[0], ds[-1] + 1))))
        print(f"[Compact] designated-venue programme-years: {len(state.cohort_special_idx)}; "
              f"{len(_sp_bad)} not 1,1,1,1")
        for pk, pat in _sp_bad[:25]:
            print(f"  [Compact-Special] {pk}: designated per-day pattern {pat}")
        print(f"[Compact] {light} light programme-years; {len(bad)} not compact")
        for pk, units, first, last, span, pattern in sorted(bad, key=lambda x: -x[4])[:25]:
            print(f"  [Compact] {pk}: {units} units, exam-days {first}..{last}, per-day pattern {pattern}")
    except Exception as _e:
        print(f"[Compact] report failed: {_e}")

def classify_courses(all_courses) -> Tuple[List, List]:
    ug, pg = [], []
    for c in all_courses:
        code = getattr(c, "course_code", "") or ""
        (pg if is_postgraduate_course(code) else ug).append(c)
    return ug, pg

def _build_shared_venue_exam_groups() -> int:
    slot_map = defaultdict(list)
    entries = list(_temp_qs().values("id", "venue_id", "date", "start_time", "end_time",
                                     "course_allocation_id", "day", "allocated_students"))
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
                # v73: total = seats ACTUALLY held in THIS room. It used to add each
                # course's FULL enrollment, so a section split across rooms was
                # counted in full in every room it touched (e.g. seven ECON 111
                # sections "totalling" 633 in a 150-seat room). Use each row's
                # allocated_students (this room's share); only legacy rows with
                # no value fall back to full enrollment (see _row_seats).
                _ca_by_id = {ca.id: ca for ca in _CA.objects.filter(pk__in=cids)}
                svg.total_students = sum(
                    _row_seats(e.get("allocated_students"), _ca_by_id.get(e["course_allocation_id"]))
                    for e in group_entries)
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
        _trace_set_phase(self.name)
        since_start = self.start - _PhaseTimer._run_start
        print(f"[TIMING] >>> {self.name} started "
              f"(t+{since_start:.1f}s since run began)")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = time.time() - self.start
        _PhaseTimer._phase_history.append((self.name, elapsed))
        _trace_set_phase("between-phases")
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


def clear_exam_tables_safely(tt_scope: Optional[dict] = None):
    """
    Wipes ExamTempTimetable / MergedCourseGroup / SharedVenueExamGroup
    before a rebuild.

    Mirrors clear_tables_safely() in regular_timetable_autosheduler_algorithm.py
    exactly — same scoping rule, same DB-locked incremental-delete fallback.

    Concurrent Allocation Sets: when `tt_scope` identifies specific
    AllocationSet(s) (the TT ticked one or more on /timetable/dashboard/),
    the wipe is narrowed to ONLY rows whose course_allocation belongs to
    one of those sets — every other department's / other set's rows are
    left completely untouched. This is what stops running the exam
    scheduler for one department's set from silently deleting another
    department's already-generated draft exam timetable.

    With `tt_scope=None` (a caller that hasn't been updated, or nothing
    picked yet this session), this falls back to the exact old behaviour —
    a full wipe — so nothing currently relying on the old single-set
    assumption changes.

    SharedVenueExamGroup reaches its CourseAllocations through the M2M
    `course_allocations`, not a direct FK — filtering on that M2M can
    return the same row more than once if it matches through several
    linked CourseAllocations, so ids are resolved with .distinct() first
    and the delete is done by id__in (Django refuses to chain
    .distinct().delete() directly).
    """
    temp_qs = ExamTempTimetable.objects.all()
    merged_qs = MergedCourseGroup.objects.all()
    shared_ids = None
    if tt_scope is not None:
        temp_qs = temp_qs.filter(tt_scope_q(tt_scope, prefix="course_allocation__allocation_set"))
        merged_qs = merged_qs.filter(tt_scope_q(tt_scope, prefix="base_course__allocation_set"))
        shared_ids = list(
            SharedVenueExamGroup.objects
            .filter(tt_scope_q(tt_scope, prefix="course_allocations__allocation_set"))
            .distinct()
            .values_list("id", flat=True)
        )
        shared_qs = SharedVenueExamGroup.objects.filter(id__in=shared_ids)
    else:
        shared_qs = SharedVenueExamGroup.objects.all()

    try:
        with transaction.atomic():
            temp_qs.delete()
            merged_qs.delete()
            shared_qs.delete()
            print(
                "[AutoScheduler] Exam tables cleared successfully"
                + (" (scoped to active allocation set(s))" if tt_scope else "")
            )
    except OperationalError as e:
        if 'database is locked' in str(e):
            print("[AutoScheduler] DB locked, using incremental deletion...")
            while temp_qs.exists():
                ids = temp_qs.values_list('id', flat=True)[:100]
                with transaction.atomic():
                    ExamTempTimetable.objects.filter(id__in=list(ids)).delete()
                time.sleep(0.1)
            while merged_qs.exists():
                ids = merged_qs.values_list('id', flat=True)[:100]
                with transaction.atomic():
                    MergedCourseGroup.objects.filter(id__in=list(ids)).delete()
                time.sleep(0.1)
            while shared_qs.exists():
                ids = shared_qs.values_list('id', flat=True)[:100]
                with transaction.atomic():
                    SharedVenueExamGroup.objects.filter(id__in=list(ids)).delete()
                time.sleep(0.1)
            print("[AutoScheduler] Incremental deletion complete")
        else:
            raise


def run_optimized_autoscheduler_thread(disabled_constraints: Optional[Set[str]] = None, tt_scope: Optional[dict] = None):
    global _ACTIVE_TT_SCOPE
    _ACTIVE_TT_SCOPE = tt_scope
    disabled_constraints = disabled_constraints or set()
    total_courses = 0
    init_logger()
    _PhaseTimer.reset()
    _trace_reset()
    print(f"[AutoScheduler v64] Log file: {get_log_file_path()}")
    try:
        enable_wal_mode()
        clear_exam_tables_safely(tt_scope)
        with transaction.atomic():
            _already_scheduled_cache.clear()
            _bulk_buffer.clear()
        # Pinned to pk=1 — every other call site (the exam config panel,
        # update_exam_config, exam_timetable_panel, etc.) reads/writes
        # ExamSchedulerConfig via id=1. Using a bare .first() here (no
        # explicit ordering) risks silently picking up a DIFFERENT row —
        # and therefore a different start_date/max_exam_days window than
        # the one shown and edited on /exam-autoscheduler/ — if a stray
        # second row ever exists. Pinning to pk=1 guarantees the algorithm
        # always runs against exactly the config the person configured.
        config = ExamSchedulerConfig.objects.filter(pk=1).first()
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
            # Real stem MEMBERSHIP (M2M) — prefetch_related, not
            # select_related, since specialization_stems is a
            # many-to-many. Needed by _exam_get_specialization_stem_ids /
            # _exam_get_specialization_category_ids so the M2M-aware
            # collision-exemption fix doesn't add an N+1 query per pair.
            .prefetch_related("specialization_stems", "specialization_stems__category")
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
        _raw_venues = list(
            Venue.objects.select_related('building')
            .filter(exam_capacity__isnull=False, exam_capacity__gt=0)
        )
        psi = PreSchedulingIntelligence(all_courses, analysis, config, _raw_venues)
        strategy = psi.run()
        state = SchedulerState(config, analysis, strategy, disabled_constraints)
        state._cross_cohort_norm_codes = set(analysis.shared_unit_groups.keys())
        if not state.date_range or not state.venues or not state.slots:
            close_logger()
            return {"status": "error", "message": "Invalid config."}
        # CRITICAL FIX: make this run aware of exams already committed by
        # OTHER departments/AllocationSets before any placement starts —
        # see preload_cross_scope_busy_state()'s docstring. Without this,
        # a combination-stem course scheduled by another department's
        # already-run scope was invisible to every check below, and could
        # be double-booked into the exact same date/slot.
        preload_cross_scope_busy_state(state, all_courses)
        scheduled_ids: Set[int] = set()
        print(f"\n[AutoScheduler v64] {total_courses} courses, {len(state.venues)} venues")
        print(f"[AutoScheduler v64] CORRECT STEM-AWARE COLLISION LOGIC enabled")
        print(f"[AutoScheduler v64] Same course codes always grouped as families")
        print(f"[AutoScheduler v64] Different codes from different stems can share slots")
        print(f"[AutoScheduler v64] FAMILY ALL-OR-NOTHING PLACEMENT enforced (no partial families, ever)")
        print(f"[AutoScheduler v64] DYNAMIC DAILY LIMITS: Soft=2, Hard=3 (avoids 4)")
        print(f"[AutoScheduler v64] STRICT CAPACITY: No overflow allowed")
        print(f"[AutoScheduler v64] BEST-FIT: Smallest venue that fits")
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
        # MOVED (was after Phase0/PhaseC/Phase3): families get one more
        # exhaustive retry across EVERY remaining date/slot combination
        # here, before any individual-course phase runs and starts eating
        # into the venue/slot capacity those families still need. Running
        # this after Phase0-Designated/PhaseC-Saturation/Phase3-CrossDayFill
        # (as it used to) meant a family that failed its first pass was
        # only retried once venues and slots individual courses wanted had
        # already been claimed out from under it.
        with _PhaseTimer("Phase3b-FamilyRescue"):
            p3b_placed = family_split_rescue_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        # `family_exhausted` is now diagnostic-only: it records which
        # families didn't fully seat in the joint pass, for logging/audit.
        # It no longer grants any later pass permission to place a subset of
        # a family individually — every family_exhausted-gated `continue`
        # below was removed so those families simply stay unscheduled as a
        # whole until a human adds capacity, instead of scattering.
        for _nc, _cids in state.analysis.shared_unit_groups.items():
            if any(_cid not in scheduled_ids for _cid in _cids):
                state.family_exhausted.add(_nc)
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
        with _PhaseTimer("Phase4-ForcedFallback"):
            p4_placed = forced_fallback_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        with _PhaseTimer("Phase5-DBFallback"):
            p5_placed = db_driven_fallback_pass(all_courses, state, scheduled_ids)
        sync_scheduled_ids_from_db(scheduled_ids)
        p7_placed = 0
        if len(all_courses) - len(scheduled_ids) > 0:
            state.compact_level = 0    # v74: compactness is a preference; relax for leftovers
            print("[Compact] Consecutive-day gate OFF from Phase7 onward (leftovers must be placed)")
            with _PhaseTimer("Phase7-Nuclear"):
                p7_placed = nuclear_fallback_pass(all_courses, state, scheduled_ids)
            sync_scheduled_ids_from_db(scheduled_ids)
        p8_placed = 0
        if len(all_courses) - len(scheduled_ids) > 0:
            with _PhaseTimer("Phase8-Ultimate"):
                p8_placed = ultimate_fallback_pass(all_courses, state, scheduled_ids)
            sync_scheduled_ids_from_db(scheduled_ids)
        p8b_placed = 0
        if len(all_courses) - len(scheduled_ids) > 0:
            with _PhaseTimer("Phase8b-SharedRoomLastResort"):
                p8b_placed = shared_room_last_resort_pass(all_courses, state, scheduled_ids)
            sync_scheduled_ids_from_db(scheduled_ids)
        with _PhaseTimer("Phase9a-VenueConsolidation"):
            p9a_moves = consolidate_underfilled_small_venues_pass(all_courses, state, scheduled_ids)
        with _PhaseTimer("Phase9-SwapOptimization"):
            p9_swaps = post_placement_swap_optimization(all_courses, state, scheduled_ids)
        p9b_placed = 0
        if len(all_courses) - len(scheduled_ids) > 0:
            with _PhaseTimer("Phase9b-PostConsolidationRescue"):
                state.allow_cross_course_sharing = True      # last-resort phase
                try:
                    p9b_placed = post_consolidation_rescue_pass(all_courses, state, scheduled_ids)
                finally:
                    state.allow_cross_course_sharing = False
        p9d_placed = 0
        if len(all_courses) - len(scheduled_ids) > 0:
            with _PhaseTimer("Phase9d-DailyLimitRelaxation"):
                state.allow_cross_course_sharing = True      # last-resort phase
                try:
                    p9d_placed = daily_limit_relaxation_pass(all_courses, state, scheduled_ids)
                finally:
                    state.allow_cross_course_sharing = False
            sync_scheduled_ids_from_db(scheduled_ids)
        with _PhaseTimer("Phase9a2-EvacuateSplitVenues"):
            p9a2_evacuated = evacuate_foreign_courses_from_split_venues_pass(all_courses, state, scheduled_ids)
        with _PhaseTimer("Phase9c-MergeBackSplits"):
            p9c_merged = merge_back_split_placements_pass(all_courses, state, scheduled_ids)
        _report_cohort_compactness(state)
        with _PhaseTimer("Audit-DuplicateFixes"):
            duplicate_fixes = _audit_and_fix_duplicate_placements(state, all_courses)
        with _PhaseTimer("Build-SharedVenueGroups"):
            _build_shared_venue_exam_groups()
        _PhaseTimer.summary()
        actual_scheduled = _temp_qs().values("course_allocation_id").distinct().count()
        remaining_count = total_courses - actual_scheduled
        if remaining_count > 0:
            diagnose_unscheduled_courses(all_courses, state, scheduled_ids, sample_size=60)
        try:
            trace_file = write_course_trace_reports(all_courses, state)
        except Exception as _trace_exc:
            print(f"[Trace] report writer failed (scheduling result is unaffected): {_trace_exc}")
            trace_file = None
        lecturer_violations = _audit_lecturer_conflicts(state, all_courses)
        student_violations = _audit_student_conflicts(state, all_courses)
        venue_violations = _audit_venue_capacity_with_sharing(state, all_courses)
        if remaining_count == 0:
            message = f"SUCCESS! All {actual_scheduled} courses scheduled. Strategy={getattr(strategy, 'mode', 'NORMAL')}"
        else:
            message = f"Scheduled {actual_scheduled}/{total_courses}. {remaining_count} unscheduled."
        print(f"\n[AutoScheduler v64] {message}")
        print(f"[AutoScheduler v64] Lecturer violations: {lecturer_violations}")
        print(f"[AutoScheduler v64] Student violations: {student_violations}")
        print(f"[AutoScheduler v64] Venue capacity violations: {venue_violations}")
        print(f"[AutoScheduler v64] Post-placement swaps: {p9_swaps}")
        if state.daily_limit_relaxed:
            print(f"[AutoScheduler v64] WARNING {len(state.daily_limit_relaxed)} course(s) placed by RELAXING the "
                  f"daily limit (last resort) — review these:")
            for _r in state.daily_limit_relaxed:
                print(f"    - {_r['course']} on {_r['date']} {_r['slot']}: over by {_r['over_by']} ({_r['why']})")
        print(f"[AutoScheduler v64] Splits merged back into a single venue: {p9c_merged}")
        print(f"[AutoScheduler v64] Cross-building family splits: {len(state.cross_building_splits)}")
        if state.cross_building_splits:
            print("[AutoScheduler v64] These need manual review — no single building had "
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
            "trace_file": trace_file,
            "daily_limit_relaxed": [
                f"{r['course']} on {r['date']} {r['slot']}: over the daily limit by {r['over_by']} ({r['why']})"
                for r in state.daily_limit_relaxed],
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
                "shared_room_last_resort": p8b_placed,
                "swap_optimization": p9_swaps,
                "post_consolidation_rescue": p9b_placed,
                "daily_limit_relaxation": p9d_placed,
                "merge_back_splits": p9c_merged,
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
        print(f"[AutoScheduler v64] Fatal: {exc}\n{traceback.format_exc()}")
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
    # v72: middle-first for individuals — see individual_priority_slots.
    all_slots = state.individual_priority_slots
    placed = 0
    candidates = [
        c for c in all_courses
        if c.id not in scheduled_ids
        and normalize_course_code(getattr(c, "course_code", "") or "")
        in state.designated_venues_by_norm_code
        and normalize_course_code(getattr(c, "course_code", "") or "")
        not in state._cross_cohort_norm_codes
        # Family-group members are excluded here unconditionally, even once
        # family_exhausted is set — they may only ever be written to the DB
        # together via place_merged_family / _commit_distributed_minimal, in
        # Phase1/Phase3b/Phase4-sweep3, never one at a time in this pass.
        and normalize_course_code(getattr(c, "course_code", "") or "")
        not in state.analysis.shared_unit_groups
    ]
    # v76/v77: light programme-years first; each programme-year's designated courses
    # together (fewest designated courses first) so the 1,1,1,1 block is built in one go.
    _spec_units: Dict[str, int] = defaultdict(int)
    for _c in candidates:
        _spec_units[state._py_key(_c)] += 1
    candidates.sort(key=lambda c: (
        state.cohort_rank(c),
        _spec_units.get(state._py_key(c), 0),
        state._py_key(c),
        -state.priority_score(c),
    ))
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
        designated_id_set = set(venue_ids)
        is_strict = nc in state.strict_norm_codes
        needed = course_student_count(course)
        lid = state._cached_lecturer_id(course)
        placed_this = False
        sorted_dates = state.get_sorted_dates(course)
        _orig_level = state.compact_level
        for _gate_lvl in (2, 1, 0):          # v76: compact first, old behaviour last
            if placed_this:
                break
            state.compact_level = min(_orig_level, _gate_lvl)
            for date_obj, _ in sorted_dates:
                if placed_this:
                    break
                for ss, se in all_slots:
                    if lid and not state.lecturer_available(lid, date_obj, ss, course):
                        _jr(course, date_obj, ss, "lecturer-conflict", "designated_venue_priority_pass")
                        continue
                    if not state.students_available(course, date_obj, ss):
                        _jr(course, date_obj, ss, "student-conflict", "designated_venue_priority_pass")
                        continue
                    if state.check_norm_code_day_conflict(course, date_obj):
                        _jr(course, date_obj, ss, "norm-code-day-conflict", "designated_venue_priority_pass")
                        continue
                    if state.check_shared_unit_conflict(course, date_obj, ss):
                        _jr(course, date_obj, ss, "shared-unit-conflict", "designated_venue_priority_pass")
                        continue
                    if state.check_family_conflict(course, date_obj, ss):
                        _jr(course, date_obj, ss, "family-conflict", "designated_venue_priority_pass")
                        continue
                    if _gate_lvl > 0 and (state.is_daily_limit_hard_exceeded(course, date_obj)
                                          or state.cohort_in_cooling(course, date_obj, ss)
                                          or not state.compact_gap_ok(course, date_obj)):
                        _jr(course, date_obj, ss, "compact-or-daily-limit", "designated_venue_priority_pass",
                            f"gate level {_gate_lvl}")
                        continue

                    # ----------------------------------------------------------
                    # PASS 1 — single, stand-alone venue. Same bug as
                    # place_merged_family had: a course with a designated-venue
                    # rule (e.g. "COSC courses use computer labs") should still
                    # get ONE room to itself whenever any single room anywhere
                    # is big enough — designated first, then (unless the course
                    # is strictly locked to its own pool) the general venue
                    # list. Splitting is a last resort, not a shortcut taken
                    # just because the designated pool happens to add up once
                    # distributed across several small rooms. This applies
                    # whether or not the course is part of a merged family —
                    # a single-program course like one done by only one
                    # program is exactly as entitled to a stand-alone room.
                    # ----------------------------------------------------------
                    room = None
                    for v in venues:
                        if state.venue_remaining(v.id, date_obj, ss) >= needed \
                                and _can_share_venue(course, v.id, date_obj, ss, state):
                            room = v
                            break

                    if room is None and not is_strict:
                        general_free = [
                            (v, rem) for v, rem in state.get_free_venues(date_obj, ss)
                            if v.id not in designated_id_set and rem >= needed
                            and _can_share_venue(course, v.id, date_obj, ss, state)
                        ]
                        if general_free:
                            general_free.sort(key=lambda x: x[1])
                            room = general_free[0][0]

                    # ----------------------------------------------------------
                    # PASS 2 — no single venue anywhere fits. Only now do we
                    # split, still preferring the designated pool over the
                    # general pool, and only spilling into the general pool
                    # if the course isn't strictly locked to its own venues.
                    # ----------------------------------------------------------
                    if room is None:
                        # A split must stay inside ONE building whenever a
                        # single building's rooms can cover it — see
                        # find_minimal_split_venues for why cross-building
                        # splits are operationally broken, not just untidy.
                        # Cross a building boundary only if no single building
                        # anywhere (designated, then designated+general) has
                        # enough combined capacity.
                        known_building = state.preferred_family_building(nc, date_obj, ss)
                        venues_desc = sorted(
                            venues, key=lambda v: -state.venue_remaining(v.id, date_obj, ss)
                        )
                        total_rem = sum(state.venue_remaining(v.id, date_obj, ss) for v in venues_desc)
                        if total_rem >= needed:
                            designated_rows = [
                                [v, state.venue_remaining(v.id, date_obj, ss), state.venue_examcap.get(v.id, 0)]
                                for v in venues_desc
                            ]
                            same_building_designated = _building_subset_for_split(designated_rows, needed)
                            split_pool = [row[0] for row in (same_building_designated or designated_rows)]
                            if place_multi_venue(course, split_pool, date_obj, ss, se, state, scheduled_ids):
                                placed += 1
                                placed_this = True
                                print(f"  [Phase0-Split] {course.course_code} → {date_obj} {ss} "
                                      f"across {[v.code for v in split_pool]}")
                                break
                        if not is_strict:
                            general_pool = [
                                v for v, rem in state.get_free_venues(date_obj, ss)
                                if v.id not in designated_id_set
                            ]
                            combined_desc = sorted(
                                venues_desc + general_pool,
                                key=lambda v: -state.venue_remaining(v.id, date_obj, ss)
                            )
                            combined_rem = sum(state.venue_remaining(v.id, date_obj, ss) for v in combined_desc)
                            if combined_rem >= needed:
                                combined_rows = [
                                    [v, state.venue_remaining(v.id, date_obj, ss), state.venue_examcap.get(v.id, 0)]
                                    for v in combined_desc
                                ]
                                same_building_combined = _building_subset_for_split(
                                    combined_rows, needed, preferred_building=known_building)
                                combined_split_pool = [row[0] for row in (same_building_combined or combined_rows)]
                                if place_multi_venue(course, combined_split_pool, date_obj, ss, se, state, scheduled_ids):
                                    placed += 1
                                    placed_this = True
                                    print(f"  [Phase0-Split] {course.course_code} → {date_obj} {ss} "
                                          f"across {[v.code for v in combined_split_pool]} (designated+general)")
                                    break
                        _jr(course, date_obj, ss, "designated-venue-and-split-failed", "designated_venue_priority_pass",
                            f"needs {needed}; no single room fits"
                            f"{'' if not is_strict else ' (STRICT: designated rooms only)'}; split not possible")
                        continue
                    cap = state.venue_examcap.get(room.id, 0)
                    effective = min(needed, cap) if cap else needed
                    try:
                        with transaction.atomic():
                            ExamTempTimetable.objects.create(
                                course_allocation=course, venue=room,
                                date=date_obj, day=date_obj.strftime("%A"),
                                start_time=ss, end_time=se,
                                allocated_students=effective,
                            )
                    except Exception as _e0:
                        _jr(course, date_obj, ss, "db-integrity-error", "designated_venue_priority_pass", str(_e0)[:120])
                        continue
                    if not state.consume_venue(room.id, date_obj, ss, effective, course):
                        ExamTempTimetable.objects.filter(
                            course_allocation=course, date=date_obj, start_time=ss
                        ).delete()
                        _jr(course, date_obj, ss, "venue-seat-accounting-failed", "designated_venue_priority_pass", room.code)
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
                    _note_pick(course, f"designated-venue rule: {room.code} "
                               f"({'designated pool' if room.id in designated_id_set else 'general pool, non-strict code'}); "
                               f"first single room with {needed} free seats")
                    _trace_placement(course, room, date_obj, ss)
                    print(f"  [Phase0] {course.course_code} → {date_obj} {ss} {room.code}")
                    break
        state.compact_level = _orig_level
        if not placed_this and nc in state.strict_norm_codes:
            _jr(course, None, None, "STRICT designated code could not be seated — locked out of every later phase")
            state.strict_locked_ids.add(course.id)
            print(f"  [WARN] '{nc}' STRICTLY designated — left unscheduled")
    print(f"[Phase0-Designated] Placed {placed}")
    return placed