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
from course_allocation.allocation_scope import (
    tt_scope_q,
    resolve_tt_scope,
    resolve_single_tt_allocation_set,
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
    - ALL groups (within scope) are published. If genuinely no entry exists,
      exam_timetable_entry is left as None and published is still set True
      (the group is considered published).

    Concurrent Allocation Sets
    --------------------------
    Mirrors publish_to_main()'s scoping exactly (same helpers, same
    resolution order, same fallback), so exam publishing gets the same
    fix as the regular timetable: this used to be a blind "wipe every
    ExamTimetable row, republish every ExamTempTimetable row" operation —
    meaning publishing one department's (or one allocation set's) draft
    exam timetable would silently delete every OTHER department's
    already-published live exam timetable first. That was the exact
    "wrong set passed → crisis" scenario this fixes.

      - An explicit `allocation_set_id` in POST (e.g. a future per-set
        publish form) takes priority. It's validated via
        resolve_single_tt_allocation_set before anything is touched — an
        unknown/invalid id aborts with a clear message, nothing is
        deleted or published.
      - Otherwise, whatever AllocationSet(s) are ticked active on
        /timetable/dashboard/ this session (the same scope the exam
        auto-scheduler run itself used) are what gets published.
      - If nothing has ever been ticked (a department that hasn't opted
        into concurrent sets), this resolves to the same "eligible" gate
        the rest of the system already uses — no set, a legacy set, or
        submitted-to-TT — which for a single-set department covers
        exactly what it always covered. Behaviour for anyone not using
        concurrent sets is unchanged.
      - Only rows IN that scope are ever deleted or (re)published; every
        other department's / other set's ExamTimetable rows are left
        exactly as they were.
    """
    explicit_id = request.POST.get("allocation_set_id") if hasattr(request, "POST") else None
    if explicit_id:
        resolution = resolve_single_tt_allocation_set(request, allocation_set_id=explicit_id)
        if not resolution.ok:
            messages.error(request, f"❌ Publish aborted — {resolution.error}")
            return redirect("exam_timetable_panel")
        scope = (
            {"type": "sets", "ids": [resolution.allocation_set.id]}
            if resolution.allocation_set
            else resolve_tt_scope(request)
        )
        scope_label = resolution.allocation_set.name if resolution.allocation_set else None
    else:
        scope = resolve_tt_scope(request)
        scope_label = None

    exam_filter = tt_scope_q(scope, prefix="course_allocation__allocation_set")
    merged_filter = tt_scope_q(scope, prefix="base_course__allocation_set")
    shared_ids = list(
        SharedVenueExamGroup.objects
        .filter(tt_scope_q(scope, prefix="course_allocations__allocation_set"))
        .distinct()
        .values_list("id", flat=True)
    )

    # Clear existing exam timetable — SCOPED: only rows belonging to the
    # allocation set(s) actually being (re)published. Every other
    # department's / set's already-live ExamTimetable rows are untouched.
    ExamTimetable.objects.filter(exam_filter).delete()

    temp_entries = list(
        ExamTempTimetable.objects.filter(exam_filter)
        .select_related("course_allocation", "venue")
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
                allocated_students=entry.allocated_students,
            )
            # First entry wins per course_allocation (for group linking)
            if entry.course_allocation_id not in ca_to_exam_entry:
                ca_to_exam_entry[entry.course_allocation_id] = exam_entry

    # ── Update MergedCourseGroup ──────────────────────────────────────────────
    # merged_courses is M2M → CourseAllocation directly (confirmed from models.py:
    # CourseAllocation has no course FK — it IS the course).
    # Look up base_course first, then fall back to any course in merged_courses.
    # Publish ALL groups IN SCOPE — a merged group belonging to a different
    # (not currently active) allocation set is left completely untouched,
    # same as its ExamTimetable rows above.
    merged_count = 0
    merged_no_entry = 0

    for group in (
        MergedCourseGroup.objects
        .filter(merged_filter)
        .select_related("base_course")
        .prefetch_related("merged_courses")
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
    # Publish ALL groups IN SCOPE — same as MergedCourseGroup above.
    # SharedVenueExamGroup reaches CourseAllocation only through the M2M
    # `course_allocations`, so scope ids are resolved up front (shared_ids)
    # rather than filtering this loop's queryset directly with the M2M lookup,
    # which could otherwise yield the same group more than once.
    shared_count = 0
    shared_no_entry = 0

    for group in SharedVenueExamGroup.objects.filter(id__in=shared_ids):
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

    # Remove temp exam timetable AFTER successful publish — SCOPED to the
    # same allocation set(s) just published, never the whole table.
    ExamTempTimetable.objects.filter(exam_filter).delete()

    scope_note = f" (allocation set: {scope_label})" if scope_label else ""
    messages.success(
        request,
        f"✅ Exam timetable published successfully{scope_note}. "
        f"Marked {merged_count} merged groups and {shared_count} shared groups as published."
    )

    return redirect("exam_timetable_panel")


# ======================================================
# 📗 Publish Normal Temp Timetable → Main Timetable
# ======================================================

@transaction.atomic
def publish_to_main(request):
    """
    Move TempTimetable entries into the main Timetable model.

    Strategy for AutoMergedExamGroup:
    - base_course is FK → CourseAllocation directly.
    - We build a dict: course_allocation_id → Timetable instance during the copy step.
    - ALL groups (within scope) are published. If no entry exists, timetable_entry is left as None.

    Concurrent Allocation Sets
    --------------------------
    This used to be a blind "wipe every Timetable row, republish every
    TempTimetable row" operation — meaning publishing one department's
    (or one allocation set's) draft timetable would silently delete every
    OTHER department's already-published live timetable first. That was
    the exact "wrong set passed → crisis" risk this fixes.

    Scoping now works like this:
      - An explicit `allocation_set_id` in POST (e.g. a future per-set
        publish form) takes priority. It's validated via
        resolve_single_tt_allocation_set before anything is touched — an
        unknown/invalid id aborts with a clear message, nothing is
        deleted or published.
      - Otherwise, whatever AllocationSet(s) are ticked active on
        /timetable/dashboard/ this session (the same scope the
        auto-scheduler run itself used) are what gets published.
      - If nothing has ever been ticked (a department that hasn't opted
        into concurrent sets), this resolves to the same "eligible" gate
        the rest of the system already uses — no set, a legacy set, or
        submitted-to-TT — which for a single-set department covers
        exactly what it always covered. Behaviour for anyone not using
        concurrent sets is unchanged.
      - Only rows IN that scope are ever deleted or (re)published; every
        other department's / other set's Timetable rows are left exactly
        as they were.
    """
    explicit_id = request.POST.get("allocation_set_id") if hasattr(request, "POST") else None
    if explicit_id:
        resolution = resolve_single_tt_allocation_set(request, allocation_set_id=explicit_id)
        if not resolution.ok:
            messages.error(request, f"❌ Publish aborted — {resolution.error}")
            return redirect("timetable_panel")
        scope = (
            {"type": "sets", "ids": [resolution.allocation_set.id]}
            if resolution.allocation_set
            else resolve_tt_scope(request)
        )
        scope_label = resolution.allocation_set.name if resolution.allocation_set else None
    else:
        scope = resolve_tt_scope(request)
        scope_label = None

    timetable_filter = tt_scope_q(scope, prefix="course_allocation__allocation_set")
    merged_filter = tt_scope_q(scope, prefix="base_course__allocation_set")

    # Clear old timetable — SCOPED: only rows belonging to the allocation
    # set(s) actually being (re)published. Every other department's / set's
    # already-live Timetable rows are untouched.
    Timetable.objects.filter(timetable_filter).delete()

    temp_entries = list(
        TempTimetable.objects.filter(timetable_filter)
        .select_related("course_allocation", "venue")
    )

    # ── Copy temp → main, build lookup: course_allocation_id → Timetable ──────
    ca_to_timetable_entry: dict = {}
    all_published_ids: list = []

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
            all_published_ids.append(timetable_entry.id)
            if entry.course_allocation_id not in ca_to_timetable_entry:
                ca_to_timetable_entry[entry.course_allocation_id] = timetable_entry

    # ── Update AutoMergedExamGroup ────────────────────────────────────────────
    # base_course is FK → CourseAllocation. Use the lookup dict — zero extra queries.
    # Publish ALL groups IN SCOPE — a merged group belonging to a different
    # (not currently active) allocation set is left completely untouched,
    # same as its Timetable rows above.
    merged_count = 0
    merged_no_entry = 0

    for group in AutoMergedExamGroup.objects.filter(merged_filter).select_related("base_course").all():
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

    # Remove temp timetable AFTER successful publish — SCOPED to the same
    # allocation set(s) just published, never the whole table.
    TempTimetable.objects.filter(timetable_filter).delete()

    # Scan the freshly-published batch for forced placements (an oversized/
    # undersized venue fit, a collision the autoscheduler couldn't avoid)
    # and record them for the panel to shade/explain later. Old issues for
    # this scope are already gone (they were on the Timetable rows just
    # deleted above, and TimetableIssue cascades). Runs entirely in the
    # background — never slows down the publish response.
    from .issue_tracking import analyze_and_flag_published_batch
    analyze_and_flag_published_batch([t.id for t in ca_to_timetable_entry.values()])

    scope_note = f" (allocation set: {scope_label})" if scope_label else ""
    messages.success(
        request,
        f"✅ Timetable published successfully{scope_note}. "
        f"Marked {merged_count} merged groups as published."
    )

    return redirect("timetable_panel")