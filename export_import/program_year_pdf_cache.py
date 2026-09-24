"""
export_import/program_year_pdf_cache.py
========================================

Pre-generated, cached timetable PDFs scoped by (department, program, year).

Why this exists: on the REMOTE side of the host/remote sync feature
(core.models.SyncNode, export_import.sync_engine), students/staff hit a
public "give me the timetable for my program + year" URL a lot. Building
the PDF from scratch on every single hit is slow and pointless when the
underlying data has usually not changed since the last time someone asked.

So instead:
  * Every (department, program, year) combination gets AT MOST one
    GeneratedTimetablePDF row per timetable_type ("class" / "exam").
  * `get_or_build()` is what the public view calls: if a ready row with
    an existing file is on disk, it hands that back immediately. If not
    (first ever request for that scope, or the cache was invalidated),
    it builds the PDF right then (synchronously, scoped to just that one
    combination, so it's still fast), saves it, and hands it back — the
    view never has to link to a file that doesn't exist yet.
  * `invalidate_scope()` deletes the cached row (+ file) for one scope
    the moment something affecting it changes locally — see
    export_import/pdf_cache_signals.py.
  * `regenerate_all_in_background()` sweeps every (department, program,
    year) combination found in ProgramCourse and rebuilds anything
    missing/invalidated, off the request thread. This is what warms the
    whole cache after a host pushes a batch of changes via receive_sync,
    so the FIRST student to ask afterwards doesn't pay the build cost.
"""
import hashlib
import io
import logging
import threading
import time

from django.core.cache import cache
from django.db.models import Q
from django.core.files.base import ContentFile
from django.utils import timezone

logger = logging.getLogger(__name__)

# Guards against piling up multiple overlapping full-sweep background
# threads if several syncs / edits land in quick succession.
_sweep_lock = threading.Lock()
_sweep_in_progress = False


# ─────────────────────────────────────────────────────────────
#  Scope discovery
# ─────────────────────────────────────────────────────────────

def _all_scopes():
    """Every distinct (department_id, program_id, year) combination that
    actually exists in the curriculum, derived from ProgramCourse."""
    from program_management.models import ProgramCourse

    return (
        ProgramCourse.objects
        .values_list("program__department_id", "program_id", "year")
        .distinct()
        .order_by("program__department_id", "program_id", "year")
    )


# ─────────────────────────────────────────────────────────────
#  PDF building (no request/user context needed — safe to call
#  from a background thread or a public, unauthenticated view)
# ─────────────────────────────────────────────────────────────

def _scope_queryset_and_hash(model, department, program, year, alloc_prefix):
    """Shared scope filter for Timetable/ExamTimetable rows: the
    allocation's program must be this program, its (or its program's)
    department must be this department, and the matching ProgramCourse
    row's year must be this year. Returns (queryset, content_hash) where
    the hash is over just the primary keys + a cheap "updated" signal
    (row count + max pk) — good enough to detect "nothing changed" for
    the purposes of skipping a rebuild during a sweep.
    """
    p = alloc_prefix
    qs = model.objects.filter(
        **{f"{p}program_id": program.id},
        **{f"{p}program_course__year": year},
        venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
    ).filter(
        Q(**{f"{p}department_id": department.id}) |
        Q(**{f"{p}program__department_id": department.id})
    ).select_related(
        f"{p}lecturer", f"{p}program", f"{p}department", "venue",
    ).order_by("day", "start_time", "venue__code")

    ids = list(qs.values_list("id", flat=True))
    digest = hashlib.sha256(f"{len(ids)}:{max(ids) if ids else 0}:{sum(ids)}".encode()).hexdigest()
    return qs, digest


