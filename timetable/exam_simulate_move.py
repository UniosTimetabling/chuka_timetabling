"""
timetable/exam_simulate_move.py
================================
"Simulate Move" + "Bulk Move" feature for the Exam Timetable Panel.

Mirrors `timetable/simulate_move.py` (the same feature built for the
regular timetable) but adapted to the exam timetable's DATE-based
scheduling — ExamTimetable rows are keyed by a specific calendar `date`
(not a recurring weekday), and conflict exemptions follow the exam
panel's own rules (elective/selection-group, differing intake, same
normalised course code across venues = an intentional split exam,
Combined Course Group).

Lets an admin right-click a scheduled exam cell and:
  1. Simulate moving it to a different date / timeslot / venue and see
     whether that would collide with anything — before committing.
  2. If it collides, get ranked slot recommendations that are free.
  3. Move exams for a particular LECTURER or a particular
     PROGRAM + YEAR of students in bulk, with per-session recommendations
     and a batch-apply step.

Design notes
------------
- Conflict-checking logic is NOT duplicated from scratch — it re-uses the
  exact same exemption rules (`exam_panel_is_exempt`, `_exam_in_same_combined_group`,
  `normalize_course_code`, `get_program_year_key`) already defined in
  `exam_timetable_panel.py`, so this module can never drift out of sync
  with the manual "Save Entry" / conflicts-panel checker.
- `_find_recommendations()` is a fast APPROXIMATE scan (bulk-prefetches busy
  intervals once per date, then does in-memory interval checks) used only
  to rank candidate slots. Whatever the user actually picks is re-validated
  with the authoritative `_check_conflicts_excluding()` before anything is
  saved.
- `_get_move_bundle()` re-derives the full set of ExamTimetable rows that
  make up one printed cell (same course_code + lecturer + date/venue/time)
  from ANY single row id, so split/duplicate rows always move together
  instead of splitting apart.
"""

from datetime import datetime, timedelta
from collections import defaultdict
import json

from django.http import JsonResponse
from core.rbac import allowed_roles, Role

from timetable.models import ExamTimetable, ExamSchedulerConfig
from room_management.models import Venue
from lecturer_portal.models import Lecturer

from timetable.exam_timetable_panel import (
    exam_panel_is_exempt,
    _exam_in_same_combined_group,
    normalize_course_code,
    get_program_year_key,
    get_course_year_from_program_course,
    _make_time_slots,
    _get_effective_exam_date_range,
    _build_venues,
)


# ═══════════════════════════════════════════════════════════════════
# Slot catalog — every valid (date, day, start, end) from live config
# ═══════════════════════════════════════════════════════════════════
def _get_slot_catalog():
    """Build every valid (date, day, start, end) slot from ExamSchedulerConfig,
    so recommendations always match the live exam-scheduling configuration
    rather than being hard-coded."""
    config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)
    date_range, _has_overflow, _overflow_count = _get_effective_exam_date_range(config)
    time_slots = _make_time_slots(config)  # [(start_str '%H:%M', label), ...]

    catalog = []
    for date_str, day_name in date_range:
        for start_s, label in time_slots:
            start_t = datetime.strptime(start_s, "%H:%M")
            end_t = start_t + timedelta(hours=config.slot_size)
            catalog.append({
                'date': date_str,
                'day': day_name,
                'start': start_s,
                'end': end_t.strftime('%H:%M'),
                'label': label,
            })
    return catalog


# ═══════════════════════════════════════════════════════════════════
# Move-bundle resolution
# ═══════════════════════════════════════════════════════════════════
def _get_move_bundle(et_id):
    """
    Given ANY single ExamTimetable row id, return (representative_row, [all
    rows that print as the same cell]). Split-venue rows for the SAME
    normalised course code are intentionally excluded from the bundle (they
    are a different physical sitting, not a duplicate of this one).
    """
    try:
        et = ExamTimetable.objects.select_related(
            'course_allocation', 'course_allocation__lecturer', 'venue'
        ).get(pk=et_id)
    except ExamTimetable.DoesNotExist:
        return None, []

    alloc = et.course_allocation
    if not alloc:
        return et, [et]

    bundle_qs = ExamTimetable.objects.filter(
        date=et.date,
        start_time=et.start_time,
        end_time=et.end_time,
        venue_id=et.venue_id,
        course_allocation__course_code__iexact=alloc.course_code,
    )
    if alloc.lecturer_id:
        bundle_qs = bundle_qs.filter(course_allocation__lecturer_id=alloc.lecturer_id)
    else:
        bundle_qs = bundle_qs.filter(course_allocation__lecturer__isnull=True)

    bundle = list(bundle_qs.select_related('course_allocation'))
    if not bundle:
        bundle = [et]
    return et, bundle


