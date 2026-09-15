"""
Host/Remote data sync — views.

  * sync_settings          GET/POST — toggle this machine as host/remote,
                            set the remote URL + shared token (HOST) or
                            just the shared token (REMOTE), and view
                            recent sync history/logs.
  * trigger_sync            POST (AJAX) — HOST only. Starts a sync run in
                            the background and returns its id right away.
  * sync_status              GET (AJAX) — poll a run's progress, including
                            the per-model log lines, for the dashboard's
                            progress bar.
  * receive_sync             POST — REMOTE only. The endpoint the HOST
                            pushes each model's changed rows to. Token
                            authenticated, CSRF-exempt (server-to-server).
  * sync_ping                GET — REMOTE only. Lightweight, token-
                            authenticated "are you there" check the HOST
                            calls to verify connectivity WITHOUT doing a
                            data push. Also what updates `last_contacted_at`
                            on the remote.
  * connection_status_api    GET (AJAX) — sudo only. Returns this node's
                            CACHED connection state (no network call) —
                            what the global status badge polls.
  * test_connection_api       POST (AJAX) — sudo only. HOST: actively pings
                            the remote right now and updates the cached
                            state. REMOTE: just returns its current passive
                            state (a remote can't initiate a check).
"""
import json

from django.apps import apps as django_apps
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

from core.models import SyncNode, SyncRun, SyncModelLog
from export_import import sync_engine
from export_import import sync_crypto
from export_import import sync_tls
from export_import import program_year_pdf_cache

# Models whose data feeds the per (department, program, year) cached PDFs —
# only these need to trigger a cache refresh when a host pushes data in.
_PDF_CACHE_RELEVANT_MODELS = {
    ("timetable", "timetable"),
    ("timetable", "examtimetable"),
    ("course_allocation", "courseallocation"),
    ("program_management", "programcourse"),
}


def sudo_required(view_func):
    """Restrict access to superusers — mirrors admins.views.sudo_required
    locally so this module doesn't have to import from the admins app."""
    return login_required(user_passes_test(lambda u: u.is_superuser)(view_func))


# ----------------------------------------------------------------------
# Settings + history page
# ----------------------------------------------------------------------
@sudo_required
def sync_settings(request):
    node = SyncNode.get_settings()

    if request.method == "POST":
        node.mode = request.POST.get("mode", node.mode)
        node.is_enabled = request.POST.get("is_enabled") == "on"
        node.remote_url = request.POST.get("remote_url", "").strip()
        node.verify_ssl = request.POST.get("verify_ssl") == "on"
        try:
            node.sync_batch_delay_seconds = float(request.POST.get("sync_batch_delay_seconds") or 1.5)
        except ValueError:
            node.sync_batch_delay_seconds = 1.5

        try:
            node.auto_sync_interval_minutes = max(0, int(request.POST.get("auto_sync_interval_minutes") or 0))
        except ValueError:
            node.auto_sync_interval_minutes = 0

        raw_token = request.POST.get("shared_token", "").strip()
        if raw_token:
            node.set_token(raw_token)

        node.save()
        messages.success(request, "Sync settings saved.")
        return redirect("sync_settings")

    recent_runs = SyncRun.objects.all()[:15]
    context = {
        "node": node,
        "has_token": bool(node.shared_token_encrypted),
        "recent_runs": recent_runs,
        "connection": node.connection_summary(),
        "pinned_fingerprint_display": sync_tls.format_fingerprint(node.pinned_cert_fingerprint) if node.pinned_cert_fingerprint else None,
        "sync_plan_preview": [
            sync_engine.model_label(m) for m in sync_engine.get_sync_plan()[0]
        ] if node.is_host else [],
    }
    return render(request, "export/sync_settings.html", context)


