from datetime import datetime, timedelta
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Q
import json
from collections import defaultdict
from admins.forms import SchedulerConfigForm
from django.db.models import Prefetch
from django.contrib.auth.decorators import login_required
from course_allocation.models import CourseAllocation, CombinedCourseGroup
from course_allocation.config_helpers import strip_course_code_tag
from timetable.models import (
    TempTimetable,
    ExamTimetable,
    AutoMergedExamGroup,
    ExamTempTimetable,
    MergedCourseGroup,
    SharedVenueExamGroup,
    SchedulerConfig,
    Timetable,
)
from room_management.models import Venue
from program_management.models import Program, ProgramCourse
from lecturer_portal.models import Lecturer
from course_allocation.models import SubmissionControl
from django.views.decorators.http import require_GET, require_POST
from course_allocation.models import SubmissionControl
from notifications.models import Notification
from timetable.timetable_panel import (
    is_scheduling_exempt,
    _are_in_same_combined_group,
    _build_combined_group_exclude_ids,
    _get_combined_group_meta_map,
)


@login_required
def unscheduled_courses_json(request):
    """
    Returns JSON with:
      - main_unscheduled: course allocations not found in Timetable/TempTimetable/AutoMergedExamGroup
      - exam_unscheduled: course allocations not found in ExamTimetable/ExamTempTimetable/MergedCourseGroup/SharedVenueExamGroup
      - zero_student:     course allocations with number_of_students == 0 or NULL (with placement info)
      - zero_student_report_url: URL to the PDF report

    Optional GET params:
      - department_id (int)
      - program_id (int)
    """
    department_id = request.GET.get("department_id")
    program_id = request.GET.get("program_id")

    qs = CourseAllocation.objects.all()
    if department_id:
        qs = qs.filter(department_id=department_id)
    if program_id:
        qs = qs.filter(program_id=program_id)

    # Precompute sets of CourseAllocation ids present in various scheduling tables for fast checks:
    timetable_ids = set(Timetable.objects.values_list("course_allocation_id", flat=True))
    temp_timetable_ids = set(TempTimetable.objects.values_list("course_allocation_id", flat=True))

    # AutoMergedExamGroup may reference base_course and merged_courses (M2M)
    auto_base_ids = set(AutoMergedExamGroup.objects.values_list("base_course_id", flat=True))
    auto_merged_ids = set(AutoMergedExamGroup.objects.values_list("merged_courses", flat=True))

    exam_timetable_ids = set(ExamTimetable.objects.values_list("course_allocation_id", flat=True))
    exam_temp_ids = set(ExamTempTimetable.objects.values_list("course_allocation_id", flat=True))

    merged_base_ids = set(MergedCourseGroup.objects.values_list("base_course_id", flat=True))
    merged_ids = set(MergedCourseGroup.objects.values_list("merged_courses", flat=True))

    shared_ids = set(SharedVenueExamGroup.objects.values_list("course_allocations", flat=True))

    # A CombinedCourseGroup member is implicitly exam-covered once its primary
    # allocation already has an exam slot (direct, merged, or shared-venue) —
    # they sit the same exam together. Conditional (unlike the teaching-timetable
    # exclusion below), since exam slots are booked separately from lecture slots.
    _exam_scheduled_base = exam_timetable_ids | exam_temp_ids | merged_base_ids | merged_ids | shared_ids
    exam_combined_exclude_ids = set()
    for group in CombinedCourseGroup.objects.prefetch_related('allocations').only('id', 'primary_allocation_id'):
        primary_id = group.primary_allocation_id
        if primary_id and primary_id in _exam_scheduled_base:
            exam_combined_exclude_ids.update(group.allocations.values_list('id', flat=True))

    # ── CombinedCourseGroup handling ────────────────────────────────────────
    # A CombinedCourseGroup is one atomic teaching unit — same rule applied
    # everywhere else (see timetable_panel._build_combined_group_exclude_ids):
    # non-primary members must NEVER surface as their own unscheduled row on
    # the main (teaching) timetable — that was the duplication bug. This
    # exclusion is unconditional, matching the regular timetable panel.
    combined_exclude_ids = _build_combined_group_exclude_ids()
    combined_meta = _get_combined_group_meta_map()

    def ca_to_dict(ca):
        meta = combined_meta.get(ca.id)
        return {
            "id": ca.id,
            "course_code": meta["display_name"] if meta else ca.course_code,
            "course_name": ca.course_name,
            "lecturer": ca.lecturer.display_name if ca.lecturer else None,
            "program": ca.program.name if ca.program else None,
            "department": ca.department.name if ca.department else None,
            "origin_department": ca.origin_department.name if getattr(ca, "origin_department", None) else None,
            "number_of_students": meta["total_students"] if meta else ca.number_of_students,
            "submitted_to_tt": ca.submitted_to_tt,
            # None for an ordinary allocation. When present this row represents
            # a whole CombinedCourseGroup (this alloc is the primary) — the
            # course_code/number_of_students above are already the group's.
            "combined_group": meta,
        }

    main_unscheduled = []
    exam_unscheduled = []

    # Collect zero-student allocation ids for exclusion from main unscheduled count
    zero_student_ids = set(
        CourseAllocation.objects.filter(
            Q(number_of_students__isnull=True) | Q(number_of_students=0)
        ).values_list("id", flat=True)
    )

    # iterate once, categorize into main/exam unscheduled
    for ca in qs:
        # Skip zero-student courses from the main unscheduled list
        # (they get their own dedicated section)
        if ca.id in zero_student_ids:
            continue

        # main scheduled if present in any of timetable, temp_timetable, auto-merged (base or merged),
        # OR is a non-primary CombinedCourseGroup member (that group is one atomic teaching unit —
        # only the primary row, relabelled with the group's name/total in ca_to_dict, represents it).
        # Without this, every member of a combined group showed up as its own duplicate row.
        is_main_scheduled = (
            (ca.id in timetable_ids)
            or (ca.id in temp_timetable_ids)
            or (ca.id in auto_base_ids)
            or (ca.id in auto_merged_ids)
            or (ca.id in combined_exclude_ids)
        )

        if not is_main_scheduled:
            main_unscheduled.append(ca_to_dict(ca))

        # exam scheduled if present in any of exam_timetable, exam_temp, merged groups, shared groups,
        # or is a CombinedCourseGroup member whose primary already has an exam slot.
        is_exam_scheduled = (
            (ca.id in exam_timetable_ids)
            or (ca.id in exam_temp_ids)
            or (ca.id in merged_base_ids)
            or (ca.id in merged_ids)
            or (ca.id in shared_ids)
            or (ca.id in exam_combined_exclude_ids)
        )

        if not is_exam_scheduled:
            exam_unscheduled.append(ca_to_dict(ca))

    # ── Zero-student courses ─────────────────────────────────────────────────
    from django.urls import reverse as _reverse
    try:
        report_url = _reverse('zero_student_report_pdf')
    except Exception:
        report_url = '/export/zero-student-report/'

    zero_student = []
    for ca in (
        CourseAllocation.objects
        .filter(Q(number_of_students__isnull=True) | Q(number_of_students=0))
        .select_related('department', 'lecturer', 'program')
    ):
        # Optionally apply department/program filter if provided
        if department_id and str(getattr(ca.department, 'id', None)) != str(department_id):
            continue
        if program_id and str(getattr(ca.program, 'id', None)) != str(program_id):
            continue

        # Get placement info from TempTimetable if scheduled
        placement = None
        entry = TempTimetable.objects.filter(
            course_allocation=ca
        ).select_related('venue').first()
        if not entry:
            # Also try published Timetable
            from timetable.models import Timetable as _TT
            entry = _TT.objects.filter(
                course_allocation=ca
            ).select_related('venue').first()
        if entry:
            placement = {
                'day':   getattr(entry, 'day', '—') or '—',
                'start': entry.start_time.strftime('%H:%M') if entry.start_time else '—',
                'end':   entry.end_time.strftime('%H:%M')   if entry.end_time   else '—',
                'venue': entry.venue.code if entry.venue else '—',
            }

        row = ca_to_dict(ca)
        row['placement'] = placement
        zero_student.append(row)

    data = {
        "counts": {
            "main_unscheduled": len(main_unscheduled),
            "exam_unscheduled": len(exam_unscheduled),
            "zero_student":     len(zero_student),
        },
        "main_unscheduled":      main_unscheduled,
        "exam_unscheduled":      exam_unscheduled,
        "zero_student":          zero_student,
        "zero_student_report_url": report_url,
    }
    return JsonResponse(data, safe=True)


