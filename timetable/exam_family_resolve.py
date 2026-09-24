"""
timetable/exam_family_resolve.py
=================================
Course-family aware placement helpers for the Exam Timetable Panel
("/exam/timetable/"). Two related but distinct features live here:

FEATURE A — "left behind" family member
----------------------------------------
A "course family" is every allocation sharing the same family code
(`exam_timetable_panel.get_course_family_code`, e.g. "ECON306" whether it
reads "ECON 306", "ECON306A" etc.) across different programmes/years. When
most members of a family have already been scheduled at one date+slot but
one member is still sitting in the unscheduled list, that is very likely
an oversight rather than an intentional split. This module:
  * flags every such "left behind" course (`exam_family_left_behind_api`),
  * proposes a tiered set of ways to seat it alongside its family
    (`exam_family_resolve_options_api`):
        TIER 1 — a single free room in the SAME BUILDING as the sibling's
                 venue, at the sibling's exact date+slot.
        TIER 2 — no single same-building room is big enough / free, so
                 split the course across several same-building rooms at
                 that same date+slot (reuses `_find_split_venues`).
        TIER 3 — even a same-building split isn't possible, so fall back
                 to any fully conflict-free slot anywhere else (breaks
                 family alignment — flagged as such).
  * commits whichever option the admin clicks (`exam_family_resolve_execute_api`),
    re-validating with the exact same authoritative
    `exam_simulate_move._check_conflicts_excluding` used everywhere else
    in this panel.

FEATURE B — same-course-code sibling group placement
------------------------------------------------------
Distinct from a "family" (same subject+number across different courses),
this covers the case where the *exact same* course code is offered to
several programmes/years and therefore exists as several *separate*,
still-unscheduled `CourseAllocation` rows (e.g. "COSC 103" unscheduled for
three different programmes). When the admin places ONE of those rows into
a slot, this module checks whether placing *every* still-unscheduled
sibling with that same normalised code into that same slot would also
work (`exam_family_group_preview_api`) — checking both real collisions
(each sibling is re-validated against unrelated already-scheduled exams)
and room capacity for the combined headcount. If they would collide or
overflow, an alternative (same-building split, or a different slot
entirely) is offered. The admin can always override and place just the
one course alone. `exam_family_group_execute_api` commits whichever
choice is made.

Design notes
------------
- No conflict-checking logic is duplicated from scratch: this module
  reuses `exam_timetable_panel.get_course_family_code` /
  `normalize_course_code` / `_scheduled_excluded_ids` / `_find_split_venues`
  and `exam_simulate_move._check_conflicts_excluding` /
  `_find_recommendations`, so it can never drift out of sync with the
  manual "Save Entry" checker or the other move/evacuation features.
- Same-normalised-code allocations are already treated as an intentional
  co-scheduling everywhere else in this panel (see `_handle_ajax_add`'s
  lecturer/venue checks) — this module relies on that existing exemption
  and focuses purely on ROOM CAPACITY and each member's clashes against
  unrelated exams.
- Nothing here is ever committed without a fresh, authoritative
  `_check_conflicts_excluding` re-check at execute time, even though the
  preview/options endpoints already screened candidates.
"""

from datetime import datetime, timedelta
from collections import defaultdict
import json
import re

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from core.rbac import allowed_roles, Role
from course_allocation.models import CourseAllocation
from course_allocation.allocation_scope import apply_tt_scope, resolve_tt_scope
from room_management.models import Venue
from timetable.models import ExamTimetable, ExamSchedulerConfig, SharedVenueExamGroup

from timetable.exam_timetable_panel import (
    get_course_family_code,
    normalize_course_code,
    _scheduled_excluded_ids,
    _find_split_venues,
    _build_venues,
)
from timetable.exam_simulate_move import (
    _find_recommendations,
    _check_conflicts_excluding,
)


# ═══════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════
def _unscheduled_qs(tt_scope):
    """Mirrors `exam_timetable_panel.load_exam_timetable_data`'s
    stage='unscheduled' population exactly, so a course this module treats
    as "unscheduled" always matches what the panel's own unscheduled list
    shows the admin."""
    excluded = _scheduled_excluded_ids(tt_scope)
    return apply_tt_scope(CourseAllocation.objects, scope=tt_scope).exclude(id__in=excluded)


def _scheduled_family_index():
    """family_code -> [ExamTimetable rows] for every currently scheduled
    exam, built once so `exam_family_left_behind_api` doesn't re-query the
    whole ExamTimetable table per unscheduled course."""
    idx = defaultdict(list)
    qs = (
        ExamTimetable.objects
        .exclude(course_allocation__isnull=True)
        .select_related('course_allocation', 'venue')
    )
    for et in qs:
        fam = get_course_family_code(et.course_allocation.course_code)
        if fam:
            idx[fam].append(et)
    return idx


def _left_behind_sibling(alloc, family_index):
    """The first scheduled ExamTimetable row belonging to the SAME family
    but a DIFFERENT normalised code than `alloc` — i.e. `alloc` is a
    family member that hasn't joined the rest of its family yet."""
    fam = get_course_family_code(alloc.course_code)
    if not fam:
        return None
    norm = normalize_course_code(alloc.course_code)
    for et in family_index.get(fam, []):
        if normalize_course_code(et.course_allocation.course_code) != norm:
            return et
    return None


