"""
allocation_reports/adapters.py
================================
The four allocation tables (main / odel / campus / resit) each have
slightly different schemas. This module normalises them into one common
row shape so `pdf_builder.py` and `signature.py` don't need to know which
scope they're dealing with:

    {
        "course_code": str,
        "course_name": str,
        "origin_department": str,
        "lecturer": str,
        "students": int,
        "program": str,
        "year": int | None,
        "semester": int | None,       # 1 or 2 — drives table splitting
        "intake": str,                # "Normal" or "Special" — drives table splitting
        "is_elective": bool,
        "stem": str | None,           # SpecializationStem name, if any
        "stem_category": str | None,  # the SpecializationCategory (choice-point)
                                       # this stem belongs to
        "selection_group": str | None,# SelectionGroup name for pick-one electives
        "merged_with": list[str],     # course codes of sibling allocations in the
                                       # same CombinedCourseGroup, for the Lecturer column
        "student_group": str | None,  # e.g. "A", "B" — splits a program/year/
                                       # semester/intake cohort into its groups;
                                       # None means shared across all groups
    }

Add a new scope by adding one entry to ADAPTERS — nothing else needs to change.
"""
from dataclasses import dataclass
from typing import Callable, Optional

from django.db.models import Exists, OuterRef, Q

from allocation_reports.models import AllocationPdfRun


def _lecturer_name(lecturer):
    if not lecturer:
        return "Unassigned"
    return getattr(lecturer, "display_name", None) or str(lecturer)


def _dept_name(dept):
    return dept.name if dept else "—"


@dataclass
class ScopeAdapter:
    scope: str
    label: str
    queryset_for_department: Callable
    row: Callable
    program_of: Callable
    year_of: Callable
    department_of: Optional[Callable] = None  # only needed when the model has no direct `department` FK
    # "Serviced courses" — rows this department actually teaches on behalf
    # of another department's program (origin_department = this department,
    # but the row's own `department`/program lives elsewhere). None for
    # scopes whose model has no origin_department concept (e.g. ODeL).
    serviced_queryset_for_department: Optional[Callable] = None
    serviced_row: Optional[Callable] = None


# ── MAIN ─────────────────────────────────────────────────────────────────────
def _main_qs(department):
    """
    NOTE on CombinedCourseGroup duplicates:
    The COD panel's "Create Combined Group" action links several
    CourseAllocation rows (e.g. multiple sections of the same course)
    into one CombinedCourseGroup for scheduling, but — unlike
    "Smart Combine" — it does NOT delete or merge the original rows.
    Left unfiltered, those rows all show up here as separate lines for
    what is really one merged offering, which reads as duplicates.

    We exclude any allocation that is a *secondary* member of a
    CombinedCourseGroup (i.e. linked via group.allocations but not
    that group's primary_allocation). The group's primary_allocation
    row still appears, and its student count is rolled up to the full
    combined total in _main_row() below, so no data is lost — only the
    redundant extra rows are hidden.

    NOTE on `department` vs `program__department`:
    `department` records who is administratively ALLOCATING the course
    (assigning it a lecturer), which is not always the department that
    owns the program's curriculum — e.g. a course can sit on the BSc
    Computer Science curriculum (program.department = Computer Science)
    but have been allocated with `department` pointing at whoever is
    actually teaching it (origin_department territory). Matching only
    `department` would then silently drop that course from Computer
    Science's own "BSc Computer Science" program/year listing even
    though every one of its courses belongs there. Matching EITHER
    `department` OR `program__department` means a department's report
    always shows every course under its own programs, however
    `department` happened to be set — mirroring the same fix already
    applied to `_apply_scope_filters` in timetable/analysis_reports.py.
    """
    from course_allocation.models import CourseAllocation, CombinedCourseGroup

    # primary_allocation is a nullable FK (on_delete=SET_NULL): if the row
    # that was the group's primary gets deleted (e.g. via the COD panel's
    # "Delete" / "bulk_delete_group_allocations" actions), Django nulls
    # primary_allocation but leaves the group's other member(s) still
    # linked via `allocations`. Without `primary_allocation__isnull=False`
    # here, every remaining member of such an orphaned group matches
    # "linked to a group but not equal to its primary" and gets excluded
    # as if it were a duplicate — even though there is no primary row left
    # to carry its data, so the course disappears from the PDF entirely
    # while still showing fine on the (unfiltered) COD dashboard listing.
    is_secondary_of_group = CombinedCourseGroup.objects.filter(
        allocations=OuterRef("pk"),
        primary_allocation__isnull=False,
    ).exclude(primary_allocation=OuterRef("pk"))

    return (
        CourseAllocation.objects
        .filter(Q(department=department) | Q(program__department=department))
        .annotate(_is_combined_secondary=Exists(is_secondary_of_group))
        .exclude(_is_combined_secondary=True)
        .select_related(
            "department", "origin_department", "lecturer", "program", "program_course",
            "specialization_stem", "specialization_stem__category",
            "selection_group", "student_group",
        )
        .prefetch_related("is_primary_of_combined_group__allocations")
        .order_by("program__name", "program_course__year", "course_code")
    )


