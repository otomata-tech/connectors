"""Klaviyo API client (private key, revision 2026-07-15).

The methods implement the functions described in
`connectors/klaviyo/connector.yaml`, under the same names and with the same
arguments: reads of the account, profiles, lists, segments, campaigns, flows,
metrics, events, metric aggregates and values reports; writes on profiles,
list membership, events and marketing consent. Nothing here sends a campaign
or creates or edits a campaign, flow or template.

Bodies and responses are JSON:API and are passed through as they are: a write
takes `data`, the resource object `{type, attributes, ...}` (a list of
`{type: "profile", id}` for list membership), and returns Klaviyo's response,
`{}` when Klaviyo answers 202 or 204 without a body.

Paging: a list answers `links.next`, a full URL carrying `page[cursor]`. Pass
`next_cursor(page)` back as `page_cursor`, or call with `all_pages=True`: the
client follows `links.next` itself (only to the Klaviyo API over https),
`max_pages` pages at most.

Usage:
    client = KlaviyoClient(api_key="pk_...")
    jane = client.list_profiles(filter='equals(email,"jane@example.com")')
    members = client.list_profiles_in_list("Y6nRLr", all_pages=True)["data"]
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import parse_qs, urlsplit

import requests

from ..common.credentials import require
from ._api.catalog import CatalogMixin
from ._api.lists import ListsMixin
from ._api.metrics import MetricsMixin
from ._api.profiles import ProfilesMixin
from .errors import (KlaviyoError, KlaviyoPaginationError, KlaviyoRateLimited,
                     Refusals, error_from_response)
from .rate_limit import SHARED_LIMITER, EndpointRateLimiter

__all__ = [
    "API_HOST",
    "BASE_URL",
    "REVISION",
    "KlaviyoClient",
    "KlaviyoError",
    "KlaviyoPaginationError",
    "KlaviyoRateLimited",
    "next_cursor",
]

API_HOST = "a.klaviyo.com"
BASE_URL = f"https://{API_HOST}/api"
REVISION = "2026-07-15"
JSON_API = "application/vnd.api+json"
TIMEOUT_S = 30
DEFAULT_MAX_PAGES = 10


def next_cursor(page: dict) -> Optional[str]:
    """The cursor of the next page, read in `links.next` (`page[cursor]`, or
    `page_cursor` for the values reports); None on the last page."""
    url = ((page or {}).get("links") or {}).get("next")
    if not url:
        return None
    query = parse_qs(urlsplit(url).query)
    for name in ("page[cursor]", "page_cursor"):
        if query.get(name):
            return query[name][0]
    return None


def _check_next(url: str) -> str:
    """`links.next` is followed only to the Klaviyo API, over https."""
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != API_HOST
            or parts.port is not None or parts.username or parts.password
            or not parts.path.startswith("/api/")):
        raise KlaviyoPaginationError(
            f"klaviyo: links.next does not point to the Klaviyo API over "
            f"https ({parts.scheme}://{parts.hostname}{parts.path}); not "
            "followed.")
    return url


def _encode(value: Any) -> Any:
    """A query value: lists comma-joined, booleans lowercase."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
        if not items or any("," in v or not v for v in items):
            raise ValueError("list parameters take non-empty items without "
                             "commas")
        return ",".join(items)
    return value


def _query(params: Dict[str, Any]) -> Dict[str, Any]:
    """Query parameters without the unset ones."""
    return {k: _encode(v) for k, v in params.items() if v is not None}


