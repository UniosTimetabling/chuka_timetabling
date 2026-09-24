"""
Tests for /groups-electives/ "Course Groups" (course_group_planner.py +
the group_plan_context / save_group_plan AJAX actions).

    python manage.py test course_allocation.test_course_group_planner
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from course_allocation import course_group_planner as planner
from course_allocation.models import (
    AllocationSet, CourseAllocation, GroupingTemplate,
    GroupingTemplateGroup, GroupingTemplateStemAssignment, StudentGroup,
    SpecializationCategory, SpecializationStem, StemStudentCount, CombinedCourseGroup,
)
from department_management.models import Department
from faculty_management.models import Faculty
from program_management.models import Program, ProgramCourse


class LettersTests(TestCase):
    def test_letters_sequence_wraps_past_z(self):
        letters = planner.letters_sequence(28)
        self.assertEqual(letters[0], "A")
        self.assertEqual(letters[25], "Z")
        self.assertEqual(letters[26], "AA")
        self.assertEqual(letters[27], "AB")

    def test_letters_sequence_zero_is_empty(self):
        self.assertEqual(planner.letters_sequence(0), [])


class SaveGroupPlanNoCombinationTests(TestCase):
    """Program/year with NO Combination Stems: plain letter split."""

    def setUp(self):
        self.user = User.objects.create_user("cod", password="x")
        fac = Faculty.objects.create(name="Education")
        self.dept = Department.objects.create(name="Education", faculty=fac, leader=self.user)
        self.prog = Program.objects.create(name="BEd Arts", department=self.dept)
        self.set1 = AllocationSet.objects.create(department=self.dept, name="Sem 1")
        self.pc_edfo = ProgramCourse.objects.create(
            program=self.prog, course_code="EDFO 111", course_name="Foundations", year=1, semester=1,
        )
        self.pc_epsc = ProgramCourse.objects.create(
            program=self.prog, course_code="EPSC 111", course_name="Psychology", year=1, semester=1,
        )
        # An elective in the same year/semester must NEVER be swept in.
        self.pc_elective = ProgramCourse.objects.create(
            program=self.prog, course_code="ELEC 100", course_name="Free choice", year=1, semester=1,
            unit_type="ELECTIVE",
        )

    def test_num_groups_one_is_a_no_op(self):
        result = planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=1,
            scope=GroupingTemplate.SCOPE_ALL, allocation_set=self.set1,
        )
        self.assertEqual(result["created"], 0)
        self.assertFalse(StudentGroup.objects.exists())
        self.assertFalse(CourseAllocation.objects.exists())

    def test_all_scope_creates_letters_and_splits_every_compulsory_course(self):
        result = planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=3,
            scope=GroupingTemplate.SCOPE_ALL, allocation_set=self.set1,
        )
        self.assertEqual(set(StudentGroup.objects.values_list("letter", flat=True)), {"A", "B", "C"})
        # 2 compulsory courses x 3 letters = 6 rows; elective excluded.
        self.assertEqual(CourseAllocation.objects.count(), 6)
        codes = set(CourseAllocation.objects.values_list("course_code", flat=True))
        self.assertEqual(codes, {
            "EDFO 111-A", "EDFO 111-B", "EDFO 111-C",
            "EPSC 111-A", "EPSC 111-B", "EPSC 111-C",
        })
        self.assertFalse(CourseAllocation.objects.filter(program_course=self.pc_elective).exists())
        self.assertEqual(result["created"] + result["tagged"], 6)

        template = GroupingTemplate.objects.get(program=self.prog, year=1, semester=1, intake="normal")
        self.assertEqual(template.scope, GroupingTemplate.SCOPE_ALL)
        self.assertEqual(set(template.groups.values_list("letter", flat=True)), {"A", "B", "C"})

    def test_selected_scope_only_splits_chosen_courses(self):
        result = planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=2,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo.id],
            allocation_set=self.set1,
        )
        self.assertEqual(CourseAllocation.objects.count(), 2)
        self.assertEqual(
            set(CourseAllocation.objects.values_list("course_code", flat=True)),
            {"EDFO 111-A", "EDFO 111-B"},
        )
        template = GroupingTemplate.objects.get(program=self.prog, year=1, semester=1, intake="normal")
        self.assertEqual({c.base_course_code for c in template.course_codes.all()}, {"EDFO 111"})
        self.assertEqual(result["created"], 2)

    def test_rerun_tags_instead_of_duplicating(self):
        planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=2,
            scope=GroupingTemplate.SCOPE_ALL, allocation_set=self.set1,
        )
        first_count = CourseAllocation.objects.count()
        result2 = planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=2,
            scope=GroupingTemplate.SCOPE_ALL, allocation_set=self.set1,
        )
        self.assertEqual(CourseAllocation.objects.count(), first_count)
        self.assertEqual(result2["created"], 0)
        self.assertEqual(result2["already_assigned"], 4)

    def test_raising_num_groups_continues_own_letters(self):
        planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=2,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo.id],
            allocation_set=self.set1,
        )
        result = planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=4,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo.id],
            allocation_set=self.set1,
        )
        self.assertEqual(
            set(StudentGroup.objects.filter(program=self.prog).values_list("letter", flat=True)),
            {"A", "B", "C", "D"},
        )
        self.assertEqual(
            set(CourseAllocation.objects.values_list("course_code", flat=True)),
            {"EDFO 111-A", "EDFO 111-B", "EDFO 111-C", "EDFO 111-D"},
        )
        self.assertIsNone(result["continued_from"])  # own cell, not cross-program

    def test_shared_unit_continues_lettering_across_different_programs(self):
        """BEd Arts splits EDFO 111 into A-E; BEd Science's OWN split of the
        same shared unit must continue from F, not restart at A."""
        planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=5,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo.id],
            allocation_set=self.set1,
        )

        prog2 = Program.objects.create(name="BEd Science", department=self.dept)
        pc_edfo_science = ProgramCourse.objects.create(
            program=prog2, course_code="EDFO 111", course_name="Foundations", year=1, semester=1,
        )
        result = planner.save_group_plan(
            program=prog2, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=3,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[pc_edfo_science.id],
            allocation_set=self.set1,
        )

        self.assertEqual(result["continued_from"], "F")
        self.assertEqual(
            set(StudentGroup.objects.filter(program=prog2).values_list("letter", flat=True)),
            {"F", "G", "H"},
        )
        self.assertEqual(
            set(CourseAllocation.objects.filter(program=prog2).values_list("course_code", flat=True)),
            {"EDFO 111-F", "EDFO 111-G", "EDFO 111-H"},
        )
        # BEd Arts's own A-E rows are untouched.
        self.assertEqual(
            set(CourseAllocation.objects.filter(program=self.prog).values_list("course_code", flat=True)),
            {"EDFO 111-A", "EDFO 111-B", "EDFO 111-C", "EDFO 111-D", "EDFO 111-E"},
        )

    def test_unrelated_course_code_does_not_affect_continuation(self):
        """A DIFFERENT course code already split elsewhere must not push this
        program's own brand-new split away from starting at A."""
        planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=5,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo.id],
            allocation_set=self.set1,
        )
        prog2 = Program.objects.create(name="BEd Science", department=self.dept)
        pc_unrelated = ProgramCourse.objects.create(
            program=prog2, course_code="CHEM 101", course_name="Chemistry", year=1, semester=1,
        )
        result = planner.save_group_plan(
            program=prog2, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=2,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[pc_unrelated.id],
            allocation_set=self.set1,
        )
        self.assertIsNone(result["continued_from"])
        self.assertEqual(
            set(StudentGroup.objects.filter(program=prog2).values_list("letter", flat=True)),
            {"A", "B"},
        )


