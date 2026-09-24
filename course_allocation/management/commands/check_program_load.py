"""
Flags two kinds of curriculum/workload problems into one Excel workbook:

  Sheet 1 "Overloaded Program-Years":
    (a) Program-Year-Semesters with MORE than --threshold (default 10)
        courses, that have NO student-group split and NO electives/stems
        to spread the load — i.e. every student in that cohort must take
        all of them at once, no escape valve.
    (b) Program-Year-Semesters that DO have electives and/or specialization
        stems, but where even the SMALLEST possible combination (the
        smallest stem in each specialization category, one course per
        elective/selection group, plus every plain compulsory course) still
        adds up to more than --threshold units. These are overloaded no
        matter what a student picks.

  Sheet 2 "Lecturer Overload":
    Lecturers assigned to more than --lecturer-threshold (default 6)
    distinct courses, with the course list alongside each one.

  Combined/merged course groups count as ONE unit everywhere in this
  report (both sheets) — if two allocations are the same physical class
  merged together (see CombinedCourseGroup), they represent one teaching
  commitment / one curriculum unit, not two, however many department rows
  point at it.

Usage:
    python manage.py check_program_load
    python manage.py check_program_load --output /path/to/file.xlsx
    python manage.py check_program_load --threshold 12 --lecturer-threshold 8
"""
import os
from collections import defaultdict
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from course_allocation.models import CourseAllocation, CombinedCourseGroup

try:
    import pandas as pd
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
except ImportError:
    pd = None