# ═══════════════════════════════════════════════════════════════════
# Authoritative conflict check (mirrors exam_conflicts_api, exclusion-aware)
# ═══════════════════════════════════════════════════════════════════
def _check_conflicts_excluding(allocation, venue_input, date_str, start_t, end_t, exclude_et_ids):
    """
    Mirrors `exam_timetable_panel.exam_conflicts_api`'s pairwise rules
    (program-year, lecturer, venue + elective/intake/selection-group +
    normalised-code split-venue + Combined Course Group exemptions) but
    excludes `exclude_et_ids` (the ExamTimetable rows that make up the
    entry currently being moved) so an exam is never flagged as colliding
    with its own current slot.
    """
    messages_list = []
    error_block_save = False
    exclude_et_ids = list(exclude_et_ids or [])

    venue_obj = Venue.objects.filter(code__iexact=venue_input).first()
    norm_code = normalize_course_code(allocation.course_code)
    py_key = get_program_year_key(allocation)

    # ── PROGRAM/YEAR CONFLICTS ──────────────────────────────────────
    if allocation.program_id and py_key:
        program_overlaps = ExamTimetable.objects.filter(
            course_allocation__program_id=allocation.program_id,
            date=date_str,
        ).exclude(id__in=exclude_et_ids).select_related(
            'course_allocation', 'course_allocation__lecturer',
            'course_allocation__program_course', 'course_allocation__selection_group',
            'course_allocation__specialization_stem', 'course_allocation__student_group', 'venue',
        )

        for existing in program_overlaps:
            other_alloc = existing.course_allocation
            if not other_alloc or other_alloc.id == allocation.id:
                continue
            if get_program_year_key(other_alloc) != py_key:
                continue
            if not (existing.start_time and existing.end_time and
                    start_t < existing.end_time and existing.start_time < end_t):
                continue
            if exam_panel_is_exempt(allocation, other_alloc):
                continue
            venue_name = existing.venue.code if existing.venue else "Unknown"
            messages_list.append(
                f"❌ Conflict: this program/year already has "
                f"{other_alloc.course_code} at this time in {venue_name}."
            )
            error_block_save = True

    # ── LECTURER CONFLICTS ───────────────────────────────────────────
    if allocation.lecturer_id:
        lecturer_overlaps = ExamTimetable.objects.filter(
            course_allocation__lecturer_id=allocation.lecturer_id,
            date=date_str,
        ).exclude(id__in=exclude_et_ids).select_related('course_allocation', 'venue')

        for existing in lecturer_overlaps:
            other_alloc = existing.course_allocation
            if not other_alloc or other_alloc.id == allocation.id:
                continue
            if not (existing.start_time and existing.end_time and
                    start_t < existing.end_time and existing.start_time < end_t):
                continue
            if norm_code and norm_code == normalize_course_code(other_alloc.course_code):
                continue  # same exam split across venues — not a conflict
            if _exam_in_same_combined_group(allocation.id, other_alloc.id):
                messages_list.append(
                    f"ℹ️ Lecturer overlap allowed: {allocation.course_code} and "
                    f"{other_alloc.course_code} are in the same Combined Course Group "
                    f"(taught/examined together)."
                )
                continue
            venue_name = existing.venue.code if existing.venue else "Unknown"
            messages_list.append(
                f"❌ Conflict: Lecturer already invigilating/teaching "
                f"{other_alloc.course_code} at this time in {venue_name}."
            )
            error_block_save = True

    # ── VENUE CONFLICTS ───────────────────────────────────────────────
    venue_overlaps = ExamTimetable.objects.filter(
        venue__code__iexact=venue_input,
        date=date_str,
    ).exclude(id__in=exclude_et_ids).select_related('course_allocation', 'venue')

    for existing in venue_overlaps:
        other_alloc = existing.course_allocation
        if not other_alloc or other_alloc.id == allocation.id:
            continue
        if not (existing.start_time and existing.end_time and
                start_t < existing.end_time and existing.start_time < end_t):
            continue
        if norm_code and norm_code == normalize_course_code(other_alloc.course_code):
            continue  # intentional split-venue exam
        if _exam_in_same_combined_group(allocation.id, other_alloc.id):
            messages_list.append(
                f"ℹ️ Venue overlap allowed: {allocation.course_code} and "
                f"{other_alloc.course_code} share venue {venue_input} "
                f"as part of a Combined Course Group."
            )
            continue
        messages_list.append(
            f"❌ Venue conflict: {venue_input} already booked for "
            f"{other_alloc.course_code} at this time."
        )
        error_block_save = True

    # ── EXAM CAPACITY (warning only, never blocks) ────────────────────
    students = allocation.number_of_students or 0
    if venue_obj:
        exam_cap = venue_obj.exam_capacity if venue_obj.exam_capacity is not None else venue_obj.capacity
        if exam_cap:
            if students > exam_cap:
                messages_list.append(
                    f"⚠️ Exam capacity warning: {venue_obj.code} holds {exam_cap}, "
                    f"but exam has {students} students ({students - exam_cap} over capacity)."
                )
            else:
                messages_list.append(
                    f"✅ Exam capacity: {venue_obj.code} ({exam_cap}) can accommodate {students} students."
                )
        else:
            messages_list.append(
                f"⚠️ Exam capacity not defined for {venue_obj.code}. Please verify capacity manually."
            )
    else:
        messages_list.append(
            f"⚠️ Venue '{venue_input}' not found in database. It will be created."
        )

    return messages_list, error_block_save


