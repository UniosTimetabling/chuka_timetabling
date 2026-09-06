# ==============================================================================
# python manage.py seed_allocation_config
#
# Creates the GLOBAL default AllocationConfig row (department=None), and
# applies example seniority-based Lecturer.max_load_override values. Safe to
# re-run — uses get_or_create / update, no duplicates.
# ==============================================================================

from django.core.management.base import BaseCommand
from course_allocation.models import AllocationConfig
from lecturer_portal.models import Lecturer

SENIOR_DESIGNATIONS = {"Prof": 8, "Dr": 7}
JUNIOR_DESIGNATIONS = {"Mr": 4, "Ms": 4, "Mrs": 4}


class Command(BaseCommand):
    help = "Seed the GLOBAL AllocationConfig row and example per-lecturer seniority overrides."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-overrides", action="store_true",
            help="Only seed the GLOBAL config row; skip setting Lecturer.max_load_override.",
        )

    def handle(self, *args, **options):
        global_cfg, created = AllocationConfig.objects.get_or_create(
            department=None,
            defaults=dict(
                max_load_per_semester=6,
                max_load_enabled=True,
                split_threshold=200,
                split_size=100,
                splitting_enabled=True,
                pg_designations="Dr,Prof",
                allowed_semesters="1,2",
            ),
        )
        self.stdout.write(self.style.SUCCESS(
            f"GLOBAL AllocationConfig: {'created' if created else 'already exists'} "
            f"(max_load/sem={global_cfg.max_load_per_semester}, "
            f"split_threshold={global_cfg.split_threshold}, "
            f"split_size={global_cfg.split_size}, "
            f"pg_designations={global_cfg.pg_designations}, "
            f"allowed_semesters={global_cfg.allowed_semesters})"
        ))

        if options["skip_overrides"]:
            return

        updated = 0
        for lec in Lecturer.objects.filter(max_load_override__isnull=True):
            if lec.designation in SENIOR_DESIGNATIONS:
                lec.max_load_override = SENIOR_DESIGNATIONS[lec.designation]
                lec.save(update_fields=["max_load_override"])
                updated += 1
            elif lec.designation in JUNIOR_DESIGNATIONS:
                lec.max_load_override = JUNIOR_DESIGNATIONS[lec.designation]
                lec.save(update_fields=["max_load_override"])
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Set max_load_override on {updated} lecturers based on designation "
            f"(Prof=8, Dr=7, Mr/Ms/Mrs=4/semester). Re-run with --skip-overrides "
            f"if you don't want this automatic seniority pass."
        ))