# ----------------------------------------------------------------------
# Certificate pinning (TOFU) — HOST only
# ----------------------------------------------------------------------
@sudo_required
@require_POST
def fetch_remote_cert(request):
    """Reads the remote's *current* certificate right now (does not
    trust or pin anything) so the admin can eyeball/compare its
    fingerprint before deciding to pin it."""
    node = SyncNode.get_settings()
    if not node.is_host or not node.remote_url:
        return JsonResponse({"success": False, "error": "Set Mode to Host and fill in a Remote URL first."}, status=400)

    try:
        fingerprint = sync_tls.get_remote_cert_fingerprint(node.remote_url)
    except (sync_tls.CertFetchError, ValueError) as e:
        return JsonResponse({"success": False, "error": str(e)}, status=502)

    return JsonResponse({
        "success": True,
        "fingerprint": fingerprint,
        "fingerprint_display": sync_tls.format_fingerprint(fingerprint),
        "currently_pinned": node.pinned_cert_fingerprint or None,
        "currently_pinned_display": sync_tls.format_fingerprint(node.pinned_cert_fingerprint) if node.pinned_cert_fingerprint else None,
        "matches_pinned": (fingerprint == node.pinned_cert_fingerprint) if node.pinned_cert_fingerprint else None,
    })


@sudo_required
@require_POST
def pin_remote_cert(request):
    """Pins (or clears) the trusted certificate fingerprint for this
    HOST's configured remote. Pinning always re-fetches the certificate
    live rather than trusting whatever fingerprint the browser posted
    back, so a stale or tampered value in the request can't get pinned —
    this endpoint pins exactly whatever the remote is presenting at the
    moment of the call."""
    node = SyncNode.get_settings()

    if request.POST.get("action") == "clear":
        node.pinned_cert_fingerprint = ""
        node.pinned_cert_saved_at = None
        node.save(update_fields=["pinned_cert_fingerprint", "pinned_cert_saved_at"])
        return JsonResponse({"success": True, "cleared": True})

    if not node.is_host or not node.remote_url:
        return JsonResponse({"success": False, "error": "Set Mode to Host and fill in a Remote URL first."}, status=400)

    expected_fingerprint = request.POST.get("fingerprint", "").strip().lower()
    if not expected_fingerprint:
        return JsonResponse({"success": False, "error": "No fingerprint supplied — fetch the certificate first."}, status=400)

    try:
        live_fingerprint = sync_tls.get_remote_cert_fingerprint(node.remote_url)
    except (sync_tls.CertFetchError, ValueError) as e:
        return JsonResponse({"success": False, "error": str(e)}, status=502)

    if live_fingerprint != expected_fingerprint:
        return JsonResponse(
            {"success": False, "error": "The remote's certificate changed between fetching and pinning — fetch it again and retry."},
            status=409,
        )

    node.pinned_cert_fingerprint = live_fingerprint
    node.pinned_cert_saved_at = timezone.now()
    node.save(update_fields=["pinned_cert_fingerprint", "pinned_cert_saved_at"])
    return JsonResponse({
        "success": True,
        "fingerprint": live_fingerprint,
        "fingerprint_display": sync_tls.format_fingerprint(live_fingerprint),
    })


@sudo_required
def sync_run_detail(request, run_id):
    run = SyncRun.objects.prefetch_related("model_logs").filter(pk=run_id).first()
    if not run:
        messages.error(request, "Sync run not found.")
        return redirect("sync_settings")
    return render(request, "export/sync_run_detail.html", {"run": run})


# ----------------------------------------------------------------------
# HOST: trigger + poll
# ----------------------------------------------------------------------
@sudo_required
@require_POST
def trigger_sync(request):
    node = SyncNode.get_settings()
    if not node.is_enabled:
        return JsonResponse({"success": False, "error": "Sync is disabled. Enable it in Sync Settings first."}, status=400)
    if not node.is_host:
        return JsonResponse({"success": False, "error": "This installation is set to REMOTE mode, so it can't push a sync."}, status=400)
    if not node.remote_url or not node.get_token():
        return JsonResponse({"success": False, "error": "Set a remote URL and shared token in Sync Settings first."}, status=400)

    # Avoid starting a second run on top of one that's still going — but
    # first clear out anything that's only *labeled* "running" because a
    # previous attempt's thread died without ever finishing (dev server
    # reload, worker restart, etc.), or every click would just keep
    # handing back that same dead run forever.
    sync_engine.reap_stale_runs()

    if SyncRun.objects.filter(status="running").exists():
        running = SyncRun.objects.filter(status="running").first()
        return JsonResponse({"success": True, "run_id": running.pk, "already_running": True})

    run = sync_engine.start_sync_in_background(triggered_by=request.user)
    return JsonResponse({"success": True, "run_id": run.pk})


