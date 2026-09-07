"""
Reusable autoscheduler-constraint engine.

This module is deliberately kept free of any scheduling-algorithm-specific
logic (slot indices, day lists, etc.) so it can be called from the regular
timetable autoscheduler, the exam autoscheduler, resit scheduling, or
anything else that needs to know:

    - which venues are hard-blocked
    - which specialization rules are "exclusive" (no other course may ever
      use that venue)
    - which lecturers are hard-blocked from a day/time
    - which lecturers have soft day/time or venue preferences

and whether each of those *categories* of constraint is switched on for
this run.

CONSTRAINT_DEFS is the single source of truth for what constraint
categories exist. Everything else (the DB registry rows, the confirmation
screen, the enable/disable checks) is derived from it.
"""

from typing import Dict, List, Optional, Set, Tuple


CONSTRAINT_DEFS = [
    {
        "key": "venue_blocks",
        "label": "Blocked Venues",
        "description": (
            "Venues marked as blocked are hidden from the autoscheduler "
            "entirely — no course will ever be placed there."
        ),
        "applies_to": "both",
    },
    {
        "key": "venue_specialization",
        "label": "Venue Specialization (priority pass)",
        "description": (
            "Designated courses/programs/departments are placed into their "
            "reserved venues first. Non-exclusive rules still allow other "
            "courses into leftover free timeslots afterwards."
        ),
        "applies_to": "both",
    },
    {
        "key": "venue_exclusive",
        "label": "Exclusive Venue Restrictions",
        "description": (
            "For specialization rules marked 'exclusive', the reserved "
            "venues are removed from the general pool entirely — no other "
            "course may use them even if free timeslots remain."
        ),
        "applies_to": "both",
    },
    {
        "key": "lecturer_blocked_slots",
        "label": "Lecturer Blocked Days/Times",
        "description": (
            "Hard rule: a lecturer will never be scheduled on a blocked day "
            "(or a blocked day+time range)."
        ),
        "applies_to": "both",
    },
    {
        "key": "lecturer_time_preference",
        "label": "Lecturer Day/Time Preferences",
        "description": (
            "Soft rule: the scheduler tries (best-effort, after the main "
            "pass) to move a lecturer's classes onto their preferred "
            "day/time when it can do so without creating new conflicts."
        ),
        "applies_to": "both",
    },
    {
        "key": "lecturer_venue_preference",
        "label": "Lecturer Venue Preferences",
        "description": (
            "Soft rule: the scheduler tries (best-effort, after the main "
            "pass) to move a lecturer's classes into a preferred venue when "
            "it can do so without creating new conflicts."
        ),
        "applies_to": "both",
    },
]

_DEF_BY_KEY = {d["key"]: d for d in CONSTRAINT_DEFS}


def ensure_constraint_toggles():
    """Create any missing SchedulerConstraintToggle rows from CONSTRAINT_DEFS."""
    from core.models import SchedulerConstraintToggle
    for d in CONSTRAINT_DEFS:
        SchedulerConstraintToggle.objects.get_or_create(
            key=d["key"],
            defaults={
                "label": d["label"],
                "description": d["description"],
                "applies_to": d["applies_to"],
            },
        )


def _persisted_enabled_keys(scheduler_type: str = "regular") -> Set[str]:
    """Keys that are enabled by their saved DB default for this scheduler type."""
    from core.models import SchedulerConstraintToggle
    ensure_constraint_toggles()
    qs = SchedulerConstraintToggle.objects.filter(is_enabled=True)
    qs = qs.filter(applies_to__in=[scheduler_type, "both"])
    return set(qs.values_list("key", flat=True))


def is_enabled(key: str, disabled_for_run: Optional[Set[str]] = None,
                scheduler_type: str = "regular") -> bool:
    """
    A constraint category is active for this run if:
      - it's not in the caller-supplied `disabled_for_run` set (the
        checkboxes unchecked on the pre-run confirmation screen), AND
      - its persisted SchedulerConstraintToggle.is_enabled default is True.
    """
    if disabled_for_run and key in disabled_for_run:
        return False
    return key in _persisted_enabled_keys(scheduler_type)


