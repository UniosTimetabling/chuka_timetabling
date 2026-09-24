"""
course_allocation/course_group_planner.py

"Course Groups" panel on /groups-electives/ (Student Group tab): lets a COD
say, for one program/year/semester/intake —

    "split into N student groups" (auto-lettered A, B, C, ... — no need to
    set anything if a course only ever needs ONE group), whether that split
    applies to EVERY compulsory course in that program/year or only a
    hand-picked few, and — when that program/year already has Combination
    Stems set up — pin specific letters to specific stems, so a course
    shared across the whole cohort (e.g. EDFO 111) ends up with one lettered
    section PER STEM (EDFO 111-A for "English/Literature", EDFO 111-B for
    "History/CRE", ...) instead of one shared class for everybody.

Every OTHER course in scope that a stem also takes (e.g. EPSC 111, EDCI 111
alongside EDFO 111) reuses the SAME letter for that stem, so
"English/Literature" ends up with EDFO 111-A, EPSC 111-A and EDCI 111-A
together — one lettered section per stem, not per course.

Stems can also come from a SIBLING program that teaches the same shared
unit — e.g. EDFO 111 might have 5 groups where 3 (A-C) are BEd Arts-only,
1 (D) is BEd Science-only, and 1 (E) is a stem from BEd Arts COMBINED with
a stem from BEd Science taught together as one class. Pinning the SAME
letter to stems from two different programs (see combinable_stems_for)
creates each program's own CourseAllocation row for that letter, then
wraps them together in a CombinedCourseGroup so scheduling treats them as
one session needing a single lecturer/venue/time slot.

Kept independent of course_management.cod_panel (which owns the older,
one-letter-at-a-time Student Group flow used elsewhere in that app) to avoid
a circular import — course_management already imports from
course_allocation at module load time, never the other way round. The
letter-suffix course-code convention ("EDFO 111" -> "EDFO 111-A") is shared
via section_utils.with_group_letter so both flows stay compatible with each
other and with the scheduler.

Programs with NO Combination Stems for a given year/semester simply skip the
stem-pinning step: `stem_letter_map` stays empty and every letter applies
directly to every course in scope, exactly like the plain (non-stem)
Student Group split.
"""
import logging

from django.db import transaction
from django.db.models import Q

from program_management.models import ProgramCourse

