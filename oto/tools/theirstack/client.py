"""TheirStack API client — job postings by employer + technologies used.

API v1 (https://api.theirstack.com, docs https://theirstack.com/en/docs/api-reference),
**Bearer** auth. Two search surfaces, each a POST whose body IS the TheirStack
filter DSL (~110 fields for jobs, ~60 for companies): the
client passes it through as is, it does not re-type it — the caller composes the dict, the
vendor docs are authoritative for field names. Response = `{metadata, data}`.

- `search_jobs(payload)`      → POST /v1/jobs/search
- `search_companies(payload)` → POST /v1/companies/search
- `credit_balance()`          → GET  /v0/billing/credit-balance (free; auth probe)

Most useful filters (extracted from the 2026-08-17 OpenAPI spec):
- pagination: `page` (0-based, default 0), `limit` (default 25), `offset`, `cursor`;
- freshness: `posted_at_max_age_days` (0 = today), `posted_at_gte`/`_lte`,
  `discovered_at_max_age_days`;
- company: `company_name_or` (exact, CASE-SENSITIVE), `company_name_case_insensitive_or`,
  `company_name_partial_match_or`, `company_domain_or`, `company_linkedin_url_or`,
  `company_country_code_or` (ISO2, HQ country), `min_employee_count`/`max_employee_count`,
  `industry_or`, `company_technology_slug_or`;
- job: `job_title_or` (keywords), `job_title_pattern_or` (regex), `job_country_code_or`
  (ISO2), `job_location_pattern_or`, `job_seniority_or`, `job_technology_slug_or`,
  `job_description_pattern_or`, `workplace_types_or`, `employment_statuses_or`;
- cost: `include_total_results` (slow, only set on the 1st call), `blur_company_data`
  (blurred preview, no credit — on request to TheirStack for new workspaces).

⚠️ `/v1/jobs/search` requires AT LEAST one of: `posted_at_max_age_days`, `posted_at_gte`,
`posted_at_lte`, `company_domain_or`, `company_linkedin_url_or`, `company_name_or` —
otherwise the API refuses (performance reasons). Nothing is added here: the upstream refusal
surfaces as is (`UpstreamHTTPError`, status 4xx), readable by the caller.

Billing (2026-08-17 OpenAPI spec): credit is counted per RECORD returned —
1 API credit per job on `/v1/jobs/search`, 3 per company on
`/v1/companies/search`; `limit` therefore bounds the spend. `metadata.truncated_results`
/ `truncated_companies` say how many results were NOT returned for lack of
credits.

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never wait indefinitely


class TheirStackClient:
    """TheirStack v1 client (https://api.theirstack.com), Bearer auth."""

    BASE_URL = "https://api.theirstack.com"

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: TheirStack key.
        """
        self.api_key = require(api_key, "THEIRSTACK_API_KEY")
        self.session = requests.Session()
        # The key goes in a HEADER, never in the query string (it would land in the URL,
        # hence in the message of any requests exception, the logs and Sentry).
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> Any:
        resp = self.session.request(
            method, f"{self.BASE_URL}{path}", timeout=_HTTP_TIMEOUT, **kwargs)
        raise_for_upstream(resp, service="theirstack")
        return resp.json() if resp.content else {}

    @staticmethod
    def _payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict (the TheirStack filter DSL).")
        return dict(payload)

    # --- search -------------------------------------------------------------

    def search_jobs(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST /v1/jobs/search — job postings filtered by the TheirStack DSL.

        `payload` = the body as documented by the vendor (`page`, `limit`,
        `posted_at_max_age_days`, `company_name_or`, `company_country_code_or`,
        `job_country_code_or`, `job_title_or`, `job_technology_slug_or`…). Returns
        `{metadata: {total_results?, truncated_results, truncated_companies,
        total_companies?}, data: [job…]}` — each job carries `company`, `job_title`,
        `date_posted`, `url`, `location`, `company_domain`, `technology_slugs`,
        `company_object`, `description`…

        Cost: 1 API credit per job returned → bound `limit`.
        """
        return self._request("POST", "/v1/jobs/search", json=self._payload(payload))

    def search_companies(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST /v1/companies/search — companies filtered by firmographics,
        technologies (`company_technology_slug_or`) and hiring signals
        (`job_filters` + `min_num_jobs_found`).

        Returns `{metadata, data: [company…]}` — each company carries `name`,
        `domain`, `employee_count`, `industry`, `country_code`, `technology_names`,
        `technology_slugs`, `num_jobs`, `jobs_found`, `technologies_found`,
        `linkedin_url`, `annual_revenue_usd`…

        Cost: 3 API credits per company returned → bound `limit`. A company
        absent from the database (partial SME coverage) returns `data: []` — this is
        not an error.
        """
        return self._request("POST", "/v1/companies/search", json=self._payload(payload))

    # --- account ------------------------------------------------------------

    def credit_balance(self) -> Dict[str, Any]:
        """GET /v0/billing/credit-balance — the team's credit balance. FREE
        authenticated call: it is the "does the key work?" probe without spending."""
        return self._request("GET", "/v0/billing/credit-balance")
