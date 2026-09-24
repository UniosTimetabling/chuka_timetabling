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
exports.listPublishedDocuments = listPublishedDocuments;
exports.listCachedDocumentsOffline = listCachedDocumentsOffline;
exports.downloadDocument = downloadDocument;
exports.removeCachedDocument = removeCachedDocument;
const fs_1 = __importDefault(require("fs"));
const path_1 = __importDefault(require("path"));
const electron_1 = require("electron");
const db = __importStar(require("./db"));
const sync = __importStar(require("./syncEngine"));
const http_1 = require("./http");
function cacheDir() {
    // CHUKA_PDF_CACHE_DIR lets QA / automated tests point at a throwaway folder without a real
    // Electron window (electron.app isn't available under ELECTRON_RUN_AS_NODE) — same pattern as
    // db.ts's CHUKA_DB_PATH.
    const dir = process.env.CHUKA_PDF_CACHE_DIR || path_1.default.join(electron_1.app.getPath('userData'), 'cached-pdfs');
    fs_1.default.mkdirSync(dir, { recursive: true });
    return dir;
}
/**
 * Uses syncEngine's already-configured session (set by the same login flow that configures
 * push/pull) rather than re-reading db.getSession() directly, so this always agrees with
 * whatever server/token the rest of the app is currently using.
 */
function requireConfig() {
    const cfg = sync.getConfig();
    if (!cfg)
        throw new Error('Not signed in.');
    return cfg;
}
/** Lists what's published on the server, merged with this device's local cache state. */
async function listPublishedDocuments() {
    const { baseUrl, token } = requireConfig();
    const res = await fetch(`${baseUrl}/api/desktop/published-pdfs/`, { headers: { Authorization: `Token ${token}` } });
    if (res.status === 401 || res.status === 403)
        throw new http_1.AuthError('Your session is no longer valid.');
    if (!res.ok)
        throw new Error(`Could not list published documents (HTTP ${res.status}).`);
    const data = await res.json();
    return data.documents.map((d) => {
        const cached = db.getCachedDocument(d.id);
        return { ...d, cached: !!cached, local_path: cached?.local_path ?? null, cached_at: null };
    });
}
/** Same list, but from the local cache only — works fully offline. */
function listCachedDocumentsOffline() {
    return db.listCachedDocuments();
}
/** Downloads one document's PDF bytes and caches them on disk. Requires a connection. */
async function downloadDocument(doc) {
    const { baseUrl, token } = requireConfig();
    const res = await (0, http_1.fetchBinary)(`${baseUrl}/api/desktop/published-pdfs/${doc.id}/file/`, {
        headers: { Authorization: `Token ${token}` }
    });
    if (res.status === 401 || res.status === 403)
        throw new http_1.AuthError('Your session is no longer valid.');
    if (!res.ok || !res.buffer)
        throw new Error(`Could not download that document (HTTP ${res.status}) — it may no longer be published.`);
    const safeName = doc.title.replace(/[^\w.-]+/g, '_').slice(0, 80);
    const localPath = path_1.default.join(cacheDir(), `${doc.id}-${safeName}.pdf`);
    fs_1.default.writeFileSync(localPath, res.buffer);
    db.upsertCachedDocument({
        id: doc.id,
        title: doc.title,
        document_type: doc.document_type,
        academic_year: doc.academic_year,
        semester: doc.semester,
        version: doc.version,
        file_size: doc.file_size,
        uploaded_at: doc.uploaded_at,
        local_path: localPath,
        cached_at: new Date().toISOString()
    });
    return localPath;
}
function removeCachedDocument(id) {
    const cached = db.getCachedDocument(id);
    if (cached && fs_1.default.existsSync(cached.local_path)) {
        try {
            fs_1.default.unlinkSync(cached.local_path);
        }
        catch {
            /* best-effort — the metadata row is removed either way */
        }
    }
    db.removeCachedDocument(id);
}
