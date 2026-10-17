"""Prospecting: the searcher, enrichment of people and companies, email
validation and job openings."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .shapes import items, page_params, path_id

#: Body filters of `POST /people/search` (all optional, combined with AND).
PEOPLE_SEARCH_FILTERS = (
    "person_name", "person_titles", "person_keywords", "person_exclude_keywords",
    "company_keywords", "company_exclude_keywords", "person_title_exact_match",
    "person_seniorities", "person_departments", "person_job_functions",
    "person_locations", "person_exclude_locations", "company_locations",
    "company_exclude_locations", "company_names", "company_exclude_names",
    "company_ids", "company_exclude_ids", "education_company_ids",
    "education_company_names", "company_domains", "company_exclude_domains",
    "company_linkedin_urls", "company_exclude_linkedin_urls",
    "company_domains_match_subsidiaries_and_affiliates", "company_focuses",
    "company_types", "company_sizes", "company_industries",
    "company_exclude_industries", "linkedin_networks", "exclude_linkedin_networks",
    "company_investors", "company_shares_investors_with", "company_revenue",
    "company_founded_date", "company_web_traffic_rank", "job_openings", "news",
    "headcount_growth", "crm_account_owner", "company_last_funding",
    "person_linkedin_presence", "company_linkedin_presence",
)

#: Body filters of `POST /companies/search` (all optional, combined with AND).
COMPANY_SEARCH_FILTERS = (
    "company_names", "company_exclude_names", "company_ids", "company_exclude_ids",
    "company_locations", "company_exclude_locations", "company_domains",
    "company_exclude_domains", "company_linkedin_urls",
    "company_exclude_linkedin_urls",
    "company_domains_match_subsidiaries_and_affiliates", "company_focuses",
    "company_types", "company_sizes", "company_industries",
    "company_exclude_industries", "company_investors",
    "company_shares_investors_with", "company_keywords", "company_exclude_keywords",
    "company_revenue", "company_founded_date", "company_web_traffic_rank",
    "job_openings", "news", "headcount_growth", "crm_account_owner",
    "company_last_funding", "company_linkedin_presence",
)

#: Statuses accepted by the `cancel_*` batch calls.
_CANCELED = {"status": "canceled"}


def _filters(allowed: tuple, filters: Dict[str, Any]) -> Dict[str, Any]:
    unknown = sorted(set(filters) - set(allowed))
    if unknown:
        raise ValueError(f"unknown search filter(s): {', '.join(unknown)}")
    return filters


class ProspectingMixin:
    """Needs `_request` (see `AmplemarketClient`)."""

    # --- searcher ---------------------------------------------------------------

    def search_people(self, page: Optional[int] = None,
                      page_size: Optional[int] = None, **filters: Any) -> Any:
        """POST /people/search — people matching the filters
        (`PEOPLE_SEARCH_FILTERS`), page by page (page_size capped at 100 by
        Amplemarket). Answers `results` and `_pagination`; reveals no email."""
        body = {**_filters(PEOPLE_SEARCH_FILTERS, filters),
                "page": page, "page_size": page_size}
        return self._request("POST", "/people/search", body=body)

    def search_companies(self, page: Optional[int] = None,
                         page_size: Optional[int] = None, **filters: Any) -> Any:
        """POST /companies/search — companies matching the filters
        (`COMPANY_SEARCH_FILTERS`), page by page (page_size capped at 50)."""
        body = {**_filters(COMPANY_SEARCH_FILTERS, filters),
                "page": page, "page_size": page_size}
        return self._request("POST", "/companies/search", body=body)

    # --- people enrichment ------------------------------------------------------

    def find_person(self, email: Optional[str] = None,
                    linkedin_url: Optional[str] = None,
                    name: Optional[str] = None, title: Optional[str] = None,
                    company_name: Optional[str] = None,
                    company_domain: Optional[str] = None,
                    reveal_email: Optional[bool] = None,
                    reveal_phone_numbers: Optional[bool] = None) -> Any:
        """GET /people/find — enrich ONE person, matched by email, LinkedIn URL,
        or name + company (name or domain). Revealing an email or phone
        numbers consumes credits. 404 `person_not_found`, 451 when the person
        was removed for privacy reasons."""
        if not (email or linkedin_url or (name and (company_name or company_domain))):
            raise ValueError("find_person needs email, linkedin_url, or name with "
                             "company_name or company_domain")
        return self._request("GET", "/people/find", params={
            "email": email, "linkedin_url": linkedin_url, "name": name,
            "title": title, "company_name": company_name,
            "company_domain": company_domain, "reveal_email": reveal_email,
            "reveal_phone_numbers": reveal_phone_numbers})

    def start_person_enrichment(self, leads: List[Dict[str, Any]],
                                reveal_email: Optional[bool] = None,
                                reveal_phone_numbers: Optional[bool] = None) -> Any:
        """POST /people/enrichment-requests — start a batch (<= 10,000 leads,
        each `{email | linkedin_url | name + company_name/company_domain,
        title?}`). Answers 202 with the batch `id`; read it with
        `get_person_enrichment`."""
        if not leads:
            raise ValueError("leads must be a non-empty list")
        return self._request("POST", "/people/enrichment-requests", body={
            "leads": leads, "reveal_email": reveal_email,
            "reveal_phone_numbers": reveal_phone_numbers})

    def get_person_enrichment(self, request_id: str) -> Any:
        """GET /people/enrichment-requests/{id} — the batch's status and results."""
        return self._request(
            "GET", f"/people/enrichment-requests/{path_id('request_id', request_id)}")

    def cancel_person_enrichment(self, request_id: str) -> Any:
        """PATCH /people/enrichment-requests/{id} — stop a running batch."""
        return self._request(
            "PATCH", f"/people/enrichment-requests/{path_id('request_id', request_id)}",
            body=_CANCELED)

    # --- company enrichment -----------------------------------------------------

    def find_company(self, domain: Optional[str] = None,
                     linkedin_url: Optional[str] = None) -> Any:
        """GET /companies/find — enrich ONE company by domain or LinkedIn URL."""
        if not (domain or linkedin_url):
            raise ValueError("find_company needs domain or linkedin_url")
        return self._request("GET", "/companies/find", params={
            "domain": domain, "linkedin_url": linkedin_url})

    def start_company_enrichment(self, companies: List[Dict[str, Any]]) -> Any:
        """POST /companies/enrichment-requests — start a batch (<= 10,000
        companies, each `{domain | linkedin_url}`). Answers 202 with its `id`."""
        if not companies:
            raise ValueError("companies must be a non-empty list")
        return self._request("POST", "/companies/enrichment-requests",
                             body={"companies": companies})

    def get_company_enrichment(self, request_id: str) -> Any:
        """GET /companies/enrichment-requests/{id} — the batch's status and results."""
        return self._request(
            "GET", f"/companies/enrichment-requests/{path_id('request_id', request_id)}")

    def cancel_company_enrichment(self, request_id: str) -> Any:
        """PATCH /companies/enrichment-requests/{id} — stop a running batch."""
        return self._request(
            "PATCH", f"/companies/enrichment-requests/{path_id('request_id', request_id)}",
            body=_CANCELED)

    # --- email validation -------------------------------------------------------

    def start_email_validation(self, emails: List[Any]) -> Any:
        """POST /email-validations — start a batch (<= 100,000 emails, given as
        strings or `{email}`); consumes credits. Answers 202 with its `id`."""
        return self._request("POST", "/email-validations",
                             body={"emails": items("emails", emails, "email")})

    def get_email_validation(self, validation_id: str,
                             page_size: Optional[int] = None,
                             page_after: Optional[str] = None,
                             page_before: Optional[str] = None) -> Any:
        """GET /email-validations/{id} — the batch's status and results, one
        page at a time (cursor on the email)."""
        return self._request(
            "GET", f"/email-validations/{path_id('validation_id', validation_id)}",
            params=page_params(page_size, page_after, page_before))

    def cancel_email_validation(self, validation_id: str) -> Any:
        """PATCH /email-validations/{id} — stop a running batch."""
        return self._request(
            "PATCH", f"/email-validations/{path_id('validation_id', validation_id)}",
            body=_CANCELED)

    # --- job openings -----------------------------------------------------------

    def list_job_openings(self, company_id: Optional[str] = None,
                          domain: Optional[str] = None,
                          linkedin_url: Optional[str] = None,
                          person_seniorities: Optional[List[str]] = None,
                          person_departments: Optional[List[str]] = None,
                          person_job_functions: Optional[List[str]] = None,
                          only_remote: Optional[bool] = None,
                          page_size: Optional[int] = None,
                          page_after: Optional[str] = None) -> Any:
        """GET /job-openings — a company's open positions (page_size <= 100),
        the company given by Amplemarket id, domain or LinkedIn URL."""
        if not (company_id or domain or linkedin_url):
            raise ValueError("list_job_openings needs company_id, domain or linkedin_url")
        return self._request("GET", "/job-openings", params={
            "company_id": company_id, "domain": domain, "linkedin_url": linkedin_url,
            "person_seniorities[]": person_seniorities,
            "person_departments[]": person_departments,
            "person_job_functions[]": person_job_functions,
            "only_remote": only_remote, **page_params(page_size, page_after)})

    def get_job_opening(self, job_opening_id: str) -> Any:
        """GET /job-openings/{id} — one job opening (410 once it has closed)."""
        return self._request(
            "GET", f"/job-openings/{path_id('job_opening_id', job_opening_id)}")
