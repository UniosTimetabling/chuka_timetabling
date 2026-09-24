"""
core/group_required.py — Legacy decorator shim
================================================
Kept for backward compatibility with ~20+ call sites that already use:

    @group_required("Director Timetable", "Timetable Admins")

All logic now lives in core.rbac so there is one source of truth.
New code should use @allowed_roles() from core.rbac instead.
"""
from core.rbac import allowed_roles


def group_required(*group_names):
    """
    Restrict access to users in the given groups (or superusers).
    Delegates entirely to core.rbac.allowed_roles() — accepts both
    canonical Role constants and legacy alias names via ROLE_ALIASES.

    Backward-compatible: existing call sites need no changes.
    """
    return allowed_roles(*group_names)
