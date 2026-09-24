# Mapping curriculum courses to combination stems / elective groups

Page: `/groups-electives/` (Combination Stem and Elective tabs).

* **Map to program course only** (on every stem / elective row; formerly "Map courses") opens a dialog: pick a **program**
  (any department), optionally narrow by **year** and then **semester**. Program only lists
  every course of the program; courses already mapped to this stem/group are left out.
  Tick courses (or the header box) and press *Map selected*.
* The mapping is stored on `SpecializationStem.program_courses` /
  `SelectionGroup.program_courses` (migration `0021_program_course_mappings`) — it needs
  no allocation to exist.
* `course_allocation/course_mapping.py` compares allocations with the mappings:
  * every NEW `CourseAllocation` whose ProgramCourse is mapped is attached automatically
    (post_save signal; same allocation set only);
  * the **Apply** button (and the "also attach existing allocations" tick in the dialog)
    attaches allocations that already exist.
* Allocations bound to a Student Group are skipped by the elective route (electives stay shared by
  construction). A Student Group + Combination Stem together is now allowed — see "Course Groups"
  below, which is exactly how a stem gets its own lettered section of a shared course.
* Un-mapping (✕) only removes the mapping; existing allocations are left untouched.

Run `python manage.py migrate` after updating.

## Map to course allocation (adds the missing allocations)

*Map to program course only* above never creates an allocation. The other route puts courses
straight into the **course allocation** of one or many stems / elective groups:

* **Map multiple to course allocation** (list header, Stem and Elective tabs) — pick a program, tick
  the stems / groups to fill, tick the courses. **Map to course allocation (N)** does the same for
  the rows you ticked in the table. Both open the same dialog, in "allocation" mode.
* Per course and stem / group:
  * not in that target's allocation yet → the existing shared allocation is attached, or created
    if the course has none (in the target's own allocation set);
  * already in it → **skipped and reported** (the dialog lists every skip);
  * already in it and **"Allow adding the course again"** ticked → one more copy is added as a
    numbered **section**, exactly like the COD panel's "Split into sections": the original row becomes
    section 1, the new one is `COSC 103-2` (then `-3`, ...), 0 students, no lecturer. One new row per
    course, shared by all selected stems / groups that already had it, and attached to those only.
* The course is always also mapped at program-course level on every target it is processed for
  (including skipped ones). Mappings created this way are flagged like the backfill script's (blue
  "Mapped from allocation" row).
* A course from another program than the stem / group is reported and left alone.
* The dialog's course list keeps every course in this mode and shows which selected targets already
  hold it ("In course allocation of selected").

Code: `course_mapping.map_courses_to_allocation`, AJAX action `map_to_allocation` (and
`list_mappable_courses` with `mode=alloc`) in `groups_electives_ajax.py`.
Tests: `python manage.py test course_allocation.test_map_to_allocation`.
No migration needed.

### Combining a mapping across programs

The "Map multiple to course allocation" (and "Map multiple to program course only") dialog can
target more than one program at once — e.g. mapping "EDFO 111" into a stem in BOTH BEd Arts and
BEd Science, when the two are really one combined class. In the dialog:

* Pick the first **Program** as usual, then **+ Add another program** to reveal a second (or
  third) program picker.
* With 2+ programs selected, the course list shows every course **code found in ANY of them** —
  not just ones common to all. A code missing its `ProgramCourse` row in one of the selected
  programs (common when a combination stem was built across programs but only one side's
  curriculum was ever kept current) is still listed, tagged "+ curriculum: <program>", and each
  row carries one `ProgramCourse` id per program that already has it, plus a `new:<program_id>:
  <rep_pc_id>` placeholder for the ones that don't.
* Ticking such a row and mapping it creates the missing program's `ProgramCourse` on the spot —
  copying course_name/year/semester/unit_type/student_cohort off whichever program already has
  it — before mapping proceeds as normal. See `_resolve_or_create_program_courses` in
  `groups_electives_ajax.py`.
* The stem / elective-group picker lists targets from every selected program together, grouped by
  program name, so you can tick a stem in each.
* Ticking that course and a stem from each program still creates/attaches **each program's own**
  `CourseAllocation` row as normal (a curriculum course always belongs to one program) — but since
  those rows are really one shared class, they are also wrapped together in a
  `CombinedCourseGroup` afterwards, so scheduling treats them as one session needing a single
  lecturer/venue/time slot. A genuine mistake (a course with no sibling in the other target's own
  program) is still reported as an error exactly as before — only a real code shared across the
  selected programs is treated as an intentional combine.
* This combining is deliberately independent of, but consistent with, the Course Groups panel's
  own cross-program stem-letter pinning (see below) — either flow can produce a
  `CombinedCourseGroup`, and both use the same model.

