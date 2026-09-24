# ============================================================
# exam_timetable_panel.py  — v33 FIXED STATS & MERGED GROUP HANDLING
# ============================================================
# FIXES v33:
#   1. Fixed _get_scheduled_count() to properly count all scheduled courses
#   2. Added total_courses to stats for accurate comparison
#   3. Fixed merged group data structure for frontend display
#   4. Added proper error handling for delete operations
#   5. Ensured all merged/shared groups have proper IDs for deletion
# ============================================================
import re
import json
import time
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.db.models import Prefetch, Q, Subquery, OuterRef, Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from course_allocation.models import CourseAllocation, CombinedCourseGroup
from course_allocation.allocation_scope import apply_tt_scope, resolve_tt_scope
from core.group_required import group_required
from room_management.models import Venue, VenueBlock, VenueSpecialization
from timetable.models import (
    ExamSchedulerConfig,
    ExamTimetable,
    ExamTempTimetable,
    MergedCourseGroup,
    SharedVenueExamGroup,
)

# ── progress store ─────────────────────────────────────────
progress_data = {}
progress_lock = threading.Lock()


# ── collision-exemption helpers ─────────────────────────────
def _exam_panel_is_elective(alloc) -> bool:
    return bool(getattr(alloc, 'is_elective', False))


def _exam_panel_intake(alloc) -> str:
    return getattr(alloc, 'intake', 'normal') or 'normal'


def _exam_panel_is_special(alloc) -> bool:
    """True when the allocation belongs to a *Special* cohort.

    "Special" is recorded in two places and they are NOT always in sync:
      * CourseAllocation.intake == 'special'
      * AllocationSet.is_special  (the set the allocation belongs to)

    Only allocations created through allocation_scope.py copy the set's
    is_special flag into `intake`. Courses created via the COD panel,
    importers, Excel uploads, auto-allocate, etc. can sit in a Special
    allocation set while still carrying intake='normal'. Checking `intake`
    alone therefore missed those, and a Special-set course and a Normal-set
    course of the same programme/year were flagged as a collision.

    Either signal is enough to count the allocation as Special.
    """
    if _exam_panel_intake(alloc) == 'special':
        return True
    try:
        aset = getattr(alloc, 'allocation_set', None)
        return bool(aset is not None and getattr(aset, 'is_special', False))
    except Exception:
        return False


def _exam_panel_cohort(alloc) -> str:
    """'special' or 'normal' — the cohort used for collision exemption."""
    return 'special' if _exam_panel_is_special(alloc) else 'normal'


def _exam_panel_semester(alloc):
    """Curriculum semester of the allocation (ProgramCourse.semester), or None.

    This is the same value the allocation view groups by, e.g.
    "Diploma in Tourism and Hotel Management — Year 1 — Semester 2".
    """
    try:
        pc = getattr(alloc, 'program_course', None)
        return getattr(pc, 'semester', None) if pc is not None else None
    except Exception:
        return None


def _exam_panel_selection_group_id(alloc):
    try:
        sg = getattr(alloc, 'selection_group', None)
        return sg.id if sg else None
    except Exception:
        return None


_exam_panel_sg_cache = {}


def _exam_panel_get_selection_group_id_cached(alloc):
    if not alloc or not hasattr(alloc, 'id'):
        return None
    if alloc.id in _exam_panel_sg_cache:
        return _exam_panel_sg_cache[alloc.id]
    sg_id = _exam_panel_selection_group_id(alloc)
    _exam_panel_sg_cache[alloc.id] = sg_id
    return sg_id


def _exam_panel_specialization_stem_id(alloc):
    """DEPRECATED single-pointer read — kept only for any remaining callers
    that want a rough single-value estimate. Do NOT use this for exemption
    decisions: `CourseAllocation.specialization_stem` is only the "primary"
    stem pointer, and course_allocation/specialization_stem_views.py
    deliberately clears it to None the moment a course belongs to MORE than
    one stem (e.g. a cross-listed course shown under several "Also in:"
    stems in the allocation UI). Reading only this pointer makes such a
    course look like "no stem at all", so it falls through the stem-exemption
    rule below and wrongly clashes against every other stem's courses,
    including stems it has nothing to do with. Use
    `_exam_panel_specialization_stem_ids` / `_exam_panel_specialization_category_ids`
    (the real M2M membership) instead — see timetable_panel.py's
    `_specialization_stem_ids_of` for the same fix applied to the manual panel.
    """
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.id if st else None
    except Exception:
        return None


def _exam_panel_specialization_category_id(alloc):
    """DEPRECATED — see `_exam_panel_specialization_stem_id` docstring."""
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.category_id if st else None
    except Exception:
        return None


_exam_panel_stem_cache = {}


def _exam_panel_get_specialization_stem_id_cached(alloc):
    """DEPRECATED — see `_exam_panel_specialization_stem_id` docstring.
    Kept only for callers still wanting the old single-value estimate."""
    if not alloc or not hasattr(alloc, 'id'):
        return None
    if alloc.id in _exam_panel_stem_cache:
        return _exam_panel_stem_cache[alloc.id]
    st_id = _exam_panel_specialization_stem_id(alloc)
    _exam_panel_stem_cache[alloc.id] = st_id
    return st_id


_exam_panel_stem_ids_cache = {}
_exam_panel_category_ids_cache = {}


def _exam_panel_specialization_stem_ids(alloc):
    """Real SpecializationStem MEMBERSHIP (M2M), cached per allocation id.
    `alloc.specialization_stems` is the reverse of `SpecializationStem.courses`
    and always reflects EVERY stem a course belongs to, unlike the often-nulled
    singular `specialization_stem` pointer. This is what exam clash-exemption
    must use — see the docstring on `_exam_panel_specialization_stem_id`."""
    if not alloc or not hasattr(alloc, 'id'):
        return frozenset()
    if alloc.id in _exam_panel_stem_ids_cache:
        return _exam_panel_stem_ids_cache[alloc.id]
    try:
        ids = frozenset(alloc.specialization_stems.values_list('id', flat=True))
    except Exception:
        ids = frozenset()
    _exam_panel_stem_ids_cache[alloc.id] = ids
    return ids


def _exam_panel_specialization_category_ids(alloc):
    """Every SpecializationCategory this allocation's stem-memberships belong
    to (M2M-derived, plural — a cross-listed course can sit in stems from more
    than one category)."""
    if not alloc or not hasattr(alloc, 'id'):
        return frozenset()
    if alloc.id in _exam_panel_category_ids_cache:
        return _exam_panel_category_ids_cache[alloc.id]
    try:
        ids = frozenset(
            cid for cid in alloc.specialization_stems.values_list('category_id', flat=True)
            if cid is not None
        )
    except Exception:
        ids = frozenset()
    _exam_panel_category_ids_cache[alloc.id] = ids
    return ids


def _exam_panel_student_group_id(alloc):
    """Plain FK-id read — no extra query, unlike the stem/selection_group
    object lookups above."""
    return getattr(alloc, 'student_group_id', None)


def normalize_course_code(code):
    if not code:
        return ""
    s = str(code).strip().upper()
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"(\d+)[A-Za-z]$", r"\1", s)
    m = re.search(r"[A-Z]+\s*\d+", s)
    if not m:
        return s.strip()
    # Strip internal whitespace so "PPMA 214" and "PPMA214" normalise identically
    return re.sub(r"\s+", "", m.group(0))


_program_year_cache = {}


def get_program_year_key(alloc):
    if not alloc or not hasattr(alloc, 'id'):
        return None
    if alloc.id in _program_year_cache:
        return _program_year_cache[alloc.id]
    if hasattr(alloc, 'program_course') and alloc.program_course:
        prog_id = alloc.program_course.program_id
        year = alloc.program_course.year
        if prog_id and year:
            key = f"{prog_id}_year_{year}"
            _program_year_cache[alloc.id] = key
            return key
    prog_id = getattr(alloc, 'program_id', None)
    if not prog_id:
        prog_id = getattr(getattr(alloc, 'program', None), 'id', None)
    if prog_id:
        code = alloc.course_code or ''
        match = re.search(r'\d{3,4}', code)
        if match:
            year = int(match.group(0)[0])
            if 1 <= year <= 6:
                key = f"{prog_id}_year_{year}"
                _program_year_cache[alloc.id] = key
                return key
        key = f"{prog_id}_year_1"
        _program_year_cache[alloc.id] = key
        return key
    return None


def get_course_year_from_program_course(alloc):
    if alloc and hasattr(alloc, 'program_course') and alloc.program_course:
        return alloc.program_course.year
    code = alloc.course_code or ''
    match = re.search(r'\d{3,4}', code)
    if match:
        return int(match.group(0)[0])
    return 1


from course_allocation import exemption_helpers as _exh


def exam_panel_is_exempt(alloc_a, alloc_b) -> bool:
    # A Special-allocation course and a Normal-allocation course are different
    # student cohorts, even when they share a programme and year, so they may
    # be examined at the same time. Two Special courses (or two Normal ones)
    # are the same cohort and still clash-check as usual below.
    if _exam_panel_cohort(alloc_a) != _exam_panel_cohort(alloc_b):
        return True
    # Within a Special allocation, courses from different semesters of the same
    # programme year (e.g. Year 1 Sem 1 vs Year 1 Sem 2) are not sat together by
    # the same students, so they may be examined at the same time. Same-semester
    # special courses still clash-check as usual below.
    if _exam_panel_is_special(alloc_a) and _exam_panel_is_special(alloc_b):
        sem_a = _exam_panel_semester(alloc_a)
        sem_b = _exam_panel_semester(alloc_b)
        if sem_a is not None and sem_b is not None and sem_a != sem_b:
            return True
    norm_a = normalize_course_code(alloc_a.course_code)
    norm_b = normalize_course_code(alloc_b.course_code)
    if norm_a and norm_b and norm_a == norm_b:
        return True
    key_a = get_program_year_key(alloc_a)
    key_b = get_program_year_key(alloc_b)
    if key_a and key_b and key_a != key_b:
        return True

    # ── SpecializationStem takes priority over everything below ───────────
    # Checked against the FULL M2M membership (_exam_panel_specialization_
    # stem_ids), NOT the singular `specialization_stem` pointer — a course
    # cross-listed into more than one stem (shown as "Also in: ..." in the
    # allocation UI) has its pointer cleared to None, so reading only the
    # pointer made it look stem-less and left it clashing against every
    # other stem's courses, including ones it has nothing to do with. This
    # mirrors the fix already applied in timetable_panel.py's
    # `_specialization_stem_ids_of` for the manual/regular timetable.
    #   * shares at least one stem with the other course → NEVER exempt —
    #     must clash-check like ordinary mandatory exams, even if a course
    #     is *also* flagged Elective or sits in a SelectionGroup (checked
    #     here BEFORE those broader exemptions so a stem pairing can never
    #     slip through them).
    #   * both sides have stem membership but share NO stem → exempt,
    #     full stop. A student commits to exactly one stem/specialization
    #     within the program+year; two different stems are, by definition,
    #     different (non-overlapping) cohorts of students, so their exams
    #     can run concurrently regardless of whether those stems happen to
    #     sit under the same SpecializationCategory or different ones.
    #     (Previously this only exempted when the stems shared a category,
    #     which wrongly left two different-category stems clashing even
    #     though no student is ever enrolled in both.)
    #   * neither side has any stem membership at all → falls through to
    #     the checks below (an untagged course is compulsory/shared across
    #     every stem, so it must keep clash-checking against all of them)
    # NESTED ELECTIVE GROUP: alternatives of the same pick-one SelectionGroup
    # are never both sat by one student, so they may share a slot even when
    # both are members of the same stem. Checked BEFORE the shared-stem rule;
    # alternative vs core unit of the stem shares no pool and still clashes.
    if _exh.share_pool(alloc_a, alloc_b):
        return True

    stems_a = _exam_panel_specialization_stem_ids(alloc_a)
    stems_b = _exam_panel_specialization_stem_ids(alloc_b)
    if stems_a and stems_b:
        if stems_a & stems_b:
            return False  # share at least one stem -> never exempt
        return True  # different stems, either side -> exempt

    # ── StudentGroup: different, explicitly-set groups within the same
    # program/year are different cohorts of students, so their compulsory
    # exams can legitimately run concurrently. A shared course
    # (student_group=None — electives/stem courses, or anything not yet
    # split into groups) is sat by everyone and must keep clashing with
    # every group's exams, so it deliberately falls through untouched here.
    # Full mapped-group sets (primary + additional groups + groups a restricted
    # elective pool is mapped to) — same rule as the exam autoscheduler (v66/v70).
    if _exh.disjoint_groups(alloc_a, alloc_b):
        return True

    # ── Elective / SelectionGroup ──────────────────────────────────────────
    # A student sits the exam for only ONE course in a selection group, so no
    # real student ever needs two SG exams at once — safe to overlap. Matches
    # the autoscheduler's corrected semantics: any course carrying a
    # selection_group is exempt, not only against its exact group-mates.
    # v70: aligned with exam_is_collision_exempt (autoscheduler). The old
    # blanket "any elective / any selection-group course is exempt from
    # EVERYTHING" hid real clashes between an elective alternative and the
    # core units its students also sit (e.g. a stem's 10 core units vs the
    # pick-one pair). Only alternatives of the SAME pool are exempt, and that
    # is handled by _exh.share_pool above.

    # CombinedCourseGroup: allocations from different departments taught together
    # by the same lecturer are intentionally scheduled at the same time.
    if _exam_in_same_combined_group(alloc_a.id, alloc_b.id):
        return True
    return False


