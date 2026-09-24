# -----------------------------------------------------------------------
# Student Groups + Combination Stems + Electives + Course Groups page
# -----------------------------------------------------------------------
#
# One "form on the left / table on the right" page (same layout/design as
# /programs/) for the four department-level knowledge-base entities that
# previously only had scattered management UIs:
#
#   * StudentGroup            (this app's own model)
#   * SpecializationStem      ("Combination Stem" — lives inside a
#                               SpecializationCategory, created on the fly
#                               here from a plain "Category name" field)
#   * SelectionGroup          ("Elective Group")
#   * GroupingTemplate        ("Course Groups" — the bulk-split / stem-pin
#                               plan that auto-allocate replays)
#
# This page only manages the records themselves (name / program / year /
# semester / etc.) — quick add, rename, delete. Assigning actual courses
# into a stem or an elective pool, and the drag-and-drop allocation work,
# still happens on the existing dedicated panels:
#   /cod/                        (main COD panel)
#   /cod/specialization-stems/   (stems + their courses)
#   /cod/base-selections/        (elective groups + their courses)
#
# Department scoping mirrors program_management.programs_page: a COD/COD
# Admin only ever sees their own department; anyone else (DVC/TT/Admin)
# picks a department via ?department_id=.
from io import BytesIO

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle)
from core.doc_export import BaseDocTemplate

from department_management.models import Department
from program_management.models import Program
from program_management.programs_page import detect_user_department

from .models import (
    CourseAllocation, StudentGroup, SpecializationStem, SelectionGroup,
    GroupingTemplate,
)
from .allocation_scope import get_active_allocation_set, get_or_default_legacy_set


def _course_item(ca):
    """Plain dict the template renders as one course chip."""
    return {
        "id": ca.id,
        "code": ca.course_code,
        "name": ca.course_name,
        "section": ca.section_number,          # None unless the course was split
        "students": ca.number_of_students,
        "program": ca.program.name if ca.program_id else "",
    }


def _sorted_items(allocations):
    """Course rows ordered by code, then section number."""
    return [
        _course_item(ca)
        for ca in sorted(
            allocations,
            key=lambda c: ((c.course_code or "").upper(), c.section_number or 0, c.id),
        )
    ]


def _term_blocks(allocations):
    """
    Split courses into Year/Semester blocks (from each course's curriculum
    entry) so the page can print "Year 1 Semester 1", "Year 1 Semester 2", ...
    as headings. Returns [{"label": str, "items": [course dicts]}], ordered by
    year then semester.
    """
    buckets = {}
    for ca in allocations:
        pc = ca.program_course
        key = (pc.year, pc.semester) if pc else (0, 0)
        buckets.setdefault(key, []).append(ca)
    blocks = []
    for key in sorted(buckets):
        year, sem = key
        label = f"Year {year} Semester {sem}" if year else "Term not set"
        blocks.append({"label": label, "items": _sorted_items(buckets[key])})
    return blocks


def _pc_term_blocks(program_courses, allocated_ids, from_allocation_ids=frozenset()):
    """
    Curriculum (ProgramCourse) courses mapped IN ADVANCE to a stem / elective group,
    split into Year/Semester blocks. `allocated` tells whether an allocation of that
    course is already attached to the stem / group. `from_allocation_ids` marks the
    ones that got mapped automatically by the map_program_courses_from_existing_
    allocations.py backfill script (reverse-derived from an allocation already
    sitting on the stem/group) rather than picked by hand in the Map courses dialog.
    """
    buckets = {}
    for pc in program_courses:
        buckets.setdefault((pc.year, pc.semester), []).append(pc)
    blocks = []
    for (year, sem) in sorted(buckets):
        items = [
            {
                "id": pc.id, "code": pc.course_code, "name": pc.course_name,
                "program": pc.program.name, "cohort": pc.student_cohort,
                "allocated": pc.id in allocated_ids,
                "from_allocation": pc.id in from_allocation_ids,
            }
            for pc in sorted(buckets[(year, sem)], key=lambda p: ((p.course_code or "").upper(), p.id))
        ]
        blocks.append({"label": f"Year {year} Semester {sem}", "items": items})
    return blocks


