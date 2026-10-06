"""HTTP base of the Brevo client — `api-key` auth, requests, pagination.

Split from the client to keep each module under ~200 lines: the domain mixins
(contacts, email, campaigns, crm) inherit from `_BrevoBase` and only have to
call `self._request(...)`.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream


class _BrevoBase:
    """Shared transport: `requests` session, `api-key` header, typed errors."""

    BASE_URL = "https://api.brevo.com/v3"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the client.

        Args:
            api_key: Brevo v3 API key.
        """
        self.api_key = require(api_key, "BREVO_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "api-key": self.api_key,
            "accept": "application/json",
            "content-type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.BASE_URL}{path}"
        resp = self.session.request(method, url, timeout=30, **kwargs)
        raise_for_upstream(resp, service="brevo")
        # 204 (Brevo PUT/PATCH) and empty bodies → empty dict rather than a JSON crash.
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    @staticmethod
    def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
        """Drop `None` values — Brevo rejects `?limit=None` and mishandles `?sort=`."""
        return {k: v for k, v in params.items() if v is not None}
