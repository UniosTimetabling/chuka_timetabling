"""Pure unit tests (no DB): python -m unittest course_allocation.tests_section_utils"""
import unittest
from course_allocation.section_utils import (
    parse_course_code, section_code, with_group_letter, max_sections_for_code, distribute_students,
)


class SectionUtilsTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_course_code("COSC 103"), ("COSC 103", None, None))
        self.assertEqual(parse_course_code("COSC 103-A"), ("COSC 103", "A", None))
        self.assertEqual(parse_course_code("COSC 103-A2"), ("COSC 103", "A", 2))
        self.assertEqual(parse_course_code("COSC 103-2"), ("COSC 103", None, 2))
        self.assertEqual(parse_course_code("COSC 471(C)"), ("COSC 471", "C", None))

    def test_section_code(self):
        self.assertEqual(section_code("COSC 103-A", 1), "COSC 103-A")
        self.assertEqual(section_code("COSC 103-A", 2), "COSC 103-A2")
        self.assertEqual(section_code("COSC 103", 3), "COSC 103-3")

    def test_relettering_keeps_section(self):
        self.assertEqual(with_group_letter("COSC 103-B2", "A"), "COSC 103-A2")
        self.assertEqual(with_group_letter("COSC 103", "A"), "COSC 103-A")

    def test_caps_and_split(self):
        self.assertEqual(max_sections_for_code("COSC 103-DA"), 9)
        self.assertEqual(distribute_students(301, 3), [101, 100, 100])


if __name__ == "__main__":
    unittest.main()
