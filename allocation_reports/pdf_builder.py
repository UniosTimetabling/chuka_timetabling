"""
allocation_reports/pdf_builder.py
===================================
Builds a Course Allocation PDF that visually matches Chuka University's
official letterhead style: black-and-white only (no color accents), one
typeface throughout (Times New Roman), logo centred at the very top of the
header block above the university name.

Content is organised:
    Program -> Year -> Semester -> Intake -> Student Group (A / B / C / ...)
each combination getting:
    - a plain table of core/mandatory courses, then
    - one sub-table per specialization stem ("Specialization — <category>:
      <stem> Stem"), showing the full combination of courses a student who
      picks that stem takes together, and
    - one sub-table per elective selection group ("Elective Options —
      <name>"), showing the combination of courses a student picks exactly
      one from,
each table: Course Code | Course Name | Type | Origin Department | Lecturer | No. of Students
"""
import io
from collections import defaultdict

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from allocation_reports.adapters import get_adapter

# Official Chuka University letterhead is black and white only — no color
# accents. Table header bars are solid black with white text, matching the
# "Header row: white text on black background" convention already used
# (and documented in code comments) in campuses_timetable/pdf_views.py and
# odel_system/pdf_management_views.py. Previous revisions of this file used
# a red accent (#FF0000, later #CC0000) which is not the official style.
HEADER_BAR = colors.black
BODY_TEXT = colors.black

# Times New Roman equivalents built into reportlab — no font registration
# needed. Used exclusively throughout this document; no other typeface.
FONT_REGULAR = "Times-Roman"
FONT_BOLD = "Times-Bold"
FONT_ITALIC = "Times-Italic"
FONT_BOLD_ITALIC = "Times-BoldItalic"


def _get_template_config():
    from export_import.models import TimetablePdfTemplate
    return TimetablePdfTemplate.get_template()


