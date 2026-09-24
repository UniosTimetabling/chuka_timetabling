"""
Feedback Module Action Logger
=============================
Logs every action performed in the feedback module to individual .txt files
inside feedback/logs/.

Each log file is named after the action type and date, e.g.:
  - feedback/logs/update_status_2026-06-26.txt
  - feedback/logs/export_feedbacks_2026-06-26.txt
  - feedback/logs/export_pdf_2026-06-26.txt
  - feedback/logs/chat_query_2026-06-26.txt

Each log entry records:
  - Timestamp
  - Action type
  - What was RECEIVED  (inputs / request data)
  - How it was HANDLED (processing steps / decisions)
  - What was RETURNED  (response / result sent to user)
"""

import os
import json
import traceback
from datetime import datetime
from django.conf import settings


# ── Resolve the logs directory ────────────────────────────────────────────────
_FEEDBACK_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(_FEEDBACK_DIR, "logs")

# Ensure the directory exists at import time
os.makedirs(LOGS_DIR, exist_ok=True)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _log_file_path(action_name: str) -> str:
    """Return the path for today's log file for a given action."""
    today = datetime.now().strftime("%Y-%m-%d")
    safe_name = action_name.replace(" ", "_").lower()
    return os.path.join(LOGS_DIR, f"{safe_name}_{today}.txt")


def _format_value(value) -> str:
    """Safely convert any value to a readable string."""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, indent=4, default=str)
        except Exception:
            return str(value)
    return str(value)


