"""
Course-code helpers for SECTIONS of a course (a course split further because
it has too many students for one class).

Background
----------
Until now a course could be split into *lettered groups* ("COSC 103-A",
"COSC 103-B", ...). The letter did double duty: it named the Student Group
that takes the course (see cod_panel._assign_course_to_group) AND it was what
"Add Group(s)" produced, so a course that a Student Group needed to split into
several parallel classes had no free, unambiguous way to say so — the next
letter would collide with the next Student Group's letter.

A SECTION is a numbered sub-class of ONE course inside ONE cohort
(a Student Group, or a stem / shared course):

    COSC 103-A     Group A, section 1      (the original row, unchanged)
    COSC 103-A2    Group A, section 2
    COSC 103-A3    Group A, section 3
    COSC 103       shared / stem course, section 1 (unchanged)
    COSC 103-2     shared / stem course, section 2

Every section carries the same base course code, so every scheduler/panel that
already collapses trailing "-X" tags (course_base_key, normalize_course_code_base,
normalize_course_code ...) sees them as the same course. The sections partition
the cohort's students, so they may run at the SAME time (different rooms)
without being a collision.

This module is deliberately pure (no Django imports) so it can be unit-tested
and imported from anywhere without side effects.
"""
import re
from typing import Optional, Tuple

# COSC 471(C) / COSC471(AC)
_PAREN_RE = re.compile(r"^(.*?\d+)\s*\(\s*([A-Za-z]+)\s*\)\s*$", re.I)
# COSC 103-A2 / COSC 103A2 / COSC 103 A2 / COSC 103/AB3  (letter tag + number)
_LETTER_SECTION_RE = re.compile(r"^(.*?\d+)\s*[-/_]?\s*([A-Za-z]+)(\d{1,2})\s*$", re.I)
# COSC 103-2 / COSC 103_2  (number only — the separator is REQUIRED, otherwise
# "COSC 103" would be split into "COSC 1" + "03")
_NUM_SECTION_RE = re.compile(r"^(.*?\d+)\s*[-/_]\s*(\d{1,2})\s*$")
# COSC 471-A / COSC 471/A / COSC 471_A / COSC 471 A / COSC471A / COSC 471-AA
_LETTER_RE = re.compile(r"^(.*?\d+)\s*[-/_]?\s*([A-Za-z]+)\s*$", re.I)

# The regular scheduler recognises a trailing section tag of at most three
# characters after the separator ("-A2", "-DA2", "-12"). Keep every generated
# code inside that window so all normalisers agree.
_MAX_TAG_LEN = 3


def parse_course_code(code: str) -> Tuple[str, Optional[str], Optional[int]]:
    """
    Split a stored course code into (base, group_letter, section_number).

        "COSC 103"     -> ("COSC 103", None, None)
        "COSC 103-A"   -> ("COSC 103", "A",  None)
        "COSC 103-A2"  -> ("COSC 103", "A",  2)
        "COSC 103-2"   -> ("COSC 103", None, 2)
        "COSC 471(C)"  -> ("COSC 471", "C",  None)

    Fully backward compatible with the letters-only parsing that
    cod_panel.strip_group_suffix used before sections existed: any code that
    has no digits after its letter tag parses exactly as it did.
    """
    code = (code or "").strip()
    if not code:
        return code, None, None

    m = _PAREN_RE.match(code)
    if m and re.search(r"\d", m.group(1)):
        return m.group(1).strip(), m.group(2).upper(), None

    m = _LETTER_SECTION_RE.match(code)
    if m and re.search(r"\d", m.group(1)):
        return m.group(1).strip(), m.group(2).upper(), int(m.group(3))

    m = _NUM_SECTION_RE.match(code)
    if m and re.search(r"\d", m.group(1)):
        return m.group(1).strip(), None, int(m.group(2))

    m = _LETTER_RE.match(code)
    if m and re.search(r"\d", m.group(1)):
        return m.group(1).strip(), m.group(2).upper(), None

    return code, None, None


def strip_group_suffix(code: str) -> Tuple[str, Optional[str]]:
    """Backward-compatible (base, letter) view of parse_course_code()."""
    base, letter, _section = parse_course_code(code)
    return base, letter


def base_of(code: str) -> str:
    """The bare base course code, e.g. 'COSC 103' for every example above."""
    return parse_course_code(code)[0]


def section_of(code: str) -> Optional[int]:
    return parse_course_code(code)[2]


def max_sections_for_code(code: str) -> int:
    """
    How many numbered sections a code can carry while its trailing tag stays
    within the 3-character window every scheduler normaliser understands.
    A 1-letter group tag allows up to 99 sections ("-A12"), a 2-letter tag
    up to 9 ("-DA2"), no tag at all up to 99 ("-12").
    """
    _base, letter, _sec = parse_course_code(code)
    room = _MAX_TAG_LEN - len(letter or "")
    if room <= 0:
        return 1
    return 10 ** room - 1


def section_code(code: str, section_number: int) -> str:
    """
    The stored course code for section `section_number` of the course that
    `code` belongs to. Section 1 is the untouched original code; sections
    2+ append the number to the group letter (or after a dash when the
    course has no group letter).

        section_code("COSC 103-A", 2) -> "COSC 103-A2"
        section_code("COSC 103",   3) -> "COSC 103-3"
        section_code("COSC 103-A5", 1) -> "COSC 103-A"
    """
    base, letter, _sec = parse_course_code(code)
    if section_number is None or section_number <= 1:
        return f"{base}-{letter}" if letter else base
    if letter:
        return f"{base}-{letter}{section_number}"
    return f"{base}-{section_number}"


def with_group_letter(code: str, group_letter: str) -> str:
    """
    Re-letter `code` for a Student Group while PRESERVING any section number:

        with_group_letter("COSC 103",    "A") -> "COSC 103-A"
        with_group_letter("COSC 103-B",  "A") -> "COSC 103-A"
        with_group_letter("COSC 103-B2", "A") -> "COSC 103-A2"
        with_group_letter("COSC 103-2",  "A") -> "COSC 103-A2"
    """
    base, _letter, sec = parse_course_code(code)
    tag = (group_letter or "").upper()
    if sec and sec > 1:
        return f"{base}-{tag}{sec}"
    return f"{base}-{tag}"


def distribute_students(total: int, sections: int) -> list:
    """
    Split `total` students across `sections` classes as evenly as possible;
    the first (total % sections) sections get one extra student.
    """
    sections = max(int(sections), 1)
    total = max(int(total or 0), 0)
    base, extra = divmod(total, sections)
    return [base + (1 if i < extra else 0) for i in range(sections)]
