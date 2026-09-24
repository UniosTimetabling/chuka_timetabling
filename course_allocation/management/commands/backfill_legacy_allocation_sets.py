"""
backfill_legacy_allocation_sets
--------------------------------
One-time, idempotent, SAFE backfill for the AllocationSet upgrade.

What it does, per department that has at least one CourseAllocation with
allocation_set IS NULL:
  1. Finds or creates ONE AllocationSet flagged is_legacy=True named
     "Legacy Current Allocation" for that department, labeled as
     Semester 1 (a single scope=ALL component, semester_number=1) so it
     shows up on the COD's allocation picker as "Semester 1" and can be
     selected/switched to as such. Status is inferred from the existing
     submitted_to_tt / approved_by_dvc booleans (majority vote — the
     per-course booleans are left completely untouched either way, this
     only sets a sensible label on the new container).
  2. Attaches every NULL-allocation_set CourseAllocation row for that
     department to that set via a plain UPDATE of the new FK column.
  3. Does the same for that department's pre-existing SelectionGroup,
     SpecializationCategory, and CombinedCourseGroup rows (NOT
     StudentGroup — those are deliberately shared across allocation sets
     by design, see StudentGroupService.list_groups, so they never get an
     allocation_set at all).
  4. Does the same for that department's pre-existing LabAllocation
     (lab/workshop) rows, joined via program_course -> program ->
     department since LabAllocation has no direct department column.

     This is safe against the tightened uniqueness constraints added
     alongside allocation_set (SpecializationCategory is now unique per
     (program, name, allocation_set) instead of (program, name);
     CombinedCourseGroup.group_code is now unique per (group_code,
     allocation_set) instead of globally): both OLD constraints were
     already at least as strict as the new one, so there cannot be two
     orphaned rows that collide once given the same legacy_set — each
     (program, name) / group_code pair was already unique before this
     upgrade, so backfilling them all to one legacy set introduces no new
     duplicates.

Labeling the legacy set as Semester 1 is a deliberate choice, not an
inference from the data: pre-existing CourseAllocation rows may carry a
ProgramCourse of semester 1, 2, or 3, and that's left completely
untouched (it's real curriculum data) — only the AllocationSet's own
declared composition (a label on the container, not on the rows inside
it) is set to Semester 1, since that's what the pre-upgrade "current
allocation" is being treated as going forward.

SAFE TO RE-RUN even if an earlier version of this command already ran
and inferred a different (possibly mixed, possibly empty) semester
composition for the legacy set: re-running with --apply corrects any
scope=ALL component that isn't semester 1 back to a single Semester 1
component. It never touches a scope=SELECTED component (those can only
exist because a COD deliberately added one via the allocation-creation
wizard's "mix in another semester" step — never something this command
creates), and it never touches any CourseAllocation/group row's own
data — only the FK column pointing at its AllocationSet.

It NEVER deletes, edits, or moves anything else. Existing course_code,
lecturer, student counts, approval flags, group names, etc. are not
touched.

Run with --apply to actually write. Without --apply it only reports what
it would do (dry run), so it is safe to run repeatedly and safe to run in
production first to eyeball the plan.

Usage:
    python manage.py backfill_legacy_allocation_sets            # dry run
    python manage.py backfill_legacy_allocation_sets --apply    # apply
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from course_allocation.models import (
    AllocationSet, AllocationSetSemesterComponent, CourseAllocation,
    SelectionGroup, SpecializationCategory, CombinedCourseGroup,
    LabAllocation,
)
from department_management.models import Department

LEGACY_SEMESTER_NUMBER = 1


class Command(BaseCommand):
    help = "Backfill a Legacy AllocationSet (labeled Semester 1) per department for pre-existing CourseAllocation/group rows (dry-run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Actually write changes. Omit to dry-run.")
        parser.add_argument("--department-id", type=int, default=None, help="Restrict to one department (optional).")

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        dept_id = options["department_id"]

        depts = Department.objects.all()
        if dept_id:
            depts = depts.filter(id=dept_id)

        total_depts_touched = 0
        total_rows_touched = 0
        total_group_rows_touched = 0
        total_lab_rows_touched = 0
        total_relabeled = 0

        for dept in depts:
            orphan_qs = CourseAllocation.objects.filter(department=dept, allocation_set__isnull=True)
            count = orphan_qs.count()

            orphan_selection_groups = SelectionGroup.objects.filter(department=dept, allocation_set__isnull=True)
            orphan_categories = SpecializationCategory.objects.filter(department=dept, allocation_set__isnull=True)
            orphan_combined_groups = CombinedCourseGroup.objects.filter(department=dept, allocation_set__isnull=True)
            # LabAllocation has no direct department FK (it hangs off
            # program_course -> program -> department instead), so it's
            # joined rather than filtered directly.
            orphan_lab_allocations = LabAllocation.objects.filter(
                program_course__program__department=dept, allocation_set__isnull=True,
            )
            group_count = (
                orphan_selection_groups.count() + orphan_categories.count()
                + orphan_combined_groups.count()
            )
            lab_count = orphan_lab_allocations.count()

            existing_legacy = AllocationSet.objects.filter(department=dept, is_legacy=True).first()
            needs_relabel = False
            if existing_legacy is not None:
                current_all_scope_semesters = set(
                    existing_legacy.semester_components
                    .filter(scope=AllocationSetSemesterComponent.SCOPE_ALL)
                    .values_list("semester_number", flat=True)
                )
                needs_relabel = current_all_scope_semesters != {LEGACY_SEMESTER_NUMBER}

            if count == 0 and group_count == 0 and lab_count == 0 and not needs_relabel:
                continue

            total_depts_touched += 1
            total_rows_touched += count
            total_group_rows_touched += group_count
            total_lab_rows_touched += lab_count

            # Infer a sensible status label only — never alters per-row flags.
            statuses = Counter(orphan_qs.values_list("submitted_to_tt", "approved_by_dvc"))
            most_common = statuses.most_common(1)[0][0] if statuses else (False, False)
            submitted_tt, approved_dvc = most_common
            inferred_status = (
                AllocationSet.STATUS_SUBMITTED_TO_TT if submitted_tt
                else AllocationSet.STATUS_SUBMITTED_TO_DVC if approved_dvc
                else AllocationSet.STATUS_DRAFT
            )

            self.stdout.write(
                f"[{dept.name}] {count} allocation(s), {group_count} group/category/combined-group row(s), "
                f"{lab_count} lab/workshop allocation(s) with no AllocationSet"
                + (f", legacy set #{existing_legacy.id} needs relabel to Semester {LEGACY_SEMESTER_NUMBER}" if needs_relabel else "")
                + f". status={inferred_status}."
            )

            if not apply_changes:
                continue

            with transaction.atomic():
                legacy_set, created = AllocationSet.objects.get_or_create(
                    department=dept,
                    is_legacy=True,
                    defaults=dict(
                        name="Legacy Current Allocation",
                        allocation_type=AllocationSet.TYPE_AUTO_FULL,
                        status=inferred_status,
                        created_at=timezone.now(),
                    ),
                )

                # Force the label to Semester 1: drop any scope=ALL component
                # that isn't semester 1 (only ones this command itself could
                # have created previously — a COD's own SELECTED-scope
                # additions via the wizard are never touched), then ensure
                # exactly one Semester 1 / scope=ALL component exists.
                legacy_set.semester_components.filter(
                    scope=AllocationSetSemesterComponent.SCOPE_ALL
                ).exclude(semester_number=LEGACY_SEMESTER_NUMBER).delete()
                legacy_set.semester_components.get_or_create(
                    semester_number=LEGACY_SEMESTER_NUMBER,
                    scope=AllocationSetSemesterComponent.SCOPE_ALL,
                )
                if needs_relabel:
                    total_relabeled += 1

                updated = orphan_qs.update(allocation_set=legacy_set)
                updated_sg = orphan_selection_groups.update(allocation_set=legacy_set)
                updated_cat = orphan_categories.update(allocation_set=legacy_set)
                updated_cg = orphan_combined_groups.update(allocation_set=legacy_set)
                updated_lab = orphan_lab_allocations.update(allocation_set=legacy_set)

                self.stdout.write(self.style.SUCCESS(
                    f"  -> {'created' if created else 'reused'} legacy set #{legacy_set.id} "
                    f"(labeled Semester {LEGACY_SEMESTER_NUMBER}), attached {updated} allocation(s), "
                    f"{updated_sg} selection group(s), {updated_cat} specialization categor(y/ies), "
                    f"{updated_cg} combined group(s), {updated_lab} lab/workshop allocation(s)."
                ))

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                f"\nDRY RUN — {total_depts_touched} department(s), {total_rows_touched} allocation row(s), "
                f"{total_group_rows_touched} group/category/combined-group row(s), "
                f"{total_lab_rows_touched} lab/workshop allocation row(s) would be attached. "
                "Re-run with --apply to write."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone — {total_depts_touched} department(s), {total_rows_touched} allocation row(s), "
                f"{total_group_rows_touched} group/category/combined-group row(s), "
                f"{total_lab_rows_touched} lab/workshop allocation row(s) attached, "
                f"{total_relabeled} legacy set(s) relabeled to Semester {LEGACY_SEMESTER_NUMBER}."
            ))
