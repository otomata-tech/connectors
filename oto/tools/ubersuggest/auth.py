"""Ubersuggest auth — signing in a PERSON with their Ubersuggest account (OAuth 2.1,
authorization code + PKCE), single source for the lib's Ubersuggest client.

Ubersuggest's programmatic surface is its remote MCP server; its authorization server
lives on the same host and follows the MCP authorization profile:

- **public client** (no client secret), PKCE S256, grants `authorization_code` +
  `refresh_token`;
- the client is obtained by **dynamic registration** (RFC 7591, `register_client`):
  the consumer registers its redirect URIs once and keeps the returned `client_id`;
- the resource indicator (RFC 8707) is the MCP endpoint, sent on every request.

Four operations, one per authorization-server endpoint:
- `register_client` — redirect URIs for a `client_id`;
- `authorize_url` — the URL to send the browser to;
- `exchange_code` — the returned code (plus the PKCE verifier) for a `Grant`;
- `refresh` — a `refresh_token` for a fresh `Grant`. The server may rotate the
  refresh token: the one returned replaces the old one, the caller must store it.

Same guards as the lib's other auth modules: tokens go in **`data=`**, never in
`params=`, and no `raise_for_status()` (its message embeds the URL).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlencode

import requests

from ..common.credentials import require

_HTTP_TIMEOUT = (10, 30)  # (connect, read)
HOST = "https://ubersuggest-mcp.neilpatelapi.com"
MCP_URL = f"{HOST}/mcp"
AUTHORIZE_URL = f"{HOST}/authorize"
TOKEN_URL = f"{HOST}/token"
REGISTER_URL = f"{HOST}/register"
#: RFC 8707 resource indicator = the protected resource the server advertises.
RESOURCE = MCP_URL

#: Every scope the server advertises. What comes back still depends on the plan.
SCOPES = ("profile", "domain", "keywords", "serp", "backlinks", "site_audit",
          "content", "projects", "utility")

_GRANT_MORT = ("invalid_grant",)


class UbersuggestAuthError(ValueError):
    """Refusal by the Ubersuggest authorization server. Carries a `status_code` (the
    `UpstreamHTTPError` contract)."""

    def __init__(self, message: str, *, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code


class UbersuggestGrantExpired(UbersuggestAuthError):
    """The person's authorization is no longer valid (revoked, expired): they must
    reconnect."""


@dataclass(frozen=True)
class Grant:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str


def register_client(redirect_uris: Iterable[str], *, client_name: str) -> str:
    """Registers a public client for these redirect URIs; returns its `client_id`."""
    uris = [u for u in redirect_uris if u]
    if not uris:
        raise ValueError("redirect_uris is empty")
    resp = requests.post(REGISTER_URL, timeout=_HTTP_TIMEOUT, json={
        "client_name": require(client_name, "client_name"),
        "redirect_uris": uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": " ".join(SCOPES),
    })
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    client_id = payload.get("client_id") if resp.status_code < 400 else None
    if not client_id:
        detail = payload.get("error_description") or payload.get("error") or "no client_id"
        raise UbersuggestAuthError(
            f"Ubersuggest refused the client registration (HTTP {resp.status_code}): {detail}",
            status_code=resp.status_code if resp.status_code >= 400 else 502)
    return str(client_id)


def authorize_url(client_id: str, redirect_uri: str, state: str, code_challenge: str, *,
                  scopes: tuple[str, ...] = SCOPES) -> str:
    """The URL of the Ubersuggest sign-in dialog (PKCE S256: the caller keeps the
    verifier and hands it back to `exchange_code`)."""
    params = {
        "client_id": require(client_id, "UBERSUGGEST_CLIENT_ID"),
        "response_type": "code",
        "redirect_uri": require(redirect_uri, "redirect_uri"),
        "scope": " ".join(scopes),
        "state": require(state, "state"),
        "code_challenge": require(code_challenge, "code_challenge"),
        "code_challenge_method": "S256",
        "resource": RESOURCE,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _token(data: dict) -> Grant:
    resp = requests.post(TOKEN_URL, data=data, timeout=_HTTP_TIMEOUT,
                         headers={"Accept": "application/json"})
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if resp.status_code >= 400 or "access_token" not in payload:
        error = str(payload.get("error") or "")
        detail = str(payload.get("error_description") or "") or error or "response without a token"
        status = resp.status_code if resp.status_code >= 400 else 401
        cls = UbersuggestGrantExpired if error in _GRANT_MORT else UbersuggestAuthError
        raise cls(f"Ubersuggest refused the token request (HTTP {resp.status_code}): {detail}",
                  status_code=status)
    return Grant(access_token=payload["access_token"],
                 refresh_token=payload.get("refresh_token") or data.get("refresh_token") or "",
                 expires_in=int(payload.get("expires_in") or 3600),
                 scope=str(payload.get("scope") or ""))


def exchange_code(client_id: str, code: str, redirect_uri: str, code_verifier: str) -> Grant:
    """The code brought back by the browser for a `Grant` (with refresh token)."""
    return _token({  # ⚠️ `data=`, NEVER `params=`
        "grant_type": "authorization_code",
        "client_id": require(client_id, "UBERSUGGEST_CLIENT_ID"),
        "code": require(code, "code"),
        "redirect_uri": require(redirect_uri, "redirect_uri"),
        "code_verifier": require(code_verifier, "code_verifier"),
        "resource": RESOURCE,
    })


def refresh(client_id: str, refresh_token: str) -> Grant:
    """A fresh `Grant`. ⚠️ Its `refresh_token` may replace the one passed in: store it."""
    return _token({  # ⚠️ `data=`, NEVER `params=`
        "grant_type": "refresh_token",
        "client_id": require(client_id, "UBERSUGGEST_CLIENT_ID"),
        "refresh_token": require(refresh_token, "UBERSUGGEST_REFRESH_TOKEN"),
        "resource": RESOURCE,
    })
