"""
timetable/lab_panel_ops.py
===========================
Lab Timetable support for the main Timetable Panel (/timetable/).

Adds a second table — the Lab Timetable — directly below the regular
weekly timetable on the same page, with:
  • its own scheduled/unscheduled counts,
  • conflict detection cross-checked against BOTH the lab timetable and the
    regular (main) timetable — lecturer double-booked between a lab and a
    lecture, and student/program clashes the same way,
  • a right-click menu mirroring the regular timetable's actions (Simulate
    Move, Swap, Combine, Copy to, Move for Lecturer, Move for Students,
    Update Student Count, Find Courses), adapted to LabAllocation's simpler
    data model (no specialization stems / electives / evening-weekend
    sessions — labs run Mon-Fri only, one block size, one venue pool per
    allocation),
  • a left-panel "Lab" tab with its own Add Entry form + its own Lab
    Scheduler Config form (start/end/slot size).

"Combining different courses into the same lab at the same time" reuses
the existing LabAllocation.additional_courses M2M — LabTimetable already
enforces one row per (lab_venue, day, start_time, end_time), so a combined
session is simply one LabAllocation whose additional_courses lists every
other course sharing that single scheduled row. Combine merges a second
LabAllocation's course(s) into the first and removes the now-redundant row;
Remove/Split pulls a course back out into its own, again-unscheduled
LabAllocation.
"""
import datetime
import json

from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils.dateparse import parse_time
from django.views.decorators.http import require_POST

from core.rbac import allowed_roles, Role
from course_allocation.allocation_scope import apply_tt_scope

from timetable.models import LabTimetable, LabSchedulerConfig, Timetable, TempTimetable
from timetable.algorithms.lab_allocation_autosheduler import (
    _involved_program_ids,
    _generate_slots as _autosched_generate_slots,
)
from course_allocation.models import LabAllocation
from room_management.models import LabVenue
from program_management.models import ProgramCourse

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

ALLOWED = allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)


# ═══════════════════════════════════════════════════════════════════
# CONFIG / SLOTS
# ═══════════════════════════════════════════════════════════════════

def get_lab_config():
    cfg = LabSchedulerConfig.objects.first()
    if not cfg:
        cfg = LabSchedulerConfig.objects.create()
    return cfg


def build_lab_slots(cfg):
    """[(start_str '07:00', label '07:00AM-09:00AM'), ...] for template + JS."""
    slots = []
    start = datetime.datetime.combine(datetime.date.today(), cfg.start_time)
    end = datetime.datetime.combine(datetime.date.today(), cfg.end_time)
    step = datetime.timedelta(hours=int(cfg.slot_size))
    cur = start
    while cur + step <= end:
        nxt = cur + step
        slots.append((cur.strftime("%H:%M"), f"{cur.strftime('%I:%M%p')}-{nxt.strftime('%I:%M%p')}"))
        cur = nxt
    return slots


def time_overlaps(start_a, end_a, start_b, end_b):
    """True if [start_a, end_a) and [start_b, end_b) overlap. Local copy of
    timetable_panel.time_overlaps to avoid a circular import (that module
    imports this one to build the /timetable/ panel context)."""
    return (start_a < end_b) and (start_b < end_a)


def _parse_time_field(value):
    if isinstance(value, datetime.time):
        return value
    t = parse_time(value)
    if not t:
        raise ValueError(f"Invalid time value: {value!r}")
    return t


# ═══════════════════════════════════════════════════════════════════
# CONFLICT DETECTION (lab-vs-lab AND lab-vs-main-timetable)
# ═══════════════════════════════════════════════════════════════════

def _lecturer_hits_main(lecturer_id, day, st, et, exclude_lab_ids=()):
    """Regular lecture rows this lecturer is already teaching at this slot."""
    if not lecturer_id:
        return []
    return list(
        Timetable.objects.filter(
            course_allocation__lecturer_id=lecturer_id,
            day=day, start_time__lt=et, end_time__gt=st,
        ).select_related("course_allocation", "venue")
    )


def _program_hits_main(program_ids, day, st, et):
    if not program_ids:
        return []
    return list(
        Timetable.objects.filter(
            course_allocation__program_id__in=program_ids,
            day=day, start_time__lt=et, end_time__gt=st,
        ).select_related("course_allocation", "venue")
    )


