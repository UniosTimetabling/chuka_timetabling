# course_allocation/management/commands/detach_converted_courses_from_existing_stems.py
"""
Undo the *collision* left behind by `migrate_education_groups_to_stems`.

WHAT WENT WRONG
---------------
`migrate_education_groups_to_stems` turned every Education Student Group into a
Combination Stem and moved the group's courses into it. When a combination
stem with the SAME NAME already existed (e.g. "STATISTICS MATHEMATICS",
"APPLIED MATHEMATICS" -- built earlier by the COD under a category such as
"statistics mathematics option"), the migration re-used that stem and dumped
the converted (group-lettered) courses into it, e.g.

    MATH 342-B, MATH 343-C, MATH 324-C ...

Those stems now hold courses that were never meant to be in them.

WHAT THIS COMMAND DOES
----------------------
For every stem that EXISTED BEFORE the migration (i.e. that the migration did
not create) and that received converted courses:

  * removes the converted courses from that stem (the stem itself, its
    category, and its original courses are NOT touched);
  * the removed courses become NORMAL STAND-ALONE courses: no stem, no student
    group -- shared by every combination / group of the program, which is
    exactly how the model treats a course that has neither;
  * if such a course is ALSO a member of some other stem, it stays there and
    only the pre-existing stem is left (its legacy `specialization_stem`
    pointer is re-synced either way).

Nothing is deleted, renamed or merged. Lecturers, student numbers, DVC
status and timetable placements stay on the same rows.

HOW IT KNOWS WHICH COURSES WERE CONVERTED
-----------------------------------------
From the backup folder(s) the migration wrote (default
<BASE_DIR>/backups/education_groups_to_stems/<timestamp>/):
    courseallocations.json   ids of every course row the migration converted
    manifest.json            ids of the stems the migration CREATED
                             (every other stem is "pre-existing")
All timestamp folders found are combined, so several migration runs are fine.
A course that already sat in the same stem BEFORE the migration (its backed-up
`specialization_stem` equals that stem) is never removed.

SAFE BY DEFAULT
---------------
Runs as a DRY RUN and prints the plan. Add --apply to write. --apply first
saves a JSON record of every membership it removes, and does the change in one
transaction (all or nothing).

USAGE
-----
    python manage.py detach_converted_courses_from_existing_stems            # dry run, all pre-existing stems
    python manage.py detach_converted_courses_from_existing_stems --apply

    # only some stems (name match, case-insensitive; repeatable)
    python manage.py detach_converted_courses_from_existing_stems \\
        --stem "STATISTICS MATHEMATICS" --stem "APPLIED MATHEMATICS" --apply

    # named stems: take out EVERY course currently in them, not only the
    # converted ones (use when you know the whole stem content is wrong)
    python manage.py detach_converted_courses_from_existing_stems \\
        --stem "STATISTICS MATHEMATICS" --all-courses --apply

    # point at one specific backup run
    python manage.py detach_converted_courses_from_existing_stems \\
        --backup-dir backups/education_groups_to_stems/20260920_180008

ROLLBACK
--------
    python manage.py detach_converted_courses_from_existing_stems \\
        --undo backups/detach_converted_from_existing_stems/<timestamp>/memberships.json
"""
import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from course_allocation.models import CourseAllocation, SpecializationStem
from department_management.models import Department


def _norm(name):
    return " ".join((name or "").split()).casefold()


