import math
import re
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


def _lecturer_free_main_timetable(lecturer, day, st, et):
    """
    Lecturer not already teaching a regular LECTURE at this slot. Mirrors
    `_main_timetable_free` below but keyed on the lecturer rather than the
    student cohort — a lecturer can't run a lab and a lecture at once even
    when the two involve completely different programs/years. Checks BOTH
    Timetable (published) and TempTimetable (in-progress draft from a
    regular-autoscheduler run chained just before this one), same reasoning
    as `_main_timetable_free`.
    """
    if not lecturer:
        return True
    for model in (Timetable, TempTimetable):
        if model.objects.filter(
            course_allocation__lecturer=lecturer,
            day=day,
            start_time__lt=et,
            end_time__gt=st,
        ).exists():
            return False
    return True


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


# ── SPLIT SECTIONS OF THE SAME COURSE ─────────────────────────────────────────
# Two lab rows both coded "EENG 243" (or "EENG 243-A" / "EENG 243 GROUP B")
# are ONE course split into sections because the cohort is too large for a
# single lab room. Each section teaches a disjoint subset of the same
# programme-year, so running them concurrently in different rooms is correct,
# not a double-booking. Mirrors Rule 0 of `is_scheduling_exempt` /
# `_is_same_base_course_pair` in timetable/timetable_panel.py — reproduced
# here as a local, lightweight copy rather than imported, because that module
# imports lab_panel_ops (which imports this one) and would go circular.

_LAB_COURSE_PREFIX_RE = re.compile(r"^\s*([A-Za-z]+)\s*(\d+)")


def _course_base_key(course_code):
    """Leading LETTER-run + DIGIT-run, section/stream tag discarded:
    'EENG 243-A', 'EENG243 GROUP B', 'EENG 243(ELE)-C' -> 'EENG243'."""
    if not course_code:
        return None
    m = _LAB_COURSE_PREFIX_RE.match(str(course_code))
    if not m:
        return None
    return f"{m.group(1).upper()}{m.group(2)}"


def _lab_base_keys(alloc):
    """Base course keys carried by a LabAllocation (primary + additional)."""
    keys = set()
    for code in (getattr(alloc, "all_course_codes", lambda: "")() or "").replace("/", ",").split(","):
        k = _course_base_key(code)
        if k:
            keys.add(k)
    if not keys:
        k = _course_base_key(getattr(alloc, "course_code", None))
        if k:
            keys.add(k)
    return keys


def _is_same_course_split(keys_a, keys_b):
    """
    True when both sides are the SAME course (by base code) — i.e. split
    sections of one course, which may legitimately run at the same time in
    different rooms. Requires the key sets to match exactly, so a combined
    lab that bundles an extra course is NOT waved through.
    """
    return bool(keys_a) and keys_a == keys_b


# ── COHORT (program + year) identity ──────────────────────────────────────────
# A Program alone is NOT a student cohort: "BSc Biochemistry" spans Years 1-4,
# and a Year-2 lab (BCHM 221) shares no students at all with a Year-3 lecture
# (BCHM 351) or a Year-4 one (BCHM 451E). Matching on program_id alone flagged
# those as student clashes and blocked genuinely-free slots during scheduling.
# The real cohort key is (program_id, year_of_study) — the same key the regular
# timetable's own program-conflict pass uses (`{prog.id}_{year}`).

def _pc_year(pc):
    """Year of study on a ProgramCourse row, as an int, or None if unknown."""
    try:
        y = getattr(pc, "year", None)
        return int(y) if y else None
    except (TypeError, ValueError):
        return None


def _involved_cohorts(alloc):
    """
    (program_id, year) pairs whose students sit in this LabAllocation —
    the primary program_course plus every additional_course (a shared
    elective lab can pull students from several programs/years).
    """
    cohorts = set()
    pc = getattr(alloc, "program_course", None)
    if pc is not None and getattr(pc, "program_id", None):
        cohorts.add((pc.program_id, _pc_year(pc)))
    for extra in alloc.additional_courses.all():
        if getattr(extra, "program_id", None):
            cohorts.add((extra.program_id, _pc_year(extra)))
    return cohorts


def _main_alloc_cohort(course_allocation):
    """(program_id, year) for a regular CourseAllocation row."""
    pc = getattr(course_allocation, "program_course", None)
    pid = getattr(course_allocation, "program_id", None)
    if pid is None and pc is not None:
        pid = getattr(pc, "program_id", None)
    return (pid, _pc_year(pc) if pc is not None else None)


