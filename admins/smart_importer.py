"""
admins/smart_importer.py
=========================
AI-powered (and AI-optional) document importer for the Sudo Dashboard.

Supports: CSV, Excel (.xlsx/.xls), PDF, Word (.docx), plain text.

This module does NOT hard-code any AI provider. It calls
`core.ai_registry.get_ai_client()` to get whichever provider is
currently active (Gemini, OpenAI, Anthropic, Ollama, or a custom
OpenAI-compatible endpoint) as configured in the DB
(`core.models.AIProviderSettings`), editable from Django admin or the
"AI Settings" sudo page.

GRACEFUL DEGRADATION: if no AI provider is configured/active, the
importer does NOT fail. It falls back to a deterministic, rule-based
CSV/Excel parser (column-header matching against MODEL_SCHEMA) so the
page keeps working exactly as a plain importer would. PDF/Word free-text
extraction does require AI to structure the data — when AI is off,
those formats return a clear, non-fatal message asking for CSV/Excel
instead, rather than a stack trace.

URL: /sudo-dashboard/smart-import/
AJAX endpoints:
  POST /sudo-dashboard/smart-import/extract/   → upload + extract (AI or rule-based)
  POST /sudo-dashboard/smart-import/refine/    → user prompt to refine rows (AI only)
  POST /sudo-dashboard/smart-import/commit/    → write approved rows to DB
  GET  /sudo-dashboard/smart-import/csv/       → generate downloadable CSV template

──────────────────────────────────────────────────────────────────────
SECURITY
──────────────────────────────────────────────────────────────────────
- No AI API key is ever accepted from the browser/request. Keys live
  only in core.models.AIProviderSettings, encrypted at rest, and are
  resolved server-side via core.ai_registry. This closes the previous
  design hole where a Gemini key was typed into a page input and sent
  with every request.
- All endpoints are sudo-only (superuser + authenticated).
- AI error text from providers is sanitised by ai_registry before it
  ever reaches this module, so provider error bodies can't leak key
  fragments into the UI.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Any

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
# Model schema registry  (what the AI / rule-based parser needs to know)
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
            "A specific room/hall/lecture theatre used for teaching or exams, e.g. "
            "'BSL 303' or 'SRP B03'. Fields: code (the room name/code exactly as "
            "written, e.g. 'BSL303' or 'FTC 602A'), capacity (int, normal teaching "
            "capacity), exam_capacity (int, seating capacity for exams — usually "
            "lower than teaching capacity), building (optional — the building name "
            "or code this room belongs to, if the document states or implies one, "
            "e.g. a 'BSL' prefix suggests the Business School Complex), "
            "is_workshop (bool, true only if explicitly described as a workshop), "
            "description (optional notes)."
        ),
        "fields": ["code", "capacity", "exam_capacity", "building", "is_workshop", "description"],
        "required": ["code"],
    },
    "Building": {
        "app": "room_management",
        "description": (
            "A building/complex that contains venues, e.g. 'Business School Complex' "
            "(code 'BSR'/'BSL') or 'Science Research Park' (code 'SRP'). Only extract "
            "this when the document explicitly names a building, not just a room-code "
            "prefix. Fields: name, code (short prefix used in room codes), "
            "is_workshop (bool, true only if the whole building is a workshop facility), "
            "description."
        ),
        "fields": ["name", "code", "is_workshop", "description"],
        "required": ["name", "code"],
    },
    "LabVenue": {
        "app": "room_management",
        "description": (
            "A laboratory room, distinct from a normal teaching venue — e.g. "
            "'CLAB1 — Computer Lab 1' or 'SLAB2 — Botany'. Fields: code (the lab's "
            "short code as written, e.g. 'CLAB1'), capacity (int, optional), "
            "description (what the lab is for, e.g. 'Computer Lab 1' or 'Botany'), "
            "equipment (optional, list of equipment if mentioned)."
        ),
        "fields": ["code", "capacity", "description", "equipment"],
        "required": ["code"],
    },
    "ProgramCode": {
        "app": "program_management",
        "description": (
            "A short registrar code for an academic program, e.g. 'BSC-CS' for "
            "'Bachelor of Science in Computer Science'. Only extract this when a "
            "document explicitly pairs a short code with a program name (common in "
            "registrar/admissions lists) — do not invent codes that aren't written "
            "in the document. Fields: program (the full program name, must match or "
            "closely match an existing Program), code (the short code)."
        ),
        "fields": ["program", "code"],
        "required": ["program", "code"],
    },
}



def _db_context() -> str:
    """
    Build a compact summary of existing DB records and send it to the AI
    with every extraction prompt, so it can:
      - resolve ambiguous names (e.g. "Dept of CS" → "Computer Science")
      - decide update-or-create (if the code already exists, update it)
      - avoid creating duplicates by recognising things already in the DB
    One entry per importable model — truncated to keep prompt size sane.
    """
    lines = []
    try:
        from faculty_management.models import Faculty
        faculties = list(Faculty.objects.values_list("name", flat=True)[:30])
        lines.append(f"Faculties: {', '.join(faculties)}")
    except Exception:
        pass
    try:
        from department_management.models import Department
        depts = list(Department.objects.values_list("name", flat=True)[:60])
        lines.append(f"Departments: {', '.join(depts)}")
    except Exception:
        pass
    try:
        from program_management.models import Program
        progs = list(Program.objects.values_list("name", flat=True)[:80])
        lines.append(f"Programs: {', '.join(progs)}")
    except Exception:
        pass
    try:
        from program_management.models import ProgramCode
        pcodes = list(ProgramCode.objects.values_list("code", flat=True)[:80])
        lines.append(f"Program codes: {', '.join(pcodes)}")
    except Exception:
        pass
    try:
        from lecturer_portal.models import Lecturer
        lects = list(Lecturer.objects.values_list("name", flat=True)[:80])
        lines.append(f"Lecturers: {', '.join(lects)}")
    except Exception:
        pass
    try:
        from course_allocation.models import CourseAllocation
        codes = list(
            CourseAllocation.objects.values_list("course_code", flat=True).distinct()[:120]
        )
        lines.append(f"Existing course codes: {', '.join(codes)}")
    except Exception:
        pass
    try:
        from course_allocation.models import ProgramEnrollment
        enroll = list(
            ProgramEnrollment.objects.select_related("program")
            .values("program__name", "entry_year")
            .distinct()[:60]
        )
        enroll_str = "; ".join(f"{e['program__name']} entry {e['entry_year']}" for e in enroll)
        lines.append(f"Enrolled program-cohorts: {enroll_str}")
    except Exception:
        pass
    try:
        from room_management.models import Building
        buildings = list(Building.objects.values("name", "code")[:40])
        b_str = ", ".join(f"{b['code']} ({b['name']})" for b in buildings)
        lines.append(f"Existing buildings: {b_str}")
    except Exception:
        pass
    try:
        from room_management.models import Venue
        venue_codes = list(Venue.objects.values_list("code", flat=True).distinct()[:200])
        lines.append(f"Existing venue codes: {', '.join(venue_codes)}")
    except Exception:
        pass
    try:
        from room_management.models import LabVenue
        lab_codes = list(LabVenue.objects.values_list("code", flat=True).distinct()[:100])
        lines.append(f"Existing lab venue codes: {', '.join(lab_codes)}")
    except Exception:
        pass
    return "\n".join(lines) if lines else "No existing DB data available."



# ─────────────────────────────────────────────────────────────────────────────
# Text / row extraction from uploaded files
# ─────────────────────────────────────────────────────────────────────────────

def _extract_docx_text(file_like) -> str:
    """
    Extract text from a .docx including TABLE content, in document reading
    order. python-docx's `Document.paragraphs` only sees top-level body
    paragraphs and silently skips anything inside a <w:tbl> — for documents
    like course-allocation sheets where ~100% of the actual data lives in
    Word tables, that previously meant the AI extractor received almost no
    real content (just the letterhead) and could only "find" one stray
    record, or none at all. This walks the raw body XML so tables are
    rendered as readable rows alongside any surrounding paragraph text.
    """
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
                # Word tables commonly repeat a merged cell's text across
                # every column it spans — collapse consecutive duplicate
                # cells so a one-column "group header" row doesn't show up
                # as the same string six times.
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
    """Extract raw text from uploaded file regardless of format."""
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
                for row in ws.iter_rows(max_row=500, values_only=True):
                    rows.append("\t".join(str(c) if c is not None else "" for c in row))
            return "\n".join(rows)
        except Exception:
            import pandas as pd
            dfs = pd.read_excel(io.BytesIO(data), sheet_name=None, nrows=500)
            return "\n".join(
                f"[Sheet: {k}]\n{df.to_csv(index=False)}" for k, df in dfs.items()
            )

    if name.endswith(".pdf"):
        # 100 pages is a generous bound for course-allocation style documents
        # (the previous 20-page cap silently dropped data on anything longer
        # than ~20 pages, which is exactly the size of real allocation
        # sheets). If a document is still longer than this, we say so
        # explicitly rather than quietly truncating.
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
                        f"{_PDF_PAGE_CAP} were processed. Split the file if you need the rest.]"
                    )
            return "\n".join(text_parts) or "[Empty PDF]"
        except ImportError:
            pass
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(data))
            pages_text = [p.extract_text() or "" for p in reader.pages[:_PDF_PAGE_CAP]]
            if len(reader.pages) > _PDF_PAGE_CAP:
                pages_text.append(
                    f"\n[NOTE: document has {len(reader.pages)} pages; only the first "
                    f"{_PDF_PAGE_CAP} were processed.]"
                )
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
    """
    For CSV/Excel only: read it back into row dicts using the header row,
    used by the rule-based (no-AI) path. Returns (rows, headers) or None
    if the file isn't CSV/Excel.
    """
    name = file.name.lower()
    file.seek(0)
    if name.endswith(".csv"):
        text = file.read().decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(r) for r in reader]
        return rows, reader.fieldnames or []
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
# Rule-based fallback extraction (used when no AI provider is active)
# ─────────────────────────────────────────────────────────────────────────────

def _best_schema_match(headers: list[str]) -> str | None:
    """Pick the MODEL_SCHEMA entry whose fields best match the given headers."""
    norm_headers = {h.strip().lower().replace(" ", "_") for h in headers if h}
    best_model, best_score = None, 0
    for model_name, schema in MODEL_SCHEMA.items():
        score = len(norm_headers & set(schema["fields"]))
        if score > best_score:
            best_model, best_score = model_name, score
    return best_model if best_score > 0 else None


def _rule_based_extract(file) -> dict:
    """
    Deterministic CSV/Excel → records conversion with no AI involved.
    Matches columns to the closest model schema by header name. This is
    the exact behaviour the importer falls back to whenever no AI
    provider is configured — the page must keep working either way.
    """
    sniffed = _sniff_rows_from_tabular(file)
    if sniffed is None:
        return {
            "records": [],
            "summary": (
                "No AI provider is configured, and this file format (PDF/Word) needs AI to "
                "turn free-form text into structured rows. Configure an AI provider in "
                "AI Settings, or upload a CSV/Excel file instead — those work without AI."
            ),
        }

    rows, headers = sniffed
    if not rows:
        return {"records": [], "summary": "No data rows found in the file."}

    model_name = _best_schema_match(headers)
    if not model_name:
        return {
            "records": [],
            "summary": (
                "Could not match the column headers to any known model "
                f"({', '.join(MODEL_SCHEMA.keys())}). Rename columns to match a CSV "
                "template, or configure an AI provider for smarter matching."
            ),
        }

    schema = MODEL_SCHEMA[model_name]
    norm_to_orig = {h.strip().lower().replace(" ", "_"): h for h in headers if h}
    records = []
    for row in rows:
        data = {}
        missing_required = []
        for field in schema["fields"]:
            orig_key = norm_to_orig.get(field)
            val = row.get(orig_key, "") if orig_key else ""
            data[field] = "" if val is None else str(val).strip()
        for req in schema["required"]:
            if not data.get(req):
                missing_required.append(req)
        confidence = "high" if not missing_required else "low"
        issues = [f"Missing required field: {f}" for f in missing_required]
        records.append({
            "model": model_name,
            "data": data,
            "confidence": confidence,
            "issues": issues,
        })

    return {
        "records": records,
        "summary": (
            f"No AI provider configured — used column-header matching instead. "
            f"Detected {len(records)} row(s) as '{model_name}'. Review carefully, "
            "especially rows flagged low-confidence."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# AI extraction prompts
# ─────────────────────────────────────────────────────────────────────────────

# Large documents (multi-page course allocation tables, etc.) can easily run
# to tens of thousands of characters. Sending the whole thing in one prompt
# either gets silently truncated or produces a response so large the model
# cuts it off mid-JSON. Instead we split the raw text into chunks small
# enough for one reliable round trip, extract each chunk independently, and
# merge the results. This is what actually fixes "AI only returned one row
# for a 21-page document" — the old code was hard-truncating to 12,000
# characters and only ever seeing the first fraction of the file.
_CHUNK_CHAR_BUDGET = 9000
_CHUNK_OVERLAP = 300  # small overlap so a row split across a chunk boundary isn't lost


def _chunk_text(raw_text: str, budget: int = _CHUNK_CHAR_BUDGET, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks, breaking on line boundaries where
    possible so a table row is less likely to be cut in half."""
    if len(raw_text) <= budget:
        return [raw_text]

    chunks = []
    start = 0
    n = len(raw_text)
    while start < n:
        end = min(start + budget, n)
        if end < n:
            # try to break at the last newline before the hard cutoff
            nl = raw_text.rfind("\n", start, end)
            if nl > start + budget // 2:  # only use it if it's not absurdly early
                end = nl
        chunks.append(raw_text[start:end])
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _build_extract_prompt(text_chunk: str, db_ctx: str, target_models: list[str],
                           chunk_index: int = 1, chunk_total: int = 1) -> str:
    schema_parts = "\n".join(
        f"- **{k}**: {v['description']}\n  Fields: {', '.join(v['fields'])}\n  Required: {', '.join(v['required'])}"
        for k, v in MODEL_SCHEMA.items()
        if not target_models or k in target_models
    )
    chunk_note = (
        f"\nNOTE: This is part {chunk_index} of {chunk_total} of a larger document, split "
        f"because of length. Extract every record you can find in THIS part only — do not "
        f"worry about records that might belong to other parts. If a row looks cut off at "
        f"the very start or end of this text, still extract what you can and note the "
        f"uncertainty in \"issues\".\n"
        if chunk_total > 1 else ""
    )
    return f"""You are a university data extraction expert. Analyse the document text and extract structured records.

## Available DB context (existing data to help you resolve names):
{db_ctx}

## Target model schemas:
{schema_parts}
{chunk_note}
## Document text:
{text_chunk}

## Instructions:
1. Extract ALL records you can find and map them to the appropriate model.
2. For CourseAllocation: if you see codes like COSC402_A and COSC402_B (section suffixes),
   these are the same course but different program groups — analyse the student enrollment
   context and existing programs to assign each to the correct program. Set course_code
   without the suffix (COSC402) but add a note in course_name or program field.
3. Infer year_of_study from the numeric prefix of the course code (e.g. COSC402 → Year 4).
4. For lecturers, normalise designation (Prof/Dr/Mr/Ms/Mrs).
5. If a department name is ambiguous, pick the closest match from the DB context.
6. If a row lists multiple lecturers for different sections (e.g. "Dr. A (A,B), Dr. B (C,D)"),
   create one record per lecturer/section-group rather than dropping the extras.
7. Mark each record with a "confidence" field: "high", "medium", or "low".
8. Add an "issues" field (array of strings) listing any problems or ambiguities.
9. Extract EVERY row in the text — do not stop early, do not summarise instead of listing,
   and do not skip rows because there are many of them.

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
    return f"""You are a university data assistant. The user has uploaded a document and
