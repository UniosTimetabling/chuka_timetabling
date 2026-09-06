"""
mobile_api/course_search.py
=============================
Course search for the mobile app's "add a course to my timetable" flow —
a self-service lookup that lets a student add a unit outside their own
program/year (a cross-cutting/elective unit) or a lecturer add a section
they teach that isn't captured by CourseAllocation.lecturer (co-teaching,
guest lecturing), onto their *personal* timetable additions — see
PersonalCourseEntry in models.py and mobile_api/personal_entries.py.

Accepts one or many raw course-code strings, comma-separated, exactly as a
person types them ("coms101, PHYS 342-a"), and is deliberately permissive
about spelling: the same course shows up written differently in different
places in this system — an individual CourseAllocation.course_code member
of a combined group is often suffixed ("COMS 101-D", "COMS 101_D",
"coms101d" all mean the same section) while the group itself is filed
under CombinedCourseGroup.base_course_code/group_code, which can differ
from any one member's own code.

Matching goes through program_management.code_utils.canonical_course_key —
the same "strip down to bare uppercase alphanumerics" function every other
course-code matching path in this codebase already uses (see that module's
own docstring for the history of why) — never a fresh regex here, so this
agrees with everything else on what counts as "the same code".

Each raw term normalizes to either:
  - an EXACT canonical match ("coms101d" -> only that D section), or
  - a PREFIX match, when the term's canonical key is a prefix of a
    candidate's ("coms101" / "COMS 101" -> every section: A, B, C, D... and
    the combined group itself, if one exists under that base code).
This is what lets a lecturer search "COMS 101" and get back all of, say,
10 sections to pick theirs from.

Combined groups collapse to ONE result — their primary allocation, exactly
like timetable/find_courses.py's rule — so a secondary member never
becomes its own independently-addable row. Adding "the group" and adding
"COMS 101-D" must be the same action once D is grouped, or the merge in
timetable_builder.py would double up the same class slot.
"""
import re

from django.db.models import Q

from program_management.code_utils import canonical_course_key
from course_allocation.models import CourseAllocation, CombinedCourseGroup
from timetable.timetable_panel import _get_year_value

MAX_TERMS = 10
MAX_RESULTS_PER_TERM = 25


def split_query(raw):
    """'coms101, PHYS 342-a ,,  ' -> ['coms101', 'PHYS 342-a']"""
    if not raw:
        return []
    terms = [t.strip() for t in raw.split(",")]
    return [t for t in terms if t][:MAX_TERMS]


def _leading_letters(key):
    """'COMS101D' -> 'COMS' — used only to cheaply narrow the DB query
    before the real canonical-key comparison happens in Python; falls back
    to the first few characters of the key for an all-digit/odd code."""
    m = re.match(r"^[A-Z]+", key)
    return m.group(0) if m else key[:4]


def _course_matches(candidates, key):
    """Exact canonical match beats a prefix match — someone who typed the
    section letter wants only that section, not every section that starts
    with the same digits."""
    exact = [c for c in candidates if canonical_course_key(c.course_code) == key]
    if exact:
        return exact
    return [c for c in candidates if canonical_course_key(c.course_code).startswith(key)]


def _matching_groups(key, letters):
    groups = CombinedCourseGroup.objects.filter(
        Q(base_course_code__icontains=letters) | Q(group_code__icontains=letters)
    )
    hits = []
    for g in groups:
        g_keys = [canonical_course_key(g.base_course_code), canonical_course_key(g.group_code)]
        if key in g_keys or any(k.startswith(key) for k in g_keys if k):
            hits.append(g)
    return hits


def search_courses(raw_query):
    """Returns (results, not_found). `results` is a flat list of match
    dicts, deduplicated across terms and collapsed one-row-per-combined-
    group (see module docstring). `not_found` lists any comma-separated
    terms that matched nothing, so the caller can tell the person which of
    several codes it couldn't find."""
    terms = split_query(raw_query)
    if not terms:
        return [], []

    seen_alloc_ids = set()
    seen_group_ids = set()
    results = []
    not_found = []

    for term in terms:
        key = canonical_course_key(term)
        if not key:
            continue
        letters = _leading_letters(key)

        candidates = list(
            CourseAllocation.objects.filter(course_code__icontains=letters)
            .select_related("lecturer", "department", "program", "program_course")
            .prefetch_related("combined_groups")
        )
        matches = _course_matches(candidates, key)

        # Combined-group codes can differ from every member's own
        # course_code (e.g. group_code "PHYS 342-A"), so also match those
        # directly and pull in that group's primary allocation.
        for g in _matching_groups(key, letters):
            if g.primary_allocation_id:
                matches.append(g.primary_allocation)
            else:
                matches.extend(list(g.allocations.all()[:1]))

        if not matches:
            not_found.append(term)
            continue

        term_hits = 0
        for alloc in matches:
            if term_hits >= MAX_RESULTS_PER_TERM:
                break

            combined_list = list(alloc.combined_groups.all())
            combined = combined_list[0] if combined_list else None
            if combined:
                if combined.id in seen_group_ids:
                    continue
                if combined.primary_allocation_id and combined.primary_allocation_id != alloc.id:
                    primary = combined.primary_allocation
                    if primary is None:
                        continue
                    alloc = primary
                seen_group_ids.add(combined.id)

            if alloc.id in seen_alloc_ids:
                continue
            seen_alloc_ids.add(alloc.id)
            term_hits += 1

            results.append({
                "id": alloc.id,
                "courseCode": combined.display_name() if combined else alloc.course_code,
                "courseName": alloc.course_name,
                "lecturer": getattr(alloc.lecturer, "name", None) or "Unassigned",
                "lecturerId": alloc.lecturer_id,
                "department": getattr(alloc.department, "name", None),
                "program": getattr(alloc.program, "name", None),
                "year": _get_year_value(alloc),
                "students": alloc.number_of_students or 0,
                "isCombinedGroup": combined is not None,
                "matchedTerm": term,
            })

    return results, not_found
