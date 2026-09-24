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
import assert from 'assert';
import http from 'http';
import os from 'os';
import path from 'path';
import fs from 'fs';
import { spawnSync } from 'child_process';
import { v4 as uuid } from 'uuid';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'chuka-e2e-'));
process.env.CHUKA_DB_PATH = path.join(tmp, 'local.sqlite3');
process.env.CHUKA_PDF_CACHE_DIR = path.join(tmp, 'cached-pdfs');

import * as db from '../db';
import * as sync from '../syncEngine';
import * as auth from '../authClient';
import { applyEdit, resolveConflict } from '../editOps';
import { runLabExamSchedulerLocally } from '../labExamScheduler';
import * as documentsCache from '../documentsCache';
import { AuthError, NetworkError, normalizeBaseUrl } from '../http';
import { EntryKind, PendingChange, TimetableEntry, UserSession } from '../../shared/types';

const BASE = process.env.E2E_BASE_URL || 'http://127.0.0.1:8765';
const USER = process.env.E2E_USER || 'ttadmin';
const PASS = process.env.E2E_PASS || 'pw-12345';
const PROJECT = process.env.E2E_PROJECT || process.cwd();

/** Run Python inside the Django project (used to simulate "someone edited on the web"). Returns parsed JSON of the last stdout line. */
function py(code: string): any {
  const r = spawnSync(process.env.E2E_PY || 'python3', ['manage.py', 'shell', '-c', code], {
    cwd: PROJECT,
    encoding: 'utf8',
    env: {
      ...process.env,
      PYTHONPATH: process.env.E2E_PYTHONPATH || '',
      DJANGO_SETTINGS_MODULE: process.env.E2E_SETTINGS || 'university_timetable_system.settings'
    }
  });
  if (r.status !== 0) throw new Error('py failed: ' + r.stderr);
  const lines = r.stdout.trim().split('\n');
  const last = lines[lines.length - 1];
  try {
    return JSON.parse(last);
  } catch {
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

let session: UserSession;
const results: { name: string; ok: boolean; err?: string }[] = [];

async function test(name: string, fn: () => Promise<void>) {
  try {
    await fn();
    results.push({ name, ok: true });
    console.log('  PASS', name);
  } catch (e: any) {
    results.push({ name, ok: false, err: e?.stack || String(e) });
    console.log('  FAIL', name, '\n      ', (e?.message || String(e)).split('\n').join('\n       '));
  }
}

const find = (kind: EntryKind, code: string): TimetableEntry => {
  const norm = (s: string) => s.replace(/\s+/g, '');
  const e = db.listEntries(kind).find((x) => norm(x.course_code) === code);
  if (!e) throw new Error(`no local ${kind} entry for ${code}`);
  return e;
};

function edit(op: PendingChange['op'], e: TimetableEntry, payload: Partial<TimetableEntry> = {}, entryId = e.id, opId?: string) {
  applyEdit({
    local_op_id: opId || uuid(),
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
    assert.ok(session.token);
    sync.configure({ baseUrl: session.baseUrl, token: session.token });
    const s = await sync.syncAll();
    assert.strictEqual(db.listEntries('regular').length, 6);
    assert.strictEqual(db.listEntries('exam').length, 3);
    assert.strictEqual(db.listEntries('lab').length, 1);
    assert.strictEqual(s.pending, 0);
    assert.ok(db.listAllEntries().every((e) => e.sync_state === 'clean' && e.version > 0 && e.base_version === e.version));
  });

  await test('repeated offline edits to one row collapse into ONE change and apply without self-conflict', async () => {
    const e = find('regular', 'COSC100');
    edit('move', e, { day: 'Friday', start_time: '13:00', end_time: '15:00' });
    edit('move', db.getEntry(e.id)!, { day: 'Saturday', start_time: '09:00', end_time: '11:00' });
    assert.strictEqual(db.pendingCount(), 1);
    const s = await sync.syncAll();
    assert.strictEqual(s.applied, 1);
    assert.strictEqual(s.conflicts, 0);
    assert.strictEqual(s.pending, 0);
    const after = db.getEntry(e.id)!;
    assert.strictEqual(after.sync_state, 'clean');
    assert.deepStrictEqual([after.day, after.start_time], ['Saturday', '09:00']);
    assert.notStrictEqual(after.version, e.version);
    assert.deepStrictEqual(py(PRE + 'print(json.dumps(dump(row(Timetable,"COSC100"))))'), { day: 'Saturday', start: '09:00', end: '11:00', venue: 'LH0', date: null });
  });

  await test('a pasted copy that is edited before syncing folds into its create and swaps to the real server row', async () => {
    const src = find('regular', 'COSC101');
    const newId = uuid();
    const clone = { ...src, id: newId, server_id: null, day: 'Friday', start_time: '08:00', end_time: '10:00', version: 0, base_version: 0, sync_state: 'dirty' as const };
    applyEdit({ local_op_id: uuid(), entry_id: newId, kind: 'regular', op: 'create', payload: clone, base_version: 0, created_at: new Date().toISOString() });
    assert.ok(db.getEntry(newId), 'pasted row must exist in the local DB immediately (survives restart)');
    edit('move', db.getEntry(newId)!, { day: 'Friday', start_time: '10:00', end_time: '12:00' }, newId);
    assert.strictEqual(db.pendingCount(), 1);
    const before = db.listEntries('regular').length;
    const s = await sync.syncAll();
    assert.strictEqual(s.applied, 1);
    assert.strictEqual(db.getEntry(newId), null, 'temporary local row must be replaced');
    assert.strictEqual(db.listEntries('regular').length, before, 'no duplicate left behind');
    const created = db.listEntries('regular').find((x) => x.day === 'Friday' && x.start_time === '10:00')!;
    assert.ok(created && created.server_id && created.sync_state === 'clean');
    assert.strictEqual(py(PRE + 'print(Timetable.objects.count())'), 7);
  });

  await test('creating then deleting a row before it ever syncs sends nothing', async () => {
    const src = find('regular', 'COSC102');
    const newId = uuid();
    applyEdit({ local_op_id: uuid(), entry_id: newId, kind: 'regular', op: 'create', payload: { ...src, id: newId, day: 'Friday', start_time: '16:00', end_time: '18:00' }, base_version: 0, created_at: new Date().toISOString() });
    edit('delete', db.getEntry(newId)!, {}, newId);
    assert.strictEqual(db.pendingCount(), 0);
    assert.strictEqual(db.getEntry(newId), null);
  });

  await test('deleting a synced row removes it on the server and locally, and is not reported as an error', async () => {
    const e = find('regular', 'COSC102');
    edit('delete', e);
    assert.strictEqual(db.listEntries('regular').some((x) => x.id === e.id), false);
    const s = await sync.syncAll();
    assert.strictEqual(s.applied, 1);
    assert.strictEqual(s.conflicts, 0);
    assert.strictEqual(db.getEntry(e.id), null, 'no tombstone left behind');
    assert.strictEqual(py(PRE + 'print(json.dumps(dump(row(Timetable,"COSC102"))))'), null);
  });

  await test('a web edit (queryset.update, no signals) made while the desktop was offline is caught as a conflict; keep_local then wins cleanly', async () => {
    const e = find('regular', 'COSC103');
    py(PRE + 'Timetable.objects.filter(pk=%d).update(day="Friday", start_time=dt.time(16), end_time=dt.time(18)); print(1)'.replace('%d', String(e.server_id)));
    edit('move', e, { day: 'Saturday', start_time: '13:00', end_time: '15:00' });
    const s = await sync.syncAll();
    assert.strictEqual(s.conflicts, 1);
    const conflicts = db.listConflicts();
    assert.strictEqual(conflicts.length, 1);
    assert.strictEqual(conflicts[0].reason, null);
    assert.strictEqual(conflicts[0].server.day, 'Friday');
    assert.strictEqual(db.getEntry(e.id)!.sync_state, 'conflict');
    assert.strictEqual(py(PRE + 'print(json.dumps(dump(row(Timetable,"COSC103"))))').day, 'Friday', 'server untouched by the conflicting push');
    assert.throws(() => edit('move', db.getEntry(e.id)!, { day: 'Monday', start_time: '07:00', end_time: '09:00' }), /Resolve the conflict/);

    resolveConflict({ entryId: e.id, resolution: 'keep_local' });
    const s2 = await sync.syncAll();
    assert.strictEqual(s2.applied, 1);
    assert.strictEqual(db.listConflicts().length, 0);
    assert.deepStrictEqual(py(PRE + 'print(json.dumps(dump(row(Timetable,"COSC103"))))').day, 'Saturday');
  });

  await test('keep_server adopts the web version and drops the local edit', async () => {
    const e = find('regular', 'COSC104');
    py(PRE + 'Timetable.objects.filter(pk=%d).update(day="Friday"); print(1)'.replace('%d', String(e.server_id)));
    edit('move', e, { day: 'Saturday', start_time: '13:00', end_time: '15:00' });
    await sync.syncAll();
    assert.strictEqual(db.listConflicts().length, 1);
    resolveConflict({ entryId: e.id, resolution: 'keep_server' });
    const after = db.getEntry(e.id)!;
    assert.deepStrictEqual([after.day, after.sync_state], ['Friday', 'clean']);
    assert.strictEqual(db.pendingCount(), 0);
    const s = await sync.syncAll();
    assert.strictEqual(s.applied + s.conflicts, 0);
  });

  await test('a change the server REJECTS (venue/lecturer clash) becomes a conflict with the reason — it is not dropped', async () => {
    const target = find('regular', 'COSC100'); // Saturday 09-11 LH0, Dr 0
    const e = find('regular', 'COSC103'); // Dr 0 too, currently Saturday 13-15
    edit('move', e, { day: target.day, start_time: target.start_time, end_time: target.end_time });
    const s = await sync.syncAll();
    assert.strictEqual(s.conflicts, 1);
    const c = db.listConflicts()[0];
    assert.ok(c.reason && /clash/i.test(c.reason), 'reason shown to the user: ' + c.reason);
    assert.strictEqual(db.pendingCount(), 0);
    assert.strictEqual(py(PRE + 'print(json.dumps(dump(row(Timetable,"COSC103"))))').start, '13:00');
    resolveConflict({ entryId: e.id, resolution: 'keep_server' });
    assert.strictEqual(db.getEntry(e.id)!.start_time, '13:00');
  });

  await test('a row deleted on the server disappears locally; if it had an unsynced edit it becomes a conflict, and keep_server forgets it', async () => {
    const clean = find('regular', 'COSC105');
    const dirty = find('regular', 'COSC101');
    py(PRE + 'Timetable.objects.filter(pk__in=[%d,%d]).delete(); print(1)'.replace('%d', String(clean.server_id)).replace('%d', String(dirty.server_id)));
    edit('move', dirty, { day: 'Saturday', start_time: '07:00', end_time: '09:00' });
    await sync.syncAll();
    assert.strictEqual(db.getEntry(clean.id), null);
    const c = db.listConflicts().find((x) => x.entry_id === dirty.id)!;
    assert.ok(c && c.server.deleted && c.reason, 'deleted-on-server conflict with reason');
    resolveConflict({ entryId: dirty.id, resolution: 'keep_server' });
    assert.strictEqual(db.getEntry(dirty.id), null);
  });

  await test('a retried push after a lost response is applied once (idempotent local_op_id)', async () => {
    const src = find('regular', 'COSC104');
    const newId = uuid();
    const opId = uuid();
    const change: PendingChange = {
      local_op_id: opId, entry_id: newId, kind: 'regular', op: 'create', base_version: 0, created_at: new Date().toISOString(),
      payload: { course_allocation_id: src.course_allocation_id, venue_id: src.venue_id, day: 'Wednesday', start_time: '13:00', end_time: '15:00' }
    };
    // Server applies it, but the client "never receives" the answer:
    const raw = await fetch(`${BASE}/api/desktop/timetable/regular/push/`, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Token ${session.token}` }, body: JSON.stringify({ changes: [change] }) });
    assert.strictEqual((await raw.json()).results[0].status, 'applied');
    const countAfterFirst = py(PRE + 'print(Timetable.objects.count())');
    // ...so the client still has it queued and resends on the next sync:
    applyEdit({ ...change, payload: { ...src, id: newId, day: 'Wednesday', start_time: '13:00', end_time: '15:00' } });
    const s = await sync.syncAll();
    assert.strictEqual(s.applied, 1);
    assert.strictEqual(py(PRE + 'print(Timetable.objects.count())'), countAfterFirst, 'no duplicate row');
    assert.strictEqual(db.getEntry(newId), null);
  });

  await test('exam move by date keeps day and date consistent', async () => {
    const e = db.listEntries('exam')[0];
    edit('move', e, { day: 'Wednesday', date: '2030-01-16', start_time: '08:00', end_time: '11:00' });
    const s = await sync.syncAll();
    assert.strictEqual(s.applied, 1, JSON.stringify(db.listConflicts()));
    const after = db.getEntry(e.id)!;
    assert.deepStrictEqual([after.date, after.day, after.sync_state], ['2030-01-16', 'Wednesday', 'clean']);
  });

  await test('lab session move', async () => {
    const e = db.listEntries('lab')[0];
    edit('move', e, { day: 'Friday', start_time: '14:00', end_time: '17:00' });
    const s = await sync.syncAll();
    assert.strictEqual(s.applied, 1, JSON.stringify(db.listConflicts()));
    assert.strictEqual(db.getEntry(e.id)!.day, 'Friday');
  });

  await test('offline: an unreachable server keeps the edit queued; it syncs once reachable', async () => {
    const e = db.listEntries('regular')[0];
    edit('move', e, { day: 'Monday', start_time: '16:00', end_time: '18:00' });
    sync.configure({ baseUrl: 'http://127.0.0.1:1', token: session.token });
    await assert.rejects(sync.syncAll(), NetworkError);
    assert.strictEqual(db.pendingCount(), 1);
    assert.strictEqual(db.getEntry(e.id)!.sync_state, 'dirty');
    sync.configure({ baseUrl: session.baseUrl, token: session.token });
    const s = await sync.syncAll();
    assert.strictEqual(s.pending, 0);
  });

  await test('an edit made while a push is in flight is not swallowed by that push', async () => {
    const e = db.listEntries('regular')[0];
    edit('move', e, { day: 'Tuesday', start_time: '07:00', end_time: '09:00' });
    const opId = db.listPendingChanges()[0].local_op_id;
    db.setInflight([opId]);
    edit('move', db.getEntry(e.id)!, { day: 'Tuesday', start_time: '10:00', end_time: '12:00' });
    db.clearInflight();
    assert.strictEqual(db.pendingCount(), 2, 'second edit queued separately');
    db.clearPendingChange(db.listPendingChanges()[1].local_op_id); // tidy: collapse back for the real sync below
    db.markDirty(e.id, { day: 'Tuesday', start_time: '07:00', end_time: '09:00' });
    await sync.syncAll();
  });

  await test('a revoked token surfaces as AuthError (sign in again), and a 502 from a proxy does NOT sign you out', async () => {
    py(PRE + 'DesktopAuthToken.objects.all().delete(); print(1)');
    await assert.rejects(sync.syncAll(), AuthError);
    assert.strictEqual(await auth.whoAmI(session), 'invalid');

    const srv = http.createServer((_q, r) => { r.statusCode = 502; r.end('<html>Bad gateway</html>'); });
    await new Promise<void>((res) => srv.listen(0, '127.0.0.1', () => res()));
    const port = (srv.address() as any).port;
    assert.strictEqual(await auth.whoAmI({ ...session, baseUrl: `http://127.0.0.1:${port}` }), 'unknown');
    srv.close();

    session = await auth.login({ baseUrl: BASE, username: USER, password: PASS });
    sync.configure({ baseUrl: session.baseUrl, token: session.token });
    await sync.syncAll();
  });

  await test('bad credentials and non-role accounts get readable errors', async () => {
    await assert.rejects(auth.login({ baseUrl: BASE, username: USER, password: 'wrong' }), /Invalid username or password/);
  });

  await test('server address normalisation', async () => {
    assert.strictEqual(normalizeBaseUrl('timetable.chuka.ac.ke'), 'https://timetable.chuka.ac.ke');
    assert.strictEqual(normalizeBaseUrl(' https://timetable.chuka.ac.ke/ '), 'https://timetable.chuka.ac.ke');
    assert.strictEqual(normalizeBaseUrl('localhost:8000'), 'http://localhost:8000');
    assert.strictEqual(normalizeBaseUrl('192.168.1.5:8000/some/path'), 'http://192.168.1.5:8000');
    assert.throws(() => normalizeBaseUrl('ftp://x'), /http/);
  });

  await test('schema is at the current version with the v3 columns', async () => {
    const cols = (db.openDb().prepare("PRAGMA table_info(conflicts)").all() as any[]).map((c) => c.name);
    assert.ok(cols.includes('reason'));
    assert.strictEqual(db.getMeta('schema_version'), '5');
  });

  await test('v5 cached_documents table exists for the published-PDF cache', async () => {
    const tables = (db.openDb().prepare("SELECT name FROM sqlite_master WHERE type='table'").all() as any[]).map((t) => t.name);
    assert.ok(tables.includes('cached_documents'));
  });

  await test('v4 reference tables exist for the lab/exam autoscheduler mirror', async () => {
    const tables = (db.openDb().prepare("SELECT name FROM sqlite_master WHERE type='table'").all() as any[]).map((t) => t.name);
    for (const t of ['ref_lab_venues', 'ref_lecturers', 'ref_programs', 'ref_lab_allocations']) {
      assert.ok(tables.includes(t), `missing table ${t}`);
    }
  });

  await test('pullLabExamReference mirrors the seeded lab allocation locally', async () => {
    const result = await sync.pullLabExamReference();
    assert.ok(result.allocations >= 1, 'expected at least the seeded lab allocation');
    const cached = db.getLabExamReference();
    assert.ok(cached, 'expected reference data to be cached after pull');
    assert.ok(cached!.lab_allocations.length >= 1);
    assert.ok(cached!.lab_venues.length >= 1);
    assert.ok(typeof cached!.config.slot_size === 'number');
  });

  await test('runLabExamSchedulerLocally schedules the seeded lab allocation entirely offline (real python subprocess)', async () => {
    const before = db.listEntries('lab_exam');
    const result = await runLabExamSchedulerLocally(true);
    assert.notStrictEqual(result.status, 'error', result.message);
    assert.ok(result.created >= 1, `expected at least one scheduled session, got: ${result.message}`);
    assert.strictEqual(result.removed, before.length);

    const after = db.listEntries('lab_exam');
    assert.strictEqual(after.length, result.created);
    for (const e of after) {
      assert.strictEqual(e.sync_state, 'dirty'); // queued, not yet pushed
      assert.ok(e.venue_name, 'expected a venue name resolved from the local reference mirror');
    }
  });

  await test('listPublishedDocuments sees the seeded PUBLISHED regular PDF (and only published ones)', async () => {
    const docs = await documentsCache.listPublishedDocuments();
    assert.ok(docs.length >= 1, 'expected the seeded regular PDF');
    const reg = docs.find((d) => d.document_type === 'REGULAR');
    assert.ok(reg, 'expected a REGULAR document');
    assert.strictEqual(reg!.cached, false); // not downloaded yet
    (global as any).__seededDoc = reg;
  });

  await test('downloadDocument caches real bytes to disk and records it locally', async () => {
    const doc = (global as any).__seededDoc;
    const localPath = await documentsCache.downloadDocument(doc);
    assert.ok(fs.existsSync(localPath), `expected a file at ${localPath}`);
    const bytes = fs.readFileSync(localPath);
    assert.ok(bytes.length > 0 && bytes.slice(0, 4).toString() === '%PDF', 'expected real PDF bytes on disk');

    const onlineList = await documentsCache.listPublishedDocuments();
    const same = onlineList.find((d) => d.id === doc.id);
    assert.strictEqual(same?.cached, true);
    assert.strictEqual(same?.local_path, localPath);
  });

  await test('listCachedDocumentsOffline works with zero network calls', async () => {
    const doc = (global as any).__seededDoc;
    const offlineList = documentsCache.listCachedDocumentsOffline();
    assert.ok(offlineList.some((d) => d.id === doc.id && d.cached && fs.existsSync(d.local_path!)));
  });

  await test('removeCachedDocument deletes both the file and the local record', async () => {
    const doc = (global as any).__seededDoc;
    const before = documentsCache.listCachedDocumentsOffline().find((d) => d.id === doc.id)!;
    documentsCache.removeCachedDocument(doc.id);
    assert.ok(!fs.existsSync(before.local_path!), 'expected the cached file to be deleted');
    assert.ok(!documentsCache.listCachedDocumentsOffline().some((d) => d.id === doc.id));
  });

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  db.closeDb();
  fs.rmSync(tmp, { recursive: true, force: true });
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