def _attach_courses(student_groups, stems, electives, alloc_set):
    """
    Attach the courses mapped to every row so the page can print them
    directly underneath it:

      * StudentGroup  -> group.terms   (primary + additional mapped groups)
      * SpecializationStem -> stem.core_terms, stem.pool_blocks, stem.course_total
                              (pool_blocks = nested pick-one pools, each with .terms)
      * SelectionGroup -> el.terms, el.nested_stem_names
    Every "terms" value is a list of Year/Semester blocks (see _term_blocks).
    """
    # ---- Student groups: one query, split in Python ----------------------
    group_ids = [g.id for g in student_groups]
    by_group = {gid: [] for gid in group_ids}
    if group_ids:
        ca_qs = (
            CourseAllocation.objects
            .filter(Q(student_group_id__in=group_ids) | Q(additional_student_groups__in=group_ids))
            .distinct()
            .select_related("program_course", "program")
            .prefetch_related("additional_student_groups")
        )
        if alloc_set is not None:
            ca_qs = ca_qs.filter(allocation_set=alloc_set)
        for ca in ca_qs:
            mapped = {g.id for g in ca.additional_student_groups.all()}
            if ca.student_group_id:
                mapped.add(ca.student_group_id)
            for gid in mapped & set(group_ids):
                by_group[gid].append(ca)
    for g in student_groups:
        g.terms = _term_blocks(by_group.get(g.id, []))
        g.course_total = len(by_group.get(g.id, []))

    # ---- Combination stems: core units + nested pick-one pools -----------
    for stem in stems:
        pool_course_ids = set()
        blocks = []
        for pool in stem.elective_groups.all():
            pool_courses = list(pool.courses.all())
            pool_course_ids.update(c.id for c in pool_courses)
            blocks.append({"name": pool.name, "terms": _term_blocks(pool_courses)})
        core = [c for c in stem.courses.all() if c.id not in pool_course_ids]
        stem.mapped_terms = _pc_term_blocks(
            stem.program_courses.all(), {c.program_course_id for c in stem.courses.all()},
            {pc.id for pc in stem.program_courses_from_allocation.all()})
        stem.mapped_total = len(stem.program_courses.all())
        stem.core_terms = _term_blocks(core)
        stem.core_total = len(core)
        stem.pool_blocks = blocks
        stem.course_total = len(core) + len(pool_course_ids)

    # ---- Elective groups --------------------------------------------------
    for el in electives:
        el_courses = list(el.courses.all())
        el.mapped_terms = _pc_term_blocks(
            el.program_courses.all(), {c.program_course_id for c in el_courses},
            {pc.id for pc in el.program_courses_from_allocation.all()})
        el.mapped_total = len(el.program_courses.all())
        el.terms = _term_blocks(el_courses)
        el.course_total = len(el_courses)
        el.nested_stem_names = [s.name for s in el.specialization_stems.all()]


