"""Leexi API client — conversation intelligence (calls, transcripts, notes).

API v1 (`https://public-api.leexi.ai/v1`, docs https://docs.public-api.leexi.ai),
auth **HTTP Basic** `base64(KEY_ID:KEY_SECRET)`. One method = one endpoint;
bodies and responses pass through as-is, the client invents no semantics.
Paths, verbs, parameters and scopes were collected page by page from the
vendor reference (OpenAPI embedded in each page) on 2026-09-02.

This module carries the **construction and the transport**, and composes the
call families of `_api/` (users, teams, calls, notes, meetings). The
constants live in `const.py` and are re-exported here: the backend pins
oto-core by tag and only imports `oto.tools.leexi.client`.

Four things constrain the caller, and none of them can be guessed:

- ⚠️ **Multi-valued list parameters are written `name[]=a&name[]=b`** (Rails).
  This is THE connector's trap: `requests` serializes `{"owner_uuid": ["a", "b"]}`
  as `owner_uuid=a&owner_uuid=b`, which Rails reads as a SCALAR and reduces to the
  LAST value — the filter goes out, upstream answers 200, and the response is that
  of a filter on `b` alone. A silently narrowed filter is worse than a refusal:
  the caller believes they listed the calls of two owners. `ARRAY_PARAMS`
  names the parameters concerned and `_encode_params` adds the suffix to them,
  once, at transport. The vendor docs write all of them with their brackets
  (« `source_id[]=abc&source_id[]=xyz` »).

- **Two scopes, not to be confused.** The *call access scope* is attached to the
  key (the whole company / a user's access / access rules) and
  decides which CALLS the key sees: out of scope, a call is not listed and
  answers **404** when fetched directly — so a 404 on `get_call` does not mean "does not
  exist". The *permission scopes* (`read_calls`, `write_users`…) decide which
  ENDPOINTS the key reaches: without the scope, it is a **403**. Both are
  configured on the Leexi admin side, never through this API.

- ⚠️ **A new key carries ONLY `read_calls`.** Everything else — and namely
  `write_users` / `write_teams`, **which commit billed licenses** — must be
  granted explicitly by an admin. Hence the choice of probe: `probe()`
  queries `/calls`, the only call a default key can honor. Probing
  `/users` would make a healthy key pass for a dead one (403 ≠ 401).

- **Pagination `page`/`items`, `items` is capped at 100** (default 10). `_check_items`
  refuses out-of-bounds values locally rather than letting a 400 go out.

The license writes (`create_user`, `update_user`, `deactivate_user`) and the
structure writes (`create_team`, `update_team`, `delete_team`) are exposed: they are
within the scope decided for this connector. The safeguard is not here — it is the
key's scope, which a Leexi admin grants or not. This client cannot
get around that notch, and does not try.

⚠️ **Nothing is ever permanently deleted on the user side**: `deactivate_user`
is a `DELETE /users/{uuid}` that *deactivates* (calls and history stay,
sessions drop, the license is freed). The method name states the real effect,
not the HTTP verb.

**Lifecycle of an imported call**: `presign_recording_url(extension)` → PUT of the file
to the returned URL, **with the returned headers** (`upload_recording` does this) → then
`create_call(recording_s3_key=…)`. The uploaded file expires after 3 days
if it is not used to create a call. Creation is **asynchronous** (a few minutes)
and the prompt completions (summary, chaptering) arrive AFTER.

Upstream rate limits: 50 requests/minute, and **10/minute for call
creation**. A 429 is retried honoring `Retry-After` — **read-only**
(see `_request`: the API offers no idempotency key).

Requires: requests
"""
from __future__ import annotations

import base64
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from ..common.credentials import require
from ..common import raise_for_upstream
from ._api import (_CallsMixin, _MeetingsMixin, _NotesMixin, _TeamsMixin,
                   _UsersMixin)
from .const import (ARRAY_PARAMS, CALL_DATE_FILTERS, CALL_ORDERS, DEFAULT_ITEMS,
                    HTTP_TIMEOUT, MAX_ATTEMPTS, MAX_ITEMS, MEETING_DATE_FILTERS,
                    MEETING_ORDERS, MEETING_ORIGINS, MIN_ITEMS, RETRY_STATUSES)


def basic_signature(key_id: str, key_secret: str) -> str:
    """`base64(KEY_ID:KEY_SECRET)` — the value that follows « Basic ».

    Written here so that a caller (a connection probe, a test) can
    build it without copying the encoding, and so that only one version exists.
    """
    raw = f"{key_id}:{key_secret}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


