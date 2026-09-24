# program_management/import_helpers.py
"""
Reusable helpers for the Program Course CSV import.

This module exists so the row-normalization and upsert logic lives in
ONE place instead of being duplicated between:
  - ProgramCourseResource.before_import_row (django-import-export, still
    used for the synchronous small-file / non-CSV path in admin.py), and
  - program_management.tasks.process_program_course_import (the new
    Celery path for large CSV files).

Behavior here intentionally mirrors:
  - ProgramCourseResource.before_import_row (program_management/admin.py)
  - ProgramCourse.save() / ProgramCourse.normalize_unit_type (models.py)
so a row imported through either path ends up identical.
"""
import re
from django.utils import timezone

from .models import Program, ProgramCourse
from .code_utils import normalize_code

DEFAULT_CHUNK_SIZE = 500

# Fields compared to decide whether an existing row needs a bulk_update,
# and (minus 'updated_at') the fields passed to bulk_update() itself.
COMPARABLE_FIELDS = ("course_name", "year", "semester", "unit_type")

_YEAR_DIGIT_RE = re.compile(r"\d{3,4}")


def normalize_program_course_row(row):
    """
    Pure function: take a raw CSV row (dict of strings) and return a
    cleaned dict ready for lookups/writes. Never touches the database.

    Mirrors ProgramCourseResource.before_import_row's cleaning rules
    (normalize course_code via the shared code_utils.normalize_code --
    strip/upper AND space-insert so 'BCOM112' and 'BCOM 112' land on the
    exact same row instead of two -- upper+validate unit_type, default
    cohort '0', default semester 1) plus ProgramCourse.save()'s
    year-inference regex (first 3-4 digit run in the course code ->
    first digit = year, clamped to 1-6), since bulk_create/bulk_update
    bypass save() entirely and would otherwise silently lose that
    behaviour.
    """
    program_name = (row.get("program") or "").strip()
    course_code = normalize_code(row.get("course_code") or "")
    course_name = (row.get("course_name") or "").strip()
    unit_type = ProgramCourse.normalize_unit_type(row.get("unit_type"))
    student_cohort = (row.get("student_cohort") or "").strip() or "0"

    semester_raw = row.get("semester")
    try:
        semester = int(semester_raw) if semester_raw not in (None, "") else 1
    except (TypeError, ValueError):
        semester = 1
    if semester not in (1, 2):
        semester = 1

    year = None
    year_raw = row.get("year")
    if year_raw not in (None, ""):
        try:
            candidate = int(year_raw)
            if 1 <= candidate <= 6:
                year = candidate
        except (TypeError, ValueError):
            year = None

    if year is None:
        match = _YEAR_DIGIT_RE.search(course_code)
        if match:
            inferred = int(match.group(0)[0])
            year = inferred if 1 <= inferred <= 6 else 1
        else:
            year = 1

    return {
        "program_name": program_name,
        "course_code": course_code,
        "course_name": course_name,
        "unit_type": unit_type,
        "student_cohort": student_cohort,
        "semester": semester,
        "year": year,
    }


class ProgramLookupCache:
    """
    Resolves Program name -> id once per import instead of once per row.
    A file with 10,000 ProgramCourse rows against ~30 programs would
    otherwise issue up to 10,000 identical SELECTs; this caches all
    programs up front (one query) and falls back to a single targeted
    query only for a name that wasn't there at warm-up time (e.g. a
    Program created by a different admin tab mid-import).
    """

    def __init__(self):
        self._by_name = {}
        self._warmed = False

    def warm(self):
        if self._warmed:
            return
        self._by_name = dict(Program.objects.values_list("name", "id"))
        self._warmed = True

    def get(self, name):
        if name in self._by_name:
            return self._by_name[name]
        program_id = Program.objects.filter(name=name).values_list("id", flat=True).first()
        if program_id is not None:
            self._by_name[name] = program_id
        return program_id


