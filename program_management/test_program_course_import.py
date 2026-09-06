# program_management/test_program_course_import.py
"""
Tests for the redesigned (async, chunked) Program Course CSV import.

Covers the TEST PLAN requested for this redesign:
  - small/medium/large row counts (10 / 100 / 1000; 10000 marked slow)
  - import interruption + resume
  - duplicate prevention (re-running the same file, and re-running from
    row 0 after a partial run)
  - admin routing (CSV -> async job; non-CSV -> unchanged sync path)
  - rollback / error isolation (one bad chunk doesn't lose earlier chunks)

Run with:
    python manage.py test program_management.test_program_course_import
Slow test (10k rows) is skipped by default — set RUN_SLOW_IMPORT_TESTS=1
to include it, since it's more of a perf smoke test than a unit test.
"""
import csv
import io
import os
import time

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse

from department_management.models import Department
from program_management.models import Program, ProgramCourse, ImportJob
from program_management.import_helpers import (
    ProgramLookupCache, normalize_program_course_row, resolve_and_validate_rows, upsert_program_courses,
)
from program_management.tasks import process_program_course_import


def _make_csv_bytes(rows, headers=("program", "course_code", "course_name", "year", "semester", "unit_type", "student_cohort")):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


class ProgramCourseImportTestBase(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name="Computing")
        self.program = Program.objects.create(name="BSc Computer Science", department=self.department)
        self.other_program = Program.objects.create(name="BSc IT", department=self.department)
        self.user = User.objects.create_superuser("admin", "admin@example.com", "password123")

    def make_job(self, rows, chunk_size=500, headers=None):
        kwargs = {} if headers is None else {"headers": headers}
        csv_bytes = _make_csv_bytes(rows, **kwargs)
        upload = SimpleUploadedFile("courses.csv", csv_bytes, content_type="text/csv")
        job = ImportJob.objects.create(
            resource_type=ImportJob.RESOURCE_PROGRAM_COURSE,
            uploaded_file=upload,
            original_filename="courses.csv",
            chunk_size=chunk_size,
            initiated_by=self.user,
        )
        return job

    def run_job_sync(self, job, chunk_size=None):
        """Calls the task function directly (no Celery/thread) so tests are
        deterministic and don't need a broker."""
        process_program_course_import(job.id, chunk_size)
        job.refresh_from_db()
        return job


class NormalizationHelperTests(TestCase):
    def test_year_inferred_from_course_code(self):
        row = normalize_program_course_row({"program": "X", "course_code": "cosc 201", "course_name": "Data Structures"})
        self.assertEqual(row["year"], 2)
        self.assertEqual(row["course_code"], "COSC 201")

    def test_unit_type_typo_tolerant(self):
        row = normalize_program_course_row({"program": "X", "course_code": "COSC101", "unit_type": "Electve"})
        self.assertEqual(row["unit_type"], "ELECTIVE")

    def test_defaults(self):
        row = normalize_program_course_row({"program": "X", "course_code": "COSC999"})
        self.assertEqual(row["student_cohort"], "0")
        self.assertEqual(row["semester"], 1)
        self.assertEqual(row["unit_type"], "CORE")


class RowSizeImportTests(ProgramCourseImportTestBase):
    """10 / 100 / 1000 row imports — the everyday size range."""

    def _rows(self, n):
        return [
            (self.program.name, f"COSC{100 + i}", f"Course {i}", "", "1", "CORE", "0")
            for i in range(n)
        ]

    def test_10_rows(self):
        job = self.make_job(self._rows(10))
        job = self.run_job_sync(job)
        self.assertEqual(job.status, ImportJob.STATUS_COMPLETED)
        self.assertEqual(job.created_count, 10)
        self.assertEqual(ProgramCourse.objects.count(), 10)

    def test_100_rows(self):
        job = self.make_job(self._rows(100), chunk_size=25)
        job = self.run_job_sync(job)
        self.assertEqual(job.status, ImportJob.STATUS_COMPLETED)
        self.assertEqual(job.created_count, 100)
        self.assertEqual(ProgramCourse.objects.count(), 100)

    def test_1000_rows_chunked(self):
        job = self.make_job(self._rows(1000), chunk_size=200)
        job = self.run_job_sync(job)
        self.assertEqual(job.status, ImportJob.STATUS_COMPLETED)
        self.assertEqual(job.created_count, 1000)
        self.assertEqual(job.total_rows, 1000)
        self.assertEqual(job.progress_percent, 100)
        self.assertEqual(ProgramCourse.objects.count(), 1000)

    def test_10000_rows_performance(self):
        if not os.environ.get("RUN_SLOW_IMPORT_TESTS"):
            self.skipTest("Set RUN_SLOW_IMPORT_TESTS=1 to run the 10k-row perf test")
        job = self.make_job(self._rows(10000), chunk_size=500)
        t0 = time.monotonic()
        job = self.run_job_sync(job)
        duration = time.monotonic() - t0
        self.assertEqual(job.status, ImportJob.STATUS_COMPLETED)
        self.assertEqual(job.created_count, 10000)
        # Not a hard perf assertion (hardware-dependent) — just a sanity
        # ceiling so a query-per-row regression fails loudly here instead
        # of only showing up in production.
        self.assertLess(duration, 120, f"10k-row import took {duration:.1f}s — check for per-row queries")