def check_lab_slot_conflicts(alloc, lab_venue, day, st, et, exclude_lab_ids=None):
    """
    Returns a list of human-readable conflict messages for placing `alloc`
    (a LabAllocation) at lab_venue/day/st-et, checking:
      1. Venue already occupied by another lab session.
      2. Lecturer already teaching another lab at this slot.
      3. Lecturer already teaching a REGULAR lecture at this slot.
      4. Any involved program already has a different lab at this slot.
      5. Any involved program already has a REGULAR lecture at this slot.
    `exclude_lab_ids` — LabTimetable ids to ignore (the row being moved).
    """
    exclude_lab_ids = set(exclude_lab_ids or [])
    messages = []

    venue_clash = LabTimetable.objects.filter(
        lab_venue=lab_venue, day=day, start_time__lt=et, end_time__gt=st,
    ).exclude(id__in=exclude_lab_ids).select_related("lab_allocation__program_course").first()
    if venue_clash:
        messages.append(
            f"Venue {lab_venue.code} is already booked for "
            f"{venue_clash.lab_allocation.all_course_codes()} on {day} at that time."
        )

    lecturer = alloc.lecturer
    program_ids = _involved_program_ids(alloc)

    if lecturer:
        lab_clash = LabTimetable.objects.filter(
            lab_allocation__lecturer_id=lecturer.id, day=day,
            start_time__lt=et, end_time__gt=st,
        ).exclude(id__in=exclude_lab_ids).select_related("lab_allocation").first()
        if lab_clash:
            messages.append(
                f"Lecturer {lecturer.display_name} is already teaching "
                f"{lab_clash.lab_allocation.all_course_codes()} (lab) on {day} at that time."
            )
        for tt in _lecturer_hits_main(lecturer.id, day, st, et):
            messages.append(
                f"Lecturer {lecturer.display_name} has a lecture — "
                f"{tt.course_allocation.course_code} in {tt.venue.code} — on {day} at that time."
            )

    if program_ids:
        prog_lab_clash = LabTimetable.objects.filter(
            Q(lab_allocation__program_course__program_id__in=program_ids)
            | Q(lab_allocation__additional_courses__program_id__in=program_ids),
            day=day, start_time__lt=et, end_time__gt=st,
        ).exclude(id__in=exclude_lab_ids).select_related("lab_allocation").first()
        if prog_lab_clash:
            messages.append(
                f"Students in this program already have a lab — "
                f"{prog_lab_clash.lab_allocation.all_course_codes()} — on {day} at that time."
            )
        for tt in _program_hits_main(program_ids, day, st, et):
            messages.append(
                f"Students in this program already have a lecture — "
                f"{tt.course_allocation.course_code} in {tt.venue.code} — on {day} at that time."
            )

    return messages


def compute_lab_conflicts():
    """
    Scans every scheduled LabTimetable row and reports:
      - lecturer conflicts (lab-vs-lab AND lab-vs-main-lecture)
      - student/program conflicts (lab-vs-lab AND lab-vs-main-lecture)
    Returns {'lecturer': [...], 'student': [...], 'flagged_ids': {lab_tt_id,...}}
    """
    labs = list(
        LabTimetable.objects.select_related(
            "lab_allocation", "lab_allocation__program_course",
            "lab_allocation__program_course__program", "lab_allocation__lecturer",
            "lab_venue",
        ).prefetch_related("lab_allocation__additional_courses__program")
    )

    lecturer_conflicts, student_conflicts, flagged = [], [], set()

    def _entry_label(tt):
        return {
            "lab_tt_id": tt.id, "day": tt.day,
            "start": tt.start_time.strftime("%H:%M"), "end": tt.end_time.strftime("%H:%M"),
            "venue": tt.lab_venue.code, "course_code": tt.lab_allocation.all_course_codes(),
        }

    for i, a in enumerate(labs):
        a_alloc = a.lab_allocation
        a_prog_ids = _involved_program_ids(a_alloc)
        a_lecturer = a_alloc.lecturer

        for b in labs[i + 1:]:
            if a.day != b.day or not time_overlaps(a.start_time, a.end_time, b.start_time, b.end_time):
                continue
            b_alloc = b.lab_allocation
            if a_lecturer and b_alloc.lecturer_id == a_lecturer.id:
                lecturer_conflicts.append({
                    "type": "lab_vs_lab", "lecturer": a_lecturer.display_name,
                    "a": _entry_label(a), "b": _entry_label(b),
                    "message": f"{a_lecturer.display_name} is booked in two labs at once on {a.day}.",
                })
                flagged.update([a.id, b.id])
            b_prog_ids = _involved_program_ids(b_alloc)
            shared = a_prog_ids & b_prog_ids
            if shared:
                student_conflicts.append({
                    "type": "lab_vs_lab", "a": _entry_label(a), "b": _entry_label(b),
                    "message": (
                        f"Students in the same program are booked into two labs at once on "
                        f"{a.day} ({a_alloc.all_course_codes()} vs {b_alloc.all_course_codes()})."
                    ),
                })
                flagged.update([a.id, b.id])

        if a_lecturer:
            for tt in _lecturer_hits_main(a_lecturer.id, a.day, a.start_time, a.end_time):
                lecturer_conflicts.append({
                    "type": "lab_vs_main", "lecturer": a_lecturer.display_name,
                    "lab": _entry_label(a),
                    "main": {
                        "tt_id": tt.id, "day": tt.day,
                        "start": tt.start_time.strftime("%H:%M"), "end": tt.end_time.strftime("%H:%M"),
                        "venue": tt.venue.code, "course_code": tt.course_allocation.course_code,
                    },
                    "message": (
                        f"{a_lecturer.display_name} is teaching {tt.course_allocation.course_code} "
                        f"in the main timetable while also assigned to lab "
                        f"{a_alloc.all_course_codes()} on {a.day} at the same time."
                    ),
                })
                flagged.add(a.id)

        if a_prog_ids:
            for tt in _program_hits_main(a_prog_ids, a.day, a.start_time, a.end_time):
                student_conflicts.append({
                    "type": "lab_vs_main", "lab": _entry_label(a),
                    "main": {
                        "tt_id": tt.id, "day": tt.day,
                        "start": tt.start_time.strftime("%H:%M"), "end": tt.end_time.strftime("%H:%M"),
                        "venue": tt.venue.code, "course_code": tt.course_allocation.course_code,
                    },
                    "message": (
                        f"Students due in lab {a_alloc.all_course_codes()} on {a.day} also have "
                        f"a lecture ({tt.course_allocation.course_code}) at the same time."
                    ),
                })
                flagged.add(a.id)

    return {"lecturer": lecturer_conflicts, "student": student_conflicts, "flagged_ids": flagged}


