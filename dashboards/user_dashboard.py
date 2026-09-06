# ----------------- USER DASHBOARD -----------------
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role, get_user_roles
from django.shortcuts import render
from core.models import OrgRole
from course_allocation.models import CourseAllocation
from lecturer_portal.models import Lecturer
from timetable.models import (
    Timetable, ExamTimetable, AutoMergedExamGroup,
    MergedCourseGroup, SharedVenueExamGroup, ExamSchedulerConfig,
    LabTimetable, LabExamTimetable,
)
from odel_system.models import ODELTimetable, ODELExamTimetable
from campuses_timetable.models import CampusTimetable, CampusExamTimetable
from collections import defaultdict
from datetime import datetime, time
from django.db.models import Prefetch, Q, Exists, OuterRef
from department_management.models import Department

WEEKDAY_ORDER = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]

def _fmt_time(t):
    try:
        return t.strftime("%H:%M")
    except Exception:
        return str(t)

def _get_course_family_code(course_code):
    """
    Extract the base course family code from a course code.
    Example: "MAT101A" -> "MAT101", "ENG201B" -> "ENG201"
    """
    if not course_code:
        return ""

    import re
    match = re.match(r'^([A-Za-z]+\s*\d+)[A-Za-z]*$', str(course_code).strip())
    if match:
        return match.group(1).strip().upper()

    match = re.match(r'^([A-Za-z]+)[\s\-]*(\d+)', str(course_code).strip())
    if match:
        return f"{match.group(1).upper()}{match.group(2)}"

    return str(course_code).strip().upper()

def _get_regular_timetable_data(lecturer=None, search_query=""):
    """
    Get regular timetable data with auto-merged groups (same logic as timetable_panel)
    """
    timetable_qs = Timetable.objects.select_related(
        'course_allocation',
        'course_allocation__lecturer',
        'course_allocation__program',
        'course_allocation__department',
        'venue'
    ).order_by("day", "start_time")

    if lecturer:
        timetable_qs = timetable_qs.filter(course_allocation__lecturer=lecturer)

    if search_query:
        timetable_qs = timetable_qs.filter(course_allocation__course_code__icontains=search_query)

    published_merged_groups = AutoMergedExamGroup.objects.filter(published=True).prefetch_related(
        Prefetch(
            'merged_courses',
            queryset=CourseAllocation.objects.select_related('lecturer', 'program', 'department')
        )
    )

    if lecturer:
        published_merged_groups = published_merged_groups.filter(
            Q(base_course__lecturer=lecturer) |
            Q(merged_courses__lecturer=lecturer)
        ).distinct()

    merged_base_ids = set(published_merged_groups.values_list('base_course_id', flat=True))
    merged_member_ids = set()
    for group in published_merged_groups:
        merged_member_ids.update(group.merged_courses.values_list('id', flat=True))

    all_merged_ids = merged_base_ids | merged_member_ids

    timetable_by_day = defaultdict(list)

    for t in timetable_qs:
        if t.course_allocation_id in all_merged_ids:
            continue

        timetable_by_day[t.day].append({
            "type": "regular",
            "entry": t,
            "venue": t.venue.code if t.venue else "TBA",
            "start_time": _fmt_time(t.start_time),
            "end_time": _fmt_time(t.end_time),
            "course_code": t.course_allocation.course_code,
            "course_name": t.course_allocation.course_name,
            "lecturer": t.course_allocation.lecturer.display_name if t.course_allocation.lecturer else "Unassigned",
            "program": t.course_allocation.program.name if t.course_allocation.program else "N/A",
            "department": t.course_allocation.department.name if t.course_allocation.department else "N/A",
            "students": t.course_allocation.number_of_students or 0,
            "day": t.day,
            "is_merged": False
        })

    for group in published_merged_groups:
        all_courses = [group.base_course] + list(group.merged_courses.all())

        if lecturer:
            all_courses = [c for c in all_courses if c.lecturer == lecturer]
            if not all_courses:
                continue

        base_timetable = Timetable.objects.filter(
            course_allocation=group.base_course
        ).first()

        if not base_timetable:
            for course in all_courses:
                base_timetable = Timetable.objects.filter(
                    course_allocation=course
                ).first()
                if base_timetable:
                    break

        if not base_timetable:
            continue

        day = base_timetable.day
        start_time = _fmt_time(base_timetable.start_time)
        end_time = _fmt_time(base_timetable.end_time)
        venue = base_timetable.venue.code if base_timetable.venue else "TBA"

        course_list = []
        for course in all_courses:
            course_list.append({
                "id": course.id,
                "code": course.course_code,
                "name": course.course_name,
                "lecturer": course.lecturer.display_name if course.lecturer else "Unassigned",
                "program": course.program.name if course.program else "N/A",
                "students": course.number_of_students or 0
            })

        timetable_by_day[day].append({
            "type": "merged",
            "group": group,
            "venue": venue,
            "start_time": start_time,
            "end_time": end_time,
            "courses": course_list,
            "total_students": sum(c["students"] for c in course_list),
            "day": day,
            "is_merged": True
        })

    days = []
    for day_name in WEEKDAY_ORDER:
        entries = timetable_by_day.get(day_name)
        if not entries:
            continue

        entries.sort(key=lambda e: (
            datetime.strptime(e["start_time"], "%H:%M") if isinstance(e["start_time"], str) else datetime.strptime("00:00", "%H:%M"),
            datetime.strptime(e["end_time"], "%H:%M") if isinstance(e["end_time"], str) else datetime.strptime("00:00", "%H:%M")
        ))

        seen_slots = []
        for e in entries:
            slot = (e["start_time"], e["end_time"])
            if slot not in seen_slots:
                seen_slots.append(slot)

        seen_venues = []
        for e in entries:
            v = e["venue"]
            if v not in seen_venues:
                seen_venues.append(v)

        lookup = {}
        for e in entries:
            slot = (e["start_time"], e["end_time"])
            venue = e["venue"]
            lookup[(venue, slot)] = e

        rows = []
        for venue in seen_venues:
            cells = []
            for slot in seen_slots:
                cells.append(lookup.get((venue, slot)))
            rows.append({
                "venue": venue,
                "cells": cells,
                "is_merged": any(cell and cell.get("is_merged") for cell in cells if cell)
            })

        days.append({
            "name": day_name,
            "timeslots": seen_slots,
            "rows": rows,
            "entries": entries
        })

    return days


