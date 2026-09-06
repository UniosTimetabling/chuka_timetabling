# RBAC — Role-Based Access Control
### Chuka University Timetabling System

---

## 1. Overview

All access control **logic** in this project lives in **`core/rbac.py`** — one file, one source of truth for what a role is, what it's called, and what it's allowed to do.

The system uses Django's built-in `auth.Group` model as the role store (no custom User model — the existing `auth.User` table has too many FKs across the project to safely change). Every role is a named Group. The `allowed_roles()` decorator checks group membership before a view runs.

> One adjacent file you'll touch occasionally: **`core/signals.py`** owns the `post_migrate` hook that actually creates the groups in the database (via `core.rbac.ensure_default_groups()`) and additionally attaches fine-grained Django `Permission` objects to a subset of roles via its own `ROLE_PERMISSION_MAP`. See [Section 10](#10-adding-a-new-role) for exactly which constant lives where.

**The old pattern** (scattered, inconsistent):
```python
from django.contrib.auth.decorators import login_required

@login_required
def my_view(request):
    ...

# or even worse — no check at all
def dangerous_view(request):
    ...
```

**The new pattern** (canonical, one line):
```python
from core.rbac import allowed_roles, Role

@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def my_view(request):
    ...
```

---

## 2. Role Constants

All roles are defined as string constants on the `Role` class in `core/rbac.py`.
**Always use `Role.X` constants in new code — never hardcode group name strings.**

```python
from core.rbac import Role
```

| Constant | Group name (stored in DB) | Who it represents |
|---|---|---|
| `Role.DVC` | `dvc` | Deputy Vice Chancellor |
| `Role.DVC_ADMIN` | `dvc_admins` | DVC support staff |
| `Role.DEAN` | `dean` | Faculty Dean |
| `Role.DEAN_ADMIN` | `dean_admins` | Dean support staff |
| `Role.COD` | `cod` | Chair of Department / HOD |
| `Role.COD_ADMIN` | `cod_admins` | COD support staff |
| `Role.DIRECTOR` | `director_timetable` | Director of Timetabling |
| `Role.TIMETABLE_ADMIN` | `timetable_admins` | Timetabling support staff |
| `Role.TIMETABLER` | `timetabler` | Timetabling officer |
| `Role.SUDO` | `sudo` | Super-admin operator |
| `Role.COT` | `cot` | Controller of Teaching |
| `Role.UTILITY` | `utility` | Utility office (venues) |
| `Role.ACADEMIC_AFFAIRS` | `academic_affairs` | Academic Affairs office |
| `Role.DEPARTMENT_USERS` | `department_users` | General department staff |
| `Role.LECTURER` | `lecturer` | Lecturer (read-only access) |
| `Role.CLASSREP` | *(not a Django Group)* | Class Rep — separate session auth |

> **Groups are seeded automatically** on every `python manage.py migrate` via `core/signals.py`.
> You never need to create them manually in the admin.

---

## 3. Pre-built Role Sets

For common tier combinations, use the pre-built sets instead of listing roles individually:

```python
from core.rbac import HOD_AND_ABOVE, MANAGEMENT_ROLES
```

| Set | Contains | Use for |
|---|---|---|
| `HOD_AND_ABOVE` | COD, COD_ADMIN, DEAN, DEAN_ADMIN, DVC, DVC_ADMIN, DIRECTOR, TIMETABLE_ADMIN, SUDO | Any view that department heads and above should access |
| `MANAGEMENT_ROLES` | Same as above | Checking if a user is "management" tier in templates/models |

Usage:
```python
@allowed_roles(*HOD_AND_ABOVE)
def some_management_view(request):
    ...
```

---

## 4. The `allowed_roles()` Decorator

### Signature
```python
allowed_roles(*roles, redirect_to="login")
```

