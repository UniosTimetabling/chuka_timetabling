"""
timetable/simulate_move.py
===========================
"Simulate Move" + "Bulk Move" feature for the Timetable Panel.

Lets an admin right-click a scheduled course cell and:
  1. Simulate moving it to a different day / timeslot / venue and see
     whether that would collide with anything — before committing.
  2. If it collides, get ranked slot recommendations that are free.
  3. Move a course for a particular LECTURER or a particular
     PROGRAM + YEAR of students in bulk, with per-session recommendations
     and a batch-apply step.

Design notes
------------
- Conflict-checking logic is NOT duplicated from scratch — it re-uses the
  exact same exemption rules (`is_scheduling_exempt`, `_are_in_same_combined_group`,
  `_get_year_value`, `time_overlaps`) already defined in `timetable_panel.py`,
  so this module can never drift out of sync with the manual "Save Entry"
  conflict checker. `_check_conflicts_excluding()` below is the authoritative
  check — it is simply `_check_conflicts()` with the ability to exclude a set
  of Timetable rows (the entry being moved) so a course never collides with
  its own current slot while being simulated/moved.
- `_find_recommendations()` is a fast APPROXIMATE scan (bulk-prefetches busy
  intervals once, then does in-memory interval checks) used only to rank
  candidate slots. Whatever the user actually picks is re-validated with the
  authoritative `_check_conflicts_excluding()` before anything is saved.
- `_get_move_bundle()` re-derives the full set of Timetable rows that make up
  one printed cell from ANY single row id, so it always moves as one unit:
    * CombinedCourseGroup members (deliberately DIFFERENT courses sharing one
      venue/slot) bundle together by shared group membership, regardless of
      course_code/lecturer differing.
    * Otherwise, falls back to same course_code + lecturer + day + venue +
      time, which is what auto-merged duplicate rows share.
"""

from datetime import datetime
from collections import defaultdict
import json

from django.db import transaction
from django.http import JsonResponse
from core.rbac import allowed_roles, Role

from timetable.models import Timetable, SchedulerConfig
from room_management.models import Venue
from lecturer_portal.models import Lecturer
from course_allocation.models import CourseAllocation, CombinedCourseGroup

from timetable.timetable_panel import (
    _get_year_value,
    is_scheduling_exempt,
    _is_elective_alloc,
    _intake_of,
    _semester_of,
    _is_same_base_course_pair,
    _are_in_same_combined_group,
    _other_belongs_to_a_different_combined_group,
    _get_combined_group_ids_for_alloc,
    _reset_combined_group_cache,
    time_overlaps,
    course_base_key,
    lecturer_blocked_slot_hit,
    lecturer_preference_mismatch,
)

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


# ═══════════════════════════════════════════════════════════════════
# Slot catalog — every valid (day, start, end, session) from live config
# ═══════════════════════════════════════════════════════════════════
def _get_slot_catalog():
    """Build every valid (day, start, end, session) slot from SchedulerConfig
    (regular / evening / weekend), so recommendations always match the live
    scheduling configuration rather than being hard-coded."""
    from timetable.algorithms.regular_timetable_autosheduler_algorithm import generate_slots

    config = SchedulerConfig.objects.first()
    if not config:
        config = SchedulerConfig.objects.create()

    catalog = []

    # ── Regular (daytime) ──────────────────────────────────────────
    for day in WEEKDAYS:
        for start_t, end_t in generate_slots(config.start_time, config.end_time, config.slot_size):
            catalog.append({
                'day': day,
                'start': start_t.strftime('%H:%M'),
                'end': end_t.strftime('%H:%M'),
                'session': 'regular',
            })

    # ── Evening ─────────────────────────────────────────────────────
    if config.enable_evening_classes:
        for day in WEEKDAYS:
            for start_t, end_t in config.get_evening_slots():
                catalog.append({
                    'day': day,
                    'start': start_t.strftime('%H:%M'),
                    'end': end_t.strftime('%H:%M'),
                    'session': 'evening',
                })

    # ── Weekend ─────────────────────────────────────────────────────
    if config.enable_weekend_classes:
        for day in config.get_weekend_days():
            for start_t, end_t in config.get_weekend_slots():
                catalog.append({
                    'day': day,
                    'start': start_t.strftime('%H:%M'),
                    'end': end_t.strftime('%H:%M'),
                    'session': 'weekend',
                })

    return catalog


# ═══════════════════════════════════════════════════════════════════
# Move-bundle resolution
# ═══════════════════════════════════════════════════════════════════
def _get_move_bundle(tt_id):
    """
    Given ANY single Timetable row id, return (representative_row, [all rows
    that print as the same cell]).

    Two independent things get bundled together, checked in this order:

    1. CombinedCourseGroup membership. A combined group is DIFFERENT courses
       (different course_code, often different lecturers) that were
       deliberately scheduled to share one venue/slot as a single class
       (e.g. EDFO 211-AG + EDFO 211-M). If the row being moved belongs to
       such a group, every other row in the same cell that shares that
       group is pulled in — course_code/lecturer match is irrelevant here,
       group membership is the only thing that matters. Without this, moving
       one member left the rest behind in the old venue/slot, silently
       breaking the combined group.
    2. Auto-merged duplicates: same course_code + lecturer at the same
       day/time/venue (rows created by the rescue/backfill logic), which
       still need to travel together even though they aren't a combined
       group.
    """
    try:
        tt = Timetable.objects.select_related(
            'course_allocation', 'course_allocation__lecturer', 'venue'
        ).get(pk=tt_id)
    except Timetable.DoesNotExist:
        return None, []

    alloc = tt.course_allocation
    if not alloc:
        return tt, [tt]

    same_cell = list(Timetable.objects.filter(
        day=tt.day,
        start_time=tt.start_time,
        end_time=tt.end_time,
        venue_id=tt.venue_id,
    ).select_related('course_allocation', 'course_allocation__lecturer'))

    # ── 1. CombinedCourseGroup bundling ────────────────────────────────
    combined_group_ids = _get_combined_group_ids_for_alloc(alloc.id)
    if combined_group_ids:
        bundle = [
            row for row in same_cell
            if row.course_allocation_id and (
                row.id == tt.id or
                combined_group_ids & _get_combined_group_ids_for_alloc(row.course_allocation_id)
            )
        ]
        if bundle:
            return tt, bundle

    # ── 2. Auto-merged duplicate bundling (course_code + lecturer) ─────
    bundle = [
        row for row in same_cell
        if row.course_allocation and
           (row.course_allocation.course_code or '').lower() == (alloc.course_code or '').lower() and
           row.course_allocation.lecturer_id == alloc.lecturer_id
    ]
    if not bundle:
        bundle = [tt]
    return tt, bundle


def _find_bundle_at_slot(day, start_t, end_t, venue_input, exclude_ids):
    """
    Look for a Timetable row occupying the exact (day, start, end, venue)
    slot, excluding any row already listed in `exclude_ids` (the bundle
    being swapped away). If one is found, resolve it to its full move
    bundle via `_get_move_bundle` (so a combined group / auto-merged set
    swaps as one unit, exactly like a regular move).

    Returns (representative_row, bundle_list) or (None, []) if the slot
    is empty.
    """
    row = Timetable.objects.filter(
        day__iexact=day, start_time=start_t, end_time=end_t,
        venue__code__iexact=venue_input,
    ).exclude(id__in=list(exclude_ids or [])).select_related(
        'course_allocation', 'course_allocation__lecturer', 'venue'
    ).first()
    if not row:
        return None, []
    return _get_move_bundle(row.id)


