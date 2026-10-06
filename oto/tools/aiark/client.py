"""
AI Ark API Client — B2B company & people data (search + contact enrichment).

Synchronous REST API (docs.ai-ark.com). Auth = API key in the `X-TOKEN` header.
Base: https://api.ai-ark.com/api/developer-portal

Endpoints covered (v1 = SYNCHRONOUS only):
- POST /v1/companies              — company search (firmographic filters)
- POST /v1/people                 — people search (company + contact filters)
- POST /v1/people/export/single   — export ONE person + email lookup (sync)
- POST /v1/people/reverse-lookup  — find a person from an email/phone
- POST /v1/people/mobile-phone-finder — find a person's mobile
- GET  /v1/payments/credits       — remaining credits

BULK exports/find-emails answer by webhook (asynchronous): out of scope for
v1 (next iteration if needed). The single-person export above is synchronous.

Requires: requests
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require


class AiArkClient:
    """Client for the AI Ark API (Company & People Data)."""

    BASE_URL = "https://api.ai-ark.com/api/developer-portal"
    TIMEOUT = 30

    def __init__(self, api_key: str | None = None):
        """
        Args:
            api_key: AI Ark key (`X-TOKEN`).
        """
        self.api_key = require(api_key, "AIARK_API_KEY")

    def _headers(self) -> Dict[str, str]:
        return {
            "X-TOKEN": self.api_key,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json: Optional[dict] = None,
        allow_404: bool = False,
    ) -> Any:
        """API call. `allow_404=True` returns None on 404 (unsuccessful lookup =
        normal case, not an error) instead of raising."""
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        resp = requests.request(
            method, url, headers=self._headers(), json=json, timeout=self.TIMEOUT
        )
        if allow_404 and resp.status_code == 404:
            return None
        resp.raise_for_status()
        # Some endpoints (204/empty body) return no JSON.
        if not resp.content:
            return None
        return resp.json()

    # ---- credits / auth ----------------------------------------------------

    def credits(self) -> Dict[str, Any]:
        """Remaining credits of the account: `{"total": <int>}`."""
        return self._request("GET", "v1/payments/credits")

    def verify_key(self) -> Dict[str, Any]:
        """Validates the key via a credits call. `{"valid": True, "credits": <int>}`
        if OK, otherwise raises the HTTPError (401 = invalid key)."""
        data = self.credits() or {}
        return {"valid": True, "credits": data.get("total")}

    # ---- search ------------------------------------------------------------

    def search_companies(
        self,
        *,
        account: Optional[dict] = None,
        lists: Optional[dict] = None,
        lookalike_domains: Optional[List[str]] = None,
        page: int = 0,
        size: int = 10,
    ) -> Dict[str, Any]:
        """Company search (firmographics). Returns the raw AI Ark page
        (`content[]`, `totalElements`, `totalPages`, `pageable`, …).

        Args:
            account: firmographic filters (name, domain, industry, location,
                headcount, revenue, technologies, funding…). AI Ark structure, e.g.
                `{"name": {"any": {"include": {"mode": "SMART", "content": ["Amazon"]}}}}`.
            lists: exclusion of companies already in saved lists.
            lookalike_domains: up to 5 URLs to find similar companies.
            page: page number (0-based). size: 0-100.
        """
        body: Dict[str, Any] = {"page": page, "size": size}
        if account is not None:
            body["account"] = account
        if lists is not None:
            body["lists"] = lists
        if lookalike_domains:
            body["lookalikeDomains"] = lookalike_domains
        return self._request("POST", "v1/companies", json=body)

    def search_people(
        self,
        *,
        account: Optional[dict] = None,
        contact: Optional[dict] = None,
        lists: Optional[dict] = None,
        page: int = 0,
        size: int = 10,
    ) -> Dict[str, Any]:
        """People search. Returns the raw AI Ark page (`content[]`,
        `totalElements`, `totalPages`, `trackId`, …).

        Args:
            account: filters on the person's company (domain, industry,
                headcount…), same DSL as `search_companies`.
            contact: filters on the person (seniority, department, job title,
                location…), e.g. `{"seniority": {"any": {"include": ["founder"]}}}`.
            lists: exclusion of people already in saved lists.
            page: page number (0-based). size: 0-100.
        """
        body: Dict[str, Any] = {"page": page, "size": size}
        if account is not None:
            body["account"] = account
        if contact is not None:
            body["contact"] = contact
        if lists is not None:
            body["lists"] = lists
        return self._request("POST", "v1/people", json=body)

    # ---- enrichment (synchronous) -----------------------------------------

    def export_person(
        self, *, id: Optional[str] = None, url: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Export ONE person + email lookup (synchronous). Returns the profile
        with `email.output[]` (`address`/`status`/`domainType`), or None if no
        email/profile found (404).

        Args:
            id: AI Ark id of a person (from a `search_people` search).
            url: OR a LinkedIn profile URL. At least one of the two is required.
        """
        if not id and not url:
            raise ValueError("export_person requires `id` or `url`.")
        body: Dict[str, Any] = {}
        if id:
            body["id"] = id
        if url:
            body["url"] = url
        return self._request(
            "POST", "v1/people/export/single", json=body, allow_404=True
        )

    def reverse_lookup(self, search: str) -> Optional[Dict[str, Any]]:
        """Finds a person from a piece of contact info (email, phone…).
        Returns the full profile, or None if not found (404).

        Args:
            search: the contact info to resolve (email, phone…).
        """
        return self._request(
            "POST",
            "v1/people/reverse-lookup",
            json={"search": search},
            allow_404=True,
        )

    def mobile_phone(
        self,
        *,
        linkedin: Optional[str] = None,
        domain: Optional[str] = None,
        name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Finds a person's mobile(s). Returns `{"id", "linkedin",
        "data": [["+..."]]}` or None if not found (404).

        Args:
            linkedin: LinkedIn profile URL (alone), OR…
            domain + name: the company's domain AND the person's name (together).
        """
        if not linkedin and not (domain and name):
            raise ValueError(
                "mobile_phone requires `linkedin` OR (`domain` AND `name`)."
            )
        body: Dict[str, Any] = {}
        if linkedin:
            body["linkedin"] = linkedin
        if domain:
            body["domain"] = domain
        if name:
            body["name"] = name
        return self._request(
            "POST",
            "v1/people/mobile-phone-finder",
            json=body,
            allow_404=True,
        )
