"""
dependency_utils
=================

Shared helpers used by both `export_data` and `import_data` management
commands so the two can never silently disagree about:

  * which apps/models are eligible for export/import,
  * what a model's "nice", predictable export filename looks like, and
  * what order models must be imported in, so that a model is never
    loaded before the other models it has a foreign key, one-to-one, or
    many-to-many field pointing at (e.g. Department before Program,
    Program before ProgramCourse, Building before Venue, etc.).

Both commands import from here instead of duplicating this logic, so a
change to the naming convention or the dependency algorithm only has to
be made once.
"""
import re

from django.apps import apps as django_apps
from django.db.models import ForeignKey, ManyToManyField, OneToOneField

# Apps that are NEVER exported/imported, regardless of flags, because they
# are Django/3rd-party internals rather than business data.
ALWAYS_EXCLUDED_APPS = {
    "contenttypes",
    "sessions",
    "admin",  # django.contrib.admin (LogEntry) - not "admins" (our app)

    # --- Per-installation operational data, not academic/timetabling data.
    # Syncing these was the actual cause of multi-hour sync runs that
    # never finished and threw unrelated errors ("Column 'timestamp'
    # cannot be null", FK failures, etc.):
    #
    #  * "core" mixes real settings with the sync/audit bookkeeping models
    #    themselves (SyncRun, SyncModelLog, SyncRecordState, ActivityLog).
    #    Syncing those pushes a HOST's own sync history to the REMOTE,
    #    which then logs its own ingestion of that history, which then
    #    looks "changed" and gets sent again next run — a self-feeding
    #    loop that only grows and never finishes.
    #  * "backup_system" is per-node audit/backup machinery (AuditLog,
    #    BackupJob, UndoAction, its own SyncQueue) — huge, ever-growing,
    #    and meaningless on another node.
    #  * "documentation" is the internal help-wiki: comments, bookmarks,
    #    ratings and search/view logs that record every user interaction.
    #    It also has hard-required (non-nullable) ForeignKeys to User
    #    (PageComment.user, UserBookmark.user, DocumentationRating.user,
    #    UserReadingProgress.user) — since Users are never synced, every
    #    one of those rows was guaranteed to fail on the remote with a
    #    NOT NULL / FK violation.
    #  * "feedback" and "mess" (student feedback, cafeteria/mess menus)
    #    aren't timetabling data either.
    #
    # None of the genuine academic apps (department_management, program
    # _management, course_allocation, timetable, lecturer_portal, venue/
    # room management, resits_timetabling, campuses_timetable, etc.) have
    # any required FK into these apps or into auth.User — every such FK
    # in those apps is null=True — so excluding these here does not drop
    # any data the timetabling side actually needs.
    "core",
    "backup_system",
    "documentation",
    "feedback",
    "mess",
}

# Apps excluded by default per the original requirement: don't touch users
# or the department_management app unless explicitly opted in.
DEFAULT_EXCLUDED_APPS = {
    "auth",
    "department_management",
}

MANIFEST_FILENAME = "manifest.json"
MANIFEST_FORMAT_VERSION = 1


def model_label(model):
    """'app_label.model_name', always lowercase, e.g. 'room_management.venue'."""
    return f"{model._meta.app_label}.{model._meta.model_name}"


def resolve_excluded_apps(options):
    excluded = set(ALWAYS_EXCLUDED_APPS)
    excluded |= set(DEFAULT_EXCLUDED_APPS)
    if options.get("include_users_app"):
        excluded.discard("auth")
    if options.get("include_department"):
        excluded.discard("department_management")
    excluded |= set(options.get("exclude_app") or [])
    return excluded


def resolve_models(options):
    """Every installed model, minus excluded apps/models, optionally
    restricted to --apps. Same selection semantics export_data has
    always used."""
    excluded_apps = resolve_excluded_apps(options)
    excluded_models = {m.lower() for m in (options.get("exclude_model") or [])}
    only_apps = set(options["apps"]) if options.get("apps") else None

    models = []
    for model in django_apps.get_models():
        app_label = model._meta.app_label
        if app_label in excluded_apps:
            continue
        if only_apps is not None and app_label not in only_apps:
            continue
        label = model_label(model)
        if label in excluded_models:
            continue
        models.append(model)
    return models