class CrossProgramCombineTests(TestCase):
    """3 groups pure BEd Arts, 1 pure BEd Science, 1 combined between an
    Arts stem and a Science stem — all splitting the SAME shared unit."""

    def setUp(self):
        self.user = User.objects.create_user("cod4", password="x")
        fac = Faculty.objects.create(name="Education")
        self.dept = Department.objects.create(name="Education", faculty=fac, leader=self.user)
        self.set1 = AllocationSet.objects.create(department=self.dept, name="Sem 1")

        self.arts = Program.objects.create(name="BEd Arts", department=self.dept)
        self.science = Program.objects.create(name="BEd Science", department=self.dept)

        self.pc_edfo_arts = ProgramCourse.objects.create(
            program=self.arts, course_code="EDFO 111", course_name="Foundations", year=1, semester=1,
        )
        self.pc_edfo_science = ProgramCourse.objects.create(
            program=self.science, course_code="EDFO 111", course_name="Foundations", year=1, semester=1,
        )

        arts_cat = SpecializationCategory.objects.create(
            name="Arts Combinations", department=self.dept, program=self.arts,
            year=1, semester=1, allocation_set=self.set1,
        )
        science_cat = SpecializationCategory.objects.create(
            name="Science Combinations", department=self.dept, program=self.science,
            year=1, semester=1, allocation_set=self.set1,
        )
        self.arts_combo_stem = SpecializationStem.objects.create(category=arts_cat, name="Arts/Science (Arts side)")
        self.science_combo_stem = SpecializationStem.objects.create(category=science_cat, name="Arts/Science (Science side)")

    def test_combinable_stems_lists_sibling_program_sharing_the_unit(self):
        combinable = planner.combinable_stems_for(self.arts, 1, 1, ["EDFO 111"])
        self.assertEqual([s.id for s in combinable], [self.science_combo_stem.id])

    def test_pinning_same_letter_across_programs_creates_combined_group(self):
        result = planner.save_group_plan(
            program=self.arts, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=1,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo_arts.id],
            stem_letter_map={
                self.arts_combo_stem.id: ["A"],
                self.science_combo_stem.id: ["A"],
            },
            allocation_set=self.set1,
        )
        self.assertEqual(len(result["combined_groups"]), 1)
        self.assertEqual(result["combined_groups"][0]["programs"], ["BEd Arts", "BEd Science"])

        arts_row = CourseAllocation.objects.get(program=self.arts, specialization_stem=self.arts_combo_stem)
        science_row = CourseAllocation.objects.get(program=self.science, specialization_stem=self.science_combo_stem)
        self.assertEqual(arts_row.course_code, "EDFO 111-A")
        self.assertEqual(science_row.course_code, "EDFO 111-A")
        # Different StudentGroup rows (one per program) but the SAME letter.
        self.assertNotEqual(arts_row.student_group_id, science_row.student_group_id)
        self.assertEqual(arts_row.student_group.letter, science_row.student_group.letter)

        combined = CombinedCourseGroup.objects.get(group_code="EDFO 111-A", allocation_set=self.set1)
        self.assertEqual(set(combined.allocations.all()), {arts_row, science_row})
        self.assertEqual(combined.base_course_code, "EDFO 111")

    def test_pure_arts_and_pure_science_letters_are_not_combined(self):
        """A-C pure Arts, D pure Science, E combined — only E gets grouped."""
        planner.save_group_plan(
            program=self.arts, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=3,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo_arts.id],
            allocation_set=self.set1,
        )
        planner.save_group_plan(
            program=self.science, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=1,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo_science.id],
            stem_letter_map={},
            allocation_set=self.set1,
        )
        # A dedicated 5th, shared letter for the combined stem — raising
        # Arts's own count so "E" exists in its lettered range too.
        result = planner.save_group_plan(
            program=self.arts, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=5,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo_arts.id],
            stem_letter_map={
                self.arts_combo_stem.id: ["E"],
                self.science_combo_stem.id: ["E"],
            },
            allocation_set=self.set1,
        )
        self.assertEqual(len(result["combined_groups"]), 1)
        self.assertFalse(CombinedCourseGroup.objects.filter(group_code="EDFO 111-A").exists())
        self.assertFalse(CombinedCourseGroup.objects.filter(group_code="EDFO 111-D").exists())
        self.assertTrue(CombinedCourseGroup.objects.filter(group_code="EDFO 111-E").exists())