def _write_entry(action_name: str, received: dict, handled: dict, returned: dict) -> None:
    """
    Append a single structured log entry to the appropriate .txt file.

    Parameters
    ----------
    action_name : str
        Short identifier for the action, e.g. 'update_status' or 'chat_query'.
    received : dict
        Key/value pairs describing what the action received as input.
    handled : dict
        Key/value pairs describing how the action processed the input.
    returned : dict
        Key/value pairs describing what was sent back to the user/client.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    separator = "=" * 80

    lines = [
        "",
        separator,
        f"TIMESTAMP : {timestamp}",
        f"ACTION    : {action_name.upper()}",
        separator,
        "",
        "── RECEIVED ─────────────────────────────────────────────────────────────────",
    ]
    for key, value in received.items():
        lines.append(f"  {key}: {_format_value(value)}")

    lines += [
        "",
        "── HANDLED ──────────────────────────────────────────────────────────────────",
    ]
    for key, value in handled.items():
        lines.append(f"  {key}: {_format_value(value)}")

    lines += [
        "",
        "── RETURNED TO USER ─────────────────────────────────────────────────────────",
    ]
    for key, value in returned.items():
        lines.append(f"  {key}: {_format_value(value)}")

    lines.append("")

    log_path = _log_file_path(action_name)
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        # Never let logging crash the main application
        import logging as _logging
        _logging.getLogger(__name__).error(
            "FeedbackLogger: could not write to %s — %s", log_path, exc
        )


# ── Public API ────────────────────────────────────────────────────────────────

def log_update_status(request_data: dict, action: str, result: dict) -> None:
    """
    Log a feedback status update action.

    Parameters
    ----------
    request_data : dict  — raw parsed request body
    action       : str   — 'individual', 'bulk', or 'mark_all_seen'
    result       : dict  — the JsonResponse payload returned to the client
    """
    received = {
        "action_type": action,
        "request_body": request_data,
    }

    # Derive a human-readable summary of what was handled
    if action == "individual":
        handled = {
            "operation": "Update single feedback status",
            "feedback_id": request_data.get("feedback_id"),
            "new_status": request_data.get("status"),
            "seen_flagged": True,
        }
    elif action == "bulk":
        handled = {
            "operation": "Bulk update feedback statuses",
            "feedback_ids": request_data.get("feedback_ids", []),
            "count": len(request_data.get("feedback_ids", [])),
            "new_status": request_data.get("status"),
        }
    elif action == "mark_all_seen":
        handled = {
            "operation": "Mark all filtered feedbacks as seen",
            "filters_applied": {
                "years": request_data.get("selected_years", []),
                "months": request_data.get("selected_months", []),
                "search_query": request_data.get("search_query", ""),
                "date_range": request_data.get("date_range", ""),
            },
        }
    else:
        handled = {"operation": "Unknown action", "raw": request_data}

    returned = {
        "success": result.get("success"),
        "message": result.get("message") or result.get("error"),
        "count_updated": result.get("count"),
    }

    _write_entry("update_status", received, handled, returned)


def log_export_feedbacks(filters: dict, total_count: int) -> None:
    """
    Log a feedback export (HTML listing) action.

    Parameters
    ----------
    filters     : dict — filter parameters used in the query
    total_count : int  — number of feedbacks returned after filtering
    """
    received = {
        "search_query": filters.get("search_query", ""),
        "selected_years": filters.get("selected_years", []),
        "selected_months": filters.get("selected_months", []),
        "date_range": filters.get("date_range", ""),
        "selected_ids": filters.get("selected_ids", []),
        "page": filters.get("page", 1),
    }

    handled = {
        "operation": "Filter and paginate feedbacks for export listing",
        "filters_active": [k for k, v in received.items() if v and k != "page"],
        "paginate_by": 50,
    }

    returned = {
        "template": "feedback/export_feedback.html",
        "total_records_shown": total_count,
        "response_type": "HTML page render",
    }

    _write_entry("export_feedbacks", received, handled, returned)


def log_export_pdf(filters: dict, record_count: int, filename: str, success: bool,
                   error: str = None) -> None:
    """
    Log a PDF export action.

    Parameters
    ----------
    filters      : dict — filter parameters used
    record_count : int  — number of feedback records included
    filename     : str  — PDF filename returned to the client
    success      : bool — whether the PDF was generated successfully
    error        : str  — error message if generation failed
    """
    received = {
        "selected_years": filters.get("selected_years", []),
        "selected_months": filters.get("selected_months", []),
        "search_query": filters.get("search_query", ""),
        "date_range": filters.get("date_range", ""),
        "selected_ids": filters.get("selected_ids", []),
    }

    handled = {
        "operation": "Generate PDF report using ReportLab",
        "template_used": "TimetablePdfTemplate (university header/footer)",
        "records_processed": record_count,
        "detailed_section_included": record_count <= 20,
    }

    if success:
        returned = {
            "response_type": "application/pdf attachment",
            "filename": filename,
            "record_count": record_count,
            "success": True,
        }
    else:
        returned = {
            "response_type": "HTTP 500 error response",
            "success": False,
            "error": error,
        }

    _write_entry("export_pdf", received, handled, returned)


def log_chat_query(user_message: str, intent: str, handler_used: str,
                   ai_used: bool, response_text: str, error: str = None) -> None:
    """
    Log a chatbot query action from chat_view.py.

    Parameters
    ----------
    user_message  : str  — the raw message the user sent
    intent        : str  — detected intent / category (e.g. 'timetable', 'course_info')
    handler_used  : str  — which handler/branch processed the query
    ai_used       : bool — whether the AI provider was invoked
    response_text : str  — the final reply sent back to the user (truncated to 500 chars)
    error         : str  — error message if the query failed
    """
    received = {
        "user_message": user_message[:500],  # cap at 500 chars for readability
        "message_length": len(user_message),
    }

    handled = {
        "intent_detected": intent,
        "handler_branch": handler_used,
        "ai_provider_invoked": ai_used,
        "error_encountered": error,
    }

    returned = {
        "response_preview": (response_text or "")[:500],
        "response_length": len(response_text or ""),
        "response_type": "JSON chat reply" if not error else "JSON error reply",
    }

    _write_entry("chat_query", received, handled, returned)


def log_feedback_submit(full_name: str, email: str, admission_number: str,
                        message_length: int, saved: bool, error: str = None) -> None:
    """
    Log a new feedback submission action.

    Parameters
    ----------
    full_name        : str  — submitter's name
    email            : str  — submitter's email
    admission_number : str  — admission number (may be None/empty)
    message_length   : int  — character count of the submitted message
    saved            : bool — whether the record was persisted to the DB
    error            : str  — error message if save failed
    """
    received = {
        "full_name": full_name,
        "email": email,
        "admission_number": admission_number or "N/A",
        "message_length_chars": message_length,
    }

    handled = {
        "operation": "Validate and persist new Feedback record",
        "validation_passed": saved,
        "db_save_attempted": True,
    }

    returned = {
        "saved_to_db": saved,
        "response_type": "JSON success/failure reply",
        "error": error,
    }

    _write_entry("feedback_submit", received, handled, returned)
