"""
notifications/notification_apis.py
====================================
Chuka University Timetable Management System

URLs (unchanged):
    path('api/notifications/',       notification_apis.get_notifications,      name='get_notifications'),
    path('api/notifications/read/',  notification_apis.mark_notification_read, name='mark_notification_read'),

THE FIX — why you were seeing nothing before:
  ✗ OLD: notifications stored as target_user=specific_user
          This only worked if _get_group_users() found your exact account,
          which failed silently when group names didn't match.
  ✓ NEW: DVC + Timetabling notifications stored as target_GROUP
          One record per Group object → every member of that group sees it
          automatically via NotificationManager.for_user().
          BOTH "Director Timetable" AND "director_timetable" are handled
          because we loop over all matching Group objects.

  COD stays as target_user=dept.leader (personalised per department).

Group names matched (exact from your Django admin):
    dvc, dvc_admins
    cod, cod_admins
    Director Timetable, Timetable Admins, director_timetable, timetable_admins
"""

from __future__ import annotations

import io
import logging
from collections import defaultdict
from datetime import datetime

from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from notifications.models import Notification
from course_allocation.models import SubmissionControl
from django.db.models import Q
import json

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — group names must match Django admin exactly
# ─────────────────────────────────────────────────────────────────────────────

PDF_THRESHOLD = 8

DVC_GROUPS = ["dvc", "dvc_admins"]
COD_GROUPS = ["cod", "cod_admins"]
# Both director variants covered — "Director Timetable" (display) + "director_timetable" (slug)
TT_GROUPS  = ["Director Timetable", "Timetable Admins",
               "director_timetable", "timetable_admins"]

COLORS = {
    "critical": "#C0392B",
    "warning":  "#D68910",
    "info":     "#1A5276",
    "success":  "#1E8449",
    "header":   "#1A237E",
    "alt_row":  "#EBF5FB",
}

# ─────────────────────────────────────────────────────────────────────────────
# PDF SUPPORT (reportlab — optional, degrades gracefully)
# ─────────────────────────────────────────────────────────────────────────────

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer,
        Table, TableStyle, HRFlowable,
    )
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False
    logger.warning("reportlab not installed — PDF generation disabled. Run: pip install reportlab")


def _rl_color(hex_str):
    return rl_colors.HexColor(hex_str)


def _pdf_doc(buf):
    return SimpleDocTemplate(buf, pagesize=A4,
                              rightMargin=2*cm, leftMargin=2*cm,
                              topMargin=2*cm,   bottomMargin=2*cm)


def _pdf_header(title, subtitle, color_hex):
    if not REPORTLAB_OK:
        return []
    st    = getSampleStyleSheet()
    uni_s = ParagraphStyle("u",  parent=st["Normal"], fontSize=13,
                            fontName="Helvetica-Bold",
                            textColor=_rl_color(COLORS["header"]),
                            alignment=TA_CENTER, spaceAfter=1)
    sys_s = ParagraphStyle("s",  parent=st["Normal"], fontSize=9,
                            textColor=_rl_color(COLORS["header"]),
                            alignment=TA_CENTER, spaceAfter=2)
    tit_s = ParagraphStyle("t",  parent=st["Normal"], fontSize=11,
                            fontName="Helvetica-Bold",
                            textColor=_rl_color(color_hex),
                            alignment=TA_CENTER, spaceAfter=2)
    sub_s = ParagraphStyle("sb", parent=st["Normal"], fontSize=8,
                            textColor=_rl_color("#555555"),
                            alignment=TA_CENTER, spaceAfter=4)
    return [
        Paragraph("CHUKA UNIVERSITY", uni_s),
        Paragraph("TIMETABLE MANAGEMENT SYSTEM", sys_s),
        HRFlowable(width="100%", thickness=2, color=_rl_color(color_hex), spaceAfter=4),
        Paragraph(title, tit_s),
        Paragraph(subtitle, sub_s),
        Paragraph(f"Generated: {datetime.now().strftime('%d %B %Y  %H:%M')}", sub_s),
        HRFlowable(width="100%", thickness=0.4, color=rl_colors.lightgrey, spaceAfter=6),
        Spacer(1, 0.3*cm),
    ]


def _sec_head(text, color_hex):
    if not REPORTLAB_OK:
        return []
    st = getSampleStyleSheet()
    s  = ParagraphStyle("sh", parent=st["Normal"], fontSize=10,
                         fontName="Helvetica-Bold",
                         textColor=_rl_color(color_hex),
                         spaceBefore=8, spaceAfter=3)
    return [Paragraph(text, s),
            HRFlowable(width="100%", thickness=0.4, color=_rl_color(color_hex), spaceAfter=3)]


