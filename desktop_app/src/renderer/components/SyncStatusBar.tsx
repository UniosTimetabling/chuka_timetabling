import React, { useEffect } from 'react';
import { useTimetableStore } from '../state/store';

export default function SyncStatusBar() {
  const syncStatus = useTimetableStore((s) => s.syncStatus);
  const setSyncStatus = useTimetableStore((s) => s.setSyncStatus);
  const conflictCount = useTimetableStore((s) => s.conflictCount);
  const pendingCount = useTimetableStore((s) => s.pendingCount);

  useEffect(() => {
    const refresh = async () => {
      const st = useTimetableStore.getState();
      await Promise.all([st.refreshConflictCount(), st.refreshPendingCount()]);
    };
    refresh();
    // Background syncs (startup, timer, right after login) finish without the user pressing anything:
    // when one lands, reload the grid so the user sees what came down.
    const unsubscribe = window.chukaApi.onSyncStatus(async (status) => {
      useTimetableStore.getState().setSyncStatus(status);
      if (status === 'synced' || status === 'conflicts') {
        await refresh();
        await useTimetableStore.getState().loadEntries(useTimetableStore.getState().kind);
      }
    });
    return unsubscribe;
  }, []);

  async function handleSync() {
    setSyncStatus('syncing');
    try {
      await window.chukaApi.syncNow();
      // status + reload are driven by the 'sync:status' event the main process emits
    } catch {
      // the main process already broadcast 'offline' / 'error' / 'auth-expired'
    }
  }

  const unsynced = pendingCount > 0 ? ` · ${pendingCount} unsynced change${pendingCount === 1 ? '' : 's'}` : '';
  return (
    <div className={`tt-syncbar tt-syncbar--${syncStatus}`}>
      <span className="tt-syncbar-status">
        {syncStatus === 'syncing' && 'Syncing…'}
        {syncStatus === 'synced' && `Up to date with the server${unsynced}`}
        {syncStatus === 'offline' && `Offline — changes are saved on this computer and will sync when reconnected${unsynced}`}
        {syncStatus === 'error' && `The server had a problem — will retry automatically${unsynced}`}
        {syncStatus === 'conflicts' && `${conflictCount} item(s) need your decision${unsynced}`}
        {syncStatus === 'idle' && `Not synced yet${unsynced}`}
      </span>
      <button onClick={handleSync} disabled={syncStatus === 'syncing'}>
        Sync now
      </button>
    </div>
  );
}