def _get_exam_timetable_data(lecturer=None, search_query=""):
    """
    Get exam timetable data with merged groups and shared venue groups
    """
    try:
        config = ExamSchedulerConfig.objects.get(id=1)
    except ExamSchedulerConfig.DoesNotExist:
        config = None

    exam_qs = ExamTimetable.objects.select_related(
        'course_allocation',
        'course_allocation__lecturer',
        'course_allocation__program',
        'course_allocation__department',
        'venue'
    ).order_by("date", "start_time")

    if lecturer:
        exam_qs = exam_qs.filter(course_allocation__lecturer=lecturer)

    if search_query:
        exam_qs = exam_qs.filter(course_allocation__course_code__icontains=search_query)

    merged_groups = MergedCourseGroup.objects.filter(published=True).select_related(
        'base_course', 'venue', 'base_course__lecturer',
        'base_course__program', 'base_course__department'
    ).prefetch_related(
        Prefetch(
            'merged_courses',
            queryset=CourseAllocation.objects.select_related(
                'lecturer', 'program', 'department'
            )
        )
    )

    if lecturer:
        merged_groups = merged_groups.filter(
            Q(base_course__lecturer=lecturer) |
            Q(merged_courses__lecturer=lecturer)
        ).distinct()

    shared_groups = SharedVenueExamGroup.objects.filter(published=True).prefetch_related(
        Prefetch(
            'course_allocations',
            queryset=CourseAllocation.objects.select_related(
                'lecturer', 'program', 'department'
            )
        )
    )

    if lecturer:
        shared_groups = shared_groups.filter(
            course_allocations__lecturer=lecturer
        ).distinct()

    exam_by_date = defaultdict(list)

    for exam in exam_qs:
        exam_date = exam.date
        if exam_date:
            exam_by_date[exam_date].append({
                "type": "regular",
                "exam": exam,
                "venue": exam.venue.code if exam.venue else "TBA",
                "start_time": _fmt_time(exam.start_time),
                "end_time": _fmt_time(exam.end_time),
                "course_code": exam.course_allocation.course_code,
                "course_name": exam.course_allocation.course_name,
                "lecturer": exam.course_allocation.lecturer.display_name if exam.course_allocation.lecturer else "Unassigned",
                "program": exam.course_allocation.program.name if exam.course_allocation.program else "N/A",
                "department": exam.course_allocation.department.name if exam.course_allocation.department else "N/A",
                "students": exam.course_allocation.number_of_students or 0,
                "day": exam.day,
                "date": exam_date,
                "is_merged": False,
                "is_shared": False
            })

    for group in merged_groups:
        exam_date = group.date
        if exam_date:
            all_courses = [group.base_course] + list(group.merged_courses.all())

            if lecturer:
                all_courses = [c for c in all_courses if c.lecturer == lecturer]
                if not all_courses:
                    continue

            seen_ids = set()
            unique_courses = []
            for course in all_courses:
                if course.id not in seen_ids:
                    seen_ids.add(course.id)
                    unique_courses.append(course)

            course_list = []
            for course in unique_courses:
                course_list.append({
                    "id": course.id,
                    "code": course.course_code,
                    "name": course.course_name,
                    "lecturer": course.lecturer.display_name if course.lecturer else "Unassigned",
                    "program": course.program.name if course.program else "N/A",
                    "department": course.department.name if course.department else "N/A",
                    "students": course.number_of_students or 0
                })

            day_name = exam_date.strftime("%A") if exam_date else "Unknown"

            exam_by_date[exam_date].append({
                "type": "merged",
                "group": group,
                "venue": group.venue.code if group.venue else "TBA",
                "start_time": _fmt_time(group.start_time) if group.start_time else "TBA",
                "end_time": _fmt_time(group.end_time) if group.end_time else "TBA",
                "courses": course_list,
                "total_students": sum(c["students"] for c in course_list),
                "day": day_name,
                "date": exam_date,
                "is_merged": True,
                "is_shared": False
            })

    for group in shared_groups:
        exam_date = group.date
        if exam_date:
            if lecturer:
                courses = group.course_allocations.filter(lecturer=lecturer)
                if not courses.exists():
                    continue
                course_list = []
                seen_ids = set()
                for course in courses:
                    if course.id not in seen_ids:
                        seen_ids.add(course.id)
                        course_list.append({
                            "id": course.id,
                            "code": course.course_code,
                            "name": course.course_name,
                            "lecturer": course.lecturer.display_name if course.lecturer else "Unassigned",
                            "program": course.program.name if course.program else "N/A",
                            "department": course.department.name if course.department else "N/A",
                            "students": course.number_of_students or 0
                        })
            else:
                courses = group.course_allocations.all()
                course_list = []
                seen_ids = set()
                for course in courses:
                    if course.id not in seen_ids:
                        seen_ids.add(course.id)
                        course_list.append({
                            "id": course.id,
                            "code": course.course_code,
                            "name": course.course_name,
                            "lecturer": course.lecturer.display_name if course.lecturer else "Unassigned",
                            "program": course.program.name if course.program else "N/A",
                            "department": course.department.name if course.department else "N/A",
                            "students": course.number_of_students or 0
                        })

            if course_list:
                exam_by_date[exam_date].append({
                    "type": "shared",
                    "group": group,
                    "venue": group.venue.code if group.venue else "TBA",
                    "start_time": _fmt_time(group.start_time),
                    "end_time": _fmt_time(group.end_time),
                    "courses": course_list,
                    "total_students": sum(c["students"] for c in course_list),
                    "day": group.day,
                    "date": exam_date,
                    "is_merged": False,
                    "is_shared": True
                })

    sorted_dates = sorted(exam_by_date.keys())

    exam_days = []
    for date in sorted_dates:
        entries = exam_by_date[date]

        entries.sort(key=lambda x: datetime.strptime(x["start_time"], "%H:%M") if isinstance(x["start_time"], str) and x["start_time"] != "TBA" else datetime.strptime("00:00", "%H:%M"))

        seen_slots = []
        for entry in entries:
            slot = (entry["start_time"], entry["end_time"])
            if slot not in seen_slots:
                seen_slots.append(slot)

        seen_venues = []
        for entry in entries:
            if entry["venue"] not in seen_venues:
                seen_venues.append(entry["venue"])

        lookup = defaultdict(dict)
        for entry in entries:
            slot = (entry["start_time"], entry["end_time"])
            venue = entry["venue"]
            lookup[venue][slot] = entry

        rows = []
        for venue in seen_venues:
            cells = []
            for slot in seen_slots:
                cells.append(lookup[venue].get(slot))
            rows.append({
                "venue": venue,
                "cells": cells,
                "is_merged": any(cell and cell.get("is_merged") for cell in cells if cell),
                "is_shared": any(cell and cell.get("is_shared") for cell in cells if cell)
            })

        exam_days.append({
            "date": date,
            "day_name": date.strftime("%A"),
            "date_display": date.strftime("%Y-%m-%d"),
            "timeslots": seen_slots,
            "rows": rows,
            "entries": entries,
            "has_merged": any(entry.get("is_merged") for entry in entries),
            "has_shared": any(entry.get("is_shared") for entry in entries)
        })

    return exam_days


