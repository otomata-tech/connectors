"""Acquiring the authorization — from the user's click to the 60-day token.

Three steps, in this order, and none is optional:

1. the authorization dialog (`authorize_url`), which the caller opens in a
   browser; Meta comes back to the return URL with a `code`;
2. `connect(app, code, redirect_uri)` — the code becomes a SHORT-lived token (one hour),
   which the same function immediately exchanges for a LONG-lived token (60 days), then
   it reads the account identity.

This module is the only one in the package that knows the `InstagramApp`: the App ID and
secret are only used here. The data client only needs the token.

⚠️ **The anti-replay state (`state`) is NOT fabricated here.** It is signed and verified
by the caller, who alone knows FOR WHOM the consent is requested (a user
identifier, an organization) and has a signing secret. A lib that
invented one would make it verifiable by itself alone,
hence useless to whoever needs it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from . import _transport
from .config import (
    AUTHORIZE_URL,
    GRAPH_API_BASE,
    GRAPH_ROOT,
    HTTP_TIMEOUT,
    LONG_LIVED_TTL_DAYS,
    SCOPES,
    TOKEN_URL,
    InstagramApp,
)
from .errors import InstagramAuthRefused


@dataclass(frozen=True)
class InstagramGrant:
    """What a successful consent produces, and which must be stored somewhere.

    `expires_in` is in seconds, as Meta returns it — the caller derives the
    expiry date it will store. That date will then decide
    renewal (`tokens.needs_refresh`): without it, we can only suffer
    the expiry."""

    access_token: str
    user_id: str
    username: str
    expires_in: int


def authorize_url(app: InstagramApp, redirect_uri: str, state: str) -> str:
    """The consent dialog URL, for THIS application and THIS return.

    `redirect_uri` must be declared byte for byte in the Meta application: it is
    the most frequent configuration mistake of this flow, and Meta flags it with
    a generic error screen that doesn't name it.

    ⚠️ Permissions are sent separated by COMMAS. Ordinary OAuth2 separates them
    by spaces, and a URL built "as usual" gets here
    a consent for a single permission — hence a token that reads the profile and
    refuses insights, much further down the line."""
    if not redirect_uri:
        raise ValueError("redirect_uri required: Meta refuses a dialog with no return.")
    if not state:
        raise ValueError(
            "state required: without it, a consent return can be tied neither "
            "to a person nor to a request, and nothing prevents a replay.")
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": app.app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ",".join(SCOPES),
        "state": state,
    })


def parse_token_exchange(payload: Any) -> tuple[str, str]:
    """`(access_token, user_id)` from the code exchange response.

    Two shapes circulate depending on the API version — flat, or wrapped in
    `data: [...]`. Both are accepted; any other raises, rather than
    returning an empty token that would fail on the first data call as a
    permissions problem."""
    entree = payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        if not payload["data"]:
            raise InstagramAuthRefused(
                "Empty exchange response: Instagram returned no token.")
        entree = payload["data"][0]
    if not isinstance(entree, dict):
        raise InstagramAuthRefused(
            "Unexpected exchange response: Instagram did not return an object.")
    jeton, user_id = entree.get("access_token"), entree.get("user_id")
    if not jeton or not user_id:
        manquants = ", ".join(n for n, v in (("access_token", jeton), ("user_id", user_id))
                              if not v)
        raise InstagramAuthRefused(
            f"Incomplete exchange response: {manquants} missing.")
    return str(jeton), str(user_id)


def connect(app: InstagramApp, code: str, redirect_uri: str,
            *, session: Optional[requests.Session] = None) -> InstagramGrant:
    """The return code becomes a 60-day token and the account identity.

    The three calls are chained here because they are only worth anything together: a
    short-lived token alone lasts an hour and is useless to store, and a long-lived token
    without `user_id` allows no data call (all Graph
    API paths are prefixed by the account identifier)."""
    if not code:
        raise ValueError("code required: it is what Meta sends back on the return URL.")
    http = session or requests
    # The code and secret go in a form-encoded BODY (RFC 6749 §2.3.1): in the
    # query string they would enter the URL, hence the logs on both
    # sides. This step allows it; the next two don't (see below).
    r = http.post(TOKEN_URL, data={
        "client_id": app.app_id,
        "client_secret": app.app_secret,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code": code,
    }, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        _transport.refus_de_consentement(r, "the authorization code exchange")
    jeton_court, _ = parse_token_exchange(r.json())

    # ⚠️ **The secret goes in the query string here, and that is no oversight.** Meta only
    # serves `ig_exchange_token` via GET with the parameters in the URL — there is
    # no body form to prefer. What the rule really protects, we uphold
    # otherwise: no `raise_for_status()` in this package (its message carries
    # the URL), and `_transport` never lets the URL or the raw body out.
    r = http.get(f"{GRAPH_ROOT}/access_token", params={
        "grant_type": "ig_exchange_token",
        "client_secret": app.app_secret,
        "access_token": jeton_court,
    }, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        _transport.refus_de_consentement(r, "the switch to a long-lived authorization")
    long = r.json()
    jeton_long = long.get("access_token")
    if not jeton_long:
        raise InstagramAuthRefused(
            "Instagram did not return a long-lived authorization: without it, the "
            "connection would last an hour.")
    expires_in = int(long.get("expires_in") or LONG_LIVED_TTL_DAYS * 86_400)

    # The account identity. It isn't only used to display a name: `user_id` is
    # the Instagram professional account identifier, and it is what prefixes
    # all data paths. It is not interchangeable with the identifier
    # returned at step 1, which is specific to the application.
    r = http.get(f"{GRAPH_API_BASE}/me",
                 params={"fields": "user_id,username", "access_token": jeton_long},
                 timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        _transport.refus_de_consentement(
            r, "reading the authorized account — is the Instagram account really a "
               "professional account (Business or Creator)?")
    moi = r.json()
    user_id = str(moi.get("user_id") or moi.get("id") or "")
    if not user_id:
        raise InstagramAuthRefused(
            "Instagram returned an authorization but no professional account "
            "identifier: nothing could be read with it.")
    return InstagramGrant(access_token=jeton_long, user_id=user_id,
                          username=str(moi.get("username") or ""),
                          expires_in=expires_in)