def _create_direct_entry(alloc, venue_obj, exam_date_obj, slot_start, slot_end):
    return ExamTimetable.objects.create(
        course_allocation=alloc,
        venue=venue_obj,
        day=exam_date_obj.strftime('%A'),
        date=exam_date_obj,
        start_time=slot_start,
        end_time=slot_end,
        allocated_students=alloc.number_of_students or 0,
    )


def _entry_payload(entry, alloc):
    """Same shape as `_handle_ajax_add`'s `new_entry`, so the frontend can
    push these straight into `timetableData` / `addEntryToDOM` unchanged."""
    return {
        'id': entry.id,
        'date': str(entry.date),
        'day': entry.day,
        'start_time': entry.start_time.strftime('%H:%M'),
        'end_time': entry.end_time.strftime('%H:%M'),
        'venue': entry.venue.code if entry.venue else None,
        'venue_id': entry.venue_id,
        'course_code': alloc.course_code,
        'normalized_code': normalize_course_code(alloc.course_code),
        'course_name': alloc.course_name or '',
        'lecturer': getattr(alloc.lecturer, 'name', 'Unassigned'),
        'program': getattr(alloc.program, 'name', 'N/A'),
        'year': alloc.program_course.year if alloc.program_course else None,
        'students': alloc.number_of_students or 0,
        'allocation_id': alloc.id,
        'type': 'direct',
    }


