# -----------------------------------------------------------------------
# "Course Groups" tab — quick-create-by-course-code flow.
#
# Lets a COD create a course-group split (e.g. "EDFO 111" -> 15 groups)
# straight from a course code, with NO program/year/semester chosen yet.
# A program is mapped in afterwards, one at a time, in a matrix: adding a
# program shows its Combination Stems as rows, the draft's letters as
# columns, and a checkbox at each intersection. Nothing touches real
# StudentGroup / CourseAllocation / SpecializationStem data until Commit,
# which fans every pending checkbox out to the existing
# course_group_planner.save_group_plan() engine (one call per program).
#
# A committed checkbox's CourseGroupDraftMapping row is kept, not deleted
# -- it is the durable record of "this letter belongs to this stem",
# separate from whether the real CourseAllocation/SpecializationStem rows
# behind it currently exist. draft_matrix() decides pending vs. committed
# display by checking the LIVE data on every read, not by whether the
# mapping row itself still exists. So if something elsewhere clears the
# real allocations for a letter (e.g. a "clear allocation" action used to
# rebuild a course from scratch), the mapping quietly reappears as pending
# here instead of vanishing -- Commit can just be run again to restore the
# same stem split, without the COD re-ticking every checkbox from memory.
# A mapping row is only ever removed by an explicit action: unticking its
# checkbox (toggle_mapping), dropping its letter (delete_letter), or
# deleting the whole draft.
#
# Year/semester for a mapping are never typed by hand: they are read off
# the target program's OWN ProgramCourse entry for this exact course code
# the moment the program is added to the matrix (see mappable_programs_for).
# -----------------------------------------------------------------------
from collections import Counter

from django.db import transaction

from program_management.models import Program, ProgramCourse
from lecturer_portal.models import Lecturer

from .models import (
    CourseGroupDraft, CourseGroupDraftLetter, CourseGroupDraftMapping,
    SpecializationStem, CourseAllocation,
)
from . import course_group_planner
from .models import GroupingTemplate
from .allocation_scope import get_active_allocation_set, get_or_default_legacy_set


# ───────────────────────── course picklist (left form) ─────────────────────────

def department_course_choices(department):
    """
    Every distinct course code known in this department's curriculum
    (ProgramCourse), alphabetically — feeds both the free-type box and the
    scrollable multi-select on the Course Groups quick-create form.

    Each entry also carries `occ`: every distinct (program, year, semester)
    the code is taught under in this department, so the picklist can be
    filtered by program/year/semester on the client without another round
    trip — a course with no program/year/semester chosen yet can still be
    taught under several programs/terms at once, so this is a list, not a
    single value.
    """
    rows = (
        ProgramCourse.objects
        .filter(program__department=department)
        .select_related("program")
        .values_list("course_code", "course_name", "program_id", "program__name", "year", "semester")
        .order_by("course_code")
        .distinct()
    )
    seen = {}
    occ_seen = {}
    for code, name, program_id, program_name, year, semester in rows:
        code = (code or "").strip()
        if not code:
            continue
        key = code.upper()
        if key not in seen:
            seen[key] = {"code": code, "name": (name or "").strip(), "occ": []}
            occ_seen[key] = set()
        occ_key = (program_id, year, semester)
        if occ_key not in occ_seen[key]:
            occ_seen[key].add(occ_key)
            seen[key]["occ"].append({
                "program_id": program_id, "program_name": program_name,
                "year": year, "semester": semester,
            })
    return sorted(seen.values(), key=lambda r: r["code"].upper())


def lecturer_choices(department):
    """Every lecturer, for the per-letter lecturer dropdown — this department's
    own lecturers first (alphabetical), then everyone else (alphabetical),
    matching the existing 'assign a lecturer' convention used elsewhere in
    this app (plain FK, no cross-department restriction — a lecturer can be
    borrowed from another department)."""
    qs = Lecturer.objects.order_by("name")
    own, other = [], []
    for l in qs:
        (own if l.department_id == department.id else other).append(l)
    return [{"id": l.id, "name": l.display_name} for l in own + other]


# ───────────────────────── create / resize a draft ─────────────────────────

