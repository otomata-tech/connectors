"""Microsoft Entra auth — signing in a PERSON (OAuth 2.0, authorization code),
single source for ALL of the lib's Microsoft clients.

The application is the one of the publisher that consumes the lib, registered once as
"multi-tenant" in its own Entra directory: `client_id` + `client_secret`,
passed as arguments. Each person authorizes it from THEIR OWN Microsoft 365 account;
the token returned acts with THEIR rights (DELEGATED permissions), no more, no less.

Three operations, one per authorization-server endpoint:
- `authorize_url` — the URL to send the browser to;
- `exchange_code` — the returned code in exchange for a `Grant`;
- `refresh` — a `refresh_token` in exchange for a fresh `Grant`. ⚠️ Entra ROTATES the
  refresh token: the one returned replaces the old one, the caller must store it.

`organizations` endpoint: work and school accounts only
(SharePoint and OneDrive for Business), not personal Microsoft accounts.

Same guards as the lib's other auth modules: the secret goes in **`data=`**, never in
`params=`, and no `raise_for_status()` (its message embeds the URL).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import requests

from ..common.credentials import require

_HTTP_TIMEOUT = (10, 30)  # (connect, read)
_AUTHORITY = "https://login.microsoftonline.com/organizations/oauth2/v2.0"
AUTHORIZE_URL = f"{_AUTHORITY}/authorize"
TOKEN_URL = f"{_AUTHORITY}/token"

#: SharePoint and OneDrive files, read-write, on behalf of the person, and
#: `offline_access` to obtain a refresh token. `User.Read` gives their identity.
FILES_SCOPES = ("offline_access", "User.Read",
                "https://graph.microsoft.com/Files.ReadWrite.All",
                "https://graph.microsoft.com/Sites.ReadWrite.All")

# AADSTS codes meaning "the authorization is dead, reconnect".
_GRANT_MORT = ("invalid_grant", "interaction_required")


class MicrosoftAuthError(ValueError):
    """Refusal by the Entra authorization server. Carries a `status_code` (the
    `UpstreamHTTPError` contract). `code` = the AADSTS code when readable
    (e.g. `AADSTS7000215`, invalid application secret), otherwise `None`."""

    def __init__(self, message: str, *, status_code: int = 401, code: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class MicrosoftGrantExpired(MicrosoftAuthError):
    """The person's authorization is no longer valid (revoked, expired, password
    changed, MFA required again…): they must reconnect. The application's
    configuration is not at fault."""


@dataclass(frozen=True)
class Grant:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str


def _aadsts(description: str) -> Optional[str]:
    """`AADSTS7000215` depuis une `error_description` Entra, ou `None`."""
    head = (description or "").split(":", 1)[0].strip()
    return head if head.startswith("AADSTS") else None


def authorize_url(client_id: str, redirect_uri: str, state: str, *,
                  scopes: tuple[str, ...] = FILES_SCOPES,
                  login_hint: Optional[str] = None) -> str:
    """The URL of the Microsoft sign-in dialog. `prompt=select_account`: a
    person with several accounts picks the right one instead of being signed
    in automatically with the one from their browser."""
    params = {
        "client_id": require(client_id, "MICROSOFT_CLIENT_ID"),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": " ".join(scopes),
        "state": state,
        "prompt": "select_account",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _token(data: dict) -> Grant:
    resp = requests.post(TOKEN_URL, data=data, timeout=_HTTP_TIMEOUT)
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if resp.status_code >= 400 or "access_token" not in payload:
        description = str(payload.get("error_description") or "")
        # First line only: the rest carries a Trace ID and a timestamp.
        first_line = description.splitlines()[0] if description else ""
        error = str(payload.get("error") or "")
        detail = first_line or error or "response without a token"
        status = resp.status_code if resp.status_code >= 400 else 401
        cls = MicrosoftGrantExpired if error in _GRANT_MORT else MicrosoftAuthError
        raise cls(f"Entra refused the token request (HTTP {resp.status_code}): {detail}",
                  status_code=status, code=_aadsts(description))
    return Grant(access_token=payload["access_token"],
                 refresh_token=payload.get("refresh_token") or data.get("refresh_token") or "",
                 expires_in=int(payload.get("expires_in", 3600)),
                 scope=str(payload.get("scope") or ""))


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str, *,
                  scopes: tuple[str, ...] = FILES_SCOPES) -> Grant:
    """The code brought back by the browser in exchange for a `Grant` (with refresh token)."""
    return _token({  # ⚠️ `data=`, JAMAIS `params=`
        "grant_type": "authorization_code",
        "client_id": require(client_id, "MICROSOFT_CLIENT_ID"),
        "client_secret": require(client_secret, "MICROSOFT_CLIENT_SECRET"),
        "code": require(code, "code"),
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
    })


def refresh(client_id: str, client_secret: str, refresh_token: str, *,
            scopes: tuple[str, ...] = FILES_SCOPES) -> Grant:
    """A fresh `Grant`. ⚠️ Its `refresh_token` replaces the one passed in: store it."""
    return _token({  # ⚠️ `data=`, JAMAIS `params=`
        "grant_type": "refresh_token",
        "client_id": require(client_id, "MICROSOFT_CLIENT_ID"),
        "client_secret": require(client_secret, "MICROSOFT_CLIENT_SECRET"),
        "refresh_token": require(refresh_token, "MICROSOFT_REFRESH_TOKEN"),
        "scope": " ".join(scopes),
    })
