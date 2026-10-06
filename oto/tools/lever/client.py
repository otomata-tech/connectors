"""Lever ATS API client.

Auth = **API key** via Basic auth (the key is the *username*, empty password).
Created in Lever: Settings → Integrations and API → API credentials. Passed in
plain text to the constructor.

Lever vocabulary: a candidate in a pipeline = an **opportunity**; a job
= a **posting**. Writes (creation, note) accept a `perform_as` (id of a
Lever user to act on behalf of).

Docs: https://hire.lever.co/developer/documentation

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream


class LeverClient:
    """Lever Hire v1 client — opportunities (candidates), postings, notes."""

    BASE_URL = "https://api.lever.co/v1"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the client.

        Args:
            api_key: Lever API key.
        """
        self.api_key = require(api_key, "LEVER_API_KEY")
        self.session = requests.Session()
        self.session.auth = (self.api_key, "")
        self.session.headers.update({"Content-Type": "application/json"})

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.BASE_URL}{path}"
        resp = self.session.request(method, url, timeout=30, **kwargs)
        raise_for_upstream(resp, service="lever")
        return resp.json() if resp.content else {}

    # --- Opportunities (candidates in a pipeline) ----------------------------

    def list_opportunities(
        self,
        limit: int = 50,
        offset: Optional[str] = None,
        posting_id: Optional[str] = None,
        stage_id: Optional[str] = None,
        email: Optional[str] = None,
        expand: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """List opportunities (candidates). Returns `{data, hasNext, next}` —
        pass `next` as `offset` for the next page.

        Args:
            posting_id / stage_id: pipeline filters.
            email: filter by exact candidate email.
            expand: fields to expand (e.g. ["applications", "stage", "owner"]).
        """
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if offset:
            params["offset"] = offset
        if posting_id:
            params["posting_id"] = posting_id
        if stage_id:
            params["stage_id"] = stage_id
        if email:
            params["email"] = email
        if expand:
            params["expand"] = expand
        return self._request("GET", "/opportunities", params=params)

    def get_opportunity(
        self, opportunity_id: str, expand: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Fetch an opportunity (candidate) by id."""
        params = {"expand": expand} if expand else None
        return self._request("GET", f"/opportunities/{opportunity_id}", params=params)

    def add_candidate(
        self, candidate: Dict[str, Any], perform_as: str,
        posting_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a candidate (opportunity).

        Args:
            candidate: Lever candidate object (`name`, `emails`, `phones`, `links`,
                `tags`, `sources`, …).
            perform_as: id of the Lever user to create on behalf of (required).
            posting_ids: postings to attach the candidate to.
        """
        body = dict(candidate)
        if posting_ids:
            body["postings"] = posting_ids
        return self._request("POST", "/opportunities", json=body,
                             params={"perform_as": perform_as})

    def add_note(
        self, opportunity_id: str, value: str, perform_as: str,
    ) -> Dict[str, Any]:
        """Add a note to an opportunity (candidate).

        Args:
            perform_as: id of the Lever user authoring the note (required).
        """
        return self._request(
            "POST", f"/opportunities/{opportunity_id}/notes",
            json={"value": value}, params={"perform_as": perform_as})

    # --- Postings (jobs) ----------------------------------------------------

    def list_postings(
        self, limit: int = 50, offset: Optional[str] = None,
        state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List postings (jobs). `state`: "published" | "internal" |
        "closed" | "draft" | "pending" | "rejected"."""
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if offset:
            params["offset"] = offset
        if state:
            params["state"] = state
        return self._request("GET", "/postings", params=params)

    def get_posting(self, posting_id: str) -> Dict[str, Any]:
        """Fetch a posting (job) by id."""
        return self._request("GET", f"/postings/{posting_id}")

    # --- Reference data -------------------------------------------------------

    def list_stages(self) -> Dict[str, Any]:
        """List the pipeline stages (reference data)."""
        return self._request("GET", "/stages")

    def list_users(self, limit: int = 50, offset: Optional[str] = None) -> Dict[str, Any]:
        """List Lever users (recruiters) — for `perform_as`."""
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if offset:
            params["offset"] = offset
        return self._request("GET", "/users", params=params)
