"""
allocation_reports/services.py
=================================
The single entry point everything else (COD panel, DVC/Dean/Timetable
dashboards, the "Generate Resit Allocation" button) calls through.

get_or_generate_pdf()
    Synchronous. Cheap enough to call inline for a *single* department (e.g.
    a COD opening their own panel): checks the content signature, and only
    re-renders the PDF if something actually changed since the last run.
    Rows with no data are skipped (STATUS_NO_DATA, no file produced).

queue_bulk_generation()
    Used by DVC / Dean / Timetable-dashboard pages, which must show *every*
    department at once. Kicks off one background thread per department that
    needs work and returns immediately, so the page renders instantly with
    whatever is already cached; the page then polls `api_status` and swaps
    placeholders for the finished PDF as each one completes — no full reload.
"""
import logging
import threading

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from allocation_reports.models import AllocationPdfRun
from allocation_reports.pdf_builder import build_allocation_pdf
from allocation_reports.signature import compute_signature

logger = logging.getLogger(__name__)

_GENERATION_LOCK = threading.Lock()
_IN_PROGRESS = set()  # (scope, department_id, campus_id) currently being built, this process


def _key(scope, department_id, campus_id=None):
    return (scope, department_id, campus_id or 0)


def _current_run(scope, department, campus=None):
    return (
        AllocationPdfRun.objects
        .filter(scope=scope, department=department, campus=campus, is_current=True)
        .first()
    )


def _next_version(scope, department, campus=None):
    """Next version number for (scope, department, campus), based on the
    highest version that has EVER existed for this combo — not just
    `current.version`. A failed run used to be created with
    `(current.version + 1) if current else 1`, which is fine for the first
    failure but collides on every retry after that: `current` never moves
    (a failed run was never promoted to is_current), so every retry recomputes
    the SAME version number, hits the `uniq_allocation_pdf_version` unique
    constraint on the second attempt, and that IntegrityError is swallowed
    silently by queue_bulk_generation's worker — so the failure never even
    reaches the log, and the row sits at "Pending" forever with no way to
    tell it's actually failing every 3 seconds behind the scenes.
    """
    from django.db.models import Max
    return (
        AllocationPdfRun.objects
        .filter(scope=scope, department=department, campus=campus)
        .aggregate(Max("version"))["version__max"] or 0
    ) + 1


def get_or_generate_pdf(scope, department, campus=None, user=None, force=False):
    """
    Returns the current AllocationPdfRun for (scope, department[, campus]),
    generating/regenerating it first if it is missing, stale, or `force=True`.
    Returns None only if the source tables have zero rows (nothing to show).
    """
    from allocation_reports.adapters import fetch_rows, fetch_serviced_rows

    signature, row_count = compute_signature(scope, department, campus=campus)
    current = _current_run(scope, department, campus=campus)

    if row_count == 0:
        # Nothing submitted yet for this department/scope. Keep any existing
        # PDF visible (still "current") rather than deleting history.
        if current is None:
            AllocationPdfRun.objects.get_or_create(
                scope=scope, department=department, campus=campus, is_current=True,
                defaults={"status": AllocationPdfRun.STATUS_NO_DATA, "version": 1},
            )
        return current

    if current is not None and current.signature == signature and current.is_ready and not force:
        return current  # nothing changed — reuse the existing PDF, no regeneration

    key = _key(scope, department.pk, campus.pk if campus else None)
    with _GENERATION_LOCK:
        if key in _IN_PROGRESS:
            return current  # another thread is already regenerating this one
        _IN_PROGRESS.add(key)

    try:
        rows = fetch_rows(scope, department, campus=campus)
        serviced_rows = fetch_serviced_rows(scope, department, campus=campus)
        pdf_bytes = build_allocation_pdf(
            scope, department, rows, campus=campus,
            generated_by=(getattr(user, "get_full_name", lambda: None)() or getattr(user, "username", None)) if user else None,
            serviced_rows=serviced_rows,
        )

        with transaction.atomic():
            next_version = _next_version(scope, department, campus)
            if current:
                current.is_current = False
                current.save(update_fields=["is_current"])

            new_run = AllocationPdfRun.objects.create(
                scope=scope, department=department, campus=campus,
                version=next_version, is_current=True,
                row_count=row_count, signature=signature,
                status=AllocationPdfRun.STATUS_READY,
                generated_at=timezone.now(),
                generated_by=user if (user and getattr(user, "is_authenticated", False)) else None,
            )
            filename = f"{scope}_{department.name.replace(' ', '_')}_v{next_version}.pdf"
            new_run.file.save(filename, ContentFile(pdf_bytes), save=True)

        return new_run
    except Exception as exc:  # pragma: no cover - defensive; surfaced via status field
        logger.exception("Allocation PDF generation failed for %s/%s", scope, department)
        # This run is promoted to is_current=True (flipping the previous
        # current off) instead of being left as an orphaned, invisible
        # is_current=False row. Previously a failure was silently buried:
        # the dashboard kept showing the OLD current run — which is
        # exactly the broken/stale one that triggered the regeneration
        # attempt in the first place — forever stuck on "Pending" with no
        # sign anything was wrong, while the JS poller (and
        # queue_bulk_generation on every page load) kept retrying every
        # few seconds indefinitely because nothing ever became `ready`.
        # Surfacing the failure as the current row means the dashboard can
        # show "Failed" with the actual error message, and a human can act
        # on it instead of staring at "Pending" indefinitely.
        with transaction.atomic():
            if current:
                current.is_current = False
                current.save(update_fields=["is_current"])
            failed_run = AllocationPdfRun.objects.create(
                scope=scope, department=department, campus=campus,
                version=_next_version(scope, department, campus), is_current=True,
                status=AllocationPdfRun.STATUS_FAILED, error_message=str(exc),
                generated_at=timezone.now(), generated_by=user if user else None,
            )
        return failed_run
    finally:
        with _GENERATION_LOCK:
            _IN_PROGRESS.discard(key)