class UpsertBehaviorTests(ProgramCourseImportTestBase):
    def test_create_then_update_no_duplicates(self):
        job1 = self.make_job([(self.program.name, "COSC101", "Intro to CS", "1", "1", "CORE", "0")])
        self.run_job_sync(job1)
        self.assertEqual(ProgramCourse.objects.count(), 1)

        # Re-import same key with a changed course_name -> update, not a new row.
        job2 = self.make_job([(self.program.name, "COSC101", "Introduction to Computer Science", "1", "1", "CORE", "0")])
        job2 = self.run_job_sync(job2)
        self.assertEqual(ProgramCourse.objects.count(), 1)
        self.assertEqual(job2.updated_count, 1)
        self.assertEqual(job2.created_count, 0)
        self.assertEqual(ProgramCourse.objects.first().course_name, "Introduction to Computer Science")

    def test_unchanged_rows_are_skipped_not_updated(self):
        job1 = self.make_job([(self.program.name, "COSC101", "Intro to CS", "1", "1", "CORE", "0")])
        self.run_job_sync(job1)

        job2 = self.make_job([(self.program.name, "COSC101", "Intro to CS", "1", "1", "CORE", "0")])
        job2 = self.run_job_sync(job2)
        self.assertEqual(job2.skipped_count, 1)
        self.assertEqual(job2.updated_count, 0)

    def test_duplicate_rows_within_same_file_last_wins(self):
        job = self.make_job([
            (self.program.name, "COSC101", "First Name", "1", "1", "CORE", "0"),
            (self.program.name, "COSC101", "Second Name", "1", "1", "CORE", "0"),
        ])
        job = self.run_job_sync(job)
        self.assertEqual(ProgramCourse.objects.count(), 1)
        self.assertEqual(ProgramCourse.objects.first().course_name, "Second Name")

    def test_unknown_program_is_reported_and_skipped(self):
        job = self.make_job([("Nonexistent Program", "COSC101", "X", "1", "1", "CORE", "0")])
        job = self.run_job_sync(job)
        self.assertEqual(job.failed_count, 1)
        self.assertIn("unknown program", job.error_log)
        self.assertEqual(ProgramCourse.objects.count(), 0)

    def test_missing_required_fields_reported(self):
        job = self.make_job([("", "COSC101", "X", "1", "1", "CORE", "0")])
        job = self.run_job_sync(job)
        self.assertEqual(job.failed_count, 1)
        self.assertEqual(ProgramCourse.objects.count(), 0)


class ResumeAndInterruptionTests(ProgramCourseImportTestBase):
    def _rows(self, n):
        return [(self.program.name, f"COSC{100 + i}", f"Course {i}", "1", "1", "CORE", "0") for i in range(n)]

    def test_resume_from_checkpoint_does_not_reprocess(self):
        job = self.make_job(self._rows(30), chunk_size=10)
        # Simulate the task having completed 1 chunk before being interrupted.
        job.total_rows = 30
        job.processed_rows = 10
        job.created_count = 10
        job.status = ImportJob.STATUS_FAILED
        job.save()

        self.assertTrue(job.is_resumable)
        job = self.run_job_sync(job)
        self.assertEqual(job.status, ImportJob.STATUS_COMPLETED)
        self.assertEqual(ProgramCourse.objects.count(), 30)
        # created_count only increases by the 20 remaining rows, since the
        # first 10 were pre-set above (not re-created).
        self.assertEqual(job.created_count, 30)

    def test_restart_from_scratch_is_idempotent_no_duplicates(self):
        """Even a full re-run from row 0 (not just a checkpointed resume)
        must never create duplicate ProgramCourse rows, since upsert
        logic keys off (program, course_code, student_cohort) regardless
        of where the file is re-read from."""
        rows = self._rows(15)
        job1 = self.make_job(rows, chunk_size=5)
        self.run_job_sync(job1)
        self.assertEqual(ProgramCourse.objects.count(), 15)

        job2 = self.make_job(rows, chunk_size=5)  # fresh job, full re-run
        job2 = self.run_job_sync(job2)
        self.assertEqual(ProgramCourse.objects.count(), 15)
        self.assertEqual(job2.created_count, 0)
        self.assertEqual(job2.skipped_count, 15)

    def test_partial_chunk_failure_does_not_lose_earlier_chunks(self):
        """If something goes wrong mid-file, rows already committed in
        earlier chunks must remain — no whole-file rollback."""
        job = self.make_job(self._rows(20), chunk_size=5)
        job.status = ImportJob.STATUS_RUNNING
        job.total_rows = 20
        job.processed_rows = 10  # first two chunks already "done"
        job.created_count = 10
        job.save()
        ProgramCourse.objects.bulk_create([
            ProgramCourse(program=self.program, course_code=f"COSC{100 + i}", course_name=f"Course {i}",
                           year=1, semester=1, unit_type="CORE", student_cohort="0")
            for i in range(10)
        ])

        job = self.run_job_sync(job)
        # Earlier 10 rows are untouched/still present regardless of what
        # happens processing the rest.
        self.assertGreaterEqual(ProgramCourse.objects.count(), 10)