# -----------------------
# Helper utilities
# -----------------------
def time_overlaps(start_a, end_a, start_b, end_b):
    """
    Return True if time intervals [start_a, end_a) and [start_b, end_b) overlap.
    Accepts datetime.time objects or "%H:%M" strings.
    """
    if isinstance(start_a, str):
        start_a = datetime.strptime(start_a, "%H:%M").time()
    if isinstance(end_a, str):
        end_a = datetime.strptime(end_a, "%H:%M").time()
    if isinstance(start_b, str):
        start_b = datetime.strptime(start_b, "%H:%M").time()
    if isinstance(end_b, str):
        end_b = datetime.strptime(end_b, "%H:%M").time()

    return (start_a < end_b) and (start_b < end_a)

def _get_year_value(allocation_or_obj):
    """
    Return the year of study (as string) from a CourseAllocation or similar object.
    """
    if allocation_or_obj is None:
        return None

    # 1️⃣ Direct fields
    for attr in ("year", "year_of_study", "level", "year_level", "academic_year"):
        val = getattr(allocation_or_obj, attr, None)
        if val:
            return str(val)

    # 2️⃣ Check related program for year
    program = getattr(allocation_or_obj, "program", None)
    if program:
        for attr in ("year", "year_of_study", "level", "year_level", "academic_year"):
            val = getattr(program, attr, None)
            if val:
                return str(val)

    # 3️⃣ Check ProgramCourse table
    pc_direct = getattr(allocation_or_obj, "program_course", None)
    if pc_direct and getattr(pc_direct, "year", None):
        return str(pc_direct.year)

    program = getattr(allocation_or_obj, "program", None)
    course_code = getattr(allocation_or_obj, "course_code", None)
    if program and course_code:
        pc = ProgramCourse.objects.filter(
            program=program,
            course_code__iexact=strip_course_code_tag(course_code)
        ).first()
        if pc and pc.year:
            return str(pc.year)

    return None