# ═══════════════════════════════════════════════════════════════════
# Authoritative conflict check (mirrors _check_conflicts, exclusion-aware)
# ═══════════════════════════════════════════════════════════════════
def _check_conflicts_excluding(allocation, venue_input, day, start_t, end_t, exclude_tt_ids, for_update=False):
    """
    Exactly mirrors `timetable_panel._check_conflicts` (program-year,
    lecturer, venue rules + elective/intake/selection-group + Combined
    Course Group exemptions, plus the same-course/same-venue exemptions)
    but excludes `exclude_tt_ids` (the Timetable rows that make up the
    entry currently being moved) so a course is never flagged as
    colliding with its own current slot.

    `for_update`: when True, this MUST be called from inside an active
    `transaction.atomic()` block. It row-locks (SELECT ... FOR UPDATE)
    every existing Timetable row this check depends on — the lecturer's
    other rows that day, the program-year's other rows that day, and the
    venue's other rows that day — before deciding whether there's a
    conflict. Any other request trying to touch one of those same rows
    (another move, another swap, a concurrent double-click) has to wait
    for this transaction to finish, so by the time the caller writes the
    new slot, nothing has changed underneath the check that just ran.
    Without this, there is a window between "check says OK" and "write
    happens" where a concurrent change can slip in, and the move/swap
    lands even though it's no longer actually conflict-free — which is
    exactly the "sometimes accepts a real collision" bug this closes.

    The lock queries filter Timetable directly by row IDs / plain FK-id
    columns (never a joined lookup like `course_allocation__lecturer=`),
    so the FOR UPDATE lock only ever touches Timetable rows themselves —
    never CourseAllocation, Lecturer, or Venue rows — keeping lock scope
    minimal and avoiding unrelated contention/deadlock risk with other
    parts of the app that edit those tables.
    """
    messages_list = []
    error_block_save = False
    exclude_tt_ids = list(exclude_tt_ids or [])

    venue_obj = Venue.objects.filter(code__iexact=venue_input).first()
    alloc_year = _get_year_value(allocation)

    def _rows_for(filter_kwargs, select_related_fields, only_fields):
        base = Timetable.objects.filter(**filter_kwargs).exclude(id__in=exclude_tt_ids)
        if not for_update:
            return base.select_related(*select_related_fields).only(*only_fields)
        locked_ids = list(base.select_for_update().values_list('id', flat=True))
        if not locked_ids:
            return Timetable.objects.none()
        return Timetable.objects.filter(id__in=locked_ids).select_related(*select_related_fields).only(*only_fields)

    # ── PROGRAM CONFLICTS ─────────────────────────────────────────
    if allocation.program and alloc_year is not None:
        program_alloc_ids = list(
            CourseAllocation.objects.filter(program=allocation.program).values_list('id', flat=True)
        )
        program_overlaps = _rows_for(
            {'course_allocation_id__in': program_alloc_ids, 'day__iexact': day},
            ['course_allocation', 'course_allocation__lecturer',
             'course_allocation__selection_group', 'course_allocation__program_course', 'venue'],
            ['start_time', 'end_time',
             'course_allocation__lecturer_id', 'course_allocation__course_code',
             'course_allocation__is_elective', 'course_allocation__intake',
             'course_allocation__selection_group_id', 'course_allocation__program_course__semester', 'venue__code'],
        )

        for existing in program_overlaps:
            existing_year = _get_year_value(existing.course_allocation)
            if existing_year is None or alloc_year != existing_year:
                continue

            if is_scheduling_exempt(allocation, existing.course_allocation):
                if time_overlaps(start_t, end_t, existing.start_time, existing.end_time):
                    if _is_same_base_course_pair(allocation, existing.course_allocation):
                        reason = "the same course split into sections/streams (different students, one course)"
                    elif _semester_of(allocation) is not None and _semester_of(existing.course_allocation) is not None and _semester_of(allocation) != _semester_of(existing.course_allocation) and _intake_of(allocation) != _intake_of(existing.course_allocation):
                        reason = "a shifted-semester special intake overlapping a different semester's normal intake"
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
                messages_list.append(
                    f"❌ Conflict: Program '{allocation.program}' Year '{alloc_year}' "
                    f"already has {existing.course_allocation.course_code} at this time "
                    f"in {venue_name} (different lecturer)."
                )
                error_block_save = True

    # ── LECTURER CONFLICTS ────────────────────────────────────────
    if allocation.lecturer:
        lecturer_alloc_ids = list(
            CourseAllocation.objects.filter(lecturer=allocation.lecturer).values_list('id', flat=True)
        )
        lecturer_overlaps = _rows_for(
            {'course_allocation_id__in': lecturer_alloc_ids, 'day__iexact': day},
            ['venue'],
            ['start_time', 'end_time', 'course_allocation__course_code', 'venue__code', 'course_allocation_id'],
        )

        for existing in lecturer_overlaps:
            if time_overlaps(start_t, end_t, existing.start_time, existing.end_time):
                if _are_in_same_combined_group(allocation.id, existing.course_allocation_id):
                    messages_list.append(
                        f"ℹ️ Lecturer overlap allowed: {allocation.course_code} and "
                        f"{existing.course_allocation.course_code} are in the same "
                        f"Combined Course Group (taught together)."
                    )
                    continue
                # Same course, same venue = one physical class, not a
                # lecturer double-booking. Mirrors timetable_panel._check_conflicts.
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
                # Never let this exemption fire if `existing` belongs to a
                # DIFFERENT CombinedCourseGroup than `allocation` — otherwise
                # two unrelated combined groups sharing a base course code
                # (e.g. "BOTA 111-A" vs "BOTA 111-D") get merged into one
                # class by a drag-move. Mirrors timetable_panel._check_single_placement.
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
                messages_list.append(
                    f"❌ Conflict: Lecturer already teaching "
                    f"{existing.course_allocation.course_code} at this time in {venue_name}."
                )
                error_block_save = True

    # ── VENUE CONFLICTS ────────────────────────────────────────────
    venue_ids = list(Venue.objects.filter(code__iexact=venue_input).values_list('id', flat=True))
    if venue_ids:
        venue_overlaps = _rows_for(
            {'venue_id__in': venue_ids, 'day__iexact': day},
            ['course_allocation', 'venue'],
            ['start_time', 'end_time', 'course_allocation__course_code', 'venue__code', 'course_allocation_id'],
        )
    else:
        venue_overlaps = Timetable.objects.none()

    for existing in venue_overlaps:
        if time_overlaps(start_t, end_t, existing.start_time, existing.end_time):
            if _are_in_same_combined_group(allocation.id, existing.course_allocation_id):
                messages_list.append(
                    f"ℹ️ Venue overlap allowed: {allocation.course_code} and "
                    f"{existing.course_allocation.course_code} share venue {venue_input} "
                    f"as part of a Combined Course Group."
                )
                continue
            # Same base course sharing a room = not a room double-booking,
            # even across different program-years. Mirrors
            # timetable_panel._check_conflicts.
            existing_cc = course_base_key(
                getattr(existing.course_allocation, "course_code", None)
            )
            alloc_cc = course_base_key(allocation.course_code)
            # Same guard as the lecturer check above.
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
            messages_list.append(
                f"❌ Venue conflict: {venue_input} already booked for "
                f"{existing.course_allocation.course_code} at this time."
            )
            error_block_save = True

    # ── LECTURER BLOCKED-DAY / PREFERENCE CHECK ─────────────────────
    # Simulate Move / Swap / Combine / Copy / the collision auto-resolver
    # all funnel through this function, so wiring it in here once means a
    # lecturer's blocked days are respected everywhere a move can land,
    # not just on the manual "Save Entry" form. Blocked = hard (blocks the
    # move); preference mismatch = soft warning only, shown to the user
    # but never blocking, and it's what drives the yellow "not preferred"
    # flag on the frontend once the move is committed.
    if allocation.lecturer:
        blocked = lecturer_blocked_slot_hit(allocation.lecturer, day, start_t, end_t)
        if blocked:
            span = "the whole day" if (not blocked.start_time and not blocked.end_time) else \
                f"{blocked.start_time.strftime('%H:%M')}–{blocked.end_time.strftime('%H:%M')}"
            reason = f" ({blocked.reason})" if blocked.reason else ""
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

    # ── VENUE CAPACITY (warning only, never blocks) ────────────────
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
            f"⚠️ Venue '{venue_input}' not found in database. It will be created."
        )

    return messages_list, error_block_save


