"""Ashby ATS API client.

Auth = **API key** via Basic auth (the key is the *username*, empty password).
Created in Ashby: Settings → Integrations → Ashby API. Passed in clear to the
constructor.

Ashby quirk: **everything is POST** on RPC endpoints (`candidate.list`,
`candidate.info`, `job.list`, …), the JSON body carries the parameters.
Pagination is by `cursor` (next-page cursor in
`nextCursor` when `moreDataAvailable` is true).

Docs: https://developers.ashbyhq.com/

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require


class AshbyClient:
    """Ashby client — POST RPC (candidate.*, job.*, application.*)."""

    BASE_URL = "https://api.ashbyhq.com"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the client.

        Args:
            api_key: Ashby API key.
        """
        self.api_key = require(api_key, "ASHBY_API_KEY")
        self.session = requests.Session()
        self.session.auth = (self.api_key, "")
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def call(self, endpoint: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Raw RPC call (POST `endpoint`). Escape hatch for any Ashby endpoint
        not covered by a helper. Raises if `success` is false."""
        url = f"{self.BASE_URL}/{endpoint}"
        resp = self.session.post(url, json=body or {}, timeout=30)
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except Exception:
                payload = resp.text
            raise Exception(f"Ashby HTTP {resp.status_code}: {payload}")
        data = resp.json() if resp.content else {}
        if isinstance(data, dict) and data.get("success") is False:
            raise Exception(f"Ashby error: {data.get('errors') or data}")
        return data

    # --- Candidates ---------------------------------------------------------

    def list_candidates(
        self, limit: int = 50, cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List candidates (paginated). `cursor` = `nextCursor` of the previous
        page. Returns `{results, moreDataAvailable, nextCursor}`."""
        body: Dict[str, Any] = {"limit": min(limit, 100)}
        if cursor:
            body["cursor"] = cursor
        return self.call("candidate.list", body)

    def get_candidate(self, candidate_id: str) -> Dict[str, Any]:
        """Fetch a candidate by id (`candidate.info`)."""
        return self.call("candidate.info", {"id": candidate_id})

    def search_candidates(
        self, email: Optional[str] = None, name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search candidates by `email` and/or `name` (`candidate.search`)."""
        body: Dict[str, Any] = {}
        if email:
            body["email"] = email
        if name:
            body["name"] = name
        return self.call("candidate.search", body)

    def add_note(self, candidate_id: str, note: str) -> Dict[str, Any]:
        """Add a note to a candidate (`candidate.createNote`)."""
        return self.call("candidate.createNote",
                         {"candidateId": candidate_id, "note": note})

    # --- Jobs ---------------------------------------------------------------

    def list_jobs(
        self, limit: int = 50, cursor: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List jobs (`job.list`). `status`: "Open" | "Closed" | "Draft" |
        "Archived"."""
        body: Dict[str, Any] = {"limit": min(limit, 100)}
        if cursor:
            body["cursor"] = cursor
        if status:
            body["status"] = status
        return self.call("job.list", body)

    def get_job(self, job_id: str) -> Dict[str, Any]:
        """Fetch a job by id (`job.info`)."""
        return self.call("job.info", {"id": job_id})

    # --- Applications -----------------------------------------------------

    def list_applications(
        self, limit: int = 50, cursor: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List applications (`application.list`), filterable by `job_id`."""
        body: Dict[str, Any] = {"limit": min(limit, 100)}
        if cursor:
            body["cursor"] = cursor
        if job_id:
            body["jobId"] = job_id
        return self.call("application.list", body)

    def get_application(self, application_id: str) -> Dict[str, Any]:
        """Fetch an application by id (`application.info`)."""
        return self.call("application.info", {"id": application_id})
