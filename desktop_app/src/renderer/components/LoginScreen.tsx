import React, { useState } from 'react';
import { useAuthStore } from '../state/authStore';

const LAST_SERVER_KEY = 'chuka_last_server_url';
const PRODUCTION_SERVER = 'https://timetable.chuka.ac.ke';
// Set at build time by run.sh (VITE_DEFAULT_SERVER_URL) so a local-testing build opens pointed at the
// local backend. It wins over the remembered address on purpose: a test build must never silently
// fall back to production. Normal builds leave it unset, so nothing changes for them.
const BUILD_SERVER_URL = (import.meta.env.VITE_DEFAULT_SERVER_URL as string | undefined)?.trim();

export default function LoginScreen() {
  const login = useAuthStore((s) => s.login);
  const error = useAuthStore((s) => s.error);
  const [baseUrl, setBaseUrl] = useState(BUILD_SERVER_URL || localStorage.getItem(LAST_SERVER_KEY) || PRODUCTION_SERVER);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await login(baseUrl.trim(), username.trim(), password);
      localStorage.setItem(LAST_SERVER_KEY, baseUrl.trim());
    } catch {
      // error is already surfaced via the store's `error` field
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="tt-login-wrap">
      <form className="tt-login-card" onSubmit={handleSubmit}>
        <h1>Chuka Timetabling</h1>
        <p className="tt-hint">Sign in with your Timetable Office account to edit and sync offline.</p>

        <label>
          Server address
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={PRODUCTION_SERVER}
            required
          />
        </label>

        <label>
          Username
          <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
        </label>

        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>

        {error && <div className="tt-login-error">{error}</div>}

        <button type="submit" disabled={submitting}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>

        <p className="tt-hint">
          Only Timetable Office staff accounts can sign in here. Once signed in, this device stays logged in and
          works offline until you sign out.
        </p>
      </form>
    </div>
  );
}
