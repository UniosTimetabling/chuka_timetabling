import React, { useEffect, useState } from 'react';
import type { CachedPdfDoc } from '@shared/types';

function fmtSize(bytes: number): string {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function PublishedTimetablesPanel() {
  const [docs, setDocs] = useState<CachedPdfDoc[]>([]);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const list = await window.chukaApi.listPublishedDocuments();
      setDocs(list);
      setOffline(false);
    } catch (err: any) {
      // No connection (or session expired) — fall back to whatever's cached, so this
      // still works as "offline viewing", not just "browse when online".
      try {
        const cached = await window.chukaApi.listCachedDocumentsOffline();
        setDocs(cached);
        setOffline(true);
        if (cached.length === 0) setError(err?.message || String(err));
      } catch (err2: any) {
        setError(err2?.message || String(err2));
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleDownload(doc: CachedPdfDoc) {
    setBusyId(doc.id);
    try {
      await window.chukaApi.downloadDocument(doc);
      await load();
    } catch (err: any) {
      setError(err?.message || String(err));
    } finally {
      setBusyId(null);
    }
  }

  async function handleOpen(doc: CachedPdfDoc) {
    if (doc.local_path) await window.chukaApi.openCachedDocument(doc.local_path);
  }

  async function handleRemove(doc: CachedPdfDoc) {
    setBusyId(doc.id);
    try {
      await window.chukaApi.removeCachedDocument(doc.id);
      await load();
    } finally {
      setBusyId(null);
    }
  }

  const byType = { REGULAR: docs.filter((d) => d.document_type === 'REGULAR'), EXAM: docs.filter((d) => d.document_type === 'EXAM') };

  return (
    <div className="tt-panel">
      <div className="tt-panel-header">
        <h2>Published Timetables</h2>
        <p className="tt-hint">
          The latest officially published Regular and Exam timetable PDFs. Download once and they stay available on
          this computer with no connection — the same file that's published on the website, not a rebuilt copy.
        </p>
      </div>

      {offline && (
        <div className="tt-result-banner tt-result-banner--warning">
          No connection — showing what's already cached on this device.
        </div>
      )}
      {error && docs.length === 0 && <div className="tt-result-banner tt-result-banner--error">{error}</div>}

      {loading ? (
        <p className="tt-hint">Loading…</p>
      ) : (
        (['REGULAR', 'EXAM'] as const).map((type) => (
          <div className="tt-settings-card" key={type}>
            <h3>{type === 'REGULAR' ? 'Regular Timetable' : 'Exam Timetable'}</h3>
            {byType[type].length === 0 ? (
              <p className="tt-hint">Nothing published yet.</p>
            ) : (
              <div className="tt-doc-list">
                {byType[type].map((doc) => (
                  <div className="tt-doc-row" key={doc.id}>
                    <div>
                      <div className="tt-doc-title">{doc.title}</div>
                      <div className="tt-hint">
                        {doc.academic_year} · Semester {doc.semester} · v{doc.version} · {fmtSize(doc.file_size)}
                        {doc.cached ? ' · cached for offline use' : ''}
                      </div>
                    </div>
                    <div className="tt-doc-actions">
                      {doc.cached ? (
                        <>
                          <button className="tt-primary-btn" onClick={() => handleOpen(doc)}>
                            Open
                          </button>
                          <button onClick={() => handleRemove(doc)} disabled={busyId === doc.id}>
                            Remove
                          </button>
                        </>
                      ) : (
                        <button className="tt-primary-btn" onClick={() => handleDownload(doc)} disabled={busyId === doc.id || offline}>
                          {busyId === doc.id ? 'Downloading…' : 'Download for offline'}
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))
      )}
    </div>
  );
}
