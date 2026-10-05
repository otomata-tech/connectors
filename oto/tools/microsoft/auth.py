"""Auth Microsoft Entra — connexion d'une PERSONNE (OAuth 2.0, code d'autorisation),
source unique de TOUS les clients Microsoft de la lib.

L'application est celle de l'éditeur qui consomme la lib, enregistrée une fois en
« multilocataire » dans son propre annuaire Entra : `client_id` + `client_secret`,
passés en argument. Chaque personne l'autorise depuis SON compte Microsoft 365 ;
le jeton rendu agit avec SES droits (permissions DÉLÉGUÉES), ni plus ni moins.

Trois gestes, un par endpoint du serveur d'autorisation :
- `authorize_url` — l'URL où envoyer le navigateur ;
- `exchange_code` — le code de retour contre un `Grant` ;
- `refresh` — un `refresh_token` contre un `Grant` neuf. ⚠️ Entra fait TOURNER le
  refresh token : celui rendu remplace l'ancien, l'appelant doit le ranger.

Point de terminaison `organizations` : comptes professionnels et scolaires seulement
(SharePoint et OneDrive Entreprise), pas les comptes Microsoft personnels.

Mêmes gardes que les autres auth de la lib : le secret part en **`data=`**, jamais en
`params=`, et pas de `raise_for_status()` (son message embarque l'URL).
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

#: Fichiers SharePoint et OneDrive en lecture-écriture, au nom de la personne, et
#: `offline_access` pour obtenir un refresh token. `User.Read` donne son identité.
FILES_SCOPES = ("offline_access", "User.Read",
                "https://graph.microsoft.com/Files.ReadWrite.All",
                "https://graph.microsoft.com/Sites.ReadWrite.All")

# Codes AADSTS qui veulent dire « l'autorisation est morte, reconnecter ».
_GRANT_MORT = ("invalid_grant", "interaction_required")


class MicrosoftAuthError(ValueError):
    """Refus du serveur d'autorisation Entra. Porte un `status_code` (contrat
    `UpstreamHTTPError`). `code` = le code AADSTS quand il est lisible
    (ex. `AADSTS7000215`, secret de l'application invalide), sinon `None`."""

    def __init__(self, message: str, *, status_code: int = 401, code: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class MicrosoftGrantExpired(MicrosoftAuthError):
    """L'autorisation de la personne n'est plus valable (révoquée, expirée, mot de
    passe changé, MFA redemandée…) : elle doit se reconnecter. La configuration de
    l'application, elle, n'est pas en cause."""


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
    """L'URL du dialogue de connexion Microsoft. `prompt=select_account` : une
    personne qui a plusieurs comptes choisit le bon au lieu d'être connectée
    d'office avec celui de son navigateur."""
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
        # Seule la 1re ligne : la suite porte un Trace ID et un horodatage.
        first_line = description.splitlines()[0] if description else ""
        error = str(payload.get("error") or "")
        detail = first_line or error or "réponse sans jeton"
        status = resp.status_code if resp.status_code >= 400 else 401
        cls = MicrosoftGrantExpired if error in _GRANT_MORT else MicrosoftAuthError
        raise cls(f"Entra refuse la demande de jeton (HTTP {resp.status_code}) : {detail}",
                  status_code=status, code=_aadsts(description))
    return Grant(access_token=payload["access_token"],
                 refresh_token=payload.get("refresh_token") or data.get("refresh_token") or "",
                 expires_in=int(payload.get("expires_in", 3600)),
                 scope=str(payload.get("scope") or ""))


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str, *,
                  scopes: tuple[str, ...] = FILES_SCOPES) -> Grant:
    """Le code ramené par le navigateur contre un `Grant` (avec refresh token)."""
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
    """Un `Grant` neuf. ⚠️ Son `refresh_token` remplace celui passé : le ranger."""
    return _token({  # ⚠️ `data=`, JAMAIS `params=`
        "grant_type": "refresh_token",
        "client_id": require(client_id, "MICROSOFT_CLIENT_ID"),
        "client_secret": require(client_secret, "MICROSOFT_CLIENT_SECRET"),
        "refresh_token": require(refresh_token, "MICROSOFT_REFRESH_TOKEN"),
        "scope": " ".join(scopes),
    })
