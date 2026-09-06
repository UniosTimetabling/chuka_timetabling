"""
Shared logic for the Special Request (SR) feature.

Kept in one place so the /cod/, /cod/... (ODEL), and campuses cod panels can
all raise SRs against their own allocation model, while the /venues/ panel
(director) reads them back the same way regardless of which panel they came
from.

An SR can cover ONE course ("this unit only") or MANY courses at once
("this lecturer", "this program", "some courses of this program") via the
SpecialRequestAllocation link table.
"""
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from .models import SpecialRequest, SpecialRequestAllocation


def _ct_for(obj_or_cls):
    cls = obj_or_cls if isinstance(obj_or_cls, type) else obj_or_cls.__class__
    return ContentType.objects.get_for_model(cls)


def create_special_request(*, target_allocations, scope, description, user, panel="normal"):
    """
    Create a SpecialRequest covering one or more course-allocation-like
    objects (course_allocation.CourseAllocation, odel_system.ODELCourseAllocation,
    campuses_timetable.CampusCourseAllocation, ...).

    `target_allocations` must be a non-empty list of allocation instances,
    all of the same model, sharing the same department. The first one is
    used to infer department/program/lecturer for the SR header.
    """
    if not target_allocations:
        raise ValueError("At least one allocation must be selected for an SR.")

    primary = target_allocations[0]
    ct = _ct_for(primary)

    sr = SpecialRequest.objects.create(
        content_type=ct,
        object_id=primary.pk,
        panel=panel,
        scope=scope,
        department=primary.department,
        program=getattr(primary, "program", None),
        lecturer=getattr(primary, "lecturer", None),
        course_code=getattr(primary, "course_code", ""),
        course_name=getattr(primary, "course_name", ""),
        description=description.strip(),
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )

    SpecialRequestAllocation.objects.bulk_create([
        SpecialRequestAllocation(
            special_request=sr,
            content_type=ct,
            object_id=a.pk,
            course_code=getattr(a, "course_code", ""),
            course_name=getattr(a, "course_name", ""),
        )
        for a in target_allocations
    ])
    return sr


def update_special_request(sr, *, description=None, status=None):
    fields = []
    if description is not None:
        sr.description = description.strip()
        fields.append("description")
    if status is not None and status in dict(SpecialRequest.STATUS_CHOICES):
        sr.status = status
        fields.append("status")
    if fields:
        fields.append("updated_at")
        sr.save(update_fields=fields)
    return sr


def get_active_special_requests_for_allocation(allocation):
    """
    Return EVERY (open, non-archived) SpecialRequest currently covering this
    allocation, newest first — a single course allocation can legitimately
    be covered by more than one SR at once (e.g. a "this lecturer" SR about
    availability AND a separate "this unit only" SR about a workshop time),
    so re-clicking the SR button on an already-flagged course must show all
    of them, plus an option to raise another one, instead of only the most
    recent request.
    """
    ct = _ct_for(allocation)
    links = (
        SpecialRequestAllocation.objects
        .filter(content_type=ct, object_id=allocation.pk, special_request__archived=False)
        .select_related("special_request")
        .order_by("-special_request__created_at")
    )
    seen = set()
    srs = []
    for link in links:
        if link.special_request_id in seen:
            continue
        seen.add(link.special_request_id)
        srs.append(link.special_request)
    return srs


def get_active_special_request_for_allocation(allocation):
    """
    Backward-compatible singular lookup — returns just the most recent
    active SpecialRequest for this allocation, if any. Prefer
    `get_active_special_requests_for_allocation` (plural) for new code,
    since an allocation may have more than one active SR at once.
    """
    srs = get_active_special_requests_for_allocation(allocation)
    return srs[0] if srs else None