the system extracted these records:

{records_json}

## DB context:
{db_ctx}

## User instruction:
"{user_prompt}"

Apply the user's instruction to modify, correct, or re-classify the records.
If the user says something like "the COSC402 courses are all for BSc Computer Science Year 4",
update the program field on all matching records.
If they say "change the lecturer for COSC401 to Dr Mwangi", update accordingly.

Respond with ONLY valid JSON using the same format:
{{
  "records": [...updated records...],
  "summary": "What was changed."
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# DB commit logic
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_fk(model_label: str, field_value: str, cache: dict | None = None):
    """Try to resolve a FK by name/payroll. Returns model instance or None.

    `cache` is a dict shared across a single _commit_records() call. Without
    it, a 500-row CSV where every row shares the same department/program
    re-runs the same 5 DB queries 500 times — that repeated round-tripping
    (not file size itself, and not the network — this is all localhost) is
    what was actually causing large imports to blow past the save timeout.
    With the cache, each distinct (model, value) pair is only resolved once.
    """
    if not field_value:
        return None
    key = (model_label, field_value.strip().lower())
    if cache is not None and key in cache:
        return cache[key]

    result = None
    try:
        from django.apps import apps
        app_label, model_name = model_label.split(".")
        Model = apps.get_model(app_label, model_name)
        for lookup in ("name__iexact", "payroll_number__iexact", "code__iexact", "title__iexact"):
            try:
                result = Model.objects.get(**{lookup: field_value.strip()})
                break
            except Exception:
                pass
        if result is None:
            qs = Model.objects.filter(name__icontains=field_value.strip())
            if qs.count() == 1:
                result = qs.first()
    except Exception:
        result = None

    if cache is not None:
        cache[key] = result
    return result


@transaction.atomic
def _commit_records(records: list[dict]) -> dict:
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
    fk_cache: dict = {}

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
        label = f"Row {i+1} [{model_name}]"
        try:

            # ── Faculty ──────────────────────────────────────────────────────
            if model_name == "Faculty":
                if not data.get("name", "").strip():
                    errors.append(f"{label}: 'name' is required")
                    continue
                obj, was_created = Faculty.objects.update_or_create(
                    name__iexact=data["name"].strip(),
                    defaults={
                        "name": data["name"].strip(),
                        "description": data.get("description", ""),
                    },
                )
                (created if was_created else updated).append(f"Faculty {obj.name}")

            # ── Department ───────────────────────────────────────────────────
            elif model_name == "Department":
                if not data.get("name", "").strip():
                    errors.append(f"{label}: 'name' is required")
                    continue
                faculty = _resolve_fk("faculty_management.Faculty", data.get("faculty", ""), fk_cache)
                if not faculty:
                    errors.append(f"{label}: Faculty '{data.get('faculty')}' not found — create the Faculty first or check the spelling")
                    continue
                obj, was_created = Department.objects.update_or_create(
                    name__iexact=data["name"].strip(),
                    defaults={
                        "name": data["name"].strip(),
                        "faculty": faculty,
                        "description": data.get("description", ""),
                    },
                )
                (created if was_created else updated).append(f"Department {obj.name}")

            # ── CourseAllocation ─────────────────────────────────────────────
            elif model_name == "CourseAllocation":
                if not data.get("course_code", "").strip():
                    errors.append(f"{label}: 'course_code' is required")
                    continue
                dept = _resolve_fk("department_management.Department", data.get("department", ""), fk_cache)
                if not dept:
                    errors.append(f"{label}: Department '{data.get('department')}' not found")
                    continue
                program = _resolve_fk("program_management.Program", data.get("program", ""), fk_cache)
                lecturer = _resolve_fk("lecturer_portal.Lecturer", data.get("lecturer", ""), fk_cache)
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
                prog = _resolve_fk("program_management.Program", data.get("program", ""), fk_cache)
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
                lect = _resolve_fk("lecturer_portal.Lecturer", data.get("lecturer", ""), fk_cache)
                if not lect:
                    errors.append(f"{label}: Lecturer '{data.get('lecturer')}' not found")
                    continue
                dept = _resolve_fk("department_management.Department", data.get("department", ""), fk_cache)
                mapping, was_created = LecturerCourseMapping.objects.get_or_create(
                    lecturer=lect,
                    department=dept,
                    defaults={"notes": data.get("notes", "")},
                )
                codes = [c.strip().upper() for c in str(data.get("course_codes", "")).split(",") if c.strip()]
                if codes:
                    pcs = ProgramCourse.objects.filter(course_code__in=codes)
                    mapping.courses.add(*pcs)
                (created if was_created else updated).append(f"LecturerCourseMapping {lect.name}")

            # ── ProgramEnrollment ────────────────────────────────────────────
            elif model_name == "ProgramEnrollment":
                prog = _resolve_fk("program_management.Program", data.get("program", ""), fk_cache)
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
                        "department": _resolve_fk(
                            "department_management.Department", data.get("department", ""), fk_cache
                        ),
                    },
                )
                (created if was_created else updated).append(f"Lecturer {obj.name}")

            # ── Program ──────────────────────────────────────────────────────
            elif model_name == "Program":
                if not data.get("name", "").strip():
                    errors.append(f"{label}: 'name' is required")
                    continue
                dept = _resolve_fk("department_management.Department", data.get("department", ""), fk_cache)
                if not dept:
                    errors.append(f"{label}: Department '{data.get('department')}' not found")
                    continue
                obj, was_created = Program.objects.update_or_create(
                    name__iexact=data["name"].strip(),
                    defaults={
                        "name": data["name"].strip(),
                        "department": dept,
                        "description": data.get("description", ""),
                    },
                )
                (created if was_created else updated).append(f"Program {obj.name}")

            # ── ProgramCode ──────────────────────────────────────────────────
            elif model_name == "ProgramCode":
                if not data.get("code", "").strip():
                    errors.append(f"{label}: 'code' is required")
                    continue
                prog = _resolve_fk("program_management.Program", data.get("program", ""), fk_cache)
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
                    errors.append(f"{label}: 'name' and 'code' are both required")
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
                (created if was_created else updated).append(f"Building {obj.code} ({obj.name})")

            # ── Venue ────────────────────────────────────────────────────────
            elif model_name == "Venue":
                if not data.get("code", "").strip():
                    errors.append(f"{label}: 'code' is required")
                    continue
                building = _resolve_fk("room_management.Building", data.get("building", ""), fk_cache)
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

            # ── Unknown ──────────────────────────────────────────────────────
            else:
                errors.append(f"{label}: Unknown model '{model_name}' — check MODEL_SCHEMA in smart_importer.py")

        except Exception as exc:
            errors.append(f"{label}: {exc}")
            logger.exception("Commit error for record %s", rec)

    return {"created": created, "updated": updated, "errors": errors}



