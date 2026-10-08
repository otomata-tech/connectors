"""Google Ads API client (REST) — READ-ONLY, with an OAuth2 user access token.

Three reads on `googleads.googleapis.com`, the surface of Google's own MCP server
(`googleads/google-ads-mcp`):

- `customers:listAccessibleCustomers` — the accounts the Google user opens directly;
- `customers/{id}/googleAds:search` — one GAQL query (Google Ads Query Language);
- `googleAdsFields:search` — the field catalog, to describe a resource.

**Read-only is enforced HERE, not by Google**: `adwords` is the only scope Google
publishes and it would allow mutating. This client builds no other URL than the
three above — no `:mutate`, no `:upload` — and refuses a query that is not a GAQL
`SELECT … FROM …` before sending it.

**No developer token.** Google sunset it on 2026-09-09: the header is ignored, and the
access level (Test / Explorer / Basic / Standard) is that of the Google Cloud project
that owns the OAuth client which issued the token. A project on Test access is
refused on real accounts (`CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION`).

**Manager accounts**: a user who reaches an account through a manager (MCC) passes
the manager's id as `login_customer_id` (header `login-customer-id`).

**Typed refusal**: `GoogleAdsError` carries Google's canonical `status` and every
`(family, code, message)` of the `GoogleAdsFailure` details — branch on the codes,
never on the text.

Scope: `adwords`. Requires: requests.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import requests

from ...common import UpstreamHTTPError
from ...common.credentials import require

SCOPES = ["https://www.googleapis.com/auth/adwords"]

#: Latest major of the Google Ads API (v25, 2026-07-22). A major is sunset about a
#: year after its release: bumping it is a deliberate act, release notes read.
API_VERSION = "v25"
_BASE = f"https://googleads.googleapis.com/{API_VERSION}"

_HTTP_TIMEOUT = (10, 60)
#: `googleAdsFields:search` pages: a resource has a few hundred selectable fields.
_MAX_FIELD_PAGES = 5

_CUSTOMER_ID = re.compile(r"^\d{10}$")
_RESOURCE = re.compile(r"^[a-z][a-z0-9_]*$")
_GAQL = re.compile(r"^\s*SELECT\s.+?\sFROM\s+[a-z][a-z0-9_]*\b", re.IGNORECASE | re.DOTALL)
_FIELD_SELECT = "SELECT name, category, selectable, filterable, sortable"


class GoogleAdsError(UpstreamHTTPError):
    """Refusal from the Google Ads API.

    `status` = Google's canonical status (`INVALID_ARGUMENT`, `PERMISSION_DENIED`…),
    `errors` = the `(family, code, message)` of each failure detail — e.g.
    `("authorizationError", "USER_PERMISSION_DENIED", "…")`; an `ErrorInfo` reason
    (`SERVICE_DISABLED`…) comes as `("errorInfo", reason, "")` —, `message` = Google's
    text, `request_id` = Google's request id when given."""

    def __init__(self, status_code: int, body: Any):
        err = (body.get("error") if isinstance(body, dict) else None) or {}
        self.status: str = err.get("status") or ""
        self.errors: list[tuple[str, str, str]] = []
        self.request_id: Optional[str] = None
        for detail in err.get("details") or []:
            self.request_id = self.request_id or detail.get("requestId")
            for e in detail.get("errors") or []:
                for family, code in (e.get("errorCode") or {}).items():
                    self.errors.append((family, str(code), e.get("message") or ""))
            if detail.get("reason"):
                self.errors.append(("errorInfo", detail["reason"], ""))
        text = body if isinstance(body, str) else ""
        self.message: str = (err.get("message") or text[:300]).strip()
        super().__init__(status_code, self.detail, service="google_ads")

    @property
    def codes(self) -> set[str]:
        return {code for _, code, _ in self.errors}

    @property
    def families(self) -> set[str]:
        return {family for family, _, _ in self.errors}

    @property
    def detail(self) -> str:
        """The per-error messages (they name the offending field), else Google's text."""
        return "; ".join(m for _, _, m in self.errors if m) or self.message


def customer_id(value: Any) -> str:
    """`123-456-7890` or `1234567890` → `1234567890`; anything else → `ValueError`."""
    v = re.sub(r"[\s-]", "", str(value if value is not None else ""))
    if not _CUSTOMER_ID.fullmatch(v):
        raise ValueError(f"A Google Ads customer id has 10 digits (e.g. 123-456-7890), "
                         f"got {value!r}.")
    return v


def check_select(query: str) -> str:
    """The query, stripped, if it is a GAQL `SELECT <fields> FROM <resource> …` —
    otherwise `ValueError` (read-only: nothing else is ever sent)."""
    if not isinstance(query, str) or not _GAQL.match(query):
        raise ValueError("Read-only: a Google Ads query must be a GAQL "
                         "`SELECT <fields> FROM <resource> …` query. Nothing was sent.")
    return query.strip()


