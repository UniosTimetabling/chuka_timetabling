"""
timetable/venue_evacuation.py
==============================
"Move all courses out of this venue" feature for the Timetable Panel.

Right-click a VENUE ROW HEADER (not a course cell) and every course
currently booked in that venue — on any day, any timeslot — gets a new
home, following a cascading placement strategy from least- to
most-disruptive:

  TIER 1 — same day & same timeslot, in another venue that is not
           blocked and not specialized/designated. If several such
           venues are free, the one whose capacity is CLOSEST to the
           evacuated venue's own capacity is picked (even if that
           closest match happens to be a lower capacity than the
           original — there simply wasn't a better one).

  TIER 2 — no eligible venue was free at the original day+timeslot at
           all, so every other day/timeslot is scanned for a venue that
           is completely collision-free (no lecturer clash, no
           program-year clash, no venue double-booking), preferring the
           original day first, then the closest capacity match.

  TIER 3 — still nothing fully clean anywhere, so fall back to the
           SAME original day, any timeslot, any eligible venue — the
           ONLY rule enforced here is that the venue itself must not
           already be double-booked at that day+time (no two different
           bookings sharing one room). Lecturer/program-year collisions
           are not checked at this tier and the entry is flagged for
           manual review.

If a course can't be placed even under Tier 3 (no eligible venue free
anywhere on its own day), it is reported back as UNRESOLVED and left
exactly where it is — nothing is ever silently dropped.

Design notes
------------
- "Not blocked or specialized" mirrors exactly how the Timetable Panel
  itself flags `is_blocked` / `is_designated` rows (see
  `timetable_panel.timetable_panel`): blocked = an ACTIVE VenueBlock
  rule; designated/specialized = `Venue.is_specialized` OR a
  VenueSpecialization rule naming that venue.
- Tiers 1 and 3 use simple in-memory interval-overlap checks against a
  venue-occupancy map (fast, no per-candidate DB hit). Tier 2 also
  checks lecturer/program-year busy maps the same way `simulate_move.
  _find_recommendations` does — an approximate scan used only to RANK
  candidates. Nothing here re-derives conflict rules from scratch for
  the DB write: `execute` re-validates every move with the exact same
  authoritative `simulate_move._check_conflicts_excluding` used
  everywhere else in the panel before committing it.
- Moves already planned earlier in the SAME evacuation batch are folded
  back into the occupancy maps as they're decided, so two courses being
  evacuated out of the same venue are never both offered the same
  target slot.
"""

from datetime import datetime
from collections import defaultdict
import json

from django.db import transaction
from django.http import JsonResponse
from core.rbac import allowed_roles, Role

from timetable.models import Timetable, SchedulerConfig
from room_management.models import Venue, VenueBlock, VenueSpecialization

from timetable.timetable_panel import _get_year_value, time_overlaps
from timetable.simulate_move import (
    _get_slot_catalog,
    _get_move_bundle,
    _check_conflicts_excluding,
)


# ═══════════════════════════════════════════════════════════════════
# Eligible venue pool — never a blocked or specialized/designated venue
# ═══════════════════════════════════════════════════════════════════
def _blocked_and_designated_codes():
    blocked = set(
        VenueBlock.objects.filter(is_active=True).values_list('venue__code', flat=True)
    )
    designated = set(
        Venue.objects.filter(is_specialized=True).values_list('code', flat=True)
    ) | set(
        VenueSpecialization.objects.values_list('venues__code', flat=True)
    )
    designated.discard(None)
    return blocked, designated