# ═══════════════════════════════════════════════════════════════════
# PANEL DATA — counts, table rows, config, form dropdowns
# ═══════════════════════════════════════════════════════════════════

def get_lab_panel_context(request=None):
    cfg = get_lab_config()
    slots = build_lab_slots(cfg)
    venues = list(LabVenue.objects.all().order_by("code"))

    scheduled_alloc_ids = set(LabTimetable.objects.values_list("lab_allocation_id", flat=True).distinct())
    # The pool of allocations feeding the "unscheduled" dropdown/counts is
    # scoped to whichever AllocationSet(s) the TT switched on at
    # /timetable/dashboard/ (course_allocation.allocation_scope) — mirrors
    # how the regular timetable's own unscheduled-allocation helpers behave.
    # Already-scheduled LabTimetable rows are deliberately left unscoped
    # below (same as the regular grid): once something's on the grid it
    # stays visible/checked for conflicts regardless of which set is active.
    all_allocations_qs = apply_tt_scope(
        LabAllocation.objects.select_related("program_course", "lecturer")
        .prefetch_related("venues", "additional_courses"),
        request=request,
    ).order_by("program_course__course_code")
    all_allocations = list(all_allocations_qs)
    unscheduled_allocations = [a for a in all_allocations if a.id not in scheduled_alloc_ids]

    return {
        "lab_config": cfg,
        "lab_default_start": cfg.start_time.strftime("%H:%M"),
        "lab_default_end": cfg.end_time.strftime("%H:%M"),
        "lab_default_slot": cfg.slot_size,
        "lab_time_slots": slots,
        "lab_venues": venues,
        "lab_weekdays": WEEKDAYS,
        "lab_all_allocations": all_allocations,
        "lab_unscheduled_allocations": unscheduled_allocations,
        "lab_scheduled_count": len(scheduled_alloc_ids),
        "lab_unscheduled_count": len(unscheduled_allocations),
    }


@ALLOWED
def load_lab_timetable_data(request):
    """AJAX: full lab timetable payload for building the table + conflict panel."""
    labs = LabTimetable.objects.select_related(
        "lab_allocation", "lab_allocation__program_course", "lab_allocation__lecturer", "lab_venue",
    ).prefetch_related("lab_allocation__additional_courses")

    entries = []
    for tt in labs:
        alloc = tt.lab_allocation
        additional = [
            {"id": pc.id, "course_code": pc.course_code}
            for pc in alloc.additional_courses.all()
        ]
        entries.append({
            "id": tt.id,
            "day": tt.day,
            "start": tt.start_time.strftime("%H:%M"),
            "end": tt.end_time.strftime("%H:%M"),
            "venue": tt.lab_venue.code,
            "allocation_id": alloc.id,
            "course_code": alloc.all_course_codes(),
            "is_combined": bool(additional),
            "additional_courses": additional,
            "is_workshop": alloc.is_workshop_course,
            "students": alloc.number_of_students or 0,
            "lecturer": getattr(alloc.lecturer, "display_name", "Unassigned"),
            "lecturer_id": alloc.lecturer_id,
        })

    conflicts = compute_lab_conflicts()
    ctx = get_lab_panel_context(request)

    return JsonResponse({
        "status": "success",
        "entries": entries,
        "conflicts": {
            "lecturer": conflicts["lecturer"],
            "student": conflicts["student"],
            "flagged_ids": list(conflicts["flagged_ids"]),
            "total": len(conflicts["lecturer"]) + len(conflicts["student"]),
        },
        "stats": {
            "scheduled_count": ctx["lab_scheduled_count"],
            "unscheduled_count": ctx["lab_unscheduled_count"],
        },
        "unscheduled_allocations": [
            {
                "id": a.id, "course_code": a.all_course_codes(),
                "lecturer": getattr(a.lecturer, "display_name", "Unassigned"),
                "students": a.number_of_students or 0,
            }
            for a in ctx["lab_unscheduled_allocations"]
        ],
    })


# ═══════════════════════════════════════════════════════════════════
# CREATE / UPDATE / DELETE
# ═══════════════════════════════════════════════════════════════════

