"use strict";
/** Small fetch wrapper shared by the auth client and the sync engine: timeouts, JSON parsing, typed errors. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.HttpError = exports.AuthError = exports.NetworkError = void 0;
exports.normalizeBaseUrl = normalizeBaseUrl;
exports.fetchJson = fetchJson;
exports.fetchBinary = fetchBinary;
/** The server could not be reached (offline, DNS, TLS, timeout). Queued work stays queued. */
class NetworkError extends Error {
}
exports.NetworkError = NetworkError;
/** The server answered 401/403: the saved token is revoked/expired or the account lost its role. */
class AuthError extends Error {
}
exports.AuthError = AuthError;
/** Any other non-2xx answer. */
class HttpError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}
exports.HttpError = HttpError;
/**
 * Accepts "timetable.chuka.ac.ke", "https://timetable.chuka.ac.ke/", or a LAN/dev address like
 * "localhost:8000". Bare hosts get https:// (the production server redirects http -> https, and a
 * redirected POST becomes a GET, which would surface as a confusing 405), except private/localhost
 * addresses, which get http://. Any path is dropped; only the origin is kept.
 */
function normalizeBaseUrl(input) {
    let v = (input || '').trim();
    if (!v)
        throw new Error('Enter the server address.');
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(v)) {
        const isLocal = /^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|\[?::1)/i.test(v);
        v = (isLocal ? 'http://' : 'https://') + v;
    }
    let url;
    try {
        url = new URL(v);
    }
    catch {
        throw new Error(`"${input}" is not a valid server address.`);
    }
    if (url.protocol !== 'http:' && url.protocol !== 'https:')
        throw new Error('The server address must start with http:// or https://.');
    return url.origin;
}
async function fetchJson(url, init = {}, timeoutMs = 30000) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    let res;
    try {
        res = await fetch(url, { ...init, signal: ctrl.signal });
    }
    catch (err) {
        if (err?.name === 'AbortError')
            throw new NetworkError(`The server did not answer within ${Math.round(timeoutMs / 1000)}s.`);
        throw new NetworkError(err?.cause?.message || err?.message || 'Network error');
    }
    finally {
        clearTimeout(timer);
    }
    let data = null;
    try {
        data = await res.json();
    }
    catch {
        /* non-JSON body (proxy error page, maintenance page, ...) */
    }
    return { status: res.status, ok: res.ok, data };
}
/** Same error handling as fetchJson, but for a binary body (PDF download) rather than JSON. */
async function fetchBinary(url, init = {}, timeoutMs = 60000) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    let res;
    try {
        res = await fetch(url, { ...init, signal: ctrl.signal });
    }
    catch (err) {
        if (err?.name === 'AbortError')
            throw new NetworkError(`The server did not answer within ${Math.round(timeoutMs / 1000)}s.`);
        throw new NetworkError(err?.cause?.message || err?.message || 'Network error');
    }
    finally {
        clearTimeout(timer);
    }
    if (!res.ok)
        return { status: res.status, ok: false, buffer: null };
    const arrayBuf = await res.arrayBuffer();
    return { status: res.status, ok: true, buffer: Buffer.from(arrayBuf) };
}
