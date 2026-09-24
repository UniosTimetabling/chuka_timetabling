import fs from 'fs';
import path from 'path';
import { app } from 'electron';
import * as db from './db';
import * as sync from './syncEngine';
import { fetchBinary, AuthError } from './http';
import type { CachedPdfDoc, PublishedPdfDoc } from '../shared/types';

function cacheDir(): string {
  // CHUKA_PDF_CACHE_DIR lets QA / automated tests point at a throwaway folder without a real
  // Electron window (electron.app isn't available under ELECTRON_RUN_AS_NODE) — same pattern as
  // db.ts's CHUKA_DB_PATH.
  const dir = process.env.CHUKA_PDF_CACHE_DIR || path.join(app.getPath('userData'), 'cached-pdfs');
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

/**
 * Uses syncEngine's already-configured session (set by the same login flow that configures
 * push/pull) rather than re-reading db.getSession() directly, so this always agrees with
 * whatever server/token the rest of the app is currently using.
 */
function requireConfig(): sync.ServerConfig {
  const cfg = sync.getConfig();
  if (!cfg) throw new Error('Not signed in.');
  return cfg;
}

/** Lists what's published on the server, merged with this device's local cache state. */
export async function listPublishedDocuments(): Promise<CachedPdfDoc[]> {
  const { baseUrl, token } = requireConfig();
  const res = await fetch(`${baseUrl}/api/desktop/published-pdfs/`, { headers: { Authorization: `Token ${token}` } });
  if (res.status === 401 || res.status === 403) throw new AuthError('Your session is no longer valid.');
  if (!res.ok) throw new Error(`Could not list published documents (HTTP ${res.status}).`);
  const data: { documents: PublishedPdfDoc[] } = await res.json();
  return data.documents.map((d) => {
    const cached = db.getCachedDocument(d.id);
    return { ...d, cached: !!cached, local_path: cached?.local_path ?? null, cached_at: null };
  });
}

/** Same list, but from the local cache only — works fully offline. */
export function listCachedDocumentsOffline(): CachedPdfDoc[] {
  return db.listCachedDocuments();
}

/** Downloads one document's PDF bytes and caches them on disk. Requires a connection. */
export async function downloadDocument(doc: PublishedPdfDoc): Promise<string> {
  const { baseUrl, token } = requireConfig();

  const res = await fetchBinary(`${baseUrl}/api/desktop/published-pdfs/${doc.id}/file/`, {
    headers: { Authorization: `Token ${token}` }
  });
  if (res.status === 401 || res.status === 403) throw new AuthError('Your session is no longer valid.');
  if (!res.ok || !res.buffer) throw new Error(`Could not download that document (HTTP ${res.status}) — it may no longer be published.`);

  const safeName = doc.title.replace(/[^\w.-]+/g, '_').slice(0, 80);
  const localPath = path.join(cacheDir(), `${doc.id}-${safeName}.pdf`);
  fs.writeFileSync(localPath, res.buffer);

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

export function removeCachedDocument(id: number) {
  const cached = db.getCachedDocument(id);
  if (cached && fs.existsSync(cached.local_path)) {
    try {
      fs.unlinkSync(cached.local_path);
    } catch {
      /* best-effort — the metadata row is removed either way */
    }
  }
  db.removeCachedDocument(id);
}