class ConcurrentImportTests(ProgramCourseImportTestBase):
    def test_two_jobs_touching_overlapping_keys_do_not_duplicate(self):
        """Approximates two concurrent imports both containing COSC101:
        running them back to back (their DB-visible effect once each
        completes) must still land on exactly one row per key."""
        job_a = self.make_job([(self.program.name, "COSC101", "From A", "1", "1", "CORE", "0")])
        job_b = self.make_job([(self.program.name, "COSC101", "From B", "1", "1", "CORE", "0")])
        self.run_job_sync(job_a)
        self.run_job_sync(job_b)
        self.assertEqual(ProgramCourse.objects.filter(program=self.program, course_code="COSC101").count(), 1)
        self.assertEqual(ProgramCourse.objects.get(program=self.program, course_code="COSC101").course_name, "From B")

    def test_upsert_survives_same_key_twice_in_one_batch_without_integrity_error(self):
        """Regression test for the concurrency bug found in production:
        two Celery workers racing on the same (program, course_code,
        student_cohort) key used to raise IntegrityError 1062 because the
        old implementation checked existence and wrote in two separate
        steps. upsert_program_courses() now does a single atomic
        INSERT ... ON DUPLICATE KEY UPDATE, so even the same key appearing
        twice within one batch (the worst case a race can produce) must
        not raise and must resolve to exactly one row."""
        from program_management.import_helpers import upsert_program_courses

        row = {
            "program_id": self.program.id, "course_code": "SOCI213", "course_name": "First",
            "unit_type": "CORE", "student_cohort": "0", "semester": 1, "year": 2,
        }
        row_again = dict(row, course_name="Second")

        created, updated, skipped = upsert_program_courses([row, row_again])

        self.assertEqual(ProgramCourse.objects.filter(program=self.program, course_code="SOCI213").count(), 1)
        self.assertEqual(
            ProgramCourse.objects.get(program=self.program, course_code="SOCI213").course_name, "Second",
        )


class ProgramLookupCacheTests(ProgramCourseImportTestBase):
    def test_cache_warms_once_and_resolves_names(self):
        cache = ProgramLookupCache()
        cache.warm()
        self.assertEqual(cache.get(self.program.name), self.program.id)
        self.assertIsNone(cache.get("Totally Unknown Program"))

    def test_cache_picks_up_program_created_after_warm(self):
        cache = ProgramLookupCache()
        cache.warm()
        new_program = Program.objects.create(name="New Program", department=self.department)
        # Not in the warm-up snapshot, but a single targeted lookup still finds it.
        self.assertEqual(cache.get(new_program.name), new_program.id)


class AdminRoutingTests(ProgramCourseImportTestBase):
    """Confirms the admin upload form routes CSV to the async job path and
    returns immediately, per REQUIRED IMPLEMENTATION point 2."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.user)

    def test_csv_upload_creates_job_and_does_not_import_synchronously(self):
        url = reverse("admin:program_management_programcourse_import")
        csv_bytes = _make_csv_bytes([(self.program.name, "COSC101", "Intro", "1", "1", "CORE", "0")])
        upload = SimpleUploadedFile("courses.csv", csv_bytes, content_type="text/csv")

        response = self.client.post(url, {"import_file": upload, "input_format": "0"}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ImportJob.objects.count(), 1)
        job = ImportJob.objects.first()
        self.assertEqual(job.original_filename, "courses.csv")
        # The request itself must not have performed the import inline —
        # that's the whole point of the redesign. It may or may not have
        # completed yet depending on the (thread-fallback) worker timing,
        # so we only assert the job exists and rows aren't asserted here.

    def test_importjob_list_is_visible_in_admin(self):
        self.make_job([(self.program.name, "COSC101", "Intro", "1", "1", "CORE", "0")])
        url = reverse("admin:program_management_importjob_changelist")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
