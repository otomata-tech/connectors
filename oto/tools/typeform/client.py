"""Typeform API client — READ ONLY: workspaces, forms, form definitions, responses.

Personal access token (`tfp_…`) sent as `Authorization: Bearer <token>`. One
method = one endpoint; responses are returned as-is, the client invents no
semantics. Paths, parameters and shapes follow the public reference
(https://www.typeform.com/developers/create/reference/ and
https://www.typeform.com/developers/responses/reference/retrieve-responses/).

Scope: reading only. Creating, updating or deleting forms, deleting responses,
webhooks, images, themes and translations are not covered here.

What the caller needs to know, and cannot guess:

- **Three API hosts, one per data center, chosen at construction** (`region`):
  `us` → `api.typeform.com`, `eu` → `api.eu.typeform.com`, `eu2` →
  `api.typeform.eu` (the newer EU data center). Workspaces and forms answer the
  same on `us` and `eu`; ⚠️ **responses do not**: queried in a region other
  than the account's, `list_responses` returns an EMPTY collection, not an
  error. A form's own `_links.responses` (from `get_form`) carries the right
  host. Tokens of the `eu2` data center are distinct from the other two.
- **The token's scopes gate each call**: `workspaces:read` for
  `list_workspaces`, `forms:read` for `list_forms`/`get_form`,
  `responses:read` for `list_responses`; a token without it is refused.
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

from typing import Any, Dict, Iterable, Optional, Union
from urllib.parse import quote

import requests

from ..common import raise_for_upstream
from ..common.credentials import require

#: (connect, read) — never an unbounded wait.
_HTTP_TIMEOUT = (10, 60)

#: Data center → API host.
REGIONS: Dict[str, str] = {
    "us": "https://api.typeform.com",
    "eu": "https://api.eu.typeform.com",
    "eu2": "https://api.typeform.eu",
}

ListParam = Union[str, Iterable[str], None]


def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
    """Drops `None` values — an omitted kwarg must not become the literal
    string 'None' in the querystring."""
    return {k: v for k, v in params.items() if v is not None}


def _csv(name: str, value: ListParam) -> Optional[str]:
    """A list parameter as the single comma-separated string Typeform expects.
    A string is taken as already joined; an item of a list containing a comma
    is refused (it would be split in two upstream)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    items = list(value)
    for item in items:
        if not isinstance(item, str) or not item:
            raise ValueError(f"`{name}`: every item must be a non-empty string.")
        if "," in item:
            raise ValueError(f"`{name}`: item {item!r} contains a comma — it would "
                             "be split in two.")
    return ",".join(items) or None


def _segment(name: str, value: str) -> str:
    """An id placed in a path: escaped, and never `.`/`..` (which `quote` leaves
    intact and which would change the path)."""
    if not isinstance(value, str) or not value.strip() or value in (".", ".."):
        raise ValueError(f"`{name}` must be a non-empty identifier.")
    return quote(value, safe="")


