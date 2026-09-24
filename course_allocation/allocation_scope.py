"""
course_allocation.allocation_scope
-----------------------------------
Everything the rest of the codebase needs to become "AllocationSet-aware"
without every call site having to know the details:

  - which AllocationSet is currently active for this COD's session
  - populating a newly created set with CourseAllocation rows drawn from
    ProgramCourse, honoring scope=all / scope=selected / pinned courses
  - a queryset helper so views/algorithms can say
    `scope_qs(CourseAllocation.objects.filter(department=dept), allocation_set)`
    and get back exactly that set's rows (or, if allocation_set is None,
    the department's legacy set — so nothing that forgets to pass a set
    silently sees a mix of everything).

Nothing here deletes or mutates existing CourseAllocation rows. Creating a
new AllocationSet's rows always INSERTs new CourseAllocation rows tagged
with that set; it never repoints or edits rows belonging to another set.
"""
from django.db import transaction
from django.db.models import Q

from .models import (
    AllocationSet,
    AllocationSetSemesterComponent,
    AllocationSetComponentCourse,
    CourseAllocation,
)

SESSION_KEY = "active_allocation_set_id"


# ─────────────────────────────────────────────────────────────────────────
# Active-set resolution
# ─────────────────────────────────────────────────────────────────────────
def get_active_allocation_set(request, department):
    """
    Returns the AllocationSet the COD is currently working in for this
    department, or None if they haven't picked one yet this session (the
    view should then redirect to the picker).

    Re-checks is_archived on every call, not just when the set was first
    picked — if it gets hidden mid-session (from another tab, or by
    someone else on the same department), this stops returning it
    immediately instead of waiting for the COD to switch again.
    """
    if department is None:
        return None
    set_id = request.session.get(SESSION_KEY, {}).get(str(department.id)) \
        if isinstance(request.session.get(SESSION_KEY), dict) else None
    if not set_id:
        return None
    return AllocationSet.objects.filter(id=set_id, department=department, is_archived=False).first()


def set_active_allocation_set(request, allocation_set):
    """Remembers this AllocationSet as the active one for its department, in-session."""
    bucket = request.session.get(SESSION_KEY)
    if not isinstance(bucket, dict):
        bucket = {}
    bucket[str(allocation_set.department_id)] = allocation_set.id
    request.session[SESSION_KEY] = bucket
    request.session.modified = True


def get_or_default_legacy_set(department):
    """
    Fallback used by code paths (algorithms, exports, mobile API) that are
    only ever given a department, not an explicit AllocationSet — e.g. not
    yet migrated to the new picker flow. Returns the department's
    is_legacy=True set if one exists (created by the backfill command),
    else None (meaning: caller should fall back to its old
    department-only behaviour, since this department has never had the
    backfill run / has no legacy data).
    """
    return AllocationSet.objects.filter(department=department, is_legacy=True).first()


def resolve_allocation_set_for_request(request, department, allow_default=True):
    """
    The one function most views should call: active session set, else
    (optionally) the legacy set, else None.
    """
    active = get_active_allocation_set(request, department)
    if active:
        return active
    if allow_default:
        return get_or_default_legacy_set(department)
    return None


# ─────────────────────────────────────────────────────────────────────────
# Queryset scoping
# ─────────────────────────────────────────────────────────────────────────
def scope_qs(qs, allocation_set):
    """
    Filter an existing CourseAllocation queryset down to one AllocationSet.
    If allocation_set is None, returns the queryset UNCHANGED (department-only
    scoping) rather than guessing — callers that have no set yet should not
    silently see nothing.
    """
    if allocation_set is None:
        return qs
    return qs.filter(allocation_set=allocation_set)


