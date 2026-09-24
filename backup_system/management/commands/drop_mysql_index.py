"""
Drop a single named MySQL index, wherever it currently lives.

Built specifically to auto-heal a known deploy failure mode: MySQL error
1061 "Duplicate key name '<x>'" happens when a migration partially
applied on an earlier failed/interrupted deploy already created an
index, and a later retry tries to create it again from scratch as part
of the same migration step. Dropping the stray leftover and letting the
migration recreate it correctly is always safe for this specific error
— it is never safe to guess-fix any other kind of migration failure,
which is why this command does nothing but this one narrow thing.

Uses Django's own already-configured DB connection (same credentials
`manage.py migrate` itself used), so no extra CLI tools or env vars are
needed inside the container.

Usage:
    python manage.py drop_mysql_index <index_name>

Exits 0 and prints which table it dropped the index from if found and
dropped. Exits 1 (does nothing) if no index with that name currently
exists anywhere in the database — that's treated as a normal, safe
outcome (nothing to clean up), not an error.
"""
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

# MySQL identifiers here are always Django-generated (e.g.
# "timetable_examtemptimetable_venue_id_9776657a") — alnum + underscore
# only. Reject anything else outright rather than interpolate it into
# raw SQL.
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


class Command(BaseCommand):
    help = "Drop a named MySQL index (auto-heal for partially-applied migrations)."

    def add_arguments(self, parser):
        parser.add_argument("index_name", help="Exact index name to look up and drop.")

    def handle(self, *args, **options):
        index_name = options["index_name"]

        if not SAFE_IDENTIFIER.match(index_name):
            raise CommandError(f"Refusing to touch a non-identifier-looking name: {index_name!r}")

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT TABLE_NAME
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND INDEX_NAME = %s
                """,
                [index_name],
            )
            rows = cursor.fetchall()

            if not rows:
                self.stdout.write(f"No index named {index_name!r} exists — nothing to drop.")
                return

            for (table_name,) in rows:
                if not SAFE_IDENTIFIER.match(table_name):
                    raise CommandError(f"Refusing to touch a non-identifier-looking table: {table_name!r}")

                # DROP INDEX doesn't support parameterized identifiers;
                # both names were just validated against SAFE_IDENTIFIER
                # above and came from information_schema itself, not
                # user input, so this is safe to interpolate.
                drop_sql = f"DROP INDEX `{index_name}` ON `{table_name}`"

                try:
                    cursor.execute(drop_sql)
                except Exception as exc:
                    # MySQL error 1553: InnoDB requires that every column
                    # participating in a FK always have *some* index
                    # covering it, at all times. This is a structural
                    # rule, not a validation check — SET
                    # FOREIGN_KEY_CHECKS=0 only suspends validation of
                    # row data against the relationship and does NOT
                    # relax this requirement (confirmed the hard way:
                    # toggling it here still raises 1553 on the retry).
                    #
                    # The correct, standard pattern: give the FK column(s)
                    # a *different* covering index first, so the FK is
                    # never left without one, then drop the stray index
                    # with an ordinary DROP INDEX. No enforcement is ever
                    # suspended, so there is no window — however brief —
                    # where FK integrity isn't being checked.
                    if "1553" not in str(exc):
                        raise

                    self.stdout.write(
                        f"Index {index_name!r} is backing a foreign key — "
                        "creating a temporary covering index on the same "
                        "column(s) first, then retrying the drop."
                    )

                    cursor.execute(
                        """
                        SELECT COLUMN_NAME
                        FROM information_schema.STATISTICS
                        WHERE TABLE_SCHEMA = DATABASE()
                          AND TABLE_NAME = %s
                          AND INDEX_NAME = %s
                        ORDER BY SEQ_IN_INDEX
                        """,
                        [table_name, index_name],
                    )
                    columns = [row[0] for row in cursor.fetchall()]
                    if not columns:
                        raise CommandError(
                            f"Could not read columns for index {index_name!r} "
                            f"on {table_name!r} — refusing to guess."
                        )
                    for col in columns:
                        if not SAFE_IDENTIFIER.match(col):
                            raise CommandError(f"Refusing to touch a non-identifier-looking column: {col!r}")

                    temp_index_name = f"{index_name}_fktmp"
                    if not SAFE_IDENTIFIER.match(temp_index_name):
                        raise CommandError(f"Refusing to touch a non-identifier-looking name: {temp_index_name!r}")

                    cursor.execute(
                        """
                        SELECT COUNT(*)
                        FROM information_schema.STATISTICS
                        WHERE TABLE_SCHEMA = DATABASE()
                          AND TABLE_NAME = %s
                          AND INDEX_NAME = %s
                        """,
                        [table_name, temp_index_name],
                    )
                    (already_exists,) = cursor.fetchone()

                    if not already_exists:
                        cols_sql = ", ".join(f"`{c}`" for c in columns)
                        cursor.execute(
                            f"CREATE INDEX `{temp_index_name}` ON `{table_name}` ({cols_sql})"
                        )
                        self.stdout.write(f"Created temporary covering index {temp_index_name!r}.")
                    else:
                        self.stdout.write(
                            f"Temporary covering index {temp_index_name!r} already exists "
                            "from an earlier attempt — reusing it."
                        )

                    # FK now has a covering index other than the stray
                    # one, so this is now an ordinary, unconditional drop.
                    cursor.execute(drop_sql)

                    self.stdout.write(
                        self.style.WARNING(
                            f"Note: temporary index {temp_index_name!r} on {table_name!r} "
                            "is intentionally left in place. The migration's own next step "
                            "will create the correct replacement index; the temporary one is "
                            "then redundant but harmless. Clean it up manually once the "
                            "migration has fully succeeded, e.g.:\n"
                            f"  ALTER TABLE `{table_name}` DROP INDEX `{temp_index_name}`;"
                        )
                    )

                self.stdout.write(
                    self.style.SUCCESS(f"Dropped index {index_name!r} from table {table_name!r}.")
                )