@ALLOWED
@require_POST
def save_lab_entry(request):
    """Create or move-into-place a lab entry from the left 'Lab' form."""
    data = request.POST
    allocation_id = data.get("allocation_id")
    venue_code = (data.get("venue") or "").strip()
    day = data.get("day")
    slot = data.get("slot")  # "HH:MM"
    force = (data.get("force_override_conflicts") or "").lower() == "true"

    if not (allocation_id and venue_code and day and slot):
        return JsonResponse({"status": "error", "messages": ["All fields are required."]}, status=400)

    cfg = get_lab_config()
    try:
        st = _parse_time_field(slot)
        end_dt = datetime.datetime.combine(datetime.date.today(), st) + datetime.timedelta(hours=int(cfg.slot_size))
        et = end_dt.time()
    except ValueError:
        return JsonResponse({"status": "error", "messages": ["Invalid time slot."]}, status=400)

    try:
        alloc = LabAllocation.objects.select_related("lecturer").get(id=allocation_id)
    except LabAllocation.DoesNotExist:
        return JsonResponse({"status": "error", "messages": ["Lab allocation not found."]}, status=404)

    try:
        lab_venue = LabVenue.objects.get(code=venue_code)
    except LabVenue.DoesNotExist:
        return JsonResponse({"status": "error", "messages": [f"Lab venue '{venue_code}' not found."]}, status=404)

    exclude_ids = list(LabTimetable.objects.filter(lab_allocation=alloc).values_list("id", flat=True))
    messages_list = check_lab_slot_conflicts(alloc, lab_venue, day, st, et, exclude_lab_ids=exclude_ids)
    if messages_list and not force:
        return JsonResponse({"status": "conflict", "messages": messages_list}, status=409)

    with transaction.atomic():
        LabTimetable.objects.filter(lab_allocation=alloc).delete()
        LabTimetable.objects.create(lab_allocation=alloc, lab_venue=lab_venue, day=day, start_time=st, end_time=et)

    return JsonResponse({"status": "success", "messages": messages_list + [
        f"Scheduled {alloc.all_course_codes()} in {lab_venue.code} on {day} {slot}."
    ]})


@ALLOWED
@require_POST
def delete_lab_entry(request):
    lab_tt_id = request.POST.get("id") or request.POST.get("lab_tt_id")
    if not lab_tt_id:
        return JsonResponse({"status": "error", "messages": ["Missing id."]}, status=400)
    deleted, _ = LabTimetable.objects.filter(id=lab_tt_id).delete()
    return JsonResponse({"status": "success" if deleted else "not_found"})


@ALLOWED
@require_POST
def update_lab_student_count_api(request):
    lab_tt_id = request.POST.get("lab_tt_id")
    students = request.POST.get("students")
    try:
        students = int(students)
        if students < 0:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "messages": ["Enter a valid, non-negative student count."]}, status=400)

    tt = LabTimetable.objects.filter(id=lab_tt_id).select_related("lab_allocation").first()
    if not tt:
        return JsonResponse({"status": "error", "messages": ["Lab entry not found."]}, status=404)

    tt.lab_allocation.number_of_students = students
    tt.lab_allocation.save(update_fields=["number_of_students"])
    return JsonResponse({"status": "success", "messages": [f"Student count updated to {students}."]})


# ═══════════════════════════════════════════════════════════════════
# SIMULATE / EXECUTE MOVE
# ═══════════════════════════════════════════════════════════════════

def _load_move_target(request):
    lab_tt_id = request.POST.get("lab_tt_id")
    day = request.POST.get("day")
    venue_code = (request.POST.get("venue") or "").strip()
    start_s = request.POST.get("start")
    end_s = request.POST.get("end")
    if not (lab_tt_id and day and venue_code and start_s and end_s):
        return None, JsonResponse({"status": "error", "messages": ["All fields are required."]}, status=400)
    try:
        st = _parse_time_field(start_s)
        et = _parse_time_field(end_s)
    except ValueError:
        return None, JsonResponse({"status": "error", "messages": ["Invalid time format."]}, status=400)
    return (lab_tt_id, day, venue_code, st, et), None


@ALLOWED
@require_POST
def simulate_lab_move_api(request):
    parsed, err = _load_move_target(request)
    if err:
        return err
    lab_tt_id, day, venue_code, st, et = parsed

    tt = LabTimetable.objects.filter(id=lab_tt_id).select_related("lab_allocation", "lab_venue").first()
    if not tt:
        return JsonResponse({"status": "error", "messages": ["Lab entry not found."]}, status=404)

    try:
        lab_venue = LabVenue.objects.get(code=venue_code)
    except LabVenue.DoesNotExist:
        return JsonResponse({"status": "error", "messages": [f"Lab venue '{venue_code}' not found."]}, status=404)

    same_slot = (tt.day == day and tt.start_time == st and tt.end_time == et and tt.lab_venue.code == lab_venue.code)
    messages_list = check_lab_slot_conflicts(tt.lab_allocation, lab_venue, day, st, et, exclude_lab_ids=[tt.id])

    return JsonResponse({
        "status": "conflict" if messages_list else ("same_slot" if same_slot else "ok"),
        "messages": messages_list,
        "course_code": tt.lab_allocation.all_course_codes(),
        "lecturer": getattr(tt.lab_allocation.lecturer, "display_name", "Unassigned"),
    })