### Rules
- Accepts `Role.X` constants **or** legacy string names (e.g. `"Director Timetable"`) — both resolve correctly
- Superusers (`is_superuser=True`) always pass, regardless of groups
- Unauthenticated users are redirected to `redirect_to` (default: `"login"` URL name)
- Replaces both `@login_required` and `@group_required` — **do not stack them**

### Examples

```python
from core.rbac import allowed_roles, Role, HOD_AND_ABOVE

# Single role
@allowed_roles(Role.COD)
def cod_only_view(request): ...

# Multiple roles — user needs at least one
@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def cod_panel(request): ...

# Timetabling department
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def timetable_panel(request): ...

# Shared between COD and timetabling
@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def shared_view(request): ...

# Using a pre-built tier set
@allowed_roles(*HOD_AND_ABOVE)
def management_view(request): ...

# Custom redirect on denial
@allowed_roles(Role.DVC, Role.DVC_ADMIN, redirect_to="dvc_panel")
def dvc_view(request): ...
```

### ❌ Do NOT do this
```python
# Wrong — stacking decorators
@login_required
@allowed_roles(Role.COD)
def view(request): ...

# Wrong — hardcoded string (bypasses alias resolution, breaks on rename)
@allowed_roles("cod")
def view(request): ...

# Wrong — bare login_required with no role check
@login_required
def sensitive_view(request): ...
```

> **This isn't just theoretical** — `api/shared_venue_group_api.py` and `export_import/course_list_pdf.py` currently stack `@login_required` on top of the legacy `@group_required(...)` shim. It's harmless (both decorators already enforce login internally, so the outer one is redundant rather than conflicting), but it's the exact pattern this section warns against. When you touch either file, drop the redundant `@login_required` and migrate `@group_required("Director Timetable", "Timetable Admins")` / `@group_required("Chairperson of Department","Timetabler","Sudo")` to the canonical `@allowed_roles(Role.X, ...)` form while you're in there.

---

## 5. ClassRep Views

ClassReps are **not** Django `auth.User` — they use a separate session-based login stored in `request.session['classrep_id']`. Use the dedicated decorator:

```python
from core.rbac import classrep_required

@classrep_required
def classrep_dashboard(request):
    classrep_id = request.session['classrep_id']
    ...
```

Never use `@allowed_roles()` for ClassRep views — they have no Django user object.

---

## 6. Role Checks in Python (non-view code)

For checking roles inside models, services, or template context:

```python
from core.rbac import user_has_role, get_user_roles, users_with_role, Role

# Check if a user holds a role
if user_has_role(request.user, Role.COD, Role.DEAN):
    do_something()

# Get all canonical roles a user holds (returns a set of Role constants)
roles = get_user_roles(request.user)

# Query all users with a given role (returns a QuerySet)
cod_users = users_with_role(Role.COD, Role.COD_ADMIN)
```

---

## 7. Role Checks in Templates

The `rbac_context` context processor (registered in `settings.py`) injects two variables into every template automatically:

| Variable | Type | Value |
|---|---|---|
| `user_roles` | `set` | Canonical Role constants the user holds |
| `is_management` | `bool` | True if user holds any management-tier role |

```django
{# Show a button only to timetabling staff #}
{% if "director_timetable" in user_roles or "timetable_admins" in user_roles %}
    <a href="{% url 'timetable_panel' %}">Manage Timetable</a>
{% endif %}

{# Show management-only section #}
{% if is_management %}
    <div class="management-panel">...</div>
{% endif %}

{# Superuser check (still use Django's built-in) #}
{% if user.is_superuser %}
    <a href="{% url 'sudo_homepage' %}">Admin</a>
{% endif %}
```

> **Note:** Compare against the string value of the Role constant (e.g. `"director_timetable"`), not the Python attribute, since templates receive the set of strings.

---

## 8. Role → View Mapping (Current State)

### ✅ Implemented