from .models import (
    CourseAllocation,
    StudentGroup,
    SpecializationStem,
    StemStudentCount,
    GroupingTemplate,
    GroupingTemplateGroup,
    GroupingTemplateCourse,
    GroupingTemplateStemAssignment,
    CombinedCourseGroup,
)
from .section_utils import with_group_letter
from .allocation_scope import get_or_default_legacy_set

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Letters
# ─────────────────────────────────────────────────────────────────────────
def index_to_letter(i):
    """1-indexed spreadsheet-column style: 1 -> 'A', 26 -> 'Z', 27 -> 'AA', ..."""
    letters = ""
    while i > 0:
        i, rem = divmod(i - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def letters_sequence(n):
    """['A', 'B', ..., 'Z', 'AA', 'AB', ...] — the first n group letters."""
    return [index_to_letter(i) for i in range(1, max(int(n or 0), 0) + 1)]


def letter_to_index(letter):
    """Inverse of index_to_letter: 'A' -> 1, 'Z' -> 26, 'AA' -> 27, ..."""
    n = 0
    for ch in (letter or "").strip().upper():
        if not ("A" <= ch <= "Z"):
            return 0
        n = n * 26 + (ord(ch) - 64)
    return n


def letters_sequence_from(start_index, n):
    """The next n group letters AFTER `start_index` (1-indexed) — e.g.
    letters_sequence_from(5, 3) -> ['F', 'G', 'H']."""
    return [index_to_letter(i) for i in range(start_index + 1, start_index + max(int(n or 0), 0) + 1)]


def max_letter_index_elsewhere(course_codes, exclude_cell=None):
    """
    Highest group-letter index already in use ANYWHERE in the system for any
    of `course_codes` (curriculum course codes, case-insensitive) — so a
    shared/common unit taken by SEVERAL programs (e.g. "EDFO 111" in both
    BEd Arts and BEd Science) gets CONTINUED lettering instead of every
    program restarting at "A": if BEd Arts already used A-E for EDFO 111,
    BEd Science's own split of the same unit starts at F.

    `exclude_cell`, when given, is (program_id, year, semester, intake) —
    the cell this call is computing a range FOR, left out of the scan so a
    plan is never pushed off its own already-reserved letters by counting
    itself (see save_group_plan, which only calls this for a BRAND NEW cell
    in the first place — an existing cell just continues its own letters).
    Returns 0 if the course code(s) have never been split anywhere yet.
    """
    if not course_codes:
        return 0
    code_filter = Q()
    for code in course_codes:
        code_filter |= Q(program_course__course_code__iexact=code)

    qs = (
        CourseAllocation.objects.filter(code_filter, student_group__isnull=False)
        .select_related("student_group")
    )
    if exclude_cell:
        program_id, year, semester, intake = exclude_cell
        qs = qs.exclude(
            program_id=program_id, program_course__year=year,
            program_course__semester=semester, intake=intake,
        )
    max_idx = 0
    for ca in qs.iterator():
        idx = letter_to_index(ca.student_group.letter)
        if idx > max_idx:
            max_idx = idx
    return max_idx


# ─────────────────────────────────────────────────────────────────────────
# Read side — what the panel needs to render
# ─────────────────────────────────────────────────────────────────────────
def eligible_stems_for(program, year, semester):
    """
    Combination Stems ("stems") available for this program/year/semester —
    same eligibility rule as StudentGroup.eligible_stems(), without needing
    a concrete StudentGroup instance to ask it from.
    """
    return (
        SpecializationStem.objects.filter(category__program=program)
        .filter(Q(category__year__isnull=True) | Q(category__year=year))
        .filter(Q(category__semester__isnull=True) | Q(category__semester=semester))
        .select_related("category")
        .distinct()
        .order_by("category__name", "name")
    )


def combinable_stems_for(program, year, semester, course_codes):
    """
    Stems belonging to OTHER programs that could be COMBINED with this one —
    e.g. BEd Arts and BEd Science both teach "EDFO 111"; one lettered
    section of it can be a joint class taken by a stem from EACH program
    together (3 groups pure BEd Arts, 1 pure BEd Science, 1 shared between
    an Arts stem and a Science stem — all under the SAME course EDFO 111).

    A sibling program only shows up here if it actually teaches at least one
    of `course_codes` in the same year/semester — that shared unit is what
    makes combining meaningful; unrelated programs are not listed.

    Scoped to `program`'s own department: course codes are not unique across
    the university, so without this a program in another department that
    happens to reuse a code would leak its stems into this department's list.
    """
    if not course_codes:
        return []
    dept_id = program.department_id
    sibling_program_ids = (
        ProgramCourse.objects.filter(
            year=year, semester=semester, course_code__in=course_codes,
            program__department_id=dept_id,
        )
        .exclude(program=program)
        .values_list("program_id", flat=True)
        .distinct()
    )
    return list(
        SpecializationStem.objects.filter(
            category__program_id__in=sibling_program_ids,
            category__department_id=dept_id,
        )
        .filter(Q(category__year__isnull=True) | Q(category__year=year))
        .filter(Q(category__semester__isnull=True) | Q(category__semester=semester))
        .select_related("category", "category__program")
        .distinct()
        .order_by("category__program__name", "category__name", "name")
    )


def _stem_eligible_for_year_semester(stem, year, semester):
    cat = stem.category
    return (cat.year is None or cat.year == year) and (cat.semester is None or cat.semester == semester)


def compulsory_program_courses(program, year, semester):
    """Curriculum (ProgramCourse) entries eligible for course-group
    splitting: every non-elective unit for this program/year/semester."""
    return (
        ProgramCourse.objects.filter(program=program, year=year, semester=semester)
        .exclude(unit_type="ELECTIVE")
        .order_by("course_code")
    )


def get_plan_context(program, year, semester, intake):
    """
    Everything the "Course Groups" panel needs to render for one
    program/year/semester/intake: the compulsory courses to choose from, the
    combination stems (if any) available to pin letters to (this program's
    own, plus combinable stems from sibling programs teaching the same
    units), and whatever was already saved for this cell (an existing
    GroupingTemplate, its letters, scope, selected course codes, and stem
    pins) — so re-opening the panel shows the current configuration instead
    of a blank form.
    """
    courses = list(compulsory_program_courses(program, year, semester))
    stems = list(eligible_stems_for(program, year, semester))
    combinable = list(combinable_stems_for(program, year, semester, [pc.course_code for pc in courses]))

    template = (
        GroupingTemplate.objects.filter(
            program=program, year=year, semester=semester, intake=intake,
        )
        .prefetch_related("groups", "course_codes", "stem_assignments__stem", "stem_assignments__group")
        .first()
    )

    letters = []
    stem_pins = {}
    selected_codes = []
    scope = GroupingTemplate.SCOPE_ALL
    if template:
        scope = template.scope
        selected_codes = sorted(c.base_course_code for c in template.course_codes.all())
        letters = sorted((g.letter for g in template.groups.all()), key=letter_to_index)
        for sa in template.stem_assignments.all():
            stem_pins.setdefault(sa.stem_id, []).append(sa.group.letter)

    next_letter_hint = None
    if not template and courses:
        elsewhere_idx = max_letter_index_elsewhere(
            [pc.course_code for pc in courses],
            exclude_cell=(program.id, year, semester, intake),
        )
        if elsewhere_idx:
            next_letter_hint = index_to_letter(elsewhere_idx + 1)

    return {
        "courses": [{"id": pc.id, "code": pc.course_code, "name": pc.course_name} for pc in courses],
        "stems": [
            {"id": s.id, "name": s.name, "category_name": s.category.name, "program_name": program.name}
            for s in stems
        ],
        "combinable_stems": [
            {
                "id": s.id, "name": s.name, "category_name": s.category.name,
                "program_id": s.category.program_id, "program_name": s.category.program.name,
            }
            for s in combinable
        ],
        "existing": {
            "num_groups": len(letters),
            "letters": letters,
            "scope": scope,
            "selected_codes": selected_codes,
            "stem_pins": stem_pins,
            "next_letter_hint": next_letter_hint,
        },
    }


# ─────────────────────────────────────────────────────────────────────────
# Write side — materializing letters into real CourseAllocation rows
# ─────────────────────────────────────────────────────────────────────────
def _assign_group_letter(program, pc, intake, department, allocation_set, group, stem=None):
    """
    Ensure a CourseAllocation row exists for curriculum entry `pc`, tagged
    with student_group=`group` (and, when given, specialization_stem=`stem`),
    with its course_code carrying `group`'s letter suffix.

    Mirrors course_management.cod_panel._assign_course_to_group's
    tag-in-place / clone / create-fresh cascade (kept as a separate, local
    implementation — course_allocation cannot import course_management
    without creating a circular import), but additionally scopes the
    ALREADY-ASSIGNED lookup by `stem`.

    `stem` is the row's legacy PRIMARY-stem pointer only, not the real
    membership — that's the `specialization_stems` M2M, updated separately
    by the caller (see _remember_stem_courses / refresh_primary_stem_pointer).
    When several Combination Stems are pinned onto the SAME letter (a
    genuine combination, taught as one class), the caller passes stem=None
    here so every stem's call resolves to the SAME row instead of each
    stem cloning its own duplicate; only a letter pinned to exactly one
    stem passes that stem through, for a precise FK pointer.

    The SHARED (untagged) row search, however, deliberately does NOT filter
    by `stem` — it looks for any plain row with no student_group and no
    stem yet, exactly like the normal COD panel flow does. That plain row
    is what a course has when it was added the ordinary way (COD panel /
    auto-allocate) before any combination-stem split was set up for it, and
    it's meant to be taken over in place — course_code relettered,
    specialization_stem set to `stem` — for the FIRST letter committed,
    rather than left untouched while a brand-new CourseAllocation (and a
    brand-new StudentGroup) is cloned alongside it. Filtering this search
    by stem too was the bug: it could never match a pre-existing plain row
    (whose stem is None), so every first commit cloned a fresh duplicate
    instead of adopting the course you already had.

    Returns (allocation, action) where action is one of "already_assigned",
    "tagged", "cloned", "created_new".
    """
    base_filter = dict(program=program, program_course=pc, intake=intake, specialization_stem=stem)

    existing = CourseAllocation.objects.filter(student_group=group, **base_filter)
    if allocation_set is not None:
        existing = existing.filter(allocation_set=allocation_set)
    existing = existing.first()
    if existing:
        correct_code = with_group_letter(existing.course_code, group.letter)
        if existing.course_code != correct_code:
            existing.course_code = correct_code
            existing.save(update_fields=["course_code"])
        return existing, "already_assigned"

    shared = CourseAllocation.objects.filter(
        program=program, program_course=pc, intake=intake,
        student_group__isnull=True, specialization_stem__isnull=True,
        is_evening_weekend=False, is_elective=False,
    )
    if allocation_set is not None:
        shared = shared.filter(allocation_set=allocation_set)
    shared = shared.first()
    if shared:
        shared.student_group = group
        shared.specialization_stem = stem
        shared.course_code = with_group_letter(shared.course_code, group.letter)
        shared.save(update_fields=["student_group", "specialization_stem", "course_code"])
        return shared, "tagged"

    template_qs = CourseAllocation.objects.filter(
        program=program, program_course=pc, intake=intake, is_evening_weekend=False,
    ).exclude(student_group=group)

    template = None
    if stem is not None:
        # Prefer a sibling row from the SAME stem as a template (its own
        # lecturer/venue are the most relevant defaults); fall back to any
        # sibling row for this course otherwise.
        stem_template_qs = template_qs.filter(specialization_stem=stem)
        if allocation_set is not None:
            template = stem_template_qs.filter(allocation_set=allocation_set).first()
        if template is None:
            template = stem_template_qs.first()
    if template is None:
        if allocation_set is not None:
            template = template_qs.filter(allocation_set=allocation_set).first()
        if template is None:
            template = template_qs.first()

    if template:
        clone = CourseAllocation.objects.create(
            course_code=with_group_letter(template.course_code, group.letter),
            course_name=template.course_name,
            department=template.department,
            origin_department=template.origin_department,
            program=program,
            program_course=pc,
            lecturer=template.lecturer,
            number_of_students=0,
            intake=intake,
            is_elective=False,
            is_evening_weekend=False,
            student_group=group,
            specialization_stem=stem,
            allocation_set=allocation_set or template.allocation_set,
        )
        return clone, "cloned"

    fresh = CourseAllocation.objects.create(
        course_code=with_group_letter(pc.course_code, group.letter),
        course_name=pc.course_name,
        department=department,
        origin_department=department,
        program=program,
        program_course=pc,
        lecturer=None,
        number_of_students=0,
        intake=intake,
        is_elective=False,
        is_evening_weekend=False,
        student_group=group,
        specialization_stem=stem,
        allocation_set=allocation_set or get_or_default_legacy_set(department),
    )
    return fresh, "created_new"


def _remember_stem_courses(stem, allocations, user):
    """Same bookkeeping quick_create_stem_for_student_group does: add the
    new rows to the stem's course set (and its "mapped in advance" curriculum
    list), then keep the singular primary-stem pointer consistent."""
    if not allocations:
        return
    stem.courses.add(*allocations)
    pcs = {a.program_course for a in allocations}
    stem.program_courses.add(*pcs)
    for ca in allocations:
        ca.refresh_primary_stem_pointer()


@transaction.atomic
def save_group_plan(*, program, year, semester, intake, department, user,
                     num_groups, scope, selected_program_course_ids=None,
                     stem_letter_map=None, allocation_set=None,
                     explicit_letters=None, plain_letters=None):
    """
    Main entry point for the Course Groups panel's Save/Apply action.

    num_groups: total lettered groups needed for this program/year/semester/
        intake. 1 (or less) means "no split needed" — a no-op, matching
        "if a course needs 1 no need of setting".
    scope: GroupingTemplate.SCOPE_ALL (every compulsory course) or
        GroupingTemplate.SCOPE_SELECTED (only `selected_program_course_ids`).
    selected_program_course_ids: required when scope is SCOPE_SELECTED — a
        list/iterable of ProgramCourse ids, a subset of
        compulsory_program_courses() for this program/year/semester.
    stem_letter_map: optional {stem_id: [letter, ...]} pinning specific
        letters to specific Combination Stems, for programs that have them.
        Every course in scope gets its own lettered section PER STEM listed
        here (so several stems can each take a different section of the
        same shared course). Letters not mentioned here still exist as
        plain StudentGroup rows (so the count/letters are correct) but get
        no stem-tied CourseAllocation rows created for them.
        Leave empty/None to apply every letter directly to every course in
        scope with no stem involved at all (the plain, non-combination
        split) — this is also what happens automatically for a program/year
        that has no Combination Stems.
    explicit_letters: letters that MUST be available in this call's
        groups_by_letter, verbatim, regardless of what the continuation
        math below would otherwise generate. For callers (like
        commit_draft) pinning ALREADY-FIXED, shared-across-programs
        letters, the auto-continuation numbering (which invents a fresh
        range per program based on what's used elsewhere) can land on a
        different range than the letter actually being pinned — silently
        dropping the pin. Passing the exact letters here guarantees they
        exist no matter what the continuation math computes.
    plain_letters: specific letters (usually a subset of explicit_letters)
        to materialize as plain (no-stem) CourseAllocation rows — run
        UNCONDITIONALLY, alongside any stem_letter_map pinning in the same
        call, rather than only when stem_letter_map is empty. This lets one
        commit correctly mix "this letter maps to stem X" with "this other
        letter is deliberately unstemmed" for the same program.

    Returns a summary dict for the AJAX response.
    """
    num_groups = max(int(num_groups or 1), 1)
    if scope not in (GroupingTemplate.SCOPE_ALL, GroupingTemplate.SCOPE_SELECTED):
        scope = GroupingTemplate.SCOPE_ALL
    stem_letter_map = stem_letter_map or {}
    explicit_letters = [str(l).strip().upper() for l in (explicit_letters or []) if str(l).strip()]
    plain_letters = [str(l).strip().upper() for l in (plain_letters or []) if str(l).strip()]

    # ── Resolve the course scope ────────────────────────────────────────
    all_courses = list(compulsory_program_courses(program, year, semester))
    if scope == GroupingTemplate.SCOPE_SELECTED:
        wanted_ids = {int(pk) for pk in (selected_program_course_ids or [])}
        target_courses = [pc for pc in all_courses if pc.id in wanted_ids]
    else:
        target_courses = all_courses

    if not target_courses:
        return {
            "groups": [], "created": 0, "tagged": 0, "already_assigned": 0,
            "combined_groups": [],
            "message": "No matching courses found for this program/year/semester.",
        }

    if num_groups <= 1 and not stem_letter_map and not explicit_letters and not plain_letters:
        return {
            "groups": [], "created": 0, "tagged": 0, "already_assigned": 0,
            "combined_groups": [],
            "message": "Only one group is needed here — nothing to split.",
        }

    # ── Work out which letters this cell should use ─────────────────────
    # An existing plan for this EXACT program/year/semester/intake keeps its
    # own already-reserved letters and simply continues past its own last
    # one if `num_groups` grew. A BRAND NEW plan, though, checks whether any
    # of the courses in scope are a shared/common unit ALREADY split
    # elsewhere (a different program, year, or intake) — e.g. "EDFO 111" has
    # taken A-E in BEd Arts — and continues from there (starting at "F")
    # instead of colliding by restarting at "A".
    existing_template = (
        GroupingTemplate.objects.filter(program=program, year=year, semester=semester, intake=intake)
        .prefetch_related("groups")
        .first()
    )
    existing_letters = []
    if existing_template:
        existing_letters = sorted(
            (g.letter for g in existing_template.groups.all()), key=letter_to_index,
        )

    if existing_letters:
        start_index = max(letter_to_index(l) for l in existing_letters)
    else:
        start_index = max_letter_index_elsewhere(
            [pc.course_code for pc in target_courses],
            exclude_cell=(program.id, year, semester, intake),
        )

    needed_new = max(num_groups - len(existing_letters), 0)
    new_letters = letters_sequence_from(start_index, needed_new)
    letters = existing_letters + new_letters

    continued_from = None
    if not existing_letters and start_index:
        continued_from = index_to_letter(start_index + 1)

    # A pinned letter (from commit_draft's shared, already-fixed lettering)
    # always wins over whatever the continuation math above generated —
    # union it in rather than trusting it fell inside the generated range.
    if explicit_letters:
        letters = sorted(set(letters) | set(explicit_letters), key=letter_to_index)

    # ── Ensure the lettered StudentGroup rows exist ─────────────────────
    groups_by_letter = {}
    for letter in letters:
        group, _ = StudentGroup.objects.get_or_create(
            program=program, year=year, semester=semester, intake=intake, letter=letter,
            defaults={"name": f"Group {letter}", "created_by": user},
        )
        groups_by_letter[letter] = group

    # ── Validate the stem/letter pins ───────────────────────────────────
    # Stems may belong to THIS program or to a sibling program that also
    # teaches one of the courses in scope (a cross-program combine — see
    # combinable_stems_for). Either way the only real eligibility rule is
    # the stem's own year/semester window — and it must sit in THIS program's
    # department (server-side guard; the panel never offers other departments'
    # stems, but a crafted/stale request must not be able to pin one either).
    stems_by_id = {s.id: s for s in SpecializationStem.objects.filter(
                       id__in=stem_letter_map.keys(),
                       category__department_id=program.department_id)
                   .select_related("category", "category__program")}
    clean_stem_letter_map = {}
    skipped_stems = []
    for stem_id, stem_letters in (stem_letter_map or {}).items():
        stem_id = int(stem_id)
        stem = stems_by_id.get(stem_id)
        if not stem or not _stem_eligible_for_year_semester(stem, year, semester):
            skipped_stems.append(stem_id)
            continue
        valid_letters = [l for l in stem_letters if l in groups_by_letter]
        if valid_letters:
            clean_stem_letter_map[stem_id] = valid_letters

    # ── Materialize CourseAllocation rows ───────────────────────────────
    created = tagged = already_assigned = 0
    affected = []
    stem_pins_for_memory = []          # [(stem, letter), ...] -> _remember_plan
    combine_tracker = {}               # (course_code_upper, letter) -> [CourseAllocation, ...]
    program_letter_groups = {program.id: groups_by_letter}  # memoized per-program StudentGroup letters
    skipped_sibling_courses = []       # sibling program doesn't teach this course

    def _group_for(prog, letter):
        by_letter = program_letter_groups.setdefault(prog.id, {})
        grp = by_letter.get(letter)
        if grp is None:
            grp, _ = StudentGroup.objects.get_or_create(
                program=prog, year=year, semester=semester, intake=intake, letter=letter,
                defaults={"name": f"Group {letter}", "created_by": user},
            )
            by_letter[letter] = grp
        return grp

    if clean_stem_letter_map:
        # Several Combination Stems can be pinned onto the SAME letter within
        # the same program (that's the whole point of a "combination" —
        # e.g. AI + Cybersecurity both sit in "EDFO 111-A" and are taught as
        # one physical class). Group by (program, letter) FIRST so that case
        # creates exactly ONE CourseAllocation row per letter+course, shared
        # across every stem mapped to it via the stem.courses M2M — instead
        # of the old per-stem loop, which called _assign_group_letter once
        # per stem with stem=<that stem>, and because its "already assigned"
        # lookup is scoped by that single stem, could never find the row a
        # sibling stem on the same letter had just created — so every extra
        # stem on the same letter cloned its own duplicate "EDFO 111-A" row
        # instead of joining the one that already existed.
        letter_groups = {}  # (stem_program_id, letter) -> [stem, ...]
        for stem_id, stem_letters in clean_stem_letter_map.items():
            stem = stems_by_id[stem_id]
            stem_program = stem.category.program
            for letter in stem_letters:
                letter_groups.setdefault((stem_program.id, letter), []).append(stem)

        for (stem_program_id, letter), stems in letter_groups.items():
            stem_program = stems[0].category.program
            group = _group_for(stem_program, letter)
            shared_allocations = []
            # A single stem on this letter keeps the precise FK pointer set
            # (stem=stems[0]); several stems sharing it leave stem=None here
            # — refresh_primary_stem_pointer() (via _remember_stem_courses
            # below) is what actually decides the pointer from the real M2M
            # membership once every stem below has been added to the row.
            pin_stem = stems[0] if len(stems) == 1 else None
            for pc in target_courses:
                if stem_program.id == program.id:
                    target_pc = pc
                else:
                    target_pc = ProgramCourse.objects.filter(
                        program=stem_program, course_code__iexact=pc.course_code,
                        year=year, semester=semester,
                    ).first()
                    if target_pc is None:
                        for stem in stems:
                            skipped_sibling_courses.append((stem.name, stem_program.name, pc.course_code))
                        continue
                alloc, action = _assign_group_letter(
                    stem_program, target_pc, intake, department, allocation_set, group, stem=pin_stem,
                )
                shared_allocations.append(alloc)
                if action == "already_assigned":
                    already_assigned += 1
                elif action == "tagged":
                    tagged += 1
                else:
                    created += 1
                for stem in stems:
                    affected.append({
                        "id": alloc.id, "course_code": alloc.course_code, "letter": letter,
                        "stem_id": stem.id, "stem_name": stem.name, "program_name": stem_program.name,
                    })
                combine_tracker.setdefault((pc.course_code.upper(), letter), []).append(alloc)
            for stem in stems:
                _remember_stem_courses(stem, shared_allocations, user)
                stem_pins_for_memory.append((stem, letter))

    # Plain (no-stem) letters: explicit_letters/plain_letters callers (like
    # commit_draft) may request specific letters be committed with no stem
    # IN THE SAME call as other letters going to stems above — so this runs
    # unconditionally rather than only when clean_stem_letter_map is empty.
    # A caller with no explicit plain_letters at all keeps the original
    # behavior: every letter in scope goes plain, but ONLY when nothing was
    # pinned to a stem (a plain, non-combination program/year).
    letters_to_plain = plain_letters if (explicit_letters or plain_letters) else (
        [] if clean_stem_letter_map else letters
    )
    for letter in letters_to_plain:
        group = groups_by_letter.get(letter)
        if group is None:
            continue
        for pc in target_courses:
            alloc, action = _assign_group_letter(
                program, pc, intake, department, allocation_set, group, stem=None,
            )
            if action == "already_assigned":
                already_assigned += 1
            elif action == "tagged":
                tagged += 1
            else:
                created += 1
            affected.append({
                "id": alloc.id, "course_code": alloc.course_code,
                "letter": letter, "stem_id": None, "stem_name": None, "program_name": program.name,
            })

    # ── Combine cross-program stems that share a letter+course ──────────
    # If TWO (or more) programs each ended up with their own row for the
    # SAME course+letter (because their stems were pinned to that letter
    # together), that is one physical class taught to both cohorts at once
    # — wrap those rows in a CombinedCourseGroup so scheduling treats them
    # as a single session needing one lecturer/venue/time slot.
    combined_groups = []
    for (course_code, letter), allocs in combine_tracker.items():
        program_ids = {a.program_id for a in allocs}
        if len(program_ids) < 2:
            continue
        group_code = allocs[0].course_code
        cg, _ = CombinedCourseGroup.objects.get_or_create(
            group_code=group_code, allocation_set=allocation_set,
            defaults={
                "base_course_code": course_code, "department": department,
                "origin_department": department, "created_by": user,
            },
        )
        cg.allocations.add(*allocs)
        if not cg.primary_allocation_id:
            cg.primary_allocation = allocs[0]
            cg.save(update_fields=["primary_allocation"])
        combined_groups.append({
            "group_code": group_code, "letter": letter,
            "programs": sorted({a.program.name for a in allocs}),
        })

    _remember_plan(
        program, year, semester, intake, letters, scope, target_courses,
        stem_pins_for_memory, user,
    )

    message = f"{len(letters)} group(s) ({', '.join(letters)}) applied to {len(target_courses)} course(s)."
    if continued_from:
        message += (
            f" Continued from letter {continued_from} — one of these courses is already split "
            f"elsewhere (another program, year, or intake), so lettering carries on from there "
            f"instead of restarting at A."
        )
    if clean_stem_letter_map:
        message += f" Pinned to {len(clean_stem_letter_map)} combination stem(s)."
    if combined_groups:
        message += f" {len(combined_groups)} of those are combined across programs."
    if skipped_stems:
        message += f" ({len(skipped_stems)} stem selection(s) ignored — not eligible for this program/year.)"
    if skipped_sibling_courses:
        message += f" ({len(skipped_sibling_courses)} sibling-program course lookup(s) skipped — that program doesn't teach it.)"

    return {
        "groups": [{"id": g.id, "letter": l, "name": g.name} for l, g in groups_by_letter.items()],
        "affected": affected,
        "combined_groups": combined_groups,
        "created": created,
        "tagged": tagged,
        "already_assigned": already_assigned,
        "continued_from": continued_from,
        "message": message,
    }


# ─────────────────────────────────────────────────────────────────────────
# Student numbers — "Update student numbers" on the Combination Stem
# Allocations panel
# ─────────────────────────────────────────────────────────────────────────
def stem_student_number_terms(stem):
    """
    One row per Year/Semester term this stem's CORE courses actually occupy
    (same term grouping as groups_electives_page._term_blocks, but only the
    fields the "Student numbers" dialog needs). Nested elective-pool
    alternatives are left out — students choose ONE of those individually,
    so a single shared headcount would misstate them.

    Each row's `number_of_students` is the value already saved on this
    term's CourseAllocation rows when they all agree, the StemStudentCount
    on file when they don't (or there are no allocations yet), or 0.
    Courses whose curriculum entry is missing (no Year/Semester to group by)
    are skipped — there's nothing to key a headcount on.
    """
    pool_course_ids = set()
    for pool in stem.elective_groups.all():
        pool_course_ids.update(pool.courses.values_list("id", flat=True))

    core = (
        stem.courses.exclude(id__in=pool_course_ids)
        .exclude(program_course__isnull=True)
        .select_related("program_course")
    )
    buckets = {}
    for ca in core:
        key = (ca.program_course.year, ca.program_course.semester)
        buckets.setdefault(key, []).append(ca)

    saved = {
        (c.year, c.semester): c.number_of_students
        for c in stem.student_counts.all()
    }

    rows = []
    for (year, semester) in sorted(buckets):
        if not year or not semester:
            continue
        allocs = buckets[(year, semester)]
        counts = {a.number_of_students for a in allocs}
        agreed = counts.pop() if len(counts) == 1 else None
        number_of_students = agreed if agreed is not None else saved.get((year, semester), 0)
        rows.append({
            "year": year, "semester": semester,
            "label": f"Year {year} Semester {semester}",
            "course_count": len(allocs),
            "number_of_students": number_of_students,
        })
    return rows


def apply_stem_student_count(*, stem, year, semester, number_of_students, user):
    """
    Upsert the (stem, year, semester) headcount and push it onto every
    CORE CourseAllocation already attached to this stem for that term.
    Returns (StemStudentCount, number_of_courses_updated).
    """
    count, _ = StemStudentCount.objects.update_or_create(
        stem=stem, year=year, semester=semester,
        defaults={"number_of_students": number_of_students, "updated_by": user},
    )
    pool_course_ids = set()
    for pool in stem.elective_groups.all():
        pool_course_ids.update(pool.courses.values_list("id", flat=True))
    updated = (
        stem.courses
        .exclude(id__in=pool_course_ids)
        .filter(program_course__year=year, program_course__semester=semester)
        .update(number_of_students=number_of_students)
    )
    return count, updated


def _remember_plan(program, year, semester, intake, letters, scope, target_courses,
                    stem_pins, user):
    """
    Upsert the GroupingTemplate knowledge-base entry for this
    program/year/semester/intake so auto-allocate can recreate the same
    split (and, going forward, the same stem pins) the next time it rebuilds
    this department's allocations — same idea as
    course_management.cod_panel._remember_grouping_template.

    stem_pins: [(stem, letter), ...]. A stem's OWN program (which may be a
    sibling program combined into this plan, not just `program` itself) gets
    its own GroupingTemplate/letter row created on demand, since
    GroupingTemplate is itself scoped per program.
    """
    template, _ = GroupingTemplate.objects.get_or_create(
        program=program, year=year, semester=semester, intake=intake,
        defaults={"scope": scope, "created_by": user},
    )
    if template.scope != scope:
        template.scope = scope
        template.save(update_fields=["scope"])

    for letter in letters:
        GroupingTemplateGroup.objects.get_or_create(template=template, letter=letter)

    if scope == GroupingTemplate.SCOPE_SELECTED:
        for pc in target_courses:
            GroupingTemplateCourse.objects.get_or_create(
                template=template, base_course_code=pc.course_code,
            )

    template_cache = {program.id: template}

    def _template_for(prog):
        tmpl = template_cache.get(prog.id)
        if tmpl is None:
            tmpl, _ = GroupingTemplate.objects.get_or_create(
                program=prog, year=year, semester=semester, intake=intake,
                defaults={"scope": scope, "created_by": user},
            )
            template_cache[prog.id] = tmpl
        return tmpl

    for stem, letter in stem_pins:
        stem_program = stem.category.program
        stem_template = _template_for(stem_program)
        grp_def, _ = GroupingTemplateGroup.objects.get_or_create(template=stem_template, letter=letter)
        GroupingTemplateStemAssignment.objects.update_or_create(
            template=stem_template, group=grp_def, defaults={"stem": stem, "created_by": user},
        )