# ═══════════════════════════════════════════════════════════════════
# Recommendation engine — fast approximate scan, ranked candidates
# ═══════════════════════════════════════════════════════════════════
def _find_recommendations(allocation, venues, exclude_tt_ids, prefer_day=None, prefer_venue=None, limit=8):
    """
    Scan the full slot catalog × all venues and return conflict-free
    candidates, ranked by how well they match the preferred day/venue.
    Uses 3 bulk queries (lecturer / program-year / venue busy intervals)
    instead of re-running the full conflict checker per candidate, since
    the catalog × venues space can be large. Whatever is picked from here
    is still re-validated with `_check_conflicts_excluding` before saving.
    """
    catalog = _get_slot_catalog()
    exclude_tt_ids = list(exclude_tt_ids or [])
    alloc_year = _get_year_value(allocation)

    # Lecturer busy intervals: {day: [(start,end)]}
    lecturer_busy = defaultdict(list)
    if allocation.lecturer_id:
        for t in Timetable.objects.filter(
            course_allocation__lecturer_id=allocation.lecturer_id
        ).exclude(id__in=exclude_tt_ids).only('day', 'start_time', 'end_time'):
            lecturer_busy[t.day].append((t.start_time, t.end_time))

    # Program-year busy intervals (exemption-aware): {day: [(start,end)]}
    program_busy = defaultdict(list)
    if allocation.program_id and alloc_year is not None:
        qs = Timetable.objects.filter(
            course_allocation__program_id=allocation.program_id
        ).exclude(id__in=exclude_tt_ids).select_related(
            'course_allocation', 'course_allocation__selection_group', 'course_allocation__program_course'
        )
        for t in qs:
            existing_year = _get_year_value(t.course_allocation)
            if existing_year is None or existing_year != alloc_year:
                continue
            if is_scheduling_exempt(allocation, t.course_allocation):
                continue
            program_busy[t.day].append((t.start_time, t.end_time))

    # Venue busy intervals: {venue_code: {day: [(start,end)]}}
    venue_busy = defaultdict(lambda: defaultdict(list))
    for t in Timetable.objects.exclude(id__in=exclude_tt_ids).select_related('venue').only(
        'day', 'start_time', 'end_time', 'venue__code'
    ):
        if t.venue:
            venue_busy[t.venue.code][t.day].append((t.start_time, t.end_time))

    students = allocation.number_of_students or 0
    cap_of = {v.code: (v.capacity or 0) for v in venues}
    prefer_venue_l = prefer_venue.lower() if prefer_venue else None

    candidates = []
    for slot in catalog:
        day, start_s, end_s = slot['day'], slot['start'], slot['end']
        start_t = datetime.strptime(start_s, "%H:%M").time()
        end_t = datetime.strptime(end_s, "%H:%M").time()

        if any(time_overlaps(start_t, end_t, s, e) for (s, e) in lecturer_busy.get(day, [])):
            continue
        if any(time_overlaps(start_t, end_t, s, e) for (s, e) in program_busy.get(day, [])):
            continue

        for v in venues:
            vcode = v.code
            if any(time_overlaps(start_t, end_t, s, e) for (s, e) in venue_busy.get(vcode, {}).get(day, [])):
                continue

            cap = cap_of.get(vcode, 0)
            over_cap = bool(cap and students > cap)
            score = 0
            if prefer_day and day == prefer_day:
                score -= 100
            if prefer_venue_l and vcode.lower() == prefer_venue_l:
                score -= 50
            if over_cap:
                score += 20

            candidates.append({
                'day': day, 'start': start_s, 'end': end_s,
                'venue': vcode, 'capacity': cap, 'over_capacity': over_cap,
                'session': slot['session'],
                'score': score,
            })

    candidates.sort(key=lambda c: (c['score'], c['day'], c['start'], c['venue']))
    return candidates[:limit]


# ═══════════════════════════════════════════════════════════════════
# API — slot catalog (feeds day/slot/venue pickers)
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def slot_catalog_api(request):
    config = SchedulerConfig.objects.first()
    weekend_days = config.get_weekend_days() if (config and config.enable_weekend_classes) else []
    return JsonResponse({
        'status': 'success',
        'slots': _get_slot_catalog(),
        'venues': list(Venue.objects.all().order_by('code').values('code', 'capacity')),
        'weekdays': WEEKDAYS,
        'weekend_days': weekend_days,
        'evening_enabled': bool(config and config.enable_evening_classes),
        'weekend_enabled': bool(config and config.enable_weekend_classes),
    })


def _detach_allocation_from_combined_groups(allocation):
    """Remove `allocation` from every CombinedCourseGroup it belongs to.

    Mirrors CombinedCourseGroupService.remove_from_combined_group's
    semantics (course_management/cod_panel.py) so a course detached via
    Simulate Move behaves identically to one removed via the COD panel:
    a group left with fewer than 2 members no longer makes sense as a
    "combined" group and is deleted outright; otherwise, if the removed
    allocation was the group's primary_allocation, a new one is chosen
    from whatever members remain.

    Used when an individual member of a combined group is moved on its
    own via Simulate Move's `detach` option — once it's scheduled
    independently at its own venue/time, it must stop being treated as
    part of the group (both for display, and so future conflict checks
    no longer exempt it against its ex-groupmates).

    Returns the list of group labels (display_name()) the allocation was
    removed from, for the response message — normally at most one, since
    an allocation isn't expected to belong to more than one combined
    group at a time, but this handles that defensively.
    """
    removed_from = []
    for group in list(allocation.combined_groups.all()):
        removed_from.append(group.display_name())
        group.allocations.remove(allocation)
        if group.allocations.count() < 2:
            group.delete()
        elif group.primary_allocation_id == allocation.id:
            group.primary_allocation = group.allocations.first()
            group.save(update_fields=["primary_allocation"])
    return removed_from


