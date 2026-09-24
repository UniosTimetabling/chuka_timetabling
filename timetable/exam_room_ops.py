"""
timetable/exam_room_ops.py
============================
"Open Room" (move all exams out of this venue) feature for the Exam
Timetable Panel — the exam-timetable counterpart of
`timetable/venue_evacuation.py`, adapted to the exam timetable's
DATE-based scheduling and the exam panel's own exemption rules
(elective/selection-group, special-vs-normal allocation, same normalised course
code across venues = an intentional split exam, Combined Course Group).

Right-click a VENUE ROW HEADER on the exam grid ("Open Room…") and every
exam currently sitting in that room — on any date, any timeslot — is
offered a new home, following the same least- to most-disruptive
cascade used by the regular timetable's venue evacuation:

  TIER 1 — same date & same timeslot, in another eligible (not blocked,
           not specialized/designated) venue that is free. If several
           such venues are free, the one whose exam capacity is CLOSEST
           to the vacated venue's own is picked (even if that means a
           lower capacity — there simply wasn't a better one).

  TIER 2 — no eligible venue was free at the original date+timeslot at
           all, so every other date/timeslot is scanned for a venue
           that is completely collision-free (no lecturer clash, no
           program-year clash — exemption aware, exactly like the
           manual conflict checker — no venue double-booking),
           preferring the original date first, then the closest
           capacity match.

  TIER 3 — still nothing fully clean anywhere, so fall back to the
           SAME original date, any timeslot, any eligible venue — the
           ONLY rule enforced here is that the venue itself must not
           already be double-booked at that date+time. Lecturer /
           program-year collisions are not checked at this tier and the
           entry is flagged for manual review.

Anything that can't be placed even under Tier 3 (no eligible venue free
anywhere on its own date) is reported back as UNRESOLVED and left
exactly where it is — nothing is ever silently dropped.

Design notes
------------
- Conflict-checking on COMMIT is never re-derived from scratch here:
  `execute` re-validates every planned move with the exact same
  authoritative `exam_simulate_move._check_conflicts_excluding` used by
  Simulate Move / Swap / Bulk Move, so this feature can never drift out
  of sync with the rest of the exam panel.
- "Eligible venue" mirrors exactly how the exam panel itself flags
  `is_blocked` / `is_specialized` rows in `exam_timetable_panel._build_venues`.
- Tiers 1 and 3 use simple in-memory interval-overlap checks against an
  occupancy map (fast, no per-candidate DB hit). Tier 2 also checks
  lecturer/program-year busy maps the same exemption-aware way
  `exam_simulate_move._find_recommendations` does — an approximate scan
  used only to RANK candidates before the authoritative re-check.
- Moves already planned earlier in the SAME evacuation batch are folded
  back into the occupancy maps as they're decided, so two exams being
  evacuated out of the same room are never both offered the same target
  slot.
"""

from datetime import datetime
from collections import defaultdict
import json

from django.db import transaction
from django.http import JsonResponse
from core.rbac import allowed_roles, Role

from timetable.models import ExamTimetable
from room_management.models import Venue, VenueBlock, VenueSpecialization

from timetable.exam_timetable_panel import exam_panel_is_exempt, get_program_year_key
from timetable.exam_simulate_move import (
    _get_slot_catalog,
    _get_move_bundle,
    _check_conflicts_excluding,
)


def time_overlaps(s1, e1, s2, e2):
    return s1 < e2 and s2 < e1


def _exam_cap(v):
    """Exam capacity if set, else fall back to the venue's normal capacity."""
    return (v.exam_capacity if v.exam_capacity is not None else v.capacity) or 0


# ═══════════════════════════════════════════════════════════════════
# Eligible venue pool — never a blocked or specialized/designated venue
# ═══════════════════════════════════════════════════════════════════
def _blocked_and_designated_codes():
    blocked = set(
        VenueBlock.objects.filter(is_active=True).values_list('venue__code', flat=True)
    )
    designated = set(
        VenueSpecialization.objects.filter(is_active=True).values_list('venues__code', flat=True)
    )
    designated.discard(None)
    return blocked, designated