def resolve_and_validate_rows(raw_rows, program_cache, row_offset=0):
    """
    Normalize + resolve FKs for a batch of raw CSV rows.

    Returns (resolved_rows, error_messages). `resolved_rows` is a list of
    dicts with program_id already resolved, deduplicated by
    (program_id, course_code, student_cohort) keeping the LAST occurrence
    (matches "last row wins" semantics of a normal CSV re-import, and
    avoids IntegrityError from bulk_create() being handed two rows for
    the same unique_together key in a single batch).
    """
    errors = []
    by_key = {}
    order = []

    for i, raw in enumerate(raw_rows):
        row_num = row_offset + i + 2  # +2: 1-indexed rows, plus the header line
        try:
            norm = normalize_program_course_row(raw)
        except Exception as exc:  # defensive: a single malformed row must never abort the chunk
            errors.append(f"Row {row_num}: could not parse row ({exc})")
            continue

        if not norm["program_name"] or not norm["course_code"]:
            errors.append(f"Row {row_num}: missing required program or course_code — skipped")
            continue

        program_id = program_cache.get(norm["program_name"])
        if not program_id:
            errors.append(f"Row {row_num}: unknown program '{norm['program_name']}' — skipped")
            continue

        norm["program_id"] = program_id
        norm["row_num"] = row_num
        key = (program_id, norm["course_code"], norm["student_cohort"])
        if key not in by_key:
            order.append(key)
        by_key[key] = norm  # last occurrence wins

    resolved_rows = [by_key[k] for k in order]
    return resolved_rows, errors


def upsert_program_courses(resolved_rows):
    """
    Upsert one already-normalized, already-deduplicated batch of
    ProgramCourse rows. Returns (created_count, updated_count,
    skipped_count) where skipped = existing row whose fields are already
    identical (django-import-export's skip_unchanged behaviour).

    Uses bulk_create(update_conflicts=True) — INSERT ... ON DUPLICATE KEY
    UPDATE on MySQL — instead of a separate "does it exist? then
    bulk_create or bulk_update" pair of steps. That distinction matters
    under concurrency: with two DB calls, two chunks racing on the same
    (program, course_code, student_cohort) key (e.g. two Celery workers
    processing overlapping/duplicate files at once) can both see "doesn't
    exist yet" and both attempt an INSERT, and the second one raises
    IntegrityError 1062. A single atomic upsert has no such window — the
    second writer's INSERT converts into an UPDATE at the database level
    instead of crashing the chunk.

    created_count/updated_count are still classified via one SELECT up
    front, for reporting only; under a genuine concurrent-write race that
    classification can occasionally be off by a row (e.g. something
    reported as "created" that a parallel job's write actually beat it
    to), but the WRITE itself is always correct and never raises — no
    duplicates, no crashed chunks.
    """
    if not resolved_rows:
        return 0, 0, 0

    program_ids = {r["program_id"] for r in resolved_rows}
    codes = {r["course_code"] for r in resolved_rows}

    existing_qs = ProgramCourse.objects.filter(
        program_id__in=program_ids, course_code__in=codes,
    ).only("id", "program_id", "course_code", "student_cohort", "created_at", *COMPARABLE_FIELDS)

    existing_map = {
        (row.program_id, row.course_code, row.student_cohort): row for row in existing_qs
    }

    now = timezone.now()
    to_write = []
    created_count = 0
    updated_count = 0
    skipped_count = 0

    for r in resolved_rows:
        key = (r["program_id"], r["course_code"], r["student_cohort"])
        existing = existing_map.get(key)

        if existing is None:
            created_count += 1
            created_at = now
        else:
            changed = any(getattr(existing, field) != r[field] for field in COMPARABLE_FIELDS)
            if not changed:
                skipped_count += 1
                continue
            updated_count += 1
            created_at = existing.created_at

        # Built fresh (no pk) regardless of create/update — bulk_create's
        # upsert path relies on the DB-level unique constraint to decide
        # INSERT vs UPDATE, not on whether this Python object has a pk set.
        to_write.append(ProgramCourse(
            program_id=r["program_id"],
            course_code=r["course_code"],
            course_name=r["course_name"],
            year=r["year"],
            semester=r["semester"],
            unit_type=r["unit_type"],
            student_cohort=r["student_cohort"],
            created_at=created_at,
            updated_at=now,
        ))

    if to_write:
        # bulk_create bypasses ProgramCourse.save(), so the normalization
        # it would have done (unit_type fuzzy-matching, year inference) has
        # already been applied above via normalize_program_course_row().
        #
        # NOTE (MySQL): unique_fields is intentionally NOT passed — MySQL's
        # ON DUPLICATE KEY UPDATE always targets whichever unique/primary
        # key was violated, it can't be told which constraint to prefer,
        # and Django's MySQL backend rejects update_conflicts calls that
        # try to specify one.
        ProgramCourse.objects.bulk_create(
            to_write,
            update_conflicts=True,
            update_fields=[*COMPARABLE_FIELDS, "updated_at"],
            batch_size=500,
        )

    return created_count, updated_count, skipped_count