# ═══════════════════════════════════════════════════════════════════
# API — check-before-you-move for a single cell
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def simulate_move_api(request):
    _reset_combined_group_cache()  # fresh, correct data for this request
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'messages': ['POST required.']}, status=405)

    tt_id = request.POST.get('tt_id')
    day = request.POST.get('day')
    venue_input = (request.POST.get('venue') or '').strip()
    start_s = request.POST.get('start')
    end_s = request.POST.get('end')
    detach = (request.POST.get('detach') or '').lower() == 'true'

    if not (tt_id and day and venue_input and start_s and end_s):
        return JsonResponse({'status': 'error', 'messages': ['All fields are required.']}, status=400)

    try:
        start_t = datetime.strptime(start_s, "%H:%M").time()
        end_t = datetime.strptime(end_s, "%H:%M").time()
    except ValueError:
        return JsonResponse({'status': 'error', 'messages': ['Invalid time format.']}, status=400)

    tt, bundle = _get_move_bundle(tt_id)
    if not tt or not tt.course_allocation:
        return JsonResponse({'status': 'error', 'messages': ['Timetable entry not found.']}, status=404)

    allocation = tt.course_allocation

    # A combined-group bundle is one where _get_move_bundle pulled in other
    # rows specifically because of shared CombinedCourseGroup membership
    # (as opposed to same-course/lecturer auto-merge duplicates, which
    # `detach` doesn't apply to — there's no group to detach from).
    is_combined_group_bundle = bool(_get_combined_group_ids_for_alloc(allocation.id)) and len(bundle) > 1

    # `detach`: move ONLY this row, not the rest of the bundle. Only
    # meaningful when this row is actually part of a combined group — if
    # it's just an auto-merged-duplicate bundle there's nothing to detach
    # from, so `detach` has no effect there (the checkbox is hidden for
    # that case on the frontend, but this stays permissive rather than
    # erroring if it's ever sent anyway).
    effective_bundle = [tt] if (detach and is_combined_group_bundle) else bundle
    exclude_ids = [b.id for b in effective_bundle]

    same_slot = (
        tt.day == day and tt.start_time == start_t and tt.end_time == end_t and
        tt.venue and tt.venue.code.lower() == venue_input.lower()
    )

    messages_list, error_block = _check_conflicts_excluding(
        allocation, venue_input, day, start_t, end_t, exclude_ids
    )

    recommendations = []
    if error_block:
        venues = list(Venue.objects.all())
        recommendations = _find_recommendations(
            allocation, venues, exclude_ids, prefer_day=day, prefer_venue=venue_input, limit=8
        )

    return JsonResponse({
        'status': 'conflict' if error_block else ('same_slot' if same_slot else 'ok'),
        'messages': messages_list,
        'course_code': allocation.course_code,
        'lecturer': getattr(allocation.lecturer, 'name', 'Unassigned'),
        'bundle_size': len(effective_bundle),
        'is_combined_group': is_combined_group_bundle,
        'combined_group_size': len(bundle) if is_combined_group_bundle else 1,
        'recommendations': recommendations,
    })


# ═══════════════════════════════════════════════════════════════════
# API — commit the move (supports force=true override)
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def execute_move_api(request):
    _reset_combined_group_cache()  # fresh, correct data for this request
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'messages': ['POST required.']}, status=405)

    tt_id = request.POST.get('tt_id')
    day = request.POST.get('day')
    venue_input = (request.POST.get('venue') or '').strip()
    start_s = request.POST.get('start')
    end_s = request.POST.get('end')
    force = (request.POST.get('force') or '').lower() == 'true'
    detach = (request.POST.get('detach') or '').lower() == 'true'

    if not (tt_id and day and venue_input and start_s and end_s):
        return JsonResponse({'status': 'error', 'messages': ['All fields are required.']}, status=400)

    try:
        start_t = datetime.strptime(start_s, "%H:%M").time()
        end_t = datetime.strptime(end_s, "%H:%M").time()
    except ValueError:
        return JsonResponse({'status': 'error', 'messages': ['Invalid time format.']}, status=400)

    detached_from = []
    with transaction.atomic():
        # Re-resolve the bundle and re-run the conflict check INSIDE the
        # same atomic block that performs the write, with row-locking on —
        # closes the check-then-write race where a concurrent move/swap
        # could slip a conflicting row in between the check and the save.
        tt, bundle = _get_move_bundle(tt_id)
        if not tt or not tt.course_allocation:
            return JsonResponse({'status': 'error', 'messages': ['Timetable entry not found.']}, status=404)

        allocation = tt.course_allocation
        is_combined_group_bundle = bool(_get_combined_group_ids_for_alloc(allocation.id)) and len(bundle) > 1
        do_detach = detach and is_combined_group_bundle

        effective_bundle = [tt] if do_detach else bundle
        exclude_ids = [b.id for b in effective_bundle]

        # Lock the row(s) actually being moved too, so a concurrent
        # double-click / second request touching this exact entry serializes
        # behind this one instead of racing it.
        list(Timetable.objects.filter(id__in=exclude_ids).select_for_update().values_list('id', flat=True))

        messages_list, error_block = _check_conflicts_excluding(
            allocation, venue_input, day, start_t, end_t, exclude_ids, for_update=True
        )
        if error_block and not force:
            return JsonResponse({'status': 'error', 'messages': messages_list}, status=409)

        venue_obj, _created = Venue.objects.get_or_create(code=venue_input, defaults={'capacity': None})

        # Move ONLY the effective bundle's row(s) — when detaching, that's
        # just this one Timetable row, so every other member of the
        # combined group is left exactly where it already was.
        Timetable.objects.filter(id__in=exclude_ids).update(
            day=day, start_time=start_t, end_time=end_t, venue=venue_obj
        )
        if do_detach:
            detached_from = _detach_allocation_from_combined_groups(allocation)

    n = len(effective_bundle)
    if do_detach:
        group_label = ", ".join(detached_from) if detached_from else "its combined group"
        left_behind = len(bundle) - 1
        success_message = (
            f"Detached {allocation.course_code} from {group_label} and moved it alone "
            f"to {day} {start_s}-{end_s} @ {venue_obj.code}. The other "
            f"{left_behind} course{'s' if left_behind != 1 else ''} in the group "
            f"{'were' if left_behind != 1 else 'was'} left untouched."
        )
    else:
        success_message = (
            f"Moved {allocation.course_code} ({n} entr{'y' if n == 1 else 'ies'}) "
            f"to {day} {start_s}-{end_s} @ {venue_obj.code}."
        )

    return JsonResponse({
        'status': 'success',
        'messages': (messages_list or []) + [success_message],
        'moved_count': n,
        'detached': do_detach,
    })