def _build_class_pdf(department, program, year):
    """Returns (pdf_bytes, filename, row_count, content_hash)."""
    from timetable.models import Timetable
    from timetable.analysis_reports import (
        _get_template_config, _styles, _letterhead, _standard_table_style,
        _day_grid_tables, _footer_factory,
    )
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table
    from reportlab.lib.units import inch
    from course_allocation.models import CourseAllocation
    from datetime import datetime

    template_config = _get_template_config()
    styles = _styles()
    ref = template_config.get_reference_number(datetime.now().strftime("%d-%b-%Y").upper())
    subtitle = f"{department.name} — {program.name} — Year {year}"

    qs, content_hash = _scope_queryset_and_hash(
        Timetable, department, program, year, alloc_prefix="course_allocation__"
    )

    elements = []
    _letterhead(elements, styles, template_config, "CLASS TIMETABLE", subtitle,
                ref=ref, date_str=datetime.now().strftime("%d-%b-%Y").upper())

    day_tables = _day_grid_tables(qs, styles, table_style_fn=_standard_table_style, rich=True)
    row_count = qs.count()

    if not day_tables:
        elements.append(Paragraph("No scheduled entries found for this program/year yet.", styles['RCell']))
    else:
        for day_label, day_table in day_tables:
            elements.append(Paragraph(day_label.upper(), styles['RSection']))
            elements.append(day_table)
            elements.append(Spacer(1, 10))

    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"Total scheduled entries: {row_count}", styles['RCellBold']))

    scope_allocations = CourseAllocation.objects.filter(
        program_id=program.id, program_course__year=year,
    ).filter(Q(department_id=department.id) | Q(program__department_id=department.id))
    scheduled_ids = set(qs.values_list("course_allocation_id", flat=True))
    unscheduled = scope_allocations.exclude(id__in=scheduled_ids).select_related("lecturer")

    if unscheduled.exists():
        elements.append(Spacer(1, 14))
        elements.append(Paragraph("UNSCHEDULED COURSES", styles['RSection']))
        rows = [[Paragraph('Course Code', styles['RHead']), Paragraph('Course Name', styles['RHead']),
                 Paragraph('Lecturer', styles['RHead']), Paragraph('Students', styles['RHead'])]]
        for alloc in unscheduled.order_by("course_code"):
            rows.append([
                Paragraph(alloc.course_code or '-', styles['RCellBold']),
                Paragraph(alloc.course_name or '-', styles['RCell']),
                Paragraph(getattr(alloc.lecturer, 'name', 'Unassigned'), styles['RCell']),
                Paragraph(str(alloc.number_of_students or 0), styles['RCell']),
            ])
        table = Table(rows, colWidths=[1.2 * inch, 3.4 * inch, 2.6 * inch, 1.0 * inch], repeatRows=1)
        table.setStyle(_standard_table_style())
        elements.append(table)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        topMargin=0.6 * inch, bottomMargin=0.6 * inch, leftMargin=0.55 * inch, rightMargin=0.55 * inch,
    )
    on_page = _footer_factory(template_config, ref, compiled_by="System (cached)")
    doc.build(elements, onFirstPage=on_page, onLaterPages=on_page)
    buffer.seek(0)

    safe = f"{program.name}_Year{year}_class_timetable.pdf".replace(' ', '_').replace('/', '-')
    return buffer.read(), safe, row_count, content_hash


