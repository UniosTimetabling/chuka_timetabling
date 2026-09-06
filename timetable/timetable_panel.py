from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.http import JsonResponse, StreamingHttpResponse
from datetime import datetime, timedelta
from core.group_required import group_required
from django.db.models import Q, Prefetch
from django.db import transaction
from course_allocation.models import CourseAllocation, CombinedCourseGroup
from timetable.models import (
    Timetable, TempTimetable, SchedulerConfig, AutoMergedExamGroup,
    LecturerBlockedSlot, LecturerTimePreference,
)
from room_management.models import Venue, VenueBlock, VenueSpecialization
from program_management.models import Program, ProgramCourse
from course_allocation.config_helpers import strip_course_code_tag
from lecturer_portal.models import Lecturer 
from department_management.models import Department
import json
import re
import time
import threading
import uuid
from collections import defaultdict


# -----------------------
# Loose base-course-code key for double-booking exemption comparisons
# -----------------------
_COURSE_PREFIX_RE = re.compile(r'^\s*([A-Za-z]+)\s*(\d+)')


def course_base_key(course_code):
    """
    Return a loose 'base course' key — the leading LETTER-run + DIGIT-run —
    with every trailing section/stream/group marker discarded, however it's
    formatted. Used ONLY to decide whether two rows are "the same course"
    for the room/lecturer double-booking exemptions below.

    strip_course_code_tag() only strips a single well-formed trailing
    "(TAG)"; real data is much messier — "ECON 441", "ECON 441 GROUP B",
    "ECON441-b", "ECON 441(ACT)-A", "SOCI 321-C" all need to collapse to
    the same key ("ECON441" / "SOCI321") so the same course isn't
    misreported as a room/lecturer clash with itself. Mirrors
    course_management.cod_panel._loose_course_prefix_key (kept as a local,
    lightweight duplicate rather than importing that heavier module — with
    its weasyprint dependency — into every timetable panel request).
    """
    if not course_code:
        return None
    m = _COURSE_PREFIX_RE.match(course_code)
    if not m:
        return None
    return f'{m.group(1).upper()}{m.group(2)}'


# -----------------------
# Smart Collision-Exemption Helpers
# Mirror the logic in the autoscheduler so panel detection is consistent.
# -----------------------

def _is_elective_alloc(alloc) -> bool:
    return bool(getattr(alloc, 'is_elective', False))


def _intake_of(alloc) -> str:
    return getattr(alloc, 'intake', 'normal') or 'normal'


def _semester_of(alloc):
    """
    Return the ProgramCourse semester (1 or 2) for an allocation, or None
    if it can't be determined. `program_course` is a required FK on
    CourseAllocation, so this is normally always resolvable.
    """
    pc = getattr(alloc, 'program_course', None)
    return getattr(pc, 'semester', None) if pc else None


def _selection_group_id_of(alloc):
    try:
        sg = getattr(alloc, 'selection_group', None)
        return sg.id if sg else None
    except Exception:
        return None


def _specialization_stem_id_of(alloc):
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.id if st else None
    except Exception:
        return None


def _specialization_category_id_of(alloc):
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.category_id if st else None
    except Exception:
        return None


# -----------------------
# Full SpecializationStem MEMBERSHIP (M2M) — NOT the singular pointer above.
#
# course_management.specialization_stem_views deliberately clears a course's
# singular `specialization_stem` pointer to None the moment that course
# belongs to MORE than one stem (comment there: "let it clash normally").
# That's correct for the *pointer's* purpose, but reading only the pointer
# (as `_specialization_stem_id_of` above does) makes a legitimately
# multi-stem course look like "no stem at all" — so it clashed against
# every stem's courses, including stems it has nothing to do with.
#
# `alloc.specialization_stems` (reverse of SpecializationStem.courses, the
# true many-to-many) always reflects EVERY stem a course belongs to,
# pointer or no pointer. Use these wherever the decision must be correct;
# the singular helpers above are kept only as lightweight compatibility
# shims for callers that just want a rough single-value estimate.
# -----------------------
def _specialization_stem_ids_of(alloc):
    try:
        return frozenset(alloc.specialization_stems.values_list('id', flat=True))
    except Exception:
        return frozenset()


def _specialization_category_ids_of(alloc):
    try:
        return frozenset(
            sid for sid in alloc.specialization_stems.values_list('category_id', flat=True)
            if sid is not None
        )
    except Exception:
        return frozenset()


def _alloc_in_any_stem(alloc) -> bool:
    """True if this allocation belongs to at least one SpecializationStem,
    reading the real M2M membership rather than the often-nulled singular
    pointer. Used by the panel to flag stem-related collisions distinctly
    (yellow) from ordinary hard collisions (red)."""
    return bool(_specialization_stem_ids_of(alloc))


def _student_group_id_of(alloc):
    return getattr(alloc, 'student_group_id', None)


def _student_group_name_of(alloc):
    """Human-readable Student Group name for an allocation, e.g. 'Group A',
    or None if the course isn't tied to a specific group (shared/common
    course). Used so conflict messages can name the actual group involved
    instead of just the bare program+year."""
    try:
        sg = getattr(alloc, 'student_group', None)
        return sg.name if sg else None
    except Exception:
        return None


def _specialization_name_of(alloc):
    """Human-readable specialization stem name for an allocation (e.g.
    'Artificial Intelligence'), reading the real M2M membership so a
    multi-stem course still reports a name instead of coming back empty.
    Returns None if the course belongs to no stem."""
    try:
        stems = list(alloc.specialization_stems.all()[:3])
        if not stems:
            return None
        names = [s.name for s in stems]
        return ', '.join(names)
    except Exception:
        return None


def day_to_python_weekday(day_name):
    """Map a 'Monday'..'Sunday' string to Python's weekday() index (Mon=0)."""
    try:
        return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                "Saturday", "Sunday"].index(day_name)
    except ValueError:
        return None


def _parse_hms(value):
    """Best-effort parse of a TimeField/str/time value into a datetime.time
    for comparison. Returns None if it can't be parsed."""
    if value is None:
        return None
    if hasattr(value, 'hour'):
        return value
    for fmt in ('%H:%M:%S', '%H:%M'):
        try:
            return datetime.strptime(str(value), fmt).time()
        except Exception:
            continue
    return None


def _build_lecturer_constraint_maps():
    """
    Batch-load every active LecturerBlockedSlot and LecturerTimePreference
    (with its slots) ONCE, keyed by lecturer_id.

    Without this, lecturer_blocked_slot_hit()/lecturer_preference_mismatch()
    each run their own query — and the preference/blocked-day check in
    timetable_conflicts_api calls both for EVERY scheduled timetable row.
    On a timetable with a few thousand entries that's several thousand
    extra round trips, and it sits on the initial page-load's critical
    path (stage='complete' calls timetable_conflicts_api directly, before
    the panel is shown) — a major, easily-avoidable slowdown on open.
    Call this once per request and pass the two maps through instead.
    """
    blocked_map = {}
    for row in LecturerBlockedSlot.objects.filter(is_active=True):
        blocked_map.setdefault(row.lecturer_id, []).append(row)

    pref_slots_map = {}
    prefs = LecturerTimePreference.objects.filter(is_active=True).prefetch_related('slots')
    for pref in prefs:
        pref_slots_map.setdefault(pref.lecturer_id, []).extend(pref.slots.all())

    return blocked_map, pref_slots_map


def lecturer_blocked_slot_hit(lecturer, day, start_t, end_t, blocked_map=None):
    """
    Return the active LecturerBlockedSlot row that HARD-blocks placing this
    lecturer on `day` between `start_t`/`end_t`, or None if there's no
    clash. Covers both "whole day blocked" rows (start/end left blank) and
    specific day+time-range blocked rows (only the overlapping portion of
    the day is off-limits).

    Pass `blocked_map` (from _build_lecturer_constraint_maps()) when
    checking many entries in a loop, to skip the per-call DB query.
    """
    if not lecturer or not day:
        return None
    s = _parse_hms(start_t)
    e = _parse_hms(end_t)
    if blocked_map is not None:
        rows = [r for r in blocked_map.get(lecturer.id, []) if (r.day or '').lower() == day.lower()]
    else:
        try:
            rows = LecturerBlockedSlot.objects.filter(
                lecturer=lecturer, day__iexact=day, is_active=True
            )
        except Exception:
            return None
    for row in rows:
        if not row.start_time and not row.end_time:
            return row  # whole day blocked
        if s is None or e is None:
            continue
        rs, re_ = row.start_time, row.end_time
        if rs is not None and re_ is not None and time_overlaps(s, e, rs, re_):
            return row
    return None


def lecturer_preference_mismatch(lecturer, day, start_t, end_t, pref_slots_map=None):
    """
    Return a short human-readable string describing WHY this placement
    doesn't match the lecturer's declared time preference, or None if
    either the lecturer has no active preference at all (nothing to
    compare against) or the placement DOES match one of their preferred
    day/time slots. This is a SOFT signal only — it should never block a
    save, only be surfaced as a warning/yellow flag so staff can see a
    lecturer's stated wishes weren't honoured.

    Pass `pref_slots_map` (from _build_lecturer_constraint_maps()) when
    checking many entries in a loop, to skip the per-call DB query.
    """
    if not lecturer or not day:
        return None
    s = _parse_hms(start_t)
    e = _parse_hms(end_t)
    if pref_slots_map is not None:
        all_slots = pref_slots_map.get(lecturer.id, [])
    else:
        try:
            prefs = LecturerTimePreference.objects.filter(lecturer=lecturer, is_active=True)
        except Exception:
            return None
        all_slots = []
        for pref in prefs:
            all_slots.extend(list(pref.slots.all()))
    if not all_slots:
        return None  # lecturer never stated a preference — nothing to compare

    for slot in all_slots:
        if slot.day.lower() != day.lower():
            continue
        if slot.is_whole_day or (not slot.start_time and not slot.end_time):
            return None  # matches a preferred whole day
        if s is not None and e is not None and slot.start_time and slot.end_time:
            if time_overlaps(s, e, slot.start_time, slot.end_time):
                return None  # matches a preferred day+time slot

    preferred_days = sorted({sl.day for sl in all_slots})
    return f"prefers {', '.join(preferred_days)} but is scheduled on {day}"


def _is_same_base_course_pair(alloc_a, alloc_b) -> bool:
    """
    True when alloc_a and alloc_b share the same base course code — e.g.
    'COSC 103-D' / 'COSC 103-F' / 'COSC 103-WA' / 'COSC 103-X', or two rows
    both simply coded 'COMS 101'. This is one course that has been SPLIT
    into multiple sections/streams (typically because the cohort is too
    large for one venue/slot), not two different courses. Every section
    teaches a disjoint subset of the same program-year's students, so the
    sections running concurrently — in the same venue or different ones —
    is not a program-year collision, regardless of whether a StudentGroup
    was formally assigned to each section.

    Uses course_base_key(), which discards ANY trailing section/stream tag
    (dash, underscore, or bare word/letter) and keeps only the leading
    letter-run + digit-run, so it recognises a split regardless of how the
    section was labelled in the data.
    """
    key_a = course_base_key(getattr(alloc_a, 'course_code', None))
    key_b = course_base_key(getattr(alloc_b, 'course_code', None))
    return key_a is not None and key_a == key_b


def is_scheduling_exempt(alloc_a, alloc_b) -> bool:
    """
    Return True when scheduling alloc_a and alloc_b at the same timeslot does NOT
    constitute a program-year collision.

    Kept IN SYNC with `is_program_year_collision_exempt` in
    timetable/algorithms/regular_timetable_autosheduler_algorithm.py — the panel
    must recognise every combination the autoscheduler is allowed to place
    concurrently, or it will misreport (and, previously, auto-delete) rows the
    autoscheduler placed on purpose. If that function's rules change, mirror the
    change here too.

    Rules (checked in this order):
    0. Same base course code (e.g. 'COSC 103-D' vs 'COSC 103-F', or two rows
       both coded 'COMS 101') → the SAME course split into multiple
       sections/streams because the cohort is too large for one venue/slot.
       Always exempt, regardless of venue or StudentGroup — this is checked
       first and short-circuits every other rule, because it's the same
       course, not two different courses that happen to interact.
    0.5. Different ProgramCourse semester (1 vs 2) → exempt ONLY when also
       paired with a different intake (special vs normal) — a special
       intake is a shifted-semester cohort of the same year, so it
       genuinely overlaps a different semester's normal courses in real
       time. Same intake + different semester is NOT exempt.
    1. SpecializationStem takes priority over everything else, checked
       against the FULL M2M membership (a course can legitimately belong to
       more than one stem, so "same stem" and "different stem" below mean
       "shares at least one stem" / "shares no stem but shares a category"):
       - shares a stem          → NEVER exempt (every pair within a stem must
         still clash-check, even if one course is also flagged elective).
       - no shared stem, same category → exempt (student only ever picks one
         stem in a category, so they can never end up in both).
       - otherwise falls through to the checks below.
    2. Different StudentGroup (both set, and not equal) → different cohorts of
       the same program/year → exempt. A shared course (student_group=None)
       still clashes with every group's courses, so this only fires when BOTH
       sides have an explicit, different group.
    3. Both belong to the SAME selection group (both set, and equal) →
       students choose exactly one course from that group, so they can
       run concurrently. Different selection groups, or merely being
       flagged elective with no group, is NOT exempt.
    4. Different intake (normal vs special) → different student cohorts.
    """
    # Rule 0: Same base course code — a split section of ONE course, not a
    # collision between two different courses. Checked first and wins
    # outright (e.g. a specialization-stem course that also got split into
    # sections must still be exempt against its own other sections).
    if _is_same_base_course_pair(alloc_a, alloc_b):
        return True

    # Rule 0.5: Different semester — but ONLY exempt when it's paired with a
    # different intake (one normal, one special). A "special" intake is a
    # shifted-semester cohort of the SAME programme year (see
    # CourseAllocation.intake help text), so a special-intake Semester 2
    # course genuinely runs at the same real time as a normal-intake
    # Semester 1 course — that pairing is not a collision. Two courses on
    # the SAME intake but different semesters (e.g. both normal, Sem 1 vs
    # Sem 2) are NOT exempt here — that combination is still treated as a
    # collision.
    sem_a = _semester_of(alloc_a)
    sem_b = _semester_of(alloc_b)
    if (
        sem_a is not None and sem_b is not None and sem_a != sem_b
        and _intake_of(alloc_a) != _intake_of(alloc_b)
    ):
        return True

    # Rule 1: SpecializationStem — checked against the FULL M2M membership
    # (_specialization_stem_ids_of), not the singular pointer, so a course
    # shared between two stems is still recognised as stem-bound instead of
    # collapsing to "no stem" and clashing against an unrelated stem's courses.
    stems_a = _specialization_stem_ids_of(alloc_a)
    stems_b = _specialization_stem_ids_of(alloc_b)
    if stems_a and stems_b:
        if stems_a & stems_b:
            return False  # share at least one stem -> never exempt
        cats_a = _specialization_category_ids_of(alloc_a)
        cats_b = _specialization_category_ids_of(alloc_b)
        if cats_a & cats_b:
            return True  # different stems, same category -> exempt

    # Rule 2: StudentGroup — different, explicitly-set groups only
    sgrp_a = _student_group_id_of(alloc_a)
    sgrp_b = _student_group_id_of(alloc_b)
    if sgrp_a is not None and sgrp_b is not None and sgrp_a != sgrp_b:
        return True

    # Rule 3: Selection group — only exempt when BOTH sides sit in the
    # SAME SelectionGroup (a student picks exactly one course from that
    # group, so two courses in it can never both be taken). Being merely
    # `is_elective=True`, or belonging to two DIFFERENT selection groups,
    # is NOT exempt — a student can pick one course from group A and one
    # from group B, so those must still clash-check normally.
    sg_a = _selection_group_id_of(alloc_a)
    sg_b = _selection_group_id_of(alloc_b)
    if sg_a is not None and sg_b is not None and sg_a == sg_b:
        return True

    # Rule 4: Different intake
    if _intake_of(alloc_a) != _intake_of(alloc_b):
        return True
    return False


# -----------------------
# CombinedCourseGroup helpers
# -----------------------
def _get_combined_group_ids_for_alloc(alloc_id):
    """
    Return the set of CombinedCourseGroup PKs that contain this allocation.
    Cached per-allocation to avoid repeated DB hits within one request.
    """
    return set(
        CombinedCourseGroup.objects.filter(
            allocations__id=alloc_id
        ).values_list('id', flat=True)
    )


def _are_in_same_combined_group(alloc_a_id, alloc_b_id):
    """
    Return True if alloc_a and alloc_b share at least one CombinedCourseGroup.
    When two allocations are combined they must be treated as ONE class —
    scheduling them together at the same time/venue is intentional and
    must NOT be flagged as a lecturer or venue collision.
    """
    groups_a = _get_combined_group_ids_for_alloc(alloc_a_id)
    if not groups_a:
        return False
    groups_b = _get_combined_group_ids_for_alloc(alloc_b_id)
    return bool(groups_a & groups_b)


def _other_belongs_to_a_different_combined_group(alloc_id, other_id):
    """
    Return True if `other` is a member of some CombinedCourseGroup that
    `alloc` is NOT also a member of.

    BUG THIS GUARDS AGAINST: the "same base course code" venue/lecturer
    double-booking exemption below (course_base_key(alloc) == course_base_key
    (other)) exists so two plain, uncombined sections of the same course can
    share a big lecture hall at the same slot. But course_base_key() strips
    every trailing section/group/stream marker — "BOTA 111-D", "BOTA 111-A",
    and "BOTA 111-CHEM" all collapse to the same "BOTA111" key — so without
    this check, that exemption also waved through placing a member of a
    brand-new CombinedCourseGroup into the exact same venue+slot already
    held by a member of a DIFFERENT, unrelated CombinedCourseGroup for the
    same base course. Visually/functionally that's two separate combined
    groups merged into one class, which must never be allowed — only
    members of the SAME group (_are_in_same_combined_group) may share a
    slot; any other combined-group membership on either side is a hard
    collision, never exempted by a matching base course code.
    """
    other_groups = _get_combined_group_ids_for_alloc(other_id)
    if not other_groups:
        return False
    alloc_groups = _get_combined_group_ids_for_alloc(alloc_id)
    return bool(other_groups - alloc_groups)


def _build_combined_group_exclude_ids():
    """
    Return the set of CourseAllocation IDs that are non-primary members of a
    CombinedCourseGroup.

    FIX (was scheduled-state-dependent, now unconditional):
    ---------------------------------------------------------
    Previously this only excluded secondary allocations once the group's
    primary_allocation already had a Timetable row with venue + timeslot.
    That meant an UNSCHEDULED combined group (e.g. COSC 101 merged across
    Pure CS / Applied CS / BBIT) showed up as N separate rows — and N in the
    unscheduled count — instead of 1, because none of the members had been
    excluded yet.

    A CombinedCourseGroup is scheduled and unscheduled as ONE unit (the
    autoscheduler's build_global_merged_tasks() Pass 1 always books a single
    venue/timeslot for the whole group — see regular_timetable_autosheduler_algorithm.py).
    So the secondary members should NEVER appear on their own in either the
    scheduled or unscheduled views — only the primary_allocation should,
    representing the whole group at every stage (whether that group already
    has a slot or is still pending).
    """
    exclude_ids = set()
    for group in CombinedCourseGroup.objects.prefetch_related('allocations').only(
        'id', 'primary_allocation_id'
    ):
        primary_id = group.primary_allocation_id
        member_ids = set(group.allocations.values_list('id', flat=True))
        if primary_id:
            member_ids.discard(primary_id)
        # No primary set yet (edge case / mid-edit group) — exclude nothing,
        # rather than silently hiding every member from the panel.
        if primary_id:
            exclude_ids |= member_ids
    return exclude_ids


def _get_combined_group_for_allocation(allocation):
    """
    If `allocation` is a member of a CombinedCourseGroup, return
    (group, [all member CourseAllocation objects]). Otherwise (None, [allocation]).

    This is the single place manual scheduling (`_handle_ajax_timetable_save`)
    consults to decide whether it's placing one course or an atomic group —
    mirrors what the autoscheduler's build_global_merged_tasks() Pass 1 and
    Simulate Move's _get_move_bundle() already do for their own flows.
    """
    group = (
        CombinedCourseGroup.objects
        .filter(allocations__id=allocation.id)
        .prefetch_related('allocations__lecturer')
        .first()
    )
    if not group:
        return None, [allocation]
    members = list(group.allocations.all())
    return group, (members or [allocation])


def _get_all_combined_group_membership_map():
    """
    Return {allocation_id: {'group_ids': set(...), 'group_id': int,
    'group_code': str, 'member_course_codes': [...],
    'total_students': int}} for EVERY member of EVERY CombinedCourseGroup
    (not just primaries).

    Built in one query so the scheduled-timetable grid can tag every row in
    a slashed cell with its group in a single pass, instead of hitting the
    DB once per row (which is what repeatedly calling
    `_get_combined_group_ids_for_alloc()` inside a per-cell loop would do).

    FIX: 'total_students' is the group's REAL total — summed here across
    ALL of the group's allocations, once, regardless of which members
    actually have a Timetable row in any particular cell. Callers must use
    THIS value rather than summing whatever member rows happen to be
    present in one scheduled cell: if a group's members are split across
    different cells/venues (the split-scheduling bug), or a query only
    picks up a subset, summing locally silently undercounts (e.g. showing
    13 or 23 instead of the group's real 150).
    """
    membership = {}
    groups = CombinedCourseGroup.objects.prefetch_related(
        'allocations__program', 'allocations__program_course'
    ).only('id', 'group_code', 'base_course_code')
    for group in groups:
        members = list(group.allocations.all())
        codes = [a.course_code for a in members]
        identifiers = _combined_group_identifiers(group, members)
        total_students = sum(a.number_of_students or 0 for a in members)
        for a in members:
            info = membership.setdefault(a.id, {
                'group_ids': set(),
                'group_id': group.id,
                'group_code': group.group_code,
                'display_name': group.display_name(),
                'member_course_codes': codes,
                'identifiers': identifiers,
                'total_students': total_students,
            })
            info['group_ids'].add(group.id)
    return membership


def _combined_group_identifiers(group, members=None):
    """
    Return one identifier dict per member allocation of a CombinedCourseGroup:
      {course_code, program, program_id, year}

    This is the ONLY place individual member course codes are meant to
    surface — as identifiers attached to the group, never as standalone
    rows/labels. Used to flag which program-years a combined group spans,
    so a program-year collision (the group accidentally overlapping a
    program-year it doesn't include) can be detected and shown.
    """
    if members is None:
        members = list(group.allocations.select_related('program', 'program_course').all())
    return [
        {
            'course_code': a.course_code,
            'program': a.program.name if a.program else None,
            'program_id': a.program_id,
            'year': _get_year_value(a),
        }
        for a in members
    ]