# -----------------------
# Conflict Detection API
# -----------------------
@login_required
def timetable_conflicts_api(request):
    """
    API to detect timetable conflicts:
    - Same program courses in same year at same timeslot
    - Same lecturer allocated to different courses at same time
    - Venue double-booking
    """
    conflicts = {
        'program_conflicts': [],
        'lecturer_conflicts': [],
        'venue_conflicts': [],
        'total_conflicts': 0
    }

    # Get all timetable entries with related data
    timetables = Timetable.objects.select_related(
        'course_allocation',
        'course_allocation__program',
        'course_allocation__lecturer'
    ).all()

    # Group by day and timeslot for efficient conflict detection
    schedule_map = defaultdict(lambda: defaultdict(list))

    for tt in timetables:
        key = f"{tt.day}_{tt.start_time.strftime('%H:%M')}_{tt.end_time.strftime('%H:%M')}"
        schedule_map[key].append(tt)

    # Detect conflicts
    for timeslot_key, entries in schedule_map.items():
        if len(entries) <= 1:
            continue

        day, start_time, end_time = timeslot_key.split('_')

        # Check program conflicts (same program, same year, different courses)
        program_groups = defaultdict(list)
        for entry in entries:
            program = getattr(entry.course_allocation, 'program', None)
            if program:
                year = _get_year_value(entry.course_allocation)
                if year:  # Only consider if year is available
                    program_key = f"{program.id}_{year}"
                    program_groups[program_key].append(entry)

        for program_key, program_entries in program_groups.items():
            # A slot with 2+ program-year entries is only a REAL conflict if
            # at least one non-exempt, non-combined pair exists among them.
            # Without this check, electives, different student groups,
            # specialization-stem alternatives, and different-intake pairs
            # — all of which the scheduler is explicitly allowed to place
            # concurrently — get reported as collisions even though they
            # were never real ones. Mirrors is_program_year_collision_exempt
            # in the autoscheduler and is_scheduling_exempt in the panel.
            real_conflict_entries = []
            for i in range(len(program_entries)):
                for j in range(i + 1, len(program_entries)):
                    ea, eb = program_entries[i], program_entries[j]
                    aa, ab = ea.course_allocation, eb.course_allocation
                    if _are_in_same_combined_group(aa.id, ab.id):
                        continue
                    if is_scheduling_exempt(aa, ab):
                        continue
                    real_conflict_entries.extend([ea, eb])
            program_entries = list({e.id: e for e in real_conflict_entries}.values())
            if len(program_entries) > 1:
                program_id, year = program_key.split('_')
                program = Program.objects.get(id=program_id)

                conflict = {
                    'type': 'program_conflict',
                    'day': day,
                    'timeslot': f"{start_time} - {end_time}",
                    'program': program.name,
                    'year': year,
                    'courses': [],
                    'message': f"Program {program.name} Year {year} has multiple courses at same time"
                }

                for entry in program_entries:
                    conflict['courses'].append({
                        'course_code': entry.course_allocation.course_code,
                        'course_name': entry.course_allocation.course_name,
                        'lecturer': getattr(entry.course_allocation.lecturer, 'name', 'Unassigned'),
                        'venue': entry.venue
                    })

                conflicts['program_conflicts'].append(conflict)

        # Check lecturer conflicts
        lecturer_groups = defaultdict(list)
        for entry in entries:
            lecturer = getattr(entry.course_allocation, 'lecturer', None)
            if lecturer:
                lecturer_groups[lecturer.id].append(entry)

        for lecturer_id, lecturer_entries in lecturer_groups.items():
            # Same fix as program_groups above: a lecturer teaching a
            # merged/co-located group (several sections of the same course,
            # deliberately combined into one room/slot by SiblingColocation
            # or a CombinedCourseGroup) is ONE class, not a double-booking.
            # Only flag pairs that are neither combined nor exempt.
            real_conflict_entries = []
            for i in range(len(lecturer_entries)):
                for j in range(i + 1, len(lecturer_entries)):
                    ea, eb = lecturer_entries[i], lecturer_entries[j]
                    aa, ab = ea.course_allocation, eb.course_allocation
                    if _are_in_same_combined_group(aa.id, ab.id):
                        continue
                    real_conflict_entries.extend([ea, eb])
            lecturer_entries = list({e.id: e for e in real_conflict_entries}.values())
            if len(lecturer_entries) > 1:
                lecturer = Lecturer.objects.get(id=lecturer_id)

                conflict = {
                    'type': 'lecturer_conflict',
                    'day': day,
                    'timeslot': f"{start_time} - {end_time}",
                    'lecturer': lecturer.name,
                    'courses': [],
                    'message': f"Lecturer {lecturer.name} scheduled for multiple courses simultaneously"
                }

                for entry in lecturer_entries:
                    conflict['courses'].append({
                        'course_code': entry.course_allocation.course_code,
                        'course_name': entry.course_allocation.course_name,
                        'program': getattr(entry.course_allocation.program, 'name', 'N/A'),
                        'venue': entry.venue
                    })

                conflicts['lecturer_conflicts'].append(conflict)

        # Check venue conflicts
        venue_groups = defaultdict(list)
        for entry in entries:
            if entry.venue:
                venue_groups[entry.venue].append(entry)

        for venue, venue_entries in venue_groups.items():
            if len(venue_entries) > 1:
                conflict = {
                    'type': 'venue_conflict',
                    'day': day,
                    'timeslot': f"{start_time} - {end_time}",
                    'venue': venue,
                    'courses': [],
                    'message': f"Venue {venue} double-booked at same time"
                }

                for entry in venue_entries:
                    conflict['courses'].append({
                        'course_code': entry.course_allocation.course_code,
                        'course_name': entry.course_allocation.course_name,
                        'lecturer': getattr(entry.course_allocation.lecturer, 'name', 'Unassigned'),
                        'program': getattr(entry.course_allocation.program, 'name', 'N/A')
                    })

                conflicts['venue_conflicts'].append(conflict)

    # Calculate total conflicts
    conflicts['total_conflicts'] = (
        len(conflicts['program_conflicts']) +
        len(conflicts['lecturer_conflicts']) +
        len(conflicts['venue_conflicts'])
    )

    return JsonResponse(conflicts)

