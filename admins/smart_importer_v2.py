"""
admins/smart_importer_v2.py
============================
Smart Importer 2.0 — Intelligent University Data Import Framework

Architecture (12-stage pipeline):

  Stage 1  – Document Classification      (block non-university documents early)
  Stage 2  – Format-specific Extraction   (CSV/Excel: no AI; PDF/DOCX: text extract)
  Stage 3  – Schema Recognition           (which model does this document describe?)
  Stage 4  – Normalization Engine         (codes, names, dates → canonical forms)
  Stage 5  – Validation Engine            (exact + fuzzy match against DB master data)
  Stage 6  – Correction Engine            (rule-based + learned corrections, pre-AI)
  Stage 7  – AI Assistant Layer           (ambiguity resolution, column mapping, explanation)
  Stage 8  – User Review Interface        (preview with VALID / WARNING / ERROR per row)
  Stage 9  – Partial Import Processing    (96 valid → import now, fix remaining 4)
  Stage 10 – DB Commit via Staging        (staging table → production on approval)
  Stage 11 – Learning & Adaptation        (user corrections become future suggestions)
  Stage 12 – Audit & Traceability         (full transcript per run)

Design philosophy: AI assists; it never extracts primary structured data alone.
The university DB is the authoritative source of truth.

AJAX endpoints (mounted alongside v1 in admins/urls.py):
  POST /sudo-dashboard/smart-import-v2/classify/   → Stage 1: classify doc type
  POST /sudo-dashboard/smart-import-v2/extract/    → Stages 2-7: full ETL → staging
  POST /sudo-dashboard/smart-import-v2/refine/     → AI refinement pass
  POST /sudo-dashboard/smart-import-v2/commit/     → Stage 10: staging → production
  GET  /sudo-dashboard/smart-import-v2/            → main UI
  GET  /sudo-dashboard/smart-import-v2/csv/        → CSV template download
  GET  /sudo-dashboard/smart-import-v2/corrections/ → learned corrections log
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import unicodedata
from typing import Any, Literal

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.ai_registry import AIError, ai_is_configured, get_ai_client
from admins.import_logger import ImportRunLog

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Auth guard
# ─────────────────────────────────────────────────────────────────────────────

def _sudo(view_fn):
    return login_required(user_passes_test(lambda u: u.is_superuser)(view_fn))


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – Document Classification
# ─────────────────────────────────────────────────────────────────────────────

#: Document categories the importer recognises.  Anything that resolves to
#: BLOCKED is rejected before extraction begins.
BLOCKED_DOCUMENT_TYPES = {
    "patent_disclosure",
    "research_paper",
    "legal_document",
    "financial_report",
    "unknown",
}

#: Keyword signals used for heuristic (pre-AI) classification.
_CLASSIFICATION_SIGNALS: dict[str, list[str]] = {
    "course_allocation": [
        "course code", "course name", "course allocation", "unit code",
        "assigned lecturer", "teaching load", "credit hours", "semester allocation",
    ],
    "lecturer_list": [
        "payroll number", "payroll no", "staff number", "designation",
        "lecturer list", "staff list", "academic staff",
    ],
    "program_structure": [
        "program structure", "curriculum", "programme courses", "year of study",
        "credit units", "elective", "core unit",
    ],
    "venue_list": [
        "room capacity", "exam capacity", "venue code", "building", "lecture hall",
        "seating capacity", "lab venue",
    ],
    "enrollment_data": [
        "enrollment", "enrolment", "number of students", "student count",
        "intake size", "registered students",
    ],
    "patent_disclosure": [
        "patent", "invention disclosure", "intellectual property",
        "prior art", "claims", "inventor",
    ],
    "research_paper": [
        "abstract", "introduction", "methodology", "literature review",
        "references", "bibliography", "doi:", "journal of",
    ],
    "financial_report": [
        "profit", "loss", "balance sheet", "income statement",
        "cash flow", "revenue", "fiscal year", "audit report",
    ],
}

# Maps classification → list of MODEL_SCHEMA keys that are expected
_CLASSIFICATION_TO_MODELS: dict[str, list[str]] = {
    "course_allocation":  ["CourseAllocation", "ProgramCourse", "ProgramEnrollment"],
    "lecturer_list":      ["Lecturer", "LecturerCourseMapping"],
    "program_structure":  ["Program", "ProgramCourse", "ProgramCode"],
    "venue_list":         ["Venue", "Building", "LabVenue"],
    "enrollment_data":    ["ProgramEnrollment"],
    "faculty_department": ["Faculty", "Department"],
    "general_university": None,  # None → all models are candidates
}


def _classify_document(text_sample: str) -> dict:
    """
    Heuristic classification using keyword scoring.
    Returns {"doc_type": str, "confidence": "high"|"medium"|"low",
             "suggested_models": list[str], "blocked": bool, "reason": str}
    """
    # Normalize underscores to spaces before matching. Real data exports in
    # this system use snake_case headers (course_code, course_name, ...),
    # but the signal phrases below are written with spaces (e.g.
    # "course code"). Without this, legitimate headers never score any
    # points and an incidental word inside actual data (e.g. a course
    # literally titled "Introduction to Business") can win by default.
    sample_lower = text_sample.lower().replace("_", " ")
    scores: dict[str, int] = {}
    for doc_type, keywords in _CLASSIFICATION_SIGNALS.items():
        score = sum(1 for kw in keywords if kw in sample_lower)
        if score > 0:
            scores[doc_type] = score

    if not scores:
        return {
            "doc_type": "general_university",
            "confidence": "low",
            "suggested_models": None,
            "blocked": False,
            "reason": "No strong signals found — will attempt extraction against all models.",
        }

    best_type = max(scores, key=scores.__getitem__)
    best_score = scores[best_type]
    confidence = "high" if best_score >= 3 else "medium" if best_score >= 2 else "low"

    # Only block on a confident match. A single incidental keyword hit
    # (e.g. one course titled "Introduction to X" matching the
    # research_paper signal "introduction") is not enough evidence to
    # reject an otherwise legitimate university data sheet.
    blocked = best_type in BLOCKED_DOCUMENT_TYPES and confidence != "low"
    reason = ""
    if blocked:
        friendly = best_type.replace("_", " ").title()
        reason = (
            f"This document appears to be a '{friendly}', not a university data sheet. "
            "Import blocked to prevent unrelated data from entering the system."
        )

    suggested = _CLASSIFICATION_TO_MODELS.get(best_type)
    return {
        "doc_type": best_type,
        "confidence": confidence,
        "suggested_models": suggested,
        "blocked": blocked,
        "reason": reason,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 – Model Schema Registry
# ─────────────────────────────────────────────────────────────────────────────

MODEL_SCHEMA = {
    "Faculty": {
        "app": "faculty_management",
        "description": (
            "A top-level faculty, e.g. 'Faculty of Science' or 'Faculty of Arts'. "
            "Fields: name, description."
        ),
        "fields": ["name", "description"],
        "required": ["name"],
    },
    "Department": {
        "app": "department_management",
        "description": (
            "An academic department that belongs to a faculty, e.g. 'Computer Science' "
            "inside 'Faculty of Science'. Fields: name, faculty (name — required, every "
            "department must belong to a faculty), description."
        ),
        "fields": ["name", "faculty", "description"],
        "required": ["name", "faculty"],
    },
    "CourseAllocation": {
        "app": "course_allocation",
        "description": (
            "A course being taught this semester, linked to a department and optionally "
            "a lecturer and program. Fields: course_code, course_name, department (name), "
            "program (name, optional), lecturer (name or payroll, optional), "
            "number_of_students (int), intake (normal|special), is_elective (bool), "
            "is_evening_weekend (bool)."
        ),
        "fields": [
            "course_code", "course_name", "department", "program",
            "lecturer", "number_of_students", "intake",
            "is_elective", "is_evening_weekend",
        ],
        "required": ["course_code", "course_name", "department"],
    },
    "ProgramCourse": {
        "app": "program_management",
        "description": (
            "A course that belongs to a program curriculum. "
            "Fields: program (name), course_code, course_name, year (1-6), semester (1-2)."
        ),
        "fields": ["program", "course_code", "course_name", "year", "semester"],
        "required": ["program", "course_code", "course_name"],
    },
    "LecturerCourseMapping": {
        "app": "course_allocation",
        "description": (
            "Links a lecturer to courses they can teach. "
            "Fields: lecturer (name or payroll), department (name), "
            "course_codes (comma-separated list of course codes)."
        ),
        "fields": ["lecturer", "department", "course_codes", "notes"],
        "required": ["lecturer", "course_codes"],
    },
    "ProgramEnrollment": {
        "app": "course_allocation",
        "description": (
            "Student enrollment numbers per program per cohort. ONE row per "
            "program+entry_year -- applies to both semesters automatically. "
            "Fields: program (name), entry_year (calendar year admitted, "
            "e.g. 2023), number_of_students (int). Year of study is NOT "
            "stored; it is derived from the global AcademicYearTracker."
        ),
        "fields": ["program", "entry_year", "number_of_students"],
        "required": ["program", "entry_year", "number_of_students"],
    },
    "Lecturer": {
        "app": "lecturer_portal",
        "description": (
            "A lecturer record. Fields: payroll_number, name, email, "
            "designation (Prof|Dr|Mr|Ms|Mrs), department (name)."
        ),
        "fields": ["payroll_number", "name", "email", "designation", "department"],
        "required": ["payroll_number", "name", "email", "designation"],
    },
    "Program": {
        "app": "program_management",
        "description": (
            "An academic program (degree). "
            "Fields: name, department (name), description."
        ),
        "fields": ["name", "department", "description"],
        "required": ["name", "department"],
    },
    "Venue": {
        "app": "room_management",
        "description": (
            "A specific room/hall/lecture theatre. "
            "Fields: code, capacity (int), exam_capacity (int), building, "
            "is_workshop (bool), description."
        ),
        "fields": ["code", "capacity", "exam_capacity", "building", "is_workshop", "description"],
        "required": ["code"],
    },
    "Building": {
        "app": "room_management",
        "description": (
            "A building/complex that contains venues. "
            "Fields: name, code (short prefix), is_workshop (bool), description."
        ),
        "fields": ["name", "code", "is_workshop", "description"],
        "required": ["name", "code"],
    },
    "LabVenue": {
        "app": "room_management",
        "description": (
            "A laboratory room. "
            "Fields: code, capacity (int), description, equipment."
        ),
        "fields": ["code", "capacity", "description", "equipment"],
        "required": ["code"],
    },
    "ProgramCode": {
        "app": "program_management",
        "description": (
            "A short registrar code for an academic program, e.g. 'BSC-CS'. "
            "Fields: program (full name), code (short code)."
        ),
        "fields": ["program", "code"],
        "required": ["program", "code"],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 – Normalization Engine
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_course_code(raw: str) -> str:
    """'BBIT 101', 'Bbit101', 'bbit-101' → 'BBIT101'"""
    if not raw:
        return ""
    # Strip accents, upper-case, remove spaces/dashes/underscores between letters+digits
    normalized = unicodedata.normalize("NFD", raw)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = normalized.upper().strip()
    # collapse internal whitespace and common separators
    normalized = re.sub(r"[\s\-_]+", "", normalized)
    return normalized


def _normalize_name(raw: str) -> str:
    """'Dept. Computer Science', 'comp sci' → title-case, stripped."""
    if not raw:
        return ""
    # Strip leading/trailing noise, normalise internal whitespace
    cleaned = re.sub(r"\s+", " ", raw.strip())
    # Expand common abbreviations
    _ABBREV = {
        r"\bDept\.?\b": "Department",
        r"\bFac\.?\b": "Faculty",
        r"\bSci\.?\b": "Science",
        r"\bMgt\.?\b": "Management",
        r"\bICT\b": "ICT",
    }
    for pattern, replacement in _ABBREV.items():
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _normalize_designation(raw: str) -> str:
    """'dr', 'DR.', 'doctor' → 'Dr'"""
    if not raw:
        return "Mr"
    mapping = {
        "prof": "Prof", "professor": "Prof",
        "dr": "Dr", "dr.": "Dr", "doctor": "Dr",
        "mr": "Mr", "mr.": "Mr",
        "ms": "Ms", "ms.": "Ms", "miss": "Ms",
        "mrs": "Mrs", "mrs.": "Mrs",
    }
    return mapping.get(raw.strip().lower(), raw.strip().title())


def _normalize_bool(raw) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "yes", "1", "y")
    return False


def _normalize_int(raw, default: int = 0) -> int:
    try:
        return int(str(raw).strip()) if raw not in (None, "") else default
    except (ValueError, TypeError):
        return default


def _normalize_record_fields(model_name: str, data: dict) -> dict:
    """Apply model-specific normalisation to all fields in a record."""
    out = dict(data)

    if model_name in ("CourseAllocation", "ProgramCourse", "LecturerCourseMapping"):
        if "course_code" in out:
            out["course_code"] = _normalize_course_code(out.get("course_code", ""))
        if "course_codes" in out:
            codes = [_normalize_course_code(c) for c in str(out.get("course_codes", "")).split(",")]
            out["course_codes"] = ", ".join(c for c in codes if c)

    if model_name in ("CourseAllocation", "ProgramCourse"):
        if "year" in out:
            out["year"] = str(_normalize_int(out.get("year"), 1))
        if "semester" in out:
            out["semester"] = str(_normalize_int(out.get("semester"), 1))

    if model_name == "ProgramEnrollment":
        # No semester field any more -- one row covers both. entry_year is
        # a calendar year, not a small 1-6 study-year, so it isn't clamped
        # the same way _normalize_int would clamp "year".
        if "entry_year" in out:
            out["entry_year"] = str(_normalize_int(out.get("entry_year"), 1))
        elif "year" in out:
            out["entry_year"] = str(_normalize_int(out.get("year"), 1))
            out.pop("year", None)

    if model_name == "CourseAllocation":
        out["is_elective"] = str(_normalize_bool(out.get("is_elective", False)))
        out["is_evening_weekend"] = str(_normalize_bool(out.get("is_evening_weekend", False)))
        if "number_of_students" in out:
            out["number_of_students"] = str(_normalize_int(out.get("number_of_students"), 0))

    if model_name == "Lecturer":
        out["designation"] = _normalize_designation(out.get("designation", ""))
        out["name"] = _normalize_name(out.get("name", ""))

    for name_field in ("department", "faculty", "program"):
        if name_field in out and out[name_field]:
            out[name_field] = _normalize_name(out[name_field])

    if model_name in ("Building", "ProgramCode"):
        if "code" in out:
            out["code"] = out["code"].strip().upper()

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Stage 5 – Validation Engine
# ─────────────────────────────────────────────────────────────────────────────

#: Validation status values mirroring the document spec
ValidStatus = Literal["VALID", "WARNING", "ERROR"]


def _db_master_data() -> dict:
    """
    Load master data from the university DB for validation.
    Returns a dict of sets keyed by entity type.
    Empty gracefully if models are unavailable (e.g. during tests).
    """
    master: dict[str, set] = {
        "faculties": set(),
        "departments": set(),
        "programs": set(),
        "program_codes": set(),
        "lecturers_name": set(),
        "lecturers_payroll": set(),
        "course_codes": set(),
        "buildings": set(),
        "venue_codes": set(),
        "lab_codes": set(),
    }
    try:
        from faculty_management.models import Faculty
        master["faculties"] = set(Faculty.objects.values_list("name", flat=True))
    except Exception:
        pass
    try:
        from department_management.models import Department
        master["departments"] = set(Department.objects.values_list("name", flat=True))
    except Exception:
        pass
    try:
        from program_management.models import Program
        master["programs"] = set(Program.objects.values_list("name", flat=True))
    except Exception:
        pass
    try:
        from program_management.models import ProgramCode
        master["program_codes"] = set(ProgramCode.objects.values_list("code", flat=True))
    except Exception:
        pass
    try:
        from lecturer_portal.models import Lecturer
        master["lecturers_name"] = set(Lecturer.objects.values_list("name", flat=True))
        master["lecturers_payroll"] = set(Lecturer.objects.values_list("payroll_number", flat=True))
    except Exception:
        pass
    try:
        from course_allocation.models import CourseAllocation
        master["course_codes"] = set(
            CourseAllocation.objects.values_list("course_code", flat=True).distinct()
        )
    except Exception:
        pass
    try:
        from room_management.models import Building, Venue, LabVenue
        master["buildings"] = set(Building.objects.values_list("code", flat=True))
        master["venue_codes"] = set(Venue.objects.values_list("code", flat=True))
        master["lab_codes"] = set(LabVenue.objects.values_list("code", flat=True))
    except Exception:
        pass
    return master


def _fuzzy_score(a: str, b: str) -> int:
    """Simple character-level similarity score 0–100 (no external deps)."""
    a, b = a.lower().strip(), b.lower().strip()
    if not a or not b:
        return 0
    if a == b:
        return 100
    # Longest-common-subsequence ratio
    la, lb = len(a), len(b)
    dp = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs = dp[la][lb]
    return int(lcs * 200 / (la + lb))


def _best_fuzzy_match(value: str, candidates: set, threshold: int = 70) -> tuple[str | None, int]:
    """Return (best_match, score) or (None, 0) if no candidate scores ≥ threshold."""
    if not value or not candidates:
        return None, 0
    best, best_score = None, 0
    for cand in candidates:
        s = _fuzzy_score(value, cand)
        if s > best_score:
            best, best_score = cand, s
    if best_score >= threshold:
        return best, best_score
    return None, best_score


# Field → which master-data set to validate against
_VALIDATION_MAP: dict[str, str] = {
    "department": "departments",
    "faculty": "faculties",
    "program": "programs",
    "course_code": "course_codes",
    "lecturer": "lecturers_name",
    "building": "buildings",
}


def _validate_record(model_name: str, data: dict, master: dict) -> dict:
    """
    Validate a single normalised record against master data.
    Returns enriched record with:
      _status: "VALID" | "WARNING" | "ERROR"
      _issues: list of human-readable issue strings
      _suggestions: dict of field → suggested value
    """
    schema = MODEL_SCHEMA.get(model_name, {})
    required_fields = schema.get("required", [])
    issues = []
    suggestions: dict[str, str] = {}

    # ── Required-field check ──────────────────────────────────────────────
    missing_required = [f for f in required_fields if not str(data.get(f, "")).strip()]
    for f in missing_required:
        issues.append(f"Missing required field: '{f}'")

    # ── FK / master-data validation ───────────────────────────────────────
    for field, master_key in _VALIDATION_MAP.items():
        value = str(data.get(field, "")).strip()
        if not value:
            continue  # already caught by required check if needed
        candidate_set = master.get(master_key, set())
        if not candidate_set:
            continue  # DB empty / unavailable — skip silently

        # Exact match (case-insensitive)
        matches_exact = any(v.lower() == value.lower() for v in candidate_set)
        if matches_exact:
            continue  # ✅

        # Fuzzy match
        best_match, score = _best_fuzzy_match(value, candidate_set)
        if best_match and score >= 85:
            issues.append(
                f"'{field}' value '{value}' not found exactly (best match: '{best_match}' "
                f"at {score}% similarity). Please verify."
            )
            suggestions[field] = best_match
        elif best_match and score >= 70:
            issues.append(
                f"'{field}' value '{value}' not found. Closest match: '{best_match}' "
                f"({score}%). May be a typo or new entry."
            )
            suggestions[field] = best_match
        else:
            issues.append(
                f"'{field}' value '{value}' not found in the university database "
                f"and no close match exists. This may be a new record or a typo."
            )

    # ── Derive status ─────────────────────────────────────────────────────
    has_error = bool(missing_required) or any(
        "not found in the university database" in i and "no close match" in i
        for i in issues
    )
    has_warning = issues and not has_error

    status: ValidStatus = "ERROR" if has_error else ("WARNING" if has_warning else "VALID")

    return {
        **data,
        "_status": status,
        "_issues": issues,
        "_suggestions": suggestions,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Stage 6 – Correction Engine
# ─────────────────────────────────────────────────────────────────────────────

#: Built-in correction rules:  {field_value_lower → corrected_value}
#: These are applied before AI and before human review.
_BUILTIN_CORRECTIONS: dict[str, dict[str, str]] = {
    # Department name corrections
    "department": {
        "comp sci": "Computer Science",
        "comp. sci": "Computer Science",
        "computer sci": "Computer Science",
        "dept of computer science": "Computer Science",
        "dept. of computer science": "Computer Science",
        "information technology": "Information Technology",
        "i.t": "Information Technology",
        "it dept": "Information Technology",
        "business": "Business Administration",
        "business admin": "Business Administration",
        "nursing": "Nursing",
        "agri": "Agriculture",
        "ag": "Agriculture",
        "math": "Mathematics",
        "maths": "Mathematics",
        "stats": "Statistics",
        "stat": "Statistics",
        "chem": "Chemistry",
        "phys": "Physics",
        "bio": "Biology",
        "phy": "Physics",
    },
    # Designation normalisation (already handled in normalize but belt-and-suspenders)
    "designation": {
        "prof.": "Prof",
        "dr.": "Dr",
        "mr.": "Mr",
        "mrs.": "Mrs",
        "ms.": "Ms",
        "doctor": "Dr",
        "professor": "Prof",
    },
    # Common intake values
    "intake": {
        "regular": "normal",
        "mainstream": "normal",
        "parallel": "special",
        "module ii": "special",
        "evening": "special",
        "weekend": "special",
    },
}


def _apply_corrections(model_name: str, data: dict) -> tuple[dict, list[str]]:
    """
    Apply built-in correction rules to a record's fields.
    Returns (corrected_data, list_of_corrections_applied).
    """
    corrected = dict(data)
    applied: list[str] = []

    for field, rules in _BUILTIN_CORRECTIONS.items():
        if field not in corrected:
            continue
        raw_value = str(corrected[field]).strip()
        raw_lower = raw_value.lower()
        if raw_lower in rules:
            new_value = rules[raw_lower]
            if new_value != raw_value:
                applied.append(f"'{field}': '{raw_value}' → '{new_value}'")
                corrected[field] = new_value

    return corrected, applied


def _load_learned_corrections() -> dict[str, dict[str, str]]:
    """
    Load user-applied corrections from the DB (Stage 11: Learning).
    Returns same structure as _BUILTIN_CORRECTIONS.
    Falls back to empty dict if the ImportCorrectionLog model doesn't exist yet.
    """
    try:
        from admins.models import ImportCorrectionLog  # noqa — may not exist
        rows = ImportCorrectionLog.objects.values("field", "original_value", "corrected_value")
        learned: dict[str, dict[str, str]] = {}
        for row in rows:
            f, orig, corr = row["field"], row["original_value"], row["corrected_value"]
            learned.setdefault(f, {})[orig.lower()] = corr
        return learned
    except Exception:
        return {}


def _apply_all_corrections(model_name: str, data: dict) -> tuple[dict, list[str]]:
    """Apply both built-in and learned corrections."""
    corrected, applied = _apply_corrections(model_name, data)
    learned = _load_learned_corrections()
    for field, rules in learned.items():
        if field not in corrected:
            continue
        raw = str(corrected[field]).strip()
        raw_lower = raw.lower()
        if raw_lower in rules:
            new_val = rules[raw_lower]
            if new_val != raw:
                applied.append(f"'{field}' (learned): '{raw}' → '{new_val}'")
                corrected[field] = new_val
    return corrected, applied


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – Format-specific Extraction (re-used from v1, with improvements)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_docx_text(file_like) -> str:
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(file_like)
    body = doc.element.body
    lines: list[str] = []
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            text = Paragraph(child, doc).text.strip()
            if text:
                lines.append(text)
        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            lines.append("[TABLE]")
            last_row_text = None
            for row in table.rows:
                cells = [c.text.strip().replace("\n", " / ") for c in row.cells]
                deduped = []
                for c in cells:
                    if not deduped or deduped[-1] != c:
                        deduped.append(c)
                row_text = " | ".join(c for c in deduped if c)
                if row_text and row_text != last_row_text:
                    lines.append(row_text)
                last_row_text = row_text
            lines.append("[/TABLE]")
    return "\n".join(lines)


def _extract_text(file) -> str:
    name = file.name.lower()
    data = file.read()

    if name.endswith(".csv"):
        return data.decode("utf-8", errors="replace")

    if name.endswith((".xlsx", ".xls")):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            rows = []
            for ws in wb.worksheets:
                rows.append(f"[Sheet: {ws.title}]")
                for row in ws.iter_rows(max_row=1000, values_only=True):
                    rows.append("\t".join(str(c) if c is not None else "" for c in row))
            return "\n".join(rows)
        except Exception:
            import pandas as pd
            dfs = pd.read_excel(io.BytesIO(data), sheet_name=None, nrows=1000)
            return "\n".join(
                f"[Sheet: {k}]\n{df.to_csv(index=False)}" for k, df in dfs.items()
            )

    if name.endswith(".pdf"):
        _PDF_PAGE_CAP = 100
        try:
            import pdfplumber
            text_parts = []
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                total_pages = len(pdf.pages)
                for page in pdf.pages[:_PDF_PAGE_CAP]:
                    t = page.extract_text()
                    if t:
                        text_parts.append(t)
                if total_pages > _PDF_PAGE_CAP:
                    text_parts.append(
                        f"\n[NOTE: document has {total_pages} pages; only the first "
                        f"{_PDF_PAGE_CAP} were processed.]"
                    )
            return "\n".join(text_parts) or "[Empty PDF]"
        except ImportError:
            pass
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(data))
            pages_text = [p.extract_text() or "" for p in reader.pages[:_PDF_PAGE_CAP]]
            return "\n".join(pages_text)
        except Exception as e:
            return f"[PDF extraction failed: {e}]"

    if name.endswith(".docx"):
        try:
            return _extract_docx_text(io.BytesIO(data))
        except Exception as e:
            return f"[DOCX extraction failed: {e}]"

    if name.endswith(".txt"):
        return data.decode("utf-8", errors="replace")

    return data.decode("utf-8", errors="replace")


def _sniff_rows_from_tabular(file) -> tuple[list[dict], list[str]] | None:
    """Read CSV/Excel into row dicts using the header row. Returns None for non-tabular."""
    name = file.name.lower()
    file.seek(0)
    if name.endswith(".csv"):
        text = file.read().decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(r) for r in reader]
        return rows, list(reader.fieldnames or [])
    if name.endswith((".xlsx", ".xls")):
        import openpyxl
        file.seek(0)
        wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows_iter = ws.iter_rows(max_row=2000, values_only=True)
        try:
            headers = [str(h).strip() if h is not None else "" for h in next(rows_iter)]
        except StopIteration:
            return [], []
        rows = []
        for row in rows_iter:
            d = {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
            if any(v not in (None, "") for v in d.values()):
                rows.append(d)
        return rows, headers
    return None


# ─────────────────────────────────────────────────────────────────────────────
# AI column-mapping helpers  (Stage 7)
# ─────────────────────────────────────────────────────────────────────────────

#: Deterministic column-name → canonical field name map.
#: Checked before calling AI so structured files never need AI.
_COLUMN_ALIASES: dict[str, str] = {
    # Course code synonyms
    "unit code": "course_code",
    "code": "course_code",
    "course code": "course_code",
    "course_code": "course_code",
    # Course name synonyms
    "unit name": "course_name",
    "unit title": "course_name",
    "course name": "course_name",
    "course title": "course_name",
    "course_name": "course_name",
    # Lecturer/staff synonyms
    "assigned to": "lecturer",
    "staff": "lecturer",
    "instructor": "lecturer",
    "teacher": "lecturer",
    "teaching staff": "lecturer",
    # Department synonyms
    "dept": "department",
    "dept.": "department",
    "school": "department",
    # Program synonyms
    "programme": "program",
    "degree": "program",
    "course": "program",  # ambiguous but common
    # Year synonyms
    "level": "year",
    "class": "year",
    "year of study": "year",
    # Students synonyms
    "no. of students": "number_of_students",
    "student count": "number_of_students",
    "students": "number_of_students",
    "enrollment": "number_of_students",
    # Name synonyms
    "full name": "name",
    "lecturer name": "name",
    "staff name": "name",
    # Payroll synonyms
    "payroll no": "payroll_number",
    "payroll no.": "payroll_number",
    "staff no": "payroll_number",
    "staff number": "payroll_number",
    "employee no": "payroll_number",
}


def _map_columns(raw_headers: list[str]) -> dict[str, str]:
    """
    Return {canonical_field → original_header} for as many headers as can
    be resolved deterministically. Unresolved headers are left out.
    """
    mapping: dict[str, str] = {}
    for h in raw_headers:
        if not h:
            continue
        canonical = _COLUMN_ALIASES.get(h.strip().lower())
        if canonical and canonical not in mapping:
            mapping[canonical] = h
    return mapping


def _best_schema_for_headers(headers: list[str], hint_models: list[str] | None = None) -> str | None:
    """Pick the MODEL_SCHEMA entry whose fields best match the given (mapped) headers."""
    canonical_headers = set(_map_columns(headers).keys())
    # also add raw headers normalised directly
    canonical_headers |= {h.strip().lower().replace(" ", "_") for h in headers if h}

    candidates = hint_models or list(MODEL_SCHEMA.keys())
    best_model, best_score = None, 0
    for model_name in candidates:
        if model_name not in MODEL_SCHEMA:
            continue
        schema_fields = set(MODEL_SCHEMA[model_name]["fields"])
        score = len(canonical_headers & schema_fields)
        if score > best_score:
            best_model, best_score = model_name, score
    return best_model if best_score > 0 else None


# ─────────────────────────────────────────────────────────────────────────────
# Rule-based extraction (no AI)  — Stages 2-6 without Stage 7
# ─────────────────────────────────────────────────────────────────────────────

def _rule_based_extract_v2(
    file, hint_models: list[str] | None = None
) -> dict:
    """
    Deterministic CSV/Excel → staged records with normalisation + validation.
    No AI required.
    """
    sniffed = _sniff_rows_from_tabular(file)
    if sniffed is None:
        return {
            "records": [],
            "summary": (
                "No AI provider is configured. PDF/Word files need AI to extract "
                "structured rows. Upload CSV or Excel instead — those work without AI."
            ),
        }

    rows, headers = sniffed
    if not rows:
        return {"records": [], "summary": "No data rows found in the file."}

    model_name = _best_schema_for_headers(headers, hint_models)
    if not model_name:
        return {
            "records": [],
            "summary": (
                "Could not match column headers to any known model. "
                f"Expected one of: {', '.join(MODEL_SCHEMA.keys())}. "
                "Rename columns to match a CSV template, or configure AI for smarter mapping."
            ),
        }

    col_map = _map_columns(headers)  # canonical → original header
    schema = MODEL_SCHEMA[model_name]
    master = _db_master_data()
    records = []

    for row in rows:
        raw_data: dict[str, str] = {}
        for field in schema["fields"]:
            orig_header = col_map.get(field, field)
            val = row.get(orig_header, "")
            raw_data[field] = "" if val is None else str(val).strip()

        # Stage 4: Normalize
        normalized = _normalize_record_fields(model_name, raw_data)

        # Stage 6: Corrections
        corrected, corrections_applied = _apply_all_corrections(model_name, normalized)

        # Stage 5: Validate
        validated = _validate_record(model_name, corrected, master)

        records.append({
            "model": model_name,
            "data": {k: v for k, v in validated.items() if not k.startswith("_")},
            "status": validated["_status"],
            "issues": validated["_issues"],
            "suggestions": validated["_suggestions"],
            "corrections_applied": corrections_applied,
            "confidence": "high" if validated["_status"] == "VALID" else
                          "medium" if validated["_status"] == "WARNING" else "low",
        })

    counts = {s: sum(1 for r in records if r["status"] == s) for s in ("VALID", "WARNING", "ERROR")}
    summary = (
        f"Rule-based extraction: {len(records)} row(s) detected as '{model_name}'. "
        f"✅ {counts['VALID']} valid | ⚠️ {counts['WARNING']} warning | ❌ {counts['ERROR']} error. "
        "AI was not used — configure an AI provider for smarter column mapping and PDF/Word support."
    )
    return {"records": records, "summary": summary}


# ─────────────────────────────────────────────────────────────────────────────
# AI extraction prompts (Stage 7)
# ─────────────────────────────────────────────────────────────────────────────

_CHUNK_CHAR_BUDGET = 9000
_CHUNK_OVERLAP = 300


def _chunk_text(raw_text: str, budget: int = _CHUNK_CHAR_BUDGET, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    if len(raw_text) <= budget:
        return [raw_text]
    chunks = []
    start = 0
    n = len(raw_text)
    while start < n:
        end = min(start + budget, n)
        if end < n:
            nl = raw_text.rfind("\n", start, end)
            if nl > start + budget // 2:
                end = nl
        chunks.append(raw_text[start:end])
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _db_context_string() -> str:
    master = _db_master_data()
    lines = []
    if master["faculties"]:
        lines.append(f"Faculties: {', '.join(sorted(master['faculties'])[:30])}")
    if master["departments"]:
        lines.append(f"Departments: {', '.join(sorted(master['departments'])[:60])}")
    if master["programs"]:
        lines.append(f"Programs: {', '.join(sorted(master['programs'])[:60])}")
    if master["lecturers_name"]:
        lines.append(f"Lecturers: {', '.join(sorted(master['lecturers_name'])[:60])}")
    if master["course_codes"]:
        lines.append(f"Course codes: {', '.join(sorted(master['course_codes'])[:100])}")
    if master["buildings"]:
        lines.append(f"Buildings: {', '.join(sorted(master['buildings'])[:40])}")
    if master["venue_codes"]:
        lines.append(f"Venue codes: {', '.join(sorted(master['venue_codes'])[:100])}")
    return "\n".join(lines) if lines else "No existing DB data available."


def _build_extract_prompt(
    text_chunk: str,
    db_ctx: str,
    target_models: list[str],
    classification: dict,
    chunk_index: int = 1,
    chunk_total: int = 1,
) -> str:
    schema_parts = "\n".join(
        f"- **{k}**: {v['description']}\n  Fields: {', '.join(v['fields'])}\n  Required: {', '.join(v['required'])}"
        for k, v in MODEL_SCHEMA.items()
        if not target_models or k in target_models
    )
    chunk_note = (
        f"\nNOTE: This is part {chunk_index} of {chunk_total} of a larger document. "
        f"Extract every record in THIS part only.\n"
        if chunk_total > 1 else ""
    )
    doc_type_note = (
        f"\nDocument type detected: {classification.get('doc_type', 'unknown')} "
        f"(confidence: {classification.get('confidence', 'low')}).\n"
    )
    return f"""You are a university data extraction expert. Your job is ONLY to extract records.