def flatten_rows(page: dict) -> tuple[list[str], list[list]]:
    """A `googleAds:search` page as a table: the `fieldMask` columns, one list per row.

    Google names fields in camelCase in both (`metrics.costMicros`); a field it
    leaves out of a row is `None`; 64-bit numbers stay strings, as sent."""
    results = page.get("results") or []
    mask = page.get("fieldMask")
    if results and not mask:
        raise ValueError("Google Ads answered rows without their field list "
                         "(`fieldMask`): the response cannot be read reliably.")
    columns = [c for c in (mask or "").split(",") if c]
    return columns, [[_value(r, c) for c in columns] for r in results]


def _value(row: dict, path: str) -> Any:
    cur: Any = row
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


class GoogleAdsClient:
    """Read-only Google Ads client for ONE OAuth2 access token (scope `adwords`)."""

    def __init__(self, access_token: str, *, session: Optional[requests.Session] = None,
                 timeout: tuple[float, float] = _HTTP_TIMEOUT):
        self._token = require(access_token, "GOOGLE_ADS_ACCESS_TOKEN")
        self._session = session or requests.Session()
        self._timeout = timeout

    def _call(self, method: str, path: str, *, body: Optional[dict] = None,
              login_customer_id: Optional[str] = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token}"}
        if login_customer_id:
            headers["login-customer-id"] = customer_id(login_customer_id)
        resp = self._session.request(method, f"{_BASE}/{path}", headers=headers,
                                     json=body, timeout=self._timeout)
        if resp.status_code >= 400:
            try:
                payload: Any = resp.json()
            except ValueError:
                payload = resp.text
            raise GoogleAdsError(resp.status_code, payload)
        return resp.json()

    def list_accessible_customers(self) -> list[str]:
        """The 10-digit ids of the accounts the user opens DIRECTLY (a manager's
        client accounts are read with a `customer_client` query on the manager)."""
        data = self._call("GET", "customers:listAccessibleCustomers")
        return [rn.removeprefix("customers/") for rn in data.get("resourceNames") or []]

    def search(self, customer: Any, query: str, *, page_token: Optional[str] = None,
               login_customer_id: Optional[str] = None) -> dict:
        """One page (10,000 rows, fixed by Google) of a GAQL query — Google's raw
        answer: `results`, `fieldMask`, `nextPageToken`?. Rows as a table:
        `flatten_rows`. Page tokens expire after ~2 h.

        The body is the query and the page token, nothing else: v25 refuses an
        unknown field (`returnTotalResultsCount` included) as an invalid payload."""
        body: dict = {"query": check_select(query)}
        if page_token:
            body["pageToken"] = page_token
        return self._call("POST", f"customers/{customer_id(customer)}/googleAds:search",
                          body=body, login_customer_id=login_customer_id)

    def search_fields(self, query: str) -> list[dict]:
        """Every row of a `googleAdsFields:search` query, following its pages
        (bounded: beyond `_MAX_FIELD_PAGES`, upstream is looping — `RuntimeError`)."""
        out: list[dict] = []
        body: dict = {"query": query}
        for _ in range(_MAX_FIELD_PAGES):
            data = self._call("POST", "googleAdsFields:search", body=body)
            out.extend(data.get("results") or [])
            if not data.get("nextPageToken"):
                return out
            body = {"query": query, "pageToken": data["nextPageToken"]}
        raise RuntimeError(f"Google Ads returned more than {_MAX_FIELD_PAGES} pages of "
                           "fields for one query — unexpected, nothing returned.")

    def describe_resource(self, resource: str) -> dict:
        """What a GAQL resource offers: `{resource, attributes, metrics, segments,
        related, not_filterable, not_sortable}` — full field names, the first four
        lists are the fields selectable with it. Unknown resource → `ValueError`."""
        res = (resource or "").strip()
        if not _RESOURCE.fullmatch(res):
            raise ValueError(f"A GAQL resource name is snake_case (e.g. campaign, "
                             f"ad_group_ad), got {resource!r}.")
        own = self.search_fields(f"{_FIELD_SELECT} WHERE name LIKE '{res}.%' "
                                 "AND category = 'ATTRIBUTE'")
        if not own:
            raise ValueError(f"Google Ads knows no resource `{res}` (API {API_VERSION}).")
        linked = self.search_fields(f"{_FIELD_SELECT} WHERE selectable_with "
                                    f"CONTAINS ANY('{res}')")
        return _describe(res, own + linked)


def _describe(resource: str, fields: list[dict]) -> dict:
    out: dict = {"resource": resource, "attributes": [], "metrics": [], "segments": [],
                 "related": []}
    not_filterable: set[str] = set()
    not_sortable: set[str] = set()
    seen: set[str] = set()
    for f in fields:
        name = f.get("name") or ""
        if not name or name in seen:
            continue
        seen.add(name)
        if not f.get("filterable"):
            not_filterable.add(name)
        if not f.get("sortable"):
            not_sortable.add(name)
        if not f.get("selectable"):
            continue
        if name.startswith(f"{resource}."):
            out["attributes"].append(name)
        elif name.startswith("metrics."):
            out["metrics"].append(name)
        elif name.startswith("segments."):
            out["segments"].append(name)
        else:
            out["related"].append(name)
    for key in ("attributes", "metrics", "segments", "related"):
        out[key].sort()
    out["not_filterable"] = sorted(not_filterable)
    out["not_sortable"] = sorted(not_sortable)
    return out
