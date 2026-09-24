# Sections + nested elective (pick-one) groups — change notes

Apply: copy these files over the project, then `python manage.py migrate course_allocation`.

## 1. Sections (a course split into parallel classes)
* `CourseAllocation.section_number` (migration 0020). Codes: `COSC 103-A` (sec 1), `COSC 103-A2`, `-A3`;
  shared/stem course: `COSC 103`, `COSC 103-2`. `course_allocation/section_utils.py` parses/builds them.
* /cod/: right-click a course row -> **Split into sections…** (sizes, keep-lecturer, merge back).
  New rows inherit student group(s), stems, elective pool(s), allocation set.
  Actions: `get_section_info`, `split_allocation_sections`, `unsplit_allocation_sections`.
* Enrollment updates (single + bulk) treat the number as the cohort TOTAL and distribute across sections.
* `dual_campus_scheduler` gained the same-base-course exemption the other schedulers already had.
* NOT covered: Auto-allocate (wipes/rebuilds allocations) does not replay sections.

## 2. Elective (pick-one) group nested in a stem / student group
* `SelectionGroup.specialization_stems` (M2M, migration 0020); `CourseAllocation.clean()` no longer forbids
  selection group + stem. Pool courses are also stem members (`sync_courses_into_mapped_stems`).
* /cod/: right-click a **stem header** -> Map Elective Group(s) (tick existing pools, or create a pick-one
  pool inside the stem). Student-group mapping already existed (`restricted_to_groups`).
* `course_allocation/exemption_helpers.py`: same-pool => exempt BEFORE the shared-stem rule; effective student
  groups include the groups a restricted pool is mapped to. Wired into: regular, stable, dual-campus, exam
  (now v70) and dual-campus-exam schedulers, and `is_scheduling_exempt` / `exam_panel_is_exempt`.
* Behaviour change: exam panel no longer exempts *every* elective from *everything* (it disagreed with the exam
  autoscheduler); only same-pool alternatives are exempt. Expect newly visible elective-vs-core clashes.
* Regular/stable/dual/panel now compare full mapped-group sets, not just the primary `student_group`.

## 3. Map ONE unit to several student groups (no Combined Course Group needed)
* /cod/ row right-click -> **Map to Student Group(s)…** (also works on every ticked row in "Select multiple…"
  mode, with Add / Replace). Uses `map_allocation_groups_and_stems` (now takes `ids`, `mode=add|replace`,
  `groups_only=1` so stems are never touched, and rejects groups from another program).
* Two units of one course (EDCI 111A / EDCI 111B) can be mapped to the SAME groups — the groups' students are split
  between them (A may hold only Group 1, B Group 1 + others). Same base course => schedulers/panels let them share a
  slot; each unit still clashes with the other courses of every group it holds.
* Student-group "set enrollment" no longer stamps one headcount on split / multi-group units (they're skipped and
  reported; set them per course).
