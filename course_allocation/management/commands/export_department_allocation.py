"""
course_allocation/management/commands/export_department_allocation.py
========================================================================

Exports a department's course allocation to a single JSON file, built the
way a Chairman of Department (COD) would actually want to read it:

  1. "courses"   - every CourseAllocation row (course, lecturer, group,
                   number of students, program, year/semester, status).
  2. "groups"    - one entry per Student Group, listing every course
                   allocated to that group, which lecturer(s) teach it,
                   and how many students are in it.
  3. "lecturers" - one entry per lecturer, listing every course they teach
                   in this department, how many groups/courses that is,
                   and the total number of students across all of them.
  4. "summary"   - department-wide totals (courses, students, lecturers,
                   groups, unassigned courses).

USAGE
-----
Drop this file at:
    course_allocation/management/commands/export_department_allocation.py

Then, from the project root (where manage.py lives):

    # Export the Education department (default)
    python manage.py export_department_allocation

    # Export a different department
    python manage.py export_department_allocation --department "Computer Science"

    # Choose the output path
    python manage.py export_department_allocation --department Education \
        --output exports/education_allocation.json

    # Only a specific program / year / semester / intake within the dept
    python manage.py export_department_allocation --department Education \
        --program "Bachelor of Education Arts" --year 2 --semester 1

    # Restrict to allocations already approved by the DVC (off by default —
    # by default EVERY allocation is exported regardless of DVC/submission status)
    python manage.py export_department_allocation --department Education --only-approved

    # See the department names available if you're not sure of the exact spelling
    python manage.py export_department_allocation --list-departments

The JSON is pretty-printed (indent=2) and UTF-8, safe to open in a browser,
VS Code, or feed into another script / spreadsheet.
"""
import json
import os
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from course_allocation.models import CourseAllocation
from department_management.models import Department


