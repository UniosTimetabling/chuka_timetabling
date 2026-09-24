"""
course_allocation.course_mapping
--------------------------------
Advance mapping of CURRICULUM courses (ProgramCourse = the course master) to
combination stems and elective groups.

The Student Groups / Stems / Electives page lets a COD say, before any
allocation exists, "this ProgramCourse belongs to this stem / elective group"
(SpecializationStem.program_courses / SelectionGroup.program_courses).

This module is the "comparison" step: whenever a CourseAllocation exists (or is
created) for a mapped ProgramCourse, it is attached to the stem(s) / elective
group(s) the course was mapped to.

Rules
  * The stem's / group's allocation set must match the allocation's set
    (a target with no set matches any set; an allocation with no set matches any).
  * Allocations bound to a Student Group are SKIPPED: the model forbids a
    course being both group-bound and stem/elective-bound (see
    CourseAllocation.clean). They are counted so the page can report them.
  * Stem  : the allocation is added to stem.courses and its legacy
            `specialization_stem` pointer is re-synced.
  * Group : the allocation is flagged is_elective, added to group.courses, its
            `selection_group` pointer is set if empty, and the pool's courses
            are re-synced into any stem the pool is nested in.
Nothing is ever removed here. The functions above never create an allocation;
`map_courses_to_allocation()` at the bottom is the one explicit exception — it is
what the "Map to course allocation" dialog on /groups-electives/ calls, and it DOES
create missing allocations (see its docstring).
"""
import logging
import threading
from contextlib import contextmanager

from django.db import transaction

from .models import BaseSelection, CombinedCourseGroup, CourseAllocation, SelectionGroup, SpecializationStem
from .section_utils import max_sections_for_code, section_code

logger = logging.getLogger(__name__)

# Lets a caller that creates an allocation on purpose (an extra section of a
# course, aimed at specific stems only) stop the post_save signal below from
# also attaching that new row to EVERY other stem / group the ProgramCourse is
# mapped to.
_local = threading.local()


@contextmanager
def suppress_auto_attach():
    prev = getattr(_local, "suppress", False)
    _local.suppress = True
    try:
        yield
    finally:
        _local.suppress = prev


def _set_ok(target_set_id, allocation_set_id):
    return target_set_id is None or allocation_set_id is None or target_set_id == allocation_set_id


def _attach_to_stem(ca, stem):
    """True if the allocation was newly added to the stem."""
    if not _set_ok(stem.category.allocation_set_id, ca.allocation_set_id):
        return False
    if stem.courses.filter(pk=ca.pk).exists():
        return False
    stem.courses.add(ca)
    ca.refresh_primary_stem_pointer()
    return True


def _attach_to_group(ca, group):
    """True if the allocation was newly added to the elective group."""
    if not _set_ok(group.allocation_set_id, ca.allocation_set_id):
        return False
    if group.courses.filter(pk=ca.pk).exists():
        return False
    updates = {}
    if not ca.is_elective:
        updates["is_elective"] = True
        ca.is_elective = True
    if ca.selection_group_id is None:
        updates["selection_group_id"] = group.pk
        ca.selection_group_id = group.pk
    if updates:
        # queryset.update -> no post_save, no recursion
        CourseAllocation.objects.filter(pk=ca.pk).update(**updates)
    group.courses.add(ca)
    group.sync_courses_into_mapped_stems()
    return True


def detach_allocations_from_stem(stem, allocation_ids):
    """
    Take allocations OUT of a combination stem's course allocation — the reverse of
    _attach_to_stem. The allocations themselves are never deleted, and the stem's
    program-course mapping is left alone (an "Apply" would attach them again).

    Courses that come in through an elective pool nested in the stem cannot be
    removed here (the pool would just re-sync them into the stem) — they are reported
    back as `pooled`, to be removed from the elective group instead.
    Returns {"removed": [CourseAllocation...], "pooled": [...], "not_in_stem": [...]}.
    """
    pool_ids = set()
    for pool in stem.elective_groups.all():
        pool_ids.update(pool.courses.values_list("id", flat=True))
    in_stem = {ca.pk: ca for ca in stem.courses.filter(pk__in=allocation_ids)}
    out = {"removed": [], "pooled": [], "not_in_stem": []}
    with transaction.atomic():
        for aid in dict.fromkeys(allocation_ids):
            ca = in_stem.get(aid)
            if ca is None:
                out["not_in_stem"].append(aid)
            elif ca.pk in pool_ids:
                out["pooled"].append(ca)
            else:
                stem.courses.remove(ca)
                ids = list(ca.specialization_stems.values_list("id", flat=True))
                pointer = ca.specialization_stem_id
                if len(ids) == 1:
                    want = ids[0]
                elif pointer == stem.pk:
                    want = None
                else:
                    want = pointer
                if pointer != want:
                    # queryset.update -> no post_save side effects
                    CourseAllocation.objects.filter(pk=ca.pk).update(specialization_stem_id=want)
                out["removed"].append(ca)
    return out


