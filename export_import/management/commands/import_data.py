"""
import_data management command
================================

Counterpart to `export_data`. Loads back a per-model export produced by
`python manage.py export_data --format json` (a zip or a directory
containing one dumpdata fixture per model plus a manifest.json), WITHOUT
touching user accounts (django.contrib.auth) or the
`department_management` app - same guardrail as the exporter, enforced
independently here too.

Models are imported ONE AT A TIME, in the dependency order recorded in
manifest.json (or, if there's no manifest, recomputed live from the
installed models' actual ForeignKey/OneToOneField/ManyToManyField graph).
That ordering is exactly what avoids the classic "IntegrityError: FK
points at a row that doesn't exist yet" problem you get when a single
whole-database fixture happens to list a dependent model before the model
it depends on: Faculty is always loaded before Department, Department
before Program, Building before Venue, and so on.

For backward compatibility, a single flat JSON file in the OLD
"whole-database dumpdata list" shape (as produced by earlier versions of
export_data, or plain `manage.py dumpdata`) is still accepted and handled
exactly as before.

SAFETY BEHAVIOUR
-----------------
  * Refuses to load any object whose app_label is auth or
    department_management, unless you pass --allow-users /
    --allow-department explicitly.
  * --dry-run parses the export and reports the planned import order and
    per-model object counts, without writing anything to the database.
  * By default the whole import runs inside ONE transaction, so a failure
    on any single model rolls back everything already loaded in that run
    (nothing partially applied). Pass --continue-on-error to instead
    import each model in its own savepoint and skip just the model(s)
    that fail, reporting a summary at the end.
  * If a model fails to load because of a missing foreign key, the error
    names that model's known dependencies (from the manifest, or the
    live model graph) so it's obvious which model needs importing/fixing
    first.
  * --backup-first runs `dumpdata` first and stores a JSON snapshot next
    to the input, as a rollback point, before importing.

USAGE
-----
    # Preview what a per-model export would do, in order, without writing anything
    python manage.py import_data --input ./exports/timetabling_export_20260101_010203.zip --dry-run

    # Actually import it (all-or-nothing)
    python manage.py import_data --input ./exports/timetabling_export_20260101_010203.zip

    # Import it, but skip (rather than abort on) any model that fails
    python manage.py import_data --input ./exports/timetabling_export_20260101_010203.zip --continue-on-error

    # Import from an unzipped export directory instead of a zip
    python manage.py import_data --input ./exports/timetabling_export_20260101_010203/

    # Old-style single whole-database fixture (still supported)
    python manage.py import_data --input ./my_old_export.json

DOCKER USAGE
------------
This project's `web` container has no bind mount of the project source,
so a host file has to be copied into the container first, and the
command has to be run with `docker compose exec`:

    # 1) copy the export from the host into the running web container
    docker compose cp ./my_export.zip web:/app/my_export.zip

    # 2) preview what it would do
    docker compose exec web python manage.py import_data \\
        --input /app/my_export.zip --dry-run

    # 3) actually load it (take a safety snapshot first)
    docker compose exec web python manage.py import_data \\
        --input /app/my_export.zip --backup-first

    # 4) (optional) remove the temp file from the container
    docker compose exec web rm /app/my_export.zip

See docker/import_data.sh for a wrapper script that does steps 1-4 for you.
"""
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime

from django.apps import apps as django_apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from export_import.dependency_utils import (
    MANIFEST_FILENAME,
    parse_export_filename,
    resolve_excluded_apps,
    topological_order,
)

ALWAYS_BLOCKED_APPS = {"contenttypes", "sessions", "admin"}
DEFAULT_BLOCKED_APPS = {"auth", "department_management"}