def _get_lab_timetable_data(lecturer=None, search_query=""):
    """
    Get lab (class) timetable data from LabTimetable, grouped by weekday.
    LabAllocation only has FKs: program_course, lecturer.
    Program/department are accessed via program_course.
    """
    lab_qs = LabTimetable.objects.select_related(
        'lab_allocation',
        'lab_allocation__program_course',
        'lab_allocation__lecturer',
        'lab_venue',
    ).order_by("day", "start_time")

    if lecturer:
        lab_qs = lab_qs.filter(lab_allocation__lecturer=lecturer)

    if search_query:
        lab_qs = lab_qs.filter(
            lab_allocation__program_course__course_code__icontains=search_query
        )

    lab_by_day = defaultdict(list)
    for entry in lab_qs:
        alloc = entry.lab_allocation
        pc = alloc.program_course if alloc else None
        # program / department live on ProgramCourse, not LabAllocation directly
        program_name = getattr(getattr(pc, 'program', None), 'name', None) or "N/A"
        dept_name = getattr(getattr(pc, 'department', None), 'name', None) or "N/A"
        lab_by_day[entry.day].append({
            "type": "regular",
            "entry": entry,
            "venue": entry.lab_venue.code if entry.lab_venue else "TBA",
            "start_time": _fmt_time(entry.start_time),
            "end_time": _fmt_time(entry.end_time),
            "course_code": pc.course_code if pc else "N/A",
            "course_name": pc.course_name if pc else "N/A",
            "lecturer": alloc.lecturer.display_name if alloc and alloc.lecturer else "Unassigned",
            "program": program_name,
            "department": dept_name,
            "students": getattr(alloc, 'number_of_students', 0) or 0,
            "day": entry.day,
            "is_merged": False,
        })

    days = []
    for day_name in WEEKDAY_ORDER:
        entries = lab_by_day.get(day_name)
        if not entries:
            continue

        entries.sort(key=lambda e: datetime.strptime(e["start_time"], "%H:%M"))

        seen_slots = []
        for e in entries:
            slot = (e["start_time"], e["end_time"])
            if slot not in seen_slots:
                seen_slots.append(slot)

        seen_venues = []
        for e in entries:
            if e["venue"] not in seen_venues:
                seen_venues.append(e["venue"])

        lookup = {}
        for e in entries:
            lookup[(e["venue"], (e["start_time"], e["end_time"]))] = e

        rows = []
        for venue in seen_venues:
            cells = [lookup.get((venue, slot)) for slot in seen_slots]
            rows.append({"venue": venue, "cells": cells, "is_merged": False})

        days.append({
            "name": day_name,
            "timeslots": seen_slots,
            "rows": rows,
            "entries": entries,
        })

    return days


