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
from core.group_required import group_required
from room_management.models import Venue
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
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.id if st else None
    except Exception:
        return None


def _exam_panel_specialization_category_id(alloc):
    try:
        st = getattr(alloc, 'specialization_stem', None)
        return st.category_id if st else None
    except Exception:
        return None


_exam_panel_stem_cache = {}


def _exam_panel_get_specialization_stem_id_cached(alloc):
    if not alloc or not hasattr(alloc, 'id'):
        return None
    if alloc.id in _exam_panel_stem_cache:
        return _exam_panel_stem_cache[alloc.id]
    st_id = _exam_panel_specialization_stem_id(alloc)
    _exam_panel_stem_cache[alloc.id] = st_id
    return st_id


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


def exam_panel_is_exempt(alloc_a, alloc_b) -> bool:
    if _exam_panel_intake(alloc_a) != _exam_panel_intake(alloc_b):
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
    st1 = _exam_panel_get_specialization_stem_id_cached(alloc_a)
    st2 = _exam_panel_get_specialization_stem_id_cached(alloc_b)
    if st1 is not None and st2 is not None and st1 == st2:
        return False
    if st1 is not None and st2 is not None and st1 != st2:
        cat1 = _exam_panel_specialization_category_id(alloc_a)
        cat2 = _exam_panel_specialization_category_id(alloc_b)
        if cat1 is not None and cat1 == cat2:
            return True

    # ── StudentGroup: different, explicitly-set groups within the same
    # program/year are different cohorts of students, so their compulsory
    # exams can legitimately run concurrently. A shared course
    # (student_group=None — electives/stem courses, or anything not yet
    # split into groups) is sat by everyone and must keep clashing with
    # every group's exams, so it deliberately falls through untouched here.
    sgrp1 = _exam_panel_student_group_id(alloc_a)
    sgrp2 = _exam_panel_student_group_id(alloc_b)
    if sgrp1 is not None and sgrp2 is not None and sgrp1 != sgrp2:
        return True

    # ── Elective / SelectionGroup ──────────────────────────────────────────
    # A student sits the exam for only ONE course in a selection group, so no
    # real student ever needs two SG exams at once — safe to overlap. Matches
    # the autoscheduler's corrected semantics: any course carrying a
    # selection_group is exempt, not only against its exact group-mates.
    sg1 = _exam_panel_get_selection_group_id_cached(alloc_a)
    sg2 = _exam_panel_get_selection_group_id_cached(alloc_b)
    if _exam_panel_is_elective(alloc_a) or _exam_panel_is_elective(alloc_b):
        return True
    if sg1 is not None or sg2 is not None:
        return True

    # CombinedCourseGroup: allocations from different departments taught together
    # by the same lecturer are intentionally scheduled at the same time.
    if _exam_in_same_combined_group(alloc_a.id, alloc_b.id):
        return True
    return False


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
    venue_dict = {}
    for v in Venue.objects.all().only('code', 'capacity', 'exam_capacity'):
        exam_cap = v.exam_capacity if v.exam_capacity is not None else v.capacity
        venue_dict[v.code] = {
            'code': v.code,
            'capacity': v.capacity or 0,
            'exam_capacity': exam_cap or 0,
        }
    if include_from_timetables:
        for code in (ExamTimetable.objects
                     .filter(venue__isnull=False)
                     .values_list('venue__code', flat=True)
                     .distinct()):
            if code and code not in venue_dict:
                venue_dict[code] = {'code': code, 'capacity': 0, 'exam_capacity': 0}
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


