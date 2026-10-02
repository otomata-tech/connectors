"""Acquisition de l'autorisation — Facebook Login for Business.

1. `authorize_url` : le dialogue, avec le `config_id` de l'application (qui fixe
   les permissions ET le type de jeton) ; Meta revient avec un `code`.
2. `connect` : le code devient un jeton, puis on lit QUI a autorisé.

Configuration recommandée côté Meta : jeton « utilisateur système d'intégration
business » (BISU). Il n'expire pas par défaut — donc aucun renouvellement à tenir,
contrairement au jeton utilisateur de 60 jours d'`instagram_meta`. Si la
configuration émet malgré tout un jeton à durée de vie, `expires_in` le dit et
l'appelant stocke l'échéance.

Le `state` anti-rejeu est signé et vérifié par l'appelant, pas ici.
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
    """Ce qu'un consentement réussi produit.

    `expires_in` = secondes, ou `None` pour un jeton sans échéance (BISU).
    `client_business_id` n'existe que pour un jeton BISU : c'est le portefeuille
    business du client qui a autorisé."""

    access_token: str
    expires_in: Optional[int]
    user_id: str
    name: str
    client_business_id: str


def authorize_url(app: MetaAdsApp, redirect_uri: str, state: str) -> str:
    """L'URL du dialogue. `redirect_uri` doit être déclarée au byte près chez Meta."""
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
        # Requis pour un jeton BISU : sans lui, le dialogue rend un jeton
        # utilisateur dans le fragment au lieu d'un code.
        "override_default_response_type": "true",
    })


def connect(app: MetaAdsApp, code: str, redirect_uri: str,
            *, session: Optional[requests.Session] = None) -> MetaAdsGrant:
    """Le code de retour devient un jeton, plus l'identité de qui l'a émis."""
    if not code:
        raise ValueError("code is required: Meta sends it back on the callback URL.")
    http = session or requests
    # Le code et le secret partent en CORPS form-encodé, jamais en query string :
    # une URL finit dans les journaux des deux côtés.
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

    # `client_business_id` n'existe que sur un utilisateur système : demandé à un
    # jeton utilisateur, Graph refuse le champ — on retombe alors sur id,name.
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
