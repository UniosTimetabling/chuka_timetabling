# Patch Guide: `course_allocation/auto_allocate_courses.py`

Apply each change below in order. Wrap each "AFTER" block in a python code fence when editing; indentation shown matches the original file's nesting level.

```python
# ==============================================================================
# PATCH GUIDE for course_allocation/auto_allocate_courses.py
# Apply each numbered change below. Line numbers refer to the ORIGINAL file
# as of your current upload; re-check after each edit since numbers shift.
# ==============================================================================

# Add near the top, with the other local imports:
from .config_helpers import get_config, get_lecturer_max_load, make_group_code


# ------------------------------------------------------------------------------
# CHANGE 1 — Constraint 5: hardcoded semester validation (was line ~474)
# ------------------------------------------------------------------------------
# BEFORE:
#     try:
#         semester = int(semester)
#         if semester not in (1, 2):
#             raise ValueError
#     except ValueError:
#         return HttpResponseBadRequest("Semester must be 1 or 2.")
#
# AFTER:
    try:
        semester = int(semester)
        allowed = get_config(None)["allowed_semesters"]  # global calendar setting
        if semester not in allowed:
            raise ValueError
    except ValueError:
        return HttpResponseBadRequest(
            f"Semester must be one of {get_config(None)['allowed_semesters']}."
        )


# ------------------------------------------------------------------------------
# CHANGE 2 — Constraints 1 & 6: SPLIT_THRESHOLD / SPLIT_SIZE / all_letters
# (was lines ~594-599, inside _run_allocation, after target_dept is known)
# ------------------------------------------------------------------------------
# BEFORE:
#     SPLIT_THRESHOLD = 200
#     SPLIT_SIZE      = 100
#     ...
#     all_letters       = [chr(ord("A") + i) for i in range(26)]
#
# AFTER:
        cfg = get_config(target_dept)          # per-department, falls back to global
        SPLIT_THRESHOLD = cfg["split_threshold"] if cfg["splitting_enabled"] else float("inf")
        SPLIT_SIZE      = cfg["split_size"]
        # all_letters / A-Z cap removed entirely — see CHANGE 3 (make_group_code)


# ------------------------------------------------------------------------------
# CHANGE 3 — Constraint 6: group code generation + group count
# (was lines ~608-618, inside the "students > SPLIT_THRESHOLD" split path)
# ------------------------------------------------------------------------------
# BEFORE:
#     if students > SPLIT_THRESHOLD:
#         num_groups     = min((students + SPLIT_SIZE - 1) // SPLIT_SIZE, 26)
#         students_each  = students // num_groups
#         students_last  = students - students_each * (num_groups - 1)
#
#         for i in range(num_groups):
#             group_code     = f"{pc.course_code}-{all_letters[i]}"
#             ...
#
# AFTER:
            if students > SPLIT_THRESHOLD:
                num_groups     = (students + SPLIT_SIZE - 1) // SPLIT_SIZE   # no 26 cap
                students_each  = students // num_groups
                students_last  = students - students_each * (num_groups - 1)

                for i in range(num_groups):
                    group_code = make_group_code(pc.course_code, i)   # A..Z, then AA, AB, ...
                    # ... rest of the loop body is unchanged


# ------------------------------------------------------------------------------
# CHANGE 4 — Constraints 2 & 3: MAX load cap + PG designation strings
# (was line ~755, inside _pick_lecturer)
# ------------------------------------------------------------------------------
# BEFORE:
#     def _pick_lecturer(pc, mapping_idx, alloc_count, year_cov,
#                        target_dept, dept_lecs, other_lecs, all_lecs):
#         MAX = 6
#         def score(lec):
#             count   = alloc_count.get(lec.id, 0)
#             covered = (pc.program_id, pc.year) in year_cov.get(lec.id, set())
#             return count * 10 + (5 if covered else 0)
#         qualified = [l for l in mapping_idx.get(pc.id, []) if alloc_count.get(l.id, 0) < MAX]
#         ...
#         is_pg = is_postgraduate_course(pc.course_code)
#         ...
#             pg_dept  = [l for l in dept_avail  if l.designation in ("Dr", "Prof")]
#             pg_other = [l for l in other_avail if l.designation in ("Dr", "Prof")]
#
# AFTER:
def _pick_lecturer(pc, mapping_idx, alloc_count, year_cov,
                   target_dept, dept_lecs, other_lecs, all_lecs, cfg):
    """
    cfg: dict from config_helpers.get_config(target_dept) — computed ONCE
    per _run_allocation() call and passed in, so this stays a zero-query fn.
    """
    def cap_for(lec):
        return get_lecturer_max_load(lec, cfg)   # None == unlimited

    def under_cap(lec):
        cap = cap_for(lec)
        return cap is None or alloc_count.get(lec.id, 0) < cap

    def score(lec):
        count   = alloc_count.get(lec.id, 0)
        covered = (pc.program_id, pc.year) in year_cov.get(lec.id, set())
        return count * 10 + (5 if covered else 0)

    # ── Qualified via mapping (preferred) ────────────────────────────────────
    qualified = [l for l in mapping_idx.get(pc.id, []) if under_cap(l)]
    if qualified:
        return sorted(
            qualified,
            key=lambda l: (0 if l.department_id == target_dept.id else 1, score(l))
        )[0]

    # ── Fallback: use pre-loaded lecturer lists ───────────────────────────────
    is_pg       = is_postgraduate_course(pc.course_code)
    dept_avail  = [l for l in dept_lecs  if under_cap(l)]
    other_avail = [l for l in other_lecs if under_cap(l)]

    if is_pg:
        pg_titles = set(cfg["pg_designations"])   # configurable, was ("Dr","Prof")
        pg_dept   = [l for l in dept_avail  if l.designation in pg_titles]
        pg_other  = [l for l in other_avail if l.designation in pg_titles]
        pool = pg_dept or pg_other or dept_avail or other_avail
    else:
        pool = dept_avail or other_avail

    if not pool:
        pool = sorted(all_lecs, key=lambda l: alloc_count.get(l.id, 0))

    return min(pool, key=score) if pool else None


# ------------------------------------------------------------------------------
# CHANGE 5 — every _pick_lecturer(...) CALL SITE must now pass cfg
# (there are two call sites, ~line 617 and ~line 643, both inside _run_allocation,
# where `cfg = get_config(target_dept)` was already computed in CHANGE 2)
# ------------------------------------------------------------------------------
# BEFORE:
#     lecturer = _pick_lecturer(
#         pc, mapping_idx, alloc_count, year_cov,
#         target_dept, dept_lecs, other_lecs, all_lecs,
#     )
#
# AFTER (both call sites):
                    lecturer = _pick_lecturer(
                        pc, mapping_idx, alloc_count, year_cov,
                        target_dept, dept_lecs, other_lecs, all_lecs, cfg,
                    )


# ------------------------------------------------------------------------------
# CHANGE 6 — IMPORTANT: alloc_count must become PER-SEMESTER, not global,
# now that the cap is meant to be "N per semester". Currently (step 5 in
# _run_allocation) it's built from ALL of a lecturer's CourseAllocations
# across every department/semester. Scope it to the semester being allocated:
# ------------------------------------------------------------------------------
# BEFORE:
#     alloc_count = {
#         row["lecturer_id"]: row["cnt"]
#         for row in CourseAllocation.objects
#             .filter(lecturer__isnull=False)
#             .values("lecturer_id")
#             .annotate(cnt=Count("id"))
#     }
#
# AFTER:
        alloc_count = {
            row["lecturer_id"]: row["cnt"]
            for row in CourseAllocation.objects
                .filter(lecturer__isnull=False, program_course__semester=semester)
                .values("lecturer_id")
                .annotate(cnt=Count("id"))
        }
        # NOTE: this now correctly resets per semester, so a lecturer capped
        # at N in semester 1 is NOT blocked from also getting N in semester 2
        # -- which is what lets a lecturer reach max_load_per_semester*2 (e.g.
        # 6+6=12, or with a senior max_load_override of 7, 7+7=14) across the year.
```