def _pdf_table(rows, col_widths, hdr_color):
    if not REPORTLAB_OK:
        return None
    st   = getSampleStyleSheet()
    cell = ParagraphStyle("c", parent=st["Normal"], fontSize=7.5, leading=9)
    hdr  = ParagraphStyle("h", parent=st["Normal"], fontSize=8,
                           fontName="Helvetica-Bold",
                           textColor=rl_colors.white, leading=10)
    fmt = []
    for ri, row in enumerate(rows):
        fmt.append([Paragraph(str(c or ""), hdr if ri == 0 else cell) for c in row])
    t = Table(fmt, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0),  _rl_color(hdr_color)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [rl_colors.white, _rl_color(COLORS["alt_row"])]),
        ("GRID",           (0, 0), (-1, -1), 0.3, rl_colors.lightgrey),
        ("VALIGN",         (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",     (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 3),
        ("LEFTPADDING",    (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 4),
    ]))
    return t


def _save_pdf(buf, slug):
    buf.seek(0)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"notifications/{slug}_{ts}.pdf"
    path = default_storage.save(name, ContentFile(buf.read()))
    try:
        from django.conf import settings
        return f"{settings.MEDIA_URL}{path}"
    except Exception:
        return f"/media/{path}"


def _alloc_table(allocs, hdr_color):
    rows = [["#", "Code", "Course Name", "Department", "Program", "Lecturer"]]
    for i, a in enumerate(allocs, 1):
        rows.append([
            str(i),
            a.course_code or "",
            a.course_name or "",
            a.department.name if a.department else "N/A",
            a.program.name    if a.program    else "N/A",
            a.lecturer.display_name if a.lecturer else "⚠ Unassigned",
        ])
    return _pdf_table(rows, [0.7*cm, 2.5*cm, 5*cm, 3.5*cm, 3.5*cm, 3.5*cm], hdr_color)


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION STORAGE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _dedup_key(message):
    """Extract hidden ||key|| marker from end of stored message."""
    if not message or "||" not in message:
        return None
    end   = message.rfind("||")
    start = message.rfind("||", 0, end)
    if start == -1 or start == end:
        return None
    return message[start + 2: end]


def _clean(message):
    """Strip hidden ||key|| before sending to frontend."""
    if "||" in message:
        idx   = message.rfind("||")
        start = message.rfind("||", 0, idx)
        if start != -1 and start != idx:
            return message[:start].rstrip()
    return message


def _linkify_message(message, request):
    """Return an HTML-safe string where URLs (absolute or media-relative) are turned into clickable links.
    Also ensures media/relative links are converted to absolute using request.build_absolute_uri.
    """
    if not message:
        return ""
    import html
    escaped = html.escape(message)

    # Replace absolute http/https URLs
    import re
    def repl_abs(m):
        url = m.group(0)
        return f'<a href="{url}" target="_blank" rel="noopener">{url}</a>'

    # Convert /media/... or relative paths starting with / to absolute
    def repl_rel(m):
        path = m.group(0)
        abs_url = request.build_absolute_uri(path)
        return f'<a href="{abs_url}" target="_blank" rel="noopener">{abs_url}</a>'

    # First linkify full http(s)
    escaped = re.sub(r'https?://[^\s<>]+', repl_abs, escaped)
    # Then linkify common root-relative paths (e.g. /media/...) to absolute URLs
    escaped = re.sub(r'(/media/[^\s<>"\']+)', repl_rel, escaped)
    return escaped


# ── GROUP-based helpers (DVC, Timetabling) ───────────────────────────────────

def _upsert_group(group_obj, message, key):
    """
    Create or update ONE notification record for a Group.
    All members of that group see it via NotificationManager.for_user().
    """
    marker   = f"||{key}||"
    full_msg = f"{message}{marker}"
    existing = Notification.objects.filter(
        target_group=group_obj,
        is_read=False,
        message__endswith=marker,
    ).first()
    if existing:
        if existing.message != full_msg:
            existing.message    = full_msg
            existing.created_at = timezone.now()
            existing.save(update_fields=["message", "created_at"])
    else:
        Notification.objects.create(target_group=group_obj, message=full_msg)


def _upsert_groups(group_name_list, message, key):
    """
    Upsert for every Group matching the name list.
    Handles both 'Director Timetable' and 'director_timetable' in one call.
    """
    for grp in Group.objects.filter(name__in=group_name_list):
        _upsert_group(grp, message, key)


def _purge_groups(group_name_list, key):
    """Delete all group-targeted notifications with this dedup key."""
    Notification.objects.filter(
        target_group__name__in=group_name_list,
        message__endswith=f"||{key}||",
    ).delete()


def _any_group_exists(group_name_list):
    """True if at least one of the named groups exists in DB."""
    return Group.objects.filter(name__in=group_name_list).exists()


