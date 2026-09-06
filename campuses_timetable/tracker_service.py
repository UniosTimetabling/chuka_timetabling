"""
tracker_service.py
==================
Core logic for the Program Year Tracker feature.

Responsibilities
----------------
1. get_or_create_tracker()   – ensure a ProgramYearTracker exists for a given
                               (program, program_year, semester, academic_year).
2. refresh_tracker()         – recalculate expected/allocated/missing courses.
3. build_report_text()       – format a human-readable gap report.
4. send_gap_notification()   – create a Notification for the COD of the relevant
                               department and save an AllocationGapReport record.
5. run_tracker_for_allocation() – called every time a CampusCourseAllocation is
                               saved; orchestrates steps 1-4.
6. generate_full_program_report() – produce a complete multi-year/semester
                               report for a given program (used in the view).
"""

import logging
from django.utils import timezone
from django.contrib.auth.models import User
from core.rbac import Role

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _current_academic_year():
    """Return academic year string like '2024/2025' based on today's date."""
    today = timezone.now().date()
    # Academic year starts in September (adjust if your university differs)
    if today.month >= 9:
        return f"{today.year}/{today.year + 1}"
    return f"{today.year - 1}/{today.year}"


def _cod_user_for_program(program):
    """
    Return the COD User for the program's department, or None.
    COD is determined by the department.leader field.
    """
    dept = getattr(program, 'department', None)
    if dept and dept.leader:
        return dept.leader
    # Fallback: find a user in COD group linked to this department
    try:
        from django.contrib.auth.models import Group
        cod_group = Group.objects.filter(name='COD').first()
        if cod_group:
            # Try to find a COD member whose lecturer profile belongs to this dept
            from lecturer_portal.models import Lecturer
            lecturer = Lecturer.objects.filter(
                department=dept,
                user__groups=cod_group
            ).first()
            if lecturer:
                return lecturer.user
    except Exception as exc:
        logger.warning(f"Could not resolve COD user for program {program}: {exc}")
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Core tracker operations
# ──────────────────────────────────────────────────────────────────────────────

def get_or_create_tracker(program, program_year, semester, academic_year):
    """Return (tracker, created) for the given combination."""
    from .models import ProgramYearTracker
    tracker, created = ProgramYearTracker.objects.get_or_create(
        program=program,
        program_year=program_year,
        semester=semester,
        academic_year=academic_year,
        defaults={
            'expected_courses': [],
            'allocated_courses': [],
            'missing_courses': [],
            'carryover_missing': [],
            'is_complete': False,
            'notification_sent': False,
        }
    )
    return tracker, created


def refresh_tracker(tracker):
    """
    Refresh a tracker's data from live DB records.
    Returns the updated tracker.
    """
    tracker.refresh_from_allocations()
    return tracker


# ──────────────────────────────────────────────────────────────────────────────
# Report builder
# ──────────────────────────────────────────────────────────────────────────────

def build_report_text(tracker):
    """
    Build a clean, notification-ready text report for one tracker record.
    """
    program_name = tracker.program.name
    yr = tracker.program_year
    sem = tracker.semester
    ay = tracker.academic_year

    lines = [
        f"📋 COURSE ALLOCATION TRACKER REPORT",
        f"Program   : {program_name}",
        f"Year      : Year {yr}  |  Semester {sem}",
        f"Acad. Year: {ay}",
        f"Generated : {timezone.now().strftime('%Y-%m-%d %H:%M')}",
        f"{'─' * 50}",
    ]

    # Expected
    lines.append(f"\n✅ Expected Courses ({len(tracker.expected_courses)}):")
    if tracker.expected_courses:
        for code in tracker.expected_courses:
            status = "✔ Allocated" if code in tracker.allocated_courses else "✘ Missing"
            lines.append(f"   {code:15s} {status}")
    else:
        lines.append("   (No standard courses defined for this year/semester)")

    # Current gaps
    if tracker.missing_courses:
        lines.append(f"\n⚠️  Missing Courses This Semester ({len(tracker.missing_courses)}):")
        for code in tracker.missing_courses:
            lines.append(f"   ✘ {code}")
    else:
        lines.append(f"\n✅ No missing courses this semester.")

    # Carryover gaps from previous semesters
    if tracker.carryover_missing:
        lines.append(
            f"\n🔁 Unresolved Carryover Gaps from Previous Semesters "
            f"({len(tracker.carryover_missing)}):"
        )
        for code in tracker.carryover_missing:
            lines.append(f"   ⚠ {code}")
    else:
        lines.append(f"\n✅ No carryover gaps from previous semesters.")

    # Overall status
    lines.append(f"\n{'─' * 50}")
    if tracker.is_complete:
        lines.append("🎉 STATUS: ALL COURSES ALLOCATED — No gaps detected.")
    else:
        total_gaps = len(set(tracker.missing_courses + tracker.carryover_missing))
        lines.append(
            f"🚨 STATUS: {total_gaps} gap(s) detected. "
            f"Please review and allocate the missing courses."
        )

    lines.append(
        "\nNote: Special/extra courses are excluded from this report."
    )
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Notification sender
# ──────────────────────────────────────────────────────────────────────────────

