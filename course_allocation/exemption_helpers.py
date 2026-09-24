"""
Shared membership readers + the two collision-exemption rules that every
scheduler and panel must agree on:

  1. NESTED ELECTIVE (pick-one) GROUPS.  Two courses that sit in the SAME
     SelectionGroup are alternatives — a student takes exactly one — so they
     never clash with each other, EVEN WHEN both are also members of the same
     Specialization Stem (a stem of 12 units where 10 are core and the
     student picks one of the last two). Alternative vs. core unit of the
     stem still clash-checks (shared stem => not exempt).

  2. EFFECTIVE STUDENT GROUPS.  A course's cohort is every StudentGroup it is
     mapped to (primary `student_group` + `additional_student_groups`). A
     shared elective pool that is restricted (`restricted_to_groups`) to
     Group A is only ever taken by Group A, so its courses have Group A as
     their effective cohort and can share a slot with another group's units.

Membership is read from a few bulk queries cached for a short TTL (and
explicitly reset at the start of every scheduler run / panel request via
reset_membership_cache()), instead of one query per course per pair.
"""
import threading
import time
from typing import Optional

_TTL_SECONDS = 15.0
_lock = threading.Lock()
_cache = {"loaded_at": 0.0}


def reset_membership_cache():
    with _lock:
        _cache.clear()
        _cache["loaded_at"] = 0.0


def _group_map(pairs):
    out = {}
    for k, v in pairs:
        out.setdefault(k, set()).add(v)
    return {k: frozenset(v) for k, v in out.items()}


def _load():
    from course_allocation.models import (
        CourseAllocation, SelectionGroup, SpecializationStem,
    )
    stem_through = SpecializationStem.courses.through
    pool_through = SelectionGroup.courses.through
    extra_grp = CourseAllocation.additional_student_groups.through
    pool_grp = SelectionGroup.restricted_to_groups.through

    stems = _group_map(stem_through.objects.values_list("courseallocation_id", "specializationstem_id"))
    for aid, sid in CourseAllocation.objects.filter(specialization_stem__isnull=False).values_list("id", "specialization_stem_id"):
        stems[aid] = frozenset(set(stems.get(aid, ())) | {sid})

    pools = _group_map(pool_through.objects.values_list("courseallocation_id", "selectiongroup_id"))
    for aid, pid in CourseAllocation.objects.filter(selection_group__isnull=False).values_list("id", "selection_group_id"):
        pools[aid] = frozenset(set(pools.get(aid, ())) | {pid})

    extra = _group_map(extra_grp.objects.values_list("courseallocation_id", "studentgroup_id"))
    primary = dict(
        CourseAllocation.objects.filter(student_group__isnull=False).values_list("id", "student_group_id")
    )
    pool_groups = _group_map(pool_grp.objects.values_list("selectiongroup_id", "studentgroup_id"))
    pool_stems = _group_map(
        SelectionGroup.specialization_stems.through.objects.values_list("selectiongroup_id", "specializationstem_id")
    )
    _cache.update(
        stems=stems, pools=pools, extra_groups=extra, primary_group=primary,
        pool_groups=pool_groups, pool_stems=pool_stems, loaded_at=time.time(),
    )


def _ensure():
    if time.time() - _cache.get("loaded_at", 0.0) > _TTL_SECONDS:
        with _lock:
            if time.time() - _cache.get("loaded_at", 0.0) > _TTL_SECONDS:
                try:
                    _load()
                except Exception:
                    # Never let a membership lookup break scheduling — fall
                    # back to empty maps (== pre-feature behaviour).
                    _cache.update(
                        stems={}, pools={}, extra_groups={}, primary_group={},
                        pool_groups={}, pool_stems={}, loaded_at=time.time(),
                    )


_EMPTY = frozenset()


def stem_ids(alloc) -> frozenset:
    _ensure()
    aid = getattr(alloc, "id", None)
    ids = set(_cache["stems"].get(aid, _EMPTY))
    legacy = getattr(alloc, "specialization_stem_id", None)
    if legacy:
        ids.add(legacy)
    return frozenset(ids)


def pool_ids(alloc) -> frozenset:
    """Every SelectionGroup (pick-one pool) the allocation belongs to."""
    _ensure()
    aid = getattr(alloc, "id", None)
    ids = set(_cache["pools"].get(aid, _EMPTY))
    legacy = getattr(alloc, "selection_group_id", None)
    if legacy:
        ids.add(legacy)
    return frozenset(ids)


def own_group_ids(alloc) -> frozenset:
    _ensure()
    aid = getattr(alloc, "id", None)
    ids = set(_cache["extra_groups"].get(aid, _EMPTY))
    prim = getattr(alloc, "student_group_id", None) or _cache["primary_group"].get(aid)
    if prim:
        ids.add(prim)
    return frozenset(ids)


def effective_group_ids(alloc) -> frozenset:
    """StudentGroups whose students take this course. Explicit mapping wins;
    otherwise a course that only lives in restricted elective pools inherits
    the groups those pools are restricted to (any unrestricted pool => open to
    everyone => empty set)."""
    own = own_group_ids(alloc)
    if own:
        return own
    pools = pool_ids(alloc)
    if not pools:
        return _EMPTY
    union = set()
    for pid in pools:
        g = _cache["pool_groups"].get(pid, _EMPTY)
        if not g:
            return _EMPTY
        union |= g
    return frozenset(union)


def share_pool(a, b) -> bool:
    pa, pb = pool_ids(a), pool_ids(b)
    return bool(pa and pb and (pa & pb))


def disjoint_groups(a, b) -> bool:
    ga, gb = effective_group_ids(a), effective_group_ids(b)
    return bool(ga and gb and not (ga & gb))


def pool_stem_ids(pool_id) -> frozenset:
    _ensure()
    return _cache["pool_stems"].get(pool_id, _EMPTY)


def alloc_is_stem_elective(alloc) -> bool:
    """True if the allocation is a pick-one alternative nested in a stem."""
    st = stem_ids(alloc)
    if not st:
        return False
    return any(pool_stem_ids(p) & st for p in pool_ids(alloc))