@ALLOWED
@require_POST
def execute_lab_move_api(request):
    parsed, err = _load_move_target(request)
    if err:
        return err
    lab_tt_id, day, venue_code, st, et = parsed
    force = (request.POST.get("force") or "").lower() == "true"

    with transaction.atomic():
        tt = LabTimetable.objects.select_for_update().filter(id=lab_tt_id).select_related("lab_allocation").first()
        if not tt:
            return JsonResponse({"status": "error", "messages": ["Lab entry not found."]}, status=404)
        try:
            lab_venue = LabVenue.objects.get(code=venue_code)
        except LabVenue.DoesNotExist:
            return JsonResponse({"status": "error", "messages": [f"Lab venue '{venue_code}' not found."]}, status=404)

        messages_list = check_lab_slot_conflicts(tt.lab_allocation, lab_venue, day, st, et, exclude_lab_ids=[tt.id])
        if messages_list and not force:
            return JsonResponse({"status": "error", "messages": messages_list}, status=409)

        tt.day, tt.start_time, tt.end_time, tt.lab_venue = day, st, et, lab_venue
        tt.save(update_fields=["day", "start_time", "end_time", "lab_venue"])

    return JsonResponse({
        "status": "success",
        "messages": (messages_list or []) + [f"Moved to {day} {st.strftime('%H:%M')}-{et.strftime('%H:%M')} @ {lab_venue.code}."],
    })


# ═══════════════════════════════════════════════════════════════════
# SWAP — exchange two lab sessions' day/slot/venue
# ═══════════════════════════════════════════════════════════════════

@ALLOWED
@require_POST
def simulate_lab_swap_api(request):
    a_id, b_id = request.POST.get("lab_tt_id"), request.POST.get("other_lab_tt_id")
    a = LabTimetable.objects.filter(id=a_id).select_related("lab_allocation", "lab_venue").first()
    b = LabTimetable.objects.filter(id=b_id).select_related("lab_allocation", "lab_venue").first()
    if not a or not b:
        return JsonResponse({"status": "error", "messages": ["One or both lab entries not found."]}, status=404)

    msgs_a = check_lab_slot_conflicts(a.lab_allocation, b.lab_venue, b.day, b.start_time, b.end_time, exclude_lab_ids=[a.id, b.id])
    msgs_b = check_lab_slot_conflicts(b.lab_allocation, a.lab_venue, a.day, a.start_time, a.end_time, exclude_lab_ids=[a.id, b.id])
    messages_list = msgs_a + msgs_b
    return JsonResponse({
        "status": "conflict" if messages_list else "ok",
        "messages": messages_list,
        "a_course": a.lab_allocation.all_course_codes(),
        "b_course": b.lab_allocation.all_course_codes(),
    })


@ALLOWED
@require_POST
def execute_lab_swap_api(request):
    a_id, b_id = request.POST.get("lab_tt_id"), request.POST.get("other_lab_tt_id")
    force = (request.POST.get("force") or "").lower() == "true"

    with transaction.atomic():
        a = LabTimetable.objects.select_for_update().filter(id=a_id).select_related("lab_allocation", "lab_venue").first()
        b = LabTimetable.objects.select_for_update().filter(id=b_id).select_related("lab_allocation", "lab_venue").first()
        if not a or not b:
            return JsonResponse({"status": "error", "messages": ["One or both lab entries not found."]}, status=404)

        msgs_a = check_lab_slot_conflicts(a.lab_allocation, b.lab_venue, b.day, b.start_time, b.end_time, exclude_lab_ids=[a.id, b.id])
        msgs_b = check_lab_slot_conflicts(b.lab_allocation, a.lab_venue, a.day, a.start_time, a.end_time, exclude_lab_ids=[a.id, b.id])
        messages_list = msgs_a + msgs_b
        if messages_list and not force:
            return JsonResponse({"status": "error", "messages": messages_list}, status=409)

        (a_day, a_st, a_et, a_venue) = (a.day, a.start_time, a.end_time, a.lab_venue)
        (b_day, b_st, b_et, b_venue) = (b.day, b.start_time, b.end_time, b.lab_venue)
        b_alloc = b.lab_allocation

        # Free b's slot first — updating both rows in place can transiently
        # collide with the unique_together(lab_venue, day, start, end)
        # constraint (a moving into b's still-occupied old slot).
        b.delete()
        a.day, a.start_time, a.end_time, a.lab_venue = b_day, b_st, b_et, b_venue
        a.save(update_fields=["day", "start_time", "end_time", "lab_venue"])
        b = LabTimetable.objects.create(
            lab_allocation=b_alloc, lab_venue=a_venue, day=a_day, start_time=a_st, end_time=a_et,
        )

    return JsonResponse({"status": "success", "messages": messages_list + ["Swap complete."]})


