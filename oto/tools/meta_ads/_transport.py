"""Read a Graph API response, and translate a refusal into the `errors` vocabulary.

⚠️ The raw body and the URL never cross this boundary: the token travels in a
header, but an error body can echo the request. We return the status,
the Graph codes and `error.message` (written by Meta for a human) — hence no
`raise_for_status()` in this package.
"""
from __future__ import annotations

from typing import Any, Optional

from .errors import (
    MetaAdsApiError,
    MetaAdsAuthExpired,
    MetaAdsAuthRefused,
    MetaAdsThrottled,
)

#: "This token is worthless": 190 = expired/revoked, 102 = session invalidated.
_CODES_JETON_MORT = (190, 102)

#: Rate limits: 4 = app, 17 = user, 32 = page, 613 = calls/time,
#: 80000-80014 = Marketing API "business use case" limits.
_CODES_DEBIT = frozenset({4, 17, 32, 613}) | frozenset(range(80000, 80015))

#: Subcode "too much data per call" (code 100): narrow the window or
#: use an async report — it is not a wrong parameter.
_SOUS_CODE_TROP_DE_DONNEES = 1487534


def _erreur(payload: Any) -> dict:
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return payload["error"]
    return {}


def _entier(valeur: Any) -> Optional[int]:
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None


def corps(reponse) -> Any:
    """A response's JSON, or `None`."""
    try:
        return reponse.json()
    except ValueError:
        return None


def jeton_mort(status: int, payload: Any) -> bool:
    if status == 401:
        return True
    return _entier(_erreur(payload).get("code")) in _CODES_JETON_MORT


def lire(reponse, geste: str) -> dict:
    """The JSON body of an OK response, or the fitting `errors` error."""
    payload = corps(reponse)
    if reponse.status_code == 200:
        if not isinstance(payload, dict):
            raise MetaAdsApiError(
                f"Unreadable response from Meta on {geste} (not JSON).",
                reponse.status_code)
        return payload
    bloc = _erreur(payload)
    dit = str(bloc.get("error_user_msg") or bloc.get("message") or "").strip()
    suffixe = f": {dit}" if dit else ""
    code, sous_code = _entier(bloc.get("code")), _entier(bloc.get("error_subcode"))
    if jeton_mort(reponse.status_code, payload):
        raise MetaAdsAuthExpired(
            f"Meta refused {geste}: the authorization is no longer valid{suffixe}.")
    if code in _CODES_DEBIT:
        raise MetaAdsThrottled(
            f"Meta rate limit reached on {geste} (code {code}){suffixe}. Wait a few "
            f"minutes before retrying.", reponse.status_code, code, sous_code)
    if sous_code == _SOUS_CODE_TROP_DE_DONNEES:
        raise MetaAdsApiError(
            f"Meta refused {geste}: too much data for one call. Narrow the date "
            f"range or breakdowns, or use an async report.",
            reponse.status_code, code, sous_code)
    raise MetaAdsApiError(
        f"Meta refused {geste} (HTTP {reponse.status_code}"
        f"{f', code {code}' if code is not None else ''}){suffixe}.",
        reponse.status_code, code, sous_code)


def refus_de_consentement(reponse, geste: str) -> None:
    """Like `lire`, for token ACQUISITION: there is no token yet to
    declare dead — it is the consent itself that Meta refuses."""
    bloc = _erreur(corps(reponse))
    dit = str(bloc.get("message") or "").strip()
    raise MetaAdsAuthRefused(
        f"Meta refused {geste} (HTTP {reponse.status_code})"
        f"{f': {dit}' if dit else ''}.", str(bloc.get("type") or ""))
