"""
sync_engine
============

Implements the HOST -> REMOTE data sync feature.

  * `run_sync()` runs on the HOST. It walks every syncable model in the
    same dependency-safe order `export_data`/`import_data` already use
    (Faculty -> Department -> Program -> ProgramCourse -> course
    allocations / lecturer-program mappings -> ... ), works out which
    rows are new or changed since the last successful sync (by content
    hash, via `core.models.SyncRecordState`), and POSTs just those rows
    to the remote's `receive_sync` endpoint, one model at a time, with a
    short pause between models so a big sync doesn't hammer the network
    or the remote server, and doesn't block the web worker for so long
    that the request/process looks hung.

  * `ingest_model_payload()` runs on the REMOTE (see export_import.views
    .receive_sync). It deserializes one model's pushed rows and saves
    them, tolerating individual row failures without losing the rest of
    the batch.

Everything here deliberately reuses export_import.dependency_utils
(topological_order / resolve_models) instead of re-deriving the model
graph, so the export/import tooling and the live sync feature can never
silently disagree about ordering.
"""
import hashlib
import json
import threading
import time

import requests
from django.apps import apps as django_apps
from django.core import serializers
from django.core.serializers.python import Deserializer as python_deserializer
from django.db import transaction
from django.db.models import ForeignKey, ManyToManyField, OneToOneField
from django.utils import timezone

from export_import.dependency_utils import model_label, resolve_models, topological_order
from export_import import sync_crypto
from export_import import sync_tls

SYNC_RECEIVE_PATH = "/sync/receive/"
SYNC_PING_PATH = "/sync/ping/"
SYNC_VERIFY_PKS_PATH = "/sync/verify-pks/"
REQUEST_TIMEOUT_SECONDS = 30
PING_TIMEOUT_SECONDS = 8
VERIFY_TIMEOUT_SECONDS = 20

# Large models (ProgramCourse, ArchivedCourseAllocation, Timetable, ...)
# were being sent as ONE giant POST per model. Two things broke on that:
#   1. Django's default DATA_UPLOAD_MAX_MEMORY_SIZE (2.5MB) on the REMOTE
#      rejects an oversized request body outright with a bare 400, before
#      our view code ever runs — which is exactly the unexplained
#      "400 Client Error: Bad Request" some big models were failing with.
#   2. A very large payload can take longer to encrypt/transmit/ingest
#      than REQUEST_TIMEOUT_SECONDS, which is what caused the
#      "Read timed out (read timeout=30)" failure on timetable.timetable.
# Capping how many rows go in a single request keeps every request small
# and fast regardless of how big any one model's table gets, and a
# failed chunk only has to resend that chunk, not the whole model.
SYNC_MAX_ROWS_PER_REQUEST = 500

# A run genuinely stuck in "running" (dev server auto-reloaded mid-sync,
# the daemon thread died some other way, the process was killed) would
# otherwise block every future sync forever, since both trigger_sync and
# auto_run_sync refuse to start a new run while one is already "running".
# Anything still "running" past this many minutes is almost certainly
# dead, not actually working — reap_stale_runs() below fails it so a new
# attempt can start.
STALE_RUN_MINUTES = 15


def reap_stale_runs():
    """Marks any SyncRun stuck at status='running' for longer than
    STALE_RUN_MINUTES as 'failed'. Call this before checking for an
    in-progress run (trigger_sync, auto_run_sync) so a dead run from a
    crashed thread/restarted worker doesn't permanently block every
    later sync attempt. Returns how many rows were reaped."""
    from core.models import SyncRun

    cutoff = timezone.now() - timezone.timedelta(minutes=STALE_RUN_MINUTES)
    stale = SyncRun.objects.filter(status="running", started_at__lt=cutoff)
    count = stale.count()
    if count:
        stale.update(
            status="failed",
            finished_at=timezone.now(),
            error_message=(
                f"Automatically marked failed: still 'running' after "
                f"{STALE_RUN_MINUTES} minutes, which was blocking every later "
                f"sync attempt (likely the process handling it was restarted "
                f"or killed mid-run)."
            ),
        )
    return count


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------
def get_sync_plan():
    """Dependency-safe list of every model this feature syncs, in send
    order: department_management first, then everything that depends on
    it (program_management, course allocations, lecturer/program
    mappings, timetables, ...). Users (django.contrib.auth) are never
    included, matching the "except the users" requirement."""
    models = resolve_models({"include_department": True})
    ordered_models, deps_map, warnings, deferred_map = topological_order(models)
    return ordered_models, deps_map, warnings, deferred_map


