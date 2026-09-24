import datetime as dt
import json
from uuid import uuid4

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase, override_settings

from core.rbac import Role
from course_allocation.models import CourseAllocation, LabAllocation
from department_management.models import Department
from desktop_sync.models import DesktopAuthToken, DesktopSyncOp
from faculty_management.models import Faculty
from lecturer_portal.models import Lecturer
from program_management.models import Program, ProgramCourse
from room_management.models import LabVenue, Venue
from timetable.models import ExamTimetable, LabTimetable, Timetable

T = dt.time


def _grant(user, role):
    user.groups.add(Group.objects.get_or_create(name=role)[0])


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        fac = Faculty.objects.create(name="Science")
        cls.dept = Department.objects.create(name="Computer Science", faculty=fac)
        cls.prog = Program.objects.create(name="BSc CS", department=cls.dept)
        cls.prog2 = Program.objects.create(name="BSc Math", department=cls.dept)
        cls.lec1 = Lecturer.objects.create(payroll_number="P1", name="Dr One", email="one@x.ke", designation="Lecturer")
        cls.lec2 = Lecturer.objects.create(payroll_number="P2", name="Dr Two", email="two@x.ke", designation="Lecturer")
        cls.v1 = Venue.objects.create(code="LH1")
        cls.v2 = Venue.objects.create(code="LH2")
        cls.a1 = cls._alloc("COSC101", "Intro", cls.prog, cls.lec1)
        cls.a2 = cls._alloc("MATH101", "Calculus", cls.prog2, cls.lec2)

        cls.admin = User.objects.create_user("ttadmin", password="pw-12345")
        _grant(cls.admin, Role.TIMETABLE_ADMIN)
        cls.staff_only = User.objects.create_user("staffy", password="pw-12345", is_staff=True)
        cls.plain = User.objects.create_user("plain", password="pw-12345")

    @classmethod
    def _alloc(cls, code, name, prog, lec):
        pc = ProgramCourse.objects.create(program=prog, course_code=code, course_name=name)
        return CourseAllocation.objects.create(course_code=code, course_name=name, department=cls.dept,
                                               program=prog, program_course=pc, lecturer=lec)

    def login(self, username="ttadmin"):
        r = Client().post("/api/desktop/auth/login/", json.dumps({"username": username, "password": "pw-12345", "device_label": "t"}),
                          content_type="application/json")
        return r

    def hdr(self, user=None):
        tok = DesktopAuthToken.objects.create(user=user or self.admin, device_label="t")
        return {"HTTP_AUTHORIZATION": f"Token {tok.key}"}

    def push(self, kind, changes, user=None):
        r = Client().post(f"/api/desktop/timetable/{kind}/push/", json.dumps({"changes": changes}),
                          content_type="application/json", **self.hdr(user))
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()["results"]

    def pull(self, kind):
        r = Client().get(f"/api/desktop/timetable/{kind}/", **self.hdr())
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    @staticmethod
    def change(op, entry_id, payload=None, base=0):
        return {"local_op_id": uuid4().hex, "kind": "regular", "op": op, "entry_id": entry_id, "base_version": base, "payload": payload or {}}