def _iter_json_array(path, chunk_size=1 << 20):
    """Stream-parse a top-level JSON array of {model, pk, fields} objects
    from `path`, yielding one object at a time, without ever holding the
    whole file's parsed contents in memory the way plain `json.load(fh)`
    does. Only used for the legacy (pre-manifest, whole-database) fixture
    format: a single old-style dumpdata/export can be the entire database,
    and loading it in one go is exactly the kind of memory spike that got
    export_data killed by the OOM killer - loaddata's own JSON deserializer
    has that same json.load()-everything behaviour, so the legacy import
    path below reads records via this generator instead of routing back
    through loaddata on a temp file.
    """
    decoder = json.JSONDecoder()
    with open(path, "r", encoding="utf-8") as fh:
        buf = fh.read(chunk_size)
        pos = 0

        def _grow():
            nonlocal buf, pos
            more = fh.read(chunk_size)
            if not more:
                return False
            buf = buf[pos:] + more
            pos = 0
            return True

        while pos < len(buf) and buf[pos].isspace():
            pos += 1
        while pos >= len(buf):
            if not _grow():
                raise CommandError(f"{path}: empty file, expected a JSON array")
            while pos < len(buf) and buf[pos].isspace():
                pos += 1
        if buf[pos] != "[":
            raise CommandError(f"{path}: expected a top-level JSON array of fixture objects")
        pos += 1

        while True:
            while True:
                while pos < len(buf) and (buf[pos].isspace() or buf[pos] == ","):
                    pos += 1
                if pos < len(buf):
                    break
                if not _grow():
                    return  # clean EOF between elements
            if buf[pos] == "]":
                return
            while True:
                try:
                    obj, end = decoder.raw_decode(buf, pos)
                    pos = end
                    yield obj
                    break
                except json.JSONDecodeError:
                    if not _grow():
                        raise CommandError(
                            f"{path}: unexpected end of file while parsing a fixture object"
                        )


