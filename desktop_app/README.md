# Chuka Timetabling — Desktop (offline client)

Cross-platform (Windows + Linux) desktop client for the Main Timetable and
Exam Timetable panels, built to work fully offline and sync back to the
Django server when connected.

## Stack & why

- **Electron** — one codebase, packages natively for both Windows (`.exe`
  via NSIS) and Linux (`AppImage` + `.deb`). Alternatives considered:
  Tauri (smaller binaries, but Rust build toolchain adds friction for a
  Python/Django shop) and a pure Python desktop (Qt/PySide) — went with
  Electron because it lets the UI be a near-1:1 port of the existing
  web timetable grid (same HTML/CSS/JS mental model as the Django
  templates) while still shipping a single installer per OS.
- **React + TypeScript** for the UI.
- **better-sqlite3** for the local offline store — synchronous, fast,
  single-file, zero server process, lives entirely in the OS user-data
  directory.
- **electron-updater** for app-shell (UI/code) auto-updates, kept
  deliberately separate from data sync (see below).

## Project layout

```
src/main/        Electron main process: SQLite (db.ts), sync engine
                 (syncEngine.ts), IPC handlers, local PDF export
                 (pdfExport.ts) — this is the "backend" that runs inside
                 the desktop app itself.
src/renderer/    React UI: Dashboard shell + Main Timetable panel (with
                 Lecture/Lab sub-tabs), Exam Timetable panel (with
                 Exam/Lab-exam sub-tabs), Analysis panel, Settings,
                 shared TimetableGrid (drag-and-drop + Ctrl+X/C/V),
                 conflict resolution modal, sync status bar.
src/shared/      TypeScript types shared by main + renderer + (conceptually)
                 the Django side — the sync wire format.
scripts/         run-electron-node.js (runs a script on Electron's Node),
                 run-e2e.sh (full sync test on a throwaway database).
../desktop_sync/ The Django side (already installed in the root project) —
                 see ../desktop_sync/README.md for the API and rules.
```

## Login & dashboard

The app now opens to a login screen (`LoginScreen.tsx`) asking for the
server address, username, and password. This authenticates against the
`desktop_sync` token-auth endpoints (`../desktop_sync/views_auth.py`), which only issue a token to
Timetable Office roles — Sudo, Director, Timetable Admin, the same roles that may edit the timetable on the web
(not students/lecturers — those already have `mobile_api`). The address can be typed as a bare host
(`timetable.chuka.ac.ke`) or a full URL; local/LAN addresses default to `http://`. The token is saved locally (`db.ts`'s `session` row) so the
app reopens signed in and works offline; on each startup it does a
best-effort check against `/api/desktop/auth/me/` and silently falls back
to "still signed in, offline" if the server can't be reached.

Once signed in, `DashboardShell.tsx` renders a sidebar (`Dashboard`,
`Main Timetable`, `Exam Timetable`, `Settings`) — a genuine
dashboard-with-panel-switching, not just two tabs. `DashboardHome.tsx` is
the landing view: it shows who's signed in, which server, and shortcut
cards into each panel plus a live conflict count.

### Staying logged in offline, and resetting

Logging in once is enough. `login()` saves `{ baseUrl, token, username,
... }` into the local SQLite `meta` table (`db.ts`), and every app launch
reads it straight back (`authStore.restoreSession`) — the dashboard opens
immediately, no login prompt, whether the server is reachable or not.
`Settings` (in the sidebar) is where this is made visible and
controllable: it shows the saved server/account, and has a **Reset
login** button (with a confirm step) that does both halves of a reset —
tells the server to revoke that device's token (`/api/desktop/auth/logout/`,
so the saved credential can't be reused even if someone copied the local
db file) *and* clears it from local storage, so the next launch asks for
a password again. The sidebar's quick "Sign out" does the same thing
without the extra confirm step. Either way, your offline timetable data
and anything still queued to sync are untouched — only the login is
reset.

## Labs and other resource types

Everything above applies equally to lab sessions — they weren't bolted on
as an afterthought. Under the hood, `LabTimetable`/`LabExamTimetable` are
genuinely different Django models from `Timetable`/`ExamTimetable`: they
point at `room_management.LabVenue` (not `Venue`) and
`course_allocation.LabAllocation` (via `ProgramCourse`, not
`CourseAllocation`) for course code/lecturer/program. The desktop app
mirrors that split rather than papering over it:

