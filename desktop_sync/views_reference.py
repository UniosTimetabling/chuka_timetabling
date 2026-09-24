"""
desktop_sync/views_reference.py
================================
GET /api/desktop/reference/lab-exam/ -> everything timetable.algorithms.
run_lab_exam_autoscheduler.run_lab_exam_autoscheduler needs as INPUT to build
a lab/workshop exam timetable from scratch: allocations, candidate venues,
lecturers, programs, the ExamSchedulerConfig singleton, and every existing
regular-exam slot per lecturer/program (existing_exam_busy) so a local rebuild
of the algorithm can avoid clashing with the already-published exam timetable
exactly like the web version does.

Read-only mirror: the desktop app never edits these rows, so unlike
views_sync.py there's no push endpoint, no version token, and no conflict
handling here — every pull just replaces the local copy wholesale.

Scoped with the same apply_tt_scope(..., TT_SCOPE_DEFAULT) the web panels use,
so the desktop autoscheduler sees exactly the allocation sets the web one does.
"""
from django.db.models import F
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from course_allocation.allocation_scope import TT_SCOPE_DEFAULT, apply_tt_scope
from course_allocation.models import LabAllocation
from timetable.models import ExamSchedulerConfig, ExamTimetable

from .views_auth import desktop_auth_required

_ELIGIBLE_SCOPE = {"type": "mode", "mode": TT_SCOPE_DEFAULT}


def _serialize_config(cfg: ExamSchedulerConfig) -> dict:
    return {
        "start_date": cfg.start_date.isoformat() if cfg.start_date else None,
        "start_time": cfg.start_time.strftime("%H:%M") if cfg.start_time else None,
        "end_time": cfg.end_time.strftime("%H:%M") if cfg.end_time else None,
        "slot_size": cfg.slot_size,
        "excluded_days": cfg.excluded_date_list(),
        "max_exam_days": cfg.max_exam_days,
        "spacing_ratio": cfg.spacing_ratio,
    }


@require_GET
@desktop_auth_required
def pull_lab_exam_reference(request):
    cfg, _ = ExamSchedulerConfig.objects.get_or_create(
        pk=1,
        defaults={
            "start_date": timezone.now().date(),
        },
    )

    allocations_qs = (
        LabAllocation.objects.select_related(
            "program_course", "program_course__program", "program_course__program__department", "lecturer"
        )
        .prefetch_related("venues", "additional_courses")
        .order_by("id")
    )
    allocations_qs = apply_tt_scope(allocations_qs, request=request, scope=_ELIGIBLE_SCOPE, prefix="allocation_set")

    venues_seen = {}
    lecturers_seen = {}
    programs_seen = {}
    allocations = []

    for alloc in allocations_qs:
        program = alloc.program_course.program if alloc.program_course else None
        if program is not None and program.id not in programs_seen:
            programs_seen[program.id] = {
                "id": program.id,
                "name": str(program),
                "department_name": program.department.name if getattr(program, "department", None) else "",
            }

        lecturer = alloc.lecturer
        if lecturer is not None and lecturer.id not in lecturers_seen:
            lecturers_seen[lecturer.id] = {
                "id": lecturer.id,
                "name": lecturer.name,
                "designation": lecturer.designation,
            }

        venue_ids = []
        for v in alloc.venues.all():
            venue_ids.append(v.id)
            if v.id not in venues_seen:
                venues_seen[v.id] = {"id": v.id, "code": v.code, "capacity": v.capacity}

        allocations.append(
            {
                "id": alloc.id,
                "course_code": alloc.course_code,
                "course_name": alloc.course_name,
                "additional_course_codes": list(alloc.additional_courses.values_list("course_code", flat=True)),
                "program_id": program.id if program else None,
                "lecturer_id": lecturer.id if lecturer else None,
                "venue_ids": venue_ids,
                "number_of_students": alloc.number_of_students,
                "is_workshop_course": alloc.is_workshop_course,
                "allocation_set_id": alloc.allocation_set_id,
            }
        )

    # The original algorithm also refuses a lab-exam slot that clashes with an EXISTING regular
    # exam for the same lecturer or program (see run_lab_exam_autoscheduler._lecturer_free /
    # _program_free). Resolved here with real IDs so the local copy doesn't have to match by
    # name against the desktop's flattened regular-exam mirror.
    existing_exam_busy = list(
        ExamTimetable.objects.select_related("course_allocation")
        .values(
            "date",
            "start_time",
            "end_time",
            lecturer_id=F("course_allocation__lecturer_id"),
            program_id=F("course_allocation__program_id"),
        )
    )
    for row in existing_exam_busy:
        row["date"] = row["date"].isoformat() if row["date"] else None
        row["start_time"] = row["start_time"].strftime("%H:%M") if row["start_time"] else None
        row["end_time"] = row["end_time"].strftime("%H:%M") if row["end_time"] else None

    return JsonResponse(
        {
            "generated_at": timezone.now().isoformat(),
            "config": _serialize_config(cfg),
            "lab_allocations": allocations,
            "lab_venues": list(venues_seen.values()),
            "lecturers": list(lecturers_seen.values()),
            "programs": list(programs_seen.values()),
            "existing_exam_busy": existing_exam_busy,
        }
    )