class LeexiClient(
    _UsersMixin,
    _TeamsMixin,
    _CallsMixin,
    _NotesMixin,
    _MeetingsMixin,
):
    """Leexi v1 client (https://public-api.leexi.ai/v1), Basic auth KEY_ID:KEY_SECRET."""

    BASE_URL = "https://public-api.leexi.ai/v1"

    #: The least demanding call of the API: `read_calls` is the ONLY scope of a
    #: new key. Every authentication probe goes through it — `/users` would require
    #: `read_users`, which an admin has not necessarily granted, and its 403 would be read
    #: wrongly as « the key is bad ».
    PROBE_PATH = "/calls"

    def __init__(self, key_id: Optional[str] = None,
                 key_secret: Optional[str] = None):
        """
        Args:
            key_id: Leexi key identifier.
            key_secret: Leexi key secret.

        Both are generated in Leexi → Settings → Company Settings → API Keys
        (admin account required).
        """
        self.key_id = require(key_id, "LEEXI_KEY_ID")
        self.key_secret = require(key_secret, "LEEXI_KEY_SECRET")
        self.session = requests.Session()
        # Signature in the HEADER only (never in the query string: it would end up
        # in the URL, hence in the message of any exception, the logs and Sentry).
        self.session.headers.update({
            "Authorization": f"Basic {basic_signature(self.key_id, self.key_secret)}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # --- transport ----------------------------------------------------------

    @staticmethod
    def _check_items(items: Optional[int]) -> None:
        """`items` outside [1, 100] is refused HERE. The API would return a 400; saying so
        locally names the real bound, which nobody can guess."""
        if items is None:
            return
        if not isinstance(items, int) or isinstance(items, bool):
            raise ValueError("`items` must be an integer.")
        if not (MIN_ITEMS <= items <= MAX_ITEMS):
            raise ValueError(
                f"`items` must be between {MIN_ITEMS} and {MAX_ITEMS} "
                f"(Leexi API cap); got {items}. "
                "Beyond that, paginate with `page`.")

    @staticmethod
    def _check_choice(name: str, value: Optional[str],
                      allowed: Iterable[str]) -> None:
        """Refuse a value outside the enumeration locally, NAMING the valid ones —
        upstream returns a 400 that does not list them."""
        if value is None:
            return
        allowed = tuple(allowed)
        if value not in allowed:
            raise ValueError(
                f"`{name}` invalid: {value!r}. Accepted values: "
                + ", ".join(repr(a) for a in allowed))

    @staticmethod
    def _encode_params(params: Optional[Dict[str, Any]]) -> List[Tuple[str, Any]]:
        """Params → flat list of pairs, ready for `requests`.

        Three rules, all imposed by upstream (Rails):

        - `None` is dropped (an absent parameter ≠ an empty parameter);
        - a value of `ARRAY_PARAMS` is repeated under `name[]` — this is the
          fix described at the top of the module, and the reason this
          function exists;
        - a boolean goes out as `true`/`false` (requests would write `True`, which the
          server does not read as a boolean).

        A list passed to a SCALAR parameter is flatly refused: letting it
        through would produce exactly the silent narrowing being fixed.
        """
        out: List[Tuple[str, Any]] = []
        for key, value in (params or {}).items():
            if value is None:
                continue
            if key in ARRAY_PARAMS:
                values = value if isinstance(value, (list, tuple, set)) else [value]
                out.extend((f"{key}[]", v) for v in values if v is not None)
            elif isinstance(value, bool):
                out.append((key, "true" if value else "false"))
            elif isinstance(value, (list, tuple, set)):
                raise ValueError(
                    f"`{key}` does not accept multiple values. Multi-valued: "
                    + ", ".join(sorted(ARRAY_PARAMS)))
            else:
                out.append((key, value))
        return out

    @staticmethod
    def _retry_after(resp: Any, attempt: int) -> float:
        """Delay before retrying: `Retry-After` if present (upstream knows better
        than we do — 50 req/min, 10/min on creation), otherwise exponential backoff."""
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
        # Retry 429/5xx on READ only: the API offers NO idempotency
        # key, so replaying a POST would create a duplicate — one more call,
        # or one more billed user. A write that gets a 429
        # bubbles up as-is, for the caller to decide.
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
        raise_for_upstream(last, service="leexi")
        return last.json() if last.content else {}

    def _list(self, path: str, page: Optional[int], items: Optional[int],
              extra: Optional[Dict[str, Any]] = None) -> Any:
        self._check_items(items)
        params: Dict[str, Any] = {"page": page, "items": items}
        params.update(extra or {})
        return self._request("GET", path, params=params)

    # --- probe --------------------------------------------------------------

    def probe(self) -> Dict[str, Any]:
        """Check that the key authenticates, at the cost of a single call.

        `GET /calls?items=1`. A **401** says the KEY_ID/KEY_SECRET pair is
        bad; a **402** that the Leexi subscription is inactive; a **403** that
        the key does not even have `read_calls` (it exists, but an admin removed it
        from it). An empty list is NOT a failure: it is a key whose *call
        access scope* covers no call, which is a valid setting.
        """
        return self._request("GET", self.PROBE_PATH, params={"items": 1})


__all__ = [
    "LeexiClient", "basic_signature",
    "MIN_ITEMS", "MAX_ITEMS", "DEFAULT_ITEMS", "ARRAY_PARAMS",
    "HTTP_TIMEOUT", "RETRY_STATUSES", "MAX_ATTEMPTS",
    "CALL_ORDERS", "CALL_DATE_FILTERS",
    "MEETING_ORDERS", "MEETING_DATE_FILTERS", "MEETING_ORIGINS",
]
