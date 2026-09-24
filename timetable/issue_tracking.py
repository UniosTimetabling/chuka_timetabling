# timetable/issue_tracking.py
# ===================================================================
# Records *why* a Timetable cell was placed somewhere despite a known
# problem (forced collision, oversized/undersized venue, etc.) so the
# panel can shade it and explain itself on click later — even after the
# thing it originally collided with has moved away.
#
# Every write in this module happens on a background thread, kicked off
# only after the wrapping DB transaction actually commits. That means:
#   - the API response that triggered the write (a save, a move, an
#     autoschedule run) never waits on it, and
#   - the thread only ever sees committed rows, so it can safely look
#     the Timetable entry back up by id.
#
# This is intentionally NOT Celery/RQ — there's no broker in this stack
# for it, and a stray issue-log write failing silently is fine (it's UI
# sugar, not part of the scheduling correctness guarantees the rest of
# simulate_move.py / _check_conflicts already enforce). If this project
# grows a task queue later, swap `_run_in_background` for a `.delay()`
# call and nothing else here needs to change.
# ===================================================================

import logging
import threading

from django.db import transaction
from django.http import JsonResponse

from core.rbac import allowed_roles, Role

logger = logging.getLogger("timetable.issue_tracking")


def _run_in_background(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) on a daemon thread after the current
    transaction commits (or immediately, if there is no open transaction)."""

    def _wrapped():
        try:
            fn(*args, **kwargs)
        except Exception:
            # Never let a background bookkeeping failure surface to the
            # user or crash the thread pool — it's purely advisory.
            logger.exception("timetable issue_tracking background task failed")

    def _launch():
        threading.Thread(target=_wrapped, daemon=True).start()

    transaction.on_commit(_launch)


# ── keyword classification of the existing ⚠️/❌ message strings ─────────
# `_check_conflicts` / `_check_conflicts_excluding` already produce
# human-readable messages like "⚠️ Venue capacity warning: ..." — rather
# than plumb a structured issue_type through every call site, we classify
# by the same wording those functions already use.
def _classify_message(message: str) -> tuple:
    """Returns (issue_type, severity) guessed from a conflict message."""
    from .models import TimetableIssue

    text = (message or "").lower()
    severity = TimetableIssue.Severity.ERROR if "❌" in message else TimetableIssue.Severity.WARNING

    if "capacity" in text and ("over capacity" in text or "holds" in text):
        return TimetableIssue.IssueType.CAPACITY_OVER, severity
    if "capacity" in text and ("not defined" in text or "verify" in text):
        return TimetableIssue.IssueType.OTHER, severity
    if "lecturer" in text and ("booked" in text or "conflict" in text or "double" in text):
        return TimetableIssue.IssueType.LECTURER_COLLISION, severity
    if "venue" in text and ("booked" in text or "conflict" in text or "double" in text):
        return TimetableIssue.IssueType.VENUE_COLLISION, severity
    if "program" in text or "year" in text:
        return TimetableIssue.IssueType.PROGRAM_COLLISION, severity
    return TimetableIssue.IssueType.OTHER, severity


def _is_flaggable(message: str) -> bool:
    """Only genuine problem messages get recorded — not plain confirmations
    like "✅ Venue capacity: ..." or "Auto-merged: ..."."""
    if not message:
        return False
    if "✅" in message:
        return False
    return ("⚠️" in message) or ("❌" in message)


def _do_record(timetable_entry_id, issue_type, message, source, severity, user_id=None):
    from .models import Timetable, TimetableIssue

    if not Timetable.objects.filter(id=timetable_entry_id).exists():
        return  # entry was deleted/moved again before this thread ran
    TimetableIssue.objects.update_or_create(
        timetable_entry_id=timetable_entry_id,
        issue_type=issue_type,
        defaults={
            "message": message,
            "source": source,
            "severity": severity,
            "created_by_id": user_id,
        },
    )


def _do_clear(timetable_entry_id, issue_types=None):
    from .models import TimetableIssue

    qs = TimetableIssue.objects.filter(timetable_entry_id=timetable_entry_id)
    if issue_types:
        qs = qs.filter(issue_type__in=issue_types)
    qs.delete()


# ── public API ──────────────────────────────────────────────────────────

def record_issue(timetable_entry_id, issue_type, message, source, severity="warning", user=None):
    """Queue a single issue record for a Timetable row (upsert by
    (entry, issue_type)). Safe to call from inside a request/view."""
    user_id = getattr(user, "id", None)
    _run_in_background(_do_record, timetable_entry_id, issue_type, message, source, severity, user_id)


def clear_issues(timetable_entry_id, issue_types=None):
    """Queue removal of stored issue(s) for a Timetable row — e.g. because
    it was moved to a clean slot and the old flag no longer applies."""
    _run_in_background(_do_clear, timetable_entry_id, issue_types)


def clear_all_issues(timetable_entry_ids):
    """Bulk-clear ALL stored issues for a set of Timetable rows (e.g. a
    combined-group bundle that just moved together to a clean slot)."""
    for tt_id in timetable_entry_ids:
        clear_issues(tt_id)


def sync_issues_from_messages(timetable_entry_id, messages_list, source, user=None,
                               replace=True):
    """The main entry point used by save/move/swap/combine/copy call sites.

    Looks at the human-readable messages already produced by the existing
    conflict checker and, in the background:
      - clears any previously-stored issues for this entry when `replace`
        is True (the entry just landed in a new slot, so stale flags from
        its old location shouldn't survive), then
      - records one TimetableIssue per flaggable (⚠️/❌) message found.

    A clean save/move (no flaggable messages) still clears old issues —
    that's how a flagged cell stops being shaded once it's fixed.
    """
    flaggable = [m for m in (messages_list or []) if _is_flaggable(m)]
    classified = [(*_classify_message(m), m) for m in flaggable]

    def _job():
        if replace:
            _do_clear(timetable_entry_id)
        for issue_type, severity, message in classified:
            _do_record(timetable_entry_id, issue_type, message, source, severity,
                       getattr(user, "id", None))

    _run_in_background(_job)


def sync_issues_for_bundle(timetable_entry_ids, messages_list, source, user=None, replace=True):
    """Same as sync_issues_from_messages, applied to every row in a moved
    bundle (combined group / auto-merged duplicates) at once."""
    for tt_id in timetable_entry_ids:
        sync_issues_from_messages(tt_id, messages_list, source, user=user, replace=replace)


def record_autoscheduler_issue(timetable_entry_id, issue_type, message, severity="warning"):
    """Convenience wrapper for autoscheduler call sites that already know
    exactly which structured issue_type applies (they don't need the
    message-sniffing classifier the manual-save/move paths use)."""
    from .models import TimetableIssue

    if issue_type not in TimetableIssue.IssueType.values:
        issue_type = TimetableIssue.IssueType.OTHER
    record_issue(timetable_entry_id, issue_type, message,
                 TimetableIssue.Source.AUTOSCHEDULER, severity=severity)


def analyze_and_flag_published_batch(timetable_ids):
    """Post-hoc, best-effort scan of a freshly-published batch of Timetable
    rows (called from publish_timetables.publish_to_main / exam_publish_to_main)
    looking for exactly the kind of thing an autoscheduler pass sometimes has
    to force through when it runs out of clean options:
      - a class booked into a venue too small for it (CAPACITY_OVER)
      - a class booked into a venue wildly bigger than it needs (CAPACITY_UNDER
        — "small class in a big venue")
      - two different courses landing on the same venue/day/time that aren't
        legitimately sharing the slot (a CombinedCourseGroup / merged group)
        (VENUE_COLLISION)
      - one lecturer double-booked across two different slots at once
        (LECTURER_COLLISION)

    Runs entirely in the background (see `_run_in_background`) — publishing
    a timetable never waits on this. Old TimetableIssue rows for a scope
    are already gone by the time this runs, because publish_to_main()
    deletes the old Timetable rows first and TimetableIssue cascades on
    delete — so this function only ever needs to record fresh findings,
    never clear anything.
    """

    def _job():
        from .models import Timetable, TimetableIssue
        from course_allocation.models import CombinedCourseGroup

        rows = list(
            Timetable.objects.filter(id__in=list(timetable_ids))
            .select_related("course_allocation", "course_allocation__lecturer", "venue")
        )
        if not rows:
            return

        alloc_ids = [r.course_allocation_id for r in rows if r.course_allocation_id]

        # Allocations that are legitimately meant to share one slot —
        # never a "collision" even if they sit at the same venue/day/time.
        combined_alloc_ids = set(
            CombinedCourseGroup.objects.filter(allocations__id__in=alloc_ids)
            .values_list("allocations__id", flat=True)
        )
        try:
            from .models import AutoMergedExamGroup
            merged_alloc_ids = set(
                AutoMergedExamGroup.objects.filter(merged_courses__id__in=alloc_ids)
                .values_list("merged_courses__id", flat=True)
            )
        except Exception:
            merged_alloc_ids = set()
        exempt_alloc_ids = combined_alloc_ids | merged_alloc_ids

        venue_slot_map = {}   # (venue_id, day, start, end) -> [row, ...]
        lecturer_day_map = {}  # (lecturer_id, day) -> [row, ...]

        for r in rows:
            if r.venue_id:
                key = (r.venue_id, r.day, r.start_time, r.end_time)
                venue_slot_map.setdefault(key, []).append(r)
            lecturer_id = getattr(r.course_allocation, "lecturer_id", None)
            if lecturer_id:
                lecturer_day_map.setdefault((lecturer_id, r.day), []).append(r)

        to_create = []
        seen_types = set()  # (tt_id, issue_type) — the unique_together key

        def _flag(row, issue_type, message, severity):
            k = (row.id, issue_type)
            if k in seen_types:
                return
            seen_types.add(k)
            to_create.append(TimetableIssue(
                timetable_entry_id=row.id, issue_type=issue_type,
                message=message, severity=severity,
                source=TimetableIssue.Source.AUTOSCHEDULER,
            ))

        # ── capacity mismatches ──
        for r in rows:
            students = getattr(r.course_allocation, "number_of_students", None) or 0
            capacity = getattr(r.venue, "capacity", None)
            if not capacity or students <= 0:
                continue
            if students > capacity:
                over = students - capacity
                _flag(
                    r, TimetableIssue.IssueType.CAPACITY_OVER,
                    f"⚠️ Autoscheduler placed {r.course_allocation.course_code} "
                    f"({students} students) in {r.venue.code} which only holds "
                    f"{capacity} — {over} over capacity.",
                    TimetableIssue.Severity.ERROR,
                )
            elif capacity >= max(students * 2, students + 40):
                _flag(
                    r, TimetableIssue.IssueType.CAPACITY_UNDER,
                    f"⚠️ Autoscheduler placed {r.course_allocation.course_code} "
                    f"({students} students) in {r.venue.code} (capacity {capacity}) — "
                    f"a much smaller venue would free this one up for a bigger class.",
                    TimetableIssue.Severity.WARNING,
                )

        # ── forced venue collisions (same venue/day/time, different course,
        #    not a legitimate combined/merged group) ──
        for key, group_rows in venue_slot_map.items():
            distinct_allocs = {r.course_allocation_id for r in group_rows}
            if len(distinct_allocs) <= 1:
                continue
            if distinct_allocs.issubset(exempt_alloc_ids):
                continue
            codes = ", ".join(sorted({r.course_allocation.course_code for r in group_rows}))
            for r in group_rows:
                if r.course_allocation_id in exempt_alloc_ids:
                    continue
                _flag(
                    r, TimetableIssue.IssueType.VENUE_COLLISION,
                    f"❌ Autoscheduler could not find a free slot — {r.venue.code} on "
                    f"{r.day} {r.start_time.strftime('%H:%M')}-{r.end_time.strftime('%H:%M')} "
                    f"is shared by more than one course: {codes}.",
                    TimetableIssue.Severity.ERROR,
                )

        # ── forced lecturer double-bookings (overlapping times, same day) ──
        for (lecturer_id, day), group_rows in lecturer_day_map.items():
            group_rows.sort(key=lambda r: r.start_time)
            for i in range(len(group_rows)):
                for j in range(i + 1, len(group_rows)):
                    a, b = group_rows[i], group_rows[j]
                    if a.course_allocation_id == b.course_allocation_id:
                        continue
                    if a.start_time >= b.end_time or b.start_time >= a.end_time:
                        continue  # no time overlap
                    if {a.course_allocation_id, b.course_allocation_id}.issubset(exempt_alloc_ids):
                        continue
                    lecturer_name = getattr(a.course_allocation.lecturer, "name", "This lecturer")
                    for r, other in ((a, b), (b, a)):
                        _flag(
                            r, TimetableIssue.IssueType.LECTURER_COLLISION,
                            f"❌ Autoscheduler double-booked {lecturer_name} — also teaching "
                            f"{other.course_allocation.course_code} on {other.day} "
                            f"{other.start_time.strftime('%H:%M')}-{other.end_time.strftime('%H:%M')} "
                            f"@ {other.venue.code if other.venue else '?'} at the same time.",
                            TimetableIssue.Severity.ERROR,
                        )

        if to_create:
            TimetableIssue.objects.bulk_create(to_create, ignore_conflicts=True)

    _run_in_background(_job)


def get_issues_map(timetable_entry_ids=None):
    """Bulk fetch: {tt_id: [{issue_type, severity, source, message,
    created_at}, ...]}. Pass a specific id list to scope it (cheap N-row
    IN query); omit it to get every stored issue currently on the board."""
    from .models import TimetableIssue

    qs = TimetableIssue.objects.all()
    if timetable_entry_ids is not None:
        qs = qs.filter(timetable_entry_id__in=list(timetable_entry_ids))
    out = {}
    for row in qs.values(
        "timetable_entry_id", "issue_type", "severity", "source", "message", "created_at"
    ):
        out.setdefault(row["timetable_entry_id"], []).append({
            "issue_type": row["issue_type"],
            "severity": row["severity"],
            "source": row["source"],
            "message": row["message"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        })
    return out


# ── views ────────────────────────────────────────────────────────────────
# Thin JSON endpoints for the panel. Kept here (rather than in
# timetable_panel.py) so the whole "flagged placement" feature — model,
# background writer, and its own read API — lives in one file.

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def timetable_issues_map_api(request):
    """GET /timetable/issues-map/  — every stored issue currently on the
    board, keyed by Timetable id, e.g.:
        {"123": [{"issue_type": "capacity_over", "severity": "error",
                   "source": "autoscheduler", "message": "...", ...}]}
    The panel fetches this once alongside the normal timetable-data load
    and again after any save/move/publish action, to (re)shade cells and
    power the "why is this flagged" popup on click. Cheap: a handful of
    rows on a well-behaved timetable, never blocking, read-only.
    """
    issues_map = get_issues_map()
    # JSON object keys must be strings.
    return JsonResponse({str(k): v for k, v in issues_map.items()})
