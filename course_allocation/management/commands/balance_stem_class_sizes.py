# course_allocation/management/commands/balance_stem_class_sizes.py
"""
Top up Combination Stem student numbers so every lettered course group
(e.g. EDFO 111-A, EAPE 411-B) totals at least N students (default 150).

The model
---------
* A "class" is one lettered group of a course: EDFO 111-A, EAPE 411-B, ...
  (same allocation set + same base code + letter + section + intake).
* A class is mapped to one or more Combination Stems (the "Stems mapped"
  column on /groups-electives/). Its size is the SUM of those stems'
  student counts (StemStudentCount, per stem + year + semester), plus any
  plain "(no stem)" row of the same class.
* If a class is below the target, the shortfall is split EQUALLY across the
  stems it is mapped to (remainder, if any, to the stems with the smallest
  counts first). Example: two stems with 70 and 50 (=120), target 150,
  shortfall 30 -> +15 each -> 85 and 65 (=150).
* Numbers only ever go UP. Nothing is ever reduced.
* A stem is shared by every course mapped to it, so raising it for one class
  also lifts every other class using that stem. Classes with the FEWEST
  stems are handled first (they are the least flexible), which keeps
  overshoot to a minimum. A class whose stems are shared with others can
  therefore end above the target; it never ends below it.

What gets written (only with --apply, in one transaction)
---------------------------------------------------------
1. StemStudentCount  (what /groups-electives/ "Student numbers" shows) for
   every stem/year/semester that takes part.
2. CourseAllocation.number_of_students for every core course row attached to
   a stem whose number changed = sum of that row's stems' counts (a row in
   one stem simply gets that stem's count).

Nested elective-pool alternatives are ignored (same rule as the existing
"Student numbers" action), and stems that have no lettered class below the
target are left alone.

Usage
-----
    python manage.py balance_stem_class_sizes                       # dry run
    python manage.py balance_stem_class_sizes --apply
    python manage.py balance_stem_class_sizes --target 150 --department "Educational Psychology" --apply
    python manage.py balance_stem_class_sizes --allocation-set 12 --report /tmp/stem_balance.csv
"""
import csv
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from course_allocation.models import (
    CourseAllocation, SpecializationStem, StemStudentCount,
)
from course_allocation.section_utils import parse_course_code


# Only these courses are balanced by default (the Course Groups on
# /groups-electives/). Pass --courses "A,B" to override or --all-courses.
DEFAULT_COURSES = [
    "EAPE 411", "EAPE 412",
    "EDCI 111", "EDCI 203", "EDCI 211", "EDCI 311", "EDCI 322",
    "EDFO 111", "EDFO 211", "EDFO 321", "EDFO 422",
    "EPSC 111", "EPSC 222", "EPSC 311", "EPSC 431",
]


def _norm_base(code):
    return (parse_course_code(code)[0] or "").replace(" ", "").upper()


# ─────────────────────────────────────────────────────────────────────────
# Pure planner (no Django) — unit-testable on its own
# ─────────────────────────────────────────────────────────────────────────
def plan_top_up(classes, counts, target):
    """
    classes: {class_key: {"stems": set(stem_term), "fixed": int}}
    counts:  {stem_term: int}   current headcount per (stem, year, semester)
    target:  int

    Returns (new_counts, class_report) where class_report is a list of dicts
    (one per class with at least one stem) in processing order.
    Only ever increases counts.
    """
    new = dict(counts)
    order = sorted(
        (k for k, c in classes.items() if c["stems"]),
        key=lambda k: (len(classes[k]["stems"]), str(k)),
    )
    report = []
    for key in order:
        info = classes[key]
        stems = sorted(info["stems"], key=lambda s: (new.get(s, 0), str(s)))
        before = sum(new.get(s, 0) for s in stems) + info["fixed"]
        deficit = target - before
        if deficit > 0:
            base, rem = divmod(deficit, len(stems))
            for i, s in enumerate(stems):
                new[s] = new.get(s, 0) + base + (1 if i < rem else 0)
        after = sum(new.get(s, 0) for s in stems) + info["fixed"]
        report.append({"key": key, "stems": len(stems), "before": before, "after": after})
    return new, report


# ─────────────────────────────────────────────────────────────────────────
def _class_key(ca):
    base, letter, section = parse_course_code(ca.course_code)
    return (
        ca.allocation_set_id,
        ca.department_id,
        (base or "").replace(" ", "").upper(),
        letter or "",
        section or ca.section_number or 1,
        ca.intake,
        bool(ca.is_evening_weekend),
    )