_exam_panel_stem_display_cache = {}


def _exam_panel_stem_display_details(alloc):
    """For messages: every SpecializationStem this allocation belongs to,
    with its name and the OTHER course codes in that same stem (the courses
    a student is committed to once they pick it) — so a collision/exemption
    message can name the actual combination stem instead of just saying
    "different stems". One extra query per distinct allocation; cached."""
    if not alloc or not hasattr(alloc, 'id'):
        return []
    if alloc.id in _exam_panel_stem_display_cache:
        return _exam_panel_stem_display_cache[alloc.id]
    details = []
    try:
        for st in alloc.specialization_stems.select_related('category').all():
            siblings = list(
                st.courses.exclude(id=alloc.id).values_list('course_code', flat=True)
            )
            details.append({
                'name': st.name,
                'category': st.category.name if st.category_id else None,
                'siblings': siblings,
            })
    except Exception:
        details = []
    _exam_panel_stem_display_cache[alloc.id] = details
    return details


def _exam_panel_format_stem_list(details):
    """Human-readable rendering of `_exam_panel_stem_display_details`
    output for use in a message, e.g. "'Economics Option'".
    Deliberately names the stem only — NOT every other course that also
    happens to sit in it — so a message about two specific colliding
    courses doesn't get buried under an unrelated course roster."""
    return "; ".join(f"'{d['name']}'" for d in details)


def _exam_panel_student_group_label(alloc):
    """Name of the StudentGroup an allocation is pinned to (primary group),
    or None when it is shared by every group of the programme/year."""
    if not alloc or not getattr(alloc, 'student_group_id', None):
        return None
    try:
        g = alloc.student_group
        return g.name if g else None
    except Exception:
        return None


def exam_panel_collision_explainer(alloc_a, alloc_b):
    """Plain-language reason WHY two same-programme-year exams count as a
    real clash, naming the combination stem (and every course in it) and the
    student group(s) involved instead of just saying "program/year".

    Returns {'text': str, 'stems': [{'name','category','courses'}],
             'group_a': str|None, 'group_b': str|None}.
    """
    parts = []
    stems_out = []

    shared = _exam_panel_specialization_stem_ids(alloc_a) & _exam_panel_specialization_stem_ids(alloc_b)
    if shared:
        try:
            for st in alloc_a.specialization_stems.filter(id__in=shared).select_related('category'):
                members = list(st.courses.values_list('course_code', flat=True))
                stems_out.append({
                    'name': st.name,
                    'category': st.category.name if st.category_id else None,
                    'courses': members,
                })
        except Exception:
            stems_out = []
        if stems_out:
            # Name the shared stem(s) only — never the full roster of other
            # courses that happen to sit in it. A collision message is about
            # THESE two courses; dumping every stem-mate onto it is exactly
            # the "crumbling everything together" that makes multi-pair
            # conflict lists unreadable.
            named = ", ".join(f"'{s['name']}'" for s in stems_out)
            stem_word = "stem" if len(stems_out) == 1 else "stems"
            parts.append(
                f"{alloc_a.course_code} and {alloc_b.course_code} share the "
                f"{named} combination {stem_word}"
            )
    else:
        sa = _exam_panel_stem_display_details(alloc_a)
        sb = _exam_panel_stem_display_details(alloc_b)
        if sa or sb:
            parts.append(
                f"{alloc_a.course_code} is in "
                f"{_exam_panel_format_stem_list(sa) or 'no stem'}, "
                f"{alloc_b.course_code} is in "
                f"{_exam_panel_format_stem_list(sb) or 'no stem'} (no shared stem to separate them)"
            )

    ga = _exam_panel_student_group_label(alloc_a)
    gb = _exam_panel_student_group_label(alloc_b)
    if ga or gb:
        if ga and gb and ga == gb:
            parts.append(f"both are for student group '{ga}'")
        elif ga and gb:
            parts.append(f"student groups: {alloc_a.course_code} \u2192 '{ga}', {alloc_b.course_code} \u2192 '{gb}'")
        else:
            solo_code, solo_g = (alloc_a.course_code, ga) if ga else (alloc_b.course_code, gb)
            open_code = alloc_b.course_code if ga else alloc_a.course_code
            parts.append(
                f"{solo_code} is for student group '{solo_g}' while {open_code} is shared by "
                f"all groups, so it clashes with every group"
            )

    if not parts:
        parts.append("same programme and year, with no stem or student group separating them")

    return {
        'text': "; ".join(parts),
        'stems': stems_out,
        'group_a': ga,
        'group_b': gb,
    }


# ── CombinedCourseGroup helpers (exam panel) ──────────────────────────────
def _exam_get_combined_group_ids(alloc_id):
    """Return the set of CombinedCourseGroup PKs that contain this allocation."""
    return set(
        CombinedCourseGroup.objects.filter(
            allocations__id=alloc_id
        ).values_list('id', flat=True)
    )


def _exam_in_same_combined_group(alloc_a_id, alloc_b_id):
    """
    Return True if both allocations share at least one CombinedCourseGroup.
    Used to exempt them from lecturer/venue/program collision detection in
    the exam timetable — they are taught (and examined) together intentionally.
    """
    groups_a = _exam_get_combined_group_ids(alloc_a_id)
    if not groups_a:
        return False
    groups_b = _exam_get_combined_group_ids(alloc_b_id)
    return bool(groups_a & groups_b)


# ── helpers ─────────────────────────────────────────────────
def get_course_family_code(course_code):
    if not course_code:
        return ""
    code = str(course_code).strip()
    m = re.match(r'^([A-Za-z]+)\s*(\d+)', code)
    if m:
        return f"{m.group(1).upper()}{m.group(2)}"
    return code.upper()


def _build_venues(include_from_timetables=True):
    blocked_codes = set(
        VenueBlock.objects
        .filter(is_active=True)
        .values_list('venue__code', flat=True)
    )
    specialized_codes = set(
        VenueSpecialization.objects
        .filter(is_active=True)
        .values_list('venues__code', flat=True)
    )
    specialized_codes.discard(None)
    venue_dict = {}
    for v in Venue.objects.all().only('code', 'capacity', 'exam_capacity'):
        exam_cap = v.exam_capacity if v.exam_capacity is not None else v.capacity
        venue_dict[v.code] = {
            'code': v.code,
            'capacity': v.capacity or 0,
            'exam_capacity': exam_cap or 0,
            'is_blocked': v.code in blocked_codes,
            'is_specialized': v.code in specialized_codes,
        }
    if include_from_timetables:
        for code in (ExamTimetable.objects
                     .filter(venue__isnull=False)
                     .values_list('venue__code', flat=True)
                     .distinct()):
            if code and code not in venue_dict:
                venue_dict[code] = {
                    'code': code,
                    'capacity': 0,
                    'exam_capacity': 0,
                    'is_blocked': code in blocked_codes,
                    'is_specialized': code in specialized_codes,
                }
    return sorted(venue_dict.values(), key=lambda v: str(v['code']).lower())


def _make_time_slots(config):
    slots = []
    current = datetime.combine(config.start_date, config.start_time)
    while current.time() < config.end_time:
        end_slot = current + timedelta(hours=config.slot_size)
        if end_slot.time() > config.end_time:
            break
        label = f"{current.strftime('%I:%M%p')} – {end_slot.strftime('%I:%M%p')}"
        slots.append((current.strftime('%H:%M'), label))
        current = end_slot + timedelta(minutes=60)
    return slots


def _excluded_set(config):
    return set(config.excluded_date_list())


def _get_effective_exam_date_range(config, tt_scope=None):
    """
    `ExamSchedulerConfig` is a single global row (id=1) shared by every
    AllocationSet — there's no per-set config. So a date that was
    perfectly normal for a PREVIOUS set's config window (before the TT
    changed the config's start_date/max_exam_days/excluded_days for the
    CURRENT set) will fall outside the current config's window purely
    because the config moved on — not because that date is actually
    "extra" for the set you're looking at right now.

    Only ExamTimetable rows in `tt_scope` (the AllocationSet(s) currently
    ticked on /timetable/dashboard/) are considered here, so a stale date
    left over from a DIFFERENT, already-scheduled AllocationSet is never
    flagged as an "overflow"/"additional" day on this set's panel.
    `tt_scope=None` keeps the old cross-set behaviour for any caller not
    yet updated to pass it.
    """
    import datetime as dt
    config_dates = config.get_excluded_date_range()
    config_date_set = {d for d, _ in config_dates}
    timetable_qs = ExamTimetable.objects.all()
    if tt_scope is not None:
        timetable_qs = apply_tt_scope(
            timetable_qs, scope=tt_scope,
            prefix="course_allocation__allocation_set",
        )
    timetable_dates = set(
        str(d)
        for d in timetable_qs.values_list('date', flat=True).distinct()
        if d
    )
    extra_dates = timetable_dates - config_date_set
    if not extra_dates:
        return config_dates, False, 0
    combined = list(config_dates)
    for s in extra_dates:
        try:
            d = dt.date.fromisoformat(s)
            combined.append((s, d.strftime('%A')))
        except ValueError:
            pass
    combined.sort(key=lambda x: x[0])
    overflow_count = len(extra_dates)
    return combined, True, overflow_count


def _scheduled_excluded_ids(tt_scope=None):
    """
    Return set of CourseAllocation IDs that should NOT appear in unscheduled list.
    Includes:
    - Courses directly in ExamTimetable
    - Base courses and merged_courses members of ANY MergedCourseGroup (published or draft)
    - Courses in ANY SharedVenueExamGroup (published or draft)
    - Non-primary members of a CombinedCourseGroup whose primary_allocation is exam-scheduled

    `tt_scope`: a snapshot from course_allocation.allocation_scope.resolve_tt_scope()
    (the AllocationSet(s) the TT ticked on /timetable/dashboard/). Every
    queryset here is narrowed to that scope so a course belonging to a
    different AllocationSet is never counted as "already scheduled" (or
    missing from) this set's unscheduled list.
    """
    # Direct ExamTimetable entries
    scheduled_ids = set(
        apply_tt_scope(
            ExamTimetable.objects.exclude(course_allocation__isnull=True),
            scope=tt_scope, prefix="course_allocation__allocation_set",
        ).values_list('course_allocation_id', flat=True).distinct()
    )

    # All merged groups (published + draft) — base and member courses
    all_merged = apply_tt_scope(
        MergedCourseGroup.objects.prefetch_related('merged_courses'),
        scope=tt_scope, prefix="base_course__allocation_set",
    )
    merged_base = set(all_merged.values_list('base_course_id', flat=True))
    merged_members = set()
    for group in all_merged:
        merged_members.update(group.merged_courses.values_list('id', flat=True))

    # All shared venue groups (published + draft)
    shared_ids = set(
        id_
        for id_ in apply_tt_scope(
            SharedVenueExamGroup.objects, scope=tt_scope,
            prefix="course_allocations__allocation_set",
        ).values_list('course_allocations__id', flat=True)
        if id_ is not None
    )

    # CombinedCourseGroup: when a primary allocation is exam-scheduled,
    # all other allocations in that combined group are implicitly covered
    # (they sit the same exam in the same venue at the same time).
    combined_ids = set()
    exam_scheduled = scheduled_ids | merged_base | merged_members | shared_ids
    for group in CombinedCourseGroup.objects.prefetch_related('allocations').select_related('primary_allocation'):
        primary_id = group.primary_allocation_id
        if primary_id and primary_id in exam_scheduled:
            for alloc_id in group.allocations.values_list('id', flat=True):
                combined_ids.add(alloc_id)

    return scheduled_ids | merged_base | merged_members | shared_ids | combined_ids


def _get_scheduled_count(tt_scope=None):
    """
    Count total 'scheduled' courses - deduplicated by CourseAllocation ID.

    `tt_scope`: same scope snapshot as `_scheduled_excluded_ids()` — every
    queryset here is narrowed to the TT's active AllocationSet(s) so the
    count reflects only the set(s) currently in scope.
    """
    # Get all CourseAllocation IDs that are scheduled in any way
    scheduled_ids = set()
    
    # Direct ExamTimetable entries
    direct_ids = set(
        apply_tt_scope(
            ExamTimetable.objects.exclude(course_allocation__isnull=True),
            scope=tt_scope, prefix="course_allocation__allocation_set",
        ).values_list('course_allocation_id', flat=True)
    )
    scheduled_ids.update(direct_ids)
    
    # Published merged groups - all merged courses
    for group in apply_tt_scope(
        MergedCourseGroup.objects.filter(published=True),
        scope=tt_scope, prefix="base_course__allocation_set",
    ):
        # Add base course
        if group.base_course_id:
            scheduled_ids.add(group.base_course_id)
        # Add all merged courses
        member_ids = group.merged_courses.values_list('id', flat=True)
        scheduled_ids.update(member_ids)
    
    # Published shared groups - all course allocations
    for group in apply_tt_scope(
        SharedVenueExamGroup.objects.filter(published=True),
        scope=tt_scope, prefix="course_allocations__allocation_set",
    ).distinct():
        shared_ids = group.course_allocations.values_list('id', flat=True)
        scheduled_ids.update(shared_ids)
    
    # Return count of unique CourseAllocation IDs
    return len(scheduled_ids)


