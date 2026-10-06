"""
Apollo.io API Client for lead enrichment and search.

Requires: requests
"""

import time
from typing import Optional, Dict, Any, List

import requests

from ..common.credentials import require

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never wait indefinitely


class ApolloError(RuntimeError):
    """Apollo API error, **upstream message surfaced as is**.

    A bare `raise_for_status()` only gives "422 Client Error … <url>": the caller
    (an agent) cannot tell WHICH field is rejected, so it cannot fix its
    call. Apollo does say precisely what is wrong in the response body
    (`error`/`errors`/`error_message`) — we propagate it.
    """

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class ApolloClient:
    """
    Apollo.io API client for:
    - Organization search and enrichment
    - People search and matching
    - Job postings lookup
    """

    # Canonical documented path (`/api/v1`) — `/v1` is a legacy alias that
    # answers on enrich/match but NOT on the search endpoints.
    BASE_URL = "https://api.apollo.io/api/v1"

    def __init__(self, api_key: str = None):
        """
        Initialize Apollo client.

        Args:
            api_key: Apollo API key
        """
        self.api_key = require(api_key, "APOLLO_API_KEY")
        self._last_request = 0.0

    def _rate_limit(self):
        """Enforce minimum 1 second between requests."""
        elapsed = time.time() - self._last_request
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_request = time.time()

    @staticmethod
    def _upstream_message(response: requests.Response) -> str:
        """Apollo's error message, otherwise an excerpt of the raw body."""
        try:
            body = response.json()
        except ValueError:
            return (response.text or "").strip()[:400]
        if isinstance(body, dict):
            for k in ("error_message", "error", "message", "errors"):
                v = body.get(k)
                if v:
                    return v if isinstance(v, str) else str(v)
        return str(body)[:400]

    #: Beyond this, we do not sleep: we hand control back, NAMING the delay. An MCP tool
    #: that waits longer than that has already lost its client (~60 s of patience on
    #: the caller side), and sleeping silently would turn a throttle into a mute timeout.
    _RETRY_AFTER_MAX = 15
    #: At most two retries — the total wait ceiling stays under 30 s.
    _RETRY_MAX = 2

    def _send(self, method: str, endpoint: str, **kwargs):
        """The shared send: rate limit, then a SHORT 429 is retried.

        Apollo throttles per window (the plan decides the rate) and answers 429 with
        `Retry-After`. Without a retry, list building loses the call in
        flight — and in a batch, ten people fall at once. So we retry,
        but BOUNDED: at most twice, and only if upstream asks for a short
        wait. A long `Retry-After` is not absorbed — it is SURFACED,
        with the delay in the message, because a wait we cannot hold
        must go back to the caller rather than be slept away quietly
        (`docs/…` of oto-backend: everything that waits on a third party has a maximum delay at
        its own level).
        """
        url = f"{self.BASE_URL}/{endpoint}"
        headers = {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

        for essai in range(self._RETRY_MAX + 1):
            self._rate_limit()
            response = requests.request(method, url, headers=headers,
                                        timeout=_HTTP_TIMEOUT, **kwargs)
            if response.status_code != 429 or essai == self._RETRY_MAX:
                return response
            try:
                attendre = int(response.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                attendre = 2
            if attendre > self._RETRY_AFTER_MAX:
                raise ApolloError(
                    f"Apollo 429 on {endpoint}: rate limit quota reached, upstream "
                    f"asks for a {attendre} s wait — too long to be absorbed "
                    "here. Retry the call after this delay.",
                    status_code=429,
                )
            time.sleep(attendre)
        return response

    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make API request. An HTTP error raises `ApolloError` carrying the
        UPSTREAM message (which field is rejected) — not an opaque "422 Client Error"."""
        response = self._send(method, endpoint, **kwargs)
        if not response.ok:
            raise ApolloError(
                f"Apollo {response.status_code} on {endpoint}: "
                f"{self._upstream_message(response)}",
                status_code=response.status_code,
            )
        return response.json()

    def _request_tolerating(self, method: str, endpoint: str,
                            tolere: tuple, **kwargs) -> tuple:
        """Like `_request`, but returns `(status, body)` without raising for the
        statuses DECLARED in `tolere` — any other status raises `ApolloError`
        as usual.

        Exists for webhook polling, where **a 404 does not mean error**
        but "not ready yet". `_request` cannot serve this case: it raises
        on any non-2xx, and `_upstream_message` reduces the body to one sentence —
        yet the body is what must be read here (`error_code`, `retry_after_seconds`).
        The list of tolerated statuses is an ARGUMENT, never a default: a
        caller that declares none gets exactly the behaviour of
        `_request`.
        """
        response = self._send(method, endpoint, **kwargs)
        if not response.ok and response.status_code not in tolere:
            raise ApolloError(
                f"Apollo {response.status_code} on {endpoint}: "
                f"{self._upstream_message(response)}",
                status_code=response.status_code,
            )
        try:
            return response.status_code, response.json()
        except ValueError:
            # ⚠️ `requests.exceptions.JSONDecodeError` INHERITS from `ValueError`:
            # a bare `except ValueError` would therefore also catch a 200 with an
            # empty or HTML body (proxy error page, maintenance) and
            # return it as an EMPTY body — which the caller would read as "ready, but
            # nothing in it". That is exactly the silent divergence: the action
            # succeeds and the readout lies. Only a TOLERATED status may
            # come back without a body, because the status carries the meaning;
            # an unreadable success is an outage, and says so.
            if response.status_code in tolere:
                return response.status_code, {}
            raise ApolloError(
                f"Apollo {response.status_code} on {endpoint} answered a "
                f"non-JSON body: {(response.text or '').strip()[:200]!r}",
                status_code=response.status_code,
            )

    def search_organizations(
        self,
        name: str = None,
        domain: str = None,
        country: str = None,
        per_page: int = 10,
        page: int = 1,
        employee_ranges: List[str] = None,
        revenue_min: int = None,
        revenue_max: int = None,
        locations: List[str] = None,
        keywords: List[str] = None,
        technologies: List[str] = None,
        org_ids: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Search for organizations (firmographics in bulk).

        Args:
            name: Company name to search
            domain: Domain to search
            country: Country filter (shortcut for `locations`)
            per_page: Results per page (≤100)
            page: Page number
            employee_ranges: Headcount ranges, bounds INCLUSIVE in the format
                "min,max" — e.g. ["1,10", "11,50"]. THE size
                qualification filter.
            revenue_min / revenue_max: annual revenue bounds
            locations: cities/regions/countries of the HEADQUARTERS
            keywords: activity keywords (`q_organization_keyword_tags`)
            technologies: uids of technologies used (e.g. "salesforce")
            org_ids: Apollo organization ids

        Returns:
            Dict with organizations list

        ⚠️ The response does NOT carry `estimated_num_employees` (verified) — only
        revenue and headcount growth rates. For the exact headcount and its
        breakdown by department: `enrich_organization` / `bulk_enrich_organizations`.
        Hence the value of `employee_ranges`: we FILTER by size without paying an
        enrichment per company (Apollo cost: 1 credit per PAGE of 100 here,
        versus 1 credit per COMPANY in enrichment).

        ⚠️ Field names imposed by the API (`q_organization_name`,
        `q_organization_domains_list`): an unknown name is NOT rejected, it is
        **silently ignored** → the response is the entire database (~28 M
        companies, generic top Google/Amazon/…) and passes for a result.
        """
        data: Dict[str, Any] = {"per_page": per_page, "page": page}
        if name:
            data["q_organization_name"] = name
        if domain:
            data["q_organization_domains_list"] = [domain]
        locs = list(locations or []) + ([country] if country else [])
        if locs:
            data["organization_locations"] = locs
        if employee_ranges:
            data["organization_num_employees_ranges"] = employee_ranges
        if revenue_min is not None or revenue_max is not None:
            rng = {}
            if revenue_min is not None:
                rng["min"] = revenue_min
            if revenue_max is not None:
                rng["max"] = revenue_max
            data["revenue_range"] = rng
        if keywords:
            data["q_organization_keyword_tags"] = keywords
        if technologies:
            data["currently_using_any_of_technology_uids"] = technologies
        if org_ids:
            data["organization_ids"] = org_ids

        return self._request("POST", "mixed_companies/search", json=data)

    #: Ceiling imposed by the API on `organizations/bulk_enrich`.
    BULK_ENRICH_MAX = 10

    def bulk_enrich_organizations(self, domains: List[str]) -> Dict[str, Any]:
        """
        Enrich up to 10 companies in ONE call (full firmographics:
        `estimated_num_employees`, `departmental_head_count`, growth, revenue…).

        Args:
            domains: company domains (≤10 — API ceiling)

        ⚠️ The batch does NOT save credits (1 credit per organization, as in
        single mode): it saves CALLS — the rate limit of `organizations/enrich`
        is 600/h, so ÷10 on a campaign.
        """
        doms = [d.strip() for d in (domains or []) if d and d.strip()]
        if not doms:
            raise ValueError("domains required (at least one domain)")
        if len(doms) > self.BULK_ENRICH_MAX:
            raise ValueError(
                f"{len(doms)} domains: the API accepts {self.BULK_ENRICH_MAX} "
                "at most per call — split into batches")
        return self._request("POST", "organizations/bulk_enrich",
                             params={"domains[]": doms})

    def enrich_organization(self, domain: str) -> Dict[str, Any]:
        """
        Enrich organization by domain.

        Args:
            domain: Company domain

        Returns:
            Detailed company data
        """
        return self._request("GET", "organizations/enrich", params={"domain": domain})

    def search_people(
        self,
        domains: List[str] = None,
        org_ids: List[str] = None,
        titles: List[str] = None,
        seniorities: List[str] = None,
        person_locations: List[str] = None,
        organization_locations: List[str] = None,
        per_page: int = 25,
        page: int = 1,
    ) -> Dict[str, Any]:
        """
        Search for people (net-new prospecting).

        Args:
            domains: Company domains to search
            org_ids: Apollo organization IDs
            titles: Title keywords
            seniorities: Seniority levels (e.g., ["c_suite", "director"])
            person_locations: Where the PERSON is, e.g. ["France", "Paris, France"]
            organization_locations: Where their EMPLOYER's site is
            per_page: Results per page
            page: Page number

        Returns:
            People search results (no email/phone — that's `match_person`)

        ⚠️ The endpoint is `mixed_people/api_search` and the domain filter
        is called `q_organization_domains_list`: `people/search` +
        `organization_domains` systematically returned a 422. There is NO
        "department" filter on this API — target by `titles`/`seniorities`.

        LOCATION is what makes the domain usable on a global
        group: `franke.com` returns 1887 profiles, `verifone.com` 3282, all countries
        combined, and nothing else can isolate the French subsidiary —
        each blind reveal costing a credit.
        """
        data = {"per_page": per_page, "page": page}
        if domains:
            data["q_organization_domains_list"] = domains
        if org_ids:
            data["organization_ids"] = org_ids
        if titles:
            data["person_titles"] = titles
        if seniorities:
            data["person_seniorities"] = seniorities
        if person_locations:
            data["person_locations"] = person_locations
        if organization_locations:
            data["organization_locations"] = organization_locations

        return self._request("POST", "mixed_people/api_search", json=data)

    @staticmethod
    def _looks_like_stub(person: Optional[Dict[str, Any]]) -> bool:
        """Is the returned record a STUB created for lack of a match?

        On too weak an identifier, Apollo does not return "nothing": it CREATES a
        new, empty person (`last_name`/`title`/`email`/`linkedin_url` null) and
        marks it `revealed_for_current_team` — the credit is consumed, the data does not
        exist. Without this test, the caller believes it has enriched.
        """
        if not isinstance(person, dict):
            return False
        return not any(person.get(k) for k in
                       ("last_name", "title", "email", "linkedin_url", "organization_id"))

    def match_person(
        self,
        person_id: str = None,
        linkedin_url: str = None,
        email: str = None,
        first_name: str = None,
        last_name: str = None,
        name: str = None,
        domain: str = None,
        org_name: str = None,
        reveal_personal_emails: bool = None,
        reveal_phone_number: bool = None,
        webhook_url: str = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Match a specific person (enrichment — 1 Apollo credit per call).

        Args:
            person_id: **Apollo id** of the person (the one `search_people` returns)
                — the safest identifier, to prefer whenever coming from a search
            linkedin_url: LinkedIn profile URL
            email: Email address
            first_name: First name
            last_name: Last name
            name: Full name
            domain: Company domain
            org_name: Organization name
            reveal_personal_emails: requests the PERSONAL emails. Synchronous —
                they come back in the response. Credit cost depends on the Apollo plan.
            reveal_phone_number: requests the phones, MOBILE AND DIRECT DIAL
                included. ⚠️ Those numbers do NOT come back in the response:
                Apollo verifies them on its side and POSTs them, several minutes
                later, to `webhook_url` — the response only carries
                `request_id`, to pass to `poll_webhook_result` to read them back
                without a webhook (re-read announced as 0 credit by Apollo — the call that
                issued the `request_id` has already been billed). Requires `webhook_url`.
            webhook_url: where Apollo POSTs the phones. REQUIRED whenever
                `reveal_phone_number`, and forbidden otherwise (Apollo:
                "Otherwise, do not use this parameter").

        Returns:
            Matched person data, or None (404). The record carries `_stub: True` when
            Apollo fabricated an empty shell instead of matching (see `_looks_like_stub`).

            ⚠️ **Neither `None` nor `_stub` means "free".** The credit is taken at
            the CALL, not at the result: Apollo bills the empty shell it fabricates
            itself — measured, ~12 credits for zero data (commit `44acc08`) — and nothing
            has ever shown that a non-match is refunded. A caller that returns a
            failure message must therefore assert NOTHING about cost: neither "no credit
            spent", nor "retry", which would present a second paid call as
            free. Either we measure the billing, or we stay silent on it.

        ⚠️ **A weak identifier costs a credit for nothing.** `search_people` returns
        OBFUSCATED family names ("Vi***l"): matching with `first_name` + company
        alone does not find the person, Apollo creates a stub and bills anyway
        (~12 credits lost in one session, feedbacks #347-350). Hence the guard
        below: without a strong identifier (`person_id`/`email`/`linkedin_url`), a
        FULL name is required — the call is refused BEFORE burning the credit.
        """
        strong = person_id or email or linkedin_url
        full_name = bool(last_name) or bool(name and len(name.split()) >= 2)
        if not strong and not full_name:
            raise ValueError(
                "identifier too weak for an Apollo match: pass `person_id` "
                "(the id returned by search_people), `email` or `linkedin_url` — otherwise a "
                "FULL name (first name + last name). A first name + a company do not match: "
                "Apollo creates an empty record and still consumes the credit.")

        # The two phone reveal parameters go AS A PAIR, and Apollo says so in
        # both directions: "If this parameter is set to `true`, you must
        # enter a webhook URL" / "Otherwise, do not use this parameter". Without a
        # webhook it answers "Please add a valid 'webhook_url' parameter" — a
        # round trip for nothing; with a webhook but without the flag, it refuses
        # NOTHING and will never send anything, which is worse: the caller
        # waits for a POST that will never leave. We refuse both halves here.
        if reveal_phone_number and not webhook_url:
            raise ValueError(
                "`reveal_phone_number` requires `webhook_url`: Apollo does not return "
                "mobiles in the response, it POSTs them to this URL a few "
                "minutes later. Without it the call is refused by Apollo.")
        if webhook_url and not reveal_phone_number:
            raise ValueError(
                "`webhook_url` only serves the phone reveal: also pass "
                "`reveal_phone_number=True`, otherwise Apollo will never send anything "
                "to this URL.")

        data = {}
        if person_id:
            data["id"] = person_id
        if linkedin_url:
            data["linkedin_url"] = linkedin_url
        if email:
            data["email"] = email
        if first_name:
            data["first_name"] = first_name
        if last_name:
            data["last_name"] = last_name
        if name:
            data["name"] = name
        if domain:
            # `domain` is the name the API expects — `organization_domain` (used
            # until 2026-08-04) is an UNKNOWN field, hence silently ignored: the
            # domain did not take part in the match, which made stubs more likely.
            data["domain"] = domain
        if org_name:
            data["organization_name"] = org_name

        # ⚠️ The three CONTROL parameters go in the QUERY STRING, not in the
        # body: the contract published by Apollo for `people/match` declares NO
        # `requestBody`, its 14 parameters are all `in: query`, and its
        # own example puts them there. Identity, for its part, stays in the body — that is
        # what this client has always done and Apollo reads it fine from there
        # (`domain` takes part in the match, verified on 2026-08-04). And the booleans
        # are serialized BY HAND: `requests` would write `reveal_phone_number=True`
        # (Python capital), where the API expects `true` — an API that silently
        # ignores what it does not recognize would return no error, just a
        # reveal that does not happen and a caller waiting for a POST for nothing.
        params = {}
        for nom, valeur in (("reveal_personal_emails", reveal_personal_emails),
                            ("reveal_phone_number", reveal_phone_number)):
            if valeur is not None:
                params[nom] = "true" if valeur else "false"
        if webhook_url:
            params["webhook_url"] = webhook_url

        try:
            out = self._request("POST", "people/match", json=data,
                                params=params or None)
        except ApolloError as e:
            if e.status_code == 404:
                return None
            raise
        person = (out or {}).get("person") if isinstance(out, dict) else None
        if self._looks_like_stub(person):
            person["_stub"] = True
        return out

    def poll_webhook_result(self, request_id) -> Dict[str, Any]:
        """Read back the result Apollo sent (or will send) to a webhook.

        This is what makes the phone reveal usable WITHOUT hosting a
        receiver: `match_person(reveal_phone_number=True, …)` returns a
        `request_id`, and this endpoint returns the same content as the POST — for
        **thirty days**, for **0 credit** (duration and free of charge: Apollo doc, not
        replayed for real here; what is already paid for is the call that issued the
        `request_id`).

        Args:
            request_id: the one returned by `match_person`. Signed 64-bit integer:
                it can be NEGATIVE and it exceeds the precision of a
                JavaScript number — we carry it as a STRING, as is, never
                reconverted.

        Returns:
            `{"done": False, "retry_after_seconds": int}` while Apollo
            is working, `{"done": True, "result": {…}}` when it is ready.

        ⚠️ **A 404 here does not mean "error"**: as long as the result is not
        ready, Apollo answers 404 with `error_code: "result_pending"` and the
        delay to wait. Only the three other cases are terminal and raise —
        `request_id_unknown` (never issued), `request_id_expired` (beyond the 30
        days) and `invalid_request_id`.
        """
        rid = str(request_id).strip()
        if not rid or not rid.lstrip("-").isdigit():
            # Apollo would answer 400 `invalid_request_id`: better to say it here,
            # where we can name where the expected value comes from.
            raise ValueError(
                "`request_id` must be the integer returned by match_person "
                f"(signed 64-bit integer, possibly negative) — received: {rid!r}")

        status, body = self._request_tolerating(
            "GET", f"webhook_result/{rid}", tolere=(404,))
        body = body if isinstance(body, dict) else {}
        if status == 404:
            if body.get("error_code") == "result_pending":
                return {"done": False,
                        "retry_after_seconds": body.get("retry_after_seconds")}
            raise ApolloError(
                f"Apollo 404 on webhook_result/{rid}: "
                f"{body.get('error_code') or body}", status_code=404)
        return {"done": True, "result": body}

    #: Ceiling imposed by the API on `people/bulk_match`.
    BULK_MATCH_MAX = 10

    def bulk_match_people(
        self,
        details: List[Dict[str, Any]],
        reveal_personal_emails: bool = None,
        reveal_phone_number: bool = None,
        webhook_url: str = None,
    ) -> Dict[str, Any]:
        """
        Enrich up to 10 people in ONE call (`people/bulk_match`).

        This is the form list building ACTUALLY uses: a search
        returns hundreds of people with obfuscated names, and they must be revealed.
        One by one, that is as many round trips — with a one-second `_rate_limit`,
        revealing 300 people takes five minutes of pure waiting, and each
        response carries the entire company record.

        Args:
            details: ≤10 people. Each entry carries the same identifiers as
                `match_person`: `id` (the safest, returned by `search_people`),
                `email`, `linkedin_url`, or a FULL name (`first_name` +
                `last_name`) with `domain`/`organization_name`.
            reveal_personal_emails: personal emails, in the response (synchronous).
            reveal_phone_number: phones — ASYNCHRONOUS, delivered to `webhook_url`.
                Requires `webhook_url`, as on `match_person`.
            webhook_url: destination of the phones. Forbidden without
                `reveal_phone_number`.

        Returns:
            The Apollo response, including `matches`: one entry PER requested person,
            in order, `None` where nothing matched.

        ⚠️ **The credit is paid per person, not per call**: a batch of 10 costs
        10 times a single call. What the batch saves are CALLS (and thus the
        rate limit), never credits.

        ⚠️ **The weak identifier guard applies to EACH entry**, and for the
        same reason as in single mode: on too weak an identifier Apollo does not return
        "nothing", it CREATES an empty record and bills it (oto feedbacks #347-350).
        In a batch the trap is worse — a weak entry lost among ten goes
        unnoticed. The faulty entry is named by its INDEX before anything leaves.
        """
        entrees = list(details or [])
        if not entrees:
            raise ValueError("details required (at least one person)")
        if len(entrees) > self.BULK_MATCH_MAX:
            raise ValueError(
                f"{len(entrees)} people: the API accepts {self.BULK_MATCH_MAX} "
                "at most per call — split into batches")

        for i, e in enumerate(entrees):
            if not isinstance(e, dict):
                raise ValueError(f"details[{i}] must be an object, not {type(e).__name__}")
            fort = e.get("id") or e.get("email") or e.get("linkedin_url")
            nom_complet = bool(e.get("last_name")) or bool(
                e.get("name") and len(str(e["name"]).split()) >= 2)
            if not fort and not nom_complet:
                raise ValueError(
                    f"details[{i}]: identifier too weak for an Apollo match — "
                    "pass `id` (the one returned by search_people), `email` or "
                    "`linkedin_url`, otherwise a FULL name (first name + last name). A first name "
                    "+ a company do not match: Apollo creates an empty record and "
                    "still consumes the credit.")

        # Same pairing as `match_person`, and for the same reasons in both
        # directions (see its guard): without a webhook Apollo refuses, and with a webhook without
        # the flag it accepts then never sends anything.
        if reveal_phone_number and not webhook_url:
            raise ValueError(
                "`reveal_phone_number` requires `webhook_url`: Apollo does not return "
                "mobiles in the response, it POSTs them to this URL a few minutes "
                "later. Without it the call is refused by Apollo.")
        if webhook_url and not reveal_phone_number:
            raise ValueError(
                "`webhook_url` only serves the phone reveal: also pass "
                "`reveal_phone_number=True`, otherwise Apollo will never send anything to "
                "this URL.")

        # Same split as `match_person`: identity in the BODY (here
        # `details`, which Apollo's contract does declare as `requestBody` for this
        # endpoint), CONTROL parameters in the query string, booleans
        # serialized by hand — `requests` would write `True`, the API expects `true`.
        params = {}
        for nom, valeur in (("reveal_personal_emails", reveal_personal_emails),
                            ("reveal_phone_number", reveal_phone_number)):
            if valeur is not None:
                params[nom] = "true" if valeur else "false"
        if webhook_url:
            params["webhook_url"] = webhook_url

        out = self._request("POST", "people/bulk_match",
                            json={"details": entrees}, params=params or None)

        # Same marking as in single mode: a billed empty shell must be SEEN,
        # otherwise the caller counts it as a successful enrichment.
        matches = (out or {}).get("matches") if isinstance(out, dict) else None
        if isinstance(matches, list):
            for m in matches:
                if self._looks_like_stub(m):
                    m["_stub"] = True
        return out

    def get_job_postings(self, org_id: str) -> Dict[str, Any]:
        """
        Get job postings for an organization.

        Args:
            org_id: Apollo organization ID

        Returns:
            Job postings list
        """
        return self._request("GET", f"organizations/{org_id}/job_postings")

    # ------------------------------------------------------------------
    # Email accounts & schedules (read prerequisites for sequences/emails:
    # without an `id` from here, `create_sequence`/`add_contacts_to_sequence` have
    # nothing to pass as `emailer_schedule_id`/`send_email_from_email_account_id`)
    # ------------------------------------------------------------------

    def list_email_accounts(self) -> Dict[str, Any]:
        """
        List the mailboxes connected to this Apollo account (0 credit).

        Returns:
            Dict with `email_accounts` — this is where to find the `id` to pass
            as `send_email_from_email_account_id` to `add_contacts_to_sequence`.
        """
        return self._request("GET", "email_accounts")

    def list_email_schedules(self) -> Dict[str, Any]:
        """
        List the send schedules configured on this team (0 credit).

        Returns:
            Dict with `emailer_schedules` — this is where to find the `id` to
            pass as `emailer_schedule_id` to `create_sequence` (required, without
            it the creation fails).
        """
        return self._request("GET", "emailer_schedules")

    # ------------------------------------------------------------------
    # Sequences
    #
    # ⚠️ Two path families, NOT an inconsistency: create/update use
    # `/sequences[...]` (newer REST), everything else — search, contacts,
    # activate/deactivate/archive — stays on the legacy `/emailer_campaigns` object.
    # Verified endpoint by endpoint in the Apollo doc (2026-08-20); not yet
    # replayed for real (no key in this environment) — to be confirmed on the first
    # real run rather than assuming a symmetry that does not exist.
    # ------------------------------------------------------------------

    def search_sequences(
        self,
        name: str = None,
        per_page: int = 25,
        page: int = 1,
    ) -> Dict[str, Any]:
        """
        Search sequences by name (0 credit).

        Args:
            name: keywords, must match a PART of the name (`q_name` on the API side)
            per_page: results per page
            page: page number

        Returns:
            Dict with `emailer_campaigns` (list) and `pagination`
        """
        data: Dict[str, Any] = {"per_page": per_page, "page": page}
        if name:
            data["q_name"] = name
        return self._request("POST", "emailer_campaigns/search", json=data)

    def create_sequence(
        self,
        name: str,
        emailer_schedule_id: str,
        active: bool = False,
        label_names: List[str] = None,
        folder_id: str = None,
        max_emails_per_day: int = None,
        emailer_steps: List[Dict[str, Any]] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        """
        Create a sequence (0 credit — the cost is in sending, not in creating).

        Args:
            name: name of the sequence
            emailer_schedule_id: id of a send schedule — REQUIRED by the API,
                obtained via `list_email_schedules` (no implicit default documented)
            active: activate immediately (default False — prefer `activate_sequence`
                once the steps/templates have been reviewed)
            label_names: labels to apply (created if absent)
            folder_id: Apollo folder id
            max_emails_per_day: daily send ceiling
            emailer_steps: definition of the steps (see Apollo doc — nested
                structure, not validated locally)
            **extra: other documented fields (`sequence_by_exact_daytime`,
                `mark_finished_if_reply`, `mark_paused_if_ooo`, etc.) passed as is

        Returns:
            Dict with `emailer_campaign`, `emailer_steps`, `emailer_touches`, `emailer_templates`
        """
        if not (name or "").strip():
            raise ValueError("name required to create a sequence")
        if not (emailer_schedule_id or "").strip():
            raise ValueError(
                "emailer_schedule_id required — get it via list_email_schedules(), "
                "the API refuses creation without a send schedule")
        data: Dict[str, Any] = {
            "name": name,
            "emailer_schedule_id": emailer_schedule_id,
            "active": active,
        }
        if label_names:
            data["label_names"] = label_names
        if folder_id:
            data["folder_id"] = folder_id
        if max_emails_per_day is not None:
            data["max_emails_per_day"] = max_emails_per_day
        if emailer_steps:
            data["emailer_steps"] = emailer_steps
        data.update(extra)
        return self._request("POST", "sequences", json=data)

    def update_sequence(self, sequence_id: str, **fields: Any) -> Dict[str, Any]:
        """
        Update a sequence (0 credit). All fields are optional on the API side —
        only those passed here are sent.

        Args:
            sequence_id: Apollo id of the sequence
            **fields: `name`, `active`, `emailer_schedule_id`, `label_names`,
                `max_emails_per_day`, `cc_emails`, `bcc_emails`, `emailer_steps`
                (include `id` per step to MODIFY, omit it to CREATE one),
                `sharing_permission`, etc. — see Apollo doc

        Returns:
            Updated sequence
        """
        if not (sequence_id or "").strip():
            raise ValueError("sequence_id required")
        return self._request("PUT", f"sequences/{sequence_id}", json=fields)

    def activate_sequence(self, sequence_id: str) -> Dict[str, Any]:
        """
        Activate a sequence — starts the scheduled sending (0 credit at call time,
        credits are consumed as sends/reveals go out).

        ⚠️ Fails with 422 if the sequence is already active or has no step.
        """
        if not (sequence_id or "").strip():
            raise ValueError("sequence_id required")
        return self._request("POST", f"emailer_campaigns/{sequence_id}/approve")

    def deactivate_sequence(self, sequence_id: str) -> Dict[str, Any]:
        """Deactivate a sequence (0 credit). 422 if already inactive."""
        if not (sequence_id or "").strip():
            raise ValueError("sequence_id required")
        return self._request("POST", f"emailer_campaigns/{sequence_id}/abort")

    def archive_sequence(self, sequence_id: str) -> Dict[str, Any]:
        """Archive a sequence (0 credit). Requires being its owner or
        having "full access" shared access."""
        if not (sequence_id or "").strip():
            raise ValueError("sequence_id required")
        return self._request("POST", f"emailer_campaigns/{sequence_id}/archive")

    def add_contacts_to_sequence(
        self,
        sequence_id: str,
        send_email_from_email_account_id: str,
        contact_ids: List[str] = None,
        label_names: List[str] = None,
        send_email_from_email_address: str = None,
        status: str = None,
        **flags: Any,
    ) -> Dict[str, Any]:
        """
        Enroll contacts in a sequence — the riskiest call of this client:
        it starts a MULTI-STEP automated campaign toward real people,
        not a single send. 0 credit at call time (the following sends/reveals cost).

        Args:
            sequence_id: Apollo id of the sequence
            send_email_from_email_account_id: id (or list of ids for rotation) of
                the CONNECTED mailbox that will send — REQUIRED by the API, obtained via
                `list_email_accounts`. Local lock below, same logic as
                Lightfield `_check_from`: without an explicit mailbox, no call goes out.
            contact_ids: Apollo ids of the contacts (mutually substitutable with `label_names`)
            label_names: labels identifying the contacts to add
            send_email_from_email_address: precise address within the account (if multi-alias)
            status: `"active"` or `"paused"` on adding
            **flags: `sequence_no_email`, `sequence_unverified_email`,
                `sequence_job_change`, `sequence_active_in_other_campaigns`,
                `sequence_finished_in_other_campaigns`,
                `sequence_same_company_in_same_campaign`,
                `contacts_without_ownership_permission`, `add_if_in_queue`,
                `contact_verification_skipped`, `user_id`, `auto_unpause_at` —
                all Apollo safeguards at `False`/absent by default; pass them
                explicitly as `True` to lift them

        ⚠️ Apollo doc: 403 "Master API key required" on this endpoint — to be
        confirmed with a real key, not verified from this environment.

        Returns:
            Dict with `contacts` (added), `skipped_contact_ids` (id → reason),
            `emailer_campaign`, `emailer_steps`, `emailer_touches`
        """
        if not (sequence_id or "").strip():
            raise ValueError("sequence_id required")
        if not send_email_from_email_account_id:
            raise ValueError(
                "send_email_from_email_account_id required — get it via "
                "list_email_accounts(): without an explicit CONNECTED mailbox, the API "
                "refuses to enroll the contacts (and without this lock, a call "
                "would go out on a default we do not control)")
        if not contact_ids and not label_names:
            raise ValueError("contact_ids or label_names required (at least one of the two)")
        params: Dict[str, Any] = {
            # `emailer_campaign_id` DOUBLED with the path segment — not redundant,
            # verified LIVE on 2026-08-20: without it, 422 "Please
            # specify a emailer_campaign_id and send_email_from_email_account_id"
            # EVEN with the id already in the URL.
            "emailer_campaign_id": sequence_id,
            "send_email_from_email_account_id": send_email_from_email_account_id,
        }
        if contact_ids:
            params["contact_ids[]"] = contact_ids
        if label_names:
            params["label_names[]"] = label_names
        if send_email_from_email_address:
            params["send_email_from_email_address"] = send_email_from_email_address
        if status:
            params["status"] = status
        params.update(flags)
        # Query params (Apollo doc), NOT a JSON body — a field sent as `json`
        # here would be silently ignored, same flaw as the historical `organization_domain`
        # on `match_person` (see the comment earlier in this file).
        return self._request(
            "POST", f"emailer_campaigns/{sequence_id}/add_contact_ids", params=params)

    def update_sequence_contact_status(
        self,
        emailer_campaign_ids: List[str],
        contact_ids: List[str],
        mode: str,
    ) -> Dict[str, Any]:
        """
        Mark-as-finished / remove / stop contacts in one or more
        sequences (0 credit).

        Args:
            emailer_campaign_ids: Apollo ids of the sequences concerned
            contact_ids: Apollo ids of the contacts concerned
            mode: `"mark_as_finished"` (finishes), `"remove"` (removes from the
                sequence) or `"stop"` (stops progression)

        Returns:
            Dict with `entity_progress_job` (async job — no final status here)
        """
        if not emailer_campaign_ids:
            raise ValueError("emailer_campaign_ids required")
        if not contact_ids:
            raise ValueError("contact_ids required")
        if mode not in ("mark_as_finished", "remove", "stop"):
            raise ValueError('mode must be "mark_as_finished", "remove" or "stop"')
        params = {
            "emailer_campaign_ids[]": emailer_campaign_ids,
            "contact_ids[]": contact_ids,
            "mode": mode,
        }
        return self._request(
            "POST", "emailer_campaigns/remove_or_stop_contact_ids", params=params)

    def get_contact_sequence_activity(
        self,
        contact_id: str,
        sequence_id: str = None,
        per_page: int = 50,
    ) -> Dict[str, Any]:
        """
        Sequence activity feed for one contact (0 credit).

        Args:
            contact_id: Apollo id of the contact — must belong to your team (404 otherwise)
            sequence_id: filter on one sequence (all if omitted)
            per_page: 1-50, the MOST RECENT events (not pagination)

        Returns:
            Dict with `events` (order from most recent to oldest, max `per_page`)
        """
        if not (contact_id or "").strip():
            raise ValueError("contact_id required")
        data: Dict[str, Any] = {"contact_id": contact_id, "per_page": per_page}
        if sequence_id:
            data["sequence_id"] = sequence_id
        return self._request("POST", "emailer_campaigns/activity_feed", json=data)

    # ------------------------------------------------------------------
    # One-off emails
    #
    # ⚠️ Draft and send remain two distinct calls (create_email_draft
    # THEN send_email_now), never merged — same choice as Lightfield
    # (`draft_email`/`send_email`) and for the same reason: so that nothing
    # confuses "prepare" and "send".
    # ------------------------------------------------------------------

    def create_email_draft(
        self,
        contact_id: str = None,
        subject: str = None,
        body_html: str = None,
        recipients: List[Dict[str, str]] = None,
        in_response_to_emailer_message_id: str = None,
        emailer_template_id: str = None,
        attachment_ids: List[str] = None,
        enable_tracking: bool = None,
        outreach_task_id: str = None,
    ) -> Dict[str, Any]:
        """
        Create an email draft (does NOT go out — use send_email_now to send).

        Args:
            contact_id: Apollo id of the recipient — required UNLESS
                `in_response_to_emailer_message_id` is provided (reply to a thread)
            subject / body_html: content (the API sanitizes the HTML)
            recipients: `[{"email":, "contact_id":, "recipient_type_cd": "to"|"cc"|"bcc"}]`
            in_response_to_emailer_message_id: id of the parent message (reply thread)
            emailer_template_id: Apollo template to associate
            attachment_ids: ids of Apollo attachments (from an upload cycle
                outside the scope of this client — not fabricated here, see the similar limit
                documented on Lightfield `send_email`)
            enable_tracking: enable open/click tracking
            outreach_task_id: Apollo task to link to the draft

        ⚠️ No sending mailbox field (`email_account_id`/`from`) is documented
        on THIS endpoint — the mailbox is decided at `send_email_now` (implicitly, or
        via the account's default mailbox). No local lock equivalent to
        `add_contacts_to_sequence` here: nothing to check before writing the draft.

        Returns:
            Dict with `emailer_message` (status `"drafted"`), `task` if linked
        """
        if not contact_id and not in_response_to_emailer_message_id:
            raise ValueError(
                "contact_id required, except when replying to a thread "
                "(in_response_to_emailer_message_id)")
        data: Dict[str, Any] = {}
        if contact_id:
            data["contact_id"] = contact_id
        if subject:
            data["subject"] = subject
        if body_html:
            data["body_html"] = body_html
        if recipients:
            data["recipients"] = recipients
        if in_response_to_emailer_message_id:
            data["in_response_to_emailer_message_id"] = in_response_to_emailer_message_id
        if emailer_template_id:
            data["emailer_template_id"] = emailer_template_id
        if attachment_ids:
            data["attachment_ids"] = attachment_ids
        if enable_tracking is not None:
            data["enable_tracking"] = enable_tracking
        if outreach_task_id:
            data["outreach_task_id"] = outreach_task_id
        return self._request("POST", "emailer_messages", json=data)

    def send_email_now(self, message_id: str, surface: str = None) -> Dict[str, Any]:
        """
        Send an existing draft NOW — the only action of this client that reaches a
        real person by direct email (outside a sequence). Irreversible.

        Args:
            message_id: id returned by create_email_draft
            surface: internal Apollo attribution (optional, e.g. "emails")

        Returns:
            Dict with `emailer_message` (status updated), `task` if linked
        """
        if not (message_id or "").strip():
            raise ValueError("message_id required")
        data: Dict[str, Any] = {}
        if surface:
            data["surface"] = surface
        return self._request("POST", f"emailer_messages/{message_id}/send_now", json=data)

    def check_email_send_status(self, message_id: str) -> Dict[str, Any]:
        """
        Poll the send status of a message (0 credit).

        Args:
            message_id: id of the message (returned by create_email_draft/send_email_now)

        Returns:
            Dict with `status`, and depending on the state: `completed_at`, or
            `failure_reason`/`not_sent_reason`/`failed_at`, or `retry_after_seconds`
        """
        if not (message_id or "").strip():
            raise ValueError("message_id required")
        return self._request("POST", "emailer_messages/email_send_status", json={"id": message_id})

    def search_emails(
        self,
        stats: List[str] = None,
        reply_classes: List[str] = None,
        sequence_ids: List[str] = None,
        exclude_sequence_ids: List[str] = None,
        keywords: str = None,
        date_range_mode: str = None,
        date_min: str = None,
        date_max: str = None,
        per_page: int = 25,
        page: int = 1,
    ) -> Dict[str, Any]:
        """
        Search sent/outreach emails (0 credit). Upstream ceiling: 50,000 displayable
        results (100/page × 500 pages).

        Args:
            stats: statuses (`delivered`, `scheduled`, `drafted`, `not_opened`,
                `opened`, `clicked`, `unsubscribed`, `demoed`, `bounced`,
                `spam_blocked`, `failed_other`)
            reply_classes: reply sentiment (`willing_to_meet`,
                `follow_up_question`, `person_referral`, `out_of_office`,
                `already_left_company_or_not_right_person`, `not_interested`,
                `unsubscribe`, `none_of_the_above`)
            sequence_ids / exclude_sequence_ids: include/exclude by sequence
            keywords: full-text search (`q_keywords`)
            date_range_mode: `"due_at"` or `"completed_at"`
            date_min / date_max: `YYYY-MM-DD` bounds
            per_page: ≤100. page: page number

        ⚠️ `page`/`per_page` are doc-claimed, not verified: the test account
        (2026-08-20) had 0 emails sent — the request returns 200 in both
        cases, with no non-EMPTY result to compare for duplicates and confirm they are
        honored (vs. silently ignored, as `organization_domain` was
        elsewhere in this file).

        Returns:
            Dict with `emailer_messages`, `emailer_steps`
        """
        params: Dict[str, Any] = {"page": page, "per_page": per_page}
        if stats:
            params["emailer_message_stats[]"] = stats
        if reply_classes:
            params["emailer_message_reply_classes[]"] = reply_classes
        if sequence_ids:
            params["emailer_campaign_ids[]"] = sequence_ids
        if exclude_sequence_ids:
            params["not_emailer_campaign_ids[]"] = exclude_sequence_ids
        if keywords:
            params["q_keywords"] = keywords
        if date_range_mode:
            params["emailer_message_date_range_mode"] = date_range_mode
        if date_min:
            params["emailer_message_date_range[min]"] = date_min
        if date_max:
            params["emailer_message_date_range[max]"] = date_max
        return self._request("GET", "emailer_messages/search", params=params)

    def get_email_content(self, ids: List[str], body_format: str = "plain") -> Dict[str, Any]:
        """
        Fetch the body of up to 10 SENT emails (0 credit).

        Args:
            ids: Apollo ids of the sent emails — 10 max, the excess is ignored
                SILENTLY on the API side (no error)
            body_format: `"plain"` (default) or `"html"` — any other value
                silently falls back to `"plain"` on the API side

        Returns:
            Dict with `emailer_messages` (in the requested order; ids without a match
            silently omitted — only emails actually SENT are returned)
        """
        if not ids:
            raise ValueError("ids required (at least one)")
        data: Dict[str, Any] = {"ids": ids[:10]}
        if body_format:
            data["body_format"] = body_format
        return self._request("POST", "emailer_messages/get_content", json=data)

    def get_email_stats(self, message_id: str) -> Dict[str, Any]:
        """
        Open/click stats for one sent email (0 credit).

        ⚠️ Apollo doc: requires a "Master" key and is NOT available with
        OAuth — to be confirmed with a real key, not verified from this
        environment (403 otherwise).

        Args:
            message_id: id of the message — obtained via search_emails

        Returns:
            Dict with `emailer_message` (`num_opens`, `num_clicks`, ...), `activities`
        """
        if not (message_id or "").strip():
            raise ValueError("message_id required")
        return self._request("GET", f"emailer_messages/{message_id}/activities")

    # ------------------------------------------------------------------
    # Conversations (recorded calls/video meetings — conditional cost: 1 credit
    # only if the conversation has AI insights, 0 otherwise — unpredictable
    # before the call, hence not metered here; byo-only connector on the backend side)
    # ------------------------------------------------------------------

    def search_conversations(
        self,
        conversation_type: str = None,
        account_id: str = None,
        contact_ids: List[str] = None,
        tag_ids: List[str] = None,
        tracker_ids: List[str] = None,
        organization_ids: List[str] = None,
        date_range: Dict[str, str] = None,
        per_page: int = 25,
        page: int = 1,
    ) -> Dict[str, Any]:
        """
        Search recorded conversations (0 credit — the cost is on get_conversation).

        Args:
            conversation_type: `"video_conference"` or `"phone_call"`
            account_id: filter by Apollo account
            contact_ids / organization_ids / tag_ids / tracker_ids: filters
            date_range: `{"start": ISO8601, "end": ISO8601}`
            per_page: results per page. page: page number

        Returns:
            Dict with `conversations`, `pagination`
        """
        data: Dict[str, Any] = {"page": page, "num_fetch_result": per_page}
        if conversation_type:
            data["conversation_type"] = conversation_type
        if account_id:
            data["account_id"] = account_id
        if contact_ids:
            data["contact_ids"] = contact_ids
        if tag_ids:
            data["tag_ids"] = tag_ids
        if tracker_ids:
            data["tracker_ids"] = tracker_ids
        if organization_ids:
            data["organization_ids"] = organization_ids
        if date_range:
            data["date_range"] = date_range
        return self._request("POST", "conversations/search", json=data)

    def get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        """
        Get one conversation — transcript, recording, participants.

        ⚠️ 1 Apollo credit IF the conversation has AI insights, 0 otherwise —
        unpredictable before the call (not a fixed cost that can be predicted or metered
        a priori).

        Args:
            conversation_id: conversation id — accepts `id_shareid`

        Returns:
            Dict with `transcript`, `participants`, `video_recording`/`audio_recording`,
            `opportunities`
        """
        if not (conversation_id or "").strip():
            raise ValueError("conversation_id required")
        return self._request("GET", f"conversations/{conversation_id}")

    def export_conversations(self, start_time: str, end_time: str, email: str) -> Dict[str, Any]:
        """
        Kick off an async export of conversations over a time range.

        Does NOT wait for the export to finish — returns an `export_id` to pass to
        `get_conversations_export` to poll (asynchronous on the Apollo side; do not
        block on it on the caller side — an export can take far longer than
        the invocation timeout of a tool).

        Args:
            start_time / end_time: ISO 8601 bounds in GMT, `start_time` < `end_time`
            email: address of a team member to notify when the export is ready

        Returns:
            Dict with `export_url`, `export_id`
        """
        if not (start_time or "").strip() or not (end_time or "").strip():
            raise ValueError("start_time and end_time required (ISO 8601)")
        if not (email or "").strip():
            raise ValueError("email required (notification to a team member)")
        data = {"start_time": start_time, "end_time": end_time, "email": email}
        return self._request("POST", "conversations/export", json=data)

    def get_conversations_export(self, export_id: str) -> Dict[str, Any]:
        """
        Poll an export started by export_conversations.

        Args:
            export_id: id returned by export_conversations

        Returns:
            Dict with `redirect_url` (signed download URL) once ready
        """
        if not (export_id or "").strip():
            raise ValueError("export_id required")
        return self._request("GET", f"conversations/export/{export_id}")

    # ------------------------------------------------------------------
    # Contacts — the people IN the key owner's workspace,
    # NOT the shared Apollo database. This is a data boundary, not a verb
    # boundary: `people/*` queries the ~275M profiles everyone sees,
    # `contacts/*` only sees what THIS team has saved.
    #
    # All three endpoints cost **0 credit** (Apollo doc, verified on
    # 2026-08-22) — that is the whole point of `get_contact`: re-reading a contact
    # we already own must not pay again the credit of `match_person`.
    #
    # ⚠️ All three require a **Master** key (or the named scope:
    # `api/v1/typed_custom_fields/index`, `api/v1/contacts/show`,
    # `api/v1/contacts/update`) and return 403 otherwise. Not replayed for real (no Apollo key in this
    # environment) — same caveat as the sequences above.
    # ------------------------------------------------------------------

    def list_typed_custom_fields(self) -> Dict[str, Any]:
        """
        List the custom field definitions of this Apollo team (0 credit).

        This is the endpoint that gives the **ids** that `update_contact` requires:
        `typed_custom_fields` is keyed by ID, not by name. Without this catalog we
        cannot write a custom field, only guess it.

        ⚠️ **Apollo marks this endpoint deprecated in favor of `GET /fields`
        (source=custom) — we stay HERE deliberately, and this choice must not be
        "modernized" without checking this point**: the two catalogs do not return
        the same id shape. `typed_custom_fields` returns the BARE ObjectId
        (`"<objectid>"`, 24 hex), which is exactly the key expected by
        `PATCH /contacts/{id}`; `/fields` returns an id PREFIXED with its modality
        (`"account.<objectid>"`, `"contact.id"`), which no doc
        allows us to split. Taking the "modern" catalog would therefore make us
        write keys that Apollo silently ignores, while returning 200.
        (Apollo doc verified on 2026-08-22; not replayed for real, no key here.)

        Returns:
            Dict with `typed_custom_fields`: each entry carries `id`
            (bare ObjectId), `name`, `modality` (contact/account/opportunity),
            `type` (text, number, date, datetime, boolean, picklist,
            multi_select, url, email, phone, currency) and, for a picklist,
            `picklist_values` — whose `id` must be sent, not the `name`.
        """
        return self._request("GET", "typed_custom_fields")

    # Documented sort fields. A name outside the list is REFUSED rather than sent:
    # Apollo ignores a sort it does not know and returns its default order, which the
    # caller will read as "here are the most recently modified".
    CONTACT_SORT_FIELDS = (
        "contact_last_activity_date", "contact_email_last_opened_at",
        "contact_email_last_clicked_at", "contact_created_at",
        "contact_updated_at",
    )

    # Types accepted when creating a custom field. ⚠️ `string` is BOUNDED
    # (`text_field_max_length`, 120 by default on the Apollo side): a longer text is
    # truncated there. For a hook sentence or an email body, use `textarea`.
    CUSTOM_FIELD_TYPES = (
        "string", "textarea", "number", "date", "datetime", "boolean",
    )
    CUSTOM_FIELD_MODALITIES = ("contact", "account", "opportunity")

    def create_custom_field(
        self,
        label: str,
        modality: str = "contact",
        field_type: str = "string",
        max_length: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Declare a new custom field on this Apollo team (0 credit).

        SETUP action, played once per field: it is the API equivalent of
        Settings → Custom Fields, for whoever has no access to the Apollo
        account interface.

        ⚠️ **Apollo does not deduplicate on the label.** Replaying this call creates a
        SECOND field with the same name, and nothing signals it. Both
        appear in the catalog, a sequence variable designates ONE, and
        writes aimed at the other appear nowhere. Read
        `list_typed_custom_fields()` before creating.

        Args:
            label: name of the field as it will be displayed
            modality: `contact` | `account` | `opportunity`
            field_type: see CUSTOM_FIELD_TYPES — `textarea` for a long text,
                `string` being bounded and silently truncating
            max_length: max length (text only)

        Returns:
            Dict with `typed_custom_fields`: the created field, whose `id` is BARE,
            directly usable as a key of `typed_custom_fields`.
        """
        if not (label or "").strip():
            raise ValueError("label required (the name of the field)")
        if modality not in self.CUSTOM_FIELD_MODALITIES:
            raise ValueError(
                f"invalid modality: {modality!r} — expected one of "
                f"{list(self.CUSTOM_FIELD_MODALITIES)}")
        if field_type not in self.CUSTOM_FIELD_TYPES:
            raise ValueError(
                f"invalid field_type: {field_type!r} — expected one of "
                f"{list(self.CUSTOM_FIELD_TYPES)}")
        if max_length is not None and field_type not in ("string", "textarea"):
            raise ValueError(
                f"max_length only makes sense on a text field, not on "
                f"{field_type!r}")
        data: Dict[str, Any] = {
            "label": label, "modality": modality, "type": field_type}
        if max_length is not None:
            data["meta"] = {"max_length": max_length}
        return self._request("POST", "fields", json=data)

    def search_contacts(
        self,
        q_keywords: Optional[str] = None,
        contact_stage_ids: Optional[List[str]] = None,
        contact_label_ids: Optional[List[str]] = None,
        sort_by_field: Optional[str] = None,
        sort_ascending: Optional[bool] = None,
        per_page: int = 25,
        page: int = 1,
    ) -> Dict[str, Any]:
        """
        Search the contacts SAVED BY THIS TEAM (0 credit).

        ⚠️ Does NOT search the shared Apollo database — that is
        `search_people`. Here we only see what the team has saved; in
        return, it is the only place that returns an `id` usable as
        `contact_id` (and as `contact_ids` of a sequence enrollment).

        Args:
            q_keywords: free search on name, title, employer, email
            contact_stage_ids: ids of stages to include
            contact_label_ids: ids of lists/labels to include
            sort_by_field: among CONTACT_SORT_FIELDS
            sort_ascending: ascending order — requires `sort_by_field`
            per_page: results per page (Apollo caps at 100)
            page: page number (Apollo caps display at 500 pages,
                i.e. 50,000 records: beyond that you must filter)

        Returns:
            Dict with `contacts`, `breadcrumbs`, `pagination`
            (`page`, `per_page`, `total_entries`, `total_pages`) and
            `partial_results_only`
        """
        if sort_by_field is not None and sort_by_field not in self.CONTACT_SORT_FIELDS:
            raise ValueError(
                f"invalid sort_by_field: {sort_by_field!r} — expected one of "
                f"{list(self.CONTACT_SORT_FIELDS)}")
        if sort_ascending is not None and not sort_by_field:
            raise ValueError(
                "sort_ascending only makes sense with sort_by_field — without it "
                "Apollo applies its default order and the requested order is lost")
        data: Dict[str, Any] = {"per_page": per_page, "page": page}
        if q_keywords:
            data["q_keywords"] = q_keywords
        if contact_stage_ids:
            data["contact_stage_ids"] = contact_stage_ids
        if contact_label_ids:
            data["contact_label_ids"] = contact_label_ids
        if sort_by_field:
            data["sort_by_field"] = sort_by_field
        if sort_ascending is not None:
            data["sort_ascending"] = sort_ascending
        return self._request("POST", "contacts/search", json=data)

    def get_contact(self, contact_id: str) -> Dict[str, Any]:
        """
        Read one contact of this workspace by its Apollo id (0 credit).

        ⚠️ **Costs nothing, unlike `match_person`**: re-reading someone
        we already own is not an enrichment. Going through `people/match` for that
        burns a credit and returns the record from the SHARED database, not the
        values the team wrote (stage, owner, custom fields).

        Args:
            contact_id: Apollo id of the contact

        Returns:
            Dict with `contact` (including `typed_custom_fields`, `label_ids`,
            `contact_stage_id`, `owner_id`, `phone_numbers`) and `labels`.

        Raises:
            ApolloError: 422 if the contact does not exist, was deleted, or
                does not belong to this key's team.
        """
        if not (contact_id or "").strip():
            raise ValueError("contact_id required")
        return self._request("GET", f"contacts/{contact_id}")

    # Documented writable fields on `PATCH /contacts/{id}`. Frozen here rather than
    # opened to free **kwargs: an invented field name goes into the body,
    # Apollo silently ignores it and returns 200 — the caller believes it has written.
    UPDATABLE_CONTACT_FIELDS = (
        "first_name", "last_name", "organization_name", "title", "account_id",
        "email", "website_url", "label_names", "contact_stage_id",
        "present_raw_address", "direct_phone", "corporate_phone", "mobile_phone",
        "home_phone", "other_phone", "typed_custom_fields",
    )

    def update_contact(self, contact_id: str, **fields: Any) -> Dict[str, Any]:
        """
        Update one contact of this workspace (0 credit). PATCH: fields
        not transmitted are left INTACT.

        ⚠️ `label_names` is an exception to this "intact" — Apollo REPLACES
        list membership with what is sent. Sending a single list
        removes the contact from all the others.

        ⚠️ `typed_custom_fields` is keyed by custom field **id**, not
        by name: `{"<field id>": "2026-08-07"}`. For a picklist, the value
        is the option `id` (`picklist_values[].id`), not its label. The ids are read
        with `list_typed_custom_fields()`.

        Args:
            contact_id: Apollo id of the contact to modify
            **fields: among UPDATABLE_CONTACT_FIELDS. A name outside the list raises
                ValueError rather than going off to be ignored by Apollo.

        Returns:
            Dict with the updated `contact`
        """
        if not (contact_id or "").strip():
            raise ValueError("contact_id required")
        unknown = sorted(set(fields) - set(self.UPDATABLE_CONTACT_FIELDS))
        if unknown:
            raise ValueError(
                f"fields not modifiable on an Apollo contact: {unknown} — "
                f"expected one of {list(self.UPDATABLE_CONTACT_FIELDS)}")
        data = {k: v for k, v in fields.items() if v is not None}
        if not data:
            raise ValueError("no field to modify")
        tcf = data.get("typed_custom_fields")
        if tcf is not None and not isinstance(tcf, dict):
            raise ValueError(
                "typed_custom_fields must be an object {field_id: value} — "
                "keyed by the id returned by list_typed_custom_fields(), not by name")
        return self._request("PATCH", f"contacts/{contact_id}", json=data)
