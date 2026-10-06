"""LinkedIn premium products: contracts, InMail, pipeline, jobs & candidates.

Extracted from `client.py` (split by domain, frozen public surface):
the bodies are unchanged. This mixin is never instantiated on its own — it is
composed into `UnipileClient`, which provides the transport (`_request`,
`_acct`, `_norm`, `_by_shape`, `session`).
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

from ..errors import UnipileError


class _PremiumMixin:
    """LinkedIn premium products: contracts, InMail, pipeline, jobs & candidates."""

    def list_contracts(self) -> dict:
        return self._request("GET", self._acct("/linkedin/contracts"))

    def select_contract(self, contract_id: str) -> dict:
        return self._request(
            "POST",
            self._acct(f"/linkedin/contracts/{quote(contract_id, safe='')}/select"),
        )

    def inmail_balance(self) -> dict:
        """InMail balance. v2: `GET /linkedin/inmail-credits`. Response `{object, credits}`."""
        return self._request("GET", self._acct("/linkedin/inmail-credits"))

    def endorse_profile(self, profile_id: str, skill_endorsement_id: int) -> dict:
        """v2: `POST /linkedin/member/{member_id}/endorse-skill`, body
        `{skill_id}`."""
        return self._request(
            "POST",
            self._acct(f"/linkedin/member/{quote(profile_id, safe='')}/endorse-skill"),
            json={"skill_id": str(skill_endorsement_id)},
        )

    def member_action(self, user_id: str, api: str, action: str,
                     hiring_project_id: Optional[str] = None,
                     stage: Optional[str] = None,
                     list_id: Optional[str] = None) -> dict:
        """Premium action (lead save / recruiter pipeline). v2 splits these
        actions by product; we map the common cases, otherwise a clear error."""
        if api == "sales_navigator" and action == "saveLead":
            if not list_id:
                raise UnipileError("saveLead: list_id (lead-list) required.")
            return self._request(
                "POST",
                self._acct(
                    f"/linkedin/sales-navigator/lead-lists/{quote(list_id, safe='')}/save"
                ),
                json={"user_id": user_id},
            )
        if api == "recruiter" and action in (
            "addCandidateToPipeline", "addApplicantToPipeline"
        ):
            if not hiring_project_id:
                raise UnipileError(
                    "recruiter pipeline: hiring_project_id required."
                )
            body: dict[str, Any] = {"user_id": user_id}
            if stage:
                body["stage"] = stage
            return self._request(
                "POST",
                self._acct(
                    f"/linkedin/recruiter/projects/"
                    f"{quote(hiring_project_id, safe='')}/pipeline/candidate/save"
                ),
                json=body,
            )
        raise UnipileError(
            f"member_action: combination api={api!r} action={action!r} "
            "not mapped."
        )

    # ---- recruiter: jobs & candidates -----------------------------------

    def list_job_postings(self, cursor: Optional[str] = None,
                         limit: Optional[int] = None) -> dict:
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        if limit:
            params["limit"] = limit
        return self._norm(self._request(
            "GET", self._acct("/linkedin/jobs"), params=params
        ))

    def get_job_posting(self, job_id: str) -> dict:
        return self._request(
            "GET", self._acct(f"/linkedin/jobs/{quote(job_id, safe='')}")
        )

    def list_job_applicants(self, job_id: str, cursor: Optional[str] = None,
                           limit: Optional[int] = None) -> dict:
        """v2: `POST /linkedin/jobs/{job_id}/applicants` (getClassicApplicants)."""
        body: dict[str, Any] = {}
        if cursor:
            body["cursor"] = cursor
        if limit:
            body["limit"] = limit
        return self._norm(self._request(
            "POST", self._acct(f"/linkedin/jobs/{quote(job_id, safe='')}/applicants"),
            json=body,
        ))

    def get_job_applicant(self, job_id: str, applicant_id: str) -> dict:
        return self._request(
            "GET",
            self._acct(
                f"/linkedin/jobs/{quote(job_id, safe='')}"
                f"/applicants/{quote(applicant_id, safe='')}"
            ),
        )

    def list_hiring_projects(self, cursor: Optional[str] = None,
                            limit: Optional[int] = None) -> dict:
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        if limit:
            params["limit"] = limit
        return self._norm(self._request(
            "GET", self._acct("/linkedin/recruiter/projects"), params=params
        ))
