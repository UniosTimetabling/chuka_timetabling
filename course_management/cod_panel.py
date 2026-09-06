from core.rbac import allowed_roles, Role
# =======================
# Standard Library Imports
# =======================
import csv
import datetime
import difflib
import json
import logging
import random
import re
import string
from collections import defaultdict
from typing import Optional, Tuple
from weasyprint import HTML
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Count, F
from django.http import (
    HttpResponse,
    JsonResponse,
    HttpResponseBadRequest,
)
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.views.decorators.http import require_POST, require_http_methods
from faculty_management.models import Faculty
from department_management.models import Department
from lecturer_portal.models import Lecturer
from room_management.models import LabVenue
from course_allocation.models import (
    CourseAllocation, LabAllocation, SubmissionControl,
    LecturerCourseMapping, ProgramEnrollment, SelectionGroup,
    CombinedCourseGroup, SpecializationCategory, SpecialIntakeGroup,
    StudentGroup, GroupingTemplate, GroupingTemplateGroup,
    GroupingTemplateCourse, CourseCombinationTemplate,
    CourseCombinationTemplateProgram,
)
from program_management.models import Program, ProgramCourse
from core.group_required import group_required
from special_requests.services import (
    create_special_request, carry_forward_special_requests,
    get_active_special_request_for_allocation,
    get_active_special_requests_for_allocation, update_special_request,
)
from special_requests.models import SpecialRequest
from django.contrib.contenttypes.models import ContentType

logger = logging.getLogger(__name__)




# -----------------------
# Helper functions
# -----------------------
def generate_unique_payroll():
    """Generate unique payroll number."""
    for _ in range(10):
        code = "P" + "".join(random.choices(string.digits, k=6))
        if not Lecturer.objects.filter(payroll_number=code).exists():
            return code
    return "P" + str(random.randint(1000000, 9999999))


def get_control(department=None):
    """
    Return the SubmissionControl instance for the given department.
    Handles duplicate records by cleaning them up.
    """
    from django.core.exceptions import MultipleObjectsReturned
    
    if not department:
        # If no department, return None or first available
        return SubmissionControl.objects.first()
    
    try:
        # Try to get or create
        control, created = SubmissionControl.objects.get_or_create(department=department)
        return control
    except MultipleObjectsReturned:
        # If duplicates exist, get the first one and clean up
        logger.warning(f"Multiple SubmissionControl records found for department {department.id}. Cleaning up...")
        
        # Get all records for this department
        records = SubmissionControl.objects.filter(department=department).order_by('id')
        control = records.first()
        
        # Delete all but the first record
        records.exclude(id=control.id).delete()
        
        logger.info(f"Cleaned up duplicate SubmissionControl records for department {department.id}")
        return control


from program_management.code_utils import normalize_code, canonical_course_key as _canonical_course_key


def letters_to_index(letters: str) -> int:
    """
    Excel-style column letters -> 1-based index. A=1, B=2, ..., Z=26,
    AA=27, AB=28, ..., AZ=52, BA=53, ... (bijective base-26).
    """
    letters = letters.upper()
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def index_to_letters(n: int) -> str:
    """
    1-based index -> Excel-style column letters. Inverse of
    letters_to_index: 1='A', 26='Z', 27='AA', 28='AB', ...
    """
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def strip_group_suffix(code: str) -> Tuple[str, Optional[str]]:
    # Handles all known suffix formats:
    #   COSC 471-A   COSC 471/A   COSC 471_A   COSC 471 A   COSC471A
    #   COSC 471(C)  COSC 471(C)  COSC471(C)                                              ← added
    #   COSC 471-AA  COSC 471/AB  COSC471(AC)                                             ← Excel-style multi-letter groups
    code = code.strip()
    # Try parenthesis form first: COSC 471(C) or COSC471(AC)
    m = re.match(r"^(.*?\d+)\s*\(\s*([A-Za-z]+)\s*\)\s*$", code, re.I)
    if m:
        base = m.group(1).strip()
        letter = m.group(2).upper()
        if re.search(r"\d", base):
            return base, letter
    # Then try the standard separator forms
    m = re.match(r"^(.*?\d+)\s*[-/_]?\s*([A-Za-z]+)\s*$", code, re.I)
    if m:
        base = m.group(1).strip()
        letter = m.group(2).upper()
        if re.search(r"\d", base):
            return base, letter
    return code, None


def append_group(code: str, group_letter: str) -> str:
    code = code.strip()
    return f"{code}-{group_letter.upper()}"


def _lettered_code_for_group(code: str, group_letter: str) -> str:
    """
    Return `code` rewritten with the given group's letter suffix, e.g.
    "EDFO 111" + "A" -> "EDFO 111-A", "EDFO 111-B" + "A" -> "EDFO 111-A".
    Always strips any existing suffix first so a course tagged to the wrong
    group's letter (or created before this convention existed) gets
    corrected rather than double-suffixed.
    """
    base, _ = strip_group_suffix(code)
    return append_group(base, group_letter)


def _alloc_row_payload(alloc, dept=None) -> dict:
    """
    Serialize a CourseAllocation into exactly the fields the frontend's
    buildAllocRow()/updateAllocRowDOM() expect, so any backend action that
    creates, clones, or retags an allocation can hand the row straight back
    to the browser and let it upsert the DOM in place -- instead of the
    caller falling back to a full page reload because it didn't have the
    right shape of data to build/patch the row itself.
    """
    dept_id = dept.id if dept else None
    return {
        "id": alloc.id,
        "course_code": alloc.course_code,
        "course_name": alloc.course_name,
        "program_id": alloc.program_id,
        "program_name": alloc.program.name if alloc.program else "",
        "program_course_id": alloc.program_course_id,
        "year": alloc.program_course.year if alloc.program_course_id else None,
        "origin_dept_name": alloc.origin_department.name if alloc.origin_department else "",
        "number_of_students": alloc.number_of_students,
        "lecturer_display": alloc.lecturer.display_name if alloc.lecturer else "",
        "is_cross_dept": bool(
            dept_id and alloc.program_id and alloc.program.department_id
            and alloc.program.department_id != dept_id
        ),
    }


def _used_group_letters(base: str, program_course_id, program_id) -> set:
    """
    Every group letter that's already "taken" for a given base course code,
    combining two separate things the app cares about:

      1. Siblings under the SAME program_course (the curriculum entry) —
         this is what "groups of a course" semantically means.
      2. Any OTHER allocation under the SAME program whose course_code
         matches this base + a letter suffix — even if it hangs off a
         different (e.g. duplicate/legacy) ProgramCourse row for the same
         program. This mirrors the (program, course_code) uniqueness that
         create_allocation's own duplicate check enforces, so a newly
         picked letter can never collide with something save/edit will
         reject a moment later.

    An allocation with no letter suffix at all counts as occupying "A"
    (matches the convention used by create_allocation's own group check).
    """
    qs = CourseAllocation.objects.filter(program_course_id=program_course_id)
    if program_id:
        qs = qs | CourseAllocation.objects.filter(
            program_id=program_id,
            course_code__iregex=rf"^{re.escape(base)}(?:[-/_]?\s*[A-Za-z]+|\s*\([A-Za-z]+\))?$",
        )
    return {
        (strip_group_suffix(a.course_code)[1] or "A")
        for a in qs.distinct()
    }


def _pick_canonical_duplicate(matches):
    """
    Given 2+ ProgramCourse rows that all match the same course code for a
    program (whether they're literal-duplicate DB rows for the same
    curriculum slot -- e.g. 'BCOM112' and 'BCOM 112' saved as separate
    rows because course_code had no DB-level normalization at write time
    -- or genuinely distinct curriculum rows sharing a code, such as the
    same course listed under two different years by a data-entry
    mistake), deterministically pick the one to link a new allocation to
    when nothing else (an explicit choice, a year/semester hint from the
    form) narrows it down further.

    Prefers a row whose course_code is already in the canonical display
    form; falls back to the lowest id (the oldest/first row for that
    program) so repeated calls -- and the offline cleanup command --
    converge on the same keeper and don't bounce FK references between
    rows on every request.
    """
    well_formatted = [pc for pc in matches if pc.course_code == normalize_code(pc.course_code)]
    pool = well_formatted or matches
    return min(pool, key=lambda pc: pc.id)


def _loose_course_prefix_key(code: str) -> str:
    """
    Last-resort, very forgiving canonical key: just the leading LETTER-run
    + DIGIT-run of a course code, with anything after that (a group
    suffix of any shape/length, stray punctuation, extra words) thrown
    away. `strip_group_suffix` deliberately only recognizes a SINGLE
    trailing A-Z letter as a group suffix (that's the one legitimate
    format), so a code typed/produced with a malformed or multi-letter
    "group" -- e.g. 'COMS 101-GB' -- sails straight through it unstripped
    and then fails to canonical-match the 'COMS 101' curriculum entry in
    resolve_program_course()'s exact-match tier, even though it's
    obviously the same course. This key is used only as a final fallback
    tier there, never for storage/display and never in place of the
    single-letter group convention itself.
    """
    if not code:
        return ""
    m = re.match(r"^\s*([A-Za-z]+)\s*(\d+)", code)
    if not m:
        return ""
    return f"{m.group(1).upper()}{m.group(2)}"


def resolve_program_course(program, course_code, year_hint=None, semester_hint=None):
    """
    Auto-resolve the ProgramCourse (curriculum entry) that `course_code`
    belongs to for `program`, WITHOUT requiring the caller to supply a
    program_course_id. The COD panel form has no explicit "curriculum
    course" selector — course_code is typed freely and often carries a
    group suffix (e.g. 'COSC 345(C)', 'COSC345-C', 'COSC_345_C',
    'COSC 345 C') that must be stripped before comparing against the
    base curriculum entry ('COSC 345').

    `year_hint`/`semester_hint` are the year/semester the COD actually
    picked on the allocation form itself (program_year / program_semester
    POST fields). They're optional, but when supplied they let us break
    ties for course codes that legitimately (or erroneously, via bad
    curriculum data) exist under more than one (year, semester) slot --
    instead of always forcing a "which one did you mean?" popup, we can
    trust the year/semester the user already chose on the form.

    Returns a tuple (program_course_or_None, suggestions).
    - If the code matches one or more curriculum entries for this program
      (exactly, or once group suffixes are stripped), it is auto-linked
      and returned directly with `suggestions` as []. When more than one
      row matches, the form's year/semester hint breaks the tie if it
      can; otherwise a single deterministic row is picked (see
      `_pick_canonical_duplicate`) rather than prompting the COD --
      an exact code match within this program's own curriculum is never
      ambiguous enough to justify a popup.
    - Only when NOTHING matches exactly is program_course None, with
      `suggestions` listing the closest curriculum entries (typo-level
      near matches) for the caller to offer a choice or an "add to
      curriculum" option.
    """
    if not program or not course_code:
        return None, []

    def _norm_hint(v):
        # POST values arrive as strings ('1'); ProgramCourse.year/semester
        # may be ints or strings depending on the field -- compare as
        # strings so '1' == 1 works either way, but leave None alone.
        return None if v in (None, "") else str(v)

    year_hint = _norm_hint(year_hint)
    semester_hint = _norm_hint(semester_hint)

    def _by_hint(matches):
        """Narrow `matches` to the ones agreeing with year/semester hints,
        if hints were supplied and doing so actually narrows the field to
        exactly one row. Never used to broaden or to produce zero results."""
        if not matches or (year_hint is None and semester_hint is None):
            return matches
        narrowed = [
            pc for pc in matches
            if (year_hint is None or str(pc.year) == year_hint)
            and (semester_hint is None or str(pc.semester) == semester_hint)
        ]
        return narrowed if len(narrowed) == 1 else matches

    base_code, _ = strip_group_suffix(course_code)
    target_key = _canonical_course_key(base_code)
    if not target_key:
        return None, []

    program_courses = list(ProgramCourse.objects.filter(program=program))
    if not program_courses:
        return None, []

    def _serialize(pc):
        return {
            "id": pc.id,
            "course_code": pc.course_code,
            "course_name": pc.course_name,
            "year": pc.year,
            "semester": pc.semester,
            "unit_type": pc.unit_type,
            "is_elective_unit": pc.is_elective_type,
        }

    # 1) Literal match FIRST -- compare the raw typed code (canonicalized,
    #    but NOT group-suffix-stripped) directly against each curriculum
    #    course code as stored. This must run before the group-stripped
    #    comparison below, because some curricula legitimately contain
    #    separate rows that only differ by a trailing letter (e.g.
    #    'COSC 223' and 'COSC 223 A' as two distinct curriculum entries,
    #    not a base course + its group). Stripping suffixes on both sides
    #    before checking for an exact hit would collapse those two
    #    distinct rows onto the same key and make a perfectly
    #    unambiguous match ('COSC 223' -> 'COSC 223') look ambiguous.
    literal_key = _canonical_course_key(course_code)
    literal_matches = [
        pc for pc in program_courses
        if _canonical_course_key(pc.course_code) == literal_key
    ]
    if len(literal_matches) == 1:
        return literal_matches[0], []
    if len(literal_matches) > 1:
        # Any code typed that lands an exact (canonical) hit against this
        # program's own curriculum should just link and save -- the COD
        # already sees this course code in Program Courses for this
        # program, so a "which one did you mean?" popup for something
        # that unambiguously matches is just friction. Prefer the
        # year/semester the form already picked; otherwise fall back to
        # a single deterministic choice (see _pick_canonical_duplicate)
        # so repeated saves always land on the same row.
        hinted = _by_hint(literal_matches)
        if len(hinted) == 1:
            return hinted[0], []
        return _pick_canonical_duplicate(literal_matches), []

    # 2) Exact match, once both sides are group-stripped and canonicalized.
    #    This is the fallback for codes typed WITH a group suffix that
    #    isn't itself a separate curriculum row (e.g. typing 'COSC 471-B'
    #    when the curriculum only has a base 'COSC 471' entry).
    exact_matches = [
        pc for pc in program_courses
        if _canonical_course_key(strip_group_suffix(pc.course_code)[0]) == target_key
    ]
    if len(exact_matches) == 1:
        return exact_matches[0], []
    if len(exact_matches) > 1:
        # Same reasoning as the literal-match branch above: any exact
        # curriculum hit for this program links and saves automatically.
        # Try the form's year/semester hint first; if that doesn't land
        # on exactly one row, fall back to a single deterministic pick
        # (prefers the canonically-formatted row, else the oldest id)
        # rather than ever surfacing the picker for something that's
        # already an exact code match within this program.
        hinted = _by_hint(exact_matches)
        if len(hinted) == 1:
            return hinted[0], []
        return _pick_canonical_duplicate(exact_matches), []

    # 3) No exact match -- before giving up, try one last very forgiving
    #    tier: match on just the leading LETTERS+DIGITS, ignoring whatever
    #    comes after (any group-suffix shape/length, stray punctuation,
    #    etc.). This is what actually rescues codes like 'COMS 101-GB'
    #    (a malformed/multi-letter group suffix that strip_group_suffix
    #    intentionally leaves untouched) so they still auto-link to the
    #    obvious 'COMS 101' curriculum entry instead of forcing the COD
    #    through the "couldn't confidently match" picker.
    loose_key = _loose_course_prefix_key(course_code)
    if loose_key:
        loose_matches = [
            pc for pc in program_courses
            if _loose_course_prefix_key(pc.course_code) == loose_key
        ]
        if len(loose_matches) == 1:
            return loose_matches[0], []
        if len(loose_matches) > 1:
            hinted = _by_hint(loose_matches)
            if len(hinted) == 1:
                return hinted[0], []
            return _pick_canonical_duplicate(loose_matches), []

    # 4) Still nothing -- surface close matches (typos, unusual
    #    separators, near-duplicates) so the user can map to the right
    #    one instead of accidentally creating a duplicate curriculum entry.
    key_to_pc = {
        _canonical_course_key(strip_group_suffix(pc.course_code)[0]): pc
        for pc in program_courses
    }
    close_keys = difflib.get_close_matches(target_key, key_to_pc.keys(), n=5, cutoff=0.6)
    return None, [_serialize(key_to_pc[k]) for k in close_keys]


def detect_user_department(user: User) -> Optional[Department]:
    """
    Try several heuristics to find the department associated with the logged-in user.
    Priority: OrgRole.department (authoritative, works for COD Admin too) ->
    Department leader -> Lecturer profile dept -> OrgRole title (legacy).
    """
    org = getattr(user, "org_role", None)
    if org and org.department_id:
        return org.department

    try:
        dept = Department.objects.filter(leader=user).first()
        if dept:
            return dept
    except Exception:
        pass

    try:
        lect = Lecturer.objects.filter(user=user).first()
        if lect and lect.department:
            return lect.department
    except Exception:
        pass

    try:
        lect = Lecturer.objects.filter(email__iexact=(user.email or "")).first()
        if lect and lect.department:
            return lect.department
    except Exception:
        pass

    try:
        org = getattr(user, "org_role", None)
        if org and "COD" in org.title.upper():
            parts = org.title.split("-", 1)
            if len(parts) > 1:
                dept_name = parts[1].strip()
                return Department.objects.filter(name__icontains=dept_name).first()
    except Exception:
        pass

    return None


# ---------------------------------------------------------------
# SECURITY FIX #2 – Department ownership check
# Always verify that the department returned by detect_user_department
# actually belongs to the requesting user before operating on it.
# ---------------------------------------------------------------
def _assert_owns_department(request, dept: Optional[Department]) -> Optional[JsonResponse]:
    """
    Returns an error JsonResponse if the user does not own `dept`, else None.
    Centralises the "no department" guard so every action benefits from it.
    """
    if dept is None:
        return JsonResponse(
            {"status": "error", "message": "No department associated with your account."},
            status=403,
        )
    return None


class RejectedCourseService:
    """Service to handle rejected course actions."""

    @staticmethod
    def list_rejected(request):
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        rejected_qs = CourseAllocation.objects.filter(
            department=dept,
            rejected_by_dvc=True
        )
        data = [{
            "id": a.id,
            "course_code": a.course_code,
            "course_name": a.course_name,
            "program": a.program.name if a.program else "",
            "lecturer": a.lecturer.display_name if a.lecturer else "",
            "number_of_students": a.number_of_students,
            "reason": a.reason_for_disapproval,
        } for a in rejected_qs]
        return JsonResponse({"status": "success", "rejected": data})

    @staticmethod
    def restore_rejected(request):
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        pk = request.POST.get("id")
        # SECURITY FIX #3 – Scope delete/restore to user's own department
        alloc = get_object_or_404(CourseAllocation, pk=pk, department=dept)
        alloc.rejected_by_dvc = False
        alloc.approved_by_dvc = False
        alloc.reason_for_disapproval = "No reason yet"
        alloc.save()
        return JsonResponse({"status": "success", "id": pk})

    @staticmethod
    def delete_rejected(request):
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        pk = request.POST.get("id")
        # SECURITY FIX #3 – Scope delete to user's own department
        alloc = get_object_or_404(CourseAllocation, pk=pk, department=dept)
        alloc.delete()
        return JsonResponse({"status": "success", "id": pk})

    @staticmethod
    def handle(request, action):
        if action == "list_rejected":
            return RejectedCourseService.list_rejected(request)
        elif action == "restore_rejected":
            return RejectedCourseService.restore_rejected(request)
        elif action == "delete_rejected":
            return RejectedCourseService.delete_rejected(request)
        return JsonResponse({"status": "error", "message": "Invalid rejected action"})


# -----------------------
# SelectionGroup helpers
# -----------------------

def _auto_group_name(course_codes):
    """Build a short, human-friendly group name from selected course codes,
    e.g. ['COSC301', 'COSC302'] -> 'COSC301/COSC302'. Used so COD staff no
    longer have to type a name for every Selection Group — the codes already
    identify it. Falls back to 'Untitled Group' if nothing was selected."""
    codes = [c for c in course_codes if c]
    if not codes:
        return "Untitled Group"
    if len(codes) <= 2:
        return "/".join(codes)
    return f"{codes[0]}/{codes[1]}+{len(codes) - 2}"


def _selection_group_to_dict(sg):
    """Serialize a SelectionGroup to a plain dict for JSON responses."""
    return {
        "id": sg.id,
        "name": sg.name,
        "program": sg.program.name if sg.program else "",
        "program_id": sg.program_id,
        "courses": [
            {
                "id": ca.id,
                "course_code": ca.course_code,
                "course_name": ca.course_name,
                "lecturer": ca.lecturer.display_name if ca.lecturer else "",
            }
            for ca in sg.courses.select_related("lecturer").all()
        ],
        "primary_allocation_count": sg.primary_allocations.count(),
    }