# ═════════════════════════════════════════════════════════════
# VIEW 1 — Shell render
# ═════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_timetable_panel(request):
    config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)
    time_slots    = _make_time_slots(config)
    excluded_list = _excluded_set(config)
    all_venues    = _build_venues()

    tt_scope = resolve_tt_scope(request)
    effective_range, has_overflow, overflow_count = _get_effective_exam_date_range(config, tt_scope)

    if request.method == 'POST' and request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return _handle_ajax_add(request, config)

    context = {
        'heading':              'Exam Timetable Management Panel',
        'time_slots':           time_slots,
        'existing_venues':      all_venues,
        'config':               config,
        'date_range':           effective_range,
        'date_excluded_range':  effective_range,
        'excluded_list':        sorted(excluded_list),
        'weekdays':             [d for d, _ in effective_range],
        'has_overflow_days':    has_overflow,
        'overflow_day_count':   overflow_count,
        'show_loading_overlay': True,
        'navbar_links': {
            'Main Timetable':     'timetable_panel',
            'Download Exam PDF':  'download_exam_timetable',
            'Export Exam CSV':    'export_exam_timetable',
            'Autoschedule Exams': 'exam_autoscheduler_home',
            'Delete All':         'delete_all_exam_timetables',
        },
    }
    return render(request, 'timetable/schedule_main_exam.html', context)


# ═════════════════════════════════════════════════════════════
# VIEW 2 — Staged data loader
# ═════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def load_exam_timetable_data(request):
    try:
        stage = request.GET.get('stage', 'initial')
        config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)
        # Resolve once per request: the AllocationSet(s) the TT ticked on
        # /timetable/dashboard/ (falls back to the "eligible" gate — no
        # set yet, legacy, or submitted to TT — if nothing's been picked
        # this session). Every stage below scopes its querysets to this,
        # so the panel only ever shows the same population the exam
        # autoscheduler would act on.
        tt_scope = resolve_tt_scope(request)

        # ── STAGE: initial (20%) ──────────────────────────────
        if stage == 'initial':
            venues_data = _build_venues()
            timetable_count = apply_tt_scope(
                ExamTimetable.objects, scope=tt_scope,
                prefix="course_allocation__allocation_set",
            ).count()
            excluded = _scheduled_excluded_ids(tt_scope)
            # Scope to the same population the exam autoscheduler considers:
            # allocations that are submission-allowed and have at least 1 student.
            from django.db.models import Q as _Q
            _exam_pool = apply_tt_scope(
                CourseAllocation.objects.filter(
                    _Q(department__submission_control__allow_submission_to_tt=True)
                    | _Q(department__submission_control__isnull=True)
                ).filter(number_of_students__gt=0),
                scope=tt_scope,
            )
            unscheduled_count = _exam_pool.exclude(id__in=excluded).count()
            scheduled_count = _get_scheduled_count(tt_scope)
            total_courses = _exam_pool.count()

            effective_range, has_overflow, overflow_count = _get_effective_exam_date_range(config, tt_scope)

            return JsonResponse({
                'progress': 20,
                'status': 'loading',
                'message': f'Loaded {len(venues_data)} venues, {timetable_count} timetable entries',
                'data': {
                    'venues': venues_data,
                    'stats': {
                        'timetable_count': scheduled_count,
                        'unscheduled_count': unscheduled_count,
                        'venues_count': len(venues_data),
                        'total_courses': total_courses,
                    },
                    'effective_date_range': [
                        {'date': d, 'day': name, 'overflow': d not in set(dd for dd, _ in config.get_excluded_date_range())}
                        for d, name in effective_range
                    ],
                    'has_overflow_days': has_overflow,
                    'overflow_day_count': overflow_count,
                },
            })

        # ── STAGE: timetables (40%) ──────────────────────────
        elif stage == 'timetables':
            timetables = (
                apply_tt_scope(
                    ExamTimetable.objects, scope=tt_scope,
                    prefix="course_allocation__allocation_set",
                )
                .select_related(
                    'course_allocation__lecturer',
                    'course_allocation__program',
                    'course_allocation__program_course',
                    'course_allocation__department',
                    'course_allocation__selection_group',
                    'venue',
                )
                .only(
                    'id', 'date', 'day', 'start_time', 'end_time', 'allocated_students',
                    'course_allocation__id',
                    'course_allocation__course_code',
                    'course_allocation__course_name',
                    'course_allocation__number_of_students',
                    'course_allocation__lecturer__name',
                    'course_allocation__program__name',
                    'course_allocation__program_course__year',
                    'course_allocation__program_course__program_id',
                    'course_allocation__is_elective',
                    'course_allocation__intake',
                    'course_allocation__selection_group__id',
                    'course_allocation__department__name',
                    'venue__code',
                    'venue__capacity',
                    'venue__exam_capacity',
                )
                .order_by('date', 'start_time')
            )
            # Pre-compute combined-group membership for every allocation appearing
            # in this timetable, so entries whose course is examined together with
            # others (as one CombinedCourseGroup) can be labelled "Combined Group"
            # instead of being mistaken for a capacity-driven venue split, and can
            # show the group's TOTAL headcount rather than just this row's share.
            alloc_ids_in_view = list(
                apply_tt_scope(
                    ExamTimetable.objects, scope=tt_scope,
                    prefix="course_allocation__allocation_set",
                ).values_list('course_allocation_id', flat=True).distinct()
            )
            combined_group_by_alloc = {}
            for grp in (
                CombinedCourseGroup.objects
                .filter(allocations__id__in=alloc_ids_in_view)
                .prefetch_related('allocations')
                .distinct()
            ):
                grp_alloc_ids = list(grp.allocations.values_list('id', flat=True))
                grp_total = sum(
                    (a.number_of_students or 0)
                    for a in grp.allocations.only('number_of_students')
                )
                for aid in grp_alloc_ids:
                    # An allocation could technically sit in more than one group —
                    # first match wins, which mirrors _exam_in_same_combined_group.
                    combined_group_by_alloc.setdefault(aid, {
                        'id': grp.id,
                        'code': grp.group_code,
                        'total_students': grp_total,
                    })

            data = []
            for t in timetables:
                alloc = t.course_allocation
                year = None
                if alloc and hasattr(alloc, 'program_course') and alloc.program_course:
                    year = alloc.program_course.year
                combined_info = combined_group_by_alloc.get(alloc.id) if alloc else None

                data.append({
                    'id': t.id,
                    'date': str(t.date),
                    'day': t.day or '',
                    'start_time': t.start_time.strftime('%H:%M') if t.start_time else '00:00',
                    'end_time': t.end_time.strftime('%H:%M') if t.end_time else '00:00',
                    'venue': t.venue.code if t.venue else 'Unknown',
                    'venue_id': t.venue.id if t.venue else None,
                    'venue_exam_capacity': (
                        (t.venue.exam_capacity if t.venue.exam_capacity is not None else t.venue.capacity) or 0
                    ) if t.venue else 0,
                    'course_code': alloc.course_code if alloc else 'Unknown',
                    'normalized_code': normalize_course_code(alloc.course_code) if alloc else '',
                    'course_name': (alloc.course_name or '') if alloc else '',
                    'lecturer': getattr(getattr(alloc, 'lecturer', None), 'name', 'Unassigned') if alloc else 'Unassigned',
                    'program': getattr(getattr(alloc, 'program', None), 'name', 'N/A') if alloc else 'N/A',
                    'year': year,
                    'is_elective': alloc.is_elective if alloc else False,
                    'intake': alloc.intake if alloc else 'normal',
                    'selection_group_id': alloc.selection_group_id if alloc and alloc.selection_group else None,
                    'students': (
                        t.allocated_students if t.allocated_students is not None
                        else ((alloc.number_of_students or 0) if alloc else 0)
                    ),
                    'allocation_id': alloc.id if alloc else None,
                    'combined_group_id': combined_info['id'] if combined_info else None,
                    'combined_group_code': combined_info['code'] if combined_info else None,
                    'combined_group_total_students': combined_info['total_students'] if combined_info else None,
                    'type': 'direct',
                })
            return JsonResponse({
                'progress': 40,
                'status': 'processing',
                'message': f'Loaded {len(data)} timetable entries',
                'data': {'timetables': data},
            })

        # ── STAGE: unscheduled (60%) ─────────────────────────
        elif stage == 'unscheduled':
            excluded = _scheduled_excluded_ids(tt_scope)
            allocations = (
                apply_tt_scope(CourseAllocation.objects, scope=tt_scope)
                .select_related('lecturer', 'program', 'department', 'program_course', 'selection_group')
                .only(
                    'id', 'course_code', 'course_name', 'number_of_students',
                    'lecturer__name', 'program__name', 'department__name',
                    'program_course__year', 'program_course__program_id',
                    'is_elective', 'intake', 'selection_group__id',
                )
                .exclude(id__in=excluded)
                .order_by('course_code')
            )
            data = [{
                'id': a.id,
                'course_code': a.course_code,
                'normalized_code': normalize_course_code(a.course_code),
                'course_name': a.course_name or '',
                'lecturer': getattr(getattr(a, 'lecturer', None), 'name', 'Unassigned'),
                'program': getattr(getattr(a, 'program', None), 'name', 'N/A'),
                'department': getattr(getattr(a, 'department', None), 'name', 'N/A'),
                'year': a.program_course.year if a.program_course else None,
                'is_elective': a.is_elective,
                'intake': a.intake,
                'selection_group_id': a.selection_group_id if a.selection_group else None,
                'students': a.number_of_students or 0,
            } for a in allocations]
            return JsonResponse({
                'progress': 60,
                'status': 'optimizing',
                'message': f'Loaded {len(data)} unscheduled courses',
                'data': {'unscheduled': data, 'ready_for_display': True},
            })

        # ── STAGE: merged groups (75%) ───────────────────────
        elif stage == 'merged':
            # Return ALL groups (published + draft) with full details
            merged_qs = (
                apply_tt_scope(
                    MergedCourseGroup.objects, scope=tt_scope,
                    prefix="base_course__allocation_set",
                )
                .select_related('base_course', 'venue', 'exam_timetable_entry', 'exam_temp_timetable_entry')
                .prefetch_related('merged_courses__lecturer')
                .order_by('date', 'start_time')
            )
            shared_qs = (
                apply_tt_scope(
                    SharedVenueExamGroup.objects, scope=tt_scope,
                    prefix="course_allocations__allocation_set",
                )
                .distinct()
                .select_related('venue', 'exam_timetable_entry', 'exam_temp_timetable_entry')
                .prefetch_related('course_allocations__lecturer')
                .order_by('date', 'start_time')
            )
            merged_data = []
            for m in merged_qs:
                active_entry = m.exam_timetable_entry if m.published else m.exam_temp_timetable_entry
                merged_data.append({
                    'id': m.id,
                    'base_code': m.base_course.course_code if m.base_course else '-',
                    'merged_code': m.merged_code or '',
                    'total_students': m.total_students or 0,
                    'date': str(m.date) if m.date else '',
                    'start_time': m.start_time.strftime('%H:%M') if m.start_time else '',
                    'end_time': m.end_time.strftime('%H:%M') if m.end_time else '',
                    'venue': m.venue.code if m.venue else '-',
                    'venue_id': m.venue.id if m.venue else None,
                    'published': m.published,
                    'exam_timetable_entry_id': m.exam_timetable_entry_id,
                    'exam_temp_timetable_entry_id': m.exam_temp_timetable_entry_id,
                    'active_timetable_entry_id': active_entry.id if active_entry else None,
                    'base_course_id': m.base_course.id if m.base_course else None,
                    'merged_courses': [
                        {
                            'id': c.id,
                            'code': c.course_code,
                            'normalized_code': normalize_course_code(c.course_code),
                            'lecturer': getattr(getattr(c, 'lecturer', None), 'name', '-'),
                            'students': c.number_of_students or 0,
                        }
                        for c in m.merged_courses.all()
                    ],
                })
            shared_data = []
            for sg in shared_qs:
                active_entry = sg.exam_timetable_entry if sg.published else sg.exam_temp_timetable_entry
                shared_data.append({
                    'id': sg.id,
                    'venue': sg.venue.code if sg.venue else '-',
                    'venue_id': sg.venue.id if sg.venue else None,
                    'date': str(sg.date) if sg.date else '',
                    'day': sg.day or '',
                    'start_time': sg.start_time.strftime('%H:%M') if sg.start_time else '',
                    'end_time': sg.end_time.strftime('%H:%M') if sg.end_time else '',
                    'total_students': sg.total_students or 0,
                    'published': sg.published,
                    'exam_timetable_entry_id': sg.exam_timetable_entry_id,
                    'exam_temp_timetable_entry_id': sg.exam_temp_timetable_entry_id,
                    'active_timetable_entry_id': active_entry.id if active_entry else None,
                    'courses': [
                        {
                            'id': ca.id,
                            'code': ca.course_code,
                            'normalized_code': normalize_course_code(ca.course_code),
                            'lecturer': getattr(getattr(ca, 'lecturer', None), 'name', '-'),
                            'students': ca.number_of_students or 0,
                        }
                        for ca in sg.course_allocations.all()
                    ],
                })
            return JsonResponse({
                'progress': 75,
                'status': 'loading',
                'message': f'Loaded {len(merged_data)} merged, {len(shared_data)} shared groups',
                'data': {'merged_groups': merged_data, 'shared_groups': shared_data},
            })

        # ── STAGE: complete (100%) ───────────────────────────
        elif stage == 'complete':
            try:
                conflicts_resp = exam_conflicts_api(request)
                conflicts = json.loads(conflicts_resp.content)
            except Exception as e:
                conflicts = {
                    'lecturer_conflicts': [], 'program_conflicts': [],
                    'venue_conflicts': [], 'course_family_conflicts': [],
                    'total_conflicts': 0, 'error': str(e),
                }
            
            # FIX: Use consistent calculation method scoped to exam pool
            from django.db.models import Q as _Q
            _exam_pool = apply_tt_scope(
                CourseAllocation.objects.filter(
                    _Q(department__submission_control__allow_submission_to_tt=True)
                    | _Q(department__submission_control__isnull=True)
                ).filter(number_of_students__gt=0),
                scope=tt_scope,
            )
            all_courses = _exam_pool.count()
            excluded = _scheduled_excluded_ids(tt_scope)
            scheduled_count = len(excluded)  # Count of unique scheduled course IDs
            unscheduled_count = _exam_pool.exclude(id__in=excluded).count()
            
            return JsonResponse({
                'progress': 100,
                'status': 'completed',
                'message': 'Exam workspace fully loaded',
                'data': {
                    'conflicts': conflicts,
                    'summary': {
                        'total_timetables': scheduled_count,
                        'total_unscheduled': unscheduled_count,
                        'total_courses': all_courses,
                        'conflict_count': conflicts.get('total_conflicts', 0),
                    },
                    'loading_complete': True,
                },
            })

        return JsonResponse({'progress': 0, 'status': 'error', 'message': 'Unknown stage'})

    except Exception as e:
        import traceback
        return JsonResponse({
            'progress': 0, 'status': 'error',
            'message': f'Error: {str(e)}',
            'data': {'traceback': traceback.format_exc()},
        })