Code: `course_mapping._combine_cross_program` (called at the end of `map_courses_to_allocation`),
multi-program support in `groups_electives_ajax._list_mappable_courses` (`program[]`), and the
`mpAddProgramBtn` / `mpExtraProgramsWrap` UI in `groups_electives.html`.
Tests: `CrossProgramCombineMappingTests` in `test_map_to_allocation.py`.

## Course Groups (bulk group count, scope, and combination-stem pinning)

Page: `/groups-electives/` (Student Group tab) → **Set up Course Groups (bulk)** button.

Lets a COD say, for one **program / year / semester / intake**, how many lettered Student
Groups are needed all at once, instead of adding them one letter at a time:

* **Number of groups** — leave at 1 if a course only ever needs one group (nothing is created).
  2 or more auto-letters the groups **A, B, C, …** (wrapping to `AA`, `AB`, … past 26).
* **Apply the split to** — *All compulsory courses* in that program/year, or *only the courses I
  pick* (electives are never swept in either way).
* **Combination Stems** — if the program/year already has any (`SpecializationStem`s eligible for
  that year/semester), the dialog lists them with one checkbox column per letter. Tick a letter
  under a stem to **pin** that lettered section to it: e.g. tick "A" under "English/Literature" and
  "B" under "History/CRE" — every course in scope then gets its own `EDFO 111-A` / `EPSC 111-A` /
  `EDCI 111-A` row (one shared `StudentGroup` per stem, not per course) for English/Literature, and
  its own `-B` set for History/CRE. Leaving a stem unticked just skips pinning it this run; its
  letters still exist as plain `StudentGroup` rows for later use.
* With **no** stems pinned (either the program/year has none, or you skip pinning), every letter is
  applied directly to every course in scope — the plain, non-combination split.
* Re-running the same plan **re-tags in place** rather than duplicating rows (same tag-in-place /
  clone / create-fresh cascade the older single-letter Student Group flow uses).
* **Continues lettering for shared/common units across programs.** A course code can be taught
  under several different programs (e.g. "EDFO 111" in both BEd Arts and BEd Science). The first
  time a program/year/semester/intake cell is set up, its starting letter is NOT always "A": the
  planner checks whether any course in scope is already split *anywhere else* in the system
  (a different program, year, or intake) and continues from there — so if BEd Arts already used
  A–E for EDFO 111, BEd Science's own split of the same unit starts at F, not A again. Raising
  `num_groups` later on an *existing* cell always continues that cell's own letters instead
  (re-scanning other programs every time would let the range drift). An unrelated course code
  used elsewhere has no effect on a brand-new cell's starting letter.
* **Combines a stem with another program's stem for the same shared unit.** Two different programs
  can each pin the *same* letter to one of their own Combination Stems on purpose — e.g. EDFO 111
  has 5 groups: A–C are BEd Arts-only, D is BEd Science-only, and E is an Arts stem COMBINED with a
  Science stem, taught together as one class. The dialog lists "combinable" stems from any sibling
  program that also teaches one of the courses in scope (`combinable_stems_for`); pinning the same
  letter to a stem from each program creates each program's own `CourseAllocation` row (its own
  `StudentGroup` — letters are namespaced per program, so there's no collision — just the same
  letter text) and then wraps both rows in a `CombinedCourseGroup` so scheduling treats them as one
  session needing a single lecturer/venue/time slot. This is always an explicit, deliberate pin by
  the COD — the automatic cross-program letter continuation above never triggers it by accident.
* Remembered in the same `GroupingTemplate` / `GroupingTemplateGroup` / `GroupingTemplateCourse`
  knowledge base the single-letter flow writes to (so auto-allocate can still replay the plain
  split), plus a new `GroupingTemplateStemAssignment` row per stem/letter pin (migration
  `0023_groupingtemplatestemassignment`). Auto-allocate does not yet replay the stem pins
  automatically — that's a follow-up; the direct "Apply" here fully creates the split immediately.
* `CourseAllocation.clean()` used to forbid a row from carrying both `student_group` and
  `specialization_stem`. That's now allowed for non-elective courses (only electives are still
  forbidden from having a `student_group`) — it's exactly what a stem-pinned lettered section is.

Code: `course_allocation/course_group_planner.py` (`get_plan_context`, `save_group_plan`), AJAX
actions `group_plan_context` / `save_group_plan` in `groups_electives_ajax.py`. Kept independent of
`course_management.cod_panel` (which owns the older one-letter-at-a-time flow) to avoid a circular
import between the two apps; both use the same `EDFO 111` → `EDFO 111-A` lettering convention from
`course_allocation/section_utils.py`.
Tests: `python manage.py test course_allocation.test_course_group_planner`.
Migration needed: `python manage.py migrate course_allocation`.