# ═══════════════════════════════════════════════════════════════════
# COPY — duplicate a session to another day/slot/venue (split groups)
# ═══════════════════════════════════════════════════════════════════

@ALLOWED
@require_POST
def simulate_lab_copy_api(request):
    parsed, err = _load_move_target(request)
    if err:
        return err
    lab_tt_id, day, venue_code, st, et = parsed
    tt = LabTimetable.objects.filter(id=lab_tt_id).select_related("lab_allocation").first()
    if not tt:
        return JsonResponse({"status": "error", "messages": ["Lab entry not found."]}, status=404)
    try:
        lab_venue = LabVenue.objects.get(code=venue_code)
    except LabVenue.DoesNotExist:
        return JsonResponse({"status": "error", "messages": [f"Lab venue '{venue_code}' not found."]}, status=404)

    # Exclude only the venue-clash check against itself is irrelevant here —
    # copy is a NEW row, so no exclude_lab_ids for the allocation-level checks.
    messages_list = check_lab_slot_conflicts(tt.lab_allocation, lab_venue, day, st, et)
    return JsonResponse({"status": "conflict" if messages_list else "ok", "messages": messages_list})


@ALLOWED
@require_POST
def execute_lab_copy_api(request):
    parsed, err = _load_move_target(request)
    if err:
        return err
    lab_tt_id, day, venue_code, st, et = parsed
    force = (request.POST.get("force") or "").lower() == "true"

    with transaction.atomic():
        tt = LabTimetable.objects.select_related("lab_allocation").filter(id=lab_tt_id).first()
        if not tt:
            return JsonResponse({"status": "error", "messages": ["Lab entry not found."]}, status=404)
        try:
            lab_venue = LabVenue.objects.get(code=venue_code)
        except LabVenue.DoesNotExist:
            return JsonResponse({"status": "error", "messages": [f"Lab venue '{venue_code}' not found."]}, status=404)

        messages_list = check_lab_slot_conflicts(tt.lab_allocation, lab_venue, day, st, et)
        if messages_list and not force:
            return JsonResponse({"status": "error", "messages": messages_list}, status=409)

        new_tt = LabTimetable.objects.create(
            lab_allocation=tt.lab_allocation, lab_venue=lab_venue, day=day, start_time=st, end_time=et,
        )

    return JsonResponse({
        "status": "success", "new_id": new_tt.id,
        "messages": messages_list + [f"Copied {tt.lab_allocation.all_course_codes()} to {day} {st.strftime('%H:%M')} @ {lab_venue.code}."],
    })


# ═══════════════════════════════════════════════════════════════════
# COMBINE — merge a second lab session's course(s) into this one
# (uses LabAllocation.additional_courses; the redundant row is dropped)
# ═══════════════════════════════════════════════════════════════════

@ALLOWED
@require_POST
def simulate_lab_combine_api(request):
    a_id, b_id = request.POST.get("lab_tt_id"), request.POST.get("other_lab_tt_id")
    a = LabTimetable.objects.filter(id=a_id).select_related("lab_allocation", "lab_venue").first()
    b = LabTimetable.objects.filter(id=b_id).select_related("lab_allocation", "lab_venue").first()
    if not a or not b:
        return JsonResponse({"status": "error", "messages": ["One or both lab entries not found."]}, status=404)
    if a.lab_allocation_id == b.lab_allocation_id:
        return JsonResponse({"status": "error", "messages": ["Those two sessions are already the same allocation."]}, status=400)

    total_students = (a.lab_allocation.number_of_students or 0) + (b.lab_allocation.number_of_students or 0)
    capacity = a.lab_venue.capacity
    warn = []
    if capacity and total_students > capacity:
        warn.append(
            f"Combined student count ({total_students}) exceeds {a.lab_venue.code}'s capacity ({capacity})."
        )

    return JsonResponse({
        "status": "warning" if warn else "ok",
        "messages": warn,
        "into_course": a.lab_allocation.all_course_codes(),
        "moving_course": b.lab_allocation.all_course_codes(),
        "combined_students": total_students,
        "venue": a.lab_venue.code, "day": a.day,
        "start": a.start_time.strftime("%H:%M"), "end": a.end_time.strftime("%H:%M"),
    })


