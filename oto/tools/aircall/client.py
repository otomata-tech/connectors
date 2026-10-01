"""Aircall Public API client (https://developer.aircall.io/api-references/) —
read-only: calls, conversation intelligence of a call, users, teams, numbers,
contacts.

HTTP Basic auth, `api_id` as user name and `api_token` as password, both
created by an Aircall admin under Company Settings → API Keys. Host
`https://api.aircall.io`; every path carries its version (`/v1/calls`,
`/v2/users`). One method per endpoint; responses are returned as parsed JSON,
unchanged.

## Protocol facts that shape a caller

- **Timestamps are UNIX seconds, UTC.** `from`/`to` bound the CREATION date of
  the listed objects. `unix_seconds` also accepts an ISO 8601 date (midnight
  UTC) or an ISO 8601 date-time carrying its offset; a date-time without an
  offset is refused rather than guessed.
- **Pagination**: `page` (from 1) and `per_page` (1-50, default 20). Every list
  answers with `meta` (`count`, `total`, `current_page`, `per_page`,
  `next_page_link`, `previous_page_link`). Calls and contacts stop at 10,000
  items whatever the page: narrow with `from` to go further.
- **Calls history covers six months.** Lists are ordered by creation date,
  ascending unless `order="desc"`.
- **Recording and voicemail URLs expire**: `recording` and `voicemail` (direct
  mp3) are valid one hour, `recording_short_url` / `voicemail_short_url` three
  hours (returned only with `fetch_short_urls=True`). Fetch them again rather
  than storing them.
- **Conversation intelligence** (transcription, summary, topics, sentiments,
  action items) is served only to companies on the Aircall AI package (AI
  Assist or AI Assist Pro); the `realtime` transcription mode needs AI Assist
  Pro. A call without such content answers 404.
- **Users are read through `/v2/users`**: the v1 user endpoints are deprecated.
  A user is addressed by its id or its email.
- **Rate limit: 120 requests per minute per company.** A 429 carries
  `X-AircallApi-Reset` (UNIX time of the reset): every method here is a GET, so
  it is retried once when the reset is close (`_RATE_LIMIT_MAX_WAIT`), and
  surfaces as `UpstreamHTTPError` otherwise.
"""
from __future__ import annotations

import base64
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Union
from urllib.parse import quote

import requests

from ..common import raise_for_upstream
from ..common.credentials import require

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait
BASE_URL = "https://api.aircall.io"

MIN_PER_PAGE, MAX_PER_PAGE = 1, 50
ORDERS = ("asc", "desc")
CONTACT_ORDER_BY = ("created_at", "updated_at")
DIRECTIONS = ("inbound", "outbound")
TRANSCRIPTION_MODES = ("async", "realtime")

# A 429 is retried once if the counter resets within this many seconds; a
# longer wait is the caller's call, not a hidden stall inside a request.
_RATE_LIMIT_MAX_WAIT = 15.0

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^\d+$")

Timestamp = Union[int, str, None]


def basic_signature(api_id: str, api_token: str) -> str:
    """`base64(api_id:api_token)` — the value that follows « Basic »."""
    return base64.b64encode(f"{api_id}:{api_token}".encode("utf-8")).decode("ascii")


