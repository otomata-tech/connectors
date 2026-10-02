"""Lire une réponse de la Graph API, et traduire un refus dans le vocabulaire d'`errors`.

⚠️ Le corps brut et l'URL ne traversent jamais cette frontière : le jeton part en
en-tête, mais un corps d'erreur peut faire écho à la requête. On rend le statut,
les codes Graph et `error.message` (rédigé par Meta pour un humain) — d'où aucun
`raise_for_status()` dans ce paquet.
"""
from __future__ import annotations

from typing import Any, Optional

from .errors import (
    MetaAdsApiError,
    MetaAdsAuthExpired,
    MetaAdsAuthRefused,
    MetaAdsThrottled,
)

#: « Ce jeton ne vaut plus rien » : 190 = expiré/révoqué, 102 = session invalidée.
_CODES_JETON_MORT = (190, 102)

#: Limites de débit : 4 = app, 17 = utilisateur, 32 = page, 613 = appels/temps,
#: 80000-80014 = limites « business use case » de la Marketing API.
_CODES_DEBIT = frozenset({4, 17, 32, 613}) | frozenset(range(80000, 80015))

#: Sous-code « trop de données par appel » (code 100) : réduire la fenêtre ou
#: passer par un rapport asynchrone — ce n'est pas un paramètre faux.
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
    """Le JSON d'une réponse, ou `None`."""
    try:
        return reponse.json()
    except ValueError:
        return None


def jeton_mort(status: int, payload: Any) -> bool:
    if status == 401:
        return True
    return _entier(_erreur(payload).get("code")) in _CODES_JETON_MORT


def lire(reponse, geste: str) -> dict:
    """Le corps JSON d'une réponse OK, ou l'erreur d'`errors` qui convient."""
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
    """Comme `lire`, pour l'ACQUISITION du jeton : il n'y a pas encore de jeton à
    déclarer mort — c'est le consentement lui-même que Meta refuse."""
    bloc = _erreur(corps(reponse))
    dit = str(bloc.get("message") or "").strip()
    raise MetaAdsAuthRefused(
        f"Meta refused {geste} (HTTP {reponse.status_code})"
        f"{f': {dit}' if dit else ''}.", str(bloc.get("type") or ""))