# ─────────────────────────────────────────────────────────────────────────
# Populating a new AllocationSet from its semester composition
# ─────────────────────────────────────────────────────────────────────────
def eligible_program_courses_for_component(component):
    """
    All ProgramCourse rows this semester-layer covers, given its scope.
    Deliberately imported lazily to avoid a hard cross-app import at
    module load time.
    """
    from program_management.models import ProgramCourse

    dept = component.allocation_set.department
    qs = ProgramCourse.objects.filter(
        semester=component.semester_number,
        program__department=dept,
    )
    if component.scope == AllocationSetSemesterComponent.SCOPE_ALL:
        return qs

    # SELECTED scope: restrict to chosen programs, and further to pinned
    # courses if any pins exist for this component.
    program_ids = list(component.programs.values_list("id", flat=True))
    qs = qs.filter(program_id__in=program_ids) if program_ids else qs.none()

    pinned_ids = list(
        AllocationSetComponentCourse.objects.filter(component=component)
        .values_list("program_course_id", flat=True)
    )
    if pinned_ids:
        qs = qs.filter(id__in=pinned_ids)
    return qs


@transaction.atomic
def populate_allocation_set(allocation_set, created_by=None, num_groups=1):
    """
    Creates one CourseAllocation row per eligible ProgramCourse across every
    semester component of this AllocationSet, tagged with this set.

    Idempotent per (program_course, allocation_set): re-running (e.g. the
    COD adds another semester component later and re-triggers this) will
    not duplicate rows already created for a program_course that's already
    in this set.

    This never touches CourseAllocation rows belonging to ANY OTHER
    AllocationSet — it only ever inserts new rows tagged with THIS one.

    `num_groups`: when > 1, every eligible course is created as that many
    sequentially-lettered groups (e.g. EDFO 111-A, -B, -C, ...) instead of
    a single plain row — mirrors the COD panel / Auto-Allocate Courses
    bulk "Add Group(s)" flow, so a course pulled in through allocation-set
    creation can start life already split into its common-course groups.
    Letters are generated with the SAME Excel-style scheme (A...Z, AA,
    AB, ...) via a deferred import of course_management.cod_panel's
    helpers, to avoid a circular import (cod_panel already imports this
    module at load time).
    """
    num_groups = max(1, int(num_groups or 1))
    already_present_pc_ids = set(
        allocation_set.course_allocations.values_list("program_course_id", flat=True)
    )

    if num_groups == 1:
        created = []
        for component in allocation_set.semester_components.all():
            for pc in eligible_program_courses_for_component(component):
                if pc.id in already_present_pc_ids:
                    continue
                already_present_pc_ids.add(pc.id)
                created.append(CourseAllocation(
                    allocation_set=allocation_set,
                    department=allocation_set.department,
                    origin_department=getattr(pc.program, "department", None),
                    program=pc.program,
                    program_course=pc,
                    course_code=pc.course_code,
                    course_name=pc.course_name,
                    number_of_students=0,
                    intake=CourseAllocation.INTAKE_SPECIAL if allocation_set.is_special else CourseAllocation.INTAKE_NORMAL,
                ))
        if created:
            CourseAllocation.objects.bulk_create(created)
        return len(created)

    # ── num_groups > 1: build each course as N lettered rows ────────────
    from course_management.cod_panel import append_group, index_to_letters

    created_count = 0
    for component in allocation_set.semester_components.all():
        for pc in eligible_program_courses_for_component(component):
            if pc.id in already_present_pc_ids:
                continue
            already_present_pc_ids.add(pc.id)
            rows = []
            for i in range(1, num_groups + 1):
                letter = index_to_letters(i)
                rows.append(CourseAllocation(
                    allocation_set=allocation_set,
                    department=allocation_set.department,
                    origin_department=getattr(pc.program, "department", None),
                    program=pc.program,
                    program_course=pc,
                    course_code=append_group(pc.course_code, letter),
                    course_name=pc.course_name,
                    number_of_students=0,
                    intake=CourseAllocation.INTAKE_SPECIAL if allocation_set.is_special else CourseAllocation.INTAKE_NORMAL,
                ))
            CourseAllocation.objects.bulk_create(rows)
            created_count += len(rows)
    return created_count