# ═════════════════════════════════════════════════════════════
# VIEW — Placement Assist API
# ═════════════════════════════════════════════════════════════
# Powers the "click a course in the Add-Entry dropdown → the grid highlights
# where it can go" helper. Reuses the exact same exam_panel_is_exempt /
# normalize_course_code / get_program_year_key logic already trusted by the
# conflicts API above, so a slot this endpoint calls "safe" is guaranteed to
# agree with what exam_conflicts_api would (or wouldn't) flag as a clash.
def _classify_entry_for_allocation(selected_alloc, entry_alloc):
    """Returns 'family', 'safe', 'clash', or None (truly unrelated program/year)."""
    if not entry_alloc:
        return None
    norm_sel = normalize_course_code(selected_alloc.course_code)
    norm_ent = normalize_course_code(entry_alloc.course_code)
    if norm_sel and norm_ent and norm_sel == norm_ent:
        # Same course already scheduled elsewhere — e.g. a sibling room of a
        # venue split, or (for a not-yet-scheduled allocation) a duplicate
        # course code in another program. Either way, worth seeing.
        return 'family'
    key_sel = get_program_year_key(selected_alloc)
    key_ent = get_program_year_key(entry_alloc)
    if key_sel and key_ent and key_sel == key_ent:
        if exam_panel_is_exempt(selected_alloc, entry_alloc):
            # Different stream/elective/selection-group/specialization within
            # the same program+year — no student sits both, so this slot can
            # be safely shared with the course being placed.
            return 'safe'
        # Same program+year, NOT exempt: the same students would need to sit
        # both exams at once. Distinct from a merely unrelated course below —
        # the frontend must never show these two the same way.
        return 'clash'
    # Different program/year entirely — has nothing to do with this placement.
    return None


def _exam_panel_program_label(alloc):
    """'(CODE) Program Name' for an allocation's program, dropping the
    bracketed code when the program has none set, and returning '' if the
    allocation has no program at all. Used so a clash message always says
    WHICH program/year it's about, instead of the vague 'this program/year'
    — otherwise two clash messages for the same course (one against a
    stem-mate, one against a plain program/year duplicate) read as if
    they're describing different, inconsistent situations."""
    program = getattr(alloc, 'program', None)
    if not program:
        return ''
    name = getattr(program, 'name', None) or ''
    code = getattr(program, 'code', None)
    if code:
        return f"({code}) {name}".strip()
    return name


def _exam_panel_wrap_program(label):
    """Wrap a program label in parentheses for mid-sentence use, unless it
    already has its own bracketed code (e.g. '(BCOM) Bachelor of Commerce')
    — in which case it's left as-is rather than double-wrapped."""
    if not label:
        return ''
    if label.startswith('('):
        return label
    return f"({label})"


def _exam_panel_short_clash_reason(selected_alloc, other_alloc):
    """Short, on-cell watermark text for WHY two same-program/year exams
    clash, in priority order:
      1. Share a SpecializationStem (combination stem)                    -> name that stem
      2. Only ONE side has any stem tag at all                            -> the untagged
         course is compulsory/shared across EVERY stream in that program
         (that's exactly why `exam_panel_is_exempt` never even reaches the
         stem comparison for it — see its docstring), so say so explicitly
         instead of leaving it looking like a bare, unexplained
         program/year clash next to a stem-named one for the same course.
         (Two DIFFERENT stems on both sides are never a clash at all —
         `exam_panel_is_exempt` exempts that pairing outright, so this
         function is never even called for it.)
      3. No stem on either side, but both pin to the SAME explicit
         StudentGroup                                                     -> name that group
      4. None of the above                                                -> plain program/year
    Every case names the actual program (code + name, or just name if the
    program has no code) so every message reads as a fact about the SAME
    program, never as if one allocation has a rule the other doesn't.
    Returns (short_label, full_title) — short_label for the on-cell
    watermark, full_title for a hover tooltip with the full detail
    (reusing exam_panel_collision_explainer's complete sentence).
    """
    explain = exam_panel_collision_explainer(selected_alloc, other_alloc)
    stems = explain.get('stems') or []
    other_code = other_alloc.course_code
    sel_code = selected_alloc.course_code
    program_label = _exam_panel_program_label(selected_alloc) or _exam_panel_program_label(other_alloc)
    prog_suffix = f" in {program_label}" if program_label else ""

    if stems:
        stem_names = "/".join(s['name'] for s in stems)
        wrapped_prog = _exam_panel_wrap_program(program_label)
        who = f"{stem_names} {wrapped_prog}".strip() if wrapped_prog else stem_names
        short = f"{who} has {other_code} at this time"
        return short, explain.get('text', short)

    stems_sel = _exam_panel_specialization_stem_ids(selected_alloc)
    stems_other = _exam_panel_specialization_stem_ids(other_alloc)
    if stems_sel and not stems_other:
        # The already-scheduled course carries no stem tag at all, so it's
        # treated as compulsory/shared by every stream in the program,
        # including the stem the course being placed belongs to — that's
        # the real reason it clashes, not just "same program/year".
        sel_names = "/".join(d['name'] for d in _exam_panel_stem_display_details(selected_alloc)) or "your stem"
        short = f"{other_code} is a shared course taken by every stream (incl. {sel_names}){prog_suffix} \u2014 clashes at this time"
        return short, explain.get('text', short)
    if stems_other and not stems_sel:
        # Reverse of the above: the course being PLACED has no stem tag, so
        # it's the shared/compulsory one here, clashing with a specific
        # stream's course already scheduled.
        other_names = "/".join(d['name'] for d in _exam_panel_stem_display_details(other_alloc)) or "its stem"
        short = f"{sel_code} is a shared course taken by every stream (incl. {other_names}){prog_suffix} \u2014 clashes with {other_code} at this time"
        return short, explain.get('text', short)
    # NOTE: stems_sel and stems_other BOTH being truthy with no overlap is
    # not reachable here — exam_panel_is_exempt already exempts that case
    # (different stems -> different, non-overlapping cohorts -> never a
    # real clash), so _classify_entry_for_allocation never labels it
    # 'clash' in the first place.

    ga, gb = explain.get('group_a'), explain.get('group_b')
    if ga and gb and ga == gb:
        who = f"Student group '{ga}' {program_label}".strip() if program_label else f"Student group '{ga}'"
        short = f"{who} has {other_code} at this time"
    else:
        who = program_label or "This program/year"
        short = f"{who} has {other_code} at this time"
    return short, explain.get('text', short)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_placement_hints_api(request):
    """
    Given ?allocation_id=<id> for a course the user is about to schedule,
    classify every currently-scheduled ExamTimetable entry (within the
    active tt_scope) as:
      - 'family': the same course, already sitting somewhere — surface it
        so the user can place a split-venue sibling next to it.
      - 'safe':   a different course in the same program+year that student
        cohorts never overlap with (different stream/elective/selection
        group) — sharing its slot causes no real clash.
      - 'clash':  a different course in the same program+year that WOULD
        clash — same students, same slot. Not a placement option.
    Anything else (a totally unrelated program/year) isn't returned at all;
    the frontend treats those as background noise for this decision.
    """
    allocation_id = request.GET.get('allocation_id')
    if not allocation_id:
        return JsonResponse({'status': 'error', 'message': 'allocation_id required'}, status=400)

    selected_alloc = get_object_or_404(
        CourseAllocation.objects.select_related('program', 'program_course'),
        id=allocation_id,
    )

    tt_scope = resolve_tt_scope(request)
    entries = (
        apply_tt_scope(
            ExamTimetable.objects, scope=tt_scope,
            prefix="course_allocation__allocation_set",
        )
        .select_related(
            'course_allocation__program',
            'course_allocation__program_course',
        )
        .only(
            'id', 'date', 'start_time', 'course_allocation__id', 'course_allocation__course_code',
            'course_allocation__program_id', 'course_allocation__program_course__year',
            'course_allocation__program_course__program_id',
        )
    )

    family_ids, safe_ids, clash_ids = [], [], []
    clash_reasons = {}   # entry.id (str) -> {'label':..., 'title':...}
    # A 'clash' entry means the selected course cannot share ITS timeslot
    # with that entry's course, full stop — that's a same-program/year
    # clash, and it doesn't matter which room either one sits in. So an
    # otherwise-empty cell at that same date+time in a DIFFERENT venue is
    # not actually placeable either; it would just recreate the identical
    # clash in another room. Collect those (date, start_time) pairs, each
    # with its own reason, so the frontend can stop stamping "you can place
    # here" stars on slots that are blocked at the timeslot level, and can
    # explain WHY (shared combination stem > shared student group > plain
    # program/year) instead of a generic "Clash risk".
    clash_slot_reasons = {}  # "date|HH:MM" -> {'label':..., 'title':...}
    for entry in entries:
        alloc = entry.course_allocation
        if not alloc:
            continue
        # NOTE: entries belonging to this exact allocation are NOT skipped.
        # For a split course, some of its rooms may already be scheduled
        # while the user is placing the remaining room(s) for the SAME
        # allocation — those sibling rows must still come back as 'family'
        # (same course_code match below) so the panel highlights them and
        # scrolls to them, instead of silently falling through to
        # "unrelated". Skipping same-id entries here was the actual cause
        # of split siblings showing no watermark at all.
        label = _classify_entry_for_allocation(selected_alloc, alloc)
        if label == 'family':
            family_ids.append(entry.id)
        elif label == 'safe':
            safe_ids.append(entry.id)
        elif label == 'clash':
            clash_ids.append(entry.id)
            short_label, full_title = _exam_panel_short_clash_reason(selected_alloc, alloc)
            clash_reasons[str(entry.id)] = {'label': short_label, 'title': full_title}
            if entry.date and entry.start_time:
                slot_key = f"{entry.date.isoformat()}|{entry.start_time.strftime('%H:%M')}"
                # First reason found for a slot wins the display text; if
                # more than one distinct clash lands on the same slot, note
                # it's not the only one rather than silently overwriting.
                if slot_key not in clash_slot_reasons:
                    clash_slot_reasons[slot_key] = {'label': short_label, 'title': full_title}
                elif clash_slot_reasons[slot_key]['label'] != short_label:
                    clash_slot_reasons[slot_key]['title'] += f"; also: {full_title}"

    return JsonResponse({
        'status': 'success',
        'allocation_id': selected_alloc.id,
        'course_code': selected_alloc.course_code,
        'family_ids': family_ids,
        'safe_ids': safe_ids,
        'clash_ids': clash_ids,
        'clash_reasons': clash_reasons,
        'clash_slot_reasons': clash_slot_reasons,
    })