class Command(BaseCommand):
    help = (
        "Find program-years overloaded with courses (no student-group split "
        "and no electives/stems to spread the load, or still overloaded even "
        "after the smallest possible elective/stem picks), and lecturers "
        "carrying more than N distinct courses. Writes both to one Excel "
        "workbook, one sheet each. Merged/combined course groups always "
        "count as ONE unit."
    )

    def add_arguments(self, parser):
        parser.add_argument("--output", type=str, default="",
                             help="Output .xlsx path. Defaults to ./exports/program_load_report_<timestamp>.xlsx")
        parser.add_argument("--threshold", type=int, default=10,
                             help="Flag a program-year-semester if its course count exceeds this (default 10).")
        parser.add_argument("--lecturer-threshold", type=int, default=6,
                             help="Flag a lecturer if their distinct course count exceeds this (default 6).")

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        if pd is None:
            raise CommandError(
                "This command needs pandas + openpyxl: "
                "pip install pandas openpyxl --break-system-packages"
            )

        threshold = options["threshold"]
        lecturer_threshold = options["lecturer_threshold"]
        out_path = options["output"] or self._default_path()
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

        allocations = list(CourseAllocation.objects.select_related(
            "program", "program__department", "program__department__faculty",
            "program_course", "lecturer", "student_group",
        ).prefetch_related("specialization_stems", "specialization_stems__category", "selection_groups"))

        # ── Canonicalize merged/combined course groups: every member of the
        #    same CombinedCourseGroup collapses to ONE representative
        #    allocation id wherever we count "how many courses" — a merged
        #    class is one teaching commitment, not one per department that
        #    happens to share it.
        canonical_id = {}
        for group in CombinedCourseGroup.objects.prefetch_related("allocations"):
            member_ids = list(group.allocations.values_list("id", flat=True))
            if not member_ids:
                continue
            rep = group.primary_allocation_id or member_ids[0]
            for mid in member_ids:
                canonical_id[mid] = rep

        def canon(a):
            return canonical_id.get(a.id, a.id)

        # ══════════════════════════════════════════════════════════════
        # SHEET 1 — Overloaded Program-Years
        # ══════════════════════════════════════════════════════════════
        py_groups = defaultdict(list)
        for a in allocations:
            if not a.program or not a.program_course:
                continue
            # Normalize intake casing (real data had both "Normal" and
            # "NORMAL" for the same thing) so they don't split one cohort
            # into two separate groups here.
            intake_norm = (a.intake or "").strip().capitalize()
            key = (a.program.name, a.program_course.year, a.program_course.semester, intake_norm)
            py_groups[key].append(a)

        flagged_rows = []
        for (program, year, semester, intake), allocs in py_groups.items():
            # Dedup to one representative row per merged group.
            by_canon = {}
            for a in allocs:
                by_canon.setdefault(canon(a), a)  # first row wins as representative
            unit_reps = list(by_canon.values())
            total_units = len(unit_reps)

            if total_units <= threshold:
                continue  # can't be overloaded either way

            grouped_units = [a for a in unit_reps if a.student_group_id]
            elective_units = [a for a in unit_reps if a.is_elective or a.selection_groups.exists()]
            stem_units = [a for a in unit_reps if a.specialization_stems.exists()]
            # A course is exactly one of: grouped / elective / stem / plain
            # shared-compulsory (the model's own clean() enforces a course
            # can't be BOTH grouped and elective/stem), so this partition is
            # clean with no double-counting.
            shared_compulsory = [
                a for a in unit_reps
                if not a.student_group_id and not a.is_elective
                and not a.selection_groups.exists() and not a.specialization_stems.exists()
            ]
            has_student_groups = len(grouped_units) > 0
            has_electives = len(elective_units) > 0
            has_stems = len(stem_units) > 0

            dept = unit_reps[0].program.department if unit_reps[0].program.department_id else None
            course_list = "; ".join(sorted({a.course_code for a in unit_reps}))

            compulsory_count = len(shared_compulsory)

            # Specialization stems: within each category, a student picks
            # ONE stem and then takes EVERY course in it — so the best
            # (smallest) case per category is its smallest stem.
            cat_stem_sizes = defaultdict(lambda: defaultdict(int))
            for a in stem_units:
                for stem in a.specialization_stems.all():
                    cat_stem_sizes[stem.category_id][stem.id] += 1
            min_stem_add = sum(min(sizes.values()) for sizes in cat_stem_sizes.values())

            # Selection/elective groups: a student picks exactly ONE course
            # per group, so each distinct group adds exactly 1. Electives
            # with no group at all can't be assumed optional, so each adds
            # 1 on its own.
            selection_group_ids = set()
            for a in elective_units:
                for sg in a.selection_groups.all():
                    selection_group_ids.add(sg.id)
            ungrouped_electives = [a for a in elective_units if not a.selection_groups.exists()]
            elective_add = len(selection_group_ids) + len(ungrouped_electives)

            # Base load: every unit that applies to EVERY student regardless
            # of which Student Group they're in (shared compulsory courses,
            # plus the elective/stem picks above, which are also shared
            # across groups in this data model).
            base_load = compulsory_count + min_stem_add + elective_add

            # Student Groups: a student belongs to exactly ONE group, so
            # only THAT group's own courses count toward their load — never
            # every group's courses added together (that was the bug).
            if grouped_units:
                per_group_counts = defaultdict(int)
                per_group_names = {}
                for a in grouped_units:
                    per_group_counts[a.student_group_id] += 1
                    per_group_names[a.student_group_id] = a.student_group.name
                lightest_gid = min(per_group_counts, key=lambda gid: per_group_counts[gid])
                heaviest_gid = max(per_group_counts, key=lambda gid: per_group_counts[gid])
                min_load = base_load + per_group_counts[lightest_gid]
                max_load = base_load + per_group_counts[heaviest_gid]
                heaviest_group_name = per_group_names[heaviest_gid]
                group_breakdown = ", ".join(
                    f"{per_group_names[gid]}: {cnt}" for gid, cnt in sorted(per_group_counts.items())
                )
            else:
                min_load = max_load = base_load
                heaviest_group_name = None
                group_breakdown = ""

            # Flag if the WORST-off student (heaviest group, if any) still
            # ends up over threshold even with every other choice minimized.
            if max_load > threshold:
                reason_parts = [f"{compulsory_count} shared compulsory"]
                if min_stem_add:
                    reason_parts.append(f"{min_stem_add} from smallest stem pick(s)")
                if elective_add:
                    reason_parts.append(f"{elective_add} elective pick(s)")
                if grouped_units:
                    reason_parts.append(
                        f"+ their own Student Group's courses only (not other groups') "
                        f"— heaviest is \"{heaviest_group_name}\" with "
                        f"{per_group_counts[heaviest_gid]} [{group_breakdown}]"
                    )
                    reason = (
                        f"Worst case (student in \"{heaviest_group_name}\") = {max_load} units "
                        f"({' + '.join(reason_parts)}). Best case (lightest group) = {min_load}."
                    )
                elif has_electives or has_stems:
                    reason = (
                        f"Even picking the smallest stem(s) and one course per elective "
                        f"group, a student still takes {max_load} units ({' + '.join(reason_parts)})."
                    )
                else:
                    reason = (
                        f"{max_load} compulsory units, no student-group split, "
                        f"no electives/stems to spread the load — every student "
                        f"takes all {max_load} at once."
                    )

                flagged_rows.append({
                    "Program": program, "Department": dept.name if dept else "",
                    "Year": year, "Semester": semester, "Intake": intake,
                    "Total Units": total_units,
                    "Has Student Groups": "Yes" if has_student_groups else "No",
                    "Has Electives": "Yes" if has_electives else "No",
                    "Has Stems": "Yes" if has_stems else "No",
                    "Min Load After Choices": min_load,
                    "Max Load (Heaviest Group)": max_load,
                    "Heaviest Student Group": heaviest_group_name or "",
                    "Reason": reason,
                    "Courses": course_list,
                })

        flagged_rows.sort(key=lambda r: -r["Max Load (Heaviest Group)"])

        # ══════════════════════════════════════════════════════════════
        # SHEET 2 — Lecturer Overload
        # ══════════════════════════════════════════════════════════════
        lecturer_units = defaultdict(dict)  # lecturer name -> {canon_id: allocation}
        for a in allocations:
            if not a.lecturer_id:
                continue
            name = a.lecturer.display_name
            lecturer_units[name].setdefault(canon(a), a)

        lecturer_rows = []
        for lecturer, units in lecturer_units.items():
            allocs = list(units.values())
            if len(allocs) > lecturer_threshold:
                course_list = "; ".join(sorted({a.course_code for a in allocs}))
                program_list = "; ".join(sorted({a.program.name for a in allocs if a.program_id}))
                dept_list = "; ".join(sorted({
                    a.department.name for a in allocs if a.department_id
                }))
                lecturer_rows.append({
                    "Lecturer": lecturer,
                    "Distinct Course Count": len(allocs),
                    "Departments": dept_list,
                    "Programs": program_list,
                    "Courses": course_list,
                })
        lecturer_rows.sort(key=lambda r: -r["Distinct Course Count"])

        # ══════════════════════════════════════════════════════════════
        # Write workbook
        # ══════════════════════════════════════════════════════════════
        cols1 = ["Program", "Department", "Year", "Semester", "Intake", "Total Units",
                 "Has Student Groups", "Has Electives", "Has Stems",
                 "Min Load After Choices", "Max Load (Heaviest Group)", "Heaviest Student Group",
                 "Reason", "Courses"]
        cols2 = ["Lecturer", "Distinct Course Count", "Departments", "Programs", "Courses"]

        df1 = pd.DataFrame(flagged_rows, columns=cols1)
        df2 = pd.DataFrame(lecturer_rows, columns=cols2)

        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            df1.to_excel(writer, sheet_name="Overloaded Program-Years", index=False)
            df2.to_excel(writer, sheet_name="Lecturer Overload", index=False)

        self._style_workbook(out_path, {
            "Overloaded Program-Years": cols1,
            "Lecturer Overload": cols2,
        })

        self.stdout.write(self.style.SUCCESS(
            f"Flagged {len(flagged_rows)} overloaded program-year-semester(s) and "
            f"{len(lecturer_rows)} overloaded lecturer(s) (> {lecturer_threshold} courses)."
        ))
        self.stdout.write(self.style.SUCCESS(f"Wrote {out_path}"))

    # ------------------------------------------------------------------
    def _style_workbook(self, path, sheet_columns):
        """Bold header row, frozen header, reasonable column widths."""
        import openpyxl
        wb = openpyxl.load_workbook(path)
        header_fill = PatternFill(start_color="1E7145", end_color="1E7145", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)

        for sheet_name, cols in sheet_columns.items():
            ws = wb[sheet_name]
            ws.freeze_panes = "A2"
            for col_idx, col_name in enumerate(cols, start=1):
                cell = ws.cell(row=1, column=col_idx)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                width = 60 if col_name in ("Reason", "Courses") else (28 if col_name in ("Program", "Programs", "Departments") else 16)
                ws.column_dimensions[get_column_letter(col_idx)].width = width
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    cell.alignment = Alignment(vertical="top", wrap_text=True)

        wb.save(path)

    def _default_path(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join("exports", f"program_load_report_{ts}.xlsx")