# ═══════════════════════════════════════════════════════════════════
# SWAP — exchange two courses' day/timeslot/venue with each other.
#
# Course A (right-clicked) ends up exactly where course B was, and course
# B ends up exactly where course A was — including venue, not just the
# day/timeslot. Both bundles (combined groups / auto-merged duplicates)
# travel together, same as a regular move. Each direction of the swap is
# re-validated with the exact same authoritative `_check_conflicts_excluding`
# used by Simulate Move, excluding BOTH bundles so the two courses never
# collide with each other (they're trading places on purpose) — only
# collisions with everything else are reported, and those can be bypassed
# with `force`, same as Simulate Move.
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def simulate_swap_api(request):
    _reset_combined_group_cache()  # fresh, correct data for this request
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'messages': ['POST required.']}, status=405)

    tt_id = request.POST.get('tt_id')
    day = request.POST.get('day')
    venue_input = (request.POST.get('venue') or '').strip()
    start_s = request.POST.get('start')
    end_s = request.POST.get('end')

    if not (tt_id and day and venue_input and start_s and end_s):
        return JsonResponse({'status': 'error', 'messages': ['All fields are required.']}, status=400)

    try:
        start_t = datetime.strptime(start_s, "%H:%M").time()
        end_t = datetime.strptime(end_s, "%H:%M").time()
    except ValueError:
        return JsonResponse({'status': 'error', 'messages': ['Invalid time format.']}, status=400)

    tt_a, bundle_a = _get_move_bundle(tt_id)
    if not tt_a or not tt_a.course_allocation:
        return JsonResponse({'status': 'error', 'messages': ['Timetable entry not found.']}, status=404)

    ids_a = [b.id for b in bundle_a]
    alloc_a = tt_a.course_allocation

    same_slot = (
        tt_a.day == day and tt_a.start_time == start_t and tt_a.end_time == end_t and
        tt_a.venue and tt_a.venue.code.lower() == venue_input.lower()
    )
    if same_slot:
        return JsonResponse({
            'status': 'same_slot',
            'messages': ["That's this course's own current slot — pick where another course sits to swap with it."],
            'course_code': alloc_a.course_code,
        })

    tt_b, bundle_b = _find_bundle_at_slot(day, start_t, end_t, venue_input, exclude_ids=ids_a)
    if not tt_b or not tt_b.course_allocation:
        return JsonResponse({
            'status': 'empty',
            'messages': [f"No course is currently scheduled at {day} {start_s}–{end_s} @ {venue_input} — "
                         f"there's nothing there to swap with. Use Simulate Move instead to move into an empty slot."],
            'course_code': alloc_a.course_code,
        })

    ids_b = [b.id for b in bundle_b]
    alloc_b = tt_b.course_allocation
    exclude_both = ids_a + ids_b

    # A moving into B's current slot (venue included) …
    messages_a, error_a = _check_conflicts_excluding(
        alloc_a, tt_b.venue.code if tt_b.venue else venue_input, tt_b.day, tt_b.start_time, tt_b.end_time, exclude_both
    )
    # … and B moving into A's current slot (venue included).
    messages_b, error_b = _check_conflicts_excluding(
        alloc_b, tt_a.venue.code if tt_a.venue else '', tt_a.day, tt_a.start_time, tt_a.end_time, exclude_both
    )

    error_block = error_a or error_b
    messages = (
        [f"— {alloc_a.course_code} → {tt_b.day} {tt_b.start_time.strftime('%H:%M')}–{tt_b.end_time.strftime('%H:%M')} @ {tt_b.venue.code if tt_b.venue else '?'} —"]
        + messages_a
        + [f"— {alloc_b.course_code} → {tt_a.day} {tt_a.start_time.strftime('%H:%M')}–{tt_a.end_time.strftime('%H:%M')} @ {tt_a.venue.code if tt_a.venue else '?'} —"]
        + messages_b
    )

    return JsonResponse({
        'status': 'conflict' if error_block else 'ok',
        'messages': messages,
        'course_a': {
            'tt_id': tt_a.id, 'course_code': alloc_a.course_code,
            'lecturer': getattr(alloc_a.lecturer, 'name', 'Unassigned'), 'bundle_size': len(bundle_a),
            'day': tt_a.day, 'start': tt_a.start_time.strftime('%H:%M'), 'end': tt_a.end_time.strftime('%H:%M'),
            'venue': tt_a.venue.code if tt_a.venue else '',
        },
        'course_b': {
            'tt_id': tt_b.id, 'course_code': alloc_b.course_code,
            'lecturer': getattr(alloc_b.lecturer, 'name', 'Unassigned'), 'bundle_size': len(bundle_b),
            'day': tt_b.day, 'start': tt_b.start_time.strftime('%H:%M'), 'end': tt_b.end_time.strftime('%H:%M'),
            'venue': tt_b.venue.code if tt_b.venue else '',
        },
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def execute_swap_api(request):
    _reset_combined_group_cache()  # fresh, correct data for this request
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'messages': ['POST required.']}, status=405)

    tt_id_a = request.POST.get('tt_id_a')
    tt_id_b = request.POST.get('tt_id_b')
    force = (request.POST.get('force') or '').lower() == 'true'

    if not (tt_id_a and tt_id_b):
        return JsonResponse({'status': 'error', 'messages': ['Both courses to swap are required.']}, status=400)

    with transaction.atomic():
        # Re-resolve everything fresh at execute time (not trusting anything
        # cached client-side from the simulate step), and run both direction
        # checks with row-locking on, all inside this one atomic block, so a
        # swap never applies against stale positions and never races a
        # concurrent move/swap touching the same rows.
        tt_a, bundle_a = _get_move_bundle(tt_id_a)
        tt_b, bundle_b = _get_move_bundle(tt_id_b)
        if not tt_a or not tt_a.course_allocation:
            return JsonResponse({'status': 'error', 'messages': ['First course not found (already moved?).']}, status=404)
        if not tt_b or not tt_b.course_allocation:
            return JsonResponse({'status': 'error', 'messages': ['Second course not found (already moved?).']}, status=404)

        ids_a = [b.id for b in bundle_a]
        ids_b = [b.id for b in bundle_b]
        if set(ids_a) & set(ids_b):
            return JsonResponse({'status': 'error', 'messages': ['Those two selections overlap — nothing to swap.']}, status=400)

        alloc_a, alloc_b = tt_a.course_allocation, tt_b.course_allocation
        exclude_both = ids_a + ids_b

        # Lock both bundles' own rows so a concurrent request touching
        # either side of this swap serializes behind it.
        list(Timetable.objects.filter(id__in=exclude_both).select_for_update().values_list('id', flat=True))

        # Snapshot each course's ORIGINAL slot before anything is written —
        # this is exactly what the other course will be moved into.
        day_a, start_a, end_a, venue_a = tt_a.day, tt_a.start_time, tt_a.end_time, tt_a.venue
        day_b, start_b, end_b, venue_b = tt_b.day, tt_b.start_time, tt_b.end_time, tt_b.venue

        messages_a, error_a = _check_conflicts_excluding(
            alloc_a, venue_b.code if venue_b else '', day_b, start_b, end_b, exclude_both, for_update=True
        )
        messages_b, error_b = _check_conflicts_excluding(
            alloc_b, venue_a.code if venue_a else '', day_a, start_a, end_a, exclude_both, for_update=True
        )
        error_block = error_a or error_b

        if error_block and not force:
            messages = (
                [f"— {alloc_a.course_code} → {day_b} {start_b.strftime('%H:%M')}–{end_b.strftime('%H:%M')} @ {venue_b.code if venue_b else '?'} —"]
                + messages_a
                + [f"— {alloc_b.course_code} → {day_a} {start_a.strftime('%H:%M')}–{end_a.strftime('%H:%M')} @ {venue_a.code if venue_a else '?'} —"]
                + messages_b
            )
            return JsonResponse({'status': 'error', 'messages': messages}, status=409)

        if not venue_a or not venue_b:
            return JsonResponse({'status': 'error', 'messages': ['Both entries must have a venue to swap.']}, status=400)

        Timetable.objects.filter(id__in=ids_a).update(day=day_b, start_time=start_b, end_time=end_b, venue=venue_b)
        Timetable.objects.filter(id__in=ids_b).update(day=day_a, start_time=start_a, end_time=end_a, venue=venue_a)

    n_a, n_b = len(bundle_a), len(bundle_b)
    return JsonResponse({
        'status': 'success',
        'messages': (messages_a or []) + (messages_b or []) + [
            f"Swapped {alloc_a.course_code} ({n_a} entr{'y' if n_a == 1 else 'ies'}) with "
            f"{alloc_b.course_code} ({n_b} entr{'y' if n_b == 1 else 'ies'}): "
            f"{alloc_a.course_code} is now {day_b} {start_b.strftime('%H:%M')}–{end_b.strftime('%H:%M')} @ {venue_b.code}, "
            f"{alloc_b.course_code} is now {day_a} {start_a.strftime('%H:%M')}–{end_a.strftime('%H:%M')} @ {venue_a.code}."
        ],
        'swapped_count': n_a + n_b,
    })


