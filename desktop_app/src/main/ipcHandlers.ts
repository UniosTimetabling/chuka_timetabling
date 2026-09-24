import { ipcMain, BrowserWindow, shell } from 'electron';
import * as db from './db';
import * as sync from './syncEngine';
import * as auth from './authClient';
import { exportHtmlToPdf } from './pdfExport';
import { applyEdit, resolveConflict, ResolveArgs } from './editOps';
import { runLabExamSchedulerLocally } from './labExamScheduler';
import * as documentsCache from './documentsCache';
import { AuthError, NetworkError } from './http';
import { EntryKind, PendingChange, SyncSummary, UserSession } from '../shared/types';

/** Last status broadcast, so a window that mounts AFTER a background sync finished can still show it. */
let lastStatus = 'idle';

function broadcastStatus(status: string) {
  lastStatus = status;
  for (const w of BrowserWindow.getAllWindows()) w.webContents.send('sync:status', status);
}

// ---- Sync runner (one at a time; also used by the background timer) --------

let running: Promise<SyncSummary> | null = null;

/**
 * Push + pull everything. Concurrent callers share the in-flight run: two overlapping
 * pushes would send the same queued changes twice.
 */
export function runSync(): Promise<SyncSummary> {
  if (running) return running;
  broadcastStatus('syncing');
  running = (async () => {
    try {
      const summary = await sync.syncAll();
      broadcastStatus(db.listConflicts().length > 0 ? 'conflicts' : 'synced');
      return summary;
    } catch (err) {
      if (err instanceof AuthError) {
        db.clearSession();
        broadcastStatus('auth-expired');
      } else if (err instanceof NetworkError) {
        broadcastStatus('offline');
      } else {
        broadcastStatus('error');
      }
      throw err;
    } finally {
      running = null;
    }
  })();
  return running;
}

let timer: NodeJS.Timeout | null = null;
/** Background sync: shortly after start, then every `everyMs`. Failures (offline...) are silent; the status bar shows them. */
export function startBackgroundSync(everyMs = 120_000) {
  if (timer) return;
  const tick = () => {
    if (db.getSession() && sync.isConfigured()) runSync().catch(() => undefined);
  };
  setTimeout(tick, 3_000);
  timer = setInterval(tick, everyMs);
}

export function registerIpcHandlers() {
  ipcMain.handle('entries:list', (_e, kind: EntryKind) => db.listEntries(kind));
  ipcMain.handle('entries:listAll', () => db.listAllEntries());

  // Every local edit (move, drag-drop, cut/paste, delete) funnels through here — see editOps.applyEdit.
  ipcMain.handle('entries:edit', (_e, change: PendingChange) => applyEdit(change));

  ipcMain.handle('conflicts:list', () => db.listConflicts());
  ipcMain.handle('sync:info', () => ({ pending: db.pendingCount(), conflicts: db.listConflicts().length, status: lastStatus }));

  // Resolution modes offered in the Conflict modal — see editOps.resolveConflict.
  ipcMain.handle('conflicts:resolve', (_e, args: ResolveArgs) => resolveConflict(args));

  ipcMain.handle('sync:configure', (_e, args: { baseUrl: string; token: string }) => {
    sync.configure(args);
  });

  // ---- Auth -----------------------------------------------------------

  ipcMain.handle('auth:login', async (_e, args: { baseUrl: string; username: string; password: string }) => {
    const session = await auth.login(args);

    // The cache mirrors ONE server. Signing in to a different one must not mix datasets, and must not
    // discard edits that were never pushed.
    const prev = db.getMeta('data_origin');
    if (prev && prev !== session.baseUrl) {
      if (db.pendingCount() > 0 || db.listConflicts().length > 0) {
        await auth.logout(session);
        throw new Error(
          `This device still holds unsynced changes made against ${prev}. Sign in to that server and sync them first, or they would be lost.`
        );
      }
      db.wipeData();
    }
    db.setMeta('data_origin', session.baseUrl);

    db.saveSession(session);
    sync.configure({ baseUrl: session.baseUrl, token: session.token });
    runSync().catch(() => undefined); // initial pull in the background; the status bar reports the outcome
    return session;
  });

  ipcMain.handle('auth:logout', async () => {
    const session = db.getSession();
    if (session) await auth.logout(session);
    db.clearSession();
  });

  // Called once on app startup: returns a saved session (if any) after confirming, best-effort,
  // that the server hasn't revoked it. Offline / server errors keep the session (work offline).
  ipcMain.handle('auth:getSession', async (): Promise<UserSession | null> => {
    const session = db.getSession();
    if (!session) return null;
    if ((await auth.whoAmI(session)) === 'invalid') {
      db.clearSession();
        return null;
    }
    sync.configure({ baseUrl: session.baseUrl, token: session.token });
    return session;
  });

  ipcMain.handle('sync:run', async (_e, _kind?: EntryKind) => runSync());

  ipcMain.handle('reference:labExam', async () => sync.pullLabExamReference().then(() => db.getLabExamReference()));
  ipcMain.handle('reference:labExamCached', async () => db.getLabExamReference());
  ipcMain.handle('scheduler:runLabExam', async (_e, freshData?: boolean) => runLabExamSchedulerLocally(freshData ?? true));

  ipcMain.handle('documents:list', async () => documentsCache.listPublishedDocuments());
  ipcMain.handle('documents:listCachedOffline', async () => documentsCache.listCachedDocumentsOffline());
  ipcMain.handle('documents:download', async (_e, doc) => documentsCache.downloadDocument(doc));
  ipcMain.handle('documents:openCached', async (_e, localPath: string) => shell.openPath(localPath));
  ipcMain.handle('documents:removeCached', async (_e, id: number) => documentsCache.removeCachedDocument(id));

  ipcMain.handle('pdf:export', (_e, args: { html: string; suggestedFileName: string }) =>
    exportHtmlToPdf(args.html, args.suggestedFileName)
  );
}
