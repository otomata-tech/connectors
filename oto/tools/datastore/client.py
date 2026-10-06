"""HTTP client for the MCP server's `/api/datastore/*`.

Auth via a long-lived API token stored in the `OTO_API_KEY` secret (issued
from `oto.ninja/account` or via the server-side `issue_token.py` script).

Base URL override: env `OTO_API_URL` (default `https://mcp.oto.cx`).
"""
from __future__ import annotations

import os
from typing import Any, Optional

import requests

from ..common.credentials import require


_DEFAULT_BASE_URL = "https://mcp.oto.cx"


class DatastoreError(RuntimeError):
    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}")


class DatastoreClient:
    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None):
        self.base_url = (base_url or os.environ.get("OTO_API_URL") or _DEFAULT_BASE_URL).rstrip("/")
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
            raise DatastoreError(r.status_code, detail)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # --- namespaces ---------------------------------------------------------

    def list_namespaces(self) -> list[dict]:
        return self._req("GET", "/api/datastore/namespaces")["namespaces"]

    def create_namespace(self, namespace: str,
                         owner: Optional[dict] = None) -> dict:
        """Creates a table. Returns `{namespace, id, url, owner_type, owner_id,
        is_personal}` — plus an `avertissement` if an org context was set without
        `owner`.

        ⚠️ Without `owner`, the table is PERSONAL: visible to you alone, not to the
        other members of your organization nor to its administrators. The call's
        org context changes NOTHING here — only `owner` decides, and it cannot be
        changed afterwards. Pass `{"type": "org"|"group", "id": N}` for a shared
        table.

        This parameter used to be missing here: an integrator going through this
        lib COULD NOT create an organization table, even though the route has
        always accepted it (otomata-tech/oto#45).
        """
        corps: dict = {"namespace": namespace}
        if owner:
            corps["owner"] = owner
        return self._req("POST", "/api/datastore/namespaces", json=corps)

    def delete_namespace(self, namespace: str) -> dict:
        return self._req("DELETE", f"/api/datastore/namespaces/{namespace}")

    def url(self, namespace: str) -> str:
        return self._req("GET", f"/api/datastore/namespaces/{namespace}/url")["url"]

    # --- rows ---------------------------------------------------------------

    def append(self, namespace: str, row: dict) -> dict:
        return self._req("POST", f"/api/datastore/namespaces/{namespace}/rows", json=row)

    def list_rows(
        self,
        namespace: str,
        filter: Optional[dict] = None,
        limit: int = 100,
    ) -> dict:
        params: list[tuple[str, str]] = [("limit", str(limit))]
        if filter:
            for k, v in filter.items():
                params.append(("filter", f"{k}:{v}"))
        return self._req("GET", f"/api/datastore/namespaces/{namespace}/rows", params=params)

    def get(self, namespace: str, row_id: str) -> dict:
        return self._req("GET", f"/api/datastore/namespaces/{namespace}/rows/{row_id}")

    def update(self, namespace: str, row_id: str, patch: dict) -> dict:
        return self._req("PATCH", f"/api/datastore/namespaces/{namespace}/rows/{row_id}", json=patch)

    def delete_row(self, namespace: str, row_id: str) -> dict:
        return self._req("DELETE", f"/api/datastore/namespaces/{namespace}/rows/{row_id}")

    # --- sharing ---------------------------------------------------------------

    def share(self, namespace: str, email: str, permission: str = "write") -> dict:
        return self._req("POST", f"/api/datastore/namespaces/{namespace}/share",
                         json={"email": email, "permission": permission})

    def unshare(self, namespace: str, email: str) -> dict:
        return self._req("DELETE", f"/api/datastore/namespaces/{namespace}/share",
                         json={"email": email})