def _get_lab_exam_timetable_data(lecturer=None, search_query=""):
    """
    Get lab exam timetable data from LabExamTimetable, grouped by date.
    LabAllocation only has FKs: program_course, lecturer.
    """
    lab_exam_qs = LabExamTimetable.objects.select_related(
        'lab_allocation',
        'lab_allocation__program_course',
        'lab_allocation__lecturer',
        'lab_venue',
    ).order_by("date", "start_time")

    if lecturer:
        lab_exam_qs = lab_exam_qs.filter(lab_allocation__lecturer=lecturer)

    if search_query:
        lab_exam_qs = lab_exam_qs.filter(
            lab_allocation__program_course__course_code__icontains=search_query
        )

    by_date = defaultdict(list)
    for entry in lab_exam_qs:
        alloc = entry.lab_allocation
        pc = alloc.program_course if alloc else None
        program_name = getattr(getattr(pc, 'program', None), 'name', None) or "N/A"
        by_date[entry.date].append({
            "type": "regular",
            "entry": entry,
            "venue": entry.lab_venue.code if entry.lab_venue else "TBA",
            "start_time": _fmt_time(entry.start_time),
            "end_time": _fmt_time(entry.end_time),
            "course_code": pc.course_code if pc else "N/A",
            "course_name": pc.course_name if pc else "N/A",
            "lecturer": alloc.lecturer.display_name if alloc and alloc.lecturer else "Unassigned",
            "program": program_name,
            "students": getattr(alloc, 'number_of_students', 0) or 0,
            "day": entry.day,
            "date": entry.date,
            "is_merged": False,
            "is_shared": False,
        })

    exam_days = []
    for date in sorted(by_date.keys()):
        entries = by_date[date]
        entries.sort(key=lambda x: datetime.strptime(x["start_time"], "%H:%M"))

        seen_slots = []
        for e in entries:
            slot = (e["start_time"], e["end_time"])
            if slot not in seen_slots:
                seen_slots.append(slot)

        seen_venues = []
        for e in entries:
            if e["venue"] not in seen_venues:
                seen_venues.append(e["venue"])

        lookup = defaultdict(dict)
        for e in entries:
            lookup[e["venue"]][(e["start_time"], e["end_time"])] = e

        rows = []
        for venue in seen_venues:
            cells = [lookup[venue].get(slot) for slot in seen_slots]
            rows.append({"venue": venue, "cells": cells, "is_merged": False, "is_shared": False})

        exam_days.append({
            "date": date,
            "day_name": date.strftime("%A"),
            "date_display": date.strftime("%Y-%m-%d"),
            "timeslots": seen_slots,
            "rows": rows,
            "entries": entries,
            "has_merged": False,
            "has_shared": False,
        })

    return exam_days