# ═══════════════════════════════════════════════════════════════════
# COMBINE WITH — merge two scheduled courses into one CombinedCourseGroup.
#
# Course A (right-clicked) is moved onto course B's exact day/timeslot/venue
# (whichever B currently sits at). A and B (and, if either is already part
# of a combined group, every existing member of that group) are then linked
# as one CombinedCourseGroup, so the panel renders them going forward as a
# single "Combined" cell — exactly like MergedCourseGroupTimetable-free,
# genuinely-different-course combos such as "EDFO 211-AG + EDFO 211-M".
#
# A's own previous slot is freed automatically: its Timetable row(s) are
# UPDATED in place to B's day/start/end/venue rather than duplicated, so
# nothing is left behind at the old slot.
#
# Two DIFFERENT existing combined groups can't be silently merged into one
# here (that's a bigger, deliberate admin action) — that case is reported
# back as a conflict instead of auto-resolved.
# ═══════════════════════════════════════════════════════════════════
def _make_combined_group_code(code_a, code_b):
    """Build a unique, human-readable CombinedCourseGroup.group_code (<=50 chars)."""
    base = f"{code_a} + {code_b}".strip()[:50] or "Combined Group"
    candidate = base
    i = 2
    while CombinedCourseGroup.objects.filter(group_code=candidate).exists():
        suffix = f" ({i})"
        candidate = base[:50 - len(suffix)] + suffix
        i += 1
    return candidate


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def simulate_combine_api(request):
    """Check-before-you-combine: resolve whatever sits at the picked
    day/timeslot/venue and report whether combining A with it is safe."""
    _reset_combined_group_cache()  # fresh, correct data for this request
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'messages': ['POST required.']}, status=405)

    tt_id = request.POST.get('tt_id')
    day = request.POST.get('day')
    venue_input = (request.POST.get('venue') or '').strip()
    start_s = request.POST.get('start')
    end_s = request.POST.get('end')

    if not (tt_id and day and venue_input and start_s and end_s):
        return JsonResponse({'status': 'error', 'messages': ['All fields are required.']}, status=400)

    try:
        start_t = datetime.strptime(start_s, "%H:%M").time()
        end_t = datetime.strptime(end_s, "%H:%M").time()
    except ValueError:
        return JsonResponse({'status': 'error', 'messages': ['Invalid time format.']}, status=400)

    tt_a, bundle_a = _get_move_bundle(tt_id)
    if not tt_a or not tt_a.course_allocation:
        return JsonResponse({'status': 'error', 'messages': ['Timetable entry not found.']}, status=404)

    ids_a = [b.id for b in bundle_a]
    alloc_a = tt_a.course_allocation

    same_slot = (
        tt_a.day == day and tt_a.start_time == start_t and tt_a.end_time == end_t and
        tt_a.venue and tt_a.venue.code.lower() == venue_input.lower()
    )
    if same_slot:
        return JsonResponse({
            'status': 'same_slot',
            'messages': ["That's this course's own current slot — pick where another course sits to combine with it."],
            'course_code': alloc_a.course_code,
        })

    tt_b, bundle_b = _find_bundle_at_slot(day, start_t, end_t, venue_input, exclude_ids=ids_a)
    if not tt_b or not tt_b.course_allocation:
        return JsonResponse({
            'status': 'empty',
            'messages': [f"No course is currently scheduled at {day} {start_s}–{end_s} @ {venue_input} — "
                         f"there's nothing there to combine with. Use Simulate Move instead to move into an empty slot."],
            'course_code': alloc_a.course_code,
        })

    ids_b = [b.id for b in bundle_b]
    alloc_b = tt_b.course_allocation

    if alloc_a.id == alloc_b.id:
        return JsonResponse({
            'status': 'error',
            'messages': ["That's the same course — nothing to combine."],
        }, status=400)

    all_alloc_ids = list(dict.fromkeys(
        [b.course_allocation_id for b in bundle_a if b.course_allocation_id]
        + [b.course_allocation_id for b in bundle_b if b.course_allocation_id]
    ))
    existing_group_ids = set(
        CombinedCourseGroup.objects.filter(allocations__id__in=all_alloc_ids)
        .values_list('id', flat=True).distinct()
    )
    if len(existing_group_ids) > 1:
        return JsonResponse({
            'status': 'error',
            'messages': ["These courses already belong to two different Combined Course Groups — "
                         "merge those groups from the Combined Course Groups admin first."],
        }, status=400)
    if len(existing_group_ids) == 1 and _are_in_same_combined_group(alloc_a.id, alloc_b.id):
        return JsonResponse({
            'status': 'already_combined',
            'messages': [f"{alloc_a.course_code} and {alloc_b.course_code} are already in the same Combined Course Group."],
            'course_code': alloc_a.course_code,
        })

    exclude_both = ids_a + ids_b
    messages_list, error_block = _check_conflicts_excluding(
        alloc_a, tt_b.venue.code if tt_b.venue else venue_input, tt_b.day, tt_b.start_time, tt_b.end_time, exclude_both
    )

    return JsonResponse({
        'status': 'conflict' if error_block else 'ok',
        'messages': messages_list,
        'course_a': {
            'tt_id': tt_a.id, 'course_code': alloc_a.course_code,
            'lecturer': getattr(alloc_a.lecturer, 'name', 'Unassigned'), 'bundle_size': len(bundle_a),
        },
        'course_b': {
            'tt_id': tt_b.id, 'course_code': alloc_b.course_code,
            'lecturer': getattr(alloc_b.lecturer, 'name', 'Unassigned'), 'bundle_size': len(bundle_b),
            'day': tt_b.day, 'start': tt_b.start_time.strftime('%H:%M'), 'end': tt_b.end_time.strftime('%H:%M'),
            'venue': tt_b.venue.code if tt_b.venue else '',
        },
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def execute_combine_api(request):
    """Commit the combine: move A's bundle onto B's exact slot and link
    both (plus any pre-existing group members) as one CombinedCourseGroup."""
    _reset_combined_group_cache()  # fresh, correct data for this request
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'messages': ['POST required.']}, status=405)

    tt_id_a = request.POST.get('tt_id_a')
    tt_id_b = request.POST.get('tt_id_b')
    force = (request.POST.get('force') or '').lower() == 'true'

    if not (tt_id_a and tt_id_b):
        return JsonResponse({'status': 'error', 'messages': ['Both courses to combine are required.']}, status=400)

    # Re-resolve fresh at execute time — never trust anything cached
    # client-side from the check step, same discipline as Swap.
    with transaction.atomic():
        tt_a, bundle_a = _get_move_bundle(tt_id_a)
        tt_b, bundle_b = _get_move_bundle(tt_id_b)
        if not tt_a or not tt_a.course_allocation:
            return JsonResponse({'status': 'error', 'messages': ['First course not found (already moved?).']}, status=404)
        if not tt_b or not tt_b.course_allocation:
            return JsonResponse({'status': 'error', 'messages': ['Second course not found (already moved?).']}, status=404)

        ids_a = [b.id for b in bundle_a]
        ids_b = [b.id for b in bundle_b]
        if set(ids_a) & set(ids_b):
            return JsonResponse({'status': 'error', 'messages': ['Those two selections overlap — nothing to combine.']}, status=400)

        alloc_a, alloc_b = tt_a.course_allocation, tt_b.course_allocation
        if alloc_a.id == alloc_b.id:
            return JsonResponse({'status': 'error', 'messages': ["That's the same course — nothing to combine."]}, status=400)

        exclude_both = ids_a + ids_b

        # Lock both bundles' own rows so a concurrent request touching
        # either side serializes behind this one.
        list(Timetable.objects.filter(id__in=exclude_both).select_for_update().values_list('id', flat=True))

        all_alloc_ids = list(dict.fromkeys(
            [b.course_allocation_id for b in bundle_a if b.course_allocation_id]
            + [b.course_allocation_id for b in bundle_b if b.course_allocation_id]
        ))
        existing_groups = list(
            CombinedCourseGroup.objects.filter(allocations__id__in=all_alloc_ids).distinct()
        )
        if len(existing_groups) > 1:
            return JsonResponse({
                'status': 'error',
                'messages': ["These courses already belong to two different Combined Course Groups — "
                             "merge those groups from the Combined Course Groups admin first."],
            }, status=400)

        # B keeps its slot; A is moved onto it — so this is checked exactly like
        # a move of A into B's current day/timeslot/venue.
        day_b, start_b, end_b, venue_b = tt_b.day, tt_b.start_time, tt_b.end_time, tt_b.venue
        if not venue_b:
            return JsonResponse({'status': 'error', 'messages': ['The target entry has no venue to combine into.']}, status=400)

        messages_list, error_block = _check_conflicts_excluding(
            alloc_a, venue_b.code, day_b, start_b, end_b, exclude_both, for_update=True
        )
        if error_block and not force:
            return JsonResponse({'status': 'error', 'messages': messages_list}, status=409)

        # Move A's whole bundle onto B's exact slot — frees A's old slot
        # automatically since these rows are UPDATED, not duplicated.
        Timetable.objects.filter(id__in=ids_a).update(
            day=day_b, start_time=start_b, end_time=end_b, venue=venue_b
        )

        allocations_qs = list(CourseAllocation.objects.filter(id__in=all_alloc_ids).select_related('department'))
        if len(existing_groups) == 1:
            group = existing_groups[0]
            group.allocations.add(*allocations_qs)
            if not group.primary_allocation_id:
                group.primary_allocation = alloc_b
                group.save(update_fields=['primary_allocation'])
        else:
            same_lecturer = alloc_a.lecturer_id and alloc_a.lecturer_id == alloc_b.lecturer_id
            dept = alloc_b.department or alloc_a.department
            group = CombinedCourseGroup.objects.create(
                group_code=_make_combined_group_code(alloc_a.course_code, alloc_b.course_code),
                base_course_code=alloc_b.course_code,
                lecturer=alloc_a.lecturer if same_lecturer else None,
                department=dept,
                origin_department=dept,
                created_by=request.user if request.user.is_authenticated else None,
            )
            group.allocations.set(allocations_qs)
            group.primary_allocation = alloc_b
            group.save(update_fields=['primary_allocation'])

    n_a = len(bundle_a)
    return JsonResponse({
        'status': 'success',
        'messages': (messages_list or []) + [
            f"Combined {alloc_a.course_code} ({n_a} entr{'y' if n_a == 1 else 'ies'}) with {alloc_b.course_code} "
            f"— now sharing {day_b} {start_b.strftime('%H:%M')}–{end_b.strftime('%H:%M')} @ {venue_b.code} as "
            f"\"{group.display_name()}\". {alloc_a.course_code}'s previous slot has been freed."
        ],
        'combined_count': n_a,
        'group_code': group.display_name(),
    })


