# RBAC Fix — COD Admin / Dean Admin department & group bugs

## The bugs (confirmed by tracing the code + reproducing end-to-end)

1. **COD Admin / Dean Admin accounts had no department/faculty association
   anywhere.** `Department.leader` was set to the COD user, but the
   auto-created `<username>_admin` account was never linked to anything.
   Every department-lookup helper in the app (`detect_user_department`,
   copy-pasted into 7 different files) only checks `Department.leader ==
   user` or a `Lecturer` profile, so it always returned `None` for admin
   accounts. `course_management/cod_panel.py`'s `_assert_owns_department()`
   then rejected them with `"No department associated with your account."`
   — this is the "login rejects" symptom.

2. **Duplicate/inconsistent Group names.** `core/rbac.py` seeds canonical
   lowercase groups (`cod`, `cod_admins`, `dean`, `dean_admins`, ...), but
   `admins/manage_cod.py` and `department_management/dean_panel.py` created
   a *second*, differently-named Group row — literally `"COD"` /
   `"COD Admins"` — instead of reusing the canonical one. That's the
   "it's creating a new group COD Admins" symptom: two separate Group rows
   end up representing the same role, so the `/sudo/cods/` listing and the
   department-form's "create new COD" option disagreed about who's a COD.

3. **`OrgRole.title` was `unique=True`**, but several places wrote a fixed
   literal title ("COD", "Dean", "Dean Admin") for every account. Since
   `user` is already a `OneToOneField` (the real uniqueness constraint),
   the `title` uniqueness was both redundant and actively harmful: creating
   a *second* department's COD, or a second faculty's Dean, silently
   **stole** the OrgRole row away from the first one instead of erroring.

4. **Django admin (`OrgRoleAdmin`) had no department/faculty field at all**
   — `fieldsets = (("Role", {"fields": ("title",)}), ("User Assignment",
   {"fields": ("user", "user_info")}))` — so there was nowhere to allocate
   a department even if you wanted to fix it by hand.

## What changed

- **`core/models.py`** — `OrgRole` gained `department` and `faculty`
  (nullable FKs). Removed the broken `unique=True` on `title`.
- **`core/migrations/0002_orgrole_department_faculty_scope.py`** — new
  migration (validated against the model with `makemigrations --check`).
- **`core/rbac.py`** — added:
  - `link_department_scope(user, department)` / `link_faculty_scope(user,
    faculty)`: scope a user's OrgRole to a department/faculty, and — via
    the `"<username>_admin"` naming convention already used everywhere in
    this codebase — automatically scope their admin counterpart too.
  - `resolve_user_department(user)` / `resolve_user_faculty(user)`: the
    new canonical resolution helpers (check `OrgRole.department`/`faculty`
    first, before falling back to `leader`/`Lecturer`/legacy title-parsing).
- **`admins/manage_cod.py`** — `create_cod()` / `edit_cod()` now:
  - use the canonical `Role.COD` / `Role.COD_ADMIN` groups instead of a
    hand-rolled `"COD"` / `"COD Admins"` group;
  - set `OrgRole.department` for both the COD and the COD Admin;
  - use a per-department OrgRole title (`"COD - <dept>"`) instead of the
    collision-prone fixed `"COD"` string.
- **`admins/manage_deans.py`** — `create_dean` action now uses a
  per-faculty (and per-admin) OrgRole title, and sets `OrgRole.faculty`.
- **`department_management/dean_panel.py`** — `create_cod_and_admin()`
  (used when a Dean creates a department from their own panel) gets the
  same treatment: canonical groups, per-department titles, `department`
  FK wired through once the `Department` row exists.
- **`admins/views.py`** — the generic `ajax_faculty` / `ajax_department`
  "create a new Dean/COD while creating the Faculty/Department" flow now
  calls `link_faculty_scope` / `link_department_scope` once the
  Faculty/Department has a pk.
- **`core/admin.py`** — `OrgRoleAdmin` now has a "Scope" fieldset with
  `department` / `faculty` autocomplete fields, `list_filter`, and columns
  — so a department/faculty can actually be allocated when creating or
  editing a role from Django admin.
- **Department-resolution helpers patched to check `OrgRole.department`
  first** (the authoritative source), in every copy found:
  - `course_allocation/detect_user_department.py`
  - `course_management/cod_panel.py`
  - `program_management/programs_page.py`
  - `course_management/department_timetable.py`
  - `odel_system/views_allocation.py`
  - `campuses_timetable/course_allocation_views.py`
  - `course_allocation/lab_allocations.py` (fallback copy)

## Verified

Ran both flows end-to-end against an in-memory SQLite DB (see test output):

- Creating COD/COD Admin for two different departments no longer collides
  (previously the second department's COD would have overwritten the
  first's `OrgRole` row, and the COD Admin resolved to no department at
  all). Now: `resolve_user_department()` correctly returns the right
  department for **both** the COD and the COD Admin of each department,
  and only the canonical `cod` / `cod_admins` groups exist — no duplicate
  `"COD"` / `"COD Admins"` groups.
- Creating Dean/Dean Admin for two different faculties, same result:
  each gets its own `OrgRole`, and `resolve_user_faculty()` resolves
  correctly for both the Dean and the Dean Admin.
- `python manage.py makemigrations core --check` reports no drift against
  the new model.

## One thing to run after deploying

```
python manage.py migrate
```

No data migration is needed — existing `OrgRole` rows just gain empty
`department`/`faculty` columns. If you have **existing** COD Admin / Dean
Admin accounts created before this fix, back-fill their scope once with:

```python
from django.contrib.auth.models import User
from department_management.models import Department
from core.rbac import link_department_scope

for dept in Department.objects.select_related("leader").all():
    if dept.leader:
        link_department_scope(dept.leader, dept)   # also scopes "<leader>_admin" if it exists
```

(same idea with `Faculty` / `link_faculty_scope` for Deans.)