def get_dependencies(model, candidate_labels):
    """Labels (restricted to candidate_labels) that `model` has a
    FK/O2O/M2M field pointing at. These are the models that must be
    imported before `model` - or already exist in the target database -
    for `model`'s rows to load without a foreign-key error.

    Self-references are ignored for ordering purposes: a model never
    "depends on itself". Self-referential trees (e.g. a parent/child
    Department-of-Department style FK) are a pre-existing limitation of
    single-fixture loaddata, not something introduced by this tool.
    """
    own_label = model_label(model)
    deps = set()
    for field in model._meta.get_fields():
        if isinstance(field, (ForeignKey, OneToOneField, ManyToManyField)):
            related_model = field.related_model
            if related_model is None:
                continue
            label = model_label(related_model)
            if label == own_label:
                continue
            if label in candidate_labels:
                deps.add(label)
    return deps


def get_dependency_edges(model, candidate_labels):
    """Like get_dependencies(), but per-field: yields
    (target_label, field_name, breakable) for every FK/O2O/M2M field on
    `model` that points at another model within candidate_labels.

    `breakable` means this ONE field's relationship can safely be loaded
    empty and patched in afterwards without violating a DB constraint:
      - ManyToManyField: always breakable. M2M rows live in a separate
        join table and are only linked via an explicit .set() call, so
        that call can simply be deferred - nothing about saving the base
        row itself requires the M2M to be populated yet.
      - ForeignKey / OneToOneField: breakable only if the column is
        nullable (field.null), since a NOT NULL FK column cannot be
        temporarily saved empty and filled in later.
    """
    own_label = model_label(model)
    edges = []
    for field in model._meta.get_fields():
        if isinstance(field, (ForeignKey, OneToOneField, ManyToManyField)):
            related_model = field.related_model
            if related_model is None:
                continue
            label = model_label(related_model)
            if label == own_label:
                continue
            if label not in candidate_labels:
                continue
            breakable = True if isinstance(field, ManyToManyField) else bool(field.null)
            edges.append((label, field.name, breakable))
    return edges


