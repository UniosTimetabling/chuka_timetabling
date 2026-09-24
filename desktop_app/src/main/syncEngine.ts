import * as db from './db';
import { EntryKind, LabExamReferenceData, PendingChange, PullResponse, PushResultItem, SyncSummary, TimetableEntry } from '../shared/types';
import { AuthError, fetchJson, HttpError } from './http';

/**
 * Sync protocol (server side: /desktop_sync in the Django project):
 *
 *  PULL  GET  /api/desktop/timetable/<kind>/
 *        -> { server_version, entries[] }            (FULL snapshot of the kind)
 *        The client diffs the snapshot against its cache to find rows the
 *        server no longer has, so no tombstones are needed.
 *
 *  PUSH  POST /api/desktop/timetable/<kind>/push/
 *        body: { changes: PendingChange[] }
 *        -> { results: PushResultItem[] }
 *        status "applied" | "conflict" (row changed on the server since the
 *        client's base_version; nothing written) | "error" (rejected: clash,
 *        group lock, bad venue...). Conflicts AND rejections are kept as
 *        conflict records for the user to decide; nothing is dropped silently.
 *
 * `version` is a content hash computed by the server on read, so a row edited
 * on the web (or by the autoscheduler) always shows up as a new version.
 */

export const ALL_KINDS: EntryKind[] = ['regular', 'exam', 'lab', 'lab_exam'];

export interface ServerConfig {
  baseUrl: string;   // e.g. https://timetable.chuka.ac.ke (already normalised)
  token: string;
}

let cfg: ServerConfig | null = null;
export function configure(c: ServerConfig) {
  cfg = c;
}
export function isConfigured(): boolean {
  return cfg !== null;
}
/** The currently active server config (set by configure()) — null if not signed in this session. */
export function getConfig(): ServerConfig | null {
  return cfg;
}

async function api(path: string, init: RequestInit = {}, timeoutMs = 60_000): Promise<any> {
  if (!cfg) throw new Error('Server not configured. Sign in first.');
  const res = await fetchJson(
    cfg.baseUrl + path,
    { ...init, headers: { 'Content-Type': 'application/json', Authorization: `Token ${cfg.token}`, ...(init.headers || {}) } },
    timeoutMs
  );
  if (res.status === 401 || (res.status === 403 && res.data?.error)) {
    throw new AuthError(res.data?.error || 'Your session is no longer valid. Please sign in again.');
  }
  if (!res.ok || res.data === null) {
    throw new HttpError(res.status, res.data?.error || `The server answered HTTP ${res.status}.`);
  }
  return res.data;
}

/** The server's view of a row that no longer exists there. */
function tombstone(local: TimetableEntry): TimetableEntry {
  return { ...local, deleted: true, sync_state: 'clean' };
}

/** Pull a full snapshot and reconcile it into the local cache without clobbering unsynced local edits. */
export async function pull(kind: EntryKind): Promise<{ pulled: number; conflicts: number }> {
  const resp: PullResponse = await api(`/api/desktop/timetable/${kind}/`);
  let conflicts = 0;

  db.runInTransaction(() => {
    const localById = new Map(db.listEntriesRaw(kind).map((e) => [e.id, e]));
    const serverIds = new Set<number>();

    for (const remote of resp.entries) {
      serverIds.add(remote.server_id as number);
      const existing = localById.get(remote.id);

      if (!existing || existing.sync_state === 'clean') {
        db.upsertEntry({ ...remote, deleted: false, sync_state: 'clean', base_version: remote.version });
        continue;
      }

      // The user has an unsynced edit on this row.
      if (remote.version === existing.base_version) continue; // server unchanged since our edit's base: it'll push normally

      if (existing.sync_state === 'conflict') {
        // Already awaiting a decision; make sure the decision is made against the LATEST server row.
        const c = db.getConflict(existing.id);
        if (c) db.saveConflict(existing.id, kind, c.local, remote, c.base_version, c.reason);
        continue;
      }

      db.dropPendingForEntry(existing.id); // the conflict record now carries the local intent
      db.saveConflict(existing.id, kind, existing, remote, existing.base_version);
      conflicts++;
    }

    // Anything we cached from the server that the snapshot no longer contains was deleted (or de-scoped) there.
    for (const e of localById.values()) {
      if (e.server_id == null || serverIds.has(e.server_id)) continue;
      if (e.sync_state === 'clean' || e.deleted) {
        db.dropPendingForEntry(e.id);
        db.purgeEntry(e.id);
      } else if (e.sync_state === 'dirty') {
        db.dropPendingForEntry(e.id);
        db.saveConflict(e.id, kind, e, tombstone(e), e.base_version, 'This entry was deleted on the server.');
        conflicts++;
      }
    }
  });

  return { pulled: resp.entries.length, conflicts };
}