def _get_odel_timetable_data(lecturer=None, search_query=""):
    """
    Get ODEL class timetable data from ODELTimetable, grouped by date.
    ODELTimetable links to ODELCourseAllocation → program_course and lecturer.
    """
    odel_qs = ODELTimetable.objects.select_related(
        'course_allocation',
        'course_allocation__program_course',
        'course_allocation__lecturer',
        'venue',
    ).order_by("date", "start_time")

    if lecturer:
        odel_qs = odel_qs.filter(course_allocation__lecturer=lecturer)

    if search_query:
        odel_qs = odel_qs.filter(
            course_allocation__program_course__course_code__icontains=search_query
        )

    by_date = defaultdict(list)
    for entry in odel_qs:
        alloc = entry.course_allocation
        by_date[entry.date].append({
            "type": "regular",
            "entry": entry,
            "venue": entry.venue.code if entry.venue else "TBA",
            "start_time": _fmt_time(entry.start_time),
            "end_time": _fmt_time(entry.end_time),
            "course_code": alloc.course_code,
            "course_name": alloc.course_name,
            "lecturer": alloc.lecturer.display_name if alloc.lecturer else "Unassigned",
            "program": alloc.program.name if alloc.program else "N/A",
            "students": alloc.number_of_students or 0,
            "date": entry.date,
            "is_merged": False,
            "is_shared": False,
        })

    days = []
    for date in sorted(by_date.keys()):
        entries = by_date[date]
        entries.sort(key=lambda x: datetime.strptime(x["start_time"], "%H:%M"))

        seen_slots = []
        for e in entries:
            slot = (e["start_time"], e["end_time"])
            if slot not in seen_slots:
                seen_slots.append(slot)

        seen_venues = []
        for e in entries:
            if e["venue"] not in seen_venues:
                seen_venues.append(e["venue"])

        lookup = defaultdict(dict)
        for e in entries:
            lookup[e["venue"]][(e["start_time"], e["end_time"])] = e

        rows = []
        for venue in seen_venues:
            cells = [lookup[venue].get(slot) for slot in seen_slots]
            rows.append({"venue": venue, "cells": cells, "is_merged": False})

        days.append({
            "date": date,
            "day_name": date.strftime("%A"),
            "date_display": date.strftime("%Y-%m-%d"),
            "name": date.strftime("%A, %d %b %Y"),
            "timeslots": seen_slots,
            "rows": rows,
            "entries": entries,
        })

    return days


def _get_odel_exam_timetable_data(lecturer=None, search_query=""):
    """
    Get ODEL exam timetable data from ODELExamTimetable, grouped by date.
    """
    odel_exam_qs = ODELExamTimetable.objects.select_related(
        'course_allocation',
        'course_allocation__program_course',
        'course_allocation__lecturer',
        'venue',
    ).order_by("date", "start_time")

    if lecturer:
        odel_exam_qs = odel_exam_qs.filter(course_allocation__lecturer=lecturer)

    if search_query:
        odel_exam_qs = odel_exam_qs.filter(
            course_allocation__program_course__course_code__icontains=search_query
        )

    by_date = defaultdict(list)
    for entry in odel_exam_qs:
        alloc = entry.course_allocation
        by_date[entry.date].append({
            "type": "regular",
            "entry": entry,
            "venue": entry.venue.code if entry.venue else "TBA",
            "start_time": _fmt_time(entry.start_time),
            "end_time": _fmt_time(entry.end_time),
            "course_code": alloc.course_code,
            "course_name": alloc.course_name,
            "lecturer": alloc.lecturer.display_name if alloc.lecturer else "Unassigned",
            "program": alloc.program.name if alloc.program else "N/A",
            "students": alloc.number_of_students or 0,
            "date": entry.date,
            "is_merged": False,
            "is_shared": False,
        })

    exam_days = []
    for date in sorted(by_date.keys()):
        entries = by_date[date]
        entries.sort(key=lambda x: datetime.strptime(x["start_time"], "%H:%M"))

        seen_slots = []
        for e in entries:
            slot = (e["start_time"], e["end_time"])
            if slot not in seen_slots:
                seen_slots.append(slot)

        seen_venues = []
        for e in entries:
            if e["venue"] not in seen_venues:
                seen_venues.append(e["venue"])

        lookup = defaultdict(dict)
        for e in entries:
            lookup[e["venue"]][(e["start_time"], e["end_time"])] = e

        rows = []
        for venue in seen_venues:
            cells = [lookup[venue].get(slot) for slot in seen_slots]
            rows.append({"venue": venue, "cells": cells, "is_merged": False, "is_shared": False})

        exam_days.append({
            "date": date,
            "day_name": date.strftime("%A"),
            "date_display": date.strftime("%Y-%m-%d"),
            "timeslots": seen_slots,
            "rows": rows,
            "entries": entries,
            "has_merged": False,
            "has_shared": False,
        })

    return exam_days