# ═══════════════════════════════════════════════════════════════════
# FEATURE A — left-behind family member: detect + resolve
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def exam_family_left_behind_api(request):
    """Every still-unscheduled course whose course-family already has at
    least one OTHER member scheduled somewhere. Feeds a warning badge +
    "Resolve" button on the unscheduled-courses panel."""
    tt_scope = resolve_tt_scope(request)
    family_index = _scheduled_family_index()

    notices = []
    allocs = (
        _unscheduled_qs(tt_scope)
        .select_related('lecturer', 'program', 'program_course')
        .only(
            'id', 'course_code', 'course_name', 'number_of_students',
            'lecturer__name', 'program__name',
            'program_course__year', 'program_course__program_id',
        )
    )
    for alloc in allocs:
        sib_et = _left_behind_sibling(alloc, family_index)
        if not sib_et:
            continue
        sib = sib_et.course_allocation
        notices.append({
            'allocation_id': alloc.id,
            'course_code': alloc.course_code,
            'course_name': alloc.course_name or '',
            'program': getattr(alloc.program, 'name', 'N/A'),
            'year': alloc.program_course.year if alloc.program_course else None,
            'students': alloc.number_of_students or 0,
            'family_code': get_course_family_code(alloc.course_code),
            'sibling': {
                'course_code': sib.course_code,
                'date': str(sib_et.date),
                'start_time': sib_et.start_time.strftime('%H:%M') if sib_et.start_time else None,
                'end_time': sib_et.end_time.strftime('%H:%M') if sib_et.end_time else None,
                'venue': sib_et.venue.code if sib_et.venue else None,
            },
        })

    return JsonResponse({'status': 'success', 'count': len(notices), 'notices': notices})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def exam_family_resolve_options_api(request):
    """Tiered placement options for ONE left-behind family course, always
    trying to seat it next to its already-scheduled family first."""
    alloc_id = request.GET.get('allocation_id')
    if not alloc_id:
        return JsonResponse({'status': 'error', 'messages': ['allocation_id required.']}, status=400)

    alloc = get_object_or_404(
        CourseAllocation.objects.select_related('lecturer', 'program', 'program_course'),
        id=alloc_id,
    )

    sib_et = _left_behind_sibling(alloc, _scheduled_family_index())
    if not sib_et:
        return JsonResponse({'status': 'empty', 'messages': ['No scheduled family sibling found for this course.']})

    exam_date_obj = sib_et.date
    date_str = str(exam_date_obj)
    slot_start = sib_et.start_time
    slot_end = sib_et.end_time
    students = alloc.number_of_students or 0
    sibling_venue = sib_et.venue

    options = []

    # ── TIER 1 — a single free room, same building, same slot ──────────
    if sibling_venue is not None:
        pool = _find_split_venues(sibling_venue, exam_date_obj, slot_start, slot_end, students, 0)
        for c in pool:
            if not c['same_building'] or (c['exam_capacity'] or 0) < students:
                continue
            _msgs, blocked = _check_conflicts_excluding(alloc, c['code'], date_str, slot_start, slot_end, [])
            if blocked:
                continue
            options.append({
                'option_type': 'same_building_room',
                'label': (
                    f"Place in {c['code']} (same building as {sibling_venue.code}) at "
                    f"{slot_start.strftime('%H:%M')}\u2013{slot_end.strftime('%H:%M')} on {date_str}."
                ),
                'date': date_str,
                'start': slot_start.strftime('%H:%M'),
                'end': slot_end.strftime('%H:%M'),
                'venue': c['code'],
            })
            break

    # ── TIER 2 — split across same-building rooms, same slot ───────────
    if not options and sibling_venue is not None:
        pool = [c for c in _find_split_venues(
            sibling_venue, exam_date_obj, slot_start, slot_end, students, 0
        ) if c['same_building']]
        chosen, total = [], 0
        for c in pool:
            _msgs, blocked = _check_conflicts_excluding(alloc, c['code'], date_str, slot_start, slot_end, [])
            if blocked:
                continue
            chosen.append(c['code'])
            total += c['exam_capacity'] or 0
            if total >= students:
                break
        if chosen and total >= students and len(chosen) > 1:
            options.append({
                'option_type': 'same_building_split',
                'label': (
                    f"Split across {', '.join(chosen)} (same building as {sibling_venue.code}) at "
                    f"{slot_start.strftime('%H:%M')}\u2013{slot_end.strftime('%H:%M')} on {date_str}."
                ),
                'date': date_str,
                'start': slot_start.strftime('%H:%M'),
                'end': slot_end.strftime('%H:%M'),
                'venues': chosen,
            })

    # ── TIER 3 — give up on the same slot, find ANY conflict-free slot ──
    if not options:
        venues = _build_venues()
        for r in _find_recommendations(alloc, venues, [], limit=5):
            options.append({
                'option_type': 'alternative_slot',
                'label': (
                    f"\u26a0\ufe0f No room could be found alongside "
                    f"{sib_et.course_allocation.course_code} — place at "
                    f"{r['date']} {r['start']}\u2013{r['end']} @ {r['venue']} instead "
                    f"(breaks family alignment)."
                ),
                'date': r['date'],
                'start': r['start'],
                'end': r['end'],
                'venue': r['venue'],
            })

    return JsonResponse({
        'status': 'success',
        'allocation_id': alloc.id,
        'course_code': alloc.course_code,
        'sibling': {
            'course_code': sib_et.course_allocation.course_code,
            'date': date_str,
            'start_time': slot_start.strftime('%H:%M'),
            'end_time': slot_end.strftime('%H:%M'),
            'venue': sibling_venue.code if sibling_venue else None,
        },
        'options': options,
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def exam_family_resolve_execute_api(request):
    """Commits whichever option `exam_family_resolve_options_api` offered
    and the admin clicked. Re-validates authoritatively before writing."""
    try:
        alloc_id = request.POST.get('allocation_id')
        option_type = request.POST.get('option_type')
        date_str = request.POST.get('date')
        start_s = request.POST.get('start')
        end_s = request.POST.get('end')
        venue_code = (request.POST.get('venue') or '').strip()
        venues_raw = request.POST.get('venues')

        if not all([alloc_id, option_type, date_str, start_s, end_s]):
            return JsonResponse({'status': 'error', 'messages': ['Missing required fields.']})

        alloc = get_object_or_404(
            CourseAllocation.objects.select_related('lecturer', 'program', 'program_course'),
            id=alloc_id,
        )
        exam_date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        slot_start = datetime.strptime(start_s, '%H:%M').time()
        slot_end = datetime.strptime(end_s, '%H:%M').time()

        if ExamTimetable.objects.filter(course_allocation=alloc).exists():
            return JsonResponse({'status': 'error', 'messages': [f'{alloc.course_code} is already scheduled.']})

        if option_type == 'same_building_split':
            try:
                venue_codes = json.loads(venues_raw or '[]')
            except (ValueError, json.JSONDecodeError):
                return JsonResponse({'status': 'error', 'messages': ['Invalid venues list.']})
            if not venue_codes:
                return JsonResponse({'status': 'error', 'messages': ['No venues supplied for split.']})

            booked = list(ExamTimetable.objects.filter(
                venue__code__in=venue_codes, date=exam_date_obj,
                start_time__lt=slot_end, end_time__gt=slot_start,
            ).values_list('venue__code', flat=True))
            if booked:
                return JsonResponse({
                    'status': 'error',
                    'messages': [f'Venue(s) {", ".join(booked)} were just booked — refresh options.'],
                })

            venues = list(Venue.objects.filter(code__in=venue_codes))
            if not venues:
                return JsonResponse({'status': 'error', 'messages': ['Venues not found.']})
            primary_venue = venues[0]

            with transaction.atomic():
                primary_entry = _create_direct_entry(alloc, primary_venue, exam_date_obj, slot_start, slot_end)
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
                if hasattr(group, 'notes') and len(venues) > 1:
                    group.notes = f"Split: primary={primary_venue.code}, extra={', '.join(v.code for v in venues[1:])}"
                    group.save(update_fields=['notes'])

            return JsonResponse({
                'status': 'success',
                'messages': [
                    f"\u2705 {alloc.course_code} placed alongside its family, "
                    f"split across {', '.join(v.code for v in venues)}.",
                ],
                'new_entry': _entry_payload(primary_entry, alloc),
            })

        # 'same_building_room' or 'alternative_slot' — one plain placement
        if not venue_code:
            return JsonResponse({'status': 'error', 'messages': ['venue is required.']})

        msgs, blocked = _check_conflicts_excluding(alloc, venue_code, date_str, slot_start, slot_end, [])
        if blocked:
            return JsonResponse({
                'status': 'error',
                'messages': msgs or ['This option is no longer conflict-free — refresh options.'],
            })

        venue_obj, _c = Venue.objects.get_or_create(code=venue_code, defaults={'capacity': None})
        entry = _create_direct_entry(alloc, venue_obj, exam_date_obj, slot_start, slot_end)

        return JsonResponse({
            'status': 'success',
            'messages': [
                f'\u2705 {alloc.course_code} scheduled at {venue_code} on {date_str} {start_s}, '
                f'alongside its course family.',
            ],
            'new_entry': _entry_payload(entry, alloc),
        })
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'messages': [str(exc)]})


