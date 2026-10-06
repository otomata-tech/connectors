"""Acquiring the authorization — Facebook Login for Business.

1. `authorize_url`: the dialog, with the application's `config_id` (which fixes
   the permissions AND the token type); Meta comes back with a `code`.
2. `connect`: the code becomes a token, then we read WHO authorized.

Recommended configuration on the Meta side: "business integration system user"
token (BISU). It does not expire by default — so no renewal to maintain,
unlike `instagram_meta`'s 60-day user token. If the
configuration nonetheless issues a token with a lifetime, `expires_in` says so and
the caller stores the expiry.

The anti-replay `state` is signed and verified by the caller, not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import requests

from . import _transport
from .config import DIALOG_URL, GRAPH_API_BASE, HTTP_TIMEOUT, TOKEN_URL, MetaAdsApp
from .errors import MetaAdsAuthRefused


@dataclass(frozen=True)
class MetaAdsGrant:
    """What a successful consent produces.

    `expires_in` = seconds, or `None` for a token with no expiry (BISU).
    `client_business_id` only exists for a BISU token: it is the business
    portfolio of the client who authorized."""

    access_token: str
    expires_in: Optional[int]
    user_id: str
    name: str
    client_business_id: str


def authorize_url(app: MetaAdsApp, redirect_uri: str, state: str) -> str:
    """The dialog URL. `redirect_uri` must be declared byte-for-byte at Meta."""
    if not redirect_uri:
        raise ValueError("redirect_uri is required.")
    if not state:
        raise ValueError("state is required: without it a callback cannot be tied "
                         "to a person, and nothing prevents a replay.")
    return DIALOG_URL + "?" + urlencode({
        "client_id": app.app_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "config_id": app.config_id,
        "response_type": "code",
        # Required for a BISU token: without it, the dialog returns a user
        # token in the fragment instead of a code.
        "override_default_response_type": "true",
    })


def connect(app: MetaAdsApp, code: str, redirect_uri: str,
            *, session: Optional[requests.Session] = None) -> MetaAdsGrant:
    """The returned code becomes a token, plus the identity of who issued it."""
    if not code:
        raise ValueError("code is required: Meta sends it back on the callback URL.")
    http = session or requests
    # The code and secret go in a form-encoded BODY, never in the query string:
    # a URL ends up in the logs on both sides.
    r = http.post(TOKEN_URL, data={
        "client_id": app.app_id,
        "client_secret": app.app_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        _transport.refus_de_consentement(r, "the authorization code exchange")
    echange = _transport.corps(r) or {}
    jeton = echange.get("access_token") if isinstance(echange, dict) else None
    if not jeton:
        raise MetaAdsAuthRefused("Meta returned no access token on code exchange.")
    expires_in = echange.get("expires_in")
    expires_in = int(expires_in) if expires_in else None

    # `client_business_id` only exists on a system user: requested with a user
    # token, Graph rejects the field — we then fall back to id,name.
    entetes = {"Authorization": f"Bearer {jeton}"}
    r = http.get(f"{GRAPH_API_BASE}/me", params={"fields": "id,name,client_business_id"},
                 headers=entetes, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        r = http.get(f"{GRAPH_API_BASE}/me", params={"fields": "id,name"},
                     headers=entetes, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        _transport.refus_de_consentement(r, "reading the authorized identity")
    moi = _transport.corps(r) or {}
    return MetaAdsGrant(
        access_token=str(jeton), expires_in=expires_in,
        user_id=str(moi.get("id") or ""), name=str(moi.get("name") or ""),
        client_business_id=str(moi.get("client_business_id") or ""))