Do NOT write anything to the database. A separate validation engine will check your output.

## University DB context (use for name disambiguation — do not invent records):
{db_ctx}

## Target model schemas:
{schema_parts}
{doc_type_note}{chunk_note}
## Document text:
{text_chunk}

## Extraction rules:
1. Extract ALL records visible in this text, without summarising or truncating.
2. For course codes like "COSC402_A", strip the section suffix → "COSC402".
3. If multiple lecturers appear for the same course (e.g. "Dr A (Group A), Dr B (Group B)"),
   emit one record per lecturer.
4. Normalise designation: Prof/Dr/Mr/Ms/Mrs only.
5. Infer year_of_study from the leading digit in the course code where possible.
6. Use exact names from the DB context when matching — do not invent new department/program names.
7. If you cannot determine a required field with confidence, leave it blank and add an issue note.
8. Set "confidence": "high" | "medium" | "low" per record.
9. Set "issues": an array of strings describing any problems or ambiguities.

Respond with ONLY valid JSON in this exact format:
{{
  "records": [
    {{
      "model": "CourseAllocation",
      "data": {{...fields...}},
      "confidence": "high",
      "issues": []
    }}
  ],
  "summary": "Brief human-readable summary of what was found and any warnings."
}}"""


def _build_refine_prompt(records_json: str, user_prompt: str, db_ctx: str) -> str:
    return f"""You are a university data assistant. The user has reviewed extracted records