def _get_campus_timetable_data(lecturer=None, search_query=""):
    """
    Get campus class timetable data from CampusTimetable, grouped by weekday.
    CampusTimetable FKs: course_allocation, campus  (no 'venue' field).
    The campus object itself acts as the location/venue.
    """
    campus_qs = CampusTimetable.objects.select_related(
        'course_allocation',
        'course_allocation__lecturer',
        'campus',
    ).order_by("day", "start_time")

    if lecturer:
        campus_qs = campus_qs.filter(course_allocation__lecturer=lecturer)

    if search_query:
        campus_qs = campus_qs.filter(
            course_allocation__course_code__icontains=search_query
        )

    by_day = defaultdict(list)
    for entry in campus_qs:
        alloc = entry.course_allocation
        campus = entry.campus
        # Use campus name/code as the "venue" label in the grid
        campus_label = getattr(campus, 'code', None) or getattr(campus, 'name', None) or "TBA"
        campus_name = getattr(campus, 'name', "N/A")
        # program / department are on CourseAllocation but may not be select_related;
        # access safely via getattr to avoid extra queries tripping on missing relations
        program_name = getattr(getattr(alloc, 'program', None), 'name', None) or "N/A"
        dept_name = getattr(getattr(alloc, 'department', None), 'name', None) or "N/A"
        by_day[entry.day].append({
            "type": "regular",
            "entry": entry,
            "venue": campus_label,
            "start_time": _fmt_time(entry.start_time),
            "end_time": _fmt_time(entry.end_time),
            "course_code": alloc.course_code,
            "course_name": alloc.course_name,
            "lecturer": alloc.lecturer.display_name if alloc.lecturer else "Unassigned",
            "program": program_name,
            "department": dept_name,
            "students": alloc.number_of_students or 0,
            "day": entry.day,
            "campus": campus_name,
            "is_merged": False,
        })

    days = []
    for day_name in WEEKDAY_ORDER:
        entries = by_day.get(day_name)
        if not entries:
            continue

        entries.sort(key=lambda e: datetime.strptime(e["start_time"], "%H:%M"))

        seen_slots = []
        for e in entries:
            slot = (e["start_time"], e["end_time"])
            if slot not in seen_slots:
                seen_slots.append(slot)

        seen_venues = []
        for e in entries:
            if e["venue"] not in seen_venues:
                seen_venues.append(e["venue"])

        lookup = {}
        for e in entries:
            lookup[(e["venue"], (e["start_time"], e["end_time"]))] = e

        rows = []
        for venue in seen_venues:
            cells = [lookup.get((venue, slot)) for slot in seen_slots]
            rows.append({"venue": venue, "cells": cells, "is_merged": False})

        days.append({
            "name": day_name,
            "timeslots": seen_slots,
            "rows": rows,
            "entries": entries,
        })

    return days


def _get_campus_exam_timetable_data(lecturer=None, search_query=""):
    """
    Get campus exam timetable data from CampusExamTimetable, grouped by date.
    CampusExamTimetable FKs: course_allocation, campus (no 'venue' field).
    """
    # FIXED: Replaced 'campus_allocation' and 'venue' with the actual FK relations: 'course_allocation' and 'campus'
    campus_exam_qs = CampusExamTimetable.objects.select_related(
        'course_allocation',
        'course_allocation__lecturer',
        'campus',
    ).order_by("date", "start_time")

    if lecturer:
        campus_exam_qs = campus_exam_qs.filter(course_allocation__lecturer=lecturer)

    if search_query:
        campus_exam_qs = campus_exam_qs.filter(
            course_allocation__course_code__icontains=search_query
        )

    by_date = defaultdict(list)
    for entry in campus_exam_qs:
        alloc = entry.course_allocation
        campus = entry.campus
        campus_label = getattr(campus, 'code', None) or getattr(campus, 'name', None) or "TBA"
        campus_name = getattr(campus, 'name', "N/A")
        program_name = getattr(getattr(alloc, 'program', None), 'name', None) or "N/A"
        dept_name = getattr(getattr(alloc, 'department', None), 'name', None) or "N/A"
        by_date[entry.date].append({
            "type": "regular",
            "entry": entry,
            "venue": campus_label,
            "start_time": _fmt_time(entry.start_time),
            "end_time": _fmt_time(entry.end_time),
            "course_code": alloc.course_code,
            "course_name": alloc.course_name,
            "lecturer": alloc.lecturer.display_name if alloc.lecturer else "Unassigned",
            "program": program_name,
            "department": dept_name,
            "students": alloc.number_of_students or 0,
            "campus": campus_name,
            "date": entry.date,
            "is_merged": False,
            "is_shared": False,
        })

    exam_days = []
    for date in sorted(by_date.keys()):
        entries = by_date[date]
        entries.sort(key=lambda x: datetime.strptime(x["start_time"], "%H:%M"))

        seen_slots = []
        for e in entries:
            slot = (e["start_time"], e["end_time"])
            if slot not in seen_slots:
                seen_slots.append(slot)

        seen_venues = []
        for e in entries:
            if e["venue"] not in seen_venues:
                seen_venues.append(e["venue"])

        lookup = defaultdict(dict)
        for e in entries:
            lookup[e["venue"]][(e["start_time"], e["end_time"])] = e

        rows = []
        for venue in seen_venues:
            cells = [lookup[venue].get(slot) for slot in seen_slots]
            rows.append({"venue": venue, "cells": cells, "is_merged": False, "is_shared": False})

        exam_days.append({
            "date": date,
            "day_name": date.strftime("%A"),
            "date_display": date.strftime("%Y-%m-%d"),
            "timeslots": seen_slots,
            "rows": rows,
            "entries": entries,
            "has_merged": False,
            "has_shared": False,
        })

    return exam_days