def _header_flowables(styles, template_config, title_text, ref_number, date_str):
    """
    Logo centred on its own row at the very top, university name directly
    beneath it, then motto/directorate, then a left/right contact-info row,
    then the bold Ref/Date rule, then the (black, bold) report title —
    every element centred and aligned to the same margins for a clean,
    symmetrical letterhead.
    """
    flow = []

    logo_img = None
    try:
        if template_config.university_logo and template_config.university_logo.path:
            logo_img = Image(template_config.university_logo.path, width=0.85 * inch, height=0.85 * inch)
            logo_img.hAlign = "CENTER"
    except Exception:
        logo_img = None

    if logo_img:
        flow.append(logo_img)
        flow.append(Spacer(1, 4))

    flow.append(Paragraph(f"<b>{template_config.university_name}</b>", styles["UniName"]))
    flow.append(Paragraph(
        f"<i>{template_config.motto_latin}</i> {template_config.motto_swahili}",
        styles["Center"],
    ))
    flow.append(Paragraph(template_config.directorate_name, styles["CenterBold"]))
    flow.append(Spacer(1, 6))

    contact_row = Table(
        [[
            Paragraph(f"Telephones: {template_config.telephone}<br/>Direct Line:", styles["Small"]),
            Paragraph(
                f"{template_config.address}<br/>Email: {template_config.email} "
                f"Website: {template_config.website}",
                styles["SmallRight"],
            ),
        ]],
        colWidths=[260, 260],
    )
    contact_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    flow.append(contact_row)
    flow.append(Spacer(1, 8))

    ref_line = Table(
        [[Paragraph(f"<b>Ref: {ref_number}</b>", styles["Small"]),
          Paragraph(f"<b>Date: {date_str}</b>", styles["SmallRight"])]],
        colWidths=[260, 260],
    )
    ref_line.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.black),
    ]))
    flow.append(ref_line)
    flow.append(Spacer(1, 10))
    flow.append(Paragraph(title_text, styles["TitleBlack"]))
    flow.append(Spacer(1, 10))
    return flow


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("Small", parent=ss["Normal"], fontName=FONT_REGULAR, fontSize=8, leading=10,
                           textColor=BODY_TEXT))
    ss.add(ParagraphStyle("SmallRight", parent=ss["Small"], alignment=2))
    ss.add(ParagraphStyle("UniName", parent=ss["Normal"], fontSize=16, alignment=1,
                           fontName=FONT_BOLD, textColor=BODY_TEXT))
    ss.add(ParagraphStyle("Center", parent=ss["Normal"], fontSize=9, alignment=1,
                           fontName=FONT_REGULAR, textColor=BODY_TEXT))
    ss.add(ParagraphStyle("CenterBold", parent=ss["Normal"], fontSize=10, alignment=1,
                           fontName=FONT_BOLD, textColor=BODY_TEXT))
    ss.add(ParagraphStyle("TitleBlack", parent=ss["Normal"], fontSize=13, alignment=1,
                           fontName=FONT_BOLD, textColor=BODY_TEXT))
    ss.add(ParagraphStyle("SectionHead", parent=ss["Normal"], fontSize=11, fontName=FONT_BOLD,
                           spaceBefore=10, spaceAfter=4, textColor=BODY_TEXT))
    # One notch below SectionHead — identifies Year + Semester + Intake, the
    # actual cohort a table belongs to (e.g. "Year 1 — Semester 1 — Normal
    # Intake" vs "Year 2 — Semester 2 — Special Intake"), so cohorts that
    # share a program/year but differ in semester or intake never collapse
    # into one table.
    ss.add(ParagraphStyle("SubHead", parent=ss["Normal"], fontSize=9.5, fontName=FONT_BOLD,
                           spaceBefore=4, spaceAfter=2, textColor=BODY_TEXT))
    # Group A / Group B / Group C sub-tables sit one notch below SubHead —
    # same cohort, split further where the COD has split the program into
    # parallel teaching groups.
    ss.add(ParagraphStyle("GroupHead", parent=ss["Normal"], fontSize=9, fontName=FONT_BOLD_ITALIC,
                           spaceBefore=3, spaceAfter=2, textColor=BODY_TEXT))
    # Specialization-stem / elective-selection-group combination headings —
    # sit under a GroupHead (or directly under SubHead when the cohort has
    # no Group A/B/C split), and additionally carry a one-line explanatory
    # note so it's clear the listed courses are a *combination* students
    # take together (a stem) or choose one from (a selection group), not
    # just more mandatory courses.
    ss.add(ParagraphStyle("ComboHead", parent=ss["Normal"], fontSize=8.5, fontName=FONT_BOLD,
                           spaceBefore=3, spaceAfter=1, textColor=BODY_TEXT, leftIndent=8))
    ss.add(ParagraphStyle("ComboNote", parent=ss["Normal"], fontSize=7.5, fontName=FONT_ITALIC,
                           spaceBefore=0, spaceAfter=2, textColor=BODY_TEXT, leftIndent=8))
    ss.add(ParagraphStyle("Cell", parent=ss["Normal"], fontSize=8, leading=10,
                           fontName=FONT_REGULAR, textColor=BODY_TEXT))
    ss.add(ParagraphStyle("CellHead", parent=ss["Normal"], fontSize=8, leading=10,
                           fontName=FONT_BOLD, textColor=colors.white))
    return ss


def _type_cell(r):
    # Stem/selection-group membership is shown via the sub-table heading it
    # sits under (see _render_group_rows) — this column just flags whether
    # the course itself is elective or core, independent of that grouping.
    return "Elective" if r.get("is_elective") else "Core"


def _lecturer_cell(r):
    text = r["lecturer"]
    merged = r.get("merged_with") or []
    if merged:
        text += f"<br/><font size=6.5><i>(merged with {', '.join(merged)})</i></font>"
    return text


def _render_table(story, styles, group_rows):
    col_widths = [62, 148, 68, 92, 100, 48]
    header_row = ["Course Code", "Course Name", "Type", "Origin Department", "Lecturer", "No. of Students"]

    table_data = [[Paragraph(h, styles["CellHead"]) for h in header_row]]
    for r in sorted(group_rows, key=lambda x: x["course_code"]):
        table_data.append([
            Paragraph(r["course_code"], styles["Cell"]),
            Paragraph(r["course_name"], styles["Cell"]),
            Paragraph(_type_cell(r), styles["Cell"]),
            Paragraph(r["origin_department"], styles["Cell"]),
            Paragraph(_lecturer_cell(r), styles["Cell"]),
            Paragraph(str(r["students"]), styles["Cell"]),
        ])

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BAR),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))


