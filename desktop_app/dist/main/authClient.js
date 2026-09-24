"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.login = login;
exports.logout = logout;
exports.whoAmI = whoAmI;
const os_1 = __importDefault(require("os"));
const http_1 = require("./http");
async function login({ baseUrl, username, password }) {
    const origin = (0, http_1.normalizeBaseUrl)(baseUrl);
    let res;
    try {
        res = await (0, http_1.fetchJson)(origin + '/api/desktop/auth/login/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password, device_label: `${os_1.default.hostname()} (Desktop)` })
        }, 20000);
    }
    catch (err) {
        const why = err instanceof http_1.NetworkError ? ` (${err.message})` : '';
        throw new Error(`Could not reach ${origin}${why}. Check the server address and your network connection.`);
    }
    if (!res.ok) {
        if (res.data?.error)
            throw new Error(res.data.error);
        if (res.status === 404)
            throw new Error('That server does not have the desktop sync API installed (/api/desktop/ not found).');
        throw new Error(`Login failed (HTTP ${res.status}).`);
    }
    const u = res.data?.user;
    if (!u?.token)
        throw new Error('The server answered, but not with a desktop login response. Is the address right?');
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
async function logout(session) {
    try {
        await (0, http_1.fetchJson)(session.baseUrl + '/api/desktop/auth/logout/', { method: 'POST', headers: { Authorization: `Token ${session.token}` } }, 10000);
    }
    catch {
        // Best-effort — even if the server can't be reached, the local session is cleared by the caller.
    }
}
/**
 * Startup check of a saved token.
 *  'valid'   - server confirmed it
 *  'invalid' - server said 401/403 (revoked, or the account lost its role) -> sign in again
 *  'unknown' - unreachable or a 5xx/proxy page: keep the session so the user can work offline
 */
async function whoAmI(session) {
    try {
        const res = await (0, http_1.fetchJson)(session.baseUrl + '/api/desktop/auth/me/', { headers: { Authorization: `Token ${session.token}` } }, 10000);
        if (res.ok)
            return 'valid';
        if (res.status === 401 || (res.status === 403 && res.data?.error))
            return 'invalid'; // a 403 from a proxy/WAF has no JSON body
        return 'unknown';
    }
    catch {
        return 'unknown';
    }
}