def _external_fk_field_names(model, candidate_labels):
    """Field names on `model` that are a ForeignKey/OneToOneField pointing
    at a model NOT included in the sync plan — most commonly auth.User
    (Department.leader, Faculty.leader, Timetable.archived_by, an audit
    log's actor/approved_by/requested_by, ...).

    These fields are deliberately nullable (null=True) in every case
    found in this codebase, which is exactly what makes this fixable:
    the value is a primary key from the HOST's auth_user table. Since
    Users are never synced ("except users"), that same numeric id on the
    REMOTE either doesn't exist or belongs to an unrelated person. Sent
    as-is, the row hits a permanent FK-constraint violation on save —
    and because the row's content hash never changes, every future sync
    run rejects it identically forever (unlike a row waiting on a
    same-run dependency, this never self-heals via retry).

    Only nullable fields are included here — a non-nullable FK pointing
    outside the sync set would be a genuine, unfixable modeling problem,
    and forcing it to null would just trade a loud, honest failure for a
    silent, wrong save. Those are left alone so they still surface as an
    error worth looking at, rather than being papered over here.
    """
    own_label = model_label(model)
    names = []
    for field in model._meta.get_fields():
        if isinstance(field, (ForeignKey, OneToOneField)):
            related_model = field.related_model
            if related_model is None:
                continue
            label = model_label(related_model)
            if label == own_label:
                continue
            if label not in candidate_labels and field.null:
                names.append(field.name)
    return names


def _strip_external_fks(record, external_field_names):
    """Mutates a single deserialized-fixture `record` (the dict shape
    Django's JSON serializer produces: {"model":..., "pk":..., "fields":
    {...}}), nulling out any field named in `external_field_names`. See
    _external_fk_field_names() for why this is necessary and safe."""
    if not external_field_names:
        return record
    fields = record.get("fields", {})
    for name in external_field_names:
        if name in fields:
            fields[name] = None
    return record