# ═══════════════════════════════════════════════════════════════════
# API — bulk move: scope picker options (lecturers / program+years)
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def bulk_move_scope_options_api(request):
    lecturer_ids = Timetable.objects.filter(
        course_allocation__lecturer__isnull=False
    ).values_list('course_allocation__lecturer_id', flat=True).distinct()
    lecturers = list(
        Lecturer.objects.filter(id__in=lecturer_ids).order_by('name').values('id', 'name')
    )

    combos = {}
    qs = Timetable.objects.filter(
        course_allocation__program__isnull=False
    ).select_related('course_allocation__program', 'course_allocation__program_course')
    for t in qs:
        alloc = t.course_allocation
        year = _get_year_value(alloc)
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
def bulk_move_candidates_api(request):
    _reset_combined_group_cache()  # fresh, correct data for this request
    scope_type = request.GET.get('scope_type')
    prefer_day = request.GET.get('prefer_day') or None
    prefer_venue = (request.GET.get('prefer_venue') or '').strip() or None

    if scope_type == 'lecturer':
        lecturer_id = request.GET.get('lecturer_id')
        if not lecturer_id:
            return JsonResponse({'status': 'error', 'messages': ['lecturer_id required.']}, status=400)
        qs = Timetable.objects.filter(course_allocation__lecturer_id=lecturer_id)
        year_filter = None
    elif scope_type == 'program_year':
        program_id = request.GET.get('program_id')
        year_filter = request.GET.get('year')
        if not (program_id and year_filter):
            return JsonResponse({'status': 'error', 'messages': ['program_id and year required.']}, status=400)
        qs = Timetable.objects.filter(course_allocation__program_id=program_id)
    else:
        return JsonResponse({'status': 'error', 'messages': ['Invalid scope_type.']}, status=400)

    qs = qs.select_related(
        'course_allocation', 'course_allocation__lecturer',
        'course_allocation__program', 'venue'
    )

    sessions = []
    bundle_map = {}       # bkey -> session dict
    alloc_by_bkey = {}    # bkey -> CourseAllocation instance (for recommendations)

    for t in qs:
        alloc = t.course_allocation
        if not alloc:
            continue
        if scope_type == 'program_year' and _get_year_value(alloc) != year_filter:
            continue

        bkey = (t.day, t.start_time, t.end_time, t.venue_id,
                (alloc.course_code or '').lower(), alloc.lecturer_id)

        if bkey in bundle_map:
            bundle_map[bkey]['bundle_ids'].append(t.id)
            continue

        entry = {
            'tt_id': t.id,
            'bundle_ids': [t.id],
            'course_code': alloc.course_code,
            'course_name': alloc.course_name,
            'lecturer': getattr(alloc.lecturer, 'name', 'Unassigned'),
            'program': getattr(alloc.program, 'name', 'N/A'),
            'day': t.day,
            'start': t.start_time.strftime('%H:%M'),
            'end': t.end_time.strftime('%H:%M'),
            'venue': t.venue.code if t.venue else 'Unknown',
            'students': alloc.number_of_students or 0,
        }
        bundle_map[bkey] = entry
        alloc_by_bkey[bkey] = alloc
        sessions.append(entry)

    venues = list(Venue.objects.all())
    for bkey, entry in bundle_map.items():
        alloc = alloc_by_bkey[bkey]
        entry['recommendations'] = _find_recommendations(
            alloc, venues, entry['bundle_ids'],
            prefer_day=prefer_day, prefer_venue=prefer_venue, limit=5,
        )

    sessions.sort(key=lambda e: (e['day'], e['start']))
    return JsonResponse({'status': 'success', 'sessions': sessions})