# ═══════════════════════════════════════════════════════════════════
# Recommendation engine — fast approximate scan, ranked candidates
# ═══════════════════════════════════════════════════════════════════
def _find_recommendations(allocation, venues, exclude_et_ids, prefer_date=None, prefer_venue=None, limit=8):
    """
    Scan the full slot catalog × all venues and return conflict-free
    candidates, ranked by how well they match the preferred date/venue.
    Uses bulk-prefetched busy intervals (per date) instead of re-running
    the full conflict checker per candidate. Whatever is picked from here
    is still re-validated with `_check_conflicts_excluding` before saving.
    """
    catalog = _get_slot_catalog()
    exclude_et_ids = list(exclude_et_ids or [])
    py_key = get_program_year_key(allocation)

    # Lecturer busy intervals: {date: [(start,end)]}
    lecturer_busy = defaultdict(list)
    if allocation.lecturer_id:
        for t in ExamTimetable.objects.filter(
            course_allocation__lecturer_id=allocation.lecturer_id
        ).exclude(id__in=exclude_et_ids).select_related('course_allocation').only(
            'date', 'start_time', 'end_time', 'course_allocation__course_code'
        ):
            if normalize_course_code(t.course_allocation.course_code) == normalize_course_code(allocation.course_code):
                continue
            lecturer_busy[str(t.date)].append((t.start_time, t.end_time))

    # Program-year busy intervals (exemption-aware): {date: [(start,end)]}
    program_busy = defaultdict(list)
    if allocation.program_id and py_key:
        qs = ExamTimetable.objects.filter(
            course_allocation__program_id=allocation.program_id
        ).exclude(id__in=exclude_et_ids).select_related(
            'course_allocation', 'course_allocation__program_course', 'course_allocation__selection_group',
            'course_allocation__specialization_stem', 'course_allocation__student_group',
        )
        for t in qs:
            other_alloc = t.course_allocation
            if not other_alloc or get_program_year_key(other_alloc) != py_key:
                continue
            if exam_panel_is_exempt(allocation, other_alloc):
                continue
            program_busy[str(t.date)].append((t.start_time, t.end_time))

    # Venue busy intervals: {venue_code: {date: [(start,end)]}}
    venue_busy = defaultdict(lambda: defaultdict(list))
    for t in ExamTimetable.objects.exclude(id__in=exclude_et_ids).select_related('venue').only(
        'date', 'start_time', 'end_time', 'venue__code'
    ):
        if t.venue:
            venue_busy[t.venue.code][str(t.date)].append((t.start_time, t.end_time))

    def overlaps(s1, e1, s2, e2):
        return s1 < e2 and s2 < e1

    students = allocation.number_of_students or 0
    cap_of = {v['code']: (v.get('exam_capacity') or v.get('capacity') or 0) for v in venues}
    prefer_venue_l = prefer_venue.lower() if prefer_venue else None

    candidates = []
    for slot in catalog:
        date_str, day_name, start_s, end_s = slot['date'], slot['day'], slot['start'], slot['end']
        start_t = datetime.strptime(start_s, "%H:%M").time()
        end_t = datetime.strptime(end_s, "%H:%M").time()

        if any(overlaps(start_t, end_t, s, e) for (s, e) in lecturer_busy.get(date_str, [])):
            continue
        if any(overlaps(start_t, end_t, s, e) for (s, e) in program_busy.get(date_str, [])):
            continue

        for v in venues:
            vcode = v['code']
            if any(overlaps(start_t, end_t, s, e) for (s, e) in venue_busy.get(vcode, {}).get(date_str, [])):
                continue

            cap = cap_of.get(vcode, 0)
            over_cap = bool(cap and students > cap)
            score = 0
            if prefer_date and date_str == prefer_date:
                score -= 100
            if prefer_venue_l and vcode.lower() == prefer_venue_l:
                score -= 50
            if over_cap:
                score += 20

            candidates.append({
                'date': date_str, 'day': day_name, 'start': start_s, 'end': end_s,
                'venue': vcode, 'capacity': cap, 'over_capacity': over_cap,
                'score': score,
            })

    candidates.sort(key=lambda c: (c['score'], c['date'], c['start'], c['venue']))
    return candidates[:limit]


