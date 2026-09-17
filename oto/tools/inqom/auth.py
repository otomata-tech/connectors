"""Inqom OAuth2 token — Resource Owner Password Credentials grant.

`POST {token_url}` with `grant_type=password`, the application's
`client_id`/`client_secret` and the Inqom account's `username`/`password`.
The token carries that account's rights: every call made with it sees exactly
what that account sees.

- Secrets travel in the form body (`data=`), never in the query string, and no
  `raise_for_status()` is called here: an exception message must never carry a
  credential.
- The cache is process-wide, keyed by a hash of the credential: a server builds
  one client per call, so a cache held by the instance would never be reused.
"""
from __future__ import annotations

import hashlib
import time
from typing import Optional

import requests

from ..common import UpstreamHTTPError

_HTTP_TIMEOUT = (10, 30)

# {cred_key: (access_token, expires_at_epoch)}
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}

# OAuth2 error codes that mean "this credential does not authenticate".
_REFUS = {"invalid_grant", "invalid_client", "unauthorized_client", "invalid_scope"}


class InqomAuthError(UpstreamHTTPError):
    """The token endpoint refused the credential.

    Always `status_code == 401` whatever the endpoint answered (400 or 401), so
    that a consumer routes it as a credential refusal, not as bad input.
    """

    def __init__(self, error: str):
        super().__init__(401, {"error": error}, service="inqom")


def cred_key(token_url: str, client_id: str, username: str, secret_material: str) -> str:
    """Opaque, stable id of a credential — never a secret in clear."""
    return hashlib.sha256(
        f"{token_url}|{client_id}|{username}|{secret_material}".encode()).hexdigest()


def get_access_token(token_url: str, *, client_id: str, client_secret: str,
                     username: str, password: str, scope: str,
                     key: Optional[str] = None) -> str:
    """A valid access token for this credential, requested only when needed."""
    k = key or cred_key(token_url, client_id, username, client_secret + "|" + password)
    cached = _TOKEN_CACHE.get(k)
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    resp = requests.post(
        token_url,
        data={  # body, never params= — see module docstring
            "grant_type": "password",
            "client_id": client_id,
            "client_secret": client_secret,
            "username": username,
            "password": password,
            "scope": scope,
        },
        timeout=_HTTP_TIMEOUT,
    )

    try:
        payload = resp.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None

    if resp.status_code >= 400:
        if resp.status_code == 401 or error in _REFUS:
            raise InqomAuthError(error or "unauthorized")
        # Only the OAuth error code is kept: the raw body is never echoed.
        raise UpstreamHTTPError(resp.status_code, {"error": error or "token endpoint error"},
                                service="inqom")

    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise UpstreamHTTPError(502, {"error": "no access_token in token response"},
                                service="inqom")

    token = payload["access_token"]
    _TOKEN_CACHE[k] = (token, time.time() + int(payload.get("expires_in") or 3600))
    return token


def invalidate(key: str) -> None:
    """Forget the cached token of this credential (called on an upstream 401)."""
    _TOKEN_CACHE.pop(key, None)
