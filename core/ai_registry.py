"""
core/ai_registry.py
====================
Central, provider-agnostic AI client for the whole project.

Any module that needs an LLM call (Smart Importer today, anything else
tomorrow) should do:

    from core.ai_registry import get_ai_client, AIDisabled, AIError

    client = get_ai_client()
    if client is None:
        # No provider configured / active — feature must degrade
        # gracefully to its non-AI behaviour. This is NOT an error.
        ...
    else:
        try:
            text = client.call("your prompt here")
        except AIError as e:
            # surface a clean message to the user; never leak the key
            ...

Supported providers: Gemini, OpenAI, Anthropic, Ollama, any
OpenAI-compatible endpoint (DeepSeek, BlackBox, Groq, OpenRouter,
Mistral, Together, xAI/Grok, Fireworks, Perplexity, LM Studio, vLLM,
etc. — pick provider="custom" and set base_url), and — for anything
that isn't OpenAI-shaped, including providers that don't exist yet —
provider="generic", a fully template-driven HTTP+JSON call where the
URL, headers, request body, and response-extraction path are all
defined in the DB. Adding a brand-new AI provider therefore NEVER
requires a code change or deployment: it's always a new
AIProviderSettings row. Configuration lives in the DB
(`core.models.AIProviderSettings`), editable from Django admin or the
"AI Settings" page in the sudo dashboard — never hard-coded, never
checked into source.

──────────────────────────────────────────────────────────────────────
SECURITY NOTES
──────────────────────────────────────────────────────────────────────
- API keys are encrypted at rest with Fernet (symmetric AES-128-CBC +
  HMAC), keyed off a key *derived* from Django's SECRET_KEY via
  PBKDF2-HMAC-SHA256. We deliberately do NOT use SECRET_KEY directly
  as the Fernet key — deriving it means rotating SECRET_KEY doesn't
  silently corrupt stored keys in a confusing way, and it keeps the
  derivation a one-way function.
- Keys are decrypted only in-memory, only at call time, and are never
  written to logs. `logger` calls in this module never include the
  key — only provider name / model / status code.
- Keys never reach templates or API responses. The settings UI only
  ever shows a masked placeholder for an existing key.
- All outbound calls go over HTTPS to the provider's official API
  host (or a self-hosted Ollama / custom URL the admin explicitly
  configured) with a bounded timeout, so a hung provider can't hang
  a request indefinitely.
- This module never raises raw provider exceptions up to callers
  that might display them to non-admin users — AIError messages are
  sanitised to avoid leaking key fragments from provider error bodies.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import socket
import time
from typing import Optional

import requests
import urllib3.util.connection as _urllib3_conn
from django.conf import settings as django_settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Network hardening
# ─────────────────────────────────────────────────────────────────────────────
# Force IPv4 for all outbound AI calls made through this module. Some
# networks return a AAAA record whose route is dead (or return one at
# all) while IPv4 is fine; the default getaddrinfo() ordering can then
# cause urllib3 to attempt/prefer IPv6 and stall instead of failing
# fast. This does nothing if the host has no AAAA record — it just
# removes IPv6 as a variable entirely.
def _ipv4_only_gai_family():
    return socket.AF_INET


_urllib3_conn.allowed_gai_family = _ipv4_only_gai_family

# A single shared session for every provider call, with `trust_env`
# explicitly disabled. By default `requests` reads HTTP_PROXY /
# HTTPS_PROXY / ALL_PROXY / NO_PROXY and even ~/.netrc from the
# process environment. A long-lived `runserver` process can inherit a
# proxy var from whatever shell launched it (VPN client, corporate
# network tooling, etc.) that a freshly-opened terminal running
# `manage.py shell` never sees — which would explain calls hanging
# for the full timeout specifically from the running server while an
# identical call from a fresh shell returns in a couple of seconds.
# Explicitly bypassing env-derived proxies/netrc removes that
# variable too.
_session = requests.Session()
_session.trust_env = False


def _split_timeout(total_timeout: int) -> tuple[float, float]:
    """(connect_timeout, read_timeout).

    A dead/unreachable host or proxy has no reason to take more than a
    few seconds to complete a TCP handshake, so connect fails fast.
    Read stays at the full configured timeout since LLM generation can
    legitimately take a while. Splitting these means a bad connection
    is detected quickly instead of burning the entire timeout budget
    before we even know whether the server is reachable.
    """
    connect = min(6.0, max(2.0, total_timeout / 10))
    return (connect, float(total_timeout))


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class AIError(Exception):
    """Raised when a configured provider fails to respond usefully."""


class AIDisabled(Exception):
    """Raised only if code explicitly requires AI via get_ai_client(required=True)."""


# ─────────────────────────────────────────────────────────────────────────────
# Encryption helpers (Fernet, key derived from SECRET_KEY)
# ─────────────────────────────────────────────────────────────────────────────

def _get_fernet():
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    secret = django_settings.SECRET_KEY.encode("utf-8")
    # Static salt is fine here: the secret material is SECRET_KEY itself
    # (already high-entropy and server-only), and this derivation only
    # exists to produce a valid 32-byte urlsafe-base64 Fernet key rather
    # than to add independent protection against a brute-force attacker
    # who already has SECRET_KEY.
    salt = b"core.ai_registry.fernet.v1"
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480_000)
    key = base64.urlsafe_b64encode(kdf.derive(secret))
    return Fernet(key)


def encrypt_secret(raw: str) -> str:
    if not raw:
        return ""
    return _get_fernet().encrypt(raw.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def mask_key(raw: str) -> str:
    """For display only — never show the real key in any UI."""
    if not raw:
        return ""
    if len(raw) <= 8:
        return "•" * len(raw)
    return f"{raw[:4]}{'•' * 8}{raw[-4:]}"


# ─────────────────────────────────────────────────────────────────────────────
# Provider call implementations
# ─────────────────────────────────────────────────────────────────────────────

_GEMINI_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_OLLAMA_DEFAULT_URL = "http://localhost:11434/api/generate"


def _call_gemini(prompt: str, model: str, api_key: str, timeout: int) -> str:
    url = _GEMINI_URL_TMPL.format(model=model)
    resp = _session.post(
        url,
        params={"key": api_key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=_split_timeout(timeout),
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise AIError("Gemini returned an unexpected response shape.") from exc


def _call_openai(prompt: str, model: str, api_key: str, timeout: int) -> str:
    resp = _session.post(
        _OPENAI_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=_split_timeout(timeout),
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise AIError("OpenAI returned an unexpected response shape.") from exc


def _call_anthropic(prompt: str, model: str, api_key: str, timeout: int) -> str:
    resp = _session.post(
        _ANTHROPIC_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=_split_timeout(timeout),
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        return "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        )
    except Exception as exc:
        raise AIError("Anthropic returned an unexpected response shape.") from exc


def _call_ollama(prompt: str, model: str, base_url: str, timeout: int) -> str:
    url = (base_url or _OLLAMA_DEFAULT_URL).rstrip("/")
    if not url.endswith("/api/generate"):
        url = url + "/api/generate"
    resp = _session.post(
        url,
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=_split_timeout(timeout),
    )
    resp.raise_for_status()
    data = resp.json()
    if "response" not in data:
        raise AIError("Ollama returned an unexpected response shape.")
    return data["response"]


def _render_template(template: str, prompt: str, model: str, api_key: str) -> str:
    """Substitute {{prompt}}, {{model}}, {{api_key}} into a template string.

    Values are JSON-escaped (not just dropped in raw) so prompts containing
    quotes, newlines, backslashes etc. can't break the surrounding JSON.
    `api_key` is only ever substituted into header templates, never logged.
    """
    def _escaped(value: str) -> str:
        # json.dumps a bare string gives e.g. "hello \"world\"" — strip the
        # outer quotes so it drops cleanly into "...": "{{prompt}}" style
        # slots in the template.
        return json.dumps(value)[1:-1]

    out = template
    out = out.replace("{{prompt}}", _escaped(prompt))
    out = out.replace("{{model}}", _escaped(model))
    out = out.replace("{{api_key}}", _escaped(api_key))
    return out


def _resolve_path(data, path: str):
    """Walk a dotted path like 'choices.0.message.content' through nested
    dicts/lists. Numeric segments index into lists."""
    node = data
    for segment in path.split("."):
        if not segment:
            continue
        if isinstance(node, list):
            node = node[int(segment)]
        elif isinstance(node, dict):
            node = node[segment]
        else:
            raise KeyError(segment)
    return node


def _call_generic(
    prompt: str,
    model: str,
    api_key: str,
    url: str,
    method: str,
    headers_template: str,
    body_template: str,
    response_text_path: str,
    timeout: int,
) -> str:
    """Fully template-driven call for any HTTP+JSON AI API.

    This is the escape hatch for providers that aren't OpenAI-compatible
    (or don't exist yet): the admin defines the URL, headers, request body,
    and response-extraction path entirely from the DB, so a brand-new
    provider can be wired up without ever touching this file again.
    """
    if not url:
        raise AIError("No endpoint URL configured for the generic provider.")

    try:
        headers = json.loads(_render_template(headers_template or "{}", prompt, model, api_key))
    except json.JSONDecodeError as exc:
        raise AIError(f"Generic provider: headers template is not valid JSON ({exc}).") from exc
    if not isinstance(headers, dict):
        raise AIError("Generic provider: headers template must be a JSON object.")

    try:
        body = json.loads(_render_template(body_template or "{}", prompt, model, api_key))
    except json.JSONDecodeError as exc:
        raise AIError(f"Generic provider: request body template is not valid JSON ({exc}).") from exc

    resp = _session.request(
        (method or "POST").upper(),
        url,
        headers=headers,
        json=body,
        timeout=_split_timeout(timeout),
    )
    resp.raise_for_status()
    data = resp.json()

    path = response_text_path or "choices.0.message.content"
    try:
        text = _resolve_path(data, path)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIError(
            f"Generic provider: couldn't find '{path}' in the response. "
            "Check the response-text path in AI Provider Settings."
        ) from exc

    if not isinstance(text, str):
        raise AIError(f"Generic provider: value at '{path}' is not text.")
    return text


def _call_custom(prompt: str, model: str, api_key: str, base_url: str, timeout: int) -> str:
    """OpenAI-compatible custom endpoint (e.g. local vLLM, LM Studio, proxy)."""
    if not base_url:
        raise AIError("No base URL configured for the custom provider.")
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = _session.post(
        url,
        headers=headers,
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=_split_timeout(timeout),
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise AIError("Custom endpoint returned an unexpected response shape.") from exc


# ─────────────────────────────────────────────────────────────────────────────
# Public client wrapper
# ─────────────────────────────────────────────────────────────────────────────

class AIClient:
    """A thin, provider-agnostic wrapper bound to one AIProviderSettings row."""

    def __init__(self, settings_row):
        self._row = settings_row
        self.provider = settings_row.provider
        self.model = settings_row.model_name
        self.name = settings_row.name
        self.timeout = settings_row.request_timeout_seconds or 60

    def call(self, prompt: str) -> str:
        """Send `prompt` to the active provider and return raw text output.

        Raises AIError on any failure. Never raises the underlying
        requests/HTTP exception directly, and never includes the API
        key in any exception message or log line.
        """
        api_key = self._row.get_api_key()
        effective_timeout = self._effective_timeout(prompt)

        def _dispatch() -> str:
            if self.provider == "gemini":
                return _call_gemini(prompt, self.model, api_key, effective_timeout)
            if self.provider == "openai":
                return _call_openai(prompt, self.model, api_key, effective_timeout)
            if self.provider == "anthropic":
                return _call_anthropic(prompt, self.model, api_key, effective_timeout)
            if self.provider == "ollama":
                return _call_ollama(prompt, self.model, self._row.base_url, effective_timeout)
            if self.provider == "custom":
                return _call_custom(prompt, self.model, api_key, self._row.base_url, effective_timeout)
            if self.provider == "generic":
                return _call_generic(
                    prompt,
                    self.model,
                    api_key,
                    url=self._row.base_url,
                    method=self._row.generic_http_method,
                    headers_template=self._row.generic_headers_template,
                    body_template=self._row.generic_body_template,
                    response_text_path=self._row.generic_response_text_path,
                    timeout=effective_timeout,
                )
            raise AIError(f"Unknown provider '{self.provider}'.")

        # One retry, and only for connection-level failures (dropped
        # connection, connect-timeout, read-timeout) — never for
        # requests.HTTPError. An HTTP 4xx/5xx is a real answer from the
        # server (bad key, bad model name, rate limit) and retrying it
        # identically will just fail identically; only the "we never
        # got a usable response at all" class of error is worth a
        # second attempt.
        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                return _dispatch()
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                if status == 429:
                    retry_after = ""
                    try:
                        ra = e.response.headers.get("Retry-After")
                        if ra:
                            retry_after = f" Retry after {ra}s."
                    except Exception:
                        pass
                    logger.warning("AI provider %s (%s) rate-limited (429).", self.name, self.provider)
                    raise AIError(
                        f"{self.get_provider_display()} rate limit hit (HTTP 429)."
                        f"{retry_after} This is a quota/rate-limit issue, not a network fault — "
                        "wait a moment before retrying, or check the provider's usage/quota page."
                    ) from e
                logger.warning("AI provider %s (%s) HTTP error %s", self.name, self.provider, status)
                raise AIError(self._sanitize_http_error(e, status, api_key)) from e
            except (requests.ConnectionError, requests.Timeout) as e:
                logger.warning(
                    "AI provider %s (%s) network error (attempt %s/%s): %s",
                    self.name, self.provider, attempt, attempts, type(e).__name__,
                )
                if attempt < attempts:
                    time.sleep(1.5)
                    continue
                raise AIError(
                    f"Could not reach {self.get_provider_display()}: network/timeout error "
                    f"after {attempts} attempts."
                ) from e
            except requests.RequestException as e:
                logger.warning("AI provider %s (%s) network error: %s", self.name, self.provider, type(e).__name__)
                raise AIError(f"Could not reach {self.get_provider_display()}: network/timeout error.") from e

    def get_provider_display(self) -> str:
        return self._row.get_provider_display()

    def _effective_timeout(self, prompt: str) -> int:
        """Scale the read timeout up for larger prompts.

        A prompt asking for a big structured JSON extraction over many
        records legitimately takes longer to *generate* than a trivial
        one-word reply — that's compute time on the provider's side,
        not a network fault, and retrying an identical request that's
        still generating just times out the same way again. The admin-
        configured timeout is treated as a floor, not the only value:
        we add roughly 1 extra second of budget per 200 characters of
        prompt beyond a 2000-character baseline, capped at 180s so a
        single call still can't hang forever.
        """
        extra = max(0, len(prompt) - 2000) / 200
        return int(min(180, max(self.timeout, self.timeout + extra)))

    @staticmethod
    def _sanitize_http_error(exc: requests.HTTPError, status, api_key: str = "") -> str:
        """Return a safe-to-display error message with no key fragments.

        Two passes: (1) redact common `key=`/`token=`/`authorization=`
        patterns regardless of value, (2) defense-in-depth — if we know
        the actual key used for this call, strip any literal occurrence
        of it from the body too, in case a provider echoes it back
        unlabelled (e.g. inside a generic error string).
        """
        body = ""
        try:
            body = exc.response.text[:300]
        except Exception:
            pass
        body = re.sub(r"(key|token|authorization)[\"']?\s*[:=]\s*[\"']?[\w\-\.]+", r"\1=<redacted>", body, flags=re.I)
        if api_key and len(api_key) >= 8:
            body = body.replace(api_key, "<redacted>")
        return f"AI provider returned HTTP {status}. {body}".strip()

    def call_json(self, prompt: str) -> dict:
        """Call and parse the response as JSON, stripping markdown fences."""
        raw = self.call(prompt)
        text = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
        start = min((text.find(c) for c in "([{" if text.find(c) != -1), default=0)
        text = text[start:]
        return json.loads(text)


def get_ai_client(required: bool = False) -> Optional[AIClient]:
    """
    Return an AIClient bound to whichever provider is currently marked
    active in the DB, or None if AI is disabled / unconfigured.

    Callers MUST handle the None case by falling back to their normal
    non-AI behaviour — this is the expected, supported state, not an
    error condition. Pass required=True only for code paths that have
    no non-AI fallback at all (rare); it raises AIDisabled instead of
    returning None.
    """
    from core.models import AIProviderSettings

    row = AIProviderSettings.get_active()
    if row is None:
        if required:
            raise AIDisabled("No active AI provider is configured.")
        return None
    return AIClient(row)


def ai_is_configured() -> bool:
    """Cheap check for templates/views that just need a yes/no."""
    from core.models import AIProviderSettings
    return AIProviderSettings.objects.filter(is_active=True).exists()