# ═══════════════════════════════════════════════════════════════════
# FEATURE B — same-course-code sibling group placement
# ═══════════════════════════════════════════════════════════════════
def _unscheduled_siblings_by_code(alloc, tt_scope):
    """Every OTHER still-unscheduled CourseAllocation with the exact same
    normalised course code as `alloc` (distinct rows because they belong
    to different programmes/years, e.g. COSC 103 offered in 3 places)."""
    norm = normalize_course_code(alloc.course_code)
    out = []
    qs = (
        _unscheduled_qs(tt_scope)
        .select_related('lecturer', 'program', 'program_course')
        .exclude(id=alloc.id)
    )
    for other in qs:
        if normalize_course_code(other.course_code) == norm:
            out.append(other)
    return out


def _describe_alloc(a):
    return {
        'allocation_id': a.id,
        'course_code': a.course_code,
        'program': getattr(a.program, 'name', 'N/A'),
        'year': a.program_course.year if a.program_course else None,
        'lecturer': getattr(a.lecturer, 'name', 'Unassigned'),
        'students': a.number_of_students or 0,
    }


# ═══════════════════════════════════════════════════════════════════
# Group placement planner
# ═══════════════════════════════════════════════════════════════════
# The planner answers: "for THIS set of sections (same course code, all
# still unscheduled), which complete seating arrangements exist?".
# An arrangement ("plan") maps every section to one room, or — when a
# section is bigger than any single free room — to several rooms.
# Sections may also share a room (same code = same paper, already exempt
# from venue clashes everywhere else in this panel).
#
# Plans are generated for the slot the admin picked first, then for every
# other slot/day where all sections are individually clash-free, and are
# ranked: one room  >  one building  >  several buildings.

_EXTRA_VENUES_RE = re.compile(r'extra\s*=\s*(.+)$', re.I)
_MAX_PLANS_REQUESTED_SLOT = 8
_MAX_OTHER_SLOTS = 12


def _extra_codes_from_notes(notes):
    """Split-venue groups only store their extra rooms in `notes`
    ("Split: primary=X, extra=A, B"). Parse them so those rooms are
    treated as booked too."""
    m = _EXTRA_VENUES_RE.search(notes or '')
    if not m:
        return []
    return [c.strip() for c in m.group(1).split(',') if c.strip()]


class _BusyIndex:
    """Every room booking (incl. split-venue extra rooms), indexed once by
    date so scanning many slots never re-queries the database."""

    def __init__(self):
        self.rows = defaultdict(list)  # 'YYYY-MM-DD' -> [(start, end, code)]
        for d, s, e, code in ExamTimetable.objects.filter(
            venue__isnull=False
        ).values_list('date', 'start_time', 'end_time', 'venue__code'):
            if d and s and e and code:
                self.rows[str(d)].append((s, e, code))
        for g in SharedVenueExamGroup.objects.all():
            for code in _extra_codes_from_notes(getattr(g, 'notes', '')):
                if g.date and g.start_time and g.end_time:
                    self.rows[str(g.date)].append((g.start_time, g.end_time, code))

    def booked(self, date_str, start, end):
        return {c for (s, e, c) in self.rows.get(date_str, ()) if s < end and start < e}


def _venue_catalog():
    """code -> {code, cap, building_id, building, ...}. Blocked and
    specialised rooms are flagged so automatic suggestions can skip them."""
    flags = {v['code']: v for v in _build_venues(include_from_timetables=False)}
    out = {}
    for v in Venue.objects.select_related('building'):
        f = flags.get(v.code, {})
        cap = (v.exam_capacity if v.exam_capacity is not None else v.capacity) or 0
        out[v.code] = {
            'code': v.code,
            'cap': cap,
            'building_id': v.building_id,
            'building': v.building.name if v.building else None,
            'blocked': bool(f.get('is_blocked')),
            'specialized': bool(f.get('is_specialized') or getattr(v, 'is_specialized', False)),
        }
    return out


def _free_rooms(catalog, busy, date_str, start, end):
    taken = busy.booked(date_str, start, end)
    return [
        r for r in catalog.values()
        if r['cap'] > 0 and not r['blocked'] and not r['specialized'] and r['code'] not in taken
    ]


def _member_free_slots(alloc):
    """{(date, start, end)} where `alloc` has no lecturer / programme-year
    clash. Reuses the panel's own recommendation scan with a dummy venue so
    the exemption rules cannot drift out of sync."""
    dummy = [{'code': '__any__', 'capacity': 0, 'exam_capacity': 0}]
    recs = _find_recommendations(alloc, dummy, [], limit=100000)
    return {(r['date'], r['start'], r['end']) for r in recs}


def _choose_split_rooms(pool, need, prefer_building_id=None):
    """Fewest rooms whose combined capacity covers `need`, kept inside one
    building when a building can do it alone."""
    def cover(rooms):
        chosen, total = [], 0
        for r in sorted(rooms, key=lambda x: (-x['cap'], x['code'])):
            chosen.append(r)
            total += r['cap']
            if total >= need:
                break
        if total < need:
            return None
        while len(chosen) > 1 and sum(r['cap'] for r in chosen[:-1]) >= need:
            chosen.pop()
        return chosen

    by_b = defaultdict(list)
    for r in pool:
        if r['building_id'] is not None:
            by_b[r['building_id']].append(r)
    best = None
    for bid, rooms in by_b.items():
        c = cover(rooms)
        if c is None:
            continue
        key = (len(c), 0 if bid == prefer_building_id else 1, sum(r['cap'] for r in c))
        if best is None or key < best[0]:
            best = (key, c)
    if best:
        return best[1]
    return cover(pool)


