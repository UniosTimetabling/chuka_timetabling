import React, { useEffect, useState } from 'react';
import TimetableGrid from './TimetableGrid';
import { useTimetableStore } from '../state/store';
import { EntryKind } from '@shared/types';
import type { PanelKey } from './DashboardShell';

// Mirrors timetable.models.ExamSchedulerConfig defaults. Each row here is
// still a day-of-week + time slot (ExamTimetable.day is a CharField like
// "Monday"); the concrete calendar date lives on entry.date and is shown
// in the cell via showDateColumn.
const EXAM_SLOTS = [
  { start_time: '08:00', end_time: '11:00' },
  { start_time: '11:30', end_time: '14:30' },
  { start_time: '15:00', end_time: '18:00' }
];

const LAB_EXAM_SLOTS = EXAM_SLOTS;

export default function ExamTimetablePanel({ onNavigate }: { onNavigate: (panel: PanelKey) => void }) {
  const loadEntries = useTimetableStore((s) => s.loadEntries);
  const [sub, setSub] = useState<Extract<EntryKind, 'exam' | 'lab_exam'>>('exam');

  useEffect(() => {
    loadEntries(sub);
  }, [loadEntries, sub]);

  return (
    <div className="tt-panel">
      <div className="tt-panel-header">
        <h2>Exam Timetable</h2>
        <p className="tt-hint">Same editing as the main timetable. Columns are exam dates: drop a paper on another date to reschedule it.</p>
        <div className="tt-subtabs">
          <button className={sub === 'exam' ? 'active' : ''} onClick={() => setSub('exam')}>
            Exam Venues
          </button>
          <button className={sub === 'lab_exam' ? 'active' : ''} onClick={() => setSub('lab_exam')}>
            Lab Exams
          </button>
        </div>
        {sub === 'lab_exam' && (
          <p className="tt-hint">
            <button className="tt-link-btn" onClick={() => onNavigate('lab_exam_autoscheduler')}>
              Auto Schedule this timetable →
            </button>{' '}
            (runs entirely offline on this computer)
          </p>
        )}
      </div>
      {sub === 'exam' ? (
        <TimetableGrid slots={EXAM_SLOTS} showDateColumn />
      ) : (
        <TimetableGrid slots={LAB_EXAM_SLOTS} showDateColumn />
      )}
    </div>
  );
}