@sudo_required
def sync_status(request):
    run_id = request.GET.get("run_id")
    run = SyncRun.objects.filter(pk=run_id).first() if run_id else SyncRun.objects.first()
    if not run:
        return JsonResponse({"success": False, "error": "No sync run found."}, status=404)

    logs = list(
        run.model_logs.order_by("order").values(
            "order", "app_label", "model_name", "status", "records_changed", "records_total", "error_message"
        )
    )
    return JsonResponse({
        "success": True,
        "run_id": run.pk,
        "status": run.status,
        "total_models": run.total_models,
        "models_completed": run.models_completed,
        "records_sent": run.records_sent,
        "error_message": run.error_message,
        "logs": logs,
    })


# ----------------------------------------------------------------------
# Connection status — cached read + on-demand active check
# ----------------------------------------------------------------------
@sudo_required
@require_GET
def connection_status_api(request):
    """
    Cheap, no-network-call read of this node's cached connection state —
    what the global status badge (every page) and the Sync Settings page
    poll regularly. Does NOT itself test connectivity; see
    test_connection_api for that.
    """
    node = SyncNode.get_settings()
    summary = node.connection_summary()
    return JsonResponse({
        "success": True,
        "mode": node.mode,
        "is_enabled": node.is_enabled,
        "state": summary["state"],
        "label": summary["label"],
        "detail": summary["detail"],
        "stale": summary["stale"],
        "last_connection_check_at": node.last_connection_check_at.isoformat() if node.last_connection_check_at else None,
        "last_contacted_at": node.last_contacted_at.isoformat() if node.last_contacted_at else None,
    })


@sudo_required
@require_POST
def test_push_api(request):
    """
    HOST only. Sends ONE real, harmless, fake record (a core.ActivityLog
    row with made-up field values) through the exact same path a real
    sync push uses — encrypt_payload -> POST /sync/receive/ -> the
    remote decrypts and saves it — but only that one record, so this
    answers "can host and remote actually exchange data" in under a
    second instead of waiting through a full run_sync() across every
    model. Distinct from test_connection_api (sync_ping), which never
    touches the database on either side — this one actually writes a
    row on the remote, so you can go check it landed.
    """
    import json as _json
    import requests as _requests
    from export_import import sync_crypto

    node = SyncNode.get_settings()

    if not node.is_enabled:
        return JsonResponse({"success": False, "error": "Sync is disabled. Enable it in Sync Settings first."}, status=400)
    if not node.is_host:
        return JsonResponse({"success": False, "error": "This installation is in REMOTE mode — only a HOST can push a test record."}, status=400)
    if not node.remote_url or not node.get_token():
        return JsonResponse({"success": False, "error": "Set a remote URL and shared token in Sync Settings first."}, status=400)

    token = node.get_token()
    test_marker = f"test-push-{timezone.now().strftime('%Y%m%d%H%M%S')}"

    fixture = _json.dumps([{
        "model": "core.activitylog",
        "pk": None,
        "fields": {
            "user": None,
            "action": "add",
            "app_label": "synctest",
            "model_name": "pingrecord",
            "object_id": test_marker,
            "data": {"source": "sync test-push button", "sent_at": timezone.now().isoformat()},
        },
    }])

    proceed, verify, pin_error = sync_tls.resolve_verify(node)
    if not proceed:
        return JsonResponse({"success": False, "stage": "tls", "error": pin_error}, status=400)

    try:
        encrypted = sync_crypto.encrypt_payload(fixture, token)
    except Exception as e:  # noqa: BLE001
        return JsonResponse({"success": False, "stage": "encrypt", "error": str(e)}, status=500)

    try:
        resp = _requests.post(
            node.remote_url.rstrip("/") + sync_engine.SYNC_RECEIVE_PATH,
            headers={"X-Sync-Token": token, "Content-Type": "application/json"},
            data=_json.dumps({
                "app_label": "core",
                "model_name": "activitylog",
                "order": 1,
                "total_models": 1,
                "fixture_encrypted": encrypted,
            }),
            timeout=15,
            verify=verify,
        )
    except _requests.exceptions.RequestException as e:
        return JsonResponse({
            "success": False, "stage": "network",
            "error": f"Could not reach the remote at all: {e}",
        }, status=502)

    try:
        remote_json = resp.json()
    except ValueError:
        remote_json = {"raw": resp.text[:500]}

    return JsonResponse({
        "success": resp.ok and remote_json.get("success", False),
        "stage": "receive",
        "http_status": resp.status_code,
        "remote_response": remote_json,
        "test_marker": test_marker,
    })


