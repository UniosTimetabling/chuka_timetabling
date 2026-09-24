"""
sync_crypto
===========
Application-layer encryption for the fixture payloads pushed by
run_sync() (see sync_engine.py).

This is a DEFENSE-IN-DEPTH layer on top of transport security (HTTPS),
not a replacement for it. It protects the confidentiality and integrity
of the actual data being synced even if:

  * TLS verification is off (SyncNode.verify_ssl = False, e.g. a
    staging remote with a self-signed cert), or
  * something between the two machines terminates/re-wraps TLS in a way
    you don't fully control (a campus proxy, load balancer, etc).

It does NOT authenticate *which machine* you're talking to on the
network the way a verified TLS cert (or cert pinning) does — that's a
different problem. What it guarantees is that whoever ends up with the
bytes on the wire cannot read or silently modify them without knowing
the shared token.

Key derivation
--------------
Both the HOST and the REMOTE already hold the plaintext shared token
(that's how the existing X-Sync-Token header check works), so no new
secret needs to be configured or distributed. We derive a dedicated
256-bit AES key from it with HKDF-SHA256 and a fixed, purpose-specific
`info` string, so the payload-encryption key is cryptographically
distinct from the token value itself — the raw token is never used
directly as an AES key, and this key can never be reused to forge the
X-Sync-Token header or vice versa.

Wire format
-----------
AES-256-GCM: authenticated encryption, so confidentiality and integrity
(tamper detection) come from one primitive, no separate MAC needed.
Every payload gets a fresh random 96-bit nonce. What travels over the
wire, as a single base64 string in the `fixture_encrypted` JSON field,
is:

    nonce (12 bytes) || ciphertext || GCM tag (16 bytes)

Decrypting with the wrong token, or a payload that was truncated or
tampered with in transit, raises SyncPayloadDecryptError — there is no
"partially decrypted" result.

Deployment note
----------------
This changes the wire format of the sync push (`fixture` -> the new
`fixture_encrypted` field), so the HOST and REMOTE sides must be
upgraded together — an old REMOTE can't decrypt a payload from a new
HOST, and vice versa.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_HKDF_INFO = b"chuka-timetabling-sync-payload-v1"
_NONCE_LEN = 12  # bytes; standard/recommended nonce size for AES-GCM


class SyncPayloadDecryptError(Exception):
    """Raised when a pushed payload can't be decrypted/authenticated.

    In practice this almost always means either the HOST and REMOTE's
    shared_token values don't match, or the payload was corrupted or
    tampered with in transit — never partial output."""


def _derive_key(shared_token: str) -> bytes:
    if not shared_token:
        raise ValueError("Cannot derive a payload encryption key without a shared token.")
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO)
    return hkdf.derive(shared_token.encode("utf-8"))


def encrypt_payload(plaintext_json: str, shared_token: str) -> str:
    """Encrypts a fixture JSON string with a key derived from
    shared_token. Returns a base64 string safe to embed directly in a
    JSON request body."""
    key = _derive_key(shared_token)
    aesgcm = AESGCM(key)
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = aesgcm.encrypt(nonce, plaintext_json.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_payload(encoded: str, shared_token: str) -> str:
    """Reverses encrypt_payload(). Raises SyncPayloadDecryptError on any
    failure — wrong token, corrupted payload, or wrong/old format."""
    key = _derive_key(shared_token)
    aesgcm = AESGCM(key)
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) < _NONCE_LEN:
            raise ValueError("Payload too short to contain a nonce.")
        nonce, ciphertext = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as e:
        raise SyncPayloadDecryptError(str(e)) from e
    return plaintext.decode("utf-8")