# ═════════════════════════════════════════════════════════════
# VIEW 3 — Conflicts API
# ═════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_conflicts_api(request):
    try:
        timetables = (
            apply_tt_scope(
                ExamTimetable.objects, request=request,
                prefix="course_allocation__allocation_set",
            )
            .select_related(
                'course_allocation',
                'course_allocation__program',
                'course_allocation__program_course',
                'course_allocation__lecturer',
                'course_allocation__selection_group',
                'course_allocation__specialization_stem',
                'course_allocation__student_group',
                'course_allocation__allocation_set',
                'venue',
            )
            .only(
                'id', 'date', 'start_time', 'end_time', 'allocated_students',
                'course_allocation__course_code',
                'course_allocation__program__id',
                'course_allocation__program__name',
                'course_allocation__program_course__year',
                'course_allocation__program_course__semester',
                'course_allocation__program_course__program_id',
                'course_allocation__lecturer__id',
                'course_allocation__lecturer__name',
                'course_allocation__is_elective',
                'course_allocation__intake',
                'course_allocation__allocation_set__id',
                'course_allocation__allocation_set__is_special',
                'course_allocation__selection_group__id',
                'course_allocation__specialization_stem__id',
                'course_allocation__specialization_stem__category_id',
                'course_allocation__student_group__id',
                'venue__code',
            )
        )

        schedule_map = defaultdict(list)
        for t in timetables:
            key = f"{t.date}_{t.start_time.strftime('%H:%M') if t.start_time else '00:00'}"
            schedule_map[key].append(t)

        lecturer_conflicts = []
        program_conflicts = []
        venue_conflicts = []
        course_fam_conflicts = []
        family_map = {}

        for timeslot_key, entries in schedule_map.items():
            date_str, start_time = timeslot_key.split('_')

            if len(entries) > 1:
                # Lecturer conflicts
                lec_groups = defaultdict(list)
                for e in entries:
                    lec = getattr(e.course_allocation, 'lecturer', None)
                    if lec:
                        lec_groups[lec.id].append(e)
                for lid, group in lec_groups.items():
                    if len(group) > 1:
                        norm_codes = {
                            normalize_course_code(g.course_allocation.course_code or '')
                            for g in group
                        }
                        if len(norm_codes) == 1:
                            continue
                        family_codes_in_group = {
                            get_course_family_code(g.course_allocation.course_code or '')
                            for g in group
                        }
                        if len(family_codes_in_group) == 1:
                            continue
                        venues_in_group = {
                            g.venue.code if g.venue else None
                            for g in group
                        }
                        if len(venues_in_group) == 1 and None not in venues_in_group:
                            continue
                        # CombinedCourseGroup: filter out pairs that are combined
                        real_entries = []
                        for i, gi in enumerate(group):
                            for j, gj in enumerate(group):
                                if j <= i:
                                    continue
                                if gi.course_allocation and gj.course_allocation:
                                    if _exam_in_same_combined_group(
                                        gi.course_allocation.id, gj.course_allocation.id
                                    ):
                                        continue
                                real_entries.append(gi)
                                real_entries.append(gj)
                        seen_r = set()
                        real_entries = [e for e in real_entries if e.id not in seen_r and not seen_r.add(e.id)]
                        if len(real_entries) < 2:
                            continue
                        lec_name = group[0].course_allocation.lecturer.name
                        lecturer_conflicts.append({
                            'type': 'lecturer_conflict',
                            'date': date_str,
                            'timeslot': start_time,
                            'lecturer': lec_name,
                            'courses': [
                                {
                                    'course_code': g.course_allocation.course_code,
                                    'normalized_code': normalize_course_code(g.course_allocation.course_code),
                                    'venue': g.venue.code if g.venue else '–',
                                }
                                for g in real_entries
                            ],
                            'message': f'Lecturer {lec_name} double-booked at {start_time} on {date_str}',
                        })

                # Program conflict detection
                prog_year_groups = defaultdict(list)
                for e in entries:
                    alloc = e.course_allocation
                    if not alloc or not alloc.program:
                        continue
                    prog_id = alloc.program_id
                    year = None
                    if alloc.program_course:
                        year = alloc.program_course.year
                    else:
                        code = alloc.course_code or ''
                        match = re.search(r'\d{3,4}', code)
                        if match:
                            year = int(match.group(0)[0])
                    if year:
                        key = f"{prog_id}_year_{year}"
                    else:
                        key = f"{prog_id}_unknown"
                    prog_year_groups[key].append(e)

                for py_key, group in prog_year_groups.items():
                    if len(group) < 2:
                        continue
                    course_code_groups = defaultdict(list)
                    for e in group:
                        norm = normalize_course_code(e.course_allocation.course_code)
                        course_code_groups[norm].append(e)
                    if len(course_code_groups) == 1:
                        continue
                    real_conflict_entries = []
                    conflict_pairs = {}  # frozenset({id_i, id_j}) -> detail dict
                    for norm_code, code_group in course_code_groups.items():
                        if len(code_group) > 1:
                            # Multiple ExamTimetable rows for the SAME normalised course
                            # code means this exam is split across venues (e.g. EDCI 332
                            # in Hall A + Hall B simultaneously).  This is intentional
                            # scheduling — NOT a conflict.  Skip entirely.
                            continue
                        # Single entry for this code.  Check only against entries from
                        # genuinely DIFFERENT course codes in the same slot.
                        ei = code_group[0]
                        for other_norm, other_group in course_code_groups.items():
                            if other_norm == norm_code:
                                continue
                            # If the other course also has multiple split-venue rows,
                            # it is a valid split exam too — not a conflict.
                            if len(other_group) > 1:
                                continue
                            ej = other_group[0]
                            if not exam_panel_is_exempt(ei.course_allocation, ej.course_allocation):
                                real_conflict_entries.append(ei)
                                real_conflict_entries.append(ej)
                                pk = frozenset((ei.id, ej.id))
                                if pk not in conflict_pairs:
                                    ex_ = exam_panel_collision_explainer(ei.course_allocation, ej.course_allocation)
                                    conflict_pairs[pk] = {
                                        'course_a': ei.course_allocation.course_code,
                                        'course_b': ej.course_allocation.course_code,
                                        'why': ex_['text'],
                                        'stems': [s_['name'] for s_ in ex_['stems']],
                                        'student_group_a': ex_['group_a'],
                                        'student_group_b': ex_['group_b'],
                                    }
                    seen_ids = set()
                    real_conflict_entries = [
                        e for e in real_conflict_entries
                        if e.id not in seen_ids and not seen_ids.add(e.id)
                    ]
                    if len(real_conflict_entries) >= 2:
                        prog_name = group[0].course_allocation.program.name
                        year_part = py_key.split('_')[-1] if '_year_' in py_key else 'unknown'

                        # Human-readable "<Weekday> <time>" for the per-pair
                        # lines below, e.g. "Wednesday 11:30 AM".
                        try:
                            _weekday = datetime.strptime(date_str, '%Y-%m-%d').strftime('%A')
                        except Exception:
                            _weekday = ''
                        try:
                            _time_label = datetime.strptime(start_time, '%H:%M').strftime('%I:%M %p').lstrip('0')
                        except Exception:
                            _time_label = start_time
                        when_label = f"{_weekday} {_time_label}".strip()

                        def _pair_collision_line(d, _when=when_label):
                            """One specific, self-contained line naming ONLY
                            this pair of courses and, if that's the reason
                            they clash, the ONE stem they actually share —
                            never every other course sitting in that stem
                            (that's the info-crumbling this replaces)."""
                            stems = d.get('stems') or []
                            when = f" at {_when}" if _when else ""
                            if stems:
                                stem_named = ", ".join(f"'{s}'" for s in stems)
                                stem_word = "stem" if len(stems) == 1 else "stems"
                                return (
                                    f"{d['course_a']} is colliding with {d['course_b']}{when} "
                                    f"in the {stem_named} combination {stem_word}"
                                )
                            return f"{d['course_a']} is colliding with {d['course_b']}{when} — {d['why']}"

                        program_conflicts.append({
                            'type': 'program_conflict',
                            'date': date_str,
                            'timeslot': start_time,
                            'program': f"{prog_name} ({year_part})",
                            'courses': [
                                {
                                    'course_code': g.course_allocation.course_code,
                                    'normalized_code': normalize_course_code(g.course_allocation.course_code),
                                    'venue': g.venue.code if g.venue else '–',
                                    'is_elective': g.course_allocation.is_elective,
                                    'selection_group': g.course_allocation.selection_group_id,
                                    'specialization_stem': g.course_allocation.specialization_stem_id,
                                    'student_group': g.course_allocation.student_group_id,
                                    'student_group_name': _exam_panel_student_group_label(g.course_allocation),
                                    'stem_names': [d_['name'] for d_ in _exam_panel_stem_display_details(g.course_allocation)],
                                }
                                for g in real_conflict_entries
                            ],
                            'message': f'Program {prog_name} Year {year_part} has conflicting exams at {start_time} on {date_str}',
                            'details': [
                                _pair_collision_line(d)
                                for d in conflict_pairs.values()
                            ],
                            'pairs': list(conflict_pairs.values()),
                        })

                # Venue conflicts — sharing a venue across two different courses
                # is fine as long as the room can physically hold everyone placed
                # there. Only flag it when the combined (split-aware) headcount
                # exceeds the venue's capacity.
                venue_groups = defaultdict(list)
                for e in entries:
                    if e.venue:
                        venue_groups[e.venue.code].append(e)

                # How many ExamTimetable rows (rooms) does each course_allocation
                # occupy in THIS exact date+timeslot? A split exam (e.g. one big
                # course spread across 3 halls) shows up as several rows all
                # carrying the SAME full course roll — without this we'd count
                # the whole course three times over instead of estimating each
                # room's own share.
                alloc_split_counts = defaultdict(int)
                for e in entries:
                    if e.course_allocation_id:
                        alloc_split_counts[e.course_allocation_id] += 1

                def _estimated_students_for_row(entry):
                    alloc = entry.course_allocation
                    if not alloc:
                        return 0
                    if entry.allocated_students is not None:
                        # Exact — no need to estimate a split's per-room
                        # share, the row already records it.
                        return entry.allocated_students
                    total = alloc.number_of_students or 0
                    splits = alloc_split_counts.get(alloc.id, 1) or 1
                    if splits > 1:
                        # Legacy row written before allocated_students existed —
                        # estimate this room's share of the split rather than
                        # re-counting the whole course roll for every room.
                        return -(-total // splits)  # ceil division
                    return total

                for vcode, group in venue_groups.items():
                    if len(group) > 1:
                        norm_codes = {
                            normalize_course_code(g.course_allocation.course_code or '')
                            for g in group
                        }
                        if len(norm_codes) == 1:
                            continue
                        # CombinedCourseGroup: all share same venue intentionally
                        alloc_ids_v = [g.course_allocation.id for g in group if g.course_allocation]
                        all_combined_v = True
                        for i in range(len(alloc_ids_v)):
                            for j in range(i + 1, len(alloc_ids_v)):
                                if not _exam_in_same_combined_group(alloc_ids_v[i], alloc_ids_v[j]):
                                    all_combined_v = False
                                    break
                            if not all_combined_v:
                                break
                        if all_combined_v and len(alloc_ids_v) >= 2:
                            continue

                        # Capacity check: two different courses sharing one venue
                        # is NOT a conflict provided their combined, split-aware
                        # headcount still fits the room.
                        venue_obj = group[0].venue
                        room_capacity = 0
                        if venue_obj:
                            room_capacity = (
                                venue_obj.exam_capacity
                                if venue_obj.exam_capacity is not None
                                else venue_obj.capacity
                            )
                        room_capacity = room_capacity or 0
                        combined_students = sum(_estimated_students_for_row(g) for g in group)
                        if room_capacity and combined_students <= room_capacity:
                            # Fits comfortably — a shared room, not a real conflict.
                            continue

                        venue_conflicts.append({
                            'type': 'venue_conflict',
                            'date': date_str,
                            'timeslot': start_time,
                            'venue': vcode,
                            'combined_students': combined_students,
                            'capacity': room_capacity,
                            'courses': [
                                {
                                    'course_code': g.course_allocation.course_code,
                                    'normalized_code': normalize_course_code(g.course_allocation.course_code)
                                }
                                for g in group
                            ],
                            'message': (
                                f'Venue {vcode} double-booked at {start_time} on {date_str} '
                                f'({combined_students} students exceeds capacity {room_capacity})'
                                if room_capacity else
                                f'Venue {vcode} double-booked at {start_time} on {date_str}'
                            ),
                        })

            for e in entries:
                family = get_course_family_code(e.course_allocation.course_code)
                if family:
                    fkey = (family, date_str)
                    if fkey in family_map:
                        prev_e, prev_time = family_map[fkey]
                        if prev_time != start_time:
                            if (e.course_allocation.course_code == prev_e.course_allocation.course_code and
                                    e.course_allocation.program_id != prev_e.course_allocation.program_id):
                                pass
                            else:
                                course_fam_conflicts.append({
                                    'type': 'course_family_conflict',
                                    'date': date_str,
                                    'family': family,
                                    'course1': prev_e.course_allocation.course_code,
                                    'time1': prev_time,
                                    'course2': e.course_allocation.course_code,
                                    'time2': start_time,
                                    'message': (
                                        f'Related courses {prev_e.course_allocation.course_code} '
                                        f'and {e.course_allocation.course_code} on same day at different times'
                                    ),
                                })
                    else:
                        family_map[fkey] = (e, start_time)

        total = (len(lecturer_conflicts) + len(program_conflicts) +
                 len(venue_conflicts) + len(course_fam_conflicts))
        return JsonResponse({
            'lecturer_conflicts': lecturer_conflicts,
            'program_conflicts': program_conflicts,
            'venue_conflicts': venue_conflicts,
            'course_family_conflicts': course_fam_conflicts,
            'total_conflicts': total,
        })

    except Exception as e:
        import traceback
        return JsonResponse({
            'lecturer_conflicts': [], 'program_conflicts': [],
            'venue_conflicts': [], 'course_family_conflicts': [],
            'total_conflicts': 0,
            'error': str(e),
        }, status=200)



def _find_split_venues(primary_venue, exam_date, slot_start, slot_end, num_students, primary_cap):
    """
    Find venues in the same building as primary_venue that are free at the
    given date/slot. Returns list of venue dicts sorted: same-building first.
    """
    from room_management.models import Venue as VenueModel

    # Codes already booked in this timeslot
    booked_codes = set(
        ExamTimetable.objects
        .filter(date=exam_date, start_time__lt=slot_end, end_time__gt=slot_start)
        .values_list('venue__code', flat=True)
    )

    building_id = primary_venue.building_id if hasattr(primary_venue, 'building_id') else None

    free_qs = (
        VenueModel.objects
        .select_related('building')
        .exclude(code=primary_venue.code)
        .exclude(code__in=booked_codes)
        .order_by('building_id', 'code')
    )

    results = []
    for v in free_qs:
        cap = v.exam_capacity if v.exam_capacity is not None else v.capacity
        if not cap:
            continue
        same_building = (building_id is not None and v.building_id == building_id)
        results.append({
            'id': v.id,
            'code': v.code,
            'capacity': v.capacity or 0,
            'exam_capacity': cap,
            'building': v.building.name if v.building else None,
            'building_id': v.building_id,
            'same_building': same_building,
            'priority': same_building,
        })

    # Same-building first, then by exam_capacity desc
    results.sort(key=lambda x: (0 if x['same_building'] else 1, -x['exam_capacity']))
    return results


# ============================================================
# confirm_venue_split
# ============================================================
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def confirm_venue_split(request):
    """
    Called when the user selects split venues to accommodate a course that
    exceeds a single venue's capacity. Creates a SharedVenueExamGroup so
    the course is recorded across two (or more) venues in the same timeslot.
    """
    try:
        alloc_id = request.POST.get('allocation_id')
        venue_codes_raw = request.POST.get('venue_codes', '[]')
        exam_date = request.POST.get('exam_date')
        slot_str = request.POST.get('slot_time')

        if not all([alloc_id, exam_date, slot_str]):
            return JsonResponse({'status': 'error', 'messages': ['allocation_id, exam_date, slot_time required.']})

        try:
            venue_codes = json.loads(venue_codes_raw)
        except (ValueError, json.JSONDecodeError):
            return JsonResponse({'status': 'error', 'messages': ['Invalid venue_codes JSON.']})

        if not venue_codes:
            return JsonResponse({'status': 'error', 'messages': ['Select at least one additional venue.']})

        config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)
        alloc = get_object_or_404(CourseAllocation, id=alloc_id)
        exam_date_obj = datetime.strptime(exam_date, '%Y-%m-%d').date()
        slot_start = datetime.strptime(slot_str, '%H:%M').time()
        slot_end = (datetime.combine(datetime.today(), slot_start) + timedelta(hours=config.slot_size)).time()

        # Verify none of the chosen venues are already booked at this slot
        booked_clash = list(
            ExamTimetable.objects
            .filter(venue__code__in=venue_codes, date=exam_date_obj,
                    start_time__lt=slot_end, end_time__gt=slot_start)
            .values_list('venue__code', flat=True)
        )
        if booked_clash:
            return JsonResponse({
                'status': 'error',
                'messages': [f'Venue(s) {", ".join(booked_clash)} are already booked at that time.'],
            })

        venues = list(Venue.objects.filter(code__in=venue_codes))
        if not venues:
            return JsonResponse({'status': 'error', 'messages': ['Could not find the selected venues.']})

        primary_venue = venues[0]

        # One ExamTimetable entry on the primary venue only.
        # Extra venues are tracked on the SharedVenueExamGroup alone —
        # no null course_allocation rows are written (avoids NOT NULL constraint).
        primary_entry = ExamTimetable.objects.create(
            course_allocation=alloc,
            venue=primary_venue,
            day=exam_date_obj.strftime('%A'),
            date=exam_date_obj,
            start_time=slot_start,
            end_time=slot_end,
            allocated_students=alloc.number_of_students or 0,
        )

        group = SharedVenueExamGroup.objects.create(
            venue=primary_venue,
            date=exam_date_obj,
            day=exam_date_obj.strftime('%A'),
            start_time=slot_start,
            end_time=slot_end,
            total_students=alloc.number_of_students or 0,
            published=True,
            exam_timetable_entry=primary_entry,
        )
        group.course_allocations.add(alloc)

        # Store the extra venue codes in notes if the model supports it,
        # so the split detail is not silently lost.
        extra_venues = venues[1:]
        if extra_venues:
            extra_codes = ', '.join(v.code for v in extra_venues)
            if hasattr(group, 'notes'):
                group.notes = (
                    f'Split: primary={primary_venue.code}, extra={extra_codes}'
                )
                group.save(update_fields=['notes'])

        return JsonResponse({
            'status': 'success',
            'messages': [
                f'\u2705 {alloc.course_code} split across {len(venues)} venue(s): '
                f'{", ".join(v.code for v in venues)}.',
            ],
            'split_entry': {
                'group_id': group.id,
                'allocation_id': alloc.id,
                'course_code': alloc.course_code,
                'course_name': alloc.course_name or '',
                'date': str(exam_date_obj),
                'day': exam_date_obj.strftime('%A'),
                'slot_time': slot_str,
                'end_time': slot_end.strftime('%H:%M'),
                'venues': [{'id': v.id, 'code': v.code} for v in venues],
                'students': alloc.number_of_students or 0,
                'lecturer': getattr(alloc.lecturer, 'name', 'Unassigned'),
                'program': getattr(alloc.program, 'name', 'N/A'),
            },
        })

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'messages': [str(exc)]})