def _build_exam_pdf(department, program, year):
    """Returns (pdf_bytes, filename, row_count, content_hash)."""
    from timetable.models import ExamTimetable
    from timetable.analysis_reports import (
        _get_template_config, _styles, _letterhead, _standard_table_style, _footer_factory,
    )
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table
    from reportlab.lib.units import inch
    from collections import defaultdict
    from datetime import datetime

    template_config = _get_template_config()
    styles = _styles()
    ref = template_config.get_reference_number(datetime.now().strftime("%d-%b-%Y").upper())
    subtitle = f"{department.name} — {program.name} — Year {year}"

    qs, content_hash = _scope_queryset_and_hash(
        ExamTimetable, department, program, year, alloc_prefix="course_allocation__"
    )
    entries = list(qs.order_by("date", "start_time"))
    row_count = len(entries)

    elements = []
    _letterhead(elements, styles, template_config, "EXAMINATION TIMETABLE", subtitle,
                ref=ref, date_str=datetime.now().strftime("%d-%b-%Y").upper())

    if not entries:
        elements.append(Paragraph("No exam entries found for this program/year yet.", styles['RCell']))
    else:
        by_date = defaultdict(list)
        for e in entries:
            by_date[e.date].append(e)
        for exam_date in sorted(by_date):
            elements.append(Paragraph(exam_date.strftime("%A, %d %B %Y").upper(), styles['RSection']))
            rows = [[Paragraph('Time', styles['RHead']), Paragraph('Course Code', styles['RHead']),
                     Paragraph('Course Name', styles['RHead']), Paragraph('Lecturer', styles['RHead']),
                     Paragraph('Venue', styles['RHead']), Paragraph('Students', styles['RHead'])]]
            for entry in sorted(by_date[exam_date], key=lambda e: e.start_time):
                alloc = entry.course_allocation
                rows.append([
                    Paragraph(f"{entry.start_time.strftime('%H:%M')}-{entry.end_time.strftime('%H:%M')}", styles['RCell']),
                    Paragraph(alloc.course_code if alloc else '-', styles['RCellBold']),
                    Paragraph(alloc.course_name if alloc else '-', styles['RCell']),
                    Paragraph(getattr(alloc.lecturer, 'name', 'Unassigned') if alloc else 'Unassigned', styles['RCell']),
                    Paragraph(entry.venue.code if entry.venue else 'N/A', styles['RCell']),
                    Paragraph(str(alloc.number_of_students or 0) if alloc else '0', styles['RCell']),
                ])
            table = Table(rows, colWidths=[0.9 * inch, 1.0 * inch, 2.6 * inch, 2.0 * inch, 0.9 * inch, 0.8 * inch], repeatRows=1)
            table.setStyle(_standard_table_style())
            elements.append(table)
            elements.append(Spacer(1, 12))

    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"Total exam entries: {row_count}", styles['RCellBold']))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        topMargin=0.6 * inch, bottomMargin=0.6 * inch, leftMargin=0.55 * inch, rightMargin=0.55 * inch,
    )
    on_page = _footer_factory(template_config, ref, compiled_by="System (cached)")
    doc.build(elements, onFirstPage=on_page, onLaterPages=on_page)
    buffer.seek(0)

    safe = f"{program.name}_Year{year}_exam_timetable.pdf".replace(' ', '_').replace('/', '-')
    return buffer.read(), safe, row_count, content_hash


_BUILDERS = {"class": _build_class_pdf, "exam": _build_exam_pdf}


# ─────────────────────────────────────────────────────────────
#  Cache read/write
# ─────────────────────────────────────────────────────────────

def build_and_store(department, program, year, timetable_type):
    """Builds one scope's PDF right now and (re)writes its cache row.
    Safe to call from a request (single scope = fast) or a background
    sweep. Returns the saved GeneratedTimetablePDF row."""
    from export_import.models import GeneratedTimetablePDF

    row, _ = GeneratedTimetablePDF.objects.get_or_create(
        department=department, program=program, year=year, timetable_type=timetable_type,
    )
    row.is_ready = False
    row.save(update_fields=["is_ready"])

    try:
        pdf_bytes, filename, row_count, content_hash = _BUILDERS[timetable_type](department, program, year)
        if row.pdf_file:
            row.pdf_file.delete(save=False)
        row.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
        row.row_count = row_count
        row.content_hash = content_hash
        row.generated_at = timezone.now()
        row.error_message = ""
        row.is_ready = True
        row.save()
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "Failed to build %s PDF for dept=%s program=%s year=%s",
            timetable_type, department.name, program.name, year,
        )
        row.error_message = str(e)[:2000]
        row.is_ready = False
        row.save(update_fields=["error_message", "is_ready"])
    return row