def _pack(group, rooms, prefer_code=None, prefer_building_id=None):
    """Seat every section inside `rooms`. Returns [(alloc, [room, ...])] or
    None. Biggest section first; sections share leftover space in an
    already-used room; a section too big for any single room is split."""
    remaining = {r['code']: r['cap'] for r in rooms}
    by_code = {r['code']: r for r in rooms}
    used, used_set, out = [], set(), []

    for a in sorted(group, key=lambda x: -(x.number_of_students or 0)):
        need = a.number_of_students or 0

        shared = [c for c in used if remaining[c] >= need]
        if shared:
            code = min(shared, key=lambda c: (remaining[c], c))
            remaining[code] -= need
            out.append((a, [by_code[code]]))
            continue

        fresh = [r for r in rooms if r['code'] not in used_set and remaining[r['code']] >= need]
        if fresh:
            r = min(fresh, key=lambda x: (0 if x['code'] == prefer_code else 1, x['cap'], x['code']))
            used.append(r['code']); used_set.add(r['code'])
            remaining[r['code']] -= need
            out.append((a, [r]))
            continue

        pool = [r for r in rooms if r['code'] not in used_set]
        chosen = _choose_split_rooms(pool, need, prefer_building_id)
        if not chosen:
            return None
        for r in chosen:
            used.append(r['code']); used_set.add(r['code'])
            remaining[r['code']] = 0
        out.append((a, chosen))
    return out


def _plan_signature(assign):
    return tuple(sorted((a.id, tuple(sorted(r['code'] for r in rs))) for a, rs in assign))


def _plan_scope(assign):
    rooms = {r['code']: r for _a, rs in assign for r in rs}
    if len(rooms) == 1:
        return 'single_room'
    bids = {r['building_id'] for r in rooms.values()}
    if len(bids) == 1 and None not in bids:
        return 'same_building'
    return 'multi_building'


_SCOPE_RANK = {'single_room': 0, 'same_building': 1, 'multi_building': 2}
_SCOPE_LABEL = {
    'single_room': 'One room',
    'same_building': 'One building',
    'multi_building': 'Several buildings',
}


def _plans_for_slot(group, rooms, prefer_code=None, prefer_building_id=None, limit=8):
    """Distinct, ranked seating plans for one date+slot."""
    if not rooms:
        return []
    total = sum(a.number_of_students or 0 for a in group)
    found, seen = [], set()

    def add(assign):
        if not assign:
            return
        sig = _plan_signature(assign)
        if sig in seen:
            return
        seen.add(sig)
        found.append(assign)

    # 1) Everyone together in one room.
    singles = sorted(
        [r for r in rooms if r['cap'] >= total],
        key=lambda r: (
            0 if r['code'] == prefer_code else 1,
            0 if (prefer_building_id is not None and r['building_id'] == prefer_building_id) else 1,
            r['cap'], r['code'],
        ),
    )[:3]
    for r in singles:
        add([(a, [r]) for a in group])

    # 2) Each building on its own.
    by_b = defaultdict(list)
    for r in rooms:
        if r['building_id'] is not None:
            by_b[r['building_id']].append(r)
    per_building = []
    for bid, pool in by_b.items():
        res = _pack(group, pool, prefer_code, prefer_building_id)
        if res:
            n_rooms = len({r['code'] for _a, rs in res for r in rs})
            n_split = sum(1 for _a, rs in res if len(rs) > 1)
            per_building.append((
                (0 if bid == prefer_building_id else 1, n_rooms, n_split), res,
            ))
    per_building.sort(key=lambda x: x[0])
    for _k, res in per_building[:4]:
        add(res)

    # 3) Anywhere.
    add(_pack(group, rooms, prefer_code, prefer_building_id))

    def rank(assign):
        rms = {r['code'] for _a, rs in assign for r in rs}
        return (
            _SCOPE_RANK[_plan_scope(assign)],
            len(rms),
            sum(1 for _a, rs in assign if len(rs) > 1),
            0 if prefer_code in rms else 1,
        )

    found.sort(key=rank)
    return found[:limit]