def _get_effective_exam_date_range(config):
    import datetime as dt
    config_dates = config.get_excluded_date_range()
    config_date_set = {d for d, _ in config_dates}
    timetable_dates = set(
        str(d)
        for d in ExamTimetable.objects.values_list('date', flat=True).distinct()
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


def _scheduled_excluded_ids():
    """
    Return set of CourseAllocation IDs that should NOT appear in unscheduled list.
    Includes:
    - Courses directly in ExamTimetable
    - Base courses and merged_courses members of ANY MergedCourseGroup (published or draft)
    - Courses in ANY SharedVenueExamGroup (published or draft)
    - Non-primary members of a CombinedCourseGroup whose primary_allocation is exam-scheduled
    """
    # Direct ExamTimetable entries
    scheduled_ids = set(ExamTimetable.objects
                         .exclude(course_allocation__isnull=True)
                         .values_list('course_allocation_id', flat=True).distinct())

    # All merged groups (published + draft) — base and member courses
    all_merged = MergedCourseGroup.objects.prefetch_related('merged_courses')
    merged_base = set(all_merged.values_list('base_course_id', flat=True))
    merged_members = set()
    for group in all_merged:
        merged_members.update(group.merged_courses.values_list('id', flat=True))

    # All shared venue groups (published + draft)
    shared_ids = set(
        id_
        for id_ in SharedVenueExamGroup.objects
        .values_list('course_allocations__id', flat=True)
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


def _get_scheduled_count():
    """
    Count total 'scheduled' courses - deduplicated by CourseAllocation ID.
    """
    # Get all CourseAllocation IDs that are scheduled in any way
    scheduled_ids = set()
    
    # Direct ExamTimetable entries
    direct_ids = set(ExamTimetable.objects
                    .exclude(course_allocation__isnull=True)
                    .values_list('course_allocation_id', flat=True))
    scheduled_ids.update(direct_ids)
    
    # Published merged groups - all merged courses
    for group in MergedCourseGroup.objects.filter(published=True):
        # Add base course
        if group.base_course_id:
            scheduled_ids.add(group.base_course_id)
        # Add all merged courses
        member_ids = group.merged_courses.values_list('id', flat=True)
        scheduled_ids.update(member_ids)
    
    # Published shared groups - all course allocations
    for group in SharedVenueExamGroup.objects.filter(published=True):
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

    effective_range, has_overflow, overflow_count = _get_effective_exam_date_range(config)

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

        # ── STAGE: initial (20%) ──────────────────────────────
        if stage == 'initial':
            venues_data = _build_venues()
            timetable_count = ExamTimetable.objects.count()
            excluded = _scheduled_excluded_ids()
            # Scope to the same population the exam autoscheduler considers:
            # allocations that are submission-allowed and have at least 1 student.
            from django.db.models import Q as _Q
            _exam_pool = CourseAllocation.objects.filter(
                _Q(department__submission_control__allow_submission_to_tt=True)
                | _Q(department__submission_control__isnull=True)
            ).filter(number_of_students__gt=0)
            unscheduled_count = _exam_pool.exclude(id__in=excluded).count()
            scheduled_count = _get_scheduled_count()
            total_courses = _exam_pool.count()

            effective_range, has_overflow, overflow_count = _get_effective_exam_date_range(config)

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
                ExamTimetable.objects
                .select_related(
                    'course_allocation__lecturer',
                    'course_allocation__program',
                    'course_allocation__program_course',
                    'course_allocation__department',
                    'course_allocation__selection_group',
                    'venue',
                )
                .only(
                    'id', 'date', 'day', 'start_time', 'end_time',
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
            data = []
            for t in timetables:
                alloc = t.course_allocation
                year = None
                if alloc and hasattr(alloc, 'program_course') and alloc.program_course:
                    year = alloc.program_course.year

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
                    'students': (alloc.number_of_students or 0) if alloc else 0,
                    'allocation_id': alloc.id if alloc else None,
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
            excluded = _scheduled_excluded_ids()
            allocations = (
                CourseAllocation.objects
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
                MergedCourseGroup.objects
                .select_related('base_course', 'venue', 'exam_timetable_entry', 'exam_temp_timetable_entry')
                .prefetch_related('merged_courses__lecturer')
                .order_by('date', 'start_time')
            )
            shared_qs = (
                SharedVenueExamGroup.objects
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
            _exam_pool = CourseAllocation.objects.filter(
                _Q(department__submission_control__allow_submission_to_tt=True)
                | _Q(department__submission_control__isnull=True)
            ).filter(number_of_students__gt=0)
            all_courses = _exam_pool.count()
            excluded = _scheduled_excluded_ids()
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
# VIEW 3 — Conflicts API
# ═════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_conflicts_api(request):
    try:
        timetables = (
            ExamTimetable.objects
            .select_related(
                'course_allocation',
                'course_allocation__program',
                'course_allocation__program_course',
                'course_allocation__lecturer',
                'course_allocation__selection_group',
                'course_allocation__specialization_stem',
                'course_allocation__student_group',
                'venue',
            )
            .only(
                'id', 'date', 'start_time', 'end_time',
                'course_allocation__course_code',
                'course_allocation__program__id',
                'course_allocation__program__name',
                'course_allocation__program_course__year',
                'course_allocation__program_course__program_id',
                'course_allocation__lecturer__id',
                'course_allocation__lecturer__name',
                'course_allocation__is_elective',
                'course_allocation__intake',
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
                    seen_ids = set()
                    real_conflict_entries = [
                        e for e in real_conflict_entries
                        if e.id not in seen_ids and not seen_ids.add(e.id)
                    ]
                    if len(real_conflict_entries) >= 2:
                        prog_name = group[0].course_allocation.program.name
                        year_part = py_key.split('_')[-1] if '_year_' in py_key else 'unknown'
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
                                }
                                for g in real_conflict_entries
                            ],
                            'message': f'Program {prog_name} Year {year_part} has conflicting exams at {start_time} on {date_str}',
                        })

                # Venue conflicts
                venue_groups = defaultdict(list)
                for e in entries:
                    if e.venue:
                        venue_groups[e.venue.code].append(e)
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
                        venue_conflicts.append({
                            'type': 'venue_conflict',
                            'date': date_str,
                            'timeslot': start_time,
                            'venue': vcode,
                            'courses': [
                                {
                                    'course_code': g.course_allocation.course_code,
                                    'normalized_code': normalize_course_code(g.course_allocation.course_code)
                                }
                                for g in group
                            ],
                            'message': f'Venue {vcode} double-booked at {start_time} on {date_str}',
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
                'specialization_stem', 'student_group',
            ).only(
                'id', 'course_code', 'course_name', 'number_of_students',
                'lecturer_id', 'program_id',
                'lecturer__name', 'program__name',
                'program_course__year', 'program_course__program_id',
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
            existing_at_slot = ExamTimetable.objects.filter(
                venue=venue_obj, date=exam_date_obj, start_time=slot_start
            ).select_related('course_allocation').only('course_allocation__id')
            venue_combined_ok = all(
                _exam_in_same_combined_group(alloc.id, ex.course_allocation.id)
                for ex in existing_at_slot if ex.course_allocation
            )
            if not venue_combined_ok:
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
            program_conflicts_qs = (ExamTimetable.objects
                         .filter(course_allocation__program_id=alloc.program_id,
                                 date=exam_date_obj, start_time=slot_start)
                         .select_related(
                             'course_allocation',
                             'course_allocation__program_course',
                             'course_allocation__selection_group',
                             'course_allocation__specialization_stem',
                             'course_allocation__student_group',
                         )
                         .only(
                             'course_allocation__course_code',
                             'course_allocation__is_elective',
                             'course_allocation__intake',
                             'course_allocation__selection_group_id',
                             'course_allocation__specialization_stem__id',
                             'course_allocation__specialization_stem__category_id',
                             'course_allocation__student_group_id',
                             'course_allocation__program_course__year',
                         ))
            for ex in program_conflicts_qs:
                if exam_panel_is_exempt(alloc, ex.course_allocation):
                    if _exam_panel_intake(alloc) != _exam_panel_intake(ex.course_allocation):
                        reason = "different intake cohorts"
                    elif normalize_course_code(alloc.course_code) == normalize_course_code(ex.course_allocation.course_code):
                        reason = "same course code (shared unit)"
                    else:
                        key_a = get_program_year_key(alloc)
                        key_b = get_program_year_key(ex.course_allocation)
                        if key_a != key_b:
                            reason = "different program-years"
                        else:
                            other = ex.course_allocation
                            st1 = _exam_panel_get_specialization_stem_id_cached(alloc)
                            st2 = _exam_panel_get_specialization_stem_id_cached(other)
                            sgrp1 = _exam_panel_student_group_id(alloc)
                            sgrp2 = _exam_panel_student_group_id(other)
                            sg1 = _exam_panel_get_selection_group_id_cached(alloc)
                            sg2 = _exam_panel_get_selection_group_id_cached(other)
                            if st1 is not None and st2 is not None and st1 != st2:
                                reason = "different specialization stems"
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
                    collisions.append(
                        f'❌ Program {alloc.program.name} already has exam for '
                        f'{ex.course_allocation.course_code} at {slot_start} on {exam_date}.'
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

        # ── Capacity overflow: suggest split venues ─────────────
        if capacity_overflow and not error_collisions:
            split_suggestions = _find_split_venues(
                venue_obj, exam_date_obj, slot_start, slot_end,
                alloc.number_of_students, effective_cap
            )
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
                'split_venues': split_suggestions,
            })

        if error_collisions:
            return JsonResponse({'status': 'error', 'messages': error_collisions + info_messages})

        entry = ExamTimetable.objects.create(
            course_allocation=alloc,
            venue=venue_obj,
            day=exam_date_obj.strftime('%A'),
            date=exam_date_obj,
            start_time=slot_start,
            end_time=slot_end,
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