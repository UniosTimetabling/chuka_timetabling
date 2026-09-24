#!/usr/bin/env python3
"""
desktop_app/python-runner/lab_exam_scheduler.py
================================================
A line-for-line-equivalent, dependency-free port of
timetable/algorithms/run_lab_exam_autoscheduler.py's scheduling RULES, built
to run standalone (no Django, no database) inside the Electron app.

Contract
--------
Reads one JSON object from stdin, shaped like desktop_sync/views_reference.py's
GET /api/desktop/reference/lab-exam/ response (see
desktop_app/src/shared/types.ts: LabExamReferenceData) — that's exactly what
db.getLabExamReference() returns on the desktop side, so the same payload the
server generates is fed straight in.

Writes one JSON object to stdout:
    {
      "status": "success" | "warning" | "error",
      "message": str,
      "schedule": [
        {"lab_allocation_id": int, "lab_venue_id": int, "date": "YYYY-MM-DD",
         "day": str, "start_time": "HH:MM", "end_time": "HH:MM"}
      ]
    }
`schedule` is the FULL regenerated lab-exam timetable — the caller (Electron's
pythonRunner.ts) is responsible for wiping the local LabExamTimetable mirror
and replacing it with this list, exactly like the original view wipes and
regenerates LabExamTimetable server-side.

An optional `"disable_shuffle": true` in the input skips the random.shuffle()
the original algorithm applies before placing allocations. Production callers
should never set this (it changes placement order, so results would no
longer match the server's random draw) — it exists solely so a parity test
can compare this port's placement LOGIC against the server algorithm without
the confound of the two processes' random module states diverging.

Kept intentionally identical to the server algorithm's rules:
  1. A slot is rejected if the allocation's PROGRAM already has a lab or
     regular exam at that slot (program_free).
  2. A slot is rejected if the LECTURER is already supervising a lab or
     regular exam at that slot (lecturer_free).
  3. A slot is rejected only when ALL candidate VENUES for the allocation are
     occupied by another LAB exam at that slot (venue_free / pick_venues).
Regular-exam clashes come from `existing_exam_busy` (resolved server-side with
real lecturer/program IDs — see views_reference.py) since the desktop's
regular-exam mirror only stores lecturer/program as display names.
"""
import datetime
import json
import math
import random
import sys


def _lookahead_days(max_exam_days: int) -> int:
    return int(max_exam_days * (7 / 5)) + 60