def get_constraint_summary(scheduler_type: str = "regular") -> List[dict]:
    """
    Build the list shown on the pre-run confirmation screen: one entry per
    constraint category that applies to this scheduler type, with a live
    count of how many active rules exist so the admin knows what they're
    about to confirm (or disable).
    """
    ensure_constraint_toggles()
    from core.models import SchedulerConstraintToggle
    from timetable.models import (
        LecturerBlockedSlot, LecturerTimePreference, LecturerVenuePreference,
    )
    from room_management.models import VenueBlock, VenueSpecialization

    counts = {
        "venue_blocks": VenueBlock.objects.filter(is_active=True).count(),
        "venue_specialization": VenueSpecialization.objects.filter(is_active=True).count(),
        "venue_exclusive": VenueSpecialization.objects.filter(is_active=True, exclusive=True).count(),
        "lecturer_blocked_slots": LecturerBlockedSlot.objects.filter(is_active=True).count(),
        "lecturer_time_preference": LecturerTimePreference.objects.filter(is_active=True).count(),
        "lecturer_venue_preference": LecturerVenuePreference.objects.filter(is_active=True).count(),
    }

    toggles = {
        t.key: t
        for t in SchedulerConstraintToggle.objects.filter(applies_to__in=[scheduler_type, "both"])
    }

    summary = []
    for d in CONSTRAINT_DEFS:
        if d["applies_to"] not in (scheduler_type, "both"):
            continue
        toggle = toggles.get(d["key"])
        summary.append({
            "key": d["key"],
            "label": d["label"],
            "description": d["description"],
            "rule_count": counts.get(d["key"], 0),
            "is_enabled": bool(toggle.is_enabled) if toggle else True,
        })
    return summary


# ─────────────────────────────────────────────────────────────────────────
# Data accessors — return raw data, no algorithm-specific slot math here.
# ─────────────────────────────────────────────────────────────────────────

def get_blocked_venue_ids(disabled_for_run: Optional[Set[str]] = None,
                            scheduler_type: str = "regular") -> Set[int]:
    if not is_enabled("venue_blocks", disabled_for_run, scheduler_type):
        return set()
    from room_management.models import VenueBlock
    return set(
        VenueBlock.objects.filter(is_active=True).values_list("venue_id", flat=True)
    )


def get_exclusive_venue_ids(disabled_for_run: Optional[Set[str]] = None,
                              scheduler_type: str = "regular") -> Set[int]:
    """
    Venue IDs that belong to at least one active, exclusive specialization
    rule. Both the 'venue_specialization' and 'venue_exclusive' categories
    must be enabled for this to have any effect — exclusivity is a
    refinement of the specialization pass, not a standalone feature.
    """
    if not is_enabled("venue_specialization", disabled_for_run, scheduler_type):
        return set()
    if not is_enabled("venue_exclusive", disabled_for_run, scheduler_type):
        return set()
    from room_management.models import VenueSpecialization
    ids: Set[int] = set()
    for rule in VenueSpecialization.objects.filter(is_active=True, exclusive=True).prefetch_related("venues"):
        ids.update(rule.venues.values_list("id", flat=True))
    return ids