@sudo_required
@require_POST
def test_connection_api(request):
    """
    HOST: actively pings the configured remote right now (a real network
    call) and records the outcome. REMOTE: can't initiate a check of its
    own — just returns its current passive status (based on when a host
    last successfully contacted it).
    """
    node = SyncNode.get_settings()

    if not node.is_enabled:
        return JsonResponse({"success": False, "error": "Sync is disabled. Enable it in Sync Settings first."}, status=400)

    if node.is_remote:
        summary = node.connection_summary()
        return JsonResponse({
            "success": True, "mode": "remote",
            "state": summary["state"], "label": summary["label"], "detail": summary["detail"],
        })

    result = sync_engine.test_connection()
    node.refresh_from_db()
    summary = node.connection_summary()
    return JsonResponse({
        "success": True,
        "mode": "host",
        "state": summary["state"],
        "label": summary["label"],
        "detail": summary["detail"],
        "raw_ok": result.get("ok"),
        "raw_error": result.get("error", ""),
    })


# ----------------------------------------------------------------------
# REMOTE: receive endpoint
# ----------------------------------------------------------------------
@csrf_exempt
@require_GET
def sync_ping(request):
    """
    Lightweight "are you there" check — the HOST calls this to verify
    connectivity to a REMOTE without pushing any data. Token
    authenticated, like receive_sync, but does no database writes beyond
    bumping `last_contacted_at` so the remote's own status badge reflects
    that a host reached it.
    """
    node = SyncNode.get_settings()

    if not node.is_enabled:
        return JsonResponse({"success": False, "error": "Sync is disabled on this installation."}, status=403)

    token = request.headers.get("X-Sync-Token", "")
    expected = node.get_token()
    if not expected or token != expected:
        return JsonResponse({"success": False, "error": "Invalid or missing sync token."}, status=401)

    if node.is_remote:
        node.last_contacted_at = timezone.now()
        node.save(update_fields=["last_contacted_at"])

    return JsonResponse({"success": True, "mode": node.mode, "is_enabled": node.is_enabled})


