import React, { useEffect } from 'react';
import LoginScreen from './components/LoginScreen';
import DashboardShell from './components/DashboardShell';
import { useAuthStore } from './state/authStore';

export default function App() {
  const status = useAuthStore((s) => s.status);
  const restoreSession = useAuthStore((s) => s.restoreSession);

  useEffect(() => {
    restoreSession();
    // The server revoked this device's token (or the account lost its role) during a background sync.
    return window.chukaApi.onSyncStatus((st) => {
      if (st === 'auth-expired') {
        useAuthStore.setState({ session: null, status: 'signed_out', error: 'Your session is no longer valid. Please sign in again.' });
      }
    });
  }, [restoreSession]);

  if (status === 'checking') {
    return (
      <div className="tt-login-wrap">
        <p className="tt-hint">Loading…</p>
      </div>
    );
  }

  return status === 'signed_in' ? <DashboardShell /> : <LoginScreen />;
}