def topological_order(models):
    """Order `models` so every model comes after everything it depends on
    (Kahn's algorithm), breaking ties alphabetically by label so the same
    input always produces the same, human-predictable order.

    If a genuine circular dependency exists (e.g. model A has an optional
    FK to B, and B has an optional/M2M field back to A), this tries to
    resolve it automatically rather than just warning: any edge that is
    "breakable" - a nullable FK/O2O, or any M2M (M2M relations are always
    safe to defer, since they're only linked via a separate .set() call) -
    can be loaded empty and patched in afterwards. Only if a cycle can't
    be broken this way (i.e. it involves a required, non-nullable FK on
    both sides) does it fall back to alphabetical order with a warning.

    Returns (ordered_models, depends_on_map, warnings, deferred_map):
      - ordered_models: `models` re-ordered, dependencies-first
      - depends_on_map: {label: sorted [dependency labels within this set]}
      - warnings: human-readable strings for any TRULY unresolvable cycle
      - deferred_map: {label: {target_label: [field_name, ...]}} - fields
        that must be saved empty on first load and patched in afterwards,
        once `target_label` has also been loaded. Empty dict if nothing
        needed deferring.
    """
    by_label = {model_label(m): m for m in models}
    candidate_labels = set(by_label.keys())

    edges_by_label = {label: get_dependency_edges(m, candidate_labels) for label, m in by_label.items()}
    depends_on = {
        label: {target for target, _field, _breakable in edges}
        for label, edges in edges_by_label.items()
    }
    # Each value must be its OWN set object (not shared with `depends_on`),
    # since the loop below mutates them in place while whittling down
    # `remaining` - reusing the same set objects would corrupt depends_on too.
    remaining = {label: set(deps) for label, deps in depends_on.items()}
    ordered_labels = []
    warnings = []
    deferred_map = {}  # label -> {target_label: [field_name, ...]}

    def breakable_targets(label):
        """{target_label: [field_name, ...]} for edges from `label` where
        EVERY field pointing at that target is breakable (if even one
        field to the same target is a required, non-nullable FK, that
        target can't be treated as breakable overall)."""
        per_target_fields = {}
        per_target_all_breakable = {}
        for target, field_name, breakable in edges_by_label[label]:
            per_target_fields.setdefault(target, []).append(field_name)
            per_target_all_breakable[target] = per_target_all_breakable.get(target, True) and breakable
        return {t: fields for t, fields in per_target_fields.items() if per_target_all_breakable[t]}

    while remaining:
        ready = sorted(label for label, deps in remaining.items() if not deps)

        if not ready:
            # Stuck: try to break the cycle using breakable edges before
            # giving up. A stuck model can be placed now if every one of
            # its still-unmet dependencies (within `remaining`) is
            # breakable - those fields get saved empty and fixed up later.
            newly_ready = []
            round_deferred = {}
            for label, deps in remaining.items():
                unmet = deps  # deps still not yet placed
                breakable = breakable_targets(label)
                if unmet and unmet.issubset(breakable.keys()):
                    newly_ready.append(label)
                    fields_for_label = {}
                    for target in unmet:
                        fields_for_label[target] = breakable[target]
                    round_deferred[label] = fields_for_label

            if newly_ready:
                newly_ready.sort()
                for label in newly_ready:
                    deferred_map.setdefault(label, {}).update(round_deferred[label])
                ready = newly_ready
            else:
                # Truly unresolvable: some model in the cycle has a
                # required (non-nullable) dependency on another model
                # that's also stuck. Best-effort alphabetical fallback.
                stuck = sorted(remaining.keys())
                warnings.append(
                    "Circular dependency detected among: "
                    + ", ".join(f"{label} (needs {sorted(remaining[label])})" for label in stuck)
                    + ". None of these edges are nullable/optional, so it can't be "
                      "broken automatically. Falling back to alphabetical order for "
                      "these; the import may fail on a strict (e.g. MySQL/InnoDB) "
                      "database - consider making one side of the cycle nullable, "
                      "or loading it manually with FK checks temporarily disabled."
                )
                ready = stuck

        for label in ready:
            ordered_labels.append(label)
            remaining.pop(label, None)

        for label, deps in remaining.items():
            deps.difference_update(ready)
            # any dependency we just deferred for THIS model should also
            # stop blocking it, even if it wasn't in `ready` this round
            if label in deferred_map:
                deps.difference_update(deferred_map[label].keys())

    ordered_models = [by_label[label] for label in ordered_labels]
    depends_on_map = {label: sorted(deps) for label, deps in depends_on.items()}
    return ordered_models, depends_on_map, warnings, deferred_map


_SAFE_RE = re.compile(r"[^a-zA-Z0-9]+")


def export_filename(order_index, model):
    """Deterministic, self-describing filename for one model's export,
    e.g. '007__room_management__venue.json'.

    - The zero-padded numeric prefix makes the correct import order
      visible/sortable even without opening the manifest.
    - The 'app_label__model_name' part is exactly what the importer's
      naming-convention fallback parses back out, so the exporter and
      importer always agree on names without either hard-coding the
      other's model list.
    """
    app_label = _SAFE_RE.sub("", model._meta.app_label)
    model_name = _SAFE_RE.sub("", model._meta.model_name)
    return f"{order_index:03d}__{app_label}__{model_name}.json"


_FILENAME_RE = re.compile(r"^(?:(\d+)__)?([a-zA-Z0-9]+)__([a-zA-Z0-9]+)\.json$")


def parse_export_filename(filename):
    """Reverse of export_filename(). Returns (order_index_or_None,
    app_label, model_name), or None if `filename` doesn't match our
    naming convention (e.g. manifest.json, or an unrelated file)."""
    match = _FILENAME_RE.match(filename)
    if not match:
        return None
    order_str, app_label, model_name = match.groups()
    order_index = int(order_str) if order_str is not None else None
    return order_index, app_label, model_name