# ── USER-based helpers (COD — personalised per dept leader) ──────────────────

def _upsert_user(user, message, key):
    """Create or update notification for a specific user."""
    marker   = f"||{key}||"
    full_msg = f"{message}{marker}"
    existing = Notification.objects.filter(
        target_user=user,
        is_read=False,
        message__endswith=marker,
    ).first()
    if existing:
        if existing.message != full_msg:
            existing.message    = full_msg
            existing.created_at = timezone.now()
            existing.save(update_fields=["message", "created_at"])
    else:
        Notification.objects.create(target_user=user, message=full_msg)


def _purge_user(user, key):
    """Delete user-targeted notifications with this dedup key."""
    Notification.objects.filter(
        target_user=user,
        message__endswith=f"||{key}||",
    ).delete()


# ─────────────────────────────────────────────────────────────────────────────
# GENERATOR 1 — DVC  (target_group)
# ─────────────────────────────────────────────────────────────────────────────

def _gen_dvc():
    from course_allocation.models import CourseAllocation

    if not _any_group_exists(DVC_GROUPS):
        return

    pending = list(
        CourseAllocation.objects.filter(
            approved_by_dvc=False,
            rejected_by_dvc=False,
        ).select_related("department", "program", "lecturer")
    )

    KEY = "dvc_pending"

    if not pending:
        _purge_groups(DVC_GROUPS, KEY)
        _upsert_groups(DVC_GROUPS,
                       "✅ All course allocations have been reviewed. No pending approvals.",
                       "dvc_all_clear")
        return

    _purge_groups(DVC_GROUPS, "dvc_all_clear")
    count = len(pending)

    if count <= PDF_THRESHOLD:
        lines = [f"⚠️ {count} course allocation(s) pending your approval:\n"]
        for ca in pending:
            dept = ca.department.name if ca.department else "N/A"
            prog = ca.program.name    if ca.program    else "N/A"
            lect = ca.lecturer.display_name if ca.lecturer else "Unassigned"
            lines.append(f"  • [{ca.course_code}] {ca.course_name} | {dept} | {prog} | {lect}")
        _upsert_groups(DVC_GROUPS, "\n".join(lines), KEY)
    else:
        pdf_url = _dvc_pdf(pending)
        _upsert_groups(DVC_GROUPS,
                       f"⚠️ {count} course allocations awaiting DVC approval.\nDownload report: {pdf_url}",
                       KEY)


def _dvc_pdf(pending):
    if not REPORTLAB_OK:
        return "#"
    buf   = io.BytesIO()
    doc   = _pdf_doc(buf)
    story = _pdf_header("PENDING COURSE ALLOCATIONS — DVC APPROVAL",
                         f"Total pending: {len(pending)}", COLORS["critical"])
    rows  = [["#", "Code", "Course Name", "Department", "Program", "Lecturer"]]
    for i, ca in enumerate(pending, 1):
        rows.append([str(i), ca.course_code or "", ca.course_name or "",
                     ca.department.name if ca.department else "N/A",
                     ca.program.name    if ca.program    else "N/A",
                     ca.lecturer.display_name if ca.lecturer else "Unassigned"])
    story.append(_pdf_table(rows, [0.7*cm, 2.5*cm, 5.5*cm, 3.5*cm, 3.5*cm, 3.5*cm], COLORS["critical"]))
    doc.build(story)
    return _save_pdf(buf, "dvc_pending")


# ─────────────────────────────────────────────────────────────────────────────
# GENERATOR 2 — COD  (target_user = dept.leader)
# ─────────────────────────────────────────────────────────────────────────────