# ═══════════════════════════════════════════════════════════════════
# Planning — the tiered cascade described above
# ═══════════════════════════════════════════════════════════════════
def _plan_venue_evacuation(venue_code):
    venue = Venue.objects.filter(code__iexact=venue_code).first()
    if not venue:
        return None, [], []

    blocked_codes, designated_codes = _blocked_and_designated_codes()
    excluded_codes = blocked_codes | designated_codes | {venue.code}
    eligible_venues = [v for v in Venue.objects.all() if v.code not in excluded_codes]

    # ── Group every current booking in this venue into move-bundles ──
    # (a "bundle" = every Timetable row that prints as one cell: a
    # Combined Course Group or an auto-merged duplicate set, exactly
    # like every other move feature in this panel.)
    qs = Timetable.objects.filter(venue_id=venue.id).select_related(
        'course_allocation', 'course_allocation__lecturer',
        'course_allocation__program', 'venue',
    ).order_by('day', 'start_time')

    seen_ids = set()
    bundles = []
    for t in qs:
        if t.id in seen_ids:
            continue
        rep, bundle = _get_move_bundle(t.id)
        if not rep:
            continue
        for b in bundle:
            seen_ids.add(b.id)
        bundles.append((rep, bundle))

    if not bundles:
        return venue, [], []

    own_ids = list(seen_ids)
    catalog = _get_slot_catalog()

    # ── Occupancy maps, seeded from every OTHER booking in the system ──
    # (own_ids are excluded since those rows are exactly what we're
    # relocating — they must not block their own new placement.)
    venue_busy = defaultdict(lambda: defaultdict(list))
    lecturer_busy = defaultdict(lambda: defaultdict(list))
    program_busy = defaultdict(lambda: defaultdict(list))  # (program_id, year) -> day -> [(s,e)]

    for t in Timetable.objects.exclude(id__in=own_ids).select_related(
        'course_allocation', 'course_allocation__program', 'venue'
    ).only(
        'day', 'start_time', 'end_time', 'venue__code',
        'course_allocation__lecturer_id', 'course_allocation__program_id',
    ):
        if t.venue:
            venue_busy[t.venue.code][t.day].append((t.start_time, t.end_time))
        alloc = t.course_allocation
        if not alloc:
            continue
        if alloc.lecturer_id:
            lecturer_busy[alloc.lecturer_id][t.day].append((t.start_time, t.end_time))
        yr = _get_year_value(alloc)
        if alloc.program_id and yr is not None:
            program_busy[(alloc.program_id, yr)][t.day].append((t.start_time, t.end_time))

    def venue_free(vcode, day, start_t, end_t):
        return not any(
            time_overlaps(start_t, end_t, s, e)
            for (s, e) in venue_busy.get(vcode, {}).get(day, [])
        )

    plan = []
    unresolved = []

    for rep, bundle in bundles:
        alloc = rep.course_allocation
        exclude_ids = [b.id for b in bundle]

        if not alloc:
            unresolved.append({
                'tt_id': rep.id, 'bundle_ids': exclude_ids, 'course_code': None,
                'day': rep.day, 'start': rep.start_time.strftime('%H:%M'),
                'end': rep.end_time.strftime('%H:%M'),
                'reason': 'This entry has no linked course allocation — move it manually.',
            })
            continue

        day0, start0, end0 = rep.day, rep.start_time, rep.end_time
        orig_cap = venue.capacity or 0
        alloc_year = _get_year_value(alloc)
        lecturer_id = alloc.lecturer_id
        program_key = (alloc.program_id, alloc_year) if (alloc.program_id and alloc_year is not None) else None

        def lecturer_free(day, start_t, end_t):
            if not lecturer_id:
                return True
            return not any(
                time_overlaps(start_t, end_t, s, e)
                for (s, e) in lecturer_busy.get(lecturer_id, {}).get(day, [])
            )

        def program_free(day, start_t, end_t):
            if not program_key:
                return True
            return not any(
                time_overlaps(start_t, end_t, s, e)
                for (s, e) in program_busy.get(program_key, {}).get(day, [])
            )

        chosen = None

        # ── TIER 1 — same day & timeslot, eligible venue, venue-free ──
        t1 = [v for v in eligible_venues if venue_free(v.code, day0, start0, end0)]
        if t1:
            t1.sort(key=lambda v: (abs((v.capacity or 0) - orig_cap), -(v.capacity or 0), v.code))
            v = t1[0]
            lower_cap = (v.capacity or 0) < orig_cap
            chosen = {
                'tier': 1, 'day': day0, 'start': start0.strftime('%H:%M'),
                'end': end0.strftime('%H:%M'), 'venue': v.code, 'capacity': v.capacity,
                'note': (
                    f"Same day & time — closest free venue was {v.code} "
                    f"(cap {v.capacity or '—'}), lower than {venue.code}'s "
                    f"{orig_cap or '—'}; no better match was free."
                ) if lower_cap else (
                    f"Same day & time — moved to {v.code} (cap {v.capacity or '—'})."
                ),
            }

        # ── TIER 2 — any day/timeslot, fully collision-free ───────────
        if not chosen:
            t2 = []
            for slot in catalog:
                day, start_s, end_s = slot['day'], slot['start'], slot['end']
                start_t = datetime.strptime(start_s, "%H:%M").time()
                end_t = datetime.strptime(end_s, "%H:%M").time()
                if not lecturer_free(day, start_t, end_t):
                    continue
                if not program_free(day, start_t, end_t):
                    continue
                for v in eligible_venues:
                    if not venue_free(v.code, day, start_t, end_t):
                        continue
                    t2.append((day, start_s, end_s, v))
            if t2:
                t2.sort(key=lambda c: (
                    0 if c[0] == day0 else 1,
                    abs((c[3].capacity or 0) - orig_cap),
                    -(c[3].capacity or 0), c[0], c[1], c[3].code,
                ))
                day, start_s, end_s, v = t2[0]
                chosen = {
                    'tier': 2, 'day': day, 'start': start_s, 'end': end_s,
                    'venue': v.code, 'capacity': v.capacity,
                    'note': (
                        f"No eligible venue was free at the original {day0} slot — "
                        f"moved to {day} {start_s}\u2013{end_s} @ {v.code} "
                        f"(the nearest fully conflict-free option)."
                    ),
                }

        # ── TIER 3 — same day, any timeslot, only avoid double-booking ─
        if not chosen:
            t3 = []
            for slot in catalog:
                if slot['day'] != day0:
                    continue
                start_t = datetime.strptime(slot['start'], "%H:%M").time()
                end_t = datetime.strptime(slot['end'], "%H:%M").time()
                for v in eligible_venues:
                    if not venue_free(v.code, day0, start_t, end_t):
                        continue
                    t3.append((slot['start'], slot['end'], v))
            if t3:
                t3.sort(key=lambda c: (
                    abs((c[2].capacity or 0) - orig_cap), -(c[2].capacity or 0), c[0], c[2].code,
                ))
                start_s, end_s, v = t3[0]
                chosen = {
                    'tier': 3, 'day': day0, 'start': start_s, 'end': end_s,
                    'venue': v.code, 'capacity': v.capacity, 'relaxed': True,
                    'note': (
                        f"\u26a0\ufe0f No fully conflict-free slot existed anywhere for "
                        f"{alloc.course_code} — placed on {day0} at {start_s}\u2013{end_s} "
                        f"@ {v.code} with only the venue double-booking check enforced. "
                        f"Please verify lecturer/program clashes manually."
                    ),
                }

        if not chosen:
            unresolved.append({
                'tt_id': rep.id, 'bundle_ids': exclude_ids,
                'course_code': alloc.course_code,
                'lecturer': getattr(alloc.lecturer, 'name', 'Unassigned'),
                'day': day0, 'start': start0.strftime('%H:%M'), 'end': end0.strftime('%H:%M'),
                'reason': (
                    'No eligible venue was free anywhere on this day, not even with '
                    'the relaxed (double-booking only) check.'
                ),
            })
            continue

        plan.append({
            'tt_id': rep.id,
            'bundle_ids': exclude_ids,
            'bundle_size': len(bundle),
            'course_code': alloc.course_code,
            'lecturer': getattr(alloc.lecturer, 'name', 'Unassigned'),
            'students': alloc.number_of_students or 0,
            'from': {
                'day': day0, 'start': start0.strftime('%H:%M'),
                'end': end0.strftime('%H:%M'), 'venue': venue.code,
            },
            'to': chosen,
        })

        # Reserve the chosen slot so later bundles in this same batch
        # don't get offered the exact same target.
        c_start_t = datetime.strptime(chosen['start'], "%H:%M").time()
        c_end_t = datetime.strptime(chosen['end'], "%H:%M").time()
        venue_busy[chosen['venue']][chosen['day']].append((c_start_t, c_end_t))
        if lecturer_id:
            lecturer_busy[lecturer_id][chosen['day']].append((c_start_t, c_end_t))
        if program_key:
            program_busy[program_key][chosen['day']].append((c_start_t, c_end_t))

    return venue, plan, unresolved