def _get_combined_group_meta_map():
    """
    Return {primary_allocation_id: {...}} for every CombinedCourseGroup that
    has a primary_allocation set.

    Used to annotate the surviving primary row wherever allocations are
    rendered (scheduled or unscheduled) so the panel shows ONE name —
    group.display_name(), e.g. "MATH 221 Combined" — instead of the
    individual member course codes, with 'identifiers' carrying which
    course codes/program-years actually make up the group (for spotting
    program-year collisions), and 'member_programs' kept for back-compat.
    """
    meta = {}
    groups = (
        CombinedCourseGroup.objects
        .filter(primary_allocation__isnull=False)
        .prefetch_related('allocations__program', 'allocations__program_course')
        .select_related('primary_allocation')
    )
    for group in groups:
        members = list(group.allocations.all())
        meta[group.primary_allocation_id] = {
            'group_id': group.id,
            'group_code': group.group_code,
            'display_name': group.display_name(),
            'member_count': len(members),
            'total_students': sum(a.number_of_students or 0 for a in members),
            'member_course_codes': [a.course_code for a in members],
            'member_programs': [
                a.program.name if a.program else '' for a in members
            ],
            'identifiers': _combined_group_identifiers(group, members),
        }
    return meta


# -----------------------
# Helper utilities
# -----------------------
def time_overlaps(start_a, end_a, start_b, end_b):
    """
    Return True if time intervals [start_a, end_a) and [start_b, end_b) overlap.
    Accepts datetime.time objects or "%H:%M" strings.
    """
    if isinstance(start_a, str):
        start_a = datetime.strptime(start_a, "%H:%M").time()
    if isinstance(end_a, str):
        end_a = datetime.strptime(end_a, "%H:%M").time()
    if isinstance(start_b, str):
        start_b = datetime.strptime(start_b, "%H:%M").time()
    if isinstance(end_b, str):
        end_b = datetime.strptime(end_b, "%H:%M").time()

    return (start_a < end_b) and (start_b < end_a)

def _get_year_value(allocation_or_obj):
    """
    Return the year of study (as string) from a CourseAllocation or similar object.
    """
    if allocation_or_obj is None:
        return None

    # 1️⃣ Direct fields
    for attr in ("year", "year_of_study", "level", "year_level", "academic_year"):
        val = getattr(allocation_or_obj, attr, None)
        if val:
            return str(val)

    # 2️⃣ Check related program for year
    program = getattr(allocation_or_obj, "program", None)
    if program:
        for attr in ("year", "year_of_study", "level", "year_level", "academic_year"):
            val = getattr(program, attr, None)
            if val:
                return str(val)

    # 3️⃣ Direct program_course FK -- exact regardless of course_code tagging
    pc_direct = getattr(allocation_or_obj, "program_course", None)
    if pc_direct and getattr(pc_direct, "year", None):
        return str(pc_direct.year)

    # 4️⃣ Check ProgramCourse table (strip any "(TAG)" shared-course suffix first)
    program = getattr(allocation_or_obj, "program", None)
    course_code = getattr(allocation_or_obj, "course_code", None)
    if program and course_code:
        pc = ProgramCourse.objects.filter(
            program=program,
            course_code__iexact=strip_course_code_tag(course_code)
        ).first()
        if pc and pc.year:
            return str(pc.year)

    return None

# -----------------------
# Progress Tracking
# -----------------------
progress_data = {}
progress_lock = threading.Lock()

class ProgressTracker:
    """Track progress for long-running operations"""
    
    def __init__(self, task_id):
        self.task_id = task_id
        self.progress = 0
        self.status = "initializing"
        self.message = ""
        self.data = {}
        
    def update(self, progress, status, message="", data=None):
        with progress_lock:
            self.progress = progress
            self.status = status
            self.message = message
            if data:
                self.data = data
            progress_data[self.task_id] = {
                'progress': self.progress,
                'status': self.status,
                'message': self.message,
                'data': self.data
            }
    
    def get_progress(self):
        with progress_lock:
            return {
                'progress': self.progress,
                'status': self.status,
                'message': self.message,
                'data': self.data
            }

def process_timetable_data(tracker):
    """Simulate processing timetable data with progress updates"""
    try:
        # Simulate data loading
        tracker.update(10, "loading", "Loading course allocations...")
        time.sleep(1)
        
        # Simulate conflict checking
        tracker.update(30, "processing", "Checking for conflicts...")
        time.sleep(2)
        
        # Simulate venue allocation
        tracker.update(50, "processing", "Allocating venues...")
        time.sleep(1)
        
        # Simulate schedule optimization
        tracker.update(70, "optimizing", "Optimizing schedule...")
        time.sleep(2)
        
        # Simulate finalizing
        tracker.update(90, "finalizing", "Finalizing timetable...")
        time.sleep(1)
        
        tracker.update(100, "completed", "Timetable loaded successfully!", {
            'total_entries': 150,
            'conflicts_found': 5,
            'loading_time': '3.2s'
        })
        
    except Exception as e:
        tracker.update(0, "error", f"Error: {str(e)}")