def unix_seconds(value: Timestamp, name: str) -> Optional[int]:
    """A bound for `from`/`to`, as UNIX seconds.

    Accepts an int (UNIX seconds), a string of digits, an ISO 8601 date
    (`2026-09-01` = midnight UTC) or an ISO 8601 date-time WITH its offset
    (`2026-09-01T08:00:00+02:00`, `…Z`). A date-time without an offset is
    refused: the same wall-clock time names different instants depending on
    the zone.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"`{name}`: a boolean is not a date.")
    if isinstance(value, int):
        seconds = value
    elif isinstance(value, str) and _ID.match(value.strip()):
        seconds = int(value.strip())
    elif isinstance(value, str) and _DATE.match(value.strip()):
        d = date.fromisoformat(value.strip())
        seconds = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(
                f"`{name}` unreadable: {value!r}. Expected UNIX seconds, an ISO "
                "8601 date (2026-09-01) or a date-time with its offset "
                "(2026-09-01T08:00:00+02:00).") from None
        if parsed.tzinfo is None:
            raise ValueError(
                f"`{name}`: {value!r} has no offset. Add `Z` (UTC) or an offset "
                "such as `+02:00`.")
        seconds = int(parsed.timestamp())
    else:
        raise ValueError(f"`{name}`: unsupported type {type(value).__name__}.")
    if seconds < 0:
        raise ValueError(f"`{name}`: a UNIX timestamp cannot be negative.")
    if seconds > 10**11:
        raise ValueError(
            f"`{name}`: {seconds} looks like milliseconds; the API expects UNIX "
            "seconds.")
    return seconds


def _object_id(value: Any, name: str) -> str:
    """A numeric path id, refused locally when it is not one."""
    text = str(value).strip() if value is not None else ""
    if isinstance(value, bool) or not _ID.match(text):
        raise ValueError(f"`{name}` must be a numeric id; got {value!r}.")
    return text


def _choice(name: str, value: Optional[str], allowed: tuple) -> Optional[str]:
    if value is None:
        return None
    if value not in allowed:
        raise ValueError(f"`{name}` invalid: {value!r}. Accepted values: "
                         + ", ".join(repr(a) for a in allowed))
    return value


def _flag(value: Optional[bool]) -> Optional[str]:
    if value is None:
        return None
    return "true" if value else "false"


class AircallClient:
    """Aircall Public API, read-only. Basic auth `api_id:api_token`."""

    BASE_URL = BASE_URL

    def __init__(self, api_id: Optional[str] = None,
                 api_token: Optional[str] = None):
        """
        Args:
            api_id: Aircall API ID (Basic auth user name).
            api_token: Aircall API token (Basic auth password), shown once at
                creation.
        """
        self.api_id = require(api_id, "AIRCALL_API_ID")
        self.api_token = require(api_token, "AIRCALL_API_TOKEN")
        self.session = requests.Session()
        # Credentials in the HEADER only — never in the URL, which ends up in
        # exception messages, logs and error tracking.
        self.session.headers.update({
            "Authorization": f"Basic {basic_signature(self.api_id, self.api_token)}",
            "Accept": "application/json",
        })

    # --- transport ----------------------------------------------------------

    @staticmethod
    def _reset_wait(resp: Any) -> Optional[float]:
        """Seconds until the rate-limit counter resets, from `X-AircallApi-Reset`
        (a UNIX time). `None` when the header is missing or unreadable."""
        raw = (getattr(resp, "headers", None) or {}).get("X-AircallApi-Reset")
        try:
            return max(0.0, float(raw) - time.time())
        except (TypeError, ValueError):
            return None

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{self.BASE_URL}{path}"
        resp = self.session.request("GET", url, params=clean or None,
                                    timeout=_HTTP_TIMEOUT)
        if resp.status_code == 429:
            wait = self._reset_wait(resp)
            if wait is not None and wait <= _RATE_LIMIT_MAX_WAIT:
                time.sleep(wait)
                resp = self.session.request("GET", url, params=clean or None,
                                            timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="aircall")
        return resp.json() if resp.content else {}

    @staticmethod
    def _page(page: Optional[int], per_page: Optional[int]) -> Dict[str, Any]:
        if page is not None and (isinstance(page, bool) or not isinstance(page, int)
                                 or page < 1):
            raise ValueError(f"`page` must be an integer >= 1; got {page!r}.")
        if per_page is not None and (
                isinstance(per_page, bool) or not isinstance(per_page, int)
                or not MIN_PER_PAGE <= per_page <= MAX_PER_PAGE):
            raise ValueError(
                f"`per_page` must be between {MIN_PER_PAGE} and {MAX_PER_PAGE} "
                f"(Aircall API maximum); got {per_page!r}. Page with `page` "
                "beyond that.")
        return {"page": page, "per_page": per_page}

    def _list(self, path: str, *, page: Optional[int], per_page: Optional[int],
              date_from: Timestamp = None, date_to: Timestamp = None,
              order: Optional[str] = None, **extra: Any) -> Any:
        params = self._page(page, per_page)
        params.update({
            "from": unix_seconds(date_from, "date_from"),
            "to": unix_seconds(date_to, "date_to"),
            "order": _choice("order", order, ORDERS),
        })
        params.update(extra)
        return self._get(path, params)

    # --- account ------------------------------------------------------------

    def probe(self) -> Any:
        """GET /v1/ping — authenticates the credential, at the cost of one call."""
        return self._get("/v1/ping")

    def get_company(self) -> Any:
        """GET /v1/company — company name, users count, numbers count."""
        return self._get("/v1/company")

    # --- calls --------------------------------------------------------------

    def list_calls(self, *, date_from: Timestamp = None, date_to: Timestamp = None,
                   order: Optional[str] = None, page: Optional[int] = None,
                   per_page: Optional[int] = None,
                   fetch_contact: Optional[bool] = None,
                   fetch_short_urls: Optional[bool] = None) -> Any:
        """GET /v1/calls — calls of the company, bounded on their creation date.

        Six months of history; at most 10,000 calls through pagination.
        """
        return self._list("/v1/calls", page=page, per_page=per_page,
                          date_from=date_from, date_to=date_to, order=order,
                          fetch_contact=_flag(fetch_contact),
                          fetch_short_urls=_flag(fetch_short_urls))

    def search_calls(self, *, date_from: Timestamp = None,
                     date_to: Timestamp = None, order: Optional[str] = None,
                     direction: Optional[str] = None,
                     user_id: Optional[Any] = None,
                     phone_number: Optional[str] = None,
                     page: Optional[int] = None, per_page: Optional[int] = None,
                     fetch_contact: Optional[bool] = None,
                     fetch_short_urls: Optional[bool] = None) -> Any:
        """GET /v1/calls/search — calls filtered by direction, user or phone number.

        A call transferred from number A to number B is found by B, not by A.
        """
        return self._list(
            "/v1/calls/search", page=page, per_page=per_page,
            date_from=date_from, date_to=date_to, order=order,
            direction=_choice("direction", direction, DIRECTIONS),
            user_id=_object_id(user_id, "user_id") if user_id is not None else None,
            phone_number=phone_number or None,
            fetch_contact=_flag(fetch_contact),
            fetch_short_urls=_flag(fetch_short_urls))

    def get_call(self, call_id: Any, *, fetch_contact: Optional[bool] = None,
                 fetch_short_urls: Optional[bool] = None) -> Any:
        """GET /v1/calls/:id — one call, with its recording and voicemail URLs
        when present (valid one hour; short URLs three hours)."""
        return self._get(f"/v1/calls/{_object_id(call_id, 'call_id')}", {
            "fetch_contact": _flag(fetch_contact),
            "fetch_short_urls": _flag(fetch_short_urls)})

    # --- conversation intelligence -----------------------------------------

    def get_transcription(self, call_id: Any, *, mode: Optional[str] = None) -> Any:
        """GET /v1/calls/:id/transcription — utterances with speaker and offsets
        in seconds. `mode`: `async` or `realtime` (AI Assist Pro)."""
        return self._get(f"/v1/calls/{_object_id(call_id, 'call_id')}/transcription",
                         {"mode": _choice("mode", mode, TRANSCRIPTION_MODES)})

    def get_summary(self, call_id: Any) -> Any:
        """GET /v1/calls/:id/summary — the AI summary of the call."""
        return self._get(f"/v1/calls/{_object_id(call_id, 'call_id')}/summary")

    def get_topics(self, call_id: Any) -> Any:
        """GET /v1/calls/:id/topics — the key topics of the call."""
        return self._get(f"/v1/calls/{_object_id(call_id, 'call_id')}/topics")

    def get_sentiments(self, call_id: Any) -> Any:
        """GET /v1/calls/:id/sentiments — sentiment per participant."""
        return self._get(f"/v1/calls/{_object_id(call_id, 'call_id')}/sentiments")

    def get_action_items(self, call_id: Any) -> Any:
        """GET /v1/calls/:id/action_items — action items, AI-generated or
        written by an agent (`ai_generated`)."""
        return self._get(f"/v1/calls/{_object_id(call_id, 'call_id')}/action_items")

    # --- users, teams, numbers ---------------------------------------------

    def list_users(self, *, date_from: Timestamp = None, date_to: Timestamp = None,
                   order: Optional[str] = None, page: Optional[int] = None,
                   per_page: Optional[int] = None) -> Any:
        """GET /v2/users — users of the company, bounded on their creation date."""
        return self._list("/v2/users", page=page, per_page=per_page,
                          date_from=date_from, date_to=date_to, order=order)

    def get_user(self, user: Any) -> Any:
        """GET /v2/users/:id — one user, by numeric id or by email address."""
        text = str(user).strip() if user is not None else ""
        if "@" in text:
            ref = quote(text, safe="@")
        else:
            ref = _object_id(user, "user")
        return self._get(f"/v2/users/{ref}")

    def list_teams(self, *, order: Optional[str] = None, page: Optional[int] = None,
                   per_page: Optional[int] = None) -> Any:
        """GET /v1/teams — teams with their users."""
        return self._list("/v1/teams", page=page, per_page=per_page, order=order)

    def get_team(self, team_id: Any) -> Any:
        """GET /v1/teams/:id — one team with its users."""
        return self._get(f"/v1/teams/{_object_id(team_id, 'team_id')}")

    def list_numbers(self, *, date_from: Timestamp = None, date_to: Timestamp = None,
                     order: Optional[str] = None, page: Optional[int] = None,
                     per_page: Optional[int] = None) -> Any:
        """GET /v1/numbers — phone numbers (lines) of the company."""
        return self._list("/v1/numbers", page=page, per_page=per_page,
                          date_from=date_from, date_to=date_to, order=order)

    def get_number(self, number_id: Any) -> Any:
        """GET /v1/numbers/:id — one phone number with its users."""
        return self._get(f"/v1/numbers/{_object_id(number_id, 'number_id')}")

    # --- contacts -----------------------------------------------------------

    def list_contacts(self, *, date_from: Timestamp = None,
                      date_to: Timestamp = None, order: Optional[str] = None,
                      order_by: Optional[str] = None, page: Optional[int] = None,
                      per_page: Optional[int] = None) -> Any:
        """GET /v1/contacts — shared contacts. Contacts synced from third-party
        integrations are not served by the API. At most 10,000 through
        pagination."""
        return self._list("/v1/contacts", page=page, per_page=per_page,
                          date_from=date_from, date_to=date_to, order=order,
                          order_by=_choice("order_by", order_by, CONTACT_ORDER_BY))

    def search_contacts(self, *, phone_number: Optional[str] = None,
                        email: Optional[str] = None,
                        date_from: Timestamp = None, date_to: Timestamp = None,
                        order: Optional[str] = None, order_by: Optional[str] = None,
                        page: Optional[int] = None,
                        per_page: Optional[int] = None) -> Any:
        """GET /v1/contacts/search — shared contacts by phone number or email."""
        return self._list("/v1/contacts/search", page=page, per_page=per_page,
                          date_from=date_from, date_to=date_to, order=order,
                          order_by=_choice("order_by", order_by, CONTACT_ORDER_BY),
                          phone_number=phone_number or None, email=email or None)

    def get_contact(self, contact_id: Any) -> Any:
        """GET /v1/contacts/:id — one shared contact."""
        return self._get(f"/v1/contacts/{_object_id(contact_id, 'contact_id')}")


__all__ = [
    "AircallClient", "basic_signature", "unix_seconds", "BASE_URL",
    "MIN_PER_PAGE", "MAX_PER_PAGE", "ORDERS", "CONTACT_ORDER_BY", "DIRECTIONS",
    "TRANSCRIPTION_MODES",
]