# ═══════════════════════════════════════════════════════════════════
# API — preview (no writes)
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def evacuate_venue_preview_api(request):
    venue_code = (request.GET.get('venue') or '').strip()
    if not venue_code:
        return JsonResponse({'status': 'error', 'messages': ['venue is required.']}, status=400)

    venue, plan, unresolved = _plan_venue_evacuation(venue_code)
    if not venue:
        return JsonResponse(
            {'status': 'error', 'messages': [f"Venue '{venue_code}' not found."]}, status=404
        )

    if not plan and not unresolved:
        return JsonResponse({
            'status': 'empty',
            'messages': [f'{venue.code} has no scheduled courses to move.'],
            'venue': venue.code, 'plan': [], 'unresolved': [],
        })

    return JsonResponse({
        'status': 'success',
        'venue': venue.code,
        'plan': plan,
        'unresolved': unresolved,
        'plan_count': len(plan),
        'unresolved_count': len(unresolved),
    })


# ═══════════════════════════════════════════════════════════════════
# API — commit the plan (re-validated authoritatively per move)
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def evacuate_venue_execute_api(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'messages': ['POST required.']}, status=405)

    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'messages': ['Invalid JSON.']}, status=400)

    moves = payload.get('moves') or []
    if not moves:
        return JsonResponse({'status': 'error', 'messages': ['No moves supplied.']}, status=400)

    applied, skipped = [], []

    for mv in moves:
        tt_id = mv.get('tt_id')
        day = mv.get('day')
        venue_input = (mv.get('venue') or '').strip()
        start_s = mv.get('start')
        end_s = mv.get('end')
        # Tier 3 moves are already flagged to the admin as relaxed
        # (venue-double-booking-only) in the preview, so they're always
        # force-committed here; Tiers 1/2 were planned conflict-free and
        # only need force if something changed underneath since preview.
        tier = mv.get('tier')
        force = bool(mv.get('force')) or tier == 3

        if not (tt_id and day and venue_input and start_s and end_s):
            skipped.append({'tt_id': tt_id, 'reason': 'Missing fields.'})
            continue
        try:
            start_t = datetime.strptime(start_s, "%H:%M").time()
            end_t = datetime.strptime(end_s, "%H:%M").time()
        except ValueError:
            skipped.append({'tt_id': tt_id, 'reason': 'Invalid time.'})
            continue

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
            if error_block and not force:
                skipped.append({
                    'tt_id': tt_id,
                    'course_code': allocation.course_code,
                    'reason': '; '.join(m for m in messages_list if '\u274c' in m) or 'Conflict detected.',
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
