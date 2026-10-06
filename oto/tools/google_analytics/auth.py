"""Google auth via SERVICE ACCOUNT KEY — the GA4 access token.

The flow is OAuth 2.0's "JWT bearer" (RFC 7523): we sign an assertion
with the service account's private key and exchange it for a one-hour access token
at Google's token endpoint. No human consent, no
refresh token.

Three rules upheld here:

- **A single token host, `oauth2.googleapis.com`.** The JSON key carries its own
  `token_uri`; following it without checking would make a field entered by a third party the
  destination of a request carrying a signed assertion (SSRF). A key
  whose `token_uri` differs is refused at read time.
- **The secret never leaves.** The assertion goes in the body (`data=`), never in the
  query string; no `raise_for_status()` (its message embeds the URL); the
  message of a refusal only quotes what Google answered, never the request.
- **Process-wide cache, keyed by credential.** The server builds one client per
  tool call: a cache held by the instance would never serve. The cache key
  is a FINGERPRINT (sha256) of the credential and the scope — never a plaintext secret
  as a dictionary key, never a token shared between two keys.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Union

import requests
from google.auth import crypt, jwt

from ..common import UpstreamHTTPError

#: The only token endpoint accepted (see the module docstring).
TOKEN_URI = "https://oauth2.googleapis.com/token"

#: Google Analytics read-only — the only scope this connector requests.
SCOPE_READONLY = "https://www.googleapis.com/auth/analytics.readonly"

_JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
_ASSERTION_TTL_S = 3600
_TOKEN_TIMEOUT = (10, 30)
# A token that expires in less than a minute is renewed before the call.
_MARGE_S = 60

# {empreinte: (access_token, expires_at_epoch)}
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_LOCK = threading.Lock()

_CHAMPS_REQUIS = ("client_email", "private_key")


class ServiceAccountAuthError(UpstreamHTTPError):
    """Google refuses to issue a token for this key (revoked, deleted,
    clock skew, service account disabled…).

    Carries a synthetic 401 `status_code` whatever the upstream code (400
    `invalid_grant` most often): for any consumer, it is a CREDENTIAL
    refusal, not a malformed request. `body` = what Google answered
    (`error`, `error_description`), which contains no secret."""

    def __init__(self, upstream_status: int, body: Any):
        self.upstream_status = upstream_status
        super().__init__(401, body, service="google_analytics")


def parse_service_account_key(raw: Union[str, bytes, dict]) -> dict:
    """The service account's JSON key, validated — or a `ValueError` that says
    what to paste instead.

    Accepts the file content (text) or the already-loaded dict. Explicitly refuses
    the two likely mix-ups: an OAuth client JSON
    (`installed`/`web`) and a key missing its private key."""
    if isinstance(raw, (str, bytes)):
        try:
            key = json.loads(raw)
        except ValueError:
            raise ValueError(
                "The service account key must be the JSON content of the file "
                "downloaded from Google Cloud (IAM → Service accounts → Keys → "
                "Add key → JSON) — the text provided is not JSON.") from None
    else:
        key = raw
    if not isinstance(key, dict):
        raise ValueError("The service account key must be a JSON object.")
    if "installed" in key or "web" in key:
        raise ValueError(
            "This JSON is an OAuth client ID, not a service account "
            "key. You need a service account's JSON key (Google Cloud → IAM "
            "→ Service accounts → Keys).")
    if key.get("type") != "service_account":
        raise ValueError(
            f"The key provided is of type {key.get('type')!r}, not `service_account` "
            "— you need a service account's JSON key.")
    manquants = [c for c in _CHAMPS_REQUIS if not key.get(c)]
    if manquants:
        raise ValueError(
            f"Incomplete service account key: {', '.join(manquants)} missing. "
            "Paste the entire JSON file again, as Google downloaded it.")
    token_uri = key.get("token_uri") or TOKEN_URI
    if token_uri != TOKEN_URI:
        raise ValueError(
            f"Unexpected `token_uri` in the key ({token_uri!r}): only {TOKEN_URI} is "
            "accepted.")
    _signer(key)  # an unreadable private key is refused at read time, not on the 1st call
    return key


def cred_key(key: dict, scope: str) -> str:
    """Stable fingerprint of a credential and a scope (never a plaintext secret)."""
    return hashlib.sha256(
        "|".join((key["client_email"], key.get("private_key_id") or "",
                  key["private_key"], scope)).encode()).hexdigest()


def _signer(key: dict):
    try:
        return crypt.RSASigner.from_service_account_info(key)
    except (ValueError, TypeError) as e:
        raise ValueError(
            "The service account's private key is unreadable "
            f"({type(e).__name__}) — paste the entire JSON file again, unmodified."
        ) from None


def _signed_assertion(key: dict, scope: str, now: int) -> str:
    signer = _signer(key)
    payload = {"iss": key["client_email"], "scope": scope, "aud": TOKEN_URI,
               "iat": now, "exp": now + _ASSERTION_TTL_S}
    token = jwt.encode(signer, payload)
    return token.decode() if isinstance(token, bytes) else token


def access_token(key: dict, *, session: requests.Session,
                 scope: str = SCOPE_READONLY) -> str:
    """A valid access token for this key, issued only if the cache does not have
    one that lives at least one more minute.

    `key` must come out of `parse_service_account_key`."""
    k = cred_key(key, scope)
    with _LOCK:
        cached = _TOKEN_CACHE.get(k)
        if cached and cached[1] > time.time() + _MARGE_S:
            return cached[0]

    now = int(time.time())
    resp = session.post(
        TOKEN_URI,
        # ⚠️ `data=`, NEVER `params=`: the signed assertion must not land
        # in a URL (exception message, logs, Sentry).
        data={"grant_type": _JWT_BEARER, "assertion": _signed_assertion(key, scope, now)},
        timeout=_TOKEN_TIMEOUT,
    )
    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        body = {k2: payload[k2] for k2 in ("error", "error_description")
                if isinstance(payload, dict) and k2 in payload}
        raise ServiceAccountAuthError(resp.status_code, body or "token server refusal")
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise ServiceAccountAuthError(resp.status_code, "response without access_token")
    expires_in = int(data.get("expires_in") or 0)
    with _LOCK:
        _TOKEN_CACHE[k] = (token, now + expires_in)
    return token
