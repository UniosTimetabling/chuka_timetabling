# Allocation & Timetable PDF fixes — what changed and why

This covers `/allocations/timetable-dashboard/`, `/allocations/DVC`,
`/allocations/dean/`, `/allocations/cod/`, and the official teaching /
exam / resit / campus timetable PDFs. All the allocation-download pages
render the **same** PDF built by `allocation_reports/pdf_builder.py`, so
fixes there fix every allocation page at once.

## 1. Black-and-white only — no red, no color accents

Every PDF generator in the project had drifted into using different,
non-official reds (`colors.red` / `#FF0000`, `#CC0000`, `#7a1f1f`) for
titles and table headers. The **official Chuka University template is
black-and-white only** — confirmed by the existing code comments already
in `campuses_timetable/pdf_views.py` and `odel_system/pdf_management_views.py`
("Header row: white text on black background", "plain black/white matching
official template"), which two other generators were already trying (and,
in `#404040` / `#CCCCCC`, half-failing) to follow.

Every PDF-generating file now uses:
- **Titles / headings:** black, bold
- **Table header bars:** solid black background, white bold text
- **Body text:** black on white

Files touched: `allocation_reports/pdf_builder.py`,
`export_import/official_timetables.py`,
`export_import/official_exam_timetable.py`,
`export_import/publish_timetables_pdfs.py`,
`export_import/global_combined_timetable_pdf.py`,
`resits_timetabling/resit_timetable_pdf.py`,
`campuses_timetable/pdf_views.py`, `odel_system/pdf_management_views.py`,
plus the on-screen CSS accents in
`allocation_reports/templates/allocation_reports/dashboard.html` and
`cod_panel.html`.

(Left untouched: the blue/purple Evening/Weekend session-type shading in
the full class timetable grids, e.g. `#1565c0`/`#6a1b9a` in
`official_timetables.py` and `publish_timetables_pdfs.py`. That's
functional color-coding to tell evening/weekend sessions apart at a
glance in a dense grid, not a branding color — flag it if you'd like that
removed too.)

## 2. One typeface throughout — Times New Roman

Every PDF generator was mixing `Helvetica` / `Helvetica-Bold` /
`Helvetica-Oblique`. All of it is now `Times-Roman` / `Times-Bold` /
`Times-Italic` / `Times-BoldItalic` — reportlab's built-in Times New Roman
equivalents, so no extra font files or registration needed. Same list of
files as above.

## 3. Logo on top, header properly aligned

`allocation_reports/pdf_builder.py`'s header block is rebuilt so the
university logo sits centred on its own row at the very top, with the
university name directly beneath it, then motto, then directorate name,
then a left/right contact-info row, then the bold Ref/Date rule — all
centred on the same margins.

## 4. Allocations no longer merge different semesters/intakes into one table

Grouping is now **Program → Year → Semester → Intake**, each combination
in its own labeled table, e.g.:

```
BSc Computer Science
  Year 2 — Semester 1 — Normal Intake
  Year 2 — Semester 2 — Special Intake
```

## 5. Course groups (Group A / B / C) now split cleanly