def _parse_date(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d").date()


def _parse_hm(s):
    """'HH:MM' -> minutes since midnight, for cheap overlap comparisons."""
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _fmt_hm(total_minutes):
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def get_valid_dates(cfg):
    """Same walk-forward as ExamSchedulerConfig.get_excluded_date_range()."""
    if not cfg.get("start_date"):
        return []
    start = _parse_date(cfg["start_date"])
    excluded = set(cfg.get("excluded_days") or [])
    max_days = cfg.get("max_exam_days") or 14
    cap = _lookahead_days(max_days)

    valid = []
    i = 0
    while len(valid) < max_days:
        current = start + datetime.timedelta(days=i)
        date_str = current.strftime("%Y-%m-%d")
        if date_str not in excluded:
            valid.append((date_str, current.strftime("%A")))
        i += 1
        if i > cap:
            break
    return valid


def generate_time_slots(cfg):
    if not cfg.get("start_time") or not cfg.get("end_time"):
        return []
    start_m = _parse_hm(cfg["start_time"])
    end_m = _parse_hm(cfg["end_time"])
    try:
        step = int(cfg.get("slot_size") or 2) * 60
    except (TypeError, ValueError):
        step = 120
    if step <= 0:
        return []

    slots = []
    cur = start_m
    while cur + step <= end_m:
        slots.append((cur, cur + step))
        cur += step
    return slots


def _overlaps(st, et, busy_st, busy_et):
    return st < busy_et and busy_st < et


def venue_free(venue_id, date, st, et, assigned_by_venue):
    for (b_date, b_st, b_et) in assigned_by_venue.get(venue_id, []):
        if b_date == date and _overlaps(st, et, b_st, b_et):
            return False
    return True


def lecturer_free(lecturer_id, date, st, et, assigned_by_lecturer, exam_busy_by_lecturer):
    if lecturer_id is None:
        return True
    for (b_date, b_st, b_et) in assigned_by_lecturer.get(lecturer_id, []):
        if b_date == date and _overlaps(st, et, b_st, b_et):
            return False
    for (b_date, b_st, b_et) in exam_busy_by_lecturer.get(lecturer_id, []):
        if b_date == date and _overlaps(st, et, b_st, b_et):
            return False
    return True


def program_free(program_id, date, st, et, assigned_by_program, exam_busy_by_program):
    if program_id is None:
        return True
    for (b_date, b_st, b_et) in assigned_by_program.get(program_id, []):
        if b_date == date and _overlaps(st, et, b_st, b_et):
            return False
    for (b_date, b_st, b_et) in exam_busy_by_program.get(program_id, []):
        if b_date == date and _overlaps(st, et, b_st, b_et):
            return False
    return True


def pick_venues(all_venues, students, date, st, et, assigned_by_venue):
    """Same 3-step strategy as the original: perfect/sufficient fit, then
    tolerable overflow (<=10), then split across multiple free venues."""
    free = [v for v in all_venues if venue_free(v["id"], date, st, et, assigned_by_venue)]
    if not free:
        return []

    free_desc = sorted(free, key=lambda v: v["capacity"] or 0, reverse=True)
    largest = free_desc[0]["capacity"] or 0

    for v in free_desc:
        if (v["capacity"] or 0) >= students:
            return [v]

    for v in free_desc:
        if students - (v["capacity"] or 0) <= 10:
            return [v]

    if largest == 0:
        return [free_desc[0]]

    num_groups = math.ceil(students / largest)
    group_size = math.ceil(students / num_groups)

    chosen = [v for v in free_desc if (v["capacity"] or 0) >= group_size]
    if len(chosen) >= num_groups:
        return chosen[:num_groups]

    return free_desc[: min(num_groups, len(free_desc))]


def run(data: dict) -> dict:
    cfg = data.get("config") or {}
    allocations = list(data.get("lab_allocations") or [])
    venues_by_id = {v["id"]: v for v in (data.get("lab_venues") or [])}

    time_slots = generate_time_slots(cfg)
    valid_dates = get_valid_dates(cfg)

    if not time_slots:
        return {"status": "error", "message": "No time slots generated — check start/end time and slot size.", "schedule": []}
    if not valid_dates:
        return {"status": "error", "message": "No valid exam dates — check start_date and max_exam_days.", "schedule": []}
    if not allocations:
        return {"status": "error", "message": "No lab allocations found.", "schedule": []}

    # Pre-index existing regular-exam clashes by lecturer/program id.
    exam_busy_by_lecturer: dict = {}
    exam_busy_by_program: dict = {}
    for row in data.get("existing_exam_busy") or []:
        if not row.get("date") or not row.get("start_time") or not row.get("end_time"):
            continue
        entry = (row["date"], _parse_hm(row["start_time"]), _parse_hm(row["end_time"]))
        if row.get("lecturer_id") is not None:
            exam_busy_by_lecturer.setdefault(row["lecturer_id"], []).append(entry)
        if row.get("program_id") is not None:
            exam_busy_by_program.setdefault(row["program_id"], []).append(entry)

    allocations = list(allocations)
    if not data.get("disable_shuffle"):
        random.shuffle(allocations)

    assigned_by_venue: dict = {}
    assigned_by_lecturer: dict = {}
    assigned_by_program: dict = {}
    schedule = []
    created_count = 0
    skipped_no_venue = []
    skipped_no_slot = []

    for alloc in allocations:
        students = alloc.get("number_of_students") or 0
        lecturer_id = alloc.get("lecturer_id")
        program_id = alloc.get("program_id")
        all_venues = sorted(
            [venues_by_id[vid] for vid in alloc.get("venue_ids") or [] if vid in venues_by_id],
            key=lambda v: v["capacity"] or 0,
        )

        codes = ", ".join([alloc["course_code"]] + list(alloc.get("additional_course_codes") or []))

        if not all_venues:
            skipped_no_venue.append(codes)
            continue

        placed = False
        for date, day in valid_dates:
            if placed:
                break
            for st, et in time_slots:
                if placed:
                    break

                if not program_free(program_id, date, st, et, assigned_by_program, exam_busy_by_program):
                    continue
                if not lecturer_free(lecturer_id, date, st, et, assigned_by_lecturer, exam_busy_by_lecturer):
                    continue

                chosen_venues = pick_venues(all_venues, students, date, st, et, assigned_by_venue)
                if not chosen_venues:
                    continue

                for venue in chosen_venues:
                    schedule.append(
                        {
                            "lab_allocation_id": alloc["id"],
                            "lab_venue_id": venue["id"],
                            "date": date,
                            "day": day,
                            "start_time": _fmt_hm(st),
                            "end_time": _fmt_hm(et),
                        }
                    )
                    assigned_by_venue.setdefault(venue["id"], []).append((date, st, et))
                    created_count += 1

                if lecturer_id is not None:
                    assigned_by_lecturer.setdefault(lecturer_id, []).append((date, st, et))
                if program_id is not None:
                    assigned_by_program.setdefault(program_id, []).append((date, st, et))
                placed = True

        if not placed:
            skipped_no_slot.append(codes)

    if created_count == 0:
        parts = ["No exam sessions scheduled."]
        if skipped_no_venue:
            parts.append(f"No venues configured: {', '.join(skipped_no_venue)}.")
        if skipped_no_slot:
            parts.append(f"No free slot found: {', '.join(skipped_no_slot)}.")
        return {"status": "warning", "message": " ".join(parts), "schedule": []}

    msg = f"{created_count} lab/workshop exam session(s) scheduled successfully."
    if skipped_no_venue:
        msg += f" No venues configured: {', '.join(skipped_no_venue)}."
    if skipped_no_slot:
        msg += f" No free slot found: {', '.join(skipped_no_slot)}."
    return {"status": "success", "message": msg, "schedule": schedule}


def main():
    data = json.load(sys.stdin)
    result = run(data)
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
