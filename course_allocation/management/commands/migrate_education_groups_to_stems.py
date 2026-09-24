# course_allocation/management/commands/migrate_education_groups_to_stems.py
"""
Migrate the Department of Education's *Student Groups* into *Combination
Stems* (SpecializationStem), and re-map the courses that were tied to those
groups onto the new stems.

WHY
---
Education does not split cohorts into anonymous "Group A / Group B" halves.
Its groups are teaching-subject COMBINATIONS (e.g. "Economics/Geography").
That is exactly what a SpecializationStem models: a student picks ONE stem and
takes EVERY course in it, while courses of DIFFERENT stems may share a slot.
The Student Group design was the wrong shape for this department, and it also
forced one CourseAllocation clone per group per year.

WHAT IT DOES (Education department only)
----------------------------------------
1. Reads every StudentGroup of every Education program. Groups are identified
   by their NAME (whitespace/case-insensitive) *within a program*, NOT by
   year/semester/intake -- because the same combination exists in every year
   of the programme, ONE stem is created per combination and reused by
   Year 1, Year 2, Year 3, ... (and both semesters / both intakes).

       StudentGroup "Economics" Y1S1, Y1S2, Y2S1, Y3S1 ...
                          |
                          v
       ONE SpecializationStem "Economics"  (category "Combinations",
                                            year=None, semester=None)

   The category has year/semester left empty on purpose so it is not tied to
   a single year. Categories/stems are per (program, allocation set) because
   the data model links a stem to the AllocationSet of its courses.

2. For every CourseAllocation mapped to one of those groups (primary
   `student_group` and/or `additional_student_groups`):
     * adds the course to the stem(s) named after its group(s)
     * keeps the legacy singular `specialization_stem` pointer consistent
       (set when the course is in exactly one stem, cleared when in several)
     * clears `student_group` / `additional_student_groups` (the model's
       clean() forbids a course being both group-bound and stem-bound)
     * registers a BaseSelection, exactly like the COD "add courses to stem"
       screen does
   Nothing is deleted, merged, renamed or re-coded; lecturers, student
   numbers, DVC status and timetable placements all stay on the same rows.

3. Elective pools (SelectionGroup) that were restricted to Education groups
   are nested inside the matching stems instead (the model's own "pick one
   inside a stem" mechanism), and their restriction is removed.

Anything that already exists (category, stem with the same name) is REUSED, so
the command is safe to run more than once.

EXTRA RULES
-----------
* A combination gets a stem even if it only existed in ONE year, or has no
  courses yet. Stems are not tied to a year, so it is available to every year.
* Combinations whose names differ only by case/spacing ("KISW/BST" and
  "kisw/bst") are ONE combination. If that left two course rows for the SAME
  curriculum course inside the same stem(s), they are MERGED into one row
  (lowest id survives; students = highest; lecturer = first non-blank;
  approval/submission flags kept if any row had them; every reference to the
  removed rows -- timetable entries, pools, combined groups ... -- is
  re-pointed to the survivor). Disable with --no-merge-duplicates.
* Typos / reordered names ("hst/geo" vs "hist/geo", "comp/geo" vs "geo/comp")
  are only REPORTED as possible duplicates. To merge one, pass
  --alias "hst/geo=hist/geo" (repeatable).

STEP 0 -- ELECTIVE DATA CLEAN-UP (runs first, ALL DEPARTMENTS)
-------------------------------------------------------------
NOTE: unlike the stem migration below (Education only), this clean-up covers
every department in the system.
Some courses were wrongly flagged as electives. Only courses that really sit
in a SelectionGroup may stay electives, so:
  * every CourseAllocation with is_elective=True that is in NO selection group
    (neither `selection_group` nor the group's `courses`) is un-marked;
  * every ProgramCourse whose unit_type is ELECTIVE / REQUIRED_ELECTIVE and
    none of whose allocations is in a selection group is set back to CORE;
  * BaseSelection rows for Education courses that are in no selection group
    (any department) are removed (the auto-allocator re-flags any course listed there as an
    elective on its next run, which would bring the bad data back);
  * SelectionGroups (any department) that contain no courses are deleted.
Use --skip-elective-cleanup to skip all of the above.

SAFE BY DEFAULT
---------------
Runs as a DRY RUN: prints the full plan and changes nothing. Add --apply to
write. --apply first saves a JSON backup of every row it is about to change,
then does everything inside one transaction (all or nothing).

USAGE
-----
    python manage.py migrate_education_groups_to_stems              # dry run
    python manage.py migrate_education_groups_to_stems --apply
    python manage.py migrate_education_groups_to_stems --apply --delete-groups
    python manage.py migrate_education_groups_to_stems --department "Education" -v 2

ROLLBACK
--------
The backup folder printed by --apply holds `courseallocations.json` and
`selectiongroups.json` (restore with `manage.py loaddata <file>`) and
`manifest.json` (ids of the categories/stems this run CREATED, which can then
be deleted; deleting a stem only detaches its courses).
"""
import json
import re
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
import difflib