class SelectionGroupService:
    """AJAX handlers for SelectionGroup CRUD."""

    @staticmethod
    def list_groups(request):
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        groups = (
            SelectionGroup.objects
            .filter(department=dept)
            .prefetch_related("courses__lecturer")
            .select_related("program")
        )
        return JsonResponse({"status": "success", "groups": [_selection_group_to_dict(g) for g in groups]})

    @staticmethod
    def create_group(request):
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        name = request.POST.get("name", "").strip()
        program_id = request.POST.get("program_id") or None
        course_ids = request.POST.getlist("course_ids[]")

        elective_courses = CourseAllocation.objects.filter(
            pk__in=course_ids,
            is_elective=True,
            department=dept,
        ) if course_ids else CourseAllocation.objects.none()

        # No name typed in? Auto-generate one from the selected course codes
        # (e.g. "COSC301/COSC302") so this is no longer a required field.
        if not name:
            code_by_id = {str(c.id): c.course_code for c in elective_courses}
            ordered_codes = [code_by_id[cid] for cid in course_ids if cid in code_by_id]
            name = _auto_group_name(ordered_codes)

        # SECURITY FIX #4 – Validate name length to prevent DoS via huge strings
        if len(name) > 200:
            name = name[:200]

        program = get_object_or_404(Program, pk=program_id) if program_id else None

        with transaction.atomic():
            sg = SelectionGroup.objects.create(
                name=name,
                department=dept,
                program=program,
                created_by=request.user,
            )
            if course_ids:
                sg.courses.set(elective_courses)

        return JsonResponse({"status": "success", "group": _selection_group_to_dict(sg)})

    @staticmethod
    def update_group(request):
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        group_id = request.POST.get("group_id")
        sg = get_object_or_404(SelectionGroup, pk=group_id, department=dept)

        name = request.POST.get("name", "").strip()
        program_id = request.POST.get("program_id") or None
        course_ids = request.POST.getlist("course_ids[]")

        elective_courses = CourseAllocation.objects.filter(
            pk__in=course_ids,
            is_elective=True,
            department=dept,
        ) if course_ids else CourseAllocation.objects.none()

        # No name typed in? Re-derive it from the (possibly updated) course
        # selection, same as on create.
        if not name:
            code_by_id = {str(c.id): c.course_code for c in elective_courses}
            ordered_codes = [code_by_id[cid] for cid in course_ids if cid in code_by_id]
            name = _auto_group_name(ordered_codes)

        if len(name) > 200:
            name = name[:200]

        program = get_object_or_404(Program, pk=program_id) if program_id else None

        with transaction.atomic():
            sg.name = name
            sg.program = program
            sg.save()
            sg.courses.set(elective_courses)

        return JsonResponse({"status": "success", "group": _selection_group_to_dict(sg)})

    @staticmethod
    def delete_group(request):
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        group_id = request.POST.get("group_id")
        sg = get_object_or_404(SelectionGroup, pk=group_id, department=dept)
        sg.delete()
        return JsonResponse({"status": "success", "group_id": group_id})

    @staticmethod
    def list_elective_allocations(request):
        """Return all elective CourseAllocations for this department (for the group builder)."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        qs = (
            CourseAllocation.objects
            .filter(department=dept, is_elective=True)
            .select_related("program", "lecturer")
            .order_by("course_code")
        )
        data = [
            {
                "id": ca.id,
                "course_code": ca.course_code,
                "course_name": ca.course_name,
                "program": ca.program.name if ca.program else "",
                "lecturer": ca.lecturer.display_name if ca.lecturer else "",
            }
            for ca in qs
        ]
        return JsonResponse({"status": "success", "electives": data})

    @staticmethod
    def handle(request, action):
        if action == "list_selection_groups":
            return SelectionGroupService.list_groups(request)
        elif action == "create_selection_group":
            return SelectionGroupService.create_group(request)
        elif action == "update_selection_group":
            return SelectionGroupService.update_group(request)
        elif action == "delete_selection_group":
            return SelectionGroupService.delete_group(request)
        elif action == "list_elective_allocations":
            return SelectionGroupService.list_elective_allocations(request)
        return JsonResponse({"status": "error", "message": "Invalid selection group action"})


# ============================================================
# Special Intake Group Services
# ============================================================

class SpecialIntakeGroupService:
    """Service to handle special intake group operations."""
    
    @staticmethod
    def list_groups(request):
        """List all special intake groups for the user's department."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        groups = SpecialIntakeGroup.objects.filter(
            program__department=dept
        ).select_related("program").prefetch_related("course_allocations")
        
        data = []
        for g in groups:
            allocations = g.course_allocations.select_related("lecturer", "program_course")
            data.append({
                "id": g.id,
                "program_id": g.program_id,
                "program_name": g.program.name,
                "year": g.year,
                "semester": g.semester,
                "entry_year": g.entry_year,
                "academic_year": g.academic_year,
                "number_of_students": g.number_of_students,
                "total_courses": g.total_courses,
                "total_electives": g.total_electives,
                "courses": [
                    {
                        "id": a.id,
                        "course_code": a.course_code,
                        "course_name": a.course_name,
                        "lecturer": a.lecturer.display_name if a.lecturer else "",
                        "is_elective": a.is_elective,
                    }
                    for a in allocations
                ]
            })
        
        return JsonResponse({"status": "success", "groups": data})
    
    @staticmethod
    def create_group(request):
        """Create a new special intake group."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        program_id = request.POST.get("program_id")
        year = request.POST.get("year")
        semester = request.POST.get("semester")
        entry_year = request.POST.get("entry_year")
        number_of_students = request.POST.get("number_of_students", 0)
        
        if not all([program_id, year, semester, entry_year]):
            return JsonResponse({
                "status": "error",
                "message": "Program, year, semester, and entry year are required."
            }, status=400)
        
        try:
            program = get_object_or_404(Program, pk=program_id, department=dept)
            year = int(year)
            semester = int(semester)
            entry_year = int(entry_year)
            number_of_students = int(number_of_students)
        except (ValueError, TypeError):
            return JsonResponse({
                "status": "error",
                "message": "Invalid numeric values."
            }, status=400)
        
        # Validate ranges
        if year < 1 or year > 6:
            return JsonResponse({"status": "error", "message": "Year must be 1-6."}, status=400)
        if semester not in [1, 2, 3]:
            return JsonResponse({"status": "error", "message": "Semester must be 1, 2, or 3."}, status=400)
        if number_of_students < 0 or number_of_students > 10000:
            return JsonResponse({"status": "error", "message": "Invalid student count."}, status=400)
        
        with transaction.atomic():
            group, created = SpecialIntakeGroup.objects.get_or_create(
                program=program,
                year=year,
                semester=semester,
                entry_year=entry_year,
                defaults={"number_of_students": number_of_students}
            )
            if not created:
                # Update student count if group exists
                group.number_of_students = number_of_students
                group.save()
        
        return JsonResponse({
            "status": "success",
            "created": created,
            "group": {
                "id": group.id,
                "program_id": group.program_id,
                "program_name": group.program.name,
                "year": group.year,
                "semester": group.semester,
                "entry_year": group.entry_year,
                "academic_year": group.academic_year,
                "number_of_students": group.number_of_students,
            }
        })
    
    @staticmethod
    def pull_courses(request):
        """
        Pull courses from a program's curriculum into a special intake group.
        Supports:
        - Full program (all courses for a given semester)
        - Specific year/semester
        - Selected courses (list of course IDs)
        """
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        program_id = request.POST.get("program_id")
        group_id = request.POST.get("group_id")
        pull_type = request.POST.get("pull_type", "full")  # full, year, selected
        year = request.POST.get("year")
        semester = request.POST.get("semester")
        course_ids = request.POST.getlist("course_ids[]")

        # Optional: COD can supply the enrollment (number of students) once
        # here, and it is applied to every course pulled in this action,
        # instead of the group defaulting to 0 students.
        enrollment_raw = request.POST.get("number_of_students")
        enrollment_override = None
        if enrollment_raw not in (None, ""):
            try:
                enrollment_override = int(enrollment_raw)
            except (TypeError, ValueError):
                return JsonResponse({
                    "status": "error",
                    "message": "Invalid enrollment number."
                }, status=400)
            if enrollment_override < 0 or enrollment_override > 10000:
                return JsonResponse({
                    "status": "error",
                    "message": "Enrollment must be between 0 and 10000."
                }, status=400)

        if not program_id:
            return JsonResponse({
                "status": "error",
                "message": "Program ID is required."
            }, status=400)
        
        if not group_id:
            return JsonResponse({
                "status": "error",
                "message": "Group ID is required."
            }, status=400)
        
        try:
            program = get_object_or_404(Program, pk=program_id, department=dept)
            group = get_object_or_404(SpecialIntakeGroup, pk=group_id, program=program)
        except (Program.DoesNotExist, SpecialIntakeGroup.DoesNotExist):
            return JsonResponse({
                "status": "error",
                "message": "Program or group not found."
            }, status=404)
        
        # NOTE: A special intake group's own `semester` is just a cohort label
        # (e.g. self-sponsored students may be labelled "Semester 3" while the
        # curriculum itself only has Semester 1 / Semester 2 content). So which
        # curriculum semester's courses get pulled must be chosen explicitly via
        # the `semester` param rather than assumed to equal group.semester.
        curriculum_semester = semester
        if pull_type in ("full", "year"):
            if not curriculum_semester:
                return JsonResponse({
                    "status": "error",
                    "message": "Curriculum semester is required to pull courses."
                }, status=400)
            try:
                curriculum_semester = int(curriculum_semester)
            except ValueError:
                return JsonResponse({
                    "status": "error",
                    "message": "Invalid semester."
                }, status=400)

        # Build the queryset of ProgramCourses to pull
        program_courses_qs = ProgramCourse.objects.filter(program=program)
        
        if pull_type == "full":
            # Pull all courses for the explicitly chosen semester, narrowed to
            # the chosen year of study when one was provided.
            program_courses_qs = program_courses_qs.filter(semester=curriculum_semester)
            if year:
                try:
                    program_courses_qs = program_courses_qs.filter(year=int(year))
                except ValueError:
                    return JsonResponse({
                        "status": "error",
                        "message": "Invalid year."
                    }, status=400)
        elif pull_type == "year":
            if not year:
                return JsonResponse({
                    "status": "error",
                    "message": "Year is required for 'year' pull type."
                }, status=400)
            try:
                year = int(year)
            except ValueError:
                return JsonResponse({
                    "status": "error",
                    "message": "Invalid year."
                }, status=400)
            program_courses_qs = program_courses_qs.filter(
                year=year,
                semester=curriculum_semester
            )
        elif pull_type == "selected":
            if not course_ids:
                return JsonResponse({
                    "status": "error",
                    "message": "No courses selected."
                }, status=400)
            program_courses_qs = program_courses_qs.filter(id__in=course_ids)
        else:
            return JsonResponse({
                "status": "error",
                "message": f"Invalid pull_type: {pull_type}"
            }, status=400)
        
        # Get the actual ProgramCourse objects
        program_courses = list(program_courses_qs)
        
        if not program_courses:
            return JsonResponse({
                "status": "warning",
                "message": "No courses found matching the criteria."
            })
        
        # Create CourseAllocation records for each ProgramCourse
        created_count = 0
        skipped_count = 0
        created_allocations = []
        pull_errors = []
        
        with transaction.atomic():
            # If the COD gave an enrollment figure for this pull, keep the
            # group's own count in sync too, so it's reflected next time
            # (e.g. in the group selector / Special Intake Group list).
            if enrollment_override is not None and group.number_of_students != enrollment_override:
                group.number_of_students = enrollment_override
                group.save(update_fields=["number_of_students"])

            for pc in program_courses:
                # Check if allocation already exists for this group
                exists = CourseAllocation.objects.filter(
                    program=program,
                    program_course=pc,
                    department=dept,
                    intake=CourseAllocation.INTAKE_SPECIAL,
                    special_intake_group=group
                ).exists()
                
                if exists:
                    skipped_count += 1
                    continue
                
                allocation = CourseAllocation(
                    course_code=pc.course_code,
                    course_name=pc.course_name,
                    department=dept,
                    origin_department=dept,
                    program=program,
                    program_course=pc,
                    number_of_students=(
                        enrollment_override if enrollment_override is not None
                        else group.number_of_students
                    ),
                    intake=CourseAllocation.INTAKE_SPECIAL,
                    is_elective=pc.is_elective_type,
                    special_intake_group=group,
                )
                # BUG (fixed): CourseAllocation.clean()'s duplicate check is
                # scoped to (program, course_code, intake) -- it does NOT
                # know about special_intake_group. So this course can be
                # "not yet in THIS group" (the `exists` check above, which
                # IS scoped to this group, correctly returns False) while
                # still colliding with a SPECIAL-intake allocation of the
                # same course code that already exists under a *different*
                # special intake group for this program (e.g. a different
                # entry-year cohort). full_clean() then raises
                # ValidationError, which nothing here used to catch -- it
                # propagated straight out of the view as an unhandled 500.
                # A wrong pull for one course must not blow up the whole
                # batch or hide the reason from the COD, so: isolate each
                # course in its own savepoint, catch the failure, report
                # it, and keep going with the rest.
                try:
                    with transaction.atomic():
                        allocation.full_clean()
                        allocation.save()
                except ValidationError as ve:
                    message_dict = getattr(ve, "message_dict", None)
                    if message_dict:
                        reason = " ".join(m for msgs in message_dict.values() for m in msgs)
                    else:
                        reason = "; ".join(ve.messages) if hasattr(ve, "messages") else str(ve)
                    skipped_count += 1
                    pull_errors.append({"course_code": pc.course_code, "reason": reason})
                    continue

                created_count += 1
                created_allocations.append({
                    "id": allocation.id,
                    "course_code": allocation.course_code,
                    "course_name": allocation.course_name,
                    "is_elective": allocation.is_elective,
                })
        
        message = f"Pulled {created_count} courses, skipped {skipped_count} (already exist)."
        if pull_errors:
            message += f" {len(pull_errors)} course(s) could not be pulled -- see 'errors'."

        return JsonResponse({
            "status": "success",
            "message": message,
            "created_count": created_count,
            "skipped_count": skipped_count,
            "courses": created_allocations,
            "errors": pull_errors,
            "group_id": group.id,
        })
    
    @staticmethod
    def pull_courses_normal(request):
        """
        Pull courses from a program's curriculum straight into NORMAL
        (regular) allocations for this department. Mirrors pull_courses()
        above but skips the special-intake-group machinery entirely --
        there is no group to resolve or create.
        """
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        program_id = request.POST.get("program_id")
        pull_type = request.POST.get("pull_type", "full")  # full, year, selected
        year = request.POST.get("year")
        semester = request.POST.get("semester")
        course_ids = request.POST.getlist("course_ids[]")

        # Optional: COD can supply the enrollment (number of students) once
        # here, and it is applied to every course pulled in this action.
        enrollment_raw = request.POST.get("number_of_students")
        enrollment_override = None
        if enrollment_raw not in (None, ""):
            try:
                enrollment_override = int(enrollment_raw)
            except (TypeError, ValueError):
                return JsonResponse({
                    "status": "error",
                    "message": "Invalid enrollment number."
                }, status=400)
            if enrollment_override < 0 or enrollment_override > 10000:
                return JsonResponse({
                    "status": "error",
                    "message": "Enrollment must be between 0 and 10000."
                }, status=400)

        if not program_id:
            return JsonResponse({
                "status": "error",
                "message": "Program ID is required."
            }, status=400)

        try:
            program = get_object_or_404(Program, pk=program_id, department=dept)
        except Program.DoesNotExist:
            return JsonResponse({
                "status": "error",
                "message": "Program not found."
            }, status=404)

        curriculum_semester = semester
        if pull_type in ("full", "year"):
            if not curriculum_semester:
                return JsonResponse({
                    "status": "error",
                    "message": "Semester is required to pull courses."
                }, status=400)
            try:
                curriculum_semester = int(curriculum_semester)
            except ValueError:
                return JsonResponse({
                    "status": "error",
                    "message": "Invalid semester."
                }, status=400)

        # Build the queryset of ProgramCourses to pull
        program_courses_qs = ProgramCourse.objects.filter(program=program)

        if pull_type == "full":
            program_courses_qs = program_courses_qs.filter(semester=curriculum_semester)
            if year:
                try:
                    program_courses_qs = program_courses_qs.filter(year=int(year))
                except ValueError:
                    return JsonResponse({
                        "status": "error",
                        "message": "Invalid year."
                    }, status=400)
        elif pull_type == "year":
            if not year:
                return JsonResponse({
                    "status": "error",
                    "message": "Year is required for 'year' pull type."
                }, status=400)
            try:
                year = int(year)
            except ValueError:
                return JsonResponse({
                    "status": "error",
                    "message": "Invalid year."
                }, status=400)
            program_courses_qs = program_courses_qs.filter(
                year=year,
                semester=curriculum_semester
            )
        elif pull_type == "selected":
            if not course_ids:
                return JsonResponse({
                    "status": "error",
                    "message": "No courses selected."
                }, status=400)
            program_courses_qs = program_courses_qs.filter(id__in=course_ids)
        else:
            return JsonResponse({
                "status": "error",
                "message": f"Invalid pull_type: {pull_type}"
            }, status=400)

        program_courses = list(program_courses_qs)

        if not program_courses:
            return JsonResponse({
                "status": "warning",
                "message": "No courses found matching the criteria."
            })

        created_count = 0
        skipped_count = 0
        created_allocations = []
        pull_errors = []

        with transaction.atomic():
            for pc in program_courses:
                # A course is considered "already pulled" if a NORMAL,
                # non-evening allocation already exists for it in this dept.
                exists = CourseAllocation.objects.filter(
                    program=program,
                    program_course=pc,
                    department=dept,
                    intake=CourseAllocation.INTAKE_NORMAL,
                    is_evening_weekend=False,
                ).exists()

                if exists:
                    skipped_count += 1
                    continue

                allocation = CourseAllocation(
                    course_code=pc.course_code,
                    course_name=pc.course_name,
                    department=dept,
                    origin_department=dept,
                    program=program,
                    program_course=pc,
                    number_of_students=(
                        enrollment_override if enrollment_override is not None else 0
                    ),
                    intake=CourseAllocation.INTAKE_NORMAL,
                    is_elective=pc.is_elective_type,
                    is_evening_weekend=False,
                )
                # Same isolate-per-course pattern as the special-intake pull:
                # one course's ValidationError must not blow up the batch.
                try:
                    with transaction.atomic():
                        allocation.full_clean()
                        allocation.save()
                except ValidationError as ve:
                    message_dict = getattr(ve, "message_dict", None)
                    if message_dict:
                        reason = " ".join(m for msgs in message_dict.values() for m in msgs)
                    else:
                        reason = "; ".join(ve.messages) if hasattr(ve, "messages") else str(ve)
                    skipped_count += 1
                    pull_errors.append({"course_code": pc.course_code, "reason": reason})
                    continue

                created_count += 1
                created_allocations.append({
                    "id": allocation.id,
                    "course_code": allocation.course_code,
                    "course_name": allocation.course_name,
                    "is_elective": allocation.is_elective,
                })

        message = f"Pulled {created_count} courses, skipped {skipped_count} (already exist)."
        if pull_errors:
            message += f" {len(pull_errors)} course(s) could not be pulled -- see 'errors'."

        return JsonResponse({
            "status": "success",
            "message": message,
            "created_count": created_count,
            "skipped_count": skipped_count,
            "courses": created_allocations,
            "errors": pull_errors,
        })

    @staticmethod
    def get_available_courses_normal(request):
        """Get available ProgramCourses for pulling straight into normal (regular) allocations."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        program_id = request.POST.get("program_id")
        year = request.POST.get("year")
        semester = request.POST.get("semester")

        if not program_id:
            return JsonResponse({
                "status": "error",
                "message": "Program ID is required."
            }, status=400)

        try:
            program = get_object_or_404(Program, pk=program_id, department=dept)
        except Program.DoesNotExist:
            return JsonResponse({
                "status": "error",
                "message": "Program not found."
            }, status=404)

        qs = ProgramCourse.objects.filter(program=program)

        if year:
            try:
                qs = qs.filter(year=int(year))
            except ValueError:
                pass

        if semester:
            try:
                qs = qs.filter(semester=int(semester))
            except ValueError:
                pass

        # Exclude courses that already have a normal (regular, non-evening)
        # allocation for this program/department, so the checklist only
        # ever offers courses that haven't been pulled yet.
        existing_course_ids = CourseAllocation.objects.filter(
            department=dept,
            program=program,
            intake=CourseAllocation.INTAKE_NORMAL,
            is_evening_weekend=False,
        ).values_list("program_course_id", flat=True)
        qs = qs.exclude(id__in=existing_course_ids)

        data = []
        for pc in qs.order_by("year", "semester", "course_code"):
            data.append({
                "id": pc.id,
                "course_code": pc.course_code,
                "course_name": pc.course_name,
                "year": pc.year,
                "semester": pc.semester,
                "unit_type": pc.unit_type,
                "is_elective": pc.is_elective_type,
            })

        return JsonResponse({"status": "success", "courses": data})

    @staticmethod
    def get_available_courses(request):
        """Get available ProgramCourses for pulling into a special intake group."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        program_id = request.POST.get("program_id")
        year = request.POST.get("year")
        semester = request.POST.get("semester")
        group_id = request.POST.get("group_id")
        
        if not program_id:
            return JsonResponse({
                "status": "error",
                "message": "Program ID is required."
            }, status=400)
        
        try:
            program = get_object_or_404(Program, pk=program_id, department=dept)
        except Program.DoesNotExist:
            return JsonResponse({
                "status": "error",
                "message": "Program not found."
            }, status=404)
        
        qs = ProgramCourse.objects.filter(program=program)
        
        if year:
            try:
                qs = qs.filter(year=int(year))
            except ValueError:
                pass
        
        if semester:
            try:
                qs = qs.filter(semester=int(semester))
            except ValueError:
                pass
        
        # If group_id provided, exclude courses already in the group
        if group_id:
            try:
                group = SpecialIntakeGroup.objects.get(pk=group_id, program=program)
                existing_course_ids = group.course_allocations.values_list("program_course_id", flat=True)
                qs = qs.exclude(id__in=existing_course_ids)
            except SpecialIntakeGroup.DoesNotExist:
                pass
        
        data = []
        for pc in qs.order_by("year", "semester", "course_code"):
            data.append({
                "id": pc.id,
                "course_code": pc.course_code,
                "course_name": pc.course_name,
                "year": pc.year,
                "semester": pc.semester,
                "unit_type": pc.unit_type,
                "is_elective": pc.is_elective_type,
            })
        
        return JsonResponse({"status": "success", "courses": data})
    
    @staticmethod
    def delete_group(request):
        """Delete a special intake group and its associated allocations."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        group_id = request.POST.get("group_id")
        
        try:
            group = SpecialIntakeGroup.objects.get(pk=group_id, program__department=dept)
        except SpecialIntakeGroup.DoesNotExist:
            return JsonResponse({
                "status": "error",
                "message": "Group not found."
            }, status=404)
        
        with transaction.atomic():
            # Delete associated allocations
            count = group.course_allocations.count()
            group.course_allocations.all().delete()
            group.delete()
        
        return JsonResponse({
            "status": "success",
            "message": f"Deleted group and {count} associated allocations.",
            "deleted_allocations": count
        })
    
    @staticmethod
    def handle(request, action):
        """Route special intake group actions."""
        if action == "list_special_intake_groups":
            return SpecialIntakeGroupService.list_groups(request)
        elif action == "create_special_intake_group":
            return SpecialIntakeGroupService.create_group(request)
        elif action == "pull_courses_to_special_intake":
            return SpecialIntakeGroupService.pull_courses(request)
        elif action == "get_available_courses_for_pull":
            return SpecialIntakeGroupService.get_available_courses(request)
        elif action == "delete_special_intake_group":
            return SpecialIntakeGroupService.delete_group(request)
        elif action == "pull_courses_to_normal":
            return SpecialIntakeGroupService.pull_courses_normal(request)
        elif action == "get_available_courses_for_normal_pull":
            return SpecialIntakeGroupService.get_available_courses_normal(request)
        return JsonResponse({"status": "error", "message": "Invalid special intake group action"})


# ============================================================
# Student Group Services
# ============================================================

def _groupable_courses_qs(program, year, semester, intake):
    """
    Canonical set of courses eligible for student-group splitting for a given
    program/year/semester/intake: one representative CourseAllocation row per
    distinct ProgramCourse, excluding electives and specialization-stem
    courses (those stay shared by construction — see CourseAllocation.clean()).

    Restricted to is_evening_weekend=False: student groups are a Regular-table
    concept (created by right-clicking the Regular allocations table), and a
    course can have a separate Evening/Weekend row alongside its Regular row.
    Without this filter, the Evening/Weekend row could get picked as the
    representative/clone-template, silently pulling the group's course out of
    the Regular table (it would still show correctly in the Student Groups
    list, since that list ignores is_evening_weekend).

    Deliberately NOT filtered by student_group__isnull — a course that has
    already been split across Group A and Group B is still "groupable" for a
    brand-new Group C. Filtering by isnull would starve later groups of
    courses that earlier groups already claimed.
    """
    base = CourseAllocation.objects.filter(
        program=program,
        program_course__year=year,
        program_course__semester=semester,
        intake=intake,
        is_elective=False,
        is_evening_weekend=False,
        specialization_stem__isnull=True,
    ).select_related("program_course", "department", "origin_department", "lecturer")

    # One row per program_course: prefer the still-shared (untagged) row so
    # callers see representative field values that haven't drifted from a
    # group-specific edit; fall back to whichever row exists otherwise.
    by_course = {}
    for alloc in base.order_by("student_group_id"):  # NULLs (shared) sort first on MariaDB
        by_course.setdefault(alloc.program_course_id, alloc)
    return by_course  # dict: program_course_id -> representative CourseAllocation


def _assign_course_to_group(program, program_course_id, intake, group):
    """
    Ensure a CourseAllocation row tagged to `group` exists for this course,
    with its course_code carrying that group's letter suffix (e.g. "EDFO 111"
    becomes "EDFO 111-A" for Group A, "EDFO 111-B" for Group B, ...) so the
    groups are distinguishable by code wherever the plain course_code is
    displayed, not only via the student_group relation.

    - If a row for this course is already tagged to `group`, its code is
      checked and corrected if it doesn't already carry the right letter
      (e.g. it predates this convention, or the group's letter changed) —
      no new row is created in this case.
    - Else if an untagged (shared, student_group=NULL) row for this course
      exists, TAG IT IN PLACE (no duplicate row created) and rename it —
      this is what makes the first group "free" rather than an unnecessary
      clone.
    - Else (every existing row for this course already belongs to some other
      group), CLONE from any sibling row as a template, so this group gets
      its own real class with its own lecturer/venue slot and its own coded
      identity.
    - Else if no CourseAllocation for this course exists anywhere yet (it's
      never been allocated to any group or shared), build a brand-new one
      straight from the ProgramCourse curriculum entry — this is what lets
      the "whole program" course picker add a course that has no allocation
      row at all yet (lecturer/venue left unassigned for the COD to fill in).

    Returns (allocation, action) where action is "already_assigned",
    "tagged", "cloned", "created_new", or None if the program_course_id
    doesn't belong to this program at all.
    """
    existing_for_group = CourseAllocation.objects.filter(
        program=program, program_course_id=program_course_id,
        intake=intake, student_group=group,
    ).first()
    if existing_for_group:
        correct_code = _lettered_code_for_group(existing_for_group.course_code, group.letter)
        if existing_for_group.course_code != correct_code:
            existing_for_group.course_code = correct_code
            existing_for_group.save(update_fields=["course_code"])
        return existing_for_group, "already_assigned"

    shared = CourseAllocation.objects.filter(
        program=program, program_course_id=program_course_id,
        intake=intake, student_group__isnull=True,
        is_elective=False, is_evening_weekend=False, specialization_stem__isnull=True,
    ).first()
    if shared:
        shared.student_group = group
        shared.course_code = _lettered_code_for_group(shared.course_code, group.letter)
        shared.save(update_fields=["student_group", "course_code"])
        return shared, "tagged"

    template = CourseAllocation.objects.filter(
        program=program, program_course_id=program_course_id, intake=intake,
        is_evening_weekend=False,
    ).exclude(student_group=group).first()
    if template:
        clone = CourseAllocation.objects.create(
            course_code=_lettered_code_for_group(template.course_code, group.letter),
            course_name=template.course_name,
            department=template.department,
            origin_department=template.origin_department,
            program=template.program,
            program_course=template.program_course,
            lecturer=template.lecturer,
            number_of_students=template.number_of_students,
            intake=template.intake,
            is_elective=template.is_elective,
            is_evening_weekend=template.is_evening_weekend,
            student_group=group,
            selection_group=template.selection_group,
            specialization_stem=template.specialization_stem,
            approved_by_dvc=template.approved_by_dvc,
            rejected_by_dvc=template.rejected_by_dvc,
            submitted_to_tt=template.submitted_to_tt,
        )
        return clone, "cloned"

    # No CourseAllocation row exists anywhere for this course yet (it's never
    # been allocated before) — build a fresh one straight from the curriculum
    # (ProgramCourse), so the "whole program" picker can add ANY course from
    # the program, not just ones that already have an allocation row.
    try:
        pc = ProgramCourse.objects.get(pk=program_course_id, program=program)
    except ProgramCourse.DoesNotExist:
        return None, None

    fresh = CourseAllocation.objects.create(
        course_code=_lettered_code_for_group(pc.course_code, group.letter),
        course_name=pc.course_name,
        department=program.department,
        origin_department=program.department,
        program=program,
        program_course=pc,
        lecturer=None,
        number_of_students=0,
        intake=intake,
        is_elective=pc.is_elective_type,
        is_evening_weekend=False,
        student_group=group,
    )
    return fresh, "created_new"


# ------------------------------------------------------------------------
# Grouping knowledge base — captures what a COD builds through the normal
# Student Group UI (single group / bulk-year / copy-to-years / add-courses)
# into a GroupingTemplate, independent of the live CourseAllocation rows
# that auto-allocate wipes and rebuilds every run. auto_allocate_courses
# then replays these templates after each run so the same split doesn't
# have to be recreated by hand every time.
# ------------------------------------------------------------------------

