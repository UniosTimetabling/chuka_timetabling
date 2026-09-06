"""
core/rbac.py — Canonical Role-Based Access Control
====================================================

Single source of truth for:
  • Role names (as string constants)
  • All legacy group-name aliases that exist in production
  • Group seeding (called from signals.py post_migrate)
  • allowed_roles() decorator — replaces scattered @login_required / raw group checks

Usage
-----
    from core.rbac import allowed_roles, Role

    @allowed_roles(Role.HOD, Role.TIMETABLER)
    def my_view(request):
        ...

    # ClassRep views use a separate session path — see classrep_required() below.

Design notes
------------
This system does NOT introduce a custom AUTH_USER_MODEL. The project already has
a populated auth.User table with FKs across dozens of migrations; swapping
AUTH_USER_MODEL mid-project would require a full DB rebuild.

Roles are stored as Django Groups (the "use Django Groups" option the project
already uses). This module formalises the existing groups into one registry and
resolves the naming chaos (e.g. "COD" vs "cod" vs "Chairperson of Department").
"""

from functools import wraps
import logging

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.shortcuts import redirect

security_logger = logging.getLogger("security")


# ─────────────────────────────────────────────────────────────────────────────
# 1. CANONICAL ROLE CONSTANTS
#    These are the official group names used in new code.
#    Keep them lowercase-snake-case to match the existing DEFAULT_GROUPS pattern
#    in signals.py.
# ─────────────────────────────────────────────────────────────────────────────

class Role:
    # Governance / Executive
    DVC             = "dvc"               # Deputy Vice Chancellor
    DVC_ADMIN       = "dvc_admins"
    DEAN            = "dean"              # Faculty Dean
    DEAN_ADMIN      = "dean_admins"
    COD             = "cod"               # Chair of Department / HOD
    COD_ADMIN       = "cod_admins"

    # Timetabling
    DIRECTOR        = "director_timetable"   # Director of Timetabling
    TIMETABLE_ADMIN = "timetable_admins"
    TIMETABLER      = "timetabler"           # Timetabling officer
    SUDO            = "sudo"                 # Super-admin operator

    # Academic support
    COT             = "cot"               # Controller of Teaching
    UTILITY         = "utility"           # Utility office (venues)
    ACADEMIC_AFFAIRS = "academic_affairs"
    DEPARTMENT_USERS = "department_users"

    # Lecturer (read access to their own timetable)
    LECTURER        = "lecturer"

    # ClassRep — separate session auth, not a Django Group.
    # Used only as a label for documentation; classrep_required() handles auth.
    CLASSREP        = "__classrep__"


# ─────────────────────────────────────────────────────────────────────────────
# 2. ALIAS MAP
#    Maps every legacy / variant group name (lowercased) → canonical Role value.
#    Lets the decorator accept both old and new names without renaming production
#    groups (which would break existing seeded data and other code).
# ─────────────────────────────────────────────────────────────────────────────

ROLE_ALIASES: dict[str, str] = {
    # DVC variants
    "dvc":                      Role.DVC,
    "dvc admins":               Role.DVC_ADMIN,
    "dvc_admins":               Role.DVC_ADMIN,

    # Dean variants
    "dean":                     Role.DEAN,
    "dean admins":              Role.DEAN_ADMIN,
    "dean_admins":              Role.DEAN_ADMIN,

    # COD / HOD variants
    "cod":                      Role.COD,
    "cod admins":               Role.COD_ADMIN,
    "cod_admins":               Role.COD_ADMIN,
    "chairperson of department": Role.COD,
    "hod":                      Role.COD,

    # Timetabling variants
    "director timetable":        Role.DIRECTOR,
    "director_timetable":        Role.DIRECTOR,
    "timetable admins":          Role.TIMETABLE_ADMIN,
    "timetable_admins":          Role.TIMETABLE_ADMIN,
    "timetabling admins":        Role.TIMETABLE_ADMIN,
    "timetabler":                Role.TIMETABLER,
    "sudo":                      Role.SUDO,

    # Other
    "cot":                       Role.COT,
    "utility":                   Role.UTILITY,
    "academic affairs":          Role.ACADEMIC_AFFAIRS,
    "academic_affairs":          Role.ACADEMIC_AFFAIRS,
    "department_users":          Role.DEPARTMENT_USERS,
    "department users":          Role.DEPARTMENT_USERS,
    "lecturer":                  Role.LECTURER,
}

# Convenience sets for common tier checks
MANAGEMENT_ROLES = {
    Role.DVC, Role.DVC_ADMIN,
    Role.DEAN, Role.DEAN_ADMIN,
    Role.COD, Role.COD_ADMIN,
    Role.DIRECTOR, Role.TIMETABLE_ADMIN,
    Role.SUDO,
}

