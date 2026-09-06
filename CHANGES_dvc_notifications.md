# DVC Submission / Approval Notification System — Implementation Notes

This update closes the gap where the DVC panel gave no visibility into
*when* a COD submitted an allocation, and CODs/lecturers received no report
when the DVC approved or rejected their courses.

## What was added

1. **`course_allocation.models.DVCActionLog`** (new model)
   Event-level audit log: one row per COD submission / DVC approve / DVC
   reject, with timestamp, actor, department, lecturer, course code, and an
   `alloc_type` (standard / campus / odel). Registered in
   `course_allocation/admin.py` (read-only, for auditing).

   Run this after pulling the change (no migrations are shipped in this repo
   by convention — see `reset-migrations.sh`):
   ```bash
   python manage.py makemigrations course_allocation
   python manage.py migrate
   ```

2. **`course_allocation/toggle_submission_to_dvc.py`** (COD "submit" action)
   When a COD locks their allocation for DVC review (`allow_submission_to_dvc`
   True → False):
   - Logs a `DVCActionLog(action="submit")` row.
   - Notifies the `dvc` / `dvc_admins` groups: *"📥 <Dept> (<COD name>)
     submitted their course allocation for review at <time>."*
   - Confirms to the COD themself: *"✅ Your course allocation submission
     for <Dept> was received at <time>..."*

3. **`faculty_management/dvc_panel.py`** (DVC approve/reject actions)
   Every approve/reject branch (single course, bulk-all, per-department,
   per-faculty, per-lecturer — across standard, campus, and ODEL allocations,
   including the "restore disapproved" panel) now calls a new
   `_log_dvc_action(...)` helper that writes a `DVCActionLog` row per
   allocation touched. These are **not** notified immediately — see below.

4. **`notifications/tasks.py`** (new — the batching / idle-timeout logic)
   A DVC clicking through 30 courses one at a time should not generate 30
   notifications. Instead:
   - `flush_idle_dvc_batches` (Celery beat task, runs every 30s) looks at
     each department with unreported `DVCActionLog` rows and checks the
     timestamp of the **latest** one.
   - If that latest action is **more than 3 minutes old** (`IDLE_MINUTES` in
     the file), the DVC is treated as "finished" for that department right
     now. All unreported rows since the last report are compiled into one
     message (or a PDF via reportlab if there are more than 8 items, reusing
     the existing PDF helpers in `notifications/notification_apis.py`), sent
     to:
     - the department's COD (`dept.leader`) — approved/rejected counts +
       breakdown, and
     - every lecturer whose course was touched, on their own dashboard.
   - If the DVC is still actively clicking (latest action newer than 3
     minutes), the batch is left alone and re-checked on the next run — so
     an in-progress review is never cut off early.
   - `flush_department_now(department_id)` is exposed separately so you can
     wire an explicit **"Finish Review" button** in the DVC panel for a
     guaranteed, non-heuristic send instead of (or in addition to) the
     timeout. This is recommended if false-early-sends during a DVC's coffee
     break are a concern.

5. **`university_timetable_system/celery.py`**
   Registered `flush-idle-dvc-batches` in `beat_schedule` (every 30s).
   Requires Celery worker + beat to be running (already used by the backup
   system in this project — same setup applies):
   ```bash
   celery -A university_timetable_system worker -l info
   celery -A university_timetable_system beat -l info
   ```

## Things you may want to adjust

- **`IDLE_MINUTES`** in `notifications/tasks.py` — currently 3, matching
  what was asked for. Raise it if DVCs regularly pause mid-review for longer
  than that.
- **DVC group names** — assumed `"dvc"` and `"dvc_admins"` (matches
  `DVC_GROUPS` already used in `notifications/notification_apis.py` and
  `core/rbac.py`). If your production Django Groups use different names,
  update `DVC_GROUP_NAMES` in `toggle_submission_to_dvc.py`.
- **"Finish Review" button** — not added to the DVC panel template/JS in
  this pass (front-end template changes weren't in scope). Wire a button to
  POST to a new small view that calls
  `notifications.tasks.flush_department_now(department_id)` if you want an
  explicit finish action in addition to the timeout.