class Command(BaseCommand):
    help = (
        "Take the courses that migrate_education_groups_to_stems pushed into PRE-EXISTING "
        "combination stems back out, leaving them as normal stand-alone courses "
        "(dry run unless --apply)."
    )

    # ------------------------------------------------------------------ args
    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Write changes (default is a dry run).")
        parser.add_argument("--department", default="Education",
                            help="Department name in the system (default: Education).")
        parser.add_argument("--backup-dir", default=None,
                            help="Backup folder written by the migration: either the parent "
                                 "(all timestamp folders are combined) or one timestamp folder "
                                 "(default: <BASE_DIR>/backups/education_groups_to_stems).")
        parser.add_argument("--stem", action="append", default=[], metavar="NAME",
                            help="Only handle stems with this name (case/spacing-insensitive). "
                                 "Repeatable. Default: every pre-existing stem that received "
                                 "converted courses.")
        parser.add_argument("--all-courses", action="store_true",
                            help="With --stem: remove EVERY course currently in the named stem(s), "
                                 "not only the ones the migration converted.")
        parser.add_argument("--include-created", action="store_true",
                            help="Also treat stems the migration CREATED as candidates (use only if "
                                 "the wrong stems turn out to be in that list -- see the diagnosis).")
        parser.add_argument("--diagnose", action="store_true",
                            help="Print where the converted courses and the named --stem(s) are now "
                                 "(printed automatically when nothing is found).")
        parser.add_argument("--undo", default=None, metavar="MEMBERSHIPS_JSON",
                            help="Put back the memberships recorded by an earlier --apply run.")

    # ---------------------------------------------------------------- output
    def _p(self, msg=""):
        self.stdout.write(msg)

    def _h(self, title):
        self._p("")
        self._p("=" * 100)
        self._p(title)
        self._p("=" * 100)

    # ------------------------------------------------------------------ main
    def handle(self, *args, **opts):
        self.opts = opts
        if opts["undo"]:
            return self._undo(Path(opts["undo"]))

        if opts["all_courses"] and not opts["stem"]:
            raise CommandError("--all-courses is only allowed together with --stem NAME "
                               "(it would otherwise empty every pre-existing stem).")

        dept = self._resolve_department(opts["department"])
        converted, created_stems, runs = self._load_backups()
        self._h(f"{'APPLY' if opts['apply'] else 'DRY RUN'} -- take converted courses out of "
                f"PRE-EXISTING stems for: {dept.name}")
        self._p(f"  backup run(s) read: {len(runs)}")
        for r in runs:
            self._p(f"    - {r}")
        self._p(f"  course rows the migration converted: {len(converted)}")
        self._p(f"  stems the migration created (left alone): {len(created_stems)}")

        plan = self._build_plan(dept, converted, created_stems)
        self._report(plan)

        if opts["diagnose"] or not plan["items"]:
            self._diagnose(dept, converted, created_stems)
        if not plan["items"]:
            self._p("\nNothing to do.")
            return
        if not opts["apply"]:
            self._p("\nDRY RUN complete -- nothing was changed. Re-run with --apply to write.")
            return

        out = self._write_record(plan)
        with transaction.atomic():
            n_stand, n_still = self._apply(plan)
        self._p(f"\nRecord (for --undo): {out / 'memberships.json'}")
        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Course rows now stand-alone: {n_stand}   "
            f"still in another stem: {n_still}"))

    # ----------------------------------------------------------- department
    def _resolve_department(self, name):
        try:
            return Department.objects.select_related("faculty").get(name__iexact=name.strip())
        except Department.DoesNotExist:
            near = list(Department.objects.filter(name__icontains="educ").values_list("name", flat=True))
            raise CommandError(f"Department '{name}' not found."
                               + (f" Similar names: {near}" if near else ""))

    # --------------------------------------------------------------- backups
    def _load_backups(self):
        base = Path(self.opts["backup_dir"]) if self.opts["backup_dir"] else \
            Path(settings.BASE_DIR) / "backups" / "education_groups_to_stems"
        if not base.is_absolute():
            base = Path(settings.BASE_DIR) / base
        if not base.exists():
            raise CommandError(f"Backup folder not found: {base}")

        folders = [base] if (base / "courseallocations.json").exists() else \
            sorted(p for p in base.iterdir() if p.is_dir() and (p / "courseallocations.json").exists())
        if not folders:
            raise CommandError(f"No migration backup (courseallocations.json) found under {base}")

        converted = {}          # alloc id -> stem id it already had BEFORE the migration (or None)
        created = set()
        for f in folders:
            for row in json.loads((f / "courseallocations.json").read_text()):
                converted.setdefault(int(row["pk"]), row["fields"].get("specialization_stem"))
            man = f / "manifest.json"
            if man.exists():
                created |= {int(i) for i in json.loads(man.read_text()).get("stems", [])}
            else:
                self.stderr.write(f"  ! {f} has no manifest.json -- stems created by that run "
                                  f"cannot be told apart from pre-existing ones.")
        return converted, created, [str(f) for f in folders]

    # ------------------------------------------------------------------ plan
    def _build_plan(self, dept, converted, created_stems):
        only = {_norm(n) for n in self.opts["stem"]}
        all_courses = self.opts["all_courses"]

        conv_ids = set(
            CourseAllocation.objects.filter(id__in=list(converted))
            .filter(Q(department=dept) | Q(program__department=dept))
            .values_list("id", flat=True)
        )

        stems = (
            SpecializationStem.objects
            .filter(Q(category__department=dept) | Q(category__program__department=dept))
            .exclude(id__in=([] if self.opts["include_created"] else created_stems))
            .select_related("category", "category__program")
            .prefetch_related("courses__program_course", "elective_groups__courses")
            .order_by("category__program__name", "category__name", "name", "id")
        )
        items, warnings = [], []
        for stem in stems:
            if only and _norm(stem.name) not in only:
                continue
            in_stem = list(stem.courses.all())
            candidates = in_stem if all_courses else [c for c in in_stem if c.id in conv_ids]
            if not candidates:
                continue
            pool_ids = set()
            for pool in stem.elective_groups.all():
                pool_ids.update(c.id for c in pool.courses.all())

            move, keep = [], []
            for c in candidates:
                if converted.get(c.id) == stem.id:
                    keep.append((c, "was already in this stem before the migration"))
                elif c.id in pool_ids:
                    keep.append((c, "belongs to a pick-one elective pool nested in this stem"))
                else:
                    move.append(c)
            if not (move or keep):
                continue
            items.append({"stem": stem, "move": move, "keep": keep, "total": len(in_stem)})

        if only:
            found = {_norm(i["stem"].name) for i in items}
            for n in self.opts["stem"]:
                if _norm(n) not in found:
                    warnings.append(f"No pre-existing stem named '{n}' with courses to detach was found "
                                    f"(it may have been created by the migration, or hold no converted courses).")
        return {"items": items, "warnings": warnings}

    # -------------------------------------------------------------- diagnose
    def _diagnose(self, dept, converted, created_stems):
        self._h("DIAGNOSIS -- where things actually are")
        conv_ids = list(CourseAllocation.objects.filter(id__in=list(converted)).values_list("id", flat=True))
        self._p(f"  converted course rows in the backup: {len(converted)}   still existing in the DB: {len(conv_ids)}")

        through = SpecializationStem.courses.through
        per_stem = {}
        for i in range(0, len(conv_ids), 500):
            for sid in through.objects.filter(courseallocation_id__in=conv_ids[i:i + 500]) \
                    .values_list("specializationstem_id", flat=True):
                per_stem[sid] = per_stem.get(sid, 0) + 1
        self._p(f"  converted rows currently inside SOME stem: {sum(per_stem.values())} membership(s) "
                f"in {len(per_stem)} stem(s)")
        stems = {s.id: s for s in SpecializationStem.objects
                 .filter(id__in=list(per_stem)).select_related("category", "category__program", "category__department")}
        pre = [(sid, n) for sid, n in per_stem.items() if sid not in created_stems]
        self._p(f"    of which in PRE-EXISTING stems (not created by the migration): {len(pre)} stem(s)")
        for sid, n in sorted(per_stem.items(), key=lambda kv: -kv[1])[:15]:
            st = stems.get(sid)
            if st is None:
                continue
            tag = "created-by-migration" if sid in created_stems else "PRE-EXISTING"
            self._p(f"      stem id={sid:<6} {st.name[:34]:<34} cat '{st.category.name[:28]}' "
                    f"dept '{st.category.department.name[:24]}'  converted courses: {n:<4} [{tag}]")

        names = {_norm(n) for n in self.opts["stem"]}
        if names:
            self._p("")
            self._p("  Stems with the name(s) you passed, in ALL departments:")
            found = False
            for st in (SpecializationStem.objects.select_related("category", "category__program", "category__department")
                       .prefetch_related("courses").order_by("id")):
                if _norm(st.name) not in names:
                    continue
                found = True
                ids = {c.id for c in st.courses.all()}
                tag = "created-by-migration" if st.id in created_stems else "PRE-EXISTING"
                self._p(f"      stem id={st.id:<6} '{st.name}'  cat '{st.category.name}' "
                        f"Y{st.category.year or '-'}S{st.category.semester or '-'}  "
                        f"program '{st.category.program.name}'  dept '{st.category.department.name}'")
                self._p(f"          courses: {len(ids)}   of them converted-by-migration: "
                        f"{len(ids & set(converted))}   [{tag}]")
            if not found:
                self._p("      (no stem with that name exists)")

    # ---------------------------------------------------------------- report
    def _report(self, plan):
        self._h("STEMS TO CLEAN")
        total_rows = set()
        for it in plan["items"]:
            s = it["stem"]
            cat = s.category
            yr = f"Year {cat.year}" if cat.year else "Any year"
            sm = f"Semester {cat.semester}" if cat.semester else "Any semester"
            self._p(f"\n  {s.name}   [category '{cat.name}', {yr} {sm}, {cat.program.name}]  (stem id={s.id})")
            self._p(f"    courses in stem now: {it['total']}   to take out: {len(it['move'])}   "
                    f"kept: {len(it['keep'])}")
            for c in sorted(it["move"], key=lambda x: (x.program_course.year, x.program_course.semester, x.course_code)):
                pc = c.program_course
                others = c.specialization_stems.exclude(id=s.id).count()
                after = "stays in another stem" if others else "-> stand-alone"
                self._p(f"      - Y{pc.year}S{pc.semester}  {c.course_code:<14} {c.course_name[:44]:<44} "
                        f"students {c.number_of_students:<4} {after}")
                total_rows.add(c.id)
            for c, why in it["keep"]:
                self._p(f"      = KEPT {c.course_code}: {why}")
        self._p(f"\n  distinct course rows affected: {len(total_rows)}")
        if plan["warnings"]:
            self._h("WARNINGS")
            for w in plan["warnings"]:
                self._p(f"  ! {w}")

    # ----------------------------------------------------------------- apply
    def _write_record(self, plan):
        out = Path(settings.BASE_DIR) / "backups" / "detach_converted_from_existing_stems" / \
            datetime.now().strftime("%Y%m%d_%H%M%S")
        out.mkdir(parents=True, exist_ok=True)
        memberships, pointers = {}, {}
        for it in plan["items"]:
            memberships[str(it["stem"].id)] = [c.id for c in it["move"]]
            for c in it["move"]:
                pointers[str(c.id)] = c.specialization_stem_id
        (out / "memberships.json").write_text(json.dumps(
            {"memberships": memberships, "specialization_stem_pointers": pointers}, indent=2))
        return out

    def _apply(self, plan):
        stand, still = 0, 0
        touched = {}
        for it in plan["items"]:
            if it["move"]:
                it["stem"].courses.remove(*it["move"])
            for c in it["move"]:
                touched[c.id] = c
        for c in touched.values():
            remaining = c.specialization_stems.count()
            if remaining == 0:
                if c.specialization_stem_id is not None:
                    c.specialization_stem = None
                    c.save(update_fields=["specialization_stem"])
                stand += 1
            else:
                c.refresh_primary_stem_pointer()
                still += 1
        return stand, still

    # ------------------------------------------------------------------ undo
    def _undo(self, path):
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        if not path.exists():
            raise CommandError(f"Record not found: {path}")
        data = json.loads(path.read_text())
        added = 0
        with transaction.atomic():
            for stem_id, alloc_ids in data["memberships"].items():
                stem = SpecializationStem.objects.filter(id=int(stem_id)).first()
                if stem is None:
                    self._p(f"  ! stem id={stem_id} no longer exists -- skipped")
                    continue
                have = set(stem.courses.filter(id__in=alloc_ids).values_list("id", flat=True))
                new = [a for a in alloc_ids if a not in have]
                if new:
                    stem.courses.add(*new)
                    added += len(new)
            for alloc_id, ptr in data.get("specialization_stem_pointers", {}).items():
                CourseAllocation.objects.filter(id=int(alloc_id)).update(specialization_stem_id=ptr)
        self.stdout.write(self.style.SUCCESS(f"Undo complete. Memberships restored: {added}"))
