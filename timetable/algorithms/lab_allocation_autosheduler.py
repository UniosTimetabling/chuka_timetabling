import math
import datetime
import random

from django.db import transaction
from django.db.models import Q
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.http import JsonResponse

from timetable.models import LabTimetable, LabSchedulerConfig, Timetable, TempTimetable
from course_allocation.models import LabAllocation


# ── helpers ───────────────────────────────────────────────────────────────────

def _to_time(value):
    if isinstance(value, datetime.time):
        return value
    if isinstance(value, str):
        parts = value.strip().split(":")
        try:
            return datetime.time(
                int(parts[0]),
                int(parts[1]) if len(parts) > 1 else 0,
                int(parts[2]) if len(parts) > 2 else 0,
            )
        except (ValueError, IndexError):
            pass
    return datetime.time(7, 0)


def _generate_slots(cfg):
    start_t = _to_time(cfg.start_time)
    end_t   = _to_time(cfg.end_time)
    try:
        slot_hours = int(cfg.slot_size)
    except (TypeError, ValueError):
        slot_hours = 2

    step   = datetime.timedelta(hours=slot_hours)
    today  = datetime.date.today()
    cur    = datetime.datetime.combine(today, start_t)
    end_dt = datetime.datetime.combine(today, end_t)

    slots = []
    while cur + step <= end_dt:
        slots.append((cur.time(), (cur + step).time()))
        cur += step
    return slots


def _venue_free(venue, day, st, et):
    """Venue is free at this slot — only blocked by the venue itself being occupied."""
    return not LabTimetable.objects.filter(
        lab_venue=venue,
        day=day,
        start_time__lt=et,
        end_time__gt=st,
    ).exists()


def _lecturer_free(lecturer, day, st, et):
    """Lecturer not teaching another lab at this slot."""
    if not lecturer:
        return True
    return not LabTimetable.objects.filter(
        lab_allocation__lecturer=lecturer,
        day=day,
        start_time__lt=et,
        end_time__gt=st,
    ).exists()


def _involved_program_ids(alloc):
    """
    A LabAllocation can bundle several course codes together (the primary
    program_course plus any additional_courses) and those course codes can
    belong to DIFFERENT programs — e.g. a shared elective lab taken by
    students from two different degree programs. Every one of those
    programs has students who must be free at the chosen day+slot, not
    just the primary course's program. Returns a set of Program IDs.
    """
    ids = set()
    if alloc.program_course_id and alloc.program_course and alloc.program_course.program_id:
        ids.add(alloc.program_course.program_id)
    ids.update(
        alloc.additional_courses
        .exclude(program__isnull=True)
        .values_list("program_id", flat=True)
    )
    return ids


def _program_lab_free(program_ids, day, st, et):
    """
    None of the involved programs should have a DIFFERENT lab course
    scheduled at the exact same day+slot — students can't be in two labs
    simultaneously. Checks the already-scheduled LabTimetable only, across
    every program touched by this allocation (primary + additional
    courses), not just the primary one.
    """
    if not program_ids:
        return True
    return not LabTimetable.objects.filter(
        Q(lab_allocation__program_course__program_id__in=program_ids)
        | Q(lab_allocation__additional_courses__program_id__in=program_ids),
        day=day,
        start_time__lt=et,
        end_time__gt=st,
    ).exists()


def _main_timetable_free(program_ids, day, st, et):
    """
    A lab should not clash with a scheduled lecture for any of the
    involved programs — students can't attend a lecture and a lab at the
    same time. Checks BOTH:
      • Timetable      — the published/live regular timetable.
      • TempTimetable  — the in-progress draft produced by a regular
        autoscheduler run that hasn't been published yet. This matters
        when the lab autoscheduler is chained immediately after the
        regular autoscheduler in the same "run" — at that point the
        freshly generated lecture slots live in TempTimetable, not yet
        in Timetable, and would otherwise be invisible to this check.
    """
    if not program_ids:
        return True
    clash_published = Timetable.objects.filter(
        course_allocation__program_id__in=program_ids,
        day=day,
        start_time__lt=et,
        end_time__gt=st,
    ).exists()
    if clash_published:
        return False

    clash_draft = TempTimetable.objects.filter(
        course_allocation__program_id__in=program_ids,
        day=day,
        start_time__lt=et,
        end_time__gt=st,
    ).exists()
    return not clash_draft