@ALLOWED
@require_POST
def execute_lab_combine_api(request):
    """Merge `other_lab_tt_id`'s course(s) into `lab_tt_id`'s LabAllocation,
    then delete the now-redundant LabTimetable row (and empty LabAllocation)."""
    a_id, b_id = request.POST.get("lab_tt_id"), request.POST.get("other_lab_tt_id")
    force = (request.POST.get("force") or "").lower() == "true"

    with transaction.atomic():
        a = LabTimetable.objects.select_for_update().filter(id=a_id).select_related("lab_allocation", "lab_venue").first()
        b = LabTimetable.objects.select_for_update().filter(id=b_id).select_related("lab_allocation", "lab_venue").first()
        if not a or not b:
            return JsonResponse({"status": "error", "messages": ["One or both lab entries not found."]}, status=404)
        if a.lab_allocation_id == b.lab_allocation_id:
            return JsonResponse({"status": "error", "messages": ["Those two sessions are already the same allocation."]}, status=400)

        a_alloc, b_alloc = a.lab_allocation, b.lab_allocation
        total_students = (a_alloc.number_of_students or 0) + (b_alloc.number_of_students or 0)
        if a.lab_venue.capacity and total_students > a.lab_venue.capacity and not force:
            return JsonResponse({
                "status": "error",
                "messages": [f"Combined student count ({total_students}) exceeds {a.lab_venue.code}'s capacity ({a.lab_venue.capacity})."],
            }, status=409)

        # Fold b's primary course + b's own additional_courses into a.
        a_alloc.additional_courses.add(b_alloc.program_course_id)
        for pc_id in b_alloc.additional_courses.values_list("id", flat=True):
            a_alloc.additional_courses.add(pc_id)
        a_alloc.number_of_students = total_students
        a_alloc.save(update_fields=["number_of_students"])

        merged_code = b_alloc.all_course_codes()
        b.delete()
        # b_alloc is now unused by any timetable row and has no course of its
        # own left to represent — remove it rather than leave an orphan.
        b_alloc.delete()

    return JsonResponse({
        "status": "success",
        "messages": [f"Combined {merged_code} into {a_alloc.all_course_codes()} in {a.lab_venue.code} on {a.day}."],
        "combined_course_code": a_alloc.all_course_codes(),
    })


@ALLOWED
@require_POST
def remove_from_lab_combine_api(request):
    """Split one course back out of a combined lab session into its own,
    unscheduled LabAllocation."""
    lab_tt_id = request.POST.get("lab_tt_id")
    program_course_id = request.POST.get("program_course_id")

    with transaction.atomic():
        tt = LabTimetable.objects.select_for_update().filter(id=lab_tt_id).select_related("lab_allocation").first()
        if not tt:
            return JsonResponse({"status": "error", "messages": ["Lab entry not found."]}, status=404)
        alloc = tt.lab_allocation

        if str(alloc.program_course_id) == str(program_course_id):
            return JsonResponse({"status": "error", "messages": ["Can't remove the primary course — remove/move the whole session instead, or split out one of the additional courses."]}, status=400)

        if not alloc.additional_courses.filter(id=program_course_id).exists():
            return JsonResponse({"status": "error", "messages": ["That course is not part of this combined session."]}, status=400)

        try:
            pc = ProgramCourse.objects.get(id=program_course_id)
        except ProgramCourse.DoesNotExist:
            return JsonResponse({"status": "error", "messages": ["Course not found."]}, status=404)
        alloc.additional_courses.remove(pc)

        new_alloc = LabAllocation.objects.create(
            program_course=pc, allocation_set=alloc.allocation_set, lecturer=alloc.lecturer,
            number_of_students=0, is_workshop_course=alloc.is_workshop_course,
        )
        new_alloc.venues.set(alloc.venues.all())

    return JsonResponse({
        "status": "success",
        "messages": [f"Split {pc.course_code} out of {alloc.all_course_codes()} — it is now unscheduled."],
    })


# ═══════════════════════════════════════════════════════════════════
# BULK MOVE — "Move for this Lecturer…" / "Move for these Students…"
# ═══════════════════════════════════════════════════════════════════

def _next_free_slot_for(alloc, exclude_lab_ids, cfg, prefer_venues):
    slots = _autosched_generate_slots(cfg)
    for day in WEEKDAYS:
        for st, et in slots:
            for venue in prefer_venues:
                if not check_lab_slot_conflicts(alloc, venue, day, st, et, exclude_lab_ids=exclude_lab_ids):
                    return day, st, et, venue
    return None


@ALLOWED
@require_POST
def lab_bulk_move_api(request):
    """
    scope = 'lecturer' | 'program'
    scope_id = lecturer id or program id
    Moves every affected, currently-scheduled lab session to the next slot
    that is free for it (best-effort auto-placement — no combined-scope
    picker UI, unlike the regular timetable's bulk move).
    """
    scope = request.POST.get("scope")
    scope_id = request.POST.get("scope_id")
    if scope not in ("lecturer", "program") or not scope_id:
        return JsonResponse({"status": "error", "messages": ["scope and scope_id are required."]}, status=400)

    cfg = get_lab_config()
    if scope == "lecturer":
        qs = LabTimetable.objects.filter(lab_allocation__lecturer_id=scope_id)
    else:
        qs = LabTimetable.objects.filter(
            Q(lab_allocation__program_course__program_id=scope_id)
            | Q(lab_allocation__additional_courses__program_id=scope_id)
        ).distinct()

    moved, failed = [], []
    with transaction.atomic():
        for tt in list(qs.select_related("lab_allocation").prefetch_related("lab_allocation__venues")):
            alloc = tt.lab_allocation
            prefer_venues = list(alloc.venues.all()) or list(LabVenue.objects.all())
            result = _next_free_slot_for(alloc, exclude_lab_ids=[tt.id], cfg=cfg, prefer_venues=prefer_venues)
            if not result:
                failed.append(alloc.all_course_codes())
                continue
            day, st, et, venue = result
            tt.day, tt.start_time, tt.end_time, tt.lab_venue = day, st, et, venue
            tt.save(update_fields=["day", "start_time", "end_time", "lab_venue"])
            moved.append(f"{alloc.all_course_codes()} → {day} {st.strftime('%H:%M')} @ {venue.code}")

    msg = f"Moved {len(moved)} lab session(s)."
    if failed:
        msg += f" Could not find a free slot for: {', '.join(failed)}."
    return JsonResponse({"status": "success" if moved else "warning", "messages": [msg] + moved})