and wants to apply a correction or instruction.

## Current records:
{records_json}

## DB context:
{db_ctx}

## User instruction:
"{user_prompt}"

Apply the instruction accurately. Common examples:
- "COSC402 is for BSc Computer Science Year 4" → update program + year on matching records
- "Change lecturer for COSC401 to Dr Mwangi" → update lecturer field
- "All records in this file are for the Computer Science department" → set department
- "Row 3 course code should be COSC403 not COSC430" → fix that specific record

Respond ONLY with valid JSON:
{{
  "records": [...updated records...],
  "summary": "What was changed."
}}"""


def _parse_json_from_ai(raw: str) -> Any:
    text = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    start = min((text.find(c) for c in "([{" if text.find(c) != -1), default=0)
    text = text[start:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = _attempt_json_repair(text)
        if repaired is not None:
            return repaired
        raise


def _attempt_json_repair(text: str) -> Any | None:
    m = re.search(r'"records"\s*:\s*\[', text)
    if not m:
        return None
    arr_start = m.end()
    depth = 0
    in_string = False
    escape = False
    last_complete_end = None
    i = arr_start
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    last_complete_end = i + 1
        i += 1
    if last_complete_end is None:
        return None
    try:
        records = json.loads("[" + text[arr_start:last_complete_end] + "]")
    except json.JSONDecodeError:
        return None
    return {"records": records, "summary": "(response truncated — partial results recovered)"}


# ─────────────────────────────────────────────────────────────────────────────
# AI extraction: normalise + validate + correct each AI-returned record
# ─────────────────────────────────────────────────────────────────────────────

def _post_process_ai_records(raw_records: list[dict]) -> list[dict]:
    """
    Apply Stages 4-6 to records returned by the AI.
    The AI may return records with un-normalised values or with issues
    that the correction engine can fix before human review.
    """
    master = _db_master_data()
    processed = []
    for rec in raw_records:
        model_name = rec.get("model", "")
        data = rec.get("data", {})
        ai_issues = rec.get("issues", [])
        ai_confidence = rec.get("confidence", "medium")

        # Stage 4: Normalize
        normalized = _normalize_record_fields(model_name, data)

        # Stage 6: Corrections
        corrected, corrections_applied = _apply_all_corrections(model_name, normalized)

        # Stage 5: Validate
        validated = _validate_record(model_name, corrected, master)

        # Merge AI issues with our validation issues (deduplicate)
        all_issues = list(dict.fromkeys(ai_issues + validated["_issues"]))

        # Effective confidence: downgrade if validation found problems
        if validated["_status"] == "ERROR":
            effective_confidence = "low"
        elif validated["_status"] == "WARNING":
            effective_confidence = "medium" if ai_confidence == "high" else ai_confidence
        else:
            effective_confidence = ai_confidence

        processed.append({
            "model": model_name,
            "data": {k: v for k, v in validated.items() if not k.startswith("_")},
            "status": validated["_status"],
            "issues": all_issues,
            "suggestions": validated["_suggestions"],
            "corrections_applied": corrections_applied,
            "confidence": effective_confidence,
        })
    return processed


# ─────────────────────────────────────────────────────────────────────────────
# Stage 10 – DB Commit Layer (via staging)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_fk(model_label: str, field_value: str):
    if not field_value:
        return None
    try:
        from django.apps import apps
        app_label, model_name = model_label.split(".")
        Model = apps.get_model(app_label, model_name)
        for lookup in ("name__iexact", "payroll_number__iexact", "code__iexact", "title__iexact"):
            try:
                return Model.objects.get(**{lookup: field_value.strip()})
            except Exception:
                pass
        qs = Model.objects.filter(name__icontains=field_value.strip())
        if qs.count() == 1:
            return qs.first()
    except Exception:
        pass
    return None


@transaction.atomic
def _commit_records(records: list[dict]) -> dict:
    """
    Stage 10: commit only VALID / WARNING-accepted records.
    Skips ERROR records and explains why.
    Returns {"created": [...], "updated": [...], "errors": [...], "skipped": [...]}
    """
    from course_allocation.models import (
        CourseAllocation, LecturerCourseMapping, ProgramEnrollment,
    )
    from program_management.models import Program, ProgramCourse, ProgramCode
    from lecturer_portal.models import Lecturer
    from department_management.models import Department
    from faculty_management.models import Faculty
    from room_management.models import Venue, Building, LabVenue

    created = []
    updated = []
    errors = []
    skipped = []

    def _bool(val, default=False):
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("true", "yes", "1")
        return default

    def _int(val, default=0):
        try:
            return int(val) if val not in (None, "") else default
        except (ValueError, TypeError):
            return default

    for i, rec in enumerate(records):
        model_name = rec.get("model")
        data = rec.get("data", {})
        status = rec.get("status", "ERROR")
        label = f"Row {i + 1} [{model_name}]"

        # Stage 9: Partial Import — skip ERRORs, let VALID + WARNING (accepted) through
        if status == "ERROR":
            skipped.append(f"{label}: skipped (ERROR status) — {'; '.join(rec.get('issues', []))}")
            continue

        try:
            # ── Faculty ──────────────────────────────────────────────────────
            if model_name == "Faculty":
                if not data.get("name", "").strip():
                    errors.append(f"{label}: 'name' is required")
                    continue
                obj, was_created = Faculty.objects.update_or_create(
                    name__iexact=data["name"].strip(),
                    defaults={"name": data["name"].strip(), "description": data.get("description", "")},
                )
                (created if was_created else updated).append(f"Faculty {obj.name}")

            # ── Department ───────────────────────────────────────────────────
            elif model_name == "Department":
                if not data.get("name", "").strip():
                    errors.append(f"{label}: 'name' is required")
                    continue
                faculty = _resolve_fk("faculty_management.Faculty", data.get("faculty", ""))
                if not faculty:
                    errors.append(f"{label}: Faculty '{data.get('faculty')}' not found")
                    continue
                obj, was_created = Department.objects.update_or_create(
                    name__iexact=data["name"].strip(),
                    defaults={"name": data["name"].strip(), "faculty": faculty, "description": data.get("description", "")},
                )
                (created if was_created else updated).append(f"Department {obj.name}")

            # ── CourseAllocation ─────────────────────────────────────────────
            elif model_name == "CourseAllocation":
                if not data.get("course_code", "").strip():
                    errors.append(f"{label}: 'course_code' is required")
                    continue
                dept = _resolve_fk("department_management.Department", data.get("department", ""))
                if not dept:
                    errors.append(f"{label}: Department '{data.get('department')}' not found")
                    continue
                program = _resolve_fk("program_management.Program", data.get("program", ""))
                lecturer = _resolve_fk("lecturer_portal.Lecturer", data.get("lecturer", ""))
                obj, was_created = CourseAllocation.objects.update_or_create(
                    course_code=data["course_code"].strip().upper(),
                    department=dept,
                    defaults={
                        "course_name": data.get("course_name", ""),
                        "program": program,
                        "lecturer": lecturer,
                        "number_of_students": _int(data.get("number_of_students")),
                        "intake": data.get("intake", "normal"),
                        "is_elective": _bool(data.get("is_elective")),
                        "is_evening_weekend": _bool(data.get("is_evening_weekend")),
                    },
                )
                (created if was_created else updated).append(f"CourseAllocation {obj.course_code}")

            # ── ProgramCourse ────────────────────────────────────────────────
            elif model_name == "ProgramCourse":
                if not data.get("course_code", "").strip():
                    errors.append(f"{label}: 'course_code' is required")
                    continue
                prog = _resolve_fk("program_management.Program", data.get("program", ""))
                if not prog:
                    errors.append(f"{label}: Program '{data.get('program')}' not found")
                    continue
                obj, was_created = ProgramCourse.objects.update_or_create(
                    program=prog,
                    course_code=data["course_code"].strip().upper(),
                    year=_int(data.get("year"), 1),
                    semester=_int(data.get("semester"), 1),
                    defaults={"course_name": data.get("course_name", "")},
                )
                (created if was_created else updated).append(f"ProgramCourse {obj.course_code}")

            # ── LecturerCourseMapping ────────────────────────────────────────
            elif model_name == "LecturerCourseMapping":
                lect = _resolve_fk("lecturer_portal.Lecturer", data.get("lecturer", ""))
                if not lect:
                    errors.append(f"{label}: Lecturer '{data.get('lecturer')}' not found")
                    continue
                dept = _resolve_fk("department_management.Department", data.get("department", ""))
                mapping, was_created = LecturerCourseMapping.objects.get_or_create(
                    lecturer=lect, department=dept, defaults={"notes": data.get("notes", "")}
                )
                codes = [c.strip().upper() for c in str(data.get("course_codes", "")).split(",") if c.strip()]
                if codes:
                    pcs = ProgramCourse.objects.filter(course_code__in=codes)
                    mapping.courses.add(*pcs)
                (created if was_created else updated).append(f"LecturerCourseMapping {lect.name}")

            # ── ProgramEnrollment ────────────────────────────────────────────
            elif model_name == "ProgramEnrollment":
                prog = _resolve_fk("program_management.Program", data.get("program", ""))
                if not prog:
                    errors.append(f"{label}: Program '{data.get('program')}' not found")
                    continue
                obj, was_created = ProgramEnrollment.objects.update_or_create(
                    program=prog,
                    entry_year=_int(data.get("entry_year") or data.get("year"), 1),
                    defaults={"number_of_students": _int(data.get("number_of_students"))},
                )
                (created if was_created else updated).append(
                    f"ProgramEnrollment {prog.name} entry {obj.entry_year}"
                )

            # ── Lecturer ─────────────────────────────────────────────────────
            elif model_name == "Lecturer":
                if not data.get("payroll_number", "").strip():
                    errors.append(f"{label}: 'payroll_number' is required")
                    continue
                obj, was_created = Lecturer.objects.update_or_create(
                    payroll_number=data["payroll_number"].strip(),
                    defaults={
                        "name": data.get("name", ""),
                        "email": data.get("email", ""),
                        "designation": data.get("designation", "Mr"),
                        "department": _resolve_fk("department_management.Department", data.get("department", "")),
                    },
                )
                (created if was_created else updated).append(f"Lecturer {obj.name}")

            # ── Program ──────────────────────────────────────────────────────
            elif model_name == "Program":
                if not data.get("name", "").strip():
                    errors.append(f"{label}: 'name' is required")
                    continue
                dept = _resolve_fk("department_management.Department", data.get("department", ""))
                if not dept:
                    errors.append(f"{label}: Department '{data.get('department')}' not found")
                    continue
                obj, was_created = Program.objects.update_or_create(
                    name__iexact=data["name"].strip(),
                    defaults={"name": data["name"].strip(), "department": dept, "description": data.get("description", "")},
                )
                (created if was_created else updated).append(f"Program {obj.name}")

            # ── ProgramCode ──────────────────────────────────────────────────
            elif model_name == "ProgramCode":
                if not data.get("code", "").strip():
                    errors.append(f"{label}: 'code' is required")
                    continue
                prog = _resolve_fk("program_management.Program", data.get("program", ""))
                if not prog:
                    errors.append(f"{label}: Program '{data.get('program')}' not found")
                    continue
                obj, was_created = ProgramCode.objects.update_or_create(
                    code=data["code"].strip().upper(),
                    defaults={"program": prog},
                )
                (created if was_created else updated).append(f"ProgramCode {obj.code}")

            # ── Building ─────────────────────────────────────────────────────
            elif model_name == "Building":
                if not data.get("name", "").strip() or not data.get("code", "").strip():
                    errors.append(f"{label}: 'name' and 'code' are required")
                    continue
                obj, was_created = Building.objects.update_or_create(
                    code__iexact=data["code"].strip(),
                    defaults={
                        "name": data["name"].strip(),
                        "code": data["code"].strip().upper(),
                        "is_workshop": _bool(data.get("is_workshop")),
                        "description": data.get("description", ""),
                    },
                )
                (created if was_created else updated).append(f"Building {obj.code}")

            # ── Venue ────────────────────────────────────────────────────────
            elif model_name == "Venue":
                if not data.get("code", "").strip():
                    errors.append(f"{label}: 'code' is required")
                    continue
                building = _resolve_fk("room_management.Building", data.get("building", ""))
                obj, was_created = Venue.objects.update_or_create(
                    code__iexact=data["code"].strip(),
                    defaults={
                        "code": data["code"].strip(),
                        "building": building,
                        "capacity": _int(data.get("capacity")) or None,
                        "exam_capacity": _int(data.get("exam_capacity")) or None,
                        "is_workshop": _bool(data.get("is_workshop")),
                        "description": data.get("description", ""),
                    },
                )
                (created if was_created else updated).append(f"Venue {obj.code}")

            # ── LabVenue ─────────────────────────────────────────────────────
            elif model_name == "LabVenue":
                if not data.get("code", "").strip():
                    errors.append(f"{label}: 'code' is required")
                    continue
                obj, was_created = LabVenue.objects.update_or_create(
                    code__iexact=data["code"].strip(),
                    defaults={
                        "code": data["code"].strip(),
                        "capacity": _int(data.get("capacity")) or None,
                        "description": data.get("description", ""),
                        "equipment": data.get("equipment", ""),
                    },
                )
                (created if was_created else updated).append(f"LabVenue {obj.code}")

            else:
                errors.append(f"{label}: Unknown model '{model_name}'")

        except Exception as exc:
            errors.append(f"{label}: {exc}")
            logger.exception("Commit error for record %s", rec)

    return {"created": created, "updated": updated, "errors": errors, "skipped": skipped}


# ─────────────────────────────────────────────────────────────────────────────
# CSV template
# ─────────────────────────────────────────────────────────────────────────────

def _generate_csv(model_name: str) -> str:
    schema = MODEL_SCHEMA.get(model_name)
    if not schema:
        return ""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(schema["fields"])
    sample = {f: f"<{f}>" for f in schema["fields"]}
    for f in schema["required"]:
        sample[f] = f"[REQUIRED] {f}"
    writer.writerow([sample[f] for f in schema["fields"]])
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Views
# ─────────────────────────────────────────────────────────────────────────────

@_sudo
def smart_import_v2_view(request):
    """Main Smart Importer 2.0 page."""
    return render(request, "admins/smart_import_v2.html", {
        "models": list(MODEL_SCHEMA.keys()),
        "model_schema_json": json.dumps(
            {k: {"fields": v["fields"], "required": v["required"], "description": v["description"]}
             for k, v in MODEL_SCHEMA.items()}
        ),
        "ai_configured": ai_is_configured(),
        "version": "2.0",
        "pipeline_stages": [
            "Upload", "Classify", "Extract", "Normalize", "Validate", "Correct",
            "AI Assist", "Review", "Partial Import", "Commit", "Learn", "Audit",
        ],
    })


@_sudo
@require_POST
def smart_import_v2_classify(request):
    """
    Stage 1: Classify an uploaded document BEFORE full extraction.
    Fast — only reads the first ~3000 characters.
    Returns: {doc_type, confidence, suggested_models, blocked, reason}
    """
    uploaded = request.FILES.get("document")
    if not uploaded:
        return JsonResponse({"error": "No file uploaded."}, status=400)

    try:
        data = uploaded.read(6000)
        sample = data.decode("utf-8", errors="replace")
    except Exception as e:
        return JsonResponse({"error": f"Could not read file: {e}"}, status=400)

    classification = _classify_document(sample)
    return JsonResponse(classification)


@_sudo
@require_POST
def smart_import_v2_extract(request):
    """
    Stages 2-7: Upload → Extract → Normalize → Correct → Validate → (AI if configured).
    Returns staged records with per-row VALID / WARNING / ERROR status.
    """
    uploaded = request.FILES.get("document")
    if not uploaded:
        return JsonResponse({"error": "No file uploaded."}, status=400)

    run_log = ImportRunLog(user=request.user, filename=uploaded.name, action="extract_v2")
    target_models = [m.strip() for m in request.POST.get("target_models", "").split(",") if m.strip()]
    force_classification = request.POST.get("doc_type", "")

    # Stage 1: classify
    run_log.section("Stage 1 – Document Classification")
    try:
        data_bytes = uploaded.read(6000)
        sample = data_bytes.decode("utf-8", errors="replace")
        uploaded.seek(0)  # rewind for full extraction
    except Exception:
        sample = ""
        uploaded.seek(0)

    classification = _classify_document(sample)
    run_log.write(classification, label="CLASSIFICATION RESULT")

    if classification["blocked"] and not force_classification:
        run_log.close(status="blocked", summary=classification["reason"])
        return JsonResponse({
            "blocked": True,
            "doc_type": classification["doc_type"],
            "reason": classification["reason"],
        }, status=422)

    # Use classification to hint model selection if no explicit target provided
    if not target_models and classification.get("suggested_models"):
        target_models = classification["suggested_models"]
        run_log.write(f"Using classification-suggested models: {target_models}")

    client = get_ai_client()

    # ── No-AI path ────────────────────────────────────────────────────────────
    if client is None:
        run_log.section("No AI — rule-based fallback (Stages 2-6)")
        result = _rule_based_extract_v2(uploaded, target_models)
        run_log.write(f"Produced {len(result['records'])} record(s).")
        run_log.write(result["summary"], label="SUMMARY")
        run_log.close(status="success (no-AI)", summary=result["summary"])
        return JsonResponse({
            "records": result["records"],
            "summary": result["summary"],
            "classification": classification,
            "ai_used": False,
            "raw_text_preview": "",
        })

    # ── AI path ───────────────────────────────────────────────────────────────
    run_log.section("Stage 2 – Text Extraction")
    try:
        raw_text = _extract_text(uploaded)
    except Exception as e:
        run_log.error(f"Text extraction failed: {e}")
        run_log.close(status="failed", summary="Could not read file")
        return JsonResponse({"error": f"Could not read file: {e}"}, status=400)

    run_log.write(f"Extracted {len(raw_text)} chars from '{uploaded.name}'.")
    run_log.write(raw_text[:3000], label="EXTRACTED TEXT (first 3000 chars)")

    if not raw_text or not raw_text.strip():
        run_log.close(status="failed", summary="Empty document")
        return JsonResponse({"error": "No readable text could be extracted from this file."}, status=400)

    db_ctx = _db_context_string()
    chunks = _chunk_text(raw_text)
    run_log.write(f"Split into {len(chunks)} chunk(s). AI: {client.get_provider_display()} / {client.model}")

    all_records: list[dict] = []
    summaries: list[str] = []
    chunk_errors: list[str] = []

    # Stage 7 – AI assistant
    for idx, chunk in enumerate(chunks, start=1):
        run_log.section(f"Stage 7 – AI Call — chunk {idx}/{len(chunks)}")
        try:
            prompt = _build_extract_prompt(chunk, db_ctx, target_models, classification, idx, len(chunks))
            run_log.write(prompt[:4000], label="PROMPT (first 4000 chars)")
            raw_response = client.call(prompt)
            run_log.write(raw_response[:4000], label="AI RESPONSE (first 4000 chars)")
            parsed = _parse_json_from_ai(raw_response)
        except AIError as e:
            run_log.error(f"Chunk {idx}: AIError — {e}")
            chunk_errors.append(f"Part {idx}/{len(chunks)}: {e}")
            continue
        except json.JSONDecodeError as e:
            run_log.error(f"Chunk {idx}: invalid JSON — {e}")
            chunk_errors.append(f"Part {idx}/{len(chunks)}: AI returned invalid JSON.")
            continue
        except Exception as e:
            logger.exception("AI extraction failed on chunk %s/%s", idx, len(chunks))
            run_log.error(f"Chunk {idx}: unexpected — {e}")
            chunk_errors.append(f"Part {idx}/{len(chunks)}: unexpected error.")
            continue

        raw_chunk_records = parsed.get("records", [])
        run_log.write(f"Chunk {idx} returned {len(raw_chunk_records)} raw record(s).")

        # Stages 4-6 applied to AI output
        processed = _post_process_ai_records(raw_chunk_records)
        run_log.write(f"After normalise/correct/validate: {len(processed)} record(s).")
        all_records.extend(processed)
        if parsed.get("summary"):
            summaries.append(parsed["summary"] if len(chunks) == 1 else f"Part {idx}: {parsed['summary']}")

    if not all_records and chunk_errors:
        error_msg = "Extraction failed for all parts. " + " | ".join(chunk_errors[:3])
        run_log.close(status="failed", summary=error_msg)
        return JsonResponse({"error": error_msg}, status=502)

    counts = {s: sum(1 for r in all_records if r.get("status") == s)
              for s in ("VALID", "WARNING", "ERROR")}
    base_summary = " ".join(summaries) if summaries else "Extraction complete."
    status_summary = (
        f"{len(all_records)} record(s) found: "
        f"✅ {counts['VALID']} valid | ⚠️ {counts['WARNING']} warning | ❌ {counts['ERROR']} error."
    )
    full_summary = status_summary + " " + base_summary
    if chunk_errors:
        full_summary += f" ⚠️ {len(chunk_errors)}/{len(chunks)} chunk(s) failed: " + " | ".join(chunk_errors[:3])
    if len(chunks) > 1:
        full_summary = f"Processed in {len(chunks)} parts. " + full_summary

    run_log.section("Result")
    run_log.write(f"Total: {len(all_records)} | Chunks failed: {len(chunk_errors)}/{len(chunks)}")
    run_log.close(
        status="success" if not chunk_errors else "partial",
        summary=f"{len(all_records)} records, {len(chunk_errors)} chunk failures"
    )

    return JsonResponse({
        "records": all_records,
        "summary": full_summary,
        "classification": classification,
        "ai_used": True,
        "ai_provider": client.get_provider_display(),
        "raw_text_preview": raw_text[:1500],
        "counts": counts,
        "chunks_total": len(chunks),
        "chunks_failed": len(chunk_errors),
    })


@_sudo
@require_POST
def smart_import_v2_refine(request):
    """Stage 7 (continued): AI refinement pass after user feedback."""
    client = get_ai_client()
    if client is None:
        return JsonResponse({
            "error": "Refine requires an AI provider. Configure one in AI Settings, "
                     "or edit table cells directly — manual editing always works."
        }, status=400)

    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    records = body.get("records", [])
    user_prompt = body.get("prompt", "").strip()
    if not user_prompt:
        return JsonResponse({"error": "No refinement prompt provided."}, status=400)

    run_log = ImportRunLog(user=request.user, filename="", action="refine_v2")
    run_log.write(user_prompt, label="USER INSTRUCTION")
    run_log.write(f"Records in: {len(records)}")

    db_ctx = _db_context_string()
    try:
        prompt = _build_refine_prompt(json.dumps(records, indent=2), user_prompt, db_ctx)
        raw_response = client.call(prompt)
        parsed = _parse_json_from_ai(raw_response)
    except AIError as e:
        run_log.close(status="failed", summary=str(e))
        return JsonResponse({"error": str(e)}, status=502)
    except Exception as e:
        logger.exception("AI refine failed")
        run_log.close(status="failed", summary="unexpected error")
        return JsonResponse({"error": "AI refine failed. Please try again."}, status=502)

    raw_records = parsed.get("records", [])
    # Re-apply normalisation + validation after AI refinement
    processed = _post_process_ai_records(raw_records)
    run_log.close(status="success", summary=f"{len(records)} → {len(processed)} after refine")

    return JsonResponse({
        "records": processed,
        "summary": parsed.get("summary", ""),
    })


@_sudo
@require_POST
def smart_import_v2_commit(request):
    """
    Stage 10: Commit approved records to production.
    Only VALID and WARNING (user-accepted) rows are written.
    ERROR rows are skipped and reported.
    Stores user corrections for Stage 11 (learning).
    """
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    records = body.get("records", [])
    user_corrections = body.get("user_corrections", [])  # [{field, original, corrected}]
    if not records:
        return JsonResponse({"error": "No records to commit."}, status=400)

    run_log = ImportRunLog(user=request.user, filename="", action="commit_v2")
    run_log.write(f"Records submitted: {len(records)}")

    # Stage 11: Persist user corrections for learning
    if user_corrections:
        try:
            from admins.models import ImportCorrectionLog  # noqa
            for correction in user_corrections:
                ImportCorrectionLog.objects.get_or_create(
                    field=correction.get("field", ""),
                    original_value=correction.get("original", ""),
                    defaults={"corrected_value": correction.get("corrected", "")},
                )
            run_log.write(f"Stored {len(user_corrections)} correction(s) for future learning.")
        except Exception as e:
            run_log.write(f"Could not store corrections (ImportCorrectionLog not available): {e}")

    try:
        result = _commit_records(records)
    except Exception as e:
        logger.exception("Commit v2 failed")
        run_log.close(status="failed", summary=str(e))
        return JsonResponse({"error": str(e)}, status=500)

    run_log.close(
        status="success" if not result.get("errors") else "partial",
        summary=(
            f"{len(result.get('created', []))} created, "
            f"{len(result.get('updated', []))} updated, "
            f"{len(result.get('skipped', []))} skipped (ERROR), "
            f"{len(result.get('errors', []))} commit errors"
        )
    )
    return JsonResponse(result)


@_sudo
def smart_import_v2_csv(request):
    """Return a downloadable CSV template for the requested model."""
    model_name = request.GET.get("model", "")
    if model_name not in MODEL_SCHEMA:
        return HttpResponse("Invalid model.", status=400)
    csv_data = _generate_csv(model_name)
    resp = HttpResponse(csv_data, content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{model_name}_template.csv"'
    return resp


@_sudo
def smart_import_v2_corrections(request):
    """Stage 11: View learned corrections log."""
    try:
        from admins.models import ImportCorrectionLog  # noqa
        corrections = list(
            ImportCorrectionLog.objects.values("field", "original_value", "corrected_value")
            .order_by("field", "original_value")[:500]
        )
    except Exception:
        corrections = []
    return JsonResponse({"corrections": corrections})