"""
allocation_reports/models.py
=============================
One row per *generated PDF* of a course-allocation report.

A "report" is uniquely identified by:
    scope        -> which allocation table it was built from
                     ("main", "odel", "campus", "resit")
    department   -> the department it was generated for
    campus       -> only used when scope == "campus" (a department can teach
                     the same program on more than one campus)

Every time content actually changes (a course added/removed, a lecturer
re-assigned, student numbers updated, etc.) a NEW row is created with the
next `version` number and `is_current=True`; the previous current row for
that (scope, department, campus) combination is flipped to
`is_current=False` — i.e. archived, not deleted, so history is always
browsable on the page.

Staleness detection does not depend on the source tables having an
`updated_at` column (CourseAllocation doesn't have one) — see
`allocation_reports.signature.compute_signature`, which fingerprints the
actual row contents instead.
"""
from django.conf import settings
from django.db import models


def allocation_pdf_upload_path(instance, filename):
    dept_slug = instance.department.name.replace(" ", "_").replace("/", "-")
    return (
        f"allocation_pdfs/{instance.scope}/{dept_slug}/"
        f"v{instance.version}_{filename}"
    )


class AllocationPdfRun(models.Model):
    SCOPE_MAIN = "main"
    SCOPE_ODEL = "odel"
    SCOPE_CAMPUS = "campus"
    SCOPE_RESIT = "resit"
    SCOPE_CHOICES = [
        (SCOPE_MAIN, "Main Allocation"),
        (SCOPE_ODEL, "ODeL Allocation"),
        (SCOPE_CAMPUS, "Campus Allocation"),
        (SCOPE_RESIT, "Resit Allocation"),
    ]

    STATUS_PENDING = "pending"
    STATUS_GENERATING = "generating"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"
    STATUS_NO_DATA = "no_data"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Queued"),
        (STATUS_GENERATING, "Generating"),
        (STATUS_READY, "Ready"),
        (STATUS_FAILED, "Failed"),
        (STATUS_NO_DATA, "No data yet"),
    ]

    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES, db_index=True)
    department = models.ForeignKey(
        "department_management.Department",
        on_delete=models.CASCADE,
        related_name="allocation_pdf_runs",
    )
    campus = models.ForeignKey(
        "campuses_timetable.Campus",
        on_delete=models.CASCADE,
        related_name="allocation_pdf_runs",
        null=True,
        blank=True,
        help_text="Only set when scope='campus'.",
    )

    version = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True, db_index=True)

    file = models.FileField(upload_to=allocation_pdf_upload_path, max_length=400, blank=True, null=True)
    row_count = models.PositiveIntegerField(default=0)
    signature = models.CharField(max_length=64, blank=True, default="")

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    error_message = models.TextField(blank=True, default="")

    generated_at = models.DateTimeField(null=True, blank=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_allocation_pdfs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        indexes = [
            models.Index(fields=["scope", "department", "campus", "is_current"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "department", "campus", "version"],
                name="uniq_allocation_pdf_version",
            ),
        ]

    def __str__(self):
        return f"{self.get_scope_display()} · {self.department.name} · v{self.version} ({self.status})"

    @property
    def is_ready(self):
        """True only when the DB row says READY *and* the PDF is actually
        sitting on disk (or whatever storage backend is configured).

        This used to be `status == READY and bool(self.file)` — but
        `bool(self.file)` just checks that the FileField has a *name* set,
        never that the file still exists in storage. If the media
        directory gets out of sync with the database — e.g. a DB
        backup/restore ("undo") that doesn't carry `media/` along with it,
        or someone manually clearing old PDFs off disk to save space —
        every run's `file` field still has its old path, `bool(self.file)`
        is still True, and this property kept reporting "ready" for a file
        that is a 404 to the browser: the dashboard shows a "Ready" badge
        and a working-looking download link that 404s, AND every call site
        that decides whether to (re)generate (get_or_generate_pdf's
        early-return check, queue_bulk_generation's `needs_work`) trusted
        this same flag, so a page reload or even "Generate again" was a
        no-op whenever the underlying allocation data hadn't changed since
        that broken run (same content signature -> nothing looked stale).
        Checking the storage backend directly closes that gap: a missing
        file now correctly falls through to a real regeneration.
        """
        if self.status != self.STATUS_READY or not self.file:
            return False
        # Memoize per-instance: templates (dashboard.html, cod_panel.html)
        # read is_ready 2-3x for the same row on one page render, and this
        # now costs a real storage stat() call each time.
        cached = getattr(self, "_is_ready_cache", None)
        if cached is not None:
            return cached
        try:
            result = self.file.storage.exists(self.file.name)
        except Exception:
            # Storage backend unreachable (e.g. network storage hiccup) —
            # fail closed rather than claiming a file is ready when we
            # can't actually confirm it.
            result = False
        self._is_ready_cache = result
        return result