def _course_group_plan_rows(templates):
    """
    One renderable dict per GroupingTemplate, carrying everything the
    Course Groups tab needs to print a row and its expandable detail:

      * letters            — ["A", "B", "C"]
      * scope / scope_label
      * selected_codes     — base course codes, only for scope="selected"
      * stem_pins          — [{"stem_id", "stem_name", "program_name", "letters": [...]}]
      * letter_details     — [{"letter", "stems": [...], "courses": [...]}]
                             (what each letter actually got applied to)

    All CourseAllocation lookups that build `letter_details` are batched by
    program/year/semester/intake for the whole set of templates, then
    bucketed in Python — no per-letter N+1 query.
    """
    templates = list(templates)
    if not templates:
        return []

    # ── Batch the one query that used to be per-letter ──────────────────
    # Collect every (program_id, year, semester, intake) that any template
    # covers, then pull all sectioned allocations (student_group set, i.e.
    # something a plan actually created) for those combinations in one go.
    combo_keys = {(t.program_id, t.year, t.semester, t.intake) for t in templates}
    combo_filter = Q()
    for program_id, year, semester, intake in combo_keys:
        combo_filter |= Q(
            program_id=program_id,
            program_course__year=year,
            program_course__semester=semester,
            intake=intake,
        )
    codes_by_combo = {}
    if combo_filter:
        rows = (
            CourseAllocation.objects
            .filter(combo_filter, student_group__isnull=False)
            .values_list(
                "program_id", "program_course__year", "program_course__semester",
                "intake", "course_code",
            )
            .distinct()
        )
        for program_id, year, semester, intake, code in rows:
            key = (program_id, year, semester, intake)
            codes_by_combo.setdefault(key, set()).add(code)

    def _letter_sort(letter):
        """A, B, ..., Z, AA, AB, ... — short first, then alpha."""
        return (len(letter), letter)

    out = []
    for tmpl in templates:
        letters = sorted((g.letter for g in tmpl.groups.all()), key=_letter_sort)
        selected_codes = sorted(c.base_course_code for c in tmpl.course_codes.all())

        # stem_id -> {"stem": stem, "letters": set()}
        pins = {}
        for sa in tmpl.stem_assignments.all():
            entry = pins.setdefault(sa.stem_id, {"stem": sa.stem, "letters": set()})
            entry["letters"].add(sa.group.letter)

        stem_pins = []
        for entry in pins.values():
            stem = entry["stem"]
            category = getattr(stem, "category", None)
            program = getattr(category, "program", None) if category else None
            stem_pins.append({
                "stem_id": stem.id,
                "stem_name": stem.name,
                "category_name": category.name if category else "",
                "program_name": program.name if program else "",
                "letters": sorted(entry["letters"], key=_letter_sort),
            })
        stem_pins.sort(key=lambda sp: (sp["program_name"], sp["category_name"], sp["stem_name"]))

        # Per-letter breakdown, using the batched code map above.
        combo_key = (tmpl.program_id, tmpl.year, tmpl.semester, tmpl.intake)
        all_codes = codes_by_combo.get(combo_key, set())
        letter_details = []
        for letter in letters:
            suffix = f"-{letter}"
            codes = sorted(c for c in all_codes if c.upper().endswith(suffix.upper()))
            stem_names = [sp["stem_name"] for sp in stem_pins if letter in sp["letters"]]
            letter_details.append({
                "letter": letter,
                "stem_names": stem_names,
                "codes": codes,
            })

        out.append({
            "id": tmpl.id,
            "program_id": tmpl.program_id,
            "program_name": tmpl.program.name if tmpl.program else "",
            "year": tmpl.year,
            "semester": tmpl.semester,
            "intake": tmpl.intake,
            "intake_display": tmpl.get_intake_display() if hasattr(tmpl, "get_intake_display") else tmpl.intake.capitalize(),
            "scope": tmpl.scope,
            "scope_label": tmpl.get_scope_display(),
            "letters": letters,
            "num_groups": len(letters),
            "selected_codes": selected_codes,
            "stem_pins": stem_pins,
            "letter_details": letter_details,
            "course_count": sum(len(ld["codes"]) for ld in letter_details),
        })
    return out


