"""
Retroactively fix CombinedCourseGroups that were combined BEFORE the
timetable-reconciliation fix existed.

Symptom: combining courses on the COD panel only linked the allocations
via the CombinedCourseGroup.allocations M2M — it never touched their
underlying Timetable rows. So a course that already had its own
individual timetable entry before being combined just kept sitting in
its own separate day/time/venue slot, and the timetable grid rendered
the "combined" class as several individual entries instead of one.

New combines are now fixed automatically (see
course_management.cod_panel.reconcile_combined_group_placement, called
from create_combined_group / add_to_combined_group). This command runs
the exact same logic over every EXISTING group so ones combined before
the fix get admitted into it too.

For each group with disagreeing member placements, it:
  - evaluates every member's existing slot as a candidate host for the
    whole combined class, and picks the first one that doesn't collide
    with anything else (venue capacity, another booking in that venue,
    lecturer double-booking, or a hard LecturerBlockedSlot);
  - if every candidate collides, force-picks the best-ranked one anyway
    (flagged in the output as "unified_forced_collision" so it can be
    reviewed on the normal conflicts screen);
  - removes the losing entries and creates a matching row at the
    winning slot for any member that didn't have one there.

Usage:
    python manage.py reconcile_combined_group_placements               # dry run, just reports
    python manage.py reconcile_combined_group_placements --fix          # apply the fix
    python manage.py reconcile_combined_group_placements --fix --group-id 42
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = (
        "Collapse each CombinedCourseGroup's members onto one shared "
        "timetable placement (dry run by default; use --fix to apply)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix", action="store_true",
            help="Apply the reconciliation. Without this flag, only reports "
                 "which groups would be affected.",
        )
        parser.add_argument(
            "--group-id", type=int, default=None,
            help="Only process a single CombinedCourseGroup, by id.",
        )

    def handle(self, *args, **options):
        from course_allocation.models import CombinedCourseGroup
        from timetable.models import Timetable
        from course_management.cod_panel import (
            _slot_key, reconcile_combined_group_placement,
        )

        groups = CombinedCourseGroup.objects.prefetch_related(
            "allocations__lecturer"
        ).order_by("id")
        if options["group_id"]:
            groups = groups.filter(pk=options["group_id"])

        total = groups.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No combined groups found."))
            return

        already_ok = 0
        unscheduled = 0
        needs_fix = 0
        forced_collisions = 0

        for group in groups:
            members = list(group.allocations.all())
            if len(members) < 2:
                continue

            member_ids = [m.id for m in members]
            existing = list(
                Timetable.objects.filter(course_allocation_id__in=member_ids)
            )
            if not existing:
                unscheduled += 1
                continue

            distinct_slots = {_slot_key(e) for e in existing}
            if len(distinct_slots) <= 1:
                already_ok += 1
                continue

            needs_fix += 1
            self.stdout.write(
                f"Group '{group.group_code}' (base {group.base_course_code}, "
                f"dept: {group.department}) — {len(distinct_slots)} disagreeing "
                f"slots across {len(members)} members:"
            )
            for m in members:
                m_entries = [e for e in existing if e.course_allocation_id == m.id]
                if not m_entries:
                    self.stdout.write(f"    - [{m.pk}] {m.course_code}: unscheduled")
                else:
                    for e in m_entries:
                        self.stdout.write(
                            f"    - [{m.pk}] {m.course_code}: {e.day} "
                            f"{e.start_time}-{e.end_time} @ venue {e.venue_id}"
                        )

            if options["fix"]:
                with transaction.atomic():
                    result = reconcile_combined_group_placement(group)
                if result["action"] == "unified_forced_collision":
                    forced_collisions += 1
                    self.stdout.write(self.style.WARNING(
                        f"    -> Forced onto slot {result['slot']} (every candidate "
                        f"collided with something — check the conflicts view)."
                    ))
                else:
                    self.stdout.write(self.style.SUCCESS(
                        f"    -> Unified onto slot {result['slot']}."
                    ))
            else:
                self.stdout.write("    -> (dry run — would unify these)")

        self.stdout.write("")
        self.stdout.write(
            f"Checked {total} group(s): {already_ok} already unified, "
            f"{unscheduled} not yet scheduled, {needs_fix} needed reconciliation"
            + (f" ({forced_collisions} forced onto a colliding slot)." if options["fix"] else ".")
        )
        if not options["fix"] and needs_fix:
            self.stdout.write(
                "\nDry run only — nothing changed. Re-run with --fix to apply."
            )