# ─────────────────────────────────────────────────────────────────────────────
# CSV template generation
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


def _parse_json_from_ai(raw: str) -> Any:
    """Parse JSON out of an AI response, tolerating markdown fences and,
    where possible, a response that got cut off mid-array (common when a
    model hits its output limit on a large extraction). If the strict
    parse fails, we try a best-effort repair that closes unterminated
    strings/arrays/objects rather than discarding the whole response.
    """
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
    """Best-effort recovery for a JSON object truncated mid-stream.

    Strategy: find the last complete top-level record object inside
    "records": [...], cut the array there, and close out the braces. This
    salvages e.g. 80 good records out of a response that got cut off
    writing the 81st, instead of losing everything.
    """
    # Find where the records array starts
    m = re.search(r'"records"\s*:\s*\[', text)
    if not m:
        return None
    arr_start = m.end()

    # Walk forward tracking brace depth within the array to find the last
    # fully-closed object boundary before the text ends or breaks.
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
    return {"records": records, "summary": "(response was truncated by the AI provider — partial results recovered)"}


# ─────────────────────────────────────────────────────────────────────────────
# Views
# ─────────────────────────────────────────────────────────────────────────────

@_sudo
def smart_import_view(request):
    """Main page — renders the importer UI. Tells the template whether AI is on."""
    return render(request, "admins/smart_import.html", {
        "models": list(MODEL_SCHEMA.keys()),
        "model_schema_json": json.dumps(
            {k: {"fields": v["fields"], "required": v["required"], "description": v["description"]}
             for k, v in MODEL_SCHEMA.items()}
        ),
        "ai_configured": ai_is_configured(),
    })


