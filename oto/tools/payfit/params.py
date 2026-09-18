"""Les GARDES de paramètres de l'API PayFit — partagées par le transport et les
familles d'appels de `_api/`.

Domicile à part pour une raison mécanique : `client.py` compose les mixins de
`_api/`, donc un mixin qui importerait `client.py` fermerait un cycle. Elles ne
dépendent de rien du client.

Trois refus, tous posés AVANT le réseau parce que le 400 qu'ils éviteraient ne
nomme aucun champ côté PayFit :

- un identifiant qui réécrirait l'URL (`../absences`, `id?x=1`) ;
- un mois qui n'est pas `AAAAMM` — la forme `2026-01`, la seule qu'un humain
  écrit spontanément, est refusée par l'API ;
- une taille de page hors de 1..50.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

MAX_LIMIT = 50
# Identifiers in the spec are Mongo ObjectIds, UUIDs or digits: a path segment
# is refused unless it is made of those characters only.
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
# A month, as every dated endpoint of this API wants it: YYYYMM, January = "01".
_MONTH = re.compile(r"2\d{3}(0[1-9]|1[0-2])")


def clean(params: Dict[str, Any]) -> Dict[str, Any]:
    """Drops `None` values — an omitted kwarg must not become 'None' in the URL."""
    return {k: v for k, v in params.items() if v is not None}


def ident(value: Any, name: str) -> str:
    text = str(value) if value is not None else ""
    if not _ID.fullmatch(text):
        raise ValueError(f"{name} invalide — reçu {value!r}.")
    return text


def month(value: Any, name: str = "date") -> str:
    """`AAAAMM`, le seul format de mois que cette API accepte."""
    text = str(value) if value is not None else ""
    if not _MONTH.fullmatch(text):
        raise ValueError(
            f"{name} doit être un mois au format AAAAMM (janvier = '01') — reçu "
            f"{value!r}.")
    return text


def page(limit: int, cursor: Optional[str]) -> Dict[str, Any]:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit doit être entre 1 et {MAX_LIMIT} — reçu {limit}.")
    return clean({"maxResults": limit, "nextPageToken": cursor or None})