def _plan_to_option(assign, date_str, day_name, start_s, end_s, prefer_code, group_tag):
    room_load = defaultdict(int)
    rooms_meta = {}
    assignments = []
    for a, rs in assign:
        need = a.number_of_students or 0
        for r in rs:
            rooms_meta[r['code']] = r
        if len(rs) == 1:
            room_load[rs[0]['code']] += need
        else:
            left = need
            for r in rs:
                take = min(left, r['cap'])
                room_load[r['code']] += take
                left -= take
        assignments.append({
            'allocation_id': a.id,
            'course_code': a.course_code,
            'program': getattr(a.program, 'name', 'N/A'),
            'year': a.program_course.year if a.program_course else None,
            'students': need,
            'split': len(rs) > 1,
            'venues': [{'code': r['code'], 'capacity': r['cap'], 'building': r['building']} for r in rs],
        })
    rooms_list = [
        {
            'code': c, 'capacity': m['cap'], 'building': m['building'],
            'load': room_load[c],
        }
        for c, m in rooms_meta.items()
    ]
    rooms_list.sort(key=lambda r: (-r['load'], r['code']))
    scope = _plan_scope(assign)
    return {
        'option_type': 'plan',
        'group': group_tag,
        'scope': scope,
        'scope_label': _SCOPE_LABEL[scope],
        'date': date_str,
        'day': day_name,
        'start': start_s,
        'end': end_s,
        'assignments': assignments,
        'rooms': rooms_list,
        'seats_used': sum(r['load'] for r in rooms_list),
        'seats_total': sum(r['capacity'] for r in rooms_list),
        'buildings': sorted({r['building'] for r in rooms_list if r['building']}),
        'uses_requested_venue': bool(prefer_code and prefer_code in rooms_meta),
    }


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def exam_family_group_preview_api(request):
    """Called right before a normal placement is submitted. If the course
    being placed shares its exact code with other still-unscheduled
    allocations, returns EVERY workable way to seat the whole group:
      - at the slot the admin picked (one room / one building / several
        buildings, sections sharing or splitting rooms as needed), and
      - at every other slot/day where all sections are clash-free."""
    alloc_id = request.GET.get('allocation_id')
    venue_code = (request.GET.get('venue') or '').strip()
    date_str = request.GET.get('exam_date')
    slot_str = request.GET.get('slot_time')
    if not all([alloc_id, venue_code, date_str, slot_str]):
        return JsonResponse(
            {'status': 'error', 'messages': ['allocation_id, venue, exam_date, slot_time required.']},
            status=400,
        )

    tt_scope = resolve_tt_scope(request)
    alloc = get_object_or_404(
        CourseAllocation.objects.select_related('lecturer', 'program', 'program_course'),
        id=alloc_id,
    )
    config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)
    slot_start = datetime.strptime(slot_str, '%H:%M').time()
    slot_end = (datetime.combine(datetime.today(), slot_start) + timedelta(hours=config.slot_size)).time()
    slot_end_s = slot_end.strftime('%H:%M')
    req_date = datetime.strptime(date_str, '%Y-%m-%d').date()

    siblings = _unscheduled_siblings_by_code(alloc, tt_scope)
    if not siblings:
        return JsonResponse({'status': 'no_family', 'messages': ['No other unscheduled course shares this code.']})

    group = [alloc] + siblings
    total_students = sum(a.number_of_students or 0 for a in group)

    catalog = _venue_catalog()
    busy = _BusyIndex()
    req_venue = catalog.get(venue_code) or next(
        (v for c, v in catalog.items() if c.lower() == venue_code.lower()), None
    )
    prefer_code = req_venue['code'] if req_venue else None
    prefer_bid = req_venue['building_id'] if req_venue else None

    # ── Are the sections themselves free at the requested slot? ─────────
    # (lecturer / programme-year clashes only; ROOM availability is handled
    # by the planner so that one taken room never hides other rooms.)
    member_collisions = []
    for a in group:
        msgs, _blocked = _check_conflicts_excluding(a, venue_code, date_str, slot_start, slot_end, [])
        bad = [m for m in msgs if '\u274c' in m and 'Venue conflict' not in m]
        if bad:
            member_collisions.append({'allocation_id': a.id, 'course_code': a.course_code, 'messages': bad})
    requested_slot_ok = not member_collisions

    options = []
    day_name_req = req_date.strftime('%A')

    # ── Plans at the requested slot ─────────────────────────────────────
    free_here = _free_rooms(catalog, busy, date_str, slot_start, slot_end)
    if requested_slot_ok:
        for assign in _plans_for_slot(group, free_here, prefer_code, prefer_bid, _MAX_PLANS_REQUESTED_SLOT):
            options.append(_plan_to_option(assign, date_str, day_name_req, slot_str, slot_end_s, prefer_code, 'requested'))

    # ── Plans at every other slot where all sections are free ───────────
    common = None
    for a in group:
        free = _member_free_slots(a)
        common = free if common is None else (common & free)
    common = common or set()
    common.discard((date_str, slot_str, slot_end_s))

    def slot_key(k):
        d = datetime.strptime(k[0], '%Y-%m-%d').date()
        st = datetime.strptime(k[1], '%H:%M')
        t0 = datetime.strptime(slot_str, '%H:%M')
        return (abs((d - req_date).days), abs((st - t0).total_seconds()), k[0], k[1])

    other_found = 0
    for (d_s, s_s, e_s) in sorted(common, key=slot_key):
        if other_found >= _MAX_OTHER_SLOTS:
            break
        st = datetime.strptime(s_s, '%H:%M').time()
        en = datetime.strptime(e_s, '%H:%M').time()
        rooms = _free_rooms(catalog, busy, d_s, st, en)
        plans = _plans_for_slot(group, rooms, prefer_code, prefer_bid, 1)
        if not plans:
            continue
        d_obj = datetime.strptime(d_s, '%Y-%m-%d').date()
        tag = 'same_day' if d_s == date_str else 'other_day'
        options.append(_plan_to_option(plans[0], d_s, d_obj.strftime('%A'), s_s, e_s, prefer_code, tag))
        other_found += 1

    biggest_free = max((r['cap'] for r in free_here), default=0)
    return JsonResponse({
        'status': 'success',
        'target_allocation_id': alloc.id,
        'group': [_describe_alloc(a) for a in group],
        'total_students': total_students,
        'requested': {
            'venue': venue_code, 'date': date_str, 'start': slot_str, 'end': slot_end_s,
            'capacity': (req_venue or {}).get('cap', 0),
        },
        'requested_slot_ok': requested_slot_ok,
        'fits_at_requested': any(o['group'] == 'requested' for o in options),
        'member_collisions': member_collisions,
        'free_rooms_at_requested': len(free_here),
        'largest_free_room_at_requested': biggest_free,
        'options': options,
    })


