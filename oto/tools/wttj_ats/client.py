"""Welcome to the Jungle ATS API client (ex-Welcome Kit) — the recruiter side:
jobs, candidates in a job's pipeline, comments, pipeline moves.

Bearer token (`Authorization: Bearer <token>`), never in the query string. One
method = one endpoint of the public reference (https://developers.welcomekit.co);
responses are returned as-is, the client invents no semantics.

Scope: what an agent needs to run a hiring pipeline — read jobs and their
stages, list and read candidates, create a candidate, move or archive one,
comment on one, read the history of moves. Job creation and edition, emails
(which reach real people), documents, departments, offices and the employer
branding API are not covered here.

What the caller needs to know, and cannot guess:

- **The token is not self-served**: the account owner asks WTTJ for it, and it
  carries OAuth scopes chosen at that time (`jobs_r`, `candidates_r`/`_rw`,
  `comments_w`, `moves_r`, `me_r`, `organizations_r`…). A call outside the
  token's scopes is refused (`invalid_scope`). `my_candidates_*` scopes only
  reach the candidates this token created.
- **Everything hangs off an organization or a job `reference`** (opaque
  strings). There is no global candidate list: `list_candidates` needs a
  `job_reference`, `list_jobs` and `list_moves` an `organization_reference`.
  `get_current_user(organizations=True)` lists the organizations the token
  can reach.
- **A job's pipeline stages are read on the job** (`get_job(stages=True)`):
  each `{id, name, reference, visible, candidates_count}`. A stage is
  addressed by its integer `id` — its `reference` may be `null`.
- **Moving a candidate = `update_candidate(job_stage_id=…)`**; there is no
  dedicated endpoint. Archiving = `update_candidate(archived=True)`.
- **Comments are write-only** in this API: no endpoint lists them back.
- **Lists are bare JSON arrays**, paged by `page` / `per_page`; the API states
  neither a default nor a maximum page size. Dates filters
  (`created_after`, `updated_after`, `published_after`) take `YYYY-MM-DD`.
- Booleans in the query string are sent as `true` / `false`.
- Errors carry `{error, error_description}` with `error` in `not_found`,
  `validation_failed`, `unauthorized`, `invalid_scope`; they surface as
  `UpstreamHTTPError`. No rate limit is published; a 429 is not retried here.

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

from ..common import raise_for_upstream
from ..common.credentials import require

#: (connect, read) — never an unbounded wait.
_HTTP_TIMEOUT = (10, 60)

BASE_URL = "https://www.welcomekit.co/api/v1/external"


def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
    """Drops `None` values and writes booleans as `true`/`false` — an omitted
    kwarg must not become the literal string 'None', nor `True` the string
    'True'."""
    return {k: (str(v).lower() if isinstance(v, bool) else v)
            for k, v in params.items() if v is not None}


def _segment(name: str, value: str) -> str:
    """A reference placed in a path: escaped, and never `.`/`..` (which `quote`
    leaves intact and which would change the path)."""
    if not isinstance(value, str) or not value.strip() or value in (".", ".."):
        raise ValueError(f"`{name}` must be a non-empty reference.")
    return quote(value, safe="")


class WttjAtsClient:
    """Welcome to the Jungle ATS API client, Bearer token."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: API token issued by WTTJ for the account, with the scopes
                the calls need (see the module docstring).
        """
        self.api_key = require(api_key, "WTTJ_API_KEY")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        self.session.headers["Accept"] = "application/json"

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 body: Optional[Dict[str, Any]] = None) -> Any:
        resp = self.session.request(
            method, f"{BASE_URL}{path}", params=_clean(params or {}),
            json=None if body is None else {k: v for k, v in body.items()
                                            if v is not None},
            timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="wttj")
        return resp.json() if (resp.content or b"").strip() else None

    def _get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_current_user(self, *, organizations: Optional[bool] = None,
                         jobs: Optional[bool] = None,
                         stages: Optional[bool] = None) -> Any:
        """GET /users/current (scope `me_r`) — the token's user
        (`display_name`, `avatar_url`), plus, on request, the organizations,
        jobs and stages it can reach (`organizations_r`, `jobs_r` needed)."""
        return self._get("/users/current", organizations=organizations,
                         jobs=jobs, stages=stages)

    def get_organization(self, reference: str, *,
                         offices: Optional[bool] = None,
                         websites: Optional[bool] = None) -> Any:
        """GET /organizations/{reference} (scope `organizations_r`) —
        `reference, name, slug, description, subsidiaries, logo_url, sectors,
        nb_employees, …`, plus `offices` / `websites` on request."""
        return self._get(f"/organizations/{_segment('reference', reference)}",
                         offices=offices, websites=websites)

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def list_jobs(self, organization_reference: str, *,
                  status: Optional[str] = None,
                  office: Optional[bool] = None,
                  stages: Optional[bool] = None,
                  websites: Optional[bool] = None,
                  created_after: Optional[str] = None,
                  updated_after: Optional[str] = None,
                  published_after: Optional[str] = None,
                  page: Optional[int] = None,
                  per_page: Optional[int] = None) -> Any:
        """GET /jobs (scope `jobs_r`) — the jobs of one organization, as an
        array of `{reference, name, status, description, profile,
        contract_type, salary, remote, office_id, created_at, published_at, …}`.

        Args:
            organization_reference: required.
            status: `draft` | `published` | `archived`.
            office / stages / websites: embed these objects in each job.
            created_after / updated_after / published_after: `YYYY-MM-DD`.
        """
        return self._get("/jobs", organization_reference=organization_reference,
                         status=status, office=office, stages=stages,
                         websites=websites, created_after=created_after,
                         updated_after=updated_after,
                         published_after=published_after, page=page,
                         per_page=per_page)

    def get_job(self, reference: str, *,
                stages: Optional[bool] = None,
                office: Optional[bool] = None,
                candidates_count: Optional[bool] = None,
                organization: Optional[bool] = None,
                websites: Optional[bool] = None,
                application_fields: Optional[bool] = None) -> Any:
        """GET /jobs/{reference} (scope `jobs_r`) — one job. `stages=True`
        embeds its pipeline `[{id, name, reference, visible,
        candidates_count}]`; `candidates_count=True` (scope `candidates_r`)
        adds the total number of candidates."""
        return self._get(f"/jobs/{_segment('reference', reference)}",
                         stages=stages, office=office,
                         candidates_count=candidates_count,
                         organization=organization, websites=websites,
                         application_fields=application_fields)

    # ------------------------------------------------------------------
    # Candidates
    # ------------------------------------------------------------------

    def list_candidates(self, job_reference: str, *,
                        email: Optional[str] = None,
                        referrer: Optional[str] = None,
                        origin: Optional[str] = None,
                        job_stage_id: Optional[int] = None,
                        job_stage_reference: Optional[str] = None,
                        archived: Optional[bool] = None,
                        created_after: Optional[str] = None,
                        updated_after: Optional[str] = None,
                        stage: Optional[bool] = None,
                        tags: Optional[bool] = None,
                        page: Optional[int] = None,
                        per_page: Optional[int] = None) -> Any:
        """GET /candidates (scope `candidates_r`) — the candidates of one job,
        as an array of `{reference, job_reference, stage_id, profile:
        {firstname, lastname, email, phone, …}, cover_letter, resume_url,
        origin, archived, created_at, updated_at, …}`.

        Args:
            job_reference: required — there is no cross-job listing.
            job_stage_id / job_stage_reference: only candidates at this stage.
            archived: filter on the archived flag.
            created_after / updated_after: `YYYY-MM-DD`.
            stage / tags: embed the stage object / the tags.
        """
        return self._get("/candidates", job_reference=job_reference, email=email,
                         referrer=referrer, origin=origin,
                         job_stage_id=job_stage_id,
                         job_stage_reference=job_stage_reference,
                         archived=archived, created_after=created_after,
                         updated_after=updated_after, stage=stage, tags=tags,
                         page=page, per_page=per_page)

    def get_candidate(self, reference: str, *, stage: Optional[bool] = None,
                      tags: Optional[bool] = None) -> Any:
        """GET /candidates/{reference} (scope `candidates_r`) — one candidate,
        same fields as a list item."""
        return self._get(f"/candidates/{_segment('reference', reference)}",
                         stage=stage, tags=tags)

    def create_candidate(self, organization_reference: str, job_reference: str,
                         job_stage_id: int, email: str, firstname: str,
                         lastname: str, **fields: Any) -> Any:
        """POST /candidates (scope `candidates_rw` or `my_candidates_rw`) —
        add a candidate to a job, at a given stage.

        Args:
            job_stage_id: integer id of a stage of this job (`get_job(stages=True)`).
            **fields: optional — `phone`, `subtitle`, `tag_list` (comma
                separated), `cover_letter`, `comment`, `referrer`, `archived`,
                `remote_resume_url` (PDF/DOC/DOCX/ODT, 5 MB), `remote_image_url`,
                `remote_portfolio_url` (PDF, 10 MB), `media_linkedin`,
                `media_github`, … (`media_<network>`).
        """
        body = {"organization_reference": organization_reference,
                "job_reference": job_reference, "job_stage_id": job_stage_id,
                "email": email, "firstname": firstname, "lastname": lastname,
                **fields}
        return self._request("POST", "/candidates", body=body)

    def update_candidate(self, reference: str, **fields: Any) -> Any:
        """PUT /candidates/{reference} (scope `candidates_rw`) — partial update:
        only the fields passed change. `job_stage_id` moves the candidate to
        that stage; `archived=True` archives it. Same optional fields as
        `create_candidate`."""
        if not fields:
            raise ValueError("update_candidate: nothing to update.")
        return self._request("PUT", f"/candidates/{_segment('reference', reference)}",
                             body=fields)

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def create_comment(self, candidate_reference: str, content: str) -> Any:
        """POST /comments (scopes `comments_w` + `candidates_rw`) — a comment
        on a candidate; `content` is text, HTML or markdown. `{id, content,
        raw_content}`."""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("create_comment: `content` must be non-empty.")
        return self._request("POST", "/comments",
                             body={"candidate_reference": candidate_reference,
                                   "content": content})

    # ------------------------------------------------------------------
    # Moves (pipeline history)
    # ------------------------------------------------------------------

    def list_moves(self, organization_reference: str, *,
                   job_reference: Optional[str] = None,
                   page: Optional[int] = None,
                   per_page: Optional[int] = None) -> Any:
        """GET /moves (scope `moves_r`) — stage changes, as an array of
        `{candidate: {reference}, from: {stage, organization, job}, to: {…},
        created_at, updated_at}`."""
        return self._get("/moves", organization_reference=organization_reference,
                         job_reference=job_reference, page=page, per_page=per_page)
