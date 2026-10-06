"""Zoho OAuth2 auth — single source of token refresh for ALL Zoho products
(CRM, Desk, Analytics).

Two incidents motivated the factoring-out (the three clients duplicated this block):

- **Secrets leak (#284, CRITICAL)**: the refresh passed the credentials as
  `params=`, hence in the QUERY STRING. `raise_for_status()` then raises an
  `HTTPError` whose message contains the full URL — `client_id`,
  `client_secret` AND `refresh_token` in clear text ended up in the agent
  transcript, the logs and any export. Here the credentials go in **`data=`**
  (form-encoded body, the form prescribed by RFC 6749 §2.3.1): they are no
  longer in the URL, hence in no error message, nor in Zoho's access logs.
  As defense in depth, we do NOT call `raise_for_status()`: we build a
  redacted message ourselves.

- **Refresh rate limit (#233 then #285)**: server-side a NEW client instance
  is created on EVERY MCP call → an instance-held cache never helps → one
  refresh per call → Zoho rate-limits `/oauth/v2/token` and EVERYTHING
  breaks for several minutes. The cache is therefore **process-wide**, keyed
  by credential. The fix had only been applied to Analytics; moving it here
  gives it to all three products at once.

Cache key = **hash** of `accounts_url|client_id|refresh_token`: isolates the
credentials from each other (never a token shared between two orgs/users)
without ever using a clear-text secret as a dictionary key.
"""
from __future__ import annotations

import hashlib
import time
from typing import Callable, Optional

import requests

# {cred_key: (access_token, expires_at_epoch)}
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}

_SAFE_ERR = ("Zoho OAuth refresh failed (HTTP {status}) on {host}: {detail}. "
             "Check the connector's client_id / client_secret / refresh_token "
             "and region (data_center).")


class ZohoAuthError(ValueError):
    """Zoho OAuth refusal (invalid_client / invalid_code / invalid_grant…).

    Zoho answers HTTP 200 with the error in the body — so we carry a synthetic
    401 `status_code` (`UpstreamHTTPError` contract) so that consumers classify
    this credential refusal as a handled error, not a bug. Subclass of
    `ValueError`: existing `except ValueError` clauses still hold.
    """

    status_code = 401


def cred_key(accounts_url: str, client_id: str, refresh_token: str) -> str:
    """Opaque, stable identifier of a credential (never a clear-text secret)."""
    return hashlib.sha256(
        f"{accounts_url}|{client_id}|{refresh_token}".encode()).hexdigest()


def _host(url: str) -> str:
    """`accounts.zoho.eu` from a URL — safe to display (no secret)."""
    return (url or "").split("//")[-1].split("/")[0] or "accounts.zoho.com"


def get_access_token(accounts_url: str, client_id: str, client_secret: str,
                     refresh_token: str, *, key: Optional[str] = None,
                     on_refresh: Optional[Callable[[dict], None]] = None) -> str:
    """Valid access token for this credential, refreshed only when necessary.

    No secret travels through the URL or the error messages.

    `on_refresh(token_data)` is called after every SUCCESSFUL refresh, with the
    full response of the authorization server. Symmetric to the `on_refresh` of
    the Salesforce client, and for the same reason: it is the only moment when
    the caller learns that this credential REALLY authenticates, right now.

    ⚠️ It is NOT called on a cache hit. A token that is still valid proves that
    a refresh worked a while ago, not that the credential works now: using it as
    proof of life would clear a flag on stale information — exactly what
    clearing a flag must not do.

    Best-effort: a failure of the callback does not fail a call whose token is
    valid.
    """
    # TWO-step connection (server-based mode): the app is set, consent not yet
    # given → no refresh token. We say so clearly here rather than letting Zoho
    # answer an incomprehensible `invalid_client`.
    if not refresh_token:
        raise ZohoAuthError(
            "Zoho connection incomplete: the app is configured but authorization "
            "has not been granted yet. Open the connector and click \"Connect "
            "with Zoho\" (or paste a refresh token if you use a self client).")

    k = key or cred_key(accounts_url, client_id, refresh_token)
    cached = _TOKEN_CACHE.get(k)
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    resp = requests.post(
        f"{accounts_url}/oauth/v2/token",
        data={  # ⚠️ `data=`, NEVER `params=`: secrets must not end up
                # in the URL (see #284, module docstring).
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        timeout=30,
    )

    # No `raise_for_status()`: its message embeds the request URL.
    if resp.status_code >= 400:
        detail = "authorization server refusal"
        try:
            payload = resp.json()
            detail = payload.get("error") or payload.get("message") or detail
        except ValueError:
            pass
        raise ZohoAuthError(_SAFE_ERR.format(
            status=resp.status_code, host=_host(accounts_url), detail=detail))

    try:
        token_data = resp.json()
    except ValueError:
        raise ZohoAuthError(_SAFE_ERR.format(
            status=resp.status_code, host=_host(accounts_url),
            detail="unreadable response (JSON expected)"))

    # Zoho returns HTTP 200 + {"error": "invalid_client"} on a wrong region or
    # client, and invalid_code / invalid_grant on a dead refresh token.
    if "error" in token_data:
        raise ZohoAuthError(f"Zoho OAuth error: {token_data['error']}")
    if "access_token" not in token_data:
        raise ZohoAuthError(_SAFE_ERR.format(
            status=resp.status_code, host=_host(accounts_url),
            detail="no access_token in the response"))

    token = token_data["access_token"]
    _TOKEN_CACHE[k] = (token, time.time() + int(token_data.get("expires_in", 3600)))
    if on_refresh is not None:
        try:
            on_refresh(token_data)
        # noqa: SILENT — caller's side effect: its failure does not break the call
        except Exception:  # noqa: BLE001 — the token itself is valid
            pass
    return token


def invalidate(key: str) -> None:
    """Forget this credential's cached token (called on an upstream 401)."""
    _TOKEN_CACHE.pop(key, None)
