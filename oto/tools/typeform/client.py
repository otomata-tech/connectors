"""Typeform API client — workspaces, forms, responses, webhooks.

Personal access token (`tfp_…`) sent as `Authorization: Bearer <token>`. One
method = one endpoint, except `summarize_responses`, which reads a form and
pages of its responses and aggregates them here, deterministically. Responses
are returned as-is, except that a webhook's signing `secret` is never
returned. Paths, parameters and shapes follow the public reference
(https://www.typeform.com/developers/create/reference/,
https://www.typeform.com/developers/responses/reference/retrieve-responses/ and
https://www.typeform.com/developers/webhooks/).

Not covered: images, themes, workspaces management, translations, custom
messages, file downloads.

What the caller needs to know, and cannot guess:

- **Three API hosts, one per data center, chosen at construction** (`region`):
  `us` → `api.typeform.com`, `eu` → `api.eu.typeform.com`, `eu2` →
  `api.typeform.eu` (the newer EU data center). Workspaces and forms answer the
  same on `us` and `eu`; ⚠️ **responses do not**: queried in a region other
  than the account's, `list_responses` returns an EMPTY collection, not an
  error. A form's own `_links.responses` (from `get_form`) carries the right
  host. Tokens of the `eu2` data center are distinct from the other two.
- **The token's scopes gate each call**: `workspaces:read` for
  `list_workspaces`; `forms:read` for `list_forms`/`get_form`; `forms:write`
  for `create_form`/`replace_form`/`update_form`/`delete_form`;
  `responses:read` for `list_responses`; `responses:write` for
  `delete_responses`; `webhooks:read` for `list_webhooks`/`get_webhook`;
  `webhooks:write` for `upsert_webhook`/`delete_webhook`. A token without it
  is refused.
- **Destructive calls**: `replace_form` overwrites the whole form (a field left
  out is deleted with its answers), `delete_form` deletes the form and all its
  responses, `delete_responses` deletes responses (asynchronously),
  `delete_webhook` cuts an integration. None can be undone.
- **A created form is public by default** (`settings.is_public` defaults to
  true upstream). `update_form` publishes or unpublishes one by JSON Patch on
  `/settings/is_public`.
- **Responses: default page 25, maximum 1000.** Paging is by cursor: pass the
  `token` of the last item of a page as `before` (default sort is newest
  first) to get the next one; `after` walks the other way. Both bounds are
  exclusive. `since`/`until` filter by date (inclusive; ISO 8601 UTC to the
  second, e.g. `2020-03-20T14:00:59`, or a Unix timestamp in seconds).
- **`response_type` decides which timestamp `since`/`until` filter on**:
  `completed` (the default) → `submitted_at`, `partial` → `staged_at`,
  `started` → `landed_at`. The deprecated `completed` boolean is not exposed.
- Responses submitted in the last ~30 minutes may not be returned yet.
- **Forms and workspaces lists: default page 10, maximum 200**, page-numbered
  (`page`, 1-based).
- Comma-separated list parameters (`included_response_ids`, `fields`, …) take
  a Python list (joined once; an item containing a comma is refused) or an
  already-joined string.

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from ..common import raise_for_upstream
from ..common.credentials import require
from ._api import _FormsMixin, _ResponsesMixin, _WebhooksMixin
from .params import ListParam, _clean, _csv, _segment

__all__ = ["REGIONS", "TypeformClient", "ListParam", "_clean", "_csv", "_segment"]

#: (connect, read) — never an unbounded wait.
_HTTP_TIMEOUT = (10, 60)

#: Data center → API host.
REGIONS: Dict[str, str] = {
    "us": "https://api.typeform.com",
    "eu": "https://api.eu.typeform.com",
    "eu2": "https://api.typeform.eu",
}


class TypeformClient(_FormsMixin, _ResponsesMixin, _WebhooksMixin):
    """Typeform API client, Bearer personal access token."""

    def __init__(self, access_token: Optional[str] = None, *, region: str = "us"):
        """
        Args:
            access_token: personal access token (Typeform: Account → Personal
                tokens), with the scopes the calls need.
            region: data center of the account — `us` (default), `eu` or
                `eu2` (see the module docstring).
        """
        self.access_token = require(access_token, "TYPEFORM_ACCESS_TOKEN")
        if region not in REGIONS:
            raise ValueError(f"Unknown Typeform region {region!r} — expected one of "
                             f"{', '.join(REGIONS)}.")
        self.region = region
        self.BASE_URL = REGIONS[region]
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.access_token}"
        self.session.headers["Accept"] = "application/json"

    def _get(self, path: str, **params: Any) -> Any:
        resp = self.session.request("GET", f"{self.BASE_URL}{path}",
                                    params=_clean(params), timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="typeform")
        return resp.json()

    def _send(self, method: str, path: str, *, json: Any = None) -> Any:
        """A write: JSON body when given; `None` back for an empty answer
        (204, or a 200 that only acknowledges)."""
        body: Dict[str, Any] = {} if json is None else {"json": json}
        resp = self.session.request(method, f"{self.BASE_URL}{path}",
                                    timeout=_HTTP_TIMEOUT, **body)
        raise_for_upstream(resp, service="typeform")
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()