def mark_special_request_constraint_applied(sr, *, constraint_type, constraint_id):
    """
    Record that the director has converted this SR into an actual
    autoscheduler constraint row (see dashboards.venues_panel). Also nudges
    the SR from "open" to "acknowledged" if it hasn't been actioned yet —
    the director has now done something about it, even though the COD may
    still want to mark it fully "resolved" later.
    """
    sr.constraint_applied_type = constraint_type
    sr.constraint_applied_id = constraint_id
    sr.constraint_applied_at = timezone.now()
    fields = ["constraint_applied_type", "constraint_applied_id", "constraint_applied_at", "updated_at"]
    if sr.status == SpecialRequest.STATUS_OPEN:
        sr.status = SpecialRequest.STATUS_ACKNOWLEDGED
        fields.append("status")
    sr.save(update_fields=fields)
    return sr


def archive_special_requests_for_allocation(allocation, reason="Course allocation deleted"):
    """
    Remove this allocation's link. If a SpecialRequest has no course links
    left afterwards (its last remaining course was just removed), archive
    it — it never gets hard-deleted, so the history stays visible to the
    director under "show archived".
    """
    ct = _ct_for(allocation)
    links = SpecialRequestAllocation.objects.filter(content_type=ct, object_id=allocation.pk)
    sr_ids = list(links.values_list("special_request_id", flat=True).distinct())
    links.delete()

    for sr in SpecialRequest.objects.filter(id__in=sr_ids, archived=False):
        if not sr.allocation_links.exists():
            sr.archive(reason=reason)


def archive_special_requests_for_allocations(allocations, reason="Course allocations archived"):
    """Bulk version — used when a COD archives a whole semester at once."""
    for a in allocations:
        archive_special_requests_for_allocation(a, reason=reason)


def tag_semester_for_allocations(allocations, semester):
    """
    Stamp `semester` onto every open SR that still has at least one of
    these allocations linked, right before a semester rollover.
    """
    if not allocations or not semester:
        return
    ct = _ct_for(allocations[0])
    ids = [a.pk for a in allocations]
    sr_ids = (
        SpecialRequestAllocation.objects
        .filter(content_type=ct, object_id__in=ids)
        .values_list("special_request_id", flat=True)
        .distinct()
    )
    SpecialRequest.objects.filter(id__in=sr_ids, archived=False).update(semester=semester)


def carry_forward_special_requests(new_allocation, panel="normal"):
    """
    Called right after a new CourseAllocation-like row is created for a
    semester. If there's a still-open SR for the SAME lecturer, the SAME
    program, or the SAME program+course_code, attach the NEW allocation as
    an extra course on that existing SR (rather than creating a duplicate),
    so the request automatically "follows" the course/lecturer/program into
    the new semester without the COD re-submitting it.
    """
    department = getattr(new_allocation, "department", None)
    lecturer = getattr(new_allocation, "lecturer", None)
    program = getattr(new_allocation, "program", None)
    course_code = getattr(new_allocation, "course_code", "")

    if not department:
        return []

    candidates = SpecialRequest.objects.filter(department=department, archived=False)

    matches = set()
    if lecturer:
        matches.update(candidates.filter(scope=SpecialRequest.SCOPE_LECTURER, lecturer=lecturer))
    if program:
        matches.update(candidates.filter(scope=SpecialRequest.SCOPE_PROGRAM, program=program))
        if course_code:
            matches.update(candidates.filter(
                scope=SpecialRequest.SCOPE_PROGRAM_COURSES,
                program=program,
                allocation_links__course_code__iexact=course_code,
            ).distinct())

    ct = _ct_for(new_allocation)
    linked = []
    for sr in matches:
        already = SpecialRequestAllocation.objects.filter(
            special_request=sr, content_type=ct, object_id=new_allocation.pk,
        ).exists()
        if already:
            continue
        SpecialRequestAllocation.objects.create(
            special_request=sr,
            content_type=ct,
            object_id=new_allocation.pk,
            course_code=course_code,
            course_name=getattr(new_allocation, "course_name", ""),
        )
        linked.append(sr)
    return linked


def list_special_requests_by_department(include_archived=False):
    """Used by the director's /venues/ Special Requests tab."""
    qs = SpecialRequest.objects.select_related(
        "department", "program", "lecturer", "created_by",
    ).prefetch_related("allocation_links").order_by("department__name", "-created_at")
    if not include_archived:
        qs = qs.filter(archived=False)
    return qs
