import React from 'react';
import { useAuthStore } from '../state/authStore';
import type { PanelKey } from './DashboardShell';

export default function DashboardHome({
  conflictCount,
  onNavigate
}: {
  conflictCount: number;
  onNavigate: (panel: PanelKey) => void;
}) {
  const session = useAuthStore((s) => s.session);

  return (
    <div className="tt-panel">
      <div className="tt-panel-header">
        <h2>Welcome{session ? `, ${session.displayName}` : ''}</h2>
        <p className="tt-hint">
          Signed in to {session?.baseUrl} · {session?.roles.join(', ') || 'Staff'}
        </p>
      </div>

      <div className="tt-dash-grid">
        <button className="tt-dash-card tt-dash-card--matrix" onClick={() => onNavigate('matrix')}>
          <h3>Operations Matrix</h3>
          <p>The full timetabling dashboard — all sections, all panels, same as the web version.</p>
        </button>

        <button className="tt-dash-card" onClick={() => onNavigate('main')}>
          <h3>Main Timetable</h3>
          <p>Edit regular class scheduling — drag-and-drop or cut/copy/paste between slots.</p>
        </button>

        <button className="tt-dash-card" onClick={() => onNavigate('exam')}>
          <h3>Exam Timetable</h3>
          <p>Edit exam scheduling — same editing tools, with exam dates per entry.</p>
        </button>

        <button
          className={'tt-dash-card' + (conflictCount > 0 ? ' tt-dash-card--warn' : '')}
          onClick={() => onNavigate('conflicts')}
        >
          <h3>Sync Conflicts</h3>
          <p>
            {conflictCount > 0
              ? `${conflictCount} entr${conflictCount === 1 ? 'y needs' : 'ies need'} your input to resolve.`
              : 'No conflicts right now — everything in sync.'}
          </p>
        </button>

        <button className="tt-dash-card" onClick={() => onNavigate('analysis')}>
          <h3>Analysis</h3>
          <p>Venue utilization and lecturer schedules from your cached data, with offline PDF export.</p>
        </button>
      </div>
    </div>
  );
}
