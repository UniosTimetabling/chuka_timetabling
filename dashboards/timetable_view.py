from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.db import models, transaction
from program_management.models import (
    Program,
    ProgramCourse,
)

from course_allocation.models import CourseAllocation
from timetable.models import (
    Timetable, ExamTimetable,
    AutoMergedExamGroup, MergedCourseGroup,
    LabTimetable, LabExamTimetable,
)
from classreps.models import MinimalTimetable, ClassRep


@transaction.atomic
def timetable_view(request, program_id=None, timetable_type=None):
    """
    Handles both page load and AJAX timetable fetch.
    Timetable types: minimal | main | exam | lab | lab_exam
    """

    # --------------------------------------------
    # CASE 1: Show viewer page
    # --------------------------------------------
    if not program_id or not timetable_type:
        programs = Program.objects.select_related(
            "department__faculty"
        ).all().order_by("name")

        return render(request, "dashboard/view_timetable.html", {"programs": programs})

    try:
        # Get program
        program = get_object_or_404(Program, id=program_id)
        program_name = program.name

        year = int(request.GET.get("year") or 1)

        # -------------------------------------------------------------
        # STRICT YEAR FILTER based on ProgramCourse
        # Step 1: Get ProgramCourse rows for this program + year
        # -------------------------------------------------------------
        program_courses_qs = ProgramCourse.objects.filter(
            program_id=program_id,
            year=year
        )
        program_course_codes = program_courses_qs.values_list("course_code", flat=True)
        program_course_ids   = program_courses_qs.values_list("id", flat=True)

        # Step 2: CourseAllocation IDs for main/exam timetable
        program_alloc_qs = CourseAllocation.objects.filter(
            program_id=program_id,
            course_code__in=program_course_codes
        )
        alloc_ids = list(program_alloc_qs.values_list("id", flat=True))

        # -------------------------------------------------------------
        # Support for merged courses (auto + manual) — main & exam only
        # -------------------------------------------------------------
        merged_base_ids = set()

        auto_merged = AutoMergedExamGroup.objects.filter(
            merged_courses__in=program_alloc_qs
        ).values_list("base_course_id", flat=True)
        merged_base_ids.update(auto_merged)

        manual_merged = MergedCourseGroup.objects.filter(
            merged_courses__in=program_alloc_qs
        ).values_list("base_course_id", flat=True)
        merged_base_ids.update(manual_merged)

        # Q filter reused by main + exam
        base_q = (
            models.Q(course_allocation_id__in=alloc_ids)
            | models.Q(course_allocation_id__in=merged_base_ids)
        )

        # -------------------------------------------------------------
        # MINIMAL (Class Rep) TIMETABLE
        # -------------------------------------------------------------
        if timetable_type == "minimal":
            rep_entries = MinimalTimetable.objects.filter(
                class_rep__program_id=program_id
            ).order_by("day", "start_time")

            minimal_data = {}
            for entry in rep_entries:
                day = entry.day
                minimal_data.setdefault(day, [])
                minimal_data[day].append({
                    "venue": entry.venue or "No Venue",
                    "course_code": entry.course_code,
                    "course_name": entry.course_name,
                    "lecturer": "Class Rep Entry",
                    "start": entry.start_time.strftime("%H:%M") if entry.start_time else "",
                    "end": entry.end_time.strftime("%H:%M") if entry.end_time else "",
                })

            return JsonResponse({
                "program_name": program_name,
                "year": year,
                "timetable_type": "minimal",
                "has_entries": rep_entries.exists(),
                "data": minimal_data,
            })

        # -------------------------------------------------------------
        # MAIN (Regular) TIMETABLE
        # -------------------------------------------------------------
        if timetable_type == "main":
            from room_management.models import Venue

            entries = Timetable.objects.filter(base_q).values(
                'id', 'day', 'start_time', 'end_time', 'venue_id',
                'course_allocation__course_code',
                'course_allocation__course_name',
                'course_allocation__lecturer__name',
            ).order_by("day", "start_time")

            venue_ids = [e['venue_id'] for e in entries if e['venue_id']]
            venues = {}
            if venue_ids:
                venues = {v['id']: v['code'] for v in
                          Venue.objects.filter(id__in=venue_ids).values('id', 'code')}

            main_data = {}
            for e in entries:
                day = e['day']
                main_data.setdefault(day, [])
                venue_code = venues.get(e['venue_id'], f"Venue {e['venue_id']}")
                main_data[day].append({
                    "venue": str(venue_code),
                    "course_code": str(e['course_allocation__course_code'] or ""),
                    "course_name": str(e['course_allocation__course_name'] or ""),
                    "lecturer": str(e['course_allocation__lecturer__name'] or "Unassigned"),
                    "start": e['start_time'].strftime("%H:%M") if e['start_time'] else "",
                    "end": e['end_time'].strftime("%H:%M") if e['end_time'] else "",
                })

            return JsonResponse({
                "program_name": program_name,
                "year": year,
                "timetable_type": "main",
                "has_entries": len(entries) > 0,
                "data": main_data,
            })

        # -------------------------------------------------------------
        # EXAM TIMETABLE
        # -------------------------------------------------------------
        if timetable_type == "exam":
            from room_management.models import Venue

            entries = ExamTimetable.objects.filter(base_q).values(
                'id', 'day', 'date', 'start_time', 'end_time', 'venue_id',
                'course_allocation__course_code',
                'course_allocation__course_name',
                'course_allocation__lecturer__name',
            ).order_by("date", "start_time")

            venue_ids = [e['venue_id'] for e in entries if e['venue_id']]
            venues = {}
            if venue_ids:
                venues = {v['id']: v['code'] for v in
                          Venue.objects.filter(id__in=venue_ids).values('id', 'code')}

            exam_data = {}
            for e in entries:
                key = f"{e['date']} ({e['day']})"
                exam_data.setdefault(key, [])
                venue_code = venues.get(e['venue_id'], f"Venue {e['venue_id']}")
                exam_data[key].append({
                    "venue": str(venue_code),
                    "course_code": str(e['course_allocation__course_code'] or ""),
                    "course_name": str(e['course_allocation__course_name'] or ""),
                    "lecturer": str(e['course_allocation__lecturer__name'] or "Unassigned"),
                    "start": e['start_time'].strftime("%H:%M") if e['start_time'] else "",
                    "end": e['end_time'].strftime("%H:%M") if e['end_time'] else "",
                })

            return JsonResponse({
                "program_name": program_name,
                "year": year,
                "timetable_type": "exam",
                "has_entries": len(entries) > 0,
                "data": exam_data,
            })

        # -------------------------------------------------------------
        # LAB TIMETABLE
        # LabTimetable → lab_allocation (LabAllocation)
        #                  → program_course (ProgramCourse) — filtered by program + year
        #              → lab_venue (LabVenue) — provides venue code
        # -------------------------------------------------------------
        if timetable_type == "lab":
            from room_management.models import LabVenue

            lab_entries = LabTimetable.objects.filter(
                lab_allocation__program_course__program_id=program_id,
                lab_allocation__program_course__year=year,
            ).values(
                'id', 'day', 'start_time', 'end_time', 'lab_venue_id',
                'lab_allocation__program_course__course_code',
                'lab_allocation__program_course__course_name',
                'lab_allocation__lecturer__name',
            ).order_by("day", "start_time")

            # Resolve LabVenue codes in one query
            lab_venue_ids = [e['lab_venue_id'] for e in lab_entries if e['lab_venue_id']]
            lab_venues = {}
            if lab_venue_ids:
                lab_venues = {v['id']: v['code'] for v in
                              LabVenue.objects.filter(id__in=lab_venue_ids).values('id', 'code')}

            lab_data = {}
            for e in lab_entries:
                day = e['day']
                lab_data.setdefault(day, [])
                venue_code = lab_venues.get(e['lab_venue_id'], f"Lab {e['lab_venue_id']}")
                lab_data[day].append({
                    "venue": str(venue_code),
                    "course_code": str(e['lab_allocation__program_course__course_code'] or ""),
                    "course_name": str(e['lab_allocation__program_course__course_name'] or ""),
                    "lecturer": str(e['lab_allocation__lecturer__name'] or "Unassigned"),
                    "start": e['start_time'].strftime("%H:%M") if e['start_time'] else "",
                    "end": e['end_time'].strftime("%H:%M") if e['end_time'] else "",
                })

            return JsonResponse({
                "program_name": program_name,
                "year": year,
                "timetable_type": "lab",
                "has_entries": len(lab_entries) > 0,
                "data": lab_data,
            })

        # -------------------------------------------------------------
        # LAB EXAM TIMETABLE
        # LabExamTimetable → lab_allocation → program_course (program + year)
        #                  → lab_venue
        # -------------------------------------------------------------
        if timetable_type == "lab_exam":
            from room_management.models import LabVenue

            lab_exam_entries = LabExamTimetable.objects.filter(
                lab_allocation__program_course__program_id=program_id,
                lab_allocation__program_course__year=year,
            ).values(
                'id', 'day', 'date', 'start_time', 'end_time', 'lab_venue_id',
                'lab_allocation__program_course__course_code',
                'lab_allocation__program_course__course_name',
                'lab_allocation__lecturer__name',
            ).order_by("date", "start_time")

            lab_venue_ids = [e['lab_venue_id'] for e in lab_exam_entries if e['lab_venue_id']]
            lab_venues = {}
            if lab_venue_ids:
                lab_venues = {v['id']: v['code'] for v in
                              LabVenue.objects.filter(id__in=lab_venue_ids).values('id', 'code')}

            lab_exam_data = {}
            for e in lab_exam_entries:
                key = f"{e['date']} ({e['day']})"
                lab_exam_data.setdefault(key, [])
                venue_code = lab_venues.get(e['lab_venue_id'], f"Lab {e['lab_venue_id']}")
                lab_exam_data[key].append({
                    "venue": str(venue_code),
                    "course_code": str(e['lab_allocation__program_course__course_code'] or ""),
                    "course_name": str(e['lab_allocation__program_course__course_name'] or ""),
                    "lecturer": str(e['lab_allocation__lecturer__name'] or "Unassigned"),
                    "start": e['start_time'].strftime("%H:%M") if e['start_time'] else "",
                    "end": e['end_time'].strftime("%H:%M") if e['end_time'] else "",
                })

            return JsonResponse({
                "program_name": program_name,
                "year": year,
                "timetable_type": "lab_exam",
                "has_entries": len(lab_exam_entries) > 0,
                "data": lab_exam_data,
            })

        return JsonResponse({"error": "Invalid timetable type"}, status=400)

    except Exception as e:
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        logger.error(f"Error in timetable_view: {e}")
        logger.error(traceback.format_exc())
        return JsonResponse({"error": str(e), "has_entries": False}, status=500)