def _main_row(a):
    # If this allocation is the primary of a CombinedCourseGroup, report
    # the group's full combined student count (covers the case where
    # "Create Combined Group" — not "Smart Combine" — was used, so the
    # primary row's own number_of_students was never updated), and record
    # which sibling course codes were merged into it so that's visible in
    # the Lecturer column instead of disappearing silently.
    students = a.number_of_students
    combined_group = a.is_primary_of_combined_group.all()[:1]
    combined_group = combined_group[0] if combined_group else None
    merged_with = []
    if combined_group is not None:
        siblings = [m for m in combined_group.allocations.all() if m.pk != a.pk]
        students = a.number_of_students + sum(m.number_of_students for m in siblings)
        merged_with = sorted({m.course_code for m in siblings})

    return {
        "course_code": a.course_code,
        "course_name": a.course_name,
        "origin_department": _dept_name(a.origin_department or a.department),
        "lecturer": _lecturer_name(a.lecturer),
        "students": students,
        "program": a.program.name if a.program else "—",
        "year": a.program_course.year if a.program_course_id else None,
        "semester": a.program_course.semester if a.program_course_id else None,
        "intake": a.get_intake_display() if a.intake else "Normal",
        "is_elective": bool(a.is_elective),
        "stem": a.specialization_stem.name if a.specialization_stem_id else None,
        # The choice-point this stem belongs to (e.g. "Year 3 Semester 1
        # Specialization"), so the PDF can show "students choose ONE stem
        # out of this category" as a combination rather than a bare tag.
        "stem_category": (
            a.specialization_stem.category.name
            if a.specialization_stem_id and a.specialization_stem.category_id
            else None
        ),
        # Pick-one-of elective grouping (SelectionGroup) — a different
        # "combination" concept from stems: any ONE course from this named
        # group satisfies the requirement, vs. a stem where ALL courses
        # in it are taken together.
        "selection_group": a.selection_group.name if a.selection_group_id else None,
        "merged_with": merged_with,
        # None means "shared across all groups" (electives / stem courses) —
        # the row is repeated under every group heading in the PDF rather
        # than being dropped or arbitrarily assigned to just one group.
        "student_group": a.student_group.letter if a.student_group_id else None,
    }


# ── MAIN — Serviced Courses ──────────────────────────────────────────────────
# A course can be allocated under one department (`department` — the program
# it's taken in) but actually be taught by a different department's lecturer
# (`origin_department`). e.g. COMS 101 sits on the BSc Computer Science
# curriculum (department = Computer Science) but is taught by a Humanities
# lecturer (origin_department = Humanities). Humanities never shows up as the
# "department" on that row, so without this section they'd have no way to
# see, on their own dashboard PDF, that they're servicing it for another
# department's program. Mirrors `_serviced_courses_qs` in
# timetable/analysis_reports.py.
def _main_serviced_qs(department):
    from course_allocation.models import CourseAllocation

    return (
        CourseAllocation.objects
        .filter(origin_department=department)
        .exclude(department=department)
        .select_related(
            "department", "origin_department", "lecturer", "program", "program_course",
        )
        .order_by("program__name", "program_course__year", "course_code")
    )