def _timetabling_user():
    """
    Return the primary Timetabling user to notify.
    Priority order:
      1. A user in the 'Director Timetable' group
      2. A user in the 'Timetable Admins' group
    Returns None if neither group has members (fallback to group notification).
    """
    from django.contrib.auth.models import Group
    for group_name in (Role.DIRECTOR, Role.TIMETABLE_ADMIN):
        try:
            grp = Group.objects.get(name=group_name)
            user = grp.user_set.first()
            if user:
                return user
        except Group.DoesNotExist:
            continue
    return None


def send_gap_notification(tracker, force=False):
    """
    Create a Notification for the Timetabling team and save an AllocationGapReport.
    Sent to 'Director Timetable' or 'Timetable Admins' group — NOT the COD.
    By default only sends when there are gaps.  Pass force=True to always send.
    """
    from notifications.models import Notification
    from .models import AllocationGapReport
    from django.contrib.auth.models import Group

    has_gaps = bool(tracker.missing_courses or tracker.carryover_missing)

    if not force and not has_gaps:
        return None

    report_text = build_report_text(tracker)
    tt_user = _timetabling_user()

    # Create notification — prefer specific user, fall back to group
    if tt_user:
        Notification.create_for_user(message=report_text, user=tt_user)
        notify_target = f"user:{tt_user.username}"
    else:
        # Try Director Timetable group first, then Timetable Admins
        notified = False
        for group_name in (Role.DIRECTOR, Role.TIMETABLE_ADMIN):
            try:
                grp = Group.objects.get(name=group_name)
                Notification.create_for_group(message=report_text, group=grp)
                notify_target = f"group:{group_name}"
                notified = True
                break
            except Group.DoesNotExist:
                continue
        if not notified:
            # Last resort: role-based broadcast
            Notification.create_for_role(message=report_text, role='timetabling')
            notify_target = 'role:timetabling'

    # Save immutable report record
    gap_report = AllocationGapReport.objects.create(
        tracker=tracker,
        report_text=report_text,
        sent_to=tt_user,   # None if group/role fallback was used
    )

    tracker.notification_sent = True
    tracker.save(update_fields=['notification_sent'])

    logger.info(
        f"Gap report sent for {tracker.program.name} Y{tracker.program_year}"
        f"S{tracker.semester} {tracker.academic_year} → {notify_target}"
    )
    return gap_report


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point – called after every allocation save
# ──────────────────────────────────────────────────────────────────────────────

def run_tracker_for_allocation(allocation):
    """
    Called every time a CampusCourseAllocation is saved.

    Steps:
    1. Skip if the allocation has no program, program_year, or semester set.
    2. Skip special courses (they are not part of standard tracking).
    3. Ensure a tracker record exists.
    4. Refresh the tracker's data.
    5. Send a notification to the COD if there are gaps.
    6. Also refresh any later trackers for the same program (to update carryover).
    """
    if allocation.is_special_course:
        return  # special courses are excluded from tracking

    if not allocation.program or not allocation.program_year or not allocation.allocation_semester:
        return  # not enough info to track

    academic_year = allocation.academic_year or _current_academic_year()

    try:
        tracker, created = get_or_create_tracker(
            program=allocation.program,
            program_year=allocation.program_year,
            semester=allocation.allocation_semester,
            academic_year=academic_year,
        )
        tracker = refresh_tracker(tracker)
        send_gap_notification(tracker)

        # Cascade: refresh all trackers for later semesters/years of this
        # program so their carryover is up to date
        _refresh_downstream_trackers(allocation.program, academic_year,
                                     allocation.program_year, allocation.allocation_semester)

    except Exception as exc:
        logger.error(f"Tracker error for allocation {allocation.pk}: {exc}", exc_info=True)