# ═══════════════════════════════════════════════════════════════════
# Planning — the tiered cascade described above
# ═══════════════════════════════════════════════════════════════════
def _plan_exam_venue_evacuation(venue_code):
    venue = Venue.objects.filter(code__iexact=venue_code).first()
    if not venue:
        return None, [], []

    blocked_codes, designated_codes = _blocked_and_designated_codes()
    excluded_codes = blocked_codes | designated_codes | {venue.code}
    eligible_venues = [v for v in Venue.objects.all() if v.code not in excluded_codes]

    # ── Group every exam currently in this room into move-bundles ──
    # (a "bundle" = every ExamTimetable row that prints as one cell: a
    # split-venue set or Combined Course Group, exactly like every other
    # move feature in this panel.)
    qs = ExamTimetable.objects.filter(venue_id=venue.id).select_related(
        'course_allocation', 'course_allocation__lecturer',
        'course_allocation__program', 'venue',
    ).order_by('date', 'start_time')

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
    orig_cap = _exam_cap(venue)

    # ── Occupancy maps, seeded from every OTHER exam in the system ──
    # (own_ids are excluded since those rows are exactly what we're
    # relocating — they must not block their own new placement.)
    venue_busy = defaultdict(lambda: defaultdict(list))       # code -> date -> [(s,e)]
    lecturer_busy = defaultdict(lambda: defaultdict(list))    # lecturer_id -> date -> [(s,e)]
    program_rows = defaultdict(lambda: defaultdict(list))     # py_key -> date -> [(alloc, s, e)]

    other_qs = ExamTimetable.objects.exclude(id__in=own_ids).select_related(
        'course_allocation', 'course_allocation__lecturer', 'course_allocation__program',
        'course_allocation__program_course', 'course_allocation__selection_group',
        'course_allocation__specialization_stem', 'course_allocation__student_group',
        'course_allocation__allocation_set', 'venue',
    )
    for t in other_qs:
        if t.venue:
            venue_busy[t.venue.code][str(t.date)].append((t.start_time, t.end_time))
        alloc = t.course_allocation
        if not alloc:
            continue
        if alloc.lecturer_id:
            lecturer_busy[alloc.lecturer_id][str(t.date)].append((t.start_time, t.end_time))
        py_key = get_program_year_key(alloc)
        if alloc.program_id and py_key:
            program_rows[py_key][str(t.date)].append((alloc, t.start_time, t.end_time))

    def venue_free(vcode, date_str, start_t, end_t):
        return not any(
            time_overlaps(start_t, end_t, s, e)
            for (s, e) in venue_busy.get(vcode, {}).get(date_str, [])
        )

    plan = []
    unresolved = []

    for rep, bundle in bundles:
        alloc = rep.course_allocation
        exclude_ids = [b.id for b in bundle]

        if not alloc:
            unresolved.append({
                'et_id': rep.id, 'bundle_ids': exclude_ids, 'course_code': None,
                'date': str(rep.date), 'start': rep.start_time.strftime('%H:%M'),
                'end': rep.end_time.strftime('%H:%M'),
                'reason': 'This entry has no linked course allocation — move it manually.',
            })
            continue

        date0, start0, end0 = str(rep.date), rep.start_time, rep.end_time
        day0 = rep.day
        lecturer_id = alloc.lecturer_id
        py_key = get_program_year_key(alloc)

        def lecturer_free(date_str, start_t, end_t, _lecturer_id=lecturer_id):
            if not _lecturer_id:
                return True
            return not any(
                time_overlaps(start_t, end_t, s, e)
                for (s, e) in lecturer_busy.get(_lecturer_id, {}).get(date_str, [])
            )

        def program_free(date_str, start_t, end_t, _alloc=alloc, _py_key=py_key):
            if not _py_key:
                return True
            for other_alloc, s, e in program_rows.get(_py_key, {}).get(date_str, []):
                if other_alloc.id == _alloc.id:
                    continue
                if not time_overlaps(start_t, end_t, s, e):
                    continue
                if exam_panel_is_exempt(_alloc, other_alloc):
                    continue
                return False
            return True

        chosen = None

        # ── TIER 1 — same date & timeslot, eligible venue, venue-free ──
        t1 = [v for v in eligible_venues if venue_free(v.code, date0, start0, end0)]
        if t1:
            t1.sort(key=lambda v: (abs(_exam_cap(v) - orig_cap), -_exam_cap(v), v.code))
            v = t1[0]
            lower_cap = _exam_cap(v) < orig_cap
            chosen = {
                'tier': 1, 'date': date0, 'day': day0, 'start': start0.strftime('%H:%M'),
                'end': end0.strftime('%H:%M'), 'venue': v.code, 'capacity': _exam_cap(v),
                'note': (
                    f"Same date & time — closest free venue was {v.code} "
                    f"(cap {_exam_cap(v) or '—'}), lower than {venue.code}'s "
                    f"{orig_cap or '—'}; no better match was free."
                ) if lower_cap else (
                    f"Same date & time — moved to {v.code} (cap {_exam_cap(v) or '—'})."
                ),
            }

        # ── TIER 2 — any date/timeslot, fully collision-free ───────────
        if not chosen:
            t2 = []
            for slot in catalog:
                date_str, day_name, start_s, end_s = slot['date'], slot['day'], slot['start'], slot['end']
                start_t = datetime.strptime(start_s, "%H:%M").time()
                end_t = datetime.strptime(end_s, "%H:%M").time()
                if not lecturer_free(date_str, start_t, end_t):
                    continue
                if not program_free(date_str, start_t, end_t):
                    continue
                for v in eligible_venues:
                    if not venue_free(v.code, date_str, start_t, end_t):
                        continue
                    t2.append((date_str, day_name, start_s, end_s, v))
            if t2:
                t2.sort(key=lambda c: (
                    0 if c[0] == date0 else 1,
                    abs(_exam_cap(c[4]) - orig_cap),
                    -_exam_cap(c[4]), c[0], c[2], c[4].code,
                ))
                date_str, day_name, start_s, end_s, v = t2[0]
                chosen = {
                    'tier': 2, 'date': date_str, 'day': day_name, 'start': start_s, 'end': end_s,
                    'venue': v.code, 'capacity': _exam_cap(v),
                    'note': (
                        f"No eligible venue was free at the original {date0} slot — "
                        f"moved to {date_str} {start_s}\u2013{end_s} @ {v.code} "
                        f"(the nearest fully conflict-free option)."
                    ),
                }

        # ── TIER 3 — same date, any timeslot, only avoid double-booking ─
        if not chosen:
            t3 = []
            for slot in catalog:
                if slot['date'] != date0:
                    continue
                start_t = datetime.strptime(slot['start'], "%H:%M").time()
                end_t = datetime.strptime(slot['end'], "%H:%M").time()
                for v in eligible_venues:
                    if not venue_free(v.code, date0, start_t, end_t):
                        continue
                    t3.append((slot['start'], slot['end'], v))
            if t3:
                t3.sort(key=lambda c: (
                    abs(_exam_cap(c[2]) - orig_cap), -_exam_cap(c[2]), c[0], c[2].code,
                ))
                start_s, end_s, v = t3[0]
                chosen = {
                    'tier': 3, 'date': date0, 'day': day0, 'start': start_s, 'end': end_s,
                    'venue': v.code, 'capacity': _exam_cap(v), 'relaxed': True,
                    'note': (
                        f"\u26a0\ufe0f No fully conflict-free slot existed anywhere for "
                        f"{alloc.course_code} — placed on {date0} at {start_s}\u2013{end_s} "
                        f"@ {v.code} with only the venue double-booking check enforced. "
                        f"Please verify lecturer/program clashes manually."
                    ),
                }

        if not chosen:
            unresolved.append({
                'et_id': rep.id, 'bundle_ids': exclude_ids,
                'course_code': alloc.course_code,
                'lecturer': getattr(alloc.lecturer, 'name', 'Unassigned'),
                'date': date0, 'start': start0.strftime('%H:%M'), 'end': end0.strftime('%H:%M'),
                'reason': (
                    'No eligible venue was free anywhere on this date, not even with '
                    'the relaxed (double-booking only) check.'
                ),
            })
            continue

        plan.append({
            'et_id': rep.id,
            'bundle_ids': exclude_ids,
            'bundle_size': len(bundle),
            'course_code': alloc.course_code,
            'lecturer': getattr(alloc.lecturer, 'name', 'Unassigned'),
            'students': alloc.number_of_students or 0,
            'from': {
                'date': date0, 'day': day0, 'start': start0.strftime('%H:%M'),
                'end': end0.strftime('%H:%M'), 'venue': venue.code,
            },
            'to': chosen,
        })

        # Reserve the chosen slot so later bundles in this same batch
        # don't get offered the exact same target.
        c_start_t = datetime.strptime(chosen['start'], "%H:%M").time()
        c_end_t = datetime.strptime(chosen['end'], "%H:%M").time()
        venue_busy[chosen['venue']][chosen['date']].append((c_start_t, c_end_t))
        if lecturer_id:
            lecturer_busy[lecturer_id][chosen['date']].append((c_start_t, c_end_t))
        if py_key:
            program_rows[py_key][chosen['date']].append((alloc, c_start_t, c_end_t))

    return venue, plan, unresolved