def overlaps(a_start, a_end, b_start, b_end):
    """True if two time intervals overlap (open intervals)."""
    return a_start < b_end and b_start < a_end

def _resolve_program_year(course_code, program):
    """
    Try to get ProgramCourse.year for the given program and course_code.
    Returns int year or None if not resolvable.
    """
    if not program or not course_code:
        return None
    pc = ProgramCourse.objects.filter(
        program=program,
        course_code__iexact=strip_course_code_tag(course_code)
    ).only("year").first()
    return pc.year if pc else None

def _group_by_key_and_detect_conflicts(entries, key_func, is_exam=False):
    """
    Generic helper:
      - entries: iterable of timetable-like objects having attributes:
          id, course_allocation (with course_code, program, lecturer),
          day/date, start_time, end_time
      - key_func: function(entry) -> grouping key (e.g., (lecturer.id, day) or (program.id, year, day))
      - is_exam: if True, the date field is used in the returned rows (for reporting)
    Returns two lists: lecturer_conflicts, program_conflicts (each element is dict)
    """
    lecturer_conflicts = []
    program_conflicts = []

    # group entries using key_func
    groups = defaultdict(list)
    for e in entries:
        key = key_func(e)
        if key is None:
            continue
        groups[key].append(e)

    # in each group, sort by start_time and compare neighbors for overlap
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        # sort by start_time to only check nearby overlaps (linear)
        rows.sort(key=lambda r: (r.start_time, r.end_time))

        # compare each row to subsequent rows until no possible overlap
        n = len(rows)
        for i in range(n):
            a = rows[i]
            for j in range(i + 1, n):
                b = rows[j]
                # quick bail: if b starts at or after a.end and times are sorted, no further overlaps for 'a'
                if b.start_time >= a.end_time:
                    break

                # must be different course allocations
                if a.course_allocation.id == b.course_allocation.id:
                    continue

                # overlapping times (we still call overlaps for clarity)
                if not overlaps(a.start_time, a.end_time, b.start_time, b.end_time):
                    continue

                # ---------- Lecturer conflict (same lecturer group key)
                # key_func for lecturer groups should ensure these share same lecturer & day/date
                if getattr(a.course_allocation, "lecturer", None) and getattr(b.course_allocation, "lecturer", None):
                    if a.course_allocation.lecturer == b.course_allocation.lecturer:
                        # Same class, deliberately co-located (merged/combined
                        # sections) — not a real double-booking. Mirrors the
                        # merged_alloc_ids exclusion in the autoscheduler's
                        # own collision-swap pass.
                        if _are_in_same_combined_group(a.course_allocation.id, b.course_allocation.id):
                            continue
                        # report once per pair
                        rec = {
                            "type": "Lecturer Double Booking" + (" (Exam)" if is_exam else ""),
                            "lecturer": a.course_allocation.lecturer.display_name,
                            "time1": f"{a.start_time}-{a.end_time}",
                            "time2": f"{b.start_time}-{b.end_time}",
                            "course1": a.course_allocation.course_code,
                            "course2": b.course_allocation.course_code,
                            "source": "Exam Timetable" if is_exam else "Teaching Timetable",
                        }
                        if is_exam:
                            rec["date"] = getattr(a, "date").strftime("%Y-%m-%d")
                        else:
                            rec["day"] = getattr(a, "day")
                        lecturer_conflicts.append(rec)

                # ---------- Program-year conflict
                # note: program/year grouping key_func should ensure same program & same year & same day/date
                prog_a = getattr(a.course_allocation, "program", None)
                prog_b = getattr(b.course_allocation, "program", None)
                if prog_a and prog_b and prog_a == prog_b:
                    # resolve years
                    year_a = _resolve_program_year(a.course_allocation.course_code, prog_a)
                    year_b = _resolve_program_year(b.course_allocation.course_code, prog_b)
                    # only flag if both years resolved and equal
                    if (
                        year_a and year_b and year_a == year_b
                        and not _are_in_same_combined_group(a.course_allocation.id, b.course_allocation.id)
                        and not is_scheduling_exempt(a.course_allocation, b.course_allocation)
                    ):
                        rec = {
                            "type": "Program Year Collision" + (" (Exam)" if is_exam else ""),
                            "program": prog_a.name,
                            "year": year_a,
                            "slot1": f"{a.start_time}-{a.end_time}",
                            "slot2": f"{b.start_time}-{b.end_time}",
                            "course1": a.course_allocation.course_code,
                            "course2": b.course_allocation.course_code,
                            "source": "Exam Timetable" if is_exam else "Teaching Timetable",
                        }
                        if is_exam:
                            rec["date"] = getattr(a, "date").strftime("%Y-%m-%d")
                        else:
                            rec["day"] = getattr(a, "day")
                        program_conflicts.append(rec)

    return lecturer_conflicts, program_conflicts