# ═══════════════════════════════════════════════════════════════════
# API — slot catalog (feeds date/slot/venue pickers)
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_slot_catalog_api(request):
    config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)
    date_range, has_overflow, overflow_count = _get_effective_exam_date_range(config)
    return JsonResponse({
        'status': 'success',
        'slots': _get_slot_catalog(),
        'venues': _build_venues(),
        'dates': [{'date': d, 'day': day} for d, day in date_range],
        'has_overflow_days': has_overflow,
        'overflow_day_count': overflow_count,
    })


# ═══════════════════════════════════════════════════════════════════
# API — check-before-you-move for a single cell
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_simulate_move_api(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'messages': ['POST required.']}, status=405)

    et_id = request.POST.get('et_id')
    date_str = request.POST.get('date')
    venue_input = (request.POST.get('venue') or '').strip()
    start_s = request.POST.get('start')
    end_s = request.POST.get('end')

    if not (et_id and date_str and venue_input and start_s and end_s):
        return JsonResponse({'status': 'error', 'messages': ['All fields are required.']}, status=400)

    try:
        start_t = datetime.strptime(start_s, "%H:%M").time()
        end_t = datetime.strptime(end_s, "%H:%M").time()
    except ValueError:
        return JsonResponse({'status': 'error', 'messages': ['Invalid time format.']}, status=400)

    et, bundle = _get_move_bundle(et_id)
    if not et or not et.course_allocation:
        return JsonResponse({'status': 'error', 'messages': ['Exam timetable entry not found.']}, status=404)

    allocation = et.course_allocation
    exclude_ids = [b.id for b in bundle]

    same_slot = (
        str(et.date) == date_str and et.start_time == start_t and et.end_time == end_t and
        et.venue and et.venue.code.lower() == venue_input.lower()
    )

    messages_list, error_block = _check_conflicts_excluding(
        allocation, venue_input, date_str, start_t, end_t, exclude_ids
    )

    recommendations = []
    if error_block:
        venues = _build_venues()
        recommendations = _find_recommendations(
            allocation, venues, exclude_ids, prefer_date=date_str, prefer_venue=venue_input, limit=8
        )

    return JsonResponse({
        'status': 'conflict' if error_block else ('same_slot' if same_slot else 'ok'),
        'messages': messages_list,
        'course_code': allocation.course_code,
        'lecturer': getattr(allocation.lecturer, 'name', 'Unassigned'),
        'bundle_size': len(bundle),
        'recommendations': recommendations,
    })