def attach_allocation(ca):
    """Attach ONE allocation to every stem / elective group its ProgramCourse is mapped to."""
    out = {"stems": 0, "groups": 0, "skipped_student_group": 0}
    if not ca.program_course_id:
        return out
    stems = list(SpecializationStem.objects.filter(program_courses=ca.program_course_id)
                 .select_related("category"))
    groups = list(SelectionGroup.objects.filter(program_courses=ca.program_course_id))
    if not stems and not groups:
        return out
    if ca.student_group_id:
        out["skipped_student_group"] = len(stems) + len(groups)
        return out
    for st in stems:
        if _attach_to_stem(ca, st):
            out["stems"] += 1
    for g in groups:
        if _attach_to_group(ca, g):
            out["groups"] += 1
    return out


def sync_target(target):
    """
    Attach every EXISTING allocation of the target's mapped ProgramCourses to the
    target (a SpecializationStem or a SelectionGroup). Returns counts.
    """
    is_stem = isinstance(target, SpecializationStem)
    target_set = target.category.allocation_set_id if is_stem else target.allocation_set_id
    pc_ids = list(target.program_courses.values_list("id", flat=True))
    res = {"attached": 0, "already": 0, "skipped_student_group": 0,
           "mapped": len(pc_ids), "without_allocation": 0}
    if not pc_ids:
        return res

    qs = CourseAllocation.objects.filter(program_course_id__in=pc_ids)
    if target_set is not None:
        qs = qs.filter(allocation_set_id=target_set)
    seen_pc = set()
    with transaction.atomic():
        for ca in qs.order_by("id"):
            seen_pc.add(ca.program_course_id)
            if ca.student_group_id:
                res["skipped_student_group"] += 1
                continue
            done = _attach_to_stem(ca, target) if is_stem else _attach_to_group(ca, target)
            res["attached" if done else "already"] += 1
    res["without_allocation"] = len(set(pc_ids) - seen_pc)
    return res


def _on_allocation_created(sender, instance, created=False, raw=False, **kwargs):
    if not created or raw or getattr(_local, "suppress", False):
        return
    try:
        with transaction.atomic():
            attach_allocation(instance)
    except Exception:  # never block an allocation from being saved
        logger.exception("course_mapping: could not attach allocation %s", getattr(instance, "pk", None))


def register_course_mapping_signals():
    from django.db.models.signals import post_save
    post_save.connect(_on_allocation_created, sender=CourseAllocation,
                      dispatch_uid="course_mapping_attach_on_create")


# ─────────────────────────────────────────────────────────────────────────────
# "Map to course allocation": add the course allocations a stem / group is missing
# ─────────────────────────────────────────────────────────────────────────────

def _target_facts(target):
    """(is_stem, department, program_id, allocation_set) of a stem / elective group."""
    if isinstance(target, SpecializationStem):
        cat = target.category
        return True, cat.department, cat.program_id, cat.allocation_set
    return False, target.department, target.program_id, target.allocation_set


def _attach(ca, target, is_stem):
    return _attach_to_stem(ca, target) if is_stem else _attach_to_group(ca, target)