# ═══════════════════════════════════════════════════════════════════
# FIND LAB COURSES — search dialog
# ═══════════════════════════════════════════════════════════════════

@ALLOWED
def lab_find_filter_options_api(request):
    from lecturer_portal.models import Lecturer
    from program_management.models import Program

    lecturers = list(
        Lecturer.objects.filter(laballocation__isnull=False).distinct()
        .order_by("name").values("id", "name")
    )
    programs = list(
        Program.objects.filter(
            Q(courses__lab_allocations__isnull=False) | Q(courses__additional_lab_allocations__isnull=False)
        ).distinct().order_by("name").values("id", "name")
    )
    lab_venues = list(LabVenue.objects.order_by("code").values("id", "code"))
    return JsonResponse({"status": "success", "lecturers": lecturers, "programs": programs, "lab_venues": lab_venues})


@ALLOWED
def lab_find_courses_api(request):
    q = (request.GET.get("q") or "").strip()
    lecturer_id = request.GET.get("lecturer_id") or None
    program_id = request.GET.get("program_id") or None
    lab_venue_id = request.GET.get("lab_venue_id") or None

    base_qs = LabAllocation.objects.select_related("program_course", "lecturer").prefetch_related("additional_courses")

    if q:
        base_qs = base_qs.filter(
            Q(program_course__course_code__icontains=q) | Q(program_course__course_name__icontains=q)
            | Q(additional_courses__course_code__icontains=q)
        )
    if lecturer_id:
        base_qs = base_qs.filter(lecturer_id=lecturer_id)
    if program_id:
        base_qs = base_qs.filter(Q(program_course__program_id=program_id) | Q(additional_courses__program_id=program_id))

    base_qs = base_qs.distinct()

    scheduled_map = {
        tt.lab_allocation_id: tt
        for tt in LabTimetable.objects.filter(lab_allocation__in=base_qs).select_related("lab_venue")
    }
    # Already-scheduled sessions stay findable no matter which AllocationSet
    # is active (they're real, live grid entries) — only the UNSCHEDULED
    # pool is scoped to the TT's active AllocationSet(s), same rule as the
    # rest of the panel (course_allocation.allocation_scope).
    scheduled_qs = base_qs.filter(id__in=scheduled_map.keys())
    unscheduled_qs = apply_tt_scope(base_qs.exclude(id__in=scheduled_map.keys()), request=request)
    qs = sorted(
        list(scheduled_qs) + list(unscheduled_qs),
        key=lambda a: a.program_course.course_code,
    )[:60]

    if lab_venue_id:
        qs = [a for a in qs if scheduled_map.get(a.id) and str(scheduled_map[a.id].lab_venue_id) == str(lab_venue_id)]

    results = []
    for alloc in qs:
        tt = scheduled_map.get(alloc.id)
        results.append({
            "id": alloc.id,
            "course_code": alloc.all_course_codes(),
            "lecturer": getattr(alloc.lecturer, "display_name", "Unassigned"),
            "lecturer_id": alloc.lecturer_id,
            "students": alloc.number_of_students or 0,
            "is_workshop": alloc.is_workshop_course,
            "scheduled": (
                {"lab_tt_id": tt.id, "day": tt.day, "start": tt.start_time.strftime("%H:%M"),
                 "end": tt.end_time.strftime("%H:%M"), "venue": tt.lab_venue.code}
                if tt else None
            ),
        })
    return JsonResponse({"status": "success", "count": len(results), "courses": results})


# ═══════════════════════════════════════════════════════════════════
# LAB SCHEDULER CONFIG (left-panel "Lab" tab config form)
# ═══════════════════════════════════════════════════════════════════

@ALLOWED
@require_POST
def update_lab_scheduler_config_api(request):
    cfg = get_lab_config()
    start_time = request.POST.get("start_time")
    end_time = request.POST.get("end_time")
    slot_size = request.POST.get("slot_size")
    try:
        if start_time:
            cfg.start_time = datetime.datetime.strptime(start_time, "%H:%M").time()
        if end_time:
            cfg.end_time = datetime.datetime.strptime(end_time, "%H:%M").time()
        if slot_size:
            cfg.slot_size = int(slot_size)
        cfg.save()
    except (ValueError, TypeError) as e:
        return JsonResponse({"status": "error", "messages": [f"Invalid input: {e}"]}, status=400)
    return JsonResponse({"status": "success", "messages": ["Lab scheduler configuration updated."]})