def _gen_cod():
    from department_management.models import Department
    from course_allocation.models import CourseAllocation
    from program_management.models import ProgramCourse
    from course_allocation.config_helpers import strip_course_code_tag

    for dept in Department.objects.select_related("faculty").prefetch_related("programs"):
        cod = dept.leader
        if not cod:
            continue

        allocs = list(
            CourseAllocation.objects.filter(department=dept)
            .select_related("program", "lecturer")
        )

        # detect dominant semester
        sem_cnt = defaultdict(int)
        for a in allocs:
            pc = ProgramCourse.objects.filter(
                program=a.program,
                course_code__iexact=strip_course_code_tag(a.course_code)).first()
            if pc:
                sem_cnt[pc.semester] += 1
        dom_sem = max(sem_cnt, key=sem_cnt.get) if sem_cnt else None

        issues = []

        def _chk(condition_list, key_suffix, severity, label):
            k = f"cod_{dept.id}_{key_suffix}"
            if condition_list:
                issues.append({"key": k, "severity": severity,
                                "label": label, "items": condition_list})
            else:
                _purge_user(cod, k)

        _chk([a for a in allocs if not a.lecturer_id],
             "unallocated", "critical", "No Lecturer Assigned")

        _chk([a for a in allocs if not a.approved_by_dvc and not a.rejected_by_dvc],
             "not_dvc", "warning", "Not Yet Submitted / Approved by DVC")

        _chk([a for a in allocs if a.approved_by_dvc and not a.submitted_to_tt],
             "approved_not_tt", "warning", "Approved but NOT Forwarded to Timetable")

        if dom_sem:
            wrong = []
            for a in allocs:
                pc = ProgramCourse.objects.filter(
                    program=a.program,
                    course_code__iexact=strip_course_code_tag(a.course_code)).first()
                if pc and pc.semester != dom_sem:
                    wrong.append(a)
            _chk(wrong, "wrong_sem", "warning",
                 f"Wrong Semester (dominant semester is {dom_sem})")

            allocated_codes = {strip_course_code_tag(a.course_code).upper() for a in allocs}
            missing = [
                pc
                for prog in dept.programs.all()
                for pc in ProgramCourse.objects.filter(program=prog, semester=dom_sem)
                if pc.course_code.upper() not in allocated_codes
            ]
            _chk(missing, "missing", "critical",
                 f"Courses in ProgramCourse NOT Allocated (Semester {dom_sem})")

        if not issues:
            _purge_user(cod, f"cod_{dept.id}_issues")
            _upsert_user(cod,
                         f"✅ [{dept.name}] All course allocations are in order.",
                         f"cod_{dept.id}_clear")
            continue

        _purge_user(cod, f"cod_{dept.id}_clear")
        total = sum(len(i["items"]) for i in issues)

        if total <= PDF_THRESHOLD:
            lines = [f"⚠️ [{dept.name}] Course Allocation Issues (Semester {dom_sem}):\n"]
            for iss in issues:
                lines.append(f"\n🔸 {iss['label']} ({len(iss['items'])} course(s)):")
                for item in iss["items"][:5]:
                    code = getattr(item, "course_code", str(item))
                    name = getattr(item, "course_name", "")
                    lines.append(f"    • {code} – {name}")
                if len(iss["items"]) > 5:
                    lines.append(f"    … and {len(iss['items']) - 5} more.")
            _upsert_user(cod, "\n".join(lines), f"cod_{dept.id}_issues")
        else:
            pdf_url = _cod_pdf(dept, issues, dom_sem)
            _upsert_user(cod,
                         (f"⚠️ [{dept.name}] {total} issue(s) found.\n"
                          f"Download full report: {pdf_url}"),
                         f"cod_{dept.id}_issues")


def _cod_pdf(dept, issues, dom_sem):
    if not REPORTLAB_OK:
        return "#"
    buf   = io.BytesIO()
    doc   = _pdf_doc(buf)
    fac   = dept.faculty.name if dept.faculty else "N/A"
    total = sum(len(i["items"]) for i in issues)
    story = _pdf_header(f"COURSE ALLOCATION ISSUES — {dept.name.upper()}",
                         f"Faculty: {fac}  |  Semester: {dom_sem or 'N/A'}  |  Issues: {total}",
                         COLORS["critical"])
    SEV = {"critical": COLORS["critical"], "warning": COLORS["warning"], "info": COLORS["info"]}
    for iss in issues:
        clr = SEV.get(iss["severity"], COLORS["info"])
        story += _sec_head(f"▶  {iss['label']}  ({len(iss['items'])} item(s))", clr)
        rows = [["#", "Code", "Course Name", "Program", "Lecturer / Note"]]
        for idx, item in enumerate(iss["items"], 1):
            code = getattr(item, "course_code", str(item))
            name = getattr(item, "course_name", "")
            prog = item.program.name if getattr(item, "program", None) else ""
            note = (item.lecturer.display_name if item.lecturer else "NO LECTURER") \
                   if hasattr(item, "lecturer") else ""
            rows.append([str(idx), code, name, prog, note])
        story.append(_pdf_table(rows, [0.7*cm, 2.5*cm, 5.5*cm, 4*cm, 4*cm], clr))
        story.append(Spacer(1, 0.4*cm))
    doc.build(story)
    return _save_pdf(buf, f"cod_{dept.id}_issues")


# ─────────────────────────────────────────────────────────────────────────────
# GENERATOR 3 — TIMETABLING  (target_group)
# ─────────────────────────────────────────────────────────────────────────────