class SaveGroupPlanWithCombinationTests(TestCase):
    """Program/year WITH Combination Stems: stem-letter pinning."""

    def setUp(self):
        self.user = User.objects.create_user("cod2", password="x")
        fac = Faculty.objects.create(name="Education")
        self.dept = Department.objects.create(name="Education", faculty=fac, leader=self.user)
        self.prog = Program.objects.create(name="BEd Arts", department=self.dept)
        self.set1 = AllocationSet.objects.create(department=self.dept, name="Sem 1")
        self.cat = SpecializationCategory.objects.create(
            name="Teaching Subjects", department=self.dept, program=self.prog,
            year=1, semester=1, allocation_set=self.set1,
        )
        self.stem_englit = SpecializationStem.objects.create(category=self.cat, name="English/Literature")
        self.stem_histcre = SpecializationStem.objects.create(category=self.cat, name="History/CRE")

        self.pc_edfo = ProgramCourse.objects.create(
            program=self.prog, course_code="EDFO 111", course_name="Foundations", year=1, semester=1,
        )
        self.pc_epsc = ProgramCourse.objects.create(
            program=self.prog, course_code="EPSC 111", course_name="Psychology", year=1, semester=1,
        )
        self.pc_edci = ProgramCourse.objects.create(
            program=self.prog, course_code="EDCI 111", course_name="Curriculum", year=1, semester=1,
        )

    def test_stem_letter_pins_give_each_stem_its_own_section(self):
        result = planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=5,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo.id, self.pc_epsc.id, self.pc_edci.id],
            stem_letter_map={self.stem_englit.id: ["A"], self.stem_histcre.id: ["B"]},
            allocation_set=self.set1,
        )
        # 3 courses x 2 stems = 6 rows.
        self.assertEqual(CourseAllocation.objects.count(), 6)

        englit_codes = set(
            CourseAllocation.objects.filter(specialization_stem=self.stem_englit)
            .values_list("course_code", flat=True)
        )
        self.assertEqual(englit_codes, {"EDFO 111-A", "EPSC 111-A", "EDCI 111-A"})

        histcre_codes = set(
            CourseAllocation.objects.filter(specialization_stem=self.stem_histcre)
            .values_list("course_code", flat=True)
        )
        self.assertEqual(histcre_codes, {"EDFO 111-B", "EPSC 111-B", "EDCI 111-B"})

        # Every row for a stem shares the SAME StudentGroup id (one physical
        # lettered section per stem, not one per course).
        englit_group_ids = set(
            CourseAllocation.objects.filter(specialization_stem=self.stem_englit)
            .values_list("student_group_id", flat=True)
        )
        self.assertEqual(len(englit_group_ids), 1)

        # Stems each got their courses added for bookkeeping.
        self.assertEqual(self.stem_englit.courses.count(), 3)
        self.assertEqual(self.stem_histcre.courses.count(), 3)

        # 5 letters requested but only A and B used by stems -> C, D, E exist
        # as plain StudentGroup rows with no CourseAllocation forced onto them.
        self.assertEqual(StudentGroup.objects.filter(program=self.prog).count(), 5)
        self.assertFalse(CourseAllocation.objects.filter(student_group__letter="C").exists())

        # Remembered for auto-allocate replay.
        template = GroupingTemplate.objects.get(program=self.prog, year=1, semester=1, intake="normal")
        pins = {
            (sa.stem_id, sa.group.letter)
            for sa in GroupingTemplateStemAssignment.objects.filter(template=template)
        }
        self.assertEqual(pins, {(self.stem_englit.id, "A"), (self.stem_histcre.id, "B")})

    def test_letter_pinned_to_two_stems_is_rejected_by_model_constraint(self):
        planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=2,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo.id],
            stem_letter_map={self.stem_englit.id: ["A"]},
            allocation_set=self.set1,
        )
        # Re-pinning the SAME letter "A" to a DIFFERENT stem updates the
        # existing pin in place (one letter -> one stem), it does not create
        # a second, conflicting row.
        planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=2,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo.id],
            stem_letter_map={self.stem_histcre.id: ["A"]},
            allocation_set=self.set1,
        )
        template = GroupingTemplate.objects.get(program=self.prog, year=1, semester=1, intake="normal")
        self.assertEqual(GroupingTemplateStemAssignment.objects.filter(template=template).count(), 1)
        self.assertEqual(
            GroupingTemplateStemAssignment.objects.get(template=template).stem_id, self.stem_histcre.id,
        )

    def test_no_stem_pins_falls_back_to_plain_split(self):
        result = planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=2,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo.id],
            stem_letter_map=None,
            allocation_set=self.set1,
        )
        rows = CourseAllocation.objects.filter(program_course=self.pc_edfo)
        self.assertEqual(rows.count(), 2)
        self.assertTrue(all(r.specialization_stem_id is None for r in rows))

    def test_course_allocation_allows_student_group_with_stem(self):
        """The relaxed clean() must accept student_group + specialization_stem
        together for a non-elective course (this used to be forbidden)."""
        group = StudentGroup.objects.create(
            program=self.prog, year=1, semester=1, intake="normal", letter="A",
        )
        ca = CourseAllocation(
            course_code="EDFO 111-A", course_name="Foundations", department=self.dept,
            program=self.prog, program_course=self.pc_edfo, intake="normal",
            student_group=group, specialization_stem=self.stem_englit,
            allocation_set=self.set1,
        )
        ca.full_clean()  # must not raise
        ca.save()
        self.assertEqual(CourseAllocation.objects.count(), 1)


class GroupPlanAjaxTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cod3", password="x")
        fac = Faculty.objects.create(name="Education")
        self.dept = Department.objects.create(name="Education", faculty=fac, leader=self.user)
        self.prog = Program.objects.create(name="BEd Arts", department=self.dept)
        AllocationSet.objects.create(department=self.dept, name="Sem 1")
        self.pc_edfo = ProgramCourse.objects.create(
            program=self.prog, course_code="EDFO 111", course_name="Foundations", year=1, semester=1,
        )
        self.client.force_login(self.user)

    def post(self, data):
        return self.client.post(reverse("ajax_groups_electives"), data)

    def test_group_plan_context_lists_courses(self):
        r = self.post({
            "action": "group_plan_context", "program": self.prog.id,
            "year": 1, "semester": 1, "intake": "normal",
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual([c["code"] for c in body["courses"]], ["EDFO 111"])
        self.assertEqual(body["existing"]["num_groups"], 0)
        self.assertIsNone(body["existing"]["next_letter_hint"])

    def test_group_plan_context_hints_continuation_from_other_program(self):
        from course_allocation import course_group_planner as planner
        from course_allocation.models import GroupingTemplate

        prog2 = Program.objects.create(name="BEd Science", department=self.dept)
        pc2 = ProgramCourse.objects.create(
            program=prog2, course_code="EDFO 111", course_name="Foundations", year=1, semester=1,
        )
        planner.save_group_plan(
            program=self.prog, year=1, semester=1, intake="normal",
            department=self.dept, user=self.user, num_groups=5,
            scope=GroupingTemplate.SCOPE_SELECTED,
            selected_program_course_ids=[self.pc_edfo.id],
        )
        r = self.post({
            "action": "group_plan_context", "program": prog2.id,
            "year": 1, "semester": 1, "intake": "normal",
        })
        body = r.json()
        self.assertEqual(body["existing"]["next_letter_hint"], "F")

    def test_save_group_plan_end_to_end(self):
        r = self.post({
            "action": "save_group_plan", "program": self.prog.id,
            "year": 1, "semester": 1, "intake": "normal",
            "num_groups": 2, "scope": "all",
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(CourseAllocation.objects.count(), 2)
        self.assertEqual(StudentGroup.objects.filter(program=self.prog).count(), 2)

    def test_save_group_plan_rejects_out_of_range_num_groups(self):
        r = self.post({
            "action": "save_group_plan", "program": self.prog.id,
            "year": 1, "semester": 1, "intake": "normal",
            "num_groups": 0, "scope": "all",
        })
        self.assertEqual(r.status_code, 400)

    def test_save_group_plan_selected_scope_requires_courses(self):
        r = self.post({
            "action": "save_group_plan", "program": self.prog.id,
            "year": 1, "semester": 1, "intake": "normal",
            "num_groups": 2, "scope": "selected",
        })
        self.assertEqual(r.status_code, 400)


class StemStudentNumberTermsTests(TestCase):
    """course_group_planner.stem_student_number_terms / apply_stem_student_count."""

    def setUp(self):
        self.user = User.objects.create_user("cod4", password="x")
        fac = Faculty.objects.create(name="Education")
        self.dept = Department.objects.create(name="Education", faculty=fac, leader=self.user)
        self.prog = Program.objects.create(name="BEd Arts", department=self.dept)
        self.set1 = AllocationSet.objects.create(department=self.dept, name="Sem 1")
        self.cat = SpecializationCategory.objects.create(
            name="Teaching Subjects", department=self.dept, program=self.prog,
            year=None, semester=None, allocation_set=self.set1,
        )
        self.stem = SpecializationStem.objects.create(category=self.cat, name="English/Literature")

        self.pc_edfo = ProgramCourse.objects.create(
            program=self.prog, course_code="EDFO 111", course_name="Foundations", year=1, semester=1,
        )
        self.pc_epsc = ProgramCourse.objects.create(
            program=self.prog, course_code="EPSC 111", course_name="Psychology", year=1, semester=1,
        )
        # A second term for the SAME stem — category.year/semester is "Any",
        # so the same combination can recur in a later cohort's year/semester.
        self.pc_edfo_y2 = ProgramCourse.objects.create(
            program=self.prog, course_code="EDFO 211", course_name="Foundations II", year=2, semester=1,
        )

        def _alloc(pc, letter="A"):
            return CourseAllocation.objects.create(
                course_code=f"{pc.course_code}-{letter}", course_name=pc.course_name,
                department=self.dept, origin_department=self.dept, program=self.prog,
                program_course=pc, intake="normal", allocation_set=self.set1,
                specialization_stem=self.stem, number_of_students=0,
            )

        self.ca_edfo = _alloc(self.pc_edfo)
        self.ca_epsc = _alloc(self.pc_epsc)
        self.ca_edfo_y2 = _alloc(self.pc_edfo_y2)
        self.stem.courses.add(self.ca_edfo, self.ca_epsc, self.ca_edfo_y2)

    def test_terms_are_grouped_by_year_semester(self):
        terms = planner.stem_student_number_terms(self.stem)
        keys = {(t["year"], t["semester"]) for t in terms}
        self.assertEqual(keys, {(1, 1), (2, 1)})
        y1 = next(t for t in terms if t["year"] == 1)
        self.assertEqual(y1["course_count"], 2)
        self.assertEqual(y1["number_of_students"], 0)

    def test_apply_updates_only_the_matching_term(self):
        count, updated = planner.apply_stem_student_count(
            stem=self.stem, year=1, semester=1, number_of_students=45, user=self.user,
        )
        self.assertEqual(updated, 2)
        self.ca_edfo.refresh_from_db()
        self.ca_epsc.refresh_from_db()
        self.ca_edfo_y2.refresh_from_db()
        self.assertEqual(self.ca_edfo.number_of_students, 45)
        self.assertEqual(self.ca_epsc.number_of_students, 45)
        self.assertEqual(self.ca_edfo_y2.number_of_students, 0)  # different term untouched

        self.assertTrue(StemStudentCount.objects.filter(
            stem=self.stem, year=1, semester=1, number_of_students=45,
        ).exists())

        terms = planner.stem_student_number_terms(self.stem)
        y1 = next(t for t in terms if t["year"] == 1)
        self.assertEqual(y1["number_of_students"], 45)

    def test_saved_count_survives_when_courses_disagree(self):
        # Mismatched numbers on the underlying rows (e.g. edited by hand one
        # at a time) fall back to the last value saved through this feature,
        # rather than guessing from disagreeing rows.
        planner.apply_stem_student_count(
            stem=self.stem, year=1, semester=1, number_of_students=30, user=self.user,
        )
        self.ca_edfo.number_of_students = 99
        self.ca_edfo.save(update_fields=["number_of_students"])

        terms = planner.stem_student_number_terms(self.stem)
        y1 = next(t for t in terms if t["year"] == 1)
        self.assertEqual(y1["number_of_students"], 30)

    def test_nested_pool_alternatives_are_never_swept_in(self):
        from course_allocation.models import SelectionGroup

        pool_pc = ProgramCourse.objects.create(
            program=self.prog, course_code="OPT 111", course_name="Optional unit", year=1, semester=1,
        )
        pool_ca = CourseAllocation.objects.create(
            course_code="OPT 111-A", course_name="Optional unit", department=self.dept,
            origin_department=self.dept, program=self.prog, program_course=pool_pc,
            intake="normal", allocation_set=self.set1, is_elective=True, number_of_students=0,
        )
        pool = SelectionGroup.objects.create(name="Pick one", department=self.dept)
        pool.courses.add(pool_ca)
        pool.specialization_stems.add(self.stem)
        self.stem.courses.add(pool_ca)

        terms = planner.stem_student_number_terms(self.stem)
        y1 = next(t for t in terms if t["year"] == 1)
        self.assertEqual(y1["course_count"], 2)  # pool course excluded

        planner.apply_stem_student_count(
            stem=self.stem, year=1, semester=1, number_of_students=50, user=self.user,
        )
        pool_ca.refresh_from_db()
        self.assertEqual(pool_ca.number_of_students, 0)  # never touched


class StemStudentNumbersAjaxTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cod5", password="x")
        fac = Faculty.objects.create(name="Education")
        self.dept = Department.objects.create(name="Education", faculty=fac, leader=self.user)
        self.other_dept = Department.objects.create(name="Science", faculty=fac)
        self.prog = Program.objects.create(name="BEd Arts", department=self.dept)
        self.set1 = AllocationSet.objects.create(department=self.dept, name="Sem 1")
        self.cat = SpecializationCategory.objects.create(
            name="Teaching Subjects", department=self.dept, program=self.prog,
            year=1, semester=1, allocation_set=self.set1,
        )
        self.stem = SpecializationStem.objects.create(category=self.cat, name="English/Literature")
        self.pc = ProgramCourse.objects.create(
            program=self.prog, course_code="EDFO 111", course_name="Foundations", year=1, semester=1,
        )
        self.ca = CourseAllocation.objects.create(
            course_code="EDFO 111-A", course_name="Foundations", department=self.dept,
            origin_department=self.dept, program=self.prog, program_course=self.pc,
            intake="normal", allocation_set=self.set1, specialization_stem=self.stem,
            number_of_students=0,
        )
        self.stem.courses.add(self.ca)
        self.client.force_login(self.user)

    def post(self, data):
        return self.client.post(reverse("ajax_groups_electives"), data)

    def test_stem_student_numbers_lists_the_term(self):
        r = self.post({"action": "stem_student_numbers", "id": self.stem.id})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(len(body["terms"]), 1)
        self.assertEqual(body["terms"][0]["label"], "Year 1 Semester 1")

    def test_stem_student_numbers_errors_with_no_core_courses(self):
        bare_stem = SpecializationStem.objects.create(category=self.cat, name="History/CRE")
        r = self.post({"action": "stem_student_numbers", "id": bare_stem.id})
        self.assertEqual(r.status_code, 400)

    def test_save_stem_student_numbers_updates_the_allocation(self):
        r = self.post({
            "action": "save_stem_student_numbers", "id": self.stem.id,
            "terms": ["1:1:60"],
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "success")
        self.ca.refresh_from_db()
        self.assertEqual(self.ca.number_of_students, 60)
        self.assertTrue(StemStudentCount.objects.filter(stem=self.stem, year=1, semester=1, number_of_students=60).exists())

    def test_save_stem_student_numbers_rejects_a_stem_from_another_department(self):
        foreign_prog = Program.objects.create(name="BSc Physics", department=self.other_dept)
        foreign_cat = SpecializationCategory.objects.create(
            name="Physics Track", department=self.other_dept, program=foreign_prog, year=1, semester=1,
        )
        foreign_stem = SpecializationStem.objects.create(category=foreign_cat, name="Astrophysics")

        r = self.post({
            "action": "save_stem_student_numbers", "id": foreign_stem.id,
            "terms": ["1:1:20"],
        })
        self.assertEqual(r.status_code, 403)