class AuthTests(Base):
    def test_timetable_admin_can_sign_in_without_is_staff(self):
        r = self.login()
        self.assertEqual(r.status_code, 200)
        u = r.json()["user"]
        self.assertTrue(u["token"])
        self.assertFalse(u["is_staff"])

    def test_is_staff_without_role_is_refused(self):
        self.assertEqual(self.login("staffy").status_code, 403)
        self.assertEqual(self.login("plain").status_code, 403)

    def test_bad_password_401_and_json_body_required(self):
        r = Client().post("/api/desktop/auth/login/", json.dumps({"username": "ttadmin", "password": "nope"}), content_type="application/json")
        self.assertEqual(r.status_code, 401)
        r = Client().post("/api/desktop/auth/login/", "garbage", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_logout_works_with_csrf_enforced_and_revokes(self):
        key = self.login().json()["user"]["token"]
        c = Client(enforce_csrf_checks=True)
        self.assertEqual(c.post("/api/desktop/auth/logout/", HTTP_AUTHORIZATION=f"Token {key}").status_code, 200)
        self.assertFalse(DesktopAuthToken.objects.filter(key=key).exists())
        self.assertEqual(Client().get("/api/desktop/auth/me/", HTTP_AUTHORIZATION=f"Token {key}").status_code, 401)

    def test_push_works_with_csrf_enforced(self):
        c = Client(enforce_csrf_checks=True)
        r = c.post("/api/desktop/timetable/regular/push/", json.dumps({"changes": []}), content_type="application/json", **self.hdr())
        self.assertEqual(r.status_code, 200)

    def test_role_removed_after_login_cuts_off_token(self):
        h = self.hdr()
        self.admin.groups.clear()
        self.assertEqual(Client().get("/api/desktop/timetable/regular/", **h).status_code, 403)
        _grant(self.admin, Role.TIMETABLE_ADMIN)

    def test_missing_or_bad_token(self):
        self.assertEqual(Client().get("/api/desktop/timetable/regular/").status_code, 401)
        self.assertEqual(Client().get("/api/desktop/timetable/regular/", HTTP_AUTHORIZATION="Token nope").status_code, 401)

    def test_login_rate_limited_returns_json_429(self):
        for _ in range(10):
            self.login("nobody")
        r = self.login("nobody")
        self.assertEqual(r.status_code, 429)
        self.assertIn("error", r.json())


class PullTests(Base):
    def test_pull_returns_rows_that_have_no_sync_meta(self):
        # rows written by bulk_create / fixtures never get any side-table entry
        Timetable.objects.bulk_create([Timetable(course_allocation=self.a1, venue=self.v1, day="Monday", start_time=T(8), end_time=T(10))])
        data = self.pull("regular")
        self.assertEqual(len(data["entries"]), 1)
        e = data["entries"][0]
        self.assertEqual((e["course_code"], e["venue_name"], e["lecturer_name"], e["program_name"]), ("COSC101", "LH1", "Dr One", "BSc CS"))
        self.assertEqual((e["start_time"], e["end_time"], e["day"], e["date"]), ("08:00", "10:00", "Monday", None))
        self.assertIsInstance(e["version"], int)
        self.assertEqual(e["id"], f"srv-regular-{e['server_id']}")

    def test_exam_and_unknown_kind(self):
        ExamTimetable.objects.create(course_allocation=self.a1, venue=self.v1, day="Monday", date=dt.date(2030, 1, 7), start_time=T(8), end_time=T(10))
        e = self.pull("exam")["entries"][0]
        self.assertEqual(e["date"], "2030-01-07")
        self.assertEqual(Client().get("/api/desktop/timetable/bogus/", **self.hdr()).status_code, 404)

    def test_lab_kind_shape(self):
        lv = LabVenue.objects.create(code="LAB1")
        la = LabAllocation.objects.create(program_course=self.a1.program_course, lecturer=self.lec1)
        la.venues.add(lv)
        LabTimetable.objects.create(lab_allocation=la, lab_venue=lv, day="Tuesday", start_time=T(14), end_time=T(17))
        e = self.pull("lab")["entries"][0]
        self.assertEqual((e["course_code"], e["venue_name"], e["venue_kind"]), (self.a1.program_course.course_code, "LAB1", "lab"))

    def test_version_changes_on_queryset_update_which_fires_no_signals(self):
        row = Timetable.objects.create(course_allocation=self.a1, venue=self.v1, day="Monday", start_time=T(8), end_time=T(10))
        v1 = self.pull("regular")["entries"][0]["version"]
        Timetable.objects.filter(pk=row.pk).update(day="Friday")
        v2 = self.pull("regular")["entries"][0]["version"]
        self.assertNotEqual(v1, v2)


class PushTests(Base):
    def slot(self, **kw):
        base = {"course_allocation_id": self.a1.pk, "venue_id": self.v1.pk, "day": "Monday", "start_time": "08:00", "end_time": "10:00"}
        return {**base, **kw}

    def make_row(self, **kw):
        d = dict(course_allocation=self.a1, venue=self.v1, day="Monday", start_time=T(8), end_time=T(10))
        d.update(kw)
        return Timetable.objects.create(**d)

    def current_version(self, row):
        return self.pull("regular")["entries"][[e["server_id"] for e in self.pull("regular")["entries"]].index(row.pk)]["version"]

    def test_create_applied_and_returns_serialized_entry(self):
        res = self.push("regular", [self.change("create", "local-uuid", self.slot())])[0]
        self.assertEqual(res["status"], "applied", res)
        self.assertEqual(res["entry_id"], "local-uuid")
        self.assertEqual(res["server_entry"]["start_time"], "08:00")
        self.assertEqual(Timetable.objects.count(), 1)

    def test_move_applied_with_fresh_version(self):
        row = self.make_row()
        v = self.current_version(row)
        res = self.push("regular", [self.change("move", f"srv-regular-{row.pk}", self.slot(day="Tuesday", start_time="10:00", end_time="12:00"), v)])[0]
        self.assertEqual(res["status"], "applied", res)
        row.refresh_from_db()
        self.assertEqual((row.day, row.start_time), ("Tuesday", T(10)))
        self.assertNotEqual(res["server_entry"]["version"], v)

    def test_venue_only_change_is_not_a_self_clash(self):
        row = self.make_row()
        v = self.current_version(row)
        res = self.push("regular", [self.change("update", f"srv-regular-{row.pk}", self.slot(venue_id=self.v2.pk), v)])[0]
        self.assertEqual(res["status"], "applied", res)

    def test_stale_base_version_conflicts_and_writes_nothing(self):
        row = self.make_row()
        res = self.push("regular", [self.change("move", f"srv-regular-{row.pk}", self.slot(day="Friday"), base=123)])[0]
        self.assertEqual(res["status"], "conflict")
        self.assertEqual(res["server_entry"]["day"], "Monday")
        row.refresh_from_db()
        self.assertEqual(row.day, "Monday")

    def test_web_edit_via_queryset_update_is_detected_as_conflict(self):
        row = self.make_row()
        v = self.current_version(row)                      # desktop pulled this version
        Timetable.objects.filter(pk=row.pk).update(day="Wednesday")   # web/bulk edit, no signals
        res = self.push("regular", [self.change("move", f"srv-regular-{row.pk}", self.slot(day="Friday"), v)])[0]
        self.assertEqual(res["status"], "conflict")
        row.refresh_from_db()
        self.assertEqual(row.day, "Wednesday")

    def test_delete_applied_and_missing_row_conflict(self):
        row = self.make_row()
        v = self.current_version(row)
        self.assertEqual(self.push("regular", [self.change("delete", f"srv-regular-{row.pk}", {}, v)])[0]["status"], "applied")
        self.assertFalse(Timetable.objects.filter(pk=row.pk).exists())
        res = self.push("regular", [self.change("delete", f"srv-regular-{row.pk}", {}, v)])[0]
        self.assertEqual(res["status"], "conflict")
        self.assertIsNone(res["server_entry"])

    def test_retry_with_same_local_op_id_is_idempotent(self):
        ch = self.change("create", "tmp", self.slot())
        self.push("regular", [ch])
        res = self.push("regular", [ch])[0]
        self.assertEqual(res["status"], "applied")
        self.assertEqual(Timetable.objects.count(), 1)
        self.assertEqual(DesktopSyncOp.objects.count(), 1)

    def test_retry_of_update_replays_instead_of_conflicting(self):
        row = self.make_row()
        v = self.current_version(row)
        ch = self.change("move", f"srv-regular-{row.pk}", self.slot(day="Thursday"), v)
        self.push("regular", [ch])
        self.assertEqual(self.push("regular", [ch])[0]["status"], "applied")

    def test_unassigned_venue_zero_is_rejected_cleanly(self):
        row = self.make_row()
        v = self.current_version(row)
        res = self.push("regular", [self.change("move", f"srv-regular-{row.pk}", self.slot(venue_id=0), v)])[0]
        self.assertEqual(res["status"], "error")
        self.assertIn("venue", res["message"].lower())

    def test_bad_day_and_times_rejected(self):
        for bad in (self.slot(day="Funday"), self.slot(start_time="11:00", end_time="09:00"), self.slot(start_time="zz")):
            self.assertEqual(self.push("regular", [self.change("create", "x", bad)])[0]["status"], "error")
        self.assertEqual(Timetable.objects.count(), 0)

    def test_one_bad_change_does_not_abort_the_batch(self):
        res = self.push("regular", [self.change("create", "a", self.slot(day="Nope")), self.change("create", "b", self.slot())])
        self.assertEqual([r["status"] for r in res], ["error", "applied"])

    def test_venue_double_booking_is_rejected_by_web_engine(self):
        self.make_row()                                                  # a1 in LH1 Mon 08-10
        other = self.change("create", "c", {**self.slot(), "course_allocation_id": self.a2.pk})   # a2 same venue+slot
        res = self.push("regular", [other])[0]
        self.assertEqual(res["status"], "error", res)
        self.assertTrue(res["message"].startswith("Clash"), res)
        self.assertEqual(Timetable.objects.count(), 1)

    def test_exam_move_derives_day_from_date(self):
        ex = ExamTimetable.objects.create(course_allocation=self.a1, venue=self.v1, day="Monday", date=dt.date(2030, 1, 7), start_time=T(8), end_time=T(10))
        v = self.pull("exam")["entries"][0]["version"]
        p = {"day": "Wednesday", "date": "2030-01-16", "start_time": "08:00", "end_time": "10:00", "venue_id": self.v1.pk}   # 16 Jan 2030 is a Wednesday
        res = self.push("exam", [{**self.change("move", f"srv-exam-{ex.pk}", p, v), "kind": "exam"}])[0]
        self.assertEqual(res["status"], "applied", res)
        ex.refresh_from_db()
        self.assertEqual((ex.date, ex.day), (dt.date(2030, 1, 16), "Wednesday"))

    def test_exam_wrong_day_for_date_cannot_be_stored(self):
        ex = ExamTimetable.objects.create(course_allocation=self.a1, venue=self.v1, day="Monday", date=dt.date(2030, 1, 7), start_time=T(8), end_time=T(10))
        v = self.pull("exam")["entries"][0]["version"]
        res = self.push("exam", [{**self.change("move", f"srv-exam-{ex.pk}", {"day": "Friday"}, v), "kind": "exam"}])[0]
        self.assertEqual(res["status"], "error")
        ex.refresh_from_db()
        self.assertEqual(ex.day, "Monday")

    def test_lab_move_and_duplicate_create_rules(self):
        lv, lv2 = LabVenue.objects.create(code="LAB1"), LabVenue.objects.create(code="LAB2")
        la = LabAllocation.objects.create(program_course=self.a1.program_course, lecturer=self.lec1)
        la.venues.add(lv, lv2)
        lt = LabTimetable.objects.create(lab_allocation=la, lab_venue=lv, day="Tuesday", start_time=T(14), end_time=T(17))
        v = self.pull("lab")["entries"][0]["version"]
        p = {"day": "Wednesday", "start_time": "14:00", "end_time": "17:00", "venue_id": lv2.pk}
        res = self.push("lab", [{**self.change("move", f"srv-lab-{lt.pk}", p, v), "kind": "lab"}])[0]
        self.assertEqual(res["status"], "applied", res)
        dup = {"course_allocation_id": la.pk, "venue_id": lv.pk, "day": "Friday", "start_time": "08:00", "end_time": "11:00"}
        self.assertEqual(self.push("lab", [{**self.change("create", "n", dup), "kind": "lab"}])[0]["status"], "error")

    def test_desktop_edit_is_attributed_in_side_table(self):
        row = self.make_row()
        v = self.current_version(row)
        self.push("regular", [self.change("move", f"srv-regular-{row.pk}", self.slot(day="Friday"), v)])
        e = [x for x in self.pull("regular")["entries"] if x["server_id"] == row.pk][0]
        self.assertEqual(e["updated_by"], "ttadmin")


class IntegrationTests(Base):
    def test_combined_group_members_are_locked_on_the_desktop(self):
        from course_allocation.models import CombinedCourseGroup

        a3 = self._alloc("COSC101B", "Intro B", self.prog, self.lec1)
        g = CombinedCourseGroup.objects.create(group_code="G1", base_course_code="COSC101", department=self.dept)
        g.allocations.add(self.a1, a3)
        row = Timetable.objects.create(course_allocation=self.a1, venue=self.v1, day="Monday", start_time=T(8), end_time=T(10))
        v = [e for e in self.pull("regular")["entries"] if e["server_id"] == row.pk][0]["version"]
        payload = {"day": "Friday", "start_time": "08:00", "end_time": "10:00", "venue_id": self.v1.pk}
        res = self.push("regular", [self.change("move", f"srv-regular-{row.pk}", payload, v)])[0]
        self.assertEqual(res["status"], "error", res)
        self.assertIn("combined", res["message"])
        self.assertEqual(res["server_entry"]["day"], "Monday")
        row.refresh_from_db()
        self.assertEqual(row.day, "Monday")
        # ...and so is delete
        res = self.push("regular", [self.change("delete", f"srv-regular-{row.pk}", {}, v)])[0]
        self.assertEqual(res["status"], "error")
        self.assertTrue(Timetable.objects.filter(pk=row.pk).exists())

    def test_audit_trail_attributes_desktop_edit_to_token_user(self):
        from backup_system.models import AuditLog

        row = Timetable.objects.create(course_allocation=self.a1, venue=self.v1, day="Monday", start_time=T(8), end_time=T(10))
        v = [e for e in self.pull("regular")["entries"] if e["server_id"] == row.pk][0]["version"]
        AuditLog.objects.all().delete()
        payload = {"day": "Friday", "start_time": "08:00", "end_time": "10:00", "venue_id": self.v1.pk}
        self.push("regular", [self.change("move", f"srv-regular-{row.pk}", payload, v)])
        logs = AuditLog.objects.filter(table_name__icontains="timetable")
        self.assertTrue(logs.exists())
        self.assertEqual({l.user_id for l in logs}, {self.admin.pk})

    def test_token_touch_and_sync_meta_do_not_spam_activity_log(self):
        from core.models import ActivityLog

        row = Timetable.objects.create(course_allocation=self.a1, venue=self.v1, day="Monday", start_time=T(8), end_time=T(10))
        v = [e for e in self.pull("regular")["entries"] if e["server_id"] == row.pk][0]["version"]
        payload = {"day": "Friday", "start_time": "08:00", "end_time": "10:00", "venue_id": self.v1.pk}
        self.push("regular", [self.change("move", f"srv-regular-{row.pk}", payload, v)])
        self.assertTrue(ActivityLog.objects.filter(app_label="timetable").exists())      # real edits still logged
        self.assertFalse(ActivityLog.objects.filter(app_label="desktop_sync").exists())  # our bookkeeping isn't