- **Main Timetable** and **Exam Timetable** each have a sub-tab toggle —
  *Lecture Venues / Lab Sessions* and *Exam Venues / Lab Exams* — backed
  by the `lab` / `lab_exam` `EntryKind`s.
- Every entry carries `venue_kind` (`hall` or `lab`), shown as a small
  "LAB" badge in the grid, so it's visually obvious which resource family
  a session is in even without switching tabs.
- Server-side, `desktop_sync/views_sync.py`'s `KIND_CONFIG` dict is the
  single place that reconciles the two different allocation/venue table
  shapes into one client-facing JSON shape — extend that dict, not the
  view functions, if another resource family (e.g. specialized/workshop
  venues) needs the same treatment later.
- The local SQLite schema was migrated (v1 → v2, in `db.ts`) to allow
  these two new kinds — a real additive migration, not a fresh-install
  assumption: every existing cached row survives the upgrade.

## Analysis, done locally — including PDF export

The **Analysis** panel (sidebar, or the dashboard shortcut card) reports
on whatever is already cached in local SQLite — entry counts by type,
venue utilization, and a per-lecturer schedule lookup — computed entirely
client-side, so it works with the app fully offline.

**PDF export doesn't call the server.** `pdfExport.ts` in the main process
renders an HTML report string to PDF using Chromium's own
`webContents.printToPDF()` on an offscreen `BrowserWindow`, then writes it
wherever the user picks in a native save dialog. There's no report-
generation endpoint involved and no network request — it's the same
mechanism a "Print to PDF" browser feature uses, just driven
programmatically. `AnalysisPanel.tsx` builds two kinds of report this way:
a summary (counts + venue utilization) and a single lecturer's full
schedule across all four `EntryKind`s.

## How offline editing works

Every edit (drag-and-drop move, cut/copy/paste, delete) goes through
`useTimetableStore` → `window.chukaApi.applyLocalEdit` → an IPC call into
the main process → `db.ts` writes it to SQLite immediately (so the UI is
instant, no network round-trip) and queues it in `pending_changes`. The
row is marked `sync_state: 'dirty'` in the UI (amber border) until it's
pushed successfully.

### Drag-and-drop, and Word-style cut/copy/paste

- **Drag-and-drop**: native HTML5 drag/drop — pick up a class, drop it on
  another day/slot. Implemented in `TimetableGrid.tsx`.
- **Ctrl+C / Ctrl+X / Ctrl+V**: click a class to select it (Ctrl+click to
  multi-select), `Ctrl+C` copies, `Ctrl+X` cuts, then click a target cell
  and `Ctrl+V` pastes — cut = move, copy = duplicate into a new slot,
  exactly like editing a table in Word. `Delete`/`Backspace` removes the
  selection. All of this is in `store.ts` (`copySelection` /
  `cutSelection` / `pasteInto` / `deleteSelection`) and wired to keyboard
  events in `TimetableGrid.tsx`.

## Sync & conflict resolution

Sync runs automatically: right after sign-in, shortly after each launch, every two minutes, and on **Sync now**.
Each run pushes the queue, then refreshes all four kinds (`syncEngine.ts`, `ipcHandlers.runSync`; one run at a time).

1. **Push.** The queue keeps at most one unsent change per row: several offline edits to the same class collapse
   into one, an edit to a pasted-but-unsynced copy folds into its `create`, and deleting such a copy cancels it
   (`db.queueChange`). Each change carries the `base_version` it was made against and a `local_op_id` so a retry
   after a lost response is applied once. The server answers `applied`, `conflict` (row changed since; nothing
   written) or `error` (refused — a clash, a combined/merged group, a bad venue...).
2. **Pull.** The server returns the full current snapshot. Rows with no local edit are replaced; rows the server
   no longer has are removed locally; a row with an unsynced local edit whose server version moved on becomes a conflict.
3. **Nothing is dropped or auto-resolved.** Version conflicts *and* server rejections both land in the Conflicts
   dialog (`ConflictResolutionModal.tsx`) with the reason. Choose **keep mine** (retry on top of the server's
   version), **keep server's** (discard my edit), or pick fields. If the row was deleted on the server you can
   forget it or re-create it. A row awaiting a decision can't be edited or moved.
