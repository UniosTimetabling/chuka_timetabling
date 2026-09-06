"""
export_data management command
================================

Exports the operational data of the timetabling system (venues, buildings,
programs, program courses, course allocations, student groups, timetables,
campuses, meeting venues, resits, feedback, notifications, etc.) WITHOUT
touching user accounts (django.contrib.auth: User/Group/Permission) or the
`department_management` app, as requested.

It supports three output formats:

  * json  - ONE JSON FILE PER MODEL (not a single whole-database dump),
            packaged together with a manifest.json into a single zip.
            This is the format `import_data` understands and reloads
            model-by-model, in the correct dependency order (e.g.
            Faculty before Department, Department before Program,
            Building before Venue, ...). Exporting per-model like this -
            instead of one giant fixture - is what makes importing a
            subset, retrying a failed model, or debugging a bad row
            actually manageable.
  * csv   - one .csv file per model, all zipped into a single archive.
            Human-readable, opens in Excel/Sheets, but is NOT reloadable
            (foreign keys are flattened to readable text).
  * xlsx  - a single Excel workbook with one sheet per model. Same
            human-readable flattening as csv, easiest to browse/share.

JSON EXPORT LAYOUT
-------------------
The zip produced by `--format json` contains:

    manifest.json                          <- import order + metadata
    001__faculty_management__faculty.json
    002__department_management__department.json   (only if --include-department)
    003__room_management__building.json
    004__room_management__venue.json
    ...

Each `NNN__app_label__model_name.json` file is a normal Django dumpdata
fixture (a JSON list of {model, pk, fields} objects) for that ONE model.
The numeric prefix is the safe import order: every model's dependencies
(anything it has a ForeignKey/OneToOneField/ManyToManyField to, that is
also part of this export) are guaranteed to appear in an earlier file.
`manifest.json` records the same order plus each model's dependencies and
row count explicitly, so `import_data` doesn't have to guess.

USAGE
-----
From the project root (where manage.py lives):

    # Per-model JSON export (zipped), safe to hand straight to import_data
    python manage.py export_data --format json

    # Same, but as a plain folder instead of a zip (handy for inspecting)
    python manage.py export_data --format json --no-zip

    # Human-readable Excel workbook, one sheet per model
    python manage.py export_data --format xlsx

    # Zipped CSVs
    python manage.py export_data --format csv

    # Only export specific apps
    python manage.py export_data --format json --apps room_management program_management course_allocation

    # See the resolved model list AND the safe import order, without writing anything
    python manage.py export_data --list

    # Exclude extra apps/models on top of the defaults
    python manage.py export_data --format json --exclude-app backup_system --exclude-model core.ActivityLog

    # Choose where the file/folder goes (default: ./exports/)
    python manage.py export_data --format json --output /tmp/my_export

INSTALL
-------
Drop this file at:
    export_import/management/commands/export_data.py
(create the empty `export_import/management/` and
 `export_import/management/commands/` folders with __init__.py files if
 they don't already exist) and it will show up automatically as a
 `manage.py` subcommand - no settings.py changes required.
"""
import io
import json
import os
import zipfile
from datetime import datetime

from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db.models import ManyToManyField

from export_import.dependency_utils import (
    MANIFEST_FILENAME,
    MANIFEST_FORMAT_VERSION,
    export_filename,
    model_label,
    resolve_excluded_apps,
    resolve_models,
    topological_order,
)