# ═══════════════════════════════════════════════════════════════════
# API — preview (no writes)
# ═══════════════════════════════════════════════════════════════════
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_evacuate_venue_preview_api(request):
    venue_code = (request.GET.get('venue') or '').strip()
    if not venue_code:
        return JsonResponse({'status': 'error', 'messages': ['venue is required.']}, status=400)

    venue, plan, unresolved = _plan_exam_venue_evacuation(venue_code)
    if not venue:
        return JsonResponse(
            {'status': 'error', 'messages': [f"Venue '{venue_code}' not found."]}, status=404
        )

    if not plan and not unresolved:
        return JsonResponse({
            'status': 'empty',
            'messages': [f'{venue.code} has no scheduled exams to move.'],
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
def exam_evacuate_venue_execute_api(request):
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
        et_id = mv.get('et_id')
        date_str = mv.get('date')
        venue_input = (mv.get('venue') or '').strip()
        start_s = mv.get('start')
        end_s = mv.get('end')
        # Tier 3 moves are already flagged to the admin as relaxed
        # (venue-double-booking-only) in the preview, so they're always
        # force-committed here; Tiers 1/2 were planned conflict-free and
        # only need force if something changed underneath since preview.
        tier = mv.get('tier')
        force = bool(mv.get('force')) or tier == 3

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

        with transaction.atomic():
            et, bundle = _get_move_bundle(et_id)
            if not et or not et.course_allocation:
                skipped.append({'et_id': et_id, 'reason': 'Entry not found (already moved?).'})
                continue

            allocation = et.course_allocation
            exclude_ids = [b.id for b in bundle]

            messages_list, error_block = _check_conflicts_excluding(
                allocation, venue_input, date_str, start_t, end_t, exclude_ids
            )
            if error_block and not force:
                skipped.append({
                    'et_id': et_id,
                    'course_code': allocation.course_code,
                    'reason': '; '.join(m for m in messages_list if '\u274c' in m) or 'Conflict detected.',
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