def _handle_ajax_add(request, config):
    try:
        alloc_id = request.POST.get('allocation_id')
        venue_input = (request.POST.get('venue') or '').strip()
        exam_date = request.POST.get('exam_date')
        slot_str = request.POST.get('slot_time')

        if not all([alloc_id, venue_input, exam_date, slot_str]):
            return JsonResponse({'status': 'error', 'messages': ['All fields are required.']})

        alloc = get_object_or_404(
            CourseAllocation.objects.select_related(
                'lecturer', 'program', 'program_course', 'selection_group',
                'specialization_stem', 'student_group', 'allocation_set',
            ).only(
                'id', 'course_code', 'course_name', 'number_of_students',
                'lecturer_id', 'program_id',
                'lecturer__name', 'program__name',
                'program_course__year', 'program_course__semester',
                'program_course__program_id',
                'allocation_set__id', 'allocation_set__is_special',
                'is_elective', 'intake', 'selection_group_id',
                'specialization_stem__id', 'specialization_stem__category_id',
                'student_group_id',
            ),
            id=alloc_id,
        )
        slot_start = datetime.strptime(slot_str, '%H:%M').time()
        slot_end = (datetime.combine(datetime.today(), slot_start)
                    + timedelta(hours=config.slot_size)).time()
        exam_date_obj = datetime.strptime(exam_date, '%Y-%m-%d').date()

        venue_obj, _ = Venue.objects.get_or_create(code=venue_input, defaults={'capacity': None})
        venue_obj = Venue.objects.only('id', 'code', 'capacity', 'exam_capacity').get(pk=venue_obj.pk)

        collisions = []
        info_messages = []

        if ExamTimetable.objects.filter(venue=venue_obj, date=exam_date_obj, start_time=slot_start).exists():
            # If the existing booking at this venue+slot belongs to the same CombinedCourseGroup,
            # it is intentional — both allocations sit their exam in the same room together.
            existing_at_slot = list(ExamTimetable.objects.filter(
                venue=venue_obj, date=exam_date_obj, start_time=slot_start
            ).select_related('course_allocation').only(
                'course_allocation__id', 'course_allocation__number_of_students',
                'course_allocation__course_code',
            ))
            venue_combined_ok = all(
                _exam_in_same_combined_group(alloc.id, ex.course_allocation.id)
                for ex in existing_at_slot if ex.course_allocation
            )
            if not venue_combined_ok:
                # Sharing a venue across two different courses is fine as long as
                # the room can physically hold everyone. Estimate each existing
                # occupant's share (dividing across their own split rooms, if any,
                # so a split exam's full roll isn't counted for every room it's in),
                # then check the combined headcount against the room's capacity.
                room_capacity = (
                    venue_obj.exam_capacity
                    if venue_obj.exam_capacity is not None
                    else venue_obj.capacity
                ) or 0

                alloc_split_counts = defaultdict(int)
                for ex in ExamTimetable.objects.filter(
                    date=exam_date_obj, start_time=slot_start,
                    course_allocation_id__in=[ex.course_allocation_id for ex in existing_at_slot if ex.course_allocation_id],
                ).only('course_allocation_id'):
                    alloc_split_counts[ex.course_allocation_id] += 1

                existing_students = 0
                for ex in existing_at_slot:
                    if not ex.course_allocation:
                        continue
                    total = ex.course_allocation.number_of_students or 0
                    splits = alloc_split_counts.get(ex.course_allocation_id, 1) or 1
                    existing_students += -(-total // splits) if splits > 1 else total  # ceil division for splits

                combined_students = existing_students + (alloc.number_of_students or 0)

                if room_capacity and combined_students <= room_capacity:
                    info_messages.append(
                        f"ℹ️ Sharing room {venue_input} with existing exam(s) — "
                        f"{combined_students}/{room_capacity} seats used, within capacity."
                    )
                else:
                    collisions.append(f'Room {venue_input} already booked at {slot_start} on {exam_date}.')

        if alloc.lecturer_id:
            conflicting_lec_exams = (
                ExamTimetable.objects
                .filter(
                    course_allocation__lecturer_id=alloc.lecturer_id,
                    date=exam_date_obj,
                    start_time=slot_start,
                )
                .select_related('course_allocation', 'venue')
                .only('course_allocation__course_code', 'venue__code', 'course_allocation__id')
            )
            for ex in conflicting_lec_exams:
                existing_code = get_course_family_code(ex.course_allocation.course_code or '')
                new_code = get_course_family_code(alloc.course_code or '')
                if normalize_course_code(ex.course_allocation.course_code) == normalize_course_code(alloc.course_code):
                    info_messages.append(
                        f"ℹ️ Same course {alloc.course_code} already scheduled at this time - allowed for shared units."
                    )
                    continue
                # CombinedCourseGroup: same lecturer teaching both is intentional
                if _exam_in_same_combined_group(alloc.id, ex.course_allocation.id):
                    info_messages.append(
                        f"ℹ️ Lecturer overlap allowed: {alloc.course_code} and "
                        f"{ex.course_allocation.course_code} are in the same "
                        f"Combined Course Group (examined together)."
                    )
                    continue
                if existing_code != new_code:
                    ex_venue_code = ex.venue.code if ex.venue else None
                    if ex_venue_code and ex_venue_code == venue_input:
                        continue
                    collisions.append(
                        f'Lecturer {alloc.lecturer.name} already has an exam for '
                        f'{ex.course_allocation.course_code} at {slot_start} on {exam_date}.'
                    )

        if alloc.program_id:
            # Scoped to THIS course's own program+year only. Previously this
            # pulled every exam for the whole program regardless of year,
            # relying on exam_panel_is_exempt's key_a != key_b check to
            # relabel other-year hits as "ℹ️ ... allowed (different
            # program-years)" — which is correct but noisy: a course being
            # scheduled has nothing to do with a completely different
            # program-year's exam, so those pairs shouldn't be surfaced to
            # the user at all, not even as an info line. Filtering them out
            # here means every message below is about a real same-year peer.
            key_a = get_program_year_key(alloc)
            program_conflicts_qs = (ExamTimetable.objects
                         .filter(course_allocation__program_id=alloc.program_id,
                                 date=exam_date_obj, start_time=slot_start)
                         .select_related(
                             'course_allocation',
                             'course_allocation__program_course',
                             'course_allocation__selection_group',
                             'course_allocation__specialization_stem',
                             'course_allocation__student_group',
                             'course_allocation__allocation_set',
                         )
                         .only(
                             'course_allocation__course_code',
                             'course_allocation__is_elective',
                             'course_allocation__intake',
                             'course_allocation__allocation_set__id',
                             'course_allocation__allocation_set__is_special',
                             'course_allocation__selection_group_id',
                             'course_allocation__specialization_stem__id',
                             'course_allocation__specialization_stem__category_id',
                             'course_allocation__student_group_id',
                             'course_allocation__program_course__year',
                             'course_allocation__program_course__semester',
                         )
                         # Real stem MEMBERSHIP (M2M) — prefetch_related, not
                         # select_related/.only() (which only cover the
                         # singular specialization_stem pointer above), so
                         # exam_panel_is_exempt's M2M-aware stem check
                         # doesn't add an N+1 query per candidate.
                         .prefetch_related(
                             'course_allocation__specialization_stems',
                             'course_allocation__specialization_stems__category',
                         ))
            for ex in program_conflicts_qs:
                # Skip other program-years entirely — not shown as info,
                # not as a collision. A course being scheduled only cares
                # about its own cohort's exam load.
                if key_a and get_program_year_key(ex.course_allocation) != key_a:
                    continue
                if exam_panel_is_exempt(alloc, ex.course_allocation):
                    if _exam_panel_cohort(alloc) != _exam_panel_cohort(ex.course_allocation):
                        reason = "special vs normal allocation (different cohorts)"
                    elif (_exam_panel_is_special(alloc) and _exam_panel_is_special(ex.course_allocation)
                          and _exam_panel_semester(alloc) is not None
                          and _exam_panel_semester(ex.course_allocation) is not None
                          and _exam_panel_semester(alloc) != _exam_panel_semester(ex.course_allocation)):
                        reason = "special allocation, different semesters"
                    elif normalize_course_code(alloc.course_code) == normalize_course_code(ex.course_allocation.course_code):
                        reason = "same course code (shared unit)"
                    else:
                        # (program-year is already guaranteed equal here —
                        # anything from a different program-year was
                        # filtered out before this loop even started.)
                        other = ex.course_allocation
                        stems1 = _exam_panel_specialization_stem_ids(alloc)
                        stems2 = _exam_panel_specialization_stem_ids(other)
                        sgrp1 = _exam_panel_student_group_id(alloc)
                        sgrp2 = _exam_panel_student_group_id(other)
                        sg1 = _exam_panel_get_selection_group_id_cached(alloc)
                        sg2 = _exam_panel_get_selection_group_id_cached(other)
                        if _exh.share_pool(alloc, other):
                            reason = "alternatives of the same elective (pick-one) group — a student sits only one"
                        elif stems1 and stems2 and not (stems1 & stems2):
                            stem_list_a = _exam_panel_format_stem_list(_exam_panel_stem_display_details(alloc))
                            stem_list_b = _exam_panel_format_stem_list(_exam_panel_stem_display_details(other))
                            reason = (
                                f"different combination stems: {alloc.course_code} is in "
                                f"{stem_list_a or 'no listed stem'}, "
                                f"{other.course_code} is in {stem_list_b or 'no listed stem'}"
                            )
                        elif sgrp1 is not None and sgrp2 is not None and sgrp1 != sgrp2:
                            reason = "different student groups"
                        elif _exam_panel_is_elective(alloc) or _exam_panel_is_elective(other):
                            reason = "elective exemption"
                        elif sg1 is not None or sg2 is not None:
                            reason = "selection group exemption"
                        elif _exam_in_same_combined_group(alloc.id, other.id):
                            reason = "combined course group"
                        else:
                            reason = "collision exemption"
                    info_messages.append(
                        f"ℹ️ {alloc.program.name} already has exam for "
                        f"{ex.course_allocation.course_code} at {slot_start} on {exam_date} "
                        f"— allowed ({reason})."
                    )
                else:
                    # If the real reason this pair clashes is that they share
                    # a specialization stem, name it and list the sibling
                    # courses a student in that stem also takes — makes a
                    # "same stem, so this is a real clash" collision
                    # distinguishable at a glance from a plain same-program
                    # clash with no stem involved at all.
                    why = exam_panel_collision_explainer(alloc, ex.course_allocation)['text']
                    collisions.append(
                        f'\u274c Program {alloc.program.name} already has exam for '
                        f'{ex.course_allocation.course_code} at {slot_start} on {exam_date} '
                        f'\u2014 {why}.'
                    )

        base = get_course_family_code(alloc.course_code)
        if base:
            for ex in (ExamTimetable.objects
                       .filter(date=exam_date_obj)
                       .select_related('course_allocation')
                       .only('start_time', 'course_allocation__course_code')):
                if (get_course_family_code(ex.course_allocation.course_code) == base
                        and ex.start_time != slot_start
                        and normalize_course_code(ex.course_allocation.course_code) != normalize_course_code(alloc.course_code)):
                    collisions.append(
                        f'Course {alloc.course_code} is related to '
                        f'{ex.course_allocation.course_code} scheduled at {ex.start_time} on {exam_date}.'
                    )
                    break

        effective_cap = venue_obj.exam_capacity if venue_obj.exam_capacity is not None else venue_obj.capacity
        capacity_overflow = False
        if effective_cap and alloc.number_of_students:
            if alloc.number_of_students > effective_cap:
                capacity_overflow = True

        if ExamTimetable.objects.filter(course_allocation=alloc).exists():
            collisions.append(f'Course {alloc.course_code} is already scheduled.')

        for ex in (ExamTimetable.objects
                   .filter(venue=venue_obj, date=exam_date_obj)
                   .select_related('course_allocation')
                   .only('start_time', 'end_time', 'course_allocation__course_code')):
            if slot_start < ex.end_time and slot_end > ex.start_time and ex.start_time != slot_start:
                collisions.append(
                    f'Time slot overlaps with {ex.course_allocation.course_code} '
                    f'({ex.start_time}–{ex.end_time}) at same venue.'
                )
                break

        error_collisions = [c for c in collisions if not c.startswith('ℹ️')]

        # ── Capacity overflow: suggest a single bigger room FIRST, only
        # offer a split as the fallback ─────────────────────────────────
        if capacity_overflow and not error_collisions:
            split_suggestions = _find_split_venues(
                venue_obj, exam_date_obj, slot_start, slot_end,
                alloc.number_of_students, effective_cap
            )
            # A single free venue that alone seats the WHOLE group at this
            # exact date+time is strictly better than splitting the exam
            # across rooms — no second invigilation team, no per-room
            # capacity bookkeeping, nothing for the student cohort to be
            # confused about. Splitting should only ever be offered once no
            # single room can do the job, mirroring the same
            # single-room-first / split-as-last-resort pattern already used
            # by the merge-group overflow flow (see confirm_merge_group's
            # 'fits' vs 'splits' below).
            single_venue_alternatives = [
                v for v in split_suggestions
                if v['exam_capacity'] >= alloc.number_of_students
            ]
            return JsonResponse({
                'status': 'capacity_overflow',
                'messages': [
                    f'⚠️ Venue <strong>{venue_input}</strong> exam capacity '
                    f'(<strong>{effective_cap}</strong>) is insufficient for '
                    f'<strong>{alloc.number_of_students}</strong> students.'
                ],
                'overflow': {
                    'students': alloc.number_of_students,
                    'venue_capacity': effective_cap,
                    'overflow_count': alloc.number_of_students - effective_cap,
                    'primary_venue': venue_input,
                    'primary_venue_id': venue_obj.id,
                    'date': str(exam_date_obj),
                    'slot_time': slot_str,
                    'allocation_id': alloc.id,
                    'course_code': alloc.course_code,
                },
                'single_venue_alternatives': single_venue_alternatives,
                'split_venues': split_suggestions,
            })

        if error_collisions:
            # Don't just say "no" — show where this course COULD go instead.
            # Reuses the exact same ranked-recommendation engine already
            # built for the "Move Exam" / Split Manager panel
            # (exam_simulate_move._find_recommendations), so a real
            # collision here surfaces the same kind of free date/time/venue
            # suggestions as that feature, instead of leaving the admin to
            # guess-and-retry venues/slots by hand. Deferred import: this
            # module is what exam_simulate_move.py itself imports FROM at
            # load time, so importing it back at module level here would be
            # circular — safe once deferred to call time, after both
            # modules have already finished loading.
            from timetable.exam_simulate_move import _find_recommendations
            venues = _build_venues()
            recommendations = _find_recommendations(
                alloc, venues, exclude_et_ids=[],
                prefer_date=str(exam_date_obj), prefer_venue=venue_input, limit=8,
            )
            return JsonResponse({
                'status': 'error',
                'messages': error_collisions + info_messages,
                'recommendations': recommendations,
            })

        entry = ExamTimetable.objects.create(
            course_allocation=alloc,
            venue=venue_obj,
            day=exam_date_obj.strftime('%A'),
            date=exam_date_obj,
            start_time=slot_start,
            end_time=slot_end,
            allocated_students=alloc.number_of_students or 0,
        )

        return JsonResponse({
            'status': 'success',
            'messages': [f'Exam scheduled for {alloc.course_code} on {exam_date} at {slot_start}.'],
            'new_entry': {
                'id': entry.id,
                'date': str(exam_date_obj),
                'day': entry.day,
                'start_time': slot_str,
                'end_time': slot_end.strftime('%H:%M'),
                'venue': venue_input,
                'venue_id': venue_obj.id,
                'course_code': alloc.course_code,
                'normalized_code': normalize_course_code(alloc.course_code),
                'course_name': alloc.course_name or '',
                'lecturer': getattr(alloc.lecturer, 'name', 'Unassigned'),
                'program': getattr(alloc.program, 'name', 'N/A'),
                'year': alloc.program_course.year if alloc.program_course else None,
                'is_elective': alloc.is_elective,
                'intake': alloc.intake,
                'selection_group_id': alloc.selection_group_id if alloc.selection_group else None,
                'students': alloc.number_of_students or 0,
                'allocation_id': alloc.id,
                'type': 'direct',
            },
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'messages': [str(e)]})


# ============================================================
# DELETE EXAM ENTRY
# ============================================================
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def delete_exam_entry(request):
    try:
        entry_id = request.POST.get('entry_id')
        if not entry_id:
            return JsonResponse({'status': 'error', 'message': 'Entry ID required'})

        # Try to get the exam timetable entry
        entry = get_object_or_404(ExamTimetable, id=entry_id)
        
        # Check if this entry is linked to any merged or shared groups
        merged_groups = MergedCourseGroup.objects.filter(exam_timetable_entry=entry)
        shared_groups = SharedVenueExamGroup.objects.filter(exam_timetable_entry=entry)
        
        allocation_info = None
        if entry.course_allocation:
            allocation_info = {
                'id': entry.course_allocation.id,
                'course_code': entry.course_allocation.course_code,
                'lecturer': getattr(entry.course_allocation.lecturer, 'name', 'Unassigned'),
                'course_name': entry.course_allocation.course_name,
                'program': getattr(entry.course_allocation.program, 'name', 'N/A'),
                'students': entry.course_allocation.number_of_students or 0,
            }
        
        # Delete the entry
        entry.delete()
        
        # Update linked groups to remove the reference
        for group in merged_groups:
            group.exam_timetable_entry = None
            group.save(update_fields=['exam_timetable_entry'])
        for group in shared_groups:
            group.exam_timetable_entry = None
            group.save(update_fields=['exam_timetable_entry'])
        
        return JsonResponse({
            'status': 'success',
            'message': 'Entry deleted successfully',
            'allocation_info': allocation_info,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(e)})


# ============================================================
# BULK DELETE EXAM ENTRIES
# ============================================================
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def bulk_delete_exam_entries(request):
    try:
        entry_ids = request.POST.getlist('entry_ids[]')
        date = request.POST.get('date')
        venue = request.POST.get('venue')
        time_slot = request.POST.get('time_slot')
        
        queryset = ExamTimetable.objects.all()
        
        if entry_ids:
            queryset = queryset.filter(id__in=entry_ids)
        elif date and venue and time_slot:
            queryset = queryset.filter(
                date=date, 
                venue__code=venue, 
                start_time=time_slot
            )
        elif date:
            queryset = queryset.filter(date=date)
        else:
            return JsonResponse({'status': 'error', 'message': 'No filter criteria provided'})
        
        count = queryset.count()
        
        # Get all entries before deletion to update linked groups
        entries = list(queryset.select_related('course_allocation'))
        
        # Update any linked merged/shared groups
        for entry in entries:
            MergedCourseGroup.objects.filter(exam_timetable_entry=entry).update(exam_timetable_entry=None)
            SharedVenueExamGroup.objects.filter(exam_timetable_entry=entry).update(exam_timetable_entry=None)
        
        # Delete the entries
        queryset.delete()
        
        return JsonResponse({
            'status': 'success',
            'message': f'Deleted {count} entries',
            'count': count,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(e)})


# ============================================================
# create_merged_group
# ============================================================
import json
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from core.group_required import group_required

from room_management.models import Venue
from timetable.models import MergedCourseGroup, ExamSchedulerConfig


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def create_merged_group(request):
    try:
        base_code      = (request.POST.get('base_code') or '').strip()
        date_str       = request.POST.get('date', '')
        start_time_str = request.POST.get('start_time', '')
        venue_code     = (request.POST.get('venue') or '').strip()
        course_ids_raw = request.POST.get('course_ids', '[]')
        total_students = int(request.POST.get('total_students', 0) or 0)
        published      = request.POST.get('published', 'true').lower() != 'false'
        # force_save=true → caller accepted the overflow warning and chose to proceed anyway
        force_save     = request.POST.get('force_save', 'false').lower() == 'true'

        if not all([base_code, date_str, start_time_str, venue_code]):
            return JsonResponse({'status': 'error', 'messages': ['base_code, date, start_time, and venue are required.']})

        try:
            course_ids = [int(i) for i in json.loads(course_ids_raw)]
        except (ValueError, json.JSONDecodeError):
            return JsonResponse({'status': 'error', 'messages': ['Invalid course_ids JSON.']})

        if len(course_ids) < 2:
            return JsonResponse({'status': 'error', 'messages': ['Select at least 2 courses to merge.']})

        allocations = list(CourseAllocation.objects.filter(id__in=course_ids))
        if len(allocations) < 2:
            return JsonResponse({'status': 'error', 'messages': ['Could not find the selected courses.']})

        exam_date  = datetime.strptime(date_str, '%Y-%m-%d').date()
        config, _  = ExamSchedulerConfig.objects.get_or_create(id=1)
        slot_start = datetime.strptime(start_time_str, '%H:%M').time()
        slot_end   = (datetime.combine(exam_date, slot_start) + timedelta(hours=config.slot_size)).time()

        # Fetch venue with building so capacity and suggestions have full data
        venue_obj = Venue.objects.select_related('building').filter(code__iexact=venue_code).first()
        if not venue_obj:
            venue_obj = Venue.objects.create(code=venue_code)

        base_alloc        = allocations[0]
        computed_students = total_students or sum(a.number_of_students or 0 for a in allocations)

        # ── CAPACITY CHECK ──────────────────────────────────────────────────
        # Skip only when: force_save=true  OR  venue has no capacity defined.
        if not force_save and venue_obj.capacity and computed_students > venue_obj.capacity:

            # Venue codes already occupied at this exact exam slot
            booked_codes = set(
                ExamTimetable.objects.filter(
                    date=exam_date,
                    start_time__lt=slot_end,
                    end_time__gt=slot_start,
                    venue__isnull=False,
                ).values_list('venue__code', flat=True)
            )
            booked_codes.add(venue_obj.code)  # exclude the overflowing venue itself

            preferred_bld = venue_obj.building_id  # may be None

            # Build free-room list (all rooms with capacity, not booked, not current)
            free = []
            for v in (
                Venue.objects.select_related('building')
                .filter(capacity__isnull=False)
                .exclude(code__iexact=venue_code)
                .order_by('building_id', '-capacity')
            ):
                if v.code in booked_codes:
                    continue
                same_bld = (preferred_bld is not None and v.building_id == preferred_bld)
                free.append({
                    'code':          v.code,
                    'capacity':      v.capacity,
                    'building_name': v.building.name if v.building else None,
                    'same_building': same_bld,
                })

            # Same-building first, then largest capacity
            free.sort(key=lambda x: (0 if x['same_building'] else 1, -(x['capacity'] or 0)))

            # Single rooms that fit everyone on their own
            fits = [v for v in free if (v['capacity'] or 0) >= computed_students]

            # Paired splits — only computed when no single room fits
            splits = []
            if not fits:
                for i, a in enumerate(free):
                    for b in free[i + 1:]:
                        if (a['capacity'] or 0) + (b['capacity'] or 0) >= computed_students:
                            splits.append({
                                'rooms': [
                                    {k: a[k] for k in ('code', 'capacity', 'building_name', 'same_building')},
                                    {k: b[k] for k in ('code', 'capacity', 'building_name', 'same_building')},
                                ],
                                'combined_capacity': (a['capacity'] or 0) + (b['capacity'] or 0),
                            })
                        if len(splits) >= 5:
                            break
                    if len(splits) >= 5:
                        break

            return JsonResponse({
                'status':  'capacity_overflow',
                'message': (
                    f'⚠️ Venue {venue_obj.code} capacity is {venue_obj.capacity} '
                    f'but the merged group has {computed_students} students '
                    f'({computed_students - venue_obj.capacity} over capacity).'
                ),
                'overflow': {
                    'venue_code':     venue_obj.code,
                    'venue_capacity': venue_obj.capacity,
                    'total_students': computed_students,
                    'overflow_count': computed_students - venue_obj.capacity,
                    'date':           date_str,
                    'start_time':     start_time_str,
                    'end_time':       slot_end.strftime('%H:%M'),
                    'building_name':  venue_obj.building.name if venue_obj.building else None,
                    # Echo form context so frontend can re-POST with a different venue
                    'base_code':      base_code,
                    'course_ids':     course_ids,
                },
                'free_venues': fits[:10],
                'split_pairs': splits,
            })

        # ── CREATE GROUP ────────────────────────────────────────────────────
        exam_tt_entry      = None
        exam_temp_tt_entry = None

        if published:
            exam_tt_entry = ExamTimetable.objects.filter(
                course_allocation=base_alloc,
                date=exam_date,
                start_time=slot_start,
            ).first()
        else:
            exam_temp_tt_entry = ExamTempTimetable.objects.filter(
                course_allocation=base_alloc,
                date=exam_date,
                start_time=slot_start,
            ).first()

        group = MergedCourseGroup.objects.create(
            merged_code=base_code,
            base_course=base_alloc,
            total_students=computed_students,
            date=exam_date,
            start_time=slot_start,
            end_time=slot_end,
            venue=venue_obj,
            published=published,
            exam_timetable_entry=exam_tt_entry,
            exam_temp_timetable_entry=exam_temp_tt_entry,
        )
        group.merged_courses.set(allocations)

        # Capacity status for frontend badge
        cap = venue_obj.capacity
        if cap:
            cap_status = 'overflow' if computed_students > cap else ('exact' if computed_students == cap else 'ok')
        else:
            cap_status = None

        return JsonResponse({
            'status':          'success',
            'messages': [f'Merged group "{base_code}" created {"and published" if published else "as draft"} with {len(allocations)} courses.'],
            'group_id':        group.id,
            'venue_capacity':  cap,
            'total_students':  computed_students,
            'capacity_status': cap_status,
            'exam_timetable_entry_id':      exam_tt_entry.id if exam_tt_entry else None,
            'exam_temp_timetable_entry_id': exam_temp_tt_entry.id if exam_temp_tt_entry else None,
        })

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'messages': [str(exc)]})