def _main_serviced_row(a):
    return {
        "course_code": a.course_code,
        "course_name": a.course_name,
        "allocating_department": _dept_name(a.department),
        "lecturer": _lecturer_name(a.lecturer),
        "students": a.number_of_students,
        "program": a.program.name if a.program else "—",
        "year": a.program_course.year if a.program_course_id else None,
        "semester": a.program_course.semester if a.program_course_id else None,
        "intake": a.get_intake_display() if a.intake else "Normal",
    }


# ── ODEL ─────────────────────────────────────────────────────────────────────
def _odel_qs(department):
    from odel_system.models import ODELCourseAllocation
    return (
        ODELCourseAllocation.objects
        .filter(program_course__program__department=department)
        .select_related("lecturer", "program_course", "program_course__program")
        .order_by("program_course__program__name", "program_course__year", "program_course__course_code")
    )


def _odel_row(a):
    return {
        "course_code": a.course_code,
        "course_name": a.course_name,
        "origin_department": _dept_name(a.department),
        "lecturer": _lecturer_name(a.lecturer),
        "students": a.number_of_students,
        "program": a.program.name if a.program else "—",
        "year": a.program_course.year if a.program_course_id else None,
        "semester": a.program_course.semester if a.program_course_id else None,
        "intake": "Normal",
        "is_elective": False,
        "stem": None,
        "stem_category": None,
        "selection_group": None,
        "merged_with": [],
        "student_group": None,
    }


# ── CAMPUS ───────────────────────────────────────────────────────────────────
def _campus_qs(department, campus=None):
    # See the note on `department` vs `program__department` in _main_qs above
    # — same fix applies here.
    from campuses_timetable.models import CampusCourseAllocation
    qs = (
        CampusCourseAllocation.objects
        .filter(Q(department=department) | Q(program__department=department))
        .select_related("department", "origin_department", "lecturer", "program", "campus")
        .order_by("program__name", "program_year", "course_code")
    )
    if campus is not None:
        qs = qs.filter(campus=campus)
    return qs


def _campus_serviced_qs(department, campus=None):
    """Serviced courses for the Campus scope — see _main_serviced_qs."""
    from campuses_timetable.models import CampusCourseAllocation
    qs = (
        CampusCourseAllocation.objects
        .filter(origin_department=department)
        .exclude(department=department)
        .select_related("department", "origin_department", "lecturer", "program", "campus")
        .order_by("program__name", "program_year", "course_code")
    )
    if campus is not None:
        qs = qs.filter(campus=campus)
    return qs


def _campus_serviced_row(a):
    return {
        "course_code": a.course_code,
        "course_name": a.course_name,
        "allocating_department": _dept_name(a.department),
        "lecturer": _lecturer_name(a.lecturer),
        "students": a.number_of_students,
        "program": a.program.name if a.program else "—",
        "year": a.program_year,
        "semester": a.allocation_semester,
        "intake": "Normal",
    }


def _campus_row(a):
    return {
        "course_code": a.course_code,
        "course_name": a.course_name,
        "origin_department": _dept_name(a.origin_department or a.department),
        "lecturer": _lecturer_name(a.lecturer),
        "students": a.number_of_students,
        "program": a.program.name if a.program else "—",
        "year": a.program_year,
        "semester": a.allocation_semester,
        "intake": "Normal",
        "is_elective": False,
        "stem": None,
        "stem_category": None,
        "selection_group": None,
        "merged_with": [],
        "student_group": None,
    }


# ── RESIT ────────────────────────────────────────────────────────────────────
def _resit_qs(department):
    # See the note on `department` vs `program__department` in _main_qs above
    # — same fix applies here.
    from resits_timetabling.models import ResitCourseAllocation
    return (
        ResitCourseAllocation.objects
        .filter(Q(department=department) | Q(program__department=department))
        .select_related("department", "origin_department", "lecturer", "program", "program_course")
        .order_by("program__name", "program_course__year", "course_code")
    )


