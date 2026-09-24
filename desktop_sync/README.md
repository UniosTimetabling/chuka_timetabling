# desktop_sync — server side of the Chuka Timetabling desktop app

Token-authenticated JSON API used by the Electron client in `desktop_app/`.
It adds **no columns to any existing table**: three small side tables only
(`SyncMeta`, `DesktopAuthToken`, `DesktopSyncOp`).

## Installed already

- `INSTALLED_APPS` → `'desktop_sync'` (`university_timetable_system/settings.py`)
- `path("", include("desktop_sync.urls"))` (`university_timetable_system/urls.py`)
- `core/signals.py` keeps `desktop_sync` out of the ActivityLog (token/meta bookkeeping isn't user activity)
- Deploy: the container entrypoint already runs `migrate`, which creates the three tables. Nothing else to configure.
  Not needed: CORS (Electron calls from its main process, not a browser page), nginx changes, new requirements.

## Who can sign in

Exactly the roles that may edit the timetable in the web panels:
`Role.SUDO`, `Role.DIRECTOR`, `Role.TIMETABLE_ADMIN` (superusers pass). `is_staff` is **not** used.
The role is re-checked on every request, so removing someone from the group cuts off a token they already hold.
Revoke a lost laptop: Django admin → *Desktop auth tokens* → delete the row.

## Endpoints

All except login need `Authorization: Token <key>`. Errors are JSON `{"error": "..."}`.

| Method + path | Purpose |
|---|---|
| `POST /api/desktop/auth/login/` `{username, password, device_label?}` | → `{user:{username, display_name, roles, token,…}}`. 401 bad credentials, 403 wrong role, 429 rate-limited (10/min per username, 30/min per IP). |
| `POST /api/desktop/auth/logout/` | Deletes this device's token. |
| `GET  /api/desktop/auth/me/` | Validates a saved token. |
| `GET  /api/desktop/timetable/<kind>/` | **Full snapshot** of the kind (gzipped). `<kind>` = `regular` \| `exam` \| `lab` \| `lab_exam`. `since_version` is accepted and ignored. |
| `POST /api/desktop/timetable/<kind>/push/` `{changes:[{local_op_id, entry_id, op, payload, base_version}]}` | → `{results:[{local_op_id, entry_id, status, server_entry?, message?}]}` |

Pull returns rows in the allocation sets the web timetable panels show by default (the "eligible" gate in
`course_allocation.allocation_scope`). The client detects server-side deletions by diffing its cache against the snapshot.

### Push semantics

`op` is `create`, `update`, `move` or `delete`. Each result has `status`:

- `applied` — written; `server_entry` is the row as it now exists (absent for deletes).
- `conflict` — the row changed on the server since `base_version` (or no longer exists: `server_entry: null`). **Nothing written.**
- `error` — refused: bad day/time/venue, a clash, or a group lock. `message` explains; `server_entry` is included when the row exists. **Nothing written.**

One bad change never aborts the batch; each change is its own transaction.

### The concurrency token (`version`)

`version` is a 52-bit hash of the row's scheduling content (allocation, venue, day, times, date), computed on read
(`views_sync.content_version`). It changes whenever the slot changes, whichever code path wrote it. That matters here:
the web panel saves with `Timetable.objects.bulk_create`, the autoscheduler publishes in bulk, and moves use
queryset `.update()` — none of which fire Django model signals, so a signal-maintained version would miss them.
A change is applied only if the row's current `version == base_version`.

### Idempotency

Every queued change carries a `local_op_id`. If a push is applied but the response is lost, the client resends; the
server recognises the id (`DesktopSyncOp`) and replays the outcome instead of creating a duplicate or reporting a
conflict with the client's own change.

### Validation on push

| Kind | Checks |
|---|---|
| `regular` | The web panel's own engine (`timetable_panel._check_conflicts`): venue, lecturer and program-year clashes, same rules as saving on the web (no force override). |
| `lab` | `lab_panel_ops.check_lab_slot_conflicts`; one session per lab allocation (a second `create` is refused). |
| `exam`, `lab_exam` | DB constraints and group protection only. The exam panel's clash rules live inside view code and aren't reusable — **clashes are not detected server-side**; review the web Exam conflicts view after syncing. |
| all | day ∈ Monday–Sunday; start < end; venue and allocation must exist; exam `day` is always derived from `date`. Members of Combined / Merged / Shared-venue groups are **read-only** from the desktop (the web panels move those as one unit). |

## Testing

```bash
python manage.py test desktop_sync          # 31 tests (auth, CSRF, scope, versions, push rules, audit attribution)
cd desktop_app && npm run test:e2e          # migrate+seed a throwaway DB, start the server, run the headless client test
```

`desktop_sync/e2e_settings.py` + `manage.py seed_desktop_e2e` create the throwaway SQLite database
(`ttadmin` / `pw-12345`) and refuse to run against anything else.

## What changed from the first draft (`desktop_app/server_integration`, now removed)

- Concurrency token computed from content instead of signal-bumped counters (signals never fire for bulk writes);
  signals and tombstones removed.
- Login gate is the timetable roles, not `is_staff`; role rechecked per request.
- `logout` was blocked by CSRF (token never revoked) — fixed.
- Push crashed after saving (assigned time strings, then called `.strftime`) so every applied change was reported as an error — fixed.
- Login rate limit used the proxy IP for everyone and answered with an HTML 403 — now real client IP (`X-Real-IP`), per-username too, JSON 429.
- Desktop edits are attributed to the signed-in user in the audit trail and ActivityLog.
- Pull respects the same allocation-set scope as the web panels; first pull works on rows that never had a side-table row.
- Regular/lab pushes go through the web conflict engines; group members are protected; retries are idempotent.
