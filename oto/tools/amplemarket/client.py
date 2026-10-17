"""Amplemarket API client — B2B prospecting, enrichment and outreach.

REST API (`https://api.amplemarket.com`), authenticated by a workspace API key
sent as `Authorization: Bearer <key>`. One method = one endpoint; responses are
returned as-is, the client invents no semantics. The methods implement the
functions described in `connectors/amplemarket/connector.yaml`, under the same
names and with the same arguments.

Scope: the account and its users, personas and territories; Prospect Hub
accounts and contacts; the people and company searcher; single and batch
enrichment of people and companies; batch email validation; lead lists;
sequences (list, add leads); tasks; calls (list, log, dispositions);
mailboxes; email and domain exclusion lists; phone-number review; job openings.
Not covered: the Duo custom-signal webhook (a per-workspace URL, not an API
route) and call recordings (a binary download).

What the caller needs to know, and cannot guess:

- **Two paginations.** Lists page by cursor: `page_size`, `page_after` /
  `page_before` (`page[size]`, `page[after]`, `page[before]`), the next page
  being announced in `_links.next.href`; `next_page_after(response)` reads the
  cursor to pass back. The searcher (`search_people`, `search_companies`) pages
  by number in the body (`page`, `page_size`) and answers `_pagination`
  (`page`, `page_size`, `total_pages`, `total`).

- **Batches are asynchronous.** `start_*` answers 202 with an `id` and a
  `status`; read the results with the matching `get_*` until the status is
  final, or stop the batch with `cancel_*`. Up to 10,000 leads or companies
  and 100,000 emails per batch; 15,000 enrichments or validations per hour.

- **Credits.** Enrichments (with `reveal_email` / `reveal_phone_numbers`),
  email validations and lead lists with enrichment options consume the
  account's credits: an empty balance answers 402 `insufficient_credits`.

- **Rate limits.** 500 requests/min overall; 300/min on the people search,
  350/min on the single person enrichment, 30/min on adding leads to a
  sequence, one daily-limit change per mailbox per minute and per hour.

- **Errors** carry `_errors[]` with `code` (`insufficient_credits`,
  `person_not_found`, `unavailable_for_legal_reasons`, `unsupported_value`,
  `missing_field`, `unauthorized`), `detail` and `source.pointer`.

Only GET is replayed on 429/5xx: adding leads to a sequence or a list, creating
a contact or logging a call twice would duplicate it.

Requires: requests
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests

from ..common import raise_for_upstream
from ..common.credentials import require
from ..http.client import refuse_redirect
from ._api.engagement import EngagementMixin
from ._api.prospecting import ProspectingMixin
from ._api.shapes import clean, encode_query, next_page_after
from ._api.workspace import WorkspaceMixin

__all__ = ["AmplemarketClient", "next_page_after"]

# (connect, read) — never an unbounded wait.
HTTP_TIMEOUT = (10, 60)

BASE_URL = "https://api.amplemarket.com"

# GET only: the writes are not idempotent.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3


class AmplemarketClient(WorkspaceMixin, ProspectingMixin, EngagementMixin):
    """Amplemarket API client (https://api.amplemarket.com), Bearer auth."""

    BASE_URL = BASE_URL

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: Amplemarket API key, passed by the consumer. An admin
                creates it in Amplemarket, Settings > API.
        """
        self.api_key = require(api_key, "AMPLEMARKET_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 body: Optional[Dict[str, Any]] = None) -> Any:
        """ONE call. Unset query parameters and body fields are dropped; a GET
        is replayed on 429/5xx, a write never is. Redirects are refused before
        any retry (the key must not follow a 3xx cross-host)."""
        query = encode_query(params or {})
        payload = clean(body) if body is not None else None
        url = f"{self.BASE_URL}{path}"
        attempts = MAX_ATTEMPTS if method == "GET" else 1
        resp = None
        for attempt in range(attempts):
            resp = self.session.request(
                method, url, params=query or None, json=payload,
                timeout=HTTP_TIMEOUT, allow_redirects=False)
            refuse_redirect(resp)
            if resp.status_code not in RETRY_STATUSES or attempt == attempts - 1:
                break
            time.sleep(float(2 ** attempt))
        raise_for_upstream(resp, service="amplemarket")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"status": resp.status_code, "text": resp.text[:2000]}

    # --- probe --------------------------------------------------------------

    def probe(self) -> Any:
        """Check that the key authenticates, for the cost of one read (401 if wrong)."""
        return self.get_account_info()
