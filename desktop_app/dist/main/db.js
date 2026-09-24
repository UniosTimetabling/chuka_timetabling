"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.getDbPath = getDbPath;
exports.closeDb = closeDb;
exports.openDb = openDb;
exports.listEntries = listEntries;
exports.listEntriesRaw = listEntriesRaw;
exports.listAllEntries = listAllEntries;
exports.getEntry = getEntry;
exports.upsertEntry = upsertEntry;
exports.markDirty = markDirty;
exports.markDeleted = markDeleted;
exports.setSyncState = setSyncState;
exports.runInTransaction = runInTransaction;
exports.purgeEntry = purgeEntry;
exports.setInflight = setInflight;
exports.clearInflight = clearInflight;
exports.queueChange = queueChange;
exports.listPendingChanges = listPendingChanges;
exports.pendingCount = pendingCount;
exports.hasPending = hasPending;
exports.clearPendingChange = clearPendingChange;
exports.dropPendingForEntry = dropPendingForEntry;
exports.rewirePending = rewirePending;
exports.rebasePending = rebasePending;
exports.saveConflict = saveConflict;
exports.listConflicts = listConflicts;
exports.getConflict = getConflict;
exports.resolveConflict = resolveConflict;
exports.getMeta = getMeta;
exports.setMeta = setMeta;
exports.wipeData = wipeData;
exports.saveSession = saveSession;
exports.getSession = getSession;
exports.clearSession = clearSession;
exports.upsertCachedDocument = upsertCachedDocument;
exports.getCachedDocument = getCachedDocument;
exports.listCachedDocuments = listCachedDocuments;
exports.removeCachedDocument = removeCachedDocument;
exports.replaceLabExamReference = replaceLabExamReference;
exports.getLabExamReference = getLabExamReference;
const better_sqlite3_1 = __importDefault(require("better-sqlite3"));
const electron_1 = require("electron");
const path_1 = __importDefault(require("path"));
const fs_1 = __importDefault(require("fs"));
/**
 * CRITICAL: the local database lives in the OS user-data directory,
 * completely outside the app's install/resources folder. Shipping a new
 * app-shell build (new UI, new renderer bundle) NEVER touches this file.
 * Only `CURRENT_SCHEMA_VERSION` bumps trigger a migration, and migrations
 * are additive (never drop/rename columns with data in them).
 */
