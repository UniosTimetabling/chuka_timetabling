# course_allocation/management/commands/map_shared_courses_to_stems.py
"""
Map already-lettered "compulsory — under every stem" shared courses (EDFO 111,
EPSC 111, EDCI 111, COSC 103, COMS 101, EAPE 411, EAPE 412, EPSC 431, EDFO 422,
EDCI 211, EDCI 203, EDFO 211, EDCI 322, EDCI 311, EPSC 311, EDFO 321, ...) onto
each program's Combination Stems (SpecializationStem) — one lettered section
PER stem.

BACKGROUND
----------
Bachelor of Education (Arts) and Bachelor of Education (Science) both teach
these courses to EVERY combination stem. Rather than one shared class for the
whole cohort, each has already been split into lettered sections with their
own lecturer (EDCI 111J, EDCI 111K, EDCI 111L, ... / EDFO 111I, EDFO 111K,
... one row per letter, each already a real CourseAllocation row with its own
lecturer). What is missing is the link from each letter to the specific
Combination Stem that letter belongs to, so:
  * each stem ends up with exactly ONE section of EDFO 111 (not zero, not two),
  * every OTHER shared course under the same letter (EPSC 111-x, EDCI 111-x,
    ...) is recognised as belonging to that SAME stem,
  * the scheduler/COD panel treats it exactly like every other stem course
    (course_allocation/course_group_planner.py's own machinery:
    StudentGroup + GroupingTemplate + GroupingTemplateStemAssignment +
    SpecializationStem.courses).

This command does NOT invent new lettered rows (they already exist with
their own lecturers) and does NOT rename/merge/delete anything — it only
attaches the existing rows to a StudentGroup (if not already grouped) and to
a SpecializationStem, and records the pairing as a GroupingTemplate /
GroupingTemplateStemAssignment so the COD panel and future auto-allocate
runs see the same picture.

TWO STEPS, BECAUSE THE PAIRING NEEDS A HUMAN'S EYES
----------------------------------------------------
There is no reliable machine signal that says "letter J of EDCI 111 IS the
Economics/Geography combination" — the letters are arbitrary, not named
after the combination. So this command:

  1. PLAN  (default; read-only): scans the given programs/course codes,
     finds every letter-group of these shared courses, finds every eligible
     Combination Stem for that program/year/semester, and writes a CSV you
     can open in Excel/Sheets. It also fills in a *suggested* pairing (ordered
     by number of students — the busiest stem gets the letter with the most
     students, and so on) in the `assign_stem_id` column, purely as a
     starting point.

  2. APPLY (--apply <path-to-the-csv>): re-reads that CSV — after you have
     reviewed/corrected the `assign_stem_id` column — and materializes it:
     StudentGroup + SpecializationStem + GroupingTemplate bookkeeping, all in
     one transaction, with a JSON backup written first. Leave a row's
     `assign_stem_id` blank to skip it (nothing is changed for that letter).

Safe to run more than once: already-correct rows are left alone and reported
as "already_assigned".

USAGE
-----
    # 1) Generate the plan (writes a CSV, changes nothing)
    python manage.py map_shared_courses_to_stems \\
        --out reports/edu_shared_courses_plan.csv

    # 2) Open the CSV, review/edit the assign_stem_id column, then:
    python manage.py map_shared_courses_to_stems \\
        --apply reports/edu_shared_courses_plan.csv

Options:
    --department NAME       Department to search (default: Education)
    --programs "A,B"        Comma-separated program names
                             (default: "Bachelor of Education (Arts),Bachelor of Education (Science)")
    --course-codes "A,B"    Comma-separated BASE course codes to look for
                             (default: the 16 Education "compulsory — under
                             every stem" codes listed above)
    --out PATH              Where to write the plan CSV (plan mode only)
    --apply PATH            Path to a (reviewed) plan CSV to execute
    --backup-dir PATH       Where --apply writes its pre-change JSON backup
    --created-by USERNAME   Attach this user as created_by on new rows
"""
import csv
import json
import os
from collections import OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from course_allocation.course_group_planner import (
    eligible_stems_for,
    _stem_eligible_for_year_semester,
)
from course_allocation.models import (
    CourseAllocation,
    GroupingTemplate,
    GroupingTemplateGroup,
    GroupingTemplateStemAssignment,
    SpecializationStem,
    StudentGroup,
)
from course_allocation.section_utils import parse_course_code
from department_management.models import Department
from program_management.models import Program

