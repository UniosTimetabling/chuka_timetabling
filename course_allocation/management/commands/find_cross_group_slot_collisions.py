"""
Find CombinedCourseGroups that are sharing a Timetable slot with course
allocations that are NOT their own members — i.e. two different combined
groups (or a combined group and a standalone course) rendering on top of
each other in the timetable grid.

This is the cross-group version of repair_split_combined_groups (which
only checks whether a group's OWN members agree with each other) and of
reconcile_combined_group_placements (same-group only). Neither of those
catches this case: a group whose members fully agree on one slot, but
that slot is also occupied by someone else's booking.

Read-only. Reports only — does not change anything.

Usage:
    python manage.py find_cross_group_slot_collisions
"""
from collections import defaultdict

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Report Timetable slots occupied by members of more than one CombinedCourseGroup (or a group + an outsider)."

    def handle(self, *args, **options):
        from course_allocation.models import CombinedCourseGroup
        from timetable.models import Timetable

        # allocation_id -> set of group_ids it belongs to
        alloc_to_groups = defaultdict(set)
        group_names = {}
        for group in CombinedCourseGroup.objects.prefetch_related("allocations"):
            group_names[group.id] = f"'{group.group_code}' (base {group.base_course_code}, dept {group.department_id})"
            for aid in group.allocations.values_list("id", flat=True):
                alloc_to_groups[aid].add(group.id)

        # slot_key -> list of (Timetable entry, group_ids-or-empty)
        slot_map = defaultdict(list)
        entries = Timetable.objects.select_related("venue", "course_allocation")
        for e in entries:
            key = (e.day, e.start_time, e.end_time, e.venue_id)
            slot_map[key].append(e)

        problems = 0
        for key, rows in slot_map.items():
            groups_at_slot = set()
            for e in rows:
                groups_at_slot |= alloc_to_groups.get(e.course_allocation_id, set())

            if len(groups_at_slot) < 2:
                continue  # only 0 or 1 group involved here — fine

            problems += 1
            day, start, end, venue_id = key
            venue_code = rows[0].venue.code if rows[0].venue else "Unknown"
            self.stdout.write(self.style.ERROR(
                f"\nCOLLISION at {day} {start}-{end} @ {venue_code}: "
                f"{len(groups_at_slot)} different combined groups present"
            ))
            for gid in groups_at_slot:
                self.stdout.write(f"    - Group {group_names.get(gid, gid)}")
            for e in rows:
                gset = alloc_to_groups.get(e.course_allocation_id, set())
                tag = ", ".join(str(g) for g in gset) if gset else "no group / standalone"
                self.stdout.write(
                    f"        [{e.id}] {e.course_allocation.course_code} "
                    f"(alloc {e.course_allocation_id}) -> group(s): {tag}"
                )

        if problems == 0:
            self.stdout.write(self.style.SUCCESS(
                "No cross-group slot collisions found."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"\n{problems} colliding slot(s) found. For each, decide which group "
                f"should actually keep the slot, then delete the Timetable rows for the "
                f"other group's members (they'll go back to unscheduled) before "
                f"re-running the autoscheduler or placing them manually."
            ))