from django.db import IntegrityError, transaction
from django.db.models import Count, Q

from course_allocation.models import (
    AllocationSet,
    BaseSelection,
    CourseAllocation,
    GroupingTemplate,
    SelectionGroup,
    SpecializationCategory,
    SpecializationStem,
    StudentGroup,
)
from department_management.models import Department
from program_management.models import ProgramCourse

GENERIC_NAME_RE = re.compile(r"^group\s*[a-z]{1,4}$", re.IGNORECASE)


def _norm(name):
    """Identity of a combination: case/whitespace-insensitive name."""
    return " ".join((name or "").split()).casefold()


class Command(BaseCommand):
    help = (
        "Convert the Education department's Student Groups into reusable "
        "Combination Stems and map their courses onto them (dry run unless --apply)."
    )

    # ------------------------------------------------------------------ args
    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Write changes (default is a dry run).")
        parser.add_argument("--department", default="Education",
                            help="Department name in the system (default: Education).")
        parser.add_argument("--category-name", default="Combinations",
                            help="Name of the SpecializationCategory that holds the "
                                 "combination stems (default: Combinations).")
        parser.add_argument("--alias", action="append", default=[], metavar="TYPO=CANONICAL",
                            help='Treat two group names as ONE combination, e.g. --alias "hst/geo=hist/geo". '
                                 'Repeatable. Comparison ignores case/spacing.')
        parser.add_argument("--no-merge-duplicates", action="store_true",
                            help="Do not merge duplicate course rows that end up identical inside a stem.")
        parser.add_argument("--skip-pools", action="store_true",
                            help="Do not convert group-restricted elective pools.")
        parser.add_argument("--skip-elective-cleanup", action="store_true",
                            help="Skip STEP 0 (un-marking stray electives, dropping empty "
                                 "selection groups, clearing stray BaseSelection rows; all departments).")
        parser.add_argument("--keep-base-selection", action="store_true",
                            help="In STEP 0, do not remove BaseSelection rows.")
        parser.add_argument("--add-base-selection", action="store_true",
                            help="Register BaseSelection rows for migrated stem courses (the COD "
                                 "stem screen does this, but the auto-allocator then re-flags those "
                                 "courses as electives, so it is OFF by default here).")
        parser.add_argument("--delete-groups", action="store_true",
                            help="After migrating, delete the Education StudentGroups that "
                                 "nothing references any more (default: keep them, unused).")
        parser.add_argument("--drop-grouping-templates", action="store_true",
                            help="Also delete the Education GroupingTemplates so a future "
                                 "auto-allocate does not recreate the student groups.")
        parser.add_argument("--backup-dir", default=None,
                            help="Where to write the pre-apply backup "
                                 "(default: <BASE_DIR>/backups/education_groups_to_stems).")

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
        self.apply = opts["apply"]
        self.verbose = opts["verbosity"] >= 2
        self.category_name = opts["category_name"].strip()
        self.opts = opts
        self.alias_map, self.alias_display = {}, {}
        for item in opts["alias"]:
            if "=" not in item:
                raise CommandError(f"--alias must look like 'typo=canonical', got: {item!r}")
            typo, canon = item.split("=", 1)
            self.alias_map[_norm(typo)] = _norm(canon)
            self.alias_display[_norm(canon)] = " ".join(canon.split())

        dept = self._resolve_department(opts["department"])
        self._h(f"{'APPLY' if self.apply else 'DRY RUN'} -- Student Groups -> Combination Stems "
                f"for: {dept.name} ({dept.faculty.name})")

        self.dept = dept
        plan = self._build_plan()
        self._report(plan)

        cl = plan["cleanup"]
        has_cleanup = bool(cl and (cl["stray_allocs"] or cl["stray_pcs"] or cl["base_sel"] or cl["empty_pools"]))
        if not plan["allocs"] and not plan["pools"] and not has_cleanup \
                and not plan["merges"] and not plan["stems_needed"]:
            self._p("\nNothing to migrate.")
            if self.apply:
                with transaction.atomic():
                    self._finish_cleanup(plan, created={"categories": [], "stems": []})
            return

        if not self.apply:
            self._p("\nDRY RUN complete -- nothing was changed. Re-run with --apply to migrate.")
            return

        backup_dir = self._write_backup(plan)
        self._p(f"\nBackup written to: {backup_dir}")

        with transaction.atomic():
            created = self._apply(plan)
            self._finish_cleanup(plan, created)
        (backup_dir / "manifest.json").write_text(json.dumps(created, indent=2))
        self._p(f"Manifest (ids created by this run): {backup_dir / 'manifest.json'}")
        self.stdout.write(self.style.SUCCESS("\nMigration applied."))

    # ----------------------------------------------------------- department
    def _resolve_department(self, name):
        try:
            return Department.objects.select_related("faculty").get(name__iexact=name.strip())
        except Department.DoesNotExist:
            near = list(Department.objects.filter(name__icontains="educ").values_list("name", flat=True))
            raise CommandError(
                f"Department '{name}' not found."
                + (f" Similar names: {near}" if near else "")
            )

    # ------------------------------------------------------------------ plan
    def _plan_cleanup(self):
        """Elective clean-up across ALL departments (read-only planning)."""
        grouped_ids = set(CourseAllocation.objects.filter(selection_group__isnull=False)
                          .values_list("id", flat=True))
        grouped_ids |= set(SelectionGroup.courses.through.objects.values_list("courseallocation_id", flat=True))

        rows = list(CourseAllocation.objects.values_list(
            "id", "program_course_id", "is_elective", "course_code", "department__name"))
        grouped_pc = {pc for aid, pc, _e, _c, _d in rows if aid in grouped_ids}

        stray_allocs = [(aid, pc, code) for aid, pc, e, code, _d in rows if e and aid not in grouped_ids]
        by_dept_alloc = Counter(d for aid, _pc, e, _c, d in rows if e and aid not in grouped_ids)

        stray_pcs, by_dept_pc = [], Counter()
        for pc in ProgramCourse.objects.select_related("program__department"):
            if pc.is_elective_type and pc.id not in grouped_pc:
                stray_pcs.append(pc)
                by_dept_pc[pc.program.department.name] += 1

        base_sel = []
        if not self.opts["keep_base_selection"]:
            base_sel = [b for b in BaseSelection.objects.select_related("department")
                        if b.program_course_id not in grouped_pc]
        by_dept_bs = Counter(b.department.name for b in base_sel)

        empty_pools, kept_pools = [], []
        for pool in (SelectionGroup.objects.select_related("department")
                     .annotate(n_courses=Count("courses", distinct=True),
                               n_primary=Count("primary_allocations", distinct=True))
                     .filter(n_courses=0, n_primary=0)):
            refs = {}
            for rel in SelectionGroup._meta.related_objects:
                n = rel.related_model._default_manager.filter(**{rel.field.name: pool}).count()
                if n:
                    refs[f"{rel.related_model.__name__}.{rel.field.name}"] = n
            (kept_pools if refs else empty_pools).append((pool, refs))

        return {
            "grouped_ids": grouped_ids, "stray_allocs": stray_allocs, "stray_pcs": stray_pcs,
            "base_sel": base_sel, "empty_pools": [p for p, _ in empty_pools], "kept_pools": kept_pools,
            "by_dept": {"alloc": by_dept_alloc, "pc": by_dept_pc, "bs": by_dept_bs,
                        "pool": Counter(p.department.name for p, _ in empty_pools)},
        }

    def _build_plan(self):
        cleanup = None if self.opts["skip_elective_cleanup"] else self._plan_cleanup()
        dropped_pool_ids = {p.id for p in cleanup["empty_pools"]} if cleanup else set()
        groups = list(
            StudentGroup.objects.filter(program__department=self.dept)
            .select_related("program").order_by("program__name", "year", "semester", "intake", "letter")
        )
        edu_group_ids = {g.id for g in groups}

        # ---- 1. Group identity: (program, normalised name) --------------------
        combos = OrderedDict()   # (program_id, key) -> info
        gid_to_combo = {}
        for g in groups:
            own = _norm(g.name)
            ck = (g.program_id, self.alias_map.get(own, own))
            info = combos.setdefault(ck, {
                "program": g.program, "key": ck[1], "raw_names": Counter(),
                "groups": [], "letters": set(),
            })
            info["raw_names"][" ".join(g.name.split())] += 1
            info["groups"].append(g)
            info["letters"].add(g.letter)
            gid_to_combo[g.id] = ck
        for info in combos.values():
            exact = Counter({n: c for n, c in info["raw_names"].items() if _norm(n) == info["key"]})
            if exact:
                info["name"] = exact.most_common(1)[0][0]
            else:
                info["name"] = self.alias_display.get(info["key"]) or info["raw_names"].most_common(1)[0][0]

        warnings = []
        for ck, info in combos.items():
            if GENERIC_NAME_RE.match(info["name"]):
                warnings.append(f"[{info['program'].name}] group name '{info['name']}' is still a "
                                f"generic 'Group X' label -- the stem will be called that too.")
            if self.verbose and len(info["letters"]) > 1:
                warnings.append(f"[{info['program'].name}] '{info['name']}' uses several letters "
                                f"{sorted(info['letters'])} across years -- merged into ONE stem by name.")
            if len(info["raw_names"]) > 1:
                warnings.append(f"[{info['program'].name}] '{info['name']}' is spelled several ways "
                                f"{dict(info['raw_names'])} -- merged; most common spelling used.")
            cohorts = Counter((g.year, g.semester, g.intake) for g in info["groups"])
            dup = [c for c, n in cohorts.items() if n > 1]
            if dup:
                warnings.append(f"[{info['program'].name}] '{info['name']}' appears twice in the same "
                                f"year/semester/intake {dup} -- merged into ONE stem.")
        by_prog_letter = defaultdict(set)
        for info in combos.values():
            for l in info["letters"]:
                by_prog_letter[(info["program"].id, l.casefold())].add(info["name"])
        for (pid, l), names in by_prog_letter.items():
            if self.verbose and len(names) > 1:
                pname = next(i["program"].name for i in combos.values() if i["program"].id == pid)
                warnings.append(f"[{pname}] letter '{l.upper()}' is used by DIFFERENT names {sorted(names)} "
                                f"in different years -- they become separate stems; check this is intended.")

        # ---- 2. Allocations to migrate ---------------------------------------
        allocs = list(
            CourseAllocation.objects
            .filter(Q(student_group_id__in=edu_group_ids) | Q(additional_student_groups__in=edu_group_ids))
            .distinct()
            .select_related("program_course", "program", "student_group")
            .prefetch_related("additional_student_groups", "specialization_stems")
            .order_by("program__name", "program_course__year", "program_course__semester", "course_code", "id")
        )
        alloc_targets = {}     # alloc.id -> [(program_id, allocation_set_id, key), ...]
        stems_needed = OrderedDict()   # (program_id, set_id, key) -> {"n": count, "years": Counter}
        for a in allocs:
            gids = {a.student_group_id} | {g.id for g in a.additional_student_groups.all()}
            gids &= edu_group_ids
            targets = OrderedDict()
            for gid in sorted(gids):
                pid, key = gid_to_combo[gid]
                targets[(pid, a.allocation_set_id, key)] = True
                if a.program_id and a.program_id != pid:
                    warnings.append(f"CourseAllocation {a.id} '{a.course_code}' is on program "
                                    f"{a.program_id} but its group is on program {pid}.")
            alloc_targets[a.id] = list(targets.keys())
            if a.selection_group_id or (cleanup is None and a.is_elective) or \
                    (cleanup is not None and a.id in cleanup["grouped_ids"]):
                warnings.append(f"CourseAllocation {a.id} '{a.course_code}' is an ELECTIVE that also had a "
                                f"student group. Group -> stem membership is migrated; if it belongs to a "
                                f"pick-one pool make sure that pool is still correct.")

        # ---- 2b. Duplicate rows that collapse into one (KISW/BST + kisw/bst) -
        merges = []
        if not self.opts["no_merge_duplicates"]:
            buckets = defaultdict(list)
            for a in sorted(allocs, key=lambda x: x.id):
                mk = (a.program_course_id, a.allocation_set_id, a.intake, a.is_evening_weekend,
                      a.section_number, a.special_intake_group_id, tuple(sorted(alloc_targets[a.id])))
                buckets[mk].append(a)
            merges = [(v[0], v[1:]) for v in buckets.values() if len(v) > 1]
        dup_ids = {d.id for _k, ds in merges for d in ds}
        survivors = [a for a in allocs if a.id not in dup_ids]
        for keeper, dups in merges:
            for d in dups:
                if d.course_code != keeper.course_code:
                    warnings.append(f"Merging '{d.course_code}' (id={d.id}) into '{keeper.course_code}' "
                                    f"(id={keeper.id}): same curriculum course, different (group-lettered) "
                                    f"course code -- the survivor gets the plain curriculum code "
                                    f"'{keeper.program_course.course_code}'.")
        for a in survivors:
            for t in alloc_targets[a.id]:
                slot = stems_needed.setdefault(t, {"n": 0, "years": Counter()})
                slot["n"] += 1
                slot["years"][a.program_course.year] += 1

        # ---- 2c. Combinations that got no courses still get a stem -------------
        used_keys = {(pid, key) for (pid, _sid, key) in stems_needed}
        unused = [info for ck, info in combos.items() if ck not in used_keys]
        fb = AllocationSet.objects.filter(department=self.dept, is_archived=False).order_by("-is_legacy", "id").first()
        prog_sets = defaultdict(set)
        for (pid, sid, _k) in list(stems_needed):
            prog_sets[pid].add(sid)
        for ck, info in combos.items():
            for sid in (prog_sets.get(ck[0]) or {fb.id if fb else None}):
                stems_needed.setdefault((ck[0], sid, ck[1]), {"n": 0, "years": Counter()})

        # ---- 3. Restricted elective pools ------------------------------------
        pools = []
        if not self.opts["skip_pools"]:
            pool_qs = (SelectionGroup.objects.filter(restricted_to_groups__in=edu_group_ids)
                       .exclude(id__in=dropped_pool_ids).distinct()
                       .prefetch_related("restricted_to_groups", "courses"))
            for pool in pool_qs:
                tgts = OrderedDict()
                for g in pool.restricted_to_groups.all():
                    if g.id in edu_group_ids:
                        pid, key = gid_to_combo[g.id]
                        tgts[(pid, pool.allocation_set_id, key)] = True
                        stems_needed.setdefault((pid, pool.allocation_set_id, key), {"n": 0, "years": Counter()})
                pools.append({"pool": pool, "targets": list(tgts.keys())})

        # ---- 4. Existing stems that can be reused / conflicts ----------------
        existing = {}
        for (pid, sid, key) in stems_needed:
            existing[(pid, sid, key)] = self._find_existing_stem(pid, sid, key, combos)
            hits = self._stems_named(pid, sid, combos[(pid, key)]["name"])
            if len(hits) > 1:
                warnings.append(f"[{combos[(pid, key)]['program'].name}] {len(hits)} stems already named "
                                f"'{combos[(pid, key)]['name']}' in this allocation set -- reusing the "
                                f"first (id={hits[0].id}); consider merging them.")

        # ---- 5. Things that still point at Education groups ------------------
        restricted_stems = list(
            SpecializationStem.objects.filter(restricted_to_groups__in=edu_group_ids).distinct()
        )
        for s in restricted_stems:
            warnings.append(f"Existing stem '{s.name}' (id={s.id}) is still restricted to Education "
                            f"student groups -- left untouched; review it by hand.")
        templates = list(GroupingTemplate.objects.filter(program__department=self.dept))

        # ---- 6. Possible duplicate combinations (report only) ----------------
        suspects = []
        by_prog = defaultdict(list)
        for (pid, key), info in combos.items():
            by_prog[pid].append(info)
        for pid, infos in by_prog.items():
            for i in range(len(infos)):
                for j in range(i + 1, len(infos)):
                    a_, b_ = infos[i], infos[j]
                    ta = sorted(re.split(r"[\s/,&+-]+", a_["key"]))
                    tb = sorted(re.split(r"[\s/,&+-]+", b_["key"]))
                    ratio = difflib.SequenceMatcher(None, a_["key"], b_["key"]).ratio()
                    if ta == tb or ratio >= 0.85:
                        suspects.append((a_, b_, "same parts, different order" if ta == tb else f"very similar ({ratio:.2f})"))

        return {
            "groups": groups, "edu_group_ids": edu_group_ids, "combos": combos,
            "gid_to_combo": gid_to_combo, "allocs": survivors, "alloc_targets": alloc_targets,
            "merges": merges, "suspects": suspects,
            "stems_needed": stems_needed, "pools": pools, "existing": existing,
            "warnings": warnings, "templates": templates, "unused": unused, "cleanup": cleanup,
        }

    def _set_label(self, set_id):
        if set_id is None:
            return "no set"
        if not hasattr(self, "_set_names"):
            self._set_names = dict(AllocationSet.objects.values_list("id", "name"))
        return f"set '{self._set_names.get(set_id, set_id)}'"

    def _stems_named(self, program_id, set_id, name):
        return list(SpecializationStem.objects.filter(
            category__program_id=program_id, category__allocation_set_id=set_id, name__iexact=name,
        ).order_by("id"))

    def _find_existing_stem(self, program_id, set_id, key, combos):
        name = combos[(program_id, key)]["name"]
        hits = self._stems_named(program_id, set_id, name)
        if not hits:
            return None
        ours = [s for s in hits if s.category.name.casefold() == self.category_name.casefold()]
        return (ours or hits)[0]

    # ---------------------------------------------------------------- report
    def _report(self, plan):
        combos, stems_needed = plan["combos"], plan["stems_needed"]
        cl = plan["cleanup"]
        if cl is not None:
            self._h("0. ELECTIVE DATA CLEAN-UP  (ALL DEPARTMENTS)")
            self._p(f"  CourseAllocations flagged elective but in NO selection group -> un-mark: {len(cl['stray_allocs'])}")
            self._p(f"  ProgramCourses typed ELECTIVE/REQUIRED_ELECTIVE but in NO selection group -> CORE: {len(cl['stray_pcs'])}")
            self._p(f"  BaseSelection rows (courses in no selection group) -> remove: {len(cl['base_sel'])}"
                    + ("  [kept: --keep-base-selection]" if self.opts["keep_base_selection"] else ""))
            self._p(f"  Empty SelectionGroups (no courses) -> delete: {len(cl['empty_pools'])}")
            for pool in cl["empty_pools"]:
                self._p(f"      - '{pool.name}' [{pool.department.name}] (id={pool.id}, "
                        f"{self._set_label(pool.allocation_set_id)})")
            for pool, refs in cl["kept_pools"]:
                self._p(f"  ! empty SelectionGroup '{pool.name}' (id={pool.id}) NOT deleted, still referenced by {refs}")
            if self.verbose:
                for aid, _pc, code in cl["stray_allocs"]:
                    self._p(f"      un-mark allocation id={aid} {code}")
                for pc in cl["stray_pcs"]:
                    self._p(f"      ProgramCourse id={pc.id} {pc.course_code} ({pc.unit_type}) -> CORE")
            depts = sorted(set().union(*[set(c) for c in cl["by_dept"].values()]))
            if depts:
                self._p("  Per department:")
                self._p(f"      {'department':<50} {'alloc':>6} {'prog.course':>12} {'base-sel':>9} {'empty grp':>10}")
                for dn in depts:
                    bd = cl["by_dept"]
                    self._p(f"      {dn:<50} {bd['alloc'][dn]:>6} {bd['pc'][dn]:>12} {bd['bs'][dn]:>9} {bd['pool'][dn]:>10}")
            self._p(f"  Electives that STAY (in a selection group): {len(cl['grouped_ids'])} allocation row(s)")

        self._h("1. STUDENT GROUPS FOUND  ->  ONE REUSABLE STEM PER COMBINATION")
        self._p(f"Education student-group rows: {len(plan['groups'])}   "
                f"distinct combinations: {len(combos)}   "
                f"course rows to migrate: {len(plan['allocs'])}")
        cur = None
        for (pid, key), info in combos.items():
            if cur != pid:
                cur = pid
                self._p(f"\n  {info['program'].name}")
            yrs = sorted({g.year for g in info["groups"]})
            self._p(f"    * {info['name']:<45} groups: {len(info['groups']):<3} in years {yrs}")

        self._h("2. STEMS THAT WILL BE USED   (course rows per year of study)")
        self._p(f"  Category name: '{self.category_name}' (year=None, semester=None -> reusable by every year)")
        for (pid, sid, key), info in stems_needed.items():
            c = combos[(pid, key)]
            state = "REUSE existing" if plan["existing"].get((pid, sid, key)) else \
                ("CREATE (empty)" if info["n"] == 0 else "CREATE")
            years = ", ".join(f"Y{y}:{n}" for y, n in sorted(info["years"].items())) or "no direct courses"
            self._p(f"  [{state:<15}] {c['program'].name} | {self._set_label(sid)} | stem '{c['name']}' "
                    f"-> {info['n']} course row(s)  ({years})")

        if plan["pools"]:
            self._h("3. GROUP-RESTRICTED ELECTIVE POOLS -> NESTED IN STEMS")
            for item in plan["pools"]:
                pool = item["pool"]
                names = ", ".join(combos[(pid, k)]["name"] for pid, _s, k in item["targets"])
                self._p(f"  Pool '{pool.name}' (id={pool.id}, {pool.courses.count()} course(s)) "
                        f"restricted to groups -> nest in stem(s): {names}")

        if self.verbose:
            self._h("4. COURSE ROWS (verbose)")
            for a in plan["allocs"]:
                tg = ", ".join(combos[(p, k)]["name"] for p, _s, k in plan["alloc_targets"][a.id])
                self._p(f"  id={a.id:<6} Y{a.program_course.year}S{a.program_course.semester} "
                        f"{a.course_code:<18} -> {tg}")

        if plan["unused"]:
            self._h("COMBINATIONS WITH NO COURSES YET (stem is still created, empty, usable by every year)")
            for info in plan["unused"]:
                yrs = sorted({g.year for g in info["groups"]})
                self._p(f"  {info['program'].name} | {info['name']}  (was only in year(s) {yrs})")

        if plan["merges"]:
            n_dup = sum(len(d) for _k, d in plan["merges"])
            self._h(f"DUPLICATE COURSE ROWS TO MERGE: {n_dup} row(s) into {len(plan['merges'])}")
            shown = plan["merges"] if self.verbose else plan["merges"][:15]
            for keeper, dups in shown:
                self._p(f"  keep id={keeper.id} {keeper.course_code} (students {keeper.number_of_students}) "
                        f"<- " + ", ".join(f"id={d.id} {d.course_code} ({d.number_of_students})" for d in dups))
            if len(shown) < len(plan["merges"]):
                self._p(f"  ... and {len(plan['merges']) - len(shown)} more (-v 2 lists all)")

        if plan["suspects"]:
            self._h("POSSIBLE DUPLICATE COMBINATIONS  (NOT merged -- decide yourself)")
            for a_, b_, why in plan["suspects"]:
                self._p(f"  [{a_['program'].name}] '{a_['name']}'  ~  '{b_['name']}'   {why}")
                self._p(f"      to merge: --alias \"{b_['name']}={a_['name']}\"")

        if plan["templates"]:
            self._h("GROUPING TEMPLATES")
            self._p(f"  {len(plan['templates'])} Education GroupingTemplate(s) exist. A future auto-allocate "
                    f"would use them to RE-CREATE student groups.")
            self._p("  Use --drop-grouping-templates to remove them together with this migration."
                    if not self.opts["drop_grouping_templates"] else "  They will be deleted (--drop-grouping-templates).")

        if plan["warnings"]:
            self._h(f"WARNINGS ({len(plan['warnings'])})")
            seen = set()
            for w in plan["warnings"]:
                if w in seen:
                    continue
                seen.add(w)
                self._p(f"  ! {w}")

    # ----------------------------------------------------------------- apply
    def _write_backup(self, plan):
        base = Path(self.opts["backup_dir"]) if self.opts["backup_dir"] else \
            Path(settings.BASE_DIR) / "backups" / "education_groups_to_stems"
        out = base / datetime.now().strftime("%Y%m%d_%H%M%S")
        out.mkdir(parents=True, exist_ok=True)
        (out / "courseallocations.json").write_text(
            serializers.serialize("json", CourseAllocation.objects.filter(id__in=[a.id for a in plan["allocs"]])))
        cl = plan["cleanup"]
        sg_ids = [p["pool"].id for p in plan["pools"]]
        if cl:
            sg_ids += [p.id for p in cl["empty_pools"]]
            stray_ids = [aid for aid, _pc, _c in cl["stray_allocs"]]
            (out / "courseallocations_unmarked.json").write_text(
                serializers.serialize("json", CourseAllocation.objects.filter(id__in=stray_ids)))
            (out / "programcourses_unit_type.json").write_text(
                serializers.serialize("json", ProgramCourse.objects.filter(id__in=[pc.id for pc in cl["stray_pcs"]])))
            (out / "baseselections_removed.json").write_text(
                serializers.serialize("json", BaseSelection.objects.filter(id__in=[b.id for b in cl["base_sel"]])))
        if plan["merges"]:
            ids = [k.id for k, _d in plan["merges"]] + [d.id for _k, ds in plan["merges"] for d in ds]
            (out / "merged_courseallocations.json").write_text(
                serializers.serialize("json", CourseAllocation.objects.filter(id__in=ids)))
        (out / "selectiongroups.json").write_text(
            serializers.serialize("json", SelectionGroup.objects.filter(id__in=sg_ids)))
        return out

    def _apply(self, plan):
        combos = plan["combos"]
        created = {"categories": [], "stems": []}
        cat_cache, stem_cache = {}, {}
        programs = {info["program"].id: info["program"] for info in combos.values()}

        def get_stem(pid, sid, key):
            ck = (pid, sid, key)
            if ck in stem_cache:
                return stem_cache[ck]
            info = combos[(pid, key)]
            stem = self._find_existing_stem(pid, sid, key, combos)
            if stem is None:
                cat_key = (pid, sid)
                if cat_key not in cat_cache:
                    cat = SpecializationCategory.objects.filter(
                        program_id=pid, allocation_set_id=sid, name__iexact=self.category_name).first()
                    if cat is None:
                        cat = SpecializationCategory.objects.create(
                            name=self.category_name, department=self.dept, program=programs[pid],
                            allocation_set_id=sid, year=None, semester=None)
                        created["categories"].append(cat.id)
                    cat_cache[cat_key] = cat
                stem = SpecializationStem.objects.create(category=cat_cache[cat_key], name=info["name"])
                created["stems"].append(stem.id)
            stem_cache[ck] = stem
            return stem

        cl = plan["cleanup"]
        if cl is not None:
            n1 = CourseAllocation.objects.filter(id__in=[a for a, _p, _c in cl["stray_allocs"]]).update(is_elective=False)
            n2 = ProgramCourse.objects.filter(id__in=[pc.id for pc in cl["stray_pcs"]]).update(unit_type="CORE")
            n3 = BaseSelection.objects.filter(id__in=[b.id for b in cl["base_sel"]]).delete()[0]
            n4 = SelectionGroup.objects.filter(id__in=[p.id for p in cl["empty_pools"]]).delete()[0]
            self._p(f"\n  clean-up: allocations un-marked {n1}, program courses -> CORE {n2}, "
                    f"base selections removed {n3}, empty selection groups deleted {len(cl['empty_pools'])}")
            created["cleanup"] = {"allocations_unmarked": n1, "program_courses_core": n2,
                                  "base_selections_removed": n3,
                                  "selection_groups_deleted": len(cl["empty_pools"])}

        # every combination gets its stem (also those with no courses)
        for (pid, sid, key) in plan["stems_needed"]:
            get_stem(pid, sid, key)

        n_merged = self._merge_duplicates(plan["merges"])
        if plan["merges"]:
            self._p(f"  duplicate course rows merged away: {n_merged}")
            created["duplicate_rows_merged"] = n_merged

        edu_ids = plan["edu_group_ids"]
        n_alloc = 0
        for a in plan["allocs"]:
            # keep the M2M consistent with a legacy singular pointer first
            if a.specialization_stem_id and not a.specialization_stems.filter(pk=a.specialization_stem_id).exists():
                a.specialization_stems.add(a.specialization_stem_id)
            for (pid, sid, key) in plan["alloc_targets"][a.id]:
                get_stem(pid, sid, key).courses.add(a)
            if self.opts["add_base_selection"]:
                BaseSelection.objects.get_or_create(program_course_id=a.program_course_id, department=self.dept)

            extra = [g.id for g in a.additional_student_groups.all() if g.id in edu_ids]
            if extra:
                a.additional_student_groups.remove(*extra)
            if a.student_group_id in edu_ids:
                a.student_group = None
                a.save(update_fields=["student_group"])
            a.refresh_primary_stem_pointer()
            n_alloc += 1

        n_pool = 0
        for item in plan["pools"]:
            pool = item["pool"]
            for (pid, sid, key) in item["targets"]:
                pool.specialization_stems.add(get_stem(pid, sid, key))
            pool.restricted_to_groups.remove(*[g.id for g in pool.restricted_to_groups.all() if g.id in edu_ids])
            pool.sync_courses_into_mapped_stems()
            n_pool += 1

        self._p(f"\n  course rows migrated: {n_alloc}   pools converted: {n_pool}   "
                f"categories created: {len(created['categories'])}   stems created: {len(created['stems'])}")
        return created

    # ------------------------------------------------------------------ merge
    @staticmethod
    def _repoint(dup, keeper):
        """Move every reference to `dup` over to `keeper` (FK / one-to-one / M2M)."""
        for rel in CourseAllocation._meta.related_objects:
            model = rel.related_model
            if rel.many_to_many:
                through = rel.field.remote_field.through
                src, tgt = rel.field.m2m_field_name(), rel.field.m2m_reverse_field_name()
                for row in through._default_manager.filter(**{tgt: dup}):
                    if through._default_manager.filter(
                            **{src: getattr(row, src + "_id"), tgt: keeper}).exists():
                        continue
                    through._default_manager.filter(pk=row.pk).update(**{tgt + "_id": keeper.pk})
            elif not model._meta.auto_created:
                for obj in model._default_manager.filter(**{rel.field.name: dup}):
                    try:
                        with transaction.atomic():
                            model._default_manager.filter(pk=obj.pk).update(**{rel.field.name: keeper})
                    except IntegrityError:
                        pass   # would violate a unique constraint -> stays on dup, removed with it

    def _merge_duplicates(self, merges):
        removed = 0
        for keeper, dups in merges:
            keeper.refresh_from_db()
            for d in dups:
                d.refresh_from_db()
                keeper.number_of_students = max(keeper.number_of_students or 0, d.number_of_students or 0)
                if not keeper.lecturer_id and d.lecturer_id:
                    keeper.lecturer_id = d.lecturer_id
                if not keeper.selection_group_id and d.selection_group_id:
                    keeper.selection_group_id = d.selection_group_id
                if not keeper.special_intake_group_id and d.special_intake_group_id:
                    keeper.special_intake_group_id = d.special_intake_group_id
                keeper.approved_by_dvc = keeper.approved_by_dvc or d.approved_by_dvc
                keeper.submitted_to_tt = keeper.submitted_to_tt or d.submitted_to_tt
                keeper.rejected_by_dvc = (keeper.rejected_by_dvc or d.rejected_by_dvc) and not keeper.approved_by_dvc
                if keeper.rejected_by_dvc and keeper.reason_for_disapproval in ("", "No reason yet") \
                        and d.reason_for_disapproval not in ("", "No reason yet"):
                    keeper.reason_for_disapproval = d.reason_for_disapproval
                self._repoint(d, keeper)
                # keep any stem membership the duplicate already had
                for st in d.specialization_stems.all():
                    st.courses.add(keeper)
            if any(d.course_code != keeper.course_code for d in dups):
                keeper.course_code = keeper.program_course.course_code
            keeper.save(update_fields=[
                "course_code", "number_of_students", "lecturer", "selection_group", "special_intake_group",
                "approved_by_dvc", "submitted_to_tt", "rejected_by_dvc", "reason_for_disapproval"])
            CourseAllocation.objects.filter(id__in=[d.id for d in dups]).delete()
            removed += len(dups)
        return removed

    # ---------------------------------------------------------------- cleanup
    def _finish_cleanup(self, plan, created):
        if self.opts["drop_grouping_templates"] and plan["templates"]:
            n = len(plan["templates"])
            GroupingTemplate.objects.filter(id__in=[t.id for t in plan["templates"]]).delete()
            self._p(f"  grouping templates deleted: {n}")

        if self.opts["delete_groups"]:
            deleted, kept = 0, []
            for g in StudentGroup.objects.filter(id__in=plan["edu_group_ids"]):
                refs = self._references(g)
                if refs:
                    kept.append((g, refs))
                else:
                    g.delete()
                    deleted += 1
            self._p(f"  student groups deleted: {deleted}")
            for g, refs in kept:
                self._p(f"  kept {g.display_name}: still referenced by {refs}")
        else:
            self._p("  Student Group rows were kept (now unused). Use --delete-groups to remove "
                    "the unreferenced ones.")

    @staticmethod
    def _references(group):
        """{'Model.field': count} of rows that still point at this StudentGroup."""
        found = {}
        for rel in StudentGroup._meta.related_objects:
            n = rel.related_model._default_manager.filter(**{rel.field.name: group}).count()
            if n:
                found[f"{rel.related_model.__name__}.{rel.field.name}"] = n
        return found
