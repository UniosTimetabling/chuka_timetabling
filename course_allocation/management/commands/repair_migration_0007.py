"""
One-off repair command for the course_allocation.0007_specialintakegroup_and_more
migration.

WHY THIS EXISTS
----------------
MySQL's DDL statements (CREATE TABLE, ALTER TABLE ADD INDEX, etc.) each
auto-commit individually -- they are NOT wrapped in the same transaction as
the rest of the migration, even though Django *tries* to run the whole
migration atomically. If a `migrate` run is interrupted (Ctrl+C, crashed
process, killed deploy, timeout) partway through a multi-operation
migration on MySQL, some of its CREATE TABLE / ADD INDEX / ADD COLUMN
statements can already be committed to the database while Django's
`django_migrations` bookkeeping table still says the migration was never
applied. The next `migrate` then tries to redo those same operations and
fails with "already exists" / "duplicate key name" errors.

This command cleans up every object migration 0007 could have partially
created, in the safe order (drop FK -> drop column -> drop index -> drop
table), checking existence before each step so it is safe to run as many
times as you like, and safe to run even if 0007 never got applied at all.

USAGE
-----
    python manage.py repair_migration_0007
    python manage.py migrate course_allocation

DO NOT run this against a database where 0007 has already been fully
applied and is in active use -- it will delete the SpecialIntakeGroup table
and all its data. Check `python manage.py showmigrations course_allocation`
first; only run this if 0007 is shown as unapplied ([ ]) but you're still
hitting "already exists" errors when trying to apply it.
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder


class Command(BaseCommand):
    help = (
        "Removes any partially-applied database objects left behind by an "
        "interrupted run of course_allocation.0007_specialintakegroup_and_more, "
        "so the migration can be re-applied cleanly. Safe to run repeatedly."
    )

    ALLOC_TABLE = "course_allocation_courseallocation"
    GROUP_TABLE = "course_allocation_specialintakegroup"
    INDEX_NAMES = ["course_allo_intake_c8dbfe_idx", "course_allo_special_445e2e_idx"]
    FK_COLUMN = "special_intake_group_id"

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            existing_tables = set(connection.introspection.table_names(cursor))

            # 1. Drop the SpecialIntakeGroup table if it exists.
            if self.GROUP_TABLE in existing_tables:
                self.stdout.write(f"Dropping table `{self.GROUP_TABLE}` ...")
                cursor.execute(f"DROP TABLE `{self.GROUP_TABLE}`")
                self.stdout.write(self.style.SUCCESS("  dropped."))
            else:
                self.stdout.write(f"`{self.GROUP_TABLE}` not present, skipping.")

            if self.ALLOC_TABLE not in existing_tables:
                self.stdout.write(self.style.WARNING(
                    f"`{self.ALLOC_TABLE}` not found -- check your DB settings."
                ))
                return

            constraints = connection.introspection.get_constraints(cursor, self.ALLOC_TABLE)
            columns = [c.name for c in connection.introspection.get_table_description(cursor, self.ALLOC_TABLE)]

            # 2. Drop the FK constraint + column on course_allocation, if present.
            #    FK must go before the column that carries it (MySQL requirement).
            if self.FK_COLUMN in columns:
                fk_name = None
                for name, info in constraints.items():
                    if info.get("foreign_key") and self.FK_COLUMN in info.get("columns", []):
                        fk_name = name
                        break
                if fk_name:
                    self.stdout.write(f"Dropping foreign key `{fk_name}` ...")
                    cursor.execute(f"ALTER TABLE `{self.ALLOC_TABLE}` DROP FOREIGN KEY `{fk_name}`")
                    self.stdout.write(self.style.SUCCESS("  dropped."))
                self.stdout.write(f"Dropping column `{self.FK_COLUMN}` ...")
                cursor.execute(f"ALTER TABLE `{self.ALLOC_TABLE}` DROP COLUMN `{self.FK_COLUMN}`")
                self.stdout.write(self.style.SUCCESS("  dropped."))
                # refresh constraint list, dropping the column may have dropped its index too
                constraints = connection.introspection.get_constraints(cursor, self.ALLOC_TABLE)
            else:
                self.stdout.write(f"Column `{self.FK_COLUMN}` not present, skipping.")

            # 3. Drop the two indexes, if still present.
            for index_name in self.INDEX_NAMES:
                if index_name in constraints:
                    self.stdout.write(f"Dropping index `{index_name}` ...")
                    cursor.execute(f"ALTER TABLE `{self.ALLOC_TABLE}` DROP INDEX `{index_name}`")
                    self.stdout.write(self.style.SUCCESS("  dropped."))
                else:
                    self.stdout.write(f"Index `{index_name}` not present, skipping.")

        # 4. Make sure Django's bookkeeping doesn't think 0007 already ran
        #    (it shouldn't, but this makes the command fully idempotent/safe).
        recorder = MigrationRecorder(connection)
        applied = recorder.applied_migrations()
        key = ("course_allocation", "0007_specialintakegroup_and_more")
        if key in applied:
            self.stdout.write("Unmarking 0007 as applied in django_migrations ...")
            recorder.record_unapplied(*key)

        self.stdout.write(self.style.SUCCESS(
            "\nCleanup complete. The database is back to its pre-0007 state.\n"
            "Now run:\n\n    python manage.py migrate course_allocation\n"
        ))