@_sudo
@require_POST
def smart_import_extract(request):
    """
    Upload file + optional model hints → extract records → return JSON preview.

    Basic mode (deterministic column-header matching) is the DEFAULT for
    this importer, even when an AI provider is configured elsewhere in the
    system — CSV/Excel already works reliably without AI, so there's no
    reason to route it through an AI call unless the user actually needs
    to. AI is only used when the user explicitly opts in for this run via
    the "use_ai" checkbox (sent as use_ai=true), which is the case for:
      - PDF/Word/TXT files, which have no rule-based path and need AI to
        turn free-form text into structured rows.
      - A CSV/Excel file where basic mode isn't cutting it (ambiguous
        columns, messy data) and the user wants AI's help instead.

    Every run writes a full transcript to admins/logs/ — what file came in,
    what text was extracted, what was sent to the AI per chunk, what came
    back, and what the final result was. This is what lets a future
    "AI got this wrong" report be diagnosed from the log alone.
    """
    uploaded = request.FILES.get("document")
    if not uploaded:
        return JsonResponse({"error": "No file uploaded."}, status=400)

    run_log = ImportRunLog(user=request.user, filename=uploaded.name, action="extract")

    target_models = [m.strip() for m in request.POST.get("target_models", "").split(",") if m.strip()]
    if target_models:
        run_log.write(target_models, label="MODEL HINTS REQUESTED")

    use_ai_requested = request.POST.get("use_ai", "").strip().lower() in ("true", "1", "yes", "on")
    client = get_ai_client() if use_ai_requested else None

    if client is None:
        # ── Basic mode: the default, whether or not AI is configured ──
        run_log.section(
            "Basic mode (rule-based)"
            + (" — AI is configured but was not requested for this run" if ai_is_configured() and not use_ai_requested else " — no AI provider active")
        )
        result = _rule_based_extract(uploaded)

        # PDF/Word/etc. have no rule-based path. If AI is available but the
        # user just hasn't ticked "Use AI" yet, say so explicitly rather
        # than repeating the generic "no AI provider configured" message,
        # which would be misleading when a provider IS active.
        if not result["records"] and ai_is_configured() and not use_ai_requested:
            unsupported_note = (
                "This file format needs AI to extract (PDF/Word aren't handled by basic "
                "mode). Check \"Use AI for this import\" above and try again."
            )
            if "PDF/Word" in result.get("summary", "") or "needs AI" in result.get("summary", ""):
                result = {**result, "summary": unsupported_note}

        run_log.write(f"Rule-based extraction produced {len(result['records'])} record(s).")
        run_log.write(result["summary"], label="SUMMARY")
        run_log.close(status="success (basic mode)", summary=result["summary"])
        return JsonResponse({
            "records": result["records"],
            "summary": result["summary"],
            "ai_used": False,
            "raw_text_preview": "",
        })

    # ── AI path (only reached when the user explicitly checked "Use AI") ──
    run_log.section("Extraction")
    try:
        raw_text = _extract_text(uploaded)
    except Exception as e:
        run_log.error(f"Text extraction raised an exception: {e}")
        run_log.close(status="failed", summary="Could not read file")
        return JsonResponse({"error": f"Could not read file: {e}"}, status=400)

    run_log.write(f"Extracted {len(raw_text)} character(s) of text from '{uploaded.name}'.")
    run_log.write(raw_text[:3000], label="EXTRACTED TEXT (first 3000 chars)")

    if not raw_text or not raw_text.strip():
        run_log.error("Extracted text was empty — nothing to send to AI.")
        run_log.close(status="failed", summary="No readable text extracted")
        return JsonResponse({"error": "No readable text could be extracted from this file."}, status=400)

    db_ctx = _db_context()
    chunks = _chunk_text(raw_text)
    run_log.write(f"Split into {len(chunks)} chunk(s) (sizes: {[len(c) for c in chunks]}).")
    run_log.write(f"AI provider: {client.get_provider_display()} | model: {client.model}")

    all_records: list[dict] = []
    summaries: list[str] = []
    chunk_errors: list[str] = []

    for idx, chunk in enumerate(chunks, start=1):
        run_log.section(f"AI Call — chunk {idx}/{len(chunks)}")
        try:
            prompt = _build_extract_prompt(chunk, db_ctx, target_models, chunk_index=idx, chunk_total=len(chunks))
            run_log.write(prompt[:4000], label="PROMPT SENT (first 4000 chars)")
            raw_response = client.call(prompt)
            run_log.write(raw_response[:4000], label="RAW AI RESPONSE (first 4000 chars)")
            parsed = _parse_json_from_ai(raw_response)
        except AIError as e:
            run_log.error(f"Chunk {idx}: AIError — {e}")
            chunk_errors.append(f"Part {idx}/{len(chunks)}: {e}")
            continue
        except json.JSONDecodeError as e:
            run_log.error(f"Chunk {idx}: AI response was not valid JSON — {e}")
            chunk_errors.append(f"Part {idx}/{len(chunks)}: AI returned invalid JSON ({e}).")
            continue
        except Exception as e:
            logger.exception("Smart import AI extraction failed on chunk %s/%s", idx, len(chunks))
            run_log.error(f"Chunk {idx}: unexpected exception — {e}")
            chunk_errors.append(f"Part {idx}/{len(chunks)}: unexpected extraction error.")
            continue

        chunk_records = parsed.get("records", [])
        run_log.write(f"Chunk {idx} produced {len(chunk_records)} record(s).")
        all_records.extend(chunk_records)
        if parsed.get("summary"):
            summaries.append(parsed["summary"] if len(chunks) == 1 else f"Part {idx}: {parsed['summary']}")

    if not all_records and chunk_errors:
        # Every chunk failed — this is a genuine error, not just "nothing found".
        error_msg = "Extraction failed for all parts of the document. " + " | ".join(chunk_errors[:3])
        run_log.close(status="failed", summary=error_msg)
        return JsonResponse({"error": error_msg}, status=502)

    summary = " ".join(summaries) if summaries else "No records were found in this document."
    if chunk_errors:
        summary += f" ⚠️ {len(chunk_errors)} of {len(chunks)} part(s) failed and were skipped — review for missing rows. " + " | ".join(chunk_errors[:3])
    if len(chunks) > 1:
        summary = f"Document was processed in {len(chunks)} parts ({len(all_records)} record(s) found). " + summary

    run_log.section("Result")
    run_log.write(f"Total records extracted: {len(all_records)}")
    run_log.write(f"Chunks failed: {len(chunk_errors)}/{len(chunks)}")
    run_log.close(
        status="success" if not chunk_errors else "partial success",
        summary=f"{len(all_records)} record(s) extracted, {len(chunk_errors)} chunk(s) failed",
    )

    return JsonResponse({
        "records": all_records,
        "summary": summary,
        "ai_used": True,
        "ai_provider": client.get_provider_display(),
        "raw_text_preview": raw_text[:1500],
        "chunks_total": len(chunks),
        "chunks_failed": len(chunk_errors),
    })