def _gen_timetable():
    if not _any_group_exists(TT_GROUPS):
        return

    data  = _collect_tt_data()
    total = (len(data["unscheduled_tt"]) + len(data["unscheduled_exam"])
             + len(data["tt_conflicts"]) + len(data["exam_conflicts"])
             + len(data["unassigned"]))

    KEY = "tt_issues"

    if total == 0:
        _purge_groups(TT_GROUPS, KEY)
        _purge_groups(TT_GROUPS, "tt_issues_pdf")
        _upsert_groups(TT_GROUPS,
                       "✅ Timetable: No unscheduled courses, no conflicts, all lecturers assigned.",
                       "tt_all_clear")
        return

    _purge_groups(TT_GROUPS, "tt_all_clear")

    if total <= PDF_THRESHOLD:
        _upsert_groups(TT_GROUPS, _tt_inline(data), KEY)
    else:
        pdf_url = _tt_pdf(data)
        _upsert_groups(TT_GROUPS,
                       (f"⚠️ Timetable System: {total} issue(s) — "
                        f"{len(data['tt_conflicts'])} TT conflicts, "
                        f"{len(data['exam_conflicts'])} exam conflicts, "
                        f"{len(data['unscheduled_tt'])} unscheduled (TT), "
                        f"{len(data['unscheduled_exam'])} unscheduled (exam), "
                        f"{len(data['unassigned'])} unassigned.\n"
                        f"Download report: {pdf_url}"),
                       "tt_issues_pdf")


def _collect_tt_data():
    from course_allocation.models import CourseAllocation

    result = dict(unscheduled_tt=[], unscheduled_exam=[],
                  tt_conflicts=[], exam_conflicts=[], unassigned=[])
    scheduled_tt_ids   = set()
    scheduled_exam_ids = set()

    try:
        from timetable.models import Timetable
        scheduled_tt_ids       = set(Timetable.objects
                                      .values_list("course_allocation_id", flat=True).distinct())
        result["tt_conflicts"] = _collisions_tt(Timetable)
    except Exception as e:
        logger.warning("Timetable model unavailable: %s", e)

    try:
        from timetable.models import ExamTimetable
        scheduled_exam_ids       = set(ExamTimetable.objects
                                        .values_list("course_allocation_id", flat=True).distinct())
        result["exam_conflicts"] = _collisions_exam(ExamTimetable)
    except Exception as e:
        logger.warning("ExamTimetable model unavailable: %s", e)

    try:
        from timetable.models import AutoMergedExamGroup
        pg = AutoMergedExamGroup.objects.filter(published=True)
        scheduled_exam_ids |= set(pg.values_list("base_course_id",     flat=True))
        scheduled_exam_ids |= set(pg.values_list("merged_courses__id", flat=True))
    except Exception:
        pass

    submitted = list(
        CourseAllocation.objects.filter(submitted_to_tt=True)
        .select_related("lecturer", "program", "department")
    )
    result["unscheduled_tt"]   = [a for a in submitted if a.id not in scheduled_tt_ids]
    result["unscheduled_exam"] = [a for a in submitted if a.id not in scheduled_exam_ids]
    result["unassigned"]       = [a for a in submitted if not a.lecturer_id]
    return result


def _collisions_tt(TT):
    from program_management.models import ProgramCourse
    from course_allocation.config_helpers import strip_course_code_tag

    entries  = list(TT.objects.select_related(
        "course_allocation", "course_allocation__program",
        "course_allocation__lecturer", "venue"))
    slot_map = defaultdict(list)
    for tt in entries:
        day   = tt.day or "Unknown"
        start = tt.start_time.strftime("%H:%M") if tt.start_time else "00:00"
        end   = tt.end_time.strftime("%H:%M")   if tt.end_time   else "00:00"
        slot_map[f"{day}|{start}|{end}"].append(tt)

    conflicts = []
    for slot_key, grp in slot_map.items():
        if len(grp) <= 1:
            continue
        day, start, end = slot_key.split("|")

        # program-year conflicts
        py_grps = defaultdict(list)
        for e in grp:
            ca   = e.course_allocation
            prog = getattr(ca, "program", None)
            if not prog:
                continue
            pc_direct = getattr(ca, "program_course", None)
            if pc_direct and getattr(pc_direct, "year", None):
                yr = str(pc_direct.year)
            else:
                pc = ProgramCourse.objects.filter(
                    program=prog, course_code__iexact=strip_course_code_tag(ca.course_code)).first()
                yr = str(pc.year) if pc else None
            if yr:
                py_grps[f"{prog.id}_{yr}"].append(e)
        for pk, sg in py_grps.items():
            if len(sg) > 1:
                pid, yr = pk.split("_", 1)
                pname   = (sg[0].course_allocation.program.name
                           if sg[0].course_allocation.program else f"Prog#{pid}")
                conflicts.append({"type": "Program Conflict",
                                   "day": day, "slot": f"{start}–{end}",
                                   "entity": f"{pname}  Year {yr}",
                                   "courses": [_cs(e) for e in sg]})

        # lecturer conflicts
        lg = defaultdict(list)
        for e in grp:
            lect = getattr(e.course_allocation, "lecturer", None)
            if lect:
                lg[lect.id].append(e)
        for lid, sg in lg.items():
            if len(sg) > 1:
                conflicts.append({"type": "Lecturer Clash",
                                   "day": day, "slot": f"{start}–{end}",
                                   "entity": sg[0].course_allocation.lecturer.display_name,
                                   "courses": [_cs(e) for e in sg]})

        # venue conflicts
        vg = defaultdict(list)
        for e in grp:
            if e.venue:
                vg[e.venue.code].append(e)
        for vcode, sg in vg.items():
            if len(sg) > 1:
                conflicts.append({"type": "Venue Double-Booked",
                                   "day": day, "slot": f"{start}–{end}",
                                   "entity": vcode,
                                   "courses": [_cs(e) for e in sg]})
    return conflicts