function recordProblem(kind: EntryKind, change: PendingChange, r: PushResultItem): boolean {
  const local = db.getEntry(change.entry_id);
  if (!local) return false;
  const serverRow = r.server_entry ?? tombstone(local);
  const reason = r.status === 'error' ? r.message || 'Rejected by the server.' : r.server_entry ? null : r.message || 'This entry was deleted on the server.';
  db.dropPendingForEntry(change.entry_id); // the conflict record carries the local intent now
  db.saveConflict(change.entry_id, kind, local, serverRow, change.base_version, reason);
  return true;
}

/** Apply one server verdict to the local cache + queue. Returns 'applied' | 'conflict'. */
function handleResult(kind: EntryKind, change: PendingChange, r: PushResultItem): 'applied' | 'conflict' | 'skipped' {
  db.clearPendingChange(change.local_op_id);

  if (r.status === 'applied') {
    const se = r.server_entry;
    if (change.op === 'delete' || !se) {
      db.purgeEntry(change.entry_id); // deleted (by us), or the row is gone server-side
      return 'applied';
    }
    const local = db.getEntry(change.entry_id);
    const editedMeanwhile = local && db.hasPending(change.entry_id);
    if (se.id !== change.entry_id) {
      // A row created offline just got its real server id: replace the temporary local row.
      db.rewirePending(change.entry_id, se.id, se.version);
      db.purgeEntry(change.entry_id);
      const base: Partial<TimetableEntry> = editedMeanwhile && local ? { ...local, id: se.id, server_id: se.server_id } : se;
      db.upsertEntry({ ...(base as TimetableEntry), version: se.version, base_version: se.version, sync_state: editedMeanwhile ? 'dirty' : 'clean' });
    } else if (editedMeanwhile && local) {
      // The user edited the row again while this push was in flight: keep their newer values, rebase them.
      db.rebasePending(se.id, se.version);
      db.upsertEntry({ ...local, version: se.version, base_version: se.version, sync_state: 'dirty' });
    } else {
      db.upsertEntry({ ...se, deleted: false, sync_state: 'clean', base_version: se.version });
    }
    return 'applied';
  }

  return recordProblem(kind, change, r) ? 'conflict' : 'skipped';
}

/** Push every queued local change. Nothing is auto-resolved and nothing is silently dropped. */
export async function push(): Promise<{ applied: number; conflicts: number }> {
  const changes = db.listPendingChanges();
  let applied = 0;
  let conflicts = 0;
  if (changes.length === 0) return { applied, conflicts };

  db.setInflight(changes.map((c) => c.local_op_id));
  try {
    for (const kind of ALL_KINDS) {
      const batch = changes.filter((c) => c.kind === kind);
      if (batch.length === 0) continue;
      const data: { results: PushResultItem[] } = await api(`/api/desktop/timetable/${kind}/push/`, {
        method: 'POST',
        body: JSON.stringify({ changes: batch })
      });
      const byOp = new Map(data.results.map((r) => [r.local_op_id, r]));
      db.runInTransaction(() => {
        for (const change of batch) {
          const r = byOp.get(change.local_op_id);
          if (!r) continue; // server gave no verdict: leave it queued for the next sync
          const outcome = handleResult(kind, change, r);
          if (outcome === 'applied') applied++;
          else if (outcome === 'conflict') conflicts++;
        }
      });
    }
  } finally {
    db.clearInflight();
  }
  return { applied, conflicts };
}

/**
 * Pulls everything the lab/exam autoscheduler needs as INPUT (allocations, venues, lecturers,
 * programs, scheduler config) and replaces the local read-only mirror wholesale — this data is
 * never edited on the desktop, so there's nothing to reconcile the way `pull()` does above.
 */
export async function pullLabExamReference(): Promise<{ allocations: number }> {
  const data: LabExamReferenceData = await api('/api/desktop/reference/lab-exam/');
  db.replaceLabExamReference(data);
  return { allocations: data.lab_allocations.length };
}

/** Push everything queued, then refresh every kind from the server. */
export async function syncAll(): Promise<SyncSummary> {
  const p = await push();
  let pulled = 0;
  let conflicts = p.conflicts;
  for (const kind of ALL_KINDS) {
    const r = await pull(kind);
    pulled += r.pulled;
    conflicts += r.conflicts;
  }
  try {
    await pullLabExamReference();
  } catch (err) {
    // Reference data is used by a panel that isn't built yet — don't fail the whole sync over it.
    console.error('pullLabExamReference failed:', err);
  }
  return { applied: p.applied, conflicts, pulled, pending: db.pendingCount() };
}
