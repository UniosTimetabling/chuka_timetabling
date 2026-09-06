"""
mobile_api/personal_entries.py
================================
Add/remove/list a student or lecturer's PersonalCourseEntry rows (see
models.py), and resolve the identity kwargs those rows are filtered/
created with.

A lecturer's identity is already individual — scope['lecturer_id'], taken
straight from their userId (see scope.py) — is enough on its own. A
student's scope is a shared cohort ("stu:<dept>:<program>:<year>"), so
personalizing anything for one student out of that cohort additionally
needs the student's own registration number, which the mobile app already
holds (returned at login) and must send as `regNo` on every endpoint that
touches personal entries.
"""
from .models import PersonalCourseEntry
from course_allocation.models import CourseAllocation


class PersonalEntryError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message
        super().__init__(message)


def identity_kwargs(scope, reg_no=None):
    """scope (+ reg_no for students) -> the exact kwargs a person's
    PersonalCourseEntry rows are filtered/created with. Raises
    PersonalEntryError if a student didn't supply one."""
    if scope["role"] == "lecturer":
        return {"role": PersonalCourseEntry.ROLE_LECTURER, "lecturer_id": scope["lecturer_id"]}

    if scope["role"] == "student":
        reg_no = (reg_no or "").strip().upper()
        if not reg_no:
            raise PersonalEntryError(
                400,
                "Please include your registration number (regNo) to manage your added courses.",
            )
        return {"role": PersonalCourseEntry.ROLE_STUDENT, "reg_no": reg_no}

    raise PersonalEntryError(400, f"Unknown role '{scope.get('role')}'.")


def identity_kwargs_or_none(scope, reg_no=None):
    """Same as identity_kwargs, but for read paths (the main/shared
    timetable fetch) where a missing regNo should just mean 'don't merge
    personal additions' rather than fail the whole request."""
    try:
        return identity_kwargs(scope, reg_no=reg_no)
    except PersonalEntryError:
        return None


def list_entries(identity):
    return (
        PersonalCourseEntry.objects.filter(**identity)
        .select_related("course_allocation", "course_allocation__lecturer")
    )


def add_entries(identity, allocation_ids):
    """Adds each allocation id to this person's personal list. Returns
    (added_ids, already_had_ids, invalid_ids) — invalid covers ids that
    don't exist, or that a search would never hand back on its own (a
    non-primary member of a combined group), so a stale client can't add
    something course_search.py/find_courses.py wouldn't itself surface."""
    clean_ids = []
    for a in allocation_ids:
        try:
            clean_ids.append(int(a))
        except (TypeError, ValueError):
            continue
    clean_ids = list(dict.fromkeys(clean_ids))
    if not clean_ids:
        return [], [], []

    valid_allocs = {
        a.id: a
        for a in CourseAllocation.objects.filter(id__in=clean_ids).prefetch_related("combined_groups")
    }

    added, already_had, invalid = [], [], []
    for aid in clean_ids:
        alloc = valid_allocs.get(aid)
        if not alloc:
            invalid.append(aid)
            continue

        combined_list = list(alloc.combined_groups.all())
        combined = combined_list[0] if combined_list else None
        if combined and combined.primary_allocation_id and combined.primary_allocation_id != alloc.id:
            invalid.append(aid)  # secondary member — not directly addable
            continue

        _, created = PersonalCourseEntry.objects.get_or_create(course_allocation_id=aid, **identity)
        (added if created else already_had).append(aid)

    return added, already_had, invalid


def remove_entry(identity, allocation_id):
    try:
        allocation_id = int(allocation_id)
    except (TypeError, ValueError):
        return False
    deleted, _ = PersonalCourseEntry.objects.filter(course_allocation_id=allocation_id, **identity).delete()
    return deleted > 0
