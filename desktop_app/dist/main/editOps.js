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
exports.applyEdit = applyEdit;
exports.resolveConflict = resolveConflict;
const uuid_1 = require("uuid");
const db = __importStar(require("./db"));
/**
 * Local edit pipeline, kept free of Electron so it can be exercised by the
 * headless sync test (src/main/tests/syncE2E.ts).
 *
 * Applies an edit to the local cache immediately (offline-first UI) and queues
 * it for the next push. The queue keeps at most one unsent change per entry
 * (see db.queueChange).
 */
function applyEdit(change) {
    const local_op_id = change.local_op_id || (0, uuid_1.v4)();
    const existing = db.getEntry(change.entry_id);
    if (change.op === 'create') {
        db.upsertEntry({
            ...change.payload,
            id: change.entry_id,
            kind: change.kind,
            server_id: null,
            version: 0,
            base_version: 0,
            sync_state: 'dirty',
            deleted: false
        });
        db.queueChange({ ...change, local_op_id, base_version: 0 });
        return;
    }
    if (!existing)
        return; // edit of a row that vanished (e.g. removed by a sync) — nothing to do
    if (existing.sync_state === 'conflict')
        throw new Error('Resolve the conflict on this entry first.');
    // The row's own stored base_version is authoritative (never trust the renderer's copy).
    const queued = { ...change, local_op_id, base_version: existing.base_version };
    if (change.op === 'delete') {
        const outcome = db.queueChange(queued);
        if (outcome === 'cancelled')
            db.purgeEntry(change.entry_id); // never reached the server: just forget it
        else
            db.markDeleted(change.entry_id);
        return;
    }
    db.markDirty(change.entry_id, change.payload);
    db.queueChange(queued);
}
/**
 *  - keep_local:  re-queue the local version against the server's current version.
 *  - keep_server: adopt the server's row (or forget a row that no longer exists there).
 *  - merged:      user hand-picked individual fields -> push that blend.
 */
function resolveConflict(args) {
    const c = db.getConflict(args.entryId);
    if (!c)
        return;
    const serverGone = !!c.server.deleted;
    db.runInTransaction(() => {
        db.dropPendingForEntry(c.entry_id);
        if (args.resolution === 'keep_server') {
            if (serverGone)
                db.purgeEntry(c.entry_id);
            else
                db.upsertEntry({ ...c.server, deleted: false, sync_state: 'clean', base_version: c.server.version });
        }
        else {
            const finalRow = args.resolution === 'merged' && args.merged ? { ...c.local, ...args.merged } : c.local;
            const now = new Date().toISOString();
            if (c.local.deleted && !serverGone) {
                // The user's pending change was a delete: keep it, rebased on the server's current row.
                db.upsertEntry({ ...c.local, deleted: true, sync_state: 'dirty', base_version: c.server.version });
                db.queueChange({ local_op_id: (0, uuid_1.v4)(), entry_id: c.entry_id, kind: c.kind, op: 'delete', payload: {}, base_version: c.server.version, created_at: now });
            }
            else if (serverGone || c.local.server_id == null) {
                // Row doesn't exist on the server (deleted there, or a rejected create): re-create it.
                db.upsertEntry({ ...finalRow, deleted: false, sync_state: 'dirty', base_version: 0 });
                db.queueChange({ local_op_id: (0, uuid_1.v4)(), entry_id: c.entry_id, kind: c.kind, op: 'create', payload: finalRow, base_version: 0, created_at: now });
            }
            else {
                db.upsertEntry({ ...finalRow, deleted: false, sync_state: 'dirty', base_version: c.server.version });
                db.queueChange({ local_op_id: (0, uuid_1.v4)(), entry_id: c.entry_id, kind: c.kind, op: 'update', payload: finalRow, base_version: c.server.version, created_at: now });
            }
        }
        db.resolveConflict(args.entryId);
    });
}
