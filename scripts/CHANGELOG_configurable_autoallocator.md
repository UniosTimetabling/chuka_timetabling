# Changelog — Configurable Autoallocator

Applied directly to this codebase (not just guidance):

## 1. `course_allocation/models.py`
- Added `AllocationConfig` model (global row via `department=None`, optional
  per-department override rows). Fields: `max_load_per_semester`,
  `max_load_enabled`, `split_threshold`, `split_size`, `splitting_enabled`,
  `pg_designations`, `allowed_semesters`.

## 2. `lecturer_portal/models.py`
- Added `Lecturer.max_load_override` (nullable) for seniority-based
  per-lecturer exceptions to the semester cap.

## 3. `course_allocation/config_helpers.py` (new file)
- `get_config(department)` — resolves department row -> global row ->
  hardcoded fallback.
- `get_lecturer_max_load(lecturer, config)` — per-lecturer override wins,
  `None` return means "unlimited" when `max_load_enabled=False`.
- `make_group_code(course_code, index)` — unlimited split-group codes
  (A..Z, then AA, AB, ... instead of hard-stopping at 26).

## 4. `course_allocation/auto_allocate_courses.py`
- Semester validation now reads `allowed_semesters` from config instead of
  a hardcoded `(1, 2)` tuple.
- `alloc_count` is now scoped `program_course__semester=semester` (was a
  global/lifetime count across all departments+semesters) — this is what
  makes "N per semester" actually mean per-semester.
- `SPLIT_THRESHOLD` / `SPLIT_SIZE` now read from `get_config(target_dept)`;
  `splitting_enabled=False` disables splitting entirely (threshold -> infinity).
- Split-group naming uses `make_group_code()` — no more 26-group ceiling.
- `_pick_lecturer()` now takes a `cfg` param; the hardcoded `MAX = 6` is gone,
  replaced by `get_lecturer_max_load()` (per-lecturer override, else config).
- PG-course lecturer filtering now checks `cfg["pg_designations"]` instead of
  the hardcoded `("Dr", "Prof")` tuple.

## 5. `course_allocation/management/commands/seed_allocation_config.py` (new)
Run once per environment:
```
python manage.py seed_allocation_config
```
Creates the GLOBAL `AllocationConfig` row and applies example seniority
overrides (Prof=8, Dr=7, Mr/Ms/Mrs=4 per semester). Use
`--skip-overrides` to only seed the config row.

## Still required in your environment (not run here — no live DB access)
```
python manage.py makemigrations course_allocation lecturer_portal
python manage.py migrate
python manage.py seed_allocation_config
```

## Also included in `scripts/` (diagnostic/one-off, not wired into urls.py)
- `inspect_lecturer_course_data.py` — read-only DB diagnostic (run via
  `python manage.py shell < scripts/inspect_lecturer_course_data.py`).
- `fix_lecturer_course_mapping.py` — populates `LecturerCourseMapping`
  toward 5-7/semester, 10-14/year, dry-run by default.
- `rebalance_lecturer_course_load.py` — earlier direct `CourseAllocation`
  rebalancer (superseded by `fix_lecturer_course_mapping.py` — kept for
  reference only, since the autoallocator rebuilds `CourseAllocation` from
  `LecturerCourseMapping` on every run).
- `auto_allocate_courses_patch_guide.md` — the diff reasoning behind the
  changes above, kept for review/audit purposes.
