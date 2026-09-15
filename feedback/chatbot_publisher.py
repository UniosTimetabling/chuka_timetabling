"""
feedback/chatbot_publisher.py
══════════════════════════════
Backend logic that reads the already-built PDF data (from the publish views)
and writes it into ChatbotPublication + ChatbotTimetableEntry.

Called automatically by a post_save signal on PDFDocument.
No user action required.

Usage (called from export_import/signals.py signal handler):
    from feedback.chatbot_publisher import sync_chatbot_from_pdf_document
    sync_chatbot_from_pdf_document(pdf_document_instance)
"""

import re
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


def _get_year_from_code(course_code: str) -> str:
    """Extract year of study digit from course code, e.g. COSC102 → '1'."""
    m = re.search(r"[A-Za-z]+(\d)", course_code or "")
    return m.group(1) if m else ""


def _lecturer_display(lecturer_obj) -> str:
    if lecturer_obj is None:
        return ""
    return getattr(lecturer_obj, "display_name", None) or str(lecturer_obj)


def _time_str(t) -> str:
    if t is None:
        return ""
    return t.strftime("%I:%M %p").lstrip("0")


def sync_chatbot_from_pdf_document(pdf_doc):
    """
    Given a PDFDocument instance (export_import.models.PDFDocument), rebuild
    the ChatbotPublication + ChatbotTimetableEntry snapshot.

    Steps:
      1. Mark all previous publications of the same type/year/sem as is_latest=False
         and delete their entries (they are now stale).
      2. Create a new ChatbotPublication.
      3. Enumerate timetable rows from the DB (same logic as publish views).
      4. Bulk-create ChatbotTimetableEntry rows.
    """
    from .chatbot_models import ChatbotPublication, ChatbotTimetableEntry

    ttype     = pdf_doc.document_type   # "EXAM" or "REGULAR"
    acad_year = pdf_doc.academic_year
    semester  = pdf_doc.semester

    # ── 1. Supersede previous publications ───────────────────────────────────
    old_pubs = ChatbotPublication.objects.filter(
        timetable_type=ttype,
        academic_year=acad_year,
        semester=semester,
        is_latest=True,
    )
    old_pub_ids = list(old_pubs.values_list("id", flat=True))
    if old_pub_ids:
        ChatbotTimetableEntry.objects.filter(publication_id__in=old_pub_ids).delete()
        old_pubs.update(is_latest=False)

    # ── 2. Create new publication record ─────────────────────────────────────
    pub = ChatbotPublication.objects.create(
        timetable_type  = ttype,
        academic_year   = acad_year,
        semester        = semester,
        published_by    = pdf_doc.uploaded_by or "",
        is_latest       = True,
        pdf_document_id = pdf_doc.id,
    )

    # ── 3 + 4. Build entries depending on type ────────────────────────────────
    if ttype == "REGULAR":
        _build_regular_entries(pub)
    else:
        _build_exam_entries(pub)

    logger.info(
        "ChatbotPublication %s created for %s %s S%s — %d entries",
        pub.id, ttype, acad_year, semester,
        pub.entries.count(),
    )
    return pub


