"""
Silae Paie REST API client — French payroll data (API v1).

Docs: https://silae-api.document360.io/docs/authentification (one page per function,
`.md` suffix for the Markdown version; the OpenAPI file is linked from the release note).

Auth is OAuth2 client-credentials (Azure AD B2C, `auth.py`). Three secrets:
  - client_id / client_secret : the API account;
  - subscription_key          : the key of an "API access configuration", sent as
                                `Ocp-Apim-Subscription-Key` — it decides which dossiers
                                and which functions are reachable.

Every function is a POST to `/v1/<Family>/<Function>` with a JSON body (the status
functions of asynchronous tasks are a GET with `guidTache` in the query string). The
dossier a call targets also goes in the `dossiers` header: without it Silae answers
error 1011 ("la valeur de la liste de dossiers est nulle ou vide").

Errors RAISE: an HTTP refusal is an `UpstreamHTTPError` carrying Silae's body
(`{"errors": [{"code", "message"}], "recoverable", "source"}`), a refused credential a
`SilaeAuthError` (401). Pay periods are passed as `AAAA-MM` and converted to the format
of each field (`periodes.py`).

Package layout: this module holds construction and transport, and composes one mixin
per API family (`_api/`). Documents (reports, declaration summaries) come back as
`{"data": bytes, "filename", "mimetype"}`, decoded from Silae's base64.

Usage:
    client = SilaeClient(client_id=..., client_secret=..., subscription_key=...)
    client.list_dossiers()
    client.bulletin_lignes("001", "0001", identifiant_emploi=1, periode="2026-05")
"""

from __future__ import annotations

import base64
import binascii
import time
from typing import Any, Optional

import requests

from ..common import FieldFilter, UpstreamHTTPError, raise_for_upstream
from ..common.credentials import require
from . import auth
from ._api import (
    _BulletinsMixin,
    _DeclarationsMixin,
    _DossiersMixin,
    _EditionsMixin,
    _SaisiesMixin,
    _SalariesMixin,
)

__all__ = ["SilaeClient"]

_TIMEOUT = 60


class SilaeClient(
    _DossiersMixin,
    _SalariesMixin,
    _BulletinsMixin,
    _EditionsMixin,
    _DeclarationsMixin,
    _SaisiesMixin,
):
    """Client for the Silae Paie REST API (v1)."""

    BASE_URL = "https://payroll-api.silae.fr/payroll"

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        subscription_key: Optional[str] = None,
        field_filter: Optional[FieldFilter] = None,
    ):
        """
        Args:
            client_id: OAuth ClientID of the API account.
            client_secret: matching ClientSecret.
            subscription_key: key of the API access configuration, sent as
                `Ocp-Apim-Subscription-Key`.
            field_filter: redacts sensitive fields from every JSON response. Defaults
                to the `field_filters.silae` policy of ~/.otomata/config.yaml (no-op
                when none is configured).
        """
        self.client_id = require(client_id, "SILAE_CLIENT_ID")
        self.client_secret = require(client_secret, "SILAE_CLIENT_SECRET")
        self.subscription_key = require(subscription_key, "SILAE_SUBSCRIPTION_KEY")
        self.field_filter = field_filter or FieldFilter.from_config("silae")
        self.session = requests.Session()

    # --- HTTP ---

    def call(
        self,
        path: str,
        body: Optional[dict] = None,
        numero_dossier: Optional[str] = None,
        method: str = "POST",
        params: Optional[dict] = None,
        retries: int = 3,
        timeout: float = _TIMEOUT,
    ) -> Any:
        """Call a Silae API function and return its parsed JSON (None on an empty body).

        Args:
            path: function path under the base URL, e.g. "v1/SalarieEmplois/ListeSalarieEmplois".
            body: JSON body (POST functions).
            numero_dossier: sent as the `dossiers` header.
            method: "POST", or "GET" for the status of an asynchronous task.
            params: query string — only the task id of a status call, never a secret.
            retries: attempts on a 401 (token refreshed once) and on a 429 (backoff).
            timeout: seconds before the request is abandoned.

        Raises:
            UpstreamHTTPError: Silae answered >= 400 (its error body attached), or
                answered 200 with something that is not JSON.
        """
        url = f"{self.BASE_URL}/{path.lstrip('/')}"
        for attempt in range(retries):
            token = auth.get_access_token(self.session, self.client_id, self.client_secret)
            headers = {
                "Authorization": f"Bearer {token}",
                "Ocp-Apim-Subscription-Key": self.subscription_key,
                "Accept": "application/json",
            }
            if numero_dossier is not None:
                headers["dossiers"] = str(numero_dossier)
            resp = self.session.request(method, url, json=body, params=params,
                                        headers=headers, timeout=timeout)
            last = attempt == retries - 1
            if resp.status_code == 401 and attempt == 0 and not last:
                auth.invalidate(self.client_id, self.client_secret)
                continue
            if resp.status_code == 429 and not last:
                time.sleep(2 ** attempt)
                continue
            raise_for_upstream(resp, service="silae")
            if not resp.content:
                return None
            try:
                parsed = resp.json()
            except ValueError:
                raise UpstreamHTTPError(502, {"error": "non-JSON response"},
                                        service="silae") from None
            return self.field_filter.apply(parsed)
        raise UpstreamHTTPError(429, {"error": "retries exhausted"}, service="silae")

    @staticmethod
    def _document(rendu: Any, champ: str, filename: str, mimetype: str) -> dict:
        """Decode the base64 document `champ` of a response into
        `{"data": bytes, "filename", "mimetype"}`."""
        b64 = rendu.get(champ) if isinstance(rendu, dict) else None
        if not b64:
            raise UpstreamHTTPError(502, {"error": f"no `{champ}` in the response"},
                                    service="silae")
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise UpstreamHTTPError(502, {"error": f"`{champ}` is not valid base64"},
                                    service="silae") from None
        return {"data": data, "filename": filename, "mimetype": mimetype}