def get_or_build(department_id, program_id, year, timetable_type):
    """What the public serving view calls. Returns a ready
    GeneratedTimetablePDF row with a file on disk, or None if the scope
    doesn't exist at all in the curriculum (→ view should 404, never
    hand out a dead link)."""
    from export_import.models import GeneratedTimetablePDF
    from program_management.models import Program, ProgramCourse
    from department_management.models import Department

    if timetable_type not in _BUILDERS:
        return None
    if not ProgramCourse.objects.filter(program_id=program_id, year=year).exists():
        return None  # not a real scope — refuse rather than build garbage

    try:
        department = Department.objects.get(id=department_id)
        program = Program.objects.get(id=program_id)
    except (Department.DoesNotExist, Program.DoesNotExist):
        return None

    row = GeneratedTimetablePDF.objects.filter(
        department=department, program=program, year=year, timetable_type=timetable_type,
    ).first()
    if row and row.is_ready and row.pdf_file:
        return row

    # Not cached (or a previous build failed) — build it now, scoped to
    # just this one combination, so this single request stays fast.
    #
    # A popular scope (e.g. a big first-year program right after results
    # come out) can get hit by many students within the same second. If
    # the cache is cold for that exact scope, don't let every one of
    # those requests kick off its own full reportlab build in parallel —
    # on a small server that's a self-inflicted thundering herd. Only
    # the first request actually builds; the rest wait briefly for it to
    # finish and then serve the same freshly-built file.
    lock_key = f"pdfbuild:{department.id}:{program.id}:{year}:{timetable_type}"
    acquired = cache.add(lock_key, 1, timeout=60)
    if not acquired:
        for _ in range(15):  # up to ~6s
            time.sleep(0.4)
            row = GeneratedTimetablePDF.objects.filter(
                department=department, program=program, year=year, timetable_type=timetable_type,
            ).first()
            if row and row.is_ready and row.pdf_file:
                return row
        # Whoever held the lock hasn't finished (or died) — fall through
        # and build it ourselves rather than leaving the request hanging.
        acquired = cache.add(lock_key, 1, timeout=60)

    try:
        return build_and_store(department, program, year, timetable_type)
    finally:
        if acquired:
            cache.delete(lock_key)


def invalidate_scope(department_id, program_id, year, timetable_type=None):
    """Deletes the cached row(s) + file for one scope so the next
    request rebuilds it. `timetable_type=None` clears both class & exam."""
    from export_import.models import GeneratedTimetablePDF

    qs = GeneratedTimetablePDF.objects.filter(
        department_id=department_id, program_id=program_id, year=year,
    )
    if timetable_type:
        qs = qs.filter(timetable_type=timetable_type)
    for row in qs:
        if row.pdf_file:
            row.pdf_file.delete(save=False)
        row.delete()


def invalidate_all():
    """Wipes the entire cache — used after a host push where we can't
    cheaply tell which scopes were touched."""
    from export_import.models import GeneratedTimetablePDF

    for row in GeneratedTimetablePDF.objects.all():
        if row.pdf_file:
            row.pdf_file.delete(save=False)
    GeneratedTimetablePDF.objects.all().delete()


# ─────────────────────────────────────────────────────────────
#  Background sweep — warms/repairs the whole cache
# ─────────────────────────────────────────────────────────────

def _sweep():
    from export_import.models import GeneratedTimetablePDF
    from department_management.models import Department
    from program_management.models import Program

    global _sweep_in_progress
    try:
        for dept_id, program_id, year in _all_scopes():
            try:
                department = Department.objects.get(id=dept_id)
                program = Program.objects.get(id=program_id)
            except (Department.DoesNotExist, Program.DoesNotExist):
                continue
            for ttype in _BUILDERS:
                existing = GeneratedTimetablePDF.objects.filter(
                    department=department, program=program, year=year, timetable_type=ttype,
                ).first()
                if existing and existing.is_ready and existing.pdf_file:
                    continue  # already cached — nothing to do
                build_and_store(department, program, year, ttype)
    except Exception:  # noqa: BLE001
        logger.exception("Program/year PDF cache sweep failed")
    finally:
        with _sweep_lock:
            _sweep_in_progress = False


def regenerate_all_in_background():
    """Kicks off `_sweep()` on a daemon thread. If a sweep is already
    running, this is a no-op — the running sweep will pick up anything
    invalidated after it started on its NEXT trigger, and callers (sync
    receive, signals) call this cheaply/often enough that nothing gets
    permanently missed."""
    global _sweep_in_progress
    with _sweep_lock:
        if _sweep_in_progress:
            return False
        _sweep_in_progress = True

    thread = threading.Thread(target=_sweep, daemon=True)
    thread.start()
    return True