def add_selected_courses_by_id(allocation_set, program_course_ids, num_groups=1):
    """
    Safer variant for UI flows where ticked courses may span more than one
    semester within the same program (e.g. a program's course list mixes
    Sem 1 and Sem 2 units): groups the given ProgramCourse ids by their
    ACTUAL semester rather than trusting a single semester_number the
    caller guessed, so each course lands in the correct semester
    component regardless of what else was ticked alongside it.

    `num_groups`: when > 1, each selected course is created as that many
    sequentially-lettered common-course groups (EDFO 111-A, -B, -C, ...)
    instead of a single plain row — the same bulk "select several courses,
    give them all N groups at once" flow as the COD panel and Auto-Allocate
    Courses pages, exposed here for allocation-set creation too.
    """
    from program_management.models import ProgramCourse

    by_semester = {}
    for pc in ProgramCourse.objects.filter(id__in=program_course_ids):
        by_semester.setdefault(pc.semester, []).append(pc)

    total_created = 0
    for semester_number, pcs in by_semester.items():
        total_created += add_selected_courses(
            allocation_set,
            semester_number=semester_number,
            program_ids=[pcs[0].program_id],
            program_course_ids=[pc.id for pc in pcs],
            num_groups=num_groups,
        )
    return total_created


def add_selected_courses(allocation_set, semester_number, program_ids=None, program_course_ids=None, num_groups=1):
    """
    Used by the "select from other semester" screen: adds/extends a
    SELECTED-scope component for `semester_number` with the given programs
    and/or specific program_course pins, then populates the resulting new
    rows. Safe to call repeatedly (e.g. once per program the COD ticks) —
    it only ever adds to the existing component for that semester rather
    than replacing it.

    `num_groups`: see add_selected_courses_by_id — passed straight through
    to populate_allocation_set.
    """
    component, _ = allocation_set.semester_components.get_or_create(
        semester_number=semester_number,
        defaults={"scope": AllocationSetSemesterComponent.SCOPE_SELECTED},
    )
    if component.scope == AllocationSetSemesterComponent.SCOPE_ALL:
        # Already a full-semester layer; nothing more to "select" into it.
        return 0

    if program_ids:
        component.programs.add(*program_ids)
    if program_course_ids:
        for pc_id in program_course_ids:
            AllocationSetComponentCourse.objects.get_or_create(component=component, program_course_id=pc_id)

    return populate_allocation_set(allocation_set, num_groups=num_groups)


# ─────────────────────────────────────────────────────────────────────────
# Timetabling Office (TT) allocation-set SCOPE MODE
# ─────────────────────────────────────────────────────────────────────────
# The COD's "active set" above is per-department and picks exactly ONE
# AllocationSet — that works because a COD only ever works on their own
# department. The TT / timetabling side is the opposite: timetable_panel,
# the exam panel, the lab panels and the autoschedulers all operate ACROSS
# every department on one page / one scheduling run.
#
# So the TT picker works like this: on entry to /timetable/dashboard/
# (dashboards.timetable_dashboard_view) the TT is shown every actual
# AllocationSet across every department (e.g. "Computer Science — Semester
# 1 2026/2027", "Mathematics — Semester 1 2026/2027", a department's
# Special intake set, ...) and ticks which ones are "in" for this session —
# typically one per department, but nothing stops picking several for the
# same department (e.g. Semester 1 + a Special set) or none at all for a
# department that isn't ready yet.
#
# The ticked AllocationSet ids are stored in-session and read by every
# TT-side query helper from here on, via
# get_tt_active_allocation_set_ids() / apply_tt_scope(). If the TT hasn't
# picked anything yet this session, everything falls back to the same
# "eligible" gate the autoscheduler algorithms always used (no set yet, a
# legacy set, or submitted to TT) so nothing breaks before their first
# visit to the dashboard.
#
# Each individual page will eventually get its own switcher (tracked
# separately); until then they all just read whatever was ticked on the
# dashboard.
SESSION_KEY_TT_ACTIVE_SETS = "tt_active_allocation_set_ids"

TT_SCOPE_ELIGIBLE = "eligible"   # fallback gate when nothing's been picked yet
TT_SCOPE_DEFAULT = TT_SCOPE_ELIGIBLE


