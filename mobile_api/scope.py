"""
mobile_api/scope.py
=====================
Turns what the mobile app sends in (a student's registration number, a
lecturer's name, or a previously-issued opaque user id) into a "scope"
dict the rest of mobile_api can query against — and back again.

Deliberately reuses export_import.student_reg_lookup rather than
reimplementing registration-number parsing, so the mobile app and the
existing "download my timetable" PDF box always agree on what a given
reg number resolves to.

A student scope looks like:
    {"role": "student", "department_id": 3, "program_id": 12, "year": 2}
A lecturer scope looks like:
    {"role": "lecturer", "lecturer_id": 45}

The opaque, stable user id format ("stu:<dept>:<program>:<year>",
"lec:<id>") is what the mobile app stores and sends back on every later
request (version checks, full timetable, QR share) — see parse_user_id.
"""
from export_import.student_reg_lookup import (
    parse_registration_number,
    resolve_program_by_code,
    year_of_study_from_intake,
)


class ScopeError(Exception):
    """Carries an HTTP status + user-facing message for a clean 4xx JSON reply.

    `extra` is merged straight into the JSON error body — used by the
    "unknown program code" case below to hand the mobile app a picker
    (status 300, mirroring the existing ambiguous-lecturer-name reply)
    instead of a dead-end 404.
    """

    def __init__(self, status, message, extra=None):
        self.status = status
        self.message = message
        self.extra = extra or {}
        super().__init__(message)


# ─────────────────────────────────────────────────────────────
#  Registration number / name -> scope (used at login)
# ─────────────────────────────────────────────────────────────

def resolve_student_scope(reg_no, program_id=None, department_id=None):
    """'EB1/66791/23' -> full student scope dict, or raises ScopeError.

    `program_id` / `department_id` are the fallback path: if the program
    code embedded in `reg_no` doesn't match anything (typo, a program
    that was renamed, a code the registrar never entered), we don't just
    fail — we raise a 300 and let the student narrow it down themselves,
    in two steps, so they're never shown every program in the university
    mixed together in one flat list:

      1. No program_id and no department_id yet -> 300 with
         `needsDepartmentSelection` and the list of departments. The
         mobile app shows that list, the student taps theirs, and calls
         back in with the same `reg_no` plus `department_id` set.
      2. department_id set (program_id still not) -> 300 with
         `needsProgramSelection` and *that department's* programs only.
         The student taps theirs and calls back in with `program_id` set.
      3. program_id set -> we trust it and skip code resolution entirely.

    The year isn't re-asked for at any step, since a reg number's intake
    segment is independent of its program-code segment and was already
    resolved successfully on the very first call.
    """
    parsed = parse_registration_number(reg_no)
    if not parsed:
        raise ScopeError(
            400,
            "That doesn't look like a registration number. Expected format: "
            "PROGRAMCODE/ADMISSIONNO/INTAKEYEAR, e.g. EB1/66791/23.",
        )
    program_code, intake_year = parsed

    year = year_of_study_from_intake(intake_year)
    if year < 1 or year > 8:
        raise ScopeError(
            404,
            "That registration number doesn't map to a current year of study — "
            "please check the intake year at the end of it.",
        )

    if program_id is not None:
        from program_management.models import Program

        program = Program.objects.filter(id=program_id).select_related("department").first()
        if not program:
            raise ScopeError(404, "Unknown program.")
    elif department_id is not None:
        from department_management.models import Department
        from program_management.models import Program

        department = Department.objects.filter(id=department_id).first()
        if not department:
            raise ScopeError(404, "Unknown department.")

        programs = list(Program.objects.filter(department_id=department_id).order_by("name"))
        if not programs:
            raise ScopeError(
                404,
                f"'{department.name}' doesn't have any programs set up yet — "
                "please pick a different department.",
            )

        raise ScopeError(
            300,
            f"Now pick your program within {department.name}.",
            extra={
                "needsProgramSelection": True,
                "year": year,
                "programs": [{"id": p.id, "name": p.name} for p in programs],
            },
        )
    else:
        program = resolve_program_by_code(program_code)
        if not program:
            from department_management.models import Department

            raise ScopeError(
                300,
                f"Couldn't recognise the program code '{program_code}' in that "
                "registration number — please pick your department below.",
                extra={
                    "needsDepartmentSelection": True,
                    "year": year,
                    "departments": [
                        {"id": d.id, "name": d.name}
                        for d in Department.objects.filter(programs__isnull=False)
                        .distinct()
                        .order_by("name")
                    ],
                },
            )

    from program_management.models import ProgramCourse
    if not ProgramCourse.objects.filter(program_id=program.id, year=year).exists():
        raise ScopeError(404, "No curriculum found for that program/year yet.")

    return {
        "role": "student",
        "department_id": program.department_id,
        "program_id": program.id,
        "year": year,
        "reg_no": reg_no.strip().upper(),
        "program_name": program.name,
    }


def find_lecturer_matches(name):
    """Returns a list of Lecturer objects matching `name`: an exact
    case-insensitive match (list of 1) if one exists, otherwise every
    partial match (may be empty, one, or several — caller decides what
    to do with more than one)."""
    from lecturer_portal.models import Lecturer

    name = (name or "").strip()
    if not name:
        return []

    exact = Lecturer.objects.filter(name__iexact=name).first()
    if exact:
        return [exact]

    return list(Lecturer.objects.filter(name__icontains=name).order_by("name")[:8])


# ─────────────────────────────────────────────────────────────
#  Opaque user id <-> scope (used on every later request)
# ─────────────────────────────────────────────────────────────

def user_id_for_student(scope):
    return f"stu:{scope['department_id']}:{scope['program_id']}:{scope['year']}"


def user_id_for_lecturer(lecturer_id):
    return f"lec:{lecturer_id}"


def parse_user_id(user_id, role):
    """Reverses user_id_for_* back into a scope dict for querying."""
    if not user_id or not role:
        raise ScopeError(400, "Missing userId or role.")

    if role == "student":
        try:
            _, dept_id, program_id, year = user_id.split(":")
            return {
                "role": "student",
                "department_id": int(dept_id),
                "program_id": int(program_id),
                "year": int(year),
            }
        except (ValueError, TypeError):
            raise ScopeError(400, "Invalid student identifier.")

    if role == "lecturer":
        try:
            _, lecturer_id = user_id.split(":")
            return {"role": "lecturer", "lecturer_id": int(lecturer_id)}
        except (ValueError, TypeError):
            raise ScopeError(400, "Invalid lecturer identifier.")

    raise ScopeError(400, f"Unknown role '{role}'.")


def owner_name_for_scope(scope):
    """Human-readable label for the timetable's owner. For a student this
    is the cohort label (there's no individual student record in this
    system — see the module docstring in timetable_builder.py) rather
    than a personal name."""
    if scope["role"] == "student":
        from program_management.models import Program

        program = Program.objects.filter(id=scope["program_id"]).first()
        program_name = program.name if program else "Unknown Program"
        return f"{program_name} — Year {scope['year']}"

    if scope["role"] == "lecturer":
        from lecturer_portal.models import Lecturer

        lecturer = Lecturer.objects.filter(id=scope["lecturer_id"]).first()
        return lecturer.name if lecturer else "Unknown Lecturer"

    return "Unknown"
