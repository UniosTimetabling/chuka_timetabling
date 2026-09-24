import React, { useEffect, useMemo, useState } from 'react';
import { TimetableEntry, EntryKind } from '@shared/types';

/** Names/codes come from the database and go into an HTML string that becomes a PDF: escape them. */
const esc = (v: unknown) =>
  String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string));

const DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const KIND_LABELS: Record<EntryKind, string> = {
  regular: 'Lecture',
  exam: 'Exam',
  lab: 'Lab session',
  lab_exam: 'Lab exam'
};

function reportShell(title: string, generatedFor: string, bodyHtml: string) {
  // Fully self-contained HTML — this is rendered off-screen via a data: URL
  // (see main/pdfExport.ts), so there's no stylesheet or network resource
  // to load; every style has to be inline/in a <style> tag here.
  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
  body { font-family: Helvetica, Arial, sans-serif; padding: 28px; color: #111827; }
  h1 { font-size: 18px; margin: 0 0 2px; }
  .meta { font-size: 11px; color: #6b7280; margin-bottom: 18px; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 18px; }
  th, td { border: 1px solid #d1d5db; padding: 5px 8px; font-size: 11px; text-align: left; }
  th { background: #f3f4f6; }
  h2 { font-size: 13px; margin: 18px 0 6px; }
</style></head>
<body>
  <h1>${esc(title)}</h1>
  <div class="meta">Chuka Timetabling — generated offline on this device, ${esc(generatedFor)}</div>
  ${bodyHtml}
</body></html>`;
}

function entryRow(e: TimetableEntry) {
  return `<tr>
    <td>${KIND_LABELS[e.kind]}</td>
    <td>${esc(e.course_code)}</td>
    <td>${esc(e.day)}${e.date ? ' · ' + esc(e.date) : ''}</td>
    <td>${e.start_time}–${e.end_time}</td>
    <td>${esc(e.venue_name)}${e.venue_kind === 'lab' ? ' (Lab)' : ''}</td>
    <td>${esc(e.program_name)}</td>
  </tr>`;
}

export default function AnalysisPanel() {
  const [entries, setEntries] = useState<TimetableEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [lecturer, setLecturer] = useState<string>('');
  const [exporting, setExporting] = useState(false);
  const [exportedPath, setExportedPath] = useState<string | null>(null);

  useEffect(() => {
    window.chukaApi
      .listAllEntries()
      .then(setEntries)
      .finally(() => setLoading(false));
  }, []);

  const kindCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const e of entries) counts[e.kind] = (counts[e.kind] || 0) + 1;
    return counts;
  }, [entries]);

  const venueUtilization = useMemo(() => {
    const map = new Map<string, { name: string; kind: string; count: number }>();
    for (const e of entries) {
      const key = `${e.venue_kind}:${e.venue_name}`;
      const existing = map.get(key);
      if (existing) existing.count++;
      else map.set(key, { name: e.venue_name, kind: e.venue_kind, count: 1 });
    }
    return [...map.values()].sort((a, b) => b.count - a.count);
  }, [entries]);

  const lecturers = useMemo(() => {
    const set = new Set(entries.map((e) => e.lecturer_name).filter((n) => n && n !== 'Unassigned'));
    return [...set].sort();
  }, [entries]);

  const lecturerEntries = useMemo(
    () => entries.filter((e) => e.lecturer_name === lecturer).sort((a, b) => (a.date || '').localeCompare(b.date || '') || DAY_ORDER.indexOf(a.day) - DAY_ORDER.indexOf(b.day) || a.start_time.localeCompare(b.start_time)),
    [entries, lecturer]
  );

  const generatedFor = new Date().toLocaleString();

  async function exportSummaryPdf() {
    setExporting(true);
    setExportedPath(null);
    try {
      const body = `
        <h2>Entries by type</h2>
        <table><thead><tr><th>Type</th><th>Count</th></tr></thead><tbody>
          ${Object.entries(kindCounts)
            .map(([k, c]) => `<tr><td>${KIND_LABELS[k as EntryKind] || k}</td><td>${c}</td></tr>`)
            .join('')}
        </tbody></table>
        <h2>Venue utilization</h2>
        <table><thead><tr><th>Venue</th><th>Type</th><th>Sessions scheduled</th></tr></thead><tbody>
          ${venueUtilization
            .map((v) => `<tr><td>${esc(v.name)}</td><td>${v.kind === 'lab' ? 'Lab' : 'Hall'}</td><td>${v.count}</td></tr>`)
            .join('')}
        </tbody></table>`;
      const html = reportShell('Timetable Analysis Summary', generatedFor, body);
      const path = await window.chukaApi.exportPdf(html, 'timetable-analysis-summary.pdf');
      setExportedPath(path);
    } finally {
      setExporting(false);
    }
  }

  async function exportLecturerPdf() {
    if (!lecturer) return;
    setExporting(true);
    setExportedPath(null);
    try {
      const body = `
        <h2>${esc(lecturer)}'s schedule</h2>
        <table><thead><tr><th>Type</th><th>Course</th><th>Day</th><th>Time</th><th>Venue</th><th>Program</th></tr></thead>
        <tbody>${lecturerEntries.map(entryRow).join('')}</tbody></table>`;
      const html = reportShell(`Lecturer Schedule — ${lecturer}`, generatedFor, body);
      const safeName = lecturer.replace(/[^a-z0-9]+/gi, '-').toLowerCase();
      const path = await window.chukaApi.exportPdf(html, `lecturer-schedule-${safeName}.pdf`);
      setExportedPath(path);
    } finally {
      setExporting(false);
    }
  }

  if (loading) {
    return (
      <div className="tt-panel">
        <p className="tt-hint">Loading cached timetable data…</p>
      </div>
    );
  }

  return (
    <div className="tt-panel">
      <div className="tt-panel-header">
        <h2>Analysis</h2>
        <p className="tt-hint">
          Computed entirely from the data already cached on this device — works offline, and PDF export happens
          locally in the app (no server round-trip).
        </p>
      </div>

      <div className="tt-settings-card">
        <h3>Entries by type</h3>
        <table className="tt-diff-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Count</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(kindCounts).map(([k, c]) => (
              <tr key={k}>
                <td>{KIND_LABELS[k as EntryKind] || k}</td>
                <td>{c}</td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr>
                <td colSpan={2}>No cached data yet — sync a panel first.</td>
              </tr>
            )}
          </tbody>
        </table>
        <button onClick={exportSummaryPdf} disabled={exporting || entries.length === 0}>
          Export summary as PDF
        </button>
      </div>

      <div className="tt-settings-card">
        <h3>Venue utilization</h3>
        <table className="tt-diff-table">
          <thead>
            <tr>
              <th>Venue</th>
              <th>Type</th>
              <th>Sessions</th>
            </tr>
          </thead>
          <tbody>
            {venueUtilization.slice(0, 15).map((v) => (
              <tr key={v.kind + v.name}>
                <td>{v.name}</td>
                <td>{v.kind === 'lab' ? 'Lab' : 'Hall'}</td>
                <td>{v.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="tt-settings-card">
        <h3>Lecturer schedule</h3>
        <div className="tt-confirm-row">
          <select value={lecturer} onChange={(e) => setLecturer(e.target.value)}>
            <option value="">Select a lecturer…</option>
            {lecturers.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
          <button onClick={exportLecturerPdf} disabled={!lecturer || exporting}>
            Export this schedule as PDF
          </button>
        </div>
        {lecturer && (
          <table className="tt-diff-table" style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>Type</th>
                <th>Course</th>
                <th>Day</th>
                <th>Time</th>
                <th>Venue</th>
              </tr>
            </thead>
            <tbody>
              {lecturerEntries.map((e) => (
                <tr key={e.id}>
                  <td>{KIND_LABELS[e.kind]}</td>
                  <td>{e.course_code}</td>
                  <td>
                    {e.day}
                    {e.date ? ` (${e.date})` : ''}
                  </td>
                  <td>
                    {e.start_time}–{e.end_time}
                  </td>
                  <td>
                    {e.venue_name}
                    {e.venue_kind === 'lab' ? ' (Lab)' : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {exportedPath && <p className="tt-hint">Saved to {exportedPath}</p>}
    </div>
  );
}