# ═══════════════════════════════════════════════════════════════════
# API — commit the move (supports force=true override)
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_execute_move_api(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'messages': ['POST required.']}, status=405)

    et_id = request.POST.get('et_id')
    date_str = request.POST.get('date')
    venue_input = (request.POST.get('venue') or '').strip()
    start_s = request.POST.get('start')
    end_s = request.POST.get('end')
    force = (request.POST.get('force') or '').lower() == 'true'

    if not (et_id and date_str and venue_input and start_s and end_s):
        return JsonResponse({'status': 'error', 'messages': ['All fields are required.']}, status=400)

    try:
        start_t = datetime.strptime(start_s, "%H:%M").time()
        end_t = datetime.strptime(end_s, "%H:%M").time()
        new_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({'status': 'error', 'messages': ['Invalid date/time format.']}, status=400)

    et, bundle = _get_move_bundle(et_id)
    if not et or not et.course_allocation:
        return JsonResponse({'status': 'error', 'messages': ['Exam timetable entry not found.']}, status=404)

    allocation = et.course_allocation
    exclude_ids = [b.id for b in bundle]

    messages_list, error_block = _check_conflicts_excluding(
        allocation, venue_input, date_str, start_t, end_t, exclude_ids
    )
    if error_block and not force:
        return JsonResponse({'status': 'error', 'messages': messages_list}, status=409)

    venue_obj, _created = Venue.objects.get_or_create(code=venue_input, defaults={'capacity': None})
    day_name = new_date.strftime('%A')
    ExamTimetable.objects.filter(id__in=exclude_ids).update(
        date=new_date, day=day_name, start_time=start_t, end_time=end_t, venue=venue_obj
    )

    n = len(bundle)
    return JsonResponse({
        'status': 'success',
        'messages': (messages_list or []) + [
            f"Moved {allocation.course_code} ({n} entr{'y' if n == 1 else 'ies'}) "
            f"to {date_str} {start_s}-{end_s} @ {venue_obj.code}."
        ],
        'moved_count': n,
    })


# ═══════════════════════════════════════════════════════════════════
# API — bulk move: scope picker options (lecturers / program+years)
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_bulk_move_scope_options_api(request):
    lecturer_ids = ExamTimetable.objects.filter(
        course_allocation__lecturer__isnull=False
    ).values_list('course_allocation__lecturer_id', flat=True).distinct()
    lecturers = list(
        Lecturer.objects.filter(id__in=lecturer_ids).order_by('name').values('id', 'name')
    )

    combos = {}
    qs = ExamTimetable.objects.filter(
        course_allocation__program__isnull=False
    ).select_related('course_allocation__program', 'course_allocation__program_course')
    for t in qs:
        alloc = t.course_allocation
        year = get_course_year_from_program_course(alloc)
        if not year or not alloc.program_id:
            continue
        key = (alloc.program_id, year)
        if key not in combos:
            combos[key] = {
                'program_id': alloc.program_id,
                'program_name': alloc.program.name,
                'year': year,
            }
    program_years = sorted(combos.values(), key=lambda c: (c['program_name'], c['year']))

    return JsonResponse({'status': 'success', 'lecturers': lecturers, 'program_years': program_years})


