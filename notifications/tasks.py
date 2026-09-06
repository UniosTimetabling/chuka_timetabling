"""
notifications/tasks.py
=======================
Batches a DVC's run of approve/reject clicks into ONE report per department,
sent to the department's COD and to every lecturer whose course was touched.

WHY A TIMEOUT INSTEAD OF AN EXPLICIT "DONE" BUTTON
---------------------------------------------------
The DVC panel lets a DVC approve/reject allocations one at a time, in bulk
by department/faculty/lecturer, and there is no single moment that reliably
means "the DVC is completely finished reviewing this department." Firing a
notification per click would spam the COD and lecturers with one message per
course. Instead:

  1. Every approve/reject action writes a `DVCActionLog` row immediately
     (see faculty_management/dvc_panel.py -> _log_dvc_action), with
     reported=False.
  2. This task runs every ~30s (see beat_schedule in
     university_timetable_system/celery.py). For each department that has
     unreported rows, it checks the timestamp of the MOST RECENT row.
  3. If that timestamp is more than IDLE_MINUTES old, we treat the DVC as
     "finished" for that department right now, compile everything unreported
     into a single summary (+ PDF if long), notify the COD + affected
     lecturers, and mark those rows reported=True.
  4. If the DVC is still actively clicking (latest row newer than the
     threshold), we leave the batch alone and check again next run — so a
     DVC who is mid-review never gets their batch cut off early.

This is a heuristic, not a guarantee — a DVC who pauses for coffee for
longer than IDLE_MINUTES will trigger an early (but still accurate) report.
If you want a hard guarantee instead of a heuristic, pair this with an
explicit "Finish Review" button in the DVC panel that calls
flush_department_now() directly (see below).
"""
from __future__ import annotations

import io
import logging
from collections import defaultdict
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from course_allocation.models import DVCActionLog
from department_management.models import Department
from notifications.models import Notification

logger = logging.getLogger(__name__)

# How long the DVC must go quiet (no approve/reject clicks for this department)
# before we consider their review session "finished" and send the report.
IDLE_MINUTES = 3

ALLOC_TYPE_LABELS = {
    DVCActionLog.ALLOC_STANDARD: "Standard",
    DVCActionLog.ALLOC_CAMPUS:   "Campus",
    DVCActionLog.ALLOC_ODEL:     "ODEL",
}


def _build_pdf_report(dept, approved_qs, rejected_qs):
    """Best-effort PDF summary of a review batch. Returns a media URL, or None
    if reportlab isn't installed (report still gets sent as plain text)."""
    try:
        from notifications.notification_apis import (
            REPORTLAB_OK, _pdf_doc, _pdf_header, _sec_head, _pdf_table, _save_pdf, COLORS,
        )
        from reportlab.lib.units import cm
    except Exception:
        return None

    if not REPORTLAB_OK:
        return None

    buf = io.BytesIO()
    doc = _pdf_doc(buf)
    story = _pdf_header(
        f"DVC REVIEW REPORT — {dept.name.upper()}",
        f"Approved: {approved_qs.count()}  |  Rejected: {rejected_qs.count()}",
        COLORS["success"],
    )

    if approved_qs.exists():
        story += _sec_head(f"✔ Approved ({approved_qs.count()})", COLORS["success"])
        rows = [["#", "Type", "Code", "Lecturer", "Time"]]
        for i, log in enumerate(approved_qs.select_related("lecturer"), 1):
            rows.append([
                str(i), ALLOC_TYPE_LABELS.get(log.alloc_type, log.alloc_type),
                log.course_code or "—",
                log.lecturer.display_name if log.lecturer else "—",
                log.created_at.strftime("%H:%M"),
            ])
        story.append(_pdf_table(rows, [0.7*cm, 2.5*cm, 3*cm, 5*cm, 2*cm], COLORS["success"]))

    if rejected_qs.exists():
        story += _sec_head(f"✘ Rejected ({rejected_qs.count()})", COLORS["critical"])
        rows = [["#", "Type", "Code", "Lecturer", "Time"]]
        for i, log in enumerate(rejected_qs.select_related("lecturer"), 1):
            rows.append([
                str(i), ALLOC_TYPE_LABELS.get(log.alloc_type, log.alloc_type),
                log.course_code or "—",
                log.lecturer.display_name if log.lecturer else "—",
                log.created_at.strftime("%H:%M"),
            ])
        story.append(_pdf_table(rows, [0.7*cm, 2.5*cm, 3*cm, 5*cm, 2*cm], COLORS["critical"]))

    doc.build(story)
    return _save_pdf(buf, f"dvc_review_{dept.id}")


