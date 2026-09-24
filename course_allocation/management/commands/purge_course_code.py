"""
Delete every CourseAllocation row for one or more course codes (base codes,
e.g. "EAPE 411" — matches that exact code AND every lettered section
"EAPE 411-A", "EAPE 411-B", ...), plus everything that would otherwise be
left dangling behind them.

Written to clean up after a bad commit_draft() run (see the
course_group_planner.py / course_group_draft.py fix) that created wrong
plain (no-stem) allocations instead of the intended stem-tagged ones —
but works for any course code(s) you need to wipe and recommit from scratch.

SAFE BY DEFAULT: dry run only, reports what it WOULD do. Nothing is
deleted until you pass --confirm.

Usage:
    # one course
    python manage.py purge_course_code "EAPE 411"

    # several courses in one run (space-separated, quote each code)
    python manage.py purge_course_code "EAPE 411" "EAPE 412" "EDCI 111"

    # every course code known to have been hit by the bad commit_draft() run
    python manage.py purge_course_code --all-affected

    # scope to one dept, by name or numeric id
    python manage.py purge_course_code "EAPE 411" --department Education
    python manage.py purge_course_code "EAPE 411" --department 3

    # actually delete (any of the above, plus --confirm)
    python manage.py purge_course_code --all-affected --confirm

What it deletes (all CourseAllocation rows matching any of the codes):
    - the CourseAllocation rows themselves
    - CASCADE side effects Django will perform automatically for each one:
      Timetable / TempTimetable entries, ExamTimetable / ExamTempTimetable
      entries, and any MergedCourseGroup / MergedCourseGroupTimetable row
      that used one of these as its base_course
    - membership in CombinedCourseGroup.allocations and
      SpecializationStem.courses (M2M — Django cleans these up on delete;
      the CombinedCourseGroup/SpecializationStem rows themselves are NOT
      deleted, just emptied of this course)

What it does NOT touch (reported, not deleted — different course codes
can share these, so a blind delete here could break unrelated courses):
    - StudentGroup rows (letters) for the program/year/semester/intake —
      other courses may use the same lettered groups
    - GroupingTemplate / GroupingTemplateGroup / GroupingTemplateStemAssignment
      "knowledge base" rows for the program/year/semester cell — these are
      scoped per program-year-semester, not per course, and can carry
      several courses' worth of scope. If letters look wrong after you
      re-commit a clean draft, check these separately.
    - CourseGroupDraftMapping / CourseGroupDraft / CourseGroupDraftLetter —
      the draft tables. commit_draft() KEEPS a mapping row after applying it
      (it's the durable record of "this letter belongs to this stem"), so
      this command deliberately leaves it alone. That's what makes this purge
      safe to use for a clean re-commit: once the CourseAllocation/
      SpecializationStem rows are gone, the Course Groups matrix on
      /groups-electives/ will show the same stem split as pending again on
      its own (draft_matrix() checks the live data, not the mapping table),
      so hitting Commit puts it straight back — no re-ticking needed.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

# Course codes observed to have been hit by the bad commit_draft() run
# (i.e. every course on the /groups-electives/ matrix that came out with
# wrong plain, no-stem allocations). Used by --all-affected as a shortcut
# so you don't have to retype all of these by hand. Keep this list in sync
# with whatever you find on the matrix before relying on --all-affected.
ALL_AFFECTED_COURSE_CODES = [
    "EAPE 411",
    "EAPE 412",
    "EDCI 111",
    "EDCI 203",
    "EDCI 211",
    "EDCI 311",
    "EDCI 322",
    "EDFO 111",
    "EDFO 211",
    "EDFO 321",
    "EDFO 422",
    "EPSC 111",
    "EPSC 222",
    "EPSC 311",
    "EPSC 431",
]


class Command(BaseCommand):
    help = "Delete all CourseAllocation rows for one or more course codes (dry run unless --confirm)."

    def add_arguments(self, parser):
        parser.add_argument(
            "course_codes",
            nargs="*",
            help='Base course code(s), e.g. "EAPE 411" "EAPE 412". Each one matches '
                 'that exact code and every lettered section ("EAPE 411-A", '
                 '"EAPE 411-B", ...). Pass several, space-separated, to purge them '
                 'all in a single run. Omit and use --all-affected instead to purge '
                 'the full known-affected list.',
        )
        parser.add_argument(
            "--all-affected", action="store_true",
            help="Purge every course code in the built-in ALL_AFFECTED_COURSE_CODES "
                 "list (the full set of courses hit by the bad commit_draft() run), "
                 "instead of passing codes individually. Cannot be combined with "
                 "explicit course_codes.",
        )
        parser.add_argument(
            "--department", default=None,
            help="Optional Department to scope the purge to: either its numeric id, "
                 "or its name (e.g. \"Education\") — matched case-insensitively, exact "
                 "first then falls back to a contains-match if nothing exact is found. "
                 "Applies to every course code in this run.",
        )
        parser.add_argument(
            "--confirm", action="store_true",
            help="Actually delete. Without this flag, only a report is printed.",
        )

    def handle(self, *args, **options):
        from course_allocation.models import (
            CourseAllocation, CombinedCourseGroup, SpecializationStem,
        )
        from department_management.models import Department

        raw_codes = options["course_codes"]
        use_all_affected = options["all_affected"]

        if use_all_affected and raw_codes:
            raise CommandError(
                "Pass either explicit course_codes or --all-affected, not both."
            )
        if not use_all_affected and not raw_codes:
            raise CommandError(
                'Provide at least one course_code (e.g. "EAPE 411"), or pass '
                "--all-affected to purge the full known-affected list."
            )

        codes = ALL_AFFECTED_COURSE_CODES if use_all_affected else raw_codes

        cleaned = []
        seen = set()
        for raw in codes:
            raw = raw.strip()
            if not raw:
                raise CommandError("course_code cannot be blank.")
            base = raw.upper()
            if base in seen:
                continue
            seen.add(base)
            cleaned.append(base)

        dept_arg = options["department"]
        department = None
        if dept_arg is not None:
            dept_arg = str(dept_arg).strip()
            if dept_arg.isdigit():
                department = Department.objects.filter(id=int(dept_arg)).first()
                if department is None:
                    raise CommandError(f"No Department with id={dept_arg}.")
            else:
                department = Department.objects.filter(name__iexact=dept_arg).first()
                if department is None:
                    candidates = list(Department.objects.filter(name__icontains=dept_arg))
                    if len(candidates) == 1:
                        department = candidates[0]
                    elif len(candidates) > 1:
                        names = ", ".join(d.name for d in candidates)
                        raise CommandError(
                            f'"{dept_arg}" matches more than one department: {names}. '
                            f"Be more specific or pass the numeric id."
                        )
                    else:
                        raise CommandError(f'No Department found matching "{dept_arg}".')

        # Build one combined filter across every requested base code, so a
        # single query/report/delete covers the whole batch.
        code_filter = Q()
        for base in cleaned:
            code_filter |= Q(course_code__iexact=base) | Q(course_code__istartswith=base + "-")

        qs = CourseAllocation.objects.filter(code_filter)
        if department is not None:
            qs = qs.filter(department=department)
            self.stdout.write(f'Scoped to department: {department.name} (id={department.id})\n')

        codes_label = ", ".join(f'"{c}"' for c in cleaned)
        self.stdout.write(f"Course codes in this run ({len(cleaned)}): {codes_label}\n")

        allocations = list(
            qs.select_related("program", "department", "student_group", "specialization_stem")
        )
        count = len(allocations)
        if count == 0:
            self.stdout.write(self.style.SUCCESS(
                f"No CourseAllocation rows found for any of {codes_label}"
                + (f" in {department.name}" if department is not None else "") + "."
            ))
            return

        ids = [a.id for a in allocations]

        # ── Report what's there, grouped by base course code ────────────
        self.stdout.write(self.style.WARNING(
            f"Found {count} CourseAllocation row(s) across {len(cleaned)} course code(s):"
        ))

        def base_of(course_code):
            # Map "EAPE 411-A" back to "EAPE 411" for grouping/reporting.
            upper = course_code.upper()
            for base in cleaned:
                if upper == base or upper.startswith(base + "-"):
                    return base
            return upper  # shouldn't happen given the filter above

        by_base = {}
        for a in allocations:
            by_base.setdefault(base_of(a.course_code), []).append(a)

        for base in cleaned:
            rows = by_base.get(base, [])
            if not rows:
                self.stdout.write(f'\n  "{base}": 0 rows found')
                continue
            self.stdout.write(f'\n  "{base}": {len(rows)} row(s)')
            for a in sorted(rows, key=lambda a: (a.program.name if a.program else "", a.course_code)):
                stem_label = a.specialization_stem.name if a.specialization_stem_id else "(no stem)"
                group_label = a.student_group.letter if a.student_group_id else "(no group)"
                prog_label = a.program.name if a.program else "(no program)"
                self.stdout.write(
                    f"    id={a.id:<6} {a.course_code:<20} program={prog_label:<45} "
                    f"letter={group_label:<3} stem={stem_label}"
                )

        # Cascading impact — counted, not yet touched.
        from timetable.models import (
            Timetable, TempTimetable, ExamTimetable, ExamTempTimetable,
            MergedCourseGroup, MergedCourseGroupTimetable,
        )
        cascade_counts = {
            "Timetable entries": Timetable.objects.filter(course_allocation_id__in=ids).count(),
            "TempTimetable entries": TempTimetable.objects.filter(course_allocation_id__in=ids).count(),
            "ExamTimetable entries": ExamTimetable.objects.filter(course_allocation_id__in=ids).count(),
            "ExamTempTimetable entries": ExamTempTimetable.objects.filter(course_allocation_id__in=ids).count(),
            "MergedCourseGroup (as base course)": MergedCourseGroup.objects.filter(base_course_id__in=ids).count(),
            "MergedCourseGroupTimetable (as base course)": MergedCourseGroupTimetable.objects.filter(base_course_id__in=ids).count(),
        }
        any_cascade = any(cascade_counts.values())
        if any_cascade:
            self.stdout.write(self.style.WARNING(
                "\nThese rows are referenced elsewhere — deleting them CASCADES and also deletes:"
            ))
            for label, n in cascade_counts.items():
                if n:
                    self.stdout.write(f"  {label}: {n}")
            self.stdout.write(self.style.WARNING(
                "If any of these are real, already-scheduled timetable/exam data (not just "
                "artifacts of the bad commit), stop and double check before confirming."
            ))
        else:
            self.stdout.write("\nNo scheduled timetable/exam entries reference these rows — safe on that front.")

        combined = (
            CombinedCourseGroup.objects.filter(allocations__id__in=ids).distinct()
        )
        combined_count = combined.count()
        if combined_count:
            self.stdout.write(self.style.WARNING(
                f"\n{combined_count} CombinedCourseGroup(s) include one of these rows — "
                f"they'll be emptied of it (not deleted). Run "
                f"`manage.py repair_orphaned_combined_groups --fix --dissolve-singletons` "
                f"afterwards to clean up any that are now empty or down to one member."
            ))

        stems_touched = (
            SpecializationStem.objects.filter(courses__id__in=ids).distinct().count()
        )
        if stems_touched:
            self.stdout.write(
                f"\n{stems_touched} SpecializationStem(s) reference one of these rows via "
                f"their `courses` link — that link will be cleared for this course, the "
                f"stem itself is untouched."
            )

        if not options["confirm"]:
            self.stdout.write(self.style.NOTICE(
                f"\nDry run only — nothing deleted. Re-run with --confirm to delete these "
                f"{count} row(s) across {len(cleaned)} course code(s)."
            ))
            return

        with transaction.atomic():
            deleted_count, _ = qs.delete()

        self.stdout.write(self.style.SUCCESS(
            f"\nDeleted {deleted_count} row(s) (including cascaded rows above) for "
            f"{codes_label}."
        ))
        self.stdout.write(
            "Reminder: GroupingTemplate rows for the affected program/year/semester cells "
            "were NOT touched (they're shared with other courses in that cell). If your next "
            "commit still comes out with unexpected letters, check those separately."
        )