def _collisions_exam(ET):
    try:
        entries = list(ET.objects.select_related("course_allocation", "venue"))
    except Exception:
        return []

    slot_map = defaultdict(list)
    for et in entries:
        d     = et.date.strftime("%Y-%m-%d") if hasattr(et, "date") and et.date else "N/A"
        start = et.start_time.strftime("%H:%M") if et.start_time else "00:00"
        end   = et.end_time.strftime("%H:%M")   if et.end_time   else "00:00"
        slot_map[f"{d}|{start}|{end}"].append(et)

    conflicts = []
    for slot_key, grp in slot_map.items():
        if len(grp) <= 1:
            continue
        d, start, end = slot_key.split("|")
        vg = defaultdict(list)
        for e in grp:
            if e.venue:
                vg[e.venue.code].append(e)
        for vcode, sg in vg.items():
            if len(sg) > 1:
                conflicts.append({"type": "Exam Venue Double-Booked",
                                   "day": d, "slot": f"{start}–{end}",
                                   "entity": vcode,
                                   "courses": [_cs(e) for e in sg]})
    return conflicts


def _cs(entry):
    ca = entry.course_allocation
    return {
        "code":     getattr(ca, "course_code", "?"),
        "name":     getattr(ca, "course_name", "?"),
        "lecturer": (ca.lecturer.display_name
                     if getattr(ca, "lecturer", None) else "Unassigned"),
        "venue":    getattr(entry.venue, "code", "N/A") if entry.venue else "N/A",
    }


def _tt_inline(data):
    lines = ["📅 Timetable System Alerts:\n"]

    def _al(lst, cap=4):
        for a in lst[:cap]:
            lines.append(
                f"    • {a.course_code} – {a.course_name} "
                f"({a.department.name if a.department else 'N/A'})"
            )
        if len(lst) > cap:
            lines.append(f"    … and {len(lst) - cap} more.")

    if data["tt_conflicts"]:
        lines.append(f"\n⚡ TT Conflicts ({len(data['tt_conflicts'])}):")
        for c in data["tt_conflicts"][:4]:
            lines.append(f"    • {c['type']}: {c['entity']}  [{c['day']} {c['slot']}]")
    if data["exam_conflicts"]:
        lines.append(f"\n⚡ Exam Conflicts ({len(data['exam_conflicts'])}):")
        for c in data["exam_conflicts"][:4]:
            lines.append(f"    • {c['type']}: {c['entity']}  [{c['day']} {c['slot']}]")
    if data["unscheduled_tt"]:
        lines.append(f"\n🔴 Unscheduled TT ({len(data['unscheduled_tt'])}):")
        _al(data["unscheduled_tt"])
    if data["unscheduled_exam"]:
        lines.append(f"\n🔴 Unscheduled Exam ({len(data['unscheduled_exam'])}):")
        _al(data["unscheduled_exam"])
    if data["unassigned"]:
        lines.append(f"\n👤 Unassigned Lecturers ({len(data['unassigned'])}):")
        _al(data["unassigned"])
    return "\n".join(lines)