# ═══════════════════════════════════════════════════════════════════
# API — bulk move: apply a batch of moves in one call
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def bulk_move_execute_api(request):
    _reset_combined_group_cache()  # fresh, correct data for this request
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
        tt_id = mv.get('tt_id')
        day = mv.get('day')
        venue_input = (mv.get('venue') or '').strip()
        start_s = mv.get('start')
        end_s = mv.get('end')

        if not (tt_id and day and venue_input and start_s and end_s):
            skipped.append({'tt_id': tt_id, 'reason': 'Missing fields.'})
            continue
        try:
            start_t = datetime.strptime(start_s, "%H:%M").time()
            end_t = datetime.strptime(end_s, "%H:%M").time()
        except ValueError:
            skipped.append({'tt_id': tt_id, 'reason': 'Invalid time.'})
            continue

        # Each move gets its own atomic + row-locked check-then-write, so a
        # move later in this same batch always sees every earlier move in
        # the batch (already committed) AND can't race a concurrent request
        # from elsewhere touching the same rows.
        with transaction.atomic():
            tt, bundle = _get_move_bundle(tt_id)
            if not tt or not tt.course_allocation:
                skipped.append({'tt_id': tt_id, 'reason': 'Entry not found (already moved?).'})
                continue

            allocation = tt.course_allocation
            exclude_ids = [b.id for b in bundle]

            list(Timetable.objects.filter(id__in=exclude_ids).select_for_update().values_list('id', flat=True))

            messages_list, error_block = _check_conflicts_excluding(
                allocation, venue_input, day, start_t, end_t, exclude_ids, for_update=True
            )
            if error_block and not (global_force or mv.get('force')):
                skipped.append({
                    'tt_id': tt_id,
                    'course_code': allocation.course_code,
                    'reason': '; '.join(m for m in messages_list if '❌' in m) or 'Conflict detected.',
                })
                continue

            venue_obj, _c = Venue.objects.get_or_create(code=venue_input, defaults={'capacity': None})
            Timetable.objects.filter(id__in=exclude_ids).update(
                day=day, start_time=start_t, end_time=end_t, venue=venue_obj
            )
            applied.append({
                'tt_id': tt_id, 'course_code': allocation.course_code,
                'moved_count': len(bundle), 'day': day, 'start': start_s, 'venue': venue_obj.code,
            })

    return JsonResponse({
        'status': 'success',
        'applied_count': len(applied),
        'skipped_count': len(skipped),
        'applied': applied,
        'skipped': skipped,
    })


# ═══════════════════════════════════════════════════════════════════
# COPY TO — schedule the SAME course a second (or third…) time at a
# different day/timeslot/venue, leaving the original entry in place.
#
# Deliberately mirrors Simulate Move's shape (same slot catalog, same
# `_check_conflicts_excluding`, same combined-group bundle resolution)
# so a copied course is checked against every existing booking with the
# exact same rules as a move — the only difference is the ORIGINAL
# entry (and its bundle-mates, for a combined group) is never touched
# or excluded from the conflict check: we're not vacating that slot,
# we're adding a second, independent one.
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def simulate_copy_api(request):
    _reset_combined_group_cache()  # fresh, correct data for this request
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'messages': ['POST required.']}, status=405)

    tt_id = request.POST.get('tt_id')
    day = request.POST.get('day')
    venue_input = (request.POST.get('venue') or '').strip()
    start_s = request.POST.get('start')
    end_s = request.POST.get('end')

    if not (tt_id and day and venue_input and start_s and end_s):
        return JsonResponse({'status': 'error', 'messages': ['All fields are required.']}, status=400)

    try:
        start_t = datetime.strptime(start_s, "%H:%M").time()
        end_t = datetime.strptime(end_s, "%H:%M").time()
    except ValueError:
        return JsonResponse({'status': 'error', 'messages': ['Invalid time format.']}, status=400)

    tt, bundle = _get_move_bundle(tt_id)
    if not tt or not tt.course_allocation:
        return JsonResponse({'status': 'error', 'messages': ['Timetable entry not found.']}, status=404)

    allocation = tt.course_allocation

    same_slot = (
        tt.day == day and tt.start_time == start_t and tt.end_time == end_t and
        tt.venue and tt.venue.code.lower() == venue_input.lower()
    )

    # No exclusions here — the original booking(s) must still be checked
    # against, since they keep existing after the copy is made.
    messages_list, error_block = _check_conflicts_excluding(
        allocation, venue_input, day, start_t, end_t, exclude_tt_ids=[]
    )

    if same_slot:
        messages_list = [
            "❌ That's the course's current slot — pick a different day, timeslot or venue to copy it to."
        ] + messages_list
        error_block = True

    recommendations = []
    if error_block:
        venues = list(Venue.objects.all())
        recommendations = _find_recommendations(
            allocation, venues, exclude_tt_ids=[], prefer_day=day, prefer_venue=venue_input, limit=8
        )

    return JsonResponse({
        'status': 'conflict' if error_block else 'ok',
        'messages': messages_list,
        'course_code': allocation.course_code,
        'lecturer': getattr(allocation.lecturer, 'name', 'Unassigned'),
        'bundle_size': len(bundle),
        'recommendations': recommendations,
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def execute_copy_api(request):
    _reset_combined_group_cache()  # fresh, correct data for this request
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'messages': ['POST required.']}, status=405)

    tt_id = request.POST.get('tt_id')
    day = request.POST.get('day')
    venue_input = (request.POST.get('venue') or '').strip()
    start_s = request.POST.get('start')
    end_s = request.POST.get('end')
    force = (request.POST.get('force') or '').lower() == 'true'

    if not (tt_id and day and venue_input and start_s and end_s):
        return JsonResponse({'status': 'error', 'messages': ['All fields are required.']}, status=400)

    try:
        start_t = datetime.strptime(start_s, "%H:%M").time()
        end_t = datetime.strptime(end_s, "%H:%M").time()
    except ValueError:
        return JsonResponse({'status': 'error', 'messages': ['Invalid time format.']}, status=400)

    with transaction.atomic():
        tt, bundle = _get_move_bundle(tt_id)
        if not tt or not tt.course_allocation:
            return JsonResponse({'status': 'error', 'messages': ['Timetable entry not found.']}, status=404)

        allocation = tt.course_allocation

        same_slot = (
            tt.day == day and tt.start_time == start_t and tt.end_time == end_t and
            tt.venue and tt.venue.code.lower() == venue_input.lower()
        )
        if same_slot and not force:
            return JsonResponse({
                'status': 'error',
                'messages': ["That's the course's current slot — pick a different day, timeslot or venue to copy it to."],
            }, status=409)

        # Row-locked check under the same atomic block that creates the new
        # rows, so a concurrent move/swap can't slip a conflicting row into
        # this exact target slot between the check and the write.
        messages_list, error_block = _check_conflicts_excluding(
            allocation, venue_input, day, start_t, end_t, exclude_tt_ids=[], for_update=True
        )
        if error_block and not force:
            return JsonResponse({'status': 'error', 'messages': messages_list}, status=409)

        venue_obj, _created = Venue.objects.get_or_create(code=venue_input, defaults={'capacity': None})

        # Copy every row in the bundle (a combined group / auto-merged set
        # travels together, same as a move) — but as NEW rows, so the
        # originals stay exactly where they are.
        new_rows = Timetable.objects.bulk_create([
            Timetable(
                course_allocation=row.course_allocation,
                venue=venue_obj,
                day=day,
                start_time=start_t,
                end_time=end_t,
            )
            for row in bundle
        ])

    n = len(new_rows)
    return JsonResponse({
        'status': 'success',
        'messages': (messages_list or []) + [
            f"Copied {allocation.course_code} ({n} entr{'y' if n == 1 else 'ies'}) "
            f"to {day} {start_s}-{end_s} @ {venue_obj.code}. The original slot is unchanged."
        ],
        'copied_count': n,
        'new_tt_ids': [r.id for r in new_rows],
    })
