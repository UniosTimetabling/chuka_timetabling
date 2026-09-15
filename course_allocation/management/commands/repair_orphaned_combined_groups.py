"""
Find (and optionally repair) CombinedCourseGroup rows whose primary
allocation was deleted, leaving primary_allocation=NULL while other
member allocations are still linked via the `allocations` M2M.

Symptom this causes: the affected course allocation(s) show up fine on
the COD dashboard listing (course_management/cod_panel.py has no
combined-group filtering) but silently disappear from the generated
Allocation PDF (allocation_reports/adapters.py._main_qs excludes any
non-primary member of a group -- and with primary_allocation NULL,
every remaining member reads as "non-primary").

Usage:
    python manage.py repair_orphaned_combined_groups              # dry run, just reports
    python manage.py repair_orphaned_combined_groups --fix         # repair by promoting
                                                                     # a remaining member to
                                                                     # primary
    python manage.py repair_orphaned_combined_groups --fix --dissolve-singletons
        # also: if a group ends up with only one member after repair,
        # delete the (now pointless) group entirely so that course
        # goes back to being a normal, un-grouped allocation.
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Find/repair CombinedCourseGroup rows with a NULL primary_allocation."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix", action="store_true",
            help="Promote the lowest-pk remaining member to primary_allocation.",
        )
        parser.add_argument(
            "--dissolve-singletons", action="store_true",
            help="With --fix: if a group has only one member left, delete the "
                 "group entirely instead of promoting a lone member to primary.",
        )

    def handle(self, *args, **options):
        from course_allocation.models import CombinedCourseGroup

        orphaned = (
            CombinedCourseGroup.objects
            .filter(primary_allocation__isnull=True)
            .prefetch_related("allocations__department", "allocations__program")
        )

        count = orphaned.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS(
                "No orphaned combined groups found (every group has a valid primary_allocation)."
            ))
            return

        self.stdout.write(self.style.WARNING(
            f"Found {count} combined group(s) with no primary_allocation:\n"
        ))

        for group in orphaned:
            members = list(group.allocations.all())
            self.stdout.write(
                f"  Group '{group.group_code}' (base course {group.base_course_code}), "
                f"department: {group.department}"
            )
            if not members:
                self.stdout.write(self.style.ERROR(
                    "    -> No members left either. This group is completely empty."
                ))
                if options["fix"]:
                    with transaction.atomic():
                        group.delete()
                    self.stdout.write(self.style.SUCCESS("    -> Deleted empty group."))
                continue

            for m in members:
                self.stdout.write(
                    f"    - [{m.pk}] {m.course_code} — {m.course_name} "
                    f"(program: {m.program.name if m.program_id else '—'}, "
                    f"dept: {m.department.name if m.department_id else '—'}) "
                    f"<- hidden from PDF right now"
                )

            if options["fix"]:
                with transaction.atomic():
                    if len(members) == 1 and options["dissolve_singletons"]:
                        group.delete()
                        self.stdout.write(self.style.SUCCESS(
                            "    -> Only one member left; dissolved the group so it's a normal allocation again."
                        ))
                    else:
                        new_primary = min(members, key=lambda m: m.pk)
                        group.primary_allocation = new_primary
                        group.save(update_fields=["primary_allocation"])
                        self.stdout.write(self.style.SUCCESS(
                            f"    -> Promoted [{new_primary.pk}] {new_primary.course_code} to primary_allocation."
                        ))

        if not options["fix"]:
            self.stdout.write(
                "\nDry run only — nothing changed. Re-run with --fix to repair "
                "(add --dissolve-singletons to auto-dissolve single-member groups)."
            )
        else:
            self.stdout.write(self.style.SUCCESS(
                "\nDone. Regenerate/force-refresh the affected departments' PDFs "
                "(e.g. via the COD panel or the 'Force Regenerate' action) to see the fix."
            ))
