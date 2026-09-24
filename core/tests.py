"""
core/tests.py — RBAC unit tests
================================
Run with:  python manage.py test core.tests
Or faster: python -m pytest core/tests.py  (needs pytest-django)
"""
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, AnonymousUser

from core.rbac import (
    Role, ROLE_ALIASES, MANAGEMENT_ROLES, HOD_AND_ABOVE,
    allowed_roles, classrep_required, get_user_roles, user_has_role,
    _resolve,
)

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_user(username, *group_names):
    u = User.objects.create_user(username=username, password="x")
    for name in group_names:
        g, _ = Group.objects.get_or_create(name=name)
        u.groups.add(g)
    return u


def dummy_view(request):
    from django.http import HttpResponse
    return HttpResponse("ok")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Alias resolution
# ─────────────────────────────────────────────────────────────────────────────

class TestAliasResolution(TestCase):

    def test_canonical_passes_through(self):
        self.assertEqual(_resolve("cod"), Role.COD)
        self.assertEqual(_resolve("dvc"), Role.DVC)

    def test_legacy_space_name(self):
        self.assertEqual(_resolve("Director Timetable"), Role.DIRECTOR)
        self.assertEqual(_resolve("COD Admins"), Role.COD_ADMIN)
        self.assertEqual(_resolve("DVC Admins"), Role.DVC_ADMIN)

    def test_chairperson_alias(self):
        self.assertEqual(_resolve("Chairperson of Department"), Role.COD)

    def test_hod_alias(self):
        self.assertEqual(_resolve("HOD"), Role.COD)

    def test_unknown_passes_through_lowercased(self):
        self.assertEqual(_resolve("SomeCustomGroup"), "somecustomgroup")

    def test_case_insensitive(self):
        self.assertEqual(_resolve("TIMETABLER"), Role.TIMETABLER)
        self.assertEqual(_resolve("Timetable Admins"), Role.TIMETABLE_ADMIN)
        self.assertEqual(_resolve("timetabling admins"), Role.TIMETABLE_ADMIN)


# ─────────────────────────────────────────────────────────────────────────────
# 2. user_has_role
# ─────────────────────────────────────────────────────────────────────────────

class TestUserHasRole(TestCase):

    def test_user_in_group(self):
        u = make_user("cod1", Role.COD)
        self.assertTrue(user_has_role(u, Role.COD))

    def test_user_not_in_group(self):
        u = make_user("cod2", Role.COD)
        self.assertFalse(user_has_role(u, Role.DVC))

    def test_superuser_passes_any_role(self):
        u = User.objects.create_superuser("su", password="x")
        self.assertTrue(user_has_role(u, Role.COD))
        self.assertTrue(user_has_role(u, Role.DVC))
        self.assertTrue(user_has_role(u, "some_random_role"))

    def test_anonymous_user_fails(self):
        u = AnonymousUser()
        self.assertFalse(user_has_role(u, Role.COD))

    def test_legacy_alias_group_name(self):
        # User stored in group named "Chairperson of Department" (legacy)
        u = make_user("hod_legacy", "Chairperson of Department")
        # Should resolve to Role.COD and pass
        self.assertTrue(user_has_role(u, Role.COD))

    def test_multiple_roles_any_match(self):
        u = make_user("timetabler1", Role.TIMETABLER)
        self.assertTrue(user_has_role(u, Role.COD, Role.TIMETABLER))

    def test_unauthenticated(self):
        u = AnonymousUser()
        self.assertFalse(user_has_role(u, Role.COD))


# ─────────────────────────────────────────────────────────────────────────────
# 3. get_user_roles
# ─────────────────────────────────────────────────────────────────────────────

class TestGetUserRoles(TestCase):

    def test_returns_canonical_roles(self):
        u = make_user("dean1", Role.DEAN, "Timetabler")
        roles = get_user_roles(u)
        self.assertIn(Role.DEAN, roles)
        self.assertIn(Role.TIMETABLER, roles)

    def test_superuser_gets_all(self):
        u = User.objects.create_superuser("su2", password="x")
        roles = get_user_roles(u)
        self.assertIn(Role.DVC, roles)
        self.assertIn(Role.COD, roles)
        self.assertIn(Role.LECTURER, roles)

    def test_unauthenticated_empty(self):
        u = AnonymousUser()
        self.assertEqual(get_user_roles(u), set())


