# program_management/tasks.py
"""
Background Program Course CSV import.

Replaces running django-import-export's import_data() synchronously
inside the admin HTTP request (see ADMIN CHANGES in admin.py). The
admin view now only saves the file, creates an ImportJob row, and
queues process_program_course_import.delay(job.id) — this task does the
actual work, chunk by chunk, updating the ImportJob's progress as it
goes so the admin can watch it from the ImportJob list/detail page.

Falls back to a background thread (like backup_system.tasks) if Celery
isn't reachable, so imports still complete without blocking the request
even on an environment without Redis configured.
"""
import csv
import io
import logging
import threading
import time

from django.core.files.storage import default_storage
from django.db import OperationalError
from django.utils import timezone

from .import_helpers import (
    DEFAULT_CHUNK_SIZE,
    ProgramLookupCache,
    resolve_and_validate_rows,
    upsert_program_courses,
)

logger = logging.getLogger(__name__)

# ─── Celery-optional import (same pattern as backup_system/tasks.py) ──────
try:
    from celery import shared_task as _shared_task
    _CELERY_AVAILABLE = True
except ImportError:
    _CELERY_AVAILABLE = False

    def _shared_task(*args, **kwargs):
        def decorator(fn):
            fn.delay = lambda *a, **kw: _thread_fallback(fn, *a, **kw)
            fn.apply_async = lambda args=(), kwargs=None, **_: _thread_fallback(fn, *args, **(kwargs or {}))
            return fn
        return decorator(args[0]) if args and callable(args[0]) else decorator


def _thread_fallback(fn, *args, **kwargs):
    t = threading.Thread(target=_run_in_thread, args=(fn, args, kwargs), daemon=True)
    t.start()
    return t


def _run_in_thread(fn, args, kwargs):
    try:
        import django
        django.setup()
    except RuntimeError:
        pass
    try:
        fn(*args, **kwargs)
    except Exception:
        logger.error("Background ProgramCourse import thread failed", exc_info=True)


def queue_program_course_import(job_id, chunk_size=None):
    """
    Entry point called from admin.py. Tries Celery's .delay() first; if the
    broker is unreachable, transparently falls back to a daemon thread —
    same resilience pattern used by backup_system for backup/sync jobs.
    """
    try:
        return process_program_course_import.delay(job_id, chunk_size)
    except Exception as exc:
        logger.warning(
            "Celery broker unavailable (%s); running ProgramCourse import in a thread instead.", exc,
        )
        return _thread_fallback(process_program_course_import, job_id, chunk_size)


