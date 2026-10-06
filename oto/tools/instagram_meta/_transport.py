"""Read a Meta response, and translate a refusal into the vocabulary of `errors`.

Three modules call Meta (the code exchange, the renewal, the data)
and all three receive error bodies of TWO different shapes depending on the
host:

    {"error": {"message": …, "type": "OAuthException", "code": 190}}   graph.instagram.com
    {"error_type": "OAuthException", "code": 400, "error_message": …}  api.instagram.com

Copying the reading three times gives two chances to recognize a dead
token on only one of the three paths — and an unrecognized dead token
shows up to the user as a transient outage that she will retry.

⚠️ **The response body never crosses this boundary.** On this API
the token travels as a URL parameter (that is Meta's protocol, not our choice),
so the URL and sometimes the request echo end up in error bodies.
We return the STATUS and, when present, the message written by Meta for the
human (`error.message` / `error_message`) — never the raw body, never the
URL. This is also why no call in this package uses
`raise_for_status()`: its message carries the called URL.
"""
from __future__ import annotations

from typing import Any, Optional

from .errors import InstagramApiError, InstagramAuthExpired, InstagramAuthRefused

#: Meta error codes meaning "this token is worthless". 190 =
#: token expired, changed or revoked; 102 = session invalidated.
_CODES_JETON_MORT = (190, 102)


def _erreur(payload: Any) -> dict:
    """The error block, whatever shape the host returns. `{}` if none."""
    if not isinstance(payload, dict):
        return {}
    bloc = payload.get("error")
    if isinstance(bloc, dict):
        return bloc
    if payload.get("error_type") or payload.get("error_message"):
        return {"type": payload.get("error_type"), "code": payload.get("code"),
                "message": payload.get("error_message")}
    return {}


def _entier(valeur: Any) -> Optional[int]:
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None


def jeton_mort(status: int, payload: Any) -> bool:
    """Does Meta say the TOKEN is dead — as opposed to "the call failed"?

    The distinction guards a destructive gesture from the user's point of view:
    the caller asks her for a new consent. A 400 for an invalid
    parameter must not trigger that."""
    if status == 401:
        return True
    bloc = _erreur(payload)
    if _entier(bloc.get("code")) in _CODES_JETON_MORT:
        return True
    return status == 400 and bloc.get("type") == "OAuthException"


def lire(reponse, geste: str, *, expires_at: Optional[str] = None) -> dict:
    """The JSON body of an OK response, or the fitting `errors` error.

    `geste` is the phrase put into the message ("reading the profile",
    "renewing the authorization"): an error that doesn't say what
    failed sends people looking in the wrong place."""
    try:
        payload = reponse.json()
    except ValueError:
        payload = None
    if reponse.status_code == 200:
        if not isinstance(payload, dict):
            raise InstagramApiError(
                f"Unreadable Instagram response while {geste} (non-JSON body).",
                reponse.status_code)
        return payload
    bloc = _erreur(payload)
    dit = str(bloc.get("message") or "").strip()
    if jeton_mort(reponse.status_code, payload):
        raise InstagramAuthExpired(
            f"Instagram refused {geste}: the account's authorization is no longer "
            f"valid{f' ({dit})' if dit else ''}.", expires_at)
    raise InstagramApiError(
        f"Instagram refused {geste} (HTTP {reponse.status_code})"
        f"{f': {dit}' if dit else ''}.", reponse.status_code)


def refus_de_consentement(reponse, geste: str) -> None:
    """Like `lire`, but for the token ACQUISITION steps.

    A refusal there has another dominant cause than "the token is dead" — there is
    no token yet: it is the consent itself that Meta refuses (account
    not invited as a tester while the application is unpublished, code already
    consumed, undeclared return URL). Hence a distinct error: the caller
    has a completely different message to compose, and it is not addressed to the same person."""
    try:
        payload = reponse.json()
    except ValueError:
        payload = None
    bloc = _erreur(payload)
    dit = str(bloc.get("message") or "").strip()
    raise InstagramAuthRefused(
        f"Meta refused {geste} (HTTP {reponse.status_code})"
        f"{f': {dit}' if dit else ''}.", str(bloc.get("type") or ""))