def _row_hash(model, obj, m2m_field_names, external_field_names=()):
    """Deterministic content hash for one row, so the host can tell
    whether it has changed since the last successful sync without
    keeping a full copy of the remote's database around to diff
    against."""
    payload = serializers.serialize("json", [obj], use_natural_foreign_keys=False, use_natural_primary_keys=False)
    record = json.loads(payload)[0]
    # Null out FKs to excluded models (e.g. auth.User) BEFORE hashing, so
    # the hash reflects exactly what actually gets sent (see
    # _strip_external_fks) — otherwise a row would look "changed" every
    # run even though nothing about the sent payload differs.
    _strip_external_fks(record, external_field_names)
    # M2M ordering isn't guaranteed to be stable row-to-row, so sort it
    # before hashing to avoid false "changed" positives.
    for field_name in m2m_field_names:
        if field_name in record.get("fields", {}):
            record["fields"][field_name] = sorted(record["fields"][field_name])
    canonical = json.dumps(record, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------
# HOST side
# ----------------------------------------------------------------------
def _changed_rows_for_model(model, label, external_field_names=()):
    """Returns (changed_objs, changed_hashes_by_pk, total_count).
    Only rows whose content hash differs from (or is missing from)
    SyncRecordState are included — unchanged rows are skipped entirely,
    which is what keeps a re-sync of an already-synced system fast.

    Returns the model instances themselves (not yet serialized) so the
    caller can split them into request-sized chunks before turning any
    of them into JSON — see SYNC_MAX_ROWS_PER_REQUEST."""
    from core.models import SyncRecordState

    m2m_field_names = {f.name for f in model._meta.get_fields() if isinstance(f, ManyToManyField)}
    known_hashes = dict(
        SyncRecordState.objects.filter(app_label=model._meta.app_label, model_name=model._meta.model_name)
        .values_list("object_id", "content_hash")
    )

    qs = model._default_manager.all().order_by("pk")
    changed_objs = []
    changed_hashes = {}
    total = 0
    for obj in qs.iterator(chunk_size=1000):
        total += 1
        h = _row_hash(model, obj, m2m_field_names, external_field_names)
        pk_str = str(obj.pk)
        if known_hashes.get(pk_str) != h:
            changed_objs.append(obj)
            changed_hashes[pk_str] = h

    return changed_objs, changed_hashes, total


def _chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _fetch_remote_existing_pks(remote_url, token, app_label, model_name, verify_ssl=True):
    """Asks the REMOTE which pks it actually has right now for this
    model. Returns a set[str], or None if the check itself couldn't be
    completed (network error, remote not upgraded yet, etc.) — None
    means "unknown", NOT "empty", which matters below: reconciliation
    is skipped rather than treating every row as missing."""
    resp = requests.post(
        remote_url.rstrip("/") + SYNC_VERIFY_PKS_PATH,
        headers={"X-Sync-Token": token, "Content-Type": "application/json"},
        data=json.dumps({"app_label": app_label, "model_name": model_name}),
        timeout=VERIFY_TIMEOUT_SECONDS,
        verify=verify_ssl,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ValueError(data.get("error", "Remote rejected the verify-pks request."))
    return {str(pk) for pk in data.get("pks", [])}


def _reconcile_stale_hashes(model, remote_url, token, verify_ssl=True):
    """The bug this fixes: SyncRecordState is a HOST-side belief — "I
    already got this row onto the remote" — recorded the moment a chunk
    POST returns success. But a row can be recorded as synced and then
    genuinely NOT exist on the remote: an old bug (before the failed_pks
    fix existed) that marked a whole chunk synced on HTTP 200 regardless
    of per-row outcome, a remote DB that was reset/restored, a row
    deleted directly on the remote outside of sync, etc. Once that
    happens the hash never looks "changed" again (the host row itself
    didn't change), so the row is silently skipped forever — exactly
    what caused Program to sit at SKIP (0/328 changed) while every
    ProgramCourse depending on it was rejected for a Program that was
    never actually there.

    This asks the remote for ground truth — its real, current set of
    pks for this model — and deletes the HOST's SyncRecordState entries
    for any pk the remote doesn't actually have. Deleting the stale
    record (rather than trying to force a hash mismatch) means the very
    next line of run_sync (_changed_rows_for_model) naturally treats
    that row as new/never-synced and resends it, using the exact same
    path as normal first-time sync.

    Best-effort and silent-safe by design: if the remote hasn't been
    upgraded with the /sync/verify-pks/ endpoint yet, or the check
    fails for any reason (timeout, bad response, model too new on this
    side), this returns 0 and changes nothing — run_sync proceeds
    exactly as it did before this existed. It should never be able to
    make a sync run fail that would otherwise have succeeded.
    """
    from core.models import SyncRecordState

    app_label, model_name = model._meta.app_label, model._meta.model_name
    try:
        remote_pks = _fetch_remote_existing_pks(remote_url, token, app_label, model_name, verify_ssl=verify_ssl)
    except Exception:  # noqa: BLE001 - reconciliation is best-effort, never fatal
        return 0, False

    if remote_pks is None:
        return 0, False

    local_qs = SyncRecordState.objects.filter(app_label=app_label, model_name=model_name)
    local_ids = set(local_qs.values_list("object_id", flat=True))
    stale_ids = local_ids - remote_pks
    if not stale_ids:
        return 0, True

    local_qs.filter(object_id__in=stale_ids).delete()
    return len(stale_ids), True


def _post_model_batch(remote_url, token, label, app_label, model_name, fixture_json, order, total_models, verify_ssl=True):
    fixture_encrypted = sync_crypto.encrypt_payload(fixture_json, token)
    resp = requests.post(
        remote_url.rstrip("/") + SYNC_RECEIVE_PATH,
        headers={"X-Sync-Token": token, "Content-Type": "application/json"},
        data=json.dumps({
            "app_label": app_label,
            "model_name": model_name,
            "order": order,
            "total_models": total_models,
            "fixture_encrypted": fixture_encrypted,
        }),
        timeout=REQUEST_TIMEOUT_SECONDS,
        verify=verify_ssl,
    )
    resp.raise_for_status()
    return resp.json()


def test_connection():
    """
    Actively checks whether this HOST can currently reach its configured
    remote and that the remote accepts the shared token — a tiny GET to
    /sync/ping/, not a data push. Safe to call often (page load, a
    "Test Connection" button, a background poll) since it does no
    database writes on the remote side beyond bumping its
    `last_contacted_at` bookkeeping field.

    Always records the outcome on THIS node (last_connection_check_at /
    last_connection_ok / last_connection_error) so the result survives
    across requests and is what powers the status badge shown on every
    page. Returns a small dict describing the outcome.
    """
    from core.models import SyncNode

    node = SyncNode.get_settings()
    now = timezone.now()

    if not node.is_host:
        return {"ok": False, "error": "This installation is not in HOST mode."}
    if not node.remote_url or not node.get_token():
        return {"ok": False, "error": "Remote URL and/or shared token are not configured."}

    token = node.get_token()
    ping_url = node.remote_url.rstrip("/") + SYNC_PING_PATH

    def _record(ok, error=""):
        node.last_connection_ok = ok
        node.last_connection_error = error
        node.last_connection_check_at = now
        node.save(update_fields=["last_connection_ok", "last_connection_error", "last_connection_check_at"])

    proceed, verify, pin_error = sync_tls.resolve_verify(node)
    if not proceed:
        _record(False, pin_error)
        return {"ok": False, "error": pin_error}

    try:
        resp = requests.get(
            ping_url, headers={"X-Sync-Token": token},
            timeout=PING_TIMEOUT_SECONDS, verify=verify,
        )
    except requests.exceptions.SSLError as e:
        err = str(e)
        if not verify:
            # We already told requests not to verify (either verify_ssl is
            # off, or a matching pin decided trust already) — this
            # shouldn't normally happen, but keep the raw error visible in
            # case something else (e.g. a system-wide config) still enforces it.
            pass
        else:
            err = (
                "TLS certificate verification failed — the remote is likely using a "
                "self-signed or internal certificate. If this is a staging/intranet-only "
                "remote you trust, use the \"Certificate Pinning\" panel below (recommended) "
                "or, as a lighter-weight option, turn off \"Verify SSL\". Original error: "
                + err
            )
        _record(False, err)
        return {"ok": False, "error": err}
    except requests.exceptions.RequestException as e:
        err = str(e)
        _record(False, err)
        return {"ok": False, "error": err}

    if resp.status_code != 200:
        err = f"Remote responded with HTTP {resp.status_code}."
        _record(False, err)
        return {"ok": False, "error": err}

    try:
        data = resp.json()
    except ValueError:
        err = "Remote returned an unreadable response."
        _record(False, err)
        return {"ok": False, "error": err}

    if not data.get("success"):
        err = data.get("error", "Remote rejected the connection check.")
        _record(False, err)
        return {"ok": False, "error": err}

    _record(True)
    return {"ok": True, "remote_mode": data.get("mode"), "remote_enabled": data.get("is_enabled")}


def run_sync(triggered_by=None, existing_run=None):
    """Sends every changed model, in dependency order, to the configured
    remote, throttled between models. Pass `existing_run` (a SyncRun
    already created with status='running') when calling this from a
    background thread via start_sync_in_background, so the caller has an
    id to return/poll immediately instead of waiting for the whole run."""
    from core.models import SyncNode, SyncRun, SyncModelLog, SyncRecordState

    node = SyncNode.get_settings()
    run = existing_run or SyncRun.objects.create(
        triggered_by=triggered_by,
        remote_url=node.remote_url,
        status="running",
    )
    node.last_sync_started_at = timezone.now()
    node.last_sync_status = "running"
    node.save(update_fields=["last_sync_started_at", "last_sync_status"])

    if not node.is_host:
        run.status = "failed"
        run.error_message = "This installation is not in HOST mode."
        run.finished_at = timezone.now()
        run.save()
        node.last_sync_status = "failed"
        node.last_sync_finished_at = timezone.now()
        node.save(update_fields=["last_sync_status", "last_sync_finished_at"])
        return run

    if not node.remote_url or not node.get_token():
        run.status = "failed"
        run.error_message = "Remote URL and/or shared token are not configured."
        run.finished_at = timezone.now()
        run.save()
        node.last_sync_status = "failed"
        node.last_sync_finished_at = timezone.now()
        node.save(update_fields=["last_sync_status", "last_sync_finished_at"])
        return run

    token = node.get_token()

    proceed, verify, pin_error = sync_tls.resolve_verify(node)
    if not proceed:
        run.status = "failed"
        run.error_message = pin_error
        run.finished_at = timezone.now()
        run.save()
        node.last_sync_status = "failed"
        node.last_sync_finished_at = timezone.now()
        node.save(update_fields=["last_sync_status", "last_sync_finished_at"])
        return run

    ordered_models, deps_map, warnings, deferred_map = get_sync_plan()
    run.total_models = len(ordered_models)
    run.save(update_fields=["total_models"])

    # Labels of every model actually in the sync plan — anything a model
    # points at that ISN'T in here (auth.User, chiefly) is an "external"
    # FK that must be nulled out before hashing/sending, or it produces a
    # permanent, non-retriable FK-constraint failure on the remote. See
    # _external_fk_field_names() for the full explanation.
    candidate_labels = {model_label(m) for m in ordered_models}

    had_failure = False
    for index, model in enumerate(ordered_models, start=1):
        label = model_label(model)
        app_label, model_name = label.split(".", 1)
        external_field_names = _external_fk_field_names(model, candidate_labels)
        try:
            reconciled_count, reconcile_checked = _reconcile_stale_hashes(
                model, node.remote_url, token, verify_ssl=verify
            )
            changed_objs, changed_hashes, total = _changed_rows_for_model(model, label, external_field_names)

            if not changed_objs:
                SyncModelLog.objects.create(
                    run=run, order=index, app_label=app_label, model_name=model_name,
                    records_changed=0, records_total=total, status="skipped",
                    error_message=(
                        "" if reconcile_checked else
                        "note: could not verify against the remote's actual pks for this model "
                        "(remote may not be upgraded with /sync/verify-pks/ yet, or the check "
                        "failed) — skip decision is based on hash comparison only."
                    )[:2000],
                )
            else:
                # Split into request-sized chunks so a model with tens of
                # thousands of rows (ProgramCourse, ArchivedCourseAllocation,
                # Timetable, ...) can never produce a single oversized/slow
                # request — see SYNC_MAX_ROWS_PER_REQUEST above. Each chunk
                # is posted and, if accepted, has its hashes recorded
                # immediately: a failure partway through only leaves the
                # *remaining* chunks to retry next run, not the whole model.
                chunks = list(_chunked(changed_objs, SYNC_MAX_ROWS_PER_REQUEST))
                sent_count = 0
                row_reject_count = 0
                row_reject_examples = []
                chunk_failure = None
                # Visible, not silent: if this model has any field pointing
                # outside the sync set (auth.User, chiefly), say so in the
                # log every time it applies — instead of quietly nulling
                # values with no trace, which is exactly the kind of
                # "fixed it, trust me" behaviour that made this bug take
                # two days to pin down. This appears whether the model
                # sends clean or not, so it's visible on every run, not
                # just failing ones.
                nulled_note = (
                    f"; note: field(s) {', '.join(external_field_names)} point outside "
                    f"the sync set (e.g. auth.User, which is never synced) and are sent "
                    f"as null rather than the host's live value, to avoid a permanent "
                    f"FK failure on the remote."
                    if external_field_names else ""
                )
                if reconciled_count:
                    nulled_note += (
                        f"; note: {reconciled_count} row(s) were marked as already-synced on this "
                        f"host but the remote confirmed it does not actually have them — their "
                        f"synced state was cleared and they are included below to be resent."
                    )
                for chunk_num, chunk in enumerate(chunks, start=1):
                    chunk_fixture = serializers.serialize(
                        "json", chunk, use_natural_foreign_keys=False, use_natural_primary_keys=False
                    )
                    if external_field_names:
                        # Same nulling applied for the hash (_row_hash)
                        # must also be applied to what's actually sent —
                        # otherwise the hash says "safe to send" but the
                        # live host FK value (e.g. a real leader_id) still
                        # goes out and still fails permanently on save.
                        chunk_records = json.loads(chunk_fixture)
                        for rec in chunk_records:
                            _strip_external_fks(rec, external_field_names)
                        chunk_fixture = json.dumps(chunk_records)
                    try:
                        result = _post_model_batch(
                            node.remote_url, token, label, app_label, model_name,
                            chunk_fixture, index, run.total_models, verify_ssl=verify,
                        )
                    except Exception as chunk_exc:  # noqa: BLE001
                        chunk_failure = (chunk_num, chunk_exc)
                        break

                    # The HTTP request succeeding only means the remote
                    # received and processed the chunk — NOT that every
                    # row in it was actually saved. ingest_model_payload
                    # (remote side) tolerates individual row failures
                    # (e.g. a CourseAllocation whose Department hasn't
                    # landed on the remote yet because an earlier
                    # model/chunk in THIS SAME run failed) and still
                    # returns success=True. Reading failed_pks here and
                    # excluding exactly those rows from the hash update
                    # is what makes that self-healing: this row is left
                    # looking "unsynced", so the very next sync run will
                    # see its hash as changed/missing and resend it —
                    # automatically, once its dependency actually exists.
                    # Recording success for the *whole chunk* regardless
                    # (the old behaviour) would have permanently hidden
                    # that this row was never actually saved.
                    failed_pks = set(result.get("failed_pks") or [])
                    if failed_pks:
                        row_reject_count += len(failed_pks)
                        row_reject_examples.extend(sorted(failed_pks)[:10])

                    to_update = []
                    for obj in chunk:
                        pk_str = str(obj.pk)
                        if pk_str in failed_pks:
                            continue
                        h = changed_hashes[pk_str]
                        record, _created = SyncRecordState.objects.get_or_create(
                            app_label=app_label, model_name=model_name, object_id=pk_str,
                            defaults={"content_hash": h},
                        )
                        if record.content_hash != h:
                            record.content_hash = h
                            to_update.append(record)
                    if to_update:
                        SyncRecordState.objects.bulk_update(to_update, ["content_hash"])

                    chunk_saved = len(chunk) - len(failed_pks)
                    sent_count += chunk_saved
                    run.records_sent += chunk_saved
                    run.save(update_fields=["records_sent"])

                if chunk_failure is not None:
                    chunk_num, chunk_exc = chunk_failure
                    had_failure = True
                    SyncModelLog.objects.create(
                        run=run, order=index, app_label=app_label, model_name=model_name,
                        records_changed=sent_count, records_total=total, status="failed",
                        error_message=(
                            f"Sent {sent_count}/{len(changed_objs)} changed rows in "
                            f"chunks of {SYNC_MAX_ROWS_PER_REQUEST}, then chunk "
                            f"{chunk_num}/{len(chunks)} failed: {chunk_exc}"
                            + nulled_note
                        )[:2000],
                    )
                elif row_reject_count:
                    had_failure = True
                    # Every chunk was delivered, but some individual rows
                    # were rejected by the remote — most commonly a
                    # dependency (Department, Program, ...) that failed
                    # earlier in this same run. Not a hard failure of
                    # this model: those specific rows will simply be
                    # retried on the next sync once the dependency is in
                    # place, so this is logged as "partial", not "failed".
                    SyncModelLog.objects.create(
                        run=run, order=index, app_label=app_label, model_name=model_name,
                        records_changed=sent_count, records_total=total, status="partial",
                        error_message=(
                            f"{row_reject_count} row(s) rejected by the remote (often because a "
                            f"row they depend on failed earlier in this run) and will be retried "
                            f"automatically next sync. Example pks: {row_reject_examples}"
                            + nulled_note
                        )[:2000],
                    )
                else:
                    SyncModelLog.objects.create(
                        run=run, order=index, app_label=app_label, model_name=model_name,
                        records_changed=sent_count, records_total=total, status="sent",
                        error_message=nulled_note.strip(" ;")[:2000] if nulled_note else "",
                    )

            run.models_completed = index
            run.save(update_fields=["models_completed"])

        except Exception as e:  # noqa: BLE001 - keep going, log, move to next model
            had_failure = True
            SyncModelLog.objects.create(
                run=run, order=index, app_label=app_label, model_name=model_name,
                records_changed=0, records_total=0, status="failed",
                error_message=str(e)[:2000],
            )
            run.models_completed = index
            run.save(update_fields=["models_completed"])

        # Throttle: pause between each model's batch so a large sync
        # doesn't fire requests back-to-back as fast as possible (which
        # can overwhelm the remote or trip rate limiting) and so the
        # background thread yields regularly instead of running hot.
        time.sleep(max(0.0, node.sync_batch_delay_seconds))

    run.status = "partial" if had_failure else "success"
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "finished_at"])

    node.last_sync_status = run.status
    node.last_sync_finished_at = run.finished_at
    node.save(update_fields=["last_sync_status", "last_sync_finished_at"])
    return run


