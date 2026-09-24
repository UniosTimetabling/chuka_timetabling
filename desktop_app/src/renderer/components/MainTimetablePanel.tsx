import React, { useEffect, useState } from 'react';
import TimetableGrid from './TimetableGrid';
import { useTimetableStore } from '../state/store';
import { EntryKind } from '@shared/types';

// Mirrors timetable.models.SchedulerConfig defaults (07:00–19:00, 3h slots)
// used by the regular timetable autoscheduler. Adjust to match the active
// SchedulerConfig fetched from /api/desktop/config/ once wired up.
const REGULAR_SLOTS = [
  { start_time: '07:00', end_time: '10:00' },
  { start_time: '10:00', end_time: '13:00' },
  { start_time: '13:00', end_time: '16:00' },
  { start_time: '16:00', end_time: '19:00' }
];

// Lab sessions currently run the same slot pattern — adjust independently
// here if the LabScheduler ever uses a different grid.
const LAB_SLOTS = REGULAR_SLOTS;

export default function MainTimetablePanel() {
  const loadEntries = useTimetableStore((s) => s.loadEntries);
  const [sub, setSub] = useState<Extract<EntryKind, 'regular' | 'lab'>>('regular');

  useEffect(() => {
    loadEntries(sub);
  }, [loadEntries, sub]);

  return (
    <div className="tt-panel">
      <div className="tt-panel-header">
        <h2>Main Timetable</h2>
        <p className="tt-hint">
          Drag a class to another slot to move it (it keeps its venue), or select it and use Ctrl+C / Ctrl+X then Ctrl+V on a
          target slot — same shortcuts as a Word document. Changes save on this computer at once and sync to the server.
        </p>
        <div className="tt-subtabs">
          <button className={sub === 'regular' ? 'active' : ''} onClick={() => setSub('regular')}>
            Lecture Venues
          </button>
          <button className={sub === 'lab' ? 'active' : ''} onClick={() => setSub('lab')}>
            Lab Sessions
          </button>
        </div>
      </div>
      {sub === 'regular' ? (
        <TimetableGrid slots={REGULAR_SLOTS} />
      ) : (
        <TimetableGrid slots={LAB_SLOTS} />
      )}
    </div>
  );
}
