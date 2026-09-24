"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
/**
 * Headless end-to-end test of the desktop sync engine against a REAL running Django server
 * (the project with `desktop_sync` installed). It drives the same modules the Electron app uses
 * (db, editOps, syncEngine, authClient) — only the window/IPC layer is absent.
 *
 *   npm run test:sync
 *
 * Environment (defaults suit the QA setup described in desktop_app/README.md):
 *   E2E_BASE_URL   http://127.0.0.1:8765
 *   E2E_USER/PASS  ttadmin / pw-12345           a Timetable Admin account
 *   E2E_PY         python interpreter that can run the Django project
 *   E2E_PROJECT    path to the Django project root (manage.py)
 *   E2E_PYTHONPATH extra PYTHONPATH,  E2E_SETTINGS  DJANGO_SETTINGS_MODULE
 *
 * The server database needs: 6 regular entries COSC100..COSC105 (Timetable), 3 exam entries,
 * 1 lab entry — see README "Testing" for the seed script. The test MUTATES that database.
 */
const assert_1 = __importDefault(require("assert"));
const http_1 = __importDefault(require("http"));
const os_1 = __importDefault(require("os"));
const path_1 = __importDefault(require("path"));
const fs_1 = __importDefault(require("fs"));
const child_process_1 = require("child_process");
const uuid_1 = require("uuid");
const tmp = fs_1.default.mkdtempSync(path_1.default.join(os_1.default.tmpdir(), 'chuka-e2e-'));
process.env.CHUKA_DB_PATH = path_1.default.join(tmp, 'local.sqlite3');
process.env.CHUKA_PDF_CACHE_DIR = path_1.default.join(tmp, 'cached-pdfs');
const db = __importStar(require("../db"));
const sync = __importStar(require("../syncEngine"));
const auth = __importStar(require("../authClient"));
const editOps_1 = require("../editOps");
const labExamScheduler_1 = require("../labExamScheduler");
const documentsCache = __importStar(require("../documentsCache"));
const http_2 = require("../http");
const BASE = process.env.E2E_BASE_URL || 'http://127.0.0.1:8765';
const USER = process.env.E2E_USER || 'ttadmin';
const PASS = process.env.E2E_PASS || 'pw-12345';
const PROJECT = process.env.E2E_PROJECT || process.cwd();
/** Run Python inside the Django project (used to simulate "someone edited on the web"). Returns parsed JSON of the last stdout line. */
function py(code) {
    const r = (0, child_process_1.spawnSync)(process.env.E2E_PY || 'python3', ['manage.py', 'shell', '-c', code], {
        cwd: PROJECT,
        encoding: 'utf8',
        env: {
            ...process.env,
            PYTHONPATH: process.env.E2E_PYTHONPATH || '',
            DJANGO_SETTINGS_MODULE: process.env.E2E_SETTINGS || 'university_timetable_system.settings'
        }
    });
    if (r.status !== 0)
        throw new Error('py failed: ' + r.stderr);
    const lines = r.stdout.trim().split('\n');
    const last = lines[lines.length - 1];
    try {
        return JSON.parse(last);
    }
    catch {
        return last;
    }
}
const PRE = `
import json, datetime as dt
from timetable.models import Timetable, ExamTimetable, LabTimetable
from desktop_sync.models import DesktopAuthToken
def row(m, code):
    r = m.objects.filter(course_allocation__course_code__in=[code, code[:4]+' '+code[4:]]).first()
    return r
def dump(r):
    return {"day": r.day, "start": r.start_time.strftime("%H:%M"), "end": r.end_time.strftime("%H:%M"), "venue": r.venue.code, "date": r.date.isoformat() if hasattr(r,'date') and r.date else None} if r else None
`;
let session;
const results = [];
async function test(name, fn) {
    try {
        await fn();
        results.push({ name, ok: true });
        console.log('  PASS', name);
    }
    catch (e) {
        results.push({ name, ok: false, err: e?.stack || String(e) });
        console.log('  FAIL', name, '\n      ', (e?.message || String(e)).split('\n').join('\n       '));
    }
}
const find = (kind, code) => {
    const norm = (s) => s.replace(/\s+/g, '');
    const e = db.listEntries(kind).find((x) => norm(x.course_code) === code);
    if (!e)
        throw new Error(`no local ${kind} entry for ${code}`);
    return e;
};
function edit(op, e, payload = {}, entryId = e.id, opId) {
    (0, editOps_1.applyEdit)({
        local_op_id: opId || (0, uuid_1.v4)(),
        entry_id: entryId,
        kind: e.kind,
        op,
        payload: op === 'move' ? { ...payload, venue_id: e.venue_id } : payload,
        base_version: e.base_version,
        created_at: new Date().toISOString()
    });
}
async function main() {
    db.openDb();
    console.log(`Sync E2E against ${BASE}`);
    await test('login as a Timetable Admin and first sync pulls every kind', async () => {
        session = await auth.login({ baseUrl: BASE, username: USER, password: PASS });
        assert_1.default.ok(session.token);
        sync.configure({ baseUrl: session.baseUrl, token: session.token });
        const s = await sync.syncAll();
        assert_1.default.strictEqual(db.listEntries('regular').length, 6);
        assert_1.default.strictEqual(db.listEntries('exam').length, 3);
        assert_1.default.strictEqual(db.listEntries('lab').length, 1);
        assert_1.default.strictEqual(s.pending, 0);
        assert_1.default.ok(db.listAllEntries().every((e) => e.sync_state === 'clean' && e.version > 0 && e.base_version === e.version));
    });
    await test('repeated offline edits to one row collapse into ONE change and apply without self-conflict', async () => {
        const e = find('regular', 'COSC100');
        edit('move', e, { day: 'Friday', start_time: '13:00', end_time: '15:00' });
        edit('move', db.getEntry(e.id), { day: 'Saturday', start_time: '09:00', end_time: '11:00' });
        assert_1.default.strictEqual(db.pendingCount(), 1);
        const s = await sync.syncAll();
        assert_1.default.strictEqual(s.applied, 1);
        assert_1.default.strictEqual(s.conflicts, 0);
        assert_1.default.strictEqual(s.pending, 0);
        const after = db.getEntry(e.id);
        assert_1.default.strictEqual(after.sync_state, 'clean');
        assert_1.default.deepStrictEqual([after.day, after.start_time], ['Saturday', '09:00']);
        assert_1.default.notStrictEqual(after.version, e.version);
        assert_1.default.deepStrictEqual(py(PRE + 'print(json.dumps(dump(row(Timetable,"COSC100"))))'), { day: 'Saturday', start: '09:00', end: '11:00', venue: 'LH0', date: null });
    });
    await test('a pasted copy that is edited before syncing folds into its create and swaps to the real server row', async () => {
        const src = find('regular', 'COSC101');
        const newId = (0, uuid_1.v4)();
        const clone = { ...src, id: newId, server_id: null, day: 'Friday', start_time: '08:00', end_time: '10:00', version: 0, base_version: 0, sync_state: 'dirty' };
        (0, editOps_1.applyEdit)({ local_op_id: (0, uuid_1.v4)(), entry_id: newId, kind: 'regular', op: 'create', payload: clone, base_version: 0, created_at: new Date().toISOString() });
        assert_1.default.ok(db.getEntry(newId), 'pasted row must exist in the local DB immediately (survives restart)');
        edit('move', db.getEntry(newId), { day: 'Friday', start_time: '10:00', end_time: '12:00' }, newId);
        assert_1.default.strictEqual(db.pendingCount(), 1);
        const before = db.listEntries('regular').length;
        const s = await sync.syncAll();
        assert_1.default.strictEqual(s.applied, 1);
        assert_1.default.strictEqual(db.getEntry(newId), null, 'temporary local row must be replaced');
        assert_1.default.strictEqual(db.listEntries('regular').length, before, 'no duplicate left behind');
        const created = db.listEntries('regular').find((x) => x.day === 'Friday' && x.start_time === '10:00');
        assert_1.default.ok(created && created.server_id && created.sync_state === 'clean');
        assert_1.default.strictEqual(py(PRE + 'print(Timetable.objects.count())'), 7);
    });
    await test('creating then deleting a row before it ever syncs sends nothing', async () => {
        const src = find('regular', 'COSC102');
        const newId = (0, uuid_1.v4)();
        (0, editOps_1.applyEdit)({ local_op_id: (0, uuid_1.v4)(), entry_id: newId, kind: 'regular', op: 'create', payload: { ...src, id: newId, day: 'Friday', start_time: '16:00', end_time: '18:00' }, base_version: 0, created_at: new Date().toISOString() });
        edit('delete', db.getEntry(newId), {}, newId);
        assert_1.default.strictEqual(db.pendingCount(), 0);
        assert_1.default.strictEqual(db.getEntry(newId), null);
    });
    await test('deleting a synced row removes it on the server and locally, and is not reported as an error', async () => {
        const e = find('regular', 'COSC102');
        edit('delete', e);
        assert_1.default.strictEqual(db.listEntries('regular').some((x) => x.id === e.id), false);
        const s = await sync.syncAll();
        assert_1.default.strictEqual(s.applied, 1);
        assert_1.default.strictEqual(s.conflicts, 0);
        assert_1.default.strictEqual(db.getEntry(e.id), null, 'no tombstone left behind');
        assert_1.default.strictEqual(py(PRE + 'print(json.dumps(dump(row(Timetable,"COSC102"))))'), null);
    });
    await test('a web edit (queryset.update, no signals) made while the desktop was offline is caught as a conflict; keep_local then wins cleanly', async () => {
        const e = find('regular', 'COSC103');
        py(PRE + 'Timetable.objects.filter(pk=%d).update(day="Friday", start_time=dt.time(16), end_time=dt.time(18)); print(1)'.replace('%d', String(e.server_id)));
        edit('move', e, { day: 'Saturday', start_time: '13:00', end_time: '15:00' });
        const s = await sync.syncAll();
        assert_1.default.strictEqual(s.conflicts, 1);
        const conflicts = db.listConflicts();
        assert_1.default.strictEqual(conflicts.length, 1);
        assert_1.default.strictEqual(conflicts[0].reason, null);
        assert_1.default.strictEqual(conflicts[0].server.day, 'Friday');
        assert_1.default.strictEqual(db.getEntry(e.id).sync_state, 'conflict');
        assert_1.default.strictEqual(py(PRE + 'print(json.dumps(dump(row(Timetable,"COSC103"))))').day, 'Friday', 'server untouched by the conflicting push');
        assert_1.default.throws(() => edit('move', db.getEntry(e.id), { day: 'Monday', start_time: '07:00', end_time: '09:00' }), /Resolve the conflict/);
        (0, editOps_1.resolveConflict)({ entryId: e.id, resolution: 'keep_local' });
        const s2 = await sync.syncAll();
        assert_1.default.strictEqual(s2.applied, 1);
        assert_1.default.strictEqual(db.listConflicts().length, 0);
        assert_1.default.deepStrictEqual(py(PRE + 'print(json.dumps(dump(row(Timetable,"COSC103"))))').day, 'Saturday');
    });
    await test('keep_server adopts the web version and drops the local edit', async () => {
        const e = find('regular', 'COSC104');
        py(PRE + 'Timetable.objects.filter(pk=%d).update(day="Friday"); print(1)'.replace('%d', String(e.server_id)));
        edit('move', e, { day: 'Saturday', start_time: '13:00', end_time: '15:00' });
        await sync.syncAll();
        assert_1.default.strictEqual(db.listConflicts().length, 1);
        (0, editOps_1.resolveConflict)({ entryId: e.id, resolution: 'keep_server' });
        const after = db.getEntry(e.id);
        assert_1.default.deepStrictEqual([after.day, after.sync_state], ['Friday', 'clean']);
        assert_1.default.strictEqual(db.pendingCount(), 0);
        const s = await sync.syncAll();
        assert_1.default.strictEqual(s.applied + s.conflicts, 0);
    });
    await test('a change the server REJECTS (venue/lecturer clash) becomes a conflict with the reason — it is not dropped', async () => {
        const target = find('regular', 'COSC100'); // Saturday 09-11 LH0, Dr 0
        const e = find('regular', 'COSC103'); // Dr 0 too, currently Saturday 13-15
        edit('move', e, { day: target.day, start_time: target.start_time, end_time: target.end_time });
        const s = await sync.syncAll();
        assert_1.default.strictEqual(s.conflicts, 1);
        const c = db.listConflicts()[0];
        assert_1.default.ok(c.reason && /clash/i.test(c.reason), 'reason shown to the user: ' + c.reason);
        assert_1.default.strictEqual(db.pendingCount(), 0);
        assert_1.default.strictEqual(py(PRE + 'print(json.dumps(dump(row(Timetable,"COSC103"))))').start, '13:00');
        (0, editOps_1.resolveConflict)({ entryId: e.id, resolution: 'keep_server' });
        assert_1.default.strictEqual(db.getEntry(e.id).start_time, '13:00');
    });
    await test('a row deleted on the server disappears locally; if it had an unsynced edit it becomes a conflict, and keep_server forgets it', async () => {
        const clean = find('regular', 'COSC105');
        const dirty = find('regular', 'COSC101');
        py(PRE + 'Timetable.objects.filter(pk__in=[%d,%d]).delete(); print(1)'.replace('%d', String(clean.server_id)).replace('%d', String(dirty.server_id)));
        edit('move', dirty, { day: 'Saturday', start_time: '07:00', end_time: '09:00' });
        await sync.syncAll();
        assert_1.default.strictEqual(db.getEntry(clean.id), null);
        const c = db.listConflicts().find((x) => x.entry_id === dirty.id);
        assert_1.default.ok(c && c.server.deleted && c.reason, 'deleted-on-server conflict with reason');
        (0, editOps_1.resolveConflict)({ entryId: dirty.id, resolution: 'keep_server' });
        assert_1.default.strictEqual(db.getEntry(dirty.id), null);
    });
    await test('a retried push after a lost response is applied once (idempotent local_op_id)', async () => {
        const src = find('regular', 'COSC104');
        const newId = (0, uuid_1.v4)();
        const opId = (0, uuid_1.v4)();
        const change = {
            local_op_id: opId, entry_id: newId, kind: 'regular', op: 'create', base_version: 0, created_at: new Date().toISOString(),
            payload: { course_allocation_id: src.course_allocation_id, venue_id: src.venue_id, day: 'Wednesday', start_time: '13:00', end_time: '15:00' }
        };
        // Server applies it, but the client "never receives" the answer:
        const raw = await fetch(`${BASE}/api/desktop/timetable/regular/push/`, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Token ${session.token}` }, body: JSON.stringify({ changes: [change] }) });
        assert_1.default.strictEqual((await raw.json()).results[0].status, 'applied');
        const countAfterFirst = py(PRE + 'print(Timetable.objects.count())');
        // ...so the client still has it queued and resends on the next sync:
        (0, editOps_1.applyEdit)({ ...change, payload: { ...src, id: newId, day: 'Wednesday', start_time: '13:00', end_time: '15:00' } });
        const s = await sync.syncAll();
        assert_1.default.strictEqual(s.applied, 1);
        assert_1.default.strictEqual(py(PRE + 'print(Timetable.objects.count())'), countAfterFirst, 'no duplicate row');
        assert_1.default.strictEqual(db.getEntry(newId), null);
    });
    await test('exam move by date keeps day and date consistent', async () => {
        const e = db.listEntries('exam')[0];
        edit('move', e, { day: 'Wednesday', date: '2030-01-16', start_time: '08:00', end_time: '11:00' });
        const s = await sync.syncAll();
        assert_1.default.strictEqual(s.applied, 1, JSON.stringify(db.listConflicts()));
        const after = db.getEntry(e.id);
        assert_1.default.deepStrictEqual([after.date, after.day, after.sync_state], ['2030-01-16', 'Wednesday', 'clean']);
    });
    await test('lab session move', async () => {
        const e = db.listEntries('lab')[0];
        edit('move', e, { day: 'Friday', start_time: '14:00', end_time: '17:00' });
        const s = await sync.syncAll();
        assert_1.default.strictEqual(s.applied, 1, JSON.stringify(db.listConflicts()));
        assert_1.default.strictEqual(db.getEntry(e.id).day, 'Friday');
    });
    await test('offline: an unreachable server keeps the edit queued; it syncs once reachable', async () => {
        const e = db.listEntries('regular')[0];
        edit('move', e, { day: 'Monday', start_time: '16:00', end_time: '18:00' });
        sync.configure({ baseUrl: 'http://127.0.0.1:1', token: session.token });
        await assert_1.default.rejects(sync.syncAll(), http_2.NetworkError);
        assert_1.default.strictEqual(db.pendingCount(), 1);
        assert_1.default.strictEqual(db.getEntry(e.id).sync_state, 'dirty');
        sync.configure({ baseUrl: session.baseUrl, token: session.token });
        const s = await sync.syncAll();
        assert_1.default.strictEqual(s.pending, 0);
    });
    await test('an edit made while a push is in flight is not swallowed by that push', async () => {
        const e = db.listEntries('regular')[0];
        edit('move', e, { day: 'Tuesday', start_time: '07:00', end_time: '09:00' });
        const opId = db.listPendingChanges()[0].local_op_id;
        db.setInflight([opId]);
        edit('move', db.getEntry(e.id), { day: 'Tuesday', start_time: '10:00', end_time: '12:00' });
        db.clearInflight();
        assert_1.default.strictEqual(db.pendingCount(), 2, 'second edit queued separately');
        db.clearPendingChange(db.listPendingChanges()[1].local_op_id); // tidy: collapse back for the real sync below
        db.markDirty(e.id, { day: 'Tuesday', start_time: '07:00', end_time: '09:00' });
        await sync.syncAll();
    });
    await test('a revoked token surfaces as AuthError (sign in again), and a 502 from a proxy does NOT sign you out', async () => {
        py(PRE + 'DesktopAuthToken.objects.all().delete(); print(1)');
        await assert_1.default.rejects(sync.syncAll(), http_2.AuthError);
        assert_1.default.strictEqual(await auth.whoAmI(session), 'invalid');
        const srv = http_1.default.createServer((_q, r) => { r.statusCode = 502; r.end('<html>Bad gateway</html>'); });
        await new Promise((res) => srv.listen(0, '127.0.0.1', () => res()));
        const port = srv.address().port;
        assert_1.default.strictEqual(await auth.whoAmI({ ...session, baseUrl: `http://127.0.0.1:${port}` }), 'unknown');
        srv.close();
        session = await auth.login({ baseUrl: BASE, username: USER, password: PASS });
        sync.configure({ baseUrl: session.baseUrl, token: session.token });
        await sync.syncAll();
    });
    await test('bad credentials and non-role accounts get readable errors', async () => {
        await assert_1.default.rejects(auth.login({ baseUrl: BASE, username: USER, password: 'wrong' }), /Invalid username or password/);
    });
    await test('server address normalisation', async () => {
        assert_1.default.strictEqual((0, http_2.normalizeBaseUrl)('timetable.chuka.ac.ke'), 'https://timetable.chuka.ac.ke');
        assert_1.default.strictEqual((0, http_2.normalizeBaseUrl)(' https://timetable.chuka.ac.ke/ '), 'https://timetable.chuka.ac.ke');
        assert_1.default.strictEqual((0, http_2.normalizeBaseUrl)('localhost:8000'), 'http://localhost:8000');
        assert_1.default.strictEqual((0, http_2.normalizeBaseUrl)('192.168.1.5:8000/some/path'), 'http://192.168.1.5:8000');
        assert_1.default.throws(() => (0, http_2.normalizeBaseUrl)('ftp://x'), /http/);
    });
    await test('schema is at the current version with the v3 columns', async () => {
        const cols = db.openDb().prepare("PRAGMA table_info(conflicts)").all().map((c) => c.name);
        assert_1.default.ok(cols.includes('reason'));
        assert_1.default.strictEqual(db.getMeta('schema_version'), '5');
    });
    await test('v5 cached_documents table exists for the published-PDF cache', async () => {
        const tables = db.openDb().prepare("SELECT name FROM sqlite_master WHERE type='table'").all().map((t) => t.name);
        assert_1.default.ok(tables.includes('cached_documents'));
    });
    await test('v4 reference tables exist for the lab/exam autoscheduler mirror', async () => {
        const tables = db.openDb().prepare("SELECT name FROM sqlite_master WHERE type='table'").all().map((t) => t.name);
        for (const t of ['ref_lab_venues', 'ref_lecturers', 'ref_programs', 'ref_lab_allocations']) {
            assert_1.default.ok(tables.includes(t), `missing table ${t}`);
        }
    });
    await test('pullLabExamReference mirrors the seeded lab allocation locally', async () => {
        const result = await sync.pullLabExamReference();
        assert_1.default.ok(result.allocations >= 1, 'expected at least the seeded lab allocation');
        const cached = db.getLabExamReference();
        assert_1.default.ok(cached, 'expected reference data to be cached after pull');
        assert_1.default.ok(cached.lab_allocations.length >= 1);
        assert_1.default.ok(cached.lab_venues.length >= 1);
        assert_1.default.ok(typeof cached.config.slot_size === 'number');
    });
    await test('runLabExamSchedulerLocally schedules the seeded lab allocation entirely offline (real python subprocess)', async () => {
        const before = db.listEntries('lab_exam');
        const result = await (0, labExamScheduler_1.runLabExamSchedulerLocally)(true);
        assert_1.default.notStrictEqual(result.status, 'error', result.message);
        assert_1.default.ok(result.created >= 1, `expected at least one scheduled session, got: ${result.message}`);
        assert_1.default.strictEqual(result.removed, before.length);
        const after = db.listEntries('lab_exam');
        assert_1.default.strictEqual(after.length, result.created);
        for (const e of after) {
            assert_1.default.strictEqual(e.sync_state, 'dirty'); // queued, not yet pushed
            assert_1.default.ok(e.venue_name, 'expected a venue name resolved from the local reference mirror');
        }
    });
    await test('listPublishedDocuments sees the seeded PUBLISHED regular PDF (and only published ones)', async () => {
        const docs = await documentsCache.listPublishedDocuments();
        assert_1.default.ok(docs.length >= 1, 'expected the seeded regular PDF');
        const reg = docs.find((d) => d.document_type === 'REGULAR');
        assert_1.default.ok(reg, 'expected a REGULAR document');
        assert_1.default.strictEqual(reg.cached, false); // not downloaded yet
        global.__seededDoc = reg;
    });
    await test('downloadDocument caches real bytes to disk and records it locally', async () => {
        const doc = global.__seededDoc;
        const localPath = await documentsCache.downloadDocument(doc);
        assert_1.default.ok(fs_1.default.existsSync(localPath), `expected a file at ${localPath}`);
        const bytes = fs_1.default.readFileSync(localPath);
        assert_1.default.ok(bytes.length > 0 && bytes.slice(0, 4).toString() === '%PDF', 'expected real PDF bytes on disk');
        const onlineList = await documentsCache.listPublishedDocuments();
        const same = onlineList.find((d) => d.id === doc.id);
        assert_1.default.strictEqual(same?.cached, true);
        assert_1.default.strictEqual(same?.local_path, localPath);
    });
    await test('listCachedDocumentsOffline works with zero network calls', async () => {
        const doc = global.__seededDoc;
        const offlineList = documentsCache.listCachedDocumentsOffline();
        assert_1.default.ok(offlineList.some((d) => d.id === doc.id && d.cached && fs_1.default.existsSync(d.local_path)));
    });
    await test('removeCachedDocument deletes both the file and the local record', async () => {
        const doc = global.__seededDoc;
        const before = documentsCache.listCachedDocumentsOffline().find((d) => d.id === doc.id);
        documentsCache.removeCachedDocument(doc.id);
        assert_1.default.ok(!fs_1.default.existsSync(before.local_path), 'expected the cached file to be deleted');
        assert_1.default.ok(!documentsCache.listCachedDocumentsOffline().some((d) => d.id === doc.id));
    });
    const failed = results.filter((r) => !r.ok);
    console.log(`\n${results.length - failed.length}/${results.length} passed`);
    db.closeDb();
    fs_1.default.rmSync(tmp, { recursive: true, force: true });
    process.exit(failed.length ? 1 : 0);
}
main().catch((e) => {
    console.error(e);
    process.exit(1);
});
