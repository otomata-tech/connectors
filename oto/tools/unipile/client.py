"""Unipile API client — hosted LinkedIn search / scrape / messaging (API v2).

Unipile keeps the LinkedIn session server-side (real Chrome + residential
proxy), which sidesteps the two constraints of the local browser: TLS
fingerprint and session isolation (the cookie doesn't live on our datacenter IP,
so it neither exposes nor disconnects the user's session). See oto-mcp#5.

Requires: requests

Parameters, always supplied by the consumer:
- api_key    (required) — X-API-KEY of the Unipile account
- dsn        (default api.unipile.com) — instance host
- account_id (optional) — otherwise, the first connected LINKEDIN account

API v2 specifics (compared to the old, retired API v1):
- **base**: `https://{dsn}/v2`.
- **`account_id` in the PATH** (`/v2/{account_id}/…`), no longer a query param — so
  it no longer leaks into query strings, but may appear in an error URL
  → we **redact** it in messages (`_sanitize`, oto feedback #178).
- **list envelope** `{data, total_count, next_cursor}`. We **normalize** every
  list response to `items`/`cursor` IN ADDITION to keeping `data`/`next_cursor` →
  the downstream oto-mcp (feed sync, wrappers, agent expectations) stays stable.
- **split surface**: separate people/companies search + per product
  (classic/recruiter/sales-navigator); invitations = `users/me/relation-requests`;
  attendees of a thread = `participants`; message reactions under the chat;
  InMail balance = `inmail-credits`.

Feedback fold-in fixes (beyond the raw API):
- **anti-mismatch guard** identifier↔response on `get_profile`/`get_company`
  (feedback #144-149/#153: under concurrency, the API returned the profile of ANOTHER
  member / a CompanyProfile instead). We check that the returned object matches
  both the type AND the requested identifier, otherwise an **actionable and
  retryable** `UnipileError` — never wrong data returned silently.
- **clean network errors**: `requests` exceptions (DNS/timeout) are mapped
  to a stable `UnipileError` instead of leaking `net::ERR_NAME_NOT_RESOLVED` (#177).
- **tolerant slug resolution** on `get_company` (#176): a brand name
  passed as a slug (`mooniz`) that 404s is retried via a company
  search → canonical `public_identifier` (`mooniz1`); on failure → clean 404
  enriched with close candidates, never the raw error.

Package structure (split of 2026-08-27, public surface UNCHANGED):
`client.py` holds the `UnipileClient` class — construction, transport and
normalization — and composes the call families of `_api/` (accounts, search,
profiles, messaging, network, content, premium). The constants/helpers live
in `const.py`, the errors in `errors.py`, the feed parsing in `feed.py`
— all **re-exported here**, because `oto.tools.unipile.client` is the import path
that the backend and the tests use.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

import requests

from ..common.credentials import require
from ._api import (
    _AccountsMixin,
    _ContentMixin,
    _MessagingMixin,
    _NetworkMixin,
    _PremiumMixin,
    _ProfilesMixin,
    _SearchMixin,
)
from .const import (
    DEFAULT_DSN,
    FEED_QUERY_ID,
    _API_PREFIX,
    _DEFAULT_PROVIDER,
    _INBOX_PROVIDERS,
    _REQUEST_TIMEOUT,
    _SCRAPE_TIMEOUT,
    _URL_SEARCH_TIMEOUT,
    _sections_param,
    _slug_from_company_url,
    cursor_with_limit,
)
from .errors import (
    UnipileError,
    UnipileRateLimited,
    _RETRY_RE,
    _parse_retry_after,
    _retry_after_header,
)
from .feed import (
    _CAMEL_SPLIT,
    _CONTENT_ALIASES,
    _CONTENT_LABEL_KEYS,
    _CONTENT_LABEL_MAX,
    _CONTENT_PRIORITY,
    _activity_urn_from,
    _annotated_entity,
    _comment_authors,
    _content_facets,
    _content_key_to_type,
    _content_label,
    _deep_get,
    _extract_activity,
    _feed_context,
    _is_promo,
    _map_feed_item,
    _posted_at_from_activity,
    _social_counts,
    _text_of,
    _unpack_cursor,
    parse_feed,
)

logger = logging.getLogger(__name__)

# Frozen surface of `oto.tools.unipile.client`: this module remains THE import point
# of the connector. Everything that was importable before the split still is —
# the backend takes `UnipileError`/`UnipileRateLimited` from here, and the test suite
# `cursor_with_limit`, `parse_feed`, `_parse_retry_after`, `_activity_urn_from`…
# Names prefixed with `_` are listed because they are IMPORTED ELSEWHERE, not
# because they would be public: `tests/test_unipile_surface_frozen.py` locks
# this list.
__all__ = [
    "DEFAULT_DSN",
    "FEED_QUERY_ID",
    "UnipileClient",
    "UnipileError",
    "UnipileRateLimited",
    "cursor_with_limit",
    "parse_feed",
    "_API_PREFIX",
    "_CAMEL_SPLIT",
    "_CONTENT_ALIASES",
    "_CONTENT_LABEL_KEYS",
    "_CONTENT_LABEL_MAX",
    "_CONTENT_PRIORITY",
    "_DEFAULT_PROVIDER",
    "_INBOX_PROVIDERS",
    "_REQUEST_TIMEOUT",
    "_RETRY_RE",
    "_SCRAPE_TIMEOUT",
    "_URL_SEARCH_TIMEOUT",
    "_activity_urn_from",
    "_annotated_entity",
    "_comment_authors",
    "_content_facets",
    "_content_key_to_type",
    "_content_label",
    "_deep_get",
    "_extract_activity",
    "_feed_context",
    "_is_promo",
    "_map_feed_item",
    "_parse_retry_after",
    "_retry_after_header",
    "_posted_at_from_activity",
    "_sections_param",
    "_slug_from_company_url",
    "_social_counts",
    "_text_of",
    "_unpack_cursor",
]


class UnipileClient(
    _AccountsMixin,
    _SearchMixin,
    _ProfilesMixin,
    _MessagingMixin,
    _NetworkMixin,
    _ContentMixin,
    _PremiumMixin,
):
    """Unipile API v2 client — hosted LinkedIn (and other IMs)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        dsn: Optional[str] = None,
        account_id: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        self.api_key = require(api_key, "UNIPILE_API_KEY")
        self.dsn = dsn or DEFAULT_DSN
        self.base_url = f"https://{self.dsn}/v2"
        self._account_id = account_id
        # Channel of the operated account (LINKEDIN, WHATSAPP, …). Determines the
        # messaging endpoint shape (see `_INBOX_PROVIDERS`); None = assumed LinkedIn (compat).
        self.provider = (provider or "").strip().upper() or None
        self.session = requests.Session()
        self.session.headers.update(
            {"X-API-KEY": self.api_key, "accept": "application/json"}
        )

    # ---- transport -------------------------------------------------------

    def _sanitize(self, msg: str) -> str:
        """Redact the account_id in an error message (it lives in the v2 path →
        would otherwise surface in a 404 URL, feedback #178)."""
        acct = self._account_id
        if acct and isinstance(msg, str):
            return msg.replace(acct, "<account>")
        return msg

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        timeout: Optional[tuple] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.request(
                method, url, params=params, json=json,
                timeout=timeout or _REQUEST_TIMEOUT)
        except requests.RequestException as e:
            # DNS/timeout/reset: stable error instead of leaking net::ERR_* (#177).
            raise UnipileError(
                self._sanitize(f"Unipile: network error ({type(e).__name__}).")
            ) from e
        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = (body.get("detail") or body.get("message")
                       or body.get("title") or resp.text)
            except (ValueError, AttributeError):
                msg = resp.text or f"{resp.status_code} {resp.reason}"
            full = self._sanitize(f"Unipile {resp.status_code}: {msg}")
            # 429 = upstream quota (LinkedIn caps company/profile pages ~100/12h per
            # account) → dedicated type + parsed delay, the caller STOPS (see UnipileRateLimited).
            # The delay comes from the `Retry-After` header when upstream sets it (seconds),
            # otherwise from the body ("Retry in 3 seconds") — oto#177.
            if resp.status_code == 429:
                raise UnipileRateLimited(full, retry_after=(
                    _retry_after_header(resp.headers) or _parse_retry_after(msg)))
            raise UnipileError(full, status_code=resp.status_code)
        if not resp.text:
            return None
        return resp.json()

    def _acct(self, sub_path: str) -> str:
        """Prefix a sub-path with `/{account_id}` (v2 path param)."""
        return f"/{quote(self.account_id(), safe='')}{sub_path}"

    def uses_inboxes(self) -> bool:
        """Does this account's provider organize its messaging by inbox? (see
        `_INBOX_PROVIDERS`). Undeclared provider → assumed LinkedIn."""
        return (self.provider or _DEFAULT_PROVIDER) in _INBOX_PROVIDERS

    def _by_shape(self, inbox_call, plain_call, what: str) -> Any:
        """Call the endpoint shape DECLARED for this provider, and fall back to
        the other one if Unipile answers **501**.

        Unipile's 501 is not an outage: it is upstream NAMING the expected shape
        ("Use List inbox Chats endpoint", "Use Start a Chat in the given
        inbox endpoint for this provider", and the symmetric one for a provider without
        inbox). The fallback therefore works in both directions and masks nothing
        else — any other status bubbles up as is, and the switch is
        LOGGED: if Unipile reclassifies a provider, it shows in the logs instead
        of silently breaking a channel, as on 2026-07-06 for LinkedIn and then
        the same day for WhatsApp. The identity (`account_id`) is in the path of
        both shapes: only the ROUTE switches here."""
        inbox_first = self.uses_inboxes()
        first, second = ((inbox_call, plain_call) if inbox_first
                         else (plain_call, inbox_call))
        try:
            return first()
        except UnipileError as e:
            if e.status_code != 501:
                raise
            logger.warning(
                "unipile %s: 501 on the %s shape for provider=%s — switching to "
                "the %s shape. If this repeats, `_INBOX_PROVIDERS` has drifted from the "
                "Unipile model.",
                what, "inbox" if inbox_first else "flat",
                self.provider or f"{_DEFAULT_PROVIDER} (assumed)",
                "flat" if inbox_first else "inbox")
            return second()

    @staticmethod
    def _norm(data: Any) -> Any:
        """Normalize a v2 list envelope `{data, next_cursor, total_count}`
        to the `items`/`cursor` shape expected downstream WITHOUT losing the native
        fields. No-op if `data` is not a list envelope."""
        if not isinstance(data, dict):
            return data
        if "data" in data and isinstance(data.get("data"), list):
            data.setdefault("items", data["data"])
        if "next_cursor" in data:
            data.setdefault("cursor", data.get("next_cursor"))
        return data