# ============================================================
# create_shared_venue_group
# ============================================================
from timetable.models import SharedVenueExamGroup


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def create_shared_venue_group(request):
    try:
        venue_code = (request.POST.get('venue') or '').strip()
        date_str = request.POST.get('date', '')
        start_time_str = request.POST.get('start_time', '')
        course_ids_raw = request.POST.get('course_ids', '[]')
        total_students = int(request.POST.get('total_students', 0) or 0)
        published = request.POST.get('published', 'true').lower() != 'false'

        if not all([venue_code, date_str, start_time_str]):
            return JsonResponse({'status': 'error', 'messages': ['venue, date, and start_time are required.']})

        try:
            course_ids = [int(i) for i in json.loads(course_ids_raw)]
        except (ValueError, json.JSONDecodeError):
            return JsonResponse({'status': 'error', 'messages': ['Invalid course_ids JSON.']})

        if len(course_ids) < 2:
            return JsonResponse({'status': 'error', 'messages': ['Select at least 2 courses.']})

        allocations = list(CourseAllocation.objects.filter(id__in=course_ids))
        if not allocations:
            return JsonResponse({'status': 'error', 'messages': ['Could not find the selected courses.']})

        exam_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)
        slot_start = datetime.strptime(start_time_str, '%H:%M').time()
        slot_end = (datetime.combine(exam_date, slot_start) + timedelta(hours=config.slot_size)).time()

        venue_obj, _ = Venue.objects.get_or_create(code=venue_code, defaults={'capacity': None})

        exam_tt_entry = None
        exam_temp_tt_entry = None

        if published:
            exam_tt_entry = ExamTimetable.objects.filter(
                venue=venue_obj,
                date=exam_date,
                start_time=slot_start,
            ).first()
        else:
            exam_temp_tt_entry = ExamTempTimetable.objects.filter(
                venue=venue_obj,
                date=exam_date,
                start_time=slot_start,
            ).first()

        group = SharedVenueExamGroup.objects.create(
            venue=venue_obj,
            date=exam_date,
            day=exam_date.strftime('%A'),
            start_time=slot_start,
            end_time=slot_end,
            total_students=total_students or sum(a.number_of_students or 0 for a in allocations),
            published=published,
            exam_timetable_entry=exam_tt_entry,
            exam_temp_timetable_entry=exam_temp_tt_entry,
        )
        group.course_allocations.set(allocations)

        return JsonResponse({
            'status': 'success',
            'messages': [f'Shared venue group at "{venue_code}" created {"and published" if published else "as draft"} with {len(allocations)} courses.'],
            'group_id': group.id,
            'exam_timetable_entry_id': exam_tt_entry.id if exam_tt_entry else None,
            'exam_temp_timetable_entry_id': exam_temp_tt_entry.id if exam_temp_tt_entry else None,
        })

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'messages': [str(exc)]})


