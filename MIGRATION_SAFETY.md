# Migration safety — what broke, why, and how to stop it happening in production

## What actually happened on your machine

1. You ran `migrate`, it started applying `course_allocation.0007_specialintakegroup_and_more`,
   and you `Ctrl+C`'d out of it (visible in your terminal history) while it was mid-flight.
2. **MySQL's `CREATE TABLE` / `ALTER TABLE ADD INDEX` statements auto-commit individually** —
   they are not part of one big rollback-able transaction the way Postgres DDL is. Django
   *tries* to wrap a migration in a transaction, but on MySQL that protection doesn't cover
   schema changes. So the `Ctrl+C` left the `course_allocation_specialintakegroup` table
   (and later, an index) **physically created in the database**, while Django's own
   bookkeeping table (`django_migrations`) still said 0007 had never run.
3. Every subsequent `migrate` tried to redo the same `CREATE TABLE` / `ADD INDEX` and failed
   with "already exists" / "duplicate key name", because the DB and Django's migration
   history disagreed about what had actually happened.
4. Separately, I found a **real bug in the generated migration itself**: it added an index on
   `special_intake_group` *before* the `AddField` that creates that column. That would have
   failed with an "unknown column" error the moment you got past the table/index collision.
   I've fixed the operation order in `0007_specialintakegroup_and_more.py` (verified with a
   static check against every migration in the project — see below).

## Fixing your current dev database

```bash
python manage.py repair_migration_0007
python manage.py migrate course_allocation
```

`repair_migration_0007` is a new management command (`course_allocation/management/commands/repair_migration_0007.py`).
It checks for each object 0007 could have partially created (table, FK, column, indexes) and
drops only what's actually there — safe to run more than once, and safe even if 0007 never
ran at all. Full explanation is in the command's docstring.

## Making sure this doesn't happen in production

1. **Never interrupt a running `migrate`.** If it must be stopped, let it fail on its own
   or wait for it to finish — don't `Ctrl+C` a schema change on MySQL.
2. **Back up before every production migration.** A `mysqldump` right before deploy is cheap
   insurance:
   ```bash
   mysqldump -u <user> -p <dbname> > backup_$(date +%Y%m%d_%H%M%S).sql
   ```
3. **Rehearse on a staging copy of the production database first**, not just on a dev DB with
   different data/state. That's exactly how the field-before-index bug in 0007 would have been
   caught before it reached you.
4. **Check what's pending before you run it:**
   ```bash
   python manage.py showmigrations course_allocation
   python manage.py migrate course_allocation --plan
   ```
5. If a migration *does* fail partway through in production, don't just re-run it — check
   `showmigrations` to see what Django thinks is applied, and inspect the actual schema
   (`SHOW CREATE TABLE ...`) before touching anything, so you fix the real mismatch instead of
   guessing.
6. I ran a static check across **every** migration file in this project (all apps) looking for
   the same "index added before its field exists" pattern that caused the 0007 bug — nothing
   else in the codebase has it as of this zip.