def _suggested_term(department, course_code):
    """Mode (most common) year & semester across every ProgramCourse in the
    department carrying this code — display-only hint for an as-yet-unmapped
    course; never fed back into save_group_plan."""
    pairs = list(
        ProgramCourse.objects
        .filter(program__department=department, course_code__iexact=course_code)
        .values_list("year", "semester")
    )
    if not pairs:
        return None, None
    years = Counter(p[0] for p in pairs)
    sems = Counter(p[1] for p in pairs)
    return years.most_common(1)[0][0], sems.most_common(1)[0][0]


def create_or_update_drafts(*, department, user, course_entries, num_groups):
    """
    course_entries: list of {"code": str, "name": str} — either typed by hand
    (one row, name blank) or picked from the department multi-select. Every
    entry gets its own CourseGroupDraft (get_or_create by course_code), all
    resized to the SAME num_groups. Returns the list of drafts touched.
    """
    num_groups = max(int(num_groups or 1), 1)
    drafts = []
    for entry in course_entries:
        code = (entry.get("code") or "").strip()
        if not code:
            continue
        name = (entry.get("name") or "").strip()
        year, sem = _suggested_term(department, code)
        draft, created = CourseGroupDraft.objects.get_or_create(
            department=department, course_code=code,
            defaults={
                "course_name": name, "num_groups": num_groups,
                "suggested_year": year, "suggested_semester": sem,
                "created_by": user,
            },
        )
        if not created:
            draft.num_groups = max(draft.num_groups, num_groups)
            if name and not draft.course_name:
                draft.course_name = name
            draft.suggested_year, draft.suggested_semester = year, sem
            draft.save(update_fields=["num_groups", "course_name", "suggested_year", "suggested_semester", "updated_at"])
        _ensure_letters(draft)
        drafts.append(draft)
    return drafts


def _ensure_letters(draft):
    """Create fresh CourseGroupDraftLetter rows up to draft.num_groups,
    continuing from the highest letter index this draft has EVER issued
    (draft.highest_letter_index) rather than restarting at 1 — so a letter
    dropped via delete_letter() is never reissued just because the course
    is grown again through this same quick-create form or the '+ Add
    groups' control (see add_groups). Shrinking num_groups never deletes an
    existing letter here — dropping one is only ever done explicitly via
    delete_letter()."""
    needed = int(draft.num_groups or 0) - draft.highest_letter_index
    if needed <= 0:
        return
    new_letters = course_group_planner.letters_sequence_from(draft.highest_letter_index, needed)
    for letter in new_letters:
        CourseGroupDraftLetter.objects.get_or_create(draft=draft, letter=letter)
    draft.highest_letter_index += needed
    draft.save(update_fields=["highest_letter_index"])


# ───────────────────────── programs that can be added to the matrix ─────────────────────────

def mappable_programs_for(draft, exclude_program_ids=frozenset()):
    """
    Programs in this draft's department whose curriculum actually contains
    this course code — the ONLY programs a mapping can be added for, since
    the year/semester come straight from that program's ProgramCourse match.
    """
    qs = (
        ProgramCourse.objects
        .filter(program__department=draft.department, course_code__iexact=draft.course_code)
        .select_related("program")
        .order_by("program__name")
    )
    out = []
    seen_programs = set(exclude_program_ids)
    for pc in qs:
        if pc.program_id in seen_programs:
            continue
        seen_programs.add(pc.program_id)
        out.append({
            "program_id": pc.program_id,
            "program_name": pc.program.name,
            "program_course_id": pc.id,
            "year": pc.year,
            "semester": pc.semester,
        })
    return out


def stems_for_program(program):
    return list(
        SpecializationStem.objects
        .filter(category__program=program)
        .select_related("category")
        .order_by("category__name", "name")
    )


def _matching_program_course(draft, program):
    return (
        ProgramCourse.objects
        .filter(program=program, course_code__iexact=draft.course_code)
        .order_by("id")
        .first()
    )


# ───────────────────────── matrix rendering ─────────────────────────

