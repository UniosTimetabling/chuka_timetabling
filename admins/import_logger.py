"""
admins/import_logger.py
========================
Writes one human-readable .txt transcript per Smart Importer run to
admins/logs/, so that "the AI got this wrong" can be debugged later
from the log alone — without asking the user to re-upload the original
file. This is the exact gap that made the table-extraction bug
(python-docx silently skipping tables) take a live file + manual
investigation to diagnose; a transcript would have shown the problem
in seconds: "extracted text length: 1041 chars" next to a 21-page
upload is an immediate red flag.

This is deliberately separate from the project's existing rotating
app/error/security logs (see LOGGING in settings.py) — those capture
aggregated operational health across the whole app. This captures the
full, readable story of ONE import end-to-end: what came in, what was
extracted, what was sent to the AI (or why AI was skipped), what came
back, and what was written to the DB. Mirrors the project's existing
convention of per-run timestamped .txt transcripts (see
timetable/algorithms/logs/) as opposed to rotating aggregated logs.

USAGE
-----
    from admins.import_logger import ImportRunLog

    run_log = ImportRunLog(user=request.user, filename=uploaded.name)
    run_log.section("Extraction")
    run_log.write(f"Extracted {len(raw_text)} chars")
    ...
    run_log.section("AI Call — chunk 1/4")
    run_log.write(prompt, label="PROMPT SENT")
    run_log.write(raw_response, label="RAW AI RESPONSE")
    ...
    run_log.close(status="success", summary="4 records committed")

Each call is also written immediately to disk (not buffered until the
end), so even if the request crashes partway through, the transcript
up to that point is still on disk for debugging.

SECURITY: API keys are never available to this module (smart_importer.py
never passes them in) — but as defense in depth, write() runs a best-
effort redaction over anything that looks like a credential before it
ever touches disk, the same pattern used for sanitising provider error
messages in core/ai_registry.py.
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent / "logs"

try:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # Read-only filesystem / permissions issue in this environment —
    # fall back to /tmp so the importer keeps working even if logging
    # to the repo can't happen. Mirrors the same fallback pattern used
    # for LOGS_DIR in settings.py.
    LOGS_DIR = Path(tempfile.gettempdir()) / "smart_importer_logs"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Same redaction pattern used in core/ai_registry.py's HTTP error
# sanitiser — defense in depth in case a prompt or response ever
# contains something that looks like a credential.
_CREDENTIAL_PATTERN = re.compile(r"(key|token|authorization)([\"']?\s*[:=]\s*[\"']?)[\w\-\.]{8,}", re.I)
_BEARER_PATTERN = re.compile(r"\bBearer\s+[\w\-\.]{8,}", re.I)


def _redact(text: str) -> str:
    if not text:
        return text
    text = _CREDENTIAL_PATTERN.sub(r"\1\2<redacted>", text)
    text = _BEARER_PATTERN.sub("Bearer <redacted>", text)
    return text


class ImportRunLog:
    """One transcript file per Smart Importer run (extract, refine, or commit)."""

    def __init__(self, *, user=None, filename: str = "", action: str = "extract"):
        self.started_at = datetime.now()
        run_id = uuid.uuid4().hex[:8]
        stamp = self.started_at.strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r"[^\w\.\-]", "_", filename)[:60] if filename else "norfile"
        self.path = LOGS_DIR / f"{stamp}_{action}_{safe_name}_{run_id}.txt"
        self._closed = False

        username = getattr(user, "username", None) or "unknown"
        header = (
            f"{'=' * 78}\n"
            f"SMART IMPORTER — RUN LOG\n"
            f"{'=' * 78}\n"
            f"Started:  {self.started_at.isoformat()}\n"
            f"User:     {username}\n"
            f"Action:   {action}\n"
            f"File:     {filename or '(none)'}\n"
            f"{'=' * 78}\n\n"
        )
        self._append(header)

    def _append(self, text: str):
        if self._closed:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            # Logging must never break the actual import — if disk write
            # fails for any reason, silently drop it rather than 500 the
            # request the user is waiting on.
            pass

    def section(self, title: str):
        """Mark a new phase of the run (e.g. 'Extraction', 'AI Call — chunk 2/4')."""
        self._append(f"\n--- {title} {'-' * max(0, 60 - len(title))}\n")

    def write(self, content, label: str = ""):
        """Log a piece of content (text, dict, list — anything str()-able)."""
        if not isinstance(content, str):
            import json
            try:
                content = json.dumps(content, indent=2, default=str)
            except Exception:
                content = str(content)
        content = _redact(content)
        prefix = f"[{label}]\n" if label else ""
        self._append(f"{prefix}{content}\n")

    def error(self, message: str):
        self._append(f"\n!!! ERROR: {_redact(message)}\n")

    def close(self, status: str = "success", summary: str = ""):
        """Finalize the transcript. Safe to call multiple times (no-op after first)."""
        if self._closed:
            return
        duration = (datetime.now() - self.started_at).total_seconds()
        footer = (
            f"\n{'=' * 78}\n"
            f"Finished: {datetime.now().isoformat()}\n"
            f"Duration: {duration:.2f}s\n"
            f"Status:   {status}\n"
            f"Summary:  {summary}\n"
            f"{'=' * 78}\n"
        )
        self._append(footer)
        self._closed = True


def list_recent_logs(limit: int = 50) -> list[Path]:
    """Return the most recent transcript files, newest first."""
    if not LOGS_DIR.exists():
        return []
    files = sorted(LOGS_DIR.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]
