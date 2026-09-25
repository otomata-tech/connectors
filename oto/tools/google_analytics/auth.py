"""Auth Google par CLÉ DE COMPTE DE SERVICE — le jeton d'accès de GA4.

Le flux est le « JWT bearer » d'OAuth 2.0 (RFC 7523) : on signe une assertion
avec la clé privée du compte de service, on l'échange contre un jeton d'accès
d'une heure sur l'endpoint de jeton de Google. Aucun consentement humain, aucun
refresh token.

Trois règles tenues ici :

- **Un seul hôte de jeton, `oauth2.googleapis.com`.** La clé JSON porte son propre
  `token_uri` ; le suivre sans le vérifier ferait d'un champ saisi par un tiers la
  destination d'une requête qui transporte une assertion signée (SSRF). Une clé
  dont le `token_uri` diffère est refusée à la lecture.
- **Le secret ne sort jamais.** L'assertion part en corps (`data=`), jamais en
  query string ; pas de `raise_for_status()` (son message embarque l'URL) ; le
  message d'un refus ne cite que ce que Google a répondu, jamais la requête.
- **Cache process-wide, keyé par credential.** Le serveur construit un client par
  appel d'outil : un cache porté par l'instance ne servirait jamais. La clé du
  cache est une EMPREINTE (sha256) du credential et du scope — jamais un secret
  en clair comme clé de dictionnaire, jamais un jeton partagé entre deux clés.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Union

import requests
from google.auth import crypt, jwt

from ..common import UpstreamHTTPError

#: Le seul endpoint de jeton accepté (cf. le docstring du module).
TOKEN_URI = "https://oauth2.googleapis.com/token"

#: Lecture seule de Google Analytics — le seul scope que ce connecteur demande.
SCOPE_READONLY = "https://www.googleapis.com/auth/analytics.readonly"

_JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
_ASSERTION_TTL_S = 3600
_TOKEN_TIMEOUT = (10, 30)
# Un jeton qui expire dans moins d'une minute est renouvelé avant l'appel.
_MARGE_S = 60

# {empreinte: (access_token, expires_at_epoch)}
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_LOCK = threading.Lock()

_CHAMPS_REQUIS = ("client_email", "private_key")


class ServiceAccountAuthError(UpstreamHTTPError):
    """Google refuse d'émettre un jeton pour cette clé (révoquée, supprimée,
    horloge décalée, compte de service désactivé…).

    Porte un `status_code` 401 synthétique quel que soit le code amont (400
    `invalid_grant` le plus souvent) : pour tout consommateur, c'est un refus de
    CREDENTIAL, pas une requête mal formée. `body` = ce que Google a répondu
    (`error`, `error_description`), qui ne contient aucun secret."""

    def __init__(self, upstream_status: int, body: Any):
        self.upstream_status = upstream_status
        super().__init__(401, body, service="google_analytics")


def parse_service_account_key(raw: Union[str, bytes, dict]) -> dict:
    """La clé JSON d'un compte de service, validée — ou `ValueError` qui dit
    quoi coller à la place.

    Accepte le contenu du fichier (texte) ou le dict déjà chargé. Refuse
    nommément les deux confusions probables : un JSON de client OAuth
    (`installed`/`web`) et une clé dont il manque la clé privée."""
    if isinstance(raw, (str, bytes)):
        try:
            key = json.loads(raw)
        except ValueError:
            raise ValueError(
                "La clé de compte de service doit être le contenu JSON du fichier "
                "téléchargé depuis Google Cloud (IAM → Comptes de service → Clés → "
                "Ajouter une clé → JSON) — le texte fourni n'est pas du JSON.") from None
    else:
        key = raw
    if not isinstance(key, dict):
        raise ValueError("La clé de compte de service doit être un objet JSON.")
    if "installed" in key or "web" in key:
        raise ValueError(
            "Ce JSON est un identifiant de client OAuth, pas une clé de compte de "
            "service. Il faut la clé JSON d'un compte de service (Google Cloud → IAM "
            "→ Comptes de service → Clés).")
    if key.get("type") != "service_account":
        raise ValueError(
            f"La clé fournie est de type {key.get('type')!r}, pas `service_account` "
            "— il faut la clé JSON d'un compte de service.")
    manquants = [c for c in _CHAMPS_REQUIS if not key.get(c)]
    if manquants:
        raise ValueError(
            f"Clé de compte de service incomplète : il manque {', '.join(manquants)}. "
            "Recolle le fichier JSON entier, tel que Google l'a téléchargé.")
    token_uri = key.get("token_uri") or TOKEN_URI
    if token_uri != TOKEN_URI:
        raise ValueError(
            f"`token_uri` inattendu dans la clé ({token_uri!r}) : seul {TOKEN_URI} est "
            "accepté.")
    _signer(key)  # une clé privée illisible se refuse à la lecture, pas au 1er appel
    return key


def cred_key(key: dict, scope: str) -> str:
    """Empreinte stable d'un credential et d'un scope (jamais un secret en clair)."""
    return hashlib.sha256(
        "|".join((key["client_email"], key.get("private_key_id") or "",
                  key["private_key"], scope)).encode()).hexdigest()


def _signer(key: dict):
    try:
        return crypt.RSASigner.from_service_account_info(key)
    except (ValueError, TypeError) as e:
        raise ValueError(
            "La clé privée du compte de service est illisible "
            f"({type(e).__name__}) — recolle le fichier JSON entier, sans le modifier."
        ) from None


def _signed_assertion(key: dict, scope: str, now: int) -> str:
    signer = _signer(key)
    payload = {"iss": key["client_email"], "scope": scope, "aud": TOKEN_URI,
               "iat": now, "exp": now + _ASSERTION_TTL_S}
    token = jwt.encode(signer, payload)
    return token.decode() if isinstance(token, bytes) else token


def access_token(key: dict, *, session: requests.Session,
                 scope: str = SCOPE_READONLY) -> str:
    """Un jeton d'accès valide pour cette clé, émis seulement si le cache n'en a
    pas un qui vit encore au moins une minute.

    `key` doit sortir de `parse_service_account_key`."""
    k = cred_key(key, scope)
    with _LOCK:
        cached = _TOKEN_CACHE.get(k)
        if cached and cached[1] > time.time() + _MARGE_S:
            return cached[0]

    now = int(time.time())
    resp = session.post(
        TOKEN_URI,
        # ⚠️ `data=`, JAMAIS `params=` : l'assertion signée ne doit pas atterrir
        # dans une URL (message d'exception, journaux, Sentry).
        data={"grant_type": _JWT_BEARER, "assertion": _signed_assertion(key, scope, now)},
        timeout=_TOKEN_TIMEOUT,
    )
    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        body = {k2: payload[k2] for k2 in ("error", "error_description")
                if isinstance(payload, dict) and k2 in payload}
        raise ServiceAccountAuthError(resp.status_code, body or "refus du serveur de jetons")
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise ServiceAccountAuthError(resp.status_code, "réponse sans access_token")
    expires_in = int(data.get("expires_in") or 0)
    with _LOCK:
        _TOKEN_CACHE[k] = (token, now + expires_in)
    return token