def draft_matrix(draft):
    """
    Everything the Course Groups matrix needs to render for one draft:
      * letters             -> ["A", "B", ...]
      * committed_rows      -> mappings currently backed by real data, read
                                back from the actual SpecializationStem /
                                CourseAllocation rows (course_code-<LETTER>
                                convention) — the live truth, regardless of
                                whether a CourseGroupDraftMapping row exists
                                for them.
      * pending_rows        -> checkboxes still awaiting Commit, from
                                CourseGroupDraftMapping, grouped by
                                (program, stem). A mapping row is NOT
                                deleted once committed (see commit_draft),
                                so this list is computed by excluding
                                whatever committed_rows already confirms
                                live — meaning a mapping whose real data
                                gets cleared elsewhere (e.g. a "clear
                                allocation" action) reappears here on its
                                own, ready to be committed again.
      * addable_programs    -> programs not already shown in either list,
                                for the "add a program" control
    """
    letter_objs = list(draft.letters.all().order_by("letter").select_related("lecturer"))
    letters = [l.letter for l in letter_objs]
    letter_lecturers = [
        {"letter": l.letter, "lecturer_id": l.lecturer_id, "lecturer_name": l.lecturer.display_name if l.lecturer_id else ""}
        for l in letter_objs
    ]

    base = draft.course_code.strip().upper()
    prefix = base + "-"
    shown_program_ids = set()

    # ── live/committed data first — this is the source of truth ─────────
    stems = (
        SpecializationStem.objects
        .filter(category__department=draft.department, courses__course_code__istartswith=prefix)
        .distinct()
        .select_related("category", "category__program")
        .prefetch_related("courses")
    )
    committed_rows = []
    # (program_id, letter) pairs already accounted for under a real stem —
    # a program can commit SOME letters to stems and OTHERS as deliberately
    # plain (see commit_draft's plain_letters), so this is tracked per
    # letter, not per program: gating on the whole program would hide (or
    # silently merge away) a program's own plain letters once it has any
    # stem-committed letter at all.
    stem_claimed_letters = set()
    # Exact (program_id, stem_id, letter) triples already live — used below
    # to keep an already-applied CourseGroupDraftMapping out of pending_rows.
    committed_keys = set()
    for stem in stems:
        program = stem.category.program if stem.category else None
        if not program:
            continue
        letters_here = set()
        for ca in stem.courses.all():
            code = (ca.course_code or "").upper()
            if code.startswith(prefix):
                letters_here.add(code[len(prefix):])
        if not letters_here:
            continue
        committed_rows.append({
            "program_id": program.id, "program_name": program.name,
            "stem_id": stem.id, "stem_name": stem.name,
            "letters": sorted(letters_here),
        })
        shown_program_ids.add(program.id)
        for letter in letters_here:
            stem_claimed_letters.add((program.id, letter))
            committed_keys.add((program.id, stem.id, letter))

    # Plain (no-stem) letters already split for a program — whether that
    # program has no combination stems at all, or it has some letters on
    # stems and this specific letter was deliberately committed with no
    # stem — also count as "committed" for display purposes. Merged into
    # one row per program (not one row per letter) so multiple plain
    # letters on the same program show as a single chip listing all of
    # them, matching how stem rows and pending rows are grouped.
    plain_codes = (
        CourseAllocation.objects
        .filter(department=draft.department, course_code__istartswith=prefix)
        .select_related("program")
        .values_list("program_id", "program__name", "course_code")
        .distinct()
    )
    plain_rows_by_program = {}
    for program_id, program_name, code in plain_codes:
        letter = (code or "").upper()[len(prefix):]
        if not letter or (program_id, letter) in stem_claimed_letters:
            continue
        row = plain_rows_by_program.setdefault(program_id, {
            "program_id": program_id, "program_name": program_name,
            "stem_id": None, "stem_name": "(no stem)", "letters": set(),
        })
        row["letters"].add(letter)
        shown_program_ids.add(program_id)
        committed_keys.add((program_id, None, letter))
    for row in plain_rows_by_program.values():
        row["letters"] = sorted(row["letters"])
        committed_rows.append(row)

    # ── pending — draft mappings not (yet, or no longer) reflected live ──
    pending_qs = (
        CourseGroupDraftMapping.objects
        .filter(draft=draft)
        .select_related("program", "stem", "stem__category", "letter")
    )
    pending_by_key = {}
    for m in pending_qs:
        if (m.program_id, m.stem_id, m.letter.letter) in committed_keys:
            continue  # already reflected live — shown via committed_rows instead
        key = (m.program_id, m.stem_id)
        row = pending_by_key.setdefault(key, {
            "program_id": m.program_id, "program_name": m.program.name,
            "stem_id": m.stem_id, "stem_name": m.stem.name if m.stem_id else "(no stem)",
            "year": m.year, "semester": m.semester,
            "letters": set(),
        })
        row["letters"].add(m.letter.letter)
        shown_program_ids.add(m.program_id)

    pending_rows = []
    for row in pending_by_key.values():
        row["letters"] = sorted(row["letters"])
        pending_rows.append(row)

    return {
        "letters": letters,
        "letter_lecturers": letter_lecturers,
        "pending_rows": pending_rows,
        "committed_rows": committed_rows,
        "addable_programs": mappable_programs_for(draft, exclude_program_ids=shown_program_ids),
    }


