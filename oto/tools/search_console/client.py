"""Google Search Console client, via a service account key.

One credential, two hosts:

- **Webmasters v3** (`www.googleapis.com/webmasters/v3`): the visible sites,
  performance (`searchAnalytics/query`) and sitemaps;
- **Search Console v1** (`searchconsole.googleapis.com`): URL inspection.

**Why a service account.** Same reason as `google_analytics`: nobody's Google
account is connected. Adding the service account's email as a user of a
property opens it, removing it cuts it off.

**Two scopes, the narrower one by default.** Every read asks for
`webmasters.readonly`. `add_sitemap`, the only write, asks for `webmasters`, and
Google also requires the service account to be an Owner or Full user there.

**What this API does not do.** It cannot ask Google to index a page: URL
inspection only reports what Google knows. The "Request indexing" button has no
API equivalent, and the Indexing API is limited to job and livestream pages.

**Typed refusals** (subclasses of `UpstreamHTTPError`, read from Google's
`status` and `reason`, never from the text):

- `SearchConsoleInvalid` — 400: bad date, dimension or filter, or a URL outside
  the property.
- `SearchConsoleDenied` — 403: the service account is not a user of the
  property, or not with enough rights. Carries its email.
- `SearchConsoleServiceDisabled` — 403: the API is off in the service account's
  Google Cloud project.

Requires: requests, google-auth (extra `google`)
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Sequence, Union
from urllib.parse import quote

import requests

from ..common import UpstreamHTTPError
from ..common.credentials import require
from ..google_analytics import auth

WEBMASTERS_BASE = "https://www.googleapis.com/webmasters/v3"
INSPECT_ENDPOINT = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"

SCOPE_READONLY = "https://www.googleapis.com/auth/webmasters.readonly"
SCOPE_WRITE = "https://www.googleapis.com/auth/webmasters"

DEFAULT_ROW_LIMIT = 1000
_MAX_ROW_LIMIT = 25000
_HTTP_TIMEOUT = (10, 60)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_METRICS = ("clicks", "impressions", "ctr", "position")
_DISABLED_REASONS = frozenset({"SERVICE_DISABLED", "accessNotConfigured"})


class SearchConsoleError(UpstreamHTTPError):
    """Refusal from a Search Console API. `status` = Google's canonical status,
    `reason` = its first reason, `message` = its text."""

    def __init__(self, status_code: int, body: Any, *, status: str = "",
                 reason: str = "", message: str = ""):
        self.status = status
        self.reason = reason
        self.message = message
        super().__init__(status_code, body, service="search_console")


class SearchConsoleInvalid(SearchConsoleError):
    """400 — bad date, dimension, filter, or a URL outside the property."""


class SearchConsoleDenied(SearchConsoleError):
    """403 — the service account lacks access to the property."""

    def __init__(self, *args, client_email: str = "", site: str = "", **kw):
        self.client_email = client_email
        self.site = site
        super().__init__(*args, **kw)
        who = client_email or "the service account"
        where = site or "this property"
        self.args = (
            f"search_console HTTP 403: {who} has no access to {where}. Add this "
            "email as a user of the property in Search Console (Settings → Users "
            "and permissions).",)


class SearchConsoleServiceDisabled(SearchConsoleError):
    """403 — the Search Console API is off in the Google Cloud project."""


def site_url(site: str) -> str:
    """The property as Google names it: `sc-domain:<domain>` or a URL prefix with
    its trailing slash — otherwise `ValueError`."""
    value = str(site or "").strip()
    if value.startswith("sc-domain:") and len(value) > len("sc-domain:"):
        return value
    if value.startswith(("https://", "http://")):
        return value if value.endswith("/") else f"{value}/"
    raise ValueError(
        f"Invalid Search Console property: {site!r}. Expected `sc-domain:example.com` "
        "or a URL prefix like `https://example.com/`: list them with op='sites'.")


def _date(value: str, name: str) -> str:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ValueError(f"{name} must be a YYYY-MM-DD date, got {value!r}.")
    return value


def build_filters(spec: Union[dict, Sequence[dict], None]) -> Optional[list[dict]]:
    """Search Analytics `dimensionFilterGroups` from a simple filter.

    `{"page": "https://…", "query": "…"}` = exact matches, combined with AND. A
    list passes Google's own filters through (`dimension`, `operator`,
    `expression`), for `contains`, `includingRegex` and the rest."""
    if spec is None:
        return None
    if isinstance(spec, dict):
        filters = [{"dimension": k, "operator": "equals", "expression": v}
                   for k, v in spec.items()]
    elif isinstance(spec, (list, tuple)):
        filters = list(spec)
    else:
        raise ValueError("A filter is {\"dimension\": value} or a list of Search "
                         "Console filters.")
    if not filters:
        raise ValueError("A filter cannot be empty.")
    return [{"groupType": "and", "filters": filters}]


def flatten_performance(resp: dict, dimensions: Optional[Iterable[str]] = None) -> dict:
    """A `searchAnalytics/query` answer as a TABLE: `columns` (dimensions then
    metrics) and `rows`. Without dimensions, one row of totals."""
    dims = list(dimensions or ())
    rows = [list(r.get("keys") or ()) + [r.get(m) for m in _METRICS]
            for r in resp.get("rows") or ()]
    out: dict[str, Any] = {"columns": dims + list(_METRICS), "rows": rows}
    if resp.get("responseAggregationType"):
        out["aggregation"] = resp["responseAggregationType"]
    return out


def flatten_inspection(resp: dict) -> dict:
    """A URL inspection reduced to what says whether the page is indexed, and why
    not. `verdict` PASS = on Google; `coverage` is the text Search Console shows."""
    result = resp.get("inspectionResult") or {}
    index = result.get("indexStatusResult") or {}
    out = {
        "verdict": index.get("verdict"),
        "coverage": index.get("coverageState"),
        "indexing": index.get("indexingState"),
        "robots_txt": index.get("robotsTxtState"),
        "page_fetch": index.get("pageFetchState"),
        "last_crawl": index.get("lastCrawlTime"),
        "crawled_as": index.get("crawledAs"),
        "google_canonical": index.get("googleCanonical"),
        "user_canonical": index.get("userCanonical"),
        "sitemaps": index.get("sitemap"),
        "link": result.get("inspectionResultLink"),
    }
    return {k: v for k, v in out.items() if v}


class SearchConsoleClient:
    """Search Console client, auth via service account key."""

    def __init__(self, service_account_key: Union[str, bytes, dict, None] = None, *,
                 session: Optional[requests.Session] = None):
        """
        Args:
            service_account_key: the JSON content of the service account key
                (text or dict), supplied by the consumer (required).
            session: HTTP transport (default: a fresh `requests.Session`).
        """
        raw = require(service_account_key, "SEARCH_CONSOLE_SERVICE_ACCOUNT_JSON")
        self._key = auth.parse_service_account_key(raw)
        self.client_email: str = self._key["client_email"]
        self.session = session or requests.Session()

    def _request(self, method: str, url: str, *, json_body: Any = None, site: str = "",
                 scope: str = SCOPE_READONLY) -> Any:
        token = auth.access_token(self._key, session=self.session, scope=scope)
        resp = self.session.request(method, url, json=json_body, timeout=_HTTP_TIMEOUT,
                                    headers={"Authorization": f"Bearer {token}"})
        if resp.status_code >= 400:
            self._raise(resp, site)
        return resp.json() if resp.content else {}

    def _raise(self, resp, site: str) -> None:
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        err = body.get("error") if isinstance(body, dict) else None
        err = err if isinstance(err, dict) else {}
        reasons = [d.get("reason") for d in [*(err.get("details") or ()), *(err.get("errors") or ())]
                   if isinstance(d, dict) and d.get("reason")]
        kw = dict(status=err.get("status") or "", reason=reasons[0] if reasons else "",
                  message=err.get("message") or "")
        if resp.status_code == 403 and _DISABLED_REASONS & set(reasons):
            raise SearchConsoleServiceDisabled(resp.status_code, body, **kw)
        if resp.status_code == 403:
            raise SearchConsoleDenied(resp.status_code, body, client_email=self.client_email,
                                      site=site, **kw)
        if resp.status_code == 400:
            raise SearchConsoleInvalid(resp.status_code, body, **kw)
        raise SearchConsoleError(resp.status_code, body, **kw)

    @staticmethod
    def _site_path(site: str) -> str:
        return f"{WEBMASTERS_BASE}/sites/{quote(site, safe='')}"

    def list_sites(self) -> list[dict]:
        """The properties the service account is a user of: `siteUrl`, `permissionLevel`."""
        return self._request("GET", f"{WEBMASTERS_BASE}/sites").get("siteEntry") or []

    def search_analytics(self, site: str, *, start_date: str, end_date: str,
                         dimensions: Optional[Sequence[str]] = None,
                         search_type: Optional[str] = None,
                         filters: Union[dict, Sequence[dict], None] = None,
                         row_limit: int = DEFAULT_ROW_LIMIT, start_row: int = 0,
                         aggregation_type: Optional[str] = None,
                         data_state: Optional[str] = None) -> dict:
        """`searchAnalytics/query` — raw answer (see `flatten_performance`).

        `dimensions`: query, page, country, device, date, searchAppearance.
        `search_type`: web (default), image, video, news, discover, googleNews."""
        name = site_url(site)
        if not 1 <= row_limit <= _MAX_ROW_LIMIT:
            raise ValueError(f"row_limit must be between 1 and {_MAX_ROW_LIMIT}.")
        body: dict[str, Any] = {"startDate": _date(start_date, "start_date"),
                                "endDate": _date(end_date, "end_date"),
                                "rowLimit": row_limit, "startRow": start_row}
        groups = build_filters(filters)
        optional = {"dimensions": list(dimensions or ()), "type": search_type,
                    "dimensionFilterGroups": groups,
                    "aggregationType": aggregation_type, "dataState": data_state}
        body.update({k: v for k, v in optional.items() if v})
        return self._request("POST", f"{self._site_path(name)}/searchAnalytics/query",
                             json_body=body, site=name)

    def inspect_url(self, site: str, url: str, *,
                    language_code: Optional[str] = None) -> dict:
        """What Google knows about one URL of the property. Read-only: it never
        queues the page for crawling. Quota: 2,000 a day per property."""
        name = site_url(site)
        body = {"siteUrl": name, "inspectionUrl": url}
        if language_code:
            body["languageCode"] = language_code
        return self._request("POST", INSPECT_ENDPOINT, json_body=body, site=name)

    def list_sitemaps(self, site: str) -> list[dict]:
        """The sitemaps submitted for the property, with their last read and errors."""
        name = site_url(site)
        resp = self._request("GET", f"{self._site_path(name)}/sitemaps", site=name)
        return resp.get("sitemap") or []

    def add_sitemap(self, site: str, feedpath: str) -> None:
        """Submit, or resubmit, a sitemap. Needs an Owner or Full user."""
        name = site_url(site)
        self._request("PUT", f"{self._site_path(name)}/sitemaps/{quote(feedpath, safe='')}",
                      site=name, scope=SCOPE_WRITE)
