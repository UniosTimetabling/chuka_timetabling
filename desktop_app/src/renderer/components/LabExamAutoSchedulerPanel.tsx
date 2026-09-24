import React, { useEffect, useState } from 'react';
import type { LabExamReferenceData } from '@shared/types';
import type { PanelKey } from './DashboardShell';
import { useTimetableStore } from '../state/store';

type RunResult = { status: 'success' | 'warning' | 'error'; message: string; removed: number; created: number };

export default function LabExamAutoSchedulerPanel({ onNavigate }: { onNavigate: (panel: PanelKey) => void }) {
  const [reference, setReference] = useState<LabExamReferenceData | null>(null);
  const [loadingRef, setLoadingRef] = useState(true);
  const [refError, setRefError] = useState<string | null>(null);
  const [pullFreshFirst, setPullFreshFirst] = useState(true);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const refreshPendingCount = useTimetableStore((s) => s.refreshPendingCount);

  useEffect(() => {
    let cancelled = false;
    window.chukaApi
      .getCachedLabExamReference()
      .then((data) => {
        if (!cancelled) setReference(data);
      })
      .catch((err) => {
        if (!cancelled) setRefError(err?.message || String(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingRef(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleRun() {
    setRunning(true);
    setResult(null);
    try {
      const r = await window.chukaApi.runLabExamScheduler(pullFreshFirst);
      setResult(r);
      if (pullFreshFirst) {
        const fresh = await window.chukaApi.getCachedLabExamReference();
        setReference(fresh);
      }
      await refreshPendingCount(); // the run queued create/delete edits — reflect that in the sync status bar right away
    } catch (err: any) {
      setResult({ status: 'error', message: err?.message || String(err), removed: 0, created: 0 });
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="tt-panel">
      <div className="tt-panel-header">
        <h2>Lab / Workshop Exam Auto Scheduler</h2>
        <p className="tt-hint">
          Wipes and regenerates the entire Lab Exam timetable from the current lab allocations — the same rules as
          the web version (program, lecturer, and venue clashes, including splitting large groups across venues),
          computed entirely on this computer. Nothing is sent anywhere until you sync.
        </p>
      </div>

      <div className="tt-settings-card">
        <h3>Input data</h3>
        {loadingRef ? (
          <p className="tt-hint">Loading…</p>
        ) : refError ? (
          <p className="tt-hint">Couldn't load cached reference data: {refError}</p>
        ) : reference ? (
          <dl className="tt-settings-list">
            <dt>Lab allocations</dt>
            <dd>{reference.lab_allocations.length}</dd>
            <dt>Candidate venues</dt>
            <dd>{reference.lab_venues.length}</dd>
            <dt>Last pulled from server</dt>
            <dd>{reference.generated_at ? new Date(reference.generated_at).toLocaleString() : '—'}</dd>
          </dl>
        ) : (
          <p className="tt-hint">
            No data pulled yet. Connect once with "Pull latest data first" checked below — after that, this can run
            fully offline against whatever was last pulled.
          </p>
        )}
      </div>

      <div className="tt-settings-card">
        <h3>Run</h3>
        <label className="tt-checkbox-row">
          <input type="checkbox" checked={pullFreshFirst} onChange={(e) => setPullFreshFirst(e.target.checked)} />
          Pull latest allocations, venues and scheduler config from the server first (needs a connection)
        </label>
        <p className="tt-hint">
          Unchecked, this runs entirely offline against the input data shown above — useful on-site with no
          connection, but any allocation changes made since the last pull won't be reflected.
        </p>
        <button className="tt-primary-btn" onClick={handleRun} disabled={running}>
          {running ? 'Running…' : 'Run Auto Scheduler'}
        </button>

        {result && (
          <div className={`tt-result-banner tt-result-banner--${result.status}`}>
            <strong>{result.status === 'success' ? 'Done.' : result.status === 'warning' ? 'Nothing scheduled.' : 'Failed.'}</strong>{' '}
            {result.message}
            {result.status !== 'error' && (
              <div className="tt-hint">
                Removed {result.removed} previous session(s), queued {result.created} new one(s) — queued locally,
                not yet synced.{' '}
                <button className="tt-link-btn" onClick={() => onNavigate('exam')}>
                  Review in Exam Timetable → Lab Exams
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