# ───────────────────────── toggling a checkbox ─────────────────────────

def toggle_mapping(*, draft, program, stem, letter_str, on, user):
    """Create/delete one pending CourseGroupDraftMapping. Returns (ok, message)."""
    letter_obj, _ = CourseGroupDraftLetter.objects.get_or_create(draft=draft, letter=letter_str.strip().upper())
    if on:
        pc = _matching_program_course(draft, program)
        if not pc:
            return False, f"{program.name} has no '{draft.course_code}' in its curriculum."
        CourseGroupDraftMapping.objects.update_or_create(
            letter=letter_obj, program=program, stem=stem,
            defaults={"draft": draft, "year": pc.year, "semester": pc.semester, "created_by": user},
        )
    else:
        CourseGroupDraftMapping.objects.filter(letter=letter_obj, program=program, stem=stem).delete()
    return True, None


# ───────────────────────── copy a whole course group's mappings to other drafts ─────────────────────────
#
# Programs very often take several courses together under the exact same
# combination-stem split, and that split is consistent across EVERY letter
# of the course, not just one — e.g. a B.Ed Arts student in the MATH/PHY
# stem sits in EDCI 203-A, -B, -C... *and* the same MATH/PHY split applies
# on EPSC 111-A, -B, -C... for the same reason. Rather than re-ticking every
# (program, stem) checkbox on every letter for every course code, "Copy to"
# lets a COD pin one course group's ENTIRE set of letter mappings once (on
# EDCI 203, say) and fan the whole thing out to any number of other
# course-group drafts, letter-for-letter (A -> A, B -> B, ...).

def copyable_target_drafts(draft):
    """Every other CourseGroupDraft in this draft's department — the pick
    list for "Copy to" (self excluded, since copying a course group onto
    itself is a no-op)."""
    return list(
        CourseGroupDraft.objects
        .filter(department=draft.department)
        .exclude(pk=draft.pk)
        .order_by("course_code")
    )


def copy_draft_mappings(*, source_draft, target_drafts, user):
    """
    Copy EVERY (program, stem, letter) currently mapped anywhere on
    `source_draft` — pending AND already-committed, i.e. exactly what the
    "Stems mapped" chips show across all of its letters — onto each draft in
    `target_drafts`, letter-for-letter (source letter 'A' lands on target
    letter 'A', etc.). Any letter the source has that the target doesn't yet
    have is created on the target as needed (growing its num_groups /
    highest_letter_index to match).

    A (program, stem) pair is only copied onto a target draft whose program
    actually teaches that target's course code (no ProgramCourse match means
    there's no year/semester to attach the mapping to, so it's skipped and
    reported rather than silently dropped). Copying onto an ALREADY-COMMITTED
    (program, stem, letter) on the target is also skipped (that combination
    already has real allocations — un-pin it first if it needs to change),
    same guard as the per-cell checkbox editor.

    Returns {"copied": {draft_id: count}, "skipped": [messages]}.
    """
    mx = draft_matrix(source_draft)
    source_rows = mx["pending_rows"] + mx["committed_rows"]

    copied = {}
    skipped = []
    if not source_rows:
        return {"copied": copied, "skipped": skipped}

    program_ids = {r["program_id"] for r in source_rows}
    programs_by_id = {p.id: p for p in Program.objects.filter(id__in=program_ids)}

    for target in target_drafts:
        if target.pk == source_draft.pk:
            continue

        # Every letter the source uses, in order, gets created on the
        # target up front (one num_groups/highest_letter_index bump for
        # the whole course group, not one per row).
        source_letters = sorted({letter for row in source_rows for letter in row["letters"]})
        letter_objs = {}
        new_letter_count = 0
        for letter_str in source_letters:
            letter_obj, _created = CourseGroupDraftLetter.objects.get_or_create(draft=target, letter=letter_str)
            letter_objs[letter_str] = letter_obj
            if _created:
                new_letter_count += 1
        if new_letter_count:
            target.num_groups = int(target.num_groups or 0) + new_letter_count
            target.highest_letter_index = max(target.highest_letter_index, target.num_groups)
            target.save(update_fields=["num_groups", "highest_letter_index", "updated_at"])

        already_committed = {
            (r["program_id"], r["stem_id"], letter)
            for r in draft_matrix(target)["committed_rows"]
            for letter in r["letters"]
        }

        pc_cache = {}
        count = 0
        for row in source_rows:
            program = programs_by_id.get(row["program_id"])
            if not program:
                continue
            if program.id not in pc_cache:
                pc_cache[program.id] = _matching_program_course(target, program)
            pc = pc_cache[program.id]
            if not pc:
                skipped.append(f"{program.name} has no '{target.course_code}' in its curriculum, skipped.")
                continue
            for letter_str in row["letters"]:
                if (row["program_id"], row["stem_id"], letter_str) in already_committed:
                    skipped.append(
                        f"{program.name} — {row['stem_name']} is already committed on "
                        f"{target.course_code}-{letter_str}, left untouched."
                    )
                    continue
                CourseGroupDraftMapping.objects.update_or_create(
                    letter=letter_objs[letter_str], program=program, stem_id=row["stem_id"],
                    defaults={"draft": target, "year": pc.year, "semester": pc.semester, "created_by": user},
                )
                count += 1
        copied[target.id] = count

    return {"copied": copied, "skipped": skipped}


