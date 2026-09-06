"""
sync_tls
========
Certificate pinning ("trust on first connect", the same model SSH host
keys use) for the HOST -> REMOTE sync feature.

Why this exists: for two machines you control on a private/intranet
network, validating the remote's cert against a public CA doesn't work
(self-signed cert) and just disabling verification (SyncNode.verify_ssl
= False) means anyone who can spoof the hostname on that network can
impersonate the remote. Pinning splits the difference: the HOST's admin
fetches the remote's certificate once, confirms it out-of-band (e.g. by
comparing the fingerprint to what `openssl x509 -in cert.pem -noout
-fingerprint -sha256` prints when run directly on the remote machine),
and pins it. Every sync request after that independently re-fetches the
remote's current certificate and compares its fingerprint to the pinned
value *before* sending any data — a mismatch aborts the request rather
than silently trusting whatever answered.

This intentionally does its own TLS handshake with verify_mode =
CERT_NONE to *retrieve* the certificate (there's nothing to verify it
against yet — that's the whole point), then makes the trust decision
itself by comparing fingerprints. That decision, not the OS/CA trust
store, is what `resolve_verify()` turns into the `verify=` kwarg for
the actual `requests` call.
"""
from __future__ import annotations

import hashlib
import socket
import ssl
from urllib.parse import urlparse


class CertFetchError(Exception):
    """Couldn't complete a TLS handshake with the remote to read its
    certificate — network/DNS/timeout issue, not a trust decision."""


def _host_port_from_url(remote_url: str):
    parsed = urlparse(remote_url)
    host = parsed.hostname
    if not host:
        raise ValueError(f"Could not determine a hostname from remote URL '{remote_url}'.")
    port = parsed.port or 443
    return host, port


def get_remote_cert_fingerprint(remote_url: str, timeout: float = 8) -> str:
    """Opens a TLS connection to remote_url's host:port and returns the
    SHA-256 fingerprint (lowercase hex) of the certificate it presents.
    Deliberately does not validate the cert — that's the caller's job
    (see resolve_verify / check_pin below)."""
    host, port = _host_port_from_url(remote_url)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls_sock:
                der_cert = tls_sock.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as e:
        raise CertFetchError(f"Could not reach {host}:{port} to read its certificate: {e}") from e
    if not der_cert:
        raise CertFetchError(f"{host}:{port} did not present a certificate.")
    return hashlib.sha256(der_cert).hexdigest()


def format_fingerprint(hexdigest: str) -> str:
    """sha256-hex -> 'AA:BB:CC:...' for display."""
    return ":".join(hexdigest[i:i + 2] for i in range(0, len(hexdigest), 2)).upper()


def check_pin(node) -> dict:
    """Re-fetches the remote's current cert and compares it to
    node.pinned_cert_fingerprint. Returns a dict with at least an "ok"
    key. Only call this when a fingerprint is already pinned."""
    try:
        current = get_remote_cert_fingerprint(node.remote_url)
    except (CertFetchError, ValueError) as e:
        return {"ok": False, "error": str(e), "current_fingerprint": None}

    if current == node.pinned_cert_fingerprint:
        return {"ok": True, "current_fingerprint": current}

    return {
        "ok": False,
        "current_fingerprint": current,
        "error": (
            "SECURITY WARNING: the remote's TLS certificate does not match the pinned "
            "fingerprint. This can happen after a legitimate certificate renewal, or it "
            "can mean you're talking to a different machine than the one you pinned "
            "(e.g. a network-level impersonation). No data was sent. If this is expected "
            "(e.g. the remote's cert was renewed), verify the new fingerprint out-of-band "
            "on the remote itself, then re-pin it below."
        ),
    }


def resolve_verify(node):
    """Central trust decision for every outbound sync HTTP request.

    Returns (proceed: bool, verify: bool, message: str):
      * proceed=False  -> abort, don't make the request at all; message
        explains why (shown to the admin / recorded as the failure).
      * proceed=True   -> go ahead; `verify` is the value to pass as
        requests' `verify=` kwarg.

    Precedence: a pinned fingerprint always wins (it's the stronger
    check) and is re-verified on every call — pinning once doesn't mean
    trusting forever without re-checking. Falls back to the plain
    verify_ssl toggle when nothing is pinned.
    """
    if node.pinned_cert_fingerprint:
        result = check_pin(node)
        if not result["ok"]:
            return False, False, result["error"]
        # Identity already independently confirmed via the pin match,
        # so we deliberately skip requests' own CA-chain check here —
        # that check would just fail again on the self-signed cert.
        return True, False, ""

    if node.verify_ssl:
        return True, True, ""

    return True, False, ""
