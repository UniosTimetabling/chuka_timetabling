"""
CourseAllocation DUPLICATE MERGER
================================================================================
RUN THIS INSIDE THE DJANGO SHELL, e.g.:

    python manage.py shell < merge_duplicate_course_allocations.py

WHAT COUNTS AS A "DUPLICATE" HERE
-----------------------------------
Two allocations are a real duplicate ONLY if they share the exact same
(program, course_code, intake) — this is precisely the triple Django's own
CourseAllocation.clean() enforces uniqueness over. In practice that means:

    COSC 101       and   COSC 101        -> DUPLICATE
    COSC 101-A     and   COSC 101-A      -> DUPLICATE
    COSC 101-B     and   COSC 101-B      -> DUPLICATE

    COSC 101-A     and   COSC 101-B      -> NOT a duplicate (different
                                              teaching groups — leave alone)
    COSC 101       and   COSC 101-A      -> NOT a duplicate (one is the
                                              whole-class row, the other a
                                              specific group — leave alone)

This is exactly the case you flagged: leftover exact-twin rows from a
previous cleanup pass (e.g. two separate "COSC 101-B" rows), NOT the
normal, expected "-A / -B / -C" group split.

MERGE RULES
------------
For every set of true duplicates:
  - number_of_students : the HIGHEST value across the duplicate set.
  - lecturer            : the "first" lecturer — the lecturer on the
                           lowest-id (earliest-created) row in the set. If
                           that row happens to have no lecturer assigned,
                           falls back to the first duplicate (in id order)
                           that DOES have one, so a real lecturer isn't
                           discarded just because the earliest row was
                           blank. (Change FIRST_LECTURER_SKIPS_BLANK below
                           to False if you want strictly-first, blank or not.)
  - approved_by_dvc / rejected_by_dvc / submitted_to_tt:
                           kept as True if ANY duplicate in the set has it
                           True (so a real approval/submission is never
                           silently lost by the merge). Disable via
                           MERGE_STATUS_FLAGS_AS_OR below if you'd rather
                           these also just take the first row's value.
  - everything else (department, origin_department, program_course,
    is_elective, is_evening_weekend, selection_group, specialization_stem,
    special_intake_group, student_group, reason_for_disapproval)
                         : taken from the surviving (lowest-id) row as-is.

The surviving row is the lowest-id row in each duplicate set (updated
in place); every other row in the set is deleted.

Set DRY_RUN = True first to preview a full report with zero writes.
"""

from collections import defaultdict

from django.db import transaction

from course_allocation.models import CourseAllocation

# ──────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────
DRY_RUN = False
DEPARTMENT_NAMES = None   # e.g. ["Education"] to restrict, or None for all

FIRST_LECTURER_SKIPS_BLANK = True
MERGE_STATUS_FLAGS_AS_OR = True


def run():
    qs = CourseAllocation.objects.select_related("lecturer", "department").order_by("id")
    if DEPARTMENT_NAMES:
        qs = qs.filter(department__name__in=DEPARTMENT_NAMES)

    allocations = list(qs)
    print(f"Loaded {len(allocations)} CourseAllocation rows"
          f"{' for ' + ', '.join(DEPARTMENT_NAMES) if DEPARTMENT_NAMES else ''}.")

    groups = defaultdict(list)
    for alloc in allocations:
        key = (alloc.program_id, (alloc.course_code or "").strip().upper(), alloc.intake)
        groups[key].append(alloc)

    duplicate_sets = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"Found {len(duplicate_sets)} duplicate set(s) "
          f"covering {sum(len(v) for v in duplicate_sets.values())} rows.")

    report = []
    to_delete_ids = []

    with transaction.atomic():
        sp = transaction.savepoint()

        for (program_id, code, intake), rows in duplicate_sets.items():
            rows = sorted(rows, key=lambda r: r.id)
            survivor, dupes = rows[0], rows[1:]

            # ── number_of_students: highest across the whole set ──────────
            best_students = max(r.number_of_students or 0 for r in rows)

            # ── lecturer: first, optionally skipping a blank first row ────
            chosen_lecturer = survivor.lecturer
            if FIRST_LECTURER_SKIPS_BLANK and chosen_lecturer is None:
                for r in rows:
                    if r.lecturer_id:
                        chosen_lecturer = r.lecturer
                        break

            # ── status flags ────────────────────────────────────────────
            approved = survivor.approved_by_dvc
            rejected = survivor.rejected_by_dvc
            submitted = survivor.submitted_to_tt
            if MERGE_STATUS_FLAGS_AS_OR:
                approved = any(r.approved_by_dvc for r in rows)
                submitted = any(r.submitted_to_tt for r in rows)
                # never merge rejected=True on top of an approved=True result
                rejected = any(r.rejected_by_dvc for r in rows) and not approved

            before = {
                "students": [r.number_of_students for r in rows],
                "lecturers": [r.lecturer.display_name if r.lecturer_id else None for r in rows],
            }

            report.append({
                "program_id": program_id,
                "code": code,
                "intake": intake,
                "surviving_id": survivor.id,
                "deleted_ids": [r.id for r in dupes],
                "students_before": before["students"],
                "students_after": best_students,
                "lecturers_before": before["lecturers"],
                "lecturer_after": chosen_lecturer.display_name if chosen_lecturer else None,
                "approved_after": approved,
                "rejected_after": rejected,
                "submitted_after": submitted,
            })

            if not DRY_RUN:
                survivor.number_of_students = best_students
                survivor.lecturer = chosen_lecturer
                survivor.approved_by_dvc = approved
                survivor.rejected_by_dvc = rejected
                survivor.submitted_to_tt = submitted
                survivor.save(update_fields=[
                    "number_of_students", "lecturer",
                    "approved_by_dvc", "rejected_by_dvc", "submitted_to_tt",
                ])
                to_delete_ids.extend(r.id for r in dupes)

        if to_delete_ids and not DRY_RUN:
            CourseAllocation.objects.filter(id__in=to_delete_ids).delete()

        if DRY_RUN:
            print("\n*** DRY_RUN=True — rolling back, nothing was written. ***")
            transaction.savepoint_rollback(sp)
        else:
            transaction.savepoint_commit(sp)

    # ── report ──────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"Duplicate sets merged : {len(report)}")
    print(f"Rows deleted          : {sum(len(r['deleted_ids']) for r in report)}")

    for r in report[:60]:
        print(
            f"\n  '{r['code']}' (program {r['program_id']}, {r['intake']}): "
            f"survivor id={r['surviving_id']}, deleted ids={r['deleted_ids']}"
        )
        print(f"    students: {r['students_before']} -> {r['students_after']}")
        print(f"    lecturers: {r['lecturers_before']} -> {r['lecturer_after']}")
        print(
            f"    flags -> approved={r['approved_after']} "
            f"rejected={r['rejected_after']} submitted={r['submitted_after']}"
        )
    if len(report) > 60:
        print(f"\n  ... and {len(report) - 60} more duplicate sets")

    print("\nDone." if not DRY_RUN else "\nDry run complete — re-run with DRY_RUN=False to commit.")


run()
