# fix_duplicate_exam_dates.py
#
# One-off cleanup for exam timetables generated BEFORE the autoscheduler
# bugfix: finds any course that was scheduled on more than one distinct
# (date, start_time) — e.g. once on the 10th and again on the 27th — and
# keeps only the earliest slot, deleting the extra row(s).
#
# Safe by design: a course with multiple rows at the SAME (date,
# start_time) but different venues is a legitimate multi-venue split for
# an oversized course and is left completely alone. Only rows for the
# same course spread across DIFFERENT dates/times are touched.
#
# Run with:  python manage.py shell < fix_duplicate_exam_dates.py
#
# Add DRY_RUN = False below (or edit the constant) to actually delete;
# by default this only reports what it WOULD remove.

from collections import defaultdict
from django.db import transaction

from timetable.models import ExamTempTimetable, ExamTimetable

DRY_RUN = True   # flip to False to actually delete the duplicate rows


def _clean(model, label):
    print("=" * 70)
    print(f" Scanning {label} ({model.__name__}) for duplicate course dates")
    print("=" * 70)

    entries = list(
        model.objects
        .values("id", "course_allocation_id", "date", "start_time")
        .order_by("date", "start_time", "id")
    )
    by_course = defaultdict(list)
    for e in entries:
        by_course[e["course_allocation_id"]].append(e)

    total_fixed = 0
    total_rows_removed = 0

    for cid, rows in by_course.items():
        distinct_slots = sorted({(r["date"], r["start_time"]) for r in rows})
        if len(distinct_slots) <= 1:
            continue  # fine — zero/one slot, or a legit same-slot multi-venue split

        keep_slot = distinct_slots[0]
        drop_ids = [r["id"] for r in rows if (r["date"], r["start_time"]) != keep_slot]

        course = model._meta.get_field("course_allocation").related_model.objects.filter(
            id=cid
        ).first()
        code = getattr(course, "course_code", "?") if course else "?"

        print(
            f"  course_allocation_id={cid} ({code}) found on {distinct_slots} "
            f"— keeping {keep_slot}, {'would remove' if DRY_RUN else 'removing'} "
            f"{len(drop_ids)} row(s)"
        )

        if not DRY_RUN:
            with transaction.atomic():
                model.objects.filter(id__in=drop_ids).delete()

        total_fixed += 1
        total_rows_removed += len(drop_ids)

    if total_fixed == 0:
        print(f"  ✓ No duplicated course dates found in {label}.")
    else:
        verb = "Would fix" if DRY_RUN else "Fixed"
        print(f"  {verb} {total_fixed} course(s), {total_rows_removed} duplicate row(s).")
    print()
    return total_fixed


def main():
    print()
    if DRY_RUN:
        print("*** DRY RUN — nothing will be deleted. Set DRY_RUN = False to apply. ***\n")

    fixed_temp = _clean(ExamTempTimetable, "Draft Exam Timetable")
    fixed_final = _clean(ExamTimetable, "Published Exam Timetable")

    print("=" * 70)
    print(f" Done. Draft duplicates: {fixed_temp} | Published duplicates: {fixed_final}")
    if DRY_RUN and (fixed_temp or fixed_final):
        print(" Re-run with DRY_RUN = False at the top of this file to apply the fix.")
    print("=" * 70)


main()
