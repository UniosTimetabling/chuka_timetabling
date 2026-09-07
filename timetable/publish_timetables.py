from django.shortcuts import redirect
from django.contrib import messages
from django.db import transaction
from collections import defaultdict

from timetable.models import (
    Timetable,
    TempTimetable,
    AutoMergedExamGroup,
    ExamTimetable,
    ExamTempTimetable,
    MergedCourseGroup,
    SharedVenueExamGroup,
)


# ======================================================
# 📘 Publish Exam Temp Timetable → Main Exam Timetable
# ======================================================

@transaction.atomic
def exam_publish_to_main(request):
    """
    Publish ExamTempTimetable to main ExamTimetable model.

    Strategy for MergedCourseGroup:
    - merged_courses is M2M → CourseAllocation (CourseAllocation IS the course, no Course FK).
    - We build a dict: course_allocation_id → ExamTimetable instance during the copy step.
    - For each group, we check base_course first, then any merged_course, using that dict.
    - ALL groups are published. If genuinely no entry exists, exam_timetable_entry is left
      as None and published is still set True (the group is considered published).
    """

    # Clear existing exam timetable
    ExamTimetable.objects.all().delete()

    temp_entries = list(
        ExamTempTimetable.objects.select_related("course_allocation", "venue")
    )

    # ── Safety net: same course scheduled on 2+ different dates/times ────
    # The auto-scheduler has its own duplicate audit
    # (_audit_and_fix_duplicate_placements) that is supposed to catch this
    # before the run finishes — but if that run crashed partway through
    # (an exception jumps straight past the audit, leaving whatever was
    # already written in ExamTempTimetable in place) or someone manually
    # edited entries afterward, this table can still hold a course on two
    # different dates. Publish is the last checkpoint before this becomes
    # the OFFICIAL timetable students and lecturers act on, so it must
    # never trust the temp table blindly — re-check here regardless of
    # whether the scheduler's own audit ran. Rows for the SAME course at
    # the SAME (date, start_time) but different venues (a shared/family
    # course legitimately split across rooms) are left untouched; only
    # rows spread across DIFFERENT dates/times are a bug.
    by_course_allocation: dict = defaultdict(list)
    for entry in temp_entries:
        by_course_allocation[entry.course_allocation_id].append(entry)

    cross_date_dupes = 0
    deduped_entries = []
    for cid, rows in by_course_allocation.items():
        distinct_slots = {(r.date, r.start_time) for r in rows}
        if len(distinct_slots) <= 1:
            deduped_entries.extend(rows)
            continue
        keep_slot = min(distinct_slots)
        dropped = [r for r in rows if (r.date, r.start_time) != keep_slot]
        code = getattr(rows[0].course_allocation, "course_code", "?")
        print(
            f"⚠️  [Publish-DUPLICATE] course_allocation_id={cid} ({code}) is "
            f"scheduled on {sorted(distinct_slots)} in the temp timetable — "
            f"publishing only {keep_slot}, dropping {len(dropped)} row(s) "
            f"instead of publishing the same exam on two dates."
        )
        cross_date_dupes += 1
        deduped_entries.extend(r for r in rows if (r.date, r.start_time) == keep_slot)

    if cross_date_dupes:
        print(
            f"⚠️  [Publish-DUPLICATE] {cross_date_dupes} course(s) were scheduled "
            f"on multiple dates in the temp timetable — trimmed to one date each "
            f"before publishing. This should not happen if the auto-scheduler run "
            f"completed cleanly; investigate why its own audit didn't catch these."
        )
    temp_entries = deduped_entries

    # ── Copy temp → main, build lookup: course_allocation_id → ExamTimetable ──
    # One ExamTimetable row per unique (course_allocation, venue, day, date, start, end).
    # We keep only the first created row per course_allocation_id for group linking.
    ca_to_exam_entry: dict = {}

    dedup_key_seen: set = set()
    for entry in temp_entries:
        key = (
            entry.course_allocation_id,
            entry.venue_id,
            entry.day,
            entry.date,
            entry.start_time,
            entry.end_time,
        )
        if key not in dedup_key_seen:
            dedup_key_seen.add(key)
            exam_entry = ExamTimetable.objects.create(
                course_allocation=entry.course_allocation,
                venue=entry.venue,
                day=entry.day,
                date=entry.date,
                start_time=entry.start_time,
                end_time=entry.end_time,
            )
            # First entry wins per course_allocation (for group linking)
            if entry.course_allocation_id not in ca_to_exam_entry:
                ca_to_exam_entry[entry.course_allocation_id] = exam_entry

    # ── Update MergedCourseGroup ──────────────────────────────────────────────
    # merged_courses is M2M → CourseAllocation directly (confirmed from models.py:
    # CourseAllocation has no course FK — it IS the course).
    # Look up base_course first, then fall back to any course in merged_courses.
    # Publish ALL groups regardless — no skipping.
    merged_count = 0
    merged_no_entry = 0

    for group in (
        MergedCourseGroup.objects
        .select_related("base_course")
        .prefetch_related("merged_courses")
        .all()
    ):
        # 1. Try base_course first (zero extra query — uses the dict)
        exam_entry = ca_to_exam_entry.get(group.base_course_id)

        # 2. Fall back to any course in the M2M set
        if exam_entry is None:
            for ca in group.merged_courses.all():
                exam_entry = ca_to_exam_entry.get(ca.pk)
                if exam_entry:
                    break

        if exam_entry is None:
            merged_no_entry += 1
            print(
                f"ℹ️  MergedCourseGroup pk={group.pk} ({group.merged_code}) — "
                f"no ExamTimetable entry found for base_course_id={group.base_course_id} "
                f"or any merged course. Publishing with exam_timetable_entry=None."
            )

        group.exam_timetable_entry = exam_entry          # may be None — that's fine
        group.exam_temp_timetable_entry = None
        group.published = True
        group.save(update_fields=[
            "exam_timetable_entry",
            "exam_temp_timetable_entry",
            "published",
        ])
        merged_count += 1

    print(f"✅ Published {merged_count} MergedCourseGroup records")
    if merged_no_entry:
        print(f"ℹ️  {merged_no_entry} MergedCourseGroup(s) had no ExamTimetable entry (entry set to None)")

    # ── Update SharedVenueExamGroup ───────────────────────────────────────────
    # Match on venue + date + start_time + end_time.
    # Publish ALL groups regardless — no skipping.
    shared_count = 0
    shared_no_entry = 0

    for group in SharedVenueExamGroup.objects.all():
        exam_entry = ExamTimetable.objects.filter(
            venue=group.venue,
            date=group.date,
            start_time=group.start_time,
            end_time=group.end_time,
        ).first()

        if exam_entry is None:
            shared_no_entry += 1
            print(
                f"ℹ️  SharedVenueExamGroup pk={group.pk} "
                f"(venue={group.venue_id}, date={group.date}, "
                f"{group.start_time}-{group.end_time}) — no matching ExamTimetable entry. "
                f"Publishing with exam_timetable_entry=None."
            )

        group.exam_timetable_entry = exam_entry          # may be None — that's fine
        group.exam_temp_timetable_entry = None
        group.published = True
        group.save(update_fields=[
            "exam_timetable_entry",
            "exam_temp_timetable_entry",
            "published",
        ])
        shared_count += 1

    print(f"✅ Published {shared_count} SharedVenueExamGroup records")
    if shared_no_entry:
        print(f"ℹ️  {shared_no_entry} SharedVenueExamGroup(s) had no ExamTimetable entry (entry set to None)")

    # Remove temp exam timetable AFTER successful publish
    ExamTempTimetable.objects.all().delete()

    messages.success(
        request,
        f"✅ Exam timetable published successfully. "
        f"Marked {merged_count} merged groups and {shared_count} shared groups as published."
    )

    return redirect("exam_timetable_panel")


