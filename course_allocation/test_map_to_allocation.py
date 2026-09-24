"""
Tests for /groups-electives/ "Map to course allocation"
(course_mapping.map_courses_to_allocation + the map_to_allocation /
list_mappable_courses(mode=alloc) AJAX actions).

    python manage.py test course_allocation.test_map_to_allocation
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from course_allocation.course_mapping import map_courses_to_allocation
from course_allocation.models import (
    AllocationSet, BaseSelection, CombinedCourseGroup, CourseAllocation, SelectionGroup,
    SpecializationCategory, SpecializationStem,
)
from department_management.models import Department
from faculty_management.models import Faculty
from program_management.models import Program, ProgramCourse


class MapToAllocationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cod", password="x")
        fac = Faculty.objects.create(name="Science")
        self.dept = Department.objects.create(name="Computer Science", faculty=fac, leader=self.user)
        self.other_dept = Department.objects.create(name="Physics", faculty=fac)
        self.prog = Program.objects.create(name="BSc CS", department=self.dept)
        self.other_prog = Program.objects.create(name="BSc Phys", department=self.other_dept)
        self.set1 = AllocationSet.objects.create(department=self.dept, name="Sem 1")
        self.cat = SpecializationCategory.objects.create(
            name="Tracks", department=self.dept, program=self.prog, allocation_set=self.set1)
        self.stem_a = SpecializationStem.objects.create(category=self.cat, name="AI")
        self.stem_b = SpecializationStem.objects.create(category=self.cat, name="Security")
        self.stem_c = SpecializationStem.objects.create(category=self.cat, name="Networks")
        self.pc103 = ProgramCourse.objects.create(program=self.prog, course_code="COSC 103", course_name="Intro", year=1, semester=1)
        self.pc105 = ProgramCourse.objects.create(program=self.prog, course_code="COSC 105", course_name="Logic", year=1, semester=1)
        self.pc_other = ProgramCourse.objects.create(program=self.other_prog, course_code="PHYS 101", course_name="Mech", year=1, semester=1)

    def rows(self, pc):
        return CourseAllocation.objects.filter(program_course=pc).order_by("id")

    # ── adding what is missing ───────────────────────────────────────────────
    def test_missing_courses_are_created_and_mapped_at_program_course_level(self):
        res = map_courses_to_allocation([self.stem_a, self.stem_b], [self.pc103, self.pc105], user=self.user)
        self.assertEqual(res["counts"]["added"], 4)          # 2 courses x 2 stems
        self.assertEqual(res["counts"]["error"], 0)
        self.assertEqual(self.rows(self.pc103).count(), 1)   # ONE shared row per course
        for stem in (self.stem_a, self.stem_b):
            self.assertEqual(stem.courses.count(), 2)
            self.assertEqual(set(stem.program_courses.all()), {self.pc103, self.pc105})
            # mapped as a side effect -> flagged like the backfill script's rows
            self.assertEqual(set(stem.program_courses_from_allocation.all()), {self.pc103, self.pc105})
        ca = self.rows(self.pc103).get()
        self.assertEqual(ca.allocation_set_id, self.set1.id)
        self.assertEqual(ca.department_id, self.dept.id)
        self.assertEqual(ca.program_id, self.prog.id)
        self.assertIsNone(ca.section_number)
        self.assertTrue(BaseSelection.objects.filter(program_course=self.pc103, department=self.dept).exists())

    def test_only_the_stems_missing_the_course_get_it(self):
        map_courses_to_allocation([self.stem_a], [self.pc103], user=self.user)
        res = map_courses_to_allocation([self.stem_a, self.stem_b, self.stem_c], [self.pc103], user=self.user)
        self.assertEqual(res["counts"]["skipped"], 1)
        self.assertEqual(res["counts"]["attached"], 2)       # existing row attached to B and C
        self.assertEqual(res["counts"]["added"], 0)
        self.assertEqual(self.rows(self.pc103).count(), 1)
        for stem in (self.stem_a, self.stem_b, self.stem_c):
            self.assertEqual(list(stem.courses.all()), [self.rows(self.pc103).get()])

    # ── already there: skip, or add another copy ─────────────────────────────
    def test_existing_course_is_skipped_and_reported_by_default(self):
        map_courses_to_allocation([self.stem_a], [self.pc103], user=self.user)
        res = map_courses_to_allocation([self.stem_a], [self.pc103], user=self.user)
        self.assertEqual(res["counts"]["skipped"], 1)
        self.assertEqual(res["counts"]["duplicate"], 0)
        self.assertEqual(self.rows(self.pc103).count(), 1)
        self.assertEqual(self.stem_a.courses.count(), 1)
        self.assertIn("skipped", res["lines"][0]["text"])

    def test_skipped_course_still_gets_program_course_mapping(self):
        map_courses_to_allocation([self.stem_a], [self.pc103], user=self.user)
        self.stem_a.program_courses.clear()
        self.stem_a.program_courses_from_allocation.clear()
        res = map_courses_to_allocation([self.stem_a], [self.pc103], user=self.user)
        self.assertEqual(res["counts"]["skipped"], 1)
        self.assertEqual(res["counts"]["pc_mapped"], 1)
        self.assertIn(self.pc103, self.stem_a.program_courses.all())

    def test_allow_duplicates_adds_a_numbered_section_shared_by_the_selected_stems(self):
        map_courses_to_allocation([self.stem_a, self.stem_b], [self.pc103], user=self.user)
        res = map_courses_to_allocation([self.stem_a, self.stem_b], [self.pc103], allow_duplicates=True, user=self.user)
        self.assertEqual(res["counts"]["duplicate"], 2)      # reported per stem...
        self.assertEqual(self.rows(self.pc103).count(), 2)   # ...but ONE new row
        first, second = self.rows(self.pc103)
        self.assertEqual((first.section_number, second.section_number), (1, 2))
        self.assertEqual(second.course_code, "COSC 103-2")
        self.assertEqual(second.number_of_students, 0)
        self.assertIsNone(second.lecturer)
        self.assertEqual(second.allocation_set_id, self.set1.id)
        for stem in (self.stem_a, self.stem_b):
            self.assertEqual(set(stem.courses.all()), {first, second})
        # a third click makes section 3
        map_courses_to_allocation([self.stem_a], [self.pc103], allow_duplicates=True, user=self.user)
        self.assertEqual([r.section_number for r in self.rows(self.pc103)], [1, 2, 3])
        self.assertEqual(self.rows(self.pc103).last().course_code, "COSC 103-3")

    def test_extra_copy_goes_only_to_the_selected_stems(self):
        """B has COSC 103 mapped at program-course level (which normally auto-attaches any new
        allocation of the course) but was not selected, so it must not receive the extra copy."""
        map_courses_to_allocation([self.stem_a], [self.pc103], user=self.user)
        self.stem_b.program_courses.add(self.pc103)
        map_courses_to_allocation([self.stem_a], [self.pc103], allow_duplicates=True, user=self.user)
        self.assertEqual(self.stem_a.courses.count(), 2)
        self.assertEqual(self.stem_b.courses.count(), 0)

    def test_allow_duplicates_does_not_double_add_to_a_stem_missing_the_course(self):
        res = map_courses_to_allocation([self.stem_a], [self.pc103], allow_duplicates=True, user=self.user)
        self.assertEqual(res["counts"]["added"], 1)
        self.assertEqual(res["counts"]["duplicate"], 0)
        self.assertEqual(self.rows(self.pc103).count(), 1)

    # ── elective groups ──────────────────────────────────────────────────────
    def test_elective_group_gets_elective_allocation_and_duplicate_section(self):
        group = SelectionGroup.objects.create(name="Y1 electives", department=self.dept,
                                              program=self.prog, allocation_set=self.set1)
        map_courses_to_allocation([group], [self.pc105], user=self.user)
        ca = self.rows(self.pc105).get()
        self.assertTrue(ca.is_elective)
        self.assertEqual(ca.selection_group_id, group.id)
        self.assertIn(self.pc105, group.program_courses.all())
        res = map_courses_to_allocation([group], [self.pc105], allow_duplicates=True, user=self.user)
        self.assertEqual(res["counts"]["duplicate"], 1)
        self.assertEqual(group.courses.count(), 2)
        self.assertTrue(all(r.is_elective for r in group.courses.all()))

    def test_stem_and_group_together(self):
        group = SelectionGroup.objects.create(name="Y1 electives", department=self.dept,
                                              program=self.prog, allocation_set=self.set1)
        res = map_courses_to_allocation([self.stem_a, group], [self.pc103], user=self.user)
        self.assertEqual(res["counts"]["added"], 2)
        self.assertEqual(self.rows(self.pc103).count(), 1)
        self.assertIn(self.rows(self.pc103).get(), group.courses.all())
        self.assertIn(self.rows(self.pc103).get(), self.stem_a.courses.all())

    # ── guards ───────────────────────────────────────────────────────────────
    def test_course_of_another_program_is_reported_not_mapped(self):
        res = map_courses_to_allocation([self.stem_a], [self.pc_other, self.pc103], user=self.user)
        self.assertEqual(res["counts"]["error"], 1)
        self.assertEqual(res["counts"]["added"], 1)
        self.assertFalse(CourseAllocation.objects.filter(program_course=self.pc_other).exists())
        self.assertNotIn(self.pc_other, self.stem_a.program_courses.all())

    def test_concurrent_sets_do_not_share_rows(self):
        set2 = AllocationSet.objects.create(department=self.dept, name="Sem 2")
        cat2 = SpecializationCategory.objects.create(
            name="Tracks", department=self.dept, program=self.prog, allocation_set=set2)
        stem2 = SpecializationStem.objects.create(category=cat2, name="AI")
        map_courses_to_allocation([self.stem_a], [self.pc103], user=self.user)
        map_courses_to_allocation([stem2], [self.pc103], user=self.user)
        self.assertEqual(self.rows(self.pc103).count(), 2)
        self.assertEqual(self.rows(self.pc103).filter(allocation_set=set2).count(), 1)


class MapToAllocationAjaxTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cod", password="x")
        fac = Faculty.objects.create(name="Science")
        self.dept = Department.objects.create(name="Computer Science", faculty=fac, leader=self.user)
        self.other_dept = Department.objects.create(name="Physics", faculty=fac)
        self.prog = Program.objects.create(name="BSc CS", department=self.dept)
        self.other_prog = Program.objects.create(name="BSc Phys", department=self.other_dept)
        self.set1 = AllocationSet.objects.create(department=self.dept, name="Sem 1", is_legacy=True)
        cat = SpecializationCategory.objects.create(name="Tracks", department=self.dept, program=self.prog, allocation_set=self.set1)
        self.stem_a = SpecializationStem.objects.create(category=cat, name="AI")
        self.stem_b = SpecializationStem.objects.create(category=cat, name="Security")
        self.pc103 = ProgramCourse.objects.create(program=self.prog, course_code="COSC 103", course_name="Intro", year=1, semester=1)
        self.pc105 = ProgramCourse.objects.create(program=self.prog, course_code="COSC 105", course_name="Logic", year=1, semester=1)
        self.pc_other = ProgramCourse.objects.create(program=self.other_prog, course_code="PHYS 101", course_name="Mech", year=1, semester=1)
        self.client.force_login(self.user)
        self.url = reverse("ajax_groups_electives")

    def post(self, data):
        return self.client.post(self.url, data)

    def test_map_to_allocation_then_skip_then_allow_duplicates(self):
        base = {"action": "map_to_allocation",
                "targets": [f"stem:{self.stem_a.id}", f"stem:{self.stem_b.id}"],
                "program_course_ids": [self.pc103.id, self.pc105.id]}
        r = self.post(base).json()
        self.assertEqual(r["status"], "success", r)
        self.assertEqual(r["counts"]["added"], 4)
        self.assertEqual(self.stem_a.courses.count(), 2)

        r = self.post(base).json()
        self.assertEqual(r["counts"]["skipped"], 4)
        self.assertEqual(r["changed"], 0)
        self.assertEqual(CourseAllocation.objects.count(), 2)

        r = self.post({**base, "allow_duplicates": "1"}).json()
        self.assertEqual(r["counts"]["duplicate"], 4)
        self.assertEqual(CourseAllocation.objects.count(), 4)
        self.assertEqual(self.stem_b.courses.count(), 4)

    def test_no_targets_or_no_courses_is_a_400(self):
        r = self.post({"action": "map_to_allocation", "program_course_ids": [self.pc103.id]})
        self.assertEqual(r.status_code, 400)
        r = self.post({"action": "map_to_allocation", "targets": [f"stem:{self.stem_a.id}"]})
        self.assertEqual(r.status_code, 400)

    def test_other_departments_course_is_refused(self):
        r = self.post({"action": "map_to_allocation", "targets": [f"stem:{self.stem_a.id}"],
                       "program_course_ids": [self.pc_other.id]})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(CourseAllocation.objects.exists())

    def test_list_in_alloc_mode_keeps_every_course_and_annotates_allocated_ones(self):
        self.post({"action": "map_to_allocation", "targets": [f"stem:{self.stem_a.id}"],
                   "program_course_ids": [self.pc103.id]})
        body = {"action": "list_mappable_courses", "program": self.prog.id, "mode": "alloc"}
        # one target: COSC 103 is already allocated there but is still listed, flagged
        rows = {c["code"]: c for c in self.post({**body, "targets": [f"stem:{self.stem_a.id}"]}).json()["courses"]}
        self.assertEqual(set(rows), {"COSC 103", "COSC 105"})
        self.assertTrue(rows["COSC 103"]["in_alloc_all"])
        self.assertEqual(rows["COSC 103"]["in_alloc"], "AI")
        self.assertFalse(rows["COSC 105"]["in_alloc_all"])
        # two targets: only one of them has it -> annotated but not "all"
        rows = {c["code"]: c for c in self.post({**body, "targets": [f"stem:{self.stem_a.id}", f"stem:{self.stem_b.id}"]}).json()["courses"]}
        self.assertFalse(rows["COSC 103"]["in_alloc_all"])
        self.assertEqual(rows["COSC 103"]["in_alloc"], "AI")
        # program-course-only mode is unchanged: mapped course is left out
        codes = {c["code"] for c in self.post({"action": "list_mappable_courses", "program": self.prog.id,
                                               "targets": [f"stem:{self.stem_a.id}"]}).json()["courses"]}
        self.assertEqual(codes, {"COSC 105"})


class CrossProgramCombineMappingTests(TestCase):
    """"Map multiple to course allocation" with a second program added —
    combining a shared unit's course group across two programs' stems."""

    def setUp(self):
        self.user = User.objects.create_user("cod5", password="x")
        fac = Faculty.objects.create(name="Education")
        self.dept = Department.objects.create(name="Education", faculty=fac, leader=self.user)
        self.set1 = AllocationSet.objects.create(department=self.dept, name="Sem 1")

        self.arts = Program.objects.create(name="BEd Arts", department=self.dept)
        self.science = Program.objects.create(name="BEd Science", department=self.dept)

        self.pc_edfo_arts = ProgramCourse.objects.create(
            program=self.arts, course_code="EDFO 111", course_name="Foundations", year=1, semester=1)
        self.pc_edfo_science = ProgramCourse.objects.create(
            program=self.science, course_code="EDFO 111", course_name="Foundations", year=1, semester=1)

        arts_cat = SpecializationCategory.objects.create(
            name="Arts Combinations", department=self.dept, program=self.arts, allocation_set=self.set1)
        science_cat = SpecializationCategory.objects.create(
            name="Science Combinations", department=self.dept, program=self.science, allocation_set=self.set1)
        self.arts_stem = SpecializationStem.objects.create(category=arts_cat, name="Arts/Science (Arts side)")
        self.science_stem = SpecializationStem.objects.create(category=science_cat, name="Arts/Science (Science side)")

    def test_list_mappable_courses_multi_program_unions_by_code(self):
        # A code only BEd Arts teaches must still show up (union, not intersection) —
        # tagged with a "new:" placeholder for BEd Science instead of being dropped.
        pc_arts_only = ProgramCourse.objects.create(
            program=self.arts, course_code="ARTS 200", course_name="Art History", year=1, semester=1)
        self.client.force_login(self.user)
        r = self.client.post(reverse("ajax_groups_electives"), {
            "action": "list_mappable_courses", "mode": "alloc",
            "program[]": [self.arts.id, self.science.id],
        })
        body = r.json()
        codes = {c["code"] for c in body["courses"]}
        self.assertEqual(codes, {"EDFO 111", "ARTS 200"})
        edfo_row = next(c for c in body["courses"] if c["code"] == "EDFO 111")
        self.assertEqual(set(edfo_row["program_course_ids"]), {self.pc_edfo_arts.id, self.pc_edfo_science.id})
        self.assertEqual(edfo_row["missing_in"], "")
        arts_row = next(c for c in body["courses"] if c["code"] == "ARTS 200")
        self.assertIn(pc_arts_only.id, arts_row["program_course_ids"])
        self.assertEqual(arts_row["missing_in"], "BEd Science")
        placeholder = next(v for v in arts_row["program_course_ids"] if v != pc_arts_only.id)
        self.assertEqual(placeholder, f"new:{self.science.id}:{pc_arts_only.id}")

    def test_map_courses_to_allocation_combines_two_programs_stems(self):
        res = map_courses_to_allocation(
            [self.arts_stem, self.science_stem], [self.pc_edfo_arts, self.pc_edfo_science], user=self.user,
        )
        self.assertEqual(res["counts"]["error"], 0)
        self.assertEqual(res["counts"]["added"], 2)
        self.assertEqual(len(res["combined_groups"]), 1)

        arts_row = CourseAllocation.objects.get(program=self.arts, program_course=self.pc_edfo_arts)
        science_row = CourseAllocation.objects.get(program=self.science, program_course=self.pc_edfo_science)
        self.assertEqual(arts_row.course_code, science_row.course_code)  # both "EDFO 111" (shared, unlettered)

        combined = CombinedCourseGroup.objects.get(group_code=arts_row.course_code, allocation_set=self.set1)
        self.assertEqual(set(combined.allocations.all()), {arts_row, science_row})

    def test_genuine_cross_program_mismatch_still_errors(self):
        """A course that has NO sibling in the target's own program is still
        a genuine mistake, not a combine — must still be reported."""
        pc_unrelated = ProgramCourse.objects.create(
            program=self.science, course_code="CHEM 101", course_name="Chemistry", year=1, semester=1)
        res = map_courses_to_allocation([self.arts_stem], [pc_unrelated], user=self.user)
        self.assertEqual(res["counts"]["error"], 1)
        self.assertFalse(CourseAllocation.objects.filter(program_course=pc_unrelated).exists())

    def test_map_to_allocation_ajax_end_to_end_with_two_programs(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse("ajax_groups_electives"), {
            "action": "map_to_allocation",
            "targets": [f"stem:{self.arts_stem.id}", f"stem:{self.science_stem.id}"],
            "program_course_ids": [self.pc_edfo_arts.id, self.pc_edfo_science.id],
        })
        body = r.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(len(body["combined_groups"]), 1)
        self.assertEqual(CourseAllocation.objects.count(), 2)

    # ── robustness: 500s reported on /groups-electives/ ────────────────────────
    def test_course_already_in_another_combined_group_is_reported_not_a_500(self):
        """An allocation may sit in only one combined group. One a COD combined by hand under
        another code must not make the whole request fail — the mapping still goes through
        and the un-combinable group is reported."""
        arts_row = CourseAllocation.objects.create(
            course_code="EDFO 111", course_name="Foundations", department=self.dept, program=self.arts,
            program_course=self.pc_edfo_arts, allocation_set=self.set1)
        custom = CombinedCourseGroup.objects.create(
            group_code="MY-CUSTOM", base_course_code="EDFO 111", allocation_set=self.set1, department=self.dept)
        custom.allocations.add(arts_row)

        self.client.force_login(self.user)
        r = self.client.post(reverse("ajax_groups_electives"), {
            "action": "map_to_allocation",
            "targets": [f"stem:{self.arts_stem.id}", f"stem:{self.science_stem.id}"],
            "program_course_ids": [self.pc_edfo_arts.id, self.pc_edfo_science.id],
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "success")
        # both programs' courses were still mapped to their own stem
        self.assertEqual(self.arts_stem.courses.count(), 1)
        self.assertEqual(self.science_stem.courses.count(), 1)
        # ...but nothing was combined, and it says why
        self.assertEqual(body["combined_groups"], [])
        self.assertEqual(len(body["combine_problems"]), 1)
        self.assertIn("MY-CUSTOM", body["combine_problems"][0]["reason"])
        self.assertTrue(any(l["kind"] == "combine" for l in body["lines"]))
        self.assertEqual(set(custom.allocations.all()), {arts_row})

    def test_existing_group_for_the_same_code_is_reused(self):
        map_courses_to_allocation([self.arts_stem, self.science_stem],
                                  [self.pc_edfo_arts, self.pc_edfo_science], user=self.user)
        res = map_courses_to_allocation([self.arts_stem, self.science_stem],
                                        [self.pc_edfo_arts, self.pc_edfo_science], user=self.user)
        self.assertEqual(res["combine_problems"], [])
        self.assertEqual(CombinedCourseGroup.objects.filter(group_code="EDFO 111").count(), 1)

    def test_program_only_mapping_keeps_each_stem_to_its_own_programs_courses(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse("ajax_groups_electives"), {
            "action": "map_courses",
            "targets": [f"stem:{self.arts_stem.id}", f"stem:{self.science_stem.id}"],
            "program_course_ids": [self.pc_edfo_arts.id, self.pc_edfo_science.id],
            "apply_now": "1",
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(self.arts_stem.program_courses.all()), {self.pc_edfo_arts})
        self.assertEqual(set(self.science_stem.program_courses.all()), {self.pc_edfo_science})

    # ── auto-creating a missing program's ProgramCourse on map ─────────────────
    def test_map_courses_creates_missing_program_course_from_placeholder(self):
        """A course code that only BEd Arts has (no BEd Science ProgramCourse) can still be
        ticked and mapped to a BEd Science stem — its ProgramCourse is created on the fly,
        copying the Arts row's fields, and mapped to the Science stem."""
        pc_arts_only = ProgramCourse.objects.create(
            program=self.arts, course_code="ARTS 200", course_name="Art History",
            year=2, semester=1, unit_type="ELECTIVE")
        self.client.force_login(self.user)
        r = self.client.post(reverse("ajax_groups_electives"), {
            "action": "map_courses",
            "targets": [f"stem:{self.science_stem.id}"],
            "program_course_ids": [f"new:{self.science.id}:{pc_arts_only.id}"],
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "success")
        self.assertIn("1 course(s) were newly added to the curriculum", body["message"])
        created = ProgramCourse.objects.get(program=self.science, course_code="ARTS 200")
        self.assertEqual(created.course_name, "Art History")
        self.assertEqual(created.year, 2)
        self.assertEqual(created.semester, 1)
        self.assertEqual(created.unit_type, "ELECTIVE")
        self.assertIn(created, self.science_stem.program_courses.all())

    def test_map_to_allocation_creates_missing_program_course_and_allocates_it(self):
        pc_arts_only = ProgramCourse.objects.create(
            program=self.arts, course_code="ARTS 200", course_name="Art History", year=2, semester=1)
        self.client.force_login(self.user)
        r = self.client.post(reverse("ajax_groups_electives"), {
            "action": "map_to_allocation",
            "targets": [f"stem:{self.science_stem.id}"],
            "program_course_ids": [f"new:{self.science.id}:{pc_arts_only.id}"],
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["counts"]["added"], 1)
        created = ProgramCourse.objects.get(program=self.science, course_code="ARTS 200")
        self.assertTrue(CourseAllocation.objects.filter(program_course=created, program=self.science).exists())
        self.assertIn("newly created to match another program's curriculum", body["message"])

    def test_new_placeholder_reuses_an_already_existing_program_course(self):
        """If the target program already has its own ProgramCourse for that code (just outside
        this list's year/semester filter, say), the placeholder must reuse it, never duplicate it."""
        pc_arts_only = ProgramCourse.objects.create(
            program=self.arts, course_code="ARTS 200", course_name="Art History", year=2, semester=1)
        pc_science_existing = ProgramCourse.objects.create(
            program=self.science, course_code="ARTS 200", course_name="Art History (Sci copy)", year=3, semester=2)
        self.client.force_login(self.user)
        r = self.client.post(reverse("ajax_groups_electives"), {
            "action": "map_courses",
            "targets": [f"stem:{self.science_stem.id}"],
            "program_course_ids": [f"new:{self.science.id}:{pc_arts_only.id}"],
        })
        self.assertEqual(r.json()["status"], "success")
        self.assertEqual(ProgramCourse.objects.filter(program=self.science, course_code="ARTS 200").count(), 1)
        self.assertIn(pc_science_existing, self.science_stem.program_courses.all())

    def test_new_placeholder_for_out_of_scope_program_is_ignored(self):
        """A forged placeholder pointing at a program outside the caller's department must not
        create anything there."""
        other_fac = Faculty.objects.create(name="Other")
        other_dept = Department.objects.create(name="Other Dept", faculty=other_fac)
        other_prog = Program.objects.create(name="Other Program", department=other_dept)
        pc_arts_only = ProgramCourse.objects.create(
            program=self.arts, course_code="ARTS 200", course_name="Art History", year=2, semester=1)
        self.client.force_login(self.user)
        r = self.client.post(reverse("ajax_groups_electives"), {
            "action": "map_courses",
            "targets": [f"stem:{self.arts_stem.id}"],
            "program_course_ids": [f"new:{other_prog.id}:{pc_arts_only.id}"],
        })
        # nothing resolves (the arts stem needed its own program's copy, not the forged one) -> 404
        self.assertEqual(r.status_code, 404)
        self.assertFalse(ProgramCourse.objects.filter(program=other_prog, course_code="ARTS 200").exists())


class StemAllocationViewTests(TestCase):
    """"View combination allocations" on /groups-electives/: list the allocations attached to a
    stem and remove them (the allocation itself and its program-course mapping stay)."""

    def setUp(self):
        self.user = User.objects.create_user("cod6", password="x")
        fac = Faculty.objects.create(name="Science")
        self.dept = Department.objects.create(name="Computer Science", faculty=fac, leader=self.user)
        self.prog = Program.objects.create(name="BSc CS", department=self.dept)
        self.set1 = AllocationSet.objects.create(department=self.dept, name="Sem 1")
        cat = SpecializationCategory.objects.create(
            name="Tracks", department=self.dept, program=self.prog, allocation_set=self.set1)
        self.stem_a = SpecializationStem.objects.create(category=cat, name="AI")
        self.stem_b = SpecializationStem.objects.create(category=cat, name="Security")
        self.pc103 = ProgramCourse.objects.create(program=self.prog, course_code="COSC 103", course_name="Intro", year=1, semester=1)
        self.pc105 = ProgramCourse.objects.create(program=self.prog, course_code="COSC 105", course_name="Logic", year=1, semester=1)
        map_courses_to_allocation([self.stem_a, self.stem_b], [self.pc103, self.pc105], user=self.user)
        self.client.force_login(self.user)

    def post(self, **data):
        return self.client.post(reverse("ajax_groups_electives"), data)

    def ca(self, pc):
        return CourseAllocation.objects.get(program_course=pc)

    def test_page_lists_the_stem_allocations_with_remove_buttons(self):
        r = self.client.get("/groups-electives/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('id="listPanel_stem_alloc"', html)
        self.assertIn('id="pmAllocViewBtn"', html)
        self.assertIn(f'data-alloc="{self.ca(self.pc103).id}"', html)
        self.assertIn("pm-alloc-add-btn", html)

    def test_remove_keeps_the_allocation_and_the_mapping(self):
        ca = self.ca(self.pc103)
        r = self.post(action="remove_from_allocation", id=self.stem_a.id, allocation_ids=[ca.id])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["removed"], 1)
        self.assertNotIn(ca, self.stem_a.courses.all())
        self.assertIn(ca, self.stem_b.courses.all())              # other stem untouched
        self.assertTrue(CourseAllocation.objects.filter(pk=ca.pk).exists())
        self.assertIn(self.pc103, self.stem_a.program_courses.all())
        # legacy single-stem pointer: now only stem B holds it
        ca.refresh_from_db()
        self.assertEqual(ca.specialization_stem_id, self.stem_b.id)

    def test_remove_from_the_only_stem_clears_the_pointer(self):
        ca = self.ca(self.pc103)
        self.post(action="remove_from_allocation", id=self.stem_a.id, allocation_ids=[ca.id])
        self.post(action="remove_from_allocation", id=self.stem_b.id, allocation_ids=[ca.id])
        ca.refresh_from_db()
        self.assertIsNone(ca.specialization_stem_id)
        self.assertEqual(ca.specialization_stems.count(), 0)

    def test_add_again_after_removal(self):
        ca = self.ca(self.pc103)
        self.post(action="remove_from_allocation", id=self.stem_a.id, allocation_ids=[ca.id])
        res = map_courses_to_allocation([self.stem_a], [self.pc103], user=self.user)
        self.assertEqual(res["counts"]["attached"], 1)
        self.assertIn(ca, self.stem_a.courses.all())

    def test_courses_from_a_nested_pool_cannot_be_removed_here(self):
        pool = SelectionGroup.objects.create(name="Pick one", department=self.dept,
                                             program=self.prog, allocation_set=self.set1)
        pool.specialization_stems.add(self.stem_a)
        ca = self.ca(self.pc105)
        pool.courses.add(ca)
        r = self.post(action="remove_from_allocation", id=self.stem_a.id, allocation_ids=[ca.id])
        self.assertEqual(r.status_code, 400)
        self.assertIn(ca, self.stem_a.courses.all())
        # the view shows the pool read-only (no remove button for its course)
        page = self.client.get("/groups-electives/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Comes from an elective pool", page.content.decode())
        panel = page.content.decode().split('id="listPanel_stem_alloc"')[1]
        stem_a_block = panel.split(f'data-parent="{self.stem_a.id}"')[1].split('data-parent=')[0]
        self.assertIn("COSC 105", stem_a_block)
        self.assertNotIn(f'data-alloc="{ca.id}"', stem_a_block)   # pooled -> no remove button

    def test_errors_and_scope(self):
        self.assertEqual(self.post(action="remove_from_allocation", id=self.stem_a.id).status_code, 400)
        r = self.post(action="remove_from_allocation", id=self.stem_a.id, allocation_ids=[999999])
        self.assertEqual(r.status_code, 404)
        # another department's stem is refused for this COD
        other = Department.objects.create(name="Physics", faculty=self.dept.faculty)
        ocat = SpecializationCategory.objects.create(name="X", department=other,
                                                     program=Program.objects.create(name="BSc Phys", department=other))
        ostem = SpecializationStem.objects.create(category=ocat, name="Optics")
        self.assertEqual(self.post(action="remove_from_allocation", id=ostem.id, allocation_ids=[1]).status_code, 403)

    def test_bulk_remove_across_stems_in_one_request(self):
        a103, a105 = self.ca(self.pc103), self.ca(self.pc105)
        r = self.post(action="remove_from_allocation", items=[
            f"{self.stem_a.id}:{a103.id}", f"{self.stem_a.id}:{a105.id}", f"{self.stem_b.id}:{a103.id}",
        ])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["removed"], 3)
        self.assertEqual(list(self.stem_a.courses.all()), [])
        self.assertEqual(list(self.stem_b.courses.all()), [a105])
        self.assertTrue(CourseAllocation.objects.filter(pk__in=[a103.pk, a105.pk]).count() == 2)
        a105.refresh_from_db()
        self.assertEqual(a105.specialization_stem_id, self.stem_b.id)

    def test_bulk_remove_is_refused_as_a_whole_if_one_stem_is_out_of_scope(self):
        other = Department.objects.create(name="Physics", faculty=self.dept.faculty)
        ocat = SpecializationCategory.objects.create(name="X", department=other,
                                                     program=Program.objects.create(name="BSc Phys", department=other))
        ostem = SpecializationStem.objects.create(category=ocat, name="Optics")
        ca = self.ca(self.pc103)
        r = self.post(action="remove_from_allocation",
                      items=[f"{self.stem_a.id}:{ca.id}", f"{ostem.id}:{ca.id}"])
        self.assertEqual(r.status_code, 403)
        self.assertIn(ca, self.stem_a.courses.all())               # nothing was removed

    def test_page_has_bulk_selection_controls(self):
        html = self.client.get("/groups-electives/").content.decode()
        panel = html.split('id="listPanel_stem_alloc"')[1]
        self.assertIn("pm-alloc-cb", panel)
        self.assertIn("pm-alloc-all", panel)
        self.assertIn("pm-alloc-rm-selected", panel)
        self.assertIn('id="pmAllocRemoveSelBtn"', html)
