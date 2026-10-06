"""
Cognism Search API Client — B2B contact & account search, reveal, and
identity-based enrichment.

Synchronous REST API (developers.cognism.com). Auth = API key as Bearer token
(`Authorization: Bearer <key>`). Base: https://app.cognism.com/api/search

Endpoints covered:
- POST /contact/search   — contact search (preview, no real email/phone)
- POST /account/search   — company search (preview)
- POST /contact/redeem   — full reveal by id/redeemId (consumes credits)
- POST /account/redeem   — company reveal by id/redeemId
- POST /contact/enrich   — find ONE contact from identity criteria
- POST /account/enrich   — find ONE company from identity criteria
- GET  /entitlement/contactEntitlementSubscription — fields visible to the key (contact)
- GET  /entitlement/accountEntitlementSubscription — fields visible to the key (account)
- GET  /filter/{kind}    — allowed values for dynamic-list fields
  (regions, countries, states, industries, sic, isic, naics, skills,
  technologies, companySizes, companyTypes, jobFunctions, managementLevels,
  seniority)

Pagination: cursor (`lastReturnedKey`), NOT an offset — Cognism does not allow
skipping a page (you must paginate sequentially from the start).

The filter DSL (`filters`) is deliberately an opaque dict passed as is
(same shape as the JSON Cognism expects) rather than modeled field by
field: ~150 fields, many nesting levels (`account.*`,
`previousAccounts.*`, `account.hiringEvent.*`, `account.fundingEvent.*`,
`locationMoveEvent.*`, `jobJoinEvent.*`, `jobLeaveEvent.*`,
`searchOptions.*`) — the full docs live in the `cognism-filters` guide
on the oto-backend side, not here. The CLOSED-value fields (seniority,
jobFunctions, managementLevel, account.types, funding type/series, hiring
department, sort_fields, the accountSearchOptions enums) are validated
client-side (`enums.validate_enum_filters`): an out-of-list value raises an
explicit `ValueError` rather than letting through a request that answers 200
with an empty page (the most likely silent failure mode here).

Requires: requests
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require
from .enums import validate_enum_filters


class CognismClient:
    """Client for Cognism's Search API (Contacts & Accounts)."""

    BASE_URL = "https://app.cognism.com/api/search"
    TIMEOUT = 30

    _FILTER_ENDPOINTS = {
        "technologies": "technologiesSearch",
        "managementLevels": "managementLevels",
        "companySizes": "companySizes",
        "industries": "industries",
        "jobFunctions": "jobFunctions",
        "regions": "regions",
        "countries": "countries",
        "states": "states",
        "sic": "sic",
        "isic": "isic",
        "naics": "naics",
        "skills": "skills",
        "companyTypes": "companyTypes",
        "seniority": "seniority",
    }

    def __init__(self, api_key: str | None = None):
        """
        Args:
            api_key: Cognism key (Bearer).
        """
        self.api_key = require(api_key, "COGNISM_API_KEY")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        resp = requests.request(
            method, url, headers=self._headers(), json=json, params=params,
            timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        if not resp.content:
            return None
        return resp.json()

    # ---- search (preview — no real email/phone) -----------------------------

    def search_contacts(
        self,
        filters: Optional[Dict[str, Any]] = None,
        *,
        index_size: int = 25,
        last_returned_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Contact search (preview data). `filters` = dict in the exact
        JSON format expected by Cognism (top-level fields like firstName/
        jobTitles/seniority/…, plus nested `account`/`previousAccounts`/
        `searchOptions`/`locationMoveEvent`/`jobJoinEvent`/`jobLeaveEvent`)
        — see the `cognism-filters` guide for full detail.

        Returns the raw Cognism page: `results[]` (contacts with boolean
        `has*` flags, NO real email/phone — use
        `redeem_contacts` for the reveal), `totalResults`, `lastReturnedKey`
        (cursor for the next page — SEQUENTIAL pagination only,
        impossible to skip a page).

        Args:
            index_size: page size, default 25, max 100.
            last_returned_key: cursor of the previous page. Empty = 1st page.
        """
        validate_enum_filters(filters, scope="contact")
        return self._request(
            "POST", "contact/search",
            json=filters or {},
            params={"indexSize": index_size, "lastReturnedKey": last_returned_key or ""},
        )

    def search_accounts(
        self,
        filters: Optional[Dict[str, Any]] = None,
        *,
        index_size: int = 100,
        last_returned_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Company search (preview data). `filters` = dict in the exact
        JSON format expected by Cognism (names/domains/industries/headcount/
        technologies/… + `accountSearchOptions`) — see the
        `cognism-filters` guide.

        Returns the raw Cognism page: `results[]` (`has*` flags),
        `totalResults`, `lastReturnedKey` (cursor, sequential pagination).

        Args:
            index_size: page size, default 100, max 100.
            last_returned_key: cursor of the previous page. Empty = 1st page.
        """
        validate_enum_filters(filters, scope="account")
        return self._request(
            "POST", "account/search",
            json=filters or {},
            params={"indexSize": index_size, "lastReturnedKey": last_returned_key or ""},
        )

    # ---- reveal (consumes credits) ------------------------------------------

    def redeem_contacts(
        self,
        *,
        ids: Optional[List[str]] = None,
        redeem_ids: Optional[List[str]] = None,
        merge_phones_and_locations: bool = False,
    ) -> Dict[str, Any]:
        """Full reveal (real email/phone) of a batch of contacts by
        `id` OR `redeemId` (from a previous `search_contacts`) — mixing
        the two in a single call is not supported by Cognism. CONSUMES
        CREDITS (unlike `search_contacts`).

        Args:
            ids: contact ids. OR…
            redeem_ids: redeemIds (identify contact+position+company at a
                given moment — see the Cognism docs on `redeemId` drift).
                Exactly one of the two required.
            merge_phones_and_locations: merges the phones/locations arrays
                in the response.
        """
        if bool(ids) == bool(redeem_ids):
            raise ValueError(
                "redeem_contacts requires exactly one of `ids` or `redeem_ids`."
            )
        body = {"ids": ids} if ids else {"redeemIds": redeem_ids}
        return self._request(
            "POST", "contact/redeem",
            json=body,
            params={"mergePhonesAndLocations": str(merge_phones_and_locations).lower()},
        )

    def redeem_accounts(
        self,
        *,
        ids: Optional[List[str]] = None,
        redeem_ids: Optional[List[str]] = None,
        merge_phones_and_locations: bool = False,
    ) -> Dict[str, Any]:
        """Full reveal of a batch of companies by `id` OR `redeemId`.
        CONSUMES CREDITS. See `redeem_contacts` for the semantics.
        """
        if bool(ids) == bool(redeem_ids):
            raise ValueError(
                "redeem_accounts requires exactly one of `ids` or `redeem_ids`."
            )
        body = {"ids": ids} if ids else {"redeemIds": redeem_ids}
        return self._request(
            "POST", "account/redeem",
            json=body,
            params={"mergePhonesAndLocations": str(merge_phones_and_locations).lower()},
        )

    # ---- enrichment (identity -> best match, scored) ------------------------

    def enrich_contact(
        self,
        *,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        email: Optional[str] = None,
        sha256: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        phone_number: Optional[str] = None,
        job_title: Optional[str] = None,
        account_name: Optional[str] = None,
        account_website: Optional[str] = None,
        anchor_fields: Optional[List[str]] = None,
        min_match_score: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Find ONE contact from identity criteria (best match,
        scored). Best precision with a unique identifier (`email`/
        `sha256`/`linkedin_url`), OR the combination `first_name`+`last_name`+
        `job_title` with `account_name`/`account_website`. Provide as many
        fields as possible — Cognism returns the best match found.

        `min_match_score`: minimum score to return a result (Cognism
        default = 30; <27 = low-quality match).

        Raises `ValueError` if NO identity field is provided (an empty call
        makes no sense on the API side).
        """
        body: Dict[str, Any] = {}
        if first_name: body["firstName"] = first_name
        if last_name: body["lastName"] = last_name
        if email: body["email"] = email
        if sha256: body["sha256"] = sha256
        if linkedin_url: body["linkedinUrl"] = linkedin_url
        if phone_number: body["phoneNumber"] = phone_number
        if job_title: body["jobTitle"] = job_title
        if account_name: body["accountName"] = account_name
        if account_website: body["accountWebsite"] = account_website
        if anchor_fields: body["anchorFields"] = anchor_fields
        if min_match_score is not None: body["minMatchScore"] = min_match_score
        if not body:
            raise ValueError("enrich_contact requires at least one identity field.")
        return self._request("POST", "contact/enrich", json=body)

    def enrich_account(
        self,
        *,
        name: Optional[str] = None,
        website: Optional[str] = None,
        domain: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        country: Optional[str] = None,
        city: Optional[str] = None,
        anchor_fields: Optional[List[str]] = None,
        min_match_score: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Find ONE company from identity criteria (best match,
        scored). Best precision with a unique identifier (`website`/
        `domain`/`linkedin_url`), OR `name` combined with `country`/`city` (HQ or
        office). Cognism default `minMatchScore` = 40 (<35 = low-quality
        match — different threshold from `enrich_contact`, where the default is 30).
        Raises `ValueError` if no field is provided.
        """
        body: Dict[str, Any] = {}
        if name: body["name"] = name
        if website: body["website"] = website
        if domain: body["domain"] = domain
        if linkedin_url: body["linkedinUrl"] = linkedin_url
        if country: body["country"] = country
        if city: body["city"] = city
        if anchor_fields: body["anchorFields"] = anchor_fields
        if min_match_score is not None: body["minMatchScore"] = min_match_score
        if not body:
            raise ValueError("enrich_account requires at least one identity field.")
        return self._request("POST", "account/enrich", json=body)

    # ---- entitlement (which fields this key can see) ------------------------

    def contact_entitlement(self) -> Dict[str, Any]:
        """Detail of the configured key's Contact entitlement (which fields
        are visible — email/phones/etc.)."""
        return self._request("GET", "entitlement/contactEntitlementSubscription")

    def account_entitlement(self) -> Dict[str, Any]:
        """Detail of the configured key's Account entitlement."""
        return self._request("GET", "entitlement/accountEntitlementSubscription")

    def verify_key(self) -> Dict[str, Any]:
        """Validate the key via an entitlement call. Raises the upstream HTTPError
        (401 = invalid key) on failure."""
        self.contact_entitlement()
        return {"valid": True}

    # ---- dynamic-list filters (regions/countries/technologies/…) -----------

    def filter_values(
        self,
        kind: str,
        *,
        search: Optional[str] = None,
        index_size: int = 20,
        last_returned_key: Optional[str] = None,
    ) -> Any:
        """Allowed values for a DYNAMIC-list filter field
        (NOT the closed-list fields already validated client-side — see
        `enums.py` — those do not need a network call).

        Args:
            kind: one of `technologies`, `managementLevels`, `companySizes`,
                `industries`, `jobFunctions`, `regions`, `countries`,
                `states`, `sic`, `isic`, `naics`, `skills`, `companyTypes`,
                `seniority`.
            search, index_size, last_returned_key: only for
                `kind="technologies"` (the only paginated/searchable list on
                Cognism's side — the others return the full list in one call).
        """
        if kind not in self._FILTER_ENDPOINTS:
            raise ValueError(
                f"Unknown filter kind {kind!r}. Allowed: "
                f"{sorted(self._FILTER_ENDPOINTS)!r}"
            )
        endpoint = self._FILTER_ENDPOINTS[kind]
        params = None
        if kind == "technologies":
            params = {
                "search": search or "",
                "indexSize": index_size,
                "lastReturnedKey": last_returned_key or "",
            }
        return self._request("GET", f"filter/{endpoint}", params=params)
