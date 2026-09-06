"""
Zero-Student Course Report
===========================
Generates a PDF (or JSON) report of all CourseAllocations whose
number_of_students is 0 or NULL.

Access is role-scoped:
  - COD / COD Admins  → sees only their own department's zero-student courses
  - Director Timetable / Timetable Admins → sees ALL departments

URL name: zero_student_report_pdf   (PDF download)
URL name: zero_student_report_json  (JSON data — used by timetable panel JS)
"""

from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.db.models import Q

from weasyprint import HTML

from course_allocation.models import CourseAllocation
from course_allocation.detect_user_department import detect_user_department
from timetable.models import TempTimetable


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_timetabler(user):
    return user.groups.filter(
        name__in=["Director Timetable", "Timetable Admins"]
    ).exists()


def _is_cod(user):
    return user.groups.filter(
        name__in=["COD", "COD Admins"]
    ).exists()


def _get_zero_student_qs(user):
    """
    Return a queryset of zero-student CourseAllocations scoped by role.
    Timetablers see all; CODs see only their department.
    """
    qs = (
        CourseAllocation.objects
        .select_related('department', 'department__faculty', 'lecturer', 'program')
        .filter(
            Q(number_of_students__isnull=True) | Q(number_of_students=0)
        )
        .filter(
            Q(department__submission_control__allow_submission_to_tt=True)
            | Q(department__submission_control__isnull=True)
        )
        .order_by('department__name', 'course_code')
    )

    if _is_timetabler(user):
        return qs  # full view

    if _is_cod(user):
        dept = detect_user_department(user)
        if dept:
            return qs.filter(department=dept)
        return qs.none()

    # Fallback: staff / superusers see everything
    if user.is_staff or user.is_superuser:
        return qs

    return qs.none()


def _build_placement_map(allocations):
    """
    Return {alloc_id: {day, start, end, venue}} for any allocation
    that has a TempTimetable slot.
    """
    alloc_ids = [a.id for a in allocations]
    placement_map = {}
    for tt in TempTimetable.objects.filter(
        course_allocation_id__in=alloc_ids
    ).select_related('venue').order_by('course_allocation_id', 'start_time'):
        if tt.course_allocation_id not in placement_map:
            placement_map[tt.course_allocation_id] = {
                'day':   tt.day or '—',
                'start': tt.start_time.strftime('%H:%M') if tt.start_time else '—',
                'end':   tt.end_time.strftime('%H:%M')   if tt.end_time   else '—',
                'venue': tt.venue.code if tt.venue else '—',
            }
    return placement_map


def _build_report_data(user):
    """
    Build the grouped-by-department report structure.
    Returns (by_department, total_count, is_timetabler_view)
    """
    allocations = list(_get_zero_student_qs(user))
    placement_map = _build_placement_map(allocations)

    by_department = defaultdict(list)
    for alloc in allocations:
        dept_name = alloc.department.name if alloc.department else 'No Department'
        by_department[dept_name].append({
            'course_code':  alloc.course_code or '—',
            'course_name':  alloc.course_name or '—',
            'lecturer':     alloc.lecturer.display_name if alloc.lecturer else '—',
            'program':      alloc.program.name if alloc.program else '—',
            'department':   dept_name,
            'faculty':      alloc.department.faculty.name if (alloc.department and alloc.department.faculty) else '—',
            'placement':    placement_map.get(alloc.id),
        })

    # Sort departments alphabetically
    by_department_sorted = dict(sorted(by_department.items()))
    return by_department_sorted, len(allocations), _is_timetabler(user)


# ──────────────────────────────────────────────────────────────────────────────
# Views
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def zero_student_report_pdf(request):
    """
    Download a PDF report of zero-student course allocations.
    Role-scoped: COD sees own department, timetabler sees all.
    """
    by_department, total, is_tt = _build_report_data(request.user)

    from django.utils import timezone
    context = {
        'by_department':    by_department,
        'total':            total,
        'is_timetabler':    is_tt,
        'generated_at':     timezone.now(),
        'user':             request.user,
    }

    html_string = render_to_string(
        'export/zero_student_report_pdf.html', context, request=request
    )

    pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()

    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="zero_student_courses_report.pdf"'
    return response


@login_required
def zero_student_report_json(request):
    """
    JSON endpoint consumed by the timetable panel JS to populate the
    zero-courses section grouped by department.

    Response shape:
    {
        "total": <int>,
        "by_department": {
            "<dept name>": [
                {
                    "course_code": "...",
                    "course_name": "...",
                    "lecturer":    "...",
                    "program":     "...",
                    "department":  "...",
                    "placement": null | {day, start, end, venue}
                },
                ...
            ]
        },
        "report_url": "/export/zero-student-report/"
    }
    """
    by_department, total, _ = _build_report_data(request.user)

    from django.urls import reverse
    try:
        report_url = reverse('zero_student_report_pdf')
    except Exception:
        report_url = '/export/zero-student-report/'

    return JsonResponse({
        'total':         total,
        'by_department': by_department,
        'report_url':    report_url,
    }, safe=True)