const CURRENT_SCHEMA_VERSION = 5;
let db;
function getDbPath() {
    // CHUKA_DB_PATH lets QA / automated tests point at a throwaway file without launching Electron.
    if (process.env.CHUKA_DB_PATH)
        return process.env.CHUKA_DB_PATH;
    const dir = path_1.default.join(electron_1.app.getPath('userData'), 'data');
    if (!fs_1.default.existsSync(dir))
        fs_1.default.mkdirSync(dir, { recursive: true });
    return path_1.default.join(dir, 'chuka_offline.sqlite3');
}
/** Test helper: close and forget the handle so a fresh file can be opened. */
function closeDb() {
    if (db)
        db.close();
    db = undefined;
}
function openDb() {
    if (db)
        return db;
    db = new better_sqlite3_1.default(getDbPath());
    db.pragma('journal_mode = WAL');
    db.pragma('foreign_keys = ON');
    migrate(db);
    return db;
}
function migrate(d) {
    d.exec(`
    CREATE TABLE IF NOT EXISTS meta (
      key TEXT PRIMARY KEY,
      value TEXT
    );
  `);
    const row = d.prepare('SELECT value FROM meta WHERE key = ?').get('schema_version');
    const currentVersion = row ? parseInt(row.value, 10) : 0;
    if (currentVersion < 1)
        d.transaction(() => {
            d.exec(`
      CREATE TABLE IF NOT EXISTS timetable_entries (
        id                    TEXT PRIMARY KEY,
        server_id             INTEGER,
        kind                  TEXT NOT NULL CHECK (kind IN ('regular','exam')),
        course_allocation_id  INTEGER NOT NULL,
        course_code           TEXT NOT NULL,
        course_name           TEXT NOT NULL DEFAULT '',
        lecturer_name         TEXT NOT NULL DEFAULT '',
        program_name          TEXT NOT NULL DEFAULT '',
        venue_id              INTEGER NOT NULL,
        venue_name            TEXT NOT NULL DEFAULT '',
        day                   TEXT NOT NULL,
        date                  TEXT,
        start_time            TEXT NOT NULL,
        end_time              TEXT NOT NULL,
        version               INTEGER NOT NULL DEFAULT 0,
        base_version          INTEGER NOT NULL DEFAULT 0,
        updated_at            TEXT,
        updated_by            TEXT,
        sync_state            TEXT NOT NULL DEFAULT 'clean',
        deleted               INTEGER NOT NULL DEFAULT 0
      );
      CREATE INDEX IF NOT EXISTS idx_entries_kind ON timetable_entries(kind);
      CREATE INDEX IF NOT EXISTS idx_entries_server_id ON timetable_entries(server_id);

      CREATE TABLE IF NOT EXISTS pending_changes (
        local_op_id  TEXT PRIMARY KEY,
        entry_id     TEXT NOT NULL,
        kind         TEXT NOT NULL,
        op           TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        base_version INTEGER NOT NULL,
        created_at   TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS conflicts (
        entry_id      TEXT PRIMARY KEY,
        kind          TEXT NOT NULL,
        local_json    TEXT NOT NULL,
        server_json   TEXT NOT NULL,
        base_version  INTEGER NOT NULL,
        created_at    TEXT NOT NULL
      );
    `);
            d.prepare('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)').run('schema_version', '1');
        })();
    if (currentVersion < 2)
        d.transaction(() => {
            // Adds lab timetables ("lab" / "lab_exam" kinds, room_management.LabVenue-backed)
            // alongside the original regular/exam ones. SQLite can't ALTER a CHECK
            // constraint in place, so this rebuilds the table under a transaction —
            // still additive from the user's point of view: every existing row and
            // column is preserved, `venue_kind` is backfilled to 'hall' (correct
            // for every row that could exist before this version, since 'lab' /
            // 'lab_exam' didn't exist yet), and this only runs once per machine.
            d.exec(`
      ALTER TABLE timetable_entries RENAME TO timetable_entries_v1;

      CREATE TABLE timetable_entries (
        id                    TEXT PRIMARY KEY,
        server_id             INTEGER,
        kind                  TEXT NOT NULL CHECK (kind IN ('regular','exam','lab','lab_exam')),
        course_allocation_id  INTEGER NOT NULL,
        course_code           TEXT NOT NULL,
        course_name           TEXT NOT NULL DEFAULT '',
        lecturer_name         TEXT NOT NULL DEFAULT '',
        program_name          TEXT NOT NULL DEFAULT '',
        venue_id              INTEGER NOT NULL,
        venue_name            TEXT NOT NULL DEFAULT '',
        venue_kind            TEXT NOT NULL DEFAULT 'hall' CHECK (venue_kind IN ('hall','lab')),
        day                   TEXT NOT NULL,
        date                  TEXT,
        start_time            TEXT NOT NULL,
        end_time              TEXT NOT NULL,
        version               INTEGER NOT NULL DEFAULT 0,
        base_version          INTEGER NOT NULL DEFAULT 0,
        updated_at            TEXT,
        updated_by            TEXT,
        sync_state            TEXT NOT NULL DEFAULT 'clean',
        deleted               INTEGER NOT NULL DEFAULT 0
      );

      INSERT INTO timetable_entries
        (id, server_id, kind, course_allocation_id, course_code, course_name, lecturer_name,
         program_name, venue_id, venue_name, venue_kind, day, date, start_time, end_time,
         version, base_version, updated_at, updated_by, sync_state, deleted)
      SELECT
        id, server_id, kind, course_allocation_id, course_code, course_name, lecturer_name,
        program_name, venue_id, venue_name, 'hall', day, date, start_time, end_time,
        version, base_version, updated_at, updated_by, sync_state, deleted
      FROM timetable_entries_v1;

      DROP TABLE timetable_entries_v1;

      CREATE INDEX IF NOT EXISTS idx_entries_kind ON timetable_entries(kind);
      CREATE INDEX IF NOT EXISTS idx_entries_server_id ON timetable_entries(server_id);
    `);
            d.prepare('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)').run('schema_version', '2');
        })();
    if (currentVersion < 3)
        d.transaction(() => {
            // Additive only. `reason` lets a server *rejection* (clash, group lock, ...)
            // live in the same conflicts list as a version conflict, so a rejected edit
            // is never silently dropped from the queue.
            d.exec(`
      ALTER TABLE conflicts ADD COLUMN reason TEXT;
      CREATE INDEX IF NOT EXISTS idx_pending_entry ON pending_changes(entry_id);
    `);
            d.prepare('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)').run('schema_version', '3');
        })();
    if (currentVersion < 4)
        d.transaction(() => {
            // Read-only mirror of the data the lab/exam autoscheduler needs as INPUT (not the
            // finished timetable — see timetable_entries above for that). Never edited on the
            // desktop, so unlike timetable_entries there's no version/conflict tracking: a pull
            // just wipes and reinserts (see db.replaceLabExamReference).
            d.exec(`
      CREATE TABLE IF NOT EXISTS ref_lab_venues (
        id        INTEGER PRIMARY KEY,
        code      TEXT NOT NULL,
        capacity  INTEGER
      );

      CREATE TABLE IF NOT EXISTS ref_lecturers (
        id           INTEGER PRIMARY KEY,
        name         TEXT NOT NULL,
        designation  TEXT NOT NULL DEFAULT ''
      );

      CREATE TABLE IF NOT EXISTS ref_programs (
        id                INTEGER PRIMARY KEY,
        name              TEXT NOT NULL,
        department_name   TEXT NOT NULL DEFAULT ''
      );

      CREATE TABLE IF NOT EXISTS ref_lab_allocations (
        id                        INTEGER PRIMARY KEY,
        course_code               TEXT NOT NULL,
        course_name               TEXT NOT NULL DEFAULT '',
        additional_course_codes   TEXT NOT NULL DEFAULT '[]',
        program_id                INTEGER,
        lecturer_id               INTEGER,
        venue_ids                 TEXT NOT NULL DEFAULT '[]',
        number_of_students        INTEGER NOT NULL DEFAULT 0,
        is_workshop_course        INTEGER NOT NULL DEFAULT 0,
        allocation_set_id         INTEGER
      );
    `);
            d.prepare('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)').run('schema_version', '4');
        })();
    if (currentVersion < 5)
        d.transaction(() => {
            // Metadata for PDFs cached locally for offline viewing (see documentsCache.ts). The actual
            // bytes live as files under app.getPath('userData')/cached-pdfs/ — `local_path` points there;
            // this table is just what lets the UI show "cached" vs "not cached" without hitting the disk.
            d.exec(`
      CREATE TABLE IF NOT EXISTS cached_documents (
        id            INTEGER PRIMARY KEY,   -- server PDFDocument id
        title         TEXT NOT NULL,
        document_type TEXT NOT NULL,
        academic_year TEXT NOT NULL,
        semester      TEXT NOT NULL,
        version       INTEGER NOT NULL,
        file_size     INTEGER NOT NULL,
        uploaded_at   TEXT,
        local_path    TEXT NOT NULL,
        cached_at     TEXT NOT NULL
      );
    `);
            d.prepare('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)').run('schema_version', '5');
        })();
}
// ---- Row <-> TimetableEntry mapping -------------------------------------
function rowToEntry(r) {
    return {
        id: r.id,
        server_id: r.server_id,
        kind: r.kind,
        course_allocation_id: r.course_allocation_id,
        course_code: r.course_code,
        course_name: r.course_name,
        lecturer_name: r.lecturer_name,
        program_name: r.program_name,
        venue_id: r.venue_id,
        venue_name: r.venue_name,
        venue_kind: r.venue_kind,
        day: r.day,
        date: r.date,
        start_time: r.start_time,
        end_time: r.end_time,
        version: r.version,
        base_version: r.base_version,
        updated_at: r.updated_at,
        updated_by: r.updated_by,
        sync_state: r.sync_state,
        deleted: !!r.deleted
    };
}
/** Visible entries (locally-deleted rows are hidden). */
function listEntries(kind) {
    const rows = openDb()
        .prepare('SELECT * FROM timetable_entries WHERE kind = ? AND deleted = 0 ORDER BY day, start_time')
        .all(kind);
    return rows.map(rowToEntry);
}
/** Every row for a kind INCLUDING locally-deleted ones — the sync engine must see tombstones. */
function listEntriesRaw(kind) {
    return openDb().prepare('SELECT * FROM timetable_entries WHERE kind = ?').all(kind).map(rowToEntry);
}
/** All entries across every kind — used by the Analysis panel, which reports across the whole timetable. */
function listAllEntries() {
    const rows = openDb().prepare('SELECT * FROM timetable_entries WHERE deleted = 0 ORDER BY kind, day, start_time').all();
    return rows.map(rowToEntry);
}
function getEntry(id) {
    const r = openDb().prepare('SELECT * FROM timetable_entries WHERE id = ?').get(id);
    return r ? rowToEntry(r) : null;
}
/** better-sqlite3 throws on `undefined` named parameters, so normalise every column. */
function upsertEntry(e) {
    const n = (v) => (v === undefined ? null : v);
    openDb()
        .prepare(`INSERT INTO timetable_entries
        (id, server_id, kind, course_allocation_id, course_code, course_name, lecturer_name,
         program_name, venue_id, venue_name, venue_kind, day, date, start_time, end_time,
         version, base_version, updated_at, updated_by, sync_state, deleted)
       VALUES (@id, @server_id, @kind, @course_allocation_id, @course_code, @course_name, @lecturer_name,
         @program_name, @venue_id, @venue_name, @venue_kind, @day, @date, @start_time, @end_time,
         @version, @base_version, @updated_at, @updated_by, @sync_state, @deleted)
       ON CONFLICT(id) DO UPDATE SET
         server_id=excluded.server_id, kind=excluded.kind, course_allocation_id=excluded.course_allocation_id,
         course_code=excluded.course_code, course_name=excluded.course_name, lecturer_name=excluded.lecturer_name,
         program_name=excluded.program_name, venue_id=excluded.venue_id, venue_name=excluded.venue_name,
         venue_kind=excluded.venue_kind,
         day=excluded.day, date=excluded.date, start_time=excluded.start_time, end_time=excluded.end_time,
         version=excluded.version, base_version=excluded.base_version, updated_at=excluded.updated_at,
         updated_by=excluded.updated_by, sync_state=excluded.sync_state, deleted=excluded.deleted`)
        .run({
        id: e.id,
        server_id: n(e.server_id),
        kind: e.kind,
        course_allocation_id: n(e.course_allocation_id),
        course_code: n(e.course_code),
        course_name: e.course_name ?? '',
        lecturer_name: e.lecturer_name ?? '',
        program_name: e.program_name ?? '',
        venue_id: n(e.venue_id),
        venue_name: e.venue_name ?? '',
        venue_kind: e.venue_kind ?? (e.kind === 'lab' || e.kind === 'lab_exam' ? 'lab' : 'hall'),
        day: n(e.day),
        date: n(e.date),
        start_time: n(e.start_time),
        end_time: n(e.end_time),
        version: e.version ?? 0,
        base_version: e.base_version ?? 0,
        updated_at: n(e.updated_at),
        updated_by: n(e.updated_by),
        sync_state: e.sync_state ?? 'clean',
        deleted: e.deleted ? 1 : 0
    });
}
function markDirty(id, patch) {
    const existing = getEntry(id);
    if (!existing)
        return;
    upsertEntry({ ...existing, ...patch, sync_state: 'dirty' });
}
function markDeleted(id) {
    const existing = getEntry(id);
    if (!existing)
        return;
    upsertEntry({ ...existing, deleted: true, sync_state: 'dirty' });
}
function setSyncState(id, state) {
    openDb().prepare('UPDATE timetable_entries SET sync_state = ? WHERE id = ?').run(state, id);
}
/** Run fn atomically (also makes bulk upserts ~100x faster than autocommit per row). */
function runInTransaction(fn) {
    return openDb().transaction(fn)();
}
/** Hard-remove a row (server confirmed a delete, or the server deleted it). */
function purgeEntry(id) {
    openDb().prepare('DELETE FROM timetable_entries WHERE id = ?').run(id);
}
// ---- Pending-change queue -----------------------------------------------
/**
 * Changes currently being sent to the server. They are never merged into:
 * an edit made while a push is in flight becomes a NEW queued change, so the
 * in-flight response can't swallow it.
 */
