import React, { useEffect, useState } from 'react';
import MainTimetablePanel from './MainTimetablePanel';
import ExamTimetablePanel from './ExamTimetablePanel';
import DashboardHome from './DashboardHome';
import SettingsPanel from './SettingsPanel';
import AnalysisPanel from './AnalysisPanel';
import OperationsMatrix from './OperationsMatrix';
import LabExamAutoSchedulerPanel from './LabExamAutoSchedulerPanel';
import PublishedTimetablesPanel from './PublishedTimetablesPanel';
import ConflictResolutionModal from './ConflictResolutionModal';
import SyncStatusBar from './SyncStatusBar';
import { useTimetableStore } from '../state/store';
import { useAuthStore } from '../state/authStore';

export type PanelKey = 'dashboard' | 'main' | 'exam' | 'analysis' | 'conflicts' | 'settings' | 'matrix' | 'lab_exam_autoscheduler' | 'published_timetables';

const NAV_ITEMS: { key: PanelKey; label: string }[] = [
  { key: 'dashboard', label: 'Dashboard' },
  { key: 'matrix', label: 'Operations Matrix' },
  { key: 'main', label: 'Main Timetable' },
  { key: 'exam', label: 'Exam Timetable' },
  { key: 'analysis', label: 'Analysis' },
  { key: 'settings', label: 'Settings' }
];

export default function DashboardShell() {
  const [panel, setPanel] = useState<PanelKey>('dashboard');
  const [showConflicts, setShowConflicts] = useState(false);
  const conflictCount = useTimetableStore((s) => s.conflictCount);
  const refreshConflictCount = useTimetableStore((s) => s.refreshConflictCount);
  const session = useAuthStore((s) => s.session);
  const logout = useAuthStore((s) => s.logout);

  useEffect(() => {
    refreshConflictCount();
  }, [refreshConflictCount]);

  function navigate(target: PanelKey) {
    if (target === 'conflicts') {
      setShowConflicts(true);
      return;
    }
    setPanel(target);
  }

  return (
    <div className="tt-app tt-app--dashboard">
      <aside className="tt-sidebar">
        <div className="tt-sidebar-brand">Chuka Timetabling</div>
        <nav>
          {NAV_ITEMS.map((item) => (
            <button key={item.key} className={panel === item.key ? 'active' : ''} onClick={() => navigate(item.key)}>
              {item.label}
            </button>
          ))}
          <button className={conflictCount > 0 ? 'tt-nav-warn' : ''} onClick={() => navigate('conflicts')}>
            Conflicts{conflictCount > 0 ? ` (${conflictCount})` : ''}
          </button>
        </nav>

        <div className="tt-sidebar-footer">
          <div className="tt-sidebar-user">
            <div className="tt-sidebar-user-name">{session?.displayName}</div>
            <div className="tt-sidebar-user-server">{session?.baseUrl}</div>
          </div>
          <button
            className="tt-logout-btn"
            onClick={() => {
              const pending = useTimetableStore.getState().pendingCount;
              if (
                pending > 0 &&
                !window.confirm(
                  `You have ${pending} unsynced change${pending === 1 ? '' : 's'}. They stay on this computer and sync after you sign in again to the same server. Sign out anyway?`
                )
              )
                return;
              logout();
            }}
          >
            Sign out
          </button>
        </div>
      </aside>

      <div className="tt-main-column">
        {panel === 'dashboard' && <DashboardHome conflictCount={conflictCount} onNavigate={navigate} />}
        {panel === 'matrix' && <OperationsMatrix onNavigate={navigate} />}
        {panel === 'main' && <MainTimetablePanel />}
        {panel === 'exam' && <ExamTimetablePanel onNavigate={navigate} />}
        {panel === 'analysis' && <AnalysisPanel />}
        {panel === 'lab_exam_autoscheduler' && <LabExamAutoSchedulerPanel onNavigate={navigate} />}
        {panel === 'published_timetables' && <PublishedTimetablesPanel />}
        {panel === 'settings' && <SettingsPanel />}

        <SyncStatusBar />
      </div>

      {showConflicts && (
        <ConflictResolutionModal
          onClosed={() => {
            setShowConflicts(false);
            refreshConflictCount();
          }}
        />
      )}
    </div>
  );
}