def _get_unscheduled_courses(lecturer=None, scheduled_ids=None, exclude_merged_ids=None):
    """
    Get unscheduled courses following the same logic as timetable_panel.
    FIXED: Replaced Subquery with Exists for MariaDB compatibility.
    """
    from timetable.models import AutoMergedExamGroup

    published_groups = AutoMergedExamGroup.objects.filter(published=True)
    base_exclude = published_groups.values_list('base_course_id', flat=True)
    merged_exclude = published_groups.values_list('merged_courses__id', flat=True)

    all_exclude_ids = set(scheduled_ids or []) | set(base_exclude) | set(merged_exclude)

    submission_control_exists = Exists(
        Department.objects.filter(
            id=OuterRef('department_id'),
            submission_control__allow_submission_to_tt=True
        )
    )

    unscheduled_qs = CourseAllocation.objects.select_related(
        'lecturer', 'program', 'department', 'department__faculty'
    ).only(
        'id', 'course_code', 'course_name',
        'lecturer__name', 'program__name', 'department__name',
        'department__faculty__name', 'number_of_students'
    ).exclude(
        id__in=all_exclude_ids
    ).filter(
        Q(
            Q(department__submission_control__allow_submission_to_tt=True) |
            Q(department__submission_control__isnull=True)
        ) |
        submission_control_exists
    )

    if lecturer:
        unscheduled_qs = unscheduled_qs.filter(lecturer=lecturer)

    return unscheduled_qs.order_by('department__faculty__name', 'department__name', 'course_code')