#### Timetabling Department — `Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN`
| App | Files |
|---|---|
| `timetable/` | `timetable_panel.py`, `exam_timetable_panel.py`, `add_to_merged.py`, `remove_from_merged.py`, `delete_merged_group.py`, `delete_all_timetables.py`, `delete_all_Exam_timetables.py`, `delete_timetable_entry.py`, `delete_exam_entry.py`, `history_views.py` |
| `timetable/algorithms/` | All autoscheduler algorithm files |
| `odel_system/` | `scheduler.py`, `views_auto.py`, `views_manual.py`, `pdf_management_views.py` |
| `campuses_timetable/` | `automatic_views.py`, `manual_views.py`, `pdf_views.py` |
| `resits_timetabling/` | `resit_autosheduler.py`, `manual_timetabler.py`, `publish.py`, `clear_timetable.py`, `resit_timetable_pdf.py` |

#### COD / Department — `Role.COD, Role.COD_ADMIN, Role.SUDO`
| App | Files |
|---|---|
| `course_allocation/` | `auto_allocate_courses.py`, `base_selection_views.py`, `cod_panel_clear.py`, `course_allocations_list.py`, `lab_allocations.py`, `program_enrollment.py`, `toggle_submission_to_dvc.py`, `toggle_submission_to_tt.py`, `view_course_allocations.py` |
| `course_management/` | `cod_panel.py` (main view) |
| `resits_timetabling/` | `cod_panel.py`, `views.py` (COD views) |
| `lecturer_portal/` | `lecturer_panel.py` (uses `*HOD_AND_ABOVE`), `department_lecturers_allocations.py` (`Role.COD, Role.COD_ADMIN, Role.TIMETABLER, Role.SUDO`) |

#### DVC — `Role.DVC, Role.DVC_ADMIN, Role.SUDO`
| App | Files |
|---|---|
| `faculty_management/` | `dvc_panel.py` (main panel + all AJAX endpoints) |

#### Dean — `Role.DEAN, Role.DEAN_ADMIN, Role.SUDO`
| App | Files |
|---|---|
| `department_management/` | `dean_panel.py` |

#### Shared COD + Timetabling — `Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN`
| App | Files |
|---|---|
| `course_management/` | `department_timetable.py`, `get_course_name.py`, `cod_panel.py` (API endpoint) |
| `campuses_timetable/` | `course_allocation_views.py` |
| `odel_system/` | `views_allocation.py` |
| `resits_timetabling/` | `resit_allocation_manager.py`, `resit_import.py`, `views.py` (timetabling views) |

---

### ⏳ Not Yet Implemented — Pending RBAC

These files still use `@login_required` only and need role decorators added.
Use the table below as your guide when implementing each.

#### `admins/`
| File | Suggested Role(s) | Notes |
|---|---|---|
| `manage_timetable_view.py` | `Role.SUDO, Role.DIRECTOR` | Admin-level timetable management |
| `sudo_manage_timetabler.py` | `Role.SUDO` | Managing timetabler accounts — superadmin only |
| `resit_import_admin.py` | `Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN` | Has existing `@group_required("Timetabling Admins","Timetabler","COD Admins")` — migrate to canonical |
| `manage_deans.py` | `Role.SUDO, Role.DVC, Role.DVC_ADMIN` | Managing Dean assignments — DVC scope |
| `manage_cod.py` | `Role.SUDO, Role.DEAN, Role.DEAN_ADMIN` | ⚠ Defines its own local `sudo_required` decorator (superuser-only `login_required` + `user_passes_test`) instead of using `core.rbac`. Comment in the file says this is to avoid a circular import with `admins.views`. Migrate to `@allowed_roles(Role.SUDO)` and resolve the import instead of carrying a third decorator pattern. |
| `sudo_manage_dvc.py` | `Role.SUDO` | ⚠ Same issue as `manage_cod.py` — defines its own near-identical local `sudo_required` decorator. These two should be consolidated into one canonical `@allowed_roles(Role.SUDO)` call rather than two separately-maintained copies of the same logic. |