The COD panel already splits some program cohorts into parallel teaching
groups (`StudentGroup` — e.g. "BSc Nursing — Year 1 — Group A / Group B /
Group C"). The allocation PDF was ignoring that split entirely and
listing every group's courses in one flat table. It now adds one more
level under Program → Year → Semester → Intake:

```
BSc Nursing
  Year 1 — Semester 1 — Normal Intake
    Group A
      [table of Group A's course allocations]
    Group B
      [table of Group B's course allocations]
    Shared across all groups
      [electives / stem courses that apply to every group]
```

A cohort that was never split into groups (no `StudentGroup` records)
renders exactly as before — one table, no extra "Group" heading.

## 6. Merged course groups now show what they were merged with

The Lecturer column previously showed only the lecturer's name for a
group's primary allocation, with the combined student count folded in
silently. It now prints a small second line:

```
Dr. J. Mwangi
(merged with CS204-B, CS204-C)
```

## 7. Type column: Elective vs Core

A **Type** column (Course Code | Course Name | **Type** | Origin
Department | Lecturer | No. of Students) shows `Elective` or `Core` for
every row — independent of stem/selection-group membership, which now has
its own heading (see item 9 below).

## 8. Cache invalidation kept in sync

`allocation_reports/signature.py`'s content hash — which decides whether a
department's PDF needs regenerating — now includes semester, intake,
elective flag, stem, stem category, selection group, merged-group
membership, and student group. Without this, a change to any of those
fields wouldn't trigger a regeneration and the dashboard would keep
serving a stale PDF.

## 9. Specialization stems and elective options now show as full combinations

Previously a stem/elective course only got a small `Stem: AI` tag in the
Type column, buried in the same flat table as everything else — no way to
see which courses actually belong together as one stem, or which courses
are the alternatives in a pick-one elective group.

Each Program/Year/Semester/Intake/(Group) block now separates out:

```
BSc Computer Science
  Year 3 — Semester 1 — Normal Intake
    [core/mandatory courses table]

    Specialization — Year 3 Semester 1 Specialization: Artificial Intelligence Stem
    Students who choose this stem take every course listed below together.
      [CS310 Machine Learning, CS311 Neural Networks — both under this stem]

    Specialization — Year 3 Semester 1 Specialization: Cybersecurity Stem
    Students who choose this stem take every course listed below together.
      [CS320 Network Security]

    Elective Options — Year 3 Sem 1 Electives - Group A
    Students choose exactly one course from the list below.
      [CS330 Mobile Development, CS331 Cloud Computing]
```

- **Stems** (`SpecializationStem`, grouped under their
  `SpecializationCategory`) — every course in the same stem is bundled
  into one table under one heading, since a student who picks that stem
  takes all of them together.
- **Elective selection groups** (`SelectionGroup`) — the "choose exactly
  one of these" alternatives are bundled the same way, clearly separated
  from stems since the semantics differ (pick-one vs. take-all).
- Plain core/mandatory courses (and any elective not part of a named
  selection group) still render in one ordinary table, unlabeled, exactly
  as before.

This sits *underneath* the Group A/B/C split from item 5 — a stem or
elective group that only applies within one teaching group renders under
that group's heading; one shared across all groups renders under "Shared
across all groups".

Implementation: `allocation_reports/adapters.py`'s `_main_row()` now
also reads `specialization_stem.category.name` and `selection_group.name`;
`pdf_builder.py`'s new `_render_group_rows()` splits a block's rows into
core / per-stem / per-selection-group buckets before rendering each as
its own table.

## Verified

Ran the `pdf_builder.py` grouping/splitting/rendering logic standalone
against sample data and rendered actual PDF bytes to confirm layout at
every level: black header bars, Times New Roman throughout, logo-on-top
header block, Group A/B/shared split, and stem/elective combination
tables each correctly labeled with their explanatory note and course
list.

## Files changed
```
allocation_reports/adapters.py
allocation_reports/pdf_builder.py
allocation_reports/signature.py
allocation_reports/templates/allocation_reports/dashboard.html
allocation_reports/templates/allocation_reports/cod_panel.html
export_import/official_timetables.py
export_import/official_exam_timetable.py
export_import/publish_timetables_pdfs.py
export_import/global_combined_timetable_pdf.py
resits_timetabling/resit_timetable_pdf.py
campuses_timetable/pdf_views.py
odel_system/pdf_management_views.py
```

## Not covered — worth flagging
- odel/campus/resit allocation scopes still don't have `is_elective` /
  `specialization_stem` / `selection_group` / `student_group` fields on
  their models, so those scopes always show "Core" and never split into
  groups or combinations. Only `main` (CourseAllocation) has that data
  today — let me know if those scopes need the same fields added.
- Blue/purple Evening/Weekend session shading in the full timetable grids
  was left as-is (see note above) — say the word if you want that gone
  too for a pure black-and-white document.
- All edits were made and compiled (`python3 -m py_compile`) against the
  uploaded copy, and the PDF layout/grouping/combination logic was
  rendered and visually verified against sample data — but nothing was
  run against your live database, so check on a staging deploy before
  pushing to production.
