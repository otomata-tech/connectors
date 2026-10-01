"""Auth Microsoft Entra — jeton applicatif (client credentials) pour Microsoft Graph,
source unique de TOUS les clients Microsoft de la lib.

Le credential est celui d'une **app Entra enregistrée par l'organisation cliente**
dans son propre tenant : `tenant_id` + `client_id` + `client_secret`. Le jeton porte
les **permissions d'application** que l'admin du tenant a consenties (ex.
`Sites.Read.All`, `Files.ReadWrite.All`, `Sites.Selected`) — aucun utilisateur
connecté, aucun refresh token.

Mêmes gardes que l'auth Zoho (`oto/tools/zoho/auth.py`), pour les mêmes raisons :

- le secret part en **`data=`** (corps form-encodé, RFC 6749 §2.3.1), jamais en
  `params=`, et on n'appelle pas `raise_for_status()` : aucun secret dans une URL,
  donc dans aucun message d'erreur ni aucun log ;
- le cache est **process-wide**, keyé par le **hash** du credential : le serveur
  construit un client par appel MCP, un cache porté par l'instance ne servirait
  jamais, et deux orgs ne partagent jamais un jeton.
"""
from __future__ import annotations

import hashlib
import time

import requests

_HTTP_TIMEOUT = (10, 30)  # (connect, read)
_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# {cred_key: (access_token, expires_at_epoch)}
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


class MicrosoftAuthError(ValueError):
    """Refus du serveur d'autorisation Entra (tenant inconnu, client inconnu, secret
    expiré ou faux…). Porte un `status_code` (contrat `UpstreamHTTPError`) pour que
    les consommateurs classent ce refus de credential comme une erreur gérée.

    `code` = le code AADSTS d'Entra quand il est lisible (ex. `AADSTS7000215`,
    secret invalide), sinon `None`."""

    def __init__(self, message: str, *, status_code: int = 401, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def cred_key(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Identifiant opaque et stable d'un credential (jamais un secret en clair)."""
    return hashlib.sha256(f"{tenant_id}|{client_id}|{client_secret}".encode()).hexdigest()


def _aadsts(description: str) -> str | None:
    """`AADSTS7000215` depuis une `error_description` Entra, ou `None`."""
    head = (description or "").split(":", 1)[0].strip()
    return head if head.startswith("AADSTS") else None


def get_access_token(tenant_id: str, client_id: str, client_secret: str, *,
                     scope: str = GRAPH_SCOPE) -> str:
    """Jeton applicatif valide pour ce credential, redemandé seulement s'il expire
    dans moins d'une minute. Lève `MicrosoftAuthError` sur tout refus.

    Les trois valeurs sont supposées présentes : c'est le client qui les exige
    (`MissingCredential` nommé), pas ce module."""
    k = cred_key(tenant_id, client_id, client_secret)
    cached = _TOKEN_CACHE.get(k)
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    resp = requests.post(
        _TOKEN_URL.format(tenant=tenant_id.strip()),
        data={  # ⚠️ `data=`, JAMAIS `params=` (cf. docstring du module).
            "grant_type": "client_credentials",
            "client_id": client_id.strip(),
            "client_secret": client_secret,
            "scope": scope,
        },
        timeout=_HTTP_TIMEOUT,
    )

    try:
        payload = resp.json()
    except ValueError:
        payload = {}

    if resp.status_code >= 400 or "access_token" not in payload:
        description = str(payload.get("error_description") or "")
        # Seule la 1re ligne : la suite porte un Trace ID et un horodatage, inutiles
        # à l'agent. Aucun secret n'y figure (Entra ne renvoie jamais le secret).
        first_line = description.splitlines()[0] if description else ""
        detail = first_line or payload.get("error") or "réponse sans jeton"
        status = resp.status_code if resp.status_code >= 400 else 401
        raise MicrosoftAuthError(
            f"Entra refuse le credential de l'app (HTTP {resp.status_code}) : {detail}",
            status_code=status, code=_aadsts(description))

    token = payload["access_token"]
    _TOKEN_CACHE[k] = (token, time.time() + int(payload.get("expires_in", 3600)))
    return token


def invalidate(key: str) -> None:
    """Oublie le jeton caché de ce credential (appelé sur 401 amont)."""
    _TOKEN_CACHE.pop(key, None)
