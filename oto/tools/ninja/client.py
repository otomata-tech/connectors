"""HTTP client for the `/api/*` endpoints of `mcp.oto.cx`.

Auth via long-lived API token (`token`, supplied by the consumer), same mechanism as
`oto.tools.datastore.client`. Base URL override: env `OTO_API_URL`
(default `https://mcp.oto.cx`).

Scope: read/write of multi-user secrets (LinkedIn cookies,
Crunchbase, per-provider API keys) that only the oto-mcp DB knows about.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import requests

from ..common.credentials import require


_DEFAULT_BASE_URL = "https://mcp.oto.cx"


class NinjaError(RuntimeError):
    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}")


class NinjaClient:
    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None):
        self.base_url = (
            base_url or os.environ.get("OTO_API_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        self.token = require(token, "OTO_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })

    def _req(self, method: str, path: str, **kw) -> Any:
        url = f"{self.base_url}{path}"
        r = self.session.request(method, url, timeout=30, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise NinjaError(r.status_code, detail)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # --- me / overview ------------------------------------------------------

    def me(self) -> dict:
        return self._req("GET", "/api/me")

    # --- LinkedIn -----------------------------------------------------------

    def get_linkedin(self) -> dict:
        """Returns `{cookie, user_agent, set_at}`. Raises NinjaError(404) if not configured."""
        return self._req("GET", "/api/settings/linkedin")

    def set_linkedin(self, cookie: str, user_agent: Optional[str] = None) -> dict:
        body = {"cookie": cookie}
        if user_agent:
            body["user_agent"] = user_agent
        return self._req("POST", "/api/settings/linkedin", json=body)

    def delete_linkedin(self) -> dict:
        return self._req("DELETE", "/api/settings/linkedin")

    # --- Crunchbase ---------------------------------------------------------

    def get_crunchbase(self) -> dict:
        return self._req("GET", "/api/settings/crunchbase")

    def set_crunchbase(self, cookies: list, user_agent: Optional[str] = None) -> dict:
        body = {"cookies": cookies}
        if user_agent:
            body["user_agent"] = user_agent
        return self._req("POST", "/api/settings/crunchbase", json=body)

    def delete_crunchbase(self) -> dict:
        return self._req("DELETE", "/api/settings/crunchbase")

    # --- API keys per provider ----------------------------------------------

    def get_api_key(self, provider: str) -> dict:
        return self._req("GET", f"/api/settings/api-keys/{provider}")

    def set_api_key(self, provider: str, key: str) -> dict:
        return self._req("POST", f"/api/settings/api-keys/{provider}", json={"key": key})

    def delete_api_key(self, provider: str) -> dict:
        return self._req("DELETE", f"/api/settings/api-keys/{provider}")
