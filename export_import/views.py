# ------------------ Export Main PDF ------------------ #
from datetime import datetime, timedelta
from collections import defaultdict, OrderedDict
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.templatetags.static import static
from weasyprint import HTML
from timetable.models import Timetable, AutoMergedExamGroup, SchedulerConfig, LabTimetable,ExamTimetable, MergedCourseGroup, LabExamTimetable
import csv

def export_main_pdf(request):
    """
    Export the published main teaching timetable (and merged/lab data) to PDF.
    Only includes AutoMergedExamGroup rows where published=True.
    """
    logo_url = request.build_absolute_uri(static('images/chuka.png'))

    # --- Scheduler Config Defaults ---
    config = SchedulerConfig.objects.first()
    start_time = config.start_time if config else datetime.strptime("07:00", "%H:%M").time()
    end_time = config.end_time if config else datetime.strptime("19:00", "%H:%M").time()
    slot_size = config.slot_size if config else 3  # hours per slot

    # --- Time Slots ---
    time_slots = []
    current = datetime.combine(datetime.today(), start_time)
    end_dt = datetime.combine(datetime.today(), end_time)
    while current < end_dt:
        next_slot = current + timedelta(hours=slot_size)
        if next_slot > end_dt:
            next_slot = end_dt
        time_slots.append(f"{current.strftime('%H:%M')} - {next_slot.strftime('%H:%M')}")
        current = next_slot

    # --- Querysets ---
    timetable_qs = Timetable.objects.select_related(
        "course_allocation__lecturer"
    ).all().order_by("venue", "start_time", "end_time", "day")

    # ✅ Only published merged groups
    merged_qs = AutoMergedExamGroup.objects.select_related(
        "venue", "base_course__lecturer"
    ).prefetch_related("merged_courses").filter(published=True).order_by("venue", "start_time", "end_time", "date")

    lab_qs = LabTimetable.objects.select_related(
        "lab_allocation__program_course", "lab_venue"
    ).all().order_by("lab_venue__code", "day", "start_time")

    if not timetable_qs.exists() and not merged_qs.exists() and not lab_qs.exists():
        return HttpResponse("No timetable data found.", content_type="text/plain")

    # --- Helper Functions ---
    DAYS_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

    def slot_label(st, en):
        return f"{st.strftime('%H:%M')} - {en.strftime('%H:%M')}"

    def venue_key_from_obj(obj):
        v = getattr(obj, "venue", None)
        if not v:
            v = getattr(obj, "lab_venue", None)
        if not v:
            return "Unknown"
        return getattr(v, "code", str(v))

    # --- Group regular timetable ---
    timetable_by_slot = defaultdict(list)
    for e in timetable_qs:
        timetable_by_slot[slot_label(e.start_time, e.end_time)].append(e)

    merged_by_slot = defaultdict(list)
    for m in merged_qs:
        if m.start_time and m.end_time:
            merged_by_slot[slot_label(m.start_time, m.end_time)].append(m)

    # --- Collect all venues ---
    venues_all = set()
    for e in timetable_qs:
        venues_all.add(venue_key_from_obj(e))
    for m in merged_qs:
        venues_all.add(venue_key_from_obj(m))
    for l in lab_qs:
        venues_all.add(venue_key_from_obj(l))

    # --- Build Day Grids ---
    day_grids = OrderedDict()
    for day in DAYS_ORDER:
        venues_set = {venue_key_from_obj(e) for e in timetable_qs if e.day.lower() == day.lower()}
        venues_set.update({venue_key_from_obj(m) for m in merged_qs if str(m.date).lower() == day.lower()})
        if not venues_set:
            venues_set = venues_all or {"Unassigned"}

        rows = []
        for vk in sorted(venues_set):
            cells = []
            for slot in time_slots:
                cell_entries = []
                # Regular classes
                for e in timetable_by_slot.get(slot, []):
                    if e.day.lower() != day.lower() or venue_key_from_obj(e) != vk:
                        continue
                    code = getattr(e.course_allocation, "course_code", "")
                    lecturer = getattr(e.course_allocation.lecturer, "display_name", "Unassigned") if getattr(e.course_allocation, "lecturer", None) else "Unassigned"
                    cell_entries.append({"course_code": code, "lecturer": lecturer})

                # ✅ Merged entries (only published ones)
                for m in merged_by_slot.get(slot, []):
                    if str(m.date).lower() != day.lower() or venue_key_from_obj(m) != vk:
                        continue
                    merged_code = m.merged_code or (getattr(m.base_course, "course_code", "") if m.base_course else "")
                    lecturer = getattr(m.base_course.lecturer, "display_name", "Unassigned") if getattr(m, "base_course", None) else "Unassigned"
                    member_codes = [getattr(mc, "course_code", "") for mc in m.merged_courses.all()]
                    display_code = merged_code or ", ".join(member_codes)
                    cell_entries.append({"course_code": display_code, "lecturer": lecturer})

                cells.append({"entries": cell_entries or []})
            rows.append({"venue_key": vk, "cells": cells})
        day_grids[day] = rows

    # --- Lab Timetable Section ---
    lab_table = [
        {
            "course_code": getattr(l.lab_allocation.program_course, "course_code", ""),
            "lab": getattr(l.lab_venue, "code", ""),
            "day": l.day,
            "start": l.start_time.strftime("%H:%M"),
            "end": l.end_time.strftime("%H:%M"),
        }
        for l in lab_qs
    ]

    from timetable.official_timetables import define_semester
    semester = define_semester()

    # --- Context ---
    context = {
        "title": "Teaching Timetable (Published)",
        "logo_url": logo_url,
        "day_grids": day_grids,
        "time_slots": time_slots,
        "day_order": DAYS_ORDER,
        "merged_table": [
            {
                "merged_code": m.merged_code or (getattr(m.base_course, "course_code", "") if m.base_course else ""),
                "total_students": m.total_students,
                "venue": venue_key_from_obj(m),
                "date": m.date,
                "start": m.start_time.strftime("%H:%M") if m.start_time else "",
                "end": m.end_time.strftime("%H:%M") if m.end_time else "",
                "courses": ", ".join([getattr(mc, "course_code", "") for mc in m.merged_courses.all()]),
                "lecturer": getattr(m.base_course.lecturer, "display_name", "Unassigned") if getattr(m, "base_course", None) else "Unassigned",
            }
            for m in merged_qs
        ],
        "lab_table": lab_table,
        "now": timezone.now(),
        "semester": semester,
    }

    # --- Render PDF ---
    html_string = render_to_string("export/timetable_main_pdf.html", context, request=request)
    pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()
    response = HttpResponse(pdf_file, content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="timetable.pdf"'
    return response





def export_exam_pdf(request):
    """
    Generate unified Exam + Lab Exam timetable PDF.
    Includes only published merged groups,
    fixes day/date collision (e.g., Oct 2 vs Oct 9),
    deduplicates overlapping entries,
    and merges lab + main exams into a single matrix.
    """
    logo_url = request.build_absolute_uri(static("images/chuka.png"))

    # ---------- MAIN EXAMS ----------
    exams = (
        ExamTimetable.objects.select_related("course_allocation__lecturer")
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

    # ---------- COLLECT TIME SLOTS ----------
    all_slots = sorted(
        {(e.start_time, e.end_time) for e in exams}
        | {(le.start_time, le.end_time) for le in lab_exams}
    )

    timeslots = [
        {
            "start": st.strftime("%H:%M"),
            "end": en.strftime("%H:%M"),
            "key": f"{st.strftime('%H:%M')}_{en.strftime('%H:%M')}",
        }
        for st, en in all_slots
    ]

    # ---------- COLLECT UNIQUE DAY–DATE PAIRS ----------
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

    # sort purely by date (so Oct 2 < Oct 9)
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
    dedup_set = set()  # prevent double-listing same course in same slot/day

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

        # handle published merged group (only once)
        merged_group = (
            MergedCourseGroup.objects.filter(base_course=e.course_allocation, published=True)
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
                            "category": "Exam (Merged, Published)",
                            "day": e.day,
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
        "title": "Unified Examination Timetable (Main + Lab, Published Merged Groups)",
        "logo_url": logo_url,
        "timeslots": timeslots,
        "day_date_pairs": day_date_pairs,
        "matrix": matrix,
        "now": timezone.now(),
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


def export_exam_csv(request):
    """
    Export ExamTimetable to CSV including only published merged courses (no duplicates).
    """
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="exam_timetable.csv"'
    writer = csv.writer(response)
    writer.writerow(
        ["Course Code", "Course Name", "Lecturer", "Venue", "Day", "Date", "Start", "End"]
    )

    entries = (
        ExamTimetable.objects.select_related("course_allocation__lecturer")
        .order_by("date", "start_time")
    )
    handled_groups = set()
    scheduled_course_ids = set(entries.values_list("course_allocation_id", flat=True))

    for e in entries:
        ca = e.course_allocation

        # venue display
        v = getattr(e, "venue", None)
        venue_display = "Unassigned"
        if v:
            if hasattr(v, "code"):
                venue_display = v.code
            elif hasattr(v, "name"):
                venue_display = v.name
            else:
                venue_display = str(v)

        # base course
        writer.writerow(
            [
                ca.course_code,
                ca.course_name,
                ca.lecturer.display_name if ca.lecturer else "Unassigned",
                venue_display,
                e.day,
                e.date.strftime("%Y-%m-%d"),
                e.start_time.strftime("%H:%M"),
                e.end_time.strftime("%H:%M"),
            ]
        )

        # merged ones only if published and not already scheduled
        merged_group = (
            MergedCourseGroup.objects.filter(base_course=ca, published=True)
            .first()
        )
        if merged_group and merged_group.id not in handled_groups:
            handled_groups.add(merged_group.id)
            for mc in merged_group.merged_courses.all():
                if mc.id not in scheduled_course_ids:  # avoid duplicate
                    writer.writerow(
                        [
                            mc.course_code,
                            mc.course_name,
                            mc.lecturer.display_name if mc.lecturer else "Unassigned",
                            venue_display,
                            e.day,
                            e.date.strftime("%Y-%m-%d"),
                            e.start_time.strftime("%H:%M"),
                            e.end_time.strftime("%H:%M"),
                        ]
                    )

    return response




def export_main_csv(request):
    """
    Export unified CSV of normal timetable rows + published auto-merged exam groups.
    Avoids duplicates and preserves all data consistency.
    """
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="timetable.csv"'
    writer = csv.writer(response)

    writer.writerow([
        "Course Code",
        "Course Name",
        "Lecturer",
        "Venue",
        "Day",
        "Start",
        "End",
        "Type"
    ])

    handled_courses = set()

    # -------- NORMAL TIMETABLE ROWS --------
    qs = Timetable.objects.select_related("course_allocation__lecturer", "venue").all()
    for e in qs:
        ca = getattr(e, "course_allocation", None)
        if not ca:
            continue

        course_code = getattr(ca, "course_code", "")
        course_name = getattr(ca, "course_name", "")
        handled_courses.add(course_code)

        # Lecturer
        lecturer = "Unassigned"
        if getattr(ca, "lecturer", None):
            if hasattr(ca.lecturer, "display_name"):
                lecturer = ca.lecturer.display_name
            elif hasattr(ca.lecturer, "get_full_name"):
                lecturer = ca.lecturer.get_full_name()
            elif hasattr(ca.lecturer, "name"):
                lecturer = ca.lecturer.name

        # Venue
        v = getattr(e, "venue", None)
        if v:
            venue_display = getattr(v, "code", getattr(v, "name", str(v)))
        else:
            venue_display = "Unassigned"

        writer.writerow([
            course_code,
            course_name,
            lecturer,
            venue_display,
            e.day or "",
            e.start_time.strftime("%H:%M") if e.start_time else "",
            e.end_time.strftime("%H:%M") if e.end_time else "",
            "Regular Timetable"
        ])

    # -------- PUBLISHED AUTO-MERGED GROUPS --------
    merged_qs = AutoMergedExamGroup.objects.filter(published=True).select_related("base_course", "venue").prefetch_related("merged_courses__lecturer")

    for group in merged_qs:
        base = group.base_course
        if not base:
            continue

        base_code = getattr(base, "course_code", "N/A")
        base_name = getattr(base, "course_name", "N/A")

        # Venue
        v = getattr(group, "venue", None)
        venue_display = getattr(v, "code", getattr(v, "name", str(v))) if v else "Unassigned"

        # Base course lecturer
        lecturer = getattr(base.lecturer, "display_name", "Unassigned") if getattr(base, "lecturer", None) else "Unassigned"

        # Include base row
        writer.writerow([
            base_code,
            base_name,
            lecturer,
            venue_display,
            getattr(group, "date", ""),
            group.start_time.strftime("%H:%M") if group.start_time else "",
            group.end_time.strftime("%H:%M") if group.end_time else "",
            "Merged Exam (Published)"
        ])

        # Include all merged courses
        for mc in group.merged_courses.all():
            mc_code = getattr(mc, "course_code", "N/A")
            mc_name = getattr(mc, "course_name", "N/A")
            if mc_code in handled_courses:
                continue  # avoid duplicates

            mc_lecturer = getattr(mc.lecturer, "display_name", "Unassigned") if getattr(mc, "lecturer", None) else "Unassigned"

            writer.writerow([
                mc_code,
                mc_name,
                mc_lecturer,
                venue_display,
                getattr(group, "date", ""),
                group.start_time.strftime("%H:%M") if group.start_time else "",
                group.end_time.strftime("%H:%M") if group.end_time else "",
                "Merged Exam (Published)"
            ])
            handled_courses.add(mc_code)

    return response