def get_designated_venue_rules(disabled_for_run: Optional[Set[str]] = None,
                                 scheduler_type: str = "regular") -> List[dict]:
    """
    Active VenueSpecialization rules, as plain dicts, so callers (e.g. the
    exam autoscheduler's priority pass) can steer designated courses into
    their reserved venue(s) FIRST, before any general placement — without
    importing the model directly.

    Returns [] entirely if the 'venue_specialization' category is disabled
    for this run. A rule's 'exclusive' flag is downgraded to False here if
    the separate 'venue_exclusive' category is disabled for this run, so
    the two toggles compose the same way they do for get_exclusive_venue_ids().

    Each dict: {"venue_ids": set[int], "codes": set[str] (normalised upper
    course codes), "exclusive": bool, "strict": bool, "priority": int,
    "program_ids": set[int], "course_ids": set[int]}

    "program_ids" and "course_ids" narrow *who* may actually use the
    designated venue(s): program_ids is every Program this rule scopes to
    (directly, or expanded from a department), and course_ids is every
    individual ProgramCourse this rule names explicitly. A course code can
    be shared by several programs (a "common"/cross-program course) — the
    code alone is not enough to know whether a *particular* allocation is
    the one this rule actually reserves the room for. Callers should treat
    an allocation as eligible for this rule's venues only if its program_id
    is in program_ids OR its program_course_id is in course_ids; a course
    whose code matches but whose program/program_course does not is a
    different section of the same code and must NOT use the reserved venue.
    """
    if not is_enabled("venue_specialization", disabled_for_run, scheduler_type):
        return []
    from room_management.models import VenueSpecialization
    exclusive_enabled = is_enabled("venue_exclusive", disabled_for_run, scheduler_type)
    rules = []
    for rule in VenueSpecialization.objects.filter(is_active=True).prefetch_related(
        "venues", "programs__courses", "departments__programs__courses", "courses"
    ):
        program_ids: Set[int] = set(rule.programs.values_list("id", flat=True))
        for dept in rule.departments.all():
            program_ids.update(dept.programs.values_list("id", flat=True))
        course_ids: Set[int] = set(rule.courses.values_list("id", flat=True))

        rules.append({
            "venue_ids": set(rule.venues.values_list("id", flat=True)),
            "codes": rule.get_designated_course_codes(),
            "exclusive": bool(rule.exclusive) and exclusive_enabled,
            "strict": bool(rule.strict),
            "priority": rule.priority,
            "program_ids": program_ids,
            "course_ids": course_ids,
        })
    return rules


def get_lecturer_blocked_ranges(
    disabled_for_run: Optional[Set[str]] = None,
    scheduler_type: str = "regular",
) -> Dict[int, List[Tuple[str, Optional["object"], Optional["object"]]]]:
    """
    Returns {lecturer_id: [(day, start_time_or_None, end_time_or_None), ...]}
    A (day, None, None) entry means the ENTIRE day is blocked for that
    lecturer. Times are datetime.time objects (or None).
    """
    if not is_enabled("lecturer_blocked_slots", disabled_for_run, scheduler_type):
        return {}
    from timetable.models import LecturerBlockedSlot
    out: Dict[int, List[Tuple[str, Optional[object], Optional[object]]]] = {}
    for row in LecturerBlockedSlot.objects.filter(is_active=True).only(
        "lecturer_id", "day", "start_time", "end_time"
    ):
        out.setdefault(row.lecturer_id, []).append((row.day, row.start_time, row.end_time))
    return out


def get_lecturer_time_preferences(
    disabled_for_run: Optional[Set[str]] = None,
    scheduler_type: str = "regular",
) -> Dict[int, List[Tuple[str, Optional["object"], Optional["object"]]]]:
    """
    Returns {lecturer_id: [(day, start_time_or_None, end_time_or_None), ...]}
    A (day, None, None) entry means "any time on this day is fine".

    LecturerTimePreference is a container (lecturer + is_active only) — the
    actual day/time data lives on its related LecturerTimePreferenceSlot
    rows (a preference can hold several day+time slots at once).
    """
    if not is_enabled("lecturer_time_preference", disabled_for_run, scheduler_type):
        return {}
    from timetable.models import LecturerTimePreference
    out: Dict[int, List[Tuple[str, Optional[object], Optional[object]]]] = {}
    for pref in LecturerTimePreference.objects.filter(is_active=True).prefetch_related("slots"):
        for slot in pref.slots.all():
            if slot.is_whole_day:
                out.setdefault(pref.lecturer_id, []).append((slot.day, None, None))
            else:
                out.setdefault(pref.lecturer_id, []).append((slot.day, slot.start_time, slot.end_time))
    return out


def get_lecturer_venue_preferences(
    disabled_for_run: Optional[Set[str]] = None,
    scheduler_type: str = "regular",
) -> Dict[int, Set[int]]:
    """Returns {lecturer_id: {preferred_venue_id, ...}}"""
    if not is_enabled("lecturer_venue_preference", disabled_for_run, scheduler_type):
        return {}
    from timetable.models import LecturerVenuePreference
    out: Dict[int, Set[int]] = {}
    for row in LecturerVenuePreference.objects.filter(is_active=True).prefetch_related("venues"):
        out.setdefault(row.lecturer_id, set()).update(row.venues.values_list("id", flat=True))
    return out