# ─────────────────────────────────────────────────────────────────────────────
# 4. allowed_roles decorator
# ─────────────────────────────────────────────────────────────────────────────

class TestAllowedRolesDecorator(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.protected_view = allowed_roles(Role.COD, Role.TIMETABLER)(dummy_view)

    def _authed_request(self, user):
        req = self.factory.get("/")
        req.user = user
        return req

    def test_allowed_user_passes(self):
        u = make_user("cod3", Role.COD)
        resp = self.protected_view(self._authed_request(u))
        self.assertEqual(resp.status_code, 200)

    def test_wrong_group_redirects(self):
        u = make_user("dean2", Role.DEAN)
        resp = self.protected_view(self._authed_request(u))
        self.assertEqual(resp.status_code, 302)

    def test_superuser_always_passes(self):
        u = User.objects.create_superuser("su3", password="x")
        resp = self.protected_view(self._authed_request(u))
        self.assertEqual(resp.status_code, 200)

    def test_unauthenticated_redirects(self):
        req = self.factory.get("/")
        req.user = AnonymousUser()
        resp = self.protected_view(req)
        self.assertEqual(resp.status_code, 302)

    def test_legacy_group_name_accepted(self):
        # Decorator called with legacy name "Director Timetable"
        view = allowed_roles("Director Timetable")(dummy_view)
        u = make_user("dir1", Role.DIRECTOR)
        resp = view(self._authed_request(u))
        self.assertEqual(resp.status_code, 200)

    def test_legacy_group_stored_resolves(self):
        # User's group is the legacy "Chairperson of Department"
        view = allowed_roles(Role.COD)(dummy_view)
        u = make_user("hod2", "Chairperson of Department")
        resp = view(self._authed_request(u))
        self.assertEqual(resp.status_code, 200)

    def test_hod_and_above_set(self):
        view = allowed_roles(*HOD_AND_ABOVE)(dummy_view)
        for role in HOD_AND_ABOVE:
            u = make_user(f"user_{role}", role)
            resp = view(self._authed_request(u))
            self.assertEqual(resp.status_code, 200, f"Expected 200 for role {role}")

    def test_custom_redirect_url(self):
        view = allowed_roles(Role.DVC, redirect_to="some_other_url")(dummy_view)
        u = make_user("nobody", Role.LECTURER)
        req = self._authed_request(u)
        resp = view(req)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("some_other_url", resp["Location"])


# ─────────────────────────────────────────────────────────────────────────────
# 5. classrep_required decorator
# ─────────────────────────────────────────────────────────────────────────────

class TestClassRepRequired(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.view = classrep_required(dummy_view)

    def _session_request(self, classrep_id=None):
        req = self.factory.get("/")
        req.session = {}
        req.user = AnonymousUser()
        if classrep_id:
            req.session["classrep_id"] = classrep_id
        return req

    def test_with_valid_session_passes(self):
        resp = self.view(self._session_request(classrep_id=42))
        self.assertEqual(resp.status_code, 200)

    def test_without_session_redirects(self):
        resp = self.view(self._session_request())
        self.assertEqual(resp.status_code, 302)


# ─────────────────────────────────────────────────────────────────────────────
# 6. group_required backward-compat shim
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupRequiredShim(TestCase):
    """Ensure existing @group_required call-sites still work unchanged."""

    def setUp(self):
        self.factory = RequestFactory()
        from core.group_required import group_required
        self.view = group_required("Director Timetable", "Timetable Admins")(dummy_view)

    def _authed_request(self, user):
        req = self.factory.get("/")
        req.user = user
        return req

    def test_allowed(self):
        u = make_user("dir2", Role.DIRECTOR)
        resp = self.view(self._authed_request(u))
        self.assertEqual(resp.status_code, 200)

    def test_denied(self):
        u = make_user("dept_user", Role.DEPARTMENT_USERS)
        resp = self.view(self._authed_request(u))
        self.assertEqual(resp.status_code, 302)