# ============================================================
# delete_merged_group
# ============================================================
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def delete_merged_group(request):
    try:
        group_id = request.POST.get('group_id')
        if not group_id:
            return JsonResponse({'status': 'error', 'message': 'group_id required.'})
        
        group = MergedCourseGroup.objects.get(id=group_id)
        
        # Get all course allocations that will be freed up
        course_ids = list(group.merged_courses.values_list('id', flat=True))
        if group.base_course_id:
            course_ids.append(group.base_course_id)
        
        # If there's an associated timetable entry, delete it too
        if group.exam_timetable_entry:
            group.exam_timetable_entry.delete()
        
        group.delete()
        
        return JsonResponse({
            'status': 'success', 
            'message': f'Group {group_id} deleted.',
            'freed_course_ids': course_ids,
        })
    except MergedCourseGroup.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Group not found.'})
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(exc)})


# ============================================================
# delete_shared_venue_group
# ============================================================
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def delete_shared_venue_group(request):
    try:
        group_id = request.POST.get('group_id')
        if not group_id:
            return JsonResponse({'status': 'error', 'message': 'group_id required.'})
        
        group = SharedVenueExamGroup.objects.get(id=group_id)
        
        # Get all course allocations that will be freed up
        course_ids = list(group.course_allocations.values_list('id', flat=True))
        
        # If there's an associated timetable entry, delete it too
        if group.exam_timetable_entry:
            group.exam_timetable_entry.delete()
        
        group.delete()
        
        return JsonResponse({
            'status': 'success', 
            'message': f'Group {group_id} deleted.',
            'freed_course_ids': course_ids,
        })
    except SharedVenueExamGroup.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Group not found.'})
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(exc)})


# ============================================================
# toggle_merged_published
# ============================================================
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def toggle_merged_published(request):
    try:
        group_id = request.POST.get('group_id')
        if not group_id:
            return JsonResponse({'status': 'error', 'message': 'group_id required.'})

        group = MergedCourseGroup.objects.select_related(
            'base_course', 'venue', 'exam_timetable_entry', 'exam_temp_timetable_entry'
        ).get(id=group_id)

        group.published = not group.published

        if group.published:
            exam_tt = ExamTimetable.objects.filter(
                course_allocation=group.base_course,
                date=group.date,
                start_time=group.start_time,
            ).first()
            group.exam_timetable_entry = exam_tt
            group.exam_temp_timetable_entry = None
        else:
            exam_temp_tt = ExamTempTimetable.objects.filter(
                course_allocation=group.base_course,
                date=group.date,
                start_time=group.start_time,
            ).first()
            group.exam_timetable_entry = None
            group.exam_temp_timetable_entry = exam_temp_tt

        group.save(update_fields=[
            'published',
            'exam_timetable_entry',
            'exam_temp_timetable_entry',
        ])

        active_entry = group.get_active_timetable_entry()
        return JsonResponse({
            'status': 'success',
            'published': group.published,
            'message': f'Group {"published" if group.published else "unpublished"}.',
            'exam_timetable_entry_id': group.exam_timetable_entry_id,
            'exam_temp_timetable_entry_id': group.exam_temp_timetable_entry_id,
            'active_timetable_entry_id': active_entry.id if active_entry else None,
        })
    except MergedCourseGroup.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Group not found.'})
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(exc)})


# ============================================================
# toggle_shared_published
# ============================================================
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def toggle_shared_published(request):
    try:
        group_id = request.POST.get('group_id')
        if not group_id:
            return JsonResponse({'status': 'error', 'message': 'group_id required.'})

        group = SharedVenueExamGroup.objects.select_related(
            'venue', 'exam_timetable_entry', 'exam_temp_timetable_entry'
        ).get(id=group_id)

        group.published = not group.published

        if group.published:
            exam_tt = ExamTimetable.objects.filter(
                venue=group.venue,
                date=group.date,
                start_time=group.start_time,
            ).first()
            group.exam_timetable_entry = exam_tt
            group.exam_temp_timetable_entry = None
        else:
            exam_temp_tt = ExamTempTimetable.objects.filter(
                venue=group.venue,
                date=group.date,
                start_time=group.start_time,
            ).first()
            group.exam_timetable_entry = None
            group.exam_temp_timetable_entry = exam_temp_tt

        group.save(update_fields=[
            'published',
            'exam_timetable_entry',
            'exam_temp_timetable_entry',
        ])

        active_entry = group.get_active_timetable_entry()
        return JsonResponse({
            'status': 'success',
            'published': group.published,
            'message': f'Group {"published" if group.published else "unpublished"}.',
            'exam_timetable_entry_id': group.exam_timetable_entry_id,
            'exam_temp_timetable_entry_id': group.exam_temp_timetable_entry_id,
            'active_timetable_entry_id': active_entry.id if active_entry else None,
        })
    except SharedVenueExamGroup.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Group not found.'})
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(exc)})


# ============================================================
# remove_from_merged
# ============================================================
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def remove_from_merged(request):
    try:
        group_id = request.POST.get('group_id')
        course_id = request.POST.get('course_id')
        
        if not group_id or not course_id:
            return JsonResponse({'status': 'error', 'message': 'group_id and course_id required'})
        
        group = MergedCourseGroup.objects.get(id=group_id)
        group.merged_courses.remove(course_id)
        
        # Update total students
        total = sum(c.number_of_students or 0 for c in group.merged_courses.all())
        group.total_students = total
        group.save(update_fields=['total_students'])
        
        return JsonResponse({
            'status': 'success', 
            'message': 'Course removed from merged group',
            'freed_course_id': int(course_id),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(e)})


# ============================================================
# add_to_merged
# ============================================================
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def add_to_merged(request):
    try:
        group_id = request.POST.get('group_id')
        course_id = request.POST.get('course_id')
        
        if not group_id or not course_id:
            return JsonResponse({'status': 'error', 'message': 'group_id and course_id required'})
        
        group = MergedCourseGroup.objects.get(id=group_id)
        course = CourseAllocation.objects.get(id=course_id)
        
        group.merged_courses.add(course)
        
        # Update total students
        total = sum(c.number_of_students or 0 for c in group.merged_courses.all())
        group.total_students = total
        group.save(update_fields=['total_students'])
        
        return JsonResponse({
            'status': 'success', 
            'message': 'Course added to merged group',
            'added_course_id': int(course_id),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(e)})