def _label(key):
    _set, _dept, base, letter, section, _intake, _eve = key
    return f"{base}-{letter}" + (str(section) if section > 1 else "") if letter else base


class Command(BaseCommand):
    help = ("Raise combination-stem student counts (StemStudentCount + the "
            "CourseAllocation rows in each stem) so every lettered course group "
            "totals at least --target students. Dry-run unless --apply.")

    def add_arguments(self, parser):
        parser.add_argument("--target", type=int, default=150,
                            help="Minimum students per lettered class (default 150).")
        parser.add_argument("--department", help="Only stems whose category belongs to this department (name).")
        parser.add_argument("--allocation-set", type=int, help="Only stems in this AllocationSet id.")
        parser.add_argument("--courses", help="Comma-separated base course codes to balance "
                            "(default: the EAPE/EDCI/EDFO/EPSC list on /groups-electives/).")
        parser.add_argument("--all-courses", action="store_true",
                            help="Balance every stem-mapped course, not just the default list.")
        parser.add_argument("--push-all-rows", action="store_true",
                            help="Also rewrite number_of_students on OTHER courses' rows that sit in a raised stem "
                                 "(default: only the listed courses' rows are touched).")
        parser.add_argument("--report", help="Write a CSV report (classes + stems) to this path.")
        parser.add_argument("--apply", action="store_true", help="Write changes. Without it nothing is saved.")

    # ------------------------------------------------------------------
    def handle(self, *args, **opts):
        target = opts["target"]
        if target < 1:
            raise CommandError("--target must be positive.")

        stems_qs = (SpecializationStem.objects
                    .select_related("category", "category__program", "category__department")
                    .order_by("category__program__name", "name"))
        if opts["department"]:
            stems_qs = stems_qs.filter(category__department__name__iexact=opts["department"])
            if not stems_qs.exists():
                raise CommandError(f"No stems found for department '{opts['department']}'.")
        if opts["allocation_set"]:
            stems_qs = stems_qs.filter(category__allocation_set_id=opts["allocation_set"])

        classes = defaultdict(lambda: {"stems": set(), "fixed": 0, "rows": []})
        counts = {}                      # stem_term -> current headcount
        term_rows = defaultdict(list)    # stem_term -> [CourseAllocation core rows]
        row_terms = defaultdict(set)     # ca.id -> {stem_term it is core in}
        stem_names = {}
        skipped_semester = 0

        for stem in stems_qs:
            pool_ids = set()
            for pool in stem.elective_groups.all():
                pool_ids.update(pool.courses.values_list("id", flat=True))
            core = (stem.courses.exclude(id__in=pool_ids)
                    .exclude(program_course__isnull=True)
                    .select_related("program_course"))

            saved = {(c.year, c.semester): c.number_of_students for c in stem.student_counts.all()}
            by_term = defaultdict(list)
            for ca in core:
                pc = ca.program_course
                if not pc.year or not pc.semester:
                    continue
                if pc.semester not in (1, 2):
                    skipped_semester += 1
                    continue
                by_term[(pc.year, pc.semester)].append(ca)

            for (year, sem), rows in by_term.items():
                term = (stem.id, year, sem)
                stem_names[term] = f"{stem.category.program.name} — {stem.name} (Y{year} S{sem})"
                if (year, sem) in saved:                 # saved value wins (see docstring of re-runs)
                    counts[term] = saved[(year, sem)]
                else:
                    agreed = {r.number_of_students for r in rows}
                    counts[term] = agreed.pop() if len(agreed) == 1 else 0
                for ca in rows:
                    term_rows[term].append(ca)
                    row_terms[ca.id].add(term)
                    k = _class_key(ca)
                    classes[k]["stems"].add(term)
                    classes[k]["rows"].append(ca)

        if not opts["all_courses"]:
            wanted = {_norm_base(c) for c in (opts["courses"].split(",") if opts["courses"] else DEFAULT_COURSES)}
            classes = {k: v for k, v in classes.items() if k[2] in wanted}
        else:
            wanted = None
        if not classes:
            self.stdout.write("No stem-mapped classes found for the selected courses.")
            return

        # Stems that take part = only those mapped to a selected class.
        involved = {t for c in classes.values() for t in c["stems"]}
        all_counts = dict(counts)
        counts = {t: n for t, n in counts.items() if t in involved}

        # Plain "(no stem)" rows of the same class count towards its total as-is.
        pc_ids = {ca.program_course_id for c in classes.values() for ca in c["rows"]}
        set_ids = {k[0] for k in classes}
        plain = (CourseAllocation.objects
                 .filter(program_course_id__in=pc_ids, allocation_set_id__in=set_ids,
                         specialization_stems__isnull=True, specialization_stem__isnull=True)
                 .distinct())
        for ca in plain:
            k = _class_key(ca)
            if k in classes:
                classes[k]["fixed"] += ca.number_of_students

        new_counts, class_report = plan_top_up(classes, counts, target)

        # ── Row totals for every core row attached to a changed stem-term ──
        changed_terms = {t for t in new_counts if new_counts[t] != counts.get(t, 0)}
        row_updates = {}   # ca.id -> (old, new)
        row_obj = {}
        # Sync rows for EVERY participating stem-term, not only the ones whose
        # StemStudentCount changed. Otherwise a row that drifted out of sync
        # with its (already high enough) StemStudentCount is never repaired —
        # the class then looks "already >= target" here while the timetable
        # still shows the stale per-row number.
        for term in involved:
            for ca in term_rows[term]:
                if wanted is not None and not opts["push_all_rows"] and _class_key(ca)[2] not in wanted:
                    continue
                row_obj[ca.id] = ca
        for ca_id, ca in row_obj.items():
            total = sum(new_counts.get(t, all_counts.get(t, 0)) for t in row_terms[ca_id])
            if total != ca.number_of_students:
                row_updates[ca_id] = (ca.number_of_students, total)

        self._print_summary(target, class_report, classes, counts, new_counts,
                            stem_names, row_updates, skipped_semester)
        if opts["report"]:
            self._write_report(opts["report"], class_report, counts, new_counts, stem_names)

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("\nDRY RUN — nothing saved. Re-run with --apply."))
            return

        with transaction.atomic():
            for term, number in new_counts.items():
                stem_id, year, sem = term
                StemStudentCount.objects.update_or_create(
                    stem_id=stem_id, year=year, semester=sem,
                    defaults={"number_of_students": number},
                )
            for ca_id, (_old, new) in row_updates.items():
                CourseAllocation.objects.filter(pk=ca_id).update(number_of_students=new)

        self.stdout.write(self.style.SUCCESS(
            f"\nApplied: {len(changed_terms)} stem counts raised, "
            f"{len(row_updates)} course allocations updated."))

    # ------------------------------------------------------------------
    def _print_summary(self, target, report, classes, counts, new_counts,
                       stem_names, row_updates, skipped_semester):
        raised = [r for r in report if r["after"] > r["before"]]
        met = [r for r in report if r["after"] == r["before"]]
        over = [r for r in report if r["after"] > target]
        short = [r for r in report if r["after"] < target]
        self.stdout.write(f"Target per class: {target}")
        self.stdout.write(f"Classes with stems: {len(report)}  |  already ≥ target: {len(met)}  |  topped up: {len(raised)}")
        self.stdout.write(f"Classes ending above target (shared stems): {len(over)}  |  still below: {len(short)}")
        self.stdout.write(f"Rows out of sync with their stem count (will be repaired): {len(row_updates)}")
        self.stdout.write(f"Stem counts changing: {sum(1 for t in new_counts if new_counts[t] != counts.get(t, 0))}"
                          f"  |  course allocations to update: {len(row_updates)}")
        if skipped_semester:
            self.stdout.write(self.style.WARNING(
                f"Skipped {skipped_semester} rows in Semester 3+ (StemStudentCount only supports 1-2)."))
        self.stdout.write("\nClass                    stems  before -> after")
        for r in report:
            if r["after"] != r["before"]:
                self.stdout.write(f"  {_label(r['key']):<22} {r['stems']:>4}  {r['before']:>6} -> {r['after']}")
        if short:
            self.stdout.write(self.style.ERROR("\nStill below target:"))
            for r in short:
                self.stdout.write(f"  {_label(r['key'])}: {r['after']}")

    def _write_report(self, path, report, counts, new_counts, stem_names):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["type", "name", "stems_mapped", "before", "after"])
            for r in report:
                w.writerow(["class", _label(r["key"]), r["stems"], r["before"], r["after"]])
            for term, after in sorted(new_counts.items(), key=lambda kv: stem_names.get(kv[0], "")):
                w.writerow(["stem", stem_names.get(term, term), "", counts.get(term, 0), after])
        self.stdout.write(f"Report written to {path}")