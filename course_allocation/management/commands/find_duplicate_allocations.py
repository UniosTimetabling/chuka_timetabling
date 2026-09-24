"""
course_allocation/management/commands/find_duplicate_allocations.py

Diagnostic (read-only) command: lists every (program_course, department)
combination that has more than one CourseAllocation row. This is the exact
condition that used to crash `add_courses_to_stem` / `add_courses_to_group`
with `get() returned more than one CourseAllocation`.

Since the Student Group feature, this is EXPECTED for courses that have
been split across groups (one row per group, e.g. "Group A" / "Group B",
plus optionally a leftover shared row). This command doesn't change
anything — it just prints what's going on so you can sanity-check a
specific stem/course before touching data.

Usage:
    python manage.py find_duplicate_allocations
    python manage.py find_duplicate_allocations --program-course-id 70
    python manage.py find_duplicate_allocations --course-code "BCOM 353-A"
"""
from django.core.management.base import BaseCommand
from django.db.models import Count

from course_allocation.models import CourseAllocation


class Command(BaseCommand):
    help = "List (program_course, department) pairs with more than one CourseAllocation row."

    def add_arguments(self, parser):
        parser.add_argument("--program-course-id", type=int, default=None)
        parser.add_argument("--course-code", type=str, default=None)

    def handle(self, *args, **options):
        qs = CourseAllocation.objects.all()
        if options["program_course_id"]:
            qs = qs.filter(program_course_id=options["program_course_id"])
        if options["course_code"]:
            qs = qs.filter(course_code__icontains=options["course_code"])

        dupes = (
            qs.values("program_course_id", "department_id")
            .annotate(n=Count("id"))
            .filter(n__gt=1)
            .order_by("-n")
        )

        if not dupes:
            self.stdout.write(self.style.SUCCESS("No duplicate (program_course, department) groups found."))
            return

        self.stdout.write(self.style.WARNING(f"Found {len(dupes)} program_course/department pairs with duplicates:\n"))

        for row in dupes:
            allocs = CourseAllocation.objects.filter(
                program_course_id=row["program_course_id"],
                department_id=row["department_id"],
            ).select_related("student_group", "specialization_stem", "selection_group").order_by("id")

            first = allocs.first()
            self.stdout.write(
                f"- {first.course_code} ({first.course_name}) "
                f"[program_course_id={row['program_course_id']}, department_id={row['department_id']}] "
                f"— {row['n']} rows:"
            )
            for ca in allocs:
                group_label = ca.student_group.name if ca.student_group_id else "SHARED (null)"
                stem_label = f", stem={ca.specialization_stem.name}" if ca.specialization_stem_id else ""
                sg_label = f", selection_group={ca.selection_group.name}" if ca.selection_group_id else ""
                self.stdout.write(
                    f"    id={ca.id:<6} student_group={group_label:<20} "
                    f"intake={ca.intake:<8} is_elective={ca.is_elective}{stem_label}{sg_label}"
                )
            self.stdout.write("")