def _venue_taken_by_other(code, date_obj, start, end, norm):
    """Name of the exam blocking `code` at that slot, or None. Rows that
    belong to the same normalised course code are the intentional
    shared-paper case and never block."""
    for et in ExamTimetable.objects.filter(
        venue__code__iexact=code, date=date_obj, start_time__lt=end, end_time__gt=start
    ).select_related('course_allocation'):
        ca = et.course_allocation
        if ca and normalize_course_code(ca.course_code) == norm:
            continue
        return ca.course_code if ca else 'another exam'
    for g in SharedVenueExamGroup.objects.filter(date=date_obj, start_time__lt=end, end_time__gt=start):
        if code.lower() in [c.lower() for c in _extra_codes_from_notes(getattr(g, 'notes', ''))]:
            cas = list(g.course_allocations.all())
            if any(normalize_course_code(c.course_code) == norm for c in cas):
                continue
            return cas[0].course_code if cas else 'another exam'
    return None


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def exam_family_group_execute_api(request):
    """Commits a same-course-code group placement.

    mode:
      'alone'          — place only the ids supplied (normally just the one
                          course the admin originally clicked), overriding
                          the group suggestion.
      'all_here'       — every id, same single venue+slot.
      'all_here_split' — every id, greedily packed across `venues` (same
                          slot), filling each room to its capacity before
                          moving to the next.
      'all_elsewhere'  — every id, same single venue but a different
                          date/slot than originally requested.
      'plan'           — every id, per-section rooms (one room, several
                          rooms shared, or a section split across rooms)
                          exactly as chosen from the preview's options.
    """
    try:
        mode = request.POST.get('mode')
        alloc_ids_raw = request.POST.get('allocation_ids', '[]')
        date_str = request.POST.get('exam_date')
        slot_str = request.POST.get('slot_time')
        venue_code = (request.POST.get('venue') or '').strip()
        venues_raw = request.POST.get('venues')
        force = request.POST.get('force') == 'true'

        if not all([mode, date_str, slot_str]):
            return JsonResponse({'status': 'error', 'messages': ['mode, exam_date, slot_time required.']})

        try:
            alloc_ids = json.loads(alloc_ids_raw)
        except (ValueError, json.JSONDecodeError):
            return JsonResponse({'status': 'error', 'messages': ['Invalid allocation_ids.']})
        if not alloc_ids:
            return JsonResponse({'status': 'error', 'messages': ['allocation_ids required.']})

        config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)
        slot_start = datetime.strptime(slot_str, '%H:%M').time()
        slot_end = (datetime.combine(datetime.today(), slot_start) + timedelta(hours=config.slot_size)).time()
        exam_date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()

        allocs = list(
            CourseAllocation.objects
            .select_related('lecturer', 'program', 'program_course')
            .filter(id__in=alloc_ids)
        )
        if len(allocs) != len(alloc_ids):
            return JsonResponse({'status': 'error', 'messages': ['One or more courses could not be found.']})
        for a in allocs:
            if ExamTimetable.objects.filter(course_allocation=a).exists():
                return JsonResponse(
                    {'status': 'error', 'messages': [f'{a.course_code} is already scheduled.']}
                )

        created = []

        if mode in ('alone', 'all_here', 'all_elsewhere'):
            if not venue_code:
                return JsonResponse({'status': 'error', 'messages': ['venue is required.']})
            venue_obj, _c = Venue.objects.get_or_create(code=venue_code, defaults={'capacity': None})

            blocking = []
            for a in allocs:
                msgs, blocked = _check_conflicts_excluding(a, venue_code, date_str, slot_start, slot_end, [])
                if blocked:
                    blocking.extend(m for m in msgs if '\u274c' in m)
            if blocking and not force:
                return JsonResponse({'status': 'error', 'messages': blocking})

            with transaction.atomic():
                for a in allocs:
                    created.append(_create_direct_entry(a, venue_obj, exam_date_obj, slot_start, slot_end))

        elif mode == 'all_here_split':
            try:
                venue_codes = json.loads(venues_raw or '[]')
            except (ValueError, json.JSONDecodeError):
                return JsonResponse({'status': 'error', 'messages': ['Invalid venues list.']})
            if not venue_codes:
                return JsonResponse({'status': 'error', 'messages': ['No venues supplied.']})
            venue_objs = list(Venue.objects.filter(code__in=venue_codes))
            if not venue_objs:
                return JsonResponse({'status': 'error', 'messages': ['Venues not found.']})

            cap_of = {v.code: (v.exam_capacity if v.exam_capacity is not None else v.capacity) or 0 for v in venue_objs}
            order = sorted(venue_objs, key=lambda v: -cap_of[v.code])
            idx = 0
            remaining = cap_of[order[0].code]

            with transaction.atomic():
                for a in allocs:
                    need = a.number_of_students or 0
                    while idx < len(order) - 1 and remaining < need:
                        idx += 1
                        remaining = cap_of[order[idx].code]
                    v = order[idx]
                    msgs, blocked = _check_conflicts_excluding(a, v.code, date_str, slot_start, slot_end, [])
                    if blocked and not force:
                        transaction.set_rollback(True)
                        return JsonResponse({'status': 'error', 'messages': [m for m in msgs if '\u274c' in m]})
                    created.append(_create_direct_entry(a, v, exam_date_obj, slot_start, slot_end))
                    remaining -= need
        elif mode == 'plan':
            try:
                plan = json.loads(request.POST.get('plan') or '{}')
            except (ValueError, json.JSONDecodeError):
                return JsonResponse({'status': 'error', 'messages': ['Invalid plan.']})
            items = plan.get('assignments') or []
            by_id = {a.id: a for a in allocs}
            try:
                plan_ids = {int(i['allocation_id']) for i in items}
            except (KeyError, TypeError, ValueError):
                return JsonResponse({'status': 'error', 'messages': ['Invalid plan assignments.']})
            if plan_ids != set(by_id):
                return JsonResponse({'status': 'error', 'messages': ['Plan does not cover exactly the selected sections.']})

            all_codes = {c for i in items for c in (i.get('venues') or [])}
            venue_map = {v.code: v for v in Venue.objects.filter(code__in=all_codes)}
            missing = sorted(all_codes - set(venue_map))
            if missing or any(not i.get('venues') for i in items):
                return JsonResponse({'status': 'error', 'messages': [
                    f'Venue(s) not found: {", ".join(missing)}.' if missing else 'Every section needs at least one venue.'
                ]})

            def _cap(v):
                return (v.exam_capacity if v.exam_capacity is not None else v.capacity) or 0

            # Authoritative re-validation (things may have changed since the
            # options were computed).
            problems = []
            room_load = defaultdict(int)
            for item in items:
                a = by_id[int(item['allocation_id'])]
                codes = item['venues']
                norm = normalize_course_code(a.course_code)
                msgs, blocked = _check_conflicts_excluding(a, codes[0], date_str, slot_start, slot_end, [])
                if blocked:
                    problems.extend(m for m in msgs if '\u274c' in m)
                for extra in codes[1:]:
                    other = _venue_taken_by_other(extra, exam_date_obj, slot_start, slot_end, norm)
                    if other:
                        problems.append(f'\u274c Venue conflict: {extra} already booked for {other} at this time.')
                need = a.number_of_students or 0
                if len(codes) == 1:
                    room_load[codes[0]] += need
                elif sum(_cap(venue_map[c]) for c in codes) < need:
                    problems.append(
                        f'\u274c {a.course_code}: {", ".join(codes)} seat '
                        f'{sum(_cap(venue_map[c]) for c in codes)}, but {need} students need seats.'
                    )
            for code, load in room_load.items():
                if _cap(venue_map[code]) and load > _cap(venue_map[code]):
                    problems.append(f'\u274c {code} holds {_cap(venue_map[code])} but this plan puts {load} students in it.')
            if problems:
                return JsonResponse({
                    'status': 'error',
                    'messages': list(dict.fromkeys(problems)) + ['Refresh the options and pick again.'],
                })

            split_n = 0
            with transaction.atomic():
                for item in items:
                    a = by_id[int(item['allocation_id'])]
                    vobjs = [venue_map[c] for c in item['venues']]
                    primary = vobjs[0]
                    entry = _create_direct_entry(a, primary, exam_date_obj, slot_start, slot_end)
                    created.append(entry)
                    if len(vobjs) > 1:
                        grp = SharedVenueExamGroup.objects.create(
                            venue=primary,
                            date=exam_date_obj,
                            day=exam_date_obj.strftime('%A'),
                            start_time=slot_start,
                            end_time=slot_end,
                            total_students=a.number_of_students or 0,
                            published=True,
                            exam_timetable_entry=entry,
                        )
                        grp.course_allocations.add(a)
                        if hasattr(grp, 'notes'):
                            grp.notes = f"Split: primary={primary.code}, extra={', '.join(v.code for v in vobjs[1:])}"
                            grp.save(update_fields=['notes'])
                        split_n += 1

            rooms_used = sorted({c for i in items for c in i['venues']})
            alloc_by_id = {a.id: a for a in allocs}
            return JsonResponse({
                'status': 'success',
                'messages': [
                    f'\u2705 Placed {len(created)} section(s) on {date_str} at {slot_str} in '
                    f'{", ".join(rooms_used)}' + (f' ({split_n} split across rooms).' if split_n else '.')
                ],
                'new_entries': [_entry_payload(e, alloc_by_id[e.course_allocation_id]) for e in created],
                'shared_groups_changed': bool(split_n),
            })

        else:
            return JsonResponse({'status': 'error', 'messages': [f'Unknown mode "{mode}".']})

        alloc_by_id = {a.id: a for a in allocs}
        entries = [_entry_payload(e, alloc_by_id[e.course_allocation_id]) for e in created]
        return JsonResponse({
            'status': 'success',
            'messages': [f'\u2705 Placed {len(created)} course(s) on {date_str} at {slot_str}.'],
            'new_entries': entries,
        })
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'messages': [str(exc)]})