def _resit_serviced_qs(department):
    """Serviced courses for the Resit scope — see _main_serviced_qs."""
    from resits_timetabling.models import ResitCourseAllocation
    return (
        ResitCourseAllocation.objects
        .filter(origin_department=department)
        .exclude(department=department)
        .select_related("department", "origin_department", "lecturer", "program", "program_course")
        .order_by("program__name", "program_course__year", "course_code")
    )


def _resit_serviced_row(a):
    return {
        "course_code": a.course_code,
        "course_name": a.course_name,
        "allocating_department": _dept_name(a.department),
        "lecturer": _lecturer_name(a.lecturer),
        "students": a.number_of_students,
        "program": a.program.name if a.program else "—",
        "year": a.program_course.year if a.program_course_id else None,
        "semester": a.program_course.semester if a.program_course_id else None,
        "intake": "Normal",
    }


def _resit_row(a):
    return {
        "course_code": a.course_code,
        "course_name": a.course_name,
        "origin_department": _dept_name(a.origin_department or a.department),
        "lecturer": _lecturer_name(a.lecturer),
        "students": a.number_of_students,
        "program": a.program.name if a.program else "—",
        "year": a.program_course.year if a.program_course_id else None,
        "semester": a.program_course.semester if a.program_course_id else None,
        "intake": "Normal",
        "is_elective": False,
        "stem": None,
        "stem_category": None,
        "selection_group": None,
        "merged_with": [],
        "student_group": None,
    }


ADAPTERS = {
    AllocationPdfRun.SCOPE_MAIN: ScopeAdapter(
        scope=AllocationPdfRun.SCOPE_MAIN, label="Main Allocation",
        queryset_for_department=_main_qs, row=_main_row,
        program_of=None, year_of=None,
        serviced_queryset_for_department=_main_serviced_qs, serviced_row=_main_serviced_row,
    ),
    AllocationPdfRun.SCOPE_ODEL: ScopeAdapter(
        scope=AllocationPdfRun.SCOPE_ODEL, label="ODeL Allocation",
        queryset_for_department=_odel_qs, row=_odel_row,
        program_of=None, year_of=None,
        # ODELCourseAllocation has no origin_department concept — it is
        # always scoped via program_course__program__department, so there
        # is no separate "serviced" case to surface.
    ),
    AllocationPdfRun.SCOPE_CAMPUS: ScopeAdapter(
        scope=AllocationPdfRun.SCOPE_CAMPUS, label="Campus Allocation",
        queryset_for_department=_campus_qs, row=_campus_row,
        program_of=None, year_of=None,
        serviced_queryset_for_department=_campus_serviced_qs, serviced_row=_campus_serviced_row,
    ),
    AllocationPdfRun.SCOPE_RESIT: ScopeAdapter(
        scope=AllocationPdfRun.SCOPE_RESIT, label="Resit Allocation",
        queryset_for_department=_resit_qs, row=_resit_row,
        program_of=None, year_of=None,
        serviced_queryset_for_department=_resit_serviced_qs, serviced_row=_resit_serviced_row,
    ),
}


def get_adapter(scope: str) -> ScopeAdapter:
    try:
        return ADAPTERS[scope]
    except KeyError:
        raise ValueError(f"Unknown allocation scope: {scope!r}")


def fetch_rows(scope: str, department, campus=None):
    """Return normalised rows for one department (+campus for scope='campus')."""
    adapter = get_adapter(scope)
    if scope == AllocationPdfRun.SCOPE_CAMPUS:
        qs = adapter.queryset_for_department(department, campus=campus)
    else:
        qs = adapter.queryset_for_department(department)
    return [adapter.row(a) for a in qs]


def fetch_serviced_rows(scope: str, department, campus=None):
    """
    Return normalised "Serviced Courses" rows for one department — courses
    this department teaches on behalf of another department's program
    (origin_department = this department, allocating department ≠ this
    department). Returns [] for scopes with no origin_department concept
    (currently ODeL).
    """
    adapter = get_adapter(scope)
    if adapter.serviced_queryset_for_department is None:
        return []
    if scope == AllocationPdfRun.SCOPE_CAMPUS:
        qs = adapter.serviced_queryset_for_department(department, campus=campus)
    else:
        qs = adapter.serviced_queryset_for_department(department)
    return [adapter.serviced_row(a) for a in qs]
