import React, { useEffect, useState } from 'react';
import { ConflictRecord, TimetableEntry } from '@shared/types';
import { useTimetableStore } from '../state/store';

const DIFF_FIELDS: (keyof TimetableEntry)[] = [
  'day',
  'date',
  'start_time',
  'end_time',
  'venue_id',
  'venue_name',
  'course_code',
  'lecturer_name'
];

function diverging(local: TimetableEntry, server: TimetableEntry) {
  return DIFF_FIELDS.filter((f) => local[f] !== server[f]);
}

export default function ConflictResolutionModal({ onClosed }: { onClosed: () => void }) {
  const [conflicts, setConflicts] = useState<ConflictRecord[]>([]);
  const [fieldChoices, setFieldChoices] = useState<Record<string, 'local' | 'server'>>({});

  useEffect(() => {
    window.chukaApi.listConflicts().then((list) => setConflicts(list as ConflictRecord[]));
  }, []);

  if (conflicts.length === 0) return null;
  const current = conflicts[0];
  const serverGone = !!current.server.deleted;
  const diffFields = serverGone ? [] : diverging(current.local, current.server);

  async function resolve(mode: 'keep_local' | 'keep_server' | 'merged') {
    let merged: Partial<TimetableEntry> | undefined;
    if (mode === 'merged') {
      merged = {};
      for (const f of diffFields) {
        const choice = fieldChoices[f] ?? 'local';
        (merged as any)[f] = choice === 'local' ? current.local[f] : current.server[f];
      }
    }
    await window.chukaApi.resolveConflict(current.entry_id, mode, merged);
    const st = useTimetableStore.getState();
    await Promise.all([st.loadEntries(st.kind), st.refreshPendingCount(), st.refreshConflictCount()]);
    const rest = conflicts.slice(1);
    setConflicts(rest);
    setFieldChoices({});
    if (rest.length === 0) onClosed();
  }

  return (
    <div className="tt-modal-backdrop">
      <div className="tt-modal">
        <h3>
          {current.reason && !serverGone ? 'Change rejected' : 'Sync conflict'} — {current.local.course_code}
        </h3>
        {current.reason ? (
          <p className="tt-conflict-reason">{current.reason}</p>
        ) : (
          <p className="tt-hint">
            This entry was changed here while it was also changed on the server (or by another user) before this
            machine could sync. Choose how to resolve it — nothing has been overwritten yet.
          </p>
        )}
        {current.reason && !serverGone && (
          <p className="tt-hint">
            Your edit was not applied on the server. Keep the server version to discard it, or keep yours to try again
            after fixing the cause (for example on the web timetable).
          </p>
        )}

        <table className="tt-diff-table">
          <thead>
            <tr>
              <th>Field</th>
              <th>This computer (offline edit)</th>
              <th>Server (current)</th>
            </tr>
          </thead>
          <tbody>
            {serverGone && (
              <tr>
                <td colSpan={3}>
                  This entry no longer exists on the server. You can forget it here, or re-create it from your copy.
                  ({current.local.day} {current.local.start_time}–{current.local.end_time}, {current.local.venue_name})
                </td>
              </tr>
            )}
            {!serverGone && diffFields.length === 0 && (
              <tr>
                <td colSpan={3}>Only the row's version number differs — the values are the same on both sides.</td>
              </tr>
            )}
            {diffFields.map((f) => (
              <tr key={f}>
                <td>{f}</td>
                <td>
                  <label>
                    <input
                      type="radio"
                      name={`choice-${f}`}
                      checked={(fieldChoices[f] ?? 'local') === 'local'}
                      onChange={() => setFieldChoices((c) => ({ ...c, [f]: 'local' }))}
                    />
                    {String(current.local[f])}
                  </label>
                </td>
                <td>
                  <label>
                    <input
                      type="radio"
                      name={`choice-${f}`}
                      checked={fieldChoices[f] === 'server'}
                      onChange={() => setFieldChoices((c) => ({ ...c, [f]: 'server' }))}
                    />
                    {String(current.server[f])}
                  </label>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="tt-modal-actions">
          <button onClick={() => resolve('keep_local')}>{serverGone ? 'Re-create it from my copy' : 'Keep my offline version'}</button>
          <button onClick={() => resolve('keep_server')}>{serverGone ? 'Forget it' : 'Keep server version'}</button>
          <button onClick={() => resolve('merged')} disabled={diffFields.length === 0 || serverGone}>
            Use field choices above
          </button>
        </div>
        <p className="tt-hint">{conflicts.length - 1} more conflict(s) after this one.</p>
      </div>
    </div>
  );
}