@login_required
def groups_electives_page(request):
    detected_dept = detect_user_department(request.user)

    requested_dept_id = request.GET.get("department_id")
    active_dept_id = detected_dept.id if detected_dept else (
        int(requested_dept_id) if requested_dept_id and requested_dept_id.isdigit() else None
    )

    if detected_dept:
        departments = Department.objects.select_related("faculty").filter(id=detected_dept.id)
    else:
        departments = Department.objects.select_related("faculty").all().only("id", "name", "faculty")

    if active_dept_id:
        programs = Program.objects.filter(department_id=active_dept_id).order_by("name")

        student_groups = list(
            StudentGroup.objects
            .filter(program__department_id=active_dept_id)
            .select_related("program")
            .order_by("program__name", "year", "semester", "intake", "letter")
        )

        stems = list(
            SpecializationStem.objects
            .filter(category__department_id=active_dept_id)
            .select_related("category", "category__program")
            .prefetch_related("courses__program_course", "courses__program",
                              "elective_groups__courses__program_course", "elective_groups__courses__program",
                              "program_courses__program", "program_courses_from_allocation")
            .order_by("category__program__name", "category__name", "name")
        )

        electives = list(
            SelectionGroup.objects
            .filter(department_id=active_dept_id)
            .select_related("program")
            .prefetch_related("courses__program_course", "specialization_stems", "program_courses__program",
                              "program_courses_from_allocation")
            .order_by("name")
        )

        course_groups = _course_group_plan_rows(
            GroupingTemplate.objects
            .filter(program__department_id=active_dept_id)
            .select_related("program")
            .prefetch_related(
                "groups",
                "course_codes",
                "stem_assignments__stem",
                "stem_assignments__stem__category",
                "stem_assignments__stem__category__program",
                "stem_assignments__group",
            )
            .order_by("program__name", "year", "semester", "intake")
        )

        # Student-group courses are scoped to the COD's active allocation set
        # (else the department's legacy set) so the same curriculum course
        # sitting in two concurrent sets is not listed twice. Stem / elective
        # courses already belong to their own container's set.
        dept_obj = detected_dept or Department.objects.filter(id=active_dept_id).first()
        alloc_set = None
        if dept_obj is not None:
            alloc_set = get_active_allocation_set(request, dept_obj) or get_or_default_legacy_set(dept_obj)

        _attach_courses(student_groups, stems, electives, alloc_set)
    else:
        programs = Program.objects.none()
        student_groups = []
        stems = []
        electives = []
        course_groups = []

    return render(request, "course_allocation/groups_electives.html", {
        "departments": departments,
        "active_department_id": active_dept_id,
        "years_range": range(1, 7),
        "programs": programs,
        "student_groups": student_groups,
        "stems": stems,
        "electives": electives,
        "course_groups": course_groups,
        # Programs for the "Map courses" dialog: scoped to the same department the
        # rest of this page is scoped to (the user's own department, or whichever
        # department a DVC/TT/Admin has picked) — never other departments' programs.
        "map_programs": (
            Program.objects.filter(department_id=active_dept_id).select_related("department").order_by("name")
            if active_dept_id else Program.objects.none()
        ),
    })


# ---------------------------------------------------------------------------
# "Export" button on the Combination Stem tab — a simple two-column PDF
# listing every combination stem in the department, one small table per
# program (S.No / Combination Stem). Tables aren't expected to be big, so
# they're laid out as two newspaper-style columns per page: reportlab flows
# each table from the left column into the right, then onto a new page,
# splitting a table across the column/page break with its header row
# repeated wherever it continues, rather than reserving a whole column per
# program.
# ---------------------------------------------------------------------------

_STEM_PDF_MARGIN = 12 * mm
_STEM_PDF_GUTTER = 6 * mm
_STEM_PDF_HEADER_H = 24 * mm