def is_generating(scope, department_id, campus_id=None):
    with _GENERATION_LOCK:
        return _key(scope, department_id, campus_id) in _IN_PROGRESS


def _departments_with_any_allocation(scope):
    """Only bother generating for departments that actually own rows in that
    scope's table (or already have a PDF run) — per the "only generate for
    tables that have data" requirement."""
    from department_management.models import Department
    from allocation_reports.adapters import get_adapter

    adapter = get_adapter(scope)
    dept_ids_with_runs = set(
        AllocationPdfRun.objects.filter(scope=scope).values_list("department_id", flat=True)
    )
    all_depts = list(Department.objects.all())
    candidates = []
    for dept in all_depts:
        if dept.pk in dept_ids_with_runs:
            candidates.append(dept)
            continue
        # Cheap existence check without building full rows. Also probe the
        # "serviced courses" queryset — a department can have zero
        # allocations of its own but still service courses for another
        # department's program, and still needs a PDF generated so that
        # section is visible on its dashboard.
        qs_probe = adapter.queryset_for_department(dept) if scope != AllocationPdfRun.SCOPE_CAMPUS \
            else adapter.queryset_for_department(dept, campus=None)
        has_own = qs_probe.exists()
        has_serviced = False
        if not has_own and adapter.serviced_queryset_for_department is not None:
            serviced_probe = adapter.serviced_queryset_for_department(dept) if scope != AllocationPdfRun.SCOPE_CAMPUS \
                else adapter.serviced_queryset_for_department(dept, campus=None)
            has_serviced = serviced_probe.exists()
        if has_own or has_serviced:
            candidates.append(dept)
    return candidates


def queue_bulk_generation(scope, user=None):
    """
    Fire-and-forget: spins up one background thread per department in this
    scope that is missing a PDF or whose signature is stale. Safe to call on
    every page load — already-current departments are skipped instantly.
    """
    departments = _departments_with_any_allocation(scope)

    def _worker(dept):
        try:
            get_or_generate_pdf(scope, dept, user=user)
        except Exception:
            logger.exception("Background allocation PDF generation crashed for %s/%s", scope, dept)

    started = []
    for dept in departments:
        signature, row_count = compute_signature(scope, dept)
        current = _current_run(scope, dept)
        needs_work = (
            row_count > 0 and (
                current is None or current.signature != signature or not current.is_ready
            )
        )
        if not needs_work:
            continue
        key = _key(scope, dept.pk)
        with _GENERATION_LOCK:
            if key in _IN_PROGRESS:
                continue
        t = threading.Thread(target=_worker, args=(dept,), daemon=True)
        t.start()
        started.append(dept.pk)
    return started


def dashboard_rows(scope):
    """One row per department for the DVC/Dean/Timetable-dashboard table."""
    departments = _departments_with_any_allocation(scope)
    out = []
    for dept in departments:
        run = _current_run(scope, dept)
        signature, row_count = compute_signature(scope, dept)
        stale = bool(run) and run.signature != signature
        out.append({
            "department": dept,
            "run": run,
            "row_count": row_count,
            "is_stale": stale,
            "is_failed": bool(run and run.status == AllocationPdfRun.STATUS_FAILED),
            "is_generating": is_generating(scope, dept.pk),
        })
    return out
