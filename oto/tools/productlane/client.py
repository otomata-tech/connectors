"""Productlane API client — customer feedback, roadmap, help center.

API **v2** (`https://productlane.com/api/v2`, docs https://productlane.mintlify.dev),
auth **Bearer**. One method = one endpoint; bodies and responses pass through as-is,
the client invents no semantics. Paths, verbs, parameters and scopes were
taken from the OpenAPI published by the vendor (`openapi-v2.json`) on 2026-09-02.

This module carries **construction and transport**, and composes the call
families from `_api/` (threads, contacts, companies, roadmap, changelogs, docs,
taxonomy, meta). Constants live in `const.py` and are re-exported here:
the backend pins oto-core by tag and only imports
`oto.tools.productlane.client`.

Five things the caller must know:

- **CURSOR pagination, everywhere, no exception.** No `page`, no `offset`,
  no `skip` anywhere. A list returns `{data: [...], page: {cursor, has_more,
  limit}}`; loop on `has_more`, not on the size of `data`
  ("Empty array on the last page if it lined up"). `iterate()` writes this
  loop once — copying it at the call site is the easiest way to
  lose a page. Order is fixed server-side (`created_at DESC, id DESC`), with no
  parameter to change it.

- ⚠️ **`limit` caps at 200** (default 50). `_check_limit` rejects out-of-range
  values locally rather than letting a 400 go out.

- ⚠️ **Productlane is a MIRROR of Linear for its roadmap.** Projects and issues
  are created in Linear first; updates and deletions are pushed
  there, **and a failure of that sync does NOT fail the call** (it is
  logged on the vendor side). A `200` on `update_issue` therefore does not prove
  that Linear followed. See `_api/roadmap.py`.

- ⚠️ **Only one call in this whole client writes to third parties**:
  `broadcast_changelog` (email to subscribed contacts and/or Slack post).
  It cannot be cancelled or recalled. It is handled separately in
  `_api/changelogs.py` — explicit signature, local refusal if no channel.

- **Enums are scoped to their endpoint**: `status` does not mean the
  same thing on a thread and on a doc draft, `type` not the same thing on
  a blocked sender and on a message. `const.py` gives the reason, and carries
  one name per usage rather than one name per parameter.

Upstream limits **per key**: 1000 GET/minute, 60 writes/minute, 2x burst over
10 s. Every response carries `X-RateLimit-{Limit,Remaining,Reset}`; the 429 carries
`Retry-After`, honored by the retry loop — **for reads only**,
since the API offers no idempotency key.

**Out of scope, deliberately** (do not "complete" without a decision): all
workspace administration — `/members`, invitations, role changes,
member removal. It requires the `admin` scope, sends invitation emails,
and has no place in a customer-feedback connector.

Requires: requests
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import requests

from ..common.credentials import require
from ..common import raise_for_upstream
from ._api import (_ChangelogsMixin, _CompaniesMixin, _ContactsMixin,
                   _DocsMixin, _MetaMixin, _RoadmapMixin, _TaxonomyMixin,
                   _ThreadsMixin)
from .const import (BLOCKED_SENDER_TYPES, DEFAULT_LIMIT, DOC_KIND_FILTERS,
                    DOC_KINDS, DOC_VISIBILITIES, DOC_VISIBILITY_FILTERS,
                    DRAFT_KINDS, DRAFT_STATUSES, HTTP_TIMEOUT, ISSUE_PRIORITIES,
                    MAX_ATTEMPTS, MAX_LIMIT, MESSAGE_DIRECTIONS,
                    MESSAGE_ORDERS, MESSAGE_TYPES, MIN_LIMIT, PAIN_LEVELS,
                    PROJECT_STATES, RETRY_STATUSES, ROADMAP_SORTS,
                    THREAD_EXPANDS, THREAD_ORIGINS, THREAD_STATUSES,
                    THREAD_TABS)


class ProductlaneClient(
    _MetaMixin,
    _ThreadsMixin,
    _ContactsMixin,
    _CompaniesMixin,
    _RoadmapMixin,
    _ChangelogsMixin,
    _DocsMixin,
    _TaxonomyMixin,
):
    """Productlane v2 client (https://productlane.com/api/v2), Bearer auth."""

    BASE_URL = "https://productlane.com/api/v2"

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: Productlane v2 API key.

        The key is generated in Productlane (Settings → API). ⚠️ A **v1** key does
        not work here: v2 is a separate API, and v1 shuts down on 2026-11-20.
        """
        self.api_key = require(api_key, "PRODUCTLANE_API_KEY")
        self.session = requests.Session()
        # Key in the HEADER only (never in the query string: it would end up in
        # the URL, hence in every exception message, the logs and Sentry).
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # --- transport ----------------------------------------------------------

    @staticmethod
    def _check_limit(limit: Optional[int]) -> None:
        """A `limit` outside [1, 200] is rejected HERE. The API would return a 400; saying
        so locally names the real bound, which nobody can guess."""
        if limit is None:
            return
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ValueError("`limit` must be an integer.")
        if not (MIN_LIMIT <= limit <= MAX_LIMIT):
            raise ValueError(
                f"`limit` must be between {MIN_LIMIT} and {MAX_LIMIT} "
                f"(Productlane API cap); got {limit}. "
                "Beyond that, paginate with `cursor` (or loop with `iterate`).")

    @staticmethod
    def _check_choice(name: str, value: Optional[Any],
                      allowed: Iterable[Any]) -> None:
        """Locally reject a value outside the enum, NAMING the valid ones.

        ⚠️ Enums are passed BY THE CALLER, from `const.py`:
        the same parameter name does not have the same values everywhere (see the header
        of `const.py`), so this guard cannot infer them from the name.
        """
        if value is None:
            return
        allowed = tuple(allowed)
        if value not in allowed:
            raise ValueError(
                f"`{name}` invalid: {value!r}. Accepted values: "
                + ", ".join(repr(a) for a in allowed))

    @staticmethod
    def _encode_params(params: Optional[Dict[str, Any]]) -> List[Tuple[str, Any]]:
        """Params → list of pairs, `None` dropped, booleans as `true`/`false`.

        (requests would write `True`, which the server does not read as a boolean.)
        A list is joined with commas: that is the form the v2 API reads
        for its few multi-valued parameters (`expand`).
        """
        out: List[Tuple[str, Any]] = []
        for key, value in (params or {}).items():
            if value is None:
                continue
            if isinstance(value, bool):
                out.append((key, "true" if value else "false"))
            elif isinstance(value, (list, tuple)):
                out.append((key, ",".join(str(v) for v in value)))
            else:
                out.append((key, value))
        return out

    @staticmethod
    def _retry_after(resp: Any, attempt: int) -> float:
        """Delay before retrying: `Retry-After` if present (upstream knows better
        than we do), otherwise exponential backoff."""
        raw = (getattr(resp, "headers", None) or {}).get("Retry-After")
        if raw:
            try:
                return max(0.0, float(raw))
            except (TypeError, ValueError):
                pass
        return float(2 ** attempt)

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 json: Any = None) -> Any:
        encoded = self._encode_params(params)
        # Retry 429/5xx for READS only: the API offers NO idempotency key,
        # so replaying a POST would create a duplicate — one more thread, or
        # worse, a changelog broadcast sent twice.
        retryable = method.upper() in ("GET", "HEAD")
        last = None
        for attempt in range(MAX_ATTEMPTS):
            last = self.session.request(
                method, f"{self.BASE_URL}{path}", params=encoded or None,
                json=json, timeout=HTTP_TIMEOUT)
            if (last.status_code not in RETRY_STATUSES
                    or not retryable or attempt == MAX_ATTEMPTS - 1):
                break
            time.sleep(self._retry_after(last, attempt))
        raise_for_upstream(last, service="productlane")
        return last.json() if last.content else {}

    def _list(self, path: str, limit: Optional[int], cursor: Optional[str],
              extra: Optional[Dict[str, Any]] = None) -> Any:
        self._check_limit(limit)
        params: Dict[str, Any] = {"limit": limit, "cursor": cursor}
        params.update(extra or {})
        return self._request("GET", path, params=params)

    # --- pagination ---------------------------------------------------------

    def iterate(self, method: Any, *args: Any,
                max_pages: Optional[int] = None, **kwargs: Any) -> Iterator[Any]:
        """Walk a cursor-paginated list page by page and yield the ROWS.

        Writes once what every caller would rewrite badly: the loop stops
        on `page.has_more`, **not** on an empty `data` — the vendor docs warn
        that a last page can be empty "if it lined up", and stopping there
        would miss the opposite case (rows behind a true `has_more`).

        `method` is a list method of this client, passed as-is ::

            for fil in client.iterate(client.list_threads, status="open"):
                ...

        `max_pages` bounds the walk — useful when the caller serves an agent and
        has to stay within a response budget.

        ⚠️ Do not pass `cursor`: this loop manages it.
        """
        if "cursor" in kwargs:
            raise ValueError(
                "`iterate` manages the cursor itself — do not pass it.")
        pages = 0
        cursor: Optional[str] = None
        while True:
            payload = method(*args, cursor=cursor, **kwargs)
            if not isinstance(payload, dict):
                return
            for row in payload.get("data") or []:
                yield row
            page = payload.get("page") or {}
            cursor = page.get("cursor")
            pages += 1
            if not page.get("has_more") or not cursor:
                return
            if max_pages is not None and pages >= max_pages:
                return


__all__ = [
    "ProductlaneClient",
    "DEFAULT_LIMIT", "MIN_LIMIT", "MAX_LIMIT", "HTTP_TIMEOUT",
    "RETRY_STATUSES", "MAX_ATTEMPTS",
    "THREAD_STATUSES", "THREAD_TABS", "PAIN_LEVELS", "THREAD_ORIGINS",
    "THREAD_EXPANDS", "MESSAGE_ORDERS", "MESSAGE_TYPES", "MESSAGE_DIRECTIONS",
    "BLOCKED_SENDER_TYPES", "PROJECT_STATES", "ROADMAP_SORTS",
    "DOC_VISIBILITIES", "DOC_VISIBILITY_FILTERS", "DOC_KINDS",
    "DOC_KIND_FILTERS", "DRAFT_KINDS", "DRAFT_STATUSES", "ISSUE_PRIORITIES",
]