@_sudo
@require_POST
def smart_import_refine(request):
    """
    Take existing extracted records + a natural-language instruction →
    AI refines → return updated records. This step genuinely requires AI;
    if none is configured, it returns a clear error rather than silently
    doing nothing, since there is no rule-based equivalent of "apply this
    instruction".
    """
    client = get_ai_client()
    if client is None:
        return JsonResponse({
            "error": "Refine requires an AI provider. Configure one in AI Settings, "
                     "or edit the table cells directly — manual editing always works.",
        }, status=400)

    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    records = body.get("records", [])
    user_prompt = body.get("prompt", "").strip()
    if not user_prompt:
        return JsonResponse({"error": "No refinement prompt provided."}, status=400)

    run_log = ImportRunLog(user=request.user, filename="", action="refine")
    run_log.write(user_prompt, label="USER REFINE INSTRUCTION")
    run_log.write(f"Records going in: {len(records)}")
    run_log.write(f"AI provider: {client.get_provider_display()} | model: {client.model}")

    db_ctx = _db_context()
    try:
        prompt = _build_refine_prompt(json.dumps(records, indent=2), user_prompt, db_ctx)
        run_log.write(prompt[:4000], label="PROMPT SENT (first 4000 chars)")
        raw_response = client.call(prompt)
        run_log.write(raw_response[:4000], label="RAW AI RESPONSE (first 4000 chars)")
        parsed = _parse_json_from_ai(raw_response)
    except AIError as e:
        run_log.error(f"AIError — {e}")
        run_log.close(status="failed", summary=str(e))
        return JsonResponse({"error": str(e)}, status=502)
    except Exception as e:
        logger.exception("Smart import AI refine failed")
        run_log.error(f"Unexpected exception — {e}")
        run_log.close(status="failed", summary="unexpected error")
        return JsonResponse({"error": "AI refine failed. Please try again."}, status=502)

    out_records = parsed.get("records", [])
    run_log.write(f"Records coming out: {len(out_records)}")
    run_log.close(status="success", summary=f"{len(records)} → {len(out_records)} records after refine")

    return JsonResponse({
        "records": out_records,
        "summary": parsed.get("summary", ""),
    })


