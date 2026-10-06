"""Recruitee ATS API client.

Auth = **API token** (Bearer) + **company id** (the subdomain/identifier of the
company, found in the Recruitee app URL). Token created in Recruitee:
Settings → Apps and plugins → Personal API tokens. Both are passed to the
constructor.

Recruitee vocabulary: a job = an **offer**; an applicant = a **candidate**
(attached to one or more offers).

Docs : https://docs.recruitee.com/reference

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require


class RecruiteeClient:
    """Client Recruitee — candidats, offers, notes."""

    BASE_URL = "https://api.recruitee.com"

    def __init__(self, api_token: Optional[str] = None,
                 company_id: Optional[str] = None):
        """Initialize the client.

        Args:
            api_token: Recruitee API token.
            company_id: company identifier.
        """
        self.api_token = require(api_token, "RECRUITEE_API_TOKEN")
        self.company_id = require(company_id, "RECRUITEE_COMPANY_ID")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.BASE_URL}/c/{self.company_id}{path}"
        resp = self.session.request(method, url, timeout=30, **kwargs)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise Exception(f"Recruitee HTTP {resp.status_code}: {body}")
        return resp.json() if resp.content else {}

    # --- Candidates ----------------------------------------------------------

    def list_candidates(
        self,
        limit: int = 50,
        offset: int = 0,
        offer_id: Optional[int] = None,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List candidates (paginated). `offer_id` filters by job, `query`
        searches by name/email."""
        params: Dict[str, Any] = {"limit": min(limit, 100), "offset": offset}
        if offer_id:
            params["offer_id"] = offer_id
        if query:
            params["query"] = query
        return self._request("GET", "/candidates", params=params)

    def get_candidate(self, candidate_id: int) -> Dict[str, Any]:
        """Fetch a candidate by id."""
        return self._request("GET", f"/candidates/{candidate_id}")

    def create_candidate(
        self, candidate: Dict[str, Any], offer_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Create a candidate.

        Args:
            candidate: candidate object (`name`, `emails`, `phones`, `social_links`,
                `links`, `cover_letter`, …).
            offer_ids: jobs to attach the candidate to.
        """
        body: Dict[str, Any] = {"candidate": candidate}
        if offer_ids:
            body["offers"] = offer_ids
        return self._request("POST", "/candidates", json=body)

    def add_note(self, candidate_id: int, body: str) -> Dict[str, Any]:
        """Add a note to a candidate."""
        return self._request(
            "POST", f"/candidates/{candidate_id}/notes",
            json={"note": {"body": body}})

    # --- Offers (jobs) ----------------------------------------------------

    def list_offers(
        self, scope: Optional[str] = None, kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List offers (jobs). `scope` : "active" | "archived" | "not_archived" ;
        `kind` : "job" | "talent_pool"."""
        params: Dict[str, Any] = {}
        if scope:
            params["scope"] = scope
        if kind:
            params["kind"] = kind
        return self._request("GET", "/offers", params=params or None)

    def get_offer(self, offer_id: int) -> Dict[str, Any]:
        """Fetch an offer (job) by id."""
        return self._request("GET", f"/offers/{offer_id}")