def _remember_grouping_template(program, year, semester, intake, letter, name, user,
                                 scope=None, assigned_base_codes=None):
    """
    Upsert the GroupingTemplate for this program/year/semester/intake and
    register `letter` as one of its remembered groups. Safe to call
    repeatedly — each call just adds/updates one group's entry without
    disturbing sibling groups already remembered here.

    `scope` should be passed by callers that know the group's intended
    scope ("all" or "selected") — typically group creation. Callers that
    are merely adding more courses to an already-existing group (and don't
    know/won't change the scope) can omit it; the template's existing
    scope is left as-is. `assigned_base_codes` (base course codes, e.g.
    "COSC 101") are only remembered when the template's scope is
    "selected" — an "all compulsory courses" template doesn't need an
    explicit course list, since _groupable_courses_qs already recomputes
    that fresh each time it's replayed.
    """
    defaults = {"created_by": user}
    if scope:
        defaults["scope"] = scope
    template, _ = GroupingTemplate.objects.get_or_create(
        program=program, year=year, semester=semester, intake=intake,
        defaults=defaults,
    )
    if scope and template.scope != scope:
        template.scope = scope
        template.save(update_fields=["scope"])

    GroupingTemplateGroup.objects.get_or_create(
        template=template, letter=letter,
        defaults={"name": name or f"Group {letter}"},
    )

    if template.scope == GroupingTemplate.SCOPE_SELECTED and assigned_base_codes:
        for code in assigned_base_codes:
            if code:
                GroupingTemplateCourse.objects.get_or_create(
                    template=template, base_course_code=code,
                )
    return template


def _extend_grouping_template(group, assigned_base_codes):
    """
    Keep an already-existing GroupingTemplate in sync when more courses are
    assigned to one of its groups later (e.g. via the "Add course(s) to
    this group" picker). Does nothing if no template has been recorded yet
    for this program/year/semester/intake — a plain "add courses" action
    on an ad-hoc group isn't itself a signal to start remembering it.
    """
    try:
        template = GroupingTemplate.objects.get(
            program=group.program, year=group.year,
            semester=group.semester, intake=group.intake,
        )
    except GroupingTemplate.DoesNotExist:
        return

    GroupingTemplateGroup.objects.get_or_create(
        template=template, letter=group.letter,
        defaults={"name": group.name},
    )

    if template.scope == GroupingTemplate.SCOPE_SELECTED:
        for code in assigned_base_codes:
            if code:
                GroupingTemplateCourse.objects.get_or_create(
                    template=template, base_course_code=code,
                )


# ------------------------------------------------------------------------
# Course-combination knowledge base — same idea as the grouping template
# above, but for CombinedCourseGroup: captures which base course codes
# (and which programs' sections of them) a COD has combined into one
# taught session, so auto-allocate can recreate the combination
# automatically after it rebuilds allocations.
# ------------------------------------------------------------------------

def _remember_course_combination_template(dept, base_course_code, lecturer_obj, allocations, user):
    """
    Upsert the CourseCombinationTemplate for this department + base course
    code, and record every program whose section was part of this combine
    (so a replay doesn't sweep in some *other* program that happens to
    share the same course code but wasn't part of the original combine).
    """
    template, created = CourseCombinationTemplate.objects.get_or_create(
        department=dept, base_course_code=base_course_code,
        defaults={"lecturer": lecturer_obj, "created_by": user},
    )
    if not created and lecturer_obj and template.lecturer_id != lecturer_obj.id:
        template.lecturer = lecturer_obj
        template.save(update_fields=["lecturer"])

    program_ids = {a.program_id for a in allocations if a.program_id}
    for pid in program_ids:
        CourseCombinationTemplateProgram.objects.get_or_create(
            template=template, program_id=pid,
        )
    return template