@_sudo
@require_POST
def smart_import_commit(request):
    """Commit the (user-approved) records to DB. Never depends on AI being configured."""
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    records = body.get("records", [])
    if not records:
        return JsonResponse({"error": "No records to commit."}, status=400)

    run_log = ImportRunLog(user=request.user, filename="", action="commit")
    run_log.write(f"Records submitted for commit: {len(records)}")
    run_log.write(records, label="RECORDS (as submitted)")

    try:
        result = _commit_records(records)
    except Exception as e:
        logger.exception("Commit failed")
        run_log.error(f"Commit raised an exception — {e}")
        run_log.close(status="failed", summary=str(e))
        return JsonResponse({"error": str(e)}, status=500)

    run_log.write(f"Created: {len(result.get('created', []))}")
    run_log.write(f"Updated: {len(result.get('updated', []))}")
    run_log.write(f"Errors:  {len(result.get('errors', []))}")
    if result.get("errors"):
        run_log.write(result["errors"], label="PER-ROW ERRORS")
    run_log.close(
        status="success" if not result.get("errors") else "partial success",
        summary=f"{len(result.get('created', []))} created, {len(result.get('updated', []))} updated, {len(result.get('errors', []))} errors",
    )

    return JsonResponse(result)


@_sudo
def smart_import_csv(request):
    """Return a downloadable CSV template for the requested model."""
    model_name = request.GET.get("model", "")
    if model_name not in MODEL_SCHEMA:
        return HttpResponse("Invalid model.", status=400)
    csv_data = _generate_csv(model_name)
    resp = HttpResponse(csv_data, content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{model_name}_template.csv"'
    return resp