# ======================================================
# 📗 Publish Normal Temp Timetable → Main Timetable
# ======================================================

@transaction.atomic
def publish_to_main(request):
    """
    Move all TempTimetable entries into the main Timetable model.

    Strategy for AutoMergedExamGroup:
    - base_course is FK → CourseAllocation directly.
    - We build a dict: course_allocation_id → Timetable instance during the copy step.
    - ALL groups are published. If no entry exists, timetable_entry is left as None.
    """

    # Clear old timetable
    Timetable.objects.all().delete()

    temp_entries = list(
        TempTimetable.objects.select_related("course_allocation", "venue")
    )

    # ── Copy temp → main, build lookup: course_allocation_id → Timetable ──────
    ca_to_timetable_entry: dict = {}

    dedup_key_seen: set = set()
    for entry in temp_entries:
        key = (
            entry.course_allocation_id,
            entry.venue_id,
            entry.day,
            entry.start_time,
            entry.end_time,
        )
        if key not in dedup_key_seen:
            dedup_key_seen.add(key)
            timetable_entry = Timetable.objects.create(
                course_allocation=entry.course_allocation,
                venue=entry.venue,
                day=entry.day,
                start_time=entry.start_time,
                end_time=entry.end_time,
            )
            if entry.course_allocation_id not in ca_to_timetable_entry:
                ca_to_timetable_entry[entry.course_allocation_id] = timetable_entry

    # ── Update AutoMergedExamGroup ────────────────────────────────────────────
    # base_course is FK → CourseAllocation. Use the lookup dict — zero extra queries.
    # Publish ALL groups regardless — no skipping.
    merged_count = 0
    merged_no_entry = 0

    for group in AutoMergedExamGroup.objects.select_related("base_course").all():
        timetable_entry = ca_to_timetable_entry.get(group.base_course_id)

        if timetable_entry is None:
            merged_no_entry += 1
            print(
                f"ℹ️  AutoMergedExamGroup pk={group.pk} ({group.merged_code}) — "
                f"no Timetable entry found for base_course_id={group.base_course_id}. "
                f"Publishing with timetable_entry=None."
            )

        group.timetable_entry = timetable_entry          # may be None — that's fine
        group.temp_timetable_entry = None
        group.published = True
        group.save(update_fields=[
            "timetable_entry",
            "temp_timetable_entry",
            "published",
        ])
        merged_count += 1

    print(f"✅ Published {merged_count} AutoMergedExamGroup records")
    if merged_no_entry:
        print(f"ℹ️  {merged_no_entry} AutoMergedExamGroup(s) had no Timetable entry (entry set to None)")

    # Remove temp timetable AFTER successful publish
    TempTimetable.objects.all().delete()

    messages.success(
        request,
        f"✅ Timetable published successfully. "
        f"Marked {merged_count} merged groups as published."
    )

    return redirect("timetable_panel")