def start_sync_in_background(triggered_by=None):
    """Kicks off run_sync() on a daemon thread and returns immediately
    with the SyncRun row (already created, status='running') so the
    dashboard's "Sync Now" click can return right away and poll for
    progress, rather than the HTTP request blocking for the whole sync —
    this is what prevents a large sync from making the request (and the
    worker handling it) hang or time out."""
    from core.models import SyncNode, SyncRun

    node = SyncNode.get_settings()
    # Pre-create the run synchronously so the caller has an id to poll
    # immediately, then hand the actual work off to the thread.
    placeholder = SyncRun.objects.create(
        triggered_by=triggered_by, remote_url=node.remote_url, status="running",
    )

    def _worker():
        run_sync(triggered_by=triggered_by, existing_run=placeholder)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return placeholder


# ----------------------------------------------------------------------
# REMOTE side
# ----------------------------------------------------------------------
def ingest_model_payload(app_label, model_name, fixture_json):
    """Deserializes and saves one model's pushed rows. Runs inside its
    own transaction (one model = one savepoint) so a bad row in one
    model doesn't roll back models already committed earlier in the
    same push, but a bad row IS rolled back within its own model. Rows
    that fail are counted but don't stop the rest of the batch.

    Returns (saved_count, failed_count, errors[], failed_pks[]).
    failed_pks (stringified, matching the format SyncRecordState uses
    for object_id) is what lets the HOST tell exactly which rows in an
    otherwise-"successful" (HTTP 200) request were actually rejected —
    see the big comment on this in run_sync() for why that distinction
    matters for models with cross-references (e.g. a CourseAllocation
    for a Department that failed to save earlier in the same run).
    """
    try:
        model = django_apps.get_model(app_label, model_name)
    except LookupError as e:
        raise ValueError(f"Unknown model {app_label}.{model_name}: {e}")

    records = json.loads(fixture_json)
    saved, failed, errors, failed_pks = 0, 0, [], []

    for rec in records:
        try:
            with transaction.atomic():
                for deserialized_obj in python_deserializer([rec]):
                    deserialized_obj.save()
            saved += 1
        except Exception as e:  # noqa: BLE001 - keep going, this row just failed
            failed += 1
            failed_pks.append(str(rec.get("pk")))
            errors.append(f"pk={rec.get('pk')}: {e}")

    return saved, failed, errors[:50], failed_pks
