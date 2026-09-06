"""
Find (and optionally repair) CombinedCourseGroup rows whose member
allocations ended up scheduled in DIFFERENT Timetable slots — the exact
symptom caused by the pre-fix manual-scheduling bug, where a single member
of a combined group (e.g. MATH 221 B) could be placed via "Add Entry" /
"Find Courses" -> "Schedule Here" without pulling the rest of the group
along, landing it in its own venue/day/time instead of the group's shared
one. The autoscheduler itself never causes this (build_global_merged_tasks()
Pass 1 always books a combined group as one atomic task) — this command is
for cleaning up rows created before timetable_panel.py / find_courses.py
were made CombinedCourseGroup-aware.

A group counts as "split" if its member allocations collectively occupy
more than one distinct (venue, day, start_time, end_time) slot.

Usage:
    python manage.py repair_split_combined_groups                 # dry run, just reports
    python manage.py repair_split_combined_groups --fix            # repair: keep the slot
                                                                     # held by the most members
                                                                     # (ties -> primary_allocation's
                                                                     # slot, else earliest-created),
                                                                     # move every member's Timetable
                                                                     # row onto it, deleting the
                                                                     # strays' old duplicate rows.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Find/repair CombinedCourseGroups whose members are scheduled in different Timetable slots."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix", action="store_true",
            help="Consolidate every member onto the group's chosen canonical slot.",
        )

    def handle(self, *args, **options):
        from course_allocation.models import CombinedCourseGroup
        from timetable.models import Timetable

        groups = (
            CombinedCourseGroup.objects
            .prefetch_related("allocations")
            .select_related("primary_allocation")
        )

        split_count = 0
        fixed_count = 0

        for group in groups:
            member_ids = list(group.allocations.values_list("id", flat=True))
            if len(member_ids) < 2:
                continue

            entries = list(
                Timetable.objects.filter(course_allocation_id__in=member_ids)
                .select_related("venue", "course_allocation")
            )
            if not entries:
                continue  # unscheduled group — nothing to repair

            slot_key = lambda e: (e.venue_id, e.day, e.start_time, e.end_time)
            by_slot = defaultdict(list)
            for e in entries:
                by_slot[slot_key(e)].append(e)

            if len(by_slot) <= 1:
                continue  # every scheduled member already agrees on one slot

            split_count += 1
            self.stdout.write(self.style.WARNING(
                f"\nSplit group '{group.group_code}' ({group.display_name()}), "
                f"{len(member_ids)} members, {len(entries)} scheduled across {len(by_slot)} different slots:"
            ))
            for (venue_id, day, start, end), rows in by_slot.items():
                venue_code = rows[0].venue.code if rows[0].venue else "Unknown"
                codes = ", ".join(r.course_allocation.course_code for r in rows)
                self.stdout.write(
                    f"    - {day} {start}-{end} @ {venue_code}: {codes} "
                    f"({len(rows)} of {len(member_ids)} members)"
                )

            unscheduled_members = len(member_ids) - len(entries)
            if unscheduled_members:
                self.stdout.write(f"    - {unscheduled_members} member(s) not yet scheduled at all")

            if not options["fix"]:
                continue

            # Choose the canonical slot: the one held by the most members;
            # ties broken by whichever slot the primary_allocation is in,
            # else the slot with the lowest Timetable id (oldest).
            def slot_rank(item):
                slot, rows = item
                has_primary = any(
                    group.primary_allocation_id and r.course_allocation_id == group.primary_allocation_id
                    for r in rows
                )
                return (-len(rows), 0 if has_primary else 1, min(r.id for r in rows))

            canonical_slot, canonical_rows = sorted(by_slot.items(), key=slot_rank)[0]
            venue_id, day, start, end = canonical_slot
            canonical_venue = canonical_rows[0].venue

            with transaction.atomic():
                # Delete every stray row outside the canonical slot, then
                # create a canonical-slot row for any member missing one.
                stray_alloc_ids = [
                    r.course_allocation_id
                    for slot, rows in by_slot.items() if slot != canonical_slot
                    for r in rows
                ]
                Timetable.objects.filter(
                    course_allocation_id__in=stray_alloc_ids
                ).exclude(
                    venue_id=venue_id, day=day, start_time=start, end_time=end,
                ).delete()

                already_on_canonical = {r.course_allocation_id for r in canonical_rows}
                to_create = [
                    Timetable(
                        course_allocation_id=aid,
                        venue=canonical_venue,
                        day=day,
                        start_time=start,
                        end_time=end,
                    )
                    for aid in member_ids
                    if aid not in already_on_canonical
                ]
                if to_create:
                    Timetable.objects.bulk_create(to_create)

            fixed_count += 1
            venue_code = canonical_venue.code if canonical_venue else "Unknown"
            self.stdout.write(self.style.SUCCESS(
                f"    -> Consolidated all {len(member_ids)} members onto {day} {start}-{end} @ {venue_code}."
            ))

        if split_count == 0:
            self.stdout.write(self.style.SUCCESS(
                "No split combined groups found (every group's scheduled members agree on one slot)."
            ))
            return

        if not options["fix"]:
            self.stdout.write(
                f"\n{split_count} split group(s) found — dry run only, nothing changed. "
                f"Re-run with --fix to consolidate each onto one slot."
            )
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone. Repaired {fixed_count} of {split_count} split group(s)."
            ))