4. **Offline/errors.** Unreachable server → everything stays queued, status bar shows "Offline". A revoked token
   or lost role → back to the login screen. A proxy 502/503 does **not** sign you out.

Moves keep the class's own venue; the grid columns for exams are calendar dates (the day is derived from the date);
lab sessions can be moved but not duplicated. Classes in combined/merged groups are read-only here — edit them on the web.

## Shipping UI updates without touching stored data

This was an explicit requirement, so it's structural, not just a note:

- The SQLite file lives in `app.getPath('userData')` — **outside** the
  installed application directory. A new installer (new renderer bundle,
  new main-process code) only replaces files under the app's install
  path; it never touches `userData`.
- `db.ts` has a `CURRENT_SCHEMA_VERSION` and a `meta` table recording the
  schema version actually on disk. Migrations only run when the on-disk
  version is behind the code's version, and they're written to be
  additive (new tables/columns, never destructive renames/drops on
  populated columns) — see the comment block at the top of `db.ts`.
- `electron-updater`'s `autoDownload` is off and update checks run
  independently of the sync engine (`main.ts`) — a UI update and a data
  sync are two unrelated network calls that can't interfere with each
  other, and neither blocks the other from working offline.

## Local testing against your own backend: `./run.sh`

```bash
# 1. start the Django backend yourself, listening on 127.0.0.1:8001
python manage.py runserver 127.0.0.1:8001
# 2. in desktop_app/
./run.sh              # installs what's missing, builds, opens the app
./run.sh --fresh      # ...after wiping the local test database
./run.sh --reinstall  # ...after deleting node_modules and reinstalling
```

The login screen opens pre-filled with `http://127.0.0.1:8001` (override with `CHUKA_BACKEND_URL=...`). The script
uses a **separate** SQLite file (`.local-test/chuka_local_test.sqlite3`, override with `CHUKA_DB_PATH`) so local
testing never mixes with — or wipes — your real offline cache, which the app does when you sign in to a different
server. Dependencies are only reinstalled when `package.json`/`package-lock.json` change. If the backend isn't
reachable the script warns and still opens the app. On Linux it adds Electron's `--no-sandbox` automatically when the
system blocks the sandbox (Ubuntu 24.04, running as root) — acceptable here because the app only loads its own bundled UI.

## Build & run

```bash
npm install            # also rebuilds better-sqlite3 for Electron's Node ABI (postinstall) — required
npm start              # build + launch
npm run typecheck
npm run dist:win       # -> release/*.exe (NSIS installer)
npm run dist:linux     # -> release/*.AppImage and *.deb
```

Windows builds can be produced from Linux/macOS via `electron-builder`'s cross-build support (wine); for a fully
native Windows build run `npm run dist:win` on Windows.

Before shipping to users: add an app icon (`build/icon.ico`, `build/icon.png`) and, when an update server exists,
a `build.publish` entry in `package.json` (updates are disabled until then). Unsigned installers trigger
SmartScreen/Gatekeeper warnings.

## Testing

```bash
npm run test:e2e       # throwaway DB + Django dev server + headless sync test (18 scenarios), needs the project's Python env active
```

Or by hand against your own dev server: sign in with the app (`npm start`), or run `npm run test:sync` against a
server seeded with `desktop_sync.e2e_settings` (see `../desktop_sync/README.md`). Manual smoke list:
sign in → grid fills without pressing anything → drag a class (amber "unsynced" badge, "1 unsynced change") →
Sync now → check the change on the web → edit the same class on the web while the app is offline, edit it in the
app, reconnect → conflict dialog → resolve.

## Known limits / next steps

- Exam entries are not clash-checked on the server (regular and lab entries are); review the web Exam conflicts view after syncing.
- No venue / course-allocation pickers: new rows come from copy-paste only; changing a class's venue isn't possible from the desktop yet.
- The login token is stored in the local SQLite file (readable by the OS user only); switch to Electron `safeStorage` for encryption at rest.
- Slot rows/columns come from the data; there is no add-a-new-slot control.
- Icons and code signing.
