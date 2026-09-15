"""
allocation_reports/signature.py
=================================
Detects "has anything changed since the last PDF for this department/scope
was generated?" without relying on an `updated_at` column (CourseAllocation
has none). We fingerprint the actual visible content of every row — course
code, name, lecturer, student count, program, year, semester, intake,
elective/stem tags, and merged-group membership — into one SHA-256 hash.
If that hash matches the hash stored on the current AllocationPdfRun, the
existing PDF is still accurate and nothing is regenerated.
"""
import hashlib

from allocation_reports.adapters import fetch_rows, fetch_serviced_rows


def compute_signature(scope, department, campus=None):
    """Return (signature_hex, row_count) for the given scope/department[/campus].

    row_count and the hash both include the "Serviced Courses" rows (courses
    this department teaches for another department's program), not just its
    own allocations — otherwise a change that only affects the serviced
    section (e.g. a new course serviced for another department, or nothing
    of its own yet) would never trigger a regeneration.
    """
    rows = fetch_rows(scope, department, campus=campus)
    serviced_rows = fetch_serviced_rows(scope, department, campus=campus)
    if not rows and not serviced_rows:
        return "", 0

    parts = []
    for r in rows:
        parts.append(
            "|".join([
                r["course_code"],
                r["course_name"],
                r["origin_department"],
                r["lecturer"],
                str(r["students"]),
                r["program"],
                str(r["year"]),
                str(r.get("semester")),
                str(r.get("intake")),
                str(r.get("is_elective")),
                str(r.get("stem")),
                str(r.get("stem_category")),
                str(r.get("selection_group")),
                ",".join(sorted(r.get("merged_with") or [])),
                str(r.get("student_group")),
            ])
        )
    for r in serviced_rows:
        parts.append(
            "|".join([
                "SERVICED",
                r["course_code"],
                r["course_name"],
                r["allocating_department"],
                r["lecturer"],
                str(r["students"]),
                r["program"],
                str(r["year"]),
                str(r.get("semester")),
                str(r.get("intake")),
            ])
        )
    parts.sort()  # order-independent — reordering rows shouldn't force a regenerate
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest, len(rows) + len(serviced_rows)
