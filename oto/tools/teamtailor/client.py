"""Teamtailor ATS API client.

Auth = **API key** in the `Authorization: Token token=<key>` header, plus a
mandatory API version header (`X-Api-Version`). Key created in
Teamtailor: Settings → API keys (Admin). Passed in clear to the constructor.

The API follows the **JSON:API** convention: resources have `{type, id,
attributes, relationships}`, filtering goes through `filter[...]` and
pagination through `page[number]`/`page[size]`. The helpers expose a simple
surface (candidates, jobs, applications); `call` remains the generic escape hatch.

Docs: https://docs.teamtailor.com/

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream

# Pinned Teamtailor API version (mandatory header). Bump it deliberately.
_API_VERSION = "20210218"


class TeamtailorClient:
    """Client Teamtailor v1 (JSON:API) — candidats, jobs, candidatures."""

    BASE_URL = "https://api.teamtailor.com/v1"

    def __init__(self, api_key: Optional[str] = None,
                 api_version: str = _API_VERSION):
        """Initialize the client.

        Args:
            api_key: Teamtailor API key.
            api_version: value of the `X-Api-Version` header (pinned date).
        """
        self.api_key = require(api_key, "TEAMTAILOR_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token token={self.api_key}",
            "X-Api-Version": api_version,
            "Content-Type": "application/vnd.api+json",
        })

    def call(self, method: str, path: str, **kwargs) -> Any:
        """Raw JSON:API call (generic escape hatch). `path` starts with `/`."""
        url = f"{self.BASE_URL}{path}"
        resp = self.session.request(method, url, timeout=30, **kwargs)
        raise_for_upstream(resp, service="teamtailor")
        return resp.json() if resp.content else {}

    @staticmethod
    def _page(page_size: int, page_number: int,
              extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "page[size]": min(page_size, 30), "page[number]": page_number,
        }
        if extra:
            params.update(extra)
        return params

    # --- Candidates ---------------------------------------------------------

    def list_candidates(
        self, page_size: int = 30, page_number: int = 1,
        email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List candidates (paginated). `email` filters by exact email."""
        extra = {"filter[email]": email} if email else None
        return self.call("GET", "/candidates",
                        params=self._page(page_size, page_number, extra))

    def get_candidate(self, candidate_id: str) -> Dict[str, Any]:
        """Fetch a candidate by id."""
        return self.call("GET", f"/candidates/{candidate_id}")

    def create_candidate(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Create a candidate.

        Args:
            attributes: JSON:API attributes (`first-name`, `last-name`, `email`,
                `phone`, `pitch`, `tags`, …). Wrapped in `{data:{type, attributes}}`.
        """
        body = {"data": {"type": "candidates", "attributes": attributes}}
        return self.call("POST", "/candidates", json=body)

    # --- Jobs ---------------------------------------------------------------

    def list_jobs(
        self, page_size: int = 30, page_number: int = 1,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List jobs (positions). `status`: "open" | "draft" | "archived" |
        "unlisted"."""
        extra = {"filter[status]": status} if status else None
        return self.call("GET", "/jobs",
                        params=self._page(page_size, page_number, extra))

    def get_job(self, job_id: str) -> Dict[str, Any]:
        """Fetch a job by id."""
        return self.call("GET", f"/jobs/{job_id}")

    # --- Applications -------------------------------------------------------

    def list_job_applications(
        self, page_size: int = 30, page_number: int = 1,
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List applications. `job_id` filters by position."""
        extra = {"filter[job-id]": job_id} if job_id else None
        return self.call("GET", "/job-applications",
                        params=self._page(page_size, page_number, extra))
