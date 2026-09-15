"""
mobile_api/timetable_builder.py
=================================
Turns a resolved scope (see scope.py) into:
  - a `timetable.models.Timetable` queryset for exactly that scope,
  - a cheap version hash for that queryset — same technique as
    export_import.program_year_pdf_cache._scope_queryset_and_hash, so a
    change that invalidates that PDF cache also changes what the mobile
    app sees as "stale" and re-syncs,
  - the day -> [entries] JSON matrix the mobile app renders directly.

NOTE on "lastUpdated": timetable.models.Timetable has no created_at /
updated_at field, so there's no true last-changed timestamp to hand back
— only "has the underlying data changed" (the version hash). The version
endpoint returns lastUpdated as the time of the check itself, not the
time of the actual change; the mobile app treats the version hash, not
this timestamp, as the source of truth for staleness.

NOTE on `personal_identity`: every public function here takes an optional
`personal_identity` (the kwargs dict from mobile_api.personal_entries) so
a person's manually-searched-and-added courses (PersonalCourseEntry — see
models.py and mobile_api/course_search.py) show up merged into their
regular timetable, tagged "personal": True. Passing None (the default —
every existing caller that predates this feature still does) reproduces
the exact original scope-only behaviour and hash, so nothing already
deployed changes just because this feature landed.

The functions below with an `exam_` prefix are the same design applied to
`timetable.models.ExamTimetable` instead of `Timetable` — same scope
filter (mirrors export_import.program_year_pdf_cache._scope_queryset_and_hash
for the "exam" timetable_type too), same version-hash technique, same
owner-name resolution via scope.py. The one real difference is the
published exam schedule is date-based, not just a weekday-based recurring
grid, so `exam_serialize_timetable` groups entries by calendar date
(carrying the weekday label alongside each date group) instead of by day.
"""
import hashlib

from django.db.models import Q
from django.utils import timezone


def queryset_for_scope(scope):
    from timetable.models import Timetable

    base = Timetable.objects.filter(
        venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
    ).select_related(
        "venue", "course_allocation", "course_allocation__lecturer",
    )

    if scope["role"] == "student":
        return base.filter(
            course_allocation__program_id=scope["program_id"],
            course_allocation__program_course__year=scope["year"],
        ).filter(
            Q(course_allocation__department_id=scope["department_id"]) |
            Q(course_allocation__program__department_id=scope["department_id"])
        ).order_by("day", "start_time", "venue__code")

    if scope["role"] == "lecturer":
        return base.filter(
            course_allocation__lecturer_id=scope["lecturer_id"],
        ).order_by("day", "start_time", "venue__code")

    return Timetable.objects.none()


def _personal_alloc_ids(personal_identity):
    """Expand a person's PersonalCourseEntry rows out to every underlying
    CourseAllocation id whose Timetable row might carry the actual class
    slot — for a combined-group entry that's every member, not just the
    stored primary, mirroring timetable/find_courses.py's "scheduled" check
    (older data can still have the Timetable row sitting on a non-primary
    member)."""
    from .models import PersonalCourseEntry

    entries = PersonalCourseEntry.objects.filter(**personal_identity).select_related(
        "course_allocation"
    ).prefetch_related("course_allocation__combined_groups__allocations")

    alloc_ids = set()
    for entry in entries:
        alloc = entry.course_allocation
        combined_list = list(alloc.combined_groups.all())
        if combined_list:
            alloc_ids.update(combined_list[0].allocations.values_list("id", flat=True))
        else:
            alloc_ids.add(alloc.id)
    return alloc_ids


def compute_version(scope, personal_identity=None):
    ids = list(queryset_for_scope(scope).values_list("id", flat=True))
    base_str = f"{len(ids)}:{max(ids) if ids else 0}:{sum(ids)}"

    if personal_identity:
        personal_ids = _personal_alloc_ids(personal_identity)
        if personal_ids:
            base_str += f":P{sum(personal_ids)}.{len(personal_ids)}"

    digest = hashlib.sha256(base_str.encode()).hexdigest()
    return digest[:16]  # short, still effectively collision-free for this purpose