def _new_section(source):
    """
    Create one more SECTION of `source`'s course (same cohort, same curriculum
    course) — the "COSC 103 twice because the students are split" case. Follows the
    numbering the COD panel's "Split into sections" uses: the original row is
    section 1, extra rows are 2, 3, ... with the code from section_utils.section_code
    ("COSC 103" -> "COSC 103-2"). The new row starts with 0 students and no lecturer;
    nothing on the original row changes apart from it being labelled section 1.
    Returns (new_allocation_or_None, reason_if_none).
    """
    fam = list(CourseAllocation.objects.filter(
        program_course_id=source.program_course_id,
        department_id=source.department_id,
        allocation_set_id=source.allocation_set_id,
        intake=source.intake,
        is_evening_weekend=source.is_evening_weekend,
        student_group_id=source.student_group_id,
        special_intake_group_id=source.special_intake_group_id,
    ))
    used = {r.section_number or 1 for r in fam} | {source.section_number or 1}
    cap = max_sections_for_code(source.course_code)
    n = 2
    while True:
        if n > cap:
            return None, f"{source.course_code} cannot carry more than {cap} sections"
        if n in used:
            n += 1
            continue
        code = section_code(source.course_code, n)
        if CourseAllocation.objects.filter(
            program_id=source.program_id, course_code__iexact=code, intake=source.intake,
            student_group_id=source.student_group_id, allocation_set_id=source.allocation_set_id,
        ).exists():
            n += 1
            continue
        break

    if source.section_number is None:
        source.section_number = 1
        source.save(update_fields=["section_number"])

    with suppress_auto_attach():
        clone = CourseAllocation.objects.create(
            course_code=code,
            course_name=source.course_name,
            department=source.department,
            origin_department=source.origin_department,
            program=source.program,
            program_course=source.program_course,
            intake=source.intake,
            is_elective=source.is_elective,
            is_evening_weekend=source.is_evening_weekend,
            number_of_students=0,
            lecturer=None,
            allocation_set=source.allocation_set,
            student_group=source.student_group,
            special_intake_group=source.special_intake_group,
            section_number=n,
        )
    clone.additional_student_groups.set(source.additional_student_groups.all())
    return clone, None


def map_courses_to_allocation(targets, program_courses, allow_duplicates=False, user=None):
    """
    Put curriculum courses (ProgramCourse) into the COURSE ALLOCATION of one or many
    stems / elective groups, adding whatever is missing, and make sure each course is
    also mapped at program-course level on every target it lands in.

    Per (course, target):
      * target has no allocation of the course yet
            -> attach the existing shared allocation(s) of that course (same allocation
               set), or create one if the course has none.
      * target already has an allocation of the course
            -> SKIPPED and reported, unless `allow_duplicates`; then one more section of
               that course is created and attached to the targets that already had it
               (one new row per course, shared by all of them — the same way one course
               allocation is shared by several stems everywhere else).
      * course belongs to another program than the target -> reported, nothing done.
    Targets with the same allocation set are processed together; each course is its own
    savepoint, so one failure does not undo the rest.

    Returns {"counts": {...}, "lines": [{"kind","target","code","text"}...]} where kind is
    added | attached | duplicate | skipped | error.

    Combining across programs: if `program_courses` includes the SAME course code from
    TWO OR MORE different programs (e.g. "EDFO 111" in both BEd Arts and BEd Science) and
    `targets` includes a stem/group from each of those programs, each program's own
    CourseAllocation row for that course ends up attached to its own program's target as
    usual — but since it is really one shared class, this also wraps those rows together
    in a CombinedCourseGroup afterwards (see _combine_cross_program), so scheduling treats
    them as one session needing a single lecturer/venue/time slot. The result dict's
    "combined_groups" key lists what got combined.
    """
    counts = {"added": 0, "attached": 0, "duplicate": 0, "skipped": 0, "error": 0, "pc_mapped": 0}
    lines = []

    def note(kind, target, pc, text):
        counts[kind] += 1
        lines.append({"kind": kind, "target": target.name if target is not None else "", "code": pc.course_code, "text": text})

    codes_by_program = {}
    for p in program_courses:
        codes_by_program.setdefault(p.program_id, set()).add(p.course_code.strip().upper())

    for pc in program_courses:
        eligible = []
        for t in targets:
            _is_stem, _dept, t_program_id, _set = _target_facts(t)
            if t_program_id and t_program_id != pc.program_id:
                # Not an error when this looks like a deliberate cross-program combine:
                # some OTHER course in this same batch shares this course's code AND
                # belongs to the target's own program — that sibling pc will map to
                # this target on its own turn through this loop instead.
                sibling_present = pc.course_code.strip().upper() in codes_by_program.get(t_program_id, set())
                if not sibling_present:
                    note("error", t, pc, f"{pc.course_code} belongs to a different program than '{t.name}' — not mapped.")
                continue
            eligible.append(t)
        if not eligible:
            continue

        by_set = {}
        for t in eligible:
            alloc_set = _target_facts(t)[3]
            by_set.setdefault(alloc_set.pk if alloc_set else None, []).append(t)

        for group in by_set.values():
            snap_counts, snap_lines = dict(counts), len(lines)
            try:
                with transaction.atomic():
                    _map_one_course(pc, group, allow_duplicates, note, counts)
                    changed = counts["added"] + counts["attached"] + counts["duplicate"]
                    if changed > snap_counts["added"] + snap_counts["attached"] + snap_counts["duplicate"]:
                        BaseSelection.objects.get_or_create(
                            program_course=pc, department=_target_facts(group[0])[1],
                            defaults={"created_by": user})
            except Exception as exc:  # this course rolled back; keep going with the rest
                logger.exception("map_courses_to_allocation: %s failed", pc.course_code)
                counts.clear(); counts.update(snap_counts)
                del lines[snap_lines:]
                note("error", None, pc, f"{pc.course_code}: {exc}")

    # Combining is a bonus step on top of rows that are already mapped: it must never
    # turn a successful mapping into a 500. Whatever cannot be combined is reported.
    try:
        combined_groups, combine_problems = _combine_cross_program(targets, program_courses, user)
    except Exception as exc:
        logger.exception("map_courses_to_allocation: combining across programs failed")
        combined_groups = []
        combine_problems = [{"group_code": "", "programs": [], "reason": f"Combining across programs failed: {exc}"}]
    for prob in combine_problems:
        label = prob["group_code"] or "Cross-program combine"
        lines.append({
            "kind": "combine", "target": "", "code": prob["group_code"],
            "text": f"{label}" + (f" ({' + '.join(prob['programs'])})" if prob["programs"] else "") + f": {prob['reason']}",
        })
    return {"counts": counts, "lines": lines, "combined_groups": combined_groups,
            "combine_problems": combine_problems}


