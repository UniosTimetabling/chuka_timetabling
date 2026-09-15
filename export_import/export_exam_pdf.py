from collections import defaultdict, OrderedDict
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML
from timetable.models import (
    ExamTimetable,
    MergedCourseGroup,
    SharedVenueExamGroup,
    LabExamTimetable,
)
from .models import TimetablePdfTemplate

def export_exam_pdf(request):
    """
    Generate unified Exam + Lab Exam timetable PDF.
    Uses dynamic template configuration from TimetablePdfTemplate model.
    """
    # Get the template configuration
    template_config = TimetablePdfTemplate.get_template()
    
    # Get header data from template configuration
    header_data = template_config.get_header_data(timetable_type="Unified Examination")
    
    # Get reference number
    now = timezone.now()
    reference_number = template_config.get_reference_number(now.strftime("%d%m%Y"))
    
    # Get page header
    page_header = template_config.get_page_header(
        timetable_type="Unified Examination",
        page=1,
        total_pages=1  # You might want to calculate this dynamically
    )
    
    # Get footer data
    footer_data = template_config.get_footer_data(
        prepared_by="J.K. KATHURU",
        director_initials="JKK",
        sub_director_initials="gmk"
    )
    
    # ---------- MAIN EXAMS ----------
    # Use ExamTimetable (published).  MergedCourseGroup rows now carry
    # exam_timetable_entry FK — use get_active_timetable_entry() to resolve
    # the correct timetable record regardless of published state.
    exams = (
        ExamTimetable.objects.select_related("course_allocation__lecturer", "venue")
        .all()
        .order_by("venue", "date", "start_time")
    )

    # ---------- LAB EXAMS ----------
    lab_exams = (
        LabExamTimetable.objects.select_related(
            "lab_allocation__program_course",
            "lab_allocation__lecturer",
            "lab_venue",
        )
        .all()
        .order_by("lab_venue", "date", "start_time")
    )

    # ---------- SHARED VENUE EXAM GROUPS (published) ----------
    # SharedVenueExamGroup carries exam_timetable_entry FK (published state).
    # We read these to include extra course codes sharing a room at the same slot.
    shared_venue_groups = (
        SharedVenueExamGroup.objects
        .filter(published=True)
        .select_related("venue", "exam_timetable_entry")
        .prefetch_related("course_allocations__lecturer")
        .order_by("date", "start_time")
    )

    # ---------- COLLECT TIME SLOTS ----------
    all_slots = (
        sorted(
            {(e.start_time, e.end_time) for e in exams}
            | {(le.start_time, le.end_time) for le in lab_exams}
            | {(svg.start_time, svg.end_time) for svg in shared_venue_groups}
        )
    )

    timeslots = [
        {
            "start": st.strftime("%H:%M"),
            "end": en.strftime("%H:%M"),
            "key": f"{st.strftime('%H:%M')}_{en.strftime('%H:%M')}",
        }
        for st, en in all_slots
    ]

    # ---------- COLLECT UNIQUE DAY-DATE PAIRS ----------
    seen_pairs = set()
    day_date_pairs = []
    for e in exams:
        pair = (e.day, e.date)
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            day_date_pairs.append({"day": e.day, "date": e.date})
    for le in lab_exams:
        pair = (le.day, le.date)
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            day_date_pairs.append({"day": le.day, "date": le.date})
    for svg in shared_venue_groups:
        pair = (svg.day, svg.date)
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            day_date_pairs.append({"day": svg.day, "date": svg.date})

    # Sort by date
    day_date_pairs.sort(key=lambda d: d["date"] or timezone.now().date())

    # ---------- HELPER ----------
    def venue_label(obj):
        v = getattr(obj, "venue", None) or getattr(obj, "lab_venue", None)
        if not v:
            return "Unassigned"
        return getattr(v, "code", getattr(v, "name", str(v)))

    # ---------- BUILD INDEX ----------
    index = defaultdict(list)
    seen_venues = OrderedDict()
    handled_groups = set()
    dedup_set = set()

    # ---- MAIN EXAMS ----
    for e in exams:
        vk = venue_label(e)
        seen_venues[vk] = True
        slot_key = f"{e.start_time.strftime('%H:%M')}_{e.end_time.strftime('%H:%M')}"
        dedup_key = (e.course_allocation.course_code, vk, slot_key, e.date)
        if dedup_key in dedup_set:
            continue
        dedup_set.add(dedup_key)

        entry = {
            "course_code": e.course_allocation.course_code,
            "course_name": e.course_allocation.course_name,
            "lecturer": getattr(
                e.course_allocation.lecturer, "display_name", "Unassigned"
            ),
            "category": "Exam",
            "day": e.day,
        }
        index[(vk, slot_key, e.date)].append(entry)

        # Handle published merged group.
        # Use exam_timetable_entry FK (new model field) to resolve the
        # published timetable record; fall back to base_course filter.
        merged_group = (
            MergedCourseGroup.objects.filter(
                base_course=e.course_allocation,
                published=True,
            )
            .prefetch_related("merged_courses__lecturer")
            .first()
        )
        if merged_group and merged_group.id not in handled_groups:
            handled_groups.add(merged_group.id)
            for mc in merged_group.merged_courses.all():
                dedup_key = (mc.course_code, vk, slot_key, e.date)
                if dedup_key not in dedup_set:
                    dedup_set.add(dedup_key)
                    index[(vk, slot_key, e.date)].append(
                        {
                            "course_code": mc.course_code,
                            "course_name": mc.course_name,
                            "lecturer": getattr(
                                mc.lecturer, "display_name", "Unassigned"
                            ),
                            "category": "Exam (Merged)",
                            "day": e.day,
                        }
                    )

    # ---- SHARED VENUE EXAM GROUPS ----
    # SharedVenueExamGroup.exam_timetable_entry links to the published
    # ExamTimetable row.  Iterate course_allocations to add each code.
    for svg in shared_venue_groups:
        vk = venue_label(svg)
        seen_venues[vk] = True
        slot_key = f"{svg.start_time.strftime('%H:%M')}_{svg.end_time.strftime('%H:%M')}"
        for ca in svg.course_allocations.all():
            dedup_key = (ca.course_code, vk, slot_key, svg.date)
            if dedup_key in dedup_set:
                continue
            dedup_set.add(dedup_key)
            index[(vk, slot_key, svg.date)].append(
                {
                    "course_code": ca.course_code,
                    "course_name": ca.course_name,
                    "lecturer": getattr(
                        ca.lecturer, "display_name", "Unassigned"
                    ),
                    "category": "Exam (Shared Venue)",
                    "day": svg.day,
                }
            )

    # ---- LAB EXAMS ----
    for le in lab_exams:
        vk = venue_label(le)
        seen_venues[vk] = True
        slot_key = f"{le.start_time.strftime('%H:%M')}_{le.end_time.strftime('%H:%M')}"
        course = le.lab_allocation.program_course
        dedup_key = (course.course_code, vk, slot_key, le.date)
        if dedup_key in dedup_set:
            continue
        dedup_set.add(dedup_key)

        index[(vk, slot_key, le.date)].append(
            {
                "course_code": course.course_code,
                "course_name": course.course_name,
                "lecturer": getattr(
                    le.lab_allocation.lecturer, "display_name", "Unassigned"
                ),
                "category": "Lab Exam",
                "day": le.day,
            }
        )

    # ---------- BUILD MATRIX ----------
    matrix = []
    for vk in seen_venues.keys():
        cells = []
        for slot in timeslots:
            per_day = []
            for dd in day_date_pairs:
                entries = index.get((vk, slot["key"], dd["date"]), [])
                per_day.append(
                    {
                        "day": dd["day"],
                        "date": dd["date"],
                        "entries": entries,
                    }
                )
            cells.append({"slot": slot, "per_day": per_day})
        matrix.append({"venue_key": vk, "cells": cells})

    # ---------- CONTEXT ----------
    context = {
        # Dynamic template data
        "header_data": header_data,
        "reference_number": reference_number,
        "page_header": page_header,
        "footer_data": footer_data,
        
        # Timetable data
        "timeslots": timeslots,
        "day_date_pairs": day_date_pairs,
        "matrix": matrix,
        "now": now,
    }

    # ---------- GENERATE PDF ----------
    html_string = render_to_string(
        "export/timetable_exam_main_pdf.html", context, request=request
    )
    pdf_file = HTML(
        string=html_string, base_url=request.build_absolute_uri("/")
    ).write_pdf()

    response = HttpResponse(pdf_file, content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="exam_lab_timetable.pdf"'
    return response