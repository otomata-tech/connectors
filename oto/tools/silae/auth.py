"""Silae access token — OAuth2 `client_credentials` on the Azure AD B2C tenant.

- The secrets travel in the form body (`data=`), never in the query string, and no
  `raise_for_status()` is called on the token endpoint: an exception message must
  never carry a credential. On a refusal, only the OAuth error CODE is kept — the
  `error_description` echoes the client id.
- The token lives 60 minutes and Silae caps token requests at 60 per minute: the
  cache is process-wide, keyed by a hash of the credential, because a server builds
  one client per call — a cache held by the instance would never be reused.
"""
from __future__ import annotations

import hashlib
import time
from typing import Optional

import requests

from ..common import UpstreamHTTPError

AUTH_URL = "https://payroll-api-auth.silae.fr/oauth2/v2.0/token"
#: Fixed scope of the payroll API, given by the Silae authentication page.
SCOPE = "https://silaecloudb2c.onmicrosoft.com/36658aca-9556-41b7-9e48-77e90b006f34/.default"

_HTTP_TIMEOUT = 30

# {cred_key: (access_token, expires_at_epoch)}
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}

# OAuth2 error codes that mean "this credential does not authenticate".
_REFUS = {"invalid_client", "invalid_grant", "unauthorized_client", "invalid_scope"}


class SilaeAuthError(UpstreamHTTPError):
    """The token endpoint refused the client id / secret.

    Always `status_code == 401` whatever the endpoint answered, so that a consumer
    routes it as a credential refusal, not as bad input."""

    def __init__(self, error: str):
        super().__init__(401, {"error": error}, service="silae")


def cred_key(client_id: str, client_secret: str) -> str:
    """Opaque, stable id of a credential — never a secret in clear."""
    return hashlib.sha256(f"{client_id}|{client_secret}".encode()).hexdigest()


def _json(resp) -> Optional[dict]:
    try:
        payload = resp.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _demander(session: requests.Session, client_id: str, client_secret: str) -> tuple[str, int]:
    resp = session.post(
        AUTH_URL,
        data={  # body, never params= — see module docstring
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": SCOPE,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=_HTTP_TIMEOUT,
    )
    payload = _json(resp)
    error = payload.get("error") if payload else None
    if resp.status_code >= 400:
        if resp.status_code == 401 or error in _REFUS:
            raise SilaeAuthError(error or "unauthorized")
        raise UpstreamHTTPError(resp.status_code, {"error": error or "token endpoint error"},
                                service="silae")
    if not payload or not payload.get("access_token"):
        raise UpstreamHTTPError(502, {"error": "no access_token in token response"},
                                service="silae")
    return payload["access_token"], int(payload.get("expires_in") or 3600)


def get_access_token(session: requests.Session, client_id: str, client_secret: str) -> str:
    """A valid bearer token for this credential, requested only when needed."""
    key = cred_key(client_id, client_secret)
    cached = _TOKEN_CACHE.get(key)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    token, ttl = _demander(session, client_id, client_secret)
    _TOKEN_CACHE[key] = (token, time.time() + ttl)
    return token


def invalidate(client_id: str, client_secret: str) -> None:
    """Forget the cached token of this credential (called on an API 401)."""
    _TOKEN_CACHE.pop(cred_key(client_id, client_secret), None)
