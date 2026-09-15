# Concurrent Allocation Sets — full delivery so far (all files touched)

## ⚠️ Read this part first — the most important fix in the whole delivery

While reviewing `admins/smart_importer_v2.py` for the import-side gap, I
found the actual pre-existing auto-allocator, `course_allocation/auto_allocate_courses.py`
(`_run_allocation`, called by the live `auto_allocate_all_ajax` endpoint).

**Its old behaviour, in its own original comments: every time the COD ran
the auto-allocator for ANY ONE semester, it archived then DELETED every
`CourseAllocation` row for the ENTIRE department — every other semester's
live data included — before rebuilding only the requested semester.**
Running Semester 1's allocator wiped Semester 2's live allocations (they
were recoverable from `ArchivedCourseAllocation`, but gone from the live
table you'd actually be working in). This is likely close to the exact
"crisis" scenario you opened this whole request with, and it was already
in the codebase before this upgrade — the concurrent-AllocationSet work
just made it far more dangerous, since a COD now legitimately has more
than one semester's data open live at once.

**Fixed:** `_run_allocation` and `_archive_semester` now take an optional
`allocation_set`. When given (the normal case now — `auto_allocate_all_ajax`
resolves the COD's active/legacy set before calling it, and the new
wizard's "auto_full" finish step calls it directly per semester layer),
the archive+delete+rebuild is scoped to that ONE set's ONE semester only —
every other set and every other semester layer within the same set is left
completely untouched. Verified this doesn't reintroduce the original bug
in miniature: I caught and fixed a follow-on case where a single
AllocationSet composed of two ALL-scope semesters (e.g. Sem 1 + Sem 2 both
full auto-allocate) would have had its second `_run_allocation` call wipe
the first call's just-created rows — the delete/archive filters now also
match on `program_course__semester`, not just `allocation_set`, so each
semester layer only ever touches its own rows.

With no `allocation_set` passed (a caller that hasn't been updated), the
function preserves its exact old whole-department behaviour — nothing
currently depending on it breaks.

**Also fixed while I was in this area:** `admins/smart_importer_v2.py`'s
AI bulk-import commit (`_commit_records`) used
`CourseAllocation.objects.update_or_create(..., defaults={...})` — since
`allocation_set` wasn't in `defaults`, this was actually already safe
against re-tagging on update, but it also meant every newly-created row
from an AI import was left with `allocation_set=NULL` forever. Now a
brand-new row gets tagged with the active/legacy set once, right after
creation, and re-running an import still never reshuffles an existing
row's set (the tag is applied only `if was_created`).

## ⚠️ Update this round: a second "wipe everything" gap found and fixed

After the `_run_allocation` fix above, I checked every other auto-allocator
(`odel_system/auto_allocate.py`, `campuses_timetable/auto_allocate.py`,
`resits_timetabling/auto_allocate.py`, `course_allocation/lab_auto_allocate.py`)
for the same pattern. **Good news: none of them have it** — all four already
scope their `.delete()` narrowly to `program_course_id__in=[the specific
courses being processed]`, not the whole department/campus/scope. The bug
was isolated to the one place already fixed.

However, I then found a second, related gap: **`course_allocation/cod_panel_clear.py`**
— the COD-facing "archive & delete" page — was scoped to the whole
department too (by explicit design, not by accident: it's a deliberate
"clear all my allocations" admin tool, not a silent side-effect). But with
concurrent AllocationSets, a COD using it to clear a Semester 2 draft
would have wiped Semester 1's submitted/approved data right along with it.
Fixed: the active-allocations list, the archive-and-delete action, and
restored rows are now all scoped to the COD's currently active/legacy
AllocationSet — clearing a different set means switching to it first via
the same switcher used everywhere else. Also fixed the restore path's
duplicate-course-code check, which would have wrongly blocked restoring a
course into a new set just because the same code already existed in a
*different* concurrent set.

---

Apply against your actual repo at the matching paths. Every file here is
either brand new or a modified copy of your existing file — diff before
overwriting, same as Phase 1.

## Run order on staging
```
python manage.py migrate
python manage.py backfill_legacy_allocation_sets            # dry run — read the output
python manage.py backfill_legacy_allocation_sets --apply     # writes: one Legacy set/department
```
Nothing else needs a data migration. Everything below reads/writes through
the new `allocation_set` FK, which defaults to the Legacy set for any
department that hasn't started using multiple concurrent sets yet.

## What's in this zip, by area

**Schema** (`course_allocation/models.py`, `migrations/0014_allocation_set.py`,
`management/commands/backfill_legacy_allocation_sets.py`)
`AllocationSet` / `AllocationSetSemesterComponent` / `AllocationSetComponentCourse`
+ a nullable `allocation_set` FK on `CourseAllocation`. Additive only —
see Phase 1 notes below for the zero-data-loss reasoning.

**Core scope helper** (`course_allocation/allocation_scope.py`)
Active-set resolution/switching (session-based), populating a set from its
semester composition, adding hand-picked courses (grouped by each course's
actual semester so a mixed tick-list can't land in the wrong layer).

**COD panel flow** (`course_management/allocation_set_panel.py` + 4 templates
+ `urls.py`)
Picker → step 1 (type: auto_full/selective, special flag) → step 2
(repeatable "add a semester, all-scope or selected-scope" builder) →
course-picker for selective sets → switch → per-set submit
(`submit_allocation_set`, never touches any other set).

**`cod_panel.py`**
Resolves the active set at the top of the view (shared by both the AJAX
action dispatcher and the GET render). Redirects to the picker once per
session if nothing's been chosen yet. Scopes `create_allocation` (new rows
get `allocation_set=active_allocation_set`), `list_allocations`, and
`get_allocation_summary` to the active set. **Not yet done:** roughly 46
other `CourseAllocation.objects` call sites in this 6,000-line file
(selection groups, combined groups, student groups, lecturer-mapping
tools, etc.) are still department-scoped only — they'll show/affect data
across every AllocationSet in the department, same as before this
upgrade, until each is reviewed individually.

**DVC panel** (`faculty_management/dvc_panel.py` + `templates/faculty/dvc_panel.html`)
Main review queue now also requires the row's own AllocationSet to be
`submitted_to_dvc` (or have no set / be a legacy set, for full backward
compatibility). Added an "Allocation (awaiting DVC)" dropdown next to the
existing Faculty/Department/Lecturer filters, wired the same way (GET
param `allocation_set_id`, page reload).

**Scheduling algorithms** (`timetable/algorithms/…`)
Patched the single "what exists to be scheduled" query in:
  - `regular_timetable_autosheduler_algorithm.py` (the main live scheduler)
  - `stable_sheduling_algorithm.py` (2 load sites)
  - `exam_timetable_autosheduler_algorith.py`
All three now only pull allocations whose AllocationSet is
`submitted_to_tt` (or has no set / is legacy). **Not yet done:**
`campus_autosheduler_algorithm.py`, `dual_campus_*`, `odel_autosheduler_algorithm.py`,
`resit_autosheduler_algorithm.py`, `lab_allocation_autosheduler.py` — these
run on separate models (`CampusCourseAllocation`, `ODELCourseAllocation`,
`ResitCourseAllocation`) that this phase's `AllocationSet` design doesn't
cover; they need their own equivalent concept if you want the same
concurrent-allocation behaviour there.

**PDF / CSV / listing exports** (`export_import/…`)
  - `publish_timetables_pdfs.py`: `get_academic_year_and_semester()` and
    `publish_regular_timetable_pdf` take an optional `?allocation_set_id=`
    and label the header from that set directly instead of guessing from
    a whole-database majority vote.
  - `export_course_allocations_csv.py`: `?allocation_set_id=` filter, plus
    an "Allocation Set" column.
  - `view_and_export_all_course_allocations.py`: `?allocation_set=` filter
    added to the existing filter chain.
  - `official_timetables.py` (`export_main_pdf`): `?allocation_set_id=`
    filter on the timetable queryset.
  - **Not yet done:** `publish_exam_timetable_pdf` (same file as
    `publish_regular_timetable_pdf`, not yet given the same treatment),
    `global_combined_timetable_pdf.py`, `program_year_pdf_cache.py`, and
    the import side of `export_import`.

**Mobile API** — not directly modified. It reads already-published
`Timetable` rows, which now only ever come from `submitted_to_tt` sets
(enforced upstream by the scheduler fix above), so it inherits correct
behaviour without a direct edit — but it hasn't been reviewed line by line,
so treat that as "probably fine," not "verified."

**Manage Program page** (`program_management/programs_page.py`, `urls.py`,
`models.py`, `templates/program/programs.html`)
Root cause fixed: it was loading every department's programs + courses +
codes in one request. Now a non-scoped user must pick a department
(`?department_id=`), and that department's Programs/ProgramCodes are
paginated (`?page=`) with Django's Paginator — the template's existing
`{% for program in programs %}` loops needed no rewrite since a Page
object iterates the same way a queryset does. `department-scoped
COD/COD-admin` users are unaffected (they were never the slow path).
Also: `ProgramCourse.SEMESTER_CHOICES` widened to include Semester 3.

## Update (this round): every CourseAllocation *creation* site in cod_panel.py

Last round only `create_allocation` (1 of 6 places that make new
`CourseAllocation` rows) was allocation-set-aware. All 6 are now:
  - Special Intake pull (`SpecialIntakeGroupService.pull_courses`) → tags
    new rows with the active/legacy set.
  - Normal "pull whole semester" (`pull_courses_normal`) → same — this is
    what the create-allocation wizard's `auto_full` flow relies on.
  - Student-group clone (`_assign_course_to_group`, clone branch) →
    inherits the sibling row's own `allocation_set` (correct regardless of
    who's calling — no session lookup needed).
  - Student-group fresh-create (same function, no-template branch) →
    active/legacy set, passed in by both call sites
    (`StudentGroupService.create_group`, `.assign_courses_to_group`).
  - Combined-group "Add Group(s)" clone → inherits the source row's set.

## Update (this round): PDF / mobile review pass

  - `publish_exam_timetable_pdf` now takes the same `?allocation_set_id=`
    as the regular timetable publish (header label + queryset filter).
  - `global_combined_timetable_pdf.py` and `program_year_pdf_cache.py`
    reviewed and deliberately left unscoped — both are genuinely
    university-/year-wide by design (a master report and a per-year cache
    respectively), and already inherit correct data transitively through
    the scheduler's `submitted_to_tt` gate on `Timetable` rows. Scoping
    them to one set would break what they're for, not fix anything.
  - `mobile_api/course_search.py`: found and fixed a real leak — course
    search wasn't excluding allocations whose set is still plain `draft`
    (not submitted anywhere yet), so students/lecturers could see
    not-yet-final plans. Fixed as an explicit `Q(isnull=True) | ~Q(status=draft)`
    filter — deliberately NOT `.exclude()`, because `.exclude()` across a
    nullable FK relation would incorrectly drop legacy/pre-upgrade rows
    too under SQL's three-valued NULL logic.

## Still open (unchanged in shape from before)
1. ~40 remaining *read/list* `cod_panel.py` query sites (selection groups,
   combined-group listings, lecturer-mapping tools) — lower risk than the
   creation sites since they don't write data, but will show/act on
   cross-set data until reviewed. Deliberately did NOT mechanically patch
   these: several are duplicate-course-code checks and cross-department
   matching utilities that are CORRECT to leave department-wide (scoping
   a duplicate check to one set would let the same course code exist
   twice across sets) — each remaining site needs individual judgment,
   not a global find/replace.
2. Campus/ODEL/resit/lab/dual-campus algorithms — separate models
   (`CampusCourseAllocation`, `ODELCourseAllocation`, `ResitCourseAllocation`)
   with no `AllocationSet` equivalent. Real design decision, not guessed at.
3. `export_import`'s generic importer (`smart_import_export.py`) does NOT
   actually support `CourseAllocation` as an importable model at all
   (checked `MODEL_MAP` — only curriculum/reference data), so there was no
   gap there after all. The real import gap was in `admins/smart_importer_v2.py`,
   fixed above.


## Merge note (this update)
A separate sync-engine fix (`chuka_timetabling_synced_fixed.zip`) was merged
in on top of this work. It touched `core/models.py` + a new migration
(`core/migrations/0011_syncmodellog_partial_status.py`), and
`export_import/{dependency_utils.py,sync_engine.py,sync_views.py,urls.py}`
— a chunked cross-node sync improvement (request-size capping, out-of-scope
FK nulling, a new `/sync/verify-pks/` endpoint, a "partial" status). None of
those files overlap with anything touched by the Allocation Sets work, so
this was a clean merge with no conflicts: every file the sync fix changed
now has that fix, and every file this upgrade changed still has this
upgrade. Confirmed by diffing the merged tree against the sync-fix upload —
the only remaining differences are exactly this upgrade's own files.

## Merge note (this update, round 2)
A second external update (`university_timetabling.zip`) was merged in —
a separate branch built from the same post-sync-fix baseline, containing
the team's independent work: the exam/timetable analysis-scope feature,
a family-split scheduling bug fix (a ~1,200-line cleanup of
`timetable/algorithms/exam_timetable_autosheduler_algorith.py`), a new
course-allocation Excel template import/export tool, a student
"my timetable" dashboard, and several infra/migration additions.

This was a genuine three-way merge (done with `git merge-file` against
the shared base), not a copy-over. Verified file-by-file against the
common ancestor: real two-sided overlap existed in only three files
(`course_management/urls.py`, `course_management/cod_panel.py`,
`timetable/algorithms/exam_timetable_autosheduler_algorith.py`) — every
other file that looked like a conflict turned out to be untouched by the
team, differing only because of this upgrade's own earlier changes.
Confirmed line-by-line that every merged file is a strict superset of
both sides (nothing subtracted from either), and the full project
(583 Python files) still parses, same 2 pre-existing unrelated broken
`tests.py` files as before.

## Merge note (round 3)
A third external snapshot was merged in — a continuation of the round-2
branch (same lineage: it already had the sync fix, the exam-scheduler
rewrite, and even the updated maintenance page). This round's actual
delta was small: a `cod_panel.py` addition (student_group name surfaced
in a listing response, merged cleanly with the AllocationSet code) and
direct-copy updates to `cod_panel.html`, several dashboard templates
(`my_timetable`, `staff_portal`, `student_portal`, `unios_homepage`,
`view_timetable`), and `static/css/website.css` — none of which this
upgrade had touched. Verified via the same method as prior rounds: only
files with legitimate two-sided changes needed merging, everything else
was a clean copy, and the full project (585 .py files) still parses.

## Follow-up: algo_backup/super_ago/superalgo.py — actually reviewed this time
Last round I copied `timetable/algorithms/algo_backup/super_ago/` over
blindly as a "backup artifact" without opening the files inside. That was
wrong to do without saying so — `superalgo.py` (6,819 lines) is a
"FINAL VERSION" rewrite of the regular scheduler (new Oracle/Healer
architecture for concurrent-aware slot calculation). It is **not wired
into any live view or URL** (confirmed: nothing outside `algo_backup/`
imports it) — it's a staged, not-yet-activated candidate replacement for
`timetable/algorithms/regular_timetable_autosheduler_algorithm.py`.

It had NOT been given the AllocationSet input-side filter, so I patched
it the same way as its live counterpart, in case/when it's activated.

## Related but distinct finding: every scheduler wipes its own output table first
While checking this, I confirmed something systemic: **every single
scheduler in the project** — regular, exam, stable, dual-campus, campus,
ODEL, resit, lab, and this inert superalgo copy — unconditionally does
`SomeTempTimetable.objects.all().delete()` before rebuilding, with no
department/set scoping. This is NOT something I introduced or missed
fixing; it's consistent, pre-existing architecture across the entire
codebase (every "run auto-scheduler" button is a full regenerate, not an
incremental update).

This is a different category from the `CourseAllocation`-wipe bug fixed
earlier: `TempTimetable`/`ExamTempTimetable`/etc. are draft/derived
*output* ("Temp" is in the name) meant to be rebuilt from the current
`CourseAllocation` source data — not source-of-truth records like
`CourseAllocation` itself. As long as the input-side filter (already
fixed, university-wide, correctly includes every currently-submitted
AllocationSet) is right, a full wipe-and-rebuild of the derived output is
self-consistent, not a data-loss bug.

One real, pre-existing characteristic worth knowing regardless: there's
no "manually placed, don't touch" flag on these Temp models, so a manual
adjustment made on a manual timetable panel will NOT survive the next
full auto-scheduler run for that timetable type, for anyone, in any
department. That's not new and not part of this upgrade — just worth
being aware of operationally.

## Fix: pre-existing data now migrates in as "Semester 1"
`backfill_legacy_allocation_sets` previously *inferred* the legacy set's
semester composition from whatever `ProgramCourse.semester` values were
already on the pre-existing data — which could come out mixed, empty, or
otherwise not what you'd want a COD to see on the allocation picker.

Changed: the legacy set is now always labeled Semester 1 explicitly (one
`scope=ALL` component, `semester_number=1`) — a deliberate labeling
decision on the *container*, not a change to any actual course's own
curriculum semester (that field on `ProgramCourse` is real data and is
never touched). This is what makes it show up on the COD's allocation
picker as "Semester 1" and be selectable as such.

**Safe to run even if you already applied the old version of this
command on staging**: re-running with `--apply` detects a legacy set
whose scope=ALL composition isn't already exactly Semester 1 and
corrects it — without touching any CourseAllocation row (the fix is only
to the AllocationSet's own composition label) and without touching any
scope=SELECTED component (those only ever exist because a COD
deliberately added one via the wizard — this command never creates or
touches those).

    python manage.py backfill_legacy_allocation_sets            # dry run — check what would change
    python manage.py backfill_legacy_allocation_sets --apply    # apply

## Fix: groups/categories/combined-groups now switch with the allocation, per your spec
You reported that Combined Course Groups, Selection Groups, and
Specialization Stems didn't switch when the COD switches allocation sets
— a Semester 1 session would still show Semester 2's groups. Investigated
and found real bugs, not just a missing filter:

- `SpecializationCategory` had `unique_together = ("program", "name")` —
  a DB-level constraint that permanently blocked reusing a category name
  for the same program in a different semester, ever.
- `CombinedCourseGroup.group_code` was **globally unique** across the
  entire system — combine "COSC 471" once, and no department could ever
  reuse that code again, in any future semester.
- The shared `CourseAllocation.get_or_create_shared()` helper (used by
  both Selection Groups and Specialization Stems to find/create their
  underlying course row) had no allocation-set awareness at all, so it
  could silently match a Semester 1 row while a COD was working in
  Semester 2.

**Fixed, per your exact spec:**
- Added `allocation_set` to `SelectionGroup`, `SpecializationCategory`,
  and `CombinedCourseGroup`. Their listings, creation flows, candidate
  searches, and name/code uniqueness checks are now all scoped to the
  COD's active allocation — switching allocations now genuinely switches
  what's visible, the same way it already did for the main course table.
- Uniqueness constraints changed from global/per-program to
  per-allocation-set, so names and codes CAN be reused across different
  sets (`SpecializationCategory`: `(program, name, allocation_set)`;
  `CombinedCourseGroup`: `(group_code, allocation_set)`).
- **`StudentGroup` deliberately NOT given an allocation_set field** — per
  your instruction, the group itself (e.g. "Group A" for a program/year/
  semester) stays shared and reusable across sets. Only which *courses*
  sit inside it is scoped: `StudentGroupService.list_groups`,
  `get_group_detail`, and the shared `_groupable_courses_qs()` helper
  (used by group creation, course assignment, and the available-courses
  picker) now all filter to the active allocation set, so a COD in
  Semester 1 sees "their" Group A with only Semester 1's courses in it.
- `backfill_legacy_allocation_sets` extended to also attach pre-existing
  `SelectionGroup` / `SpecializationCategory` / `CombinedCourseGroup` rows
  to each department's legacy set (never touches `StudentGroup`, by
  design). Verified this can't collide with the new, stricter uniqueness
  constraints: both OLD constraints (global group_code, per-program
  category name) were already at least as strict, so there's no way two
  orphaned rows can end up violating the new per-set constraint once
  they're all attached to the same legacy set.
- New migration: `0015_group_allocation_sets.py` (additive: 3 new
  nullable FKs + 2 tightened-but-non-destructive uniqueness constraints).