class Command(BaseCommand):
    help = (
        "Export one department's course allocation to JSON, including "
        "student groups (with lecturer + student counts) and a per-lecturer "
        "teaching-load breakdown."
    )

    # ------------------------------------------------------------------
    # CLI arguments
    # ------------------------------------------------------------------
    def add_arguments(self, parser):
        parser.add_argument(
            "--department",
            default="Education",
            help="Department name to export (default: 'Education'). "
                 "Case-insensitive, matched against department_management.Department.name.",
        )
        parser.add_argument(
            "--output",
            default=None,
            help="Output file path. Default: ./exports/<department>_allocation_<timestamp>.json",
        )
        parser.add_argument(
            "--program",
            default=None,
            help="Optional: restrict export to a single program name (partial, case-insensitive match).",
        )
        parser.add_argument("--year", type=int, default=None, help="Optional: restrict to a study year (1-6).")
        parser.add_argument(
            "--semester", type=int, default=None,
            help="Optional: restrict to a semester (1, 2 or 3).",
        )
        parser.add_argument(
            "--intake", choices=["normal", "special"], default=None,
            help="Optional: restrict to 'normal' or 'special' intake.",
        )
        parser.add_argument(
            "--only-approved",
            action="store_true",
            help="Restrict export to allocations approved by the DVC and not rejected. "
                 "Off by default — the export includes ALL allocations regardless of "
                 "DVC/submission status.",
        )
        parser.add_argument(
            "--list-departments",
            action="store_true",
            help="Just print every department name in the system and exit (no export).",
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _resolve_department(self, name):
        dept = Department.objects.filter(name__iexact=name).first()
        if dept:
            return dept
        # fall back to a partial, case-insensitive match so "education"
        # or "Dept of Education" style typos still work.
        candidates = Department.objects.filter(name__icontains=name)
        if candidates.count() == 1:
            return candidates.first()
        available = ", ".join(Department.objects.order_by("name").values_list("name", flat=True))
        if candidates.exists():
            names = ", ".join(candidates.values_list("name", flat=True))
            raise CommandError(
                f"Ambiguous department name '{name}'. Matches: {names}. "
                f"Use --department with the exact name."
            )
        raise CommandError(
            f"No department found matching '{name}'. Available departments: {available}"
        )

    def _lecturer_dict(self, lecturer):
        if not lecturer:
            return None
        return {
            "id": lecturer.id,
            "name": lecturer.display_name,
            "designation": lecturer.designation,
            "payroll_number": lecturer.payroll_number,
            "email": lecturer.email,
        }

    def _course_row(self, ca):
        return {
            "id": ca.id,
            "course_code": ca.course_code,
            "course_name": ca.course_name,
            "program": ca.program.name if ca.program else None,
            "year": ca.program_course.year if ca.program_course else None,
            "semester": ca.program_course.semester if ca.program_course else None,
            "intake": ca.intake,
            "is_elective": ca.is_elective,
            "is_evening_weekend": ca.is_evening_weekend,
            "student_group": ca.student_group.display_name if ca.student_group else None,
            "student_group_letter": ca.student_group.letter if ca.student_group else None,
            "number_of_students": ca.number_of_students,
            "lecturer": self._lecturer_dict(ca.lecturer),
            "status": ca.status_label(),
            "approved_by_dvc": ca.approved_by_dvc,
            "rejected_by_dvc": ca.rejected_by_dvc,
            "submitted_to_timetable": ca.submitted_to_tt,
        }

    def _print_diagnostics(self, department, options):
        """
        Run when the filtered queryset comes back empty. Explains WHY,
        instead of leaving the user to guess, by checking the department
        both as `department` and `origin_department`, and breaking the
        unfiltered set down by approval status.
        """
        self.stdout.write(self.style.NOTICE("\n--- Diagnostics ---"))

        as_department = CourseAllocation.objects.filter(department=department)
        as_origin = CourseAllocation.objects.filter(origin_department=department)

        self.stdout.write(
            f"Rows with department = '{department.name}' (no filters): {as_department.count()}"
        )
        self.stdout.write(
            f"Rows with origin_department = '{department.name}' (no filters): {as_origin.count()}"
        )

        if as_department.exists():
            approved = as_department.filter(approved_by_dvc=True, rejected_by_dvc=False).count()
            rejected = as_department.filter(rejected_by_dvc=True).count()
            pending = as_department.filter(approved_by_dvc=False, rejected_by_dvc=False).count()
            with_lecturer = as_department.exclude(lecturer__isnull=True).count()
            self.stdout.write(
                f"  approved_by_dvc=True & not rejected : {approved}\n"
                f"  rejected_by_dvc=True                : {rejected}\n"
                f"  pending (neither approved nor rejected): {pending}\n"
                f"  with a lecturer assigned            : {with_lecturer}"
            )
            self.stdout.write(self.style.NOTICE(
                "-> Rows exist for this department but were filtered out by "
                "--program/--year/--semester/--intake/--only-approved. Widen or drop those flags."
            ))
        elif as_origin.exists():
            self.stdout.write(self.style.NOTICE(
                "-> No rows have this as their CURRENT department, but some rows "
                "were ORIGINALLY offered by this department (origin_department). "
                "This department may only be a course *source*, not the owner of "
                "any current allocation, or the courses were re-assigned elsewhere."
            ))
        else:
            total_all = CourseAllocation.objects.count()
            self.stdout.write(self.style.NOTICE(
                f"-> No CourseAllocation rows reference this department at all "
                f"(as department or origin_department). Total CourseAllocation rows "
                f"in the whole system: {total_all}. Either allocations for this "
                f"department haven't been created yet, or check --department spelling "
                f"with --list-departments."
            ))
        self.stdout.write(self.style.NOTICE("--- End diagnostics ---\n"))

    # ------------------------------------------------------------------
    # main
    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        if options["list_departments"]:
            for name in Department.objects.order_by("name").values_list("name", flat=True):
                self.stdout.write(name)
            return

        department = self._resolve_department(options["department"])

        qs = (
            CourseAllocation.objects
            .filter(department=department)
            .select_related("lecturer", "program", "program_course", "student_group", "department")
        )

        if options["program"]:
            qs = qs.filter(program__name__icontains=options["program"])
        if options["year"]:
            qs = qs.filter(program_course__year=options["year"])
        if options["semester"]:
            qs = qs.filter(program_course__semester=options["semester"])
        if options["intake"]:
            qs = qs.filter(intake=options["intake"])
        if options["only_approved"]:
            qs = qs.filter(approved_by_dvc=True, rejected_by_dvc=False)

        qs = qs.order_by("program__name", "program_course__year", "program_course__semester", "course_code")
        allocations = list(qs)

        if not allocations:
            self.stdout.write(self.style.WARNING(
                f"No course allocations found for department '{department.name}' with the given filters."
            ))
            self._print_diagnostics(department, options)

        # ---- 1. courses -------------------------------------------------
        courses = [self._course_row(ca) for ca in allocations]

        # ---- 2. groups ----------------------------------------------------
        groups = {}
        shared_bucket = {
            "group": "(Shared / no specific group)",
            "letter": None,
            "program": None,
            "year": None,
            "semester": None,
            "intake": None,
            "courses": [],
            "lecturers": [],
            "total_students": 0,
        }
        for ca in allocations:
            row = self._course_row(ca)
            if ca.student_group:
                key = ca.student_group.id
                if key not in groups:
                    sg = ca.student_group
                    groups[key] = {
                        "group": sg.display_name,
                        "letter": sg.letter,
                        "program": sg.program.name,
                        "year": sg.year,
                        "semester": sg.semester,
                        "intake": sg.intake,
                        "courses": [],
                        "lecturers": [],
                        "total_students": 0,
                    }
                bucket = groups[key]
            else:
                bucket = shared_bucket

            bucket["courses"].append({
                "course_code": row["course_code"],
                "course_name": row["course_name"],
                "lecturer": row["lecturer"]["name"] if row["lecturer"] else "Unassigned",
                "number_of_students": row["number_of_students"],
            })
            if row["lecturer"] and row["lecturer"]["name"] not in bucket["lecturers"]:
                bucket["lecturers"].append(row["lecturer"]["name"])
            bucket["total_students"] = max(bucket["total_students"], row["number_of_students"])

        groups_list = list(groups.values())
        if shared_bucket["courses"]:
            groups_list.append(shared_bucket)

        # ---- 3. lecturers ---------------------------------------------
        lecturers = {}
        unassigned_courses = []
        for ca in allocations:
            row = self._course_row(ca)
            if not ca.lecturer:
                unassigned_courses.append(f"{row['course_code']} ({row['program']})")
                continue
            key = ca.lecturer.id
            if key not in lecturers:
                lecturers[key] = {
                    "lecturer": self._lecturer_dict(ca.lecturer),
                    "courses": [],
                    "number_of_courses": 0,
                    "total_students": 0,
                }
            lecturers[key]["courses"].append({
                "course_code": row["course_code"],
                "course_name": row["course_name"],
                "program": row["program"],
                "student_group": row["student_group"],
                "number_of_students": row["number_of_students"],
            })
            lecturers[key]["number_of_courses"] += 1
            lecturers[key]["total_students"] += row["number_of_students"]

        lecturers_list = sorted(
            lecturers.values(), key=lambda x: x["lecturer"]["name"].lower()
        )

        # ---- 4. summary -------------------------------------------------
        total_students = sum(c["number_of_students"] for c in courses)
        summary = {
            "total_courses": len(courses),
            "total_student_groups": len(groups_list),
            "total_lecturers_assigned": len(lecturers_list),
            "total_courses_unassigned": len(unassigned_courses),
            "total_students_enrolled": total_students,
        }

        payload = {
            "department": department.name,
            "faculty": department.faculty.name,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "filters": {
                "program": options["program"],
                "year": options["year"],
                "semester": options["semester"],
                "intake": options["intake"],
                "only_approved": options["only_approved"],
            },
            "summary": summary,
            "courses": courses,
            "groups": groups_list,
            "lecturers": lecturers_list,
            "unassigned_courses": unassigned_courses,
        }

        # ---- write file ---------------------------------------------------
        output_path = options["output"]
        if not output_path:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = department.name.lower().replace(" ", "_")
            exports_dir = os.path.join(os.getcwd(), "exports")
            os.makedirs(exports_dir, exist_ok=True)
            output_path = os.path.join(exports_dir, f"{safe_name}_allocation_{stamp}.json")
        else:
            out_dir = os.path.dirname(output_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        self.stdout.write(self.style.SUCCESS(f"Exported {len(courses)} course allocation(s) for "
                                              f"'{department.name}' -> {output_path}"))
        self.stdout.write(
            f"  Groups: {summary['total_student_groups']}  |  "
            f"Lecturers: {summary['total_lecturers_assigned']}  |  "
            f"Students: {summary['total_students_enrolled']}  |  "
            f"Unassigned courses: {summary['total_courses_unassigned']}"
        )