def _pick_venues(all_venues, students, day, st, et):
    """
    Find venue(s) for this slot, respecting only VENUE availability.
    Capacity determines how many venues needed (split if overflow > 10),
    but never causes a hard skip — best-effort if split not fully possible.

    Returns list of venues to use (1 = normal, 2+ = split groups).
    Returns [] only if every single venue is physically occupied at this slot.
    """
    # All venues free at this slot
    free = [v for v in all_venues if _venue_free(v, day, st, et)]
    if not free:
        return []

    free_desc = sorted(free, key=lambda v: v.capacity or 0, reverse=True)
    largest   = free_desc[0].capacity or 0

    # 1. Perfect fit — single venue holds all students
    for v in free_desc:
        if (v.capacity or 0) >= students:
            return [v]

    # 2. Single venue with ≤ 10 overflow
    for v in free_desc:
        if students - (v.capacity or 0) <= 10:
            return [v]

    # 3. Split needed — largest venue exceeded by more than 10
    if largest == 0:
        return [free_desc[0]]  # just use whatever is free

    num_groups = math.ceil(students / largest)
    group_size = math.ceil(students / num_groups)

    chosen = [v for v in free_desc if (v.capacity or 0) >= group_size]
    if len(chosen) >= num_groups:
        return chosen[:num_groups]

    # Best-effort: not enough correctly-sized venues, use what we have
    return free_desc[:min(num_groups, len(free_desc))]


# ── core scheduling routine ─────────────────────────────────────────────────