def serialize_timetable(scope, owner_name, personal_identity=None):
    rows = list(queryset_for_scope(scope))
    personal_row_ids = set()

    if personal_identity:
        from timetable.models import Timetable

        personal_ids = _personal_alloc_ids(personal_identity)
        if personal_ids:
            base_ids = {r.id for r in rows}
            extra_rows = list(
                Timetable.objects.filter(
                    course_allocation_id__in=personal_ids,
                    venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
                )
                .exclude(id__in=base_ids)
                .select_related("venue", "course_allocation", "course_allocation__lecturer")
            )
            personal_row_ids = {r.id for r in extra_rows}
            rows = rows + extra_rows
            rows.sort(key=lambda r: (r.day, r.start_time, r.venue.code))

    by_day = {}
    for row in rows:
        alloc = row.course_allocation
        lecturer_name = alloc.lecturer.name if alloc.lecturer else "Unassigned"
        entry = {
            "timeSlot": f"{row.start_time.strftime('%H:%M')}-{row.end_time.strftime('%H:%M')}",
            "venue": row.venue.code,
            "course": f"{alloc.course_code} - {alloc.course_name}",
            "lecturer": lecturer_name,
            "allocationId": alloc.id,
            "personal": row.id in personal_row_ids,
        }
        by_day.setdefault(row.day, []).append(entry)

    days = [{"day": day, "entries": entries} for day, entries in by_day.items()]

    return {
        "owner": {"name": owner_name, "role": scope["role"]},
        "version": compute_version(scope, personal_identity),
        "lastUpdated": timezone.now().isoformat(),
        "days": days,
    }


# ─────────────────────────────────────────────────────────────
#  Exam timetable — same design, timetable.models.ExamTimetable
# ─────────────────────────────────────────────────────────────

def exam_queryset_for_scope(scope):
    from timetable.models import ExamTimetable

    base = ExamTimetable.objects.filter(
        venue__isnull=False, start_time__isnull=False, end_time__isnull=False,
    ).select_related(
        "venue", "course_allocation", "course_allocation__lecturer",
    )

    if scope["role"] == "student":
        return base.filter(
            course_allocation__program_id=scope["program_id"],
            course_allocation__program_course__year=scope["year"],
        ).filter(
            Q(course_allocation__department_id=scope["department_id"]) |
            Q(course_allocation__program__department_id=scope["department_id"])
        ).order_by("date", "start_time", "venue__code")

    if scope["role"] == "lecturer":
        return base.filter(
            course_allocation__lecturer_id=scope["lecturer_id"],
        ).order_by("date", "start_time", "venue__code")

    return ExamTimetable.objects.none()


def exam_compute_version(scope):
    ids = list(exam_queryset_for_scope(scope).values_list("id", flat=True))
    digest = hashlib.sha256(f"{len(ids)}:{max(ids) if ids else 0}:{sum(ids)}".encode()).hexdigest()
    return digest[:16]


def exam_serialize_timetable(scope, owner_name):
    rows = exam_queryset_for_scope(scope)

    by_date = {}
    for row in rows:
        alloc = row.course_allocation
        lecturer_name = alloc.lecturer.name if alloc.lecturer else "Unassigned"
        entry = {
            "timeSlot": f"{row.start_time.strftime('%H:%M')}-{row.end_time.strftime('%H:%M')}",
            "venue": row.venue.code,
            "course": f"{alloc.course_code} - {alloc.course_name}",
            "lecturer": lecturer_name,
        }
        by_date.setdefault(row.date, {"day": row.day, "entries": []})
        by_date[row.date]["entries"].append(entry)

    dates = [
        {"date": date.isoformat(), "day": bucket["day"], "entries": bucket["entries"]}
        for date, bucket in sorted(by_date.items())
    ]

    return {
        "owner": {"name": owner_name, "role": scope["role"]},
        "version": exam_compute_version(scope),
        "lastUpdated": timezone.now().isoformat(),
        "dates": dates,
    }