def collision_report_api(request):
    """
    API endpoint returning JSON with accurate lecturer and program-year conflicts.
    """
    report = {"lecturer_conflicts": [], "program_conflicts": []}

    # --- TEACHING TIMETABLE ---
    # fetch timetables with related allocations (avoid extra queries)
    timetables = list(Timetable.objects.select_related(
        "course_allocation__lecturer", "course_allocation__program"
    ).all())

    # 1) Lecturer groups: group by (lecturer_id, day)
    def lecturer_key_tt(e):
        lec = getattr(e.course_allocation, "lecturer", None)
        if not lec:
            return None
        return (lec.id, getattr(e, "day"))

    lec_conflicts_tt, _ = _group_by_key_and_detect_conflicts(timetables, lecturer_key_tt, is_exam=False)
    report["lecturer_conflicts"].extend(lec_conflicts_tt)

    # 2) Program-year groups: group by (program_id, day)
    def program_key_tt(e):
        prog = getattr(e.course_allocation, "program", None)
        if not prog:
            return None
        return (prog.id, getattr(e, "day"))

    _, prog_conflicts_tt = _group_by_key_and_detect_conflicts(timetables, program_key_tt, is_exam=False)
    report["program_conflicts"].extend(prog_conflicts_tt)

    # --- EXAM TIMETABLE ---
    exams = list(ExamTimetable.objects.select_related(
        "course_allocation__lecturer", "course_allocation__program"
    ).all())

    # 1) Lecturer groups: group by (lecturer_id, date)
    def lecturer_key_exam(e):
        lec = getattr(e.course_allocation, "lecturer", None)
        if not lec:
            return None
        return (lec.id, getattr(e, "date"))

    lec_conflicts_ex, _ = _group_by_key_and_detect_conflicts(exams, lecturer_key_exam, is_exam=True)
    report["lecturer_conflicts"].extend(lec_conflicts_ex)

    # 2) Program-year groups for exams: group by (program_id, date)
    def program_key_exam(e):
        prog = getattr(e.course_allocation, "program", None)
        if not prog:
            return None
        return (prog.id, getattr(e, "date"))

    _, prog_conflicts_ex = _group_by_key_and_detect_conflicts(exams, program_key_exam, is_exam=True)
    report["program_conflicts"].extend(prog_conflicts_ex)

    # Optionally: deduplicate identical conflict entries (in case of multiple detection paths)
    # A simple dedupe by JSON string:
    import json
    def dedupe_list(lst):
        seen = set()
        out = []
        for item in lst:
            s = json.dumps(item, sort_keys=True, default=str)
            if s not in seen:
                seen.add(s)
                out.append(item)
        return out

    report["lecturer_conflicts"] = dedupe_list(report["lecturer_conflicts"])
    report["program_conflicts"] = dedupe_list(report["program_conflicts"])

    return JsonResponse(report, safe=False)