def get_tt_active_allocation_set_ids(request):
    """
    The AllocationSet ids the TT has explicitly switched on for this
    session (picked on /timetable/dashboard/). Empty list if they haven't
    picked anything yet (or any that got picked have since been deleted
    or hidden).

    Re-validates against is_archived on every call (not only when the
    selection was first made), so an allocation hidden mid-session drops
    out of the timetable/dashboard/reports immediately — the session is
    also cleaned up in place so this stays cheap on repeat calls.
    """
    if request is None:
        return []
    raw = request.session.get(SESSION_KEY_TT_ACTIVE_SETS)
    if not isinstance(raw, list):
        return []
    ids = []
    for v in raw:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    if not ids:
        return []

    valid_ids = list(
        AllocationSet.objects.filter(id__in=ids, is_archived=False).values_list("id", flat=True)
    )
    if len(valid_ids) != len(ids):
        request.session[SESSION_KEY_TT_ACTIVE_SETS] = valid_ids
        request.session.modified = True
    return valid_ids


def set_tt_active_allocation_set_ids(request, ids):
    """
    Validates and remembers the TT's ticked AllocationSets in-session.
    Silently drops anything that isn't a real, non-archived AllocationSet
    id. Returns the list actually stored (possibly empty, if everything
    ticked turned out invalid — callers should treat that the same as
    "nothing picked yet").
    """
    try:
        wanted_ids = {int(v) for v in ids}
    except (TypeError, ValueError):
        wanted_ids = set()

    valid_ids = list(
        AllocationSet.objects.filter(id__in=wanted_ids, is_archived=False)
        .values_list("id", flat=True)
    ) if wanted_ids else []

    request.session[SESSION_KEY_TT_ACTIVE_SETS] = valid_ids
    request.session.modified = True
    return valid_ids


def get_tt_active_allocation_sets(request):
    """The actual AllocationSet rows the TT has switched on, for display (e.g. on the dashboard)."""
    ids = get_tt_active_allocation_set_ids(request)
    if not ids:
        return AllocationSet.objects.none()
    return AllocationSet.objects.filter(id__in=ids).select_related("department")


def resolve_tt_scope(request):
    """
    A small, JSON-serializable snapshot of the current TT scope — safe to
    hand to a background thread (autoscheduler runs), which can't touch
    request.session directly. Resolve this on the request thread BEFORE
    spawning the thread, then pass it through as a plain dict/list.
    """
    ids = get_tt_active_allocation_set_ids(request)
    if ids:
        return {"type": "sets", "ids": ids}
    return {"type": "mode", "mode": TT_SCOPE_DEFAULT}


def tt_scope_q(scope=None, prefix="allocation_set"):
    """
    A Q object expressing which AllocationSets are "in scope", given a
    `scope` snapshot from resolve_tt_scope() (or None, which falls back to
    the default "eligible" gate). `prefix` is the lookup path from the
    queryset's model to AllocationSet — "allocation_set" for a
    CourseAllocation queryset (the default), or e.g.
    "course_allocation__allocation_set" for a Timetable queryset joined
    back to its CourseAllocation.

      {"type": "sets", "ids": [...]} -> ONLY those specific AllocationSets
                                          the TT ticked on the dashboard.
      {"type": "mode", "mode": "eligible"} (or no scope at all) -> no set
                                          yet, OR a legacy set, OR
                                          submitted to TT — the same
                                          fallback gate the autoscheduler
                                          algorithms always used, for
                                          before the TT has picked anything.
    """
    if scope and scope.get("type") == "sets" and scope.get("ids"):
        return Q(**{f"{prefix}_id__in": scope["ids"]})

    # Fallback "eligible" gate.
    return (
        Q(**{f"{prefix}__isnull": True})
        | Q(**{f"{prefix}__is_legacy": True})
        | Q(**{f"{prefix}__status": AllocationSet.STATUS_SUBMITTED_TO_TT})
    )


def apply_tt_scope(qs, request=None, scope=None, prefix="allocation_set"):
    """
    Filter a queryset down to the AllocationSets the TT has switched on.
    Pass either `request` (reads the picked ids from its session) or an
    explicit `scope` snapshot from resolve_tt_scope() — e.g. from a
    background thread that was handed the snapshot up front instead.
    """
    if scope is None:
        scope = resolve_tt_scope(request)
    return qs.filter(tt_scope_q(scope, prefix=prefix))


