"""Affinity API client (https://api-docs.affinity.co for v1,
https://developer.affinity.co for v2) — relationship CRM: persons, companies,
opportunities, lists and list entries, field values, notes, interactions,
relationship strength.

One API key, sent as a Bearer token to both API generations on the same host
`https://api.affinity.co`: v1 paths carry no prefix (`/persons`), v2 paths start
with `/v2/`. The key is created per user (Settings → Manage Apps) and ACTS AS
that user: what it writes is attributed to them, and their sharing rules decide
what it sees. One method per endpoint; responses are returned as parsed JSON,
unchanged.

## Protocol facts that shape a caller

- **v2 is versioned by date.** Without `X-Affinity-Api-Version` a request runs
  at the version set as the app's default in Manage Apps. Every v2 call here pins
  `API_VERSION`; v1 is unversioned and never receives the header.
- **Which generation for what.** Reads use v2 (typed field values). Some writes
  exist in v1 only: create/update an organization, create an opportunity, edit
  a person's name or emails, remove a list entry, and per-entity interaction
  reads. Where v2 offers a write only as BETA (person create, list-entry add),
  the stable v1 endpoint is used.
- **v1 PUT REPLACES arrays.** `emails` / `organization_ids` on a person,
  `person_ids` on an organization or opportunity: what is sent becomes the whole
  set. Send the existing values too to add one.
- **v2 GET returns NO field data** unless `field_ids` or `field_types` is
  passed; list-specific fields are readable only through list entries.
- **Field values are typed** `{"type": <valueType>, "data": ...}` on write; a
  `data` of `None` clears the field. `field_value()` builds that shape from the
  field's `valueType`. Dropdowns take an option ID, never the option text.
- **A list entry ID is not an entity ID.** Removing an entry from an
  OPPORTUNITY list deletes the opportunity itself (v1 documentation).
- **Pagination.** v2: `limit` + `cursor`, the next cursor is carried by
  `pagination.nextUrl` (`next_cursor()` extracts it). v1: `page_size` +
  `page_token`, answered by `next_page_token`.
- **Rate limits.** 900 requests per user per minute, and a monthly account quota
  (100k on Scale/Advanced, none on Enterprise) shared by v1, v2 and Affinity's
  MCP server. A 429 on a GET is retried once when the per-user counter resets
  within `_RATE_LIMIT_MAX_WAIT` seconds; writes are never replayed. The last
  seen counters are kept in `last_rate_limit`.
- **Errors.** v2 answers `{"errors": [{"code", "message"}]}`; v1 answers plain
  text (`Unauthorized API Key.`), so `UpstreamHTTPError.body` may be a string.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable, Optional

import requests

from ..common import raise_for_upstream
from ..common.credentials import require
from ._api import _ActivityMixin, _EntitiesMixin, _ListsMixin
from .values import (ENTITY_KINDS, FIELD_TYPES, INTERACTION_DIRECTIONS, INTERACTION_TYPES,
                     MAX_FIELD_UPDATES, NOTE_ALLOWED_TAGS, WRITABLE_VALUE_TYPES, _choice,
                     field_value, next_cursor, note_html)

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait
BASE_URL = "https://api.affinity.co"
API_VERSION = "2026-09-17"

# A 429 is retried once if the per-user counter resets within this many
# seconds; a longer wait is the caller's call, not a hidden stall.
_RATE_LIMIT_MAX_WAIT = 15.0

# --- client -------------------------------------------------------------------

class AffinityClient(_EntitiesMixin, _ListsMixin, _ActivityMixin):
    """Affinity API v1 + v2, one Bearer API key."""

    BASE_URL = BASE_URL
    API_VERSION = API_VERSION

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        """
        Args:
            api_key: Affinity API key (Settings → Manage Apps). Acts as its owner.
            base_url: host override (a mock server in tests); defaults to
                `https://api.affinity.co`.
        """
        self.api_key = require(api_key, "AFFINITY_API_KEY")
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.session = requests.Session()
        # Key in the HEADER only — never in the URL, which ends up in exception
        # messages, logs and error tracking.
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })
        self.last_rate_limit: Dict[str, int] = {}

    # --- transport ----------------------------------------------------------

    def _remember_rate_limit(self, resp: Any) -> None:
        headers = getattr(resp, "headers", None) or {}
        seen = {}
        for key, name in (("x-ratelimit-limit-user-remaining", "user_remaining"),
                          ("x-ratelimit-limit-org-remaining", "org_remaining"),
                          ("x-ratelimit-limit-org", "org_limit")):
            raw = headers.get(key)
            try:
                seen[name] = int(raw)
            except (TypeError, ValueError):
                continue
        if seen:
            self.last_rate_limit = seen

    @staticmethod
    def _reset_wait(resp: Any) -> Optional[float]:
        raw = (getattr(resp, "headers", None) or {}).get("x-ratelimit-limit-user-reset")
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return None

    def _request(self, method: str, path: str, *, v2: bool,
                 params: Optional[Dict[str, Any]] = None,
                 json: Any = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{self.base_url}{path}"
        headers = {"X-Affinity-Api-Version": self.API_VERSION} if v2 else None
        kwargs: Dict[str, Any] = {"params": clean or None, "headers": headers}
        if json is not None:
            kwargs["json"] = json
        resp = self.session.request(method, url, timeout=_HTTP_TIMEOUT, **kwargs)
        if resp.status_code == 429 and method == "GET":
            wait = self._reset_wait(resp)
            if wait is not None and wait <= _RATE_LIMIT_MAX_WAIT:
                time.sleep(wait)
                resp = self.session.request(method, url, timeout=_HTTP_TIMEOUT, **kwargs)
        self._remember_rate_limit(resp)
        raise_for_upstream(resp, service="affinity")
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def _v1(self, method: str, path: str, **kw: Any) -> Any:
        return self._request(method, path, v2=False, **kw)

    def _v2(self, method: str, path: str, **kw: Any) -> Any:
        return self._request(method, f"/v2{path}", v2=True, **kw)

    @staticmethod
    def _fields(field_ids: Optional[Iterable[str]],
                field_types: Optional[Iterable[str]]) -> Dict[str, Any]:
        types = list(field_types) if field_types else None
        for t in types or ():
            _choice("field_types", t, FIELD_TYPES)
        return {"fieldIds": list(field_ids) if field_ids else None, "fieldTypes": types}

    @staticmethod
    def _plural(kind: str) -> str:
        return {"person": "persons", "company": "companies",
                "opportunity": "opportunities"}[_choice("kind", kind, ENTITY_KINDS)]

    # --- account ------------------------------------------------------------

    def whoami(self) -> Any:
        """GET /v2/auth/whoami — tenant, user and grant (type, scopes) of the key."""
        return self._v2("GET", "/auth/whoami")

    def whoami_v1(self) -> Any:
        """GET /auth/whoami (v1) — the same identity through v1, which most writes
        use. Exempt from the monthly account quota."""
        return self._v1("GET", "/auth/whoami")

    def rate_limit(self) -> Any:
        """GET /v2/rate-limit — per-user and per-account usage and limits."""
        return self._v2("GET", "/rate-limit")


__all__ = [
    "AffinityClient", "BASE_URL", "API_VERSION", "ENTITY_KINDS", "FIELD_TYPES",
    "INTERACTION_TYPES", "INTERACTION_DIRECTIONS", "WRITABLE_VALUE_TYPES",
    "NOTE_ALLOWED_TAGS", "MAX_FIELD_UPDATES", "field_value", "note_html", "next_cursor",
]