@_shared_task(bind=True, max_retries=3, default_retry_delay=60, acks_late=True)
def process_program_course_import(self, job_id, chunk_size=None):
    """
    Reads the ImportJob's CSV file and upserts ProgramCourse rows in
    chunks (default DEFAULT_CHUNK_SIZE rows), saving progress back onto
    the ImportJob after every chunk.

    Resumable: processed_rows is checkpointed after each chunk, so if the
    worker dies or the task is retried, it picks up from
    job.processed_rows instead of re-reading rows already committed.
    Re-processing an already-committed row is also harmless — upsert
    logic keys off (program, course_code, student_cohort), so it never
    creates a duplicate even if a row is processed twice.
    """
    # Imported here (not at module scope) so this module is safe to import
    # even before Django app registry is ready (e.g. from admin.py).
    from .models import ImportJob

    try:
        job = ImportJob.objects.get(pk=job_id)
    except ImportJob.DoesNotExist:
        logger.error("process_program_course_import: ImportJob %s not found", job_id)
        return

    if job.status == ImportJob.STATUS_CANCELLED:
        logger.info("process_program_course_import: job %s was cancelled before starting", job_id)
        return

    chunk_size = chunk_size or job.chunk_size or DEFAULT_CHUNK_SIZE
    task_start = time.monotonic()

    job.status = ImportJob.STATUS_RUNNING
    job.celery_task_id = getattr(self.request, "id", "") or ""
    if not job.started_at:
        job.started_at = timezone.now()
    job.save(update_fields=["status", "celery_task_id", "started_at"])

    program_cache = ProgramLookupCache()
    program_cache.warm()

    try:
        with default_storage.open(job.uploaded_file.name, "rb") as fh:
            text_stream = io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")
            rows = list(csv.DictReader(text_stream))
    except FileNotFoundError:
        job.status = ImportJob.STATUS_FAILED
        job.append_error(f"Uploaded file is missing from storage: {job.uploaded_file.name}")
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "error_log", "completed_at"])
        return
    except Exception as exc:
        job.status = ImportJob.STATUS_FAILED
        job.append_error(f"Could not read CSV file: {exc}")
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "error_log", "completed_at"])
        return

    total_rows = len(rows)
    if job.total_rows != total_rows:
        job.total_rows = total_rows
        job.save(update_fields=["total_rows"])

    # Resume support: skip rows already committed by a previous run/retry.
    resume_from = min(job.processed_rows or 0, total_rows)

    logger.info(
        "ProgramCourse import job %s: starting at row %s/%s (chunk_size=%s)",
        job_id, resume_from, total_rows, chunk_size,
    )

    for chunk_start in range(resume_from, total_rows, chunk_size):
        # Cheap per-chunk check (1 query per chunk_size rows) so an admin
        # cancelling from the ImportJob list takes effect within one chunk
        # instead of only being noticed at the very start of the task.
        current_status = ImportJob.objects.filter(pk=job_id).values_list("status", flat=True).first()
        if current_status == ImportJob.STATUS_CANCELLED:
            logger.info("ProgramCourse import job %s: cancelled at row %s/%s", job_id, chunk_start, total_rows)
            return

        chunk_t0 = time.monotonic()
        chunk = rows[chunk_start:chunk_start + chunk_size]

        try:
            resolved, row_errors = resolve_and_validate_rows(chunk, program_cache, row_offset=chunk_start)
            created, updated, skipped = upsert_program_courses(resolved)
        except OperationalError as exc:
            # Transient DB issue (e.g. connection drop mid-import) — checkpoint
            # is already saved as of the end of the *previous* chunk, so a
            # retry resumes here without re-doing already-committed work.
            logger.warning("ProgramCourse import job %s: DB error on chunk at row %s, retrying task: %s",
                            job_id, chunk_start, exc)
            raise self.retry(exc=exc)
        except Exception as exc:
            # Unexpected failure processing this chunk: record it and move on
            # to the next chunk rather than losing all previously-completed
            # chunks (requirement: "continue remaining chunks when safe").
            logger.error("ProgramCourse import job %s: chunk at row %s failed entirely: %s",
                          job_id, chunk_start, exc, exc_info=True)
            job.append_error(f"Rows {chunk_start + 2}-{chunk_start + len(chunk) + 1}: chunk failed ({exc})")
            job.failed_count += len(chunk)
            job.processed_rows = chunk_start + len(chunk)
            job.progress_percent = int(job.processed_rows * 100 / total_rows) if total_rows else 100
            job.save(update_fields=["error_log", "failed_count", "processed_rows", "progress_percent"])
            continue

        job.processed_rows = chunk_start + len(chunk)
        job.created_count += created
        job.updated_count += updated
        job.skipped_count += skipped
        job.failed_count += len(row_errors)
        for msg in row_errors:
            job.append_error(msg)
        job.progress_percent = int(job.processed_rows * 100 / total_rows) if total_rows else 100
        job.save(update_fields=[
            "processed_rows", "created_count", "updated_count",
            "skipped_count", "failed_count", "error_log", "progress_percent",
        ])

        chunk_duration = time.monotonic() - chunk_t0
        logger.info(
            "ProgramCourse import job %s: chunk rows %s-%s done in %.2fs "
            "(+%s created, ~%s updated, =%s skipped, !%s failed) — %s%% complete",
            job_id, chunk_start, job.processed_rows, chunk_duration,
            created, updated, skipped, len(row_errors), job.progress_percent,
        )

    job.status = ImportJob.STATUS_COMPLETED if job.failed_count == 0 else ImportJob.STATUS_COMPLETED_WITH_ERRORS
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "completed_at"])

    total_duration = time.monotonic() - task_start
    logger.info(
        "ProgramCourse import job %s finished in %.2fs: %s created, %s updated, %s skipped, %s failed (of %s rows)",
        job_id, total_duration, job.created_count, job.updated_count,
        job.skipped_count, job.failed_count, total_rows,
    )
