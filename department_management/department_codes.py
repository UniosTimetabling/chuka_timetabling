"""
department_management/department_codes.py
=============================================
Generates and resolves the short department "initials" stored on
DepartmentCode (see models.py for why these exist — long department names
were overflowing/cutting off columns in the Course Allocation Excel
template and PDF).

DEFAULT CONVENTION (matches what was asked for explicitly)
------------------------------------------------------------
    "Department of Humanities"                                          -> DHUM
    "Department of Plant Science"                                       -> DPLSC
    "Department of Animal Science"                                      -> DANSC
    "Agricultural Economics, Agribusiness Management &
     Agricultural Education"                                            -> DAEAM
    "Management Science"                                                -> DMASC

The rule that reproduces all of the above (see generate_default_code):
  * strip "Department"/"Department of"/"&"/punctuation, split into words
  * 1 significant word  -> "D" + first 3 letters of that word
  * 2 significant words -> "D" + first 2 letters of each word
  * 3+ significant words -> "D" + the first letter of each of the first
    4 significant words (initials)

This is only a DEFAULT — a code is only auto-generated the first time a
department is used without one (new department, or an existing one being
backfilled). Anyone can edit the code afterwards in the Department Codes
admin and it will never be silently regenerated or overwritten.
"""
import re

STOPWORDS = {"of", "and", "the", "for", "in", "department", "dept", "faculty", "school"}

# Explicit lookup for the department names called out by name — kept
# verbatim (rather than relying solely on the algorithm below) so these
# specific defaults never drift if the generation rule is ever tuned.
KNOWN_DEFAULTS = {
    "humanities": "DHUM",
    "plant science": "DPLSC",
    "animal science": "DANSC",
    "agricultural economics agribusiness management agricultural education": "DAEAM",
    "management science": "DMASC",
}


def _significant_words(name):
    name = (name or "").lower()
    name = name.replace("&", " ")
    name = re.sub(r"[.,;:/]", " ", name)
    return [w for w in name.split() if w not in STOPWORDS]


def _normalized_key(name):
    return " ".join(_significant_words(name))


def generate_default_code(name):
    """Pure function: derive a default 'D' + 3-4 letter code from a
    department name. Does not touch the database and never guarantees
    global uniqueness on its own — see unique_default_code for that."""
    key = _normalized_key(name)
    if key in KNOWN_DEFAULTS:
        return KNOWN_DEFAULTS[key]

    words = _significant_words(name)
    if not words:
        return "DEPT"

    if len(words) == 1:
        core = words[0][:3].upper()
    elif len(words) == 2:
        core = (words[0][:2] + words[1][:2]).upper()
    else:
        core = "".join(w[0] for w in words[:4]).upper()

    return f"D{core}"


def unique_default_code(name, exclude_department_id=None):
    """generate_default_code(), de-duplicated against codes already on
    record (appends 2, 3, ... on collision — e.g. a second, unrelated
    department that happens to reduce to the same initials)."""
    from department_management.models import DepartmentCode

    base = generate_default_code(name)
    qs = DepartmentCode.objects.all()
    if exclude_department_id:
        qs = qs.exclude(department_id=exclude_department_id)
    existing = set(qs.values_list("code", flat=True))

    code = base
    suffix = 2
    while code in existing:
        code = f"{base}{suffix}"
        suffix += 1
    return code


def get_or_create_department_code(department):
    """Return the DepartmentCode entry for `department`, auto-creating one
    from the default heuristic the first time it's needed. Never
    overwrites an existing (possibly hand-edited) code."""
    from department_management.models import DepartmentCode

    entry = department.department_codes.order_by("code").first()
    if entry is not None:
        return entry
    code = unique_default_code(department.name, exclude_department_id=department.id)
    return DepartmentCode.objects.create(department=department, code=code)


def department_initials(department):
    """The short code to DISPLAY for a department (Excel/PDF exports) —
    auto-provisioning one if this department somehow doesn't have one
    yet. Falls back to the full department name only if `department` is
    falsy or something prevents a code from being resolved."""
    if not department:
        return ""
    try:
        return get_or_create_department_code(department).code
    except Exception:
        return department.name


def name_for_code(code):
    """Reverse lookup used to build the "Department Codes" legend on
    exports — the full department name for a given code, or None if the
    code isn't on record (e.g. the text was already a full name because
    no code existed at export time)."""
    if not code:
        return None
    from department_management.models import DepartmentCode

    entry = DepartmentCode.objects.select_related("department").filter(code__iexact=code).first()
    return entry.department.name if entry else None