@transaction.atomic
def run_lab_autoscheduling(log=None):
    """
    Auto-schedules every LabAllocation into LabTimetable.

    This is the plain, request-free version of the scheduler so it can be
    called either from the HTTP view below, or chained straight after the
    regular (lecture) autoscheduler runs — see
    timetable.algorithms.regular_timetable_autosheduler_algorithm, which
    calls this once it has finished placing lectures, so labs are always
    scheduled against the very latest timetable state in the same run.

    Each allocation searches ALL days × ALL slots independently.
    A slot is considered blocked for a given allocation only when:

      1. ANY program involved in the allocation (the primary course's
         program, plus the program of every "additional course" bundled
         into the same lab session) already has a different lab scheduled
         at that exact day+slot  (students can't be in two labs at once).
      2. The LECTURER is already teaching another lab at that day+slot.
      3. The regular LECTURE timetable — published (Timetable) or an
         in-progress draft (TempTimetable) — has a class for any involved
         program at that day+slot (students can't attend a lecture and a
         lab at once).
      4. Every one of the allocation's candidate VENUES is already occupied
         at that day+slot.

    Critically — condition 1/2/3 only fires for that SPECIFIC allocation's
    programs/lecturer. Another allocation with completely different
    programs/lecturer is unaffected and will happily use the same
    day+slot in a different venue.

    Capacity is handled gracefully:
      • Fits in one venue  →  one session.
      • Overflow ≤ 10      →  one session (relaxed).
      • Overflow > 10      →  split into parallel groups, each in its own venue.
      • Can't split neatly →  best-effort with available free venues.

    Returns a plain dict (no HttpResponse) so callers can format it however
    they need:
        {"status": "success" | "warning" | "error", "message": str,
         "created_count": int, "skipped_no_venue": [...], "skipped_no_slot": [...]}
    """
    def _log(msg):
        if log:
            log(msg)

    LabTimetable.objects.all().delete()

    cfg = LabSchedulerConfig.objects.first()
    if not cfg:
        msg = "Scheduler configuration missing."
        _log(f"[Labs] {msg}")
        return {"status": "error", "message": msg, "created_count": 0}

    slots    = _generate_slots(cfg)
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    if not slots:
        msg = "No slots generated — check start/end time and slot size."
        _log(f"[Labs] {msg}")
        return {"status": "error", "message": msg, "created_count": 0}

    allocations = list(
        LabAllocation.objects
        .select_related("program_course", "program_course__program", "lecturer")
        .prefetch_related("venues", "additional_courses__program")
    )

    if not allocations:
        msg = "No lab allocations found."
        _log(f"[Labs] {msg}")
        return {"status": "error", "message": msg, "created_count": 0}

    random.shuffle(allocations)

    created_count    = 0
    scheduled_ids    = set()
    skipped_no_venue = []
    skipped_no_slot  = []

    for alloc in allocations:
        if alloc.id in scheduled_ids:
            continue

        students    = alloc.number_of_students or 0
        lecturer    = alloc.lecturer
        program_ids = _involved_program_ids(alloc)
        all_venues  = sorted(list(alloc.venues.all()), key=lambda v: v.capacity or 0)

        if not all_venues:
            skipped_no_venue.append(alloc.all_course_codes())
            continue

        placed = False

        for day in weekdays:
            if placed:
                break
            for st, et in slots:
                if placed:
                    break

                # ── 1. Any involved program already has a lab at this slot ───
                if not _program_lab_free(program_ids, day, st, et):
                    continue

                # ── 2. Lecturer already teaching a lab at this slot ───────────
                if not _lecturer_free(lecturer, day, st, et):
                    continue

                # ── 3. Any involved program has a lecture at this slot ────────
                if not _main_timetable_free(program_ids, day, st, et):
                    continue

                # ── 4. Find free venue(s) at this slot ────────────────────────
                chosen_venues = _pick_venues(all_venues, students, day, st, et)
                if not chosen_venues:
                    continue  # every candidate venue is occupied at this slot

                # ── Schedule ──────────────────────────────────────────────────
                for venue in chosen_venues:
                    LabTimetable.objects.create(
                        lab_allocation=alloc,
                        lab_venue=venue,
                        day=day,
                        start_time=st,
                        end_time=et,
                    )
                    created_count += 1

                scheduled_ids.add(alloc.id)
                placed = True

        if not placed:
            skipped_no_slot.append(alloc.all_course_codes())

    # ── Result ────────────────────────────────────────────────────────────────
    if created_count == 0:
        parts = ["No sessions scheduled."]
        if skipped_no_venue:
            parts.append(f"No venues configured: {', '.join(skipped_no_venue)}.")
        if skipped_no_slot:
            parts.append(f"No free slot: {', '.join(skipped_no_slot)}.")
        msg = " ".join(parts)
        _log(f"[Labs] {msg}")
        return {
            "status": "warning", "message": msg, "created_count": 0,
            "skipped_no_venue": skipped_no_venue, "skipped_no_slot": skipped_no_slot,
        }

    msg = f"{created_count} lab/workshop session(s) scheduled successfully."
    if skipped_no_venue:
        msg += f" No venues configured: {', '.join(skipped_no_venue)}."
    if skipped_no_slot:
        msg += f" No free slot found: {', '.join(skipped_no_slot)}."

    _log(f"[Labs] {msg}")
    return {
        "status": "success", "message": msg, "created_count": created_count,
        "skipped_no_venue": skipped_no_venue, "skipped_no_slot": skipped_no_slot,
    }


# ── HTTP view (manual "Run lab autoscheduler" button) ───────────────────────

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def run_autoscheduler(request):
    result = run_lab_autoscheduling()
    status_code = 400 if result["status"] == "error" else 200
    return JsonResponse(result, status=status_code)