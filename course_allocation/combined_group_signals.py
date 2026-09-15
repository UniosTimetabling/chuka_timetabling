"""
Enforce a hard invariant: a CourseAllocation may belong to AT MOST ONE
CombinedCourseGroup at a time.

Background / bug this closes
-----------------------------
create_combined_group() and add_to_combined_group() (course_management/
cod_panel.py) are the two normal entry points that add allocations to a
CombinedCourseGroup's `allocations` M2M. add_to_combined_group() already
checked for double-membership before this fix, but create_combined_group()
did not — so a common real-world sequence like "delete the old combined
group, then create a new differently-composed one" could leave an
allocation linked to BOTH the old (not actually deleted, e.g. the delete
call failed or raced) and the new group.

Once that happens, _get_combined_group_for_allocation()
(timetable/timetable_panel.py) has no reliable way to know which group
"owns" the allocation — it just takes CombinedCourseGroup.objects
.filter(allocations__id=allocation.id).first(). Different allocations in
the same Resolve run can then resolve to different groups whose member
sets overlap, so "Resolve Remaining Unscheduled" ends up gluing the old
group's members back onto the new group's placement: two combined groups
merged into one class, which must never happen. It also corrupts
_get_combined_group_meta_map()'s per-allocation "Combined: X ×N" badges,
since that map is keyed by primary_allocation_id with no collision
handling.

This module adds a defense-in-depth signal so the invariant holds
regardless of which code path (existing or future) touches the M2M —
smart_combine, course-combination-template replay, direct ORM/shell
access, migrations/data scripts, etc. — not just the two views above.
Application code should still validate up front and return a friendly
error; this signal is the last line of defense that turns "silent data
corruption" into a loud, immediate failure instead.
"""
from django.db.models.signals import m2m_changed
from django.core.exceptions import ValidationError


def _combined_course_group_allocations_changed(sender, instance, action, pk_set, **kwargs):
    if action != "pre_add" or not pk_set:
        return

    # Local import to avoid any import-order issues at app-loading time.
    from .models import CombinedCourseGroup, CourseAllocation

    conflicting = (
        CourseAllocation.objects
        .filter(pk__in=pk_set, combined_groups__isnull=False)
        .exclude(combined_groups=instance)
        .distinct()
    )
    if conflicting.exists():
        details = ", ".join(
            f"{a.course_code} (id={a.id}, already in "
            f"{', '.join(g.group_code for g in a.combined_groups.exclude(pk=instance.pk))})"
            for a in conflicting
        )
        raise ValidationError(
            "Refusing to add allocation(s) to combined group "
            f"'{instance.group_code}' — already a member of another combined "
            f"group: {details}. An allocation may belong to only one "
            "combined group at a time; remove it from the other group first."
        )


def register_combined_group_signals():
    from .models import CombinedCourseGroup

    m2m_changed.connect(
        _combined_course_group_allocations_changed,
        sender=CombinedCourseGroup.allocations.through,
        dispatch_uid="combined_course_group_allocations_no_overlap",
    )