# ─────────────────────────────────────────────────────────────────────────
# Single-set resolution for high-stakes REGULAR-timetable actions
# (publish, PDF). These are irreversible/visible-to-everyone actions, not
# just a filtered page view, so — unlike the multi-select dashboard scope
# above — they must resolve to exactly ONE AllocationSet, or refuse with a
# clear reason instead of guessing. This is the direct fix for "you pass
# the wrong allocation set and it causes a crisis": a mismatch is blocked
# here, before any query or delete runs, not discovered afterwards.
# ─────────────────────────────────────────────────────────────────────────
class SingleSetResolution:
    """Result of resolve_single_tt_allocation_set(). Never raises — callers
    check `.ok` and show `.error` to the user instead of proceeding."""

    def __init__(self, ok, allocation_set=None, error=None):
        self.ok = ok
        self.allocation_set = allocation_set
        self.error = error

    def __bool__(self):
        return self.ok


def resolve_single_tt_allocation_set(request, department=None, allocation_set_id=None):
    """
    Resolve exactly ONE AllocationSet for a publish/PDF action.

    Resolution order:
      1. `allocation_set_id` given explicitly (e.g. a dropdown on the
         publish/PDF form) — looked up directly. If `department` was also
         given, the set's own department must match it; a mismatch is
         refused here rather than silently publishing into the wrong
         department's timetable.
      2. Otherwise, fall back to whatever's ticked in the TT dashboard
         session (`get_tt_active_allocation_set_ids`) — but ONLY if
         exactly one is ticked. Zero or several is treated as "ambiguous"
         and refused, because guessing which one the person meant is
         exactly the mistake this exists to prevent.
      3. Otherwise: no set to resolve. This is NOT an error by itself —
         it means "no concurrent-set scoping is in play," and callers
         should fall back to their pre-existing (single-set / legacy)
         behaviour unchanged. `allocation_set` will be None and `ok` True.
    """
    if allocation_set_id:
        try:
            allocation_set_id = int(allocation_set_id)
        except (TypeError, ValueError):
            return SingleSetResolution(False, error="Invalid allocation set id.")

        allocation_set = AllocationSet.objects.filter(id=allocation_set_id).first()
        if allocation_set is None:
            return SingleSetResolution(
                False, error=f"Allocation set #{allocation_set_id} does not exist."
            )
        if department is not None and allocation_set.department_id != department.id:
            return SingleSetResolution(
                False,
                error=(
                    f"Allocation set '{allocation_set.name}' belongs to "
                    f"{allocation_set.department}, not {department}. "
                    f"Refusing to act on it in this context — switch to the "
                    f"correct department's set first."
                ),
            )
        return SingleSetResolution(True, allocation_set=allocation_set)

    ticked_ids = get_tt_active_allocation_set_ids(request) if request is not None else []
    if len(ticked_ids) == 1:
        allocation_set = AllocationSet.objects.filter(id=ticked_ids[0]).first()
        if allocation_set is None:
            # Ticked earlier, deleted since — treat as "nothing resolved"
            # rather than erroring, same as an empty pick.
            return SingleSetResolution(True, allocation_set=None)
        if department is not None and allocation_set.department_id != department.id:
            return SingleSetResolution(
                False,
                error=(
                    f"Your active allocation set ('{allocation_set.name}') belongs to "
                    f"{allocation_set.department}, not {department}. "
                    f"Pick the correct allocation set on the timetable dashboard first."
                ),
            )
        return SingleSetResolution(True, allocation_set=allocation_set)

    if len(ticked_ids) > 1:
        return SingleSetResolution(
            False,
            error=(
                "Several allocation sets are active in your session at once "
                "— this action needs exactly one. Pick a single allocation "
                "set (e.g. from the publish/PDF form) instead of relying on "
                "the multi-select dashboard scope for this action."
            ),
        )

    # Nothing ticked at all — not an error, just "no scoping in play".
    return SingleSetResolution(True, allocation_set=None)