# ─────────────────────────────────────────────────────────────────────────────
#  Regular timetable builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_regular_entries(pub):
    """
    Mirror the cell-building logic from publish_regular_timetable_pdf but
    write into ChatbotTimetableEntry rows instead of a PDF.
    """
    from .chatbot_models import ChatbotTimetableEntry
    from timetable.models import Timetable, MergedCourseGroupTimetable

    entries_to_create = []

    timetables = (
        Timetable.objects
        .select_related(
            "course_allocation__lecturer",
            "course_allocation__program",
            "course_allocation__program_course",
            "venue",
        )
        .order_by("day", "start_time")
    )

    # Build cell map: (day, start_time, end_time, venue_code) → set of codes + meta
    cell_meta = {}      # (day, st, et, vc) → {course_code, course_name, program, dept, lecturer, year}
    cell_codes = defaultdict(set)  # (day, st, et, vc) → set of all codes in cell (merged)

    for t in timetables:
        if not t.day or not t.start_time or not t.end_time:
            continue

        ca    = t.course_allocation
        venue = t.venue
        vc    = getattr(venue, "code", None) or getattr(venue, "name", None) or "Unassigned"
        vn    = getattr(venue, "name", vc)
        key   = (t.day, t.start_time, t.end_time, vc)

        prog_name = ""
        dept_name = ""
        if ca.program:
            prog_name = ca.program.name
        if hasattr(ca, "department") and ca.department:
            dept_name = ca.department.name

        # Resolve course_name: prefer program_course, fall back to allocation
        cname = ca.course_name or ""
        if ca.program_course:
            cname = ca.program_course.course_name or cname

        cell_meta[key] = {
            "day":         t.day,
            "start_time":  t.start_time,
            "end_time":    t.end_time,
            "venue_code":  vc,
            "venue_name":  vn,
            "course_code": ca.course_code,
            "course_name": cname,
            "program_name": prog_name,
            "department_name": dept_name,
            "year_of_study": _get_year_from_code(ca.course_code),
            "lecturer_name": _lecturer_display(ca.lecturer),
        }
        cell_codes[key].add(ca.course_code)

        # Add merged courses
        for mg in MergedCourseGroupTimetable.objects.filter(
            base_course=ca, published=True
        ).prefetch_related("merged_courses"):
            for mc in mg.merged_courses.all():
                if mc.pk != ca.pk:
                    cell_codes[key].add(mc.course_code)

    # Create one entry per (cell_key) — the primary course for that cell
    for key, meta in cell_meta.items():
        all_codes = sorted(cell_codes[key])
        entries_to_create.append(ChatbotTimetableEntry(
            publication     = pub,
            day             = meta["day"],
            date            = None,
            start_time      = meta["start_time"],
            end_time        = meta["end_time"],
            venue_code      = meta["venue_code"],
            venue_name      = meta["venue_name"],
            course_code     = meta["course_code"],
            course_name     = meta["course_name"],
            program_name    = meta["program_name"],
            department_name = meta["department_name"],
            year_of_study   = meta["year_of_study"],
            lecturer_name   = meta["lecturer_name"],
            merged_codes    = ",".join(all_codes),
        ))

    if entries_to_create:
        ChatbotTimetableEntry.objects.bulk_create(entries_to_create, batch_size=500)