def _refresh_downstream_trackers(program, academic_year, from_year, from_semester):
    """Refresh trackers for years/semesters after the given point."""
    from .models import ProgramYearTracker
    from django.db.models import Q

    later_trackers = ProgramYearTracker.objects.filter(
        program=program,
        academic_year=academic_year,
    ).filter(
        Q(program_year__gt=from_year) |
        Q(program_year=from_year, semester__gt=from_semester)
    ).order_by('program_year', 'semester')

    for t in later_trackers:
        try:
            t.refresh_from_allocations()
        except Exception as exc:
            logger.warning(f"Could not refresh downstream tracker {t}: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# Full program report (used in the tracker dashboard view)
# ──────────────────────────────────────────────────────────────────────────────

def generate_full_program_report(program, academic_year=None):
    """
    Return a structured dict describing the full allocation status for a program
    across all tracked years and semesters.

    Structure:
    {
      'program': <Program>,
      'academic_year': '2024/2025',
      'started_from_year': 1,
      'years': [
        {
          'program_year': 1,
          'semesters': [
            {
              'semester': 1,
              'tracker': <ProgramYearTracker or None>,
              'expected': [...],
              'allocated': [...],
              'missing': [...],
              'carryover': [...],
              'is_complete': bool,
              'report_text': str,
            },
            { 'semester': 2, ... },
          ],
          'year_missing': [...],   # union of both semesters
          'year_complete': bool,
        },
        ...
      ],
      'overall_missing': [...],
      'overall_complete': bool,
    }
    """
    from .models import ProgramYearTracker
    from program_management.models import ProgramCourse

    if not academic_year:
        academic_year = _current_academic_year()

    # Determine which years this program has trackers for
    tracked_years = (
        ProgramYearTracker.objects
        .filter(program=program, academic_year=academic_year)
        .values_list('program_year', flat=True)
        .distinct()
        .order_by('program_year')
    )

    # Also consider years with ProgramCourse entries (curriculum definition)
    curriculum_years = (
        ProgramCourse.objects
        .filter(program=program)
        .values_list('year', flat=True)
        .distinct()
        .order_by('year')
    )

    all_years = sorted(set(list(tracked_years) + list(curriculum_years))) or [1]
    started_from = all_years[0]

    overall_missing = []
    years_data = []

    for yr in all_years:
        semesters_data = []
        year_missing = []

        for sem in [1, 2]:
            tracker = ProgramYearTracker.objects.filter(
                program=program,
                program_year=yr,
                semester=sem,
                academic_year=academic_year,
            ).first()

            if tracker:
                tracker.refresh_from_allocations()
                sem_data = {
                    'semester': sem,
                    'tracker': tracker,
                    'expected': tracker.expected_courses,
                    'allocated': tracker.allocated_courses,
                    'missing': tracker.missing_courses,
                    'carryover': tracker.carryover_missing,
                    'is_complete': tracker.is_complete,
                    'report_text': build_report_text(tracker),
                }
                year_missing.extend(tracker.missing_courses)
            else:
                # Check if curriculum has courses for this slot
                expected_qs = ProgramCourse.objects.filter(
                    program=program, year=yr, semester=sem
                ).values_list('course_code', flat=True)
                expected = list(expected_qs)

                sem_data = {
                    'semester': sem,
                    'tracker': None,
                    'expected': expected,
                    'allocated': [],
                    'missing': expected,  # none allocated yet
                    'carryover': [],
                    'is_complete': (len(expected) == 0),
                    'report_text': (
                        f"No allocations created yet for Year {yr} Semester {sem}."
                        if expected else
                        f"No courses defined in curriculum for Year {yr} Semester {sem}."
                    ),
                }
                year_missing.extend(expected)

            semesters_data.append(sem_data)

        overall_missing.extend(year_missing)
        years_data.append({
            'program_year': yr,
            'semesters': semesters_data,
            'year_missing': list(set(year_missing)),
            'year_complete': (len(year_missing) == 0),
        })

    return {
        'program': program,
        'academic_year': academic_year,
        'started_from_year': started_from,
        'years': years_data,
        'overall_missing': list(set(overall_missing)),
        'overall_complete': (len(overall_missing) == 0),
    }
