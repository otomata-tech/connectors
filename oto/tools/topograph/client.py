"""Topograph API Client — KYB data & documents for European public registers.

Topograph (https://www.topograph.co) normalizes 100+ European public registries
behind a single REST API (KYB onboarding + verification). Docs:
https://docs.topograph.co.

Auth: API key in the `x-api-key` header (Dashboard → Settings → API Keys).

Requires: requests
"""

import time
from typing import Any, Dict, Optional

import requests

from ..common.credentials import require


class TopographClient:
    """Client for the Topograph v2 API.

    - `search`: company search by name or registration number (GET /v2/search).
    - `company`: normalized company data (POST /v2/company), mode
      `onboarding` (fast/cheap) or `verification` (rigorous).
    """

    BASE_URL = "https://api.topograph.co/v2"

    def __init__(self, api_key: str = None):
        """
        Args:
            api_key: Topograph API key.
        """
        self.api_key = require(api_key, "TOPOGRAPH_API_KEY")

    def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        headers = {"x-api-key": self.api_key, "Accept": "application/json"}
        if method.upper() != "GET":
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            resp = requests.request(method, url, headers=headers, timeout=60, **kwargs)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2))
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text
                raise Exception(f"HTTP {resp.status_code}: {body}")
            return resp.json() if resp.content else {}
        raise Exception("Topograph: rate-limited (429) after retries")

    def search(
        self,
        query: str,
        country: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Search companies by name or registration number.

        Args:
            query: company name or registration number.
            country: ISO 3166-1 alpha-2 country code (e.g. "FR", "GB", "DE").
            limit: maximum number of results.

        Returns:
            Search results (candidates with identity + registration number).
        """
        params: Dict[str, Any] = {"query": query}
        if country:
            params["country"] = country
        if limit is not None:
            params["limit"] = limit
        return self._request("GET", "search", params=params)

    def company(
        self,
        country: Optional[str] = None,
        registration_number: Optional[str] = None,
        company_id: Optional[str] = None,
        mode: str = "onboarding",
    ) -> Dict[str, Any]:
        """Normalized company data (POST /v2/company).

        Identify the company by (`country` + `registration_number`) — both
        returned by `search` — or by `company_id`.

        Args:
            country: ISO 3166-1 alpha-2 country code.
            registration_number: registration number (SIREN/SIRET, etc.).
            company_id: Topograph identifier (alternative to the registration number).
            mode: "onboarding" (fast/cheap) or "verification" (rigorous).

        Returns:
            Normalized company record (identity, legal form, directors…).
        """
        body: Dict[str, Any] = {"mode": mode}
        if country:
            body["country"] = country
        if registration_number:
            body["registrationNumber"] = registration_number
        if company_id:
            body["companyId"] = company_id
        return self._request("POST", "company", json=body)