def _combine_one(exact_code, alloc_set_id, group_allocs, user):
    """
    Wrap `group_allocs` (one CourseAllocation per program, same exact course code) in
    the CombinedCourseGroup with that code, creating it if needed.
    Returns (group_or_None, reason_if_none).

    An allocation may belong to only ONE combined group (enforced by
    combined_group_signals, which raises). So if any of these rows already sits in a
    combined group other than the one for this code — e.g. one a COD built by hand on
    the COD panel — nothing is changed and the reason is reported instead of failing.
    """
    cg = (CombinedCourseGroup.objects
          .filter(group_code=exact_code, allocation_set_id=alloc_set_id)
          .order_by("id").first())
    elsewhere = set()
    for a in group_allocs:
        for g in a.combined_groups.all():
            if cg is None or g.pk != cg.pk:
                elsewhere.add(g.group_code)
    if elsewhere:
        return None, (
            f"already part of another combined group ({', '.join(sorted(elsewhere))}) — left unchanged. "
            f"Remove it from that group on the COD panel first if you want these combined here."
        )
    if cg is None:
        department = group_allocs[0].department
        cg = CombinedCourseGroup.objects.create(
            group_code=exact_code, allocation_set_id=alloc_set_id,
            base_course_code=group_allocs[0].program_course.course_code,
            department=department, origin_department=department, created_by=user,
        )
    cg.allocations.add(*group_allocs)
    if not cg.primary_allocation_id:
        cg.primary_allocation = group_allocs[0]
        cg.save(update_fields=["primary_allocation"])
    return cg, None