HOD_AND_ABOVE = {
    Role.COD, Role.COD_ADMIN,
    Role.DEAN, Role.DEAN_ADMIN,
    Role.DVC, Role.DVC_ADMIN,
    Role.DIRECTOR, Role.TIMETABLE_ADMIN,
    Role.SUDO,
}


# ─────────────────────────────────────────────────────────────────────────────
# 3. ALL GROUPS TO SEED ON post_migrate
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_ROLE_GROUPS = [
    Role.DVC, Role.DVC_ADMIN,
    Role.DEAN, Role.DEAN_ADMIN,
    Role.COD, Role.COD_ADMIN,
    Role.DIRECTOR, Role.TIMETABLE_ADMIN,
    Role.TIMETABLER,
    Role.SUDO,
    Role.COT,
    Role.UTILITY,
    Role.ACADEMIC_AFFAIRS,
    Role.DEPARTMENT_USERS,
    Role.LECTURER,
]


def ensure_default_groups():
    """
    Create any missing canonical groups.
    Safe to call multiple times (get_or_create is idempotent).
    Called from core/signals.py inside the post_migrate hook.
    """
    for name in DEFAULT_ROLE_GROUPS:
        Group.objects.get_or_create(name=name)


# ─────────────────────────────────────────────────────────────────────────────
# 4. HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _resolve(raw_name: str) -> str:
    """Normalise a group name to its canonical Role constant."""
    return ROLE_ALIASES.get(raw_name.strip().lower(), raw_name.strip().lower())


def get_user_roles(user) -> set[str]:
    """
    Return the set of canonical Role values the user holds.
    Superusers implicitly hold all roles.
    """
    if not user or not user.is_authenticated:
        return set()
    if user.is_superuser:
        return set(DEFAULT_ROLE_GROUPS)
    return {_resolve(g.name) for g in user.groups.all()}


def user_has_role(user, *roles: str) -> bool:
    """
    Return True if the authenticated user holds at least one of the given roles.
    Accepts both canonical Role constants and legacy/alias names.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    canonical_needed = {_resolve(r) for r in roles}
    return bool(get_user_roles(user) & canonical_needed)


def users_with_role(*roles: str):
    """
    QuerySet of User objects that belong to at least one of the given roles.
    Useful for notification targeting, admin lists, etc.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    canonical = [_resolve(r) for r in roles]
    return User.objects.filter(groups__name__in=canonical).distinct()


# ─────────────────────────────────────────────────────────────────────────────
# 5. DECORATORS
# ─────────────────────────────────────────────────────────────────────────────