# ───────────────────────── lecturer assignment (per letter) ─────────────────────────

def _propagate_letter_lecturer(draft, letter_str, lecturer):
    """One letter (e.g. 'A') is one physical class no matter how many
    programs/stems it's mapped into (see the cross-program CombinedCourseGroup
    logic in course_group_planner.save_group_plan), so a lecturer assigned to
    a letter here is pushed onto EVERY already-committed CourseAllocation row
    for 'code-LETTER' in this department, across every program/stem that
    currently holds it."""
    prefix = draft.course_code.strip().upper() + "-"
    CourseAllocation.objects.filter(
        department=draft.department,
        course_code__iexact=prefix + letter_str.strip().upper(),
    ).update(lecturer=lecturer)


def set_letter_lecturer(*, draft, letter_str, lecturer, user):
    """Assign/clear the lecturer for one lettered group of this draft
    ('EDFO 111-A', say). Works whether the letter is still fully pending or
    already committed to real CourseAllocation rows — either way it stays
    in sync going forward: pending mappings pick it up at Commit time
    (see commit_draft), and any already-committed rows are updated right
    now via _propagate_letter_lecturer."""
    letter_str = letter_str.strip().upper()
    letter_obj, _ = CourseGroupDraftLetter.objects.get_or_create(draft=draft, letter=letter_str)
    letter_obj.lecturer = lecturer
    letter_obj.save(update_fields=["lecturer"])
    _propagate_letter_lecturer(draft, letter_str, lecturer)
    return letter_obj


# ───────────────────────── drop / grow the group count ─────────────────────────

def delete_letter(*, draft, letter_str):
    """
    Drop one lettered group (e.g. 'C') from this draft's view. Deletes its
    CourseGroupDraftLetter row, which cascades any still-pending
    CourseGroupDraftMapping rows for it — nothing left checked for a letter
    that no longer exists. Already-committed CourseAllocation rows for
    'CODE-LETTER' are never touched here (this only edits the draft's own
    pending state); if any exist, the caller is told so rather than this
    silently pretending the real data went away too.

    draft.highest_letter_index is left untouched, so this letter is never
    reissued by add_groups()/_ensure_letters() later — only num_groups
    (the current active count) goes down.

    Returns (ok, error_message_or_None, had_committed_allocations).
    """
    letter_str = letter_str.strip().upper()
    letter_obj = draft.letters.filter(letter=letter_str).first()
    if not letter_obj:
        return False, f"Group '{letter_str}' not found on this course.", False

    prefix = draft.course_code.strip().upper() + "-"
    had_committed = CourseAllocation.objects.filter(
        department=draft.department, course_code__iexact=prefix + letter_str,
    ).exists()

    letter_obj.delete()
    if draft.num_groups > 0:
        draft.num_groups -= 1
        draft.save(update_fields=["num_groups", "updated_at"])
    return True, None, had_committed