def _combine_cross_program(targets, program_courses, user):
    """
    After mapping, look for courses that landed on targets belonging to TWO
    OR MORE different programs (matched by course_code — each program has
    its own ProgramCourse row for a shared unit like "EDFO 111") and wrap
    those programs' own CourseAllocation rows together in a
    CombinedCourseGroup, since they are really one class taught to both
    cohorts at once. Never touches a course_code that only ever landed on
    targets of a single program.

    Returns (summary, problems): `summary` lists what got combined; `problems`
    lists the ones that could not be (each in its own savepoint, so one bad group
    never blocks the others or the mapping that already happened).
    """
    codes = {pc.course_code.strip().upper() for pc in program_courses}
    if not codes:
        return [], []

    rows_by_code_program = {}  # (code, allocation_set_id) -> {CourseAllocation, ...}
    for t in targets:
        for ca in t.courses.filter(program_course__course_code__in=[pc.course_code for pc in program_courses]).select_related("program"):
            key = (ca.course_code.strip().upper(), ca.allocation_set_id)
            rows_by_code_program.setdefault(key, set()).add(ca)

    summary, problems = [], []
    for (code_upper, alloc_set_id), allocs in rows_by_code_program.items():
        allocs = sorted(allocs, key=lambda a: a.pk)
        program_ids = {a.program_id for a in allocs}
        if len(program_ids) < 2:
            continue
        # One CombinedCourseGroup per distinct EXACT course_code (section
        # suffix and all) so "EDFO 111-D" and "EDFO 111-E" combine
        # separately if both happen to span the same two programs.
        by_exact_code = {}
        for a in allocs:
            by_exact_code.setdefault(a.course_code, []).append(a)
        for exact_code, group_allocs in by_exact_code.items():
            if len({a.program_id for a in group_allocs}) < 2:
                continue
            programs = sorted({a.program.name for a in group_allocs})
            try:
                with transaction.atomic():
                    cg, reason = _combine_one(exact_code, alloc_set_id, group_allocs, user)
            except Exception as exc:
                logger.exception("combine across programs failed for %s", exact_code)
                cg, reason = None, str(exc)
            if cg is None:
                problems.append({"group_code": exact_code, "programs": programs, "reason": reason})
            else:
                summary.append({"group_code": exact_code, "programs": programs})
    return summary, problems


def _map_one_course(pc, group, allow_duplicates, note, counts):
    """One ProgramCourse onto the targets of ONE allocation set (see map_courses_to_allocation)."""
    # 1. Course-master mapping is always ensured; a row that only exists because of this
    #    action is flagged like the backfill script's (blue "Mapped from allocation").
    for t in group:
        if not t.program_courses.filter(pk=pc.pk).exists():
            t.program_courses.add(pc)
            t.program_courses_from_allocation.add(pc)
            counts["pc_mapped"] += 1

    present, missing = [], []
    for t in group:
        rows = list(t.courses.filter(program_course_id=pc.pk).order_by("id"))
        (present if rows else missing).append((t, rows))

    # 2. Targets that do not have the course yet: attach what exists, else create it.
    if missing:
        _is_stem, dept, _pid, alloc_set = _target_facts(missing[0][0])
        base = CourseAllocation.objects.filter(
            program_course_id=pc.pk, department=dept, student_group__isnull=True)
        if alloc_set is not None:
            base = base.filter(allocation_set=alloc_set)
        base_rows = list(base.order_by("id"))
        created_row = None
        if not base_rows:
            first_t = missing[0][0]
            first_is_stem = _target_facts(first_t)[0]
            defaults = ({"specialization_stem": first_t} if first_is_stem
                        else {"is_elective": True, "selection_group": first_t})
            created_row, _c = CourseAllocation.get_or_create_shared(
                program_course=pc, department=dept, allocation_set=alloc_set, defaults=defaults)
            base_rows = [created_row]
        for t, _rows in missing:
            is_stem = _target_facts(t)[0]
            for ca in base_rows:
                _attach(ca, t, is_stem)
            if t.courses.filter(program_course_id=pc.pk).exists():
                if created_row is not None:
                    note("added", t, pc, f"{pc.course_code} was not in the course allocation — added to '{t.name}'.")
                else:
                    note("attached", t, pc, f"{pc.course_code} already had an allocation — attached to '{t.name}'.")
            else:
                note("error", t, pc, f"{pc.course_code} could not be attached to '{t.name}' (allocation set mismatch).")

    # 3. Targets that already have it: skip, or add one more section.
    if not present:
        return
    if not allow_duplicates:
        for t, rows in present:
            extra = f" ({len(rows)} copies)" if len(rows) > 1 else ""
            note("skipped", t, pc,
                 f"{pc.course_code} is already in the course allocation of '{t.name}'{extra} — skipped.")
        return

    by_source = {}
    for t, rows in present:
        source = min(rows, key=lambda r: (r.section_number or 1, r.id))
        by_source.setdefault(source.pk, (source, []))[1].append(t)
    for source, ts in by_source.values():
        clone, why = _new_section(source)
        if clone is None:
            for t in ts:
                note("error", t, pc, why)
            continue
        for t in ts:
            is_stem = _target_facts(t)[0]
            _attach(clone, t, is_stem)
            note("duplicate", t, pc,
                 f"Another copy of {pc.course_code} added to '{t.name}' as {clone.course_code} "
                 f"(section {clone.section_number}, 0 students — set its numbers on the allocation page).")