def _tt_pdf(data):
    if not REPORTLAB_OK:
        return "#"
    buf   = io.BytesIO()
    doc   = _pdf_doc(buf)
    story = _pdf_header(
        "TIMETABLE SYSTEM — ISSUE REPORT",
        (f"TT Conflicts: {len(data['tt_conflicts'])}  |  "
         f"Exam Conflicts: {len(data['exam_conflicts'])}  |  "
         f"Unscheduled TT: {len(data['unscheduled_tt'])}  |  "
         f"Unscheduled Exam: {len(data['unscheduled_exam'])}  |  "
         f"Unassigned: {len(data['unassigned'])}"),
        COLORS["critical"],
    )
    if data["tt_conflicts"]:
        story += _sec_head("⚡  TIMETABLE CONFLICTS", COLORS["critical"])
        rows = [["Type", "Day", "Slot", "Entity", "Courses"]]
        for c in data["tt_conflicts"]:
            rows.append([c["type"], c["day"], c["slot"], c["entity"],
                         "  |  ".join(f"{x['code']} ({x['lecturer']})" for x in c["courses"])])
        story.append(_pdf_table(rows, [3.5*cm, 2.5*cm, 3*cm, 4.5*cm, 5.5*cm], COLORS["critical"]))
        story.append(Spacer(1, 0.4*cm))
    if data["exam_conflicts"]:
        story += _sec_head("⚡  EXAM CONFLICTS", COLORS["critical"])
        rows = [["Type", "Date", "Slot", "Venue", "Courses"]]
        for c in data["exam_conflicts"]:
            rows.append([c["type"], c["day"], c["slot"], c["entity"],
                         "  |  ".join(x["code"] for x in c["courses"])])
        story.append(_pdf_table(rows, [4*cm, 2.5*cm, 3*cm, 2.5*cm, 7*cm], COLORS["critical"]))
        story.append(Spacer(1, 0.4*cm))
    if data["unscheduled_tt"]:
        story += _sec_head("🔴  UNSCHEDULED (TIMETABLE)", COLORS["warning"])
        story.append(_alloc_table(data["unscheduled_tt"], COLORS["warning"]))
        story.append(Spacer(1, 0.4*cm))
    if data["unscheduled_exam"]:
        story += _sec_head("🔴  UNSCHEDULED (EXAM)", COLORS["warning"])
        story.append(_alloc_table(data["unscheduled_exam"], COLORS["warning"]))
        story.append(Spacer(1, 0.4*cm))
    if data["unassigned"]:
        story += _sec_head("👤  UNASSIGNED LECTURERS", COLORS["info"])
        story.append(_alloc_table(data["unassigned"], COLORS["info"]))
    doc.build(story)
    return _save_pdf(buf, "timetable_issues")


# ─────────────────────────────────────────────────────────────────────────────
# MASTER RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def _run_all():
    """Run all generators. Called lazily from get_notifications on first hit."""
    for fn in (_gen_dvc, _gen_cod, _gen_timetable):
        try:
            fn()
        except Exception as e:
            logger.exception("Notification generator error (%s): %s", fn.__name__, e)


# ─────────────────────────────────────────────────────────────────────────────
# RESOLVED CHECK
# ─────────────────────────────────────────────────────────────────────────────

def _is_resolved(notif):
    """True if the DB condition behind this notification no longer exists."""
    from course_allocation.models import CourseAllocation

    key = _dedup_key(notif.message) or ""
    msg = notif.message

    if msg.lstrip().startswith("✅") or "all_clear" in key:
        return True
    if "dvc_pending" in key:
        return not CourseAllocation.objects.filter(
            approved_by_dvc=False, rejected_by_dvc=False).exists()
    return False


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API VIEWS  (exact same signatures as the original)
# ─────────────────────────────────────────────────────────────────────────────