def add_groups(*, draft, add_count):
    """
    Append `add_count` fresh lettered groups after the highest letter this
    draft has EVER issued (draft.highest_letter_index) — e.g. going from 16
    groups (A..P) to 20 appends Q, R, S, T, never reusing a letter dropped
    earlier via delete_letter(). Returns the list of newly created letters.
    """
    add_count = max(int(add_count or 0), 0)
    if add_count < 1:
        return []
    old_highest = draft.highest_letter_index
    draft.num_groups = int(draft.num_groups or 0) + add_count
    draft.save(update_fields=["num_groups", "updated_at"])
    _ensure_letters(draft)
    return course_group_planner.letters_sequence_from(old_highest, add_count)


# ───────────────────────── commit ─────────────────────────

@transaction.atomic
def commit_draft(*, draft, user, request, department):
    """
    Applies every outstanding checkbox for this draft: one save_group_plan()
    call per program, carrying that program's own pending stem -> letters
    map. "Outstanding" comes from draft_matrix()'s pending_rows — i.e. any
    CourseGroupDraftMapping not currently backed by real data — rather than
    every mapping row that has ever existed, so a normal Commit only redoes
    what actually needs redoing.

    Unlike the old behaviour, a successfully-applied mapping row is kept,
    not deleted: it is the durable record of the intended stem split, and
    draft_matrix() already knows to stop showing it as pending once the
    real data confirms it. That means if the underlying CourseAllocation
    rows are later wiped by something else (a "clear allocation" action,
    say), the same mapping reappears as pending on its own and Commit can
    simply be run again to put it straight back — no re-ticking needed.

    A program whose call raises is skipped and reported, without losing
    the other programs' progress.
    """
    pending_rows = draft_matrix(draft)["pending_rows"]
    if not pending_rows:
        return {"applied": 0, "errors": [], "message": "Nothing pending to commit."}

    by_program = {}
    for row in pending_rows:
        bucket = by_program.setdefault(row["program_id"], {
            "year": row["year"], "semester": row["semester"], "rows": [],
        })
        bucket["rows"].append(row)

    programs_by_id = {p.id: p for p in Program.objects.filter(id__in=by_program.keys())}

    applied, errors = 0, []
    for program_id, bucket in by_program.items():
        program = programs_by_id.get(program_id)
        if not program:
            errors.append(f"Program {program_id}: no longer exists, skipped.")
            continue
        year, semester = bucket["year"], bucket["semester"]
        if not year or not semester:
            errors.append(f"{program.name}: missing year/semester, skipped.")
            continue
        pc = _matching_program_course(draft, program)
        if not pc:
            errors.append(f"{program.name}: '{draft.course_code}' no longer in its curriculum, skipped.")
            continue

        stem_letter_map = {}
        plain_letters = []
        program_letters = set()
        row_letter_count = 0
        for row in bucket["rows"]:
            for letter in row["letters"]:
                program_letters.add(letter)
                row_letter_count += 1
                if row["stem_id"]:
                    stem_letter_map.setdefault(row["stem_id"], []).append(letter)
                else:
                    plain_letters.append(letter)

        dept = department or program.department
        alloc_set = get_active_allocation_set(request, dept) or get_or_default_legacy_set(dept)
        try:
            course_group_planner.save_group_plan(
                program=program, year=year, semester=semester, intake="normal",
                department=dept, user=user,
                num_groups=draft.num_groups,
                scope=GroupingTemplate.SCOPE_SELECTED,
                selected_program_course_ids=[pc.id],
                stem_letter_map=stem_letter_map,
                allocation_set=alloc_set,
                # These letters are already fixed/shared on the draft — never
                # let save_group_plan's own continuation numbering invent a
                # different range and silently drop the pin (see
                # course_group_planner.save_group_plan docstring).
                explicit_letters=sorted(
                    program_letters, key=course_group_planner.letter_to_index,
                ),
                plain_letters=sorted(set(plain_letters)),
            )
        except Exception as exc:  # noqa: BLE001 — reported to the COD, other programs still proceed
            errors.append(f"{program.name}: {exc}")
            continue

        # Deliberately NOT deleting the CourseGroupDraftMapping rows here —
        # see the module docstring and commit_draft's own docstring above.
        applied += row_letter_count

    # Any letter that already has a lecturer pinned (assigned before or
    # during this commit) gets that lecturer pushed onto the CourseAllocation
    # rows save_group_plan() just created/renamed for it.
    if applied:
        for letter_obj in draft.letters.filter(lecturer__isnull=False):
            _propagate_letter_lecturer(draft, letter_obj.letter, letter_obj.lecturer)

    return {"applied": applied, "errors": errors}