@csrf_exempt
@require_POST
def receive_sync(request):
    node = SyncNode.get_settings()

    if not node.is_enabled or not node.is_remote:
        return JsonResponse({"success": False, "error": "This installation is not accepting sync pushes."}, status=403)

    token = request.headers.get("X-Sync-Token", "")
    expected = node.get_token()
    if not expected or token != expected:
        return JsonResponse({"success": False, "error": "Invalid or missing sync token."}, status=401)

    # A valid, authenticated request just arrived from a host — regardless
    # of what happens below, that proves the two sides can currently reach
    # each other, so record it right away for the connection status badge.
    node.last_contacted_at = timezone.now()
    node.save(update_fields=["last_contacted_at"])

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"success": False, "error": "Malformed JSON body."}, status=400)

    app_label = payload.get("app_label")
    model_name = payload.get("model_name")
    order = payload.get("order") or 0
    total_models = payload.get("total_models") or 0
    fixture_encrypted = payload.get("fixture_encrypted")
    if not app_label or not model_name or fixture_encrypted is None:
        return JsonResponse({"success": False, "error": "app_label, model_name and fixture_encrypted are required."}, status=400)

    try:
        fixture_json = sync_crypto.decrypt_payload(fixture_encrypted, expected)
    except sync_crypto.SyncPayloadDecryptError:
        return JsonResponse(
            {"success": False, "error": "Could not decrypt the payload — the shared token may not match, "
                                         "or the payload was corrupted or tampered with in transit."},
            status=400,
        )

    # Group this model's log line under one SyncRun record per incoming
    # batch: `order == 1` marks the first model of a new push, so close
    # out whatever run was still "running" from a previous batch (the
    # host never sends an explicit "batch finished" signal, so this is
    # the best signal we get) and start a fresh local run here;
    # otherwise attach to whichever run is still in progress.
    run = SyncRun.objects.filter(status="running").order_by("-started_at").first()
    if run is None or int(order) == 1:
        if run is not None:
            run.status = "success"
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at"])
        run = SyncRun.objects.create(status="running", remote_url="", total_models=total_models)

    try:
        saved, failed, errors, failed_pks = sync_engine.ingest_model_payload(app_label, model_name, fixture_json)
        status = "sent" if failed == 0 else "partial"
        SyncModelLog.objects.create(
            run=run, order=order, app_label=app_label, model_name=model_name,
            records_changed=saved, records_total=saved + failed, status=status,
            error_message="; ".join(errors)[:2000],
        )
        run.models_completed += 1
        run.records_sent += saved
        run.save(update_fields=["models_completed", "records_sent"])

        if saved > 0 and (app_label.lower(), model_name.lower()) in _PDF_CACHE_RELEVANT_MODELS:
            # New/changed data affecting timetable PDFs just landed from
            # the host. We don't know precisely which (department,
            # program, year) scopes it touched from a raw fixture
            # payload, so wipe the cache and let the background sweep
            # rebuild only what's actually missing — the next student
            # to ask gets a fresh PDF instead of a stale one, and every
            # OTHER cached scope keeps serving instantly while the sweep
            # catches up in the background.
            try:
                program_year_pdf_cache.invalidate_all()
                program_year_pdf_cache.regenerate_all_in_background()
            except Exception:  # noqa: BLE001 — never fail the sync push over this
                pass

        # failed_pks tells the HOST exactly which rows in this chunk were
        # rejected (e.g. a CourseAllocation referencing a Department that
        # hasn't landed yet) even though the request itself succeeded, so
        # the host can avoid marking those specific rows as "synced" and
        # naturally resend them once their dependency exists — see
        # run_sync() in sync_engine.py.
        return JsonResponse({"success": True, "saved": saved, "failed": failed, "errors": errors, "failed_pks": failed_pks})
    except Exception as e:  # noqa: BLE001
        SyncModelLog.objects.create(
            run=run, order=order, app_label=app_label, model_name=model_name,
            records_changed=0, records_total=0, status="failed", error_message=str(e)[:2000],
        )
        run.status = "partial"
        run.save(update_fields=["status"])
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@csrf_exempt
@require_POST
def sync_verify_pks(request):
    """
    REMOTE: reports back the actual, current set of pks it holds for one
    model, given {app_label, model_name}. This is what lets the HOST
    catch a row it *believes* it already synced (its SyncRecordState
    hash matches) but that doesn't actually exist here — e.g. it was
    recorded as synced during the old pre-failed_pks-fix behaviour,
    where a whole chunk was marked synced on a bare HTTP 200 even if
    some rows inside it were rejected. Read-only, no DB writes beyond
    the same last_contacted_at bookkeeping every other sync endpoint
    does. See sync_engine._reconcile_stale_hashes() for how the host
    uses this.
    """
    node = SyncNode.get_settings()

    if not node.is_enabled or not node.is_remote:
        return JsonResponse({"success": False, "error": "This installation is not accepting sync pushes."}, status=403)

    token = request.headers.get("X-Sync-Token", "")
    expected = node.get_token()
    if not expected or token != expected:
        return JsonResponse({"success": False, "error": "Invalid or missing sync token."}, status=401)

    node.last_contacted_at = timezone.now()
    node.save(update_fields=["last_contacted_at"])

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"success": False, "error": "Malformed JSON body."}, status=400)

    app_label = payload.get("app_label")
    model_name = payload.get("model_name")
    if not app_label or not model_name:
        return JsonResponse({"success": False, "error": "app_label and model_name are required."}, status=400)

    try:
        model = django_apps.get_model(app_label, model_name)
    except LookupError as e:
        return JsonResponse({"success": False, "error": f"Unknown model {app_label}.{model_name}: {e}"}, status=400)

    pks = list(model._default_manager.values_list("pk", flat=True))
    return JsonResponse({"success": True, "pks": [str(pk) for pk in pks]})