class Command(BaseCommand):
    help = (
        "Export venues/programs/course-allocation/timetable data (and similar) "
        "to JSON (one file per model + manifest), CSV or Excel, without "
        "exporting user accounts or the department_management app."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=["json", "csv", "xlsx"],
            default="json",
            help="Output format. json is the model-by-model, reloadable "
                 "format import_data understands; csv/xlsx are "
                 "human-readable reports only (default: json).",
        )
        parser.add_argument(
            "--output",
            default=None,
            help="Output file path (without extension) or directory. "
                 "Defaults to ./exports/<timestamp>_export",
        )
        parser.add_argument(
            "--apps",
            nargs="*",
            default=None,
            help="Restrict export to these app labels only "
                 "(e.g. room_management program_management course_allocation). "
                 "Default: every installed app except the excluded ones.",
        )
        parser.add_argument(
            "--exclude-app",
            nargs="*",
            default=[],
            help="Extra app label(s) to exclude, on top of the defaults "
                 "(auth, department_management, contenttypes, sessions, admin).",
        )
        parser.add_argument(
            "--exclude-model",
            nargs="*",
            default=[],
            help="Extra model(s) to exclude, as app_label.ModelName "
                 "(e.g. core.ActivityLog backup_system.AuditLog).",
        )
        parser.add_argument(
            "--include-users-app",
            action="store_true",
            help="Danger: also export django.contrib.auth (User/Group/Permission). "
                 "Off by default per the requirement to not touch users.",
        )
        parser.add_argument(
            "--include-department",
            action="store_true",
            help="Danger: also export department_management. Off by default.",
        )
        parser.add_argument(
            "--natural-keys",
            action="store_true",
            help="Resolve foreign keys (e.g. created_by/actor -> auth.User) to "
                 "human-readable natural keys instead of raw numeric IDs. Off by "
                 "default: it requires dereferencing every such FK, which fails "
                 "with 'User matching query does not exist' if any row has a "
                 "stale/orphaned user reference. Raw-PK export (the default) "
                 "is just as reloadable and never hits that.",
        )
        parser.add_argument(
            "--no-zip",
            action="store_true",
            help="(json format only) Write manifest.json + per-model json "
                 "files into a plain directory instead of zipping them up.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="Print which apps/models would be exported AND the safe "
                 "import order (with each model's dependencies), then exit "
                 "(no files written).",
        )

    # ------------------------------------------------------------------
    # shared helpers
    # ------------------------------------------------------------------
    def _default_output_base(self, fmt):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        exports_dir = os.path.join(os.getcwd(), "exports")
        os.makedirs(exports_dir, exist_ok=True)
        return os.path.join(exports_dir, f"timetabling_export_{stamp}")

    def _flatten_value(self, field, value):
        """Turn a model field value into something writable to a cell."""
        if value is None:
            return ""
        return value

    def _model_rows(self, model):
        """Yield (field_names, list_of_row_dicts) for a model, with FKs and
        M2M flattened to human-readable text rather than raw IDs. Used by
        the csv/xlsx (report-only) exporters."""
        fields = [
            f for f in model._meta.get_fields()
            if getattr(f, "concrete", False) and not isinstance(f, ManyToManyField)
        ]
        m2m_fields = [f for f in model._meta.get_fields() if isinstance(f, ManyToManyField)]

        field_names = [f.name for f in fields] + [f.name for f in m2m_fields]
        qs = model._default_manager.all().order_by("pk")

        rows = []
        for obj in qs.iterator():
            row = {}
            for f in fields:
                if f.is_relation:
                    related = getattr(obj, f.name, None)
                    row[f.name] = str(related) if related is not None else ""
                else:
                    row[f.name] = self._flatten_value(f, getattr(obj, f.name, ""))
            for f in m2m_fields:
                try:
                    related_qs = getattr(obj, f.name).all()
                    row[f.name] = ", ".join(str(r) for r in related_qs)
                except Exception:
                    row[f.name] = ""
            rows.append(row)
        return field_names, rows

    # ------------------------------------------------------------------
    # format writers
    # ------------------------------------------------------------------
    def _stream_model_fixture(self, model, out_fh, use_natural_keys, deferred_fields,
                               is_m2m_fields, label, deferred_relations_sink, batch_size=2000):
        """Write one model's dumpdata-equivalent JSON array straight to
        `out_fh`, processing the queryset via .iterator() in bounded
        batches (serializing/holding at most `batch_size` rows in memory
        at a time) instead of calling `dumpdata` once for the whole model.

        A single `call_command("dumpdata", label, ...)` still has to hold
        that ENTIRE model's serialized output in one string before it can
        be written out - fine for most models, but a single table with a
        very large row count (a big Timetable/CourseAllocation/meeting-slot
        table, say) can be big enough on its own to repeat the same
        OOM-kill problem the outer per-model streaming fix already solved
        for the export as a whole. This closes that remaining gap: no
        matter how many rows a model has, peak memory here stays
        proportional to `batch_size`, not to the model's total size.

        Returns the total record count written.
        """
        qs = model._default_manager.all().order_by("pk")
        count = 0
        first = True
        batch = []

        def flush(pending):
            nonlocal first
            if not pending:
                return
            payload = serializers.serialize(
                "json", pending,
                use_natural_foreign_keys=use_natural_keys,
                use_natural_primary_keys=False,
            )
            records = json.loads(payload)
            for rec in records:
                if deferred_fields:
                    fields = rec.get("fields", {})
                    for field_name in deferred_fields:
                        if field_name not in fields:
                            continue
                        original_value = fields[field_name]
                        empty_value = [] if field_name in is_m2m_fields else None
                        if original_value == empty_value:
                            continue  # nothing to defer, already empty
                        deferred_relations_sink.append({
                            "label": label,
                            "pk": rec.get("pk"),
                            "field": field_name,
                            "value": original_value,
                            "is_m2m": field_name in is_m2m_fields,
                        })
                        fields[field_name] = empty_value
                if not first:
                    out_fh.write(",\n")
                first = False
                json.dump(rec, out_fh, indent=2)

        out_fh.write("[\n")
        for obj in qs.iterator(chunk_size=batch_size):
            batch.append(obj)
            count += 1
            if len(batch) >= batch_size:
                flush(batch)
                batch = []
        flush(batch)
        out_fh.write("\n]")
        return count

    def _export_json(self, ordered_models, deps_map, deferred_map, warnings, base_path,
                      use_natural_keys, no_zip):
        """Write ONE fixture per model, plus a manifest.json recording the
        import order, each model's dependencies (within this export) and
        row count - then package it all as a zip (default) or a plain
        directory (--no-zip).

        Each model's rows are streamed straight into its output file in
        bounded batches via `_stream_model_fixture` (see above) - so
        memory is bounded by one batch of rows, not by any single model's
        total size, and models are written to disk/zip one at a time
        rather than being buffered together in memory.

        If `deferred_map` says a model has fields that had to be deferred
        to break a circular dependency (see dependency_utils.topological_order),
        those fields are stripped from the per-model fixture (saved
        empty/null) and their real values are recorded separately under
        manifest["deferred_relations"], to be patched back in by
        import_data once every model involved has been loaded.
        """
        manifest = {
            "format_version": MANIFEST_FORMAT_VERSION,
            "generated_at": datetime.now().isoformat(),
            "natural_keys": use_natural_keys,
            "warnings": warnings,
            "models": [],
            "deferred_relations": [],
        }

        # field_name -> is_m2m, per model label, needed to know how to
        # strip/record a deferred field's value correctly.
        m2m_field_names = {
            model_label(model): {f.name for f in model._meta.get_fields() if isinstance(f, ManyToManyField)}
            for model in ordered_models
        }

        if no_zip:
            out_dir = base_path
            os.makedirs(out_dir, exist_ok=True)
            dest = None
        else:
            out_path = base_path + ".zip"
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            dest = zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED)

        written_filenames = []
        try:
            for index, model in enumerate(ordered_models, start=1):
                label = model_label(model)
                deferred_fields = {
                    field_name
                    for target_fields in deferred_map.get(label, {}).values()
                    for field_name in target_fields
                }
                is_m2m = m2m_field_names.get(label, set())
                filename = export_filename(index, model)

                if dest is None:
                    with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as fh:
                        count = self._stream_model_fixture(
                            model, fh, use_natural_keys, deferred_fields, is_m2m,
                            label, manifest["deferred_relations"],
                        )
                else:
                    with dest.open(filename, "w") as zstream:
                        wrapper = io.TextIOWrapper(zstream, encoding="utf-8", write_through=True)
                        count = self._stream_model_fixture(
                            model, wrapper, use_natural_keys, deferred_fields, is_m2m,
                            label, manifest["deferred_relations"],
                        )
                        wrapper.flush()

                written_filenames.append(filename)
                manifest["models"].append({
                    "order": index,
                    "app_label": model._meta.app_label,
                    "model_name": model._meta.model_name,
                    "label": label,
                    "file": filename,
                    "count": count,
                    "depends_on": deps_map.get(label, []),
                    "deferred_fields": sorted(deferred_fields) if deferred_fields else [],
                })

            manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
            if dest is None:
                with open(os.path.join(out_dir, MANIFEST_FILENAME), "wb") as fh:
                    fh.write(manifest_bytes)
            else:
                dest.writestr(MANIFEST_FILENAME, manifest_bytes)
        finally:
            if dest is not None:
                dest.close()

        if no_zip:
            written = [os.path.join(out_dir, MANIFEST_FILENAME)] + [
                os.path.join(out_dir, f) for f in written_filenames
            ]
            return written, manifest

        return [out_path], manifest

    def _export_csv(self, models, base_path):
        import csv

        out_path = base_path + ".zip"
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for model in models:
                field_names, rows = self._model_rows(model)
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=field_names, extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
                fname = f"{model._meta.app_label}_{model._meta.model_name}.csv"
                zf.writestr(fname, buf.getvalue())
        return [out_path]

    def _export_xlsx(self, models, base_path):
        import pandas as pd

        out_path = base_path + ".xlsx"
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        used_sheet_names = set()
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            wrote_any = False
            for model in models:
                field_names, rows = self._model_rows(model)
                df = pd.DataFrame(rows, columns=field_names)
                sheet = f"{model._meta.app_label}_{model._meta.model_name}"[:31]
                base_sheet = sheet
                n = 1
                while sheet in used_sheet_names:
                    suffix = f"_{n}"
                    sheet = base_sheet[: 31 - len(suffix)] + suffix
                    n += 1
                used_sheet_names.add(sheet)
                df.to_excel(writer, sheet_name=sheet, index=False)
                wrote_any = True
            if not wrote_any:
                pd.DataFrame({"info": ["No models matched the given filters."]}).to_excel(
                    writer, sheet_name="info", index=False
                )
        return [out_path]

    # ------------------------------------------------------------------
    # entrypoint
    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        models = resolve_models(options)
        excluded_apps = sorted(resolve_excluded_apps(options))

        if not models:
            raise CommandError(
                "No models matched. Check --apps / --exclude-app / --exclude-model."
            )

        ordered_models, deps_map, warnings, deferred_map = topological_order(models)

        if options["list"]:
            self.stdout.write(self.style.WARNING("Excluded apps:"))
            for a in excluded_apps:
                self.stdout.write(f"  - {a}")
            self.stdout.write(
                self.style.SUCCESS(f"\nSafe import order ({len(ordered_models)} model(s)):")
            )
            for i, m in enumerate(ordered_models, start=1):
                label = model_label(m)
                deps = deps_map.get(label, [])
                dep_str = f"  (depends on: {', '.join(deps)})" if deps else ""
                self.stdout.write(f"  {i:>3}. {label}{dep_str}")
            if deferred_map:
                self.stdout.write(self.style.WARNING(
                    "\nCircular relationships resolved by deferring these optional "
                    "fields (saved empty on first load, patched in afterwards):"
                ))
                for label in sorted(deferred_map):
                    for target, fields in deferred_map[label].items():
                        self.stdout.write(f"  - {label}.{'/'.join(fields)} -> {target}")
            for w in warnings:
                self.stdout.write(self.style.WARNING(f"\nWARNING: {w}"))
            return

        for w in warnings:
            self.stdout.write(self.style.WARNING(f"WARNING: {w}"))

        base_path = options["output"] or self._default_output_base(options["format"])
        # if user passed a directory, drop a default filename inside it
        if options["output"] and os.path.isdir(options["output"]):
            base_path = os.path.join(
                options["output"],
                f"timetabling_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            )

        fmt = options["format"]
        self.stdout.write(f"Exporting {len(ordered_models)} model(s), format={fmt} ...")
        self.stdout.write(f"Excluded apps: {', '.join(excluded_apps)}")

        if fmt == "json":
            paths, manifest = self._export_json(
                ordered_models, deps_map, deferred_map, warnings, base_path,
                options["natural_keys"], options["no_zip"],
            )
            self.stdout.write(self.style.SUCCESS("\nModels exported, in safe import order:"))
            for entry in manifest["models"]:
                defer_note = (
                    f"  [{len(entry['deferred_fields'])} field(s) deferred: {', '.join(entry['deferred_fields'])}]"
                    if entry["deferred_fields"] else ""
                )
                self.stdout.write(
                    f"  {entry['order']:>3}. {entry['file']}  ({entry['count']} record(s)){defer_note}"
                )
            if manifest["deferred_relations"]:
                self.stdout.write(self.style.WARNING(
                    f"\n{len(manifest['deferred_relations'])} relation value(s) deferred to "
                    f"break circular dependencies; import_data will patch them back in "
                    f"automatically after every model is loaded."
                ))
        elif fmt == "csv":
            paths = self._export_csv(ordered_models, base_path)
        else:
            paths = self._export_xlsx(ordered_models, base_path)

        for p in paths:
            size = os.path.getsize(p)
            self.stdout.write(self.style.SUCCESS(f"Wrote {p} ({size:,} bytes)"))
