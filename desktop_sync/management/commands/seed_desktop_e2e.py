import datetime as dt

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError

from core.rbac import Role
from course_allocation.models import CourseAllocation, LabAllocation
from department_management.models import Department
from export_import.models import PDFDocument
from faculty_management.models import Faculty
from lecturer_portal.models import Lecturer
from program_management.models import Program, ProgramCourse
from room_management.models import LabVenue, Venue
from timetable.models import ExamTimetable, LabTimetable, Timetable


class Command(BaseCommand):
    help = "Seed a small timetable + a Timetable Admin (ttadmin / pw-12345) for desktop-app QA. Refuses to run outside desktop_sync.e2e_settings."

    def handle(self, *args, **opts):
        if not getattr(settings, "DESKTOP_E2E", False):
            raise CommandError("Refusing to seed: run with DJANGO_SETTINGS_MODULE=desktop_sync.e2e_settings (throwaway database only).")
        if Timetable.objects.exists() or User.objects.filter(username="ttadmin").exists():
            raise CommandError("This database already has data. Delete the e2e SQLite file and migrate again.")

        T = dt.time
        dept = Department.objects.create(name="CS", faculty=Faculty.objects.create(name="Science"))
        progs = [Program.objects.create(name=f"BSc P{i}", department=dept) for i in range(3)]
        lecs = [Lecturer.objects.create(payroll_number=f"P{i}", name=f"Dr {i}", email=f"l{i}@example.com", designation="Lecturer") for i in range(3)]
        venues = [Venue.objects.create(code=f"LH{i}") for i in range(3)]
        allocs = []
        for i in range(6):
            pc = ProgramCourse.objects.create(program=progs[i % 3], course_code=f"COSC{100 + i}", course_name=f"Course {i}")
            allocs.append(CourseAllocation.objects.create(course_code=pc.course_code, course_name=pc.course_name, department=dept,
                                                          program=progs[i % 3], program_course=pc, lecturer=lecs[i % 3]))
        days = ["Monday", "Tuesday", "Wednesday"]
        for i, a in enumerate(allocs):
            Timetable.objects.create(course_allocation=a, venue=venues[i % 3], day=days[i % 3], start_time=T(8), end_time=T(10))
        for i, a in enumerate(allocs[:3]):
            ExamTimetable.objects.create(course_allocation=a, venue=venues[i], day=days[i], date=dt.date(2030, 1, 7 + i), start_time=T(8), end_time=T(11))
        lab_venues = [LabVenue.objects.create(code="LAB1"), LabVenue.objects.create(code="LAB2")]
        lab = LabAllocation.objects.create(program_course=allocs[0].program_course, lecturer=lecs[0])
        lab.venues.add(*lab_venues)
        LabTimetable.objects.create(lab_allocation=lab, lab_venue=lab_venues[0], day="Thursday", start_time=T(14), end_time=T(17))

        PDFDocument.objects.create(
            title="2030/2031 1st Semester Regular Timetable",
            document_type="REGULAR",
            academic_year="2030/2031",
            semester="1",
            status="PUBLISHED",
            pdf_file=SimpleUploadedFile("regular.pdf", b"%PDF-1.4 seeded e2e regular timetable\n%%EOF", content_type="application/pdf"),
        )

        user = User.objects.create_user("ttadmin", password="pw-12345")
        user.groups.add(Group.objects.get_or_create(name=Role.TIMETABLE_ADMIN)[0])
        self.stdout.write(self.style.SUCCESS("Seeded 6 regular, 3 exam, 1 lab entry, 1 published PDF. Sign in as ttadmin / pw-12345."))