DEFAULT_DEPARTMENT = "Education"
DEFAULT_PROGRAMS = [
    "Bachelor of Education (Arts)",
    "Bachelor of Education (Science)",
]
# The Education "compulsory — under every stem" shared course codes named in
# the request ("EDC1 311" is treated as the obvious typo for "EDCI 311").
DEFAULT_COURSE_CODES = [
    "EDFO 111", "EPSC 111", "EDCI 111", "COSC 103", "COMS 101",
    "EAPE 411", "EAPE 412", "EPSC 431", "EDFO 422", "EDCI 211",
    "EDCI 203", "EDFO 211", "EDCI 322", "EDCI 311", "EPSC 311", "EDFO 321",
]

CSV_FIELDS = [
    "row_key", "program", "year", "semester", "intake", "letter",
    "course_codes", "allocation_ids", "lecturers", "students_on_letter",
    "suggested_stem_id", "suggested_stem_name", "stem_category",
    "stem_students_hint", "assign_stem_id", "note",
]


def _norm_code(code):
    return " ".join((code or "").upper().split())


class Command(BaseCommand):
    help = (
        "Map existing lettered shared/compulsory courses (EDFO 111, EDCI 111, "
        "...) onto each program's Combination Stems, one lettered section per "
        "stem. Default mode writes a reviewable CSV plan; --apply executes a "
        "(reviewed) plan CSV."
    )

    # ------------------------------------------------------------ arguments
    def add_arguments(self, parser):
        parser.add_argument("--department", default=DEFAULT_DEPARTMENT,
                             help=f"Department to search (default: {DEFAULT_DEPARTMENT}).")
        parser.add_argument("--programs", default=",".join(DEFAULT_PROGRAMS),
                             help="Comma-separated program names.")
        parser.add_argument("--course-codes", default=",".join(DEFAULT_COURSE_CODES),
                             help="Comma-separated BASE course codes to look for.")
        parser.add_argument("--out", default=None,
                             help="Output CSV path for the plan (plan mode only). "
                                  "Default: reports/shared_courses_to_stems_plan_<timestamp>.csv")
        parser.add_argument("--apply", dest="apply_path", default=None,
                             help="Path to a reviewed plan CSV to execute (switches to apply mode).")
        parser.add_argument("--backup-dir", default=None,
                             help="Where --apply writes its pre-change JSON backup "
                                  "(default: <BASE_DIR>/backups/shared_courses_to_stems).")
        parser.add_argument("--created-by", default=None,
                             help="Username to attach as created_by on new StudentGroup/"
                                  "GroupingTemplate rows (optional).")

    # ------------------------------------------------------------------ run
    def handle(self, *args, **opts):
        self.dept = self._resolve_department(opts["department"])
        self.programs = self._resolve_programs(opts["programs"], self.dept)
        self.course_bases = [_norm_code(c) for c in opts["course_codes"].split(",") if c.strip()]
        self.created_by = self._resolve_user(opts["created_by"])

        if opts["apply_path"]:
            self._run_apply(opts["apply_path"], opts["backup_dir"])
        else:
            self._run_plan(opts["out"])

    # ------------------------------------------------------------ resolving
    def _resolve_department(self, name):
        try:
            return Department.objects.get(name__iexact=name.strip())
        except Department.DoesNotExist:
            near = list(Department.objects.filter(name__icontains=name[:4]).values_list("name", flat=True))
            raise CommandError(f"Department '{name}' not found." + (f" Similar: {near}" if near else ""))

    def _resolve_programs(self, csv_names, dept):
        names = [n.strip() for n in csv_names.split(",") if n.strip()]
        programs = []
        missing = []
        for n in names:
            p = Program.objects.filter(name__iexact=n, department=dept).first() \
                or Program.objects.filter(name__iexact=n).first()
            if p:
                programs.append(p)
            else:
                missing.append(n)
        if missing:
            raise CommandError(f"Program(s) not found: {missing}")
        return programs

    def _resolve_user(self, username):
        if not username:
            return None
        try:
            return User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"User '{username}' not found.")

    # ------------------------------------------------------------------
    # PLAN
    # ------------------------------------------------------------------
    def _discover_cells(self):
        """
        Returns an OrderedDict keyed by (program_id, year, semester, intake)
        -> {"program": Program, "letters": {letter: [CourseAllocation, ...]}}
        for every target course code found under a lettered section for the
        target programs. Rows with NO letter (still shared/unsplit) are
        reported separately so nothing silently falls through the cracks.
        """
        program_ids = [p.id for p in self.programs]
        programs_by_id = {p.id: p for p in self.programs}

        qs = (
            CourseAllocation.objects
            .filter(program_id__in=program_ids, is_elective=False)
            .select_related("program_course", "program", "lecturer", "student_group")
        )

        cells = OrderedDict()
        unlettered = []
        skipped_other_codes = 0
        for ca in qs.iterator():
            base = _norm_code(ca.program_course.course_code)
            if base not in self.course_bases:
                # Also try the *allocation's own* course_code base, in case
                # program_course and course_code diverge (e.g. after a
                # lettering pass already touched course_code only).
                alloc_base = _norm_code(parse_course_code(ca.course_code)[0])
                if alloc_base not in self.course_bases:
                    skipped_other_codes += 1
                    continue
            _base_code, letter, _section = parse_course_code(ca.course_code)
            if not letter:
                unlettered.append(ca)
                continue
            key = (ca.program_id, ca.program_course.year, ca.program_course.semester, ca.intake)
            cell = cells.setdefault(key, {
                "program": programs_by_id[ca.program_id],
                "letters": defaultdict(list),
            })
            cell["letters"][letter.upper()].append(ca)

        return cells, unlettered

    def _stem_student_hint(self, stem):
        """Best-effort headcount for a stem: the largest number_of_students
        already recorded on any of its OWN (non-shared) courses. 0 if the
        stem has no courses yet."""
        vals = list(stem.courses.values_list("number_of_students", flat=True))
        return max(vals) if vals else 0

    def _run_plan(self, out_path):
        cells, unlettered = self._discover_cells()

        if not cells:
            self.stdout.write(self.style.WARNING(
                "No lettered CourseAllocation rows found for the given programs/course codes."
            ))
            if unlettered:
                self.stdout.write(f"{len(unlettered)} matching row(s) exist but are NOT lettered yet "
                                   f"(still one shared row) — split those into letters first.")
            return

        if not out_path:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = Path(getattr(settings, "BASE_DIR", ".")) / "reports"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = str(out_dir / f"shared_courses_to_stems_plan_{ts}.csv")
        else:
            out_dir = Path(out_path).parent
            if str(out_dir) not in ("", "."):
                out_dir.mkdir(parents=True, exist_ok=True)

        rows = []
        for (program_id, year, semester, intake), cell in cells.items():
            program = cell["program"]
            stems = list(eligible_stems_for(program, year, semester))
            letters = sorted(cell["letters"].items(), key=lambda kv: kv[0])

            # Sort letters by their own headcount (desc), stems by their own
            # headcount hint (desc) -- pair the busiest letter with the
            # busiest stem, purely as a *suggestion* for the human reviewer.
            def letter_students(item):
                _l, allocs = item
                vals = [a.number_of_students for a in allocs]
                return max(vals) if vals else 0

            letters_sorted = sorted(letters, key=letter_students, reverse=True)
            stems_sorted = sorted(stems, key=self._stem_student_hint, reverse=True)

            note_cell = ""
            if len(letters_sorted) != len(stems_sorted):
                note_cell = (
                    f"MISMATCH: {len(letters_sorted)} letter(s) vs {len(stems_sorted)} "
                    f"eligible stem(s) -- review pairing by hand."
                )
            if not stems_sorted:
                note_cell = "No Combination Stems exist yet for this program/year/semester."

            for i, (letter, allocs) in enumerate(letters_sorted):
                allocs_sorted = sorted(allocs, key=lambda a: a.course_code)
                suggested_stem = stems_sorted[i] if i < len(stems_sorted) else None
                row = {
                    "row_key": f"{program_id}|{year}|{semester}|{intake}|{letter}",
                    "program": program.name,
                    "year": year,
                    "semester": semester,
                    "intake": intake,
                    "letter": letter,
                    "course_codes": ";".join(a.course_code for a in allocs_sorted),
                    "allocation_ids": ";".join(str(a.id) for a in allocs_sorted),
                    "lecturers": ";".join(
                        (a.lecturer.display_name if getattr(a, "lecturer", None) else "Unassigned")
                        for a in allocs_sorted
                    ),
                    "students_on_letter": letter_students((letter, allocs)),
                    "suggested_stem_id": suggested_stem.id if suggested_stem else "",
                    "suggested_stem_name": suggested_stem.name if suggested_stem else "",
                    "stem_category": suggested_stem.category.name if suggested_stem else "",
                    "stem_students_hint": self._stem_student_hint(suggested_stem) if suggested_stem else "",
                    "assign_stem_id": suggested_stem.id if suggested_stem else "",
                    "note": note_cell,
                }
                rows.append(row)

        with open(out_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

        self._h(f"PLAN written: {out_path}")
        self.stdout.write(f"{len(rows)} letter-section row(s) across {len(cells)} program/year/semester/"
                           f"intake cell(s).")
        mismatches = [r for r in rows if r["note"]]
        if mismatches:
            self.stdout.write(self.style.WARNING(
                f"{len(mismatches)} row(s) flagged for review (letter/stem count mismatch or no stems yet)."
            ))
        if unlettered:
            self.stdout.write(self.style.WARNING(
                f"{len(unlettered)} matching CourseAllocation row(s) are NOT lettered yet "
                f"(still one shared row) and were left out of the plan."
            ))
        self.stdout.write(
            "\nOpen the CSV, review/correct the 'assign_stem_id' column for each row "
            "(blank = skip that row), then run:\n"
            f"    python manage.py map_shared_courses_to_stems --apply {out_path}"
        )

    # ------------------------------------------------------------------
    # APPLY
    # ------------------------------------------------------------------
    def _h(self, title):
        self.stdout.write("")
        self.stdout.write("=" * 100)
        self.stdout.write(title)
        self.stdout.write("=" * 100)

    def _write_backup(self, allocations, backup_dir):
        backup_dir = Path(backup_dir) if backup_dir else (
            Path(getattr(settings, "BASE_DIR", ".")) / "backups" / "shared_courses_to_stems"
        )
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = backup_dir / ts
        run_dir.mkdir(parents=True, exist_ok=True)
        data = serializers.serialize("json", allocations)
        (run_dir / "courseallocations.json").write_text(data)
        return run_dir

    @transaction.atomic
    def _apply_rows(self, rows):
        """Returns (results, manifest) — results is a list of per-row outcome
        strings, manifest is {"student_groups": [...], "grouping_templates": [...]} of
        NEW ids created this run (for backup/rollback bookkeeping)."""
        results = []
        manifest = {"student_groups": [], "grouping_template_groups": [], "stem_assignments": []}
        template_cache = {}

        for row in rows:
            program_id = int(row["program_id"])
            program = row["program"]
            year, semester = int(row["year"]), int(row["semester"])
            intake, letter = row["intake"], row["letter"]
            assign_stem_id = (row.get("assign_stem_id") or "").strip()
            alloc_ids = [int(x) for x in row["allocation_ids"].split(";") if x.strip()]
            label = f"{program.name} Y{year}S{semester} ({intake}) letter {letter}"

            if not assign_stem_id:
                results.append(f"SKIPPED (no assign_stem_id): {label}")
                continue

            try:
                stem = SpecializationStem.objects.select_related("category", "category__program").get(
                    id=int(assign_stem_id)
                )
            except (SpecializationStem.DoesNotExist, ValueError):
                results.append(f"ERROR: stem id '{assign_stem_id}' not found -- {label}")
                continue

            if stem.category.program_id != program_id:
                results.append(
                    f"ERROR: stem '{stem.name}' belongs to program '{stem.category.program.name}', "
                    f"not '{program.name}' -- {label}"
                )
                continue
            if not _stem_eligible_for_year_semester(stem, year, semester):
                results.append(f"ERROR: stem '{stem.name}' is not eligible for Y{year}S{semester} -- {label}")
                continue

            allocations = list(CourseAllocation.objects.filter(id__in=alloc_ids).select_related("program_course"))
            found_ids = {a.id for a in allocations}
            missing = set(alloc_ids) - found_ids
            if missing:
                results.append(f"WARNING: allocation id(s) {sorted(missing)} no longer exist -- {label}")

            group, group_created = StudentGroup.objects.get_or_create(
                program=program, year=year, semester=semester, intake=intake, letter=letter,
                defaults={"name": f"Group {letter}", "created_by": self.created_by},
            )
            if group_created:
                manifest["student_groups"].append(group.id)

            touched = []
            for ca in allocations:
                if ca.program_id != program_id:
                    results.append(f"ERROR: CourseAllocation {ca.id} '{ca.course_code}' is on a "
                                    f"different program -- skipped ({label})")
                    continue
                if ca.student_group_id and ca.student_group_id != group.id:
                    results.append(
                        f"ERROR: CourseAllocation {ca.id} '{ca.course_code}' is already tied to a "
                        f"DIFFERENT student group (id={ca.student_group_id}) -- left untouched ({label})"
                    )
                    continue
                if ca.student_group_id != group.id:
                    ca.student_group = group
                    ca.save(update_fields=["student_group"])
                touched.append(ca)

            if not touched:
                results.append(f"NOTHING TO DO: {label}")
                continue

            already_in_stem = set(stem.courses.filter(id__in=[c.id for c in touched]).values_list("id", flat=True))
            new_to_stem = [c for c in touched if c.id not in already_in_stem]
            if new_to_stem:
                stem.courses.add(*new_to_stem)
            pcs = {c.program_course for c in touched}
            stem.program_courses.add(*pcs)
            for c in touched:
                c.refresh_primary_stem_pointer()

            template_key = program_id
            template = template_cache.get(template_key)
            if template is None:
                template, _ = GroupingTemplate.objects.get_or_create(
                    program=program, year=year, semester=semester, intake=intake,
                    defaults={"scope": GroupingTemplate.SCOPE_ALL, "created_by": self.created_by},
                )
                template_cache[template_key] = template
            grp_def, grp_def_created = GroupingTemplateGroup.objects.get_or_create(
                template=template, letter=letter,
            )
            if grp_def_created:
                manifest["grouping_template_groups"].append(grp_def.id)
            _, sa_created = GroupingTemplateStemAssignment.objects.update_or_create(
                template=template, group=grp_def, defaults={"stem": stem, "created_by": self.created_by},
            )
            if sa_created:
                manifest["stem_assignments"].append(f"{template.id}:{grp_def.id}")

            results.append(
                f"OK: {label} -> stem '{stem.name}' "
                f"({len(new_to_stem)} course(s) newly linked, {len(touched) - len(new_to_stem)} already linked)"
            )

        return results, manifest

    def _run_apply(self, csv_path, backup_dir):
        if not os.path.exists(csv_path):
            raise CommandError(f"Plan CSV not found: {csv_path}")

        programs_by_name = {p.name: p for p in self.programs}
        rows = []
        all_alloc_ids = set()
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                if not raw.get("row_key"):
                    continue
                program = programs_by_name.get(raw["program"])
                if program is None:
                    program = Program.objects.filter(name__iexact=raw["program"]).first()
                if program is None:
                    raise CommandError(f"Program '{raw['program']}' from the CSV is not one of "
                                        f"--programs and could not be resolved. Row: {raw['row_key']}")
                raw["program_id"] = program.id
                raw["program"] = program
                if raw.get("allocation_ids"):
                    all_alloc_ids.update(int(x) for x in raw["allocation_ids"].split(";") if x.strip())
                rows.append(raw)

        if not rows:
            self.stdout.write(self.style.WARNING("The CSV has no data rows -- nothing to apply."))
            return

        backup_allocations = list(CourseAllocation.objects.filter(id__in=all_alloc_ids))
        run_dir = self._write_backup(backup_allocations, backup_dir)

        results, manifest = self._apply_rows(rows)

        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        self._h(f"APPLY complete -- backup: {run_dir}")
        for line in results:
            if line.startswith("ERROR"):
                self.stdout.write(self.style.ERROR(line))
            elif line.startswith(("WARNING", "SKIPPED")):
                self.stdout.write(self.style.WARNING(line))
            else:
                self.stdout.write(self.style.SUCCESS(line))

        n_ok = sum(1 for l in results if l.startswith("OK"))
        n_err = sum(1 for l in results if l.startswith("ERROR"))
        self.stdout.write(f"\n{n_ok} letter-group(s) mapped successfully, {n_err} error(s).")
        self.stdout.write(f"Manifest (ids created this run): {run_dir / 'manifest.json'}")
