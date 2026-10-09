"""Luma public API client (https://docs.luma.com) — events, guests, tickets,
blasts, calendar contacts and tags, memberships, webhooks, organization.

Auth by header `x-luma-api-key`. Host `https://public-api.luma.com`; routes
are `/v{n}/{resource}/{action}`, versioned PER ROUTE (a `/v2/` path is that
route's version, not the API's). Reads are `GET`, every write is a `POST`
with a JSON body. One method per endpoint; responses are returned as parsed
JSON, unchanged.

This module holds construction and transport, and composes the call families
of `_api/`. Constants live in `const.py` and are re-exported here: the backend
pins oto-core by tag and only imports `oto.tools.luma.client`.

## Protocol facts that shape a caller

- **A key belongs to a calendar or to an organization.** A calendar key acts
  on its own calendar. An organization key covers every calendar of the
  organization: calendar-scoped routes then need the target calendar, sent as
  the `x-luma-calendar-id` header — pass `calendar_id` to the constructor.
  The API requires an active **Luma Plus** subscription on the calendar.
- **Cursor pagination everywhere**: `pagination_cursor` / `pagination_limit`
  in, `{entries, has_more, next_cursor}` out. Loop on `has_more`, never on
  the size of `entries`; `iterate()` writes that loop once.
- **Dates are ISO 8601 in UTC** (`2026-10-04T05:20:00.000Z`); durations are
  ISO 8601 durations (`PT1H30M`). An event also carries its IANA `timezone`.
- **Several calls reach people outside the organization**: sending invites,
  creating a blast (email to guests, cannot be recalled once sent), changing a
  guest's status (emails the guest unless `send_email=false`), cancelling an
  event (notifies every guest, may refund, deletes the event). Cancellation
  is a two-step flow on Luma's side: `request_event_cancellation` returns a
  token valid 15 minutes, `cancel_event` spends it.
- **Rate limit**: 200 requests/minute per calendar (calendar keys), 500 per
  organization (organization keys). A 429 blocks for one minute and carries
  `Retry-After`; reads are retried once when the wait is short. Writes are
  never replayed (no idempotency key).
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import requests

from ..common import raise_for_upstream
from ..common.credentials import require
from ._api import (_BlastsMixin, _CalendarMixin, _ContactsMixin, _EventsMixin,
                   _GuestsMixin, _MembershipsMixin, _MetaMixin,
                   _OrganizationMixin, _TicketsMixin, _WebhooksMixin)
from .const import (BASE_URL, BLAST_RECIPIENT_STATUSES,
                    CALENDAR_EVENT_SORT_COLUMNS, CALENDAR_EVENT_STATUSES,
                    CALENDAR_LAUNCH_STATUSES, CONTACT_SORT_COLUMNS,
                    COUPON_DISCOUNT_TYPES, EVENT_ACCESS, EVENT_PLATFORMS,
                    GUEST_ADD_STATUSES, GUEST_LIST_STATUSES,
                    GUEST_SET_STATUSES, GUEST_SORT_COLUMNS, HOST_ACCESS_LEVELS,
                    HTTP_TIMEOUT, IMAGE_CONTENT_TYPES, MAX_ATTEMPTS,
                    MEMBERSHIP_LIST_STATUSES, MEMBERSHIP_SET_STATUSES,
                    MIN_LIMIT, RATE_LIMIT_MAX_WAIT, RETRY_STATUSES,
                    SORT_DIRECTIONS, SUBMISSION_MODES, TAG_COLORS,
                    TICKET_PRICE_TYPES, WEBHOOK_EVENT_TYPES, WEBHOOK_STATUSES)


class LumaClient(
    _MetaMixin,
    _EventsMixin,
    _GuestsMixin,
    _BlastsMixin,
    _TicketsMixin,
    _CalendarMixin,
    _ContactsMixin,
    _MembershipsMixin,
    _WebhooksMixin,
    _OrganizationMixin,
):
    """Luma public API. Header auth `x-luma-api-key`."""

    BASE_URL = BASE_URL

    def __init__(self, api_key: Optional[str] = None,
                 calendar_id: Optional[str] = None):
        """
        Args:
            api_key: a Luma API key — calendar key or organization key
                (Luma → calendar or organization settings → Developer).
            calendar_id: with an ORGANIZATION key, the calendar that
                calendar-scoped routes act on (`cal-…`), sent as
                `x-luma-calendar-id`. Ignored by a calendar key's own routes.
        """
        self.api_key = require(api_key, "LUMA_API_KEY")
        self.calendar_id = (calendar_id or "").strip() or None
        self.session = requests.Session()
        # Key in the HEADER only — never in the URL, which ends up in exception
        # messages, logs and error tracking.
        headers = {"x-luma-api-key": self.api_key,
                   "Accept": "application/json"}
        if self.calendar_id:
            headers["x-luma-calendar-id"] = self.calendar_id
        self.session.headers.update(headers)

    # --- validation ---------------------------------------------------------

    @staticmethod
    def _check_choice(name: str, value: Optional[Any],
                      allowed: Iterable[Any]) -> None:
        """Reject locally a value outside the enum, NAMING the valid ones.

        Enums are passed by the caller from `const.py`: the same parameter
        name does not carry the same values on every route."""
        if value is None:
            return
        allowed = tuple(allowed)
        values = value if isinstance(value, (list, tuple)) else [value]
        for v in values:
            if v not in allowed:
                raise ValueError(
                    f"`{name}` invalid: {v!r}. Accepted values: "
                    + ", ".join(repr(a) for a in allowed))

    @staticmethod
    def _need(value: Any, name: str) -> Any:
        """A required argument, refused locally when empty."""
        if value is None or (isinstance(value, (str, list, tuple, dict))
                             and not value):
            raise ValueError(f"`{name}` is required.")
        return value

    @staticmethod
    def _check_limit(limit: Optional[int]) -> None:
        if limit is None:
            return
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < MIN_LIMIT:
            raise ValueError(
                f"`limit` must be an integer >= {MIN_LIMIT}; got {limit!r}. "
                "The server caps it at its own maximum.")

    # --- transport ----------------------------------------------------------

    @staticmethod
    def _encode_params(params: Optional[Dict[str, Any]]) -> List[Tuple[str, Any]]:
        """Params → list of pairs, `None` dropped, booleans as `true`/`false`,
        a list as REPEATED keys (`?access=manage&access=view`) — the form the
        API reads for its multi-valued filters."""
        out: List[Tuple[str, Any]] = []
        for key, value in (params or {}).items():
            if value is None:
                continue
            if isinstance(value, bool):
                out.append((key, "true" if value else "false"))
            elif isinstance(value, (list, tuple)):
                out.extend((key, v) for v in value)
            else:
                out.append((key, value))
        return out

    @staticmethod
    def _retry_wait(resp: Any, attempt: int) -> Optional[float]:
        """Seconds to wait before retrying a read; `None` = do not retry.

        A 429 honours `Retry-After` when it is short enough; a 5xx backs off
        exponentially."""
        if resp.status_code == 429:
            raw = (getattr(resp, "headers", None) or {}).get("Retry-After")
            try:
                wait = max(0.0, float(raw))
            except (TypeError, ValueError):
                return None
            return wait if wait <= RATE_LIMIT_MAX_WAIT else None
        return float(2 ** attempt)

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 json: Any = None) -> Any:
        encoded = self._encode_params(params)
        retryable = method.upper() == "GET"
        last = None
        for attempt in range(MAX_ATTEMPTS):
            last = self.session.request(
                method, f"{self.BASE_URL}{path}", params=encoded or None,
                json=json, timeout=HTTP_TIMEOUT)
            if (last.status_code not in RETRY_STATUSES or not retryable
                    or attempt == MAX_ATTEMPTS - 1):
                break
            wait = self._retry_wait(last, attempt)
            if wait is None:
                break
            time.sleep(wait)
        raise_for_upstream(last, service="luma")
        return last.json() if last.content else {}

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._request("GET", path, params=params)

    def _post(self, path: str, body: Dict[str, Any]) -> Any:
        """POST a JSON body, sent as given — build it with `_body`."""
        return self._request("POST", path, json=body)

    def _list(self, path: str, limit: Optional[int], cursor: Optional[str],
              extra: Optional[Dict[str, Any]] = None) -> Any:
        self._check_limit(limit)
        params: Dict[str, Any] = {"pagination_limit": limit,
                                  "pagination_cursor": cursor}
        params.update(extra or {})
        return self._get(path, params)

    @staticmethod
    def _body(required: Dict[str, Any],
              fields: Optional[Dict[str, Any]] = None,
              optional: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """A request body: `fields` as given (explicit nulls kept — that is
        how a nullable field is CLEARED), then the named optional arguments
        that were set (a `None` one is dropped, so omitting an argument never
        clears a field upstream), then the required ones, which win over a
        same-named key in `fields`."""
        body: Dict[str, Any] = dict(fields or {})
        body.update({k: v for k, v in (optional or {}).items() if v is not None})
        body.update(required)
        return body

    # --- pagination ---------------------------------------------------------

    def iterate(self, method: Any, *args: Any,
                max_pages: Optional[int] = None, **kwargs: Any) -> Iterator[Any]:
        """Walk a cursor-paginated list page by page and yield the ENTRIES.

        Stops on `has_more` false or a missing `next_cursor` — never on an
        empty page. `method` is a list method of this client ::

            for guest in client.iterate(client.list_guests, "evt-…"):
                ...

        ⚠️ Do not pass `cursor`: this loop manages it.
        """
        if "cursor" in kwargs:
            raise ValueError("`iterate` manages the cursor itself — do not pass it.")
        pages = 0
        cursor: Optional[str] = None
        while True:
            payload = method(*args, cursor=cursor, **kwargs)
            if not isinstance(payload, dict):
                return
            for row in payload.get("entries") or []:
                yield row
            cursor = payload.get("next_cursor")
            pages += 1
            if not payload.get("has_more") or not cursor:
                return
            if max_pages is not None and pages >= max_pages:
                return


__all__ = [
    "LumaClient", "BASE_URL", "HTTP_TIMEOUT", "RETRY_STATUSES", "MAX_ATTEMPTS",
    "RATE_LIMIT_MAX_WAIT", "MIN_LIMIT", "SORT_DIRECTIONS",
    "EVENT_PLATFORMS", "EVENT_ACCESS", "CALENDAR_EVENT_STATUSES",
    "CALENDAR_EVENT_SORT_COLUMNS", "SUBMISSION_MODES",
    "GUEST_LIST_STATUSES", "GUEST_SORT_COLUMNS", "GUEST_SET_STATUSES",
    "GUEST_ADD_STATUSES", "HOST_ACCESS_LEVELS", "BLAST_RECIPIENT_STATUSES",
    "TICKET_PRICE_TYPES", "COUPON_DISCOUNT_TYPES", "CONTACT_SORT_COLUMNS",
    "MEMBERSHIP_LIST_STATUSES", "MEMBERSHIP_SET_STATUSES", "TAG_COLORS",
    "CALENDAR_LAUNCH_STATUSES", "IMAGE_CONTENT_TYPES", "WEBHOOK_EVENT_TYPES",
    "WEBHOOK_STATUSES",
]