class Command(BaseCommand):
    help = (
        "Import a per-model export produced by export_data (a zip/directory "
        "of one JSON fixture per model + manifest.json), loading models one "
        "at a time in dependency order, without touching user accounts or "
        "the department_management app. Also still accepts a single legacy "
        "whole-database JSON fixture for backward compatibility."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            required=True,
            help="Path to the export to import: a zip or directory produced "
                 "by `export_data --format json`, or (legacy) a single "
                 "whole-database JSON fixture. Inside the container, if run "
                 "in Docker - see docker/import_data.sh.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the planned import order and per-model object "
                 "counts without writing anything to the database.",
        )
        parser.add_argument(
            "--allow-users",
            action="store_true",
            help="Danger: also allow loading django.contrib.auth objects "
                 "(User/Group/Permission) found in the export. Off by default.",
        )
        parser.add_argument(
            "--allow-department",
            action="store_true",
            help="Danger: also allow loading department_management objects "
                 "found in the export. Off by default.",
        )
        parser.add_argument(
            "--backup-first",
            action="store_true",
            help="Run dumpdata first and save a JSON snapshot next to the "
                 "input, as a rollback point, before importing.",
        )
        parser.add_argument(
            "--continue-on-error",
            action="store_true",
            help="Import each model in its own savepoint: if one model's "
                 "file fails to load, skip it and keep going, instead of "
                 "rolling back everything imported so far in this run.",
        )

    def _blocked_apps(self, options):
        blocked = set(ALWAYS_BLOCKED_APPS) | set(DEFAULT_BLOCKED_APPS)
        if options["allow_users"]:
            blocked.discard("auth")
        if options["allow_department"]:
            blocked.discard("department_management")
        return blocked

    # ------------------------------------------------------------------
    # Multi-file (manifest-based or naming-convention-based) import
    # ------------------------------------------------------------------
    def _load_manifest_plan(self, source_dir, blocked_apps):
        manifest_path = os.path.join(source_dir, MANIFEST_FILENAME)
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)

        plan = []
        skipped = []
        skipped_labels = set()
        for entry in manifest.get("models", []):
            if entry["app_label"] in blocked_apps:
                skipped.append(entry["label"])
                skipped_labels.add(entry["label"])
                continue
            file_path = os.path.join(source_dir, entry["file"])
            if not os.path.isfile(file_path):
                raise CommandError(
                    f"manifest.json references '{entry['file']}' for "
                    f"{entry['label']} but that file is missing from the "
                    f"import source."
                )
            plan.append({
                "label": entry["label"],
                "file_path": file_path,
                "depends_on": entry.get("depends_on", []),
                "count": entry.get("count"),
            })

        deferred_relations = [
            rel for rel in manifest.get("deferred_relations", [])
            if rel["label"] not in skipped_labels
        ]
        return plan, skipped, deferred_relations

    def _load_convention_plan(self, source_dir, blocked_apps):
        """No manifest.json present - fall back to parsing the
        'NNN__app_label__model_name.json' naming convention export_data
        uses, and computing the dependency order ourselves from the live
        model graph (same algorithm export_data used to build it)."""
        candidates = []
        for filename in sorted(os.listdir(source_dir)):
            if filename == MANIFEST_FILENAME:
                continue
            parsed = parse_export_filename(filename)
            if not parsed:
                continue
            _, app_label, model_name = parsed
            try:
                model = django_apps.get_model(app_label, model_name)
            except LookupError:
                self.stdout.write(self.style.WARNING(
                    f"Skipping '{filename}': no installed model "
                    f"{app_label}.{model_name}."
                ))
                continue
            candidates.append((model, os.path.join(source_dir, filename)))

        if not candidates:
            return [], [], []

        models = [m for m, _ in candidates]
        file_by_label = {
            f"{m._meta.app_label}.{m._meta.model_name}": path for m, path in candidates
        }
        ordered_models, deps_map, warnings, deferred_map = topological_order(models)
        for w in warnings:
            self.stdout.write(self.style.WARNING(f"WARNING: {w}"))
        if deferred_map:
            self.stdout.write(self.style.WARNING(
                "NOTE: a circular dependency was found and could be broken by "
                "deferring some optional field(s), but this directory has no "
                "manifest.json, so the original (pre-deferral) field values "
                "aren't available here to patch back in automatically. If any "
                "of these rows relied on that optional field, re-export with "
                "`export_data --format json` (which writes a manifest.json) "
                "and import that instead: "
                + ", ".join(
                    f"{label} -> {list(targets)}" for label, targets in deferred_map.items()
                )
            ))

        plan = []
        skipped = []
        for model in ordered_models:
            label = f"{model._meta.app_label}.{model._meta.model_name}"
            if model._meta.app_label in blocked_apps:
                skipped.append(label)
                continue
            plan.append({
                "label": label,
                "file_path": file_by_label[label],
                "depends_on": deps_map.get(label, []),
                "count": None,
            })
        return plan, skipped, []

    def _run_multi_file_import(self, source_dir, options):
        """Returns True if `source_dir` looked like a per-model export and
        was handled (imported or reported on); False if it wasn't
        recognisable as one, so the caller can fall back elsewhere."""
        blocked_apps = self._blocked_apps(options)

        manifest_path = os.path.join(source_dir, MANIFEST_FILENAME)
        if os.path.isfile(manifest_path):
            plan, skipped, deferred_relations = self._load_manifest_plan(source_dir, blocked_apps)
        else:
            plan, skipped, deferred_relations = self._load_convention_plan(source_dir, blocked_apps)

        if not plan and not skipped:
            return False

        self.stdout.write(self.style.SUCCESS(f"Import plan ({len(plan)} model file(s), in order):"))
        for i, item in enumerate(plan, start=1):
            dep_str = f" (depends on: {', '.join(item['depends_on'])})" if item["depends_on"] else ""
            count_str = f" - {item['count']} record(s)" if item["count"] is not None else ""
            self.stdout.write(f"  {i:>3}. {item['label']}{count_str}{dep_str}")

        if skipped:
            self.stdout.write(self.style.WARNING(
                "\nBlocked (will NOT be imported - auth/department_management/django-internal):"
            ))
            for label in skipped:
                self.stdout.write(f"  - {label}")

        if deferred_relations:
            self.stdout.write(self.style.WARNING(
                f"\n{len(deferred_relations)} relation value(s) were deferred at export time "
                f"to break a circular dependency; they'll be patched back in after every "
                f"model above has loaded."
            ))

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\n--dry-run: no changes made."))
            return True

        if not plan:
            self.stdout.write(self.style.WARNING("Nothing to import."))
            return True

        if options["backup_first"]:
            self._write_backup([item["label"] for item in plan], source_dir)

        succeeded, failed = [], []

        if options["continue_on_error"]:
            for item in plan:
                try:
                    with transaction.atomic():
                        call_command("loaddata", item["file_path"])
                    succeeded.append(item["label"])
                    self.stdout.write(self.style.SUCCESS(f"  OK    {item['label']}"))
                except Exception as e:  # noqa: BLE001 - surface any loaddata failure and move on
                    failed.append((item["label"], str(e)))
                    dep_note = (
                        f" (this model depends on: {', '.join(item['depends_on'])} - "
                        f"make sure those rows are imported/already exist)"
                        if item["depends_on"] else ""
                    )
                    self.stdout.write(self.style.ERROR(f"  FAIL  {item['label']}: {e}{dep_note}"))
            if failed:
                self.stdout.write(self.style.WARNING(
                    f"\n{len(failed)} model(s) failed and were skipped; everything else committed."
                ))
            if deferred_relations:
                succeeded_set = set(succeeded)
                applicable = [rel for rel in deferred_relations if rel["label"] in succeeded_set]
                self._apply_deferred_relations(applicable, best_effort=True)
        else:
            try:
                with transaction.atomic():
                    for item in plan:
                        try:
                            call_command("loaddata", item["file_path"])
                        except Exception as e:
                            dep_hint = (
                                f" This model depends on: {', '.join(item['depends_on'])} - "
                                f"make sure those were imported first (or already exist in "
                                f"the target database)."
                                if item["depends_on"] else ""
                            )
                            raise CommandError(
                                f"Failed importing {item['label']} from "
                                f"{os.path.basename(item['file_path'])}: {e}.{dep_hint}"
                            )
                        succeeded.append(item["label"])
                        self.stdout.write(self.style.SUCCESS(f"  OK    {item['label']}"))

                    if deferred_relations:
                        self._apply_deferred_relations(deferred_relations, best_effort=False)
            except CommandError:
                self.stdout.write(self.style.ERROR(
                    "\nImport failed - the whole batch was rolled back (nothing partially "
                    "applied). Fix the issue above, or re-run with --continue-on-error to "
                    "skip just the failing model(s) instead."
                ))
                raise

        self.stdout.write(self.style.SUCCESS(
            f"\nImport complete: {len(succeeded)} model file(s) loaded"
            + (f", {len(failed)} failed" if failed else "")
            + f", {len(skipped)} model(s) skipped (blocked apps)."
        ))
        return True

    def _apply_deferred_relations(self, deferred_relations, best_effort):
        """Patch back in the field values that export_data had to save
        empty/null in order to break a circular dependency (see
        dependency_utils.topological_order). Called once every model
        involved has already been loaded, so the target rows now exist.

        `best_effort=True` (used with --continue-on-error) logs and skips
        any relation that fails rather than aborting the whole run.
        `best_effort=False` (the default, single-transaction path) lets
        any failure propagate so the whole import rolls back, same as a
        normal model-load failure would.
        """
        self.stdout.write(f"\nPatching {len(deferred_relations)} deferred relation value(s) back in ...")
        applied, failed = 0, 0
        for rel in deferred_relations:
            app_label, model_name = rel["label"].split(".", 1)
            try:
                model = django_apps.get_model(app_label, model_name)
                if rel["is_m2m"]:
                    obj = model._default_manager.get(pk=rel["pk"])
                    getattr(obj, rel["field"]).set(rel["value"])
                else:
                    model._default_manager.filter(pk=rel["pk"]).update(**{rel["field"]: rel["value"]})
                applied += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                message = (
                    f"  Could not patch {rel['label']}.{rel['field']} for pk={rel['pk']}: {e}"
                )
                if best_effort:
                    self.stdout.write(self.style.ERROR(message))
                else:
                    raise CommandError(message + " (import rolled back)")
        self.stdout.write(self.style.SUCCESS(
            f"Patched {applied} relation value(s)."
            + (f" {failed} failed and were skipped." if failed else "")
        ))

    def _write_backup(self, labels, near_path):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_base = os.path.join(
            os.path.dirname(os.path.abspath(near_path)) or ".",
            f"pre_import_backup_{stamp}",
        )
        self.stdout.write(f"\nTaking safety backup before import -> {backup_base}.json ...")
        with open(backup_base + ".json", "w", encoding="utf-8") as bfh:
            call_command(
                "dumpdata",
                *sorted(labels),
                indent=2,
                natural_foreign=False,  # raw PKs - avoids "X matching query
                                         # does not exist" on any stale FK
                stdout=bfh,
            )
        self.stdout.write(self.style.SUCCESS("Backup written."))

    # ------------------------------------------------------------------
    # Legacy single-file (old whole-database dumpdata list) import
    # ------------------------------------------------------------------
    def _run_legacy_single_file_import(self, input_path, options):
        # Quick shape check without holding the file in memory: peek at the
        # first non-whitespace character rather than json.load()-ing it.
        with open(input_path, "r", encoding="utf-8") as fh:
            head = fh.read(4096).lstrip()
        if not head:
            raise CommandError(f"{input_path} is empty.")
        if head[0] != "[":
            raise CommandError(
                "Expected a Django fixture (a JSON list of {model, pk, fields} "
                "objects) - this doesn't look like one, and it isn't a "
                "manifest-based export directory/zip either."
            )

        blocked_apps = self._blocked_apps(options)

        # First streamed pass: just tally counts, never holding any
        # records (or the parsed file) in memory at once - same principle
        # as the export_data fix, applied to reading this time.
        counts, skipped_counts, total = {}, {}, 0
        for rec in _iter_json_array(input_path):
            total += 1
            model_label = rec.get("model", "")  # "app_label.model_name"
            app_label = model_label.split(".")[0] if "." in model_label else model_label
            if app_label in blocked_apps:
                skipped_counts[model_label] = skipped_counts.get(model_label, 0) + 1
            else:
                counts[model_label] = counts.get(model_label, 0) + 1

        self.stdout.write(self.style.WARNING(
            f"Legacy whole-database fixture detected (no manifest.json / per-model "
            f"files found): {input_path}"
        ))
        self.stdout.write(f"Total records in file: {total}")

        if skipped_counts:
            self.stdout.write(self.style.WARNING(
                "\nBlocked (will NOT be imported — auth/department_management/django-internal):"
            ))
            for label, n in sorted(skipped_counts.items()):
                self.stdout.write(f"  - {label}: {n}")

        self.stdout.write(self.style.SUCCESS("\nWill import:"))
        if not counts:
            self.stdout.write("  (nothing — every record was blocked)")
        for label, n in sorted(counts.items()):
            self.stdout.write(f"  - {label}: {n}")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\n--dry-run: no changes made."))
            return

        kept_total = sum(counts.values())
        if not kept_total:
            self.stdout.write(self.style.WARNING("Nothing to import. Exiting."))
            return

        if options["backup_first"]:
            self._write_backup(sorted(counts.keys()), input_path)

        # Second streamed pass: deserialize and save one record at a time
        # inside a single transaction, using Django's low-level python
        # deserializer directly. This intentionally avoids the old
        # write-a-filtered-temp-file-then-call-loaddata round trip: that
        # would still call json.load() on the whole temp file internally
        # (loaddata's JSON deserializer does exactly that), so it held the
        # same data in memory two or three times over for no benefit.
        # `deserialized_obj.save()` here is the same call loaddata makes
        # per object, so behaviour (including FK/natural-key handling) is
        # unchanged - only the memory profile is.
        from django.core.serializers.python import Deserializer as python_deserializer

        self.stdout.write("\nLoading data ...")
        loaded = 0
        try:
            with transaction.atomic():
                for rec in _iter_json_array(input_path):
                    model_label = rec.get("model", "")
                    app_label = model_label.split(".")[0] if "." in model_label else model_label
                    if app_label in blocked_apps:
                        continue
                    for deserialized_obj in python_deserializer([rec]):
                        deserialized_obj.save()
                    loaded += 1
        except Exception as e:  # noqa: BLE001 - surface, then roll back whole batch
            raise CommandError(
                f"Import failed after {loaded} object(s): {e} (whole batch rolled back)"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nImport complete: {loaded} object(s) loaded, "
            f"{sum(skipped_counts.values())} object(s) skipped (blocked apps)."
        ))

    # ------------------------------------------------------------------
    # entrypoint
    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        input_path = options["input"]
        if not os.path.exists(input_path):
            raise CommandError(f"No such file or directory: {input_path}")

        tmp_extract_dir = None
        try:
            if os.path.isdir(input_path):
                handled = self._run_multi_file_import(input_path, options)
                if handled:
                    return
                raise CommandError(
                    f"{input_path} is a directory but contains neither a "
                    f"manifest.json nor recognisably-named per-model json files "
                    f"(expected names like '001__app_label__model_name.json')."
                )

            if zipfile.is_zipfile(input_path):
                tmp_extract_dir = tempfile.mkdtemp(prefix="import_data_")
                with zipfile.ZipFile(input_path) as zf:
                    zf.extractall(tmp_extract_dir)
                handled = self._run_multi_file_import(tmp_extract_dir, options)
                if handled:
                    return
                raise CommandError(
                    f"{input_path} is a zip but contains neither a "
                    f"manifest.json nor recognisably-named per-model json files."
                )

            # Plain .json file: legacy whole-database fixture.
            self._run_legacy_single_file_import(input_path, options)
        finally:
            if tmp_extract_dir:
                shutil.rmtree(tmp_extract_dir, ignore_errors=True)