#### `dashboards/`
| File | Suggested Role(s) | Notes |
|---|---|---|
| `admin_homepage.py` | `Role.SUDO` | Sudo/superadmin landing page |
| `timetable_dashboard_view.py` | `Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN` | Timetabling dept dashboard |
| `analysis_dashboard.py` | `Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN, Role.DVC` | Analytics — management tier |
| `lab_timetable_panel.py` | `Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN` | Lab timetable management |
| `lab_exam_timetable_panel.py` | `Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN` | Lab exam timetable management |
| `cot_exam_timetable.py` | `Role.COT, Role.SUDO` | COT scope |

✅ **Already migrated** (was previously listed here as pending — keep this list in sync as you go): `venues_panel.py` (`Role.UTILITY, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN`), `user_dashboard.py` (`@allowed_roles(...)`, see file for current role set). `published_timetables.py` is intentionally public — see note below.

> `published_timetables.py` is **not pending** — it's deliberately public-facing with no login required. Its download links are protected instead by short-lived HMAC tokens via `core.signed_download` (`make_download_token` / `validate_download_token`), not by a role check. Don't add `@allowed_roles()` here; that would break the public-link use case it was built for.

#### `faculty_management/`
| File | Suggested Role(s) | Notes |
|---|---|---|

#### `department_management/`
| File | Suggested Role(s) | Notes |
|---|---|---|

#### `api/`
| File | Suggested Role(s) | Notes |
|---|---|---|
| `lecturer_api.py` | `Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN` | Shared — used by COD and timetabling |
| `conflicts_report_apis.py` | `Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN` | Timetabling conflict analysis |
| `shared_venue_group_api.py` | `Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN` | Has existing pair to migrate |
| `cot_exam_api.py` | `Role.COT, Role.SUDO` | COT scope |
| `lab_timetable_api.py` | `Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN` | Lab timetabling |

#### `export_import/`
| File | Suggested Role(s) | Notes |
|---|---|---|
| `publish_timetables_pdfs.py` | `Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN` | Publishing — timetabling dept |
| `course_list_pdf.py` | `Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN` | Has existing `@group_required("Chairperson of Department","Timetabler","Sudo")` to migrate |
| `view_and_export_all_course_allocations.py` | `Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN, Role.DVC` | Export for management |
| `zero_student_report.py` | `Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR` | Dept + timetabling report |

#### `feedback/`
| File | Suggested Role(s) | Notes |
|---|---|---|
| `feedback_panel.py` | All authenticated roles | Any staff can submit feedback |

✅ **Already migrated** — `room_management/lab_venues.py` now uses `@allowed_roles(Role.UTILITY, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)`. No longer pending.

#### `program_management/`
| File | Suggested Role(s) | Notes |
|---|---|---|
| `programs_page.py` | `Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DEAN, Role.DEAN_ADMIN` | Program management — department heads |