class StudentGroupService:
    """Service to handle student group operations."""
    
    @staticmethod
    def list_groups(request):
        """List all student groups for the user's department."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        groups = StudentGroup.objects.filter(
            program__department=dept
        ).select_related("program").prefetch_related("course_allocations")
        
        data = []
        for g in groups:
            allocations = g.course_allocations.select_related("lecturer", "program_course")
            data.append({
                "id": g.id,
                "program_id": g.program_id,
                "program_name": g.program.name,
                "year": g.year,
                "semester": g.semester,
                "intake": g.intake,
                "name": g.name,
                "letter": g.letter,
                "course_count": allocations.count(),
                "created_at": g.created_at.strftime("%Y-%m-%d %H:%M"),
                "courses": [
                    {
                        "id": a.id,
                        "course_code": a.course_code,
                        "course_name": a.course_name,
                        "lecturer": a.lecturer.display_name if a.lecturer else "",
                        "is_elective": a.is_elective,
                        "number_of_students": a.number_of_students,
                        "is_evening_weekend": a.is_evening_weekend,
                    }
                    for a in allocations
                ]
            })
        
        return JsonResponse({"status": "success", "groups": data})
    
    @staticmethod
    def create_group(request):
        """Create a new student group and optionally assign courses."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        program_id = request.POST.get("program_id")
        year = request.POST.get("year")
        semester = request.POST.get("semester")
        intake = request.POST.get("intake", CourseAllocation.INTAKE_NORMAL)
        letter = request.POST.get("letter", "").strip().upper()
        name = request.POST.get("name", "").strip()
        scope = request.POST.get("scope", "all")  # "all" or "selected"
        course_ids = request.POST.getlist("course_ids[]")
        
        # Validate required fields
        if not all([program_id, year, semester, letter]):
            return JsonResponse({
                "status": "error",
                "message": "Program, year, semester, and group letter are required."
            }, status=400)
        
        try:
            program = get_object_or_404(Program, pk=program_id, department=dept)
            year = int(year)
            semester = int(semester)
        except (ValueError, TypeError):
            return JsonResponse({
                "status": "error",
                "message": "Invalid numeric values."
            }, status=400)
        
        # Validate letter: 1-4 alphanumeric characters (e.g. "A", "DA", "DB").
        if not letter or len(letter) > 4 or not letter.isalnum():
            return JsonResponse({
                "status": "error",
                "message": "Group code must be 1-4 letters/numbers (e.g. 'A' or 'DA')."
            }, status=400)
        
        # Validate year and semester ranges
        if year < 1 or year > 6:
            return JsonResponse({
                "status": "error",
                "message": "Year must be between 1 and 6."
            }, status=400)
        if semester not in [1, 2, 3]:
            return JsonResponse({
                "status": "error",
                "message": "Semester must be 1, 2, or 3."
            }, status=400)
        if intake not in [CourseAllocation.INTAKE_NORMAL, CourseAllocation.INTAKE_SPECIAL]:
            return JsonResponse({
                "status": "error",
                "message": "Invalid intake type."
            }, status=400)
        
        # Check if group already exists
        if StudentGroup.objects.filter(
            program=program, year=year, semester=semester,
            intake=intake, letter=letter
        ).exists():
            return JsonResponse({
                "status": "error",
                "message": f"Group '{letter}' already exists for this program/year/semester."
            }, status=400)
        
        with transaction.atomic():
            # Create the group
            group = StudentGroup.objects.create(
                program=program,
                year=year,
                semester=semester,
                intake=intake,
                letter=letter,
                name=name or f"Group {letter}",
                created_by=request.user,
            )
            
            # Canonical set of groupable courses for this program/year/semester
            # (excludes electives/stem courses; includes courses already split
            # across other groups, since this new group still needs its own row).
            by_course = _groupable_courses_qs(program, year, semester, intake)

            if scope == "selected" and course_ids:
                # course_ids are CourseAllocation PKs from the picker (any
                # representative row); resolve them to program_course ids.
                selected_pc_ids = set(
                    CourseAllocation.objects.filter(pk__in=course_ids)
                    .values_list("program_course_id", flat=True)
                )
                target_pc_ids = [pc for pc in by_course if pc in selected_pc_ids]
            else:
                target_pc_ids = list(by_course.keys())

            created_count = 0
            tagged_count = 0
            cloned_courses = []
            # Every row touched by this group -- cloned (brand new row),
            # tagged (existing shared row retitled with this group's
            # letter), AND already-assigned -- serialized so the frontend
            # can upsert each one straight into the table via
            # updateAllocRowDOM() instead of reloading.
            affected_allocations = []
            assigned_base_codes = []
            for pc_id in target_pc_ids:
                alloc, action = _assign_course_to_group(program, pc_id, intake, group)
                if action == "tagged":
                    tagged_count += 1
                    affected_allocations.append(_alloc_row_payload(alloc, dept))
                elif action in ("cloned", "created_new"):
                    created_count += 1
                    cloned_courses.append({
                        "id": alloc.id,
                        "course_code": alloc.course_code,
                        "course_name": alloc.course_name,
                    })
                    affected_allocations.append(_alloc_row_payload(alloc, dept))
                elif action == "already_assigned" and alloc is not None:
                    affected_allocations.append(_alloc_row_payload(alloc, dept))
                if alloc is not None:
                    assigned_base_codes.append(strip_group_suffix(alloc.course_code)[0])

            # Remember this group in the grouping knowledge base so
            # auto-allocate can recreate the same split automatically next
            # time it rebuilds this department's allocations.
            _remember_grouping_template(
                program, year, semester, intake, letter, name, request.user,
                scope=scope, assigned_base_codes=assigned_base_codes,
            )
        
        return JsonResponse({
            "status": "success",
            "group": {
                "id": group.id,
                "program_id": group.program_id,
                "program_name": group.program.name,
                "year": group.year,
                "semester": group.semester,
                "intake": group.intake,
                "name": group.name,
                "letter": group.letter,
            },
            "created_count": created_count,
            "tagged_count": tagged_count,
            "cloned_courses": cloned_courses,
            "affected_allocations": affected_allocations,
            "message": (
                f"Created group {letter}: {tagged_count} course(s) carried over in place, "
                f"{created_count} new course(s) cloned for this group."
            ),
        })
    
    @staticmethod
    def delete_group(request):
        """Delete a student group and its associated allocations."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        group_id = request.POST.get("group_id")
        
        try:
            group = StudentGroup.objects.get(pk=group_id, program__department=dept)
        except StudentGroup.DoesNotExist:
            return JsonResponse({
                "status": "error",
                "message": "Group not found."
            }, status=404)
        
        with transaction.atomic():
            # Delete associated allocations -- capture their ids first so the
            # frontend can remove exactly those rows from the DOM instead of
            # reloading the whole page.
            deleted_ids = list(group.course_allocations.values_list("id", flat=True))
            count = len(deleted_ids)
            group.course_allocations.all().delete()
            group.delete()
        
        return JsonResponse({
            "status": "success",
            "message": f"Deleted group and {count} associated allocations.",
            "deleted_allocations": count,
            "deleted_allocation_ids": deleted_ids,
        })
    
    @staticmethod
    def assign_courses_to_group(request):
        """
        Assign selected courses to an existing student group.

        Two source modes, selected by which param is present:
        - program_course_ids[]: raw ProgramCourse PKs from the "whole program"
          picker — ANY course in the program's curriculum, any year/semester,
          electives included. Used by the group's right-click "Add course(s)
          to this group" picker so the COD isn't limited to only the courses
          that already happen to have a matching CourseAllocation row.
        - course_ids[] (legacy) / scope="all": resolved through
          _groupable_courses_qs, restricted to the group's own year/semester/
          intake and to compulsory (non-elective, non-stem) courses — this is
          what the "Create Student Group" and bulk-add-to-groups modals use.
        """
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        group_id = request.POST.get("group_id")
        course_ids = request.POST.getlist("course_ids[]")
        program_course_ids = request.POST.getlist("program_course_ids[]")
        scope = request.POST.get("scope", "selected")
        
        try:
            group = StudentGroup.objects.get(pk=group_id, program__department=dept)
        except StudentGroup.DoesNotExist:
            return JsonResponse({
                "status": "error",
                "message": "Group not found."
            }, status=404)

        if program_course_ids:
            # Whole-program picker: any curriculum course belonging to this
            # group's program, regardless of year/semester/elective status.
            target_pc_ids = list(
                ProgramCourse.objects.filter(
                    pk__in=program_course_ids, program=group.program
                ).values_list("id", flat=True)
            )
        else:
            by_course = _groupable_courses_qs(group.program, group.year, group.semester, group.intake)
            if scope == "all":
                target_pc_ids = list(by_course.keys())
            else:
                selected_pc_ids = set(
                    CourseAllocation.objects.filter(pk__in=course_ids)
                    .values_list("program_course_id", flat=True)
                )
                target_pc_ids = [pc for pc in by_course if pc in selected_pc_ids]

        created_count = 0
        tagged_count = 0
        cloned_courses = []
        # Every row touched -- cloned/newly-created, tagged, AND already
        # assigned -- serialized so the frontend can upsert each straight
        # into the table via updateAllocRowDOM() instead of reloading the
        # page. Previously "already_assigned" rows were left out of this
        # list entirely, so if a course's CourseAllocation row already
        # existed (e.g. tagged by an earlier action, auto-allocate, or a
        # stale duplicate click) it never got inserted into the DOM here —
        # it would only appear after a full page reload even though the
        # server had already reported success.
        affected_allocations = []
        assigned_base_codes = []
        for pc_id in target_pc_ids:
            alloc, action = _assign_course_to_group(group.program, pc_id, group.intake, group)
            if action == "tagged":
                tagged_count += 1
                affected_allocations.append(_alloc_row_payload(alloc, dept))
            elif action in ("cloned", "created_new"):
                created_count += 1
                cloned_courses.append({
                    "id": alloc.id,
                    "course_code": alloc.course_code,
                    "course_name": alloc.course_name,
                })
                affected_allocations.append(_alloc_row_payload(alloc, dept))
            elif action == "already_assigned" and alloc is not None:
                affected_allocations.append(_alloc_row_payload(alloc, dept))
            if alloc is not None:
                assigned_base_codes.append(strip_group_suffix(alloc.course_code)[0])

        _extend_grouping_template(group, assigned_base_codes)

        return JsonResponse({
            "status": "success",
            "created_count": created_count,
            "tagged_count": tagged_count,
            "cloned_courses": cloned_courses,
            "affected_allocations": affected_allocations,
            "message": (
                f"Group {group.letter}: {tagged_count} course(s) carried over in place, "
                f"{created_count} new course(s) cloned."
            ),
        })
    
    @staticmethod
    def get_available_courses(request):
        """Get courses available for assignment to a student group."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        program_id = request.POST.get("program_id")
        year = request.POST.get("year")
        semester = request.POST.get("semester")
        intake = request.POST.get("intake", CourseAllocation.INTAKE_NORMAL)
        
        if not program_id or not year or not semester:
            return JsonResponse({
                "status": "error",
                "message": "Program ID, year, and semester are required."
            }, status=400)
        
        try:
            program = get_object_or_404(Program, pk=program_id, department=dept)
            year = int(year)
            semester = int(semester)
        except (ValueError, TypeError):
            return JsonResponse({
                "status": "error",
                "message": "Invalid numeric values."
            }, status=400)
        
        # Canonical groupable courses — includes courses already split across
        # other groups, so later groups (B, C, ...) still see the full list.
        by_course = _groupable_courses_qs(program, year, semester, intake)

        data = []
        for a in by_course.values():
            base_code, _ = strip_group_suffix(a.course_code)
            data.append({
                "id": a.id,
                "course_code": base_code,
                "course_name": a.course_name,
                "lecturer": a.lecturer.display_name if a.lecturer else "",
                "year": a.program_course.year if a.program_course else None,
                "semester": a.program_course.semester if a.program_course else None,
                "number_of_students": a.number_of_students,
            })
        
        return JsonResponse({"status": "success", "courses": data})
    
    @staticmethod
    def get_group_detail(request):
        """Get detailed information about a student group."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        group_id = request.POST.get("group_id")
        
        try:
            group = StudentGroup.objects.get(pk=group_id, program__department=dept)
        except StudentGroup.DoesNotExist:
            return JsonResponse({
                "status": "error",
                "message": "Group not found."
            }, status=404)
        
        allocations = group.course_allocations.select_related("lecturer", "program_course")
        
        return JsonResponse({
            "status": "success",
            "group": {
                "id": group.id,
                "program_id": group.program_id,
                "program_name": group.program.name,
                "year": group.year,
                "semester": group.semester,
                "intake": group.intake,
                "name": group.name,
                "letter": group.letter,
                "created_at": group.created_at.strftime("%Y-%m-%d %H:%M"),
                "course_count": allocations.count(),
                "courses": [
                    {
                        "id": a.id,
                        "course_code": a.course_code,
                        "course_name": a.course_name,
                        "lecturer": a.lecturer.display_name if a.lecturer else "",
                        "number_of_students": a.number_of_students,
                        "is_elective": a.is_elective,
                        "is_evening_weekend": a.is_evening_weekend,
                        "approved_by_dvc": a.approved_by_dvc,
                        "rejected_by_dvc": a.rejected_by_dvc,
                        "submitted_to_tt": a.submitted_to_tt,
                    }
                    for a in allocations
                ]
            }
        })
    
    @staticmethod
    def update_group_enrollment(request):
        """
        Set the enrollment (number_of_students) for a student group's courses.

        Applies one number across every CourseAllocation row tagged to this
        group by default (a group is one cohort of students, so its courses
        normally share a single headcount). Pass course_id to instead update
        just one course within the group (e.g. an evening/weekend or elective
        row that legitimately carries a different count).
        """
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        group_id = request.POST.get("group_id")
        course_id = request.POST.get("course_id")  # optional — single-course scope
        number_of_students = request.POST.get("number_of_students")

        try:
            group = StudentGroup.objects.get(pk=group_id, program__department=dept)
        except StudentGroup.DoesNotExist:
            return JsonResponse({
                "status": "error",
                "message": "Group not found."
            }, status=404)

        try:
            number_of_students = int(number_of_students)
        except (TypeError, ValueError):
            return JsonResponse({
                "status": "error", "message": "Enrollment must be a whole number."
            }, status=400)
        if number_of_students < 0 or number_of_students > 10000:
            return JsonResponse({
                "status": "error", "message": "Enrollment must be between 0 and 10000."
            }, status=400)

        qs = group.course_allocations.all()
        if course_id:
            qs = qs.filter(pk=course_id)
            if not qs.exists():
                return JsonResponse({
                    "status": "error", "message": "Course not found in this group."
                }, status=404)

        with transaction.atomic():
            # Capture which rows were touched first so the frontend can
            # patch just those table cells in place instead of reloading.
            updated_ids = list(qs.values_list("id", flat=True))
            updated = qs.update(number_of_students=number_of_students)

        return JsonResponse({
            "status": "success",
            "updated_count": updated,
            "updated_ids": updated_ids,
            "number_of_students": number_of_students,
            "message": f"Updated enrollment to {number_of_students} for {updated} course(s) in {group.name}.",
        })

    @staticmethod
    def handle(request, action):
        """Route student group actions."""
        if action == "list_student_groups":
            return StudentGroupService.list_groups(request)
        elif action == "create_student_group":
            return StudentGroupService.create_group(request)
        elif action == "delete_student_group":
            return StudentGroupService.delete_group(request)
        elif action == "assign_courses_to_student_group":
            return StudentGroupService.assign_courses_to_group(request)
        elif action == "get_available_courses_for_student_group":
            return StudentGroupService.get_available_courses(request)
        elif action == "get_student_group_detail":
            return StudentGroupService.get_group_detail(request)
        elif action == "update_student_group_enrollment":
            return StudentGroupService.update_group_enrollment(request)
        return JsonResponse({"status": "error", "message": "Invalid student group action"})


# ============================================================
# Combined Course Group Services
# ============================================================

# ------------------------------------------------------------------------
# Combined-group timetable reconciliation
#
# Bug this fixes: a course could already be individually scheduled
# (have its own Timetable row) before it was combined with others on
# the COD panel. Combining only linked the allocations via the M2M
# `CombinedCourseGroup.allocations` — it never touched the underlying
# Timetable rows, so each member kept sitting in its own separate
# day/time/venue slot and the timetable grid showed the "combined"
# course as several individual entries instead of one.
#
# Fix: every time group membership changes (create_combined_group,
# add_to_combined_group), reconcile so the WHOLE group ends up sharing
# ONE physical placement:
#   - If members already scheduled somewhere disagree, evaluate every
#     one of their existing slots as a candidate host for the full
#     combined class and pick the first one that doesn't collide with
#     anything else (venue capacity, another booking in that venue,
#     lecturer double-booking, or a hard LecturerBlockedSlot).
#   - If every candidate slot collides, fall back to the best-ranked
#     one anyway and place the group there (a human can resolve the
#     resulting conflict from the normal conflicts view) — better than
#     leaving members scattered across incompatible slots.
#   - Members without their own row get one created at the winning
#     slot; members whose row was NOT the winner have it removed.
#
# `reconcile_combined_group_placements` (management command) runs the
# exact same logic over every existing CombinedCourseGroup, so groups
# combined before this fix existed get straightened out too.
# ------------------------------------------------------------------------

def _slot_key(entry):
    return (entry.day, entry.start_time, entry.end_time, entry.venue_id)


def _combined_group_slot_collides(day, start, end, venue, venue_id, member_ids,
                                   total_students, lecturer_id):
    """
    True if hosting the WHOLE combined group at (day, start, end, venue)
    would collide with something outside the group itself.
    """
    from timetable.models import Timetable, LecturerBlockedSlot

    if venue is not None and venue.capacity and total_students > venue.capacity:
        return True

    # Another (non-member) booking in the same venue overlapping this time?
    if Timetable.objects.filter(
        day=day, venue_id=venue_id,
        start_time__lt=end, end_time__gt=start,
    ).exclude(course_allocation_id__in=member_ids).exists():
        return True

    if lecturer_id:
        # Lecturer already booked elsewhere (non-member) at an overlapping time?
        if Timetable.objects.filter(
            day=day, course_allocation__lecturer_id=lecturer_id,
            start_time__lt=end, end_time__gt=start,
        ).exclude(course_allocation_id__in=member_ids).exists():
            return True

        # Hard lecturer-blocked slot (whole day, or overlapping range)?
        if LecturerBlockedSlot.objects.filter(
            lecturer_id=lecturer_id, day=day, is_active=True,
        ).filter(
            Q(start_time__isnull=True, end_time__isnull=True) |
            Q(start_time__lt=end, end_time__gt=start)
        ).exists():
            return True

    return False


def _find_free_slot_for_allocation_set(alloc_ids, lecturer_id, total_students,
                                        program_id=None, alloc_year=None,
                                        sample_allocation=None, prefer_day=None):
    """
    Search the live slot catalog (SchedulerConfig-driven, same source
    `simulate_move`'s recommendation engine uses) for a free
    (day, start, end, venue) that:
      - is not in a hard-blocked venue (VenueBlock),
      - doesn't collide on venue/lecturer/capacity for any allocation in
        `alloc_ids` (via the same `_combined_group_slot_collides` check
        combined-group reconciliation already uses), and
      - when `program_id`/`alloc_year` are given, doesn't clash with
        another course in the same program-year (honouring the normal
        elective/selection/combined-group scheduling exemptions).

    Returns (day, start_time, end_time, venue_obj) for the first fit found,
    or None if nothing in the current configuration works — callers should
    treat None as "leave unscheduled", not as an error.
    """
    from datetime import datetime as _dt
    from timetable.simulate_move import _get_slot_catalog
    from timetable.timetable_panel import _get_year_value, is_scheduling_exempt
    from timetable.models import Timetable
    from room_management.models import Venue, VenueBlock

    alloc_ids = list(alloc_ids)

    blocked_venue_ids = set(
        VenueBlock.objects.filter(is_active=True).values_list("venue_id", flat=True)
    )
    venues = [v for v in Venue.objects.all() if v.id not in blocked_venue_ids]
    if not venues:
        return None

    # Try venues that comfortably fit the class first; venues with no
    # capacity on record sort as "fits" too rather than being penalised.
    def _cap_key(v):
        cap = v.capacity or 0
        fits = cap == 0 or cap >= total_students
        return (0 if fits else 1, cap if cap else 10 ** 6)
    venues.sort(key=_cap_key)

    catalog = _get_slot_catalog()
    if prefer_day:
        catalog = sorted(catalog, key=lambda s: 0 if s["day"] == prefer_day else 1)

    program_alloc_ids = None
    if program_id and alloc_year is not None:
        program_alloc_ids = list(
            CourseAllocation.objects.filter(program_id=program_id)
            .exclude(id__in=alloc_ids).values_list("id", flat=True)
        )

    for slot in catalog:
        day = slot["day"]
        start_t = _dt.strptime(slot["start"], "%H:%M").time()
        end_t = _dt.strptime(slot["end"], "%H:%M").time()

        if program_alloc_ids:
            clash = False
            for existing in Timetable.objects.filter(
                course_allocation_id__in=program_alloc_ids, day=day,
                start_time__lt=end_t, end_time__gt=start_t,
            ).select_related(
                "course_allocation", "course_allocation__selection_group",
                "course_allocation__program_course",
            ):
                existing_year = _get_year_value(existing.course_allocation)
                if existing_year != alloc_year:
                    continue
                if sample_allocation and is_scheduling_exempt(sample_allocation, existing.course_allocation):
                    continue
                clash = True
                break
            if clash:
                continue

        for v in venues:
            if _combined_group_slot_collides(
                day, start_t, end_t, v, v.id, set(alloc_ids), total_students, lecturer_id,
            ):
                continue
            return (day, start_t, end_t, v)

    return None


def reconcile_combined_group_placement(group):
    """
    Make sure every allocation in `group` shares one Timetable placement.
    Safe to call any time group membership changes, and safe to re-run
    on an already-consistent group (it's a no-op in that case).

    Returns a dict describing what happened, for logging/audit purposes:
        {"action": "no_members" | "no_existing_entries" | "already_unified"
                    | "unified" | "unified_forced_collision",
         "slot": (day, start, end, venue_id) or None,
         "removed_entry_ids": [...], "created_for_allocation_ids": [...]}
    """
    from timetable.models import Timetable

    members = list(group.allocations.select_related('lecturer').all())
    if len(members) < 2:
        return {"action": "no_members"}

    member_ids = [m.id for m in members]
    existing = list(
        Timetable.objects
        .filter(course_allocation_id__in=member_ids)
        .select_related('venue')
        .order_by('id')
    )

    if not existing:
        # No member has a Timetable row anywhere yet — search the live
        # scheduling configuration for a fresh slot instead of just giving
        # up. Combined-group placement historically only checked venue +
        # lecturer collisions (see _combined_group_slot_collides above), so
        # the search below is intentionally kept to that same scope for
        # consistency; it additionally skips any VenueBlock'd room.
        found = _find_free_slot_for_allocation_set(
            member_ids, lecturer_id, total_students,
        )
        if not found:
            return {"action": "no_existing_entries"}
        day, start_t, end_t, venue_obj = found
        bucket = {"member_ids": set(), "venue": venue_obj}
        created = _place_all_members_at_slot(
            members, (day, start_t, end_t, venue_obj.id), bucket,
        )
        return {"action": "placed_new_slot", "slot": (day, start_t, end_t, venue_obj.id),
                "removed_entry_ids": [], "created_for_allocation_ids": created}

    slots = {}
    for e in existing:
        key = _slot_key(e)
        bucket = slots.setdefault(key, {"entries": [], "member_ids": set(), "venue": e.venue})
        bucket["entries"].append(e)
        bucket["member_ids"].add(e.course_allocation_id)

    total_students = sum(m.number_of_students or 0 for m in members)
    lecturer_id = group.lecturer_id

    if len(slots) == 1:
        # A single existing slot is only safe to trust if it isn't also
        # still held by something outside this group — e.g. a course that
        # just left another combined group can arrive here still carrying
        # its old Timetable row at the old group's slot, which the old
        # group's remaining members may still occupy. Run the same
        # collision check the multi-slot branch uses instead of assuming
        # "one slot" means "safe slot".
        (winner, bucket), = slots.items()
        day, start, end, venue_id = winner
        if _combined_group_slot_collides(
            day, start, end, bucket["venue"], venue_id,
            bucket["member_ids"], total_students, lecturer_id,
        ):
            # Stale/colliding slot inherited from elsewhere — don't unify
            # onto it. Wipe it and leave the group unscheduled so it gets
            # a real slot (manually or via the autoscheduler) instead of
            # silently overlapping another booking.
            Timetable.objects.filter(id__in=[e.id for e in bucket["entries"]]).delete()
            return {"action": "collision_left_unscheduled", "slot": None,
                    "removed_entry_ids": [e.id for e in bucket["entries"]],
                    "created_for_allocation_ids": []}

        created = _place_all_members_at_slot(members, winner, bucket)
        return {"action": "already_unified", "slot": winner,
                "removed_entry_ids": [], "created_for_allocation_ids": created}

    # Rank candidates: whichever slot already hosts the most members wins
    # ties (fewest changes needed); then earliest-created entry first.
    ordered_keys = sorted(
        slots.keys(),
        key=lambda k: (-len(slots[k]["member_ids"]), min(e.id for e in slots[k]["entries"])),
    )

    winner = None
    for key in ordered_keys:
        day, start, end, venue_id = key
        if not _combined_group_slot_collides(
            day, start, end, slots[key]["venue"], venue_id,
            slots[key]["member_ids"], total_students, lecturer_id,
        ):
            winner = key
            break

    forced = winner is None
    if winner is None:
        winner = ordered_keys[0]

    removed_ids = []
    for key, bucket in slots.items():
        if key != winner:
            removed_ids.extend(e.id for e in bucket["entries"])
    if removed_ids:
        Timetable.objects.filter(id__in=removed_ids).delete()

    created = _place_all_members_at_slot(members, winner, slots[winner])

    return {
        "action": "unified_forced_collision" if forced else "unified",
        "slot": winner,
        "removed_entry_ids": removed_ids,
        "created_for_allocation_ids": created,
    }


def _place_all_members_at_slot(members, slot_key, bucket):
    """Create a Timetable row at slot_key for any member that doesn't
    already have one there. Returns the list of allocation ids a new
    row was created for."""
    from timetable.models import Timetable

    day, start, end, venue_id = slot_key
    already_there = bucket["member_ids"]
    created = []
    for m in members:
        if m.id in already_there:
            continue
        Timetable.objects.create(
            course_allocation=m, venue_id=venue_id, day=day,
            start_time=start, end_time=end,
        )
        created.append(m.id)
    return created


def _remove_allocation_from_group_core(group, allocation, dept):
    """
    Core removal logic shared by `remove_from_combined_group` and
    `move_to_combined_group`. Must be called inside an active
    transaction.atomic() block. Returns:
        {"deleted": bool, "remaining_count": int, "total_students": float,
         "placement": None | {"action": "placed", "slot": (...)}
                            | {"action": "unscheduled"}}
    `placement` is None when the departing allocation didn't need any
    normalization (it either kept its own standalone slot, or never had a
    Timetable row to begin with).
    """
    from timetable.models import Timetable
    from timetable.timetable_panel import _get_year_value

    # Capture the group's current shared slot BEFORE we touch membership,
    # so we know what to strip from the departing allocation. Without this,
    # the leaving course keeps its old Timetable row at the group's slot —
    # and if it's then folded into a *different* combined group,
    # reconcile_combined_group_placement() can treat that stale row as a
    # legitimate existing placement and drag the new group onto the same
    # slot the old group is still occupying (the "both groups on same
    # slot" symptom).
    remaining_before = list(
        group.allocations.exclude(pk=allocation.id).values_list("id", flat=True)
    )
    stale_entries = list(Timetable.objects.filter(course_allocation_id=allocation.id))
    stripped = False
    if stale_entries and remaining_before:
        stale_keys = {_slot_key(e) for e in stale_entries}
        # Only strip rows that actually match a slot the rest of the group
        # still occupies — don't blow away an entry that happens to belong
        # to this course for an unrelated reason.
        group_still_there = set(
            _slot_key(e) for e in
            Timetable.objects.filter(course_allocation_id__in=remaining_before)
        )
        to_delete_ids = [
            e.id for e in stale_entries if _slot_key(e) in (stale_keys & group_still_there)
        ]
        if to_delete_ids:
            Timetable.objects.filter(id__in=to_delete_ids).delete()
            stripped = True

    group.allocations.remove(allocation)

    if group.allocations.count() < 2:
        group.delete()
        deleted = True
        remaining_count = 0
        total_students = 0
    else:
        deleted = False
        # Reassign primary if either: (a) the removed allocation WAS the
        # primary, or (b) the group already had no primary at all — self-
        # healing unconditionally here mirrors the add-side behaviour.
        if group.primary_allocation_id == allocation.id or not group.primary_allocation_id:
            new_primary = group.allocations.filter(department=dept).first()
            if not new_primary:
                new_primary = group.allocations.first()
            group.primary_allocation = new_primary
            group.save()
        remaining_count = group.allocations.count()
        total_students = group.total_students()

    # Normalize the departing allocation: if leaving the group stripped its
    # only Timetable row (the common case — it was only ever booked at the
    # group's shared slot), try to find it a fresh, non-colliding,
    # unblocked-venue slot instead of just abandoning it mid-air. If
    # nothing in the current configuration fits, leave it unscheduled
    # rather than force a collision.
    placement = None
    if stripped and not Timetable.objects.filter(course_allocation_id=allocation.id).exists():
        try:
            found = _find_free_slot_for_allocation_set(
                [allocation.id], allocation.lecturer_id, allocation.number_of_students or 0,
                program_id=allocation.program_id,
                alloc_year=_get_year_value(allocation),
                sample_allocation=allocation,
            )
        except Exception:
            logger.exception(
                "Slot search failed while normalizing removed allocation %s", allocation.id
            )
            found = None

        if found:
            day, start_t, end_t, venue_obj = found
            Timetable.objects.create(
                course_allocation=allocation, venue=venue_obj,
                day=day, start_time=start_t, end_time=end_t,
            )
            placement = {"action": "placed", "slot": (day, start_t, end_t, venue_obj.id)}
        else:
            placement = {"action": "unscheduled"}

    return {
        "deleted": deleted,
        "remaining_count": remaining_count,
        "total_students": total_students,
        "placement": placement,
    }


def _add_allocation_to_group_core(group, allocation, dept):
    """
    Core add logic shared by `add_to_combined_group` and
    `move_to_combined_group`. Must be called inside an active
    transaction.atomic() block. Returns:
        {"remaining_count": int, "total_students": float, "placement": {...}}
    `placement` is whatever `reconcile_combined_group_placement` reports —
    folded onto an existing shared slot, placed at a freshly found one, or
    left unscheduled if nothing fits.
    """
    group.allocations.add(allocation)

    # Keep the newly-added allocation's own lecturer field in sync with the
    # group's, same as create_combined_group does — otherwise the
    # auto-scheduler's conflict checks (which key off each allocation's own
    # lecturer_id, not the group's) can miss or misattribute conflicts for
    # this course.
    if group.lecturer_id and allocation.lecturer_id != group.lecturer_id:
        allocation.lecturer = group.lecturer
        allocation.save(update_fields=["lecturer"])

    if not group.primary_allocation_id:
        group.primary_allocation = group.allocations.first()
        group.save()

    # The newly-added course (or an existing member) may already hold its
    # own individual timetable entry — fold everyone onto one shared
    # placement instead of leaving it behind; or, if nobody in the group
    # has a placement yet, search for a fresh one.
    try:
        reconcile_result = reconcile_combined_group_placement(group)
        logger.info(
            "reconcile_combined_group_placement on add for group %s: %s",
            group.id, reconcile_result,
        )
    except Exception:
        reconcile_result = None
        logger.exception(
            "reconcile_combined_group_placement failed for group %s", group.id
        )

    return {
        "remaining_count": group.allocations.count(),
        "total_students": group.total_students(),
        "placement": reconcile_result,
    }


class CombinedCourseGroupService:
    """Service to handle combined course group operations."""
    
    @staticmethod
    def list_available_for_combination(request):
        """
        List course allocations that can be combined.
        Returns allocations that match the base course code.
        When include_grouped=1, also returns already-grouped allocations
        (marked with already_grouped=True) so the UI can show them greyed out.
        """
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        base_code = request.POST.get('base_code', '').strip()
        if not base_code:
            return JsonResponse({"status": "error", "message": "Base course code required"}, status=400)

        include_grouped = request.POST.get('include_grouped', '0') == '1'

        # Normalize the user input so "cosc471", "COSC471", "COSC 471" all resolve to "COSC 471"
        base_code = normalize_code(base_code)

        # Strip any group suffix the user may have typed (e.g. "COSC 471-A" → "COSC 471")
        base_code, _ = strip_group_suffix(base_code)
        base_code = normalize_code(base_code)

        # ------------------------------------------------------------------
        # Matching strategy — CANONICAL KEY, not a hand-rolled regex.
        #
        # The previous implementation matched course_code against an anchored
        # regex built from an ESCAPED, LITERAL copy of the base code (with a
        # small menu of separator characters spliced in). Because the regex
        # was anchored at both ends (^...$), any stored code that didn't
        # fit the exact shape the regex author anticipated — extra spaces,
        # a word instead of a single letter after the separator, a slightly
        # different separator, etc. — silently failed to match and never
        # reached the checkbox list in the UI, even though it was plainly
        # "the same base course". That's why typing a course code would
        # only surface a handful of the real sections.
        #
        # Fix: normalize every stored code down to its bare alphanumeric
        # "canonical key" (see program_management.code_utils) and compare
        # that to the canonical key of the base code in plain Python. This
        # is immune to spacing/punctuation differences by construction —
        # "COSC 471-A", "COSC471(A)", "cosc 471 - a" all canonicalize to
        # "COSC471A" regardless of how the separator was typed.
        #
        # We still reject a suffix that starts with a DIGIT (e.g. base
        # "COSC 471" must NOT swallow the unrelated course "COSC 4712"),
        # since this app's own group-lettering convention (see
        # append_group/index_to_letters) always uses letters, never digits,
        # for a combinable section suffix.
        # ------------------------------------------------------------------
        base_no_space = base_code.replace(" ", "")
        canon_base = _canonical_course_key(base_code)

        def _matches_base(course_code: str) -> bool:
            canon = _canonical_course_key(course_code)
            if not canon.startswith(canon_base):
                return False
            suffix = canon[len(canon_base):]
            if suffix == "":
                return True  # exact base code, no group suffix at all
            return suffix.isalpha()  # letter-only suffix (A, B, AA, AB, ...)

        try:
            # Broad, cheap DB-level prefilter (superset) so we don't have to
            # pull the whole table into Python: any code that contains the
            # spaceless base is a candidate. The precise, format-agnostic
            # check happens in _matches_base() above.
            candidate_q = Q(course_code__icontains=base_no_space)
            if " " in base_code:
                candidate_q |= Q(course_code__icontains=base_code)

            candidates = (
                CourseAllocation.objects
                .filter(candidate_q)
                .select_related('department', 'origin_department', 'program', 'lecturer')
                .distinct()
            )

            allocations = [a for a in candidates if _matches_base(a.course_code)]
            matched_ids = [a.id for a in allocations]

            # Collect IDs already in a combined group.
            # Guarded separately: combined_groups relation may not exist if the
            # migration hasn't been applied yet.
            try:
                grouped_ids = set(
                    CourseAllocation.objects
                    .filter(pk__in=matched_ids, combined_groups__isnull=False)
                    .values_list('id', flat=True)
                )
            except Exception:
                grouped_ids = set()

            if not include_grouped:
                allocations = [a for a in allocations if a.id not in grouped_ids]

        except Exception as exc:
            logger.exception("list_available_for_combination DB error")
            return JsonResponse(
                {"status": "error", "message": f"Database error: {exc}"},
                status=500,
            )

        data = []
        for a in allocations:
            is_own_dept = (a.department_id == dept.id)
            is_origin_dept = (getattr(a, 'origin_department_id', None) == dept.id)
            already_grouped = a.id in grouped_ids
            data.append({
                "id": a.id,
                "course_code": a.course_code,
                "course_name": a.course_name,
                "department_id": a.department_id,
                "department_name": a.department.name,
                "origin_department_name": a.origin_department.name if a.origin_department else "",
                "program": a.program.name if a.program else "",
                "lecturer_id": a.lecturer_id,
                "lecturer_name": a.lecturer.display_name if a.lecturer else "Unassigned",
                "students": a.number_of_students,
                "is_own_dept": is_own_dept,
                "is_origin_dept": is_origin_dept,
                "already_grouped": already_grouped,
                "is_elective": a.is_elective,
                "is_evening_weekend": a.is_evening_weekend,
                "intake": a.intake,
            })

        # Sort: available (not grouped) first, then grouped ones
        data.sort(key=lambda x: (x['already_grouped'], x['course_code']))

        return JsonResponse({
            "status": "success",
            "allocations": data,
            "base_code": base_code,
        })
    
    @staticmethod
    def create_combined_group(request):
        """Create a new combined group from selected allocations."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        allocation_ids = request.POST.getlist('allocation_ids[]')
        group_name = request.POST.get('group_name', '').strip()
        lecturer_id = request.POST.get('lecturer_id')
        base_code = request.POST.get('base_code', '').strip()
        
        if len(allocation_ids) < 2:
            return JsonResponse({
                "status": "error",
                "message": "At least two allocations are required to create a combined group"
            }, status=400)
        
        if not group_name:
            # Auto-generate group name from base course code
            base_code_clean = base_code.replace(' ', '_')
            group_name = f"{base_code_clean}_COMBINED_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Get all allocations and validate
        allocations = CourseAllocation.objects.filter(
            pk__in=allocation_ids
        ).select_related('department', 'lecturer')
        
        if allocations.count() != len(allocation_ids):
            return JsonResponse({
                "status": "error",
                "message": "Some allocations not found"
            }, status=400)

        # Don't allow an allocation that's already grouped elsewhere.
        # Without this check, an allocation can end up an M2M member of TWO
        # CombinedCourseGroup rows at once — e.g. the frontend deletes an
        # old combined group and creates a new, differently-composed one,
        # but the delete request fails/races and the old group row survives.
        # _get_combined_group_for_allocation() then has no reliable way to
        # know which group "owns" that allocation and just picks whichever
        # sorts first, so Resolve on the timetable panel can silently glue
        # the old group's members back onto the new group's placement —
        # two combined groups merged into one class, which must never
        # happen. Mirrors the same guard already in add_to_combined_group().
        already_grouped = (
            CourseAllocation.objects
            .filter(pk__in=allocation_ids, combined_groups__isnull=False)
            .select_related("department")
            .prefetch_related("combined_groups")
            .distinct()
        )
        if already_grouped.exists():
            conflicts = [
                {
                    "id": a.id,
                    "course_code": a.course_code,
                    "department": a.department.name if a.department else "",
                    "existing_group_codes": [g.group_code for g in a.combined_groups.all()],
                }
                for a in already_grouped
            ]
            return JsonResponse({
                "status": "error",
                "message": (
                    "One or more selected allocations already belong to another "
                    "combined group. Delete/remove them from that group first, "
                    "then create this one."
                ),
                "conflicts": conflicts,
            }, status=400)

        # Validate all allocations have the same base course code.
        #
        # BUG FIXED HERE: strip_group_suffix() only strips a single trailing
        # letter-group token ("COSC 471-A" -> "COSC 471"). Real course codes
        # can carry a specialization-stem tag AND a group letter stacked
        # together — e.g. "BOTA 111-MATH-B", "BOTA 111-HSC-A" — and
        # strip_group_suffix's regex requires everything after the digit
        # run to be letters-only up to the end of the string, so "MATH-B"
        # (letters + hyphen + letter) fails to match at all and the code is
        # returned unchanged. That made every one of these compound codes
        # register as its own distinct "base code", falsely blocking a
        # combine of sections that all genuinely share the same base course
        # (e.g. BOTA 111) with the "must have same base course code" error.
        #
        # course_base_key() (from timetable_panel) is the robust version:
        # it takes only the leading LETTER-run + DIGIT-run and discards
        # every trailing tag/letter, however many are stacked, so it
        # correctly treats "BOTA 111-MATH-B" and "BOTA 111-HSC-A" as the
        # same base course. Used here for the actual equality check;
        # strip_group_suffix/normalize_code are still used to produce the
        # clean, human-readable base_course_code stored on the group.
        from timetable.timetable_panel import course_base_key

        base_codes = set()
        comparison_keys = set()
        for a in allocations:
            base, _ = strip_group_suffix(a.course_code)
            base_codes.add(normalize_code(base))
            comparison_keys.add(course_base_key(a.course_code))

        if len(comparison_keys) > 1:
            return JsonResponse({
                "status": "error",
                "message": f"All allocations must have the same base course code. Found: {', '.join(sorted(base_codes))}"
            }, status=400)

        # Prefer the cleanest available display form: if strip_group_suffix
        # actually managed to normalize every code down to one shared
        # base_codes entry, use that (nicely spaced, e.g. "BOTA 111"). If
        # the codes were compound enough that base_codes still disagrees
        # (possible whenever strip_group_suffix bailed on at least one of
        # them) even though comparison_keys agrees, fall back to rebuilding
        # a clean display form directly from the robust key.
        if len(base_codes) == 1:
            base_course_code = list(base_codes)[0]
        else:
            base_course_code = normalize_code(list(comparison_keys)[0])
        
        # Validate lecturer (if specified) matches or is consistent
        lecturer_obj = None
        if lecturer_id:
            lecturer_obj = get_object_or_404(Lecturer, pk=lecturer_id)
        else:
            # Check if all have same lecturer or none
            lecturers = set(a.lecturer_id for a in allocations if a.lecturer_id)
            if len(lecturers) > 1:
                return JsonResponse({
                    "status": "error",
                    "message": "Allocations have different lecturers. Please select a lecturer for the combined group or ensure all have the same lecturer."
                }, status=400)
            elif len(lecturers) == 1:
                lecturer_obj = allocations.filter(lecturer_id=list(lecturers)[0]).first().lecturer
        
        with transaction.atomic():
            # Create combined group
            group = CombinedCourseGroup.objects.create(
                group_code=group_name,
                base_course_code=base_course_code,
                lecturer=lecturer_obj,
                department=dept,
                origin_department=dept,
                created_by=request.user,
            )
            group.allocations.set(allocations)

            # Keep every member allocation's own lecturer field in sync with
            # the group's, since the auto-scheduler's conflict checks
            # (lecturer_busy, etc.) key off each allocation's own
            # lecturer_id, not the group's — leaving them out of sync would
            # let the scheduler double-book the old lecturer or miss
            # conflicts for the new one.
            if lecturer_obj:
                allocations.exclude(lecturer_id=lecturer_obj.id).update(lecturer=lecturer_obj)

            # Set primary allocation (prefer the one from the combining COD's
            # own department; fall back to the first allocation so a
            # cross-department combine — e.g. every member belongs to some
            # OTHER department — still gets a primary. Without this fallback
            # the group is created with primary_allocation left NULL, and
            # every member then shows up individually in the timetable
            # panel's unscheduled dropdown instead of collapsing into one
            # "Combined" row, since _build_combined_group_exclude_ids()
            # deliberately excludes nothing for a primary-less group.
            primary = allocations.filter(department=dept).first()
            if not primary:
                primary = allocations.first()
            group.primary_allocation = primary
            group.save()

            # These allocations may have been individually timetabled
            # before being combined here — collapse them onto one shared
            # placement instead of leaving each in its old separate slot.
            try:
                reconcile_result = reconcile_combined_group_placement(group)
                logger.info(
                    "reconcile_combined_group_placement on create for group %s: %s",
                    group.id, reconcile_result,
                )
            except Exception:
                logger.exception(
                    "reconcile_combined_group_placement failed for new group %s", group.id
                )

            # Remember this combination in the knowledge base so
            # auto-allocate can recreate it automatically next time it
            # rebuilds this department's allocations.
            _remember_course_combination_template(
                dept, base_course_code, lecturer_obj, allocations, request.user,
            )

            # Create audit log entry if available
            try:
                from audit_management.models import AuditLog
                AuditLog.objects.create(
                    user=request.user,
                    action="CREATE_COMBINED_GROUP",
                    model_name="CombinedCourseGroup",
                    object_id=group.id,
                    details={
                        "group_code": group.group_code,
                        "allocations": list(allocation_ids),
                        "total_students": group.total_students(),
                    }
                )
            except ImportError:
                pass
        
        return JsonResponse({
            "status": "success",
            "group": {
                "id": group.id,
                "group_code": group.group_code,
                "base_course_code": group.base_course_code,
                "lecturer_id": group.lecturer_id,
                "lecturer_name": group.lecturer.display_name if group.lecturer else "",
                "total_students": group.total_students(),
                "allocation_count": group.allocations.count(),
                "allocations": [
                    {
                        "id": a.id,
                        "course_code": a.course_code,
                        "department_name": a.department.name,
                        "students": a.number_of_students,
                    }
                    for a in group.allocations.all()
                ],
            }
        })
    
    @staticmethod
    def list_combined_groups(request):
        """List all combined groups for this department."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        # A department can see a combined group in two capacities:
        #   • department == dept       → they currently hold it and are
        #                                 responsible for allocating a lecturer
        #                                 (can submit / split / edit it)
        #   • origin_department == dept → they originally combined it and are
        #                                 servicing it, but it has been
        #                                 submitted elsewhere for lecturer
        #                                 allocation (read-only here)
        groups = (
            CombinedCourseGroup.objects
            .filter(Q(department=dept) | Q(origin_department=dept))
            .select_related('department', 'origin_department')
            .prefetch_related('allocations__department')
        )

        data = []
        for g in groups:
            breakdown = []
            for a in g.allocations.select_related('department'):
                breakdown.append({
                    "department_name": a.department.name,
                    "course_code": a.course_code,
                    "students": a.number_of_students,
                })
            is_holder = (g.department_id == dept.id)
            is_origin = (g.origin_department_id == dept.id)
            data.append({
                "id": g.id,
                "group_code": g.group_code,
                "base_course_code": g.base_course_code,
                "lecturer_name": g.lecturer.display_name if g.lecturer else "",
                "total_students": g.total_students(),
                "allocation_count": g.allocations.count(),
                "breakdown": breakdown,
                "created_at": g.created_at.strftime("%Y-%m-%d %H:%M"),
                "department_id": g.department_id,
                "department_name": g.department.name if g.department else "",
                "origin_department_id": g.origin_department_id,
                "origin_department_name": g.origin_department.name if g.origin_department else "",
                "is_holder": is_holder,      # can edit/submit/split/assign lecturer
                "is_origin": is_origin,      # originally combined it here
                "submitted_elsewhere": is_origin and not is_holder,
            })

        return JsonResponse({"status": "success", "groups": data})
    
    @staticmethod
    def get_combined_group_detail(request):
        """Get details of a specific combined group."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        group_id = request.POST.get("group_id")
        # Viewable by whoever currently holds it (can act on it) OR whoever
        # originated it (read-only visibility into where it was submitted).
        group = get_object_or_404(
            CombinedCourseGroup.objects.filter(Q(department=dept) | Q(origin_department=dept)),
            pk=group_id,
        )

        allocations_data = []
        for a in group.allocations.select_related('department', 'program', 'lecturer'):
            allocations_data.append({
                "id": a.id,
                "course_code": a.course_code,
                "course_name": a.course_name,
                "department_id": a.department_id,
                "department_name": a.department.name,
                "program_name": a.program.name if a.program else "",
                "lecturer_name": a.lecturer.display_name if a.lecturer else "",
                "students": a.number_of_students,
                "is_own_dept": (a.department_id == dept.id),
            })

        is_holder = (group.department_id == dept.id)

        return JsonResponse({
            "status": "success",
            "group": {
                "id": group.id,
                "group_code": group.group_code,
                "base_course_code": group.base_course_code,
                "lecturer_id": group.lecturer_id,
                "lecturer_name": group.lecturer.display_name if group.lecturer else "",
                "total_students": group.total_students(),
                "allocations": allocations_data,
                "primary_allocation_id": group.primary_allocation_id,
                "created_at": group.created_at.strftime("%Y-%m-%d %H:%M"),
                "department_id": group.department_id,
                "department_name": group.department.name if group.department else "",
                "origin_department_id": group.origin_department_id,
                "origin_department_name": group.origin_department.name if group.origin_department else "",
                "is_holder": is_holder,
                "submitted_elsewhere": (group.origin_department_id == dept.id) and not is_holder,
            }
        })
    
    @staticmethod
    def submit_combined_group(request):
        """
        Submit a combined group to another department for lecturer allocation.

        Mirrors the CourseAllocation department / origin_department pattern:
          - `department` becomes the target (allocating) department — they
            now own the group and can assign a lecturer, split it, etc.
          - `origin_department` never changes — it stays whichever COD first
            combined the group, so it keeps appearing (read-only) in that
            department's list too.
        """
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        group_id = request.POST.get("group_id")
        target_department_id = request.POST.get("target_department_id")

        if not target_department_id:
            return JsonResponse(
                {"status": "error", "message": "Target department is required."}, status=400
            )

        # Only the current holder (allocating department) can submit it onward.
        group = get_object_or_404(CombinedCourseGroup, pk=group_id, department=dept)

        target_dept = get_object_or_404(Department, pk=target_department_id)
        if target_dept.id == dept.id:
            return JsonResponse(
                {"status": "error", "message": "That is already this department."}, status=400
            )

        with transaction.atomic():
            group.department = target_dept
            # origin_department is left untouched on purpose.
            group.save(update_fields=["department", "updated_at"])

            try:
                from audit_management.models import AuditLog
                AuditLog.objects.create(
                    user=request.user,
                    action="SUBMIT_COMBINED_GROUP",
                    model_name="CombinedCourseGroup",
                    object_id=group.id,
                    details={
                        "group_code": group.group_code,
                        "from_department": dept.name,
                        "to_department": target_dept.name,
                    }
                )
            except ImportError:
                pass

        return JsonResponse({
            "status": "success",
            "message": f"\"{group.group_code}\" submitted to {target_dept.name} for lecturer allocation.",
            "group_id": group.id,
            "new_department_id": target_dept.id,
            "new_department_name": target_dept.name,
        })

    @staticmethod
    def split_combined_group(request):
        """
        Split a combined group (typically after it's been submitted to this
        department) into two or more separate Combined Groups, so each part
        can be allocated its own lecturer.

        Expects repeated POST keys:
          splits[0][]=alloc_id_a&splits[0][]=alloc_id_b&splits[1][]=alloc_id_c...
        Every allocation currently in the group must appear in exactly one
        split. Each split with 2+ allocations becomes a new CombinedCourseGroup
        (department = this dept, origin_department = the parent's origin,
        split_from = the parent group). A split left with exactly 1
        allocation is simply released back to being an ordinary, ungrouped
        CourseAllocation. The original (parent) group is deleted once it has
        been fully split.
        """
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        group_id = request.POST.get("group_id")
        # Only the current holder can split it — that's who is responsible
        # for lecturer allocation right now.
        group = get_object_or_404(CombinedCourseGroup, pk=group_id, department=dept)

        # Parse splits[N][] lists from POST.
        splits = []
        i = 0
        while True:
            key = f"splits[{i}][]"
            ids = request.POST.getlist(key)
            if not ids:
                break
            splits.append(ids)
            i += 1

        if len(splits) < 2:
            return JsonResponse({
                "status": "error",
                "message": "Provide at least two splits to divide the group into."
            }, status=400)

        original_alloc_ids = set(str(a.id) for a in group.allocations.all())
        provided_ids = [aid for split in splits for aid in split]

        if len(provided_ids) != len(set(provided_ids)):
            return JsonResponse({
                "status": "error",
                "message": "An allocation was placed in more than one split."
            }, status=400)

        if set(provided_ids) != original_alloc_ids:
            return JsonResponse({
                "status": "error",
                "message": "Every allocation currently in the group must be assigned to exactly one split."
            }, status=400)

        lecturer_ids = request.POST.getlist("split_lecturer_ids[]")  # parallel to splits[], "" allowed

        new_groups = []
        released_allocations = []

        with transaction.atomic():
            for idx, alloc_ids in enumerate(splits):
                allocs = list(CourseAllocation.objects.filter(pk__in=alloc_ids))
                lecturer_obj = None
                if idx < len(lecturer_ids) and lecturer_ids[idx]:
                    lecturer_obj = get_object_or_404(Lecturer, pk=lecturer_ids[idx])

                if len(allocs) < 2:
                    # Nothing left to combine — release it as a standalone allocation.
                    if lecturer_obj and allocs:
                        allocs[0].lecturer = lecturer_obj
                        allocs[0].save(update_fields=["lecturer"])
                    released_allocations.extend(a.id for a in allocs)
                    continue

                base_codes = set()
                for a in allocs:
                    base, _ = strip_group_suffix(a.course_code)
                    base_codes.add(normalize_code(base))
                base_course_code = list(base_codes)[0] if len(base_codes) == 1 else group.base_course_code

                new_group = CombinedCourseGroup.objects.create(
                    group_code=f"{group.group_code}_SPLIT{idx + 1}",
                    base_course_code=base_course_code,
                    lecturer=lecturer_obj,
                    department=dept,
                    origin_department=group.origin_department,
                    created_by=request.user,
                    split_from_group_code=group.group_code,
                    split_from_group_id=group.id,
                )
                new_group.allocations.set(allocs)
                new_group.primary_allocation = allocs[0]
                new_group.save(update_fields=["primary_allocation"])

                # Same as create_combined_group — keep every member's own
                # lecturer field in sync with the new split group's lecturer.
                if lecturer_obj:
                    CourseAllocation.objects.filter(
                        pk__in=[a.id for a in allocs]
                    ).exclude(lecturer_id=lecturer_obj.id).update(lecturer=lecturer_obj)

                new_groups.append({
                    "id": new_group.id,
                    "group_code": new_group.group_code,
                    "lecturer_name": lecturer_obj.display_name if lecturer_obj else "",
                    "allocation_count": len(allocs),
                    "total_students": new_group.total_students(),
                })

            try:
                from audit_management.models import AuditLog
                AuditLog.objects.create(
                    user=request.user,
                    action="SPLIT_COMBINED_GROUP",
                    model_name="CombinedCourseGroup",
                    object_id=group.id,
                    details={
                        "group_code": group.group_code,
                        "new_groups": [g["group_code"] for g in new_groups],
                        "released_allocation_ids": released_allocations,
                    }
                )
            except ImportError:
                pass

            group.delete()

        return JsonResponse({
            "status": "success",
            "message": f"Split into {len(new_groups)} combined group(s)"
                       + (f" and {len(released_allocations)} standalone allocation(s)." if released_allocations else "."),
            "new_groups": new_groups,
            "released_allocation_ids": released_allocations,
        })

    @staticmethod
    def remove_from_combined_group(request):
        """Remove an allocation from a combined group."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        group_id = request.POST.get("group_id")
        allocation_id = request.POST.get("allocation_id")

        group = get_object_or_404(CombinedCourseGroup, pk=group_id, department=dept)
        allocation = get_object_or_404(CourseAllocation, pk=allocation_id)

        if allocation not in group.allocations.all():
            return JsonResponse({
                "status": "error",
                "message": "Allocation not in this group"
            }, status=400)

        with transaction.atomic():
            result = _remove_allocation_from_group_core(group, allocation, dept)

        if result.get("deleted"):
            return JsonResponse({
                "status": "success",
                "message": "Group deleted (less than 2 allocations remaining)",
                "deleted": True,
                "placement": result.get("placement"),
            })

        return JsonResponse({
            "status": "success",
            "remaining_count": result["remaining_count"],
            "total_students": result["total_students"],
            "placement": result.get("placement"),
        })

    @staticmethod
    def add_to_combined_group(request):
        """Add an existing allocation to a combined group."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        group_id = request.POST.get("group_id")
        allocation_id = request.POST.get("allocation_id")

        group = get_object_or_404(CombinedCourseGroup, pk=group_id, department=dept)
        allocation = get_object_or_404(CourseAllocation, pk=allocation_id)

        if allocation in group.allocations.all():
            return JsonResponse({
                "status": "error",
                "message": "That allocation is already in this group."
            }, status=400)

        # Don't allow an allocation that's already grouped elsewhere.
        if allocation.combined_groups.exclude(pk=group.id).exists():
            return JsonResponse({
                "status": "error",
                "message": "That allocation already belongs to a different combined group."
            }, status=400)

        with transaction.atomic():
            result = _add_allocation_to_group_core(group, allocation, dept)

        return JsonResponse({
            "status": "success",
            "remaining_count": result["remaining_count"],
            "total_students": result["total_students"],
            "placement": result.get("placement"),
        })

    @staticmethod
    def move_to_combined_group(request):
        """
        Move an allocation from one combined group to another (right-click
        "Move to Combined Group…" on the allocations table). Both groups
        must share the same base course code — you can move EDFO 211-M
        between two EDFO 211 combined groups, not into a MATH 122 group.

        Implemented as an atomic remove-core + add-core so the departing
        group gets exactly the same re-normalization as a standalone
        removal (re-homed to a free slot or left unscheduled), and the
        destination group gets exactly the same reconciliation as a
        standalone add (folded onto the shared slot, or placed fresh).
        """
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        allocation_id = request.POST.get("allocation_id")
        from_group_id = request.POST.get("from_group_id")
        to_group_id = request.POST.get("to_group_id")

        if not (allocation_id and from_group_id and to_group_id):
            return JsonResponse({
                "status": "error", "message": "allocation_id, from_group_id and to_group_id are required."
            }, status=400)

        if str(from_group_id) == str(to_group_id):
            return JsonResponse({"status": "error", "message": "That course is already in this group."}, status=400)

        allocation = get_object_or_404(CourseAllocation, pk=allocation_id)
        from_group = get_object_or_404(CombinedCourseGroup, pk=from_group_id, department=dept)
        to_group = get_object_or_404(CombinedCourseGroup, pk=to_group_id, department=dept)

        if allocation not in from_group.allocations.all():
            return JsonResponse({"status": "error", "message": "Allocation not in the source group."}, status=400)

        if allocation in to_group.allocations.all():
            return JsonResponse({"status": "error", "message": "Already in the target group."}, status=400)

        if normalize_code(to_group.base_course_code) != normalize_code(from_group.base_course_code):
            return JsonResponse({
                "status": "error",
                "message": (
                    f"Can't move — the target group is for "
                    f"{to_group.base_course_code}, not {from_group.base_course_code}."
                ),
            }, status=400)

        with transaction.atomic():
            removal = _remove_allocation_from_group_core(from_group, allocation, dept)
            add_result = _add_allocation_to_group_core(to_group, allocation, dept)

        return JsonResponse({
            "status": "success",
            "message": f"Moved {allocation.course_code} to {to_group.group_code}.",
            "from_group_deleted": bool(removal.get("deleted")),
            "from_group_id": from_group.id,
            "to_group_id": to_group.id,
            "to_group_code": to_group.group_code,
            "remaining_count": add_result["remaining_count"],
            "total_students": add_result["total_students"],
            "placement": add_result.get("placement"),
        })

    @staticmethod
    def update_combined_group_lecturer(request):
        """
        Change the lecturer assigned to a combined course group.

        Only the current holder (allocating department, i.e. `department`
        == dept) can reassign the lecturer — mirrors the permission model
        used by submit/split/add/remove above.

        The new lecturer is written to the group AND propagated to every
        member CourseAllocation, since the auto-scheduler's conflict
        checks (lecturer_busy, etc.) key off each allocation's own
        `lecturer_id`, not the group's — leaving them out of sync would
        let the scheduler double-book the old lecturer or miss conflicts
        for the new one.
        """
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        group_id = request.POST.get("group_id")
        lecturer_id = request.POST.get("lecturer_id")

        # Only the current holder can reassign — same rule as submit/split.
        group = get_object_or_404(CombinedCourseGroup, pk=group_id, department=dept)

        new_lecturer = None
        if lecturer_id:
            new_lecturer = get_object_or_404(Lecturer, pk=lecturer_id)

        old_lecturer = group.lecturer

        with transaction.atomic():
            group.lecturer = new_lecturer
            group.save(update_fields=["lecturer", "updated_at"])

            # Keep every member allocation's own lecturer field in sync so
            # the scheduler/reports see one consistent lecturer for the group.
            group.allocations.update(lecturer=new_lecturer)

            try:
                from audit_management.models import AuditLog
                AuditLog.objects.create(
                    user=request.user,
                    action="UPDATE_COMBINED_GROUP_LECTURER",
                    model_name="CombinedCourseGroup",
                    object_id=group.id,
                    details={
                        "group_code": group.group_code,
                        "old_lecturer": old_lecturer.display_name if old_lecturer else None,
                        "new_lecturer": new_lecturer.display_name if new_lecturer else None,
                    }
                )
            except ImportError:
                pass

        return JsonResponse({
            "status": "success",
            "message": f"Lecturer updated to {new_lecturer.display_name}" if new_lecturer
                       else "Lecturer cleared for this group.",
            "group_id": group.id,
            "lecturer_id": new_lecturer.id if new_lecturer else None,
            "lecturer_name": new_lecturer.display_name if new_lecturer else "",
        })

    @staticmethod
    def update_combined_group_name(request):
        """
        Rename a combined group (its group_code).

        group_code is the single canonical name for the group — every place
        that displays it (autoscheduler logs, timetable panel, unscheduled
        list, Find Courses, PDF/analysis reports) reads it live via
        CombinedCourseGroup.display_name(), which just returns group_code.
        Nothing else caches a copy of the name, so updating this one field
        is enough for every one of those "children" views to immediately
        show the new name too — there is no separate per-allocation label
        to keep in sync (unlike the lecturer, which IS denormalized onto
        each member CourseAllocation and has to be pushed out explicitly).

        Only the current holder (allocating department, i.e. `department`
        == dept) can rename — mirrors update_combined_group_lecturer.
        """
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        group_id = request.POST.get("group_id")
        new_name = request.POST.get("group_name", "").strip()

        if not new_name:
            return JsonResponse(
                {"status": "error", "message": "Group name cannot be empty."}, status=400
            )
        if len(new_name) > 50:
            return JsonResponse(
                {"status": "error", "message": "Group name must be 50 characters or fewer."},
                status=400,
            )

        # Only the current holder can rename — same rule as submit/split/lecturer.
        group = get_object_or_404(CombinedCourseGroup, pk=group_id, department=dept)

        if CombinedCourseGroup.objects.filter(group_code=new_name).exclude(pk=group.id).exists():
            return JsonResponse(
                {"status": "error", "message": f'A combined group named "{new_name}" already exists.'},
                status=400,
            )

        old_name = group.group_code

        with transaction.atomic():
            group.group_code = new_name
            group.save(update_fields=["group_code", "updated_at"])

            try:
                from audit_management.models import AuditLog
                AuditLog.objects.create(
                    user=request.user,
                    action="RENAME_COMBINED_GROUP",
                    model_name="CombinedCourseGroup",
                    object_id=group.id,
                    details={
                        "old_group_code": old_name,
                        "new_group_code": new_name,
                    }
                )
            except ImportError:
                pass

        return JsonResponse({
            "status": "success",
            "message": f'Renamed "{old_name}" to "{new_name}".',
            "group_id": group.id,
            "group_code": group.group_code,
        })

    @staticmethod
    def delete_combined_group(request):
        """Delete a combined group (allocations remain unchanged)."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err
        
        group_id = request.POST.get("group_id")
        group = get_object_or_404(CombinedCourseGroup, pk=group_id, department=dept)
        group.delete()
        
        return JsonResponse({"status": "success", "deleted": True})
    
    @staticmethod
    def detect_combinable_groups(request):
        """
        Scan all allocations for the user's department and identify sets that
        can be auto-combined:
          - Same base course code (e.g. COSC 102, COSC 102-A, COSC 102-B)
          - Same lecturer
          - Each individual allocation has ≤ 120 students
          - Not already in a combined group
        Returns a list of candidate groups, each containing the allocations
        that could be merged.
        """
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        allocations = (
            CourseAllocation.objects
            .filter(department=dept)
            .exclude(combined_groups__isnull=False)
            .select_related("lecturer", "program")
        )

        # Group by (base_code, lecturer_id)
        groups: dict = {}
        for a in allocations:
            base, _ = strip_group_suffix(a.course_code)
            base = normalize_code(base)
            lect_id = a.lecturer_id
            key = (base, lect_id)
            groups.setdefault(key, []).append(a)

        candidates = []
        for (base_code, lect_id), allocs in groups.items():
            # Need at least 2 allocations with different suffixes (or one plain + one suffixed)
            if len(allocs) < 2:
                continue
            # All must be ≤ 120 students individually
            if any(a.number_of_students > 120 for a in allocs):
                continue
            # Must actually have separate group identifiers or plain+suffixed mix
            codes = [a.course_code for a in allocs]
            if len(set(codes)) < 2:
                continue

            lect_name = allocs[0].lecturer.display_name if allocs[0].lecturer else "Unassigned"
            candidates.append({
                "base_code": base_code,
                "lecturer_id": lect_id,
                "lecturer_name": lect_name,
                "total_students": sum(a.number_of_students for a in allocs),
                "allocations": [
                    {
                        "id": a.id,
                        "course_code": a.course_code,
                        "course_name": a.course_name,
                        "program": a.program.name if a.program else "",
                        "students": a.number_of_students,
                    }
                    for a in allocs
                ],
            })

        return JsonResponse({"status": "success", "candidates": candidates})

    @staticmethod
    def smart_combine(request):
        """
        Perform a 'smart combine': given a list of allocation IDs,
        create a CombinedCourseGroup, then DELETE the individual
        CourseAllocation records and remap their ProgramCourse references
        to the primary (surviving) allocation.

        The primary allocation is the first one in the list (or the one
        with the plain base code if present).  All others are deleted
        after the combined group is created.
        """
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        allocation_ids = request.POST.getlist("allocation_ids[]")
        lecturer_id = request.POST.get("lecturer_id")

        if len(allocation_ids) < 2:
            return JsonResponse(
                {"status": "error", "message": "At least two allocations are required."},
                status=400,
            )

        allocations = list(
            CourseAllocation.objects
            .filter(pk__in=allocation_ids, department=dept)
            .select_related("lecturer", "program", "program_course")
        )

        if len(allocations) != len(allocation_ids):
            return JsonResponse(
                {"status": "error", "message": "Some allocations not found or not in your department."},
                status=400,
            )

        # Validate all have same base code
        base_codes = set()
        for a in allocations:
            base, _ = strip_group_suffix(a.course_code)
            base_codes.add(normalize_code(base))
        if len(base_codes) > 1:
            return JsonResponse(
                {"status": "error", "message": "All allocations must share the same base course code."},
                status=400,
            )

        base_course_code = list(base_codes)[0]

        # Resolve lecturer
        lecturer_obj = None
        if lecturer_id:
            lecturer_obj = get_object_or_404(Lecturer, pk=lecturer_id)
        else:
            lects = set(a.lecturer_id for a in allocations if a.lecturer_id)
            if len(lects) == 1:
                lecturer_obj = allocations[0].lecturer
            elif len(lects) > 1:
                return JsonResponse(
                    {"status": "error", "message": "Allocations have different lecturers. Please specify one."},
                    status=400,
                )

        # Choose primary: prefer the plain base code, else first in list
        primary = next(
            (a for a in allocations if strip_group_suffix(a.course_code)[1] is None),
            allocations[0],
        )
        secondary = [a for a in allocations if a.id != primary.id]

        with transaction.atomic():
            # Update primary to use plain base code and combined student count
            primary.course_code = base_course_code
            primary.number_of_students = sum(a.number_of_students for a in allocations)
            primary.lecturer = lecturer_obj
            primary.save()

            # Create combined group
            group_code = f"{base_course_code.replace(' ', '_')}_COMBINED"
            group = CombinedCourseGroup.objects.create(
                group_code=group_code,
                base_course_code=base_course_code,
                lecturer=lecturer_obj,
                department=dept,
                origin_department=dept,
                created_by=request.user,
                primary_allocation=primary,
            )
            group.allocations.set([primary] + secondary)

            # Remember this combination in the knowledge base so
            # auto-allocate can recreate it automatically next time it
            # rebuilds this department's allocations.
            _remember_course_combination_template(
                dept, base_course_code, lecturer_obj, allocations, request.user,
            )

            # Delete secondary allocations (they are now merged into primary)
            deleted_ids = [a.id for a in secondary]
            CourseAllocation.objects.filter(pk__in=deleted_ids).delete()

        return JsonResponse({
            "status": "success",
            "group": {
                "id": group.id,
                "group_code": group.group_code,
                "base_course_code": group.base_course_code,
                "primary_allocation_id": primary.id,
                "primary_course_code": primary.course_code,
                "total_students": primary.number_of_students,
                "deleted_allocation_ids": deleted_ids,
            }
        })

    @staticmethod
    def handle(request, action):
        """Route combined group actions."""
        if action == "list_available_for_combination":
            return CombinedCourseGroupService.list_available_for_combination(request)
        elif action == "create_combined_group":
            return CombinedCourseGroupService.create_combined_group(request)
        elif action == "list_combined_groups":
            return CombinedCourseGroupService.list_combined_groups(request)
        elif action == "get_combined_group_detail":
            return CombinedCourseGroupService.get_combined_group_detail(request)
        elif action == "remove_from_combined_group":
            return CombinedCourseGroupService.remove_from_combined_group(request)
        elif action == "add_to_combined_group":
            return CombinedCourseGroupService.add_to_combined_group(request)
        elif action == "move_to_combined_group":
            return CombinedCourseGroupService.move_to_combined_group(request)
        elif action == "delete_combined_group":
            return CombinedCourseGroupService.delete_combined_group(request)
        elif action == "update_combined_group_lecturer":
            return CombinedCourseGroupService.update_combined_group_lecturer(request)
        elif action == "update_combined_group_name":
            return CombinedCourseGroupService.update_combined_group_name(request)
        elif action == "submit_combined_group":
            return CombinedCourseGroupService.submit_combined_group(request)
        elif action == "split_combined_group":
            return CombinedCourseGroupService.split_combined_group(request)
        elif action == "detect_combinable_groups":
            return CombinedCourseGroupService.detect_combinable_groups(request)
        elif action == "smart_combine":
            return CombinedCourseGroupService.smart_combine(request)
        return JsonResponse({"status": "error", "message": "Invalid combined group action"})


class KnowledgeBaseService:
    """
    Read/manage access to the grouping + course-combination knowledge base
    (GroupingTemplate / CourseCombinationTemplate) that gets captured
    automatically as a COD builds student groups and combined groups
    through the normal UI, and that auto-allocate replays after every run.
    This service doesn't create templates — that happens automatically
    elsewhere (see _remember_grouping_template /
    _remember_course_combination_template) — it just lets a COD see what's
    been remembered and remove entries they no longer want replayed.
    """

    @staticmethod
    def list_grouping_templates(request):
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        templates = (
            GroupingTemplate.objects.filter(program__department=dept)
            .select_related("program")
            .prefetch_related("groups", "course_codes")
            .order_by("program__name", "year", "semester", "intake")
        )

        data = []
        for t in templates:
            data.append({
                "id": t.id,
                "program_id": t.program_id,
                "program_name": t.program.name,
                "year": t.year,
                "semester": t.semester,
                "intake": t.intake,
                "scope": t.scope,
                "groups": [{"id": g.id, "letter": g.letter, "name": g.name} for g in t.groups.all()],
                "course_codes": [c.base_course_code for c in t.course_codes.all()],
                "updated_at": t.updated_at.strftime("%Y-%m-%d %H:%M"),
            })

        return JsonResponse({"status": "success", "templates": data})

    @staticmethod
    def delete_grouping_template(request):
        """Forget an entire program/year/semester/intake grouping — auto-allocate will stop recreating it."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        template_id = request.POST.get("template_id")
        template = get_object_or_404(GroupingTemplate, pk=template_id, program__department=dept)
        label = str(template)
        template.delete()
        return JsonResponse({"status": "success", "message": f"Forgot grouping: {label}."})

    @staticmethod
    def delete_grouping_template_group(request):
        """Forget just one remembered group letter within a template (e.g. stop replaying Group C but keep A/B)."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        group_id = request.POST.get("group_id")
        grp = get_object_or_404(
            GroupingTemplateGroup, pk=group_id, template__program__department=dept
        )
        template = grp.template
        letter = grp.letter
        grp.delete()
        # Nothing left to replay for this program/year/semester/intake —
        # clean up the now-empty template too.
        if not template.groups.exists():
            template.delete()
        return JsonResponse({"status": "success", "message": f"Forgot group {letter}."})

    @staticmethod
    def list_course_combination_templates(request):
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        templates = (
            CourseCombinationTemplate.objects.filter(department=dept)
            .select_related("lecturer")
            .prefetch_related("programs__program")
            .order_by("base_course_code")
        )

        data = []
        for t in templates:
            data.append({
                "id": t.id,
                "base_course_code": t.base_course_code,
                "lecturer_name": t.lecturer.display_name if t.lecturer else "",
                "programs": [p.program.name for p in t.programs.all()],
                "updated_at": t.updated_at.strftime("%Y-%m-%d %H:%M"),
            })

        return JsonResponse({"status": "success", "templates": data})

    @staticmethod
    def delete_course_combination_template(request):
        """Forget a remembered course combination — auto-allocate will stop recreating it."""
        dept = detect_user_department(request.user)
        err = _assert_owns_department(request, dept)
        if err:
            return err

        template_id = request.POST.get("template_id")
        template = get_object_or_404(CourseCombinationTemplate, pk=template_id, department=dept)
        label = str(template)
        template.delete()
        return JsonResponse({"status": "success", "message": f"Forgot combination: {label}."})

    @staticmethod
    def handle(request, action):
        """Route knowledge-base actions."""
        if action == "list_grouping_templates":
            return KnowledgeBaseService.list_grouping_templates(request)
        elif action == "delete_grouping_template":
            return KnowledgeBaseService.delete_grouping_template(request)
        elif action == "delete_grouping_template_group":
            return KnowledgeBaseService.delete_grouping_template_group(request)
        elif action == "list_course_combination_templates":
            return KnowledgeBaseService.list_course_combination_templates(request)
        elif action == "delete_course_combination_template":
            return KnowledgeBaseService.delete_course_combination_template(request)
        return JsonResponse({"status": "error", "message": "Invalid knowledge base action"})


# ---------------------------------------------------------------
# SECURITY FIX #5 – AJAX guard helper
# The original check (x-requested-with == XMLHttpRequest) is NOT a
# reliable CSRF defence by itself; Django's CsrfViewMiddleware is the
# real guard.  We keep the header check only as a routing hint.
# The view already has @login_required + @group_required which is fine.
# ---------------------------------------------------------------

@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def cod_panel(request):
    from django.db.models import Q as _Q
    """
    Main view for COD panel. Supports AJAX endpoints via POST.
    """

    # -----------------------
    # Handle AJAX POST
    # -----------------------
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":

        action = request.POST.get("action")

        # SECURITY FIX #6 – Whitelist allowed actions to prevent enumeration
        ALLOWED_ACTIONS = {
            "list_rejected", "restore_rejected", "delete_rejected",
            "toggle_elective", "toggle_evening_weekend",
            "list_selection_groups", "create_selection_group",
            "update_selection_group", "delete_selection_group",
            "list_elective_allocations", "get_combos_and_groups_for_program",
            "get_program_courses", "get_enrollment", "save_enrollment_if_missing",
            "get_mapped_lecturers", "save_lecturer_mapping_if_missing",
            "create_program_course", "create_program", "search_lecturers",
            "create_allocation", "delete_allocation", "bulk_delete_allocations", "allocation_detail",
            "move_allocations_to_year",
            "list_allocations", "get_allocation_summary",
            "list_special_intake_allocs",
            "get_programs_for_department",
            # Added for combined course groups
            "list_available_for_combination", "create_combined_group",
            "list_combined_groups", "get_combined_group_detail",
            "remove_from_combined_group", "add_to_combined_group", "move_to_combined_group", "delete_combined_group",
            "update_combined_group_lecturer", "update_combined_group_name",
            "submit_combined_group", "split_combined_group",
            "detect_combinable_groups", "smart_combine",
            # SR (Special Request) actions
            "create_special_request", "get_special_request_for_allocation",
            "get_courses_for_sr_scope", "update_special_request",
            "cancel_special_request",
            # Special Intake Group actions
            "list_special_intake_groups", "create_special_intake_group",
            "pull_courses_to_special_intake", "get_available_courses_for_pull",
            "delete_special_intake_group",
            # Pull Courses to Normal allocation (mirrors the special-intake pull above)
            "pull_courses_to_normal", "get_available_courses_for_normal_pull",
            # Bulk group actions (right-click on a Program/Year header)
            "bulk_update_group_enrollment", "bulk_delete_group_allocations",
            # Add Group(s) — clone an allocation into new sequential groups
            "get_group_clone_info", "add_allocation_groups",
            # Student Group actions
            "list_student_groups", "create_student_group", "delete_student_group",
            "assign_courses_to_student_group", "get_available_courses_for_student_group",
            "get_student_group_detail", "update_student_group_enrollment",
            # Knowledge base (grouping + course-combination templates)
            "list_grouping_templates", "delete_grouping_template",
            "delete_grouping_template_group",
            "list_course_combination_templates", "delete_course_combination_template",
        }
        if action not in ALLOWED_ACTIONS:
            return JsonResponse({"status": "error", "message": "Unknown action"}, status=400)

        if action in ["list_rejected", "restore_rejected", "delete_rejected"]:
            return RejectedCourseService.handle(request, action)

        # -----------------------
        # SR (Special Request) actions
        # -----------------------
        def _sr_dept_scoped_qs(dept):
            # Courses the COD can legitimately attach an SR to: their own
            # department's allocations, or cross-dept ones they originated.
            return CourseAllocation.objects.filter(
                Q(department=dept) | Q(origin_department=dept)
            )

        def _serialize_sr(sr):
            return {
                "id": sr.id,
                "scope": sr.scope,
                "scope_display": sr.get_scope_display(),
                "description": sr.description,
                "status": sr.status,
                "status_display": sr.get_status_display(),
                "lecturer_name": sr.lecturer.display_name if sr.lecturer else "",
                "program_name": sr.program.name if sr.program else "",
                "semester": sr.semester,
                "created_at": sr.created_at.strftime("%d %b %Y %H:%M"),
                "courses": [
                    {"code": c, } for c in sr.affected_courses
                ] or [{"code": sr.course_code}],
            }

        # See if this allocation already has open SR(s) — the "already
        # flagged" case: re-clicking SR on that course shows what was said
        # (every active SR, since a course can have more than one) AND
        # still offers a "raise another SR" option, instead of locking the
        # COD out of raising a second SR (e.g. one for the lecturer, one
        # for the program) once the first one exists.
        if action == "get_special_request_for_allocation":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err
            allocation_id = request.POST.get("allocation_id")
            allocation = get_object_or_404(CourseAllocation, pk=allocation_id)
            if allocation.department_id != dept.pk and allocation.origin_department_id != dept.pk:
                from django.http import Http404
                raise Http404
            srs = get_active_special_requests_for_allocation(allocation)
            return JsonResponse({
                "status": "success",
                "has_sr": bool(srs),
                # Kept for backward compatibility with older cached JS.
                "sr": _serialize_sr(srs[0]) if srs else None,
                "srs": [_serialize_sr(sr) for sr in srs],
            })

        # Fetch the candidate courses for a "this lecturer" / "this program"
        # / "some courses of this program" SR, so the timetabling submission
        # (and, for program scope, a select-all-or-one-by-one popup) can
        # show every course affected instead of just the one row clicked.
        if action == "get_courses_for_sr_scope":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err
            allocation_id = request.POST.get("allocation_id")
            scope = request.POST.get("scope")
            allocation = get_object_or_404(CourseAllocation, pk=allocation_id)
            if allocation.department_id != dept.pk and allocation.origin_department_id != dept.pk:
                from django.http import Http404
                raise Http404

            base_qs = _sr_dept_scoped_qs(dept)
            if scope == SpecialRequest.SCOPE_LECTURER:
                if not allocation.lecturer_id:
                    return JsonResponse({"status": "error", "message": "This allocation has no lecturer assigned."}, status=400)
                courses = base_qs.filter(lecturer=allocation.lecturer).order_by("course_code")
                label = allocation.lecturer.display_name
            elif scope in (SpecialRequest.SCOPE_PROGRAM, SpecialRequest.SCOPE_PROGRAM_COURSES):
                if not allocation.program_id:
                    return JsonResponse({"status": "error", "message": "This allocation has no program assigned."}, status=400)
                courses = base_qs.filter(program=allocation.program).order_by("course_code")
                label = allocation.program.name
            else:
                return JsonResponse({"status": "error", "message": "Invalid SR scope."}, status=400)

            return JsonResponse({
                "status": "success",
                "label": label,
                "courses": [
                    {
                        "id": c.id,
                        "course_code": c.course_code,
                        "course_name": c.course_name,
                        "lecturer_name": c.lecturer.display_name if c.lecturer else "Unassigned",
                    }
                    for c in courses
                ],
            })

        if action == "create_special_request":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err

            scope = request.POST.get("scope", SpecialRequest.SCOPE_UNIT)
            description = (request.POST.get("description") or "").strip()
            target_ids = request.POST.getlist("target_ids[]") or [request.POST.get("allocation_id")]
            target_ids = [t for t in target_ids if t]

            if scope not in dict(SpecialRequest.SCOPE_CHOICES):
                return JsonResponse({"status": "error", "message": "Invalid SR scope."}, status=400)
            if not description:
                return JsonResponse({"status": "error", "message": "Please describe what needs to be done."}, status=400)
            if len(description) > 2000:
                return JsonResponse({"status": "error", "message": "Description is too long (max 2000 characters)."}, status=400)
            if not target_ids:
                return JsonResponse({"status": "error", "message": "Select at least one course for this SR."}, status=400)

            allocations = list(_sr_dept_scoped_qs(dept).filter(pk__in=target_ids))
            if not allocations:
                return JsonResponse({"status": "error", "message": "None of the selected courses could be found."}, status=404)

            sr = create_special_request(
                target_allocations=allocations,
                scope=scope,
                description=description,
                user=request.user,
                panel="normal",
            )
            return JsonResponse({"status": "success", "sr": _serialize_sr(sr)})

        if action == "update_special_request":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err
            sr_id = request.POST.get("id")
            description = (request.POST.get("description") or "").strip()
            if not description:
                return JsonResponse({"status": "error", "message": "Please describe what needs to be done."}, status=400)
            sr = get_object_or_404(SpecialRequest, pk=sr_id, department=dept, archived=False)
            sr = update_special_request(sr, description=description)
            return JsonResponse({"status": "success", "sr": _serialize_sr(sr)})

        if action == "cancel_special_request":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err
            sr_id = request.POST.get("id")
            sr = get_object_or_404(SpecialRequest, pk=sr_id, department=dept)
            sr.archive(reason="Cancelled by COD")
            return JsonResponse({"status": "success", "id": sr_id})

        # -----------------------
        # Toggle is_elective
        # -----------------------
        if action == "toggle_elective":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err
            pk = request.POST.get("id")
            # SECURITY FIX #3 – scope to user's department
            alloc = get_object_or_404(CourseAllocation, pk=pk, department=dept)
            alloc.is_elective = not alloc.is_elective
            alloc.save(update_fields=["is_elective"])
            return JsonResponse({"status": "success", "id": pk, "is_elective": alloc.is_elective})

        # -----------------------
        # Toggle is_evening_weekend
        # -----------------------
        if action == "toggle_evening_weekend":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err
            pk = request.POST.get("id")
            # SECURITY FIX #3 – scope to user's department
            alloc = get_object_or_404(CourseAllocation, pk=pk, department=dept)
            alloc.is_evening_weekend = not alloc.is_evening_weekend
            alloc.save(update_fields=["is_evening_weekend"])
            return JsonResponse({"status": "success", "id": pk, "is_evening_weekend": alloc.is_evening_weekend})

        # -----------------------
        # SelectionGroup actions
        # -----------------------
        if action in [
            "list_selection_groups", "create_selection_group",
            "update_selection_group", "delete_selection_group",
            "list_elective_allocations",
        ]:
            return SelectionGroupService.handle(request, action)

        # -----------------------
        # Special Intake Group actions
        # -----------------------
        if action in [
            "list_special_intake_groups", "create_special_intake_group",
            "pull_courses_to_special_intake", "get_available_courses_for_pull",
            "delete_special_intake_group",
            "pull_courses_to_normal", "get_available_courses_for_normal_pull",
        ]:
            return SpecialIntakeGroupService.handle(request, action)

        # -----------------------
        # Student Group actions
        # -----------------------
        if action in [
            "list_student_groups", "create_student_group", "delete_student_group",
            "assign_courses_to_student_group", "get_available_courses_for_student_group",
            "get_student_group_detail", "update_student_group_enrollment",
        ]:
            return StudentGroupService.handle(request, action)

        # -----------------------
        # Combined Group actions
        # -----------------------
        if action in [
            "list_available_for_combination", "create_combined_group",
            "list_combined_groups", "get_combined_group_detail",
            "remove_from_combined_group", "add_to_combined_group", "move_to_combined_group", "delete_combined_group",
            "update_combined_group_lecturer", "update_combined_group_name",
            "submit_combined_group", "split_combined_group",
            "detect_combinable_groups", "smart_combine",
        ]:
            return CombinedCourseGroupService.handle(request, action)

        # -----------------------
        # Knowledge base actions (grouping + course-combination templates)
        # -----------------------
        if action in [
            "list_grouping_templates", "delete_grouping_template",
            "delete_grouping_template_group",
            "list_course_combination_templates", "delete_course_combination_template",
        ]:
            return KnowledgeBaseService.handle(request, action)

        # ---------------------------------------------------------------
        # Return selection groups filtered by program
        # ---------------------------------------------------------------
        if action == "get_combos_and_groups_for_program":
            program_id = request.POST.get("program_id")
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err
            groups_qs = SelectionGroup.objects.filter(department=dept)
            if program_id:
                groups_qs = groups_qs.filter(
                    Q(program_id=program_id) | Q(program__isnull=True)
                )
            groups = [{"id": g.id, "name": g.name, "program": g.program.name if g.program else ""}
                      for g in groups_qs.order_by("name")]
            return JsonResponse({"status": "success", "selection_groups": groups})

        # -----------------------
        # Return program courses
        # -----------------------
        if action == "get_program_courses":
            program_id = request.POST.get("program_id")
            department_id = request.POST.get("department_id")

            qs = ProgramCourse.objects.select_related("program")
            if program_id:
                qs = qs.filter(program_id=program_id)
            elif department_id:
                qs = qs.filter(program__department_id=department_id)
            else:
                return JsonResponse({"status": "error", "message": "program_id or department_id required"}, status=400)

            # Look up the SHARED (student_group=NULL) CourseAllocation for
            # each ProgramCourse in this department, if one exists, so the
            # frontend can flag courses already mapped to a Selection Group
            # or Specialization Stem and steer the bulk stem/elective
            # pickers away from creating duplicate mappings.
            dept_for_mapping = detect_user_department(request.user)
            shared_by_pc = {}
            pc_ids_all = list(qs.values_list("id", flat=True))
            if dept_for_mapping and pc_ids_all:
                shared_qs = CourseAllocation.objects.filter(
                    program_course_id__in=pc_ids_all,
                    department=dept_for_mapping,
                    student_group__isnull=True,
                ).select_related("selection_group", "specialization_stem")
                for ca in shared_qs:
                    shared_by_pc.setdefault(ca.program_course_id, ca)

            data = []
            for pc in qs.order_by("course_code"):
                base_code, group = strip_group_suffix(pc.course_code)
                ca = shared_by_pc.get(pc.id)
                data.append({
                    "id": pc.id,
                    "program_id": pc.program.id,
                    "program_name": pc.program.name,
                    "course_code": pc.course_code,
                    "base_code": base_code,
                    "group": group,
                    "course_name": pc.course_name,
                    "year": pc.year,
                    "semester": pc.semester,
                    "unit_type": pc.unit_type,
                    "is_elective_unit": pc.is_elective_type,
                    "in_group_name": ca.selection_group.name if ca and ca.selection_group_id else None,
                    "in_stem_name": ca.specialization_stem.name if ca and ca.specialization_stem_id else None,
                })
            return JsonResponse({"status": "success", "program_courses": data})

        # -----------------------
        # Get enrollment for program + year
        # -----------------------
        if action == "get_enrollment":
            # NOTE: ProgramEnrollment is now keyed by (program, entry_year) --
            # ONE row per cohort, shared across both semesters. "year" here
            # is still the curriculum's YEAR OF STUDY (1-6), so we translate
            # it into the matching entry_year using the single global
            # AcademicYearTracker before looking the row up. "semester" no
            # longer distinguishes enrollment rows at all -- the same cohort
            # feeds both semesters -- so it's accepted but ignored here.
            from course_allocation.models import AcademicYearTracker

            program_id = request.POST.get("program_id")
            year = request.POST.get("year")

            if not program_id:
                return JsonResponse({"status": "error", "message": "program_id required"}, status=400)

            enrollment = None
            if year:
                reference_year = AcademicYearTracker.get_current().current_year
                entry_year = reference_year - (int(year) - 1)
                enrollment = ProgramEnrollment.objects.filter(
                    program_id=program_id, entry_year=entry_year
                ).first()

            if enrollment:
                return JsonResponse({
                    "status": "success",
                    "found": True,
                    "number_of_students": enrollment.number_of_students,
                    "enrollment_id": enrollment.id,
                    "entry_year": enrollment.entry_year,
                })
            else:
                return JsonResponse({"status": "success", "found": False, "number_of_students": 0})

        # -----------------------
        # Save enrollment if missing
        # -----------------------
        if action == "save_enrollment_if_missing":
            # "year" is the YEAR OF STUDY (1-6); translated to entry_year via
            # the global AcademicYearTracker. "semester" is accepted for
            # backward compatibility but no longer stored -- one enrollment
            # row covers both semesters of that cohort.
            from course_allocation.models import AcademicYearTracker

            program_id = request.POST.get("program_id")
            year = request.POST.get("year")
            number_of_students = request.POST.get("number_of_students", "0")

            if not program_id:
                return JsonResponse({"status": "error", "message": "program_id required"}, status=400)

            try:
                year = int(year) if year else 1
                number_of_students = int(number_of_students)
            except (ValueError, TypeError):
                return JsonResponse({"status": "error", "message": "Invalid values"}, status=400)

            # SECURITY FIX #4 – Reasonable upper-bound on student count
            if number_of_students < 0 or number_of_students > 10000:
                return JsonResponse({"status": "error", "message": "Invalid number of students"}, status=400)

            program = get_object_or_404(Program, pk=program_id)

            reference_year = AcademicYearTracker.get_current().current_year
            entry_year = reference_year - (year - 1)

            exists = ProgramEnrollment.objects.filter(
                program=program, entry_year=entry_year
            ).exists()

            if not exists and number_of_students > 0:
                ProgramEnrollment.objects.create(
                    program=program,
                    entry_year=entry_year,
                    number_of_students=number_of_students,
                )
                return JsonResponse({"status": "success", "created": True})

            return JsonResponse({"status": "success", "created": False})

        # -----------------------
        # Get lecturers mapped to a specific ProgramCourse
        # -----------------------
        if action == "get_mapped_lecturers":
            program_course_id = request.POST.get("program_course_id")
            course_code = request.POST.get("course_code")
            user_dept_id = request.POST.get("user_dept_id")

            if not program_course_id and not course_code:
                return JsonResponse({"status": "error", "message": "program_course_id or course_code required"}, status=400)

            mapped_lecturer_ids = []

            if program_course_id:
                try:
                    pc = ProgramCourse.objects.get(pk=program_course_id)
                    mappings = LecturerCourseMapping.objects.filter(courses=pc).select_related("lecturer")
                    mapped_lecturer_ids = [m.lecturer.id for m in mappings]
                except ProgramCourse.DoesNotExist:
                    pass

            from django.db.models import Case, When, Value, IntegerField

            try:
                user_dept_id = int(user_dept_id) if user_dept_id else None
            except (ValueError, TypeError):
                user_dept_id = None

            all_lecturers = Lecturer.objects.all().annotate(
                is_mapped=Case(
                    When(id__in=mapped_lecturer_ids, then=Value(2)),
                    default=Value(0),
                    output_field=IntegerField(),
                ),
                is_dept=Case(
                    When(department_id=user_dept_id, then=Value(1)) if user_dept_id else When(id__in=[], then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            ).order_by("-is_mapped", "-is_dept", "name")

            data = []
            for l in all_lecturers:
                data.append({
                    "id": l.id,
                    "label": l.display_name,
                    "email": l.email,
                    "is_mapped": l.id in mapped_lecturer_ids,
                    "is_dept": l.department_id == user_dept_id if user_dept_id else False,
                })

            return JsonResponse({"status": "success", "lecturers": data, "mapped_ids": mapped_lecturer_ids})

        # -----------------------
        # Save lecturer mapping if not exists
        # -----------------------
        if action == "save_lecturer_mapping_if_missing":
            lecturer_id = request.POST.get("lecturer_id")
            program_course_id = request.POST.get("program_course_id")
            user_dept_id = request.POST.get("user_dept_id")

            if not lecturer_id or not program_course_id:
                return JsonResponse({"status": "error", "message": "lecturer_id and program_course_id required"}, status=400)

            try:
                lecturer = Lecturer.objects.get(pk=lecturer_id)
                pc = ProgramCourse.objects.get(pk=program_course_id)
                dept = Department.objects.get(pk=user_dept_id) if user_dept_id else None

                already_mapped = LecturerCourseMapping.objects.filter(
                    lecturer=lecturer, courses=pc
                ).exists()

                if not already_mapped:
                    mapping, _ = LecturerCourseMapping.objects.get_or_create(
                        lecturer=lecturer,
                        department=dept,
                        defaults={}
                    )
                    mapping.courses.add(pc)
                    return JsonResponse({"status": "success", "created": True})

                return JsonResponse({"status": "success", "created": False, "message": "Already mapped"})

            except (Lecturer.DoesNotExist, ProgramCourse.DoesNotExist, Department.DoesNotExist) as e:
                # SECURITY FIX #7 – Never leak raw exception text to the client
                logger.warning("save_lecturer_mapping_if_missing error: %s", e)
                return JsonResponse({"status": "error", "message": "Invalid resource reference"}, status=400)

        # -----------------------
        # Create ProgramCourse
        # -----------------------
        if action == "create_program_course":
            program_id = request.POST.get("program_id")
            course_code = normalize_code(request.POST.get("course_code", ""))
            course_name = request.POST.get("course_name", "").strip()
            year = request.POST.get("year")
            semester = request.POST.get("semester")

            if not (program_id and course_code and course_name and year and semester):
                return JsonResponse({"status": "error", "message": "Missing fields"}, status=400)

            # SECURITY FIX #4 – Input length validation
            if len(course_code) > 20 or len(course_name) > 200:
                return JsonResponse({"status": "error", "message": "Input value too long"}, status=400)

            program = get_object_or_404(Program, pk=program_id)

            try:
                year = int(year)
                semester = int(semester)
            except (ValueError, TypeError):
                return JsonResponse({"status": "error", "message": "Invalid year/semester"}, status=400)

            if year < 1 or year > 10 or semester < 1 or semester > 3:
                return JsonResponse({"status": "error", "message": "Year/semester out of range"}, status=400)

            # Canonical (whitespace/case/separator-insensitive) duplicate
            # check -- course_code is already normalize_code()'d above, but
            # an existing row from before the DB-level save() normalization
            # (or from a legacy import) could still be stored as e.g.
            # 'BCOM112' while this form submits 'BCOM 112'. A plain
            # __iexact wouldn't catch that; compare canonical keys instead.
            target_key = _canonical_course_key(course_code)
            duplicate = next(
                (pc for pc in ProgramCourse.objects.filter(program=program)
                 if _canonical_course_key(pc.course_code) == target_key),
                None,
            )
            if duplicate:
                return JsonResponse({
                    "status": "error",
                    "message": f"Program already has that course code (saved as '{duplicate.course_code}')."
                }, status=400)

            pc = ProgramCourse.objects.create(
                program=program,
                course_code=course_code,
                course_name=course_name,
                year=year,
                semester=semester
            )

            return JsonResponse({"status": "success", "program_course": {
                "id": pc.id,
                "program_id": program.id,
                "program_name": program.name,
                "course_code": pc.course_code,
                "course_name": pc.course_name,
                "year": pc.year,
                "semester": pc.semester,
            }})

        # -----------------------
        # Create Program
        # -----------------------
        if action == "create_program":
            dept_id = request.POST.get("department_id")
            name = request.POST.get("name", "").strip()
            description = request.POST.get("description", "").strip()

            if not (dept_id and name):
                return JsonResponse({"status": "error", "message": "Missing fields"}, status=400)

            if len(name) > 200 or len(description) > 1000:
                return JsonResponse({"status": "error", "message": "Input value too long"}, status=400)

            dept = get_object_or_404(Department, pk=dept_id)

            program, created = Program.objects.get_or_create(
                name__iexact=name,
                defaults={"name": name, "department": dept, "description": description}
            )
            if not created:
                program = Program.objects.filter(name__iexact=name, department=dept).first()
                if not program:
                    return JsonResponse({"status": "error", "message": "Program exists in a different department"}, status=400)

            return JsonResponse({"status": "success", "program": {
                "id": program.id,
                "name": program.name,
                "department_id": program.department.id
            }})

        # -----------------------
        # Search Lecturers
        # -----------------------
        if action == "search_lecturers":
            q = request.POST.get("q", "").strip()
            user_dept_id = request.POST.get("user_dept_id")
            from django.db.models import Case, When, Value, IntegerField

            # SECURITY FIX #4 – Limit search term length
            if len(q) > 100:
                return JsonResponse({"status": "error", "message": "Search term too long"}, status=400)

            qs = Lecturer.objects.all()
            if q:
                qs = qs.filter(name__icontains=q)

            try:
                user_dept_id = int(user_dept_id) if user_dept_id else None
            except (ValueError, TypeError):
                user_dept_id = None

            if user_dept_id:
                qs = qs.annotate(
                    is_dept=Case(
                        When(department_id=user_dept_id, then=Value(1)),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                ).order_by("-is_dept", "name")[:30]
            else:
                qs = qs.order_by("name")[:30]

            data = [{"id": l.id, "label": l.display_name, "email": l.email} for l in qs]
            return JsonResponse({"status": "success", "lecturers": data})

        # -----------------------
        # Create or Update Allocation
        # -----------------------
        if action == "create_allocation":
            allocation_id = request.POST.get("id")
            raw_course_code = request.POST.get("course_code", "").strip()
            base_course_code = normalize_code(raw_course_code.split("-", 1)[0])
            group_choice = request.POST.get("group_letter")

            if group_choice:
                group_choice = group_choice.strip().upper()
                # Groups use Excel-column-style letters by convention
                # (append_group / strip_group_suffix both assume this):
                # A, B, C, ... Z, AA, AB, AC, ... Silently accepting
                # anything else here (digits, punctuation, stray spaces)
                # used to build an unmatchable course code that then
                # failed to auto-link back to the curriculum entry
                # further down -- reject it up front with a clear message
                # instead.
                if not re.fullmatch(r"[A-Z]+", group_choice):
                    return JsonResponse({
                        "status": "error",
                        "message": f"'{group_choice}' isn't a valid group letter — please enter letters only, like Excel columns (A, B, … Z, AA, AB, …)."
                    }, status=400)
                course_code = append_group(base_course_code, group_choice)
            else:
                course_code = normalize_code(raw_course_code)

            course_name = request.POST.get("course_name", "").strip()
            dept_id = request.POST.get("department_id")
            origin_dept_id = request.POST.get("origin_department_id")
            program_id = request.POST.get("program_id") or None
            lecturer_id = request.POST.get("lecturer_id")
            lecturer_name = request.POST.get("lecturer_name", "").strip()
            number_of_students = request.POST.get("number_of_students", "0")
            program_year = request.POST.get("program_year")
            program_semester = request.POST.get("program_semester")
            program_course_id = request.POST.get("program_course_id")
            is_elective = request.POST.get("is_elective") == "1"
            is_evening_weekend = request.POST.get("is_evening_weekend") == "1"
            from course_allocation.models import CourseAllocation as _CA
            intake_val = request.POST.get("intake", _CA.INTAKE_NORMAL)
            if intake_val not in [_CA.INTAKE_NORMAL, _CA.INTAKE_SPECIAL]:
                intake_val = _CA.INTAKE_NORMAL
            selection_group_id = request.POST.get("selection_group_id") or None
            special_intake_group_id = request.POST.get("special_intake_group_id") or None
            student_group_id = request.POST.get("student_group_id") or None

            # SECURITY FIX #4 – Input length bounds
            if len(course_code) > 20:
                return JsonResponse({"status": "error", "message": "Course code too long"}, status=400)
            if len(course_name) > 200:
                return JsonResponse({"status": "error", "message": "Course name too long"}, status=400)
            if len(lecturer_name) > 200:
                return JsonResponse({"status": "error", "message": "Lecturer name too long"}, status=400)

            try:
                number_of_students = int(number_of_students)
                if number_of_students < 0:
                    number_of_students = 0
                if number_of_students > 10000:
                    return JsonResponse({"status": "error", "message": "Student count out of range"}, status=400)
            except (ValueError, TypeError):
                number_of_students = 0

            if not dept_id:
                return JsonResponse({"status": "error", "message": "Department is required."}, status=400)

            department = get_object_or_404(Department, pk=dept_id)
            origin_department = get_object_or_404(Department, pk=origin_dept_id) if origin_dept_id else None
            program = get_object_or_404(Program, pk=program_id) if program_id else None

            # Ownership check: COD may manage allocations in their own dept OR cross-dept
            # allocations they originated (origin_department = their dept).
            user_dept = detect_user_department(request.user)
            if user_dept and department.pk != user_dept.pk:
                if allocation_id:
                    try:
                        existing = CourseAllocation.objects.get(pk=allocation_id)
                        if existing.origin_department_id != user_dept.pk and existing.department_id != user_dept.pk:
                            return JsonResponse(
                                {"status": "error", "message": "You can only manage allocations for your own department."},
                                status=403,
                            )
                    except CourseAllocation.DoesNotExist:
                        return JsonResponse({"status": "error", "message": "Allocation not found."}, status=404)
                else:
                    return JsonResponse(
                        {"status": "error", "message": "You can only manage allocations for your own department."},
                        status=403,
                    )

            selection_group_obj = None
            if selection_group_id:
                try:
                    selection_group_obj = SelectionGroup.objects.get(pk=selection_group_id, department=department)
                except SelectionGroup.DoesNotExist:
                    pass

            special_intake_group_obj = None
            if special_intake_group_id:
                try:
                    special_intake_group_obj = SpecialIntakeGroup.objects.get(pk=special_intake_group_id, program=program)
                except SpecialIntakeGroup.DoesNotExist:
                    pass

            student_group_obj = None
            if student_group_id:
                try:
                    student_group_obj = StudentGroup.objects.get(pk=student_group_id, program=program)
                except StudentGroup.DoesNotExist:
                    pass
            # Some of the save forms on this page (evening/weekend modal,
            # special-intake modal, and — until recently — the main edit
            # form) don't carry a student_group_id field at all. Treat a
            # completely absent field as "leave the group alone" rather
            # than "clear it", so editing/renaming an allocation can never
            # silently detach it from its student group. An explicit empty
            # value (student_group_id="") still clears it intentionally.
            student_group_field_present = "student_group_id" in request.POST

            # Lecturer Handling
            clean_name = re.sub(r"\s+", "", lecturer_name.lower())
            lecturer_obj = None

            if lecturer_id and lecturer_id not in ["", "__new__"]:
                lecturer_obj = get_object_or_404(Lecturer, pk=lecturer_id)
            elif lecturer_id == "__new__" and lecturer_name:
                # SECURITY FIX #8 – Use a safe placeholder email instead of
                # constructing one from user-controlled input
                safe_suffix = re.sub(r"[^a-z0-9]", "", clean_name)[:40]
                placeholder_email = f"staff.{safe_suffix}@placeholder.internal"
                lecturer_obj, _ = Lecturer.objects.get_or_create(
                    name=lecturer_name,
                    defaults={
                        "payroll_number": generate_unique_payroll(),
                        "email": placeholder_email,
                        "designation": "Mr"
                    }
                )

            # duplication check
            qs = CourseAllocation.objects.filter(program=program, course_code__iexact=course_code)
            if allocation_id:
                qs = qs.exclude(pk=allocation_id)
            if qs.exists():
                return JsonResponse({"status": "error", "message": "This course code is already allocated to that program."}, status=400)

            # group logic — ONLY run for NEW allocations (not edits).
            # When editing an existing allocation the course_code was already
            # committed; re-running the "need_group" check on every save causes
            # the false "entry already exists / give a group letter" error.
            if not allocation_id:
                base, existing_group = strip_group_suffix(course_code)
                similar_qs = CourseAllocation.objects.filter(
                    course_code__iregex=rf"^{re.escape(base)}(?:[-/]\s*[A-Z]+)?$"
                )
                existing_groups = {strip_group_suffix(s.course_code)[1] or "A" for s in similar_qs}

                if not strip_group_suffix(course_code)[1] and existing_groups:
                    return JsonResponse({
                        "status": "need_group",
                        "message": "Course code exists in allocation database. Please choose a group letter.",
                        # Excel-column order (A, B, ... Z, AA, AB, ...), not plain
                        # alphabetical sort (which would put "AA" before "B").
                        "existing_groups": sorted(existing_groups, key=letters_to_index)
                    }, status=409)

                if strip_group_suffix(course_code)[1] in existing_groups:
                    return JsonResponse({"status": "error", "message": f"Group {strip_group_suffix(course_code)[1]} already exists."}, status=400)

            # ------------------------------------------------------------
            # Resolve the curriculum entry (ProgramCourse) for this code.
            # Preference order:
            #   1. An explicit program_course_id (e.g. the user just chose
            #      one from a "map to..." / "add to curriculum" prompt).
            #   2. Auto-resolve from course_code + program, tolerating any
            #      group-suffix style (COSC 345(C), COSC345-C, ...).
            # If neither works, don't guess and don't crash — tell the
            # caller exactly what's wrong and give it something to act on.
            # ------------------------------------------------------------
            program_course_instance = None
            program_course_suggestions = []
            if program_course_id:
                try:
                    program_course_instance = ProgramCourse.objects.get(pk=program_course_id)
                except ProgramCourse.DoesNotExist:
                    program_course_instance = None

            if not program_course_instance and program is not None:
                program_course_instance, program_course_suggestions = resolve_program_course(
                    program, course_code,
                    year_hint=program_year,
                    semester_hint=program_semester,
                )

            # ------------------------------------------------------------
            # Auto-map electives to a SelectionGroup by program/year/semester.
            # The curriculum entry (ProgramCourse) already defines which year
            # and semester a unit belongs to, so there's no need to make the
            # COD pick a SelectionGroup by hand — find (or create) the group
            # for that exact program+year+semester and use it automatically.
            # ------------------------------------------------------------
            if is_elective and program_course_instance and not selection_group_obj:
                auto_group_name = (
                    f"{program.name} Year {program_course_instance.year} "
                    f"Semester {program_course_instance.semester} Electives"
                )
                selection_group_obj, _ = SelectionGroup.objects.get_or_create(
                    department=department,
                    program=program,
                    name=auto_group_name,
                    defaults={
                        "created_by": request.user if request.user.is_authenticated else None,
                    },
                )

            # save
            with transaction.atomic():
                if allocation_id:
                    # Scope update: allow if allocation belongs to user's dept OR origin_dept
                    allocation = get_object_or_404(CourseAllocation, pk=allocation_id)

                    # An "origin department" collaborator (e.g. Business Admin on a
                    # course that Computer Science owns and originated from them)
                    # is allowed in here purely to assign/change the lecturer -- they
                    # do NOT own this allocation. Previously every save (even one
                    # only meant to set a lecturer) blindly overwrote department,
                    # course_code, course_name, program, origin_department, intake,
                    # etc. with whatever the collaborator's own form happened to
                    # hold, which silently reassigned ownership away from the true
                    # owning department and made the allocation vanish from their
                    # panel. Only the true owning department (or a superuser/no
                    # detected department) may change authoring fields; a pure
                    # origin-department collaborator may only touch the lecturer,
                    # student count, and the group suffix on the course code
                    # (e.g. "MATH 122" <-> "MATH 122-A") -- see below, they can
                    # reflect how the serviced course is split/labelled without
                    # being able to rename it to a different course entirely.
                    is_true_owner = (
                        not user_dept or allocation.department_id == user_dept.pk
                    )

                    if is_true_owner:
                        allocation.course_code = course_code
                        allocation.course_name = course_name
                        allocation.department = department
                        allocation.origin_department = origin_department
                        allocation.program = program
                        allocation.is_elective = is_elective
                        allocation.is_evening_weekend = is_evening_weekend
                        allocation.intake = intake_val
                        allocation.selection_group = selection_group_obj
                        allocation.special_intake_group = special_intake_group_obj
                        if student_group_field_present:
                            allocation.student_group = student_group_obj
                        if program_course_instance:
                            allocation.program_course = program_course_instance
                    else:
                        # Non-owner (serviced) edit: allow adjusting only the
                        # group suffix on the course code, not the base course.
                        # e.g. "MATH 122" -> "MATH 122-A" or "MATH 122-A" ->
                        # "MATH 122" are fine; changing the base code itself
                        # is an authoring change reserved for the true owner.
                        existing_base, _ = strip_group_suffix(allocation.course_code)
                        new_base, _ = strip_group_suffix(course_code)
                        if new_base == existing_base:
                            allocation.course_code = course_code
                        elif course_code != allocation.course_code:
                            return JsonResponse({
                                "status": "error",
                                "message": (
                                    "You can only add or remove a group letter on this "
                                    "serviced course's code (e.g. 'MATH 122' \u2194 'MATH 122-A'). "
                                    "Changing the course itself is reserved for the "
                                    "department that owns this allocation."
                                ),
                            }, status=403)

                    allocation.lecturer = lecturer_obj
                    allocation.number_of_students = number_of_students
                else:
                    # Build (but do not yet save) a new instance so we can
                    # validate it with full_clean() just like the update path.
                    # Creating via .objects.create() here used to skip
                    # full_clean() entirely, so a missing/invalid
                    # program_course_id (a required, non-nullable FK) caused
                    # an unhandled IntegrityError -> Django 500 HTML error
                    # page instead of a clean JSON response.
                    if not program_course_instance:
                        if program_course_suggestions:
                            message = (
                                f"Couldn't confidently match '{course_code}' to a curriculum "
                                f"entry for {program.name if program else 'this program'}. "
                                "Pick one of the suggested matches, or add it as a new "
                                "curriculum course."
                            )
                        elif program is not None and not ProgramCourse.objects.filter(program=program).exists():
                            message = (
                                f"{program.name} has no curriculum entries yet. "
                                f"Add '{course_code}' to the curriculum to continue."
                            )
                        else:
                            message = (
                                f"'{course_code}' isn't in the curriculum for "
                                f"{program.name if program else 'the selected program'}. "
                                "Add it to the curriculum to continue."
                            )
                        return JsonResponse({
                            "status": "need_program_course",
                            "message": message,
                            "suggestions": program_course_suggestions,
                            "course_code": course_code,
                            "base_course_code": base_course_code,
                            "program_id": program.id if program else None,
                        }, status=409)

                    allocation = CourseAllocation(
                        course_code=course_code,
                        course_name=course_name,
                        department=department,
                        origin_department=origin_department,
                        program=program,
                        lecturer=lecturer_obj,
                        number_of_students=number_of_students,
                        is_elective=is_elective,
                        is_evening_weekend=is_evening_weekend,
                        intake=intake_val,
                        selection_group=selection_group_obj,
                        special_intake_group=special_intake_group_obj,
                        student_group=student_group_obj,
                        program_course=program_course_instance,
                    )

                try:
                    allocation.full_clean()
                except ValidationError as ve:
                    message_dict = getattr(ve, "message_dict", None)
                    if message_dict:
                        message = " ".join(
                            m for msgs in message_dict.values() for m in msgs
                        )
                    else:
                        message = "; ".join(ve.messages) if hasattr(ve, "messages") else str(ve)
                    return JsonResponse({"status": "error", "message": message}, status=400)

                allocation.save()

                # ---- Auto-place a brand-new course into the timetable.
                # A newly created allocation isn't in any combined group
                # yet, so try to find it its own free, non-colliding slot
                # in an unblocked venue (same venue/lecturer/program-year
                # collision rules the timetable panel and combined-group
                # placement use). If nothing in the current scheduling
                # configuration fits, leave it unscheduled — never force a
                # double-booking just to get it onto the grid.
                placement_result = None
                if not allocation_id:
                    try:
                        from timetable.timetable_panel import _get_year_value
                        found = _find_free_slot_for_allocation_set(
                            [allocation.id], allocation.lecturer_id,
                            allocation.number_of_students or 0,
                            program_id=allocation.program_id,
                            alloc_year=_get_year_value(allocation),
                            sample_allocation=allocation,
                        )
                    except Exception:
                        logger.exception(
                            "Slot search failed while auto-placing new allocation %s", allocation.id
                        )
                        found = None

                    if found:
                        from timetable.models import Timetable as _Timetable
                        day, start_t, end_t, venue_obj = found
                        _Timetable.objects.create(
                            course_allocation=allocation, venue=venue_obj,
                            day=day, start_time=start_t, end_time=end_t,
                        )
                        placement_result = {"action": "placed", "day": day, "venue": venue_obj.code}
                    else:
                        placement_result = {"action": "unscheduled"}

                # ---- Carry forward any still-open SRs (e.g. "this lecturer
                # is a part-timer") from a previous semester's allocation for
                # the same lecturer/program/course, so CODs don't have to
                # re-raise them every semester. Only for brand-new rows.
                if not allocation_id:
                    try:
                        carry_forward_special_requests(allocation, panel="normal")
                    except Exception:
                        logger.exception("Failed to carry forward special requests for allocation %s", allocation.id)

                # ---- Auto-add to BaseSelection if this is an elective and mapped to a ProgramCourse
                try:
                    from course_allocation.models import BaseSelection
                    if allocation.is_elective and allocation.program_course:
                        BaseSelection.objects.get_or_create(
                            program_course=allocation.program_course,
                            department=allocation.department,
                            defaults={"created_by": request.user},
                        )
                except Exception:
                    # Non-fatal: if BaseSelection model isn't available or any error occurs,
                    # we don't want to break allocation creation. Silent pass.
                    pass

                # Auto-save enrollment (one row per program+entry_year, shared
                # across both semesters -- translate the curriculum year-of-
                # study into entry_year via the global AcademicYearTracker).
                if program and program_year and number_of_students > 0:
                    try:
                        from course_allocation.models import AcademicYearTracker
                        yr = int(program_year)
                        if 1 <= yr <= 10:
                            reference_year = AcademicYearTracker.get_current().current_year
                            entry_year = reference_year - (yr - 1)
                            enrollment_exists = ProgramEnrollment.objects.filter(
                                program=program, entry_year=entry_year
                            ).exists()
                            if not enrollment_exists:
                                ProgramEnrollment.objects.create(
                                    program=program, entry_year=entry_year,
                                    number_of_students=number_of_students,
                                )
                    except (ValueError, TypeError):
                        pass

                # Auto-save lecturer mapping
                #
                # BUG (fixed): this used to gate on the raw `program_course_id`
                # POST field, which is only populated when the caller
                # explicitly picked a suggestion from the "map to..." popup.
                # In the normal/common flow the course code is auto-resolved
                # via resolve_program_course() above (program_course_id is
                # never sent), so `program_course_instance` is set but the
                # raw POST field is empty -- and the mapping silently never
                # saved even though the allocation itself succeeded. Key off
                # `program_course_instance` (the resolved FK target) instead,
                # which is populated on both the explicit-pick and the
                # auto-resolved paths.
                if lecturer_obj and program_course_instance:
                    already_mapped = LecturerCourseMapping.objects.filter(
                        lecturer=lecturer_obj, courses=program_course_instance
                    ).exists()
                    if not already_mapped:
                        mapping, _ = LecturerCourseMapping.objects.get_or_create(
                            lecturer=lecturer_obj, department=department, defaults={}
                        )
                        mapping.courses.add(program_course_instance)

            return JsonResponse({
                "status": "success",
                "allocation": {
                    "id": allocation.id,
                    "course_code": allocation.course_code,
                    "course_name": allocation.course_name,
                    "department": allocation.department.name,
                    "origin_department": allocation.origin_department.name if allocation.origin_department else "",
                    "program": allocation.program.name if allocation.program else "",
                    # SECURITY/UI FIX: expose program_id + curriculum year so the
                    # frontend can insert the new row into the correct
                    # program/year group section without a full page reload
                    # (previously only the display name was sent, which isn't
                    # enough to match the group header's data-program-id /
                    # data-year attributes).
                    "program_id": allocation.program_id,
                    "year": allocation.program_course.year if allocation.program_course_id else None,
                    "lecturer": allocation.lecturer.display_name if allocation.lecturer else "",
                    "number_of_students": allocation.number_of_students,
                    "is_elective": allocation.is_elective,
                    "is_evening_weekend": allocation.is_evening_weekend,
                    "intake": allocation.intake,
                    "selection_group_id": allocation.selection_group_id,
                    "selection_group_name": allocation.selection_group.name if allocation.selection_group else "",
                    "special_intake_group_id": allocation.special_intake_group_id,
                    "special_intake_group_name": allocation.special_intake_group.academic_year if allocation.special_intake_group else "",
                    "student_group_id": allocation.student_group_id,
                    "student_group_name": allocation.student_group.name if allocation.student_group else "",
                    "program_course_id": allocation.program_course_id,
                    "is_cross_dept": bool(
                        allocation.program_id and allocation.program.department_id and allocation.program.department_id != department.id
                    ),
                },
                "placement": placement_result,
            })

        # -----------------------
        # Delete allocation
        # -----------------------
        if action == "delete_allocation":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err
            pk = request.POST.get("id")
            # Allow delete if user owns the allocating dept OR originated this cross-dept course
            allocation = get_object_or_404(CourseAllocation, pk=pk)
            if allocation.department_id != dept.pk and allocation.origin_department_id != dept.pk:
                from django.http import Http404
                raise Http404
            allocation.delete()
            return JsonResponse({"status": "success", "id": pk})

        # -----------------------
        # Bulk delete (multi-select via checkboxes + right-click "Delete"
        # on the normal allocations table on the right)
        # -----------------------
        if action == "bulk_delete_allocations":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err

            raw_ids = request.POST.get("ids", "")
            ids = [i for i in (x.strip() for x in raw_ids.split(",")) if i]
            if not ids:
                return JsonResponse({"status": "error", "message": "No allocations selected."}, status=400)

            # Same ownership rule as the single-row delete: only allocations
            # this department owns, or cross-dept ones it originated.
            qs = CourseAllocation.objects.filter(
                Q(department=dept) | Q(origin_department=dept),
                pk__in=ids,
            )
            found_ids = set(str(pk) for pk in qs.values_list("pk", flat=True))
            missing = [i for i in ids if i not in found_ids]

            with transaction.atomic():
                deleted_count = qs.count()
                qs.delete()

            resp = {
                "status": "success",
                "deleted_count": deleted_count,
                "message": f"Deleted {deleted_count} allocation(s).",
            }
            if missing:
                resp["skipped_ids"] = missing
                resp["message"] += f" ({len(missing)} could not be deleted — not found or not owned by your department.)"
            return JsonResponse(resp)

        # -----------------------
        # Move to Year — reassign the curriculum year for the course(s)
        # behind the selected allocation(s). This is a curriculum-level
        # change: it updates the linked ProgramCourse's `year`, so every
        # group (A, B, C, ...) sharing that course moves together and the
        # row re-renders under the target year's header.
        # -----------------------
        if action == "move_allocations_to_year":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err

            raw_ids = request.POST.get("ids", "")
            ids = [i for i in (x.strip() for x in raw_ids.split(",")) if i]
            if not ids:
                return JsonResponse({"status": "error", "message": "No courses selected."}, status=400)

            try:
                target_year = int(request.POST.get("target_year", ""))
            except (TypeError, ValueError):
                return JsonResponse({"status": "error", "message": "Choose a valid target year."}, status=400)
            valid_years = {y for y, _ in ProgramCourse.YEAR_CHOICES}
            if target_year not in valid_years:
                return JsonResponse({
                    "status": "error",
                    "message": f"Year must be one of: {', '.join(str(y) for y in sorted(valid_years))}.",
                }, status=400)

            # Same ownership rule as delete: only allocations this
            # department owns, or cross-dept ones it originated.
            qs = CourseAllocation.objects.filter(
                Q(department=dept) | Q(origin_department=dept),
                pk__in=ids,
            ).select_related("program_course")
            found_ids = set(str(pk) for pk in qs.values_list("pk", flat=True))
            missing = [i for i in ids if i not in found_ids]

            program_course_ids = set(qs.values_list("program_course_id", flat=True))
            if not program_course_ids:
                return JsonResponse({"status": "error", "message": "None of the selected courses could be found."}, status=400)

            with transaction.atomic():
                pcs = list(ProgramCourse.objects.select_for_update().filter(pk__in=program_course_ids))
                already_there = [pc for pc in pcs if pc.year == target_year]
                to_move = [pc for pc in pcs if pc.year != target_year]
                for pc in to_move:
                    pc.year = target_year
                    pc.save(update_fields=["year"])

            moved_codes = sorted({pc.course_code for pc in to_move})
            affected_allocations = [
                _alloc_row_payload(a, dept)
                for a in CourseAllocation.objects.filter(program_course_id__in=program_course_ids)
            ]

            msg_bits = []
            if moved_codes:
                msg_bits.append(f"Moved {', '.join(moved_codes)} to Year {target_year}.")
            if already_there:
                msg_bits.append(f"{len(already_there)} course(s) were already in Year {target_year}.")
            if missing:
                msg_bits.append(f"{len(missing)} selected row(s) could not be moved — not found or not owned by your department.")

            return JsonResponse({
                "status": "success",
                "moved_course_codes": moved_codes,
                "target_year": target_year,
                "affected_allocations": affected_allocations,
                "message": " ".join(msg_bits) if msg_bits else f"Already in Year {target_year}.",
            })

        # -----------------------
        # Add Group(s) — clone an allocation into new sequentially-lettered
        # groups of the SAME program course (e.g. COSC 111-A → also create
        # COSC 111-B, COSC 111-C, ...). Continues after whatever the highest
        # existing group letter is (COSC 111-D already exists → next is E).
        # -----------------------
        if action == "get_group_clone_info":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err
            pk = request.POST.get("id")
            source = get_object_or_404(CourseAllocation, pk=pk)
            if source.department_id != dept.pk and source.origin_department_id != dept.pk:
                from django.http import Http404
                raise Http404

            base, _ = strip_group_suffix(source.course_code)
            # Excel-column order (A, B, ... Z, AA, AB, ...), not plain
            # alphabetical sort (which would put "AA" before "B").
            existing_letters = sorted(
                _used_group_letters(base, source.program_course_id, source.program_id),
                key=letters_to_index,
            )
            last_index = max((letters_to_index(l) for l in existing_letters), default=0)
            next_letter = index_to_letters(last_index + 1)

            return JsonResponse({
                "status": "success",
                "base_code": base,
                "existing_letters": existing_letters,
                "next_letter": next_letter,
            })

        if action == "add_allocation_groups":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err
            pk = request.POST.get("id")
            source = get_object_or_404(CourseAllocation, pk=pk)
            if source.department_id != dept.pk and source.origin_department_id != dept.pk:
                from django.http import Http404
                raise Http404

            try:
                num_groups = int(request.POST.get("num_groups", ""))
            except (TypeError, ValueError):
                return JsonResponse({"status": "error", "message": "Enter a valid number of groups."}, status=400)
            if num_groups < 1 or num_groups > 20:
                return JsonResponse({"status": "error", "message": "Choose between 1 and 20 groups."}, status=400)

            base, _ = strip_group_suffix(source.course_code)

            with transaction.atomic():
                # Lock every row that could hold a letter for this base code
                # (same program_course, OR same program + matching base text)
                # so two concurrent "Add Group(s)" clicks — or a concurrent
                # save from the left form — can't both claim the same letter.
                lock_qs = CourseAllocation.objects.select_for_update().filter(
                    program_course_id=source.program_course_id
                )
                if source.program_id:
                    lock_qs = lock_qs | CourseAllocation.objects.select_for_update().filter(
                        program_id=source.program_id,
                        course_code__iregex=rf"^{re.escape(base)}(?:[-/_]?\s*[A-Za-z]+|\s*\([A-Za-z]+\))?$",
                    )
                existing_letters = {
                    (strip_group_suffix(a.course_code)[1] or "A") for a in lock_qs.distinct()
                }
                last_index = max((letters_to_index(l) for l in existing_letters), default=0)

                created = []
                for i in range(1, num_groups + 1):
                    letter = index_to_letters(last_index + i)
                    new_code = append_group(base, letter)
                    # Final belt-and-braces check — mirrors create_allocation's
                    # own duplicate guard exactly, so this can never leave
                    # behind a row that a later edit/save can't find again.
                    if CourseAllocation.objects.filter(
                        program_id=source.program_id, course_code__iexact=new_code
                    ).exists():
                        return JsonResponse({
                            "status": "error",
                            "message": f"{new_code} already exists for this program — please retry.",
                        }, status=400)
                    clone = CourseAllocation.objects.create(
                        course_code=new_code,
                        course_name=source.course_name,
                        department=source.department,
                        origin_department=source.origin_department,
                        program=source.program,
                        program_course=source.program_course,
                        intake=source.intake,
                        is_elective=source.is_elective,
                        is_evening_weekend=source.is_evening_weekend,
                        number_of_students=0,
                        lecturer=None,
                    )
                    created.append({
                        "id": clone.id,
                        "course_code": clone.course_code,
                        "course_name": clone.course_name,
                        # UI FIX: include everything buildAllocRow() needs so the
                        # frontend can append each new group row via AJAX/DOM
                        # insertion instead of a full page reload.
                        "program_id": clone.program_id,
                        "program_name": clone.program.name if clone.program else "",
                        "program_course_id": clone.program_course_id,
                        "year": clone.program_course.year if clone.program_course_id else None,
                        "origin_dept_name": clone.origin_department.name if clone.origin_department else "",
                        "number_of_students": clone.number_of_students,
                        "lecturer_display": clone.lecturer.display_name if clone.lecturer else "",
                        "is_cross_dept": bool(
                            clone.program_id and clone.program.department_id and clone.program.department_id != dept.id
                        ),
                    })

            return JsonResponse({
                "status": "success",
                "message": f"Added {len(created)} group(s).",
                "created": created,
                "base_code": base,
            })

        # -----------------------
        # Allocation detail
        # -----------------------
        if action == "allocation_detail":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err
            pk = request.POST.get("id")
            # Allow access if this COD owns the allocating department OR the origin department.
            # Cross-dept courses (e.g. a COSC course taught to Education students) are created
            # by the origin COD but stored under the host department — they must still be editable.
            alloc = get_object_or_404(
                CourseAllocation.objects.select_related(
                    "department", "origin_department", "program", "lecturer",
                    "program_course", "selection_group", "special_intake_group",
                    "student_group"
                ),
                pk=pk,
            )
            if alloc.department_id != dept.pk and alloc.origin_department_id != dept.pk:
                from django.http import Http404
                raise Http404
            return JsonResponse({
                "status": "success",
                "allocation": {
                    "id": alloc.id,
                    "course_code": alloc.course_code,
                    "course_name": alloc.course_name,
                    "department_id": alloc.department.id,
                    "department_name": alloc.department.name,
                    "origin_department_id": alloc.origin_department.id if alloc.origin_department else None,
                    "origin_department_name": alloc.origin_department.name if alloc.origin_department else "",
                    "program_id": alloc.program.id if alloc.program else None,
                    "program_name": alloc.program.name if alloc.program else "",
                    "program_department_id": alloc.program.department_id if alloc.program else None,
                    "program_department_name": alloc.program.department.name if (alloc.program and alloc.program.department_id) else "",
                    "is_cross_dept": bool(
                        alloc.program and alloc.program.department_id and alloc.program.department_id != dept.pk
                    ),
                    "program_course_id": alloc.program_course_id,
                    "lecturer_id": alloc.lecturer.id if alloc.lecturer else None,
                    "lecturer_name": alloc.lecturer.display_name if alloc.lecturer else "",
                    "number_of_students": alloc.number_of_students,
                    "is_elective": alloc.is_elective,
                    "is_evening_weekend": alloc.is_evening_weekend,
                    "intake": alloc.intake,
                    "selection_group_id": alloc.selection_group_id,
                    "selection_group_name": alloc.selection_group.name if alloc.selection_group else "",
                    "special_intake_group_id": alloc.special_intake_group_id,
                    "special_intake_group_name": alloc.special_intake_group.academic_year if alloc.special_intake_group else "",
                    "student_group_id": alloc.student_group_id,
                    "student_group_name": alloc.student_group.name if alloc.student_group else "",
                }
            })

        # -----------------------
        # List allocations
        # SECURITY FIX #3 – Scope list to user's own department
        # -----------------------
        if action == "list_allocations":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err
            allocations = CourseAllocation.objects.select_related(
                "department", "program", "lecturer", "origin_department", "selection_group",
                "program_course", "special_intake_group", "student_group",
            ).filter(department=dept)
            data = [{
                "id": a.id,
                "course_code": a.course_code,
                "course_name": a.course_name,
                "department": a.department.name,
                "origin_department": a.origin_department.name if a.origin_department else "",
                "program": a.program.name if a.program else "",
                "program_year": a.program_course.year if a.program_course else None,
                "program_semester": a.program_course.semester if a.program_course else None,
                "lecturer": a.lecturer.display_name if a.lecturer else "",
                "number_of_students": a.number_of_students,
                "is_elective": a.is_elective,
                "is_evening_weekend": a.is_evening_weekend,
                "intake": a.intake,
                "selection_group_id": a.selection_group_id,
                "selection_group_name": a.selection_group.name if a.selection_group else "",
                "special_intake_group_id": a.special_intake_group_id,
                "special_intake_group_name": a.special_intake_group.academic_year if a.special_intake_group else "",
                "student_group_id": a.student_group_id,
                "student_group_name": a.student_group.name if a.student_group else "",
            } for a in allocations]
            return JsonResponse({"status": "success", "allocations": data})

        # -----------------------
        # Allocation Summary
        # -----------------------
        if action == "get_allocation_summary":
            dept = detect_user_department(request.user)
            if not dept:
                return JsonResponse({"status": "error", "message": "Department not found"}, status=400)

            semester_filter = request.POST.get("semester")
            # SECURITY FIX #4 – Validate semester filter value
            if semester_filter and semester_filter not in ("1", "2", "3"):
                return JsonResponse({"status": "error", "message": "Invalid semester"}, status=400)

            qs = (
                CourseAllocation.objects
                .filter(
                    Q(department=dept) | Q(origin_department=dept)
                )
                .select_related("department", "origin_department", "program", "lecturer", "program_course")
                .filter(is_evening_weekend=False, intake=CourseAllocation.INTAKE_NORMAL)
            )

            if semester_filter:
                qs = qs.filter(program_course__semester=semester_filter)

            lecturer_counts = defaultdict(list)
            for a in qs:
                lect_name = a.lecturer.display_name if a.lecturer else "Unassigned"
                lecturer_counts[lect_name].append({
                    "course_code": a.course_code,
                    "course_name": a.course_name,
                    "program": a.program.name if a.program else "-",
                })

            program_year_summary = {}
            dept_programs = Program.objects.filter(department=dept)
            for prog in dept_programs:
                prog_courses_qs = ProgramCourse.objects.filter(program=prog)
                if semester_filter:
                    prog_courses_qs = prog_courses_qs.filter(semester=semester_filter)

                for pc in prog_courses_qs.order_by("year", "semester"):
                    key = (prog.id, prog.name, pc.year, pc.semester)
                    if key not in program_year_summary:
                        program_year_summary[key] = {"expected": [], "allocated": []}
                    program_year_summary[key]["expected"].append({
                        "id": pc.id,
                        "course_code": pc.course_code,
                        "course_name": pc.course_name,
                    })

            for a in qs:
                if a.program and a.program_course:
                    key = (a.program.id, a.program.name, a.program_course.year, a.program_course.semester)
                    if key in program_year_summary:
                        program_year_summary[key]["allocated"].append({
                            "id": a.id,
                            "course_code": a.course_code,
                            "course_name": a.course_name,
                            "lecturer": a.lecturer.display_name if a.lecturer else "Unassigned",
                        })

            program_year_data = []
            for (prog_id, prog_name, year, sem), info in sorted(program_year_summary.items(), key=lambda x: (x[0][1], x[0][2], x[0][3])):
                allocated_codes = {x["course_code"].upper() for x in info["allocated"]}
                missing = [c for c in info["expected"] if c["course_code"].upper() not in allocated_codes]
                total_expected = len(info["expected"])
                total_allocated = len(info["allocated"])
                program_year_data.append({
                    "program_id": prog_id,
                    "program_name": prog_name,
                    "year": year,
                    "semester": sem,
                    "total_expected": total_expected,
                    "total_allocated": total_allocated,
                    "remaining": total_expected - total_allocated,
                    "missing_courses": missing,
                    "allocated_courses": info["allocated"],
                })

            return JsonResponse({
                "status": "success",
                "lecturer_summary": [
                    {"lecturer": lect, "count": len(courses), "courses": courses}
                    for lect, courses in sorted(lecturer_counts.items(), key=lambda x: -len(x[1]))
                ],
                "program_year_summary": program_year_data,
                "dept_name": dept.name,
            })

        # -----------------------
        # Get programs for a given department (used when editing cross-dept allocations)
        # -----------------------
        if action == "get_programs_for_department":
            dept_id = request.POST.get("department_id")
            if not dept_id:
                return JsonResponse({"status": "error", "message": "department_id required"}, status=400)
            try:
                dept_id = int(dept_id)
            except (ValueError, TypeError):
                return JsonResponse({"status": "error", "message": "Invalid department_id"}, status=400)
            dept_obj = get_object_or_404(Department, pk=dept_id)
            user_dept = detect_user_department(request.user)
            progs = Program.objects.filter(department=dept_obj).order_by("name")
            is_own = user_dept and dept_obj.pk == user_dept.pk
            data = [{"id": p.id, "name": p.name, "is_own_dept": is_own} for p in progs]
            return JsonResponse({
                "status": "success",
                "programs": data,
                "department_id": dept_obj.id,
                "department_name": dept_obj.name,
                "is_own_dept": is_own,
            })

        # -----------------------
        # Special Intake – list
        # -----------------------
        if action == "list_special_intake_allocs":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err
            from course_allocation.models import CourseAllocation as _CA
            qs = (
                CourseAllocation.objects
                .filter(department=dept, intake=_CA.INTAKE_SPECIAL)
                .select_related("program", "lecturer", "origin_department", "special_intake_group", "program_course", "student_group")
                .order_by(
                    "program__name",
                    "program_course__year",
                    "program_course__semester",
                    # Shared courses (student_group=NULL) first, then each
                    # named student group (Group A, Group B, …) kept together
                    # in letter order, so the frontend can divide the table
                    # into distinct Shared / Group A / Group B sections
                    # instead of interleaving groups.
                    F("student_group__letter").asc(nulls_first=True),
                    "course_code",
                )
            )
            data = [
                {
                    "id": a.id,
                    "course_code": a.course_code,
                    "course_name": a.course_name,
                    "program": a.program.name if a.program else "",
                    "program_id": a.program_id,
                    # Curriculum year/semester of the course itself — used to
                    # group this table by Program / Year / Semester, the same
                    # way the normal allocations table is grouped.
                    "year": a.program_course.year if a.program_course else None,
                    "semester": a.program_course.semester if a.program_course else None,
                    "origin_department": a.origin_department.name if a.origin_department else "",
                    "lecturer": a.lecturer.display_name if a.lecturer else "",
                    "number_of_students": a.number_of_students,
                    "is_elective": a.is_elective,
                    "is_evening_weekend": a.is_evening_weekend,
                    "intake": a.intake,
                    # Named student group (e.g. "Group A" / "Group B") this
                    # course is restricted to, distinct from the special
                    # intake cohort/academic-year grouping.
                    "student_group_id": a.student_group_id,
                    "student_group_name": a.student_group.name if a.student_group else "",
                    "special_intake_group_id": a.special_intake_group_id,
                    "special_intake_group_name": a.special_intake_group.academic_year if a.special_intake_group else "",
                }
                for a in qs
            ]
            return JsonResponse({"status": "success", "allocations": data})

        # -----------------------
        # Bulk group actions — right-click on a Program/Year(/Semester)
        # header in either the normal or special-intake table.
        # -----------------------
        if action == "bulk_update_group_enrollment":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err

            program_id = request.POST.get("program_id")
            year = request.POST.get("year")
            semester = request.POST.get("semester")  # optional — blank means "all semesters"
            intake = request.POST.get("intake", CourseAllocation.INTAKE_NORMAL)
            number_of_students = request.POST.get("number_of_students")

            if not program_id or not year:
                return JsonResponse({
                    "status": "error", "message": "Program and year are required."
                }, status=400)
            if intake not in (CourseAllocation.INTAKE_NORMAL, CourseAllocation.INTAKE_SPECIAL):
                return JsonResponse({"status": "error", "message": "Invalid intake type."}, status=400)

            try:
                year = int(year)
                number_of_students = int(number_of_students)
            except (TypeError, ValueError):
                return JsonResponse({
                    "status": "error", "message": "Invalid year or enrollment number."
                }, status=400)
            if number_of_students < 0 or number_of_students > 10000:
                return JsonResponse({
                    "status": "error", "message": "Enrollment must be between 0 and 10000."
                }, status=400)

            qs = CourseAllocation.objects.filter(
                Q(department=dept) | Q(origin_department=dept),
                program_id=program_id,
                program_course__year=year,
                intake=intake,
            )
            if semester:
                try:
                    qs = qs.filter(program_course__semester=int(semester))
                except ValueError:
                    pass

            with transaction.atomic():
                # Capture which rows are affected first so the frontend can
                # patch just those cells instead of reloading the page.
                updated_ids = list(qs.values_list("id", flat=True))
                updated = qs.update(number_of_students=number_of_students)

            return JsonResponse({
                "status": "success",
                "message": f"Updated enrollment to {number_of_students} for {updated} course(s).",
                "updated_count": updated,
                "updated_ids": updated_ids,
                "number_of_students": number_of_students,
            })

        if action == "bulk_delete_group_allocations":
            dept = detect_user_department(request.user)
            err = _assert_owns_department(request, dept)
            if err:
                return err

            program_id = request.POST.get("program_id")
            year = request.POST.get("year")
            semester = request.POST.get("semester")  # optional — blank means "all semesters"
            intake = request.POST.get("intake", CourseAllocation.INTAKE_NORMAL)

            if not program_id or not year:
                return JsonResponse({
                    "status": "error", "message": "Program and year are required."
                }, status=400)
            if intake not in (CourseAllocation.INTAKE_NORMAL, CourseAllocation.INTAKE_SPECIAL):
                return JsonResponse({"status": "error", "message": "Invalid intake type."}, status=400)

            try:
                year = int(year)
            except (TypeError, ValueError):
                return JsonResponse({"status": "error", "message": "Invalid year."}, status=400)

            qs = CourseAllocation.objects.filter(
                Q(department=dept) | Q(origin_department=dept),
                program_id=program_id,
                program_course__year=year,
                intake=intake,
            )
            if semester:
                try:
                    qs = qs.filter(program_course__semester=int(semester))
                except ValueError:
                    pass

            with transaction.atomic():
                # Capture which rows are being removed first so the frontend
                # can delete just those DOM rows instead of reloading.
                deleted_ids = list(qs.values_list("id", flat=True))
                deleted_count = len(deleted_ids)
                qs.delete()

            return JsonResponse({
                "status": "success",
                "message": f"Deleted {deleted_count} course allocation(s).",
                "deleted_count": deleted_count,
                "deleted_ids": deleted_ids,
            })

        # Should be unreachable due to whitelist above, but kept as safety net
        return JsonResponse({"status": "error", "message": "Unknown action"}, status=400)

    # -----------------------
    # GET request
    # -----------------------
    detected_dept = detect_user_department(request.user)

    if detected_dept:
        from django.db.models import Case, When, Value, IntegerField
        allocations = (
            CourseAllocation.objects
            .select_related(
                "department", "origin_department", "program", "program__department",
                "lecturer", "program_course", "selection_group", "special_intake_group",
                "student_group"
            )
            .filter(
                _Q(department=detected_dept) | _Q(origin_department=detected_dept)
            )
            .annotate(
                # 0 = own dept program (shown first), 1 = cross-dept program (shown after)
                is_cross_dept_order=Case(
                    When(program__department=detected_dept, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
                # 0 = not mapped to any specialization stem (shown first), 1 = mapped to a stem (shown after)
                stem_sort_order=Case(
                    When(specialization_stem__isnull=True, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
            )
            .order_by(
                "is_cross_dept_order",   # own-dept programs FIRST
                "program__name",
                "program_course__year",
                "stem_sort_order",        # courses not in any stem FIRST, then grouped by stem
                "specialization_stem__name",
                "program_course__semester",
                "student_group__letter",  # NULL (shared/ungrouped) sorts first, then A, B, C…
                "course_code",
            )
        )
        departments = Department.objects.select_related("faculty").all()

        lecturers = Lecturer.objects.all().annotate(
            is_dept=Case(
                When(department=detected_dept, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        ).order_by("-is_dept", "name")

        programs = Program.objects.filter(department=detected_dept)

        selection_groups = (
            SelectionGroup.objects
            .filter(department=detected_dept)
            .prefetch_related("courses__lecturer")
            .select_related("program")
        )
        
        combined_groups = (
            CombinedCourseGroup.objects
            .filter(department=detected_dept)
            .prefetch_related('allocations__department', 'allocations__program', 'allocations__lecturer')
            .select_related('lecturer')
        )

        specialization_categories = (
            SpecializationCategory.objects
            .filter(department=detected_dept)
            .select_related("program")
            .prefetch_related("stems__courses")
            .annotate(stem_total=Count("stems", distinct=True))
            .order_by("program__name", "name")
        )

        special_intake_groups = (
            SpecialIntakeGroup.objects
            .filter(program__department=detected_dept)
            .select_related("program")
            .prefetch_related("course_allocations")
            .order_by("program__name", "-entry_year", "year", "semester")
        )

        student_groups = (
            StudentGroup.objects
            .filter(program__department=detected_dept)
            .select_related("program")
            .prefetch_related("course_allocations")
            .order_by("program__name", "year", "semester", "letter")
        )
    else:
        allocations = CourseAllocation.objects.none()
        departments = Department.objects.none()
        lecturers = Lecturer.objects.none()
        programs = Program.objects.none()
        selection_groups = SelectionGroup.objects.none()
        combined_groups = CombinedCourseGroup.objects.none()
        specialization_categories = SpecializationCategory.objects.none()
        special_intake_groups = SpecialIntakeGroup.objects.none()
        student_groups = StudentGroup.objects.none()

    # FIXED: Use the updated get_control function that handles duplicates
    control = get_control(detected_dept) if detected_dept else None

    # Build a dict: allocation_id -> combined group info so the template can
    # render an inline badge + member list on every row that is part of a group.
    # We store simple NamespacedObjects on a per-allocation basis so the Django
    # template can access them directly (avoids needing a custom templatetag filter).
    combined_info = {}   # id -> dict, kept for JS json_script below
    for cg in combined_groups:
        members = []
        total = 0
        for a in cg.allocations.all():
            members.append({
                "course_code": a.course_code,
                "dept": a.department.name if a.department_id else "",
                "program": a.program.name if a.program_id else "",
                "students": a.number_of_students,
            })
            total += a.number_of_students
        group_data = {
            "group_id": cg.id,
            "group_code": cg.group_code,
            "base_course_code": cg.base_course_code,
            "total_students": total,
            "lecturer_name": cg.lecturer.display_name if cg.lecturer else "",
            "members": members,
        }
        for a in cg.allocations.all():
            combined_info[a.id] = group_data

    # Split regular allocations into own-dept-first, cross-dept-after
    regular_qs = allocations.filter(is_evening_weekend=False, intake=CourseAllocation.INTAKE_NORMAL)
    own_dept_regular    = regular_qs.filter(
        _Q(program__department=detected_dept) | _Q(program__isnull=True)
    )
    cross_dept_regular  = regular_qs.exclude(
        _Q(program__department=detected_dept) | _Q(program__isnull=True)
    )

    # Same split for evening allocations
    evening_qs = allocations.filter(is_evening_weekend=True)
    own_dept_evening   = evening_qs.filter(
        _Q(program__department=detected_dept) | _Q(program__isnull=True)
    )
    cross_dept_evening = evening_qs.exclude(
        _Q(program__department=detected_dept) | _Q(program__isnull=True)
    )

    # Same split for special intake
    special_qs = allocations.filter(intake=CourseAllocation.INTAKE_SPECIAL)
    own_dept_special   = special_qs.filter(
        _Q(program__department=detected_dept) | _Q(program__isnull=True)
    )
    cross_dept_special = special_qs.exclude(
        _Q(program__department=detected_dept) | _Q(program__isnull=True)
    )

    # Annotate each split queryset with cg_info so the template can render
    # combined-group badges without needing a custom templatetag filter.
    def _annotate_cg(qs):
        rows = list(qs)
        for a in rows:
            a.cg_info = combined_info.get(a.id)
        return rows

    own_dept_regular   = _annotate_cg(own_dept_regular)
    cross_dept_regular = _annotate_cg(cross_dept_regular)
    own_dept_evening   = _annotate_cg(own_dept_evening)
    cross_dept_evening = _annotate_cg(cross_dept_evening)
    own_dept_special   = _annotate_cg(own_dept_special)
    cross_dept_special = _annotate_cg(cross_dept_special)

    return render(request, "course_management/cod_panel.html", {
        "allocations": allocations,
        "regular_allocations": regular_qs,
        "own_dept_regular": own_dept_regular,
        "cross_dept_regular": cross_dept_regular,
        "evening_allocations": evening_qs,
        "own_dept_evening": own_dept_evening,
        "cross_dept_evening": cross_dept_evening,
        "special_intake_allocations": special_qs,
        "own_dept_special": own_dept_special,
        "cross_dept_special": cross_dept_special,
        "departments": departments,
        "lecturers": lecturers,
        "programs": programs,
        "years": range(1, 7),
        "detected_dept": detected_dept,
        "allow_submission_to_dvc": control.allow_submission_to_dvc if control else False,
        "allow_submission_to_tt": control.allow_submission_to_tt if control else False,
        "selection_groups": selection_groups,
        "combined_groups": combined_groups,
        "combined_info": combined_info,
        "specialization_categories": specialization_categories,
        "special_intake_groups": special_intake_groups,
        "student_groups": student_groups,
    })


# SECURITY FIX #9 – api_course_codes was missing @login_required
@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def api_course_codes(request):
    """
    Returns JSON list of course codes ordered so that courses belonging to the
    user's own department's programs come FIRST, followed by all other courses.

    Query params:
      program_id    - filter to a single program
      department_id - filter to a specific department's programs
      user_dept_id  - the COD's own dept ID (drives priority ordering)
    """
    from django.db.models import Case, When, Value, IntegerField

    program_id    = request.GET.get("program_id")
    department_id = request.GET.get("department_id")
    user_dept_id  = request.GET.get("user_dept_id")

    if program_id:
        courses = ProgramCourse.objects.filter(program_id=program_id)
    elif department_id:
        courses = ProgramCourse.objects.filter(program__department_id=department_id)
    else:
        courses = ProgramCourse.objects.all()

    courses = courses.select_related("program__department")

    if user_dept_id:
        courses = courses.annotate(
            is_own_dept=Case(
                When(program__department_id=user_dept_id, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        ).order_by("is_own_dept", "program__name", "year", "semester", "course_code")
    else:
        courses = courses.order_by("program__name", "year", "semester", "course_code")

    data = []
    for c in courses:
        own = (str(c.program.department_id) == str(user_dept_id)) if user_dept_id else True
        data.append({
            "code": c.course_code,
            "name": c.course_name,
            "id": c.id,
            "year": c.year,
            "semester": c.semester,
            "program_id": c.program_id,
            "program_name": c.program.name,
            "dept_name": c.program.department.name if c.program.department_id else "",
            "is_own_dept": own,
            "unit_type": c.unit_type,
            "is_elective_unit": c.is_elective_type,
        })
    return JsonResponse({"courses": data})