@require_GET
def get_notifications(request):
    """Return recent notifications and submission status"""

    # ✅ Get all notifications for the current user (filter before slicing)
    notifications_qs = Notification.objects.order_by('-created_at')

    # ✅ Check if there are unread notifications
    unread = notifications_qs.filter(is_read=False).exists()

    # ✅ Now slice AFTER filtering
    notifications = notifications_qs[:10]

    # ✅ Department submission summary
    submitted = SubmissionControl.objects.filter(allow_submission_to_tt=True)
    not_submitted = SubmissionControl.objects.filter(allow_submission_to_tt=False)

    # ✅ Return JSON response
    return JsonResponse({
        "notifications": [
            {
                "id": n.id,
                "message": n.message,
                "is_read": n.is_read,
                "created_at": n.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for n in notifications
        ],
        "has_unread": unread,
        "submitted": [s.department.name for s in submitted if s.department],
        "not_submitted": [s.department.name for s in not_submitted if s.department],
    })


@require_POST
def mark_notification_read(request):
    """Mark a notification as read"""
    notif_id = request.POST.get("id")

    if notif_id:
        Notification.objects.filter(id=notif_id).update(is_read=True)
        return JsonResponse({"success": True, "id": notif_id})
    else:
        return JsonResponse({"success": False, "error": "Notification ID not provided"}, status=400)