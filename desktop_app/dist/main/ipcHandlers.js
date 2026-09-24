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
Object.defineProperty(exports, "__esModule", { value: true });
exports.runSync = runSync;
exports.startBackgroundSync = startBackgroundSync;
exports.registerIpcHandlers = registerIpcHandlers;
const electron_1 = require("electron");
const db = __importStar(require("./db"));
const sync = __importStar(require("./syncEngine"));
const auth = __importStar(require("./authClient"));
const pdfExport_1 = require("./pdfExport");
const editOps_1 = require("./editOps");
const labExamScheduler_1 = require("./labExamScheduler");
const documentsCache = __importStar(require("./documentsCache"));
const http_1 = require("./http");
/** Last status broadcast, so a window that mounts AFTER a background sync finished can still show it. */
let lastStatus = 'idle';
function broadcastStatus(status) {
    lastStatus = status;
    for (const w of electron_1.BrowserWindow.getAllWindows())
        w.webContents.send('sync:status', status);
}
// ---- Sync runner (one at a time; also used by the background timer) --------
let running = null;
/**
 * Push + pull everything. Concurrent callers share the in-flight run: two overlapping
 * pushes would send the same queued changes twice.
 */
function runSync() {
    if (running)
        return running;
    broadcastStatus('syncing');
    running = (async () => {
        try {
            const summary = await sync.syncAll();
            broadcastStatus(db.listConflicts().length > 0 ? 'conflicts' : 'synced');
            return summary;
        }
        catch (err) {
            if (err instanceof http_1.AuthError) {
                db.clearSession();
                broadcastStatus('auth-expired');
            }
            else if (err instanceof http_1.NetworkError) {
                broadcastStatus('offline');
            }
            else {
                broadcastStatus('error');
            }
            throw err;
        }
        finally {
            running = null;
        }
    })();
    return running;
}
let timer = null;
/** Background sync: shortly after start, then every `everyMs`. Failures (offline...) are silent; the status bar shows them. */
function startBackgroundSync(everyMs = 120000) {
    if (timer)
        return;
    const tick = () => {
        if (db.getSession() && sync.isConfigured())
            runSync().catch(() => undefined);
    };
    setTimeout(tick, 3000);
    timer = setInterval(tick, everyMs);
}
function registerIpcHandlers() {
    electron_1.ipcMain.handle('entries:list', (_e, kind) => db.listEntries(kind));
    electron_1.ipcMain.handle('entries:listAll', () => db.listAllEntries());
    // Every local edit (move, drag-drop, cut/paste, delete) funnels through here — see editOps.applyEdit.
    electron_1.ipcMain.handle('entries:edit', (_e, change) => (0, editOps_1.applyEdit)(change));
    electron_1.ipcMain.handle('conflicts:list', () => db.listConflicts());
    electron_1.ipcMain.handle('sync:info', () => ({ pending: db.pendingCount(), conflicts: db.listConflicts().length, status: lastStatus }));
    // Resolution modes offered in the Conflict modal — see editOps.resolveConflict.
    electron_1.ipcMain.handle('conflicts:resolve', (_e, args) => (0, editOps_1.resolveConflict)(args));
    electron_1.ipcMain.handle('sync:configure', (_e, args) => {
        sync.configure(args);
    });
    // ---- Auth -----------------------------------------------------------
    electron_1.ipcMain.handle('auth:login', async (_e, args) => {
        const session = await auth.login(args);
        // The cache mirrors ONE server. Signing in to a different one must not mix datasets, and must not
        // discard edits that were never pushed.
        const prev = db.getMeta('data_origin');
        if (prev && prev !== session.baseUrl) {
            if (db.pendingCount() > 0 || db.listConflicts().length > 0) {
                await auth.logout(session);
                throw new Error(`This device still holds unsynced changes made against ${prev}. Sign in to that server and sync them first, or they would be lost.`);
            }
            db.wipeData();
        }
        db.setMeta('data_origin', session.baseUrl);
        db.saveSession(session);
        sync.configure({ baseUrl: session.baseUrl, token: session.token });
        runSync().catch(() => undefined); // initial pull in the background; the status bar reports the outcome
        return session;
    });
    electron_1.ipcMain.handle('auth:logout', async () => {
        const session = db.getSession();
        if (session)
            await auth.logout(session);
        db.clearSession();
    });
    // Called once on app startup: returns a saved session (if any) after confirming, best-effort,
    // that the server hasn't revoked it. Offline / server errors keep the session (work offline).
    electron_1.ipcMain.handle('auth:getSession', async () => {
        const session = db.getSession();
        if (!session)
            return null;
        if ((await auth.whoAmI(session)) === 'invalid') {
            db.clearSession();
            return null;
        }
        sync.configure({ baseUrl: session.baseUrl, token: session.token });
        return session;
    });
    electron_1.ipcMain.handle('sync:run', async (_e, _kind) => runSync());
    electron_1.ipcMain.handle('reference:labExam', async () => sync.pullLabExamReference().then(() => db.getLabExamReference()));
    electron_1.ipcMain.handle('reference:labExamCached', async () => db.getLabExamReference());
    electron_1.ipcMain.handle('scheduler:runLabExam', async (_e, freshData) => (0, labExamScheduler_1.runLabExamSchedulerLocally)(freshData ?? true));
    electron_1.ipcMain.handle('documents:list', async () => documentsCache.listPublishedDocuments());
    electron_1.ipcMain.handle('documents:listCachedOffline', async () => documentsCache.listCachedDocumentsOffline());
    electron_1.ipcMain.handle('documents:download', async (_e, doc) => documentsCache.downloadDocument(doc));
    electron_1.ipcMain.handle('documents:openCached', async (_e, localPath) => electron_1.shell.openPath(localPath));
    electron_1.ipcMain.handle('documents:removeCached', async (_e, id) => documentsCache.removeCachedDocument(id));
    electron_1.ipcMain.handle('pdf:export', (_e, args) => (0, pdfExport_1.exportHtmlToPdf)(args.html, args.suggestedFileName));
}