# ─────────────────────────────────────────────────────────────────────────────
#  Exam timetable builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_exam_entries(pub):
    """
    Mirror the cell-building logic from publish_exam_timetable_pdf but
    write into ChatbotTimetableEntry rows.
    """
    from .chatbot_models import ChatbotTimetableEntry
    from timetable.models import (
        ExamTimetable, LabExamTimetable,
        MergedCourseGroup, SharedVenueExamGroup,
    )
    from collections import defaultdict as _dd

    entries_to_create = []
    cell_meta  = {}
    cell_codes = defaultdict(set)  # (date, st, et, vc) → set of codes

    exams = (
        ExamTimetable.objects
        .select_related("course_allocation__lecturer",
                        "course_allocation__program",
                        "course_allocation__program_course",
                        "venue")
        .order_by("date", "start_time")
    )
    lab_exams = (
        LabExamTimetable.objects
        .select_related("lab_allocation__program_course",
                        "lab_allocation__lecturer",
                        "lab_venue")
        .order_by("date", "start_time")
    )

    # Pre-fetch merged groups
    all_merged = (
        MergedCourseGroup.objects.filter(published=True)
        .select_related("base_course", "exam_timetable_entry", "venue")
        .prefetch_related("merged_courses")
    )
    groups_by_entry = _dd(list)
    groups_by_alloc = _dd(list)
    for mg in all_merged:
        if mg.exam_timetable_entry_id:
            groups_by_entry[mg.exam_timetable_entry_id].append(mg)
        if mg.base_course_id:
            groups_by_alloc[mg.base_course_id].append(mg)
        for mc in mg.merged_courses.all():
            if mc.pk != mg.base_course_id:
                groups_by_alloc[mc.pk].append(mg)

    def _add(date, st, et, vc, vn, ca, extra_code=None):
        key  = (date, st, et, vc)
        code = extra_code or ca.course_code
        cell_codes[key].add(code)
        if key not in cell_meta:
            prog_name = ca.program.name if ca.program else ""
            dept_name = ca.department.name if hasattr(ca, "department") and ca.department else ""
            cname = ca.course_name or ""
            if ca.program_course:
                cname = ca.program_course.course_name or cname
            cell_meta[key] = {
                "date":        date,
                "day":         date.strftime("%A") if date else "",
                "start_time":  st,
                "end_time":    et,
                "venue_code":  vc,
                "venue_name":  vn,
                "course_code": ca.course_code,
                "course_name": cname,
                "program_name": prog_name,
                "department_name": dept_name,
                "year_of_study": _get_year_from_code(ca.course_code),
                "lecturer_name": _lecturer_display(ca.lecturer),
            }

    for e in exams:
        if not e.date or not e.start_time or not e.end_time:
            continue
        vc = getattr(e.venue, "code", None) or getattr(e.venue, "name", None) or "Unassigned"
        vn = getattr(e.venue, "name", vc)
        ca = e.course_allocation
        _add(e.date, e.start_time, e.end_time, vc, vn, ca)

        seen = set()
        for mg in groups_by_entry.get(e.pk, []) + groups_by_alloc.get(ca.pk, []):
            if mg.pk in seen:
                continue
            seen.add(mg.pk)
            mvn  = getattr(mg.venue, "code", None) if mg.venue else None
            mkey_vc = mvn or vc
            mkey_vn = getattr(mg.venue, "name", mkey_vc) if mg.venue else vn
            mdate = mg.date or e.date
            mst   = mg.start_time or e.start_time
            met   = mg.end_time   or e.end_time
            for mc in mg.merged_courses.all():
                if mc.pk == ca.pk:
                    continue
                cell_codes[(mdate, mst, met, mkey_vc)].add(mc.course_code)

    for le in lab_exams:
        if not le.date or not le.start_time or not le.end_time:
            continue
        vc  = getattr(le.lab_venue, "code", None) or getattr(le.lab_venue, "name", None) or "Unassigned"
        vn  = getattr(le.lab_venue, "name", vc)
        pc  = le.lab_allocation.program_course
        key = (le.date, le.start_time, le.end_time, vc)
        cell_codes[key].add(pc.course_code)
        if key not in cell_meta:
            prog_name = pc.program.name if pc.program else ""
            cell_meta[key] = {
                "date": le.date,
                "day":  le.date.strftime("%A"),
                "start_time": le.start_time,
                "end_time":   le.end_time,
                "venue_code": vc,
                "venue_name": vn,
                "course_code": pc.course_code,
                "course_name": pc.course_name,
                "program_name": prog_name,
                "department_name": "",
                "year_of_study": _get_year_from_code(pc.course_code),
                "lecturer_name": _lecturer_display(le.lab_allocation.lecturer),
            }

    for svg in SharedVenueExamGroup.objects.filter(published=True).select_related(
        "venue", "exam_timetable_entry"
    ).prefetch_related("course_allocations__program", "course_allocations__program_course",
                        "course_allocations__lecturer"):
        vc  = getattr(svg.venue, "code", None) or getattr(svg.venue, "name", None) or "Unassigned"
        vn  = getattr(svg.venue, "name", vc)
        key = (svg.date, svg.start_time, svg.end_time, vc)
        for ca in svg.course_allocations.all():
            cell_codes[key].add(ca.course_code)
            if key not in cell_meta:
                prog_name = ca.program.name if ca.program else ""
                cname = ca.course_name or ""
                if ca.program_course:
                    cname = ca.program_course.course_name or cname
                cell_meta[key] = {
                    "date": svg.date,
                    "day":  svg.date.strftime("%A") if svg.date else "",
                    "start_time": svg.start_time,
                    "end_time":   svg.end_time,
                    "venue_code": vc,
                    "venue_name": vn,
                    "course_code": ca.course_code,
                    "course_name": cname,
                    "program_name": prog_name,
                    "department_name": getattr(ca.department, "name", ""),
                    "year_of_study": _get_year_from_code(ca.course_code),
                    "lecturer_name": _lecturer_display(ca.lecturer),
                }

    for key, meta in cell_meta.items():
        all_codes = sorted(cell_codes[key])
        entries_to_create.append(ChatbotTimetableEntry(
            publication     = pub,
            day             = meta["day"],
            date            = meta["date"],
            start_time      = meta["start_time"],
            end_time        = meta["end_time"],
            venue_code      = meta["venue_code"],
            venue_name      = meta["venue_name"],
            course_code     = meta["course_code"],
            course_name     = meta["course_name"],
            program_name    = meta["program_name"],
            department_name = meta["department_name"],
            year_of_study   = meta["year_of_study"],
            lecturer_name   = meta["lecturer_name"],
            merged_codes    = ",".join(all_codes),
        ))

    if entries_to_create:
        ChatbotTimetableEntry.objects.bulk_create(entries_to_create, batch_size=500)
