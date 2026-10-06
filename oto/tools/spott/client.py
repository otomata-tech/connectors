"""Spott ATS/CRM API client (recruitment agencies).

Auth = **API key** passed in the `x-api-key` header (Spott: Settings → API Keys).
Base URL `https://api.gospott.com` (declared by the official OpenAPI spec).

Spott vocabulary — two words not to be confused:
- a **vacancy** = a **job** (open position). The API keeps `/vacancies` in its
  paths, its labels say "job": we expose "job".
- a **client** = the agency's client company (with its **client contacts**,
  the counterparts). A **candidate** applies via an **application**, which lives
  in a pipeline **stage**. A **placement** = a concluded placement.

Two pagination regimes coexist, and we keep that fact visible:
- the `list_*` (GET) paginate by **cursor** (`limit` ≤ 50, `cursor` returned
  in the previous response);
- the `search_*` (POST `_search`) paginate by **page** (`page`/`pageSize`) and
  take an array of **structured filters** (`type`/`operator`/`path`/`value`)
  — passed raw, the agent composes what it needs.

Docs: https://api-docs.spott.io

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require
from ..common import FieldFilter, raise_for_upstream

# Entities that have a stage pipeline (`GET /pipeline/<entity>/stages`).
# `applications`/`vacancies` = the two everyday recruiting pipelines;
# `clients`/`opportunities` = the CRM side (the agency's business development).
PIPELINE_ENTITIES = ("applications", "vacancies", "clients", "opportunities")

# Entities a note can be attached to (`links[].entityType`).
NOTE_ENTITY_TYPES = ("candidate", "vacancy", "client", "application",
                     "clientContact", "interview", "opportunity")


class SpottClient:
    """Spott client — candidates, jobs, applications, notes, clients, placements."""

    BASE_URL = "https://api.gospott.com"

    def __init__(self, api_key: Optional[str] = None,
                 field_filter: Optional[FieldFilter] = None):
        """Initialize the client.

        Args:
            api_key: Spott API key.
            field_filter: field redaction (default = `spott` policy) — responses
                carry candidate PII (emails, phone numbers, salaries).
        """
        self.api_key = require(api_key, "SPOTT_API_KEY")
        self.field_filter = field_filter or FieldFilter.from_config("spott")
        self.session = requests.Session()
        self.session.headers.update({
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> Any:
        resp = self.session.request(
            method, f"{self.BASE_URL}{path}", timeout=30, **kwargs)
        raise_for_upstream(resp, service="spott")
        if not resp.content:
            return {}
        return self.field_filter.apply(resp.json())

    @staticmethod
    def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
        """Remove unset parameters (the API rejects an explicit `null`).

        Note: `include` is declared `required` in the spec although it carries
        a default `[]` (zod→OpenAPI artifact: a field with `.default()` stays
        optional on input) — so we omit it when the caller does not ask for it.
        """
        return {k: v for k, v in params.items() if v not in (None, [], ())}

    @staticmethod
    def _page(page: Optional[int], page_size: Optional[int],
              filters: Optional[List[dict]]) -> Dict[str, Any]:
        """Common body of the `_search` endpoints (filters + page pagination)."""
        body: Dict[str, Any] = {"filters": filters or []}
        if page is not None:
            body["page"] = page
        if page_size is not None:
            body["pageSize"] = page_size
        return body

    # --- Candidates ---------------------------------------------------------

    def list_candidates(
        self,
        limit: int = 25,
        cursor: Optional[str] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
        list_ids: Optional[List[str]] = None,
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """List candidates (cursor, `limit` ≤ 50).

        Args:
            modified_since / modified_until: ISO-8601 bounds on modification.
            list_ids: restrict to Spott lists (≤ 25).
            include: relations to embed — `skills`.
        """
        return self._request("GET", "/candidates", params=self._clean({
            "limit": min(limit, 50), "cursor": cursor,
            "modifiedSince": modified_since, "modifiedUntil": modified_until,
            "listIds": list_ids, "include": include,
        }))

    def get_candidate(self, candidate_id: str) -> Dict[str, Any]:
        """Fetch a candidate (identity, contacts, linked client contacts)."""
        return self._request("GET", f"/candidates/{candidate_id}")

    def search_candidates(
        self,
        filters: Optional[List[dict]] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Search candidates by structured filters (page pagination).

        Args:
            filters: list of `{type, operator, path, value}` filters. Native
                fields: `candidate.firstName` / `candidate.lastName` (type
                `text`, operators contains|equals|startsWith|notEquals),
                `candidate.mainContact` (`entitySelect`, in|notIn),
                `candidate.createdAt` (`date`). Custom attributes
                go through the `custom*` types (see Spott docs).
        """
        return self._request("POST", "/candidates/_search",
                             json=self._page(page, page_size, filters))

    def create_candidate(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Create a candidate.

        Args:
            candidate: candidate object — `firstName` and `lastName` required;
                then `emails` / `phoneNumbers` (`{email|phoneNumber, purpose,
                isPrimary}`), `locations`, `socialMedia` (`{url, type}` with type
                LINKEDIN|TWITTER|FACEBOOK|INSTAGRAM), `education`,
                `workExperiences`, `certifications`, `languages`, `skills`,
                `compensation`, `status`, `customAttributes`…
        """
        return self._request("POST", "/candidates", json=candidate)

    def update_candidate(self, candidate_id: str,
                         patch: Dict[str, Any]) -> Dict[str, Any]:
        """Update a candidate (partial PATCH: only the provided fields)."""
        return self._request("PATCH", f"/candidates/{candidate_id}", json=patch)

    # --- Jobs (vacancies) ---------------------------------------------------

    def list_jobs(
        self,
        limit: int = 25,
        cursor: Optional[str] = None,
        company_ids: Optional[List[str]] = None,
        candidate_emails: Optional[List[str]] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """List jobs (positions; `/vacancies` endpoint, cursor, `limit` ≤ 50).

        Args:
            company_ids: restrict to the jobs of these client companies.
            candidate_emails: jobs these candidates (≤ 25 emails) applied to.
            include: relations to embed — `jobBoards`.
        """
        return self._request("GET", "/vacancies", params=self._clean({
            "limit": min(limit, 50), "cursor": cursor,
            "companyIds": company_ids, "candidateEmailAddresses": candidate_emails,
            "modifiedSince": modified_since, "modifiedUntil": modified_until,
            "include": include,
        }))

    def get_job(self, job_id: str) -> Dict[str, Any]:
        """Fetch a job (detail, custom attributes, metadata)."""
        return self._request("GET", f"/vacancies/{job_id}")

    def search_jobs(
        self,
        filters: Optional[List[dict]] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Search jobs by structured filters (page pagination).

        Args:
            filters: native fields `vacancy.name` / `vacancy.client.company.name`
                (`text`), `vacancy.client.company` / `vacancy.team` /
                `vacancy.stage` (`entitySelect`, in|notIn),
                `vacancy.stage.isOpen` (`boolean`) — "the open positions".
        """
        return self._request("POST", "/vacancies/_search",
                             json=self._page(page, page_size, filters))

    # --- Applications -------------------------------------------------------

    def list_applications(
        self,
        limit: int = 25,
        cursor: Optional[str] = None,
        job_ids: Optional[List[str]] = None,
        candidate_emails: Optional[List[str]] = None,
        is_inbound: Optional[bool] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """List applications (cursor, `limit` ≤ 50).

        Args:
            job_ids: restrict to these jobs (`vacancyIds` on the API side).
            candidate_emails: ≤ 25 candidate emails.
            is_inbound: True = spontaneous/inbound applications only.
            include: `lastActivity`, `candidate.latestWorkExperience`,
                `candidate.locations`, `candidate.emailAddresses`,
                `candidate.phoneNumbers`, `vacancy.clientContactTeam`,
                `vacancy.jobBoards`.
        """
        return self._request("GET", "/applications", params=self._clean({
            "limit": min(limit, 50), "cursor": cursor,
            "vacancyIds": job_ids, "candidateEmailAddresses": candidate_emails,
            "isInbound": is_inbound,
            "modifiedSince": modified_since, "modifiedUntil": modified_until,
            "include": include,
        }))

    def get_application(self, application_id: str,
                        include: Optional[List[str]] = None) -> Dict[str, Any]:
        """Fetch an application."""
        return self._request("GET", f"/applications/{application_id}",
                             params=self._clean({"include": include}))

    def applications_by_candidate(self, candidate_id: str) -> Dict[str, Any]:
        """A candidate's applications (jobs + spontaneous ones to a client),
        from most recent activity to oldest."""
        return self._request("GET", f"/applications/candidate/{candidate_id}")

    def applications_by_job(self, job_id: str) -> Dict[str, Any]:
        """A job's applications (candidate, status, progress in the pipeline)."""
        return self._request("GET", f"/applications/vacancy/{job_id}")

    def create_application(
        self,
        candidate_id: str,
        stage_id: str,
        job_id: Optional[str] = None,
        status_id: Optional[str] = None,
        client_id: Optional[str] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Have a candidate apply — to a job, or to a client (spontaneous).

        Args:
            stage_id: starting pipeline stage (see `pipeline_stages`).
            job_id: the targeted job; `None` + `client_id` = spontaneous application.
            status_id: status within the stage (optional).
            **extra: raw API fields (`teamUserIds`, `clientTeamContactIds`,
                `owner`, `position`).
        """
        body: Dict[str, Any] = {
            "candidateId": candidate_id, "stageId": stage_id,
            "vacancyId": job_id, "statusId": status_id,
        }
        if client_id:
            body["clientId"] = client_id
        body.update(extra)
        return self._request("POST", "/applications", json=body)

    def move_application(self, application_id: str, stage_id: str,
                         status_id: Optional[str] = None) -> Dict[str, Any]:
        """Move an application to another stage of the job's pipeline."""
        body: Dict[str, Any] = {"stageId": stage_id}
        if status_id is not None:
            body["statusId"] = status_id
        return self._request("PUT", f"/applications/{application_id}/move",
                             json=body)

    def application_activities(self, application_id: str) -> Dict[str, Any]:
        """An application's activity log (stage changes, actions)."""
        return self._request("GET", f"/applications/{application_id}/activities")

    def pipeline_stages(self, entity: str = "applications",
                        template_id: Optional[str] = None) -> Dict[str, Any]:
        """Ordered stages of a pipeline.

        Args:
            entity: applications | vacancies | clients | opportunities.
            template_id: pipeline of a specific template (applications only).
        """
        if entity not in PIPELINE_ENTITIES:
            raise ValueError(
                f"unknown Spott pipeline: {entity!r} — expected "
                f"{', '.join(PIPELINE_ENTITIES)}")
        return self._request("GET", f"/pipeline/{entity}/stages",
                             params=self._clean({"templateId": template_id}))

    # --- Notes --------------------------------------------------------------

    def list_notes(
        self,
        limit: int = 25,
        cursor: Optional[str] = None,
        candidate_id: Optional[str] = None,
        client_contact_id: Optional[str] = None,
        source: Optional[str] = None,
        label_ids: Optional[List[str]] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List notes (cursor, `limit` ≤ 50).

        Args:
            source: phone | phoneInbound | phoneOutbound | inPerson |
                onlineMeeting | callAttempted.
        """
        return self._request("GET", "/notes", params=self._clean({
            "limit": min(limit, 50), "cursor": cursor,
            "candidateId": candidate_id, "clientContactId": client_contact_id,
            "source": source, "labelIds": label_ids,
            "modifiedSince": modified_since, "modifiedUntil": modified_until,
        }))

    def create_note(
        self,
        content: str,
        title: Optional[str] = None,
        links: Optional[List[dict]] = None,
        source: Optional[str] = None,
        label_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a note, optionally attached to records.

        Args:
            links: `[{"entityType": …, "entityId": …}]` — entityType among
                candidate, vacancy, client, application, clientContact,
                interview, opportunity.
            source: channel of the exchange (see `list_notes`).
        """
        for link in links or []:
            kind = link.get("entityType")
            if kind not in NOTE_ENTITY_TYPES:
                raise ValueError(
                    f"unknown Spott entityType: {kind!r} — expected "
                    f"{', '.join(NOTE_ENTITY_TYPES)}")
        body: Dict[str, Any] = {"title": title, "content": content}
        if links:
            body["links"] = links
        if source:
            body["source"] = source
        if label_ids:
            body["labelIds"] = label_ids
        return self._request("POST", "/notes", json=body)

    # --- Clients (the agency's client companies) ----------------------------

    def list_clients(
        self,
        limit: int = 25,
        cursor: Optional[str] = None,
        list_ids: Optional[List[str]] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List clients (companies; cursor, `limit` ≤ 50)."""
        return self._request("GET", "/clients", params=self._clean({
            "limit": min(limit, 50), "cursor": cursor, "listIds": list_ids,
            "modifiedSince": modified_since, "modifiedUntil": modified_until,
        }))

    def get_client(self, client_id: str) -> Dict[str, Any]:
        """Fetch a client (company, contacts, industry, size, hierarchies)."""
        return self._request("GET", f"/clients/{client_id}")

    def search_clients(
        self,
        filters: Optional[List[dict]] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Search clients by structured filters (page pagination).

        Args:
            filters: native fields `client.company.name` / `.domain` /
                `.description` (`text`), `client.stage` / `client.contacts`
                (`entitySelect`).
        """
        return self._request("POST", "/clients/_search",
                             json=self._page(page, page_size, filters))

    def list_client_contacts(
        self,
        limit: int = 25,
        cursor: Optional[str] = None,
        client_ids: Optional[List[str]] = None,
        list_ids: Optional[List[str]] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List client contacts (counterparts; cursor, `limit` ≤ 50)."""
        return self._request("GET", "/clients/contacts", params=self._clean({
            "limit": min(limit, 50), "cursor": cursor,
            "client_ids": client_ids, "listIds": list_ids,
            "modifiedSince": modified_since, "modifiedUntil": modified_until,
        }))

    # --- Placements ---------------------------------------------------------

    def list_placements(
        self,
        page: int = 0,
        page_size: int = 20,
        company_id: Optional[str] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List placements (candidate, company, job, fees).

        ⚠️ **Page** pagination here (no cursor): `page` (0-based),
        `pageSize` ≤ 100.
        """
        return self._request("GET", "/placements", params=self._clean({
            "page": page, "pageSize": min(page_size, 100),
            "companyId": company_id,
            "modifiedSince": modified_since, "modifiedUntil": modified_until,
        }))

    # --- Cross-cutting ------------------------------------------------------

    def search_people(self, query: str, limit: int = 25) -> Dict[str, Any]:
        """Search a person (candidates ∪ client contacts) by name, email or
        phone — fuzzy matching, ranked by relevance. `limit` ≤ 100."""
        return self._request("GET", "/search/people", params={
            "query": query, "limit": min(limit, 100)})

    def list_users(self, include_deactivated: bool = False) -> Dict[str, Any]:
        """List Spott users (recruiters). Also serves as a connection
        probe: the smallest authenticated call of the API."""
        return self._request("GET", "/users", params=self._clean({
            "includeDeactivated": include_deactivated or None}))
