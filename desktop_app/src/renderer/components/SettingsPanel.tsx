import React, { useState } from 'react';
import { useAuthStore } from '../state/authStore';

/**
 * This is the visible face of a thing that already happens silently:
 * login() persists { baseUrl, token, username, ... } into the local
 * SQLite `meta` table (db.ts -> saveSession), and every app launch reads
 * it back (authStore.restoreSession -> IPC 'auth:getSession') so the user
 * is never asked to log in again — online or offline — until this screen's
 * Reset button is used.
 */
export default function SettingsPanel() {
  const session = useAuthStore((s) => s.session);
  const logout = useAuthStore((s) => s.logout);
  const [confirming, setConfirming] = useState(false);
  const [resetting, setResetting] = useState(false);

  async function handleReset() {
    setResetting(true);
    try {
      // logout() does both halves of "reset": tells the server to revoke
      // this device's token (so it can't be used from anywhere, even if
      // someone copied the local db file), THEN clears the token from
      // this machine's local storage. If the server can't be reached, the
      // local half still runs — you're always able to reset locally even
      // fully offline; the server-side token just lapses on its own once
      // this device stops presenting it.
      await logout();
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className="tt-panel">
      <div className="tt-panel-header">
        <h2>Settings</h2>
        <p className="tt-hint">Connection and account details for this device.</p>
      </div>

      <div className="tt-settings-card">
        <h3>Signed in</h3>
        <dl className="tt-settings-list">
          <dt>Server</dt>
          <dd>{session?.baseUrl}</dd>
          <dt>Signed in as</dt>
          <dd>
            {session?.displayName} ({session?.username})
          </dd>
          <dt>Roles</dt>
          <dd>{session?.roles?.length ? session.roles.join(', ') : '—'}</dd>
        </dl>
        <p className="tt-hint">
          These credentials are saved on this computer. The app opens straight to the dashboard on every launch —
          including fully offline — without asking you to log in again, until you reset below.
        </p>
      </div>

      <div className="tt-settings-card tt-settings-card--danger">
        <h3>Reset this device</h3>
        <p className="tt-hint">
          Clears the saved login from this computer and revokes it on the server, so it can't be used again from
          here. Your offline timetable data and any edits queued to sync are kept — only the login is reset. You'll
          need to sign in again to keep editing or syncing.
        </p>
        {!confirming ? (
          <button className="tt-danger-btn" onClick={() => setConfirming(true)}>
            Reset login
          </button>
        ) : (
          <div className="tt-confirm-row">
            <span>Reset the saved login on this device?</span>
            <button className="tt-danger-btn" onClick={handleReset} disabled={resetting}>
              {resetting ? 'Resetting…' : 'Yes, reset'}
            </button>
            <button onClick={() => setConfirming(false)} disabled={resetting}>
              Cancel
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
