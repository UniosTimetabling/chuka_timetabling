"""
Find (and optionally repair) CourseAllocation rows that are M2M members of
MORE THAN ONE CombinedCourseGroup at the same time.

Why this can exist
-------------------
create_combined_group() (course_management/cod_panel.py) used to have no
check preventing an allocation already in one CombinedCourseGroup from
being folded into a second one. add_to_combined_group() always had this
check; create_combined_group() didn't (fixed alongside this command — see
the guard added there, plus course_allocation/combined_group_signals.py
for the ongoing DB-level safety net).

The most common way existing data got into this state: a COD deletes a
combined group and immediately creates a new, differently-composed one
from (partly) the same allocations. If the delete request failed, was
never actually sent, or raced with the create request, the old group row
survives with its `allocations` M2M intact, and the new group's create
call added the same allocation(s) into a second group.

Symptom this causes downstream
-------------------------------
_get_combined_group_for_allocation() (timetable/timetable_panel.py) picks
whichever group happens to sort first for a given allocation — with no
warning. Different allocations in the same "Resolve Remaining
Unscheduled" run can then resolve to different groups whose member sets
overlap, so previously-separated courses get glued back together as one
class, potentially spanning what should be two distinct combined groups.
It also corrupts the "Combined: <code> ×N" badges shown in the COD panel
and Find Courses (_get_combined_group_meta_map()), since a member showing
up in two groups makes it ambiguous which group's identity should win.

Usage
-----
    python manage.py find_overlapping_combined_groups
        # dry run — reports every allocation in >1 group and the
        # candidate groups it's torn between.

    python manage.py find_overlapping_combined_groups --fix
        # repair: for each conflicted allocation, keep it in the group
        # with the most recent created_at (the group most likely to be
        # the deliberate, current combination) and remove it from every
        # older group. If an older group drops to 0 or 1 remaining
        # members afterward, report it (use --dissolve-singletons to also
        # delete now-pointless single-member groups).

    python manage.py find_overlapping_combined_groups --fix --dissolve-singletons
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count


class Command(BaseCommand):
    help = "Find/repair CourseAllocation rows belonging to more than one CombinedCourseGroup."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix", action="store_true",
            help="Keep each conflicted allocation only in its most-recently-created "
                 "group, removing it from older group(s).",
        )
        parser.add_argument(
            "--dissolve-singletons", action="store_true",
            help="With --fix: if a group ends up with 0 or 1 members after repair, "
                 "delete the (now pointless) group entirely.",
        )

    def handle(self, *args, **options):
        from course_allocation.models import CombinedCourseGroup, CourseAllocation

        conflicted = (
            CourseAllocation.objects
            .annotate(n_groups=Count("combined_groups"))
            .filter(n_groups__gt=1)
            .prefetch_related("combined_groups")
            .select_related("department", "program")
        )

        count = conflicted.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS(
                "No overlaps found — every allocation belongs to at most one combined group."
            ))
            return

        self.stdout.write(self.style.WARNING(
            f"Found {count} allocation(s) belonging to more than one combined group:\n"
        ))

        touched_groups = set()
        for alloc in conflicted:
            groups = list(alloc.combined_groups.all().order_by("-created_at"))
            self.stdout.write(
                f"  - {alloc.course_code} (id={alloc.id}, dept={getattr(alloc.department, 'name', '?')}, "
                f"program={getattr(alloc.program, 'name', '?')}): "
                + ", ".join(f"'{g.group_code}' (id={g.id}, created={g.created_at:%Y-%m-%d %H:%M})" for g in groups)
            )
            touched_groups.update(g.id for g in groups)

            if options["fix"]:
                keep, drop = groups[0], groups[1:]
                with transaction.atomic():
                    for g in drop:
                        g.allocations.remove(alloc)
                        if g.primary_allocation_id == alloc.id:
                            remaining = g.allocations.exclude(pk=alloc.id)
                            g.primary_allocation = remaining.first()
                            g.save(update_fields=["primary_allocation"])
                self.stdout.write(
                    f"      -> kept in '{keep.group_code}' (id={keep.id}); "
                    f"removed from {', '.join(g.group_code for g in drop)}"
                )

        if not options["fix"]:
            self.stdout.write(self.style.WARNING(
                "\nDry run only — re-run with --fix to repair (keeps each allocation "
                "in its most recently created group)."
            ))
            return

        # Report/clean up groups that are now empty or singleton.
        for group in CombinedCourseGroup.objects.filter(pk__in=touched_groups):
            remaining = group.allocations.count()
            if remaining > 1:
                continue
            if remaining == 0:
                self.stdout.write(self.style.WARNING(
                    f"Group '{group.group_code}' (id={group.id}) has 0 members left."
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    f"Group '{group.group_code}' (id={group.id}) has only 1 member left "
                    f"— no longer a meaningful combination."
                ))
            if options["dissolve_singletons"]:
                group.delete()
                self.stdout.write(f"      -> dissolved (deleted group '{group.group_code}').")

        self.stdout.write(self.style.SUCCESS("\nDone."))