def _stem_pdf_page_template(dept_name, generated_at):
    """Two side-by-side Frames (the "columns") sharing one running header
    (department name + generated-at stamp + page number) drawn on every page."""
    page_w, page_h = A4
    col_width = (page_w - 2 * _STEM_PDF_MARGIN - _STEM_PDF_GUTTER) / 2
    frame_height = page_h - 2 * _STEM_PDF_MARGIN - _STEM_PDF_HEADER_H

    frame_left = Frame(
        _STEM_PDF_MARGIN, _STEM_PDF_MARGIN, col_width, frame_height,
        id="colL", leftPadding=0, rightPadding=5, topPadding=0, bottomPadding=0,
    )
    frame_right = Frame(
        _STEM_PDF_MARGIN + col_width + _STEM_PDF_GUTTER, _STEM_PDF_MARGIN, col_width, frame_height,
        id="colR", leftPadding=5, rightPadding=0, topPadding=0, bottomPadding=0,
    )

    def draw_header(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#1B5E20"))
        canvas.setFont("Helvetica-Bold", 15)
        canvas.drawString(_STEM_PDF_MARGIN, page_h - _STEM_PDF_MARGIN - 12, "Combination Stems")
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(colors.HexColor("#5a6b5e"))
        canvas.drawString(_STEM_PDF_MARGIN, page_h - _STEM_PDF_MARGIN - 25, f"{dept_name} \u00b7 Generated {generated_at}")
        canvas.setFont("Helvetica", 9)
        canvas.drawRightString(page_w - _STEM_PDF_MARGIN, page_h - _STEM_PDF_MARGIN - 12, f"Page {doc.page}")
        canvas.setStrokeColor(colors.HexColor("#2E7D32"))
        canvas.setLineWidth(1.1)
        canvas.line(_STEM_PDF_MARGIN, page_h - _STEM_PDF_MARGIN - 32, page_w - _STEM_PDF_MARGIN, page_h - _STEM_PDF_MARGIN - 32)
        canvas.restoreState()

    return PageTemplate(id="TwoCol", frames=[frame_left, frame_right], onPage=draw_header), col_width


def _stem_pdf_table(names, col_width):
    """S.No / Combination Stem table for one program, sized to fit one column."""
    no_col = 9 * mm
    stem_col = col_width - no_col - 5 * mm - 3  # minus the frame's inner padding + a hair of slack

    data = [["S.No", "Combination Stem"]]
    for i, name in enumerate(names, start=1):
        data.append([str(i), name])

    table = Table(data, colWidths=[no_col, stem_col], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2E7D32")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F8F1")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c4d8c8")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


@login_required
def export_combination_stems_pdf(request):
    """
    "Export" button on the Combination Stem tab: a PDF listing every
    combination stem in the department, one S.No/Stem table per program,
    flowed across two columns per page (see _stem_pdf_page_template).
    Department scoping matches groups_electives_page above.
    """
    detected_dept = detect_user_department(request.user)
    requested_dept_id = request.GET.get("department_id")
    active_dept_id = detected_dept.id if detected_dept else (
        int(requested_dept_id) if requested_dept_id and requested_dept_id.isdigit() else None
    )
    dept = detected_dept or (Department.objects.filter(id=active_dept_id).first() if active_dept_id else None)
    if not dept:
        return HttpResponse("Select a department first.", status=400, content_type="text/plain")

    stems = (
        SpecializationStem.objects
        .filter(category__department_id=dept.id)
        .select_related("category", "category__program")
        .order_by("category__program__name", "name")
    )

    by_program = {}
    program_order = []
    for stem in stems:
        pname = stem.category.program.name if stem.category and stem.category.program_id else "Unassigned"
        if pname not in by_program:
            by_program[pname] = []
            program_order.append(pname)
        by_program[pname].append(stem.name)

    generated_at = timezone.localtime().strftime("%d %b %Y, %H:%M")
    buffer = BytesIO()
    template, col_width = _stem_pdf_page_template(dept.name, generated_at)

    doc = BaseDocTemplate(
        buffer, pagesize=A4,
        leftMargin=_STEM_PDF_MARGIN, rightMargin=_STEM_PDF_MARGIN,
        topMargin=_STEM_PDF_MARGIN, bottomMargin=_STEM_PDF_MARGIN,
        title=f"Combination Stems - {dept.name}",
    )
    doc.addPageTemplates([template])

    styles = getSampleStyleSheet()
    program_style = ParagraphStyle(
        "ProgramHeading", parent=styles["Heading3"], fontName="Helvetica-Bold",
        fontSize=10.5, leading=13, textColor=colors.HexColor("#1B5E20"),
        spaceBefore=4, spaceAfter=4,
    )

    story = []
    if not program_order:
        story.append(Paragraph("No combination stems have been created for this department yet.", styles["Normal"]))
    else:
        for pname in program_order:
            story.append(Paragraph(pname, program_style))
            story.append(_stem_pdf_table(by_program[pname], col_width))
            story.append(Spacer(1, 10))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    safe_name = "".join(c if c.isalnum() else "_" for c in dept.name).strip("_") or "department"
    filename = f"combination_stems_{safe_name}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response