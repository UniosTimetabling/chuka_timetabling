"""
core/department_recipients.py
==============================
Shared "who administers this department" email-recipient resolution.

Previously duplicated between timetable/analysis_reports.py (Email
Department tab — timetable/exam PDFs) and course_management's Course
Allocation Template Excel sharing. Both now import from here so a fix or
change to how CODs/COD Admins/overrides are resolved only has to happen
once.

Recipients are resolved the same way the rest of the system resolves "who
administers this department": via OrgRole.department + the canonical
Role.COD / Role.COD_ADMIN groups (see admins/manage_cod.py, which is what
actually creates these links when a COD account is created), falling back
to Department.leader for the COD's own email if no OrgRole/group match
exists yet (e.g. an older account created before OrgRole existed).
"""

import re

from django.contrib.auth.models import User

from core.rbac import Role


def resolve_department_email_recipients(department):
    """
    Returns {'cod': [...], 'cod_admins': [...]} — each entry
    {'name': str, 'email': str} — for the given Department, deduplicated
    and skipping any account with no email on file.
    """
    cod_qs = User.objects.filter(
        groups__name=Role.COD, org_role__department_id=department.id
    ).distinct()
    cod_admin_qs = User.objects.filter(
        groups__name=Role.COD_ADMIN, org_role__department_id=department.id
    ).distinct()

    cod_list = [
        {'name': u.get_full_name() or u.username, 'email': u.email}
        for u in cod_qs if u.email
    ]
    # Fallback: Department.leader is set at COD-creation time even before
    # OrgRole existed (see admins/manage_cod.py) — use it if the group
    # lookup above found nobody with an email.
    if not cod_list and department.leader_id and department.leader.email:
        u = department.leader
        cod_list = [{'name': u.get_full_name() or u.username, 'email': u.email}]

    cod_admin_list = [
        {'name': u.get_full_name() or u.username, 'email': u.email}
        for u in cod_admin_qs if u.email
    ]

    return {'cod': cod_list, 'cod_admins': cod_admin_list}


def filter_recipients_by_audience(recipients, audience):
    """recipients is the {'cod': [...], 'cod_admins': [...]} dict from
    resolve_department_email_recipients(). audience narrows which of the
    two groups actually get emailed: 'cod' / 'cod_admin' / 'both' (default)."""
    cod = recipients['cod'] if audience in (None, '', 'both', 'cod') else []
    cod_admins = recipients['cod_admins'] if audience in (None, '', 'both', 'cod_admin') else []
    return list(dict.fromkeys([r['email'] for r in cod] + [r['email'] for r in cod_admins]))


def parse_custom_emails(raw):
    """Free-text recipient override from an Email tab / share form — comma,
    semicolon, or newline separated. Returns a deduplicated list of the
    addresses that at least look like an email (contain '@'); silently
    drops anything that doesn't, since this is a manually-typed field."""
    if not raw:
        return []
    parts = re.split(r'[,;\n]+', raw)
    seen = []
    for p in parts:
        p = p.strip()
        if p and '@' in p and p not in seen:
            seen.append(p)
    return seen
