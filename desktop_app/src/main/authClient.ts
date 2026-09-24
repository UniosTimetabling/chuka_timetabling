import os from 'os';
import { UserSession } from '../shared/types';
import { AuthError, fetchJson, NetworkError, normalizeBaseUrl } from './http';

interface LoginArgs {
  baseUrl: string;
  username: string;
  password: string;
}

/** Maps directly onto desktop_sync/views_auth.py's response shape. */
interface ServerUser {
  username: string;
  display_name: string;
  is_staff: boolean;
  is_superuser: boolean;
  roles: string[];
  token: string;
}

export async function login({ baseUrl, username, password }: LoginArgs): Promise<UserSession> {
  const origin = normalizeBaseUrl(baseUrl);
  let res;
  try {
    res = await fetchJson(
      origin + '/api/desktop/auth/login/',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, device_label: `${os.hostname()} (Desktop)` })
      },
      20_000
    );
  } catch (err) {
    const why = err instanceof NetworkError ? ` (${err.message})` : '';
    throw new Error(`Could not reach ${origin}${why}. Check the server address and your network connection.`);
  }

  if (!res.ok) {
    if (res.data?.error) throw new Error(res.data.error);
    if (res.status === 404) throw new Error('That server does not have the desktop sync API installed (/api/desktop/ not found).');
    throw new Error(`Login failed (HTTP ${res.status}).`);
  }

  const u: ServerUser | undefined = res.data?.user;
  if (!u?.token) throw new Error('The server answered, but not with a desktop login response. Is the address right?');
  return {
    baseUrl: origin,
    token: u.token,
    username: u.username,
    displayName: u.display_name,
    isStaff: u.is_staff,
    isSuperuser: u.is_superuser,
    roles: u.roles
  };
}

export async function logout(session: UserSession): Promise<void> {
  try {
    await fetchJson(session.baseUrl + '/api/desktop/auth/logout/', { method: 'POST', headers: { Authorization: `Token ${session.token}` } }, 10_000);
  } catch {
    // Best-effort — even if the server can't be reached, the local session is cleared by the caller.
  }
}

/**
 * Startup check of a saved token.
 *  'valid'   - server confirmed it
 *  'invalid' - server said 401/403 (revoked, or the account lost its role) -> sign in again
 *  'unknown' - unreachable or a 5xx/proxy page: keep the session so the user can work offline
 */
export async function whoAmI(session: UserSession): Promise<'valid' | 'invalid' | 'unknown'> {
  try {
    const res = await fetchJson(session.baseUrl + '/api/desktop/auth/me/', { headers: { Authorization: `Token ${session.token}` } }, 10_000);
    if (res.ok) return 'valid';
    if (res.status === 401 || (res.status === 403 && res.data?.error)) return 'invalid'; // a 403 from a proxy/WAF has no JSON body
    return 'unknown';
  } catch {
    return 'unknown';
  }
}