# -----------------------
# Progress Tracking API Endpoints
# -----------------------
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def timetable_loading_progress(request):
    """API to get current loading progress"""
    task_id = request.GET.get('task_id', 'default')
    
    with progress_lock:
        progress = progress_data.get(task_id, {
            'progress': 0,
            'status': 'not_started',
            'message': 'Waiting to start...',
            'data': {}
        })
    
    return JsonResponse(progress)

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def start_timetable_loading(request):
    """Start the timetable loading process and return task ID"""
    task_id = str(uuid.uuid4())
    tracker = ProgressTracker(task_id)
    
    # Store tracker
    with progress_lock:
        progress_data[task_id] = tracker.get_progress()
    
    # Start processing in background thread
    thread = threading.Thread(
        target=process_timetable_data,
        args=(tracker,),
        daemon=True
    )
    thread.start()
    
    return JsonResponse({'task_id': task_id, 'status': 'started'})

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def stream_timetable_progress(request):
    """Stream progress updates using Server-Sent Events"""
    
    def event_stream():
        task_id = request.GET.get('task_id', 'default')
        last_progress = -1
        
        while True:
            with progress_lock:
                progress = progress_data.get(task_id, {
                    'progress': 0,
                    'status': 'not_started',
                    'message': 'Waiting...',
                    'data': {}
                })
            
            # Only send if progress changed
            if progress['progress'] != last_progress:
                yield f"data: {json.dumps(progress)}\n\n"
                last_progress = progress['progress']
                
                # Stop streaming if completed or errored
                if progress['status'] in ['completed', 'error']:
                    yield f"data: {json.dumps({'status': 'stream_end'})}\n\n"
                    break
            
            time.sleep(0.5)  # Poll every 0.5 seconds
    
    response = StreamingHttpResponse(
        event_stream(),
        content_type='text/event-stream'
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'  # Disable buffering for nginx
    return response

# -----------------------
# Conflict Detection API
#
# AUTO-RESOLUTION FLOW (correct order):
#
#   PASS 1 — scan all entries, find every collision group,
#             collect ids_to_delete (all but the lowest-id per group).
#             "Keep 1, delete the rest" means:
#               sort group by id → kept = group[0], deleted = group[1:]
#             A row that appears in multiple groups is only deleted once.
#
#   DELETE  — bulk-delete all collected ids RIGHT NOW, before anything
#             is returned. DB is now clean.
#
#   PASS 2 — re-query the DB (which now has no collisions) and build
#             the conflict response from the clean data. Since collisions
#             were deleted, total_conflicts will be 0 and the frontend
#             shows a clean timetable immediately.
#
# Exemptions — never flagged as a collision (kept in sync with
# is_program_year_collision_exempt in
# timetable/algorithms/regular_timetable_autosheduler_algorithm.py, and with
# _check_conflicts() below which guards the manual add/edit save flow):
#   • Same base course_code sharing a VENUE at the same time → never a
#     room double-booking, even across different program-years or
#     different lecturers (e.g. a cross-listed class).
#   • Same lecturer teaching the same base course_code in the same VENUE
#     at the same time → never a lecturer double-booking (one physical
#     class). Still flagged if the venue differs.
#   • Combined Course Group members (intentionally share lecturer/venue/slot)
#   • Different SpecializationStem within the same category
#   • Different, explicitly-set StudentGroup (separate cohorts)
#   • Elective / selection-group pairs
#   • Different intake cohorts
#
# NOTE: automatic deletion of colliding rows is currently DISABLED (see the
# commented-out DELETE block below) — this endpoint only detects/reports.
# -----------------------
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def timetable_conflicts_api(request):
    """
    Detect and auto-resolve timetable conflicts.
    Deletes the extra colliding rows first, then returns the clean state.
    """
    try:

        # ════════════════════════════════════════════════════════════
        # PASS 1 — find every collision group and collect ids to delete
        # ════════════════════════════════════════════════════════════
        def _load_schedule_map():
            """Load all timetable rows and group by (day, start, end)."""
            timetables = Timetable.objects.select_related(
                'course_allocation',
                'course_allocation__program',
                'course_allocation__program_course',
                'course_allocation__lecturer',
                'course_allocation__selection_group',
                'course_allocation__specialization_stem',
                'course_allocation__specialization_stem__category',
                'venue',
            ).prefetch_related(
                # Real stem MEMBERSHIP (M2M) — see _specialization_stem_ids_of.
                # The singular relations above are select_related'd separately
                # for the (still-used) compatibility shims.
                'course_allocation__specialization_stems',
                'course_allocation__specialization_stems__category',
            ).all()
            smap = {}
            for tt in timetables:
                try:
                    day = tt.day or 'Unknown'
                    s   = tt.start_time.strftime('%H:%M') if tt.start_time else '00:00'
                    e   = tt.end_time.strftime('%H:%M')   if tt.end_time   else '00:00'
                    key = f'{day}_{s}_{e}'
                    smap.setdefault(key, []).append(tt)
                except Exception as ex:
                    print(f'DEBUG: skip entry {tt.id}: {ex}')
            return smap

        schedule_map   = _load_schedule_map()
        ids_to_delete  = set()   # accumulated across all collision groups

        def _mark_for_deletion(entries):
            """
            Keep the entry with the LOWEST id (earliest created).
            Mark every other entry (ids [1:]) for deletion.
            Never marks ALL entries — always leaves exactly one.
            """
            if len(entries) < 2:
                return
            sorted_by_id = sorted(entries, key=lambda e: e.id)
            # sorted_by_id[0] is KEPT — skip it
            for entry in sorted_by_id[1:]:       # delete the rest
                ids_to_delete.add(entry.id)

        for timeslot_key, entries in schedule_map.items():
            if len(entries) <= 1:
                continue
            try:
                day, start_time, end_time = timeslot_key.split('_')
            except ValueError:
                continue

            # ── venue collisions ──────────────────────────────────────────
            venue_groups = defaultdict(list)
            for entry in entries:
                if entry.venue:
                    venue_groups[entry.venue.code].append(entry)

            for venue_code, v_entries in venue_groups.items():
                if len(v_entries) < 2:
                    continue
                # Same BASE course_code (any section/stream/group suffix —
                # "(TAG)", "-A", " GROUP B", " b", etc. — discarded) sharing
                # a room at the same time is never a room double-booking,
                # even across different program-years or different
                # lecturers (e.g. a cross-listed class serving two
                # program-years).
                distinct = set()
                for e in v_entries:
                    raw_cc = getattr(e.course_allocation, 'course_code', None)
                    cc  = course_base_key(raw_cc) or raw_cc or f'_id_{e.id}'
                    distinct.add(cc)
                if len(distinct) <= 1:
                    continue  # all same course, not a collision
                # BUG FIX: a non-destructive CombinedCourseGroup keeps DIFFERENT
                # course_code values per member (e.g. COSC 101 / COSC 101(App) /
                # COSC 101(BBIT)) while sharing the same lecturer + venue + slot
                # on purpose. Without this check the (code, lecturer) distinctness
                # test above sees >1 distinct pairs and wrongly treats the whole
                # merged booking as a venue collision, deleting all but one row.
                # This check already exists in the lecturer-collision block below
                # and in the read-only Pass 2 report — it was simply missing here,
                # in the pass that actually performs the deletion.
                alloc_ids_in_venue = [e.course_allocation.id for e in v_entries if e.course_allocation]
                if len(alloc_ids_in_venue) >= 2:
                    all_combined = all(
                        _are_in_same_combined_group(alloc_ids_in_venue[i], alloc_ids_in_venue[j])
                        for i in range(len(alloc_ids_in_venue))
                        for j in range(i + 1, len(alloc_ids_in_venue))
                    )
                    if all_combined:
                        continue  # intentional combined group sharing a venue — not a conflict
                _mark_for_deletion(v_entries)

            # ── lecturer collisions ───────────────────────────────────────
            lect_groups = defaultdict(list)
            for entry in entries:
                lect = getattr(entry.course_allocation, 'lecturer', None) if entry.course_allocation else None
                if lect:
                    lect_groups[lect.id].append(entry)

            for lect_id, l_entries in lect_groups.items():
                if len(l_entries) < 2:
                    continue
                # Pairwise check (not a blanket "any same course code"
                # shortcut): a lecturer teaching the SAME course in the
                # SAME venue at the same time is one physical class, not a
                # double-booking. If the venue differs, it's still a real
                # conflict — the lecturer can't physically be in two rooms
                # at once, even for the same course code.
                real_conflict_entries = []
                for i, ei in enumerate(l_entries):
                    for j, ej in enumerate(l_entries):
                        if j <= i:
                            continue
                        if ei.course_allocation and ej.course_allocation:
                            if _are_in_same_combined_group(
                                ei.course_allocation.id, ej.course_allocation.id
                            ):
                                continue  # combined group — intentional, not a conflict
                            cc_i = course_base_key(
                                getattr(ei.course_allocation, 'course_code', None)
                            )
                            cc_j = course_base_key(
                                getattr(ej.course_allocation, 'course_code', None)
                            )
                            same_course = cc_i is not None and cc_i == cc_j
                            venue_i = ei.venue.code if ei.venue else None
                            venue_j = ej.venue.code if ej.venue else None
                            same_venue = (
                                venue_i is not None
                                and venue_j is not None
                                and venue_i.strip().lower() == venue_j.strip().lower()
                            )
                            if same_course and same_venue:
                                continue  # same course, same venue — one class, not a conflict
                        real_conflict_entries.append(ei)
                        real_conflict_entries.append(ej)
                seen = set()
                real_conflict_entries = [
                    e for e in real_conflict_entries
                    if e.id not in seen and not seen.add(e.id)
                ]
                if len(real_conflict_entries) < 2:
                    continue
                _mark_for_deletion(real_conflict_entries)

            # ── program-year collisions ───────────────────────────────────
            prog_groups = defaultdict(list)
            for entry in entries:
                if not entry.course_allocation:
                    continue
                prog = getattr(entry.course_allocation, 'program', None)
                if not prog:
                    continue
                year = _get_year_value(entry.course_allocation)
                if year:
                    prog_groups[f'{prog.id}_{year}'].append(entry)

            for prog_key, p_entries in prog_groups.items():
                if len(p_entries) < 2:
                    continue
                # Filter to only genuinely conflicting (non-exempt) pairs
                real_ids = set()
                for i, ei in enumerate(p_entries):
                    for j, ej in enumerate(p_entries):
                        if j <= i:
                            continue
                        if not is_scheduling_exempt(ei.course_allocation, ej.course_allocation):
                            # DEFENSE-IN-DEPTH: same protection as the venue/lecturer
                            # blocks above — two members of the same CombinedCourseGroup
                            # can in rare cases share program+year (e.g. two intake
                            # streams of one degree); they must never be deleted as a
                            # program-year collision since they're one intentional class.
                            if ei.course_allocation and ej.course_allocation and _are_in_same_combined_group(
                                ei.course_allocation.id, ej.course_allocation.id
                            ):
                                continue
                            real_ids.add(ei.id)
                            real_ids.add(ej.id)
                real_entries = [e for e in p_entries if e.id in real_ids]
                if len(real_entries) < 2:
                    continue
                _mark_for_deletion(real_entries)

        # ════════════════════════════════════════════════════════════
        # DELETE — remove colliding extras NOW, before building response
        # ════════════════════════════════════════════════════════════
        # DISABLED (commented out for now): auto-deleting colliding rows
        # before reload. This used to silently remove the "extra" rows in
        # each collision group (keeping only the lowest-id entry) every
        # time the conflicts API was hit. Leaving collisions in place now —
        # only detection/reporting (PASS 2 below) still runs.
        auto_resolved = 0
        # if ids_to_delete:
        #     try:
        #         deleted, _ = Timetable.objects.filter(id__in=ids_to_delete).delete()
        #         auto_resolved = deleted
        #         print(f'DEBUG: auto-resolved {auto_resolved} collision entries (kept 1 per group)')
        #     except Exception as ex:
        #         print(f'DEBUG: auto-resolution error (non-fatal): {ex}')

        # ════════════════════════════════════════════════════════════
        # PASS 2 — re-query the now-clean DB and build the response
        # ════════════════════════════════════════════════════════════
        conflicts = {
            'program_conflicts':    [],
            'lecturer_conflicts':   [],
            'venue_conflicts':      [],
            'preference_conflicts': [],
            'total_conflicts':      0,
            'auto_resolved':        auto_resolved,
            'error':                None,
        }

        schedule_map = _load_schedule_map()   # fresh query after deletions

        for timeslot_key, entries in schedule_map.items():
            if len(entries) <= 1:
                continue
            try:
                day, start_time, end_time = timeslot_key.split('_')
            except ValueError:
                continue

            # venue conflicts (any still remaining after auto-resolve)
            venue_groups = defaultdict(list)
            for entry in entries:
                try:
                    if entry.venue:
                        venue_groups[entry.venue.code].append(entry)
                except Exception as e:
                    print(f'DEBUG: venue group error: {e}')

            for venue_code, venue_entries in venue_groups.items():
                if len(venue_entries) > 1:
                    # Same BASE course (tag stripped) sharing a room at the
                    # same time is never a room double-booking — even across
                    # different program-years or different lecturers (e.g. a
                    # cross-listed class serving two program-years). This
                    # must compare the base code, not the raw tagged one —
                    # see PASS 1 above.
                    distinct = set()
                    for e in venue_entries:
                        raw_cc = getattr(e.course_allocation, 'course_code', None)
                        cc  = course_base_key(raw_cc) or raw_cc or f'_id_{e.id}'
                        distinct.add(cc)
                    if len(distinct) <= 1:
                        continue
                    # CombinedCourseGroup: all entries share the same group → skip
                    all_combined = True
                    alloc_ids_in_cell = [e.course_allocation.id for e in venue_entries if e.course_allocation]
                    for i in range(len(alloc_ids_in_cell)):
                        for j in range(i + 1, len(alloc_ids_in_cell)):
                            if not _are_in_same_combined_group(alloc_ids_in_cell[i], alloc_ids_in_cell[j]):
                                all_combined = False
                                break
                        if not all_combined:
                            break
                    if all_combined and len(alloc_ids_in_cell) >= 2:
                        continue  # intentional combined group sharing a venue
                    conflict = {
                        'type': 'venue_conflict',
                        'day': day,
                        'timeslot': f'{start_time} - {end_time}',
                        'venue': venue_code,
                        'courses': [],
                        'message': f'Venue {venue_code} double-booked at same time',
                    }
                    for entry in venue_entries:
                        try:
                            conflict['courses'].append({
                                'id':             entry.id,
                                'course_code':    getattr(entry.course_allocation, 'course_code', 'Unknown'),
                                'course_name':    getattr(entry.course_allocation, 'course_name', 'Unknown'),
                                'lecturer':       getattr(getattr(entry.course_allocation, 'lecturer', None), 'name', 'Unassigned'),
                                'program':        getattr(getattr(entry.course_allocation, 'program',  None), 'name', 'N/A'),
                                'venue':          venue_code,
                                'student_group':  _student_group_name_of(entry.course_allocation),
                                'specialization': _specialization_name_of(entry.course_allocation),
                            })
                        except Exception as e:
                            print(f'DEBUG: {e}')
                    conflicts['venue_conflicts'].append(conflict)

            # lecturer conflicts
            lect_groups = defaultdict(list)
            for entry in entries:
                try:
                    lect = getattr(entry.course_allocation, 'lecturer', None)
                    if lect:
                        lect_groups[lect.id].append(entry)
                except Exception as e:
                    print(f'DEBUG: {e}')

            for lect_id, lect_entries in lect_groups.items():
                if len(lect_entries) > 1:
                    # Pairwise check (not a blanket "any same course code"
                    # shortcut): a lecturer teaching the SAME base course in
                    # the SAME venue at the same slot is one class split
                    # across streams/sections (e.g. "MATH 124(ASTA)" / "MATH
                    # 124(D)") → not a conflict. If the venue differs, it's
                    # still a real conflict — the lecturer can't physically
                    # be in two rooms at once, even for the same course.
                    real_entries = []
                    for i, ei in enumerate(lect_entries):
                        for j, ej in enumerate(lect_entries):
                            if j <= i:
                                continue
                            if ei.course_allocation and ej.course_allocation:
                                cc_i = course_base_key(
                                    getattr(ei.course_allocation, 'course_code', None)
                                )
                                cc_j = course_base_key(
                                    getattr(ej.course_allocation, 'course_code', None)
                                )
                                same_course = cc_i is not None and cc_i == cc_j
                                venue_i = ei.venue.code if ei.venue else None
                                venue_j = ej.venue.code if ej.venue else None
                                same_venue = (
                                    venue_i is not None
                                    and venue_j is not None
                                    and venue_i.strip().lower() == venue_j.strip().lower()
                                )
                                if same_course and same_venue:
                                    continue  # same course, same venue — one class, not a conflict
                                if _are_in_same_combined_group(
                                    ei.course_allocation.id, ej.course_allocation.id
                                ):
                                    continue
                            real_entries.append(ei)
                            real_entries.append(ej)
                    seen = set()
                    real_entries = [e for e in real_entries if e.id not in seen and not seen.add(e.id)]
                    if len(real_entries) < 2:
                        continue
                    try:
                        lect_obj  = Lecturer.objects.filter(id=lect_id).first()
                        lect_name = lect_obj.name if lect_obj else f'Lecturer {lect_id}'
                    except Exception:
                        lect_name = f'Lecturer {lect_id}'
                    conflict = {
                        'type': 'lecturer_conflict',
                        'day': day,
                        'timeslot': f'{start_time} - {end_time}',
                        'lecturer': lect_name,
                        'courses': [],
                        'message': f'Lecturer {lect_name} scheduled for multiple courses simultaneously',
                    }
                    for entry in real_entries:
                        try:
                            conflict['courses'].append({
                                'id':             entry.id,
                                'course_code':    getattr(entry.course_allocation, 'course_code', 'Unknown'),
                                'course_name':    getattr(entry.course_allocation, 'course_name', 'Unknown'),
                                'lecturer':       lect_name,
                                'program':        getattr(getattr(entry.course_allocation, 'program', None), 'name', 'N/A'),
                                'venue':          getattr(entry.venue, 'code', 'Unknown') if entry.venue else 'Unknown',
                                'student_group':  _student_group_name_of(entry.course_allocation),
                                'specialization': _specialization_name_of(entry.course_allocation),
                            })
                        except Exception as e:
                            print(f'DEBUG: {e}')
                    conflicts['lecturer_conflicts'].append(conflict)

            # program-year conflicts
            prog_groups = defaultdict(list)
            for entry in entries:
                try:
                    prog = getattr(entry.course_allocation, 'program', None)
                    if prog:
                        year = _get_year_value(entry.course_allocation)
                        if year:
                            prog_groups[f'{prog.id}_{year}'].append(entry)
                except Exception as e:
                    print(f'DEBUG: {e}')

            for prog_key, prog_entries in prog_groups.items():
                if len(prog_entries) > 1:
                    try:
                        real_ids = set()
                        for i, ei in enumerate(prog_entries):
                            for j, ej in enumerate(prog_entries):
                                if j <= i:
                                    continue
                                if not is_scheduling_exempt(ei.course_allocation, ej.course_allocation):
                                    real_ids.add(ei.id)
                                    real_ids.add(ej.id)
                        real_conflicts = [e for e in prog_entries if e.id in real_ids]
                        if len(real_conflicts) < 2:
                            continue

                        prog_id, year = prog_key.split('_')
                        prog_obj  = Program.objects.filter(id=prog_id).first()
                        prog_name = prog_obj.name if prog_obj else f'Program {prog_id}'

                        # Severity: 'stem' (softer, yellow in the panel) only when
                        # EVERY course in this collision belongs to a
                        # SpecializationStem — i.e. the whole thing is a
                        # stem-vs-stem overlap. The moment a plain shared /
                        # mandatory course (no stem membership at all) is one of
                        # the colliders, this is an unambiguous, ordinary
                        # program-year collision and stays 'hard' (red) —
                        # "still shared [flagged] if any [non-stem] collision
                        # exists" — a real course really is double-booked here.
                        is_stem_conflict = all(
                            _alloc_in_any_stem(e.course_allocation) for e in real_conflicts
                        )

                        # ── Name the actual Student Group / specialization
                        # involved, not just the bare Program+Year, so staff
                        # reading the conflict list can see AT A GLANCE which
                        # cohort collides — e.g. "Student Group Math Geo has
                        # COSC 101 and COSC 205 both at Monday 09:00-11:00"
                        # instead of a generic Program-Year message that
                        # doesn't distinguish between groups within the year.
                        group_names = sorted({
                            n for n in (_student_group_name_of(e.course_allocation) for e in real_conflicts)
                            if n
                        })
                        stem_names = sorted({
                            n for n in (_specialization_name_of(e.course_allocation) for e in real_conflicts)
                            if n
                        })

                        if len(group_names) == 1:
                            subject = f'Student Group {group_names[0]} ({prog_name} Year {year})'
                        elif len(group_names) > 1:
                            subject = f'{prog_name} Year {year} (Groups: {", ".join(group_names)})'
                        else:
                            subject = f'{prog_name} Year {year}'

                        if is_stem_conflict:
                            stem_note = f' — specialization(s): {", ".join(stem_names)}' if stem_names else ''
                            message = (
                                f'{subject} has multiple specialization-stem courses at the same '
                                f'time (verify: students only pick one stem){stem_note}'
                            )
                        else:
                            message = f'{subject} has multiple courses at the same time'

                        conflict = {
                            'type': 'program_conflict',
                            'day': day,
                            'timeslot': f'{start_time} - {end_time}',
                            'program': prog_name,
                            'year': year,
                            'student_groups': group_names,
                            'specializations': stem_names,
                            'courses': [],
                            'severity': 'stem' if is_stem_conflict else 'hard',
                            'message': message,
                        }
                        for entry in real_conflicts:
                            try:
                                conflict['courses'].append({
                                    'id':             entry.id,
                                    'course_code':    getattr(entry.course_allocation, 'course_code', 'Unknown'),
                                    'course_name':    getattr(entry.course_allocation, 'course_name', 'Unknown'),
                                    'lecturer':       getattr(getattr(entry.course_allocation, 'lecturer', None), 'name', 'Unassigned'),
                                    'venue':          getattr(entry.venue, 'code', 'Unknown') if entry.venue else 'Unknown',
                                    'program':        prog_name,
                                    'is_stem':        _alloc_in_any_stem(entry.course_allocation),
                                    'student_group':  _student_group_name_of(entry.course_allocation),
                                    'specialization': _specialization_name_of(entry.course_allocation),
                                })
                            except Exception as e:
                                print(f'DEBUG: {e}')
                        conflicts['program_conflicts'].append(conflict)
                    except Exception as e:
                        print(f'DEBUG: program conflict error: {e}')

        # ════════════════════════════════════════════════════════════
        # PREFERENCE / BLOCKED-DAY CHECK — unlike the collision passes
        # above, this doesn't need two entries sharing a slot; a single
        # course sitting on a lecturer's blocked day, or outside their
        # stated preferred days/times, is flagged on its own. 'blocked'
        # severity is a real rule violation (course placed somewhere the
        # lecturer marked unavailable) — the frontend renders it the same
        # yellow used for 'stem' soft-conflicts, since it's a should-fix
        # rather than a physically-impossible double-booking. 'preference'
        # severity is purely informational (lecturer stated a wish, it
        # wasn't followed) and is styled lighter still.
        # ════════════════════════════════════════════════════════════
        seen_lecturer_days = set()
        # Batch-load once — see _build_lecturer_constraint_maps() docstring
        # for why this matters: without it, every entry below triggers 2
        # separate DB queries, and this loop runs on every scheduled row.
        blocked_map, pref_slots_map = _build_lecturer_constraint_maps()
        for timeslot_key, entries in schedule_map.items():
            try:
                day, start_time, end_time = timeslot_key.split('_')
            except ValueError:
                continue
            for entry in entries:
                alloc = entry.course_allocation
                lecturer = getattr(alloc, 'lecturer', None) if alloc else None
                if not lecturer:
                    continue
                dedupe_key = (lecturer.id, day, start_time, end_time)
                blocked = lecturer_blocked_slot_hit(lecturer, day, entry.start_time, entry.end_time, blocked_map)
                if blocked:
                    if dedupe_key in seen_lecturer_days:
                        continue
                    seen_lecturer_days.add(dedupe_key)
                    span = "the whole day" if (not blocked.start_time and not blocked.end_time) else \
                        f"{blocked.start_time.strftime('%H:%M')}–{blocked.end_time.strftime('%H:%M')}"
                    reason = f" ({blocked.reason})" if blocked.reason else ""
                    conflicts['preference_conflicts'].append({
                        'type': 'blocked_slot',
                        'severity': 'blocked',
                        'day': day,
                        'timeslot': f'{start_time} - {end_time}',
                        'lecturer': lecturer.name,
                        'message': (
                            f'Lecturer {lecturer.name} is placed on {day} {span} even though '
                            f'they marked it unavailable{reason}'
                        ),
                        'courses': [{
                            'id':             entry.id,
                            'course_code':    getattr(alloc, 'course_code', 'Unknown'),
                            'course_name':    getattr(alloc, 'course_name', 'Unknown'),
                            'lecturer':       lecturer.name,
                            'venue':          getattr(entry.venue, 'code', 'Unknown') if entry.venue else 'Unknown',
                            'program':        getattr(getattr(alloc, 'program', None), 'name', 'N/A'),
                            'student_group':  _student_group_name_of(alloc),
                            'specialization': _specialization_name_of(alloc),
                        }],
                    })
                    continue
                mismatch = lecturer_preference_mismatch(lecturer, day, entry.start_time, entry.end_time, pref_slots_map)
                if mismatch:
                    if dedupe_key in seen_lecturer_days:
                        continue
                    seen_lecturer_days.add(dedupe_key)
                    conflicts['preference_conflicts'].append({
                        'type': 'preference_mismatch',
                        'severity': 'preference',
                        'day': day,
                        'timeslot': f'{start_time} - {end_time}',
                        'lecturer': lecturer.name,
                        'message': f'Lecturer {lecturer.name} {mismatch} (stated preference not honoured)',
                        'courses': [{
                            'id':             entry.id,
                            'course_code':    getattr(alloc, 'course_code', 'Unknown'),
                            'course_name':    getattr(alloc, 'course_name', 'Unknown'),
                            'lecturer':       lecturer.name,
                            'venue':          getattr(entry.venue, 'code', 'Unknown') if entry.venue else 'Unknown',
                            'program':        getattr(getattr(alloc, 'program', None), 'name', 'N/A'),
                            'student_group':  _student_group_name_of(alloc),
                            'specialization': _specialization_name_of(alloc),
                        }],
                    })

        conflicts['total_conflicts'] = (
            len(conflicts['program_conflicts']) +
            len(conflicts['lecturer_conflicts']) +
            len(conflicts['venue_conflicts'])
        )
        # Preference/blocked-day flags are kept out of total_conflicts (they
        # never block a save and aren't a physical double-booking) but the
        # count is still surfaced separately so the panel can show a badge
        # for them without inflating the main "hard conflicts" number.
        conflicts['total_preference_conflicts'] = len(conflicts['preference_conflicts'])
        print(f"DEBUG: After auto-resolve — {conflicts['total_conflicts']} conflicts remain, {auto_resolved} deleted")
        return JsonResponse(conflicts)

    except Exception as e:
        import traceback
        print(f'ERROR in timetable_conflicts_api: {e}')
        print(traceback.format_exc())
        return JsonResponse({
            'program_conflicts': [],
            'lecturer_conflicts': [],
            'venue_conflicts': [],
            'total_conflicts': 0,
            'auto_resolved': 0,
            'error': 'An error occurred while checking for conflicts',
            'debug_error': str(e) if request.user.is_staff else None,
        }, status=200)

# -----------------------
# Helper Functions
# -----------------------
def _handle_ajax_timetable_save(request, slot_map):
    """Handle AJAX timetable save with optimized queries"""
    alloc_id = request.POST.get("allocation_id")
    venue_input = (request.POST.get("venue") or "").strip()
    day = request.POST.get("day")
    slot_value = request.POST.get("slot")
    # When true, genuine collisions (lecturer/venue/program-year) are
    # downgraded from blocking errors to warnings and the save proceeds
    # anyway. Surfaced on the left-hand form as a "Force Save (Override)"
    # button that only appears after a collision has actually blocked a save.
    force_override = (request.POST.get("force_override_conflicts") or "").strip().lower() == "true"

    if not (alloc_id and venue_input and day and slot_value):
        return JsonResponse({"status": "error", "messages": ["All fields are required."]}, status=400)

    try:
        allocation = CourseAllocation.objects.select_related(
            "program", "lecturer"
        ).only(
            "id", "program_id", "lecturer_id", "course_code", "number_of_students", 
            "program__name", "lecturer__name"
        ).get(pk=alloc_id)
    except CourseAllocation.DoesNotExist:
        return JsonResponse({"status": "error", "messages": ["Selected allocation not found."]}, status=404)

    # Validate slot times
    if slot_value not in slot_map:
        return JsonResponse({"status": "error", "messages": ["Invalid slot selected."]}, status=400)

    try:
        start_t = datetime.strptime(slot_value, "%H:%M").time()
        end_t = datetime.strptime(slot_map[slot_value], "%H:%M").time()
    except ValueError:
        return JsonResponse({"status": "error", "messages": ["Invalid slot times."]}, status=400)

    if not (start_t < end_t):
        return JsonResponse({"status": "error", "messages": ["Start time must be before end time."]}, status=400)

    # ── CombinedCourseGroup awareness ───────────────────────────────────
    # A member of a CombinedCourseGroup is never scheduled on its own — the
    # whole group is one atomic unit (same rule build_global_merged_tasks()
    # enforces for the autoscheduler, and _get_move_bundle() enforces for
    # Simulate Move). This is what was letting individual members of a
    # combined group (e.g. MATH 221 / MATH 221 B / MATH 221 C) get booked
    # into separate slots when placed here.
    combined_group, group_members = _get_combined_group_for_allocation(allocation)

    if combined_group and len(group_members) > 1:
        already_scheduled = Timetable.objects.filter(
            course_allocation_id__in=[m.id for m in group_members]
        ).select_related('venue', 'course_allocation').first()
        if already_scheduled:
            venue_name = already_scheduled.venue.code if already_scheduled.venue else "Unknown"
            return JsonResponse({
                "status": "error",
                "messages": [
                    f"❌ {combined_group.display_name()} is already scheduled "
                    f"({already_scheduled.course_allocation.course_code}) on "
                    f"{already_scheduled.day} {already_scheduled.start_time.strftime('%H:%M')}"
                    f"-{already_scheduled.end_time.strftime('%H:%M')} in {venue_name}. "
                    f"Use Simulate Move to relocate the whole group instead of "
                    f"scheduling a member separately."
                ],
            }, status=400)

        messages_list, error_block_save = _check_conflicts_for_members(
            group_members, venue_input, day, start_t, end_t, force_override=force_override
        )
        if error_block_save:
            return JsonResponse({
                "status": "error", "messages": messages_list, "conflict": True,
            }, status=400)

        return _save_timetable_entries(
            allocation, venue_input, day, start_t, end_t, messages_list,
            forced_members=group_members, group_display_name=combined_group.display_name(),
        )

    # Prefetch related data for conflict checks
    messages_list, error_block_save = _check_conflicts(
        allocation, venue_input, day, start_t, end_t, force_override=force_override
    )

    if error_block_save:
        return JsonResponse({
            "status": "error", "messages": messages_list, "conflict": True,
        }, status=400)

    # Handle auto-merge and save
    return _save_timetable_entries(allocation, venue_input, day, start_t, end_t, messages_list)

def _check_conflicts(allocation, venue_input, day, start_t, end_t, force_override=False):
    """
    Optimized conflict checking with batched queries and smart exemption rules.

    force_override: when True, genuine collisions (program-year, lecturer,
    venue) are still detected and reported, but downgraded from a blocking
    "❌" error to a "⚠️ Overridden" warning, and error_block_save stays
    False so the save proceeds anyway. Used by the "Force Save (Override)"
    button that appears on the panel after a collision has already blocked
    a normal save once.

    Exemptions (not a real conflict) — mirrors is_scheduling_exempt():
    - Same base course code (split sections/streams of ONE course, e.g.
      'COSC 103-D' vs 'COSC 103-F', or two rows both coded 'COMS 101')
    - Different SpecializationStem within the same category
    - Different, explicitly-set StudentGroup
    - Elective / selection-group courses (students choose one)
    - Different intake cohorts (normal vs special)

    Additional room/lecturer exemptions:
    - VENUE: the SAME course (base code) sharing a room at the same time is
      never a room double-booking, even across different program-years.
    - LECTURER: a lecturer teaching the SAME course in the SAME venue at the
      same time is never a lecturer double-booking (one physical class).
      It's still flagged if the venue differs — the lecturer can't
      physically be in two rooms at once.
    """
    messages_list = []
    error_block_save = False

    # Get venue object for capacity check
    venue_obj = Venue.objects.filter(code__iexact=venue_input).first()

    # Batch fetch all potential conflicts in single queries
    alloc_year = _get_year_value(allocation)

    # ── PROGRAM CONFLICTS ─────────────────────────────────────────────
    if allocation.program and alloc_year is not None:
        program_overlaps = Timetable.objects.filter(
            course_allocation__program=allocation.program,
            day__iexact=day
        ).select_related(
            'course_allocation', 'course_allocation__lecturer',
            'course_allocation__selection_group',
            'course_allocation__specialization_stem',
            'course_allocation__program_course',
            'venue'
        ).only(
            'start_time', 'end_time',
            'course_allocation__lecturer_id', 'course_allocation__course_code',
            'course_allocation__is_elective', 'course_allocation__intake',
            'course_allocation__selection_group_id',
            'course_allocation__specialization_stem_id',
            'course_allocation__specialization_stem__category_id',
            'course_allocation__student_group_id',
            'course_allocation__program_course__semester',
            'venue__code'
        )

        for existing in program_overlaps:
            existing_year = _get_year_value(existing.course_allocation)
            if existing_year is None or alloc_year != existing_year:
                continue

            # Apply smart exemption rules
            if is_scheduling_exempt(allocation, existing.course_allocation):
                # Not a real conflict — inform the user why it is allowed
                if time_overlaps(start_t, end_t, existing.start_time, existing.end_time):
                    stems_a = _specialization_stem_ids_of(allocation)
                    stems_b = _specialization_stem_ids_of(existing.course_allocation)
                    sgrp_a = _student_group_id_of(allocation)
                    sgrp_b = _student_group_id_of(existing.course_allocation)
                    if _is_same_base_course_pair(allocation, existing.course_allocation):
                        reason = "the same course split into sections/streams (different students, one course)"
                    elif _semester_of(allocation) is not None and _semester_of(existing.course_allocation) is not None and _semester_of(allocation) != _semester_of(existing.course_allocation) and _intake_of(allocation) != _intake_of(existing.course_allocation):
                        reason = "a shifted-semester special intake overlapping a different semester's normal intake"
                    elif stems_a and stems_b and not (stems_a & stems_b):
                        reason = "different specialization stems (same category — a student only picks one)"
                    elif sgrp_a is not None and sgrp_b is not None and sgrp_a != sgrp_b:
                        reason = "different student groups (separate cohorts)"
                    elif _is_elective_alloc(allocation) or _is_elective_alloc(existing.course_allocation):
                        reason = "elective/selection courses"
                    elif _intake_of(allocation) != _intake_of(existing.course_allocation):
                        reason = "different intake cohorts"
                    else:
                        reason = "selection group membership"
                    messages_list.append(
                        f"ℹ️ {allocation.program} Year {alloc_year}: "
                        f"{existing.course_allocation.course_code} is at the same time "
                        f"— allowed because these are {reason}."
                    )
                continue

            existing_lecturer = getattr(existing.course_allocation, "lecturer", None)
            same_lecturer = (existing_lecturer and allocation.lecturer and
                           existing_lecturer.id == allocation.lecturer.id)

            if (not same_lecturer) and time_overlaps(start_t, end_t, existing.start_time, existing.end_time):
                venue_name = existing.venue.code if existing.venue else "Unknown"
                if force_override:
                    messages_list.append(
                        f"⚠️ Overridden: Program '{allocation.program}' Year '{alloc_year}' "
                        f"already has {existing.course_allocation.course_code} at this time "
                        f"in {venue_name} (different lecturer) — saved anyway by override."
                    )
                else:
                    messages_list.append(
                        f"❌ Conflict: Program '{allocation.program}' Year '{alloc_year}' "
                        f"already has {existing.course_allocation.course_code} at this time "
                        f"in {venue_name} (different lecturer)."
                    )
                    error_block_save = True

    # ── LECTURER CONFLICTS ────────────────────────────────────────────
    if allocation.lecturer:
        lecturer_overlaps = Timetable.objects.filter(
            course_allocation__lecturer=allocation.lecturer,
            day__iexact=day
        ).select_related('venue').only(
            'start_time', 'end_time', 'course_allocation__course_code', 'venue__code',
            'course_allocation_id',
        )

        for existing in lecturer_overlaps:
            if time_overlaps(start_t, end_t, existing.start_time, existing.end_time):
                # ── CombinedCourseGroup exemption ──────────────────────
                # If both allocations belong to the same CombinedCourseGroup
                # they are intentionally taught together by the same lecturer —
                # this is NOT a double-booking.
                if _are_in_same_combined_group(allocation.id, existing.course_allocation_id):
                    messages_list.append(
                        f"ℹ️ Lecturer overlap allowed: {allocation.course_code} and "
                        f"{existing.course_allocation.course_code} are in the same "
                        f"Combined Course Group (taught together)."
                    )
                    continue
                # ── Same course, same venue exemption ───────────────────
                # A lecturer teaching the SAME course in the SAME venue at
                # the same time is one physical class (e.g. split across
                # program-year sections) — NOT a lecturer double-booking.
                # It only becomes a real conflict if the venue differs
                # (the lecturer can't physically be in two rooms at once).
                #
                # GUARD: never apply this exemption if `existing` already
                # belongs to a DIFFERENT CombinedCourseGroup than
                # `allocation` — course_base_key() strips every trailing
                # section/group marker, so two entirely separate combined
                # groups of the same base course (e.g. "BOTA 111-A" and
                # "BOTA 111-D") would otherwise be waved through as "the
                # same course" and merged into one class.
                existing_cc = course_base_key(
                    getattr(existing.course_allocation, "course_code", None)
                )
                alloc_cc = course_base_key(allocation.course_code)
                existing_venue_code = existing.venue.code if existing.venue else None
                same_course = alloc_cc is not None and alloc_cc == existing_cc
                same_venue = (
                    existing_venue_code is not None
                    and existing_venue_code.strip().lower() == venue_input.strip().lower()
                )
                different_combined_group = _other_belongs_to_a_different_combined_group(
                    allocation.id, existing.course_allocation_id
                )
                if same_course and same_venue and not different_combined_group:
                    messages_list.append(
                        f"ℹ️ Lecturer overlap allowed: {allocation.course_code} and "
                        f"{existing.course_allocation.course_code} are the same course "
                        f"taught in the same venue ({venue_input}) — not a double-booking."
                    )
                    continue
                venue_name = existing.venue.code if existing.venue else "Unknown"
                if force_override:
                    messages_list.append(
                        f"⚠️ Overridden: Lecturer already teaching "
                        f"{existing.course_allocation.course_code} at this time in {venue_name} "
                        f"— saved anyway by override."
                    )
                else:
                    messages_list.append(
                        f"❌ Conflict: Lecturer already teaching "
                        f"{existing.course_allocation.course_code} at this time in {venue_name}."
                    )
                    error_block_save = True

    # ── VENUE CONFLICTS ───────────────────────────────────────────────
    venue_overlaps = Timetable.objects.filter(
        venue__code__iexact=venue_input,
        day__iexact=day
    ).select_related('course_allocation', 'venue').only(
        'start_time', 'end_time', 'course_allocation__course_code', 'venue__code',
        'course_allocation_id',
    )

    for existing in venue_overlaps:
        if time_overlaps(start_t, end_t, existing.start_time, existing.end_time):
            # Combined groups share the same venue intentionally — not a collision.
            if _are_in_same_combined_group(allocation.id, existing.course_allocation_id):
                messages_list.append(
                    f"ℹ️ Venue overlap allowed: {allocation.course_code} and "
                    f"{existing.course_allocation.course_code} share venue {venue_input} "
                    f"as part of a Combined Course Group."
                )
                continue
            # ── Same course exemption ───────────────────────────────────
            # The SAME course (base code, tag stripped) sharing a room at
            # the same time is not a room double-booking, even if it's
            # attached to a different program/year (e.g. a cross-listed
            # class serving two program-years in one physical session).
            #
            # GUARD: same as the lecturer check above — never apply this
            # exemption if `existing` belongs to a DIFFERENT
            # CombinedCourseGroup than `allocation`, or two unrelated
            # combined groups of the same base course get merged into one
            # class.
            existing_cc = course_base_key(
                getattr(existing.course_allocation, "course_code", None)
            )
            alloc_cc = course_base_key(allocation.course_code)
            different_combined_group = _other_belongs_to_a_different_combined_group(
                allocation.id, existing.course_allocation_id
            )
            if alloc_cc is not None and alloc_cc == existing_cc and not different_combined_group:
                messages_list.append(
                    f"ℹ️ Venue overlap allowed: {allocation.course_code} and "
                    f"{existing.course_allocation.course_code} are the same course "
                    f"sharing {venue_input} — not a double-booking."
                )
                continue
            if force_override:
                messages_list.append(
                    f"⚠️ Overridden: Venue conflict: {venue_input} already booked for "
                    f"{existing.course_allocation.course_code} at this time — saved anyway by override."
                )
            else:
                messages_list.append(
                    f"❌ Venue conflict: {venue_input} already booked for "
                    f"{existing.course_allocation.course_code} at this time."
                )
                error_block_save = True

    # ── LECTURER BLOCKED-DAY / PREFERENCE CHECK ─────────────────────────
    # Blocked slots are a HARD constraint (mirrors what the autoscheduler
    # already enforces via ConflictTracker) — a manual save/move that lands
    # a lecturer on a day/time they've blocked should be treated the same
    # way as any other collision. Preferences are SOFT — never block the
    # save, just flag it so staff can see it and, on the frontend, the cell
    # gets the same yellow styling used for other soft/stem conflicts.
    if allocation.lecturer:
        blocked = lecturer_blocked_slot_hit(allocation.lecturer, day, start_t, end_t)
        if blocked:
            span = "the whole day" if (not blocked.start_time and not blocked.end_time) else \
                f"{blocked.start_time.strftime('%H:%M')}–{blocked.end_time.strftime('%H:%M')}"
            reason = f" ({blocked.reason})" if blocked.reason else ""
            if force_override:
                messages_list.append(
                    f"⚠️ Overridden: {allocation.lecturer.name} has blocked {day} {span}{reason} "
                    f"— saved anyway by override."
                )
            else:
                messages_list.append(
                    f"❌ Blocked day/time: {allocation.lecturer.name} has marked {day} {span} "
                    f"as unavailable{reason}."
                )
                error_block_save = True
        else:
            mismatch = lecturer_preference_mismatch(allocation.lecturer, day, start_t, end_t)
            if mismatch:
                messages_list.append(
                    f"⚠️ Preference not honoured: {allocation.lecturer.name} {mismatch}."
                )

    # ── VENUE CAPACITY CHECK ──────────────────────────────────────────
    students = allocation.number_of_students or 0

    if venue_obj:
        if venue_obj.capacity:
            if students > venue_obj.capacity:
                messages_list.append(
                    f"⚠️ Venue capacity warning: {venue_obj.code} holds {venue_obj.capacity}, "
                    f"but course has {students} students ({students - venue_obj.capacity} over capacity)."
                )
            else:
                messages_list.append(
                    f"✅ Venue capacity: {venue_obj.code} ({venue_obj.capacity}) "
                    f"can accommodate {students} students."
                )
        else:
            messages_list.append(
                f"⚠️ Venue capacity not defined for {venue_obj.code}. Please verify capacity manually."
            )
    else:
        messages_list.append(
            f"⚠️ Venue '{venue_input}' not found in database. Capacity check skipped."
        )

    return messages_list, error_block_save

def _check_conflicts_for_members(members, venue_input, day, start_t, end_t, force_override=False):
    """
    Run `_check_conflicts` for every member of a CombinedCourseGroup and merge
    the results, so a program-year collision on ANY member (not just the one
    the user happened to click) blocks the save. Informational ("ℹ️"/"✅"/"⚠️")
    messages are de-duplicated; a "❌" from any member blocks the whole group
    (unless force_override is set, in which case collisions are downgraded
    to "⚠️ Overridden" warnings and the save proceeds).
    """
    messages_list = []
    seen = set()
    error_block_save = False
    for member in members:
        msgs, blocked = _check_conflicts(member, venue_input, day, start_t, end_t, force_override=force_override)
        error_block_save = error_block_save or blocked
        for m in msgs:
            if m not in seen:
                seen.add(m)
                messages_list.append(m)
    return messages_list, error_block_save


def _save_timetable_entries(allocation, venue_input, day, start_t, end_t, messages_list, forced_members=None, group_display_name=None):
    """Handle timetable entry saving with optimized queries.

    forced_members: when the allocation being placed belongs to a
    CombinedCourseGroup, pass the group's full member list here. This
    bypasses the weaker "same course code + same lecturer" heuristic below
    (unrelated to CombinedCourseGroup — it predates it) and instead books
    every member of the group at the exact same venue/day/time, the same
    atomic-unit guarantee build_global_merged_tasks() gives the autoscheduler.
    """
    if forced_members is not None:
        merged_allocations = list(forced_members)
        label = group_display_name or allocation.course_code
        messages_list.append(
            f"Scheduled as Combined Course Group: {label} "
            f"({len(merged_allocations)} courses, "
            f"{sum(a.number_of_students or 0 for a in merged_allocations)} students total)."
        )
        return _finalize_timetable_save(allocation, merged_allocations, venue_input, day, start_t, end_t, messages_list)

    # SAME-COURSE SAME-LECTURER AUTO-MERGE EXCEPTION
    merged_allocations = [allocation]
    if allocation.lecturer:
        # Single query to find similar allocations
        similar_allocs = CourseAllocation.objects.filter(
            course_code__iexact=allocation.course_code,
            lecturer=allocation.lecturer
        ).exclude(id=allocation.id).only('id', 'number_of_students')
        
        total_students = allocation.number_of_students or 0
        similar_students = sum(alloc.number_of_students or 0 for alloc in similar_allocs)
        total_students += similar_students

        if total_students <= 210:
            merged_allocations.extend(similar_allocs)
            messages_list.append(
                f"Auto-merged: {allocation.course_code} ({len(merged_allocations)} groups, total {total_students} students)."
            )
        else:
            running_total = allocation.number_of_students or 0
            for other in similar_allocs:
                if running_total + (other.number_of_students or 0) <= 200:
                    merged_allocations.append(other)
                    running_total += other.number_of_students or 0
            if len(merged_allocations) > 1:
                messages_list.append(
                    f"Partial merge: combined {len(merged_allocations)} groups under 200; others remain separate."
                )

    return _finalize_timetable_save(allocation, merged_allocations, venue_input, day, start_t, end_t, messages_list)


def _finalize_timetable_save(allocation, merged_allocations, venue_input, day, start_t, end_t, messages_list):
    """Shared tail of _save_timetable_entries: venue resolution + bulk create + AutoMergedExamGroup bookkeeping."""
    # Get or create venue object
    venue_obj, created = Venue.objects.get_or_create(
        code=venue_input,
        defaults={'capacity': None}
    )
    
    if created:
        messages_list.append(f"Created new venue: {venue_input}")

    # BULK CREATE ENTRIES
    entries_to_create = []
    alloc_ids = [alloc.id for alloc in merged_allocations]
    
    # Check existing entries in single query
    existing_entries = set(
        Timetable.objects.filter(
            course_allocation_id__in=alloc_ids, 
            day=day, 
            start_time=start_t, 
            end_time=end_t,
            venue=venue_obj
        ).values_list('course_allocation_id', flat=True)
    )

    for alloc in merged_allocations:
        if alloc.id not in existing_entries:
            entries_to_create.append(
                Timetable(
                    course_allocation=alloc,
                    venue=venue_obj,
                    day=day,
                    start_time=start_t,
                    end_time=end_t
                )
            )

    if entries_to_create:
        created_entries = Timetable.objects.bulk_create(entries_to_create)

        # If this was a multi-alloc merge, record the AutoMergedExamGroup and link
        # temp_timetable_entry to the Timetable row for the base (first) allocation.
        # The entry is unpublished (draft) until publish_to_main() is called.
        if len(merged_allocations) > 1:
            base_alloc = merged_allocations[0]
            # Find the TempTimetable entry for the base course at this slot
            temp_tt_entry = TempTimetable.objects.filter(
                course_allocation=base_alloc,
                day=day,
                start_time=start_t,
                end_time=end_t,
            ).first()
            # Also find the already-saved Timetable entry for the base course
            timetable_entry = Timetable.objects.filter(
                course_allocation=base_alloc,
                day=day,
                start_time=start_t,
                end_time=end_t,
            ).first()

            # Try to update an existing AutoMergedExamGroup for this merge code,
            # or create a new one.
            existing_group = AutoMergedExamGroup.objects.filter(
                merged_code__iexact=allocation.course_code,
                published=False,
            ).first()

            if existing_group:
                existing_group.base_course         = base_alloc
                existing_group.total_students      = sum(a.number_of_students or 0 for a in merged_allocations)
                existing_group.timetable_entry     = timetable_entry
                existing_group.temp_timetable_entry = temp_tt_entry
                existing_group.save(update_fields=[
                    'base_course', 'total_students',
                    'timetable_entry', 'temp_timetable_entry',
                ])
                existing_group.merged_courses.set(merged_allocations)
            else:
                new_group = AutoMergedExamGroup.objects.create(
                    base_course          = base_alloc,
                    merged_code          = allocation.course_code,
                    total_students       = sum(a.number_of_students or 0 for a in merged_allocations),
                    venue                = venue_obj,
                    start_time           = start_t,
                    end_time             = end_t,
                    published            = False,
                    timetable_entry      = timetable_entry,
                    temp_timetable_entry = temp_tt_entry,
                )
                new_group.merged_courses.set(merged_allocations)

        return JsonResponse({
            "status": "success",
            "messages": messages_list or ["Timetable entry created successfully."],
            "created_count": len(entries_to_create),
            "has_warnings": any("⚠️" in msg for msg in messages_list),
            # Every allocation that is now scheduled as part of this save —
            # not just the one picked in the form. Covers the auto-merge
            # (same course/lecturer) and forced CombinedCourseGroup paths,
            # where several allocations get booked together in one go.
            # The frontend uses this to drop ALL of them out of the
            # "unscheduled courses" select, not just the one submitted.
            "scheduled_allocation_ids": alloc_ids,
        })
    else:
        return JsonResponse({
            "status": "info",
            "messages": ["Timetable entry already exists."],
            "scheduled_allocation_ids": alloc_ids,
        })

def _get_conflicts_data(request):
    """Get conflicts data with error handling"""
    try:
        conflicts_response = timetable_conflicts_api(request)
        return json.loads(conflicts_response.content)
    except Exception:
        return {
            'program_conflicts': [],
            'lecturer_conflicts': [],
            'venue_conflicts': [],
            'total_conflicts': 0
        }


def _build_merged_exclude_ids(timetable_ids):
    """
    Returns the set of CourseAllocation IDs that should be considered
    scheduled because they belong to a published AutoMergedExamGroup
    (regular-timetable merge group) whose base course is placed.

    The three coverage conditions match what the autoscheduler guarantees:
      a) group.venue / start_time set directly (merged-UG batch phase), OR
      b) group.timetable_entry → Timetable row with venue+start_time (publish phase), OR
      c) base_course has a direct Timetable row (individual / PG / sweep / compress phases).

    IMPORTANT: merged_courses M2M may be sparsely populated if the scheduler
    only stored the base_course FK and never called group.merged_courses.set().
    Condition (c) — base_course_id__in=timetable_ids — is the safety net: once
    the base is published the non-base siblings are correctly excluded even when
    the M2M table is empty.
    """
    pub_auto_with_slot = AutoMergedExamGroup.objects.filter(
        published=True,
    ).filter(
        Q(venue__isnull=False, start_time__isnull=False) |
        Q(timetable_entry__venue__isnull=False, timetable_entry__start_time__isnull=False) |
        Q(base_course_id__in=timetable_ids)
    )
    auto_base    = set(pub_auto_with_slot.values_list('base_course_id', flat=True))
    auto_members = set(pub_auto_with_slot.values_list('merged_courses__id', flat=True))
    auto_members.discard(None)

    # Also exclude CombinedCourseGroup members whose primary allocation is scheduled.
    # These are courses from different departments that are taught together — once the
    # primary allocation has a timetable slot all the other members are implicitly placed.
    combined_exclude = _build_combined_group_exclude_ids()

    return auto_base | auto_members | combined_exclude


def _get_unscheduled_allocations():
    """
    Returns CourseAllocations that are genuinely unscheduled for the
    REGULAR (non-exam) timetable.

    A course is considered scheduled when:
      • it has a Timetable row with venue + timeslot, OR
      • it is a member (base or merged) of a published AutoMergedExamGroup
        whose base course has been placed (see _build_merged_exclude_ids).

    This queryset intentionally includes is_evening_weekend=True courses
    and zero-student courses — all are counted in unscheduled_count so the
    panel total reflects every outstanding allocation.

    Evening/Weekend courses are also returned by
    _get_unscheduled_evening_weekend_allocations() for their dedicated
    display section; zero-student courses are returned by
    _get_zero_student_allocations() for their section.  The counts for
    those sub-groups are derived from the same pool — no double-counting
    in the total.
    """
    # 1. Directly timetabled WITH venue AND timeslot
    timetable_ids = set(
        Timetable.objects.filter(
            venue__isnull=False,
            start_time__isnull=False,
            end_time__isnull=False,
        ).values_list('course_allocation_id', flat=True).distinct()
    )

    # 2. Exclude merged-group members whose base is placed
    merged_ids = _build_merged_exclude_ids(timetable_ids)

    all_exclude_ids = timetable_ids | merged_ids

    # NOTE: No filter on is_evening_weekend or number_of_students —
    # all outstanding allocations contribute to the unscheduled total.
    return (
        CourseAllocation.objects
        .select_related('department', 'department__faculty', 'lecturer', 'program')
        .exclude(id__in=all_exclude_ids)
        .filter(
            Q(department__submission_control__allow_submission_to_tt=True)
            | Q(department__submission_control__isnull=True)
        )
        .order_by('department__faculty__name', 'department__name', 'course_code')
    )


def _get_unscheduled_evening_weekend_allocations():
    """
    Returns CourseAllocations with is_evening_weekend=True that are not yet
    placed in any Timetable row with a venue + timeslot.
    These are shown in the Evening/Weekend section of the timetable panel.

    Applies the same merged-group exclusion as _get_unscheduled_allocations()
    so that evening/weekend courses that are base or members of a published
    AutoMergedExamGroup (and whose base is placed) are not shown as unscheduled.
    """
    timetable_ids = set(
        Timetable.objects.filter(
            venue__isnull=False,
            start_time__isnull=False,
            end_time__isnull=False,
        ).values_list('course_allocation_id', flat=True).distinct()
    )
    merged_ids = _build_merged_exclude_ids(timetable_ids)
    all_exclude_ids = timetable_ids | merged_ids
    return (
        CourseAllocation.objects
        .select_related('department', 'department__faculty', 'lecturer', 'program')
        .filter(is_evening_weekend=True)
        .exclude(id__in=all_exclude_ids)
        .filter(
            Q(department__submission_control__allow_submission_to_tt=True)
            | Q(department__submission_control__isnull=True)
        )
        .order_by('department__faculty__name', 'department__name', 'course_code')
    )


def _get_zero_student_allocations():
    """
    Returns CourseAllocations with 0 or null students.
    These are excluded from the regular unscheduled count and
    displayed in their own section in the timetable panel.
    """
    return (
        CourseAllocation.objects
        .select_related('department', 'department__faculty', 'lecturer', 'program')
        .filter(
            Q(number_of_students__isnull=True) | Q(number_of_students=0)
        )
        .filter(
            Q(department__submission_control__allow_submission_to_tt=True)
            | Q(department__submission_control__isnull=True)
        )
        .order_by('department__name', 'course_code')
    )


# -----------------------
# Resolve Remaining Unscheduled — auto-place every course still sitting in
# the left-hand "Select a course..." dropdown.
#
# PASS 1 (clean slot): for each unscheduled allocation (or CombinedCourseGroup,
# booked atomically), search every day / timeslot / venue combination for one
# that has NO collision at all — no venue double-booking, no lecturer
# double-booking, no program-year collision — honouring every exemption
# is_scheduling_exempt() / _are_in_same_combined_group() already grant manual
# saves (same base course split into sections, different specialization
# stems within one category, different explicit StudentGroups, electives /
# selection groups, shifted-semester special intakes, and
# CombinedCourseGroup members sharing one class).
#
# PASS 2 (last resort, only if PASS 1 found nothing): repeats the search but
# lets a program-year collision through as a flagged, recorded override —
# and ONLY that collision. The venue must still not be on an active
# VenueBlock, must not be `is_specialized` (unless this course is one of its
# designated courses), and the lecturer must still not be double-booked;
# venue double-booking is never allowed either, since the search simply
# keeps trying venues until it finds a genuinely empty one.
# -----------------------

def _build_time_slots(start_time, end_time, slot_hrs):
    """Return [(start_time, end_time), ...] as datetime.time tuples, stepping
    by slot_hrs from start_time up to (not exceeding) end_time."""
    slots = []
    cur = datetime.combine(datetime.today(), start_time)
    end = datetime.combine(datetime.today(), end_time)
    step = timedelta(hours=slot_hrs)
    while cur < end:
        nxt = cur + step
        if nxt > end:
            break
        slots.append((cur.time(), nxt.time()))
        cur = nxt
    return slots


class _ResolveState:
    """In-memory index of every booked Timetable slot, built once per resolve
    run and updated as new placements are made during the run — so course #2
    correctly sees the room/lecturer that course #1 just took, without a
    fresh DB round-trip for every candidate combination."""

    def __init__(self):
        self.by_day_venue = defaultdict(list)     # (day_lc, venue_code_lc) -> [(start,end,alloc)]
        self.by_day_lecturer = defaultdict(list)  # (day_lc, lecturer_id)   -> [(start,end,alloc,venue_code)]
        self.by_day_progyear = defaultdict(list)  # (day_lc, program_id, year) -> [(start,end,alloc)]

    def add(self, day, start_t, end_t, venue_code, alloc):
        day_lc = (day or "").lower()
        self.by_day_venue[(day_lc, (venue_code or "").lower())].append((start_t, end_t, alloc))
        if getattr(alloc, "lecturer_id", None):
            self.by_day_lecturer[(day_lc, alloc.lecturer_id)].append((start_t, end_t, alloc, venue_code))
        year = _get_year_value(alloc)
        if getattr(alloc, "program_id", None) and year is not None:
            self.by_day_progyear[(day_lc, alloc.program_id, year)].append((start_t, end_t, alloc))


def _check_single_placement(alloc, state, day, start_t, end_t, venue_code, allow_program_year_override):
    """
    Test whether `alloc` can sit at (day, start_t, end_t, venue_code) given
    everything already in `state`. Returns (ok, used_override).
    """
    day_lc = day.lower()
    alloc_key = course_base_key(getattr(alloc, "course_code", None))

    # ── lecturer blocked day/time: HARD constraint, checked first so the
    # auto-resolver / "Resolve Unscheduled" pass never proposes a slot a
    # lecturer has explicitly marked unavailable, the same way it already
    # never proposes a room/lecturer double-booking. ───────────────────────
    lecturer = getattr(alloc, "lecturer", None)
    if lecturer and lecturer_blocked_slot_hit(lecturer, day, start_t, end_t):
        return False, False

    # ── venue: never allowed to double-book a room, barring the
    # same-base-course exemption (split sections sharing a room) or a
    # CombinedCourseGroup sharing its own venue. ──────────────────────────
    for (s, e, other) in state.by_day_venue.get((day_lc, venue_code.lower()), []):
        if not time_overlaps(start_t, end_t, s, e):
            continue
        if _are_in_same_combined_group(alloc.id, other.id):
            continue
        # `other` already belongs to a DIFFERENT combined group than `alloc`
        # — never let the same-base-course-code exemption below paper over
        # that; that would merge two distinct combined groups into one class.
        if _other_belongs_to_a_different_combined_group(alloc.id, other.id):
            return False, False
        other_key = course_base_key(getattr(other, "course_code", None))
        if alloc_key is not None and alloc_key == other_key:
            continue
        return False, False

    # ── lecturer: always strict, in both passes — a lecturer can never
    # physically be in two rooms at once. ─────────────────────────────────
    if getattr(alloc, "lecturer_id", None):
        for (s, e, other, other_venue) in state.by_day_lecturer.get((day_lc, alloc.lecturer_id), []):
            if not time_overlaps(start_t, end_t, s, e):
                continue
            if _are_in_same_combined_group(alloc.id, other.id):
                continue
            # Same reasoning as the venue check above: a different combined
            # group is a hard collision, never exempted by a shared base
            # course code.
            if _other_belongs_to_a_different_combined_group(alloc.id, other.id):
                return False, False
            other_key = course_base_key(getattr(other, "course_code", None))
            same_course = alloc_key is not None and alloc_key == other_key
            same_venue = (other_venue or "").strip().lower() == venue_code.strip().lower()
            if same_course and same_venue:
                continue
            return False, False

    # ── program-year: exempt pairs pass silently; a genuine collision
    # blocks PASS 1, but is let through (flagged) in PASS 2. ─────────────
    used_override = False
    year = _get_year_value(alloc)
    if getattr(alloc, "program_id", None) and year is not None:
        for (s, e, other) in state.by_day_progyear.get((day_lc, alloc.program_id, year), []):
            if not time_overlaps(start_t, end_t, s, e):
                continue
            if is_scheduling_exempt(alloc, other):
                continue
            if allow_program_year_override:
                used_override = True
                continue
            return False, False

    return True, used_override


def _find_free_slot_for_members(members, state, days, slot_pairs, venues_ordered, allow_program_year_override):
    """
    Search day x slot x venue (in that priority order) for a placement valid
    for EVERY member of `members` at once (a CombinedCourseGroup is booked
    as one atomic unit — same rule the manual Save Entry flow enforces).
    Returns (day, start_t, end_t, venue_code, used_override) or None.
    """
    for day in days:
        for start_t, end_t in slot_pairs:
            for venue in venues_ordered:
                any_override = False
                all_ok = True
                for m in members:
                    ok, used_override = _check_single_placement(
                        m, state, day, start_t, end_t, venue.code, allow_program_year_override
                    )
                    if not ok:
                        all_ok = False
                        break
                    any_override = any_override or used_override
                if all_ok:
                    return day, start_t, end_t, venue.code, any_override
    return None


def _alloc_label(alloc, group_label=None):
    if group_label:
        return group_label
    name = getattr(alloc, "course_name", None)
    return f"{alloc.course_code} – {name}" if name else alloc.course_code


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def resolve_unscheduled_courses(request):
    """
    AJAX endpoint behind the left-panel "Resolve Remaining Unscheduled"
    button. See the module comment above for the two-pass search strategy.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "messages": ["POST required."]}, status=405)

    config = SchedulerConfig.objects.first()
    if not config:
        return JsonResponse({"status": "error", "messages": ["Scheduler is not configured yet."]}, status=400)

    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    weekend_days = ["Saturday"] if config.enable_weekend_classes else []

    regular_slots = _build_time_slots(config.start_time, config.end_time, config.slot_size)

    evening_slots = []
    if config.enable_evening_classes:
        from datetime import time as _time
        ev_start = getattr(config, "evening_start_time", _time(19, 0))
        ev_end = getattr(config, "evening_end_time", _time(21, 0))
        ev_count = int(getattr(config, "evening_slot_count", 1))
        evening_slots = _build_time_slots(ev_start, ev_end, config.slot_size)[:ev_count]

    weekend_slots = []
    if config.enable_weekend_classes:
        from datetime import time as _time
        wk_start = getattr(config, "weekend_start_time", _time(9, 0))
        wk_end = getattr(config, "weekend_end_time", _time(17, 0))
        wk_slot_size = int(getattr(config, "weekend_slot_size", 3))
        weekend_slots = _build_time_slots(wk_start, wk_end, wk_slot_size)

    # ── Venue candidate pool — shared across every allocation this run ────
    blocked_codes = set(
        VenueBlock.objects.filter(is_active=True).values_list("venue__code", flat=True)
    )
    all_venues = list(Venue.objects.exclude(code__in=blocked_codes))
    specialized_venues = [v for v in all_venues if v.is_specialized]
    specialized_codes = {v.code for v in specialized_venues}
    designated_keys_by_venue = {
        v.code: {course_base_key(c) for c in v.get_designated_course_codes()}
        for v in specialized_venues
    }

    def usable_venues_for(alloc):
        key = course_base_key(alloc.course_code)
        pool = [
            v for v in all_venues
            if v.code not in specialized_codes or key in designated_keys_by_venue.get(v.code, set())
        ]
        students = alloc.number_of_students or 0

        def sort_key(v):
            if v.capacity is None:
                return (1, 0)
            if v.capacity >= students:
                return (0, v.capacity)       # smallest sufficient room first
            return (2, -v.capacity)          # too small — least preferred

        return sorted(pool, key=sort_key)

    # ── Preload every existing booking into fast in-memory indices ────────
    state = _ResolveState()
    for t in Timetable.objects.select_related(
        "course_allocation", "course_allocation__lecturer",
        "course_allocation__program", "course_allocation__selection_group",
        "course_allocation__specialization_stem", "course_allocation__program_course",
        "venue",
    ).prefetch_related(
        "course_allocation__specialization_stems"
    ).filter(venue__isnull=False, start_time__isnull=False, end_time__isnull=False):
        state.add(t.day, t.start_time, t.end_time, t.venue.code, t.course_allocation)

    # ── Build the work list: one item per allocation OR CombinedCourseGroup ─
    unscheduled = list(
        _get_unscheduled_allocations().select_related(
            "lecturer", "program", "selection_group",
            "specialization_stem", "program_course",
        ).prefetch_related("specialization_stems")
    )
    seen_group_ids = set()
    work_items = []
    for alloc in unscheduled:
        group, members = _get_combined_group_for_allocation(alloc)
        if group:
            if group.id in seen_group_ids:
                continue
            seen_group_ids.add(group.id)
            work_items.append((alloc, members, group.display_name()))
        else:
            work_items.append((alloc, [alloc], None))

    resolved, still_unresolved, warnings = [], [], []

    for representative, members, group_label in work_items:
        is_evening_weekend = bool(getattr(representative, "is_evening_weekend", False))
        days, slot_pairs = list(weekdays), list(regular_slots)
        if is_evening_weekend:
            ew_days, ew_slots = [], []
            if evening_slots:
                ew_days += weekdays
                ew_slots += evening_slots
            if weekend_slots:
                ew_days = list(dict.fromkeys(ew_days + weekend_days))
                ew_slots += weekend_slots
            # Only switch to the evening/weekend pool if one is actually
            # enabled — otherwise fall back to regular hours rather than
            # silently refusing to place the course at all.
            if ew_slots:
                days, slot_pairs = ew_days, ew_slots

        venues_ordered = usable_venues_for(representative)
        if not venues_ordered:
            still_unresolved.append(_alloc_label(representative, group_label))
            continue

        placement = _find_free_slot_for_members(
            members, state, days, slot_pairs, venues_ordered, allow_program_year_override=False
        )
        if not placement:
            placement = _find_free_slot_for_members(
                members, state, days, slot_pairs, venues_ordered, allow_program_year_override=True
            )

        if not placement:
            still_unresolved.append(_alloc_label(representative, group_label))
            continue

        day, start_t, end_t, venue_code, used_override = placement

        messages_list = []
        if len(members) > 1:
            _save_timetable_entries(
                representative, venue_code, day, start_t, end_t, messages_list,
                forced_members=members, group_display_name=group_label,
            )
        else:
            _save_timetable_entries(representative, venue_code, day, start_t, end_t, messages_list)

        # Record it locally too, so later allocations in this same run treat
        # this room/lecturer/slot as occupied without hitting the DB again.
        for m in members:
            state.add(day, start_t, end_t, venue_code, m)

        label = _alloc_label(representative, group_label)
        time_label = f"{start_t.strftime('%H:%M')}-{end_t.strftime('%H:%M')}"
        resolved.append({
            "label": label, "day": day, "time": time_label,
            "venue": venue_code, "overridden": used_override,
        })
        if used_override:
            warnings.append(
                f"⚠️ {label} placed at {day} {time_label} in {venue_code} "
                f"despite a program-year collision — no clean slot was available "
                f"anywhere else."
            )

    total = len(resolved) + len(still_unresolved)
    summary = [f"Resolved {len(resolved)} of {total} unscheduled course(s)."]
    if still_unresolved:
        summary.append(
            f"{len(still_unresolved)} could not be placed anywhere without a "
            f"lecturer clash or an unavoidable room double-booking."
        )

    return JsonResponse({
        "status": "success" if resolved else "info",
        "resolved": resolved,
        "unresolved": still_unresolved,
        "resolved_count": len(resolved),
        "unresolved_count": len(still_unresolved),
        "warnings": warnings,
        "messages": summary,
    })


# -----------------------
# Resolve Collisions (program-year + lecturer) — PROPOSAL / COMMIT flow
#
# This is the "Option 2" half of the single Resolve button: after
# resolve_unscheduled_courses has already run (and already committed its
# placements), the frontend calls propose_collision_resolutions to scan the
# CURRENT timetable for genuine lecturer and program-year collisions and try
# to fix each one by moving — or, failing that, swapping — one of the
# colliding entries to a different day/timeslot (and venue, if needed).
#
# Nothing is written to the DB here. Each fix becomes a self-contained
# "proposal" (a list of {timetable_id, from, to} changes) sent back to the
# browser so the user can review them one by one and choose which to keep.
# commit_collision_resolutions then re-validates and applies exactly the
# proposals the user confirmed — every change inside one proposal is applied
# together, in one transaction, or not at all.
# -----------------------

def _build_full_state(exclude_ids=None):
    """Fresh _ResolveState built from every current Timetable row, skipping
    any row id in `exclude_ids` (used to “lift” an entry out of the grid
    before searching for somewhere new to put it)."""
    exclude_ids = exclude_ids or set()
    state = _ResolveState()
    rows = Timetable.objects.select_related(
        "course_allocation", "course_allocation__lecturer",
        "course_allocation__program", "course_allocation__selection_group",
        "course_allocation__specialization_stem", "course_allocation__program_course",
        "venue",
    ).prefetch_related(
        "course_allocation__specialization_stems"
    ).filter(venue__isnull=False, start_time__isnull=False, end_time__isnull=False)
    for t in rows:
        if t.id in exclude_ids:
            continue
        state.add(t.day, t.start_time, t.end_time, t.venue.code, t.course_allocation)
    return state


def _day_slot_pools(config):
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    weekend_days = ["Saturday"] if config.enable_weekend_classes else []
    regular_slots = _build_time_slots(config.start_time, config.end_time, config.slot_size)

    evening_slots = []
    if config.enable_evening_classes:
        from datetime import time as _time
        ev_start = getattr(config, "evening_start_time", _time(19, 0))
        ev_end = getattr(config, "evening_end_time", _time(21, 0))
        ev_count = int(getattr(config, "evening_slot_count", 1))
        evening_slots = _build_time_slots(ev_start, ev_end, config.slot_size)[:ev_count]

    weekend_slots = []
    if config.enable_weekend_classes:
        from datetime import time as _time
        wk_start = getattr(config, "weekend_start_time", _time(9, 0))
        wk_end = getattr(config, "weekend_end_time", _time(17, 0))
        wk_slot_size = int(getattr(config, "weekend_slot_size", 3))
        weekend_slots = _build_time_slots(wk_start, wk_end, wk_slot_size)

    return weekdays, weekend_days, regular_slots, evening_slots, weekend_slots


def _days_slots_for_alloc(alloc, weekdays, weekend_days, regular_slots, evening_slots, weekend_slots):
    is_evening_weekend = bool(getattr(alloc, "is_evening_weekend", False))
    days, slot_pairs = list(weekdays), list(regular_slots)
    if is_evening_weekend:
        ew_days, ew_slots = [], []
        if evening_slots:
            ew_days += weekdays
            ew_slots += evening_slots
        if weekend_slots:
            ew_days = list(dict.fromkeys(ew_days + weekend_days))
            ew_slots += weekend_slots
        if ew_slots:
            days, slot_pairs = ew_days, ew_slots
    return days, slot_pairs


def _venue_pool(all_venues, specialized_codes, designated_keys_by_venue, alloc):
    key = course_base_key(alloc.course_code)
    pool = [
        v for v in all_venues
        if v.code not in specialized_codes or key in designated_keys_by_venue.get(v.code, set())
    ]
    students = alloc.number_of_students or 0

    def sort_key(v):
        if v.capacity is None:
            return (1, 0)
        if v.capacity >= students:
            return (0, v.capacity)
        return (2, -v.capacity)

    return sorted(pool, key=sort_key)


def _timetable_label(tt):
    ca = tt.course_allocation
    code = getattr(ca, "course_code", None) if ca else None
    name = getattr(ca, "course_name", None) if ca else None
    label = f"{code} – {name}" if code and name else (code or f"Entry #{tt.id}")
    lect = getattr(ca, "lecturer", None) if ca else None
    lect_name = getattr(lect, "display_name", None) or getattr(lect, "name", None) if lect else None
    return label, lect_name


def _entry_snapshot(tt):
    return {
        "day": tt.day,
        "start": tt.start_time.strftime("%H:%M") if tt.start_time else None,
        "end": tt.end_time.strftime("%H:%M") if tt.end_time else None,
        "venue": tt.venue.code if tt.venue else None,
    }


def _find_alt_slot_for_row(cand, day_pools, all_venues, specialized_codes, designated_keys_by_venue):
    """Try to find a genuinely clean (never overridden) day/slot/venue for
    the group of Timetable rows tied to `cand`'s allocation — combined-group
    aware, so a merged class always moves as one unit. Returns a list of
    {timetable_id, label, from, to} changes, or None if nothing free exists."""
    alloc = cand.course_allocation
    if not alloc:
        return None
    group, members = _get_combined_group_for_allocation(alloc)
    member_ids = {m.id for m in members} if members else {alloc.id}

    sibling_rows = list(Timetable.objects.select_related(
        "venue", "course_allocation", "course_allocation__lecturer", "course_allocation__program"
    ).filter(
        course_allocation_id__in=member_ids, day=cand.day,
        start_time=cand.start_time, end_time=cand.end_time,
    ))
    if not sibling_rows:
        sibling_rows = [cand]
    sibling_ids = {r.id for r in sibling_rows}

    weekdays, weekend_days, regular_slots, evening_slots, weekend_slots = day_pools
    days, slot_pairs = _days_slots_for_alloc(alloc, weekdays, weekend_days, regular_slots, evening_slots, weekend_slots)
    venues_ordered = _venue_pool(all_venues, specialized_codes, designated_keys_by_venue, alloc)
    if not venues_ordered:
        return None

    tmp_state = _build_full_state(exclude_ids=sibling_ids)
    placement = _find_free_slot_for_members(members, tmp_state, days, slot_pairs, venues_ordered, allow_program_year_override=False)
    if not placement:
        return None
    day, start_t, end_t, venue_code, _ = placement

    changes = []
    for row in sibling_rows:
        label, _lect = _timetable_label(row)
        changes.append({
            "timetable_id": row.id,
            "label": label,
            "from": _entry_snapshot(row),
            "to": {
                "day": day,
                "start": start_t.strftime("%H:%M"),
                "end": end_t.strftime("%H:%M"),
                "venue": venue_code,
            },
        })
    return changes


def _try_swap_for_row(cand, partner_pool):
    """Last-resort fallback when no free slot exists anywhere: look for one
    already-scheduled row (`partner_pool`, pre-narrowed for relevance) whose
    day/slot `cand` could trade places with, such that BOTH end up
    collision-free. Returns a list of two {timetable_id, label, from, to}
    changes, or None."""
    cand_alloc = cand.course_allocation
    if not cand_alloc:
        return None
    tried = 0
    for partner in partner_pool:
        if partner.id == cand.id or not partner.course_allocation:
            continue
        if partner.day == cand.day and partner.start_time == cand.start_time and partner.end_time == cand.end_time:
            continue  # trading identical slots achieves nothing
        tried += 1
        if tried > 150:
            break
        tmp_state = _build_full_state(exclude_ids={cand.id, partner.id})
        ok1, _ = _check_single_placement(
            cand_alloc, tmp_state, partner.day, partner.start_time, partner.end_time,
            partner.venue.code, allow_program_year_override=False,
        )
        if not ok1:
            continue
        tmp_state.add(partner.day, partner.start_time, partner.end_time, partner.venue.code, cand_alloc)
        ok2, _ = _check_single_placement(
            partner.course_allocation, tmp_state, cand.day, cand.start_time, cand.end_time,
            cand.venue.code, allow_program_year_override=False,
        )
        if not ok2:
            continue

        cand_label, _l1 = _timetable_label(cand)
        partner_label, _l2 = _timetable_label(partner)
        return [
            {
                "timetable_id": cand.id, "label": cand_label,
                "from": _entry_snapshot(cand),
                "to": _entry_snapshot(partner),
            },
            {
                "timetable_id": partner.id, "label": partner_label,
                "from": _entry_snapshot(partner),
                "to": _entry_snapshot(cand),
            },
        ]
    return None


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def propose_collision_resolutions(request):
    """
    AJAX endpoint behind Option 2 of the Resolve button, run AFTER
    resolve_unscheduled_courses. Scans the current timetable for genuine
    lecturer and program-year collisions and returns a list of proposed
    fixes — nothing is saved yet. See module comment above.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "messages": ["POST required."]}, status=405)

    config = SchedulerConfig.objects.first()
    if not config:
        return JsonResponse({"status": "error", "messages": ["Scheduler is not configured yet."]}, status=400)

    blocked_codes = set(VenueBlock.objects.filter(is_active=True).values_list("venue__code", flat=True))
    all_venues = list(Venue.objects.exclude(code__in=blocked_codes))
    specialized_venues = [v for v in all_venues if v.is_specialized]
    specialized_codes = {v.code for v in specialized_venues}
    designated_keys_by_venue = {
        v.code: {course_base_key(c) for c in v.get_designated_course_codes()}
        for v in specialized_venues
    }
    day_pools = _day_slot_pools(config)

    all_rows = list(Timetable.objects.select_related(
        "course_allocation", "course_allocation__lecturer", "course_allocation__program",
        "course_allocation__selection_group", "course_allocation__specialization_stem",
        "course_allocation__program_course", "venue",
    ).prefetch_related("course_allocation__specialization_stems").filter(
        venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
    ))

    buckets = defaultdict(list)
    for tt in all_rows:
        buckets[(tt.day or "", tt.start_time, tt.end_time)].append(tt)

    conflict_groups = []  # [(conflict_type, [rows])]

    for _key, entries in buckets.items():
        if len(entries) < 2:
            continue

        # ── lecturer collisions ────────────────────────────────────────
        lect_groups = defaultdict(list)
        for e in entries:
            lect = getattr(e.course_allocation, "lecturer", None) if e.course_allocation else None
            if lect:
                lect_groups[lect.id].append(e)
        for _lid, l_entries in lect_groups.items():
            if len(l_entries) < 2:
                continue
            real = []
            for i, ei in enumerate(l_entries):
                for j, ej in enumerate(l_entries):
                    if j <= i:
                        continue
                    if _are_in_same_combined_group(ei.course_allocation.id, ej.course_allocation.id):
                        continue
                    cc_i = course_base_key(getattr(ei.course_allocation, "course_code", None))
                    cc_j = course_base_key(getattr(ej.course_allocation, "course_code", None))
                    same_course = cc_i is not None and cc_i == cc_j
                    v_i = ei.venue.code if ei.venue else None
                    v_j = ej.venue.code if ej.venue else None
                    same_venue = v_i and v_j and v_i.strip().lower() == v_j.strip().lower()
                    if same_course and same_venue:
                        continue
                    real += [ei, ej]
            seen = set()
            real = [e for e in real if e.id not in seen and not seen.add(e.id)]
            if len(real) >= 2:
                conflict_groups.append(("lecturer", real))

        # ── program-year collisions ────────────────────────────────────
        prog_groups = defaultdict(list)
        for e in entries:
            if not e.course_allocation:
                continue
            prog = getattr(e.course_allocation, "program", None)
            if not prog:
                continue
            year = _get_year_value(e.course_allocation)
            if year:
                prog_groups[(prog.id, year)].append(e)
        for _pkey, p_entries in prog_groups.items():
            if len(p_entries) < 2:
                continue
            real_ids = set()
            for i, ei in enumerate(p_entries):
                for j, ej in enumerate(p_entries):
                    if j <= i:
                        continue
                    if not is_scheduling_exempt(ei.course_allocation, ej.course_allocation):
                        if _are_in_same_combined_group(ei.course_allocation.id, ej.course_allocation.id):
                            continue
                        real_ids.add(ei.id)
                        real_ids.add(ej.id)
            real_entries = [e for e in p_entries if e.id in real_ids]
            if len(real_entries) >= 2:
                conflict_groups.append(("program_year", real_entries))

    proposals = []
    unresolved_conflicts = []
    already_targeted_alloc_ids = set()

    for conflict_type, rows in conflict_groups:
        candidates = sorted(rows, key=lambda r: -r.id)  # prefer moving the newer entry first
        found = False

        for cand in candidates:
            if not cand.course_allocation or cand.course_allocation.id in already_targeted_alloc_ids:
                continue
            changes = _find_alt_slot_for_row(cand, day_pools, all_venues, specialized_codes, designated_keys_by_venue)
            if not changes:
                continue
            other = [r for r in rows if r.id != cand.id]
            other_label, _ol = _timetable_label(other[0]) if other else ("another course", None)
            mover_label, lect_name = _timetable_label(cand)
            to, frm = changes[0]["to"], changes[0]["from"]
            reason = f"a lecturer clash with {other_label}" if conflict_type == "lecturer" else f"a program-year collision with {other_label}"
            who = f" ({lect_name})" if conflict_type == "lecturer" and lect_name else ""
            desc = (
                f"Move {mover_label}{who} from {frm['day']} {frm['start']}-{frm['end']} in {frm['venue']} "
                f"to {to['day']} {to['start']}-{to['end']} in {to['venue']} to resolve {reason}."
            )
            proposals.append({
                "id": f"p{len(proposals) + 1}",
                "type": "move",
                "conflict_type": conflict_type,
                "description": desc,
                "changes": changes,
            })
            already_targeted_alloc_ids.add(cand.course_allocation.id)
            found = True
            break

        if found:
            continue

        # No free slot anywhere for either side — try a swap instead. Bias
        # the partner pool toward rows that are actually relevant (the same
        # lecturer's other classes, or the same program-year's other
        # classes) before giving up.
        for cand in candidates:
            if not cand.course_allocation or cand.course_allocation.id in already_targeted_alloc_ids:
                continue
            alloc = cand.course_allocation
            partner_pool = []
            if getattr(alloc, "lecturer_id", None):
                partner_pool += list(Timetable.objects.select_related(
                    "venue", "course_allocation", "course_allocation__lecturer", "course_allocation__program"
                ).filter(course_allocation__lecturer_id=alloc.lecturer_id, venue__isnull=False).exclude(id__in=[r.id for r in rows]))
            year = _get_year_value(alloc)
            if getattr(alloc, "program_id", None) and year is not None:
                partner_pool += list(Timetable.objects.select_related(
                    "venue", "course_allocation", "course_allocation__lecturer", "course_allocation__program"
                ).filter(
                    course_allocation__program_id=alloc.program_id, venue__isnull=False
                ).exclude(id__in=[r.id for r in rows]))
            changes = _try_swap_for_row(cand, partner_pool)
            if not changes:
                continue
            a_from, a_to = changes[0]["from"], changes[0]["to"]
            b_label = changes[1]["label"]
            mover_label, lect_name = _timetable_label(cand)
            reason = "a lecturer clash" if conflict_type == "lecturer" else "a program-year collision"
            desc = (
                f"Swap {mover_label} ({a_from['day']} {a_from['start']}-{a_from['end']} in {a_from['venue']}) "
                f"with {b_label} ({a_to['day']} {a_to['start']}-{a_to['end']} in {a_to['venue']}) to resolve {reason}."
            )
            proposals.append({
                "id": f"p{len(proposals) + 1}",
                "type": "swap",
                "conflict_type": conflict_type,
                "description": desc,
                "changes": changes,
            })
            already_targeted_alloc_ids.add(cand.course_allocation.id)
            found = True
            break

        if not found:
            labels = ", ".join(_timetable_label(r)[0] for r in rows)
            unresolved_conflicts.append(f"{conflict_type.replace('_', '-')} collision between {labels} — no free slot or safe swap found.")

    return JsonResponse({
        "status": "success" if proposals else "info",
        "proposals": proposals,
        "unresolved_conflicts": unresolved_conflicts,
        "messages": [
            f"Found {len(proposals)} possible fix(es) for {len(conflict_groups)} collision(s)."
            if conflict_groups else "No lecturer or program-year collisions found."
        ],
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def commit_collision_resolutions(request):
    """
    AJAX endpoint that actually applies the proposals the user confirmed.
    Every change inside one proposal (a move, or both halves of a swap) is
    re-validated against the CURRENT database and applied together in one
    transaction — or not applied at all if the slot is no longer free.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "messages": ["POST required."]}, status=405)

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"status": "error", "messages": ["Invalid payload."]}, status=400)

    proposals = body.get("proposals") or []
    if not proposals:
        return JsonResponse({"status": "error", "messages": ["No proposals to commit."]}, status=400)

    applied, failed = [], []

    for p in proposals:
        pid = p.get("id", "?")
        desc = p.get("description", "")
        changes = p.get("changes") or []
        if not changes:
            failed.append({"id": pid, "description": desc, "reason": "Empty proposal."})
            continue
        try:
            with transaction.atomic():
                row_ids = [c["timetable_id"] for c in changes]
                rows = {
                    r.id: r for r in Timetable.objects.select_for_update().select_related(
                        "course_allocation", "course_allocation__lecturer", "course_allocation__program", "venue"
                    ).filter(id__in=row_ids)
                }
                if len(rows) != len(row_ids):
                    raise ValueError("One or more entries in this proposal no longer exist — it may already have been resolved.")

                fresh_state = _build_full_state(exclude_ids=set(row_ids))
                venue_cache = {}

                def get_venue(code):
                    if code not in venue_cache:
                        venue_cache[code] = Venue.objects.filter(code=code).first()
                    return venue_cache[code]

                validated = []
                for c in changes:
                    row = rows[c["timetable_id"]]
                    to = c["to"]
                    v = get_venue(to["venue"])
                    if not v:
                        raise ValueError(f"Venue {to['venue']} no longer exists.")
                    start_t = datetime.strptime(to["start"], "%H:%M").time()
                    end_t = datetime.strptime(to["end"], "%H:%M").time()
                    ok, _ = _check_single_placement(
                        row.course_allocation, fresh_state, to["day"], start_t, end_t, v.code,
                        allow_program_year_override=False,
                    )
                    if not ok:
                        raise ValueError(f"{c.get('label', 'This entry')} can no longer move there — the slot is no longer free.")
                    fresh_state.add(to["day"], start_t, end_t, v.code, row.course_allocation)
                    validated.append((row, to["day"], start_t, end_t, v))

                for row, day, start_t, end_t, v in validated:
                    row.day = day
                    row.start_time = start_t
                    row.end_time = end_t
                    row.venue = v
                    row.save(update_fields=["day", "start_time", "end_time", "venue"])

            applied.append({"id": pid, "description": desc})
        except Exception as ex:
            failed.append({"id": pid, "description": desc, "reason": str(ex)})

    return JsonResponse({
        "status": "success" if applied else "info",
        "applied": applied,
        "failed": failed,
        "messages": [f"Committed {len(applied)} of {len(proposals)} proposal(s)."],
    })


# -----------------------
# Main View - UPDATED TO FIX VENUE ISSUE
# -----------------------
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def timetable_panel(request):
    """
    Timetable Panel - Single page that loads immediately, shows progress, 
    then reveals workspace when 60-70% loaded
    """
    # ---------- CONFIG ----------
    config = SchedulerConfig.objects.first()  # load all fields including evening/weekend
    if not config:
        config = SchedulerConfig.objects.create()

    # ---------- RESET FORM SUBMIT ----------
    if request.method == "POST" and "reset_form" in request.POST:
        start_time = request.POST.get("start_time")
        end_time = request.POST.get("end_time")
        slot_size = request.POST.get("slot_size")

        if start_time and end_time and slot_size:
            try:
                config.start_time = datetime.strptime(start_time, "%H:%M").time()
                config.end_time = datetime.strptime(end_time, "%H:%M").time()
                config.slot_size = int(slot_size)
                config.save()
                messages.success(request, "Scheduler configuration updated successfully!")
            except Exception as e:
                messages.error(request, f"Invalid input: {e}")
        return redirect("timetable_panel")

    # ---------- DEFAULT CONFIG VALUES ----------
    default_start = config.start_time.strftime("%H:%M")
    default_end = config.end_time.strftime("%H:%M")
    default_slot = config.slot_size

    slot_start = request.GET.get("slot_start", default_start)
    slot_end = request.GET.get("slot_end", default_end)
    slot_interval = int(request.GET.get("slot_interval", default_slot))

    # ---------- TIME SLOTS GENERATION ----------
    start_dt = datetime.strptime(slot_start, "%H:%M")
    end_dt = datetime.strptime(slot_end, "%H:%M")

    time_slots = []
    slot_map = {}
    current = start_dt
    while current < end_dt:
        next_time = current + timedelta(hours=slot_interval)
        start_str = current.strftime("%H:%M")
        end_str = next_time.strftime("%H:%M")
        slot_label = f"{current.strftime('%I:%M%p')}-{next_time.strftime('%I:%M%p')}"
        time_slots.append((start_str, slot_label))
        slot_map[start_str] = end_str
        current = next_time

    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    # ── Evening slot list for the template ────────────────────────────────────
    def _build_slot_list(start_time, end_time, slot_hrs):
        """Return [(start_str, label), ...] for template rendering."""
        from datetime import datetime as _dt, timedelta as _td
        slots = []
        cur = _dt.combine(_dt.today(), start_time)
        end = _dt.combine(_dt.today(), end_time)
        while cur < end:
            nxt = cur + _td(hours=slot_hrs)
            if nxt > end:
                nxt = end
            s = cur.strftime("%H:%M")
            lbl = f"{cur.strftime('%I:%M%p')}-{nxt.strftime('%I:%M%p')}"
            slots.append((s, lbl))
            cur = nxt
        return slots

    # Evening slots (if enabled)
    evening_slots = []
    if config.enable_evening_classes:
        from datetime import time as _time
        ev_start = getattr(config, 'evening_start_time', _time(19, 0))
        ev_end   = getattr(config, 'evening_end_time',   _time(21, 0))
        ev_count = int(getattr(config, 'evening_slot_count', 1))
        ev_raw   = _build_slot_list(ev_start, ev_end, config.slot_size)
        evening_slots = ev_raw[:ev_count]

    # Weekend slots (if enabled)
    weekend_slots = []
    weekend_days  = []
    if config.enable_weekend_classes:
        from datetime import time as _time
        wk_start     = getattr(config, 'weekend_start_time', _time(9, 0))
        wk_end       = getattr(config, 'weekend_end_time',   _time(17, 0))
        wk_slot_size = int(getattr(config, 'weekend_slot_size', 3))
        weekend_slots = _build_slot_list(wk_start, wk_end, wk_slot_size)
        weekend_days  = ["Saturday"]

    # ---------- AJAX: SAVE TIMETABLE ENTRY ----------
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        return _handle_ajax_timetable_save(request, slot_map)

    # ---------- GET ALL VENUES FOR TIMETABLE RENDERING ----------
    # CRITICAL FIX: Get ALL venues for timetable rendering
    venue_ids_from_timetables = Timetable.objects.filter(
        venue__isnull=False
    ).values_list('venue_id', flat=True).distinct()
    
    # Get venues from timetables
    venues_from_timetables = Venue.objects.filter(
        id__in=venue_ids_from_timetables
    ).only('code', 'capacity')
    
    # Get venues with capacity defined
    venues_with_capacity = Venue.objects.filter(
        capacity__isnull=False
    ).only('code', 'capacity')
    
    # Get ALL venues from the Venue table
    all_venues_from_db = Venue.objects.all().only('code', 'capacity')

    # ── Blocked / designated venue codes (for row highlighting) ────────────
    # Blocked  = has an ACTIVE VenueBlock rule (hidden from the autoscheduler).
    # Designated = either the venue's own is_specialized flag is set, or it
    # has at least one VenueSpecialization rule reserving it for courses.
    blocked_venue_codes = set(
        VenueBlock.objects.filter(is_active=True).values_list('venue__code', flat=True)
    )
    designated_venue_codes = set(
        Venue.objects.filter(is_specialized=True).values_list('code', flat=True)
    ) | set(
        VenueSpecialization.objects.values_list('venues__code', flat=True)
    )
    designated_venue_codes.discard(None)
    
    # Combine and deduplicate
    venue_dict = {}
    
    # Add venues from timetables first
    for venue in venues_from_timetables:
        venue_dict[venue.code] = {
            'code': venue.code,
            'capacity': venue.capacity or '—',
            'is_blocked': venue.code in blocked_venue_codes,
            'is_designated': venue.code in designated_venue_codes,
        }
    
    # Add venues with capacity
    for venue in venues_with_capacity:
        if venue.code not in venue_dict:
            venue_dict[venue.code] = {
                'code': venue.code,
                'capacity': venue.capacity or '—',
                'is_blocked': venue.code in blocked_venue_codes,
                'is_designated': venue.code in designated_venue_codes,
            }
    
    # Add all venues from DB
    for venue in all_venues_from_db:
        if venue.code not in venue_dict:
            venue_dict[venue.code] = {
                'code': venue.code,
                'capacity': venue.capacity or '—',
                'is_blocked': venue.code in blocked_venue_codes,
                'is_designated': venue.code in designated_venue_codes,
            }
    
    # Also get any venue codes that might be in timetables but not in Venue table
    timetable_venue_codes = set(
        Timetable.objects.filter(venue__isnull=False)
        .values_list('venue__code', flat=True)
        .distinct()
    )
    for venue_code in timetable_venue_codes:
        if venue_code and venue_code not in venue_dict:
            venue_dict[venue_code] = {
                'code': venue_code,
                'capacity': '—',
                'is_blocked': venue_code in blocked_venue_codes,
                'is_designated': venue_code in designated_venue_codes,
            }
    
    existing_venues = sorted(venue_dict.values(), key=lambda x: x['code'].lower())
    print(f"DEBUG: Loaded {len(existing_venues)} venues for timetable display")
    
    # ---------- INITIAL CONTEXT ----------
    context = {
        "heading": "Timetable Management Panel",
        "time_slots": time_slots,
        "slot_start": slot_start,
        "slot_end": slot_end,
        "slot_interval": slot_interval,
        "weekdays": weekdays,
        "default_start": default_start,
        "default_end": default_end,
        "default_slot": default_slot,
        "existing_venues": existing_venues,  # Now includes ALL venues
        "config": config,
        "evening_slots": evening_slots,
        "weekend_slots": weekend_slots,
        "weekend_days": weekend_days,
        "navbar_links": {
            "Exam Timetable": "exam_timetable_panel",
            "Download PDF": "export_main_pdf",
            "Export CSV": "export_main_csv",
            "Lab Timetabling": "lab_timetable_panel",
        },
    }
    
    # Always load real unscheduled allocations so the form dropdown is populated
    # immediately (they are also re-populated via AJAX for the JS allAllocations array).
    unschedule_allocations    = _get_unscheduled_allocations()
    ew_unschedule_allocations = _get_unscheduled_evening_weekend_allocations()

    # NOTE: no fix needed here — this already counts distinct course_allocation_id,
    # and a CombinedCourseGroup's secondary members never get their own Timetable
    # row (only the primary is ever scheduled), so a merged group of 4 correctly
    # contributes exactly 1 to this count, both before and after the fix above.
    scheduled_count           = Timetable.objects.filter(
        venue__isnull=False,
        start_time__isnull=False,
        end_time__isnull=False,
    ).values('course_allocation_id').distinct().count()
    unscheduled_count         = unschedule_allocations.count()
    ew_unscheduled_count      = ew_unschedule_allocations.count()
    zero_student_count        = _get_zero_student_allocations().count()

    context['unschedule_allocations']    = unschedule_allocations
    context['ew_unschedule_allocations'] = ew_unschedule_allocations
    context['scheduled_count']           = scheduled_count
    context['unscheduled_count']         = unscheduled_count
    context['ew_unscheduled_count']      = ew_unscheduled_count
    context['zero_student_count']        = zero_student_count

    if request.GET.get('initial_load', 'true') == 'true':
        context['show_loading_overlay'] = True
    else:
        context.update(_load_full_timetable_data(request))
        context['show_loading_overlay'] = False

    return render(request, "timetable/timetable_panel.html", context)

def _load_full_timetable_data(request):
    """Load all timetable data - called after initial page load"""
    config = SchedulerConfig.objects.first()  # load all fields including evening/weekend
    
    # Prefetch venues directly with timetables
    venue_prefetch = Prefetch(
        'venue',
        queryset=Venue.objects.only('code', 'capacity')
    )
    
    # Optimized timetables query
    timetables = Timetable.objects.select_related(
        'course_allocation__lecturer',
        'course_allocation__program',
        'course_allocation__department',
    ).prefetch_related(
        venue_prefetch
    ).only(
        'id', 'venue_id', 'day', 'start_time', 'end_time',
        'course_allocation_id', 'course_allocation__course_code',
        'course_allocation__lecturer__name', 'course_allocation__program__name',
        'course_allocation__department__name', 'course_allocation__course_name',
        'course_allocation__number_of_students',
    ).order_by('day', 'start_time')
    
    # Get all unscheduled allocations using the shared helper
    unschedule_allocations = _get_unscheduled_allocations()

    # Membership map covers EVERY member of EVERY CombinedCourseGroup (not
    # just primaries) — needed here because a scheduled cell can contain a
    # secondary member row (e.g. left over from before members were forced
    # onto one slot, or a manual-scheduling bug), and that row must still be
    # recognised and folded into the group rather than shown on its own.
    combined_membership = _get_all_combined_group_membership_map()

    # ── Group timetable entries that share the same cell (day + start_time + venue)
    # so the grid renders them as a single slashed card without duplicates.
    raw_rows = list(timetables.values(
        'id', 'day', 'start_time', 'end_time',
        'venue__code',
        'course_allocation_id',
        'course_allocation__course_code',
        'course_allocation__course_name',
        'course_allocation__lecturer__name',
        'course_allocation__program__name',
        'course_allocation__department__name',
        'course_allocation__number_of_students',
    ))

    slot_groups = {}
    for row in raw_rows:
        st = row['start_time'].strftime('%H:%M') if hasattr(row['start_time'], 'strftime') else str(row['start_time'] or '00:00')
        vc = row['venue__code'] or 'Unknown'
        cell_key = (row['day'] or '', st, vc)
        slot_groups.setdefault(cell_key, []).append(row)

    timetables_data = []
    for cell_key, rows in slot_groups.items():
        # ── Step 1: split rows into combined-group clusters vs. ordinary rows.
        # A row belongs to a cluster if its course_allocation is a member of
        # ANY CombinedCourseGroup — grouped by group_id, NEVER by raw
        # course_code (members can share or differ in course_code; either
        # way they must collapse to ONE card carrying the group's name).
        group_clusters = {}   # group_id -> list of rows
        ordinary_rows = []
        for r in rows:
            info = combined_membership.get(r['course_allocation_id'])
            if info:
                group_clusters.setdefault(info['group_id'], {'info': info, 'rows': []})
                group_clusters[info['group_id']]['rows'].append(r)
            else:
                ordinary_rows.append(r)

        # ── Step 2: one merged card per combined group present in this cell.
        # Dedup by course_allocation_id first, in case the same member was
        # somehow written twice into this cell (the split/duplicate bug).
        for group_id, cluster in group_clusters.items():
            info = cluster['info']
            seen_alloc_ids = {}
            for r in cluster['rows']:
                seen_alloc_ids.setdefault(r['course_allocation_id'], r)
            member_rows = list(seen_alloc_ids.values())
            base = member_rows[0].copy()
            # Never show a raw individual course_code here — only the
            # group's canonical name. Individual member codes / programs /
            # years are carried separately in 'combined_group.identifiers'
            # so the panel can flag a program-year collision without ever
            # printing the member courses as if they were their own row.
            base['course_allocation__course_code'] = info['display_name']
            # Use the group's REAL total (all members, always) — never a
            # local sum of whatever rows happen to be in this one cell.
            base['course_allocation__number_of_students'] = info['total_students']
            base['grouped_ids'] = [r['id'] for r in member_rows]
            base['is_grouped'] = len(member_rows) > 1
            base['is_combined_group'] = True
            base['combined_group'] = {
                'group_id': info['group_id'],
                'group_code': info['group_code'],
                'display_name': info['display_name'],
                'member_course_codes': info['member_course_codes'],
                'identifiers': info['identifiers'],
            }
            timetables_data.append(base)

        # ── Step 3: ordinary (non-combined) rows keep the old raw
        # course_code dedup behaviour — this only ever applies to courses
        # that were never part of a CombinedCourseGroup to begin with.
        seen = {}
        unique = []
        for r in ordinary_rows:
            cc = r['course_allocation__course_code'] or ''
            if cc not in seen:
                seen[cc] = r
                unique.append(r)

        if len(unique) == 1:
            timetables_data.append(unique[0])
        elif unique:
            base = unique[0].copy()
            base['course_allocation__course_code'] = ' / '.join(
                r['course_allocation__course_code'] or '' for r in unique)
            base['course_allocation__course_name'] = ' / '.join(
                filter(None, (r['course_allocation__course_name'] or '' for r in unique)))
            base['course_allocation__lecturer__name'] = ' / '.join(
                dict.fromkeys(r['course_allocation__lecturer__name'] or 'Unassigned' for r in unique))
            base['course_allocation__number_of_students'] = sum(
                r['course_allocation__number_of_students'] or 0 for r in unique)
            base['grouped_ids'] = [r['id'] for r in unique]
            base['is_grouped'] = True
            timetables_data.append(base)

    # Attach CombinedCourseGroup metadata to primary rows so the panel can
    # show e.g. "merged x3 (340 students: Pure CS, Applied CS, BBIT)" instead
    # of presenting a merged group's primary as an ordinary single section.
    combined_meta = _get_combined_group_meta_map()
    unscheduled_dicts = list(unschedule_allocations.values(
        'id', 'course_code', 'course_name',
        'department__name', 'department__faculty__name',
        'lecturer__name', 'program__name', 'number_of_students',
    ))
    for row in unscheduled_dicts:
        meta = combined_meta.get(row['id'])
        row['combined_group'] = meta  # None for ordinary, non-merged allocations
        if meta:
            # Show the group's canonical name only — never the primary's own
            # raw course_code — with individual course codes/program-years
            # available separately in combined_group['identifiers'].
            row['course_code'] = meta['display_name']

    return {
        "timetables": timetables,
        "timetables_data": timetables_data,
        "unscheduled_allocations": unscheduled_dicts,
        "unschedule_allocations": unschedule_allocations,
    }

# -----------------------
# API Endpoints for Progressive Loading - UPDATED
# -----------------------
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def load_timetable_data(request):
    """API endpoint to load full timetable data progressively - RETURNS ALL ENTRIES"""
    try:
        # Get loading stage
        stage = request.GET.get('stage', 'initial')
        
        if stage == 'initial':
            # Load basic data first (20%)
            
            # CRITICAL FIX: Get ALL venues for timetable rendering
            venue_ids_from_timetables = Timetable.objects.filter(
                venue__isnull=False
            ).values_list('venue_id', flat=True).distinct()
            
            venues_from_timetables = Venue.objects.filter(
                id__in=venue_ids_from_timetables
            ).only('code', 'capacity')
            
            venues_with_capacity = Venue.objects.filter(
                capacity__isnull=False
            ).only('code', 'capacity')
            
            # Get ALL venues from the Venue table
            all_venues_from_db = Venue.objects.all().only('code', 'capacity')

            # Blocked / designated venue codes (for row highlighting)
            blocked_venue_codes = set(
                VenueBlock.objects.filter(is_active=True).values_list('venue__code', flat=True)
            )
            designated_venue_codes = set(
                Venue.objects.filter(is_specialized=True).values_list('code', flat=True)
            ) | set(
                VenueSpecialization.objects.values_list('venues__code', flat=True)
            )
            designated_venue_codes.discard(None)
            
            # Combine and deduplicate
            venue_dict = {}
            
            # Add venues from timetables first
            for venue in venues_from_timetables:
                venue_dict[venue.code] = {
                    'code': venue.code,
                    'capacity': venue.capacity or 0,
                    'is_blocked': venue.code in blocked_venue_codes,
                    'is_designated': venue.code in designated_venue_codes,
                }
            
            # Add venues with capacity
            for venue in venues_with_capacity:
                if venue.code not in venue_dict:
                    venue_dict[venue.code] = {
                        'code': venue.code,
                        'capacity': venue.capacity or 0,
                        'is_blocked': venue.code in blocked_venue_codes,
                        'is_designated': venue.code in designated_venue_codes,
                    }
            
            # Add all venues from DB
            for venue in all_venues_from_db:
                if venue.code not in venue_dict:
                    venue_dict[venue.code] = {
                        'code': venue.code,
                        'capacity': venue.capacity or 0,
                        'is_blocked': venue.code in blocked_venue_codes,
                        'is_designated': venue.code in designated_venue_codes,
                    }
            
            # Also get any venue codes from timetables
            timetable_venue_codes = set(
                Timetable.objects.filter(venue__isnull=False)
                .values_list('venue__code', flat=True)
                .distinct()
            )
            for venue_code in timetable_venue_codes:
                if venue_code and venue_code not in venue_dict:
                    venue_dict[venue_code] = {
                        'code': venue_code,
                        'capacity': 0,
                        'is_blocked': venue_code in blocked_venue_codes,
                        'is_designated': venue_code in designated_venue_codes,
                    }
            
            venues_data = list(venue_dict.values())
            venues_data.sort(key=lambda x: x['code'].lower())
            
            # Get counts
            timetable_count       = Timetable.objects.filter(
                venue__isnull=False,
                start_time__isnull=False,
                end_time__isnull=False,
            ).values('course_allocation_id').distinct().count()
            unscheduled_count     = _get_unscheduled_allocations().count()
            ew_unscheduled_count  = _get_unscheduled_evening_weekend_allocations().count()
            zero_student_count    = _get_zero_student_allocations().count()

            return JsonResponse({
                'progress': 20,
                'status': 'loading',
                'message': f'Loaded {len(venues_data)} venues and {timetable_count} timetable entries',
                'data': {
                    'venues': venues_data,  # ALL venues needed for table rows
                    'stats': {
                        'timetable_count': timetable_count,
                        'unscheduled_count': unscheduled_count,
                        'ew_unscheduled_count': ew_unscheduled_count,
                        'zero_student_count': zero_student_count,
                        'venues_count': len(venues_data)
                    }
                }
            })
            
        elif stage == 'timetables':
            # Load ALL timetable data (40%)
            timetables = Timetable.objects.select_related(
                'course_allocation__lecturer',
                'course_allocation__program',
                'course_allocation__department',
                'course_allocation__program_course',
                'venue'
            ).only(
                'id', 'day', 'start_time', 'end_time',
                'course_allocation__course_code',
                'course_allocation__course_name',
                'course_allocation__lecturer__name',
                'course_allocation__program__name',
                'course_allocation__department__name',
                'course_allocation__number_of_students',
                'course_allocation__program_course__year',
                'venue__code',
            ).order_by('day', 'start_time')

            # ── Group entries that share the same cell (day + start_time + venue) ──
            # so the timetable grid displays them as a single slashed card
            combined_membership = _get_all_combined_group_membership_map()
            slot_groups = {}   # key: (day, start_time, venue_code) -> list of raw entry dicts
            for tt in timetables:
                start_time = tt.start_time.strftime('%H:%M') if tt.start_time else "00:00"
                end_time   = tt.end_time.strftime('%H:%M')   if tt.end_time   else "00:00"
                venue_code = tt.venue.code if tt.venue else 'Unknown'
                cell_key   = (tt.day or '', start_time, venue_code)

                entry = {
                    'id':         tt.id,
                    'day':        tt.day,
                    'start_time': start_time,
                    'end_time':   end_time,
                    'course_code': tt.course_allocation.course_code if tt.course_allocation else 'Unknown',
                    'course_name': tt.course_allocation.course_name or '' if tt.course_allocation else '',
                    'lecturer':   getattr(tt.course_allocation.lecturer, 'name', 'Unassigned') if tt.course_allocation else 'Unassigned',
                    'program':    getattr(tt.course_allocation.program, 'name', 'N/A') if tt.course_allocation else 'N/A',
                    'program_id': tt.course_allocation.program_id if tt.course_allocation else None,
                    'year':       _get_year_value(tt.course_allocation) if tt.course_allocation else None,
                    'department': getattr(tt.course_allocation.department, 'name', 'N/A') if tt.course_allocation else 'N/A',
                    'venue':      venue_code,
                    'students':   tt.course_allocation.number_of_students or 0 if tt.course_allocation else 0,
                    # ── Needed by the Simulate Move / Bulk Move context menu ──
                    'tt_id':               tt.id,
                    'course_allocation_id': tt.course_allocation_id,
                }
                slot_groups.setdefault(cell_key, []).append(entry)

            # Collapse each cell group into a single representative entry.
            #
            # FIX: combined-group detection MUST happen before any raw
            # course_code deduplication. Members of a CombinedCourseGroup
            # can legitimately share the exact same literal course_code
            # (e.g. PHYS 121 offered to two programs, combined into one
            # class). The old code deduped by course_code FIRST, so a group
            # like that collapsed to a single raw, unprocessed entry before
            # the combined-group check ever ran — printing the bare course
            # code instead of the group's display name. Now every entry is
            # matched against combined_membership by course_allocation_id
            # (never by course_code string) up front, so it's caught
            # regardless of whether members happen to share a code.
            timetable_data = []
            for cell_key, entries in slot_groups.items():
                group_clusters = {}   # group_id -> {'info': ..., 'entries': [...]}
                ordinary_entries = []
                for e in entries:
                    info = combined_membership.get(e['course_allocation_id'])
                    if info:
                        cluster = group_clusters.setdefault(
                            info['group_id'], {'info': info, 'entries': []})
                        cluster['entries'].append(e)
                    else:
                        ordinary_entries.append(e)

                # One merged card per combined group present in this cell.
                for group_id, cluster in group_clusters.items():
                    info = cluster['info']
                    # De-dup by course_allocation_id in case the same member
                    # was somehow written twice into this cell.
                    seen_alloc = {}
                    for e in cluster['entries']:
                        seen_alloc.setdefault(e['course_allocation_id'], e)
                    member_entries = list(seen_alloc.values())

                    base = member_entries[0].copy()
                    base['course_name'] = ' / '.join(
                        filter(None, (e['course_name'] for e in member_entries)))
                    base['lecturer'] = ' / '.join(
                        dict.fromkeys(e['lecturer'] for e in member_entries))
                    # Use the group's REAL total (all members, always) —
                    # never a local sum of whatever rows happen to be in
                    # this one cell. If members are split across cells (the
                    # split-scheduling bug) or a query only surfaces a
                    # subset, a local sum silently undercounts.
                    base['students'] = info['total_students']
                    base['grouped_ids'] = [e['id'] for e in member_entries]
                    base['is_grouped'] = len(member_entries) > 1
                    # Never show a raw individual course_code for a combined
                    # group — only the group's canonical name. Individual
                    # member codes/programs/years are carried separately in
                    # 'identifiers' so the panel can flag a program-year
                    # collision without ever printing member courses as if
                    # they were their own row.
                    base['course_code'] = info['display_name']
                    base['is_combined_group'] = True
                    base['combined_group_id'] = group_id
                    base['combined_group_label'] = info['group_code']
                    base['identifiers'] = info['identifiers']
                    # Per-course breakdown so the right-click "Simulate Move"
                    # menu can target one specific course within a slashed
                    # cell (the backend bundles the rest of the group
                    # automatically).
                    base['courses'] = [
                        {
                            'tt_id':                e['id'],
                            'course_allocation_id': e['course_allocation_id'],
                            'course_code':          e['course_code'],
                            'lecturer':             e['lecturer'],
                            'program':              e.get('program'),
                            'program_id':           e.get('program_id'),
                            'year':                 e.get('year'),
                            'students':             e['students'],
                        }
                        for e in member_entries
                    ]
                    timetable_data.append(base)

                # Ordinary (non-combined) rows keep the old raw course_code
                # dedup behaviour — this only ever applies to courses that
                # were never part of a CombinedCourseGroup to begin with.
                seen_codes = {}
                unique_entries = []
                for e in ordinary_entries:
                    if e['course_code'] not in seen_codes:
                        seen_codes[e['course_code']] = e
                        unique_entries.append(e)

                if len(unique_entries) == 1:
                    timetable_data.append(unique_entries[0])
                elif unique_entries:
                    # A coincidental overlap of unrelated courses in the same
                    # cell — a real scheduling conflict, not a combined
                    # group. Keep the old slash-joined label so it still
                    # reads as distinct courses.
                    base = unique_entries[0].copy()
                    base['course_name'] = ' / '.join(
                        filter(None, (e['course_name'] for e in unique_entries)))
                    base['lecturer'] = ' / '.join(
                        dict.fromkeys(e['lecturer'] for e in unique_entries))
                    base['students'] = sum(e['students'] for e in unique_entries)
                    base['grouped_ids'] = [e['id'] for e in unique_entries]
                    base['is_grouped'] = True
                    base['course_code'] = ' / '.join(e['course_code'] for e in unique_entries)
                    base['courses'] = [
                        {
                            'tt_id':                e['id'],
                            'course_allocation_id': e['course_allocation_id'],
                            'course_code':          e['course_code'],
                            'lecturer':             e['lecturer'],
                            'program':              e.get('program'),
                            'program_id':           e.get('program_id'),
                            'year':                 e.get('year'),
                            'students':             e['students'],
                        }
                        for e in unique_entries
                    ]
                    timetable_data.append(base)

            return JsonResponse({
                'progress': 40,
                'status': 'processing',
                'message': f'Loaded ALL {len(timetable_data)} timetable cells ({sum(len(v) for v in slot_groups.values())} entries)',
                'data': {
                    'timetables': timetable_data
                }
            })
            
        elif stage == 'unscheduled':
            # Load ALL unscheduled allocations (60%)
            # Regular courses (is_evening_weekend=False) only
            unscheduled = _get_unscheduled_allocations()
            # Evening/weekend flagged courses
            ew_unscheduled = _get_unscheduled_evening_weekend_allocations()

            # Precompute once — avoids re-querying CombinedCourseGroup per row.
            combined_meta = _get_combined_group_meta_map()

            def _alloc_dict(alloc):
                meta = combined_meta.get(alloc.id)
                return {
                    'id': alloc.id,
                    # Canonical group name (e.g. "MATH 221 Combined") when this
                    # row represents a CombinedCourseGroup — never the primary's
                    # own raw course_code. Individual member codes/program-years
                    # live only in combined_group['identifiers'] below.
                    'course_code': meta['display_name'] if meta else alloc.course_code,
                    'course_name': alloc.course_name,
                    'lecturer': getattr(alloc.lecturer, 'name', 'Unassigned'),
                    'program': getattr(alloc.program, 'name', 'N/A'),
                    'department': getattr(alloc.department, 'name', 'N/A'),
                    'faculty': getattr(alloc.department.faculty, 'name', 'N/A') if alloc.department and alloc.department.faculty else 'N/A',
                    'students': alloc.number_of_students or 0,
                    'is_evening_weekend': alloc.is_evening_weekend,
                    # None for an ordinary allocation. When present, this row
                    # represents a whole CombinedCourseGroup (this alloc is the
                    # primary) — 'students' above is already the group total.
                    'combined_group': meta,
                }

            unscheduled_data    = [_alloc_dict(a) for a in unscheduled]
            ew_unscheduled_data = [_alloc_dict(a) for a in ew_unscheduled]

            return JsonResponse({
                'progress': 60,
                'status': 'optimizing',
                'message': (
                    f'Loaded {len(unscheduled_data)} regular + '
                    f'{len(ew_unscheduled_data)} evening/weekend unscheduled'
                ),
                'data': {
                    'unscheduled': unscheduled_data,
                    'ew_unscheduled': ew_unscheduled_data,
                    'ready_for_display': True
                }
            })
            
        elif stage == 'venues':
            # Load venue details (80%)
            venues = Venue.objects.only('code', 'capacity').order_by('code')
            
            venue_data = []
            for venue in venues:
                # Get all timetable entries for this venue
                venue_timetables = Timetable.objects.filter(venue=venue).count()
                venue_data.append({
                    'code': venue.code,
                    'capacity': venue.capacity or 0,
                    'timetable_count': venue_timetables,
                    'utilization': f"{((venue_timetables * 100) / (5 * 8)):.1f}%" if venue.capacity else "N/A"
                })
            
            return JsonResponse({
                'progress': 80,
                'status': 'loading',
                'message': f'Loaded ALL {len(venue_data)} venues',
                'data': {
                    'venues': venue_data
                }
            })
            
        elif stage == 'auto_merged':
            # Load AutoMergedExamGroup entries (for the merged table).
            # Properly resolves venue/timeslot/day from timetable_entry when group
            # fields are empty. Published groups with NO venue+timeslot are
            # returned as unscheduled so the frontend can handle them correctly.
            from timetable.models import AutoMergedExamGroup
            groups = list(
                AutoMergedExamGroup.objects
                .select_related('base_course', 'venue', 'timetable_entry__venue',
                                'temp_timetable_entry')
                .prefetch_related('merged_courses__lecturer')
                .order_by('-created_at')
            )

            # Build per-member timetable lookup for course-level slot info
            all_member_ids = set()
            for g in groups:
                for aid in g.merged_courses.values_list('id', flat=True):
                    all_member_ids.add(aid)

            tt_map = {}
            for tt in Timetable.objects.filter(
                course_allocation_id__in=all_member_ids,
                venue__isnull=False,
                start_time__isnull=False,
            ).select_related('venue').order_by('course_allocation_id', 'start_time'):
                if tt.course_allocation_id not in tt_map:
                    tt_map[tt.course_allocation_id] = tt

            data = []
            unscheduled_from_merged = []  # published groups with no slot → members go to unscheduled

            for g in groups:
                active_entry = g.get_active_timetable_entry()

                # Resolve group-level venue/timeslot/day:
                # Priority: group.venue/start_time → active timetable_entry
                display_venue = g.venue.code if g.venue else ''
                display_start = g.start_time.strftime('%H:%M') if g.start_time else ''
                display_end   = g.end_time.strftime('%H:%M')   if g.end_time   else ''
                display_day   = ''

                if active_entry:
                    if not display_venue and getattr(active_entry, 'venue', None):
                        display_venue = active_entry.venue.code
                    if not display_start and getattr(active_entry, 'start_time', None):
                        display_start = active_entry.start_time.strftime('%H:%M')
                    if not display_end and getattr(active_entry, 'end_time', None):
                        display_end = active_entry.end_time.strftime('%H:%M')
                    display_day = getattr(active_entry, 'day', '') or ''

                # If published but no venue+timeslot → treat members as unscheduled
                if g.published and not (display_venue and display_start):
                    for c in g.merged_courses.all():
                        unscheduled_from_merged.append({
                            'id': c.id,
                            'course_code': c.course_code,
                            'course_name': getattr(c, 'course_name', '') or '',
                            'lecturer': getattr(c.lecturer, 'name', '–') if c.lecturer else '–',
                            'students': c.number_of_students or 0,
                        })
                    # Still include in the merged table as a no-slot published group
                    # so the UI can show it needs a venue assigned
                    has_slot = False
                else:
                    has_slot = bool(display_venue and display_start)

                courses = []
                for c in g.merged_courses.all():
                    ctt = tt_map.get(c.id)
                    courses.append({
                        'id':          c.id,
                        'course_code': c.course_code,
                        'lecturer':    getattr(c.lecturer, 'name', '–') if c.lecturer else '–',
                        'venue':       ctt.venue.code if ctt and ctt.venue else display_venue,
                        'start_time':  ctt.start_time.strftime('%H:%M') if ctt and ctt.start_time else display_start,
                        'end_time':    ctt.end_time.strftime('%H:%M') if ctt and ctt.end_time else display_end,
                        'day':         getattr(ctt, 'day', display_day) if ctt else display_day,
                    })

                data.append({
                    'id':             g.id,
                    'merged_code':    g.merged_code or '',
                    'total_students': g.total_students or 0,
                    'published':      g.published,
                    'has_slot':       has_slot,
                    'venue':          display_venue,
                    'start_time':     display_start,
                    'end_time':       display_end,
                    'day':            display_day,
                    'allocation_ids': list(g.merged_courses.values_list('id', flat=True)),
                    'timetable_entry_id':        g.timetable_entry_id,
                    'temp_timetable_entry_id':   g.temp_timetable_entry_id,
                    'active_timetable_entry_id': active_entry.id if active_entry else None,
                    'courses':        courses,
                })

            return JsonResponse({'progress': 85, 'status': 'loading',
                                 'message': f'Loaded {len(data)} merged groups',
                                 'data': {
                                     'merged_groups': data,
                                     'unscheduled_from_merged': unscheduled_from_merged,
                                 }})

        elif stage == 'complete':
            # Load conflicts and complete the load (100%)
            try:
                conflicts_response = timetable_conflicts_api(request)
                conflicts = json.loads(conflicts_response.content)
            except Exception as e:
                conflicts = {
                    'program_conflicts': [],
                    'lecturer_conflicts': [],
                    'venue_conflicts': [],
                    'total_conflicts': 0,
                    'error': str(e)
                }
            
            timetable_count = Timetable.objects.filter(
                venue__isnull=False,
                start_time__isnull=False,
                end_time__isnull=False,
            ).values('course_allocation_id').distinct().count()
            unscheduled_count = _get_unscheduled_allocations().count()
            venue_count = Venue.objects.count()
            lecturer_count = Lecturer.objects.count()
            
            return JsonResponse({
                'progress': 100,
                'status': 'completed',
                'message': f'Timetable workspace fully loaded with ALL data',
                'data': {
                    'conflicts': conflicts,
                    'summary': {
                        'total_timetables': timetable_count,
                        'total_unscheduled': unscheduled_count,
                        'total_venues': venue_count,
                        'total_lecturers': lecturer_count,
                        'conflict_count': conflicts.get('total_conflicts', 0)
                    },
                    'loading_complete': True
                }
            })
            
    except Exception as e:
        import traceback
        return JsonResponse({
            'progress': 0,
            'status': 'error',
            'message': f'Error loading data: {str(e)}',
            'data': {'traceback': traceback.format_exc()}
        })

# ─────────────────────────────────────────────────────────────
# merged_api  — GET / POST / PUT / DELETE
#
# Manages AutoMergedExamGroup records from the main timetable panel.
#
# ROOT CAUSE OF "base_course_id cannot be null":
#   AutoMergedExamGroup.base_course is a NOT-NULL FK.
#   The original view was creating the group without setting it.
#   FIX: always set base_course = first allocation in the list.
# ─────────────────────────────────────────────────────────────
from django.views.decorators.http import require_http_methods
from timetable.models import AutoMergedExamGroup


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def merged_api(request):
    """
    GET  ?id=<pk>  → fetch single group (for edit modal)
    GET            → list all groups
    POST           → create new group
    PUT            → update existing group
    DELETE         → delete group
    """

    # ── GET ──────────────────────────────────────────────────
    if request.method == 'GET':
        pk = request.GET.get('id')
        if pk:
            # Single-record fetch for the edit modal
            try:
                g = AutoMergedExamGroup.objects.prefetch_related('merged_courses').get(pk=pk)
                return JsonResponse({
                    'id':             g.id,
                    'merged_code':    g.merged_code or '',
                    'total_students': g.total_students or 0,
                    'published':      g.published,
                    'venue':          g.venue.code if g.venue else '',
                    'start_time':     g.start_time.strftime('%H:%M') if g.start_time else '',
                    'end_time':       g.end_time.strftime('%H:%M') if g.end_time else '',
                    'allocation_ids': list(g.merged_courses.values_list('id', flat=True)),
                    'timetable_entry_id':      g.timetable_entry_id,
                    'temp_timetable_entry_id': g.temp_timetable_entry_id,
                    'active_timetable_entry_id': g.get_active_timetable_entry().id if g.get_active_timetable_entry() else None,
                })
            except AutoMergedExamGroup.DoesNotExist:
                return JsonResponse({'error': 'Not found'}, status=404)

        # Full list
        groups = list(
            AutoMergedExamGroup.objects
            .select_related('base_course', 'venue', 'timetable_entry__venue', 'temp_timetable_entry')
            .prefetch_related('merged_courses__lecturer')
            .order_by('-created_at')
        )

        # Batch-fetch timetable entries (venue+timeslot) for all member courses
        all_member_ids = set()
        for g in groups:
            for aid in g.merged_courses.values_list('id', flat=True):
                all_member_ids.add(aid)

        tt_map = {}  # allocation_id -> first Timetable row with venue+timeslot
        for tt in Timetable.objects.filter(
            course_allocation_id__in=all_member_ids,
            venue__isnull=False,
            start_time__isnull=False,
        ).select_related('venue').order_by('course_allocation_id', 'start_time'):
            if tt.course_allocation_id not in tt_map:
                tt_map[tt.course_allocation_id] = tt

        data = []
        for g in groups:
            courses = []
            for c in g.merged_courses.all():
                ctt = tt_map.get(c.id)
                courses.append({
                    'id':          c.id,
                    'course_code': c.course_code,
                    'lecturer':    getattr(c.lecturer, 'name', '–') if c.lecturer else '–',
                    'venue':       ctt.venue.code if ctt and ctt.venue else '',
                    'start_time':  ctt.start_time.strftime('%H:%M') if ctt and ctt.start_time else '',
                    'end_time':    ctt.end_time.strftime('%H:%M') if ctt and ctt.end_time else '',
                    'day':         getattr(ctt, 'day', '') if ctt else '',
                })

            try:
                active_entry = g.get_active_timetable_entry()
            except Exception:
                active_entry = None

            # Group-level venue/timeslot: stored directly, or fall back to active entry
            display_venue = g.venue.code if g.venue else ''
            display_start = g.start_time.strftime('%H:%M') if g.start_time else ''
            display_end   = g.end_time.strftime('%H:%M')   if g.end_time   else ''
            display_day   = ''
            if active_entry:
                if not display_venue and getattr(active_entry, 'venue', None):
                    display_venue = active_entry.venue.code
                if not display_start and getattr(active_entry, 'start_time', None):
                    display_start = active_entry.start_time.strftime('%H:%M')
                if not display_end and getattr(active_entry, 'end_time', None):
                    display_end = active_entry.end_time.strftime('%H:%M')
                display_day = getattr(active_entry, 'day', '') or ''

            has_slot = bool(display_venue and display_start)

            # If published but no slot, fill per-course venue/timeslot from group-level
            # so the regular timetable can render them
            for c_item in courses:
                if not c_item['venue'] and display_venue:
                    c_item['venue'] = display_venue
                if not c_item['start_time'] and display_start:
                    c_item['start_time'] = display_start
                if not c_item['end_time'] and display_end:
                    c_item['end_time'] = display_end
                if not c_item['day'] and display_day:
                    c_item['day'] = display_day

            data.append({
                'id':             g.id,
                'merged_code':    g.merged_code or '',
                'total_students': g.total_students or 0,
                'published':      g.published,
                'has_slot':       has_slot,
                'venue':          display_venue,
                'start_time':     display_start,
                'end_time':       display_end,
                'day':            display_day,
                'allocation_ids': [c['id'] for c in courses],
                'courses':        courses,
                'timetable_entry_id':        g.timetable_entry_id,
                'temp_timetable_entry_id':   g.temp_timetable_entry_id,
                'active_timetable_entry_id': active_entry.id if active_entry else None,
            })
        return JsonResponse({'status': 'success', 'data': data})

    # ── POST — CREATE ────────────────────────────────────────
    if request.method == 'POST':
        try:
            merged_code    = (request.POST.get('merged_code') or '').strip()
            total_students = int(request.POST.get('total_students') or 0)
            published      = request.POST.get('published', 'false').lower() == 'true'
            alloc_ids      = [int(x) for x in request.POST.getlist('allocation_ids[]') if x]
            venue_code     = (request.POST.get('venue') or '').strip()

            if not merged_code:
                return JsonResponse({'status': 'error', 'message': 'merged_code is required.'}, status=400)
            if len(alloc_ids) < 2:
                return JsonResponse({'status': 'error', 'message': 'Select at least 2 courses.'}, status=400)

            allocations = list(CourseAllocation.objects.filter(id__in=alloc_ids))
            if not allocations:
                return JsonResponse({'status': 'error', 'message': 'No valid allocations found.'}, status=400)

            # Always set base_course to the first allocation
            base_course = allocations[0]

            # Resolve venue: use provided code, else inherit from base course timetable entry
            venue_obj = None
            if venue_code:
                venue_obj = Venue.objects.filter(code__iexact=venue_code).first()
                if not venue_obj:
                    venue_obj = Venue.objects.create(code=venue_code)

            # Link timetable entry based on published state
            timetable_entry      = None
            temp_timetable_entry = None
            if published:
                tt_qs = Timetable.objects.filter(course_allocation=base_course)
                if venue_obj:
                    timetable_entry = tt_qs.filter(venue=venue_obj).first() or tt_qs.first()
                else:
                    timetable_entry = tt_qs.filter(
                        venue__isnull=False, start_time__isnull=False
                    ).first() or tt_qs.first()
                # Inherit venue+timeslot from timetable entry if not explicitly provided
                if timetable_entry and not venue_obj:
                    venue_obj = timetable_entry.venue
            else:
                temp_timetable_entry = TempTimetable.objects.filter(
                    course_allocation=base_course,
                ).first()

            group = AutoMergedExamGroup.objects.create(
                base_course          = base_course,
                merged_code          = merged_code,
                total_students       = total_students or sum(a.number_of_students or 0 for a in allocations),
                published            = published,
                venue                = venue_obj,
                start_time           = timetable_entry.start_time if timetable_entry else None,
                end_time             = timetable_entry.end_time   if timetable_entry else None,
                timetable_entry      = timetable_entry,
                temp_timetable_entry = temp_timetable_entry,
            )
            group.merged_courses.set(allocations)

            return JsonResponse({
                'status':  'success',
                'id':      group.id,
                'message': f'Merged group "{merged_code}" created.',
                'venue':      venue_obj.code if venue_obj else '',
                'start_time': group.start_time.strftime('%H:%M') if group.start_time else '',
                'end_time':   group.end_time.strftime('%H:%M')   if group.end_time   else '',
                'timetable_entry_id':      group.timetable_entry_id,
                'temp_timetable_entry_id': group.temp_timetable_entry_id,
            })

        except Exception as exc:
            import traceback; traceback.print_exc()
            return JsonResponse({'status': 'error', 'message': str(exc)}, status=500)

    # ── PUT — UPDATE ─────────────────────────────────────────
    if request.method == 'PUT':
        try:
            body          = json.loads(request.body)
            pk            = body.get('id')
            merged_code   = (body.get('merged_code') or '').strip()
            venue_code    = (body.get('venue') or '').strip()
            published     = bool(body.get('published', False))
            alloc_ids     = [int(x) for x in (body.get('allocation_ids') or []) if x]
            total_students = int(body.get('total_students') or 0)

            group = AutoMergedExamGroup.objects.get(pk=pk)

            if merged_code:
                group.merged_code = merged_code
            group.published      = published
            group.total_students = total_students

            if venue_code:
                venue_obj = Venue.objects.filter(code=venue_code).first()
                if not venue_obj:
                    try:
                        venue_obj = Venue.objects.create(code=venue_code)
                    except Exception:
                        venue_obj = Venue.objects.filter(code=venue_code).first()
                group.venue = venue_obj
            elif 'venue' in body and not venue_code:
                group.venue = None

            if alloc_ids:
                allocations = list(CourseAllocation.objects.filter(id__in=alloc_ids))
                if allocations:
                    # ── THE FIX also applies on update: keep base_course in sync ──
                    group.base_course = allocations[0]
                    group.merged_courses.set(allocations)
                    # Auto-compute total_students from allocations when not supplied
                    if not total_students:
                        total_students = sum(a.number_of_students or 0 for a in allocations)

            group.total_students = total_students

            # Sync timetable entry links to match the (possibly new) published state
            if published:
                timetable_entry = Timetable.objects.filter(
                    course_allocation=group.base_course,
                ).first()
                group.timetable_entry      = timetable_entry
                group.temp_timetable_entry = None
            else:
                temp_tt_entry = TempTimetable.objects.filter(
                    course_allocation=group.base_course,
                ).first()
                group.timetable_entry      = None
                group.temp_timetable_entry = temp_tt_entry

            group.save()
            active_entry = group.get_active_timetable_entry()
            return JsonResponse({
                'status':  'success',
                'message': 'Group updated.',
                'timetable_entry_id':        group.timetable_entry_id,
                'temp_timetable_entry_id':   group.temp_timetable_entry_id,
                'active_timetable_entry_id': active_entry.id if active_entry else None,
            })

        except AutoMergedExamGroup.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Group not found.'}, status=404)
        except Exception as exc:
            import traceback; traceback.print_exc()
            return JsonResponse({'status': 'error', 'message': str(exc)}, status=500)

    # ── DELETE ───────────────────────────────────────────────
    if request.method == 'DELETE':
        try:
            body = json.loads(request.body)
            pk   = body.get('id')
            AutoMergedExamGroup.objects.filter(pk=pk).delete()
            return JsonResponse({'status': 'success', 'message': 'Deleted.'})
        except Exception as exc:
            return JsonResponse({'status': 'error', 'message': str(exc)}, status=500)

    return JsonResponse({'status': 'error', 'message': 'Method not allowed.'}, status=405)

# ─────────────────────────────────────────────────────────────
# bulk_delete_timetable_entries
#
# Deletes one OR multiple Timetable rows by id.
# The frontend passes either a single `id` OR a list `ids[]`
# (for grouped/slashed cells).  Both Timetable and TempTimetable
# rows are deleted so the grid cell clears cleanly.
# ─────────────────────────────────────────────────────────────
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def bulk_delete_timetable_entries(request):
    """
    DELETE /timetable/bulk-delete/
    Body (JSON):
        { "ids": [1, 2, 3] }          <- grouped slashed cell
        { "id": 5 }                   <- single entry (backward compat)
    """
    if request.method != 'DELETE':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed.'}, status=405)

    try:
        body = json.loads(request.body)
        ids = body.get('ids') or ([body['id']] if body.get('id') else [])
        ids = [int(i) for i in ids if i]

        if not ids:
            return JsonResponse({'status': 'error', 'message': 'No ids provided.'}, status=400)

        deleted_tt, _  = Timetable.objects.filter(id__in=ids).delete()
        deleted_tmp, _ = TempTimetable.objects.filter(id__in=ids).delete()

        return JsonResponse({
            'status': 'success',
            'message': 'Deleted {} timetable and {} temp-timetable entries.'.format(deleted_tt, deleted_tmp),
            'deleted_ids': ids,
        })

    except Exception as exc:
        import traceback; traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(exc)}, status=500)

# ─────────────────────────────────────────────────────────────
# optimize_venues_per_timeslot
#
# Constraints honoured (in priority order):
#
#   1. Specialization-CELL lock  — when a timetable entry sits in a
#      specialized venue AND its course_code is designated to that venue,
#      that specific CELL (course + venue + timeslot) is frozen in place.
#      The course is NOT moved.  Its venue is NOT offered to others in
#      that slot.  Every OTHER course in the same timeslot (including
#      other rows in the same timetable "row") is still eligible for
#      swapping — only the one matched cell is skipped.
#
#   2. Merged-group venues  — venue codes used by published
#      AutoMergedExamGroups are excluded from the swap pool (existing
#      behaviour, unchanged).
#
#   3. Capacity optimisation — all remaining free courses in each
#      timeslot are reassigned to best-fit venues by student count.
# ─────────────────────────────────────────────────────────────
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def optimize_venues_per_timeslot(request):
    """POST /timetable/optimize-venues/"""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST required.'}, status=405)
    try:
        from timetable.models import AutoMergedExamGroup
        from room_management.models import Venue as VenueModel, VenueSpecialization

        # ── 1. Collect merged-group constraints ───────────────────────────
        merged_alloc_ids = set()
        merged_venues    = set()   # venue codes locked by published merge groups

        for group in (
            AutoMergedExamGroup.objects
            .filter(published=True)
            .prefetch_related('merged_courses')
            .select_related('venue')
        ):
            if group.venue:
                merged_venues.add(group.venue.code)
            for alloc in group.merged_courses.all():
                merged_alloc_ids.add(alloc.id)

        print(f"DEBUG optimize: {len(merged_alloc_ids)} allocs in merged groups, "
              f"{len(merged_venues)} merged-group venues: {merged_venues}")

        # ── 2. Build specialization lookup structures ──────────────────────
        #
        # specialized_venue_codes : set of venue codes that are specialised
        # venue_designated_codes  : venue_code -> set[str] of upper-cased
        #                           course codes designated to that venue
        #
        # A single CELL is "specialization-locked" when ALL of:
        #   a) entry.venue.code is in specialized_venue_codes
        #   b) entry.course_allocation.course_code (upper) is in
        #      venue_designated_codes[venue_code]
        #
        # Only that one entry is frozen.  All other entries in the same
        # timeslot (including others sharing the same timetable "row")
        # remain completely free to be swapped.

        specialized_venue_codes = set()
        venue_designated_codes  = {}   # venue_code -> set of course codes

        # exclusive_venue_codes: venues belonging to a rule with exclusive=True.
        # These venues may NEVER be offered to any other course — not even
        # when temporarily empty or occupied by a non-designated course.
        # This is what makes e.g. Law venues fully off-limits to everyone
        # else, whereas a soft (non-exclusive) reservation like Nursing's
        # dedicated venues may still be lent out once free / on overflow.
        exclusive_venue_codes = set()

        active_rules = list(
            VenueSpecialization.objects
            .filter(is_active=True)
            .prefetch_related(
                'venues',
                'courses',
                'programs__courses',
                'departments__programs__courses',
            )
        )

        for rule in active_rules:
            rule_codes = rule.get_designated_course_codes()   # normalised upper-case
            for venue in rule.venues.all():
                specialized_venue_codes.add(venue.code)
                venue_designated_codes.setdefault(venue.code, set()).update(rule_codes)
                if rule.exclusive:
                    exclusive_venue_codes.add(venue.code)

        # Also honour is_specialized flag even when no rule exists yet
        # (soft reservation only — there's no rule to read exclusivity from)
        for v in VenueModel.objects.filter(is_specialized=True).only('code'):
            specialized_venue_codes.add(v.code)
            venue_designated_codes.setdefault(v.code, set())

        print(f"DEBUG optimize: {len(specialized_venue_codes)} specialized venues: "
              f"{specialized_venue_codes}")
        print(f"DEBUG optimize: {len(exclusive_venue_codes)} EXCLUSIVE (fully blocked) "
              f"venues: {exclusive_venue_codes}")
        print(f"DEBUG optimize: designated codes per venue: "
              f"{ {k: len(v) for k, v in venue_designated_codes.items()} }")

        # ── 3. Load all timetable rows ────────────────────────────────────
        all_rows = list(
            Timetable.objects
            .select_related('venue', 'course_allocation')
            .filter(
                venue__isnull=False,
                start_time__isnull=False,
                end_time__isnull=False,
            )
            .only(
                'id', 'day', 'start_time', 'end_time',
                'venue__code', 'venue__capacity',
                'course_allocation__course_code',
                'course_allocation__number_of_students',
                'course_allocation_id',
            )
        )

        # ── 3b. Detect existing venue double-bookings ──────────────────────
        #
        # Two different course_allocations already sharing the exact same
        # (day, start, end, venue) — a straight data conflict, regardless of
        # any specialization rule. We report every instance found. Where
        # possible the optimisation pass below will resolve it automatically
        # (the venue-dedup in step 6 means only the first-seen occupant can
        # "keep" that venue; every other row sharing it is forced to look
        # for a different venue in the same slot). Any pair that can't be
        # resolved (e.g. no free alternate venue in that slot) is still
        # surfaced here so it can be fixed by hand.
        venue_slot_occupants = defaultdict(list)
        for row in all_rows:
            if not row.venue:
                continue
            key = (
                row.day or '',
                row.start_time.strftime('%H:%M'),
                row.end_time.strftime('%H:%M'),
                row.venue.code,
            )
            venue_slot_occupants[key].append(row)

        double_bookings = []
        for (day, start_str, end_str, vcode), rows in venue_slot_occupants.items():
            distinct_allocs = {r.course_allocation_id for r in rows if r.course_allocation_id}
            if len(distinct_allocs) > 1:
                course_codes = sorted({
                    (r.course_allocation.course_code or '?').strip().upper()
                    for r in rows if r.course_allocation
                })
                double_bookings.append({
                    'day':     day,
                    'slot':    f'{start_str}–{end_str}',
                    'venue':   vcode,
                    'courses': course_codes,
                })
                print(f"DEBUG optimize: DOUBLE-BOOKING — {vcode} ({day} "
                      f"{start_str}-{end_str}) shared by {course_codes}")

        # ── 4. Pre-compute per-CELL lock flags ────────────────────────────
        #
        # locked_entry_ids   : set of Timetable.id values whose individual
        #                      cell is frozen.  Only these specific entries
        #                      must not be moved; every other entry — even
        #                      in the same timeslot row — is free.
        #
        # locked_venue_slots : set of (day, start_str, end_str, venue_code)
        #                      where the venue is already occupied by its
        #                      designated course THIS slot → excluded from
        #                      the swap pool for that slot only.  The same
        #                      venue is fully available in other slots.

        locked_entry_ids   = set()
        locked_venue_slots = set()   # (day, start, end, venue_code)

        # exclusive_violations: rows currently sitting in an EXCLUSIVE venue
        # but whose course is NOT one of that venue's designated courses.
        # This should never happen going forward (exclusive venues are
        # removed from the swap pool below) but existing bad data or rows
        # created by another code path can still land here. We do NOT lock
        # these — leaving them unlocked lets the optimisation pass below
        # move them out into a legitimate venue, since their current venue
        # is excluded from every candidate pool. We just report them.
        exclusive_violations = []

        for row in all_rows:
            if not row.venue or not row.course_allocation:
                continue
            vcode     = row.venue.code
            ccode     = (row.course_allocation.course_code or '').strip().upper()
            start_str = row.start_time.strftime('%H:%M')
            end_str   = row.end_time.strftime('%H:%M')
            day       = row.day or ''

            if vcode in specialized_venue_codes:
                designated = venue_designated_codes.get(vcode, set())
                if ccode in designated:
                    # Only THIS cell is frozen — NOT the whole timeslot row.
                    # This applies to both soft and exclusive rules: a
                    # designated course sitting in its own venue never moves.
                    locked_entry_ids.add(row.id)
                    locked_venue_slots.add((day, start_str, end_str, vcode))
                    print(f"DEBUG optimize: CELL LOCKED — {ccode} stays in "
                          f"{vcode} ({day} {start_str}-{end_str})")
                elif vcode in exclusive_venue_codes:
                    # Someone else is sitting in a venue that is supposed to
                    # be 100% reserved. Flag it — the optimisation pass will
                    # relocate it since this venue is never in the pool.
                    exclusive_violations.append({
                        'course_code': ccode,
                        'venue':       vcode,
                        'day':         day,
                        'slot':        f'{start_str}–{end_str}',
                        'reason':      'course is not designated to this exclusive venue — will be relocated',
                    })
                    print(f"DEBUG optimize: EXCLUSIVE VIOLATION — {ccode} found in "
                          f"reserved-only venue {vcode} ({day} {start_str}-{end_str}); "
                          f"will be moved out")

        # ── 5. Group ALL non-merged rows by timeslot ──────────────────────
        #
        # KEY CHANGE FROM PREVIOUS VERSION:
        # Locked entries are INCLUDED in slot_groups so the rest of the
        # courses in that timeslot are processed normally.  The cell-lock
        # is enforced inside the per-course loop below, not by exclusion
        # from the group.  This means other courses in the same timeslot
        # can still be freely swapped.

        slot_groups = defaultdict(list)
        for row in all_rows:
            if row.course_allocation_id in merged_alloc_ids:
                continue   # merged groups handled separately
            key = (
                row.day or '',
                row.start_time.strftime('%H:%M'),
                row.end_time.strftime('%H:%M'),
            )
            slot_groups[key].append(row)

        print(f"DEBUG optimize: {len(all_rows)} total rows, "
              f"{len(locked_entry_ids)} cell-locked entries (skipped per-cell, "
              f"rest of their timeslot is still optimised)")

        # ── 6. Optimise each timeslot ─────────────────────────────────────
        swaps                  = []
        updates                = []
        skipped_specialization = []   # informational list of pinned cells

        for (day, start_str, end_str), slot_rows in slot_groups.items():
            if len(slot_rows) < 2:
                continue

            # Build venue pool for this slot.
            # A venue is excluded from the pool when:
            #   a) it belongs to a merged group (global), OR
            #   b) a locked entry already sits in it THIS slot
            #      (locked_venue_slots is slot-specific — same venue is
            #       available in every other slot).
            venue_map = {}
            for r in slot_rows:
                if not r.venue:
                    continue
                vcode    = r.venue.code
                slot_key = (day, start_str, end_str, vcode)
                if vcode in merged_venues:
                    continue
                if vcode in exclusive_venue_codes:
                    # Reserved-only venue (e.g. Law) — NEVER offered to any
                    # other course, whether currently occupied by its own
                    # designated course, empty, or (wrongly) occupied by
                    # something else. This is unconditional, unlike the
                    # soft-reservation check below.
                    continue
                if slot_key in locked_venue_slots:
                    # Soft-reservation venue occupied by its designated
                    # course this slot (e.g. Nursing) — excluded here only,
                    # free again in every other slot / once vacated.
                    continue
                if vcode not in venue_map:
                    venue_map[vcode] = (r.venue, r.venue.capacity or 0)

            # Need at least 2 swappable venues to do anything useful
            if len(venue_map) < 2:
                continue

            # Sort courses largest-first, venues largest-first
            courses_sorted = sorted(
                slot_rows,
                key=lambda r: r.course_allocation.number_of_students or 0,
                reverse=True,
            )
            venues_sorted = sorted(venue_map.values(), key=lambda v: v[1], reverse=True)

            assigned_venues = set()

            for course_row in courses_sorted:
                vcode = course_row.venue.code
                ccode = (course_row.course_allocation.course_code or '').strip().upper()

                # ── CELL-LEVEL LOCK CHECK ──────────────────────────────
                # This specific entry (course + venue + this timeslot) is
                # frozen.  Skip it — but mark its venue as "used" so that
                # other courses in this slot do not try to move into it.
                # All OTHER entries in this timeslot continue normally.
                if course_row.id in locked_entry_ids:
                    skipped_specialization.append({
                        'course_code': ccode,
                        'venue':       vcode,
                        'day':         day,
                        'slot':        f'{start_str}–{end_str}',
                        'reason':      'specialization cell-locked (course designated to this venue)',
                    })
                    assigned_venues.add(vcode)
                    continue

                # Skip courses in merged-group venues
                if vcode in merged_venues:
                    assigned_venues.add(vcode)
                    continue

                # ── Find best available venue ──────────────────────────
                #
                # Bonus: if this course IS designated to a specialised venue
                # that is FREE in the pool (no locked entry claimed it this
                # slot), route it there first — reunites course with its home.
                preferred_venue    = None
                preferred_capacity = 0
                for venue, capacity in venues_sorted:
                    if venue.code in assigned_venues:
                        continue
                    designated = venue_designated_codes.get(venue.code, set())
                    if ccode in designated:
                        preferred_venue    = venue
                        preferred_capacity = capacity
                        break

                best_venue          = preferred_venue
                best_venue_capacity = preferred_capacity

                if best_venue is None:
                    # No specialization preference — pick by capacity fit
                    students = course_row.course_allocation.number_of_students or 0
                    for venue, capacity in venues_sorted:
                        if venue.code in assigned_venues:
                            continue
                        if capacity >= students:
                            best_venue          = venue
                            best_venue_capacity = capacity
                            break
                    # Fall back to largest still-free venue
                    if best_venue is None:
                        for venue, capacity in venues_sorted:
                            if venue.code not in assigned_venues:
                                best_venue          = venue
                                best_venue_capacity = capacity
                                break

                # Nothing to do if no venue found or already at the best one
                if best_venue is None or course_row.venue.code == best_venue.code:
                    if best_venue:
                        assigned_venues.add(best_venue.code)
                    continue

                # Stage the swap
                swaps.append({
                    'day':            day,
                    'slot':           f'{start_str}–{end_str}',
                    'courses':        ccode or '?',
                    'from_venue':     course_row.venue.code,
                    'to_venue':       best_venue.code,
                    'students':       course_row.course_allocation.number_of_students or 0,
                    'venue_capacity': best_venue_capacity or '—',
                })
                course_row.venue = best_venue
                updates.append(course_row)
                assigned_venues.add(best_venue.code)

        # ── 7. Persist changes ────────────────────────────────────────────
        if updates:
            Timetable.objects.bulk_update(updates, ['venue'])
            print(f"DEBUG optimize: bulk_update applied to {len(updates)} rows")

        affected = len(set((s['day'], s['slot']) for s in swaps))

        extra_notes = []
        if exclusive_violations:
            extra_notes.append(
                f'{len(exclusive_violations)} course(s) were found sitting in a '
                f'reserved-only (exclusive) venue and were relocated.'
            )
        if double_bookings:
            extra_notes.append(
                f'{len(double_bookings)} venue double-booking(s) were detected — '
                f'see double_bookings for details.'
            )
        extra_msg = (' ' + ' '.join(extra_notes)) if extra_notes else ''

        if swaps:
            msg = (
                f'{len(swaps)} venue reassignment(s) applied across {affected} timeslot(s). '
                f'({len(locked_entry_ids)} specialization-locked cells left intact, '
                f'{len(exclusive_venue_codes)} exclusive venues fully protected, '
                f'{len(merged_venues)} venues reserved for merged groups.'
                ')' + extra_msg
            )
        else:
            msg = (
                'All venues are already optimally assigned — no changes needed. '
                f'({len(locked_entry_ids)} specialization-locked cells were left intact, '
                f'{len(exclusive_venue_codes)} exclusive venues fully protected.'
                ')' + extra_msg
            )

        return JsonResponse({
            'status':     'success',
            'swaps_made': len(swaps),
            'swaps':      swaps,
            'message':    msg,
            'locked_count':           len(locked_entry_ids),
            'exclusive_venue_count':  len(exclusive_venue_codes),
            'exclusive_violations':   exclusive_violations,
            'double_bookings':        double_bookings,
            'skipped_specialization': skipped_specialization,
        })

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(exc)}, status=500)