def _render_group_rows(story, styles, rows):
    """
    Given the rows for one Program/Year/Semester/Intake/(Group) bucket,
    split out any specialization-stem or elective-selection-group
    "combinations" into their own clearly labeled sub-tables, so a stem's
    full set of courses reads as one unit rather than being scattered
    through a flat table with only a small per-row tag. Plain
    core/mandatory courses (and electives outside any named selection
    group) still render as a single ordinary table.
    """
    core_rows = []
    stem_buckets = defaultdict(list)   # (category_name, stem_name) -> rows
    selgroup_buckets = defaultdict(list)  # selection_group_name -> rows

    for r in rows:
        if r.get("stem"):
            stem_buckets[(r.get("stem_category") or "Specialization", r["stem"])].append(r)
        elif r.get("selection_group"):
            selgroup_buckets[r["selection_group"]].append(r)
        else:
            core_rows.append(r)

    if core_rows:
        _render_table(story, styles, core_rows)

    for (category_name, stem_name) in sorted(stem_buckets.keys(), key=lambda k: (k[0], k[1])):
        story.append(Paragraph(f"Specialization — {category_name}: {stem_name} Stem", styles["ComboHead"]))
        story.append(Paragraph(
            "Students who choose this stem take every course listed below together.",
            styles["ComboNote"],
        ))
        _render_table(story, styles, stem_buckets[(category_name, stem_name)])

    for selgroup_name in sorted(selgroup_buckets.keys()):
        story.append(Paragraph(f"Elective Options — {selgroup_name}", styles["ComboHead"]))
        story.append(Paragraph(
            "Students choose exactly one course from the list below.",
            styles["ComboNote"],
        ))
        _render_table(story, styles, selgroup_buckets[selgroup_name])


def _render_serviced_section(story, styles, serviced_rows):
    """
    "SERVICED COURSES" — courses this department actually teaches on behalf
    of another department's program (origin_department = this department,
    but the row's own allocating department is someone else's). Appended
    once at the very end of the PDF, after every program/year section,
    grouped by program so it still reads program-by-program.
    """
    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.8, color=colors.black, spaceAfter=10))
    story.append(Paragraph("SERVICED COURSES", styles["SectionHead"]))
    story.append(Paragraph(
        "Courses this department teaches on behalf of another department's program "
        "(shown here for visibility; they are allocated under the department below, not this one).",
        styles["ComboNote"],
    ))
    story.append(Spacer(1, 4))

    if not serviced_rows:
        story.append(Paragraph("None.", styles["Cell"]))
        return

    grouped = defaultdict(list)
    for r in serviced_rows:
        grouped[r["program"]].append(r)

    col_widths = [58, 130, 90, 92, 80, 48]
    header_row = ["Course Code", "Course Name", "Allocating Dept", "Lecturer", "Year/Sem", "No. of Students"]

    for program_name in sorted(grouped.keys()):
        story.append(Paragraph(program_name, styles["SubHead"]))
        table_data = [[Paragraph(h, styles["CellHead"]) for h in header_row]]
        for r in sorted(grouped[program_name], key=lambda x: x["course_code"]):
            year_sem = f"Y{r['year']}" if r.get("year") else "—"
            if r.get("semester"):
                year_sem += f" S{r['semester']}"
            table_data.append([
                Paragraph(r["course_code"], styles["Cell"]),
                Paragraph(r["course_name"], styles["Cell"]),
                Paragraph(r["allocating_department"], styles["Cell"]),
                Paragraph(r["lecturer"], styles["Cell"]),
                Paragraph(year_sem, styles["Cell"]),
                Paragraph(str(r["students"]), styles["Cell"]),
            ])
        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HEADER_BAR),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
        story.append(Spacer(1, 8))