def flush_department_now(department_id):
    """
    Compile and send the report for one department immediately, regardless
    of idle time. Called by the periodic task once idle-timeout is reached,
    and can also be wired to an explicit "Finish Review" button for a
    guaranteed (non-heuristic) send.
    """
    pending = DVCActionLog.objects.filter(department_id=department_id, reported=False) \
                                   .exclude(action=DVCActionLog.ACTION_SUBMIT)
    if not pending.exists():
        return

    dept = Department.objects.filter(id=department_id).first()
    if not dept:
        pending.update(reported=True)
        return

    approved_qs = pending.filter(action=DVCActionLog.ACTION_APPROVE)
    rejected_qs = pending.filter(action=DVCActionLog.ACTION_REJECT)
    now_display = timezone.localtime().strftime("%b %d, %Y — %H:%M")

    pdf_url = None
    total = pending.count()
    if total > 8:
        pdf_url = _build_pdf_report(dept, approved_qs, rejected_qs)

    # ── Notify the COD ──────────────────────────────────────────────────────
    if dept.leader:
        summary = (
            f"📋 DVC finished reviewing {dept.name}'s course allocation at {now_display}: "
            f"{approved_qs.count()} approved, {rejected_qs.count()} rejected."
        )
        if pdf_url:
            summary += f"\nFull report: {pdf_url}"
        elif total <= 8:
            lines = [summary, ""]
            for log in pending.select_related("lecturer").order_by("created_at"):
                verb = "Approved" if log.action == DVCActionLog.ACTION_APPROVE else "Rejected"
                lect = log.lecturer.display_name if log.lecturer else "Unassigned"
                lines.append(f"  • [{log.course_code or 'N/A'}] {verb} — {lect} ({log.created_at:%H:%M})")
            summary = "\n".join(lines)
        Notification.create_for_user(summary, dept.leader)

    # ── Notify each affected lecturer, on their own dashboard ──────────────
    lecturer_logs = defaultdict(list)
    for log in pending.exclude(lecturer=None).select_related("lecturer__user"):
        lecturer_logs[log.lecturer_id].append(log)

    for lecturer_id, logs in lecturer_logs.items():
        lecturer = logs[0].lecturer
        if not lecturer or not lecturer.user:
            continue
        approved = [l for l in logs if l.action == DVCActionLog.ACTION_APPROVE]
        rejected = [l for l in logs if l.action == DVCActionLog.ACTION_REJECT]
        parts = []
        if approved:
            codes = ", ".join(l.course_code or "N/A" for l in approved)
            parts.append(f"approved: {codes}")
        if rejected:
            codes = ", ".join(l.course_code or "N/A" for l in rejected)
            parts.append(f"rejected: {codes}")
        Notification.create_for_user(
            f"📬 DVC review update for {dept.name} ({now_display}) — " + "; ".join(parts) + ".",
            lecturer.user,
        )

    pending.update(reported=True)


@shared_task
def flush_idle_dvc_batches():
    """
    Runs every ~30s (see university_timetable_system/celery.py beat_schedule).
    Finds departments with unreported DVC approve/reject activity whose most
    recent action is older than IDLE_MINUTES, and flushes a single combined
    report for each.
    """
    cutoff = timezone.now() - timedelta(minutes=IDLE_MINUTES)

    dept_ids = (
        DVCActionLog.objects
        .filter(reported=False)
        .exclude(action=DVCActionLog.ACTION_SUBMIT)
        .values_list("department_id", flat=True)
        .distinct()
    )

    for dept_id in dept_ids:
        if dept_id is None:
            continue
        latest = (
            DVCActionLog.objects
            .filter(department_id=dept_id, reported=False)
            .exclude(action=DVCActionLog.ACTION_SUBMIT)
            .order_by("-created_at")
            .first()
        )
        if not latest:
            continue
        if latest.created_at > cutoff:
            # DVC likely still actively reviewing this department — wait.
            continue
        try:
            flush_department_now(dept_id)
        except Exception:
            logger.exception("Failed to flush DVC review batch for department %s", dept_id)