def _as_cohorts(values):
    """
    Normalise a mixed iterable of program ids and/or (program_id, year)
    pairs into cohort tuples, so older callers that still pass a plain set
    of program ids keep working (they just degrade to year-unknown, which
    behaves exactly like the previous program-only matching).
    """
    out = set()
    for v in values or ():
        if isinstance(v, (tuple, list)):
            out.add((v[0], v[1] if len(v) > 1 else None))
        else:
            out.add((v, None))
    return out


def _cohorts_overlap(cohorts_a, cohorts_b):
    """
    True when the two sides genuinely share students.
      • different program            → never overlap
      • same program, same year      → same cohort, overlap
      • same program, year unknown on either side → treated as an overlap
        (fail safe: better to flag a possible clash than miss a real one)
      • same program, different years → DIFFERENT students, no overlap
    """
    for pid_a, year_a in _as_cohorts(cohorts_a):
        if pid_a is None:
            continue
        for pid_b, year_b in _as_cohorts(cohorts_b):
            if pid_a != pid_b:
                continue
            if year_a is None or year_b is None or year_a == year_b:
                return True
    return False


def _program_lab_free(cohorts, day, st, et, exclude_lab_ids=(), base_keys=None):
    """
    None of the involved cohorts (program + year of study) should have a
    DIFFERENT lab course scheduled at the same day+slot — students can't be
    in two labs simultaneously. Candidate rows are narrowed in SQL by
    program, then compared cohort-by-cohort in Python so that a different
    YEAR of the same program is not mistaken for the same students.

    `base_keys` — the base course key(s) of the allocation being placed.
    A candidate carrying exactly the same key(s) is a split SECTION of the
    same course (each section holds a disjoint slice of the cohort), so it
    is skipped rather than treated as a clash.
    """
    cohorts = _as_cohorts(cohorts)
    if not cohorts:
        return True
    pids = {p for p, _ in cohorts if p}
    if not pids:
        return True
    candidates = (
        LabTimetable.objects.filter(
            Q(lab_allocation__program_course__program_id__in=pids)
            | Q(lab_allocation__additional_courses__program_id__in=pids),
            day=day,
            start_time__lt=et,
            end_time__gt=st,
        )
        .exclude(id__in=set(exclude_lab_ids or ()))
        .select_related("lab_allocation", "lab_allocation__program_course")
        .prefetch_related("lab_allocation__additional_courses")
        .distinct()
    )
    for tt in candidates:
        if base_keys and _is_same_course_split(base_keys, _lab_base_keys(tt.lab_allocation)):
            continue  # split section of the same course — allowed to run concurrently
        if _cohorts_overlap(cohorts, _involved_cohorts(tt.lab_allocation)):
            return False
    return True


def _main_timetable_free(cohorts, day, st, et):
    """
    A lab should not clash with a scheduled lecture for any of the
    involved cohorts (program + year) — students can't attend a lecture and
    a lab at the same time, but a Year-2 lab and a Year-3 lecture of the
    same program are different students and don't clash. Checks BOTH:
      • Timetable      — the published/live regular timetable.
      • TempTimetable  — the in-progress draft produced by a regular
        autoscheduler run that hasn't been published yet. This matters
        when the lab autoscheduler is chained immediately after the
        regular autoscheduler in the same "run" — at that point the
        freshly generated lecture slots live in TempTimetable, not yet
        in Timetable, and would otherwise be invisible to this check.
    """
    cohorts = _as_cohorts(cohorts)
    if not cohorts:
        return True
    pids = {p for p, _ in cohorts if p}
    if not pids:
        return True

    for model in (Timetable, TempTimetable):
        rows = model.objects.filter(
            course_allocation__program_id__in=pids,
            day=day,
            start_time__lt=et,
            end_time__gt=st,
        ).select_related("course_allocation", "course_allocation__program_course")
        for tt in rows:
            if _cohorts_overlap(cohorts, {_main_alloc_cohort(tt.course_allocation)}):
                return False
    return True


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
      2. The LECTURER is already teaching another lab at that day+slot, OR
         already teaching a regular LECTURE at that day+slot.
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
        cohorts     = _involved_cohorts(alloc)
        base_keys   = _lab_base_keys(alloc)
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

                # ── 1. Any involved cohort already has a lab at this slot ────
                if not _program_lab_free(cohorts, day, st, et, base_keys=base_keys):
                    continue

                # ── 2. Lecturer already teaching a lab at this slot ───────────
                if not _lecturer_free(lecturer, day, st, et):
                    continue

                # ── 2b. Lecturer already teaching a LECTURE at this slot ──────
                if not _lecturer_free_main_timetable(lecturer, day, st, et):
                    continue

                # ── 3. Any involved cohort has a lecture at this slot ─────────
                if not _main_timetable_free(cohorts, day, st, et):
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