def build_allocation_pdf(scope, department, rows, campus=None, generated_by=None, serviced_rows=None):
    """
    rows: normalised list of dicts from adapters.fetch_rows()
    Returns raw PDF bytes.
    """
    adapter = get_adapter(scope)
    template_config = _get_template_config()
    styles = _styles()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=36, bottomMargin=36, leftMargin=36, rightMargin=36,
    )

    now = timezone.now()
    date_str = now.strftime("%d-%b-%Y").upper()
    ref_number = template_config.get_reference_number(date_str)

    campus_bit = f" — {campus.name} Campus" if campus else ""
    title_text = f"{adapter.label} — {department.name}{campus_bit}"

    story = _header_flowables(styles, template_config, title_text, ref_number, date_str)

    if not rows:
        story.append(Paragraph("No allocations have been submitted for this department yet.", styles["Small"]))
        if serviced_rows:
            _render_serviced_section(story, styles, serviced_rows)
        doc.build(story)
        return buf.getvalue()

    # Group by Program -> Year -> Semester -> Intake. Semester and intake are
    # both cohort-defining: a "Special Intake" Year-2-Semester-2 cohort is a
    # different set of students from the Normal-intake Year-1-Semester-1
    # cohort of the same program, and must never share a table with it even
    # though they're the same program/year.
    grouped = defaultdict(lambda: defaultdict(list))
    for r in rows:
        semester_key = r.get("semester")
        intake_key = r.get("intake") or "Normal"
        grouped[r["program"]][(r["year"], semester_key, intake_key)].append(r)

    def _cohort_sort_key(k):
        year, semester, intake = k
        return (
            year is None, year,
            semester is None, semester,
            intake != "Normal",  # Normal intake first, Special after
        )

    def _group_sort_key(letter):
        return (letter is None, letter)  # shared (None) rows first, then A, B, C...

    for program_name in sorted(grouped.keys()):
        story.append(Paragraph(program_name, styles["SectionHead"]))
        cohorts = grouped[program_name]

        for (year, semester, intake) in sorted(cohorts.keys(), key=_cohort_sort_key):
            year_label = f"Year {year}" if year else "Year — (unspecified)"
            semester_label = f"Semester {semester}" if semester else "Semester — (unspecified)"
            intake_label = f"{intake} Intake" if intake and intake != "Normal" else "Normal Intake"
            story.append(Paragraph(f"{year_label} — {semester_label} — {intake_label}", styles["SubHead"]))

            cohort_rows = cohorts[(year, semester, intake)]

            # Split further by Student Group (COD-defined Group A / B / C...)
            # so a cohort the COD has split into parallel teaching groups
            # (e.g. BSc Nursing — Group A / Group B / Group C) gets one
            # clearly-labeled table per group instead of one table that
            # silently mixes every group's allocations together.
            by_group = defaultdict(list)
            for r in cohort_rows:
                by_group[r.get("student_group")].append(r)

            group_keys = sorted(by_group.keys(), key=_group_sort_key)
            has_real_groups = any(k is not None for k in group_keys)

            if not has_real_groups:
                # No group splitting in this cohort — one set of tables
                # (core + any stem/selection-group combinations) as before.
                _render_group_rows(story, styles, cohort_rows)
            else:
                for letter in group_keys:
                    if letter is None:
                        # Shared across every group (electives / stem courses) —
                        # still labeled explicitly so it isn't mistaken for a
                        # missed group.
                        story.append(Paragraph("Shared across all groups", styles["GroupHead"]))
                    else:
                        story.append(Paragraph(f"Group {letter}", styles["GroupHead"]))
                    _render_group_rows(story, styles, by_group[letter])

    _render_serviced_section(story, styles, serviced_rows or [])

    story.append(Spacer(1, 14))
    story.append(Paragraph(
        f"{template_config.prepared_by_label} DIRECTORATE OF EXAMINATIONS AND TIMETABLING",
        styles["Small"],
    ))
    story.append(Paragraph(template_config.director_label, styles["Small"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Generated: {now.strftime('%d %b %Y, %H:%M')}"
                            + (f" by {generated_by}" if generated_by else ""), styles["Small"]))
    story.append(Paragraph(template_config.notes, styles["Small"]))

    doc.build(story)
    return buf.getvalue()