const inflight = new Set();
function setInflight(ids) {
    inflight.clear();
    ids.forEach((i) => inflight.add(i));
}
function clearInflight() {
    inflight.clear();
}
/**
 * Queue an edit, keeping AT MOST ONE not-yet-sent change per entry:
 *
 *  - Several offline edits to the same row collapse into one change that keeps
 *    the ORIGINAL base_version. (Sending them separately would make the 2nd and
 *    3rd edit conflict with the 1st — i.e. with the user's own change.)
 *  - Editing a row that only exists locally folds into its pending `create`.
 *  - Deleting a row that only exists locally cancels the `create` outright.
 */
function queueChange(c) {
    const d = openDb();
    const rows = d
        .prepare('SELECT * FROM pending_changes WHERE entry_id = ? ORDER BY created_at DESC')
        .all(c.entry_id);
    const open = rows.find((r) => !inflight.has(r.local_op_id));
    if (!open) {
        d.prepare(`INSERT INTO pending_changes (local_op_id, entry_id, kind, op, payload_json, base_version, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`).run(c.local_op_id, c.entry_id, c.kind, c.op, JSON.stringify(c.payload), c.base_version, c.created_at);
        return 'queued';
    }
    if (open.op === 'delete')
        return 'ignored';
    if (open.op === 'create') {
        if (c.op === 'delete') {
            d.prepare('DELETE FROM pending_changes WHERE local_op_id = ?').run(open.local_op_id);
            return 'cancelled';
        }
        const merged = { ...JSON.parse(open.payload_json), ...c.payload };
        d.prepare('UPDATE pending_changes SET payload_json = ? WHERE local_op_id = ?').run(JSON.stringify(merged), open.local_op_id);
        return 'merged';
    }
    // open is update/move
    if (c.op === 'delete') {
        d.prepare('UPDATE pending_changes SET op = ?, payload_json = ? WHERE local_op_id = ?').run('delete', '{}', open.local_op_id);
        return 'merged';
    }
    const merged = { ...JSON.parse(open.payload_json), ...c.payload };
    const op = open.op === 'update' || c.op === 'update' || c.op === 'create' ? 'update' : 'move';
    d.prepare('UPDATE pending_changes SET op = ?, payload_json = ? WHERE local_op_id = ?').run(op, JSON.stringify(merged), open.local_op_id);
    return 'merged';
}
function listPendingChanges() {
    const rows = openDb().prepare('SELECT * FROM pending_changes ORDER BY created_at ASC').all();
    return rows.map((r) => ({
        local_op_id: r.local_op_id,
        entry_id: r.entry_id,
        kind: r.kind,
        op: r.op,
        payload: JSON.parse(r.payload_json),
        base_version: r.base_version,
        created_at: r.created_at
    }));
}
function pendingCount() {
    return openDb().prepare('SELECT COUNT(*) AS n FROM pending_changes').get().n;
}
function hasPending(entry_id) {
    return !!openDb().prepare('SELECT 1 FROM pending_changes WHERE entry_id = ? LIMIT 1').get(entry_id);
}
function clearPendingChange(local_op_id) {
    openDb().prepare('DELETE FROM pending_changes WHERE local_op_id = ?').run(local_op_id);
}
function dropPendingForEntry(entry_id) {
    openDb().prepare('DELETE FROM pending_changes WHERE entry_id = ?').run(entry_id);
}
/** A created-offline row got its real server id: retarget anything still queued against the temp id. */
function rewirePending(oldId, newId, baseVersion) {
    openDb().prepare('UPDATE pending_changes SET entry_id = ?, base_version = ? WHERE entry_id = ?').run(newId, baseVersion, oldId);
}
function rebasePending(entry_id, baseVersion) {
    openDb().prepare('UPDATE pending_changes SET base_version = ? WHERE entry_id = ?').run(baseVersion, entry_id);
}
function saveConflict(entry_id, kind, local, server, base_version, reason = null) {
    const d = openDb();
    d.prepare(`INSERT INTO conflicts (entry_id, kind, local_json, server_json, base_version, created_at, reason)
     VALUES (?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(entry_id) DO UPDATE SET local_json=excluded.local_json, server_json=excluded.server_json,
       base_version=excluded.base_version, created_at=excluded.created_at, reason=excluded.reason`).run(entry_id, kind, JSON.stringify(local), JSON.stringify(server), base_version, new Date().toISOString(), reason);
    setSyncState(entry_id, 'conflict');
}
function listConflicts() {
    const rows = openDb().prepare('SELECT * FROM conflicts ORDER BY created_at ASC').all();
    return rows.map((r) => ({
        entry_id: r.entry_id,
        kind: r.kind,
        local: JSON.parse(r.local_json),
        server: JSON.parse(r.server_json),
        base_version: r.base_version,
        reason: r.reason ?? null
    }));
}
function getConflict(entry_id) {
    return listConflicts().find((c) => c.entry_id === entry_id);
}
function resolveConflict(entry_id) {
    openDb().prepare('DELETE FROM conflicts WHERE entry_id = ?').run(entry_id);
}
// ---- Meta + data origin ---------------------------------------------------
function getMeta(key) {
    const r = openDb().prepare('SELECT value FROM meta WHERE key = ?').get(key);
    return r ? r.value : null;
}
function setMeta(key, value) {
    openDb().prepare('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)').run(key, value);
}
/** Remove every cached timetable row, queued change and conflict (used when switching to a different server). */
function wipeData() {
    const d = openDb();
    d.transaction(() => {
        d.exec('DELETE FROM timetable_entries; DELETE FROM pending_changes; DELETE FROM conflicts;');
    })();
}
// ---- Session (persisted login) ------------------------------------------
// Stored in the same per-user SQLite file as everything else — this file
// is already only readable by the OS user account, which is the same
// protection a browser's cookie jar relies on. Swap for Electron's
// `safeStorage` (OS keychain) before shipping to production if you want
// encryption at rest for the token specifically.
function saveSession(session) {
    setMeta('session', JSON.stringify(session));
}
function getSession() {
    const v = getMeta('session');
    return v ? JSON.parse(v) : null;
}
function clearSession() {
    openDb().prepare('DELETE FROM meta WHERE key = ?').run('session');
}
// ---- Cached published-PDF metadata (bytes live on disk — see documentsCache.ts) ----
function upsertCachedDocument(doc) {
    openDb().prepare(`
    INSERT INTO cached_documents (id, title, document_type, academic_year, semester, version, file_size, uploaded_at, local_path, cached_at)
    VALUES (@id, @title, @document_type, @academic_year, @semester, @version, @file_size, @uploaded_at, @local_path, @cached_at)
    ON CONFLICT(id) DO UPDATE SET
      title=excluded.title, document_type=excluded.document_type, academic_year=excluded.academic_year,
      semester=excluded.semester, version=excluded.version, file_size=excluded.file_size,
      uploaded_at=excluded.uploaded_at, local_path=excluded.local_path, cached_at=excluded.cached_at
  `).run(doc);
}
function getCachedDocument(id) {
    return openDb().prepare('SELECT local_path FROM cached_documents WHERE id = ?').get(id) || null;
}
function listCachedDocuments() {
    const rows = openDb().prepare('SELECT * FROM cached_documents ORDER BY cached_at DESC').all();
    return rows.map((r) => ({
        id: r.id,
        title: r.title,
        document_type: r.document_type,
        academic_year: r.academic_year,
        semester: r.semester,
        version: r.version,
        file_size: r.file_size,
        uploaded_at: r.uploaded_at,
        cached: true,
        local_path: r.local_path,
        cached_at: r.cached_at
    }));
}
function removeCachedDocument(id) {
    openDb().prepare('DELETE FROM cached_documents WHERE id = ?').run(id);
}
function replaceLabExamReference(data) {
    const d = openDb();
    d.transaction(() => {
        d.exec('DELETE FROM ref_lab_allocations; DELETE FROM ref_lab_venues; DELETE FROM ref_lecturers; DELETE FROM ref_programs;');
        const insVenue = d.prepare('INSERT INTO ref_lab_venues (id, code, capacity) VALUES (?, ?, ?)');
        for (const v of data.lab_venues)
            insVenue.run(v.id, v.code, v.capacity);
        const insLecturer = d.prepare('INSERT INTO ref_lecturers (id, name, designation) VALUES (?, ?, ?)');
        for (const l of data.lecturers)
            insLecturer.run(l.id, l.name, l.designation);
        const insProgram = d.prepare('INSERT INTO ref_programs (id, name, department_name) VALUES (?, ?, ?)');
        for (const p of data.programs)
            insProgram.run(p.id, p.name, p.department_name);
        const insAlloc = d.prepare(`
      INSERT INTO ref_lab_allocations
        (id, course_code, course_name, additional_course_codes, program_id, lecturer_id, venue_ids, number_of_students, is_workshop_course, allocation_set_id)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
        for (const a of data.lab_allocations) {
            insAlloc.run(a.id, a.course_code, a.course_name, JSON.stringify(a.additional_course_codes), a.program_id, a.lecturer_id, JSON.stringify(a.venue_ids), a.number_of_students, a.is_workshop_course ? 1 : 0, a.allocation_set_id);
        }
        setMeta('lab_exam_reference_generated_at', data.generated_at);
        setMeta('lab_exam_scheduler_config', JSON.stringify(data.config));
        setMeta('lab_exam_existing_exam_busy', JSON.stringify(data.existing_exam_busy));
    })();
}
function getLabExamReference() {
    const configRaw = getMeta('lab_exam_scheduler_config');
    if (!configRaw)
        return null; // never pulled yet
    const d = openDb();
    const lab_venues = d.prepare('SELECT id, code, capacity FROM ref_lab_venues').all();
    const lecturers = d.prepare('SELECT id, name, designation FROM ref_lecturers').all();
    const programs = d.prepare('SELECT id, name, department_name FROM ref_programs').all();
    const allocRows = d.prepare('SELECT * FROM ref_lab_allocations').all();
    const lab_allocations = allocRows.map((r) => ({
        id: r.id,
        course_code: r.course_code,
        course_name: r.course_name,
        additional_course_codes: JSON.parse(r.additional_course_codes),
        program_id: r.program_id,
        lecturer_id: r.lecturer_id,
        venue_ids: JSON.parse(r.venue_ids),
        number_of_students: r.number_of_students,
        is_workshop_course: !!r.is_workshop_course,
        allocation_set_id: r.allocation_set_id
    }));
    return {
        generated_at: getMeta('lab_exam_reference_generated_at') || '',
        config: JSON.parse(configRaw),
        lab_allocations,
        lab_venues,
        lecturers,
        programs,
        existing_exam_busy: JSON.parse(getMeta('lab_exam_existing_exam_busy') || '[]')
    };
}