def allowed_roles(*roles: str, redirect_to: str = "login"):
    """
    View decorator. Restricts access to authenticated users that hold at least
    one of the specified roles. Superusers always pass.

    Accepts canonical Role constants OR legacy alias names — both resolve
    through ROLE_ALIASES so existing call-sites keep working as-is.

    Args:
        *roles:       One or more Role constants or legacy group-name strings.
        redirect_to:  Named URL to redirect unauthorised users to (default "login").

    Examples::

        @allowed_roles(Role.COD, Role.COD_ADMIN)
        def my_view(request): ...

        # Legacy-style still works:
        @allowed_roles("COD", "COD Admins", "Timetabler")
        def another_view(request): ...

        # Tier shorthand via set:
        @allowed_roles(*HOD_AND_ABOVE)
        def hod_view(request): ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if request.user.is_authenticated:
                if request.user.is_superuser or user_has_role(request.user, *roles):
                    return view_func(request, *args, **kwargs)
                security_logger.warning(
                    "Permission denied | user=%s | view=%s | required_roles=%s | held_roles=%s",
                    request.user.username,
                    view_func.__name__,
                    ", ".join(str(r) for r in roles),
                    ", ".join(sorted(get_user_roles(request.user))) or "none",
                )
            return redirect(redirect_to)

        # Also enforce login (redirects to Django's LOGIN_URL if not authenticated)
        return login_required(_wrapped)

    return decorator


def link_department_scope(user, department):
    """
    Associate `user`'s OrgRole with `department`, and — by the
    "<username>_admin" naming convention used throughout the admin-creation
    code (admins/manage_cod.py, admins/views.py, department_management/
    dean_panel.py) — also scope that user's auto-generated admin
    counterpart, if one exists.

    This is what makes department-scoped views (see the various
    detect_user_department() helpers) resolve a department for a "COD Admin"
    account, which is never Department.leader itself.

    Safe to call with `user=None` or `department=None` (no-op).
    """
    from core.models import OrgRole
    from django.contrib.auth import get_user_model

    if not user or not department:
        return
    OrgRole.objects.filter(user=user).update(department=department)

    AuthUser = get_user_model()
    admin_user = AuthUser.objects.filter(username=f"{user.username}_admin").first()
    if admin_user:
        OrgRole.objects.filter(user=admin_user).update(department=department)


def link_faculty_scope(user, faculty):
    """
    Faculty-level counterpart of link_department_scope(), used for
    Dean / Dean Admin accounts. See its docstring for details.
    """
    from core.models import OrgRole
    from django.contrib.auth import get_user_model

    if not user or not faculty:
        return
    OrgRole.objects.filter(user=user).update(faculty=faculty)

    AuthUser = get_user_model()
    admin_user = AuthUser.objects.filter(username=f"{user.username}_admin").first()
    if admin_user:
        OrgRole.objects.filter(user=admin_user).update(faculty=faculty)


def resolve_user_department(user):
    """
    Canonical department-resolution helper.

    Historically this logic was copy-pasted into half a dozen modules
    (course_allocation/detect_user_department.py, course_management/
    cod_panel.py, program_management/programs_page.py, ...), each slightly
    different, and NONE of them could resolve a department for a "COD Admin"
    account, because that account is never Department.leader and never has
    a Lecturer profile. New code should import this function directly;
    existing duplicates now delegate to it (see their modules for details).

    Priority:
      1. OrgRole.department — direct, authoritative link set at account-
         creation time (works for both COD and COD Admin accounts).
      2. Department.leader == user — the department head themself.
      3. Lecturer profile department (by user FK, then by email match).
      4. Legacy fallback: OrgRole.title formatted as "COD - <dept name>".
    """
    from department_management.models import Department

    if not user or not getattr(user, "is_authenticated", False):
        return None

    org = getattr(user, "org_role", None)
    if org and org.department_id:
        return org.department

    try:
        dept = Department.objects.filter(leader=user).first()
        if dept:
            return dept
    except Exception:
        pass

    try:
        from lecturer_portal.models import Lecturer
        lect = Lecturer.objects.filter(user=user).first()
        if lect and getattr(lect, "department", None):
            return lect.department
        if user.email:
            lect = Lecturer.objects.filter(email__iexact=user.email).first()
            if lect and getattr(lect, "department", None):
                return lect.department
    except Exception:
        pass

    try:
        if org and org.title and "COD" in org.title.upper() and "-" in org.title:
            dept_name = org.title.split("-", 1)[1].strip()
            if dept_name:
                return Department.objects.filter(name__icontains=dept_name).first()
    except Exception:
        pass

    return None


def resolve_user_faculty(user):
    """
    Faculty-level counterpart of resolve_user_department(), for
    Dean / Dean Admin accounts.

    Priority:
      1. OrgRole.faculty — direct, authoritative link.
      2. Faculty.leader == user.
    """
    from faculty_management.models import Faculty

    if not user or not getattr(user, "is_authenticated", False):
        return None

    org = getattr(user, "org_role", None)
    if org and org.faculty_id:
        return org.faculty

    try:
        fac = Faculty.objects.filter(leader=user).first()
        if fac:
            return fac
    except Exception:
        pass

    return None


def classrep_required(view_func):
    """
    Decorator for ClassRep views. ClassReps authenticate via a separate
    plain-session mechanism (request.session['classrep_id']), not Django auth.
    This decorator mirrors the pattern used across classreps/*.py.

    Usage::

        @classrep_required
        def classrep_dashboard(request): ...
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.session.get("classrep_id"):
            security_logger.warning(
                "ClassRep access denied (no session) | view=%s | ip=%s",
                view_func.__name__,
                request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")),
            )
            return redirect("classrep_login")
        return view_func(request, *args, **kwargs)
    return _wrapped


# ─────────────────────────────────────────────────────────────────────────────
# 6. CONTEXT PROCESSOR  (register in settings.py TEMPLATES › OPTIONS › context_processors)
# ─────────────────────────────────────────────────────────────────────────────

def rbac_context(request):
    """
    Injects `user_roles` (set) and `is_management` (bool) into every template
    so role-conditional UI doesn't need per-view boilerplate.

    Register in settings.py::

        TEMPLATES = [{
            ...
            'OPTIONS': {
                'context_processors': [
                    ...
                    'core.rbac.rbac_context',
                ],
            },
        }]
    """
    if request.user.is_authenticated:
        roles = get_user_roles(request.user)
        return {
            "user_roles": roles,
            "is_management": bool(roles & MANAGEMENT_ROLES),
        }
    return {
        "user_roles": set(),
        "is_management": False,
    }