class TypeformClient:
    """Typeform API client, read only, Bearer personal access token."""

    def __init__(self, access_token: Optional[str] = None, *, region: str = "us"):
        """
        Args:
            access_token: personal access token (Typeform: Account → Personal
                tokens), with the read scopes the calls need.
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

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    def list_workspaces(self, *, search: Optional[str] = None,
                        page: Optional[int] = None,
                        page_size: Optional[int] = None) -> Any:
        """GET /workspaces — every workspace the token can access, across
        organizations. `{total_items, page_count, items: [{id, name,
        account_id, shared, forms: {count, href}, self: {href}}]}`.

        Args:
            search: only workspaces containing this string.
            page: 1-based page number (default 1).
            page_size: default 10, maximum 200.
        """
        return self._get("/workspaces", search=search, page=page, page_size=page_size)

    # ------------------------------------------------------------------
    # Forms
    # ------------------------------------------------------------------

    def list_forms(self, *, search: Optional[str] = None,
                   page: Optional[int] = None,
                   page_size: Optional[int] = None,
                   workspace_id: Optional[str] = None,
                   sort_by: Optional[str] = None,
                   order_by: Optional[str] = None,
                   is_public: Optional[bool] = None) -> Any:
        """GET /forms — forms of the account, public and private.
        `{total_items, page_count, items: [{id, title, created_at,
        last_updated_at, settings: {is_public}, self, theme, _links: {display,
        responses}}]}`.

        Args:
            search: only forms containing this string.
            page: 1-based page number (default 1).
            page_size: default 10, maximum 200.
            workspace_id: only the forms of this workspace.
            sort_by: `created_at` | `last_updated_at`.
            order_by: `asc` | `desc`.
            is_public: filter on `settings.is_public`.
        """
        return self._get("/forms", search=search, page=page, page_size=page_size,
                         workspace_id=workspace_id, sort_by=sort_by,
                         order_by=order_by,
                         is_public=None if is_public is None else str(is_public).lower())

    def get_form(self, form_id: str) -> Any:
        """GET /forms/{form_id} — the full form definition: `title`, `fields`
        (each `{id, ref, title, type, properties: {description, choices:
        [{id, ref, label}], fields: […] for group/matrix, …}, validations}`),
        `hidden`, `variables`, `logic`, screens, `settings`, `_links: {display,
        responses}`. A response's `answers[].field.id`/`ref` point to these
        fields.

        Args:
            form_id: the form id (the last path segment of the form's public
                URL, e.g. `u6nXL7` in `…typeform.com/to/u6nXL7`).
        """
        return self._get(f"/forms/{_segment('form_id', form_id)}")

    # ------------------------------------------------------------------
    # Responses
    # ------------------------------------------------------------------

    def list_responses(self, form_id: str, *,
                       page_size: Optional[int] = None,
                       since: Optional[Union[str, int]] = None,
                       until: Optional[Union[str, int]] = None,
                       after: Optional[str] = None,
                       before: Optional[str] = None,
                       included_response_ids: ListParam = None,
                       excluded_response_ids: ListParam = None,
                       response_type: ListParam = None,
                       sort: Optional[str] = None,
                       query: Optional[str] = None,
                       fields: ListParam = None,
                       answered_fields: ListParam = None) -> Any:
        """GET /forms/{form_id}/responses — `{total_items, page_count, items:
        [{response_id, token, landing_id, landed_at, submitted_at, metadata,
        hidden, calculated: {score}, variables, answers: [{field: {id, type,
        ref}, type, <type>: value}]}]}`. An answer's value sits under the key
        named by its `type`: `text`, `choice` (`{label}`), `choices`
        (`{labels}`), `number`, `boolean`, `email`, `url`, `file_url`, `date`,
        `payment`, `signature` (`{url}`), `multi_format`. `answers` are in no
        particular order: match them to the form's fields by `field.id`.

        Args:
            form_id: the form id.
            page_size: default 25, maximum 1000.
            since: inclusive lower bound, ISO 8601 UTC or Unix seconds.
            until: inclusive upper bound, same formats.
            after: cursor — responses after this response `token` (exclusive).
            before: cursor — responses before this response `token`
                (exclusive); with the default newest-first sort, the last
                item's `token` here gives the next page.
            included_response_ids: only these `response_id`s.
            excluded_response_ids: all but these `response_id`s.
            response_type: `completed` (default upstream) | `partial` |
                `started`, one or several — also picks the timestamp
                `since`/`until` filter on.
            sort: `<field>,<asc|desc>`, e.g. `submitted_at,desc` (default for
                completed responses).
            query: exact phrase searched in answers, hidden fields and
                variables.
            fields: field ids — only these appear in `answers`.
            answered_fields: field ids — only responses answering at least one.
        """
        return self._get(
            f"/forms/{_segment('form_id', form_id)}/responses",
            page_size=page_size, since=since, until=until, after=after,
            before=before,
            included_response_ids=_csv("included_response_ids", included_response_ids),
            excluded_response_ids=_csv("excluded_response_ids", excluded_response_ids),
            response_type=_csv("response_type", response_type),
            sort=sort, query=query,
            fields=_csv("fields", fields),
            answered_fields=_csv("answered_fields", answered_fields))