#### `mess/`
| File | Suggested Role(s) | Notes |
|---|---|---|
| `views.py` | `Role.SUDO, Role.UTILITY` (or a dedicated `Role.MESS_ADMIN` if mess staff aren't already covered by an existing role) | Currently `@login_required` only — any authenticated user can hit the mess admin endpoints, not just canteen staff |

#### `notifications/`
| File | Suggested Role(s) | Notes |
|---|---|---|
| `notification_apis.py` (`get_notifications`, `mark_notification_read`) | N/A — leave as-is, but tighten | Not a role-scoping gap: notifications are inherently per-user (`Notification.objects.for_user(user)`), so a role decorator doesn't apply here the way it does elsewhere. The gap is that these two endpoints have **no `@login_required` either** — they check `request.user.is_authenticated` manually inside the view body instead of failing fast at the decorator level. Lower priority than the others above, but worth tightening for consistency with the rest of the codebase (`@login_required` + the existing manual checks). |

#### `documentation/`
The `documentation` app does **not** use `core.rbac` decorators at all — and that's by design, not an oversight. Access is controlled per-object via `category.can_access(request.user)` / `page.can_access(request.user)` model methods (see `documentation/models.py`), which allow per-category and per-page visibility rules (e.g. some pages public, some staff-only) that a single role decorator on the view can't express. If you need to audit or change documentation access rules, look at `can_access()` on `DocumentationCategory`/`DocumentationPage`, not at `documentation/views.py`'s decorators — there aren't any.

---

## 9. How to Add RBAC to a New or Existing View

### Step 1 — Import
```python
from core.rbac import allowed_roles, Role
```

### Step 2 — Remove old decorator(s)
Delete `@login_required` and/or `@group_required(...)`. Remove the imports if no longer used.

### Step 3 — Add `@allowed_roles`
```python
@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def my_view(request):
    ...
```

### Step 4 — Decide which roles apply
Use this decision tree:

```
Is this a superadmin-only operation?
  └─ Yes → Role.SUDO (optionally + Role.DIRECTOR)

Is this timetable creation / scheduling / generation?
  └─ Yes → Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN

Is this course allocation / department management?
  └─ Yes → Role.COD, Role.COD_ADMIN, Role.SUDO

Is this both (e.g. a shared lookup used by COD and timetablers)?
  └─ Yes → Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN

Is this faculty management (assigning deans, faculties)?
  └─ Yes → Role.DVC, Role.DVC_ADMIN, Role.SUDO

Is this department management (assigning CODs)?
  └─ Yes → Role.DEAN, Role.DEAN_ADMIN, Role.SUDO

Is this a read-only dashboard/view for general staff?
  └─ Yes → Role.DEPARTMENT_USERS (+ any management roles that also need it)
```

### Step 5 — Test
```python
# In Python shell / test
from core.rbac import user_has_role, Role
from django.contrib.auth.models import User

u = User.objects.get(username='testcod')
print(user_has_role(u, Role.COD))  # True / False
```

---

## 10. Adding a New Role

If you need a role that doesn't exist yet:

**1. Add the constant to `core/rbac.py`:**
```python
class Role:
    ...
    MY_NEW_ROLE = "my_new_role"   # add here
```

**2. Add it to `DEFAULT_ROLE_GROUPS` (in `core/rbac.py`):**
```python
DEFAULT_ROLE_GROUPS = [
    ...
    Role.MY_NEW_ROLE,    # add here
]
```
This is what `core.rbac.ensure_default_groups()` reads to create the Group on `post_migrate`.

> **There are two separate group-seeding lists — know which one you're editing.**
> `core/rbac.py` → `DEFAULT_ROLE_GROUPS` (all 15 canonical roles) is read by `ensure_default_groups()`.
> `core/signals.py` → `DEFAULT_GROUPS` (currently 9 entries: `dvc`, `dvc_admins`, `dean`, `dean_admins`, `cod`, `cod_admins`, `director_timetable`, `timetable_admins`, `department_users`) is read by `seed_roles_and_permissions()`, which *also* attaches granular Django `Permission` objects to each group via `ROLE_PERMISSION_MAP`.
> Both functions run on every `post_migrate` (see `core/signals.py` → `sync_groups_and_permissions`), so adding a role only to `DEFAULT_ROLE_GROUPS` is enough to get the Group created and usable with `allowed_roles()`. You only need step 3a below if the new role should also carry fine-grained Django permissions (e.g. for Django admin or `has_perm()` checks).

**3. Add any aliases to `ROLE_ALIASES` if legacy names exist:**
```python
ROLE_ALIASES = {
    ...
    "old name for this role": Role.MY_NEW_ROLE,
}
```

**3a. (Optional) Attach Django permissions, in `core/signals.py`:**
```python
DEFAULT_GROUPS = [
    ...
    "my_new_role",    # add here too, if it needs permissions
]

ROLE_PERMISSION_MAP = {
    ...
    "my_new_role": [
        "some_permission_codename",
    ],
}
```

**4. Run migrations** (no schema migration needed — Groups are data, not schema):
```bash
python manage.py migrate   # triggers post_migrate → sync_groups_and_permissions() → ensure_default_groups()
```

**5. Assign the group to a user** via Django admin → Users → Groups, or:
```python
from django.contrib.auth.models import Group
from django.contrib.auth import get_user_model
User = get_user_model()

u = User.objects.get(username='someone')
g = Group.objects.get(name='my_new_role')
u.groups.add(g)
```

---

## 11. Assigning Roles to Users (Admin)

Via Django admin:
1. Go to `/admin/auth/user/`
2. Select the user
3. Under **Groups**, add the appropriate group(s)
4. Save

Via shell:
```python
from django.contrib.auth.models import Group
user.groups.add(Group.objects.get(name='cod'))
user.groups.set([Group.objects.get(name='timetable_admins')])
user.groups.clear()
```

A user can hold **multiple groups** — `allowed_roles()` grants access if they hold **any one** of the listed roles.

---

## 12. Legacy `@group_required` (Existing Call Sites)

`core/group_required.py` is now a thin shim that delegates to `allowed_roles()`. All existing call sites like:

```python
@group_required("Director Timetable", "Timetable Admins")
```

continue to work unchanged via the alias resolution in `ROLE_ALIASES`. However, **new code should always use `@allowed_roles(Role.X)`** — `@group_required` is kept only for backward compatibility and should not be used going forward.

---

## 13. Running the Tests

```bash
python manage.py test core.tests
```

The test suite (`core/tests.py`) covers:
- Alias resolution (legacy names → canonical constants)
- `user_has_role()` for regular users, superusers, anonymous users
- `get_user_roles()` return values
- `allowed_roles()` decorator: allowed, denied, superuser bypass, unauthenticated redirect
- Legacy group names stored in DB resolving correctly
- `classrep_required()` session path
- `group_required` backward-compat shim

---

## 14. Keeping Section 8 Accurate

Section 8's "✅ Implemented" / "⏳ Not Yet Implemented" tables are a snapshot, not a live view — they go stale the moment someone adds a decorator. Before trusting either table for a security review, re-verify with:

```bash
# What's actually using the canonical decorator
grep -rn "@allowed_roles" --include="*.py" .

# What's still on the legacy shim (candidates to migrate)
grep -rn "@group_required" --include="*.py" .

# What has *only* @login_required (or nothing) — the real "pending" list
grep -rLn "@allowed_roles\|@group_required\|@classrep_required" $(grep -rl "@login_required" --include="*.py" .)
```

When you migrate a file off the pending list, move its row out of the relevant "Not Yet Implemented" table in Section 8 and into the matching "Implemented" table (or mark it ✅ inline) in the same change — don't let the two sections diverge from the codebase again.

---

## Changelog

| Version | Date | Changes |
|---|---|---|
| 1.1.0 | 2026-06-20 | Verified Section 8 against the live codebase: removed `venues_panel.py`, `user_dashboard.py`, and `room_management/lab_venues.py` from "pending" (already migrated to `@allowed_roles`); corrected `lecturer_portal` role mapping (`lecturer_panel.py` uses `*HOD_AND_ABOVE`, not COD-only); clarified `published_timetables.py` is intentionally public, not pending; flagged `admins/manage_cod.py` and `admins/sudo_manage_dvc.py` for carrying their own duplicated local `sudo_required` decorator instead of `core.rbac`; documented the `DEFAULT_ROLE_GROUPS` (`core/rbac.py`) vs `DEFAULT_GROUPS`/`ROLE_PERMISSION_MAP` (`core/signals.py`) distinction in Section 10; added `mess/`, `notifications/`, and `documentation/` (model-level `can_access()` pattern, not a gap) to Section 8; linked the "do not stack decorators" warning to its two real existing instances (`api/shared_venue_group_api.py`, `export_import/course_list_pdf.py`); added Section 14 with grep recipes for re-verifying this section over time |
| 1.0.0 | 2026-03-21 | Initial RBAC documentation alongside the `core/rbac.py` canonicalisation (Role constants, ROLE_ALIASES, allowed_roles decorator, classrep_required, rbac_context) |