@require_GET
@ratelimit(key='user_or_ip', rate='60/m', method='GET', block=True)
def get_notifications(request):
    """
    Return recent notifications + submission status.

    JSON shape (identical to original — no frontend changes needed):
    {
        "notifications": [{"id", "message", "is_read", "created_at"}, ...],
        "has_unread":    bool,
        "submitted":     [...dept names...],
        "not_submitted": [...dept names...]
    }

    First call for a user with no notifications → generators run automatically.
    """
    user = request.user

    if user.is_authenticated:
        # Some environments may not have the custom manager method available
        if hasattr(Notification.objects, 'for_user'):
            qs = Notification.objects.for_user(user).order_by("-created_at")
        else:
            # Fallback: build equivalent queryset manually
            user_groups = user.groups.all()
            q = Q(target_user__isnull=True, target_group__isnull=True, target_role__isnull=True)
            q |= Q(target_user=user)
            if user_groups.exists():
                q |= Q(target_group__in=user_groups)
            if hasattr(user, 'profile') and hasattr(user.profile, 'role'):
                q |= Q(target_role=user.profile.role)
            qs = Notification.objects.filter(q).order_by("-created_at")
    else:
        qs = Notification.objects.filter(
            target_user__isnull=True,
            target_group__isnull=True,
            target_role__isnull=True,
        ).order_by("-created_at")

    # Lazy first-time generation
    if not qs.exists():
        _run_all()
        if user.is_authenticated:
            if hasattr(Notification.objects, 'for_user'):
                qs = Notification.objects.for_user(user).order_by("-created_at")
            else:
                user_groups = user.groups.all()
                q = Q(target_user__isnull=True, target_group__isnull=True, target_role__isnull=True)
                q |= Q(target_user=user)
                if user_groups.exists():
                    q |= Q(target_group__in=user_groups)
                if hasattr(user, 'profile') and hasattr(user.profile, 'role'):
                    q |= Q(target_role=user.profile.role)
                qs = Notification.objects.filter(q).order_by("-created_at")
        else:
            qs = Notification.objects.filter(
                target_user__isnull=True,
                target_group__isnull=True,
                target_role__isnull=True,
            ).order_by("-created_at")

    # Auto-purge resolved read notifications
    resolved_ids = [n.id for n in qs.filter(is_read=True) if _is_resolved(n)]
    if resolved_ids:
        Notification.objects.filter(id__in=resolved_ids).delete()
        if user.is_authenticated:
            if hasattr(Notification.objects, 'for_user'):
                qs = Notification.objects.for_user(user).order_by("-created_at")
            else:
                user_groups = user.groups.all()
                q = Q(target_user__isnull=True, target_group__isnull=True, target_role__isnull=True)
                q |= Q(target_user=user)
                if user_groups.exists():
                    q |= Q(target_group__in=user_groups)
                if hasattr(user, 'profile') and hasattr(user.profile, 'role'):
                    q |= Q(target_role=user.profile.role)
                qs = Notification.objects.filter(q).order_by("-created_at")

    unread        = qs.filter(is_read=False).exists()
    notifications = list(qs[:10])

    submitted     = SubmissionControl.objects.filter(allow_submission_to_tt=True)
    not_submitted = SubmissionControl.objects.filter(allow_submission_to_tt=False)

    # Submission Status tab is only meaningful for Timetabling staff.
    # COD users should NOT see it — return empty lists for them.
    user_group_names = set(user.groups.values_list('name', flat=True)) if user.is_authenticated else set()
    is_timetabling_user = bool(user_group_names & set(TT_GROUPS)) or (user.is_authenticated and user.is_superuser)

    return JsonResponse({
        "notifications": [
            {
                "id":         n.id,
                "message":    _clean(n.message),
                "html":       _linkify_message(_clean(n.message), request),
                "is_read":    n.is_read,
                "created_at": n.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for n in notifications
        ],
        "has_unread":    unread,
        "submitted":     [s.department.name for s in submitted     if s.department] if is_timetabling_user else [],
        "not_submitted": [s.department.name for s in not_submitted if s.department] if is_timetabling_user else [],
    })


@require_POST
@ratelimit(key='user_or_ip', rate='20/m', method='POST', block=True)
def mark_notification_read(request):
    """
    Mark a notification as read or mark all as read.
    Accepts form-encoded POSTs (legacy) or JSON body { id: <id> } / { mark_all: true }.
    Auto-deletes a notification if its underlying condition is resolved.
    """
    # Accept JSON or form-encoded bodies
    data = {}
    try:
        ctype = request.META.get('CONTENT_TYPE', '')
        if ctype.startswith('application/json'):
            data = json.loads(request.body.decode('utf-8') or '{}')
        else:
            data = request.POST
    except Exception:
        data = request.POST

    # Support mark_all
    if data.get('mark_all'):
        user = request.user
        if user.is_authenticated:
            if hasattr(Notification.objects, 'for_user'):
                Notification.objects.for_user(user).update(is_read=True)
            else:
                user_groups = user.groups.all()
                q = Q(target_user__isnull=True, target_group__isnull=True, target_role__isnull=True)
                q |= Q(target_user=user)
                if user_groups.exists():
                    q |= Q(target_group__in=user_groups)
                if hasattr(user, 'profile') and hasattr(user.profile, 'role'):
                    q |= Q(target_role=user.profile.role)
                Notification.objects.filter(q).update(is_read=True)
        else:
            Notification.objects.filter(
                target_user__isnull=True,
                target_group__isnull=True,
                target_role__isnull=True,
            ).update(is_read=True)
        return JsonResponse({"success": True, "marked_all": True})

    notif_id = data.get('id')
    if notif_id is None:
        return JsonResponse({"success": False, "error": "Notification ID not provided"}, status=400)

    updated = Notification.objects.filter(id=notif_id).update(is_read=True)
    if not updated:
        return JsonResponse({"success": False, "error": "Notification not found"}, status=404)

    try:
        notif = Notification.objects.get(id=notif_id)
        if _is_resolved(notif):
            notif.delete()
            return JsonResponse({"success": True, "id": notif_id, "deleted": True})
    except Notification.DoesNotExist:
        pass

    return JsonResponse({"success": True, "id": notif_id, "deleted": False})