class KlaviyoClient(ProfilesMixin, ListsMixin, CatalogMixin, MetricsMixin):
    """Client for the Klaviyo API (`Authorization: Klaviyo-API-Key <key>`).

    Every request first waits for a slot of its endpoint's burst and steady
    windows for this key (`rate_limiter`, shared process-wide by default). An
    upstream refusal raises a `KlaviyoError` (an `UpstreamHTTPError`):
    `KlaviyoRateLimited` on 429, and also when the pacing would wait longer
    than the limiter allows.
    """

    BASE_URL = BASE_URL
    REVISION = REVISION

    def __init__(self, api_key: str = None, *,
                 rate_limiter: Optional[EndpointRateLimiter] = None,
                 session: Optional[requests.Session] = None):
        """
        Args:
            api_key: Klaviyo private API key (`pk_...`), supplied by the
                consumer, with the scopes of the calls it makes.
            rate_limiter: pacing per key and endpoint; the process-wide one
                by default.
            session: HTTP session; a new one by default.
        """
        self._api_key = require(api_key, "KLAVIYO_API_KEY")
        self._limiter = rate_limiter or SHARED_LIMITER
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Klaviyo-API-Key {self._api_key}",
            "revision": REVISION,
            "Accept": JSON_API,
        })

    # --- Transport ------------------------------------------------------

    def _send(self, method: str, url: str, buckets: Sequence[str], *,
              params: Optional[dict] = None, body: Any = None,
              refusals: Optional[Refusals] = None) -> Any:
        """ONE call, paced, and the only place that translates a refusal."""
        self._limiter.acquire(self._api_key, buckets)
        headers = None
        data = None
        if body is not None:
            headers = {"Content-Type": JSON_API}
            data = json.dumps(body, separators=(",", ":"))
        path = urlsplit(url).path
        try:
            response = self.session.request(
                method, url, params=params, data=data, headers=headers,
                timeout=TIMEOUT_S, allow_redirects=False)
        except requests.RequestException as e:
            raise RuntimeError(
                f"klaviyo: {method} {path} — {type(e).__name__}: {e}") from e

        status = response.status_code
        if status >= 400:
            raise error_from_response(response, refusals)
        if 300 <= status < 400:
            raise KlaviyoError(
                status, "unexpected_redirect",
                f"Klaviyo answered {method} {path} with a redirect; redirects "
                "are not followed.")
        if status in (202, 204) or not response.content:
            return {}
        try:
            return response.json()
        except ValueError as e:
            raise RuntimeError(
                f"klaviyo: {method} {path} answered {status} without readable "
                f"JSON — {response.text[:200]!r}") from e

    def _call(self, method: str, path: str, bucket: str, *,
              params: Optional[dict] = None, body: Any = None,
              refusals: Optional[Refusals] = None,
              extra_buckets: Iterable[str] = ()) -> Any:
        return self._send(method, f"{self.BASE_URL}{path}",
                          [bucket, *extra_buckets], params=_query(params or {}),
                          body=body, refusals=refusals)

    def _list(self, path: str, bucket: str, params: dict, *,
              all_pages: bool, max_pages: int,
              refusals: Optional[Refusals] = None) -> dict:
        """One page as Klaviyo returns it, or, with `all_pages`, every page up
        to `max_pages`, following `links.next`.

        Walked, it returns `{data, included, links: {next}, pages}`: `data`
        concatenated, `included` without duplicates, and `links.next` set
        when the bound stopped the walk, so the result never passes for
        complete when it is not.
        """
        first = self._call("GET", path, bucket, params=params,
                           refusals=refusals)
        if not all_pages:
            return first
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        data: List[Any] = list(first.get("data") or [])
        included: List[Any] = []
        seen = set()

        def keep(page: dict) -> None:
            for item in page.get("included") or []:
                key = (item.get("type"), item.get("id"))
                if key not in seen:
                    seen.add(key)
                    included.append(item)

        keep(first)
        pages = 1
        next_url = (first.get("links") or {}).get("next")
        while next_url and pages < max_pages:
            page = self._send("GET", _check_next(next_url), [bucket],
                              refusals=refusals)
            data.extend(page.get("data") or [])
            keep(page)
            pages += 1
            next_url = (page.get("links") or {}).get("next")
        return {"data": data, "included": included,
                "links": {"next": next_url or None}, "pages": pages}