# ═══════════════════════════════════════════════════════════════════
# API — bulk move: all sessions for a scope, each with recommendations
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_bulk_move_candidates_api(request):
    scope_type = request.GET.get('scope_type')
    prefer_date = request.GET.get('prefer_date') or None
    prefer_venue = (request.GET.get('prefer_venue') or '').strip() or None

    if scope_type == 'lecturer':
        lecturer_id = request.GET.get('lecturer_id')
        if not lecturer_id:
            return JsonResponse({'status': 'error', 'messages': ['lecturer_id required.']}, status=400)
        qs = ExamTimetable.objects.filter(course_allocation__lecturer_id=lecturer_id)
        year_filter = None
    elif scope_type == 'program_year':
        program_id = request.GET.get('program_id')
        year_filter = request.GET.get('year')
        if not (program_id and year_filter):
            return JsonResponse({'status': 'error', 'messages': ['program_id and year required.']}, status=400)
        qs = ExamTimetable.objects.filter(course_allocation__program_id=program_id)
    else:
        return JsonResponse({'status': 'error', 'messages': ['Invalid scope_type.']}, status=400)

    qs = qs.select_related(
        'course_allocation', 'course_allocation__lecturer',
        'course_allocation__program', 'course_allocation__program_course', 'venue'
    )

    sessions = []
    bundle_map = {}
    alloc_by_bkey = {}

    for t in qs:
        alloc = t.course_allocation
        if not alloc:
            continue
        if scope_type == 'program_year' and str(get_course_year_from_program_course(alloc)) != str(year_filter):
            continue

        bkey = (str(t.date), t.start_time, t.end_time, t.venue_id,
                (alloc.course_code or '').lower(), alloc.lecturer_id)

        if bkey in bundle_map:
            bundle_map[bkey]['bundle_ids'].append(t.id)
            continue

        entry = {
            'et_id': t.id,
            'bundle_ids': [t.id],
            'course_code': alloc.course_code,
            'course_name': alloc.course_name,
            'lecturer': getattr(alloc.lecturer, 'name', 'Unassigned'),
            'program': getattr(alloc.program, 'name', 'N/A'),
            'date': str(t.date),
            'day': t.day,
            'start': t.start_time.strftime('%H:%M'),
            'end': t.end_time.strftime('%H:%M'),
            'venue': t.venue.code if t.venue else 'Unknown',
            'students': alloc.number_of_students or 0,
        }
        bundle_map[bkey] = entry
        alloc_by_bkey[bkey] = alloc
        sessions.append(entry)

    venues = _build_venues()
    for bkey, entry in bundle_map.items():
        alloc = alloc_by_bkey[bkey]
        entry['recommendations'] = _find_recommendations(
            alloc, venues, entry['bundle_ids'],
            prefer_date=prefer_date, prefer_venue=prefer_venue, limit=5,
        )

    sessions.sort(key=lambda e: (e['date'], e['start']))
    return JsonResponse({'status': 'success', 'sessions': sessions})


# ═══════════════════════════════════════════════════════════════════
# API — bulk move: apply a batch of moves in one call
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_bulk_move_execute_api(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'messages': ['POST required.']}, status=405)

    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'messages': ['Invalid JSON.']}, status=400)

    moves = payload.get('moves') or []
    global_force = bool(payload.get('force'))
    if not moves:
        return JsonResponse({'status': 'error', 'messages': ['No moves supplied.']}, status=400)

    applied, skipped = [], []

    for mv in moves:
        et_id = mv.get('et_id')
        date_str = mv.get('date')
        venue_input = (mv.get('venue') or '').strip()
        start_s = mv.get('start')
        end_s = mv.get('end')

        if not (et_id and date_str and venue_input and start_s and end_s):
            skipped.append({'et_id': et_id, 'reason': 'Missing fields.'})
            continue
        try:
            start_t = datetime.strptime(start_s, "%H:%M").time()
            end_t = datetime.strptime(end_s, "%H:%M").time()
            new_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            skipped.append({'et_id': et_id, 'reason': 'Invalid date/time.'})
            continue

        et, bundle = _get_move_bundle(et_id)
        if not et or not et.course_allocation:
            skipped.append({'et_id': et_id, 'reason': 'Entry not found (already moved?).'})
            continue

        allocation = et.course_allocation
        exclude_ids = [b.id for b in bundle]

        messages_list, error_block = _check_conflicts_excluding(
            allocation, venue_input, date_str, start_t, end_t, exclude_ids
        )
        if error_block and not (global_force or mv.get('force')):
            skipped.append({
                'et_id': et_id,
                'course_code': allocation.course_code,
                'reason': '; '.join(m for m in messages_list if '❌' in m) or 'Conflict detected.',
            })
            continue

        venue_obj, _c = Venue.objects.get_or_create(code=venue_input, defaults={'capacity': None})
        day_name = new_date.strftime('%A')
        ExamTimetable.objects.filter(id__in=exclude_ids).update(
            date=new_date, day=day_name, start_time=start_t, end_time=end_t, venue=venue_obj
        )
        applied.append({
            'et_id': et_id, 'course_code': allocation.course_code,
            'moved_count': len(bundle), 'date': date_str, 'start': start_s, 'venue': venue_obj.code,
        })

    return JsonResponse({
        'status': 'success',
        'applied_count': len(applied),
        'skipped_count': len(skipped),
        'applied': applied,
        'skipped': skipped,
    })