@allowed_roles(
    Role.DEPARTMENT_USERS, Role.LECTURER,
    Role.COD, Role.COD_ADMIN,
    Role.DEAN, Role.DEAN_ADMIN,
    Role.DVC, Role.DVC_ADMIN,
    Role.DIRECTOR, Role.TIMETABLE_ADMIN,
    Role.TIMETABLER, Role.SUDO,
    Role.COT, Role.UTILITY, Role.ACADEMIC_AFFAIRS,
)
def user_dashboard(request):
    """
    Dashboard with real-time user info, regular/exam timetables,
    lab timetables, ODEL timetables and campus timetables.
    """
    from course_allocation.models import LabAllocation
    from odel_system.models import ODELCourseAllocation
    # CampusCourseAllocation name may vary — import defensively
    try:
        from campuses_timetable.models import CampusCourseAllocation
        _has_campus_alloc = True
    except ImportError:
        _has_campus_alloc = False

    role = OrgRole.objects.filter(user=request.user).first()
    lecturer = Lecturer.objects.filter(user=request.user).first()

    # Supported view modes: 'regular', 'exam', 'lab', 'lab_exam', 'odel', 'odel_exam', 'campus', 'campus_exam'
    view_mode = request.GET.get('view', 'regular')
    search_query = request.GET.get("course_code", "").strip()

    # ── Collect all allocation types for this lecturer ────────────────────────
    allocations = CourseAllocation.objects.none()
    lab_allocations = LabAllocation.objects.none()
    odel_allocations = ODELCourseAllocation.objects.none()
    campus_allocations = CampusCourseAllocation.objects.none()

    if lecturer:
        allocations = CourseAllocation.objects.filter(lecturer=lecturer).select_related(
            'program', 'department'
        )
        lab_allocations = LabAllocation.objects.filter(
            lecturer=lecturer
        ).select_related('program_course')
        odel_allocations = ODELCourseAllocation.objects.filter(
            lecturer=lecturer
        ).select_related('program_course')
        # CampusCourseAllocation may or may not exist — guard gracefully
        if _has_campus_alloc:
            try:
                campus_allocations = CampusCourseAllocation.objects.filter(
                    lecturer=lecturer
                ).select_related('program_course')
            except Exception:
                campus_allocations = []

    # ── Notifications: combine across all allocation types ────────────────────
    notifications = [f"{alloc.course_code} - {alloc.status_label()}" for alloc in allocations]
    for alloc in odel_allocations:
        notifications.append(f"[ODEL] {alloc.course_code} - {alloc.status_label()}")
    # Lab and campus allocations typically don't have status_label; skip or add basic label
    for alloc in lab_allocations:
        pc = getattr(alloc, 'program_course', None)
        if pc:
            notifications.append(f"[LAB] {pc.course_code} - {pc.course_name}")
    try:
        for alloc in campus_allocations:
            pc = getattr(alloc, 'program_course', None)
            if pc:
                notifications.append(f"[CAMPUS] {pc.course_code} - {pc.course_name}")
    except Exception:
        pass

    # ── Base context shared across all modes ──────────────────────────────────
    base_context = {
        "role": role,
        "allocations": allocations,
        "lab_allocations": lab_allocations,
        "odel_allocations": odel_allocations,
        "campus_allocations": campus_allocations,
        "notifications": notifications[:10],
        "search_query": search_query,
        "view_mode": view_mode,
        # All timetable slots default to empty; populated per mode below
        "days": [],
        "full_timetable_flat": [],
        "exam_days": [],
        "exam_full_flat": [],
        "has_exam_data": False,
        "lab_days": [],
        "lab_exam_days": [],
        "has_lab_data": False,
        "has_lab_exam_data": False,
        "odel_days": [],
        "odel_exam_days": [],
        "has_odel_data": False,
        "has_odel_exam_data": False,
        "campus_days": [],
        "campus_exam_days": [],
        "has_campus_data": False,
        "has_campus_exam_data": False,
        "unscheduled_courses": CourseAllocation.objects.none(),
        "has_unscheduled": False,
        "user_roles": get_user_roles(request.user),
    }

    # ── Regular timetable ─────────────────────────────────────────────────────
    if view_mode == 'regular':
        days = _get_regular_timetable_data(lecturer, search_query)
        full_timetable_flat = []
        for day in days:
            for row in day["rows"]:
                for idx, slot in enumerate(day["timeslots"]):
                    entry = row["cells"][idx]
                    if entry:
                        full_timetable_flat.append({
                            "day": day["name"],
                            "venue": row["venue"],
                            "start": slot[0],
                            "end": slot[1],
                            "entry": entry,
                        })

        scheduled_ids = Timetable.objects.filter(
            course_allocation__lecturer=lecturer
        ).values_list('course_allocation_id', flat=True) if lecturer else []

        unscheduled_courses = _get_unscheduled_courses(lecturer, scheduled_ids)

        base_context.update({
            "days": days,
            "full_timetable_flat": full_timetable_flat,
            "unscheduled_courses": unscheduled_courses,
            "has_unscheduled": unscheduled_courses.exists(),
        })

    # ── Exam timetable ────────────────────────────────────────────────────────
    elif view_mode == 'exam':
        exam_days = _get_exam_timetable_data(lecturer, search_query)
        exam_full_flat = []
        for day in exam_days:
            for row in day["rows"]:
                for idx, slot in enumerate(day["timeslots"]):
                    entry = row["cells"][idx]
                    if entry:
                        exam_full_flat.append({
                            "date": day["date_display"],
                            "day": day["day_name"],
                            "venue": row["venue"],
                            "start": slot[0],
                            "end": slot[1],
                            "entry": entry,
                        })

        scheduled_exam_ids = ExamTimetable.objects.filter(
            course_allocation__lecturer=lecturer
        ).values_list('course_allocation_id', flat=True) if lecturer else []

        unscheduled_courses = _get_unscheduled_courses(lecturer, scheduled_exam_ids)

        base_context.update({
            "exam_days": exam_days,
            "exam_full_flat": exam_full_flat,
            "has_exam_data": len(exam_days) > 0,
            "unscheduled_courses": unscheduled_courses,
            "has_unscheduled": unscheduled_courses.exists(),
        })

    # ── Lab class timetable ───────────────────────────────────────────────────
    elif view_mode == 'lab':
        lab_days = _get_lab_timetable_data(lecturer, search_query)
        base_context.update({
            "lab_days": lab_days,
            "has_lab_data": len(lab_days) > 0,
        })

    # ── Lab exam timetable ────────────────────────────────────────────────────
    elif view_mode == 'lab_exam':
        lab_exam_days = _get_lab_exam_timetable_data(lecturer, search_query)
        base_context.update({
            "lab_exam_days": lab_exam_days,
            "has_lab_exam_data": len(lab_exam_days) > 0,
        })

    # ── ODEL class timetable ──────────────────────────────────────────────────
    elif view_mode == 'odel':
        odel_days = _get_odel_timetable_data(lecturer, search_query)
        base_context.update({
            "odel_days": odel_days,
            "has_odel_data": len(odel_days) > 0,
        })

    # ── ODEL exam timetable ───────────────────────────────────────────────────
    elif view_mode == 'odel_exam':
        odel_exam_days = _get_odel_exam_timetable_data(lecturer, search_query)
        base_context.update({
            "odel_exam_days": odel_exam_days,
            "has_odel_exam_data": len(odel_exam_days) > 0,
        })

    # ── Campus class timetable ────────────────────────────────────────────────
    elif view_mode == 'campus':
        campus_days = _get_campus_timetable_data(lecturer, search_query)
        base_context.update({
            "campus_days": campus_days,
            "has_campus_data": len(campus_days) > 0,
        })

    # ── Campus exam timetable ─────────────────────────────────────────────────
    elif view_mode == 'campus_exam':
        campus_exam_days = _get_campus_exam_timetable_data(lecturer, search_query)
        base_context.update({
            "campus_exam_days": campus_exam_days,
            "has_campus_exam_data": len(campus_exam_days) > 0,
        })

    return render(request, "dashboard/user_dashboard.html", base_context)